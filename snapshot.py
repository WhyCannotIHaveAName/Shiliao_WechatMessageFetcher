"""Authenticated read-only snapshots of the user's Windows WeChat 4.x databases."""
import hashlib
import hmac
import os
import shutil
import sqlite3
import struct
import time
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from native import _verify_enc_key

PAGE = 4096
RESERVE = 80

def signature(path):
    try:
        s = path.stat()
        return s.st_size, s.st_mtime_ns
    except FileNotFoundError:
        return None

def decrypt_page(page, number, key, mac_key):
    if len(page) != PAGE:
        raise ValueError("数据库页不完整")
    start = 16 if number == 1 else 0
    expected = hmac.new(mac_key, page[start:PAGE-64] + struct.pack("<I", number), hashlib.sha512).digest()
    if not hmac.compare_digest(expected, page[-64:]):
        if not any(page):
            return page
        raise ValueError("数据库第 %d 页校验失败，请保持微信空闲后重试。" % number)
    dec = Cipher(algorithms.AES(key), modes.CBC(page[-80:-64])).decryptor()
    plain = dec.update(page[start:-80]) + dec.finalize()
    return (b"SQLite format 3\0" if number == 1 else b"") + plain + bytes(80)

def wal_checksum(data, endian, state=(0, 0)):
    a, b = state
    for x, y in struct.iter_unpack(endian + "II", data):
        a = (a + x + b) & 0xffffffff
        b = (b + y + a) & 0xffffffff
    return a, b

def committed_wal(path, check):
    """Return committed frame offsets and final database size. Never apply open transactions."""
    if not path.exists() or path.stat().st_size == 0:
        return {}, None
    frames, transaction = {}, {}
    final_size = None
    with path.open("rb") as stream:
        head = stream.read(32)
        if len(head) != 32:
            raise ValueError("WAL 日志头不完整，请重试")
        magic, version, page_size = struct.unpack(">III", head[:12])
        if magic not in (0x377f0682, 0x377f0683) or page_size != PAGE or version != 3007000:
            raise ValueError("不支持此 WAL 日志格式")
        endian = "<" if magic == 0x377f0682 else ">"
        rolling = wal_checksum(head[:24], endian)
        if rolling != struct.unpack(">II", head[24:32]):
            raise ValueError("WAL 日志头校验失败")
        while True:
            check()
            offset = stream.tell()
            frame = stream.read(24 + PAGE)
            if not frame:
                break
            # SQLite also ignores incomplete/uncommitted tail frames.
            if len(frame) != 24 + PAGE or frame[8:16] != head[16:24]:
                break
            number, db_size = struct.unpack(">II", frame[:8])
            if not number:
                break
            computed = wal_checksum(frame[:8] + frame[24:], endian, rolling)
            if computed != struct.unpack(">II", frame[16:24]):
                break
            rolling = computed
            transaction[number] = offset + 24
            if db_size:
                frames.update(transaction)
                transaction.clear()
                final_size = db_size
    return frames, final_size

def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.execute("PRAGMA query_only=ON")
    db.row_factory = sqlite3.Row
    db.text_factory = lambda b: b.decode("utf8") if is_utf8(b) else b
    return db

def is_utf8(b):
    try:
        b.decode("utf8")
        return True
    except UnicodeDecodeError:
        return False

def snapshot(source, target, material, check=lambda: None):
    """Stable copies plus authenticated pages, committed WAL frames and SQLite structural validation."""
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = target.with_suffix(".encrypted")
    wal = target.with_suffix(".walcopy")
    partial = target.with_suffix(".partial")
    origin_wal = Path(str(source) + "-wal")
    try:
        for attempt in range(3):
            check()
            before = (signature(source), signature(origin_wal))
            shutil.copyfile(source, raw)
            if before[1]:
                shutil.copyfile(origin_wal, wal)
            elif wal.exists():
                wal.unlink()
            if before == (signature(source), signature(origin_wal)):
                break
        else:
            raise RuntimeError("微信正在更新记录，无法取得一致快照，请稍后重试。")
        with raw.open("rb") as f:
            first = f.read(PAGE)
        plain_source = first.startswith(b"SQLite format 3\0") and material is None
        if not plain_source and not _verify_enc_key(material, first):
            raise ValueError("数据库密钥不匹配")
        key = material[:32] if material else None
        salt = material[32:] if material and len(material) == 48 else first[:16]
        mac = hashlib.pbkdf2_hmac("sha512", key, bytes(x ^ 0x3a for x in salt), 2, 32) if key else None
        with raw.open("rb") as src, partial.open("wb") as out:
            number = 1
            while True:
                check()
                page = src.read(PAGE)
                if not page:
                    break
                out.write(page if plain_source else decrypt_page(page, number, key, mac))
                number += 1
        frames, db_size = committed_wal(wal, check)
        if frames:
            with wal.open("rb") as src, partial.open("r+b") as out:
                for number, offset in frames.items():
                    if number > db_size:
                        continue
                    check()
                    src.seek(offset)
                    page = src.read(PAGE)
                    out.seek((number - 1) * PAGE)
                    out.write(page if plain_source else decrypt_page(page, number, key, mac))
                out.truncate(db_size * PAGE)
        with partial.open("r+b") as out:
            out.seek(18)
            out.write(b"\x01\x01")
        db = readonly(partial)
        try:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("快照结构检查失败，请重新读取")
        finally:
            db.close()
        os.replace(partial, target)
        return target
    finally:
        for p in (raw, wal, partial):
            if p.exists():
                p.unlink()

