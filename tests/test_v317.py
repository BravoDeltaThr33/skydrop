"""3.17: skydrop in a window of its own - the Windows app (skydrop.exe) and skydrop
--window: the screen model (vt), the painter (paint) with its Ghostty-sized cells and
the characters it draws itself, the window's keys, mouse, pastes and selections, the
real app running in the window's terminal, the CLI's window mode and the .exe's ways
(its console, sounds and background watch), the Settings rows for the window's font,
--licenses, and the tools that build and check the .exe."""

import base64
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp(prefix="sd-cache-"))
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sd-config-"))

from PIL import Image  # noqa: E402

from skydrop import VERSION, cli, paint, speech, vt, watch, window  # noqa: E402
from skydrop import sources as S  # noqa: E402
from skydrop.config import DEFAULTS  # noqa: E402
from skydrop.settings import FIELD_BY_KEY, parse_field  # noqa: E402

FONTS = os.path.join(ROOT, "skydrop", "fonts")


def offline_cfg(**extra):
    """Settings for the app with every data source off (no network in tests)."""
    cfg = json.loads(json.dumps(DEFAULTS))
    cfg.update(lat=32.78, lon=-96.80, zip="75201", location_name="Dallas", refresh_seconds=3600)
    cfg["sources"] = {s.id: {"enabled": False} for s in S.SOURCES}
    cfg.update(extra)
    return cfg


def pixels(img):
    """Every pixel of a picture, row by row."""
    px = img.load()
    return [px[x, y] for y in range(img.size[1]) for x in range(img.size[0])]


def lit(img):
    """The lit pixels of a mask, as a set of (x, y)."""
    w, h = img.size
    px = img.load()
    return {(x, y) for y in range(h) for x in range(w) if px[x, y] >= 128}


class TestVersion(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(tuple(int(x) for x in VERSION.split(".")), (3, 17, 0))

    def test_installers_say_the_same(self):
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as fh:
            self.assertIn(f'VERSION="{VERSION}"', fh.read())
        with open(os.path.join(ROOT, "install.ps1"), encoding="utf-8") as fh:
            self.assertIn(f'$Version = "{VERSION}"', fh.read())

    def test_changelog_and_readme(self):
        with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as fh:
            self.assertIn(f"## {VERSION}", fh.read())
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn(f"skydrop-{VERSION}-windows.exe", readme)
        self.assertIn("skydrop --window", readme)


# ---------------------------------------------------------------------------- the screen
class TestScreen(unittest.TestCase):
    def test_text_colors_and_moves(self):
        s = vt.Screen(20, 5)
        s.feed("\x1b[2;3Hhi\x1b[1;38;2;10;20;30;48;5;196mX\x1b[0m")
        self.assertEqual(s.chars[1][2:5], ["h", "i", "X"])
        fg, bg, attrs = s.styles[1][4]
        self.assertEqual(fg, (10, 20, 30))
        self.assertEqual(bg, vt.palette(196))
        self.assertTrue(attrs & vt.BOLD)
        self.assertEqual(s.styles[1][5], vt.PLAIN)

    def test_pieces_cut_anywhere(self):
        s = vt.Screen(20, 5)
        for piece in ("\x1b[", "3;", "4H", "\x1b]0;ti", "tle\x07", "ok"):
            s.feed(piece)
        self.assertEqual(s.chars[2][3:5], ["o", "k"])
        self.assertEqual(s.title, "title")

    def test_wide_characters_take_two_cells(self):
        s = vt.Screen(10, 2)
        s.feed("a💧b")
        self.assertEqual(s.chars[0][:4], ["a", "💧", "", "b"])

    def test_erase_and_scroll(self):
        s = vt.Screen(10, 3)
        s.feed("one\r\ntwo\r\nthree\r\nfour")
        self.assertEqual([ln.rstrip() for ln in s.text().split("\n")], ["two", "three", "four"])
        s.feed("\x1b[2J")
        self.assertEqual(s.text().strip(), "")

    def test_answers_what_skydrop_asks(self):
        s = vt.Screen(110, 40, (10, 22), "skydrop window", "9.9")
        s.feed("\x1b[c\x1b[>q\x1b[14t\x1b[18t\x1b]11;?\x07")
        got = s.take_replies()
        self.assertIn("\x1b[?62;22c", got)
        self.assertIn("skydrop window 9.9", got)
        self.assertIn("\x1b[4;880;1100t", got)
        self.assertIn("\x1b[8;40;110t", got)
        self.assertIn("\x1b]11;rgb:0000/0000/0000", got)

    def test_modes(self):
        s = vt.Screen(10, 3)
        s.feed("\x1b[?1h\x1b[?1000h\x1b[?1006h\x1b[?2004h\x1b[?25l")
        self.assertTrue(s.app_cursor and s.mouse and s.mouse_sgr and s.bracketed_paste)
        self.assertFalse(s.cursor_visible)

    def test_clipboard(self):
        s = vt.Screen(10, 3)
        s.feed("\x1b]52;c;" + base64.b64encode("75201".encode()).decode() + "\x1b\\")
        self.assertEqual(s.clipboard, "75201")

    def test_kitty_pictures(self):
        s = vt.Screen(20, 10, (10, 20))
        img = Image.new("RGBA", (30, 40), (255, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        data = base64.b64encode(buf.getvalue()).decode()
        # in two chunks, then placed over 3 x 2 cells at row 1, column 2
        s.feed(f"\x1b_Ga=t,f=100,i=7,m=1;{data[:40]}\x1b\\\x1b_Gm=0;{data[40:]}\x1b\\")
        self.assertIn(7, s.pictures)
        self.assertEqual(s.pictures[7].image.size, (30, 40))
        s.feed("\x1b[2;3H\x1b_Ga=p,i=7,p=1,c=3,r=2,C=1,z=-1\x1b\\")
        pl = s.placements[(7, 1)]
        self.assertEqual((pl.row, pl.col, pl.cols, pl.rows, pl.z), (1, 2, 3, 2, -1))
        raw = zlib.compress(bytes([0, 255, 0]) * 4)
        s.feed("\x1b_Ga=T,f=24,s=2,v=2,o=z,i=8,q=2;" + base64.b64encode(raw).decode() + "\x1b\\")
        self.assertEqual(s.pictures[8].image.getpixel((1, 1)), (0, 255, 0))
        s.feed("\x1b_Ga=d,d=i,i=7\x1b\\")
        self.assertNotIn((7, 1), s.placements)
        s.feed("\x1b_Ga=q,i=31,s=1,v=1,f=24;AAAA\x1b\\")
        self.assertIn("\x1b_Gi=31;OK\x1b\\", s.take_replies())
        self.assertEqual(s.problems, [])

    def test_junk_never_stops_it(self):
        s = vt.Screen(10, 3)
        s.feed("\x1b_Ga=T,i=1,f=100;not base64!\x1b\\\x1b[999;999H\x1b[38;2m\x1bZ\x07\x1b[Hok")
        self.assertTrue(s.text().startswith("ok"))
        self.assertTrue(s.problems)

    def test_resize_keeps_the_cells(self):
        s = vt.Screen(10, 3)
        s.feed("abcdefghij")
        s.resize(5, 4)
        self.assertEqual(s.chars[0], list("abcde"))
        self.assertEqual(len(s.chars), 4)


# ---------------------------------------------------------------------------- the painter
class TestCell(unittest.TestCase):
    def test_jetbrains_mono_as_ghostty_sizes_it(self):
        p = paint.Painter("jetbrains", 12, 96)
        self.assertEqual(p.font_label, "JetBrains Mono")
        self.assertEqual(p.cell, (10, 22))
        self.assertEqual(p.baseline, 17)
        self.assertEqual(p.line, 1)
        self.assertTrue(p.regular.path.endswith("JetBrainsMono-Regular.ttf"))

    def test_dejavu(self):
        p = paint.Painter("dejavu", 12, 96)
        self.assertEqual(p.font_label, "DejaVu Sans Mono")
        self.assertEqual(p.cell, (10, 19))

    def test_bigger_font_and_screen(self):
        small, big = paint.Painter("jetbrains", 12, 96), paint.Painter("jetbrains", 12, 192)
        self.assertGreater(big.cell[0], small.cell[0] * 1.8)
        self.assertTrue(small.set_font(size=13))
        self.assertEqual(small.cell, (11, 23))

    def test_settings_values(self):
        self.assertEqual(paint.font_key("DejaVu Sans Mono"), "dejavu")
        self.assertEqual(paint.font_key("nonsense"), "jetbrains")
        self.assertEqual(paint.font_size("200"), paint.SIZE_RANGE[1])
        self.assertEqual(paint.font_size("x"), paint.DEFAULT_SIZE)
        self.assertEqual(window.window_settings({"window_font": "dejavu", "window_font_size": 14}),
                         ("dejavu", 14.0))
        self.assertEqual(window.window_settings({}), ("jetbrains", 12.0))

    def test_zround_is_zigs(self):
        self.assertEqual([paint.zround(v) for v in (0.5, 1.5, 2.5, -0.5, -1.5, 2.49)], [1, 2, 3, -1, -2, 2])


class TestSprites(unittest.TestCase):
    """The characters the window draws itself (as Ghostty does), in a 10 x 22 cell."""

    def setUp(self):
        self.sp = paint.Sprites(10, 22, 1)

    def test_box_names(self):
        self.assertEqual(paint.box_arms("─"), ("line", (0, 1, 0, 1)))
        self.assertEqual(paint.box_arms("┃"), ("line", (2, 0, 2, 0)))
        self.assertEqual(paint.box_arms("╞"), ("line", (1, 3, 1, 0)))
        self.assertEqual(paint.box_arms("┞"), ("line", (2, 1, 1, 0)))     # UP HEAVY AND RIGHT DOWN LIGHT
        self.assertEqual(paint.box_arms("┡"), ("line", (2, 2, 1, 0)))     # DOWN LIGHT AND RIGHT UP HEAVY
        self.assertEqual(paint.box_arms("╌")[0], "dash")
        self.assertEqual(paint.box_arms("╭")[0], "arc")
        self.assertIsNone(paint.box_arms("A"))

    def test_lines_meet_across_cells(self):
        h, v = lit(self.sp.mask("─")), lit(self.sp.mask("│"))
        rows = {y for _x, y in h}
        cols = {x for x, _y in v}
        self.assertEqual(len(rows), 1)                       # one pixel: the underline's thickness
        self.assertEqual({x for x, _y in h}, set(range(10)))  # edge to edge
        self.assertEqual(len(cols), 1)
        self.assertEqual({y for _x, y in v}, set(range(22)))
        cross = lit(self.sp.mask("┼"))
        self.assertEqual(cross, h | v)
        heavy = lit(self.sp.mask("━"))
        self.assertEqual(len({y for _x, y in heavy}), 2)       # heavy: twice as thick

    def test_round_corner_joins_its_lines(self):
        arc = lit(self.sp.mask("╭"))
        hrow = next(iter({y for _x, y in lit(self.sp.mask("─"))}))
        vcol = next(iter({x for x, _y in lit(self.sp.mask("│"))}))
        self.assertIn((9, hrow), arc)                         # it leaves by the right edge on the line
        self.assertIn((vcol, 21), arc)                        # and by the bottom edge on the line
        self.assertNotIn((0, 0), arc)

    def test_blocks(self):
        full = lit(self.sp.mask("█"))
        self.assertEqual(len(full), 220)
        top, bottom = lit(self.sp.mask("▀")), lit(self.sp.mask("▄"))
        self.assertEqual(top | bottom, full)
        self.assertFalse(top & bottom)
        self.assertEqual(len(lit(self.sp.mask("▌"))), 5 * 22)

    def test_sextants_tile_the_cell(self):
        parts = []
        for code in range(paint.SEXTANT_BASE, 0x1FB3C):
            if bin(paint.sextant_bits(code)).count("1") == 1:
                parts.append(lit(self.sp.mask(chr(code))))
        self.assertEqual(len(parts), 6)
        union = set().union(*parts)
        self.assertEqual(len(union), 220)
        self.assertEqual(sum(len(p) for p in parts), 220)     # no overlap

    def test_braille(self):
        dots = lit(self.sp.mask("⣿"))
        one = lit(self.sp.mask("⠁"))
        self.assertTrue(one)
        self.assertEqual(len(dots), 8 * len(one))
        self.assertTrue(paint.is_sprite("⣿") and paint.is_sprite("█") and not paint.is_sprite("A"))


class TestPainting(unittest.TestCase):
    def test_paints_the_screen(self):
        p = paint.Painter("jetbrains", 12, 96)
        s = vt.Screen(20, 4, p.cell)
        s.feed("\x1b[48;2;0;0;160m\x1b[2J\x1b[H\x1b[38;2;255;255;0mHello ╭──╮ 💧")
        p.paint(s)
        img = p.snapshot()
        self.assertEqual(img.size, (200, 88))
        self.assertEqual(img.getpixel((199, 87)), (0, 0, 160))       # the background
        text = pixels(img.crop((0, 0, 50, 22)))
        self.assertTrue(any(r > 230 and g > 230 and b < 60 for r, g, b in text))   # the yellow text
        drop = img.crop((11 * 10, 0, 13 * 10, 22))
        self.assertTrue(any(b > 150 and r < 120 for r, g, b in pixels(drop)))     # a blue droplet
        got = p.take_rows()
        self.assertEqual(got[:3], (20, 4, (10, 22)))
        self.assertEqual(set(got[3]), {0, 1, 2, 3})
        self.assertIsNone(p.take_rows())

    def test_pictures_over_the_text(self):
        p = paint.Painter("jetbrains", 12, 96)
        s = vt.Screen(20, 4, p.cell)
        img = Image.new("RGB", (40, 44), (0, 200, 0))
        raw = base64.b64encode(img.tobytes()).decode()
        s.feed(f"\x1b[2;3H\x1b_Ga=T,f=24,s=40,v=44,i=3,q=2;{raw}\x1b\\")
        p.paint(s)
        shot = p.snapshot()
        self.assertEqual(shot.getpixel((25, 25)), (0, 200, 0))       # row 1, column 2 on
        self.assertEqual(shot.getpixel((5, 5)), vt.DEFAULT_BG)

    def test_dim_and_reverse(self):
        p = paint.Painter("jetbrains", 12, 96)
        fg, bg = p.colors(((200, 100, 0), (0, 0, 0), vt.DIM))
        self.assertEqual((fg, bg), ((100, 50, 0), (0, 0, 0)))
        fg, bg = p.colors(((200, 100, 0), (0, 0, 0), vt.REVERSE))
        self.assertEqual((fg, bg), ((0, 0, 0), (200, 100, 0)))


# ---------------------------------------------------------------------------- input
class TestKeys(unittest.TestCase):
    def test_keys_as_ghostty_sends_them(self):
        kb = window.key_bytes
        self.assertEqual(kb("Up", "", 0), "\x1b[A")
        self.assertEqual(kb("Up", "", 0, app_cursor=True), "\x1bOA")
        self.assertEqual(kb("Right", "", window.CONTROL), "\x1b[1;5C")
        self.assertEqual(kb("Prior", "", 0), "\x1b[5~")
        self.assertEqual(kb("Delete", "", 0), "\x1b[3~")
        self.assertEqual(kb("F1", "", 0), "\x1bOP")
        self.assertEqual(kb("F5", "", 0), "\x1b[15~")
        self.assertEqual(kb("Tab", "\t", window.SHIFT), "\x1b[Z")
        self.assertEqual(kb("Return", "\r", 0), "\r")
        self.assertEqual(kb("BackSpace", "\x08", 0), "\x7f")
        self.assertEqual(kb("Escape", "\x1b", 0), "\x1b")
        self.assertEqual(kb("s", "\x13", window.CONTROL), "\x13")
        self.assertEqual(kb("l", "l", window.CONTROL), "\x0c")
        self.assertEqual(kb("question", "?", window.SHIFT), "?")
        self.assertIsNone(kb("Shift_L", "", 0))

    def test_alt_on_windows_and_x11(self):
        self.assertEqual(window.key_bytes("x", "x", window.ALT_X11, windows=False), "\x1bx")
        self.assertEqual(window.key_bytes("x", "x", window.ALT_WINDOWS, windows=True), "\x1bx")
        # NumLock on Windows is X11's Alt bit: it changes nothing
        self.assertEqual(window.key_bytes("x", "x", window.ALT_X11, windows=True), "x")
        # AltGr (Ctrl+Alt on Windows) makes a character of its own
        self.assertEqual(window.key_bytes("at", "@", window.CONTROL | window.ALT_WINDOWS, windows=True), "@")

    def test_mouse(self):
        mb = window.mouse_bytes
        self.assertEqual(mb("press", 0, 5, 7), "\x1b[<0;5;7M")
        self.assertEqual(mb("release", 0, 5, 7), "\x1b[<0;5;7m")
        self.assertEqual(mb("drag", 0, 6, 7), "\x1b[<32;6;7M")
        self.assertEqual(mb("wheel_up", 0, 1, 1), "\x1b[<64;1;1M")
        self.assertEqual(mb("press", 2, 1, 1, ctrl=True), "\x1b[<18;1;1M")
        self.assertEqual(mb("press", 0, 1, 1, sgr=False), "\x1b[M !!")

    def test_paste(self):
        self.assertEqual(window.paste_bytes("a\r\nb\nc", False), "a\rb\rc")
        self.assertEqual(window.paste_bytes("x\x1b[201~y", True), "\x1b[200~xy\x1b[201~")

    def test_selection(self):
        s = vt.Screen(10, 3)
        s.feed("one  \r\ntwo three\r\nfour")
        self.assertEqual(window.selection_text(s, (1, 0), (2, 1)), "ne\ntwo")
        self.assertEqual(window.selection_text(s, (3, 2), (0, 1)), "two three\nfour")


# ---------------------------------------------------------------------------- the app in the window
def run_in_window(cfg, keys=(), cols=110, rows=40, settle=1.0):
    term_ = window.WindowTerminal(cols, rows)
    term_.problem = None
    from skydrop.store import DataStore
    store = DataStore(cfg, persist=False)
    result = {}

    def app():
        result["rc"] = window.run_app(term_, cfg, store, 0)

    th = threading.Thread(target=app, daemon=True)
    texts = []
    with mock.patch("urllib.request.urlopen", side_effect=OSError("offline in tests")):
        th.start()
        t0 = time.time()
        while term_.frames == 0 and time.time() - t0 < 30:
            time.sleep(0.05)
        time.sleep(settle)
        texts.append(term_.screen.text())
        for key in keys:
            if isinstance(key, tuple):
                term_.resize_to(*key)
            else:
                term_.give(key.encode())
            time.sleep(settle)
            texts.append(term_.screen.text())
        shot = term_.painter.snapshot()
        term_.close()
        th.join(15)
    return term_, result, texts, shot


class TestAppInTheWindow(unittest.TestCase):
    def test_runs_answers_and_ends(self):
        term_, result, texts, shot = run_in_window(offline_cfg(), keys=["?", "\x1b", (120, 42)])
        self.assertEqual(result.get("rc"), 0, term_.problem)
        self.assertIsNone(term_.problem)
        self.assertGreater(term_.frames, 0)
        self.assertEqual(term_.caps.name, "skydrop window")
        self.assertIn("skydrop", texts[0])
        self.assertIn("F11 or Alt-Enter full screen", texts[1])        # ? lists the window's keys
        self.assertEqual(shot.size, (120 * 10, 42 * 22))               # it followed the new size
        self.assertEqual((term_.screen.cols, term_.screen.rows), (120, 42))
        self.assertFalse(term_.screen.problems)

    def test_snapshot(self):
        out = os.path.join(tempfile.mkdtemp(prefix="sd-shot-"), "now.png")
        from skydrop.store import DataStore
        cfg = offline_cfg()
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline in tests")):
            rc = window.snapshot(cfg, DataStore(cfg, persist=False), out, wait=5, settle=0.5)
        self.assertEqual(rc, 0)
        with Image.open(out) as img:
            self.assertEqual(img.size, (1100, 880))

    def test_refont(self):
        term_ = window.WindowTerminal(80, 24)
        term_.refont(font="dejavu", size=14)
        self.assertEqual(term_.painter.font_label, "DejaVu Sans Mono")
        self.assertEqual((term_.caps.cell_w, term_.caps.cell_h), term_.painter.cell)
        self.assertNotEqual(term_.painter.cell, (10, 22))


def tk_root():
    """A Tk window, where this computer can show one (CI's Windows and macOS runners can)."""
    try:
        import importlib
        import tkinter as tk
        importlib.import_module("PIL.ImageTk")
    except ImportError:
        return None
    try:
        root = tk.Tk(className="skydrop")
    except tk.TclError:
        return None
    return root


class TestTheRealWindow(unittest.TestCase):
    def test_window_shows_the_app(self):
        root = tk_root()
        if root is None:
            self.skipTest("no window can open here")
        cfg = offline_cfg()
        term_ = window.WindowTerminal(window.COLS, window.ROWS)
        term_.problem = None
        win = window.Window(root, term_, cfg)
        from skydrop.store import DataStore
        store = DataStore(cfg, persist=False)
        result = {}

        def app():
            result["rc"] = window.run_app(term_, cfg, store, 0)
            win.closing = True

        th = threading.Thread(target=app, daemon=True)
        win.thread = th
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline in tests")):
            th.start()
            t0 = time.time()

            def settled():
                rows = win.grid[1]
                return rows and rows == term_.screen.rows and len(win.photos) == rows

            while time.time() - t0 < 30 and not settled():
                root.update()
                time.sleep(0.02)
            self.assertTrue(settled(), (win.grid, term_.screen.rows, len(win.photos)))   # every row shows
            ev = type("Event", (), {"keysym": "question", "char": "?", "state": window.SHIFT})()
            win.on_key(ev)
            t0 = time.time()
            while time.time() - t0 < 5 and "F11 or Alt-Enter" not in term_.screen.text():
                root.update()
                time.sleep(0.02)
            self.assertIn("F11 or Alt-Enter", term_.screen.text())
            win.on_close()
            t0 = time.time()
            while not win.done and time.time() - t0 < 10:
                try:
                    root.update()
                except Exception:
                    break
                time.sleep(0.02)
            th.join(10)
        self.assertTrue(win.done)
        self.assertEqual(result.get("rc"), 0)
        geo, state = win.geometry_left
        self.assertRegex(geo, r"^\d+x\d+[+-]\d+[+-]\d+$")


# ---------------------------------------------------------------------------- the CLI and the .exe
class TestWindowMode(unittest.TestCase):
    def test_terminal_variables_go(self):
        env = {"TERM": "xterm-ghostty", "TERM_PROGRAM": "ghostty", "GHOSTTY_RESOURCES_DIR": "/x",
               "KITTY_WINDOW_ID": "3", "KITTY_PID": "9", "VTE_VERSION": "7600", "WT_SESSION": "a",
               "HOME": "/home/x", "PATH": "/bin"}
        window.own_environment(env)
        self.assertEqual(env, {"TERM": "xterm-256color", "COLORTERM": "truecolor", "HOME": "/home/x",
                               "PATH": "/bin"})

    def test_probe(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ), mock.patch("sys.stdout", out):
            rc = cli.main(["--window", "--probe"])
        text = out.getvalue()
        self.assertIn("skydrop window", text)
        self.assertIn("JetBrains Mono 12 pt, 10x22 px cells", text)
        self.assertIn("Zones:", text)
        self.assertIn(rc, (0, 1))                    # 1 where no window can open (no display)

    def test_the_exe_opens_the_window_by_itself(self):
        calls = []

        class Exit(Exception):
            pass

        def fake_exit(rc):
            raise Exit(rc)

        with mock.patch.dict(os.environ), mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(window, "run", lambda *a, **k: calls.append(a) or 0), \
                mock.patch("os._exit", fake_exit):
            with self.assertRaises(Exit):
                cli.main(["--zip", "75201"])
            self.assertEqual(len(calls), 1)
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                self.assertEqual(cli.main(["--licenses"]), 0)       # a one-shot flag: no window
            self.assertEqual(len(calls), 1)
            self.assertIn("MIT License", out.getvalue())

    def test_the_exe_plays_and_watches_by_itself(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(speech.player_cmd("winsound", "x.wav"), [sys.executable, "--play-wav", "x.wav"])
            self.assertEqual(watch.command(), [os.path.abspath(sys.executable)])
            with mock.patch.object(watch, "watcher_pid", return_value=None), \
                    mock.patch("subprocess.Popen") as popen, mock.patch("time.sleep"):
                watch.start_watcher()
            env = popen.call_args.kwargs["env"]
            self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")
            self.assertEqual(popen.call_args.args[0], [os.path.abspath(sys.executable), "--watch"])
        self.assertEqual(speech.player_cmd("winsound", "x.wav")[:2], [sys.executable, "-c"])

    def test_play_wav_flag(self):
        if os.name == "nt":
            self.skipTest("Windows plays it")
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            self.assertEqual(cli.main(["--play-wav", "x.wav"]), 2)

    def test_console_for_the_exe(self):
        with mock.patch.object(cli.Console, "streams", None), \
                mock.patch("skydrop.osdeps.attach_console", return_value=None):
            c = cli.Console(0)
            c.write("nowhere")
            self.assertFalse(c.isatty())
            self.assertEqual(c.name, os.devnull)
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.Console, "streams", None), \
                mock.patch("skydrop.osdeps.attach_console", return_value=(buf_out, buf_err)):
            cli.Console(0).write("to the console")
            cli.Console(1).write("problem")
        self.assertEqual((buf_out.getvalue(), buf_err.getvalue()), ("to the console", "problem"))
        with mock.patch.object(cli.Console, "streams", None):
            cli.Console.quiet()
            self.assertEqual(cli.Console.streams[0].name, os.devnull)

    def test_licenses(self):
        text = cli.licenses_text()
        for want in ("Copyright (c) 2026 BravoDeltaThr33", "Ghostty contributors", "JetBrains Mono",
                     "SIL OPEN FONT LICENSE", "Noto Color Emoji", "Bitstream"):
            self.assertIn(want, text)


class TestSettings(unittest.TestCase):
    def test_rows(self):
        font, size = FIELD_BY_KEY["window_font"], FIELD_BY_KEY["window_font_size"]
        self.assertEqual(parse_field(font, "dejavu"), ("dejavu", None))
        self.assertEqual(parse_field(size, "14"), (14, None))
        self.assertIsNotNone(parse_field(size, "99")[1])
        self.assertEqual((DEFAULTS["window_font"], DEFAULTS["window_font_size"]), ("jetbrains", 12))
        self.assertEqual(paint.font_key(DEFAULTS["window_font"]), "jetbrains")
        for choice in font[3].split(":", 1)[1].split(","):
            self.assertEqual(paint.font_key(choice), choice)

    def test_set_from_the_command_line(self):
        cfg = dict(DEFAULTS)
        with mock.patch.object(cli, "save_config") as save:
            self.assertEqual(cli.set_options(cfg, ["window_font=dejavu", "window_font_size=16"]), 0)
        self.assertEqual(save.call_args.args[0]["window_font"], "dejavu")
        self.assertEqual(save.call_args.args[0]["window_font_size"], 16)


# ---------------------------------------------------------------------------- what ships
class TestFiles(unittest.TestCase):
    def test_fonts_and_their_licenses(self):
        for name in ("JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf", "DejaVuSansMono.ttf",
                     "DejaVuSansMono-Bold.ttf", "emoji-1f4a7.png"):
            self.assertTrue(os.path.isfile(os.path.join(FONTS, name)), name)
        with open(os.path.join(FONTS, "OFL-JetBrainsMono.txt"), encoding="utf-8") as fh:
            self.assertIn("JetBrainsMono-Regular.ttf", fh.read())
        with open(os.path.join(FONTS, "LICENSE-DejaVu.txt"), encoding="utf-8") as fh:
            self.assertIn("DejaVuSansMono.ttf", fh.read())
        with open(os.path.join(FONTS, "OFL-NotoColorEmoji.txt"), encoding="utf-8") as fh:
            self.assertIn("emoji-1f4a7.png", fh.read())
        with open(os.path.join(FONTS, "LICENSE-Ghostty.txt"), encoding="utf-8") as fh:
            self.assertIn("Permission is hereby granted", fh.read())

    def test_the_deb_takes_jetbrains_mono_but_not_dejavu(self):
        import build_release
        names = [rel for _full, rel in build_release.package_files(dejavu=False)]
        self.assertIn("skydrop/fonts/JetBrainsMono-Regular.ttf", names)
        self.assertIn("skydrop/fonts/emoji-1f4a7.png", names)
        self.assertFalse([n for n in names if "DejaVu" in n])
        everything = [rel for _full, rel in build_release.package_files()]
        self.assertIn("skydrop/fonts/DejaVuSansMono.ttf", everything)
        self.assertIn("python3-tk", build_release.CONTROL)


class TestExeTools(unittest.TestCase):
    def test_build_tools_are_pinned_and_hashed(self):
        with open(os.path.join(ROOT, "tools", "exe-requirements.txt"), encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
        names = set()
        for ln in lines:
            self.assertRegex(ln, r"^[A-Za-z0-9_.-]+==[0-9][^ ]* --hash=sha256:[0-9a-f]{64}$")
            names.add(ln.split("==")[0].lower())
        self.assertLessEqual({"pyinstaller", "pyinstaller-hooks-contrib", "pillow", "tzdata", "altgraph",
                              "pefile", "pywin32-ctypes", "packaging", "setuptools"}, names)

    def test_version_info(self):
        import build_exe
        text = build_exe.version_info("3.17.0")
        self.assertIn("filevers=(3, 17, 0, 0)", text)
        self.assertIn("StringStruct('ProductVersion', '3.17.0')", text)
        self.assertIn("Copyright (c) 2026 BravoDeltaThr33", text)

    def test_pyinstaller_options(self):
        import build_exe
        work = tempfile.mkdtemp(prefix="sd-exe-")
        self.addCleanup(shutil.rmtree, work, True)
        args = build_exe.pyinstaller_args(work, VERSION)
        for flag in ("--onefile", "--windowed", "--icon", "--version-file"):
            self.assertIn(flag, args)
        joined = " ".join(args)
        for want in ("PIL.ImageTk", "PIL._tkinter_finder", "tzdata", "skydrop/fonts"):
            self.assertIn(want, joined)
        with Image.open(os.path.join(work, "skydrop.ico")) as ico:
            self.assertIn((256, 256), ico.info.get("sizes", {(256, 256)}))
        with open(os.path.join(work, "THIRD-PARTY.txt"), encoding="utf-8") as fh:
            self.assertIn("Pillow", fh.read())
        with open(os.path.join(work, "skydrop_exe.py"), encoding="utf-8") as fh:
            self.assertIn("from skydrop.cli import main", fh.read())

    def test_builds_on_windows_only(self):
        if os.name == "nt":
            self.skipTest("this is Windows")
        run = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_exe.py")],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertIn("Windows only", run.stderr)

    def test_checker_reads_pe_and_png(self):
        import check_exe
        folder = tempfile.mkdtemp(prefix="sd-pe-")
        self.addCleanup(shutil.rmtree, folder, True)
        head = bytearray(512)
        head[:2] = b"MZ"
        struct.pack_into("<I", head, 0x3C, 0x80)
        head[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", head, 0x80 + 24 + 68, 2)
        path = os.path.join(folder, "gui.exe")
        with open(path, "wb") as fh:
            fh.write(bytes(head))
        self.assertEqual(check_exe.subsystem(path), 2)
        with open(path, "wb") as fh:
            fh.write(b"not a program")
        self.assertIsNone(check_exe.subsystem(path))
        png = os.path.join(folder, "x.png")
        img = Image.new("RGB", (60, 30), (0, 0, 80))
        img.paste((255, 200, 0), (10, 5, 30, 20))
        img.save(png)
        w, h, colors = check_exe.png_facts(png)
        self.assertEqual((w, h), (60, 30))
        self.assertGreaterEqual(colors, 2)

    def test_ci_builds_and_checks_the_exe(self):
        with open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8") as fh:
            ci = fh.read()
        self.assertIn("windows-app:", ci)
        self.assertIn("--require-hashes", ci)
        self.assertIn("tools/build_exe.py", ci)
        self.assertIn("tools/check_exe.py", ci)
        self.assertIn("release\\skydrop-$env:SKYDROP_VERSION-windows.exe", ci)       # the copy in release/


if __name__ == "__main__":
    unittest.main()
