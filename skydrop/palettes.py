"""Radar colour tables in the GRLevelX .pal format (the format Supercell Wx uses too).

The built-in tables follow the familiar National Weather Service colour scales.
To use your own, put a .pal file named after the table in
~/.config/skydrop/palettes/ (ref.pal, vel.pal, cc.pal, zdr.pal, kdp.pal,
hc.pal, et.pal, vil.pal, rate.pal or precip.pal).  Values are in the
GRLevelX "internal" units (dBZ, m/s, km, mm, ...) unless the file has a Scale:
line that converts them, exactly as in GRLevel3 and Supercell Wx.

Supported lines: Units, Scale, Offset, Step, Color, Color4, SolidColor,
SolidColor4, RF and ND.  "Color: value r g b [r2 g2 b2]" blends from the first
colour at value to the second colour (or the next line's colour) at the next
value; SolidColor holds one colour up to the next value.
"""

import bisect
import os

from .paths import CONFIG_DIR

PALETTE_DIR = os.path.join(CONFIG_DIR, "palettes")

BUILTIN = {
    "ref": """
; Reflectivity, NWS colours with a soft low end
Units: dBZ
Step: 5
Color4: -10   45  70 120  60
Color4:   0   70 120 170 120
Color4:   5   60 200 235 190
Color:   10   40 170 245
Color:   15   35 110 245
Color:   20    2 230  60
Color:   25    1 190   1
Color:   30    0 140   0
Color:   35  253 242   2
Color:   40  238 190   0
Color:   45  253 140   0
Color:   50  253  16  16
Color:   55  212   0   0
Color:   60  170   0   0
Color:   65  248   0 253
Color:   70  152  84 198
Color:   75  253 253 253
Color:   85  220 220 255
""",
    "vel": """
; Velocity in knots: green toward the radar, red away
Units: KT
Scale: 1.94384
Step: 10
Color: -120  225 255 255
Color:  -90   30 235 255
Color:  -70    0 255 130
Color:  -50    0 225   0
Color:  -30    0 165   0
Color:  -10    0 100   0
Color:   -2   60  95  60
Color:    0   92  92  92
Color:    2  100  70  70
Color:   10  125   0   0
Color:   30  190   0   0
Color:   50  255  20  20
Color:   70  255 110 160
Color:   90  255 205 110
Color:  120  255 255 225
RF: 119 0 125
""",
    "cc": """
; Correlation coefficient: rain near 1 (warm colours), debris and non-weather low (cool)
Units: CC
Step: 0.05
Color: 0.20   40  35  80
Color: 0.45   70  50 150
Color: 0.60   95  95 215
Color: 0.70  110 165 255
Color: 0.80   60 230 150
Color: 0.85  140 230  40
Color: 0.90  255 250   0
Color: 0.93  255 190   0
Color: 0.95  255 120   0
Color: 0.97  230  25  25
Color: 0.99  165  15  85
Color: 1.00  255 170 220
Color: 1.05  255 235 245
RF: 119 0 125
""",
    "zdr": """
; Differential reflectivity
Units: dB
Step: 1
Color: -4.0   60  60  60
Color: -2.0  130 130 130
Color: -0.5  185 185 205
Color:  0.0  145 145 225
Color:  0.5   60  95 235
Color:  1.0   40 185 250
Color:  1.5   50 230 140
Color:  2.0  255 255  60
Color:  3.0  255 160   0
Color:  4.0  235  25  25
Color:  5.0  255  95 195
Color:  6.0  255 255 255
Color:  8.0  190 125 255
RF: 119 0 125
""",
    "kdp": """
; Specific differential phase
Units: deg/km
Step: 1
Color: -2.0   90  90  90
Color: -0.5  130 130 130
Color:  0.0   65  75 135
Color:  0.3   45 115 225
Color:  0.7   40 190 240
Color:  1.0   40 230 130
Color:  1.5  150 240  40
Color:  2.0  255 255   0
Color:  3.0  255 160   0
Color:  4.0  240  45  45
Color:  5.0  255  95 205
Color:  7.0  255 230 255
RF: 119 0 125
""",
    "hc": """
; Hydrometeor classification (NWS colours)
Units: class
SolidColor:  10  156 156 156 ; BI biological
SolidColor:  20  118 118 118 ; GC ground clutter
SolidColor:  30  255 176 176 ; IC ice crystals
SolidColor:  40    0 255 255 ; DS dry snow
SolidColor:  50    0 144 255 ; WS wet snow
SolidColor:  60    0 251 144 ; RA light/moderate rain
SolidColor:  70    0 187   0 ; HR heavy rain
SolidColor:  80  208 208  96 ; BD big drops
SolidColor:  90  210 132 132 ; GR graupel
SolidColor: 100  255   0   0 ; HA hail and rain
SolidColor: 110  160  20  20 ; LH large hail
SolidColor: 120  255 255   0 ; GH giant hail
SolidColor4: 130   0   0   0   0 ; ND
SolidColor: 140  231   0 255 ; UK unknown
SolidColor: 150  119   0 125 ; RF range folded
""",
    "et": """
; Echo tops in thousands of feet
Units: KFT
Scale: 3.28084
Step: 10
Color:  5  110 110 135
Color: 10    0 140 255
Color: 15    0 200 255
Color: 20    0 255 170
Color: 25    0 200  40
Color: 30  140 230   0
Color: 35  255 255   0
Color: 40  255 190   0
Color: 45  255 120   0
Color: 50  255  20  20
Color: 55  190   0  40
Color: 60  255   0 255
Color: 65  180 110 255
Color: 70  255 255 255
""",
    "vil": """
; Vertically integrated liquid
Units: kg/m2
Step: 10
Color:  1  100 110 135
Color:  5    0 200 230
Color: 10    0 130 255
Color: 15   45  75 240
Color: 20    0 230  60
Color: 25    0 180   0
Color: 30  120 210   0
Color: 35  255 255   0
Color: 40  255 190   0
Color: 45  255 130   0
Color: 50  255  20  20
Color: 55  200   0   0
Color: 60  255   0 255
Color: 65  165  95 225
Color: 70  240 240 240
""",
    "precip": """
; Rainfall in inches
Units: IN
Scale: 0.0393701
Step: 0.5
Color4: 0.01   90 125 160 170
Color: 0.10    0 190 255
Color: 0.25    0 240 160
Color: 0.50    0 200   0
Color: 0.75  140 230   0
Color: 1.00  255 255   0
Color: 1.50  255 170   0
Color: 2.00  255  90   0
Color: 2.50  230   0   0
Color: 3.00  170   0  60
Color: 4.00  255   0 255
Color: 6.00  200 140 255
Color: 8.00  255 255 255
""",
    "rate": """
; Rain rate in inches per hour
Units: IN/HR
Scale: 0.0393701
Step: 0.5
Color4: 0.01   90 125 160 170
Color: 0.05    0 190 255
Color: 0.10    0 240 160
Color: 0.25    0 200   0
Color: 0.50  255 255   0
Color: 1.00  255 150   0
Color: 2.00  255   0   0
Color: 3.00  255   0 255
Color: 4.00  255 255 255
""",
}

# Native decoder units -> GRLevelX internal units (m/s, km, mm, mm/h).
CANON = {56: 1 / 1.94384, 55: 1 / 1.94384, 135: 1 / 3.28084, 138: 25.4, 169: 25.4, 170: 25.4, 172: 25.4,
         173: 25.4, 174: 25.4, 175: 25.4, 176: 25.4, 78: 25.4, 79: 25.4, 80: 25.4}


class ColorTable:
    def __init__(self, text, name="", path=None):
        self.name, self.path = name, path
        self.units, self.scale, self.offset, self.step = "", 1.0, 0.0, None
        self.rf = self.nd = None
        stops = []
        for raw in text.splitlines():
            line = raw.split(";", 1)[0].strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, val = line.split(":", 1)
            key = key.strip().lower()
            nums = val.replace(",", " ").split()
            try:
                if key == "units":
                    self.units = val.strip()
                elif key == "scale":
                    self.scale = float(nums[0]) or 1.0
                elif key == "offset":
                    self.offset = float(nums[0])
                elif key == "step":
                    self.step = float(nums[0])
                elif key in ("rf", "nd"):
                    c = tuple(int(float(x)) for x in nums[:4])
                    c = c + (255,) if len(c) == 3 else c
                    setattr(self, key, c)
                elif key in ("color", "color4", "solidcolor", "solidcolor4"):
                    four = key.endswith("4")
                    n = 4 if four else 3
                    v = float(nums[0])
                    c1 = tuple(int(float(x)) for x in nums[1:1 + n])
                    c2 = tuple(int(float(x)) for x in nums[1 + n:1 + 2 * n]) or None
                    if len(c1) < n or (c2 is not None and len(c2) < n):
                        continue
                    if not four:
                        c1 = c1 + (255,)
                        c2 = c2 + (255,) if c2 else None
                    stops.append((v, c1, c2, key.startswith("solid")))
            except (ValueError, IndexError):
                continue
        stops.sort(key=lambda s: s[0])
        self.stops = stops
        self.keys = [s[0] for s in stops]

    def color(self, v):
        """RGBA for a value in palette units, or None below the table."""
        if not self.stops or v is None:
            return None
        i = bisect.bisect_right(self.keys, v) - 1
        if i < 0:
            return None
        sv, c1, c2, solid = self.stops[i]
        if solid or i == len(self.stops) - 1:
            return c1
        nv, nc1, _nc2, _ns = self.stops[i + 1]
        end = c2 or nc1
        if nv <= sv:
            return c1
        f = (v - sv) / (nv - sv)
        return tuple(int(round(a + (b - a) * f)) for a, b in zip(c1, end))

    def span(self):
        if not self.stops:
            return 0.0, 1.0
        return self.stops[0][0], self.stops[-1][0]


_cache = {}


def load(name):
    """The colour table to use: a user file if there is one, else the built-in."""
    path = os.path.join(PALETTE_DIR, f"{name}.pal")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    key = (name, mtime)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    table = None
    if mtime is not None:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                table = ColorTable(fh.read(), name, path)
            if not table.stops:
                table = None
        except OSError:
            table = None
    if table is None:
        table = ColorTable(BUILTIN.get(name, BUILTIN["ref"]), name)
    _cache[key] = table
    return table


def canonical(code, native):
    if native is None:
        return None
    return native * CANON.get(code, 1.0)


def lut(l3, table, product_kind="value", min_value=None, opacity=1.0, show_rf=True):
    """256-entry colour lookup for this file's data levels: (rgb bytes, alpha list).

    min_value hides values below it (palette units, e.g. dBZ)."""
    rgb = bytearray(768)
    alpha = [0] * 256
    rf = getattr(l3, "rf_level", None)
    for i in range(256):
        c = None
        if i == rf:
            c = table.rf if show_rf else None
        else:
            v = l3.values[i] if i < len(l3.values) else None
            if v is None:
                continue
            if product_kind == "class":
                c = table.color(v)
            else:
                pv = canonical(l3.code, v) * table.scale + table.offset
                if min_value is not None and pv < min_value:
                    continue
                c = table.color(pv)
        if c is None:
            continue
        rgb[3 * i:3 * i + 3] = bytes(c[:3])
        alpha[i] = max(0, min(255, int(c[3] * opacity)))
    return bytes(rgb), alpha


def legend_stops(table, product_kind="value", count=24):
    """[(palette value, rgba)] samples across the table for drawing a colour bar."""
    if product_kind == "class":
        return [(s[0], s[1]) for s in table.stops if s[1][3] > 0]
    lo, hi = table.span()
    if hi <= lo:
        return [(lo, table.color(lo))]
    out = []
    for k in range(count):
        v = lo + (hi - lo) * k / (count - 1)
        c = table.color(v)
        if c is not None:
            out.append((v, c))
    return out


def user_tables():
    try:
        return sorted(f[:-4] for f in os.listdir(PALETTE_DIR) if f.endswith(".pal"))
    except OSError:
        return []
