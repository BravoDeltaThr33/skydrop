"""Charts: pixel versions for kitty/sixel terminals, text versions for the rest."""

import math

from . import gfx
from .text import BLOCKS, braille_plot, resample
from .theme import blend, full_palette


class Ink:
    """The colors a chart is drawn with, taken from the theme so charts read on any page,
    light or dark."""

    def __init__(self, theme=None, bg=None):
        p = theme.pal if theme is not None else full_palette("weatherstar")
        self.light = bool(p.get("light"))
        self.bg = tuple(bg) if bg is not None else p["bg"]
        self.grid, self.axis, self.day, self.text = p["faint"], p["label"], p["title"], p["value"]
        self.feels, self.pop = p["dim"], p["rain"]
        self.amount = blend(p["rain"], p["value"], 0.35)
        self.lo, self.hi = p["t_cold"], p["t_warm"]
        self.sun, self.sun_line = p["title_bg"], p["t_warm"]
        self.twilight, self.night = p["label"], p["faint"]
        self.band = (0, 0, 0, 12) if self.light else (255, 255, 255, 10)
        self.stops = [(0, p["t_frigid"]), (32, p["t_cold"]), (50, p["t_cool"]), (65, p["t_mild"]),
                      (75, p["t_warm"]), (85, p["t_hot"]), (100, p["t_scorch"])]

    def temp(self, c):
        return _temp_rgb(c, self)


def _temp_rgb(c, ink=None):
    """Color for a temperature in Celsius along the theme's cold-to-hot scale."""
    if ink is not None and not isinstance(ink, Ink):
        ink = Ink(ink)                     # a Theme
    if c is None:
        return ink.feels if ink else (200, 200, 200)
    f = c * 9 / 5 + 32
    stops = ink.stops if ink else Ink().stops
    if f <= stops[0][0]:
        return stops[0][1]
    for (f0, c0), (f1, c1) in zip(stops, stops[1:]):
        if f <= f1:
            return blend(c0, c1, (f - f0) / (f1 - f0))
    return stops[-1][1]


def _line_mask(size, pts, width):
    """An L mask of a polyline `width` wide (drawn at the picture's own scale)."""
    m = gfx.Image.new("L", size, 0)
    if len(pts) > 1:
        gfx.ImageDraw.Draw(m).line(pts, fill=255, width=max(1, int(round(width))), joint="curve")
    return m


def _strip(W, H, colors):
    """W x H RGBA, each column the color given for it."""
    st = gfx.Image.new("RGBA", (W, 1))
    st.putdata([tuple(c[:3]) + (255,) for c in colors])
    return st.resize((W, H), gfx.NEAREST)


def _tag(d, xy, text, fnt, fill, ink, anchor="mb", pad=(0.45, 0.18)):
    """A value in a rounded tag: `fill` behind it, `ink` for the text; xy is where the
    tag's `anchor` (mb: middle bottom, mt: middle top) goes."""
    x, y = xy
    try:
        bx0, by0, bx1, by1 = d.textbbox((0, 0), text, font=fnt, anchor="lt")
    except (AttributeError, TypeError, ValueError):
        bx0, by0, bx1, by1 = 0, 0, gfx.text_width(d, text, fnt), getattr(fnt, "size", 10)
    tw, th = bx1 - bx0, by1 - by0
    px, py = th * pad[0], th * pad[1] + 1
    w, h = tw + 2 * px, th + 2 * py
    x0 = x - w / 2
    y0 = y - h if anchor == "mb" else y
    d.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=h / 2, fill=fill)
    gfx.draw_text(d, (x0 + px - bx0, y0 + py - by0), text, fnt, ink, anchor="lt")
    return (x0, y0, x0 + w, y0 + h)


def hourly_chart(rows, F, w, h, bg=None, theme=None):
    """The next 48 hours: the temperature as a smooth line colored by how warm it is, with
    a glow under it; the feels-like dashed; the chance of precipitation in shaded columns
    (the amount brighter at their feet); the night hours a shade darker; a line every 5
    degrees, the 10s labeled; the day's high and low in tags."""
    ink = Ink(theme, bg)
    bg = ink.bg
    S = 3
    W, H = w * S, h * S
    img = gfx.Image.new("RGB", (W, H), bg)
    d = gfx.ImageDraw.Draw(img, "RGBA")             # (drawn over what's there, blended)
    if not rows:
        return img.resize((w, h), gfx.LANCZOS)
    fsz = max(10 * S / 2, H * 0.075)
    fnt = gfx.font(fsz, bold=True)
    small = gfx.font(max(9 * S / 2, fsz * 0.85), bold=False)
    left, right, top, bottom = int(fsz * 3.0), int(fsz * 1.2), int(fsz * 1.6), int(fsz * 2.2)
    pw, ph = W - left - right, H - top - bottom
    n = len(rows)
    temps = [F.tv(r.get("temp")) for r in rows]
    feels = [F.tv(r.get("feels")) for r in rows]
    vals = [v for v in temps + feels if v is not None]
    if not vals:
        return img.resize((w, h), gfx.LANCZOS)
    lo, hi = min(vals), max(vals)
    pad = max(2.0, (hi - lo) * 0.15)
    lo, hi = math.floor(lo - pad), math.ceil(hi + pad)
    colw = pw / n

    def X(i):
        return left + (i + 0.5) * colw

    def Y(v):
        return top + (hi - v) / (hi - lo) * ph

    def native(v):
        return None if v is None else (v - 32) * 5 / 9 if F.imp else v

    # the night hours, a shade darker
    shade = (0, 0, 0, 34) if not ink.light else (40, 50, 90, 22)
    for i, r in enumerate(rows):
        if r.get("is_day") is False:
            d.rectangle([left + i * colw, top, left + (i + 1) * colw, top + ph], fill=shade)
    # a line every 5 degrees, the 10s labeled (every 10 and 20 on a very wide range), each
    # on a whole pixel once the picture is shrunk, so it's crisp
    label_step = 10 if hi - lo <= 60 else 20
    step = label_step / 2
    k = math.ceil(lo / step)
    while k * step <= hi:
        v = k * step
        y = round(Y(v) / S) * S
        labeled = k % 2 == 0
        d.rectangle([left, y - S // 2, W - right, y - S // 2 + S - 1],
                    fill=ink.grid + ((140,) if labeled else (85,)))
        if labeled:
            gfx.draw_text(d, (left - 6 * S / 2, y), f"{v:.0f}°", small, ink.axis + (255,), anchor="rm")
        k += 1
    # the chance of precipitation: shaded columns, a hair apart, the amount brighter below
    gap = max(1, S)
    for i, r in enumerate(rows):
        p = r.get("pop")
        x0, x1 = left + i * colw + gap / 2, left + (i + 1) * colw - gap / 2
        if p:
            y = top + ph - p / 100 * ph
            a0, a1 = 48 + int(p * 1.0), 22 + int(p * 0.45)
            col = gfx.Image.new("RGBA", (1, max(1, int(top + ph - y))))
            hh = col.height
            col.putdata([ink.pop + (int(a0 + (a1 - a0) * (j / max(1, hh - 1))),) for j in range(hh)])
            col = col.resize((max(1, int(x1 - x0)), hh))
            img.paste(col, (int(x0), int(y)), col)
            d.rectangle([x0, y, x1, y + S], fill=blend(ink.pop, (255, 255, 255), 0.35) + (min(255, a0 + 60),))
        amt = (r.get("rain") or 0) + (r.get("snow") or 0)
        if amt > 0.05:                          # the amount: a narrower, brighter core at the foot
            hh = min(ph * 0.35, amt / 5.0 * ph * 0.35 + 3 * S)
            inset = (x1 - x0) * 0.2
            d.rounded_rectangle([x0 + inset, top + ph - hh, x1 - inset, top + ph], radius=min(2 * S, hh / 2),
                                fill=ink.amount + (205,))
    # day separators, each with its day in a tag
    for i, r in enumerate(rows):
        t = F.local(r["t"])
        if t.hour == 0 and i:
            xs = left + i * colw
            for yy in range(int(top), int(top + ph), 6 * S):
                d.line([xs, yy, xs, min(top + ph, yy + 3 * S)], fill=ink.day + (190,), width=S)
            _tag(d, (xs + 4 * S, top + 2 * S), f"{t:%a}", small, blend(bg, ink.day, 0.28) + (235,), ink.day + (255,),
                 anchor="mt")
        if t.hour % 3 == 0:
            lab = f"{t.hour % 12 or 12}{'a' if t.hour < 12 else 'p'}"
            gfx.draw_text(d, (X(i), top + ph + 6 * S / 2), lab, small, ink.axis + (255,), anchor="mt")
    # the lines: smooth curves through the hours, a gap where an hour is missing
    def runs(series):
        out, cur = [], []
        for i, v in enumerate(series):
            if v is None:
                if len(cur) > 1:
                    out.append(cur)
                cur = []
            else:
                cur.append((X(i), v))
        if len(cur) > 1:
            out.append(cur)
        return out

    def sample(run):
        f = smooth_curve([x for x, _v in run], [v for _x, v in run])
        x0, x1 = run[0][0], run[-1][0]
        steps = max(2, int((x1 - x0) / S))
        return [(x0 + (x1 - x0) * j / steps, f(x0 + (x1 - x0) * j / steps)) for j in range(steps + 1)]

    feel_w, temp_w = max(1.5, 1.6 * S), max(2.0, 2.6 * S)
    for run in runs(feels):                     # dashed: every other short stretch
        pts = [(x, Y(v)) for x, v in sample(run)]
        dash = int(8 * S)
        for j in range(0, len(pts) - 1, dash):
            if (j // dash) % 2 == 0:
                d.line(pts[j:j + dash // 2 + 1], fill=ink.feels + (230,), width=int(feel_w), joint="curve")
    temp_cols = [ink.temp(None)] * W
    line = gfx.Image.new("L", (W, H), 0)
    for run in runs(temps):
        pts = sample(run)
        for x, v in pts:
            for xx in range(int(x) - S, int(x) + S + 1):
                if 0 <= xx < W:
                    temp_cols[xx] = ink.temp(native(v))
        line = gfx.ImageChops.lighter(line, _line_mask((W, H), [(x, Y(v)) for x, v in pts], temp_w))
    colors = _strip(W, H, temp_cols)
    glow = line.filter(gfx.ImageFilter.GaussianBlur(4 * S)).point(lambda v: int(v * (0.55 if not ink.light else 0.3)))
    img.paste(colors, (0, 0), glow)
    img.paste(colors, (0, 0), line)
    # the high and low: a dot on the line and the value in a tag of its own color
    tv = [(v, i) for i, v in enumerate(temps) if v is not None]
    if tv:
        vmax, imax = max(tv)
        vmin, imin = min(tv)
        for v, i, above in ((vmax, imax, True), (vmin, imin, False)):
            x, y = X(i), Y(v)
            col = ink.temp(native(v))
            r = 3.2 * S
            d.ellipse([x - r, y - r, x + r, y + r], fill=col + (255,), outline=bg + (255,), width=max(1, S))
            text_ink = (14, 20, 48, 255) if sum(col) > 380 else (255, 255, 255, 255)
            _tag(d, (x, y - r - 3 * S) if above else (x, y + r + 3 * S), f"{v:.0f}°", fnt, col + (255,), text_ink,
                 anchor="mb" if above else "mt")
    # the key: a sample of each before its name
    lx, ly = left, 4 * S
    sw = 14 * S
    for name, col, kind in (("Temperature", ink.hi, "line"), ("Feels like", ink.feels, "dash"),
                            ("Precip chance", ink.pop, "box")):
        ym = ly + small.size * 0.55 if hasattr(small, "size") else ly + 6 * S
        if kind == "line":
            d.line([lx, ym, lx + sw, ym], fill=col + (255,), width=int(temp_w))
        elif kind == "dash":
            d.line([lx, ym, lx + sw * 0.4, ym], fill=col + (255,), width=int(feel_w))
            d.line([lx + sw * 0.6, ym, lx + sw, ym], fill=col + (255,), width=int(feel_w))
        else:
            d.rectangle([lx, ym - 4 * S, lx + sw, ym + 4 * S], fill=col + (150,))
        lx += sw + 4 * S
        gfx.draw_text(d, (lx, ly), name, small, col + (255,), anchor="lt")
        lx += gfx.text_width(d, name + "   ", small)
    return img.resize((w, h), gfx.LANCZOS)


def hourly_text(rows, F, width, height, ascii_only=False):
    """Braille temperature line over block-element precip bars."""
    n = len(rows)
    if not n or width < 10:
        return []
    temps = [F.tv(r.get("temp")) for r in rows]
    pops = [r.get("pop") for r in rows]
    tv = [t for t in temps if t is not None]
    if not tv:
        return []
    out = []
    lab_w = 5
    plot_w = max(8, width - lab_w)
    hi, lo = max(tv), min(tv)
    if ascii_only:
        from .text import spark
        out.append([(f"{hi:>3.0f}° ", "label"), (spark(resample(temps, plot_w), ascii_only=True), "t_warm")])
        out.append([(f"{lo:>3.0f}° ", "label"), (spark(resample(pops, plot_w), 0, 100, zero="_", ascii_only=True), "rain")])
        return out
    th = max(2, height - 2)
    plot = braille_plot([(temps, "t_warm")], plot_w, th, lo - 1, hi + 1)
    for i, ln in enumerate(plot):
        lab = f"{hi:>3.0f}° " if i == 0 else f"{lo:>3.0f}° " if i == th - 1 else " " * lab_w
        out.append([(lab, "label")] + ln)
    bars = []
    for p in resample(pops, plot_w):
        k = 0 if not p else max(1, int(round(p / 100 * 7)))
        bars.append(BLOCKS[k] if p else " ")
    out.append([("rain ", "label"), ("".join(bars), "rain")])
    # hour axis
    axis = [" "] * plot_w
    for i, r in enumerate(rows):
        hr = F.local(r["t"]).hour
        if hr % 6 == 0:
            pos = int(i * plot_w / n)
            lab = f"{hr % 12 or 12}{'a' if hr < 12 else 'p'}" if hr else F.local(r["t"]).strftime("%a")
            for k, ch in enumerate(lab):
                if pos + k < plot_w:
                    axis[pos + k] = ch
    out.append([(" " * lab_w, "text"), ("".join(axis), "dim")])
    return out


# the moon's seas, as seen from the north: (x, y, half-width, half-height, how dark), x to the
# right and y down, in the disc's radii
LUNAR_MARIA = ((-0.42, -0.02, 0.30, 0.46, 0.55), (-0.25, -0.40, 0.27, 0.20, 0.62), (0.12, -0.33, 0.15, 0.14, 0.62),
               (0.27, -0.10, 0.19, 0.16, 0.62), (0.62, -0.26, 0.10, 0.09, 0.7), (0.47, 0.14, 0.12, 0.15, 0.5),
               (0.32, 0.27, 0.09, 0.09, 0.5), (-0.17, 0.36, 0.18, 0.13, 0.5), (-0.50, 0.36, 0.09, 0.09, 0.55),
               (0.02, -0.64, 0.42, 0.06, 0.45), (0.04, 0.0, 0.10, 0.08, 0.38))
# bright young craters: (x, y, radius, rays?)
LUNAR_CRATERS = ((-0.12, 0.66, 0.035, True), (-0.32, -0.12, 0.03, True), (-0.52, -0.07, 0.022, False),
                 (-0.66, -0.20, 0.02, False), (0.36, 0.52, 0.02, False))


def moon_image(frac, w, h, bg=(11, 22, 92), waxing=True, theme=None):
    """The moon as it is tonight: its face with the seas and bright craters, darker toward
    the edge; the lit part by the phase (frac: 0 new, 0.5 full) with a soft terminator, the
    rest faintly lit by the earth; a glow as bright as it's full; a few faint stars."""
    import random
    ink = Ink(theme, bg)
    bg = ink.bg
    S = 3
    W, H = w * S, h * S
    img = gfx.Image.new("RGB", (W, H), bg)
    r = min(W, H) * 0.4
    cx, cy = W / 2, H / 2
    box = [cx - r, cy - r, cx + r, cy + r]
    rng = random.Random(7)
    if not ink.light:                           # a few faint stars, away from the moon
        d = gfx.ImageDraw.Draw(img, "RGBA")
        for _ in range(14):
            x, y = rng.uniform(0, W), rng.uniform(0, H)
            if math.hypot(x - cx, y - cy) < r * 1.5:
                continue
            q = rng.uniform(0.5, 1.2) * S
            d.ellipse([x - q, y - q, x + q, y + q], fill=(230, 236, 255, rng.randint(70, 170)))
    # the face: bright highlands, the seas darker, a speckle of small craters
    face = gfx.Image.new("L", (W, H), 0)
    fd = gfx.ImageDraw.Draw(face)
    fd.ellipse(box, fill=232)
    seas = gfx.Image.new("L", (W, H), 255)
    sd = gfx.ImageDraw.Draw(seas)
    for x, y, rx, ry, dark in LUNAR_MARIA:
        sd.ellipse([cx + (x - rx) * r, cy + (y - ry) * r, cx + (x + rx) * r, cy + (y + ry) * r],
                   fill=int(255 - dark * 170))
    seas = seas.filter(gfx.ImageFilter.GaussianBlur(r * 0.07))
    face = gfx.ImageChops.multiply(face, seas)
    pits = gfx.Image.new("L", (W, H), 255)
    pd = gfx.ImageDraw.Draw(pits)
    for _ in range(40):
        a, rho = rng.uniform(0, 2 * math.pi), r * math.sqrt(rng.uniform(0, 0.9))
        x, y, q = cx + math.cos(a) * rho, cy + math.sin(a) * rho, r * rng.uniform(0.01, 0.032)
        pd.ellipse([x - q, y - q, x + q, y + q], fill=rng.randint(222, 242))
    face = gfx.ImageChops.multiply(face, pits.filter(gfx.ImageFilter.GaussianBlur(r * 0.01)))
    shine = gfx.Image.new("L", (W, H), 0)
    hd = gfx.ImageDraw.Draw(shine)
    for x, y, q, rays in LUNAR_CRATERS:
        px, py, qq = cx + x * r, cy + y * r, q * r
        hd.ellipse([px - qq, py - qq, px + qq, py + qq], fill=90)
        if rays:
            for k in range(7):
                a = k * 2 * math.pi / 7 + 0.3
                hd.line([px, py, px + math.cos(a) * r * 0.24, py + math.sin(a) * r * 0.24], fill=16,
                        width=max(1, int(S * 1.5)))
    face = gfx.ImageChops.add(face, shine.filter(gfx.ImageFilter.GaussianBlur(r * 0.02)))
    limb = gfx.radial_mask(W, H, cx, cy, r).point(lambda v: 255 - int(v * 0.22))   # darker toward the edge
    face = gfx.ImageChops.multiply(face, limb)
    to_rgb = (lambda lo, hi: gfx.Image.merge("RGB", [face.point(lambda v, a=a, b=b: int(a + (b - a) * v / 255))
                                                       for a, b in zip(lo, hi)]))
    day = to_rgb((70, 68, 64), (250, 246, 232))
    # the rest, faintly lit by the earth (on a light page, a pale shadow rather than a dark hole)
    night = to_rgb((10, 12, 22), (54, 62, 92)) if not ink.light else to_rgb((120, 128, 150), (182, 190, 210))
    # the lit part: the limb on the sunward side, the terminator an ellipse across
    phase = frac % 1.0
    illum = (1 - math.cos(2 * math.pi * phase)) / 2
    lit = gfx.Image.new("L", (W, H), 0)
    side = 1 if phase < 0.5 else -1             # waxing: lit on the right
    c = math.cos(2 * math.pi * phase)
    pts = []
    for k in range(0, 181):
        t = math.radians(k - 90)
        y = math.sin(t)
        half = math.sqrt(max(0.0, 1 - y * y))
        pts.append((cx + side * c * half * r, cy + y * r))         # the terminator, top to bottom
    for k in range(180, -1, -1):
        t = math.radians(k - 90)
        y = math.sin(t)
        pts.append((cx + side * math.sqrt(max(0.0, 1 - y * y)) * r, cy + y * r))   # the limb, back up
    gfx.ImageDraw.Draw(lit).polygon(pts, fill=255)
    lit = lit.filter(gfx.ImageFilter.GaussianBlur(r * 0.035))
    disc = gfx.Image.new("L", (W, H), 0)
    gfx.ImageDraw.Draw(disc).ellipse(box, fill=255)
    glow = gfx.ImageChops.multiply(gfx.ImageChops.multiply(lit, disc).filter(gfx.ImageFilter.GaussianBlur(r * 0.22)),
                                   gfx.frame_fade(W, H))
    if not ink.light and illum > 0.02:
        img.paste(gfx.Image.new("RGB", (W, H), (200, 212, 255)), (0, 0),
                  glow.point(lambda v: int(v * (0.12 + 0.3 * illum))))
    moon = gfx.Image.composite(day, night, lit)
    img.paste(moon, (0, 0), disc)
    if ink.light:
        # on a light page the lit face would melt into the background: ring it
        gfx.ImageDraw.Draw(img).ellipse(box, outline=ink.feels, width=max(2, S))
    return img.resize((w, h), gfx.LANCZOS)


ASCII_MOON = {
    "New moon": ["   _..._   ", " .:::::::. ", ":::::::::::", ":::::::::::", "`:::::::::'", "  `':::'`  "],
    "Waxing crescent": ["   _..._   ", " .::::. `. ", ":::::::  :", ":::::::  :", "`::::::  .'", "  `'::.'`  "],
    "First quarter": ["   _..._   ", " .::::  `. ", "::::::    :", "::::::    :", "`:::::   .'", "  `'::.'`  "],
    "Waxing gibbous": ["   _..._   ", " .::'   `. ", ":::       :", ":::       :", "`::.     .'", "  `':..'`  "],
    "Full moon": ["   _..._   ", " .'     `. ", ":         :", ":         :", "`.       .'", "  `-...-'  "],
    "Waning gibbous": ["   _..._   ", " .'   `::. ", ":       :::", ":       :::", "`.     .::'", "  `-..:'`  "],
    "Last quarter": ["   _..._   ", " .'  ::::. ", ":    ::::::", ":    ::::::", "`.   :::::'", "  `-.::'`  "],
    "Waning crescent": ["   _..._   ", " .' .::::. ", ":  :::::::", ":  :::::::", "`. ::::::'", "  `-.::'`  "],
}


def sun_path_image(points, now_t, F, w, h, bg=None, theme=None):
    """Solar elevation across the day: points [(epoch, altitude)]."""
    ink = Ink(theme, bg)
    bg = ink.bg
    S = 2
    W, H = w * S, h * S
    img = gfx.Image.new("RGB", (W, H), bg)
    d = gfx.ImageDraw.Draw(img, "RGBA")
    if not points:
        return img.resize((w, h))
    fnt = gfx.font(max(9, int(H * 0.11)), bold=True)
    top, bottom = H * 0.12, H * 0.82
    horizon = top + (bottom - top) * 0.62
    amax = max(10.0, max(a for _t, a in points))
    amin = min(-20.0, min(a for _t, a in points))

    def Y(a):
        if a >= 0:
            return horizon - a / amax * (horizon - top)
        return horizon + a / amin * (bottom - horizon)

    n = len(points)
    for i, (t, a) in enumerate(points):
        x = i * W / max(1, n - 1)
        col = ink.sun if a > 0 else ink.twilight if a > -6 else ink.night
        d.line([x, horizon, x, Y(a)], fill=col + (110 if ink.light else 60,), width=S)
    d.line([(i * W / max(1, n - 1), Y(a)) for i, (t, a) in enumerate(points)], fill=ink.sun_line + (255,), width=S * 2)
    d.line([0, horizon, W, horizon], fill=ink.axis + (200,), width=S)
    # now marker
    t0, t1 = points[0][0], points[-1][0]
    if t0 <= now_t <= t1:
        i = (now_t - t0) / (t1 - t0) * (n - 1)
        a = points[int(i)][1]
        x = (now_t - t0) / (t1 - t0) * W
        r = S * 5
        d.ellipse([x - r, Y(a) - r, x + r, Y(a) + r], fill=ink.sun + (255,), outline=ink.text + (255,))
    for hr in (6, 12, 18):
        x = hr / 24 * W
        gfx.draw_text(d, (x, H - 4), {6: "6a", 12: "noon", 18: "6p"}[hr], fnt, ink.axis + (255,), anchor="mb")
    return img.resize((w, h), gfx.LANCZOS)


def _interp(vals, n):
    """Linear interpolation of day-centered values onto n samples (keeps gaps)."""
    m = len(vals)
    if m == 0 or n <= 0:
        return []
    out = []
    for i in range(n):
        x = (i + 0.5) * m / n - 0.5
        a = max(0, min(m - 1, int(math.floor(x))))
        b = min(m - 1, a + 1)
        f = min(1.0, max(0.0, x - a))
        va, vb = vals[a], vals[b]
        if va is None or vb is None:
            out.append(va if f < 0.5 else vb)
        else:
            out.append(va + (vb - va) * f)
    return out


def daily_chart(rows, F, w, h, bg=None, theme=None):
    """The days ahead: the highs and the lows as smooth curves, the band between them shaded
    in the colors of the temperatures it spans, each day's high and low marked and set
    above and below; the chance of precipitation in columns along the bottom; every other
    day a shade lighter, today picked out."""
    ink = Ink(theme, bg)
    bg = ink.bg
    S = 3
    W, H = w * S, h * S
    img = gfx.Image.new("RGB", (W, H), bg)
    d = gfx.ImageDraw.Draw(img, "RGBA")
    rows = [r for r in rows if r.get("hi") is not None or r.get("lo") is not None]
    if not rows:
        return img.resize((w, h), gfx.LANCZOS)
    n = len(rows)
    fsz = max(10 * S / 2, H * 0.085)
    fnt = gfx.font(fsz, bold=True)
    small = gfx.font(max(9 * S / 2, fsz * 0.8), bold=False)
    top, bottom = int(fsz * 1.6), int(fsz * 1.8)
    ph = H - top - bottom
    colw = W / n
    his = [F.tv(r.get("hi")) for r in rows]
    los = [F.tv(r.get("lo")) for r in rows]
    vals = [x for x in his + los if x is not None]
    lo, hi = min(vals), max(vals)
    pad = max(2.0, (hi - lo) * 0.25)
    lo, hi = lo - pad, hi + pad

    def X(i):
        return (i + 0.5) * colw

    span_y = ph * 0.66                          # the temperatures above; the rain chances below

    def Y(v):
        return top + (hi - v) / (hi - lo) * span_y

    def native(v):
        return (v - 32) * 5 / 9 if F.imp else v

    # every other day a shade lighter; today picked out
    for i, r in enumerate(rows):
        if r["date"] == F.today():
            d.rounded_rectangle([i * colw + S, top - fsz * 0.4, (i + 1) * colw - S, top + ph],
                                radius=6 * S, fill=(255, 255, 255, 16) if not ink.light else (0, 0, 0, 14))
        elif i % 2:
            d.rectangle([i * colw, top - fsz * 0.4, (i + 1) * colw, top + ph], fill=ink.band)
    # the chance of precipitation: columns rising from the bottom, brighter at their tops
    bar_h = ph * 0.14
    base = top + ph
    for i, r in enumerate(rows):
        pop = r.get("pop") or 0
        if not pop:
            continue
        bh = max(2 * S, bar_h * pop / 100)
        x0, x1 = X(i) - colw * 0.28, X(i) + colw * 0.28
        col = gfx.Image.new("RGBA", (1, max(1, int(bh))))
        hh = col.height
        a0, a1 = 120 + int(pop), 60 + int(pop * 0.6)
        col.putdata([ink.pop + (min(255, int(a0 + (a1 - a0) * j / max(1, hh - 1))),) for j in range(hh)])
        col = col.resize((max(1, int(x1 - x0)), hh))
        img.paste(col, (int(x0), int(base - bh)), col)
        d.rectangle([x0, base - bh, x1, base - bh + S], fill=blend(ink.pop, (255, 255, 255), 0.4) + (230,))
        if pop >= 10:
            gfx.draw_text(d, (X(i), base - bh - 2 * S), f"{pop:.0f}%", small, ink.pop + (255,), anchor="mb")
    # the curves, smooth through the days
    def curve(series):
        known = [(X(i), v) for i, v in enumerate(series) if v is not None]
        if len(known) < 2:
            return None
        f = smooth_curve([x for x, _v in known], [v for _x, v in known])
        x0, x1 = known[0][0], known[-1][0]
        steps = max(2, int((x1 - x0) / S))
        return [(x0 + (x1 - x0) * j / steps, f(x0 + (x1 - x0) * j / steps)) for j in range(steps + 1)]
    hc, lc = curve(his), curve(los)
    # the colors by height: each level the color of the temperature there
    levels = gfx.Image.new("RGB", (1, H))
    levels.putdata([ink.temp(native(hi - (y - top) / span_y * (hi - lo))) for y in range(H)])
    levels = levels.resize((W, H), gfx.NEAREST)
    if hc and lc:
        band = gfx.Image.new("L", (W, H), 0)
        poly = [(x, Y(v)) for x, v in hc] + [(x, Y(v)) for x, v in reversed(lc)]
        gfx.ImageDraw.Draw(band).polygon(poly, fill=int(255 * (0.3 if not ink.light else 0.22)))
        img.paste(levels, (0, 0), band)
    lines = gfx.Image.new("L", (W, H), 0)
    for c in (hc, lc):
        if c:
            lines = gfx.ImageChops.lighter(lines, _line_mask((W, H), [(x, Y(v)) for x, v in c], 2.4 * S))
    if not ink.light:
        img.paste(levels, (0, 0), lines.filter(gfx.ImageFilter.GaussianBlur(3 * S)).point(lambda v: int(v * 0.5)))
    img.paste(levels, (0, 0), lines)
    # each day's high and low: a dot, the value above (the high) or below (the low)
    for i in range(n):
        for v, raw, above in ((his[i], rows[i].get("hi"), True), (los[i], rows[i].get("lo"), False)):
            if v is None:
                continue
            x, y = X(i), Y(v)
            r = 3.4 * S
            col = ink.temp(raw)
            d.ellipse([x - r, y - r, x + r, y + r], fill=col + (255,), outline=bg + (255,), width=max(1, S))
            gfx.draw_text(d, (x, y - r - 2 * S) if above else (x, y + r + 2 * S), f"{v:.0f}°", fnt,
                          col + (255,), anchor="mb" if above else "mt", stroke=S, stroke_fill=bg)
    # the days along the bottom
    for i, r in enumerate(rows):
        today = r["date"] == F.today()
        lab = "Today" if today else f"{r['date']:%a}"
        gfx.draw_text(d, (X(i), H - 3 * S / 2), lab, fnt if today else small,
                      (ink.day if today else ink.axis) + (255,), anchor="mb")
    return img.resize((w, h), gfx.LANCZOS)


def daily_text(rows, F, width, height, ascii_only=False):
    """Text version: braille high/low lines, rain-chance blocks and a day axis."""
    rows = [r for r in rows if r.get("hi") is not None or r.get("lo") is not None]
    n = len(rows)
    if not n or width < 20:
        return []
    lab_w = 5
    colw = max(3, (width - lab_w) // n)
    pw = colw * n
    his = [F.tv(r.get("hi")) for r in rows]
    los = [F.tv(r.get("lo")) for r in rows]
    vals = [x for x in his + los if x is not None]
    hi, lo = max(vals), min(vals)
    out = []
    if ascii_only:
        from .text import spark
        out.append([(f"{hi:>3.0f}° ", "label"), (spark(_interp(his, pw), lo, hi, ascii_only=True), "t_warm")])
        out.append([(f"{lo:>3.0f}° ", "label"), (spark(_interp(los, pw), lo, hi, ascii_only=True), "t_cool")])
    else:
        th = max(2, height - 3)
        plot = braille_plot([(_interp(los, pw * 2), "t_cool"), (_interp(his, pw * 2), "t_warm")], pw, th, lo - 1, hi + 1)
        for i, ln in enumerate(plot):
            lab = f"{hi:>3.0f}° " if i == 0 else f"{lo:>3.0f}° " if i == th - 1 else " " * lab_w
            out.append([(lab, "label")] + ln)
    rain = []
    for r in rows:
        p = r.get("pop") or 0
        k = 0 if p < 5 else max(1, int(round(p / 100 * 7)))
        cell = (BLOCKS[k] if not ascii_only else " .:-=+*#"[k]) if k else " "
        txt = (f"{p:.0f}%" if p >= 10 else "").center(colw)
        rain.append((txt if p >= 10 and colw >= 4 else cell * (colw - 1) + " ", "rain"))
    out.append([("rain ", "label")] + rain)
    out.append([(" " * lab_w, "text")] + [(("Today" if r["date"] == F.today() else f"{r['date']:%a}")[:colw - 1].center(colw), "dim")
                                          for r in rows])
    return out


def bars_image(values, lo, hi, colors, w, h, bg):
    """Solid bars, one a value, each outlined in a darker shade of its own color with a
    pixel of the card between neighbors, on `bg`: a station's last 24 hours."""
    return columns_image(values, lo, hi, colors, w, h, bg)


def columns_image(values, lo, hi, colors, w, h, bg, cols=1, rows=1, head=0, left=0, tops=(), guides=(), axis=(),
                  axis_ink=((150, 150, 150), (90, 90, 90))):
    """Solid columns as a picture w x h (cols x rows cells), one a value, each outlined in a
    darker shade of its own color (`colors`, one a value) with a pixel of the card between
    neighbors. `head` rows over the plot are kept for the values' text, `tops` [(i, text,
    rgb)], set over their columns; `guides` [(value, rgb)] run dashed behind the columns;
    `left` cells on the left hold the scale, `axis` [(value, text)], in axis_ink (text,
    line)."""
    bg = tuple(bg)
    img = gfx.Image.new("RGB", (w, h), bg)
    d = gfx.ImageDraw.Draw(img)
    n = max(1, len(values))
    cell_w, cell_h = w / max(1, cols), h / max(1, rows)
    x0 = int(round(left * cell_w))
    top = int(round(head * cell_h))
    ph = h - top
    span = (hi - lo) or 1.0
    step = (w - x0) / n
    edge = max(1, round(min(step, ph) / 14))

    def Y(v):
        return top + (1 - max(0.0, min(1.0, (v - lo) / span))) * ph

    dash, gap = max(3, int(cell_w * 0.4)), max(2, int(cell_w * 0.3))
    for value, rgb in guides:
        if value is None or not lo < value < hi:
            continue
        y = min(h - 1, int(Y(value)))
        for x in range(x0, w, dash + gap):
            d.line([(x, y), (min(w - 1, x + dash - 1), y)], fill=tuple(rgb), width=1)
    for i, v in enumerate(values):
        if v is None or v <= lo:
            continue
        frac = max(0.0, min(1.0, (v - lo) / span))
        y = h - max(edge * 2 + 2, round(frac * ph))
        xa, xb = x0 + round(i * step) + 1, x0 + round((i + 1) * step) - 1
        if xb - xa < edge * 2 + 1:
            xa, xb = x0 + round(i * step), x0 + round((i + 1) * step) - 1
        col = tuple(colors[i])
        d.rectangle([xa, y, xb, h - 1], fill=col, outline=blend(col, (0, 0, 0), 0.5), width=edge)
    if axis and x0 > 0:
        txt, ln = axis_ink
        ax = x0 - int(cell_w * 0.5)
        d.line([(ax, 0), (ax, h - 1)], fill=tuple(ln), width=1)
        size = cell_h * 0.6
        small = gfx.font(size, bold=False)
        placed = []
        for value, text in reversed(list(axis)):     # the marks before the scale's top
            if not lo <= value <= hi:
                continue
            y = min(h - 1, int(Y(value)))
            ty = max(size * 0.6, min(h - size * 0.6, y))
            if any(abs(ty - p) < size * 1.05 for p in placed):
                continue                        # too close to one already there
            placed.append(ty)
            d.line([(ax - int(cell_w * 0.45), y), (ax, y)], fill=tuple(ln), width=1)
            gfx.draw_text(d, (ax - cell_w * 0.9, ty), text, small, tuple(txt), anchor="rm")
    if tops:
        bold = gfx.font(cell_h * 0.62, bold=True)
        for i, text, rgb in tops:
            if not text or i >= len(values) or values[i] is None:
                continue
            x = x0 + (i + 0.5) * step
            y = Y(values[i]) - max(2.0, cell_h * 0.15)
            gfx.draw_text(d, (min(w - 2, max(x0 + 2, x)), max(cell_h * 0.7, y)), text, bold, tuple(rgb), anchor="md",
                          stroke=1, stroke_fill=bg)
    return img


def smooth_curve(xs, ys):
    """A smooth curve through the points (x ascending): a monotone cubic, so it rises and
    falls only where the points do and never swings past them (a 0% hour stays at 0%,
    a peak stays the peak). Returns f(x); flat beyond the first and last points."""
    n = len(xs)
    if n == 0:
        return lambda x: None
    if n == 1:
        return lambda x: ys[0]
    h = [xs[k + 1] - xs[k] for k in range(n - 1)]
    d = [(ys[k + 1] - ys[k]) / h[k] for k in range(n - 1)]
    m = [0.0] * n
    if n == 2:
        m[0] = m[1] = d[0]
    else:
        for k in range(1, n - 1):
            if d[k - 1] * d[k] > 0:             # a weighted harmonic mean of the slopes either side
                w1, w2 = 2 * h[k] + h[k - 1], h[k] + 2 * h[k - 1]
                m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k])

        def end(h0, h1, d0, d1):
            s = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
            if s * d0 <= 0:
                return 0.0
            if d0 * d1 <= 0 and abs(s) > abs(3 * d0):
                return 3 * d0
            return s
        m[0] = end(h[0], h[1], d[0], d[1])
        m[-1] = end(h[-1], h[-2], d[-1], d[-2])

    def f(x):
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        lo_k, hi_k = 0, n - 1
        while hi_k - lo_k > 1:
            mid = (lo_k + hi_k) // 2
            if xs[mid] <= x:
                lo_k = mid
            else:
                hi_k = mid
        k = lo_k
        hk = h[k]
        t = (x - xs[k]) / hk
        t2, t3 = t * t, t * t * t
        return ((2 * t3 - 3 * t2 + 1) * ys[k] + (t3 - 2 * t2 + t) * hk * m[k]
                + (-2 * t3 + 3 * t2) * ys[k + 1] + (t3 - t2) * hk * m[k + 1])
    return f


AREA_TOP, AREA_BASE = 0.8, 0.38        # the fill's strength at the top of the plot, and at the base
AREA_LINE = 0.2                         # the line along the top: its color, this much lighter (darker
                                        # on a light card)


def area_image(values, lo, hi, w, h, bg, color, cols=1, rows=1, head=0, left=0, tops=(), guides=(), axis=(),
               axis_ink=((150, 150, 150), (90, 90, 90)), band=0, bars=(), line=(), line_scale=(0.0, 100.0),
               line_color=None):
    """An area chart as a picture `w` x `h` pixels, `cols` x `rows` cells: a smooth curve through
    the values (each at the middle of its share of the width), the area under it shaded
    from just below the curve to darker at the base, a crisp line along the top. `color(p,
    value)` is the color where the curve is at position p (in values, fractional) and that
    value. `head` rows over the plot are kept for each value's text, `tops` [(i, text, rgb)],
    set over the curve; `guides` [(value, rgb)] run across dashed; `left` cells on the left
    hold the scale, `axis` [(value, text)], in axis_ink (text, line). `band` rows at the
    foot hold a bar for some of the values, `bars` [(i, fraction, rgb, text)]: each as tall
    as its fraction of the band (less a line for its text, set over it), in its color with
    a darker rim; the area stands on the band, its shade fading out before it. `line`, a
    second series (one value for each of the area's, None where there's none), runs over
    the area as a smooth line on its own scale, `line_scale` (lo at the area's floor, hi at
    the top of the plot), colored along its length by line_color(value); the values' text
    stays over it."""
    bg = tuple(bg)
    img = gfx.Image.new("RGB", (w, h), bg)
    n = len(values)
    known = [(i, v) for i, v in enumerate(values) if v is not None]
    if not n or not known or w < 4 or h < 4:
        return img
    cell_w, cell_h = w / max(1, cols), h / max(1, rows)
    x0 = int(round(left * cell_w))
    pw = w - x0
    lw = max(1.5, min(3.0, cell_h * 0.11))     # the line's width
    top = int(round(head * cell_h)) + int(math.ceil(lw / 2)) + 1   # the line at the top stays whole
    base = h - int(round(band * cell_h)) if band else h      # the area's floor
    base = max(top + 4, base)
    ph = base - top
    span = (hi - lo) or 1.0

    def X(p):
        return x0 + (p + 0.5) * pw / n

    def Y(v):
        return top + (1.0 - max(0.0, min(1.0, (v - lo) / span))) * ph

    curve = smooth_curve([X(i) for i, _v in known], [max(lo, min(hi, v)) for _i, v in known])
    S = 4                                       # drawn 4x over and shrunk: smooth edges
    Ws, Hs = w * S, h * S
    xs = [x0 + k * 0.5 for k in range(0, 2 * pw + 1)]
    pts = [(x * S, Y(curve(x)) * S) for x in xs]
    shape = gfx.Image.new("L", (Ws, Hs), 0)
    gfx.ImageDraw.Draw(shape).polygon([(x0 * S, base * S)] + pts + [(Ws, base * S)], fill=255)
    shape = shape.resize((w, h), gfx.BOX)
    # the shade: strongest at the top of the plot, fading toward the base (over a band of
    # bars, fading out altogether, so the area floats over them)
    strip = gfx.Image.new("L", (1, h))
    if band:
        strip.putdata([int(255 * AREA_TOP * (1.0 - max(0.0, min(1.0, (y - top) / max(1.0, ph)))) ** 1.4)
                       for y in range(h)])
    else:
        strip.putdata([int(255 * (AREA_TOP - (AREA_TOP - AREA_BASE) * max(0.0, min(1.0, (y - top) / max(1.0, ph)))))
                       for y in range(h)])
    mask = gfx.ImageChops.multiply(shape, strip.resize((w, h), gfx.NEAREST))
    # the colors, column by column, where the curve is: blended a little either side of
    # where one gives way to the next
    cols = [tuple(color((x + 0.5 - x0) * n / pw - 0.5, curve(x + 0.5))) for x in range(x0, w)]
    cols = [cols[0]] * x0 + cols
    strip = gfx.Image.new("RGB", (w, 1))
    strip.putdata(cols)
    soft = max(1, int(pw / n * 0.3))
    strip = strip.filter(gfx.ImageFilter.BoxBlur(soft)) if soft > 1 else strip
    paint = strip.resize((w, h), gfx.NEAREST)
    img = gfx.Image.composite(paint, img, mask)
    # the line: a touch lighter than the area on a dark card, a touch darker on a light one
    toward = (0, 0, 0) if 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2] > 140 else (255, 255, 255)
    bright = gfx.Image.blend(paint, gfx.Image.new("RGB", (w, h), toward), AREA_LINE)
    # the line along the top, where there's any area under it
    crest = gfx.Image.new("L", (Ws, Hs), 0)
    ld = gfx.ImageDraw.Draw(crest)
    run = []
    floor_y = Y(lo) - 0.35
    for px, py in pts:
        if py / S < floor_y:
            run.append((px, py))
        elif run:
            if len(run) > 1:
                ld.line(run, fill=255, width=int(round(lw * S)), joint="curve")
            run = []
    if len(run) > 1:
        ld.line(run, fill=255, width=int(round(lw * S)), joint="curve")
    crest = crest.resize((w, h), gfx.BOX)
    img = gfx.Image.composite(bright, img, crest)
    d = gfx.ImageDraw.Draw(img)
    # guides across, dashed, on whole pixels so they're crisp
    dash, gap = max(3, int(cell_w * 0.4)), max(2, int(cell_w * 0.3))
    for value, rgb in guides:
        if value is None or not lo < value <= hi:
            continue
        y = min(h - 1, int(Y(value)))
        for x in range(x0, w, dash + gap):
            d.line([(x, y), (min(w - 1, x + dash - 1), y)], fill=tuple(rgb), width=1)
    # the scale on the left: a line down, a tick and the number at each mark
    if axis and x0 > 0:
        txt, ln = axis_ink
        ax = x0 - int(cell_w * 0.5)
        d.line([(ax, 0), (ax, h - 1)], fill=tuple(ln), width=1)
        size = cell_h * 0.6
        small = gfx.font(size, bold=False)
        placed = []
        for value, text in reversed(list(axis)):     # the marks before the scale's top
            if not lo <= value <= hi:
                continue
            y = min(h - 1, int(Y(value)))
            ty = max(size * 0.6, min(h - size * 0.6, y))         # the number kept whole
            if any(abs(ty - p) < size * 1.05 for p in placed):
                continue                        # too close to one already there
            placed.append(ty)
            d.line([(ax - int(cell_w * 0.45), y), (ax, y)], fill=tuple(ln), width=1)
            gfx.draw_text(d, (ax - cell_w * 0.9, ty), text, small, tuple(txt), anchor="rm")
    # the second series' line over the area, under the values' text
    if line and line_color is not None:
        img = _line_over(img, list(line)[:n], line_scale, line_color, n, x0, pw, top, base, cell_h, bg)
        d = gfx.ImageDraw.Draw(img)
    # each value's text over the curve where it is, kept off its neighbors
    bold = gfx.font(cell_h * 0.62, bold=True)
    taken = []
    for i, text, rgb in tops:
        if not text or i >= n or values[i] is None:
            continue
        x = X(i)
        y = Y(curve(x)) - max(2.0, cell_h * 0.18)
        try:
            bx0, by0, bx1, by1 = d.textbbox((x, y), text, font=bold, anchor="md")
        except (AttributeError, TypeError, ValueError):
            tw = gfx.text_width(d, text, bold)
            bx0, by0, bx1, by1 = x - tw / 2, y - cell_h * 0.7, x + tw / 2, y
        dx = max(x0 - bx0, 0) + min(w - 1 - bx1, 0)
        dy = max(-by0, 0)
        box = (bx0 + dx - 2, by0 + dy, bx1 + dx + 2, by1 + dy)
        if any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in taken):
            continue
        taken.append(box)
        gfx.draw_text(d, (x + dx, y + dy), text, bold, tuple(rgb), anchor="md", stroke=1, stroke_fill=bg)
    if band and bars:
        img = _band_bars(img, bars, n, x0, pw, base, cell_h, bg)
    return img


def _line_over(img, values, scale, color, n, x0, pw, top, base, cell_h, bg):
    """A second series over an area chart (area_image's `line`): a smooth curve through its
    values (each at the middle of its share of the width, from the first to the last) on
    its own scale, lo at the area's floor and hi at the top of the plot, colored along its
    length by color(value), round at its ends, and edged with a darker shade of the card
    (a lighter one on a light card) so it reads over the area's shade as well as off it."""
    w, h = img.size
    known = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(known) < 2 or base - top < 2:
        return img
    lo, hi = scale
    span = (hi - lo) or 1.0

    def X(p):
        return x0 + (p + 0.5) * pw / n

    def Y(v):
        return top + (1.0 - max(0.0, min(1.0, (v - lo) / span))) * (base - top)

    curve = smooth_curve([X(i) for i, _v in known], [max(lo, min(hi, v)) for _i, v in known])
    xa, xb = X(known[0][0]), X(known[-1][0])
    S = 4                                       # drawn 4x over and shrunk: smooth edges
    steps = max(2, int((xb - xa) * 2) + 1)
    xs = [xa + (xb - xa) * k / (steps - 1) for k in range(steps)]
    pts = [(x * S, Y(curve(x)) * S) for x in xs]
    lw = max(2.0, min(5.0, cell_h * 0.19))      # heavier than the area's own line
    rim = max(1.0, lw * 0.35)

    def mask(width):
        m = gfx.Image.new("L", (w * S, h * S), 0)
        md = gfx.ImageDraw.Draw(m)
        md.line(pts, fill=255, width=max(1, int(round(width * S))), joint="curve")
        r = width * S / 2
        for px, py in (pts[0], pts[-1]):         # round ends
            md.ellipse([px - r, py - r, px + r, py + r], fill=255)
        return m.resize((w, h), gfx.BOX)

    light = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2] > 140
    edge = blend(bg, (255, 255, 255), 0.7) if light else blend(bg, (0, 0, 0), 0.55)
    img = gfx.Image.composite(gfx.Image.new("RGB", (w, h), edge), img, mask(lw + 2 * rim))
    # the colors, column by column, where the line is
    strip = gfx.Image.new("RGB", (w, 1))
    strip.putdata([tuple(color(curve(min(xb, max(xa, x + 0.5))))[:3]) for x in range(w)])
    return gfx.Image.composite(strip.resize((w, h), gfx.NEAREST), img, mask(lw))


def _band_bars(img, bars, n, x0, pw, base, cell_h, bg):
    """The bars in the band under an area chart (area_image's `bars`): rounded at the top,
    each in its color with a darker rim, its text over it in a smaller type."""
    w, h = img.size
    lab = cell_h * 0.62                         # a line over the tallest bar for its text
    room = max(2.0, h - base - lab)
    step = pw / max(1, n)
    bw = max(2.0, min(step * 0.6, step - 2.0))
    rim_w = max(1.0, cell_h / 16)
    radius = min(bw * 0.3, cell_h * 0.3)
    light = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2] > 140
    # drawn 4x over (from the band's top down) and shrunk: smooth corners
    S = 4
    y0 = max(0, int(base) - 1)
    strip = img.crop((0, y0, w, h)).resize((w * S, (h - y0) * S), gfx.NEAREST)
    d = gfx.ImageDraw.Draw(strip)
    tags = []
    for i, frac, rgb, text in bars:
        if i is None or i >= n or not frac or frac <= 0:
            continue
        rgb = tuple(rgb[:3])
        cx = x0 + (i + 0.5) * step
        hh = max(rim_w * 3, min(1.0, frac) * room)
        xa, xb, ya = cx - bw / 2, cx + bw / 2, h - hh
        rim = blend(rgb, (0, 0, 0), 0.45)
        box = [xa * S, (ya - y0) * S, xb * S, (h - y0 + radius) * S]     # rounded at the top only
        try:
            d.rounded_rectangle(box, radius=radius * S, fill=rgb, outline=rim, width=max(1, int(round(rim_w * S))))
        except AttributeError:                  # Pillow before 8.2
            d.rectangle(box, fill=rgb, outline=rim, width=max(1, int(round(rim_w * S))))
        if text:
            tags.append((cx, ya, text, rim if light else rgb))
    img.paste(strip.resize((w, h - y0), gfx.BOX), (0, y0))
    if tags:
        d = gfx.ImageDraw.Draw(img)
        small = gfx.font(cell_h * 0.5, bold=True)
        taken = []
        for cx, ya, text, ink in tags:
            tw = gfx.text_width(d, text, small)
            x = max(x0 + tw / 2 + 1, min(w - tw / 2 - 1, cx))
            box = (x - tw / 2 - 2, ya - cell_h * 0.6, x + tw / 2 + 2, ya)
            if any(box[0] < b[2] and b[0] < box[2] for b in taken):
                continue                        # kept off its neighbor's
            taken.append(box)
            gfx.draw_text(d, (x, ya - max(1.0, cell_h * 0.08)), text, small, ink, anchor="md", stroke=1,
                          stroke_fill=bg)
    return img


def range_image(lo, hi, gmin, gmax, w, h, bg, theme=None, now=None):
    """A day's low-to-high as a pill along the days' shared scale (gmin..gmax, in Celsius),
    shaded through the colors of the temperatures it spans, over a faint track; `now`, the
    temperature now, a dot on it."""
    ink = Ink(theme, bg)
    bg = ink.bg
    S = 4
    W, H = w * S, h * S
    img = gfx.Image.new("RGB", (W, H), bg)
    if lo is None or hi is None or gmin is None or gmax is None:
        return img.resize((w, h), gfx.LANCZOS)
    span = (gmax - gmin) or 1.0
    pad = H * 0.32
    x_of = lambda t: pad + (t - gmin) / span * (W - 2 * pad)                 # noqa: E731
    d = gfx.ImageDraw.Draw(img, "RGBA")
    track = H * 0.16
    cy = H / 2
    d.rounded_rectangle([pad - track, cy - track / 2, W - pad + track, cy + track / 2], radius=track / 2,
                        fill=blend(bg, ink.text, 0.14 if not ink.light else 0.18) + (255,))
    half = H * 0.2
    x0, x1 = x_of(lo) - half, x_of(max(lo, hi)) + half
    pill = gfx.Image.new("L", (W, H), 0)
    gfx.ImageDraw.Draw(pill).rounded_rectangle([x0, cy - half, x1, cy + half], radius=half, fill=255)
    colors = [ink.temp(gmin + max(0.0, min(1.0, (x - pad) / max(1.0, W - 2 * pad))) * span) for x in range(W)]
    img.paste(_strip(W, H, colors), (0, 0), pill)
    if now is not None and lo - 3 <= now <= hi + 3:
        x = x_of(max(gmin, min(gmax, now)))
        r = H * 0.2
        d.ellipse([x - r, cy - r, x + r, cy + r], fill=(255, 255, 255, 255), outline=bg + (255,), width=S)
    return img.resize((w, h), gfx.LANCZOS)


def daylight_image(points, today_doy, lo, hi, w, h, bg, theme=None):
    """Day length through the year as a picture: points [(day of the year, seconds)], the
    area under the curve golden, shading toward amber at its foot, a crisp line along its
    top; the months marked faintly, 12 hours dashed across, the longest and shortest days
    dotted, today a line up to a glowing dot on the curve. lo and hi (seconds) are the
    scale's foot and top."""
    ink = Ink(theme, bg)
    bg = ink.bg
    S = 3
    W, H = w * S, h * S
    img = gfx.Image.new("RGB", (W, H), bg)
    pts = [(doy, x) for doy, x in points if x is not None]
    if len(pts) < 2 or hi <= lo:
        return img.resize((w, h), gfx.LANCZOS)
    d = gfx.ImageDraw.Draw(img, "RGBA")
    top_pad = 4 * S

    def X(doy):
        return (doy - 1) / 366 * W

    def Y(sec):
        return top_pad + (1 - max(0.0, min(1.0, (sec - lo) / (hi - lo)))) * (H - top_pad)

    # the months, faintly
    for mth in range(1, 12):
        x = X(sum((31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[:mth]) + 1)
        d.line([x, 0, x, H], fill=ink.grid + (70,), width=max(1, S // 2))
    f = smooth_curve([X(doy) for doy, _x in pts], [x for _doy, x in pts])
    xs = [k * S / 2 for k in range(int(2 * W / S) + 1)]
    curve = [(x, Y(f(x))) for x in xs]
    area = gfx.Image.new("L", (W, H), 0)
    gfx.ImageDraw.Draw(area).polygon([(0, H)] + curve + [(W, H)], fill=255)
    gold, amber = (255, 214, 84), (255, 146, 36)
    shade = gfx.Image.new("L", (1, H))
    shade.putdata([int(255 * (0.82 - 0.5 * y / max(1, H - 1))) for y in range(H)])
    img.paste(gfx.vgrad(W, H, gold, amber, top_pad, H).convert("RGB"), (0, 0),
              gfx.ImageChops.multiply(area, shade.resize((W, H))))
    # 12 hours, dashed, where it falls on the scale
    if lo < 12 * 3600 < hi:
        y = round(Y(12 * 3600) / S) * S
        for x in range(0, W, 9 * S):
            d.line([x, y, x + 4 * S, y], fill=ink.text + (120,), width=S)
    line = _line_mask((W, H), curve, 1.8 * S)
    img.paste(gfx.Image.new("RGB", (W, H), (255, 236, 150)), (0, 0), line)
    # the longest and shortest days
    for doy, x in (max(pts, key=lambda p: p[1]), min(pts, key=lambda p: p[1])):
        cx, cy = X(doy), Y(x)
        r = 2.4 * S
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 250, 220, 255), outline=bg + (255,), width=S)
    # today: a line up from the foot to a glowing dot on the curve
    if today_doy:
        x = X(today_doy)
        y = Y(f(x))
        acc = blend(ink.day, (255, 120, 40), 0.5)
        d.line([x, y, x, H], fill=acc + (220,), width=S)
        glow = gfx.Image.new("L", (W, H), 0)
        r = 3.4 * S
        gfx.ImageDraw.Draw(glow).ellipse([x - r * 1.6, y - r * 1.6, x + r * 1.6, y + r * 1.6], fill=255)
        img.paste(gfx.Image.new("RGB", (W, H), acc), (0, 0), glow.filter(gfx.ImageFilter.GaussianBlur(r)).point(
            lambda v: int(v * 0.7)))
        d.ellipse([x - r, y - r, x + r, y + r], fill=acc + (255,), outline=(255, 255, 255, 255), width=S)
    return img.resize((w, h), gfx.LANCZOS)
