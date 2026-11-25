#!/usr/bin/env python3
"""Take skydrop's screenshots: every screen, the themes, CRT mode and the drawing modes.

    python3 tools/screenshots.py [--out FOLDER] [--size 193x53] [--zip 75201] [--only NAMES]

It runs the real app, with live data for the ZIP code (Dallas, TX by default), inside a
test terminal (tests/harness.py, tests/gfxharness.py) that understands kitty and sixel
pictures, and saves each view as a PNG plus an index.png contact sheet and a README.txt.
The warnings-map picture shows a made-up severe weather day, and now-precipitation
made-up hours of rain turning to ice and snow; both say so.

Needs Linux with Pillow, pyte and the DejaVu Sans Mono font (fonts-dejavu-core).
"""

import argparse
import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from gfxharness import GfxTerm  # noqa: E402
from harness import run_app  # noqa: E402
from skydrop import icons  # noqa: E402

SCREENS = [("1", "now", "Now"), ("2", "hourly", "Hourly"), ("3", "ten-day", "10-Day"), ("4", "radar", "Radar"),
           ("5", "stations", "Stations"), ("6", "alerts", "Alerts"), ("7", "air", "Air & UV"),
           ("8", "sky", "Sky & Almanac"), ("9", "nws-radio", "NWS & Radio"), ("0", "settings", "Settings")]
WAIT = {"4": 9.0, "3": 3.0, "8": 3.0}          # seconds a screen gets before its picture is taken


def simulate_outbreak(app, term):
    """Color the counties around the location with a made-up set of watches and warnings."""
    m = app.model
    smap = m.get("state_map")
    if not smap:
        return []

    def middle(county):
        xs = [x for ring in county["rings"] for x, _y in ring]
        ys = [y for ring in county["rings"] for _x, y in ring]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    lat0, lon0 = m["loc"]["lat"], m["loc"]["lon"]

    def km(county):
        x, y = middle(county)
        return math.hypot((x - lon0) * 111 * math.cos(math.radians(lat0)), (y - lat0) * 111)

    near = sorted(smap["counties"], key=km)

    def same(counties):
        return ["0" + c["geoid"] for c in counties]

    m["state_alerts"] = [
        {"key": "demo.to.w", "event": "Tornado Warning", "rank": 1, "rgb": (230, 20, 30), "same": same(near[:2]), "ugc": []},
        {"key": "demo.sv.w", "event": "Severe Thunderstorm Warning", "rank": 4, "rgb": (255, 150, 0),
         "same": same(near[2:6]), "ugc": []},
        {"key": "demo.to.a", "event": "Tornado Watch", "rank": 8, "rgb": (255, 225, 0), "same": same(near[6:40]), "ugc": []},
        {"key": "demo.sv.a", "event": "Severe Thunderstorm Watch", "rank": 9, "rgb": (219, 112, 147),
         "same": same(near[40:70]), "ugc": []},
        {"key": "demo.ff.a", "event": "Flood Watch", "rank": 10, "rgb": (46, 139, 87), "same": same(near[70:95]), "ugc": []},
    ]
    app.cache.clear()
    return []


# made-up next 12 hours for the precipitation picture: rain turning to ice, then snow
DEMO_HOURS = [(4.0, 10, "Mostly Cloudy"), (3.2, 20, "Chance Light Rain"), (2.5, 40, "Chance Rain"),
              (1.8, 60, "Rain Likely"), (1.0, 80, "Rain"), (0.4, 90, "Rain And Sleet"), (0.0, 85, "Freezing Rain"),
              (-0.6, 80, "Freezing Rain"), (-1.2, 70, "Wintry Mix"), (-1.8, 60, "Snow"), (-2.3, 50, "Snow"),
              (-2.6, 35, "Light Snow"), (-2.8, 20, "Chance Light Snow")]


KEPT = {}


def simulate_precipitation(app, term):
    """Make the next 12 hours rain, then sleet and freezing rain, then snow, as it cools
    (restore_hours puts the real ones back)."""
    import copy
    import time
    KEPT["hourly"] = copy.deepcopy(app.model.get("hourly") or [])
    now = time.time()
    hours = [r for r in app.model.get("hourly") or [] if r.get("t") and r["t"] + 3600 > now]
    for r, (temp, pop, words) in zip(hours, DEMO_HOURS):
        r.update(temp=temp, pop=pop, nws_desc=words, desc=words, kind=icons.from_text(words))
        r.pop("wmo", None)
    app.cache.clear()
    return []


def restore_hours(app, term):
    if "hourly" in KEPT:
        app.model["hourly"] = KEPT.pop("hourly")
        app.cache.clear()
    return []


def change(settings, term_mode=None):
    """A script step: change settings as the Settings screen would (and the test
    terminal's picture protocol along with Graphics)."""
    def apply(app, term):
        app.cfg.update(settings)
        if term_mode:
            term.mode = term_mode
            term.caps.kitty = term_mode == "kitty"
            term.caps.sixel = term_mode == "sixel"
        app.apply_display()
        app.renderer.invalidate()
        return []
    return apply


def contact_sheet(out, names, labels, cols=4, width=420):
    tiles = []
    for name in names:
        path = os.path.join(out, name + ".png")
        if os.path.exists(path):
            im = Image.open(path).convert("RGB")
            h = round(im.height * width / im.width)
            tiles.append((name, im.resize((width, h), Image.LANCZOS)))
    if not tiles:
        return
    th = max(t.height for _n, t in tiles)
    pad, cap = 14, 22
    rows_n = math.ceil(len(tiles) / cols)
    sheet = Image.new("RGB", (pad + cols * (width + pad), pad + rows_n * (th + cap + pad)), (12, 14, 24))
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype(os.path.join(ROOT, "skydrop", "fonts", "DejaVuSans.ttf"), 13)
    except OSError:
        font = ImageFont.load_default()
    for i, (name, tile) in enumerate(tiles):
        x = pad + (i % cols) * (width + pad)
        y = pad + (i // cols) * (th + cap + pad)
        d.text((x, y), labels.get(name, name), font=font, fill=(225, 228, 238))
        sheet.paste(tile, (x, y + cap))
    sheet.save(os.path.join(out, "index.png"), optimize=True)


def main():
    ap = argparse.ArgumentParser(description="Take skydrop's screenshots.")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "screenshots"))
    ap.add_argument("--size", default="193x53", help="terminal size in cells (default 193x53)")
    ap.add_argument("--zip", default="75201", help="the place (default 75201, Dallas, TX)")
    ap.add_argument("--only", default="", help="comma-separated groups: screens,demo,themes,crt,modes,narrow")
    ap.add_argument("--home", help="the home folder the app runs in (its cache is reused between runs)")
    args = ap.parse_args()
    cols, rows = map(int, args.size.lower().split("x"))
    os.makedirs(args.out, exist_ok=True)
    home = args.home or tempfile.mkdtemp(prefix="skydrop-shots-home-")
    groups = set((args.only or "screens,demo,themes,crt,modes,narrow").split(","))
    names, labels = [], {}

    def add(name, label):
        names.append(name)
        labels[name] = label

    # One session for everything, so the forecast is downloaded once.
    script = []

    def press(key):
        script.append((0.3, key))

    def snap(name, label, wait=2.5):
        script.append((wait, f"SNAP:{name}"))
        add(name, label)

    if "screens" in groups:
        for key, name, label in SCREENS:
            press(key)
            snap(name, label, WAIT.get(key, 2.0))
        press("1")
        press("L")
        snap("local8s", "Local on the 8s (L)", 6.0)
        press("L")
    if "demo" in groups:
        press("6")
        script.append((1.5, simulate_outbreak))
        snap("warnings-map", "Alerts: warnings map (simulated outbreak)", 1.0)
    press("1")
    if "demo" in groups:
        script.append((0.5, simulate_precipitation))
        snap("now-precipitation", "Now: rain turning to ice and snow (simulated)", 2.5)
        script.append((0.3, restore_hours))
    if "themes" in groups:
        for theme in ("midnight", "retro8s", "star4000", "magnetic"):
            script.append((0.3, change({"theme": theme})))
            snap(f"theme-{theme}", f"Theme: {theme}")
        script.append((0.3, change({"theme": "weatherstar"})))
    if "crt" in groups:
        for phosphor in ("green", "amber", "color"):
            script.append((0.3, change({"crt": True, "crt_phosphor": phosphor})))
            snap(f"crt-{phosphor}", f"CRT mode: {phosphor} phosphor", 7.0)
        script.append((0.3, change({"crt": False})))
    if "narrow" in groups:
        script.append((0.5, "RESIZE:110x40"))
        snap("narrow-110x40", "A smaller window (110 x 40)", 3.0)
        script.append((0.5, f"RESIZE:{cols}x{rows}"))
    if "modes" in groups:
        for mode, label in (("sixel", "Sixel: Windows Terminal, iTerm2, foot"),
                            ("blocks", "Blocks: Tilix, GNOME Terminal, macOS Terminal"),
                            ("ascii", "Plain characters (ASCII)")):
            script.append((0.3, change({"graphics": mode}, mode)))
            snap(f"mode-{mode}", label, 3.0)

    base = {"graphics": "kitty", "zip": args.zip, "location_name": "", "refresh_seconds": 3600,
            "sources": {"owm": {"enabled": False}}}
    app, _term, errors = run_app(base, cols, rows, script, home=home, shots_dir=args.out,
                                 term=GfxTerm(cols, rows, "kitty"), wait_data=40)
    failed = app.store.errors()
    if failed:
        print("data sources that failed while the pictures were taken (they show in the status bar):",
              file=sys.stderr)
        for key, err in failed.items():
            print(f"  {key}: {err}", file=sys.stderr)
    for name in names:
        txt = os.path.join(args.out, name + ".txt")
        if os.path.exists(txt):
            os.remove(txt)
    if errors:
        print("\n".join(errors), file=sys.stderr)
    ok = not errors

    contact_sheet(args.out, names, labels)
    with open(os.path.join(args.out, "README.txt"), "w", encoding="utf-8") as fh:
        fh.write("skydrop screenshots\n\nLive weather for ZIP code %s, taken in a test terminal that draws the\n"
                 "pictures the way kitty and Ghostty do (the mode- pictures show the other drawing modes).\n"
                 "warnings-map.png shows a made-up severe weather day: the watches and warnings\n"
                 "in it are simulated. now-precipitation.png shows made-up hours of rain turning\n"
                 "to ice and snow, to show the Next 12 hours chart's colors.\n\n" % args.zip)
        for name in names:
            fh.write(f"{name}.png  {labels[name]}\n")
        fh.write("index.png  all of them on one sheet\n")
    print(("done: " if ok and not failed else "finished with errors: ") + args.out)
    return 0 if ok and not failed else 1


if __name__ == "__main__":
    sys.exit(main())
