"""'Local on the 8s': a full-screen, auto-advancing WeatherSTAR-style
presentation of your local weather, with optional narration."""

import datetime as dt
import time

from . import charts, icons, scripts, ui
from .radar import summary_lines
from .text import big_text, center_line, corners, padto, seglen, wrap_plain
from .theme import alert_style, blend, pop_style, temp_style
from .util import first, log
from .wxmath import SYNODIC, aqi_cat, compass, moon_info, next_moon_phases, sun_day, uv_cat

PAGES = ["alerts", "current", "observations", "radar", "forecast", "extended", "almanac", "air"]
TITLES = {"alerts": ("Weather", "Alert"), "current": ("Current", "Conditions"),
          "observations": ("Latest", "Observations"), "radar": ("Local", "Radar"),
          "forecast": ("Local", "Forecast"), "extended": ("Extended", "Forecast"),
          "almanac": ("Almanac", ""), "air": ("Air Quality", "& UV Index")}
P = "panel"
POLLUTANT_NAMES = {"PM2.5": "fine particles", "PM10": "coarse particles", "O3": "ozone",
                   "NO2": "nitrogen dioxide", "SO2": "sulfur dioxide", "CO": "carbon monoxide"}


class Local8s:
    def __init__(self, app):
        self.app = app
        self.idx = 0
        self.t0 = time.time()
        self.paused = False
        self.narrated = set()
        self.narrate_failed = False
        self.pages = []
        self.subpage = 0

    def available(self, m):
        out = []
        for p in PAGES:
            if p == "alerts" and not m.get("alerts"):
                continue
            if p == "observations" and not _obs_rows(m):
                continue
            if p == "almanac" and not m.get("loc"):
                continue
            if p == "air" and not (m.get("air") or {}).get("us"):
                continue
            out.append(p)
        return out or ["current"]

    def page(self, m):
        self.pages = self.available(m)
        self.idx %= len(self.pages)
        return self.pages[self.idx]

    def advance(self, step=1):
        self.idx = (self.idx + step) % max(1, len(self.pages))
        self.t0 = time.time()
        self.subpage = 0
        if self.app.speech.active:
            self.app.speech.stop()

    def tick(self, m):
        """Advance automatically; returns True when the page changed."""
        if self.paused:
            return False
        page = self.page(m)
        secs = int(self.app.cfg.get("local8s_seconds") or 12)
        narrate = str(self.app.cfg.get("narrate") or "off") == "on"
        sp = self.app.speech
        if sp.active:
            return False  # never cut a reading short; turn the page when it ends
        if narrate and not self.narrate_failed:
            key = (page, self.idx, int(self.t0))
            if key not in self.narrated:
                self.narrated.add(key)
                if len(self.narrated) > 200:
                    self.narrated = {key}
                title, text = self.script(m, page)
                if text:
                    if sp.speak(title, text):
                        return True
                    # no voice available: say why once, then keep the normal page timer
                    self.narrate_failed = True
                    self.app.flash(f"Narration is off for now: {sp.error}", 8)
            if self.narrated_done(key) and time.time() - self.t0 >= 2.5:
                self.advance()
                return True
            return False
        if time.time() - self.t0 >= secs:
            self.advance()
            return True
        return False

    def narrated_done(self, key):
        """This page's narration ran and finished (or there was nothing to say)."""
        return key in self.narrated

    # -- narration ----------------------------------------------------------------------
    def script(self, m, page):
        v = self.app
        F = v.fmt
        if page == "forecast":
            return "Local forecast", [f"{name}: {text}" for name, text, _k, _d in _periods(m, 3)]
        if page == "observations":
            out = ["Here are the latest observations from around the area."]
            for s in _obs_rows(m)[:6]:
                o = s["obs"]
                if o.get("temp") is not None:
                    out.append(f"{s['name']}, {F.tv(o['temp']):.0f} degrees.")
            return "Latest observations", out
        fn = {"current": scripts.now_script, "radar": scripts.radar_script, "extended": scripts.daily_script,
              "alerts": scripts.alerts_script, "air": scripts.air_script, "almanac": scripts.sky_script}.get(page)
        if fn is None:
            return "", []
        title, lines = fn(m, v)
        if page == "current":
            # the forecast and alerts have pages of their own
            skip = tuple(f"{name}:" for name, _t in ui.periods_text(m, 3)) + ("Attention:",)
            lines = [ln for ln in lines if not ln.startswith(skip)]
        if page == "extended":
            lines = lines[:6]
        return title, lines

    # -- drawing ------------------------------------------------------------------------
    def draw(self, canvas, m, W, H):
        v = self.app
        F = v.fmt
        page = self.page(m)
        th = v.theme
        # header: gradient band with a two-line title and the clock
        h1, h2 = th.rgb("head1"), th.rgb("head2")
        for y in range(3):
            for x in range(W):
                c = blend(h1, h2, x / max(1, W - 1))
                c = blend(c, (0, 0, 0), 0.15 * (2 - y) / 2)
                canvas.chars[y][x] = " "
                canvas.styles[y][x] = ("rgb", th.rgb("head_title"), c, "b")
        t1, t2 = TITLES.get(page, ("", ""))
        hdr_bg = blend(h1, h2, 0.2)
        canvas.put(0, 3, [(t1, ("rgb", th.rgb("head_title"), blend(h1, h2, 3 / max(1, W)), "b"))])
        canvas.put(1, 3, [(t2, ("rgb", th.rgb("head_title"), blend(h1, h2, 3 / max(1, W)), "b"))])
        loc = m.get("loc") or {}
        canvas.put(2, 3, [(loc.get("name") or "", ("rgb", th.rgb("head_label"), hdr_bg, ""))])
        now = F.now()
        clock = F.clock(True)
        date = f"{now:%a %b} {now.day}".upper()
        canvas.put(0, max(0, W - len(clock) - 3), [(clock, ("rgb", th.rgb("head_white"), h2, "b"))])
        canvas.put(1, max(0, W - len(date) - 3), [(date, ("rgb", th.rgb("head_white"), h2, "b"))])
        brand = "skydrop · Local on the 8s"
        canvas.put(2, max(0, W - len(brand) - 3), [(brand, ("rgb", th.rgb("head_label"), h2, ""))])
        # field: vertical gradient, then a framed panel like the WeatherSTAR 4000
        top, bottom = 3, H - 2
        ph = bottom - top
        b0, b1 = th.rgb("bg"), th.rgb("bar")
        if th.light:
            b1 = blend(b0, b1, 0.3)       # a light page: just a hint of the band color
        for y in range(top, bottom):
            c = blend(b0, b1, (y - top) / max(1, ph - 1))
            for x in range(W):
                canvas.chars[y][x] = " "
                canvas.styles[y][x] = ("rgb", th.rgb("text"), c, "")
        inner_w = max(20, min(W - 6, 120))
        x0 = (W - inner_w) // 2
        box_top, box_h = top + 1, ph - 3
        if box_h < 5:
            box_top, box_h = top, ph
        canvas.fill(box_top, max(0, x0 - 2), inner_w + 4, box_h, "panel")
        edge = ("rgb", th.rgb("label"), th.rgb("bg2"), "")
        hz, vt = ("-", "|") if v.ascii else ("─", "│")
        tl, tr, bl, br = corners(v.ascii)
        canvas.put(box_top, max(0, x0 - 2), [(tl + hz * (inner_w + 2) + tr, edge)])
        canvas.put(box_top + box_h - 1, max(0, x0 - 2), [(bl + hz * (inner_w + 2) + br, edge)])
        for y in range(box_top + 1, box_top + box_h - 1):
            canvas.put(y, max(0, x0 - 2), [(vt, edge)])
            canvas.put(y, x0 + inner_w + 1, [(vt, edge)])
        avail = max(1, box_h - 2)
        body = self.body(m, page, inner_w, avail)[:avail]
        off = max(0, (avail - len(body)) // 2) if page != "radar" else 0
        for i, ln in enumerate(body):
            canvas.put(box_top + 1 + off + i, x0, _on_panel(ln, th), inner_w)
        # page dots
        if box_h == ph - 3:
            dots = []
            for i, _p in enumerate(self.pages):
                dots.append(("●" if i == self.idx else "○", "paneltitle" if i == self.idx else "paneldim"))
                dots.append((" ", "panel"))
            dw = seglen(dots)
            dot_bg = blend(b0, b1, (ph - 2) / max(1, ph - 1))
            canvas.put(bottom - 1, max(0, (W - dw) // 2), [(t, ("rgb", th.spec(st_)[0], dot_bg, "b")) for t, st_ in dots])
        # LDL
        sp = v.speech
        if sp.active and sp.current():
            line = [(" ♪ ", "speaklabel"), (sp.current(), "speak")]
            canvas.put(H - 2, 0, padto(line, W, "speak"))
        elif time.time() < getattr(v, "flash_until", 0):
            canvas.put(H - 2, 0, padto([(" " + v.flash_msg, "stkey")], W, "status"))
        else:
            canvas.put(H - 2, 0, v.ticker.render(W, "crawl" if (v.cfg.get("ticker") == "crawl") else "paged"))
        hint = " Esc/L exit   ← → page   space " + ("resume" if self.paused else "pause") + \
               "   s " + ("stop" if sp.active else "read this page") + "   n narration " + \
               ("on" if str(v.cfg.get("narrate")) == "on" else "off") + " "
        canvas.put(H - 1, 0, padto([(hint, "status")], W, "status"))

    def body(self, m, page, w, h):
        large = h >= 22 and w >= 90
        fn = getattr(self, "_p_" + page, None)
        try:
            return fn(m, w, h, large) if fn else []
        except Exception as e:  # never let one page take the show down
            log(f"local8s {page}: {e!r}")
            return [center_line([("This page is unavailable right now.", "paneldim")], w, P)]

    # -- pages --------------------------------------------------------------------------
    def _p_alerts(self, m, w, h, large):
        F = self.app.fmt
        alerts = (m.get("alerts") or [])[:3]
        out = []
        budget = max(3, (h - 1) // max(1, len(alerts)) - 5)
        for a in alerts:
            out.append([(f" {a['event']} ".center(w), alert_style(a.get("event") or ""))])
            sub = []
            if a.get("end"):
                sub.append(f"until {F.dtime(a['end'])}")
            if a.get("sender"):
                sub.append(a["sender"])
            if sub:
                out.append(center_line([("   ·   ".join(sub), "paneltitle")], w, P))
            out.append([])
            head = a.get("nws_headline") or a.get("headline") or ""
            lines = [center_line([(ln, P)], w, P) for ln in wrap_plain(head, w - 10)] if head else []
            desc = " ".join((a.get("desc") or "").split())
            if desc and (large or not lines):
                if lines:
                    lines.append([])
                lines += [[("    " + ln, P)] for ln in wrap_plain(desc, w - 10)]
            if len(lines) > budget:
                lines = lines[:budget - 1] + [[("    …more on the Alerts screen (6)", "paneldim")]]
            out += lines
            out.append([])
        return out

    def _p_current(self, m, w, h, large):
        v, F = self.app, self.app.fmt
        obs = m.get("obs") or {}
        now = m.get("now") or {}
        temp = now.get("temp")
        desc = now.get("desc") or ""
        kind = now.get("kind") or icons.from_text(desc)
        day = now.get("is_day")
        if day is None:
            sr, ss, _ = ui.sun_pair(m, F)
            day = bool(sr and ss and sr <= time.time() <= ss)
        gust = now.get("gust")
        rows = [("Humidity", F.pct(now.get("rh"))),
                ("Dewpoint", F.t(now.get("dew"))),
                ("Ceiling", _ceiling(obs, F)),
                ("Visibility", F.vis(now.get("vis"))),
                ("Pressure", F.press(now.get("press")) + _trend(obs)),
                ("Wind", _wind(F, now.get("wind_deg"), now.get("wind"))),
                ("Gusts", F.wind(gust) if gust else "None"),
                ("Feels Like", F.t(first(now.get("feels"), temp)))]
        tcol = _tc(v, temp)
        title = (desc or icons.label(kind) or "")[:40]
        out = []
        if not large:
            ic = ui.icon_block(v, kind, day, 13, 5, slot="l8icon", bg_role="bg2")
            big = ui.big_temp(v, F.t(temp))
            left = [center_line([(title[:28], "paneltitle")], 30, P), []]
            for i in range(5):
                left.append([(" ", P)] + padto(ic[i], 13) + [(" ", P), (big[i] if i < len(big) else "", tcol)])
            right = [[(lab.ljust(12), "panellabel"), (val, P)] for lab, val in rows]
            colw = max(32, w // 2)
            for i in range(max(len(left), len(right))):
                out.append([(" ", P)] + padto(left[i] if i < len(left) else [], colw, P) + (right[i] if i < len(right) else []))
            return out
        ic_w, ic_h = 26, 12
        ic = ui.icon_block(v, kind, day, ic_w, ic_h, slot="l8icon", big=True, bg_role="bg2")
        big = big_text(F.t(temp), ascii_only=v.ascii, scale=1 if v.ascii else 2)
        bw = max((len(r) for r in big), default=0)
        lw = ic_w + 4 + bw
        left = [center_line([(title, "paneltitle")], lw, P), []]
        n = max(ic_h, len(big))
        for i in range(n):
            ln = [(" ", P)] + (padto(ic[i], ic_w) if i < len(ic) else [(" " * ic_w, P)]) + [("   ", P)]
            bi = i - (n - len(big)) // 2
            ln.append((big[bi] if 0 <= bi < len(big) else "", tcol))
            left.append(ln)
        right = []
        for lab, val in rows:
            right += [[(lab.ljust(13), "panellabel"), (val, "paneltitle")], []]
        right = right[:-1]
        gap, rw = 8, 30
        margin = max(0, (w - (lw + gap + rw)) // 2)
        lpad = max(0, (len(right) - len(left)) // 2)
        left = [[] for _ in range(lpad)] + left
        for i in range(max(len(left), len(right))):
            out.append([(" " * margin, P)] + padto(left[i] if i < len(left) else [], lw + gap, P) +
                       (right[i] if i < len(right) else []))
        # today at a glance
        today = next((d for d in m.get("daily") or [] if d.get("date") == F.today()), None)
        sr, ss, _ = ui.sun_pair(m, F)
        bits = []
        if today and today.get("hi") is not None:
            bits += [("High ", "panellabel"), (F.t(today["hi"]), _tc(v, today["hi"])), ("     ", P)]
        if today and today.get("lo") is not None:
            bits += [("Low ", "panellabel"), (F.t(today["lo"]), _tc(v, today["lo"])), ("     ", P)]
        if today and today.get("pop") is not None:
            bits += [("Precip ", "panellabel"), (f"{today['pop']:.0f}%", P), ("     ", P)]
        if sr:
            bits += [("Sunrise ", "panellabel"), (F.time(sr), P), ("     ", P)]
        if ss:
            bits += [("Sunset ", "panellabel"), (F.time(ss), P), ("     ", P)]
        aqi = (m.get("air") or {}).get("us")
        if aqi is not None:
            bits += [("Air ", "panellabel"), (f"{aqi} {aqi_cat(aqi)[0]}", P)]
        if bits and len(out) + 3 <= h:
            out += [[], [("  " + ("-" if v.ascii else "─") * (w - 4), "paneldim")], center_line(bits, w, P)]
        # the next several hours
        hrs = [r for r in m.get("hourly") or [] if r.get("t") and r["t"] > time.time() - 1800 and r.get("temp") is not None]
        step = 2 if len(hrs) >= 16 else 1
        colw = 14
        sel = hrs[1::step][:max(1, (w - 4) // colw)]
        if sel and len(out) + 7 <= h:
            out += [[], center_line([("Coming up", "paneltitle")], w, P)]
            cells = [[], [], [], []]
            for r in sel:
                pop = r.get("pop")
                cells[0].append((F.hour(r["t"]).center(colw), "panellabel"))
                cells[1].append((F.t(r.get("temp")).center(colw), _tc(v, r.get("temp"))))
                cells[2].append(((icons.label(r.get("kind")) or (r.get("desc") or ""))[:colw - 1].center(colw), P))
                cells[3].append(((f"{pop:.0f}% precip" if pop and pop >= 10 else "").center(colw), pop_style(pop)))
            for ln in cells:
                out.append(center_line(ln, w, P))
        return out

    def _p_observations(self, m, w, h, large):
        v, F = self.app, self.app.fmt
        rows = _obs_rows(m)
        out = []
        if not large:
            wxw = 18 if any(s["obs"].get("desc") for s in rows) else 0
            out.append([("  " + "Location".ljust(30) + "Temp".rjust(5) + "   " + ("Weather".ljust(wxw) if wxw else "") + "Wind",
                         "paneltitle")])
            for s in rows[:max(1, h - 2)]:
                o = s["obs"]
                out.append([("  " + s["name"][:29].ljust(30), P), (F.t(o.get("temp")).rjust(5), _tc(v, o.get("temp"))),
                            ("   " + ((o.get("desc") or "")[:17].ljust(wxw) if wxw else ""), P),
                            (_wind(F, o.get("wind_deg"), o.get("wind")), P)])
            return out
        has_wx = any(s["obs"].get("desc") for s in rows)
        cols = [("Location", 32, "l"), ("Temp", 5, "r"), ("Humid", 7, "r"), ("Dewpt", 7, "r"), ("", 3, "l"),
                ("Wind", 13, "l"), ("Weather", 20 if has_wx else 0, "l"), ("Away", 7, "r")]
        tw = sum(c[1] for c in cols)
        margin = " " * max(0, (w - tw) // 2)
        head = "".join(t.ljust(n) if a == "l" else t.rjust(n) for t, n, a in cols if n)
        out.append([(margin + head, "paneltitle")])
        out.append([(margin + ("-" if v.ascii else "─") * tw, "paneldim")])
        room = h - 5
        dbl = len(rows) * 2 <= room
        for s in rows[:room // (2 if dbl else 1)]:
            o = s["obs"]
            wx = o.get("desc") or ""
            out.append([(margin + s["name"][:31].ljust(32), P),
                        (F.t(o.get("temp")).rjust(5), _tc(v, o.get("temp"))),
                        (F.pct(o.get("rh")).rjust(7), P),
                        (F.t(o.get("dew")).rjust(7), P),
                        ("   ", P),
                        (_wind(F, o.get("wind_deg"), o.get("wind"))[:12].ljust(13), P),
                        ((wx[:19].ljust(20)) if has_wx else "", P),
                        ((F.dist(s.get("dist")) if s.get("dist") is not None else "").rjust(7), "paneldim")])
            if dbl:
                out.append([])
        temps = [(s["obs"]["temp"], s["name"]) for s in rows if s["obs"].get("temp") is not None]
        if len(temps) >= 2:
            hi, lo = max(temps), min(temps)
            if out and out[-1] == []:
                out.pop()
            out += [[], center_line([("Warmest ", "panellabel"), (F.t(hi[0]), _tc(v, hi[0])), (f" {hi[1][:30]}", P),
                                     ("        Coolest ", "panellabel"), (F.t(lo[0]), _tc(v, lo[0])), (f" {lo[1][:30]}", P),
                                     ("        Reporting ", "panellabel"), (str(len(rows)), P)], w, P)]
        return out

    def _p_radar(self, m, w, h, large):
        v, F = self.app, self.app.fmt
        extra = 3 if large else 1
        lines, res = ui.radar_block(v, m, w, max(6, h - extra), slot="l8radar",
                                    span=min(240, int(v.cfg.get("radar_span") or 120)))
        out = list(lines)
        if res:
            txt = "   ".join(t for t, _s in summary_lines(res, F, compact=True)[:2])
            out.append(center_line([(txt[:w], P)], w, P))
        if large:
            out.append([])
            out.append(center_line(ui.radar_legend(min(w, 72), v.ascii)[0] if ui.radar_legend(min(w, 72), v.ascii) else [], w, P))
        return out

    def _p_forecast(self, m, w, h, large):
        v = self.app
        pers = _periods(m, 8 if large else 3)
        if not pers:
            return [center_line([("No NWS forecast text is available.", "paneldim")], w, P)]
        out = []
        if not large:
            for name, text, _k, _d in pers:
                out.append([(name, "paneltitle")])
                out += [[(ln, P)] for ln in wrap_plain(text, w - 2)]
                out.append([])
            return out[:h]
        tw = w - 22
        for i, (name, text, kind, day) in enumerate(pers):
            lines = wrap_plain(text, tw)
            blk = max(5, 1 + len(lines))
            if out and len(out) + blk > h:
                break
            ic = ui.icon_block(v, kind, day, 13, 5, slot=f"l8fc{i}", bg_role="bg2")
            body = [[(name, "paneltitle")]] + [[(ln, P)] for ln in lines]
            for k in range(blk):
                left = [("   ", P)] + (padto(ic[k], 13) if k < len(ic) else [(" " * 13, P)]) + [("    ", P)]
                out.append(left + (body[k] if k < len(body) else []))
            out.append([])
        if out and out[-1] == []:
            out.pop()
        return out

    def _p_extended(self, m, w, h, large):
        v = self.app
        days = 7 if large else 5
        strip = ui.extended_strip(v, m, w, days, "l8ext", bg_role="bg2")
        sw = max((seglen(ln) for ln in strip), default=0)
        pad = [(" " * max(0, (w - sw) // 2), P)]
        out = [pad + [(t, s if s != "text" else P) for t, s in ln] for ln in strip]
        if large:
            rows = [r for r in (m.get("daily") or [])[:days] if r.get("hi") is not None and r.get("lo") is not None]
            if rows and len(out) + len(rows) + 3 <= h:
                out += [[], [], center_line([("This week's temperature range", "paneltitle")], w, P), []]
                out += _range_bars(v, rows, w, dbl=len(out) + 2 * len(rows) <= h)
        return out

    def _p_almanac(self, m, w, h, large):
        v, F = self.app, self.app.fmt
        loc = m.get("loc") or {}
        if loc.get("lat") is None:
            return []
        today = F.today()
        days = [today - dt.timedelta(days=1), today, today + dt.timedelta(days=1)]
        s0, s1, s2 = (sun_day(d, loc["lat"], loc["lon"], F.tz) for d in days)
        L = 15
        left = [[("".ljust(L), P), (f"{days[1]:%A}".ljust(16), "paneltitle"), (f"{days[2]:%A}", "paneltitle")],
                [("Sunrise".ljust(L), "panellabel"), (F.time(s1["sun"][0]).ljust(16), P), (F.time(s2["sun"][0]), P)],
                [("Sunset".ljust(L), "panellabel"), (F.time(s1["sun"][1]).ljust(16), P), (F.time(s2["sun"][1]), P)]]
        if large and s1.get("length"):
            left.append([("Daylight".ljust(L), "panellabel"), (F.duration(s1["length"]).ljust(16), P),
                         (F.duration(s2["length"]) if s2.get("length") else "", P)])
            if s0.get("length"):
                d = s1["length"] - s0["length"]
                left.append([("".ljust(L), P), (f"{'+' if d >= 0 else '-'}{abs(d) // 60}m {abs(d) % 60:02d}s compared with yesterday", "paneldim")])
            if s1.get("noon"):
                left.append([("Solar noon".ljust(L), "panellabel"), (F.time(s1["noon"]), P)])
            if s1["golden"][1]:
                left.append([("Golden hour".ljust(L), "panellabel"), (F.time(s1["golden"][1]), P)])
            if s1["civil"][1]:
                left.append([("Dusk".ljust(L), "panellabel"), (F.time(s1["civil"][1]), P)])
        left.append([])
        ph = next_moon_phases(time.time(), 4)
        left.append([("Moon phases".ljust(L), "panellabel")] + [(f"{n:<8}", "paneltitle") for n, _t in ph])
        left.append([("".ljust(L), P)] + [(f"{F.local(t):%b} {F.local(t).day}".ljust(8), P) for _n, t in ph])
        a = m.get("almanac") or {}
        if a.get("normal_hi") is not None or a.get("rec_hi") is not None:
            left.append([])
        if a.get("normal_hi") is not None:
            left.append([("Normal".ljust(L), "panellabel"), ("High ", P), (F.t(a["normal_hi"]), _tc(v, a["normal_hi"])),
                         ("   Low ", P), (F.t(a.get("normal_lo")), _tc(v, a.get("normal_lo")))])
        if a.get("rec_hi") is not None:
            left.append([("Record".ljust(L), "panellabel"), ("High ", P), (F.t(a["rec_hi"]), _tc(v, a["rec_hi"])),
                         (f" ({a.get('rec_hi_year')})   Low ", P), (F.t(a.get("rec_lo")), _tc(v, a.get("rec_lo"))),
                         (f" ({a.get('rec_lo_year')})", P)])
        if a.get("mtd_precip") is not None:
            left.append([("Month precip".ljust(L), "panellabel"),
                         (f"{F.precip(a['mtd_precip'])}  (normal {F.precip(a.get('mtd_normal'))})", P)])
        lw = max(seglen(ln) for ln in left)
        if not large:
            return [[("  ", P)] + ln for ln in left]
        age, illum, name = moon_info(time.time())
        mw, mh = 24, 11
        moon = ui.moon_block(v, age / SYNODIC, mw, mh, slot="l8moon", bg_role="bg2")
        right = [ln for ln in moon]
        right += [[], center_line([(name, "paneltitle")], mw, P),
                  center_line([(f"{illum * 100:.0f}% illuminated", P)], mw, P),
                  center_line([(f"{age:.1f} days old", "paneldim")], mw, P)]
        gap = 10
        margin = max(0, (w - (lw + gap + mw)) // 2)
        n = max(len(left), len(right))
        loff, roff = max(0, (n - len(left)) // 2), max(0, (n - len(right)) // 2)
        out = []
        for i in range(n):
            li, ri = i - loff, i - roff
            out.append([(" " * margin, P)] + padto(left[li] if 0 <= li < len(left) else [], lw + gap, P) +
                       (right[ri] if 0 <= ri < len(right) else []))
        return out

    def _p_air(self, m, w, h, large):
        v, F = self.app, self.app.fmt
        air = m.get("air") or {}
        bg2 = v.theme.rgb("bg2")
        lab, sty, advice = aqi_cat(air.get("us"))
        acol = ("rgb", v.theme.spec(sty)[0], bg2, "b")
        big = big_text(str(air.get("us")), ascii_only=v.ascii, scale=2 if large and not v.ascii else 1)
        bw = max((len(r) for r in big), default=0)
        dom = air.get("dominant")
        info = [[(lab, acol)], [("Mainly " + POLLUTANT_NAMES.get(dom, dom or "--"), P)], []]
        info += [[(ln, P)] for ln in wrap_plain(advice, max(20, min(56, w - bw - 16)))]
        if air.get("src"):
            info += [[], [(f"Source: {air['src']}", "paneldim")]]
        out = []
        n = max(len(big), len(info))
        off = max(0, (n - len(info)) // 2)
        margin = 4 if not large else max(4, (w - (bw + 8 + 56)) // 2)
        for i in range(n):
            ln = [(" " * margin, P), ((big[i] if i < len(big) else "").ljust(bw + 8), acol)]
            j = i - off
            if 0 <= j < len(info):
                ln += info[j]
            out.append(ln)
        uv_all = (m.get("air_om") or {}).get("uv") or []
        uv = [(t, u) for t, u in uv_all if F.local(t).date() == F.today()]
        uv_title = "UV index today"
        if not uv or max(u for _t, u in uv) < 1:
            tmr = F.today() + dt.timedelta(days=1)
            nxt = [(t, u) for t, u in uv_all if F.local(t).date() == tmr]
            if nxt:
                uv, uv_title = nxt, "UV index tomorrow"
        if not large:
            if uv and max(u for _t, u in uv) >= 1:
                t, u = max(uv, key=lambda x: x[1])
                out += [[], [(" " * margin + uv_title + " peak ", "panellabel"), (f"{u:.0f} {uv_cat(u)[0]} around {F.hour(t)}", P)]]
            return out
        prow = [r for r in air.get("rows") or [] if r.get("aqi") is not None or r.get("conv") is not None]
        if prow and len(out) + len(prow) + 3 <= h:
            tm = " " * max(4, (w - 78) // 2)
            out += [[], [(tm + "Pollutant".ljust(28) + "Level".rjust(14) + "     AQI", "paneltitle")]]
            for r in prow:
                a = r.get("aqi")
                l2, s2, _ = aqi_cat(a) if a is not None else ("", "paneldim", "")
                c2 = v.theme.spec(s2)[0]
                conv = r.get("conv")
                level = f"{conv:.1f} {r.get('unit') or ''}" if conv is not None else "--"
                nbar = 0 if a is None else max(1, int(round(min(a, 300) / 300 * 20)))
                out.append([(tm + f"{r['pol']}  {(r.get('name') or '').lower()}"[:28].ljust(28), P), (level.rjust(14), P),
                            (f"{a if a is not None else '--':>8}", ("rgb", c2, bg2, "b")), ("  ", P),
                            (("#" if v.ascii else "▬") * nbar, ("rgb", c2, bg2, "")), (("  " + l2) if a is not None else "", ("rgb", c2, bg2, ""))])
        charts_w = min(72, w - 24)
        fc = [(t, a) for t, a in air.get("forecast") or [] if t >= time.time() - 1800][:24]
        if len(fc) >= 6 and len(out) + 7 <= h:
            peak = max(fc, key=lambda x: x[1])
            out += [[], center_line([("Air quality, next 24 hours", "paneltitle"),
                                     (f"   peak {peak[1]} {aqi_cat(peak[1])[0]} around {F.hour(peak[0])}", P)], w, P)]
            out += _color_bars(v, fc, charts_w, 3, max(100, max(a for _t, a in fc)), lambda a: aqi_cat(a)[1], w)
        if len(uv) >= 4 and len(out) + 7 <= h and max(u for _t, u in uv) >= 1:
            peak = max(uv, key=lambda x: x[1])
            day = [(t, u) for t, u in uv if 6 <= F.local(t).hour <= 20]
            out += [[], center_line([(uv_title, "paneltitle"),
                                     (f"   peak {peak[1]:.0f} {uv_cat(peak[1])[0]} around {F.hour(peak[0])}", P)], w, P)]
            out += _color_bars(v, day or uv, charts_w, 3, max(8.0, peak[1]), lambda u: uv_cat(u)[1], w)
        return out


# -- helpers ------------------------------------------------------------------------------

def _tc(v, temp):
    return ("rgb", v.theme.spec(temp_style(temp))[0], v.theme.rgb("bg2"), "b")


def _obs_rows(m):
    return [s for s in (m.get("stations") or {}).get("list") or [] if s.get("has_met") and not s.get("stale")]


def _periods(m, n):
    """The next NWS forecast periods as (name, text, icon kind, is_day)."""
    out = []
    for d in m.get("daily") or []:
        for key in ("nws_day", "nws_night"):
            p = d.get(key)
            if p and p.get("text"):
                kind = p.get("kind") or icons.from_text(p.get("short") or p["text"])
                out.append((p["start"], p["name"], p["text"], kind, p.get("is_day", key == "nws_day")))
    out.sort(key=lambda x: x[0])
    now = time.time()
    return [(name, text, kind, day) for start, name, text, kind, day in out if start >= now - 12 * 3600][:n]


def _range_bars(v, rows, w, dbl=False):
    """Apple-style low/high bars on a common scale, colored by temperature."""
    F = v.fmt
    bg2 = v.theme.rgb("bg2")
    dim = v.theme.spec("paneldim")[0]
    glo = min(r["lo"] for r in rows)
    ghi = max(r["hi"] for r in rows)
    span = max(0.5, ghi - glo)
    bw = max(10, min(64, w - 40))
    tot = 9 + 6 + bw + 6 + 12
    margin = " " * max(0, (w - tot) // 2)
    bar, track = ("=", ".") if v.ascii else ("━", "·")
    out = []
    for r in rows:
        lab = "Today" if r["date"] == F.today() else f"{r['date']:%a} {r['date'].day}"
        a = int(round((r["lo"] - glo) / span * (bw - 1)))
        b = max(a, int(round((r["hi"] - glo) / span * (bw - 1))))
        segs = [(margin + lab.ljust(9), "panellabel"), (F.t(r["lo"]).rjust(5) + " ", _tc(v, r["lo"]))]
        for x in range(bw):
            if a <= x <= b:
                segs.append((bar, ("rgb", charts._temp_rgb(glo + x / max(1, bw - 1) * span, v.theme), bg2, "b")))
            else:
                segs.append((track, ("rgb", dim, bg2, "")))
        pop = r.get("pop")
        segs += [(" " + F.t(r["hi"]).ljust(5), _tc(v, r["hi"])),
                 ((f"{pop:.0f}% precip" if pop and pop >= 10 else "").rjust(12), pop_style(pop))]
        out.append(segs)
        if dbl:
            out.append([])
    return out


def _color_bars(v, series, width, height, hi, style_of, w):
    """Vertical bars (one per value), each colored by its category, with an hour axis."""
    F = v.fmt
    bg2 = v.theme.rgb("bg2")
    n = len(series)
    colw = max(1, width // n)
    width = colw * n
    parts = " .:-=+*#" if v.ascii else " ▁▂▃▄▅▆▇"
    full = "#" if v.ascii else "█"
    margin = " " * max(0, (w - width - 6) // 2)
    out = []
    for row in range(height):
        base = (height - 1 - row) * 8
        segs = [(margin + ("      " if row else f"{hi:>4.0f}  "), "paneldim")]
        for _t, val in series:
            e = int(round(min(max(val, 0), hi) / hi * height * 8)) if hi else 0
            if val and val > 0:
                e = max(1, e)
            k = e - base
            ch = full if k >= 8 else parts[k] if k > 0 else " "
            cell = ch * colw if colw == 1 else (ch * (colw - 1) + " ")
            segs.append((cell, ("rgb", v.theme.spec(style_of(val))[0], bg2, "")))
        out.append(segs)
    axis = [" "] * width
    last = -9
    for i, (t, _v) in enumerate(series):
        hr = F.local(t).hour
        if hr % 3 == 0 and i * colw - last >= 5:
            lab = F.hour_short(t)
            for k, ch in enumerate(lab):
                if i * colw + k < width:
                    axis[i * colw + k] = ch
            last = i * colw
    out.append([(margin + "      " + "".join(axis), "paneldim")])
    return out


def _wind(F, deg, ms):
    if ms is None:
        return "--"
    if ms < 0.5:
        return "Calm"
    return f"{compass(deg)} {F.wind(ms)}"


def _ceiling(obs, F):
    for amount, base in obs.get("clouds") or []:
        if amount in ("BKN", "OVC", "VV") and base is not None:
            return F.height(base)
    return "Unlimited" if obs else "--"


def _trend(obs):
    tr = obs.get("trend")
    if tr is None:
        return ""
    return " ↑" if tr > 0.6 else " ↓" if tr < -0.6 else " →"


def _on_panel(line, th):
    """Redraw named styles over the panel background."""
    bg2 = th.rgb("bg2")
    out = []
    for text, st in line:
        if isinstance(st, str) and not st.startswith("panel") and st not in ("alert", "watch", "advis"):
            fg, _bg, attrs = th.spec(st)[:3]
            st = ("rgb", fg, bg2, attrs)
        elif isinstance(st, tuple) and st and st[0] == "rgb" and st[2] is None:
            st = ("rgb", st[1], bg2, st[3] if len(st) > 3 else "")
        out.append((text, st))
    return out
