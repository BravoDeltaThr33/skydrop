"""Off-screen canvas, image blocks, and a diffing renderer that writes ANSI
escape sequences (truecolor, 256 or 16 colors) plus kitty or sixel images.
"""

import time
from collections import OrderedDict

from . import gfx
from .crt import CRT
from .errlog import LOG
from .theme import rgb_to_16, rgb_to_256
from .util import WIDE_OK, safe_text

_ATTR_CODES = {"b": "1", "d": "2", "i": "3", "u": "4", "r": "7"}
COVERED = "\0"  # marks cells owned by a sixel image in the previous frame


class Canvas:
    def __init__(self, w, h, fill="text"):
        self.w, self.h = w, h
        self.chars = [[" "] * w for _ in range(h)]
        self.styles = [[fill] * w for _ in range(h)]
        self.img_rows = {}      # slot -> {row_index: (y, x, width)}
        self.cursor = None      # (y, x) -> show the text cursor there
        self.hotspots = []      # (y, x0, x1, action) for mouse clicks

    def put(self, y, x, line, width=None):
        if y < 0 or y >= self.h or not line:
            return x
        limit = self.w if width is None else min(self.w, x + width)
        row_c, row_s = self.chars[y], self.styles[y]
        col = x
        for text, style in line:
            if not text or col >= limit:
                if col >= limit:
                    break
                continue
            if isinstance(style, tuple) and style and style[0] == "@img":
                n = len(text)
                fill = style[3] if len(style) > 3 else "text"
                if col >= 0 and col + n <= limit:
                    self.img_rows.setdefault(style[1], {})[style[2]] = (y, col, n, fill)
                for k in range(n):
                    c = col + k
                    if 0 <= c < limit:
                        row_c[c] = " "
                        row_s[c] = fill
                col += n
                continue
            if text.isascii() and text.isprintable():
                plain = True
            else:
                text = safe_text(text)
                plain = WIDE_OK.isdisjoint(text)
            if plain and col >= 0:                  # a run of ordinary characters: one step
                end = col + len(text)
                if end > limit:
                    end = limit
                    text = text[:end - col]
                row_c[col:end] = text
                row_s[col:end] = [style] * (end - col)
                col = end
                continue
            for ch in text:
                if col >= limit:
                    break
                if ch in WIDE_OK:
                    if col + 1 >= limit:                 # no room for both halves
                        ch = " "
                    elif col >= 0:
                        row_c[col], row_s[col] = ch, style
                        row_c[col + 1], row_s[col + 1] = "", style   # the glyph's second cell
                        col += 2
                        continue
                if col >= 0:
                    row_c[col] = ch
                    row_s[col] = style
                col += 1
        return col

    def fill(self, y, x, w, h, style="text", ch=" "):
        x0, x1 = max(0, x), min(self.w, x + w)
        if x1 <= x0:
            return
        for yy in range(max(0, y), min(self.h, y + h)):
            self.chars[yy][x0:x1] = [ch] * (x1 - x0)
            self.styles[yy][x0:x1] = [style] * (x1 - x0)

    def recolor(self, y, x, w, style):
        """Change the style of existing characters (used for highlights)."""
        x0, x1 = max(0, x), min(self.w, x + w)
        if 0 <= y < self.h and x1 > x0:
            self.styles[y][x0:x1] = [style] * (x1 - x0)

    def hotspot(self, y, x0, x1, action):
        self.hotspots.append((y, x0, x1, action))

    def embed(self, inner, oy, ox):
        """Put another canvas inside this one at (oy, ox): text, pictures, click spots and cursor."""
        for y in range(inner.h):
            yy = y + oy
            if 0 <= yy < self.h:
                n = max(0, min(inner.w, self.w - ox))
                self.chars[yy][ox:ox + n] = inner.chars[y][:n]
                self.styles[yy][ox:ox + n] = inner.styles[y][:n]
        for slot, rows in inner.img_rows.items():
            self.img_rows[slot] = {r: (y + oy, x + ox, w, fill) for r, (y, x, w, fill) in rows.items()}
        self.hotspots += [(y + oy, x0 + ox, x1 + ox, act) for (y, x0, x1, act) in inner.hotspots]
        if inner.cursor:
            self.cursor = (inner.cursor[0] + oy, inner.cursor[1] + ox)

    def placements(self, registry):
        """Complete image blocks that nothing was drawn over: [(slot, y, x, cols, rows)].

        A block partly covered by later text (the help box, a dialog) is left out, so the
        text stays visible instead of the picture hiding it."""
        out = []
        for slot, rows in self.img_rows.items():
            spec = registry.current.get(slot)
            if not spec:
                continue
            first = rows.get(0)
            if not first:
                continue
            y0, x0, w0, fill = first
            blank_c, blank_s = [" "] * w0, [fill] * w0
            ok = True
            for r in range(spec.rows):
                got = rows.get(r)
                if not got or got[0] != y0 + r or got[1] != x0 or got[2] != w0:
                    ok = False
                    break
                yy = y0 + r
                if self.chars[yy][x0:x0 + w0] != blank_c or self.styles[yy][x0:x0 + w0] != blank_s:
                    ok = False
                    break
            if ok:
                out.append((slot, y0, x0, spec.cols, spec.rows))
        return out


class ImageSpec:
    __slots__ = ("slot", "key", "cols", "rows", "builder", "bg", "back")

    def __init__(self, slot, key, cols, rows, builder, bg, back=None):
        self.slot, self.key, self.cols, self.rows, self.builder, self.bg = slot, key, cols, rows, builder, bg
        self.back = back          # the color the picture is drawn on (None: a picture of its own, the radar)


class ImageRegistry:
    """Screens ask for picture blocks here; how they're drawn depends on the
    graphics mode: real pixels (kitty/sixel) or text (blocks/ascii)."""

    def __init__(self):
        self.mode = "blocks"
        self.cell = (10, 20)
        self.current = {}                 # slot -> ImageSpec (this frame)
        self.text_cache = OrderedDict()   # (key, mode, cols, rows) -> lines
        self.text_cells = 0
        self.pix_cache = OrderedDict()    # (key, w, h, tint) -> PIL image
        self.pix_bytes = 0
        self.errors = {}
        self.tint = None                  # img -> img (a CRT phosphor), applied to every picture
        self.tint_key = None

    def _failed(self, slot, e):
        self.errors[slot] = f"{type(e).__name__}: {e}"
        LOG.report(f"image:{slot}", f"Picture ({slot})", self.errors[slot])

    def _worked(self, slot):
        if self.errors.pop(slot, None) is not None:
            LOG.resolve(f"image:{slot}")

    @property
    def pixels(self):
        return self.mode in ("kitty", "sixel")

    def begin_frame(self):
        self.current = {}

    def block(self, slot, key, cols, rows, builder, bg="text", back=None):
        """Return `rows` lines that draw the picture.

        builder(kind, w, h) -> PIL image, or a list of lines for text kinds.
        kind is "pixels" (w,h in pixels), "blocks" (w cols, h rows) or "ascii".
        `back`: the color the builder draws the picture on, when it has one (CRT mode
        shows it as the tube shows that background)."""
        cols, rows = max(1, int(cols)), max(1, int(rows))
        if self.pixels and gfx.HAVE_PIL:
            self.current[slot] = ImageSpec(slot, key, cols, rows, builder, bg, back)
            return [[(" " * cols, ("@img", slot, r, bg))] for r in range(rows)]
        kind = "ascii" if self.mode == "ascii" else "blocks"
        ck = (key, kind, cols, rows)
        hit = self.text_cache.get(ck)
        if hit is not None:
            self.text_cache.move_to_end(ck)
            return hit
        try:
            res = builder(kind, cols, rows)
            self._worked(slot)
        except Exception as e:  # never let a picture break the screen
            self._failed(slot, e)
            return [[(" " * cols, bg)] for _ in range(rows)]
        if res is None:
            return [[(" " * cols, bg)] for _ in range(rows)]
        hit = res if isinstance(res, list) else gfx.halfblock_lines(res, cols, rows)
        self.text_cache[ck] = hit
        self.text_cells += cols * rows
        while len(self.text_cache) > 1 and (len(self.text_cache) > 96 or self.text_cells > 250000):
            (_k, _m, c, r), _v = self.text_cache.popitem(last=False)
            self.text_cells -= c * r
        return hit

    def image(self, slot, w, h):
        spec = self.current.get(slot)
        if spec is None:
            return None
        ck = (spec.key, w, h, self.tint_key)
        hit = self.pix_cache.get(ck)
        if hit is not None:
            self.pix_cache.move_to_end(ck)
            return hit
        try:
            img = spec.builder("pixels", w, h)
            if img is not None and not isinstance(img, list):
                self._worked(slot)
        except Exception as e:
            self._failed(slot, e)
            img = None
        if img is None or isinstance(img, list):
            return None
        if img.size != (w, h):
            iw, ih = img.size
            if self.mode == "kitty" and iw <= w and ih <= h and abs(iw / ih - w / h) < 0.02 * (w / h):
                pass  # kitty scales it to the cell area itself; sending the smaller picture is faster
            elif iw >= w and ih >= h:
                img = img.resize((w, h), gfx.LANCZOS)
            else:  # enlarging: NEAREST keeps the palette small, which keeps sixel encoding fast
                img = img.resize((w, h), gfx.NEAREST if self.mode == "sixel" else gfx.BILINEAR)
        if img.mode != "RGB":
            img = img.convert("RGB")
        if self.tint is not None and self.tint_key is not None:
            img = self.tint(img, spec.key, spec.back)
        self.pix_cache[ck] = img
        self.pix_bytes += img.size[0] * img.size[1] * 3
        while len(self.pix_cache) > 1 and (len(self.pix_cache) > 64 or self.pix_bytes > 128 * 1024 * 1024):
            _k, gone = self.pix_cache.popitem(last=False)
            self.pix_bytes -= gone.size[0] * gone.size[1] * 3
        return img

    def forget(self):
        self.text_cache.clear()
        self.text_cells = 0
        self.pix_cache.clear()
        self.pix_bytes = 0


class _KittyLayer:
    def __init__(self):
        self.ids = OrderedDict()   # (key, w, h) -> image id
        self.sizes = {}            # image id -> bytes the terminal keeps for it
        self.placed = {}           # slot -> (image id, y, x, cols, rows)
        self.pids = {}
        self.next_id = 7000

    def reset(self, images=False):
        self.placed = {}
        if images:
            # the terminal no longer holds any of our pictures: send each one again
            self.ids = OrderedDict()
            self.sizes = {}

    def pid(self, slot):
        if slot not in self.pids:
            self.pids[slot] = len(self.pids) + 1
        return self.pids[slot]

    def update(self, wanted, registry, cell, out):
        cw, ch = cell
        keep = set()
        for slot, y, x, cols, rows in wanted:
            spec = registry.current[slot]
            w, h = min(cols * cw, 2400), min(rows * ch, 1600)
            ck = (spec.key, w, h, registry.tint_key)
            iid = self.ids.get(ck)
            if iid is None:
                img = registry.image(slot, w, h)
                if img is None:
                    continue
                iid = self.next_id
                self.next_id += 1
                out.append(gfx.kitty_transmit(iid, img))
                self.ids[ck] = iid
                self.sizes[iid] = img.size[0] * img.size[1] * 4
            else:
                self.ids.move_to_end(ck)
            keep.add(slot)
            want = (iid, y, x, cols, rows)
            old = self.placed.get(slot)
            if old != want:
                out.append(f"\x1b[{y + 1};{x + 1}H")
                out.append(gfx.kitty_place(iid, self.pid(slot), cols, rows))
                if old and old[0] != iid:
                    out.append(gfx.kitty_delete_placement(old[0], self.pid(slot)))
                self.placed[slot] = want
        for slot in [s for s in self.placed if s not in keep]:
            old = self.placed.pop(slot)
            out.append(gfx.kitty_delete_placement(old[0], self.pid(slot)))
        # stay well inside kitty's image memory quota (it silently drops images past it)
        in_use = {v[0] for v in self.placed.values()}
        total = sum(self.sizes.values())
        for ck, iid in list(self.ids.items()):
            if len(self.ids) <= 40 and total <= 160 * 1024 * 1024:
                break
            if iid in in_use:
                continue
            del self.ids[ck]
            total -= self.sizes.pop(iid, 0)
            out.append(gfx.kitty_delete_image(iid))


class _SixelLayer:
    def __init__(self):
        self.shown = {}            # slot -> (key, y, x, cols, rows, w, h)
        self.cache = OrderedDict() # (key, w, h) -> sixel string

    def reset(self):
        self.shown = {}

    def update(self, wanted, registry, cell, out, force=False):
        cw, ch = cell
        keep = set()
        for slot, y, x, cols, rows in wanted:
            spec = registry.current[slot]
            w, h = cols * cw, rows * ch
            want = (spec.key, y, x, cols, rows, w, h, registry.tint_key)
            keep.add(slot)
            if not force and self.shown.get(slot) == want:
                continue
            ck = (spec.key, w, h, registry.tint_key)
            data = self.cache.get(ck)
            if data is None:
                img = registry.image(slot, w, h)
                if img is None:
                    continue
                data = gfx.sixel_encode(img, 255)
                self.cache[ck] = data
                while len(self.cache) > 1 and (len(self.cache) > 24 or
                                               sum(len(v) for v in self.cache.values()) > 64 * 1024 * 1024):
                    self.cache.popitem(last=False)
            out.append(f"\x1b[{y + 1};{x + 1}H")
            out.append(data)
            self.shown[slot] = want
        for slot in [s for s in self.shown if s not in keep]:
            del self.shown[slot]


_ASCII = {"°": "o", "·": ".", "…": ".", "−": "-", "–": "-", "—": "-", "‒": "-", "•": "*", "●": "*", "○": "o",
          "◉": "*", "◎": "@", "◆": "*", "◇": "o", "■": "#", "□": "o", "▪": "#", "▫": ".", "◀": "<", "▶": ">",
          "◂": "<", "▸": ">", "▲": "^", "▼": "v", "↑": "^", "↓": "v", "←": "<", "→": ">", "↳": ">", "↗": "/",
          "↘": "\\", "↙": "/", "↖": "\\", "⚠": "!", "✓": "v", "✔": "v", "✗": "x", "✘": "x", "❚": "|", "‹": "<",
          "›": ">", "«": "<", "»": ">", "“": '"', "”": '"', "‘": "'", "’": "'", "″": '"', "′": "'", "×": "x",
          "÷": "/", "±": "+", "≈": "~", "≤": "<", "≥": ">", "½": "h", "¼": "q", "¾": "t", "µ": "u", "²": "2",
          "³": "3", "⁄": "/", "★": "*", "☆": "*", "⚡": "!", "♪": "~", "▏": "|", "▕": "|"}
_ASCII_CACHE = {}


def to_ascii(ch):
    """A plain stand-in for a character a plain-text terminal can't show (Graphics: ascii)."""
    hit = _ASCII_CACHE.get(ch)
    if hit is None:
        hit = _ASCII.get(ch)
        if hit is None:
            import unicodedata
            o = ord(ch)
            if 0x2500 <= o <= 0x257F:      # box drawing
                name = unicodedata.name(ch, "")
                hit = "-" if "HORIZONTAL" in name and "VERTICAL" not in name else \
                    "|" if "VERTICAL" in name and "HORIZONTAL" not in name else "+"
            elif 0x2580 <= o <= 0x259F:    # blocks and shades
                hit = "#" if o in (0x2588, 0x2589, 0x258A, 0x2593) else ":"
            elif 0x2800 <= o <= 0x28FF:    # braille dots (charts)
                hit = "." if o != 0x2800 else " "
            else:                          # é -> e, and so on
                base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
                hit = base[:1] if base else "?"
        _ASCII_CACHE[ch] = hit
    return hit


class Renderer:
    def __init__(self, term, theme, color_mode="truecolor", graphics="blocks"):
        self.term = term
        self.theme = theme
        self.color_mode = color_mode
        self.graphics = graphics
        self.prev_c = None
        self.prev_s = None
        self.sgr_cache = {}
        self.sgr_crt = False
        self.kitty = _KittyLayer() if graphics == "kitty" else None
        self.sixel = _SixelLayer() if graphics == "sixel" else None
        self.frames = 0
        self.last_present = 0.0
        self.crt = CRT()                   # CRT mode: off until the settings say otherwise
        self.crt_cfg = None
        self.last = None                   # (canvas, registry, cell) of the last frame, for repaint()
        self.cursor = None                 # where the text cursor is shown, False hidden, None not known

    def configure(self, theme=None, color_mode=None, graphics=None, cfg=None):
        if theme is not None:
            self.theme = theme
        if color_mode is not None:
            self.color_mode = color_mode
        if graphics is not None and graphics != self.graphics:
            if self.graphics == "kitty":
                self.term.write(gfx.KITTY_CLEAR_ALL)
                self.crt.lost()
            self.graphics = graphics
            self.kitty = _KittyLayer() if graphics == "kitty" else None
            self.sixel = _SixelLayer() if graphics == "sixel" else None
        if cfg is not None:
            self.crt_cfg = cfg
        if self.crt_cfg is not None:
            self.crt.configure(self.crt_cfg, self.theme, self.graphics, self.color_mode)
        self.sgr_cache = {}
        self.invalidate()

    def repaint(self):
        """Draw the last frame again (the CRT's effects move on with time)."""
        if self.last is not None:
            self.present(*self.last)

    def invalidate(self):
        self.prev_c = None
        self.prev_s = None
        self.cursor = None

    def reset_graphics(self):
        """Forget every image the terminal holds (after Ctrl-Z/fg or Ctrl-L) and redraw."""
        if self.graphics == "kitty":
            self.term.write(gfx.KITTY_CLEAR_ALL)
            self.kitty = _KittyLayer()
            self.crt.lost()
        elif self.graphics == "sixel":
            self.sixel = _SixelLayer()
        self.invalidate()

    # -- colors ---------------------------------------------------------------
    def sgr(self, style):
        hit = self.sgr_cache.get(style)
        if hit is not None:
            return hit
        th = self.theme
        fg, bg, attrs, f16, b16, a16 = th.spec(style)
        # Background: the terminal's own - the page is left to the terminal (not under CRT
        # mode: a tube paints its own raised black)
        own = th.default_bg and not self.crt.on and tuple(bg) == th.pal["bg"]
        codes = ["0"]
        if self.color_mode == "16":
            for a in (a16 if f16 is not None else attrs) or "":
                if a in _ATTR_CODES:
                    codes.append(_ATTR_CODES[a])
            fi = f16 if f16 is not None else rgb_to_16(fg)
            bi = b16 if b16 is not None else rgb_to_16(bg)
            codes.append(str(30 + fi) if fi < 8 else str(90 + fi - 8))
            codes.append("49" if own else str(40 + bi) if bi < 8 else str(100 + bi - 8))
        else:
            for a in attrs or "":
                if a in _ATTR_CODES:
                    codes.append(_ATTR_CODES[a])
            if self.color_mode == "256":
                codes.append(f"38;5;{rgb_to_256(fg)}")
                codes.append("49" if own else f"48;5;{rgb_to_256(bg)}")
            else:
                codes.append(f"38;2;{fg[0]};{fg[1]};{fg[2]}")
                codes.append("49" if own else f"48;2;{bg[0]};{bg[1]};{bg[2]}")
        s = "\x1b[" + ";".join(codes) + "m"
        self.sgr_cache[style] = s
        if len(self.sgr_cache) > 20000:
            self.sgr_cache.clear()
        return s

    # -- output -----------------------------------------------------------------
    def present(self, canvas, registry, cell=(10, 20)):
        out = []
        H, W = canvas.h, canvas.w
        now = time.time()
        self.last = (canvas, registry, cell)
        crt = self.crt
        if crt.on != self.sgr_crt:
            self.sgr_cache, self.sgr_crt = {}, crt.on      # the terminal's own background: not under a tube
        registry.tint, registry.tint_key = (crt.tint_picture, crt.tint_key) if crt.on else (None, None)
        src = canvas
        if crt.on:
            canvas = crt.process(canvas, now)     # the phosphor and the text-layer effects
        full = (self.prev_c is None or len(self.prev_c) != H or (H and len(self.prev_c[0]) != W))
        if full:
            out.append("\x1b[0m\x1b[H\x1b[2J")
            if self.kitty:
                # Erasing the screen makes Ghostty (and some other terminals) drop every
                # kitty image, data and all - not just where it was shown. Free them
                # everywhere and transmit each picture again, or a picture whose content
                # didn't change (the radar after a theme switch or resize) stays blank.
                out.append(gfx.KITTY_CLEAR_ALL)
                self.kitty.reset(images=True)
                crt.lost()
            if self.sixel:
                self.sixel.reset()
            prev_c = [[COVERED] * W for _ in range(H)]
            prev_s = [[None] * W for _ in range(H)]
        else:
            prev_c, prev_s = self.prev_c, self.prev_s
        wanted = src.placements(registry) if (self.kitty or self.sixel) and not crt.hide_pictures else []
        covered = set()
        if self.sixel:
            cw, ch = cell
            wanted = [wnt for wnt in wanted if registry.image(wnt[0], wnt[3] * cw, wnt[4] * ch) is not None]
            for slot, y, x, cols, rows in wanted:
                for yy in range(y, min(H, y + rows)):
                    for xx in range(x, min(W, x + cols)):
                        covered.add((yy, xx))
        cur_style = None
        sgr = self.sgr
        for y in range(H):
            rc, rs = canvas.chars[y], canvas.styles[y]
            pc, ps = prev_c[y], prev_s[y]
            if rc == pc and rs == ps:
                continue
            x = 0
            while x < W:
                if (rc[x] == pc[x] and rs[x] == ps[x]) or (y, x) in covered:
                    x += 1
                    continue
                # start of a changed run
                out.append(f"\x1b[{y + 1};{x + 1}H")
                run_end = x
                gap = 0
                while run_end < W and gap < 4:
                    if (y, run_end) in covered:
                        break
                    if rc[run_end] == pc[run_end] and rs[run_end] == ps[run_end]:
                        gap += 1
                    else:
                        gap = 0
                    run_end += 1
                run_end -= gap
                plain = self.graphics == "ascii"
                for xx in range(x, run_end):
                    st = rs[xx]
                    if st != cur_style:
                        out.append(sgr(st))
                        cur_style = st
                    ch = rc[xx]
                    out.append(to_ascii(ch) if plain and ch >= "\x80" else ch)
                x = run_end
        if self.kitty:
            self.kitty.update(wanted, registry, cell, out)
            if crt.on and crt.pictures:
                crt.glass_frame(out, now, W, H, cell)              # the glass over the text
            elif crt.glass.any:
                crt.glass.off(out)
            elif crt.glass.pending:
                out.append(crt.glass.take())                       # the glass taken away
        elif self.sixel:
            self.sixel.update(wanted, registry, cell, out, force=full)
        if canvas.cursor:
            cy, cx = canvas.cursor
            if out or self.cursor != (cy, cx):
                out.append(f"\x1b[{cy + 1};{cx + 1}H\x1b[?25h")
                self.cursor = (cy, cx)
        elif self.cursor is not False:
            out.append("\x1b[?25l")
            self.cursor = False
        if out:                                  # nothing to change: nothing to send, nothing to redraw
            self.term.write("".join(out))
            self.term.flush()
        # remember what's on screen; sixel-owned cells become "unknown"
        self.prev_c = [list(r) for r in canvas.chars]
        self.prev_s = [list(r) for r in canvas.styles]
        if covered:
            for (yy, xx) in covered:
                self.prev_c[yy][xx] = COVERED
        self.frames += 1
        self.last_present = time.time()
        wait = crt.next_wake(now) if crt.on else None
        crt.wake_at = now + wait if wait is not None else None
