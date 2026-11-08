# Core4SD for Apple II (A2)

**Core4SD** is firmware for SD-card-based drive emulators connected to the SmartPort of Apple II computers. It needs an ATmega328 microcontroller (e.g. boards with an Arduino Nano or Uno) and an SD card reader. The classic SPIISD V1 DIY Kit is a working combination of a Nano and an SD card reader. The firmware turns disk images on the SD card (`.PO`, `.HDV`, `.2MG`, `.DSK`, `.DO`) into SmartPort drives and handles all of the A2's reads and writes to them. To choose the images, it supplies a small application, the **A2let**, which runs on the A2, browses the folders of the SD card and mounts the images into the drives.

```
 Core4SD 1.0 - Slot 5, Units 1-4
SD:/
  ADTPRO-2.1.0.PO                  800K
  A2DeskTop15.hdv                32768K     <- selection (inverse)
  A2FILECMD-800K-0.9.3.po          800K
  ProDOS_2_5_a8_143k.po            140K*
  TotalReplay260605.hdv          32768K
  Visicalc_Advanced.po             800K

Page 1      Mounted as drive 2
Drives ---------------------------------
1:TotalReplay260605.hdv          32768K
2:A2DeskTop15.hdv                32768K
3:ProDOS_2_5_a8_143k.po            140K*
4:- empty -
1-4 Mount Eject Rotate Boot 1       Help
```

*The A2let on the Apple II: the current SD card folder at the top, the four drives below.*

## Details

- **Four drives.** The adapter shows up as up to four SmartPort drives. The Apple boots from drive 1 if the Core4SD is the first device on the SmartPort; a IIc Plus does so if its internal 3.5" drive is empty. See [Boot order](#boot-order-of-the-apple-ii-models).
- **Several adapters:** a IIe, II+ or IIgs can hold several SmartPort cards (e.g. Liron), each with its own Core4SD, its own SD card and its own four drives. The A2let starts with the Core4SD in the highest slot; `Tab` switches to the next one. `B` boots the slot of the Core4SD shown.
- **Browsing the SD card** on the Apple: into and out of subdirectories, page by page (15 entries per page). Long file names are shown in up to 28 characters; a longer name keeps its start and its last 4 characters with a `.` for the part left out, so the extension stays visible: `Apple Diskware - ProDOS Assembler Tools (680-0194-A)(Apple Computer, Inc.)(1983) [!].2mg` becomes `Apple Diskware - ProDOS..2mg`. File and directory names are not sorted, because the Nano does not have the RAM for that: they appear in the order in which they are stored on the card. A card sorted on a PC beforehand, e.g. with `tools/sdsort.py` (directories first, then files, alphabetically), is shown in that order.
- **Image formats:** `.PO`, `.HDV`, `.2MG`, `.DSK` and `.DO`, see [Supported disk image formats](#supported-disk-image-formats).
- **Built-in boot menu:** the A2let is built into the firmware as a small virtual boot disk. If drive 1 is empty, the button was pressed, or `CONFIG.TXT` asks for it, the Apple boots straight into the A2let, without a floppy and without ProDOS.
- **The drives are remembered:** each drive keeps its image in EEPROM as an 8.3 path. A drive that was never set, or was ejected, stays empty.
- **Read-only files** (FAT attribute "read-only", or a locked `.2MG`) are mounted read-only. The A2let marks them with `*` behind the size: in the file list (FAT attribute) and in the drive lines.
- **The help window names the version and the Apple model** in its first line, e.g. `Core4SD 1.0 on A2ee`. The version is the one the firmware reports in its device information block (`FW_VERSION_HI`/`FW_VERSION_LO` in `src/main.cpp`). The model is found from the monitor ROM id bytes: `A2`, `A2+`, `A3` (Apple /// in emulation mode), `A2e`, `A2ee` (enhanced IIe), `A2c`, `A2c+` or `A2gs`; `?` for anything else.
- **Empty drives** report "no medium" (status `$E8`, 0 blocks) instead of staying silent, so there are no phantom drives that do not answer.
- **Without an SD card** the menu still boots and shows "No SD card". After inserting the card, `S` reads it again.

## Supported disk image formats

**DOS order and ProDOS order.** A 5.25" disk has 35 tracks of 16 sectors of 256 bytes, 140K in all. DOS 3.3 and ProDOS number the sectors of a track differently, so an image file can store the same 560 sectors in two arrangements:

- **DOS order** (`.dsk`, `.do`): track by track, each track in DOS 3.3 sector order 0–15.
- **ProDOS order** (`.po`): in ProDOS block order. Block n is the 512 bytes at offset n × 512, made from two sectors of track n / 8.

The contents of the disk are the same either way; only the order in the file differs. The order also says nothing about the operating system on the disk: a DOS 3.3 disk can be stored in ProDOS order, and a ProDOS disk in DOS order. Larger images (800K, hard disk images) exist only in ProDOS order.

A SmartPort drive hands out 512-byte blocks, so a ProDOS-order image can be read straight through. For a DOS-order image the firmware fetches the two sectors that make up each block (block 0 = track 0, DOS sectors 0 and 14).

**How the firmware tells them apart.** The firmware does not look at the contents of an image. Its sector order comes only from the extension of the 8.3 name and the file size, and for `.2MG` from its header:

| Extension | Condition | Sector order |
|---|---|---|
| `.DSK`, `.DO` | exactly 143360 bytes (140K) | DOS 3.3 |
| `.DSK`, `.DO` | any other multiple of 512 bytes | ProDOS (e.g. an 800K `.dsk`) |
| `.PO`, `.HDV` | a multiple of 512 bytes | ProDOS |
| `.2MG` | header format (offset `$0C`) 0 and data length 143360 | DOS 3.3 |
| `.2MG` | header format 1 | ProDOS |
| `.2MG` | format 0 with another length, or format 2 (nibble) | refused (`$22`) |
| any other | a multiple of 512 bytes | ProDOS (mountable, but not marked as an image in the list) |

A DOS-order image is reported as 280 blocks. Each ProDOS block is translated to two DOS 3.3 sectors on every read and write.

The extension therefore has to match the order of the image:
- A 140K image in **ProDOS** order with the extension `.dsk` or `.do` is read as DOS order, and ProDOS finds no volume on it. With the extension `.po` it is read correctly.
- A DOS-order image with the extension `.po` is not translated.

`.DSK` and `.DO` are treated alike. Nothing checks that the image really holds DOS 3.3.

**What runs.** A SmartPort drive hands out blocks, and at boot the Apple only loads block 0 from it. So the format of the file is not the whole story; what the disk does next counts too:

- **Runs:** ProDOS volumes, in any of the formats above, and disks whose boot code gets by with block 0 or loads the rest through SmartPort or ProDOS calls.
- **Does not run:** DOS 3.3, and any disk whose boot code uses the Disk II: it calls the read routine in the Disk II ROM (`$Cn5C`) or reads the Disk II registers itself. This includes many games and copy-protected disks, the Microsoft SoftCard CP/M disks and Apple Pascal: its boot block stops with `MUST BOOT FROM SLOT 6` unless it booted from a Disk II in slot 6.

These disks still mount, so the A2let shows them and the drive works; they just do not start from it. `tools/imgcheck.py` looks for these cases in an image.

## Installation

### 1. Flashing the firmware

The board and its wiring are described in [TECHNICAL.md](TECHNICAL.md#hardware). For assembling an SPIISD from the DIY kit, see the [SP2SD DIY KIT](https://github.com/kerokero5150/SP2SD_DIY_KIT) repository.

**Important:** the +5 V line to the Apple has to be disconnected first. USB power and Apple power must never be connected at the same time.

**Which chip?** Nano boards come with an ATmega328P or, on some newer clones, an ATmega328PB; the marking on the chip says which. There is a firmware for each:

| Chip | Ready-made firmware | avrdude | PlatformIO |
|---|---|---|---|
| ATmega328P | `bin/core4sd_328p.hex` | `-p m328p` | `-e nano328p` |
| ATmega328PB | `bin/core4sd_328pb.hex` | `-p m328pb` | `-e nano328pb` |

With the wrong one avrdude stops with a device signature error (the 328P reads as `0x1e950f`, the 328PB as `0x1e9516`), before anything is written.

There are two ways to flash.

**With PlatformIO** (builds the firmware as well; SdFat 2.3 is fetched automatically). 64tass must be installed, see [TECHNICAL.md](TECHNICAL.md#layout-and-building). The serial port is set in `platformio.ini` (`pport` for the 328P, `pbport` for the 328PB):

```
pio run -e nano328p -t upload
pio run -e nano328pb -t upload
```

**Ready-made firmware with avrdude.** The Nano's usual bootloader needs `-c arduino` and `-b 115200`; Nanos with the old bootloader need `-b 57600` instead.

- **Windows:** the port is `COMn`, shown in the Device Manager under "Ports (COM & LPT)" once the Nano is plugged in. Clones with a CH340 USB chip may need its driver first. avrdude comes with PlatformIO (`%USERPROFILE%\.platformio\packages\tool-avrdude\avrdude.exe`) or the Arduino IDE, or from github.com/avrdudes/avrdude. In the project folder:
  ```
  avrdude -p m328p -c arduino -P COM6 -b 115200 -U flash:w:bin\core4sd_328p.hex:i
  avrdude -p m328pb -c arduino -P COM6 -b 115200 -U flash:w:bin\core4sd_328pb.hex:i
  ```
- **macOS:** the port is `/dev/cu.usbserial-…` or `/dev/cu.wchusbserial…`; `ls /dev/cu.*` before and after plugging in the Nano shows which. avrdude is installed with Homebrew (`brew install avrdude`) or comes with PlatformIO.
  ```
  avrdude -p m328p -c arduino -P /dev/cu.usbserial-1410 -b 115200 -U flash:w:bin/core4sd_328p.hex:i
  avrdude -p m328pb -c arduino -P /dev/cu.usbserial-1410 -b 115200 -U flash:w:bin/core4sd_328pb.hex:i
  ```
- **Linux:** the port is usually `/dev/ttyUSB0`; the user has to be in the group `dialout` (or `uucp`, depending on the distribution).
  ```
  avrdude -p m328p -c arduino -P /dev/ttyUSB0 -b 115200 -U flash:w:bin/core4sd_328p.hex:i
  ```

### 2. SD card

The card is formatted with FAT16 or FAT32; the images can be in any folders. macOS `._*` files, hidden or system files and files ending in `.TXT`, `.BAK` or `.ZIP` are not shown. The list of hidden extensions is `HIDE_EXTENSIONS` in `src/config.h`; it goes by the extension of the 8.3 name, so a long name like `Notes for disk 2.txt` is hidden too. The list is in the order of the FAT directory; it is not sorted, because the Nano does not have the RAM for that. `tools/sdsort.py` sorts the directories of a card (or card image) on a PC.

#### Tips for the SD card

The drives are remembered by the **8.3 path** of their image, e.g. `/GAMESC~1/A2DESK~1.2MG` for `/Games Collection/A2DeskTop-1.5-de_800k.2mg` (the serial port shows it after mounting: `2 /GAMESC~1/A2DESK~1.2MG 1600 P 64`). That short name is not part of the file: the PC makes it up when the file (or folder) is created on the card, and it can be different the next time. After a power-up the firmware looks the file up by that short name. Therefore:

- **Short names suit images that stay mounted.** A name that already fits 8.3 (up to 8 characters, a dot and up to 3 characters, no spaces, e.g. `ADT15DE.2MG` or `GAMES`) is its own short name and never changes. The same goes for the folders on the way to it.
- **The first six characters should differ.** `A2DeskTop-1.5-de_800k.2mg` and `A2DeskTop15.hdv` both start with `A2DESK` and become `A2DESK~1` and `A2DESK~2` — which one gets `~1` depends on which was copied first.
- **A mounted image should not be deleted and copied back.** It may come back with a different short name, or another file may take its old one. An image is replaced safely by ejecting the drive (`E`), copying the new file and mounting it again in the A2let.
- **After reorganising the card** (restoring a backup, reformatting, copying everything to a new card) the drive lines are worth a check: a drive whose file is gone stays empty, but a *different* file that now has the same short name would be mounted. If in doubt, the images are best mounted again. The same applies after a swap to another card.
- **Sorting with `tools/sdsort.py` is safe:** it only moves the directory entries, the short names stay the same.
- **Names of 28 characters or less** are shown in full; up to 91 characters are shortened to 28 (start, `.`, last 4 characters), longer names are shown only as their 8.3 name.
- **At most 8 folder levels** can be browsed, and a directory shows at most 960 entries.
- **Protecting originals:** with the read-only attribute set on the PC (Windows: Properties → Read-only), the image is mounted write-protected and marked with `*`, so ProDOS cannot change it.
- **Notes and archives** (`.TXT`, `.BAK`, `.ZIP`) can stay on the card; they are not listed.
- **FAT16 or FAT32 only.** Cards of 64 GB and more come formatted as exFAT, which the firmware cannot read; they have to be reformatted as FAT32 (on Windows with a tool such as `guiformat`, since Windows itself only offers FAT32 up to 32 GB).

#### CONFIG.TXT

An optional file `CONFIG.TXT` in the root folder of the card holds settings (the name is not case-sensitive). It starts with a single digit (0–9) that switches options on; anything after the digit (e.g. a comment) and all further lines are ignored:

| Value | Option |
|---|---|
| 1 | boot menu at every power-up, as if the button were held (for boards without a button) |
| 2 | no button on the board: its pin (A3) is never read, so an unconnected pin cannot bring up the menu by chance |
| 3 | both: a board without a button that comes up in the boot menu at every power-up |

Without the file, or without a digit at the start of its first line, all options are off. The file is read at power-up only, not when `S` re-reads the card.

### 3. Optional: CORE4SD.PO

`bin/CORE4SD.PO` is a ready-made 140K ProDOS image (volume `/CORE4SD`) holding `CORE4SD.SYSTEM` (type `SYS`, aux type `$2000`). There are two ways to use it:

- **Booting it directly:** on the SD card and mounted as drive 1, its boot block loads the A2let without ProDOS. This works from SmartPort and other block drives, and from a Disk II (e.g. as a 5.25" disk in an emulator such as AppleWin; the Disk II path has only been tested in simulation).
- **Under ProDOS:** `CORE4SD.SYSTEM` is copied from it to the ProDOS drive, for example with CiderPress II or AppleCommander, and started from Bitsy Bye or similar. `Q` goes back to ProDOS.

`bin/CORE4SD.HDV` is the same image with a hard drive extension. Some emulators take a 140K `.po` for a 5.25" disk and put it into a floppy drive even when it is loaded into a hard drive slot; the `.hdv` goes into the hard drive slot.

`bin/CORE4SD-800.PO` is the same volume with 800K (1600 blocks), for a 3.5" drive, e.g. the internal drive of a IIc Plus or a 3.5" drive in an emulator. It boots and is used the same way.

`bin/CORE4SD.SYSTEM` is also included on its own.

## Usage

**Ways into the A2let:**

The first three only work when the Apple boots the Core4SD, i.e. when no other SmartPort device sits in front of it in the chain (on a IIc Plus: its internal 3.5" drive is empty); see [Boot order](#boot-order-of-the-apple-ii-models).

- Drive 1 is empty: the Apple boots into the A2let by itself.
- Pressing the button while powering up, or while the Apple is not accessing the adapter (the serial port reports this), brings up the A2let on the next boot (Ctrl-OpenApple-Reset, or `PR#n` for the slot of the adapter, e.g. `PR#5` on a IIc), even if drive 1 holds an image.
- `CONFIG.TXT` with `1` (or `3`) in its first line: the A2let comes up at every power-up, as if the button were held. This is for boards without a button; see [CONFIG.TXT](#configtxt).
- Under ProDOS, by starting `CORE4SD.SYSTEM`, also when another SmartPort device comes first.

The title line shows the firmware version, the SmartPort slot and the units of drives 1–4, e.g. `Core4SD 1.0 - Slot 5, Units 1-4`. When another SmartPort device comes first in the chain, the units start at 2 or higher, e.g. `Units 2-5`.

The key line at the bottom shows the keys in inverse: **1-4** Mount, **E**ject, **R**otate, **B**oot 1 (drive 1), **Q**uit (only when there is a ProDOS to go back to) and **H**elp.

| Key | Function |
|---|---|
| ↑ ↓ (or Ctrl-K / Ctrl-J) | move the selection; going past the edge changes the page. The Apple II/II+ has no ↑ ↓ keys; Ctrl-K and Ctrl-J work there |
| , . (or < >), Open-Apple-↑ ↓ | page up / down (15 entries), the selection on the first entry of the page; on the last page `.` selects its last entry. Open-Apple only on the IIe, IIc and IIgs; Shift cannot be used, as it gives the same key codes as without it |
| ← or ESC | parent directory |
| → or RETURN | open a directory; on a file, asks for the drive |
| 1 – 4 | mount the selected file as drive 1–4 |
| E | eject a drive (asks for the number) |
| R | rotate the drives: drive 1 moves to 2, 2 to 3, 3 to 4 and 4 to 1. The new order is remembered. Afterwards `Q` is hidden and disabled, because ProDOS would find a different image in its drive; only `B` reboots |
| S | read the SD card again |
| Tab | the next Core4SD adapter: searches the slots below the current one, then from slot 7 down to the slots above it, e.g. 5, 4, 3, 2, 1, 7 when the first was found in slot 6. Shows its directory, drives and slot; `B` then boots that slot. With only one Core4SD: `No other Core4SD` |
| B | boot menu off and reboot: starts the image in drive 1. With another SmartPort device in front of the Core4SD the Apple boots that one instead; the key line then says `Reboot` (see [TECHNICAL.md](TECHNICAL.md#core4sd-in-a-chain-with-other-smartport-devices)) |
| Q | back to ProDOS. Only when the A2let was started from ProDOS (`CORE4SD.SYSTEM`); in the boot menu `Q` is not shown, `B` takes its place |
| ? or H | a window in the middle of the screen with the version, the Apple model, these keys and what `-` and `*` in the list mean (`Help` at the right of the key line at the bottom); any key closes it and puts the screen back as it was |

In the list, files that are not disk images are marked with `-`. Directories end in `/` and show `<DIR>`.

**Caution:** swapping the image in the drive the running ProDOS was started from and then going back to ProDOS with `Q` is unsafe. After a change in drive 1, the way out is always a reboot with `B`.

### Boot order of the Apple II models

The Apple boots the Core4SD only if it is the first device on the SmartPort ([TECHNICAL.md](TECHNICAL.md#core4sd-in-a-chain-with-other-smartport-devices)). The exception is the IIc Plus: if its internal 3.5" drive is empty, it boots the Core4SD. What the Apple starts at power-up:

| Model | At power-up | Core4SD |
|---|---|---|
| Apple II (original Monitor ROM) | no autostart: the Monitor prompt `*` | with a Liron card: `PR#n` (n = slot of the card) or `Cn00G` in the Monitor |
| Apple II+, II with Autostart ROM, IIe unenhanced | slots 7 → 1, the first card with `$Cn01`=`$20`, `$Cn03`=`$00`, `$Cn05`=`$03` **and `$Cn07`=`$3C`** (the Disk II id); none: Applesoft | a SmartPort card (Liron) has `$Cn07`=`$00` and is skipped: `PR#n` |
| IIe enhanced | slots 7 → 1, the first card with `$Cn01`=`$20`, `$Cn03`=`$00`, `$Cn05`=`$03`; none: Applesoft | boots by itself when its card is the highest bootable slot, else `PR#n`. A card in slot 3 is not seen while the IIe's 80-column firmware is mapped there |
| IIc ROM 0 (`PRINT PEEK(64447)` gives 0) | the internal 5.25" drive (slot 6); with no disk in it, after a few seconds the SmartPort on the rear disk port (slot 5) | boots by itself when the internal drive is empty, else `PR#5` |
| IIc ROM 3, 4, 4X (`PRINT PEEK(64447)` gives 3 or 4) | the RAM card (slot 4, if it holds a bootable disk), then as ROM 0: the internal 5.25" drive (slot 6), then the SmartPort (slot 5). Nothing bootable: ROM 4X drops to Applesoft | boots by itself when the internal drive is empty, else `PR#5` |
| IIc Plus (`PRINT PEEK(64447)` gives 5) | slot 4 (the RAM card, if it holds a bootable disk), then slot 5: the internal 3.5" drive (unit 1), then the next SmartPort unit; then the external 5.25" drive (slot 6) | boots by itself when the internal 3.5" drive is empty: as slot 5, drive 2 (`X` = `$D0`) |
| IIgs | the **Startup Slot** of the Control Panel; *Scan*: slots 7 → 1 | slot 5 must be set to *Smart Port* in the Control Panel; then startup slot 5, or *Scan* with no other bootable slot above it |

The power-up scan of the II+, IIe, IIc ROM 0 and IIc Plus was checked in their ROMs (the IIc ROM 0 gives up on an empty internal drive and jumps to `$C500`), and the IIc ROM 0 case in the POM2 emulator; the order of the IIc ROMs 3, 4 and 4X is taken from the [ROM 4X](https://github.com/mgcaret/rom4x) documentation (ROM 4X boots as ROM 4, apart from the drop to Applesoft; ROM 3 is assumed to boot as ROM 4). The original IIc ROM (`PRINT PEEK(64447)` gives 255) has no SmartPort: with an empty internal drive it stops with `Check Disk Drive.`; see [Limits](#limits-and-known-points). `PR#n` and `Cn00G` boot slot n on every model. The A2let itself looks for the Core4SD from slot 7 down to 1, and `Tab` goes on to the next one.

### Serial monitor while connected to the Apple

The firmware prints its messages on the serial port (230400 baud). How much it prints is set by `LOG_LEVEL` in `src/config.h`:

| `LOG_LEVEL` | Output |
|---|---|
| `LVL_NONE` | nothing |
| `LVL_ERROR` | errors, short lines starting with `E` (e.g. `E READ d1 b5 $27 IOERROR`; all of them are listed in [TECHNICAL.md](TECHNICAL.md#error-messages-in-the-serial-log)) |
| `LVL_INFO` | also the version, the mounted drives (`1 /PRODOS~2.PO 280 P`: drive, 8.3 path, blocks, `D` for DOS or `P` for ProDOS order, then the offset of block 0 in the file if not 0, e.g. 64 for a `.2MG`, and `*` for a write-protected image, as in the A2let list) and the free RAM (`f: 419 s: 183`: free memory and the smallest stack reserve, in bytes) |
| `LVL_DEBUG` (default) | also the bus: `R` for a bus reset, `I 1 81` for each INIT (drive and the id it got, in hex), `NO $81 $80` for every packet for another device (its id and command), and `?` (no line break) for each garbled packet, which the Apple simply sends again |

To read them while the adapter runs on the Apple:

- **No USB power.** The Nano is powered by the Apple; USB and Apple power must never meet. Use a USB cable without its +5 V wire: open the jacket over 3–4 cm, cut the red wire (VBUS) and insulate both ends; black (GND), white (D−) and green (D+) stay connected. Check with a multimeter (no continuity on VBUS, continuity on the other three) and label the cable. A ready-made "USB data only" / "power blocker" adapter does the same.
- **Or** a USB serial adapter with only **GND** and **RX** connected to GND and D1 (TX) of the Nano, its +5 V left unconnected.
- **Open the monitor before switching on the Apple.** Opening the port sets DTR, which resets the Nano; during Apple access that would restart it. PlatformIO can open the port without DTR/RTS, though some USB chips (CH340) still pulse DTR:
  ```
  pio device monitor -b 230400 --dtr 0 --rts 0
  ```
- **Android phone or tablet:** with a USB OTG adapter and an app such as "Serial USB Terminal" (Kai Morich; knows the CH340, CP210x and FTDI chips of the Nano boards), set to 230400 baud. The phone supplies +5 V over OTG as well, so the cable without VBUS is needed here too; DTR and RTS can be switched off in the app.

## Limits and known points

- **Tested on a German Apple IIc with ROM 4X, also booting from a floppy, and on an Apple IIgs with ROM 01 (ROM 3 only in the GSSquared emulator).** Other models and SmartPort cards (e.g. a IIe or II+ with a Liron card) have only been checked in the POM2 emulator.
- **The IIc needs a ROM with SmartPort support.** The original IIc ROM (version 255) has no SmartPort on its disk port, so the Core4SD does not work with it. The later original ROMs 0 (the "UniDisk 3.5" ROM), 3 and 4 have SmartPort, so it should work with them as well; it has only been tried with ROM 4X. The version is shown in Applesoft by `PRINT PEEK(64447)` (address `$FBBF`): 255 original, 0 UniDisk 3.5, 3 memory expansion, 4 revised memory expansion, 5 IIc Plus.
- Long file names are read up to 91 characters and shortened to 28. Longer names are shown as their 8.3 name, because SdFat 2 does not cut names itself and the Nano has no RAM for a 255-byte buffer.
- At most 960 entries per directory are shown (64 pages), the directory depth is limited to 8, and the stored 8.3 path to 127 characters.
- The SD card must not be swapped while running. After a card is inserted, `S` reads it again.
- Nibble images (`.NIB`, `.WOZ`) are not supported: SmartPort only knows blocks.
- **DOS 3.3 disks mount, but do not run.** DOS 3.3 and its boot sector use the Disk II hardware and its ROM, and ProDOS cannot read such a disk. The same goes for other disks whose boot code needs a Disk II, such as many games, CP/M and Apple Pascal; see [What runs](#supported-disk-image-formats). A `.DSK` or `.DO` holding a ProDOS volume works. `tools/imgcheck.py` shows what an image holds.
- Lower case is shown on a IIe, IIc and IIgs. On an Apple II or II+ (e.g. with a Liron card) the character ROM has no lower case, so the A2let shows all names and texts in upper case there (it tells the models apart by the monitor ROM id at `$FBB3`).
- With other SmartPort devices in the same chain (in front of or behind the Core4SD), see [TECHNICAL.md](TECHNICAL.md#core4sd-in-a-chain-with-other-smartport-devices): a device in front takes unit 1 and the boot, a device behind stays unused.
- **Booting from the internal floppy drive:** ProDOS sees the Core4SD drives after about 5 seconds. On this path the IIc ROM sends no INIT; it resets the SmartPort bus and sends INIT only after a command has gone unanswered a few thousand times. Tested with ROM 4X.
- **IIc Plus: the internal 3.5" drive is unit 1**, so the Core4SD drives 1–4 are units 2–5 and the Apple boots the Core4SD as slot 5, drive 2 (`X` = `$D0`).
- **IIc Plus ROM bug: SmartPort unit 5 does not work.** As far as known the IIc Plus had only one ROM version (5); it has this bug. Its SmartPort dispatcher takes the bus ID of units 1–5 from a table at `$CE14`+unit−1 in the RAM of the MIG (Multidrive Interface Glue: a custom chip of the IIc Plus that connects its drives to the disk controller, with a small RAM the firmware uses as work space) and computes units 6 and up as unit − 1, but its INIT scan only fills the table up to unit 4: the entry for unit 5 (`$CE18`) is never written. Unit 5 then answers `$28` "no device" without asking the bus, or, depending on what the MIG RAM held at power-on, reaches a wrong device. With the Core4SD alone this hits **drive 4**: the IIc Plus reports 5 devices (internal drive + 4), unit 5 fails, and drive 4 is only reachable as unit 6, so tools that list units 1 to the device count (e.g. SPInfo) do not show it. With one device in front of the Core4SD it hits drive 3 instead. Booting, the A2let (which sends all its calls to the first unit named `CORE4SD`) and ProDOS 8 (at most four units per SmartPort) are not affected.

## Origin

The bus routines `packet_16mhz.S` and the handshake and reset sequence come from SmartportCFA/SmartportSD by Robert Justice and Andrea Ottaviani, continued by Katherine Stark (FAT version: Eric Helgeson, github.com/erichelgeson/smartportsd). SmartportSD is published under the MIT License (Copyright (c) 2020-2023 Katherine Stark, Robert Justice, Andrea Ottaviani). For Core4SD `packet_16mhz.S` was improved: bounded waits instead of endless loops (receiving and sending), a buffer size limit, and receiving without acknowledging, so that a packet is only acknowledged once it is known to be for Core4SD; the bit timing is unchanged (details in the file's header). The rest (firmware logic, protocol, A2let, boot block, tests) is newly written.

## License

Core4SD is free software under the GNU General Public License, version 3 or (at your option) any later version: it may be used, changed and passed on, and changed versions that are passed on must stay under the same license, with their source. The file [LICENSE](LICENSE) has the copyright notice, the MIT License of the SmartportSD code Core4SD contains, and notes on the libraries in the built firmware (SdFat: MIT, Arduino AVR core: LGPL 2.1); [COPYING](COPYING) has the full text of the GPL. These notices have to be passed on with every copy, the ready-made files in `bin/` included. Versions released before the change to the GPL (2026-10-30) remain available under the MIT License.

## More

[QUICK.md](QUICK.md) takes beginners from a new device to a booting Apple IIc, IIc Plus, IIgs, or IIe or II+ with a Liron card. Wiring, the SmartPort protocol, and the layout, build and tests are described in [TECHNICAL.md](TECHNICAL.md).