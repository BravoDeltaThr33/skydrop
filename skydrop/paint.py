"""Drawing skydrop's window: the cells of a vt.Screen in a monospace font, the way a
GPU terminal such as Ghostty or kitty draws them, with the kitty pictures over them.

Like those terminals it draws the box-drawing characters, the block elements, the
sextants and the other blocks from Unicode's Symbols for Legacy Computing, and braille,
itself - exact to the pixel and joined across cells - rather than taking them from the
font. Text comes from the window's font (JetBrains Mono, Ghostty's own, or DejaVu Sans
Mono, both in skydrop/fonts), and a character the font lacks from DejaVu Sans Mono,
DejaVu Sans, then the system's symbol and emoji fonts.

The cell size and those characters follow Ghostty's font code, rewritten in Python
(Ghostty's MIT license is in skydrop/fonts/LICENSE-Ghostty.txt).

Painter.paint() brings its picture up to date with the screen and says which rows of
pixels changed; the window shows those.
"""

import math
import os
import struct
import threading
import unicodedata
from collections import OrderedDict

from PIL import Image, ImageChops, ImageDraw, ImageFont

from . import vt
from .gfx import AFFINE, BILINEAR, BOX, BUNDLED_FONTS, LANCZOS

# Settings, Display: Window font - (label, regular file, bold file)
FONTS = {
    "jetbrains": ("JetBrains Mono", "JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf"),
    "dejavu": ("DejaVu Sans Mono", "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf"),
}
FONT_ORDER = ("jetbrains", "dejavu")
SIZE_RANGE = (6.0, 48.0)
DEFAULT_SIZE = 12.0                 # points
DIM_SHARE = 0.5                     # faint text: its color half way to the background (Ghostty's)
# where a character the fonts lack is looked for, after skydrop's own
SYSTEM_FALLBACKS = ("seguisym.ttf", "segoeui.ttf", "seguiemj.ttf", "NotoSansSymbols2-Regular.ttf",
                    "NotoSansSymbols-Regular.ttf", "Symbola.ttf", "NotoSansMath-Regular.ttf",
                    "NotoColorEmoji.ttf", "unifont.ttf")
COLOR_FONTS = ("seguiemj.ttf", "NotoColorEmoji.ttf")
BOLD_FILES = {"JetBrainsMono-Regular.ttf": "JetBrainsMono-Bold.ttf", "DejaVuSansMono.ttf": "DejaVuSansMono-Bold.ttf",
              "DejaVuSans.ttf": "DejaVuSans-Bold.ttf", "segoeui.ttf": "segoeuib.ttf"}


def font_key(name):
    name = str(name or "").strip().lower()
    for key, (label, *_rest) in FONTS.items():
        if name in (key, label.lower()):
            return key
    return "jetbrains"


def font_size(value):
    try:
        size = float(value)
    except (TypeError, ValueError):
        return DEFAULT_SIZE
    if math.isnan(size):
        return DEFAULT_SIZE
    return max(SIZE_RANGE[0], min(SIZE_RANGE[1], size))


# ---------------------------------------------------------------------------- font files
_FOUND = {}


def find_font(name):
    """A font file by its file name: skydrop's own first, then the system's."""
    if name in _FOUND:
        return _FOUND[name]
    dirs = [BUNDLED_FONTS]
    try:
        from .osdeps import font_dirs
        dirs += font_dirs()
    except ImportError:
        pass
    found = None
    low = name.lower()
    for top in dirs:
        path = os.path.join(top, name)
        if os.path.isfile(path):
            found = path
            break
    if found is None:
        for top in dirs[1:]:
            for folder, _dirs, files in os.walk(top):
                hit = next((f for f in files if f.lower() == low), None)
                if hit:
                    found = os.path.join(folder, hit)
                    break
            if found:
                break
    _FOUND[name] = found
    return found


def _table(data, base, tag):
    """(offset, length) of a table in a TrueType/OpenType font, or None."""
    try:
        count = struct.unpack_from(">H", data, base + 4)[0]
        for i in range(count):
            rec = base + 12 + 16 * i
            if data[rec:rec + 4] == tag:
                off, length = struct.unpack_from(">II", data, rec + 8)
                return off, length
    except struct.error:
        return None
    return None


def font_facts(path):
    """What a font file says about itself: the characters it has (its cmap), its units
    per em, its height (hhea and OS/2), and where its underline and strikeout go. {} when
    it can't be read."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return {}
    base = 0
    if data[:4] == b"ttcf":
        try:
            base = struct.unpack_from(">I", data, 12)[0]
        except struct.error:
            return {}
    facts = {"chars": set(), "upem": 1000, "underline": None, "strike": None, "hhea": None,
             "typo": None, "win": None, "use_typo": False}
    try:
        head = _table(data, base, b"head")
        if head:
            facts["upem"] = struct.unpack_from(">H", data, head[0] + 18)[0] or 1000
        hhea = _table(data, base, b"hhea")
        if hhea:
            facts["hhea"] = struct.unpack_from(">hhh", data, hhea[0] + 4)
        post = _table(data, base, b"post")
        if post:
            pos, thick = struct.unpack_from(">hh", data, post[0] + 8)
            if thick > 0:
                facts["underline"] = (pos, thick)
        os2 = _table(data, base, b"OS/2")
        if os2 and os2[1] >= 30:
            size, pos = struct.unpack_from(">hh", data, os2[0] + 26)
            if size > 0:
                facts["strike"] = (pos, size)
        if os2 and os2[1] >= 78:
            facts["use_typo"] = bool(struct.unpack_from(">H", data, os2[0] + 62)[0] & 0x80)
            facts["typo"] = struct.unpack_from(">hhh", data, os2[0] + 68)
            facts["win"] = struct.unpack_from(">HH", data, os2[0] + 74)
        cmap = _table(data, base, b"cmap")
        if cmap:
            facts["chars"] = _cmap_chars(data, cmap[0])
    except struct.error:
        pass
    return facts


def vertical_metrics(facts):
    """(ascent, descent, line gap) in font units, chosen as Ghostty chooses them: the
    OS/2 typographic ones when the font asks for them, else hhea's, else OS/2's, else the
    Windows ones. Descent is negative."""
    hhea, typo, win = facts.get("hhea"), facts.get("typo"), facts.get("win")
    if typo is None:
        return hhea or (800, -200, 0)
    if facts.get("use_typo"):
        return typo
    if hhea and (hhea[0] or hhea[1]):
        return hhea
    if typo[0] or typo[1]:
        return typo
    if win:
        return win[0], -win[1], 0
    return 800, -200, 0


def _cmap_chars(data, cmap):
    count = struct.unpack_from(">H", data, cmap + 2)[0]
    best, rank = None, -1
    for i in range(count):
        plat, enc, off = struct.unpack_from(">HHI", data, cmap + 4 + 8 * i)
        fmt = struct.unpack_from(">H", data, cmap + off)[0]
        score = {(3, 10, 12): 5, (0, 4, 12): 5, (0, 6, 12): 4, (3, 1, 4): 3, (0, 3, 4): 3,
                 (0, 1, 4): 2, (3, 0, 4): 1}.get((plat, enc, fmt), 0)
        if fmt == 12 and score == 0:
            score = 4
        elif fmt == 4 and score == 0:
            score = 1
        if score > rank:
            best, rank = cmap + off, score
    chars = set()
    if best is None or rank <= 0:
        return chars
    fmt = struct.unpack_from(">H", data, best)[0]
    if fmt == 12:
        groups = struct.unpack_from(">I", data, best + 12)[0]
        for g in range(groups):
            start, end, glyph = struct.unpack_from(">III", data, best + 16 + 12 * g)
            if end - start > 0x30000:
                continue
            first = start if glyph else start + 1
            chars.update(range(first, end + 1))
        return chars
    segs = struct.unpack_from(">H", data, best + 6)[0] // 2
    ends = best + 14
    starts = ends + 2 * segs + 2
    deltas = starts + 2 * segs
    ranges = deltas + 2 * segs
    for s in range(segs):
        end = struct.unpack_from(">H", data, ends + 2 * s)[0]
        start = struct.unpack_from(">H", data, starts + 2 * s)[0]
        delta = struct.unpack_from(">h", data, deltas + 2 * s)[0]
        roff = struct.unpack_from(">H", data, ranges + 2 * s)[0]
        if start == 0xFFFF:
            continue
        for c in range(start, end + 1):
            if roff == 0:
                glyph = (c + delta) & 0xFFFF
            else:
                at = ranges + 2 * s + roff + 2 * (c - start)
                if at + 2 > len(data):
                    continue
                glyph = struct.unpack_from(">H", data, at)[0]
                if glyph:
                    glyph = (glyph + delta) & 0xFFFF
            if glyph:
                chars.add(c)
    return chars


class Face:
    """One font file at the window's size."""

    def __init__(self, path, px, color=False):
        self.path = path
        self.color = color
        facts = font_facts(path)
        self.chars = facts.get("chars") or set()
        self.facts = facts
        self.font = None
        self.scale = 1.0
        try:
            self.font = ImageFont.truetype(path, px)
        except (OSError, ValueError, TypeError):
            try:
                self.font = ImageFont.truetype(path, max(1, int(round(px))))
            except (OSError, ValueError):
                if color:          # a bitmap emoji font comes in one size: draw it there and shrink
                    try:
                        self.font = ImageFont.truetype(path, 109)
                        self.scale = px / 109
                    except (OSError, ValueError):
                        self.font = None

    def has(self, ch):
        return self.font is not None and ord(ch) in self.chars


def _face(cache, path, px, color=False):
    key = (path, px)
    face = cache.get(key)
    if face is None:
        face = cache[key] = Face(path, px, color)
    return face


# ---------------------------------------------------------------------------- the sprites
# The characters the window draws itself, sized and placed as Ghostty draws them
# (src/font/sprite in Ghostty): a light line is the font's underline thickness, a
# heavy one twice that, and blocks are cut with Ghostty's rounding.

def zround(x):
    """Round halves away from zero, as Zig's @round does (Ghostty's sprites use it)."""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(0.5 - x))


# box drawing: the weight of a line's four arms (up, right, down, left): 1 light, 2 heavy, 3 double
LIGHT, HEAVY, DOUBLE = 1, 2, 3
_WEIGHT = {"LIGHT": LIGHT, "SINGLE": LIGHT, "HEAVY": HEAVY, "DOUBLE": DOUBLE}
_ARMS = {"UP": (0,), "RIGHT": (1,), "DOWN": (2,), "LEFT": (3,), "HORIZONTAL": (1, 3), "VERTICAL": (0, 2)}
_DASHES = (("DOUBLE DASH", 2), ("TRIPLE DASH", 3), ("QUADRUPLE DASH", 4))


def box_arms(ch):
    """What a box-drawing character (U+2500-U+257F) is, from its Unicode name:
    ("line", (up, right, down, left)) with each arm's weight, ("dash", (count, weight,
    vertical)), ("arc", corner) or ("diagonal", which); None for anything else.

    The names put each arm's weight before or after it - "LIGHT DOWN AND RIGHT",
    "DOWN LIGHT AND RIGHT HEAVY", "UP HEAVY AND RIGHT DOWN LIGHT" - and a part after AND
    with no weight of its own takes the one before it."""
    name = unicodedata.name(ch, "")
    if not name.startswith("BOX DRAWINGS "):
        return None
    rest = name[len("BOX DRAWINGS "):]
    for words, count in _DASHES:
        if words in rest:
            weight = HEAVY if rest.startswith("HEAVY") else LIGHT
            return "dash", (count, weight, rest.endswith("VERTICAL"))
    if "DIAGONAL" in rest:
        return "diagonal", ord(ch)
    if " ARC " in rest:
        return "arc", ord(ch)
    arms = [0, 0, 0, 0]
    weight = LIGHT
    for part in rest.split(" AND "):
        words = part.split()
        weight = next((_WEIGHT[w] for w in words if w in _WEIGHT), weight)
        for w in words:
            for arm in _ARMS.get(w, ()):
                arms[arm] = weight
    if not any(arms):
        return None
    return "line", tuple(arms)


# U+2580-U+2595 and U+1FB70-U+1FB8B, as Ghostty cuts them: a list of
# (horizontal place, vertical place, width, height), the sizes as fractions of the cell;
# or for the one-eighth strips of U+1FB70-U+1FB7B ("col"/"row", which eighth)
_L, _R, _T, _B = "left", "right", "top", "bottom"
_BLOCK_SHAPES = {0x2580: [(_L, _T, 1, 1 / 2)], 0x2588: [(_L, _T, 1, 1)], 0x2590: [(_R, _T, 1 / 2, 1)],
                 0x2594: [(_L, _T, 1, 1 / 8)], 0x2595: [(_R, _T, 1 / 8, 1)]}
for _k in range(1, 8):
    _BLOCK_SHAPES[0x2580 + _k] = [(_L, _B, 1, _k / 8)]                 # lower k eighths
    _BLOCK_SHAPES[0x2590 - _k] = [(_L, _T, _k / 8, 1)]                 # left k eighths
for _k in range(6):
    _BLOCK_SHAPES[0x1FB70 + _k] = [("col", _k + 1)]                    # vertical one eighth block 2-7
    _BLOCK_SHAPES[0x1FB76 + _k] = [("row", _k + 1)]                    # horizontal one eighth block 2-7
_BLOCK_SHAPES.update({
    0x1FB7C: [(_L, _T, 1 / 8, 1), (_L, _B, 1, 1 / 8)], 0x1FB7D: [(_L, _T, 1 / 8, 1), (_L, _T, 1, 1 / 8)],
    0x1FB7E: [(_R, _T, 1 / 8, 1), (_L, _T, 1, 1 / 8)], 0x1FB7F: [(_R, _T, 1 / 8, 1), (_L, _B, 1, 1 / 8)],
    0x1FB80: [(_L, _T, 1, 1 / 8), (_L, _B, 1, 1 / 8)],
    0x1FB81: [(_L, _T, 1, 1 / 8), ("row", 2), ("row", 4), (_L, _B, 1, 1 / 8)],
    0x1FB82: [(_L, _T, 1, 2 / 8)], 0x1FB83: [(_L, _T, 1, 3 / 8)], 0x1FB84: [(_L, _T, 1, 5 / 8)],
    0x1FB85: [(_L, _T, 1, 6 / 8)], 0x1FB86: [(_L, _T, 1, 7 / 8)],
    0x1FB87: [(_R, _T, 2 / 8, 1)], 0x1FB88: [(_R, _T, 3 / 8, 1)], 0x1FB89: [(_R, _T, 5 / 8, 1)],
    0x1FB8A: [(_R, _T, 6 / 8, 1)], 0x1FB8B: [(_R, _T, 7 / 8, 1)],
})
# U+2596-U+259F: quadrants (top left, top right, bottom left, bottom right)
_QUADRANTS = {0x2596: (0, 0, 1, 0), 0x2597: (0, 0, 0, 1), 0x2598: (1, 0, 0, 0), 0x2599: (1, 0, 1, 1),
              0x259A: (1, 0, 0, 1), 0x259B: (1, 1, 1, 0), 0x259C: (1, 1, 0, 1), 0x259D: (0, 1, 0, 0),
              0x259E: (0, 1, 1, 0), 0x259F: (0, 1, 1, 1)}
_SHADES = {0x2591: 0x40, 0x2592: 0x80, 0x2593: 0xC0}
SEXTANT_BASE = 0x1FB00
SUPER = 4                           # curves are drawn this many times larger, then shrunk


def sextant_bits(code):
    """The six parts (bit 0 top left, 1 top right, 2 middle left ... 5 bottom right) of a
    sextant character U+1FB00-U+1FB3B: every pattern but the four that have their own
    characters elsewhere (empty, the left and right halves, full)."""
    n = code - SEXTANT_BASE + 1
    if n >= 21:
        n += 1
    if n >= 42:
        n += 1
    return n


def is_sprite(ch):
    o = ord(ch)
    return (0x2500 <= o <= 0x259F or 0x2800 <= o <= 0x28FF or SEXTANT_BASE <= o <= 0x1FB3B or
            0x1FB70 <= o <= 0x1FB8B)


class Sprites:
    """The characters the window draws itself, as masks the size of a cell."""

    def __init__(self, cw, ch, thin):
        self.cw, self.ch = cw, ch
        self.thin = max(1, thin)                     # a light line's width in pixels
        self.thick = 2 * self.thin                   # a heavy line's

    def mask(self, ch):
        o = ord(ch)
        if o in _BLOCK_SHAPES:
            return self._blocks(_BLOCK_SHAPES[o])
        if o in _QUADRANTS:
            return self._quadrants(_QUADRANTS[o])
        if o in _SHADES:
            return Image.new("L", (self.cw, self.ch), _SHADES[o])
        if SEXTANT_BASE <= o <= 0x1FB3B:
            return self._sextant(sextant_bits(o))
        if 0x2800 <= o <= 0x28FF:
            return self._braille(o - 0x2800)
        if 0x2500 <= o <= 0x257F:
            got = box_arms(ch)
            if got is None:
                return None
            kind, what = got
            if kind == "line":
                return self._lines(*what)
            if kind == "dash":
                return self._dashes(*what)
            if kind == "arc":
                return self._arc(what)
            return self._diagonal(what)
        return None

    def _canvas(self, boxes):
        """A cell with these boxes (x0, y0, x1, y1; the far edges not included) filled."""
        img = Image.new("L", (self.cw, self.ch), 0)
        d = ImageDraw.Draw(img)
        for x0, y0, x1, y1 in boxes:
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(self.cw, x1), min(self.ch, y1)
            if x1 > x0 and y1 > y0:
                d.rectangle([x0, y0, x1 - 1, y1 - 1], fill=255)
        return img

    def _blocks(self, shapes):
        W, H = self.cw, self.ch
        boxes = []
        for shape in shapes:
            if shape[0] == "col":                    # Ghostty: x = round(n * W / 8), width round(W / 8)
                x, w = zround(shape[1] * W / 8), zround(W / 8)
                boxes.append((x, 0, x + w, H))
                continue
            if shape[0] == "row":
                y, h = zround(shape[1] * H / 8), zround(H / 8)
                boxes.append((0, y, W, y + h))
                continue
            across, down, fw, fh = shape
            w, h = zround(W * fw), zround(H * fh)
            x = W - w if across == _R else 0
            y = H - h if down == _B else 0
            boxes.append((x, y, x + w, y + h))
        return self._canvas(boxes)

    def _quadrants(self, quads):
        W, H = self.cw, self.ch
        cx, cy = W // 2 + W % 2, H // 2 + H % 2
        tl, tr, bl, br = quads
        boxes = []
        if tl:
            boxes.append((0, 0, cx, cy))
        if tr:
            boxes.append((cx, 0, W, cy))
        if bl:
            boxes.append((0, cy, cx, H))
        if br:
            boxes.append((cx, cy, W, H))
        return self._canvas(boxes)

    def _sextant(self, bits):
        W, H = self.cw, self.ch
        half = zround(W / 2)
        xs = ((0, half), (W - half, W))
        thirds = (0, zround(H / 3), zround(H * 2 / 3), H)
        boxes = []
        for k in range(6):
            if bits >> k & 1:
                col, row = k % 2, k // 2
                boxes.append((xs[col][0], thirds[row], xs[col][1], thirds[row + 1]))
        return self._canvas(boxes)

    def _braille(self, bits):
        """Square dots on a grid, spaced and sized the way Ghostty works them out."""
        W, H = self.cw, self.ch
        w = min(W // 4, H // 8)
        x_spacing, y_spacing = W // 4, H // 8
        x_margin, y_margin = x_spacing // 2, y_spacing // 2
        x_left = W - 2 * x_margin - x_spacing - 2 * w
        y_left = H - 2 * y_margin - 3 * y_spacing - 4 * w
        if x_left >= 2 and y_left >= 4 and w == 0:            # first a dot at least a pixel big
            w, x_left, y_left = w + 1, x_left - 2, y_left - 4
        if x_left >= 2 and x_margin == 0:                     # then a margin
            x_margin, x_left = 1, x_left - 2
        if y_left >= 2 and y_margin == 0:
            y_margin, y_left = 1, y_left - 2
        if x_left >= 1:                                       # then more space between dots
            x_spacing, x_left = x_spacing + 1, x_left - 1
        if y_left >= 3:
            y_spacing, y_left = y_spacing + 1, y_left - 3
        if x_left >= 2:                                       # then wider margins
            x_margin, x_left = x_margin + 1, x_left - 2
        if y_left >= 2:
            y_margin, y_left = y_margin + 1, y_left - 2
        if x_left >= 2 and y_left >= 4:                       # last, bigger dots
            w += 1
        xs = (x_margin, x_margin + w + x_spacing)
        ys = [y_margin + i * (w + y_spacing) for i in range(4)]
        dots = ((0x01, 0, 0), (0x02, 0, 1), (0x04, 0, 2), (0x08, 1, 0), (0x10, 1, 1), (0x20, 1, 2),
                (0x40, 0, 3), (0x80, 1, 3))
        return self._canvas([(xs[c], ys[r], xs[c] + w, ys[r] + w) for bit, c, r in dots if bits & bit])

    # -- lines ---------------------------------------------------------------------------
    def _geometry(self):
        """Where light, heavy and double lines run across a cell (Ghostty's linesChar)."""
        W, H, lt, hv = self.cw, self.ch, self.thin, self.thick
        g = {}
        g["h_light_top"] = max(0, (H - lt) // 2)
        g["h_light_bottom"] = g["h_light_top"] + lt
        g["h_heavy_top"] = max(0, (H - hv) // 2)
        g["h_heavy_bottom"] = g["h_heavy_top"] + hv
        g["h_double_top"] = max(0, g["h_light_top"] - lt)
        g["h_double_bottom"] = g["h_light_bottom"] + lt
        g["v_light_left"] = max(0, (W - lt) // 2)
        g["v_light_right"] = g["v_light_left"] + lt
        g["v_heavy_left"] = max(0, (W - hv) // 2)
        g["v_heavy_right"] = g["v_heavy_left"] + hv
        g["v_double_left"] = max(0, g["v_light_left"] - lt)
        g["v_double_right"] = g["v_light_right"] + lt
        return g

    def _lines(self, up, right, down, left):
        W, H = self.cw, self.ch
        g = self._geometry()
        # how far each arm runs into the middle, so the joins come out square
        if HEAVY in (left, right):
            up_bottom, down_top = g["h_heavy_bottom"], g["h_heavy_top"]
        else:
            dbl = DOUBLE in (left, right)
            if left != right or down == up:
                up_bottom = g["h_double_bottom"] if dbl else g["h_light_bottom"]
            elif not left and not right:
                up_bottom = g["h_light_bottom"]
            else:
                up_bottom = g["h_light_top"]
            if left != right or up == down:
                down_top = g["h_double_top"] if dbl else g["h_light_top"]
            elif not left and not right:
                down_top = g["h_light_top"]
            else:
                down_top = g["h_light_bottom"]
        if HEAVY in (up, down):
            left_right, right_left = g["v_heavy_right"], g["v_heavy_left"]
        else:
            dbl = DOUBLE in (up, down)
            if up != down or left == right:
                left_right = g["v_double_right"] if dbl else g["v_light_right"]
            elif not up and not down:
                left_right = g["v_light_right"]
            else:
                left_right = g["v_light_left"]
            if up != down or right == left:
                right_left = g["v_double_left"] if dbl else g["v_light_left"]
            elif not up and not down:
                right_left = g["v_light_left"]
            else:
                right_left = g["v_light_right"]
        boxes = []
        if up == LIGHT:
            boxes.append((g["v_light_left"], 0, g["v_light_right"], up_bottom))
        elif up == HEAVY:
            boxes.append((g["v_heavy_left"], 0, g["v_heavy_right"], up_bottom))
        elif up == DOUBLE:
            boxes.append((g["v_double_left"], 0, g["v_light_left"], g["h_light_top"] if left == DOUBLE else up_bottom))
            boxes.append((g["v_light_right"], 0, g["v_double_right"], g["h_light_top"] if right == DOUBLE else up_bottom))
        if right == LIGHT:
            boxes.append((right_left, g["h_light_top"], W, g["h_light_bottom"]))
        elif right == HEAVY:
            boxes.append((right_left, g["h_heavy_top"], W, g["h_heavy_bottom"]))
        elif right == DOUBLE:
            boxes.append((g["v_light_right"] if up == DOUBLE else right_left, g["h_double_top"], W, g["h_light_top"]))
            boxes.append((g["v_light_right"] if down == DOUBLE else right_left, g["h_light_bottom"], W, g["h_double_bottom"]))
        if down == LIGHT:
            boxes.append((g["v_light_left"], down_top, g["v_light_right"], H))
        elif down == HEAVY:
            boxes.append((g["v_heavy_left"], down_top, g["v_heavy_right"], H))
        elif down == DOUBLE:
            boxes.append((g["v_double_left"], g["h_light_bottom"] if left == DOUBLE else down_top, g["v_light_left"], H))
            boxes.append((g["v_light_right"], g["h_light_bottom"] if right == DOUBLE else down_top, g["v_double_right"], H))
        if left == LIGHT:
            boxes.append((0, g["h_light_top"], left_right, g["h_light_bottom"]))
        elif left == HEAVY:
            boxes.append((0, g["h_heavy_top"], left_right, g["h_heavy_bottom"]))
        elif left == DOUBLE:
            boxes.append((0, g["h_double_top"], g["v_light_left"] if up == DOUBLE else left_right, g["h_light_top"]))
            boxes.append((0, g["h_light_bottom"], g["v_light_left"] if down == DOUBLE else left_right, g["h_double_bottom"]))
        return self._canvas(boxes)

    def _dashes(self, count, weight, vertical):
        """┄ ┆ ┈ ┊ ╌ ╎ and the heavy ones: `count` dashes with half a gap at each end, so
        they tile into an even line."""
        W, H = self.cw, self.ch
        length = H if vertical else W
        if length < 2 * count:                       # too small for gaps: a plain light line
            return self._lines(*((LIGHT, 0, LIGHT, 0) if vertical else (0, LIGHT, 0, LIGHT)))
        thick = self.thick if weight == HEAVY else self.thin
        gap = min(max(4, self.thin), length // (2 * count))
        total = length - count * gap
        dash, extra = total // count, total % count
        across = max(0, ((W if vertical else H) - thick) // 2)
        boxes = []
        pos = gap // 2
        for _ in range(count):
            end = pos + dash
            if extra > 0:                            # the pixels left over go to the first dashes
                extra -= 1
                end += 1
            boxes.append((across, pos, across + thick, end) if vertical else (pos, across, end, across + thick))
            pos = end + gap
        return self._canvas(boxes)

    def _stroke(self, points, width):
        """A line through these points (in cell pixels, a pixel's left or top edge at its
        number), `width` pixels wide with round joins, drawn SUPER times finer and shrunk:
        every fine pixel whose middle is within width/2 of the line is in."""
        s = SUPER
        W, H = self.cw * s, self.ch * s
        buf = bytearray(W * H)
        half = width / 2
        reach = half * half + 1e-9
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            dx, dy = x1 - x0, y1 - y0
            seg = dx * dx + dy * dy
            lo_x = max(0, int(math.floor((min(x0, x1) - half) * s)))
            hi_x = min(W - 1, int(math.ceil((max(x0, x1) + half) * s)))
            lo_y = max(0, int(math.floor((min(y0, y1) - half) * s)))
            hi_y = min(H - 1, int(math.ceil((max(y0, y1) + half) * s)))
            for fy in range(lo_y, hi_y + 1):
                py = (fy + 0.5) / s
                row = fy * W
                for fx in range(lo_x, hi_x + 1):
                    if buf[row + fx]:
                        continue
                    px = (fx + 0.5) / s
                    t = ((px - x0) * dx + (py - y0) * dy) / seg if seg else 0.0
                    t = 0.0 if t < 0 else 1.0 if t > 1 else t
                    ex, ey = x0 + t * dx - px, y0 + t * dy - py
                    if ex * ex + ey * ey <= reach:
                        buf[row + fx] = 255
        return Image.frombytes("L", (W, H), bytes(buf)).resize((self.cw, self.ch), BOX)

    def _arc(self, code):
        """╭ ╮ ╯ ╰: the two arms joined by a curve (Ghostty's: a Bezier whose middle
        points sit a quarter of the radius from the corner)."""
        W, H, t = self.cw, self.ch, self.thin
        cx = max(0, (W - t) // 2) + t / 2              # the middle of a light line, across
        cy = max(0, (H - t) // 2) + t / 2              # and down
        r = min(W, H) / 2
        k = 0.25
        sx, sy = {0x256D: (1, 1), 0x256E: (-1, 1), 0x256F: (-1, -1), 0x2570: (1, -1)}[code]
        p0, p3 = (cx, cy + sy * r), (cx + sx * r, cy)
        c1, c2 = (cx, cy + sy * k * r), (cx + sx * k * r, cy)
        pts = [(cx, H + 1 if sy > 0 else -1), p0]
        for i in range(1, 25):
            u = i / 24
            a, b, c, e = (1 - u) ** 3, 3 * (1 - u) ** 2 * u, 3 * (1 - u) * u * u, u ** 3
            pts.append((a * p0[0] + b * c1[0] + c * c2[0] + e * p3[0], a * p0[1] + b * c1[1] + c * c2[1] + e * p3[1]))
        pts.append((W + 1 if sx > 0 else -1, cy))
        return self._stroke(pts, t)

    def _diagonal(self, code):
        """╱ ╲ ╳: corner to corner, run a little past the corners so cells join."""
        W, H = self.cw, self.ch
        slope_x, slope_y = min(1.0, W / H), min(1.0, H / W)
        img = Image.new("L", (W, H), 0)
        if code in (0x2571, 0x2573):
            img = ImageChops.lighter(img, self._stroke([(W + 0.5 * slope_x, -0.5 * slope_y),
                                                       (-0.5 * slope_x, H + 0.5 * slope_y)], self.thin))
        if code in (0x2572, 0x2573):
            img = ImageChops.lighter(img, self._stroke([(-0.5 * slope_x, -0.5 * slope_y),
                                                       (W + 0.5 * slope_x, H + 0.5 * slope_y)], self.thin))
        return img


# ---------------------------------------------------------------------------- the painter
PIECE = (128, 64)                   # a big see-through picture is laid on in pieces this size...
PIECE_MIN = 256 * 256               # ...when it is at least this big
PIECE_SHARE = 0.8                   # and its pieces with something in them cover less than this of it


def pieces(img):
    """A big, partly see-through picture (RGBA) in the pieces that show something:
    [(x, y, piece)], or None when it isn't worth cutting up (laying a picture on costs the
    same for its clear pixels as for the rest)."""
    w, h = img.size
    if img.mode != "RGBA" or w * h < PIECE_MIN:
        return None
    alpha = img.getchannel("A")
    pw, ph = PIECE
    found, area = [], 0
    for y in range(0, h, ph):
        for x in range(0, w, pw):
            box = alpha.crop((x, y, min(w, x + pw), min(h, y + ph))).getbbox()
            if box:
                found.append((x + box[0], y + box[1], x + box[2], y + box[3]))
                area += (box[2] - box[0]) * (box[3] - box[1])
    if area > PIECE_SHARE * w * h:
        return None
    return [(x0, y0, img.crop((x0, y0, x1, y1))) for x0, y0, x1, y1 in found]


def stretch(img, size):
    """A picture stretched (or shrunk) to `size`, smoothly, as a terminal's GPU draws it.
    A see-through one has each channel stretched on its own, as kitty samples a picture
    (and Pillow's own way, through premultiplied color, takes twice as long)."""
    if img.mode == "RGBA" and size[0] * size[1] > img.size[0] * img.size[1]:
        return Image.merge("RGBA", [band.resize(size, BILINEAR) for band in img.split()])
    return img.resize(size, BILINEAR)


def _blend(a, b, t):
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


class Painter:
    """The window's picture of a vt.Screen. paint() is called after every write the app
    makes (in the app's thread); the window takes the rows that changed (rows(), row())
    from its own thread."""

    def __init__(self, font="jetbrains", size=DEFAULT_SIZE, dpi=96.0):
        self.lock = threading.RLock()
        self.font_key = font_key(font)
        self.size_pt = font_size(size)
        self.dpi = float(dpi) if dpi and dpi > 0 else 96.0
        self.default_fg, self.default_bg = vt.DEFAULT_FG, vt.DEFAULT_BG
        self.faces = {}
        self.cols = self.rows = 0
        self.text = None                 # the cells
        self.frame = None                # the cells with the pictures over them (the text itself when none)
        self.changed = set()             # rows of the frame changed since the window last took them
        self.version = 0
        self.layers = OrderedDict()      # a placement's picture as shown (see _layer)
        self.shown_layers = []           # [(what is shown where, top row, bottom row)] the last frame drew
        self.base = self.top = None      # see _compose
        self.base_keys = self.top_keys = ()
        self.base_churn = False
        self.pic_version = -1
        self.problems = []
        self.cursor = None               # (column, row, block, hollow): the cursor, when shown
        self._cursor_key = None
        self._setup_fonts()

    # -- fonts ---------------------------------------------------------------------------
    @property
    def px(self):
        return self.size_pt * self.dpi / 72.0

    def _setup_fonts(self):
        label, regular, bold = FONTS[self.font_key]
        px = self.px
        self.faces = {}
        path = find_font(regular)
        if path is None:                 # not bundled (the .deb): the other one, then any monospace font
            for key in FONT_ORDER:
                path = find_font(FONTS[key][1])
                if path:
                    label, regular, bold = FONTS[key]
                    break
        self.font_label = label
        self.regular = _face(self.faces, path, px) if path else None
        bpath = find_font(bold)
        self.bold = _face(self.faces, bpath, px) if bpath else None
        font = self.regular.font if self.regular and self.regular.font else None
        facts = self.regular.facts if self.regular else {}
        # the cell, as Ghostty works it out: the widest ASCII advance and the font's line
        # height, each rounded up; the baseline sits half the line gap and the descent
        # (rounded) above the cell's foot. A hair over a whole pixel is not a pixel more.
        upem = facts.get("upem", 1000) or 1000
        scale = px / upem
        try:
            advance = max(font.getlength(chr(c)) for c in range(32, 127))
        except (AttributeError, OSError, TypeError, ValueError):
            advance = px * 0.6
        ascent, descent, gap = (v * scale for v in vertical_metrics(facts))
        self.cw = max(1, math.ceil(advance - 0.01))
        self.ch = max(2, math.ceil(ascent - descent + gap - 0.01))
        self.baseline = self.ch - zround(gap / 2 - descent)
        # underline and strikeout: their tops and thicknesses (a light box line is the
        # underline's thickness, as in Ghostty)
        ul_pos, ul_thick = facts.get("underline") or (-0.15 * upem, 0.06 * upem)
        st_pos, st_size = facts.get("strike") or (0.25 * upem, 0.06 * upem)
        self.line = max(1, math.ceil(ul_thick * scale - 0.01))
        self.strike_line = max(1, math.ceil(st_size * scale - 0.01))
        self.underline_y = max(0, min(self.ch - self.line, zround(self.baseline - ul_pos * scale)))
        self.strike_y = max(0, min(self.ch - self.strike_line, zround(self.baseline - st_pos * scale)))
        self.sprites = Sprites(self.cw, self.ch, self.line)
        # the fonts after the window's own, for characters it lacks
        chain = []
        for name in [FONTS[key][1] for key in FONT_ORDER] + ["DejaVuSans.ttf"] + list(SYSTEM_FALLBACKS):
            p = find_font(name)
            if p and p not in chain:
                chain.append(p)
        self.chain = chain
        self.glyphs = {}
        self.emoji = {}

    def set_font(self, font=None, size=None, dpi=None):
        """Another font or size: every cell is drawn again. True when the cell size changed."""
        with self.lock:
            old = (self.cw, self.ch)
            if font is not None:
                self.font_key = font_key(font)
            if size is not None:
                self.size_pt = font_size(size)
            if dpi:
                self.dpi = float(dpi)
            self._setup_fonts()
            self.cols = self.rows = 0            # the next paint starts over
            self.layers = OrderedDict()
            self._cursor_key = None
            return (self.cw, self.ch) != old

    @property
    def cell(self):
        return self.cw, self.ch

    def _fallback_face(self, ch, bold):
        for path in self.chain:
            color = os.path.basename(path) in COLOR_FONTS
            face = _face(self.faces, path, self.px, color)
            if face.has(ch):
                if bold and not color:
                    bname = BOLD_FILES.get(os.path.basename(path))
                    bpath = find_font(bname) if bname else None
                    if bpath:
                        bface = _face(self.faces, bpath, self.px)
                        if bface.has(ch):
                            return bface
                return face
        return None

    def glyph(self, ch, attrs, cells=1):
        """The mask (L) - or for a color emoji the picture (RGBA) - that draws a character
        in `cells` cells."""
        key = (ch, attrs & (vt.BOLD | vt.ITALIC), cells)
        hit = self.glyphs.get(key)
        if hit is not None:
            return hit
        img = None
        if is_sprite(ch):
            try:
                img = self.sprites.mask(ch)
            except Exception as e:               # draw it from a font instead
                self._problem(f"sprite {ord(ch):04X}: {e}")
        if img is None:
            img = self._from_font(ch, attrs, cells)
        if len(self.glyphs) > 6000:
            self.glyphs.clear()
        self.glyphs[key] = img
        return img

    def _from_font(self, ch, attrs, cells):
        bold = bool(attrs & vt.BOLD)
        face = self.bold if bold and self.bold and self.bold.has(ch) else None
        if face is None and self.regular and self.regular.has(ch):
            face = self.regular
        fake_bold = False
        if face is None:
            picture = self._bundled_emoji(ch, cells)
            if picture is not None:
                return picture
            face = self._fallback_face(ch, bold)
            if face is not None and bold and "Bold" not in os.path.basename(face.path) and not face.color:
                fake_bold = True
        elif bold and face is self.regular:
            fake_bold = True
        if face is None:
            face = self.regular
        if face is None or face.font is None:
            return Image.new("L", (self.cw * cells, self.ch), 0)
        if face.color:
            return self._color_glyph(face, ch, cells)
        w, h = self.cw * cells, self.ch
        img = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(img)
        font = face.font
        primary = face is self.regular or face is self.bold
        x = 0.0
        try:
            adv = font.getlength(ch)
            box = font.getbbox(ch, anchor="ls")
        except (OSError, ValueError, TypeError):
            adv, box = float(self.cw), (0, -self.baseline, self.cw, 0)
        ink_w = box[2] - box[0]
        if not primary or adv > w + 0.5:
            if ink_w > w or (box[3] - box[1]) > h:
                # too big for its cells: a smaller size of the same font, as Ghostty fits symbols
                shrink = min(w / max(1, ink_w), h / max(1, box[3] - box[1]), 1.0) * 0.95
                try:
                    font = ImageFont.truetype(face.path, max(1.0, self.px * shrink))
                    adv = font.getlength(ch)
                    box = font.getbbox(ch, anchor="ls")
                except (OSError, ValueError, TypeError):
                    pass
            x = (w - (box[2] - box[0])) / 2 - box[0]
            if box[3] > self.ch - self.baseline:          # keep it inside the cell
                x_shift_y = box[3] - (self.ch - self.baseline)
            else:
                x_shift_y = 0
            if box[1] + self.baseline < 0:
                x_shift_y = box[1] + self.baseline
        else:
            x_shift_y = 0
        try:
            d.text((x, self.baseline - x_shift_y), ch, font=font, fill=255, anchor="ls")
            if fake_bold:
                d.text((x + max(1, self.px / 24), self.baseline - x_shift_y), ch, font=font, fill=255, anchor="ls")
        except (OSError, ValueError, TypeError) as e:
            self._problem(f"glyph {ord(ch):04X}: {e}")
        if attrs & vt.ITALIC:
            img = img.transform(img.size, AFFINE, (1, 0.2, -0.2 * self.baseline, 0, 1, 0), BILINEAR)
        return img

    def _bundled_emoji(self, ch, cells):
        """skydrop's own copy of an emoji it shows (the droplet in the title bar), drawn as
        Noto Color Emoji draws it on Linux."""
        path = os.path.join(BUNDLED_FONTS, f"emoji-{ord(ch):x}.png")
        if not os.path.isfile(path):
            return None
        try:
            with Image.open(path) as src:
                pic = src.convert("RGBA")
        except OSError:
            return None
        return self._fit_emoji(pic, cells)

    def _fit_emoji(self, pic, cells):
        """An emoji's picture (its whole em square) fitted to its cells, centred."""
        w, h = self.cw * cells, self.ch
        scale = min(w / pic.size[0], h / pic.size[1])
        size = (max(1, round(pic.size[0] * scale)), max(1, round(pic.size[1] * scale)))
        small = pic.resize(size, LANCZOS)
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        out.paste(small, ((w - size[0]) // 2, (h - size[1]) // 2))
        return out

    def _color_glyph(self, face, ch, cells):
        size = round(face.font.size * 1.6) + 8
        canvas = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(canvas)
        try:
            d.text((size // 2, size // 2), ch, font=face.font, embedded_color=True)
            box = face.font.getbbox(ch)
        except (OSError, ValueError, TypeError):
            return Image.new("L", (self.cw * cells, self.ch), 0)
        em = canvas.crop((size // 2 + box[0], size // 2 + box[1], size // 2 + box[2], size // 2 + box[3]))
        if em.getbbox() is None:
            return Image.new("L", (self.cw * cells, self.ch), 0)
        return self._fit_emoji(em, cells)

    def _problem(self, what):
        if len(self.problems) < 50:
            self.problems.append(what)

    # -- painting ------------------------------------------------------------------------
    def colors(self, style):
        fg, bg, attrs = style
        fgc = fg if fg is not None else self.default_fg
        bgc = bg if bg is not None else self.default_bg
        if attrs & vt.REVERSE:
            fgc, bgc = bgc, fgc
        if attrs & vt.DIM:
            fgc = _blend(bgc, fgc, DIM_SHARE)
        return fgc, bgc

    def paint(self, screen):
        """Bring the picture up to date with the screen. Returns the rows that changed."""
        with self.lock:
            cols, rows = screen.cols, screen.rows
            full = (cols, rows) != (self.cols, self.rows) or self.text is None
            if full:
                self.cols, self.rows = cols, rows
                self.text = Image.new("RGB", (cols * self.cw, rows * self.ch), self.default_bg)
                self.frame = self.text
                self.p_chars = [[None] * cols for _ in range(rows)]
                self.p_styles = [[None] * cols for _ in range(rows)]
                self.shown_layers = []
                self.pic_version = -1
                screen.dirty = set(range(rows))
                screen.cell = (self.cw, self.ch)
            done = set()
            for y in sorted(screen.take_dirty()):
                if 0 <= y < rows and self._paint_row(screen, y):
                    done.add(y)
            done = self._compose(screen, done, full)
            if full:
                done = set(range(rows))
            if done:
                self.changed |= done
                self.version += 1
            if done or screen.cursor_visible != (self.cursor is not None) or (
                    self.cursor is not None and (screen.x, screen.y) != self.cursor[:2]):
                self.cursor = self._cursor(screen)
            return done

    def _paint_row(self, screen, y):
        rc, rs = screen.chars[y], screen.styles[y]
        pc, ps = self.p_chars[y], self.p_styles[y]
        cols, cw, ch = self.cols, self.cw, self.ch
        img = self.text
        top = y * ch
        changed = False
        x = 0
        while x < cols:
            c, s = rc[x], rs[x]
            if c == pc[x] and s == ps[x]:
                x += 1
                continue
            if c == "" and x > 0:                       # the second half of a wide character:
                x -= 1                                  # draw it from its first half
                c, s = rc[x], rs[x]
            # a run of changed cells in one background: one fill, then the characters
            fgc, bgc = self.colors(s)
            start = x
            end = x + 1
            while end < cols and (rc[end] != pc[end] or rs[end] != ps[end] or rc[end] == "") \
                    and self.colors(rs[end])[1] == bgc:
                end += 1
            img.paste(bgc, (start * cw, top, end * cw, top + ch))
            for xx in range(start, end):
                c, s = rc[xx], rs[xx]
                pc[xx], ps[xx] = c, s
                if c in (" ", "") or s[2] & vt.INVISIBLE:
                    if s[2] & (vt.UNDERLINE | vt.STRIKE) and c == " ":
                        self._lines(img, xx, top, 1, s)
                    continue
                cells = 2 if xx + 1 < cols and rc[xx + 1] == "" else 1
                fg = self.colors(s)[0]
                g = self.glyph(c, s[2], cells)
                box = (xx * cw, top, (xx + cells) * cw, top + ch)
                if cells == 2 and end <= xx + 1:            # its second half is outside this run's fill
                    img.paste(self.colors(rs[xx + 1])[1], (box[0] + cw, top, box[2], top + ch))
                if g.mode == "RGBA":
                    img.paste(g, box, g)
                else:
                    img.paste(fg, box, g)
                if s[2] & (vt.UNDERLINE | vt.STRIKE):
                    self._lines(img, xx, top, cells, s)
            changed = True
            x = end
        return changed

    def _lines(self, img, x, top, cells, style):
        fg = self.colors(style)[0]
        x0, x1 = x * self.cw, (x + cells) * self.cw
        if style[2] & vt.UNDERLINE:
            y = top + self.underline_y
            img.paste(fg, (x0, y, x1, y + self.line))
        if style[2] & vt.STRIKE:
            y = top + self.strike_y
            img.paste(fg, (x0, y, x1, y + self.strike_line))

    # -- the pictures --------------------------------------------------------------------
    # The frame is the text with the pictures over it, bottom to top (z, then the order
    # they were placed in). Two pictures of the whole frame are kept to save work when
    # only some of the pictures change from one frame to the next, as the CRT glass's do
    # (its grain, hum bar and flicker change every tenth of a second, its glow and the
    # rest seldom): the base, the text with the pictures at the bottom that stayed as they
    # were; and the top, the pictures at the top that stayed, laid into one.
    def _layer(self, screen, pl):
        """A placement's picture as shown - cropped, stretched over its cells - and
        whether it is opaque: (image, opaque, what it is); None for none."""
        pic = screen.pictures.get(pl.iid)
        if pic is None:
            return None
        ident = (pic.serial, pic.frame, pic.edits, pl.crop, pl.cols, pl.rows, self.cw, self.ch)
        hit = self.layers.get(ident)
        if hit is not None:
            self.layers.move_to_end(ident)
            if hit[3] is False:          # shown again: worth cutting into pieces now
                hit = self.layers[ident] = hit[:3] + (pieces(hit[0]),)
            return hit
        src = pic.image
        if pl.crop:
            x, y, w, h = pl.crop
            x, y = max(0, min(x, src.size[0])), max(0, min(y, src.size[1]))
            w = src.size[0] - x if not w else min(w, src.size[0] - x)
            h = src.size[1] - y if not h else min(h, src.size[1] - y)
            if w <= 0 or h <= 0:
                return None
            src = src.crop((x, y, x + w, y + h))
        sw, sh = src.size
        if pl.cols and pl.rows:
            size = (pl.cols * self.cw, pl.rows * self.ch)
        elif pl.cols:
            size = (pl.cols * self.cw, max(1, round(sh * pl.cols * self.cw / sw)))
        elif pl.rows:
            size = (max(1, round(sw * pl.rows * self.ch / sh)), pl.rows * self.ch)
        else:
            size = (sw, sh)
        if size != (sw, sh):
            src = stretch(src, size)
        opaque = src.mode == "RGB"
        if not opaque:
            if src.mode != "RGBA":
                src = src.convert("RGBA")
            if src.getchannel("A").getextrema()[0] == 255:
                src, opaque = src.convert("RGB"), True
        hit = (src, opaque, ident, None if opaque else False)
        self.layers[ident] = hit
        while len(self.layers) > 96:
            self.layers.popitem(last=False)
        return hit

    def _shown(self, screen):
        """The pictures over the text, bottom to top: [(what is shown where, its top row,
        its bottom row, image, x, y, opaque)]."""
        ch, width, height = self.ch, self.cols * self.cw, self.rows * self.ch
        out = []
        for pl in sorted(screen.placements.values(), key=lambda pl: (pl.z, pl.seq)):
            got = self._layer(screen, pl)
            if got is None:
                continue
            img, opaque, ident, parts = got
            x, y = pl.col * self.cw + pl.xoff, pl.row * ch + pl.yoff
            w, h = img.size
            if x >= width or y >= height or x + w <= 0 or y + h <= 0:
                continue
            out.append(((ident, x, y), max(0, y // ch), min(self.rows - 1, (y + h - 1) // ch), img, x, y, opaque,
                        parts))
        return out

    @staticmethod
    def _lay(img, layer):
        """A picture onto the whole of `img` (cut off at its edges)."""
        pic, x, y, opaque, parts = layer[3:]
        if opaque:
            img.paste(pic, (x, y))
        elif parts:
            for dx, dy, part in parts:
                img.paste(part, (x + dx, y + dy), part)
        else:
            img.paste(pic, (x, y), pic)

    def _lay_rows(self, img, layer, y0, y1):
        """The part of a picture between pixel rows y0 and y1 onto `img`."""
        pic, lx, ly, opaque = layer[3:7]
        width = self.cols * self.cw
        sy0, sy1 = max(y0, ly), min(y1, ly + pic.size[1])
        sx0, sx1 = max(0, lx), min(width, lx + pic.size[0])
        if sy1 <= sy0 or sx1 <= sx0:
            return
        part = pic.crop((sx0 - lx, sy0 - ly, sx1 - lx, sy1 - ly))
        if opaque:
            img.paste(part, (sx0, sy0))
        else:
            img.paste(part, (sx0, sy0), part)

    def _merge(self, layers):
        """Pictures laid into one (RGBA, the size of the frame), to go on in one step."""
        width, height = self.cols * self.cw, self.rows * self.ch
        out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        for layer in layers:
            pic, x, y = layer[3:6]
            sx0, sy0 = max(0, -x), max(0, -y)
            sx1, sy1 = min(pic.size[0], width - x), min(pic.size[1], height - y)
            if sx1 <= sx0 or sy1 <= sy0:
                continue
            part = pic.crop((sx0, sy0, sx1, sy1))
            out.alpha_composite(part if part.mode == "RGBA" else part.convert("RGBA"), (x + sx0, y + sy0))
        return out

    @staticmethod
    def _bands(rows):
        """Consecutive rows in runs: [[first, last], ...]."""
        bands = []
        for y in rows:
            if bands and bands[-1][1] == y - 1:
                bands[-1][1] = y
            else:
                bands.append([y, y])
        return bands

    def _compose(self, screen, done, full):
        """Lay the pictures over the rows that changed and over those whose pictures did.
        Returns every row of the frame that changed."""
        if not screen.placements and not self.shown_layers:
            self.frame = self.text
            self.base = self.top = None
            self.base_keys = self.top_keys = ()
            return done
        layers = self._shown(screen)
        keys = [layer[0] for layer in layers]
        prev_keys = [k for k, _t, _b in self.shown_layers]
        redo = set(range(self.rows)) if full else set(done)
        if keys != prev_keys:
            before = {k: (t, b) for k, t, b in self.shown_layers}
            after = {layer[0]: (layer[1], layer[2]) for layer in layers}
            for k, (t, b) in list(before.items()) + list(after.items()):
                if k not in before or k not in after:
                    redo.update(range(t, b + 1))
            if set(before) == set(after):          # the same pictures, stacked another way
                for t, b in after.values():
                    redo.update(range(t, b + 1))
        self.shown_layers = [(layer[0], layer[1], layer[2]) for layer in layers]
        ch, width = self.ch, self.cols * self.cw
        if not layers:                              # the last pictures went: the text again
            self.frame = self.text
            self.base = self.top = None
            self.base_keys = self.top_keys = ()
            return {y for y in redo if 0 <= y < self.rows}
        n = len(layers)
        # the base: the text, and the pictures at the bottom that stayed as they were
        stay = 0
        if not full:
            while stay < min(n, len(prev_keys)) and keys[stay] == prev_keys[stay]:
                stay += 1
        nb = len(self.base_keys)
        if self.base is None or full or nb > n or tuple(keys[:nb]) != self.base_keys:
            # a picture in the base changed: the base again, as deep as it was (a picture that
            # changes now and then stays in it), or only as deep as what stayed when the base
            # had to be made again last frame as well
            deep = 0 if (full or self.base is None or self.base_churn) else min(nb, n)
            self.base = self.text.copy()
            for layer in layers[:deep]:
                self._lay(self.base, layer)
            self.base_keys, nb = tuple(keys[:deep]), deep
            self.base_churn = True
        elif done:                                  # text changed under the base's pictures
            for first, last in self._bands(sorted(y for y in done if 0 <= y < self.rows)):
                y0, y1 = first * ch, (last + 1) * ch
                self.base.paste(self.text.crop((0, y0, width, y1)), (0, y0))
                for layer in layers[:nb]:
                    if layer[2] >= first and layer[1] <= last:
                        self._lay_rows(self.base, layer, y0, y1)
            self.base_churn = False
        else:
            self.base_churn = False
        if stay > nb:
            for layer in layers[nb:stay]:
                self._lay(self.base, layer)
            self.base_keys, nb = tuple(keys[:stay]), stay
        # the top: the pictures at the top that stayed as they were, laid into one
        nt = len(self.top_keys)
        if nt and (nt > n - nb or tuple(keys[n - nt:]) != self.top_keys):
            self.top, self.top_keys, nt = None, (), 0
        if not nt and not full:
            same = 0
            while (same < min(n - nb, len(prev_keys)) and
                   keys[n - 1 - same] == prev_keys[len(prev_keys) - 1 - same]):
                same += 1
            if same >= 2:
                merged = self._merge(layers[n - same:])
                self.top = (None, 0, 0, merged, 0, 0, False, pieces(merged))
                self.top_keys, nt = tuple(keys[n - same:]), same
        middle = layers[nb:n - nt]
        rows = sorted(y for y in redo if 0 <= y < self.rows)
        if not rows:
            return set()
        if len(rows) * 5 >= self.rows * 3 or self.frame is self.text or self.frame is None:
            frame = self.base.copy()                # most of the frame: all of it at once
            for layer in middle:
                self._lay(frame, layer)
            if nt:
                self._lay(frame, self.top)
            self.frame = frame
            return set(range(self.rows))
        for first, last in self._bands(rows):
            y0, y1 = first * ch, (last + 1) * ch
            box = (0, y0, width, y1)
            self.frame.paste(self.base.crop(box), (0, y0))
            for layer in middle:
                if layer[2] >= first and layer[1] <= last:
                    self._lay_rows(self.frame, layer, y0, y1)
            if nt:
                self._lay_rows(self.frame, self.top, y0, y1)
        return set(rows)

    # -- for the window ------------------------------------------------------------------
    def take_rows(self):
        """The rows changed since the last call, with their pixels: (columns, rows, cell
        size, {row: image}); None when none did."""
        with self.lock:
            if not self.changed or self.frame is None:
                return None
            rows, self.changed = self.changed, set()
            width = self.cols * self.cw
            return (self.cols, self.rows, (self.cw, self.ch),
                    {y: self.frame.crop((0, y * self.ch, width, (y + 1) * self.ch)) for y in rows if 0 <= y < self.rows})

    def _cursor(self, screen):
        """The cursor as the window shows it: (column, row, the cell reversed - its
        character in its background color on a block of its foreground color -, the cell
        with a box of that color round it); None when the app hides the cursor."""
        if not screen.cursor_visible:
            return None
        x, y = min(screen.x, screen.cols - 1), min(screen.y, screen.rows - 1)
        c, s = screen.chars[y][x], screen.styles[y][x]
        if c == "" and x > 0:
            x -= 1
            c, s = screen.chars[y][x], screen.styles[y][x]
        cells = 2 if x + 1 < screen.cols and screen.chars[y][x + 1] == "" else 1
        key = (x, y, c, s, cells, self.cw, self.ch)
        if self._cursor_key == key:
            return self.cursor
        fg, bg = self.colors(s)
        w, h = self.cw * cells, self.ch
        block = Image.new("RGB", (w, h), fg)
        hollow = Image.new("RGB", (w, h), bg)
        if c not in (" ", "") and not s[2] & vt.INVISIBLE:
            g = self.glyph(c, s[2], cells)
            if g.mode == "RGBA":
                block.paste(g, (0, 0), g)
                hollow.paste(g, (0, 0), g)
            else:
                block.paste(bg, (0, 0, w, h), g)
                hollow.paste(fg, (0, 0, w, h), g)
        ImageDraw.Draw(hollow).rectangle([0, 0, w - 1, h - 1], outline=fg, width=max(1, self.line))
        self._cursor_key = key
        return (x, y, block, hollow)

    def snapshot(self):
        with self.lock:
            return self.frame.copy() if self.frame is not None else None
