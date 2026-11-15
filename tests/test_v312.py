"""3.12: richer pictures where the terminal draws them: shaded weather icons, the big
temperature in shaded numerals, the Next 48 hours chart (smooth lines, night shading,
tagged extremes), the 10-Day chart (a ribbon between smooth highs and lows) and its range
pills, a realistic moon, and the daylight-through-the-year chart."""

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
from skydrop import charts as C  # noqa: E402
from skydrop import icons  # noqa: E402
from skydrop import screens as SC  # noqa: E402
from skydrop import theme as T  # noqa: E402
from skydrop import ui  # noqa: E402
from skydrop.fmt import Fmt  # noqa: E402
from skydrop.text import restyle  # noqa: E402

BG = (17, 34, 120)


def lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


class Pictures:
    def __init__(self, pixels=True):
        self.pixels = pixels
        self.asked = {}

    def block(self, slot, key, cols, rows, builder, bg="text", back=None):
        self.asked[slot] = (cols, rows, builder, key)
        if self.pixels:
            return [[(" " * cols, ("@img", slot, r, bg))] for r in range(rows)]
        return [[(" " * cols, bg)] for _ in range(rows)]


def view(pixels=True):
    return types.SimpleNamespace(fmt=Fmt("imperial"), ascii=False, theme=T.Theme(), images=Pictures(pixels),
                                 gmode="kitty" if pixels else "blocks", sel={"daily": 0}, meter_look=lambda: "led",
                                 radar=None, cfg={})


def imgs(lines):
    return [s for ln in lines for _t, s in ln if isinstance(s, tuple) and s and s[0] == "@img"]


class TestVersion(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(tuple(int(x) for x in VERSION.split(".")), (3, 12, 0))


class TestIcons(unittest.TestCase):
    def test_sun_shaded_and_glowing(self):
        im = icons.pixel_icon("clear", True, 130, 100, BG)
        self.assertEqual(im.size, (130, 100))
        center = im.getpixel((62, 46))
        rim = im.getpixel((65 + 24, 50))
        self.assertGreater(lum(center), lum(rim))                         # bright in the middle, gold at the rim
        near = im.getpixel((65, 50 - 38))                                # between rays, just off the disc
        self.assertNotEqual(near, BG)                                     # the glow
        for corner in ((0, 0), (129, 0), (0, 99), (129, 99)):
            self.assertLess(sum(abs(a - b) for a, b in zip(im.getpixel(corner), BG)), 12)   # faded by the edge

    def test_cloud_lit_from_above(self):
        im = icons.pixel_icon("showers", True, 130, 100, BG)                # one white cloud over the drops
        col = [im.getpixel((65, y)) for y in range(58)]
        cloud = [c for c in col if lum(c) > 110]
        self.assertGreater(len(cloud), 10)
        self.assertGreater(lum(cloud[0]), lum(cloud[-1]))                # lighter at the top

    def test_every_kind_draws(self):
        for kind in icons.KINDS:
            for day in (True, False):
                im = icons.pixel_icon(kind, day, 60, 46, BG)
                self.assertEqual(im.size, (60, 46))
        im = icons.pixel_icon("thunder", True, 60, 46, (236, 232, 220), outline=(30, 30, 50))
        self.assertTrue(any(lum(im.getpixel((x, y))) < 80 for x in range(60) for y in range(46)))   # the rim


class TestBigNumber(unittest.TestCase):
    def test_numerals_shaded(self):
        rgb = (250, 230, 120)
        im = icons.number_image("57°", rgb, 170, 100, BG)
        self.assertEqual(im.size, (170, 100))
        lit = [(x, y) for x in range(170) for y in range(100) if lum(im.getpixel((x, y))) > 150]
        self.assertGreater(len(lit), 600)
        ys = sorted(y for _x, y in lit)
        top = [im.getpixel((x, y)) for x, y in lit if y < ys[len(ys) // 5]]
        foot = [im.getpixel((x, y)) for x, y in lit if y > ys[len(ys) * 4 // 5]]
        self.assertGreater(sum(map(lum, top)) / len(top), sum(map(lum, foot)) / len(foot))   # bright at the top
        self.assertEqual(im.getpixel((169, 2)), BG)                       # set at the left

    def test_widths(self):
        self.assertLess(icons.number_cols("57°", 5), icons.number_cols("103°", 5))
        self.assertTrue(12 <= icons.number_cols("57°", 5) <= 22)

    def test_in_current_conditions(self):
        v = view()
        out = ui.big_temp_block(v, "57°", "t_mild")
        lines, cols = out
        self.assertEqual(len(lines), 5)
        self.assertTrue(all(s[1].startswith("nowtemp") for s in imgs(lines)))
        self.assertIsNone(ui.big_temp_block(view(False), "57°", "t_mild"))
        src = Path(SC.__file__).read_text(encoding="utf-8")
        self.assertIn('pic = ui.big_temp_block(v, F.t(temp), temp_style(temp), 5)', src)


def hourly_rows(n=48):
    h0 = int(time.time()) // 3600 * 3600
    return [{"t": h0 + i * 3600, "temp": 10 + 6 * math.sin(i / 7), "feels": 9 + 6 * math.sin(i / 7),
             "pop": (i * 9) % 100, "rain": 0.5 if i % 11 == 0 else 0, "is_day": 6 <= (i % 24) < 18}
            for i in range(n)]


class TestHourlyChart(unittest.TestCase):
    def test_draws(self):
        im = C.hourly_chart(hourly_rows(), Fmt("imperial"), 900, 200, BG, T.Theme())
        self.assertEqual(im.size, (900, 200))

    def test_night_darker(self):
        rows = [dict(r, pop=0, rain=0) for r in hourly_rows()]
        F = Fmt("imperial")
        night = C.hourly_chart([dict(r, is_day=False) for r in rows], F, 900, 200, BG, T.Theme())
        day = C.hourly_chart([dict(r, is_day=True) for r in rows], F, 900, 200, BG, T.Theme())
        y = 100
        xs = range(80, 880, 37)
        self.assertLess(sum(lum(night.getpixel((x, y))) for x in xs), sum(lum(day.getpixel((x, y))) for x in xs))

    def test_smooth_line(self):
        # the temperature line's pixels change color along it (by how warm it is)
        rows = [dict(r, pop=0, rain=0, feels=None) for r in hourly_rows()]
        rows = [dict(r, temp=-10 + i) for i, r in enumerate(rows)]
        im = C.hourly_chart(rows, Fmt("imperial"), 900, 200, BG, T.Theme())
        left = [im.getpixel((x, y)) for x in range(60, 120) for y in range(30, 180)]
        right = [im.getpixel((x, y)) for x in range(820, 880) for y in range(30, 180)]
        brightest = lambda px: max(px, key=lum)                          # noqa: E731
        self.assertNotEqual(brightest(left), brightest(right))


class TestDailyChart(unittest.TestCase):
    def rows(self):
        d0 = Fmt("imperial").today()
        return [{"date": d0 + dt.timedelta(days=i), "hi": 18 + i, "lo": 6 + i * 0.5, "pop": (i * 20) % 100}
                for i in range(7)]

    def test_ribbon_between(self):
        im = C.daily_chart(self.rows(), Fmt("imperial"), 700, 220, BG, T.Theme())
        self.assertEqual(im.size, (700, 220))
        x = int(3.5 * 100)
        col = [im.getpixel((x, y)) for y in range(220)]
        tinted = [c for c in col if c != BG and sum(abs(a - b) for a, b in zip(c, BG)) > 30]
        self.assertGreater(len(tinted), 20)                               # the band between high and low


class TestRangePills(unittest.TestCase):
    def test_pill_shaded_along_the_scale(self):
        im = C.range_image(5, 25, 0, 30, 120, 20, BG, T.Theme())
        self.assertEqual(im.size, (120, 20))
        left, right = im.getpixel((30, 10)), im.getpixel((95, 10))
        self.assertNotEqual(left, right)                                  # cool to warm
        self.assertGreater(lum(im.getpixel((10, 10))), lum(BG))           # the track before it

    def test_now_dot(self):
        im = C.range_image(5, 25, 0, 30, 120, 20, BG, T.Theme(), now=15)
        self.assertTrue(any(im.getpixel((x, 10)) == (255, 255, 255) or lum(im.getpixel((x, 10))) > 240
                            for x in range(50, 75)))

    def test_in_the_table(self):
        v = view()
        F = v.fmt
        d0 = F.today()
        m = {"daily": [{"date": d0 + dt.timedelta(days=i), "hi": 18 + i, "lo": 6 + i, "pop": 10, "desc": "Sunny",
                        "kind": "clear"} for i in range(7)], "now": {"temp": 12}, "errors": {}}
        out = SC.render_daily(m, v, 190, 50)
        bars = [s for s in imgs(out["lines"]) if s[1].startswith("dayrange")]
        self.assertEqual(len(bars), 7)
        fills = {s[1]: s[3] for s in bars}
        chosen = [f for slot, f in fills.items() if slot.startswith("dayrange0x")]
        self.assertTrue(chosen and "sel" in str(chosen[0]))               # the selection's background
        self.assertTrue(any("stripe" in str(f) for f in fills.values()))  # and the striped rows'

    def test_restyle_keeps_pictures(self):
        line = [("ab", "text"), ("   ", ("@img", "x", 0, "text")), ("c", "dim")]
        out = restyle(line, "sel")
        self.assertEqual(out[1][1], ("@img", "x", 0, "sel"))
        self.assertEqual(out[0][1], "sel")


class TestMoon(unittest.TestCase):
    def half(self, im, right):
        w, h = im.size
        xs = range(w // 2 + 5, w - 10) if right else range(10, w // 2 - 5)
        return sum(lum(im.getpixel((x, h // 2))) for x in xs) / len(xs)

    def test_phases(self):
        th = T.Theme()
        new = C.moon_image(0.0, 160, 160, BG, theme=th)
        full = C.moon_image(0.5, 160, 160, BG, theme=th)
        waxing = C.moon_image(0.15, 160, 160, BG, theme=th)
        waning = C.moon_image(0.85, 160, 160, BG, theme=th)
        self.assertLess(lum(new.getpixel((80, 80))), 90)
        self.assertGreater(lum(full.getpixel((80, 80))), 120)
        self.assertGreater(self.half(waxing, True), self.half(waxing, False))      # lit on the right
        self.assertGreater(self.half(waning, False), self.half(waning, True))      # lit on the left

    def test_seas_and_glow(self):
        full = C.moon_image(0.5, 160, 160, BG, theme=T.Theme())
        face = [lum(full.getpixel((x, y))) for x in range(40, 120, 4) for y in range(40, 120, 4)
                if math.hypot(x - 80, y - 80) < 55]
        self.assertGreater(max(face) - min(face), 25)                     # darker seas on a bright face
        self.assertNotEqual(full.getpixel((80, 80 - 72)), BG)             # the glow just off the limb


class TestDaylight(unittest.TestCase):
    def points(self):
        return [(d, 36000 + 14000 * math.sin((d - 80) / 366 * 2 * math.pi)) for d in range(1, 366, 4)]

    def test_picture(self):
        pts = self.points()
        lo, hi = min(x for _d, x in pts) * 0.9, max(x for _d, x in pts)
        im = C.daylight_image(pts, 318, lo, hi, 300, 80, BG, theme=T.Theme())
        self.assertEqual(im.size, (300, 80))
        mid = im.getpixel((int((172 - 1) / 366 * 300), 70))              # under the curve in June: golden
        self.assertGreater(mid[0], mid[2])
        x = int((318 - 1) / 366 * 300)
        self.assertTrue(any(im.getpixel((x, y))[0] > 200 and im.getpixel((x, y))[2] < 140 for y in range(80)))

    def test_on_the_sky_screen(self):
        v = view()
        F = v.fmt
        out = SC.daylight_year(F, 32.8, -96.8, None, 40, False, v=v)
        self.assertTrue(any(s[1].startswith("daylight") for s in imgs(out)))
        text_out = SC.daylight_year(F, 32.8, -96.8, None, 40, False)
        self.assertFalse(imgs(text_out))
        self.assertTrue(any("▇" in "".join(t for t, _s in ln) or "█" in "".join(t for t, _s in ln) for ln in text_out))


if __name__ == "__main__":
    unittest.main()
