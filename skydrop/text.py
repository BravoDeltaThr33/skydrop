"""Styled lines: a line is a list of (text, style) segments.

Styles are theme role names ("title", "label", ...) or ("rgb", fg, bg, attrs)
tuples. Image placeholders use ("@img", key, row) and are swapped for real
pixels by the terminal layer.
"""

import re
import textwrap

from .util import safe_text

# ----------------------------------------------------------------------------
# basics
# ----------------------------------------------------------------------------


def seglen(line):
    return sum(len(t) for t, _ in line)


def clip(line, width):
    out, n = [], 0
    for text, style in line:
        if n >= width:
            break
        text = text[:width - n]
        out.append((text, style))
        n += len(text)
    return out


def padto(line, width, style="text"):
    line = clip(line, width)
    n = seglen(line)
    return line + [(" " * (width - n), style)] if n < width else line


def rjust_line(line, width, style="text"):
    line = clip(line, width)
    n = seglen(line)
    return ([(" " * (width - n), style)] if n < width else []) + line


def center_line(line, width, style="text"):
    line = clip(line, width)
    n = seglen(line)
    left = max(0, (width - n) // 2)
    out = ([(" " * left, style)] if left else []) + line
    return padto(out, width, style)


def restyle(line, style):
    """Every span in `style`; a picture keeps its cells, the style taking the fill around it."""
    return [(t, st[:3] + (style,) + st[4:]) if isinstance(st, tuple) and st and st[0] == "@img" else (t, style)
            for t, st in line]


def plain(line):
    return "".join(t for t, _ in line)


def wrap(text, width, style="text", indent=2):
    out = []
    if not text:
        return out
    paras = re.split(r"\n\s*\n", safe_text(str(text)).strip())
    for i, para in enumerate(paras):
        flat = " ".join(para.split())
        for piece in textwrap.wrap(flat, max(10, width - indent - 1)) or [""]:
            out.append([(" " * indent + piece, style)])
        if i < len(paras) - 1:
            out.append([])
    return out


def wrap_plain(text, width):
    """Wrap to a list of strings (no styling)."""
    flat = " ".join(safe_text(str(text or "")).split())
    return textwrap.wrap(flat, max(4, width)) or [""]


def labeled_wrap(label, text, width, lw=16, style="text", indent=2):
    body = textwrap.wrap(" ".join(safe_text(str(text or "")).split()), max(10, width - indent - lw - 1)) or [""]
    out = [[(" " * indent + (label if len(label) < lw else label[:lw - 2] + "… ").ljust(lw), "label"), (body[0], style)]]
    for piece in body[1:]:
        out.append([(" " * (indent + lw) + piece, style)])
    return out


def kv_grid(pairs, width, label_w=16, indent=2, max_cols=3, min_col=None):
    """Lay (label, value[, style]) pairs out in 1-3 columns."""
    pairs = [p for p in pairs if p and p[1] is not None]
    if min_col:
        ncols = max(1, (width - indent) // min_col)
    else:
        ncols = 3 if width >= 150 else 2 if width >= 84 else 1
    ncols = min(ncols, max_cols)
    colw = max(20, (width - indent) // ncols)
    out = []
    for i in range(0, len(pairs), ncols):
        line = [(" " * indent, "text")]
        chunk = pairs[i:i + ncols]
        for j, pair in enumerate(chunk):
            label, value = pair[0], pair[1]
            style = pair[2] if len(pair) > 2 else "value"
            cell = [(label[:label_w - 1].ljust(label_w), "label")]
            cell += value if isinstance(value, list) else [(safe_text(str(value)), style)]
            if j < len(chunk) - 1:
                line += padto(cell, colw - 1) + [(" ", "text")]
            else:
                line += clip(cell, colw)
        out.append(line)
    return out


def block(title, subtitle, rows, lw=16):
    lines = [[(title, "title")] + ([("  " + subtitle, "dim")] if subtitle else [])]
    for row in rows:
        if not row:
            continue
        label, value = row[0], row[1]
        style = row[2] if len(row) > 2 else "value"
        segs = value if isinstance(value, list) else [(safe_text(str(value)), style)]
        lines.append([("  " + label.ljust(lw), "label")] + segs)
    return lines


def side_by_side(blocks, width, gap=4, indent=2):
    n = len(blocks)
    colw = (width - indent - gap * (n - 1)) // n
    height = max(len(b) for b in blocks)
    out = []
    for i in range(height):
        line = [(" " * indent, "text")]
        for j, blk in enumerate(blocks):
            seg = blk[i] if i < len(blk) else []
            if j < n - 1:
                line += padto(seg, colw) + [(" " * gap, "text")]
            else:
                line += clip(seg, colw)
        out.append(line)
    return out


def columns(cols, widths, gap=2):
    """Join lists of lines column-wise with fixed widths."""
    height = max((len(c) for c in cols), default=0)
    out = []
    for i in range(height):
        line = []
        for j, (col, w) in enumerate(zip(cols, widths)):
            seg = col[i] if i < len(col) else []
            line += padto(seg, w)
            if j < len(cols) - 1 and gap:
                line.append((" " * gap, "text"))
        out.append(line)
    return out


def stacked(blocks, indent=2):
    out = []
    for i, blk in enumerate(blocks):
        if i:
            out.append([])
        out += [[(" " * indent, "text")] + ln for ln in blk]
    return out


def indent_lines(lines, n):
    return [[(" " * n, "text")] + ln if ln else [] for ln in lines]


# ----------------------------------------------------------------------------
# tables
# ----------------------------------------------------------------------------

def fit_columns(spec, width, flex, flex_max, indent=2, gap=1):
    """Drop low-priority columns until the table fits, then widen `flex`."""
    cols = [list(c) for c in spec]

    def total():
        return indent + sum(c[2] for c in cols) + gap * (len(cols) - 1)

    while total() > width:
        droppable = [c for c in cols if c[4] > 0]
        if not droppable:
            break
        worst = max(droppable, key=lambda c: (c[4], cols.index(c)))
        cols.remove(worst)
    extra = width - total()
    for c in cols:
        if c[0] == flex and extra > 0:
            c[2] += min(extra, max(0, flex_max - c[2]))
    return cols


def unit_cols(spec, F):
    rep = {"{precip}": F.u_precip, "{wind}": F.u_wind, "{press}": F.u_press, "{vis}": F.u_vis,
           "{dist}": F.u_dist, "{temp}": F.u_temp}
    out = []
    for key, head, width, align, prio in spec:
        for a, b in rep.items():
            head = head.replace(a, b)
        out.append((key, head, max(width, len(head)), align, prio))
    return out


def table_width(cols, gap=1):
    return sum(c[2] for c in cols) + gap * max(0, len(cols) - 1)


def table_row(cols, cells, gap=1, prefix=("  ", "text")):
    line = [prefix]
    for i, c in enumerate(cols):
        key, width, align = c[0], c[2], c[3]
        cell = cells.get(key, ("", "text"))
        if i:
            line.append((" " * gap, "text"))
        if isinstance(cell, list):
            line += padto(cell, width)
            continue
        text, style = cell
        text = "" if text is None else safe_text(str(text))
        if len(text) > width:
            text = text[:max(0, width - 1)] + "…"
        line.append((text.rjust(width) if align == "r" else text.ljust(width), style))
    return line


def table_header(cols):
    return table_row(cols, {c[0]: (c[1], "label") for c in cols})


# ----------------------------------------------------------------------------
# little charts
# ----------------------------------------------------------------------------

BLOCKS = "▁▂▃▄▅▆▇█"
ASCII_BLOCKS = "_.-~=+*#"


def spark(vals, lo=None, hi=None, zero=None, ascii_only=False):
    chars = ASCII_BLOCKS if ascii_only else BLOCKS
    xs = [v for v in vals if v is not None]
    if not xs:
        return " " * len(vals)
    lo = min(xs) if lo is None else lo
    hi = max(xs) if hi is None else hi
    span = (hi - lo) or 1.0
    out = []
    for v in vals:
        if v is None:
            out.append(" ")
        elif zero is not None and v <= 0:
            out.append(zero if not ascii_only else "_")
        else:
            k = int(round((min(max(v, lo), hi) - lo) / span * 7))
            if zero is not None:
                k = max(1, k)
            out.append(chars[k])
    return "".join(out)


def resample(vals, n):
    """Stretch/shrink a list to n entries (nearest, keeps None)."""
    if not vals or n <= 0:
        return []
    m = len(vals)
    return [vals[min(m - 1, int(i * m / n))] for i in range(n)]


_BRAILLE_BITS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))


def braille_plot(series_list, width, height, lo=None, hi=None):
    """Plot one or more series as braille dots.

    series_list: [(values, style), ...] -> list of `height` lines.
    Each cell holds 2x4 dots; later series draw over earlier ones."""
    W, H = width * 2, height * 4
    allv = [v for vals, _ in series_list for v in vals if v is not None]
    if not allv:
        return [[(" " * width, "text")] for _ in range(height)]
    lo = min(allv) if lo is None else lo
    hi = max(allv) if hi is None else hi
    span = (hi - lo) or 1.0
    cells = [[0] * width for _ in range(height)]
    owner = [[None] * width for _ in range(height)]

    def dot(x, y, style):
        if 0 <= x < W and 0 <= y < H:
            cx, cy = x // 2, y // 4
            cells[cy][cx] |= _BRAILLE_BITS[y % 4][x % 2]
            owner[cy][cx] = style

    for vals, style in series_list:
        pts = resample(vals, W)
        prev = None
        for x, v in enumerate(pts):
            if v is None:
                prev = None
                continue
            y = int(round((hi - min(max(v, lo), hi)) / span * (H - 1)))
            if prev is not None:
                y0 = prev
                step = 1 if y > y0 else -1
                for yy in range(y0, y, step):
                    dot(x, yy, style)
            dot(x, y, style)
            prev = y
    out = []
    for r in range(height):
        line = []
        for c in range(width):
            bits = cells[r][c]
            ch = chr(0x2800 + bits) if bits else " "
            st = owner[r][c] or "text"
            if line and line[-1][1] == st:
                line[-1] = (line[-1][0] + ch, st)
            else:
                line.append((ch, st))
        out.append(line)
    return out


def bars(vals, width, height, lo=0.0, hi=None, style="rain", ascii_only=False):
    """Vertical bar chart built from block elements -> `height` lines."""
    pts = resample(vals, width)
    xs = [v for v in pts if v is not None]
    hi = hi if hi is not None else (max(xs) if xs else 1.0)
    span = (hi - lo) or 1.0
    eighths = [int(round((min(max(v, lo), hi) - lo) / span * height * 8)) if v is not None else 0 for v in pts]
    out = []
    full = "#" if ascii_only else "█"
    parts = " .:-=+*#" if ascii_only else " ▁▂▃▄▅▆▇"
    for r in range(height):
        base = (height - 1 - r) * 8
        s = []
        for e in eighths:
            k = e - base
            s.append(full if k >= 8 else parts[k] if k > 0 else " ")
        out.append([("".join(s), style)])
    return out


# ----------------------------------------------------------------------------
# big digits: 5x9 bitmap font drawn with half blocks (5 columns x 5 rows)
# ----------------------------------------------------------------------------

_FONT = {
    "0": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "1": ["..#..", ".##..", "#.#..", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"],
    "2": [".###.", "#...#", "....#", "....#", "...#.", "..#..", ".#...", "#....", "#####"],
    "3": [".###.", "#...#", "....#", "....#", "..##.", "....#", "....#", "#...#", ".###."],
    "4": ["...#.", "..##.", ".#.#.", "#..#.", "#..#.", "#####", "...#.", "...#.", "...#."],
    "5": ["#####", "#....", "#....", "####.", "....#", "....#", "....#", "#...#", ".###."],
    "6": [".###.", "#...#", "#....", "#....", "####.", "#...#", "#...#", "#...#", ".###."],
    "7": ["#####", "....#", "....#", "...#.", "...#.", "..#..", "..#..", "..#..", "..#.."],
    "8": [".###.", "#...#", "#...#", "#...#", ".###.", "#...#", "#...#", "#...#", ".###."],
    "9": [".###.", "#...#", "#...#", "#...#", ".####", "....#", "....#", "#...#", ".###."],
    "-": [".....", ".....", ".....", ".....", "####.", ".....", ".....", ".....", "....."],
    "°": [".#.", "#.#", ".#.", "...", "...", "...", "...", "...", "..."],
    " ": ["..", "..", "..", "..", "..", "..", "..", "..", ".."],
}

_ASCII_GLYPHS = {
    "0": ["###", "# #", "# #", "# #", "###"], "1": [" # ", "## ", " # ", " # ", "###"],
    "2": ["###", "  #", "###", "#  ", "###"], "3": ["###", "  #", " ##", "  #", "###"],
    "4": ["# #", "# #", "###", "  #", "  #"], "5": ["###", "#  ", "###", "  #", "###"],
    "6": ["###", "#  ", "###", "# #", "###"], "7": ["###", "  #", "  #", "  #", "  #"],
    "8": ["###", "# #", "###", "# #", "###"], "9": ["###", "# #", "###", "  #", "###"],
    "-": ["   ", "   ", "###", "   ", "   "], "°": ["o", " ", " ", " ", " "],
    " ": ["  ", "  ", "  ", "  ", "  "],
}


def big_text(text, ascii_only=False, scale=1):
    """Return 5 strings (10 when scale=2) drawing `text` in big digits."""
    if ascii_only:
        rows = [""] * 5
        for ch in text:
            glyph = _ASCII_GLYPHS.get(ch, _ASCII_GLYPHS[" "])
            for i in range(5):
                rows[i] += glyph[i] + " "
        return [r.rstrip() for r in rows]
    bitmap = [""] * 10
    for ch in text:
        glyph = _FONT.get(ch, _FONT[" "]) + ["." * len(_FONT.get(ch, _FONT[" "])[0])]
        for i in range(10):
            bitmap[i] += glyph[i] + "."
    if scale > 1:
        bitmap = ["".join(c * scale for c in row) for row in bitmap for _ in range(scale)]
    rows = []
    for r in range(len(bitmap) // 2):
        top, bot = bitmap[2 * r], bitmap[2 * r + 1]
        s = []
        for a, b in zip(top, bot):
            s.append("█" if a == "#" and b == "#" else "▀" if a == "#" else "▄" if b == "#" else " ")
        rows.append("".join(s).rstrip())
    return rows


# ----------------------------------------------------------------------------
# boxes and panels
# ----------------------------------------------------------------------------

BOX = {"tl": "╭", "tr": "╮", "bl": "╰", "br": "╯", "h": "─", "v": "│"}
BOX_ASCII = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|"}
ROUNDED = {"tl": "╭", "tr": "╮", "bl": "╰", "br": "╯"}
SQUARE = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘"}


def set_rounded(rounded):
    """Settings, Display: Rounded corners - ╭╮ rounded box corners, or ┌┐ square ones for
    fonts without them."""
    BOX.update(ROUNDED if rounded else SQUARE)


def corners(ascii_only=False):
    """A box's corners as drawn now: (top left, top right, bottom left, bottom right)."""
    b = BOX_ASCII if ascii_only else BOX
    return b["tl"], b["tr"], b["bl"], b["br"]


def panel(title, body, width, ascii_only=False, style="box", title_style="title", fill="text"):
    """Draw `body` lines inside a rounded box `width` columns wide."""
    b = BOX_ASCII if ascii_only else BOX
    inner = max(1, width - 2)
    head = [(b["tl"] + b["h"], style)]
    if title:
        t = f" {title} "[:max(0, inner - 2)]
        head.append((t, title_style))
    head.append((b["h"] * max(0, width - 1 - seglen(head)) + b["tr"], style))
    out = [clip(head, width)]
    for ln in body:
        out.append([(b["v"], style)] + padto(ln, inner, fill) + [(b["v"], style)])
    out.append([(b["bl"] + b["h"] * inner + b["br"], style)])
    return out


def on_panel(line, how="onpanel"):
    """A line's spans as drawn inside a card (or on a table's lighter row): each style keeps
    its colors, but what would show the page shows the card's fill. Pictures keep their
    cells and take the fill around them; a span with a background of its own (a chip, a
    selection bar) keeps it."""
    out = []
    for text, st in line:
        if isinstance(st, tuple) and st:
            if st[0] == "@img":
                fill = st[3] if len(st) > 3 else "text"
                out.append((text, st[:3] + ((how, fill),) + st[4:]))
                continue
            if st[0] in ("onpanel", how) or (st[0] == "rgb" and len(st) > 2 and st[2] is not None):
                out.append((text, st))
                continue
        out.append((text, (how, st)))
    return out


def card(title, body, width, note="", ascii_only=False, height=0, style="card", title_style="title"):
    """A box with rounded corners (square ones when Settings says so) `width` columns wide:
    the title set in its top edge with a short note after it when there's room, the body a
    cell in from each side, padded to `height` lines inside. A title style with a
    background of its own (an alert's colour) gets a cell of padding either side."""
    b = BOX_ASCII if ascii_only else BOX
    inner = max(1, width - 4)
    chip = isinstance(title_style, tuple) and len(title_style) > 2 and title_style[2] is not None
    room = width - (6 if chip else 5)            # the title never pushes the corner off the edge
    if title and len(title) > room:
        title = title[:max(0, room - 1)].rstrip() + "…" if room > 1 else ""
    if not title:
        head = [(b["tl"] + b["h"], style)]
    elif chip:
        head = [(b["tl"] + b["h"], style), (f" {title} ", title_style)]
    else:
        head = [(b["tl"] + b["h"] + " ", style), (title, title_style)]
    if note and seglen(head) + 2 + len(note) + 3 <= width:
        head.append(("  " + note, "dim"))
    head.append((" " + b["h"] * max(0, width - seglen(head) - 2) + b["tr"], style))
    out = [clip(head, width)]
    rows = list(body) + [[]] * max(0, height - len(body))
    for ln in rows:
        out.append([(b["v"] + " ", style)] + padto(clip(ln, inner), inner) + [(" " + b["v"], style)])
    out.append([(b["bl"] + b["h"] * max(0, width - 2) + b["br"], style)])
    return [on_panel(ln) for ln in out]


def rule(width, title=None, style="faint", title_style="title"):
    if not title:
        return [("─" * width, style)]
    head = [("── ", style), (title + " ", title_style)]
    head.append(("─" * max(0, width - seglen(head)), style))
    return head
