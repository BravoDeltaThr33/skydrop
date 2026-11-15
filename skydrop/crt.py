"""CRT mode: an old phosphor screen (Settings, Display: CRT effect).

Two layers, the way the real thing has two. In any color terminal skydrop draws
what a screen of text can show: the phosphor's own colors (or the theme's, on
raised blacks), the power-on sweep and a self test, text typed out as down a
slow line, trails where text moves away, burn-in of the borders shown longest,
now and then a row that slips sideways out of sync, alerts that blink and
numbers that flash as fresh data changes them. Where the terminal shows pictures
(kitty, Ghostty) see-through pictures laid over the text add the glass:
scanlines and the aperture grille, the vignette, a glow round bright text with
red and blue fringes, grain, flicker, a slow hum bar, the face's curve, a glare
on the glass and a bezel round it.

Every effect has a level from 0 (off) to 10: 5 is the standard look, 10 twice as
strong.

The renderer hands each frame to CRT.process(), which returns the frame to draw:
the cells as the phosphor shows them with the text effects laid over them. After
the text, Glass writes the pictures of the glass.
"""

import base64
import math
import os
import random
import re
import subprocess
import sys
import threading
import time
import zlib

# ---------------------------------------------------------------------------- the tube
PHOSPHORS = {
    # the label, the raised black, the phosphor's color and what its brightest
    # glow washes out to; color keeps the theme's own colors
    "green": ("green (P1)", (6, 22, 12), (51, 255, 102), (205, 255, 215)),
    "amber": ("amber (P3)", (24, 13, 3), (255, 176, 0), (255, 232, 180)),
    "white": ("white (P4)", (14, 16, 19), (212, 222, 232), (255, 255, 255)),
    "color": ("color", (12, 16, 14), None, None),
}
ORDER = ("green", "amber", "white", "color")
PHOSPHOR_LABEL = {key: spec[0] for key, spec in PHOSPHORS.items()}
IMAGE_BASE = 900        # the glass's picture numbers (skydrop's own pictures start at 7000)
BOOT = (0.10, 0.30, 0.85)   # power-on: the dot, the line, the picture opening
TRAIL = (0.07, 0.14)        # a trail fades a step, then goes
TRAIL_MAX = 4000        # cells of trail at most (a whole screen changing)
BURN = 90.0             # seconds a border is on screen before it burns in
SLIP = (7.0, 19.0)      # seconds between rows slipping out of sync
HUM = 9.0               # seconds the hum bar takes to cross the screen
TICK = 0.1              # how often the glass's moving parts step - the hum bar,
                        # the grain's and the flicker's frames - all in one write
GRAIN_FRAMES = 5        # the grain's frames, one a step, round and round
CURVE = 0.022           # how far the tube bulges at the standard Curvature
TYPE = 0.9              # seconds the typewriter takes over a full screen
POST = 0.16             # seconds between the POST screen's lines
POST_LINES = 10         # the checks it runs
BLINK = 0.4             # an alert's half-blink, off and on
FLASH = (0.10, 0.16)    # an update flash: white, then bright, then back
GLOW_EVERY = 0.3        # the glow follows the screen at most this often
FAST_ROW = 0.6          # a row that changes again within this (the crawl) is not typed
# grain: two specks in three pixels clear, the rest faint, light or dark
GRAIN_ALPHA = bytes(0 if v < 170 else 3 + (v - 170) * 8 // 85 for v in range(256))
GRAIN_STANDARD = 0.4    # the share of that the standard grain draws
GRAIN_SHADE = bytes(255 if v & 1 else 0 for v in range(256))
# the flicker veil's darkness (of 255, at the standard), one a step, round and round
VEIL = (0, 0, 4, 0, 8, 0, 0, 3, 0, 0)
FLICKER = VEIL

# The effects the CRT effects card sets (Settings, c), each from 0 (off) to 10: 5 is
# the standard look, 10 twice as strong. (key, label, layer, what it is) - "text"
# effects work in any color terminal, "glass" ones need pictures (kitty, Ghostty).
EFFECTS = (
    ("blacks", "Raised blacks", "text",
     "The background is a lit tube's faint glow, not true black: how far it is raised."),
    ("power", "Power-on", "text",
     "A dot, a line across the middle, then the picture opening out from it, whenever CRT mode starts: how "
     "long it takes."),
    ("trails", "Persistence", "text",
     "Text that moves away leaves a trail that fades, and so does a moving cursor bar: how long it lasts and "
     "how bright it starts."),
    ("burn", "Burn-in", "text",
     "The tab bar and the borders shown longest leave a faint ghost wherever the screen is dark there: how "
     "soon, and how bright. 0 wipes it."),
    ("slips", "Sync slips", "text",
     "Now and then a row or two jumps a cell to the right and back: how often."),
    ("type", "Typewriter", "text",
     "New text arrives a character at a time, as down a slow serial line: how slowly. T types the screen out "
     "again."),
    ("post", "POST screen", "text",
     "A power-on self test before the first screen - the program, the display, the glass, the font, the "
     "config, the cache, the data sources and the network, checked line by line - when skydrop starts in CRT "
     "mode or CRT mode is switched on: how long it takes. T runs it now."),
    ("blink", "Blinking alerts", "text",
     "A new warning on the status line blinks until you press a key, and nothing else ever does: how fast. T "
     "shows a test alert."),
    ("flash", "Update flash", "text",
     "A number that changes when fresh data arrives - a temperature, the wind, a reading - flashes bright for "
     "a moment: how bright and how long. T flashes the numbers on the screen now."),
    ("scanlines", "Scanlines", "glass",
     "A thin dark gap between every few rows of pixels, as between the beam's lines: how dark."),
    ("grille", "Aperture grille", "glass",
     "The fine red, green and blue stripes of the tube's face, close up."),
    ("bloom", "Bloom", "glass",
     "The wide glow round bright text, meters and bars."),
    ("focus", "Soft focus", "glass",
     "A tight haze round every lit cell, the beam a little out of focus: it softens the text itself."),
    ("fringes", "Color fringes", "glass",
     "Red off the left edge of each word, blue off its right, as from misaligned color beams."),
    ("vignette", "Vignette", "glass",
     "The edges and corners darker than the middle."),
    ("grain", "Grain", "glass",
     "Faint moving grain, light and dark specks over the whole picture."),
    ("flicker", "Flicker", "glass",
     "A light, uneven flicker of the whole picture."),
    ("hum", "Hum bar", "glass",
     "A faint brighter band drifting down the screen every nine seconds: how bright."),
    ("curve", "Curvature", "glass",
     "The tube's face bulging out: the picture's edges and corners fall away round a curved rim, and the "
     "bezel's edges bow with it. The text and the scanlines stay straight; the light on the glass is Glare's, "
     "the row below."),
    ("glare", "Glare", "glass",
     "A soft oval of the room's light reflected on the glass, high on the left, whether the face is curved or "
     "flat: how bright."),
    ("bezel", "Bezel", "glass",
     "A dark frame round the picture, its opening's corners rounded and the tube set back behind it: how deep "
     "the rounding and the shadow. skydrop gives the frame its outer ring of cells (a row top and bottom, a "
     "column or two each side)."),
)
STANDARD = 5            # every effect's level as it comes
LEVEL_TOP = 10
EFFECT_KEYS = [key for key, *_ in EFFECTS]
EFFECT_INFO = {key: (label, layer, text) for key, label, layer, text in EFFECTS}
GLASS = [key for key, _label, layer, _text in EFFECTS if layer == "glass"]
# T on the CRT effects card: what it does on each effect that comes and goes
CARD_TRY = {"power": "replays it", "slips": "slips a row now", "burn": "wipes it",
            "type": "types the screen again", "post": "runs it now", "blink": "a test alert",
            "flash": "flashes the numbers now", "hum": "starts it at the top"}
# the names 3.2.0 used, read from older config files
OLD_EFFECT_KEYS = {"poweron": "power", "burnin": "burn", "typewriter": "type", "humbar": "hum", "curvature": "curve"}
SCALE = 2               # written with the levels: which scale they are on


def levels_of(cfg):
    """Every effect's level from config.json's "crt_levels": a whole number from 0
    to 10, the standard for one missing or unreadable (a config from before Glare
    had a row of its own has Glare at its Curvature level)."""
    raw = cfg.get("crt_levels") if isinstance(cfg, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key in EFFECT_KEYS:
        value = raw.get(key, STANDARD)
        if key == "glare" and key not in raw and "curve" in raw:
            value = raw["curve"]
        try:
            level = STANDARD if isinstance(value, bool) else int(round(float(value)))
        except (TypeError, ValueError, OverflowError):
            level = STANDARD
        out[key] = max(0, min(LEVEL_TOP, level))
    return out


def level_of(cfg, key):
    return levels_of(cfg)[key]


def levels_saved(levels):
    """Levels as config.json keeps them: every effect, and the scale."""
    out = {key: levels.get(key, STANDARD) for key in EFFECT_KEYS}
    out["scale"] = SCALE
    return out


def summary(cfg):
    """The CRT effects' levels in a few words, for the Settings row."""
    return summary_changed(cfg)[0]


def summary_changed(cfg):
    """(the levels in a few words, whether any is not the standard)."""
    levels = levels_of(cfg)
    off = sum(1 for level in levels.values() if level == 0)
    changed = sum(1 for level in levels.values() if level not in (0, STANDARD))
    if not off and not changed:
        return "all standard", False
    parts = ([f"{changed} changed"] if changed else []) + ([f"{off} off"] if off else [])
    return " · ".join(parts), True


def level_word(level):
    return {0: "off", STANDARD: "standard", LEVEL_TOP: "most"}.get(level, "")


def phosphor_of(cfg):
    name = str(cfg.get("crt_phosphor") or "green").lower()
    name = "color" if name == "colour" else name
    return name if name in PHOSPHORS else "green"


def CRT_ON(cfg):
    v = cfg.get("crt", False)
    return v if isinstance(v, bool) else str(v).strip().lower() in ("on", "true", "1", "yes")


def black_of(phosphor, amount):
    """A phosphor's raised black at a Raised blacks strength (1 the standard)."""
    spec = PHOSPHORS.get(phosphor) or PHOSPHORS["green"]
    return tuple(min(255, round(c * amount)) for c in spec[1])


def describe(key, level, phosphor="green"):
    """What an effect's level comes to, in a few words."""
    amount = level / STANDARD
    if not level:
        return {"blacks": "true black", "power": "no sweep: the picture is simply there",
                "trails": "no trails", "burn": "nothing burns in (and what had is wiped)",
                "slips": "rows never slip", "type": "text appears at once",
                "post": "no self test: straight to the first screen",
                "blink": "alerts hold still", "flash": "numbers change quietly",
                "curve": "a flat face", "glare": "no glare: the glass is clear",
                "bezel": "no frame: the picture to the edges"}.get(key, "off")
    if key == "type":
        return f"a full screen in about {TYPE * amount:.1f} s"
    if key == "post":
        return f"about {POST * amount * (POST_LINES + 4):.1f} s; any key skips it"
    if key == "blink":
        return f"{1 / (2 * BLINK / amount):.2g} blinks a second"
    if key == "flash":
        return f"{sum(FLASH) * amount:.2f} s of light"
    if key == "blacks":
        return "a black of #%02X%02X%02X" % black_of(phosphor, amount)
    if key == "power":
        return f"takes {BOOT[2] * amount:.2f} s; any key skips it"
    if key == "trails":
        return f"a trail lasts {sum(TRAIL) * amount:.2f} s"
    if key == "burn":
        wait = BURN / amount
        return "burns in after " + (f"{round(wait)} s" if wait < 120 else f"{wait / 60:.1f} minutes") + " on screen"
    if key == "slips":
        return f"about every {round(sum(SLIP) / 2 / amount)} s"
    strength = "the standard" if level == STANDARD else f"{amount:g}× the standard"
    if key == "hum":
        return f"every {HUM:g} s, {strength}"
    return strength


# ---------------------------------------------------------------------------- the phosphor's colors
def lum(rgb):
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255.0


def tint(rgb, phosphor, role=None, black=None):
    """A color as the phosphor shows it: its brightness on the phosphor's ramp, from
    the raised black up to the phosphor's color and, at the very top, towards white.
    Text (`role` "text") is never so dark the beam barely lights it, and a background
    that is not black (`role` "back": the cursor bar, a highlighted row) is never so
    dim it vanishes. In color: the color itself, with no black darker than the raised
    one."""
    spec = PHOSPHORS.get(phosphor) or PHOSPHORS["green"]
    black = spec[1] if black is None else black
    peak, white = spec[2], spec[3]
    if peak is None:
        return tuple(max(c, k) for c, k in zip(rgb, black))
    light = lum(rgb)
    if role == "text":
        light = 0.5 + 0.5 * light
    elif role == "back" and light > 0.02:
        light = 0.18 + 0.82 * light
    if light < 0.8:
        t = light / 0.8
        return tuple(round(k + (p - k) * t) for k, p in zip(black, peak))
    t = min(1.0, (light - 0.8) / 0.2)
    return tuple(round(p + (w - p) * t) for p, w in zip(peak, white))


def shade(phosphor, level, black=None):
    """The phosphor at a brightness from 0 to 1."""
    value = max(0, min(255, round(level * 255)))
    return tint((value, value, value), phosphor, None, black)


# ---------------------------------------------------------------------------- fonts
# Bitmap-style fonts for CRT mode: (key, label, the family to write in a terminal's
# config, the names fontconfig may list it under, where to get it). Only the terminal
# draws its font, so skydrop cannot switch it: it looks for the font among those
# installed and hands over the line that sets it. kitty draws no bitmap fonts at all,
# so a font that comes as a bitmap needs its TrueType copy there (FONT_KITTY).
def _apt(package, elsewhere):
    """Where to get a font: the Debian package on Debian-like Linux, else the project."""
    if sys.platform.startswith("linux") and os.path.exists("/usr/bin/apt-get"):
        return f"sudo apt install {package} (or {elsewhere})"
    return elsewhere


FONTS = (
    ("vt323", "VT323", "VT323", ("vt323",), "Google Fonts (fonts.google.com, VT323)"),
    ("glasstty", "Glass TTY VT220", "Glass TTY VT220", ("glass tty vt220",),
     _apt("fonts-glasstty", "github.com/svofski/glasstty")),
    ("ibm3270", "IBM 3270", "IBM 3270", ("ibm 3270", "3270"),
     _apt("fonts-3270", "github.com/rbanffy/3270font")),
    ("px437", "Px437 IBM VGA", "Px437 IBM VGA 8x16", ("px437 ibm vga", "pxplus ibm vga"),
     "the Ultimate Oldschool PC Font Pack (int10h.org/oldschool-pc-fonts)"),
    ("terminus", "Terminus", "Terminus", ("terminus",), _apt("fonts-terminus-otb", "files.ax86.net/terminus-ttf")),
    ("own", "the terminal's own", "", (), ""),
)
FONT_KITTY = {
    "terminus": ("Terminus (TTF)", ("terminus (ttf)",),
                 "its TrueType copy, Terminus (TTF), from files.ax86.net/terminus-ttf (kitty draws no bitmap "
                 "fonts)"),
}
FONT_INFO = {key: rest for key, *rest in FONTS}
FONT_KEYS = [key for key, *_ in FONTS]
# skydrop 3.2 and 3.3 kept the family's name
FONT_OLD = {"VT323": "vt323", "Glass TTY VT220": "glasstty", "IBM 3270": "ibm3270",
            "Px437 IBM VGA 8x16": "px437", "Terminus": "terminus"}
FONT_HELP = ("A bitmap-style font finishes the look - VT323, Glass TTY VT220, IBM 3270, Px437 IBM VGA or "
             "Terminus. In kitty skydrop sets it itself, with its size: it writes them into kitty.conf, in a block "
             "of its own at the end (the file is backed up first), and kitty reloads at once - every kitty window "
             "takes the font. The terminal's own takes the block out again. Elsewhere only the terminal can change "
             "its font: skydrop looks for it among the installed fonts, and enter copies the lines that set it, "
             "ready for the terminal's config.")
# Each font's size in points (Settings, Display: CRT font size): (the size it starts at,
# the sizes it draws pixel-sharp at on a 96-DPI screen). VT323 is drawn on 25 pixels to
# the em, Glass TTY VT220 on 20 and Px437 IBM VGA 8x16 on 16; Terminus (TTF) carries
# bitmaps for 12 to 32 pixels; IBM 3270 is drawn in smooth outlines.
FONT_SIZES = {
    "vt323": (18.75, (18.75, 37.5)),
    "glasstty": (15.0, (15.0, 30.0)),
    "ibm3270": (13.0, ()),
    "px437": (12.0, (12.0, 24.0, 36.0)),
    "terminus": (12.0, (9.0, 10.5, 12.0, 13.5, 15.0, 16.5, 18.0, 21.0, 24.0)),
    "own": (None, ()),
}
SIZE_RANGE = (6.0, 48.0)
FONT_SIZE_HELP = ("The CRT font's size, in points. In kitty skydrop sets it with the font, and kitty reloads at once. "
                  "← → step half a point, stopping at the sizes where the font draws pixel-sharp (marked sharp: "
                  "on a screen without scaling, each of the font's pixels lands on whole screen pixels); enter goes "
                  "back to the size the font starts at. With the terminal's own font it starts at the terminal's own "
                  "size, which skydrop leaves alone until you step it. Each font keeps its own size.")
FONT_FAMILIES = {"names": None, "asked": False}     # fc-list's answer, read once
TERMINAL_FONT = {"at": -60.0, "kind": None, "found": (None, None)}
TERMINAL = {"name": ""}                             # what the terminal answered (XTVERSION), set by the app


def font_key(name):
    name = FONT_OLD.get(str(name or ""), str(name or "own")).lower()
    return name if name in FONT_INFO else "own"


def font_size_of(cfg, key=None):
    """The CRT font's size in points (None: the terminal's own, for the terminal's own font)."""
    key = font_of(cfg) if key is None else font_key(key)
    sizes = cfg.get("crt_font_sizes") if isinstance(cfg, dict) else None
    sizes = sizes if isinstance(sizes, dict) else {}
    standard = FONT_SIZES[key][0]
    if key not in sizes:
        return standard
    value = sizes[key]
    if value is None:
        return None if key == "own" else standard
    try:
        value = float(value)
    except (TypeError, ValueError):
        return standard
    return max(SIZE_RANGE[0], min(SIZE_RANGE[1], value))


def font_sizes_saved(cfg, key, size):
    """crt_font_sizes as config.json keeps it, with one font's size changed."""
    sizes = cfg.get("crt_font_sizes") if isinstance(cfg.get("crt_font_sizes"), dict) else {}
    out = {k: v for k, v in sizes.items() if k in FONT_SIZES}
    out[font_key(key)] = size
    return out


def size_steps(key):
    """The sizes ← → step through: every half point, and the font's sharp sizes."""
    low, high = SIZE_RANGE
    steps = {n / 2 for n in range(int(low * 2), int(high * 2) + 1)} | set(FONT_SIZES[font_key(key)][1])
    return sorted(steps)


def step_size(key, size, step, start=11.0):
    """The next size up (step > 0) or down from `size` (None, the terminal's own: from
    `start`, the terminal's own size)."""
    steps = size_steps(key)
    here = start if size is None else size
    if step > 0:
        return next((s for s in steps if s > here + 1e-6), steps[-1])
    return next((s for s in reversed(steps) if s < here - 1e-6), steps[0])


def size_label(size):
    if size is None:
        return "the terminal's own"
    text = f"{float(size):.2f}".rstrip("0").rstrip(".")
    return f"{text} pt"


def size_sharp(key, size):
    return size is not None and any(abs(size - s) < 1e-6 for s in FONT_SIZES[font_key(key)][1])


def font_of(cfg):
    return font_key(cfg.get("crt_font"))


def font_label(key):
    return FONT_INFO[font_key(key)][0]


def installed_font_families():
    """{family in lower case: as fontconfig spells it} for the fonts installed, asked
    once in the background; None while it is still being asked, False where there is
    no fc-list to ask."""
    if not FONT_FAMILIES["asked"]:
        FONT_FAMILIES["asked"] = True

        def ask():
            try:
                out = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True,
                                     timeout=10).stdout
            except (OSError, subprocess.SubprocessError, ValueError):
                # no fontconfig (macOS, Windows): read the families from the font files
                from .osdeps import font_families
                try:
                    FONT_FAMILIES["names"] = font_families() or False
                except Exception:
                    FONT_FAMILIES["names"] = False
                return
            names = {}
            for line in out.splitlines():
                for part in line.split(","):
                    part = part.strip().replace("\\-", "-")
                    if part:
                        names.setdefault(part.lower(), part)
            FONT_FAMILIES["names"] = names or False

        try:
            threading.Thread(target=ask, daemon=True).start()
        except RuntimeError:
            FONT_FAMILIES["names"] = False
    return FONT_FAMILIES["names"]


def terminal_kind():
    """"kitty", "ghostty" or "" (another terminal): from what the terminal answered
    when skydrop started, and its environment."""
    name = str(TERMINAL.get("name") or "").lower()
    term = os.environ.get("TERM", "")
    if "ghostty" in name or "ghostty" in term or os.environ.get("TERM_PROGRAM", "").lower() == "ghostty":
        return "ghostty"
    if "kitty" in name or term == "xterm-kitty" or os.environ.get("KITTY_WINDOW_ID"):
        return "kitty"
    return ""


def font_family(key, kind=None):
    """(the family to set, the names it may be installed under, where to get it) for
    one of FONTS in this terminal."""
    key = font_key(key)
    _, family, names, where = FONT_INFO[key]
    kind = terminal_kind() if kind is None else kind
    if kind == "kitty" and key in FONT_KITTY:
        family, names, where = FONT_KITTY[key]
    return family, names, where


def font_installed(key, kind=None):
    """The family as installed (fontconfig's spelling) when the font is, False when it
    is not, None when nobody can say (fontconfig still being asked, or not here; or the
    terminal's own font)."""
    family, names, _ = font_family(key, kind)
    if not family:
        return None
    found = installed_font_families()
    if not found:
        return None
    matches = sorted((name for name in found if any(name.startswith(w) for w in names)),
                     key=lambda name: (len(name), name))
    return found[matches[0]] if matches else False


def home_path(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path and path.startswith(home + os.sep) else path


def terminal_config_path(kind):
    """The terminal's config file: the first that exists, else where it would go; None
    for a terminal skydrop does not know."""
    home = os.path.expanduser("~")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    if kind == "kitty":
        folder = os.environ.get("KITTY_CONFIG_DIRECTORY") or os.path.join(base, "kitty")
        return os.path.join(folder, "kitty.conf")
    if kind == "ghostty":
        places = [os.path.join(base, "ghostty", "config.ghostty"), os.path.join(base, "ghostty", "config")]
        if sys.platform == "darwin":
            mac = os.path.join(home, "Library", "Application Support", "com.mitchellh.ghostty")
            places += [os.path.join(mac, "config.ghostty"), os.path.join(mac, "config")]
        return next((path for path in places if os.path.isfile(path)), places[1])
    return None


def terminal_font(kind=None):
    """(the font family the terminal's config file sets, or None; that file, or None):
    the file is only read, and again at most every few seconds."""
    kind = terminal_kind() if kind is None else kind
    now = time.monotonic()
    if TERMINAL_FONT["kind"] == kind and now - TERMINAL_FONT["at"] < 5:
        return TERMINAL_FONT["found"]
    path, family = terminal_config_path(kind), None
    text = ""
    if path:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read(500_000)
        except OSError:
            text = ""
    for raw in text.splitlines():
        line = raw.strip()
        if kind == "kitty":
            part = line.split(None, 1)
            if len(part) == 2 and part[0] == "font_family":
                value = part[1].strip()
                named = re.search(r"""family=(?:"([^"]+)"|'([^']+)'|(\S+))""", value)
                family = (named.group(1) or named.group(2) or named.group(3)) if named else value
        else:
            found = re.match(r"font-family\s*=\s*(.*)$", line)
            if found:
                value = found.group(1).strip()
                if len(value) >= 2 and value[0] == value[-1] == '"':
                    value = value[1:-1]
                if not value:
                    family = None           # an empty one starts the list again
                elif family is None:
                    family = value          # the first is the font; the rest fall back
    TERMINAL_FONT.update(at=now, kind=kind, found=(family, path))
    return family, path


def font_config_line(family, kind=None):
    """(the line that sets the font, the file it goes in, how to load it) for this
    terminal."""
    kind = terminal_kind() if kind is None else kind
    path = terminal_config_path(kind)
    where = home_path(path) if path else "your terminal's font setting"
    mac = sys.platform == "darwin"
    if kind == "ghostty":
        return (f'font-family = "{family}"', where, ("cmd+shift+," if mac else "ctrl+shift+,") + " reloads it")
    if kind == "kitty":
        return (f"font_family {family}", where, ("ctrl+cmd+," if mac else "ctrl+shift+F5") + " reloads it")
    return family, where, ""


def font_word(key, kind=None):
    """A few words on a font here: installed, not installed, in use..."""
    key = font_key(key)
    current, _ = terminal_font(kind)
    if key == "own":
        return current or ""
    family, names, _ = font_family(key, kind)
    if current and any(current.lower().startswith(want) for want in names):
        return "in use"
    state = font_installed(key, kind)
    if state:
        return "installed"
    if state is False:
        return "not installed"
    return "looking…" if installed_font_families() is None else "not checked here"


def font_advice(key, kind=None, size=None):
    """What the CRT effects card says about a font, for this terminal (at `size`)."""
    kind = terminal_kind() if kind is None else kind
    key = font_key(key)
    current, path = terminal_font(kind)
    name = os.path.basename(path) if path else "the terminal's config"
    direct, why = kitty_direct(kind)
    if key == "own":
        if direct:
            return ("skydrop takes its font out of kitty.conf: kitty goes back to its own" +
                    (f" ({current})." if current else ".") + (f" Its size: {size_label(size)}." if size else ""))
        return (f"skydrop leaves the font alone: {name} sets {current}." if current else
                "skydrop leaves the terminal's font alone.")
    family, names, where = font_family(key, kind)
    state = font_installed(key, kind)
    lines = font_lines(key, size, kind)
    _, place, reload = font_config_line(state or family, kind)
    parts = []
    if current and any(current.lower().startswith(want) for want in names):
        parts.append(f"In use: {name} sets {current}.")
    elif state is False:
        parts.append(f"Not installed here - get it: {where}.")
    elif state is None and installed_font_families() is False:
        parts.append(f"skydrop cannot see the installed fonts here; it comes from {where}.")
    if direct:
        parts.append("Picking it sets it in kitty at once (kitty.conf); enter sets it again.")
    elif kind:
        if why:
            parts.append(f"skydrop can't set kitty's font here: {why}.")
        parts.append(f"enter copies   {'   '.join(lines)}   for {place}" + (f" ({reload})." if reload else "."))
    else:
        parts.append(f"enter copies its name, {state or family}, for your terminal's font setting.")
    return " ".join(parts)


def kitty_direct(kind=None):
    """(True, None) when skydrop can set kitty's font itself here; (False, why not) in a
    kitty it can't reach; (False, None) in any other terminal."""
    kind = terminal_kind() if kind is None else kind
    if kind != "kitty":
        return False, None
    from .termfont import kitty_process
    pid, why = kitty_process()
    return bool(pid), (None if pid else why)


def font_lines(key, size=None, kind=None):
    """The config lines that set a font (and its size) in this terminal: kitty's and
    Ghostty's, else just the font's name. Empty for the terminal's own at its own size."""
    kind = terminal_kind() if kind is None else kind
    key = font_key(key)
    lines = []
    if key != "own":
        family, _, _ = font_family(key, kind)
        lines.append(font_config_line(font_installed(key, kind) or family, kind)[0])
    if size is not None and kind in ("kitty", "ghostty"):
        text = f"{float(size):.2f}".rstrip("0").rstrip(".")
        lines.append(f"font_size {text}" if kind == "kitty" else f"font-size = {text}")
    return lines


def font_line(key, kind=None):
    """The line Enter copies for a font: the config line in kitty and Ghostty, else its name."""
    kind = terminal_kind() if kind is None else kind
    family, _, _ = font_family(key, kind)
    return font_config_line(font_installed(key, kind) or family, kind)[0]


def osc52(text):
    """Text onto the clipboard through the terminal (OSC 52: kitty, Ghostty and most
    others; nothing happens where it is turned off)."""
    return "\x1b]52;c;" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "\x07"


# ---------------------------------------------------------------------------- the glass's pictures
def kitty_raw(image_id, rgba, width, height):
    """Escape codes that store width x height RGBA pixels, deflated, as picture
    `image_id` without showing it (q=2: no replies)."""
    data = base64.b64encode(zlib.compress(bytes(rgba), 6))
    return _kitty_chunks(f"a=t,f=32,t=d,o=z,q=2,i={image_id},s={width},v={height}", data)


def _kitty_chunks(head, data):
    parts = []
    for start in range(0, max(1, len(data)), 4096):
        more = 1 if start + 4096 < len(data) else 0
        keys = f"{head},m={more}" if start == 0 else f"m={more}"
        parts.append("\x1b_G" + keys + ";" + data[start:start + 4096].decode("ascii") + "\x1b\\")
    return "".join(parts)


def kitty_frames(image_id, frames, width, height):
    """Frames of RGBA as one picture, the first showing: skydrop picks the frame itself
    (a=a with c=, Glass.step), so everything that moves on the glass changes in the
    same write and the terminal draws it once. The terminal's own animation stays
    stopped. A terminal without frames keeps showing the first."""
    parts = [kitty_raw(image_id, frames[0], width, height)]
    for frame in frames[1:]:
        data = base64.b64encode(zlib.compress(bytes(frame), 6))
        parts.append(_kitty_chunks(f"a=f,f=32,t=d,o=z,q=2,X=1,i={image_id},s={width},v={height}", data))
    parts.append(f"\x1b_Ga=a,i={image_id},s=1,c=1,q=2\x1b\\")
    return "".join(parts)


def scaled_size(width, height, most=400):
    """The smallest picture size with exactly the screen's shape, so the terminal can
    stretch it over the screen without a gap (a stretched picture keeps its shape);
    None when there is none under `most`."""
    g = math.gcd(width, height)
    w, h = width // g, height // g
    return (w, h) if w <= most and h <= most else None


def raster(width, height, mono, scan=1.0, stripes=1.0):
    """Scanlines and the aperture grille over the whole screen: every third pixel row a
    dark gap and the row before it a third as dark, the pixel columns tinted red, green
    and blue in turn, faintly. `scan` and `stripes` are their strengths (1 the
    standard). Straight even with Curvature: rows this fine bowed on the screen's pixel
    grid break their rhythm along curves across the screen (wavy creases), so the rim
    and the bezel show the bulge."""
    grille = (0.018 if mono else 0.032) * stripes
    units = []
    for dark in (0.0, min(0.3, 0.12 * scan), min(0.85, 0.38 * scan)):
        unit = b""
        for colour in ((255, 70, 70), (70, 255, 70), (70, 70, 255)):
            alpha = dark + grille * (1 - dark)
            if alpha <= 0:
                unit += bytes(colour) + b"\x00"
                continue
            rgb = bytes(round(c * grille * (1 - dark) / alpha) for c in colour)
            unit += rgb + bytes([round(alpha * 255)])
        units.append(unit)
    rows = [(unit * (width // 3 + 1))[:width * 4] for unit in units]
    return b"".join(rows[y % 3] for y in range(height))


def barrel(u, v, k):
    """Where a point of the screen (u, v from -1 to 1) shows the picture from, on a tube
    bulging by k: further out toward the edges."""
    shift = 1 - k * (u * u + v * v)
    return (u / shift, v / shift) if shift > 0 else (9.0, 9.0)


def sheen(width, height, curve, framed, glare=1.0):
    """The glass's own picture, stretched over the screen: with `curve` (Curvature) the
    picture falling away round a curved rim - darker beyond it, a little darker just
    inside it - and with `glare` (Glare) a soft oval of light high on the left of the
    glass, curved or flat. Beyond the rim is only part dark where there is no bezel's
    margin, so nothing the app drew there is lost."""
    k = CURVE * curve
    beyond = 0.92 if framed else 0.5
    bright = 0.035 * min(2.0, max(0.0, glare))
    rows = []
    for py in range(height):
        v = (py + 0.5) / height * 2 - 1
        row = bytearray()
        for px in range(width):
            u = (px + 0.5) / width * 2 - 1
            rim = 0.0
            if k:
                su, sv = barrel(u, v, k)
                edge = max(abs(su), abs(sv)) - 1
                rim = min(1.0, max(0.0, (edge + 0.025) / 0.05)) * beyond
            du, dv = (u + 0.42) / 0.55, (v + 0.58) / 0.30
            gleam = max(0.0, 1 - (du * du + dv * dv)) ** 3 * bright
            if rim >= gleam:
                row += bytes((0, 0, 0, round(rim * 255)))
            else:
                row += bytes((255, 255, 255, round(gleam * 255)))
        rows.append(bytes(row))
    return b"".join(rows)


def bezel(width, height, ring_x, ring_y, round_by, curve):
    """The bezel: a dark frame over the outer ring of cells the app left for it (ring_x,
    ring_y pixels), its opening's corners rounded by `round_by` (1 the standard) and
    its edges bowing out with `curve`, the tube set back behind it - a soft shadow
    inside the opening's edge."""
    inset_x, inset_y = ring_x * 0.62, ring_y * 0.62
    radius = max(4.0, min(ring_x, ring_y) * 1.8 * round_by)
    bulge_x, bulge_y = ring_x * 0.3 * min(2.0, curve), ring_y * 0.3 * min(2.0, curve)
    shadow = max(3.0, min(ring_x, ring_y) * 0.55 * min(2.0, 0.5 + round_by / 2))
    left, top, bottom = inset_x, inset_y, height - inset_y
    cx, cy = width / 2, height / 2

    def edge_x(y):
        """The opening's left bound in a row (its right bound mirrors it)."""
        t = (y - cy) / max(1.0, cy - top)
        bound = left - bulge_x * max(0.0, 1 - t * t)
        if y < top + radius:
            d = top + radius - y
            bound = max(bound, left + radius - math.sqrt(max(0.0, radius * radius - d * d)))
        elif y > bottom - radius:
            d = y - (bottom - radius)
            bound = max(bound, left + radius - math.sqrt(max(0.0, radius * radius - d * d)))
        return bound

    def edge_y(x):
        """The opening's top bound in a column (its bottom mirrors it)."""
        t = (x - cx) / max(1.0, cx - left)
        return top - bulge_y * max(0.0, 1 - t * t)

    frame = []
    for y in range(height):
        grey = 34 - round(14 * y / max(1, height - 1))
        frame.append(bytes((grey, grey, grey + 2, 255)))
    clear = b"\x10\x10\x12\x00"
    tops = [edge_y(x + 0.5) for x in range(width)]
    rows = []
    for y in range(height):
        fill = frame[y]
        yc = y + 0.5
        near = min(yc, height - yc)
        bound = edge_x(yc)
        if near < top + shadow + 2 or near < radius + top:
            # the bands along the top and bottom: worked out pixel by pixel
            row = bytearray()
            for x in range(width):
                xc = x + 0.5
                inside_x = min(xc - bound, width - bound - xc)
                inside_y = near - tops[x]
                inside = min(inside_x, inside_y)
                if inside <= -0.5:
                    row += fill
                elif inside < 0.5:
                    row += fill[:3] + bytes([round(255 * (0.5 - inside))])
                elif inside < shadow:
                    row += bytes((0, 0, 0, round(150 * (1 - inside / shadow) ** 2)))
                else:
                    row += clear
            rows.append(bytes(row))
            continue
        solid = max(0, int(bound - 0.5))
        part = bound - solid                    # how far the edge pixel is in
        edge = fill[:3] + bytes([round(255 * max(0.0, min(1.0, 1 - part)))])
        ramp = [bytes((0, 0, 0, round(150 * (1 - (i + 0.5) / shadow) ** 2))) for i in range(int(shadow))]
        middle = width - 2 * (solid + 1 + len(ramp))
        if middle <= 0:
            rows.append(fill * width)
            continue
        rows.append(fill * solid + edge + b"".join(ramp) + clear * middle + b"".join(reversed(ramp)) + edge +
                    fill * solid)
    return b"".join(rows)


def vignette(width, height, strength=1.0):
    """The darker edges and corners: black, clear over the middle and about half
    covering the corners (at the standard `strength`, 1), by distance from the
    centre."""
    cx, cy = max(1.0, (width - 1) / 2), max(1.0, (height - 1) / 2)
    across = bytes(min(255, round(((x - cx) / cx) ** 2 * 255)) for x in range(width))
    tables = {}
    rows = [b""] * height
    for y in range((height + 1) // 2):
        ny2 = ((y - cy) / cy) ** 2
        key = round(ny2 * 500)
        table = tables.get(key)
        if table is None:
            table = bytes(_vignette_alpha(v / 255 + ny2, strength) for v in range(256))
            tables[key] = table
        line = bytearray(width * 4)
        line[3::4] = across.translate(table)
        rows[y] = rows[height - 1 - y] = bytes(line)
    return b"".join(rows)


def _vignette_alpha(d2, strength=1.0):
    t = max(0.0, min(1.0, (math.sqrt(d2) - 0.55) / 0.86))
    return round(255 * min(0.9, 0.5 * strength * t ** 1.8))


def flat(width, height, rgb, alpha):
    return bytes(list(rgb) + [alpha]) * (width * height)


def band(width, height, rgb, peak=0.05):
    """The hum bar: a soft band of light, brightest across its middle."""
    rows = []
    for y in range(height):
        alpha = round(255 * peak * math.sin(math.pi * (y + 0.5) / height) ** 2)
        rows.append(bytes(list(rgb) + [alpha]) * width)
    return b"".join(rows)


def grain(width, height, strength=1.0):
    """A frame of grain: faint specks, light and dark, on a third of the pixels
    (`strength` 1: the standard)."""
    count = width * height
    table = bytes(min(255, round(a * GRAIN_STANDARD * strength)) for a in GRAIN_ALPHA)
    alpha = os.urandom(count).translate(table)
    tone = os.urandom(count).translate(GRAIN_SHADE)
    out = bytearray(count * 4)
    out[0::4] = tone
    out[1::4] = tone
    out[2::4] = tone
    out[3::4] = alpha
    return bytes(out)


# how much of a cell the meters' segments and other partial blocks light (a raised
# meter block lights its whole cell through its background)
GLYPH_WEIGHTS = {"\U0001FB13": 0.4, "\U0001FB03": 0.2, "\U0001FB7F": 0.15, "▕": 0.15,
                 "▁": 0.15, "▔": 0.15, "▏": 0.15, "▖": 0.25, "▌": 0.5, "▆": 0.75,
                 "▓": 0.7, "▒": 0.45, "░": 0.25}


def glyph_weight(ch):
    """How much of a cell a character lights."""
    code = ord(ch)
    weight = GLYPH_WEIGHTS.get(ch)
    if weight is not None:
        return weight
    if 0x2580 <= code <= 0x259F or 0x1FB00 <= code <= 0x1FBFF or ch in "■◼●":
        return 0.9                      # blocks, meters, pictures drawn in blocks
    if 0x2500 <= code <= 0x257F:
        return 0.35                     # box drawing: thin lines
    if 0x2800 <= code <= 0x28FF:
        return 0.3                      # braille
    return 0.55


# ---------------------------------------------------------------------------- meters
# Every meter in skydrop is drawn in one of six looks (Settings, Display: Meters), one
# segment a cell and only the lit segments ever drawn. Each look is (its label, then
# (segment, how it is colored) where the terminal draws Unicode's legacy computing
# blocks itself - kitty, Ghostty, VTE - and the same in plainer blocks). The coloring
# is a prefix to the meter's style: "" the segment in the color on the background;
# "^" the cell reversed, so the color fills it and the thin corner the character
# draws takes the background's (a gap after the block and above it); "%" the cell in
# the color with the character - a thin edge down its right side and along its foot
# - in a darker shade of it (METER_SHADE of the way from the background), a block lit
# from the top left.
METER_STYLES = {
    # an old stereo's level meter: a tall lit segment, then a dark gap
    # (BLOCK SEXTANT-35: the left half of the cell, its lower two thirds)
    "led": ("LED segments", ("\U0001FB13", ""), ("▌", "")),
    # a row of small square lamps (BLOCK SEXTANT-3: the left half's middle)
    "dots": ("LED dots", ("\U0001FB03", ""), ("▖", "")),
    # blocks with a sliver of gap after them and above them
    # (RIGHT AND UPPER ONE EIGHTH BLOCK in a reversed cell)
    "bricks": ("bricks", ("\U0001FB7E", "^"), ("▊", "")),
    # blocks with depth (RIGHT AND LOWER ONE EIGHTH BLOCK, shaded)
    "raised": ("raised blocks", ("\U0001FB7F", "%"), ("▕", "%")),
    # one bar, the lower three quarters of each cell, so a chart's rows still stand apart
    "solid": ("solid bar", ("▆", ""), ("▆", "")),
    # a terminal of the 1980s: full blocks, the last three shading off ▓ ▒ ░
    "shade": ("shaded blocks █▓▒░", ("█", ""), ("█", "")),
}
METER_ORDER = ("led", "dots", "bricks", "raised", "solid", "shade")
METER_LOOKS = list(METER_ORDER)
METER_LABEL = {key: spec[0] for key, spec in METER_STYLES.items()}
METER_SHADES = "▓▒░"
METER_SHADE = 0.45
# "blocks": False where the terminal cannot draw the legacy computing characters;
# "ascii": the ascii graphics mode (# for every segment); "style": the look
METER = {"blocks": True, "ascii": False, "style": "led"}
# Settings, Display: Meter blocks. Four of the looks are drawn with characters from
# Unicode's Symbols for Legacy Computing, which kitty, Ghostty, WezTerm, foot and the VTE
# terminals (GNOME Terminal, Tilix, ...) draw themselves; anywhere else they need a font
# that has them. auto uses them in those terminals and plainer blocks everywhere else.
METER_BLOCKS = ("auto", "legacy", "plain")
METER_BLOCKS_LABEL = {"auto": "auto", "legacy": "legacy blocks", "plain": "plain blocks"}
LEGACY_TERMINALS = ("kitty", "ghostty", "wezterm", "foot", "vte")


def meter_blocks_of(cfg):
    name = str((cfg.get("meter_blocks") if isinstance(cfg, dict) else cfg) or "auto").lower()
    return name if name in METER_BLOCKS else "auto"


def legacy_blocks(cfg, terminal=None):
    """Whether the meters are drawn with the legacy computing characters (Meter blocks):
    asked for, or auto in a terminal that draws them itself."""
    choice = meter_blocks_of(cfg)
    if choice != "auto":
        return choice == "legacy"
    env = os.environ
    who = " ".join((TERMINAL.get("name", "") if terminal is None else terminal, env.get("TERM", ""),
                    env.get("TERM_PROGRAM", ""), "vte" if env.get("VTE_VERSION") else "",
                    "kitty" if env.get("KITTY_WINDOW_ID") else "",
                    "ghostty" if env.get("GHOSTTY_RESOURCES_DIR") else "")).lower()
    return any(name in who for name in LEGACY_TERMINALS)


def meter_style(cfg_or_name):
    name = cfg_or_name.get("meter_style") if isinstance(cfg_or_name, dict) else cfg_or_name
    name = str(name or "led").lower()
    name = "shade" if name == "shaded" else name
    return name if name in METER_STYLES else "led"


def meter_glyph(style=None):
    """(segment, coloring prefix) for a meter look, as this terminal draws it."""
    spec = METER_STYLES[meter_style(style or METER["style"])]
    return spec[1] if METER["blocks"] else spec[2]


def styled(prefix, style):
    """A meter's style: "^" the cell reversed, "%" a raised block (Theme.spec and the
    CRT know both)."""
    if prefix == "^":
        return ("rev", style)
    if prefix == "%":
        return ("raised", style)
    return style


def meter_block(count, style, look=None):
    """`count` lit meter segments in `style`'s color, in the meter look chosen (or
    `look`), as spans."""
    if count <= 0:
        return []
    if METER["ascii"]:
        return [("#" * count, style)]
    look = meter_style(look or METER["style"])
    glyph, prefix = meter_glyph(look)
    if look == "shade":
        tip = METER_SHADES[:max(0, min(3, count - 1))]
        return [(glyph * (count - len(tip)) + tip, styled(prefix, style))]
    return [(glyph * count, styled(prefix, style))]


def meter_spans(fraction, width, style, reverse=False):
    """A level meter `width` segments long: the lit segments only, in the meter look
    chosen and `style`'s color; the rest of the scale is left empty, so the next column
    stays where it was. Anything above zero lights at least one segment. `reverse`
    lights it from the right."""
    width = max(1, int(width))
    fraction = max(0.0, min(1.0, fraction or 0.0))
    lit = int(round(fraction * width))
    if fraction > 0 and lit == 0:
        lit = 1
    spans = meter_block(lit, style)
    if width > lit:
        spans.append((" " * (width - lit), "text"))
    return spans[::-1] if reverse else spans


def meter(n, look="led", ascii_only=False):
    """The lit segments as plain text (no coloring), for places that only need the
    characters."""
    if n <= 0:
        return ""
    if ascii_only:
        return "#" * n
    look = meter_style(look)
    glyph = meter_glyph(look)[0]
    if look == "shade":
        tip = METER_SHADES[:max(0, min(3, n - 1))]
        return glyph * (n - len(tip)) + tip
    return glyph * n


# ---------------------------------------------------------------------------- the glass
class Glass:
    """CRT mode's glass, where the terminal shows pictures: see-through pictures placed
    over the text (z above 0) - the scanlines and grille, the vignette, the flicker veil
    and the grain (frames skydrop steps through itself), the hum bar (moved a few pixels
    at a time, in the same write as those frames), the curve's rim and the glare (one
    picture), the bezel, and the glow, its fringes and the soft focus (sent again when
    the screen changes). Each is left out while its effect is at 0. What it sends waits
    in `pending` for the renderer's next write."""

    RASTER, VIGNETTE, VEIL, HUM, GRAIN = (IMAGE_BASE + n for n in range(5))
    SHEEN, BEZEL = IMAGE_BASE + 11, IMAGE_BASE + 12
    # the glow pictures, two sets to swap between: the bloom, its fringes (+2) and the
    # soft focus (+4)
    GLOWS = (IMAGE_BASE + 5, IMAGE_BASE + 6)
    LAYERS = (0, 2, 4)
    PARTS = {"raster": RASTER, "vignette": VIGNETTE, "veil": VEIL, "hum": HUM,
             "grain": GRAIN, "sheen": SHEEN, "bezel": BEZEL}
    MOVING = frozenset((VEIL, HUM, GRAIN))      # stepped together (step)

    def __init__(self, crt):
        self.crt = crt
        self.pending = []
        self.geometry = None
        self.reset()

    def reset(self):
        """Forget what the terminal has (after it dropped every picture)."""
        self.geometry = None
        self.margin = (0, 0, 0, 0)
        self.sheen_size = None
        self.parts = set()          # the pictures sent for this screen
        self.glow = None            # the glow pictures on screen now (the first id)
        self.glow_layers = ()
        self.small = None
        self.scaled = False
        self.hum_h = 1
        self.tile = (1, 1)
        self.hum_top = 0.0
        self.hum_at = 0.0
        self.step_at = 0.0
        self.frames = {}            # the frame each stepped picture shows
        self.veil_frames = {}       # the flicker's darkness -> its frame

    @property
    def any(self):
        return bool(self.geometry or self.glow)

    def write(self, text):
        if text:
            self.pending.append(text)

    def take(self):
        out, self.pending = "".join(self.pending), []
        return out

    def moving(self):
        """Whether anything on the glass moves by itself: the grain, the flicker, the hum bar."""
        return bool(self.parts & self.MOVING)

    def setup(self, cols, rows, cw, ch, phosphor, margin=(0, 0, 0, 0)):
        """Send the pictures that do not change for this screen size (and the ring of
        cells the app left for the bezel, `margin`)."""
        self.clear()
        self.geometry = (cols, rows, cw, ch, phosphor)
        self.margin = margin
        width, height = cols * cw, rows * ch
        self.small = scaled_size(width, height)
        self.scaled = bool(self.small)
        self.hum_h = max(ch * 8, height // 5)
        self.tile = (min(cols, 24), min(rows, 8))
        self.write("".join(self.build(part) for part in self.PARTS))
        self.hum_top = -self.hum_h
        self.hum_at = time.time()

    def build(self, part):
        """One of the glass's pictures as the escape codes that send it, at its effect's
        level; nothing while that is 0."""
        cols, rows, cw, ch, phosphor = self.geometry
        width, height = cols * cw, rows * ch
        spec = PHOSPHORS.get(phosphor) or PHOSPHORS["green"]
        amount = self.crt.amount
        image = self.PARTS[part]
        self.parts.discard(image)
        self.frames.pop(image, None)            # sent again, it shows its first
        framed = any(self.margin)
        if part == "raster":
            scan, stripes = amount("scanlines"), amount("grille")
            if not (scan or stripes):
                return ""
            data = kitty_raw(image, raster(width, height, spec[2] is not None, scan, stripes), width, height)
        elif part == "sheen":
            curve, glare = amount("curve"), amount("glare")
            if not (curve or glare):
                return ""
            size = self.small or (192, max(1, round(192 * height / width)))
            if size[0] > 192:
                size = (192, max(1, round(192 * height / width)))
            data = kitty_raw(image, sheen(*size, curve, framed, glare), *size)
            self.sheen_size = size
        elif part == "bezel":
            if not (framed and amount("bezel")):
                return ""
            top, left, bottom, right = self.margin
            data = kitty_raw(image, bezel(width, height, min(left, right + 1) * cw, min(top, bottom) * ch,
                                          amount("bezel"), amount("curve")), width, height)
        elif part == "vignette":
            strength = amount("vignette")
            if not strength:
                return ""
            size = (width, height)
            if self.small:
                times = max(1, min(400 // self.small[0], 400 // self.small[1], 4))
                size = (self.small[0] * times, self.small[1] * times)
            data = kitty_raw(image, vignette(*size, strength), *size)
        elif part == "veil":
            strength = amount("flicker")
            if not strength:
                return ""
            veil = self.small or (96, max(1, round(96 * height / width)))
            shades = sorted(set(VEIL))
            self.veil_frames = {alpha: n + 1 for n, alpha in enumerate(shades)}
            data = kitty_frames(image, [flat(*veil, (0, 0, 0), min(255, round(alpha * strength)))
                                        for alpha in shades], *veil)
        elif part == "hum":
            strength = amount("hum")
            if not strength:
                return ""
            data = kitty_raw(image, band(width, self.hum_h, spec[3] or (255, 255, 255), 0.01 * strength),
                             width, self.hum_h)
        else:
            strength = amount("grain")
            if not strength:
                return ""
            tile_w, tile_h = self.tile[0] * cw, self.tile[1] * ch
            data = kitty_frames(image, [grain(tile_w, tile_h, strength) for _ in range(GRAIN_FRAMES)],
                                tile_w, tile_h)
        self.parts.add(image)
        return data

    def redo(self, part):
        """Send one picture again at its effect's level now (or take it away, at 0), and
        place it."""
        if not self.geometry:
            return
        self.write(f"\x1b_Ga=d,d=I,i={self.PARTS[part]},q=2\x1b\\" + self.build(part))
        if part == "hum":
            self.restart_hum()
        self.place_all()

    def restart_hum(self):
        self.hum_top = -self.hum_h
        self.hum_at = time.time()

    @staticmethod
    def _put(row, col, image_id, z, extra="", pid=1):
        return f"\x1b[{row + 1};{col + 1}H\x1b_Ga=p,i={image_id},p={pid},z={z},C=1,q=2{extra}\x1b\\"

    def _hum(self):
        cols, rows, cw, ch, _ = self.geometry
        height = rows * ch
        top = int(self.hum_top)
        skip = max(0, -top)
        shown = min(self.hum_h - skip, height - max(0, top))
        if shown <= 0:
            return f"\x1b_Ga=d,d=i,i={self.HUM},q=2\x1b\\"
        at = max(0, top)
        return self._put(at // ch, 0, self.HUM, 13, f",y={skip},h={shown},Y={at % ch}")

    def place_all(self):
        if not self.geometry:
            return
        cols, rows, cw, ch, _ = self.geometry
        parts = self.parts
        out = ["\x1b7"]
        if self.RASTER in parts:
            out.append(self._put(0, 0, self.RASTER, 11))
        if self.VIGNETTE in parts:
            out.append(self._put(0, 0, self.VIGNETTE, 15, f",c={cols},r={rows}" if self.scaled else ""))
        if self.VEIL in parts:
            out.append(self._put(0, 0, self.VEIL, 14, f",c={cols},r={rows}"))
        if self.GRAIN in parts:
            tc, tr = self.tile
            pid = 1
            for ty in range(0, rows, tr):
                for tx in range(0, cols, tc):
                    out.append(self._put(ty, tx, self.GRAIN, 12, f",w={min(tc, cols - tx) * cw},"
                                         f"h={min(tr, rows - ty) * ch}", pid))
                    pid += 1
        if self.HUM in parts:
            out.append(self._hum())
        if self.SHEEN in parts:
            out.append(self._put(0, 0, self.SHEEN, 16, f",c={cols},r={rows}"))
        if self.BEZEL in parts:
            out.append(self._put(0, 0, self.BEZEL, 17))
        if self.glow:
            for layer in self.glow_layers:
                out.append(self._put(0, 0, self.glow + layer, 10, f",c={cols},r={rows}"))
        out.append("\x1b8")
        self.write("".join(out))

    def step(self, now):
        """The glass's moving parts on by a step - the hum bar down the screen (round
        again from the top), the grain's and the flicker's next frames - in one write,
        so the terminal draws them together, ten times a second at most, not each on a
        clock of its own."""
        parts = self.parts
        if not self.geometry or not parts & self.MOVING or now - self.step_at < TICK:
            return
        self.step_at = now
        out = []
        if self.HUM in parts:
            _, rows, _, ch, _ = self.geometry
            travel = rows * ch + self.hum_h
            self.hum_top += travel * (now - self.hum_at) / HUM
            self.hum_at = now
            if self.hum_top >= rows * ch:
                self.hum_top = -self.hum_h
            out.append(self._hum())
        beat = int(now / TICK)
        for image, frame in ((self.GRAIN, beat % GRAIN_FRAMES + 1),
                             (self.VEIL, self.veil_frames.get(VEIL[beat % len(VEIL)], 1))):
            if image in parts and self.frames.get(image, 1) != frame:
                out.append(f"\x1b_Ga=a,i={image},c={frame},q=2\x1b\\")
                self.frames[image] = frame
        if out:
            self.write("\x1b7" + "".join(out) + "\x1b8")

    def next_step(self, now):
        """Seconds until the moving parts step again (None: nothing moves)."""
        if not self.geometry or not self.parts & self.MOVING:
            return None
        return max(0.0, TICK - (now - self.step_at))

    def show_glow(self, layers):
        """Send the glow's layers, {0 bloom, 2 fringes, 4 soft focus: (rgba, width,
        height)} - the ones whose effects are on - and swap them in for those on
        screen."""
        if not self.geometry:
            return
        cols, rows = self.geometry[0], self.geometry[1]
        new = self.GLOWS[1] if self.glow == self.GLOWS[0] else self.GLOWS[0]
        order = sorted(layers)
        parts = [kitty_raw(new + layer, *layers[layer]) for layer in order]
        if order:
            parts.append("\x1b7" + "".join(self._put(0, 0, new + layer, 10, f",c={cols},r={rows}")
                                           for layer in order) + "\x1b8")
        if self.glow:
            parts.extend(f"\x1b_Ga=d,d=I,i={self.glow + layer},q=2\x1b\\" for layer in self.LAYERS)
        self.glow = new if order else None
        self.glow_layers = tuple(order)
        self.write("".join(parts))

    def clear(self):
        """Take every picture of the glass away."""
        if self.geometry or self.glow:
            self.write("".join(f"\x1b_Ga=d,d=I,i={IMAGE_BASE + n},q=2\x1b\\" for n in range(13)))
        self.geometry = None
        self.parts = set()
        self.frames = {}
        self.glow = None
        self.glow_layers = ()

    def off(self, out):
        """CRT mode (or its glass) off: the pictures go."""
        self.clear()
        sent = self.take()
        if sent:
            out.append(sent)


def _theme_sig(theme):
    """What of a theme the tube's colors follow: its name and its page (Background)."""
    return (getattr(theme, "name", None), tuple((getattr(theme, "pal", None) or {}).get("bg") or ()))


# ---------------------------------------------------------------------------- the text layer
class Frame:
    """What the renderer draws: chars and styles, like a Canvas."""
    __slots__ = ("chars", "styles", "h", "w", "cursor")

    def __init__(self, chars, styles, h, w, cursor=None):
        self.chars, self.styles, self.h, self.w, self.cursor = chars, styles, h, w, cursor


HALF = frozenset("▀▄")                 # half-block picture pixels (blocks mode): exact colors


class CRT:
    """CRT mode for the renderer: what is on the screen cell by cell, as the phosphor
    shows it, so the effects a screen of text can show are drawn over each frame -
    trails, burn-in, the power-on sweep, rows slipping, the typewriter, flashes and
    blinks - and the glow can be worked out; and the glass, where the terminal shows
    pictures. Off, it does nothing."""

    def __init__(self):
        self.on = False
        self.levels = {key: STANDARD for key in EFFECT_KEYS}
        self.phosphor = "green"
        self.theme = None
        self.pictures = False          # the terminal shows kitty pictures: the glass layer works
        self.glass_ok = True           # the terminal is kitty or Ghostty (the app says)
        self.checks = None             # the app's POST checks: index -> (what, found, verdict, figure)
        self.first_screen = "Now"
        self.urgent = False            # a tornado warning on screen: no typing, no boot, nothing held back
        self.fresh = False             # the next new frame came with fresh data: its changed numbers flash
        self.glass = Glass(self)
        self.version = 0
        self.wake_at = None
        self.black = PHOSPHORS["green"][1]
        self.page = (0, 0, 0)
        self.bg_rgb = (0, 0, 0)
        self.base = self.black         # the background as CRT mode paints it
        self.styles = {}
        self._maps = {}
        self._rows = {}
        self.boot = None
        self.post = None
        self.blinking = False
        self.blink_epoch = None
        self.blink_phase = 0
        self.flash_all = False
        self.slip_at = time.time() + self.slip_gap()
        self.glow_due = False
        self.glow_at = 0.0
        self.glow_grid = None
        self.glow_rows = {}
        self.glow_sig = None
        self.size = None
        self.burn_ver = 0
        self._looks = {}
        self.blank = ("rgb", (200, 200, 200), (0, 0, 0), "")
        self._reset()
        self._colors()

    # -- levels -----------------------------------------------------------------------
    def amount(self, key):
        """An effect's strength as a share of the standard: 0 off, 1 the standard, 2 the most."""
        return self.levels.get(key, STANDARD) / STANDARD

    def slip_gap(self):
        """Seconds until the next sync slip, at the Sync slips level."""
        amount = self.amount("slips") if hasattr(self, "levels") else 1.0
        return random.uniform(*SLIP) / amount if amount else float("inf")

    def glowing(self):
        """Whether any of the glow's layers is on."""
        return any(self.amount(key) for key in ("bloom", "fringes", "focus"))

    def _reset(self):
        self.src_obj = None
        self.chars = []                # the screen as the app drew it, mapped: chars
        self.cells = []                # ... and their styles
        self.prev = None               # (chars, cells) of the last whole frame
        self.trails = []
        self.trail_step = 0
        self.trail_at = 0.0
        self.wear = {}
        self.burnt = {}                # row -> {column: the character burnt in}
        self.worn_at = None
        self.slip = None
        self.typing = None
        self.flashes = None
        self.row_seen = {}             # row -> when it last changed (rows that keep changing aren't typed)
        self.glow_grid = None
        self.cache = None
        self.cache_state = None

    # -- settings ----------------------------------------------------------------------
    def configure(self, cfg, theme, graphics, color_mode):
        was_on = self.on
        old_levels, old_phosphor, old_theme = self.levels, self.phosphor, self.theme
        self.on = CRT_ON(cfg)
        self.levels = levels_of(cfg)
        self.phosphor = phosphor_of(cfg)
        self.theme = theme
        if theme is not None and hasattr(theme, "flatten"):
            theme.flatten(self.on)          # a tube's glass has no panels: cards take the page's color
        self.pictures = graphics == "kitty" and self.glass_ok
        METER["style"] = meter_style(cfg)
        METER["ascii"] = graphics == "ascii"
        METER["blocks"] = legacy_blocks(cfg)
        keys = [key for key in self.levels if self.levels[key] != old_levels.get(key)]
        self._colors()
        self.version += 1
        self.cache = None
        if not self.on:
            self.stop()
            return
        if not was_on:
            self.start(boot=True, post=True)
            return
        if self.phosphor != old_phosphor:
            # a new phosphor: the tube starts again (the power-on sweep, not the self test)
            self.start(boot=True, post=False)
            return
        if _theme_sig(old_theme) != _theme_sig(theme):
            keys = keys + ["blacks"]            # every color anew: the glow too
        self.retune(keys)

    def _colors(self):
        """The phosphor's colors for this theme: the raised black, the background as CRT
        mode paints it, and CRT mode's own styles (trails fading, burnt-in borders, the
        power-on beam, an update flash; the first two brighter or fainter with their
        effects' levels)."""
        spec = PHOSPHORS[self.phosphor]
        self.black = black_of(self.phosphor, self.amount("blacks"))
        self.page = tuple(self.theme.rgb("bg")) if self.theme is not None else (0, 0, 0)
        # the raised black of a lit tube (a one-color tube has no other background)
        self.bg_rgb = (0, 0, 0) if spec[2] is not None else self.page
        self.base = self.tint(self.bg_rgb)
        self._maps, self._rows, self._looks = {}, {}, {}
        self.base_lum = lum(self.base)
        self.glow_rows, self.glow_sig = {}, None
        self.src_obj = None                    # the next frame is mapped anew
        trail = 0.5 + 0.5 * self.amount("trails")
        burn = 0.5 + 0.5 * self.amount("burn")
        flash = min(1.0, 0.75 + 0.25 * self.amount("flash"))
        base = self.base

        def sh(level):
            return shade(self.phosphor, level, self.black)

        self.styles = {
            "crtghost": ("rgb", sh(0.30 * trail), base, ""),
            "crtfaint": ("rgb", sh(0.17 * trail), base, ""),
            "crtghostbg": ("rgb", sh(0.30 * trail), sh(0.16 * trail), ""),
            "crtburn": ("rgb", sh(0.10 * burn), base, ""),
            "crtbeam": ("rgb", sh(1.0), base, "b"),
            "crtflash": ("rgb", sh(flash), base, "b"),
            "crtflash2": ("rgb", sh(0.55 + 0.25 * flash), base, "b"),
        }
        self.blank = self.mapped("text")
        self.tint_key = (("crt", self.phosphor, self.black) if self.on and spec[2] is not None else None)

    def tint(self, rgb, role=None):
        return tint(rgb, self.phosphor, role, self.black)

    def start(self, boot=True, post=True):
        """CRT mode starts (or starts again): the power-on sweep, then the self test."""
        now = time.time()
        self._reset()
        self.boot = now if boot and self.amount("power") else None
        self.post = None
        if post:
            self.start_post(now)
        self.slip_at = now + self.slip_gap()
        self.blinking = False
        self.blink_epoch, self.blink_phase = None, 0
        self.flash_all = self.fresh = False
        self.glow_due, self.glow_at = False, 0.0
        self.glass.clear()

    def stop(self):
        self.glass.clear()
        self._reset()
        self.boot = self.post = None
        self.blinking = False

    def retune(self, keys):
        """Effects whose levels have just changed (`keys`): put right what each touches -
        the glass's pictures, the glow, the timings."""
        now = time.time()
        if "trails" in keys and not self.amount("trails"):
            self.trails = []
        if "burn" in keys and not self.amount("burn"):
            self.wear, self.burnt, self.worn_at = {}, {}, None
        if "slips" in keys:
            self.slip_at = now + self.slip_gap()
        if "power" in keys and not self.amount("power"):
            self.boot = None
        if "type" in keys and not self.amount("type"):
            self.typing = None
        if "flash" in keys and not self.amount("flash"):
            self.flashes = None
        glass = self.glass
        if glass.geometry:
            for part, effects in (("raster", ("scanlines", "grille")), ("vignette", ("vignette",)),
                                  ("veil", ("flicker",)), ("hum", ("hum",)), ("grain", ("grain",)),
                                  ("sheen", ("curve", "glare", "bezel")), ("bezel", ("bezel", "curve"))):
                if any(key in keys for key in effects):
                    glass.redo(part)
        if any(key in keys for key in ("bloom", "fringes", "focus", "blacks")):
            self.glow_grid = None
            if self.glowing():
                self.glow_due = True
            else:
                glass.show_glow({})

    def tint_picture(self, img, key=None, back=None):
        """A picture in the phosphor's colors (unchanged in color): each pixel's
        brightness on the phosphor's ramp. `back`: the color it was drawn on, when it
        has one - a weather icon on the page, a chart - which goes where the tube shows
        that background (the page: the raised black), the brightness above it spread
        over the rest of the ramp, as a see-through picture would sit on the tube."""
        spec = PHOSPHORS.get(self.phosphor)
        if img is None or not spec or spec[2] is None:
            return img
        try:
            from . import gfx
            alpha = img.getchannel("A") if img.mode in ("RGBA", "LA") else None
            rgb = img.convert("RGB")
            weights = (54 / 256, 183 / 256, 19 / 256, 0)
            gray = rgb.convert("L", weights)
            levels = list(range(256))
            if back is not None:
                under = gfx.Image.new("RGB", (1, 1), tuple(back)).convert("L", weights).getpixel((0, 0))
                if 0 < under < 128:                 # a dark background (a light one keeps the plain ramp)
                    # as the tube shows it under text: the page its raised black, a panel lit
                    # (tint's "back")
                    page = tuple(back) == self.page or lum(back) <= 0.02
                    shown = 0 if page else round(255 * (0.18 + 0.82 * lum(back)))
                    levels = [round(v * shown / under) if v <= under else
                              round(shown + (v - under) * (255 - shown) / (255 - under)) for v in levels]
            ramp = [self.tint((v, v, v)) for v in levels]
            out = gfx.Image.merge("RGB", [gray.point([c[i] for c in ramp]) for i in range(3)])
            if alpha is not None:
                out.putalpha(alpha)
            return out
        except Exception:
            return img

    tint_key = None

    def ring(self, W, H):
        """Cells the app leaves for the bezel: (top, left, bottom, right), or None."""
        if self.on and self.pictures and self.amount("bezel") and H >= 16 and W >= 64:
            return (1, 2, 1, 1)
        return None

    @property
    def hide_pictures(self):
        return self.on and (self.boot is not None or self.post is not None)

    def lost(self):
        """The terminal dropped every picture (the screen was cleared): the glass is sent again."""
        self.glass.reset()
        self.glow_grid = None
        self.glow_due = self.on and self.glowing()

    # -- styles ------------------------------------------------------------------------
    def mapped(self, style):
        """A style as the phosphor shows it: ("rgb", fg, bg, attrs)."""
        hit = self._maps.get(style)
        if hit is None:
            hit = self._maps[style] = self._map(style, False)
        return hit

    def _map(self, style, pixel):
        th = self.theme
        if isinstance(style, tuple) and len(style) == 2 and style[0] in ("onpanel", "stripe"):
            return self._map(style[1], pixel)          # a card's fill: the tube shows the page
        if isinstance(style, tuple) and style and style[0] == "rev":
            # a meter segment: the style's color fills the cell and the thin corner the
            # character draws takes the background's
            m = self.mapped(style[1])
            return ("rgb", m[1], m[2], "r")
        if isinstance(style, tuple) and len(style) == 3 and style[0] == "over":
            # a guide across a chart's filled cell: both colors lit as text is
            fg, bg = th.spec(style)[:2] if th else ((200, 200, 200), (60, 60, 60))
            return ("rgb", self.tint(fg, "text"), self.tint(bg, "text"), "")
        if isinstance(style, tuple) and style and style[0] == "raised":
            # a raised meter block: the style's color fills the cell, the thin edge the
            # character draws is a shade of it
            face = self.tint(th.spec(style[1])[0] if th else (200, 200, 200), "text")
            base = self.base
            edge = tuple(round(b + (f - b) * METER_SHADE) for f, b in zip(face, base))
            return ("rgb", edge, face, "")
        try:
            fg, bg, attrs = th.spec(style)[:3]
        except Exception:
            fg, bg, attrs = (200, 200, 200), self.page, ""
        attrs = attrs or ""
        if bg is None or tuple(bg) == self.page:
            bg = None
        if pixel:
            # exact colors (a picture drawn in blocks): no lift for text or a lit background
            return ("rgb", self.tint(fg), self.base if bg is None else self.tint(bg), attrs)
        back = self.bg_rgb if bg is None else bg
        # dark text on a bright bar stays dark: the bar is what is lit
        bright_bar = (0.2126 * back[0] + 0.7152 * back[1] + 0.0722 * back[2]) > 0.35 * 255
        return ("rgb", self.tint(fg, None if bright_bar else "text"), self.tint(back, "back"), attrs)

    def _map_row(self, rc, rs):
        key = tuple(rs) if HALF.isdisjoint(rc) else (tuple(rs), tuple(rc))
        row = self._rows.get(key)
        if row is None:
            maps = self._maps
            row = []
            for ch, st in zip(rc, rs):
                if ch in HALF:
                    m = self._map(st, True)
                else:
                    m = maps.get(st)
                    if m is None:
                        m = maps[st] = self._map(st, False)
                row.append(m)
            if len(self._rows) > 4000:
                self._rows.clear()
            self._rows[key] = row
        return row

    def look(self, m):
        """(fg, bg, fg light, bg light) for a mapped style: how bright its text and its
        background glow."""
        hit = self._looks.get(m)
        if hit is not None:
            return hit
        fg, bg, attrs = m[1], m[2], m[3]
        if "r" in attrs:
            fg, bg = bg, fg
        light = lum(fg) ** 1.5 * (1.2 if "b" in attrs else 1.0) * (0.55 if "d" in attrs else 1.0)
        glow = lum(bg) ** 1.5 * 0.7 if lum(bg) > self.base_lum + 0.04 else 0.0
        hit = self._looks[m] = (fg, bg, light, glow)
        return hit

    def lit(self, ch, m):
        """0 for a dark cell, 1 for one with a character, 2 for a lit background."""
        if ch not in (" ", ""):
            return 1
        return 2 if self.look(m)[3] else 0

    def dark(self, ch, m):
        return ch == " " and not self.look(m)[3]

    # -- input -------------------------------------------------------------------------
    def skip_boot(self):
        """A key during the power-on sweep ends it."""
        if self.boot is not None:
            self.boot = None
            self.cache = None
            return True
        return False

    def on_input(self):
        """Any key: the sweep ends, the self test ends, a blinking alert holds still."""
        self.skip_boot()
        if self.post is not None:
            self.post = None
            self.cache = None
        self.blinking = False

    def start_blink(self):
        self.blinking = True
        self.blink_epoch, self.blink_phase = None, 0
        self.cache = None

    # -- the self test ------------------------------------------------------------------
    def start_post(self, now=None):
        """The POST screen: a power-on self test before the first screen. False when it
        is off (POST screen at 0)."""
        if not (self.on and self.amount("post")) or self.urgent:
            self.post = None
            return False
        installed_font_families()           # fontconfig is asked meanwhile
        self.post = {"start": now or time.time(), "step": POST * self.amount("post"), "checks": [],
                     "canvas": None, "key": None}
        return True

    def post_canvas(self, W, H, now):
        """The POST screen as the app's canvas (None once it is over): a check a line at
        a time, then the first screen booting."""
        post = self.post
        ring = self.ring(W, H) or (0, 0, 0, 0)
        width, height = W - ring[1] - ring[3], H - ring[0] - ring[2]
        if not self.on or height < 18 or width < 56:
            self.post = None
            return None
        if self.boot is not None:
            post["start"] = now               # the test starts once the picture is open
        step = post["step"]
        elapsed = max(0.0, now - post["start"])
        due = int(elapsed / step)              # the checks showing
        total = POST_LINES
        if due > total + 3:
            self.post = None
            return None
        checks = post["checks"]
        while len(checks) < min(due, total):
            try:
                checks.append(self.checks(len(checks)) if self.checks else ("Check", "none", "NONE", None))
            except Exception as exc:  # the self test must never cost the app
                checks.append(("Check", f"{type(exc).__name__}", "WARN", None))
        newest = due - 1 if due <= total else -1
        share = min(1.0, (elapsed - due * step) / (step * 0.8)) if 0 <= newest < len(checks) else 1.0
        figure = checks[newest][3] if 0 <= newest < len(checks) else None
        key = (W, H, due, round(share, 2) if figure is not None else 1.0, len(checks), ring)
        if post["key"] == key and post["canvas"] is not None:
            return post["canvas"]
        from . import VERSION
        from .canvas import Canvas
        inner = Canvas(width, height, "text")
        left, right = 3, min(width - 3, 84)
        head = "SKYDROP BIOS"
        inner.put(1, left, [(head, "title")])
        inner.put(1, left + len(head) + 1, [(f"v{VERSION}", "value")])
        inner.put(2, left, [("Power-on self test · free public weather data", "dim")])
        verdicts = {"OK": "good", "NONE": "dim", "WARN": "warn"}
        for index, (what, found, verdict, fig) in enumerate(checks):
            y = 4 + index
            part = share if index == newest else 1.0
            if fig is not None:
                found = found.replace("{n}", f"{int(fig * part):,}")
            label = f"{what} " + "." * max(1, 10 - len(what))
            room = right - left - len(label) - 1 - 8
            found = found if len(found) <= room else found[:max(0, room - 1)] + "…"
            inner.put(y, left, [(label, "accent")])
            inner.put(y, left + len(label) + 1, [(found, "text")])
            if fig is not None and part < 1.0:
                continue                       # still counting: no verdict yet
            dots_at = left + len(label) + 2 + len(found)
            end = right - len(verdict) - 1
            if end > dots_at:
                inner.put(y, dots_at, [("." * (end - dots_at), "dim")])
            inner.put(y, right - len(verdict), [(verdict, verdicts.get(verdict, "dim"))])
        if due > total:
            inner.put(5 + total, left, [(f"Booting {self.first_screen}" + "." * min(3, due - total), "value")])
        inner.put(height - 2, left, [("any key skips the self test", "dim")])
        if any(ring):
            canvas = Canvas(W, H, "text")
            canvas.embed(inner, ring[0], ring[1])
        else:
            canvas = inner
        post["canvas"], post["key"] = canvas, key
        return canvas

    # -- T on the CRT effects card ------------------------------------------------------
    def power_on(self):
        """Play the power-on sweep again (False while Power-on is at 0)."""
        if not (self.on and self.amount("power")):
            return False
        self.boot = time.time()
        self.cache = None
        return True

    def slip_now(self):
        if not (self.on and self.amount("slips")):
            return False
        self.slip = None
        self.slip_at = time.time()
        self.cache = None
        return True

    def type_again(self):
        """T on the card: the whole screen typed out again."""
        if not (self.on and self.amount("type")):
            return False
        self.prev = None
        self._start_typing(time.time(), every=True)
        self.prev = (self.chars, self.cells)
        self.cache = None
        return True

    def flash_now(self):
        """T on the card: every number on the screen flashes."""
        if not (self.on and self.amount("flash")):
            return False
        self._start_flash(time.time(), every=True)
        self.cache = None
        return True

    def wipe_burn(self):
        self.wear, self.burnt, self.worn_at = {}, {}, None
        self.burn_ver += 1
        self.cache = None

    def try_effect(self, key):
        """T on the card: an effect that comes and goes, shown now."""
        if not self.on:
            return "CRT effect is off: switch it on at the top of the card."
        if key == "power":
            return "Powering on again." if self.power_on() else "Power-on is at 0."
        if key == "slips":
            return "A row slips now." if self.slip_now() else "Sync slips are at 0."
        if key == "burn":
            self.wipe_burn()
            return "Burn-in wiped: a fresh tube."
        if key == "hum":
            glass = self.glass
            if glass.geometry and glass.HUM in glass.parts:
                glass.restart_hum()
                return "The hum bar starts again at the top."
            return "The hum bar is at 0." if self.pictures else \
                "The hum bar is on the glass, which needs kitty or Ghostty."
        if key == "type":
            return "The screen types out again." if self.type_again() else "The typewriter is at 0."
        if key == "post":
            if self.start_post():
                self.cache = None
                return "The self test runs now: any key skips it."
            return "The POST screen is at 0."
        if key == "blink":
            return "alert" if self.amount("blink") else "Blinking alerts are at 0."
        if key == "flash":
            return "Every number on the screen flashes." if self.flash_now() else "The update flash is at 0."
        return ""

    # -- each frame ----------------------------------------------------------------------
    def process(self, src, now):
        """The frame to draw for this canvas at this moment."""
        H, W = src.h, src.w
        if self.size != (H, W):
            self._reset()
            self.size = (H, W)
        if self.urgent:
            self.boot = self.post = None
            self.typing = None
        if self.post is not None:
            post = self.post_canvas(W, H, now)
            if post is not None:
                src = post
        if src is not self.src_obj:
            self._take(src, now)
        self._steps(now)
        state = (id(self.src_obj), self.typing["done"] if self.typing else None,
                 self.flashes["step"] if self.flashes else None,
                 (self.trail_at, self.trail_step) if self.trails else None,
                 self.slip[1] if self.slip else None, self.blink_phase if self.blinking else None,
                 self.burn_ver, self.boot is not None)
        if self.cache is not None and self.boot is None and state == self.cache_state:
            return self.cache
        fr = self._compose(now)
        self.cache, self.cache_state = fr, state
        return fr

    def _take(self, src, now):
        """A new frame from the app: its cells mapped, and what changed feeds the effects."""
        self.src_obj = src
        chars = [list(r) for r in src.chars]
        cells = [self._map_row(rc, rs) for rc, rs in zip(src.chars, src.styles)]
        oc, ocs = self.chars, self.cells
        changed = []
        if oc and len(oc) == len(chars) and len(oc[0]) == len(chars[0]):
            changed = [y for y in range(len(chars)) if oc[y] != chars[y] or ocs[y] != cells[y]]
        self.chars, self.cells = chars, cells
        if self.boot is None:
            self.slip = None                    # a fresh frame puts slipped rows back
            self._burn(now)
            self._start_typing(now)
            self._start_flash(now, every=self.flash_all)
            self.flash_all = False
            self._find_trails(now)
            self.prev = (chars, cells)
            if self.typing is None and self.glowing() and self.glow_grid != (chars, cells):
                self.glow_due = True
        for y in changed:
            self.row_seen[y] = now

    def _steps(self, now):
        """Between frames: the sweep, the trails fading, a row slipping and coming back,
        the typing, the flashes, an alert's blink."""
        if self.boot is not None:
            if now - self.boot >= BOOT[2] * self.amount("power"):
                self.boot = None
                if self.amount("type") and not self.urgent:
                    self.prev = None             # the typewriter brings the picture in
                    self._start_typing(now, every=True)
                self.prev = (self.chars, self.cells)
                self.glow_due = self.glowing() and self.typing is None
            return
        if self.trails and now - self.trail_at >= TRAIL[self.trail_step] * self.amount("trails"):
            if self.trail_step == 0:
                self.trail_step = 1
            else:
                self.trails = []
        if self.slip and now >= self.slip[1]:
            self.slip = None
        elif not self.slip and now >= self.slip_at and self.typing is None:
            self._start_slip(now)
        if self.typing:
            self._type_more(now)
        if self.flashes:
            self._flash_more(now)
        if self.blinking and self.amount("blink"):
            if self.blink_epoch is None:
                self.blink_epoch, self.blink_phase = now, 0
            self.blink_phase = int((now - self.blink_epoch) / (BLINK / self.amount("blink"))) % 2
        else:
            self.blink_epoch, self.blink_phase = None, 0

    def _burn(self, now):
        """Wear: every second a border or the tab bar is on screen counts towards burning
        it in."""
        last = self.worn_at
        if last is not None and now - last < 1.0:
            return                          # counted once a second is plenty
        self.worn_at = now
        amount = self.amount("burn")
        if last is None or not amount:
            return
        spent = min(2.0, now - last)
        needed = BURN / amount
        wear, burnt = self.wear, self.burnt
        for y, row in enumerate(self.chars):
            top = y < 3
            for x, ch in enumerate(row):
                if ch in (" ", "") or not (top or "─" <= ch <= "╿"):
                    continue
                seen = wear.get((y, x))
                if seen is None:
                    seen = wear[(y, x)] = {}
                seen[ch] = seen.get(ch, 0.0) + spent
                if seen[ch] >= needed:
                    best = max(seen, key=seen.get)
                    if burnt.get(y, {}).get(x) != best:
                        burnt.setdefault(y, {})[x] = best
                        self.burn_ver += 1

    def _start_typing(self, now, every=False):
        """Typewriter: what changed since the last frame (and what was still to come) to
        be typed in again, in reading order. Rows that keep changing (the crawl, a radar
        loop drawn in blocks) are left to change."""
        amount = self.amount("type")
        if not amount or self.urgent:
            self.typing = None
            return
        chars, cells = self.chars, self.cells
        if not chars:
            return
        prev = self.prev
        same = prev is not None and len(prev[0]) == len(chars) and len(prev[0][0]) == len(chars[0])
        waiting, clock = set(), None
        if self.typing:
            # still typing: what was to come waits on, and the line keeps its pace
            waiting = set(self.typing["cells"][self.typing["done"]:])
            clock = self.typing["clock"]
        waiting_rows = {y for y, _ in waiting}
        todo = []
        for y, row in enumerate(chars):
            orow = prev[0][y] if same else None
            ocel = prev[1][y] if same else None
            if same and orow == row and ocel == cells[y] and y not in waiting_rows:
                continue                    # nothing new in this row
            if same and not every and y not in waiting_rows and now - self.row_seen.get(y, -9.0) < FAST_ROW:
                continue                    # a row that keeps changing
            crow = cells[y]
            for x, ch in enumerate(row):
                if ch in (" ", ""):
                    continue
                if orow is None or orow[x] != ch or ocel[x] != crow[x] or (y, x) in waiting:
                    todo.append((y, x))
        if not todo:
            self.typing = None
            return
        rate = max(120.0, len(chars) * len(chars[0]) * 0.45 / (TYPE * amount))
        # "clock": when the next character is due, so frames drawn while it types (a clock
        # ticking, the POST screen) never hold it back
        self.typing = {"cells": todo, "done": 0, "rate": rate, "set": set(todo),
                       "clock": now - 1.0 / rate if clock is None else clock}

    def _type_more(self, now):
        typing = self.typing
        cells = typing["cells"]
        count = int((now - typing["clock"]) * typing["rate"])
        if count <= 0:
            return
        target = min(len(cells), typing["done"] + count)
        for c in cells[typing["done"]:target]:
            typing["set"].discard(c)
        typing["clock"] += (target - typing["done"]) / typing["rate"]
        typing["done"] = target
        if target >= len(cells):
            self.typing = None
            self.glow_due = self.glowing()

    def _start_flash(self, now, every=False):
        """Update flash: the digits that changed with fresh data (or, `every`, all of them
        on the screen) lit bright for a moment."""
        fresh, self.fresh = self.fresh, False
        amount = self.amount("flash")
        chars, prev = self.chars, self.prev
        if not amount or not chars or not (fresh or every):
            return False
        same = prev is not None and len(prev[0]) == len(chars) and len(prev[0][0]) == len(chars[0])
        if not same and not every:
            return False
        limit = max(24, len(chars) * len(chars[0]) // 50)
        waiting = set(self.typing["cells"][self.typing["done"]:]) if self.typing else set()
        cells = []
        for y, row in enumerate(chars):
            old = prev[0][y] if same else None
            if not every and old == row:
                continue                    # no number changed in this row
            for x, ch in enumerate(row):
                if not ch[:1].isdigit() or (y, x) in waiting:
                    continue
                if every or old[x][:1] != ch[:1]:
                    cells.append((y, x))
            if len(cells) > limit and not every:
                return False                # a new layout, not a value changing
        if not cells:
            return False
        self.flashes = {"cells": cells, "at": now, "step": 0}
        return True

    def _flash_more(self, now):
        flashes = self.flashes
        amount = self.amount("flash") or 1.0
        if flashes["step"] == 0 and now - flashes["at"] >= FLASH[0] * amount:
            flashes["step"] = 1
        if flashes["step"] == 1 and now - flashes["at"] >= sum(FLASH) * amount:
            self.flashes = None

    def _find_trails(self, now):
        prev, chars, cells = self.prev, self.chars, self.cells
        if prev is None or len(prev[0]) != len(chars) or (chars and len(prev[0][0]) != len(chars[0])) \
                or not self.amount("trails"):
            return
        found = []
        for y, (orow, nrow) in enumerate(zip(prev[0], chars)):
            ocel, ncel = prev[1][y], cells[y]
            if orow == nrow and ocel == ncel:
                continue
            for x in range(len(nrow)):
                if orow[x] == nrow[x] and ocel[x] == ncel[x]:
                    continue
                was = self.lit(orow[x], ocel[x])
                if was and not self.lit(nrow[x], ncel[x]):
                    found.append((y, x, orow[x] if was == 1 else " ", was))
                    if len(found) >= TRAIL_MAX:
                        break
        if found:
            self.trails, self.trail_step, self.trail_at = found, 0, now

    def _start_slip(self, now):
        """A row or a few slipping a cell or two to the right, out of sync."""
        self.slip_at = now + self.slip_gap()
        height = len(self.chars)
        if height < 8:
            return False
        first = random.randint(3, height - 3)
        rows = list(range(first, min(height - 2, first + random.choice((1, 1, 1, 2, 2, 3)))))
        shift = random.choice((1, 1, 2))
        self.slip = ({y: shift for y in rows}, now + random.uniform(0.05, 0.11))
        return True

    def _blink_cells(self):
        """The cells of an alert on the status line (the ones drawn in its style)."""
        src = self.src_obj
        if src is None:
            return []
        out = []
        for y in range(len(src.styles) - 1, max(-1, len(src.styles) - 3), -1):
            for x, st in enumerate(src.styles[y]):
                if st == "stalert":
                    out.append((y, x))
        return out

    def _compose(self, now):
        H = len(self.chars)
        W = len(self.chars[0]) if H else 0
        if self.boot is not None:
            return self._sweep(now, H, W)
        chars = [list(r) for r in self.chars]
        styles = [list(r) for r in self.cells]
        blank = self.blank
        # burn-in: wherever the screen is dark there
        if self.burnt and self.amount("burn"):
            burn = self.styles["crtburn"]
            for y, cols in self.burnt.items():
                if y >= H:
                    continue
                rc, rs = self.chars[y], self.cells[y]
                for x, ch in cols.items():
                    if x < W and self.dark(rc[x], rs[x]):
                        chars[y][x], styles[y][x] = ch, burn
        # trails: what moved away, fading
        if self.trails:
            glyph = self.styles["crtghost" if self.trail_step == 0 else "crtfaint"]
            lit_bg = self.styles["crtghostbg" if self.trail_step == 0 else "crtburn"]
            for y, x, ch, kind in self.trails:
                if y < H and x < W:
                    if kind == 1:
                        chars[y][x], styles[y][x] = ch, glyph
                    else:
                        chars[y][x], styles[y][x] = " ", lit_bg
        # the typewriter: what hasn't been typed yet is blank, its background kept
        if self.typing:
            for y, x in self.typing["cells"][self.typing["done"]:]:
                if y < H and x < W:
                    chars[y][x] = " "
        # an update flash: white-hot, then bright, then back
        if self.flashes:
            st = self.styles["crtflash" if self.flashes["step"] == 0 else "crtflash2"]
            for y, x in self.flashes["cells"]:
                if y < H and x < W:
                    chars[y][x], styles[y][x] = self.chars[y][x], st
        # an alert in its dark half-beat: its characters gone, its background kept
        if self.blinking and self.blink_phase:
            for y, x in self._blink_cells():
                chars[y][x] = " "
        # a row or two out of sync, pushed a cell or two to the right
        if self.slip:
            for y, n in self.slip[0].items():
                if y < H:
                    chars[y] = [" "] * n + chars[y][:W - n]
                    styles[y] = [blank] * n + styles[y][:W - n]
        cursor = self.src_obj.cursor if self.src_obj is not None else None
        return Frame(chars, styles, H, W, cursor)

    def _sweep(self, now, H, W):
        """Power-on: a dot, a line across the middle, then the picture opening out from it
        to the full screen."""
        t = now - self.boot
        dot, line, opened = (part * self.amount("power") for part in BOOT)
        mid = H // 2
        blank = self.blank
        beam = self.styles["crtbeam"]
        chars = [[" "] * W for _ in range(H)]
        styles = [[blank] * W for _ in range(H)]

        def real(y):
            chars[y] = list(self.chars[y])
            styles[y] = list(self.cells[y])
            for x, ch in (self.burnt.get(y) or {}).items():
                if x < W and self.dark(self.chars[y][x], self.cells[y][x]):
                    chars[y][x], styles[y][x] = ch, self.styles["crtburn"]

        if t < line:
            if t < dot:
                text, x0 = "•", W // 2
            else:
                half = int((t - dot) / max(1e-9, line - dot) * W / 2) + 1
                x0 = max(0, W // 2 - half)
                text = "━" * (min(W - 1, W // 2 + half) - x0)
            for i, ch in enumerate(text):
                if 0 <= mid < H and 0 <= x0 + i < W:
                    chars[mid][x0 + i], styles[mid][x0 + i] = ch, beam
            return Frame(chars, styles, H, W, None)
        frac = min(1.0, (t - line) / max(1e-9, opened - line))
        reach = int((1 - (1 - frac) ** 2) * (H / 2 + 1))
        for y in range(H):
            if abs(y - mid) <= reach:
                real(y)
        for y in (mid - reach - 1, mid + reach + 1):
            if 0 <= y < H:
                chars[y] = ["─"] * (W - 1) + [" "]
                styles[y] = [beam] * (W - 1) + [blank]
        return Frame(chars, styles, H, W, None)

    # -- the glow --------------------------------------------------------------------------
    def glass_frame(self, out, now, W, H, cell):
        """After the frame's text: the glass over it - set up for this screen, the glow when
        it is due, the moving parts on a step."""
        glass = self.glass
        cw, ch = cell
        geometry = (W, H, max(1, round(cw)), max(1, round(ch)), self.phosphor)
        margin = self.ring(W, H) or (0, 0, 0, 0)
        if glass.geometry != geometry or glass.margin != margin:
            glass.setup(*geometry, margin=margin)
            glass.place_all()
            self.glow_grid = None
            if self.glowing():
                self.glow_due = True
        if self.glow_due and self.boot is None and now - self.glow_at >= GLOW_EVERY:
            self.send_glow()
        glass.step(now)
        sent = glass.take()
        if sent:
            out.append(sent)

    def send_glow(self):
        """Work out the glow from what is on the screen and send it: the bloom, the color
        fringes and the soft focus, each at its level."""
        self.glow_due = False
        self.glow_at = time.time()
        geometry = self.glass.geometry
        if not geometry:
            return
        cols, rows, cw, ch, _ = geometry
        if len(self.chars) != rows or not self.chars or len(self.chars[0]) != cols:
            return
        bloom_on, fringe_on, focus_on = (self.amount(key) for key in ("bloom", "fringes", "focus"))
        self.glow_grid = (self.chars, self.cells)
        if not (bloom_on or fringe_on or focus_on):
            self.glass.show_glow({})
            return
        width, height = cols * cw, rows * ch
        bloom_w = (cols + 1) // 2
        bloom_h = max(1, round(bloom_w * height / width))
        fringe_w = cols * 3
        fringe_h = max(1, round(fringe_w * height / width))
        focus_w = cols * 2
        focus_h = max(1, round(focus_w * height / width))
        spec = PHOSPHORS.get(self.phosphor) or PHOSPHORS["green"]
        tone = bytes(spec[2] or (180, 180, 180))
        clear = tone + b"\x00"
        bloom_top = min(255, round(24 * bloom_on))
        focus_top = min(255, round(12.8 * focus_on))
        sig = (bloom_on, fringe_on, focus_on, clear, bloom_w, fringe_w, bloom_top, focus_top, self.base)
        cache = self.glow_rows
        if self.glow_sig != sig or len(cache) > 8 * rows:
            cache.clear()
            self.glow_sig = sig
        bloom_rows, fringe_rows, focus_rows = [], [], []
        for rc, rs in zip(self.chars, self.cells):
            key = (tuple(rc), tuple(rs))
            done = cache.get(key)
            if done is None:
                done = cache[key] = self.glow_row(rc, rs, sig)
            bloom_rows.append(done[0])
            focus_rows.append(done[1])
            fringe_rows.append(done[2])
        layers = {}
        if bloom_on:
            layers[0] = (b"".join(bloom_rows[min(rows - 1, py * rows // bloom_h)] for py in range(bloom_h)),
                         bloom_w, bloom_h)
        if fringe_on:
            blank = clear * fringe_w
            lines = []
            for py in range(fringe_h):
                place = (py + 0.5) * rows / fringe_h
                ty = min(rows - 1, int(place))
                # glyphs fill the middle of a row, so their fringes do
                lines.append(fringe_rows[ty] if 0.22 <= place - ty <= 0.82 else blank)
            layers[2] = (b"".join(lines), fringe_w, fringe_h)
        if focus_on:
            blank = clear * focus_w
            lines = []
            for py in range(focus_h):
                place = (py + 0.5) * rows / focus_h
                ty = min(rows - 1, int(place))
                # over the body of a row's letters, not the gap between rows
                lines.append(focus_rows[ty] if 0.18 <= place - ty <= 0.86 else blank)
            layers[4] = (b"".join(lines), focus_w, focus_h)
        self.glass.show_glow(layers)

    def glow_row(self, rc, rs, sig):
        """One row of cells' glow: (its bloom, its soft focus, its fringes), each a row of
        pixels, or None while that layer is off."""
        bloom_on, fringe_on, focus_on, clear, bloom_w, fringe_w, bloom_top, focus_top, _base = sig
        bloom = focus = fringe = None
        light_row, bright = [], []
        for ch, m in zip(rc, rs):
            fg, bg, light, glow = self.look(m)
            lit = ch not in (" ", "")
            strength = glyph_weight(ch[:1]) * light if lit else 0.0
            light_row.append((strength + glow, *((c1 * strength + c2 * glow) for c1, c2 in zip(fg, bg))))
            bright.append(min(1.0, light) if lit else 0.0)
        if bloom_on:
            # two cells a pixel: the terminal's own smoothing spreads it wider
            pixels, last = [], clear
            for x in range(0, len(light_row), 2):
                pair = light_row[x:x + 2]
                total = sum(p[0] for p in pair)
                if total <= 0.03:
                    pixels.append(last[:3] + b"\x00")
                    continue
                rgb = bytes(min(255, round(sum(p[k] for p in pair) / total)) for k in (1, 2, 3))
                # the brightest things bloom; ordinary text only just
                last = rgb + bytes([min(bloom_top, round((total / len(pair)) ** 1.6 * 20 * bloom_on))])
                pixels.append(last)
            while len(pixels) < bloom_w:
                pixels.append(clear)
            bloom = b"".join(pixels)
        if focus_on:
            # two pixels a cell: a haze over the text itself, reaching half a cell past it
            pixels, last = [], clear
            for total, red, green, blue in light_row:
                if total <= 0.03:
                    pixels.append((last[:3] + b"\x00") * 2)
                    continue
                last = bytes((min(255, round(red / total)), min(255, round(green / total)),
                              min(255, round(blue / total)), min(focus_top, round(total ** 1.2 * 10.4 * focus_on))))
                pixels.append(last * 2)
            focus = b"".join(pixels)
        if fringe_on:
            # the color beams a pixel out of line: red off the left edge of each lit run,
            # blue off its right
            edges = bytearray(clear * fringe_w)
            for x, light in enumerate(bright):
                if light < 0.2:
                    continue
                alpha = min(255, round(light * 20 * fringe_on))
                if x == 0 or bright[x - 1] < 0.2:
                    edges[12 * x:12 * x + 4] = bytes([255, 40, 40, alpha])
                if x == len(bright) - 1 or bright[x + 1] < 0.2:
                    edges[12 * x + 8:12 * x + 12] = bytes([50, 80, 255, alpha])
            fringe = bytes(edges)
        return bloom, focus, fringe

    # -- timing ----------------------------------------------------------------------------
    def animating(self):
        return bool(self.boot is not None or self.post is not None or self.typing or self.flashes
                    or self.slip or self.trails)

    def next_wake(self, now):
        """Seconds until the tube needs another frame (None: not until something changes)."""
        if not self.on:
            return None
        waits = []
        if self.slip:
            waits.append(self.slip[1] - now)
        elif self.amount("slips"):
            waits.append(self.slip_at - now)
        if self.boot is not None:
            waits.append(0.03)
        if self.post is not None:
            waits.append(0.04)
        if self.trails:
            waits.append(TRAIL[self.trail_step] * self.amount("trails") - (now - self.trail_at))
        if self.typing or self.flashes:
            waits.append(0.016)
        if self.blinking and self.amount("blink"):
            half = BLINK / self.amount("blink")
            since = now - (self.blink_epoch if self.blink_epoch is not None else now)
            waits.append(half - since % half)
        if self.pictures:
            step = self.glass.next_step(now)
            if step is not None:
                waits.append(step)
            if self.glow_due:
                waits.append(GLOW_EVERY - (now - self.glow_at))
        return max(0.015, min(waits)) if waits else None
