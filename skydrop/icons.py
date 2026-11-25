"""Weather icons: classify a condition, then draw it as pixels (kitty/sixel),
colored line art (block terminals) or plain ASCII art.
"""

import math

from . import gfx

KINDS = ("clear", "mostly_clear", "partly", "mostly_cloudy", "cloudy", "fog", "haze", "smoke", "dust",
         "wind", "drizzle", "rain", "showers", "heavy_rain", "thunder", "snow", "flurries", "sleet",
         "freezing", "mix", "unknown")

PRECIP_KINDS = {"drizzle", "rain", "showers", "heavy_rain", "thunder", "snow", "flurries", "sleet",
                "freezing", "mix"}


def from_text(text):
    t = (text or "").lower()
    if not t:
        return "unknown"
    if "tornado" in t or "thunder" in t or "t-storm" in t or "tstorm" in t:
        return "thunder"
    if "freezing" in t or "ice pellets" in t or "glaze" in t:
        return "freezing" if "fog" not in t or "rain" in t or "drizzle" in t else "fog"
    if "sleet" in t:
        return "sleet"
    if "snow" in t and ("rain" in t or "wintry" in t) or "wintry mix" in t:
        return "mix"
    if "blizzard" in t or "heavy snow" in t:
        return "snow"
    if "flurr" in t or "snow shower" in t or "light snow" in t:
        return "flurries"
    if "snow" in t:
        return "snow"
    if "heavy rain" in t or "heavy shower" in t or "downpour" in t:
        return "heavy_rain"
    if "shower" in t:
        return "showers"
    if "drizzle" in t:
        return "drizzle"
    if "rain" in t:
        return "rain"
    if "fog" in t or "mist" in t:
        return "fog"
    if "smoke" in t:
        return "smoke"
    if "haze" in t:
        return "haze"
    if "dust" in t or "sand" in t or "ash" in t:
        return "dust"
    last = t.split(" then ")[-1]
    if "overcast" in last or ("cloudy" in last and "mostly" not in last and "partly" not in last):
        return "cloudy"
    if "mostly cloudy" in last or "considerable cloud" in last or "broken" in last:
        return "mostly_cloudy"
    if "partly" in last or "scattered" in last or "few clouds" in last or "partly sunny" in last:
        return "partly"
    if "mostly sunny" in last or "mostly clear" in last or "fair" in last:
        return "mostly_clear"
    if "sunny" in last or "clear" in last or "hot" in last:
        return "clear"
    if "wind" in last or "breezy" in last or "blustery" in last or "gust" in last:
        return "wind"
    if "cloud" in last:
        return "mostly_cloudy"
    return "unknown"


def from_wmo(code):
    if code is None:
        return "unknown"
    c = int(code)
    table = {0: "clear", 1: "mostly_clear", 2: "partly", 3: "cloudy", 45: "fog", 48: "fog",
             51: "drizzle", 53: "drizzle", 55: "drizzle", 56: "freezing", 57: "freezing",
             61: "rain", 63: "rain", 65: "heavy_rain", 66: "freezing", 67: "freezing",
             71: "flurries", 73: "snow", 75: "snow", 77: "flurries", 80: "showers", 81: "showers",
             82: "heavy_rain", 85: "flurries", 86: "snow", 95: "thunder", 96: "thunder", 99: "thunder"}
    return table.get(c, "unknown")


def from_owm(wid):
    if wid is None:
        return "unknown"
    w = int(wid)
    if 200 <= w < 300:
        return "thunder"
    if 300 <= w < 400:
        return "drizzle"
    if w in (500, 501):
        return "rain"
    if 502 <= w <= 504:
        return "heavy_rain"
    if w == 511:
        return "freezing"
    if 520 <= w < 600:
        return "showers"
    if w in (611, 612, 613):
        return "sleet"
    if w in (615, 616):
        return "mix"
    if w in (600, 620):
        return "flurries"
    if 600 <= w < 700:
        return "snow"
    if w in (701, 741):
        return "fog"
    if w == 711:
        return "smoke"
    if w == 721:
        return "haze"
    if w in (731, 751, 761, 762):
        return "dust"
    if w in (771, 781):
        return "wind"
    return {800: "clear", 801: "mostly_clear", 802: "partly", 803: "mostly_cloudy", 804: "cloudy"}.get(w, "unknown")


def from_clouds(clouds, pop=None):
    if pop is not None and pop >= 50:
        return "showers"
    if clouds is None:
        return "unknown"
    if clouds < 12:
        return "clear"
    if clouds < 35:
        return "mostly_clear"
    if clouds < 65:
        return "partly"
    if clouds < 88:
        return "mostly_cloudy"
    return "cloudy"


def label(kind):
    return {"clear": "Clear", "mostly_clear": "Mostly clear", "partly": "Partly cloudy",
            "mostly_cloudy": "Mostly cloudy", "cloudy": "Cloudy", "fog": "Fog", "haze": "Haze",
            "smoke": "Smoke", "dust": "Dust", "wind": "Windy", "drizzle": "Drizzle", "rain": "Rain",
            "showers": "Showers", "heavy_rain": "Heavy rain", "thunder": "Thunderstorms", "snow": "Snow",
            "flurries": "Flurries", "sleet": "Sleet", "freezing": "Freezing rain", "mix": "Wintry mix"
            }.get(kind, "")


# ----------------------------------------------------------------------------
# line-art icons: 5 rows x 13 columns
# ----------------------------------------------------------------------------

_SUN = ["   \\  │  /   ",
        "    ╭───╮    ",
        " ── │   │ ── ",
        "    ╰───╯    ",
        "   /  │  \\   "]
_MOON = ["    .--.   * ",
         "   /  ,'     ",
         "  |  (    *  ",
         "   \\  `.     ",
         "    `--'  *  "]
_CLOUD = ["             ",
          "     .--.    ",
          "  .-(    ).  ",
          " (___.__)__) ",
          "             "]
_CLOUD_HI = ["     .--.    ",
             "  .-(    ).  ",
             " (___.__)__) ",
             "             ",
             "             "]
_PARTLY_SUN = ["\\ │ /        ",
               "─( )─  .--.  ",
               "/ │ \\.(    ).",
               "   (___.__)_)",
               "             "]
_PARTLY_MOON = ["  .-.        ",
                " (  (  .--.  ",
                "  `-'.(    ).",
                "   (___.__)_)",
                "             "]
_MOSTLY_CLOUDY = ["  \\ /        ",
                  " ─( ).--.    ",
                  "  .-(    ).  ",
                  " (___.__)__) ",
                  "             "]
_FOG = ["             ",
        " _ ‒ _ ‒ _ ‒ ",
        "  _ ‒ _ ‒ _  ",
        " _ ‒ _ ‒ _ ‒ ",
        "             "]
_WIND = ["             ",
         "  ─────────╮ ",
         " ──────────╯ ",
         "   ──────╮   ",
         "   ──────╯   "]
_TAILS = {
    "drizzle": ["   '  '  '   ", "  '  '  '    "],
    "rain": ["  ╱ ╱ ╱ ╱    ", " ╱ ╱ ╱ ╱     "],
    "heavy_rain": [" ╱╱╱╱╱╱╱╱   ", "╱╱╱╱╱╱╱╱    "],
    "showers": ["  ╱   ╱  ╱   ", "   ╱   ╱     "],
    "thunder": ["   /_  ╱ /_  ", "    /  ╱  /  "],
    "snow": ["  *  *  *  * ", " *  *  *  *  "],
    "flurries": ["   *    *    ", "     *    *  "],
    "sleet": ["  * ╱ * ╱    ", " ╱ * ╱ *     "],
    "freezing": ["  ╱ ╱ ╱ ╱    ", " ~ ~ ~ ~     "],
    "mix": ["  * ╱ * ╱    ", " ╱ * ╱ *     "],
}
_TAIL_STYLE = {"drizzle": "rain", "rain": "rain", "heavy_rain": "rain", "showers": "rain", "thunder": "accent",
               "snow": "snow", "flurries": "snow", "sleet": "snow", "freezing": "rain", "mix": "snow"}
_ASCII_MAP = str.maketrans({"│": "|", "─": "-", "╭": ".", "╮": ".", "╰": "'", "╯": "'", "╱": "/", "‒": "-"})


def _rows(rows, styles):
    return [[(r, s)] for r, s in zip(rows, styles)]


def art(kind, is_day=True, ascii_only=False):
    """5 lines of 13 columns."""
    sun, cloud, dark = "title", "value", "dim"
    if kind in ("clear", "mostly_clear"):
        rows = _SUN if is_day else _MOON
        out = _rows(rows, [sun if is_day else "t_mild"] * 5)
    elif kind == "partly":
        rows = _PARTLY_SUN if is_day else _PARTLY_MOON
        out = []
        for i, r in enumerate(rows):
            split = 5 if i < 3 else 0
            if i == 1:
                out.append([(r[:5], sun if is_day else "t_mild"), (r[5:], cloud)])
            elif i == 2:
                out.append([(r[:5], sun if is_day else "t_mild"), (r[5:], cloud)])
            elif i == 0:
                out.append([(r, sun if is_day else "t_mild")])
            else:
                out.append([(r[split:], cloud)] if split else [(r, cloud)])
    elif kind == "mostly_cloudy":
        out = [[(_MOSTLY_CLOUDY[0], sun if is_day else "t_mild")],
               [(_MOSTLY_CLOUDY[1][:5], sun if is_day else "t_mild"), (_MOSTLY_CLOUDY[1][5:], cloud)],
               [(_MOSTLY_CLOUDY[2], cloud)], [(_MOSTLY_CLOUDY[3], cloud)], [(_MOSTLY_CLOUDY[4], cloud)]]
    elif kind == "cloudy":
        out = _rows(_CLOUD, [cloud] * 5)
    elif kind in ("fog", "haze", "smoke", "dust"):
        st = {"fog": "dim", "haze": "warn", "smoke": "dim", "dust": "accent"}[kind]
        out = _rows(_FOG, [st] * 5)
    elif kind == "wind":
        out = _rows(_WIND, ["label"] * 5)
    elif kind in _TAILS:
        cst = dark if kind in ("thunder", "heavy_rain") else cloud
        out = _rows(_CLOUD_HI[:3], [cst] * 3) + _rows(_TAILS[kind], [_TAIL_STYLE[kind]] * 2)
    else:
        rows = list(_CLOUD)
        rows[2] = "  .-(  ? ).  "
        out = _rows(rows, ["dim"] * 5)
    if ascii_only:
        out = [[(t.translate(_ASCII_MAP), s) for t, s in ln] for ln in out]
    return out


def mini(kind, is_day=True, ascii_only=False):
    """A two-character tag for tight table cells."""
    if ascii_only:
        return {"clear": "Sun", "mostly_clear": "Sun", "partly": "P/C", "mostly_cloudy": "M/C", "cloudy": "Cld",
                "fog": "Fog", "rain": "Rn", "showers": "Shw", "thunder": "Tst", "snow": "Sn"}.get(kind, "")
    return {"clear": ("☼" if is_day else "☾"), "mostly_clear": ("☼" if is_day else "☾")}.get(kind, "")


# ----------------------------------------------------------------------------
# pixel icons (Pillow)
# ----------------------------------------------------------------------------

def _mask(layer):
    return gfx.Image.new("L", layer.size, 0)


CLOUD_TONES = {"white": ((255, 255, 255), (196, 208, 232)),
               "gray": ((226, 230, 240), (146, 156, 182)),
               "dark": ((158, 166, 190), (78, 86, 114))}


def _cloud(layer, cx, cy, w, tone="white", shadow=True):
    """A puffy cloud centered at (cx, cy), w wide: lit from above, a soft shadow under it."""
    m = _mask(layer)
    d = gfx.ImageDraw.Draw(m)
    h = w * 0.55
    base_y = cy + h * 0.22
    puffs = [(cx - w * 0.29, base_y - h * 0.15, w * 0.20), (cx - w * 0.07, base_y - h * 0.40, w * 0.27),
             (cx + w * 0.19, base_y - h * 0.28, w * 0.21), (cx + w * 0.34, base_y - h * 0.07, w * 0.15)]
    for x, y, r in puffs:
        d.ellipse([x - r, y - r, x + r, y + r], fill=255)
    d.rounded_rectangle([cx - w * 0.48, base_y - h * 0.22, cx + w * 0.48, base_y + h * 0.25],
                        radius=h * 0.24, fill=255)
    if shadow:
        gfx.soft(layer, m, (0, 6, 30), 0.42, w * 0.035, w * 0.02, w * 0.05)
    top, bottom = CLOUD_TONES[tone]
    gfx.paint(layer, m, gfx.vgrad(layer.width, layer.height, top, bottom,
                            base_y - h * 0.40 - w * 0.27, base_y + h * 0.25))


def _sun(layer, cx, cy, r, rays=True):
    """The sun: a glow, tapered rays and a disc bright to the upper left, deep gold at the rim."""
    W, H = layer.size
    g = _mask(layer)
    gfx.ImageDraw.Draw(g).ellipse([cx - r * 1.2, cy - r * 1.2, cx + r * 1.2, cy + r * 1.2], fill=255)
    gfx.soft(layer, g, (255, 186, 60), 0.36, r * 0.32)
    if rays:
        m = _mask(layer)
        d = gfx.ImageDraw.Draw(m)
        for k in range(12):
            a = k * math.pi / 6 + math.pi / 12
            ca, sa = math.cos(a), math.sin(a)
            px, py = -sa, ca
            r0, r1, half = r * 1.22, r * 1.70, r * 0.12
            tip = half * 0.35
            d.polygon([(cx + ca * r0 + px * half, cy + sa * r0 + py * half),
                       (cx + ca * r1 + px * tip, cy + sa * r1 + py * tip),
                       (cx + ca * r1 - px * tip, cy + sa * r1 - py * tip),
                       (cx + ca * r0 - px * half, cy + sa * r0 - py * half)], fill=255)
            d.ellipse([cx + ca * r1 - tip, cy + sa * r1 - tip, cx + ca * r1 + tip, cy + sa * r1 + tip], fill=255)
        gfx.paint(layer, m, gfx.radial(W, H, cx, cy, r * 1.75, (255, 220, 80), (255, 160, 20)))
    disc = _mask(layer)
    gfx.ImageDraw.Draw(disc).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    gfx.paint(layer, disc, gfx.radial(W, H, cx - r * 0.32, cy - r * 0.38, r * 1.65, (255, 251, 214), (255, 168, 18)))


def _crescent(layer, cx, cy, r):
    """A crescent moon with a pale glow."""
    W, H = layer.size
    m = _mask(layer)
    d = gfx.ImageDraw.Draw(m)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    d.ellipse([cx - r * 0.45, cy - r * 1.05, cx + r * 1.45, cy + r * 0.85], fill=0)
    gfx.soft(layer, m, (186, 204, 255), 0.4, r * 0.4)
    gfx.paint(layer, m, gfx.radial(W, H, cx - r * 0.55, cy - r * 0.1, r * 1.5, (255, 251, 228), (206, 198, 160)))


def _star(layer, x, y, s, alpha=1.0):
    """A four-point twinkle."""
    m = _mask(layer)
    q = s * 0.2
    gfx.ImageDraw.Draw(m).polygon([(x, y - s), (x + q, y - q), (x + s, y), (x + q, y + q), (x, y + s),
                                   (x - q, y + q), (x - s, y), (x - q, y - q)], fill=int(255 * alpha))
    gfx.paint(layer, m, (255, 252, 230))


def _drops(layer, cx, top, bot, u, n, short=False, cold=False):
    """n raindrops, teardrops leaning with the wind, across under a cloud."""
    W, H = layer.size
    m = _mask(layer)
    d = gfx.ImageDraw.Draw(m)
    s = u * (0.055 if short else 0.075)
    lean = 0.32
    for k in range(n):
        x = cx - u * 0.28 + k * (u * 0.56 / max(1, n - 1))
        y = top + (bot - top) * (0.35 if k % 2 else 0.62)
        pts = []
        for a in range(0, 361, 12):
            t = math.radians(a)
            px = math.sin(t) * math.sin(t / 2) ** 1.3 * 0.7
            py = -math.cos(t) * 1.25
            # lean the drop: its point toward the upper right, as if blown
            rx = px * math.cos(lean) - py * math.sin(lean)
            ry = px * math.sin(lean) + py * math.cos(lean)
            pts.append((x + rx * s, y + ry * s))
        d.polygon(pts, fill=255)
    top_c, bot_c = ((214, 240, 255), (130, 196, 246)) if cold else ((176, 228, 255), (34, 124, 236))
    gfx.paint(layer, m, gfx.vgrad(W, H, top_c, bot_c, top, bot))


def _flakes(layer, cx, top, bot, u, n):
    """n snowflakes, each six arms with a pair of branches."""
    m = _mask(layer)
    d = gfx.ImageDraw.Draw(m)
    width = max(2, int(u * 0.022))
    for k in range(n):
        x = cx - u * 0.22 + k * (u * 0.44 / max(1, n - 1))
        y = top + (bot - top) * (0.4 if k % 2 else 0.68)
        r = u * 0.075
        for a in range(6):
            ang = a * math.pi / 3 + math.pi / 2
            d.line([x, y, x + math.cos(ang) * r, y + math.sin(ang) * r], fill=255, width=width)
            bx, by = x + math.cos(ang) * r * 0.55, y + math.sin(ang) * r * 0.55
            for side in (-1, 1):
                ba = ang + side * math.pi / 4
                d.line([bx, by, bx + math.cos(ba) * r * 0.32, by + math.sin(ba) * r * 0.32], fill=255, width=width)
    gfx.soft(layer, m, (170, 210, 255), 0.5, u * 0.012)
    gfx.paint(layer, m, (244, 250, 255))


def _bolt(layer, cx, cy, top, bot, u):
    """A lightning bolt, glowing."""
    W, H = layer.size
    m = _mask(layer)
    pts = [(cx + u * 0.03, top - u * 0.02), (cx - u * 0.13, cy + u * 0.3), (cx - u * 0.01, cy + u * 0.3),
           (cx - u * 0.11, bot + u * 0.07), (cx + u * 0.15, cy + u * 0.21), (cx + u * 0.03, cy + u * 0.21),
           (cx + u * 0.13, top - u * 0.02)]
    gfx.ImageDraw.Draw(m).polygon(pts, fill=255)
    gfx.soft(layer, m, (255, 214, 60), 0.75, u * 0.04)
    gfx.paint(layer, m, gfx.vgrad(W, H, (255, 246, 160), (255, 160, 0), top, bot))


def _bands(layer, color, W, H, S):
    """Fog, haze, smoke or dust: soft bands that fade at their ends."""
    m = _mask(layer)
    d = gfx.ImageDraw.Draw(m)
    for k in range(4):
        y = H * (0.27 + k * 0.15)
        off = (k % 2) * W * 0.08
        d.rounded_rectangle([W * 0.1 + off, y, W * 0.9 - off, y + H * 0.065], radius=H * 0.033, fill=255)
    ends = gfx.Image.linear_gradient("L").rotate(90, expand=True).resize((W, H))      # 255 at the left
    fade = gfx.ImageChops.multiply(ends.point(lambda v: min(255, int(v * 3))),
                                   ends.transpose(gfx.Image.FLIP_LEFT_RIGHT).point(lambda v: min(255, int(v * 3))))
    gfx.paint(layer, gfx.ImageChops.multiply(m, fade), gfx.vgrad(W, H, tuple(min(255, c + 30) for c in color), color))


def pixel_icon(kind, is_day, w, h, bg=(11, 22, 92), outline=None):
    """Return an RGB PIL image w x h of the icon over `bg`: shaded shapes with soft shadows
    and glows. `outline` rims every shape in that color (used on light themes, where white
    clouds would vanish)."""
    if not gfx.HAVE_PIL:
        return None
    S = 4
    W, H = max(8, w * S), max(8, h * S)
    bg = tuple(bg[:3])
    img = gfx.Image.new("RGBA", (W, H), (0, 0, 0, 0))
    u = min(W, H * 1.15)
    cx, cy = W / 2, H / 2
    if kind in ("clear", "mostly_clear"):
        if is_day:
            _sun(img, cx, cy, u * 0.24)
        else:
            _crescent(img, cx, cy, u * 0.3)
            for sx, sy, ss in ((0.8, 0.2, 0.05), (0.88, 0.58, 0.035), (0.18, 0.8, 0.04)):
                _star(img, W * sx, H * sy, u * ss)
        if kind == "mostly_clear":
            _cloud(img, cx + u * 0.22, cy + u * 0.22, u * 0.42, "white")
    elif kind in ("partly", "mostly_cloudy"):
        if is_day:
            _sun(img, cx - u * 0.16, cy - u * 0.1, u * 0.18)
        else:
            _crescent(img, cx - u * 0.18, cy - u * 0.14, u * 0.22)
            _star(img, W * 0.86, H * 0.18, u * 0.035)
        _cloud(img, cx + u * 0.08, cy + u * 0.12, u * (0.62 if kind == "partly" else 0.78),
               "white" if kind == "partly" else "gray")
    elif kind == "cloudy":
        _cloud(img, cx - u * 0.12, cy - u * 0.08, u * 0.55, "dark")
        _cloud(img, cx + u * 0.06, cy + u * 0.06, u * 0.75, "gray")
    elif kind in ("fog", "haze", "smoke", "dust"):
        col = {"fog": (200, 208, 224), "haze": (232, 202, 124), "smoke": (172, 162, 162),
               "dust": (212, 162, 92)}[kind]
        _bands(img, col, W, H, S)
    elif kind == "wind":
        m = _mask(img)
        d = gfx.ImageDraw.Draw(m)
        width = int(S * 2.5)
        for y, l in ((0.32, 0.72), (0.5, 0.86), (0.68, 0.6)):
            d.line([W * 0.1, H * y, W * l, H * y], fill=255, width=width)
            d.arc([W * l - H * 0.08, H * y - H * 0.16, W * l + H * 0.08, H * y], 270, 180, fill=255, width=width)
        gfx.paint(img, m, gfx.vgrad(W, H, (200, 232, 255), (120, 184, 246)))
    else:
        tone = "dark" if kind in ("thunder", "heavy_rain") else "gray" if kind in ("rain", "freezing", "sleet", "mix") \
            else "white"
        _cloud(img, cx, cy - u * 0.14, u * 0.78, tone)
        top, bot = cy + u * 0.12, cy + u * 0.44
        if kind in ("rain", "drizzle", "showers", "heavy_rain", "freezing"):
            n = {"drizzle": 3, "showers": 3, "rain": 4, "heavy_rain": 5, "freezing": 4}[kind]
            _drops(img, cx, top, bot, u, n, short=kind == "drizzle", cold=kind == "freezing")
        if kind in ("snow", "flurries", "sleet", "mix"):
            _flakes(img, cx - (u * 0.06 if kind in ("sleet", "mix") else 0), top, bot, u, 2 if kind == "flurries" else 3)
            if kind in ("sleet", "mix"):
                _drops(img, cx + u * 0.42, top, bot, u * 0.6, 1)
        if kind == "thunder":
            _bolt(img, cx, cy, top, bot, u)
    out = gfx.Image.new("RGBA", (W, H), bg + (255,))
    if outline is not None:
        # grow the shapes' mask by a few pixels (shifted copies, cheap at any size)
        r = max(int(S * 1.25), int(min(W, H) * 0.014))   # at least a pixel once scaled down
        a = gfx.Image.new("L", (W + 2 * r + 2, H + 2 * r + 2), 0)   # padded, so shifts don't wrap
        a.paste(img.getchannel("A").point(lambda v: 255 if v > 96 else 0), (r + 1, r + 1))
        grown = a
        for k in (r, max(1, r // 2)):
            q = max(1, int(k * 0.7))
            for dx, dy in ((k, 0), (-k, 0), (0, k), (0, -k), (q, q), (q, -q), (-q, q), (-q, -q)):
                grown = gfx.ImageChops.lighter(grown, gfx.ImageChops.offset(a, dx, dy))
        rim = gfx.Image.new("RGBA", (W + 2 * r + 2, H + 2 * r + 2), tuple(outline[:3]) + (255,))
        rim.putalpha(grown)
        rim.alpha_composite(img, (r + 1, r + 1))
        # shapes that touch the edge would lose their rim there: shrink the drawing to fit
        img = rim.resize((W, H), gfx.LANCZOS)
    out.alpha_composite(img)
    return out.resize((w, h), gfx.LANCZOS).convert("RGB")


# ----------------------------------------------------------------------------
# the big numbers: a squared display face with rounded corners and flat stroke ends,
# drawn from strokes (a center line, its corners rounded, given a width)
# ----------------------------------------------------------------------------

DIGIT_STROKE = 0.19          # the stroke, as a share of the numerals' height
DIGIT_ROUND = 0.17           # the corners' radius along the stroke's middle
DIGIT_GAP = 0.15             # between numerals


def _glyphs():
    """Each character: (width, [(closed, [(x, y), ...]), ...]) in the numerals' height,
    the points along the strokes' middles (y down)."""
    w = DIGIT_STROKE
    T, M, B = w / 2, 0.5, 1 - w / 2

    def box(width):
        return w / 2, width - w / 2

    g = {}
    L, R = box(0.92)
    g["0"] = (0.92, [(True, [(L, T), (R, T), (R, B), (L, B)])])
    L, R = box(0.5)
    g["1"] = (0.5, [(False, [(L, T), (R, T), (R, B)])])
    L, R = box(0.88)
    g["2"] = (0.88, [(False, [(L, T), (R, T), (R, M), (L, M), (L, B), (R, B)])])
    g["3"] = (0.88, [(False, [(L, T), (R, T), (R, B), (L, B)]), (False, [(L + 0.2, M), (R, M)])])
    g["4"] = (0.88, [(False, [(L, T), (L, M), (R, M)]), (False, [(R, T), (R, B)])])
    g["5"] = (0.88, [(False, [(R, T), (L, T), (L, M), (R, M), (R, B), (L, B)])])
    g["6"] = (0.88, [(False, [(R, T), (L, T), (L, B), (R, B), (R, M), (L, M)])])
    g["7"] = (0.88, [(False, [(L, T), (R, T), (R, 0.42), (R - 0.16, 0.6), (R - 0.16, B)])])
    g["8"] = (0.88, [(True, [(L, T), (R, T), (R, M), (L, M)]), (True, [(L, M), (R, M), (R, B), (L, B)])])
    g["9"] = (0.88, [(False, [(L, B), (R, B), (R, T), (L, T), (L, M), (R, M)])])
    L, R = box(0.55)
    g["-"] = (0.55, [(False, [(L, M), (R, M)])])
    dw = w * 0.72                              # the degree sign: a small ring, a lighter stroke
    g["°"] = (0.4, [(True, [(dw / 2, dw / 2), (0.4 - dw / 2, dw / 2), (0.4 - dw / 2, 0.4 - dw / 2),
                            (dw / 2, 0.4 - dw / 2)], dw)])
    g[" "] = (0.3, [])
    return g


GLYPHS = _glyphs()


def _rounded(pts, closed, radius, step):
    """The points along a stroke's middle with its corners rounded (circular, `radius` or
    less where the sides are short), every `step` or so."""
    n = len(pts)
    out = []
    idx = range(n) if closed else range(n)
    for i in idx:
        p = pts[i]
        if not closed and i in (0, n - 1):
            out.append(p)
            continue
        a, b = pts[(i - 1) % n], pts[(i + 1) % n]
        d1 = (p[0] - a[0], p[1] - a[1])
        d2 = (b[0] - p[0], b[1] - p[1])
        l1, l2 = math.hypot(*d1), math.hypot(*d2)
        if l1 < 1e-9 or l2 < 1e-9:
            out.append(p)
            continue
        u1, u2 = (d1[0] / l1, d1[1] / l1), (d2[0] / l2, d2[1] / l2)
        turn = math.atan2(u1[0] * u2[1] - u1[1] * u2[0], u1[0] * u2[0] + u1[1] * u2[1])
        if abs(turn) < 1e-6:
            out.append(p)
            continue
        t = min(radius * abs(math.tan(turn / 2)), l1 * 0.48, l2 * 0.48)
        r = t / abs(math.tan(turn / 2))
        a0 = (p[0] - u1[0] * t, p[1] - u1[1] * t)
        side = 1 if turn > 0 else -1
        c = (a0[0] - u1[1] * r * side, a0[1] + u1[0] * r * side)
        start = math.atan2(a0[1] - c[1], a0[0] - c[0])
        k = max(2, int(abs(turn) * r / step))
        for j in range(k + 1):
            ang = start + turn * j / k
            out.append((c[0] + math.cos(ang) * r, c[1] + math.sin(ang) * r))
    return out


def _squared(pts, half):
    """An open stroke's ends carried on by half its width, so its flat ends meet the
    numeral's edges as the outside of its corners do."""
    def ext(p, q):
        dx, dy = p[0] - q[0], p[1] - q[1]
        ln = math.hypot(dx, dy) or 1.0
        return (p[0] + dx / ln * half, p[1] + dy / ln * half)
    return [ext(pts[0], pts[1])] + pts[1:-1] + [ext(pts[-1], pts[-2])]


def _outline(pts, closed, half):
    """The two edges of a stroke `half` either side of its middle: (left, right)."""
    n = len(pts)
    left, right = [], []
    for i in range(n):
        a = pts[i - 1] if (closed or i > 0) else pts[i]
        b = pts[(i + 1) % n] if (closed or i < n - 1) else pts[i]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln, dx / ln
        left.append((pts[i][0] + nx * half, pts[i][1] + ny * half))
        right.append((pts[i][0] - nx * half, pts[i][1] - ny * half))
    return left, right


def number_mask(text, height):
    """The big numerals of `text` drawn white on black, `height` pixels tall: an L image."""
    adv = [GLYPHS.get(ch, GLYPHS[" "])[0] for ch in text]
    width = (sum(adv) + DIGIT_GAP * max(0, len(text) - 1)) * height
    out = gfx.Image.new("L", (max(1, int(math.ceil(width)) + 2), max(1, int(height) + 2)), 0)
    x0 = 1.0
    for ch in text:
        gw, strokes = GLYPHS.get(ch, GLYPHS[" "])
        for stroke in strokes:
            closed, pts = stroke[0], stroke[1]
            sw = (stroke[2] if len(stroke) > 2 else DIGIT_STROKE) * height
            radius = (DIGIT_ROUND if len(stroke) < 3 else stroke[2] * 0.9) * height
            pts = [(x0 + x * height, 1 + y * height) for x, y in pts]
            if not closed:                      # the ends square to the numeral's edges
                pts = _squared(pts, sw / 2)
            path = _rounded(pts, closed, radius, max(0.5, height / 120))
            left, right = _outline(path, closed, sw / 2)
            d = gfx.ImageDraw.Draw(out)
            # the stroke a sliver at a time, so a stroke that crosses itself stays solid
            n = len(path)
            for i in range(n if closed else n - 1):
                j = (i + 1) % n
                d.polygon([left[i], left[j], right[j], right[i]], fill=255)
        x0 += (gw + DIGIT_GAP) * height
    return out


def number_cols(text, rows, cell_aspect=2.0):
    """How many cells wide the big number `text` drawn `rows` cells tall needs (cells
    `cell_aspect` times as tall as wide)."""
    if not text:
        return 3
    units = sum(GLYPHS.get(ch, GLYPHS[" "])[0] for ch in text) + DIGIT_GAP * max(0, len(text) - 1)
    return int(math.ceil(units * rows * cell_aspect * NUMBER_HEIGHT)) + 2


NUMBER_HEIGHT = 0.7          # the numerals' height, as a share of the picture's


def number_image(text, rgb, w, h, bg, light=False):
    """A big number (the temperature, the AQI) as a picture w x h: squared numerals with
    rounded corners, shaded in `rgb` (bright at the top, deep at the foot), a dark edge, a
    soft shadow under them and a faint glow of their own color; set at the left, upright
    in the middle."""
    bg = tuple(bg[:3])
    S = 3
    W, H = w * S, h * S
    out = gfx.Image.new("RGBA", (W, H), bg + (255,))
    if not text:
        return out.convert("RGB").resize((w, h), gfx.LANCZOS)
    cap = H * NUMBER_HEIGHT
    glyphs = number_mask(text, cap)
    room = W * 0.96
    if glyphs.width > room:                     # too wide: smaller, to fit
        cap *= room / glyphs.width
        glyphs = number_mask(text, cap)
    mask = gfx.Image.new("L", (W, H), 0)
    top_y = (H - cap) / 2
    mask.paste(glyphs, (int(W * 0.02), int(round(top_y)) - 1))
    foot_y = top_y + cap
    rgb = tuple(rgb[:3])
    hi = tuple(int(c + (255 - c) * 0.55) for c in rgb)
    lo = tuple(int(c * 0.78) for c in rgb)
    col = gfx.Image.new("RGBA", (1, H))
    rows = []
    for y in range(H):
        t = max(0.0, min(1.0, (y - top_y) / max(1.0, foot_y - top_y)))
        if t < 0.55:
            k = t / 0.55
            c = tuple(int(a + (b - a) * k) for a, b in zip(hi, rgb))
        else:
            k = (t - 0.55) / 0.45
            c = tuple(int(a + (b - a) * k) for a, b in zip(rgb, lo))
        rows.append(c + (255,))
    col.putdata(rows)
    fill = col.resize((W, H), gfx.NEAREST)
    if not light:
        gfx.soft(out, mask, rgb, 0.22, H * 0.09)               # a faint glow of its own color
    gfx.soft(out, mask, (0, 4, 24) if not light else (60, 60, 80), 0.6 if not light else 0.28, H * 0.03,
             H * 0.03, H * 0.045)
    edge = mask.filter(gfx.ImageFilter.MaxFilter(max(3, (int(H * 0.022) // 2) * 2 + 1)))
    gfx.paint(out, edge, tuple(int(c * 0.42) for c in rgb))
    gfx.paint(out, mask, fill)
    return out.convert("RGB").resize((w, h), gfx.LANCZOS)


def raindrop_icon(size=256):
    """skydrop's app icon: a raindrop on a rounded royal-blue tile (RGBA, transparent corners)."""
    if not gfx.HAVE_PIL:
        return None
    S = 4
    N = size * S
    img = gfx.Image.new("RGBA", (N, N), (0, 0, 0, 0))
    # the tile: navy at the top to royal blue at the bottom
    tile = gfx.Image.new("RGBA", (N, N), (0, 0, 0, 0))
    td = gfx.ImageDraw.Draw(tile)
    top, bot = (10, 22, 84), (34, 76, 178)
    for y in range(N):
        f = y / (N - 1)
        td.line([0, y, N, y], fill=tuple(int(a + (b - a) * f) for a, b in zip(top, bot)) + (255,))
    mask = gfx.Image.new("L", (N, N), 0)
    gfx.ImageDraw.Draw(mask).rounded_rectangle([N * 0.04, N * 0.04, N * 0.96, N * 0.96], radius=N * 0.2, fill=255)
    img.paste(tile, (0, 0), mask)
    # the drop: x = sin t * sin^m(t/2), y = cos t (point at the top)
    cx, cy, rw, rh = N * 0.5, N * 0.52, N * 0.27, N * 0.36
    pts = []
    for k in range(361):
        t = math.radians(k)
        x = math.sin(t) * math.sin(t / 2) ** 1.25
        y = math.cos(t)
        pts.append((cx + x * rw * 1.45, cy - y * rh))
    drop_mask = gfx.Image.new("L", (N, N), 0)
    gfx.ImageDraw.Draw(drop_mask).polygon(pts, fill=255)
    grad = gfx.Image.new("RGBA", (N, N), (0, 0, 0, 0))
    gd = gfx.ImageDraw.Draw(grad)
    y0, y1 = cy - rh, cy + rh
    c0, c1 = (176, 232, 255), (24, 116, 236)
    for y in range(int(y0), int(y1) + 1):
        f = max(0.0, min(1.0, (y - y0) / (y1 - y0)))
        gd.line([0, y, N, y], fill=tuple(int(a + (b - a) * f) for a, b in zip(c0, c1)) + (255,))
    # a soft dark rim so the drop stands off the tile
    rim = gfx.Image.new("RGBA", (N, N), (4, 12, 48, 170))
    rim.putalpha(drop_mask.filter(gfx.ImageFilter.GaussianBlur(N * 0.012)).point(lambda v: int(v * 0.75)))
    img.alpha_composite(rim, (int(N * 0.006), int(N * 0.012)))
    img.paste(grad, (0, 0), drop_mask)
    # highlight: a white sliver on the upper left of the bulb
    hl = gfx.Image.new("L", (N, N), 0)
    hd = gfx.ImageDraw.Draw(hl)
    hd.arc([cx - rw * 0.95, cy - rh * 0.1, cx + rw * 0.55, cy + rh * 0.88], 160, 235, fill=235, width=int(N * 0.035))
    img.paste(gfx.Image.new("RGBA", (N, N), (255, 255, 255, 255)), (0, 0), hl)
    hd2 = gfx.ImageDraw.Draw(img)
    r = N * 0.022
    hx, hy = cx - rw * 0.62, cy + rh * 0.02
    hd2.ellipse([hx - r, hy - r, hx + r, hy + r], fill=(255, 255, 255, 220))
    return img.resize((size, size), gfx.LANCZOS)
