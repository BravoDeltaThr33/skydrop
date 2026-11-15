"""The CRT font set in kitty itself: skydrop's block in kitty.conf, the checks that the kitty
is skydrop's own, the held-back reloads, and the font sizes."""

import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp(prefix="sd-cache-"))
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sd-config-"))

from skydrop import crt as C  # noqa: E402
from skydrop import termfont as TF  # noqa: E402

USER = """# my kitty
font_family      VT323
font_size        18.75
background_opacity 0.9
"""


@unittest.skipIf(os.name == "nt", "kitty and its font switching are Unix-only")
class TestBlock(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sd-kitty-")
        self.path = os.path.join(self.dir, "kitty.conf")

    def write(self, text, mode=0o600):
        with open(self.path, "w") as fh:
            fh.write(text)
        os.chmod(self.path, mode)

    def read(self, path=None):
        with open(path or self.path) as fh:
            return fh.read()

    def test_block_at_the_end_and_back_out(self):
        self.write(USER)
        self.assertIsNone(TF.write_block(self.path, "Px437 IBM VGA 8x16", 24.0))
        text = self.read()
        self.assertTrue(text.startswith(USER + "\n" + TF.BEGIN), text)
        self.assertTrue(text.endswith("font_family Px437 IBM VGA 8x16\nfont_size 24.0\n" + TF.END + "\n"), text)
        self.assertEqual(self.read(self.path + TF.BACKUP), USER)          # backed up first
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)  # its permissions kept
        # again, another font: still one block, the backup left as it was
        self.assertIsNone(TF.write_block(self.path, "VT323", 18.75))
        text = self.read()
        self.assertEqual(text.count(TF.BEGIN), 1)
        self.assertIn("font_family VT323\nfont_size 18.75\n", text)
        self.assertEqual(self.read(self.path + TF.BACKUP), USER)
        # the terminal's own font and size: the block goes, the file is as it was
        self.assertIsNone(TF.write_block(self.path, None, None))
        self.assertEqual(self.read(), USER)

    def test_block_moves_to_the_end(self):
        self.write(USER)
        TF.write_block(self.path, "VT323", 18.75)
        with open(self.path, "a") as fh:
            fh.write("font_family Fira Code\n")                         # added later, after skydrop's block
        TF.write_block(self.path, "IBM 3270", 13.0)
        text = self.read()
        self.assertLess(text.index("Fira Code"), text.index(TF.BEGIN))   # skydrop's comes last, so it wins
        self.assertEqual(text.count(TF.BEGIN), 1)
        TF.write_block(self.path, None, None)
        self.assertEqual(self.read(), USER + "font_family Fira Code\n")

    def test_size_only_and_nothing(self):
        self.write(USER)
        TF.write_block(self.path, None, 14.5)
        text = self.read()
        self.assertIn("font_size 14.5\n" + TF.END, text)
        self.assertNotIn("font_family Px", text)
        self.assertEqual(TF.block_text(None, None), "")
        self.assertEqual(TF.format_size(12), "12.0")
        self.assertEqual(TF.format_size(18.75), "18.75")
        self.assertEqual(TF.format_size(10.5), "10.5")

    def test_new_file_and_symlink(self):
        TF.write_block(self.path, "VT323", 18.75)                       # no kitty.conf yet: made
        self.assertTrue(self.read().startswith(TF.BEGIN))
        self.assertFalse(os.path.exists(self.path + TF.BACKUP))
        os.unlink(self.path)
        real = os.path.join(self.dir, "dotfiles.conf")
        with open(real, "w") as fh:
            fh.write(USER)
        os.symlink(real, self.path)
        TF.write_block(self.path, "VT323", 18.75)
        self.assertTrue(os.path.islink(self.path))                       # the link stays a link
        self.assertIn(TF.BEGIN, self.read(real))

    def test_unwritable(self):
        if os.geteuid() == 0:
            self.skipTest("root can write anywhere")
        self.write(USER)
        os.chmod(self.dir, 0o500)
        try:
            self.assertIn("kitty.conf", TF.write_block(self.path, "VT323", 18.75))
        finally:
            os.chmod(self.dir, 0o700)

    def test_cut_short_block_and_own_size(self):
        self.write(USER + "\n" + TF.BEGIN + "\nfont_family VT323\nfont_size 30\n")    # its END line lost
        rest, block = TF.split_block(self.read())
        self.assertEqual(rest, USER)
        self.assertEqual(len(block), 3)
        self.assertEqual(TF.own_size(self.path), 18.75)                 # the user's, not skydrop's
        self.write("background_opacity 0.9\n")
        self.assertEqual(TF.own_size(self.path), TF.DEFAULT_SIZE)


class FakeProc:
    """A /proc with kitty (100) -> a shell (200) -> skydrop (300)."""

    def __init__(self, kitty_name="kitty", caught=0x4202, cmdline=b"kitty\0", blocked=None):
        self.root = tempfile.mkdtemp(prefix="sd-proc-")
        for pid, ppid, name, mask, cmd in ((100, 1, kitty_name, caught, cmdline), (200, 100, "zsh", 0, b"zsh\0"),
                                           (300, 200, "python3", 0, b"python3\0"), (400, 1, "kitty", caught, b"x\0")):
            d = os.path.join(self.root, str(pid))
            os.makedirs(d)
            with open(os.path.join(d, "stat"), "w") as fh:
                fh.write(f"{pid} ({name} (x)) S {ppid} {pid} {pid} 0 -1\n")
            with open(os.path.join(d, "status"), "w") as fh:
                fh.write(f"Name:\t{name}\nSigBlk:\t0000000000000000\nSigCgt:\t{mask:016x}\n")
            with open(os.path.join(d, "comm"), "w") as fh:
                fh.write(name + "\n")
            with open(os.path.join(d, "cmdline"), "wb") as fh:
                fh.write(cmd)
            os.symlink("/home/someone", os.path.join(d, "cwd"))
            if pid == 100 and blocked is not None:          # kitty's threads, each with its blocked mask
                for tid, mask in enumerate(blocked, start=100):
                    t = os.path.join(d, "task", str(tid))
                    os.makedirs(t)
                    with open(os.path.join(t, "status"), "w") as fh:
                        fh.write(f"Name:\t{name}\nSigBlk:\t{mask:016x}\nSigCgt:\t{mask:016x}\n")

    def run(self, kitty_pid, **env):
        TF._KNOWN["at"] = -60.0
        with mock.patch.object(TF, "PROC", self.root), mock.patch.object(TF.os, "getpid", return_value=300), \
                mock.patch.dict(os.environ, dict(env, KITTY_PID=str(kitty_pid)) if kitty_pid else env, clear=False):
            if not kitty_pid:
                os.environ.pop("KITTY_PID", None)
            return TF.kitty_process()


@unittest.skipIf(os.name == "nt", "kitty and its font switching are Unix-only")
class TestProcess(unittest.TestCase):
    def test_own_kitty(self):
        self.assertEqual(FakeProc().run(100), (100, None))
        # kitty reads SIGUSR1 from a signalfd: no handler, but blocked in every thread
        usr1 = 1 << (signal.SIGUSR1 - 1)
        self.assertEqual(FakeProc(caught=0x2, blocked=[0x14a03, 0xfffffffe3ffbfaff, 0x14a03]).run(100), (100, None))
        self.assertTrue(0x14a03 & usr1)
        # one thread that doesn't block it would take the default (the end of kitty): not sent
        self.assertIn("update kitty", FakeProc(caught=0x2, blocked=[0x14a03, 0x0]).run(100)[1])

    def test_not_kitty_or_not_ours(self):
        self.assertIn("isn't running in kitty", FakeProc().run(None)[1])
        self.assertIn("ssh or tmux", FakeProc().run(400)[1])            # a kitty, but not skydrop's ancestor
        self.assertIn("ssh or tmux", FakeProc().run(999)[1])            # no such process
        self.assertIsNone(FakeProc(kitty_name="bash").run(100)[0])      # KITTY_PID left over in another terminal
        no_usr1 = 0x4202 & ~(1 << (signal.SIGUSR1 - 1))
        self.assertIn("update kitty", FakeProc(caught=no_usr1).run(100)[1])

    def test_config_path(self):
        fake = FakeProc(cmdline=b"kitty\0--single-instance\0-c\0~/a.conf\0--config=b.conf\0")
        with mock.patch.object(TF, "PROC", fake.root):
            self.assertEqual(TF.kitty_conf_path(100), "/home/someone/b.conf")
        fake = FakeProc(cmdline=b"kitty\0--config\0NONE\0")
        with mock.patch.object(TF, "PROC", fake.root), \
                mock.patch.dict(os.environ, {"KITTY_CONFIG_DIRECTORY": "/cfg/kitty"}):
            self.assertEqual(TF.kitty_conf_path(100), "/cfg/kitty/kitty.conf")


@unittest.skipIf(os.name == "nt", "kitty and its font switching are Unix-only")
class TestReloads(unittest.TestCase):
    def test_held_back_then_sent(self):
        k = TF.KittyFont()
        path = os.path.join(tempfile.mkdtemp(prefix="sd-kitty-"), "kitty.conf")
        sent = []
        with mock.patch.object(TF, "kitty_process", return_value=(4242, None)), \
                mock.patch.object(TF, "kitty_conf_path", return_value=path), \
                mock.patch.object(TF.os, "kill", side_effect=lambda pid, sig: sent.append((pid, sig))):
            self.assertTrue(k.apply("VT323", 18.75, now=100.0)[0])
            self.assertEqual(sent, [(4242, signal.SIGUSR1)])             # the first goes at once
            k.apply("VT323", 19.0, now=100.05)
            k.apply("VT323", 19.5, now=100.10)
            self.assertEqual(len(sent), 1)                               # a quick run is held back...
            self.assertAlmostEqual(k.pump(100.2), 0.05)
            self.assertIsNone(k.pump(100.26))
            self.assertEqual(len(sent), 2)                               # ...and sent once, after it
            self.assertIn("font_size 19.5", Path(path).read_text(encoding="utf-8"))           # with the last size
            self.assertIsNone(k.pump(101.0))
        with mock.patch.object(TF, "kitty_process", return_value=(None, "why")):
            self.assertEqual(k.apply("VT323", 12.0), (False, "why"))


@unittest.skipIf(os.name == "nt", "kitty and its font switching are Unix-only")
class TestSizes(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(C.font_size_of({"crt_font": "vt323"}), 18.75)
        self.assertEqual(C.font_size_of({"crt_font": "px437"}), 12.0)
        self.assertIsNone(C.font_size_of({"crt_font": "own"}))
        cfg = {"crt_font": "vt323", "crt_font_sizes": {"vt323": 20, "own": None, "px437": "bad"}}
        self.assertEqual(C.font_size_of(cfg), 20.0)
        self.assertEqual(C.font_size_of(cfg, "px437"), 12.0)
        self.assertEqual(C.font_size_of({"crt_font_sizes": {"vt323": 900}}, "vt323"), C.SIZE_RANGE[1])
        self.assertEqual(C.font_sizes_saved(cfg, "glasstty", 15.0)["glasstty"], 15.0)

    def test_steps_stop_at_sharp_sizes(self):
        self.assertEqual(C.step_size("vt323", 18.5, 1), 18.75)
        self.assertEqual(C.step_size("vt323", 18.75, 1), 19.0)
        self.assertEqual(C.step_size("vt323", 19.0, -1), 18.75)
        self.assertEqual(C.step_size("terminus", 10.0, 1), 10.5)
        self.assertEqual(C.step_size("own", None, 1, start=11.0), 11.5)    # from the terminal's own size
        self.assertEqual(C.step_size("own", None, -1, start=11.0), 10.5)
        self.assertEqual(C.step_size("px437", C.SIZE_RANGE[1], 1), C.SIZE_RANGE[1])
        self.assertEqual(C.step_size("px437", C.SIZE_RANGE[0], -1), C.SIZE_RANGE[0])
        self.assertTrue(C.size_sharp("px437", 24.0))
        self.assertFalse(C.size_sharp("px437", 13.0))
        self.assertFalse(C.size_sharp("ibm3270", 13.0))
        self.assertEqual(C.size_label(18.75), "18.75 pt")
        self.assertEqual(C.size_label(12.0), "12 pt")
        self.assertEqual(C.size_label(None), "the terminal's own")

    def test_lines_to_copy(self):
        with mock.patch.object(C, "font_installed", return_value="VT323"):
            self.assertEqual(C.font_lines("vt323", 18.75, "kitty"), ["font_family VT323", "font_size 18.75"])
            self.assertEqual(C.font_lines("vt323", 18.75, "ghostty"), ['font-family = "VT323"', "font-size = 18.75"])
            self.assertEqual(C.font_lines("vt323", 18.75, None), ["VT323"])
        self.assertEqual(C.font_lines("own", None, "kitty"), [])
        self.assertEqual(C.font_lines("own", 13.0, "kitty"), ["font_size 13"])


if __name__ == "__main__":
    unittest.main()
