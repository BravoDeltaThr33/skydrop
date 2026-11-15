"""Radar and live station feed screens."""

import math
import time

from . import gfx, ui
from .columns import bar_chart
from .fmt import wind_text
from .model import build_station_history
from .radar import summary_lines
from .text import (clip, fit_columns, kv_grid, restyle, seglen, table_header, table_row, table_width,
                   unit_cols, wrap, labeled_wrap)
from .theme import temp_style, wind_style
from .wxmath import compass, comfort


# ----------------------------------------------------------------------------
# 4 Radar
# ----------------------------------------------------------------------------

LAYER_NAMES = {"reflectivity": "Radar reflectivity", "type": "Precipitation type", "satellite": "Infrared satellite",
               "radar+sat": "Radar + satellite"}
BASEMAP_NAMES = {"classic": "Classic (Census map lines)", "imagery": "USGS satellite imagery", "topo": "USGS topo",
                 "osm": "OpenStreetMap", "plain": "Plain"}
def pane_products(v):
    """Products for each pane: the chosen one first, then the configured extras."""
    from . import nexrad
    st = v.radar_state
    main = st.get("product") or v.cfg.get("radar_product") or "ref"
    extra = [x.strip() for x in str(v.cfg.get("radar_pane_products") or "ref,vel,cc,zdr").split(",")]
    extra = [x for x in extra if x in nexrad.PRODUCT_BY_KEY and x != main]
    for fill in ("ref", "vel", "cc", "zdr", "srv", "kdp"):
        if fill not in extra and fill != main:
            extra.append(fill)
    return [main] + extra


def describe_value(prod, l3, level, value, label, F):
    """(main text, note) for an inspected radar value."""
    from . import nexrad, palettes
    if level is None:
        return label or "no data", ""
    if value is None:
        return (label or "nothing detected").capitalize(), ""
    key = prod.key
    if key in ("ref", "cr"):
        word = ("weak echo, often not precipitation" if value < 5 else "very light" if value < 20 else
                "light" if value < 30 else "moderate" if value < 40 else "heavy" if value < 50 else
                "very heavy, likely a thunderstorm" if value < 60 else "intense, hail likely")
        return f"{value:.1f} dBZ", word
    if key in ("vel", "srv"):
        ms = palettes.canonical(l3.code, value)
        way = "away from" if ms > 0 else "toward" if ms < 0 else "across"
        speed = f"{abs(ms) * 1.94384:.0f} kt ({abs(ms) * 2.23694:.0f} mph)" if F.imp else f"{abs(ms):.0f} m/s"
        return speed, f"moving {way} the radar" + (" (storm-relative)" if key == "srv" else "")
    if key == "cc":
        note = ("rain or snow" if value >= 0.97 else "mixed or melting precipitation, hail" if value >= 0.85
                else "non-weather targets or debris" if value >= 0.5 else "noise or clutter")
        return f"{value:.3f}", note
    if key == "zdr":
        return f"{value:+.1f} dB", ("big drops" if value >= 3 else "rain" if value >= 0.5 else
                                     "hail, graupel or snow" if value > -0.5 else "vertically shaped targets")
    if key == "kdp":
        return f"{value:.2f}°/km", "heavy rain" if value >= 1.5 else ""
    if key in ("hc", "hhc"):
        return nexrad.HC_CLASSES.get(int(value), ("", "unknown"))[1], ""
    if key == "et":
        return (f"{value:.0f},000 ft" if F.imp else f"{value / 3.28084:.1f} km"), "echo top"
    if key == "vil":
        return f"{value:.0f} kg/m²", "vertically integrated liquid"
    if key == "rate":
        return (f"{value:.2f} in/hr" if F.imp else f"{value * 25.4:.1f} mm/hr"), "rain rate"
    if key in ("p1h", "pst"):
        return F.precip(value * 25.4), "in the last hour" if key == "p1h" else "storm total"
    return f"{value:.2f}", ""


def inspect_lines(res, point, F, width, v):
    """Readout for the inspected point: value, position from the radar, beam height."""
    from . import nexrad
    from .util import bearing_deg, distance_mi
    from .wxmath import compass
    out = []
    lat, lon = point
    loc_home = res.view.home
    d_home = distance_mi(loc_home[0], loc_home[1], lat, lon)
    where_home = f"{F.dist(d_home)} {compass(bearing_deg(loc_home[0], loc_home[1], lat, lon))} of home" \
        if d_home and d_home >= 0.1 else "at home"
    l3 = res.l3_at(getattr(res, "shown", len(res.frames) - 1)) if res.l3_frames else None
    if l3 is not None and res.product is not None:
        x, y = nexrad.to_xy(l3.lat, l3.lon, lat, lon)
        level, value, label = l3.value_at(x, y)
        main, note = describe_value(res.product, l3, level, value, label, F)
        out.append(clip([("  ", "text"), (main, "value"), (f"  {note}" if note else "", "dim")], width))
        rng_km = math.hypot(x, y)
        az = (math.degrees(math.atan2(x, y)) + 360) % 360
        line = f"{F.dist(rng_km / 1.609344)} {compass(az)} of {res.view.site}"
        if l3.elevation:
            hb = nexrad.beam_height_km(nexrad.slant_range(rng_km, l3.elevation), l3.elevation,
                                       (l3.height_ft or 0) * 0.3048)
            line += f", beam {F.height(hb * 1000)}"
        out.append(clip([("  " + line, "dim")], width))
    elif res.frames:
        img = res.frames[getattr(res, "shown", -1)][1]
        px, py = res.view.to_px(lat, lon)
        if 0 <= px < img.size[0] and 0 <= py < img.size[1]:
            from .radar import color_dbz, intensity_word
            rgb = img.getpixel((int(px), int(py)))[:3]
            sat = max(rgb) - min(rgb)
            if sat > 80:
                dbz = color_dbz(rgb)
                out.append([("  about ", "dim"), (f"{dbz:.0f} dBZ", "value"), (f"  {intensity_word(dbz)}", "dim")])
            else:
                out.append([("  no precipitation here", "dim")])
    out.append(clip([(f"  {lat:.3f}, {lon:.3f} · {where_home}", "faint")], width))
    from .placefile import hover_at
    px, py = res.view.to_px(lat, lon)
    tol = 6.0
    for _gx, _gy, gw, gh, pres in (getattr(v, "layout", None) or {}).get("panes") or []:
        if pres is res and gw and gh:   # half a cell either way: where the pointer could really be
            tol = 0.6 * max(res.view.w / gw, res.view.h / gh)
    note = hover_at(res, px, py, tol)
    if note:
        lines = [ln.strip() for ln in str(note).split("\n") if ln.strip()][:6]
        for i, ln in enumerate(lines):
            out.append(clip([("  ▸ " if i == 0 else "    ", "accent"), (ln, "text" if i else "value")], width))
    return out


def storm_rows(res, F, width):
    """The storm-cell table for the sidebar: most threatening first."""
    from .wxmath import compass
    rows = []
    for c in (res.cells or [])[:12]:
        hail = c.get("hail") or {}
        flags = []
        if c.get("tvs_flag"):
            flags.append(("TVS ", "bad"))
        if c.get("meso_rank"):
            flags.append(("MESO ", "warn"))
        if (hail.get("posh") or 0) >= 50:
            size = hail.get("size") or 0
            flags.append((f'hail {size:.2f}" ' if size else "hail ", "warn"))
        elif (hail.get("poh") or 0) >= 50:
            flags.append(("hail? ", "dim"))
        where = f"{F.dist(c.get('dist'))} {compass(c.get('bearing'))}" if c.get("dist") is not None else ""
        dbz = f"{c['dbz']:.0f}dBZ" if c.get("dbz") is not None else ""
        mv = f"→{compass(c['toward'])} {F.speed_mph(c['speed_mph'])}" if c.get("toward") is not None and \
            c.get("speed_mph") else ""
        line = [("  " + (c.get("id") or "--").ljust(3), "accent"), ((where + " ").ljust(11), "text"),
                (dbz.ljust(6), "value")]
        sub = []
        if mv:
            sub.append((mv.replace(" mph", "") + " ", "dim"))
        if c.get("top") is not None:
            sub.append((f"{c['top']:.0f}k ft " if F.imp else f"{c['top'] / 3.28084:.0f} km ", "dim"))
        eta = [(f"reaches you ~{F.time(c['eta']['t'])}", "bad")] if c.get("eta") else []
        one = line + sub + flags + eta
        if seglen(one) <= width:              # one line per cell when it fits
            rows.append(one)
            continue
        rows.append(clip(line + flags, width))
        rest = sub + eta
        if rest:
            rows.append(clip([("     ", "text")] + rest, width))
    return rows


def render_radar(m, v, W, H, printing=False):
    from . import nexrad
    F = v.fmt
    st = v.radar_state
    loc = m.get("loc") or {}
    if loc.get("lat") is None:
        return {"lines": [[("  Waiting for the location lookup.", "dim")]] + ui.error_lines(m, ("loc",), W)}
    if printing:
        cols, rows = min(W, 100), 30
        lines, res = ui.radar_block(v, m, cols, rows, slot="radar")
        out = list(lines)
        if res:
            out.append([])
            out.append(clip([("  ", "text")] + ui.radar_legend(cols - 2, v.ascii, res)[0], cols))
            for txt, sty in summary_lines(res, F):
                out += wrap(txt, W, sty, 2)
        return {"lines": out}
    side_w = 0 if W < 90 else max(34, min(46, W // 4))
    map_w = W - side_w - (1 if side_w else 0)
    tb_lines, tb_hits = [], []
    if not side_w:
        # no room for the toolbox: a compact button bar above the map
        cur = st.get("product") or "ref"
        cat = nexrad.category_of(cur)
        items = [(c[0], "sel" if c is cat else "opt", f"product:{c[2][0]}") for c in nexrad.CATEGORIES]
        items += [(("❚❚" if not v.ascii else "||") if st.get("playing") else ("▶" if not v.ascii else ">"),
                   "sel" if st.get("playing") else "act", "time:play"),
                  (f"{st.get('panes') or 1} pane", "act", "panes:cycle"), ("Guide", "act", "guide")]
        tb_lines, tb_hits = ui.buttons(items, map_w, indent=1, ascii_only=v.ascii)
        tb_lines = tb_lines[:2]
        tb_hits = [h for h in tb_hits if h[0] < 2]
    map_h = max(6, H - 2 - len(tb_lines))
    npanes = st.get("panes") or 1
    if npanes == 4 and (map_w < 60 or map_h < 16):
        npanes = 2
    if npanes == 2 and map_w < 50:
        npanes = 1
    prods = pane_products(v)[:npanes]
    frame = st.get("frame")
    inspect = st.get("inspect")
    panes = []
    if npanes == 1:
        geo = [(0, 0, map_w, map_h)]
    elif npanes == 2:
        pw = (map_w - 1) // 2
        geo = [(0, 0, pw, map_h), (pw + 1, 0, map_w - pw - 1, map_h)]
    else:
        pw, ph = (map_w - 1) // 2, (map_h - 1) // 2
        geo = [(0, 0, pw, ph), (pw + 1, 0, map_w - pw - 1, ph), (0, ph + 1, pw, map_h - ph - 1),
               (pw + 1, ph + 1, map_w - pw - 1, map_h - ph - 1)]
    grid = [[(" ", "text")] * map_w for _ in range(map_h)]
    main_res = None
    t_main = None
    for k, (gx, gy, gw, gh) in enumerate(geo):
        slot = "radar" if k == 0 else f"radar{k + 1}"
        lines, res = ui.radar_block(v, m, gw, gh, slot=slot, frame=frame if k == 0 else None,
                                    product=prods[k], inspect=inspect, at_time=t_main if k else None)
        if k == 0:
            main_res = res
            if res and res.frames:
                t_main = res.frames[getattr(res, "shown", -1)][0]
        panes.append((gx, gy, gw, gh, res, prods[k]))
        for r, ln in enumerate(lines[:gh]):
            cells = []
            for text, sty in ln:
                cells += [(ch, sty) for ch in text]
            cells = (cells + [(" ", "text")] * gw)[:gw]
            grid[gy + r][gx:gx + gw] = cells
        if npanes > 1 and v.gmode not in ("kitty", "sixel"):
            title = f" {nexrad.PRODUCT_BY_KEY[prods[k]].name} "
            for i, ch in enumerate(title[:gw]):
                grid[gy][gx + i] = (ch, "tabon" if k == 0 else "tab")
    if npanes > 1:
        sep = "│" if not v.ascii else "|"
        for gx, gy, gw, gh in geo[1:]:
            if gx > 0:
                for r in range(gy, gy + gh):
                    grid[r][gx - 1] = (sep, "faint")
            if gy > 0:
                for c in range(gx, gx + gw):
                    grid[gy - 1][c] = ("─" if not v.ascii else "-", "faint")
    L = list(tb_lines) + ui._grid_to_lines(grid)
    res = main_res
    # timeline under the map
    tl = []
    if res and res.frames:
        n = len(res.frames)
        idx = getattr(res, "shown", n - 1)
        tl.append(("  ", "text"))
        for i, (t, _img) in enumerate(res.frames):
            on = i == idx
            mark = ("■" if on else "▪") if not v.ascii else ("#" if on else "-")
            tl.append((mark + " ", "accent" if on else "faint"))
        t = res.frames[idx][0]
        age = int((time.time() - t) / 60)
        tl.append((f" {F.time(t)}", "value"))
        tl.append((f"  ({age} min ago)" if age > 1 else "  (latest)", "dim"))
        tl.append(("   ▶ playing" if st.get("playing") else "   ❚❚ paused", "good" if st.get("playing") else "warn"))
        if res.view.source == "site" and res.product is not None:
            el = (res.scan or {}).get("elevation")
            tl.append((f"   {res.view.site} {res.product.short}" + (f" {el:.1f}°" if el else ""), "label"))
    elif v.radar is not None and v.radar.busy():
        tl.append(("  " + (v.radar.status or "Loading radar…"), "dim"))
    L.append(clip(tl, map_w))
    L.append(clip([("  ", "text")] + ui.radar_legend(map_w - 2, v.ascii, res, F.imp)[0], map_w))
    side = []
    if side_w:
        side = radar_sidebar(m, v, res, side_w, inspect, F, loc, H)
    off = len(tb_lines)
    out = {"lines": L, "fixed": True, "radar": main_res, "map_rect": (0, off, map_w, map_h),
           "panes": [(gx, gy + off, gw, gh, r_) for gx, gy, gw, gh, r_, _p in panes], "toolbar_hits": tb_hits}
    if side:
        out["side"], out["side_w"] = side, side_w
    return out


def toolbox(v, m, res, sw, loc, compact=False):
    """The radar toolbox (after Supercell Wx): map settings, products, elevation, timeline,
    layers and panes. Choices are chips (the chosen one filled in the accent color),
    switches are check boxes, one-shot keys are keycaps. Returns (lines, hits) with hits (row, x0, x1, action)."""
    from . import nexrad
    F = v.fmt
    st = v.radar_state
    out, hits = [], []
    asc = v.ascii
    width = sw - 1                      # the last column holds the scroll arrows

    def add(items, **kw):
        lines, hh = ui.buttons(items, width, ascii_only=asc, **kw)
        base = len(out)
        out.extend(lines)
        hits.extend((base + r, x0, x1, a) for r, x0, x1, a in hh)

    left, right = ("◀", "▶") if not asc else ("<", ">")
    minus = "−" if not asc else "-"
    site_mode = ui.radar_source(v, m) == "site"
    # -- map settings
    out.append(ui.section("Map settings", sw, "i guide"))
    add([("Site radar", "sel" if site_mode else "opt", "source:site"),
         ("Mosaic", "opt" if site_mode else "sel", "source:mosaic")] +
        ([("Smooth", "on" if st.get("smooth") else "off", "toggle:smooth")] if compact else []))
    if not compact:
        out.append([])
    if site_mode:
        site = (res.view.site if res is not None and res.view.source == "site" else None) or \
            ui.radar_site(v, m, st.get("lat") or loc["lat"], st.get("lon") or loc["lon"]) or "--"
        info = (res.site if res is not None and res.site else None) or {}
        name = (info.get("name") or "").title()
        add([("Radar", "lbl", None), (left, "act", "site:prev"), (site, "val", "site:menu"),
             (right, "act", "site:next"), ("Auto", "off" if st.get("site") else "on", "site:auto")])
        scan = (res.scan if res is not None and res.view.source == "site" else None) or {}
        if name and not (compact and scan.get("time")):
            out.append(clip([("  " + name, "label")], sw))
        if scan.get("time"):
            age = max(0, int((time.time() - scan["time"]) / 60))
            dot = "good" if age < 5 else "warn" if age < 30 else "bad"
            out.append(clip(([("  " + name + " ", "label")] if compact and name else [("  ", "text")]) +
                            [("● " if not asc else "* ", dot), ("" if compact else "scan ", "dim"),
                             (F.time(scan["time"]), "value"), (f"  {age} min ago" if age else "  just now", "dim")], sw))
        if scan.get("vcp"):
            precip = scan.get("mode") == "precip"
            out.append(clip([("  VCP ", "dim"), (str(scan["vcp"]), "value"),
                             (" · precipitation mode" if precip else " · clear air mode", "good" if precip else "dim")],
                            sw))
    else:
        out.append(clip([("  NOAA MRMS national mosaic", "label")], sw))
    op = int(v.cfg.get("radar_opacity") or 90)
    add(([] if compact else [("Smooth", "on" if st.get("smooth") else "off", "toggle:smooth")]) +
        [("Opacity", "lbl", None), (minus, "act", "opacity:-"), (f"{op}%", "val", None), ("+", "act", "opacity:+")])
    # -- products
    if site_mode:
        cur = st.get("product") or "ref"
        cat = nexrad.category_of(cur)
        prod = nexrad.PRODUCT_BY_KEY[cur]
        out.append(ui.section("Products", sw, "p / P"))
        add([(c[0], "sel" if c is cat else "opt", f"product:{c[2][0] if cur not in c[2] else cur}")
             for c in nexrad.CATEGORIES], grid=5)
        if len(cat[2]) > 1:
            if not compact:
                out.append([])
            add([("↳" if not asc else ">", "lbl", None)] +
                [(nexrad.VARIANT_NAMES.get(k, k), "sel" if k == cur else "opt", f"product:{k}") for k in cat[2]])
        el = (res.scan or {}).get("elevation") if res is not None and res.view.source == "site" else None
        out.append(clip([("  " + prod.name, "value")] + ([(f"  {el:.1f}° scan", "dim")] if el else []), sw))
        if len(prod.tilts) > 1:
            out.append(ui.section("Elevation", sw, "degrees · e / E"))
            cur_t = min(st.get("tilt", 0), len(prod.tilts) - 1)
            tilts = [((nexrad.TILT_NAMES[k] if k < len(nexrad.TILT_NAMES) else f"#{k + 1}").rstrip("°"),
                      "sel" if k == cur_t else "opt", f"tilt:{k}") for k in range(len(prod.tilts))]
            add(tilts, **({} if ui.fits(tilts, width) else {"grid": (len(tilts) + 1) // 2}))
    else:
        out.append(ui.section("Mosaic layer", sw, "y"))
        layer = st.get("layer") or "reflectivity"
        add([(lab, "sel" if layer == key else "opt", f"layer:{key}") for key, lab in
             (("reflectivity", "Reflectivity"), ("type", "Precip type"), ("radar+sat", "Radar+sat"),
              ("satellite", "Satellite"))])
    # -- timeline
    play = st.get("playing")
    n_fr = st.get("nframes") or 0
    at_end = st.get("frame") is None or st.get("frame") >= n_fr - 1
    out.append(ui.section("Timeline", sw, "looping" if play else "latest scan" if at_end else "looking back"))
    add([("|◀" if not asc else "|<", "act", "time:begin"), (left, "act", "time:back"),
         (("❚❚" if not asc else "||") if play else right, "sel" if play else "act", "time:play"),
         (right, "act", "time:next"), ("▶|" if not asc else ">|", "act", "time:end")])
    if not compact:
        out.append([])
    frames = int(v.cfg.get("radar_frames") or 8)
    speed = int(v.cfg.get("radar_speed") or 4)
    add([("Loop", "lbl", None), (minus, "act", "loop:-"), (str(frames), "val", None), ("+", "act", "loop:+"),
         ("Speed", "lbl", None), (minus, "act", "speed:-"), (str(speed), "val", None), ("+", "act", "speed:+")])
    # -- layers
    out.append(ui.section("Layers", sw, "a w l t d D M F"))
    lay = [("Tracks", "cells"), ("Warnings", "warnings"), ("Cities", "labels"), ("Temps", "temps"),
           ("Range", "range"), ("Sites", "sites"), ("Markers", "markers"), ("Placefiles", "placefiles")]
    add([(lab, "on" if st.get(key) else "off", f"toggle:{key}") for lab, key in lay
         if site_mode or key not in ("cells", "range")], grid=3)
    # -- panes and tools
    out.append(ui.section("Panes and tools", sw, "v · x"))
    panes = st.get("panes") or 1
    add([("Panes", "lbl", None)] + [(str(n), "sel" if panes == n else "opt", f"panes:{n}") for n in (1, 2, 4)] +
        [("Inspect", "on" if st.get("inspect_on") else "off", "inspect"), ("Guide", "act", "guide")])
    return out, hits


def radar_sidebar(m, v, res, side_w, inspect, F, loc, height=None):
    """The radar's sidebar as cards `side_w` wide: the toolbox, then what's under the
    cursor, storm cells, alerts in view and the nowcast. Its click targets go in
    st["_side_hits"] as (row, x0, x1, action), rows and columns those of the cards."""
    st = v.radar_state
    span = st.get("span") or 120
    sw = side_w - 2                     # built two cells narrower: the cards' edges take them
    side, hits = toolbox(v, m, res, sw, loc, compact=bool(height) and height < 56)
    site_mode = res is not None and res.view.source == "site"
    if inspect is not None and res is not None:
        side.append(ui.section("Inspect", sw, "x or Esc to stop" if st.get("inspect_on") else "click the map"))
        side += inspect_lines(res, inspect, F, sw, v)
    hover = st.get("hover")
    if hover is not None and res is not None and hover != inspect:
        side.append(ui.section("Under the mouse", sw))
        side += inspect_lines(res, hover, F, sw, v)
    cells_at = cells_rows = None
    if site_mode and res.view.cells:
        side.append(ui.section("Storm cells", sw, f"{len(res.cells)} tracked" if res.cells else ""))
        if res.cells:
            cells_rows = storm_rows(res, F, sw)
            cells_at = len(side)
            side += cells_rows
        else:
            side.append([("  No storm cells tracked.", "good")])
        if res.tvs:
            side.append(clip([("  ▼ ", "bad"), (f"{len(res.tvs)} tornado vortex signature"
                                                  f"{'s' if len(res.tvs) != 1 else ''}", "bad")], sw))
        if res.meso:
            strong = [x for x in res.meso if "weak" not in x["kind"]]
            side.append(clip([("  ◎ ", "warn"), (f"{len(res.meso)} mesocyclone detection"
                                                  f"{'s' if len(res.meso) != 1 else ''}"
                                                  + (f" ({len(strong)} strong)" if strong else ""), "warn")], sw))
    # alerts in view, click to go (Supercell's alert dock)
    from .alerts import rings_center, threat_text
    area = [a for a in m.get("area_alerts") or [] if a.get("rings") and a.get("dist") is not None
            and a["dist"] <= span * 0.75]
    warns = area or [w for w in m.get("sbw") or [] if w.get("dist") is not None and w["dist"] <= span * 0.7]
    side.append(ui.section("Alerts in view", sw, "click to go" if warns else ""))
    if warns:
        for w in warns[:8]:
            if area:
                rgb, name, tag = w["rgb"], w["event"].replace(" Warning", ""), threat_text(w)
                c = rings_center(w["rings"])
                inside = w.get("in_zip") or w.get("inside")
                exp = w.get("end")
            else:
                from .radar import warning_style
                rgb, _f, _x, tag = warning_style(w)
                name = w["name"].replace(" Warning", "")
                c = rings_center(w["rings"]) if w.get("rings") else None
                inside = w.get("inside")
                exp = w.get("expire")
            where = "covers you" if inside else f"{F.dist(w['dist'])} {compass(w.get('bearing'))}"
            line = clip([("  ▌", ("rgb", rgb, None, "b")), (name, "value"), (f"  {where}", "bad" if inside else "dim")], sw)
            if c:
                hits.append((len(side), 0, sw, f"go:{c[0]:.4f},{c[1]:.4f}"))
            side.append(line)
            sub = (f"until {F.time(exp)}" if exp else "") + (f" · {tag}" if tag else "")
            if sub:
                side.append(clip([("    " + sub, "bad" if "OBSERVED" in tag or "EMERGENCY" in tag else "faint")], sw))
    else:
        side.append([("  none", "good")])
    side.append(ui.section("Nowcast", sw, "estimate"))
    if res and res.analysis:
        for txt, sty in summary_lines(res, F):
            side += wrap(txt, sw, sty, 2)
    elif v.radar is not None and v.radar.busy():
        side.append([("  " + (v.radar.status or "Loading radar…"), "dim")])
    elif v.radar is None:
        side.append([("  The radar needs Pillow.", "warn")])
    elif site_mode and res.product is not None and res.product.key not in ("ref", "cr"):
        side.append([("  Shown with reflectivity (REF).", "dim")])
    else:
        side.append([("  " + (v.radar.last_error or "No radar data yet."), "warn" if v.radar.last_error else "dim")])
    if res and res.notes:
        side.append([])
        for n_ in res.notes[:3]:
            side += wrap(n_, sw, "warn", 2)
    over = len(side) + sum(isinstance(ln, ui.Head) for ln in side) - (height or 10 ** 6)
    if over > 0 and cells_rows and len(cells_rows) > 3:
        # the most threatening cells (they're sorted that way) and a count of the rest
        keep = max(3, len(cells_rows) - over - 1)
        if keep < len(cells_rows):
            more = [clip([(f"  + {len(cells_rows) - keep} more on the map", "dim")], sw)]
            side = side[:cells_at + keep] + more + side[cells_at + len(cells_rows):]
            hits = [(r if r < cells_at else r - (len(cells_rows) - keep - 1), x0, x1, a) for r, x0, x1, a in hits]
    # as cards: a row moves down by the cards above it; a button keeps its column (it loses
    # its two-cell indent and the card's edge puts two cells back)
    side, where = ui.cardify(side, side_w, v.ascii)
    st["_side_hits"] = [(where[r], x0, x1, a) for r, x0, x1, a in hits if r < len(where)]
    return side


# ----------------------------------------------------------------------------
# 5 Stations
# ----------------------------------------------------------------------------

STATION_COLS = [
    ("flag", "", 2, "l", 0),
    ("dist", "Dist", 7, "r", 0),
    ("id", "Station", 8, "l", 1),
    ("name", "Name", 22, "l", 0),
    ("net", "Type", 6, "l", 3),
    ("temp", "Temp", 4, "r", 0),
    ("dew", "Dew", 4, "r", 4),
    ("rh", "Hum", 4, "r", 2),
    ("wind", "Wind {wind}", 10, "l", 1),
    ("gust", "Gust", 4, "r", 5),
    ("press", "{press}", 6, "r", 6),
    ("pcp", "Pcp {precip}", 6, "r", 5),
    ("age", "Age", 4, "r", 2),
]

FILTERS = ["weather", "all", "zip"]
SORTS = ["distance", "temp", "name", "age"]


def station_rows(m, v):
    st = m.get("stations") or {}
    lst = list(st.get("list") or [])
    f = v.sel.get("st_filter", "weather")
    if f == "weather":
        lst = [s for s in lst if s.get("has_met")]
    elif f == "zip":
        lst = [s for s in lst if s.get("in_zip")]
    srt = v.sel.get("st_sort", "distance")
    if srt == "temp":
        lst.sort(key=lambda s: -((s.get("obs") or {}).get("temp") if (s.get("obs") or {}).get("temp") is not None else -999))
    elif srt == "name":
        lst.sort(key=lambda s: s["name"].lower())
    elif srt == "age":
        lst.sort(key=lambda s: s["age"] if s.get("age") is not None else 9e9)
    else:
        lst.sort(key=lambda s: (not s.get("has_met"), s["dist"]))
    return lst


def station_detail(s, m, v, W):
    F = v.fmt
    o = s.get("obs") or {}
    L = []
    flags = []
    if s.get("primary"):
        flags.append(("★ used for current conditions", "accent"))
    if s.get("in_zip"):
        flags.append(("✓ inside your ZIP code", "good"))
    L.append([("  " + s["name"], "value")])
    L.append(clip([("  ", "text"), (f"{s['id']}  ·  {s['net_long']}", "dim")], W))
    if flags:
        L.append([("  ", "text")] + [x for f in flags for x in (f, ("   ", "text"))])
    L.append(clip([("  ", "text"), (f"{F.dist(s['dist'], digits=1)} {compass(s.get('bearing'))} of the ZIP center"
                                     + (f", elevation {F.height(s['elev'])}" if s.get("elev") is not None else ""), "dim")], W))
    L.append([])
    if not s.get("has_met"):
        L += wrap("This site doesn't report weather (it's probably a river or lake gauge, or it hasn't reported "
                  "recently).", W, "dim", 2)
    pairs = [
        ("Observed", f"{F.time(o.get('t'))}  ({F.age(o.get('t'))} ago)" if o.get("t") else "--",
         "warn" if s.get("stale") else "value"),
        ("Temperature", F.t(o.get("temp"), True), temp_style(o.get("temp"))) if o.get("temp") is not None else None,
        ("Feels like", F.t(o.get("feels"), True), temp_style(o.get("feels"))) if o.get("feels") is not None else None,
        ("Dew point", [(F.t(o.get("dew"), True), "value"), ("  " + comfort(o.get("dew"))[0], comfort(o.get("dew"))[1])])
        if o.get("dew") is not None else None,
        ("Humidity", F.pct(o.get("rh"))) if o.get("rh") is not None else None,
        ("Wind", wind_text(F, o.get("wind_deg"), o.get("wind")), wind_style(o.get("wind"))) if o.get("wind") is not None else None,
        ("Gusts", F.wind(o.get("gust"))) if o.get("gust") else None,
        ("Peak gust today", F.wind(o.get("max_gust"))) if o.get("max_gust") else None,
        ("Pressure", F.press(o.get("press"))) if o.get("press") is not None else None,
        ("Visibility", F.vis(o.get("vis"))) if o.get("vis") is not None else None,
        ("Precip, 1 hr", F.precip(o.get("p1"))) if o.get("p1") is not None else None,
        ("Precip today", F.precip(o.get("pday"))) if o.get("pday") is not None else None,
        ("High / low today", f"{F.t(o.get('hi'))} / {F.t(o.get('lo'))}") if o.get("hi") is not None else None,
        ("Snow depth", F.precip(o.get("snowd"))) if o.get("snowd") else None,
        ("Sky / weather", (o.get("desc") or "") + (f"  {o['wx']}" if o.get("wx") else "")) if (o.get("desc") or o.get("wx")) else None,
        ("Data from", "NWS API" if s.get("src") == "NWS" else "Iowa Environmental Mesonet"),
    ]
    L += kv_grid([p for p in pairs if p], W, label_w=17, max_cols=1 if W < 70 else 2)
    if o.get("raw"):
        L.append([])
        L += labeled_wrap("Raw report", o["raw"], W, lw=12, style="dim")
    hist = m.get("_hist", {}).get(s["id"])
    if hist is not None:
        L.append(ui.section("Last 24 hours", note=f"{len(hist)} reports"))
        L += last_day_charts(hist, F, W, time.time(), v, s.get("id") or "")
        L.append([])
        for r in hist[:8]:
            L.append(clip([("  " + F.time(r["t"]).rjust(8), "label"), (F.t(r["temp"]).rjust(5), temp_style(r["temp"])),
                           ("  " + wind_text(F, r["wind_deg"], r["wind"], True).ljust(12), "text"),
                           ("  " + (r["desc"] or ""), "dim")], W))
    elif s.get("src") == "NWS" or s.get("id"):
        L.append([])
        L.append([("  Loading the last 24 hours…", "dim")])
    return L


def last_day_charts(hist, F, width, now, v=None, sid=""):
    """A station's last 24 hours as solid bar charts, an hour a bar: the temperature, the
    strongest wind and the pressure, the hours along the bottom, the ranges in a line
    under them. Where the terminal draws pictures, each chart's bars are one, every bar
    outlined in a darker shade of its color; elsewhere they're text, a sliver apart."""
    h_now = int(now) // 3600 * 3600
    hours = [h_now - (23 - k) * 3600 for k in range(24)]
    bins = {h: [] for h in hours}
    for r in hist:
        if r.get("t") is not None and int(r["t"]) // 3600 * 3600 in bins:
            bins[int(r["t"]) // 3600 * 3600].append(r)

    def each(key, pick):
        out = []
        for h in hours:
            xs = [r[key] for r in bins[h] if r.get(key) is not None]
            out.append(pick(xs) if xs else None)
        return out

    def mean(xs):
        return sum(xs) / len(xs)

    temps, winds, press = each("temp", mean), each("wind", max), each("press", mean)
    charts = [(name, vals) for name, vals in (("Temp", temps), ("Wind", winds), ("Press", press))
              if sum(1 for x in vals if x is not None) >= 2]
    if not charts:
        return []
    w = width - 2
    gutter = 7
    slot = max(1, (w - gutter) // len(hours))
    labels = ui.time_labels(hours, F, slot, now, room=6)
    pictures = v is not None and v.images.pixels and gfx.HAVE_PIL

    def bars(name, vals, height, lo, hi, style, tops, last):
        plot = None
        if pictures:
            from .charts import bars_image
            colors = [v.theme.spec(style(x) if callable(style) else style[i] if isinstance(style, list) else style)[0]
                      if x is not None else (0, 0, 0) for i, x in enumerate(vals)]
            back = v.theme.rgb("card_bg")
            key = f"sthist:{sid}:{name}:{hours[-1]}:{hash(tuple(vals))}:{v.theme.name}:{back}"
            plot = v.images.block(f"sthist{name}", key, slot * len(hours), height,
                                  lambda mode, pw, ph: bars_image(vals, lo, hi, colors, pw, ph, back)
                                  if mode == "pixels" else None, back=back)
        return bar_chart(vals, w, height, lo=lo, hi=hi, style=style, tops=tops, gutter=gutter, title=name,
                         base=last, labels=labels if last else None, plot=plot)
    out, notes = [], []
    for k, (name, vals) in enumerate(charts):
        last = k == len(charts) - 1
        nums = [x for x in vals if x is not None]
        if out:
            out.append([])                      # a row between the charts
        if name == "Temp":
            shown = [F.tv(x) for x in vals]
            hi = max(x for x in shown if x is not None)
            span = max(hi - min(x for x in shown if x is not None), 8.0 if F.imp else 5.0)
            i_hi, i_lo = shown.index(hi), shown.index(min(x for x in shown if x is not None))
            out += bars(name, shown, 5, hi - span * 1.25, hi, [temp_style(x) for x in vals],
                        [F.t(x) if i in (i_hi, i_lo) else "" for i, x in enumerate(vals)], last)
            notes.append(f"{F.t(min(nums))} to {F.t(max(nums))}")
        elif name == "Wind":
            top = max(nums)
            i_hi = vals.index(top)
            out += bars(name, vals, 4, 0, max(5.0, top),
                        lambda x: wind_style(x) if wind_style(x) != "text" else "label",
                        [F.wind(x) if i == i_hi and x else "" for i, x in enumerate(vals)], last)
            notes.append(f"wind up to {F.wind(top)}")
        else:
            hi = max(nums)
            span = max(hi - min(nums), 3.0)
            out += bars(name, vals, 4, hi - span * 1.25, hi, "accent", None, last)
            first_p = next(x for x in vals if x is not None)
            notes.append(f"pressure {F.press_delta(nums[-1] - first_p)}")
    out = [[("  ", "text")] + ln if ln else [] for ln in out]
    return out + wrap("   ".join(notes), width, "dim", 2)


def render_stations(m, v, W, H, printing=False):
    F = v.fmt
    st = m.get("stations") or {}
    zc = m.get("zcta") or {}
    loc = m.get("loc") or {}
    if not st.get("list"):
        notes, off = m.get("notes") or {}, m.get("off") or {}
        why = notes.get("stations") or off.get("stations")
        if why:
            L = [[("  Not available here: ", "dim"), (why.rstrip(".") + ".", "text")]]
        elif m["errors"].get("stations"):
            L = [[("  The station feed isn't available right now.", "dim")]]
        elif st.get("list") is not None and st.get("radius"):
            L = [[(f"  No weather stations reported within {F.dist(st.get('radius'))}. "
                   "Raise the radius with + on this screen.", "dim")]]
        else:
            L = [[("  Gathering the live station feed…", "dim")]]
        L += ui.error_lines(m, ("stations", "iem_currents"), W)
        return {"lines": L}
    wide = W >= 140 and not printing
    side_w = max(48, min(64, W // 3)) if wide else 0
    main_w = W - side_w - 1 if side_w else W
    rows = station_rows(m, v)
    sel = max(0, min(v.sel.get("stations", 0), len(rows) - 1)) if rows else 0
    zipc = loc.get("zip") or ""
    cw = main_w - 2                  # lines are built with the usual indent; the cards' edges take it
    L = [ui.section("Live station feed",
                    note=f"{len(st['list'])} sites within {F.dist(st.get('radius'))} of "
                         f"{('ZIP ' if zipc.isdigit() else '') + zipc if zipc else 'your location'}" +
                         (f", {st.get('in_zip')} inside the ZIP" if zc.get("rings") else ""))]
    sm = st.get("summary") or {}
    if sm.get("n"):
        line = [("  ", "text"), (f"{sm['n']} reporting now: ", "label"),
                (F.t(sm["min"]), temp_style(sm["min"])), (" to ", "dim"), (F.t(sm["max"]), temp_style(sm["max"])),
                (f", average {F.t(sm['avg'])}", "dim")]
        if sm.get("warm"):
            line += [("   warmest ", "label"), (f"{sm['warm']['name'][:18]}", "text")]
        if sm.get("windiest"):
            wo = sm["windiest"]["obs"]
            line += [("   windiest ", "label"), (f"{sm['windiest']['name'][:18]} {F.wind(wo.get('gust') or wo.get('wind'))}", "text")]
        L.append(clip(line, cw))
    nets = st.get("nets") or {}
    if nets:
        L.append(clip([("  ", "text"), ("Networks: " + ", ".join(f"{k} {n}" for k, n in sorted(nets.items(), key=lambda x: -x[1])), "dim"),
                       (f"   updated {F.age(st.get('ts'))} ago" if st.get("ts") else "", "dim")], cw))
    L.append(clip([("  ", "text"), ("f", "accent"), (f" filter: {v.sel.get('st_filter', 'weather')}   ", "dim"),
                   ("o", "accent"), (f" sort: {v.sel.get('st_sort', 'distance')}   ", "dim"),
                   ("+/-", "accent"), (" radius   ", "dim"), ("Enter", "accent"), (" use for current conditions   ", "dim"),
                   ("★", "accent"), (" current  ", "dim"), ("✓", "good"), (" in ZIP", "dim")], cw))
    # the table in a card of its own; its rows keep the column the ▸ marker goes in
    L.append(ui.section("Stations", note=f"{len(rows)} shown · ↑ ↓ pick one" if not printing else "", indent=0))
    cols = fit_columns(unit_cols(STATION_COLS, F), main_w - 4, "name", 34)
    L.append(table_header(cols))
    L.append([("  " + "─" * table_width(cols), "faint")])
    sticky = len(L)
    cursor = None
    rowmap = {}
    if not rows:
        L.append([("  No stations match this filter. Press f to change it.", "dim")])
    for i, s in enumerate(rows):
        o = s.get("obs") or {}
        flag = "★" if s.get("primary") else "✓" if s.get("in_zip") else " "
        dim = s.get("stale") or not s.get("has_met")
        pcp = o.get("pday") if o.get("pday") is not None else o.get("p1")
        cells = {
            "flag": (flag, "accent" if s.get("primary") else "good"),
            "dist": (f"{F.dist(s['dist'], False, 1)} {compass(s.get('bearing'))}", "dim"),
            "id": (s["id"], "label"),
            "name": (s["name"], "dim" if dim else "text"),
            "net": (s["net"], "dim"),
            "temp": (F.t(o.get("temp")), "dim" if dim else temp_style(o.get("temp"))),
            "dew": (F.t(o.get("dew")), comfort(o.get("dew"))[1] if not dim else "dim"),
            "rh": (F.pct(o.get("rh")), "text" if not dim else "dim"),
            "wind": (wind_text(F, o.get("wind_deg"), o.get("wind"), False), wind_style(o.get("wind")) if not dim else "dim"),
            "gust": (F.wind(o.get("gust"), False) if o.get("gust") else "", wind_style(o.get("gust"))),
            "press": (F.press(o.get("press"), False), "text" if not dim else "dim"),
            "pcp": (F.precip(pcp, False) if pcp is not None else "", "rain" if pcp else "dim"),
            "age": (F.age(o.get("t")) if o.get("t") else "--", "warn" if s.get("stale") else "dim"),
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
    feed = [s_ for s_ in st["list"] if (s_.get("obs") or {}).get("t") and s_.get("has_met")]
    feed.sort(key=lambda s_: -s_["obs"]["t"])
    room = H - len(L) - 6
    if feed and room >= 4 and not printing:
        L.append(ui.section("Latest reports", note="newest first, refreshed every few minutes while this screen is open"))
        for k, s_ in enumerate(feed[:room]):
            o = s_["obs"]
            L.append(ui.zebra(clip([("  " + F.time(o["t"]).rjust(8), "label"), ("  " + s_["id"].ljust(7), "accent"),
                                    (" " + s_["name"][:26].ljust(27), "text"), (F.t(o.get("temp")).rjust(4), temp_style(o.get("temp"))),
                                    ("  " + wind_text(F, o.get("wind_deg"), o.get("wind"), True).ljust(12),
                                     wind_style(o.get("wind"))),
                                    (f"  {F.pct(o.get('rh'))} RH" if o.get("rh") is not None else "", "dim"),
                                    (f"  {s_['net']}", "dim")], cw), cw, k))
    nets = {}
    for s_ in st["list"]:
        if s_.get("net") and s_.get("net_long"):
            nets.setdefault(s_["net"], [s_["net_long"], 0])[1] += 1
    L.append(ui.section("Station networks", note="who runs the stations on this list"))
    if nets and not printing and H - len(L) - 6 >= min(len(nets), 4) + 2:
        for net, (long_, n) in sorted(nets.items(), key=lambda x: -x[1][1])[:max(1, H - len(L) - 6)]:
            L.append(clip([("  " + net.ljust(9), "accent"), (f"{n:>3}  ", "label"), (long_, "text")], cw))
        L.append([])
    L += wrap("Sources: NWS API (airport ASOS/AWOS, citizen CWOP stations, MesoWest partners) and the Iowa "
              "Environmental Mesonet (state DOT road sensors, co-op observers, river gauges, soil and climate "
              "networks). Stations inside the ZIP use the Census ZIP Code Tabulation Area outline.", cw, "dim")
    errs = ui.error_lines(m, ("stations", "iem_currents", "zcta"), main_w)
    out = ui.recard({"lines": L, "sticky": sticky, "cursor": cursor, "rowmap": rowmap}, main_w, v.ascii)
    out["lines"] += errs
    if rows and not printing:
        s = rows[sel]
        if s.get("id") and v.store is not None:
            v.store.request_history(s["id"])
            raw = v.store.get(f"hist:{s['id']}")
            if raw is not None:
                m.setdefault("_hist", {})[s["id"]] = build_station_history(raw)
        title = f"{s['id']}  {s['name']}"
        if side_w:
            sw = side_w - 2
            det = [ui.section("Selected station", sw)] + station_detail(s, m, v, sw)
            mh = min(16, side_w // 3, H - len(det) - 5)     # the map takes what the details leave
            if v.radar is not None and v.gmode != "ascii" and mh >= 8:
                span = max(15, int((st.get("radius") or 10) * 2.5))
                lines, _res = ui.radar_block(v, m, side_w - 4, mh, slot="stationmap", mini=True, span=span)
                det = [ui.section("Station map", sw, f"{F.dist(span)} across")] + [[("  ", "text")] + ln for ln in lines] + det
            out["side"], out["side_w"] = ui.cardify(det, side_w, v.ascii)[0], side_w
        else:
            out["footer_title"] = title
            out["footer"] = station_detail(s, m, v, W - 2)
    return out
