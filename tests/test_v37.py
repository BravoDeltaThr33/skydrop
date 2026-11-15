"""3.7: no warning banner up top, a warning's counties going by in the crawl, and Air & UV
as a screen of rounded cards."""

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

from skydrop import screens as SC  # noqa: E402
from skydrop import text as TX  # noqa: E402
from skydrop import ui  # noqa: E402
from skydrop.alerts import ticker_items  # noqa: E402
from skydrop.config import DEFAULTS  # noqa: E402
from skydrop.fmt import Fmt  # noqa: E402
from skydrop.ticker import PAUSE, STILL, TURN, AlertCrawl  # noqa: E402

COUNTIES = "; ".join(f"County{i:02d}" for i in range(60))


def text(segs):
    return "".join(t for t, _s in segs)


def alert(label="FREEZE WARNING", rest="until 9:00 AM · covers your county", area=COUNTIES, new=False, key="a"):
    return (label, rest, (110, 150, 255), new, key, area)


class TestCrawl(unittest.TestCase):
    def crawl(self, items, t0=1000.0):
        c = AlertCrawl()
        c.update(items, now=t0)
        c.t0 = t0
        return c

    def test_counties_go_by_and_the_name_stays(self):
        c = self.crawl([alert()])
        first = text(c.render(120, 1000.0, []))
        self.assertTrue(c.moving)
        self.assertEqual(len(first), 120)
        self.assertTrue(first.startswith(" FREEZE WARNING  until 9:00 AM · covers your county · County00"))
        held = text(c.render(120, 1000.0 + PAUSE - 0.1, []))
        self.assertEqual(held, first)                                  # still for a moment at first
        later = text(c.render(120, 1000.0 + PAUSE + 4.0, []))         # normal: 5 cells a second
        self.assertTrue(later.startswith(" FREEZE WARNING  until 9:00 AM · covers your county · "))
        self.assertEqual(later[55:], first[75:] + later[-20:])         # 20 cells along, to the left
        self.assertNotEqual(later, first)

    def test_speed_setting(self):
        c = self.crawl([alert()])
        slow = text(c.render(120, 1000.0 + PAUSE + 10, [], "slow"))
        self.assertAlmostEqual(c.step, 1 / 3)
        fast = text(c.render(120, 1000.0 + PAUSE + 10, [], "fast"))
        self.assertAlmostEqual(c.step, 1 / 8)
        self.assertNotEqual(slow, fast)

    def test_the_list_comes_round_again(self):
        c = self.crawl([alert()])
        loop = len(COUNTIES) + len("   ◆   ")
        a = text(c.render(120, 1000.0 + PAUSE + 1, []))
        b = text(c.render(120, 1000.0 + PAUSE + 1 + loop / 5, []))
        self.assertEqual(a, b)

    def test_short_list_sits_still(self):
        c = self.crawl([alert(area="Dallas; Tarrant")])
        line = text(c.render(120, 1050.0, []))
        self.assertFalse(c.moving)
        self.assertIn("Dallas; Tarrant", line)

    def test_all_fitting_sit_side_by_side(self):
        c = self.crawl([alert(area="Dallas"), ("STORM REPORT", "6:12 PM: hail near Plano", (120, 170, 255), False,
                                               "r", "")])
        line = text(c.render(200, 1050.0, []))
        self.assertFalse(c.moving)
        self.assertIn("FREEZE WARNING", line)
        self.assertIn("STORM REPORT", line)

    def test_several_take_turns_newest_first(self):
        items = [alert(), alert("TORNADO WARNING", "until 6:45 PM · radar indicated · 12 mi SW", "Dallas; Collin",
                                new=True, key="t")]
        c = AlertCrawl()
        c.update(items[:1], now=900.0)
        c.update(items, now=1000.0)                                    # the tornado warning just arrived
        line = text(c.render(160, 1000.5, []))
        self.assertIn("NEW  TORNADO WARNING", line)
        self.assertTrue(line.rstrip().endswith("1/2"))
        line = text(c.render(160, 1000.0 + STILL + 1, []))            # its turn is up: the freeze warning
        self.assertIn("FREEZE WARNING", line)
        self.assertTrue(line.rstrip().endswith("2/2"))
        self.assertTrue(c.moving)
        line = text(c.render(160, 1000.0 + STILL + TURN + 1, []))     # round again
        self.assertIn("TORNADO WARNING", line)

    def test_turns_carry_on_where_they_left_off(self):
        items = [alert("WIND ADVISORY", "until 6 PM", "Dallas", key="w"), alert()]
        c = self.crawl(items)
        cycle = STILL + TURN
        a = text(c.render(160, 1000.0 + STILL + 5, []))
        b = text(c.render(160, 1000.0 + cycle + STILL + 5, []))      # the same moment of its next turn
        self.assertNotEqual(a, b)                                       # ... further along the list

    def test_narrow_times_go_by_too(self):
        c = self.crawl([alert("SEVERE THUNDERSTORM WARNING", "until 6:45 PM · radar indicated · 70 mph wind · 12 mi SW")])
        line = text(c.render(70, 1000.0, []))
        self.assertEqual(len(line), 70)
        self.assertTrue(line.startswith(" SEVERE THUNDERSTORM WARNING  until 6:45 PM"))

    def test_calm(self):
        c = AlertCrawl()
        self.assertEqual(c.render(80, 1000.0, [("calm", "status")]), [("calm", "status")])
        self.assertFalse(c.moving)

    def test_items_carry_every_county(self):
        F = Fmt("imperial")
        a = {"event": "Freeze Warning", "sig": "W", "phen": "FZ", "area": COUNTIES, "end": time.time() + 3600, "rgb": (1, 2, 3),
             "key": "k", "in_zip": False, "in_county": True, "dist": 0.0, "src": "NWS", "tags": {}}
        items = ticker_items([a], [], dict(DEFAULTS), F)
        self.assertEqual(len(items), 1)
        label, rest, _rgb, _new, key, area, in_county = items[0]
        self.assertTrue(in_county)
        self.assertEqual(area, COUNTIES)                                # the whole list, not cut short
        self.assertNotIn("County00", rest)
        self.assertTrue(rest.startswith("until "))


class TestNoTopBanner(unittest.TestCase):
    def test_banner_gone(self):
        self.assertFalse(hasattr(ui, "alert_banner"))
        src = Path(os.path.join(os.path.dirname(SC.__file__), "app.py")).read_text(encoding="utf-8")
        self.assertNotIn("alert_banner", src)


class TestCards(unittest.TestCase):
    def test_card(self):
        TX.set_rounded(True)
        box = TX.card("Pollutants", [[("PM2.5", "text")]], 30, note="ug/m3", height=3)
        rows = [text(r) for r in box]
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(len(r) == 30 for r in rows))
        self.assertTrue(rows[0].startswith("╭─ Pollutants  ug/m3 ") and rows[0].endswith("╮"))
        self.assertEqual(rows[1], "│ PM2.5" + " " * 21 + " │")
        self.assertTrue(rows[-1].startswith("╰") and rows[-1].endswith("╯"))
        TX.set_rounded(False)
        self.assertTrue(text(TX.card("x", [], 10)[0]).startswith("┌"))
        TX.set_rounded(True)
        self.assertTrue(text(TX.card("x", [], 10, ascii_only=True)[0]).startswith("+-"))

    def test_note_dropped_when_no_room(self):
        row = text(TX.card("AQI · past 24 h", [], 30, note="high 52 at 12 AM · low 36")[0])
        self.assertNotIn("high", row)
        self.assertEqual(len(row), 30)

    def test_grid_rows_match(self):
        def build(n):
            return lambda w: [[("x" * w, "text")] for _ in range(n)]
        rows = ui.card_grid([[{"title": "a", "build": build(2)}, {"title": "b", "build": build(5)},
                              {"title": "c", "build": build(1)}],
                             [{"title": "d", "build": build(1), "span": 3}],
                             [{"title": "e", "build": build(1)}, None, None]], 160)
        lines = [text(r) for r in rows]
        self.assertEqual(len(lines), 7 + 3 + 3)
        self.assertTrue(all(len(ln) <= 160 for ln in lines))
        self.assertEqual(len(lines[0]), 160)
        self.assertEqual(lines[0].count("╭"), 3)
        self.assertEqual(lines[6].count("╰"), 3)                      # the short cards stretched to match
        self.assertEqual(lines[7].count("╭"), 1)
        self.assertEqual(len(lines[7]), 160)                           # a span-3 card: the whole width

    def test_fit_chart(self):
        self.assertEqual(SC.fit_chart(24, 48, 5), (False, 48))         # the scale would squeeze the columns
        self.assertEqual(SC.fit_chart(24, 60, 5), (True, 53))
        self.assertEqual(SC.fit_chart(24, 120, 5), (True, 101))
        self.assertEqual(SC.fit_chart(13, 48, 4, axis=False), (False, 39))

    def test_cat_label(self):
        self.assertEqual(SC.cat_label("Unhealthy for sensitive groups", 40), "Unhealthy for sensitive groups")
        self.assertEqual(SC.cat_label("Unhealthy for sensitive groups", 21), "Unhealthy (sensitive)")
        self.assertEqual(SC.cat_label("Unhealthy for sensitive groups", 16), "Sensitive groups")
        self.assertEqual(SC.cat_label("Moderate", 9), "Moderate")


class TestAirScreen(unittest.TestCase):
    def model(self):
        F = Fmt("imperial")
        now = time.time()
        h0 = int(now) // 3600 * 3600
        rows = [{"pol": p, "name": n, "conc": c, "conv": cv, "unit": un, "aqi": a, "owm": o}
                for p, n, c, cv, un, a, o in (("PM2.5", "Fine particles", 4.5, None, "", 52, 2),
                                              ("O3", "Ozone", 40.0, 20.4, "ppb", 17, 1),
                                              ("CO", "Carbon monoxide", 164.0, 0.14, "ppm", 2, 1))]
        uv = []
        for i in range(-12, 36):
            t = h0 + i * 3600
            hr = F.local(t).hour
            uv.append((t, max(0.0, 7 * math.sin((hr - 6.5) / 11 * math.pi)) if 6 <= hr <= 18 else 0.0))
        air = {"src": "OpenWeather", "t": now, "us": 152, "owm": 3, "dominant": "PM2.5", "rows": rows, "extra": [],
               "history": [(h0 - (23 - i) * 3600, 60 + i * 4, 8 + i) for i in range(24)],
               "forecast": [(h0 + i * 3600, 90 + (i % 30)) for i in range(96)]}
        return {"air": air, "air_om": {"uv": uv, "us": 98, "dust": 3.0, "aod": 0.12}, "errors": {}}, F

    def test_layouts(self):
        m, F = self.model()
        for W, H, ascii_only in ((160, 44, False), (200, 50, False), (120, 44, False), (90, 40, True), (60, 30, False)):
            v = types.SimpleNamespace(fmt=F, ascii=ascii_only)
            lines = [text(r) for r in SC.render_air(m, v, W, H)["lines"]]
            self.assertTrue(all(len(ln) <= W for ln in lines), (W, max(len(ln) for ln in lines)))
            joined = "\n".join(lines)
            for word in ("Air quality", "Pollutants", "Scales", "Sun safety", "What they are", "Unhealthy"):
                self.assertIn(word, joined, (W, word))
            self.assertIn("+-" if ascii_only else "╭─", joined)
            self.assertIn("Open-Meteo", joined)                    # the second opinion, OpenWeather being first
            self.assertIn("Dust 3 ug/m3", joined)
            if W >= 160:
                self.assertLessEqual(len(lines), H, W)                # the whole screen at once

    def test_charts_grow_into_the_room(self):
        m, F = self.model()
        v = types.SimpleNamespace(fmt=F, ascii=False)
        small = SC.render_air(m, v, 160, 34)["lines"]
        big = SC.render_air(m, v, 160, 60)["lines"]
        self.assertLessEqual(len(small), 34)
        self.assertGreater(len(big), len(small))

    def test_no_sidebar(self):
        m, F = self.model()
        out = SC.render_air(m, types.SimpleNamespace(fmt=F, ascii=False), 160, 44)
        self.assertNotIn("side", out)


if __name__ == "__main__":
    unittest.main()
