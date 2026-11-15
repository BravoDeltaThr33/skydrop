"""3.8: Alerts as cards with the radar top right, the ZIP code pinned to the front of the
crawl, and every screen's sections in rounded cards (no rules, no column bars)."""

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

from skydrop import alerts as AL  # noqa: E402
from skydrop import model as MD  # noqa: E402
from skydrop import screens as SC  # noqa: E402
from skydrop import text as TX  # noqa: E402
from skydrop import ui  # noqa: E402
from skydrop.config import DEFAULTS  # noqa: E402
from skydrop.fmt import Fmt  # noqa: E402
from skydrop.theme import ink_on  # noqa: E402
from skydrop.ticker import AlertCrawl  # noqa: E402

ZIP = "75201"
DESC = ("* WHAT...Snow expected. Total snow accumulations of 4 to 8 inches.\n\n"
        "* WHERE...Portions of north central Texas.\n\n"
        "* WHEN...From 6 PM this evening to noon CST Saturday.\n\n"
        "* IMPACTS...Travel could be very difficult.")
SVR = ("The National Weather Service in Fort Worth has issued a\n\n* Severe Thunderstorm Warning for...\n"
       "  Central Dallas County in north central Texas...\n\n* Until 615 PM CST.\n\n"
       "HAZARD...60 mph wind gusts and quarter size hail.\n\nSOURCE...Radar indicated.\n\n"
       "IMPACT...Hail damage to vehicles is expected.\n\n&&")


def text(segs):
    return "".join(t for t, _s in segs)


def lines_of(rows):
    return [text(r) for r in rows]


def an_alert(event="Winter Storm Warning", desc=DESC, area="Dallas; Tarrant", rgb=(255, 105, 180), **kw):
    now = time.time()
    a = {"event": event, "src": "NWS", "severity": "Severe", "urgency": "Expected", "certainty": "Likely",
         "msgtype": "Alert", "headline": None, "nws_headline": f"{event.upper()} IN EFFECT UNTIL NOON SATURDAY",
         "desc": desc, "instruction": "If you must travel, keep an extra flashlight, food, and water in your vehicle.",
         "note": None, "start": now - 600, "end": now + 6 * 3600, "sender": "NWS Fort Worth TX", "area": area,
         "extras": [], "rgb": rgb}
    a.update(kw)
    return a


class Pictures:
    """Stands in for the picture cache: blank text where a picture would go."""

    def block(self, slot, key, cols, rows, builder, bg="text", back=None):
        return [[(" " * cols, "text")] for _ in range(rows)]


def view(F, ascii_only=False):
    from skydrop.theme import Theme
    return types.SimpleNamespace(fmt=F, ascii=ascii_only, radar=None, gmode="ascii" if ascii_only else "blocks",
                                 theme=Theme(), images=Pictures())


def a_model(alerts):
    now = time.time()
    return {"alerts": alerts, "alerts_at": now, "errors": {}, "loc": {"zip": ZIP, "lat": 32.79, "lon": -96.80},
            "sbw": [{"name": "Severe Thunderstorm Warning", "inside": False, "dist": 42.0, "bearing": 250,
                     "rgb": (255, 150, 0), "expire": now + 1800, "hail": 1.0, "wind": 60}],
            "lsrs": [{"t": now - 3600, "type": "Hail", "mag": 1.0, "unit": "INCH", "city": "IRVING", "state": "TX",
                      "dist": 9.0, "bearing": 290, "remark": "Quarter size hail."}],
            "outlooks": [], "watches": []}


class TestCrawlPin(unittest.TestCase):
    """Since 3.10 your county's name follows the alert's (the ZIP code chip in front is gone)."""

    def test_county_after_the_alert(self):
        F = Fmt("imperial")
        a = {"event": "Tornado Warning", "sig": "W", "phen": "TO", "area": "Dallas; Collin", "end": time.time() + 1800,
             "rgb": (230, 20, 30), "key": "t", "in_zip": True, "in_county": True, "inside": True, "dist": 0.0,
             "src": "NWS", "tags": {}}
        items = AL.ticker_items([a], [], dict(DEFAULTS), F)
        self.assertEqual(len(items[0]), 7)
        self.assertTrue(items[0][6])
        self.assertNotIn("ZIP", items[0][1])                       # the county's name says it
        c = AlertCrawl()
        c.update(items, now=1000.0)
        c.county, c.county_zone = "Dallas County", "Dallas"
        line = text(c.render(140, 1000.5, []))
        self.assertTrue(line.startswith(" TORNADO WARNING   Dallas County  until "), line[:60])
        self.assertNotIn(ZIP, line)
        self.assertEqual(len(line), 140)
        c.update([], now=1001.0)                                    # the alert clears: the county goes with it
        self.assertEqual(c.render(140, 1002.0, [("calm", "status")]), [("calm", "status")])

    def test_no_county_outside_it(self):
        c = AlertCrawl()
        c.update([("WIND ADVISORY", "until 6 PM · 30 mi NW", (210, 180, 140), False, "w", "Denton", False)], now=10.0)
        c.county, c.county_zone = "Dallas County", "Dallas"
        line = text(c.render(120, 11.0, []))
        self.assertNotIn("Dallas County", line)
        self.assertTrue(line.startswith(" WIND ADVISORY  until 6 PM"), line[:40])


class TestAlertText(unittest.TestCase):
    def test_labelled_parts(self):
        parts = SC.alert_parts(DESC)
        self.assertEqual([p[0] for p in parts], ["What", "Where", "When", "Impacts"])
        self.assertTrue(parts[0][1].startswith("Snow expected."))

    def test_a_sentence_over_two_paragraphs(self):
        parts = SC.alert_parts(SVR)
        self.assertEqual(parts[0][0], "")
        self.assertTrue(parts[0][1].startswith("The National Weather Service in Fort Worth has issued a Severe "
                                               "Thunderstorm Warning for..."), parts[0][1])
        self.assertTrue(parts[0][1].endswith("north central Texas."))
        self.assertIn(("", "Until 615 PM CST."), parts)
        self.assertEqual([p[0] for p in parts if p[0]], ["Hazard", "Source", "Impact"])
        self.assertNotIn("&&", " ".join(p[1] for p in parts))

    def test_long_area_lists_cut_short(self):
        area = "; ".join(f"County{i:02d}" for i in range(60))
        rows = lines_of(SC.area_rows(area, 60))
        self.assertLessEqual(len(rows), 3)
        self.assertTrue(rows[0].startswith("Areas      County00"))
        self.assertRegex(rows[-1], r"and \d+ more$")
        self.assertEqual(lines_of(SC.area_rows("Dallas; Tarrant", 60)), ["Areas      Dallas; Tarrant"])


class TestAlertsScreen(unittest.TestCase):
    def render(self, alerts, W=193, H=49, ascii_only=False):
        F = Fmt("imperial")
        return SC.render_alerts(a_model(alerts), view(F, ascii_only), W, H)

    def test_cards_two_columns(self):
        out = self.render([an_alert(), an_alert("Wind Advisory", rgb=(210, 180, 140)),
                           an_alert("Severe Thunderstorm Warning", SVR, rgb=(255, 150, 0))])
        rows = lines_of(out["lines"])
        main_w = 193 - out["side_w"] - 1
        self.assertTrue(all(len(r) <= main_w for r in rows))
        self.assertTrue(rows[0].startswith("╭─ Winter Storm Warning   1 of 3"), rows[0][:50])
        joined = "\n".join(rows)
        for word in ("What       Snow expected", "Where      Portions", "In effect", "What to do",
                     "Warnings nearby", "Storm reports", "2 of 3", "3 of 3", "Hazard     60 mph"):
            self.assertIn(word, joined)
        self.assertNotIn("─" * 20 + "  ", rows[1])
        self.assertNotIn("covers your ZIP", joined)
        side = "\n".join(lines_of(out["side"]))
        self.assertIn("╭─ Storm risk scale", side)

    def test_chip_in_the_alerts_colour(self):
        out = self.render([an_alert()])
        head = out["lines"][0]
        chip = [st for t, st in head if "Winter Storm Warning" in t][0]
        self.assertEqual(chip[2], (255, 105, 180))
        self.assertEqual(chip[1], ink_on((255, 105, 180)))

    def test_radar_top_right(self):
        F = Fmt("imperial")
        v = view(F)
        v.radar = object()                          # a radar is there: a card for it, top right
        calls = []

        def block(v_, m_, cols, rows, slot="radar", **kw):
            calls.append((cols, rows, slot, kw.get("span")))
            return [[(" " * cols, "text")] for _ in range(rows)], None
        old = ui.radar_block, ui.mini_radar_panel
        ui.radar_block = block
        ui.mini_radar_panel = lambda v_, m_, w, h: [ui.section("Local radar", w)] + [[("  x", "text")]] * h
        try:
            out = SC.render_alerts(a_model([an_alert()]), v, 193, 49)
        finally:
            ui.radar_block, ui.mini_radar_panel = old
        rows = lines_of(out["lines"])
        self.assertIn("╭─ Regional radar", rows[0])
        x = rows[0].index("╭─ Regional radar")
        self.assertGreater(x, 60)                                    # beside the first alert, not under it
        cols, nrows, slot, span = calls[0]
        self.assertEqual((slot, span), ("regional", 480))
        self.assertLessEqual(nrows, 16)                              # smaller than before
        self.assertLess(cols, 70)

    def test_no_alerts(self):
        rows = lines_of(self.render([])["lines"])
        joined = "\n".join(rows)
        self.assertIn(f"No watches, warnings or advisories for ZIP code {ZIP}.", joined)
        self.assertTrue(rows[0].startswith("╭─ Alerts"))

    def test_narrow_and_ascii(self):
        for W, asc in ((100, False), (80, True), (120, False)):
            out = self.render([an_alert(), an_alert("Wind Advisory")], W=W, ascii_only=asc)
            rows = lines_of(out["lines"])
            self.assertTrue(all(len(r) <= W for r in rows), W)
            self.assertTrue(rows[0].startswith("+-" if asc else "╭─"))

    def test_printing(self):
        F = Fmt("imperial")
        out = SC.render_alerts(a_model([an_alert()]), view(F), 100, 40, printing=True)
        rows = lines_of(out["lines"])
        self.assertNotIn("side", out)
        self.assertIn("╭─ Storm risk scale", "\n".join(rows))


class TestColours(unittest.TestCase):
    def test_event_colours(self):
        self.assertEqual(AL._style("FZ", "W", "Freeze Warning")[1], (72, 61, 139))
        self.assertEqual(AL._style("TO", "W", "Tornado Warning")[1], AL.EVENT_STYLE["TO.W"][1])
        self.assertEqual(AL._style("", "", "Wind Advisory")[1], (210, 180, 140))
        self.assertEqual(AL._style("", "", "Some New Warning")[1], AL.SIG_COLOR["W"])

    def test_point_alerts_get_a_colour(self):
        feat = {"properties": {"event": "Frost Advisory", "parameters": {"VTEC": [
            "/O.NEW.KFWD.FR.Y.0001.261114T0600Z-261114T1500Z/"]}, "ends": None, "expires": None}}
        out = MD.build_alerts({"features": [feat]}, None)
        self.assertEqual(out[0]["rgb"], (100, 149, 237))

    def test_ink(self):
        self.assertEqual(ink_on((255, 228, 181)), (16, 16, 28))       # moccasin: dark text
        self.assertEqual(ink_on((72, 61, 139)), (255, 255, 255))       # dark slate blue: white text
        self.assertEqual(ink_on((0, 255, 255)), (16, 16, 28))


class TestCardBits(unittest.TestCase):
    def test_long_title_keeps_its_corner(self):
        for style in ("title", ("rgb", (0, 0, 0), (200, 0, 0), "b")):
            row = text(TX.card("A very long title for a very small card", [], 24, title_style=style)[0])
            self.assertEqual(len(row), 24)
            self.assertTrue(row.endswith("╮"), row)
            self.assertIn("…", row)

    def test_dedent_keeps_a_pictures_cells(self):
        pic = [(" ", "text"), ("  ##", "x")]                        # a one-cell indent, then a picture row
        self.assertEqual(ui.dedent(pic, 2), [("  ##", "x")])
        self.assertEqual(ui.dedent([("  Label", "label")], 2), [("Label", "label")])
        self.assertEqual(ui.dedent([("  ", "text"), ("Label", "label")], 2), [("Label", "label")])
        self.assertEqual(ui.dedent([("    deeper", "text")], 2), [("  deeper", "text")])

    def test_flow_cards_carry_on(self):
        body = [[(f"line {i}", "text")] for i in range(60)]
        rows = lines_of(SC.flow_cards([("Short", "", body[:5]), ("Long", "note", body)], 3, 40, width=124))
        joined = "\n".join(rows)
        self.assertIn("╭─ Long (continued)", joined)
        self.assertIn("line 59", joined)
        self.assertLess(len(rows), 40)                                # about a third each
        self.assertEqual(rows[0].count("╭"), 3)
        self.assertEqual(len(rows[0]), 124)


class TestNoRules(unittest.TestCase):
    """No rules across the screens, no bars between columns: cards instead."""

    def test_sources(self):
        here = os.path.dirname(SC.__file__)
        for name in ("screens.py", "screens2.py", "settings.py"):
            src = Path(os.path.join(here, name)).read_text(encoding="utf-8")
            self.assertNotIn("ui.subsection(", src, name)
            self.assertNotIn("ui.flow(", src, name)
            self.assertNotIn('ln[0][0].startswith(("━━"', src, name)
        self.assertFalse(hasattr(ui, "flow") or hasattr(ui, "subsection"))

    def test_sky_cards(self):
        F = Fmt("imperial")
        m = {"loc": {"lat": 32.79, "lon": -96.80, "zip": ZIP}, "errors": {}, "daily": [], "hourly": []}
        for W, asc in ((193, False), (150, False), (90, True)):
            out = SC.render_sky(m, view(F, asc), W, 49)
            rows = lines_of(out["lines"])
            joined = "\n".join(rows)
            corner = "+-" if asc else "╭─"
            for title in ("Sun", "Moon", "Day and night", "Week ahead"):
                self.assertIn(f"{corner} {title}", joined, (W, title))
            self.assertNotIn(" │ ", "\n".join(r for r in rows if not r.startswith(("│", "|"))))
            side = lines_of(out.get("side") or [])
            if W >= 150:
                self.assertIn("╭─ Coming up", "\n".join(side))


if __name__ == "__main__":
    unittest.main()
