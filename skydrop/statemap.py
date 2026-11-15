"""The warnings map: your state's counties, each filled in the color of the most urgent
watch, warning or advisory covering it, the way a TV station shows them.

Counties come from the U.S. Census TIGERweb service (outlines, generalized), the alerts
from the National Weather Service's feed for the state (each names the counties it covers
by their FIPS codes), the cities from TIGERweb's places."""

import math
import time

from . import gfx
from .alerts import _from_nws
from .theme import blend
from .util import num

# the states and territories: postal code -> (FIPS code, name)
STATES = {
    "AL": ("01", "Alabama"), "AK": ("02", "Alaska"), "AZ": ("04", "Arizona"), "AR": ("05", "Arkansas"),
    "CA": ("06", "California"), "CO": ("08", "Colorado"), "CT": ("09", "Connecticut"), "DE": ("10", "Delaware"),
    "DC": ("11", "District of Columbia"), "FL": ("12", "Florida"), "GA": ("13", "Georgia"), "HI": ("15", "Hawaii"),
    "ID": ("16", "Idaho"), "IL": ("17", "Illinois"), "IN": ("18", "Indiana"), "IA": ("19", "Iowa"),
    "KS": ("20", "Kansas"), "KY": ("21", "Kentucky"), "LA": ("22", "Louisiana"), "ME": ("23", "Maine"),
    "MD": ("24", "Maryland"), "MA": ("25", "Massachusetts"), "MI": ("26", "Michigan"), "MN": ("27", "Minnesota"),
    "MS": ("28", "Mississippi"), "MO": ("29", "Missouri"), "MT": ("30", "Montana"), "NE": ("31", "Nebraska"),
    "NV": ("32", "Nevada"), "NH": ("33", "New Hampshire"), "NJ": ("34", "New Jersey"), "NM": ("35", "New Mexico"),
    "NY": ("36", "New York"), "NC": ("37", "North Carolina"), "ND": ("38", "North Dakota"), "OH": ("39", "Ohio"),
    "OK": ("40", "Oklahoma"), "OR": ("41", "Oregon"), "PA": ("42", "Pennsylvania"), "RI": ("44", "Rhode Island"),
    "SC": ("45", "South Carolina"), "SD": ("46", "South Dakota"), "TN": ("47", "Tennessee"), "TX": ("48", "Texas"),
    "UT": ("49", "Utah"), "VT": ("50", "Vermont"), "VA": ("51", "Virginia"), "WA": ("53", "Washington"),
    "WV": ("54", "West Virginia"), "WI": ("55", "Wisconsin"), "WY": ("56", "Wyoming"), "AS": ("60", "American Samoa"),
    "GU": ("66", "Guam"), "MP": ("69", "Northern Mariana Islands"), "PR": ("72", "Puerto Rico"),
    "VI": ("78", "U.S. Virgin Islands"),
}
FIPS_STATE = {fips: code for code, (fips, _name) in STATES.items()}


def counties_url(base, fips):
    """TIGERweb's counties for a state, outlines generalized to about a kilometer."""
    return (f"{base}/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query?where=STATE%3D%27{fips}%27"
            "&outFields=GEOID,BASENAME&returnGeometry=true&maxAllowableOffset=0.01&outSR=4326"
            "&geometryPrecision=3&f=geojson")


def places_url(base, fips, count=60):
    """TIGERweb's largest incorporated places in a state, by land area."""
    return (f"{base}/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer/28/query?where=STATE%3D%27{fips}%27"
            "&outFields=BASENAME,CENTLAT,CENTLON,AREALAND&returnGeometry=false"
            f"&orderByFields=AREALAND%20DESC&resultRecordCount={count}&f=json")


def parse(raw):
    """The fetched counties and places, made ready to draw: {"state", "name", "counties":
    [{"geoid", "name", "rings"}], "places": [(name, lat, lon)], "bbox": (lon0, lat0, lon1,
    lat1)}, or None."""
    if not isinstance(raw, dict) or not raw.get("state"):
        return None
    state = raw["state"]
    wrap = state == "AK"                       # the Aleutians cross the date line
    counties = []
    for f in (raw.get("counties") or {}).get("features") or []:
        p = f.get("properties") or {}
        g = f.get("geometry") or {}
        polys = g.get("coordinates") or []
        if g.get("type") == "Polygon":
            polys = [polys]
        elif g.get("type") != "MultiPolygon":
            continue
        rings = []
        for poly in polys:
            for ring in poly[:1]:                # the outer ring of each part
                pts = [(x - 360 if wrap and x > 0 else x, y) for x, y in ring if x is not None and y is not None]
                if len(pts) >= 3:
                    rings.append(pts)
        if rings and p.get("GEOID"):
            counties.append({"geoid": str(p["GEOID"]), "name": p.get("BASENAME") or "", "rings": rings})
    if not counties:
        return None
    xs = [x for c in counties for r in c["rings"] for x, _y in r]
    ys = [y for c in counties for r in c["rings"] for _x, y in r]
    places = []
    for f in (raw.get("places") or {}).get("features") or []:
        a = f.get("attributes") or {}
        la, lo = num(a.get("CENTLAT")), num(a.get("CENTLON"))
        if la is not None and lo is not None and a.get("BASENAME"):
            places.append((a["BASENAME"], la, lo - 360 if wrap and lo > 0 else lo))
    return {"state": state, "name": STATES.get(state, ("", state))[1], "counties": counties, "places": places,
            "bbox": (min(xs), min(ys), max(xs), max(ys))}


def state_alerts(feed, now=None):
    """Every alert in the state's feed still in effect, the latest of each."""
    now = now or time.time()
    out = {}
    for f in (feed or {}).get("features") or []:
        a = _from_nws(f, now)
        if a:
            old = out.get(a["key"])
            if old is None or (a.get("issued") or 0) >= (old.get("issued") or 0):
                out[a["key"]] = a
    return list(out.values())


def county_fills(alerts, smap):
    """{county GEOID: the most urgent alert covering it}, by the counties each alert names
    (its SAME codes, or its county UGC codes)."""
    if not smap:
        return {}
    have = {c["geoid"] for c in smap["counties"]}
    fips = STATES.get(smap["state"], ("",))[0]
    best = {}
    for a in alerts or []:
        ids = set()
        for code in a.get("same") or []:
            code = str(code)
            if len(code) == 6:
                ids.add(code[1:])
        for code in a.get("ugc") or []:
            code = str(code)
            if len(code) == 6 and code[2] == "C" and code[:2] == smap["state"]:
                ids.add(fips + code[3:])
        for geoid in ids & have:
            if geoid not in best or a.get("rank", 99) < best[geoid].get("rank", 99):
                best[geoid] = a
    return best


def legend(fills):
    """The kinds of alert on the map, most urgent first: [(name, rgb, counties)]."""
    kinds = {}
    for a in fills.values():
        k = kinds.setdefault(a["event"], [a.get("rank", 99), tuple(a.get("rgb") or (200, 40, 40)), 0])
        k[2] += 1
    return [(name, rgb, n) for name, (_rank, rgb, n) in sorted(kinds.items(), key=lambda kv: (kv[1][0], kv[0]))]


def short_name(event):
    return event.upper().replace("THUNDERSTORM", "T-STORM").replace("SPECIAL WEATHER", "SPECIAL WX")


def projection(bbox, x0, y0, w, h, pad=0.04):
    """lon/lat -> pixels, the state as large as fits in the box (w x h at x0, y0), north up,
    east-west scaled by the cosine of its middle latitude."""
    lon0, lat0, lon1, lat1 = bbox
    k = math.cos(math.radians((lat0 + lat1) / 2))
    sw, sh = max(1e-6, (lon1 - lon0) * k), max(1e-6, lat1 - lat0)
    scale = min(w * (1 - 2 * pad) / sw, h * (1 - 2 * pad) / sh)
    ox = x0 + (w - sw * scale) / 2
    oy = y0 + (h - sh * scale) / 2

    def at(lon, lat):
        return ox + (lon - lon0) * k * scale, oy + (lat1 - lat) * scale
    return at


def aspect(smap):
    """The state's height over its width, as drawn."""
    lon0, lat0, lon1, lat1 = smap["bbox"]
    k = math.cos(math.radians((lat0 + lat1) / 2))
    return (lat1 - lat0) / max(1e-6, (lon1 - lon0) * k)


def map_image(smap, fills, w, h, bg, cols=1, rows=1, home=None, labels=True, head=0, light=False,
              ink=(235, 238, 245), coarse=False):
    """The warnings map as a picture w x h (cols x rows cells): the state's counties in gray,
    those under an alert in its color, the county lines dark, the state's edge light, a soft
    shadow under it; the largest cities dotted and named (`labels`), home marked; `head` rows
    at the top for the key: the kinds of alert, each in its color. `coarse`: for a picture
    of a few dozen pixels a side (drawn in text blocks), the colors alone, home a dot."""
    bg = tuple(bg[:3])
    S = 3
    W, H = w * S, h * S
    img = gfx.Image.new("RGB", (W, H), bg)
    if not smap:
        return img.resize((w, h), gfx.LANCZOS)
    cell_w, cell_h = W / max(1, cols), H / max(1, rows)
    top = int(head * cell_h)
    at = projection(smap["bbox"], 0, top, W, H - top, pad=0.03)
    base_fill = (88, 94, 110) if not light else (196, 200, 210)
    lines = (30, 33, 44) if not light else (110, 116, 130)
    state = gfx.Image.new("L", (W, H), 0)
    sd = gfx.ImageDraw.Draw(state)
    shapes = []
    for c in smap["counties"]:
        polys = [[at(x, y) for x, y in ring] for ring in c["rings"]]
        shapes.append((c, polys))
        for p in polys:
            sd.polygon(p, fill=255)
    if not coarse:                              # a soft shadow under the state
        sh = gfx.Image.new("L", (W, H), 0)
        sh.paste(state, (int(cell_w * 0.12), int(cell_h * 0.16)))
        sh = sh.filter(gfx.ImageFilter.GaussianBlur(cell_h * 0.18)).point(
            lambda v: int(v * (0.65 if not light else 0.3)))
        img.paste(gfx.Image.new("RGB", (W, H), (0, 0, 8)), (0, 0), sh)
    d = gfx.ImageDraw.Draw(img)
    for c, polys in shapes:
        a = fills.get(c["geoid"])
        fill = tuple(a.get("rgb") or (200, 40, 40)) if a else base_fill
        for p in polys:
            d.polygon(p, fill=fill)
    if not coarse:
        width = max(1, int(S * 0.8))
        for _c, polys in shapes:
            for p in polys:
                d.line(p + p[:1], fill=lines, width=width)
    # the state's edge, light
    grown = state.filter(gfx.ImageFilter.MaxFilter(3 if coarse else max(3, (int(S * 1.6) // 2) * 2 + 1)))
    edge = gfx.ImageChops.subtract(grown, state)
    img.paste(gfx.Image.new("RGB", (W, H), (214, 220, 232) if not light else (60, 66, 84)), (0, 0), edge)
    img = img.resize((w, h), gfx.LANCZOS)
    d = gfx.ImageDraw.Draw(img)
    at1 = projection(smap["bbox"], 0, top / S, w, h - top / S, pad=0.03)
    cw, ch = w / max(1, cols), h / max(1, rows)
    # home
    if home and coarse:
        x, y = at1(home[1], home[0])
        d.point((int(x), int(y)), fill=(255, 255, 255))
    elif home:
        x, y = at1(home[1], home[0])
        r = max(2.5, ch * 0.2)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255), outline=(10, 14, 40), width=1)
        d.ellipse([x - r * 0.45, y - r * 0.45, x + r * 0.45, y + r * 0.45], fill=(230, 40, 60))
    # the cities, the largest first, each where its name has room
    halo = (16, 18, 30) if not light else (250, 250, 252)
    if labels and not coarse and smap.get("places"):
        fnt = gfx.font(max(8, ch * 0.5), bold=True)
        taken = []
        if home:
            hx, hy = at1(home[1], home[0])
            taken.append((hx - ch * 0.3, hy - ch * 0.3, hx + ch * 0.3, hy + ch * 0.3))
        shown = 0
        for name, la, lo in smap["places"]:
            if shown >= max(4, int(w * h / (cw * ch) / 28)):
                break
            x, y = at1(lo, la)
            if not (0 <= x < w and top / S <= y < h):
                continue
            tw = gfx.text_width(d, name, fnt)
            r = max(1.5, ch * 0.1)
            right = x + r * 2 + tw < w - 2
            box = (x + r * 2, y - ch * 0.32, x + r * 2 + tw, y + ch * 0.32) if right else \
                (x - r * 2 - tw, y - ch * 0.32, x - r * 2, y + ch * 0.32)
            pad = ch * 0.25
            if box[0] < 1 or any(box[0] - pad < b[2] and b[0] - pad < box[2] and box[1] - pad < b[3] and
                                 b[1] - pad < box[3] for b in taken):
                continue
            taken.append(box)
            shown += 1
            d.ellipse([x - r, y - r, x + r, y + r], fill=ink, outline=halo)
            gfx.draw_text(d, (box[0], y), name, fnt, ink, anchor="lm", stroke=max(1, int(ch * 0.08)),
                          stroke_fill=halo)
    # the key, across the top: each kind of alert in its color
    if head:
        kinds = legend(fills)
        fnt = gfx.font(max(8, ch * 0.5), bold=True)
        x, y = cw * 0.4, ch * 0.5
        sw = ch * 0.62
        for name, rgb, _n in kinds:
            text = short_name(name)
            tw = gfx.text_width(d, text, fnt)
            if x + sw + 6 + tw > w - 2:
                x, y = cw * 0.4, y + ch * 0.95
                if y > head * ch - ch * 0.3:
                    break
            d.rectangle([x, y - sw / 2, x + sw, y + sw / 2], fill=tuple(rgb), outline=blend(tuple(rgb), (0, 0, 0), 0.5))
            gfx.draw_text(d, (x + sw + 5, y), text, fnt, ink if not light else (20, 24, 40), anchor="lm")
            x += sw + 5 + tw + cw * 1.4
        if not kinds:
            gfx.draw_text(d, (cw * 0.4, ch * 0.5), "NO WATCHES OR WARNINGS", fnt,
                          (150, 220, 150) if not light else (20, 110, 40), anchor="lm")
    return img
