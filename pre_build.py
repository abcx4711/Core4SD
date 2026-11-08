# pre_build.py -- PlatformIO pre-script: builds the Apple II side before the
# firmware is compiled (what apple/Makefile does, without make):
#
#   apple/browser.asm  -> apple/CORE4SD.SYSTEM   (64tass)
#   apple/boot.asm     -> apple/boot.bin         (64tass, NBLK = browser blocks)
#   boot.bin + CORE4SD.SYSTEM -> src/bootimg.h   (tools/mkbootimg.py)
#   boot.bin + CORE4SD.SYSTEM -> apple/CORE4SD.PO (tools/mkpo.py)
#   apple/CORE4SD.PO          -> apple/CORE4SD.HDV (the same image; some
#                                emulators take a 140K .po for a 5.25"
#                                disk, a .hdv for a hard drive)
#   boot.bin + CORE4SD.SYSTEM -> apple/CORE4SD-800.PO (mkpo.py, 800K for a
#                                3.5" drive)
#
# src/bootimg.h is only rewritten when its content changes, so an unchanged
# browser does not force main.cpp to be recompiled.

import os
import shutil
import subprocess
import sys
from SCons.Script import Import  # pyright: ignore[reportMissingImports]

Import("env")

# 64tass: the file named in the environment variable TASS, else 64tass from
# PATH. To fix the file here instead, remove the # from the second line below
# and enter its full path; that line then takes priority over both.
TASS_PATH = os.environ.get("TASS", "")
# TASS_PATH = r"C:/path/to/64tass.exe"
TASS_FLAGS = ["-C", "-Wall", "--nostart"]

project_dir = env.get("PROJECT_DIR")  # pyright: ignore[reportUndefinedVariable]
apple_dir = os.path.join(project_dir, "apple")
tools_dir = os.path.join(project_dir, "tools")
bootimg_h = os.path.join(project_dir, "src", "bootimg.h")
python = env.subst("$PYTHONEXE")  # pyright: ignore[reportUndefinedVariable]


def fail(msg):
    print("ERROR: " + msg)
    sys.exit(1)


def find_tass():
    if TASS_PATH:
        if not os.path.isfile(TASS_PATH):
            fail("64tass not found: TASS_PATH is %s" % TASS_PATH)
        return TASS_PATH
    tass = shutil.which("64tass")
    if tass:
        return tass
    fail("64tass not found: put it in PATH, name the file in the environment variable TASS, or enter it as TASS_PATH in pre_build.py")


def run(cmd, what):
    try:
        return subprocess.run(cmd, check=True, cwd=apple_dir, stdout=subprocess.PIPE).stdout
    except subprocess.CalledProcessError as e:
        fail("%s failed (exit code %d)" % (what, e.returncode))


def build_apple():
    tass = find_tass()
    browser = os.path.join(apple_dir, "CORE4SD.SYSTEM")
    boot = os.path.join(apple_dir, "boot.bin")

    print(">>> [Apple] 64tass browser.asm -> CORE4SD.SYSTEM")
    run([tass] + TASS_FLAGS + ["-L", "browser.lst", "-o", "CORE4SD.SYSTEM", "browser.asm"], "64tass browser.asm")

    nblk = (os.path.getsize(browser) + 511) // 512
    print(">>> [Apple] 64tass boot.asm -> boot.bin (NBLK=%d)" % nblk)
    run([tass] + TASS_FLAGS + ["-D", "NBLK=%d" % nblk, "-L", "boot.lst", "-o", "boot.bin", "boot.asm"],
        "64tass boot.asm")

    header = run([python, os.path.join(tools_dir, "mkbootimg.py"), boot, browser], "mkbootimg.py")
    header = header.replace(b"\r\n", b"\n")
    old = open(bootimg_h, "rb").read().replace(b"\r\n", b"\n") if os.path.isfile(bootimg_h) else None
    if header != old:
        with open(bootimg_h, "wb") as f:
            f.write(header)
        print(">>> [Apple] src/bootimg.h updated")
    else:
        print(">>> [Apple] src/bootimg.h unchanged")

    po = os.path.join(apple_dir, "CORE4SD.PO")
    run([python, os.path.join(tools_dir, "mkpo.py"), boot, browser, po], "mkpo.py")
    shutil.copyfile(po, os.path.join(apple_dir, "CORE4SD.HDV"))
    po800 = os.path.join(apple_dir, "CORE4SD-800.PO")
    run([python, os.path.join(tools_dir, "mkpo.py"), "--blocks", "1600", boot, browser, po800], "mkpo.py 800K")
    print(">>> [Apple] apple/CORE4SD.PO, CORE4SD.HDV and CORE4SD-800.PO written")


build_apple()
