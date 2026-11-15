"""3.6: LED column charts (since 3.10 the area charts of Now's next 12 hours, the UV index and
the month's highs, and a station's last 24 hours in solid bars) and the Settings screen's
sections that open and close."""

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

from skydrop import columns as CO  # noqa: E402
from skydrop import screens as SC  # noqa: E402
from skydrop import screens2 as SC2  # noqa: E402
from skydrop import ui  # noqa: E402
from skydrop.config import DEFAULTS  # noqa: E402
from skydrop.crt import METER  # noqa: E402
from skydrop.fmt import Fmt  # noqa: E402
from skydrop.settings import FIELDS, GLOBAL_BUTTONS, SECTION_OF, Settings  # noqa: E402
from skydrop.term import KEY_DOWN, KEY_ENTER, KEY_LEFT, KEY_NPAGE, KEY_PPAGE, KEY_RIGHT, KEY_UP  # noqa: E402

LED = "\U0001FB39"
LED_NARROW = "\U0001FB13"


def text(lines):
    return ["".join(t for t, _s in ln) for ln in lines]


def styles(lines, ch):
    """The style of each cell drawn with `ch`, row by row."""
    out = []
    for ln in lines:
        for t, s in ln:
            out += [s] * t.count(ch)
    return out


def bar_styles(lines):
    """The styles solid bars are drawn in (since 3.13: the AQI and PM2.5 charts)."""
    out = []
    for ln in lines:
        for t, s in ln:
            out += [s] * sum(1 for ch in t if ch in "█▉" + CO.EIGHTHS[1:])
    return out


def area_styles(lines):
    """The styles an area chart fills its cells with (since 3.10: Now, UV and the month's
    highs), a fade or a guide over the fill taken back to the fill's own style."""
    out = []
    for ln in lines:
        for t, s in ln:
            while isinstance(s, tuple) and s and s[0] in ("fade", "over"):
                s = s[1] if s[0] == "fade" else s[2]
            out += [s] * sum(1 for ch in t if ch in CO.EIGHTHS[1:])
    return out


class MeterState(unittest.TestCase):
    def setUp(self):
        self.saved = dict(METER)
        METER.update({"blocks": True, "ascii": False, "style": "led"})

    def tearDown(self):
        METER.clear()
        METER.update(self.saved)


class TestColumnChart(MeterState):
    def test_heights_follow_the_values(self):
        lines = CO.column_chart([0, 25, 50, 100], 40, 4, lo=0, hi=100, base=False)
        rows = text(lines)
        self.assertEqual(len(rows), 4)
        # each column's lit cells, counted down its slot
        slot, body = CO.layout(4, 40)
        heights = []
        for i in range(4):
            x = i * slot
            heights.append(sum(1 for r in rows if len(r) > x and r[x] == LED))
        self.assertEqual(heights, [0, 1, 2, 4])

    def test_anything_above_lo_lights_a_segment(self):
        rows = text(CO.column_chart([1, 100], 20, 5, lo=0, hi=100, base=False))
        self.assertEqual(rows[-1].count(LED) > 0 and rows[-1][0], LED)

    def test_tops_sit_over_their_columns(self):
        rows = text(CO.column_chart([50, 100], 20, 4, lo=0, hi=100, tops=["50", "100"], base=False))
        self.assertIn("100", rows[0])                 # the full column's value in the row above the plot
        r50 = next(i for i, r in enumerate(rows) if "50" in r and "100" not in r)
        self.assertEqual(rows[r50 + 1].count(LED) > 0, True)

    def test_labels_base_and_now(self):
        lines = CO.column_chart([1, 2, 3], 30, 3, labels=["a", "b", "c"], now=None)
        rows = text(lines)
        self.assertTrue(rows[-2].strip().startswith("─"))
        self.assertEqual(rows[-1].split(), ["a", "b", "c"])

    def test_crowded_labels_drop_out(self):
        rows = text(CO.column_chart(list(range(1, 25)), 30, 3, labels=[f"{h}PM" for h in range(24)]))
        shown = rows[-1].split()
        self.assertLess(len(shown), 24)
        self.assertEqual(len(shown), len(set(shown)))

    def test_axis_one_mark_a_row(self):
        lines = CO.column_chart([10, 20], 30, 3, lo=0, hi=500,
                                axis=[(500, "500"), (300, "300"), (200, "200"), (150, "150"), (100, "100"), (50, "50")])
        rows = text(lines)
        marks = [r for r in rows if "┤" in r]
        self.assertTrue(1 <= len(marks) <= 3)
        self.assertTrue(all(r.count("┤") == 1 for r in marks))
        self.assertIn("500", marks[0])                # the first given wins a shared row
        self.assertNotIn("50 ", rows[-2][:4])

    def test_guides_under_the_columns(self):
        rows = text(CO.column_chart([100, 10], 20, 4, lo=0, hi=100, guides=[(50, "faint")], base=False))
        guide = [r for r in rows if "┄" in r]
        self.assertEqual(len(guide), 1)
        self.assertIn(LED, guide[0])                  # the tall column runs through it

    def test_per_column_styles(self):
        lines = CO.column_chart([10, 20], 20, 2, lo=0, hi=20, style=["aqi1", "aqi4"], base=False)
        self.assertEqual(set(styles(lines, LED)), {"aqi1", "aqi4"})
        lines = CO.column_chart([10, 60], 20, 2, lo=0, hi=60, style=lambda v: "hot" if v > 50 else "cold", base=False)
        self.assertEqual(set(styles(lines, LED)), {"hot", "cold"})

    def test_none_is_no_column(self):
        rows = text(CO.column_chart([None, 5, None], 30, 3, lo=0, hi=5, base=False))
        slot, _b = CO.layout(3, 30)
        self.assertNotIn(LED, "".join(r[:slot] for r in rows))

    def test_ascii_and_looks(self):
        METER["ascii"] = True
        rows = text(CO.column_chart([5, 10], 20, 3, lo=0, hi=10))
        self.assertTrue(all(ord(ch) < 128 for r in rows for ch in r))
        self.assertIn("#", "".join(rows))
        METER["ascii"] = False
        METER["style"] = "solid"
        rows = text(CO.column_chart([2.5, 10], 20, 2, lo=0, hi=10, base=False))
        self.assertIn("█", "".join(rows))
        self.assertTrue(any(ch in "▁▂▃▄▅▆▇" for ch in "".join(rows)))
        METER["style"] = "shade"
        rows = text(CO.column_chart([10], 10, 5, lo=0, hi=10, base=False))
        self.assertEqual([r.strip()[:1] for r in rows], ["░", "▒", "▓", "█", "█"])
        METER["style"] = "led"
        METER["blocks"] = False
        rows = text(CO.column_chart([10], 10, 2, lo=0, hi=10, base=False))
        self.assertIn("▄", "".join(rows))

    def test_one_cell_columns_stay_apart(self):
        rows = text(CO.column_chart([5] * 20, 20, 2, lo=0, hi=5, base=False))
        self.assertIn(LED_NARROW, rows[-1])
        self.assertNotIn(LED, rows[-1])

    def test_empty_top_row_is_dropped(self):
        lines = CO.column_chart([10, 20], 20, 4, lo=0, hi=100, tops=["10", "20"], base=False)
        self.assertTrue(lines[0])


class TestNowCharts(MeterState):
    def setUp(self):
        super().setUp()
        self.F = Fmt("imperial")
        self.h0 = int(time.time()) // 3600 * 3600

    def hourly(self, pops, kinds=None):
        return [{"t": self.h0 + i * 3600, "temp": 10 + i * 0.5, "pop": p, "kind": (kinds or {}).get(i, "rain")}
                for i, p in enumerate(pops)]

    def test_twelve_hours_temperature_and_rain(self):
        m = {"hourly": self.hourly([0, 0, 20, 60, 90, 40, 0, 0, 0, 0, 0, 0, 0, 0])}
        rows = text(SC.next_hours(m, self.F, 109))
        joined = "\n".join(rows)
        self.assertIn("Temp", joined)
        self.assertIn("Rain", joined)
        self.assertIn("90%", joined)
        self.assertIn("Now", rows[-1])
        self.assertEqual(sum(1 for r in rows if "─" in r), 1)     # one base line, shared
        for r in rows:
            self.assertLessEqual(len(r), 109)

    def test_dry_hours_say_so(self):
        m = {"hourly": self.hourly([0] * 14)}
        rows = text(SC.next_hours(m, self.F, 100))
        self.assertIn("None expected in the next 12 hours", rows[-1])
        self.assertIn("Now", rows[-2])

    def test_snow_hours(self):
        m = {"hourly": self.hourly([50] * 14, {i: "snow" for i in range(14)})}
        lines = SC.next_hours(m, self.F, 100)
        self.assertIn("Snow", "\n".join(text(lines)))
        self.assertIn("snow", area_styles(lines))
        m = {"hourly": self.hourly([50] * 14, {3: "snow"})}
        self.assertIn("Precip", "\n".join(text(SC.next_hours(m, self.F, 100))))

    def test_old_hours_are_skipped(self):
        rows = self.hourly([0] * 14)
        rows = [dict(r, t=r["t"] - 3 * 3600) for r in rows]
        lines = SC.next_hours({"hourly": rows}, self.F, 100)
        self.assertEqual(text(lines)[-2].split().count("Now"), 1)

    def test_temperatures_in_their_colors(self):
        m = {"hourly": [{"t": self.h0 + i * 3600, "temp": t, "pop": 0} for i, t in enumerate([-10, 0, 10, 20, 30, 40])]}
        sts = set(area_styles(SC.next_hours(m, self.F, 100)))
        self.assertTrue({"t_frigid", "t_cold", "t_cool", "t_mild", "t_hot", "t_scorch"} <= sts, sts)

    def test_next_hour(self):
        now = time.time()
        dry = [(now + k * 60, 0.0) for k in range(60)]
        self.assertEqual(len(SC.next_hour(dry, 100)), 1)
        wet = [(now + k * 60, 0.0 if k < 20 else 3.0 if k < 40 else 9.0) for k in range(60)]
        rows = text(SC.next_hour(wet, 100))
        self.assertIn("starting in about 20 min", rows[0])
        self.assertTrue(any("heavy" in r for r in rows) and any("light" in r for r in rows))
        self.assertIn("+15m", rows[-1])

    def test_now_screen_has_no_six_hour_rows(self):
        src = Path(SC.__file__).read_text(encoding="utf-8")
        self.assertNotIn("Next 6 hr", src)
        self.assertNotIn("Rain 6 hr", src)
        self.assertNotIn('"nowext"', src)


class TestAirCharts(MeterState):
    def setUp(self):
        super().setUp()
        self.F = Fmt("imperial")
        self.now = time.time()
        self.h0 = int(self.now) // 3600 * 3600

    def test_aqi_scale_and_colors(self):
        series = [(self.h0 - (23 - i) * 3600, 30 + i * 4) for i in range(24)]
        lines = SC.aqi_chart(series, self.F, 110, self.now)
        rows = text(lines)
        self.assertTrue(any(r.lstrip().startswith("150") for r in rows))      # the top of the highest category
        self.assertIn("122", "\n".join(rows))                                  # the peak over its column
        self.assertTrue({"aqi1", "aqi2", "aqi3"} <= set(bar_styles(lines)))
        self.assertIn("Now", rows[-1])

    def test_aqi_top(self):
        self.assertEqual([SC.aqi_top(v) for v in (10, 99, 101, 151, 250, 400)], [100, 100, 150, 200, 300, 500])

    def test_pm_chart(self):
        series = [(self.h0 - (23 - i) * 3600, 2 + i) for i in range(24)]
        lines = SC.pm_chart(series, self.F, 110, self.now)
        rows = text(lines)
        self.assertTrue(any(r.lstrip().startswith("9 ") or r.lstrip().startswith("9┤") for r in rows))
        self.assertIn("25.0", "\n".join(rows))
        self.assertTrue({"aqi1", "aqi2"} <= set(bar_styles(lines)))
        self.assertEqual(SC.pm_chart([(self.h0, None)], self.F, 110, self.now), [])

    def test_round_top(self):
        self.assertEqual([SC.round_top(v) for v in (0, 0.7, 9, 12, 26, 140)], [1, 0.8, 10, 15, 30, 150])

    def test_uv_chart_trims_the_night(self):
        day = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        rows = [(day + h * 3600, max(0.0, 8 * math.sin((h - 7) / 11 * math.pi)) if 7 <= h <= 18 else 0.0)
                for h in range(24)]
        lines = SC.uv_chart(rows, self.F, 110, self.now)
        drawn = text(lines)
        self.assertTrue(any(r.lstrip().startswith("11") for r in drawn))
        self.assertTrue({"good", "warn"} <= set(area_styles(lines)))
        import re
        hours = [int(h) % 12 + (12 if ap == "PM" else 0) for h, ap in re.findall(r"(\d+) (AM|PM)", drawn[-1])]
        self.assertTrue(hours)
        self.assertTrue(all(5 <= h <= 21 for h in hours), drawn[-1])          # the night's hours left out

    def test_air_screen_cards(self):
        h0 = self.h0
        air = {"src": "Open-Meteo (CAMS)", "t": self.now, "us": 52, "dominant": "PM2.5", "rows": [], "extra": [],
               "history": [(h0 - (24 - i) * 3600, 40 + i, 5.0 + i / 3) for i in range(24)],
               "forecast": [(h0 + i * 3600, 50 + (i % 9)) for i in range(72)]}
        uv = [(h0 + i * 3600, 4.0) for i in range(-6, 30)]
        v = types.SimpleNamespace(fmt=self.F, ascii=False)
        out = SC.render_air({"air": air, "air_om": {"uv": uv}, "errors": {}}, v, 160, 44)
        rows = text(out["lines"])
        titles = [t for t in ("Air quality", "Pollutants", "Scales", "AQI · past 24 h", "AQI · next 24 h",
                              "PM2.5 · past 24 h", "Daily peak AQI", "Sun safety", "What they are")
                  if any("─ " + t in r for r in rows)]
        self.assertEqual(len(titles), 9, titles)
        self.assertTrue(any("UV index · " in r for r in rows))
        self.assertLessEqual(len(rows), 44)                                    # it all fits
        self.assertNotIn("▁▂", "\n".join(rows))                               # no sparklines left


class TestOtherCharts(MeterState):
    def test_station_last_day(self):
        F = Fmt("imperial")
        now = time.time()
        hist = [{"t": now - k * 1200, "temp": 10 + 5 * math.sin(k / 20), "wind": 3 + k % 5,
                 "press": 1015 + k / 30} for k in range(72)]
        rows = text(SC2.last_day_charts(hist, F, 60, now))
        joined = "\n".join(rows)
        for word in ("Temp", "Wind", "Press", "Now", "wind up to", "pressure"):
            self.assertIn(word, joined)
        self.assertEqual(SC2.last_day_charts([], F, 60, now), [])

    def test_month_chart(self):
        F = Fmt("imperial")
        days = [{"date": dt.date(2026, 11, d), "hi": 18 + d % 5, "nhi": 20} for d in range(1, 14)]
        rows = text(SC.month_chart(days, F, 56))
        self.assertTrue(any("┄" in r for r in rows))                         # the normal, dotted
        self.assertEqual(rows[-1].split(), ["1", "5", "10"])
        self.assertTrue(any(r.lstrip().startswith("68°") for r in rows))      # 20 C, the normal, on the scale

    def test_time_labels(self):
        F = Fmt("imperial")
        h0 = int(time.time()) // 3600 * 3600
        times = [h0 + i * 3600 for i in range(24)]
        labels = ui.time_labels(times, F, 4, now=h0 + 10)
        self.assertEqual(labels[0], "Now")
        named = [x for x in labels[1:] if x]
        self.assertTrue(all(F.local(t).hour % 3 == 0 for t, x in zip(times[1:], labels[1:]) if x))
        self.assertTrue(named and all(" " in x for x in named))               # "3 PM", room for it
        short = ui.time_labels(times, F, 1, room=6)
        self.assertTrue(all(" " not in x for x in short if x))                # "3p"


class FakeApp:
    def __init__(self):
        self.cfg = dict(DEFAULTS)
        self.ascii = False
        self.backs = 0

    def back(self):
        self.backs += 1


class TestSettingsSections(unittest.TestCase):
    def setUp(self):
        self.st = Settings(FakeApp())
        self.titles = [it[1] for it in FIELDS if it[0] == "sec"]

    def focus(self):
        return self.st.items[self.st.focus]

    def test_all_closed_at_first(self):
        st = self.st
        self.assertEqual([it[1] for it in st.items if it[0] == "sec"], self.titles)
        self.assertEqual([it[1] for it in st.items if it[0] != "sec"], list(GLOBAL_BUTTONS))
        self.assertEqual(st.open, set())

    def test_every_row_has_a_section(self):
        for it in FIELDS:
            if it[0] == "sec" or (it[0] == "btn" and it[1] in GLOBAL_BUTTONS):
                self.assertNotIn(it, SECTION_OF)
            else:
                self.assertIn(SECTION_OF[it], self.titles, it)
        display = [it[1] for it in FIELDS if SECTION_OF.get(it) == "Display"]
        for key in ("units", "theme", "background", "rounded_borders", "meter_style", "crt", "crt_font",
                    "crt_font_size", "crt_card", "clock_seconds"):
            self.assertIn(key, display)

    def test_enter_opens_and_closes(self):
        st = self.st
        st.focus = st.items.index(("sec", "Display"))
        st.nav_key(KEY_ENTER)
        self.assertEqual(st.open, {"Display"})
        self.assertEqual(self.focus(), ("sec", "Display"))
        shown = [it[1] for it in st.items if SECTION_OF.get(it) == "Display"]
        self.assertEqual(shown[0], "units")
        st.nav_key(KEY_DOWN)
        self.assertEqual(self.focus()[1], "units")
        st.nav_key(KEY_ENTER)                     # a choice: Enter changes it
        self.assertEqual(st.draft["units"], "metric")
        st.nav_key("\x1b")                        # Esc closes the section around it
        self.assertEqual(st.open, set())
        self.assertEqual(self.focus(), ("sec", "Display"))

    def test_arrows_on_titles(self):
        st = self.st
        st.focus = st.items.index(("sec", "Radar"))
        st.nav_key(KEY_RIGHT)
        self.assertEqual(st.open, {"Radar"})
        st.nav_key(KEY_RIGHT)
        self.assertEqual(self.focus()[1], "radar_source")
        st.nav_key(KEY_UP)
        st.nav_key(KEY_LEFT)
        self.assertEqual(st.open, set())

    def test_page_keys_go_title_to_title(self):
        st = self.st
        st.set_all(True)
        st.focus_key("theme")
        st.nav_key(KEY_NPAGE)
        self.assertEqual(self.focus(), ("sec", "Tornado alarm"))
        st.nav_key(KEY_DOWN)
        st.nav_key(KEY_PPAGE)
        self.assertEqual(self.focus(), ("sec", "Tornado alarm"))
        st.nav_key(KEY_PPAGE)
        self.assertEqual(self.focus(), ("sec", "Display"))

    def test_open_and_close_all(self):
        st = self.st
        st.nav_key("+")
        self.assertEqual(st.open, set(self.titles))
        st.focus_key("radar_span")
        st.nav_key("-")
        self.assertEqual(st.open, set())
        self.assertEqual(self.focus(), ("sec", "Radar"))

    def test_focus_key_opens_the_section(self):
        st = self.st
        self.assertIsNotNone(st.focus_key("nws_user_agent"))
        self.assertEqual(st.open, {"National Weather Service"})
        self.assertEqual(self.focus()[1], "nws_user_agent")

    def test_find_opens_and_closes(self):
        st = self.st
        st.nav_key("/")
        for ch in "opacity":
            st.edit_key(ch)
        self.assertEqual(self.focus()[1], "radar_opacity")
        self.assertEqual(st.open, {"Radar"})
        st.edit_key("\x1b")
        self.assertEqual(st.open, set())
        self.assertEqual(self.focus(), ("sec", "Location"))
        st.nav_key("/")
        for ch in "seconds":
            st.edit_key(ch)
        first = self.focus()
        st.edit_key(KEY_DOWN)                     # the next match: its section opens, the first's closes
        self.assertNotEqual(self.focus(), first)
        self.assertEqual(len(st.open), 1)
        st.edit_key(KEY_ENTER)
        self.assertEqual(st.open, {SECTION_OF[self.focus()]})

    def test_find_section_title(self):
        st = self.st
        st.nav_key("/")
        for ch in "voice and":
            st.edit_key(ch)
        self.assertEqual(self.focus(), ("sec", "Voice and sound"))

    def test_quick_zip_opens_location_then_closes_it(self):
        st = self.st
        st.begin_edit("zip", quick=True)
        self.assertEqual(st.open, {"Location"})
        self.assertTrue(st.editing)
        st.edit_key("\x1b")
        self.assertEqual(st.open, set())
        self.assertEqual(st.app.backs, 1)

    def test_quick_zip_keeps_an_open_location(self):
        st = self.st
        st.toggle_section("Location", True)
        st.begin_edit("zip", quick=True)
        st.edit_key("\x1b")
        self.assertEqual(st.open, {"Location"})

    def test_deleting_a_source_keeps_the_place(self):
        st = self.st
        st.toggle_section("Data sources", True)
        srcs = [it for it in st.items if it[0] == "src"]
        st.focus = st.items.index(srcs[2])
        st.nav_key("x")
        self.assertIn(srcs[2][1], st.draft["__removed__"])
        self.assertEqual(self.focus(), srcs[3])

    def test_closed_title_counts_unsaved(self):
        st = self.st
        st.draft["units"] = "metric"
        row = "".join(t for t, _s in st.title_row(("sec", "Display"), 80, st.dirty()))
        self.assertIn("19 settings", row)
        self.assertIn("1 unsaved", row)
        row = "".join(t for t, _s in st.title_row(("sec", "Data sources"), 80, st.dirty()))
        self.assertRegex(row, r"\d+ sources, \d+ on")

    def test_buttons_stay_at_the_foot(self):
        st = self.st
        st.set_all(True)
        self.assertEqual([it[1] for it in st.items[-3:]], list(GLOBAL_BUTTONS))
        st.set_all(False)
        self.assertEqual([it[1] for it in st.items[-3:]], list(GLOBAL_BUTTONS))


if __name__ == "__main__":
    unittest.main()
