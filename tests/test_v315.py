"""3.15: the Now screen's Next 12 hours as one chart, the temperature over a bar for each
hour precipitation may fall, colored by what falls (rain in greens, storms gold, snow
white, sleet pink, freezing rain magenta, a wintry mix violet)."""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp(prefix="sd-cache-"))
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sd-config-"))

from skydrop import VERSION  # noqa: E402
from skydrop import charts as C  # noqa: E402
from skydrop import columns as CO  # noqa: E402
from skydrop import precip as P  # noqa: E402
from skydrop import theme as T  # noqa: E402
from skydrop.crt import METER  # noqa: E402


def text(lines):
    return ["".join(t for t, _s in ln) for ln in lines]


class TestVersion(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(tuple(int(x) for x in VERSION.split(".")), (3, 15, 0))


class TestWords(unittest.TestCase):
    def test_types(self):
        cases = {
            "Rain Showers Likely": ("rain", 1), "Chance Light Rain": ("rain", 0), "Heavy Rain": ("rain", 2),
            "Patchy Drizzle": ("rain", 0), "Slight Chance Sprinkles": ("rain", 0),
            "Showers And Thunderstorms": ("storm", 1), "Snow Showers Likely": ("snow", 0), "Snow": ("snow", 1),
            "Heavy Snow": ("snow", 2), "Blizzard": ("snow", 2), "Sleet": ("sleet", 1), "Ice Pellets": ("sleet", 1),
            "Freezing Rain": ("ice", 1), "Freezing Drizzle": ("ice", 0), "Rain And Snow": ("mix", 1),
            "Wintry Mix Likely": ("mix", 1), "light shower sleet": ("sleet", 0), "heavy intensity rain": ("rain", 2),
        }
        for words, want in cases.items():
            self.assertEqual(P.from_words(words), want, words)
        for words in ("Mostly Cloudy", "Sunny", "", None, "Patchy Fog"):
            self.assertIsNone(P.from_words(words), words)

    def test_a_slight_chance_is_not_light(self):
        self.assertEqual(P.from_words("Slight Chance Rain Showers"), ("rain", 1))
        self.assertEqual(P.from_words("Slight Chance Light Rain"), ("rain", 0))


class TestHourType(unittest.TestCase):
    def test_nws_words_first(self):
        r = {"nws_desc": "Chance Freezing Rain", "wmo": 61, "kind": "rain", "temp": -1}
        self.assertEqual(P.hour_type(r), ("ice", 1))

    def test_weather_codes_say_how_hard(self):
        self.assertEqual(P.hour_type({"wmo": 61}), ("rain", 0))
        self.assertEqual(P.hour_type({"wmo": 65}), ("rain", 2))
        self.assertEqual(P.hour_type({"wmo": 75}), ("snow", 2))
        self.assertEqual(P.hour_type({"wmo": 67}), ("ice", 2))
        self.assertEqual(P.hour_type({"wmo": 95}), ("storm", 1))

    def test_grid_weather(self):
        both = [{"coverage": "chance", "weather": "rain", "intensity": "light"},
                {"coverage": "chance", "weather": "snow", "intensity": "light"}]
        self.assertEqual(P.hour_type({"nws_desc": "Cloudy", "grid_wx": both}), ("mix", 1))
        ice = [{"coverage": "likely", "weather": "freezing_rain", "intensity": "moderate"}]
        self.assertEqual(P.hour_type({"nws_desc": "Cloudy", "grid_wx": ice}), ("ice", 1))
        self.assertEqual(P.hour_type({"nws_desc": "Cloudy", "grid_wx": [{"weather": None}], "temp": 8}), ("rain", 0))

    def test_unnamed_hours_follow_the_temperature(self):
        self.assertEqual(P.hour_type({"desc": "Overcast", "wmo": 3, "temp": -3}), ("snow", 0))
        self.assertEqual(P.hour_type({"desc": "Overcast", "wmo": 3, "temp": 6}), ("rain", 0))
        self.assertEqual(P.hour_type({"temp": 2, "snow": 4.0, "rain": 0.0}), ("snow", 0))

    def test_amounts_lift_the_shade(self):
        self.assertEqual(P.hour_type({"nws_desc": "Rain", "rain": 9.0}), ("rain", 2))
        self.assertEqual(P.hour_type({"nws_desc": "Light Rain", "rain": 3.0}), ("rain", 1))
        self.assertEqual(P.hour_type({"nws_desc": "Rain", "rain": (24.0, 0, 6 * 3600)}), ("rain", 1))
        self.assertEqual(P.hour_type({"nws_desc": "Snow", "snowfall": (90.0, 0, 3 * 3600)}), ("snow", 2))

    def test_styles(self):
        self.assertEqual(P.style_of(("rain", 0)), "p_rain1")
        self.assertEqual(P.style_of(("rain", 2)), "p_rain3")
        self.assertEqual(P.style_of(("snow", 1)), "p_snow2")
        self.assertEqual(P.style_of(("ice", 2)), "p_ice")
        self.assertEqual(P.style_of(("sleet", 0)), "p_sleet")
        self.assertEqual(P.style_of(None), "p_rain2")


class TestKey(unittest.TestCase):
    def test_names_what_falls(self):
        spans = P.legend([("rain", 0), None, ("rain", 1), ("ice", 1)], 120)
        self.assertEqual("".join(t for t, _s in spans), "Precipitation  ■ Light rain   ■ Rain   ■ Freezing rain")
        self.assertEqual(spans[0], ("Precipitation  ", "label"))
        self.assertIn(("■", "p_ice"), spans)

    def test_in_the_order_they_come(self):
        kinds = [("storm", 1), ("rain", 2), ("rain", 0), ("sleet", 1), ("snow", 1), ("snow", 0)]
        line = "".join(t for t, _s in P.legend(kinds, 200))
        names = ["Storms", "Light rain", "Heavy rain", "Sleet", "Light snow", "Snow"]
        self.assertEqual([n for n in names if n in line], names)
        self.assertEqual(sorted(names, key=line.index), names)    # rain's shades together, lightest first

    def test_none(self):
        self.assertEqual("".join(t for t, _s in P.legend([None] * 12, 100)),
                         "Precipitation  None expected in the next 12 hours")

    def test_narrow(self):
        kinds = [("rain", 0), ("rain", 1), ("rain", 2), ("snow", 0), ("snow", 2), ("storm", 1)]
        wide = "".join(t for t, _s in P.legend(kinds, 200))
        self.assertIn("Heavy rain", wide)
        narrow = P.legend(kinds, 60)
        line = "".join(t for t, _s in narrow)
        self.assertLessEqual(len(line), 60)
        self.assertIn("Rain", line)
        self.assertNotIn("Light rain", line)
        self.assertFalse(line.endswith("■") or line.endswith(" "))
        tiny = "".join(t for t, _s in P.legend(kinds, 30))
        self.assertLessEqual(len(tiny), 30)
        self.assertTrue(tiny.startswith("Precipitation"))

    def test_ascii(self):
        spans = P.legend([("snow", 1)], 80, ascii_only=True)
        self.assertIn(("#", "p_snow2"), spans)


class TestColors(unittest.TestCase):
    def test_every_theme_has_them(self):
        for name in T.THEMES:
            th = T.Theme(name)
            seen = set()
            for style in P.NAMES:
                fg = th.spec(style)[0]
                self.assertGreaterEqual(T.contrast(fg, th.pal["bg"]), 3.0, f"{name}:{style}")
                seen.add(fg)
            self.assertEqual(len(seen), len(P.NAMES), name)            # each its own color

    def test_radar_colors(self):
        th = T.Theme("weatherstar")
        r, g, b = th.spec("p_rain2")[0]
        self.assertTrue(g > r + 80 and g > b + 80)                     # rain green
        self.assertTrue(min(th.spec("p_snow3")[0]) >= 240)              # snow white
        r, g, b = th.spec("p_ice")[0]
        self.assertTrue(r > g + 100 and b > g + 60)                     # ice magenta
        light = [sum(th.spec(s)[0]) for s in ("p_rain1", "p_rain2", "p_rain3")]
        self.assertEqual(light, sorted(light, reverse=True))           # light rain the palest green


class TestBand(unittest.TestCase):
    def setUp(self):
        self.saved = dict(METER)
        METER.update({"blocks": True, "ascii": False, "style": "led"})

    def tearDown(self):
        METER.clear()
        METER.update(self.saved)

    def test_text_bars_under_the_area(self):
        vals = [10, 12, 14, 16, 14, 12]
        bars = [(1, 0.5, "p_rain2", "50%"), (3, 1.0, "p_snow2", "100%"), (5, 0.05, "p_ice", "5%")]
        lines = CO.area_chart(vals, 60, 6, lo=0, hi=16, style="t_mild", tops=["10"] * 6, band=3, bars=bars,
                              gutter=6, title="Temp", labels=["a"] * 6)
        rows = text(lines)
        self.assertEqual(len(rows), 1 + 6 + 2)                         # values, chart, base, labels
        area = [i for i, ln in enumerate(lines) if any(isinstance(s, tuple) and s[0] == "fade" for _t, s in ln)]
        self.assertEqual(max(area), 3)                                 # the area: rows 1-3; the band: 4-6
        self.assertIn("100%", rows[4])                                 # over a full bar, the band's top row
        self.assertIn("50%", rows[5])                                  # over a half bar
        self.assertTrue(any(s == "p_snow2" and "█" in t for t, s in lines[6]))
        self.assertTrue(any(s == "p_snow2" and "█" in t for t, s in lines[5]))
        self.assertFalse(any(s == "p_rain2" and "█" in t for t, s in lines[5]))
        self.assertTrue(any(s == "p_ice" for _t, s in lines[6]))       # a sliver for 5%
        self.assertIn("5%", rows[5])

    def test_ascii_bars(self):
        METER.update({"ascii": True})
        lines = CO.area_chart([5, 6, 7], 30, 6, lo=0, hi=7, band=3, bars=[(1, 1.0, "p_rain2", "90%")])
        rows = text(lines)
        self.assertTrue(all(ch.isascii() for r in rows for ch in r))
        self.assertIn("#", rows[-2])
        self.assertIn("90%", "\n".join(rows))

    def test_labels_kept_apart(self):
        bars = [(i, 0.3, "p_rain2", "30%") for i in range(12)]
        rows = text(CO.area_chart(list(range(12)), 30, 6, lo=0, hi=12, band=3, bars=bars))
        line = next(r for r in rows if "%" in r)
        self.assertNotIn("%3", line)                                   # never run together
        self.assertGreater(line.count("30%"), 1)

    def test_picture(self):
        bg = (18, 34, 122)
        green, white = (64, 206, 92), (255, 255, 255)
        img = C.area_image([10, 12, 14, 12], 0, 14, 400, 140, bg, lambda p, v: (240, 200, 80), cols=40, rows=7,
                           head=1, band=3, bars=[(0, 1.0, green, "100%"), (2, 0.5, white, "50%")])
        self.assertEqual(img.size, (400, 140))

        def where(rgb):
            return [(x, y) for y in range(140) for x in range(400)
                    if sum(abs(a - b) for a, b in zip(img.getpixel((x, y)), rgb)) < 24]
        g, w = where(green), where(white)
        self.assertTrue(g and w)
        self.assertEqual(max(y for _x, y in g), 139)                    # standing on the base
        self.assertGreaterEqual(min(y for _x, y in g), 140 - 60)        # inside the band (3 rows of 20)
        self.assertLess(max(x for x, _y in g), 100)                     # the first hour's quarter
        self.assertTrue(all(200 <= x < 300 for x, _y in w))
        self.assertLess(max(y for _x, y in g) - min(y for _x, y in g), 60)
        tall = max(y for _x, y in g) - min(y for _x, y in g)
        half = max(y for _x, y in w) - min(y for _x, y in w)
        self.assertGreater(tall, half * 1.6)                            # as tall as the chance
        # over the band, the area's shade has faded out: the card shows between the bars
        y = 140 - 60 + 2
        self.assertEqual(img.getpixel((150, y)), bg)

    def test_no_band_no_change(self):
        a = C.area_image([3, 5, 4], 0, 6, 120, 60, (0, 0, 0), lambda p, v: (200, 200, 200), cols=12, rows=3)
        b = C.area_image([3, 5, 4], 0, 6, 120, 60, (0, 0, 0), lambda p, v: (200, 200, 200), cols=12, rows=3,
                         band=0, bars=[(0, 1.0, (0, 255, 0), "x")])
        self.assertEqual(a.tobytes(), b.tobytes())


class TestNow(unittest.TestCase):
    def test_legend_follows_the_hours(self):
        from skydrop import screens as SC
        from skydrop.fmt import Fmt
        h0 = int(time.time()) // 3600 * 3600
        words = ["Rain"] * 4 + ["Freezing Rain"] * 3 + ["Snow"] * 5
        rows = [{"t": h0 + i * 3600, "temp": 1 - i * 0.5, "pop": 70, "nws_desc": w} for i, w in enumerate(words)]
        lines = SC.next_hours({"hourly": rows}, Fmt("imperial"), 130)
        key = text(lines)[-1]
        self.assertIn("Rain", key)
        self.assertIn("Freezing rain", key)
        self.assertIn("Snow", key)
        self.assertLess(key.index("Rain"), key.index("Freezing rain"))
        self.assertLess(key.index("Freezing rain"), key.index("Snow"))


if __name__ == "__main__":
    unittest.main()
