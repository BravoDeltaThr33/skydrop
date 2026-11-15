"""3.11: the area charts drawn as pictures where the terminal draws them (a smooth curve
that never swings past the readings, a crisp line along its top, the area shaded under
it), and the Hourly chart's line every 5 degrees, only the 10s labeled."""

import math
import os
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
from skydrop import gfx  # noqa: E402
from skydrop import screens as SC  # noqa: E402
from skydrop import theme as T  # noqa: E402
from skydrop.crt import METER  # noqa: E402
from skydrop.fmt import Fmt  # noqa: E402

BG = (17, 32, 120)
RED = (230, 40, 40)


def text(segs):
    return "".join(t for t, _s in segs)


class Pictures:
    def __init__(self, pixels=True):
        self.pixels = pixels
        self.asked = {}

    def block(self, slot, key, cols, rows, builder, bg="text", back=None):
        self.asked[slot] = (cols, rows, builder, key)
        if self.pixels:
            return [[(" " * cols, ("@img", slot, r, bg))] for r in range(rows)]
        return [[(" " * cols, bg)] for _ in range(rows)]


def view(pixels=True, printing=False):
    v = types.SimpleNamespace(fmt=Fmt("imperial"), ascii=False, theme=T.Theme(), images=Pictures(pixels),
                              gmode="kitty" if pixels else "blocks")
    if printing:
        v.printing = True
    return v


class TestVersion(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(tuple(int(x) for x in VERSION.split(".")), (3, 11, 0))


class TestSmoothCurve(unittest.TestCase):
    def test_through_the_points(self):
        xs, ys = [0, 10, 20, 30, 40], [5, 9, 3, 3, 8]
        f = C.smooth_curve(xs, ys)
        for x, y in zip(xs, ys):
            self.assertAlmostEqual(f(x), y, places=9)

    def test_never_past_the_points(self):
        xs, ys = [0, 10, 20, 30, 40, 50], [0, 0, 12, 12, 39, 0]
        f = C.smooth_curve(xs, ys)
        for k in range(len(xs) - 1):
            a, b = ys[k], ys[k + 1]
            for j in range(1, 50):
                y = f(xs[k] + (xs[k + 1] - xs[k]) * j / 50)
                self.assertGreaterEqual(y, min(a, b) - 1e-9)
                self.assertLessEqual(y, max(a, b) + 1e-9)

    def test_smooth(self):
        f = C.smooth_curve([0, 10, 20, 30], [0, 10, 15, 30])
        # no corner at a point: the slope just either side of x=10 matches
        left = (f(10) - f(9.999)) / 0.001
        right = (f(10.001) - f(10)) / 0.001
        self.assertAlmostEqual(left, right, places=2)

    def test_flat_beyond_the_ends(self):
        f = C.smooth_curve([5, 15], [2, 6])
        self.assertEqual(f(-10), 2)
        self.assertEqual(f(99), 6)
        self.assertEqual(C.smooth_curve([3], [7])(50), 7)


def picture(values, lo=0, hi=10, cols=40, rows=5, **kw):
    w, h = cols * 9, rows * 18
    return C.area_image(values, lo, hi, w, h, BG, lambda p, v: RED, cols=cols, rows=rows, **kw)


class TestAreaPicture(unittest.TestCase):
    def test_size_and_card(self):
        img = picture([2, 8, 5])
        self.assertEqual(img.size, (360, 90))
        self.assertEqual(img.getpixel((180, 2)), BG)                     # over the curve: the card

    def test_shaded_and_darker_down(self):
        img = picture([10, 10, 10])
        near_top = img.getpixel((180, 12))
        base = img.getpixel((180, 89))
        self.assertNotEqual(near_top, BG)
        self.assertGreater(near_top[0], base[0])                          # fading toward the base
        self.assertGreater(base[0], BG[0])                                # but still there

    def test_crisp_line_brighter_than_the_area(self):
        img = picture([5, 5, 5])
        col = [img.getpixel((180, y)) for y in range(90)]
        top = next(y for y, c in enumerate(col) if c != BG)
        self.assertGreater(max(c[1] for c in col[top:top + 4]), col[top + 8][1])

    def test_smooth_edges(self):
        img = picture([0, 10, 0, 10, 0])
        between = 0
        for x in range(0, 360):
            for y in range(90):
                c = img.getpixel((x, y))
                if c != BG and c[0] < 120:                               # part card, part area
                    between += 1
        self.assertGreater(between, 50)

    def test_values_over_the_curve(self):
        plain = picture([2, 9, 4], rows=6, head=1)
        marked = picture([2, 9, 4], rows=6, head=1, tops=[(1, "9", (255, 255, 0))])
        diff = [(x, y) for x in range(360) for y in range(108) if plain.getpixel((x, y)) != marked.getpixel((x, y))]
        self.assertTrue(diff)
        self.assertTrue(all(120 < x < 240 for x, _y in diff))            # around the middle value
        self.assertTrue(all(y < 40 for _x, y in diff))                   # over the peak, in the room left for it

    def test_labels_kept_apart(self):
        drawn = []
        real = gfx.draw_text

        def spy(draw, xy, text, *a, **kw):
            drawn.append(text)
            return real(draw, xy, text, *a, **kw)
        gfx.draw_text = spy
        try:
            C.area_image([5] * 30, 0, 10, 90, 72, BG, lambda p, v: RED, cols=10, rows=4, head=1,
                         tops=[(i, "55", (255, 255, 0)) for i in range(30)])
        finally:
            gfx.draw_text = real
        self.assertGreaterEqual(len(drawn), 2)
        self.assertLessEqual(len(drawn), 7)                               # 90 pixels hold a handful, not 30

    def test_dashed_guide(self):
        img = picture([2, 2, 2], guides=[(8, (200, 200, 200))])
        rows = [y for y in range(90) if sum(1 for x in range(360) if img.getpixel((x, y)) == (200, 200, 200)) > 50]
        self.assertTrue(rows)
        y = rows[0]
        run = [img.getpixel((x, y)) == (200, 200, 200) for x in range(360)]
        self.assertTrue(any(run) and not all(run))                        # dashes, not a solid line

    def test_scale_on_the_left(self):
        img = C.area_image([5, 6, 7], 0, 10, 400, 90, BG, lambda p, v: RED, cols=40, rows=5, left=5,
                           axis=[(10, "10°"), (5, "5°")], axis_ink=((220, 220, 220), (120, 120, 120)))
        margin = [img.getpixel((x, y)) for x in range(0, 40) for y in range(90)]
        self.assertTrue(any(c != BG for c in margin))
        self.assertEqual(img.getpixel((60, 2)), BG)

    def test_no_line_on_a_dry_hour(self):
        img = picture([0, 0, 0, 0])
        self.assertEqual({img.getpixel((x, y)) for x in range(img.width) for y in range(img.height)}, {BG})


class TestAreaOnScreens(unittest.TestCase):
    def setUp(self):
        self.saved = dict(METER)
        METER.update({"blocks": True, "ascii": False, "style": "led"})

    def tearDown(self):
        METER.clear()
        METER.update(self.saved)

    def hourly(self):
        h0 = int(time.time()) // 3600 * 3600
        return {"hourly": [{"t": h0 + i * 3600, "temp": 10 + i * 0.6, "pop": (0 if i < 4 else 30), "kind": "rain"}
                           for i in range(14)]}

    def test_now_draws_pictures(self):
        v = view()
        lines = SC.next_hours(self.hourly(), v.fmt, 120, v=v)
        slots = {s[1] for ln in lines for _t, s in ln if isinstance(s, tuple) and s and s[0] == "@img"}
        self.assertEqual(len(slots), 2)
        self.assertTrue(any(x.startswith("areaTemp") for x in slots))
        self.assertTrue(any(x.startswith("areaRain") for x in slots))
        joined = "\n".join(text(ln) for ln in lines)
        self.assertIn("Temp", joined)                                    # the titles stay text
        self.assertIn("Rain", joined)
        self.assertIn("Now", joined)                                     # and the hours along the bottom
        cols, rows, build, _key = next(v.images.asked[s] for s in v.images.asked if s.startswith("areaTemp"))
        self.assertEqual(rows, 6)                                        # 5 rows of chart, 1 for the values
        img = build("pixels", cols * 9, rows * 18)
        self.assertEqual(img.size, (cols * 9, rows * 18))

    def test_text_elsewhere(self):
        for v in (view(pixels=False), view(printing=True), None):
            lines = SC.next_hours(self.hourly(), Fmt("imperial"), 120, v=v)
            self.assertFalse(any(isinstance(s, tuple) and s and s[0] == "@img" for ln in lines for _t, s in ln))
            self.assertTrue(any("█" in text(ln) or "▇" in text(ln) for ln in lines))

    def test_each_size_its_own_slot(self):
        v = view()
        SC.area(v, "areaUV", [1, 3, 5, 3, 1], 40, 6, lo=0, hi=8, style="good")
        SC.area(v, "areaUV", [1, 3, 5, 3, 1], 40, 3, lo=0, hi=8, style="good")
        self.assertEqual(sorted(v.images.asked), ["areaUV40x3", "areaUV40x6"])

    def test_scale_in_the_picture(self):
        v = view()
        lines = SC.area(v, "areaMonth", [70, 75, 80, 72], 60, 5, lo=60, hi=80, style="t_warm",
                        axis=[(80, "80°"), (70, "70°")], guides=[(70, "dim")], gutter=5,
                        labels=["1", "", "", "4"])
        first = lines[0]
        self.assertEqual(first[0][1][0], "@img")                         # from the first column
        self.assertEqual(len(first[0][0]), 60)
        self.assertTrue(text(lines[-2]).startswith("    └"))             # the base line meets the scale
        self.assertIn("1", text(lines[-1]))

    def test_colors_follow_the_styles(self):
        v = view()
        SC.area(v, "x", [1, 1, 9, 9], 40, 4, lo=0, hi=10, style=lambda u: "bad" if u > 5 else "good")
        cols, rows, build, _key = v.images.asked["x40x4"]
        img = build("pixels", cols * 9, rows * 18)
        good, bad = v.theme.spec("good")[0], v.theme.spec("bad")[0]
        left_px = img.getpixel((10, rows * 18 - 30))
        right_px = img.getpixel((cols * 9 - 10, rows * 18 - 60))

        def near(a, b):
            return sum(abs(x - y) for x, y in zip(a, b))
        card = v.theme.rgb("card_bg")
        self.assertLess(near(left_px, card) - near(left_px, good), near(left_px, card) - near(left_px, bad) + 1e9)
        self.assertGreater(right_px[0], left_px[0] - 255)                 # drawn on both sides
        self.assertNotEqual(left_px, right_px)


class TestHourlyGrid(unittest.TestCase):
    def rows(self, temps):
        h0 = 1_700_000_000 // 3600 * 3600
        return [{"t": h0 + i * 3600, "temp": t, "feels": t, "pop": 0} for i, t in enumerate(temps)]

    def labels(self, temps, F):
        got = []
        real = gfx.draw_text

        def spy(draw, xy, text, fnt, fill, anchor="la", **kw):
            if anchor == "rm":
                got.append(text)
            return real(draw, xy, text, fnt, fill, anchor=anchor, **kw)
        gfx.draw_text = spy
        try:
            img = C.hourly_chart(self.rows(temps), F, 900, 200)
        finally:
            gfx.draw_text = real
        return got, img

    def test_tens_labeled(self):
        F = Fmt("imperial")
        temps = [(3 + 6 * math.sin(i / 8)) for i in range(48)]          # about 37 to 48 F
        got, _img = self.labels(temps, F)
        self.assertTrue(got)
        for t in got:
            self.assertEqual(int(t.rstrip("°")) % 10, 0, got)

    def test_a_line_every_five(self):
        F = Fmt("imperial")
        temps = [(3 + 6 * math.sin(i / 8)) for i in range(48)]
        got, img = self.labels(temps, F)
        bg = img.getpixel((2, 100))
        x = 49                                                           # inside the plot, left of the data
        lines, prev = [], None
        for y in range(int(img.height * 0.12), int(img.height * 0.83)):
            c = img.getpixel((x, y))
            if sum(abs(a - b) for a, b in zip(c, bg)) > 12:
                if prev is None or y > prev + 1:
                    lines.append(y)
                prev = y
        # every 5 degrees in the chart's range has a line, and only every other one a number
        self.assertGreaterEqual(len(lines), 2 * len(got) - 1)
        self.assertLessEqual(len(lines), 2 * len(got) + 1)


if __name__ == "__main__":
    unittest.main()
