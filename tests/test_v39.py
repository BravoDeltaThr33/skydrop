"""3.9: cards as filled panels (flat under CRT mode), striped tables, the weather in the
title bar, condition tiles on Now, shorter sky words, the help card."""

import datetime as dt
import os
import sys
import tempfile
import time
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp(prefix="sd-cache-"))
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sd-config-"))

from skydrop import screens as SC  # noqa: E402
from skydrop import text as TX  # noqa: E402
from skydrop import theme as T  # noqa: E402
from skydrop import ui  # noqa: E402
from skydrop.fmt import Fmt  # noqa: E402


def text(segs):
    return "".join(t for t, _s in segs)


class Pictures:
    def block(self, slot, key, cols, rows, builder, bg="text", back=None):
        return [[(" " * cols, "text")] for _ in range(rows)]


def view(F, ascii_only=False):
    return types.SimpleNamespace(fmt=F, ascii=ascii_only, radar=None, gmode="ascii" if ascii_only else "blocks",
                                 theme=T.Theme(), images=Pictures(), sel={}, meter_look=lambda: "led")


class TestPanels(unittest.TestCase):
    def test_card_spans_on_the_panel(self):
        chip = ("rgb", (0, 0, 0), (200, 0, 0), "b")
        box = TX.card("Title", [[("x", "value"), ("  ", "text"), (" ", ("@img", "slot", 0, "text"))]], 30,
                      title_style=chip)
        for row in box:
            for t, st in row:
                if st == chip:
                    continue
                if isinstance(st, tuple) and st[0] == "@img":
                    self.assertEqual(st[3], ("onpanel", "text"))         # the picture keeps its place
                    continue
                self.assertEqual(st[0], "onpanel", (t, st))

    def test_spec(self):
        th = T.Theme("weatherstar")
        card_bg, stripe, page = th.rgb("card_bg"), th.rgb("stripe"), th.rgb("bg")
        self.assertNotEqual(card_bg, page)
        self.assertEqual(th.spec(("onpanel", "text"))[1], card_bg)
        self.assertEqual(th.spec(("onpanel", "text"))[0], th.spec("text")[0])
        self.assertEqual(th.spec(("stripe", "label"))[1], stripe)
        self.assertEqual(th.spec(("onpanel", ("stripe", "text")))[1], stripe)
        self.assertEqual(th.spec(("onpanel", "sel"))[1], th.spec("sel")[1])     # a bar of its own stays
        self.assertEqual(th.spec(("onpanel", ("rev", "good")))[1], card_bg)
        self.assertEqual(th.spec(("onpanel", ("rev", "good")))[2], "r")

    def test_a_background_of_your_own(self):
        th = T.Theme("weatherstar", "#202020")
        self.assertEqual(th.rgb("bg"), (32, 32, 32))
        self.assertNotEqual(th.rgb("card_bg"), (32, 32, 32))
        self.assertLess(max(abs(a - b) for a, b in zip(th.rgb("card_bg"), (32, 32, 32))), 30)

    def test_light_theme_panels_lighter(self):
        th = T.Theme("magnetic")
        self.assertGreater(sum(th.rgb("card_bg")), sum(th.rgb("bg")))

    def test_flat_under_crt(self):
        from skydrop import crt as C
        th = T.Theme("weatherstar")
        card_bg = th.rgb("card_bg")
        tube = C.CRT()
        tube.configure({"crt": True, "crt_phosphor": "green"}, th, "kitty", "truecolor")
        self.assertEqual(th.rgb("card_bg"), th.rgb("bg"))
        self.assertEqual(th.rgb("stripe"), th.rgb("bg"))
        tube.configure({"crt": False}, th, "kitty", "truecolor")
        self.assertEqual(th.rgb("card_bg"), card_bg)

    def test_zebra(self):
        line = [("a", "text"), ("b", "value")]
        self.assertIs(ui.zebra(line, 10, 0), line)
        odd = ui.zebra(line, 10, 1)
        self.assertEqual(text(odd), "ab" + " " * 8)
        self.assertTrue(all(st[0] == "stripe" for _t, st in odd))

    def test_tabs_on_a_band(self):
        th = T.Theme("weatherstar")
        self.assertEqual(th.spec("tab")[1], th.rgb("bar"))
        self.assertEqual(th.spec("tabkey")[1], th.rgb("bar"))


class TestWords(unittest.TestCase):
    def test_short_sky(self):
        self.assertEqual(ui.short_sky("Showers And Thunderstorms", 13), ["T-storms"])
        self.assertEqual(ui.short_sky("Chance Rain Showers then Mostly Sunny", 13), ["Chance", "Showers"])
        self.assertEqual(ui.short_sky("Slight Chance Showers And Thunderstorms then Partly Sunny", 13),
                         ["Slight chance", "T-storms"])
        self.assertEqual(ui.short_sky("Mostly Cloudy", 13), ["Mostly Cloudy"])
        three = ui.short_sky("Patchy Freezing Drizzle And Snow Showers Likely", 13)
        self.assertEqual(len(three), 2)
        self.assertTrue(three[-1].endswith("…"))

    def test_no_ceiling(self):
        F = Fmt("imperial")
        r = {"t": time.time(), "temp": 15.0, "ceiling": -30.0}
        rows = [text(x) for x in SC.hour_detail(r, F, 60)]
        self.assertTrue(any(x.strip().startswith("Cloud ceiling") and x.rstrip().endswith("none") for x in rows), rows)

    def test_help_lists_every_key(self):
        from skydrop.app import HELP
        joined = " ".join(w for _k, w in HELP)
        for word in ("y mosaic layer", "b map background", "e edit its address", "v show keys", "i the radar guide"):
            self.assertIn(word, joined)


def now_model():
    F = Fmt("imperial")
    now = time.time()
    today = F.today()
    obs = {"t": now - 600, "temp": 17.0, "dew": 4.0, "rh": 42.0, "wind": 2.2, "wind_deg": 70, "gust": 4.0,
           "press": 1017.0, "trend": 0.0, "vis": 16090.0, "clouds": [("FEW", 3800.0)], "desc": "Clear",
           "max24": 20.0, "min24": 11.0, "elevation": 148.0, "station_id": "KDAL"}
    cur = {"src": "Open-Meteo", "dt": now - 300, "temp": 16.0, "feels": 14.0, "humidity": 45.0, "dew": 4.0,
           "pressure": 1016.0, "vis": 26000.0, "clouds": 100.0, "uvi": 0.0, "wind": 1.8, "wind_deg": 70,
           "gust": 4.0, "rain_1h": 0.0, "desc": "overcast"}
    daily = [{"date": today + dt.timedelta(days=i), "hi": 20.0, "lo": 8.0, "uvi": 5.0, "pop": 10, "desc": "Sunny",
              "kind": "clear"} for i in range(7)]
    m = {"current": cur, "obs": obs, "station": {"id": "KDAL", "name": "Dallas Love Field"},
         "now": {"temp": 17.0, "desc": "Clear", "kind": "clear", "is_day": True, "t": now - 600},
         "daily": daily, "hourly": [], "errors": {}, "loc": {"lat": 32.8, "lon": -96.8, "zip": "75201"}}
    return m, F


class TestNowTiles(unittest.TestCase):
    def test_tiles(self):
        m, F = now_model()
        tiles = SC.conditions_tiles(m, F)
        self.assertEqual([t["title"] for t in tiles], ["Wind", "Humidity", "Pressure", "Visibility", "Clouds", "UV index"])
        wind = [text(r) for r in tiles[0]["build"](20)]
        self.assertTrue(wind[0].startswith("← ENE"), wind)                  # from 70°: blowing toward the west
        press = [text(r) for r in tiles[2]["build"](20)]
        self.assertIn("steady", press[1])
        asc = [text(r) for r in SC.conditions_tiles(m, F, True)[0]["build"](20)]
        self.assertTrue(asc[0].startswith("ENE"), asc)

    def test_now_screen(self):
        m, F = now_model()
        out = SC.render_now(m, view(F), 193, 49)
        rows = [text(r) for r in out["lines"]]
        joined = "\n".join(rows)
        for word in ("╭─ Wind", "╭─ Humidity", "╭─ UV index", "╭─ Now, measured and modeled", "NWS KDAL",
                     "Open-Meteo model", "╭─ Station KDAL", "Dallas Love Field"):
            self.assertIn(word, joined)
        self.assertTrue(all(len(r) <= 193 for r in rows))
        narrow = [text(r) for r in SC.render_now(m, view(F, True), 90, 40)["lines"]]
        self.assertTrue(all(len(r) <= 90 for r in narrow))
        self.assertIn("+- Humidity", "\n".join(narrow))


class TestTenDayColumns(unittest.TestCase):
    def test_empty_columns_left_out(self):
        F = Fmt("imperial")
        today = F.today()
        rows = [{"date": today + dt.timedelta(days=i), "hi": 20.0, "lo": 8.0, "pop": 10, "desc": "Sunny", "kind": "clear",
                 "wind": 3.0, "wind_deg": 90, "src": "NWS"} for i in range(7)]
        m = {"daily": rows, "errors": {}, "loc": {"lat": 32.8, "lon": -96.8}}
        out = SC.render_daily(m, view(F), 193, 49, printing=True)
        head = next(text(r) for r in out["lines"] if "Conditions" in text(r))
        for col in ("Hum", "Dew", "UV", "HeatRisk", "Gust"):
            self.assertNotIn(col, head)
        self.assertIn("Wind", head)


if __name__ == "__main__":
    unittest.main()
