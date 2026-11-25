#!/usr/bin/env python3
"""Build skydrop's release files into dist/.

    python3 tools/build_release.py [--screenshots FOLDER] [--out dist]

Makes, for the version in skydrop/__init__.py:

    skydrop-VERSION-linux.tar.gz      Linux: unpack and run ./install.sh
    skydrop_VERSION_all.deb           Debian, Ubuntu, Mint: sudo apt install ./skydrop_VERSION_all.deb
                                      (built only where dpkg-deb is installed)
    skydrop-VERSION-macos.zip         macOS: unzip and run bash install.sh
    skydrop-VERSION-windows.zip       Windows 10 and 11: unzip and double-click install.cmd
    skydrop-VERSION-screenshots.zip   with --screenshots: the PNGs in that folder
    SHA256SUMS.txt                    checksums of all of the above

Nothing outside the repository is read except Pillow (to draw the .deb's menu icon).
"""

import argparse
import gzip
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = ("README.md", "LICENSE", "CHANGELOG.md")
UNIX_SCRIPTS = ("install.sh", "uninstall.sh")
WINDOWS_SCRIPTS = ("install.cmd", "install.ps1", "uninstall.cmd", "uninstall.ps1")

RUN_PY = '''"""Starts skydrop from its install folder (keeps the current folder out of sys.path)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from skydrop.cli import main  # noqa: E402

sys.exit(main())
'''


def version():
    with open(os.path.join(ROOT, "skydrop", "__init__.py"), encoding="utf-8") as fh:
        return re.search(r'^VERSION = "([^"]+)"', fh.read(), re.M).group(1)


def package_files(fonts=True):
    """(path in the repository, path in the package) for the program's files."""
    out = []
    base = os.path.join(ROOT, "skydrop")
    for folder, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__" and (fonts or d != "fonts"))
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")) or name.startswith("."):
                continue
            full = os.path.join(folder, name)
            out.append((full, os.path.relpath(full, ROOT).replace(os.sep, "/")))
    return out


def read(path, crlf=False):
    with open(path, "rb") as fh:
        data = fh.read()
    if crlf:
        data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return data


def release_time():
    """One timestamp for every file (SOURCE_DATE_EPOCH when set, so builds can repeat)."""
    return int(os.environ.get("SOURCE_DATE_EPOCH") or time.time())


# ---------------------------------------------------------------------------- archives
def unix_entries(top):
    entries = []
    for name in UNIX_SCRIPTS:
        entries.append((f"{top}/{name}", read(os.path.join(ROOT, name)), 0o755))
    for name in DOCS:
        entries.append((f"{top}/{name}", read(os.path.join(ROOT, name)), 0o644))
    for full, rel in package_files():
        entries.append((f"{top}/{rel}", read(full), 0o644))
    return entries


def windows_entries(top):
    entries = []
    for name in WINDOWS_SCRIPTS:
        entries.append((f"{top}/{name}", read(os.path.join(ROOT, name), crlf=True), 0o644))
    for name in DOCS:
        entries.append((f"{top}/{name}", read(os.path.join(ROOT, name)), 0o644))
    for full, rel in package_files():
        entries.append((f"{top}/{rel}", read(full), 0o644))
    return entries


def write_tar(path, entries, mtime):
    # the gzip header carries a time too: the release's, so the same tree builds the same file
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=int(mtime)) as gz, \
            tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
        made = set()
        for name, data, mode in entries:
            parts = name.split("/")
            for i in range(1, len(parts)):
                d = "/".join(parts[:i])
                if d not in made:
                    made.add(d)
                    info = tarfile.TarInfo(d)
                    info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, mtime
                    info.uid = info.gid = 0
                    info.uname = info.gname = "root"
                    tar.addfile(info)
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), mode, mtime
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))


def write_zip(path, entries, mtime):
    stamp = time.localtime(mtime)[:6]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        made = set()
        for name, data, mode in entries:
            parts = name.split("/")
            for i in range(1, len(parts)):
                d = "/".join(parts[:i]) + "/"
                if d not in made:
                    made.add(d)
                    zi = zipfile.ZipInfo(d, stamp)
                    zi.external_attr = (0o40755 << 16) | 0x10
                    zi.create_system = 3
                    zf.writestr(zi, b"")
            zi = zipfile.ZipInfo(name, stamp)
            zi.external_attr = ((0o100000 | mode) << 16)
            zi.create_system = 3                       # Unix: keeps the scripts executable
            zi.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zi, data)


# ---------------------------------------------------------------------------- the .deb
CONTROL = """Package: skydrop
Version: {version}
Section: utils
Priority: optional
Architecture: all
Maintainer: skydrop maintainers <maintainers@skydrop.invalid>
Installed-Size: {size}
Depends: python3 (>= 3.8), python3-pil, fonts-dejavu-core
Recommends: espeak-ng, pipewire-bin | pulseaudio-utils | alsa-utils, libnotify-bin, tzdata
Suggests: kitty
Description: retro Weather Channel style weather dashboard for the terminal
 skydrop brings the look of the 1990s Weather Channel "Local on the 8s" to the
 terminal, with serious weather tools: single-site NEXRAD radar, a live
 warnings crawl and county warnings map, a NOAA Weather Radio style tornado
 alarm for your ZIP code, a feed from the weather stations around you, and
 forecasts read aloud.
 .
 It draws real pictures in terminals that can show them (kitty, Ghostty,
 WezTerm, Konsole, foot and other sixel terminals) and a block-character
 version everywhere else. No account or API key is needed.
"""

POSTINST = """#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
  /usr/bin/python3 -m compileall -q /usr/lib/skydrop/skydrop >/dev/null 2>&1 || true
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
  fi
  if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
  fi
fi
exit 0
"""

PRERM = """#!/bin/sh
set -e
find /usr/lib/skydrop -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
exit 0
"""

LAUNCHER = """#!/bin/sh
# skydrop (the Debian package): the program lives in /usr/lib/skydrop
exec /usr/bin/python3 /usr/lib/skydrop/run.py "$@"
"""

DESKTOP = """[Desktop Entry]
Type=Application
Name=skydrop
GenericName=Terminal Weather
Comment=Retro Weather Channel style forecasts, NEXRAD radar, warnings and a tornado alarm
Exec=skydrop
Icon=skydrop
Terminal=true
Categories=Utility;
Keywords=weather;forecast;radar;terminal;
"""

COPYRIGHT = """Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: skydrop

Files: *
License: MIT
{license}
"""


def icon_png(size=256):
    sys.path.insert(0, ROOT)
    from skydrop import icons
    buf = io.BytesIO()
    icons.raindrop_icon(size).save(buf, "PNG")
    return buf.getvalue()


def build_deb(out_dir, ver):
    if not shutil.which("dpkg-deb"):
        print("  (no dpkg-deb here: skipping the .deb)")
        return None
    stage = tempfile.mkdtemp(prefix="skydrop-deb-")
    try:
        def put(rel, data, mode=0o644):
            path = os.path.join(stage, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)
            os.chmod(path, mode)
            return path

        files = []
        for full, rel in package_files(fonts=False):       # the system's DejaVu (fonts-dejavu-core)
            files.append(put("usr/lib/skydrop/" + rel, read(full)))
        files.append(put("usr/lib/skydrop/run.py", RUN_PY.encode()))
        files.append(put("usr/bin/skydrop", LAUNCHER.encode(), 0o755))
        files.append(put("usr/share/applications/skydrop.desktop", DESKTOP.encode()))
        files.append(put("usr/share/icons/hicolor/256x256/apps/skydrop.png", icon_png(256)))
        with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as fh:
            lic = "\n".join(" " + (line if line.strip() else ".") for line in fh.read().strip().splitlines())
        files.append(put("usr/share/doc/skydrop/copyright", COPYRIGHT.format(license=lic).encode()))
        files.append(put("usr/share/doc/skydrop/README.md.gz",
                         gzip.compress(read(os.path.join(ROOT, "README.md")), mtime=0)))
        size_kb = sum((os.path.getsize(f) + 1023) // 1024 for f in files)
        md5 = []
        for f in sorted(files):
            with open(f, "rb") as fh:
                md5.append(f"{hashlib.md5(fh.read()).hexdigest()}  {os.path.relpath(f, stage)}")
        put("DEBIAN/control", CONTROL.format(version=ver, size=size_kb).encode())
        put("DEBIAN/md5sums", ("\n".join(md5) + "\n").encode())
        put("DEBIAN/postinst", POSTINST.encode(), 0o755)
        put("DEBIAN/prerm", PRERM.encode(), 0o755)
        os.chmod(stage, 0o755)
        for folder, dirs, _files in os.walk(stage):
            for d in dirs:
                os.chmod(os.path.join(folder, d), 0o755)
        out = os.path.join(out_dir, f"skydrop_{ver}_all.deb")
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", stage, out], check=True,
                       stdout=subprocess.DEVNULL)
        return out
    finally:
        shutil.rmtree(stage, ignore_errors=True)


# ---------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Build skydrop's release files.")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"), help="where to put them (default: dist/)")
    ap.add_argument("--screenshots", metavar="FOLDER", help="also zip the PNG screenshots in FOLDER")
    args = ap.parse_args()
    ver = version()
    mtime = release_time()
    os.makedirs(args.out, exist_ok=True)
    made = []

    path = os.path.join(args.out, f"skydrop-{ver}-linux.tar.gz")
    write_tar(path, unix_entries(f"skydrop-{ver}"), mtime)
    made.append(path)

    deb = build_deb(args.out, ver)
    if deb:
        made.append(deb)

    path = os.path.join(args.out, f"skydrop-{ver}-macos.zip")
    write_zip(path, unix_entries(f"skydrop-{ver}-macos"), mtime)
    made.append(path)

    path = os.path.join(args.out, f"skydrop-{ver}-windows.zip")
    write_zip(path, windows_entries(f"skydrop-{ver}-windows"), mtime)
    made.append(path)

    if args.screenshots:
        shots = []
        for folder, _dirs, files in os.walk(args.screenshots):
            for name in sorted(files):
                if name.lower().endswith((".png", ".md", ".txt")):
                    full = os.path.join(folder, name)
                    rel = os.path.relpath(full, args.screenshots).replace(os.sep, "/")
                    shots.append((f"skydrop-{ver}-screenshots/{rel}", read(full), 0o644))
        path = os.path.join(args.out, f"skydrop-{ver}-screenshots.zip")
        write_zip(path, sorted(shots), mtime)
        made.append(path)

    sums = []
    for path in made:
        with open(path, "rb") as fh:
            sums.append(f"{hashlib.sha256(fh.read()).hexdigest()}  {os.path.basename(path)}")
    with open(os.path.join(args.out, "SHA256SUMS.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(sums) + "\n")
    for path in made + [os.path.join(args.out, "SHA256SUMS.txt")]:
        print(f"  {os.path.basename(path):40} {os.path.getsize(path) / 1024:8.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
