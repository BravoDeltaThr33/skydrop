"""Watches, warnings and advisories around you: what's in effect, what's new, and
whether a Tornado Warning covers your ZIP code.

Data (refreshed every refresh_seconds by the store):
  nws_alerts_area  NWS API /alerts/active?area=<your state> (every alert in the state)
  nws_alerts       NWS API /alerts/active?point=<home>      (alerts at your exact point)
  iem_sbw          IEM storm-based warning polygons (nationwide, for warnings across a state line)
  iem_lsr          IEM local storm reports near you
  zcta             your ZIP code's outline from the Census Bureau

An alert's identity across updates is its VTEC event (office, phenomenon,
significance, event number, year), so a warning that is continued or extended
isn't "new" again, while one newly extended into your ZIP code is.
"""

import calendar
import math
import os
import re
import time

from .paths import CACHE_DIR
from .util import bearing_deg, distance_mi, geojson_rings, iso_epoch, num, point_in_rings, safe_text
from .wxmath import compass

ALARM_DIR = os.path.join(CACHE_DIR, "alarms")

VTEC_RE = re.compile(r"/([OTEX])\.([A-Z]{3})\.([A-Z]{4})\.([A-Z]{2})\.([A-Z])\.(\d{4})\."
                     r"(\d{6}T\d{4}Z)-(\d{6}T\d{4}Z)/")
MOTION_RE = re.compile(r"(\S+?)\.\.\.storm\.\.\.(\d+)DEG\.\.\.(\d+)KT\.\.\.(.+)", re.I)

# (rank, colour) by VTEC phenomenon+significance; lower rank is more urgent
EVENT_STYLE = {
    "TO.W": (1, (230, 20, 30)), "EW.W": (2, (255, 60, 150)), "SV.W": (4, (255, 150, 0)),
    "FF.W": (5, (30, 200, 70)), "SQ.W": (6, (170, 110, 255)), "MA.W": (7, (255, 120, 0)),
    "DS.W": (7, (200, 170, 120)), "TO.A": (8, (255, 225, 0)), "SV.A": (9, (219, 112, 147)),
    "FF.A": (10, (46, 139, 87)), "HU.W": (3, (220, 20, 60)), "TR.W": (5, (178, 34, 34)),
    "SS.W": (4, (181, 36, 247)), "BZ.W": (6, (255, 69, 0)), "WS.W": (7, (255, 105, 180)),
    "IS.W": (6, (139, 0, 139)), "HW.W": (8, (218, 165, 32)), "EH.W": (8, (199, 21, 133)),
    "FL.W": (9, (0, 255, 0)), "FA.W": (9, (0, 255, 0)),
}
SIG_RANK = {"W": 12, "A": 14, "Y": 16, "S": 18}
SIG_COLOR = {"W": (220, 40, 40), "A": (255, 170, 0), "Y": (230, 200, 70), "S": (110, 160, 230)}

# the National Weather Service's map colours for the rest, by event name
EVENT_RGB = {
    "winter storm watch": (70, 130, 180), "winter weather advisory": (123, 104, 238),
    "freeze warning": (72, 61, 139), "freeze watch": (0, 255, 255), "frost advisory": (100, 149, 237),
    "hard freeze warning": (148, 0, 211), "hard freeze watch": (65, 105, 225),
    "extreme cold warning": (0, 0, 255), "extreme cold watch": (0, 0, 255), "cold weather advisory": (175, 238, 238),
    "wind chill warning": (176, 196, 222), "wind chill watch": (95, 158, 160), "wind chill advisory": (175, 238, 238),
    "lake effect snow warning": (0, 139, 139), "snow squall warning": (199, 21, 133),
    "heavy freezing spray warning": (0, 191, 255), "freezing spray advisory": (0, 191, 255),
    "freezing fog advisory": (0, 128, 128), "dense fog advisory": (112, 128, 144), "dense smoke advisory": (240, 230, 140),
    "wind advisory": (210, 180, 140), "lake wind advisory": (210, 180, 140), "high wind watch": (184, 134, 11),
    "extreme wind warning": (255, 140, 0), "heat advisory": (255, 127, 80), "excessive heat watch": (128, 0, 0),
    "extreme heat watch": (128, 0, 0), "extreme heat warning": (199, 21, 133),
    "flood watch": (46, 139, 87), "flood advisory": (0, 255, 127), "flood statement": (0, 255, 0),
    "flash flood statement": (139, 0, 0), "hydrologic outlook": (144, 238, 144),
    "coastal flood warning": (34, 139, 34), "coastal flood watch": (102, 205, 170),
    "coastal flood advisory": (124, 252, 0), "coastal flood statement": (107, 142, 35),
    "lakeshore flood warning": (34, 139, 34), "lakeshore flood advisory": (124, 252, 0),
    "high surf warning": (34, 139, 34), "high surf advisory": (186, 85, 211), "rip current statement": (64, 224, 208),
    "beach hazards statement": (64, 224, 208), "small craft advisory": (216, 191, 216),
    "brisk wind advisory": (216, 191, 216), "hazardous seas warning": (216, 191, 216),
    "hazardous seas watch": (72, 61, 139), "gale warning": (221, 160, 221), "gale watch": (255, 192, 203),
    "storm warning": (148, 0, 211), "storm watch": (255, 228, 181), "special marine warning": (255, 165, 0),
    "marine weather statement": (255, 218, 185), "red flag warning": (255, 20, 147),
    "fire weather watch": (255, 222, 173), "extreme fire danger": (233, 150, 122), "fire warning": (160, 82, 45),
    "dust storm warning": (255, 228, 196), "blowing dust warning": (255, 228, 196),
    "blowing dust advisory": (189, 183, 107), "dust advisory": (189, 183, 107),
    "air quality alert": (128, 128, 128), "air stagnation advisory": (128, 128, 128),
    "special weather statement": (255, 228, 181), "severe weather statement": (0, 255, 255),
    "hazardous weather outlook": (238, 232, 170), "short term forecast": (152, 251, 152),
    "tropical storm watch": (240, 128, 128), "hurricane watch": (255, 0, 255), "storm surge watch": (219, 127, 247),
    "tsunami warning": (253, 99, 71), "tsunami advisory": (210, 105, 30), "tsunami watch": (255, 0, 255),
    "avalanche warning": (30, 144, 255), "avalanche watch": (244, 164, 96), "avalanche advisory": (205, 133, 63),
}

AREA_CHOICES = ["zip", "county", "25", "50", "100", "200", "state"]


# ----------------------------------------------------------------------------
# parsing
# ----------------------------------------------------------------------------

def vtec_parse(text):
    m = VTEC_RE.search(text or "")
    if not m:
        return None
    cls, action, office, phen, sig, etn, start, end = m.groups()

    def t(stamp):
        if stamp.startswith("000000"):
            return None
        try:
            return calendar.timegm(time.strptime(stamp, "%y%m%dT%H%MZ"))
        except ValueError:
            return None

    return {"class": cls, "action": action, "office": office, "phen": phen, "sig": sig, "etn": int(etn),
            "start": t(start), "end": t(end)}


def motion_parse(text):
    """'2026-10-30T00:14:00-00:00...storm...135DEG...20KT...29.49,-100.26' ->
    {t, toward, kt, lat, lon, points}.  The DEG value is the direction the storm moves from."""
    m = MOTION_RE.search(text or "")
    if not m:
        return None
    stamp, deg, kt, locs = m.groups()
    pts = []
    for pair in locs.split():
        try:
            la, lo = (float(x) for x in pair.split(","))
            pts.append((la, lo))
        except ValueError:
            continue
    if not pts:
        return None
    return {"t": iso_epoch(stamp), "from": int(deg), "toward": (int(deg) + 180) % 360, "kt": int(kt),
            "lat": pts[0][0], "lon": pts[0][1], "points": pts}


def _ugc_from_url(url):
    if not url:
        return None
    return str(url).rstrip("/").rsplit("/", 1)[-1].upper()


def county_label(name, state=""):
    """An NWS county zone's name as people say it: 'Dallas' -> 'Dallas County', a Louisiana
    parish 'Orleans Parish'; names that already say what they are ('City of Richmond',
    'St. Louis City', 'District of Columbia') and Alaska's boroughs stay as they are."""
    name = " ".join(str(name or "").split())
    if not name:
        return ""
    low = name.lower()
    if any(w in low for w in ("county", "parish", "borough", "city", "district", "(")):
        return name
    state = str(state or "").upper()
    if state == "LA":
        return f"{name} Parish"
    if state == "AK":
        return name
    return f"{name} County"


def home_codes(pts):
    """The UGC county and zone codes for the home point, from the NWS point lookup."""
    pts = pts or {}
    out = set()
    for k in ("county", "forecastZone", "fireWeatherZone"):
        c = _ugc_from_url(pts.get(k))
        if c:
            out.add(c)
    return out


# ----------------------------------------------------------------------------
# geometry
# ----------------------------------------------------------------------------

def _seg_cross(a, b, c, d):
    def orient(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return 0 if abs(v) < 1e-14 else (1 if v > 0 else -1)
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return o1 != o2 and o3 != o4


def _bbox(rings):
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def rings_intersect(a, b):
    """True when two polygons (lists of (lon, lat) rings) overlap at all."""
    if not a or not b:
        return False
    ba, bb = _bbox(a), _bbox(b)
    if not ba or not bb or ba[2] < bb[0] or bb[2] < ba[0] or ba[3] < bb[1] or bb[3] < ba[1]:
        return False
    if any(point_in_rings(p[0], p[1], b) for r in a for p in r[:1]):
        return True
    if any(point_in_rings(p[0], p[1], a) for r in b for p in r[:1]):
        return True
    for ra in a:
        segs_a = list(zip(ra, ra[1:] + ra[:1]))
        for rb in b:
            # only edges of b near a's box can cross
            for c, d in zip(rb, rb[1:] + rb[:1]):
                if max(c[0], d[0]) < ba[0] or min(c[0], d[0]) > ba[2] or max(c[1], d[1]) < ba[1] \
                        or min(c[1], d[1]) > ba[3]:
                    continue
                for p, q in segs_a:
                    if _seg_cross(p, q, c, d):
                        return True
    return False


def distance_to_rings(lat, lon, rings):
    """Miles from a point to a polygon (0 inside)."""
    if not rings:
        return None
    if point_in_rings(lon, lat, rings):
        return 0.0
    kx = 69.172 * math.cos(math.radians(lat))
    ky = 69.0
    best = None
    for r in rings:
        for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1]):
            ax, ay = (x1 - lon) * kx, (y1 - lat) * ky
            bx, by = (x2 - lon) * kx, (y2 - lat) * ky
            dx, dy = bx - ax, by - ay
            L = dx * dx + dy * dy
            f = 0.0 if L == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L))
            d = math.hypot(ax + dx * f, ay + dy * f)
            if best is None or d < best:
                best = d
    return best


def rings_center(rings):
    b = _bbox(rings)
    if not b:
        return None
    return (b[1] + b[3]) / 2.0, (b[0] + b[2]) / 2.0


# ----------------------------------------------------------------------------
# building the list
# ----------------------------------------------------------------------------

def _style(phen, sig, event):
    hit = EVENT_STYLE.get(f"{phen}.{sig}")
    if hit:
        return hit
    e = (event or "").lower()
    if "tornado warning" in e:
        return EVENT_STYLE["TO.W"]
    if e in EVENT_RGB:
        kind = "W" if "warning" in e else "A" if "watch" in e else "Y" if "advisory" in e else "S"
        return (SIG_RANK.get(sig or kind, SIG_RANK[kind]), EVENT_RGB[e])
    if "warning" in e or "emergency" in e:
        return (SIG_RANK["W"], SIG_COLOR["W"])
    if "watch" in e:
        return (SIG_RANK["A"], SIG_COLOR["A"])
    if "advisory" in e:
        return (SIG_RANK["Y"], SIG_COLOR["Y"])
    return (SIG_RANK.get(sig, 20), SIG_COLOR.get(sig, (150, 150, 200)))


def _from_nws(feat, now):
    p = feat.get("properties") or {}
    if (p.get("status") or "Actual") != "Actual" or p.get("messageType") == "Cancel":
        return None
    end = iso_epoch(p.get("ends") or p.get("expires"))
    if end is not None and end < now:
        return None
    params = p.get("parameters") or {}
    vt = None
    for v in params.get("VTEC") or []:
        vt = vtec_parse(v)
        if vt and vt["class"] == "O":
            break
        vt = None
    if vt and vt["action"] in ("CAN", "EXP", "UPG"):
        return None
    if vt and vt.get("end"):
        end = vt["end"] if end is None or vt["end"] < end + 60 else end
    event = p.get("event") or "Alert"
    phen, sig = (vt["phen"], vt["sig"]) if vt else ("", "")
    key = f"{vt['office']}.{phen}.{sig}.{vt['etn']:04d}" if vt else (p.get("id") or feat.get("id"))
    if vt and vt.get("start"):
        key += time.strftime(".%Y", time.gmtime(vt["start"]))
    elif vt:
        key += time.strftime(".%Y", time.gmtime(iso_epoch(p.get("sent")) or now))
    tags = {}
    for k, label in (("tornadoDetection", "tornado"), ("tornadoDamageThreat", "damage"),
                     ("thunderstormDamageThreat", "damage"), ("maxHailSize", "hail"), ("maxWindGust", "wind"),
                     ("flashFloodDetection", "flood"), ("flashFloodDamageThreat", "flood_damage"),
                     ("waterspoutDetection", "waterspout")):
        val = params.get(k)
        if isinstance(val, list) and val:
            tags[label] = str(val[0])
    motion = None
    for mtext in params.get("eventMotionDescription") or []:
        motion = motion_parse(mtext)
        if motion:
            break
    text = " ".join([p.get("description") or "", " ".join(params.get("NWSheadline") or [])]).upper()
    emergency = "TORNADO EMERGENCY" in text or "FLASH FLOOD EMERGENCY" in text or \
        tags.get("damage") == "CATASTROPHIC" or tags.get("flood_damage") == "CATASTROPHIC"
    pds = "PARTICULARLY DANGEROUS SITUATION" in text or tags.get("damage") == "CONSIDERABLE" and phen == "TO"
    rank, rgb = _style(phen, sig, event)
    if emergency:
        rank, rgb = 0, (200, 40, 255)
    rings = geojson_rings(feat.get("geometry"))
    geo = p.get("geocode") or {}
    return {
        "id": p.get("id") or feat.get("id"), "key": key, "src": "NWS", "event": event,
        "phen": phen, "sig": sig, "action": vt["action"] if vt else "", "office": vt["office"] if vt else "",
        "etn": vt["etn"] if vt else None,
        "severity": p.get("severity"), "urgency": p.get("urgency"), "certainty": p.get("certainty"),
        "msgtype": p.get("messageType"), "headline": p.get("headline"),
        "nws_headline": safe_text(" ".join(params.get("NWSheadline") or [])),
        "desc": safe_text(p.get("description")), "instruction": safe_text(p.get("instruction")),
        "area": safe_text(p.get("areaDesc") or ""), "sender": p.get("senderName"),
        "issued": iso_epoch(p.get("sent")), "start": iso_epoch(p.get("onset") or p.get("effective")), "end": end,
        "rings": rings, "ugc": [str(u).upper() for u in geo.get("UGC") or []],
        "same": [str(x) for x in geo.get("SAME") or []], "tags": tags, "emergency": emergency, "pds": pds,
        "motion": motion, "rank": rank, "rgb": rgb,
    }


def _from_sbw(w, now):
    """An IEM storm-based warning (used for warnings in neighbouring states)."""
    exp = w.get("expire")
    if exp is not None and exp < now:
        return None
    phen, sig = w.get("phen") or "", w.get("sig") or "W"
    rank, rgb = _style(phen, sig, w.get("name"))
    if w.get("emergency"):
        rank, rgb = 0, (200, 40, 255)
    key = w.get("id") or ""
    parts = key.split(".")
    if len(parts) >= 5 and parts[3].isdigit():
        key = f"K{parts[0]}.{parts[1]}.{parts[2]}.{int(parts[3]):04d}.{parts[4]}" if len(parts[0]) == 3 else key
    tags = {}
    if w.get("tornado"):
        tags["tornado"] = str(w["tornado"])
    if w.get("damage"):
        tags["damage"] = str(w["damage"])
    if w.get("hail"):
        tags["hail"] = f"{w['hail']:.2f}"
    if w.get("wind"):
        tags["wind"] = f"{w['wind']:.0f} MPH"
    return {"id": key, "key": key, "src": "IEM", "event": w.get("name") or "Warning", "phen": phen, "sig": sig,
            "action": "", "office": "", "severity": None, "urgency": None, "certainty": None, "msgtype": "Alert",
            "headline": None, "nws_headline": "", "desc": "", "instruction": "", "area": "",
            "sender": f"NWS {w.get('wfo') or ''}".strip(), "issued": w.get("issue"), "start": w.get("issue"),
            "end": exp, "rings": w.get("rings") or [], "ugc": [], "same": [], "tags": tags,
            "emergency": bool(w.get("emergency")), "pds": bool(w.get("pds")), "motion": None, "rank": rank,
            "rgb": rgb}


def build_area_alerts(state_feed, point_feed, sbw, loc, pts, zcta, now=None, max_mi=250, watches=None):
    """Every alert worth knowing about around home, most urgent first, with
    distance and whether it covers the home point, the ZIP code and the county.

    watches: SPC watch outlines (IEM GeoJSON). Watches are issued by county and carry no
    polygon, so the outline with the same watch number gives them a distance."""
    now = now or time.time()
    if not loc or loc.get("lat") is None:
        return []
    outlines = {}
    for f in (watches or {}).get("features") or []:
        p = f.get("properties") or {}
        try:
            n = int(p.get("num"))
        except (TypeError, ValueError):
            continue
        r = geojson_rings(f.get("geometry"))
        if r:
            outlines[n] = r
    lat, lon = loc["lat"], loc["lon"]
    codes = home_codes(pts)
    county = next((c for c in codes if len(c) == 6 and c[2] == "C"), None)
    zrings = (zcta or {}).get("rings") or []
    point_ids = set()
    out = {}
    for feed, at_point in ((point_feed, True), (state_feed, False)):
        for feat in (feed or {}).get("features") or []:
            a = _from_nws(feat, now)
            if a is None:
                continue
            if at_point:
                point_ids.add(a["id"])
            old = out.get(a["key"])
            if old is not None and (old.get("issued") or 0) >= (a.get("issued") or 0):
                if at_point:
                    old["at_point"] = True
                continue
            a["at_point"] = at_point or (old or {}).get("at_point", False)
            out[a["key"]] = a
    for w in sbw or []:
        a = _from_sbw(w, now)
        if a is None or not a["rings"]:
            continue
        same = next((x for x in out.values() if x["phen"] == a["phen"] and x["sig"] == a["sig"] and x["rings"]
                     and rings_intersect(x["rings"], a["rings"])), None)
        if same is not None:
            continue
        a["at_point"] = False
        out[a["key"]] = a
    rows = []
    for a in out.values():
        rings = a["rings"]
        outline = outlines.get(a.get("etn")) if not rings and a["sig"] == "A" and a["phen"] in ("TO", "SV") else None
        if outline:
            # by county, as issued; the SPC outline only says how far away the watch is
            mine = bool(a.get("at_point")) or bool(codes & set(a["ugc"]))
            d = 0.0 if mine else distance_to_rings(lat, lon, outline)
            c = rings_center(outline)
            a["dist"] = d
            a["bearing"] = bearing_deg(lat, lon, c[0], c[1]) if c and d else None
            a["inside"] = a["in_zip"] = mine
        elif rings:
            d = distance_to_rings(lat, lon, rings)
            c = rings_center(rings)
            a["dist"] = d
            a["bearing"] = bearing_deg(lat, lon, c[0], c[1]) if c and d else None
            a["inside"] = d == 0.0 or a.get("at_point", False)
            a["in_zip"] = rings_intersect(rings, zrings) if zrings else (d is not None and d <= 1.0)
        else:
            a["dist"] = 0.0 if (a.get("at_point") or codes & set(a["ugc"])) else None
            a["bearing"] = None
            a["inside"] = bool(a.get("at_point")) or bool(codes & set(a["ugc"]))
            a["in_zip"] = a["inside"]
        a["in_county"] = bool(county and county in a["ugc"]) or a["inside"] or a["in_zip"]
        if a["dist"] is not None and a["dist"] > max_mi and not a["inside"]:
            continue
        if a["motion"] and a["motion"].get("lat") is not None:
            a["motion"]["dist"] = distance_mi(lat, lon, a["motion"]["lat"], a["motion"]["lon"])
        rows.append(a)
    rows.sort(key=lambda a: (a["rank"], not a["in_zip"], a["dist"] if a["dist"] is not None else 9e9,
                             -(a.get("issued") or 0)))
    return rows


def in_area(a, area):
    """Does this alert belong in the ticker for the chosen area?"""
    area = str(area or "100").lower()
    if area == "state":   # the state's own alert feed (polygons from IEM alone may be next door)
        return a.get("src") == "NWS" or (a["dist"] is not None and a["dist"] <= 60)
    if area == "zip":
        return a["in_zip"]
    if area == "county":
        return a["in_county"]
    try:
        miles = float(area)
    except ValueError:
        miles = 100.0
    if a["in_county"]:
        return True
    return a["dist"] is not None and a["dist"] <= miles   # a distance needs a polygon or a watch outline


def wanted(a, cfg):
    """Warnings and watches always; advisories and statements when the setting asks."""
    from .config import on
    if a["sig"] in ("W", "A") or "warning" in a["event"].lower() or "watch" in a["event"].lower() \
            or "emergency" in a["event"].lower():
        return True
    return on(cfg, "ticker_advisories")


# ----------------------------------------------------------------------------
# what's new, alarms
# ----------------------------------------------------------------------------

class AlertWatcher:
    """Remembers what it has seen, so it can say what's new and when to sound an alarm.

    The same alert key is never alarmed twice: a claim file under ~/.cache/skydrop/alarms
    is created atomically before sounding, which also keeps a background watcher and the
    open app from both playing the alarm."""

    NEW_SECONDS = 15 * 60

    def __init__(self):
        self.first_seen = {}
        self.started = time.time()
        self.primed = False

    def update(self, alerts, now=None):
        """Note when each alert was first seen; returns the ones that just appeared."""
        now = now or time.time()
        fresh = []
        for a in alerts:
            k = a["key"]
            if k not in self.first_seen:
                if not self.primed:
                    issued = a.get("issued") or now
                    self.first_seen[k] = issued if now - issued < self.NEW_SECONDS else now - 10 * self.NEW_SECONDS
                else:
                    self.first_seen[k] = now
                    fresh.append(a)
            a["first_seen"] = self.first_seen[k]
            a["new"] = now - self.first_seen[k] < self.NEW_SECONDS
        self.primed = True
        live = {a["key"] for a in alerts}
        for k in [k for k in self.first_seen if k not in live and now - self.first_seen[k] > 86400]:
            del self.first_seen[k]
        return fresh

    @staticmethod
    def claim(key, now=None):
        """True the first time anyone claims this alarm key (atomic across processes)."""
        now = now or time.time()
        try:
            os.makedirs(ALARM_DIR, exist_ok=True)
        except OSError:
            return True
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)[:120]
        path = os.path.join(ALARM_DIR, safe)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(fd, str(int(now)).encode())
            os.close(fd)
        except FileExistsError:
            return False
        except OSError:
            return True
        prune_claims(now)
        return True


def prune_claims(now=None, keep=3 * 86400):
    now = now or time.time()
    try:
        for name in os.listdir(ALARM_DIR):
            p = os.path.join(ALARM_DIR, name)
            try:
                if now - os.path.getmtime(p) > keep:
                    os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


def alarm_events(alerts, cfg):
    """Alarms due now: [(kind, alert, claim key)], kind 'tornado' or 'warning'.

    A Tornado Warning covering the ZIP code is a 'tornado' alarm; an upgrade of one to a
    tornado emergency sounds again.  Other warnings covering the ZIP code are 'warning'."""
    from .config import on
    out = []
    for a in alerts:
        if not a.get("in_zip"):
            continue
        tor = a["phen"] == "TO" and a["sig"] == "W" or (not a["phen"] and a["event"].lower() == "tornado warning")
        if tor and on(cfg, "tornado_alarm"):
            out.append(("tornado", a, a["key"] + (".EMERGENCY" if a.get("emergency") else "")))
        elif a["sig"] == "W" or "warning" in a["event"].lower():
            if (cfg.get("warning_alarm") or "chime") != "off":
                out.append(("warning", a, a["key"]))
    return out


# ----------------------------------------------------------------------------
# words
# ----------------------------------------------------------------------------

def threat_text(a):
    """Short impact text: 'tornado observed', 'radar indicated rotation', '70 mph wind, 1.75 in hail'."""
    tags = a.get("tags") or {}
    bits = []
    if a.get("emergency"):
        bits.append("TORNADO EMERGENCY" if a["phen"] == "TO" else "EMERGENCY")
    elif a.get("pds") and a["phen"] == "TO":
        bits.append("particularly dangerous situation")
    tor = (tags.get("tornado") or "").upper()
    if a["phen"] == "TO" or tor:
        if "OBSERVED" in tor:
            bits.append("tornado OBSERVED")
        elif "RADAR" in tor:
            bits.append("radar indicated rotation")
        elif "POSSIBLE" in tor:
            bits.append("tornado possible")
    if tags.get("wind"):
        bits.append(tags["wind"].lower().replace(" mph", " mph").replace("mph", "mph") + " wind")
    if tags.get("hail"):
        h = num(tags["hail"])
        if h:
            bits.append(f'{h:.2f}" hail'.replace("0.", "."))
    if (tags.get("flood") or "").upper() == "OBSERVED":
        bits.append("flooding observed")
    return ", ".join(bits)


def where_text(a, F):
    if a.get("in_zip"):
        return "covers your ZIP code"
    if a.get("inside"):
        return "at your location"
    if a.get("dist") is not None and a.get("rings"):
        b = a.get("bearing")
        return f"{F.dist(a['dist'])} {compass(b)}" if b is not None else F.dist(a["dist"])
    if a.get("in_county"):
        return "your county"
    return ""


def ticker_items(alerts, lsrs, cfg, F, now=None):
    """Items for the bottom warnings crawl: [(label, text, rgb, is_new, key, area, in_county)],
    the area being every county or zone the alert covers (the crawl scrolls it by), in_county
    whether it covers your county (the crawl pins your county's name to it, after its name,
    instead of saying so)."""
    from .config import on
    now = now or time.time()
    area = cfg.get("ticker_area") or "100"
    items = []
    for a in alerts:
        if not wanted(a, cfg) or not in_area(a, area):
            continue
        label = a["event"].upper()
        parts = []
        if a.get("end"):
            parts.append(f"until {F.time(a['end'])}" if a["end"] - now < 20 * 3600 else f"until {F.dtime(a['end'])}")
        threat = threat_text(a)
        if threat:
            parts.append(threat)
        mo = a.get("motion")
        if mo and mo.get("kt"):
            parts.append(f"moving {compass(mo['toward'])} at {F.speed_mph(mo['kt'] * 1.15078)}")
        where = where_text(a, F)
        if a.get("in_county") and where in ("covers your ZIP code", "at your location", "your county"):
            where = ""                  # your county's name, pinned to it, says so
        if where:
            parts.append(where)
        items.append((label, " · ".join(parts), a["rgb"], a.get("new", False), a["key"],
                      " ".join((a.get("area") or "").split()), bool(a.get("in_county"))))
    if on(cfg, "ticker_reports"):
        try:
            miles = float(area)
        except ValueError:
            miles = 60.0
        for r in lsrs or []:
            if not r.get("t") or now - r["t"] > 3 * 3600:
                continue
            if r.get("dist") is not None and r["dist"] > max(25.0, min(miles, 150.0)):
                continue
            kind = (r.get("type") or "").lower()
            if not any(w in kind for w in ("tornado", "funnel", "hail", "wind", "flood", "waterspout")):
                continue
            mag = ""
            if r.get("mag"):
                mag = f'{r["mag"]:.2f}" ' if "hail" in kind else f"{r['mag']:.0f} {r.get('unit') or ''} "
            where = f"{F.dist(r['dist'])} {compass(r['bearing'])}" if r.get("dist") is not None else ""
            place = ", ".join(x for x in (r.get("city"), r.get("state")) if x)
            text = f"{F.time(r['t'])}: {mag}{r.get('type') or 'report'}" + (f" near {place}" if place else "") + \
                (f" ({where})" if where else "")
            items.append(("STORM REPORT", text, (120, 170, 255), now - r["t"] < 900, f"lsr:{r['t']}:{place}", ""))
    return items
