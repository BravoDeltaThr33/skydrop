"""Terminal I/O without curses: raw mode, key and mouse parsing, and probing
the terminal for graphics support (kitty graphics protocol, sixel), color
depth and cell size in pixels.

Terminal works on a Unix tty (Linux, macOS). WinTerminal works on the Windows
console (Windows Terminal, the classic console window): both directions speak the
same escape sequences there once the console's VT modes are on, so everything
past reading and writing is shared. open_terminal() picks the right one.
"""

import codecs
import locale
import os
import re
import select
import signal
import struct
import sys
import time

try:
    import fcntl
    import termios
except ImportError:          # Windows
    fcntl = termios = None

KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT = "KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT"
KEY_SLEFT, KEY_SRIGHT = "KEY_SLEFT", "KEY_SRIGHT"      # with Shift (or Ctrl, Alt) held
KEY_HOME, KEY_END, KEY_PPAGE, KEY_NPAGE = "KEY_HOME", "KEY_END", "KEY_PPAGE", "KEY_NPAGE"
KEY_DC, KEY_IC, KEY_BACKSPACE, KEY_BTAB = "KEY_DC", "KEY_IC", "KEY_BACKSPACE", "KEY_BTAB"
KEY_ENTER, KEY_ESC, KEY_RESIZE = "\n", "\x1b", "KEY_RESIZE"
KEY_F = {n: f"KEY_F{n}" for n in range(1, 13)}
CTRL_C, CTRL_Z, CTRL_L = "\x03", "\x1a", "\x0c"

ESC_DELAY = 0.03      # a lone Esc key
SEQ_DELAY = 0.5       # an escape sequence that arrives in pieces (ssh, tmux)
PASTE_TIMEOUT = 3.0   # give up on a paste whose end marker never comes

_TILDE = {1: KEY_HOME, 2: KEY_IC, 3: KEY_DC, 4: KEY_END, 5: KEY_PPAGE, 6: KEY_NPAGE, 7: KEY_HOME,
          8: KEY_END, 11: KEY_F[1], 12: KEY_F[2], 13: KEY_F[3], 14: KEY_F[4], 15: KEY_F[5],
          17: KEY_F[6], 18: KEY_F[7], 19: KEY_F[8], 20: KEY_F[9], 21: KEY_F[10], 23: KEY_F[11],
          24: KEY_F[12]}
_CSI_LETTER = {"A": KEY_UP, "B": KEY_DOWN, "C": KEY_RIGHT, "D": KEY_LEFT, "H": KEY_HOME,
               "F": KEY_END, "Z": KEY_BTAB, "P": KEY_F[1], "Q": KEY_F[2], "S": KEY_F[4]}
_SS3 = {"A": KEY_UP, "B": KEY_DOWN, "C": KEY_RIGHT, "D": KEY_LEFT, "H": KEY_HOME, "F": KEY_END,
        "P": KEY_F[1], "Q": KEY_F[2], "R": KEY_F[3], "S": KEY_F[4], "M": KEY_ENTER}


class Mouse:
    """A mouse event: kind is press, release, drag, wheel_up or wheel_down."""
    __slots__ = ("kind", "button", "x", "y")

    def __init__(self, kind, button, x, y):
        self.kind, self.button, self.x, self.y = kind, button, x, y

    def __repr__(self):
        return f"Mouse({self.kind},{self.button},{self.x},{self.y})"


class Paste:
    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text


class Caps:
    """What the terminal can do."""

    def __init__(self):
        self.name = ""            # e.g. "kitty 0.35.2", "Tilix (VTE 7600)"
        self.kitty = False        # kitty graphics protocol answered OK
        self.sixel = False        # DA1 advertises sixel
        self.truecolor = False
        self.colors = 16
        self.utf8 = True
        self.cell_w = 0
        self.cell_h = 0
        self.tmux = False
        self.da1 = ""
        self.xtversion = ""
        self.bg_rgb = None        # the terminal's own background, when it says (OSC 11)
        self.probed = False

    def graphics_auto(self):
        if self.tmux:
            return "blocks" if self.utf8 else "ascii"
        if self.kitty:
            return "kitty"
        if self.sixel:
            return "sixel"
        return "blocks" if self.utf8 else "ascii"

    def color_auto(self):
        if self.truecolor:
            return "truecolor"
        return "256" if self.colors >= 256 else "16"

    def describe(self):
        bits = []
        if self.kitty:
            bits.append("kitty graphics")
        if self.sixel:
            bits.append("sixel")
        if not bits:
            bits.append("no pixel graphics")
        bits.append("24-bit color" if self.truecolor else f"{self.colors} colors")
        if self.cell_w and self.cell_h:
            bits.append(f"cell {self.cell_w}x{self.cell_h} px")
        return ", ".join(bits)


def _macos_major():
    """macOS's major version (26 for Tahoe), or 0 elsewhere."""
    if sys.platform != "darwin":
        return 0
    try:
        import platform
        return int((platform.mac_ver()[0] or "0").split(".")[0])
    except (ValueError, OSError):
        return 0


def _env_caps(caps):
    env = os.environ
    term = env.get("TERM", "")
    prog = env.get("TERM_PROGRAM", "")
    caps.tmux = bool(env.get("TMUX")) or term.startswith("screen") or term.startswith("tmux")
    vte = env.get("VTE_VERSION", "")
    ct = env.get("COLORTERM", "").lower()
    caps.truecolor = (ct in ("truecolor", "24bit") or term.endswith("-direct") or "kitty" in term
                      or "ghostty" in term or prog in ("ghostty", "WezTerm", "iTerm.app", "vscode")
                      or bool(env.get("KITTY_WINDOW_ID")) or bool(env.get("KONSOLE_VERSION"))
                      or bool(env.get("WT_SESSION")) or (vte.isdigit() and int(vte) >= 3600)
                      or os.name == "nt"          # the Windows 10+ console draws 24-bit color
                      or (prog == "Apple_Terminal" and _macos_major() >= 26))   # Terminal on macOS 26+
    if caps.truecolor or "256" in term or "kitty" in term or "ghostty" in term:
        caps.colors = 256
    elif term in ("linux", "vt100", "vt220", "dumb"):
        caps.colors = 8
    else:
        caps.colors = 16 if term else 8
    enc = (getattr(sys.stdout, "encoding", "") or locale.getpreferredencoding(False) or "").lower()
    caps.utf8 = "utf" in enc and term not in ("linux", "vt100", "dumb")
    names = []
    if env.get("KITTY_WINDOW_ID") or "kitty" in term:
        names.append("kitty")
    if prog == "ghostty" or "ghostty" in term or env.get("GHOSTTY_RESOURCES_DIR"):
        names.append("Ghostty")
    if env.get("TILIX_ID"):
        names.append("Tilix")
    if env.get("KONSOLE_VERSION"):
        names.append("Konsole")
    if prog == "WezTerm":
        names.append("WezTerm")
    if prog == "iTerm.app":
        names.append("iTerm2")
    if env.get("WT_SESSION"):
        names.append("Windows Terminal")
    elif os.name == "nt" and not names:
        names.append("Windows console")
    if vte and not names:
        names.append("VTE terminal")
    if vte:
        names.append(f"VTE {vte}")
    if not names and prog:
        names.append(prog)
    caps.name = " / ".join(names) or (term or "unknown terminal")
    return caps


class Terminal:
    def __init__(self):
        self._saved = None
        self.buf = b""
        self.out = []
        self.esc_at = 0.0
        self.paste = None
        self.paste_at = 0.0
        self.responses = []       # replies to queries that arrived mid-session
        self.resized = True
        self.resumed = False      # set after fg; the app then redraws every picture
        self.suspended = False
        self.active = False
        self.mouse = False
        self.motion = False
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.caps = _env_caps(Caps())
        self._init_io()

    def _init_io(self):
        self.fd_in = sys.stdin.fileno()
        self.fd_out = sys.stdout.fileno()
        self._wake_r, self._wake_w = os.pipe()
        for fd in (self._wake_r, self._wake_w):
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

    # -- modes -----------------------------------------------------------------
    def raw(self):
        if self._saved is None:
            self._saved = termios.tcgetattr(self.fd_in)
        mode = termios.tcgetattr(self.fd_in)
        mode[0] &= ~(termios.BRKINT | termios.ICRNL | termios.INPCK | termios.ISTRIP | termios.IXON)
        mode[2] &= ~(termios.CSIZE | termios.PARENB)
        mode[2] |= termios.CS8
        mode[3] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
        mode[6][termios.VMIN] = 1
        mode[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd_in, termios.TCSAFLUSH, mode)

    def cooked(self):
        if self._saved is not None:
            try:
                termios.tcsetattr(self.fd_in, termios.TCSAFLUSH, self._saved)
            except termios.error:
                pass

    def enter(self, mouse=True):
        self.raw()
        self.mouse = mouse
        seq = "\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[?2004h\x1b[H\x1b[2J"
        if mouse:
            seq += "\x1b[?1000h\x1b[?1006h"
            if getattr(self, "motion", False):
                seq += "\x1b[?1003h"
        self.write(seq)
        self.flush()
        self.active = True
        self.resized = True

    def leave(self):
        if not self.active:
            self.cooked()
            return
        seq = ""
        if self.caps.kitty:
            seq += "\x1b_Ga=d,d=A,q=2\x1b\\"
        seq += "\x1b[?1003l\x1b[?1006l\x1b[?1000l\x1b[?2004l\x1b[0m\x1b[?7h\x1b[?25h\x1b[2J\x1b[?1049l"
        self.out = [seq]
        try:
            self.flush()
        except OSError:
            pass
        self.cooked()
        self.active = False

    def set_mouse(self, on):
        if on == self.mouse:
            return
        self.mouse = on
        if not on and getattr(self, "motion", False):
            self.write("\x1b[?1003l")
            self.motion = False
        self.write("\x1b[?1000h\x1b[?1006h" if on else "\x1b[?1006l\x1b[?1000l")

    def set_motion(self, on):
        """Report mouse movement too (used on the radar map for hover readouts and dragging)."""
        on = bool(on) and bool(self.mouse)
        if on == getattr(self, "motion", False):
            return
        self.motion = on
        self.write("\x1b[?1003h" if on else "\x1b[?1003l")

    _eof_on_empty = True      # an empty read from a tty means it is gone

    def install_signals(self):
        def on_winch(_sig, _frm):
            self.resized = True
            self.wake()

        def on_cont(_sig, _frm):
            if self.suspended:
                self.suspended = False
                self.enter(self.mouse)
                self.resumed = True
                self.wake()

        def on_tstp(_sig, _frm):  # `kill -TSTP` or a job-control stop from outside
            if self.active and not self.suspended:
                self.suspend()

        def on_term(sig, _frm):   # closing the window or `kill`: unwind so the terminal is restored
            raise SystemExit(128 + sig)

        signal.signal(signal.SIGWINCH, on_winch)
        signal.signal(signal.SIGCONT, on_cont)
        signal.signal(signal.SIGTSTP, on_tstp)
        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGHUP, on_term)

    def suspend(self):
        """Ctrl-Z: hand the terminal back to the shell, stop, resume on fg."""
        mouse = self.mouse
        self.leave()
        self.mouse = mouse
        self.suspended = True
        os.kill(os.getpid(), signal.SIGSTOP)

    def wake(self):
        try:
            os.write(self._wake_w, b"x")
        except OSError:
            pass

    # -- size -------------------------------------------------------------------
    def size(self):
        """(cols, rows, xpixels, ypixels)."""
        for fd in (self.fd_out, self.fd_in):
            try:
                raw = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)
                rows, cols, xp, yp = struct.unpack("HHHH", raw)
                if cols and rows:
                    return cols, rows, xp, yp
            except OSError:
                continue
        try:
            sz = os.get_terminal_size()
            return sz.columns, sz.lines, 0, 0
        except OSError:
            return 80, 24, 0, 0

    def cell_px(self):
        cols, rows, xp, yp = self.size()
        if xp and yp and cols and rows:
            w, h = xp // cols, yp // rows
            if 4 <= w <= 64 and 8 <= h <= 128:
                return w, h
        if self.caps.cell_w and self.caps.cell_h:
            return self.caps.cell_w, self.caps.cell_h
        return 10, 20

    # -- output -----------------------------------------------------------------
    def write(self, s):
        self.out.append(s)

    def flush(self):
        if not self.out:
            return
        data = "".join(self.out).encode("utf-8", "replace")
        self.out = []
        self._send(data)

    def _send(self, data):
        """Write every byte of `data` to the terminal."""
        view = memoryview(data)
        while view:
            try:
                n = os.write(self.fd_out, view)
            except BlockingIOError:
                select.select([], [self.fd_out], [], 0.5)
                continue
            except InterruptedError:
                continue
            view = view[n:]

    def _poll(self, wait):
        """Wait up to `wait` seconds for input or a wake(): (the bytes read, whether input
        was ready, whether wake() was called)."""
        wake_r = getattr(self, "_wake_r", None)
        try:
            ready, _, _ = select.select([self.fd_in] + ([wake_r] if wake_r is not None else []), [], [], wait)
        except (InterruptedError, OSError):
            ready = []
        woke = wake_r is not None and wake_r in ready
        if woke:
            try:
                os.read(wake_r, 4096)
            except OSError:
                pass
        chunk = b""
        if self.fd_in in ready:
            try:
                chunk = os.read(self.fd_in, 65536)
            except OSError:
                chunk = b""
        return chunk, self.fd_in in ready, woke

    # -- input ------------------------------------------------------------------
    def read_events(self, timeout):
        """Wait up to `timeout` seconds; return a list of keys/events."""
        events = []
        deadline = time.time() + max(0.0, timeout)
        while True:
            wait = max(0.0, deadline - time.time())
            if self.buf.startswith(b"\x1b") and len(self.buf) < 64:
                wait = min(wait, ESC_DELAY)
            if self.paste is not None:
                wait = min(wait, 0.25)
                if time.time() - self.paste_at > PASTE_TIMEOUT:  # the end marker never came
                    events.append(Paste(self.paste + self._decoder.decode(b"", final=True)))
                    self._decoder.reset()
                    self.paste = None
            chunk, came, woke = self._poll(wait)
            if chunk:
                if not self.buf:
                    self.esc_at = time.time()
                if self.paste is not None:
                    self.paste_at = time.time()
                self.buf += chunk
            events += self._parse(final=not came)
            self._check_size()
            if self.resized:
                events.append(KEY_RESIZE)
                self.resized = False
            if events or time.time() >= deadline or woke:
                return events

    def _check_size(self):
        """Where no signal says so, notice a resize by looking (Windows)."""

    def _parse(self, final=False):
        out = []
        b = self.buf
        i, n = 0, len(b)
        while i < n:
            if self.paste is not None:
                end = b.find(b"\x1b[201~", i)
                if end < 0:
                    # keep a tail that may be the start of a split end marker
                    k = b.find(b"\x1b", max(i, n - 5))
                    cut = k if k >= 0 else n
                    self.paste += self._decoder.decode(b[i:cut])
                    i = cut
                    break
                self.paste += self._decoder.decode(b[i:end], final=True)
                self._decoder.reset()
                out.append(Paste(self.paste))
                self.paste = None
                i = end + 6
                continue
            c = b[i]
            if c == 0x1b:
                if i + 1 >= n:
                    if final:
                        out.append(KEY_ESC)
                        i += 1
                        continue
                    break
                nx = b[i + 1]
                late = time.time() - self.esc_at > SEQ_DELAY
                if nx == 0x5b:  # CSI
                    j = i + 2
                    while j < n and not (0x40 <= b[j] <= 0x7e):
                        j += 1
                    if j >= n:
                        if (final and late) or n - i > 64:  # stale or garbage: drop it
                            i = n
                        break
                    ev = self._csi(b[i + 2:j].decode("latin-1"), chr(b[j]))
                    if ev is not None:
                        out.append(ev)
                    i = j + 1
                    continue
                if nx == 0x4f:  # SS3
                    if i + 2 >= n:
                        if final:
                            out.append(KEY_ESC)
                            i += 2
                            continue
                        break
                    key = _SS3.get(chr(b[i + 2]))
                    if key:
                        out.append(key)
                    i += 3
                    continue
                if nx in (0x5f, 0x50, 0x5d, 0x58, 0x5e):  # APC, DCS, OSC, SOS, PM strings
                    st = b.find(b"\x1b\\", i + 2)
                    bel = b.find(b"\x07", i + 2) if nx == 0x5d else -1
                    ends = [e for e in (st, bel) if e >= 0]
                    if not ends:
                        if n - i > 8192:
                            i = n
                        elif final and late:
                            i += 1  # no string followed: it was Alt+key (Alt+], Alt+P ...)
                            continue
                        break
                    e = min(ends)
                    self.responses.append(b[i:e].decode("latin-1"))
                    i = e + (2 if e == st else 1)
                    continue
                # Alt+key: report the key on its own
                i += 1
                continue
            if c < 0x20 or c == 0x7f:
                if c in (0x0d, 0x0a):
                    out.append(KEY_ENTER)
                elif c == 0x09:
                    out.append("\t")
                elif c in (0x7f, 0x08):
                    out.append(KEY_BACKSPACE)
                else:
                    out.append(chr(c))
                i += 1
                continue
            # UTF-8 text
            if c < 0x80:
                need = 1
            elif c >> 5 == 0x6:
                need = 2
            elif c >> 4 == 0xe:
                need = 3
            elif c >> 3 == 0x1e:
                need = 4
            else:
                i += 1
                continue
            if i + need > n:
                break
            ch = b[i:i + need].decode("utf-8", "replace")
            out.append(ch)
            i += need
        self.buf = b[i:]
        return out

    def _csi(self, params, final):
        if params.startswith("<") and final in "Mm":
            try:
                bt, x, y = (int(v) for v in params[1:].split(";")[:3])
            except ValueError:
                return None
            x, y = x - 1, y - 1
            if bt & 64:
                if bt & 3 >= 2:  # sideways scrolling
                    return None
                return Mouse("wheel_up" if (bt & 3) == 0 else "wheel_down", 0, x, y)
            button = bt & 3
            if bt & 32:
                return Mouse("move" if button == 3 else "drag", button, x, y)
            return Mouse("press" if final == "M" else "release", button, x, y)
        if final == "~":
            first = params.split(";")[0]
            if first == "200":
                self.paste = ""
                self.paste_at = time.time()
                self._decoder.reset()
                return None
            if first.isdigit():
                return _TILDE.get(int(first))
            return None
        if final in "ct" or (final == "R" and params.startswith("?")):
            self.responses.append("\x1b[" + params + final)
            return None
        if final in ("I", "O", "u"):
            return None
        if final == "R" and ";" in params and not params.startswith("1;"):
            return None  # a cursor position report
        if final in "CD" and ";" in params:
            return KEY_SRIGHT if final == "C" else KEY_SLEFT
        return _CSI_LETTER.get(final)

    # -- probing ------------------------------------------------------------------
    def probe(self, timeout=0.6):
        """Ask the terminal what it supports. Call in raw mode, before drawing."""
        caps = self.caps
        caps.probed = True
        queries = ("\x1b_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA\x1b\\" + "\x1b[>q" + "\x1b[16t" + "\x1b[14t" +
                   "\x1b]11;?\x1b\\" + "\x1b[c")
        if caps.tmux:
            queries = "\x1b[c"
        try:
            self._send(queries.encode())
        except OSError:
            return caps
        got = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk, came, woke = self._poll(max(0.0, deadline - time.time()))
            if not came:
                if woke:
                    continue
                break
            if not chunk:
                if self._eof_on_empty:
                    break
                continue
            got += chunk
            if re.search(rb"\x1b\[\?[0-9;]*c", got):
                time.sleep(0.02)
                chunk, _came, _woke = self._poll(0)
                got += chunk
                break
        text = got.decode("latin-1")
        if re.search(r"\x1b_Gi=31;OK", text):
            caps.kitty = True
        m = re.search(r"\x1b\[\?([0-9;]*)c", text)
        if m:
            caps.da1 = m.group(1)
            if "4" in m.group(1).split(";"):
                caps.sixel = True
        m = re.search(r"\x1bP>\|([^\x1b]*)\x1b\\", text)
        if m:
            caps.xtversion = m.group(1).strip()
        m = re.search(r"\x1b\]11;rgba?:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})", text)
        if m:
            caps.bg_rgb = tuple(int(h, 16) * 255 // (16 ** len(h) - 1) for h in m.groups())
        m = re.search(r"\x1b\[6;(\d+);(\d+)t", text)
        if m:
            h, w = int(m.group(1)), int(m.group(2))
            if 4 <= w <= 64 and 8 <= h <= 128:
                caps.cell_w, caps.cell_h = w, h
        if not caps.cell_w:
            m = re.search(r"\x1b\[4;(\d+);(\d+)t", text)
            cols, rows, _xp, _yp = self.size()
            if m and cols and rows:
                h, w = int(m.group(1)) // rows, int(m.group(2)) // cols
                if 4 <= w <= 64 and 8 <= h <= 128:
                    caps.cell_w, caps.cell_h = w, h
        xt = caps.xtversion.lower()
        if xt:
            if "kitty" in xt or "ghostty" in xt or "wezterm" in xt or "foot" in xt:
                caps.truecolor = True
                caps.colors = 256
            name = caps.xtversion.replace("(", " ").replace(")", "")
            caps.name = name if not caps.name or caps.name.startswith("xterm") else f"{name} ({caps.name})"
        if caps.kitty and not caps.truecolor:
            caps.truecolor = True
            caps.colors = 256
        if caps.kitty and caps.sixel and os.environ.get("TERM_PROGRAM") == "iTerm.app":
            caps.kitty = False    # iTerm2's kitty pictures are partial; its sixel is the sure thing
        # leftovers (rare) are kept so they don't show up as keystrokes
        rest = re.sub(rb"\x1b_G[^\x1b]*\x1b\\|\x1bP[^\x1b]*\x1b\\|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|"
                      rb"\x1b\[[0-9;?>]*[ct]", b"", got)
        if rest.strip():
            self.buf += rest
        return caps


# ---------------------------------------------------------------------------- Windows
import ctypes  # noqa: E402

_BOOL, _WORD, _DWORD, _SHORT, _UINT = ctypes.c_int, ctypes.c_ushort, ctypes.c_uint32, ctypes.c_short, ctypes.c_uint
_HANDLE = ctypes.c_void_p


class _COORD(ctypes.Structure):
    _fields_ = [("X", _SHORT), ("Y", _SHORT)]


class _CHAR(ctypes.Union):
    _fields_ = [("UnicodeChar", ctypes.c_wchar), ("AsciiChar", ctypes.c_char)]


class _KEY_EVENT(ctypes.Structure):
    _fields_ = [("bKeyDown", _BOOL), ("wRepeatCount", _WORD), ("wVirtualKeyCode", _WORD),
                ("wVirtualScanCode", _WORD), ("uChar", _CHAR), ("dwControlKeyState", _DWORD)]


class _MOUSE_EVENT(ctypes.Structure):
    _fields_ = [("dwMousePosition", _COORD), ("dwButtonState", _DWORD), ("dwControlKeyState", _DWORD),
                ("dwEventFlags", _DWORD)]


class _SIZE_EVENT(ctypes.Structure):
    _fields_ = [("dwSize", _COORD)]


class _MENU_EVENT(ctypes.Structure):
    _fields_ = [("dwCommandId", _UINT)]


class _FOCUS_EVENT(ctypes.Structure):
    _fields_ = [("bSetFocus", _BOOL)]


class _EVENT(ctypes.Union):
    _fields_ = [("KeyEvent", _KEY_EVENT), ("MouseEvent", _MOUSE_EVENT), ("WindowBufferSizeEvent", _SIZE_EVENT),
                ("MenuEvent", _MENU_EVENT), ("FocusEvent", _FOCUS_EVENT)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", _WORD), ("Event", _EVENT)]


KEY_EVENT, WINDOW_BUFFER_SIZE_EVENT = 0x0001, 0x0004
STD_INPUT_HANDLE, STD_OUTPUT_HANDLE = -10, -11
WAIT_OBJECT_0, WAIT_TIMEOUT = 0, 0x102
# console input modes
ENABLE_PROCESSED_INPUT, ENABLE_LINE_INPUT, ENABLE_ECHO_INPUT = 0x0001, 0x0002, 0x0004
ENABLE_WINDOW_INPUT, ENABLE_MOUSE_INPUT, ENABLE_QUICK_EDIT_MODE = 0x0008, 0x0010, 0x0040
ENABLE_EXTENDED_FLAGS, ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0080, 0x0200
# console output modes
ENABLE_PROCESSED_OUTPUT, ENABLE_WRAP_AT_EOL_OUTPUT = 0x0001, 0x0002
ENABLE_VIRTUAL_TERMINAL_PROCESSING, DISABLE_NEWLINE_AUTO_RETURN = 0x0004, 0x0008
CP_UTF8 = 65001


def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetStdHandle.restype = _HANDLE
    k.GetStdHandle.argtypes = [_DWORD]
    k.CreateEventW.restype = _HANDLE
    k.CreateEventW.argtypes = [ctypes.c_void_p, _BOOL, _BOOL, ctypes.c_wchar_p]
    k.SetEvent.argtypes = [_HANDLE]
    k.GetConsoleMode.argtypes = [_HANDLE, ctypes.POINTER(_DWORD)]
    k.SetConsoleMode.argtypes = [_HANDLE, _DWORD]
    k.WaitForMultipleObjects.restype = _DWORD
    k.WaitForMultipleObjects.argtypes = [_DWORD, ctypes.POINTER(_HANDLE), _BOOL, _DWORD]
    k.GetNumberOfConsoleInputEvents.argtypes = [_HANDLE, ctypes.POINTER(_DWORD)]
    k.ReadConsoleInputW.argtypes = [_HANDLE, ctypes.POINTER(INPUT_RECORD), _DWORD, ctypes.POINTER(_DWORD)]
    k.GetConsoleCP.restype = _UINT
    k.GetConsoleOutputCP.restype = _UINT
    k.SetConsoleCP.argtypes = [_UINT]
    k.SetConsoleOutputCP.argtypes = [_UINT]
    k.CloseHandle.argtypes = [_HANDLE]
    return k


class _WinConsole:
    """The Windows console's input and output, through kernel32. Input arrives as key
    events carrying the characters of the escape sequences (ENABLE_VIRTUAL_TERMINAL_INPUT),
    which are handed on as UTF-8 for the same parser the Unix terminal uses."""

    def __init__(self, kernel32=None, stream=None):
        self.k = kernel32 if kernel32 is not None else _kernel32()
        self.stream = stream if stream is not None else getattr(sys.__stdout__, "buffer", None)
        self.hin = self.k.GetStdHandle(STD_INPUT_HANDLE & 0xFFFFFFFF)
        self.hout = self.k.GetStdHandle(STD_OUTPUT_HANDLE & 0xFFFFFFFF)
        self.event = self.k.CreateEventW(None, False, False, None)   # wake(): auto-reset
        self.saved = None
        self.high = None          # a high surrogate waiting for its partner
        self.resized = False

    def _mode(self, handle):
        mode = _DWORD(0)
        return mode.value if self.k.GetConsoleMode(handle, ctypes.byref(mode)) else None

    def enter(self):
        if self.saved is None:
            self.saved = (self._mode(self.hin), self._mode(self.hout), self.k.GetConsoleCP(),
                          self.k.GetConsoleOutputCP())
        self.k.SetConsoleMode(self.hin, ENABLE_VIRTUAL_TERMINAL_INPUT | ENABLE_WINDOW_INPUT | ENABLE_EXTENDED_FLAGS)
        out = self.saved[1] if self.saved[1] is not None else ENABLE_PROCESSED_OUTPUT | ENABLE_WRAP_AT_EOL_OUTPUT
        out |= ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if not self.k.SetConsoleMode(self.hout, out | DISABLE_NEWLINE_AUTO_RETURN):
            self.k.SetConsoleMode(self.hout, out)
        self.k.SetConsoleCP(CP_UTF8)
        self.k.SetConsoleOutputCP(CP_UTF8)

    def restore(self):
        if self.saved is None:
            return
        mode_in, mode_out, cp_in, cp_out = self.saved
        if mode_in is not None:
            self.k.SetConsoleMode(self.hin, mode_in)
        if mode_out is not None:
            self.k.SetConsoleMode(self.hout, mode_out)
        if cp_in:
            self.k.SetConsoleCP(cp_in)
        if cp_out:
            self.k.SetConsoleOutputCP(cp_out)

    def write(self, data):
        if self.stream is None:
            raise OSError("no console to write to")
        self.stream.write(data)
        self.stream.flush()

    def wake(self):
        if self.event:
            self.k.SetEvent(self.event)

    def poll(self, wait):
        """(UTF-8 bytes typed, whether input was ready, whether wake() was called)."""
        handles = (_HANDLE * 2)(self.hin, self.event)
        count = 2 if self.event else 1
        ms = int(max(0.0, wait) * 1000)
        got = self.k.WaitForMultipleObjects(count, handles, False, ms)
        if got == WAIT_OBJECT_0 + 1:
            return b"", False, True
        if got != WAIT_OBJECT_0:
            if got != WAIT_TIMEOUT:      # WAIT_FAILED: don't spin
                time.sleep(min(0.05, max(0.0, wait)))
            return b"", False, False
        waiting = _DWORD(0)
        if not self.k.GetNumberOfConsoleInputEvents(self.hin, ctypes.byref(waiting)) or not waiting.value:
            return b"", True, False
        records = (INPUT_RECORD * min(waiting.value, 512))()
        read = _DWORD(0)
        if not self.k.ReadConsoleInputW(self.hin, records, len(records), ctypes.byref(read)):
            return b"", True, False
        units = []
        for rec in records[:read.value]:
            if rec.EventType == KEY_EVENT:
                key = rec.Event.KeyEvent
                ch = key.uChar.UnicodeChar
                if key.bKeyDown and ch and ch != "\0":
                    units += [ord(ch)] * max(1, key.wRepeatCount)
            elif rec.EventType == WINDOW_BUFFER_SIZE_EVENT:
                self.resized = True
        return self._text(units).encode("utf-8", "replace"), True, False

    def _text(self, units):
        """UTF-16 code units -> text, keeping a high surrogate that ends a batch for the next."""
        if self.high is not None:
            units = [self.high] + units
            self.high = None
        if units and 0xD800 <= units[-1] <= 0xDBFF:
            self.high = units.pop()
        out = []
        i = 0
        while i < len(units):
            u = units[i]
            if 0xD800 <= u <= 0xDBFF and i + 1 < len(units) and 0xDC00 <= units[i + 1] <= 0xDFFF:
                out.append(chr(0x10000 + ((u - 0xD800) << 10) + (units[i + 1] - 0xDC00)))
                i += 2
                continue
            out.append("�" if 0xD800 <= u <= 0xDFFF else chr(u))
            i += 1
        return "".join(out)


class WinTerminal(Terminal):
    """The Windows console. Pictures go out as sixel (Windows Terminal 1.22 and later draw
    them, scaled from a 10x20-pixel cell); the classic console window gets blocks. There is
    no job control (Ctrl-Z does nothing) and no SIGWINCH: a resize arrives as a console
    event, and the size is looked at on every pass besides."""

    _eof_on_empty = False     # key-up and focus events read as nothing; that's not the end

    def __init__(self, kernel32=None, stream=None):
        self._kernel32, self._stream = kernel32, stream
        Terminal.__init__(self)

    def _init_io(self):
        self.fd_in = self.fd_out = None
        self._con = _WinConsole(self._kernel32, self._stream)
        self._seen_size = None

    def raw(self):
        self._con.enter()

    def cooked(self):
        self._con.restore()

    def install_signals(self):
        def on_term(sig, _frm):
            raise SystemExit(128 + int(sig))

        for name in ("SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, on_term)
                except (OSError, ValueError, RuntimeError):
                    pass

    def suspend(self):
        """Ctrl-Z: Windows has no job control, so it does nothing."""

    def wake(self):
        self._con.wake()

    def size(self):
        try:
            sz = os.get_terminal_size(sys.__stdout__.fileno())
            if sz.columns and sz.lines:
                return sz.columns, sz.lines, 0, 0
        except (OSError, AttributeError, ValueError):
            pass
        try:
            sz = os.get_terminal_size()
            return sz.columns, sz.lines, 0, 0
        except OSError:
            return 80, 24, 0, 0

    def _check_size(self):
        if self._con.resized:
            self._con.resized = False
            self.resized = True
        now = self.size()[:2]
        if self._seen_size is not None and now != self._seen_size:
            self.resized = True
        self._seen_size = now

    def _send(self, data):
        self._con.write(data)

    def _poll(self, wait):
        return self._con.poll(wait)


def open_terminal():
    """The terminal skydrop runs in: the Windows console, or a Unix tty."""
    if os.name == "nt":
        return WinTerminal()
    return Terminal()
