"""Screen renderers. Each returns a dict:

  lines         main content (scrolls)
  sticky        how many top lines stay put
  cursor        line index of the selection (kept on screen)
  footer        detail pane lines (bottom), footer_title
  side          sidebar lines (right, doesn't scroll), side_w
  fixed         True when the content is sized to the screen (no scrolling)
  rowmap        {line index: selection index} for mouse clicks
"""

import datetime as dt
import math
import re
import textwrap
import time

from . import gfx, icons, precip, statemap, ui
from .charts import (area_image, columns_image, daily_chart, daily_text, daylight_image, hourly_chart,
                     hourly_text, range_image)
from .columns import area_chart, bar_chart, column_chart, layout
from .crt import METER, meter_block, meter_spans
from .fmt import wind_text
from .text import (block, card, seglen, fit_columns, kv_grid, labeled_wrap, padto, restyle, spark,
                   table_header, table_row, table_width, unit_cols, wrap, clip)
from .theme import HUMIDITY_STYLES, blend, humidity_style, ink_on, pop_style, temp_style, wind_style
from .util import cap, first, iso_epoch, sum_opt
from .wxmath import (LIGHTNING_LAL, OWM_AQI, OWM_AQI_STYLE, SYNODIC, aqi_cat, beaufort, comfort, compass,
                     grid_weather_text, hazard_text, heatrisk_label, moon_info, moon_name, next_moon_phases,
                     nws_wind_range, sun_altitude, sun_day, us_aqi, uv_cat)


def heatrisk_seg(val, short=False):
    label, style = heatrisk_label(val)
    if not label:
        return [("--", "dim")]
    if short:
        label = "None" if label.startswith("Little") else label
    return [(f"{int(round(val))} {label}", style)]


def minutely_summary(mins):
    wet = [(p or 0) >= 0.1 for _, p in mins[:61]]
    if not any(wet):
        return "No precipitation expected in the next hour"
    if wet[0]:
        if all(wet):
            return "Precipitation continuing through the hour"
        return f"Precipitation ending in about {wet.index(False)} min"
    return f"Precipitation starting in about {wet.index(True)} min"


CLOUD_WORDS = {"FEW": "few", "SCT": "scattered", "BKN": "broken", "OVC": "overcast",
               "CLR": "clear", "SKC": "clear", "VV": "obscured"}


def sky_text(layers, F):
    if not layers:
        return "--"
    parts = []
    for amount, base in layers:
        word = CLOUD_WORDS.get(amount or "", (amount or "").lower())
        parts.append(f"{word} at {F.height(base)}" if base is not None else word)
    return cap(", ".join(parts))


def with_side(L, side, side_w, printing, out=None, W=0, ascii_only=False, main_w=None):
    """A screen's lines and its sidebar, both as cards: the main lines' section headings
    become cards `main_w` wide (their cursor and row map following), the sidebar's cards
    `side_w` wide, or under the main lines when printing or with no room for a sidebar.
    Build a sidebar's lines two cells narrower than it (sidebar_width) and a card's lines
    with the usual two-cell indent: the cards' edges take those cells."""
    out = out or {}
    out["lines"] = L
    ui.recard(out, main_w or (W - side_w - 1 if side_w and not printing else W) or 80, ascii_only)
    if side:
        if side_w and not printing:
            out["side"], out["side_w"] = ui.cardify(side, side_w, ascii_only)[0], side_w
        else:
            out["lines"] = out["lines"] + [[]] + ui.cardify(side, W or 80, ascii_only)[0]
    return out


def sidebar_width(side_w, W, printing):
    """The width to build a sidebar's lines at: two cells inside its cards' edges."""
    return (side_w if side_w and not printing else W) - 2


# ----------------------------------------------------------------------------
# 1 Now
# ----------------------------------------------------------------------------

# the Now screen's column charts: LED columns in the meters' look (Settings > Display)
SNOWY = {"snow", "flurries", "sleet", "freezing", "mix"}
CHART_GUTTER = 7          # "Temp", "Rain" and the like left of the columns


def native_temp(F, x):
    """A temperature as shown (°F or °C) back in °C, for the temperature colors."""
    return None if x is None else (x - 32) * 5 / 9 if F.imp else x


def indented(lines, n=2):
    return [[(" " * n, "text")] + ln if ln else [] for ln in lines]


def next_hour(mins, width):
    """The next hour's precipitation: a line saying when it starts or stops, and while any
    is expected, five-minute columns of how hard it falls (light, moderate, heavy)."""
    head = [("  Next hour   ", "label"), (minutely_summary(mins), "text")]
    vals = [p or 0.0 for _t, p in mins[:60]]
    if not any(x >= 0.1 for x in vals):
        return [head]
    # rain rates (mm/h): light under 2.5, moderate to 7.6, heavy above
    bins = [max(vals[i:i + 5]) for i in range(0, len(vals), 5)]
    levels = [0 if b < 0.1 else 1 if b < 2.5 else 2 if b < 7.6 else 3 for b in bins]
    labels = ["Now" if i == 0 else f"+{i * 5}m" if i % 3 == 0 else "" for i in range(len(bins))]
    chart = column_chart(levels, width - 2, 3, lo=0, hi=3, style="rain", labels=labels, gutter=CHART_GUTTER,
                         axis=[(1, "light"), (2, "mod"), (3, "heavy")])
    return [head] + indented(chart)


def area(v, slot, values, width, height, **kw):
    """An area chart. Where the terminal draws pictures, a picture of it (a smooth curve
    with a crisp line along its top, the area shaded under it, the values over it, the
    guides and the scale its own) in place of the eighths of cells drawn elsewhere, in
    the chart's own colors. The arguments are area_chart's."""
    images = getattr(v, "images", None)
    if (images is None or getattr(v, "printing", False) or not getattr(images, "pixels", False)
            or not gfx.HAVE_PIL or not values):
        return area_chart(values, width, height, **kw)
    th = v.theme
    style = kw.get("style", "value")
    if callable(style):
        def paint(p, value):
            return th.spec(style(value))[0]
    elif isinstance(style, (list, tuple)) and not (style and style[0] in ("rgb", "rev", "raised", "fade")):
        palette = [th.spec(x)[0] for x in style] or [th.spec("value")[0]]

        def paint(p, value):                    # each value's color, blended toward the next
            p = max(0.0, min(len(palette) - 1.0, p))
            i = int(p)
            return blend(palette[i], palette[min(len(palette) - 1, i + 1)], p - i)
    else:
        one = th.spec(style)[0]

        def paint(p, value):
            return one
    axis, tops, title = kw.get("axis") or (), kw.get("tops"), kw.get("title") or ""
    gutter = kw.get("gutter")
    if gutter is None:
        gutter = max([len(text) + 2 for _v, text in axis] + [len(title) + 2 if title else 0])
        kw["gutter"] = gutter
    nums = [x for x in values if x is not None]
    if not nums or width - gutter < 2:
        return area_chart(values, width, height, **kw)
    lo = min(nums) if kw.get("lo") is None else kw["lo"]
    hi = max(nums) if kw.get("hi") is None else kw["hi"]
    head = 1 if tops else 0
    start = 0 if axis else gutter               # with a scale, the picture draws it too
    cols, rows = width - start, head + height
    marks = [(i, text, paint(i, values[i])) for i, text in enumerate(tops or [])
             if text and i < len(values) and values[i] is not None]
    lines = [(g, th.spec(gs)[0]) for g, gs in kw.get("guides") or () if g is not None]
    scale = [(value, text) for value, text in axis]
    ink = (th.spec("dim")[0], th.spec("faint")[0])
    back = th.rgb("card_bg")
    band = kw.get("band") or 0
    bars = [(i, frac, th.spec(bs)[0], text) for i, frac, bs, text in kw.get("bars") or ()] if band else []
    # a second series as a line over the area: its colors along the styles given for its
    # scale, blended between neighbors
    line = tuple(kw.get("line") or ())
    line_scale = tuple(kw.get("line_scale") or (0.0, 100.0))
    ramp = [th.spec(s)[0] for s in kw.get("line_styles") or ()]
    line_color = None
    if ramp and sum(1 for x in line if x is not None) >= 2:
        l_lo, l_hi = line_scale

        def ramp_color(value):
            if len(ramp) == 1:
                return ramp[0]
            p = max(0.0, min(1.0, (value - l_lo) / ((l_hi - l_lo) or 1.0))) * (len(ramp) - 1)
            i = min(len(ramp) - 2, int(p))
            return blend(ramp[i], ramp[i + 1], p - i)
        line_color = ramp_color
    else:
        line = ()
    key = (f"area:{slot}:{th.name}:{back}:{cols}x{rows}:{start}:{gutter}:"
           f"{hash((tuple(values), lo, hi, tuple(marks), tuple(lines), tuple(scale), band, tuple(bars)))}:"
           f"{hash((line, line_scale, tuple(ramp)))}")

    def build(mode, pw, ph):
        if mode != "pixels":
            return None
        return area_image(values, lo, hi, pw, ph, back, paint, cols=cols, rows=rows, head=head,
                          left=gutter if axis else 0, tops=marks, guides=lines, axis=scale, axis_ink=ink,
                          band=band, bars=bars, line=line, line_scale=line_scale, line_color=line_color)
    # a slot of its own for each size: a screen that tries a few sizes keeps the one it shows
    plot = v.images.block(f"{slot}{cols}x{rows}", key, cols, rows, build, back=back)
    return area_chart(values, width, height, plot=plot, plot_from=start, **kw)


def columns(v, slot, values, width, height, **kw):
    """Solid columns, each outlined: where the terminal draws pictures, a picture of them
    (with the scale, guide lines and the values over them); solid bars of text elsewhere.
    The arguments are bar_chart's."""
    images = getattr(v, "images", None)
    nums = [x for x in values if x is not None]
    if (images is None or getattr(v, "printing", False) or not getattr(images, "pixels", False)
            or not gfx.HAVE_PIL or not nums):
        return bar_chart(values, width, height, **kw)
    th = v.theme
    style = kw.get("style", "value")

    def look(i, value):
        if callable(style):
            return th.spec(style(value))[0]
        if isinstance(style, (list, tuple)) and not (style and style[0] in ("rgb", "rev", "raised", "fade")):
            return th.spec(style[i] if i < len(style) else "value")[0]
        return th.spec(style)[0]
    axis, tops = kw.get("axis") or (), kw.get("tops")
    gutter = kw.get("gutter") or (max(len(text) for _v, text in axis) + 2 if axis else 0)
    kw["gutter"] = gutter
    lo = min(nums) if kw.get("lo") is None else kw["lo"]
    hi = max(nums) if kw.get("hi") is None else kw["hi"]
    head = 1 if tops else 0
    rows = head + height
    n = len(values)
    slot_w = max(1, (width - gutter) // n)
    cols = gutter + slot_w * n                  # the columns' own width, as the text's
    colors = [look(i, x) if x is not None else (0, 0, 0) for i, x in enumerate(values)]
    marks = [(i, t, look(i, values[i])) for i, t in enumerate(tops or []) if t and values[i] is not None]
    lines = [(g, th.spec(gs)[0]) for g, gs in kw.get("guides") or () if g is not None]
    scale = list(axis)
    ink = (th.spec("dim")[0], th.spec("faint")[0])
    back = th.rgb("card_bg")
    key = (f"cols:{slot}:{th.name}:{back}:{cols}x{rows}:{gutter}:"
           f"{hash((tuple(values), lo, hi, tuple(marks), tuple(lines), tuple(scale)))}")

    def build(mode, pw, ph):
        if mode != "pixels":
            return None
        return columns_image(values, lo, hi, colors, pw, ph, back, cols=cols, rows=rows, head=head,
                             left=gutter, tops=marks, guides=lines, axis=scale, axis_ink=ink)
    plot = images.block(f"{slot}{cols}x{rows}", key, cols, rows, build, back=back)
    return bar_chart(values, width, height, plot=plot, plot_from=0, **kw)


def next_hours(m, F, width, hours=12, v=None):
    """The next 12 hours in one chart: the temperature as an area along the top, the
    humidity as a line over it (0% at the area's foot, 100% at its top) colored along the
    whole spectrum, red for the driest air to violet for the most humid, and under them a
    bar for each hour precipitation may fall, as tall as its chance and colored by what
    falls, as on weather radar (rain in greens, light to heavy; storms gold; snow white;
    sleet pink; freezing rain magenta; a wintry mix violet); the hours along the bottom
    and a key under them for the humidity and the precipitation, or a line saying none is
    expected. Given the view, pictures where the terminal draws them."""
    now = time.time()
    rows = [r for r in m.get("hourly") or [] if r.get("t") is not None and r["t"] + 3600 > now][:hours]
    temps = [F.tv(r.get("temp")) for r in rows]
    tv = [t for t in temps if t is not None]
    if len(rows) < 2 or not tv:
        return []
    w = width - 2
    slot, _body = layout(len(rows), w - CHART_GUTTER)
    labels = ui.time_labels([r["t"] for r in rows], F, slot, now, room=5)
    pops = [r.get("pop") if (r.get("pop") or 0) >= 5 else None for r in rows]
    kinds = [precip.hour_type(r) if p is not None else None for r, p in zip(rows, pops)]
    wet = any(p is not None for p in pops)
    bars = [(i, min(100.0, p) / 100.0, precip.style_of(k), f"{p:.0f}%")
            for i, (p, k) in enumerate(zip(pops, kinds)) if p is not None]
    hums = [r.get("hum") for r in rows]
    humid = sum(1 for x in hums if x is not None) >= 2
    # the coolest hour still lights a segment or two, and a flat day stays flat
    t_hi = max(tv)
    span = max(t_hi - min(tv), 8.0 if F.imp else 5.0)
    out = area(v, "areaNext", temps, w, 6 if wet else 5, lo=t_hi - span * 1.25, hi=t_hi,
               style=lambda x: temp_style(native_temp(F, x)),
               tops=[F.t(r.get("temp")) if r.get("temp") is not None else "" for r in rows],
               gutter=CHART_GUTTER, title="Temp", labels=labels, band=3 if wet else 0, bars=bars,
               line=hums if humid else (), line_scale=(0.0, 100.0), line_styles=HUMIDITY_STYLES)
    if humid:
        out.append(humidity_key(hums, w, ascii_only=METER["ascii"]))
    out.append(precip.legend(kinds, w, ascii_only=METER["ascii"]))
    return indented(out)


def humidity_key(hums, width, ascii_only=False):
    """The humidity line's key: 'Humidity', the spectrum from 0% to 100% drawn as a short
    line in its colors, and the lowest and highest humidity in the next 12 hours, each in
    its color; the words, then the range, then half the line left out where there's no
    room."""
    head = [("Humidity       ", "label")]       # lined up with the Precipitation key's
    vals = [x for x in hums if x is not None]
    if not vals:
        return head + [("--", "dim")]
    stroke = "=" if ascii_only else "━"

    def scale(k):
        last = len(HUMIDITY_STYLES) - 1
        return ([("0% ", "dim")] + [(stroke, HUMIDITY_STYLES[int(round(j * last / (k - 1)))]) for j in range(k)]
                + [(" 100%", "dim")])
    lo, hi = min(vals), max(vals)
    if f"{lo:.0f}" == f"{hi:.0f}":
        span = [(f"{lo:.0f}%", humidity_style(lo))]
        words = " through the next 12 hours"
    else:
        span = [(f"{lo:.0f}%", humidity_style(lo)), (" to ", "text"), (f"{hi:.0f}%", humidity_style(hi))]
        words = " in the next 12 hours"
    gap = [("   ", "text")]
    for body in (scale(11) + gap + span + [(words, "text")], scale(11) + gap + span, scale(6) + gap + span, scale(6)):
        if seglen(head + body) <= width:
            return head + body
    return clip(head + scale(6), width)


def runin(label, text, width, indent=2, hang=4):
    """'Tonight  Mostly cloudy, with ...' wrapped with a hanging indent: the label takes no column."""
    first = indent + len(label) + 2
    words = str(text or "").split()
    lines, cur, x = [], [(" " * indent + label + "  ", "label")], first
    for w in words:
        if x + len(w) > width and x > first - 0:
            lines.append(cur)
            cur, x = [(" " * hang, "text")], hang
        cur.append((w + " ", "text"))
        x += len(w) + 1
    lines.append(cur)
    return lines


WIND_ARROWS = "↓↙←↖↑↗→↘"         # where the wind blows to, from a north wind round


def gauge(frac, width, style, ascii_only=False):
    """A meter `width` segments long: the lit part in `style`, the rest of the scale faint."""
    width = max(1, int(width))
    frac = max(0.0, min(1.0, frac or 0.0))
    lit = int(round(frac * width)) or (1 if frac > 0 else 0)
    if ascii_only:
        return [("#" * lit, style), ("." * (width - lit), "faint")]
    return meter_block(lit, style) + (meter_block(width - lit, "faint") if width > lit else [])


def conditions_tiles(m, F, ascii_only=False):
    """The weather now as a row of small cards: wind, humidity, pressure, visibility, the
    sky and UV, each with its number large and a word or a meter under it."""
    o, c = m.get("obs") or {}, m.get("current") or {}
    if not o and not c:
        return []
    today = ui.today_row(m, F)
    wind, deg, gust = first(o.get("wind"), c.get("wind")), first(o.get("wind_deg"), c.get("wind_deg")), \
        first(o.get("gust"), c.get("gust"))
    rh, dew = first(o.get("rh"), c.get("humidity")), first(o.get("dew"), c.get("dew"))
    press, trend = first(o.get("press"), c.get("pressure")), o.get("trend")
    vis, cover = first(o.get("vis"), c.get("vis")), c.get("clouds")
    uvi = first(c.get("uvi"), (m.get("now") or {}).get("uvi"))

    def tile(title, rows, note=""):
        return {"title": title, "note": note, "build": lambda cw, rows=rows: [clip(r(cw) if callable(r) else r, cw)
                                                                              for r in rows]}
    out = []
    if wind is not None:
        bf = beaufort(wind)
        if wind < 0.5 or deg is None:
            head = [("Calm", "value")]
        else:
            arrow = "" if ascii_only else WIND_ARROWS[int(((deg + 22.5) % 360) // 45)] + " "
            head = [(arrow, "accent"), (wind_text(F, deg, wind), wind_style(wind))]
        out.append(tile("Wind", [head, [(f"gusts {F.wind(gust)}" if gust else "no gusts", wind_style(gust) if gust else "dim")],
                                 [(bf[1].lower() if bf else "", "dim")]]))
    if rh is not None:
        lab, sty = comfort(dew)
        out.append(tile("Humidity", [[(F.pct(rh), "value"), (f"   dew point {F.t(dew)}" if dew is not None else "", "dim")],
                                     lambda cw: gauge(rh / 100, max(4, cw), "rain", ascii_only),
                                     [(lab, sty)]]))
    if press is not None:
        word, arrow = "steady", "→"
        if trend is not None:
            word, arrow = ("rising", "↑") if trend > 0.6 else ("falling", "↓") if trend < -0.6 else ("steady", "→")
        out.append(tile("Pressure", [[(F.press(press), "value")],
                                     [("" if ascii_only else arrow + " ", "accent"), (word, "text")],
                                     [(f"{press:.0f} hPa" if F.imp else f"{press * 0.02953:.2f} inHg", "dim")]]))
    if vis is not None:
        km = vis / 1000.0
        word = ("dense fog" if km < 0.5 else "fog" if km < 1 else "mist or haze" if km < 5 else "a little hazy"
                if km < 9.5 else "clear")
        out.append(tile("Visibility", [[(F.vis(vis), "value")], [(word, "text" if km >= 9.5 else "warn")],
                                       lambda cw: gauge(min(1.0, km / 16.0), max(4, cw), "good" if km >= 9.5 else "warn",
                                                        ascii_only)]))
    if cover is not None or o.get("clouds"):
        sky = sky_text(o.get("clouds"), F) if o.get("clouds") else ""
        pct = cover if cover is not None else None
        rows = [[(F.pct(pct) if pct is not None else cap(sky.split(" at ")[0]) or "--", "value")]]
        rows.append([(sky or ("clear" if (pct or 0) < 10 else "partly cloudy" if pct < 60 else "cloudy"), "text")])
        if pct is not None:
            rows.append(lambda cw, pct=pct: gauge(pct / 100, max(4, cw), "dim", ascii_only))
        out.append(tile("Clouds", rows))
    if uvi is not None:
        lab, sty = uv_cat(uvi)
        peak = today.get("uvi")
        out.append(tile("UV index", [[(f"{uvi:.0f} ", "value"), (lab, sty)],
                                     lambda cw: gauge(min(1.0, uvi / 11), max(4, cw), sty, ascii_only),
                                     [(f"peak {peak:.0f} today" if peak is not None else "", "dim")]]))
    return out


def render_now(m, v, W, H, printing=False):
    """Now, in cards: the conditions (icon, big temperature, the day so far) beside the sun
    and air; the next hour, the forecast in words, the next 12 hours, the station's report
    and the model's; a sidebar of cards (local radar, stations, almanac, outlooks, air)."""
    F = v.fmt
    cur, obs, st = m["current"], m["obs"], m["station"]
    main_w, side_w = ui.sidebar_split(W, want=50) if not printing else (W, 0)
    L = []
    if not cur and not obs:
        busy = not m["errors"]
        L.append([("  Loading current conditions…" if busy else
                   "  Current conditions aren't available right now.", "dim")])
        L += ui.error_lines(m, ("loc", "owm_onecall", "owm_current", "om_forecast", "nws_points", "nws_station",
                                "nws_obs"), main_w)
        return {"lines": L}
    today = ui.today_row(m, F)
    now = m.get("now") or {}
    temp, feels = now.get("temp"), first(now.get("feels"), now.get("temp"))
    desc = cap(now.get("desc"))
    kind = now.get("kind") or icons.from_text(desc)
    is_day = now.get("is_day")
    if is_day is None:
        sr, ss, _ = ui.sun_pair(m, F)
        is_day = bool(sr and ss and sr <= time.time() <= ss)
    dew = now.get("dew")
    uvi = now.get("uvi")
    sr, ss, how = ui.sun_pair(m, F)
    air = m.get("air") or {}
    asc = v.ascii

    info = [
        [(desc or "--", "value")],
        [("Feels like ", "label"), (F.t(feels), temp_style(feels))],
        [("High ", "label"), (F.t(today.get("hi")), temp_style(today.get("hi"))),
         ("   Low ", "label"), (F.t(today.get("lo")), temp_style(today.get("lo")))],
    ]
    seg = [("Precip ", "label"), (F.pct(today.get("pop")), pop_style(today.get("pop")))]
    if uvi is not None:
        lab, sty = uv_cat(uvi)
        seg += [("   UV ", "label"), (f"{uvi:.0f} {lab}", sty)]
    info.append(seg)
    extra = []
    if dew is not None:
        lab, sty = comfort(dew)
        extra += [(lab, sty), (f"   dew point {F.t(dew)}", "label")]
    if today.get("heatrisk") is not None:
        extra += [("   HeatRisk ", "label")] + heatrisk_seg(today["heatrisk"])
    if extra:
        info.append(extra)
    big = ui.big_temp(v, F.t(temp))

    def conditions(cw):
        icon = ui.icon_block(v, kind, is_day, 13, 5, slot="nowicon", bg_role="card_bg") if cw >= 56 else []
        pic = ui.big_temp_block(v, F.t(temp), temp_style(temp), 5)
        bw = pic[1] + 1 if pic else max(len(r) for r in big) + 3
        out = []
        for i in range(max(5, len(info))):
            row = []
            if i < 5:
                if icon:
                    row += padto(icon[i], 13) + [("  ", "text")]
                if pic:
                    row += pic[0][i] + [(" ", "text")]
                else:
                    row.append(((big[i] if i < len(big) else "").ljust(bw), temp_style(temp)))
            else:
                row.append((" " * ((15 if icon else 0) + bw), "text"))
            out.append(row + (info[i] if i < len(info) else []))
        if today.get("hazards"):
            out.append([("NWS hazards today  ", "label"), (", ".join(today["hazards"]), "warn")])
        return out

    def sun_air(cw):
        out = [[("Sunrise   ", "label"), (F.time(sr), "value")],
               [("Sunset    ", "label"), (F.time(ss), "value")],
               [("Daylight  ", "label"), (F.duration(ss - sr) if sr and ss else "--", "value")]]
        if air.get("us") is not None:
            lab, sty, _ = ui.aqi_cat(air["us"])
            out.append([("Air       ", "label"), (f"{air['us']} {lab}", sty)])
            if air.get("dominant"):
                out.append([("          ", "text"), (f"mostly {air['dominant']}", "dim")])
        return out

    hero = {"title": "Current conditions", "note": F.time(now.get("t")) if now.get("t") else "", "build": conditions}
    sun = {"title": "Sun and air", "note": f"from {how}" if how else "", "build": sun_air}
    L += ui.card_grid([[dict(hero, span=2), sun]] if main_w >= 100 else [[hero], [sun]], main_w, asc)
    tiles = conditions_tiles(m, F, asc) if main_w >= 72 else []
    if tiles:
        per = 6 if main_w >= 126 else 3
        L += ui.card_grid([tiles[i:i + per] + [None] * (per - len(tiles[i:i + per])) for i in range(0, len(tiles), per)],
                          main_w, asc)

    def full(title, body, note="", indent=2):
        """A card the main area's width: body(content width) -> lines built with the usual
        indent, which the card's edge takes."""
        lines = [ui.dedent(ln, indent) for ln in body(main_w - 4 + indent)]
        return ui.card(title, lines, main_w, note, asc) if lines else []

    mins = m.get("minutely") or []
    if mins:
        L += full("Next hour", lambda w: [[("  " + minutely_summary(mins), "text")]] + next_hour(mins, w)[1:],
                  "minute by minute")
    if m.get("overview"):
        L += full("Today's outlook", lambda w: wrap(m["overview"], w, "text", 2), "OpenWeather summary")
    periods = ui.periods_text(m, 3)
    if periods:
        def forecast(w):
            out = []
            for name, text in periods:
                if w >= 100:
                    out += labeled_wrap(name, text, min(w, 140), lw=18, style="text", indent=2)
                else:             # narrow: the period name runs into its text, which gets the whole width
                    out += runin(name, text, w)
            return out
        L += full("Local forecast", forecast, "National Weather Service · press s to hear it")
    if next_hours(m, F, main_w - 2):
        L += full("Next 12 hours", lambda w: next_hours(m, F, w, v=v), "press 2 for every hour")

    # the station's report and the model's, side by side in one table; the station's own
    # facts in a card beside it
    cards = []
    o, c = obs or {}, cur or {}
    sid = st.get("id") or o.get("station_id") or ""
    msrc = (c.get("src") if c.get("src") != "Open-Meteo" else "Open-Meteo model") if c else ""
    if obs or cur:
        def tv(x, style=None):
            return [(F.t(x, True), style or temp_style(x))] if x is not None else [("--", "dim")]

        def plain(x, style="value"):
            return [(x, style)] if x not in (None, "", "--") else [("--", "dim")]
        feels_o = o.get("heat") if o.get("heat") is not None else o.get("chill")
        trend = ""
        if o.get("trend") is not None:
            tr = o["trend"]
            trend = "  rising" if tr > 0.6 else "  falling" if tr < -0.6 else "  steady"
        precip_o = next((f"{F.precip(o[k])}, last {h}" for k, h in (("p1", "hour"), ("p6", "6 hours"),
                                                                       ("p24", "24 hours")) if o.get(k) is not None), None)
        precip_c = (f"{F.precip(c['rain_1h'])}, last hour" if c.get("rain_1h") is not None else None)
        bf = beaufort(c.get("wind")) if c else None
        table = [
            ("Conditions", plain(o.get("desc")), plain(cap(c.get("desc")) if c.get("desc") else None)),
            ("Temperature", tv(o.get("temp")) if obs else [("", "dim")], tv(c.get("temp")) if cur else [("", "dim")]),
            ("Feels like", tv(feels_o if feels_o is not None else o.get("temp")) if obs else [("", "dim")],
             tv(c.get("feels")) if cur else [("", "dim")]),
            ("Humidity", plain(F.pct(o.get("rh")) if o.get("rh") is not None else None),
             plain(F.pct(c.get("humidity")) if c.get("humidity") is not None else None)),
            ("Dew point", tv(o.get("dew"), "value") if obs else [("", "dim")], tv(c.get("dew"), "value") if cur else [("", "dim")]),
            ("Wind", [(wind_text(F, o.get("wind_deg"), o.get("wind")), wind_style(o.get("wind")))] if obs else [("", "dim")],
             [(wind_text(F, c.get("wind_deg"), c.get("wind")), wind_style(c.get("wind")))] if cur else [("", "dim")]),
            ("Gusts", plain(F.wind(o["gust"]) if o.get("gust") else "none" if obs else None),
             plain(F.wind(c["gust"]) if c.get("gust") else "none" if cur else None)),
            ("Pressure", [(F.press(o.get("press")), "value"), (trend, "dim")] if o.get("press") is not None else plain(None),
             plain(F.press(c.get("pressure")) if c.get("pressure") is not None else None)),
            ("Visibility", plain(F.vis(o.get("vis")) if o.get("vis") is not None else None),
             plain(F.vis(c.get("vis")) if c.get("vis") is not None else None)),
            ("Sky", plain(sky_text(o.get("clouds"), F) if o.get("clouds") else None),
             plain(f"{F.pct(c.get('clouds'))} cloud cover" if c.get("clouds") is not None else None)),
            ("Precipitation", plain(precip_o), plain(precip_c)),
            ("Wind force", [("", "dim")], plain(f"Beaufort {bf[0]}, {bf[1].lower()}" if bf else None)),
        ]
        table = [r for r in table if any("".join(t for t, _s in x) not in ("", "--") for x in r[1:])]
        heads = ([f"NWS {sid}".strip(), F.time(o.get("t")) if o.get("t") else ""] if obs else None,
                 [msrc, F.time(c.get("dt")) if c.get("dt") else ""] if cur else None)

        def compare(cw):
            lw = 15
            colw = max(16, (cw - lw) // 2 - 2) if obs and cur else cw - lw
            out = [[(" " * lw, "text")] + [x for h in heads if h for x in ((h[0], "label"), (f"  {h[1]}".ljust(
                colw - len(h[0])) if h[1] else " " * (colw - len(h[0])), "dim"))]]
            for k, (label, a, b) in enumerate(table):
                row = [(label.ljust(lw), "label")]
                if obs:
                    if cur and seglen(a) > colw - 1:      # keep a space before the model's column
                        a = clip(a, colw - 2) + [("…", "dim")]
                    row += padto(a, colw)
                if cur:
                    row += b
                out.append(ui.zebra(clip(row, cw), cw, k))
            return out
        cards.append({"title": "Now, measured and modeled",
                      "note": "the station's report beside the model's" if obs and cur else "", "build": compare,
                      "span": 2})
    if obs:
        sname = st.get("name") or o.get("station_name")
        pts = m.get("pts") or {}

        def station(cw, o=o, sname=sname, pts=pts):
            out = [[(sname or sid, "value")]]
            if o.get("present"):
                out += wrap(cap("; ".join(o["present"])), cw + 1, "warn", 0)
            if o.get("max24") is not None:
                out.append([("24-hr high/low  ", "label"), (F.t(o["max24"]), temp_style(o["max24"])), (" / ", "dim"),
                            (F.t(o.get("min24")), temp_style(o.get("min24")))])
            if o.get("elevation") is not None:
                out.append([("Elevation       ", "label"), (F.height(o["elevation"]), "value")])
            if o.get("fallback"):
                out.append([("the last full report shown", "dim")])
            if o.get("metar"):
                out += wrap(o["metar"], cw + 1, "faint", 0)
            if pts.get("gridId"):
                tail = f" · radar {pts['radarStation']}" if pts.get("radarStation") else ""
                nwr = (pts.get("nwr") or {}).get("transmitter")
                tail += f" · NOAA radio {nwr}" if nwr else ""
                out += wrap(f"NWS office {pts['gridId']} · grid {pts.get('gridX')},{pts.get('gridY')}{tail}", cw + 1,
                            "dim", 0)
            return out
        cards.append({"title": f"Station {sid}".strip(), "note": "press 5 for the others", "build": station})
    if cards:
        if len(cards) == 2 and main_w >= 110:
            L += ui.card_grid([cards], main_w, asc)
        else:
            L += ui.card_grid([[dict(c_, span=1)] for c_ in cards], main_w, asc)
    for name, note in (m.get("oc_notes") or {}).items():
        if name != "summary":
            L += wrap(f"OpenWeather {name}: {note}", main_w, "warn")
    L += ui.error_lines(m, ("owm_current", "om_forecast", "nws_obs", "nws_station"), main_w)

    sidebar = []
    if side_w or printing:
        sw = sidebar_width(side_w, W, printing)
        panels = [p for p in (ui.stations_panel(m, F, sw, 5), ui.almanac_panel(m, F, sw), ui.outlook_panel(m, F, sw),
                              ui.air_panel(m, F, sw)) if p]          # sun times are already in the main area
        if v.radar is not None and not printing:
            want = max(8, min(18, int(sw * (0.42 if v.gmode != "ascii" else 0.3))))
            room = H - sum(len(p) + 1 for p in panels) - 4      # the radar shrinks before anything scrolls
            sidebar += ui.mini_radar_panel(v, m, sw, max(8, min(want, room)))
        for panel in panels:
            sidebar += panel
    return with_side(L, sidebar, side_w, printing, W=W, ascii_only=asc, main_w=main_w)


# ----------------------------------------------------------------------------
# 2 Hourly
# ----------------------------------------------------------------------------

HOURLY_COLS = [
    ("time", "Time", 9, "l", 0),
    ("temp", "Temp", 4, "r", 0),
    ("feels", "Feels", 5, "r", 3),
    ("desc", "Conditions", 16, "l", 0),
    ("pop", "Rain", 4, "r", 0),
    ("amt", "Amt {precip}", 6, "r", 4),
    ("wind", "Wind {wind}", 10, "l", 1),
    ("gust", "Gust", 4, "r", 5),
    ("hum", "Hum", 4, "r", 2),
    ("dew", "Dew", 4, "r", 3),
    ("cloud", "Cloud", 5, "r", 6),
    ("uv", "UV", 3, "r", 6),
    ("tstm", "Tstm", 4, "r", 7),
    ("wbgt", "WBGT", 4, "r", 9),
    ("press", "{press}", 5, "r", 8),
    ("vis", "Vis {vis}", 6, "r", 8),
]


def hour_detail(r, F, W):
    src = r.get("src")
    owm = src == "owm"
    head = []
    if owm and r.get("desc"):
        head += labeled_wrap("OpenWeather", cap(r["desc"]), W)
    if r.get("nws_desc"):
        head += labeled_wrap("NWS", r["nws_desc"], W)
    if src == "om" or r.get("om_desc"):
        head += labeled_wrap("Open-Meteo", r.get("om_desc") or r.get("desc"), W, style="dim" if src != "om" else "text")
    wx = grid_weather_text(r.get("grid_wx"))
    if wx:
        head += labeled_wrap("NWS weather", wx, W)
    hz = hazard_text(r.get("hazards"))
    if hz:
        head += labeled_wrap("NWS hazards", hz, W, style="warn")
    pairs = []
    t = F.t(r.get("temp"), True)
    if owm and r.get("nws_temp") is not None:
        t += f"  (NWS {F.t(r['nws_temp'])})"
    if r.get("trend"):
        t += f"  {r['trend']}"
    pairs.append(("Temperature", t, temp_style(r.get("temp"))))
    fl = F.t(r.get("feels"), True)
    if owm and r.get("apparent") is not None:
        fl += f"  (NWS {F.t(r['apparent'])})"
    pairs.append(("Feels like", fl, temp_style(r.get("feels"))))
    if r.get("heat_index") is not None and r["heat_index"] >= 26.7:
        pairs.append(("Heat index", F.t(r["heat_index"], True), temp_style(r["heat_index"])))
    if r.get("wind_chill") is not None and r["wind_chill"] <= 10:
        pairs.append(("Wind chill", F.t(r["wind_chill"], True), temp_style(r["wind_chill"])))
    if r.get("wbgt") is not None:
        pairs.append(("Wet bulb globe", F.t(r["wbgt"], True), temp_style(r["wbgt"])))
    if r.get("heatrisk") is not None:
        pairs.append(("HeatRisk", heatrisk_seg(r["heatrisk"])))
    lab, sty = comfort(r.get("dew"))
    pairs.append(("Dew point", [(F.t(r.get("dew"), True), "value"), ("  " + lab, sty)]))
    pairs.append(("Humidity", F.pct(r.get("hum"))))
    w = wind_text(F, r.get("wind_deg"), r.get("wind"))
    if r.get("gust"):
        w += f", gusts {F.wind(r['gust'])}"
    pairs.append(("Wind", w, wind_style(r.get("wind"))))
    if owm and r.get("nws_wind_dir"):
        nw = f"{r['nws_wind_dir']} {nws_wind_range(F, r.get('nws_wind_raw'))}".strip()
        if r.get("nws_gust"):
            nw += f", gusts {F.wind(r['nws_gust'])}"
        pairs.append(("NWS wind", nw))
    bf = beaufort(r.get("wind"))
    if bf:
        pairs.append(("Wind force", f"Beaufort {bf[0]}, {bf[1].lower()}"))
    for key, label in (("p_gust40", f"Gusts over {F.wind(40 * 0.44704)}"), ("p_gust60", f"Gusts over {F.wind(60 * 0.44704)}"),
                       ("p_ts_wind", "Tropical wind"), ("p_hu_wind", "Hurricane wind")):
        if r.get(key):
            pairs.append((label, F.pct(r[key]), "warn"))
    pp = F.pct(r.get("pop"))
    if owm and r.get("nws_pop") is not None:
        pp += f"  (NWS {r['nws_pop']:.0f}%)"
    pairs.append(("Precip chance", pp, pop_style(r.get("pop"))))
    if r.get("thunder") is not None:
        pairs.append(("Thunder chance", F.pct(r["thunder"]), "warn" if r["thunder"] >= 30 else "value"))
    if r.get("cape") is not None and r["cape"] >= 500:
        pairs.append(("Storm energy", f"CAPE {r['cape']:.0f} J/kg", "warn" if r["cape"] >= 1500 else "value"))
    if r.get("lal") is not None and r["lal"] >= 2:
        pairs.append(("Lightning", LIGHTNING_LAL.get(int(r["lal"]), f"level {r['lal']:.0f}"), "warn"))
    if owm or src == "om":
        pairs.append(("Rain / snow", f"{F.precip(r.get('rain') or 0)} / {F.precip(r.get('snow') or 0)}"))
    for key, label in (("qpf", "NWS rainfall"), ("snowfall", "NWS snowfall"), ("ice", "NWS ice")):
        blk = r.get(key)
        if blk and blk[0] is not None and (key == "qpf" or blk[0] > 0):
            pairs.append((label, f"{F.precip(blk[0])}, {F.hour(blk[1])} to {F.hour(blk[1] + blk[2])}"))
    if r.get("snow_level") is not None and r.get("temp") is not None and r["temp"] < 7:
        pairs.append(("Snow level", F.height(r["snow_level"])))
    cl = F.pct(r.get("clouds"))
    if owm and r.get("sky") is not None:
        cl += f"  (NWS {r['sky']:.0f}%)"
    pairs.append(("Cloud cover", cl))
    if r.get("ceiling") is not None:
        # NWS gives a negative height when there's no ceiling (no broken or overcast layer)
        pairs.append(("Cloud ceiling", F.height(r["ceiling"]) if r["ceiling"] > 0 else "none"))
    if r.get("uvi") is not None:
        lab, sty = uv_cat(r["uvi"])
        pairs.append(("UV index", [(f"{r['uvi']:.1f}", "value"), ("  " + lab, sty)]))
    if r.get("press") is not None:
        pairs.append(("Pressure", F.press(r["press"])))
    if r.get("vis") is not None:
        pairs.append(("Visibility", F.vis(r["vis"])))
    if r.get("mixing") is not None:
        pairs.append(("Mixing height", F.height(r["mixing"])))
    if r.get("transport") is not None:
        pairs.append(("Transport wind", F.wind(r["transport"])))
    if r.get("fire_danger") is not None:
        pairs.append(("Grass fire index", f"{r['fire_danger']:.0f}"))
    if r.get("red_flag") is not None and r["red_flag"] > 0:
        pairs.append(("Red flag threat", f"{r['red_flag']:.0f}", "warn"))
    return head + kv_grid(pairs, W)


def render_hourly(m, v, W, H, printing=False):
    F = v.fmt
    rows = m.get("hourly") or []
    L = []
    if not rows:
        L.append([("  The hourly forecast hasn't loaded yet.", "dim")])
        L += ui.error_lines(m, ("owm_onecall", "nws_points", "nws_hourly", "om_forecast"), W)
        return {"lines": L}
    wide = W >= 140 and not printing
    side_w = max(44, min(60, W // 3)) if wide else 0
    main_w = W - side_w - 1 if side_w else W
    sel = max(0, min(v.sel.get("hourly", 0), len(rows) - 1))
    srcs = []
    if m.get("oc_ok"):
        srcs.append(f"OpenWeather One Call {m.get('oc_version')}")
    if any(r.get("src") == "nws" for r in rows):
        srcs.append("NWS hourly + gridpoints")
    if any(r.get("src") == "om" or r.get("om_temp") is not None for r in rows):
        srcs.append("Open-Meteo")
    L.append(ui.section(f"Next {len(rows)} hours", note=", ".join(srcs)))
    chart_rows = 9 if H >= 40 else 7 if H >= 30 else 5      # the hours scroll by under it
    if v.gmode in ("kitty", "sixel") and main_w >= 60 and not printing:
        cols = main_w - 4
        ch = v.images.block("hourlychart", f"hchart:{m.get('updated')}:{F.imp}:{len(rows)}:{v.theme.name}:{v.theme.rgb('card_bg')}",
                            cols, chart_rows,
                            lambda mode, w, h: hourly_chart(rows, F, w, h, v.theme.rgb("card_bg"), theme=v.theme) if mode == "pixels"
                            else hourly_text(rows, F, w, h, mode == "ascii"), back=v.theme.rgb("card_bg"))
        L += [[("  ", "text")] + ln for ln in ch]
    else:
        tl = hourly_text(rows, F, min(main_w - 4, len(rows) * 2 + 6), chart_rows - 1, v.ascii)
        L += [[("  ", "text")] + ln for ln in tl]
    temps = [r.get("temp") for r in rows]
    tv = [(t, r["t"]) for t, r in zip(temps, rows) if t is not None]
    if tv:
        hi, lo = max(tv), min(tv)
        pops = [(r.get("pop") or 0, r["t"]) for r in rows]
        peak = max(pops)
        note = (f"best rain chance {peak[0]:.0f}% at {F.hour(peak[1])}" if peak[0] >= 5
                else "little or no rain expected")
        L.append([("  ", "text"), (f"High {F.t(hi[0])} at {F.hour(hi[1])}, low {F.t(lo[0])} at {F.hour(lo[1])}; {note}", "dim")])
    # the table in a card of its own; its rows keep the column the ▸ marker goes in
    cols = fit_columns(unit_cols(HOURLY_COLS, F), main_w - 4, "desc", 32)
    L.append(ui.section("Hour by hour", note="↑ ↓ pick an hour, PgUp PgDn or the bar at the right to scroll"
                        if not printing else "", indent=0))
    L.append(table_header(cols))
    L.append([("  " + "─" * table_width(cols), "faint")])
    sticky = len(L)
    cursor = None
    rowmap = {}
    for i, r in enumerate(rows):
        d = F.local(r["t"])
        stamp = F.hour(r["t"])
        if i == 0 or d.hour == 0:
            stamp = f"{d:%a} {stamp}"
        amt = sum_opt(r.get("rain"), r.get("snow"))
        uvi = r.get("uvi")
        cells = {
            "time": (stamp, "label"),
            "temp": (F.t(r.get("temp")), temp_style(r.get("temp"))),
            "feels": (F.t(r.get("feels")), temp_style(r.get("feels"))),
            "desc": (cap(r.get("desc")), "warn" if r.get("hazards") else "text"),
            "pop": (F.pct(r.get("pop")), pop_style(r.get("pop"))),
            "amt": (F.precip(amt, False), "rain" if amt else "dim"),
            "wind": (wind_text(F, r.get("wind_deg"), r.get("wind"), False), wind_style(r.get("wind"))),
            "gust": (F.wind(r.get("gust"), False), wind_style(r.get("gust"))),
            "hum": (F.pct(r.get("hum")), "text"),
            "dew": (F.t(r.get("dew")), comfort(r.get("dew"))[1]),
            "cloud": (F.pct(r.get("clouds")), "text"),
            "uv": ("--" if uvi is None else f"{uvi:.0f}", uv_cat(uvi)[1]),
            "tstm": (F.pct(r.get("thunder")), "warn" if (r.get("thunder") or 0) >= 30 else "text"),
            "wbgt": (F.t(r.get("wbgt")), temp_style(r.get("wbgt"))),
            "press": (F.press(r.get("press"), False), "text"),
            "vis": (F.vis(r.get("vis"), False), "text"),
        }
        chosen = i == sel and not printing
        line = table_row(cols, cells, prefix=("▸ " if chosen else "  ", "accent"))
        if chosen:
            line = [line[0]] + restyle(line[1:], "sel")
            cursor = len(L)
        else:
            line = ui.zebra(line, main_w - 4, len(rowmap))
        rowmap[len(L)] = i
        L.append(line)
    out = ui.recard({"lines": L, "sticky": sticky, "cursor": cursor, "rowmap": rowmap}, main_w, v.ascii)
    out["scrollbar"] = not printing
    if not printing:
        r = rows[sel]
        title = f"{F.local(r['t']):%A} {F.hour(r['t'])}"
        if side_w:
            sw = side_w - 2
            det = [ui.section(title, sw)] + hour_detail(r, F, sw)
            det += hourly_highlights(rows, F, sw)
            det += best_outside(m, rows, F, sw)
            out["side"], out["side_w"] = ui.cardify(det, side_w, v.ascii)[0], side_w
        else:
            out["footer_title"] = title
            out["footer"] = hour_detail(r, F, W - 2)
    return out


def hourly_highlights(rows, F, width):
    out = [ui.section("Highlights", width, f"next {len(rows)} hours")]
    temps = [(r["temp"], r["t"]) for r in rows if r.get("temp") is not None]
    if temps:
        hi, lo = max(temps), min(temps)
        out.append(ui.kv("Warmest", [(F.t(hi[0]), temp_style(hi[0])), (f"  {F.local(hi[1]):%a} {F.hour(hi[1])}", "dim")], width, 14))
        out.append(ui.kv("Coolest", [(F.t(lo[0]), temp_style(lo[0])), (f"  {F.local(lo[1]):%a} {F.hour(lo[1])}", "dim")], width, 14))
    wind = [(first(r.get("gust"), r.get("wind")) or 0, r) for r in rows]
    if wind:
        g, r = max(wind, key=lambda x: x[0])
        if g >= 0.5:
            out.append(ui.kv("Windiest", [(wind_text(F, r.get("wind_deg"), g), wind_style(g)),
                                          (f"  {F.local(r['t']):%a} {F.hour(r['t'])}", "dim")], width, 14))
    pops = [(r.get("pop") or 0, r["t"]) for r in rows]
    p, t = max(pops)
    out.append(ui.kv("Rain chance", [(f"{p:.0f}%", pop_style(p)), (f"  peak {F.local(t):%a} {F.hour(t)}" if p >= 5 else "  low all period", "dim")], width, 14))
    hums = [r.get("hum") for r in rows if r.get("hum") is not None]
    if hums:
        out.append(ui.kv("Humidity", f"{min(hums):.0f}% to {max(hums):.0f}%", width, 14))
    dews = [r.get("dew") for r in rows if r.get("dew") is not None]
    if dews:
        lab, sty = comfort(max(dews))
        out.append(ui.kv("Muggiest", [(F.t(max(dews)), sty), (f"  dew point, {lab.lower()}", "dim")], width, 14))
    return out


def best_outside(m, rows, F, width):
    """Daylight hours that are comfortable: mild, fairly dry and not too windy."""
    out = [ui.section("Best times outside", width)]
    good = []
    for r in rows:
        t = r.get("temp")
        tf = t * 9 / 5 + 32 if t is not None else None
        day = r.get("is_day")
        if day is None:
            sr, ss, _ = ui.sun_pair(m, F, F.local(r["t"]).date())
            day = bool(sr and ss and sr <= r["t"] < ss)
        ok = (tf is not None and day and 55 <= tf <= 85 and (r.get("pop") or 0) < 25
              and (first(r.get("gust"), r.get("wind")) or 0) < 8.9)
        good.append((r["t"], ok, tf))
    spans = []
    for t, ok, tf in good:
        if ok and spans and spans[-1][1] == t - 3600:
            spans[-1][1] = t
            spans[-1][2].append(tf)
        elif ok:
            spans.append([t, t, [tf]])
    spans = [s_ for s_ in spans if s_[1] - s_[0] >= 3600]
    if not spans:
        out += wrap("No stretch of mild, dry, calm daylight in the next couple of days.", width, "dim", 2)
        return out
    for a, b, tfs in spans[:4]:
        avg = sum(tfs) / len(tfs)
        feel = "cool" if avg < 62 else "pleasant" if avg < 76 else "warm"
        out.append(clip([("  " + f"{F.local(a):%a}".ljust(5), "label"),
                         (f"{F.hour(a)} to {F.hour(b + 3600)}", "good"), (f"   {feel}", "dim")], width))
    out += wrap(f"Daylight, {F.t(12.8)} to {F.t(29.4)}, rain chance under 25% and wind under {F.wind(8.9)}.",
                width, "faint", 2)
    return out


# ----------------------------------------------------------------------------
# 3 10-Day
# ----------------------------------------------------------------------------

DAILY_COLS = [
    ("day", "Day", 9, "l", 0),
    ("hi", "High", 4, "r", 0),
    ("lo", "Low", 4, "r", 0),
    ("bar", "", 14, "l", 3),
    ("desc", "Conditions", 18, "l", 0),
    ("pop", "Rain", 4, "r", 0),
    ("amt", "Amt {precip}", 6, "r", 2),
    ("wind", "Wind {wind}", 10, "l", 1),
    ("gust", "Gust", 4, "r", 4),
    ("hum", "Hum", 4, "r", 4),
    ("dew", "Dew", 4, "r", 4),
    ("uv", "UV", 3, "r", 2),
    ("risk", "HeatRisk", 10, "l", 5),
    ("sun", "Sunrise-sunset", 15, "l", 6),
    ("moon", "Moon", 15, "l", 7),
]


def range_bar(lo, hi, gmin, gmax, width, ascii_only=False, look=None):
    if lo is None or hi is None or gmin is None or width < 3:
        return [(" " * width, "text")]
    span = (gmax - gmin) or 1.0
    a = int(round((lo - gmin) / span * (width - 1)))
    b = max(a, int(round((hi - gmin) / span * (width - 1))))
    if look:                      # the meter look (Settings, Meters): only the lit segments drawn
        return [(" " * a, "text")] + meter_block(b - a + 1, temp_style(hi), look) + [(" " * (width - b - 1), "text")]
    dot, bar = (".", "=") if ascii_only else ("·", "━")
    return [(dot * a, "faint"), (bar * (b - a + 1), temp_style(hi)), (dot * (width - b - 1), "faint")]


def range_pill(v, i, r, gmin, gmax, width, chosen, n, now=None):
    """A day's low-to-high as a picture (a pill shaded through its temperatures' colors,
    along the days' shared scale) where the terminal draws pictures: the table cell's spans,
    on the row's own background (the selection's, a lighter stripe's, or the card's). None
    elsewhere, for the text bar."""
    images = getattr(v, "images", None)
    if (images is None or not getattr(images, "pixels", False) or not gfx.HAVE_PIL or getattr(v, "printing", False)
            or r.get("lo") is None or r.get("hi") is None or gmin is None or width < 3):
        return None
    th = v.theme
    back = th.spec("sel")[1] if chosen else th.pal["stripe"] if n % 2 else th.rgb("card_bg")
    lo, hi = r["lo"], r["hi"]
    key = f"range:{lo}:{hi}:{gmin}:{gmax}:{now}:{back}:{th.name}"

    def build(mode, w, h):
        return range_image(lo, hi, gmin, gmax, w, h, back, theme=th, now=now) if mode == "pixels" else None
    return images.block(f"dayrange{i}x{width}", key, width, 1, build, back=back)[0]


def day_moon(r, tz):
    frac = r.get("moon_phase")
    if frac is None:
        d = r["date"]
        noon = dt.datetime(d.year, d.month, d.day, 12, tzinfo=tz) if tz else dt.datetime(d.year, d.month, d.day, 12)
        age, _illum, name = moon_info(noon.timestamp())
        return name, age / SYNODIC
    return moon_name(frac), frac


def nws_period_line(p, F):
    wind = f"{p['dir']} {nws_wind_range(F, p.get('speed'))}".strip()
    if p.get("gust_ms"):
        wind += f", gusts {F.wind(p['gust_ms'])}"
    bits = [F.t(p["temp"]), f"precip {F.pct(p['pop'])}", f"wind {wind or '--'}"]
    if p.get("trend"):
        bits.append(f"temperature {p['trend']}")
    return "   " + "   ".join(bits)


def day_detail(r, F, W, m):
    L = []
    if r.get("error"):
        L += wrap(f"Long-range data for this day didn't load: {r['error']}", W, "warn")
    if r.get("hazards"):
        L.append([("  NWS hazards     ", "label"), (", ".join(r["hazards"]), "warn")])
    if r.get("summary"):
        L += labeled_wrap("OpenWeather", r["summary"], W)
    for key in ("nws_day", "nws_night"):
        p = r.get(key)
        if not p:
            continue
        L.append([])
        L.append([("  " + p["name"], "title")])
        L.append([(nws_period_line(p, F), "dim")])
        L += wrap(p["text"], min(W, 120), "text", 4)
    if r.get("src") == "long-range":
        L.append([])
        L += wrap("This day comes from OpenWeather's long-range daily aggregation, so it has less "
                  "detail and more uncertainty than the days before it.", W, "dim")
    if r.get("src") == "Open-Meteo":
        L.append([])
        if m.get("pts"):
            L += wrap("This day comes from the Open-Meteo model blend beyond the NWS forecast range, so expect "
                      "more uncertainty.", W, "dim")
        else:
            L += wrap("This day comes from the Open-Meteo model blend; forecasts further ahead carry "
                      "more uncertainty.", W, "dim")
    if r.get("partial"):
        L += wrap("OpenWeather's free 3-hour forecast only covers part of this day.", W, "dim")
    temps = [("morning", "morn"), ("afternoon", "noon"), ("evening", "eve"), ("night", "night")]
    L.append([])
    if any(r.get(k) is not None for _, k in temps):
        seg = [("  " + "Temperature".ljust(16), "label")]
        for name, k in temps:
            seg += [(name + " ", "label"), (F.t(r.get(k)), temp_style(r.get(k))), ("   ", "text")]
        L.append(seg)
    dew_lab, dew_sty = comfort(r.get("dew"))
    pairs = [
        ("High / low", f"{F.t(r.get('hi'))} / {F.t(r.get('lo'))}"),
        ("Feels like", f"{F.t(r.get('f_hi'))} / {F.t(r.get('f_lo'))}") if r.get("f_hi") is not None else None,
        ("HeatRisk", heatrisk_seg(r["heatrisk"])) if r.get("heatrisk") is not None else None,
        ("Precip chance", F.pct(r.get("pop")), pop_style(r.get("pop"))),
        ("Rain", F.precip(r.get("rain")) + ("  (Open-Meteo)" if r.get("rain_src") else "")) if r.get("rain") is not None else None,
        ("Snow", F.precip(r.get("snow"))) if r.get("snow") else None,
        ("Precip hours", f"{r['precip_hours']:.0f}") if r.get("precip_hours") else None,
        ("Wind", wind_text(F, r.get("wind_deg"), r.get("wind")), wind_style(r.get("wind"))),
        ("Gusts", F.wind(r.get("gust"))) if r.get("gust") is not None else None,
        ("Humidity", F.pct(r.get("hum"))) if r.get("hum") is not None else None,
        ("Dew point", [(F.t(r.get("dew"), True), "value"), ("  " + dew_lab, dew_sty)]) if r.get("dew") is not None else None,
        ("Pressure", F.press(r.get("press"))) if r.get("press") is not None else None,
        ("Cloud cover", F.pct(r.get("clouds"))) if r.get("clouds") is not None else None,
        ("Sunshine", F.duration(r["sunshine"])) if r.get("sunshine") is not None else None,
    ]
    if r.get("uvi") is not None:
        lab, sty = uv_cat(r["uvi"])
        pairs.append(("UV index", [(f"{r['uvi']:.1f}", "value"), ("  " + lab, sty)]))
    sr, ss = r.get("sunrise"), r.get("sunset")
    if not sr or not ss:
        sr, ss, _how = ui.sun_pair(m, F, r["date"])
    pairs.append(("Sunrise/sunset", f"{F.time(sr)} / {F.time(ss)}"))
    pairs.append(("Daylight", F.duration(ss - sr) if sr and ss else "--"))
    if r.get("moonrise") or r.get("moonset"):
        pairs.append(("Moonrise/set", f"{F.time(r.get('moonrise'))} / {F.time(r.get('moonset'))}"))
    pairs.append(("Moon", day_moon(r, F.tz)[0]))
    L += kv_grid([p for p in pairs if p], W)
    return L


def render_daily(m, v, W, H, printing=False):
    F = v.fmt
    rows = m.get("daily") or []
    L = []
    if not rows:
        L.append([("  The extended forecast hasn't loaded yet.", "dim")])
        L += ui.error_lines(m, ("owm_onecall", "owm_forecast", "nws_points", "nws_forecast", "om_forecast"), W)
        return {"lines": L}
    wide = W >= 140 and not printing
    side_w = max(46, min(64, W // 3)) if wide else 0
    main_w = W - side_w - 1 if side_w else W
    sel = max(0, min(v.sel.get("daily", 0), len(rows) - 1))
    srcs = sorted({r.get("src") for r in rows if r.get("src")}, key=lambda s: (s != "NWS", s))
    strip = ui.extended_strip(v, m, main_w - 2, None, "dayicon", bg_role="card_bg") if not printing else []
    if strip and H >= 30:
        L.append(ui.section(f"{len(rows)}-day forecast", note=", ".join(srcs), indent=1))
        L += strip
    # the table in a card of its own; its rows keep the column the ▸ marker goes in
    L.append(ui.section("Day by day" if strip and H >= 30 else f"{len(rows)}-day forecast",
                        note="↑ ↓ pick a day" if strip and H >= 30 else ", ".join(srcs), indent=0))
    # a column no day has a value for (humidity and dew point when only NWS reports) stays out
    have = {"gust": "gust", "hum": "hum", "dew": "dew", "uv": "uvi", "risk": "heatrisk"}
    empty = {c for c, k in have.items() if not any(r.get(k) is not None for r in rows)}
    cols = fit_columns([c for c in unit_cols(DAILY_COLS, F) if c[0] not in empty], main_w - 4, "desc", 34)
    his = [r["hi"] for r in rows if r.get("hi") is not None]
    los = [r["lo"] for r in rows if r.get("lo") is not None]
    temps = los + his
    gmin, gmax = (min(temps), max(temps)) if temps else (None, None)
    barw = next((c[2] for c in cols if c[0] == "bar"), 0)
    L.append(table_header(cols))
    L.append([("  " + "─" * table_width(cols), "faint")])
    sticky = len(L) if H >= 40 or not strip else 0
    cursor = None
    rowmap = {}
    for i, r in enumerate(rows):
        amt = sum_opt(r.get("rain"), r.get("snow"))
        sr, ss = r.get("sunrise"), r.get("sunset")
        if not sr or not ss:
            sr, ss, _how = ui.sun_pair(m, F, r["date"])
        uvi = r.get("uvi")
        faded = r.get("src") in ("long-range", "Open-Meteo")
        risk = r.get("heatrisk")
        desc = cap(r.get("desc")) or ("unavailable" if r.get("error") else "--")
        cells = {
            "day": (F.day_label(r["date"]), "label"),
            "hi": (F.t(r.get("hi")), temp_style(r.get("hi"))),
            "lo": (F.t(r.get("lo")), temp_style(r.get("lo"))),
            "bar": range_pill(v, i, r, gmin, gmax, barw, i == sel and not printing, len(rowmap),
                              (m.get("now") or {}).get("temp") if r["date"] == F.today() else None)
            or range_bar(r.get("lo"), r.get("hi"), gmin, gmax, barw, v.ascii, v.meter_look()),
            "desc": (("▲ " + desc) if r.get("hazards") else desc,
                     "warn" if r.get("hazards") else "dim" if faded else "text"),
            "pop": (F.pct(r.get("pop")), pop_style(r.get("pop"))),
            "amt": (F.precip(amt, False), "rain" if amt else "dim"),
            "wind": (wind_text(F, r.get("wind_deg"), r.get("wind"), False), wind_style(r.get("wind"))),
            "gust": (F.wind(r.get("gust"), False), wind_style(r.get("gust"))),
            "hum": (F.pct(r.get("hum")), "text"),
            "dew": (F.t(r.get("dew")), comfort(r.get("dew"))[1]),
            "uv": ("--" if uvi is None else f"{uvi:.0f}", uv_cat(uvi)[1]),
            "risk": heatrisk_seg(risk, short=True) if risk is not None else ("--", "dim"),
            "sun": (f"{F.time(sr)}-{F.time(ss)}".replace(" AM", "a").replace(" PM", "p"), "text"),
            "moon": (day_moon(r, F.tz)[0], "text"),
        }
        chosen = i == sel and not printing
        line = table_row(cols, cells, prefix=("▸ " if chosen else "  ", "accent"))
        if chosen:
            line = [line[0]] + restyle(line[1:], "sel")
            cursor = len(L)
        else:
            line = ui.zebra(line, main_w - 4, len(rowmap))
        rowmap[len(L)] = i
        L.append(line)
    ins = outlook_insights(m, rows, F, main_w - 2)
    if ins and (printing or H - len(L) >= len(ins) + 4):
        L += ins
    room = H - len(L) - 8
    if not printing and room >= 7 and main_w >= 50:
        crow = min(12, room - 2)
        L.append(ui.section("Highs, lows and rain chances", note=f"next {len(rows)} days"))
        if v.gmode in ("kitty", "sixel"):
            ch = v.images.block("dailychart", f"dchart:{m.get('updated')}:{F.imp}:{len(rows)}:{v.theme.name}:{v.theme.rgb('card_bg')}",
                                main_w - 4, crow,
                                lambda mode, w, h: daily_chart(rows, F, w, h, v.theme.rgb("card_bg"), theme=v.theme) if mode == "pixels"
                                else daily_text(rows, F, w, h, mode == "ascii"), back=v.theme.rgb("card_bg"))
        else:
            ch = daily_text(rows, F, main_w - 4, crow, v.ascii)
        L += [[("  ", "text")] + ln for ln in ch]
    if printing:
        for r in rows:
            L.append(ui.section(f"{r['date']:%A, %B} {r['date'].day}", note=r.get("src") or ""))
            L += day_detail(r, F, W - 2, m)
    out = ui.recard({"lines": L, "sticky": sticky, "cursor": cursor, "rowmap": rowmap}, main_w, v.ascii)
    if not printing:
        r = rows[sel]
        title = f"{r['date']:%A, %B} {r['date'].day}"
        if side_w:
            sw = side_w - 2
            det = [ui.section(title, sw, r.get("src") or "")]
            ic = ui.icon_block(v, r.get("kind"), True, 13, 5, slot="daydetail", bg_role="card_bg")
            info = [[(cap(r.get("desc")) or "", "value")],
                    [("High ", "label"), (F.t(r.get("hi")), temp_style(r.get("hi"))), ("  Low ", "label"),
                     (F.t(r.get("lo")), temp_style(r.get("lo")))],
                    [("Precip ", "label"), (F.pct(r.get("pop")), pop_style(r.get("pop")))]]
            for k in range(5):
                det.append([("  ", "text")] + padto(ic[k], 14) + (info[k] if k < len(info) else []))
            det += [[]] + day_detail(r, F, sw, m)
            hrs = day_hours(m, F, r["date"])
            if hrs:
                det += [ui.section("Hour by hour", sw, "every 3 hours")]
                for h_ in hrs:
                    det.append(clip([("  " + F.hour(h_["t"]).rjust(5) + "  ", "label"),
                                     (F.t(h_.get("temp")).rjust(4), temp_style(h_.get("temp"))),
                                     ("  " + (icons.label(h_.get("kind")) or cap(h_.get("desc") or ""))[:15].ljust(15), "text"),
                                     (F.pct(h_.get("pop")).rjust(5), pop_style(h_.get("pop"))),
                                     ("  " + wind_text(F, h_.get("wind_deg"), h_.get("wind")), wind_style(h_.get("wind")))],
                                    sw))
            out["side"], out["side_w"] = ui.cardify(det, side_w, v.ascii)[0], side_w
        else:
            out["footer_title"] = f"{title}   {r.get('src') or ''}"
            out["footer"] = day_detail(r, F, W - 2, m)
    return out


def day_hours(m, F, date, step=3):
    """Hourly rows for one calendar day (merged forecast first, Open-Meteo beyond it)."""
    rows = [r for r in m.get("hourly") or [] if r.get("t") and F.local(r["t"]).date() == date]
    if len(rows) < 6:
        om = (m.get("om") or {}).get("hourly") or {}
        more = [om[t] for t in sorted(om) if F.local(t).date() == date]
        if len(more) > len(rows):
            rows = more
    now = time.time() - 1800
    return [r for r in rows if F.local(r["t"]).hour % step == 0 and r["t"] >= now]


def outlook_insights(m, rows, F, width):
    """Plain-language takeaways from the daily forecast."""
    if not rows:
        return []
    out = [ui.section("The outlook at a glance")]
    his = [(r["hi"], r) for r in rows if r.get("hi") is not None]
    los = [(r["lo"], r) for r in rows if r.get("lo") is not None]
    if his:
        t, r = max(his, key=lambda x: x[0])
        out.append([("  Warmest      ", "label"), (f"{F.day_label(r['date'])}  {F.t(t)}", temp_style(t))])
    if los:
        t, r = min(los, key=lambda x: x[0])
        out.append([("  Coolest      ", "label"), (f"{F.day_label(r['date'])} night  {F.t(t)}", temp_style(t))])
    wet = [r for r in rows if (r.get("pop") or 0) >= 30]
    if wet:
        r = wet[0]
        out.append([("  Next rain    ", "label"), (f"{F.day_label(r['date'])}  {r['pop']:.0f}%  ", "rain"),
                    (cap(r.get("desc") or ""), "text")])
    else:
        out.append([("  Next rain    ", "label"), ("none likely in this forecast", "good")])
    tot = sum((r.get("rain") or 0) + (r.get("snow") or 0) for r in rows)
    if tot > 0.2:
        out.append([("  Total precip ", "label"), (f"about {F.precip(tot)} over {len(rows)} days", "rain")])
    gusty = [(r.get("gust") or r.get("wind") or 0, r) for r in rows]
    g, r = max(gusty, key=lambda x: x[0])
    if g >= 9:
        out.append([("  Windiest     ", "label"), (f"{F.day_label(r['date'])}  gusts {F.wind(g)}", "warn")])
    frost = [r for r in rows if r.get("lo") is not None and r["lo"] <= 1.5]
    if frost:
        out.append([("  Frost risk   ", "label"), (", ".join(F.day_label(r["date"]) for r in frost[:5]), "t_cold")])
    hot = [r for r in rows if r.get("hi") is not None and r["hi"] >= 32.2]
    if hot:
        out.append([("  Heat         ", "label"), (", ".join(F.day_label(r["date"]) for r in hot[:5]) + f" at {F.t(32.2)} or more", "t_scorch")])
    a = m.get("almanac") or {}
    if a.get("normal_hi") is not None and his:
        diffs = [h - a["normal_hi"] for h, _r in his[:7]]
        avg = sum(diffs) / len(diffs)
        word = "above" if avg > 1 else "below" if avg < -1 else "near"
        out.append([("  Vs normal    ", "label"), (f"highs average {F.tdelta(avg)} ({word} the normal {F.t(a['normal_hi'])})",
                                                  "warn" if avg > 3 else "rain" if avg < -3 else "text")])
    return out


# ----------------------------------------------------------------------------
# 6 Alerts
# ----------------------------------------------------------------------------

DESC_LABELS = {"WHAT": "What", "WHERE": "Where", "WHEN": "When", "IMPACTS": "Impacts", "IMPACT": "Impact",
               "HAZARD": "Hazard", "SOURCE": "Source", "ADDITIONAL DETAILS": "Details",
               "LOCATIONS IMPACTED INCLUDE": "Locations", "LOCATIONS IMPACTED": "Locations",
               "PRECAUTIONARY/PREPAREDNESS ACTIONS": "What to do"}
ALERT_LW = 11            # an alert card's label column: "What to do" and a space


def alert_parts(text):
    """An alert's text as [(label, text)]: the National Weather Service's "* WHAT...",
    "HAZARD..." paragraphs under their labels, the rest (label "") as paragraphs, with a
    sentence that NWS breaks over two of them ("...has issued a" / "* Flood Warning for...")
    joined up again."""
    out = []
    for para in re.split(r"\n\s*\n", text or ""):
        flat = " ".join(para.split())
        if not flat or flat in ("&&", "$$"):
            continue
        mt = re.match(r"\*?\s*([A-Z][A-Z /&'-]{1,40}?)\s*\.\.\.\s*(.*)$", flat)
        if mt and mt.group(2) and len(mt.group(1).split()) <= 4:
            label = mt.group(1).strip()
            out.append((DESC_LABELS.get(label, label.capitalize()), mt.group(2)))
            continue
        flat = re.sub(r"\s*\.\.\.$", ".", re.sub(r"^\*\s*", "", flat))
        if out and not out[-1][0] and out[-1][1][-1:].isalpha():
            out[-1] = ("", out[-1][1] + " " + flat)
        else:
            out.append(("", flat))
    return out


def area_rows(area, cw, lw=ALERT_LW, rows=3):
    """An alert's counties and zones in at most `rows` lines, "and 12 more" at the end of
    a list too long for them."""
    names = [x.strip() for x in (area or "").split(";") if x.strip()]
    if not names:
        return []
    budget = rows * (cw - lw) - 18
    k, used = 0, 0
    while k < len(names) and used + len(names[k]) + 2 <= budget:
        used += len(names[k]) + 2
        k += 1
    k = max(1, k)
    while True:
        text = "; ".join(names[:k]) + (f" and {len(names) - k} more" if k < len(names) else "")
        out = labeled_wrap("Areas", text, cw + 1, lw=lw, style="dim", indent=0)
        if len(out) <= rows or k == 1:
            return out
        k -= 1


def alert_body(a, F, cw):
    """One alert's card: the headline; when, from whom; the threats; the description in its
    labelled parts (What, Where, When, Impacts); what to do; the areas it covers."""
    lw = ALERT_LW
    out = []
    head = a.get("nws_headline") or a.get("headline")
    if head:
        out += wrap(head, cw + 1, "value", 0)
    span = f"{F.dtime(a.get('start'))} to {F.dtime(a.get('end'))}" if a.get("end") else F.dtime(a.get("start"))
    sender = a.get("sender") or ""
    if sender and lw + len(span) + 3 + lw + len(sender) <= cw:
        out.append([("In effect".ljust(lw), "label"), (span, "value"), ("   " + "Issued by".ljust(lw), "label"),
                    (sender, "text")])
    else:
        out += labeled_wrap("In effect", span, cw + 1, lw=lw, style="value", indent=0)
        if sender:
            out += labeled_wrap("Issued by", sender, cw + 1, lw=lw, indent=0)
    threats = [f"{label.lower()} {str(val).lower()}" for label, val in a.get("extras") or [] if val]
    if threats:
        out += labeled_wrap("Threats", " · ".join(threats), cw + 1, lw=lw, style="warn", indent=0)
    for label, text in alert_parts(a.get("desc")):
        if label:
            out += labeled_wrap(label, text, cw + 1, lw=lw, indent=0)
        else:
            out += wrap(text, cw + 1, "text", 0)
    paras = [x for x in re.split(r"\n\s*\n", a.get("instruction") or "") if x.strip()]
    for i, para in enumerate(paras):
        out += labeled_wrap("What to do" if i == 0 else "", para, cw + 1, lw=lw, style="text", indent=0)
    out += area_rows(a.get("area"), cw, lw)
    if a.get("note"):
        out += labeled_wrap("Note", a["note"], cw + 1, lw=lw, style="dim", indent=0)
    return out


def fit_note(title, notes, width, chip=False):
    """The longest of `notes` that fits on a card's top edge after its title."""
    used = len(title) + (4 if chip else 3)
    return next((n for n in notes if n and used + 2 + len(n) + 3 <= width), "")


def stack(cols, widths, gap=1):
    """Columns of cards side by side, each column's cards one under the other."""
    out = []
    for r in range(max((len(c) for c in cols), default=0)):
        line = []
        for k, (col, w) in enumerate(zip(cols, widths)):
            if k:
                line.append((" " * gap, "text"))
            line += padto(col[r], w) if r < len(col) else [(" " * w, "text")]
        out.append(line)
    return out


def render_alerts(m, v, W, H, printing=False):
    """Alerts in rounded cards: one for each alert (the event in its National Weather
    Service colour on the top edge, the description's What, Where, When and Impacts in
    labelled rows), the warnings map of the state in the top right (the regional radar
    when the map can't be had), and cards for warnings nearby,
    watches and storm reports wherever there's room (each goes under the shorter column);
    a sidebar of cards (local radar, outlooks, the storm risk scale, the HWO)."""
    F = v.fmt
    asc = v.ascii
    alerts = m.get("alerts") or []
    main_w, side_w = ui.sidebar_split(W, want=46) if not printing else (W, 0)
    two = not printing and main_w >= 100
    map_w = max(44, min(64, round(main_w * 0.38))) if two else main_w
    widths = [main_w - map_w - 1, map_w] if two else [main_w]
    cards = []                      # builders: card width -> the card's lines

    def box(title, body, notes=(), style="title"):
        chip = isinstance(style, tuple)
        return lambda w: ui.card(title, body(w - 4), w, fit_note(title, notes, w, chip), asc, title_style=style)

    n = len(alerts)
    for k, a in enumerate(alerts):
        rgb = a.get("rgb") or (220, 40, 40)
        bits = [x for x in (a.get("severity"), a.get("urgency"), a.get("certainty")) if x and x != "Unknown"]
        if a.get("msgtype") == "Update":
            bits.append("updated")
        count = f"{k + 1} of {n}" if n > 1 else ""
        src = "" if a.get("src") == "NWS" else a.get("src") or ""
        notes = (" · ".join(x for x in [count] + bits + [src] if x), " · ".join(x for x in (count, *bits[:1]) if x),
                 count)
        cards.append(box(a["event"], lambda cw, a=a: alert_body(a, F, cw), notes, ("rgb", ink_on(rgb), rgb, "b")))
    if not alerts:
        why = ((m.get("off") or {}).get("nws_alerts") and "the National Weather Service source is turned off in Settings"
               or (m.get("notes") or {}).get("nws_alerts")
               or (m["errors"].get("nws_alerts") and f"the alerts check failed ({m['errors']['nws_alerts']})")
               or (m["errors"].get("nws_points") and f"the NWS point lookup failed ({m['errors']['nws_points']})"))
        zip_ = ((m.get("loc") or {}).get("zip") or "").strip()

        def calm(cw):
            if why:
                return wrap(f"Alerts aren't available: {why}.", cw + 1, "warn", 0)
            if not m.get("alerts_at"):
                return [[("Checking for watches, warnings and advisories…", "dim")]]
            mark = "ok " if asc else "✓ "
            out = wrap(mark + f"No watches, warnings or advisories for {f'ZIP code {zip_}' if zip_ else 'your location'}.",
                       cw + 1, "good", 0)
            return out + wrap(f"Checked with the National Weather Service at {F.time(m['alerts_at'])}.", cw + 1, "dim", 0)
        cards.append(box("Alerts", calm, ("National Weather Service",)))

    sbw = [w for w in m.get("sbw") or [] if (w.get("dist") or 0) <= 150]
    if sbw:
        def nearby(cw):
            out = []
            for w in sbw[:10]:
                where = "you're inside" if w["inside"] else f"{F.dist(w['dist'])} {compass(w['bearing'])}"
                tags = []
                if w.get("tornado"):
                    tags.append(f"tornado {str(w['tornado']).lower()}")
                if w.get("hail"):
                    tags.append(f"hail {F.precip(w['hail'] * 25.4)}")
                if w.get("wind"):
                    tags.append(f"wind {F.wind(w['wind'] * 0.44704)}")
                if w.get("emergency"):
                    tags.append("EMERGENCY")
                until = f"until {F.time(w['expire'])}" if w.get("expire") else ""
                bar = ("▌" if not asc else "|", ("rgb", w["rgb"], None, "b"))
                if cw >= 84:
                    out.append(clip([bar, (f"{w['name']:<28}", "value"), (f"{where:<15}", "bad" if w["inside"] else "text"),
                                     (until, "dim"), (("   " + ", ".join(tags)) if tags else "", "warn")], cw))
                    continue
                name = w["name"][:max(8, cw - len(where) - 3)]
                out.append(clip([bar, (name.ljust(cw - 1 - len(where)), "value"),
                                 (where, "bad" if w["inside"] else "dim")], cw))
                sub = " · ".join(x for x in (until, ", ".join(tags)) if x)
                if sub:
                    out.append(clip([("  " + sub, "warn" if tags else "dim")], cw))
            if len(sbw) > 10:
                out.append([(f"+ {len(sbw) - 10} more on the radar", "dim")])
            return out
        cards.append(box("Warnings nearby", nearby, ("storm-based, within 150 mi", "within 150 mi")))
    wat = [w for w in m.get("watches") or [] if (w.get("dist") or 0) <= 250]
    if wat:
        def watches(cw):
            out = []
            for w in wat:
                where = "covers you" if w["inside"] else f"{F.dist(w['dist'])} away"
                until = f"until {F.time(w['expire'])}" if w.get("expire") else ""
                bar = ("▌" if not asc else "|", ("rgb", w["rgb"], None, "b"))
                tail = f"{where}   {until}".rstrip()
                out.append(clip([bar, (w["name"][:max(8, cw - len(tail) - 3)].ljust(cw - 1 - len(tail)), "value"),
                                 (where, "bad" if w["inside"] else "dim"), (f"   {until}" if until else "", "dim")], cw))
            return out
        cards.append(box("SPC watches", watches, ("within 250 mi",)))
    ol = m.get("outlooks") or []
    if ol and not side_w and not printing:          # the sidebar shows them otherwise
        def outlooks(cw):
            out, today = [], F.today()
            for o in ol:
                day = today + dt.timedelta(days=(o["day"] or 1) - 1)
                lab = "Today" if day == today else "Tomorrow" if day == today + dt.timedelta(days=1) else f"{day:%A}"
                lvl = o.get("level") and o["category"] == "CATEGORICAL" and o["type"] == "C"
                out.append(clip([(lab.ljust(10), "label"), (o["kind"].ljust(16), "text"), (o["word"], o["style"]),
                                 ((f"  ({o['level']} of 5)" if lvl else ""), "dim")], cw))
            return out
        cards.append(box("Outlooks for your point", outlooks, ("SPC and WPC",)))
    lsrs = m.get("lsrs") or []
    if lsrs or not printing:
        def reports(cw):
            if not lsrs:
                return [[("None within 60 miles in the last 48 hours.", "dim")]]
            out = []
            for r in lsrs[:10]:
                mag = f" {r['mag']:g} {r['unit'].lower()}" if r.get("mag") else ""
                where = f"{(r.get('city') or '').title()}, {r.get('state') or ''}".strip(", ")
                dist = f"{F.dist(r['dist'])} {compass(r['bearing'])}" if r.get("dist") is not None else ""
                when = F.dtime(r["t"])
                if cw >= 84:
                    out.append(clip([(when.ljust(17), "dim"), ((r["type"] or "") + mag, "warn"),
                                     ("  " + where, "text"), ("  " + dist, "dim")], cw))
                else:
                    out.append(clip([((r["type"] or "") + mag, "warn"), ("  " + when, "dim")], cw))
                    out.append(clip([("  " + where, "text"), ("  " + dist, "dim")], cw))
                if r.get("remark"):
                    out += wrap(r["remark"], cw + 1, "faint", 2)[:2]
            if len(lsrs) > 10:
                out.append([(f"+ {len(lsrs) - 10} more", "dim")])
            return out
        cards.append(box("Storm reports", reports, ("last 48 hours, within 60 mi", "48 hours, 60 mi")))

    def radar_card(w, cap):
        cw = w - 4
        rows = max(8, min(cap, round(cw * 0.32)))
        lines, res = ui.radar_block(v, m, cw, rows, slot="regional", mini=True, span=480)
        body = list(lines) + [ui.dedent(ui.radar_legend(cw + 2, asc, res, F.imp)[0], 2)]
        return ui.card("Regional radar", body, w, fit_note("Regional radar", (
            "480 mi across, warnings outlined", "480 mi across"), w), asc)
    smap = m.get("state_map")

    def map_card(w, cap):
        """The warnings map: the state's counties, each in the color of the most urgent
        alert covering it, the key over it (in the picture where the terminal draws them,
        in words under it elsewhere)."""
        cw = w - 4
        fills = statemap.county_fills(m.get("state_alerts") or [], smap)
        kinds = statemap.legend(fills)
        pics = getattr(v.images, "pixels", False) and gfx.HAVE_PIL
        head = 2 if pics else 0
        rows = max(8, min(cap, round(cw * statemap.aspect(smap) / 2))) + head
        loc = m.get("loc") or {}
        home = (loc["lat"], loc["lon"]) if loc.get("lat") is not None else None
        back, light = v.theme.rgb("card_bg"), bool(v.theme.light)
        ink = v.theme.rgb("value")
        key = (f"warnmap:{smap['state']}:{len(smap['counties'])}:{v.theme.name}:{back}:{home}:"
               f"{hash(tuple(sorted((g, a['key'], a.get('rank')) for g, a in fills.items())))}")

        def build(mode, pw, ph):
            if mode == "pixels":
                return statemap.map_image(smap, fills, pw, ph, back, cols=cw, rows=rows, home=home, head=head,
                                          light=light, ink=ink)
            if mode == "blocks" and gfx.HAVE_PIL:
                return statemap.map_image(smap, fills, pw, ph * 2, back, cols=pw, rows=ph * 2, home=home,
                                          labels=False, light=light, coarse=True)
            return None
        body = [] if asc else list(v.images.block("warnmap", key, cw, rows, build, back=back))
        if not pics:                            # the key in words
            line, used = [], 0
            for name, rgb, n in kinds:
                bit = [("██ " if not asc else "## ", ("rgb", tuple(rgb), None, "")),
                       (f"{statemap.short_name(name).title()} ({n})", "text"), ("   ", "text")]
                size = sum(len(t) for t, _s in bit)
                if line and used + size > cw:
                    body.append(line)
                    line, used = [], 0
                line += bit
                used += size
            if line:
                body.append(line)
            if not kinds:
                body.append([("No watches or warnings in the state.", "good")])
        n = len(fills)
        note = (f"{smap['name']} · {n} {'county' if n == 1 else 'counties'} under alerts" if n
                else f"{smap['name']} · none in effect")
        return ui.card("Warnings map", body, w, fit_note("Warnings map", (note, smap["name"]), w), asc)
    radar = (map_card if smap and smap.get("counties") else radar_card if v.radar is not None else None) \
        if not printing else None
    if two:
        cols = [[], radar(map_w, 20 if radar is map_card else 16) if radar else []]
        for i, build in enumerate(cards):
            k = 0 if i == 0 or len(cols[0]) <= len(cols[1]) else 1
            cols[k] += build(widths[k])
        L = stack(cols, widths)
    else:
        L = []
        tall = 18 if radar is map_card else 12
        for i, build in enumerate(cards):
            L += build(main_w)
            if i == 0 and radar:
                L += radar(main_w, tall)
        if not cards and radar:
            L += radar(main_w, tall)
    L += ui.error_lines(m, ("nws_alerts", "iem_outlook", "iem_lsr", "iem_sbw"), main_w)

    side = []
    if side_w or printing:
        sw = sidebar_width(side_w, W, printing)
        panels = [p for p in (ui.outlook_panel(m, F, sw), spc_scale(m, F, sw, asc)) if p]
        notes = []
        hw = hwo_summary(m.get("hwo"))
        if hw:
            office = (m.get("hwo") or {}).get("office") or ""
            notes = [ui.section("Hazardous weather outlook", sw, f"NWS {office}".strip())]
            for head, when, body in hw:
                notes.append(clip([("  " + head, "label"), (("  " + when) if when else "", "dim")], sw))
                notes += wrap(body, sw, "text", 4)
        else:
            off = m.get("office") or {}
            items = [(x["title"], x.get("text") or "") for x in off.get("stories") or [] if x.get("title")][:3]
            items += [(x["title"], "") for x in off.get("headlines") or [] if x.get("title")][:3]
            if items:
                notes = [ui.section("From your NWS office", sw, "press 9 for more")]
                for title, text in items[:4]:
                    notes += wrap(title, sw, "label", 2)
                    if text:
                        notes += wrap(text[:220] + ("…" if len(text) > 220 else ""), sw, "dim", 4)
        used = sum(len(p) + 1 for p in panels + ([notes] if notes else []))
        if v.radar is not None and not printing:
            # the local radar takes what the cards leave (8 to 18 rows); the office notes give way first
            want = max(8, min(18, int(sw * 0.42)))
            rh = max(8, min(want, H - used - 4))
            local = ui.mini_radar_panel(v, m, sw, rh)
            panels.insert(0, local)
            used += len(local) + 1
        over = used - H
        if notes and over > 0 and not printing:
            keep = max(3, len(notes) - over - 1)
            if keep < len(notes):
                notes = notes[:keep] + [[("  … more on screen 9 (NWS & Radio)", "dim")]]
        for p_ in panels + ([notes] if notes else []):
            side += p_
    return with_side(L, side, side_w, printing, W=W, ascii_only=asc, main_w=main_w)


def hwo_summary(prod):
    """The sections of a Hazardous Weather Outlook: [(heading, when, text)]."""
    text = (prod or {}).get("text") or ""
    seg = text.split("$$")[0]
    out = []
    for mt in re.finditer(r"^\.([A-Z][A-Z \-]+?)\.\.\.(.*?)$(.*?)(?=^\.[A-Z]|\Z)", seg, re.S | re.M):
        head = mt.group(1).strip().capitalize()
        when = mt.group(2).strip().rstrip(".")
        body = " ".join(mt.group(3).split())
        if body:
            out.append((head, when, body))
    return out


SPC_SCALE = [("TSTM", "Thunder", "label"), ("MRGL", "Marginal", "good"), ("SLGT", "Slight", "warn"),
             ("ENH", "Enhanced", "accent"), ("MDT", "Moderate", "bad"), ("HIGH", "High", "bad")]


def spc_scale(m, F, width, ascii_only=False):
    """Day 1-3 severe-storm risk at your point on the SPC 0-5 scale."""
    out = [ui.section("Storm risk scale", width, "SPC days 1-3")]
    today = F.today()
    best = {}
    for o in m.get("outlooks") or []:
        if o["type"] == "C" and o["category"] == "CATEGORICAL" and o["day"] in (1, 2, 3):
            lvl = [k for k, _n, _s in SPC_SCALE].index(o["threshold"]) if o["threshold"] in [k for k, _n, _s in SPC_SCALE] else -1
            best[o["day"]] = max(best.get(o["day"], -1), lvl)
    for day in (1, 2, 3):
        d = today + dt.timedelta(days=day - 1)
        lab = "Today" if day == 1 else f"{d:%a}"
        lvl = best.get(day, -1)
        segs = [("  " + lab.ljust(6), "label")]
        for i, (_k, name, sty) in enumerate(SPC_SCALE):
            mark = ("#" if ascii_only else "█") if i <= lvl else ("." if ascii_only else "·")
            segs.append((mark * 2, sty if i <= lvl else "faint"))
        segs.append(("  " + (SPC_SCALE[lvl][1] if lvl >= 0 else "none"), SPC_SCALE[lvl][2] if lvl >= 0 else "good"))
        out.append(clip(segs, width))
    out += wrap("SPC risk of severe storms: Thunder, 1 Marginal, 2 Slight, 3 Enhanced, 4 Moderate, 5 High.",
                width, "dim", 2)
    return out


# ----------------------------------------------------------------------------
# 7 Air & UV
# ----------------------------------------------------------------------------

def render_air(m, v, W, H, printing=False):
    """Air & UV as a dashboard of rounded cards: the AQI now, the pollutants and the scales;
    the AQI and PM2.5 over the past day, the AQI and the UV index ahead, the daily peaks and
    sun safety; what each pollutant is. The charts take the height that lets it all fit."""
    air = m.get("air") or {}
    if not air:
        L = [[("  Air quality hasn't loaded yet.", "dim")]]
        L += ui.error_lines(m, ("owm_air", "owm_air_fc", "om_air"), W)
        return {"lines": L}
    now = time.time()
    ncols = max(1, min(3, (W + 1) // 51))
    # the tallest charts that let it all fit; failing that, all but the pollutant guide
    tries = [air_cards(m, v, W, ncols, h, now) for h in (8, 7, 6, 5, 4, 3)]
    L = next((L for L, _main in tries if len(L) <= H), None) or \
        next((L for L, main in tries if main <= H), tries[-1][0])
    return {"lines": L}


SHORT_CATS = {"Unhealthy for sensitive groups": ("Unhealthy (sensitive)", "Sensitive groups", "Sensitive")}


def cat_label(label, room):
    """An AQI category's name in `room` cells: the long one shortened when it has to be."""
    if len(label) <= room:
        return label
    return next((x for x in SHORT_CATS.get(label, ()) if len(x) <= room), label[:room])


def centered(lines, width, cw):
    """Lines `width` wide set in the middle of `cw` cells."""
    pad = max(0, (cw - width) // 2)
    return [[(" " * pad, "text")] + ln if ln else [] for ln in lines]


def fit_chart(n, cw, gutter, axis=True):
    """(axis?, width) for n columns in a card's cw cells: the scale on the left when the
    columns still get two cells each, else none, so the columns don't shrink to slivers."""
    if axis and gutter + 2 * n <= cw:
        return True, gutter + (cw - gutter) // n * n
    return False, max(1, cw // n) * n


def air_cards(m, v, W, ncols, h, now):
    """The Air & UV screen's cards in `ncols` columns, the charts `h` rows tall: (lines,
    how many of them come before the pollutant guide)."""
    F = v.fmt
    air = m.get("air") or {}
    om = m.get("air_om") or {}
    aqi = air.get("us")
    lab, sty, advice = aqi_cat(aqi)

    def now_card(cw):
        number = str(aqi) if aqi is not None else "--"
        pic = ui.big_temp_block(v, number, sty, 5, slot="aqinum")
        big = pic[0] if pic else ui.big_temp(v, number)
        bw = pic[1] + 1 if pic else max(len(r) for r in big) + 3

        def digits(i):
            return list(big[i]) + [(" ", "text")] if pic else [(big[i].ljust(bw), sty)]
        info = [[(lab, sty)], [("US AQI", "label"), (", driven by ", "dim"), (air.get("dominant") or "--", "value")]]
        owm = int(air["owm"]) if air.get("owm") else None
        if owm:
            info.append([("OpenWeather ", "label"), (f"{owm} of 5, {OWM_AQI.get(owm, '').lower()}",
                                                     OWM_AQI_STYLE.get(owm, "value"))])
        if air.get("src") == "OpenWeather" and om.get("us") is not None:
            olab, osty, _ = aqi_cat(om["us"])
            info.append([("Open-Meteo ", "label"), (f"{om['us']} {olab.lower()}", osty)])
        if cw - bw < 24:                        # too narrow to sit beside the digits: under them
            return [digits(i) for i in range(len(big))] + info + wrap(advice, cw, "text", 0)
        side = cw - bw
        words = advice.split()
        beside, k = [], 0
        while len(info) + len(beside) < len(big) and k < len(words):
            line = words[k]
            k += 1
            while k < len(words) and len(line) + 1 + len(words[k]) <= side:
                line += " " + words[k]
                k += 1
            beside.append([(line, "text")])
        right = info + beside
        out = [digits(i) + (right[i] if i < len(right) else []) for i in range(len(big))]
        out += right[len(big):]
        return out + (wrap(" ".join(words[k:]), cw, "text", 0) if k < len(words) else [])

    def pollutants_card(cw):
        wide = cw >= 66
        cols = [("pol", "Pollutant", 22 if wide else 9, "l", 0), ("conc", "ug/m3", 6, "r", 0),
                ("conv", "Converted", 10, "r", 2), ("aqi", "AQI", 4, "r", 0), ("cat", "Category", 9, "l", 0)]
        if any(row.get("owm") for row in air.get("rows") or []):
            cols.append(("owm", "OpenWeather", 11, "l", 3))
        cols = fit_columns(cols, cw, "cat", 30, indent=0)
        catw = next(c[2] for c in cols if c[0] == "cat")
        out = [table_row(cols, {c[0]: (c[1], "label") for c in cols}, prefix=("", "text"))]
        for row in air.get("rows") or []:
            clab, csty, _ = aqi_cat(row["aqi"]) if row["aqi"] is not None else ("--", "dim", "")
            conv = "" if row["conv"] is None else (
                f"{row['conv']:.2f} {row['unit']}" if row["unit"] == "ppm" else f"{row['conv']:.1f} {row['unit']}")
            lvl = row.get("owm")
            out.append(ui.zebra(table_row(cols, {
                "pol": (f"{row['pol']:<6}{row['name'].lower()}" if wide else row["pol"], "text"),
                "conc": ("--" if row["conc"] is None else f"{row['conc']:.1f}", "value"),
                "conv": (conv, "dim"),
                "aqi": ("--" if row["aqi"] is None else str(row["aqi"]), csty),
                "cat": (cat_label(clab, catw), csty),
                "owm": (f"{lvl} {OWM_AQI[lvl].lower()}" if lvl else "--", OWM_AQI_STYLE.get(lvl, "dim")),
            }, prefix=("", "text")), cw, len(out) - 1))
        for row in air.get("extra") or []:
            out.append(table_row(cols, {
                "pol": (f"{row['pol']:<6}{row['name'].lower()}" if wide else row["pol"], "text"),
                "conc": ("--" if row["conc"] is None else f"{row['conc']:.1f}", "value"),
                "cat": ("no AQI scale", "dim")}, prefix=("", "text")))
        if om.get("dust") is not None:
            bits = [f"Dust {om['dust']:.0f} ug/m3"] + ([f"haze (AOD) {om['aod']:.2f}"] if om.get("aod") is not None
                                                        else [])
            out.append(clip([("  ·  ".join(bits), "dim")], cw))
        return out

    def scales_card(cw):
        aqi_rows = [(50, "Good", "aqi1", "0-50"), (100, "Moderate", "aqi2", "51-100"),
                    (150, "Unhealthy for sensitive groups", "aqi3", "101-150"), (200, "Unhealthy", "aqi4", "151-200"),
                    (300, "Very unhealthy", "aqi5", "201-300"), (500, "Hazardous", "aqi6", "301-500")]
        uv_rows = [("Low", "good", "0-2"), ("Moderate", "warn", "3-5"), ("High", "warn", "6-7"),
                   ("Very high", "bad", "8-10"), ("Extreme", "bad", "11+")]
        sw = "##" if v.ascii else "██"
        uvw = 3 + 9 + 1 + 4                      # "██ Very high 8-10"
        two = cw >= 3 + 16 + 1 + 7 + 2 + 2 + uvw
        name_w = (cw - 3 - 1 - 7 - 2 - ((2 + uvw) if two else 0))
        name_w = max(9, min(21, name_w))
        left = [[("US AQI", "label")]]
        prev = -1
        for lim, name, st_, rng in aqi_rows:
            here = aqi is not None and prev < aqi <= lim
            prev = lim
            left.append([(sw, st_), (" " + cat_label(name, name_w).ljust(name_w), "text"), (f" {rng:>7}", "dim"),
                         (" ◀" if here else "  ", "accent")])
        right = [[("UV index", "label")]] + [[(sw, st_), (f" {name:<9} {rng:>4}", "text")] for name, st_, rng in uv_rows]
        if not two:
            return left + [[]] + right
        lw = max(seglen(x) for x in left) + 2
        return [padto(left[i], lw) + (right[i] if i < len(right) else []) for i in range(len(left))]

    def chart_card(make, series, gutter, axis=True):
        def build(cw):
            use_axis, width = fit_chart(len(series), cw, gutter, axis)
            return centered(make(series, F, width, now, h, use_axis), width, cw)
        return build

    def peaks_card(cw):
        shown = sorted(days)[:5]
        top = aqi_top(max(days[d] for d in shown))
        catw = min(14, max(len(cat_label(aqi_cat(days[d])[0], 14)) for d in shown))
        mw = max(6, cw - 10 - 4 - 2 - 2 - catw)
        out = []
        for d in shown:
            clab, csty, _ = aqi_cat(days[d])
            out.append(clip([(F.day_label(d).ljust(10), "label"), (f"{days[d]:>4}  ", csty)] +
                            meter_spans(days[d] / top, mw, csty) + [("  " + cat_label(clab, catw), csty)], cw))
        return out

    def sun_card(cw):
        if not uv_day:
            return [[("No UV forecast yet.", "dim")]]
        t_pk, pk = max(uv_day, key=lambda x: x[1])
        ulab, usty = uv_cat(pk)
        out = [[("Peak UV".ljust(16), "label"), (f"{pk:.0f} {ulab}", usty), (f"  around {F.hour(t_pk)}", "dim")]]
        hot = [t for t, u in uv_day if u >= 3]
        if hot:
            out.append([("UV 3 or higher".ljust(16), "label"), (f"{F.hour(min(hot))} to {F.hour(max(hot) + 3600)}",
                                                                 "value")])
            mins = 250 / (pk * 0.025) / 60     # ~250 J/m2 reddens fair skin; UV index 1 = 0.025 W/m2
            out.append([("Burn time".ljust(16), "label"), (f"~{mins:.0f} min, fair skin", "value")])
            out += wrap("Sunscreen (SPF 30+), a hat and shade help most during those hours.", cw, "dim", 0)
        else:
            out += wrap("UV stays low, so sunburn risk is minimal for most people.", cw, "good", 0)
        return [clip(x, cw) for x in out]

    def guide_card(cw):
        k = max(1, min(3, (cw + 3) // 46))
        colw = (cw - 3 * (k - 1)) // k
        dom = air.get("dominant")
        blocks = []
        for pol, text in POLLUTANT_GUIDE:
            rows = runin(pol, ("(driving the AQI now) " if pol == dom else "") + text, colw, indent=0, hang=2)
            rows[0] = [(t, ("accent" if pol == dom else "label") if j == 0 else "dim") for j, (t, _s) in enumerate(rows[0])]
            blocks.append([rows[0]] + [[(t, "dim") for t, _s in r] for r in rows[1:]])
        out, _where = ui.columns(blocks, k, colw, ascii_only=v.ascii)
        return out

    past = air.get("history") or []
    fc = air.get("forecast") or []
    nxt = [(t, a) for t, a in fc if now - 3600 < t <= now + 24 * 3600][:24]
    days = {}
    for t, a in fc:
        d = F.local(t).date()
        days[d] = max(days.get(d, 0), a)
    uv_day, uv_word = uv_focus(m, F)
    pm = [(t, p) for t, _a, p in past if p is not None]

    cards = {"now": {"title": "Air quality", "note": f"{air.get('src')}, {F.time(air.get('t'))}", "build": now_card},
             "pollutants": {"title": "Pollutants", "build": pollutants_card},
             "scales": {"title": "Scales", "build": scales_card},
             "sun": {"title": "Sun safety", "note": uv_word, "build": sun_card},
             "guide": {"title": "What they are", "build": guide_card}}
    if past:
        hi = max(past, key=lambda x: x[1])
        cards["past"] = {"title": "AQI · past 24 h", "note": f"high {hi[1]} at {F.hour(hi[0])} · low "
                                                               f"{min(a for _t, a, _p in past)}",
                         "build": chart_card(lambda *a: aqi_chart(*a, v=v, slot="aqiPast"),
                                             [(t, a) for t, a, _p in past], len(str(aqi_top(hi[1]))) + 2)}
    if pm:
        vals = [p for _t, p in pm]
        cards["pm"] = {"title": "PM2.5 · past 24 h", "note": f"{min(vals):.1f}-{max(vals):.1f} ug/m3 · "
                                                             f"avg {sum(vals) / len(vals):.1f}",
                       "build": chart_card(lambda *a: pm_chart(*a, v=v, slot="pmPast"), [(t, p) for t, _a, p in past],
                                           len(f"{round_top(max(max(vals), PM25_BOUNDS[0])):g}") + 2)}
    if nxt:
        peak = max(nxt, key=lambda x: x[1])
        cards["next"] = {"title": "AQI · next 24 h", "note": f"peak {peak[1]} at {F.hour(peak[0])}",
                         "build": chart_card(lambda *a: aqi_chart(*a, v=v, slot="aqiNext"), nxt,
                                             len(str(aqi_top(peak[1]))) + 2)}
    if days:
        cards["peaks"] = {"title": "Daily peak AQI", "note": "forecast", "build": peaks_card}
    if uv_day:
        t_pk, pk = max(uv_day, key=lambda x: x[1])
        cards["uv"] = {"title": f"UV index · {uv_word}", "note": f"peak {pk:.0f} at {F.hour(t_pk)}",
                       "build": chart_card(lambda *a: uv_chart(*a, v=v), uv_trim(uv_day), 4, axis=False)}

    def wide(key, span):
        return dict(cards[key], span=span) if key in cards else None

    layouts = {3: [["now", "pollutants", "scales"], ["past", "next", "pm"], ["uv", "peaks", "sun"], [("guide", 3)]],
               2: [["now", "pollutants"], ["past", "next"], ["pm", "uv"], ["peaks", "sun"], [("scales", 2)],
                   [("guide", 2)]],
               1: [["now"], ["pollutants"], ["past"], ["next"], ["pm"], ["uv"], ["peaks"], ["sun"], ["scales"],
                   ["guide"]]}
    rows = []
    for row in layouts[ncols]:
        got = [wide(*x) if isinstance(x, tuple) else cards.get(x) for x in row]
        got = [c for c in got if c]
        used = sum(c.get("span", 1) for c in got)
        rows.append(got + [None] * (ncols - used))
    main = ui.card_grid(rows[:-1], W, ascii_only=v.ascii)
    L = main + ui.card_grid(rows[-1:], W, ascii_only=v.ascii)
    L += wrap("US AQI values here are model estimates (OpenWeather or CAMS via Open-Meteo), so they can differ "
              "from AirNow's official monitor readings.", W, "dim", 1)
    L += ui.error_lines(m, ("owm_air", "owm_air_fc", "owm_air_hist", "om_air"), W)
    return L, len(main)


# Air & UV's charts: solid outlined columns in each reading's category colors
AQI_BOUNDS = (50, 100, 150, 200, 300, 500)          # the top of each US AQI category
PM25_BOUNDS = (9.0, 35.4, 55.4, 125.4, 225.4)       # PM2.5 (ug/m3) at the top of each, to unhealthy


def aqi_top(value):
    """A scale's top for AQI readings: the top of the category `value` is in, at least
    moderate's."""
    return next((b for b in AQI_BOUNDS if b >= max(value, 100)), 500)


def aqi_chart(series, F, width, now, height=6, axis=True, v=None, slot="aqiChart"):
    """AQI hour by hour, [(t, aqi)]: columns in the categories' colors on a scale to the
    top of the highest reading's category, a dotted line at each category's top, the
    highest reading over its column; the scale's numbers on the left when `axis`."""
    vals = [a for _t, a in series]
    top = max(a for a in vals if a is not None)
    hi = aqi_top(top)
    gutter = len(str(hi)) + 2 if axis else 0
    cell, _body = layout(len(vals), width - gutter)
    peak = vals.index(top)
    return columns(v, slot, vals, width, height, lo=0, hi=hi, style=lambda a: aqi_cat(a)[1],
                   tops=[str(a) if i == peak else "" for i, a in enumerate(vals)],
                   axis=[(b, str(b)) for b in reversed(AQI_BOUNDS) if b <= hi] if axis else (),
                   guides=[(b, "faint") for b in reversed(AQI_BOUNDS) if b < hi],
                   labels=ui.time_labels([t for t, _a in series], F, cell, now), gutter=gutter)


def pm_chart(series, F, width, now, height=5, axis=True, v=None, slot="pmChart"):
    """PM2.5 hour by hour, [(t, ug/m3)]: columns in the AQI colors their readings fall in,
    a dotted line where each category ends."""
    vals = [p for _t, p in series]
    nums = [p for p in vals if p is not None]
    if not nums:
        return []
    hi = round_top(max(max(nums), PM25_BOUNDS[0]))
    marks = [b for b in PM25_BOUNDS if b < hi]
    gutter = len(f"{hi:g}") + 2 if axis else 0
    cell, _body = layout(len(vals), width - gutter)
    peak = vals.index(max(nums))
    return columns(v, slot, vals, width, height, lo=0, hi=hi, style=lambda p: aqi_cat(us_aqi("PM2.5", p))[1],
                   tops=[f"{p:.1f}" if i == peak else "" for i, p in enumerate(vals)],
                   axis=([(hi, f"{hi:g}")] + [(b, f"{b:.0f}") for b in reversed(marks)]) if axis else (),
                   guides=[(b, "faint") for b in reversed(marks)],
                   labels=ui.time_labels([t for t, _p in series], F, cell, now), gutter=gutter)


def round_top(value):
    """A round number at or above `value` for a scale's top: 1, 1.5, 2, 2.5, 3, 4, 5, 6, 8
    times a power of ten."""
    if value <= 0:
        return 1
    k = 10 ** math.floor(math.log10(value))
    return next(m * k for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10) if m * k >= value - 1e-9)


def uv_trim(rows):
    """The UV hours through the day's sun: the dark hours cut off, one kept either side."""
    lit = [i for i, (_t, u) in enumerate(rows) if u >= 0.5]
    return rows[max(0, lit[0] - 1):lit[-1] + 2] if lit else rows


def uv_chart(rows, F, width, now, height=6, axis=True, v=None):
    """The UV index hour by hour through the day's sun, [(t, uv)]: an area chart colored
    by the UV categories it rises through on the 0 to 11+ scale, marks where each category
    starts, each hour's reading over the line."""
    rows = uv_trim(rows)
    vals = [u if u >= 0.5 else 0 for _t, u in rows]          # the dark hours either side stay dark
    # the whole 0 to 11+ scale with its marks; the small card's scale to 8 at least, so a
    # winter day's curve still has a shape
    hi = max(11, math.ceil(max(vals))) if axis else max(8, math.ceil(max(vals)))
    bounds = (3, 6, 8, 11)
    gutter = len(str(hi)) + 2 if axis else 0
    slot, _body = layout(len(vals), width - gutter)
    # each hour's reading over the line where there's room for them all; else every other
    # hour's, the peak's always
    peak = vals.index(max(vals))
    every = 1 if slot >= 4 else 2
    tops = [f"{u:.0f}" if round(u) >= 1 and (i == peak or (i % every == peak % every and abs(i - peak) > 1)
                                             or every == 1) else "" for i, u in enumerate(vals)]
    return area(v, "areaUV", vals, width, height, lo=0, hi=hi, style=lambda u: uv_cat(u)[1], tops=tops,
                axis=(([] if hi in bounds else [(hi, str(hi))]) + [(b, str(b)) for b in reversed(bounds)])
                if axis else (),
                labels=ui.time_labels([t for t, _u in rows], F, slot, now), gutter=gutter)


def uv_focus(m, F):
    """Today's UV curve, or tomorrow's once today's sun is done: (series, word)."""
    uv = (m.get("air_om") or {}).get("uv") or []
    today = F.today()
    rows = [(t, u) for t, u in uv if F.local(t).date() == today]
    if rows and max(u for _t, u in rows) >= 1:
        return rows, "today"
    tmr = today + dt.timedelta(days=1)
    rows2 = [(t, u) for t, u in uv if F.local(t).date() == tmr]
    if rows2:
        return rows2, "tomorrow"
    return rows, "today"


POLLUTANT_GUIDE = [
    ("PM2.5", "Fine particles from smoke, exhaust and industry, small enough to reach deep into the lungs."),
    ("PM10", "Coarser dust, pollen and mold that irritate the eyes, nose and throat."),
    ("O3", "Ground-level ozone, cooked up by sunlight from traffic and industrial fumes; worst on hot afternoons."),
    ("NO2", "Nitrogen dioxide, mostly from traffic and power plants; it inflames airways."),
    ("SO2", "Sulfur dioxide from burning coal and oil; it can trigger asthma."),
    ("CO", "Carbon monoxide, an odorless gas from incomplete burning that crowds oxygen out of the blood."),
]


# ----------------------------------------------------------------------------
# 8 Sky & Almanac
# ----------------------------------------------------------------------------

def day_bar(s, day, tz, cells, ascii_only=False, lat=None, lon=None):
    """Daylight and twilight across the day.  Uses the event times in `s`, or
    the sun's height when an event doesn't happen (midnight sun, polar night)."""
    if tz:
        start = int(dt.datetime(day.year, day.month, day.day, tzinfo=tz).timestamp())
    else:
        start = int(time.mktime(day.timetuple()))
    step = 86400 / cells
    limits = {"sun": -0.833, "civil": -6.0, "nautical": -12.0, "astro": -18.0}

    def inside(t, name):
        pair = s[name]
        if pair[0] is not None and pair[1] is not None:
            return pair[0] <= t < pair[1]
        if lat is None or lon is None:
            return False
        return sun_altitude(t, lat, lon) > limits[name]

    chars = ("#", "=", "-", ".", " ") if ascii_only else ("█", "▓", "▒", "░", "·")
    segs = []
    for i in range(cells):
        t = start + (i + 0.5) * step
        if inside(t, "sun"):
            ch, sty = chars[0], "t_warm"
        elif inside(t, "civil"):
            ch, sty = chars[1], "t_hot"
        elif inside(t, "nautical"):
            ch, sty = chars[2], "accent"
        elif inside(t, "astro"):
            ch, sty = chars[3], "rain"
        else:
            ch, sty = chars[4], "faint"
        if segs and segs[-1][1] == sty:
            segs[-1] = (segs[-1][0] + ch, sty)
        else:
            segs.append((ch, sty))
    return segs, start, step


def month_chart(days, F, width, height=5, v=None):
    """The month's daily highs as an area chart in their temperature colors, a dotted line
    across it at the normal high, the days of the month along the bottom."""
    vals = [F.tv(d["hi"]) for d in days]
    norms = [F.tv(d["nhi"]) for d in days if d.get("nhi") is not None]
    hi = max(vals + norms)
    span = max(hi - min(vals + norms), 10.0 if F.imp else 6.0)
    normal = sum(norms) / len(norms) if norms else None
    axis = [(hi, f"{hi:.0f}°")] + ([(normal, f"{normal:.0f}°")] if normal is not None else [])
    return area(v, "areaMonth", vals, width, height, lo=hi - span * 1.25, hi=hi,
                style=lambda x: temp_style(native_temp(F, x)),
                axis=axis, guides=[(normal, "dim")] if normal is not None else [],
                labels=[str(d["date"].day) if d["date"].day in (1, 5, 10, 15, 20, 25, 30) else ""
                        for d in days], gutter=max(len(t) for _v, t in axis) + 2)


def render_sky(m, v, W, H, printing=False):
    """Sky & Almanac in cards: the sun beside the moon, the day's light and dark in a bar,
    the almanac beside the week ahead, this month's highs; a sidebar of cards (tonight's
    moon, the sun's path today, what's coming up, daylight through the year)."""
    F = v.fmt
    asc = v.ascii
    loc = m.get("loc") or {}
    main_w, side_w = ui.sidebar_split(W, want=40) if not printing else (W, 0)
    paired = main_w >= 96 and not printing         # cards side by side, in pairs
    if loc.get("lat") is None:
        return {"lines": [[("  Waiting for the location lookup.", "dim")]] + ui.error_lines(m, ("loc",), W)}
    lat, lon, tz = loc["lat"], loc["lon"], F.tz
    today = F.today()
    s = sun_day(today, lat, lon, tz)
    y = sun_day(today - dt.timedelta(days=1), lat, lon, tz)
    calc_len = s["length"]
    astro = m.get("astro") or {}
    if astro:
        for key, (a, b) in (("sun", ("sunrise", "sunset")), ("civil", ("civilTwilightBegin", "civilTwilightEnd")),
                            ("nautical", ("nauticalTwilightBegin", "nauticalTwilightEnd")),
                            ("astro", ("astronomicalTwilightBegin", "astronomicalTwilightEnd"))):
            if astro.get(a) and astro.get(b):
                s[key] = (astro[a], astro[b])
        s["noon"] = astro.get("transit") or s["noon"]
        if s["sun"][0] and s["sun"][1]:
            s["length"] = s["sun"][1] - s["sun"][0]
    source = "from NWS for this point" if astro else f"calculated for {lat:.4f}, {lon:.4f}"
    change = short = ""
    if calc_len and y["length"] and not s.get("polar") and not y.get("polar"):
        diff = calc_len - y["length"]
        short = f"  ({abs(diff) // 60}m {abs(diff) % 60:02d}s {'longer' if diff >= 0 else 'shorter'}"
        change, short = short + " than yesterday)", short + ")"
    gold = s["golden"]
    polar = {"day": "the sun stays up all day (midnight sun)",
             "night": "the sun stays below the horizon all day (polar night)"}.get(s.get("polar"))
    sun_rows = [
        ("Sunrise", F.time(s["sun"][0]) if not polar else polar),
        ("Sunset", F.time(s["sun"][1]) if not polar else "--"),
        ("Solar noon", F.time(s["noon"])),
        ("Daylight", [(F.duration(s["length"]), "value"), (change, "dim")]),
        ("Civil twilight", f"{F.time(s['civil'][0])} to {F.time(s['civil'][1])}"),
        ("Nautical", f"{F.time(s['nautical'][0])} to {F.time(s['nautical'][1])}"),
        ("Astronomical", f"{F.time(s['astro'][0])} to {F.time(s['astro'][1])}"),
        ("Golden hour", f"until {F.time(gold[0])}, from {F.time(gold[1])}" if gold[0] and gold[1] else "--"),
    ]
    now = time.time()
    age, illum, name = moon_info(now)
    drow = ui.today_row(m, F)
    if drow.get("moon_phase") is not None:
        name = moon_name(drow["moon_phase"])
    moon_rows = [
        ("Phase", name),
        ("Illuminated", f"{illum * 100:.0f}%"),
        ("Age", f"{age:.1f} days into the cycle"),
        ("Moonrise", F.time(drow.get("moonrise"))) if drow.get("moonrise") else None,
        ("Moonset", F.time(drow.get("moonset"))) if drow.get("moonset") else None,
    ]
    for pname, t in next_moon_phases(now, 4):
        moon_rows.append((f"{pname} {'moon' if pname in ('New', 'Full') else 'quarter'}",
                          f"{F.local(t):%a %b} {F.local(t).day}"))

    def kv_rows(rows, lw=16):
        return [ui.dedent(ln, 2) for ln in block("", "", [r for r in rows if r], lw=lw)[1:]]

    def light(cw):
        cells = max(24, cw)
        segs, start, step = day_bar(s, today, tz, cells, asc, lat, lon)
        out = []
        now_i = int((now - start) / step)
        if 0 <= now_i < cells:
            arrow = "v" if asc else "▼"
            out.append([(" " * now_i + arrow + " now" if now_i + 5 <= cells else " " * (now_i - 4) + "now " + arrow,
                         "value")])
        out.append(segs)
        scale = [" "] * cells
        for frac, lab in ((0, "12a"), (0.25, "6a"), (0.5, "noon"), (0.75, "6p")):
            pos = int(frac * cells)
            for k, ch in enumerate(lab):
                if pos + k < cells:
                    scale[pos + k] = ch
        out.append([("".join(scale), "dim")])
        out.append([("█ day  " if not asc else "# day  ", "t_warm"), ("▓ civil  " if not asc else "= civil  ", "t_hot"),
                    ("▒ nautical  " if not asc else "- nautical  ", "accent"),
                    ("░ astronomical  " if not asc else ". astronomical  ", "rain"), ("· night", "faint")])
        return out

    a = m.get("almanac") or {}
    def sun_card(cw):
        rows = list(sun_rows)
        if 16 + len(F.duration(s["length"])) + len(change) > cw:      # narrow: the shorter way to say it
            rows[3] = ("Daylight", [(F.duration(s["length"]), "value"), (short, "dim")])
        return kv_rows(rows)

    cards = [[{"title": "Sun", "note": source, "build": sun_card},
              {"title": "Moon", "note": "phases approximate", "build": lambda cw: kv_rows(moon_rows)}],
             [{"title": "Day and night", "note": "today, midnight to midnight", "build": light}]]
    almanac = month = None
    if a:
        st = a.get("station") or {}
        arows = [
            ("Normal high/low", [(F.t(a.get("normal_hi")), temp_style(a.get("normal_hi"))), (" / ", "dim"),
                                 (F.t(a.get("normal_lo")), temp_style(a.get("normal_lo")))]),
            ("Record high", [(F.t(a.get("rec_hi")), temp_style(a.get("rec_hi"))), (f"  set in {a.get('rec_hi_year')}", "dim")])
            if a.get("rec_hi") is not None else None,
            ("Record low", [(F.t(a.get("rec_lo")), temp_style(a.get("rec_lo"))), (f"  set in {a.get('rec_lo_year')}", "dim")])
            if a.get("rec_lo") is not None else None,
            ("Record rainfall", [(F.precip(a.get("rec_precip")), "rain"), (f"  in {a.get('rec_precip_year')}", "dim")])
            if a.get("rec_precip") else None,
            ("Yesterday", [(F.t(a.get("y_hi")), temp_style(a.get("y_hi"))), (" / ", "dim"),
                           (F.t(a.get("y_lo")), temp_style(a.get("y_lo"))),
                           (f"   precip {F.precip(a.get('y_precip'))}" if a.get("y_precip") is not None else "", "rain")])
            if a.get("y_hi") is not None else None,
            ("Month to date", [(F.precip(a.get("mtd_precip")), "rain"),
                               (f"   normal {F.precip(a.get('mtd_normal'))}" if a.get("mtd_normal") is not None else "", "dim")])
            if a.get("mtd_precip") is not None else None,
            ("Year to date", [(F.precip(a.get("ytd_precip")), "rain"),
                              (f"   normal {F.precip(a.get('ytd_normal'))}" if a.get("ytd_normal") is not None else "", "dim")])
            if a.get("ytd_precip") is not None else None,
            ("Month average", [(F.t1(a.get("mtd_avg")), temp_style(a.get("mtd_avg"))),
                               (f"   normal {F.t1(a['mtd_avg_normal'])}  ({F.tdelta(a['mtd_avg'] - a['mtd_avg_normal'])})"
                                if a.get("mtd_avg_normal") is not None else "", "dim")])
            if a.get("mtd_avg") is not None else None,
        ]
        since = f"records since {st['since'][:4]}" if st.get("since") else "NOAA ACIS"
        almanac = {"title": "Almanac", "note": f"{a.get('name') or st.get('name') or ''} · {since}".strip(" ·"),
                   "build": lambda cw: kv_rows(arows)}
        days = [d for d in a.get("days") or [] if d.get("hi") is not None]
        if days and main_w >= 60:
            his = [d["hi"] - d["nhi"] for d in days if d.get("nhi") is not None]
            above = f"; {sum(1 for h in his if h > 0)} of {len(his)} days above normal" if his else ""

            def month_body(cw):
                return month_chart(days, F, cw, v=v) + wrap(
                    f"warmest {F.t(max(d['hi'] for d in days))}, coolest high {F.t(min(d['hi'] for d in days))}" + above,
                    cw + 1, "dim", 0)
            month = {"title": "This month", "note": "daily highs, the normal dotted" if his else "daily highs",
                     "build": month_body}

    week_cols = [("day", "Day", 10, "l", 0), ("rise", "Sunrise", 8, "r", 0), ("set", "Sunset", 8, "r", 0),
                 ("len", "Daylight", 8, "r", 0), ("moon", "Moon", 16, "l", 1)]

    def week(cw):
        cols = fit_columns(week_cols, cw + 2, "moon", 16)
        out = [ui.dedent(table_header(cols), 2), [("─" * table_width(cols), "faint")]]
        for i in range(7):
            d = today + dt.timedelta(days=i)
            sd = sun_day(d, lat, lon, tz)
            out.append(ui.zebra(ui.dedent(table_row(cols, {
                "day": (F.day_label(d), "label"), "rise": (F.time(sd["sun"][0]), "text"),
                "set": (F.time(sd["sun"][1]), "text"), "len": (F.duration(sd["length"]), "text"),
                "moon": (day_moon({"date": d}, tz)[0], "text")}), 2), cw, i))
        return out
    week_card = {"title": "Week ahead", "note": "sunrise, sunset and the moon", "build": week}
    cards.append([c for c in (almanac, week_card) if c])
    if month:
        cards.append([month])
    if not paired:
        cards = [[c] for row in cards for c in row]
    L = ui.card_grid(cards, main_w, asc)
    L += ui.error_lines(m, ("acis",), main_w)

    side = []
    if side_w or printing:
        sw = sidebar_width(side_w, W, printing)
        cw = sw - 2                                    # inside the cards
        mrows = max(6, min(12, sw // 3))
        mcols = min(cw, mrows * 2 + 2)
        side.append(ui.section("Tonight's moon", sw, f"{illum * 100:.0f}% lit"))
        mb = ui.moon_block(v, age / SYNODIC, mcols, mrows, bg_role="card_bg")
        pad = 2 + max(0, (cw - mcols) // 2)
        side += [[(" " * pad, "text")] + ln for ln in mb]
        side.append([("  " + name.center(cw), "t_mild")])
        day0 = int(dt.datetime(today.year, today.month, today.day, tzinfo=tz).timestamp()) if tz else int(now // 86400 * 86400)
        if v.gmode in ("kitty", "sixel") and not printing:
            from .charts import sun_path_image
            pts = [(day0 + k * 900, sun_altitude(day0 + k * 900, lat, lon)) for k in range(97)]
            side.append(ui.section("Sun path today", sw))
            srows = max(5, sw // 5)
            sp = v.images.block("sunpath", f"sunpath:{today}:{int(now // 600)}:{sw}:{v.theme.rgb('card_bg')}", cw, srows,
                                lambda mode, w, h: sun_path_image(pts, now, F, w, h, v.theme.rgb("card_bg"), theme=v.theme),
                                back=v.theme.rgb("card_bg"))
            side += [[("  ", "text")] + ln for ln in sp]
        elif not printing:
            from .text import braille_plot
            alts = [sun_altitude(day0 + k * 1800, lat, lon) for k in range(49)]
            side.append(ui.section("Sun path today", sw, f"highest {max(alts):.0f}° at {F.time(s['noon'])}"))
            if asc:
                side.append([("  ", "text"), (spark([max(0, x) for x in alts], 0, max(10, max(alts)), ascii_only=True), "t_warm")])
            else:
                plot = braille_plot([([max(-6.0, x) for x in alts], "t_warm")], cw, 4, -6.0, max(10.0, max(alts)))
                side += [[("  ", "text")] + ln for ln in plot]
            side.append([("  12a" + " " * max(0, cw // 2 - 5) + "noon" + " " * max(0, cw - cw // 2 - 4 - 4) + "12a", "dim")])
        side += coming_up(F, tz, sw)
        side += daylight_year(F, lat, lon, tz, sw, asc, v=v)
        side += ui.almanac_panel(m, F, sw) if not a else []
    return with_side(L, side, side_w, printing, W=W, ascii_only=asc, main_w=main_w)


def daylight_year(F, lat, lon, tz, width, ascii_only=False, v=None):
    """Day length through the year, with today marked: a picture where the terminal draws
    pictures, block bars elsewhere."""
    today = F.today()
    year = today.year
    pts = []
    d = dt.date(year, 1, 1)
    while d.year == year:
        pts.append((d, sun_day(d, lat, lon, tz)["length"]))
        d += dt.timedelta(days=4)
    lens = [x for _d, x in pts if x is not None]
    out = [ui.section("Daylight this year", width)]
    if not lens or max(lens) <= 0:
        return out
    pw = max(12, width - 2)
    vals = [x for _d, x in pts]
    samp = [vals[min(len(vals) - 1, int(i * len(vals) / pw))] for i in range(pw)]
    lo, hi = min(lens) * 0.9, max(lens)
    if hi - lo < 60:
        lo = max(0, hi - 3600)
    parts = " .:-=+*#" if ascii_only else " ▁▂▃▄▅▆▇"
    full = "#" if ascii_only else "█"
    pos = min(pw - 1, int((today.timetuple().tm_yday - 1) / 366 * pw))
    images = getattr(v, "images", None)
    if images is not None and getattr(images, "pixels", False) and gfx.HAVE_PIL and not getattr(v, "printing", False):
        back = v.theme.rgb("card_bg")
        doys = [(dd.timetuple().tm_yday, x) for dd, x in pts]
        tdoy = today.timetuple().tm_yday
        key = f"daylight:{year}:{tdoy}:{round(lat, 2)}:{round(lon, 2)}:{v.theme.name}:{back}"

        def build(mode, w, h):
            return daylight_image(doys, tdoy, lo, hi, w, h, back, theme=v.theme) if mode == "pixels" else None
        out += [[("  ", "text")] + ln for ln in images.block(f"daylight{pw}", key, pw, 4, build, back=back)]
    else:
        out += daylight_bars(samp, lo, hi, pos, parts, full)
    rest = daylight_rest(F, pts, today, lat, lon, tz, width, pw, pos)
    return out + rest


def daylight_bars(samp, lo, hi, pos, parts, full):
    """Day length in three rows of block bars, today's column picked out."""
    out = []
    for row in range(3):
        base = (2 - row) * 8
        line = [("  ", "text")]
        for i, x in enumerate(samp):
            e = max(0, int(round((x - lo) / (hi - lo) * 24))) if x else 0
            k = e - base
            ch = full if k >= 8 else parts[k] if k > 0 else " "
            line.append((ch, "accent" if i == pos else "t_warm"))
        out.append(line)
    return out


def daylight_rest(F, pts, today, lat, lon, tz, width, pw, pos):
    """Under the daylight chart: the months, today's mark, the shortest and longest days
    and which way it's going."""
    out = []
    axis = [" "] * pw
    for mth in range(12):
        x = int(mth / 12 * pw)
        if x < pw:
            axis[x] = "JFMAMJJASOND"[mth]
    out.append([("  " + "".join(axis), "dim")])
    out.append([("  " + " " * pos + "▲ today", "accent")] if pos < pw - 8 else [("  " + " " * max(0, pos - 7) + "today ▲", "accent")])
    short = min(((dd, x) for dd, x in pts if x is not None), key=lambda p: p[1])
    long_ = max(((dd, x) for dd, x in pts if x is not None), key=lambda p: p[1])
    t0 = sun_day(today, lat, lon, tz)["length"]
    t1 = sun_day(today + dt.timedelta(days=1), lat, lon, tz)["length"]
    out.append(ui.kv("Shortest", f"{F.duration(short[1])} around {short[0]:%b} {short[0].day}", width, 12))
    out.append(ui.kv("Longest", f"{F.duration(long_[1])} around {long_[0]:%b} {long_[0].day}", width, 12))
    if t0 and t1 and 0 < t0 < 86400 and 0 < t1 < 86400:
        dd = t1 - t0
        out.append(ui.kv("Trend", f"{'gaining' if dd > 0 else 'losing'} {abs(dd) // 60}m {abs(dd) % 60:02d}s a day", width, 12))
    return out


def _season_dates(year):
    """Approximate equinox/solstice dates (good to a day)."""
    return [(dt.date(year, 3, 20), "Spring equinox"), (dt.date(year, 6, 21), "Summer solstice"),
            (dt.date(year, 9, 22), "Fall equinox"), (dt.date(year, 12, 21), "Winter solstice")]


def coming_up(F, tz, width):
    out = [ui.section("Coming up", width)]
    today = F.today()
    seasons = [x for x in _season_dates(today.year) + _season_dates(today.year + 1) if x[0] >= today][:1]
    for d, name in seasons:
        n = (d - today).days
        out.append(ui.kv(name, f"{d:%b} {d.day}, " + ("today" if not n else "tomorrow" if n == 1 else f"in {n} days"),
                         width, 16))
    if tz is not None:
        try:
            base = dt.datetime(today.year, today.month, today.day, 12, tzinfo=tz)
            off0 = base.utcoffset()
            for k in range(1, 200):
                dd = base + dt.timedelta(days=k)
                if dd.utcoffset() != off0:
                    word = "Clocks back" if dd.utcoffset() < off0 else "Clocks forward"
                    day = (base + dt.timedelta(days=k - 1)).date() + dt.timedelta(days=1)
                    out.append(ui.kv(word, [(f"{day:%a %b} {day.day}", "value"), (", 2 AM", "dim")], width, 16))
                    break
        except Exception:
            pass
    return out


# ----------------------------------------------------------------------------
# 9 NWS office & NOAA Weather Radio
# ----------------------------------------------------------------------------

def product_lines(prod, title, F, W):
    if prod and prod.get("missing"):
        return [[("  " + title, "title")]] + wrap(
            f"NWS {prod.get('office') or 'your office'} doesn't issue this product, so there's nothing to show "
            "here. The Alerts screen (6) and the storm outlooks cover hazards instead.", W, "dim", 2)
    if not prod or not prod.get("text"):
        return [[("  " + title, "title")], [("  Not available from this office right now.", "dim")]]
    issued = iso_epoch(prod.get("issued"))
    L = [[("  " + title, "title"),
          (f"   NWS {prod.get('office') or ''}, issued {F.dtime(issued)}", "dim")]]
    para = []

    def flush():
        if para:
            for piece in textwrap.wrap(" ".join(para), max(20, W - 4)) or [""]:
                L.append([("  " + piece, "text")])
            para.clear()

    for raw in prod["text"].splitlines():
        s = raw.rstrip()
        style = "title" if re.match(r"^\.[A-Z]", s) else "dim" if s.strip() in ("&&", "$$") else "text"
        body = s.strip()
        prose = style == "text" and body and not re.search(r"\S {3,}\S", body) and re.search(r"[a-z]", body)
        bullet = prose and re.match(r"^[-*•] ", body)
        if prose and (bullet or not s.startswith((" ", "\t")) or para):
            # NWS hard-wraps at 66 columns: let paragraphs (and bullets) fill the width
            if bullet:
                flush()
            para.append(body)
            continue
        flush()
        if len(s) <= W - 3:
            L.append([("  " + s, style)])
        else:
            for piece in textwrap.wrap(s, W - 4) or [""]:
                L.append([("  " + piece, style)])
    flush()
    return L


def flow_cards(sections, ncols, col_w, ascii_only=False, gap=1, width=0):
    """Sections [(title, note, lines)] as cards running down `ncols` newspaper columns
    `col_w` wide (lines built col_w - 4 wide; the last column takes what's left of
    `width`), the columns about equally long: a card too long for what's left of a column
    carries on at the top of the next, its title again with "continued"."""
    widths = [col_w] * ncols
    if width:
        widths[-1] = max(col_w, width - (col_w + gap) * (ncols - 1))
    sections = [(t, n, trim_blank(b)) for t, n, b in sections]
    total = sum(len(b) + 2 for _t, _n, b in sections)
    target = max(12, -(-total // max(1, ncols)))
    cols = [[]]
    for title, note, body in sections:
        i, part = 0, 0
        while True:
            col = cols[-1]
            last = len(cols) >= ncols
            rest = len(body) - i
            room = rest if last else target - len(col) - 2
            if col and not last and room < min(4, rest):
                cols.append([])                    # too little room left here: the next column
                continue
            take = rest if rest - room <= 3 else max(1, room)
            chunk = trim_blank(body[i:i + take])
            col += card(title + (" (continued)" if part else ""), chunk, widths[len(cols) - 1], "" if part else note,
                        ascii_only)
            i += take
            part += 1
            if i >= len(body):
                break
            cols.append([])
    return stack(cols, widths[:len(cols)], gap)


def trim_blank(lines):
    lines = list(lines)
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def product_card(prod, title, F, cw):
    """An NWS text product as (title, note, lines): its paragraphs filled to `cw`."""
    if prod and prod.get("missing"):
        return (title, "", wrap(f"NWS {prod.get('office') or 'your office'} doesn't issue this product, so there's "
                                "nothing to show here. The Alerts screen (6) and the storm outlooks cover hazards "
                                "instead.", cw + 1, "dim", 0))
    if not prod or not prod.get("text"):
        return (title, "", [[("Not available from this office right now.", "dim")]])
    note = f"NWS {prod.get('office') or ''}, issued {F.dtime(iso_epoch(prod.get('issued')))}"
    return (title, note, [ui.dedent(ln, 2) for ln in product_lines(prod, title, F, cw + 3)[1:]])


def bullet(mark, mark_style, text, style, cw, tail=None):
    """A list item: the mark, then the text wrapped under itself; `tail` (text, style) after it."""
    pieces = textwrap.wrap(" ".join(str(text or "").split()), max(8, cw - len(mark))) or [""]
    out = [[(mark, mark_style), (pieces[0], style)]]
    out += [[(" " * len(mark) + p, style)] for p in pieces[1:]]
    if tail and tail[0]:
        if seglen(out[-1]) + len(tail[0]) <= cw:
            out[-1].append(tail)
        else:
            out.append([(" " * len(mark) + tail[0].strip(), tail[1])])
    return out


def render_office(m, v, W, H, printing=False):
    """NWS & Radio in cards: the weather radio script, the office's briefing, stories and
    headlines, the Hazardous Weather Outlook and the forecast discussion. Wide screens run
    the cards down newspaper columns (a long one carries on in the next); narrower ones
    stack them beside a sidebar of facts (listening, the radio transmitter, the office)."""
    F = v.fmt
    asc = v.ascii
    office = m.get("office") or {}
    pts = m.get("pts") or {}
    radio = m.get("radio") or {}
    nwr = pts.get("nwr") or {}
    ncols, col_w = ui.flow_columns(W, gap=1) if not printing else (1, W)
    if ncols > 1:        # long reading: newspaper columns across the whole width, the sidebar's cards first
        main_w, side_w = col_w, 0
    else:
        main_w, side_w = ui.sidebar_split(W, want=42) if not printing else (W, 0)
    cw = main_w - 4
    away = (m.get("notes") or {}).get("nws_points") or (m.get("off") or {}).get("nws_points")
    if away and not pts:
        return {"lines": card("NWS & Radio", wrap(f"Nothing to show here: {away.rstrip('.')}. The other screens use "
                                                  "Open-Meteo for this location.", min(main_w, 110) - 3, "dim", 0),
                              min(main_w, 110), "", asc)}
    sections = []
    # -- the weather radio script
    body = []
    bits = []
    tx = radio.get("transmitter") or nwr.get("transmitter")
    if tx:
        bits.append(f"transmitter {tx}")
    same = radio.get("same") or nwr.get("sameCode")
    if same:
        bits.append(f"SAME code {same}")
    if bits:
        body.append([("   ".join(bits), "label")])
        body.append([])
    text = radio.get("text")
    hl = getattr(v, "speech_highlight", None)
    if text:
        for chunk in re.split(r"\n\s*\n", text):
            rows = [r.strip() for r in chunk.split("\n") if r.strip()]
            if not rows:
                continue
            # a short first line without a period ('Tuesday Night') is a heading; the rest is one paragraph
            head = rows[0] if len(rows) > 1 and len(rows[0]) < 30 and not rows[0].endswith(".") else None
            para = " ".join(rows[1:] if head else rows)
            if head:
                body.append([(head, "title" if not (hl and hl in head) else "speakhi")])
            style = "title" if para.isupper() and len(para) < 60 else "text"
            if hl and hl in para:
                style = "speakhi"
            body += wrap(para, cw + 1, style, 2 if head else 0)
            body.append([])
    else:
        body.append([("No broadcast script is available for this point right now.", "dim")])
    sections.append(("NOAA Weather Radio", "press s to listen", body))
    # -- the office's briefing, stories and headlines
    gid = pts.get("gridId") or ""
    brief = office.get("briefing")
    if brief:
        body = [[(brief["title"], "value")]] + wrap(brief["text"], cw + 1, "text", 0)
        if brief.get("link"):
            body += labeled_wrap("Download", brief["link"], cw + 1, lw=10, style="dim", indent=0)
        sections.append(("Briefing", f"NWS {gid}" + (f", updated {F.dtime(brief['updated'])}" if brief.get("updated")
                                                      else ""), body))
    stories = office.get("stories") or []
    if stories:
        body = []
        for st in stories:
            if body:
                body.append([])
            body += bullet("▸ " if st["priority"] else "· ", "accent", st["title"] or "Weather story", "value", cw,
                           (f"   until {F.dtime(st['end'])}", "dim") if st.get("end") else None)
            body += wrap(st["text"], cw + 1, "text", 2)
            if st.get("alt") and st["alt"] != st["text"]:
                body += wrap(f"Image shows: {st['alt']}", cw + 1, "dim", 2)
        sections.append(("Weather stories", "NWS graphics, in words", body))
    heads = office.get("headlines") or []
    if heads:
        body = []
        for h in heads[:8]:
            body += bullet("! " if h["important"] else "· ", "warn" if h["important"] else "accent", h["title"],
                           "value", cw, (f"   {F.dtime(h['issued'])}", "dim") if h.get("issued") else None)
            if h.get("text"):
                body += wrap(h["text"], cw + 1, "text", 2)
        sections.append(("Headlines", f"NWS {gid}".strip(), body))
    if not (brief or stories or heads):
        sections.append((f"NWS {gid} office".replace("  ", " "), "",
                         wrap("No briefing, weather stories, or headlines are posted right now.", cw + 1, "dim", 0)))
    sections.append(product_card(m.get("hwo"), "Hazardous weather outlook", F, cw))
    sections.append(product_card(m.get("afd"), "Area forecast discussion", F, cw))
    errors = ui.error_lines(m, ("nws_points", "nws_radio", "nws_afd", "nws_hwo", "nws_headlines", "nws_stories",
                                "nws_briefing"), W if ncols > 1 else main_w)
    # -- the facts: listening, the radio transmitter, the office
    sw = col_w if ncols > 1 else sidebar_width(side_w, W, printing) + 2
    fw = sw - 4

    def kvl(label, value, lw):
        return ui.dedent(ui.kv(label, value, fw + 2, lw), 2)
    facts = []
    if side_w or printing or ncols > 1:
        listen = wrap("Press s on any forecast screen to have skydrop read it aloud. Press s again (or Esc) to "
                      "stop; [ and ] skip between sentences.", fw + 1, "text", 0)
        eng = getattr(v, "speech", None)
        if eng is not None:
            listen += labeled_wrap("Voice", eng.describe(), fw + 1, lw=7, style="value", indent=0)
        facts.append(("Listen", "", listen))
        if tx or same:
            body = ([kvl("Transmitter", tx, 13)] if tx else []) + ([kvl("SAME code", same, 13)] if same else [])
            body += wrap("Program a weather radio with this SAME code to get alerts for your county.", fw + 1, "dim", 0)
            facts.append(("Weather radio", "", body))
        if gid:
            facts.append(("Your NWS office", "", [kvl("Office", gid, 10), kvl("Grid", f"{pts.get('gridX')},{pts.get('gridY')}", 10),
                                                   kvl("Radar", pts.get("radarStation") or "--", 10),
                                                   kvl("Zone", (pts.get("forecastZone") or "").rsplit("/", 1)[-1], 10)]))
    if ncols > 1:
        return {"lines": flow_cards(facts + sections, ncols, col_w, asc, width=W) + errors}
    L = []
    for title, note, body in sections:
        L += card(title, trim_blank(body), main_w, note, asc)
    L += errors
    side = []
    for title, note, body in facts:
        side += card(title, trim_blank(body), sw, note, asc)
    if printing:
        return {"lines": L + side}
    out = {"lines": L}
    if side_w and side:
        out["side"], out["side_w"] = side, side_w
    return out
