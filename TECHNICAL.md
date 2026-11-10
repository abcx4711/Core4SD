# Core4SD – Technical Reference

Hardware, SmartPort protocol, and the layout, build and tests of the Core4SD
project. Installation and use are described in [README.md](README.md).

## Hardware

The firmware runs on any board with an ATmega328P or ATmega328PB and an SD card module, wired to the Apple's SmartPort connector, e.g. an Arduino Nano (also clones) or an Arduino Uno. A ready-made board with this wiring is the SPIISD V1, the Nano build of the SmartportSD (wiring by Robert Justice).

What the board needs:

- **16 MHz clock.** The SmartPort bus routines in `packet_16mhz.S` are timed to the cycle for 16 MHz; boards at 8 MHz (e.g. 3.3 V boards) do not work.
- **5 V.** The SmartPort signals of the Apple are 5 V.
- **An SD card module on SPI** (the chip's hardware SPI: MOSI, MISO, SCK, plus a chip select). SD cards run at 3.3 V: the module needs its own regulator and level shifters, as the usual Arduino SD modules have. A bare card on the 5 V pins would be damaged. SdFat talks to the card at 8 MHz.

| Signal | Pin (Nano / Uno) | Can be changed |
|---|---|---|
| PH0/REQ, PH1, PH2, PH3 | D2, D3, D4, D5 | no |
| RDDATA | D6 | no |
| WRDATA | D7 | no |
| WRPROT/ACK | A5 | no |
| SD: MOSI, MISO, SCK | D11, D12, D13 | no (hardware SPI) |
| SD: CS | D10 | yes, `SD_CS_PIN` in `src/config.h` |
| Serial output (TX) | D1, 230400 baud | no |
| Button (optional; without it and its pull-down, `CONFIG.TXT` with `2` or `3` keeps the floating pin from being read, or A3 is tied to GND; see the README) | A3, switches to +5 V, with a pull-down | yes, `EJECT_PIN` / `EJECT_ACTIVE` in `src/config.h` |

The SmartPort lines are fixed because the assembler routines access port D and port C directly. The firmware prints messages and errors on the serial port.

Schematics of the SPIISD V1 (board A and board B), a variant with an Arduino Uno and a data logger shield, and breadboard layouts for both are in [hardware/SmartportSD_Schematic_Breadboard.md](hardware/SmartportSD_Schematic_Breadboard.md) (the drawings as PNG and SVG in `hardware/img`).

The **SPIISD V2** with LCD has closed firmware and a different user interface. This has not been tested on it.

## The protocol (for other programs)

Each of the four Core4SD drives also answers these SmartPort calls:

| Call | Data | Meaning |
|---|---|---|
| STATUS `$20` | 160 bytes | INFO: `[0]` directory depth, `[1]` flags (bit0 menu forced, bit1 menu active, bit7 no SD card), `[2..29]` name of the current directory; from `[30]` 32 bytes per drive: flags (bit0 mounted, bit1 write protected), blocks (2 bytes), the KB of the file beyond its blocks (1 byte, e.g. 1 for the 64-byte header of a `.2MG`; the drive line adds it to the blocks, so it shows the file size as the list does), name (28 bytes) |
| STATUS `$40`+p | 512 bytes | directory page p (0–63): `[0]` number of entries n (≤15), `[1]` bit0 = another page follows; from `[2]` 34 bytes per entry: type (0 file, 1 directory, 2 image; + `$80` for a read-only file), FAT directory index (2 bytes), size in KB rounded up (3 bytes), name (28 bytes) |
| CONTROL `$20` | 2 bytes | change directory: index low/high; `$FFFF` = parent directory, `$FFFE` = root |
| CONTROL `$21` | 3 bytes | mount: drive 0–3, index low/high in the current directory |
| CONTROL `$22` | 1 byte | eject drive 0–3 |
| CONTROL `$23` | 1 byte | boot menu off |
| CONTROL `$24` | 1 byte | re-initialise the SD card and reload the drives |
| CONTROL `$25` | 1 byte | rotate the drives: 1 → 2, 2 → 3, 3 → 4, 4 → 1 |

The error codes are the usual SmartPort codes:

- `$21` unknown code
- `$22` not a valid image, or bad parameter
- `$27` I/O error (a READ that fails on the SD card is tried three times before)
- `$28` no SD card
- `$2B` write protected
- `$2D` block out of range
- `$2F` drive empty

The A2let finds the adapter by its DIB name (STATUS 3), which starts with `CORE4SD` (`CORE4SD DRIVE1` to `CORE4SD DRIVE4`, `CORE4SD MENU` in the boot menu).

## Core4SD in a chain with other SmartPort devices

After a bus reset the Apple sends INIT to the IDs `$81`, `$82`, … one after the other. Only the first device in the chain that has no ID yet answers; the next one is reached once it has its ID. Core4SD takes the INITs it receives, in order, for drives 1–4 (`cmd_init()`), and answers the fourth with the status `$FF`, "last device in the chain".

**Another device in front of Core4SD** (closer to the Apple) takes `$81`. Core4SD then gets `$82`–`$85`:

| Unit | ID | Device |
|---|---|---|
| 1 | `$81` | the other device |
| 2 | `$82` | Core4SD drive 1 |
| 3 | `$83` | Core4SD drive 2 |
| 4 | `$84` | Core4SD drive 3 |
| 5 | `$85` | Core4SD drive 4 |

Packets for `$81` are not Core4SD's (`slot_of()` finds no drive with that ID). Core4SD reads every command packet without acknowledging it (`ReceivePacket` does not touch ACK); only when the DEST is one of its own units does it pull ACK low (`ack_packet()`). A packet for another device is never acknowledged: ACK is let go at once (`ignore_packet()`). If the other device takes the packet, Core4SD stays off ACK until that exchange is over (`not_ours()`); if nobody takes it within about 2 ms, or the Apple resets the bus, it goes straight back to listening. The consequences:

- **Booting:** the Apple boots unit 1, the other device. The Core4SD boot menu does not come up by itself, not even with drive 1 empty or the button pressed, and `B` in the A2let starts the other device too. The A2let shows this: with drive 1 on a unit other than 1, its key line says `Reboot` instead of `Boot 1`, and the help window `Reboot (unit 1)`. The A2let can then only be started as `CORE4SD.SYSTEM` under ProDOS.
- **The A2let** still works: it looks for the unit whose DIB name starts with `CORE4SD`, not for unit 1, and sends all its calls there. Its title line shows the units of drives 1–4, e.g. `Slot 5, Units 2-5`.
- **ProDOS 8** uses at most four units per SmartPort (two in the slot, two remapped to other slots by ProDOS 2.x): Core4SD drive 4, unit 5, is not seen by ProDOS. Programs that call the SmartPort themselves still reach it.
- **A garbled command packet** (no PBEGIN at its place, DEST not a unit, or SRC not the host) is never acknowledged. The Apple's SmartPort ROM then sends it again: the IIc ROM 4X, the IIc Plus ROM and the Liron card try an unacknowledged command up to about 3000 times, then reset the bus, send INIT again and try once more. Answering it (as SmartportSD does with a bus error) is not needed, and a reply could collide with the other device's; a bus-error reply to a garbled INIT would even make the ROM count a device that has no ID.

**A device behind Core4SD** gets no ID: Core4SD reports itself as the last device with its fourth INIT reply, so the Apple sends no further INIT. The other device stays unused, as behind the old SmartportSD.

All this depends on the chain passing the bus on only after the device in front has its ID. If the Nano saw the INIT for `$81` as well, it would answer it too (it still has free drives), both devices would answer at the same time, and the bus would hang. How the SPIISD board behaves in a chain has not been tested.

The Core4SD emulation in the POM2 emulator can show the case of a device in front: a drive of the Liron card (or of the //c's slot 5) that holds a medium comes first in the chain, and the Core4SD drives follow as units 2–5 (or 3–6). It is an ideal chain at the level of the firmware, so it shows the unit numbers, the boot and the A2let, but not the electrical side of the bus (ACK, collisions).

## Error messages in the serial log

With `LOG_LEVEL` `LVL_ERROR` or higher the firmware prints errors as short lines starting with `E`. `dn` is the drive (1–4), `bn` the block, `$cc` a command or CONTROL code, followed by the SmartPort error code and its name where there is one.

| Message | Meaning |
|---|---|
| `E SEND no REQ` | a reply was ready, but the Apple did not raise REQ to fetch it within about 200 ms |
| `E DATA timeout` | no data packet came after a WRITE or CONTROL command |
| `E DATA REQ high` | the Apple did not lower REQ after the data packet |
| `E DATA long 700` | the data packet does not fit into the buffer (its length so far) |
| `E CMD REQ high` | the Apple did not lower REQ after the command packet |
| `E CMD long 700` | the command packet does not fit into the buffer |
| `E CMD? d1 $C0` | unknown command for drive 1, answered with `$01` (e.g. an extended call, which Core4SD does not support) |
| `E OTHER $41` | the handshake of a packet for another device in the chain broke off (its command) |
| `E READ d1 b5 $27 IOERROR` | READ of drive 1, block 5 failed (after three tries for `$27`) |
| `E WRITE d1 b5 $2B NOWRITE` | WRITE of drive 1, block 5 failed |
| `E CTL $21 d2 $22 BADCTLPARM` | CONTROL `$21` sent to drive 2 failed |
| `E no file` | an image saved in the EEPROM is no longer on the card |
| `E image d2 $2D BADBLOCK` | the image for drive 2 is not usable (size or format) |
| `E path long d1` | the path of the image for drive 1 does not fit into the EEPROM, so it is not saved |
| `E no SD` | no SD card (at power-up or when re-initialising it) |

## Layout and building

```
platformio.ini   PlatformIO project (Nano, SdFat 2.3)
pre_build.py     builds the Apple side before the firmware (apple/*.asm -> src/bootimg.h, CORE4SD.PO, CORE4SD.HDV, CORE4SD-800.PO)
post_build.py    after the build, copies the hex, CORE4SD.SYSTEM, CORE4SD.PO, CORE4SD.HDV and CORE4SD-800.PO to bin/,
                 then rebuilds the SmartPortTools disks next to the project (if there) with the new CORE4SD.SYSTEM
src/main.cpp     firmware
src/sp_proto.*   packet encoding (plain C, testable on a PC)
src/packet_16mhz.S  bus routines (from SmartportSD-1.17, changed)
src/bootimg.h    built-in menu disk (generated)
apple/           browser.asm (the A2let), boot.asm (64tass)
tools/           mkbootimg.py (menu disk), mkpo.py (CORE4SD.PO, --blocks 1600: CORE4SD-800.PO), imgcheck.py (does an image mount as a SmartPort drive?), sdsort.py (sort an SD card)
test/            tests on the PC
bin/             ready-made files (`pio run` updates them after every firmware build):
                 core4sd_328p.hex (ATmega328P), core4sd_328pb.hex (ATmega328PB),
                 CORE4SD.SYSTEM, CORE4SD.PO, CORE4SD.HDV (the same image as the .po),
                 CORE4SD-800.PO (the same volume with 800K)
```

- Apple side, menu disk, `CORE4SD.PO`, `CORE4SD.HDV` and `CORE4SD-800.PO`: `make -C apple` (needs 64tass and Python 3). Step by step:
  ```
  64tass -C --nostart -o CORE4SD.SYSTEM browser.asm
  64tass -C --nostart -D NBLK=<n> -o boot.bin boot.asm
  ```
  `<n>` is the number of 512-byte blocks of `CORE4SD.SYSTEM` (its size divided by 512, rounded up); `make` and `pre_build.py` work it out themselves. `-C` is needed because the source is case-sensitive (`NENT`/`nent`).
- Firmware: `pio run` in the project folder. This also builds the Apple side first (`pre_build.py`) and puts everything into `bin/` afterwards (`post_build.py`). This needs 64tass: either in `PATH`, or named with its full path in the environment variable `TASS`, or entered in the commented-out `TASS_PATH` line near the top of `pre_build.py` (remove the `#`; that line then takes priority).
- Tests: `make -C test`, on Windows `mingw32-make -C test` (needs gcc, Python 3 and `pip install py65`).

What the tests check:

- `test_proto`: the packet encoding is byte-identical to the proven encoders of the SmartportSD (data packets, status, init), and every payload from 0 to 512 bytes survives encoding → decoding.
- `sim_e2e.py`: the real 6502 program runs in an emulator (py65). Its SmartPort calls are encoded into packets by an independently written Python model of the Apple side and go into the real firmware code, compiled for the PC, with a directory as the SD card. Among other things it checks:
  - booting from the menu disk
  - paging across page boundaries
  - mounting, ejecting, write protection and writing
  - `.2MG` and DOS images
  - restoring the drives from EEPROM
  - a missing SD card
  - starting as a SYS program and `Q` through ProDOS QUIT
  - rotating the drives with `R`, after which `Q` is disabled
  - names too long for SdFat (falling back to the 8.3 name)
  - booting `CORE4SD.PO` through its own boot block
  - the help window, `CONFIG.TXT`, an Apple II+ (upper case only)
  - a IIc Plus (the Core4SD in slot 5) and `Tab` with only one Core4SD
  - the handshake: a packet for another device is never acknowledged, an own one once per packet

  That is 160 checks in total. The SdFat replacement for the PC mimics the behaviour of SdFat 2.3.
