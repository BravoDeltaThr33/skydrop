"""Performance: the glass's moving parts step together, at most every 0.1 s and in one
write; the glow is cached a row at a time; the bookkeeping is light; and the radar loop's
frames keep their own due times."""

import os
import random
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp(prefix="sd-cache-"))
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sd-config-"))

from skydrop import crt as C  # noqa: E402
from skydrop import gfx  # noqa: E402
from skydrop import theme as T  # noqa: E402
from skydrop.canvas import Canvas, ImageRegistry, Renderer  # noqa: E402

T0 = 1_000_000.0
QUIET = {"type": 0, "flash": 0, "trails": 0, "slips": 0, "blink": 0, "power": 0, "post": 0, "burn": 0}
CELL = (9, 18)


def tube(phosphor="green", **levels):
    c = C.CRT()
    with mock.patch("time.time", return_value=T0):
        c.configure({"crt": True, "crt_phosphor": phosphor, "crt_levels": levels}, T.Theme("weatherstar"),
                    "kitty", "truecolor")
    c.boot = c.post = None
    return c


def screen(seed, W=80, H=24):
    r = random.Random(seed)
    cv = Canvas(W, H, "text")
    for y in range(H):
        x = 0
        while x < W:
            x = cv.put(y, x, [("".join(r.choice("abc 0123─▮") for _ in range(r.randint(1, 9))),
                               r.choice(["text", "value", "label", "dim", "title"]))])
    return cv


class Out:
    def __init__(self):
        self.writes = []

    def write(self, s):
        self.writes.append(s)

    def flush(self):
        pass


class TestOneClock(unittest.TestCase):
    def run_glass(self, c, seconds=2.0, step=0.005):
        """The glass set up at T0, then asked for its next step every few milliseconds:
        [(when, what it wrote)] - the glow left out, it is measured on its own below."""
        c.process(screen(1), T0)
        out = []
        with mock.patch("time.time", return_value=T0):
            c.glass_frame(out, T0, 80, 24, CELL)
        calls = []
        for i in range(1, int(seconds / step)):
            t = T0 + i * step
            out = []
            c.glow_due = False
            c.glass_frame(out, t, 80, 24, CELL)
            calls.append((t, "".join(out)))
        return calls

    def test_moving_parts_step_together(self):
        calls = self.run_glass(tube(**QUIET))
        stepped = [t for t, s in calls if s]
        for t, s in calls:
            if s:
                self.assertEqual(s.count("\x1b7"), 1, t)       # one write, the cursor kept once
                self.assertIn("i=904,c=", s)                   # the grain's next frame
                self.assertIn("i=903,", s)                     # and the hum bar, together
        gaps = [b - a for a, b in zip(stepped, stepped[1:])]
        self.assertTrue(all(g >= C.TICK - 1e-6 for g in gaps), gaps)
        self.assertTrue(all(g <= C.TICK + 0.006 for g in gaps), gaps)
        self.assertGreaterEqual(len(stepped), 18)

    def test_still_glass_sends_nothing(self):
        c = tube(grain=0, flicker=0, hum=0, **QUIET)
        for t, s in self.run_glass(c):
            self.assertEqual(s, "", t)
        self.assertTrue(c.glass.parts)                         # the still pictures are up
        self.assertFalse(c.glass.moving())
        self.assertIsNone(c.glass.next_step(T0 + 0.5))

    def test_wakes_for_the_next_step(self):
        c = tube(**QUIET)
        self.run_glass(c, seconds=0.3)
        g = c.glass
        at = g.step_at
        for dt in (0.0, 0.013, 0.05, 0.099):
            self.assertAlmostEqual(g.next_step(at + dt), C.TICK - dt, delta=1e-6)
        self.assertEqual(g.next_step(at + 0.2), 0.0)
        c.glow_due = False
        self.assertAlmostEqual(c.next_wake(at + 0.03), 0.07, delta=1e-6)

    def test_flicker_follows_its_pattern(self):
        self.assertEqual(C.VEIL, (0, 0, 4, 0, 8, 0, 0, 3, 0, 0))
        for level in (1, 5, 10):
            c = tube(grain=0, hum=0, flicker=level, **QUIET)
            self.run_glass(c, seconds=0.05)
            g = c.glass
            self.assertEqual(g.veil_frames, {0: 1, 3: 2, 4: 3, 8: 4})
            t = g.step_at
            for _ in range(25):
                t += C.TICK + 0.001
                g.step(t)
                g.take()
                want = g.veil_frames[C.VEIL[int(t / C.TICK) % 10]]
                self.assertEqual(g.frames.get(g.VEIL, 1), want, (level, t))

    def test_one_write_per_repaint(self):
        for levels, moving in ((dict(QUIET, bloom=0, focus=0, fringes=0), True),
                               (dict(QUIET, bloom=0, focus=0, fringes=0, grain=0, flicker=0, hum=0), False)):
            term = Out()
            r = Renderer(term, T.Theme("weatherstar"), "truecolor", "kitty")
            with mock.patch("time.time", return_value=T0):
                r.configure(cfg={"crt": True, "crt_levels": levels})
                r.crt.boot = r.crt.post = None
                reg = ImageRegistry()
                reg.mode = "kitty"
                r.present(screen(2), reg, CELL)
            term.writes.clear()
            written = []
            for i in range(1, 300):
                t = T0 + i * 0.005
                n = len(term.writes)
                with mock.patch("time.time", return_value=t):
                    r.repaint()
                self.assertLessEqual(len(term.writes) - n, 1, t)
                if len(term.writes) > n:
                    written.append(t)
            if moving:
                gaps = [b - a for a, b in zip(written, written[1:])]
                self.assertGreaterEqual(len(written), 13)
                self.assertTrue(all(g >= C.TICK - 1e-6 for g in gaps), gaps)
            else:
                self.assertEqual(term.writes, [])
                self.assertIsNone(r.crt.wake_at)

    def test_hum_restart_comes_in_at_the_top(self):
        c = tube(grain=0, flicker=0, **QUIET)
        self.run_glass(c, seconds=4.0)                         # the bar is part way down
        g = c.glass
        self.assertGreater(g.hum_top, 0)
        now = T0 + 4.037                                       # pressed between two steps
        with mock.patch("time.time", return_value=now):
            self.assertIn("top", c.try_effect("hum"))
        self.assertEqual(g.hum_top, -g.hum_h)
        first, t = None, now
        while t < now + 1.0 and first is None:
            t += 0.01
            g.step(t)
            placed = [s for s in g.take().split("\x1b[") if "i=903,p=1,z=13" in s]
            if placed:
                first = placed[0]
        self.assertIsNotNone(first)
        self.assertTrue(first.startswith("1;1H"), repr(first[:12]))   # it comes in at the top


class TestGlowCache(unittest.TestCase):
    def setUp(self):
        if not gfx.HAVE_PIL:
            self.skipTest("no Pillow")

    @staticmethod
    def glow(c, cv, t, W, H):
        """The glow's layers as the glass would be sent them."""
        c.process(cv, t)
        if c.glass.geometry is None or c.glass.geometry[:2] != (W, H):
            c.glass.setup(W, H, *CELL, c.phosphor)
        got = {}
        with mock.patch.object(c.glass, "show_glow", side_effect=lambda layers: got.update(layers)):
            c.send_glow()
        return got

    def test_cached_rows_give_the_same_bytes(self):
        W, H = 90, 26
        for seed in range(3):
            warm = tube(**QUIET)
            for s in range(seed * 10, seed * 10 + 4):            # rows seen before, some shared
                self.glow(warm, screen(s, W, H), T0 + s, W, H)
            cv = screen(seed * 10 + 1, W, H)
            cv.put(3, 2, [("12:34:56", "value")])                 # a few rows changed
            cv.put(H - 1, 0, [("crawl " * 20, "text")])
            cold = tube(**QUIET)
            a, b = self.glow(warm, cv, T0 + 100, W, H), self.glow(cold, cv, T0 + 100, W, H)
            self.assertEqual(sorted(a), [0, 2, 4])
            self.assertEqual(a, b, seed)

    def test_cache_follows_levels_colours_and_size(self):
        warm = tube(**QUIET)
        self.glow(warm, screen(7, 80, 24), T0, 80, 24)
        for change, (W, H) in (({"crt_levels": {"bloom": 9}}, (80, 24)), ({"crt_levels": {"blacks": 9}}, (80, 24)),
                               ({"crt_phosphor": "amber"}, (80, 24)), ({}, (100, 30))):
            cfg = {"crt": True, "crt_phosphor": "green", "crt_levels": dict(QUIET)}
            for k, v in change.items():
                if k == "crt_levels":
                    cfg[k].update(v)
                else:
                    cfg[k] = v
            with mock.patch("time.time", return_value=T0):
                warm.configure(cfg, T.Theme("weatherstar"), "kitty", "truecolor")
            warm.boot = warm.post = None
            cold = C.CRT()
            with mock.patch("time.time", return_value=T0):
                cold.configure(cfg, T.Theme("weatherstar"), "kitty", "truecolor")
            cold.boot = cold.post = None
            cv = screen(7, W, H)
            self.assertEqual(self.glow(warm, cv, T0 + 5, W, H), self.glow(cold, cv, T0 + 5, W, H), change)

    def test_glow_at_most_every_so_often(self):
        c = tube(**QUIET)
        sent = []
        c.process(screen(1), T0)
        with mock.patch.object(C.Glass, "show_glow", side_effect=lambda self_, layers: sent.append(1),
                               autospec=True):
            for i in range(200):                               # a screen that changes every 10 ms
                t = T0 + i * 0.01
                c.process(screen(i % 7), t)
                with mock.patch("time.time", return_value=t):
                    c.glass_frame([], t, 80, 24, CELL)
        self.assertLessEqual(len(sent), int(2.0 / C.GLOW_EVERY) + 1)
        self.assertGreaterEqual(len(sent), int(2.0 / C.GLOW_EVERY) - 1)


class TestBookkeeping(unittest.TestCase):
    def test_runs_match_cell_by_cell(self):
        r = random.Random(11)
        alphabet = list("abcdefgh 0123456789.,:-()") + ["─", "▮", "°", "é", "漢", "💧", "\t"]
        for _ in range(500):
            W = r.randint(5, 40)
            text = "".join(r.choice(alphabet) for _ in range(r.randint(0, 30)))
            x = r.randint(-6, W)
            width = r.choice([None, r.randint(0, W)])
            run = Canvas(W, 1, "text")
            one = Canvas(W, 1, "text")
            end = run.put(0, x, [(text, "value")], width)
            col = x
            for ch in text:                                  # the same text, a character at a time
                col = one.put(0, col, [(ch, "value")], None if width is None else max(0, width - (col - x)))
            self.assertEqual(run.chars, one.chars, (text, x, width))
            self.assertEqual(run.styles, one.styles, (text, x, width))
            self.assertEqual(end, col, (text, x, width))

    def test_no_write_when_nothing_changed(self):
        term = Out()
        r = Renderer(term, T.Theme("weatherstar"), "truecolor", "blocks")
        reg = ImageRegistry()
        cv = screen(3)
        r.present(cv, reg)
        n = len(term.writes)
        r.present(cv, reg)
        r.present(screen(3), reg)
        self.assertEqual(len(term.writes), n)
        cv2 = screen(3)
        cv2.put(0, 0, [("X", "value")])
        r.present(cv2, reg)
        self.assertEqual(len(term.writes), n + 1)

    def test_still_tube_sends_nothing(self):
        term = Out()
        r = Renderer(term, T.Theme("weatherstar"), "truecolor", "blocks")
        with mock.patch("time.time", return_value=T0):
            r.configure(cfg={"crt": True, "crt_levels": dict(QUIET)})
            r.crt.boot = r.crt.post = None
            reg = ImageRegistry()
            r.present(screen(3), reg)
        n = len(term.writes)
        for i in range(1, 50):
            with mock.patch("time.time", return_value=T0 + i * 0.05):
                r.present(screen(3), reg)
        self.assertEqual(len(term.writes), n)
        self.assertIsNone(r.crt.wake_at)

    def test_fades_wake_when_they_change(self):
        c = tube(**dict(QUIET, flash=5, grain=0, flicker=0, hum=0))
        cv = screen(4)
        c.process(cv, T0)
        cv2 = screen(4)
        cv2.put(5, 10, [("42", "value")])
        c.fresh = True
        c.process(cv2, T0 + 5)
        self.assertTrue(c.flashes)
        c.glow_due = False                               # (the glow's own wake isn't measured here)
        self.assertAlmostEqual(c.next_wake(T0 + 5), 0.016, delta=1e-6)
        c.process(cv2, T0 + 5 + sum(C.FLASH) + 0.01)
        self.assertFalse(c.flashes)
        c.glow_due = False
        self.assertIsNone(c.next_wake(T0 + 5.27))        # nothing left to fade: no wakes at all

    def test_style_rows_mapped_once(self):
        c = tube(**QUIET)
        a = c.process(screen(5), T0)
        n = len(c._rows)
        b = c.process(screen(5), T0 + 1)
        self.assertEqual(len(c._rows), n)
        self.assertEqual(a.styles, b.styles)
        self.assertEqual(a.chars, b.chars)

    def test_same_frame_twice_is_composed_once(self):
        c = tube(**QUIET)
        cv = screen(6)
        a = c.process(cv, T0)
        self.assertIs(c.process(cv, T0 + 0.5), a)
        cv2 = screen(6)
        b = c.process(cv2, T0 + 1)
        self.assertIsNot(b, a)
        self.assertEqual(b.chars, a.chars)


class TestRadarClock(unittest.TestCase):
    def app(self, speed):
        from skydrop import app as A
        idx = next(i for i, s in enumerate(A.SCREENS) if s[0] == "radar")
        st = {"playing": True, "nframes": 6, "frame": 0, "tick": 0.0, "hold": 0}
        return A, types.SimpleNamespace(radar_state=st, idx=idx, local8s=None, cfg={"radar_speed": speed})

    def test_frames_on_due_times(self):
        A, app = self.app(4)
        st = app.radar_state
        r = random.Random(1)
        t = T0
        st["tick"] = T0
        advanced = []
        while t < T0 + 10:
            t += r.choice([0.004, 0.02, 0.05, 0.1, 0.11])        # wakes for keys, the glass, anything
            if A.App.animate(app, t):
                advanced.append(t)
            due = A.App.next_frame_at(app)
            k = round((due - T0) / 0.25)
            self.assertAlmostEqual(due, T0 + k * 0.25, delta=1e-6)   # the due times stay on their grid
            self.assertGreater(due, t - 0.25)
        steps = [b - a for a, b in zip(advanced, advanced[1:])]
        self.assertTrue(all(s > 0.13 for s in steps), steps)   # never early, however often it wakes

    def test_long_pause_starts_fresh(self):
        A, app = self.app(4)
        st = app.radar_state
        st["tick"] = T0
        self.assertTrue(A.App.animate(app, T0 + 30))
        self.assertEqual(st["tick"], T0 + 30)


if __name__ == "__main__":
    unittest.main()
