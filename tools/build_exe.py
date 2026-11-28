#!/usr/bin/env python3
"""Build skydrop.exe, the Windows app: skydrop in a window of its own.

    py -3.13 -m pip install --require-hashes --only-binary :all: -r tools/exe-requirements.txt
    py -3.13 tools/build_exe.py [--out dist]

Makes OUT/skydrop-VERSION-windows.exe with PyInstaller: one file that opens skydrop's
window and no console window, with the raindrop icon and skydrop's version on it, and
inside it Python, Tk, Pillow, tzdata, the fonts in skydrop/fonts and every one of
their licenses (skydrop.exe --licenses prints them). Double-clicked, it opens the
window; from a Command Prompt or PowerShell, skydrop's one-shot flags (--now,
--version, ...) print there as they do anywhere else.

It builds on Windows only: CI's "Windows app" job runs it, checks what it made with
tools/check_exe.py, and keeps it as the job's artifact. The build tools are needed
only here; skydrop itself still needs nothing but Python and Pillow.
"""

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENTRY = '''"""skydrop.exe starts here."""
import sys

from skydrop.cli import main

sys.exit(main())
'''

# Windows shows these in the file's Properties (Details) and in Task Manager
VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'skydrop'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'skydrop'),
      StringStruct('LegalCopyright', {copyright!r}),
      StringStruct('OriginalFilename', 'skydrop.exe'),
      StringStruct('ProductName', 'skydrop'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def version():
    with open(os.path.join(ROOT, "skydrop", "__init__.py"), encoding="utf-8") as fh:
        return re.search(r'^VERSION = "([^"]+)"', fh.read(), re.M).group(1)


def copyright_line():
    with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as fh:
        for line in fh:
            if line.strip().lower().startswith("copyright"):
                return line.strip()
    return ""


def version_info(ver):
    nums = tuple((list(int(n) for n in re.findall(r"\d+", ver)[:4]) + [0, 0, 0, 0])[:4])
    return VERSION_INFO.format(nums=nums, version=ver, copyright=copyright_line())


def third_party(base_prefix=None):
    """The licenses of what goes inside skydrop.exe besides skydrop, from this build's
    own copies: Python (whose Windows license covers the Tcl/Tk and the other libraries
    it comes with), Pillow (and the libraries in its wheel), tzdata, and PyInstaller,
    whose bootloader starts the .exe. `skydrop.exe --licenses` prints them."""
    import importlib.metadata as md
    parts = []

    def add(title, path):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read().strip()
        except OSError:
            return
        parts.append(f"{title}\n\n{text}\n")

    add(f"Python {platform.python_version()}", os.path.join(base_prefix or sys.base_prefix, "LICENSE.txt"))
    for name in ("pillow", "tzdata", "pyinstaller"):
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        for f in sorted(dist.files or [], key=str):
            if any(k in f.name.upper() for k in ("LICENSE", "COPYING", "NOTICE")):
                add(f"{dist.metadata['Name']} {dist.version} ({f.name})", f.locate())
    rule = "\n" + "=" * 78 + "\n\n"
    return rule.join(parts)


def pyinstaller_args(work, ver):
    """PyInstaller's command line for skydrop.exe (files it needs go in `work`)."""
    entry = os.path.join(work, "skydrop_exe.py")
    with open(entry, "w", encoding="utf-8") as fh:
        fh.write(ENTRY)
    vfile = os.path.join(work, "version.txt")
    with open(vfile, "w", encoding="utf-8") as fh:
        fh.write(version_info(ver))
    icon = os.path.join(work, "skydrop.ico")
    sys.path.insert(0, ROOT)
    from skydrop import icons
    icons.raindrop_icon(256).save(icon, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128),
                                              (256, 256)])
    fonts = os.path.join(ROOT, "skydrop", "fonts")
    notices = os.path.join(work, "THIRD-PARTY.txt")
    with open(notices, "w", encoding="utf-8") as fh:
        fh.write(third_party())
    license_txt = os.path.join(work, "LICENSE.txt")
    shutil.copyfile(os.path.join(ROOT, "LICENSE"), license_txt)
    return [entry,
            "--name", "skydrop",
            "--onefile",
            "--windowed",                    # a window program: no console window of its own
            "--noconfirm", "--clean",
            "--icon", icon,
            "--version-file", vfile,
            "--paths", ROOT,
            "--add-data", f"{fonts}{os.pathsep}skydrop/fonts",
            "--add-data", f"{license_txt}{os.pathsep}skydrop",      # skydrop's own license
            "--add-data", f"{notices}{os.pathsep}skydrop",          # and the others' (--licenses)
            "--hidden-import", "PIL.ImageTk",    # imported by name (skydrop.window)
            "--hidden-import", "PIL._tkinter_finder",    # ImageTk's link to Tk, imported from C
            "--hidden-import", "tzdata",
            "--collect-data", "tzdata",          # the time zones (Windows has no zone files)
            "--exclude-module", "pytest",
            "--distpath", os.path.join(work, "dist"),
            "--workpath", os.path.join(work, "build"),
            "--specpath", work]


def main():
    ap = argparse.ArgumentParser(description="Build skydrop.exe, the Windows app.")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"), help="where to put it (default: dist/)")
    args = ap.parse_args()
    if os.name != "nt":
        print("skydrop.exe builds on Windows only (CI's Windows app job builds it).", file=sys.stderr)
        return 2
    ver = version()
    work = tempfile.mkdtemp(prefix="skydrop-exe-")
    try:
        cmd = [sys.executable, "-m", "PyInstaller"] + pyinstaller_args(work, ver)
        env = dict(os.environ, PYTHONHASHSEED="0")       # the same build twice is the same inside
        subprocess.run(cmd, check=True, cwd=work, env=env)
        os.makedirs(args.out, exist_ok=True)
        out = os.path.join(args.out, f"skydrop-{ver}-windows.exe")
        shutil.copyfile(os.path.join(work, "dist", "skydrop.exe"), out)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"  {os.path.basename(out):40} {os.path.getsize(out) / 1024:8.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
