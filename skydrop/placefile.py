"""GRLevelX placefiles: the overlay format Supercell Wx, GRLevel3 and RadarScope read
(spotter positions, lightning, storm attributes, station plots, range rings and more).

Statements: Title, Refresh / RefreshSeconds, Threshold, Color, HSLuv, Font, IconFile, Icon,
Text, Place, Line, Polygon, Triangles, Image, Object ... End: (pixel offsets from an anchor),
TimeRange, and ; comments.

Thresholds follow Supercell Wx: 0 or 999+ always shows; a positive threshold shows while the
map distance (center to the nearest edge, nautical miles) is at or below it; a negative one
only when zoomed out past it. Items with a TimeRange show only in loop frames inside it.
"""

import calendar
import colorsys
import math
import os
import re
import threading
import time
import urllib.parse
from collections import OrderedDict

from . import gfx

MAX_ITEMS = 30000
MAX_LOCAL_BYTES = 16 * 1024 * 1024
NM_PER_MI = 1 / 1.150779
_STMT = re.compile(r"([A-Za-z]+)\s*:\s?(.*)$")
_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


# ----------------------------------------------------------------------------
# reading
# ----------------------------------------------------------------------------

def urls(text):
    """The placefile setting -> list of addresses (one per line, or separated by commas,
    semicolons or spaces). Local files work too: /path, ~/path or file:///path."""
    out = []
    for part in re.split(r"[\s;]+|,(?=\s*(?:https?://|file://|/|~))", str(text or "")):
        part = part.strip().strip(",")
        if part and (re.match(r"(?i)https?://\S+$", part) or part.startswith(("/", "~", "file://"))):
            if part not in out:
                out.append(part)
    return out[:20]


def is_local(url):
    return url.startswith(("/", "~", "file://"))


def local_path(url):
    if url.startswith("file://"):
        url = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
    return os.path.expanduser(url)


def read_local(url):
    path = local_path(url)
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_LOCAL_BYTES + 1)
    except OSError as e:
        raise PlacefileError(f"can't read {path}: {e.strerror or e}")
    if len(raw) > MAX_LOCAL_BYTES:
        raise PlacefileError(f"{path} is larger than {MAX_LOCAL_BYTES // (1024 * 1024)} MB")
    return raw.decode("utf-8", "replace")


def resolve(base, ref):
    """An icon or image file named in a placefile, relative to the placefile itself."""
    ref = (ref or "").strip()
    if not ref:
        return ""
    if re.match(r"(?i)https?://", ref) or ref.startswith(("file://", "/", "~")):
        return ref
    if is_local(base):
        return os.path.join(os.path.dirname(local_path(base)), ref)
    return urllib.parse.urljoin(base, ref)


class PlacefileError(ValueError):
    pass


def _fields(text):
    """Arguments split on commas, keeping "quoted strings" whole. Neighbouring quoted strings
    join ("a" "b" -> "ab") and \\n inside them is a line break."""
    out, cur = [], []
    quoted = in_q = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if in_q:
            if c == '"':
                in_q = False
            elif c == "\\" and i + 1 < n and text[i + 1] in "nN":
                cur.append("\n")
                i += 1
            else:
                cur.append(c)
        elif c == '"':
            if not quoted and not "".join(cur).strip():
                cur = []
            in_q = quoted = True
        elif c == ",":
            out.append("".join(cur) if quoted else "".join(cur).strip())
            cur, quoted = [], False
        elif not (quoted and c in " \t"):
            cur.append(c)
        i += 1
    if cur or quoted or out:
        out.append("".join(cur) if quoted else "".join(cur).strip())
    return out


def _f(v, default=None):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


def _color(rest, current):
    nums = [float(x) for x in _NUM.findall(rest)]
    if len(nums) < 3:
        return current
    r, g, b = (max(0, min(255, int(x))) for x in nums[:3])
    a = max(0, min(255, int(nums[3]))) if len(nums) > 3 else 255
    return (r, g, b, a)


def _hsluv(rest, current):
    """HSLuv: hue 0-360, saturation and lightness 0-100 (approximated through HLS)."""
    nums = [float(x) for x in _NUM.findall(rest)]
    if len(nums) < 3:
        return current
    r, g, b = colorsys.hls_to_rgb((nums[0] % 360) / 360.0, max(0, min(100, nums[2])) / 100.0,
                                  max(0, min(100, nums[1])) / 100.0)
    a = max(0, min(255, int(nums[3]))) if len(nums) > 3 else 255
    return (int(r * 255), int(g * 255), int(b * 255), a)


def _when(stamp):
    stamp = stamp.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return calendar.timegm(time.strptime(stamp, fmt))
        except ValueError:
            continue
    return None


def _latlon(lat, lon):
    lat, lon = _f(lat), _f(lon)
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def parse(text, url=""):
    """Placefile text -> {url, title, refresh (s), items, icon_files, fonts, bad, timed}."""
    pf = {"url": url, "title": "", "refresh": 0, "items": [], "icon_files": {}, "fonts": {}, "bad": 0,
          "timed": False}
    items = pf["items"]
    st = {"color": (255, 255, 255, 255), "thr": 999.0, "time": None}
    saved = None      # drawing state when the open Object began
    anchor = None     # (lat, lon) of the open Object
    block = None      # open Line / Polygon / Triangles / Image

    def add(item):
        if len(items) < MAX_ITEMS:
            item.setdefault("thr", st["thr"])
            item.setdefault("time", st["time"])
            item["at"] = anchor
            items.append(item)
            if item["time"]:
                pf["timed"] = True

    def finish(b):
        pts = b["pts"]
        if b["k"] == "line" and len(pts) >= 2:
            add(b)
        elif b["k"] == "poly":
            for ring, rgba in b["rings"]:
                if len(ring) >= 3:
                    add({"k": "poly", "pts": ring, "color": rgba})
        elif b["k"] in ("tri", "image"):
            for i in range(0, len(pts) - 2, 3):
                tri = pts[i:i + 3]
                if b["k"] == "tri":
                    add({"k": "tri", "pts": [p[:2] for p in tri], "color": tri[0][2] or b["color"]})
                else:
                    add({"k": "image", "pts": [p[:2] for p in tri], "uv": [p[2] for p in tri], "src": b["src"]})

    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        m = _STMT.match(line)
        key = m.group(1).lower() if m else ""
        if block is not None:
            if key == "end":
                finish(block)
                block = None
                continue
            if key == "color":
                block["color"] = st["color"] = _color(m.group(2), st["color"])
                continue
            if m and key:
                pf["bad"] += 1
                continue
            v = _fields(line)
            if len(v) < 2:
                pf["bad"] += 1
                continue
            if anchor is not None:
                xy = (_f(v[0]), _f(v[1]))
                if None in xy:
                    pf["bad"] += 1
                    continue
                p = xy
            else:
                p = _latlon(v[0], v[1])
                if p is None:
                    pf["bad"] += 1
                    continue
            if block["k"] == "line":
                block["pts"].append(p)
            elif block["k"] == "poly":
                rgba = _color(" ".join(v[2:6]), None) if len(v) >= 5 else None
                if rgba is not None or not block["rings"]:
                    block["rings"].append(([], rgba or block["color"]))
                block["rings"][-1][0].append(p)
            elif block["k"] == "tri":
                rgba = _color(" ".join(v[2:6]), None) if len(v) >= 5 else None
                block["pts"].append((p[0], p[1], rgba))
            else:  # image vertex: lat, lon, u, v
                u, w = (_f(v[2], 0.0), _f(v[3], 0.0)) if len(v) >= 4 else (0.0, 0.0)
                block["pts"].append((p[0], p[1], (u, w)))
            continue
        if not m:
            pf["bad"] += 1
            continue
        rest = m.group(2)
        try:
            if key == "title":
                pf["title"] = rest.strip().strip('"')[:80]
            elif key == "refresh":
                pf["refresh"] = int(max(0.25, _f(rest, 1.0)) * 60)
            elif key == "refreshseconds":
                pf["refresh"] = int(max(5, _f(rest, 60.0)))
            elif key == "threshold":
                st["thr"] = _f(rest, 999.0)
            elif key == "color":
                st["color"] = _color(rest, st["color"])
            elif key == "hsluv":
                st["color"] = _hsluv(rest, st["color"])
            elif key == "timerange":
                parts = rest.split()
                t0, t1 = (_when(parts[0]), _when(parts[1])) if len(parts) >= 2 else (None, None)
                st["time"] = (t0, t1) if t0 and t1 and t1 > t0 else None
            elif key == "font":
                v = _fields(rest)
                pf["fonts"][int(_f(v[0], 1))] = (max(6, min(48, int(_f(v[1], 11)))), int(_f(v[2], 0)) & 1)
            elif key == "iconfile":
                v = _fields(rest)
                pf["icon_files"][int(_f(v[0], 1))] = {
                    "w": max(1, int(_f(v[1], 16))), "h": max(1, int(_f(v[2], 16))),
                    "hx": _f(v[3], 0.0), "hy": _f(v[4], 0.0), "src": resolve(url, ",".join(v[5:]))}
            elif key == "icon":
                v = _fields(rest)
                if anchor is not None:
                    pos = (_f(v[0], 0.0), _f(v[1], 0.0))
                else:
                    pos = _latlon(v[0], v[1])
                if pos is None or len(v) < 5:
                    raise ValueError
                add({"k": "icon", "p": pos, "angle": _f(v[2], 0.0), "file": int(_f(v[3], 1)),
                     "num": int(_f(v[4], 1)), "hover": ",".join(v[5:]).strip()})
            elif key == "text":
                v = _fields(rest)
                pos = (_f(v[0], 0.0), _f(v[1], 0.0)) if anchor is not None else _latlon(v[0], v[1])
                if pos is None or len(v) < 4:
                    raise ValueError
                add({"k": "text", "p": pos, "font": int(_f(v[2], 1)), "text": v[3],
                     "hover": ",".join(v[4:]).strip(), "color": st["color"]})
            elif key == "place":
                v = _fields(rest)
                pos = _latlon(v[0], v[1]) if anchor is None else (_f(v[0], 0.0), _f(v[1], 0.0))
                if pos is None or len(v) < 3:
                    raise ValueError
                add({"k": "place", "p": pos, "text": ",".join(v[2:]).strip(), "color": st["color"]})
            elif key == "object":
                v = _fields(rest)
                pos = _latlon(v[0], v[1])
                if pos is None:
                    raise ValueError
                saved = dict(st)
                anchor = pos
            elif key == "end":
                if anchor is not None:
                    anchor = None
                    st = saved or st
                    saved = None
            elif key == "line":
                v = _fields(rest)
                block = {"k": "line", "pts": [], "width": max(1.0, min(12.0, _f(v[0], 1.0) if v else 1.0)),
                         "hover": ",".join(v[2:]).strip() if len(v) > 2 else "", "color": st["color"]}
            elif key == "polygon":
                block = {"k": "poly", "pts": [], "rings": [], "color": st["color"]}
            elif key == "triangles":
                block = {"k": "tri", "pts": [], "color": st["color"]}
            elif key in ("image", "imagexy"):
                v = _fields(rest)
                block = {"k": "image", "pts": [], "src": resolve(url, v[0] if v else "")}
            else:
                pf["bad"] += 1
        except (ValueError, IndexError, TypeError):
            pf["bad"] += 1
    if block is not None:
        finish(block)
    if not pf["title"]:
        tail = local_path(url) if is_local(url) else urllib.parse.urlsplit(url).netloc + \
            urllib.parse.urlsplit(url).path
        pf["title"] = os.path.basename(tail.rstrip("/")) or tail or "placefile"
    return pf


# ----------------------------------------------------------------------------
# drawing
# ----------------------------------------------------------------------------

_SHEETS = OrderedDict()   # source -> RGBA image (or None when it couldn't be loaded)
_SHEET_LOCK = threading.Lock()


def _sheet(src, http):
    """An icon sheet or image (black is transparent in sheets without an alpha channel)."""
    with _SHEET_LOCK:
        if src in _SHEETS:
            _SHEETS.move_to_end(src)
            return _SHEETS[src]
    img = None
    try:
        if is_local(src):
            with open(local_path(src), "rb") as fh:
                raw = fh.read(8 * 1024 * 1024)
        elif http is not None and src:
            raw = http.get_bytes(src, ttl=6 * 3600, disk_ttl=7 * 86400, min_age=600)
        else:
            raw = None
        if raw:
            import io
            img = gfx.Image.open(io.BytesIO(raw))
            img.load()
            if img.mode not in ("RGBA", "LA") and not (img.mode == "P" and "transparency" in img.info):
                rgb = img.convert("RGB")
                alpha = rgb.convert("L").point(lambda v: 0 if v < 12 else 255)
                img = rgb.convert("RGBA")
                img.putalpha(alpha)
            else:
                img = img.convert("RGBA")
    except Exception:
        img = None
    with _SHEET_LOCK:
        _SHEETS[src] = img
        while len(_SHEETS) > 64:
            _SHEETS.popitem(last=False)
    return img


def _icon(pf, item, http):
    spec = pf["icon_files"].get(item["file"])
    if not spec:
        return None, None
    sheet = _sheet(spec["src"], http)
    if sheet is None:
        return None, spec
    cols = max(1, sheet.size[0] // spec["w"])
    idx = max(0, item["num"] - 1)
    x0, y0 = (idx % cols) * spec["w"], (idx // cols) * spec["h"]
    if y0 + spec["h"] > sheet.size[1]:
        return None, spec
    return sheet.crop((x0, y0, x0 + spec["w"], y0 + spec["h"])), spec


def map_distance_nm(view):
    """Center of the map to its nearest edge, in nautical miles (what thresholds compare to)."""
    w_mi = view.w * view.mi_per_px
    h_mi = view.h * view.mi_per_px * view.yscale
    return min(w_mi, h_mi) / 2 * NM_PER_MI


def shown(thr, dist):
    if thr is None or thr == 0 or thr >= 999:
        return True
    return thr >= dist if thr > 0 else -thr <= dist


def _rotated(icon, hx, hy, angle):
    """The icon turned clockwise about its hot spot, on a square canvas centered there."""
    w, h = icon.size
    r = int(math.ceil(max(math.hypot(hx - cx, hy - cy) for cx, cy in ((0, 0), (w, 0), (0, h), (w, h))))) + 1
    canvas = gfx.Image.new("RGBA", (2 * r, 2 * r), (0, 0, 0, 0))
    canvas.paste(icon, (int(round(r - hx)), int(round(r - hy))))
    if angle % 360:
        canvas = canvas.rotate(-angle, resample=gfx.BICUBIC, center=(r, r))
    return canvas, r


def _avg_color(icon):
    raw = icon.convert("RGBA").tobytes()          # (getdata is going away in newer Pillow)
    px = [raw[i:i + 4] for i in range(0, len(raw), 4) if raw[i + 3] > 100]
    if not px:
        return (255, 255, 255, 255)
    n = len(px)
    return (sum(p[0] for p in px) // n, sum(p[1] for p in px) // n, sum(p[2] for p in px) // n, 255)


def draw(img, d, view, pf, res, pix, http=None, at_time=None):
    """Draw one placefile. With at_time, only its time-ranged items for that moment."""
    dist = map_distance_nm(view)
    scale = 1.0 if pix else 0.18          # object pixel offsets shrink on the small block map
    hover = getattr(res, "hover", None)
    fonts = {}

    def pos(item, p):
        a = item["at"]
        if a is None:
            return view.to_px(p[0], p[1])
        ax, ay = view.to_px(a[0], a[1])
        return ax + p[0] * scale, ay - p[1] * scale     # object offsets: y counts upward

    def font(num):
        size, bold = pf["fonts"].get(num, (11, 1))
        if num not in fonts:
            fonts[num] = gfx.font(max(8, int(size * (1.15 if view.w > 900 else 1.0))), bold=True)
        return fonts[num]

    margin = 40
    for item in pf["items"]:
        t = item["time"]
        if at_time is None:
            if t:
                continue
        elif not t or not (t[0] <= at_time < t[1]):
            continue
        if not shown(item["thr"], dist):
            continue
        k = item["k"]
        if k in ("icon", "text", "place"):
            x, y = pos(item, item["p"])
            if not (-margin <= x < view.w + margin and -margin <= y < view.h + margin):
                continue
            if k == "icon":
                icon, spec = _icon(pf, item, http)
                if icon is None:
                    if pix:
                        d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(255, 255, 255, 230), outline=(0, 0, 0, 200))
                    else:
                        d.point((x, y), fill=(255, 255, 255, 255))
                    rad = 6 if pix else 1.5
                elif pix:
                    canvas, r = _rotated(icon, spec["hx"], spec["hy"], item["angle"])
                    _paste_clipped(img, canvas, int(round(x - r)), int(round(y - r)))
                    rad = max(6, min(spec["w"], spec["h"]) / 2)
                else:
                    d.point((x, y), fill=_avg_color(icon))
                    rad = 1.5
                if hover is not None and item["hover"] and at_time is None:
                    hover.append(("pt", x, y, rad, item["hover"]))
            elif k == "text":
                if not pix:
                    if hover is not None and item["hover"] and at_time is None:
                        hover.append(("pt", x, y, 1.5, item["hover"]))
                    continue
                fnt = font(item["font"])
                lines = item["text"].split("\n")
                lh = (getattr(fnt, "size", 12) or 12) + 2
                y0 = y - lh * (len(lines) - 1) / 2
                for i, ln in enumerate(lines):
                    gfx.draw_text(d, (x, y0 + i * lh), ln, fnt, item["color"][:3] + (255,), anchor="mm",
                                  stroke=2, stroke_fill=(0, 0, 0, 200))
                if hover is not None and item["hover"] and at_time is None:
                    hover.append(("pt", x, y, max(8, lh / 2 + 2), item["hover"]))
            else:  # place: a dot with its name
                rgba = item["color"][:3] + (255,)
                if pix:
                    d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=rgba, outline=(0, 0, 0, 220))
                    gfx.draw_text(d, (x + 6, y), item["text"], font(1), rgba, anchor="lm", stroke=2,
                                  stroke_fill=(0, 0, 0, 200))
                else:
                    d.point((x, y), fill=rgba)
                if hover is not None and at_time is None:
                    hover.append(("pt", x, y, 6 if pix else 1.5, item["text"]))
        elif k == "line":
            pts = [pos(item, p) for p in item["pts"]]
            if not _on_map(pts, view, margin):
                continue
            if pix:
                w = max(1, int(round(item["width"])))
                d.line(pts, fill=(0, 0, 0, 150), width=w + 2, joint="curve")
                d.line(pts, fill=item["color"], width=w, joint="curve")
            else:
                d.line(pts, fill=item["color"], width=1)
            if hover is not None and item["hover"] and at_time is None:
                hover.append(("line", pts, 5 if pix else 1.5, item["hover"]))
        elif k in ("poly", "tri"):
            pts = [pos(item, p) for p in item["pts"]]
            if len(pts) >= 3 and _on_map(pts, view, margin):
                d.polygon(pts, fill=item["color"])
        elif k == "image":
            pts = [pos(item, p) for p in item["pts"]]
            if len(pts) == 3 and _on_map(pts, view, margin):
                tex = _sheet(item["src"], http)
                if tex is not None:
                    _textured_triangle(img, tex, pts, item["uv"])


def _on_map(pts, view, margin):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return max(xs) >= -margin and min(xs) < view.w + margin and max(ys) >= -margin and min(ys) < view.h + margin


def _paste_clipped(img, tile, x, y):
    """alpha_composite that tolerates a tile hanging off the edge of the image."""
    W, H = img.size
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + tile.size[0]), min(H, y + tile.size[1])
    if x1 <= x0 or y1 <= y0:
        return
    img.alpha_composite(tile.crop((x0 - x, y0 - y, x1 - x, y1 - y)), (x0, y0))


def _textured_triangle(img, tex, pts, uv):
    """Map one triangle of a placefile Image onto the map (affine)."""
    (x0, y0), (x1, y1), (x2, y2) = pts
    tw, th = tex.size
    (u0, v0), (u1, v1), (u2, v2) = [(u * tw, v * th) for u, v in uv]
    det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if abs(det) < 1e-6:
        return
    # output (x, y) -> texture (u, v):  u = a*x + b*y + c,  v = d*x + e*y + f
    a = ((u1 - u0) * (y2 - y0) - (u2 - u0) * (y1 - y0)) / det
    b = ((u2 - u0) * (x1 - x0) - (u1 - u0) * (x2 - x0)) / det
    c = u0 - a * x0 - b * y0
    dd = ((v1 - v0) * (y2 - y0) - (v2 - v0) * (y1 - y0)) / det
    e = ((v2 - v0) * (x1 - x0) - (v1 - v0) * (x2 - x0)) / det
    f = v0 - dd * x0 - e * y0
    W, H = img.size
    bx0, by0 = max(0, int(min(x0, x1, x2))), max(0, int(min(y0, y1, y2)))
    bx1, by1 = min(W, int(math.ceil(max(x0, x1, x2))) + 1), min(H, int(math.ceil(max(y0, y1, y2))) + 1)
    if bx1 <= bx0 or by1 <= by0 or (bx1 - bx0) * (by1 - by0) > 6_000_000:
        return
    patch = tex.transform((bx1 - bx0, by1 - by0), gfx.AFFINE,
                          (a, b, c + a * bx0 + b * by0, dd, e, f + dd * bx0 + e * by0),
                          resample=gfx.BILINEAR)
    mask = gfx.Image.new("L", patch.size, 0)
    gfx.ImageDraw.Draw(mask).polygon([(x - bx0, y - by0) for x, y in pts], fill=255)
    alpha = gfx.ImageChops.multiply(patch.getchannel("A"), mask)
    patch.putalpha(alpha)
    img.alpha_composite(patch, (bx0, by0))


def draw_all(img, d, view, snap, res, pix, http=None):
    """Every loaded placefile (the parts without a time range)."""
    for pf in snap.get("placefiles") or []:
        try:
            draw(img, d, view, pf, res, pix, http)
        except Exception as e:  # one bad file must never break the radar
            res.notes.append(f"placefile {pf.get('title') or pf.get('url')}: {type(e).__name__}: {e}")


def any_timed(snap):
    return any(pf.get("timed") for pf in snap.get("placefiles") or [])


def timed_layer(view, snap, res, pix, http, t):
    """A transparent layer with the time-ranged placefile items for the frame at time t."""
    img = gfx.Image.new("RGBA", (view.w, view.h), (0, 0, 0, 0))
    d = gfx.ImageDraw.Draw(img)
    for pf in snap.get("placefiles") or []:
        if pf.get("timed"):
            try:
                draw(img, d, view, pf, res, pix, http, at_time=t)
            except Exception as e:
                res.notes.append(f"placefile {pf.get('title')}: {type(e).__name__}: {e}")
    return img


# ----------------------------------------------------------------------------
# hover
# ----------------------------------------------------------------------------

def _seg_dist(px, py, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / L))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def hover_at(res, px, py, tol=0.0):
    """The hover text of the placefile item nearest a map point (in map pixels), if any.
    tol widens every target (a terminal cell is much coarser than a pixel)."""
    best = None
    for entry in getattr(res, "hover", None) or []:
        if entry[0] == "pt":
            _k, x, y, r, text = entry
            dd = math.hypot(px - x, py - y)
        else:
            _k, pts, r, text = entry
            dd = min(_seg_dist(px, py, *pts[i], *pts[i + 1]) for i in range(len(pts) - 1))
        if dd <= r + tol and (best is None or dd < best[0]):
            best = (dd, text)
    return best[1] if best else None
