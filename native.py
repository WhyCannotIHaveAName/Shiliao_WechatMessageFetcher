# Adapted from fanyuantaier/wechatauto-replica, Apache-2.0.

# Upstream commit bc2e6f2461330eb1a53996d2f2afc18416376b52.

# Modified for Shiliao: isolated read-only extraction; cancellation and bounded buffers;

# no persistent key cache, account switching, background listener, or UI automation.

from __future__ import annotations

import ctypes, glob, hashlib, hmac as hmac_mod, json, os, re, struct, subprocess, time

from ctypes import wintypes

from typing import Dict,List,Optional,Tuple

PAGE_SZ=4096
RESERVE_SZ=80
CONFIG_CIPHER_NAME=b"com.Tencent.WCDB.Config.Cipher"

CONFIG_XOR_MASK=bytes.fromhex("d2c7442458020000004889442450488b450048844c2448488944254048584c24")

HEX_LITERAL_RE=re.compile(rb"[xX]\x27([0-9a-fA-F]{64,192})\x27")

class _MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("__alignment1", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("__alignment2", wintypes.DWORD),
    ]

def _pbkdf2(passwd: bytes, salt: bytes, iters: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", passwd, salt, iters, dklen=32)

def _verify_enc_key(enc_key: bytes, page1: bytes, salt: Optional[bytes] = None) -> bool:
    """验证 enc_key 是否为 page1 的 SQLCipher 4 密钥。

    - enc_key 为 32 字节裸 key：默认用文件头前 16 字节作 salt（标准形式）；
    - enc_key 为 48 字节 key+salt：显式 salt 优先于文件头（明文头模式）；
    - 也允许单独传 salt 参数覆盖（供提取逻辑按 96hex 拆分尝试）。
    """
    if len(page1) < PAGE_SZ:
        return False
    if len(enc_key) == 48 and salt is None:
        enc_key, salt = enc_key[:32], enc_key[32:]
    if salt is None:
        salt = page1[:16]
    elif len(salt) != 16:
        return False
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = _pbkdf2(enc_key, mac_salt, 2)
    hmac_data = page1[16: PAGE_SZ - RESERVE_SZ + 16]
    stored_hmac = page1[PAGE_SZ - 64: PAGE_SZ]
    hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    return hm.digest() == stored_hmac

def _find_account_dirs(db_dir: str) -> List[str]:
    """列出 db_dir 下所有含 db_storage 子目录的账号目录。

    微信号目录不一定以 wxid_ 开头（如自定义微信号），这里只依赖
    db_storage 子目录的存在性判断。
    """
    out = []
    try:
        for name in os.listdir(db_dir):
            p = os.path.join(db_dir, name, "db_storage")
            if os.path.isdir(p):
                out.append(os.path.join(db_dir, name))
    except OSError:
        pass
    return sorted(out)

def _extract_path_from_config(content: str) -> Optional[str]:
    """从配置内容中提取数据目录路径，兼容 JSON 字段 / 纯路径 / 任意文本。

    微信 4.x 不同版本配置文件格式不一：有的是纯路径，有的是 JSON
    （字段如 dataDir / fileSavePath）。这里统一兜底提取第一个 Windows 路径。
    """
    content = (content or "").strip().lstrip("\ufeff")
    if not content:
        return None
    try:
        obj = json.loads(content)
        if isinstance(obj, dict):
            for key in ("dataDir", "data_dir", "fileSavePath", "savePath",
                        "path", "defaultFileSavePath"):
                v = obj.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, str) and re.match(r"^[A-Za-z]:[\\/]", item):
                    return item
    except Exception:
        pass
    if re.match(r"^[A-Za-z]:[\\/]", content):
        return content
    m = re.search(r"[A-Za-z]:[\\/][^\s\x00-\x1f\"']+", content)
    if m:
        return _abs_root(m.group(0))        # 不能用 rstrip：'d:\\' 会变成 'd:'（盘符相对）
    return None

def _config_candidates() -> List[str]:
    """可能的微信 4.x 配置目录（按新旧版本与 32/64 位安装差异）。"""
    out = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(env, "")
        if base:
            out.extend([
                os.path.join(base, "Tencent", "xwechat"),
                os.path.join(base, "Tencent", "xwechat", "config"),
                os.path.join(base, "Tencent", "WeChat"),
            ])
    return out

def _registry_data_dirs() -> List[str]:
    """从注册表读取可能指向数据目录的值（用户自定义保存位置时补充来源）。"""
    import winreg
    dirs = []
    for hive, sub in (
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\xwechat"),
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\xwechat\config"),
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat"),
    ):
        try:
            key = winreg.OpenKey(hive, sub)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                if not isinstance(data, str) or not data.strip():
                    continue
                low = name.lower()
                if "path" in low or "dir" in low or "save" in low:
                    dirs.append(data.strip())
        finally:
            winreg.CloseKey(key)
    return dirs

def _abs_root(path: str) -> str:
    """把目录路径规范化成绝对路径，尤其修好「盘符根目录」这一类。

    以前这里用 ``rstrip("\\\\/")`` 去掉尾分隔符，于是 ``d:\\`` 变成 ``d:`` ——
    而 ``d:`` 在 Windows 上是**盘符相对路径**（相对该盘当前目录），
    ``os.path.join('d:', 'xwechat_files')`` 得到 ``d:xwechat_files``，会按进程在
    D 盘的当前目录解析：从 ``D:\\`` 启动恰好对，从 ``D:\\某个子目录`` 启动就找错地方
    （issue #32）。normpath 保留根目录语义；再补一手「只写了盘符没写分隔符」的输入。
    """
    p = os.path.normpath(path or "")
    if re.match(r"^[A-Za-z]:$", p):          # "d:" 这种盘符相对写法
        p = p + os.sep
    return p

def _locate_account_root(root: Optional[str]) -> Optional[str]:
    """在候选根目录下定位「包含账号目录」的目录。

    账号目录以含 db_storage 子目录为准（微信号不一定以 wxid_ 开头）。
    兼容两种布局：
      <root>/xwechat_files/<account>/db_storage
      <root>/<account>/db_storage
    返回的目录即 WeChatDB.db_dir（账号目录的父目录）。
    """
    if not root or not os.path.isdir(root):
        return None
    root = _abs_root(root)
    candidates = [root]
    for name in ("xwechat_files", "WeChat Files", "xwechat_files_data"):
        candidates.append(os.path.join(root, name))
    seen = set()
    for cand in candidates:
        cand = _abs_root(cand)
        if cand in seen or not os.path.isdir(cand):
            continue
        seen.add(cand)
        try:
            dirs = os.listdir(cand)
        except OSError:
            continue
        if any(
            os.path.isdir(os.path.join(cand, d, "db_storage"))
            for d in dirs
        ):
            return cand
    return None

def auto_detect_db_dir() -> Optional[str]:
    """自动定位微信 4.x 数据目录（不同电脑存储位置不同）。

    探测顺序：
      1. 微信配置文件（%APPDATA%/%LOCALAPPDATA%，支持 JSON/纯路径/任意文本）；
      2. 注册表；
      3. 常见默认目录（Documents / 用户主目录）。
    """
    # 1) 配置文件
    for cfg_dir in _config_candidates():
        if not os.path.isdir(cfg_dir):
            continue
        for fp in glob.glob(os.path.join(cfg_dir, "*")):
            if os.path.isdir(fp):
                continue
            try:
                raw = open(fp, "r", encoding="utf-8").read(8192)
            except (UnicodeDecodeError, OSError):
                try:
                    raw = open(fp, "r", encoding="gbk").read(8192)
                except (UnicodeDecodeError, OSError):
                    continue
            path = _extract_path_from_config(raw)
            if not path:
                continue
            hit = _locate_account_root(path)
            if hit:
                return hit
    # 2) 注册表
    for p in _registry_data_dirs():
        hit = _locate_account_root(p)
        if hit:
            return hit
    # 3) 常见默认目录兜底
    userprofile = os.environ.get("USERPROFILE", "")
    for base in (os.path.join(userprofile, "Documents"), userprofile):
        hit = _locate_account_root(base)
        if hit:
            return hit
    return None

_k32=ctypes.WinDLL("kernel32",use_last_error=True)
_k32.OpenProcess.restype=wintypes.HANDLE
_k32.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
_k32.CloseHandle.argtypes=[wintypes.HANDLE]
_k32.VirtualQueryEx.argtypes=[wintypes.HANDLE,ctypes.c_void_p,ctypes.POINTER(_MBI),ctypes.c_size_t]
_k32.VirtualQueryEx.restype=ctypes.c_size_t
_k32.ReadProcessMemory.argtypes=[wintypes.HANDLE,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
class MemoryKeys:
    def __init__(self,cancel,progress):
        self.cancel=cancel
        self.progress=progress
        self.deadline=time.monotonic()+180
    def check(self):
        if self.cancel.is_set(): raise InterruptedError("已取消")
        if time.monotonic()>self.deadline: raise TimeoutError("读取微信密钥超过 3 分钟，请保持微信登录后重试。")
    def _find_bytes(self,h,read,needle):
        hits=[]
        for base,buf in memory_chunks(h,self.check):
            pos=buf.find(needle)
            while pos>=0:
                hits.append(base+pos)
                pos=buf.find(needle,pos+1)
        return list(dict.fromkeys(hits))
    def candidates(self):
        seen=set()
        pids=self._find_weixin_pids()
        if not pids: raise RuntimeError("请先在电脑上登录微信，再点击读取。")
        result=set()
        for i,pid in enumerate(pids):
            self.check()
            self.progress("正在读取微信登录状态（%d/%d）"%(i+1,len(pids)))
            result.update(self._collect_key_candidates(pid,seen))
        return result


    def _collect_key_candidates(self, pid: int, seen: set) -> set:
        """收集候选密钥材料（**不做账号校验**）：{(cand, salt_or_None), ...}

        与 `_extract_keys_pid` 的区别：这里只负责“找出可能是密钥的 32 字节
        （或 32+16 带显式 salt）”，校验交给调用方对**每个账号目录**分别做。
        原因：内存里的密钥属于**当前登录账号**，只有对正确的账号目录才能通过
        页1 HMAC；这样账号选择由密码学校验决定，不再依赖“最近修改 .db”启发式
        （微信每次更新会重写 .db，mtime 全变 → 启发式会选错账号 → 0 密钥）。
        """
        h = _k32.OpenProcess(0x0010 | 0x0400, False, pid)
        if not h:
            return set()
        out: set = set()
        try:
            def read(addr: int, n: int):
                buf = ctypes.create_string_buffer(n)
                br = ctypes.c_size_t(0)
                if _k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, n,
                                          ctypes.byref(br)) and br.value:
                    return buf.raw[: br.value]
                return None

            needles = self._find_bytes(h, read, CONFIG_CIPHER_NAME)
            pairs = [
                struct.pack("<Q", addr) + struct.pack("<Q", len(CONFIG_CIPHER_NAME))
                for addr in needles
            ]
            for pair in pairs:
                for qaddr in self._find_bytes(h, read, pair):
                    node = read(qaddr - 0x10, 0x50)
                    if not node or len(node) < 0x40:
                        continue
                    if struct.unpack_from("<Q", node, 0x10)[0] not in needles:
                        continue
                    if struct.unpack_from("<Q", node, 0x18)[0] != len(CONFIG_CIPHER_NAME):
                        continue
                    config_ptr = struct.unpack_from("<Q", node, 0x28)[0]
                    if not (0x10000 <= config_ptr < 0x800000000000):
                        continue
                    obj = read(config_ptr + 0x88, 0x28)
                    if not obj or len(obj) < 0x18:
                        continue
                    data_ptr = struct.unpack_from("<Q", obj, 0x8)[0]
                    data_len = struct.unpack_from("<Q", obj, 0x10)[0]
                    if not (0 < data_len <= 1024 and 0x10000 <= data_ptr < 0x800000000000):
                        continue
                    blob = read(data_ptr, int(data_len))
                    if not blob or len(blob) != data_len:
                        continue
                    decoded = bytes(
                        v ^ CONFIG_XOR_MASK[i % len(CONFIG_XOR_MASK)]
                        for i, v in enumerate(blob)
                    )
                    for m in HEX_LITERAL_RE.finditer(decoded):
                        run = m.group(1).decode().lower()
                        starts = [0]
                        if len(run) > 96:
                            starts += list(range(0, len(run) - 63, 32))
                            starts.append(len(run) - 64)
                        for s in dict.fromkeys(starts):
                            if s + 64 > len(run):
                                continue
                            cand = bytes.fromhex(run[s:s + 64])
                            if cand in seen or not self._probable_key(cand):
                                continue
                            seen.add(cand)
                            out.add((cand, None))
                            if s + 96 <= len(run):
                                out.add((cand, bytes.fromhex(run[s + 64: s + 96])))
        finally:
            _k32.CloseHandle(h)
        return out

    def _find_weixin_pids(self) -> List[int]:
        class ProcessEntry(ctypes.Structure):
            _fields_=[('size',wintypes.DWORD),('usage',wintypes.DWORD),('pid',wintypes.DWORD),
                      ('heap',ctypes.c_size_t),('module',wintypes.DWORD),('threads',wintypes.DWORD),
                      ('parent',wintypes.DWORD),('priority',wintypes.LONG),('flags',wintypes.DWORD),
                      ('name',ctypes.c_wchar*260)]
        _k32.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
        _k32.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(ProcessEntry)]
        _k32.Process32NextW.argtypes=[wintypes.HANDLE,ctypes.POINTER(ProcessEntry)]
        handle=_k32.CreateToolhelp32Snapshot(2,0)
        if handle==ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry=ProcessEntry()
            entry.size=ctypes.sizeof(entry)
            ok=_k32.Process32FirstW(handle,ctypes.byref(entry))
            pids=[]
            while ok:
                if entry.name.lower()=='weixin.exe': pids.append(entry.pid)
                ok=_k32.Process32NextW(handle,ctypes.byref(entry))
            return pids
        finally:
            _k32.CloseHandle(handle)

    @staticmethod
    def _probable_key(b: bytes) -> bool:
        return (
            len(b) == 32
            and len(set(b)) >= 15
            and b not in {b"\x00" * 32, b"\xff" * 32}
        )

def memory_chunks(handle,check):
    addr=0
    while True:
        check()
        info=_MBI()
        if not _k32.VirtualQueryEx(handle,ctypes.c_void_p(addr),ctypes.byref(info),ctypes.sizeof(info)):
            return
        start=info.BaseAddress or 0
        end=start+info.RegionSize
        if end<=addr: return
        if info.State==0x1000 and (info.Protect&0xff) in (2,4,8,32,64,128) and not info.Protect&0x100:
            for offset in range(start,end,4*1024*1024):
                check()
                size=min(4*1024*1024+256,end-offset)
                buf=ctypes.create_string_buffer(size)
                actual=ctypes.c_size_t()
                ok=_k32.ReadProcessMemory(handle,ctypes.c_void_p(offset),buf,size,ctypes.byref(actual))
                if actual.value:
                    yield offset,buf.raw[:actual.value]
        addr=end

