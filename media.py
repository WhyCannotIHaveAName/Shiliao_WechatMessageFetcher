"""Local media decoding. Format reference: wechatauto-replica, Apache-2.0."""
import ctypes
import hashlib
import io
import os
import re
import struct
import time
from pathlib import Path
from collections import Counter
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image
import native

V2 = b"\x07\x08\x56\x32\x08\x07"
V1 = b"\x07\x08\x05\x56\x02\x05"

def kind(data):
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"BM"):
        return "bmp"
    if data.startswith(b"wxgf"):
        return "wxgf"
    return None

def aes_decrypt(key, data):
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return dec.update(data) + dec.finalize()

def local_image_keys(account):
    """Derive candidate AES/XOR pairs from local cache names, without storing keys."""
    roaming = os.environ.get('APPDATA')
    if not roaming:
        return []
    account_names = {account.name, re.sub(r'_[a-zA-Z0-9]{4}$', '', account.name)}
    ids = set()
    for entry in (Path(roaming) / 'Tencent' / 'xwechat').glob('net*/kvcomm/*'):
        match = re.match(r'(?:key_(?:reportnow_)?)?(\d{1,10})_', entry.name)
        if match:
            ids.add(int(match[1]))
    return [(hashlib.md5(f'{number}{name}'.encode()).hexdigest()[:16].encode(), number & 255)
            for number in sorted(ids) for name in sorted(account_names)]

def key_signature(data):
    # A two-byte BMP signature creates false matches across large memory scans.
    return (data.startswith(b'\xff\xd8\xff') or data.startswith(b'\x89PNG\r\n\x1a\n')
            or data.startswith((b'GIF87a',b'GIF89a',b'wxgf'))
            or (data.startswith(b'RIFF') and data[8:12] == b'WEBP'))

def valid_image(data):
    ext = kind(data)
    if not ext or ext == "wxgf":
        return False
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        return True
    except Exception:
        return False

def xor_guess(paths):
    counts = Counter()
    for p in paths[:64]:
        try:
            with p.open("rb") as f:
                f.seek(-2, 2)
                a, b = f.read(2)
            key = a ^ 0xff
            if b ^ key == 0xd9:
                counts[key] += 1
        except (OSError, ValueError):
            continue
    return counts.most_common(1)[0][0] if counts else None

def find_image_key(probe, check, progress):
    deadline = time.monotonic() + 120
    def bounded_check():
        check()
        if time.monotonic() > deadline:
            raise ValueError('图片密钥读取超时，可稍后重新导出图片')
    patterns = (re.compile(rb"(?<![a-zA-Z0-9])([a-zA-Z0-9]{16}|[a-zA-Z0-9]{32})(?![a-zA-Z0-9])"),
                re.compile(rb"(?:[a-zA-Z0-9]\x00){32}"))
    pids = native.MemoryKeys(__import__("threading").Event(), progress)._find_weixin_pids()
    seen = set()
    for pid in pids:
        handle = native._k32.OpenProcess(0x410, False, pid)
        if not handle:
            continue
        try:
            for base, data in native.memory_chunks(handle, bounded_check):
                for pattern in patterns:
                    for hit in pattern.finditer(data):
                        value = hit.group(1) if pattern is patterns[0] else hit.group()[::2]
                        candidate = value[:16]
                        if candidate in seen:
                            continue
                        seen.add(candidate)
                        if key_signature(aes_decrypt(candidate, probe)):
                            return candidate
        finally:
            native._k32.CloseHandle(handle)
    return None

def wxgf_preview(data):
    """Decode first HEVC frame for HTML, retaining the original container."""
    start = data.find(b'\x00\x00\x00\x01')
    if start < 0:
        return None
    try:
        import av
        with av.open(io.BytesIO(data[start:]),format='hevc') as container:
            for frame in container.decode(video=0):
                out = io.BytesIO()
                frame.to_image().save(out,format='JPEG',quality=95)
                return out.getvalue()
    except Exception:
        return None

def decode_dat(data, aes_key=None, xor_key=None):
    if valid_image(data):
        return data
    if data[:6] == V2:
        if len(data) < 31 or not aes_key:
            raise ValueError("图片密钥尚未就绪")
        size, xor_size = struct.unpack_from("<II", data, 6)
        encrypted_size = (size // 16 + 1) * 16
        end = 15 + encrypted_size
        tail_start = len(data) - xor_size
        if end > tail_start or xor_size > len(data):
            raise ValueError("图片文件不完整")
        first = aes_decrypt(aes_key, data[15:end])
        pad = first[-1]
        if not 1 <= pad <= 16 or first[-pad:] != bytes([pad]) * pad:
            raise ValueError("图片解密校验失败")
        first = first[:-pad]
        if len(first) != size:
            raise ValueError("图片长度校验失败")
        raw = data[end:tail_start]
        tail = data[tail_start:] if xor_size else b""
        keys = [xor_key] if xor_key is not None else []
        keys += [k for k in range(256) if k != xor_key]
        if not tail:
            return first + raw
        if kind(first) == 'wxgf':
            if xor_key is None:
                raise ValueError('缺少 WXGF 尾部解码信息')
            return first + raw + bytes(b ^ xor_key for b in tail)
        for key in keys:
            result = first + raw + bytes(b ^ key for b in tail)
            if valid_image(result):
                return result
        raise ValueError("图片尾部无法校验")
    body = data[22:] if data[:6] == V1 else data
    for expected in (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF"):
        if not body:
            continue
        key = body[0] ^ expected[0]
        if bytes(b ^ key for b in body[:len(expected)]) != expected:
            continue
        out = bytes(b ^ key for b in body)
        if valid_image(out):
            return out
    raise ValueError("暂不支持此图片格式")

class LocalImages:
    def __init__(self, session, chats):
        self.session = session
        self.files = {}
        for chat in chats:
            session.check()
            root = session.account / "msg" / "attach" / chat["id"]
            for p in root.rglob("*") if root.exists() else []:
                session.check()
                if p.is_file() and p.suffix.lower() in (".dat", ".jpg", ".png", ".gif", ".webp"):
                    self.files.setdefault((chat["id"], p.name.lower()), []).append(p)
        thumbnails = [p for (_, name), paths in self.files.items() if name.endswith("_t.dat") for p in paths]
        self.xor = xor_guess(thumbnails)
        self.key = session.image_key
        self.local_keys = None
        self.scan_attempted = False
        self.saved = {}

    def _ensure_key(self, data):
        if data[:6] != V2:
            return
        if len(data) < 31:
            raise ValueError('图片文件不完整')
        size, tail = struct.unpack_from('<II', data, 6)
        if 15 + (size // 16 + 1) * 16 > len(data) - tail:
            raise ValueError('图片文件不完整')
        if self.local_keys is None:
            self.local_keys = local_image_keys(self.session.account)
        # Validate both ends of the AES segment before accepting an account/key pair.
        end = 15 + (size // 16 + 1) * 16
        pad = 16 - size % 16
        for key, xor in self.local_keys:
            if (key_signature(aes_decrypt(key, data[15:31]))
                    and aes_decrypt(key, data[end-16:end]).endswith(bytes([pad]) * pad)):
                self.key, self.xor = key, xor
                self.session.image_key = key
                return
        if self.key and key_signature(aes_decrypt(self.key, data[15:31])):
            return
        if self.scan_attempted:
            return
        self.scan_attempted = True
        self.session.progress('正在读取图片解码信息，最多等待 120 秒。超时后继续导出消息。')
        self.key = find_image_key(data[15:31], self.session.check, self.session.progress)
        self.session.image_key = self.key

    def attach(self, chat, message, destination):
        body = message["raw_content"]
        packed = message.get("_packed") or b""
        if isinstance(packed, str):
            packed = packed.encode()
        hashes = re.findall(rb"(?<![a-fA-F0-9])[a-fA-F0-9]{32}(?![a-fA-F0-9])", packed)
        hashes += [x.encode() for x in re.findall(r'(?i)(?:md5|fullmd5)\s*=\s*["\x27]([0-9a-f]{32})', body)]
        hashes = list(dict.fromkeys(x.decode().lower() for x in hashes))
        candidates = []
        for digest in hashes:
            for suffix, quality in (("_h.dat", "原图"), (".dat", "本地图片"), ("_t.dat", "缩略图"),
                                    (".jpg", "本地图片"), (".png", "本地图片"), (".gif", "本地图片")):
                for p in self.files.get((chat["id"], digest + suffix), []):
                    candidates.append((p, quality))
        if not candidates:
            return {'status': '本地未找到原图或缩略图（可能已过期、被清理或未下载）', 'code': 'missing'}
        failures = []
        raw_wxgf = None
        ready, encrypted = [], []
        for source, quality in dict.fromkeys(candidates):
            self.session.check()
            try:
                with source.open('rb') as stream:
                    header = stream.read(31)
            except InterruptedError:
                raise
            except OSError as exc:
                failures.append(type(exc).__name__)
                continue
            needs_key = header.startswith(V2) and not self.key
            (encrypted if needs_key else ready).append((source, quality))
        # Use a readable thumbnail before waiting for key discovery.
        for source, quality in ready + encrypted:
            self.session.check()
            if source in self.saved:
                return dict(self.saved[source])
            try:
                data = source.read_bytes()
                self._ensure_key(data)
                decoded = decode_dat(data, self.key, self.xor)
                ext = kind(decoded)
                if ext != 'wxgf' and not valid_image(decoded):
                    raise ValueError('图片内容不完整')
            except (InterruptedError, MemoryError):
                raise
            except Exception as exc:
                failures.append(type(exc).__name__ + '：' + str(exc))
                continue
            # Destination errors must stop the export, not masquerade as missing media.
            is_thumbnail = quality == '缩略图'
            if ext == 'wxgf':
                name = hashlib.sha256(decoded).hexdigest() + '.wxgf'
                (destination / name).write_bytes(decoded)
                raw_wxgf = 'assets/' + name
                decoded = wxgf_preview(decoded)
                if decoded is None:
                    continue
                ext = 'jpg'
                quality += '（WXGF 首帧预览）'
            name = hashlib.sha256(decoded).hexdigest() + '.' + ext
            path = destination / name
            if not path.exists():
                path.write_bytes(decoded)
            result = {'status': ('原图未取得，已导出' if is_thumbnail else '') + quality,
                      'code': 'thumbnail' if is_thumbnail else 'ok', 'file': 'assets/' + name}
            if raw_wxgf:
                result['original_file'] = raw_wxgf
            self.saved[source] = result
            return dict(result)
        if raw_wxgf:
            return {'status': '已保留 WXGF 原文件，无法生成预览', 'code': 'original_only', 'original_file': raw_wxgf}
        return {'status': '未取得可查看图片，已保留消息', 'code': 'decode_failed', 'details': failures}

