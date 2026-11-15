"""NEXRAD single-site radar from NOAA's open Level 3 feeds, decoded in pure Python.

Level 3 products are the small files each WSR-88D's product generator writes
after every elevation scan: reflectivity and velocity at several tilts, the
dual-polarization fields, echo tops, VIL, rainfall, and the storm algorithms
(cell tracks, hail, mesocyclones, tornado vortex signatures).  This module reads
them (NEXRAD ICD 2620001 formats), looks up values, and projects them onto the
skydrop map.

Sources (both free, no key):
  * NOAA Open Data on AWS, bucket unidata-nexrad-level3: every product with
    history, so loops work.  Keys look like LZK_N0B_2026_10_31_00_19_34.
  * NWS tgftp (tgftp.nws.noaa.gov): the latest copy of each product; used for
    the storm overlays (hail and TVS aren't in the bucket) and as a backup.
"""

import bisect
import bz2
import calendar
import math
import re
import struct
import time

from . import gfx

EARTH_KM = 6371.0
BEAM_EARTH_KM = EARTH_KM * 4.0 / 3.0   # standard 4/3-earth refraction model


class L3Error(ValueError):
    pass


# ----------------------------------------------------------------------------
# product catalog
# ----------------------------------------------------------------------------

class Product:
    """A product family the radar screen can show.

    tilts: one entry per elevation slot, lowest first (0.5, 0.9, 1.3, 1.8, 2.4, 3.1
    degrees in most scan strategies); each entry lists AWIPS IDs to try in order,
    because some slots use the super-resolution product and others the regular one."""

    def __init__(self, key, name, short, tilts, palette, kind="value", smooth=True, tgftp=None):
        self.key, self.name, self.short = key, name, short
        self.tilts, self.palette, self.kind = tilts, palette, kind
        self.smooth, self.tgftp = smooth, tgftp or []


def _six(letter, alt=None):
    slots = ["0", "A", "1", "B", "2", "3"]
    out = []
    for slot in slots:
        ids = [f"N{slot}{letter}"]
        if alt:
            ids.append(f"N{slot}{alt}")
        out.append(ids)
    return out


PRODUCTS = [
    Product("ref", "Reflectivity", "Ref", _six("B", "Q"), "ref",
            tgftp=["DS.p94r0", "DS.p94ra", "DS.p94r1", "DS.p94rb", "DS.p94r2", "DS.p94r3"]),
    Product("vel", "Velocity", "Vel", _six("G", "U"), "vel",
            tgftp=["DS.p99v0", "DS.p99va", "DS.p99v1", "DS.p99vb", "DS.p99v2", "DS.p99v3"]),
    Product("srv", "Storm-relative velocity", "SRV", [["N0S"], ["N1S"], ["N2S"], ["N3S"]], "vel",
            tgftp=["DS.56rm0", "DS.56rm1", "DS.56rm2", "DS.56rm3"]),
    Product("cc", "Correlation coefficient", "CC", _six("C"), "cc",
            tgftp=["DS.161c0", "DS.161ca", "DS.161c1", "DS.161cb", "DS.161c2", "DS.161c3"]),
    Product("zdr", "Differential reflectivity", "ZDR", _six("X"), "zdr",
            tgftp=["DS.159x0", "DS.159xa", "DS.159x1", "DS.159xb", "DS.159x2", "DS.159x3"]),
    Product("kdp", "Specific differential phase", "KDP", _six("K"), "kdp",
            tgftp=["DS.163k0", "DS.163ka", "DS.163k1", "DS.163kb", "DS.163k2", "DS.163k3"]),
    Product("hc", "Hydrometeor classification", "HC", _six("H"), "hc", kind="class", smooth=False,
            tgftp=["DS.165h0", "DS.165ha", "DS.165h1", "DS.165hb", "DS.165h2", "DS.165h3"]),
    Product("hhc", "Hybrid hydrometeor class", "HHC", [["HHC"]], "hc", kind="class", smooth=False,
            tgftp=["DS.177hh"]),
    Product("et", "Echo tops", "Tops", [["EET"]], "et", tgftp=["DS.135et"]),
    Product("vil", "Vertically integrated liquid", "VIL", [["DVL"]], "vil", tgftp=["DS.134il"]),
    Product("rate", "Rain rate", "Rate", [["DPR"]], "rate", tgftp=["DS.176pr"]),
    Product("p1h", "1-hour rainfall", "1-hr", [["DAA"]], "precip", tgftp=["DS.170aa"]),
    Product("pst", "Storm-total rainfall", "Total", [["DTA"]], "precip", tgftp=["DS.172dt"]),
    Product("cr", "Composite reflectivity", "Comp", [["NCR"]], "ref", smooth=False, tgftp=["DS.p37cr"]),
]
PRODUCT_BY_KEY = {p.key: p for p in PRODUCTS}

# Level 3 categories as Supercell Wx groups them; the first product is the default.
CATEGORIES = [
    ("REF", "Reflectivity", ["ref", "cr"]),
    ("VEL", "Velocity", ["vel"]),
    ("SRM", "Storm-relative motion", ["srv"]),
    ("CC", "Correlation coefficient", ["cc"]),
    ("ZDR", "Differential reflectivity", ["zdr"]),
    ("KDP", "Specific differential phase", ["kdp"]),
    ("HC", "Hydrometeor classification", ["hc", "hhc"]),
    ("VIL", "Vertically integrated liquid", ["vil"]),
    ("ET", "Echo tops", ["et"]),
    ("ACC", "Precipitation", ["rate", "p1h", "pst"]),
]
VARIANT_NAMES = {"ref": "Base", "cr": "Composite", "hc": "Base", "hhc": "Hybrid", "rate": "Rate",
                 "p1h": "1-hour", "pst": "Storm total"}


def category_of(key):
    for cat in CATEGORIES:
        if key in cat[2]:
            return cat
    return CATEGORIES[0]


TILT_NAMES = ["0.5°", "0.9°", "1.3°", "1.8°", "2.4°", "3.1°"]   # typical; the file says the real angle

# Storm-algorithm overlays: AWIPS ID on AWS (when there) and the tgftp folder.
OVERLAYS = {"sti": ("NST", "DS.58sti"), "hail": ("NHI", "DS.p59hi"), "meso": ("NMD", "DS.141md"),
            "tvs": ("NTV", "DS.61tvs")}

# Maximum range (km) and bin size (km) for products whose header doesn't say.
PRODUCT_GEOMETRY = {
    94: (460, 1.0), 99: (300, 0.25), 153: (460, 0.25), 154: (300, 0.25), 155: (300, 0.25),
    56: (230, 1.0), 159: (300, 0.25), 161: (300, 0.25), 163: (300, 0.25), 165: (300, 0.25),
    134: (460, 1.0), 135: (346, 1.0), 138: (230, 0.25), 169: (230, 2.0), 170: (230, 0.25), 172: (230, 0.25),
    173: (230, 0.25), 174: (230, 0.25), 175: (230, 0.25), 176: (230, 0.25), 177: (230, 0.25),
    30: (230, 1.0), 32: (230, 1.0), 19: (230, 1.0), 27: (230, 1.0), 20: (460, 2.0),
}

HC_CLASSES = {10: ("BI", "Biological"), 20: ("GC", "Ground clutter / AP"), 30: ("IC", "Ice crystals"),
              40: ("DS", "Dry snow"), 50: ("WS", "Wet snow"), 60: ("RA", "Light to moderate rain"),
              70: ("HR", "Heavy rain"), 80: ("BD", "Big drops"), 90: ("GR", "Graupel"),
              100: ("HA", "Hail, possibly with rain"), 110: ("LH", "Large hail"), 120: ("GH", "Giant hail"),
              130: ("ND", "No data"), 140: ("UK", "Unknown"), 150: ("RF", "Range folded")}

LEGACY_LABELS = ["Blank", "TH", "ND", "RF", "BI", "GC", "IC", "GR", "WS", "DS", "RA", "HR", "BD", "HA", "UK"]

VCP_MODE = {12: "precip", 212: "precip", 112: "precip", 215: "precip", 35: "clear air", 31: "clear air",
            32: "clear air", 21: "precip", 121: "precip", 11: "precip", 211: "precip", 221: "precip"}


def float16(val):
    """NEXRAD's 16-bit float (sign, 5-bit exponent, 10-bit fraction)."""
    val &= 0xFFFF
    frac, exp, sign = val & 0x3FF, (val >> 10) & 0x1F, val >> 15
    v = 2 ** (exp - 16) * (1 + frac / 1024.0) if exp else frac / 512.0
    return -v if sign else v


def float32(a, b):
    return struct.unpack(">f", struct.pack(">HH", a & 0xFFFF, b & 0xFFFF))[0]


def nexrad_time(date, secs):
    """NEXRAD Julian date (day 1 = 1970-01-01) and seconds after midnight -> epoch."""
    if not date:
        return None
    return (int(date) - 1) * 86400 + int(secs)


# ----------------------------------------------------------------------------
# the decoder
# ----------------------------------------------------------------------------

_HDR = struct.Struct(">hHiIhhh")                         # message header, 18 bytes
_PDB = struct.Struct(">hiihhhhhhHIHIhhhh16h7hbbIII")     # product description, 102 bytes
assert _HDR.size == 18 and _PDB.size == 102


class Level3:
    """One decoded Level 3 product."""

    def __init__(self, data, name=""):
        self.name = name
        self.radials = []        # [(start_az, width, bytes of levels)]
        self.nbins = 0
        self.first_bin = 0
        self.bin_km = 1.0
        self.raster = None       # (rows, ncols, cell_km, x0_km, y0_km) for raster products
        self.values = None       # 256 floats/None: level -> physical value
        self.labels = {}         # level -> text for special levels (ND, RF, classes)
        self.rf_level = None
        self.storms = []         # [{id, x, y}] in km east/north of the radar
        self.past = []           # [[(x, y), ...]] past tracks, newest point first
        self.forecast = []       # [[(x, y), ...]] forecast tracks, current position first
        self.hail = []           # [{x, y, poh, posh, size}]
        self.meso = []           # [{x, y, r, kind}]
        self.tvs = []            # [{x, y, kind}]
        self.texts = []          # [{x, y, text}]
        self.tables = []         # tabular alphanumeric pages (lists of lines)
        self.generic = None
        self._parse(data)

    # -- basics ------------------------------------------------------------------
    @staticmethod
    def _start(data):
        """Where the NIDS message starts (after any WMO / AWIPS header lines)."""
        cands = [0]
        i = 0
        while True:
            j = data.find(b"\r\r\n", i, 160)
            if j < 0:
                break
            cands.append(j + 3)
            i = j + 3
        for off in reversed(cands):
            if off + 120 > len(data):
                continue
            code = struct.unpack_from(">h", data, off)[0]
            div, = struct.unpack_from(">h", data, off + 18)
            pcode, = struct.unpack_from(">h", data, off + 30)
            if div == -1 and code == pcode and 0 < code < 1000:
                return off
        raise L3Error("not a NEXRAD Level 3 product")

    def _parse(self, data):
        if len(data) < 140:
            raise L3Error("file too short")
        off = self._start(data)
        hdr = _HDR.unpack_from(data, off)
        pdb = _PDB.unpack_from(data, off + 18)
        (_div, lat, lon, height, code, opmode, vcp, _seq, volnum, vdate, vtime, gdate, gtime,
         p1, p2, elnum, p3) = pdb[:17]
        self.thresholds = list(pdb[17:33])
        p4to10 = list(pdb[33:40])
        self.params = [p1, p2, p3] + p4to10           # p1, p2, p3, p4..p10
        sym_off, tab_off = pdb[42], pdb[44]
        self.code, self.msg_date, self.lat, self.lon = code, hdr[1], lat / 1000.0, lon / 1000.0
        self.height_ft, self.op_mode, self.vcp, self.vol_num, self.el_num = height, opmode, vcp, volnum, elnum
        self.vol_time = nexrad_time(vdate, vtime)
        self.gen_time = nexrad_time(gdate, gtime)
        self.elevation = None
        msg = data[off:]
        body = msg[120:]
        if body[:3] == b"BZh":
            dec = bz2.BZ2Decompressor()
            try:
                body = dec.decompress(body)
            except (OSError, ValueError, EOFError) as e:
                raise L3Error(f"bad compressed data: {e}")
            msg = msg[:120] + body
        self._msg = msg
        geo = PRODUCT_GEOMETRY.get(code)
        if code in (94, 99, 153, 154, 155, 56, 159, 161, 163, 165, 19, 20, 27, 30, 32, 37, 38, 134, 135):
            if code not in (32, 37, 38, 134, 135):
                self.elevation = p3 / 10.0
        self._values_for(code)
        if sym_off:
            try:
                self._symbology(sym_off * 2)
            except (struct.error, IndexError) as e:
                if not (self.radials or self.raster):
                    raise L3Error(f"damaged product: {e}")
        if tab_off:
            try:
                self._tabular(tab_off * 2)
            except (struct.error, IndexError, UnicodeDecodeError):
                pass
        if self.radials and geo:
            self.bin_km = geo[1]
        if code == 176 and self.generic is not None:
            self._generic_radials()
        del self._msg

    # -- values ------------------------------------------------------------------
    def _values_for(self, code):
        thr = self.thresholds
        vals = [None] * 256
        labels = {}
        if code == 138:
            mn, inc, n = thr[0] * 0.1, thr[1] * 0.01, min(thr[2], 254)
            for i in range(n):
                vals[2 + i] = mn + i * inc
            labels[0] = "no accumulation"
        elif code in (94, 153, 32, 180, 186, 195) or code in (99, 154, 182):
            mn, inc, n = thr[0] * 0.1, thr[1] * 0.1, thr[2]
            n = min(n, 254)
            for i in range(n):
                vals[2 + i] = mn + i * inc
            labels[0] = "below threshold"
            if code in (99, 154, 182):
                labels[1] = "range folded"
                self.rf_level = 1
            else:
                labels[1] = "missing"
        elif code == 155:
            mn, inc = thr[0] * 0.1, thr[1] * 0.1
            for i in range(129, 168):
                vals[i] = mn + (i - 129) * inc
            labels[1] = "range folded"
            self.rf_level = 1
        elif code in (159, 161, 163, 170, 172, 173, 174, 175, 176):
            scale, offset = float32(thr[0], thr[1]), float32(thr[2], thr[3])
            top = thr[5] & 0xFFFF
            lead, trail = thr[6], thr[7]
            unit = 100.0 if code in (170, 172, 173, 174, 175) else 1.0   # rainfall comes in 0.01 inch
            if scale:
                for i in range(max(0, lead), min(255, top - trail) + 1):
                    vals[i] = (i - offset) / scale / unit
            if lead > 1:
                labels[1] = "range folded"
                self.rf_level = 1
            labels[0] = "no data"
            self.scale_offset = (scale, offset, lead, trail, top)
        elif code in (165, 177):
            for i in range(10, 256):
                vals[i] = float(i // 10 * 10)
            for c, (abbr, word) in HC_CLASSES.items():
                labels[c] = word
            self.rf_level = 150
        elif code == 134:
            lin_s, lin_o = float16(thr[0]), float16(thr[1])
            log_start, log_s, log_o = thr[2], float16(thr[3]), float16(thr[4])
            for i in range(2, 255):
                if i < log_start:
                    vals[i] = (i - lin_o) / lin_s if lin_s else None
                elif log_s:
                    vals[i] = math.exp((i - log_o) / log_s)
        elif code == 135:
            mask, scale, offset, topped = thr[0], thr[1] or 1, thr[2], thr[3]
            for i in range(2, 256):
                vals[i] = ((i & mask) - offset) / float(scale)
            self.topped_mask = topped
        else:
            # 16-level legacy products: each threshold halfword describes one level
            for i, t in enumerate(thr):
                codes, val = (t >> 8) & 0xFF, t & 0xFF
                v, label = float(val), ""
                if codes & 0x80:
                    label = LEGACY_LABELS[val] if val < len(LEGACY_LABELS) else "?"
                    v = None
                    if label == "RF":
                        self.rf_level = i
                else:
                    if codes & 0x40:
                        v *= 0.01
                    elif codes & 0x20:
                        v *= 0.05
                    elif codes & 0x10:
                        v *= 0.1
                    if codes & 0x01:
                        v = -v
                    if codes & 0x04:
                        label = "<"
                    elif codes & 0x08:
                        label = ">"
                vals[i] = v
                if label and v is None:
                    labels[i] = {"TH": "below threshold", "ND": "no data", "RF": "range folded",
                                 "Blank": ""}.get(label, label)
        self.values, self.labels = vals, labels

    # -- symbology -----------------------------------------------------------------
    def _symbology(self, pos):
        m = self._msg
        div, bid, _blen, nlayers = struct.unpack_from(">hhIh", m, pos)
        if div != -1 or bid != 1:
            return
        pos += 10
        for _ in range(max(0, nlayers)):
            if pos + 6 > len(m):
                break
            div, llen = struct.unpack_from(">hI", m, pos)
            pos += 6
            end = min(len(m), pos + llen)
            self._packets(pos, end)
            pos = end

    def _packets(self, pos, end, scale=0.25):
        m = self._msg
        while pos + 2 <= end:
            code, = struct.unpack_from(">H", m, pos)
            pos += 2
            if code == 16:
                pos = self._digital_radial(pos)
            elif code == 0xAF1F:
                pos = self._rle_radial(pos)
            elif code in (0xBA0F, 0xBA07):
                pos = self._raster(pos)
            elif code == 28 or code == 29:
                _res, n = struct.unpack_from(">hI", m, pos)
                pos += 6
                try:
                    self.generic = _XDR(m[pos:pos + n]).product()
                except (struct.error, IndexError, ValueError):
                    self.generic = None
                pos += n
            elif code == 0x0802:
                pos += 4
            elif code == 0x0E03:
                _flag, _i, _j, n = struct.unpack_from(">Hhhh", m, pos)
                pos += 8 + max(0, n)
            elif code in (23, 24):
                n, = struct.unpack_from(">H", m, pos)
                pos += 2
                self._scit(code, pos, pos + n, scale)
                pos += n
            elif code in (1, 2, 8):
                n, = struct.unpack_from(">H", m, pos)
                pos += 2
                body = m[pos:pos + n]
                pos += n
                k = 2 if code == 8 else 0
                if len(body) >= 4 + k:
                    i, j = struct.unpack_from(">hh", body, k)
                    text = body[4 + k:].decode("latin-1", "replace")
                    self.texts.append({"x": i * scale, "y": j * scale, "text": text, "code": code})
            else:
                n, = struct.unpack_from(">H", m, pos)
                pos += 2
                body = m[pos:pos + n]
                pos += n
                self._symbol(code, body, scale)

    def _symbol(self, code, body, scale):
        n = len(body)
        if code == 15:            # storm IDs
            for k in range(0, n - 5, 6):
                i, j = struct.unpack_from(">hh", body, k)
                sid = body[k + 4:k + 6].decode("latin-1", "replace")
                self.storms.append({"id": sid.strip(), "x": i * scale, "y": j * scale})
        elif code == 19:          # HDA hail
            for k in range(0, n - 9, 10):
                i, j, poh, posh, size = struct.unpack_from(">hhhhH", body, k)
                self.hail.append({"x": i * scale, "y": j * scale, "poh": poh, "posh": posh, "size": size})
        elif code == 20:          # point features (mesocyclones, TVS)
            for k in range(0, n - 7, 8):
                i, j, kind, attr = struct.unpack_from(">hhHH", body, k)
                rec = {"x": i * scale, "y": j * scale, "kind": kind, "r": attr * scale}
                if kind in (5, 6, 7, 8):
                    self.tvs.append({"x": rec["x"], "y": rec["y"], "kind": "ETVS" if kind in (6, 8) else "TVS"})
                else:
                    rec["kind"] = {1: "meso (ext)", 3: "meso", 9: "MDA", 10: "MDA (elevated)",
                                   11: "MDA (weak)"}.get(kind, f"feature {kind}")
                    self.meso.append(rec)
        elif code in (3, 11):     # mesocyclone / 3D shear
            for k in range(0, n - 5, 6):
                i, j, r = struct.unpack_from(">hhh", body, k)
                self.meso.append({"x": i * scale, "y": j * scale, "r": r * scale,
                                  "kind": "meso" if code == 3 else "3D shear"})
        elif code in (12, 26):    # TVS / elevated TVS
            for k in range(0, n - 3, 4):
                i, j = struct.unpack_from(">hh", body, k)
                self.tvs.append({"x": i * scale, "y": j * scale, "kind": "TVS" if code == 12 else "ETVS"})
        elif code in (13, 14):    # positive / probable hail (legacy)
            for k in range(0, n - 3, 4):
                i, j = struct.unpack_from(">hh", body, k)
                self.hail.append({"x": i * scale, "y": j * scale, "poh": 100 if code == 13 else 50,
                                  "posh": 50 if code == 13 else 0, "size": 0})

    def _scit(self, code, pos, end, scale):
        """Storm track packets: special symbols (positions) and linked vectors (the track)."""
        m = self._msg
        track = []
        while pos + 4 <= end:
            sub, n = struct.unpack_from(">HH", m, pos)
            pos += 4
            body = m[pos:pos + n]
            pos += n
            if sub == 6:
                pts = struct.unpack_from(f">{len(body) // 2}h", body)
                track = [(pts[k] * scale, pts[k + 1] * scale) for k in range(0, len(pts) - 1, 2)]
            elif sub == 9 and len(body) >= 2:
                pts = struct.unpack_from(f">{(len(body) - 2) // 2}h", body, 2)
                track = [(pts[k] * scale, pts[k + 1] * scale) for k in range(0, len(pts) - 1, 2)]
            elif sub == 25:
                pass
        if track:
            (self.past if code == 23 else self.forecast).append(track)

    def _digital_radial(self, pos):
        m = self._msg
        first, nbins, _i, _j, _scale, nrad = struct.unpack_from(">Hhhhhh", m, pos)
        pos += 12
        self.first_bin, self.nbins = first, nbins
        out = []
        for _ in range(nrad):
            nb, start, delta = struct.unpack_from(">Hhh", m, pos)
            pos += 6
            out.append((start / 10.0, delta / 10.0, m[pos:pos + nb]))
            pos += nb
        self.radials = out
        return pos

    def _rle_radial(self, pos):
        m = self._msg
        first, nbins, _i, _j, _scale, nrad = struct.unpack_from(">Hhhhhh", m, pos)
        pos += 12
        self.first_bin, self.nbins = first, nbins
        cache = {}
        out = []
        for _ in range(nrad):
            nhw, start, delta = struct.unpack_from(">Hhh", m, pos)
            pos += 6
            rle = m[pos:pos + 2 * nhw]
            pos += 2 * nhw
            parts = []
            for b in rle:
                p = cache.get(b)
                if p is None:
                    p = cache[b] = bytes((b & 15,)) * (b >> 4)
                parts.append(p)
            row = b"".join(parts)
            out.append((start / 10.0, delta / 10.0, row[:nbins].ljust(nbins, b"\0")))
        self.radials = out
        return pos

    def _raster(self, pos):
        m = self._msg
        _flags, i0, j0, xs_i, xs_f, ys_i, ys_f, nrows, _packing = struct.unpack_from(">Ihhhhhhhh", m, pos)
        pos += 20
        rows = []
        width = 0
        cache = {}
        for _ in range(nrows):
            nb, = struct.unpack_from(">H", m, pos)
            pos += 2
            rle = m[pos:pos + nb]
            pos += nb
            parts = []
            for b in rle:
                p = cache.get(b)
                if p is None:
                    p = cache[b] = bytes((b & 15,)) * (b >> 4)
                parts.append(p)
            row = b"".join(parts)
            width = max(width, len(row))
            rows.append(row)
        rows = [r.ljust(width, b"\0") for r in rows]
        cell = float(xs_i or 1)
        # the grid is centred on the radar, north at the top
        self.raster = (rows, width, cell, -width * cell / 2.0, len(rows) * cell / 2.0)
        return pos

    def _tabular(self, pos):
        m = self._msg
        div, bid = struct.unpack_from(">hh", m, pos)
        if div != -1 or bid != 3:
            return
        pos += 8 + 18 + 102          # block header, then a copy of the message header + PDB
        div, npages = struct.unpack_from(">hh", m, pos)
        pos += 4
        pages = []
        for _ in range(max(0, min(npages, 64))):
            lines = []
            while pos + 2 <= len(m):
                n, = struct.unpack_from(">h", m, pos)
                pos += 2
                if n == -1:
                    break
                lines.append(m[pos:pos + n].decode("latin-1", "replace"))
                pos += n
            pages.append(lines)
        self.tables = pages

    def _generic_radials(self):
        comp = self.generic.get("radial") if self.generic else None
        if not comp:
            return
        rads = comp["radials"]
        if not rads:
            return
        scale, offset, lead, trail, top = getattr(self, "scale_offset", (1000.0, 0.0, 0, 0, 65535))
        # re-quantize 16-bit levels to 8 bits so the colour tables work the same way
        step = 0.02            # inches per hour per level
        vals = [None] * 256
        for q in range(2, 256):
            vals[q] = (q - 1) * step
        self.values = vals
        self.labels = {0: "no precipitation", 1: "no data"}
        cache = {}
        out = []
        nb = 0
        for az, _el, width, nbins, data in rads:
            row = bytearray(nbins)
            for k, lev in enumerate(data):
                q = cache.get(lev)
                if q is None:
                    if lev < max(1, lead):
                        q = 0
                    elif lev > top - trail:
                        q = 1
                    else:
                        v = (lev - offset) / scale if scale else 0.0
                        q = 0 if v <= 0 else min(255, 1 + int(round(v / step)))
                    cache[lev] = q
                row[k] = q
            nb = max(nb, nbins)
            out.append((az, width or 1.0, bytes(row)))
        self.radials = out
        self.nbins = nb
        self.first_bin = 0
        gw = comp.get("gate_width") or 250.0
        self.bin_km = gw / 1000.0 if gw > 5 else gw

    # -- lookups -----------------------------------------------------------------
    def value_at(self, x_km, y_km):
        """(level, value, label) at a point given in km east/north of the radar."""
        if self.raster is not None:
            rows, width, cell, x0, y0 = self.raster
            col = int((x_km - x0) / cell)
            row = int((y0 - y_km) / cell)
            if 0 <= row < len(rows) and 0 <= col < width:
                lev = rows[row][col]
                return lev, self.values[lev] if lev < len(self.values) else None, self.labels.get(lev, "")
            return None, None, "outside the radar's range"
        if not self.radials:
            return None, None, "no data"
        rng = math.hypot(x_km, y_km)
        az = (math.degrees(math.atan2(x_km, y_km)) + 360.0) % 360.0
        if self.elevation:
            rng = slant_range(rng, self.elevation)
        b = int(rng / self.bin_km) - self.first_bin
        if b < 0 or b >= self.nbins:
            return None, None, "outside the radar's range"
        for start, width, data in self._radials_near(az):
            if _az_in(az, start, width):
                if b >= len(data):
                    return 0, None, self.labels.get(0, "")
                lev = data[b]
                return lev, self.values[lev], self.labels.get(lev, "")
        return None, None, "no data"

    def _radials_near(self, az):
        if not hasattr(self, "_az_index"):
            self._az_index = sorted(((r[0] % 360.0, r) for r in self.radials), key=lambda t: t[0])
            self._az_keys = [t[0] for t in self._az_index]
        k = bisect.bisect_right(self._az_keys, az)
        n = len(self._az_index)
        return [self._az_index[(k - 1 - d) % n][1] for d in range(3)] + [self._az_index[k % n][1]]

    def nbytes(self):
        if self.raster is not None:
            return len(self.raster[0]) * self.raster[1]
        return sum(len(r[2]) for r in self.radials) + 2000

    def summary(self):
        mode = VCP_MODE.get(self.vcp) or ("precip" if self.vcp and self.vcp // 10 != 3 else "clear air")
        return {"code": self.code, "vcp": self.vcp, "mode": mode, "elevation": self.elevation,
                "time": self.vol_time, "gen": self.gen_time, "lat": self.lat, "lon": self.lon,
                "height_ft": self.height_ft}


def _az_in(az, start, width):
    d = (az - start) % 360.0
    return d < max(width, 0.01) + 1e-6


class _XDR:
    """The XDR 'generic' product format (packet 28), enough for radial products."""

    def __init__(self, data):
        self.b, self.p = data, 0

    def u32(self):
        v, = struct.unpack_from(">I", self.b, self.p)
        self.p += 4
        return v

    def i32(self):
        v, = struct.unpack_from(">i", self.b, self.p)
        self.p += 4
        return v

    def f32(self):
        v, = struct.unpack_from(">f", self.b, self.p)
        self.p += 4
        return v

    def string(self):
        n = self.u32()
        s = self.b[self.p:self.p + n].decode("latin-1", "replace")
        self.p += (n + 3) // 4 * 4
        return s

    def ints(self):
        n = self.u32()
        vals = struct.unpack_from(f">{n}i", self.b, self.p)
        self.p += 4 * n
        return vals

    def params(self):
        n = self.i32()
        self.i32()
        out = []
        for k in range(max(0, n)):
            out.append((self.string(), self.string()))
            if k < n - 1:
                self.i32()
        return out

    def product(self):
        out = {"name": self.string(), "description": self.string(), "code": self.i32(), "type": self.i32(),
               "prod_time": self.u32(), "radar": self.string(), "lat": self.f32(), "lon": self.f32(),
               "height": self.f32(), "vol_time": self.u32(), "el_time": self.u32(), "el_angle": self.f32(),
               "vol_num": self.i32(), "op_mode": self.i32(), "vcp": self.i32(), "el_num": self.i32(),
               "compression": self.i32(), "size": self.i32()}
        out["params"] = self.params()
        n = self.i32()
        self.i32()
        for k in range(max(0, n)):
            kind = self.i32()
            if kind == 1:
                desc = self.string()
                gw, fg = self.f32(), self.f32()
                self.params()
                nr = self.i32()
                rads = []
                for _ in range(max(0, nr)):
                    az, el, width = self.f32(), self.f32(), self.f32()
                    nb = self.i32()
                    self.string()
                    rads.append((az, el, width, nb, self.ints()))
                out["radial"] = {"description": desc, "gate_width": gw, "first_gate": fg, "radials": rads}
            elif kind == 4:
                self.params()
                out.setdefault("text", []).append(self.string())
            else:
                break
            if k < n - 1:
                self.i32()
        return out


def decode(data, name=""):
    return Level3(data, name)


# ----------------------------------------------------------------------------
# geometry
# ----------------------------------------------------------------------------

def slant_range(ground_km, elev_deg):
    """Slant range along a beam tilted elev_deg that is ground_km away (4/3 earth)."""
    if not elev_deg:
        return ground_km
    el = math.radians(elev_deg)
    r = ground_km / max(0.2, math.cos(el))
    for _ in range(2):
        s = ground_range(r, elev_deg)
        if s <= 0:
            break
        r *= ground_km / s
    return r


def ground_range(slant_km, elev_deg):
    el = math.radians(elev_deg or 0.0)
    re = BEAM_EARTH_KM
    h = math.sqrt(slant_km ** 2 + re ** 2 + 2 * slant_km * re * math.sin(el)) - re
    return re * math.asin(min(1.0, slant_km * math.cos(el) / (re + h)))


def beam_height_km(slant_km, elev_deg, site_m=0.0):
    el = math.radians(elev_deg or 0.0)
    re = BEAM_EARTH_KM
    return math.sqrt(slant_km ** 2 + re ** 2 + 2 * slant_km * re * math.sin(el)) - re + site_m / 1000.0


def to_xy(site_lat, site_lon, lat, lon):
    """Point -> (km east, km north) of the site on the azimuthal-equidistant plane."""
    p1, p2 = math.radians(site_lat), math.radians(lat)
    dl = math.radians(lon - site_lon)
    c = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)
    ang = math.acos(max(-1.0, min(1.0, c)))
    if ang < 1e-12:
        return 0.0, 0.0
    az = math.atan2(math.cos(p2) * math.sin(dl), math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))
    d = ang * EARTH_KM
    return d * math.sin(az), d * math.cos(az)


def from_xy(site_lat, site_lon, x_km, y_km):
    d = math.hypot(x_km, y_km) / EARTH_KM
    if d < 1e-12:
        return site_lat, site_lon
    az = math.atan2(x_km, y_km)
    p1 = math.radians(site_lat)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(az))
    lon = math.radians(site_lon) + math.atan2(math.sin(az) * math.sin(d) * math.cos(p1),
                                              math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(lon) + 540.0) % 360.0 - 180.0


# ----------------------------------------------------------------------------
# drawing a product onto the map
# ----------------------------------------------------------------------------

_mesh_cache = {}


def _polar_image(l3):
    """Radials -> an 'L' image (columns = range bins, rows = a regular azimuth grid),
    with a few rows repeated at the bottom so the 360/0 degree seam interpolates."""
    widths = sorted(w for _s, w, _d in l3.radials if w > 0)
    width = widths[len(widths) // 2] if widths else 1.0
    nrows = 720 if width < 0.75 else 360
    daz = 360.0 / nrows
    nb = l3.nbins
    blank = b"\0" * nb
    grid = [None] * nrows
    for start, w, data in l3.radials:
        w = w if w > 0 else width
        k0 = int(math.ceil(start / daz - 0.5 - 1e-9))
        k1 = int(math.ceil((start + w) / daz - 0.5 - 1e-9))
        row = data if len(data) == nb else data[:nb].ljust(nb, b"\0")
        for k in range(k0, max(k1, k0 + 1)):
            grid[k % nrows] = row
    rows = [g if g is not None else blank for g in grid]
    pad = 6
    raw = b"".join(rows) + b"".join(rows[:pad])
    return gfx.Image.frombytes("L", (nb, nrows + pad), raw), nrows, daz


def _mesh(view, l3, nrows, daz, cell):
    """Pillow MESH data mapping map pixels to (range bin, azimuth row) in the polar image.

    Cells are refined near the radar, where azimuth changes quickly across a cell."""
    key = (view.geo_key(), round(l3.lat, 4), round(l3.lon, 4), l3.elevation, l3.bin_km, l3.first_bin,
           l3.nbins, nrows, cell, l3.raster is not None and l3.raster[1:])
    hit = _mesh_cache.get(key)
    if hit is not None:
        return hit
    w, h = view.w, view.h
    el = l3.elevation or 0.0
    raster = l3.raster

    def src(x, y):
        lat, lon = view.from_px(x, y)
        ex, ny = to_xy(l3.lat, l3.lon, lat, lon)
        if raster is not None:
            _rows, _width, cs, x0, y0 = raster
            return ((ex - x0) / cs, (y0 - ny) / cs)
        s = math.hypot(ex, ny)
        r = slant_range(s, el) if el else s
        az = (math.degrees(math.atan2(ex, ny)) + 360.0) % 360.0
        return (r / l3.bin_km - l3.first_bin, az / daz)

    xs = list(range(0, w, cell)) + [w]
    ys = list(range(0, h, cell)) + [h]
    corners = [[src(x, y) for x in xs] for y in ys]
    half, fine, coarse = nrows / 2.0, nrows / 32.0, nrows / 6.0
    mesh = []

    def add(x0, y0, x1, y1, ul, ll, lr, ur):
        if raster is not None:
            mesh.append(((x0, y0, x1, y1), (ul[0], ul[1], ll[0], ll[1], lr[0], lr[1], ur[0], ur[1])))
            return
        azs = [ul[1], ll[1], lr[1], ur[1]]
        if max(azs) - min(azs) > half:
            azs = [a + nrows if a < half else a for a in azs]
        spread = max(azs) - min(azs)
        if spread > fine and (x1 - x0 > 1 or y1 - y0 > 1):
            xm, ym = (x0 + x1) // 2, (y0 + y1) // 2
            xm = xm if xm > x0 else x1
            ym = ym if ym > y0 else y1
            top, left = src(xm, y0), src(x0, ym)
            mid, right, bot = src(xm, ym), src(x1, ym), src(xm, y1)
            if xm < x1 and ym < y1:
                add(x0, y0, xm, ym, ul, left, mid, top)
                add(xm, y0, x1, ym, top, mid, right, ur)
                add(x0, ym, xm, y1, left, ll, bot, mid)
                add(xm, ym, x1, y1, mid, bot, lr, right)
            elif xm < x1:
                add(x0, y0, xm, y1, ul, ll, bot, top)
                add(xm, y0, x1, y1, top, bot, lr, ur)
            else:
                add(x0, y0, x1, ym, ul, left, right, ur)
                add(x0, ym, x1, y1, left, ll, lr, right)
            return
        if spread > coarse:
            azs = [-10.0] * 4                    # the pixel holding the radar itself
        mesh.append(((x0, y0, x1, y1), (ul[0], azs[0], ll[0], azs[1], lr[0], azs[2], ur[0], azs[3])))

    for j in range(len(ys) - 1):
        top, bot = corners[j], corners[j + 1]
        for i in range(len(xs) - 1):
            add(xs[i], ys[j], xs[i + 1], ys[j + 1], top[i], bot[i], bot[i + 1], top[i + 1])
    if len(_mesh_cache) > 12:
        _mesh_cache.pop(next(iter(_mesh_cache)))
    _mesh_cache[key] = mesh
    return mesh


def render(l3, view, lut, smooth=False):
    """Draw a decoded product for this view as an RGBA image.

    lut: (rgb bytes for 256 levels, alpha list of 256)."""
    rgb, alpha = lut
    cell = 4 if view.w < 400 else 8 if view.w < 1200 else 12
    if l3.raster is not None:
        rows, width, _cs, _x0, _y0 = l3.raster
        src = gfx.Image.frombytes("L", (width, len(rows)), b"".join(rows))
        nrows, daz = len(rows), 1.0
    elif l3.radials and l3.nbins:
        src, nrows, daz = _polar_image(l3)
    else:
        return gfx.Image.new("RGBA", (view.w, view.h), (0, 0, 0, 0))
    mesh = _mesh(view, l3, nrows, daz, cell)
    size = (view.w, view.h)
    near = src.transform(size, gfx.MESH, mesh, gfx.NEAREST)
    if smooth:
        soft = src.transform(size, gfx.MESH, mesh, gfx.BILINEAR)
        special = near.point([255 if v < 2 else 0 for v in range(256)])
        levels = gfx.Image.composite(near, soft, special)
    else:
        levels = near
    pimg = gfx.Image.frombytes("P", size, levels.tobytes())
    pimg.putpalette(rgb)
    out = pimg.convert("RGB")
    out.putalpha(levels.point(alpha))
    return out.convert("RGBA")


# ----------------------------------------------------------------------------
# listings: AWS bucket and tgftp
# ----------------------------------------------------------------------------

BUCKET = "https://unidata-nexrad-level3.s3.amazonaws.com"
TGFTP = "https://tgftp.nws.noaa.gov/SL.us008001/DF.of/DC.radar"
_KEY_RE = re.compile(r"<Key>([^<]+)</Key>")
_TIME_RE = re.compile(r"_(\d{4})_(\d\d)_(\d\d)_(\d\d)_(\d\d)_(\d\d)$")


def key_time(key):
    m = _TIME_RE.search(key)
    if not m:
        return None
    try:
        return calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))
    except (OverflowError, ValueError):
        return None


def list_keys(http, base, site3, awips, since, ttl=30):
    """Keys for one product newer than `since` (epoch), oldest first."""
    out = []
    now = time.time()
    days = sorted({time.gmtime(since)[:3], time.gmtime(now)[:3]})
    for y, mo, d in days:
        prefix = f"{site3}_{awips}_{y:04d}_{mo:02d}_{d:02d}_"
        after = time.strftime(f"{site3}_{awips}_%Y_%m_%d_%H_%M_%S", time.gmtime(since))
        token = None
        for _page in range(6):
            url = f"{base}/?list-type=2&prefix={prefix}&start-after={after}"
            if token:
                import urllib.parse
                url += "&continuation-token=" + urllib.parse.quote(token, safe="")
            text = http.get_text(url, ttl=ttl)
            out += _KEY_RE.findall(text)
            m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", text)
            if not m or "<IsTruncated>true</IsTruncated>" not in text:
                break
            token = m.group(1)
    seen = set()
    keys = []
    for k in sorted(out, key=lambda k: key_time(k) or 0):
        t = key_time(k)
        if t and t >= since and k not in seen:
            seen.add(k)
            keys.append(k)
    return keys


# ----------------------------------------------------------------------------
# radar sites
# ----------------------------------------------------------------------------

def parse_sites(j):
    """api.weather.gov/radar/stations -> [{id, name, lat, lon, elev_m, type}]."""
    out = []
    for f in (j or {}).get("features") or []:
        p = f.get("properties") or {}
        coords = (f.get("geometry") or {}).get("coordinates") or []
        sid = str(p.get("id") or "").upper()
        if len(coords) < 2 or not re.fullmatch(r"[A-Z]{4}", sid):
            continue
        elev = p.get("elevation") or {}
        out.append({"id": sid, "name": p.get("name") or sid, "lat": float(coords[1]), "lon": float(coords[0]),
                    "elev_m": elev.get("value") if isinstance(elev, dict) else None,
                    "type": p.get("stationType") or "WSR-88D"})
    return out


def nearest_sites(sites, lat, lon, n=8, kind="WSR-88D"):
    from .util import distance_mi
    rows = []
    for s in sites:
        if kind and s.get("type") != kind:
            continue
        d = distance_mi(lat, lon, s["lat"], s["lon"])
        if d is not None:
            rows.append((d, s))
    rows.sort(key=lambda r: r[0])
    return [dict(s, dist=d) for d, s in rows[:n]]


def site3(site_id):
    """KFWS -> FWS (the bucket names WSR-88D sites by their last three letters)."""
    sid = site_id.upper()
    return sid[1:] if len(sid) == 4 else sid


def tgftp_url(site_id, ds, base=TGFTP):
    return f"{base}/{ds}/SI.{site_id.lower()}/sn.last"
