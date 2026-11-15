"""Command line entry point: the full-screen app or one-shot printouts."""

import argparse
import json
import locale
import os
import re
import shutil
import sys
import time
import traceback

from . import APP, VERSION, gfx
from .canvas import ImageRegistry
from .config import load_config, run_only, save_config
from .fmt import Fmt
from .model import build_model
from .net import prune_disk_cache
from .paths import LOG_PATH, ensure, CACHE_DIR
from .settings import FIELD_BY_KEY, parse_field
from .store import SOURCE_LABELS, DataStore, check_connections
from .text import padto
from .theme import THEMES, Theme

ONE_SHOT = [("--now", "now", "print current conditions"), ("--hourly", "hourly", "print the hourly forecast"),
            ("--daily", "daily", "print the daily forecast with details"), ("--radar", "radar", "print the radar map (text)"),
            ("--stations", "stations", "print the live station feed"), ("--alerts", "alerts", "print alerts and outlooks"),
            ("--air", "air", "print air quality"), ("--sky", "sky", "print sun, moon and almanac"),
            ("--office", "office", "print NWS office products and the weather radio script")]


class PrintView:
    """What screens need when printing to stdout instead of the full-screen app."""

    def __init__(self, cfg, store, fmt, mode):
        self.cfg = cfg
        self.store = store
        self.fmt = fmt
        self.sel = {"hourly": 0, "daily": 0, "stations": 0, "st_filter": "weather", "st_sort": "distance"}
        self.images = ImageRegistry()
        self.images.mode = mode
        self.gmode = mode
        self.theme = Theme(cfg.get("theme"), cfg.get("background"))
        # how the meters and boxes are drawn, as in the app
        from . import crt as C
        from .config import on
        from .text import set_rounded
        C.METER.update(style=C.meter_style(cfg), ascii=mode == "ascii", blocks=C.legacy_blocks(cfg))
        set_rounded(on(cfg, "rounded_borders"))
        self.radar = None
        self.radar_state = {"span": int(cfg.get("radar_span") or 120), "lat": None, "lon": None, "frame": None,
                            "layer": cfg.get("radar_layer") or "reflectivity", "labels": True, "temps": True,
                            "warnings": True}
        self.printing = True
        self.speech = None
        self.last_radar = None
        self.cell = (10, 20)

    @property
    def ascii(self):
        return self.gmode == "ascii"


def to_ansi(line, theme, color, mode="256"):
    if not color:
        return "".join(t for t, _ in line).rstrip()
    from .canvas import Renderer

    class _T:
        def write(self, s):
            pass

        def flush(self):
            pass

    r = Renderer(_T(), theme, mode, "blocks")
    out = []
    for text, style in line:
        if isinstance(style, tuple) and style and style[0] == "@img":
            out.append(text)
            continue
        out.append(r.sgr(style) + text)
    out.append("\x1b[0m")
    return "".join(out)


def model_json(m):
    out = dict(m)
    out["tz"] = str(m.get("tz"))
    out["grid"] = {k: v for k, v in (m.get("grid") or {}).items() if k == "updated"}
    out["om"] = {"current": (m.get("om") or {}).get("current")}
    st = dict(m.get("stations") or {})
    out["stations"] = st
    return out


def one_shot(args, cfg, store):
    from .app import SCREENS
    from .radar import RadarManager
    store.refresh_sync()
    m = build_model(store, cfg)
    if args.raw:
        with store.lock:
            raw = {k: e["data"] for k, e in store.entries.items()}
        json.dump(raw, sys.stdout, indent=2, default=str)
        print()
        return 0
    if args.json:
        json.dump(model_json(m), sys.stdout, indent=2, default=str)
        print()
        return 0
    color = sys.stdout.isatty() and not args.no_color and "NO_COLOR" not in os.environ
    mode = "blocks" if color and (args.graphics in (None, "auto", "blocks", "kitty", "sixel")) else "ascii"
    if args.graphics == "ascii":
        mode = "ascii"
    F = Fmt(args.units or cfg.get("units"), m.get("tz"))
    view = PrintView(cfg, store, F, mode)
    if args.once == "radar" and gfx.HAVE_PIL:
        view.radar = RadarManager(store)
    width = args.width or min(shutil.get_terminal_size((110, 24)).columns, 160)
    idx = next(i for i, s in enumerate(SCREENS) if s[0] == args.once)
    name, fn = SCREENS[idx][1], SCREENS[idx][3]
    if args.once == "radar" and view.radar is not None:
        from .ui import radar_view
        v0 = radar_view(view, m, min(width, 100), 30)
        view.radar.get(v0, m)
        t0 = time.time()
        while view.radar.busy() and time.time() - t0 < 40:
            time.sleep(0.2)
    loc = m.get("loc") or {}
    theme = Theme(cfg.get("theme"), cfg.get("background"))
    cmode = "truecolor" if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit") else "256"
    head = [(f" {name}   {loc.get('name', '')} {loc.get('zip', '')} ".ljust(width), "head")]
    r = fn(m, view, width, 60, printing=True)
    for line in [head, []] + r["lines"]:
        if color:
            line = padto(line, width)
        print(to_ansi(line, theme, color, cmode))
    for k, e in m["errors"].items():
        print(f"{APP}: {SOURCE_LABELS.get(k, k)}: {e}", file=sys.stderr)
    return 0


def speak_once(cfg, store, what, units=None):
    from .scripts import SCRIPTS
    from .speech import Speaker
    if what not in SCRIPTS:
        print(f"{APP}: --speak takes one of: {', '.join(SCRIPTS)}", file=sys.stderr)
        return 2
    store.refresh_sync()
    m = build_model(store, cfg)
    F = Fmt(units or cfg.get("units"), m.get("tz"))
    view = PrintView(cfg, store, F, "ascii")
    title, text = SCRIPTS[what](m, view)
    sp = Speaker(cfg)
    print(f"{title}: {sp.describe()}")
    if not sp.speak(title, text):
        print(f"{APP}: can't speak: {sp.error}", file=sys.stderr)
        return 1
    try:
        last = -1
        while sp.active:
            if sp.index != last and sp.index >= 0:
                print("  " + sp.current())
                last = sp.index
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        sp.shutdown()
    return 0


def get_voice(cfg, name=None):
    """Download a Piper voice into ~/.local/share/skydrop/voices (used by install.sh)."""
    from .speech import VOICE_CHOICES, Speaker, voice_path
    sp = Speaker(cfg)
    if name and not re.fullmatch(r"[a-z]{2}_[A-Z]{2}-[A-Za-z0-9_]+-(x_low|low|medium|high)", name.strip()):
        print(f"{APP}: '{name}' doesn't look like a Piper voice name (e.g. {VOICE_CHOICES[0]})", file=sys.stderr)
        return 2
    chosen = bool(name and name.strip())
    name = name.strip() if chosen else sp.voice_name()
    path = voice_path(name)

    def use_it():
        if chosen and cfg.get("voice") != name:
            cfg["voice"] = name
            try:
                save_config(cfg)
            except OSError:
                pass

    if os.path.exists(path) and os.path.exists(path + ".json"):
        print(f"Voice {name} is already installed.")
        use_it()
        return 0
    state = {"pct": -1}

    def progress(done, total):
        if total:
            pct = int(done * 100 / total)
            if pct >= state["pct"] + 5 or pct == 100:
                state["pct"] = pct
                print(f"\r  downloading {name}: {pct:3d}% of {total / 1048576:.0f} MB", end="", flush=True)

    ok = sp.download_voice(name, wait=True, progress=progress)
    print()
    if not ok:
        print(f"{APP}: voice download failed: {(sp.download or ('', 0, 0, 'unknown error'))[3]}", file=sys.stderr)
        return 1
    print(f"Voice {name} is ready.")
    use_it()
    return 0


def voice_check(cfg):
    """Make Piper say one word into memory (install.sh runs this in a separate process, so a
    computer whose CPU can't run it falls back to another engine instead of crashing the app)."""
    import io
    import wave
    from .speech import Speaker, _piper_available, voice_path
    sp = Speaker(cfg)
    if not _piper_available():
        print("Piper isn't installed")
        return 1
    path = voice_path(sp.voice_name())
    if not os.path.exists(path):
        print(f"voice {sp.voice_name()} isn't downloaded")
        return 1
    from piper import PiperVoice
    voice = PiperVoice.load(path)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        if hasattr(voice, "synthesize_wav"):      # piper-tts 1.3+
            voice.synthesize_wav("Weather.", wf)
        else:
            voice.synthesize("Weather.", wf)
    if buf.tell() < 1000:
        print("Piper produced no audio")
        return 1
    print("Piper works")
    return 0


def _set_crt(cfg, key, raw):
    """--set for the CRT settings: crt, crt_phosphor, crt_font, crt_font_size (the chosen font's), meter_style,
    meter_blocks and crt.EFFECT=0-10."""
    from . import crt as C
    if key == "crt_meter":                                  # its 3.2.0 name
        key, raw = "meter_style", {"segments": "led"}.get(raw, raw)
    if key == "crt":
        if raw.lower() not in ("on", "off", "true", "false"):
            return "must be on or off"
        cfg["crt"] = raw.lower() in ("on", "true")
        return None
    if key == "crt_phosphor":
        raw = "color" if raw.lower() == "colour" else raw.lower()
        if raw not in C.ORDER:
            return "must be one of: " + ", ".join(C.ORDER)
        cfg[key] = raw
        return None
    if key == "meter_style":
        raw = "shade" if raw.lower() == "shaded" else raw.lower()
        if raw not in C.METER_ORDER:
            return "must be one of: " + ", ".join(C.METER_ORDER)
        cfg[key] = raw
        return None
    if key == "meter_blocks":
        if raw.lower() not in C.METER_BLOCKS:
            return "must be one of: " + ", ".join(C.METER_BLOCKS)
        cfg[key] = raw.lower()
        return None
    if key == "crt_font_size":
        font = C.font_of(cfg)
        if raw.lower() in ("standard", "default"):
            size = C.FONT_SIZES[font][0]
        elif raw.lower() in ("own", "terminal") and font == "own":
            size = None
        else:
            try:
                size = float(raw)
            except ValueError:
                return "a size in points, such as 12 or 18.75 (or standard)"
            if not C.SIZE_RANGE[0] <= size <= C.SIZE_RANGE[1]:
                return f"from {C.SIZE_RANGE[0]:g} to {C.SIZE_RANGE[1]:g} points"
        cfg["crt_font_sizes"] = C.font_sizes_saved(cfg, font, size)
        return None
    if key == "crt_font":
        pick = C.FONT_OLD.get(raw, raw.lower())
        if pick not in C.FONT_KEYS:
            return "must be one of: " + ", ".join(C.FONT_KEYS)
        cfg[key] = pick
        return None
    fx = C.OLD_EFFECT_KEYS.get(key[4:], key[4:])
    levels = C.levels_of(cfg)
    if fx not in C.EFFECT_KEYS:
        return "unknown effect; one of: " + ", ".join(C.EFFECT_KEYS)
    if not raw.isdigit() or not 0 <= int(raw) <= 10:
        return "a level from 0 (off) to 10 (5 is the standard)"
    levels[fx] = int(raw)
    cfg["crt_levels"] = C.levels_saved(levels)
    return None


def set_options(cfg, pairs):
    """--set key=value (used by install.sh)."""
    for pair in pairs:
        key, _, raw = pair.partition("=")
        if key.strip() in ("crt", "crt_phosphor", "crt_meter", "meter_style", "meter_blocks", "crt_font",
                           "crt_font_size") or \
                key.strip().startswith("crt."):
            err = _set_crt(cfg, key.strip(), raw.strip())
            if err:
                print(f"{APP}: {key.strip()}: {err}", file=sys.stderr)
                return 2
            continue
        item = FIELD_BY_KEY.get(key.strip())
        if item is None:
            print(f"{APP}: unknown setting '{key}'", file=sys.stderr)
            return 2
        if item[3].startswith("choice:") and raw.strip() not in item[3].split(":", 1)[1].split(","):
            print(f"{APP}: {key} must be one of: {item[3].split(':', 1)[1].replace(',', ', ')}", file=sys.stderr)
            return 2
        val, err = parse_field(item, raw)
        if err:
            print(f"{APP}: {key}: {err}", file=sys.stderr)
            return 2
        cfg[key.strip()] = val
    save_config(cfg)
    return 0


def write_icon(path, size=256):
    """Draw the app icon, a raindrop, as a PNG or ICO (the desktop menu entry, the Windows shortcuts)."""
    if not gfx.HAVE_PIL:
        print(f"{APP}: Pillow is needed to draw the icon", file=sys.stderr)
        return 1
    from . import icons
    img = icons.raindrop_icon(size)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img.save(path)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog=APP, description="Retro Weather Channel style weather for the terminal "
                                                       "(NWS, Open-Meteo, NOAA radar, live stations, read-aloud).")
    ap.set_defaults(once=None)
    modes = ap.add_mutually_exclusive_group()
    for flag, key, helptext in ONE_SHOT:
        modes.add_argument(flag, dest="once", action="store_const", const=key, help=helptext)
    modes.add_argument("--history", dest="once", action="store_const", const="stations", help=argparse.SUPPRESS)
    modes.add_argument("--json", action="store_true", help="print the merged data as JSON")
    modes.add_argument("--raw", action="store_true", help="print the raw API responses as JSON")
    modes.add_argument("--check", action="store_true", help="test every enabled data source and exit")
    modes.add_argument("--probe", action="store_true", help="show what your terminal supports and exit")
    modes.add_argument("--speak", nargs="?", const="now", metavar="SCREEN",
                       help="read a forecast aloud and exit (now, hourly, daily, radar, alerts, office)")
    modes.add_argument("--get-voice", nargs="?", const="", metavar="VOICE",
                       help="download a Piper voice and use it (default: the one chosen in Settings), then exit")
    modes.add_argument("--watch", action="store_true",
                       help="watch for tornado warnings in the background, without a window (Settings > "
                            "Watch when closed starts this for you at login)")
    modes.add_argument("--watch-stop", action="store_true", help="stop the background watch")
    modes.add_argument("--test-alarm", action="store_true", help="play the tornado alarm as it will sound, then exit")
    modes.add_argument("--watch-sync", action="store_true", help=argparse.SUPPRESS)
    modes.add_argument("--write-icon", metavar="FILE", help=argparse.SUPPRESS)
    modes.add_argument("--voice-check", action="store_true", help=argparse.SUPPRESS)
    modes.add_argument("--set", nargs="+", metavar="KEY=VALUE", help=argparse.SUPPRESS)
    ap.add_argument("--zip", metavar="PLACE", help="ZIP code or 'City, ST' for this run only (not saved)")
    ap.add_argument("--screen", type=int, default=1, help="start on screen N (1-9, 0 for Settings)")
    ap.add_argument("--local8s", action="store_true", help="start in Local on the 8s presentation mode")
    ap.add_argument("--units", choices=["imperial", "metric"], help="units for this run only")
    ap.add_argument("--graphics", choices=["auto", "kitty", "sixel", "blocks", "ascii"], help="graphics for this run only")
    ap.add_argument("--theme", choices=list(THEMES), help="theme for this run only: " + ", ".join(THEMES))
    ap.add_argument("--crt", nargs="?", const="on", choices=["on", "off", "color", "green", "amber", "white"],
                    help="CRT mode for this run only: on, off, or a phosphor (color, green, amber, white)")
    ap.add_argument("--width", type=int, help="output width for one-shot modes")
    ap.add_argument("--no-color", action="store_true", help="plain one-shot output; the app runs without the CRT effect")
    ap.add_argument("--version", action="version", version=f"{APP} {VERSION}")
    args = ap.parse_args(argv)

    cfg = load_config()
    persist = True
    if args.graphics:
        run_only(cfg, graphics=args.graphics)
    if args.theme:
        run_only(cfg, theme=args.theme)
    if args.crt:
        run_only(cfg, crt=args.crt != "off")
        if args.crt not in ("on", "off"):
            run_only(cfg, crt_phosphor=args.crt)
    if args.no_color:
        run_only(cfg, crt=False)                       # the CRT needs color
    store = DataStore(cfg)
    if args.zip:
        val, err = parse_field(FIELD_BY_KEY["zip"], args.zip)
        if err or not val:
            ap.error(err or "enter a ZIP code or place")
        cfg = dict(cfg, zip=val, lat="", lon="", nws_station="", location_name="")
        for k in ("_geo", "_nws_points", "_nws_station", "_rev", "_acis"):
            cfg.pop(k, None)
        persist = False
    if not persist:
        store = DataStore(cfg, persist=False)

    if args.watch:
        from . import watch
        return watch.run(cfg)
    if args.watch_stop:
        from . import watch
        pid = watch.stop_watcher()
        print(f"{APP}: stopped the background watch (process {pid})." if pid else
              f"{APP}: the background watch isn't running.")
        return 0
    if args.test_alarm:
        from . import watch
        return watch.test_alarm(cfg)
    if args.watch_sync:   # the installer: start or stop the watch (and its login entry) to match Settings
        from . import watch
        err = watch.sync(load_config())
        if err:
            print(f"{APP}: {err}", file=sys.stderr)
        return 1 if err else 0
    if args.write_icon:
        return write_icon(args.write_icon)
    if args.voice_check:
        return voice_check(cfg)
    if args.set:
        return set_options(load_config(), args.set)
    if args.get_voice is not None:
        return get_voice(cfg, args.get_voice or None)

    if args.check:
        loc = store._location(dict(cfg))
        if not loc:
            print(store.err("loc"))
            return 1
        print(f"Location: {loc['lat']:.4f}, {loc['lon']:.4f} ({loc['how']})")
        bad = 0
        for name, ok, msg in check_connections(cfg, loc["lat"], loc["lon"], store.usage):
            bad += 1 if ok is False else 0
            print(f"{'ok  ' if ok else 'off ' if ok is None else 'FAIL'}  {name}: {msg}")
        from .speech import Speaker
        print(f"info  Voice: {Speaker(cfg).describe()}")
        print(f"info  Pillow (pixel graphics): {'yes' if gfx.HAVE_PIL else 'no - radar and icons fall back to text'}")
        return 1 if bad else 0

    if args.speak:
        return speak_once(cfg, store, args.speak, args.units)

    if args.once is not None or args.json or args.raw:
        return one_shot(args, cfg, store)

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(f"{APP}: the full-screen app needs a terminal; try --now or --help", file=sys.stderr)
        return 2
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    from .app import SETTINGS_IDX, SCREENS, App
    from .local8s import Local8s
    from .term import open_terminal
    term = open_terminal()
    try:
        term.raw()
        term.probe()
    except Exception:
        term.cooked()
    if args.probe:
        term.cooked()
        c = term.caps
        cols, rows, xp, yp = term.size()
        print(f"Terminal:  {c.name}")
        print(f"Supports:  {c.describe()}")
        print(f"Size:      {cols}x{rows} cells" + (f", {xp}x{yp} px" if xp else ""))
        print(f"DA1:       {c.da1 or '(no reply)'}   XTVERSION: {c.xtversion or '(no reply)'}")
        print(f"Auto mode: {c.graphics_auto()} graphics, {c.color_auto()} color")
        print(f"Pillow:    {'available' if gfx.HAVE_PIL else 'missing (pip install pillow)'}")
        return 0
    start = SETTINGS_IDX if args.screen == 0 else min(max(args.screen, 1), len(SCREENS)) - 1
    app = None
    rc = 0
    try:
        prune_disk_cache()
        term.enter(mouse=str(cfg.get("mouse", "on")).lower() in ("on", "true", "1"))
        app = App(term, cfg, store, start, args.units)
        if args.local8s:
            app.local8s = Local8s(app)
        app.run()
    except KeyboardInterrupt:
        pass
    except Exception:
        term.leave()
        tb = traceback.format_exc()
        try:
            ensure(CACHE_DIR)
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(f"--- {time.ctime()} {APP} {VERSION}\n{tb}\n")
        except OSError:
            pass
        print(tb, file=sys.stderr)
        print(f"{APP} hit an unexpected problem; details were saved to {LOG_PATH}", file=sys.stderr)
        rc = 1
    finally:
        if app is not None:
            app.speech.shutdown()
        term.leave()
    # Downloads still in flight would otherwise hold the exit until they time out.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)
