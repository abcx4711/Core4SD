# Builds the Apple II side with 64tass and regenerates the menu disk that is
# compiled into the firmware, plus CORE4SD.PO and CORE4SD.HDV.
#
#   make            CORE4SD.SYSTEM, boot.bin, ../src/bootimg.h, CORE4SD.PO,
#                   CORE4SD.HDV, CORE4SD-800.PO

TASS    = 64tass -C -Wall --nostart
BOOTIMG = ../src/bootimg.h

all: CORE4SD.SYSTEM boot.bin $(BOOTIMG) CORE4SD.PO CORE4SD.HDV CORE4SD-800.PO

CORE4SD.SYSTEM: browser.asm
	$(TASS) -L browser.lst -o $@ browser.asm

# NBLK = blocks the browser occupies on the menu disk
boot.bin: boot.asm CORE4SD.SYSTEM
	$(TASS) -D NBLK=$$(( ($$(wc -c < CORE4SD.SYSTEM) + 511) / 512 )) -L boot.lst -o $@ boot.asm

$(BOOTIMG): boot.bin CORE4SD.SYSTEM ../tools/mkbootimg.py
	python3 ../tools/mkbootimg.py boot.bin CORE4SD.SYSTEM > $@

CORE4SD.PO: boot.bin CORE4SD.SYSTEM ../tools/mkpo.py
	python3 ../tools/mkpo.py boot.bin CORE4SD.SYSTEM $@

# The same image as a hard drive image: some emulators take a
# 140K .po for a 5.25" disk.
CORE4SD.HDV: CORE4SD.PO
	cp CORE4SD.PO $@

# The same volume with 800K, for a 3.5" drive
CORE4SD-800.PO: boot.bin CORE4SD.SYSTEM ../tools/mkpo.py
	python3 ../tools/mkpo.py --blocks 1600 boot.bin CORE4SD.SYSTEM $@

clean:
	rm -f *.lst CORE4SD.SYSTEM boot.bin CORE4SD.PO CORE4SD.HDV CORE4SD-800.PO

.PHONY: all clean
