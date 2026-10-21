#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wxdecrypt.py — 微信 4.x (WCDB / SQLCipher 4) 单库解密 + WAL 合并。

只读源文件；输出到目标路径。WAL 合并依据 sqlite.org/fileformat2.html#walformat：
SQLCipher 的 WAL 帧校验和算在**加密负载**上，所以可以在密文态直接拼页，再逐页解密。

用法:
    python wxdecrypt.py <passphrase_hex> <src_db> <dst_db>
    python wxdecrypt.py <passphrase_hex> <src_dir> <dst_dir>     # 递归整个目录
"""
from __future__ import annotations

import glob
import hashlib
import hmac
import os
import struct
import sys

from Crypto.Cipher import AES
from Crypto.Hash import SHA512
from Crypto.Protocol.KDF import PBKDF2

PAGE_SIZE = 4096
SALT_SIZE = 16
KEY_SIZE = 32
IV_SIZE = 16
HMAC_SIZE = 64
ROUND_COUNT = 256000
RESERVE = ((IV_SIZE + HMAC_SIZE + 15) // 16) * 16   # 80
SQLITE_HEADER = b"SQLite format 3\x00"


# ── WAL ────────────────────────────────────────────────────────────────────
def _checksum(data: bytes, endian: str, state: tuple[int, int] = (0, 0)) -> tuple[int, int]:
    a, b = state
    for x, y in struct.iter_unpack(endian + "II", data):
        a = (a + x + b) & 0xFFFFFFFF
        b = (b + y + a) & 0xFFFFFFFF
    return a, b


def merge_wal(database: bytes, wal: bytes, page_size: int = PAGE_SIZE):
    """把 WAL 中校验通过的已提交帧合并进数据库镜像（全在密文态）。"""
    info = {"wal_bytes": len(wal), "committed_frames": 0, "applied_pages": 0,
            "database_pages": len(database) // page_size}
    if not wal or len(wal) < 32:
        return database, info
    magic, version, wal_page_size = struct.unpack(">III", wal[:12])
    if magic not in (0x377F0682, 0x377F0683) or version != 3007000:
        return database, info
    if wal_page_size != page_size or len(database) % page_size:
        return database, info
    endian = "<" if magic == 0x377F0682 else ">"
    state = _checksum(wal[:24], endian)
    if state != struct.unpack(">II", wal[24:32]):
        return database, info
    salt = wal[16:24]
    frame_size = 24 + page_size
    pending: dict[int, bytes] = {}
    committed: dict[int, bytes] = {}
    base_pages = len(database) // page_size
    size = base_pages
    commit_end = 32
    for offset in range(32, len(wal) - frame_size + 1, frame_size):
        header = wal[offset:offset + 24]
        if header[8:16] != salt:
            break                      # WAL 重置后遗留的旧帧 / 预分配零尾
        pgno, db_size = struct.unpack(">II", header[:8])
        if pgno == 0 or pgno > 0xFFFFFFFE:
            break
        page = wal[offset + 24:offset + frame_size]
        state = _checksum(page, endian, _checksum(header[:8], endian, state))
        if state != struct.unpack(">II", header[16:24]):
            break                      # 帧校验失败 → 停在最后一个完好提交点
        pending[pgno] = page
        if db_size:
            committed.update(pending)
            pending.clear()
            committed = {n: p for n, p in committed.items() if n <= db_size}
            base_pages = min(base_pages, db_size)
            size = db_size
            commit_end = offset + frame_size
            info["committed_frames"] = (commit_end - 32) // frame_size
    if not info["committed_frames"]:
        return database, info
    out = bytearray(database[:base_pages * page_size])
    out.extend(b"\0" * (size * page_size - len(out)))
    for pgno, page in committed.items():
        out[(pgno - 1) * page_size:pgno * page_size] = page
    info.update(applied_pages=len(committed), database_pages=size)
    return bytes(out), info


# ── SQLCipher 页 ───────────────────────────────────────────────────────────
def derive_keys(passphrase: bytes, salt: bytes):
    enc = PBKDF2(passphrase, salt, dkLen=KEY_SIZE, count=ROUND_COUNT,
                 hmac_hash_module=SHA512)
    mac_key = PBKDF2(enc, bytes(b ^ 0x3A for b in salt), dkLen=KEY_SIZE,
                     count=2, hmac_hash_module=SHA512)
    return enc, mac_key


def _page_mac(mac_key: bytes, page: bytes, pgno: int) -> bytes:
    body = page[SALT_SIZE:PAGE_SIZE - RESERVE + IV_SIZE] if pgno == 1 \
        else page[:PAGE_SIZE - RESERVE + IV_SIZE]
    m = hmac.new(mac_key, body, SHA512)
    m.update(struct.pack("<I", pgno))
    return m.digest()


def verify_key(raw: bytes, passphrase: bytes) -> bool:
    """只验第一页 HMAC —— 成本约等于一次 PBKDF2(256000) 迭代。"""
    if len(raw) < PAGE_SIZE:
        return False
    enc, mac_key = derive_keys(passphrase, raw[:SALT_SIZE])
    return _page_mac(mac_key, raw[:PAGE_SIZE], 1) == raw[PAGE_SIZE - RESERVE + IV_SIZE:PAGE_SIZE]


def decrypt_db(src: str, dst: str, passphrase: bytes, use_wal: bool = True):
    with open(src, "rb") as f:
        raw = f.read()
    if len(raw) < PAGE_SIZE:
        return "skip:too-small", {}
    wal_info: dict = {}
    if use_wal:
        wp = src + "-wal"
        if os.path.exists(wp):
            try:
                with open(wp, "rb") as f:
                    wal = f.read()
                raw, wal_info = merge_wal(raw, wal)
            except OSError:
                pass
    enc, mac_key = derive_keys(passphrase, raw[:SALT_SIZE])
    if _page_mac(mac_key, raw[:PAGE_SIZE], 1) != raw[PAGE_SIZE - RESERVE + IV_SIZE:PAGE_SIZE]:
        return "fail:hmac", wal_info

    npages = len(raw) // PAGE_SIZE
    out = bytearray(npages * PAGE_SIZE)
    bad = 0
    for i in range(npages):
        pgno = i + 1
        base = i * PAGE_SIZE
        page = raw[base:base + PAGE_SIZE]
        if len(page) < PAGE_SIZE:
            out[base:base + PAGE_SIZE] = b"\0" * PAGE_SIZE
            continue
        try:
            start = SALT_SIZE if pgno == 1 else 0
            ct = page[start:PAGE_SIZE - RESERVE]
            iv = page[PAGE_SIZE - RESERVE:PAGE_SIZE - RESERVE + IV_SIZE]
            plain = AES.new(enc, AES.MODE_CBC, iv).decrypt(ct)
            if pgno == 1:
                plain = SQLITE_HEADER + plain
            out[base:base + PAGE_SIZE] = plain + b"\0" * RESERVE
        except Exception:
            bad += 1
            out[base:base + PAGE_SIZE] = b"\0" * PAGE_SIZE
    # 页数 = 实际写出的页，避免 WAL 截断后 header 里的旧页数误导 SQLite
    out[28:32] = npages.to_bytes(4, "big")
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    tmp = dst + ".tmp"
    with open(tmp, "wb") as f:
        f.write(out)
    os.replace(tmp, dst)
    return f"ok:{npages}p" + (f",{bad}bad" if bad else ""), wal_info


# ── CLI ────────────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    passphrase = bytes.fromhex(argv[1])
    src, dst = argv[2], argv[3]
    if os.path.isdir(src):
        files = sorted(f for f in glob.glob(os.path.join(src, "**", "*.db"), recursive=True)
                       if not f.endswith(("-wal", "-shm"))
                       and ".factory" not in f)
        ok = fail = 0
        for i, f in enumerate(files, 1):
            rel = os.path.relpath(f, src)
            res, _ = decrypt_db(f, os.path.join(dst, rel), passphrase)
            if res.startswith("ok"):
                ok += 1
            else:
                fail += 1
                print(f"  [{i}/{len(files)}] {res:18s} {rel}")
        print(f"ok {ok} fail {fail}")
        return 0
    res, wi = decrypt_db(src, dst, passphrase)
    print(res, wi)
    return 0 if res.startswith("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
