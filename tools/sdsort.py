#!/usr/bin/env python3
"""
sdsort.py -- sorts the directories of a FAT16/FAT32 SD card alphabetically.

Core4SD (like any FAT device) lists files in the order in which they are
stored in the directory. This script rewrites the entries of every directory
in sorted order: directories first, then files, case-insensitive, optionally
"natural" (DISK2 before DISK10). File contents, the FAT and the clusters are
left alone; only the 32-byte directory entries are rearranged (long names
stay with their short name, deleted entries are dropped).

Usage (a card needs root / administrator rights):

    Linux:    sudo umount /dev/sdb1                       # unmount first
              sudo python3 sdsort.py /dev/sdb1            # show only
              sudo python3 sdsort.py --write /dev/sdb1    # sort
    macOS:    diskutil unmount /dev/disk4s1               # unmount first
              sudo python3 sdsort.py --write /dev/rdisk4s1
    Windows:  (prompt "Run as administrator", close Explorer windows of the card)
              python sdsort.py E:                         # show only
              python sdsort.py --write E:                 # sort
              The drive is locked and dismounted while writing; Windows
              mounts it again afterwards. The system drive (C:) is always
              refused, and only a FAT12/16/32 volume is accepted (not NTFS
              or exFAT).
    Image:    python3 sdsort.py --write card.img          # also a whole card with MBR

Options (python3 sdsort.py -h shows the same):
    --write          really write. Without it nothing is changed; every
                     unsorted directory is listed with its first 12 entries
                     in the new order.
    -n, --natural    natural order: numbers are compared by value, so DISK2
                     comes before DISK10 (default: character by character).
    --files-first    files before directories (default: directories first).
    --mixed          directories and files mixed, sorted only by name.
    --case           case-sensitive: upper case before lower case (default:
                     case-insensitive).
    --only PATH      only this directory, e.g. /GAMES, without its
                     subdirectories (default: the whole card).
    -q, --quiet      no output except errors; exit code 0 = ok, 1 = error.
    -v, --verbose    also list the directories that are already sorted.

Python 3 only, no extra packages. Backing up the card first is still a good
idea.
"""
import argparse
import os
import re
import struct
import subprocess
import sys
import time

ATTR_RO, ATTR_HIDDEN, ATTR_SYSTEM, ATTR_VOLUME, ATTR_DIR, ATTR_ARCHIVE = 1, 2, 4, 8, 16, 32
ATTR_LFN = 0x0F

WINDOWS = sys.platform == "win32"
FSCTL_LOCK_VOLUME = 0x00090018
FSCTL_UNLOCK_VOLUME = 0x0009001C
FSCTL_DISMOUNT_VOLUME = 0x00090020


class FatError(Exception):
    pass


def device_path(dev):
    """Windows: a drive letter (E:, E:\\) becomes the volume device \\\\.\\E:."""
    if WINDOWS and re.fullmatch(r"[A-Za-z]:\\?", dev):
        return "\\\\.\\" + dev[0].upper() + ":"
    return dev


def is_win_device(path):
    return WINDOWS and path.startswith("\\\\.\\")


def check_not_system(dev):
    """Windows: refuse the system drive (%SystemDrive%, normally C:) and the
    first physical disk, which holds it -- for reading as well as writing."""
    if not is_win_device(dev):
        return
    name = dev[4:].upper()                    # "C:", "E:", "PHYSICALDRIVE0"
    system = os.environ.get("SystemDrive", "C:").upper()
    if name in (system, "C:", "PHYSICALDRIVE0"):
        raise FatError("%s is the Windows system drive -- refused. Give the drive "
                       "letter of the SD card, e.g. E:" % dev[4:])


# ----------------------------------------------------------------------------
# Block access (whole sectors, so raw devices on macOS and Windows work too)
# ----------------------------------------------------------------------------

_K32 = None


def kernel32():
    """kernel32 with the prototypes used here (Windows only)."""
    global _K32
    if _K32 is None:
        import ctypes
        from ctypes import wintypes as w
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
        k.CreateFileW.restype = w.HANDLE
        k.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, w.LPVOID, w.DWORD, w.LPVOID, w.DWORD,
                                      ctypes.POINTER(w.DWORD), w.LPVOID]
        k.DeviceIoControl.restype = w.BOOL
        k.SetFilePointerEx.argtypes = [w.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), w.DWORD]
        k.SetFilePointerEx.restype = w.BOOL
        k.ReadFile.argtypes = [w.HANDLE, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), w.LPVOID]
        k.ReadFile.restype = w.BOOL
        k.WriteFile.argtypes = [w.HANDLE, w.LPCVOID, w.DWORD, ctypes.POINTER(w.DWORD), w.LPVOID]
        k.WriteFile.restype = w.BOOL
        k.FlushFileBuffers.argtypes = [w.HANDLE]
        k.FlushFileBuffers.restype = w.BOOL
        k.CloseHandle.argtypes = [w.HANDLE]
        k.CloseHandle.restype = w.BOOL
        _K32 = k
    return _K32


INVALID_HANDLE = (None, -1, 2 ** 32 - 1, 2 ** 64 - 1)


class Disk:
    """Whole-sector access to a device or an image file. A Windows volume
    goes through its Win32 handle: the C runtime would turn a Windows error
    into a meaningless "Bad file descriptor"."""

    def __init__(self, path, write):
        self.path = path
        self.base = 0          # byte offset of the partition
        self.bps = 512
        self.fd = None
        self.handle = None
        self.locked = False
        self.written = False
        if is_win_device(path):
            if write and "PHYSICALDRIVE" in path.upper():
                raise FatError("on Windows --write needs the drive letter (e.g. E:), not %s" % path)
            self._win_open(path, write)
            return
        try:
            self.fd = os.open(path, (os.O_RDWR if write else os.O_RDONLY) | getattr(os, "O_BINARY", 0))
        except PermissionError:
            if path.startswith("/dev/"):
                raise FatError("%s: permission denied -- run with sudo" % path)
            raise

    # --- Windows --------------------------------------------------------------

    def _win_error(self, what, err=None):
        import ctypes
        if err is None:
            err = ctypes.get_last_error()
        return FatError("%s: %s failed (Windows error %d: %s)" % (
            self.path, what, err, ctypes.FormatError(err).strip()))

    # Windows refuses writes to a mounted FAT volume, and would keep showing
    # its cached directories: lock the volume (no open files on it), then
    # dismount it. Closing the handle releases it; Windows mounts it again
    # on the next access.
    def _win_open(self, path, write):
        import ctypes
        k = kernel32()
        access = 0x80000000 | (0x40000000 if write else 0)          # GENERIC_READ | GENERIC_WRITE
        h = k.CreateFileW(path, access, 3, None, 3, 0, None)         # share read+write, OPEN_EXISTING
        if h in INVALID_HANDLE:
            err = ctypes.get_last_error()
            if err == 5:
                raise FatError("%s: access denied -- run the prompt as administrator" % path)
            raise self._win_error("open", err)
        self.handle = h
        if not write:
            return
        for _ in range(10):                   # files may still be closing
            if self._ioctl(FSCTL_LOCK_VOLUME):
                break
            time.sleep(0.5)
        else:
            e = self._win_error("lock")
            self.close()
            raise FatError("%s -- close Explorer windows and programs that use the card" % e)
        self.locked = True
        if not self._ioctl(FSCTL_DISMOUNT_VOLUME):
            e = self._win_error("dismount")
            self.close()
            raise e

    def _ioctl(self, code):
        import ctypes
        from ctypes import wintypes
        n = wintypes.DWORD()
        return bool(kernel32().DeviceIoControl(self.handle, code, None, 0, None, 0, ctypes.byref(n), None))

    def _win_seek(self, pos):
        if not kernel32().SetFilePointerEx(self.handle, pos, None, 0):
            raise self._win_error("seek to byte %d" % pos)

    def _win_read(self, pos, n):
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_string_buffer(n)
        got = wintypes.DWORD()
        self._win_seek(pos)
        if not kernel32().ReadFile(self.handle, buf, n, ctypes.byref(got), None) or got.value != n:
            raise self._win_error("read at byte %d" % pos)
        return buf.raw

    def _win_write(self, pos, data):
        import ctypes
        from ctypes import wintypes
        done = wintypes.DWORD()
        for _ in range(5):                    # Windows may still be busy with the volume
            self._win_seek(pos)
            if kernel32().WriteFile(self.handle, data, len(data), ctypes.byref(done), None) \
                    and done.value == len(data):
                return
            err = self._win_error("write at byte %d" % pos)
            time.sleep(0.5)
        raise err

    # --- all platforms ----------------------------------------------------------

    def read(self, off, n):
        pos = self.base + off
        start = pos - pos % 512
        end = -(-(pos + n) // 512) * 512
        if self.handle is not None:
            data = self._win_read(start, end - start)
        else:
            os.lseek(self.fd, start, os.SEEK_SET)
            data = b""
            while len(data) < end - start:
                chunk = os.read(self.fd, end - start - len(data))
                if not chunk:
                    raise FatError("read error at byte %d" % (start + len(data)))
                data += chunk
        return data[pos - start:pos - start + n]

    def write(self, off, data):
        pos = self.base + off
        if pos % 512 or len(data) % 512:
            raise FatError("internal error: write not sector-aligned")
        self.written = True
        if self.handle is not None:
            self._win_write(pos, bytes(data))
            return
        os.lseek(self.fd, pos, os.SEEK_SET)
        done = 0
        while done < len(data):
            done += os.write(self.fd, data[done:])

    def close(self):
        if self.handle is not None:
            k = kernel32()
            if self.written:
                k.FlushFileBuffers(self.handle)
            if self.locked:
                self._ioctl(FSCTL_UNLOCK_VOLUME)
            k.CloseHandle(self.handle)
            self.handle = None
        elif self.fd is not None:
            if self.written:
                os.fsync(self.fd)
            os.close(self.fd)
            self.fd = None


# ----------------------------------------------------------------------------
# File system
# ----------------------------------------------------------------------------

def is_boot_sector(b):
    """A FAT12/16/32 boot sector. NTFS and exFAT pass the signature and jump
    checks too, so the BPB must also make sense for FAT: NTFS has 0 reserved
    sectors and 0 FATs, exFAT has 0 bytes per sector."""
    if b[510:512] != b"\x55\xAA" or b[0] not in (0xEB, 0xE9):
        return False
    if b[3:11] in (b"NTFS    ", b"EXFAT   "):
        return False
    bps, spc, reserved, nfats = struct.unpack_from("<HBHB", b, 11)
    fatsz = struct.unpack_from("<H", b, 22)[0] or struct.unpack_from("<I", b, 36)[0]
    return (bps in (512, 1024, 2048, 4096) and spc and not (spc & (spc - 1))
            and reserved >= 1 and nfats in (1, 2) and fatsz > 0)


class Fat:
    def __init__(self, disk):
        self.disk = disk
        b = disk.read(0, 512)
        if not is_boot_sector(b):
            # a whole card with an MBR? find the first FAT partition
            if b[510:512] != b"\x55\xAA":
                raise FatError("no FAT file system and no partition table found")
            for i in range(4):
                ptype = b[446 + 16 * i + 4]
                lba = struct.unpack_from("<I", b, 446 + 16 * i + 8)[0]
                if ptype in (0x01, 0x04, 0x06, 0x0B, 0x0C, 0x0E) and lba:
                    disk.base = lba * 512
                    b = disk.read(0, 512)
                    break
            if not is_boot_sector(b):
                raise FatError("no FAT partition found")
        (self.bps, self.spc, self.reserved, self.nfats, self.root_entries,
         tot16, _, fatsz16, _, _, _, tot32) = struct.unpack_from("<HBHBHHBHHHII", b, 11)
        fatsz = fatsz16 or struct.unpack_from("<I", b, 36)[0]
        total = tot16 or tot32
        self.cluster_size = self.bps * self.spc
        self.fat_start = self.reserved * self.bps
        root_sectors = (self.root_entries * 32 + self.bps - 1) // self.bps
        self.root_start = (self.reserved + self.nfats * fatsz) * self.bps
        self.data_start = self.root_start + root_sectors * self.bps
        clusters = (total - self.reserved - self.nfats * fatsz - root_sectors) // self.spc
        if clusters < 4085:
            raise FatError("FAT12 is not supported")
        self.fat32 = clusters >= 65525
        self.root_cluster = struct.unpack_from("<I", b, 44)[0] if self.fat32 else 0
        self.fat = disk.read(self.fat_start, fatsz * self.bps)
        self.max_cluster = clusters + 1

    def next_cluster(self, c):
        if self.fat32:
            return struct.unpack_from("<I", self.fat, c * 4)[0] & 0x0FFFFFFF
        return struct.unpack_from("<H", self.fat, c * 2)[0]

    def chain(self, first):
        end = 0x0FFFFFF8 if self.fat32 else 0xFFF8
        out, c = [], first
        while 2 <= c <= self.max_cluster:
            if c in out:
                raise FatError("loop in a cluster chain")
            out.append(c)
            c = self.next_cluster(c)
            if c >= end:
                break
        return out

    def cluster_offset(self, c):
        return self.data_start + (c - 2) * self.cluster_size

    # A directory as a list of (offset, length) regions.
    def dir_regions(self, first_cluster):
        if first_cluster == 0 and not self.fat32:
            return [(self.root_start, self.root_entries * 32)]
        if first_cluster == 0:
            first_cluster = self.root_cluster
        return [(self.cluster_offset(c), self.cluster_size) for c in self.chain(first_cluster)]

    def read_dir(self, regions):
        return b"".join(self.disk.read(o, n) for o, n in regions)

    def write_dir(self, regions, data):
        pos = 0
        for o, n in regions:
            self.disk.write(o, data[pos:pos + n])
            pos += n


# ----------------------------------------------------------------------------
# Directory entries
# ----------------------------------------------------------------------------

def sfn_checksum(name11):
    s = 0
    for c in name11:
        s = (((s & 1) << 7) + (s >> 1) + c) & 0xFF
    return s


def sfn_text(e):
    base, ext = e[0:8].decode("latin-1").rstrip(), e[8:11].decode("latin-1").rstrip()
    if base[:1] == "\x05":
        base = "\xe5" + base[1:]
    if e[12] & 0x08:
        base = base.lower()
    if e[12] & 0x10:
        ext = ext.lower()
    return base + ("." + ext if ext else "")


def lfn_text(lfns):
    chars = []
    for e in reversed(lfns):         # the last piece is stored first
        raw = e[1:11] + e[14:26] + e[28:32]
        for i in range(0, 26, 2):
            ch = struct.unpack_from("<H", raw, i)[0]
            if ch in (0x0000, 0xFFFF):
                return "".join(chars)
            chars.append(chr(ch))
    return "".join(chars)


class Entry:
    def __init__(self, lfns, sfn):
        self.raw = b"".join(lfns) + sfn
        self.sfn = sfn
        self.name = lfn_text(lfns) if lfns else sfn_text(sfn)
        self.is_dir = bool(sfn[11] & ATTR_DIR)
        hi, lo = struct.unpack_from("<H", sfn, 20)[0], struct.unpack_from("<H", sfn, 26)[0]
        self.cluster = (hi << 16) | lo


def parse_dir(data, path):
    """-> (fixed entries at the front, sortable entries)."""
    fixed, entries, lfns = [], [], []
    for i in range(0, len(data), 32):
        e = data[i:i + 32]
        if e[0] == 0x00:
            break
        if e[0] == 0xE5:
            lfns = []
            continue
        attr = e[11]
        if attr & 0x3F == ATTR_LFN:
            if e[0] & 0x40:
                lfns = [e]
            elif lfns and (e[0] & 0x1F) == (lfns[-1][0] & 0x1F) - 1:
                lfns.append(e)
            else:
                raise FatError("%s: broken long-name chain (run chkdsk/fsck first)" % path)
            continue
        if e[0:1] == b"." or (attr & ATTR_VOLUME and not attr & ATTR_DIR):
            if lfns:
                raise FatError("%s: long name in front of a special entry" % path)
            fixed.append(e)
            continue
        if lfns:
            ok = (lfns[-1][0] & 0x1F) == 1 and all(l[13] == sfn_checksum(e[0:11]) for l in lfns)
            if not ok:
                raise FatError("%s: long name does not match the short name %r "
                               "(run chkdsk/fsck first)" % (path, sfn_text(e)))
        entries.append(Entry(lfns, e))
        lfns = []
    if lfns:
        raise FatError("%s: long name without a short name at the end" % path)
    return fixed, entries


def natural_key(s):
    return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", s) if t]


def sort_key(opts):
    def key(e):
        name = e.name if opts.case else e.name.casefold()
        group = 0
        if opts.order == "dirs":
            group = 0 if e.is_dir else 1
        elif opts.order == "files":
            group = 1 if e.is_dir else 0
        return (group, natural_key(name) if opts.natural else name)
    return key


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def sort_tree(fat, opts):
    changed = 0
    seen = set()
    todo = [("/", 0)]
    if opts.only:
        todo = [find_dir(fat, opts.only)]
    while todo:
        path, first = todo.pop(0)
        if first in seen:
            continue
        seen.add(first)
        regions = fat.dir_regions(first)
        data = fat.read_dir(regions)
        fixed, entries = parse_dir(data, path)
        ordered = sorted(entries, key=sort_key(opts))
        new = b"".join(fixed) + b"".join(e.raw for e in ordered)
        new += bytes(len(data) - len(new))
        if new != data:
            before = [e.name for e in entries]
            after = [e.name for e in ordered]
            if before != after:
                changed += 1
                if opts.write:
                    write_verified(fat, regions, new, path)
                if not opts.quiet:
                    print("%s: %d entries %s" % (path, len(after), "sorted" if opts.write else "would be sorted"))
                    if not opts.write:
                        for n in after[:12]:
                            print("      " + n)
                        if len(after) > 12:
                            print("      ... (%d more)" % (len(after) - 12))
            elif opts.write and data != new:
                write_verified(fat, regions, new, path)      # only deleted entries removed
        elif not opts.quiet and opts.verbose:
            print("%s: already sorted" % path)
        if not opts.only:
            for e in ordered:
                if e.is_dir and e.cluster >= 2:
                    todo.append((path.rstrip("/") + "/" + e.name, e.cluster))
    return changed


def write_verified(fat, regions, new, path):
    """Writes a directory and reads it back, so that a write that did not
    reach the card is reported instead of claimed as done."""
    fat.write_dir(regions, new)
    if fat.read_dir(regions) != new:
        raise FatError("%s: the directory read back differs from what was written" % path)


def find_dir(fat, path):
    first, cur = 0, "/"
    for part in [p for p in path.split("/") if p]:
        _, entries = parse_dir(fat.read_dir(fat.dir_regions(first)), cur)
        match = [e for e in entries if e.is_dir and e.name.casefold() == part.casefold()]
        if not match:
            raise FatError("directory %s not found" % path)
        first = match[0].cluster
        cur = cur.rstrip("/") + "/" + match[0].name
    return cur, first


def check_not_mounted(dev):
    """Linux and macOS: refuse to write to a mounted device. (Windows: the
    volume is locked and dismounted by Disk instead.)"""
    if WINDOWS or not dev.startswith("/dev/"):
        return
    real = os.path.realpath(dev)
    if os.path.exists("/proc/mounts"):
        with open("/proc/mounts") as f:
            sources = [line.split()[0] for line in f if line.strip()]
        hint = "sudo umount %s" % dev
    elif sys.platform == "darwin":
        try:
            out = subprocess.run(["mount"], capture_output=True, text=True).stdout
        except OSError:
            return
        sources = [line.split()[0] for line in out.splitlines() if line.strip()]
        real = real.replace("/dev/rdisk", "/dev/disk")      # raw and buffered node
        hint = "diskutil unmount %s" % real
    else:
        return
    for src in sources:
        if src.startswith("/dev/") and os.path.realpath(src) == real:
            raise FatError("%s is mounted -- unmount it first (%s)" % (dev, hint))


EPILOG = """\
examples:
  Linux    sudo umount /dev/sdb1                     unmount the card first
           sudo python3 sdsort.py /dev/sdb1          show what would change
           sudo python3 sdsort.py --write -n /dev/sdb1
  macOS    diskutil unmount /dev/disk4s1
           sudo python3 sdsort.py --write /dev/rdisk4s1
  Windows  (prompt "Run as administrator", close Explorer windows of the card)
           python sdsort.py E:
           python sdsort.py --write E:
  image    python3 sdsort.py --write card.img        also a whole card image with MBR

Only the 32-byte directory entries are rearranged; file contents, the FAT and
the clusters are left alone. Backing up the card first is still a good idea.
"""


def main():
    ap = argparse.ArgumentParser(
        description="Sort the directories of a FAT16/FAT32 SD card, so that Core4SD "
                    "(which lists entries in the order stored on the card) shows them sorted.\n"
                    "Default: directories first, then files, alphabetically, case-insensitive.",
        epilog=EPILOG, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("device", help=(
        "the card or an image of it:\n"
        "  Linux    the partition, e.g. /dev/sdb1 (unmount it first)\n"
        "  macOS    the raw partition, e.g. /dev/rdisk4s1 (diskutil unmount first)\n"
        "  Windows  the drive letter, e.g. E: (needs an administrator prompt);\n"
        "           the system drive (C:) is always refused\n"
        "  image    a file with a FAT partition or a whole card with MBR;\n"
        "           the first FAT partition is used"))
    ap.add_argument("--write", action="store_true", help=(
        "really write the sorted directories to the card.\n"
        "Without it nothing is changed: every unsorted directory is\n"
        "listed with its first 12 entries in the new order.\n"
        "A mounted card is refused on Linux/macOS; on Windows the drive\n"
        "is locked and dismounted while writing. Every written directory\n"
        "is read back to check that it arrived."))
    ap.add_argument("-n", "--natural", action="store_true", help=(
        "natural order: numbers in names are compared by their value,\n"
        "so DISK2 comes before DISK10 (default: DISK10 before DISK2,\n"
        "character by character)."))
    ap.add_argument("--files-first", dest="order", action="store_const", const="files", default="dirs", help=(
        "files before directories in each directory\n"
        "(default: directories first)."))
    ap.add_argument("--mixed", dest="order", action="store_const", const="mixed", help=(
        "directories and files mixed, sorted only by name."))
    ap.add_argument("--case", action="store_true", help=(
        "case-sensitive: upper case before lower case (Zork before\n"
        "apple). Default is case-insensitive (apple before Zork)."))
    ap.add_argument("--only", metavar="PATH", help=(
        "sort only this directory, e.g. --only /GAMES, without its\n"
        "subdirectories. The path is matched case-insensitively against\n"
        "the long names. Default: the whole card, all subdirectories."))
    ap.add_argument("-q", "--quiet", action="store_true", help=(
        "no output except errors (useful in scripts; the exit code is\n"
        "0 on success, 1 on an error)."))
    ap.add_argument("-v", "--verbose", action="store_true", help=(
        "also list the directories that are already sorted."))
    opts = ap.parse_args()
    opts.device = device_path(opts.device)
    try:
        check_not_system(opts.device)
        if opts.write:
            check_not_mounted(opts.device)
        disk = Disk(opts.device, opts.write)
        try:
            fat = Fat(disk)
            n = sort_tree(fat, opts)
        finally:
            disk.close()
    except (FatError, OSError) as e:
        print("Error:", e, file=sys.stderr)
        return 1
    if not opts.quiet:
        if n == 0:
            print("Already sorted.")
        elif not opts.write:
            print("\n%d directory(ies) unsorted. Use --write to sort." % n)
        else:
            print("\nDone: %d directory(ies) sorted." % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())