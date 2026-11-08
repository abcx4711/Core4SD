#!/usr/bin/env python3
"""
End-to-end test: the real 6502 browser (CORE4SD.SYSTEM + boot block) runs in
py65, its SmartPort calls go through an independent Python model of the
Apple's SmartPort firmware (packet encoding/decoding written from the
protocol, not from the firmware), and land in the real firmware code
compiled for the host (libspiisd.so) on a test SD card (a directory).

    python3 sim_e2e.py            (after `make -C test`)
"""
import ctypes, os, re, shutil, struct, sys, tempfile

from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
lib = ctypes.CDLL(os.path.join(HERE, "libspiisd.so"))
lib.sim_eeprom.restype = ctypes.POINTER(ctypes.c_uint8)

FAILS = []


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print("  FAIL:", what)
    else:
        print("  ok:  ", what)


# ---------------------------------------------------------------------------
# SmartPort packets, host side (the Apple ROM's job)
# ---------------------------------------------------------------------------

def encode_rx(dest, ptype, payload):
    """A packet as the device's ReceivePacket stores it: C3 at [5], DEST =
    $80 | unit (every byte on the bus has bit 7 set)."""
    odd, grps = len(payload) % 7, len(payload) // 7
    b = [0xFF, 0x3F, 0xCF, 0xF3, 0xFC, 0xC3, 0x80 | dest, 0x80, ptype, 0x80, 0x80, 0x80 | odd, 0x80 | grps]
    chk = 0
    for x in b[6:13]:
        chk ^= x
    for x in payload:
        chk ^= x

    def msb(bs):
        m = 0x80
        for i, x in enumerate(bs):
            if x & 0x80:
                m |= 0x40 >> i
        return m

    if odd:
        b.append(msb(payload[:odd]))
        b += [x | 0x80 for x in payload[:odd]]
    for g in range(grps):
        grp = payload[odd + 7 * g: odd + 7 * g + 7]
        b.append(msb(grp))
        b += [x | 0x80 for x in grp]
    b += [chk | 0xAA, (chk >> 1) | 0xAA, 0xC8, 0x00]
    return bytes(b)


def decode_tx(tx):
    """A packet as the device sends it: sync [0..5], C3 at [6]."""
    assert tx[:7] == bytes([0xFF, 0x3F, 0xCF, 0xF3, 0xFC, 0xFF, 0xC3]), tx[:8].hex()
    dest, src, ptype, aux, stat, oddc, grpc = tx[7:14]
    odd, grps = oddc & 0x7F, grpc & 0x7F
    i = 14
    data = []
    if odd:
        m = tx[i]; i += 1
        for k in range(odd):
            data.append((tx[i] & 0x7F) | (0x80 if m & (0x40 >> k) else 0)); i += 1
    for g in range(grps):
        m = tx[i]; i += 1
        for k in range(7):
            data.append((tx[i] & 0x7F) | (0x80 if m & (0x40 >> k) else 0)); i += 1
    chk = 0
    for x in tx[7:14]:
        chk ^= x
    for x in data:
        chk ^= x
    got = (tx[i] & 0x55) | ((tx[i + 1] & 0x55) << 1)
    assert got == chk, "checksum"
    assert tx[i + 2] == 0xC8
    assert dest == 0x80
    return src, ptype, stat, bytes(data)


class Bus:
    """The SmartPort bus with the Core4SD on it."""

    def __init__(self):
        self.tx = (ctypes.c_uint8 * 700)()
        self.log = []

    def packet(self, dest, cmd, params, data=None):
        payload = bytes([cmd, 3]) + bytes(params) + bytes(7 - len(params))
        rx = encode_rx(dest, 0x80, payload)
        drx = encode_rx(dest, 0x82, data) if data is not None else None
        n = lib.sim_packet(rx, len(rx), drx, len(drx) if drx else 0, self.tx)
        assert n > 0, "no reply"
        src, ptype, stat, reply = decode_tx(bytes(self.tx[:n + 1]))
        assert src == 0x80 | dest
        return stat, reply

    def reset(self):
        """Bus reset + INIT: ids 1.. until a device says it is the last."""
        self.units = 0
        lib.sim_bus_reset()
        for dest in range(1, 16):
            stat, _ = self.packet(dest, 0x05, [])
            self.units = dest
            if stat == 0xFF:
                break
        return self.units

    def status(self, unit, code):
        stat, data = self.packet(unit, 0x00, [0, 0, code])
        return stat & 0x7F, data

    def control(self, unit, code, payload):
        stat, data = self.packet(unit, 0x04, [0, 0, code], payload)
        self.log.append(("control", unit, code, bytes(payload), stat & 0x7F))
        return stat & 0x7F

    def read(self, unit, blk):
        stat, data = self.packet(unit, 0x01, [0, 0, blk & 0xFF, (blk >> 8) & 0xFF, blk >> 16])
        return stat & 0x7F, data

    def write(self, unit, blk, data):
        stat, _ = self.packet(unit, 0x02, [0, 0, blk & 0xFF, (blk >> 8) & 0xFF, blk >> 16], data)
        return stat & 0x7F


# ---------------------------------------------------------------------------
# The Apple II
# ---------------------------------------------------------------------------

SLOT = 5
ROMBASE = 0xC000 + SLOT * 0x100
DRIVER = ROMBASE + 0x0A          # ProDOS block driver entry ($CnFF = $0A)
DISPATCH = DRIVER + 3            # SmartPort dispatcher


class Apple:
    RESET = 0xFA62                                   # where the reset vector points

    def __init__(self, bus, plus=False, iicplus=False):
        self.bus = bus
        self.mem = ObservableMemory()
        self.mem[0xFBB3] = 0xEA if plus else 0x06     # monitor ROM id: II+ or IIe/IIc/IIgs
        if not plus and not iicplus:
            # enhanced IIe: $FBC0 = $E0, and $FE1F is an RTS (a IIgs clears
            # the carry there)
            self.mem[0xFBC0] = 0xE0
            self.mem[0xFE1F] = 0x60
        self.mem[0xFFFC], self.mem[0xFFFD] = self.RESET & 0xFF, self.RESET >> 8
        self.keys = []
        self.idle = 0
        self.mem.subscribe_to_read([0xC000], self.kbd)
        self.mem.subscribe_to_read([0xC010], lambda a: 0)
        for a in range(0xC100, 0xC800):
            self.mem[a] = 0xFF
        self.dispatch = DISPATCH
        if iicplus:
            # IIc Plus: slot 5 carries the SmartPort id bytes as on the IIc;
            # its ROM also has the "hidden" entries $C64E (SEC) and $C651
            # (CLC) in slot 6, which the A2let must not need (not trapped)
            self.mem[0xFBBF] = 0x05
            self.mem[0xC64E], self.mem[0xC651] = 0x38, 0x18
        rom = {0x01: 0x20, 0x03: 0x00, 0x05: 0x03, 0x07: 0x00, 0xFF: 0x0A}
        for off, v in rom.items():
            self.mem[ROMBASE + off] = v
        self.mpu = MPU(memory=self.mem)
        self.stopped = None
        self.boot_reads = []

    def kbd(self, addr):
        if self.keys:
            return self.keys[0] | 0x80
        self.idle += 1
        return 0

    def _strobe(self):
        if self.keys:
            self.keys.pop(0)

    def press(self, *keys):
        for k in keys:
            if isinstance(k, str):
                for ch in k:
                    self.keys.append(ord(ch))
            else:
                self.keys.append(k & 0x7F)

    # --- traps ---------------------------------------------------------------
    def pull_return(self):
        sp = self.mpu.sp
        lo = self.mem[0x100 + ((sp + 1) & 0xFF)]
        hi = self.mem[0x100 + ((sp + 2) & 0xFF)]
        self.mpu.sp = (sp + 2) & 0xFF
        return (hi << 8) | lo

    def finish(self, err):
        self.mpu.a = err
        if err:
            self.mpu.p |= self.mpu.CARRY
        else:
            self.mpu.p &= ~self.mpu.CARRY

    def smartport(self):
        ret = self.pull_return()
        cmd = self.mem[ret + 1]
        parm = self.mem[ret + 2] | (self.mem[ret + 3] << 8)
        self.mpu.pc = ret + 4
        unit = self.mem[parm + 1]
        buf = self.mem[parm + 2] | (self.mem[parm + 3] << 8)
        code = self.mem[parm + 4]
        if cmd == 0x00 and unit == 0:            # the ROM answers unit 0 itself
            for i, v in enumerate([self.bus.units, 0, 0, 0, 0, 0, 0, 0]):
                self.mem[buf + i] = v
            return self.finish(0)
        if unit < 1 or unit > self.bus.units:
            return self.finish(0x28)
        if cmd == 0x00:
            err, data = self.bus.status(unit, code)
            for i, v in enumerate(data):
                self.mem[buf + i] = v
            self.mpu.x, self.mpu.y = len(data) & 0xFF, len(data) >> 8
            return self.finish(err)
        if cmd == 0x04:
            n = self.mem[buf] | (self.mem[buf + 1] << 8)
            payload = [self.mem[buf + 2 + i] for i in range(n)]
            return self.finish(self.bus.control(unit, code, payload))
        raise RuntimeError("unexpected SmartPort command %02X" % cmd)

    def driver(self):
        ret = self.pull_return()
        self.mpu.pc = ret + 1
        cmd, unit = self.mem[0x42], self.mem[0x43]
        buf = self.mem[0x44] | (self.mem[0x45] << 8)
        blk = self.mem[0x46] | (self.mem[0x47] << 8)
        assert cmd == 1, "driver: only READ expected"
        assert (unit >> 4) & 7 == SLOT
        self.boot_reads.append(blk)
        err, data = self.bus.read(1 + (unit >> 7), blk)
        if not err:
            for i in range(512):
                self.mem[buf + i] = data[i]
        return self.finish(err)

    def run(self, max_steps=3_000_000):
        """Runs until the program waits for a key that is not there."""
        self.idle = 0
        for _ in range(max_steps):
            pc = self.mpu.pc
            if pc == self.dispatch:
                self.smartport()
                continue
            if pc == DRIVER:
                self.driver()
                continue
            if pc == ROMBASE or pc == 0xBF00 or pc == self.RESET:
                self.stopped = pc
                return pc
            if self.mem[pc] == 0x8D and self.mem[pc + 1] == 0x10 and self.mem[pc + 2] == 0xC0:
                self._strobe()             # STA $C010
            if self.idle > 50:
                return None
            self.mpu.step()
        raise RuntimeError("runaway at %04X" % self.mpu.pc)

    # --- screen --------------------------------------------------------------
    def line(self, row):
        base = [0x400, 0x480, 0x500, 0x580, 0x600, 0x680, 0x700, 0x780][row % 8] + 0x28 * (row // 8)
        s, inv = "", False
        for c in (self.mem[base + i] for i in range(40)):
            if c >= 0x80:
                s += chr(c & 0x7F)
            else:
                inv = True
                if c < 0x20:
                    s += chr(c + 0x40)
                elif c < 0x40 or c >= 0x60:
                    s += chr(c)
                else:
                    s += "?"
        return s, inv

    def screen(self):
        return [self.line(r)[0] for r in range(24)]

    def show(self, title):
        print("   +" + "-" * 40 + "+  " + title)
        for r in range(24):
            txt, inv = self.line(r)
            print("   |" + txt + "|" + (" <" if inv and 3 <= r < 18 else ""))
        print("   +" + "-" * 40 + "+")

    def selected(self):
        for r in range(2, 17):
            txt, inv = self.line(r)
            if inv:
                return txt.strip()
        return None


# ---------------------------------------------------------------------------
# Test SD card
# ---------------------------------------------------------------------------

def prodos_image(path, blocks, volname):
    """A disk image whose blocks each start with their block number."""
    with open(path, "wb") as f:
        for b in range(blocks):
            blk = bytearray(512)
            blk[0:2] = struct.pack("<H", b)
            blk[2:2 + len(volname)] = volname.encode()
            f.write(blk)


def make_card(root):
    os.makedirs(os.path.join(root, "GAMES"))
    os.makedirs(os.path.join(root, "UTILS"))
    os.makedirs(os.path.join(root, "BIG", "INNER"))
    LONG88 = "Apple Diskware - ProDOS Assembler Tools (680-0194-A)(Apple Computer, Inc.)(1983) [!].2mg"
    open(os.path.join(root, "BIG", "INNER", LONG88), "wb").write(bytes(1024))
    prodos_image(os.path.join(root, "PART1.PO"), 280, "PART1")
    prodos_image(os.path.join(root, "GAMES", "Total Replay v5.po"), 65535, "TOTALREPLAY")
    prodos_image(os.path.join(root, "GAMES", "ZORK.PO"), 280, "ZORK")
    prodos_image(os.path.join(root, "GAMES", "a very long file name for a game disk.hdv"), 1600, "LONG")
    prodos_image(os.path.join(root, "GAMES", "._ZORK.PO"), 1, "MAC")
    prodos_image(os.path.join(root, "GAMES", "_hidden.po"), 280, "HIDDEN")
    prodos_image(os.path.join(root, "GAMES", "locked_ro.po"), 280, "LOCKED")
    # .2MG with a 64-byte header, ProDOS order
    body = bytearray()
    for b in range(280):
        blk = bytearray(512); blk[0:2] = struct.pack("<H", b); blk[2:6] = b"2MGI"; body += blk
    hdr = bytearray(64)
    hdr[0:4] = b"2IMG"; hdr[4:8] = b"XGS!"; hdr[8:10] = struct.pack("<H", 64); hdr[10:12] = struct.pack("<H", 1)
    hdr[12:16] = struct.pack("<I", 1); hdr[16:20] = struct.pack("<I", 0)
    hdr[20:24] = struct.pack("<I", 280); hdr[24:28] = struct.pack("<I", 64); hdr[28:32] = struct.pack("<I", len(body))
    open(os.path.join(root, "UTILS", "Copy II Plus.2mg"), "wb").write(hdr + body)
    # .DSK in DOS order: each 256-byte DOS sector starts with (track, sector)
    dsk = bytearray()
    for t in range(35):
        for s in range(16):
            sec = bytearray(256); sec[0] = t; sec[1] = s; dsk += sec
    open(os.path.join(root, "UTILS", "DOS ORDER.DSK"), "wb").write(dsk)
    open(os.path.join(root, "README.DOC"), "w").write("hello\n")     # not an image: listed with '-'
    for hidden in ("notes.txt", "backup.bak", "Archiv.zip", os.path.join("GAMES", "old.po.bak")):
        open(os.path.join(root, hidden), "wb").write(bytes(1024))  # HIDE_EXTENSIONS: not listed
    shutil.copy(os.path.join(ROOT, "apple", "CORE4SD.PO"), os.path.join(root, "UTILS", "MENUDISK.PO"))  # sorts after DOS ORDER.DSK
    prodos_image(os.path.join(root, "UTILS", "Z" + "x" * 95 + ".po"), 280, "HUGE")
    for i in range(40):
        prodos_image(os.path.join(root, "BIG", "disk%02d.po" % i), 1, "D%02d" % i)


# ---------------------------------------------------------------------------

def main():
    tmp = tempfile.mkdtemp(prefix="spiisd-sd-")
    try:
        make_card(tmp)
        run(tmp)
    finally:
        lib.sim_close()
        shutil.rmtree(tmp)
    print()
    if FAILS:
        print("%d FAILURES" % len(FAILS))
        sys.exit(1)
    print("end-to-end: all checks passed")


def boot(bus, apple):
    """What the Apple's ROM does on PR#5: reset the bus, read block 0 of
    unit 1 to $0800, jump to $0801 with X = $50."""
    bus.reset()
    err, blk0 = bus.read(1, 0)
    assert not err
    for i in range(512):
        apple.mem[0x800 + i] = blk0[i]
    apple.mpu.pc = 0x801
    apple.mpu.x = SLOT * 16
    apple.mpu.sp = 0xFF
    return apple.run()


def run(card):
    lib.sim_eeprom_clear()
    print("== 1a. power-up with an empty EEPROM: all slots empty, no /PARTn.PO")
    lib.sim_setup(card.encode(), 0, 1)
    bus = Bus()
    check(bus.reset() == 4, "four units answer INIT")
    err, st = bus.status(1, 0)
    check(st[0] == 0xB4, "unit 1 serves the boot menu")
    err, dib = bus.status(1, 3)
    check(dib[4] == 14 and dib[5:19] == b"CORE4SD MENU  ", "boot menu DIB id = 'CORE4SD MENU'")
    err, dib = bus.status(3, 3)
    check(dib[5:19] == b"CORE4SD DRIVE3", "unit 3 DIB id = 'CORE4SD DRIVE3'")
    for u in (2, 3, 4):
        err, st = bus.status(u, 0)
        check(st[0] == 0xE8, "unit %d empty" % u)

    print("== 1. slot 1 = /PART1.PO stored in EEPROM")
    ee = lib.sim_eeprom()
    for i, c in enumerate(b"/PART1.PO\0"):
        ee[16 + i] = c
    lib.sim_setup(card.encode(), 0, 1)
    check(bus.reset() == 4, "four units answer INIT")
    err, dib = bus.status(1, 3)
    check(dib[4] == 14 and dib[5:19] == b"CORE4SD DRIVE1", "unit 1 DIB id = 'CORE4SD DRIVE1'")
    err, st = bus.status(1, 0)
    check(st[0] == 0xF8 and st[1] | (st[2] << 8) == 280, "unit 1 online, 280 blocks")
    err, st = bus.status(2, 0)
    check(st[0] == 0xE8 and st[1] == 0 and st[2] == 0, "unit 2 without image: offline, 0 blocks")
    err, blk = bus.read(1, 5)
    check(not err and blk[0] == 5 and blk[2:7] == b"PART1", "unit 1 block 5 comes from PART1.PO")
    err, _ = bus.read(2, 0)
    check(err == 0x2F, "reading an empty slot gives $2F (offline)")
    err, _ = bus.read(1, 280)
    check(err == 0x2D, "block past the end gives $2D")

    print("== 2. button held at power-up: boot menu, browser boots from unit 1")
    lib.sim_setup(card.encode(), 1, 1)
    lib.sim_set_button(0)
    apple = Apple(bus)
    stop = boot(bus, apple)
    apple.show("after boot")
    scr = apple.screen()
    check(stop is None, "browser runs and waits for a key")
    check(re.fullmatch(r" Core4SD \d+\.\d+ - Slot 5, Units 1-4", scr[0].rstrip()), "title line (no model), firmware version, SmartPort slot 5, units 1-4: %r" % scr[0])
    check(scr[1].startswith("SD:/"), "path is the root")
    check(scr[18] == "Drives " + "-" * 33 and scr[2][2:30].strip() == "BIG/", "no key line above the list; 'Drives ----' to the right edge above the drive lines")
    names = [l[2:30].strip() for l in scr[2:17]]
    check(names[:6] == ["BIG/", "GAMES/", "PART1.PO", "README.DOC", "UTILS/", ""],
          "root listing without .txt/.bak/.zip files: %s" % names[:6])
    check(scr[2].rstrip().endswith("<DIR>") and len(scr[2].rstrip()) == 39, "directories are marked <DIR>, ending in column 38")
    check(scr[4][30:39].strip() == "140K" and scr[4][38] == "K" and scr[19][38] == "K", "PART1.PO shows 140K, in the same column as the drive lines")
    check(scr[5].startswith("- README.DOC"), "a non-image file is marked with '-'")
    check(scr[19].startswith("1:PART1.PO"), "drive 1 shows its image (no [BOOT] any more)")
    check([apple.mem[b] for b in (0x5D0, 0x650, 0x6D0, 0x750)] == [0xB1, 0xB2, 0xB3, 0xB4] and apple.mem[0x5D1] == 0xBA,
          "drive numbers 1-4 and the ':' in normal video")
    check(scr[20].startswith("2:- empty -"), "slot 2 empty")
    check(apple.selected().startswith("BIG/"), "first entry selected")
    check(scr[23].rstrip() == "1-4 Mount Eject Rotate Boot 1".ljust(36) + "Help", "key line: %r" % scr[23])
    inverse = [c for c in range(40) if apple.mem[0x7D0 + c] < 0x80]
    check(inverse == [0, 1, 2, 10, 16, 23, 36], "key line: only 1-4, E, R, B and H in inverse (columns %s)" % inverse)

    print("== 2b. help window: ? opens it, any key restores the screen")
    before = bytes(apple.mem[0x400:0x800])
    before_scr = apple.screen()
    apple.press(ord("?"))
    apple.run()
    scr = apple.screen()
    apple.show("help window")
    check(scr[2][3:37].rstrip() == " " * 15 + "Help" and apple.mem[0x503] < 0x80,
          "window title Help centred in the inverse top edge, row 2 from column 3: %r" % scr[2][3:37])
    check(scr[3][4:].startswith("      Core4SD 1.0 on A2ee"), "first line: firmware version (from the DIB) and model: %r" % scr[3][4:])
    check(all(apple.mem[[0x400, 0x480, 0x500, 0x580, 0x600, 0x680, 0x700, 0x780][r % 8] + 0x28 * (r // 8) + c] == 0x20
              for r in range(3, 21) for c in (3, 36)), "inverse left and right edge, rows 3-20")
    check(scr[4][4:36].strip() == "" and scr[5][4:].startswith(" Up Dn") and scr[16][4:].startswith(" Tab           Next Core4SD")
          and scr[17][4:].startswith(" ?  H"), "a blank line after the version, then the key lines (Tab too)")
    check(scr[18][4:].startswith(" In the list:") and scr[19][4:].startswith("   - = not a valid disk image")
          and scr[20][4:].startswith("   * = write protected"), "the help explains - and * as a legend, not as keys")
    check(scr[21][3:37].startswith(" Any key: back"), "bottom edge in row 21: ANY KEY")
    check(scr[:2] == before_scr[:2] and scr[22:] == before_scr[22:], "rows outside the window untouched")
    apple.press(ord(" "))
    apple.run()
    check(bytes(apple.mem[0x400:0x800]) == before, "any key: the screen is restored byte for byte")
    check(apple.selected().startswith("BIG/"), "selection unchanged after the help")
    apple.press(ord("H"))
    apple.run()
    check(apple.screen()[2][3:37].strip() == "Help", "H opens the help too")
    apple.press(0x1B)                       # ESC closes it, and does nothing else
    apple.run()
    check(bytes(apple.mem[0x400:0x800]) == before and apple.screen()[1].rstrip() == "SD:/",
          "ESC only closes the help (still in the root)")

    print("== 3. into GAMES, mount the long-named image into slot 2")
    apple.press(0x0A, 0x0D)                 # down, RETURN
    apple.run()
    apple.show("GAMES")
    scr = apple.screen()
    check(scr[1].startswith("SD:/GAMES/"), "path SD:/GAMES/")
    names = [l[2:30].rstrip() for l in scr[2:5]]
    check(names[0] == "Total Replay v5.po" and names[1] == "ZORK.PO", "GAMES sorted as on the card, ._ and hidden skipped: %s" % names)
    check(scr[5][2:30].rstrip() == "locked_ro.po" and scr[5][38:40] == "K*" and scr[3][39] == " ",
          "read-only file marked with * in the list, others not: %r" % scr[5])
    check(names[2] == "a very long file name f..hdv", "long name: first 23 characters, '.', last 4 (%r)" % names[2])
    check(scr[2][30:39].strip() == "32768K", "32 MB image shows 32768K")
    check(len([l for l in scr[2:17] if l.strip()]) == 4, "4 visible entries")
    apple.press(0x0A, 0x0A, "2")            # down, down, 2
    apple.run()
    apple.show("mounted in slot 2")
    scr = apple.screen()
    check("Mounted as drive 2" in scr[17], "status line reports the mount")
    check(scr[20].startswith("2:a very long file name f..hdv") and scr[20][31:39].strip() == "800K", "slot 2 shows name and 800K")
    err, st = bus.status(2, 0)
    check(st[0] == 0xF8 and st[1] | (st[2] << 8) == 1600, "firmware: unit 2 online with 1600 blocks")
    err, blk = bus.read(2, 1599)
    check(blk[0:2] == struct.pack("<H", 1599) and blk[2:6] == b"LONG", "unit 2 last block from the new image")
    ee = bytes(lib.sim_eeprom()[16 + 128:16 + 128 + 40]).split(b"\0")[0]
    check(ee.startswith(b"/GAMES/AVERYL~") and ee.endswith(b".HDV"), "EEPROM slot 2 holds the 8.3 path %r" % ee)

    print("== 4. write-protected file, RETURN on a file asks for a slot")
    apple.press(0x0A, 0x0D)                 # down to locked_ro.po, RETURN
    apple.run()
    check("Which drive (1-4)?" in apple.screen()[17], "drive question")
    apple.press("3")
    apple.run()
    scr = apple.screen()
    check(scr[21].startswith("3:locked_ro.po") and scr[21][39] == "*", "slot 3 mounted, marked write protected")
    check(bus.write(3, 0, bytes(512)) == 0x2B, "firmware refuses writes to it ($2B)")

    print("== 5. write through slot 2 reaches the file")
    data = bytes((i * 7) & 0xFF for i in range(512))
    check(bus.write(2, 10, data) == 0, "write block 10 on unit 2")
    raw = open(os.path.join(card, "GAMES", "a very long file name for a game disk.hdv"), "rb").read()
    check(raw[10 * 512:11 * 512] == data, "block 10 in the file on the card")

    print("== 6. back to the root, paging through BIG (40 entries)")
    apple.press(0x08)                       # left arrow
    apple.run()
    check(apple.screen()[1].rstrip() == "SD:/", "left arrow: back at the root")
    apple.press(0x0D)                       # BIG is selected (first entry)
    apple.run()
    scr = apple.screen()
    check(scr[1].startswith("SD:/BIG/"), "in BIG")
    check(scr[2][2:30].strip() == "INNER/" and scr[3][2:30].strip() == "disk00.po", "BIG starts with INNER/, disk00.po")
    check("Page 1 >" in scr[17], "page 1 of several")
    apple.press(*([0x0A] * 16))             # 16 x down: second page, second entry
    apple.run()
    scr = apple.screen()
    apple.show("BIG, page 2")
    check("Page 2 >" in scr[17], "page 2")
    check(apple.selected().startswith("disk15.po"), "selection on disk15.po (%s)" % apple.selected())
    apple.press(*([0x0A] * 30))
    apple.run()
    scr = apple.screen()
    check(scr[17].startswith("Page 3") and ">" not in scr[17], "last page, no more")
    check(apple.selected().startswith("disk39.po"), "last entry disk39.po (%s)" % apple.selected())
    check(scr[12][2:30].strip() == "disk39.po", "disk39.po is the 11th entry of page 3")
    check(scr[13].strip() == "", "nothing after the last entry")
    apple.press(*([0x0B] * 11))             # up into page 2
    apple.run()
    check("Page 2" in apple.screen()[17] and apple.selected().startswith("disk28.po"), "up across the page boundary (%s)" % apple.selected())

    def page_sel():
        return apple.screen()[17][:6], apple.selected()
    apple.press(ord("."))                   # . = next page, first entry
    apple.run()
    check(page_sel()[0] == "Page 3" and page_sel()[1].startswith("disk29.po"), "'.': next page, first entry %s" % (page_sel(),))
    apple.press(ord("."))                   # on the last page: its last entry
    apple.run()
    check(page_sel()[0] == "Page 3" and page_sel()[1].startswith("disk39.po"), "'.' on the last page: last entry %s" % (page_sel(),))
    apple.press(ord(","))                   # , = previous page, first entry
    apple.run()
    check(page_sel()[0] == "Page 2" and page_sel()[1].startswith("disk14.po"), "',': previous page, first entry %s" % (page_sel(),))
    apple.press(ord("<"), ord(","))         # < too; on page 1: its first entry
    apple.run()
    check(page_sel()[0] == "Page 1" and page_sel()[1].startswith("INNER/"), "'<' and ',' on page 1: first entry %s" % (page_sel(),))
    apple.mem[0xC061] = 0x80                # Open-Apple held
    apple.press(0x0A)                       # Open-Apple-down: next page
    apple.run()
    check(page_sel()[0] == "Page 2" and page_sel()[1].startswith("disk14.po"), "Open-Apple-down: next page %s" % (page_sel(),))
    apple.press(0x0B)                       # Open-Apple-up: previous page
    apple.run()
    check(page_sel()[0] == "Page 1" and page_sel()[1].startswith("INNER/"), "Open-Apple-up: previous page %s" % (page_sel(),))
    apple.mem[0xC061] = 0x00
    apple.press(0x0A)                       # down without Open-Apple: one entry
    apple.run()
    check(page_sel()[0] == "Page 1" and page_sel()[1].startswith("disk00.po"), "down without Open-Apple: next entry %s" % (page_sel(),))
    apple.press(0x0B, 0x0D)                 # up to INNER/, RETURN
    apple.run()
    scr = apple.screen()
    check(scr[1].rstrip() == "SD:/.../INNER/" and scr[2][2:31].rstrip() == "Apple Diskware - ProDOS..2mg",
          "88-character name: start, '.', last 4 characters: %r %r" % (scr[1], scr[2][2:31]))
    apple.press(0x1B)                       # ESC: back to BIG
    apple.run()
    apple.press(0x1B)                       # ESC = parent
    apple.run()
    check(apple.screen()[1].rstrip() == "SD:/", "ESC: parent directory")

    print("== 7. .2MG and DOS-order .DSK")
    apple.press(0x0A, 0x0A, 0x0A, 0x0A, 0x0D)   # UTILS
    apple.run()
    scr = apple.screen()
    apple.show("UTILS")
    check(scr[2][2:30].strip() == "Copy II Plus.2mg" and scr[3][2:30].strip() == "DOS ORDER.DSK", "UTILS listing")
    apple.press("4")
    apple.run()
    scr = apple.screen()
    check(scr[2][30:39].strip() == scr[22][30:39].strip() == "141K" and scr[22].startswith("4:Copy II Plus.2mg"),
          ".2MG: list and drive line show the file size, header included (%s / %s)" % (scr[2][30:39].strip(), scr[22][30:39].strip()))
    err, blk = bus.read(4, 7)
    check(blk[0:2] == struct.pack("<H", 7) and blk[2:6] == b"2MGI", ".2MG: block 7 read past the 64-byte header")
    apple.press(0x0A, "4")
    apple.run()
    err, blk = bus.read(4, 9)       # block 9 = track 1, block 1 -> DOS sectors 13 and 12
    check(err == 0 and blk[0] == 1 and blk[1] == 13 and blk[256] == 1 and blk[257] == 12, "DOS order: block 9 = T1 S13 + T1 S12")
    err, st = bus.status(4, 0)
    check(st[1] | (st[2] << 8) == 280, "DOS order image = 280 blocks")

    print("== 8. eject slot 3, mounting a non-image fails")
    apple.press("E", "3")
    apple.run()
    scr = apple.screen()
    check("Ejected: drive 3" in scr[17] and scr[21].startswith("3:- empty -"), "slot 3 ejected")
    apple.press(0x1B, 0x0A, 0x0A, 0x0A, "3")   # root, README.DOC, into slot 3
    apple.run()
    scr = apple.screen()
    check("Error $22 no image" in scr[17], "README.DOC refused: '%s'" % scr[17].strip())

    print("== 8b. a control list sent with its length bytes is understood too")
    check(bus.control(1, 0x22, [1, 0, 3]) == 0, "eject slot 4 with [len lo, len hi, slot]")
    err, st = bus.status(4, 0)
    check(st[0] == 0xE8, "slot 4 empty")
    check(bus.control(1, 0x21, [3, 0, 3, 0xFF, 0x7F]) == 0x27, "mount with an index that does not exist: $27")
    apple.press(0x0A, 0x0A, 0x0A, 0x0A, 0x0D, 0x0A, "4")   # UTILS, DOS ORDER.DSK -> slot 4 again
    apple.run()
    err, st = bus.status(4, 0)
    check(st[1] | (st[2] << 8) == 280, "slot 4 mounted again from the browser")

    print("== 9. B: boot menu off and reboot into the image of slot 1")
    apple.press("B")
    stop = apple.run()
    check(stop == ROMBASE, "jumps to $C500 (PR#5)")
    bus.reset()
    err, dib = bus.status(1, 3)
    check(dib[5:19] == b"CORE4SD DRIVE1", "unit 1 is the image again")
    err, blk = bus.read(1, 0)
    check(blk[2:7] == b"PART1", "unit 1 block 0 = PART1.PO")

    print("== 10. power cycle: slots come back from EEPROM")
    lib.sim_setup(card.encode(), 0, 1)
    bus.reset()
    err, st2 = bus.status(2, 0)
    err, st3 = bus.status(3, 0)
    err, st4 = bus.status(4, 0)
    check(st2[1] | (st2[2] << 8) == 1600, "slot 2 restored (long name via 8.3 path)")
    err, info = bus.status(1, 0x20)               # INFO: drive s name at 30 + 32*s + 4
    name2 = info[30 + 32 + 4: 30 + 64].split(b"\0")[0]
    check(name2.rstrip() == b"a very long file name f..hdv", "after power-up drive 2 shows its long name: %r" % name2)
    name4 = info[30 + 96 + 4: 30 + 128].split(b"\0")[0]
    check(name4 == b"DOS ORDER.DSK", "after power-up drive 4 (in /UTILS) shows its long name: %r" % name4)
    ee = lib.sim_eeprom()                           # a stored path that is not on the card
    saved = bytes(ee[16 + 128 * 2: 16 + 128 * 2 + 20])
    for i, c in enumerate(b"/GAMES/NOSUCH.PO\0"):
        ee[16 + 128 * 2 + i] = c
    lib.sim_setup(card.encode(), 0, 1)
    bus.reset()
    err, st3 = bus.status(3, 0)
    check(st3[0] == 0xE8, "a stored path that is not on the card leaves the drive empty")
    for i, c in enumerate(saved):
        ee[16 + 128 * 2 + i] = c
    lib.sim_setup(card.encode(), 0, 1)
    bus.reset()
    check(st3[0] == 0xE8, "slot 3 stays ejected")
    check(st4[1] | (st4[2] << 8) == 280, "slot 4 restored (DOS order)")
    err, blk = bus.read(4, 9)
    check(blk[0] == 1 and blk[1] == 13, "slot 4 still in DOS order")

    print("== 10a. CONFIG.TXT: 1 in the first line forces the boot menu at power-up")
    cfg = os.path.join(card, "config.txt")         # lower case: found as CONFIG.TXT all the same

    def power_up_menu(button=0):
        lib.sim_setup(card.encode(), button, 1)     # no button, unless asked for
        bus.reset()
        err, info = bus.status(1, 0x20)
        err, dib = bus.status(1, 3)
        return info[1] & 3, bytes(dib[5:21]).rstrip()
    check(power_up_menu() == (0, b"CORE4SD DRIVE1"), "without CONFIG.TXT: drive 1 boots its image")
    for text, want, what in [
            ("1 ; always boot menu\r\nsecond line\r\n", (3, b"CORE4SD MENU"), "'1 ; comment': menu forced"),
            ("  3\n", (3, b"CORE4SD MENU"), "'  3' (bits 1+2, leading spaces): menu forced"),
            ("2\n", (0, b"CORE4SD DRIVE1"), "'2' (no button): no menu"),
            ("0\n", (0, b"CORE4SD DRIVE1"), "'0': no menu"),
            ("menu\n", (0, b"CORE4SD DRIVE1"), "no number: no menu")]:
        open(cfg, "w", newline="").write(text)
        got = power_up_menu()
        check(got == want, "CONFIG.TXT %s (flags, DIB name %r)" % (what, got))
    # bit 2: a board without a button, A3 floats; a "pressed" pin is ignored
    check(power_up_menu(1) == (1 | 2, b"CORE4SD MENU"), "without CONFIG.TXT the button still brings the menu")
    for text, want, what in [
            ("2\n", (0, b"CORE4SD DRIVE1"), "'2' (no button): a floating pin read as pressed is ignored"),
            ("3\n", (3, b"CORE4SD MENU"), "'3' (no button, always menu): menu forced")]:
        open(cfg, "w", newline="").write(text)
        got = power_up_menu(1)
        check(got == want, "CONFIG.TXT %s (flags, DIB name %r)" % (what, got))
    open(cfg, "w").write("1\n")
    lib.sim_setup(card.encode(), 0, 1)
    bus.reset()
    bus.control(1, 0x24, [0])                       # S re-reads the card: CONFIG.TXT is not read again
    bus.control(1, 0x23, [0])                       # B: menu off
    bus.control(1, 0x24, [0])
    err, info = bus.status(1, 0x20)
    check(info[1] & 1 == 0, "CONFIG.TXT is only read at power-up, not when the card is re-read")
    os.remove(cfg)

    print("== 10b. R rotates the slots: 1 -> 2, 2 -> 3, 3 -> 4, 4 -> 1")
    lib.sim_setup(card.encode(), 1, 1)           # button: menu
    lib.sim_set_button(0)
    apple = Apple(bus)
    boot(bus, apple)
    check(apple.screen()[23].rstrip() == "1-4 Mount Eject Rotate Boot 1".ljust(36) + "Help", "menu boot: key line")
    apple.press("R")
    apple.run()
    scr = apple.screen()
    apple.show("slots rotated")
    check("Drives rotated" in scr[17], "status line reports the rotation")
    check(scr[19].startswith("1:DOS ORDER.DSK"), "slot 1 = old slot 4")
    check(scr[20].startswith("2:PART1.PO"), "slot 2 = old slot 1")
    check(scr[21].startswith("3:a very long file name f..hdv"), "slot 3 = old slot 2")
    check(scr[22].startswith("4:- empty -"), "slot 4 = old slot 3 (empty)")
    check(scr[23].rstrip() == "1-4 Mount Eject Rotate Boot 1".ljust(36) + "Help", "after R the key line has no Quit")
    apple.press("Q")
    stop = apple.run()
    check(stop is None and re.search(r"Core4SD \d+\.\d+ - Slot ", apple.screen()[0]), "after R, Q is ignored")
    err, blk = bus.read(2, 5)
    check(not err and blk[2:7] == b"PART1", "unit 2 now reads PART1.PO")
    err, st3 = bus.status(3, 0)
    err, st4 = bus.status(4, 0)
    check(st3[1] | (st3[2] << 8) == 1600 and st4[0] == 0xE8, "unit 3 = long image, unit 4 empty")
    lib.sim_setup(card.encode(), 0, 1)           # power cycle: rotation kept in EEPROM
    bus.reset()
    err, blk = bus.read(1, 9)
    check(not err and blk[0] == 1 and blk[1] == 13, "after power-up unit 1 is the DOS-order image")
    err, blk = bus.read(2, 5)
    check(not err and blk[2:7] == b"PART1", "after power-up unit 2 is PART1.PO")
    for _ in range(3):                           # all the way round: back to the start
        check(bus.control(1, 0x25, [0]) == 0, "CONTROL $25 ok")
    err, blk = bus.read(1, 5)
    err2, st4 = bus.status(4, 0)
    check(blk[2:7] == b"PART1" and st4[1] | (st4[2] << 8) == 280, "four rotations give the old order")

    print("== 10c. Apple II+ (no lower case in its character ROM): upper case only")
    lib.sim_setup(card.encode(), 1, 1)           # button: menu
    lib.sim_set_button(0)
    apple = Apple(bus, plus=True)
    boot(bus, apple)
    apple.press(0x0A, 0x0D)                      # down, RETURN: into GAMES
    apple.run()
    scr = apple.screen()
    apple.show("II+: GAMES")

    def lower_cells():
        cells = []
        for row in range(24):
            base = [0x400, 0x480, 0x500, 0x580, 0x600, 0x680, 0x700, 0x780][row % 8] + 0x28 * (row // 8)
            cells += [apple.mem[base + i] for i in range(40)]
        return [c for c in cells if 0xE1 <= c <= 0xFA or 0x61 <= c <= 0x7A]
    check(scr[2][2:30].rstrip() == "TOTAL REPLAY V5.PO", "II+: names in upper case: %r" % scr[2][2:30])
    check(re.fullmatch(r" CORE4SD \d+\.\d+ - SLOT 5, UNITS 1-4", scr[0].rstrip()), "II+: title in upper case: %r" % scr[0])
    apple.press("?")
    apple.run()
    check(apple.screen()[3][4:].startswith("      CORE4SD 1.0 ON A2+"), "II+: the help names the model A2+: %r" % apple.screen()[3])
    apple.press(" ")
    apple.run()
    check(any(l.startswith("2:- EMPTY -") or l.startswith("3:- EMPTY -") or l.startswith("4:- EMPTY -")
              for l in scr[19:23]), "II+: '- EMPTY -' in upper case")
    check(lower_cells() == [], "II+: no lower case (normal or inverse) on the screen")

    print("== 11. no SD card: menu boots and says so")
    lib.sim_setup(card.encode(), 0, 0)
    apple = Apple(bus)
    stop = boot(bus, apple)
    scr = apple.screen()
    apple.show("no SD card")
    check(stop is None and scr[1].startswith("No SD card"), "browser reports the missing card")
    check(re.search(r"Core4SD \d+\.\d+ - Slot ", scr[0]), "boot menu active")
    check("(S = reload)" in scr[1], "the missing card points to S")
    lib.sim_set_sd(1)                       # card inserted, S reads it
    apple.press("S")
    apple.run()
    scr = apple.screen()
    check(scr[1].rstrip() == "SD:/" and scr[2][2:30].strip() == "BIG/", "S: the card is read, the root is listed")

    print("== 12. CORE4SD.SYSTEM started by ProDOS at $2000, Q quits via MLI")
    lib.sim_setup(card.encode(), 0, 1)
    apple = Apple(bus)
    bus.reset()
    prog = open(os.path.join(ROOT, "apple", "CORE4SD.SYSTEM"), "rb").read()
    for i, b in enumerate(prog):
        apple.mem[0x2000 + i] = b
    apple.mem[0xBF00] = 0x4C
    apple.mpu.pc = 0x2000
    apple.mpu.sp = 0xFF
    stop = apple.run()
    check(stop is None and re.match(r" Core4SD \d+\.\d+ - Slot ", apple.screen()[0]), "runs as a SYS program: the same title as the boot menu")
    check(apple.screen()[23].rstrip() == "1-4 Mount Eject Rotate Boot 1 Quit".ljust(36) + "Help", "under ProDOS the key line offers Quit")
    check([c for c in range(40) if apple.mem[0x7D0 + c] < 0x80] == [0, 1, 2, 10, 16, 23, 30, 36],
          "under ProDOS: 1-4, E, R, B, Q and H in inverse")
    check([apple.mem[b] for b in (0x5D0, 0x650, 0x6D0, 0x750)] == [0xB1, 0xB2, 0xB3, 0xB4], "no boot menu: drive numbers in normal video too")
    apple.press("Q")
    stop = apple.run()
    check(stop == 0xBF00 and apple.mem[apple.mem[0x100 + apple.mpu.sp + 1] + (apple.mem[0x100 + apple.mpu.sp + 2] << 8) + 1] == 0x65, "Q calls ProDOS QUIT")


    print("== 12b. under ProDOS, S hides and blocks Q")
    apple = Apple(bus)
    for i, b in enumerate(prog):
        apple.mem[0x2000 + i] = b
    apple.mem[0xBF00] = 0x4C
    apple.mpu.pc = 0x2000
    apple.mpu.sp = 0xFF
    apple.run()
    apple.press("R")
    apple.run()
    check(apple.screen()[23].rstrip() == "1-4 Mount Eject Rotate Boot 1".ljust(36) + "Help", "after R the key line has no Quit")
    apple.press("Q")
    stop = apple.run()
    check(stop is None, "after R, Q no longer quits to ProDOS")
    for _ in range(3):                           # back to the old order
        bus.control(1, 0x25, [0])

    print("== 12c. Apple IIc Plus: the Core4SD in slot 5, as on the IIc")
    lib.sim_setup(card.encode(), 0, 1)
    apple = Apple(bus, iicplus=True)
    bus.reset()
    for i, b in enumerate(prog):
        apple.mem[0x2000 + i] = b
    apple.mpu.pc = 0x2000
    apple.mpu.sp = 0xFF
    stop = apple.run()
    scr = apple.screen()
    apple.show("IIc Plus")
    check(stop is None and re.fullmatch(r" Core4SD \d+\.\d+ - Slot 5, Units 1-4", scr[0].rstrip()),
          "IIc Plus: found in slot 5 ($C50D), title shows slot 5, units 1-4: %r" % scr[0])
    apple.press("?")
    apple.run()
    check(apple.screen()[3][4:].startswith("      Core4SD 1.0 on A2c+"), "IIc Plus: the help names the model A2c+: %r" % apple.screen()[3])
    apple.press(" ")
    apple.run()
    check(scr[19].startswith("1:PART1.PO"), "IIc Plus: drive lines read through slot 5")
    apple.press("	")
    apple.run()
    check("No other Core4SD" in apple.screen()[17] and " - Slot 5, Units 1-4" in apple.screen()[0],
          "Tab with one Core4SD only: 'No other Core4SD', still slot 5: %r" % apple.screen()[17])
    apple.press("B")
    stop = apple.run()
    check(stop == ROMBASE, "IIc Plus: B jumps to $C500 (PR#5)")

    print("== 12d. acknowledge only after the DEST check: never another device's packet")
    lib.sim_setup(card.encode(), 0, 1)
    check(bus.reset() == 4, "four units, ids 1-4")

    def raw(dest, cmd, params=(), corrupt=False):
        """A command packet straight to the firmware; returns (reply length, ACK pulls)."""
        payload = bytes([cmd, 3]) + bytes(params) + bytes(7 - len(params))
        rx = bytearray(encode_rx(dest, 0x80, payload))
        if corrupt:
            rx[5] = 0x00                                 # no PBEGIN at [5]
        n = lib.sim_packet(bytes(rx), len(rx), None, 0, bus.tx)
        return n, lib.sim_ack_lows()

    n, lows = raw(5, 0x00, [0, 0, 0])                    # STATUS for id 5: not one of ours
    check(n == 0 and lows == 0 and lib.sim_ack_driven() == 0,
          "STATUS for another device: no reply, ACK never pulled low, ACK let go (%d, %d)" % (n, lows))
    n, lows = raw(5, 0x05)                               # INIT once all four ids are taken
    check(n == 0 and lows == 0 and lib.sim_ack_driven() == 0,
          "INIT after the fourth: not ours, not acknowledged (%d, %d)" % (n, lows))
    err, _ = bus.status(1, 0)
    check(err == 0 and lib.sim_ack_lows() == 1, "own STATUS: acknowledged once (%d)" % lib.sim_ack_lows())
    err = bus.control(1, 0x23, [0])                      # CONTROL: command and data packet
    check(err == 0 and lib.sim_ack_lows() == 2,
          "own CONTROL: command and data packet acknowledged (%d)" % lib.sim_ack_lows())
    n, lows = raw(1, 0x00, [0, 0, 0], corrupt=True)
    check(n == 0 and lows == 0 and lib.sim_ack_driven() == 0,
          "invalid packet: not acknowledged, no reply (%d, %d)" % (n, lows))
    # A garbled INIT right after a bus reset (seen when booting from the
    # internal drive): it must not be answered -- a $06 reply would make the
    # ROM count a device without an id here. The ROM sends it again.
    lib.sim_bus_reset()
    n, lows = raw(1, 0x05, corrupt=True)
    check(n == 0 and lows == 0, "garbled INIT: not acknowledged, not answered (%d, %d)" % (n, lows))
    stat, _ = bus.packet(1, 0x05, [])
    check(stat == 0x80, "the INIT sent again: drive 1 takes id 1 (status $%02X)" % stat)
    err, dib = bus.status(1, 3)
    check(err == 0 and bytes(dib[5:19]) == b"CORE4SD DRIVE1", "drive 1 answers as unit 1 after it: %r" % bytes(dib[5:19]))
    check(bus.reset() == 4, "a full INIT afterwards: four units again")

    print("== 13. CORE4SD.PO (as MENUDISK.PO): a name too long for SdFat's buffer, then boot the .po")
    lib.sim_setup(card.encode(), 1, 1)           # button: menu
    lib.sim_set_button(0)
    apple = Apple(bus)
    boot(bus, apple)
    apple.press(0x0A, 0x0A, 0x0A, 0x0A, 0x0D)    # UTILS
    apple.run()
    scr = apple.screen()
    apple.show("UTILS")
    check(scr[4][2:30].strip() == "MENUDISK.PO", "MENUDISK.PO listed")
    check(scr[5][2:9] == "ZXXXXX~" and scr[5][10:14] == ".PO ", "a 100-character name falls back to its 8.3 name (%r)" % scr[5][2:14])
    apple.press(0x0A, 0x0A, "1")                 # MENUDISK.PO into slot 1
    apple.run()
    check(apple.screen()[19].startswith("1:MENUDISK.PO"), "MENUDISK.PO in slot 1")
    apple.press("B")
    check(apple.run() == ROMBASE, "reboot")
    apple = Apple(bus)
    stop = boot(bus, apple)
    scr = apple.screen()
    apple.show("booted from MENUDISK.PO")
    check(stop is None and re.match(r" Core4SD \d+\.\d+ - Slot ", scr[0]), "the .po's own boot block starts the browser")
    po = open(os.path.join(ROOT, "apple", "CORE4SD.PO"), "rb").read()
    prog = open(os.path.join(ROOT, "apple", "CORE4SD.SYSTEM"), "rb").read()
    # the file's data blocks, from its index block (entry 1 of the volume directory)
    ent = po[2 * 512 + 4 + 0x27: 2 * 512 + 4 + 0x4E]
    key = ent[0x11] | (ent[0x12] << 8)
    n = (len(prog) + 511) // 512
    data_blocks = [po[key * 512 + i] | (po[key * 512 + 256 + i] << 8) for i in range(n)]
    check(apple.boot_reads == data_blocks,
          "boot block read the file's data blocks %s" % apple.boot_reads)
    check(bytes(apple.mem[0x2000 + i] for i in range(0x40)) == prog[:0x40], "CORE4SD.SYSTEM code at $2000")


if __name__ == "__main__":
    main()
