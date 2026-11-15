"""Charts in text. Column charts in the meters' look (Settings > Display > Meters): every
column a stack of lit segments, LED segments by default, so a forecast reads like a
stereo's level display (Air & UV's readings and forecasts). Area charts: a line drawn to
an eighth of a cell with the area under it filled, brightest along the line (the Now
screen's next 12 hours, the UV index through the day, the month's highs). Bar charts:
solid bars a gap apart (a station's last 24 hours where pictures can't be drawn).

column_chart() returns lines of (text, style) spans: an optional row of values over the
columns, the columns, a base line, and the labels under them, with a scale down the left
side when asked for."""

import math

from .crt import METER, METER_SHADES, meter_style, styled

# a meter's look stood on end: (glyph, coloring prefix) where the terminal draws Unicode's
# legacy computing blocks, and the plainer glyph elsewhere
COLUMN_GLYPHS = {
    # LED segments: a lit band, the cell's lower two thirds, a dark gap above it
    # (BLOCK SEXTANT-3456); plainer, the lower half
    "led": (("\U0001FB39", ""), ("▄", "")),
    # LED dots: one small lamp a cell (BLOCK SEXTANT-3)
    "dots": (("\U0001FB03", ""), ("▖", "")),
    # bricks: a reversed cell, a sliver of gap after and above each one
    "bricks": (("\U0001FB7E", "^"), ("▇", "")),
    # raised blocks, shaded at their right and lower edges; plainer, a shadow under each
    "raised": (("\U0001FB7F", "%"), ("▁", "%")),
    # one solid column, its top drawn to an eighth of a cell
    "solid": (("█", ""), ("█", "")),
    # full blocks, the top three shading off ▓ ▒ ░
    "shade": (("█", ""), ("█", "")),
}
# one-cell columns side by side: the LED band half a cell wide, so neighbors stay apart
NARROW_GLYPHS = {"led": (("\U0001FB13", ""), ("▖", ""))}
EIGHTHS = " ▁▂▃▄▅▆▇█"
GUIDE, GUIDE_ASCII = "┄", "-"


def column_glyph(look=None, narrow=False):
    """(look, glyph, prefix) for a column's lit cells, as this terminal draws them;
    `narrow` for columns a cell wide with no gap between them."""
    look = meter_style(look or METER["style"])
    spec = (NARROW_GLYPHS.get(look) if narrow else None) or COLUMN_GLYPHS[look]
    glyph, prefix = spec[0] if METER["blocks"] else spec[1]
    return look, glyph, prefix


def layout(n, width):
    """(slot, body): the cells each of `n` columns takes, and how many of them are lit,
    the rest a gap after it."""
    slot = max(1, width // max(1, n))
    if slot <= 1:
        return slot, 1
    gap = 1 if slot <= 4 else 2 if slot <= 9 else slot // 4
    return slot, slot - gap


def column_chart(values, width, height, lo=None, hi=None, style="value", tops=None, labels=None,
                 axis=(), guides=(), look=None, gutter=None, title="", now=None, base=True):
    """A column chart `width` cells wide whose columns are `height` rows tall at most.

    values   one number a column (None: no column)
    lo, hi   the scale (default: the values' own); a value at `lo` or below draws nothing,
             anything above it at least one segment
    style    a style, a style for each column, or style(value)
    tops     a text over each column (its value), left out where it would not fit
    labels   a label under each column ("" for none), left out where they would crowd
    axis     [(value, text)] marks for a scale down the left side
    guides   [(value, style)] dotted lines across the chart at those values
    gutter   the cells left of the columns (default: room for `axis` and `title`)
    title    a word in the gutter, at the chart's top
    now      the index of the column that is now, marked under the base line
    base     draw the base line (and the labels under it)
    Returns a list of lines."""
    n = len(values)
    if not n or width < 4 or height < 1:
        return []
    asc = METER["ascii"]
    nums = [x for x in values if x is not None]
    lo = (min(nums) if nums else 0.0) if lo is None else lo
    hi = (max(nums) if nums else 1.0) if hi is None else hi
    span = (hi - lo) or 1.0
    if gutter is None:
        gutter = max([len(text) + 2 for _v, text in axis] + [len(title) + 2 if title else 0])
    plot_w = width - gutter
    slot, body = layout(n, plot_w)
    look, glyph, prefix = column_glyph(look, narrow=slot == 1)
    used = slot * n
    top_row = 1 if tops else 0
    rows = top_row + height
    blank = (" ", "text")
    grid = [[blank] * width for _ in range(rows)]

    def put(r, c, text, sty):
        for k, ch in enumerate(text):
            if 0 <= c + k < width:
                grid[r][c + k] = (ch, sty)

    def free(r, c, length):
        return 0 <= c and c + length <= width and all(grid[r][c + k][0] in " " + guide for k in range(length))

    def level(value):
        return max(0.0, min(1.0, (value - lo) / span)) * height

    def row_of(value):
        """The row a column of this value has its top in."""
        return top_row + height - max(1, min(height, int(round(level(value)))))

    def style_of(i, value):
        if callable(style):
            return style(value)
        if isinstance(style, (list, tuple)) and not (style and style[0] in ("rgb", "rev", "raised")):
            return style[i] if i < len(style) else "value"
        return style

    guide = GUIDE_ASCII if asc else GUIDE
    # dotted guides behind the columns
    for value, gstyle in guides:
        if lo < value <= hi:
            r = row_of(value)
            if grid[r][gutter][0] == " ":         # one guide a row
                for c in range(gutter, gutter + used):
                    put(r, c, guide, gstyle)
    # the columns
    heights = []
    for i, value in enumerate(values):
        x0 = gutter + i * slot
        if value is None or value <= lo:
            heights.append(0)
            continue
        lit = styled(prefix, style_of(i, value))
        h = level(value)
        if look == "solid" and not asc:
            eighths = max(1, int(round(h * 8)))
            full, part = divmod(eighths, 8)
            cells = full + (1 if part else 0)
            for k in range(cells):
                ch = EIGHTHS[part] if (k == full and part) else glyph
                put(top_row + height - 1 - k, x0, ch * body, lit)
        else:
            cells = max(1, int(round(h)))
            tip = METER_SHADES[:max(0, min(3, cells - 1))] if look == "shade" and not asc else ""
            for k in range(cells):
                from_top = cells - 1 - k
                ch = "#" if asc else (tip[len(tip) - 1 - from_top] if from_top < len(tip) else glyph)
                put(top_row + height - 1 - k, x0, ch * body, lit)
        heights.append(cells)
    # the values over the columns, each just above its own column
    for i, text in enumerate(tops or []):
        if not text or i >= n:
            continue
        r = top_row + height - 1 - heights[i]
        if r < 0:
            continue
        x0 = gutter + i * slot
        c = max(gutter, min(x0 + (body - len(text)) // 2, gutter + used - len(text)))
        if free(r, c, len(text)):
            put(r, c, text, style_of(i, values[i]) if values[i] is not None else "dim")
    # the scale down the left side, and the title
    if axis:
        for r in range(top_row, rows):
            put(r, gutter - 1, "|" if asc else "│", "faint")
        for value, text in axis:
            if lo < value <= hi:
                r = row_of(value)
                if grid[r][gutter - 1][0] in "┤+":   # one mark a row: the first given
                    continue
                put(r, gutter - 2 - len(text), text, "dim")
                put(r, gutter - 1, "+" if asc else "┤", "faint")
    if title:
        put(top_row, 0, title[:max(0, gutter - 1)], "label")
    lines = [_spans(row) for row in grid]
    while lines and not lines[0]:           # no value up in the top row
        lines.pop(0)
    if not base:
        return lines
    # the base line, the "now" mark and the labels
    if gutter and axis:
        lead = [(" " * (gutter - 1), "text"), ("+" if asc else "└", "faint")]
    else:
        lead = [(" " * gutter, "text")] if gutter else []
    lines.append(lead + [(("-" if asc else "─") * used, "faint")])
    if labels or now is not None:
        row = [blank] * width
        taken = [False] * width

        def place(c, text, sty):
            c = max(gutter, min(c, width - len(text)))       # at the ends, pulled in to fit
            if c < gutter or any(taken[max(0, c - 1):c + len(text) + 1]):
                return False
            for k, ch in enumerate(text):
                row[c + k] = (ch, sty)
                taken[c + k] = True
            return True

        if now is not None and 0 <= now < n:
            place(gutter + now * slot + (body - 1) // 2, "^" if asc else "▲", "accent")
        for i, text in enumerate(labels or []):
            if text and i < n:
                x0 = gutter + i * slot
                place(x0 + (body - len(text)) // 2 if len(text) <= body else x0, text, "dim")
        lines.append(_spans(row))
    return lines


def bar_chart(values, width, height, lo=None, hi=None, style="value", tops=None, labels=None,
              gutter=0, title="", now=None, base=True, plot=None, axis=(), guides=(), plot_from=None):
    """Solid bars, one a value, each drawn to an eighth of a cell and kept a cell apart
    from the next (a sliver apart, when each has only a cell); `axis` and `guides` as in
    column_chart. `plot`, when given, is a picture of the bars to show in their place: from
    the gutter's end, `height` rows, with values over it only where a bar reaches its top;
    or, with `plot_from` 0, the whole width and the row over the bars for the values too,
    its values, guides and scale its own. The other arguments are column_chart's. Returns
    a list of lines."""
    n = len(values)
    nums = [x for x in values if x is not None]
    if not n or not nums or width < 4 or height < 1:
        return []
    asc = METER["ascii"]
    lo = min(nums) if lo is None else lo
    hi = max(nums) if hi is None else hi
    span = (hi - lo) or 1.0
    if not gutter and axis:
        gutter = max(len(text) for _v, text in axis) + 2
    plot_w = width - gutter
    slot = max(1, plot_w // n)
    # text bars keep a cell between them (a picture's fill their slot, outlined)
    body = slot if plot is not None else max(1, slot - 1)
    top_row = 1 if tops else 0
    rows = top_row + height
    blank = (" ", "text")
    grid = [[blank] * width for _ in range(rows)]
    guide = GUIDE_ASCII if asc else GUIDE
    whole = plot is not None and plot_from == 0     # the picture has the scale and values too

    def put(r, c, text, sty):
        for k, ch in enumerate(text):
            if 0 <= c + k < width:
                grid[r][c + k] = (ch, sty)

    def free(r, c, length):
        return 0 <= c and c + length <= width and all(grid[r][c + k][0] in " " + guide for k in range(length))

    def style_of(i, value):
        if callable(style):
            return style(value)
        if isinstance(style, (list, tuple)) and not (style and style[0] in ("rgb", "rev", "raised", "fade")):
            return style[i] if i < len(style) else "value"
        return style

    def row_of(value):
        return top_row + height - max(1, min(height, int(round(max(0.0, min(1.0, (value - lo) / span)) * height))))

    heights = [0] * n
    for i, value in enumerate(values):
        if value is None or value <= lo:
            continue
        x0 = gutter + i * slot
        frac = max(0.0, min(1.0, (value - lo) / span))
        sty = style_of(i, value)
        if plot is not None:                    # the picture draws it; just where its top is
            heights[i] = max(1, math.ceil(frac * height - 1e-9))
        elif asc:
            heights[i] = max(1, int(round(frac * height)))
            for k in range(heights[i]):
                put(top_row + height - 1 - k, x0, "#" * body, sty)
        else:
            # a cell apart, or (a cell each, no room for that) each the left seven eighths
            # of its cell, so a sliver of the card shows between neighbors
            cell = "▉" if body == slot else "█" * body
            full, part = divmod(max(1, int(round(frac * height * 8))), 8)
            for k in range(full):
                put(top_row + height - 1 - k, x0, cell, sty)
            if part:
                put(top_row + height - 1 - full, x0, EIGHTHS[part] * body, sty)
            heights[i] = full + (1 if part else 0)
    if plot is None:
        # guides across, dotted where there's no bar
        for value, gstyle in guides:
            if value is None or not lo < value < hi:
                continue
            r = row_of(value)
            for c in range(gutter, gutter + slot * n):
                if grid[r][c][0] == " ":
                    put(r, c, guide, gstyle)
    if not whole:
        # each value's text just over its bar (over a picture, only where the bar reaches
        # the picture's top: the text can't go inside it)
        for i, text in enumerate(tops or []):
            if not text or i >= n or values[i] is None:
                continue
            c = max(gutter, min(gutter + i * slot + (body - len(text)) // 2, width - len(text)))
            r = top_row + height - 1 - heights[i]
            if r >= 0 and (plot is None or r == 0) and free(r, c, len(text)):
                put(r, c, text, style_of(i, values[i]))
        if axis:
            for r in range(top_row, rows):
                put(r, gutter - 1, "|" if asc else "│", "faint")
            for value, text in axis:
                if lo < value <= hi:
                    r = row_of(value)
                    if grid[r][gutter - 1][0] in "┤+":
                        continue
                    put(r, gutter - 2 - len(text), text, "dim")
                    put(r, gutter - 1, "+" if asc else "┤", "faint")
        if title:
            put(top_row, 0, title[:max(0, gutter - 1)], "label")
    lines = [_spans(row) for row in grid]
    if whole:
        lines = [list(plot[r]) for r in range(rows)]
    elif plot is not None:
        for r in range(height):
            lead = _spans(grid[top_row + r][:gutter])
            used = sum(len(t) for t, _s in lead)
            lines[top_row + r] = lead + ([(" " * (gutter - used), "text")] if gutter > used else []) + list(plot[r])
    while lines and not lines[0]:
        lines.pop(0)
    if not base:
        return lines
    if gutter and axis:
        lead = [(" " * (gutter - 1), "text"), ("+" if asc else "└", "faint")]
    else:
        lead = [(" " * gutter, "text")] if gutter else []
    lines.append(lead + [(("-" if asc else "─") * (slot * n), "faint")])
    if labels or now is not None:
        row = [blank] * width
        taken = [False] * width

        def place(c, text, sty):
            c = max(gutter, min(c, width - len(text)))
            if any(taken[max(0, c - 1):c + len(text) + 1]):
                return False
            for k, ch in enumerate(text):
                row[c + k] = (ch, sty)
                taken[c + k] = True
            return True

        if now is not None and 0 <= now < n:
            place(gutter + now * slot + (body - 1) // 2, "^" if asc else "▲", "accent")
        for i, text in enumerate(labels or []):
            if text and i < n:
                x0 = gutter + i * slot
                place(x0 + (body - len(text)) // 2 if len(text) <= body else x0, text, "dim")
        lines.append(_spans(row))
    return lines


# an area chart's fill: full strength along its top edge, darker toward the base
FADE = (1.0, 0.8, 0.66, 0.57, 0.5)


def faded(style, k):
    """`style` k of the way from the card's fill to its own color."""
    return style if k >= 0.999 else ("fade", style, round(k, 2))


def area_chart(values, width, height, lo=None, hi=None, style="value", tops=None, labels=None,
               axis=(), guides=(), gutter=None, title="", now=None, base=True, plot=None, plot_from=None):
    """An area chart `width` cells wide and `height` rows tall: a line through the values
    (each at the middle of its share of the width, straight between them) drawn to an
    eighth of a cell, the area under it filled, brightest along the line and darker toward
    the base. The arguments are column_chart's; a style for each value colors the area
    around it, a style(value) colors it by the line's height there; `guides` run across
    the area, over its fill. `plot`, when given, is a picture of the area to show in its
    place (with the row over it for the values' text, when there are values): rows of
    spans from column `plot_from` (the gutter's end, or 0 when the picture draws the scale
    too), its values, guides and scale its own. Returns a list of lines."""
    n = len(values)
    nums = [x for x in values if x is not None]
    if not n or not nums or width < 4 or height < 1:
        return []
    asc = METER["ascii"]
    lo = min(nums) if lo is None else lo
    hi = max(nums) if hi is None else hi
    span = (hi - lo) or 1.0
    if gutter is None:
        gutter = max([len(text) + 2 for _v, text in axis] + [len(title) + 2 if title else 0])
    plot_w = width - gutter
    if plot_w < 2:
        return []
    top_row = 1 if tops else 0
    rows = top_row + height
    blank = (" ", "text")
    grid = [[blank] * width for _ in range(rows)]
    guide = GUIDE_ASCII if asc else GUIDE
    start = gutter if plot_from is None else max(0, min(gutter, plot_from))

    def put(r, c, text, sty):
        for k, ch in enumerate(text):
            if 0 <= c + k < width:
                grid[r][c + k] = (ch, sty)

    def free(r, c, length):
        return 0 <= c and c + length <= width and all(grid[r][c + k][0] in " " + guide for k in range(length))

    def center(i):
        """The cell at the middle of value i's share of the width."""
        return gutter + min(plot_w - 1, int((i + 0.5) * plot_w / n))

    def at(p):
        """The line's value at p (counted in values), straight between its neighbors."""
        p = max(0.0, min(n - 1.0, p))
        i0 = int(p)
        i1 = min(n - 1, i0 + 1)
        a, b = values[i0], values[i1]
        if a is None or b is None:
            return a if p - i0 < 0.5 else b
        return a + (b - a) * (p - i0)

    def style_at(p, value):
        if callable(style):
            return style(value)
        if isinstance(style, (list, tuple)) and not (style and style[0] in ("rgb", "rev", "raised", "fade")):
            i = int(round(max(0.0, min(n - 1.0, p))))
            return style[i] if i < len(style) else "value"
        return style

    def row_of(value):
        return top_row + height - max(1, min(height, int(round(max(0.0, min(1.0, (value - lo) / span)) * height))))

    if plot is None:
        # the area: each cell column sampled at its middle
        edge = {}                               # cell column -> the row its area's top is in
        fill = {}                               # (row, column) -> the style a full cell there has
        for c in range(plot_w):
            p = (c + 0.5) * n / plot_w - 0.5
            value = at(p)
            x = gutter + c
            if value is None or value <= lo:
                continue
            sty = style_at(p, value)
            if asc:
                cells = max(1, int(round(max(0.0, min(1.0, (value - lo) / span)) * height)))
                for k in range(cells):
                    put(top_row + height - 1 - k, x, "#" if k < cells - 1 else ":", sty)
                edge[x] = top_row + height - cells
                continue
            eighths = max(1, int(round(max(0.0, min(1.0, (value - lo) / span)) * height * 8)))
            full, part = divmod(eighths, 8)
            cells = full + (1 if part else 0)
            for k in range(cells):              # k = 0 at the base
                r = top_row + height - 1 - k
                look = faded(sty, FADE[min(cells - 1 - k, len(FADE) - 1)])
                if part and k == full:
                    put(r, x, EIGHTHS[part], look)
                else:
                    put(r, x, "█", look)
                    fill[(r, x)] = look
            edge[x] = top_row + height - cells
        # guides across the chart: over the area's full cells, dotted where there is none
        for value, gstyle in guides:
            if value is None or not lo < value <= hi:
                continue
            r = row_of(value)
            for c in range(gutter, gutter + plot_w):
                if (r, c) in fill:
                    put(r, c, guide, ("over", gstyle, fill[(r, c)]))
                elif grid[r][c][0] == " ":
                    put(r, c, guide, gstyle)
        # each value's text just over the line where it is
        for i, text in enumerate(tops or []):
            if not text or i >= n or values[i] is None:
                continue
            c = max(gutter, min(center(i) - (len(text) - 1) // 2, gutter + plot_w - len(text)))
            r = min(edge.get(c + k, top_row + height) for k in range(len(text))) - 1
            if r >= 0 and free(r, c, len(text)):
                put(r, c, text, style_at(i, values[i]))
    # the scale down the left side, and the title (unless the picture has the gutter too)
    if axis and start > 0:
        for r in range(top_row, rows):
            put(r, gutter - 1, "|" if asc else "│", "faint")
        for value, text in axis:
            if lo < value <= hi:
                r = row_of(value)
                if grid[r][gutter - 1][0] in "┤+":
                    continue
                put(r, gutter - 2 - len(text), text, "dim")
                put(r, gutter - 1, "+" if asc else "┤", "faint")
    if title and start > 0:
        put(top_row, 0, title[:max(0, gutter - 1)], "label")
    if plot is None:
        lines = [_spans(row) for row in grid]
        while lines and not lines[0]:
            lines.pop(0)
    else:
        lines = []
        for r in range(rows):
            lead = _spans(grid[r][:start])
            used = sum(len(t) for t, _s in lead)
            lines.append(lead + ([(" " * (start - used), "text")] if start > used else []) + list(plot[r]))
    if not base:
        return lines
    if gutter and axis:
        lead = [(" " * (gutter - 1), "text"), ("+" if asc else "└", "faint")]
    else:
        lead = [(" " * gutter, "text")] if gutter else []
    lines.append(lead + [(("-" if asc else "─") * plot_w, "faint")])
    if labels or now is not None:
        row = [blank] * width
        taken = [False] * width

        def place(c, text, sty):
            c = max(gutter, min(c, width - len(text)))
            if any(taken[max(0, c - 1):c + len(text) + 1]):
                return False
            for k, ch in enumerate(text):
                row[c + k] = (ch, sty)
                taken[c + k] = True
            return True

        if now is not None and 0 <= now < n:
            place(center(now), "^" if asc else "▲", "accent")
        for i, text in enumerate(labels or []):
            if text and i < n:
                place(center(i) - (len(text) - 1) // 2, text, "dim")
        lines.append(_spans(row))
    return lines


def _spans(cells):
    """A row of (char, style) cells as spans, runs of one style together."""
    out = []
    for ch, sty in cells:
        if out and out[-1][1] == sty:
            out[-1] = (out[-1][0] + ch, sty)
        else:
            out.append((ch, sty))
    while out and out[-1][1] == "text" and not out[-1][0].strip():
        out.pop()
    return out


def every(n, width, want=5):
    """How often to label `n` columns `width` cells wide, so labels of `want` cells don't crowd."""
    slot = max(1, width // max(1, n))
    k = 1
    while slot * k < want + 1:
        k += 1
    return k
