"""3.16: the Now screen's Next 12 hours with the humidity as a line over the temperature,
colored along the whole spectrum (red for the driest air, then orange, yellow, green, cyan
and blue, to violet for the most humid), its key under the chart, and the version beside
skydrop's name in the title bar."""

import colorsys
import os
import sys
import tempfile
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp(prefix="sd-cache-"))
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sd-config-"))

from skydrop import VERSION  # noqa: E402
from skydrop import charts as C  # noqa: E402
from skydrop import columns as CO  # noqa: E402
from skydrop import theme as T  # noqa: E402
from skydrop.crt import METER  # noqa: E402

RISING = [12, 18, 25, 33, 41, 50, 58, 66, 74, 83, 92, 99]


def text(lines):
    return ["".join(t for t, _s in ln) for ln in lines]


def is_braille(ch):
    return 0x2800 < ord(ch) <= 0x28FF


class TestVersion(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(tuple(int(x) for x in VERSION.split(".")), (3, 16, 0))

    def test_installers_say_the_same(self):
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as fh:
            self.assertIn(f'VERSION="{VERSION}"', fh.read())
        with open(os.path.join(ROOT, "install.ps1"), encoding="utf-8") as fh:
            self.assertIn(f'$Version = "{VERSION}"', fh.read())


class TestColors(unittest.TestCase):
    def test_the_whole_spectrum_in_order(self):
        hues = []
        for rh in range(0, 101, 5):
            r, g, b = (c / 255 for c in T.humidity_rgb(rh))
            hues.append(colorsys.rgb_to_hsv(r, g, b)[0])
        self.assertEqual(hues, sorted(hues))                           # red, orange, yellow ... violet
        self.assertEqual(len(set(hues)), len(hues))
        self.assertLess(hues[0], 0.02)                                 # red
        self.assertTrue(0.12 < hues[7] < 0.2)                          # yellow at 35%
        self.assertTrue(0.28 < hues[10] < 0.42)                        # green at 50%
        self.assertTrue(0.55 < hues[17] < 0.67)                        # blue at 85%
        self.assertGreater(hues[20], 0.72)                             # violet

    def test_ends(self):
        r, g, b = T.humidity_rgb(0)
        self.assertTrue(r > g + 150 and r > b + 150)                   # the driest air red
        r, g, b = T.humidity_rgb(100)
        self.assertTrue(b > g + 120 and r > g + 60)                    # the most humid violet
        self.assertEqual(T.humidity_rgb(-20), T.humidity_rgb(0))
        self.assertEqual(T.humidity_rgb(140), T.humidity_rgb(100))

    def test_every_theme_reads(self):
        for name in T.THEMES:
            th = T.Theme(name)
            colors = [th.spec(s)[0] for s in T.HUMIDITY_STYLES]
            for style, fg in zip(T.HUMIDITY_STYLES, colors):
                self.assertGreaterEqual(T.contrast(fg, th.pal["card_bg"]), 3.0, f"{name}:{style}")
                self.assertGreaterEqual(T.contrast(fg, th.pal["bg"]), 3.0, f"{name}:{style}")
            self.assertGreater(len(set(colors)), 15, name)
            r, g, b = colors[0]
            self.assertTrue(r > g + 100 and r > b + 100, name)         # still red on a light card
            r, g, b = colors[-1]
            self.assertTrue(b > g + 80 and r > g + 40, name)           # and violet

    def test_a_background_of_your_own(self):
        th = T.Theme("weatherstar", background="#06102A")
        for style in T.HUMIDITY_STYLES:
            self.assertGreaterEqual(T.contrast(th.spec(style)[0], th.pal["card_bg"]), 3.0, style)

    def test_sixteen_colors(self):
        th = T.Theme("weatherstar")
        self.assertIn(th.spec("rh0")[3], (1, 9))                       # red
        self.assertIn(th.spec("rh10")[3], (2, 10))                     # green
        self.assertIn(th.spec("rh20")[3], (4, 5, 12, 13))              # blue to violet

    def test_style_of(self):
        self.assertEqual(T.humidity_style(0), "rh0")
        self.assertEqual(T.humidity_style(52), "rh10")
        self.assertEqual(T.humidity_style(100), "rh20")
        self.assertEqual(T.humidity_style(130), "rh20")
        self.assertEqual(T.humidity_style(-3), "rh0")
        self.assertEqual(T.humidity_style(None), "dim")


class TestLineCells(unittest.TestCase):
    def test_braille_rising(self):
        cells = CO.line_cells(RISING, (0, 100), 60, 5, 21)
        self.assertTrue(cells)
        self.assertTrue(all(is_braille(g) for g, _k in cells.values()))
        cols = sorted({c for _r, c in cells})
        self.assertEqual(cols, list(range(cols[0], cols[-1] + 1)))      # no column left out
        first = [r for r, c in cells if c == cols[0]]
        last = [r for r, c in cells if c == cols[-1]]
        self.assertEqual(max(first), 4)                                # 12%: in the bottom row
        self.assertEqual(min(last), 0)                                 # 99%: in the top row
        ks = [k for _c, k in sorted((c, k) for (_r, c), (_g, k) in cells.items())]
        self.assertLessEqual(ks[0], 3)                                 # red at the start
        self.assertGreaterEqual(ks[-1], 19)                            # violet at the end
        self.assertEqual(ks, sorted(ks))

    def test_from_the_first_hour_to_the_last(self):
        n, width = 12, 60
        cells = CO.line_cells([50] * n, (0, 100), width, 4, 21)
        cols = sorted({c for _r, c in cells})
        self.assertEqual(cols[0], int(0.5 * width / n))                # the first hour's middle
        self.assertEqual(cols[-1], int((n - 0.5) * width / n))         # the last hour's
        self.assertEqual({k for _g, k in cells.values()}, {10})

    def test_gaps_and_too_few(self):
        cells = CO.line_cells([20, None, None, 80], (0, 100), 40, 4, 21)
        cols = sorted({c for _r, c in cells})
        self.assertEqual(cols, list(range(cols[0], cols[-1] + 1)))      # straight across the gap
        self.assertEqual(CO.line_cells([50, None, None], (0, 100), 40, 4, 21), {})
        self.assertEqual(CO.line_cells([], (0, 100), 40, 4, 21), {})

    def test_ascii(self):
        cells = CO.line_cells(RISING, (0, 100), 48, 3, 21, ascii_only=True)
        self.assertTrue(all(g in "'-." for g, _k in cells.values()))
        cols = [c for _r, c in cells]
        self.assertEqual(len(cols), len(set(cols)))                    # one character a column


class TestTextChart(unittest.TestCase):
    def setUp(self):
        self.saved = dict(METER)
        METER.update({"blocks": True, "ascii": False, "style": "led"})

    def tearDown(self):
        METER.clear()
        METER.update(self.saved)

    def chart(self, **kw):
        vals = [8.0, 7.5, 7.0, 6.0, 5.5, 5.0, 4.5, 4.4, 4.2, 3.9, 3.3, 2.8]
        tops = [f"{v:.0f}" for v in vals]
        return CO.area_chart(vals, 130, 6, lo=0.0, hi=8.0, style="t_cool", tops=tops, gutter=8, title="Temp",
                             labels=["x"] * 12, band=3, bars=[(9, 0.7, "p_rain2", "70%")], **kw)

    def test_the_line_over_the_area(self):
        plain = self.chart()
        lined = self.chart(line=RISING, line_scale=(0, 100), line_styles=T.HUMIDITY_STYLES)
        self.assertEqual(len(plain), len(lined))
        rows = text(lined)
        base = next(i for i, row in enumerate(rows) if "─────" in row)
        dots = [i for i, row in enumerate(rows) if any(is_braille(ch) for ch in row)]
        self.assertTrue(dots)
        self.assertLess(max(dots), base - 3)                           # the area's rows, never the band's
        self.assertEqual(rows[base - 3:], text(plain)[base - 3:])      # the band, base and labels as they were
        styles = [s for ln in lined for _t, s in ln]
        self.assertTrue(any(isinstance(s, tuple) and s[0] == "over" and s[1] in T.HUMIDITY_STYLES for s in styles))
        self.assertTrue(any(s in T.HUMIDITY_STYLES for s in styles))   # off the area, on the card

    def test_the_values_stay(self):
        plain, lined = text(self.chart()), text(self.chart(line=RISING, line_styles=T.HUMIDITY_STYLES))
        digits = 0
        for r, row in enumerate(plain):
            for c, ch in enumerate(row):
                if ch.isdigit():                                       # the line passes behind them
                    self.assertEqual(lined[r][c], ch, (r, c))
                    digits += 1
        self.assertGreater(digits, 12)

    def test_no_styles_no_line(self):
        self.assertEqual(self.chart(), self.chart(line=RISING))
        self.assertEqual(self.chart(), self.chart(line=[50] + [None] * 11, line_styles=T.HUMIDITY_STYLES))

    def test_ascii(self):
        METER.update({"ascii": True})
        rows = text(self.chart(line=RISING, line_styles=T.HUMIDITY_STYLES))
        self.assertTrue(all(ch.isascii() for r in rows for ch in r))
        self.assertTrue(any("'" in r or "." in r for r in rows[:4]))


class TestPicture(unittest.TestCase):
    BG = (18, 34, 122)
    MAGENTA = (255, 0, 255)

    def image(self, **kw):
        return C.area_image([10, 12, 14, 13, 11, 9], 0, 14, 600, 140, self.BG, lambda p, v: (240, 200, 80),
                            cols=60, rows=7, head=1, **kw)

    def where(self, img, rgb, tol=40):
        w, h = img.size
        return [(x, y) for y in range(h) for x in range(w)
                if sum(abs(a - b) for a, b in zip(img.getpixel((x, y)), rgb)) < tol]

    def test_nothing_without_it(self):
        self.assertEqual(self.image().tobytes(), self.image(line=[40] * 6).tobytes())
        self.assertEqual(self.image().tobytes(), self.image(line=[40] + [None] * 5,
                                                            line_color=lambda v: self.MAGENTA).tobytes())

    def test_on_its_own_scale(self):
        img = self.image(line=[0, 20, 40, 60, 80, 100], line_color=lambda v: self.MAGENTA)
        pts = self.where(img, self.MAGENTA)
        self.assertTrue(pts)
        left = [y for x, y in pts if x < 70]
        right = [y for x, y in pts if x > 530]
        self.assertGreater(min(left), 115)                             # 0%: down at the floor
        self.assertLess(max(right), 40)                                # 100%: up at the top
        xs = [x for x, _y in pts]
        self.assertGreater(min(xs), 30)                                # from the first hour's middle
        self.assertLess(max(xs), 570)                                  # to the last's

    def test_colored_along_its_length(self):
        def color(v):
            return T.humidity_rgb(v)
        img = self.image(line=[5, 20, 40, 60, 80, 95], line_color=color)
        self.assertTrue(self.where(img, T.humidity_rgb(5), 30))
        self.assertTrue(self.where(img, T.humidity_rgb(95), 30))
        red = self.where(img, T.humidity_rgb(5), 30)
        violet = self.where(img, T.humidity_rgb(95), 30)
        self.assertLess(max(x for x, _y in red), min(x for x, _y in violet))

    def test_edged_and_out_of_the_band(self):
        img = self.image(line=[30, 50, 70, 50, 30, 10], line_color=lambda v: self.MAGENTA, band=3,
                         bars=[(2, 1.0, (64, 206, 92), "90%")])
        pts = self.where(img, self.MAGENTA)
        self.assertTrue(pts)
        self.assertLess(max(y for _x, y in pts), 140 - 3 * 20 + 3)     # stays over the band
        dark = T.blend(self.BG, (0, 0, 0), 0.55)
        self.assertTrue(self.where(img, dark, 12))                     # its darker edge


class TestNow(unittest.TestCase):
    def rows(self, hums, pops=None):
        h0 = int(time.time()) // 3600 * 3600
        temps = [8.0, 7.5, 7.0, 6.0, 5.5, 5.0, 4.5, 4.4, 4.2, 3.9, 3.3, 2.8]
        pops = pops or [0] * 12
        return [{"t": h0 + i * 3600, "temp": t, "hum": h, "pop": p, "nws_desc": "Rain" if p else "Cloudy"}
                for i, (t, h, p) in enumerate(zip(temps, hums, pops))]

    def next_hours(self, rows, width=130):
        from skydrop import screens as SC
        from skydrop.fmt import Fmt
        return SC.next_hours({"hourly": rows}, Fmt("imperial"), width)

    def test_line_and_key(self):
        lines = self.next_hours(self.rows(RISING, [0] * 9 + [60, 70, 40]))
        rows = text(lines)
        self.assertTrue(any(is_braille(ch) for r in rows for ch in r))
        self.assertIn("Precipitation", rows[-1])                       # still the last line
        key = rows[-2]
        self.assertIn("Humidity", key)
        self.assertIn("0% ━━━━━━━━━━━ 100%", key)
        self.assertIn("12% to 99% in the next 12 hours", key)
        self.assertEqual(key.index("0%") - key.index("Humidity"),
                         rows[-1].index("■") - rows[-1].index("Precipitation"))   # the two keys lined up
        spans = dict((t, s) for t, s in lines[-2])
        self.assertEqual(spans["12%"], "rh2")                          # each number in its color
        self.assertEqual(spans["99%"], "rh20")

    def test_steady(self):
        rows = text(self.next_hours(self.rows([55] * 12)))
        self.assertIn("55% through the next 12 hours", rows[-2])

    def test_none_reported(self):
        rows = text(self.next_hours(self.rows([None] * 12)))
        self.assertFalse(any("Humidity" in r for r in rows))
        self.assertFalse(any(is_braille(ch) for r in rows for ch in r))
        self.assertIn("Precipitation", rows[-1])

    def test_narrow(self):
        from skydrop import screens as SC
        for width in (90, 60, 40, 24):
            key = SC.humidity_key(RISING, width)
            self.assertLessEqual(sum(len(t) for t, _s in key), width, width)
            self.assertTrue("".join(t for t, _s in key).startswith("Humidity"))
        self.assertIn("12% to 99%", "".join(t for t, _s in SC.humidity_key(RISING, 60)))
        self.assertNotIn("next 12 hours", "".join(t for t, _s in SC.humidity_key(RISING, 60)))

    def test_ascii_key(self):
        from skydrop import screens as SC
        line = "".join(t for t, _s in SC.humidity_key(RISING, 100, ascii_only=True))
        self.assertTrue(line.isascii())
        self.assertIn("0% =========== 100%", line)


class TestTitleBar(unittest.TestCase):
    def header(self, color_mode, W=160, ascii_=False):
        from skydrop import app as A
        from skydrop.canvas import Canvas
        from skydrop.fmt import Fmt
        fake = types.SimpleNamespace(
            theme=T.Theme("weatherstar"), color_mode=color_mode, ascii=ascii_, cfg={}, fmt=Fmt("imperial"),
            model={"loc": {"name": "Dallas, TX", "zip": "75201"}, "now": {"temp": 8.0, "desc": "cloudy"}},
            store=types.SimpleNamespace(busy=False, last_done=None),
            next_refresh_at=lambda: time.time() + 300)
        cv = Canvas(W, 3)
        A.App.header(fake, cv, W)
        return "".join(cv.chars[0]), cv.chars[0], cv.styles[0]

    @staticmethod
    def cell(cells, word):
        """The cell `word` starts in (a wide glyph's second cell holds "")."""
        where = [i for i, ch in enumerate(cells) for _ in ch]
        return where["".join(cells).index(word)]

    def test_version_beside_the_name(self):
        row, cells, styles = self.header("truecolor")
        self.assertIn(f"skydrop {VERSION} ", row)
        self.assertLess(row.index("skydrop"), row.index("Dallas"))
        name, ver = self.cell(cells, "skydrop"), self.cell(cells, VERSION)
        self.assertEqual(styles[ver][3], "")                           # the version not bold
        self.assertEqual(styles[name][3], "b")
        self.assertEqual(styles[ver][1:3], styles[name][1:3])          # in the same chip

    def test_every_color_mode(self):
        for mode in ("256", "16"):
            row, cells, styles = self.header(mode)
            self.assertIn(f"skydrop {VERSION} ", row)
            self.assertEqual(styles[self.cell(cells, VERSION)], "tabon")
        row, _cells, _styles = self.header("truecolor", ascii_=True)
        self.assertTrue(row.startswith(f" * skydrop {VERSION} "))

    def test_narrow(self):
        row, _cells, _styles = self.header("truecolor", W=60)
        self.assertIn(f"skydrop {VERSION}", row)


if __name__ == "__main__":
    unittest.main()
