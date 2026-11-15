"""3.14: macOS and Windows. The Windows console terminal (driven here through a stand-in
for kernel32, so it runs anywhere), the voices and players each system has, notifications,
the login item for the background watch, and where the files go on Windows."""

import ctypes
import importlib
import os
import plistlib
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp(prefix="sd-cache-"))
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sd-config-"))

from skydrop import VERSION  # noqa: E402
from skydrop import osdeps  # noqa: E402
from skydrop import term as TM  # noqa: E402


# ---------------------------------------------------------------------------- a stand-in kernel32
class FakeKernel32:
    """Enough of kernel32 for WinTerminal: a queue of console input records, an event,
    console modes and code pages."""
    HIN, HOUT, EVENT = 101, 102, 103

    def __init__(self, cols=120, rows=40):
        self.records = []          # (kind, value): ("key", "x") or ("size", (c, r)) or ("up", "x")
        self.event_set = False
        self.modes = {self.HIN: 0x01F7, self.HOUT: 0x0003}
        self.cp = [437, 437]
        self.set_modes = []

    # handles
    def GetStdHandle(self, which):
        return self.HIN if which == (TM.STD_INPUT_HANDLE & 0xFFFFFFFF) else self.HOUT

    def CreateEventW(self, *a):
        return self.EVENT

    def SetEvent(self, h):
        self.event_set = True
        return 1

    def CloseHandle(self, h):
        return 1

    # modes
    def GetConsoleMode(self, h, ref):
        ref._obj.value = self.modes[h]
        return 1

    def SetConsoleMode(self, h, mode):
        self.modes[h] = mode
        self.set_modes.append((h, mode))
        return 1

    def GetConsoleCP(self):
        return self.cp[0]

    def GetConsoleOutputCP(self):
        return self.cp[1]

    def SetConsoleCP(self, cp):
        self.cp[0] = cp
        return 1

    def SetConsoleOutputCP(self, cp):
        self.cp[1] = cp
        return 1

    # input
    def WaitForMultipleObjects(self, count, handles, wait_all, ms):
        if self.records:
            return TM.WAIT_OBJECT_0
        if self.event_set and count > 1:
            self.event_set = False
            return TM.WAIT_OBJECT_0 + 1
        return TM.WAIT_TIMEOUT

    def GetNumberOfConsoleInputEvents(self, h, ref):
        ref._obj.value = len(self.records)
        return 1

    def ReadConsoleInputW(self, h, records, n, ref):
        take, self.records = self.records[:n], self.records[n:]
        for i, (kind, value) in enumerate(take):
            rec = records[i]
            if kind in ("key", "up"):
                rec.EventType = TM.KEY_EVENT
                rec.Event.KeyEvent.bKeyDown = 1 if kind == "key" else 0
                rec.Event.KeyEvent.wRepeatCount = 1
                rec.Event.KeyEvent.uChar.UnicodeChar = value
            else:
                rec.EventType = TM.WINDOW_BUFFER_SIZE_EVENT
                rec.Event.WindowBufferSizeEvent.dwSize.X, rec.Event.WindowBufferSizeEvent.dwSize.Y = value
        ref._obj.value = len(take)
        return 1

    def type(self, text):
        self.records += [("key", ch) for ch in text]


class FakeStream:
    def __init__(self):
        self.data = b""

    def write(self, b):
        self.data += bytes(b)

    def flush(self):
        pass


def win_terminal(cols=120, rows=40):
    k, out = FakeKernel32(), FakeStream()
    t = TM.WinTerminal(kernel32=k, stream=out)
    t.size = lambda: (cols, rows, 0, 0)
    return t, k, out


class TestWinTerminal(unittest.TestCase):
    def test_keys_arrows_and_mouse(self):
        t, k, _ = win_terminal()
        t.read_events(0)                      # the first pass reports the size (KEY_RESIZE)
        k.type("q\x1b[A\x1b[<0;12;5M\r\x7f\x03")
        ev = t.read_events(0.1)
        self.assertEqual(ev[0], "q")
        self.assertEqual(ev[1], TM.KEY_UP)
        self.assertIsInstance(ev[2], TM.Mouse)
        self.assertEqual((ev[2].kind, ev[2].x, ev[2].y), ("press", 11, 4))
        self.assertEqual(ev[3:], [TM.KEY_ENTER, TM.KEY_BACKSPACE, TM.CTRL_C])

    def test_key_ups_are_ignored(self):
        t, k, _ = win_terminal()
        t.read_events(0)
        k.records = [("key", "a"), ("up", "a"), ("key", "b"), ("up", "b")]
        self.assertEqual(t.read_events(0.1), ["a", "b"])

    def test_lone_escape_and_paste(self):
        t, k, _ = win_terminal()
        t.read_events(0)
        k.type("\x1b")
        self.assertEqual(t.read_events(0.2), [TM.KEY_ESC])
        k.type("\x1b[200~75201\x1b[201~")
        ev = t.read_events(0.2)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0].text, "75201")

    def test_text_beyond_the_basic_plane(self):
        t, k, _ = win_terminal()
        t.read_events(0)
        con = t._con
        # a surrogate pair split across two reads comes out whole
        self.assertEqual(con._text([0x48, 0xD83C]), "H")
        self.assertEqual(con._text([0xDF27, 0x21]), "\U0001F327!")
        self.assertEqual(con._text([0xDC00]), "\ufffd")

    def test_resize_event_and_polling(self):
        t, k, _ = win_terminal()
        t.read_events(0)
        k.records = [("size", (100, 30))]
        self.assertIn(TM.KEY_RESIZE, t.read_events(0.05))
        self.assertEqual(t.read_events(0), [])
        t.size = lambda: (90, 30, 0, 0)       # a resize no event told of
        self.assertIn(TM.KEY_RESIZE, t.read_events(0))

    def test_wake_returns_early(self):
        t, k, _ = win_terminal()
        t.read_events(0)
        t.wake()
        self.assertEqual(t.read_events(5.0), [])
        self.assertFalse(k.event_set)

    def test_modes_on_and_back(self):
        t, k, _ = win_terminal()
        t.raw()
        self.assertTrue(k.modes[k.HIN] & TM.ENABLE_VIRTUAL_TERMINAL_INPUT)
        self.assertFalse(k.modes[k.HIN] & (TM.ENABLE_LINE_INPUT | TM.ENABLE_ECHO_INPUT | TM.ENABLE_PROCESSED_INPUT))
        self.assertFalse(k.modes[k.HIN] & TM.ENABLE_QUICK_EDIT_MODE)
        self.assertTrue(k.modes[k.HOUT] & TM.ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        self.assertEqual(k.cp, [TM.CP_UTF8, TM.CP_UTF8])
        t.cooked()
        self.assertEqual((k.modes[k.HIN], k.modes[k.HOUT], k.cp), (0x01F7, 0x0003, [437, 437]))

    def test_output_is_utf8(self):
        t, k, out = win_terminal()
        t.write("\x1b[H·61°")
        t.flush()
        self.assertEqual(out.data, "\x1b[H·61°".encode("utf-8"))

    def test_probe_finds_sixel_and_the_cell(self):
        t, k, out = win_terminal()
        k.type("\x1b[6;20;10t\x1b[4;800;1200t\x1b[?61;4;6;7;14;21;22;23;24;28;32;42c")
        caps = t.probe(timeout=0.3)
        self.assertIn(b"\x1b[16t", out.data)
        self.assertTrue(caps.sixel)
        self.assertFalse(caps.kitty)
        self.assertEqual((caps.cell_w, caps.cell_h), (10, 20))
        self.assertEqual(caps.graphics_auto(), "sixel")
        self.assertEqual(t.buf, b"")

    def test_probe_without_answers(self):
        t, k, _ = win_terminal()
        caps = t.probe(timeout=0.1)
        self.assertFalse(caps.sixel or caps.kitty)
        self.assertEqual(t.cell_px(), (10, 20))

    def test_suspend_is_harmless(self):
        t, _, _ = win_terminal()
        t.suspend()
        t.install_signals()

    def test_structures(self):
        # the record layout Windows uses: a 2-byte event type, then the 16-byte union at offset 4
        self.assertEqual(TM.INPUT_RECORD.Event.offset, 4)
        if ctypes.sizeof(ctypes.c_wchar) == 2:
            self.assertEqual(ctypes.sizeof(TM.INPUT_RECORD), 20)


class TestOpenTerminal(unittest.TestCase):
    def test_picks_by_system(self):
        with mock.patch.object(TM.os, "name", "nt"), mock.patch.object(TM, "WinTerminal", lambda: "win"):
            self.assertEqual(TM.open_terminal(), "win")


# ---------------------------------------------------------------------------- voices and players
def reload_for(platform):
    """skydrop.osdeps and skydrop.speech as they load on another system."""
    with mock.patch.object(sys, "platform", platform):
        importlib.reload(osdeps)
        from skydrop import speech
        importlib.reload(speech)
    return speech


class TestSpeechBySystem(unittest.TestCase):
    def tearDown(self):
        reload_for(sys.platform)

    def test_macos(self):
        sp = reload_for("darwin")
        self.assertEqual(sp.ENGINES, ("piper", "say", "espeak"))
        self.assertEqual(sp.PLAYERS, ("afplay", "ffplay"))
        self.assertEqual(sp.player_cmd("afplay", "/tmp/a.wav"), ["afplay", "/tmp/a.wav"])
        self.assertIn("say", sp.ENGINE_CHOICES)
        self.assertIn("macOS", sp.engines_help())

    def test_windows(self):
        sp = reload_for("win32")
        self.assertEqual(sp.ENGINES, ("piper", "sapi", "espeak"))
        self.assertEqual(sp.PLAYERS, ("winsound", "ffplay"))
        self.assertEqual(sp.find_player("auto"), "winsound")           # always there on Windows
        cmd = sp.player_cmd("winsound", r"C:\x\a.wav")
        self.assertEqual(cmd[0], sys.executable)
        self.assertIn("winsound.PlaySound", cmd[2])
        self.assertEqual(cmd[3], r"C:\x\a.wav")
        self.assertEqual(sp.ENGINE_NAMES["sapi"], "Windows voice")

    def test_linux(self):
        sp = reload_for("linux")
        self.assertEqual(sp.PLAYERS, ("pw-play", "paplay", "aplay", "ffplay"))
        self.assertEqual(sp.ENGINE_CHOICES[0], "auto")
        self.assertEqual(sp.ENGINE_CHOICES[-1], "off")

    def test_sapi_script_takes_its_input_from_the_environment(self):
        sp = reload_for("win32")
        for var in ("SKYDROP_TEXT", "SKYDROP_WAV", "SKYDROP_RATE"):
            self.assertIn("$env:" + var, sp._SAPI)
        argv = osdeps.powershell_command(sp._SAPI)
        self.assertIn("-EncodedCommand", argv)
        import base64
        self.assertEqual(base64.b64decode(argv[-1]).decode("utf-16-le"), sp._SAPI)


# ---------------------------------------------------------------------------- notifications, login item
class TestNotifications(unittest.TestCase):
    def test_macos_osascript(self):
        with mock.patch.object(osdeps, "WINDOWS", False), mock.patch.object(osdeps, "MACOS", True), \
                mock.patch.object(osdeps.shutil, "which", lambda n: "/usr/bin/" + n):
            argv, env = osdeps.notification_command("skydrop", "TORNADO WARNING", "Dallas County")
        self.assertEqual(argv[0], "/usr/bin/osascript")
        self.assertEqual(argv[-2:], ["TORNADO WARNING", "Dallas County"])   # passed as data, never as script

    def test_windows_toast(self):
        with mock.patch.object(osdeps, "WINDOWS", True), mock.patch.object(osdeps, "MACOS", False), \
                mock.patch.object(osdeps.shutil, "which", lambda n: None):
            argv, env = osdeps.notification_command("skydrop", "T<'&>", "b")
        self.assertIn("-EncodedCommand", argv)
        self.assertEqual(env, {"SKYDROP_TITLE": "T<'&>", "SKYDROP_BODY": "b"})

    def test_linux_notify_send(self):
        with mock.patch.object(osdeps, "WINDOWS", False), mock.patch.object(osdeps, "MACOS", False), \
                mock.patch.object(osdeps.shutil, "which", lambda n: "/usr/bin/" + n):
            argv, env = osdeps.notification_command("skydrop", "t", "b", critical=False)
        self.assertEqual(argv[:2], ["/usr/bin/notify-send", "-a"])
        self.assertIn("normal", argv)


class TestLoginItem(unittest.TestCase):
    def test_macos_launch_agent(self):
        home = tempfile.mkdtemp()
        with mock.patch.object(osdeps, "WINDOWS", False), mock.patch.object(osdeps, "MACOS", True), \
                mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}):
            err = osdeps.set_login_item(True, ["/x/python", "/x/run.py", "--watch"])
            path = osdeps.autostart_place()
            self.assertEqual(err, "")
            with open(path, "rb") as fh:
                plist = plistlib.load(fh)
            self.assertEqual(plist["ProgramArguments"], ["/x/python", "/x/run.py", "--watch"])
            self.assertTrue(plist["RunAtLoad"])
            self.assertEqual(osdeps.set_login_item(False, []), "")
            self.assertFalse(os.path.exists(path))

    def test_linux_desktop_entry(self):
        home = tempfile.mkdtemp()
        with mock.patch.object(osdeps, "WINDOWS", False), mock.patch.object(osdeps, "MACOS", False), \
                mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home,
                                             "XDG_CONFIG_HOME": os.path.join(home, ".config")}):
            self.assertEqual(osdeps.set_login_item(True, ["/x/py thon", "--watch"], "skydrop", VERSION), "")
            with open(osdeps.autostart_place("skydrop")) as fh:
                text = fh.read()
        self.assertIn('Exec="/x/py thon" --watch', text)

    def test_windows_run_key(self):
        store = {}

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        fake = types.SimpleNamespace(
            HKEY_CURRENT_USER="HKCU", REG_SZ=1, CreateKey=lambda root, path: Key(),
            SetValueEx=lambda key, name, _r, _t, value: store.__setitem__(name, value),
            DeleteValue=lambda key, name: store.pop(name))
        with mock.patch.object(osdeps, "WINDOWS", True), mock.patch.dict(sys.modules, {"winreg": fake}):
            self.assertEqual(osdeps.set_login_item(True, [r"C:\py\pythonw.exe", r"C:\a b\run.py", "--watch"]), "")
            self.assertEqual(store[osdeps.RUN_VALUE], r'C:\py\pythonw.exe "C:\a b\run.py" --watch')
            self.assertEqual(osdeps.set_login_item(False, []), "")
            self.assertNotIn(osdeps.RUN_VALUE, store)

    def test_windowless_python(self):
        d = tempfile.mkdtemp()
        for name in ("python.exe", "pythonw.exe"):
            open(os.path.join(d, name), "w").close()
        with mock.patch.object(osdeps, "WINDOWS", True):
            self.assertEqual(osdeps.windowless_python(os.path.join(d, "python.exe")), os.path.join(d, "pythonw.exe"))
        with mock.patch.object(osdeps, "WINDOWS", False):
            self.assertEqual(osdeps.windowless_python(os.path.join(d, "python.exe")), os.path.join(d, "python.exe"))

    def test_background_flags(self):
        with mock.patch.object(osdeps, "WINDOWS", True):
            self.assertEqual(osdeps.background(), {"creationflags": osdeps.CREATE_NO_WINDOW})
            self.assertTrue(osdeps.background(detach=True)["creationflags"] & osdeps.DETACHED_PROCESS)
        with mock.patch.object(osdeps, "WINDOWS", False):
            self.assertEqual(osdeps.background(), {"start_new_session": True})


@unittest.skipIf(os.name == "nt", "checks the Unix side")
class TestProcesses(unittest.TestCase):
    def test_alive_and_names(self):
        self.assertTrue(osdeps.alive(os.getpid()))
        self.assertFalse(osdeps.alive(0))
        self.assertFalse(osdeps.alive("x"))
        self.assertEqual(osdeps.parent_pid(os.getpid()), os.getppid())
        self.assertIn("python", (osdeps.command_line(os.getpid()) or "").lower())
        self.assertTrue(osdeps.process_name(os.getpid()))


class TestWatchStop(unittest.TestCase):
    def test_windows_asks_instead_of_ending(self):
        from skydrop import watch
        with mock.patch.object(osdeps, "WINDOWS", True), \
                mock.patch.object(watch, "watcher_pid", side_effect=[1234, None]), \
                mock.patch.object(osdeps, "terminate") as end:
            self.assertEqual(watch.stop_watcher(), 1234)
            end.assert_not_called()
            self.assertTrue(watch._stop_asked())
            self.assertFalse(watch._stop_asked())

    def test_a_fresh_pid_file_stands_in_for_the_command_line(self):
        import time
        from skydrop import watch
        os.makedirs(os.path.dirname(watch.PID_PATH), exist_ok=True)
        with open(watch.PID_PATH, "w") as fh:
            fh.write(str(os.getpid()))
        try:
            with mock.patch.object(osdeps, "command_line", return_value=None):
                self.assertEqual(watch.watcher_pid(), os.getpid())
                old = time.time() - 10 * watch.BEAT
                os.utime(watch.PID_PATH, (old, old))
                self.assertIsNone(watch.watcher_pid())
        finally:
            os.remove(watch.PID_PATH)


# ---------------------------------------------------------------------------- files on Windows
class TestWindowsPaths(unittest.TestCase):
    def tearDown(self):
        from skydrop import paths
        importlib.reload(paths)

    def test_appdata(self):
        env = {"APPDATA": "/u/AppData/Roaming", "LOCALAPPDATA": "/u/AppData/Local"}
        from skydrop import paths
        with mock.patch.object(os, "name", "nt"), mock.patch.dict(os.environ, env), \
                mock.patch.dict(os.environ, {}, clear=False):
            for var in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
                os.environ.pop(var, None)
            importlib.reload(paths)
            self.assertEqual(paths.CONFIG_PATH, os.path.join("/u/AppData/Roaming", "skydrop", "config.json"))
            self.assertEqual(paths.CACHE_DIR, os.path.join("/u/AppData/Local", "skydrop", "cache"))
            self.assertEqual(paths.VOICES_DIR, os.path.join("/u/AppData/Local", "skydrop", "voices"))


class TestBundledFonts(unittest.TestCase):
    def test_labels_use_the_bundled_dejavu(self):
        from skydrop import gfx
        if not gfx.HAVE_PIL:
            self.skipTest("no Pillow")
        for style in ((True, False), (False, False), (True, True), (False, True)):
            path = gfx._find_font(style)
            self.assertTrue(path and os.path.dirname(path) == gfx.BUNDLED_FONTS, (style, path))


if __name__ == "__main__":
    unittest.main()
