#!/usr/bin/env python3
"""Builds CORE4SD.PO: a 140K (or, with --blocks 1600, 800K) ProDOS volume
/CORE4SD holding CORE4SD.SYSTEM (type SYS $FF, aux $2000).

Block 0 is the SD-Browser boot block (apple/boot.asm) with its block table
pointing at the data blocks of CORE4SD.SYSTEM, so the image boots straight
into the browser (no ProDOS needed) and ProDOS still sees a normal volume
with CORE4SD.SYSTEM on it.

usage: mkpo.py [--blocks N] boot.bin CORE4SD.SYSTEM CORE4SD.PO [VOLNAME]

  --blocks N   size of the volume in blocks (default 280 = 140K,
               1600 = 800K; at most 4096, one bitmap block)
"""
import datetime
import struct
import sys

TOTAL = 280                 # 140K (default)
VOLDIR = [2, 3, 4, 5]       # volume directory blocks
BITMAP = 6
CNT_OFF, TAB_OFF, TAB_MAX = 0x1BF, 0x1C0, 32


def prodos_datetime(t):
    date = ((t.year % 100) << 9) | (t.month << 5) | t.day
    time = (t.hour << 8) | t.minute
    return struct.pack("<HH", date, time)


def name_field(name):
    n = name.upper().encode("ascii")
    assert 1 <= len(n) <= 15 and n[:1].isalpha()
    return len(n), n.ljust(15, b"\0")


def build(boot, prog, volname="CORE4SD", fname="CORE4SD.SYSTEM", total=TOTAL):
    assert BITMAP + 2 <= total <= 4096, "one bitmap block: 4096 blocks at most"
    now = prodos_datetime(datetime.datetime.now())
    blocks = [bytearray(512) for _ in range(total)]

    # --- file: seedling (1 block) or sapling (index + up to 256 blocks)
    ndata = max(1, (len(prog) + 511) // 512)
    assert ndata <= TAB_MAX, "program too large for the boot table"
    nxt = BITMAP + 1
    if ndata == 1:
        storage, key, data = 1, nxt, [nxt]
        used = 1
    else:
        storage, key = 2, nxt
        data = list(range(nxt + 1, nxt + 1 + ndata))
        used = ndata + 1
        for i, b in enumerate(data):
            blocks[key][i] = b & 0xFF
            blocks[key][256 + i] = b >> 8
    for i, b in enumerate(data):
        chunk = prog[i * 512:(i + 1) * 512]
        blocks[b][:len(chunk)] = chunk
    last_used = max(data + [key])

    # --- boot block, pointed at the data blocks
    bb = bytearray(boot)
    assert len(bb) == 512 and bb[0] == 0x01
    bb[CNT_OFF] = ndata
    for i in range(TAB_MAX):
        struct.pack_into("<H", bb, TAB_OFF + 2 * i, data[i] if i < ndata else 0)
    blocks[0][:] = bb

    # --- volume directory
    for i, b in enumerate(VOLDIR):
        prev = VOLDIR[i - 1] if i else 0
        nx = VOLDIR[i + 1] if i + 1 < len(VOLDIR) else 0
        struct.pack_into("<HH", blocks[b], 0, prev, nx)
    hdr = blocks[VOLDIR[0]]
    ln, nm = name_field(volname)
    e = 4
    hdr[e] = 0xF0 | ln
    hdr[e + 1:e + 16] = nm
    hdr[e + 0x18:e + 0x1C] = now
    hdr[e + 0x1C] = 0                      # version
    hdr[e + 0x1D] = 0                      # min version
    hdr[e + 0x1E] = 0xC3                   # access
    hdr[e + 0x1F] = 0x27                   # entry length
    hdr[e + 0x20] = 0x0D                   # entries per block
    struct.pack_into("<HHH", hdr, e + 0x21, 1, BITMAP, total)

    ent = hdr
    e = 4 + 0x27                           # first file entry
    ln, nm = name_field(fname)
    ent[e] = (storage << 4) | ln
    ent[e + 1:e + 16] = nm
    ent[e + 0x10] = 0xFF                   # SYS
    struct.pack_into("<HH", ent, e + 0x11, key, used)
    ent[e + 0x15:e + 0x18] = struct.pack("<I", len(prog))[:3]
    ent[e + 0x18:e + 0x1C] = now
    ent[e + 0x1C] = 0
    ent[e + 0x1D] = 0
    ent[e + 0x1E] = 0xE3                   # destroy, rename, backup, write, read
    struct.pack_into("<H", ent, e + 0x1F, 0x2000)
    ent[e + 0x21:e + 0x25] = now
    struct.pack_into("<H", ent, e + 0x25, VOLDIR[0])

    # --- bitmap: 1 = free
    bm = blocks[BITMAP]
    for b in range(last_used + 1, total):
        bm[b >> 3] |= 0x80 >> (b & 7)

    return b"".join(bytes(b) for b in blocks)


def main():
    args = sys.argv[1:]
    total = TOTAL
    if args[:1] == ["--blocks"] and len(args) > 1:
        total = int(args[1])
        args = args[2:]
    if len(args) < 3:
        sys.exit(__doc__)
    boot = open(args[0], "rb").read()
    prog = open(args[1], "rb").read()
    vol = args[3] if len(args) > 3 else "CORE4SD"
    open(args[2], "wb").write(build(boot, prog, vol, total=total))


if __name__ == "__main__":
    main()
