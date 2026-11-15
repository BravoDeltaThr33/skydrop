"""Shared screen pieces: banners, panels, icons, mini radar, sidebars."""

import datetime as dt
import time

from . import charts, gfx, icons, nexrad, palettes
from .radar import MAP_ROLES, View as RadarView
from .radar import summary_lines, LEGEND, color_dbz
from .store import SOURCE_LABELS
from .text import big_text, card, clip, on_panel, padto, seglen, wrap
from .theme import blend, pop_style, temp_style
from .wxmath import aqi_cat, moon_info, moon_name, sun_day


_FAMILY = {"sel": "choice", "opt": "choice", "on": "switch", "off": "switch", "act": "keys", "val": "keys",
           "lbl": "keys", "dis": "keys"}


def control(label, kind, ascii_only=False):
    """One control as [(text, style)]: see buttons()."""
    if kind == "sel":
        return [(f" {label} ", "btnsel")]
    if kind == "opt":
        return [(f" {label} ", "btnopt")]
    if kind == "on":
        return [("[x]" if ascii_only else "■", "tgonmk"), (f" {label}", "tgon")]
    if kind == "off":
        return [("[ ]" if ascii_only else "□", "tgoffmk"), (f" {label}", "tgoff")]
    if kind == "act":
        return [(f" {label} ", "btnact")]
    if kind == "val":
        return [(str(label), "btnval")]
    if kind == "lbl":
        return [(str(label), "btnlbl")]
    return [(f" {label} ", "btndis")]


def buttons(items, width, indent=2, gap=1, ascii_only=False, grid=0):
    """Clickable controls laid out in rows that wrap at width.

    items: [(label, kind, action)], kind being
      'sel' / 'opt'  one choice of several: the chosen one is filled, the rest are quiet chips
      'on' / 'off'   a switch, drawn as a check box (■ on, □ off) with no fill
      'act'          a one-shot key (◀ ▶ + −), drawn as a grey keycap
      'val' / 'lbl'  a value (bold) or a caption (dim) between keys
      'dis'          unavailable
    Neighbours of the same kind sit one space apart, different kinds two, so each group
    reads as a unit. grid=N lines the items up in N columns (the layer switches).
    Returns (lines, hits); hits are (row, x0, x1, action) in the lines."""
    lines, hits = [], []
    if grid:   # chips in a grid share one width, so the columns line up like a keypad
        chipw = max((len(str(lab)) for lab, kind, _a in items if kind in ("sel", "opt", "act")), default=0)
        items = [(str(lab).center(chipw) if kind in ("sel", "opt", "act") else lab, kind, a) for lab, kind, a in items]
    cells = [(control(label, kind, ascii_only), kind, action) for label, kind, action in items]
    if grid:
        chips = all(kind in ("sel", "opt", "act") for _s, kind, _a in cells)
        colw = max(seglen(c[0]) for c in cells) + (1 if chips else 2) if cells else 1
        cols = max(1, min(grid, (width - indent + 2) // colw))
        for i in range(0, len(cells), cols):
            row, x = [(" " * indent, "text")], indent
            for segs, kind, action in cells[i:i + cols]:
                w = seglen(segs)
                if action and kind not in ("lbl", "dis"):
                    hits.append((len(lines), x, x + w, action))
                row += segs + [(" " * (colw - w), "text")]
                x += colw
            lines.append(row)
        return lines, hits
    def spacing(prev, kind):
        if prev is None:
            return 0
        if kind == "lbl" and prev != "lbl":
            return gap + 2                 # a caption starts a new little group: give it room
        return gap if _FAMILY.get(kind) == _FAMILY.get(prev) else gap + 1

    # a caption and the keys after it (Opacity − 90% +) wrap together, as one unit
    units = []
    for cell in cells:
        if units and cell[1] in ("act", "val") and units[-1][0][1] == "lbl":
            units[-1].append(cell)
        else:
            units.append([cell])
    row, x, prev = [], indent, None
    for unit in units:
        need = spacing(prev, unit[0][1]) + sum(seglen(c[0]) for c in unit) + \
            sum(spacing(a[1], b[1]) for a, b in zip(unit, unit[1:]))
        if row and x + need > width:
            lines.append([(" " * indent, "text")] + row)
            row, x, prev = [], indent, None
        for segs, kind, action in unit:
            g = spacing(prev, kind)
            if g:
                row.append((" " * g, "text"))
                x += g
            w = seglen(segs)
            row += segs
            if action and kind not in ("lbl", "dis"):
                hits.append((len(lines), x, x + w, action))
            x += w
            prev = kind
    if row:
        lines.append([(" " * indent, "text")] + row)
    return lines, hits


def columns(blocks, ncols, col_w, gap=3, ascii_only=False):
    """Blocks (lists of lines) in ncols columns `gap` cells apart, in order, balanced so the
    tallest column is as short as possible. Returns (lines, where) with where[i] = (column,
    first row) of block i."""
    ncols = max(1, min(ncols, len(blocks)))
    h = [len(b) + 1 for b in blocks]
    n = len(blocks)
    best = None
    if ncols == 1:
        cuts = ()
    else:
        import itertools
        for cuts in itertools.combinations(range(1, n), ncols - 1):
            edges = (0,) + cuts + (n,)
            tallest = max(sum(h[edges[i]:edges[i + 1]]) for i in range(ncols))
            if best is None or tallest < best[0]:
                best = (tallest, cuts)
        cuts = best[1]
    edges = (0,) + tuple(cuts) + (n,)
    cols, where = [], {}
    for c in range(len(edges) - 1):
        rows = []
        for i in range(edges[c], edges[c + 1]):
            if rows:
                rows.append([])
            where[i] = (c, len(rows))
            rows += blocks[i]
        cols.append(rows)
    out = []
    for r in range(max((len(c) for c in cols), default=0)):
        row = []
        for k, col in enumerate(cols):
            seg = col[r] if r < len(col) else []
            row += (padto(seg, col_w) + [(" " * gap, "text")]) if k < len(cols) - 1 else seg
        out.append(row)
    return out, where


def card_grid(rows, width, ascii_only=False, gap=1):
    """Rows of cards side by side across `width`, each row as tall as its tallest card.
    rows: lists of cards, each a dict (title, note, build, span) or None for an empty
    place; build(inner width) gives the card's lines. A row's places share the width by
    their spans."""
    out = []
    for row in rows:
        if not any(row):
            continue
        spans = [(c or {}).get("span", 1) for c in row]
        unit = (width - gap * (sum(spans) - 1)) / sum(spans)
        widths, x = [], 0.0
        for sp in spans:
            w = int(round(x + unit * sp + gap * (sp - 1))) - int(round(x))
            widths.append(w)
            x += unit * sp + gap * sp
        widths[-1] = width - sum(widths[:-1]) - gap * (len(widths) - 1)
        bodies = [c["build"](w - 4) if c else None for c, w in zip(row, widths)]
        h = max(len(b) for b in bodies if b is not None)
        boxes = [card(c["title"], b, w, c.get("note", ""), ascii_only, h, title_style=c.get("title_style", "title"))
                 if c else [[]] * (h + 2) for c, b, w in zip(row, bodies, widths)]
        for r in range(h + 2):
            line = []
            for k, (box, w) in enumerate(zip(boxes, widths)):
                if k:
                    line.append((" " * gap, "text"))
                line += padto(box[r], w)
            out.append(line)
    return out


def flow_columns(W, min_col=52, max_cols=3, gap=3):
    """How many newspaper columns fit in W, and how wide each is."""
    n = max(1, min(max_cols, (W + gap) // (min_col + gap)))
    return n, (W - gap * (n - 1)) // n


def fits(items, width, indent=2, gap=1):
    """Would these controls fit on one row?"""
    total, prev = indent, None
    for label, kind, _a in items:
        total += sum(len(t) for t, _s in control(label, kind)) + (0 if prev is None else gap)
        prev = kind
    return total <= width


def sidebar_split(W, want=48, min_main=72, min_side=34):
    """Split a body width into (main, side) when there's room for a sidebar."""
    if W < min_main + min_side + 1:
        return W, 0
    side = max(min_side, min(want, (W - 1) // 3 + 4))
    if W - side - 1 < min_main:
        side = W - 1 - min_main
    return W - side - 1, side


class Head(list):
    """A section's heading: the top edge of a card once cardify draws it (plain text, the
    title and a note, anywhere else)."""

    def __init__(self, title, note="", style="title", indent=None):
        super().__init__([(title, style)] + ([("  " + note, "dim")] if note else []))
        self.title, self.note, self.style, self.indent = title, note, style, indent


def section(title, width=0, note="", style="title", indent=None):
    """The heading of a section: the lines after it, up to the next heading, go in a card
    under this title (see cardify). `indent`: how many leading cells its lines lose, when
    not cardify's usual (0 keeps a table's selection marker column)."""
    return Head(title, note, style, indent)


def zebra(line, width, n):
    """A table's row `n`: every other one a touch lighter, the stripe the card's width."""
    return on_panel(padto(line, width), "stripe") if n % 2 else line


def dedent(line, n):
    """A line with up to n of its leading spaces taken off (a card's edge indents it). A
    run of nothing but spaces is an indent: what follows it (a picture, a chart) keeps its
    own leading cells."""
    out = list(line)
    while n > 0 and out:
        t, st = out[0]
        if isinstance(st, tuple) and st and st[0] == "@img":
            break
        k = len(t) - len(t.lstrip(" "))
        if not k:
            break
        cut = min(k, n)
        n -= cut
        out[0:1] = [(t[cut:], st)] if cut < len(t) else []
        if cut == len(t):
            break
    return out


def cardify(lines, width, ascii_only=False, indent=2):
    """Lines with section headings (Head) in them as rounded cards `width` wide, a card a
    section; lines before the first heading stay as they are. A section's lines lose up to
    `indent` leading spaces (the card's edge sets them in now) and the blank lines at its
    ends. Returns (lines, where): where[i] is the row old line i went to."""
    out, where = [], [0] * len(lines)
    i, n = 0, len(lines)
    while i < n and not isinstance(lines[i], Head):
        where[i] = len(out)
        out.append(lines[i])
        i += 1
    while i < n:
        head = lines[i]
        j = i + 1
        while j < n and not isinstance(lines[j], Head):
            j += 1
        body = list(range(i + 1, j))
        while body and not lines[body[0]]:
            body.pop(0)
        while body and not lines[body[-1]]:
            body.pop()
        top = len(out)
        cut = indent if head.indent is None else head.indent
        out += card(head.title, [dedent(lines[k], cut) for k in body], width, head.note, ascii_only,
                    title_style=head.style)
        for k in range(i, j):
            where[k] = top
        for r, k in enumerate(body):
            where[k] = top + 1 + r
        i = j
    return out, where


def recard(out, width, ascii_only=False, indent=2):
    """A screen's main lines as cards (cardify), its cursor, row map and frozen rows moved
    to where their lines went."""
    lines = out.get("lines") or []
    if not any(isinstance(ln, Head) for ln in lines):
        return out
    new, where = cardify(lines, width, ascii_only, indent)
    out["lines"] = new
    if out.get("cursor") is not None and out["cursor"] < len(where):
        out["cursor"] = where[out["cursor"]]
    if out.get("rowmap"):
        out["rowmap"] = {where[k]: val for k, val in out["rowmap"].items() if k < len(where)}
    if out.get("sticky"):
        k = out["sticky"]
        out["sticky"] = (where[k - 1] + 1) if 0 < k <= len(where) else k
    return out


def time_labels(times, F, slot, now=None, room=10):
    """Labels for hourly columns `slot` cells apart: round hours (every 1, 2, 3, 6 or 12)
    far enough apart to read, "3 PM" where there's room, else "3p"; the column holding
    `now` says "Now"."""
    step = next((k for k in (1, 2, 3, 6, 12) if slot * k >= room), 12)
    name = F.hour if slot * step >= 7 else F.hour_short
    out = []
    for t in times:
        if now is not None and t <= now < t + 3600:
            out.append("Now")
        else:
            out.append(name(t) if F.local(t).hour % step == 0 else "")
    if "Now" in out:                       # an hour crowding "Now" gives way to it
        at = out.index("Now")
        for j, label in enumerate(out):
            if label and j != at and abs(j - at) * slot < len(label) + 2:
                out[j] = ""
    return out


def kv(label, value, width, lw=16, style="value"):
    segs = value if isinstance(value, list) else [(str(value), style)]
    return clip([("  " + label.ljust(lw), "label")] + segs, width)


def error_lines(m, keys, width):
    out = []
    for k in keys:
        e = (m.get("errors") or {}).get(k)
        if e:
            out += wrap(f"{SOURCE_LABELS.get(k, k)}: {e}", width, "warn", 2)
    return out


def off_note(m, keys):
    names = sorted({(m.get("off") or {}).get(k) for k in keys if (m.get("off") or {}).get(k)})
    return ", ".join(names)


def today_row(m, F):
    today = F.today()
    for d in m.get("daily") or []:
        if d["date"] == today:
            return d
    return {}


def sun_pair(m, F, day=None):
    day = day or F.today()
    astro = m.get("astro") or {}
    if day == F.today() and astro.get("sunrise") and astro.get("sunset"):
        return astro["sunrise"], astro["sunset"], "NWS"
    for d in m.get("daily") or []:
        if d["date"] == day and d.get("sunrise") and d.get("sunset"):
            return d["sunrise"], d["sunset"], d.get("src") or ""
    loc = m.get("loc") or {}
    if loc.get("lat") is None:
        return None, None, ""
    s = sun_day(day, loc["lat"], loc["lon"], F.tz)
    return s["sun"][0], s["sun"][1], "calculated"


# ----------------------------------------------------------------------------
# pictures
# ----------------------------------------------------------------------------

def icon_block(v, kind, is_day, cols=13, rows=5, slot="icon", big=False, bg_role="bg"):
    """A weather icon: pixels on kitty/sixel, line art (or half-block pixels when big) elsewhere."""
    kind = kind or "unknown"
    day = True if is_day is None else bool(is_day)
    bg = v.theme.rgb(bg_role)
    # on a light page icons get a dark rim, like the magnets on a 1970s weather board
    outline = v.theme.rgb("ink") if v.theme.light else None

    def build(mode, w, h):
        if mode == "pixels" and gfx.HAVE_PIL:
            return icons.pixel_icon(kind, day, w, h, bg, outline=outline)
        if mode == "blocks" and big and gfx.HAVE_PIL:
            return icons.pixel_icon(kind, day, w, h * 2, bg, outline=outline)
        art = icons.art(kind, day, ascii_only=(mode == "ascii"))
        if w > 13 or h > 5:
            padl = (w - 13) // 2
            top = (h - 5) // 2
            out = [[(" " * w, "text")] for _ in range(top)]
            out += [[(" " * padl, "text")] + ln for ln in art]
            out += [[(" " * w, "text")] for _ in range(h - len(out))]
            return out[:h]
        return art

    return v.images.block(slot, f"icon:{kind}:{day}:{v.theme.name}:{big}:{bg_role}:{bg}", cols, rows, build, back=bg)


def big_temp(v, temp_text):
    return big_text(temp_text, ascii_only=v.ascii)


def big_temp_block(v, temp_text, style, rows=5, slot="nowtemp", bg_role="card_bg"):
    """The big temperature as a picture (bold numerals shaded in its color, a soft shadow
    under them) where the terminal draws pictures: (lines, width in cells). None elsewhere,
    for the big block digits instead."""
    images = getattr(v, "images", None)
    if (images is None or not getattr(images, "pixels", False) or not gfx.HAVE_PIL or v.ascii
            or getattr(v, "printing", False)):
        return None
    bg = v.theme.rgb(bg_role)
    rgb = v.theme.spec(style)[0]
    light = bool(v.theme.light)
    cols = icons.number_cols(temp_text, rows) + 1

    def build(mode, w, h):
        return icons.number_image(temp_text, rgb, w, h, bg, light=light) if mode == "pixels" else None
    return (images.block(f"{slot}{cols}x{rows}", f"num:{temp_text}:{rgb}:{bg}:{light}", cols, rows, build, back=bg),
            cols)


def moon_block(v, frac, cols, rows, slot="moon", bg_role="bg"):
    bg = v.theme.rgb(bg_role)
    name = moon_name(frac)

    def build(mode, w, h):
        if mode == "pixels" and gfx.HAVE_PIL:
            return charts.moon_image(frac, w, h, bg, theme=v.theme)
        if mode == "blocks" and gfx.HAVE_PIL:
            return charts.moon_image(frac, w, h * 2, bg, theme=v.theme)
        art = charts.ASCII_MOON.get(name, charts.ASCII_MOON["Full moon"])
        out = [[(ln.center(w)[:w], "t_mild")] for ln in art[:h]]
        while len(out) < h:
            out.append([(" " * w, "text")])
        return out

    return v.images.block(slot, f"moon:{round(frac, 3)}:{v.theme.name}:{bg_role}:{bg}", cols, rows, build, back=bg)


# ----------------------------------------------------------------------------
# radar pictures (shared by the radar screen, mini radar and Local on the 8s)
# ----------------------------------------------------------------------------

def radar_source(v, m):
    """'site' (one NEXRAD) or 'mosaic' (national composite) for the radar map."""
    st, cfg = v.radar_state, v.cfg
    src = st.get("source") or cfg.get("radar_source") or "site"
    loc = m.get("loc") or {}
    if src == "site" and (loc.get("country") or "US") not in ("US", "PR", "GU", "VI", "AS", "MP"):
        return "mosaic"
    return "site" if src == "site" else "mosaic"


def radar_site(v, m, lat, lon):
    """The radar to show: the one chosen, else the nearest to the map centre (with a little
    stickiness so a small pan doesn't flip between two radars), else the NWS point's radar."""
    st, cfg = v.radar_state, v.cfg
    chosen = st.get("site") or str(cfg.get("radar_site") or "auto").strip().upper()
    if chosen and chosen != "AUTO":
        return chosen
    pts = m.get("pts") or {}
    mgr = v.radar
    sites = mgr.sites(block=False) if mgr is not None else []
    if not sites:
        return (pts.get("radarStation") or "").upper() or None
    near = nexrad.nearest_sites(sites, lat, lon, n=1)
    if not near:
        return (pts.get("radarStation") or "").upper() or None
    best = near[0]
    cur = st.get("auto_site")
    if cur and cur != best["id"]:
        info = next((x for x in sites if x["id"] == cur), None)
        if info is not None:
            from .util import distance_mi
            d = distance_mi(lat, lon, info["lat"], info["lon"])
            if d is not None and d <= best["dist"] * 1.15 + 5:
                return cur
    st["auto_site"] = best["id"]
    return best["id"]


def radar_view(v, m, cols, rows, span=None, mini=False, lat=None, lon=None, product=None):
    loc = m.get("loc") or {}
    if loc.get("lat") is None:
        return None
    st = v.radar_state
    span = span or st.get("span") or 120
    lat = lat if lat is not None else st.get("lat") if st.get("lat") is not None and not mini else loc["lat"]
    lon = lon if lon is not None else st.get("lon") if st.get("lon") is not None and not mini else loc["lon"]
    cfg = v.cfg
    if v.gmode in ("kitty", "sixel"):
        cw, ch = v.cell
        w, h, ys, mode = cols * cw, rows * ch, 1.0, "pixels"
        cap = 1600 if not mini else 700
        if w > cap:
            f = cap / w
            w, h = int(w * f), int(h * f)
    elif v.gmode == "blocks":
        w, h, ys, mode = cols, rows * 2, 1.0, "blocks"
    else:
        w, h, ys, mode = cols, rows, 2.0, "ascii"
    if mode == "pixels":
        ys = (rows * v.cell[1] / h) / (cols * v.cell[0] / w) if h and w else 1.0
    source = radar_source(v, m)
    site = radar_site(v, m, lat, lon) if source == "site" else None
    prod = product or st.get("product") or cfg.get("radar_product") or "ref"
    if prod not in nexrad.PRODUCT_BY_KEY:
        prod = "ref"
    tilt = st.get("tilt", 0) if not mini else 0
    from .config import intval, on
    return RadarView(lat, lon, span, w, h, ys, mode, layer=st.get("layer") or cfg.get("radar_layer") or "reflectivity",
                     basemap=cfg.get("radar_basemap") or "classic",
                     frames=1 if mini else intval(cfg, "radar_frames", 1, 30),
                     step=intval(cfg, "radar_step", 2, 60),
                     labels=st.get("labels", True), stations=st.get("temps", True),
                     warnings=st.get("warnings", True), mini=mini, imperial=v.fmt.imp,
                     source=source, site=site, product=prod if not mini else "ref", tilt=tilt,
                     smooth=st.get("smooth", on(cfg, "radar_smooth")), cells=st.get("cells", on(cfg, "radar_cells")),
                     min_dbz=intval(cfg, "radar_min_dbz", -30, 40), opacity=intval(cfg, "radar_opacity", 30, 100),
                     home=(loc["lat"], loc["lon"]),
                     layers=() if mini else tuple(k for k in ("range", "sites", "markers", "placefiles") if st.get(k)),
                     mapcolors=tuple(v.theme.rgb(r) for r in MAP_ROLES))


def _cell_bg(style):
    if isinstance(style, tuple) and style and style[0] == "rgb":
        return style[2] if style[2] is not None else style[1]
    return None


def _stamp(grid, row, col, text, fg, bold=True, bg=None):
    if not (0 <= row < len(grid)):
        return
    line = grid[row]
    for k, ch in enumerate(text):
        c = col + k
        if 0 <= c < len(line):
            old = line[c][1]
            cb = bg or _cell_bg(old) or (11, 22, 92)
            # darken the background a touch so labels stay readable over radar colors
            cb = tuple(int(x * 0.55) for x in cb)
            line[c] = (ch, ("rgb", fg, cb, "b" if bold else ""))


def _lines_to_grid(lines, cols):
    grid = []
    for ln in lines:
        row = []
        for text, style in ln:
            for ch in text:
                row.append((ch, style))
        row = (row + [(" ", "text")] * cols)[:cols]
        grid.append(row)
    return grid


def _grid_to_lines(grid):
    out = []
    for row in grid:
        line = []
        for ch, st in row:
            if line and line[-1][1] == st:
                line[-1] = (line[-1][0] + ch, st)
            else:
                line.append((ch, st))
        out.append(line)
    return out


def radar_text(res, frame_img, cols, rows, mode, show_labels=True, inspect=None):
    """Blocks/ASCII rendering of a radar frame with text labels on top."""
    view = res.view
    if mode == "blocks":
        lines = gfx.halfblock_lines(frame_img, cols, rows)
        grid = _lines_to_grid(lines, cols)
        ydiv = 2.0
    else:
        img = frame_img.convert("RGB").resize((cols, rows), gfx.NEAREST)
        px = img.load()
        base = getattr(res, "base", None)
        bpx = base.convert("RGB").resize((cols, rows), gfx.NEAREST).load() if base is not None else None
        grid = []
        ramp = " .:-=+*#%@"
        # the same dark map as the picture modes, so echoes read the same on every theme
        top, bot, _w, c_county, _r, c_state = view.mapcolors[:6]
        for y in range(rows):
            row = []
            mbg = blend(top, bot, y / max(1, rows - 1))
            for x in range(cols):
                r, g, b = px[x, y]
                lum = (r * 3 + g * 6 + b) / 10
                sat = max(r, g, b) - min(r, g, b)
                if bpx is not None:
                    br, bgr, bb = bpx[x, y]
                    echo = abs(r - br) + abs(g - bgr) + abs(b - bb) > 60 and sat > 60
                else:
                    echo = sat > 90 and not (b > r and b > g and b - max(r, g) > 60 and lum < 120)
                if echo:
                    dbz = color_dbz((r, g, b))
                    ch = ramp[min(9, max(1, int(dbz / 8)))]
                    row.append((ch, ("rgb", (r, g, b), mbg, "b")))
                elif lum > 150:
                    row.append((":", ("rgb", c_state, mbg, "")))
                elif lum > 70:
                    row.append((".", ("rgb", c_county, mbg, "")))
                else:
                    row.append((" ", ("rgb", c_state, mbg, "")))
            grid.append(row)
        ydiv = 1.0
    if show_labels:
        for name, px_, py_, anchor in res.labels:
            r, c = int(py_ / ydiv), int(px_ / (view.w / cols))
            label = name
            if anchor == "l":
                _stamp(grid, r, c, "•" if mode == "blocks" else "*", (255, 255, 255))
                _stamp(grid, r, c + 1, label, (255, 255, 255))
            elif anchor == "r":
                _stamp(grid, r, c - len(label), label, (255, 255, 255))
                _stamp(grid, r, c, "•" if mode == "blocks" else "*", (255, 255, 255))
            elif anchor == "t":
                _stamp(grid, r, c, "•" if mode == "blocks" else "*", (255, 255, 255))
                _stamp(grid, r - 1, c - len(label) // 2, label, (255, 255, 255))
            else:
                _stamp(grid, r, c, "•" if mode == "blocks" else "*", (255, 255, 255))
                _stamp(grid, r + 1, c - len(label) // 2, label, (255, 255, 255))
        for px_, py_, tc, _sid in res.temps:
            r, c = int(py_ / ydiv), int(px_ / (view.w / cols))
            col = (255, 236, 120) if tc >= 22.2 else (150, 220, 255) if tc < 4.4 else (255, 255, 255)
            _stamp(grid, r, c - 1, view.temp_text(tc), col)
    for px_, py_, ch, rgb, label in res.marks:
        r, c = int(py_ / ydiv), int(px_ / (view.w / cols))
        if mode != "blocks":
            ch = {"▲": "^", "▼": "V", "◎": "O"}.get(ch, ch)
        _stamp(grid, r, c, ch, rgb)
        if label and len(label) <= 3:
            _stamp(grid, r, c + 1, label, rgb, bold=False)
    mx, my = res.marker
    _stamp(grid, int(my / ydiv), int(mx / (view.w / cols)), "◆" if mode == "blocks" else "@", (255, 150, 20))
    if inspect is not None:
        ix, iy = view.to_px(*inspect)
        _stamp(grid, int(iy / ydiv), int(ix / (view.w / cols)), "✛" if mode == "blocks" else "+", (255, 255, 255))
    return _grid_to_lines(grid)


def radar_block(v, m, cols, rows, slot="radar", mini=False, frame=None, span=None, product=None, inspect=None,
                at_time=None):
    """Picture lines for a radar map, plus the Result (or None while loading).

    frame picks the loop frame by index; at_time picks the frame closest to that time
    (so extra panes stay in step with the main one)."""
    view = radar_view(v, m, cols, rows, span=span, mini=mini, product=product)
    if view is None or v.radar is None:
        out = [[(" " * cols, "text")] for _ in range(rows)]
        if v.radar is None and rows:
            msg = "The radar map needs Pillow (the installer adds it)." if not gfx.HAVE_PIL else ""
            out[rows // 2] = [(msg[:cols].center(cols), "dim")]
        return out, None
    res = v.radar.get(view, m)
    if res is None or not res.frames:
        msg = v.radar.status or "Loading radar…"
        if v.radar.last_error and not v.radar.busy():
            msg = "Radar unavailable: " + v.radar.last_error
        out = [[(" " * cols, "text")] for _ in range(rows)]
        mid = rows // 2
        out[mid] = [(msg[:cols].center(cols), "dim")]
        return out, None
    if at_time is not None:
        idx = min(range(len(res.frames)), key=lambda k: (abs(res.frames[k][0] - at_time), -k))
    else:
        idx = len(res.frames) - 1 if frame is None else max(0, min(len(res.frames) - 1, frame))
    res.shown = idx
    t, img = res.frames[idx]
    # keyed by what's in the picture, so a rebuild that changed nothing reuses the drawn image
    ikey = "" if inspect is None else f":{inspect[0]:.4f},{inspect[1]:.4f}"
    key = f"radar:{slot}:{res.sig}:{int(t)}:{cols}x{rows}{ikey}"
    show_labels = view.labels
    old = getattr(v, "last_radar", None)
    if slot == "radar" or old is None or old.view.mini or time.time() - old.built_at > 600:
        v.last_radar = res  # the nowcast that speech and the ticker describe

    def build(mode, w, h):
        if mode == "pixels":
            if inspect is None:
                return img
            pic = img.copy()
            d = gfx.ImageDraw.Draw(pic)
            x, y = view.to_px(*inspect)
            for wd, col in ((5, (0, 0, 0)), (2, (255, 255, 255))):
                d.line([(x - 13, y), (x - 4, y)], fill=col, width=wd)
                d.line([(x + 4, y), (x + 13, y)], fill=col, width=wd)
                d.line([(x, y - 13), (x, y - 4)], fill=col, width=wd)
                d.line([(x, y + 4), (x, y + 13)], fill=col, width=wd)
            return pic
        return radar_text(res, img, w, h, mode, show_labels, inspect)

    return v.images.block(slot, key, cols, rows, build), res


def radar_legend(width, ascii_only=False, res=None, imperial=True):
    """One colour-bar line for the product on the map (or the mosaic's dBZ scale)."""
    table = getattr(res, "table", None) if res is not None else None
    prod = getattr(res, "product", None) if res is not None else None
    if table is None or prod is None:
        if ascii_only:
            return [[("  light ", "dim"), (".:-=+*#%@", "accent"), (" intense", "dim")]]
        segs = [("  light ", "dim")]
        for _dbz, rgb in LEGEND:
            segs.append(("██", ("rgb", rgb, None, "")))
        segs.append((" intense", "dim"))
        return [clip(segs, width)]
    if prod.kind == "class":
        segs = [("  ", "dim")]
        for v_, rgba in palettes.legend_stops(table, "class"):
            abbr = nexrad.HC_CLASSES.get(int(v_), ("?", ""))[0]
            if abbr in ("ND", "RF"):
                continue
            segs += [("█" if not ascii_only else "#", ("rgb", rgba[:3], None, "")), (abbr + " ", "dim")]
        return [clip(segs, width)]
    stops = palettes.legend_stops(table, "value", count=max(8, min(40, width - 24)))
    if not stops:
        return [[]]
    unit = (table.units or "").strip()
    lo, hi = stops[0][0], stops[-1][0]
    segs = [(f"  {_fmt_tick(lo)} ", "dim")]
    for _v, rgba in stops:
        segs.append(("█" if not ascii_only else "#", ("rgb", rgba[:3], None, "")))
    segs.append((f" {_fmt_tick(hi)} {unit}", "dim"))
    return [clip(segs, width)]


def _fmt_tick(v):
    if abs(v) >= 10 or v == int(v):
        return f"{v:.0f}"
    return f"{v:.2f}".rstrip("0").rstrip(".")


def mini_radar_panel(v, m, width, rows):
    lines, res = radar_block(v, m, width - 2, rows, slot="miniradar", mini=True,
                             span=min(120, int(v.cfg.get("radar_span") or 120)))
    out = [section("Local radar", width, "press 4 for the loop")]
    out += [[(" ", "text")] + ln for ln in lines]
    if res:
        # the same nowcast the crawl and speech use (the full radar's when there is one),
        # so this panel and the bottom line never give two different storm motions
        best = getattr(v, "last_radar", None) or res
        for txt, sty in summary_lines(best, v.fmt, compact=True)[:2]:
            out += wrap(txt, width, sty, 2)
    return out


# ----------------------------------------------------------------------------
# text panels for sidebars
# ----------------------------------------------------------------------------

def almanac_panel(m, F, width):
    a = m.get("almanac") or {}
    if not a:
        return []
    out = [section("Almanac", width, (a.get("name") or "")[:max(0, width - 14)])]
    lw = 15
    if a.get("normal_hi") is not None:
        out.append(kv("Normal hi/lo", [(F.t(a["normal_hi"]), temp_style(a["normal_hi"])), (" / ", "dim"),
                                       (F.t(a["normal_lo"]), temp_style(a["normal_lo"]))], width, lw))
    if a.get("rec_hi") is not None:
        out.append(kv("Record high", [(F.t(a["rec_hi"]), temp_style(a["rec_hi"])),
                                      (f"  ({a.get('rec_hi_year') or '?'})", "dim")], width, lw))
    if a.get("rec_lo") is not None:
        out.append(kv("Record low", [(F.t(a["rec_lo"]), temp_style(a["rec_lo"])),
                                     (f"  ({a.get('rec_lo_year') or '?'})", "dim")], width, lw))
    if a.get("y_hi") is not None:
        out.append(kv("Yesterday", [(F.t(a["y_hi"]), temp_style(a["y_hi"])), (" / ", "dim"),
                                    (F.t(a.get("y_lo")), temp_style(a.get("y_lo"))),
                                    (f"  {F.precip(a.get('y_precip'))}" if a.get("y_precip") else "", "rain")],
                      width, lw))
    for key, nkey, label in (("mtd_precip", "mtd_normal", "Month precip"), ("ytd_precip", "ytd_normal", "Year precip")):
        if a.get(key) is None:
            continue
        segs = [(F.precip(a[key]), "rain")]
        if a.get(nkey) is not None:  # no normal for this station: no comparison
            diff = a[key] - a[nkey]
            segs.append((f"  ({'+' if diff >= 0 else '−'}{F.precip(abs(diff), False)} vs normal)", "dim"))
        out.append(kv(label, segs, width, lw))
    if a.get("mtd_avg") is not None and a.get("mtd_avg_normal") is not None:
        d = a["mtd_avg"] - a["mtd_avg_normal"]
        out.append(kv("Month so far", [(f"{F.tdelta(d)} vs normal", "warn" if d > 0 else "rain")], width, lw))
    return out


def outlook_panel(m, F, width):
    ol = m.get("outlooks") or []
    if not ol:
        return []
    out = [section("Severe outlook", width, "SPC / WPC")]
    today = F.today()
    for o in ol[:6]:
        day = today + dt.timedelta(days=(o["day"] or 1) - 1)
        lab = "Today" if day == today else f"{day:%a}"
        txt = f"{o['word']}" + (f" ({o['level']}/5)" if o.get("level") and o["category"] == "CATEGORICAL" and o["type"] == "C" else "")
        if 2 + 6 + 15 + len(txt) > width:              # the line loses its two-cell indent in a card
            txt = txt.replace("General thunderstorms", "Thunderstorms")
        out.append(clip([("  " + lab.ljust(6), "label"), (o["kind"][:14].ljust(15), "dim"), (txt, o["style"])], width))
    return out


def stations_panel(m, F, width, n=6):
    st = m.get("stations") or {}
    lst = [s for s in st.get("list") or [] if s.get("has_met") and not s.get("stale")]
    if not lst:
        return []
    out = [section("Nearby stations", width, "press 5 for all")]
    for s in lst[:n]:
        o = s["obs"]
        name = s["name"][:max(6, width - 22)]
        out.append(clip([("  " + name.ljust(max(6, width - 22)), "text"), (F.t(o.get("temp")).rjust(5), temp_style(o.get("temp"))),
                         (f" {F.dist(s['dist'], False):>3}{F.u_dist}", "dim"), (" ✓" if s.get("in_zip") else "", "good")],
                        width))
    sm = st.get("summary") or {}
    if sm.get("n"):
        out.append(clip([("  ", "text"), (f"{sm['n']} reporting, {F.t(sm['min'])}–{F.t(sm['max'])}", "dim")], width))
    return out


def sun_panel(m, F, width):
    loc = m.get("loc") or {}
    if loc.get("lat") is None:
        return []
    sr, ss, _how = sun_pair(m, F)
    age, illum, name = moon_info(time.time())
    out = [section("Sun & moon", width)]
    out.append(kv("Sunrise", F.time(sr), width, 12))
    out.append(kv("Sunset", F.time(ss), width, 12))
    if sr and ss:
        out.append(kv("Daylight", F.duration(ss - sr), width, 12))
    out.append(kv("Moon", [(name, "t_mild"), (f"  {illum * 100:.0f}% lit", "dim")], width, 12))
    return out


def air_panel(m, F, width):
    air = m.get("air") or {}
    if air.get("us") is None:
        return []
    lab, sty, _ = aqi_cat(air["us"])
    out = [section("Air quality", width, air.get("src") or "")]
    out.append(kv("US AQI", [(f"{air['us']} {lab}", sty)], width, 12))
    if air.get("dominant"):
        out.append(kv("Driven by", air["dominant"], width, 12))
    return out


SKY_SHORT = (("Showers And Thunderstorms", "T-storms"), ("Thunderstorms", "T-storms"),
             ("Rain Showers", "Showers"), ("Snow Showers", "Snow showers"), ("Rain And Snow", "Rain, snow"),
             ("Freezing Rain", "Freezing rain"), ("Slight Chance", "Slight chance"))


def short_sky(desc, width, lines=2):
    """A day's sky in a few words for a narrow column: the first part of "A then B",
    thunderstorms as T-storms, in at most `lines` lines of `width` (the last cut with …)."""
    d = " ".join(str(desc or "").split()).split(" then ")[0].strip()
    for a, b in SKY_SHORT:
        d = d.replace(a, b)
    out, cur = [], ""
    for w in d.split():
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = (out[-1][:width - 1] + "…") if len(out[-1]) >= width else out[-1] + "…"
    return [x if len(x) <= width else x[:width - 1] + "…" for x in out]


def extended_strip(v, m, width, days=None, slot_prefix="ext", bg_role="bg"):
    """The Weather Channel 'Extended Forecast': one column per day with an icon."""
    F = v.fmt
    rows = (m.get("daily") or [])[:days or 7]
    if not rows:
        return []
    colw = 15
    n = max(1, min(len(rows), (width - 2) // colw))
    rows = rows[:n]
    cols_lines = []
    for i, r in enumerate(rows):
        lab = "Today" if r["date"] == F.today() else f"{r['date']:%a} {r['date'].day}"
        col = [[(lab.center(colw - 1)[:colw - 1], "title")]]
        ic = icon_block(v, r.get("kind"), True, 13, 5, slot=f"{slot_prefix}{i}", bg_role=bg_role)
        col += [[(" ", "text")] + ln for ln in ic]
        desc = (r.get("desc") or icons.label(r.get("kind")) or "").strip()
        sky = short_sky(desc, colw - 2) + ["", ""]
        col.append([(sky[0].center(colw - 1), "text")])
        col.append([(sky[1].center(colw - 1), "text")])
        hi, lo = r.get("hi"), r.get("lo")
        col.append([(" " * max(0, (colw - 9) // 2), "text"), (F.t(hi).rjust(4), temp_style(hi)), (" ", "text"),
                    (F.t(lo).ljust(4), temp_style(lo))])
        pop = r.get("pop")
        col.append([((f"{pop:.0f}% precip" if pop else "").center(colw - 1), pop_style(pop))])
        cols_lines.append(col)
    out = []
    for k in range(max(len(c) for c in cols_lines)):
        line = [(" ", "text")]
        for c in cols_lines:
            line += padto(c[k] if k < len(c) else [], colw)
        out.append(line)
    return out


def periods_text(m, n=2):
    """The next NWS forecast periods (name, text)."""
    out = []
    for d in m.get("daily") or []:
        for key in ("nws_day", "nws_night"):
            p = d.get(key)
            if p and p.get("text"):
                out.append((p["start"], p["name"], p["text"]))
    out.sort()
    now = time.time()
    res = [(name, text) for start, name, text in out if start >= now - 12 * 3600]
    return res[:n]
