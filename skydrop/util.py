"""Small helpers shared by every module."""

import datetime as dt
import math
import re
import time
import unicodedata


def log(msg):
    """Append a line to the log file (best effort, never raises)."""
    try:
        import os
        from .paths import LOG_PATH, ensure
        ensure(os.path.dirname(LOG_PATH))
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def dig(obj, *path, default=None):
    """Walk nested dicts/lists without raising; None anywhere -> default."""
    cur = obj
    for key in path:
        try:
            cur = cur[key]
        except (KeyError, IndexError, TypeError):
            return default
        if cur is None:
            return default
    return cur


def num(val):
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def first(*vals):
    for val in vals:
        if val is not None:
            return val
    return None


def sum_opt(*vals):
    xs = [v for v in vals if v is not None]
    return sum(xs) if xs else None


def cap(text):
    text = (text or "").strip()
    return text[:1].upper() + text[1:]


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def iso_epoch(text):
    """ISO-8601 timestamp -> int epoch seconds (None if unparseable)."""
    if not text or not isinstance(text, str):
        return None
    s = text.strip()
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        mt = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?)(?:\.\d+)?(Z|[+-]\d\d:?\d\d)?$", s)
        if not mt:
            return None
        zone = mt.group(2) or "+00:00"
        if zone == "Z":
            zone = "+00:00"
        elif ":" not in zone:
            zone = zone[:3] + ":" + zone[3:]
        try:
            d = dt.datetime.fromisoformat(mt.group(1) + zone)
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp())


def local_iso_epoch(text, tz):
    """'2026-10-27T15:00' (no zone, local to tz) -> epoch."""
    if not text:
        return None
    try:
        d = dt.datetime.fromisoformat(str(text))
    except ValueError:
        return None
    if d.tzinfo is None:
        if tz is not None:
            d = d.replace(tzinfo=tz)
        else:
            return int(time.mktime(d.timetuple()))
    return int(d.timestamp())


_DUR_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def _duration(text):
    mt = _DUR_RE.match(text or "")
    if not mt:
        return None
    d, h, mi, se = (int(x or 0) for x in mt.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def parse_valid_time(text):
    """NWS ISO-8601 interval -> (start hour epoch, seconds).

    Handles 'start/PT3H', 'start/end', 'P1D/end', and NOW in either slot."""
    left, _, right = text.partition("/")
    now = int(time.time())
    t0 = now if left == "NOW" else iso_epoch(left)
    if t0 is not None:
        secs = _duration(right)
        if secs is None:
            end = now if right == "NOW" else iso_epoch(right)
            secs = (end - t0) if end is not None else 3600
    else:
        secs = _duration(left)
        end = now if right == "NOW" else iso_epoch(right)
        if secs is None or end is None:
            raise ValueError(text)
        t0 = end - secs
    return t0 - t0 % 3600, max(int(secs), 3600)


# ----------------------------------------------------------------------------
# geography
# ----------------------------------------------------------------------------

EARTH_MI = 3958.8
EARTH_KM = 6371.0


def distance_mi(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_MI * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def offset_point(lat, lon, bearing, miles):
    """Point `miles` away from (lat, lon) along `bearing` degrees."""
    d = miles / EARTH_MI
    b = math.radians(bearing)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def point_in_ring(lon, lat, ring):
    """Ray casting; ring is a list of (lon, lat)."""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def point_in_rings(lon, lat, rings):
    """Even-odd test across all rings (handles holes and multipolygons)."""
    inside = False
    for ring in rings or []:
        if point_in_ring(lon, lat, ring):
            inside = not inside
    return inside


def geojson_rings(geom):
    """GeoJSON Polygon/MultiPolygon -> flat list of rings [(lon, lat), ...]."""
    if not isinstance(geom, dict):
        return []
    kind, coords = geom.get("type"), geom.get("coordinates") or []
    out = []
    try:
        if kind == "Polygon":
            out = [[(p[0], p[1]) for p in ring] for ring in coords]
        elif kind == "MultiPolygon":
            out = [[(p[0], p[1]) for p in ring] for poly in coords for ring in poly]
    except (TypeError, IndexError):
        return []
    return out


# ----------------------------------------------------------------------------
# text safety: keep the terminal grid aligned
# ----------------------------------------------------------------------------

_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


# Wide glyphs drawn on purpose: each takes two cells (the glyph, then an empty
# continuation cell that the renderer skips). Everything else wide becomes "?".
WIDE_OK = frozenset("💧")


def safe_text(text):
    """Strip control characters and replace wide/combining characters so every
    character occupies exactly one terminal cell."""
    if not text:
        return ""
    s = _CTRL_RE.sub("", str(text).replace("\t", " ").replace("\r", ""))
    if s.isascii():
        return s
    out = []
    for ch in s:
        if ord(ch) < 0x300:
            out.append(ch)
            continue
        if unicodedata.combining(ch):
            continue
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            out.append(ch if ch in WIDE_OK else "?")
            continue
        out.append(ch)
    return "".join(out)


def ordinal(n):
    n = int(n)
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"
