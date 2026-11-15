"""3.13: the big numbers (the temperature, the AQI) in a squared display face of rounded
strokes; the AQI and PM2.5 charts as outlined solid columns; the Alerts screen's warnings
map (the state's counties colored by the alerts covering them) in place of the regional
radar."""

import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp(prefix="sd-cache-"))
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sd-config-"))

from skydrop import VERSION  # noqa: E402
from skydrop import charts as C  # noqa: E402
from skydrop import columns as CO  # noqa: E402
from skydrop import icons  # noqa: E402
from skydrop import screens as SC  # noqa: E402
from skydrop import statemap as SM  # noqa: E402
from skydrop import theme as T  # noqa: E402
from skydrop.crt import METER  # noqa: E402
from skydrop.fmt import Fmt  # noqa: E402
from skydrop.net import ApiError  # noqa: E402
from skydrop.store import DataStore  # noqa: E402

BG = (17, 34, 120)


def text(segs):
    return "".join(t for t, _s in segs)


def imgs(lines):
    return [s for ln in lines for _t, s in ln if isinstance(s, tuple) and s and s[0] == "@img"]



def pixels(im):
    """An RGB picture's pixels as (r, g, b) triples."""
    raw = im.convert("RGB").tobytes()
    return [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]

class Pictures:
    def __init__(self, pixels=True):
        self.pixels = pixels
        self.asked = {}

    def block(self, slot, key, cols, rows, builder, bg="text", back=None):
        self.asked[slot] = (cols, rows, builder, key)
        if self.pixels:
            return [[(" " * cols, ("@img", slot, r, bg))] for r in range(rows)]
        return [[(" " * cols, bg)] for _ in range(rows)]


def view(pixels=True, gmode=None):
    return types.SimpleNamespace(fmt=Fmt("imperial"), ascii=gmode == "ascii", theme=T.Theme(),
                                 images=Pictures(pixels), gmode=gmode or ("kitty" if pixels else "blocks"),
                                 radar=None, cfg={}, sel={})


class Meter(unittest.TestCase):
    def setUp(self):
        self.saved = dict(METER)
        METER.update({"blocks": True, "ascii": False, "style": "led"})

    def tearDown(self):
        METER.clear()
        METER.update(self.saved)


class TestVersion(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(tuple(map(int, VERSION.split("."))), (3, 13, 0))


class TestDigits(unittest.TestCase):
    def test_every_digit(self):
        for ch in "0123456789°-":
            m = icons.number_mask(ch, 100)
            self.assertGreater(sum(1 for v in m.tobytes() if v > 128), 300, ch)

    def test_zero_is_a_ring(self):
        m = icons.number_mask("0", 100)
        w = m.width
        self.assertEqual(m.getpixel((w // 2, 51)), 0)                      # open in the middle
        self.assertGreater(m.getpixel((w // 2, 5)), 200)                   # the top bar
        self.assertGreater(m.getpixel((5, 51)), 200)                       # the left side

    def test_four_open_at_the_top(self):
        m = icons.number_mask("4", 100)
        self.assertEqual(m.getpixel((m.width // 2, 5)), 0)                 # no bar across the top
        self.assertGreater(m.getpixel((m.width // 2, 51)), 200)            # the bar across the middle

    def test_one_narrow_with_a_flag(self):
        one, zero = icons.number_mask("1", 100), icons.number_mask("0", 100)
        self.assertLess(one.width, zero.width * 0.7)
        self.assertGreater(one.getpixel((4, 8)), 200)                      # the flag, top left
        self.assertEqual(one.getpixel((5, 60)), 0)

    def test_rounded_corners_flat_ends(self):
        m = icons.number_mask("2", 100)
        self.assertEqual(m.getpixel((m.width - 2, 2)), 0)                  # the top right corner rounded
        self.assertGreater(m.getpixel((2, 6)), 200)                        # the top bar's flat end, square

    def test_widths(self):
        self.assertLess(icons.number_cols("7", 5), icons.number_cols("57", 5))
        self.assertLess(icons.number_cols("57°", 5), icons.number_cols("103°", 5))

    def test_number_picture(self):
        im = icons.number_image("25", (40, 200, 60), 160, 100, BG)
        self.assertEqual(im.size, (160, 100))
        green = sum(1 for p in pixels(im) if p[1] > 150 and p[0] < 150)
        self.assertGreater(green, 300)

    def test_air_quality_number(self):
        src = Path(SC.__file__).read_text(encoding="utf-8")
        self.assertIn('ui.big_temp_block(v, number, sty, 5, slot="aqinum")', src)


class TestColumnsPicture(unittest.TestCase):
    def test_outlined_with_scale_guides_and_peak(self):
        vals = [10, 30, 60, 90, 40]
        cols = [(0, 200, 0)] * 5
        im = C.columns_image(vals, 0, 100, cols, 300, 120, BG, cols=30, rows=6, head=1, left=5,
                             tops=[(3, "90", (255, 255, 0))], guides=[(50, (180, 180, 180))],
                             axis=[(100, "100"), (50, "50")], axis_ink=((220, 220, 220), (120, 120, 120)))
        self.assertEqual(im.size, (300, 120))
        self.assertTrue(any(im.getpixel((x, y)) != BG for x in range(0, 45) for y in range(120)))   # the scale
        x = int(50 + (3 + 0.5) * 250 / 5)
        self.assertEqual(im.getpixel((x, 110)), (0, 200, 0))                # inside a column
        self.assertNotEqual(im.getpixel((int(50 + 3 * 50 + 2), 110)), (0, 200, 0))   # its outline
        self.assertTrue(any(im.getpixel((xx, yy))[2] < 80 and im.getpixel((xx, yy))[0] > 200
                            for xx in range(x - 12, x + 12) for yy in range(0, 40)))   # the peak's value

    def test_stations_keep_their_bars(self):
        im = C.bars_image([1, 2, 3], 0, 3, [(200, 100, 50)] * 3, 60, 30, BG)
        self.assertEqual(im.size, (60, 30))


class TestBarChartText(Meter):
    def test_scale_and_guides(self):
        lines = CO.bar_chart([10, 30, 60, 90], 30, 5, lo=0, hi=100, axis=[(100, "100"), (50, "50")],
                             guides=[(50, "faint")], labels=["a", "", "", "d"])
        rows = [text(ln) for ln in lines]
        self.assertTrue(any("┤" in r for r in rows))
        self.assertTrue(rows[-2].lstrip().startswith("└"))
        self.assertTrue(any(CO.GUIDE in r for r in rows))
        self.assertTrue(any("█" in r for r in rows))

    def test_whole_picture(self):
        plot = [[("x" * 30, ("@img", "p", r, "text"))] for r in range(6)]
        lines = CO.bar_chart([10, 30, 60, 90], 30, 5, lo=0, hi=100, tops=["", "", "", "90"],
                             axis=[(100, "100")], plot=plot, plot_from=0, labels=["a", "", "", "d"])
        self.assertEqual(len(imgs(lines)), 6)                              # the values' row and 5 of bars
        self.assertTrue(text(lines[-2]).lstrip().startswith("└"))


class TestAirCharts(Meter):
    def series(self):
        h0 = int(time.time()) // 3600 * 3600
        return [(h0 - (23 - i) * 3600, 30 + i * 4) for i in range(24)]

    def test_pictures_where_the_terminal_draws_them(self):
        v = view()
        lines = SC.aqi_chart(self.series(), v.fmt, 110, time.time(), 6, True, v=v, slot="aqiPast")
        got = imgs(lines)
        self.assertTrue(got and all(s[1].startswith("aqiPast") for s in got))
        cols, rows, build, _key = next(iter(v.images.asked.values()))
        im = build("pixels", cols * 9, rows * 18)
        self.assertEqual(im.size, (cols * 9, rows * 18))
        pm = SC.pm_chart([(t, a / 10) for t, a in self.series()], v.fmt, 110, time.time(), 5, True, v=v,
                         slot="pmPast")
        self.assertTrue(imgs(pm))

    def test_solid_bars_elsewhere(self):
        lines = SC.aqi_chart(self.series(), Fmt("imperial"), 110, time.time(), 6, True, v=view(False))
        self.assertFalse(imgs(lines))
        self.assertTrue(any("█" in text(ln) for ln in lines))


def county(geoid, x, y, d=1.0):
    return {"type": "Feature", "properties": {"GEOID": geoid, "BASENAME": f"C{geoid}"},
            "geometry": {"type": "Polygon", "coordinates": [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]]}}


def raw_map():
    feats = [county(f"48{k:03d}", -100 + (k % 4), 30 + (k // 4)) for k in range(1, 17, 2)]
    feats.append({"type": "Feature", "properties": {"GEOID": "48099", "BASENAME": "Isles"},
                  "geometry": {"type": "MultiPolygon", "coordinates": [
                      [[[-96, 34], [-95.5, 34], [-95.5, 34.5], [-96, 34]]], [[[-95, 34], [-94.5, 34], [-94.5, 34.5], [-95, 34]]]]}})
    places = {"features": [{"attributes": {"BASENAME": "Bigtown", "CENTLAT": 31.5, "CENTLON": -98.5, "AREALAND": 9}},
                           {"attributes": {"BASENAME": "Smallville", "CENTLAT": 32.5, "CENTLON": -97.5, "AREALAND": 1}}]}
    return {"state": "TX", "counties": {"type": "FeatureCollection", "features": feats}, "places": places}


class TestStateMap(unittest.TestCase):
    def test_parse(self):
        smap = SM.parse(raw_map())
        self.assertEqual(smap["state"], "TX")
        self.assertEqual(smap["name"], "Texas")
        self.assertEqual(len(smap["counties"]), 9)
        isles = next(c for c in smap["counties"] if c["geoid"] == "48099")
        self.assertEqual(len(isles["rings"]), 2)
        self.assertEqual(smap["places"][0][0], "Bigtown")
        self.assertIsNone(SM.parse(None))
        self.assertIsNone(SM.parse({"state": "TX", "counties": {}}))

    def test_alaska_wraps(self):
        raw = {"state": "AK", "counties": {"features": [county("02016", 179.0, 51.0), county("02013", -165, 54)]}}
        smap = SM.parse(raw)
        self.assertTrue(all(x < 0 for c in smap["counties"] for r in c["rings"] for x, _y in r))

    def test_fills_most_urgent(self):
        smap = SM.parse(raw_map())
        alerts = [{"key": "a", "event": "Tornado Watch", "rank": 8, "rgb": (255, 225, 0), "same": ["048001", "048003"],
                   "ugc": []},
                  {"key": "b", "event": "Tornado Warning", "rank": 1, "rgb": (230, 20, 30), "same": ["048003"], "ugc": []},
                  {"key": "c", "event": "Wind Advisory", "rank": 16, "rgb": (210, 180, 140), "same": [],
                   "ugc": ["TXC005", "TXZ119", "OKC003"]}]
        fills = SM.county_fills(alerts, smap)
        self.assertEqual(fills["48001"]["key"], "a")
        self.assertEqual(fills["48003"]["key"], "b")                       # the warning over the watch
        self.assertEqual(fills["48005"]["key"], "c")                       # by its county UGC code
        self.assertEqual(set(fills), {"48001", "48003", "48005"})          # not a zone, nor next door
        kinds = SM.legend(fills)
        self.assertEqual([k[0] for k in kinds], ["Tornado Warning", "Tornado Watch", "Wind Advisory"])
        self.assertEqual(SM.short_name("Severe Thunderstorm Warning"), "SEVERE T-STORM WARNING")

    def test_state_alerts(self):
        now = time.time()

        def feat(event, same, ends):
            return {"properties": {"status": "Actual", "event": event, "id": event + str(ends),
                                   "ends": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ends)),
                                   "geocode": {"SAME": same, "UGC": []}, "parameters": {}}}
        feed = {"features": [feat("Freeze Warning", ["048001"], now + 3600), feat("Frost Advisory", ["048003"], now - 60)]}
        got = SM.state_alerts(feed, now)
        self.assertEqual([a["event"] for a in got], ["Freeze Warning"])     # the expired one gone
        self.assertEqual(got[0]["same"], ["048001"])

    def test_picture(self):
        smap = SM.parse(raw_map())
        fills = {"48001": {"key": "a", "event": "Tornado Warning", "rank": 1, "rgb": (230, 20, 30)}}
        im = SM.map_image(smap, fills, 400, 300, BG, cols=40, rows=15, home=(31.5, -97.5), head=2)
        self.assertEqual(im.size, (400, 300))
        at = SM.projection(smap["bbox"], 0, 40, 400, 260, pad=0.03)
        x, y = at(-98.5, 30.5)                                             # the middle of county 48001
        self.assertEqual(im.getpixel((int(x), int(y))), (230, 20, 30))
        x, y = at(-98.5, 31.5)                                             # an uncovered county: gray
        c = im.getpixel((int(x), int(y)))
        self.assertTrue(abs(c[0] - c[2]) < 40 and c != BG)
        self.assertTrue(any(im.getpixel((xx, yy)) == (230, 20, 30) for xx in range(0, 40) for yy in range(0, 30)))
        coarse = SM.map_image(smap, fills, 60, 40, BG, cols=60, rows=40, home=(31.5, -97.5), labels=False,
                              coarse=True)
        self.assertEqual(coarse.size, (60, 40))

    def test_on_the_alerts_screen(self):
        v = view()
        smap = SM.parse(raw_map())
        m = {"alerts": [], "errors": {}, "alerts_at": time.time(), "loc": {"lat": 31.5, "lon": -97.5, "zip": "75201"},
             "state_map": smap, "state_alerts": [{"key": "a", "event": "Tornado Warning", "rank": 1,
                                                 "rgb": (230, 20, 30), "same": ["048001"], "ugc": []}],
             "sbw": [], "watches": [], "lsrs": [], "outlooks": []}
        out = SC.render_alerts(m, v, 190, 50)
        joined = "\n".join(text(ln) for ln in out["lines"])
        self.assertIn("Warnings map", joined)
        self.assertIn("Texas", joined)
        self.assertIn("warnmap", v.images.asked)
        cols, rows, build, _key = v.images.asked["warnmap"]
        self.assertEqual(build("pixels", cols * 9, rows * 18).size, (cols * 9, rows * 18))
        v2 = view(False)
        out = SC.render_alerts(m, v2, 190, 50)
        joined = "\n".join(text(ln) for ln in out["lines"])
        self.assertIn("Tornado Warning (1)", joined)                       # the key in words
        m["state_map"] = None
        out = SC.render_alerts(m, view(), 190, 50)
        self.assertNotIn("Warnings map", "\n".join(text(ln) for ln in out["lines"]))


class FakeHttp:
    def __init__(self):
        self.urls = []

    def get_json(self, url, headers=None, **kw):
        self.urls.append(url)
        return {"features": []}


class TestStore(unittest.TestCase):
    def test_counties_and_places_for_the_state(self):
        st = DataStore.__new__(DataStore)
        st.http = FakeHttp()
        got = st._state_map({}, {"state": "TX"}, {})
        self.assertEqual(got["state"], "TX")
        self.assertTrue(all("STATE%3D%2748%27" in u for u in st.http.urls))
        self.assertTrue(any("State_County/MapServer/1/query" in u for u in st.http.urls))
        self.assertTrue(any("tigerWMS_Current/MapServer/28/query" in u for u in st.http.urls))
        with self.assertRaises(ApiError):
            st._state_map({}, {}, {})
        with self.assertRaises(ApiError):
            st._state_map({"sources": {"tigerweb": {"enabled": False}}}, {"state": "TX"}, {})


if __name__ == "__main__":
    unittest.main()
