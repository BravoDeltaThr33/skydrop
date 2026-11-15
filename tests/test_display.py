"""Settings, Display: Background, Rounded corners and
Meter blocks (with the meters' looks and the CRT rows tested in test_crt)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp(prefix="sd-cache-"))
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sd-config-"))

from skydrop import crt as C  # noqa: E402
from skydrop import term as TM  # noqa: E402
from skydrop import text as TX  # noqa: E402
from skydrop import theme as T  # noqa: E402
from skydrop.canvas import Canvas, ImageRegistry, Renderer  # noqa: E402
from skydrop.settings import FIELD_BY_KEY, parse_field  # noqa: E402


class Out:
    def __init__(self):
        self.writes = []

    def write(self, s):
        self.writes.append(s)

    def flush(self):
        pass


class TestBackground(unittest.TestCase):
    def test_values(self):
        for raw, want in ((None, "theme"), ("", "theme"), ("theme", "theme"), ("#0a0a10", "#0A0A10"),
                          ("0A0A10", "#0A0A10"), ("black", "#000000"), ("charcoal", "#1A1A1A"),
                          ("terminal", "terminal"), ("the terminal's own", "terminal"), ("bogus", None),
                          ("#12345", None)):
            self.assertEqual(T.background_of(raw), want, raw)
        item = FIELD_BY_KEY["background"]
        self.assertEqual(parse_field(item, "#06102a"), ("#06102A", None))
        self.assertIsNone(parse_field(item, "blue-ish")[0])

    def test_the_page_takes_it(self):
        th = T.Theme("weatherstar", "#000000")
        self.assertEqual(th.pal["bg"], (0, 0, 0))
        self.assertEqual(th.spec("text")[1], (0, 0, 0))
        self.assertEqual(th.spec("text")[4], 0)                      # the 16-color page follows
        self.assertEqual(th.spec("status")[1], T.PALETTES["weatherstar"]["bar"])   # bands keep theirs
        self.assertEqual(T.Theme("weatherstar").pal["bg"], T.PALETTES["weatherstar"]["bg"])
        light = T.Theme("magnetic", "#000000")                       # dark lettering: keeps its page
        self.assertEqual(light.pal["bg"], T.PALETTES["magnetic"]["bg"])
        self.assertFalse(T.Theme("magnetic", "terminal").default_bg)

    def test_terminals_own(self):
        th = T.Theme("midnight", "terminal", (30, 32, 48))
        self.assertTrue(th.default_bg)
        self.assertEqual(th.pal["bg"], (30, 32, 48))                 # pictures and blends use its color
        self.assertEqual(T.Theme("midnight", "terminal").pal["bg"], (0, 0, 0))
        for mode in ("truecolor", "256", "16"):
            r = Renderer(Out(), th, mode, "blocks")
            self.assertTrue(r.sgr("text").endswith(";49m"), (mode, r.sgr("text")))
            self.assertNotIn(";49m", r.sgr("status"))               # a band keeps its color
        r = Renderer(Out(), T.Theme("midnight"), "truecolor", "blocks")
        self.assertNotIn(";49m", r.sgr("text"))

    def test_not_under_a_tube(self):
        th = T.Theme("weatherstar", "terminal", (0, 0, 0))
        term = Out()
        r = Renderer(term, th, "truecolor", "blocks")
        cv = Canvas(40, 10, "text")
        cv.put(2, 2, [("hello", "value")])
        r.present(cv, ImageRegistry())
        self.assertIn(";49m", "".join(term.writes))
        term.writes.clear()
        with mock.patch("time.time", return_value=1e6):
            r.configure(cfg={"crt": True, "crt_phosphor": "color", "crt_levels": {"blacks": 0, "power": 0, "post": 0}})
            r.present(cv, ImageRegistry())
        self.assertTrue(r.crt.on)
        self.assertNotIn(";49m", "".join(term.writes))           # the tube paints its own black


class TestCorners(unittest.TestCase):
    def tearDown(self):
        TX.set_rounded(True)

    def test_switch(self):
        self.assertEqual(TX.corners(), ("╭", "╮", "╰", "╯"))
        TX.set_rounded(False)
        self.assertEqual(TX.corners(), ("┌", "┐", "└", "┘"))
        box = TX.panel("Now", [[("x", "text")]], 10)
        self.assertTrue(box[0][0][0].startswith("┌"))
        self.assertTrue(box[-1][0][0].startswith("└") and box[-1][0][0].endswith("┘"))
        self.assertEqual(TX.corners(True), ("+", "+", "+", "+"))
        TX.set_rounded(True)
        self.assertTrue(TX.panel("Now", [], 10)[0][0][0].startswith("╭"))


class TestMeterBlocks(unittest.TestCase):
    CLEAN = {k: v for k, v in os.environ.items()
             if k not in ("KITTY_WINDOW_ID", "GHOSTTY_RESOURCES_DIR", "VTE_VERSION", "TERM_PROGRAM", "TERM")}

    def tearDown(self):
        C.METER.update(blocks=True, ascii=False, style="led")

    def test_choice(self):
        with mock.patch.dict(os.environ, self.CLEAN, clear=True):
            self.assertTrue(C.legacy_blocks({"meter_blocks": "legacy"}, "xterm"))
            self.assertFalse(C.legacy_blocks({"meter_blocks": "plain"}, "kitty 0.35.2"))
            for who, want in (("kitty 0.35.2", True), ("ghostty 1.1.0", True), ("wezterm 20240203", True),
                              ("foot(1.16.2)", True), ("tilix / vte 7600", True), ("xterm-256color", False),
                              ("apple_terminal", False), ("", False)):
                self.assertEqual(C.legacy_blocks({}, who), want, who)
        with mock.patch.dict(os.environ, dict(self.CLEAN, VTE_VERSION="7600"), clear=True):
            self.assertTrue(C.legacy_blocks({}, ""))
        self.assertEqual(C.meter_blocks_of({"meter_blocks": "bogus"}), "auto")

    def test_plain_glyphs(self):
        for blocks, want in ((True, "\U0001FB13" * 3), (False, "▌" * 3)):
            C.METER.update(blocks=blocks, style="led")
            self.assertEqual(C.meter_block(3, "accent")[0][0], want)
        C.METER.update(blocks=False, style="bricks")
        self.assertEqual(C.meter_block(2, "accent"), [("▊▊", "accent")])
        C.METER.update(blocks=True, style="bricks")
        self.assertEqual(C.meter_block(2, "accent"), [("\U0001FB7E\U0001FB7E", ("rev", "accent"))])

    def test_the_tube_sets_it(self):
        c = C.CRT()
        with mock.patch.dict(os.environ, self.CLEAN, clear=True):
            C.TERMINAL["name"] = "xterm-256color"
            c.configure({"crt": False, "meter_blocks": "auto"}, T.Theme("weatherstar"), "blocks", "truecolor")
            self.assertFalse(C.METER["blocks"])
            C.TERMINAL["name"] = "kitty 0.35.2"
            c.configure({"crt": False, "meter_blocks": "auto"}, T.Theme("weatherstar"), "kitty", "truecolor")
            self.assertTrue(C.METER["blocks"])
            c.configure({"crt": False, "meter_blocks": "plain"}, T.Theme("weatherstar"), "kitty", "truecolor")
            self.assertFalse(C.METER["blocks"])
        C.TERMINAL["name"] = ""


@unittest.skipIf(os.name == "nt", "select() can't wait on pipes on Windows")
class TestTerminalBackground(unittest.TestCase):
    def probe(self, reply):
        t = TM.Terminal.__new__(TM.Terminal)
        t.caps = TM.Caps()
        t.buf = b""
        rin, win = os.pipe()
        rout, wout = os.pipe()
        os.write(win, reply)
        t.fd_in, t.fd_out = rin, wout
        try:
            caps = t.probe(timeout=0.3)
            asked = os.read(rout, 4096)
        finally:
            for fd in (rin, win, rout, wout):
                os.close(fd)
        return caps, t.buf, asked

    def test_osc11(self):
        caps, rest, asked = self.probe(b"\x1b[6;18;9t\x1b]11;rgb:1e1e/2020/3030\x1b\\\x1b[?62;4c")
        self.assertIn(b"\x1b]11;?", asked)
        self.assertEqual(caps.bg_rgb, (30, 32, 48))
        self.assertEqual(rest, b"")                                  # nothing left over as keystrokes
        caps, rest, _ = self.probe(b"\x1b[6;18;9t\x1b]11;rgb:ff/80/00\x07\x1b[?62c")
        self.assertEqual(caps.bg_rgb, (255, 128, 0))
        self.assertEqual(rest, b"")
        caps, _, _ = self.probe(b"\x1b[6;18;9t\x1b[?62c")
        self.assertIsNone(caps.bg_rgb)


if __name__ == "__main__":
    unittest.main()
