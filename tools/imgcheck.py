#!/usr/bin/env python3
"""
imgcheck.py -- tells whether a disk image mounts as a SmartPort drive.

Applies the checks of three programs to each image, without changing it:

    Core4SD    the firmware: image_kind() and analyse() in src/main.cpp
    POM2       a Liron card's bay: LironCard::mountBay(), Disk35Image::loadFile()
               and Block512Backing, plus classifyDiskForSlot() for its Drive Rack
    GSSquared  its SmartPort block card: identify_media(), probe_apm() and
               PDBlock3's media_reject_reason()

The programs look at the extension, the size and the 2IMG header. None of them
checks that the data is a usable volume; only POM2 warns about an 800K image
without a ProDOS directory in block 2. An "OK" therefore says the image mounts,
not that it boots. So the "Content" line looks into the image: a ProDOS volume
works; a DOS 3.3 disk and boot code that needs a Disk II do not; anything else
is marked "?". This is a search for known byte patterns, not a test run.

Usage:

    python3 imgcheck.py IMAGE...            check these files
    python3 imgcheck.py SDCARD/             check all images in a folder tree
    python3 imgcheck.py -b SDCARD/          list only images with an "NO" in them

The exit status is 0 if the Core4SD mounts every image, otherwise 1.
"""
import argparse
import os
import re
import struct
import sys

SIZE_140K = 143360
SIZE_400K = 409600
SIZE_800K = 819200
SIZE_1440K = 1474560
MIB = 1024 * 1024

IMAGE_EXTENSIONS = {".po", ".hdv", ".2mg", ".dsk", ".do", ".img", ".hda", ".iso",
                    ".nib", ".d13", ".woz", ".moof", ".image"}


class Image:
    """What the checks need to know about a file."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self.ext = os.path.splitext(self.name)[1].lower()
        self.size = os.path.getsize(path)
        self.read_only = not os.access(path, os.W_OK)
        with open(path, "rb") as f:
            self.head = f.read(64)

    def read_at(self, offset, length):
        with open(self.path, "rb") as f:
            f.seek(offset)
            return f.read(length)

    @property
    def has_2img_magic(self):
        return self.size >= 64 and self.head[:4] == b"2IMG"


class Result:
    def __init__(self, ok, text):
        self.ok = ok        # True, False, or None if it cannot be decided here
        self.text = text


def accept(text):
    return Result(True, text)


def refuse(text):
    return Result(False, text)


def blocks_text(blocks, extra=""):
    return "%d block%s%s" % (blocks, "" if blocks == 1 else "s", extra)


def twoimg_fields(head):
    return {
        "header": struct.unpack_from("<H", head, 8)[0],
        "format": struct.unpack_from("<I", head, 12)[0],
        "flags": struct.unpack_from("<I", head, 16)[0],
        "count": struct.unpack_from("<I", head, 20)[0],
        "offset": struct.unpack_from("<I", head, 24)[0],
        "length": struct.unpack_from("<I", head, 28)[0],
    }


# ---- Core4SD: image_kind() and analyse() in src/main.cpp --------------------

def check_core4sd(img):
    # The firmware reads the extension of the 8.3 name: three characters.
    ext = img.ext[:4]
    size = img.size
    if ext == ".2mg":
        if len(img.head) != 64 or img.head[:4] != b"2IMG":
            return refuse("no 2IMG header")
        h = twoimg_fields(img.head)
        fmt = h["format"] & 0xFFFF
        off, length = h["offset"], h["length"]
        if off > 0xFFFF or off > size:
            return refuse("2IMG data offset %d is out of range" % off)
        if not length or length > size - off:
            length = size - off
        if fmt == 0 and length == SIZE_140K:
            blocks, order = 280, "DOS order"
        elif fmt == 1:
            blocks, order = length >> 9, "ProDOS order"
        elif fmt == 0:
            return refuse("2IMG in DOS order is not 140K")
        else:
            return refuse("2IMG format %d (nibble) has no blocks" % fmt)
    elif ext in (".dsk", ".do") and size == SIZE_140K:
        blocks, order = 280, "DOS order"
    else:
        if size & 511:
            return refuse("size is not a multiple of 512")
        blocks, order = size >> 9, "ProDOS order"
    if not blocks:
        return refuse("no blocks")
    note = ", " + order
    if blocks > 0xFFFF:
        note += ", only 65535 blocks reachable"
        blocks = 0xFFFF
    if ext not in (".2mg", ".dsk", ".do", ".po", ".hdv"):
        note += ", listed as not an image (-)"
    return accept(blocks_text(blocks, note))


# ---- POM2: classifyDiskForSlot() in DiskImage.cpp ---------------------------

def twoimg_payload_length(img):
    """read2mgPayloadLength(): the data length, if the window fits the file."""
    if img.size < 32 or img.head[:4] != b"2IMG":
        return None
    h = twoimg_fields(img.head)
    off, length = h["offset"], h["length"]
    if off < 64 or length == 0 or off > img.size or off + length > img.size:
        return None
    return length


def woz_is_35(img):
    """The disk type in the INFO chunk of a WOZ file."""
    if img.head[:3] != b"WOZ":
        return False
    pos = 12
    while True:
        chunk = img.read_at(pos, 8)
        if len(chunk) < 8:
            return False
        length = struct.unpack_from("<I", chunk, 4)[0]
        if chunk[:4] == b"INFO":
            return img.read_at(pos + 8, 2)[1:2] == b"\x02"
        if length > 1 << 30:
            return False
        pos += 8 + length


def near(size, nominal):
    return size == nominal or nominal < size < nominal + 4096


def classify_for_slot(img):
    ext, sz = img.ext, img.size
    if ext == ".moof":
        return "3.5"
    if ext == ".woz":
        return "3.5" if woz_is_35(img) else "5.25"
    if ext == ".2mg":
        length = twoimg_payload_length(img)
        if length is not None:
            if length in (SIZE_140K, 232960, 223440):
                return "5.25"
            if length in (SIZE_800K, SIZE_400K, SIZE_1440K):
                return "3.5"
            if length >= 512 and length % 512 == 0:
                return "hard disk"
            return "unknown"
    if ext in (".dsk", ".do", ".nib", ".d13"):
        if ext == ".dsk" and sz in (SIZE_800K, SIZE_400K, SIZE_1440K):
            return "3.5"
        return "5.25"
    if ext == ".po" and sz in (SIZE_140K, SIZE_140K + 64):
        return "5.25"
    if ext == ".2mg" and sz < SIZE_400K:
        return "5.25"
    if ext in (".po", ".2mg", ".image") and (
            near(sz, SIZE_800K) or near(sz, SIZE_400K) or near(sz, SIZE_1440K)):
        return "3.5"
    if ext == ".hdv" and sz >= 512 and (sz % 512 == 0 or (sz > 64 and (sz - 64) % 512 == 0)):
        return "hard disk"
    if ext == ".2mg" and sz > SIZE_800K and (sz % 512 == 0 or (sz - 64) % 512 == 0):
        return "hard disk"
    return "unknown"


# ---- POM2: LironCard::mountBay() --------------------------------------------

def pom2_load_35(img):
    """Disk35Image::loadFile() on a card without a SuperDrive."""
    n = img.size
    if n == 0:
        return refuse("empty file")
    if n > 16 * MIB:
        return refuse("too large for a 3.5\" disk")
    if img.has_2img_magic:
        h = twoimg_fields(img.head)
        off, length = h["offset"], h["length"]
        if h["header"] < 52 or off < h["header"] or off > n or length == 0 or off + length > n:
            return refuse("malformed 2IMG header")
        if h["format"] != 1:
            return refuse("2IMG format %d on a 3.5\" disk" % h["format"])
        if length not in (SIZE_800K, SIZE_400K):
            return refuse("2IMG data is not 400K or 800K")
        return accept(blocks_text(length // 512, " on a 3.5\" drive"))
    if img.head[:8] == b"MOOF\xff\x0a\x0d\x0a" or img.head[:4] in (b"WOZ1", b"WOZ2"):
        return Result(None, "flux image: POM2 decodes it, not checked here")
    if n in (SIZE_800K, SIZE_400K):
        key = img.read_at(2 * 512, 5)
        looks_prodos = key[0] == 0 and key[1] == 0 and (key[4] & 0xF0) == 0xF0
        note = "" if looks_prodos else ", warns: no ProDOS directory in block 2"
        return accept(blocks_text(n // 512, " on a 3.5\" drive" + note))
    return refuse("not a 400K or 800K image")


def pom2_load_blocks(img):
    """Block512Backing::readImageFile() and adoptImage()."""
    n = img.size
    if n > 64 * MIB:
        return refuse("file is larger than 64 MiB")
    if n == 0:
        return refuse("empty file")
    length = n
    if img.has_2img_magic:
        h = twoimg_fields(img.head)
        off = h["offset"]
        length = h["length"]
        if h["format"] != 1:
            return refuse("2IMG format %d is not ProDOS order" % h["format"])
        if off < 64 or off < h["header"] or off > n or length == 0 or off + length > n:
            return refuse("invalid 2IMG data window")
    if length % 512:
        return refuse("data is not a whole number of 512-byte blocks")
    if length // 512 > 0x10000:
        return refuse("more than 65536 blocks")
    return accept(blocks_text(length // 512, " as a hard disk"))


def looks_like_35(img):
    if img.ext == ".hdv":
        return False
    if img.ext in (".woz", ".moof"):
        return True
    if img.ext == ".2mg":
        return classify_for_slot(img) == "3.5"
    return img.size <= MIB


def check_pom2(img):
    result = None
    if looks_like_35(img):
        result = pom2_load_35(img)
        # Small but no 3.5" disk: a block device takes any whole number of blocks.
        if result.ok is False and img.ext not in (".moof", ".woz"):
            result = None
    if result is None:
        result = pom2_load_blocks(img)
    slot_class = classify_for_slot(img)
    if result.ok is not False and slot_class in ("5.25", "unknown"):
        result.text += "; its Drive Rack refuses it (%s)" % slot_class
    return result


# ---- GSSquared: identify_media() in util/media.cpp --------------------------

MEDIA_BLOCK, MEDIA_FLOPPY, MEDIA_OTHER = range(3)


def apm_partitions(img, base, data_size):
    """read_apm() in util/apm.cpp: the mountable partitions, None if no map."""
    data_end = img.size
    if data_size > 0:
        data_end = min(data_end, base + data_size)
    ddm = img.read_at(base, 512)
    if len(ddm) != 512 or ddm[:2] != b"ER":
        return None
    block_size = struct.unpack_from(">H", ddm, 2)[0]
    if block_size not in (512, 2048):
        return None
    first, stride = base + block_size, block_size
    if img.read_at(first, 2) != b"PM":
        if block_size == 512 and img.read_at(base + 2048, 2) == b"PM":
            first, stride = base + 2048, 2048
        else:
            return None
    pm = img.read_at(first, 512)
    if len(pm) != 512:
        return None
    entries = struct.unpack_from(">I", pm, 4)[0]
    if entries == 0 or entries > 128:
        return None
    seen, mountable = 0, 0
    for i in range(entries):
        pm = img.read_at(first + i * stride, 512)
        if len(pm) != 512 or pm[:2] != b"PM":
            break
        seen += 1
        start, count = struct.unpack_from(">II", pm, 8)
        kind = pm[48:80].split(b"\0")[0]
        offset, length = base + start * block_size, count * block_size
        if offset >= data_end:
            length = 0
        elif offset + length > data_end:
            length = data_end - offset
        if kind in (b"Apple_PRODOS", b"Apple_ProDOS", b"Apple_HFS") and length >= 512:
            mountable += 1
    return mountable if seen else None


def gs2_identify(img):
    """(kind, block size, data size, data offset), or a Result that refuses it."""
    ext, size = img.ext, img.size
    if ext == ".2mg":
        if len(img.head) != 64 or img.head[:4] != b"2IMG":
            return refuse("no 2IMG header")
        h = twoimg_fields(img.head)
        off = h["offset"] or h["header"]
        if off >= size:
            return refuse("2IMG data offset past the end of the file")
        avail = size - off
        data = h["length"] or h["count"] * 512
        if data == 0 or data > avail:
            data = avail
        if h["format"] == 0:
            if avail < SIZE_140K:
                return refuse("2IMG in DOS order is short")
            return MEDIA_FLOPPY, 256, SIZE_140K, off
        if h["format"] == 1:
            if data == SIZE_140K:
                return MEDIA_FLOPPY, 256, data, off
            data -= data % 512
            if not data:
                return refuse("2IMG has no data")
            return MEDIA_BLOCK, 512, data, off
        if h["format"] == 2:
            if avail < 35 * 6656:
                return refuse("2IMG nibble image is short")
            return MEDIA_OTHER, 256, 140 * 1024, off
        return refuse("unknown 2IMG format %d" % h["format"])
    if ext in (".hdv", ".img", ".hda", ".iso"):
        return MEDIA_BLOCK, 512, size, 0
    if ext in (".do", ".dsk"):
        if size != SIZE_140K:
            return refuse("%s must be exactly 140K" % ext)
        return MEDIA_FLOPPY, 256, size, 0
    if ext == ".po":
        if size == SIZE_140K:
            return MEDIA_FLOPPY, 256, size, 0
        return MEDIA_BLOCK, 512, size, 0
    if ext in (".nib", ".woz"):
        return MEDIA_OTHER, 256, size, 0
    return refuse("unknown extension")


def check_gssquared(img):
    identified = gs2_identify(img)
    if isinstance(identified, Result):
        return identified
    kind, block_size, data, offset = identified
    if kind == MEDIA_BLOCK:
        partitions = apm_partitions(img, offset, data)
        if partitions == 0:
            return refuse("partition map without ProDOS or HFS partitions")
        if partitions:
            return accept("partition map, %d volume(s)" % partitions)
    # PDBlock3::media_reject_reason()
    if kind != MEDIA_BLOCK or block_size != 512:
        return refuse("not a 512-byte block image: needs a 5.25\" drive"
                      if kind == MEDIA_FLOPPY else "not a 512-byte block image")
    if data == SIZE_140K:
        return refuse("140K images must be mounted on a 5.25\" drive")
    note = ", read-only (CD-ROM)" if img.ext == ".iso" else ""
    if data % 512:
        note += ", %d bytes at the end ignored" % (data % 512)
    return accept(blocks_text(data // 512, note))


# ---- What the image holds ---------------------------------------------------

# Where a ProDOS-order file keeps each DOS 3.3 sector of a track.
DOS_TO_PRODOS_SECTOR = [0, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 15]


def data_of(img):
    """The disk data: without a 2IMG header, and no more than a directory needs."""
    off = 0
    if img.ext == ".2mg" and img.has_2img_magic:
        off = twoimg_fields(img.head)["offset"]
    return img.read_at(off, 256 * 1024), img.size - off


def prodos_volume(block):
    """The volume name, if this is the key block of a ProDOS volume directory."""
    if len(block) < 512 or block[0] or block[1] or block[4] & 0xF0 != 0xF0:
        return None
    if block[0x23] != 0x27 or block[0x24] != 0x0D:
        return None
    name = block[5:5 + (block[4] & 15)]
    if not name or not 65 <= name[0] <= 90:
        return None
    if any(not (c == 46 or 48 <= c <= 57 or 65 <= c <= 90) for c in name):
        return None
    return name.decode("ascii")


def pascal_volume(block):
    """The volume name, if this is the start of an Apple Pascal directory."""
    if len(block) < 14 or block[:6] != b"\0\0\6\0\0\0":
        return None
    name = block[7:7 + block[6]]
    if not 1 <= block[6] <= 7 or any(c < 32 or c > 126 for c in name):
        return None
    return name.decode("ascii")


def dos33_order(data):
    """The sector order of a DOS 3.3 disk, or None if there is no VTOC."""
    vtoc = data[17 * 4096:17 * 4096 + 256]
    if len(vtoc) < 256 or vtoc[0x34] != 35 or vtoc[0x35] != 16 or vtoc[0x36:0x38] != b"\0\1":
        return None

    def links(prodos_order):
        # Follows the catalog: only the right order gives an unbroken chain.
        track, sector, count = vtoc[1], vtoc[2], 0
        while count < 15 and track < 35 and sector < 16 and track:
            pos = DOS_TO_PRODOS_SECTOR[sector] if prodos_order else sector
            entry = data[(track * 16 + pos) * 256:(track * 16 + pos + 1) * 256]
            if len(entry) < 256 or entry[1] != track and entry[1]:
                break
            if entry[1] and entry[2] != sector - 1:
                break
            track, sector, count = entry[1], entry[2], count + 1
        return count

    return "ProDOS" if links(True) > links(False) else "DOS"


def order_warning(img, order):
    """Core4SD reads the order of a 140K file from its extension."""
    if img.size != SIZE_140K:
        return ""
    if order == "DOS" and img.ext in (".po", ".hdv"):
        return "; in DOS order, but %s is read in ProDOS order: rename it to .dsk" % img.ext
    if order == "ProDOS" and img.ext in (".dsk", ".do"):
        return "; in ProDOS order, but %s is read in DOS order: rename it to .po" % img.ext
    return ""


def disk_ii_boot_code(data):
    """Why this boot code needs a Disk II, or None. A SmartPort drive only loads block 0."""
    if b"\x5C\x85\x3E" in data[:256] or b"\x6C\x3E\x00" in data[:256]:
        return "boot code calls the read routine in the Disk II ROM ($Cn5C)"
    # JMP $Cn5C with a fixed slot, as Apple Pascal does (slot 6 only).
    jump = re.search(rb"\x4C\x5C([\xC1-\xC7])", data[:256])
    if jump:
        return "boot code jumps to the Disk II ROM of slot %d ($C%d5C)" % (
            jump.group(1)[0] & 7, jump.group(1)[0] & 7)
    # LDA $C08C,X reads the disk; a lone LDA $C088,X only stops the motor.
    if b"\xBD\x8C\xC0" in data[:512]:
        return "boot code reads the Disk II registers itself"
    return None


def check_content(img):
    if img.head[:3] == b"WOZ" or img.head[:4] == b"MOOF" or img.ext == ".nib":
        return refuse("flux or nibble data, no blocks")
    data, size = data_of(img)
    if len(data) < 2048:
        reason = disk_ii_boot_code(data)
        if reason:
            return refuse(reason)
        return Result(None, "too small for a volume: only a boot block could run")
    if not any(data[:512]) and not any(data[1024:1536]):
        return Result(None, "blank at the start: no volume found")

    # A file of 140K may hold either order; block 2 is at another place in each.
    in_dos_order = data[11 * 256:12 * 256] + data[10 * 256:11 * 256]
    for block, order in ((data[1024:1536], "ProDOS"),
                         (in_dos_order if size == SIZE_140K else b"", "DOS")):
        volume = prodos_volume(block)
        if volume:
            boot = "" if any(data[:256]) else ", no boot block"
            return accept("ProDOS volume /%s%s%s" % (volume, boot, order_warning(img, order)))

    order = dos33_order(data) if size == SIZE_140K else None
    if order:
        return refuse("DOS 3.3 disk: it needs a Disk II and does not run on a SmartPort drive"
                      + order_warning(img, order))
    volume = pascal_volume(data[1024:1536]) or pascal_volume(in_dos_order)
    if volume:
        reason = disk_ii_boot_code(data)
        if reason:
            return refuse("Apple Pascal volume %s: %s" % (volume, reason))
        return Result(None, "Apple Pascal volume %s: ProDOS cannot read it" % volume)
    reason = disk_ii_boot_code(data)
    if reason:
        return refuse(reason)
    return Result(None, "no known file system; it runs only if its boot code does not need a Disk II")


# ---- The report -------------------------------------------------------------

CHECKS = (("Core4SD", check_core4sd), ("POM2", check_pom2), ("GSSquared", check_gssquared),
          ("Content", check_content))
VERDICT = {True: "OK", False: "NO", None: "?"}


def find_images(paths):
    for path in paths:
        if os.path.isdir(path):
            for folder, dirs, files in os.walk(path):
                dirs.sort()
                for name in sorted(files):
                    if os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS:
                        yield os.path.join(folder, name)
        else:
            yield path


def size_text(size):
    if size >= MIB:
        return "%.1f MB" % (size / MIB)
    return "%d KB" % (size // 1024)


def main():
    doc = __doc__.strip().split("\n\n")
    ap = argparse.ArgumentParser(
        description=doc[0].split(" -- ")[1], epilog="\n\n".join(doc[1:3]),
        formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("paths", nargs="+", metavar="PATH", help="an image, or a folder to search")
    ap.add_argument("-b", "--bad", action="store_true",
                    help="list only images with an \"NO\" in them")
    opts = ap.parse_args()

    status = 0
    for path in find_images(opts.paths):
        try:
            img = Image(path)
        except OSError as e:
            print("%s: %s" % (path, e), file=sys.stderr)
            status = 1
            continue
        results = [(name, check(img)) for name, check in CHECKS]
        if results[0][1].ok is False:
            status = 1
        if opts.bad and all(r.ok is not False for _, r in results):
            continue
        print("%s  (%s, %d bytes%s)" % (path, size_text(img.size), img.size,
                                        ", read-only" if img.read_only else ""))
        for name, r in results:
            print("  %-10s %-3s %s" % (name, VERDICT[r.ok], r.text))
    return status


if __name__ == "__main__":
    sys.exit(main())
