# post_build.py -- PlatformIO post-script: once firmware.hex has been built,
# the finished files are copied to bin/:
#
#   .pio/build/<env>/firmware.hex -> bin/core4sd_328p.hex (nano328p) or
#                                    bin/core4sd_328pb.hex (nano328pb)
#   apple/CORE4SD.SYSTEM          -> bin/CORE4SD.SYSTEM
#   apple/CORE4SD.PO              -> bin/CORE4SD.PO
#   apple/CORE4SD.HDV             -> bin/CORE4SD.HDV
#   apple/CORE4SD-800.PO          -> bin/CORE4SD-800.PO
#
# Then the disk images of SmartPortTools (next to this project, or the folder
# in the environment variable SMARTPORTTOOLS_DIR) are rebuilt with its
# build.py, so they carry the new bin/CORE4SD.SYSTEM. Without that folder
# this step is skipped; if it fails, the firmware build still counts.
#
# It runs only when the .hex is (re)built, so a failed build leaves bin/ as
# it was.

import os
import shutil
import subprocess
from SCons.Script import Import  # pyright: ignore[reportMissingImports]

Import("env")


def build_smartporttools(project_dir):
    tools_dir = os.environ.get("SMARTPORTTOOLS_DIR") or \
        os.path.join(os.path.dirname(project_dir), "SmartPortTools")
    script = os.path.join(tools_dir, "build.py")
    if not os.path.isfile(script):
        print(">>> [SmartPortTools] %s not found, disk images not updated" % script)
        return
    r = subprocess.run([env.subst("$PYTHONEXE"), script], cwd=tools_dir,  # pyright: ignore[reportUndefinedVariable]
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if r.returncode != 0:
        print(r.stdout)
        print(">>> [SmartPortTools] WARNING: build.py failed (exit code %d)" % r.returncode)
        return
    print(">>> [SmartPortTools] SPTOOLS and SPBOOT (.po, .HDV) rebuilt with CORE4SD.SYSTEM")


def copy_to_bin(source, target, env):
    project_dir = env.subst("$PROJECT_DIR")
    apple_dir = os.path.join(project_dir, "apple")
    hex_name = "core4sd_328pb.hex" if env.subst("$PIOENV") == "nano328pb" else "core4sd_328p.hex"
    files = [
        (str(target[0]), hex_name),
        (os.path.join(apple_dir, "CORE4SD.SYSTEM"), "CORE4SD.SYSTEM"),
        (os.path.join(apple_dir, "CORE4SD.PO"), "CORE4SD.PO"),
        (os.path.join(apple_dir, "CORE4SD.HDV"), "CORE4SD.HDV"),
        (os.path.join(apple_dir, "CORE4SD-800.PO"), "CORE4SD-800.PO"),
    ]
    bin_dir = os.path.join(project_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    for src, name in files:
        shutil.copyfile(src, os.path.join(bin_dir, name))
    print(">>> [bin] %s" % ", ".join(name for _, name in files))
    build_smartporttools(project_dir)


env.AddPostAction("$BUILD_DIR/${PROGNAME}.hex", copy_to_bin)  # pyright: ignore[reportUndefinedVariable]
