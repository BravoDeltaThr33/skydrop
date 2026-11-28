#!/usr/bin/env python3
"""Check skydrop.exe, the Windows app, on Windows:

    python tools/check_exe.py EXE [VERSION]

The .exe passes when it is a window program (no console window of its own), says it
is VERSION (the version in skydrop/__init__.py when not given), finds its fonts, its
time zones and Tk inside itself, opens its window's terminal, draws skydrop's Now
screen with live weather and saves it as a PNG, prints the licenses it carries, and
takes the flags skydrop starts it with for sounds and the background watch. It runs with settings and a cache of
its own, in a new folder, so it changes nothing of the PC's. Needs nothing but
Python. CI runs it on the .exe it builds, and on the one in release/.
"""

import os
import re
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CELL = (10, 22)                    # JetBrains Mono at 12 points, 96 dpi
SIZE = (110, 40)                   # the window's first size, in cells


def repo_version():
    with open(os.path.join(ROOT, "skydrop", "__init__.py"), encoding="utf-8") as fh:
        return re.search(r'^VERSION = "([^"]+)"', fh.read(), re.M).group(1)


def subsystem(path):
    """The PE file's subsystem: 2 for a window program, 3 for a console one."""
    with open(path, "rb") as fh:
        head = fh.read(4096)
    if head[:2] != b"MZ":
        return None
    pe = struct.unpack_from("<I", head, 0x3C)[0]
    if head[pe:pe + 4] != b"PE\0\0":
        return None
    return struct.unpack_from("<H", head, pe + 24 + 68)[0]


def png_facts(path):
    """(width, height, how many different pixel values the first rows hold) of a PNG
    written by Pillow (8-bit RGB or RGBA, any filters)."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, idat, width = 8, b"", 0
    while pos < len(data):
        n, kind = struct.unpack_from(">I4s", data, pos)
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            width, height, depth, ctype = struct.unpack_from(">IIBB", body)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    channels = {2: 3, 6: 4}.get(ctype)
    if depth != 8 or channels is None:
        return width, height, 0
    raw = zlib.decompress(idat)
    stride = width * channels
    seen, prev = set(), bytearray(stride)
    for y in range(min(height, 200)):
        line = raw[y * (stride + 1):(y + 1) * (stride + 1)]
        ftype, row = line[0], bytearray(line[1:])
        for i in range(stride):
            a = row[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if ftype == 1:
                row[i] = (row[i] + a) & 0xFF
            elif ftype == 2:
                row[i] = (row[i] + b) & 0xFF
            elif ftype == 3:
                row[i] = (row[i] + (a + b) // 2) & 0xFF
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                row[i] = (row[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        seen.update(bytes(row[i:i + channels]) for i in range(0, stride, channels * 7))
        prev = row
    return width, height, len(seen)


def run(exe, args, env, timeout=120):
    try:
        done = subprocess.run([exe] + args, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "", "timed out"
    return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")


def problems(exe, version):
    """What's wrong with the .exe: an empty list when nothing is."""
    found = []
    kind = subsystem(exe)
    if kind != 2:
        found.append(f"it isn't a window program (PE subsystem {kind}): it would open a console window")
    home = tempfile.mkdtemp(prefix="skydrop-check-")
    env = dict(os.environ)
    for var, sub in (("APPDATA", "Roaming"), ("LOCALAPPDATA", "Local")):
        env[var] = os.path.join(home, sub)
        os.makedirs(env[var], exist_ok=True)

    rc, out, err = run(exe, ["--version"], env)
    if rc != 0 or out.strip() != f"skydrop {version}":
        found.append(f"--version said {out.strip()!r} (exit {rc}) {err.strip()}")

    rc, out, err = run(exe, ["--probe"], env)
    print(out.rstrip())
    if rc != 0:
        found.append(f"--probe failed (exit {rc}): {err.strip()}")
    for want in ("JetBrains Mono 12 pt, 10x22 px cells", "Zones:     found", "Window:    Tk 8.6"):
        if want not in out:
            found.append(f"--probe didn't say {want!r}")
    if os.path.join("skydrop", "fonts", "JetBrainsMono-Regular.ttf") not in out:
        found.append("--probe didn't find JetBrains Mono inside the .exe")

    shot = os.path.join(home, "now.png")
    rc, out, err = run(exe, ["--snapshot", shot, "--zip", "75201", "--screen", "1"], env, timeout=180)
    if rc != 0:
        found.append(f"--snapshot failed (exit {rc}): {err.strip()[-2000:]}")
    facts = png_facts(shot) if os.path.exists(shot) else None
    want = (SIZE[0] * CELL[0], SIZE[1] * CELL[1])
    if facts is None:
        found.append("--snapshot saved no PNG")
    else:
        print(f"Snapshot:  {facts[0]}x{facts[1]} px, {facts[2]} colors in its first rows")
        if facts[:2] != want:
            found.append(f"the snapshot is {facts[0]}x{facts[1]} px, not {want[0]}x{want[1]}")
        if facts[2] < 8:
            found.append("the snapshot is all but blank")

    rc, out, err = run(exe, ["--play-wav", os.path.join(home, "none.wav")], env, timeout=60)
    if rc not in (0, 1):
        found.append(f"--play-wav ended with {rc}: {err.strip()}")

    rc, out, err = run(exe, ["--licenses"], env)
    for want in ("Copyright (c) 2026 BravoDeltaThr33", "Ghostty contributors", "SIL OPEN FONT LICENSE",
                 "Bitstream", "PYTHON SOFTWARE FOUNDATION LICENSE", "Pillow", "Apache License", "PyInstaller"):
        if want not in out:
            found.append(f"--licenses didn't print {want!r}")

    rc, out, err = run(exe, ["--watch-stop"], env)
    if rc != 0 or "background watch" not in out:
        found.append(f"--watch-stop said {out.strip()!r} (exit {rc}) {err.strip()}")
    log = os.path.join(env["LOCALAPPDATA"], "skydrop", "cache", "skydrop.log")
    if os.path.exists(log):
        with open(log, encoding="utf-8", errors="replace") as fh:
            found.append("skydrop logged a problem:\n" + fh.read()[-3000:])
    return found


def main():
    if len(sys.argv) not in (2, 3):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    exe = sys.argv[1]
    version = sys.argv[2] if len(sys.argv) == 3 else repo_version()
    if not os.path.isfile(exe):
        print(f"no such file: {exe}", file=sys.stderr)
        return 2
    print(f"{exe}: {os.path.getsize(exe) / 1024 / 1024:.1f} MB")
    found = problems(exe, version)
    for p in found:
        print(f"PROBLEM: {p}")
    print("skydrop.exe: " + ("OK" if not found else f"{len(found)} problem(s)"))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
