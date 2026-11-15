"""Local radar: map composition, animation frames, storm overlays and a simple nowcast.

Layers (all free, no keys):
  site radar NEXRAD Level 3 from one radar (NOAA Open Data on AWS, NWS tgftp): any
             product and tilt, with storm tracks, hail, mesocyclones and TVS
  mosaic     NOAA MRMS national mosaic via opengeo GeoServer (IEM NEXRAD and RainViewer
             as fallbacks)
  satellite  NASA GIBS GOES infrared
  map        U.S. Census TIGERweb (water, counties, states, interstates, places, ZIP outline)
             or USGS imagery/topo, or OpenStreetMap (when enabled)
Everything is Web Mercator (EPSG:3857) so the layers line up.
"""

import hashlib
import io
import math
import re
import struct
import threading
import time
import urllib.parse
from collections import OrderedDict

from . import gfx, nexrad, palettes, placefile
from .alerts import rings_intersect
from . import sources as S
from .errlog import LOG
from .net import ApiError
from .store import nws_headers
from .theme import blend
from .util import bearing_deg, distance_mi, iso_epoch, num

R = 6378137.0
CONUS = (-127.0, 22.0, -64.0, 51.0)
ZOOMS = [15, 30, 60, 120, 240, 480, 960]   # miles across


def pmap(fn, items, workers=4):
    """map() on daemon threads, in order (daemon so quitting never waits for a download)."""
    items = list(items)
    out = [None] * len(items)
    nxt = [0]
    lock = threading.Lock()

    def run():
        while True:
            with lock:
                i = nxt[0]
                nxt[0] += 1
            if i >= len(items):
                return
            try:
                out[i] = fn(items[i])
            except Exception as e:  # keep going; the caller sees the exception object
                out[i] = e

    threads = [threading.Thread(target=run, daemon=True) for _ in range(max(1, min(workers, len(items))))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for r in out:
        if isinstance(r, Exception):
            raise r
    return out


def merc(lat, lon):
    lat = max(-85.0, min(85.0, lat))
    return math.radians(lon) * R, math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R


def inv_merc(x, y):
    return math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2), math.degrees(x / R)


MAP_ROLES = ("map_top", "map_bot", "map_water", "map_county", "map_road", "map_state", "map_glow", "map_tint")
MAP_COLORS = ((7, 14, 58), (16, 32, 104), (30, 56, 150), (78, 104, 186), (176, 62, 80), (214, 222, 250),
              (110, 130, 230), (120, 130, 175))


def _scale(rgb, k):
    return tuple(max(0, min(255, int(c * f))) for c, f in zip(rgb, k))


class View:
    """What to draw: center, width in miles, output size in pixels and how
    tall each pixel is relative to its width (2.0 for one-pixel-per-cell ASCII)."""

    def __init__(self, lat, lon, span_mi, w, h, yscale=1.0, mode="pixels", layer="reflectivity",
                 basemap="classic", frames=8, step=10, labels=True, stations=True, warnings=True, mini=False,
                 imperial=True, source="mosaic", site=None, product="ref", tilt=0, smooth=True, cells=True,
                 min_dbz=5.0, opacity=100, home=None, layers=(), mapcolors=None):
        self.lat, self.lon, self.span_mi = lat, lon, span_mi
        self.w, self.h, self.yscale = max(8, int(w)), max(6, int(h)), yscale
        self.mode, self.layer, self.basemap = mode, layer, basemap
        self.frames, self.step = max(1, int(frames)), max(2, int(step))
        self.labels, self.stations, self.warnings, self.mini = labels, stations, warnings, mini
        self.imperial = imperial
        self.source = "site" if source == "site" and site else "mosaic"
        self.site, self.product, self.tilt = site, product, int(tilt or 0)
        self.smooth, self.cells, self.min_dbz, self.opacity = smooth, cells, min_dbz, opacity
        self.home = home or (lat, lon)
        self.layers = tuple(sorted(layers or ()))   # extra layers: range, sites, markers, placefiles
        # the map's colors follow the theme: (top, bottom, water, counties, roads, states, glow, tile tint)
        self.mapcolors = tuple(tuple(c) for c in (mapcolors or MAP_COLORS))
        cx, cy = merc(lat, lon)
        self.mpp = span_mi * 1609.344 / self.w / max(0.2, math.cos(math.radians(lat)))
        self.mpp_y = self.mpp * yscale
        self.x0, self.x1 = cx - self.w / 2 * self.mpp, cx + self.w / 2 * self.mpp
        self.y0, self.y1 = cy - self.h / 2 * self.mpp_y, cy + self.h / 2 * self.mpp_y
        self.mi_per_px = span_mi / self.w

    def key(self):
        return (round(self.lat, 4), round(self.lon, 4), self.span_mi, self.w, self.h, round(self.yscale, 3),
                self.mode, self.layer, self.basemap, self.frames, self.step, self.labels, self.stations,
                self.warnings, self.mini, self.imperial, self.source, self.site, self.product, self.tilt,
                self.smooth, self.cells, self.min_dbz, self.opacity,
                (round(self.home[0], 4), round(self.home[1], 4)), self.layers, self.mapcolors)

    def family(self):
        """The same map at any size (used to show the old picture while a resize settles)."""
        return (round(self.lat, 4), round(self.lon, 4), self.span_mi, self.mode, self.layer, self.basemap,
                self.mini, self.source, self.site, self.product, self.tilt)

    def temp_text(self, tc):
        return f"{tc * 9 / 5 + 32:.0f}" if self.imperial else f"{tc:.0f}"

    def geo_key(self):
        """Where and how big: what the radar data layers depend on."""
        return (round(self.lat, 4), round(self.lon, 4), self.span_mi, self.w, self.h, self.yscale)

    def base_key(self):
        """The map underneath: the geography plus its style and colors."""
        return self.geo_key() + (self.basemap, self.mapcolors)

    def bbox(self):
        return f"{self.x0:.1f},{self.y0:.1f},{self.x1:.1f},{self.y1:.1f}"

    def to_px(self, lat, lon):
        x, y = merc(lat, lon)
        return (x - self.x0) / self.mpp, (self.y1 - y) / self.mpp_y

    def from_px(self, px, py):
        return inv_merc(self.x0 + px * self.mpp, self.y1 - py * self.mpp_y)

    def lonlat_bounds(self):
        la0, lo0 = inv_merc(self.x0, self.y0)
        la1, lo1 = inv_merc(self.x1, self.y1)
        return lo0, la0, lo1, la1

    def in_conus(self):
        return CONUS[0] <= self.lon <= CONUS[2] and CONUS[1] <= self.lat <= CONUS[3]


class Result:
    def __init__(self, view):
        self.view = view
        self.frames = []          # [(epoch, PIL image)]
        self.labels = []          # [(name, px, py)]
        self.temps = []           # [(px, py, temp_c, id)]
        self.marker = view.to_px(*view.home)
        self.analysis = {}
        self.radar_src = ""
        self.base_src = ""
        self.base = None          # the map alone (ascii mode only)
        self.sat = False
        self.notes = []
        self.built_at = 0.0
        self.version = 0
        self.sig = ""             # changes only when the picture itself changes
        self.warn_boxes = []      # [(name, rgb, px, py)] label anchor for polygons
        self.l3_frames = []       # decoded Level 3 product behind each frame (site radar)
        self.site = None          # {id, name, lat, lon, ...} of the radar shown
        self.product = None       # nexrad.Product shown
        self.table = None         # palettes.ColorTable used
        self.cells = []           # storm cells with tracks, hail, meso, TVS (site radar)
        self.marks = []           # [(px, py, char, rgb, label)] storm symbols for text modes
        self.hover = []           # placefile items with hover text (see placefile.hover_at)
        self.site_marks = []      # [(site id, px, py)] radar sites drawn on the map
        self.scan = {}            # VCP, mode, elevation, times of the newest scan
        self.meso = []            # mesocyclone detections [{lat, lon, r_km, kind}]
        self.tvs = []             # tornado vortex signatures [{lat, lon, kind}]

    def nbytes(self):
        return sum(img.size[0] * img.size[1] * 3 for _t, img in self.frames)

    def l3_at(self, idx):
        if not self.l3_frames:
            return None
        return self.l3_frames[max(0, min(len(self.l3_frames) - 1, idx))]


# ----------------------------------------------------------------------------
# intensity from radar colors
# ----------------------------------------------------------------------------

INTENSITY = [(0, "none"), (15, "very light"), (25, "light"), (35, "moderate"), (45, "heavy"),
             (53, "very heavy"), (62, "intense"), (70, "extreme")]
_dbz_cache = {}


def color_dbz(rgb):
    """Approximate reflectivity (dBZ) of a radar color (NWS-style palettes)."""
    hit = _dbz_cache.get(rgb)
    if hit is not None:
        return hit
    r, g, b = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
    mx, mn = max(r, g, b), min(r, g, b)
    v, s = mx, (mx - mn) / mx if mx else 0
    if mx == mn:
        h = 0.0
    elif mx == r:
        h = (60 * ((g - b) / (mx - mn)) + 360) % 360
    elif mx == g:
        h = 60 * ((b - r) / (mx - mn)) + 120
    else:
        h = 60 * ((r - g) / (mx - mn)) + 240
    if s < 0.22:
        dbz = 72 if v > 0.9 else 5
    elif 180 <= h < 260:
        dbz = 12 + (1 - v) * 8 if s > 0.35 else 10
    elif 150 <= h < 180:
        dbz = 18
    elif 85 <= h < 150:
        dbz = 22 + (1 - v) * 14          # bright green ~22, dark green ~35
    elif 70 <= h < 85:
        dbz = 35
    elif 45 <= h < 70:
        dbz = 38 + (1 - v) * 4
    elif 18 <= h < 45:
        dbz = 44 + (45 - h) / 27 * 5
    elif h < 18 or h >= 345:
        dbz = 52 + (1 - v) * 10
    elif 260 <= h < 345:
        dbz = 64 if h > 290 else 68
    else:
        dbz = 20
    _dbz_cache[rgb] = dbz
    return dbz


def intensity_word(dbz):
    word = "none"
    for lim, name in INTENSITY:
        if dbz >= lim:
            word = name
    return word


def precip_word(dbz, snowy=False):
    base = intensity_word(dbz)
    if dbz >= 55:
        return "strong storms, possible hail"
    if dbz >= 45:
        return "heavy " + ("snow" if snowy else "rain") + (" and thunderstorms" if dbz >= 50 else "")
    return f"{base} {'snow' if snowy else 'rain'}"


def _despeckle(m):
    """Drop lone wet pixels (fewer than two wet neighbours) from a small dBZ map."""
    w, h = m.size
    wet = gfx.Image.new("L", (w + 2, h + 2), 0)   # padded: the filter skips the outermost pixels
    wet.paste(m.point(lambda v: 255 if v else 0), (1, 1))
    near = wet.filter(gfx.ImageFilter.Kernel((3, 3), [1] * 9, scale=9)).crop((1, 1, w + 1, h + 1))
    keep = near.point(lambda v: 255 if v >= 84 else 0)   # mean of the 3x3 block: 3+ of 9 wet
    return gfx.ImageChops.darker(m, keep)


def dbz_map(img, maxdim=200):
    """RGBA radar image -> (small 'L' image of dBZ (0-80), scale factor)."""
    w, h = img.size
    f = min(1.0, maxdim / max(w, h))
    small = img.resize((max(1, int(w * f)), max(1, int(h * f))), gfx.NEAREST) if f < 1 else img
    px = small.load()
    sw, sh = small.size
    out = gfx.Image.new("L", (sw, sh), 0)
    op = out.load()
    for y in range(sh):
        for x in range(sw):
            p = px[x, y]
            if p[3] > 100:
                op[x, y] = int(color_dbz(p[:3]))
    return out, f


# ----------------------------------------------------------------------------
# the manager
# ----------------------------------------------------------------------------

class RadarManager:
    def __init__(self, store):
        self.store = store
        self.http = store.http
        self.lock = threading.Lock()
        self.frame_lock = threading.Lock()
        self.results = OrderedDict()
        self.building = set()
        self.gen = 0                       # bumped by invalidate()
        self.frame_cache = OrderedDict()   # url -> decoded RGBA frame
        self.frame_bytes = 0
        self.base_cache = OrderedDict()
        self.places_cache = OrderedDict()
        self.failed = {}                   # view key -> time of the last failed build
        self.first_seen = {}               # view key -> when it was first asked for
        self.version = 0
        self.status = ""
        self.last_error = ""
        self.refresh_s = 150
        self.retry_s = 60
        self.l3_lock = threading.Lock()
        self.l3_cache = OrderedDict()      # url -> decoded Level 3 product
        self.l3_bytes = 0
        self.layer_cache = OrderedDict()   # (url, view, colours) -> RGBA radar layer
        self.analysis_cache = {}           # (view, scans) -> nowcast
        self.layer_bytes = 0
        self._sites, self._sites_t, self._sites_loading = [], 0.0, False

    def cfg(self):
        return self.store.cfg_copy()

    def get(self, view, model):
        """The newest picture for this view (or the same map at another size while a
        resize settles); starts a background build when it's missing or old."""
        key = view.key()
        now = time.time()
        refresh = self.site_refresh_s() if view.source == "site" else self.refresh_s
        with self.lock:
            res = self.results.get(key)
            if res is not None:
                self.results.move_to_end(key)
            stale = res is None or now - res.built_at > refresh
            standin = None
            if res is None:
                fam = view.family()
                standin = next((r for r in reversed(self.results.values()) if r.view.family() == fam), None)
            start = stale and key not in self.building and now - self.failed.get(key, 0) > self.retry_s
            if start and res is None and standin is not None:
                # probably a terminal resize in progress: wait until the size stops changing
                first = self.first_seen.setdefault(key, now)
                if now - first < 0.4:
                    start = False
                    self._later(0.45)
            if start and len(self.building) >= 2:
                start = False
                self._later(1.0)
            if start:
                self.building.add(key)
                self.first_seen.pop(key, None)
                snap = {k: model.get(k) for k in ("sbw", "watches", "zcta", "loc", "stations", "area_alerts",
                                                  "placefiles", "markers")}
                snap["iem"] = self.store.get("iem_currents") or {}
                threading.Thread(target=self._build, args=(view, snap, self.gen), daemon=True).start()
            if len(self.first_seen) > 50:
                self.first_seen.clear()
        return res if res is not None else standin

    def site_refresh_s(self):
        """Site radar checks for a new scan at the app's refresh interval."""
        cfg = self.store.cfg
        try:
            secs = int(float(cfg.get("refresh_seconds") or 30))
        except (TypeError, ValueError):
            secs = 30
        return max(15, min(600, secs))

    def _later(self, secs):
        t = threading.Timer(secs, self._poke)
        t.daemon = True
        t.start()

    def _poke(self):
        """Ask the app to draw again (a deferred build may be ready to start)."""
        with self.lock:
            self.version += 1

    def busy(self):
        return bool(self.building)

    def invalidate(self):
        with self.lock:
            self.gen += 1          # a build already under way started from old data: it won't count as fresh
            self.failed.clear()
            for res in self.results.values():
                res.built_at = 0

    def _build(self, view, snap, gen=None):
        """gen: the invalidate() count when snap was taken. If it moves on while we build,
        the result is drawn but counts as stale, so the next frame builds it again."""
        key = view.key()
        with self.lock:
            have = key in self.results
            gen = self.gen if gen is None else gen
        if view.source == "site" and not have and not view.mini and view.frames > 1:
            # First look at this product: show the newest scan right away, then the loop.
            self._build_once(view, snap, quick=True, gen=gen)
        self._build_once(view, snap, gen=gen)

    def _build_once(self, view, snap, quick=False, gen=0):
        key = view.key()
        try:
            res = Result(view)
            cfg = self.cfg()
            self.status = "Loading map…"
            base = self._basemap(view, cfg, res)
            res.base = base if view.mode == "ascii" else None   # text mode tells echoes from map by it
            self.status = "Loading radar…"
            if view.source == "site":
                frames = self._site_frames(view, cfg, res, limit=1 if quick else None)
                if view.cells and not view.mini:
                    self._storm_cells(view, cfg, res)
            else:
                frames = self._radar_frames(view, cfg, res)
            sat = self._satellite(view, cfg, res, [t for t, _ in frames]) if view.layer in ("satellite", "radar+sat") else {}
            cw, ch = self._metrics(view)
            mx, my = res.marker
            rr = 8 if view.mode == "pixels" else 0.9
            boxes = [(mx - rr * (1 if view.mode == "pixels" else cw), my - rr * (1 if view.mode == "pixels" else ch),
                      mx + rr * (1 if view.mode == "pixels" else cw), my + rr * (1 if view.mode == "pixels" else ch))]
            if view.labels:
                res.labels = self._place_labels(view, cfg, boxes)
            if view.stations:
                res.temps = self._station_temps(view, snap, boxes)
            if view.source != "site":
                res.analysis = self._analyze(view, frames, snap)
            elif frames:
                res.analysis = self._analyze_l3(view, cfg, res)
            overlay = self._overlay(view, snap, res)
            composed = []
            timed = "placefiles" in view.layers and not view.mini and placefile.any_timed(snap)
            for t, rimg in frames:
                img = base.copy()
                simg = sat.get(t) if sat else None
                if simg is not None:
                    img.alpha_composite(simg)
                if rimg is not None and view.layer != "satellite":
                    img.alpha_composite(rimg)
                if overlay is not None:
                    img.alpha_composite(overlay)
                if timed:
                    img.alpha_composite(placefile.timed_layer(view, snap, res, view.mode == "pixels", self.http, t))
                if view.mode == "pixels":
                    self._stamp_time(img, t, view, res)
                composed.append((t, img.convert("RGB")))
            if not composed:
                img = base.copy()
                if sat:
                    simg = next(iter(sat.values()))
                    img.alpha_composite(simg)
                if overlay is not None:
                    img.alpha_composite(overlay)
                composed = [(time.time(), img.convert("RGB"))]
            res.frames = composed
            res.built_at = time.time() if not quick else 0.0
            with self.lock:
                if self.gen != gen:
                    res.built_at = 0.0     # invalidated while building (a new warning): build again now
            sig = hashlib.md5()
            sig.update(repr((key, res.labels, [(round(x), round(y), round(t, 1)) for x, y, t, _s in res.temps],
                             res.warn_boxes, res.radar_src, res.base_src,
                             [round(t) for t, _i in frames], res.marks)).encode())
            if overlay is not None:
                sig.update(overlay.tobytes())
            res.sig = sig.hexdigest()[:16]
            with self.lock:
                self.version += 1
                res.version = self.version
                self.results[key] = res
                self.failed.pop(key, None)
                total = sum(r.nbytes() for r in self.results.values())
                while len(self.results) > 1 and (len(self.results) > 8 or total > 120 * 1024 * 1024):
                    _k, gone = self.results.popitem(last=False)
                    total -= gone.nbytes()
            self.status = ""
            self.last_error = ""
            LOG.resolve("radar")
        except Exception as e:  # keep the old result; report the problem; retry in a minute
            self.last_error = f"{type(e).__name__}: {e}"
            self.status = f"Radar problem: {e}"
            LOG.report("radar", "Radar map", self.last_error)
            with self.lock:
                self.failed[key] = time.time()
                old = self.results.get(key)
                if old:
                    old.built_at = time.time() - self.refresh_s + self.retry_s
        finally:
            with self.lock:
                if not quick:
                    self.building.discard(key)
                self.version += 1

    # -- fetch helpers ----------------------------------------------------------
    def _img(self, url, ttl=600, disk_ttl=0, headers=None):
        raw = self.http.get_image(url, headers=headers, ttl=ttl, disk_ttl=disk_ttl)
        try:
            return gfx.Image.open(io.BytesIO(raw)).convert("RGBA")
        except (OSError, ValueError) as e:  # a truncated or odd image
            raise ApiError(0, f"couldn't read the image: {e}")

    def _tiles(self, view, template, zmax=19, ttl=86400 * 7):
        """Stitch XYZ tiles covering the view. template has {z} {x} {y}."""
        world = 2 * math.pi * R
        span_m = view.x1 - view.x0
        want_mpp = span_m / view.w
        z = int(max(0, min(zmax, math.floor(math.log2(world / 256 / max(want_mpp, 0.01))))))
        n = 2 ** z
        tile_m = world / n

        def tx(xm):
            return (xm + world / 2) / tile_m

        def ty(ym):
            return (world / 2 - ym) / tile_m

        fx0, fx1 = tx(view.x0), tx(view.x1)
        fy0, fy1 = ty(view.y1), ty(view.y0)
        ix0, ix1, iy0, iy1 = int(math.floor(fx0)), int(math.floor(fx1)), int(math.floor(fy0)), int(math.floor(fy1))
        if (ix1 - ix0 + 1) * (iy1 - iy0 + 1) > 64:
            raise ApiError(0, "too many map tiles for this view")
        canvas = gfx.Image.new("RGBA", ((ix1 - ix0 + 1) * 256, (iy1 - iy0 + 1) * 256), (0, 0, 0, 0))
        jobs = [(x, y) for x in range(ix0, ix1 + 1) for y in range(iy0, iy1 + 1) if 0 <= y < n]

        def one(xy):
            x, y = xy
            url = template.format(z=z, x=x % n, y=y)
            try:  # time-stamped radar tiles change hourly: keep them in memory only
                return xy, self._img(url, ttl=ttl, disk_ttl=ttl if ttl >= 86400 else 0)
            except ApiError:
                return xy, None

        for (x, y), img in pmap(one, jobs, 6):
            if img is not None:
                canvas.paste(img.resize((256, 256)) if img.size != (256, 256) else img,
                             ((x - ix0) * 256, (y - iy0) * 256))
        left, top = (fx0 - ix0) * 256, (fy0 - iy0) * 256
        right, bottom = (fx1 - ix0) * 256, (fy1 - iy0) * 256
        crop = canvas.crop((int(left), int(top), int(math.ceil(right)), int(math.ceil(bottom))))
        return crop.resize((view.w, view.h), gfx.LANCZOS)

    # -- map ----------------------------------------------------------------------
    def _tiger(self, view, cfg, service, layers, scale=1, dpi=96):
        # ArcGIS keeps pixels square (it widens the extent otherwise), so ask
        # for square pixels and squash afterwards when cells aren't square.
        base = S.url(cfg, "tigerweb")
        w, h = int(view.w * scale), int(round(view.h * view.yscale * scale))
        url = (f"{base}/arcgis/rest/services/TIGERweb/{service}/MapServer/export?bbox={view.bbox()}"
               f"&bboxSR=3857&imageSR=3857&size={w},{h}&layers=show:{layers}&format=png32&transparent=true"
               f"&dpi={dpi}&f=image")
        img = self._img(url, ttl=86400 * 7, disk_ttl=86400 * 30)
        if img.size != (view.w, view.h):
            img = img.resize((view.w, view.h), gfx.LANCZOS)
        return img

    @staticmethod
    def _tint(img, rgb, strength=1.0):
        a = img.getchannel("A")
        if strength != 1.0:
            a = a.point(lambda v: int(v * strength))
        out = gfx.Image.new("RGBA", img.size, rgb + (0,))
        out.putalpha(a)
        return out

    def _basemap(self, view, cfg, res):
        bkey = view.base_key()
        hit = self.base_cache.get(bkey)
        if hit is not None:
            res.base_src = hit[1]
            return hit[0].copy()
        w, h = view.w, view.h
        img = gfx.Image.new("RGBA", (w, h))
        d = gfx.ImageDraw.Draw(img)
        top, bot, c_water, c_county, c_road, c_state, c_glow, tint = view.mapcolors
        for y in range(h):
            d.line([(0, y), (w, y)], fill=blend(top, bot, y / max(1, h - 1)) + (255,))
        src = []
        style = view.basemap
        small = view.mode != "pixels"
        try:
            if style == "imagery" and S.enabled(cfg, "usgs_basemap"):
                t = self._tiles(view, S.url(cfg, "usgs_basemap") +
                                "/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}", zmax=16)
                t = gfx.ImageChops.multiply(t.convert("RGB"), gfx.Image.new("RGB", t.size, tint)).convert("RGBA")
                img.alpha_composite(t)
                src.append("USGS imagery")
            elif style == "topo" and S.enabled(cfg, "usgs_basemap"):
                t = self._tiles(view, S.url(cfg, "usgs_basemap") +
                                "/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}", zmax=16)
                t = gfx.ImageChops.multiply(t.convert("RGB"), gfx.Image.new("RGB", t.size, _scale(tint, (0.75, 0.85, 0.97)))).convert("RGBA")
                img.alpha_composite(t)
                src.append("USGS topo")
            elif style == "osm" and S.enabled(cfg, "osm_tiles"):
                t = self._tiles(view, S.url(cfg, "osm_tiles") + "/{z}/{x}/{y}.png", zmax=17)
                t = gfx.ImageChops.multiply(t.convert("RGB"), gfx.Image.new("RGB", t.size, _scale(tint, (1.25, 1.23, 1.14)))).convert("RGBA")
                img.alpha_composite(t)
                src.append("© OpenStreetMap contributors")
        except ApiError as e:
            res.notes.append(f"background: {e}")
        in_us = view.in_conus() or (view.lat > 17 and view.lon < -60)
        if S.enabled(cfg, "tigerweb") and in_us:
            scale = 2 if small else 1
            try:
                if style in ("classic", "plain"):
                    water = self._tiger(view, cfg, "Hydro", "1", scale)
                    img.alpha_composite(self._tint(water, c_water, 0.9))
                counties = self._tiger(view, cfg, "tigerWMS_Current", "82", scale, dpi=96 if small else 64)
                img.alpha_composite(self._tint(counties, c_county, 0.7 if small else 0.9))
                if view.span_mi <= 600:
                    roads = self._tiger(view, cfg, "Transportation", "0,1,2", scale, dpi=96 if small else 72)
                    img.alpha_composite(self._tint(roads, c_road, 0.85 if small else 0.95))
                states = self._tiger(view, cfg, "tigerWMS_Current", "80", scale, dpi=96 if small else 72)
                st = self._tint(states, c_state)
                if not small:
                    glow = self._tint(states.filter(gfx.ImageFilter.MaxFilter(3)), c_glow, 0.3)
                    img.alpha_composite(glow)
                img.alpha_composite(st)
                src.append("U.S. Census TIGERweb")
            except ApiError as e:
                res.notes.append(f"map lines: {e}")
        res.base_src = ", ".join(src)
        self.base_cache[bkey] = (img.copy(), res.base_src)
        while len(self.base_cache) > 6:
            self.base_cache.popitem(last=False)
        return img

    # -- radar ----------------------------------------------------------------------
    def _noaa_times(self, cfg, layer):
        base = S.url(cfg, "noaa_radar")
        caps = self.http.get_text(f"{base}/conus/{layer}/ows?service=WMS&version=1.1.1&request=GetCapabilities",
                                  ttl=110)
        m = re.search(r'<Extent name="time"[^>]*>([^<]+)</Extent>', caps)
        if not m:
            m = re.search(r'<Dimension name="time"[^>]*>([^<]+)</Dimension>', caps)
        if not m:
            return []
        times = [iso_epoch(t.strip()) for t in m.group(1).split(",")]
        return sorted(t for t in times if t)

    @staticmethod
    def _pick_times(times, n, step_min):
        if not times:
            return []
        out = [times[-1]]
        target = times[-1] - step_min * 60
        for t in reversed(times[:-1]):
            if len(out) >= n:
                break
            if t <= target + 60:
                out.append(t)
                target = t - step_min * 60
        return sorted(out)

    def _radar_frames(self, view, cfg, res):
        n = 1 if view.mini else view.frames
        errors = []
        if view.in_conus() and S.enabled(cfg, "noaa_radar"):
            layer = "conus_pcpn_typ" if view.layer == "type" else "conus_bref_qcd"
            try:
                times = self._pick_times(self._noaa_times(cfg, layer), n, view.step)
                if times:
                    base = S.url(cfg, "noaa_radar")

                    def one(t):
                        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
                        url = (f"{base}/conus/{layer}/ows?service=WMS&version=1.1.1&request=GetMap&layers={layer}"
                               f"&styles=&srs=EPSG:3857&bbox={view.bbox()}&width={view.w}&height={view.h}"
                               f"&format=image/png&transparent=true&time={stamp}")
                        try:
                            return t, self._cached_frame(url)
                        except ApiError as e:
                            return t, e

                    got = pmap(one, times, 4)
                    frames = [(t, img) for t, img in got if not isinstance(img, Exception)]
                    if not frames:
                        raise got[-1][1]
                    res.radar_src = "NOAA MRMS " + ("precipitation type" if layer == "conus_pcpn_typ" else "reflectivity")
                    return frames
            except ApiError as e:
                errors.append(f"NOAA: {e}")
        if S.enabled(cfg, "iem") and (view.in_conus() or view.lat > 15):
            try:
                base = S.url(cfg, "iem")
                now = int(time.time()) // 300 * 300
                mins = sorted({min(50, round(k * view.step / 5) * 5) for k in range(n)}, reverse=True)

                def one(mm):
                    layer = "nexrad-n0q-900913" + (f"-m{mm:02d}m" if mm else "")
                    url = (f"{base}/cgi-bin/wms/nexrad/n0q.cgi?service=WMS&version=1.1.1&request=GetMap"
                           f"&layers={layer}&styles=&srs=EPSG:3857&bbox={view.bbox()}&width={view.w}"
                           f"&height={view.h}&format=image/png&transparent=true&_t={now}")
                    return now - mm * 60, self._cached_frame(url)

                frames = pmap(one, mins, 4)
                res.radar_src = "IEM NEXRAD composite"
                res.notes += errors
                return sorted(frames, key=lambda f: f[0])
            except ApiError as e:
                errors.append(f"IEM: {e}")
        if S.enabled(cfg, "rainviewer"):
            try:
                j = self.http.get_json(f"{S.url(cfg, 'rainviewer')}/public/weather-maps.json", ttl=110)
                host = j.get("host") or "https://tilecache.rainviewer.com"
                past = (j.get("radar") or {}).get("past") or []
                pick = self._pick_times([int(p["time"]) for p in past], n, view.step)
                paths = {int(p["time"]): p["path"] for p in past}
                frames = []
                for t in pick:
                    tpl = host + paths[t] + "/256/{z}/{x}/{y}/2/1_1.png"
                    frames.append((t, self._tiles(view, tpl, zmax=7, ttl=3600)))
                res.radar_src = "RainViewer"
                res.notes += errors
                return frames
            except (ApiError, KeyError, ValueError) as e:
                errors.append(f"RainViewer: {e}")
        res.notes += errors or ["every radar source is turned off"]
        return []

    # -- site radar (NEXRAD Level 3) ------------------------------------------------
    def sites(self, block=True):
        """Every WSR-88D (NWS API, cached for a week; the list rarely changes).

        With block=False (the drawing thread) it never waits: a load starts in the
        background and the list fills in on a later frame."""
        now = time.time()
        if self._sites and now - self._sites_t < 6 * 3600:
            return self._sites
        if now - self._sites_t < 300 and not self._sites:
            return self._sites          # a recent attempt failed: don't hammer the API
        if not block:
            if not self._sites_loading:
                self._sites_loading = True
                threading.Thread(target=self._load_sites, daemon=True).start()
            return self._sites
        self._load_sites()
        return self._sites

    def _load_sites(self):
        cfg = self.cfg()
        try:
            if S.enabled(cfg, "nws"):
                j = self.http.get_json(f"{S.url(cfg, 'nws')}/radar/stations?stationType=WSR-88D",
                                       nws_headers(cfg), ttl=7 * 86400, disk_ttl=30 * 86400)
                got = nexrad.parse_sites(j)
                if got:
                    self._sites = got
        except ApiError:
            pass
        finally:
            self._sites_t = time.time()
            self._sites_loading = False
            self._poke()

    def site_info(self, site_id):
        for st in self.sites(block=False):
            if st["id"] == site_id:
                return st
        return {"id": site_id, "name": site_id}

    def nearest_site(self, lat, lon, skip=0):
        near = nexrad.nearest_sites(self.sites(block=False), lat, lon, n=skip + 1)
        return near[skip] if len(near) > skip else None

    def _decoded(self, url, ttl, immutable):
        with self.l3_lock:
            hit = self.l3_cache.get(url)
            if hit is not None and (immutable or time.time() - hit[0] < ttl):
                self.l3_cache.move_to_end(url)
                return hit[1]
        raw = self.http.get_bytes(url, ttl=ttl, disk_ttl=2 * 86400 if immutable else 0, min_age=10)
        try:
            l3 = nexrad.decode(raw, url)
        except (nexrad.L3Error, struct.error, IndexError, ValueError, EOFError, OSError) as e:
            raise ApiError(0, f"couldn't read radar product {url.rsplit('/', 1)[-1]}: {e}")
        with self.l3_lock:
            old = self.l3_cache.pop(url, None)
            if old is not None:
                self.l3_bytes -= old[1].nbytes()
            self.l3_cache[url] = (time.time(), l3)
            self.l3_bytes += l3.nbytes()
            while len(self.l3_cache) > 1 and (self.l3_bytes > 160 * 1024 * 1024 or len(self.l3_cache) > 400):
                _u, gone = self.l3_cache.popitem(last=False)
                self.l3_bytes -= gone[1].nbytes()
        return l3

    def _layer(self, url, l3, view, prod, table):
        min_value = view.min_dbz if prod.palette == "ref" else None
        key = (url, view.geo_key(), prod.key, table.name, table.path, min_value, view.smooth, view.opacity,
               view.mode)
        with self.l3_lock:
            hit = self.layer_cache.get(key)
            if hit is not None:
                self.layer_cache.move_to_end(key)
                return hit
        lut = palettes.lut(l3, table, prod.kind, min_value=min_value, opacity=view.opacity / 100.0)
        img = nexrad.render(l3, view, lut, smooth=view.smooth and prod.smooth and view.mode == "pixels")
        with self.l3_lock:
            self.layer_cache[key] = img
            self.layer_bytes += img.size[0] * img.size[1] * 4
            while len(self.layer_cache) > 1 and (self.layer_bytes > 200 * 1024 * 1024 or len(self.layer_cache) > 160):
                _k, gone = self.layer_cache.popitem(last=False)
                self.layer_bytes -= gone.size[0] * gone.size[1] * 4
        return img

    def product_keys(self, cfg, site_id, prod, tilt, n):
        """[(time, url)] of the newest n scans of a product/tilt, oldest first.

        Tries each AWIPS ID listed for the tilt and uses the first one with recent scans."""
        ids = prod.tilts[max(0, min(tilt, len(prod.tilts) - 1))]
        errors = []
        if S.enabled(cfg, "nexrad_aws"):
            base = S.url(cfg, "nexrad_aws")
            since = time.time() - 3 * 3600
            for awips in ids:
                try:
                    keys = nexrad.list_keys(self.http, base, nexrad.site3(site_id), awips, since,
                                            ttl=self.site_refresh_s())
                except ApiError as e:
                    errors.append(f"AWS: {e}")
                    break
                out = {}
                for k in keys:
                    t = nexrad.key_time(k)
                    if t:
                        out[t] = f"{base}/{k}"
                if out:
                    return sorted(out.items())[-n:], errors
        return [], errors

    def _site_frames(self, view, cfg, res, limit=None):
        prod = nexrad.PRODUCT_BY_KEY.get(view.product) or nexrad.PRODUCTS[0]
        res.product = prod
        self.sites()
        res.site = self.site_info(view.site)
        table = palettes.load(prod.palette)
        res.table = table
        n = 1 if view.mini else (limit or view.frames)
        tilt = max(0, min(view.tilt, len(prod.tilts) - 1))
        picks, errors = self.product_keys(cfg, view.site, prod, tilt, n)
        immutable = True
        if not picks and S.enabled(cfg, "tgftp") and prod.tgftp:
            ds = prod.tgftp[min(tilt, len(prod.tgftp) - 1)]
            picks = [(0, nexrad.tgftp_url(view.site, ds, S.url(cfg, "tgftp")))]
            immutable = False
        if not picks:
            res.notes += errors or [f"no {prod.name.lower()} scans from {view.site} in the last 3 hours"]
            return []

        def one(item):
            t, url = item
            try:
                l3 = self._decoded(url, 60 if not immutable else 6 * 3600, immutable)
            except ApiError as e:
                return e
            if not l3.radials and l3.raster is None:
                return ApiError(0, f"{prod.name} from {view.site} has no data")
            return (l3.vol_time or t, self._layer(url, l3, view, prod, table), l3)

        got = pmap(one, picks, 4)
        ok = [g for g in got if not isinstance(g, Exception)]
        bad = [g for g in got if isinstance(g, Exception)]
        if not ok:
            raise bad[-1] if bad else ApiError(0, "no radar data")
        if bad:
            res.notes.append(f"{len(bad)} scan(s) couldn't be read: {bad[-1]}")
        ok.sort(key=lambda g: g[0])
        res.l3_frames = [g[2] for g in ok]
        last = ok[-1][2]
        res.scan = last.summary()
        el = res.scan.get("elevation")
        res.radar_src = f"NEXRAD {view.site} {prod.name}" + (f" {el:.1f}°" if el else "")
        if res.site.get("lat") is None:
            res.site = dict(res.site, lat=last.lat, lon=last.lon)
        return [(t, img) for t, img, _l in ok]

    def _storm_cells(self, view, cfg, res):
        """Storm-algorithm overlays from the radar: cell tracks, hail, mesocyclones, TVS."""
        site = view.site
        objs = {}
        if S.enabled(cfg, "tgftp"):
            base = S.url(cfg, "tgftp")

            def one(item):
                name, (_awips, ds) = item
                try:
                    return name, self._decoded(nexrad.tgftp_url(site, ds, base), 90, False)
                except ApiError:
                    return name, None

            for name, obj in pmap(one, list(nexrad.OVERLAYS.items()), 4):
                if obj is not None:
                    objs[name] = obj
        attrs = {}
        if S.enabled(cfg, "iem"):
            try:
                j = self.http.get_json(f"{S.url(cfg, 'iem')}/geojson/nexrad_attr.geojson", ttl=120, min_age=60)
                s3 = nexrad.site3(site)
                for f in j.get("features") or []:
                    p = f.get("properties") or {}
                    if p.get("nexrad") == s3 and p.get("storm_id"):
                        attrs[p["storm_id"]] = p
            except ApiError:
                pass
        sti = objs.get("sti")
        anchor = sti or next(iter(objs.values()), None)
        if anchor is None:
            return
        slat, slon = anchor.lat, anchor.lon

        def ll(x, y):
            return nexrad.from_xy(slat, slon, x, y)

        cells = []
        by_pos = {}
        if sti is not None:
            tracks_p = {(round(t[0][0], 2), round(t[0][1], 2)): t for t in sti.past if t}
            tracks_f = {(round(t[0][0], 2), round(t[0][1], 2)): t for t in sti.forecast if t}
            for st in sti.storms:
                pos = (round(st["x"], 2), round(st["y"], 2))
                lat, lon = ll(st["x"], st["y"])
                c = {"id": st["id"], "lat": lat, "lon": lon, "x": st["x"], "y": st["y"],
                     "past": [ll(x, y) for x, y in (tracks_p.get(pos) or [])[1:]],
                     "fcst": [ll(x, y) for x, y in (tracks_f.get(pos) or [])[1:]]}
                cells.append(c)
                by_pos[pos] = c
        for h in (objs.get("hail").hail if objs.get("hail") else []):
            if h["poh"] is None or h["poh"] < 0:
                continue
            pos = (round(h["x"], 2), round(h["y"], 2))
            c = by_pos.get(pos)
            if c is None:
                lat, lon = ll(h["x"], h["y"])
                c = {"id": "", "lat": lat, "lon": lon, "x": h["x"], "y": h["y"], "past": [], "fcst": []}
                cells.append(c)
                by_pos[pos] = c
            c["hail"] = {"poh": h["poh"], "posh": h["posh"], "size": h["size"]}
        meso = []
        for m in (objs.get("meso").meso if objs.get("meso") else []):
            lat, lon = ll(m["x"], m["y"])
            meso.append({"lat": lat, "lon": lon, "r_km": m.get("r") or 2.0, "kind": m.get("kind") or "meso"})
        tvs = []
        for t in (objs.get("tvs").tvs if objs.get("tvs") else []):
            lat, lon = ll(t["x"], t["y"])
            tvs.append({"lat": lat, "lon": lon, "kind": t["kind"]})
        for c in cells:
            a = attrs.get(c["id"])
            if a:
                c.update({"dbz": num(a.get("max_dbz")), "top": num(a.get("top")), "vil": num(a.get("vil")),
                          "drct": num(a.get("drct")), "sknt": num(a.get("sknt")),
                          "meso_rank": a.get("meso") if a.get("meso") not in (None, "NONE") else None,
                          "tvs_flag": a.get("tvs") if a.get("tvs") not in (None, "NONE") else None})
                if "hail" not in c and num(a.get("poh")) is not None:
                    c["hail"] = {"poh": num(a.get("poh")), "posh": num(a.get("posh")),
                                 "size": num(a.get("max_size")) or 0}
            self._cell_motion(c, anchor.vol_time)
            hlat, hlon = view.home
            c["dist"] = distance_mi(hlat, hlon, c["lat"], c["lon"])
            c["bearing"] = bearing_deg(hlat, hlon, c["lat"], c["lon"])
            c["eta"] = self._cell_eta(c, view.home, anchor.vol_time)
            px, py = view.to_px(c["lat"], c["lon"])
            c["in_view"] = 0 <= px < view.w and 0 <= py < view.h
        res.cells = sorted(cells, key=self._cell_rank)
        res.meso, res.tvs = meso, tvs

    @staticmethod
    def _cell_motion(c, t0):
        f = c.get("fcst") or []
        if f:
            d = distance_mi(c["lat"], c["lon"], f[0][0], f[0][1])
            c["speed_mph"] = d * 4.0 if d is not None else None       # forecast points are 15 minutes apart
            c["toward"] = bearing_deg(c["lat"], c["lon"], f[0][0], f[0][1])
        elif c.get("sknt"):
            c["speed_mph"] = c["sknt"] * 1.15078
            c["toward"] = (c["drct"] + 180.0) % 360.0 if c.get("drct") is not None else None

    @staticmethod
    def _cell_eta(c, home, t0):
        """When the cell's forecast track passes within 4 miles of home (epoch), or None."""
        pts = [(c["lat"], c["lon"])] + list(c.get("fcst") or [])
        if len(pts) < 2 or not t0:
            return None
        hlat, hlon = home
        best = None
        for k in range(len(pts) - 1):
            (a_lat, a_lon), (b_lat, b_lon) = pts[k], pts[k + 1]
            for step in range(0, 11):
                f = step / 10.0
                la, lo = a_lat + (b_lat - a_lat) * f, a_lon + (b_lon - a_lon) * f
                d = distance_mi(hlat, hlon, la, lo)
                if d is not None and (best is None or d < best[0]):
                    best = (d, (k + f) * 15 * 60)
        if best and best[0] <= 4.0:
            return {"t": t0 + best[1], "miss_mi": best[0]}
        return None

    @staticmethod
    def _cell_rank(c):
        """Cells on the map first, then the most threatening: TVS, mesocyclone, severe hail, strength."""
        h = c.get("hail") or {}
        return (0 if c.get("in_view") else 1, 0 if c.get("eta") else 1, 0 if c.get("tvs_flag") else 1,
                0 if c.get("meso_rank") else 1, -(h.get("posh") or 0), -(c.get("dbz") or 0), c.get("dist") or 9e9)

    def _cached_frame(self, url):
        with self.frame_lock:
            hit = self.frame_cache.get(url)
            if hit is not None:
                self.frame_cache.move_to_end(url)
                return hit
        img = self._img(url, ttl=3600)
        with self.frame_lock:
            if url not in self.frame_cache:
                self.frame_cache[url] = img
                self.frame_bytes += img.size[0] * img.size[1] * 4
            while self.frame_cache and (self.frame_bytes > 96 * 1024 * 1024 or len(self.frame_cache) > 80):
                _u, gone = self.frame_cache.popitem(last=False)
                self.frame_bytes -= gone.size[0] * gone.size[1] * 4
        return img

    def _satellite(self, view, cfg, res, times):
        if not S.enabled(cfg, "gibs"):
            return {}
        layer = "GOES-West_ABI_Band13_Clean_Infrared" if view.lon < -105 else "GOES-East_ABI_Band13_Clean_Infrared"
        base = S.url(cfg, "gibs")
        out = {}
        for t in times or [time.time()]:
            tt = int(t - 15 * 60) // 600 * 600
            stamp = time.strftime("%Y-%m-%dT%H:%M:00Z", time.gmtime(tt))
            url = (f"{base}/wms/epsg3857/best/wms.cgi?SERVICE=WMS&REQUEST=GetMap&VERSION=1.1.1&LAYERS={layer}"
                   f"&STYLES=&SRS=EPSG:3857&BBOX={view.bbox()}&WIDTH={view.w}&HEIGHT={view.h}"
                   f"&FORMAT=image/png&TRANSPARENT=true&TIME={stamp}")
            try:
                img = self._cached_frame(url)
            except ApiError as e:
                res.notes.append(f"satellite: {e}")
                break
            lum = img.convert("L")
            alpha = lum.point(lambda v: max(0, min(210, (v - 120) * 3)))
            cloud = img.copy()
            cloud.putalpha(gfx.ImageChops.multiply(alpha, img.getchannel("A")))
            out[t] = cloud
        if out:
            res.sat = True
        return out

    # -- overlays ---------------------------------------------------------------------
    def _places(self, view, cfg):
        lo0, la0, lo1, la1 = view.lonlat_bounds()
        key = (round(lo0, 2), round(la0, 2), round(lo1, 2), round(la1, 2))
        if key in self.places_cache:
            return self.places_cache[key]
        if not S.enabled(cfg, "tigerweb") or not (view.in_conus() or view.lat > 17):
            return []
        base = S.url(cfg, "tigerweb")
        q = urllib.parse.urlencode({
            "geometry": f"{view.x0:.0f},{view.y0:.0f},{view.x1:.0f},{view.y1:.0f}",
            "geometryType": "esriGeometryEnvelope", "inSR": 3857, "spatialRel": "esriSpatialRelIntersects",
            "outFields": "BASENAME,CENTLAT,CENTLON,AREALAND", "returnGeometry": "false",
            "orderByFields": "AREALAND DESC", "resultRecordCount": 150, "f": "json"})
        try:
            j = self.http.get_json(f"{base}/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer/28/query?{q}",
                                   ttl=86400 * 7, disk_ttl=86400 * 30)
        except ApiError:
            return []
        out = []
        for f in j.get("features") or []:
            a = f.get("attributes") or {}
            la, lo = num(a.get("CENTLAT")), num(a.get("CENTLON"))
            if la is None or lo is None:
                continue
            out.append((a.get("BASENAME") or "", la, lo, num(a.get("AREALAND")) or 0))
        self.places_cache[key] = out
        while len(self.places_cache) > 40:
            self.places_cache.popitem(last=False)
        return out

    def _metrics(self, view):
        """Approximate size of one label character in image pixels."""
        if view.mode == "pixels":
            fsize = 15 if view.w > 900 else 13 if view.w > 500 else 11
            return fsize * 0.62, fsize * 1.25
        if view.mode == "blocks":
            return 1.0, 2.0
        return 1.0, 1.0

    def _place_labels(self, view, cfg, boxes):
        places = self._places(view, cfg)
        if not places:
            return []
        cw, ch = self._metrics(view)
        if view.mode == "pixels":
            limit = 40 if not view.mini else 12
        else:
            limit = (16 if view.mode == "blocks" else 12) if not view.mini else 5
        out = []
        for name, la, lo, _area in places:
            px, py = view.to_px(la, lo)
            if not (0 <= px < view.w and 0 <= py < view.h):
                continue
            wbox = (len(name) + 1.5) * cw
            for anchor in ("l", "r", "t", "b"):
                if anchor == "l":
                    box = (px - 0.8 * cw, py - ch * 0.55, px + wbox, py + ch * 0.55)
                elif anchor == "r":
                    box = (px - wbox, py - ch * 0.55, px + 0.8 * cw, py + ch * 0.55)
                elif anchor == "t":
                    box = (px - wbox / 2, py - ch * 1.6, px + wbox / 2, py + ch * 0.3)
                else:
                    box = (px - wbox / 2, py - ch * 0.3, px + wbox / 2, py + ch * 1.6)
                if box[0] < 0 or box[2] > view.w or box[1] < 0 or box[3] > view.h:
                    continue
                if any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]) for b in boxes):
                    continue
                boxes.append(box)
                out.append((name, px, py, anchor))
                break
            if len(out) >= limit:
                break
        return out

    def _station_temps(self, view, snap, boxes):
        rows = (snap.get("iem") or {}).get("rows") or []
        cand = []
        now = time.time()
        for r in rows:
            t = num(r.get("tmpf"))
            la, lo = num(r.get("lat")), num(r.get("lon"))
            ts = iso_epoch(r.get("utc_valid"))
            if t is None or la is None or not ts or now - ts > 2 * 3600:
                continue
            px, py = view.to_px(la, lo)
            if not (2 <= px < view.w - 2 and 2 <= py < view.h - 2):
                continue
            pri = 0 if (r.get("network") or "").endswith(("ASOS", "AWOS")) else 1
            cand.append((pri, r.get("_dist") or 0, px, py, (t - 32) * 5 / 9, r.get("station")))
        for st in ((snap.get("stations") or {}).get("list") or []):
            o = st.get("obs") or {}
            if o.get("temp") is None or st.get("stale") or st.get("lat") is None:
                continue
            px, py = view.to_px(st["lat"], st["lon"])
            if 2 <= px < view.w - 2 and 2 <= py < view.h - 2:
                cand.append((2, st["dist"], px, py, o["temp"], st["id"]))
        cand.sort()
        cw, ch = self._metrics(view)
        limit = 28 if view.mode == "pixels" else 14 if view.mode == "blocks" else 10
        if view.mini:
            limit = max(3, limit // 3)
        out = []
        for _p, _d, px, py, tc, sid in cand:
            box = (px - 1.6 * cw, py - ch * 0.55, px + 1.6 * cw, py + ch * 0.55)
            if any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]) for b in boxes):
                continue
            boxes.append(box)
            out.append((px, py, tc, sid))
            if len(out) >= limit:
                break
        return out

    def _overlay(self, view, snap, res):
        img = gfx.Image.new("RGBA", (view.w, view.h), (0, 0, 0, 0))
        d = gfx.ImageDraw.Draw(img)
        pix = view.mode == "pixels"
        lw = 2 if pix and view.w > 500 else 1
        mx, my = view.to_px(*view.home)
        if not view.mini and pix:
            span = view.span_mi
            ring = next((r for r in (5, 10, 25, 50, 100, 200, 400) if r >= span / 6), 400)
            for k in (1, 2, 3):
                rad = ring * k / view.mi_per_px
                if rad > max(view.w, view.h) * 1.5:
                    break
                d.ellipse([mx - rad, my - rad / view.yscale, mx + rad, my + rad / view.yscale],
                          outline=(150, 175, 255, 60), width=1)
        if view.warnings:
            for wx in (snap.get("watches") or []):
                for ring in wx["rings"]:
                    pts = [view.to_px(p[1], p[0]) for p in ring]
                    if len(pts) > 2:
                        _dashed(d, pts + [pts[0]], wx["rgb"] + (210,), lw, 10 if pix else 3)
            order = {"TO": 9, "EW": 8, "SV": 7, "FF": 6, "SQ": 5, "MA": 4, "DS": 3}
            for w in sorted(storm_warnings(snap), key=lambda w: order.get(w.get("phen"), 0)):
                rgb, fill_a, width, tag = warning_style(w)
                for ring in w["rings"]:
                    pts = [view.to_px(p[1], p[0]) for p in ring]
                    if len(pts) < 3:
                        continue
                    if pix:
                        d.polygon(pts, fill=rgb + (fill_a,))
                        d.line(pts + [pts[0]], fill=(0, 0, 0, 170), width=lw + width + 2, joint="curve")
                    d.line(pts + [pts[0]], fill=rgb + (255,), width=lw + (width if pix else 0), joint="curve")
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    res.warn_boxes.append((w["name"] + (f" {tag}" if tag else ""), rgb, sum(xs) / len(xs), min(ys)))
            for a in (snap.get("area_alerts") or []):
                mo = a.get("motion")
                if not mo or mo.get("lat") is None or not pix:
                    continue
                self._motion_arrow(d, view, mo, a.get("rgb") or (255, 255, 255))
        rings = (snap.get("zcta") or {}).get("rings") or []
        allpts = [view.to_px(p[1], p[0]) for ring in rings for p in ring]
        if allpts:
            xs, ys = [p[0] for p in allpts], [p[1] for p in allpts]
            big_enough = max(xs) - min(xs) >= (10 if pix else 7) or max(ys) - min(ys) >= (10 if pix else 7)
            if big_enough:
                for ring in rings:
                    pts = [view.to_px(p[1], p[0]) for p in ring]
                    if len(pts) > 2:
                        if pix:
                            d.line(pts + [pts[0]], fill=(0, 0, 0, 150), width=lw + 2)
                        d.line(pts + [pts[0]], fill=(255, 214, 0, 255), width=lw)
        if "range" in view.layers and view.source == "site":
            self._draw_range(d, view, res, pix)
        if "sites" in view.layers:
            self._draw_sites(d, view, res, pix)
        self._draw_cells(d, view, res, pix)
        if "markers" in view.layers:
            self._draw_markers(d, view, snap, res, pix)
        if pix:
            self._draw_labels(d, view, res)
        if "placefiles" in view.layers and not view.mini:   # on top: it's what the user asked to see
            placefile.draw_all(img, d, view, snap, res, pix, self.http)
        return img

    def _draw_range(self, d, view, res, pix):
        """Range rings every 50 km around the radar, and its edge for this product."""
        l3 = res.l3_frames[-1] if res.l3_frames else None
        if l3 is None or not pix:
            return
        sx, sy = view.to_px(l3.lat, l3.lon)
        km_px = 1.0 / (view.mi_per_px * 1.609344)
        edge = l3.nbins * l3.bin_km if l3.radials else 230.0
        small = gfx.font(11 if view.w > 900 else 10, bold=True)
        for k in range(50, int(edge) + 1, 50):
            r = k * km_px
            if r < 8:
                continue
            _dashed(d, [(sx + r * math.cos(a / 36.0 * math.pi), sy + r * math.sin(a / 36.0 * math.pi) / view.yscale)
                        for a in range(73)], (200, 210, 255, 90), 1, 6)
            lx, ly = sx, sy - r / view.yscale
            if 0 <= lx < view.w and 0 <= ly < view.h:
                gfx.draw_text(d, (lx, ly - 2), f"{k} km" if not view.imperial else f"{k / 1.609344:.0f} mi", small,
                              (210, 220, 255, 200), anchor="mb", stroke=2, stroke_fill=(0, 0, 30, 255))
        r = edge * km_px
        d.ellipse([sx - r, sy - r / view.yscale, sx + r, sy + r / view.yscale], outline=(255, 255, 255, 120), width=1)

    def _draw_sites(self, d, view, res, pix):
        """Every WSR-88D on the map (click one to switch to it)."""
        small = gfx.font(11 if view.w > 900 else 10, bold=True) if pix else None
        res.site_marks = []
        for st in self.sites():
            px, py = view.to_px(st["lat"], st["lon"])
            if not (0 <= px < view.w and 0 <= py < view.h):
                continue
            on = st["id"] == view.site
            res.site_marks.append((st["id"], px, py))
            if pix:
                col = (255, 214, 0, 255) if on else (120, 230, 255, 235)
                d.ellipse([px - 6, py - 6, px + 6, py + 6], outline=(0, 0, 0, 200), width=4)
                d.ellipse([px - 6, py - 6, px + 6, py + 6], outline=col, width=2)
                d.ellipse([px - 2, py - 2, px + 2, py + 2], fill=col)
                gfx.draw_text(d, (px, py + 8), st["id"], small, col, anchor="mt", stroke=2,
                              stroke_fill=(0, 0, 30, 255))
            else:
                res.marks.append((px, py, "◉" if on else "◎", (255, 214, 0) if on else (120, 230, 255), st["id"]))

    def _draw_markers(self, d, view, snap, res, pix):
        small = gfx.font(12 if view.w > 900 else 10, bold=True) if pix else None
        for m in snap.get("markers") or []:
            px, py = view.to_px(m["lat"], m["lon"])
            if not (-10 <= px < view.w + 10 and -10 <= py < view.h + 10):
                continue
            if pix:
                d.polygon([(px, py), (px - 6, py - 12), (px + 6, py - 12)], fill=(0, 0, 0, 140))
                d.ellipse([px - 7, py - 20, px + 7, py - 6], fill=(240, 60, 120, 255), outline=(255, 255, 255, 255),
                          width=2)
                d.polygon([(px, py), (px - 5, py - 10), (px + 5, py - 10)], fill=(240, 60, 120, 255))
                d.ellipse([px - 2.5, py - 15.5, px + 2.5, py - 10.5], fill=(255, 255, 255, 255))
                gfx.draw_text(d, (px + 9, py - 13), m["name"], small, (255, 225, 240, 255), anchor="lm", stroke=2,
                              stroke_fill=(40, 0, 20, 255))
            else:
                res.marks.append((px, py, "⚑", (255, 120, 170), m["name"][:10]))

    def _motion_arrow(self, d, view, mo, rgb):
        px, py = view.to_px(mo["lat"], mo["lon"])
        if not (0 <= px < view.w and 0 <= py < view.h):
            return
        mph = (mo.get("kt") or 0) * 1.15078
        if mph < 3 or mo.get("toward") is None:
            d.ellipse([px - 4, py - 4, px + 4, py + 4], outline=(255, 255, 255, 230), width=2)
            return
        dist_mi = mph * 0.25           # 15 minutes of travel
        ang = math.radians(mo["toward"])
        ex = px + math.sin(ang) * dist_mi / view.mi_per_px
        ey = py - math.cos(ang) * dist_mi / view.mi_per_px / view.yscale
        d.line([(px, py), (ex, ey)], fill=(0, 0, 0, 180), width=5)
        d.line([(px, py), (ex, ey)], fill=(255, 255, 255, 240), width=2)
        for side in (-1, 1):
            a2 = ang + math.pi + side * 0.45
            d.line([(ex, ey), (ex + math.sin(a2) * 9, ey - math.cos(a2) * 9)], fill=(255, 255, 255, 240), width=2)
        d.ellipse([px - 3, py - 3, px + 3, py + 3], fill=rgb + (255,), outline=(255, 255, 255, 255))

    def _draw_cells(self, d, view, res, pix):
        """Storm tracks and algorithm symbols (site radar)."""
        if view.source != "site" or not view.cells:
            return
        w, h = view.w, view.h

        def inside(px, py, pad=0):
            return -pad <= px < w + pad and -pad <= py < h + pad

        small = gfx.font(11 if view.w > 900 else 10, bold=True) if pix else None
        for c in res.cells:
            px, py = view.to_px(c["lat"], c["lon"])
            if not inside(px, py, 40):
                continue
            past = [view.to_px(la, lo) for la, lo in c.get("past") or []]
            fc = [view.to_px(la, lo) for la, lo in c.get("fcst") or []]
            if pix:
                if past:
                    d.line([(px, py)] + past, fill=(200, 200, 225, 150), width=1)
                    for qx, qy in past:
                        d.ellipse([qx - 2, qy - 2, qx + 2, qy + 2], fill=(200, 200, 225, 170))
                if fc:
                    d.line([(px, py)] + fc, fill=(0, 0, 0, 160), width=4)
                    d.line([(px, py)] + fc, fill=(255, 255, 255, 235), width=2)
                    for qx, qy in fc:
                        d.ellipse([qx - 3, qy - 3, qx + 3, qy + 3], fill=(255, 255, 255, 235), outline=(0, 0, 0, 200))
                hail = c.get("hail") or {}
                if (hail.get("posh") or 0) >= 50 or (hail.get("poh") or 0) >= 50:
                    severe = (hail.get("posh") or 0) >= 50
                    col = (255, 230, 0, 255) if severe else (60, 230, 90, 255)
                    tri = [(px, py - 15), (px - 7, py - 4), (px + 7, py - 4)]
                    d.polygon(tri, fill=col if severe else None, outline=(0, 0, 0, 255) if severe else col)
                    if severe and hail.get("size"):
                        gfx.draw_text(d, (px + 9, py - 12), f'{hail["size"]:.2f}"'.replace("0.", "."), small,
                                      (255, 240, 120, 255), anchor="lm", stroke=2, stroke_fill=(0, 0, 0, 255))
                d.ellipse([px - 4, py - 4, px + 4, py + 4], fill=(30, 30, 40, 220), outline=(255, 255, 255, 255),
                          width=2)
                if c.get("id"):
                    gfx.draw_text(d, (px + 6, py + 5), c["id"], small, (235, 240, 255, 255), anchor="lt",
                                  stroke=2, stroke_fill=(0, 0, 0, 255))
            else:
                hail = c.get("hail") or {}
                ch, col = "+", (235, 240, 255)
                if (hail.get("posh") or 0) >= 50:
                    ch, col = "▲", (255, 230, 0)
                res.marks.append((px, py, ch, col, c.get("id") or ""))
        for m in getattr(res, "meso", []):
            px, py = view.to_px(m["lat"], m["lon"])
            if not inside(px, py, 20):
                continue
            weak = "weak" in m["kind"]
            if pix:
                r = max(6.0, (m["r_km"] or 2.0) / 1.609344 / view.mi_per_px)
                d.ellipse([px - r, py - r, px + r, py + r], outline=(0, 0, 0, 170), width=4 if not weak else 3)
                d.ellipse([px - r, py - r, px + r, py + r], outline=(255, 225, 0, 255 if not weak else 170),
                          width=2 if not weak else 1)
            else:
                res.marks.append((px, py, "◎" if not weak else "o", (255, 225, 0), ""))
        for t in getattr(res, "tvs", []):
            px, py = view.to_px(t["lat"], t["lon"])
            if not inside(px, py, 20):
                continue
            if pix:
                tri = [(px - 8, py - 6), (px + 8, py - 6), (px, py + 9)]
                if t["kind"] == "TVS":
                    d.polygon(tri, fill=(255, 20, 30, 255), outline=(0, 0, 0, 255))
                else:
                    d.polygon(tri, outline=(255, 40, 40, 255))
            else:
                res.marks.append((px, py, "▼", (255, 40, 40), t["kind"]))

    def _draw_labels(self, d, view, res):
        fsize = 15 if view.w > 900 else 13 if view.w > 500 else 11
        fnt = gfx.font(fsize, bold=True)
        small = gfx.font(max(9, fsize - 2), bold=True)
        for name, px, py, anchor in res.labels:
            d.ellipse([px - 3, py - 3, px + 3, py + 3], fill=(255, 255, 255, 255), outline=(0, 0, 40, 255))
            pos, anc = {"l": ((px + 6, py), "lm"), "r": ((px - 6, py), "rm"),
                        "t": ((px, py - 5), "mb"), "b": ((px, py + 5), "mt")}[anchor]
            gfx.draw_text(d, pos, name, fnt, (255, 255, 255, 255), anchor=anc, stroke=2,
                          stroke_fill=(5, 10, 40, 255))
        for px, py, tc, _sid in res.temps:
            txt = view.temp_text(tc)
            col = (255, 236, 120) if tc >= 22.2 else (150, 220, 255) if tc < 4.4 else (255, 255, 255)
            gfx.draw_text(d, (px, py), txt, small, col + (255,), anchor="mm", stroke=2, stroke_fill=(30, 10, 60, 255))
        mx, my = view.to_px(*view.home)
        if -20 <= mx < view.w + 20 and -20 <= my < view.h + 20:
            r = 8 if view.w > 500 else 6
            diamond = [(mx, my - r), (mx + r, my), (mx, my + r), (mx - r, my)]
            d.polygon([(x + 1, y + 2) for x, y in diamond], fill=(0, 0, 0, 120))
            d.polygon(diamond, fill=(255, 140, 0, 255), outline=(40, 15, 0, 255))
            d.ellipse([mx - 2.5, my - 2.5, mx + 2.5, my + 2.5], fill=(255, 255, 255, 255))

    def _stamp_time(self, img, t, view, res=None):
        """A small translucent card in the corner: radar, product, tilt and scan time."""
        if view.mini:
            return
        d = gfx.ImageDraw.Draw(img, "RGBA")
        big = gfx.font(16 if view.w > 900 else 12, bold=True)
        small = gfx.font(13 if view.w > 900 else 10, bold=True)
        when = time.strftime("%I:%M %p", time.localtime(t)).lstrip("0")
        if res is not None and view.source == "site" and res.product is not None:
            el = (res.scan or {}).get("elevation")
            title = f"{view.site}  {res.product.name}" + (f"  {el:.1f}°" if el else "")
        else:
            title = "NOAA MRMS mosaic" if "MRMS" in (getattr(res, "radar_src", "") or "") else \
                (getattr(res, "radar_src", "") or "Radar")
        tw = max(gfx.text_width(d, title, small), gfx.text_width(d, when, big))
        lh1 = (small.size if hasattr(small, "size") else 12) + 4
        lh2 = (big.size if hasattr(big, "size") else 16) + 2
        box = [8, 8, 8 + tw + 20, 8 + lh1 + lh2 + 12]
        if hasattr(d, "rounded_rectangle"):
            d.rounded_rectangle(box, radius=7, fill=(6, 12, 46, 200), outline=(120, 150, 255, 120), width=1)
        else:
            d.rectangle(box, fill=(6, 12, 46, 200))
        gfx.draw_text(d, (18, 14), title, small, (205, 220, 255, 255), anchor="la")
        gfx.draw_text(d, (18, 14 + lh1), when, big, (255, 214, 0, 255), anchor="la")

    # -- nowcast ----------------------------------------------------------------------
    def _analyze(self, view, frames, snap):
        """Nowcast from mosaic frames, reading reflectivity back from the colours."""
        valid = [(t, img) for t, img in frames if img is not None]
        if not valid:
            return {}

        def wet_map(img):
            m, f = dbz_map(img)
            return _despeckle(m.point(lambda v: v if v >= 18 else 0)), f

        t_last, last = valid[-1]
        dmap, f = wet_map(last)
        old = None
        if len(valid) >= 3:
            t_old, img_old = valid[max(0, len(valid) - 4)]
            old = (t_old, wet_map(img_old)[0])
        return self._nowcast(view, t_last, dmap, old, f, len(valid))

    def _analyze_l3(self, view, cfg, res):
        """Nowcast straight from the decoded scans (true dBZ, not colours), whatever product
        is on screen: it always reads base reflectivity (0.5 degrees).

        Only echo that the radar's own dual-pol rain-rate product (DPR, from the same volume
        scan) calls precipitation counts, so the 'bloom' of birds, insects, dust and clutter
        around a radar on a clear night is never reported as rain. DPR comes out a few
        minutes after the volume's first tilt, so this uses the newest volume that has both."""
        ref = nexrad.PRODUCT_BY_KEY["ref"]
        try:
            refs, _e = self.product_keys(cfg, view.site, ref, 0, 30)
        except ApiError:
            refs = []
        rate = nexrad.PRODUCT_BY_KEY.get("rate")
        try:
            dprs = self.product_keys(cfg, view.site, rate, 0, 30)[0] if rate else []
        except ApiError:
            dprs = []

        def dpr_near(t):
            m = min(dprs, key=lambda p: abs(p[0] - t), default=None)
            return m[1] if m is not None and abs(m[0] - t) <= 300 else None

        chosen = []   # [(time, ref url or Level3, dpr url)] newest first
        if refs:
            new = next(((t, u, dpr_near(t)) for t, u in reversed(refs) if dpr_near(t)), None)
            if new is None or refs[-1][0] - new[0] > 900:
                new = (refs[-1][0], refs[-1][1], None)   # no rain-rate product: go by reflectivity alone
            chosen.append(new)
            older = [(t, u) for t, u in refs if 600 <= new[0] - t <= 1800]
            if older:
                t, u = min(older, key=lambda p: abs(new[0] - p[0] - 1200))
                chosen.append((t, u, dpr_near(t)))
        elif view.product in ("ref", "cr") and res.l3_frames:
            frames = res.l3_frames   # no listing (e.g. the AWS source is off): the scans on screen
            chosen.append((frames[-1].vol_time or time.time(), frames[-1], None))
            if len(frames) >= 3:
                old = frames[max(0, len(frames) - 4)]
                chosen.append((old.vol_time or 0, old, None))
        if not chosen:
            return {}
        ckey = (view.geo_key(), view.site, (round(view.home[0], 4), round(view.home[1], 4)),
                tuple((t, bool(d)) for t, _u, d in chosen))
        with self.lock:
            hit = self.analysis_cache.get(ckey)
        if hit is not None:
            return hit
        scans = []
        for t, u, d in chosen:
            try:
                l3 = u if isinstance(u, nexrad.Level3) else self._decoded(u, 6 * 3600, True)
            except ApiError:
                if not scans:
                    return {}
                break
            try:
                dl3 = self._decoded(d, 6 * 3600, True) if d else None
            except ApiError:
                dl3 = None
            scans.append((t, l3, dl3))
        slat, slon = scans[0][1].lat, scans[0][1].lon
        clear_air = (scans[0][1].summary().get("mode") or "") == "clear air"
        f = min(1.0, 160.0 / max(view.w, view.h, 1))
        sw, sh = max(1, int(view.w * f)), max(1, int(view.h * f))
        pts = [nexrad.to_xy(slat, slon, *view.from_px((x + 0.5) / f, (y + 0.5) / f))
               for y in range(sh) for x in range(sw)]
        maps = []
        rejected = strong = 0
        for n, (t, l3, d) in enumerate(scans):
            floor = 25 if clear_air else 18   # without the dual-pol check
            vals = bytearray(sw * sh)
            for i, (xk, yk) in enumerate(pts):
                v = l3.value_at(xk, yk)[1]
                if v is None or v < 5:
                    continue
                if d is not None:
                    lev = d.value_at(xk, yk)[0]
                    if lev is not None:          # inside the rain-rate product's range
                        if n == 0 and v >= 18:
                            strong += 1
                            rejected += lev == 0
                        if lev > 0:              # the radar says: precipitation
                            vals[i] = int(max(15, min(80, v)))
                        continue
                if v >= floor:
                    vals[i] = int(min(80, v))
            maps.append(_despeckle(gfx.Image.frombytes("L", (sw, sh), bytes(vals))))
        old = (scans[1][0], maps[1]) if len(maps) > 1 else None
        out = self._nowcast(view, scans[0][0], maps[0], old, f, len(scans))
        out["checked"] = scans[0][2] is not None
        if strong >= 12 and rejected >= strong * 0.5:
            out["nonmet"] = rejected / max(1, strong)
        with self.lock:
            if len(self.analysis_cache) > 40:
                self.analysis_cache.clear()
            self.analysis_cache[ckey] = out
        return out

    def _nowcast(self, view, t_last, dmap, old, f, nframes):
        """Nearest / heaviest precipitation, motion and arrival from small maps of dBZ
        (0 where it's dry)."""
        sw, sh = dmap.size
        mpp = view.mi_per_px / f
        hx, hy = view.to_px(*view.home)
        cx, cy = hx * f, hy * f
        px = dmap.load()
        ys = view.yscale
        best_near, best_heavy = None, None
        total = wet = 0
        for y in range(sh):
            for x in range(sw):
                v = px[x, y]
                total += 1
                if not v:
                    continue
                wet += 1
                dist = math.hypot((x - cx), (y - cy) * ys) * mpp
                if best_near is None or dist < best_near[0]:
                    best_near = (dist, x, y, v)
                if best_heavy is None or v > best_heavy[3] or (v == best_heavy[3] and dist < best_heavy[0]):
                    best_heavy = (dist, x, y, v)
        out = {"time": t_last, "coverage": wet / max(1, total), "frames": nframes}

        def where(entry):
            dist, x, y, v = entry
            lat, lon = view.from_px(x / f, y / f)
            return {"dist": dist, "bearing": bearing_deg(view.home[0], view.home[1], lat, lon), "dbz": v,
                    "word": intensity_word(v)}

        if best_near:
            out["nearest"] = where(best_near)
            out["overhead"] = best_near[0] <= max(2.0, mpp * 1.5)
        if best_heavy:
            out["heaviest"] = where(best_heavy)
        if old is not None and wet >= 12:
            t_old, omap = old
            dt_h = (t_last - t_old) / 3600.0
            if dt_h > 0.05:
                motion = self._motion(omap, dmap, dt_h, mpp, ys)
                if motion:
                    out["motion"] = motion
                    eta = self._eta(dmap, cx, cy, motion, mpp, ys)
                    if eta:
                        out["eta"] = eta
        return out

    @staticmethod
    def _motion(a, b, dt_h, mpp, ys=1.0):
        """Storm motion: the shift that best lines up two small dBZ maps."""
        a = a.point(lambda v: 255 if v else 0)
        b = b.point(lambda v: 255 if v else 0)
        w, h = b.size
        maxs = int(min(w, h) * 0.25)
        maxs = max(2, min(maxs, int(80 * dt_h / max(mpp, 0.01)) + 1))
        ImageStat = gfx.ImageStat
        best = None
        step = 2 if maxs > 12 else 1
        for dy in range(-maxs, maxs + 1, step):
            for dx in range(-maxs, maxs + 1, step):
                sh = gfx.Image.new("L", (w, h), 0)
                sh.paste(a, (dx, dy))
                x0, y0 = max(0, dx), max(0, dy)
                x1, y1 = min(w, w + dx), min(h, h + dy)
                if x1 - x0 < w * 0.5 or y1 - y0 < h * 0.5:
                    continue
                box = (x0, y0, x1, y1)
                diff = gfx.ImageChops.difference(sh.crop(box), b.crop(box))
                score = ImageStat.Stat(diff).mean[0]
                if best is None or score < best[0]:
                    best = (score, dx, dy)
        if best is None:
            return None
        if step == 2:
            _s, bx, by = best
            for dy in range(by - 1, by + 2):
                for dx in range(bx - 1, bx + 2):
                    sh = gfx.Image.new("L", (w, h), 0)
                    sh.paste(a, (dx, dy))
                    x0, y0 = max(0, dx), max(0, dy)
                    x1, y1 = min(w, w + dx), min(h, h + dy)
                    box = (x0, y0, x1, y1)
                    score = ImageStat.Stat(gfx.ImageChops.difference(sh.crop(box), b.crop(box))).mean[0]
                    if score < best[0]:
                        best = (score, dx, dy)
        _score, dx, dy = best
        speed = math.hypot(dx, dy * ys) * mpp / dt_h   # rows can be taller than columns are wide
        if speed < 3:
            return {"speed": 0.0, "toward": None, "dx": 0, "dy": 0, "stationary": True}
        if speed > 90:
            return None
        toward = (math.degrees(math.atan2(dx, -dy * ys)) + 360) % 360
        return {"speed": speed, "toward": toward, "dx": dx / dt_h, "dy": dy / dt_h, "stationary": False}

    @staticmethod
    def _eta(dmap, cx, cy, motion, mpp, ys):
        if motion.get("stationary") or not motion.get("speed"):
            return None
        px = dmap.load()
        w, h = dmap.size
        vx, vy = motion["dx"], motion["dy"]   # pixels per hour (small map)
        norm = math.hypot(vx, vy) or 1
        ux, uy = -vx / norm, -vy / norm       # look upstream
        steps = int(min(motion["speed"] * 2.0 / mpp, max(w, h)))
        for k in range(1, steps):
            x, y = cx + ux * k, cy + uy * k
            hit = False
            for ox, oy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
                xi, yi = int(x + ox), int(y + oy)
                if 0 <= xi < w and 0 <= yi < h and px[xi, yi]:
                    hit = px[xi, yi]
                    break
            if hit:
                dist = math.hypot(ux * k, uy * k * ys) * mpp
                return {"minutes": dist / motion["speed"] * 60, "dist": dist, "dbz": hit,
                        "word": intensity_word(hit)}
            if not (0 <= x < w and 0 <= y < h):
                break
        return None


WARNING_COLORS = {"TO": (255, 25, 25), "SV": (255, 214, 0), "FF": (30, 225, 70), "FA": (0, 160, 60),
                  "FL": (0, 160, 60), "MA": (255, 150, 0), "SQ": (190, 120, 255), "EW": (255, 100, 160),
                  "DS": (230, 200, 160)}


SBW_PHEN = ("TO", "EW", "SV", "FF", "SQ", "MA", "DS")


def storm_warnings(snap):
    """Storm-based warning polygons to draw: the IEM feed (whole country), plus any
    warning the NWS feed has already issued near home that IEM hasn't picked up yet."""
    sbw = list(snap.get("sbw") or [])
    extra = []
    for a in snap.get("area_alerts") or []:
        if a.get("src") != "NWS" or a.get("sig") != "W" or a.get("phen") not in SBW_PHEN or not a.get("rings"):
            continue
        if any(w.get("phen") == a["phen"] and rings_intersect(w.get("rings") or [], a["rings"]) for w in sbw):
            continue
        tags = a.get("tags") or {}
        extra.append({"phen": a["phen"], "name": a.get("event") or "Warning", "rings": a["rings"],
                      "tornado": tags.get("tornado"), "damage": tags.get("damage"),
                      "emergency": a.get("emergency"), "pds": a.get("pds")})
    return sbw + extra


def warning_style(w):
    """(rgb, fill alpha, extra line width, tag) for a storm-based warning, by impact."""
    phen = w.get("phen") or ""
    rgb = w.get("rgb") or WARNING_COLORS.get(phen, (255, 255, 255))
    tag, extra, fill = "", 0, 40
    tor = str(w.get("tornado") or "").upper()
    dmg = str(w.get("damage") or "").upper()
    if phen == "TO":
        if w.get("emergency"):
            rgb, tag, extra, fill = (230, 40, 255), "EMERGENCY", 3, 90
        elif w.get("pds") or dmg == "CONSIDERABLE":
            rgb, tag, extra, fill = (255, 40, 140), "PDS", 2, 70
        elif "OBSERVED" in tor:
            tag, extra, fill = "OBSERVED", 1, 60
    elif phen == "SV":
        if "POSSIBLE" in tor:
            rgb, tag = (255, 140, 0), "tornado possible"
        if dmg == "DESTRUCTIVE":
            rgb, tag, extra, fill = (255, 90, 0), "destructive", 1, 55
    elif phen == "FF" and w.get("emergency"):
        rgb, tag, extra, fill = (255, 60, 200), "EMERGENCY", 2, 70
    return rgb, fill, extra, tag


def _dashed(d, pts, fill, width, dash):
    """A dashed polyline (Pillow has no dash style)."""
    on = True
    left = dash
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        pos = 0.0
        while seg > 0 and pos < seg:
            step = min(left, seg - pos)
            if on:
                f0, f1 = pos / seg, (pos + step) / seg
                d.line([(x0 + (x1 - x0) * f0, y0 + (y1 - y0) * f0), (x0 + (x1 - x0) * f1, y0 + (y1 - y0) * f1)],
                       fill=fill, width=width)
            pos += step
            left -= step
            if left <= 0:
                on = not on
                left = dash


def summary_lines(res, F, compact=False):
    """Plain-language nowcast from a Result: list of (text, style)."""
    a = res.analysis if res else {}
    if not a:
        return []
    out = []
    if a.get("overhead"):
        out.append((f"Precipitation over you now ({a['nearest']['word']}).", "rain"))
    elif a.get("nearest"):
        n = a["nearest"]
        out.append((f"Nearest precipitation: {n['word']}, {F.dist(n['dist'])} {_dir8(n['bearing'])}.", "text"))
    else:
        out.append(("No precipitation on the radar here.", "good"))
    if a.get("nonmet") and not compact:
        out.append(("Some echo is birds, insects or dust, not rain (dual-pol check).", "dim"))
    mo = a.get("motion")
    if mo and not mo.get("stationary"):
        out.append((f"Moving {_dir8(mo['toward'])} at about {F.speed_mph(mo['speed'])}.", "text"))
    elif mo:
        out.append(("Little movement.", "dim"))
    eta = a.get("eta")
    if eta and not a.get("overhead"):
        mins = eta["minutes"]
        when = "within 10 minutes" if mins < 10 else f"in about {int(round(mins / 5.0) * 5)} minutes" \
            if mins < 90 else f"in about {mins / 60:.1f} hours"
        out.append((f"Arrival estimate: {eta['word']} {when}.", "accent"))
    elif mo and not mo.get("stationary") and a.get("nearest") and not a.get("overhead"):
        out.append(("Not headed your way right now.", "good"))
    hv = a.get("heaviest")
    if hv and not compact and hv["dbz"] >= 40:
        out.append((f"Heaviest: {hv['word']} ({hv['dbz']:.0f} dBZ) {F.dist(hv['dist'])} {_dir8(hv['bearing'])}.",
                    "warn" if hv["dbz"] < 55 else "bad"))
    return out


def _dir8(b):
    if b is None:
        return ""
    return ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"][int((b % 360) / 45 + 0.5) % 8]


LEGEND = [(5, (100, 120, 140)), (15, (80, 150, 200)), (22, (20, 210, 30)), (30, (10, 150, 15)),
          (38, (255, 220, 0)), (45, (255, 140, 0)), (52, (230, 0, 0)), (60, (150, 0, 0)), (66, (230, 60, 230)),
          (72, (240, 240, 255))]
