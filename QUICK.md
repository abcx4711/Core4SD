# Core4SD Quick Start

From a new device to a booting Apple IIc, IIc Plus, IIgs, or IIe or II+ with a Liron card. For the details and other Apple models, see [README.md](README.md).

**One rule protects your hardware: never connect USB and the Apple at the same time.** Both supply power to the Nano. Unplug the device from the Apple before you plug in USB, and unplug USB before you connect the Apple.

## What you need

- An SPIISD V1 (or compatible board), either assembled or as the [SP2SD DIY kit](https://github.com/kerokero5150/SP2SD_DIY_KIT). The SPIISD V2 with LCD is not supported.
- A USB cable that fits the Nano, and a PC to flash it.
- A microSD card and a way to write to it on the PC.
- One of these Apple II models:

| Apple | Requirement |
|---|---|
| IIc | A ROM with SmartPort. To check, type `PRINT PEEK(64447)` in Applesoft. **255** means the original ROM, which does not work. 0, 3 and 4 have SmartPort. |
| IIc Plus | None |
| IIgs | None |
| IIe | A Liron card (Apple II 3.5 Disk Controller Card) |
| II+ | A Liron card, and 64K of memory for ProDOS (a language card) |

## 1. Assemble

Skip this step if your board is ready-made.

Follow the kit's [assembly guide](https://github.com/kerokero5150/SP2SD_DIY_KIT/blob/main/SP2SD_DIY_INST.pdf). The mistakes that are hardest to fix:

- **Nano the wrong way round:** the USB connector goes on top. Its 30 soldered pins are hard to remove, so check before you solder.
- **Diode the wrong way round:** the line on the diode must match the line on the board, otherwise the device gets no +5 V.
- **ATmega168 Nano:** its memory is too small for the firmware. Buy a Nano with an ATmega328P or ATmega328PB.
- **Solder bridges:** check every joint before the first power-up.

## 2. Flash the firmware

You need [avrdude](https://github.com/avrdudes/avrdude) and the `bin` folder of this project. These steps are for Windows; for macOS and Linux, see [Flashing the firmware](README.md#1-flashing-the-firmware).

1. **Disconnect the device from the Apple**, then connect the Nano to the PC by USB.
2. Find the port in the Device Manager under "Ports (COM & LPT)", for example `COM6`. If no port appears, a clone with a CH340 USB chip may need its driver.
3. Read the marking on the Nano's chip, then open a command prompt in the project folder and run the matching command with your port:

   | Chip | Command |
   |---|---|
   | ATmega328P | `avrdude -p m328p -c arduino -P COM6 -b 115200 -U flash:w:bin\core4sd_328p.hex:i` |
   | ATmega328PB | `avrdude -p m328pb -c arduino -P COM6 -b 115200 -U flash:w:bin\core4sd_328pb.hex:i` |

4. If avrdude cannot reach the board, the Nano probably has the old bootloader: run the command again with `-b 57600`.
5. **Unplug the USB cable.**

## 3. Prepare the SD card

1. Format the card with FAT32. Cards of 64 GB and more come as exFAT, which the firmware cannot read.
2. Copy some disk images to the card: `.PO`, `.HDV`, `.2MG`, `.DSK` or `.DO`. Folders are fine. Choose images with **ProDOS**: DOS 3.3 disks need a Disk II and do not run on the device. `python tools/imgcheck.py IMAGE` shows what an image holds.
3. For names that stay stable, use short file names that differ in their first six characters, such as `ADT15DE.2MG`. [Tips for the SD card](README.md#tips-for-the-sd-card) explains why.
4. Insert the card into the device.

## 4. Connect it and start the Apple

Switch the Apple off before you connect the device. Connect it directly, with no other SmartPort drive in between: a drive in front of it takes the boot and the first unit. Then follow the section for your Apple. In each case, a new device has an empty drive 1, so a successful boot brings up the **A2let**, the menu for choosing images.

### Apple IIc

1. Connect the device to the **disk port on the back**.
2. Take any floppy out of the internal drive.
3. Switch the IIc on. The A2let appears after a few seconds.

### Apple IIc Plus

1. Connect the device to the **disk port on the back**.
2. Take any disk out of the internal 3.5" drive.
3. Switch the IIc Plus on. The A2let appears. Its title line shows `Units 2-5`, because the internal drive is unit 1.

### Apple IIgs

1. Connect the device to the **disk port on the back**, and take out any 3.5" disk.
2. Switch the IIgs on and open the Control Panel by pressing Ctrl-Open Apple-Esc.
3. Under *Slots*, set slot 5 to **Smart Port** and the startup slot to **5** or **Scan**. With *Scan*, the IIgs tries slots 7 to 1, so no other slot above 5 may hold a bootable disk.
4. Restart the IIgs. The A2let appears.

### Apple IIe with a Liron card

1. Put the Liron card into a free slot, but not slot 3: a IIe does not see a card there while its 80-column firmware is mapped in. Slot 5 is a good choice.
2. Connect the device to the **connector of the Liron card**.
3. Switch the IIe on. It tries the slots from 7 down to 1 and boots the first card it can:
   - **Enhanced IIe:** it boots the Liron card by itself if that is the highest bootable slot.
   - **Unenhanced IIe, or a Disk II card in a higher slot such as 6:** press Ctrl-Reset to stop the boot, then type `PR#5` in Applesoft, with the slot of your Liron card as the number.

### Apple II+ with a Liron card

1. Put the Liron card into a free slot. Slot 5 is a good choice.
2. Connect the device to the **connector of the Liron card**.
3. Switch the II+ on. It never boots a Liron card by itself, so you start the device by hand every time: press Reset to stop any floppy boot, then type `PR#5` in Applesoft, with the slot of your Liron card as the number.
4. The A2let appears, with all text in upper case, because the II+ has no lower-case characters.

## 5. Mount and boot an image

1. In the A2let, use ↑ and ↓ to select an image, or RETURN to open a folder. The II+ has no ↑ and ↓ keys: use Ctrl-K and Ctrl-J.
2. Press **1** to mount the image as drive 1.
3. Press **B** to boot it.

The device remembers its drives, so from now on the Apple boots drive 1 the same way it booted the A2let. On a II+ that means `PR#5` again.

## Getting back to the A2let

- **With the button:** hold it while you switch on the Apple.
- **Without a button:** create a file `CONFIG.TXT` in the root folder of the card with `3` in its first line. The A2let then comes up at every power-up.
- **When the Apple boots something else first:** type `PR#n` in Applesoft to boot the device, with n as its slot. On a IIc, IIc Plus and IIgs that is `PR#5`.

## If something goes wrong

| What you see | What to do |
|---|---|
| avrdude reports a device signature error | Use the command for the other chip. Nothing has been written. |
| avrdude cannot reach the board | Check the port, try `-b 57600`, and try another USB cable. |
| `Check Disk Drive.` on the IIc | The IIc has the original ROM 255, which has no SmartPort. |
| The IIc boots from the floppy | Remove the floppy, or type `PR#5`. |
| The IIc Plus boots from its internal drive | Take the disk out of the internal 3.5" drive. |
| The IIgs does not boot the device | Check slot 5 and the startup slot in the Control Panel. |
| The IIe or II+ boots another card or stops at the `]` prompt | Type `PR#n`, with n as the slot of the Liron card. |
| `No SD card` in the A2let | Check that the card is FAT16 or FAT32 and fully inserted, then press `S`. |
| A file is not in the list | `.TXT`, `.BAK`, `.ZIP`, hidden and system files are not listed. |
| The image does not boot or ProDOS finds no volume | It may be a DOS 3.3 disk, which does not run on the device; `tools/imgcheck.py` shows this. A 140K image in ProDOS order needs the extension `.po`, see [disk image formats](README.md#supported-disk-image-formats). |

For anything else, the device reports errors on its [serial port](README.md#serial-monitor-while-connected-to-the-apple).

Press `H` in the A2let to see all keys.
