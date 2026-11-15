"""3.10: area charts (Now's next 12 hours, the UV index, the month's highs), a station's
last 24 hours in solid outlined bars, your county's name in the warnings crawl (the ZIP
code chip is gone), a taller Hourly chart over an hour-by-hour table that scrolls, with a
scroll bar."""

import datetime as dt
import math
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
from skydrop import alerts as AL  # noqa: E402
from skydrop import columns as CO  # noqa: E402
from skydrop import screens as SC  # noqa: E402
from skydrop import screens2 as SC2  # noqa: E402
from skydrop import theme as T  # noqa: E402
from skydrop.app import App  # noqa: E402
from skydrop.canvas import Canvas  # noqa: E402
from skydrop.charts import bars_image  # noqa: E402
from skydrop.config import DEFAULTS  # noqa: E402
from skydrop.crt import METER  # noqa: E402
from skydrop.fmt import Fmt  # noqa: E402
from skydrop.store import DataStore  # noqa: E402
from skydrop.net import ApiError  # noqa: E402
from skydrop.term import Mouse  # noqa: E402
from skydrop.text import card  # noqa: E402
from skydrop.ticker import AlertCrawl  # noqa: E402

ZIP = "75201"


def text(segs):
    return "".join(t for t, _s in segs)


def rows_of(lines):
    return [text(ln) for ln in lines]


class Meter(unittest.TestCase):
    def setUp(self):
        self.saved = dict(METER)
        METER.update({"blocks": True, "ascii": False, "style": "led"})

    def tearDown(self):
        METER.clear()
        METER.update(self.saved)


class Pictures:
    """The picture registry as a pixel terminal has it: pictures come back as @img rows."""

    def __init__(self, pixels=True):
        self.pixels = pixels
        self.asked = {}

    def block(self, slot, key, cols, rows, builder, bg="text", back=None):
        self.asked[slot] = (cols, rows, builder)
        if self.pixels:
            return [[(" " * cols, ("@img", slot, r, bg))] for r in range(rows)]
        return [[(" " * cols, bg)] for _ in range(rows)]


def view(F, pixels=True, gmode="kitty"):
    return types.SimpleNamespace(fmt=F, ascii=False, radar=None, gmode=gmode, theme=T.Theme(),
                                 images=Pictures(pixels), sel={}, meter_look=lambda: "led", store=None)


class TestVersion(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(tuple(int(x) for x in VERSION.split(".")), (3, 10, 0))


# -- area charts ------------------------------------------------------------------------

class TestAreaChart(Meter):
    def test_filled_under_the_line_and_darker_down(self):
        lines = CO.area_chart([0, 50, 100, 50, 0], 40, 5, lo=0, hi=100, style="value", base=False)
        self.assertEqual(len(lines), 5)
        bottom = lines[-1]
        looks = [s for t, s in bottom if "█" in t]
        self.assertTrue(looks)
        # the cells along the base under the peak are faded; the line's own edge isn't
        self.assertTrue(any(isinstance(s, tuple) and s[0] == "fade" and s[2] < 1 for s in looks), looks)
        top = rows_of(lines)[0]
        self.assertTrue(any(ch in top for ch in CO.EIGHTHS[1:]))          # the peak reaches the top row

    def test_fade_resolves_toward_the_card(self):
        th = T.Theme()
        full = th.spec("value")[0]
        half = th.spec(("fade", "value", 0.5))[0]
        card_bg = th.pal["card_bg"]
        for f, h, c in zip(full, half, card_bg):
            self.assertAlmostEqual(h, c + (f - c) * 0.5, delta=1.01)

    def test_guide_over_the_fill(self):
        lines = CO.area_chart([80] * 6, 30, 4, lo=0, hi=100, guides=[(50, "dim")], base=False)
        over = [s for ln in lines for t, s in ln if isinstance(s, tuple) and s[0] == "over"]
        self.assertTrue(over)
        fg, bg = T.Theme().spec(over[0])[:2]
        self.assertEqual(fg, T.Theme().spec("dim")[0])                    # the guide's color, on the fill's
        lines = CO.area_chart([20] * 6, 30, 4, lo=0, hi=100, guides=[(80, "dim")], base=False)
        self.assertTrue(any(CO.GUIDE in r for r in rows_of(lines)))       # dotted over empty cells

    def test_values_over_the_line(self):
        lines = CO.area_chart([10, 30, 90, 30, 10], 50, 6, lo=0, hi=100, tops=["", "", "90", "", ""], base=False)
        joined = "\n".join(rows_of(lines))
        self.assertIn("90", joined)

    def test_ascii(self):
        METER.update({"ascii": True})
        rows = rows_of(CO.area_chart([10, 50, 90], 30, 4, lo=0, hi=100, base=False))
        self.assertTrue(any("#" in r for r in rows))
        self.assertTrue(all(ch.isascii() for r in rows for ch in r))

    def test_the_screens_use_it(self):
        src = Path(SC.__file__).read_text(encoding="utf-8")
        for name in ("def next_hours", "def uv_chart", "def month_chart"):
            body = src[src.index(name):]
            body = body[:body.index("\ndef ", 10)]
            self.assertTrue("area_chart(" in body or "area(v," in body, name)

    def test_temperature_area_colored_by_its_height(self):
        F = Fmt("imperial")
        h0 = int(time.time()) // 3600 * 3600
        m = {"hourly": [{"t": h0 + i * 3600, "temp": -10 + i * 4, "pop": 0} for i in range(14)]}
        looks = set()
        for ln in SC.next_hours(m, F, 100):
            for t, s in ln:
                while isinstance(s, tuple) and s and s[0] in ("fade", "over"):
                    s = s[1] if s[0] == "fade" else s[2]
                if any(ch in t for ch in CO.EIGHTHS[1:]):
                    looks.add(s)
        self.assertTrue({"t_frigid", "t_cold", "t_cool", "t_mild"} <= looks, looks)

    def test_small_uv_chart_scale(self):
        F = Fmt("imperial")
        day = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        rows = [(day + h * 3600, max(0.0, 3 * math.sin((h - 7) / 11 * math.pi)) if 7 <= h <= 18 else 0.0)
                for h in range(24)]
        tall = SC.uv_chart(rows, F, 60, time.time(), height=6, axis=False)
        # a winter day's peak of 3 fills most of the small card's height, not a quarter of 11
        filled = [r for r in rows_of(tall) if any(ch in r for ch in CO.EIGHTHS[1:])]
        self.assertGreaterEqual(len(filled), 3)


# -- solid outlined bars ----------------------------------------------------------------

class TestBarChart(Meter):
    def test_bars_a_cell_apart(self):
        lines = CO.bar_chart([10, 20, 30, 40], 20, 4, lo=0, hi=40, base=False)
        bottom = rows_of(lines)[-1]
        self.assertEqual(bottom.rstrip(), "████ ████ ████ ████")

    def test_a_sliver_apart_with_a_cell_each(self):
        lines = CO.bar_chart([20, 40, 40, 40], 4, 2, lo=0, hi=40, base=False)
        self.assertEqual(rows_of(lines), [" ▉▉▉", "▉▉▉▉"])

    def test_eighths_on_top(self):
        rows = rows_of(CO.bar_chart([40, 25], 6, 4, lo=0, hi=40, base=False))
        self.assertTrue(any(ch in "".join(rows) for ch in CO.EIGHTHS[1:8]))

    def test_tops_over_their_bars(self):
        rows = rows_of(CO.bar_chart([40, 10], 12, 4, lo=0, hi=40, tops=["40", "10"], base=False))
        self.assertIn("40", rows[0])
        k = next(i for i, r in enumerate(rows) if "10" in r)
        self.assertGreater(k, 0)                                          # just over the short bar

    def test_over_a_picture(self):
        plot = [[("x" * 12, "text")] for _ in range(4)]
        lines = CO.bar_chart([40, 10], 12, 4, lo=0, hi=40, tops=["40", "10"], plot=plot, title="", base=False)
        rows = rows_of(lines)
        self.assertIn("40", rows[0])
        self.assertNotIn("10", "".join(rows))                             # it couldn't sit on its bar
        self.assertEqual(rows[1:], ["x" * 12] * 4)

    def test_ascii(self):
        METER.update({"ascii": True})
        rows = rows_of(CO.bar_chart([10, 20, 30], 9, 3, lo=0, hi=30, base=False))
        self.assertEqual(rows[-1].rstrip(), "## ## ##")

    def test_outlined_picture(self):
        img = bars_image([5, 10, None, 7], 0, 10, [(200, 100, 50)] * 4, 80, 40, (10, 20, 90))
        self.assertEqual(img.size, (80, 40))
        px = img.load()
        self.assertEqual(px[20 + 10, 35], (200, 100, 50))                 # inside the second bar: its color
        self.assertEqual(px[20 + 10, 39], (100, 50, 25))                  # its bottom edge: outlined too
        self.assertNotEqual(px[20 + 1, 30], (200, 100, 50))               # its edge: the outline
        self.assertEqual(px[20 + 1, 30], (100, 50, 25))
        self.assertEqual(px[20, 30], (10, 20, 90))                        # between bars: the card
        self.assertEqual(px[50, 30], (10, 20, 90))                        # no reading: no bar


class TestStationBars(Meter):
    def hist(self, now):
        return [{"t": now - k * 1200, "temp": 10 + 5 * math.sin(k / 20), "wind": 3 + k % 5,
                 "press": 1015 + k / 30} for k in range(72)]

    def test_pictures_where_the_terminal_draws_them(self):
        F = Fmt("imperial")
        now = time.time()
        v = view(F)
        lines = SC2.last_day_charts(self.hist(now), F, 60, now, v, "KXYZ")
        imgs = {s[1] for ln in lines for _t, s in ln if isinstance(s, tuple) and s[0] == "@img"}
        self.assertEqual(imgs, {"sthistTemp", "sthistWind", "sthistPress"})
        cols, rows, builder = v.images.asked["sthistTemp"]
        self.assertEqual(rows, 5)
        img = builder("pixels", cols * 10, rows * 20)
        self.assertEqual(img.size, (cols * 10, rows * 20))
        joined = "\n".join(rows_of(lines))
        for word in ("Temp", "Wind", "Press", "Now", "pressure"):
            self.assertIn(word, joined)

    def test_text_bars_elsewhere(self):
        F = Fmt("imperial")
        now = time.time()
        lines = SC2.last_day_charts(self.hist(now), F, 60, now, view(F, pixels=False, gmode="blocks"), "KXYZ")
        joined = "\n".join(rows_of(lines))
        self.assertIn("█", joined)
        self.assertFalse(any(isinstance(s, tuple) and s[0] == "@img" for ln in lines for _t, s in ln))
        self.assertEqual(SC2.last_day_charts([], F, 60, now), [])

    def test_charts_a_row_apart(self):
        F = Fmt("imperial")
        now = time.time()
        rows = rows_of(SC2.last_day_charts(self.hist(now), F, 60, now))
        k = next(i for i, r in enumerate(rows) if r.strip().startswith("Wind"))
        self.assertTrue(any(not rows[j].strip() for j in range(max(0, k - 2), k)))


# -- your county in the crawl -----------------------------------------------------------

class TestCountyLabel(unittest.TestCase):
    def test_names(self):
        self.assertEqual(AL.county_label("Dallas", "TX"), "Dallas County")
        self.assertEqual(AL.county_label("Orleans", "LA"), "Orleans Parish")
        self.assertEqual(AL.county_label("City of Richmond", "VA"), "City of Richmond")
        self.assertEqual(AL.county_label("St. Louis City", "MO"), "St. Louis City")
        self.assertEqual(AL.county_label("New York (Manhattan)", "NY"), "New York (Manhattan)")
        self.assertEqual(AL.county_label("Anchorage", "AK"), "Anchorage")
        self.assertEqual(AL.county_label("  Fairfax ", "VA"), "Fairfax County")
        self.assertEqual(AL.county_label("", "TX"), "")


class FakeHttp:
    def __init__(self, reply=None, fail=False):
        self.reply, self.fail, self.urls = reply, fail, []

    def get_json(self, url, headers=None, **kw):
        self.urls.append(url)
        if self.fail:
            raise ApiError(503, "down")
        return self.reply


class TestCountyLookup(unittest.TestCase):
    def store(self, http):
        st = DataStore.__new__(DataStore)
        st.http = http
        return st

    def test_from_the_county_zone(self):
        http = FakeHttp({"properties": {"id": "TXC113", "name": "Dallas", "state": "TX"}})
        got = self.store(http)._county("https://nws.example", "https://api.weather.gov/zones/county/TXC113", {}, None)
        self.assertEqual(got, ("Dallas County", "Dallas"))
        self.assertEqual(http.urls, ["https://nws.example/zones/county/TXC113"])    # the base from Settings

    def test_falls_back_to_what_was_saved(self):
        url = "https://api.weather.gov/zones/county/TXC113"
        saved = {"props": {"county": url, "county_name": "Dallas County", "county_zone": "Dallas"}}
        got = self.store(FakeHttp(fail=True))._county("https://nws.example", url, {}, saved)
        self.assertEqual(got, ("Dallas County", "Dallas"))
        other = {"props": {"county": "https://api.weather.gov/zones/county/TXC085", "county_name": "Collin County"}}
        self.assertEqual(self.store(FakeHttp(fail=True))._county("b", url, {}, other), ("", ""))

    def test_not_a_county(self):
        http = FakeHttp({"properties": {"name": "x"}})
        self.assertEqual(self.store(http)._county("b", "https://api.weather.gov/zones/forecast/TXZ119", {}, None),
                         ("", ""))
        self.assertEqual(self.store(http)._county("b", None, {}, None), ("", ""))
        self.assertEqual(http.urls, [])


def an_alert(**kw):
    a = {"event": "Freeze Warning", "sig": "W", "phen": "FZ", "area": "Collin; Dallas; Denton",
         "end": time.time() + 3 * 3600, "rgb": (72, 61, 139), "key": "fz", "in_zip": True, "in_county": True,
         "inside": True, "dist": 0.0, "src": "NWS", "tags": {}}
    a.update(kw)
    return a


class TestCrawlCounty(unittest.TestCase):
    def crawl(self, items, county="Dallas County", zone="Dallas"):
        c = AlertCrawl()
        c.update(items, now=1000.0)
        c.county, c.county_zone = county, zone
        return c

    def test_between_the_alert_and_its_time(self):
        items = AL.ticker_items([an_alert()], [], dict(DEFAULTS), Fmt("imperial"))
        self.assertTrue(items[0][6])
        self.assertEqual(items[0][1].split(" · ")[0][:6], "until ")
        line = self.crawl(items).render(150, 1000.5, [])
        flat = text(line)
        self.assertTrue(flat.startswith(" FREEZE WARNING   Dallas County  until "), flat[:60])
        self.assertNotIn(ZIP, flat)
        self.assertNotIn("ZIP", flat)
        self.assertEqual(len(flat), 150)

    def test_lit_up(self):
        items = AL.ticker_items([an_alert()], [], dict(DEFAULTS), Fmt("imperial"))
        line = self.crawl(items).render(150, 1000.5, [])
        lit = [(t, s) for t, s in line if "Dallas County" in t]
        self.assertTrue(lit)
        st = lit[0][1]
        self.assertEqual(st[0], "rgb")
        self.assertEqual(st[2], (255, 255, 255))                          # white, unlike the rest of the line
        self.assertIn("b", st[3])
        others = [s for t, s in line if "until" in t]
        self.assertNotEqual(others[0], st)
        # and where it goes by among the counties
        self.assertTrue(any(t == "Dallas" and s == st for t, s in line), line)

    def test_readable_on_any_alert_color(self):
        c = AlertCrawl()
        for rgb in ((255, 228, 181), (255, 255, 0), (72, 61, 139), (230, 20, 30), (210, 180, 140)):
            fg = c._lit(rgb)[1]
            lum = 0.299 * fg[0] + 0.587 * fg[1] + 0.114 * fg[2]
            self.assertLessEqual(lum, 86, rgb)                            # dark letters on white

    def test_when_the_counties_go_by(self):
        many = "; ".join(f"County{i:02d}" for i in range(40))
        items = [("FREEZE WARNING", "until 9:00 AM", (72, 61, 139), False, "fz", many + "; Dallas", True),
                 ("WIND ADVISORY", "until 6:00 PM · 30 mi NW", (210, 180, 140), False, "w", "Denton; Wise", False)]
        c = self.crawl(items)
        c.t0 = 1000.0
        a = text(c.render(120, 1000.5, []))
        b = text(c.render(120, 1006.5, []))
        for flat in (a, b):
            self.assertTrue(flat.startswith(" FREEZE WARNING   Dallas County  until 9:00 AM ·"), flat[:60])
        self.assertTrue(c.moving)
        self.assertNotEqual(a, b)                                         # the counties moved, the county didn't

    def test_not_on_an_alert_elsewhere(self):
        items = [("WIND ADVISORY", "until 6:00 PM · 30 mi NW", (210, 180, 140), False, "w", "Denton; Dallas", False)]
        flat = text(self.crawl(items).render(120, 1000.5, []))
        self.assertTrue(flat.startswith(" WIND ADVISORY  until 6:00 PM"))
        self.assertNotIn("Dallas County", flat)

    def test_without_its_name(self):
        items = AL.ticker_items([an_alert()], [], dict(DEFAULTS), Fmt("imperial"))
        flat = text(self.crawl(items, "", "").render(150, 1000.5, []))
        self.assertTrue(flat.startswith(" FREEZE WARNING   Your county  until "), flat[:60])

    def test_clears_with_the_alert(self):
        items = AL.ticker_items([an_alert()], [], dict(DEFAULTS), Fmt("imperial"))
        c = self.crawl(items)
        c.update([], now=1001.0)
        self.assertEqual(c.render(150, 1002.0, [("calm", "status")]), [("calm", "status")])

    def test_distance_kept_for_a_warning_elsewhere_in_the_county(self):
        a = an_alert(event="Severe Thunderstorm Warning", phen="SV", in_zip=False, inside=False, dist=8.0, bearing=225,
                     rings=[[(-97.0, 32.0), (-96.9, 32.0), (-96.9, 32.1), (-97.0, 32.0)]], rgb=(255, 165, 0), key="sv")
        items = AL.ticker_items([a], [], dict(DEFAULTS), Fmt("imperial"))
        self.assertTrue(items[0][6])
        self.assertIn("8 mi SW", items[0][1])
        self.assertNotIn("your county", items[0][1])

    def test_app_hands_it_on(self):
        src = Path(os.path.join(os.path.dirname(SC.__file__), "app.py")).read_text(encoding="utf-8")
        self.assertIn('self.crawl.county = pts.get("county_name")', src)
        self.assertNotIn("crawl.zip", src)
        self.assertFalse(hasattr(AlertCrawl(), "zip"))
        self.assertFalse(hasattr(AlertCrawl, "pin"))


# -- Hourly: a taller chart, every hour, a scroll bar ----------------------------------

def hourly_model(n=48):
    h0 = int(time.time()) // 3600 * 3600
    rows = [{"t": h0 + i * 3600, "temp": 10 + 5 * math.sin(i / 6), "feels": 9, "pop": (i * 7) % 100, "desc": "cloudy",
             "wind": 4, "wind_deg": 180, "src": "nws"} for i in range(n)]
    return {"hourly": rows, "updated": 1, "errors": {}}


class TestHourly(unittest.TestCase):
    def render(self, H=48, W=193, pixels=True):
        F = Fmt("imperial")
        v = view(F, pixels, "kitty" if pixels else "blocks")
        return SC.render_hourly(hourly_model(), v, W, H), v

    def test_taller_chart(self):
        _out, v = self.render(H=48)
        self.assertEqual(v.images.asked["hourlychart"][1], 9)
        _out, v = self.render(H=34)
        self.assertEqual(v.images.asked["hourlychart"][1], 7)
        _out, v = self.render(H=26)
        self.assertEqual(v.images.asked["hourlychart"][1], 5)

    def test_same_width(self):
        out, v = self.render(H=48)
        main_w = 193 - out["side_w"] - 1
        self.assertEqual(v.images.asked["hourlychart"][0], main_w - 4)

    def test_every_hour_in_the_table(self):
        out, _v = self.render(H=48)
        self.assertEqual(sorted(out["rowmap"].values()), list(range(48)))
        joined = "\n".join(rows_of(out["lines"]))
        self.assertNotIn("every 2 hours", joined)
        self.assertNotIn("every 3 hours", joined)
        self.assertTrue(out["scrollbar"])

    def test_printing_has_no_bar(self):
        F = Fmt("imperial")
        out = SC.render_hourly(hourly_model(), view(F), 193, 48, printing=True)
        self.assertFalse(out.get("scrollbar"))


def a_card_canvas(rows=30, width=40):
    cv = Canvas(width, rows, "text")
    box = card("Hour by hour", [[("row", "text")]] * (rows - 2), width)
    for y, ln in enumerate(box):
        cv.put(y, 0, ln, width)
    return cv


class TestScrollBar(unittest.TestCase):
    def me(self, ascii_only=False):
        app = App.__new__(App)
        app.gmode = "ascii" if ascii_only else "blocks"
        return app

    def thumb(self, cv, x):
        return [y for y in range(cv.h) if cv.chars[y][x] in ("█", "#")]

    def test_thumb_is_the_part_on_screen(self):
        cv = a_card_canvas()
        sb = App.scrollbar(self.me(), cv, 39, 1, 20, 0, 80)
        self.assertEqual(len(sb["rows"]), 20)
        self.assertEqual(sb["size"], 5)                                   # 20 of 80 lines: a quarter
        self.assertEqual(self.thumb(cv, 39), [1, 2, 3, 4, 5])

    def test_at_the_end(self):
        cv = a_card_canvas()
        App.scrollbar(self.me(), cv, 39, 1, 20, 60, 80)
        self.assertEqual(self.thumb(cv, 39), [16, 17, 18, 19, 20])

    def test_only_over_the_border(self):
        cv = a_card_canvas(rows=12)
        sb = App.scrollbar(self.me(), cv, 39, 1, 20, 0, 80)                # runs past the card's bottom
        self.assertEqual(sb["rows"], list(range(1, 11)))
        self.assertEqual(cv.chars[11][39], "╯")

    def test_ascii(self):
        cv = Canvas(40, 30, "text")
        for y, ln in enumerate(card("t", [[("row", "text")]] * 28, 40, ascii_only=True)):
            cv.put(y, 0, ln, 40)
        App.scrollbar(self.me(True), cv, 39, 1, 20, 0, 80)
        self.assertEqual(self.thumb(cv, 39), [1, 2, 3, 4, 5])

    def mouse_app(self):
        app = App.__new__(App)
        app.gmode = "blocks"
        app.ring_off = (0, 0)
        app.alarm = None
        app.idx = 1
        app.settings = types.SimpleNamespace(card=None)
        app.guide = None
        app.local8s = None
        app.help = False
        app.scroll = [0] * 10
        app.fscroll = [0] * 10
        app.sel = {"hourly": 0}
        app.sb_drag = None
        cv = a_card_canvas()
        app.layout = {"body_top": 1, "sticky": 0, "scroll": 0, "body_h": 20, "rowmap": {}, "side_x": None,
                      "tabs": []}
        app.layout["scrollbar"] = App.scrollbar(app, cv, 39, 1, 20, 0, 80)
        return app

    def test_press_the_bar_to_jump(self):
        app = self.mouse_app()
        app.on_mouse(Mouse("press", 0, 39, 20))                            # the bottom of the bar
        self.assertEqual(app.scroll[1], 60)
        app.on_mouse(Mouse("release", 0, 39, 20))
        self.assertIsNone(app.sb_drag)

    def test_drag_the_thumb(self):
        app = self.mouse_app()
        app.on_mouse(Mouse("press", 0, 39, 2))                             # the thumb's second row
        self.assertEqual(app.sb_drag, 1)
        self.assertEqual(app.scroll[1], 0)                                 # taken hold of, not moved
        app.on_mouse(Mouse("drag", 0, 39, 9))                              # down 7 of the 15 it can go
        self.assertEqual(app.scroll[1], 28)
        app.on_mouse(Mouse("release", 0, 39, 9))
        self.assertIsNone(app.sb_drag)
        app.on_mouse(Mouse("drag", 0, 39, 15))                             # let go: a drag doesn't move it
        self.assertEqual(app.scroll[1], 28)

    def test_motion_on_while_dragging(self):
        src = Path(os.path.join(os.path.dirname(SC.__file__), "app.py")).read_text(encoding="utf-8")
        self.assertIn('set_motion((on_radar and on(self.cfg, "radar_hover")) or self.sb_drag is not None)', src)

    def test_draw_body_draws_it(self):
        src = Path(os.path.join(os.path.dirname(SC.__file__), "app.py")).read_text(encoding="utf-8")
        self.assertIn('if r.get("scrollbar") and len(body) > body_h else None', src)


if __name__ == "__main__":
    unittest.main()
