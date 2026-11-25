"""Run the full app inside a pyte terminal emulator and save screenshots (a tool for
tools/screenshots.py and for poking at the app by hand; not a test module itself)."""

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pyte  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from skydrop.term import Caps  # noqa: E402

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONTB = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

NAMED = {"black": (0, 0, 0), "red": (205, 0, 0), "green": (0, 205, 0), "brown": (205, 205, 0),
         "blue": (0, 0, 238), "magenta": (205, 0, 205), "cyan": (0, 205, 205), "white": (229, 229, 229),
         "default": None, "brightblack": (127, 127, 127), "brightred": (255, 0, 0), "brightgreen": (0, 255, 0),
         "brightbrown": (255, 255, 0), "brightblue": (92, 92, 255), "brightmagenta": (255, 0, 255),
         "brightcyan": (0, 255, 255), "brightwhite": (255, 255, 255)}


# blocks a terminal draws itself, as rectangles (fractions of the cell)
SHAPES = {
    "\U0001FB13": [(0, 1 / 3, 0.5, 1)],                       # sextant-35: left half, lower two thirds
    "\U0001FB03": [(0, 1 / 3, 0.5, 2 / 3)],                   # sextant-3: left half, the middle
    "\U0001FB39": [(0, 1 / 3, 1, 1)],                         # sextant-3456: the lower two thirds
    "\U0001FB7E": [(7 / 8, 0, 1, 1), (0, 0, 1, 1 / 8)],       # right and upper one eighth
    "\U0001FB7F": [(7 / 8, 0, 1, 1), (0, 7 / 8, 1, 1)],       # right and lower one eighth
    "▌": [(0, 0, 0.5, 1)], "▖": [(0, 0.5, 0.5, 1)], "▊": [(0, 0, 0.75, 1)], "▕": [(7 / 8, 0, 1, 1)],
    "▆": [(0, 0.25, 1, 1)],
    # the eighth blocks the area and bar charts are drawn in, as a terminal draws them
    "█": [(0, 0, 1, 1)], "▉": [(0, 0, 7 / 8, 1)], "▁": [(0, 7 / 8, 1, 1)], "▂": [(0, 6 / 8, 1, 1)],
    "▃": [(0, 5 / 8, 1, 1)], "▄": [(0, 0.5, 1, 1)], "▅": [(0, 3 / 8, 1, 1)], "▇": [(0, 1 / 8, 1, 1)],
}
# a braille cell's dots, [row][column], which the terminal draws itself too (the test font may have none)
BRAILLE = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))


def color(c, default):
    if c in (None, "default"):
        return default
    if c in NAMED:
        return NAMED[c] or default
    if isinstance(c, str) and len(c) == 6:
        try:
            return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return default
    return default


class FakeTerm:
    def __init__(self, cols=160, rows=48, truecolor=True):
        self.cols, self.rows = cols, rows
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.Stream(self.screen)
        self.caps = Caps()
        self.caps.truecolor = truecolor
        self.caps.colors = 256
        self.caps.utf8 = True
        self.caps.name = "pyte test terminal"
        self.out = []
        self.raw_log = []
        self.queue = []
        self.lock = threading.Lock()
        self.resized = True

    def size(self):
        return self.cols, self.rows, 0, 0

    def cell_px(self):
        return (10, 20)

    def write(self, s):
        self.out.append(s)

    def flush(self):
        data = "".join(self.out)
        self.out = []
        self.raw_log.append(data)
        self.stream.feed(data)

    def push(self, *keys):
        with self.lock:
            self.queue.extend(keys)

    def read_events(self, timeout):
        time.sleep(min(timeout, 0.05))
        with self.lock:
            ev = self.queue[:1]
            self.queue = self.queue[1:]
        if self.resized:
            self.resized = False
            ev = ["KEY_RESIZE"] + ev
        return ev

    def install_signals(self):
        pass

    def set_mouse(self, on):
        pass

    def set_motion(self, on):
        pass

    def wake(self):
        pass

    def leave(self):
        pass

    def suspend(self):
        pass

    def resize(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.screen.resize(rows, cols)
        self.resized = True

    def text(self):
        return "\n".join(self.screen.display)

    def snapshot(self, path, scale=1):
        cw, ch = 9 * scale, 18 * scale
        img = Image.new("RGB", (self.cols * cw, self.rows * ch), (0, 0, 0))
        d = ImageDraw.Draw(img)
        f = ImageFont.truetype(FONT, 14 * scale)
        fb = ImageFont.truetype(FONTB, 14 * scale)
        buf = self.screen.buffer
        for y in range(self.rows):
            row = buf[y]
            for x in range(self.cols):
                c = row[x]
                fg = color(c.fg, (229, 229, 229))
                bg = color(c.bg, (0, 0, 0))
                if c.reverse:
                    fg, bg = bg, fg
                d.rectangle([x * cw, y * ch, (x + 1) * cw - 1, (y + 1) * ch - 1], fill=bg)
                if c.data and c.data != " ":
                    ch_ = c.data
                    if ch_ in SHAPES:           # drawn by the terminal itself, as kitty and Ghostty do
                        for fx0, fy0, fx1, fy1 in SHAPES[ch_]:
                            d.rectangle([x * cw + round(fx0 * cw), y * ch + round(fy0 * ch),
                                         x * cw + round(fx1 * cw) - 1, y * ch + round(fy1 * ch) - 1], fill=fg)
                    elif 0x2800 < ord(ch_) <= 0x28FF:     # braille, dot by dot, as kitty draws it
                        bits, r = ord(ch_) - 0x2800, max(1.0, min(cw, ch) * 0.13)
                        for row_, pair in enumerate(BRAILLE):
                            for col_, bit in enumerate(pair):
                                if bits & bit:
                                    cx, cy = x * cw + (2 * col_ + 1) * cw / 4, y * ch + (2 * row_ + 1) * ch / 8
                                    d.ellipse([cx - r, cy - r, cx + r - 1, cy + r - 1], fill=fg)
                    elif ch_ in "▀▄█":
                        if ch_ == "█":
                            d.rectangle([x * cw, y * ch, (x + 1) * cw - 1, (y + 1) * ch - 1], fill=fg)
                        elif ch_ == "▀":
                            d.rectangle([x * cw, y * ch, (x + 1) * cw - 1, y * ch + ch // 2 - 1], fill=fg)
                        else:
                            d.rectangle([x * cw, y * ch + ch // 2, (x + 1) * cw - 1, (y + 1) * ch - 1], fill=fg)
                    else:
                        d.text((x * cw, y * ch), ch_, font=fb if c.bold else f, fill=fg)
        img.save(path)
        return img


def run_app(cfg_overrides=None, cols=160, rows=48, script=(), truecolor=True, shots_dir=None,
            home=None, wait_data=25, term=None):
    """script: list of (delay_before_s, key_or_'SNAP:name')"""
    import tempfile
    shots_dir = shots_dir or os.path.join(tempfile.gettempdir(), "skydrop-shots")
    home = home or os.path.join(tempfile.gettempdir(), "skydrop-test-home")
    os.environ["HOME"] = home
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    os.environ["XDG_CACHE_HOME"] = os.path.join(home, ".cache")
    os.makedirs(shots_dir, exist_ok=True)
    from skydrop.config import load_config
    from skydrop.store import DataStore
    from skydrop.app import App
    cfg = load_config()
    cfg.update(cfg_overrides or {})
    store = DataStore(cfg, persist=False)
    term = term or FakeTerm(cols, rows, truecolor)
    app = App(term, cfg, store, 0, None)
    term.app = app
    done = threading.Event()
    errors = []

    def runner():
        try:
            app.run()
        except Exception:  # pragma: no cover
            import traceback
            errors.append(traceback.format_exc())
        finally:
            done.set()

    th = threading.Thread(target=runner, daemon=True)
    th.start()
    t0 = time.time()
    while time.time() - t0 < wait_data and (store.busy or not store.last_done):
        time.sleep(0.2)
    time.sleep(1.0)
    for delay, key in script:
        time.sleep(delay)
        if isinstance(key, str) and key.startswith("SNAP:"):
            name = key[5:]
            term.snapshot(os.path.join(shots_dir, f"{name}.png"))
            with open(os.path.join(shots_dir, f"{name}.txt"), "w") as fh:
                fh.write(term.text())
        elif isinstance(key, str) and key.startswith("RESIZE:"):
            c, r = key[7:].split("x")
            term.resize(int(c), int(r))
        elif callable(key):
            for k in key(app, term) or []:
                term.push(k)
        else:
            term.push(key)
    term.push("q")
    done.wait(5)
    return app, term, errors
