"""The WeatherSTAR-style "lower display line" along the bottom of the screen."""

import time

from . import ui
from .radar import summary_lines
from .theme import ink_on
from .wxmath import aqi_cat, compass, moon_info


def build_items(m, F, radar_res=None):
    items = []
    loc = m.get("loc") or {}
    now = m.get("now") or {}
    temp = now.get("temp")
    desc = now.get("desc") or ""
    name = loc.get("name") or "your location"
    for a in (m.get("alerts") or [])[:3]:
        until = f" until {F.dtime(a['end'])}" if a.get("end") else ""
        items.append(("ALERT", f"{a['event']}{until}", "ldlalert"))
    if temp is not None:
        items.append((f"Conditions at {name}", f"{desc.capitalize() or 'Reported'}   {F.t(temp, True)}", "ldl"))
    feels, rh, dew = now.get("feels"), now.get("rh"), now.get("dew")
    bits = []
    if feels is not None:
        bits.append(f"Feels like {F.t(feels)}")
    if rh is not None:
        bits.append(f"Humidity {rh:.0f}%")
    if dew is not None:
        bits.append(f"Dew point {F.t(dew)}")
    if bits:
        items.append(("Comfort", "   ".join(bits), "ldl"))
    wind = now.get("wind")
    if wind is not None:
        deg, gust = now.get("wind_deg"), now.get("gust")
        w = "Calm" if wind < 0.5 else f"{compass(deg)} {F.wind(wind)}"
        if gust and gust > wind + 1:
            w += f"   Gusts {F.wind(gust)}"
        press = now.get("press")
        if press is not None:
            tr = (m.get("obs") or {}).get("trend")
            word = "" if tr is None else " rising" if tr > 0.6 else " falling" if tr < -0.6 else " steady"
            w += f"   Pressure {F.press(press)}{word}"
        items.append(("Wind", w, "ldl"))
    for pname, text in ui.periods_text(m, 2):
        short = text.split(". ")[0].rstrip(".") + "."
        items.append((pname, short, "ldl"))
    if radar_res is not None:
        lines = summary_lines(radar_res, F, compact=True)
        if lines:
            items.append(("Radar", "  ".join(t for t, _s in lines[:2]), "ldl"))
    air = m.get("air") or {}
    if air.get("us") is not None:
        items.append(("Air quality", f"{air['us']} {aqi_cat(air['us'])[0]}" +
                      (f"   mainly {air['dominant']}" if air.get("dominant") else ""), "ldl"))
    a = m.get("almanac") or {}
    if a.get("normal_hi") is not None:
        s = f"Normal {F.t(a['normal_hi'])}/{F.t(a['normal_lo'])}"
        if a.get("rec_hi") is not None:
            s += f"   Record high {F.t(a['rec_hi'])} ({a.get('rec_hi_year')})   Record low {F.t(a.get('rec_lo'))} ({a.get('rec_lo_year')})"
        items.append(("Almanac", s, "ldl"))
    sr, ss, _ = ui.sun_pair(m, F)
    if sr and ss:
        age, illum, mname = moon_info(time.time())
        items.append(("Sun & moon", f"Sunrise {F.time(sr)}   Sunset {F.time(ss)}   {mname} {illum * 100:.0f}%", "ldl"))
    st = (m.get("stations") or {}).get("summary") or {}
    if st.get("n"):
        items.append(("Area stations", f"{st['n']} reporting   {F.t(st['min'])} to {F.t(st['max'])}"
                      + (f"   warmest {st['warm']['name']}" if st.get("warm") else ""), "ldl"))
    for o in (m.get("outlooks") or [])[:2]:
        if o["category"] == "CATEGORICAL" and (o.get("level") or 0) >= 1:
            items.append(("Outlook", f"{o['word']} risk of {o['kind'].lower()} on day {o['day']}", "ldl"))
    return items


class Ticker:
    def __init__(self):
        self.items = []
        self.t0 = time.time()

    def update(self, items):
        if [i[:2] for i in items] != [i[:2] for i in self.items]:
            self.items = items

    def page(self, now):
        """Index of the item to show and seconds into it (paged mode)."""
        if not self.items:
            return -1, 0
        durs = [4.0 + max(0, len(i[0]) + len(i[1]) - 50) / 12.0 for i in self.items]
        total = sum(durs)
        t = (now - self.t0) % total
        for k, d in enumerate(durs):
            if t < d:
                return k, t
            t -= d
        return len(self.items) - 1, 0

    def render(self, W, mode="paged", now=None):
        now = now or time.time()
        if not self.items or mode == "off":
            return [(" " * W, "ldl")]
        if mode == "crawl":
            parts = []
            for label, text, st in self.items:
                parts.append((f"{label}: ", "ldllabel" if st == "ldl" else st))
                parts.append((f"{text}", "ldl" if st == "ldl" else st))
                parts.append(("   •   ", "ldldim"))
            flat = []
            for text, st in parts:
                flat += [(ch, st) for ch in text]
            n = len(flat)
            off = int((now - self.t0) * 10) % n
            seq = (flat[off:] + flat[:off]) * (W // n + 2)
            seq = seq[:W]
            out = []
            for ch, st in seq:
                if out and out[-1][1] == st:
                    out[-1] = (out[-1][0] + ch, st)
                else:
                    out.append((ch, st))
            return out
        k, t = self.page(now)
        label, text, st = self.items[k]
        lab = f" {label} "
        room = W - len(lab) - 2
        body = text
        if len(body) > room > 10:
            shift = int(max(0.0, t - 1.5) * 12)
            span = len(body) - room
            body = body[min(shift, span):][:room]
        seg_style = "ldl" if st == "ldl" else st
        lab_style = "ldllabel" if st == "ldl" else st
        out = [(lab, lab_style), ("  ", seg_style), (body, seg_style)]
        used = len(lab) + 2 + len(body)
        dots = f" {k + 1}/{len(self.items)} "
        if W - used > len(dots) + 2:
            out.append((" " * (W - used - len(dots)), seg_style))
            out.append((dots, "ldldim" if st == "ldl" else st))
        else:
            out.append((" " * max(0, W - used), seg_style))
        return out


# how fast a warning's counties go by in the crawl, cells a second
SPEEDS = {"slow": 3.0, "normal": 5.0, "fast": 8.0}
PAUSE = 2.0          # seconds the counties hold still before they start to move
STILL = 8.0          # with several alerts taking turns: seconds one that fits stays up
TURN = 20.0          # ... and one whose counties go by (they carry on from there next turn)
MIN_WINDOW = 24      # narrower than this, the times go by with the counties
GAP, GAP_ASCII = "   ◆   ", "   *   "


def _merge(cells):
    out = []
    for ch, st in cells:
        if out and out[-1][1] == st:
            out[-1] = (out[-1][0] + ch, st)
        else:
            out.append((ch, st))
    return out


class AlertCrawl:
    """The warnings crawl along the bottom: every watch, warning and storm report in the
    chosen area, colour-coded. When they all fit they sit side by side; when they don't,
    they take turns (the newest arrival first), each alert's name and times staying put
    while its counties go slowly by to the left. An alert that covers your county carries
    the county's name, lit up, right after its own, for as long as it's in effect."""

    def __init__(self):
        self.items = []
        self.t0 = time.time()
        self.keys = set()
        self.moving = False      # the line last drawn has counties going by
        self.step = 1 / SPEEDS["normal"]     # ... a cell every this many seconds
        self.county = ""         # your county's name ("Dallas County"), pinned to the alerts that cover it
        self.county_zone = ""    # ... its zone's own name ("Dallas"), lit up among the counties going by

    def mine(self, item):
        """Does this alert cover your county?"""
        return len(item) > 6 and bool(item[6])

    def chip(self, item):
        """Your county's name as it sits after the alert's name, lit up (the alert's color
        on white), while the alert covers it: [] otherwise."""
        if not self.mine(item):
            return []
        _lab, body = self._styles(item[2])
        return [(" ", body)] + [(ch, self._lit(item[2])) for ch in f" {self.county or 'Your county'} "]

    def _lit(self, rgb):
        """White, with the alert's color for the letters, darkened as far as it takes to read."""
        lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
        return ("rgb", self._shade(rgb, min(1.0, 85.0 / max(1.0, lum))), (255, 255, 255), "b")

    def _area_cells(self, item, text, style):
        """The text with your county lit up wherever it's among the counties going by."""
        cells = [(ch, style) for ch in text]
        name = self.county_zone.strip().lower()
        if not name or not self.mine(item):
            return cells
        low = text.lower()
        k = low.find(name)
        while k >= 0:
            end = k + len(name)
            before = low[k - 1] if k else ";"
            after = low[end] if end < len(low) else ";"
            if before in "; ·" and after in "; ·":       # the whole name, not a part of another
                for j in range(k, end):
                    cells[j] = (cells[j][0], self._lit(item[2]))
            k = low.find(name, end)
        return cells

    def update(self, items, now=None):
        now = now or time.time()
        keys = {i[4] for i in items}
        arrived = [i for i in items if i[4] not in self.keys and i[3]]
        if arrived and self.keys is not None:
            # put what just arrived first and start the crawl over so it's seen right away
            first = arrived[0][4]
            items = sorted(items, key=lambda i: 0 if i[4] == first else 1)
            self.t0 = now
        self.items = items
        self.keys = keys

    @staticmethod
    def _shade(rgb, f):
        return tuple(int(c * f) for c in rgb)

    def _styles(self, rgb):
        return ("rgb", ink_on(rgb), rgb, "b"), ("rgb", (255, 255, 255), self._shade(rgb, 0.32), "")

    @staticmethod
    def _tag(new, blink):
        if not new:
            return []
        return [(ch, ("rgb", (20, 10, 0), (255, 214, 0) if blink else (255, 150, 0), "b")) for ch in " NEW "]

    def _layout(self, item, W, count):
        """(fixed text, moving text, window width) for one alert in the banner: its times
        and distance stay put when the counties still get room enough to read."""
        label, text, _rgb, new, _key = item[:5]
        area = item[5] if len(item) > 5 else ""
        head = (5 if new else 0) + len(label) + 2 + len(self.chip(item))
        win = W - head - len(count)
        fixed = f" {text} ·" if text and area else f" {text} " if text else ""
        moving = area
        if area and win - len(fixed) - 1 < MIN_WINDOW:
            fixed, moving = "", " · ".join(x for x in (text, area) if x)
        return fixed, moving, max(0, win - len(fixed) - 1)

    def _turn(self, item, W, n):
        _fixed, moving, room = self._layout(item, W, f" {n}/{n} ")
        return TURN if len(moving) > room else STILL

    def render(self, W, now, calm, speed="normal", ascii_only=False):
        self.moving = False
        if not self.items:
            return calm
        return self._line(W, now, speed, ascii_only)

    def _line(self, W, now, speed, ascii_only):
        blink = int(now * 2) % 2 == 0
        cps = SPEEDS.get(speed, SPEEDS["normal"])
        self.step = 1 / cps
        sep = GAP_ASCII if ascii_only else GAP
        # all of them in full on one line: nothing needs to move
        flat = []
        for item in self.items:
            label, text, rgb = item[:3]
            area = item[5] if len(item) > 5 else ""
            lab_style, body_style = self._styles(rgb)
            words = " · ".join(x for x in (text, area) if x)
            flat += self._tag(item[3], blink) + [(ch, lab_style) for ch in f" {label} "] + self.chip(item)
            flat += self._area_cells(item, f" {words} ", body_style) + [(ch, "status") for ch in sep]
        flat = flat[:-len(sep)]
        if len(flat) <= W:
            return _merge(flat + [(" ", "status")] * (W - len(flat)))
        # one at a time: how long the one up now has been up, all its turns together
        n = len(self.items)
        el = max(0.0, now - self.t0)
        if n == 1:
            k, shown = 0, el
        else:
            durs = [self._turn(item, W, n) for item in self.items]
            cycle, t = divmod(el, sum(durs))
            k = 0
            while k < n - 1 and t >= durs[k]:
                t -= durs[k]
                k += 1
            shown = cycle * durs[k] + t
        return self._banner(self.items[k], k, n, W, shown, cps, blink, sep)

    def _banner(self, item, k, n, W, shown, cps, blink, sep):
        """One alert across the line: NEW, its name, your county (when it covers it), its
        times and distance, then its counties, going by to the left when they don't fit;
        k/n at the end."""
        label, _text, rgb, new = item[:4]
        lab_style, body_style = self._styles(rgb)
        count = f" {k + 1}/{n} " if n > 1 else ""
        fixed, moving, room = self._layout(item, W, count)
        cells = self._tag(new, blink) + [(ch, lab_style) for ch in f" {label} "] + self.chip(item)
        cells += [(ch, body_style) for ch in fixed + " "]
        if len(moving) <= room:
            window = self._area_cells(item, moving.ljust(room), body_style)
        else:
            loop = self._area_cells(item, moving + sep, body_style)
            off = int(max(0.0, shown - PAUSE) * cps) % len(loop)
            window = (loop[off:] + loop)[:room]
            self.moving = True
        cells += window
        cells = cells[:W - len(count)]
        cells += [(" ", body_style)] * (W - len(count) - len(cells))
        cells += [(ch, "status") for ch in count]
        return _merge(cells)
