"""The screen of skydrop's own window: what the app's escape codes draw there.

skydrop's window (the Windows app, and skydrop --window) is a terminal of skydrop's
own. The app writes to it exactly what it writes to kitty or Ghostty - text, colors,
cursor moves and the kitty graphics protocol's pictures - and Screen keeps what that
draws: a grid of cells, each a character and a style, and the pictures with the places
they are shown. paint.py draws it; window.py shows it and turns keys and the mouse
into what a terminal sends.

Screen reads what skydrop sends and what terminals are commonly sent, and answers the
questions skydrop asks a terminal (its size in cells and pixels, the kitty graphics
query, DA1, XTVERSION, its colors). Anything it doesn't know is skipped: nothing that
arrives can stop it.
"""

import base64
import io
import itertools
import re
import unicodedata
import zlib

try:
    from PIL import Image
except ImportError:          # pragma: no cover - the window needs Pillow; Screen's text works without
    Image = None

from .theme import ANSI16

# cell attributes
BOLD, DIM, ITALIC, UNDERLINE, REVERSE, INVISIBLE, STRIKE, BLINK = 1, 2, 4, 8, 16, 32, 64, 128

DEFAULT_FG = (229, 229, 229)
DEFAULT_BG = (0, 0, 0)
PLAIN = (None, None, 0)        # a cell's style: (foreground, background, attributes); None: the default

# kitty keeps this much picture data at most (it drops the oldest unshown pictures past it)
PICTURE_BYTES = 320 * 1024 * 1024

_TOKEN = re.compile(
    r"\x1b\[([0-?]*)([ -/]*)([@-~])"          # 1-3: CSI parameters, intermediates, final
    r"|\x1b\]([^\x07\x1b]*)(?:\x07|\x1b\\)"   # 4: OSC, ended by BEL or ST
    r"|\x1b_([^\x1b]*)\x1b\\"                 # 5: APC (kitty graphics)
    r"|\x1bP([^\x1b]*)\x1b\\"                 # 6: DCS
    r"|\x1b[X^][^\x1b]*\x1b\\"                # SOS, PM: skipped
    r"|\x1b[()*+\-./][ -/]*[0-~]"             # a character set chosen: skipped
    r"|\x1b(#[0-~]|[0-9:;<=>?@A-OQ-WYZ\\`a-z{|}~])"   # 7: ESC 7, ESC 8, ESC c, ESC M ...
    r"|([\x00-\x1a\x1c-\x1f\x7f])"            # 8: a control character (not ESC)
    r"|([^\x00-\x1f\x7f]+)")                  # 9: text

_WIDTH = {}


def char_width(ch):
    """Cells a character takes: 2 for wide ones (East Asian wide, most emoji), 0 for
    combining marks, else 1."""
    w = _WIDTH.get(ch)
    if w is None:
        o = ord(ch)
        if o < 0x300:
            w = 1
        elif unicodedata.combining(ch) or o in (0x200B, 0x200C, 0x200D, 0xFE0E, 0xFE0F):
            w = 0
        elif unicodedata.east_asian_width(ch) in ("W", "F"):
            w = 2
        else:
            w = 1
        _WIDTH[ch] = w
    return w


def palette(n):
    """The 256-color palette: skydrop's 16 (theme.ANSI16), the 6x6x6 cube, the grays."""
    if n < 16:
        return ANSI16[n]
    if n < 232:
        n -= 16
        steps = (0, 95, 135, 175, 215, 255)
        return (steps[n // 36], steps[n // 6 % 6], steps[n % 6])
    v = 8 + 10 * (n - 232)
    return (v, v, v)


def _int(text, default=0):
    try:
        return int(text) if text else default
    except ValueError:
        return default


_SERIAL = itertools.count(1)


class Picture:
    """A picture sent with the kitty graphics protocol: its frames (Pillow images) and
    the one shown. `version` changes whenever what it shows does; `serial` is this
    picture's alone (one sent again under the same id is another), and `edits` counts
    the changes made to its frames after they came."""
    __slots__ = ("id", "frames", "frame", "version", "bytes", "serial", "edits")

    def __init__(self, pid, img):
        self.id = pid
        self.frames = [img]
        self.frame = 0
        self.version = 0
        self.bytes = img.size[0] * img.size[1] * 4
        self.serial = next(_SERIAL)
        self.edits = 0

    @property
    def image(self):
        return self.frames[self.frame]


class Placement:
    """Where a picture is shown: from the cell (row, col), stretched over cols x rows cells
    (0: its own size in pixels), part of it (crop: x, y, w, h), moved by (xoff, yoff)
    pixels inside its first cell, stacked by z (above the text from 0 up)."""
    __slots__ = ("iid", "pid", "row", "col", "cols", "rows", "crop", "xoff", "yoff", "z", "seq")

    def __init__(self, iid, pid, row, col, cols, rows, crop, xoff, yoff, z, seq):
        self.iid, self.pid, self.row, self.col = iid, pid, row, col
        self.cols, self.rows, self.crop, self.xoff, self.yoff, self.z, self.seq = \
            cols, rows, crop, xoff, yoff, z, seq

    def key(self):
        return (self.iid, self.row, self.col, self.cols, self.rows, self.crop, self.xoff, self.yoff, self.z)


class Screen:
    def __init__(self, cols=80, rows=24, cell=(10, 20), name="skydrop window", version=""):
        self.cols, self.rows = max(1, cols), max(1, rows)
        self.cell = cell                  # pixels a cell takes (for the pixel-size answers)
        self.name, self.version = name, version
        self.default_fg, self.default_bg = DEFAULT_FG, DEFAULT_BG
        self.chars = [[" "] * self.cols for _ in range(self.rows)]
        self.styles = [[PLAIN] * self.cols for _ in range(self.rows)]
        self.dirty = set(range(self.rows))    # rows changed since the painter last looked
        self.pending = ""                     # the start of an escape code cut off at the end of a write
        self.replies = []                     # what a terminal answers, for the app to read as input
        self.title = ""
        self.clipboard = None                 # text sent to the clipboard (OSC 52), until taken
        self.font_request = None              # a font asked for (OSC 50), until taken
        self.pictures = {}
        self.placements = {}
        self.pic_version = 0                  # changes whenever a picture or a placement does
        self.pic_bytes = 0
        self._seq = 0
        self._transfer = None                 # a picture arriving in chunks: (keys, [payloads])
        self.problems = []                    # what couldn't be read (kept short), for tests and the log
        self.reset_modes()

    # -- state ---------------------------------------------------------------------
    def reset_modes(self):
        self.x = self.y = 0
        self.wrap_next = False
        self.fg = self.bg = None
        self.attrs = 0
        self.style = PLAIN
        self.saved = (0, 0, PLAIN, None, None, 0)
        self.autowrap = True
        self.cursor_visible = True
        self.cursor_shape = 0                 # DECSCUSR: 0/1/2 block, 3/4 underline, 5/6 bar
        self.app_cursor = False               # DECCKM: arrows send ESC O A
        self.mouse_buttons = False            # 1000: presses and releases
        self.mouse_drag = False               # 1002: and moves with a button held
        self.mouse_motion = False             # 1003: and every move
        self.mouse_sgr = False                # 1006: reported as CSI < ... M
        self.bracketed_paste = False
        self.focus_events = False
        self.top, self.bottom = 0, self.rows - 1

    @property
    def mouse(self):
        return self.mouse_buttons or self.mouse_drag or self.mouse_motion

    def _set_style(self):
        self.style = (self.fg, self.bg, self.attrs)

    def resize(self, cols, rows):
        """The window took another size: the cells keep their places (cut off or padded)."""
        cols, rows = max(1, int(cols)), max(1, int(rows))
        if (cols, rows) == (self.cols, self.rows):
            return
        chars, styles = [], []
        for y in range(rows):
            if y < self.rows:
                rc, rs = self.chars[y][:cols], self.styles[y][:cols]
                if len(rc) < cols:
                    rc += [" "] * (cols - len(rc))
                    rs += [PLAIN] * (cols - len(rs))
                elif cols < self.cols and self.chars[y][cols] == "":
                    rc[cols - 1] = " "            # a wide character cut in half
            else:
                rc, rs = [" "] * cols, [PLAIN] * cols
            chars.append(rc)
            styles.append(rs)
        self.chars, self.styles = chars, styles
        self.cols, self.rows = cols, rows
        self.x, self.y = min(self.x, cols - 1), min(self.y, rows - 1)
        self.top, self.bottom = 0, rows - 1
        self.wrap_next = False
        self.dirty = set(range(rows))
        self.pic_version += 1

    def take_dirty(self):
        out, self.dirty = self.dirty, set()
        return out

    def take_replies(self):
        out, self.replies = "".join(self.replies), []
        return out

    def text(self):
        """The screen as plain text, a line a row (for tests and copying)."""
        return "\n".join("".join(row) for row in self.chars)

    def _problem(self, what):
        if len(self.problems) < 50:
            self.problems.append(what)

    # -- reading what the app writes ----------------------------------------------
    def feed(self, data):
        """Apply what the app wrote: text and escape codes, in any pieces."""
        if self.pending:
            data = self.pending + data
            self.pending = ""
        if not data:
            return
        pos, n = 0, len(data)
        match = _TOKEN.match
        while pos < n:
            m = match(data, pos)
            if m is None:
                # only an escape code can fail to match: complete, or cut off at the end?
                if data[pos] == "\x1b" and self._incomplete(data, pos):
                    self.pending = data[pos:]
                    if len(self.pending) > 64 * 1024 * 1024:     # never ends: drop it
                        self.pending = ""
                    return
                pos += 1                                        # a lone ESC: skip it
                continue
            pos = m.end()
            last = m.lastindex
            if last == 9:
                self._text(m.group(9))
            elif last == 8:
                self._control(m.group(8))
            elif last == 3:
                self._csi(m.group(1), m.group(2), m.group(3))
            elif last == 5:
                self._apc(m.group(5))
            elif last == 4:
                self._osc(m.group(4))
            elif last == 7:
                self._esc(m.group(7))
            # DCS (6), SOS/PM and character sets: nothing to do

    @staticmethod
    def _incomplete(data, pos):
        """Whether the escape code at `pos` may still be arriving: it runs to the end of
        what came (a string's end, ESC \\, may be about to follow an ESC at the very end)."""
        rest = data[pos + 1:pos + 2]
        if not rest:
            return True
        if rest == "[":
            return re.fullmatch(r"\x1b\[[0-?]*[ -/]*", data[pos:]) is not None
        if rest in "]_PX^":
            esc = data.find("\x1b", pos + 2)
            return esc < 0 or esc == len(data) - 1
        if rest in "()*+-./#":
            return len(data) - pos < 3
        return False

    # -- text -----------------------------------------------------------------------
    def _text(self, text):
        cols = self.cols
        style = self.style
        if text.isascii():
            while text:
                if self.wrap_next:
                    self._wrap()
                x, y = self.x, self.y
                room = cols - x
                part = text[:room] if self.autowrap else text
                if not self.autowrap and len(part) > room:
                    part = part[:room - 1] + part[-1]          # the last character lands on the last cell
                n = len(part)
                rc = self.chars[y]
                if rc[x] == "" and x > 0:
                    rc[x - 1] = " "                            # half of a wide character overwritten
                end = x + n
                if end < cols and rc[end] == "":
                    rc[end] = " "
                rc[x:end] = part
                self.styles[y][x:end] = [style] * n
                self.dirty.add(y)
                if end >= cols:
                    self.x = cols - 1
                    self.wrap_next = self.autowrap
                else:
                    self.x = end
                text = text[len(part):] if self.autowrap else ""
            return
        for ch in text:
            self._put(ch, style)

    def _put(self, ch, style):
        w = char_width(ch)
        if w == 0:
            return                       # combining marks and joiners: the base character stands alone
        cols = self.cols
        if self.wrap_next:
            self._wrap()
        if w == 2 and self.x >= cols - 1:
            if not self.autowrap or cols < 2:
                ch, w = " ", 1           # no room for both halves
            else:
                self.chars[self.y][self.x] = " "
                self.wrap_next = True
                self._wrap()
        x, y = self.x, self.y
        rc, rs = self.chars[y], self.styles[y]
        if rc[x] == "" and x > 0:
            rc[x - 1] = " "
        if x + w < cols and rc[x + w] == "":
            rc[x + w] = " "
        rc[x], rs[x] = ch, style
        if w == 2:
            rc[x + 1], rs[x + 1] = "", style
        self.dirty.add(y)
        if x + w >= cols:
            self.x = cols - 1
            self.wrap_next = self.autowrap
        else:
            self.x = x + w

    def _wrap(self):
        self.wrap_next = False
        self.x = 0
        self._linefeed()

    def _linefeed(self):
        if self.y == self.bottom:
            self._scroll_up(1)
        elif self.y < self.rows - 1:
            self.y += 1

    def _scroll_up(self, n):
        top, bottom = self.top, self.bottom
        n = max(1, min(n, bottom - top + 1))
        blank = self._blank_style()
        for _ in range(n):
            del self.chars[top]
            del self.styles[top]
            self.chars.insert(bottom, [" "] * self.cols)
            self.styles.insert(bottom, [blank] * self.cols)
        self.dirty.update(range(top, bottom + 1))
        if top == 0 and bottom == self.rows - 1 and self.placements:
            for key, pl in list(self.placements.items()):     # pictures move with the text
                pl.row -= n
                if pl.row + max(1, pl.rows) <= 0:
                    del self.placements[key]
            self.pic_version += 1

    def _scroll_down(self, n):
        top, bottom = self.top, self.bottom
        n = max(1, min(n, bottom - top + 1))
        blank = self._blank_style()
        for _ in range(n):
            del self.chars[bottom]
            del self.styles[bottom]
            self.chars.insert(top, [" "] * self.cols)
            self.styles.insert(top, [blank] * self.cols)
        self.dirty.update(range(top, bottom + 1))

    def _blank_style(self):
        """Erased cells take the current background (and nothing else)."""
        return (None, self.bg, 0) if self.bg is not None else PLAIN

    def _erase(self, y, x0, x1):
        if not 0 <= y < self.rows:
            return
        x0, x1 = max(0, x0), min(self.cols, x1)
        if x1 <= x0:
            return
        rc = self.chars[y]
        if x0 > 0 and rc[x0] == "":
            rc[x0 - 1] = " "
        if x1 < self.cols and rc[x1] == "":
            rc[x1] = " "
        rc[x0:x1] = [" "] * (x1 - x0)
        self.styles[y][x0:x1] = [self._blank_style()] * (x1 - x0)
        self.dirty.add(y)

    def _control(self, c):
        if c == "\r":
            self.x = 0
            self.wrap_next = False
        elif c in "\n\x0b\x0c":
            self.wrap_next = False
            self._linefeed()
        elif c == "\x08":
            self.wrap_next = False
            self.x = max(0, self.x - 1)
        elif c == "\t":
            self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)
        # BEL and the rest: nothing to show

    def _move(self, y, x):
        self.y = max(0, min(self.rows - 1, y))
        self.x = max(0, min(self.cols - 1, x))
        self.wrap_next = False

    # -- escape codes -----------------------------------------------------------------
    def _esc(self, code):
        if code == "7":
            self.saved = (self.x, self.y, self.style, self.fg, self.bg, self.attrs)
        elif code == "8":
            x, y, self.style, self.fg, self.bg, self.attrs = self.saved
            self._move(y, x)
        elif code == "c":
            self.reset_modes()
            self._erase_all()
            self._delete_pictures("A")
        elif code == "M":                                   # reverse index
            if self.y == self.top:
                self._scroll_down(1)
            else:
                self._move(self.y - 1, self.x)
        elif code == "D":
            self._linefeed()
        elif code == "E":
            self.x = 0
            self._linefeed()

    def _erase_all(self):
        for y in range(self.rows):
            self._erase(y, 0, self.cols)

    def _csi(self, params, inter, final):
        private = params[:1] if params[:1] in "?<>=" else ""
        if private:
            params = params[1:]
        if inter:
            if inter == " " and final == "q":
                self.cursor_shape = _int(params)
            return                                          # DECRQM, DECSCA and the like
        if final == "m" and not private:
            self._sgr(params)
            return
        p = params.split(";") if params else []
        n0 = _int(p[0], 1) if p else 1
        n0 = n0 or 1
        if private == "?":
            if final in "hl":
                for item in p:
                    self._mode(_int(item), final == "h")
            elif final == "u":
                pass                                        # the kitty keyboard protocol's query: not here
            return
        if private == ">":
            if final == "q":                                # XTVERSION
                self.replies.append(f"\x1bP>|{self.name} {self.version}".rstrip() + "\x1b\\")
            elif final == "c":                              # DA2
                self.replies.append("\x1b[>1;10;0c")
            return
        if private:
            return
        if final in "Hf":
            row = _int(p[0], 1) if p else 1
            col = _int(p[1], 1) if len(p) > 1 else 1
            self._move((row or 1) - 1, (col or 1) - 1)
        elif final == "A":
            self._move(self.y - n0, self.x)
        elif final in "Be":
            self._move(self.y + n0, self.x)
        elif final in "Ca":
            self._move(self.y, self.x + n0)
        elif final == "D":
            self._move(self.y, self.x - n0)
        elif final == "E":
            self._move(self.y + n0, 0)
        elif final == "F":
            self._move(self.y - n0, 0)
        elif final in "G`":
            self._move(self.y, n0 - 1)
        elif final == "d":
            self._move(n0 - 1, self.x)
        elif final == "J":
            mode = _int(p[0]) if p else 0
            if mode == 0:
                self._erase(self.y, self.x, self.cols)
                for y in range(self.y + 1, self.rows):
                    self._erase(y, 0, self.cols)
            elif mode == 1:
                for y in range(0, self.y):
                    self._erase(y, 0, self.cols)
                self._erase(self.y, 0, self.x + 1)
            elif mode in (2, 3):
                self._erase_all()
                self._delete_pictures("a")                 # kitty takes the pictures off the screen
        elif final == "K":
            mode = _int(p[0]) if p else 0
            if mode == 0:
                self._erase(self.y, self.x, self.cols)
            elif mode == 1:
                self._erase(self.y, 0, self.x + 1)
            elif mode == 2:
                self._erase(self.y, 0, self.cols)
        elif final == "X":
            self._erase(self.y, self.x, self.x + n0)
        elif final == "@":
            rc, rs = self.chars[self.y], self.styles[self.y]
            n = min(n0, self.cols - self.x)
            rc[self.x:self.x] = [" "] * n
            rs[self.x:self.x] = [self._blank_style()] * n
            del rc[self.cols:], rs[self.cols:]
            self.dirty.add(self.y)
        elif final == "P":
            rc, rs = self.chars[self.y], self.styles[self.y]
            n = min(n0, self.cols - self.x)
            del rc[self.x:self.x + n], rs[self.x:self.x + n]
            rc += [" "] * n
            rs += [self._blank_style()] * n
            self.dirty.add(self.y)
        elif final == "S":
            self._scroll_up(n0)
        elif final == "T":
            self._scroll_down(n0)
        elif final in "LM":
            if self.top <= self.y <= self.bottom:
                top = self.top
                self.top = self.y
                (self._scroll_down if final == "L" else self._scroll_up)(n0)
                self.top = top
        elif final == "r":
            top = (_int(p[0], 1) or 1) - 1 if p else 0
            bottom = (_int(p[1], self.rows) or self.rows) - 1 if len(p) > 1 else self.rows - 1
            if 0 <= top < bottom < self.rows:
                self.top, self.bottom = top, bottom
                self._move(0, 0)
        elif final == "s":
            self._esc("7")
        elif final == "u":
            self._esc("8")
        elif final == "c":                                  # DA1: a VT220 with color, no sixel
            if not p or p[0] in ("", "0"):
                self.replies.append("\x1b[?62;22c")
        elif final == "n":
            what = _int(p[0]) if p else 0
            if what == 5:
                self.replies.append("\x1b[0n")
            elif what == 6:
                self.replies.append(f"\x1b[{self.y + 1};{self.x + 1}R")
        elif final == "t":
            what = _int(p[0]) if p else 0
            cw, ch = self.cell
            if what == 14:
                self.replies.append(f"\x1b[4;{self.rows * ch};{self.cols * cw}t")
            elif what == 16:
                self.replies.append(f"\x1b[6;{ch};{cw}t")
            elif what == 18:
                self.replies.append(f"\x1b[8;{self.rows};{self.cols}t")

    def _mode(self, n, on):
        if n == 25:
            self.cursor_visible = on
        elif n == 7:
            self.autowrap = on
            if not on:
                self.wrap_next = False
        elif n == 1:
            self.app_cursor = on
        elif n == 1000:
            self.mouse_buttons = on
        elif n == 1002:
            self.mouse_drag = on
        elif n == 1003:
            self.mouse_motion = on
        elif n == 1006:
            self.mouse_sgr = on
        elif n == 2004:
            self.bracketed_paste = on
        elif n == 1004:
            self.focus_events = on
        elif n in (47, 1047, 1049):
            # skydrop's window has one screen: the other screen's text starts and ends blank
            self._erase_all()
            if n == 1049 and on:
                self._esc("7")
            elif n == 1049:
                self._esc("8")

    def _sgr(self, params):
        if not params:
            self.fg = self.bg = None
            self.attrs = 0
            self._set_style()
            return
        parts = params.replace(":", ";:").split(";")
        i, n = 0, len(parts)
        while i < n:
            raw = parts[i]
            sub = raw.startswith(":")
            code = _int(raw.lstrip(":"))
            i += 1
            if sub:
                continue                                    # a colon sub-parameter left over
            if code == 0:
                self.fg = self.bg = None
                self.attrs = 0
            elif code in (38, 48, 58):
                color, i = self._color(parts, i)
                if code == 38:
                    self.fg = color
                elif code == 48:
                    self.bg = color
            elif 30 <= code <= 37:
                self.fg = palette(code - 30)
            elif 40 <= code <= 47:
                self.bg = palette(code - 40)
            elif 90 <= code <= 97:
                self.fg = palette(code - 90 + 8)
            elif 100 <= code <= 107:
                self.bg = palette(code - 100 + 8)
            elif code == 39:
                self.fg = None
            elif code == 49:
                self.bg = None
            elif code == 1:
                self.attrs |= BOLD
            elif code == 2:
                self.attrs |= DIM
            elif code == 3:
                self.attrs |= ITALIC
            elif code in (4, 21):
                styled = i < n and parts[i].startswith(":")
                if styled and _int(parts[i][1:]) == 0:
                    self.attrs &= ~UNDERLINE
                else:
                    self.attrs |= UNDERLINE
            elif code in (5, 6):
                self.attrs |= BLINK
            elif code == 7:
                self.attrs |= REVERSE
            elif code == 8:
                self.attrs |= INVISIBLE
            elif code == 9:
                self.attrs |= STRIKE
            elif code == 22:
                self.attrs &= ~(BOLD | DIM)
            elif code == 23:
                self.attrs &= ~ITALIC
            elif code == 24:
                self.attrs &= ~UNDERLINE
            elif code == 25:
                self.attrs &= ~BLINK
            elif code == 27:
                self.attrs &= ~REVERSE
            elif code == 28:
                self.attrs &= ~INVISIBLE
            elif code == 29:
                self.attrs &= ~STRIKE
        self._set_style()

    @staticmethod
    def _color(parts, i):
        """An extended color (38/48/58 ...): 5;n, 2;r;g;b or their colon forms."""
        def val(k):
            return _int(parts[k].lstrip(":")) if k < len(parts) else 0
        if i >= len(parts):
            return None, i
        kind = val(i)
        colon = parts[i].startswith(":")
        if kind == 5:
            return palette(max(0, min(255, val(i + 1)))), i + 2
        if kind == 2:
            k = i + 1
            # 38:2::r:g:b carries an (empty) color space first
            if colon and k + 3 < len(parts) and parts[k + 3].startswith(":") and parts[k] in (":", ""):
                k += 1
            rgb = tuple(max(0, min(255, val(k + j))) for j in range(3))
            return rgb, k + 3
        return None, i + 1

    def _osc(self, body):
        num, _, rest = body.partition(";")
        if num in ("0", "2"):
            self.title = rest[:200]
        elif num == "52":
            _, _, data = rest.partition(";")
            if data and data != "?":
                try:
                    self.clipboard = base64.b64decode(data).decode("utf-8", "replace")
                except (ValueError, TypeError):
                    self._problem("unreadable clipboard text")
        elif num in ("10", "11") and rest == "?":
            r, g, b = self.default_fg if num == "10" else self.default_bg
            self.replies.append(f"\x1b]{num};rgb:{r:02x}{r:02x}/{g:02x}{g:02x}/{b:02x}{b:02x}\x1b\\")
        elif num == "50":
            self.font_request = rest[:200]

    # -- the kitty graphics protocol -----------------------------------------------------
    def _apc(self, body):
        if not body.startswith("G"):
            return
        ctrl, _, payload = body[1:].partition(";")
        keys = {}
        for item in ctrl.split(","):
            k, eq, v = item.partition("=")
            if eq and k:
                keys[k] = v
        if self._transfer is not None and set(keys) <= {"m", "q"}:
            # the next chunk of a picture on its way
            self._transfer[1].append(payload)
            if keys.get("m", "0") != "1":
                head, chunks = self._transfer
                self._transfer = None
                self._kitty(head, "".join(chunks))
            return
        if keys.get("m") == "1" and keys.get("a", "t") in ("t", "T", "f"):
            self._transfer = (keys, [payload])
            return
        self._transfer = None
        self._kitty(keys, payload)

    def _reply(self, keys, message):
        """kitty's answer to a command: only when it carries an id and q doesn't hush it."""
        quiet = _int(keys.get("q"))
        if quiet >= 2 or (quiet == 1 and message == "OK"):
            return
        iid = keys.get("i")
        if not iid:
            return
        self.replies.append(f"\x1b_Gi={iid};{message}\x1b\\")

    def _kitty(self, keys, payload):
        action = keys.get("a", "t")
        try:
            if action in ("t", "T", "q"):
                img = self._decode(keys, payload)
                if action == "q":
                    self._reply(keys, "OK" if img is not None else "EINVAL:bad picture data")
                    return
                if img is None:
                    self._reply(keys, "EINVAL:bad picture data")
                    return
                iid = _int(keys.get("i"))
                if not iid:
                    self._reply(keys, "EINVAL:no image id")
                    return
                self._store(iid, img)
                self._reply(keys, "OK")
                if action == "T":
                    self._place(keys)
            elif action == "p":
                self._place(keys)
            elif action == "d":
                self._delete(keys)
            elif action == "f":
                self._frame(keys, payload)
            elif action == "a":
                self._animate(keys)
        except Exception as e:      # a picture is never worth the screen
            self._problem(f"kitty {action}: {type(e).__name__}: {e}")

    def _decode(self, keys, payload):
        if Image is None:
            return None
        if keys.get("t", "d") != "d":
            self._problem("kitty: only pictures sent directly (t=d) are read")
            return None
        try:
            data = base64.b64decode(payload)
            if keys.get("o") == "z":
                data = zlib.decompress(data)
        except (ValueError, zlib.error) as e:
            self._problem(f"kitty: undecodable data: {e}")
            return None
        fmt = keys.get("f", "32")
        if fmt == "100":
            img = Image.open(io.BytesIO(data))
            img.load()
            return img.convert("RGBA" if "A" in img.getbands() or "transparency" in img.info else "RGB")
        width, height = _int(keys.get("s")), _int(keys.get("v"))
        mode, bpp = ("RGBA", 4) if fmt == "32" else ("RGB", 3) if fmt == "24" else (None, 0)
        if mode is None or width <= 0 or height <= 0 or len(data) < width * height * bpp:
            self._problem(f"kitty: a {fmt} picture {width}x{height} with {len(data)} bytes")
            return None
        return Image.frombytes(mode, (width, height), data[:width * height * bpp])

    def _store(self, iid, img):
        old = self.pictures.get(iid)
        if old is not None:
            self.pic_bytes -= old.bytes
        pic = Picture(iid, img)
        if old is not None:
            pic.version = old.version + 1
        self.pictures[iid] = pic
        self.pic_bytes += pic.bytes
        self.pic_version += 1
        if self.pic_bytes > PICTURE_BYTES:
            shown = {pl.iid for pl in self.placements.values()}
            for other in list(self.pictures):
                if self.pic_bytes <= PICTURE_BYTES:
                    break
                if other != iid and other not in shown:
                    self.pic_bytes -= self.pictures.pop(other).bytes

    def _place(self, keys):
        iid = _int(keys.get("i"))
        if iid not in self.pictures:
            self._reply(keys, "ENOENT:no such image")
            self._problem(f"kitty: placing unknown image {iid}")
            return
        pid = _int(keys.get("p"))
        crop = None
        if any(k in keys for k in ("x", "y", "w", "h")):
            crop = (_int(keys.get("x")), _int(keys.get("y")), _int(keys.get("w")), _int(keys.get("h")))
        self._seq += 1
        pl = Placement(iid, pid, self.y, self.x, _int(keys.get("c")), _int(keys.get("r")), crop,
                       _int(keys.get("X")), _int(keys.get("Y")), _int(keys.get("z")), self._seq)
        old = self.placements.get((iid, pid))
        if old is not None:
            pl.seq = old.seq                       # placed again: it keeps its turn in the stack
        self.placements[(iid, pid)] = pl
        self.pic_version += 1
        self._reply(keys, "OK")
        if _int(keys.get("C")) != 1:               # the cursor moves past the picture
            cw, ch = self.cell
            pic = self.pictures[iid].image
            cols = pl.cols or -(-pic.size[0] // max(1, cw))
            rows = pl.rows or -(-pic.size[1] // max(1, ch))
            self._move(self.y + max(0, rows - 1), self.x + cols)

    def _delete(self, keys):
        what = keys.get("d", "a")
        free = what.isupper()
        lower = what.lower()
        if lower == "a":
            self._delete_pictures(what)
            return
        gone = []
        if lower == "i":
            iid = _int(keys.get("i"))
            pid = keys.get("p")
            gone = [k for k in self.placements if k[0] == iid and (pid is None or k[1] == _int(pid))]
            for k in gone:
                del self.placements[k]
            if free and iid in self.pictures and not any(k[0] == iid for k in self.placements):
                self.pic_bytes -= self.pictures.pop(iid).bytes
            self.pic_version += 1
            return
        if lower in ("c", "p"):
            x = _int(keys.get("x"), self.x + 1) - 1 if lower == "p" else self.x
            y = _int(keys.get("y"), self.y + 1) - 1 if lower == "p" else self.y
            gone = [k for k, pl in self.placements.items() if self._covers(pl, y, x)]
        elif lower == "z":
            z = _int(keys.get("z"))
            gone = [k for k, pl in self.placements.items() if pl.z == z]
        elif lower == "x":
            x = _int(keys.get("x"), 1) - 1
            gone = [k for k, pl in self.placements.items() if pl.col <= x < pl.col + max(1, pl.cols)]
        elif lower == "y":
            y = _int(keys.get("y"), 1) - 1
            gone = [k for k, pl in self.placements.items() if pl.row <= y < pl.row + max(1, pl.rows)]
        for k in gone:
            del self.placements[k]
        if free:
            for iid in {k[0] for k in gone}:
                if iid in self.pictures and not any(k[0] == iid for k in self.placements):
                    self.pic_bytes -= self.pictures.pop(iid).bytes
        self.pic_version += 1

    def _covers(self, pl, y, x):
        cw, ch = self.cell
        pic = self.pictures.get(pl.iid)
        size = pic.image.size if pic else (cw, ch)
        cols = pl.cols or -(-size[0] // max(1, cw))
        rows = pl.rows or -(-size[1] // max(1, ch))
        return pl.row <= y < pl.row + rows and pl.col <= x < pl.col + cols

    def _delete_pictures(self, what):
        self.placements.clear()
        if what == "A":
            self.pictures.clear()
            self.pic_bytes = 0
        self.pic_version += 1

    def _frame(self, keys, payload):
        """A frame of animation (a=f): a new frame, or a change to one (r=)."""
        iid = _int(keys.get("i"))
        pic = self.pictures.get(iid)
        if pic is None:
            self._reply(keys, "ENOENT:no such image")
            self._problem(f"kitty: a frame for unknown image {iid}")
            return
        data = self._decode(keys, payload)
        if data is None:
            return
        size = pic.frames[0].size
        edit = _int(keys.get("r"))
        base_no = _int(keys.get("c"))
        if edit and 1 <= edit <= len(pic.frames):
            canvas = pic.frames[edit - 1].convert("RGBA")
        elif base_no and 1 <= base_no <= len(pic.frames):
            canvas = pic.frames[base_no - 1].convert("RGBA")
        else:
            bg = _int(keys.get("Y"))
            canvas = Image.new("RGBA", size, ((bg >> 24) & 255, (bg >> 16) & 255, (bg >> 8) & 255, bg & 255))
        at = (_int(keys.get("x")), _int(keys.get("y")))
        layer = data.convert("RGBA")
        if _int(keys.get("X")) == 1 or (at == (0, 0) and layer.size == size and not (edit or base_no)):
            canvas.paste(layer, at)                         # replaces what is under it
        else:
            canvas.alpha_composite(layer, at)
        frame = canvas if any(b != 255 for b in canvas.getchannel("A").getextrema()) else canvas.convert("RGB")
        if edit and 1 <= edit <= len(pic.frames):
            pic.frames[edit - 1] = frame
            pic.edits += 1
            if edit - 1 == pic.frame:
                pic.version += 1
        else:
            pic.frames.append(frame)
            pic.bytes += size[0] * size[1] * 4
            self.pic_bytes += size[0] * size[1] * 4
        self.pic_version += 1
        self._reply(keys, "OK")

    def _animate(self, keys):
        """Animation control (a=a): skydrop steps through the frames itself (c=)."""
        pic = self.pictures.get(_int(keys.get("i")))
        if pic is None:
            return
        frame = _int(keys.get("c"))
        if frame and 1 <= frame <= len(pic.frames) and frame - 1 != pic.frame:
            pic.frame = frame - 1
            pic.version += 1
            self.pic_version += 1
