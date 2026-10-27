# -*- coding: utf-8 -*-
r"""keyfind.py — 自动取微信 4.x 的数据库密钥（只读，不注入、不调试、不改内存）。

思路来自社区公开的静态特征，不依赖 yara / pymem，纯 ctypes：
  1. 从 Weixin.dll 的代码段里抠出若干 32 字节的静态 XOR key 候选
     （特征：连续 4 条 `48 BA imm64` 紧接 `48 85 C0`）
  2. 在微信进程内存里找 `GetKeyAddrStub` 结构：{指针, 0, 32, 47}，
     读出指针指向的 32 字节
  3. 与 XOR 候选逐一对撞，用目标库第一页的 HMAC 判定，命中即真密钥

第三页的 HMAC 校验跟 wxdecrypt.verify_key 是同一套算法，所以只要通过
就一定能把库解开。整个流程只做 OpenProcess/ReadProcessMemory。
"""
from __future__ import annotations

import ctypes
import os
import re
import struct
import time
from ctypes import wintypes
from typing import Iterable, Optional

from Crypto.Hash import SHA512
from Crypto.Protocol.KDF import PBKDF2

PAGE_SIZE = 4096
SALT_SIZE = 16
KEY_SIZE = 32
IV_SIZE = 16
HMAC_SIZE = 64
ROUND_COUNT = 256000
RESERVE = ((IV_SIZE + HMAC_SIZE + 15) // 16) * 16          # 80

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
_k32.ReadProcessMemory.restype = wintypes.BOOL
_k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_size_t]
_k32.VirtualQueryEx.restype = ctypes.c_size_t
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL
_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                             wintypes.HANDLE]
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_k32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_k32.Module32FirstW.restype = wintypes.BOOL
_k32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_k32.Module32NextW.restype = wintypes.BOOL


class _MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD), ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wintypes.DWORD), ("hModule", wintypes.HMODULE),
        ("szModule", ctypes.c_wchar * 256), ("szExePath", ctypes.c_wchar * 260)
    ]


def _find_module_path(pid: int, mod_name: str) -> str:
    snap = _k32.CreateToolhelp32Snapshot(0x00000008 | 0x00000010, pid)
    if snap in (0, -1):
        return ""
    me = _MODULEENTRY32W()
    me.dwSize = ctypes.sizeof(_MODULEENTRY32W)
    try:
        ok = _k32.Module32FirstW(snap, ctypes.byref(me))
        target = mod_name.lower()
        while ok:
            if (me.szModule or "").lower() == target:
                return str(me.szExePath)
            ok = _k32.Module32NextW(snap, ctypes.byref(me))
    finally:
        _k32.CloseHandle(snap)
    return ""


def is_wechat_v3_running() -> bool:
    """是否检测到微信 3.x 正在运行（含 WeChatWin.dll）。"""
    for pid in _weixin_pids():
        p = _find_module_path(pid, "wechatwin.dll")
        if p and os.path.exists(p):
            return True
    return False


class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("__p1", wintypes.DWORD),
                ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD), ("__p2", wintypes.DWORD)]


# ── 内存小工具 ──────────────────────────────────────────────────────────────
def _read(h, addr: int, size: int) -> Optional[bytes]:
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    ok = _k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got))
    if not ok or got.value == 0:
        return None
    return buf.raw[:got.value]


def _regions(h) -> list[tuple[int, int, int]]:
    """[(base, size, protect)]，只要已提交、可读的。"""
    out: list[tuple[int, int, int]] = []
    addr = 0
    mbi = _MBI()
    while _k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize or 0
        # 0x02 = PAGE_READONLY, 0x04 = PAGE_READWRITE, 0x20 = EXECUTE_READ,
        # 0x40 = EXECUTE_READWRITE, 0x08 = WRITECOPY
        if mbi.State == MEM_COMMIT and size and (mbi.Protect & 0x6E):
            out.append((base, size, mbi.Protect))
        nxt = base + size
        if nxt <= addr:
            break
        addr = nxt
    return out


def _scan(h, patterns: list[re.Pattern], chunk: int = 8 << 20,
          progress=None) -> tuple[list[int], int]:
    """在可读内存里跑一组正则，返回 (命中偏移表, 扫描字节数)。

    大块读失败时自动退到小块 —— 有些区域只能一点点读，整块读会直接返回 0。
    """
    regs = _regions(h)
    total = sum(s for _b, s, _p in regs)
    hits: list[int] = []
    seen = 0
    for base, size, _prot in regs:
        off = 0
        while off < size:
            n = min(chunk, size - off)
            mem = _read(h, base + off, n)
            if mem is None:
                if n > (256 << 10):                 # 退小块重试
                    chunk = max(256 << 10, chunk // 4)
                    continue
                off += n
                seen += n
                continue
            for pat in patterns:
                for m in pat.finditer(mem):
                    hits.append(base + off + m.start())
            off += n
            seen += n
        if progress:
            progress(min(seen, total), total)
    return hits, seen


# ── 第 1 步：从 Weixin.dll 抠静态 XOR key ───────────────────────────────────
_CODE_CHAR = 0x20000000
_XK_PAT = re.compile(
    b"^\x48\xBA(.{8})"
    b".{3,8}?"
    b"\x48\xBA(.{8})"
    b".{3,8}?"
    b"\x48\xBA(.{8})"
    b".{3,8}?"
    b"\x48\xBA(.{8})"
    b".{3,8}?"
    b"\x48\x85\xC0",
    re.DOTALL,
)


def dll_xor_keys(dll_path: str) -> list[bytes]:
    """扫 Weixin.dll 代码段，取 32B XOR key 候选（通常 1~3 个）。"""
    if not dll_path or not os.path.exists(dll_path):
        return []
    try:
        import pefile
    except ImportError:
        print("[keyfind] 警告：未安装 pefile，无法提取 XOR 密钥！请运行：pip install pefile", flush=True)
        return []
    out: list[bytes] = []
    try:
        pe = pefile.PE(dll_path, fast_load=True)
    except Exception:
        return []
    for sec in pe.sections:
        if not (int(sec.Characteristics) & _CODE_CHAR):
            continue
        try:
            data = sec.get_data()
        except Exception:
            continue
        off = 0
        while True:
            i = data.find(b"\x48\xBA", off)
            if i < 0:
                break
            m = _XK_PAT.match(data[i:i + 85])
            if m:
                key = m.group(1) + m.group(2) + m.group(3) + m.group(4)
                if key not in out:
                    out.append(key)
                off = i + len(m.group(0))
            else:
                off = i + 1
    return out


# ── 第 2 步：内存里的 GetKeyAddrStub ────────────────────────────────────────
# 结构：{ 指针(8) , 0(8) , 0x20(8) , 0x2f(8) }
_STUB = re.compile(b".{8}\x00{8}\x20\x00{7}\x2f\x00{7}", re.DOTALL)
_STUB_LOOSE = re.compile(b"\x20\x00{7}\x2f\x00{7}", re.DOTALL)


def find_key(key_hex: str) -> dict:
    """拿已知密钥在内存里定位它所在的地址（调试/自检用）。"""
    needle = bytes.fromhex(key_hex)
    out: list[int] = []
    for pid in _weixin_pids():
        h = _k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not h:
            continue
        try:
            hits, _ = _scan(h, [re.compile(re.escape(needle))])
            out.extend(hits)
        finally:
            _k32.CloseHandle(h)
    return {"key": key_hex, "addresses": [hex(a) for a in out]}


_PID_CACHE: tuple[float, list[int]] = (0.0, [])


def _weixin_pids(fresh: bool = False) -> list[int]:
    """所有 Weixin.exe 的 pid，主窗口那个排第一。

    走 powershell 查一次要 1.4 秒 —— 探活一秒轮一次根本轮不起，
    所以缓存 3 秒。需要立刻确认真实状态时传 fresh=True。
    """
    global _PID_CACHE
    now = time.time()
    if not fresh and _PID_CACHE[0] and now - _PID_CACHE[0] < 3.0:
        return list(_PID_CACHE[1])
    pids = _weixin_pids_uncached()
    _PID_CACHE = (now, pids)
    return list(pids)


def _weixin_pids_uncached() -> list[int]:
    try:
        import subprocess
        ps = ("Get-Process Weixin,WeChat -ErrorAction SilentlyContinue | "
              "Sort-Object -Property @{Expression={$_.MainWindowHandle -ne 0};Descending=$true},"
              "@{Expression={$_.WorkingSet64};Descending=$true} | "
              "Select-Object -ExpandProperty Id")
        r = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=20,
                           creationflags=0x08000000)
        pids = [int(x) for x in r.stdout.split() if x.strip().isdigit()]
        if pids:
            return pids
    except Exception:
        pass
    # 退路：ctypes 自己枚举
    return _pids_by_name(("Weixin.exe", "WeChat.exe"))


def _pids_by_name(names: str | tuple[str, ...]) -> list[int]:
    TH32CS_SNAPPROCESS = 0x00000002
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (0, -1):
        return []
    out: list[int] = []
    e = PE32()
    e.dwSize = ctypes.sizeof(PE32)
    targets = {names.lower()} if isinstance(names, str) else {n.lower() for n in names}
    try:
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if (e.szExeFile or "").lower() in targets:
                out.append(int(e.th32ProcessID))
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


_LAST_ACCESS_DENIED = False


def memory_candidates(progress=None) -> list[bytes]:
    """扫所有微信进程，返回去重后的 32 字节候选。"""
    global _LAST_ACCESS_DENIED
    _LAST_ACCESS_DENIED = False
    cands: list[bytes] = []
    seen: set[bytes] = set()
    denied = 0
    pids = _weixin_pids()
    for pid in pids:
        h = _k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not h:
            if ctypes.get_last_error() == 5:
                denied += 1
            continue
        try:
            hits, _ = _scan(h, [_STUB, _STUB_LOOSE], progress=progress)
            ptrs: list[int] = []
            for a in hits:
                # Stub 命中点可能是 32B 结构开头，也可能落在 0x20 那 16 字节上
                for delta in (-16, 0):
                    b = _read(h, a + delta, 8)
                    if b and len(b) == 8:
                        p = struct.unpack("<Q", b)[0]
                        if 0x10000 <= p <= 0x7FFFFFFFFFFF and p not in ptrs:
                            ptrs.append(p)
            for p in ptrs:
                k = _read(h, p, KEY_SIZE)
                if not k or len(k) != KEY_SIZE or k in seen:
                    continue
                if not is_potential_key(k):
                    continue
                seen.add(k)
                cands.append(k)
                if len(cands) >= 512:
                    return cands
        finally:
            _k32.CloseHandle(h)
    if denied > 0 and denied == len(pids):
        _LAST_ACCESS_DENIED = True
    return cands


# ── 第 3 步：HMAC 校验 ─────────────────────────────────────────────────────
def is_potential_key(k: bytes) -> bool:
    """熵/字符分布粗筛，省掉成千上万次 PBKDF2。"""
    if len(k) != KEY_SIZE:
        return False
    if len(set(k)) < 15:
        return False
    if sum(32 <= b <= 126 for b in k) > 24:
        return False
    if k == b"\x00" * KEY_SIZE:
        return False
    return True


def verify(passphrase: bytes, page1: bytes) -> bool:
    """page1 是某个库文件的前 4096 字节；HMAC 对得上就是这个账号的密钥。"""
    if len(page1) < PAGE_SIZE:
        return False
    salt = page1[:SALT_SIZE]
    enc = PBKDF2(passphrase, salt, dkLen=KEY_SIZE, count=ROUND_COUNT,
                 hmac_hash_module=SHA512)
    mac_key = PBKDF2(enc, bytes(b ^ 0x3A for b in salt), dkLen=KEY_SIZE, count=2,
                     hmac_hash_module=SHA512)
    mac = __import__("hmac").new(mac_key, page1[SALT_SIZE:PAGE_SIZE - RESERVE + IV_SIZE],
                                 SHA512)
    mac.update(struct.pack("<I", 1))
    tail = page1[PAGE_SIZE - RESERVE + IV_SIZE:PAGE_SIZE - RESERVE + IV_SIZE + HMAC_SIZE]
    return mac.digest() == tail


def xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def verify_many(keys: Iterable[bytes], page1: bytes, workers: int = 8,
                progress=None) -> Optional[bytes]:
    """verify() 的并行版：一堆候选里挑出能过 HMAC 的那把。

    单次 PBKDF2 是 256000 轮，本机实测 0.31s/次，纯串行判 1500 个候选要
    8 分钟。PBKDF2 是 C 实现、会放 GIL，所以开线程就能吃满多核
    （实测 8 线程 5.3x）。这是整个取密钥流程里唯一慢的环节，必须并行。
    """
    if len(page1) < PAGE_SIZE:
        return None
    ks = list(keys)
    if not ks:
        return None
    # 候选少的时候开线程反而更贵
    workers = max(1, min(workers, len(ks), (os.cpu_count() or 4)))
    if workers <= 1:
        for i, k in enumerate(ks, 1):
            if verify(k, page1):
                return k
            if progress and i % 8 == 0:
                progress(i, len(ks))
        return None
    try:
        from concurrent.futures import ThreadPoolExecutor
        done = 0
        chunk = workers * 2
        # 分块提交而不是一次性 map：命中后剩下的块根本不提交，
        # 平均情况能省掉一大半 —— 一次性 map 会老老实实把所有候选都算完。
        with ThreadPoolExecutor(workers) as ex:
            for i in range(0, len(ks), chunk):
                part = ks[i:i + chunk]
                for k, ok in zip(part, ex.map(lambda kk: verify(kk, page1), part)):
                    done += 1
                    if ok:
                        return k
                if progress:
                    progress(done, len(ks))
    except Exception:
        for k in ks:
            if verify(k, page1):
                return k
    return None


def _probe_db(account_root: str) -> Optional[str]:
    """挑一个用来校验的库 —— 越小越好，只要有 4096 字节。"""
    db_root = os.path.join(account_root, "db_storage")
    if not os.path.isdir(db_root):
        return None
    best: Optional[tuple[int, str]] = None
    for dp, _dn, fns in os.walk(db_root):
        for fn in fns:
            if not fn.endswith(".db"):
                continue
            p = os.path.join(dp, fn)
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            if sz < PAGE_SIZE:
                continue
            if best is None or sz < best[0]:
                best = (sz, p)
    return best[1] if best else None


# ── 对外主入口 ─────────────────────────────────────────────────────────────
_CAND_CACHE: Optional[list[bytes]] = None


def recover(account_root: str, dll_path: str = "", try_saved: Iterable[str] = (),
            progress=None, candidates: Optional[list[bytes]] = None,
            use_cache: bool = True) -> dict:
    """从运行中的微信里取出打开 account_root 下数据库所需的密钥。

    参数
    ----
    account_root  形如 C:\\Users\\x\\Documents\\xwechat_files\\wxid_xxx_yyyy
    dll_path      Weixin.dll 路径；不给就去装目录里找
    try_saved     之前存下的密钥（hex），先试这些，命中就不用扫内存
    candidates    现成的内存候选（多个账号共用一次扫描，省 90 秒）
    use_cache     允许复用模块级候选缓存

    返回
    ----
    {"ok":bool, "key":"<64hex>"|"", "how":"saved|memory", "tried":N,
     "dll_keys":N, "candidates":N, "secs":float, "error":"..."}
    """
    t0 = time.time()
    out = {"ok": False, "key": "", "how": "", "tried": 0,
           "dll_keys": 0, "candidates": 0, "secs": 0.0, "error": ""}

    db = _probe_db(account_root)
    if not db:
        out["error"] = "这个目录里没有 db_storage/*.db，不像微信账号目录"
        out["secs"] = round(time.time() - t0, 1)
        return out
    try:
        with open(db, "rb") as f:
            page1 = f.read(PAGE_SIZE)
    except OSError as e:
        out["error"] = f"读不了 {db}: {e}"
        out["secs"] = round(time.time() - t0, 1)
        return out
    if page1[:16] == b"SQLite format 3\x00":
        out.update(ok=True, how="plaintext", error="")
        out["note"] = "这个库本来就没加密"
        out["secs"] = round(time.time() - t0, 1)
        return out

    # ① 以前存过的密钥
    saved_keys: list[bytes] = []
    for h in try_saved:
        h = (h or "").strip().lower()
        if len(h) != 64:
            continue
        try:
            saved_keys.append(bytes.fromhex(h))
        except ValueError:
            continue
    out["tried"] += len(saved_keys)
    hit = verify_many(saved_keys, page1)
    if hit:
        out.update(ok=True, key=hit.hex(), how="saved")
        out["secs"] = round(time.time() - t0, 1)
        return out

    # ② 静态 XOR key
    if not dll_path:
        dll_path = find_dll()
    xks = dll_xor_keys(dll_path) if dll_path else []
    out["dll_keys"] = len(xks)

    # ③ 内存候选（多账号时共用一次扫描 —— 扫一遍要 90 秒，不该每个账号重扫）
    global _CAND_CACHE
    if candidates is not None:
        cands = candidates
    elif use_cache and _CAND_CACHE:
        cands = _CAND_CACHE
    else:
        cands = memory_candidates(progress=progress)
        if use_cache:
            _CAND_CACHE = cands
    out["candidates"] = len(cands)

    # 不去重就白算：候选 × XOR key 常常有大量重复
    combos: list[bytes] = []
    seen_k: set[bytes] = set()
    for c in cands:
        for k in [c] + [xor(c, xk) for xk in xks]:
            if k not in seen_k:
                seen_k.add(k)
                combos.append(k)
    out["combos"] = len(combos)
    out["tried"] += len(combos)

    if progress:
        progress(0, len(combos))
    hit = verify_many(combos, page1)
    if hit:
        out.update(ok=True, key=hit.hex(), how="memory")
        out["secs"] = round(time.time() - t0, 1)
        return out

    out["secs"] = round(time.time() - t0, 1)
    if is_wechat_v3_running():
        out["error"] = "检测到本机运行的是微信 3.x (WeChatWin.dll)。wxlocal 仅支持微信 4.x (WeChat 4.0+)，请先升级微信至 4.x 版本。"
    elif not dll_path or not xks:
        try:
            import pefile  # noqa: F401
            out["error"] = "未找到 Weixin.dll 或未能提取 XOR 密钥，请确保微信 4.x 正在运行。"
        except ImportError:
            out["error"] = "缺少依赖 pefile，无法提取 XOR 密钥。请运行 pip install pefile 后重试。"
    elif not cands:
        if _LAST_ACCESS_DENIED:
            out["error"] = "读取微信进程被系统拒绝（Access Denied）。微信可能以管理员身份运行，请尝试右键以管理员身份运行本程序。"
        else:
            out["error"] = ("内存里没找到密钥特征。多半是微信没在运行，或者版本变了。"
                            "先登录一下微信再试，也可以手动填密钥。")
    else:
        out["error"] = f"{len(cands)} 个候选都没通过校验，密钥可能已换，可手动填。"
    return out


# ── 找微信装在哪 / 数据在哪 ────────────────────────────────────────────────
def find_dll() -> str:
    """Weixin.dll 的完整路径（版本号最大的那个）。"""
    # 优先从运行中的微信进程模块直接取准确路径
    for pid in _weixin_pids():
        p = _find_module_path(pid, "weixin.dll")
        if p and os.path.exists(p):
            return p
    for base in wechat_installs():
        cands = []
        for d in os.listdir(base) if os.path.isdir(base) else []:
            p = os.path.join(base, d, "Weixin.dll")
            if os.path.exists(p):
                cands.append((_verkey(d), p))
        if cands:
            cands.sort(reverse=True)
            return cands[0][1]
        p = os.path.join(base, "Weixin.dll")
        if os.path.exists(p):
            return p
    return ""


def _verkey(s: str) -> tuple:
    parts = re.findall(r"\d+", s or "")
    return tuple(int(x) for x in parts[:4]) + (0,) * (4 - len(parts[:4]))


def wechat_installs() -> list[str]:
    """微信安装目录（注册表优先，再扫常见位置）。"""
    out: list[str] = []
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin"),
                          (winreg.HKEY_LOCAL_MACHINE, r"Software\Tencent\Weixin"),
                          (winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Tencent\WeChat")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for name in ("InstallPath", "InstallDir", "Path"):
                        try:
                            v = winreg.QueryValueEx(k, name)[0]
                        except OSError:
                            continue
                        if v and os.path.isdir(v) and v not in out:
                            out.append(v)
            except OSError:
                pass
    except ImportError:
        pass
    for p in (r"C:\Program Files\Tencent\Weixin",
              r"C:\Program Files (x86)\Tencent\Weixin",
              r"C:\Program Files\Tencent\WeChat",
              r"C:\Program Files (x86)\Tencent\WeChat"):
        if os.path.isdir(p) and p not in out:
            out.append(p)
    # 从运行中的进程反推
    try:
        import subprocess
        r = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-Process Weixin,WeChat -ErrorAction SilentlyContinue | "
             "Select-Object -First 1 -ExpandProperty Path)"],
            capture_output=True, text=True, timeout=15, creationflags=0x08000000)
        exe = r.stdout.strip()
        if exe and os.path.exists(exe):
            d = os.path.dirname(exe)
            if d not in out:
                out.insert(0, d)
    except Exception:
        pass
    return out


def _dir_size(p: str, cap: int = 400000) -> int:
    total = n = 0
    for dp, _dn, fns in os.walk(p):
        for fn in fns:
            try:
                total += os.path.getsize(os.path.join(dp, fn))
            except OSError:
                pass
            n += 1
            if n > cap:
                return total
    return total


def find_accounts(roots: Optional[list[str]] = None, deep: bool = True) -> list[dict]:
    """找出本机所有微信账号目录。

    判据：目录下有 `db_storage`（微信 4.x 账号根），或 `Msg/MicroMsg.db`（微信 3.x 账号根）。
    深度扫描时也会看 `Documents\\xwechat_files` 之外的常见备份位置。
    """
    if roots is None:
        roots = default_search_roots()
    found: dict[str, dict] = {}
    for base in roots:
        if not base or not os.path.isdir(base):
            continue
        # 直接把 account 目录传进来
        if os.path.isdir(os.path.join(base, "db_storage")) or _is_v3_account(base):
            _add_account(found, base, base, deep)
            continue
        try:
            entries = list(os.scandir(base))
        except OSError:
            continue
        for e in entries:
            try:
                if not e.is_dir():
                    continue
            except OSError:
                continue
            p = e.path
            if os.path.isdir(os.path.join(p, "db_storage")) or _is_v3_account(p):
                _add_account(found, p, base, deep)
                continue
            # 扫描第二层
            try:
                subs = list(os.scandir(p))
            except OSError:
                continue
            for s in subs:
                try:
                    if not s.is_dir():
                        continue
                    if os.path.isdir(os.path.join(s.path, "db_storage")) or _is_v3_account(s.path):
                        _add_account(found, s.path, base, deep)
                        continue
                    # 如果这层是 xwechat_files 或 WeChat Files，再扫一层它的子目录
                    if s.name.lower() in ("xwechat_files", "wechat files", "wechat_files"):
                        try:
                            for acc in os.scandir(s.path):
                                if acc.is_dir() and (os.path.isdir(os.path.join(acc.path, "db_storage")) or _is_v3_account(acc.path)):
                                    _add_account(found, acc.path, s.path, deep)
                        except OSError:
                            pass
                except OSError:
                    pass
    out = sorted(found.values(),
                 key=lambda a: (a.get("active", False), not a.get("v3", False), a["live"], a["size"],
                                a["db_count"]),
                 reverse=True)
    for a in out:
        a.pop("_size_raw", None)
    return out


def _quick_size(account_root: str) -> int:
    """deep=False 时的体积估算：只累加 db_storage 里的 .db 文件。

    别小看它 —— 不估体积的话所有账号 size 都是 0，排序退化成目录遍历顺序，
    最小的那个账号会被当成「主账号」命名为 pc。
    """
    total = 0
    for dp, _dn, fns in os.walk(os.path.join(account_root, "db_storage")):
        for f in fns:
            if f.endswith(".db"):
                try:
                    total += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
    return total


def _is_v3_account(path: str) -> bool:
    """判断是否为微信 3.x (旧版) 账号目录（含有 Msg/MicroMsg.db 或 Msg/MSG0.db 且没有 db_storage）。"""
    if not path or not os.path.isdir(path):
        return False
    if os.path.isdir(os.path.join(path, "db_storage")):
        return False
    msg_dir = os.path.join(path, "Msg")
    if os.path.isdir(msg_dir):
        if any(os.path.exists(os.path.join(msg_dir, fn)) for fn in ("MicroMsg.db", "MSG0.db", "FTSMSG.db")):
            return True
    return False


def _add_account(found: dict, path: str, base: str, deep: bool) -> None:
    if path in found:
        return
    name = os.path.basename(path.rstrip("\\/"))
    wxid = name.rsplit("_", 1)[0] if name.count("_") >= 2 else name
    is_v3 = _is_v3_account(path)
    size = _dir_size(path) if deep else _quick_size(path)
    found[path] = {
        "path": path,
        "base": base,
        "account": name,
        "wxid": wxid,
        "v3": is_v3,
        "size": size,
        "size_gb": round(size / 1073741824, 2),
        "live": _is_live(base, name),
        "active": _is_active(path) if not is_v3 else False,
        "locked_dbs": _locked_dbs(path) if not is_v3 else 0,
        "db_count": _count_dbs(path) if deep else 0,
    }


def _is_live(base: str, name: str) -> bool:
    """目录位置意义上的「像正版数据目录」（xwechat_files / Documents 下）。

    注意这**不等于**「现在登录着」—— 这里只看路径。判断当前活跃账号要用
    `_is_active()`：已登录账号的库会被微信独占打开，没登录的不会。
    """
    low = base.lower()
    return low.endswith("xwechat_files") or "documents" in low


_CF_READ = 0x80000000
_CF_OPEN_EXISTING = 3
_CF_NORMAL = 0x80
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_ERROR_SHARING_VIOLATION = 32


def _file_locked(path: str) -> bool:
    """这个文件是不是被别的进程独占打开了。

    用 CreateFileW 要 0 共享权限 —— 微信开着库时必然 ERROR_SHARING_VIOLATION，
    这是判断「哪个账号正在登录」最便宜也最准的办法（不用开进程、不用读内存）。
    """
    try:
        h = _k32.CreateFileW(path, _CF_READ, 0, None, _CF_OPEN_EXISTING,
                             _CF_NORMAL, None)
    except Exception:
        return False
    if not h or h == _INVALID_HANDLE:
        return ctypes.get_last_error() == _ERROR_SHARING_VIOLATION
    _k32.CloseHandle(h)
    return False


def _locked_dbs(account_root: str, cap: int = 200) -> int:
    """数一下有多少个库被独占 —— 大于是 0 就是当前登录的账号。"""
    root = os.path.join(account_root, "db_storage")
    if not os.path.isdir(root):
        return 0
    n = 0
    for dp, _dn, fns in os.walk(root):
        for f in fns:
            if not f.endswith(".db"):
                continue
            if _file_locked(os.path.join(dp, f)):
                n += 1
                if n >= cap:
                    return n
    return n


def _is_active(account_root: str) -> bool:
    """这个账号现在是不是登录着的（库被微信开着）。

    带 4 秒缓存：引导面板一秒探一次活，每次真去 walk 一遍 db_storage
    要 1.6 秒，纯属浪费（而且探活不该比人眼还慢）。
    """
    key = os.path.normcase(os.path.abspath(account_root))
    now = time.time()
    hit = _ACTIVE_CACHE.get(key)
    if hit and now - hit[0] < 4.0:
        return hit[1]
    v = _locked_dbs(account_root, cap=8) > 0
    _ACTIVE_CACHE[key] = (now, v)
    return v


_ACTIVE_CACHE: dict[str, tuple[float, bool]] = {}


def _count_dbs(account_root: str) -> int:
    n = 0
    for _dp, _dn, fns in os.walk(os.path.join(account_root, "db_storage")):
        n += sum(1 for f in fns if f.endswith(".db"))
    return n


def default_search_roots() -> list[str]:
    """默认扫这些地方（涵盖真实系统文档、OneDrive、常用微信自定义盘符）。"""
    out: list[str] = []

    # 1. 查询 Windows 系统真实个人文档目录（含 OneDrive / 自定义重定向路径）
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            for name in ("Personal", "{F42EE2D3-909F-4907-8871-4C22FC0BF756}"):
                try:
                    val = winreg.QueryValueEx(k, name)[0]
                    p = os.path.expandvars(val)
                    if os.path.isdir(p):
                        for sub in ("xwechat_files", "WeChat Files"):
                            cand = os.path.join(p, sub)
                            if os.path.isdir(cand) and cand not in out:
                                out.append(cand)
                except OSError:
                    pass
    except Exception:
        pass

    # 2. 用户主目录及各种常见文档变体
    home = os.path.expanduser("~")
    for doc_cand in (
        os.path.join(home, "Documents"),
        os.path.join(home, "文档"),
        os.path.join(home, "OneDrive", "Documents"),
        os.path.join(home, "OneDrive", "文档"),
    ):
        if not os.path.isdir(doc_cand):
            continue
        for sub in ("xwechat_files", "WeChat Files"):
            cand = os.path.join(doc_cand, sub)
            if os.path.isdir(cand) and cand not in out:
                out.append(cand)

    # 3. 微信注册表里登记的自定义目录
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin"),
                          (winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for name in ("FileSavePath", "DataPath", "UserDataPath"):
                        try:
                            v = winreg.QueryValueEx(k, name)[0]
                            if v and v != "1" and os.path.isdir(v) and v not in out:
                                out.insert(0, v)
                        except OSError:
                            pass
            except OSError:
                pass
    except Exception:
        pass

    # 4. 常见各盘根目录及二级常用微信目录
    for drive in ("C:", "D:", "E:", "F:", "G:"):
        dp = drive + "\\"
        if not os.path.isdir(dp):
            continue
        for sub in ("xwechat_files", "WeChat Files", "WeChat\\xwechat_files",
                    "WeChat\\WeChat Files", "Tencent\\xwechat_files", "Tencent\\WeChat Files"):
            cand = os.path.join(dp, sub)
            if os.path.isdir(cand) and cand not in out:
                out.append(cand)
        out.append(dp)

    return out


# ── CLI ────────────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    import json
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        print("用法：")
        print("  python keyfind.py --scan                 列出本机所有微信账号目录")
        print("  python keyfind.py --key <账号目录>        试着取密钥")
        print("  python keyfind.py --dll                  打印 Weixin.dll 路径与 XOR 候选")
        return 2
    if argv[1] == "--dll":
        d = find_dll()
        print("dll:", d or "(没找到)")
        for k in dll_xor_keys(d):
            print("  xor key:", k.hex())
        return 0
    if argv[1] == "--scan":
        print(json.dumps(find_accounts(), ensure_ascii=False, indent=2))
        return 0
    if argv[1] == "--key":
        if len(argv) < 3:
            print("缺账号目录")
            return 2
        r = recover(argv[2])
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1
    print("不认识的参数")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))

