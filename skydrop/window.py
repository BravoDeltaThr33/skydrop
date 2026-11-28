"""skydrop in a window of its own: the Windows app (skydrop.exe), and skydrop --window.

The window is a small terminal of skydrop's own, made to look like Ghostty with its
default font. The app runs in it unchanged: it writes the same escape codes it writes
to kitty or Ghostty, and WindowTerminal hands them to a vt.Screen, which keeps the text
and the kitty pictures, and a paint.Painter, which draws them. The window (tkinter)
shows the rows of pixels that changed and turns keys, the mouse and pastes into the
bytes a terminal sends.

The app runs in a thread of its own; the window's loop runs in the main thread, as Tk
needs. Closing the window ends the app as `q` does.
"""

import os
import sys
import threading
import time
import traceback

from . import APP, VERSION, paint, term, vt

COLS, ROWS = 110, 40                   # the window's first size, in cells (skydrop's own layouts)
MIN_COLS, MIN_ROWS = 40, 12
PAD = 2                                # pixels around the cells (Ghostty's window-padding), at 96 DPI
BLINK = 0.6                            # seconds the cursor is on, then off
WHEEL_LINES = 3                        # arrow keys a wheel notch sends when the app takes no mouse
NAME = "skydrop window"                # what the window answers when the app asks (XTVERSION)


# ---------------------------------------------------------------------------- the terminal
def window_caps(cell, bg=vt.DEFAULT_BG):
    """What the window can do, said up front rather than asked: kitty pictures, 24-bit
    color, Unicode, its cell size."""
    caps = term.Caps()
    caps.name = NAME
    caps.xtversion = f"{NAME} {VERSION}"
    caps.kitty = True
    caps.sixel = False
    caps.truecolor = True
    caps.colors = 256
    caps.utf8 = True
    caps.cell_w, caps.cell_h = cell
    caps.da1 = "62;22"
    caps.bg_rgb = tuple(bg)
    caps.tmux = False
    caps.probed = True
    return caps


class WindowTerminal(term.Terminal):
    """The app's terminal in the window. What the app writes is drawn on `screen` and
    painted by `painter`; what the window gets from the keyboard and mouse arrives through
    give(). Works without a window too (headless: --snapshot and the tests)."""

    is_window = True
    _eof_on_empty = False

    def __init__(self, cols=COLS, rows=ROWS, font="jetbrains", size=paint.DEFAULT_SIZE, dpi=96.0):
        self.painter = paint.Painter(font, size, dpi)
        self.screen = vt.Screen(max(MIN_COLS, cols), max(MIN_ROWS, rows), self.painter.cell, NAME, VERSION)
        self.closed = False
        term.Terminal.__init__(self)
        self.caps = window_caps(self.painter.cell, self.screen.default_bg)

    def _init_io(self):
        self.fd_in = self.fd_out = None
        self._cond = threading.Condition()
        self._input = bytearray()
        self._woke = False
        self._want = None                  # a size the window asked for: (cols, rows)
        self._redraw = False               # the cell size changed: the app draws everything again
        self.frames = 0                    # frames painted (the window shows each)

    # the window has no modes, signals or job control to look after
    def raw(self):
        pass

    def cooked(self):
        pass

    def install_signals(self):
        pass

    def suspend(self):
        """Ctrl-Z does nothing in a window."""

    def probe(self, timeout=0.6):
        self.caps.probed = True
        return self.caps

    def wake(self):
        with self._cond:
            self._woke = True
            self._cond.notify_all()

    def size(self):
        cw, ch = self.painter.cell
        return self.screen.cols, self.screen.rows, self.screen.cols * cw, self.screen.rows * ch

    def cell_px(self):
        return self.painter.cell

    # -- the app's output ---------------------------------------------------------------
    def flush(self):
        if not self.out:
            return
        text = "".join(self.out)
        self.out = []
        self.show(text)

    def _send(self, data):
        self.show(data.decode("utf-8", "replace") if isinstance(data, bytes) else data)

    def show(self, text):
        """Draw what the app wrote, answer what it asked, paint the rows it changed."""
        self.screen.feed(text)
        replies = self.screen.take_replies()
        if replies:
            self.give(replies.encode("utf-8"))
        self.painter.paint(self.screen)
        self.frames += 1

    # -- the window's input -------------------------------------------------------------
    def give(self, data):
        """Bytes for the app to read, as a terminal sends them (keys, the mouse, a paste)."""
        if not data:
            return
        with self._cond:
            self._input += data
            self._cond.notify_all()

    def _poll(self, wait):
        with self._cond:
            if not (self._input or self._woke or self.closed):
                self._cond.wait(max(0.0, wait))
            if self.closed:
                raise SystemExit(0)            # the window was closed: the app ends as with q
            chunk = bytes(self._input)
            self._input.clear()
            woke, self._woke = self._woke, False
        return chunk, bool(chunk), woke

    def resize_to(self, cols, rows):
        """The window's new size in cells (from the window's thread)."""
        with self._cond:
            self._want = (max(MIN_COLS, int(cols)), max(MIN_ROWS, int(rows)))
            self._woke = True
            self._cond.notify_all()

    def refont(self, font=None, size=None, dpi=None):
        """Another font or size (from the window's thread): every cell is painted again and
        the app draws its pictures again at the new cell size."""
        self.painter.set_font(font, size, dpi)
        self.caps.cell_w, self.caps.cell_h = self.painter.cell
        with self._cond:
            self._redraw = True
            self._woke = True
            self._cond.notify_all()

    def _check_size(self):
        with self._cond:
            want, self._want = self._want, None
            redraw, self._redraw = self._redraw, False
        if want and want != (self.screen.cols, self.screen.rows):
            self.screen.resize(*want)
            self.resized = True
        if redraw:
            self.screen.dirty = set(range(self.screen.rows))
            self.screen.pic_version += 1
            self.resized = True
            self.resumed = True                # the app sends its pictures again

    def close(self):
        with self._cond:
            self.closed = True
            self._cond.notify_all()


# ---------------------------------------------------------------------------- keys and the mouse
SHIFT, LOCK, CONTROL = 0x1, 0x2, 0x4
ALT_X11, ALT_WINDOWS = 0x8, 0x20000          # Tk's Alt: Mod1 on X11; on Windows 0x8 is Num Lock
_CURSOR_KEYS = {"Up": "A", "Down": "B", "Right": "C", "Left": "D", "Home": "H", "End": "F",
                "KP_Up": "A", "KP_Down": "B", "KP_Right": "C", "KP_Left": "D", "KP_Home": "H", "KP_End": "F"}
_TILDE_KEYS = {"Insert": 2, "Delete": 3, "Prior": 5, "Next": 6, "KP_Insert": 2, "KP_Delete": 3,
               "KP_Prior": 5, "KP_Next": 6, "F5": 15, "F6": 17, "F7": 18, "F8": 19, "F9": 20, "F10": 21,
               "F11": 23, "F12": 24}
_SS3_KEYS = {"F1": "P", "F2": "Q", "F3": "R", "F4": "S"}
_MODIFIERS = {"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Meta_L", "Meta_R",
              "Super_L", "Super_R", "Caps_Lock", "Num_Lock", "Scroll_Lock", "ISO_Level3_Shift",
              "Win_L", "Win_R", "App"}


def modifiers(state, windows=None):
    """(shift, alt, ctrl) from a Tk event's state."""
    windows = os.name == "nt" if windows is None else windows
    alt = ALT_WINDOWS if windows else ALT_X11
    return bool(state & SHIFT), bool(state & alt), bool(state & CONTROL)


def key_bytes(keysym, char, state, app_cursor=False, windows=None):
    """What a terminal sends for a key (xterm's sequences, as kitty and Ghostty send them
    by default), or None for a key that sends nothing (Shift on its own)."""
    if keysym in _MODIFIERS:
        return None
    shift, alt, ctrl = modifiers(state, windows)
    windows = os.name == "nt" if windows is None else windows
    mod = 1 + shift + 2 * alt + 4 * ctrl
    if keysym in _CURSOR_KEYS:
        letter = _CURSOR_KEYS[keysym]
        if mod > 1:
            return f"\x1b[1;{mod}{letter}"
        return ("\x1bO" if app_cursor else "\x1b[") + letter
    if keysym in _TILDE_KEYS:
        n = _TILDE_KEYS[keysym]
        return f"\x1b[{n};{mod}~" if mod > 1 else f"\x1b[{n}~"
    if keysym in _SS3_KEYS:
        return f"\x1b[1;{mod}{_SS3_KEYS[keysym]}" if mod > 1 else "\x1bO" + _SS3_KEYS[keysym]
    if keysym in ("ISO_Left_Tab",) or (keysym == "Tab" and shift):
        return "\x1b[Z"
    if keysym == "Tab":
        return "\x1b\t" if alt else "\t"
    if keysym in ("Return", "KP_Enter"):
        return "\x1b\r" if alt else "\r"
    if keysym == "BackSpace":
        out = "\x08" if ctrl else "\x7f"
        return "\x1b" + out if alt else out
    if keysym == "Escape":
        return "\x1b"
    if keysym == "space" and ctrl:
        return "\x00"
    if not char:
        return None
    if windows and ctrl and alt and char.isprintable():
        return char                      # AltGr: the character it makes (@ on a German keyboard)
    if ctrl and not alt and len(char) == 1 and char.isprintable() and char.isascii():
        low = char.lower()               # Ctrl and a letter Tk left as the letter
        if "a" <= low <= "z":
            char = chr(ord(low) - 96)
        elif char in "[\\]^_":
            char = chr(ord(char) - 64)
        elif char == "@":
            char = "\x00"
    return "\x1b" + char if alt else char


def mouse_bytes(kind, button, col, row, shift=False, alt=False, ctrl=False, sgr=True):
    """A mouse report (cells counted from 1). kind: press, release, drag, move, wheel_up,
    wheel_down; button 0 left, 1 middle, 2 right."""
    if kind == "wheel_up":
        code = 64
    elif kind == "wheel_down":
        code = 65
    elif kind == "move":
        code = 35
    else:
        code = button + (32 if kind == "drag" else 0)
    code += 4 * shift + 8 * alt + 16 * ctrl
    if sgr:
        return f"\x1b[<{code};{col};{row}{'m' if kind == 'release' else 'M'}"
    if col > 223 or row > 223:
        return ""
    if kind == "release":
        code = 3 + 4 * shift + 8 * alt + 16 * ctrl
    return "\x1b[M" + chr(32 + code) + chr(32 + col) + chr(32 + row)


def paste_bytes(text, bracketed):
    """A paste as a terminal sends it: line breaks as Enter, and between the bracketed
    paste marks when the app asked for them (any marks inside are taken out)."""
    text = text.replace("\r\n", "\r").replace("\n", "\r")
    if bracketed:
        text = text.replace("\x1b[200~", "").replace("\x1b[201~", "")
        return "\x1b[200~" + text + "\x1b[201~"
    return text


def selection_text(screen, start, end):
    """The text between two cells (col, row), in reading order, each line's trailing
    spaces dropped."""
    (c0, r0), (c1, r1) = sorted((start, end), key=lambda p: (p[1], p[0]))
    lines = []
    for r in range(max(0, r0), min(screen.rows - 1, r1) + 1):
        row = screen.chars[r]
        a = c0 if r == r0 else 0
        b = c1 if r == r1 else screen.cols - 1
        lines.append("".join(ch for ch in row[a:b + 1]).rstrip())
    return "\n".join(lines)


# ---------------------------------------------------------------------------- the window
def _dpi_aware():
    """Windows: draw at the screen's own resolution (the font comes out sharp), rather
    than be stretched by Windows on a scaled screen; and show skydrop's icon in the
    taskbar, not Python's."""
    if os.name != "nt":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP}.window")
    except (AttributeError, OSError, ImportError):
        pass


class Window:
    """The Tk window: shows the painter's rows, and sends keys and the mouse to the app."""

    def __init__(self, root, term_, cfg=None):
        import tkinter as tk
        from PIL import Image, ImageOps, ImageTk
        self.tk, self.Image, self.ImageOps, self.ImageTk = tk, Image, ImageOps, ImageTk
        self.root, self.term, self.cfg = root, term_, cfg or {}
        self.painter, self.screen = term_.painter, term_.screen
        self.scale = max(1.0, term_.painter.dpi / 96.0)
        self.pad = max(1, round(PAD * self.scale))
        self.rows = {}                       # row -> its picture, as the painter last gave it
        self.photos = {}                     # row -> (PhotoImage, canvas item)
        self.edges = {}                      # "top"/"bottom" -> (PhotoImage, canvas item)
        self.grid = (0, 0, 0, 0)             # the cells shown: cols, rows, cell width, height
        self.size = (1, 1)                   # the canvas in pixels
        self.sel = None                      # a selection: [start (col, row), end (col, row)]
        self.selecting = False
        self.mouse_down = None               # the button held, for drags
        self.last_cell = None
        self.cursor = None                   # (PhotoImage, canvas item) of the cursor's cell
        self.cursor_on = True
        self.blink_at = time.monotonic()
        self.focused = True
        self.full = False
        self.title = ""
        self.closing = False
        self.busy_at = 0.0
        self.done = False
        self.font_set = window_settings(self.cfg)    # Settings' window font and size, as applied
        self.settings_at = 0.0

        root.title(APP)
        root.configure(bg="#000000")
        self.canvas = tk.Canvas(root, bg="#000000", highlightthickness=0, borderwidth=0, takefocus=1,
                                cursor="xterm")
        self.canvas.pack(fill="both", expand=True)
        self._icon()
        self._place()
        c = self.canvas
        c.bind("<Configure>", self.on_configure)
        root.bind("<KeyPress>", self.on_key)
        c.bind("<ButtonPress>", self.on_press)
        c.bind("<ButtonRelease>", self.on_release)
        c.bind("<Motion>", self.on_motion)
        c.bind("<MouseWheel>", self.on_wheel)
        root.bind("<FocusIn>", lambda _e: self.on_focus(True))
        root.bind("<FocusOut>", lambda _e: self.on_focus(False))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        c.focus_set()
        self.root.after(10, self.tick)

    # -- size and place ----------------------------------------------------------------
    def _icon(self):
        try:
            from . import icons
            self._icons = [self.ImageTk.PhotoImage(icons.raindrop_icon(n)) for n in (256, 48, 32, 16)]
            self.root.iconphoto(True, *self._icons)
        except Exception:          # no icon is no reason not to open
            self._icons = []

    def _place(self):
        """The window's size and place: as it was left, if that still fits the screen;
        else COLS x ROWS cells (smaller on a small screen), in the middle."""
        cw, ch = self.painter.cell
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w = 2 * self.pad + self.screen.cols * cw
        h = 2 * self.pad + self.screen.rows * ch
        w, h = min(w, int(sw * 0.92)), min(h, int(sh * 0.88))
        geo = f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}"
        saved = str(self.cfg.get("_window") or "")
        try:
            sz, x, y = saved.replace("-", "+-").split("+")[0], *saved.split("+")[1:3]
            sw_, sh_ = (int(v) for v in sz.split("x"))
            x, y = int(x), int(y)
            if 200 <= sw_ <= sw + 50 and 120 <= sh_ <= sh + 50 and -50 <= x < sw - 100 and -10 <= y < sh - 60:
                geo = f"{sw_}x{sh_}+{x}+{y}"
        except (ValueError, TypeError):
            pass
        self.root.geometry(geo)
        if str(self.cfg.get("_window_state") or "") == "zoomed":
            try:
                self.root.state("zoomed")
            except self.tk.TclError:
                pass

    def geometry(self):
        """("WxH+X+Y" of the window when it isn't maximized or full screen, its state)."""
        try:
            state = self.root.state()
        except self.tk.TclError:
            return None, None
        return self.root.geometry(), ("zoomed" if state == "zoomed" else "normal")

    def on_configure(self, event):
        self.size = (max(1, event.width), max(1, event.height))
        cw, ch = self.painter.cell
        cols = max(MIN_COLS, (event.width - 2 * self.pad) // cw)
        rows = max(MIN_ROWS, (event.height - 2 * self.pad) // ch)
        self.term.resize_to(cols, rows)
        self._edges(force=True)

    def toggle_full(self):
        self.full = not self.full
        self.root.attributes("-fullscreen", self.full)

    def zoom(self, step):
        """Ctrl + and Ctrl -: the font a point bigger or smaller; Ctrl 0: as set."""
        if step == 0:
            size = paint.font_size(self.cfg.get("window_font_size", paint.DEFAULT_SIZE))
        else:
            size = paint.font_size(self.painter.size_pt + step)
        if size == self.painter.size_pt:
            return
        self.term.refont(size=size)
        self._fit()

    def _fit(self):
        """As many cells as fit the window, after the cell size changed."""
        cw, ch = self.painter.cell
        w, h = self.size
        self.term.resize_to((w - 2 * self.pad) // cw, (h - 2 * self.pad) // ch)

    def _follow_settings(self, now):
        """Settings, Display: Window font or Window font size changed: use them at once."""
        if now - self.settings_at < 0.5:
            return
        self.settings_at = now
        want = window_settings(self.cfg)
        if want == self.font_set:
            return
        self.font_set = want
        self.term.refont(font=want[0], size=want[1])
        self._fit()

    # -- showing what the painter painted -------------------------------------------------
    def tick(self):
        if self.done:
            return
        now = time.monotonic()
        try:
            got = self.painter.take_rows()
        except Exception:          # never let a bad frame stop the window
            got = None
        if got:
            self.busy_at = now
            self.show(*got)
        if self.screen.clipboard is not None:          # OSC 52: the app copied something
            text, self.screen.clipboard = self.screen.clipboard, None
            self._copy(text)
        if self.screen.title != self.title:
            self.title = self.screen.title
            self.root.title(self.title or APP)
        self._cursor(now)
        self._follow_settings(now)
        if self.closing and not self.app_alive():
            self.finish()
            return
        self.root.after(8 if now - self.busy_at < 1.0 else 25, self.tick)

    def app_alive(self):
        thread = getattr(self, "thread", None)
        return thread is not None and thread.is_alive()

    def show(self, cols, rows, cell, changed):
        """Put the rows the painter changed on the canvas."""
        cw, ch = cell
        if (cols, rows, cw, ch) != self.grid:
            self.grid = (cols, rows, cw, ch)
            for y in [y for y in self.photos if y >= rows]:
                self.canvas.delete(self.photos.pop(y)[1])
                self.rows.pop(y, None)
            if self.sel:
                self.sel = None
        for y, img in changed.items():
            self.rows[y] = img
            self._show_row(y)
        if changed and (0 in changed or rows - 1 in changed):
            self._edges()
        self.cursor_on, self.blink_at = True, time.monotonic()

    def _show_row(self, y):
        img = self.rows.get(y)
        if img is None:
            return
        cols, rows, cw, ch = self.grid
        span = self._sel_span(y)
        if span:
            img = img.copy()
            a, b = span
            box = (a * cw, 0, (b + 1) * cw, ch)
            img.paste(self.ImageOps.invert(img.crop(box)), box[:2])
        img = self._padded(img)
        top = self.pad + y * ch
        old = self.photos.get(y)
        if old is not None and (old[0].width(), old[0].height()) == img.size:
            old[0].paste(img)
            self.canvas.coords(old[1], 0, top)
        else:
            photo = self.ImageTk.PhotoImage(img)
            if old is not None:
                self.canvas.itemconfigure(old[1], image=photo)
                self.canvas.coords(old[1], 0, top)
                self.photos[y] = (photo, old[1])
            else:
                item = self.canvas.create_image(0, top, image=photo, anchor="nw")
                self.photos[y] = (photo, item)
        if self.cursor is not None:
            self.canvas.tag_raise(self.cursor[1])

    def _padded(self, img):
        """A row with the padding beside it, each side the color of the cells next to it
        (as Ghostty's window-padding-color = extend)."""
        w, h = img.size
        full = max(w + 2 * self.pad, self.size[0])
        out = self.Image.new("RGB", (full, h))
        out.paste(img, (self.pad, 0))
        if self.pad:
            out.paste(img.crop((0, 0, 1, h)).resize((self.pad, h)), (0, 0))
        if full > w + self.pad:
            out.paste(img.crop((w - 1, 0, w, h)).resize((full - w - self.pad, h)), (w + self.pad, 0))
        return out

    def _edges(self, force=False):
        """The padding above the first row and below the last, each the color of the row
        beside it."""
        cols, rows, cw, ch = self.grid
        if not rows or 0 not in self.rows or rows - 1 not in self.rows:
            return
        W, H = self.size
        bottom_top = self.pad + rows * ch
        for name, src_y, top, height in (("top", 0, 0, self.pad), ("bottom", rows - 1, bottom_top, H - bottom_top)):
            row = self._padded(self.rows[src_y])
            line_y = 0 if name == "top" else row.size[1] - 1
            if height <= 0:
                if name in self.edges:
                    self.canvas.delete(self.edges.pop(name)[1])
                continue
            img = row.crop((0, line_y, row.size[0], line_y + 1)).resize((row.size[0], height))
            old = self.edges.get(name)
            if old is not None and (old[0].width(), old[0].height()) == img.size:
                old[0].paste(img)
                self.canvas.coords(old[1], 0, top)
            else:
                photo = self.ImageTk.PhotoImage(img)
                if old is not None:
                    self.canvas.delete(old[1])
                item = self.canvas.create_image(0, top, image=photo, anchor="nw")
                self.edges[name] = (photo, item)

    def _cursor(self, now):
        """The cursor's cell, drawn reversed; it blinks while nothing changes, and is
        hollow while another window has the focus (as in Ghostty)."""
        got = self.painter.cursor
        if got is None or not self.grid[0]:
            if self.cursor is not None:
                self.canvas.delete(self.cursor[1])
                self.cursor = None
            return
        x, y, img, outline = got
        if now - self.blink_at >= BLINK:
            self.cursor_on = not self.cursor_on
            self.blink_at = now
        cols, rows, cw, ch = self.grid
        if not self.focused:
            img = outline
        show = self.cursor_on or not self.focused
        left, top = self.pad + x * cw, self.pad + y * ch
        if self.cursor is None or self.cursor[2] is not img:
            photo = self.ImageTk.PhotoImage(img)
            if self.cursor is not None:
                self.canvas.delete(self.cursor[1])
            item = self.canvas.create_image(left, top, image=photo, anchor="nw")
            self.cursor = (photo, item, img)
        self.canvas.coords(self.cursor[1], left, top)
        self.canvas.itemconfigure(self.cursor[1], state="normal" if show else "hidden")
        self.canvas.tag_raise(self.cursor[1])

    # -- the selection --------------------------------------------------------------------
    def _sel_span(self, y):
        if not self.sel:
            return None
        (c0, r0), (c1, r1) = sorted(self.sel, key=lambda p: (p[1], p[0]))
        if not r0 <= y <= r1:
            return None
        cols = self.grid[0]
        return (c0 if y == r0 else 0, c1 if y == r1 else cols - 1)

    def _set_sel(self, sel):
        old = self.sel
        self.sel = sel
        rows = set()
        for s in (old, sel):
            if s:
                rows.update(range(min(s[0][1], s[1][1]), max(s[0][1], s[1][1]) + 1))
        for y in rows:
            self._show_row(y)

    def copy(self):
        if not self.sel:
            return False
        text = selection_text(self.screen, *self.sel)
        self._copy(text)
        self._set_sel(None)
        return True

    def _copy(self, text):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except self.tk.TclError:
            pass

    def paste(self):
        try:
            text = self.root.clipboard_get()
        except self.tk.TclError:
            return
        if text:
            self.term.give(paste_bytes(text, self.screen.bracketed_paste).encode("utf-8"))

    # -- input ----------------------------------------------------------------------------
    def cell_at(self, x, y):
        cols, rows, cw, ch = self.grid
        if not cols:
            return 0, 0
        return (max(0, min(cols - 1, (x - self.pad) // cw)), max(0, min(rows - 1, (y - self.pad) // ch)))

    def on_key(self, e):
        shift, alt, ctrl = modifiers(e.state)
        sym = e.keysym
        # the window's own keys
        if sym == "F11" or (alt and sym in ("Return", "KP_Enter")):
            self.toggle_full()
            return "break"
        if ctrl and not alt and sym in ("plus", "equal", "KP_Add"):
            self.zoom(1)
            return "break"
        if ctrl and not alt and sym in ("minus", "underscore", "KP_Subtract"):
            self.zoom(-1)
            return "break"
        if ctrl and not alt and sym in ("0", "KP_0", "KP_Insert") and not shift:
            self.zoom(0)
            return "break"
        if (ctrl and shift and sym.lower() == "c") or (ctrl and sym == "Insert") or \
                (ctrl and not alt and sym.lower() == "c" and self.sel):
            self.copy()
            return "break"
        if (ctrl and not alt and sym.lower() == "v") or (shift and sym == "Insert"):
            self.paste()
            return "break"
        data = key_bytes(sym, e.char, e.state, self.screen.app_cursor)
        if data:
            if self.sel:
                self._set_sel(None)
            self.term.give(data.encode("utf-8"))
        return "break"

    def _report(self, kind, button, e):
        col, row = self.cell_at(e.x, e.y)
        shift, alt, ctrl = modifiers(e.state)
        data = mouse_bytes(kind, button, col + 1, row + 1, shift, alt, ctrl, self.screen.mouse_sgr)
        if data:
            self.term.give(data.encode("utf-8"))

    def _wants_mouse(self, e):
        """The app takes the mouse (it asked for it), unless Shift is held: then the mouse
        selects text, as in Ghostty."""
        return self.screen.mouse and not (e.state & SHIFT)

    def on_press(self, e):
        self.canvas.focus_set()
        if e.num in (4, 5):                      # X11's wheel
            return self._wheel(e, e.num == 4)
        button = {1: 0, 2: 1, 3: 2}.get(e.num)
        if button is None:
            return None
        if self._wants_mouse(e):
            if self.sel:
                self._set_sel(None)
            self.mouse_down = button
            self.last_cell = self.cell_at(e.x, e.y)
            self._report("press", button, e)
            return "break"
        if button == 0:
            cell = self.cell_at(e.x, e.y)
            self.selecting = True
            self._set_sel([cell, cell])
        elif button == 2 and self.sel:            # right click copies, as in the Windows console
            self.copy()
        return "break"

    def on_release(self, e):
        if e.num in (4, 5):
            return "break"
        button = {1: 0, 2: 1, 3: 2}.get(e.num)
        if self.selecting and button == 0:
            self.selecting = False
            if self.sel and self.sel[0] == self.sel[1]:
                self._set_sel(None)              # a click, not a drag: nothing selected
            return "break"
        if self.mouse_down is not None and button is not None:
            self.mouse_down = None
            if self.screen.mouse:
                self._report("release", button, e)
        return "break"

    def on_motion(self, e):
        cell = self.cell_at(e.x, e.y)
        if self.selecting:
            if self.sel and cell != self.sel[1]:
                self._set_sel([self.sel[0], cell])
            return
        if cell == self.last_cell:
            return
        self.last_cell = cell
        held = self.mouse_down is not None
        if held and (self.screen.mouse_drag or self.screen.mouse_motion):
            self._report("drag", self.mouse_down, e)
        elif not held and self.screen.mouse_motion:
            self._report("move", 0, e)

    def on_wheel(self, e):
        return self._wheel(e, e.delta > 0, max(1, abs(int(e.delta)) // 120))

    def _wheel(self, e, up, notches=1):
        if self.screen.mouse:
            for _ in range(notches):
                self._report("wheel_up" if up else "wheel_down", 0, e)
        else:                                     # no mouse in the app: the arrow keys, as in Ghostty
            key = key_bytes("Up" if up else "Down", "", 0, self.screen.app_cursor)
            self.term.give((key * WHEEL_LINES * notches).encode("utf-8"))
        return "break"

    def on_focus(self, focused):
        if focused == self.focused:
            return
        self.focused = focused
        if self.screen.focus_events:
            self.term.give(b"\x1b[I" if focused else b"\x1b[O")

    # -- closing ----------------------------------------------------------------------------
    def on_close(self):
        """The window's close button (or Alt+F4): the app ends as with q, then the window."""
        if self.closing:
            self.finish()                        # asked twice: don't wait
            return
        self.closing = True
        self.closed_at = time.monotonic()
        self.term.close()
        self.root.after(3000, self.finish)

    def finish(self):
        if self.done:
            return
        self.done = True
        try:
            self.geometry_left = self.geometry()
            self.root.destroy()
        except self.tk.TclError:
            pass

    def ended(self):
        """The app ended by itself (q): close the window."""
        self.closing = True


# ---------------------------------------------------------------------------- running
def window_settings(cfg):
    """(font, size in points) from Settings, Display: Window font and Window font size."""
    return (paint.font_key(cfg.get("window_font")), paint.font_size(cfg.get("window_font_size", paint.DEFAULT_SIZE)))


# what a terminal tells the programs it runs about itself: started from one (skydrop
# --window in Ghostty, say), the app would take that terminal for its own
TERMINAL_ENV = ("TERM_PROGRAM", "TERM_PROGRAM_VERSION", "TERM_SESSION_ID", "COLORTERM", "TMUX", "TMUX_PANE",
                "STY", "WINDOWID", "VTE_VERSION", "TILIX_ID", "WT_SESSION", "WT_PROFILE_ID")
TERMINAL_ENV_PREFIXES = ("KITTY_", "GHOSTTY_", "WEZTERM_", "KONSOLE_", "ITERM_", "ALACRITTY_", "TERMINATOR_")


def own_environment(env=None):
    """The window is the app's terminal: forget the one it may have been started from."""
    env = os.environ if env is None else env
    for key in list(env):
        if key in TERMINAL_ENV or key.startswith(TERMINAL_ENV_PREFIXES):
            del env[key]
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    return env


def probe(cfg):
    """skydrop --window --probe (skydrop.exe --probe): what the window can do, and whether
    it can open here. Returns 0 when it can."""
    font, size = window_settings(cfg)
    term_ = WindowTerminal(COLS, ROWS, font, size, 96.0)
    c = term_.caps
    cols, rows, xp, yp = term_.size()
    print(f"Terminal:  {c.name} {VERSION} (skydrop's own window)")
    print(f"Supports:  {c.describe()}")
    print(f"Size:      {cols}x{rows} cells, {xp}x{yp} px at 96 dpi")
    p = term_.painter
    where = p.regular.path if p.regular else "not found"
    print(f"Font:      {p.font_label} {p.size_pt:g} pt, {p.cell[0]}x{p.cell[1]} px cells ({where})")
    from .model import resolve_tz
    zone = resolve_tz({"timeZone": "America/Chicago"}, None, None, None)
    print(f"Zones:     {'found' if zone is not None else 'missing (pip install tzdata)'}")
    try:
        import importlib
        import tkinter as tk
        importlib.import_module("PIL.ImageTk")
    except ImportError as e:
        print(f"Window:    can't open: {e}")
        return 1
    try:
        root = tk.Tk(className=APP)
        root.withdraw()
        tk_ver = root.tk.call("info", "patchlevel")
        dpi = float(root.winfo_fpixels("1i"))
        root.destroy()
    except tk.TclError as e:
        print(f"Window:    can't open here ({e})")
        return 1
    print(f"Window:    Tk {tk_ver}, the screen at {dpi:.0f} dpi")
    return 0


def run_app(term_, cfg, store, start, units=None, local8s=False):
    """The app, in the window's terminal, until it ends (the CLI's full-screen app,
    without the parts only a real terminal needs). Returns the exit status."""
    from .app import App
    from .config import on
    from .local8s import Local8s
    from .net import prune_disk_cache
    from .paths import CACHE_DIR, LOG_PATH, ensure
    app = None
    rc = 0
    try:
        prune_disk_cache()
        term_.enter(mouse=on(cfg, "mouse"))
        app = App(term_, cfg, store, start, units)
        if local8s:
            app.local8s = Local8s(app)
        app.run()
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception:
        tb = traceback.format_exc()
        try:
            ensure(CACHE_DIR)
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(f"--- {time.ctime()} {APP} {VERSION} (window)\n{tb}\n")
        except OSError:
            pass
        term_.problem = tb
        rc = 1
    finally:
        if app is not None:
            try:
                app.speech.shutdown()
            except Exception:
                pass
        try:
            term_.leave()
        except Exception:
            pass
    return rc


def run(cfg, store, start=0, units=None, local8s=False):
    """Open the window and run the app in it. Returns the exit status."""
    try:
        import importlib
        import tkinter as tk
        importlib.import_module("PIL.ImageTk")
    except ImportError as e:
        print(f"{APP}: the window needs tkinter and Pillow's ImageTk ({e}). On Debian and Ubuntu: "
              "sudo apt install python3-tk python3-pil.imagetk", file=sys.stderr)
        return 2
    _dpi_aware()
    try:
        root = tk.Tk(className=APP)
    except tk.TclError as e:
        print(f"{APP}: can't open a window here ({e})", file=sys.stderr)
        return 2
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except (tk.TclError, ValueError):
        dpi = 96.0
    font, size = window_settings(cfg)
    term_ = WindowTerminal(COLS, ROWS, font, size, dpi if 48 <= dpi <= 480 else 96.0)
    term_.problem = None
    win = Window(root, term_, cfg)
    result = {"rc": 0}

    def app_thread():
        try:
            result["rc"] = run_app(term_, cfg, store, start, units, local8s)
        finally:
            win.closing = True                    # the window closes when the app is done

    thread = threading.Thread(target=app_thread, name="skydrop-app", daemon=True)
    win.thread = thread
    thread.start()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    term_.close()
    thread.join(2.0)
    _remember(getattr(win, "geometry_left", None))
    if term_.problem:
        _report_problem(term_.problem)
    return result["rc"]


def _remember(geo):
    """Keep the window's size and place for next time (in config.json, as _window)."""
    if not geo or not geo[0]:
        return
    try:
        from .config import load_config, save_config
        cfg = load_config()
        if geo[1] != "zoomed":
            cfg["_window"] = geo[0]
        cfg["_window_state"] = geo[1]
        save_config(cfg)
    except Exception:
        pass


def _report_problem(tb):
    """The app hit a bug: say so where it can be seen (the console, if any; else a box)."""
    from .paths import LOG_PATH
    msg = f"{APP} hit an unexpected problem; details were saved to {LOG_PATH}"
    if sys.stderr is not None:
        try:
            print(tb, file=sys.stderr)
            print(msg, file=sys.stderr)
        except (OSError, ValueError):
            pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, APP, 0x10)
        except (AttributeError, OSError):
            pass


def snapshot(cfg, store, path, start=0, units=None, cols=COLS, rows=ROWS, wait=20.0, settle=1.5):
    """Run the app headless in the window's terminal, wait for its data (at most `wait`
    seconds), and save what the window would show as a PNG. For checks (the .exe's
    smoke test) and pictures. Returns the exit status."""
    font, size = window_settings(cfg)
    term_ = WindowTerminal(cols, rows, font, size, 96.0)
    term_.problem = None
    result = {"rc": 0}

    def app_thread():
        result["rc"] = run_app(term_, cfg, store, start, units)

    thread = threading.Thread(target=app_thread, name="skydrop-app", daemon=True)
    thread.start()
    t0 = time.time()
    time.sleep(0.5)
    while time.time() - t0 < wait and (store.busy or not getattr(store, "last_done", True)):
        time.sleep(0.2)
    time.sleep(settle)
    img = term_.painter.snapshot()
    term_.close()
    thread.join(5.0)
    if img is None:
        print(f"{APP}: nothing was drawn", file=sys.stderr)
        return 1
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    img.save(path)
    if term_.problem:
        _report_problem(term_.problem)
        return 1
    return result["rc"]
