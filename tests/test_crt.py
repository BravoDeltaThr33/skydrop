"""Unit tests for CRT mode: the phosphor's colors, the meters, the
text effects (typewriter, update flash, persistence, burn-in, slips, blink, power-on, the
self test) on a controlled clock, the glass's pictures and steps, the fonts and the card."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp(prefix="sd-cache-"))
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sd-config-"))

from skydrop import crt as C  # noqa: E402
from skydrop import theme as T  # noqa: E402
from skydrop.canvas import Canvas  # noqa: E402

T0 = 1_000_000.0
QUIET = {"type": 0, "flash": 0, "trails": 0, "slips": 0, "power": 0, "post": 0}


def tube(phosphor="green", theme="weatherstar", graphics="kitty", **levels):
    c = C.CRT()
    with mock.patch("time.time", return_value=T0):
        c.configure({"crt": True, "crt_phosphor": phosphor, "crt_levels": levels}, T.Theme(theme), graphics,
                    "truecolor")
    c.boot = c.post = None             # no power-on or self test unless asked
    return c


def page(W=80, H=24, text="row"):
    cv = Canvas(W, H, "text")
    for y in range(H):
        cv.put(y, 0, [(f"{text} {y} " * 6, "text")])
    return cv


def run(c, cv, t0, seconds, step=0.016):
    t = t0
    fr = None
    while t <= t0 + seconds:
        fr = c.process(cv, t)
        t += step
    return fr, t


class TestPhosphor(unittest.TestCase):
    def test_ramp(self):
        green = C.PHOSPHORS["green"]
        self.assertEqual(C.tint((0, 0, 0), "green"), green[1])
        self.assertEqual(C.tint((255, 255, 255), "green"), green[3])
        self.assertGreaterEqual(C.lum(C.tint((0, 0, 0), "green", "text")), C.lum(C.tint((128, 128, 128), "green")))

    def test_page_is_the_raised_black(self):
        self.assertEqual(tube().mapped("text")[2], C.PHOSPHORS["green"][1])
        self.assertEqual(tube(blacks=0).mapped("text")[2], (0, 0, 0))
        self.assertEqual(tube(blacks=10).mapped("text")[2], (12, 44, 24))

    def test_dark_text_on_a_bright_bar_stays_dark(self):
        m = tube().mapped("tabon")                 # ink on an amber tab
        self.assertLess(C.lum(m[1]), C.lum(m[2]))

    def test_color_keeps_colors_on_raised_blacks(self):
        c = tube("color")
        th = T.Theme("weatherstar")
        fg, bg = th.spec("title")[:2]
        m = c.mapped("title")
        self.assertEqual(m[1], tuple(max(a, b) for a, b in zip(fg, c.black)))
        self.assertEqual(m[2], tuple(max(a, b) for a, b in zip(bg, c.black)))

    def test_pictures_take_the_ramp(self):
        from skydrop import gfx
        if not gfx.HAVE_PIL:
            self.skipTest("no Pillow")
        c = tube()
        img = gfx.Image.new("RGB", (4, 1), (255, 0, 0))
        out = c.tint_picture(img)
        v = (54 * 255) >> 8
        got = out.getpixel((0, 0))
        want = c.tint((v, v, v))
        self.assertTrue(all(abs(a - b) <= 1 for a, b in zip(got, want)), (got, want))
        self.assertIs(tube("color").tint_picture(img), img)

    def test_a_pictures_background_sits_on_the_tube(self):
        from skydrop import gfx
        if not gfx.HAVE_PIL:
            self.skipTest("no Pillow")
        c = tube()
        th = T.Theme("weatherstar")
        page, panel = th.rgb("bg"), th.rgb("bg2")
        img = gfx.Image.new("RGB", (3, 1))
        for x, rgb in enumerate((page, panel, (255, 255, 255))):
            img.putpixel((x, 0), rgb)
        near = lambda a, b: all(abs(p - q) <= 3 for p, q in zip(a, b))   # noqa: E731
        out = c.tint_picture(img, None, page)                 # drawn on the page: the page is the raised black
        self.assertEqual(out.getpixel((0, 0)), c.mapped("text")[2])
        self.assertEqual(out.getpixel((2, 0)), C.PHOSPHORS["green"][3])
        out = c.tint_picture(img, None, panel)                # on a panel: the panel as the tube lights it
        self.assertTrue(near(out.getpixel((1, 0)), c.mapped("panel")[2]), (out.getpixel((1, 0)), c.mapped("panel")))
        plain = c.tint_picture(img)                           # a picture of its own (the radar): the plain ramp
        v = gfx.Image.new("RGB", (1, 1), page).convert("L", (54 / 256, 183 / 256, 19 / 256, 0)).getpixel((0, 0))
        self.assertEqual(plain.getpixel((0, 0)), c.tint((v, v, v)))
        light = T.Theme("magnetic")                           # a light page keeps the plain ramp
        self.assertEqual(c.tint_picture(img, None, light.rgb("bg")).getpixel((0, 0)), plain.getpixel((0, 0)))


class TestMeters(unittest.TestCase):
    def tearDown(self):
        C.METER.update(style="led", blocks=True, ascii=False)

    def test_looks(self):
        C.METER.update(blocks=True, ascii=False)
        self.assertEqual(C.meter_block(3, "good", "led"), [("\U0001FB13" * 3, "good")])
        self.assertEqual(C.meter_block(2, "good", "dots"), [("\U0001FB03" * 2, "good")])
        self.assertEqual(C.meter_block(2, "good", "bricks"), [("\U0001FB7E" * 2, ("rev", "good"))])
        self.assertEqual(C.meter_block(2, "good", "raised"), [("\U0001FB7F" * 2, ("raised", "good"))])
        self.assertEqual(C.meter_block(5, "good", "shade"), [("██▓▒░", "good")])
        self.assertEqual(C.meter_block(2, "good", "shade"), [("█▓", "good")])
        self.assertEqual(C.meter_block(0, "good"), [])
        C.METER["blocks"] = False
        self.assertEqual(C.meter_block(2, "good", "bricks"), [("▊▊", "good")])
        C.METER["ascii"] = True
        self.assertEqual(C.meter_block(2, "good", "bricks"), [("##", "good")])

    def test_only_lit_segments(self):
        C.METER.update(style="led")
        spans = C.meter_spans(0.4, 10, "good")
        self.assertEqual(spans[0], ("\U0001FB13" * 4, "good"))
        self.assertEqual(spans[1], (" " * 6, "text"))
        self.assertEqual(C.meter_spans(0.01, 10, "good")[0][0], "\U0001FB13")

    def test_wrapped_styles(self):
        th = T.Theme("weatherstar")
        fg, bg = th.spec("good")[:2]
        self.assertEqual(th.spec(("rev", "good"))[:3], (fg, bg, "r"))
        edge, face = th.spec(("raised", "good"))[:2]
        self.assertEqual(face, fg)
        self.assertEqual(edge, tuple(round(b + (f - b) * 0.45) for f, b in zip(fg, th.rgb("bg"))))
        c = tube()
        self.assertEqual(c.mapped(("rev", "good"))[3], "r")
        self.assertEqual(c.mapped(("raised", "good"))[2], c.tint(fg, "text"))


class TestTypewriter(unittest.TestCase):
    def test_new_text_is_typed_in_reading_order(self):
        c = tube(**{"type": 5, "flash": 0, "trails": 0, "slips": 0})
        c.process(page(), T0)
        run(c, page(), T0, 1.2)
        fr = c.process(page(text="new"), T0 + 5)
        self.assertTrue(c.typing)
        self.assertEqual(fr.chars[0][1], " ")                 # blanked, to be typed
        self.assertEqual(fr.styles[0][1], c.mapped("text"))   # its background kept
        fr = c.process(page(text="new"), T0 + 5.02)
        self.assertEqual(fr.chars[0][0], "n")                 # the first cells first
        self.assertEqual(fr.chars[23][10], " ")
        run(c, page(text="new"), T0 + 5.04, 1.0)
        self.assertIsNone(c.typing)

    def test_rate(self):
        for level, slow in ((5, 0.9), (10, 1.8)):
            c = tube(type=level)
            c.process(Canvas(80, 24), T0)
            c.process(page(), T0 + 5)
            self.assertAlmostEqual(c.typing["rate"], max(120.0, 80 * 24 * 0.45 / slow), places=3)

    def test_rows_that_keep_changing_are_not_typed(self):
        c = tube(type=5, flash=0)
        c.process(page(), T0)
        run(c, page(), T0, 1.2)
        cv = page()
        cv.put(23, 0, [("crawl one", "text")])
        c.process(cv, T0 + 5)
        run(c, cv, T0 + 5, 0.3)
        cv2 = page()
        cv2.put(23, 0, [("crawl two", "text")])
        c.process(cv2, T0 + 5.35)                            # again within 0.6 s: left to change
        self.assertIsNone(c.typing)


class TestFlash(unittest.TestCase):
    def test_fresh_data_flashes_changed_digits(self):
        c = tube(type=0, trails=0, slips=0)
        cv = Canvas(80, 24)
        cv.put(5, 10, [("72", "value")])
        c.process(cv, T0)
        cv2 = Canvas(80, 24)
        cv2.put(5, 10, [("73", "value")])
        c.fresh = True
        fr = c.process(cv2, T0 + 5)
        self.assertEqual(c.flashes["cells"], [(5, 11)])
        self.assertEqual(fr.styles[5][11], c.styles["crtflash"])
        fr = c.process(cv2, T0 + 5.11)
        self.assertEqual(fr.styles[5][11], c.styles["crtflash2"])
        fr = c.process(cv2, T0 + 5.27)
        self.assertIsNone(c.flashes)
        self.assertEqual(fr.styles[5][11], c.mapped("value"))

    def test_no_flash_without_fresh_data(self):
        c = tube(type=0)
        cv = Canvas(80, 24)
        cv.put(5, 10, [("72", "value")])
        c.process(cv, T0)
        cv2 = Canvas(80, 24)
        cv2.put(5, 10, [("73", "value")])
        c.process(cv2, T0 + 5)
        self.assertIsNone(c.flashes)

    def test_typed_digits_are_not_flashed(self):
        c = tube(type=5)
        cv = Canvas(80, 24)
        cv.put(5, 10, [("72", "value")])
        c.process(cv, T0)
        run(c, cv, T0, 1.2)
        cv2 = Canvas(80, 24)
        cv2.put(5, 10, [("73", "value")])
        c.fresh = True
        c.process(cv2, T0 + 5)
        self.assertIsNone(c.flashes)                           # the typewriter has them

    def test_a_new_layout_does_not_flash(self):
        c = tube(type=0)
        c.process(page(text="1"), T0)
        c.fresh = True
        c.process(page(text="2"), T0 + 5)
        self.assertIsNone(c.flashes)

    def test_t_flashes_every_number(self):
        c = tube(type=0)
        c.process(page(), T0)
        self.assertTrue(c.flash_now())
        self.assertGreater(len(c.flashes["cells"]), 30)


class TestPersistence(unittest.TestCase):
    def test_text_gone_dark_fades(self):
        c = tube(type=0, flash=0, slips=0)
        cv = Canvas(80, 24)
        cv.put(3, 3, [("HELLO", "value")])
        c.process(cv, T0)
        fr = c.process(Canvas(80, 24), T0 + 5)
        self.assertEqual(fr.chars[3][3], "H")
        self.assertEqual(fr.styles[3][3], c.styles["crtghost"])
        fr = c.process(Canvas(80, 24), T0 + 5.08)
        self.assertEqual(fr.styles[3][3], c.styles["crtfaint"])
        fr = c.process(Canvas(80, 24), T0 + 5.15)
        self.assertEqual(fr.chars[3][3], " ")
        self.assertFalse(c.trails)

    def test_capped(self):
        c = tube(type=0, flash=0)
        c.process(page(200, 60), T0)
        c.process(Canvas(200, 60), T0 + 5)
        self.assertLessEqual(len(c.trails), C.TRAIL_MAX)


class TestBurnIn(unittest.TestCase):
    def test_borders_burn_in_and_level_0_wipes(self):
        c = tube(type=0, flash=0, trails=0, slips=0)
        t = T0
        for i in range(95):
            cv = Canvas(80, 24)
            cv.put(10, 0, [("─" * 40, "box")])
            c.process(cv, t)
            t += 1.0
        self.assertIn(5, c.burnt.get(10, {}))
        fr = c.process(Canvas(80, 24), t)
        self.assertEqual(fr.chars[10][5], "─")
        self.assertEqual(fr.styles[10][5], c.styles["crtburn"])
        with mock.patch("time.time", return_value=t):
            c.configure({"crt": True, "crt_levels": dict(QUIET, burn=0)}, T.Theme("weatherstar"), "kitty",
                        "truecolor")
        self.assertFalse(c.burnt)


class TestSlipsBlinkBoot(unittest.TestCase):
    def test_slip_happens_and_snaps_back(self):
        c = tube(type=0, flash=0, trails=0, slips=10)
        cv = page()
        c.process(cv, T0)
        c.slip_at = T0 + 1
        c.process(cv, T0 + 1.0)
        self.assertTrue(c.slip)
        rows = list(c.slip[0])
        self.assertTrue(all(3 <= y <= 21 for y in rows))
        y = rows[0]
        shift = c.slip[0][y]
        fr = c.process(cv, T0 + 1.01)
        self.assertEqual(fr.chars[y][:shift], [" "] * shift)
        fr = c.process(cv, T0 + 1.2)
        self.assertIsNone(c.slip)
        self.assertEqual(fr.chars[y][0], "r")

    def test_blink_until_a_key(self):
        c = tube(type=0, flash=0, trails=0, slips=0)
        cv = Canvas(80, 24)
        cv.put(23, 2, [("TORNADO WARNING", "stalert")])
        c.process(cv, T0)
        c.start_blink()
        fr = c.process(cv, T0 + 0.1)
        self.assertEqual(fr.chars[23][2], "T")
        fr = c.process(cv, T0 + 0.5)
        self.assertEqual(fr.chars[23][2], " ")                    # the dark half-beat
        self.assertEqual(fr.styles[23][2], c.mapped("stalert"))   # its background kept
        c.on_input()
        fr = c.process(cv, T0 + 0.9)
        self.assertEqual(fr.chars[23][2], "T")

    def test_power_on_sweep(self):
        c = tube(type=0, flash=0, trails=0, slips=0, post=0)
        cv = page()
        c.process(cv, T0)
        with mock.patch("time.time", return_value=T0 + 10):
            c.power_on()
        b = c.boot
        fr = c.process(cv, b + 0.05)
        self.assertEqual(fr.chars[12][40], "•")
        self.assertEqual(fr.styles[12][40], c.styles["crtbeam"])
        fr = c.process(cv, b + 0.2)
        self.assertIn("━", "".join(fr.chars[12]))
        fr = c.process(cv, b + 0.6)
        self.assertEqual(fr.chars[12][0], "r")                     # the picture opening from the middle
        self.assertTrue(any(row[0] == "─" for row in fr.chars))      # the beam at its edges
        self.assertEqual(fr.chars[0][0], " ")
        fr = c.process(cv, b + 0.9)
        self.assertIsNone(c.boot)
        self.assertEqual(fr.chars[0][0], "r")

    def test_after_the_sweep_the_screen_types_in(self):
        c = tube(type=5, flash=0, trails=0, slips=0, post=0)
        cv = page()
        c.process(cv, T0)
        with mock.patch("time.time", return_value=T0 + 10):
            c.power_on()
        c.process(cv, c.boot + 0.9)
        self.assertTrue(c.typing)

    def test_any_key_skips_the_sweep(self):
        c = tube(post=0)
        c.process(page(), T0)
        c.power_on()
        c.on_input()
        self.assertIsNone(c.boot)

    def test_self_test(self):
        c = tube(type=0, flash=0, trails=0, slips=0)
        c.checks = lambda i: (["CPU", "Memory"][i % 2], "{n} K in use" if i % 2 else "x86 · 8 cores", "OK",
                              1000 if i % 2 else None)
        c.start_post(T0)
        cv = page()
        fr = c.process(cv, T0 + 0.01)
        text = ["".join(r) for r in fr.chars]
        self.assertIn("SKYDROP BIOS v", text[2])
        self.assertIn("Power-on self test", text[3])
        fr = c.process(cv, T0 + 0.5)
        text = ["".join(r) for r in fr.chars]
        self.assertIn("CPU ....... x86 · 8 cores", text[5])
        self.assertTrue(text[5].rstrip().endswith("OK"))
        fr = c.process(cv, T0 + 0.16 * 12.5)
        self.assertIn("Booting", "".join("".join(r) for r in fr.chars))
        c.process(cv, T0 + 0.16 * 15)
        self.assertIsNone(c.post)

    def test_self_test_needs_room(self):
        c = tube()
        c.checks = lambda i: ("CPU", "x", "OK", None)
        c.start_post(T0)
        c.process(page(50, 16), T0)
        self.assertIsNone(c.post)

    def test_urgent_skips_everything(self):
        c = tube(type=5)
        c.urgent = True
        c.power_on()
        c.start_post(T0)
        c.process(page(), T0)
        self.assertIsNone(c.boot)
        self.assertIsNone(c.post)
        self.assertIsNone(c.typing)


class TestGlass(unittest.TestCase):
    def test_ring(self):
        c = tube()
        self.assertEqual(c.ring(80, 24), (1, 2, 1, 1))
        self.assertEqual(c.ring(64, 16), (1, 2, 1, 1))
        self.assertIsNone(c.ring(63, 24))
        self.assertIsNone(c.ring(80, 15))
        self.assertIsNone(tube(bezel=0).ring(80, 24))
        self.assertIsNone(tube(graphics="blocks").ring(80, 24))

    def glass(self, **levels):
        c = tube(**dict(QUIET, **levels))
        c.process(page(), T0)
        out = []
        with mock.patch("time.time", return_value=T0):
            c.glass_frame(out, T0, 80, 24, (9, 18))
        return c, "".join(out)

    def test_pictures_by_level(self):
        c, out = self.glass()
        for iid, z in ((900, 11), (901, 15), (902, 14), (904, 12), (911, 16), (912, 17)):
            self.assertIn(f"a=p,i={iid},p=1,z={z},", out)
        self.assertIn("a=d,d=i,i=903,", out)                 # the hum bar starts above the top
        g = c.glass
        g.step(T0 + 1.0)
        self.assertIn("a=p,i=903,p=1,z=13,", g.take())      # and comes in from there
        c, out = self.glass(grain=0, flicker=0, hum=0, scanlines=0, grille=0)
        for iid in (900, 902, 903, 904):
            self.assertNotIn(f"a=t,f=32,t=d,o=z,q=2,i={iid},", out)

    def test_moving_parts_step_every_tenth(self):
        c, _ = self.glass()
        g = c.glass
        sent = []
        t = T0 + 1.0
        for i in range(200):
            g.step(t + i * 0.01)
            sent.append(g.take())
        steps = [s for s in sent if s]
        self.assertGreaterEqual(len(steps), 18)
        self.assertLessEqual(len(steps), 21)
        self.assertTrue(any("a=a,i=904,c=" in s for s in steps))   # the grain's next frame

    def test_still_glass_sleeps(self):
        c, _ = self.glass(grain=0, flicker=0, hum=0)
        self.assertFalse(c.glass.moving())
        self.assertIsNone(c.glass.next_step(T0 + 2))
        g = c.glass
        g.step(T0 + 5)
        self.assertEqual(g.take(), "")

    def test_glow_follows_at_most_every_third_of_a_second(self):
        c, out = self.glass()
        self.assertIn("i=905,", out)                       # the first glow at once
        with mock.patch("time.time", return_value=T0 + 0.1):
            c.process(page(text="next"), T0 + 0.1)
            out2 = []
            c.glass_frame(out2, T0 + 0.1, 80, 24, (9, 18))
        self.assertNotIn("a=t,f=32", "".join(out2))
        self.assertTrue(c.glow_due)
        with mock.patch("time.time", return_value=T0 + 0.4):
            out3 = []
            c.glass_frame(out3, T0 + 0.4, 80, 24, (9, 18))
        self.assertIn("i=906,", "".join(out3))              # the other set swapped in

    def test_lost_sends_it_again(self):
        c, _ = self.glass()
        c.lost()
        out = []
        c.glass_frame(out, T0 + 1, 80, 24, (9, 18))
        self.assertIn("a=t,f=32,t=d,o=z,q=2,i=900,", "".join(out))


class TestFonts(unittest.TestCase):
    def test_kitty(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "kitty"))
            with open(os.path.join(d, "kitty", "kitty.conf"), "w") as fh:
                fh.write("font_size 12\nfont_family      VT323\n")
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
                C.TERMINAL_FONT["at"] = -60.0
                self.assertEqual(C.terminal_font("kitty")[0], "VT323")
                self.assertEqual(C.font_config_line("VT323", "kitty")[0], "font_family VT323")

    def test_ghostty_first_family_wins_and_blank_resets(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "ghostty"))
            with open(os.path.join(d, "ghostty", "config"), "w") as fh:
                fh.write('font-family = "Ubuntu Mono"\nfont-family = ""\nfont-family = "IBM 3270"\n'
                         'font-family = "Noto Color Emoji"\n')
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
                C.TERMINAL_FONT["at"] = -60.0
                self.assertEqual(C.terminal_font("ghostty")[0], "IBM 3270")
                self.assertEqual(C.font_config_line("IBM 3270", "ghostty")[0], 'font-family = "IBM 3270"')

    def test_keys_and_old_names(self):
        self.assertEqual(C.font_key("VT323"), "vt323")
        self.assertEqual(C.font_key("Px437 IBM VGA 8x16"), "px437")
        self.assertEqual(C.font_key("nonsense"), "own")
        self.assertEqual(C.font_family("terminus", "kitty")[0], "Terminus (TTF)")
        self.assertEqual(C.font_family("terminus", "ghostty")[0], "Terminus")


class TestSettingsShape(unittest.TestCase):
    def test_card_rows(self):
        from skydrop.crtcard import ROWS
        self.assertEqual(ROWS[:5], ("crt", "phosphor", "font", "fontsize", "meters"))
        self.assertEqual(list(ROWS[5:]), C.EFFECT_KEYS)
        self.assertEqual(C.EFFECT_KEYS[:9], ["blacks", "power", "trails", "burn", "slips", "type", "post",
                                             "blink", "flash"])

    def test_levels(self):
        self.assertEqual(C.levels_of({}), {k: 5 for k in C.EFFECT_KEYS})
        self.assertEqual(C.levels_of({"crt_levels": {"curve": 8}})["glare"], 8)
        self.assertEqual(C.levels_of({"crt_levels": {"grain": "x", "hum": 14, "flicker": True}})["hum"], 10)
        self.assertEqual(C.levels_saved({})["scale"], 2)
        self.assertEqual(C.summary({"crt_levels": {"burn": 0, "hum": 7}}), "1 changed · 1 off")

    def test_describe(self):
        self.assertEqual(C.describe("burn", 10), "burns in after 45 s on screen")
        self.assertEqual(C.describe("blacks", 5, "green"), "a black of #06160C")
        self.assertEqual(C.describe("grain", 10), "2× the standard")


if __name__ == "__main__":
    unittest.main()
