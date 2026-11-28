"""The full-screen app: main loop, layout, keys and mouse."""

import os
import sys
import re
import time

from . import APP, VERSION, gfx, screens, screens2
from . import crt as C
from .canvas import Canvas, ImageRegistry, Renderer
from .config import intval, on
from .fmt import Fmt
from .local8s import Local8s
from .model import build_model, empty_model
from .radar import ZOOMS, RadarManager
from .scripts import SCRIPTS
from .settings import Settings
from .speech import Speaker
from .term import (CTRL_C, CTRL_L, CTRL_Z, KEY_BTAB, KEY_DOWN, KEY_END, KEY_ENTER, KEY_ESC, KEY_F, KEY_HOME,
                   KEY_LEFT, KEY_NPAGE, KEY_PPAGE, KEY_RESIZE, KEY_RIGHT, KEY_SLEFT, KEY_SRIGHT, KEY_UP, Mouse,
                   Paste)
from .termfont import KITTY
from .text import card, corners, padto, seglen, set_rounded
from .ui import dedent
from .theme import PALETTES, Theme, blend, temp_style
from . import sounds, watch
from .alerts import AlertWatcher, alarm_events, ticker_items, where_text
from .ticker import AlertCrawl, Ticker, build_items
from .errlog import LOG
from .util import cap, log, num

SCREENS = [
    ("now", "Now", "Now", screens.render_now),
    ("hourly", "Hourly", "Hourly", screens.render_hourly),
    ("daily", "10-Day", "Daily", screens.render_daily),
    ("radar", "Radar", "Radar", screens2.render_radar),
    ("stations", "Stations", "Stns", screens2.render_stations),
    ("alerts", "Alerts", "Alerts", screens.render_alerts),
    ("air", "Air & UV", "Air", screens.render_air),
    ("sky", "Sky & Almanac", "Sky", screens.render_sky),
    ("office", "NWS & Radio", "NWS", screens.render_office),
    ("settings", "Settings", "Set", None),
]
SETTINGS_IDX = len(SCREENS) - 1
SCREEN_IDX = {s[0]: i for i, s in enumerate(SCREENS)}

HELP = [
    ("1-9, 0", "jump to a screen (0 is Settings)"),
    ("Tab, ← →", "next or previous screen (arrows pan on Radar)"),
    ("↑ ↓, j k", "move the selection or scroll"),
    ("PgUp PgDn, g G", "page, top, bottom"),
    ("J K", "scroll the detail pane or sidebar"),
    ("s", "read this screen aloud (again to stop)"),
    ("[ ]", "previous / next sentence while reading"),
    ("L", "Local on the 8s presentation mode"),
    ("z", "weather for another ZIP code or place"),
    ("r", "refresh now"),
    ("u", "switch °F and °C"),
    ("Radar", "p P product, e E tilt, n N radar, m site/mosaic, v panes"),
    ("", "+ - zoom, arrows pan, c center, space , . < > loop"),
    ("", "x inspect, a tracks, o smooth, l t w labels/temps/warnings"),
    ("", "d range, D sites, M markers, F placefiles, y mosaic layer, b map background"),
    ("", "i the radar guide"),
    ("Alarm", "any key acknowledges a tornado alarm"),
    ("Stations", "f filter, o sort, + - radius, Enter use station"),
    ("Settings", "↑↓ move, PgUp PgDn sections, Enter opens a section or edits, ← → change, / find"),
    ("", "Esc closes a section, + - open or close them all, S save, T test, D discard"),
    ("", "R restore sources, A test alarm, x delete source, e edit its address, v show keys"),
    ("CRT", "c on Settings opens the CRT effects card: j k pick, h l or 0-9 level, Enter off/back,"),
    ("", "T try it, R this one to 5, A all to 5, U undo all, Esc closes"),
    ("Ctrl-L", "redraw the screen"),
    ("q", "quit"),
]
# skydrop's own window (skydrop.exe, --window): its keys, shown after the keys of every
# screen, where a small window still has room for them
WINDOW_HELP = [
    ("Window", "F11 or Alt-Enter full screen, Ctrl + Ctrl - font size, Ctrl 0 the size in Settings"),
    ("", "drag selects text (Shift-drag while the mouse is on), Ctrl-Shift-C or a right-click copies, "
         "Ctrl-V pastes"),
]


# radar state that changes by itself (animation, mouse) and doesn't by itself change a screen
RADAR_VOLATILE = {"tick", "hold", "drag", "nframes", "hover"}


def screen_key(i):
    return "0" if i == 9 else str(i + 1)


class App:
    def __init__(self, term, cfg, store, screen=0, units=None):
        self.term = term
        self.cfg = cfg
        self.store = store
        self.idx = screen
        self.prev_idx = 0
        self.units_override = units
        self.scroll = [0] * len(SCREENS)
        self.fscroll = [0] * len(SCREENS)
        self.sb_drag = None        # dragging a scroll bar's thumb: the row of it that was taken hold of
        self.sel = {"hourly": 0, "daily": 0, "stations": 0, "st_filter": "weather", "st_sort": "distance"}
        self.help = False
        self.flash_msg, self.flash_until = "", 0.0
        self.flash_style = "stkey"
        self.ring_off = (0, 0)          # the CRT bezel's ring of cells around the app
        self.pending_refresh = False
        self.model, self.model_version = None, -1
        self.fmt = Fmt(self.units(), None)
        self.images = ImageRegistry()
        self.theme = Theme(cfg.get("theme"), cfg.get("background"))
        self.gmode, self.color_mode = "blocks", "256"
        self.renderer = Renderer(term, self.theme, "256", "blocks")
        self.radar = RadarManager(store) if gfx.HAVE_PIL else None
        self.radar_state = {"span": int(num(cfg.get("radar_span")) or 120), "lat": None, "lon": None, "frame": None,
                            "playing": True, "layer": cfg.get("radar_layer") or "reflectivity",
                            "labels": on(cfg, "radar_labels"), "temps": on(cfg, "radar_stations"),
                            "warnings": on(cfg, "radar_warnings"), "tick": 0.0, "nframes": 0, "hold": 0,
                            "source": cfg.get("radar_source") or "site", "site": None,
                            "product": cfg.get("radar_product") or "ref", "tilt": 0,
                            "panes": intval(cfg, "radar_panes", 1, 4), "smooth": on(cfg, "radar_smooth"),
                            "cells": on(cfg, "radar_cells"), "inspect": None, "inspect_on": False, "hover": None,
                            "drag": None, "range": False, "sites": False, "markers": True, "placefiles": True}
        self.radar_from_cfg()
        self.guide = None      # radar guide card: scroll offset while open
        if self.radar_state["panes"] not in (1, 2, 4):
            self.radar_state["panes"] = 1
        self.speech = Speaker(cfg)
        self.ticker = Ticker()
        self.settings = Settings(self)
        self.local8s = None
        self.watcher = AlertWatcher()
        self.crawl = AlertCrawl()
        self.alarm = None          # the alarm on screen: {kind, alert, at, ack, phase, ...}
        self.alarm_shown = set()   # alerts whose banner has been shown this session
        self.warn_sig = None       # the warning polygons the radar last drew
        self.last_radar = None
        self.body_h = 10
        self.last_cursor = {}
        self.more = None
        self.layout = {}
        self.cache = {}
        self.last_stations = 0.0
        self.last_ticker_items = 0.0
        self.printing = False
        self.speech_highlight = None
        self.apply_display()

    # -- properties used by screens ---------------------------------------------
    @property
    def ascii(self):
        return self.gmode == "ascii"

    @property
    def cell(self):
        return self.term.cell_px()

    def units(self):
        return self.units_override or self.cfg.get("units") or "imperial"

    def apply_crt(self):
        """CRT settings changed (they apply as you change them)."""
        self.renderer.crt.configure(self.cfg, self.theme, self.gmode, self.color_mode)
        self.cache.clear()

    def post_check(self, index):
        """The POST screen's check `index` (of the CRT's POST_LINES): (what, the finding, its
        verdict - "OK", "NONE" or "WARN" - and, for a figure that counts up while it is
        checked, that figure; the finding has {n} where it goes)."""
        import platform
        from . import sources as S
        from .paths import CACHE_DIR, CONFIG_PATH
        crt = self.renderer.crt
        kind = C.terminal_kind()
        if index == 0:
            return ("CPU", f"{platform.machine() or 'processor'} · {os.cpu_count() or 1} cores · "
                           f"Python {platform.python_version()}", "OK", None)
        if index == 1:
            try:
                import resource
                used = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                kilobytes = used // 1024 if sys.platform == "darwin" else used
            except (ImportError, AttributeError, OSError, ValueError):
                return ("Memory", "not measured here", "OK", None)
            return ("Memory", "{n} K in use", "OK", int(kilobytes))
        if index == 2:
            W, H = self.term.size()[:2]
            colors = {"truecolor": "24-bit color", "256": "256 colors", "16": "16 colors"}.get(
                self.color_mode, f"{self.color_mode} colors")
            where = self.term.caps.name if getattr(self.term, "is_window", False) else os.environ.get("TERM", "?")
            return ("Display", f"{W} × {H} cells · {where} · {colors}", "OK", None)
        if index == 3:
            if crt.pictures:
                name = C.TERMINAL.get("name") or kind or "this terminal"
                W, H = self.term.size()[:2]
                framed = " · a bezel" if crt.ring(W, H) else ""
                return ("Glass", f"pictures in {name}{framed}", "OK", None)
            return ("Glass", "none: this terminal shows no pictures", "NONE", None)
        if index == 4:
            return ("Phosphor", C.PHOSPHOR_LABEL[crt.phosphor], "OK", None)
        if index == 5:
            current, path = C.terminal_font(kind)
            pick = C.font_of(self.cfg)
            if current:
                return ("Font", f"{current} (from {os.path.basename(path)})", "OK", None)
            if pick == "own":
                return ("Font", "the terminal's own", "OK", None)
            word = C.font_word(pick, kind)
            return ("Font", f"{C.font_label(pick)} · {word}", "WARN" if word == "not installed" else "OK", None)
        if index == 6:
            there = os.path.isfile(CONFIG_PATH)
            return ("Config", C.home_path(CONFIG_PATH) + ("" if there else " · not written yet"), "OK", None)
        if index == 7:
            total, n = 0, 0
            for dp, _dn, fn in os.walk(CACHE_DIR):
                for f in fn:
                    n += 1
                    try:
                        total += os.path.getsize(os.path.join(dp, f))
                    except OSError:
                        pass
                if n > 4000:
                    break
            if not n:
                return ("Cache", "empty", "OK", None)
            size = f"{total / 1048576:.1f} MB" if total >= 1048576 else f"{max(1, total // 1024)} KB"
            return ("Cache", f"{n} entries · {size}", "OK", None)
        if index == 8:
            vis = [src for src in S.SOURCES if not S.removed(self.cfg, src.id)]
            n_on = sum(1 for src in vis if S.enabled(self.cfg, src.id))
            return ("Sources", f"{n_on} of {len(vis)} data sources on · {SCREENS[self.idx][1]} first", "OK", None)
        failed = len(LOG) > 0
        busy = 1 if self.store.busy else 0
        return ("Network", f"{self.store.http.requests} requests · {busy} in flight" +
                (" · one failed" if failed else ""), "WARN" if failed else "OK", None)

    def meter_look(self):
        """How every meter is drawn (Settings, Display, Meters)."""
        return C.meter_style(self.cfg)

    def apply_display(self):
        caps = self.term.caps
        g = (self.cfg.get("graphics") or "auto").lower()
        if g == "auto":
            g = caps.graphics_auto()
        if g in ("kitty", "sixel") and not gfx.HAVE_PIL:
            g = "blocks"
        c = (self.cfg.get("color_mode") or "auto").lower()
        if c == "auto":
            c = caps.color_auto()
        self.gmode, self.color_mode = g, c
        self.theme = Theme(self.cfg.get("theme"), self.cfg.get("background"), getattr(caps, "bg_rgb", None))
        set_rounded(on(self.cfg, "rounded_borders"))
        self.images.mode = g
        self.images.forget()
        self.renderer.crt.checks = self.post_check
        who = f"{caps.name} {getattr(caps, 'xtversion', '')}".lower()
        C.TERMINAL["name"] = who.strip()
        # the CRT glass: kitty, Ghostty and skydrop's own window
        self.renderer.crt.glass_ok = "kitty" in who or "ghostty" in who or "skydrop window" in who
        self.renderer.configure(self.theme, c, g, cfg=self.cfg)
        self.term.set_mouse(on(self.cfg, "mouse"))
        self.cache.clear()

    # -- loop -------------------------------------------------------------------
    def run(self):
        self.term.install_signals()
        self.store.refresh()
        if on(self.cfg, "background_watch") and self.store.persist_enabled:
            if getattr(sys, "frozen", False):
                watch.set_autostart(True)  # skydrop.exe has no installer: the login item follows it about
            watch.start_watcher()          # in case it isn't running (it was stopped, or never started)
        try:
            self._loop()
        finally:
            watch.ui_gone()                # the background watch takes over again

    def _loop(self):
        drawn, last, beat = None, 0.0, 0.0
        while True:
            now = time.time()
            if now - beat > 30:
                watch.ui_heartbeat()       # 'a window is open': the background watch stands back
                beat = now
            if getattr(self.term, "resumed", False):  # back from Ctrl-Z / kill -TSTP
                self.term.resumed = False
                self.renderer.reset_graphics()
                self.cache.clear()
            if not self.store.busy and (self.pending_refresh or now >= self.next_refresh_at()):
                self.store.refresh(force=self.pending_refresh)
                self.pending_refresh = False
            on_stations = SCREENS[self.idx][0] == "stations"
            self.store.stations_visible = on_stations
            if on_stations and now - self.last_stations > 180 and not self.store.busy:
                if self.store.refresh_stations():
                    self.last_stations = now
            self.ensure_model()
            self.tick_alarm(now)
            anim = self.animate(now)
            if self.local8s is not None and self.model is not None:
                anim = self.local8s.tick(self.model) or anim
            state = (self.store.version, self.settings.version, self.speech.version, self.idx,
                     (self.radar.version if self.radar else 0))
            ticking = (self.cfg.get("ticker") or "paged") != "off"
            period = 0.25 if ticking or self.speech.active else 1.0
            if self.crawl.moving:
                period = min(period, self.crawl.step)      # a warning's counties go by a cell at a time
            crt = self.renderer.crt
            # While the CRT glass moves (grain, flicker, hum bar: a step every 0.1 s), the screen's
            # own timed redraws (the clock, the tickers) ride the same beat, so the text and the glass
            # change in one write and the terminal redraws once for both.
            riding = crt.on and crt.glass.moving()
            beat_due = crt.on and crt.wake_at is not None and now >= crt.wake_at
            due = now - last >= (period - 0.051 if riding else period)
            if state != drawn or anim or (due and (beat_due or not riding)):
                # a frame fresh data drew (not a key): its changed numbers flash
                crt.fresh = drawn is not None and state[0] != drawn[0]
                self.draw()
                drawn, last = state, now
            elif beat_due:
                self.renderer.repaint()           # the CRT's effects move on; the screen itself didn't change
            fast = anim or self.radar_state.get("playing") and SCREENS[self.idx][0] == "radar"
            on_radar = SCREENS[self.idx][0] == "radar" and self.local8s is None
            self.term.set_motion((on_radar and on(self.cfg, "radar_hover")) or self.sb_drag is not None)
            wait = 0.08 if fast else 0.2
            if self.crawl.moving:
                wait = max(0.004, min(wait, last + period - time.time()))
            nxt = self.next_frame_at()
            if nxt is not None:
                wait = max(0.004, min(wait, nxt - time.time()))           # the radar loop's next frame, on time
            if crt.on and crt.wake_at is not None:
                wait = max(0.004, min(wait, crt.wake_at - time.time()))   # the CRT may only shorten the wait
            held = KITTY.pump(time.time())                                  # a kitty reload held back (CRT font size)
            if held is not None:
                wait = max(0.004, min(wait, held))
            events = self.term.read_events(wait)
            for ev in events:
                got = self.on_event(ev)
                if got is False:
                    self.speech.stop()
                    return
                if got is not None:
                    drawn = None

    def next_refresh_at(self):
        return self.store.last_refresh + intval(self.cfg, "refresh_seconds", 15, 3600)

    def request_refresh(self):
        if not self.store.refresh(force=True):
            self.pending_refresh = True

    def ensure_model(self):
        ver = self.store.version
        rebuilt = False
        if self.model is None or ver != self.model_version or self.fmt.imp != (self.units() != "metric"):
            try:
                self.model = build_model(self.store, self.cfg)
                LOG.resolve("model")
            except Exception as e:  # one odd payload mustn't take the whole app down
                import traceback
                log("build_model: " + traceback.format_exc())
                LOG.report("model", "Combining the data", f"{type(e).__name__}: {e}")
                if self.model is None:
                    self.model = empty_model(self.store)
            self.model_version = ver
            self.fmt = Fmt(self.units(), self.model.get("tz"))
            rebuilt = True
            self.update_alerts()
        now = time.time()
        if rebuilt or now - self.last_ticker_items > 20 or not self.ticker.items:
            try:
                self.ticker.update(build_items(self.model, self.fmt, self.last_radar))
            except Exception as e:
                LOG.report("ticker", "Bottom ticker", f"{type(e).__name__}: {e}")
            self.last_ticker_items = now

    def flash(self, msg, secs=3.5):
        self.flash_msg, self.flash_until, self.flash_style = msg, time.time() + secs, "stkey"

    def flash_alert(self, msg):
        """A new warning on the status line. In CRT mode it blinks until a key is pressed."""
        crt = self.renderer.crt
        blink = crt.on and crt.levels.get("blink")
        self.flash_msg, self.flash_style = msg, "stalert"
        self.flash_until = time.time() + (600 if blink else 10)
        if blink:
            crt.start_blink()

    # -- alerts: crawl, banner, alarms ------------------------------------------------
    def update_alerts(self):
        """After new data: refresh the crawl, note new alerts, sound alarms for the ZIP code."""
        m = self.model or {}
        if not self.store.last_done:
            return
        alerts = m.get("area_alerts") or []
        fresh = self.watcher.update(alerts)
        sig = (tuple(sorted(a["key"] for a in alerts if a.get("rings"))),
               tuple(sorted(str(w.get("id")) for w in m.get("sbw") or [])))
        if sig != self.warn_sig:
            if self.warn_sig is not None and self.radar:
                self.radar.invalidate()  # a warning was issued, changed or ended: redraw the polygons now
            self.warn_sig = sig
        try:
            self.crawl.update(ticker_items(alerts, m.get("lsrs"), self.cfg, self.fmt))
            pts = m.get("pts") or {}
            self.crawl.county = pts.get("county_name") or ""
            self.crawl.county_zone = pts.get("county_zone") or ""
            LOG.resolve("ticker")
        except Exception as e:
            LOG.report("ticker", "Warnings crawl", f"{type(e).__name__}: {e}")
        for kind, a, key in alarm_events(alerts, self.cfg):
            first_time = AlertWatcher.claim(key)
            if kind == "tornado":
                if first_time or key not in self.alarm_shown:
                    self.start_alarm(kind, a, key, sound=first_time)
            elif first_time:
                self.start_alarm(kind, a, key, sound=True)
        shown = {a["key"] for _k, a, _x in alarm_events(alerts, self.cfg)}
        if self.alarm and self.alarm["alert"]["key"] not in shown and self.alarm["kind"] == "tornado":
            self.alarm["expired"] = True
        near = [a for a in fresh if not a.get("in_zip") and a["sig"] == "W" and a.get("dist") is not None
                and a["dist"] <= 60]
        if near and not self.alarm:
            a = near[0]
            self.flash_alert(f"NEW: {a['event']} {where_text(a, self.fmt)}")

    def start_alarm(self, kind, a, key, sound=True):
        self.alarm_shown.add(key)
        now = time.time()
        if kind == "tornado" or self.alarm is None or self.alarm.get("ack"):
            self.alarm = {"kind": kind, "alert": a, "key": key, "at": now, "ack": False, "phase": "idle",
                          "next": 0.0, "plays": 0}
        if not sound:
            self.alarm["phase"] = "done"
            return
        self.play_alarm()

    def play_alarm(self):
        """Sound it: SAME bursts and the 1050 Hz tone for a tornado warning, then the voice,
        then the end-of-message bursts; a chime or tone for other warnings."""
        al = self.alarm
        if al is None:
            return
        a = al["alert"]
        now = time.time()
        al["plays"] += 1
        if al["kind"] == "tornado":
            snd = (self.cfg.get("tornado_alarm_sound") or "noaa").lower()
            if snd not in ("noaa", "nwr", "eas"):
                snd = "noaa"
            mins = max(15, int(((a.get("end") or now + 2700) - now) / 60))
            try:
                path, secs = sounds.alarm_file(snd, "TOR", a.get("same") or None, mins,
                                               f"{a.get('office') or 'KWNS'}/NWS", a.get("issued"))
            except OSError as e:
                LOG.report("alarm", "Tornado alarm sound", str(e))
                path, secs = None, 0.0
            self.speech.stop()
            if path and self.speech.play_file(path):
                LOG.resolve("alarm")
            elif path:
                LOG.report("alarm", "Tornado alarm sound", "no audio player found (install alsa-utils or pipewire)")
            al["phase"] = "tone"
            al["voice_at"] = now + secs + 0.3
            al["eom"] = snd == "noaa"
            rep = intval(self.cfg, "tornado_alarm_repeat", 0, 60)
            al["next"] = now + rep * 60 if rep else 0.0
        else:
            snd = (self.cfg.get("warning_alarm") or "chime").lower()
            if snd in ("chime", "nwr"):
                self.speech.tone(snd)
            al["phase"] = "voice" if on(self.cfg, "auto_read_warnings") else "done"
            al["voice_at"] = now + 2.5

    def tick_alarm(self, now):
        al = self.alarm
        if al is None:
            return
        a = al["alert"]
        if al["phase"] == "tone" and now >= al.get("voice_at", 0):
            if on(al.get("cfg") or self.cfg, "tornado_alarm_voice"):
                text = [f"{a['event']} for your area." if not al.get("test") else "", a.get("nws_headline") or "",
                        (a.get("desc") or "").split("\n\n")[0], a.get("instruction") or ""]
                self.speech.speak(a["event"], [t for t in text if t], overrides=al.get("cfg"))
                al["phase"] = "voice"
            else:
                al["phase"] = "eom" if al.get("eom") else "done"
        elif al["phase"] == "voice" and al["kind"] != "tornado" and now >= al.get("voice_at", 0):
            text = [a["event"] + ".", a.get("nws_headline") or a.get("headline") or "", a.get("instruction") or ""]
            self.speech.speak("New warning", [t for t in text if t])
            al["phase"] = "done"
        elif al["phase"] == "voice" and al["kind"] == "tornado" and not self.speech.active and \
                now - al.get("voice_at", 0) > 3:
            al["phase"] = "eom" if al.get("eom") else "done"
        if al["phase"] == "eom":
            try:
                self.speech.play_file(sounds.eom_file())
            except OSError:
                pass
            al["phase"] = "done"
        if al["kind"] == "tornado" and not al["ack"] and al.get("next") and now >= al["next"] \
                and not al.get("expired") and al["phase"] == "done":
            self.play_alarm()
        if al.get("test") and al["phase"] == "done":
            self.alarm = None
        elif al["kind"] != "tornado" and al["phase"] == "done" and now - al["at"] > 12:
            self.alarm = None
        elif al.get("expired") and al["ack"]:
            self.alarm = None

    def acknowledge_alarm(self):
        if self.alarm is None:
            return False
        if self.alarm["kind"] != "tornado":
            self.alarm = None
            return False
        if not self.alarm["ack"]:
            self.alarm["ack"] = True
            self.alarm["next"] = 0.0
            return True
        return False

    def test_alarm(self, cfg=None):
        """Settings button: the tornado alarm as it will sound, with a test header and message,
        run through the same alarm sequence as a real warning (tone, voice, end of message)."""
        cfg = cfg or self.cfg
        snd = (cfg.get("tornado_alarm_sound") or "noaa").lower()
        snd = snd if snd in ("noaa", "nwr", "eas") else "noaa"
        try:
            path, secs = sounds.alarm_file(snd, "RWT", None, 15, "SKYDROP")
        except OSError as e:
            self.flash(f"Couldn't make the alarm sound: {e}")
            return False
        self.speech.stop()
        if not self.speech.play_file(path, cfg):
            return False
        now = time.time()
        test = {"event": "Required Weekly Test", "key": "TEST", "nws_headline": "",
                "desc": "This is a test of the skydrop tornado alarm. If this were a real tornado warning for "
                        "your area, you would hear the warning here.",
                "instruction": "This concludes the test.", "end": now + 900}
        self.alarm = {"kind": "tornado", "alert": test, "key": "TEST", "at": now, "ack": True, "phase": "tone",
                      "next": 0.0, "plays": 1, "voice_at": now + secs + 0.3, "eom": snd == "noaa", "test": True,
                      "cfg": cfg}
        self.flash("Playing the tornado alarm (test)", 8)
        return True

    # -- radar animation ------------------------------------------------------------
    def animate(self, now):
        st = self.radar_state
        if SCREENS[self.idx][0] != "radar" or not st.get("playing") or self.local8s is not None:
            return False
        n = st.get("nframes") or 0
        if n <= 1:
            return False
        speed = max(1, intval(self.cfg, "radar_speed", 1, 10))
        step = 1.0 / speed
        if now - st["tick"] < step:
            return False
        # each frame is due a step after the last one was due, whenever the loop happened to wake:
        # other wakes (a key, the CRT's glass) can't bring frames early or push them late
        st["tick"] = st["tick"] + step if now - st["tick"] < 2 * step else now
        f = st.get("frame")
        if f is None:
            f = n - 1
        if f >= n - 1:
            st["hold"] = st.get("hold", 0) + 1
            if st["hold"] < 3:
                return False
            st["hold"] = 0
            st["frame"] = 0
        else:
            st["frame"] = f + 1
        return True

    def next_frame_at(self):
        """When the radar loop's next frame is due (None: it isn't playing)."""
        st = self.radar_state
        if SCREENS[self.idx][0] != "radar" or not st.get("playing") or self.local8s is not None:
            return None
        if (st.get("nframes") or 0) <= 1:
            return None
        return st.get("tick", 0.0) + 1.0 / max(1, intval(self.cfg, "radar_speed", 1, 10))

    # -- keys -------------------------------------------------------------------------
    def switch(self, i):
        if i != self.idx:
            self.prev_idx = self.idx
        self.idx = i
        if i == SETTINGS_IDX:
            self.settings.on_show()

    def back(self):
        self.switch(self.prev_idx if self.prev_idx != SETTINGS_IDX else 0)

    def on_event(self, k):
        handled = self._on_event(k)
        if SCREENS[self.idx][0] == "radar" or self.idx == SETTINGS_IDX:
            self.persist_radar()
        return handled

    # radar toolbox choices kept in the config file, so the radar comes back as it was left
    RADAR_PERSIST = (("product", "radar_product"), ("source", "radar_source"), ("panes", "radar_panes"),
                     ("smooth", "radar_smooth"), ("cells", "radar_cells"), ("labels", "radar_labels"),
                     ("temps", "radar_stations"), ("warnings", "radar_warnings"), ("layer", "radar_layer"),
                     ("span", "radar_span"), ("site", "radar_site"), ("layers", "radar_extra_layers"))
    EXTRA_LAYERS = ("range", "sites", "markers", "placefiles")

    def radar_cfg_values(self):
        st = self.radar_state
        out = {}
        for sk, ck in self.RADAR_PERSIST:
            if sk == "layers":
                out[ck] = ",".join(k for k in self.EXTRA_LAYERS if st.get(k))
            elif sk == "site":
                out[ck] = st.get("site") or "auto"
            elif isinstance(st.get(sk), bool):
                out[ck] = "on" if st[sk] else "off"
            else:
                out[ck] = st.get(sk)
        return out

    def persist_radar(self):
        """Save toolbox choices that changed (product, source, panes, layers, zoom, site...)."""
        changed = [k for k, v in self.radar_cfg_values().items() if v is not None and self.cfg.get(k) != v]
        if not changed:
            return
        vals = self.radar_cfg_values()
        with self.store.cfg_lock:
            for k in changed:
                self.cfg[k] = vals[k]
        self.store.persist()
        self.settings.adopt(*changed)

    def radar_from_cfg(self):
        """Take the radar settings from the config (at start, and after Settings saves)."""
        from . import nexrad
        cfg, st = self.cfg, self.radar_state
        prod = cfg.get("radar_product") or "ref"
        st["product"] = prod if prod in nexrad.PRODUCT_BY_KEY else "ref"
        st["tilt"] = min(st.get("tilt", 0), len(nexrad.PRODUCT_BY_KEY[st["product"]].tilts) - 1)
        st["source"] = cfg.get("radar_source") if cfg.get("radar_source") in ("site", "mosaic") else "site"
        panes = intval(cfg, "radar_panes", 1, 4)
        st["panes"] = panes if panes in (1, 2, 4) else 1
        for sk, ck in (("smooth", "radar_smooth"), ("cells", "radar_cells"), ("labels", "radar_labels"),
                       ("temps", "radar_stations"), ("warnings", "radar_warnings")):
            st[sk] = on(cfg, ck)
        st["layer"] = cfg.get("radar_layer") or "reflectivity"
        st["span"] = int(num(cfg.get("radar_span")) or 120)
        site = str(cfg.get("radar_site") or "auto").strip().upper()
        st["site"] = site if re.fullmatch(r"[KTPR][A-Z0-9]{3}", site) else None
        layers = cfg.get("radar_extra_layers")
        if layers is not None:
            want = {x.strip() for x in str(layers).split(",")}
            for k in self.EXTRA_LAYERS:
                st[k] = k in want

    def _on_event(self, k):
        if k in (KEY_SLEFT, KEY_SRIGHT) and not (self.idx == SETTINGS_IDX and not self.settings.capturing):
            k = KEY_LEFT if k == KEY_SLEFT else KEY_RIGHT     # only Settings gives Shift-arrows a meaning
        if k == KEY_RESIZE:
            self.renderer.invalidate()
            self.cache.clear()
            return True
        crt = self.renderer.crt
        if crt.on and (not isinstance(k, Mouse) or k.kind == "press"):
            crt.skip_boot()                  # a key during the power-on sweep ends it
            if crt.post is not None:
                crt.post = None              # any key ends the self test, and does nothing else
                crt.cache = None
                return True
            if crt.blinking:
                crt.blinking = False         # a blinking alert holds still
                if self.flash_style == "stalert":
                    self.flash_until = min(self.flash_until, time.time() + 10)
        if isinstance(k, Mouse):
            return self.on_mouse(k)
        if k == CTRL_C:
            return False
        if k == CTRL_Z:
            self.term.suspend()
            self.term.resumed = False
            self.renderer.reset_graphics()  # the terminal dropped our pictures while we were stopped
            self.cache.clear()
            return True
        if k == CTRL_L:
            self.renderer.reset_graphics()
            self.images.forget()
            self.cache.clear()
            return True
        if self.acknowledge_alarm():
            return True              # the first key after a tornado alarm just acknowledges it
        if self.idx == SETTINGS_IDX and self.settings.capturing:
            self.settings.edit_key(k)
            return True
        if self.idx == SETTINGS_IDX and self.settings.card is not None and self.local8s is None:
            return self.settings.card.key(k)     # the CRT effects card has every key while it's open
        if isinstance(k, Paste):
            return True
        if self.guide is not None:
            return self.guide_key(k)
        if self.help:
            self.help = False
            return True
        if self.local8s is not None:
            return self.local8s_key(k)
        if k in ("q", "Q"):
            return False
        if k == "?":
            self.help = True
        elif k == KEY_ESC and self.speech.active:
            self.speech.stop()
        elif k == KEY_ESC and SCREENS[self.idx][0] == "radar":
            self.radar_key(k)
        elif self.idx == SETTINGS_IDX and self.settings.crt_key(k):
            pass                             # a CRT effect row: digits set its level, R A U T act on it
        elif isinstance(k, str) and len(k) == 1 and k.isdigit():
            idx = 9 if k == "0" else int(k) - 1
            if idx < len(SCREENS):
                self.switch(idx)
        elif k in ("z", "Z"):
            if self.idx != SETTINGS_IDX:
                self.prev_idx = self.idx
            self.idx = SETTINGS_IDX
            self.settings.begin_edit("zip", quick=True)
        elif self.idx == SETTINGS_IDX and self.settings.nav_key(k):
            pass
        elif k == "L":
            self.local8s = Local8s(self)
            self.speech.stop()
        elif k in ("s",):
            self.toggle_speech()
        elif k == "[":
            self.speech.skip(-1)
        elif k == "]":
            self.speech.skip(1)
        elif SCREENS[self.idx][0] == "radar" and self.radar_key(k):
            pass
        elif SCREENS[self.idx][0] == "stations" and self.stations_key(k):
            pass
        elif k in ("\t", KEY_RIGHT, "l"):
            self.switch((self.idx + 1) % len(SCREENS))
        elif k in (KEY_BTAB, KEY_LEFT, "h"):
            self.switch((self.idx - 1) % len(SCREENS))
        elif k in ("r", "R", KEY_F[5]):
            self.flash("Refreshing…" if self.store.refresh(force=True) else "Already refreshing…")
            if self.radar:
                self.radar.invalidate()
        elif k in ("u", "U"):
            self.toggle_units()
        elif k in (KEY_DOWN, "j"):
            self.move(1)
        elif k in (KEY_UP, "k"):
            self.move(-1)
        elif k in (KEY_NPAGE, " "):
            self.move(max(1, self.body_h - 1))
        elif k == KEY_PPAGE:
            self.move(-max(1, self.body_h - 1))
        elif k in (KEY_HOME, "g"):
            self.move(-10 ** 6)
        elif k in (KEY_END, "G"):
            self.move(10 ** 6)
        elif k == "J":
            self.fscroll[self.idx] += 3
        elif k == "K":
            self.fscroll[self.idx] = max(0, self.fscroll[self.idx] - 3)
        return True

    def local8s_key(self, k):
        l8 = self.local8s
        if k in (KEY_ESC, "L", "q", "Q"):
            self.local8s = None
            self.speech.stop()
            self.renderer.invalidate()
        elif k in (KEY_RIGHT, "l", KEY_DOWN, "j", "\t"):
            l8.advance(1)
        elif k in (KEY_LEFT, "h", KEY_UP, "k", KEY_BTAB):
            l8.advance(-1)
        elif k == " ":
            l8.paused = not l8.paused
            l8.t0 = time.time()
        elif k == "s":
            if self.speech.active:
                self.speech.stop()
            else:
                title, text = l8.script(self.model, l8.page(self.model))
                if not self.speech.speak(title, text):
                    self.flash(f"Can't speak: {self.speech.error}")
        elif k == "n":
            self.cfg["narrate"] = "off" if on(self.cfg, "narrate") else "on"
            l8.narrate_failed = False
            self.settings.adopt("narrate")
            self.store.persist()
            if not on(self.cfg, "narrate"):
                self.speech.stop()
        return True

    def toggle_speech(self):
        if self.speech.active:
            self.speech.stop()
            return
        key = SCREENS[self.idx][0]
        fn = SCRIPTS.get(key)
        if fn is None or self.model is None:
            self.flash("Nothing to read on this screen.")
            return
        title, text = fn(self.model, self)
        if not self.speech.speak(title, text):
            self.flash(f"Can't speak: {self.speech.error}", 6)

    RADAR_KEYMAP = {"p": "product:next", "P": "product:prev", "e": "tilt:up", "E": "tilt:down",
                    "n": "site:next", "N": "site:prev", "m": "source:toggle", "v": "panes:cycle", "x": "inspect",
                    "i": "guide", "a": "toggle:cells", "o": "toggle:smooth", "y": "layer:next", "b": "basemap:next",
                    "l": "toggle:labels", "t": "toggle:temps", "w": "toggle:warnings", " ": "time:play",
                    ",": "time:back", ".": "time:next", "<": "time:begin", ">": "time:end", "+": "zoom:in",
                    "=": "zoom:in", "-": "zoom:out", "_": "zoom:out", "c": "center", "C": "center",
                    "d": "toggle:range", "D": "toggle:sites", "M": "toggle:markers", "F": "toggle:placefiles"}

    def radar_key(self, k):
        st = self.radar_state
        loc = (self.model or {}).get("loc") or {}
        if st.get("inspect_on") and k in (KEY_LEFT, KEY_RIGHT, KEY_UP, KEY_DOWN):
            self.move_inspect(k)
            return True
        if k in (KEY_LEFT, KEY_RIGHT, KEY_UP, KEY_DOWN) and loc.get("lat") is not None:
            import math
            lat = st["lat"] if st["lat"] is not None else loc["lat"]
            lon = st["lon"] if st["lon"] is not None else loc["lon"]
            step_mi = st["span"] / 4
            dlat = step_mi / 69.0
            dlon = step_mi / (69.0 * max(0.2, math.cos(math.radians(lat))))
            if k == KEY_LEFT:
                lon -= dlon
            elif k == KEY_RIGHT:
                lon += dlon
            elif k == KEY_UP:
                lat += dlat
            else:
                lat -= dlat
            st["lat"], st["lon"] = lat, lon
            st["frame"] = None
            return True
        if k == KEY_ESC and (st.get("inspect") is not None or st.get("inspect_on")):
            st["inspect"], st["inspect_on"] = None, False
            return True
        action = self.RADAR_KEYMAP.get(k) if isinstance(k, str) else None
        if action is None:
            return False
        self.radar_action(action)
        return True

    def radar_action(self, action):
        """Everything the radar toolbox buttons and keys can do."""
        from . import nexrad
        st = self.radar_state
        loc = (self.model or {}).get("loc") or {}
        kind, _, arg = action.partition(":")
        keep_frame = kind in ("time", "inspect", "guide", "toggle", "opacity", "speed")
        if kind == "product":
            keys = [p.key for p in nexrad.PRODUCTS]
            cur = st.get("product") or "ref"
            if arg in ("next", "prev"):
                i = keys.index(cur) if cur in keys else 0
                new = keys[(i + (1 if arg == "next" else -1)) % len(keys)]
            else:
                new = arg if arg in nexrad.PRODUCT_BY_KEY else cur
            st["product"] = new
            prod = nexrad.PRODUCT_BY_KEY[new]
            st["tilt"] = min(st.get("tilt", 0), len(prod.tilts) - 1)
            st["source"] = "site"
            self.flash(f"{nexrad.category_of(new)[0]} · {prod.name}")
        elif kind == "tilt":
            prod = nexrad.PRODUCT_BY_KEY.get(st.get("product") or "ref") or nexrad.PRODUCTS[0]
            n = len(prod.tilts)
            if n <= 1:
                self.flash(f"{prod.name} comes from the whole scan; it has no tilts")
                return
            cur = st.get("tilt", 0)
            new = cur + 1 if arg == "up" else cur - 1 if arg == "down" else int(arg or 0)
            st["tilt"] = max(0, min(n - 1, new))
            if st["tilt"] == cur and arg in ("up", "down"):
                self.flash("Already at the " + ("highest" if arg == "up" else "lowest") + " tilt")
                return
            name = nexrad.TILT_NAMES[st["tilt"]] if st["tilt"] < len(nexrad.TILT_NAMES) else str(st["tilt"] + 1)
            self.flash(f"Elevation about {name} (the scan shows the exact angle)")
        elif kind == "site":
            self.radar_site_step(arg, loc)
        elif kind == "source":
            st["source"] = ("mosaic" if st.get("source") == "site" else "site") if arg == "toggle" else arg
            self.flash("Site radar (one NEXRAD, Level 3)" if st["source"] == "site" else
                       "National radar mosaic (NOAA MRMS)")
        elif kind == "panes":
            st["panes"] = {1: 2, 2: 4}.get(st.get("panes") or 1, 1) if arg == "cycle" else int(arg)
            self.flash(f"{st['panes']} linked pane{'s' if st['panes'] > 1 else ''}")
        elif kind == "inspect":
            st["inspect_on"] = not st.get("inspect_on")
            if st["inspect_on"]:
                if st.get("inspect") is None:
                    lat = st["lat"] if st["lat"] is not None else loc.get("lat")
                    lon = st["lon"] if st["lon"] is not None else loc.get("lon")
                    st["inspect"] = (lat, lon) if lat is not None else None
                self.flash("Inspect: arrows move the crosshair, x or Esc stops")
            else:
                st["inspect"] = None
        elif kind == "guide":
            self.guide = 0
        elif kind == "toggle":
            st[arg] = not st.get(arg)
            names = {"cells": "Storm tracks and symbols", "smooth": "Smoothing", "labels": "City names",
                     "temps": "Station temperatures", "warnings": "Warning boxes", "range": "Range rings",
                     "sites": "Radar sites", "markers": "Location markers", "placefiles": "Placefiles"}
            self.flash(f"{names.get(arg, arg)} {'on' if st[arg] else 'off'}")
        elif kind == "layer":
            order = ["reflectivity", "type", "radar+sat", "satellite"]
            cur = st.get("layer") or "reflectivity"
            st["layer"] = order[(order.index(cur) + 1) % len(order)] if arg == "next" else arg
            st["source"] = "mosaic"
            self.flash(f"Mosaic layer: {st['layer']}")
        elif kind == "basemap":
            order = ["classic", "imagery", "topo", "plain", "osm"]
            cur = self.cfg.get("radar_basemap") or "classic"
            self.cfg["radar_basemap"] = order[(order.index(cur) + 1) % len(order)] if cur in order else order[0]
            self.store.persist()
            self.settings.adopt("radar_basemap")
            self.flash(f"Background: {self.cfg['radar_basemap']}")
        elif kind == "time":
            n = st.get("nframes") or 1
            cur = st["frame"] if st["frame"] is not None else n - 1
            if arg == "play":
                st["playing"] = not st["playing"]
            else:
                st["playing"] = False
                st["frame"] = {"begin": 0, "back": max(0, cur - 1), "next": min(n - 1, cur + 1),
                               "end": None}.get(arg, cur)
        elif kind in ("loop", "speed", "opacity"):
            key, lo, hi, step = {"loop": ("radar_frames", 2, 20, 2), "speed": ("radar_speed", 1, 10, 1),
                                 "opacity": ("radar_opacity", 30, 100, 10)}[kind]
            val = intval(self.cfg, key, lo, hi) + (step if arg == "+" else -step)
            self.cfg[key] = max(lo, min(hi, val))
            self.store.persist()
            self.settings.adopt(key)
            self.flash({"loop": f"Loop: {self.cfg[key]} scans", "speed": f"Loop speed: {self.cfg[key]} frames a second",
                        "opacity": f"Radar opacity: {self.cfg[key]}%"}[kind])
            keep_frame = kind != "loop"
        elif kind == "zoom":
            span = st["span"]
            if arg == "in":
                smaller = [z for z in ZOOMS if z < span]
                st["span"] = smaller[-1] if smaller else span
            else:
                bigger = [z for z in ZOOMS if z > span]
                st["span"] = bigger[0] if bigger else span
        elif kind == "center":
            st["lat"], st["lon"] = None, None
            st["site"] = None
            st["auto_site"] = None
            self.flash("Centered on home; the radar follows the map again")
        elif kind == "go":
            try:
                lat, lon = (float(x) for x in arg.split(","))
            except ValueError:
                return
            st["lat"], st["lon"] = lat, lon
            self.flash("Centered on the alert (c returns home)")
        if not keep_frame:
            st["frame"] = None  # the view changed: start again from the latest frame

    def radar_site_step(self, arg, loc):
        from . import nexrad
        st = self.radar_state
        if arg == "auto":
            shown = getattr(getattr(self, "last_radar", None), "view", None)
            here = st.get("auto_site") or (shown.site if shown is not None and shown.source == "site" else None)
            if st.get("site") is None and here:   # Auto was on: stay on this radar
                st["site"], st["source"] = here, "site"
                self.flash(f"Staying on {st['site']} (Auto is off; click Auto to follow the map again)")
                return
            st["site"] = None
            st["auto_site"] = None
            self.flash("Radar: Auto (the one nearest the map center)")
            return
        if self.radar is None:
            return
        lat = st["lat"] if st["lat"] is not None else loc.get("lat")
        lon = st["lon"] if st["lon"] is not None else loc.get("lon")
        if lat is None:
            return
        near = nexrad.nearest_sites(self.radar.sites(block=False), lat, lon, n=12)
        if not near:
            self.flash("The radar list is still loading…")
            return
        if arg == "menu":
            st["sites"] = True
            self.flash("Radar sites are on the map now: click one to switch to it")
            return
        if arg not in ("prev", "next"):
            info = self.radar.site_info(arg)
            st["site"], st["source"] = arg, "site"
            self.flash(f"Radar {arg} {str(info.get('name') or '').title()}")
            return
        ids = [x["id"] for x in near]
        cur = st.get("site") or st.get("auto_site") or ids[0]
        i = ids.index(cur) if cur in ids else 0
        pick = near[(i + (-1 if arg == "prev" else 1)) % len(near)]
        st["site"] = pick["id"]
        st["source"] = "site"
        self.flash(f"Radar {pick['id']} {pick['name'].title()}, {self.fmt.dist(pick['dist'])} from the map center")

    def move_inspect(self, k):
        import math
        st = self.radar_state
        loc = (self.model or {}).get("loc") or {}
        lat, lon = st.get("inspect") or (st["lat"] if st["lat"] is not None else loc.get("lat"),
                                         st["lon"] if st["lon"] is not None else loc.get("lon"))
        if lat is None:
            return
        step_mi = max(0.25, st["span"] / 60.0)
        dlat = step_mi / 69.0
        dlon = step_mi / (69.0 * max(0.2, math.cos(math.radians(lat))))
        if k == KEY_LEFT:
            lon -= dlon
        elif k == KEY_RIGHT:
            lon += dlon
        elif k == KEY_UP:
            lat += dlat
        else:
            lat -= dlat
        st["inspect"] = (lat, lon)

    def stations_key(self, k):
        from .screens2 import FILTERS, SORTS, station_rows
        if k == "f":
            cur = self.sel.get("st_filter", "weather")
            self.sel["st_filter"] = FILTERS[(FILTERS.index(cur) + 1) % len(FILTERS)]
            self.sel["stations"] = 0
            self.flash(f"Showing: {self.sel['st_filter']}")
        elif k == "o":
            rows = station_rows(self.model or {}, self)
            keep = rows[max(0, min(self.sel.get("stations", 0), len(rows) - 1))]["id"] if rows else None
            cur = self.sel.get("st_sort", "distance")
            self.sel["st_sort"] = SORTS[(SORTS.index(cur) + 1) % len(SORTS)]
            rows = station_rows(self.model or {}, self)  # keep the same station selected
            self.sel["stations"] = next((i for i, r in enumerate(rows) if r["id"] == keep), 0)
            self.flash(f"Sorted by {self.sel['st_sort']}")
        elif k in ("+", "=", "-", "_"):
            r = intval(self.cfg, "station_radius", 1, 100)
            steps = [1, 2, 3, 5, 10, 15, 20, 30, 50, 75, 100]
            if k in ("+", "="):
                r = next((s for s in steps if s > r), r)
            else:
                r = next((s for s in reversed(steps) if s < r), r)
            self.cfg["station_radius"] = r
            self.store.persist()
            self.settings.adopt("station_radius")
            self.store.refresh_stations(force=False)
            self.flash(f"Station radius: {self.fmt.dist(r)}")
        elif k == KEY_ENTER:
            rows = station_rows(self.model or {}, self)
            if rows:
                s = rows[max(0, min(self.sel.get("stations", 0), len(rows) - 1))]
                if not s.get("has_met"):
                    self.flash("That site doesn't report weather.")
                    return True
                with self.store.cfg_lock:
                    self.cfg["nws_station"] = s["id"]
                    self.cfg.pop("_nws_station", None)
                self.store.persist()
                self.settings.adopt("nws_station")
                for key in ("nws_obs", "nws_obs_hist", "nws_station", "nws_taf"):
                    self.store.clear(key)
                self.request_refresh()
                self.flash(f"Using {s['id']} ({s['name']}) for current conditions")
        else:
            return False
        return True

    def move(self, delta):
        key = {1: "hourly", 2: "daily", 4: "stations"}.get(self.idx)
        if key:
            if key == "stations":
                from .screens2 import station_rows
                count = len(station_rows(self.model or {}, self))
            else:
                count = len((self.model or {}).get(key) or [])
            if count:
                new = max(0, min(count - 1, self.sel[key] + delta))
                if new != self.sel[key]:
                    self.sel[key] = new
                    self.fscroll[self.idx] = 0
        else:
            self.scroll[self.idx] = max(0, self.scroll[self.idx] + delta)

    def toggle_units(self):
        new = "metric" if self.units() == "imperial" else "imperial"
        keep_draft = "units" not in self.settings.dirty()
        self.units_override = None
        with self.store.cfg_lock:
            self.cfg["units"] = new
        self.store.persist()
        if keep_draft:
            self.settings.draft["units"] = new
        self.settings.bump()
        self.request_refresh()
        self.flash(f"Units: {new}")

    def guide_key(self, k):
        page = max(1, self.term.size()[1] - 10)
        if k in (KEY_ESC, "i", "q", "Q", "?", KEY_ENTER):
            self.guide = None
        elif k in (KEY_DOWN, "j"):
            self.guide += 1
        elif k in (KEY_UP, "k"):
            self.guide = max(0, self.guide - 1)
        elif k in (KEY_NPAGE, " "):
            self.guide += page
        elif k == KEY_PPAGE:
            self.guide = max(0, self.guide - page)
        elif k in (KEY_HOME, "g"):
            self.guide = 0
        elif k in (KEY_END, "G"):
            self.guide = 10 ** 6
        return True

    def on_mouse(self, ev):
        oy, ox = self.ring_off
        if oy or ox:                                   # the CRT bezel's ring: the app sits inside it
            ev = Mouse(ev.kind, ev.button, ev.x - ox, ev.y - oy)
            if ev.x < 0 or ev.y < 0:
                return None
        lay = self.layout
        if ev.kind == "press" and self.acknowledge_alarm():
            return True
        if self.idx == SETTINGS_IDX and self.settings.card is not None and self.local8s is None:
            return self.settings.card.mouse(ev)
        if self.guide is not None:
            if ev.kind in ("wheel_up", "wheel_down"):
                self.guide = max(0, self.guide + (-3 if ev.kind == "wheel_up" else 3))
            elif ev.kind == "press":
                self.guide = None
            return True if ev.kind != "move" else None
        if self.sb_drag is not None:                   # a scroll bar's thumb in hand
            if ev.kind == "drag" and ev.button == 0:
                self.bar_scroll(ev.y)
                return True
            if ev.kind in ("release", "move", "drag"):  # let go (a move: no button down any more)
                self.sb_drag = None
                return True
            self.sb_drag = None
        on_radar = SCREENS[self.idx][0] == "radar" and self.local8s is None and not self.help
        if ev.kind == "move":
            if on_radar:
                hit = self.radar_hit(ev)
                st = self.radar_state
                if st.get("drag") and st["drag"][4]:
                    return True
                new = hit[0] if hit else None
                if new != st.get("hover"):
                    st["hover"] = new
                    return True
            return None
        if ev.kind in ("wheel_up", "wheel_down"):
            d = -3 if ev.kind == "wheel_up" else 3
            if lay.get("side_x") is not None and ev.x >= lay["side_x"]:
                self.fscroll[self.idx] = max(0, self.fscroll[self.idx] + d)
            elif on_radar:
                self.radar_key("+" if d < 0 else "-")
            else:
                self.move(d // 3 if self.idx in (1, 2, 4) else d)
            return True
        if on_radar and ev.kind in ("drag", "release") and ev.button == 0 and self.radar_state.get("drag"):
            return self.radar_drag(ev)
        if ev.kind != "press":
            return True
        if self.local8s is not None:
            self.local8s.advance(1)
            return True
        if self.help:
            self.help = False
            return True
        if ev.y == 1 and ev.button == 0:
            for x0, x1, i in lay.get("tabs", []):
                if x0 <= ev.x < x1:
                    self.switch(i)
                    return True
            return True
        top = lay.get("body_top")
        if top is None or ev.y < top:
            return True
        sb = lay.get("scrollbar")
        if sb and ev.button == 0 and ev.x == sb["x"] and sb["rows"][0] <= ev.y <= sb["rows"][-1]:
            k = ev.y - sb["rows"][0]
            # the thumb: hold it where it was taken; the bar: the thumb comes to the mouse
            self.sb_drag = k - sb["pos"] if sb["pos"] <= k < sb["pos"] + sb["size"] else sb["size"] // 2
            self.bar_scroll(ev.y)
            return True
        if on_radar:
            if lay.get("side_x") is not None and ev.x >= lay["side_x"]:
                return self.radar_side_click(ev)
            for row, x0, x1, action in lay.get("toolbar_hits") or []:
                if ev.y - top == row and x0 <= ev.x < x1:
                    self.radar_action(action)
                    return True
            hit = self.radar_hit(ev)
            if hit is None:
                return True
            (lat, lon), _res = hit
            st = self.radar_state
            if ev.button == 0:
                st["drag"] = (ev.x, ev.y, st["lat"], st["lon"], False, lat, lon)
            elif ev.button in (1, 2):
                st["lat"], st["lon"] = lat, lon
                st["frame"] = None
                self.flash("Map recentered (c returns home)")
            return True
        if ev.button != 0:
            return True
        rowmap = lay.get("rowmap") or {}
        sticky = lay.get("sticky", 0)
        line = None
        rel = ev.y - top
        if rel >= sticky + lay.get("body_h", 10 ** 6):
            return True  # a click in the detail pane below the table
        if rel < sticky:
            line = rel
        else:
            line = sticky + lay.get("scroll", 0) + (rel - sticky)
        if self.idx == SETTINGS_IDX and self.local8s is None:
            self.settings.click(line, ev.x)        # a section's title opens or closes, a button presses
            return True
        key = {1: "hourly", 2: "daily", 4: "stations"}.get(self.idx)
        if key and line in rowmap and (lay.get("side_x") is None or ev.x < lay["side_x"]):
            self.sel[key] = rowmap[line]
            self.fscroll[self.idx] = 0
        return True

    def radar_hit(self, ev):
        """((lat, lon), result) under the mouse on the radar map, or None."""
        lay = self.layout
        top = lay.get("body_top")
        if top is None:
            return None
        for gx, gy, gw, gh, res in lay.get("panes") or []:
            if res is None:
                continue
            cx, cy = ev.x - gx, ev.y - top - gy
            if 0 <= cx < gw and 0 <= cy < gh:
                px = (cx + 0.5) / gw * res.view.w
                py = (cy + 0.5) / gh * res.view.h
                return res.view.from_px(px, py), res
        return None

    def radar_drag(self, ev):
        import math
        st = self.radar_state
        x0, y0, lat0, lon0, moved, plat, plon = st["drag"]
        if ev.kind == "release":
            st["drag"] = None
            if not moved:
                site = self.site_at(ev)
                if site:
                    self.radar_site_step(site, (self.model or {}).get("loc") or {})
                else:
                    st["inspect"] = (plat, plon)
                    st["inspect_on"] = False
            return True
        if abs(ev.x - x0) + abs(ev.y - y0) < 1:
            return True
        loc = (self.model or {}).get("loc") or {}
        res = self.last_radar
        panes = self.layout.get("panes") or []
        if res is None or not panes or loc.get("lat") is None:
            return True
        _gx, _gy, gw, gh, pres = panes[0]
        view = (pres or res).view
        lat_c = lat0 if lat0 is not None else loc["lat"]
        lon_c = lon0 if lon0 is not None else loc["lon"]
        mi_x = (ev.x - x0) / gw * view.span_mi
        mi_y = (ev.y - y0) / gh * view.span_mi * view.h / view.w * view.yscale
        st["lat"] = lat_c + mi_y / 69.0
        st["lon"] = lon_c - mi_x / (69.0 * max(0.2, math.cos(math.radians(lat_c))))
        st["drag"] = (x0, y0, lat0, lon0, True, plat, plon)
        st["frame"] = None
        return True

    def site_at(self, ev):
        """The radar site icon under a click, if the Sites layer is on."""
        if not self.radar_state.get("sites"):
            return None
        hit = self.radar_hit(ev)
        if hit is None:
            return None
        (_lat, _lon), res = hit
        lay = self.layout
        for gx, gy, gw, gh, pres in lay.get("panes") or []:
            if pres is res:
                cx = (ev.x - gx + 0.5) / gw * res.view.w
                cy = (ev.y - lay["body_top"] - gy + 0.5) / gh * res.view.h
                cell_w = res.view.w / gw
                cell_h = res.view.h / gh
                for sid, px, py in getattr(res, "site_marks", []):
                    if abs(px - cx) <= cell_w * 1.2 and abs(py - cy) <= cell_h * 1.2:
                        return sid
        return None

    def radar_side_click(self, ev):
        st = self.radar_state
        lay = self.layout
        line = ev.y - lay["body_top"] + self.fscroll[self.idx]
        rel = ev.x - lay["side_x"]
        for row, x0, x1, action in st.get("_side_hits") or []:
            if row == line and x0 <= rel < x1:
                self.radar_action(action)
                return True
        return True

    # -- drawing --------------------------------------------------------------------------
    def header(self, canvas, W):
        th = self.theme
        h1, h2 = th.rgb("head1"), th.rgb("head2")
        truecolor = self.color_mode == "truecolor"
        for x in range(W):
            canvas.chars[0][x] = " "
            canvas.styles[0][x] = ("rgb", th.rgb("head_title"), blend(h1, h2, x / max(1, W - 1)), "b") if truecolor else "head"

        def bg(x):
            return blend(h1, h2, x / max(1, W - 1)) if truecolor else None

        def st(role, x, attrs=""):
            if not truecolor:
                return "head" if role == "title" else "headdim"
            return ("rgb", th.rgb("head_" + role), bg(x), attrs)

        loc = (self.model or {}).get("loc") or {}
        name = loc.get("name") or self.cfg.get("location_name") or ""
        zipc = loc.get("zip") or ""
        sym = "*" if self.ascii else "💧"
        # the name, then the version in the same chip, not bold
        chip = (th.rgb("brand_fg"), th.rgb("brand_bg"))
        x = canvas.put(0, 0, [(f" {sym} {APP} ", ("rgb",) + chip + ("b",) if truecolor else "tabon"),
                              (f"{VERSION} ", ("rgb",) + chip + ("",) if truecolor else "tabon")])
        x = canvas.put(0, x + 1, [(f" {name}", st("white", x + 1, "b")), (f" {zipc}" if zipc else "", st("label", x + 1))])
        F = self.fmt
        now = F.now()
        clock = F.clock(on(self.cfg, "clock_seconds"))
        date = f"{now:%a %b} {now.day}"
        right = f"{clock} {F.tzname()}  {date} "
        s = self.store
        if s.busy:
            spin = "|/-\\"[int(time.time() * 4) % 4]
            upd = f"Updating {spin}   "
        else:
            wait = max(0, int(self.next_refresh_at() - time.time()))
            done = F.time(s.last_done) if s.last_done else "never"
            upd = f"Updated {done} · next {wait // 60}:{wait % 60:02d}   "
        rx = W - len(right)
        # the weather right now, on every screen: the temperature in its color, the sky in words
        now_ = (self.model or {}).get("now") or {}
        temp, desc = now_.get("temp"), cap(now_.get("desc") or "")
        if temp is not None:
            # the title bar is dark in every theme: a light theme takes the dark themes' colors here
            tcol = PALETTES["weatherstar"][temp_style(temp)] if th.light else th.spec(temp_style(temp))[0]
            seg = [("   ", st("label", x)), (F.t(temp), ("rgb", tcol, bg(x + 3), "b") if truecolor else "head"),
                   (f" {desc}" if desc else "", st("white", x + 3))]
            if x + seglen(seg) + 2 > rx - len(upd) and len(desc) > 12:
                seg[2] = (f" {desc[:12].rstrip()}…", st("white", x + 3))
            if x + seglen(seg) + 2 <= rx:
                x = canvas.put(0, x, seg)
        if rx - len(upd) > x + 2:
            canvas.put(0, rx - len(upd), [(upd, st("label", rx - len(upd)))])
        if rx > x + 2:
            canvas.put(0, rx, [(right, st("white", rx, "b"))])

    def tabs(self, canvas, W):
        alerts = len((self.model or {}).get("alerts") or [])

        def label(i, name):
            return name + (f" {alerts}" if i == SCREEN_IDX["alerts"] and alerts else "")

        # widest layout that fits: full names, short names, then short names packed tight
        layouts = [(1, 1, lambda i, s: label(i, s[1])), (1, 1, lambda i, s: label(i, s[2])),
                   (0, 0, lambda i, s: label(i, s[2])), (0, 0, lambda i, s: label(i, s[2][:3]))]
        for lead, gap, name_of in layouts:
            total = sum(lead + 1 + len(name_of(i, sc)) + 2 + gap for i, sc in enumerate(SCREENS))
            if total <= W:
                break
        x = 0
        hot = []
        canvas.fill(1, 0, W, 1, "tab")
        for i, sc in enumerate(SCREENS):
            on_ = i == self.idx
            style = "tabon" if on_ else ("tabalert" if i == SCREEN_IDX["alerts"] and alerts else "tab")
            seg = [((" " if lead else "") + screen_key(i), "tabon" if on_ else "tabkey"), (f" {name_of(i, sc)} ", style)]
            w = seglen(seg)
            if x + w > W:
                break
            canvas.put(1, x, seg)
            hot.append((x, x + w, i))
            x += w + gap
        self.layout["tabs"] = hot

    def badges(self):
        s = self.store
        out = []

        def badge(label, keys, need_any=True):
            if all(s.is_off(k) for k in keys):
                return
            errs = sum(1 for k in keys if s.err(k))
            have = sum(1 for k in keys if s.get(k) is not None)
            if not have and not errs:
                out.append((label, "status"))
            elif errs and not have:
                out.append((label, "stbad"))
            elif errs:
                out.append((label, "stwarn"))
            else:
                out.append((label, "stgood"))

        badge("NWS", ("nws_obs", "nws_forecast", "nws_hourly", "nws_alerts"))
        if (self.cfg.get("owm_api_key") or "").strip():
            if s.fresh("owm_onecall", 3600) and not s.err("owm_onecall"):
                out.append(("OpenWeather", "stgood"))
            elif not s.is_off("owm_current"):
                badge("OWM free", ("owm_current",))
        badge("Open-Meteo", ("om_forecast",))
        badge("IEM", ("iem_currents", "iem_outlook"))
        badge("ACIS", ("acis",))
        return out

    def status(self, canvas, W, y):
        segs = [(" ", "status")]
        if time.time() < self.flash_until:
            segs.append((self.flash_msg + "   ", self.flash_style))
        else:
            for label, style in self.badges():
                segs += [("●" if not self.ascii else "*", style), (f" {label}  ", "status")]
            n = len(LOG)
            if n:
                segs += [(f"{'!' if self.ascii else '⚠'} {n} active error{'s' if n != 1 else ''}", "stbad"),
                         (" (0 Settings)  ", "status")]
        keys = [("s", "speak"), ("L", "local 8s"), ("z", "ZIP"), ("r", "refresh"), ("?", "help"), ("q", "quit")]
        more = ""
        if self.more and self.more[1] > self.more[2]:
            top, total, h = self.more
            more = f"{top + 1}-{min(total, top + h)}/{total}  "
        room = W - seglen(segs) - len(more)
        while keys and sum(len(k) + len(w) + 3 for k, w in keys) > room:
            keys.pop(0)
        right = [(more, "status")] if more else []
        for k, what in keys:
            right += [(k, "stkey"), (f" {what}  ", "status")]
        segs.append((" " * max(0, W - seglen(segs) - seglen(right)), "status"))
        segs += right
        canvas.put(y, 0, segs, W)

    def speech_bar(self, canvas, W, y):
        sp = self.speech
        n = len(sp.sentences)
        cur = sp.current()
        head = [(" ♪ " if not self.ascii else " > ", "speaklabel"), (f"{sp.title} ", "speaklabel"),
                (f"{max(1, sp.index + 1)}/{n} ", "speak")]
        tail = [("  s/Esc stop  [ ] skip ", "speak")]
        room = W - seglen(head) - seglen(tail)
        body = cur if len(cur) <= room else cur[:max(0, room - 1)] + "…"
        line = head + [(" " + body + " ", "speakhi" if sp.state == "speaking" else "speak")]
        line = padto(line, W - seglen(tail), "speak") + tail
        canvas.put(y, 0, line)

    def render_screen(self, W, H):
        """Render the current screen, cached until something it depends on changes."""
        key_name = SCREENS[self.idx][0]
        st = self.radar_state
        rv = self.radar.version if self.radar else 0
        ckey = (self.idx, self.store.version, self.settings.version if self.idx == SETTINGS_IDX else 0,
                tuple(sorted((k, v) for k, v in self.sel.items())), W, H, self.fmt.imp, self.gmode,
                self.theme.name, rv if key_name in ("radar", "now", "alerts", "stations") else 0,
                # every toolbox choice and radar setting: any of them can change the picture
                (tuple(sorted((k, repr(v)) for k, v in st.items() if k not in RADAR_VOLATILE and k[:1] != "_")),
                 tuple(sorted((k, repr(v)) for k, v in self.cfg.items() if k.startswith("radar_") or
                              k in ("map_markers", "placefiles"))),
                 st.get("hover") if key_name == "radar" else None)
                if key_name in ("radar", "now", "alerts", "stations") else None,
                self.speech.version if key_name in ("office", "settings") else 0,
                (LOG.version, self.speech.download) if key_name == "settings" else 0,
                int(time.time() // 30), (self.radar.busy() if self.radar else False), self.meter_look())
        hit = self.cache.get("screen")
        if hit and hit[0] == ckey:
            self.images.current.update(hit[2])
            return hit[1]
        self.images.begin_frame()
        name, fn = SCREENS[self.idx][1], SCREENS[self.idx][3]
        if fn is None:
            r = self.settings.render(W, H)
        else:
            try:
                self.speech_highlight = self.speech.current() if key_name == "office" and self.speech.active else None
                r = fn(self.model, self, W, H)
                LOG.resolve(f"draw:{key_name}")
            except Exception as e:  # a bad payload shouldn't take the app down
                import traceback
                tb = traceback.format_exc().splitlines()[-3:]
                log(f"draw {name}: " + traceback.format_exc())
                LOG.report(f"draw:{key_name}", f"{name} screen", f"{type(e).__name__}: {e}")
                r = {"lines": [[(f"  Couldn't draw {name}: {type(e).__name__}: {e}", "bad")]] +
                              [[("  " + t, "dim")] for t in tb]}
        if key_name == "radar" and r.get("radar") is not None:
            res = r["radar"]
            self.radar_state["nframes"] = len(res.frames)
        self.cache["screen"] = (ckey, r, dict(self.images.current))
        return r

    def draw(self):
        W, H, _xp, _yp = self.term.size()
        crt = self.renderer.crt
        crt.urgent = bool(self.alarm and self.alarm.get("kind") == "tornado" and not self.alarm.get("ack"))
        crt.first_screen = SCREENS[self.idx][1]
        ring = crt.ring(W, H)
        if ring:
            top, left, bottom, right = ring
            canvas = Canvas(W, H, "text")
            canvas.embed(self.compose(W - left - right, H - top - bottom), top, left)
            self.ring_off = (top, left)
        else:
            canvas = self.compose(W, H)
            self.ring_off = (0, 0)
        self.renderer.present(canvas, self.images, self.cell)

    def compose(self, W, H):
        """Everything on screen, W x H."""
        canvas = Canvas(W, H, "text")
        self.crawl.moving = False                # until the crawl draws counties going by
        if H < 16 or W < 60:
            canvas.put(0, 0, [("Make the terminal at least 60x16.", "warn")])
            self.images.begin_frame()
            return canvas
        if self.model is None:
            self.ensure_model()
        if self.local8s is not None:
            self.images.begin_frame()
            self.local8s.draw(canvas, self.model, W, H)
            return canvas
        self.header(canvas, W)
        self.tabs(canvas, W)
        ticker = (self.cfg.get("ticker") or "paged") != "off"
        crawl = on(self.cfg, "alert_ticker")
        if ticker and crawl and H < 26 and self.crawl.items:
            ticker = False                       # short screen: the warnings win
        bottom = H - 1 - (1 if ticker else 0) - (1 if crawl else 0)
        if self.speech.active:
            bottom -= 1
        top = 2                                  # the screen's cards start right under the tabs
        height = bottom - top
        r = self.render_screen(W, height)
        self.draw_body(canvas, r, top, height, W)
        y = bottom
        if self.speech.active:
            self.speech_bar(canvas, W, y)
            y += 1
        if ticker:
            canvas.put(y, 0, self.ticker.render(W, self.cfg.get("ticker") or "paged"))
            y += 1
        if crawl:
            canvas.put(y, 0, self.crawl.render(W, time.time(), self.calm_line(W), self.cfg.get("ticker_speed") or "normal",
                                               self.ascii), W)
            y += 1
        self.status(canvas, W, H - 1)
        if self.alarm is not None and self.alarm["kind"] == "tornado":
            self.draw_alarm(canvas, H, W)
        card = self.settings.card if self.idx == SETTINGS_IDX else None
        if card is not None:
            card.draw(canvas, H, W)
        if self.guide is not None:
            self.draw_guide(canvas, H, W)
        elif self.help:
            self.draw_help(canvas, H, W)
        elif card is None and SCREENS[self.idx][3] is None and self.settings.editing and r.get("ccol") is not None:
            row = self.layout.get("cursor_row")
            if row is not None:
                canvas.cursor = (row, min(W - 1, r["ccol"]))
        return canvas

    def draw_body(self, canvas, r, top, height, W):
        i = self.idx
        side = r.get("side")
        side_w = r.get("side_w", 0) if side else 0
        main_w = W - side_w - 1 if side_w else W
        lines = r.get("lines") or []
        footer = r.get("footer") or []
        sticky = min(r.get("sticky", 0), len(lines))
        fh = 0
        if footer:
            if len(lines) + len(footer) + 2 <= height:
                fh = len(footer) + 2
            else:
                fh = min(len(footer) + 2, max(height // 3, height - len(lines)), height - sticky - 3)
                fh = max(0, fh)
        main_h = height - fh
        body = lines[sticky:]
        body_h = max(1, main_h - sticky)
        self.body_h = body_h
        scroll = 0 if r.get("fixed") else self.scroll[i]
        cur = r.get("cursor")
        # follow the selection when it moves, but let PgDn/G scroll past it otherwise
        if cur is not None and cur >= sticky and (cur, len(lines)) != self.last_cursor.get(i):
            ci = cur - sticky
            if ci < scroll:
                scroll = ci
            elif ci >= scroll + body_h:
                scroll = ci - body_h + 1
        self.last_cursor[i] = (cur, len(lines))
        scroll = max(0, min(scroll, max(0, len(body) - body_h)))
        self.scroll[i] = scroll
        self.more = (scroll, len(body), body_h)
        y = top
        for ln in lines[:sticky]:
            canvas.put(y, 0, ln, main_w)
            y += 1
        for ln in body[scroll:scroll + body_h]:
            canvas.put(y, 0, ln, main_w)
            y += 1
        cursor_row = None
        if cur is not None:
            if cur < sticky:
                cursor_row = top + cur
            elif scroll <= cur - sticky < scroll + body_h:
                cursor_row = top + sticky + (cur - sticky - scroll)
        self.layout.update({"body_top": top, "sticky": sticky, "scroll": scroll, "rowmap": r.get("rowmap"),
                            "body_h": body_h,
                            "cursor_row": cursor_row, "side_x": (main_w + 1) if side_w else None,
                            "map_rect": r.get("map_rect"), "panes": r.get("panes"),
                            "toolbar_hits": r.get("toolbar_hits")})
        self.layout["scrollbar"] = (self.scrollbar(canvas, main_w - 1, top + sticky, body_h, scroll, len(body))
                                    if r.get("scrollbar") and len(body) > body_h else None)
        if fh:
            # the detail pane: a card along the bottom, its lines scrolling with J and K
            fy = top + height - fh
            room = max(0, fh - 2)
            fs = max(0, min(self.fscroll[i], len(footer) - room))
            self.fscroll[i] = fs
            title = r.get("footer_title") or ""
            more = f"{fs + 1}-{min(len(footer), fs + room)} of {len(footer)}, J/K scroll" if len(footer) > room else ""
            box = card(title, [dedent(ln, 2) for ln in footer[fs:fs + room]], main_w, more, self.ascii, room)
            for k, ln in enumerate(box[:fh]):
                canvas.put(fy + k, 0, ln, main_w)
        if side_w:
            sx = main_w
            fs = max(0, min(self.fscroll[i], max(0, len(side) - height)))
            self.fscroll[i] = fs
            self.layout["side_total"], self.layout["side_h"] = len(side), height
            for k, ln in enumerate(side[fs:fs + height]):
                canvas.put(top + k, sx + 1, ln, side_w)
            if len(side) > height:
                ind = "▼" if fs + height < len(side) else "▲"
                canvas.put(top + height - 1, W - 1, [(ind, "dim")])

    def scrollbar(self, canvas, x, y0, h, scroll, total):
        """A scroll bar down the right edge of the card that scrolls, over its border: the
        thumb is the part on screen, and where it sits is how far down that is. Returns where
        it is, for the mouse (press the bar to jump there, drag the thumb)."""
        edge = "|" if self.ascii else "│"
        rows = [y for y in range(max(0, y0), min(y0 + h, canvas.h))
                if 0 <= x < canvas.w and canvas.chars[y][x] == edge]
        if len(rows) < 3:
            return None
        n = len(rows)
        span = max(1, total - h)
        size = max(1, min(n - 1, round(n * h / total)))
        pos = round((n - size) * max(0, min(scroll, span)) / span)
        for y in rows[pos:pos + size]:
            canvas.put(y, x, [("#" if self.ascii else "█", ("onpanel", "title"))], 1)
        return {"x": x, "rows": rows, "pos": pos, "size": size, "span": span}

    def bar_scroll(self, y):
        """Scroll to where the mouse is on the scroll bar, the thumb held where it was taken."""
        sb = self.layout.get("scrollbar")
        if not sb:
            return
        n, size = len(sb["rows"]), sb["size"]
        pos = max(0, min(n - size, y - sb["rows"][0] - (self.sb_drag or 0)))
        self.scroll[self.idx] = round(pos * sb["span"] / max(1, n - size))

    def calm_line(self, W):
        """The crawl line when nothing is in effect."""
        m = self.model or {}
        area = str(self.cfg.get("ticker_area") or "100").lower()
        state = ((m.get("pts") or {}).get("state") or "").upper()
        where = {"zip": "for your ZIP code", "county": "for your county",
                 "state": f"in {state}" if state else "in your state"}.get(area, f"within {self.fmt.dist(float(area))}"
                                                                          if area.replace(".", "").isdigit() else "")
        err = self.store.err("nws_alerts_area") or self.store.err("nws_alerts")
        at = m.get("alerts_at")
        if err and not m.get("area_alerts"):
            segs = [(" ! " if self.ascii else " ⚠ ", "stwarn"), (f"Can't check alerts right now: {err}", "status")]
        else:
            segs = [(" ✓ " if not self.ascii else " ok ", "stgood"), (f"No watches or warnings {where}", "status"),
                    (f"   checked {self.fmt.time(at)}" if at else "   checking…", "status")]
        return [(t, st) for t, st in segs] + [(" " * max(0, W - sum(len(t) for t, _s in segs)), "status")]

    def home_zip(self):
        return (((self.model or {}).get("loc") or {}).get("zip") or "").strip()

    def draw_alarm(self, canvas, H, W):
        """The tornado warning banner: big and flashing until acknowledged, then a red bar."""
        al = self.alarm
        if al.get("test"):
            return
        a = al["alert"]
        F = self.fmt
        from .alerts import threat_text
        flash_on = int(time.time() * 2) % 2 == 0
        red, white = (200, 0, 20), (255, 255, 255)
        until = f" until {F.time(a['end'])}" if a.get("end") else ""
        title = ("TORNADO EMERGENCY" if a.get("emergency") else a["event"].upper()) + until
        if al["ack"]:
            # acknowledged: the crawl along the bottom carries it; with the crawl off, a red bar
            # under the tabs keeps it in view
            if on(self.cfg, "alert_ticker"):
                return
            zc = self.home_zip()
            covers = f"covers {zc}" if zc else "covers your ZIP code"
            line = f" ▲ {title} · {covers} · press 6 for details " if not self.ascii else \
                f" ! {title} - {covers} - press 6 "
            canvas.put(2, 0, [(line.ljust(W)[:W], ("rgb", white, red if flash_on else (150, 0, 15), "b"))])
            return
        bw = min(W - 4, 96)
        x0 = (W - bw) // 2
        y0 = 4
        bg = red if flash_on else (120, 0, 10)
        lines = []
        lines.append(("", "b"))
        lines.append(((" ▲ " if not self.ascii else " ! ") + title + (" ▲ " if not self.ascii else " ! "), "big"))
        lines.append(("", "b"))
        zc = self.home_zip()
        detail = " · ".join(x for x in (threat_text(a), f"covers {zc}" if zc else "covers your ZIP code") if x)
        lines.append((detail, ""))
        mo = a.get("motion")
        if mo and mo.get("kt"):
            from .wxmath import compass
            dist = f"{F.dist(mo['dist'])} from you, " if mo.get("dist") is not None else ""
            lines.append((f"Storm {dist}moving {compass(mo['toward'])} at {F.speed_mph(mo['kt'] * 1.15078)}", ""))
        area = a.get("area") or ""
        if area:
            lines.append((area[:bw - 6], ""))
        lines.append(("", ""))
        instr = (a.get("instruction") or "TAKE COVER NOW! Move to a basement or an interior room on the lowest "
                                          "floor of a sturdy building. Avoid windows.").replace("\n", " ")
        import textwrap
        for piece in textwrap.wrap(" ".join(instr.split()), bw - 6)[:4]:
            lines.append((piece, "b"))
        lines.append(("", ""))
        lines.append(("press any key to acknowledge (the warning stays on screen)", "dim"))
        lines.append(("", ""))
        for k, (text, kind) in enumerate(lines):
            if kind == "big":
                style = ("rgb", red if flash_on else white, white if flash_on else red, "b")
            elif kind == "dim":
                style = ("rgb", (255, 200, 200), bg, "")
            else:
                style = ("rgb", white, bg, "b" if kind == "b" else "")
            canvas.put(y0 + k, x0, [(text.center(bw)[:bw], style)])

    def draw_guide(self, canvas, H, W):
        """The radar guide card: a framed, scrollable panel over the screen."""
        from .guide import guide_lines
        bw = max(50, min(W - 4, 112))
        inner = bw - 4
        lines = guide_lines(inner, self.ascii)
        bh = max(8, min(H - 2, len(lines) + 4))
        room = bh - 4
        top = max(0, min(self.guide, max(0, len(lines) - room)))
        self.guide = top
        y0, x0 = max(0, (H - bh) // 2), max(0, (W - bw) // 2)
        for yy in range(max(3, y0 - 1), min(H - 1, y0 + bh + 1)):   # a quiet backdrop behind the card
            canvas.put(yy, 0, [(" " * W, "status")])
        edge = "paneltitle"
        h_, v_ = ("-", "|") if self.ascii else ("─", "│")
        tl, tr, bl, br = corners(self.ascii)
        title = " RADAR GUIDE "
        canvas.put(y0, x0, [(tl + h_ * 2, edge), (title, "paneltitle"), (h_ * (bw - 4 - len(title)) + tr, edge)])
        for r in range(room):
            ln = lines[top + r] if top + r < len(lines) else []
            row = [(v_ + " ", edge)] + list(ln)
            used = sum(len(t) for t, _s in row)
            row.append((" " * max(0, bw - 1 - used), "panel"))
            canvas.put(y0 + 1 + r, x0, row[:], bw - 1)
            canvas.put(y0 + 1 + r, x0 + bw - 1, [(v_, edge)])
        pos = f" {top + 1}-{min(len(lines), top + room)} of {len(lines)} "
        foot = " ↑↓ PgUp PgDn scroll · i or Esc closes " if not self.ascii else " up/down scroll, i or Esc closes "
        canvas.put(y0 + bh - 3, x0, [(v_, edge), (" " * (bw - 2), "panel"), (v_, edge)])
        canvas.put(y0 + bh - 2, x0, [(v_, edge), (foot.ljust(bw - 2 - len(pos))[:bw - 2 - len(pos)], "paneldim"),
                                     (pos, "paneldim"), (v_, edge)])
        canvas.put(y0 + bh - 1, x0, [(bl + h_ * (bw - 2) + br, edge)])

    def draw_help(self, canvas, H, W):
        """The keys, in a card over the screen with a shadow under it."""
        import textwrap
        groups = ("Radar", "Alarm", "Stations", "Settings", "CRT", "Window")
        rows = list(HELP)
        if getattr(self.term, "is_window", False):
            at = next(i for i, (k, _w) in enumerate(rows) if k == "Radar")
            rows[at:at] = WINDOW_HELP
        need = max(len(what) for _k, what in rows) + 24
        bw = max(44, min(W - 4, need))
        tw = bw - 4 - 18
        body = []
        for keys, what in rows:
            if keys in groups:
                body.append([])
            for j, part in enumerate(textwrap.wrap(what, tw) or [""]):
                body.append([((keys if j == 0 else "").ljust(18), "title" if keys in groups else "accent"),
                             (part, "text")])
        body += [[], [(f"Drawing: {self.gmode} graphics, {self.color_mode} color", "dim")]]
        body = body[:max(1, H - 5)]
        box = card("Keys", body, bw, f"{APP} {VERSION} · any key closes this", self.ascii)
        bh = len(box)
        y0, x0 = max(0, (H - bh) // 2), max(0, (W - bw) // 2)
        self.shadow(canvas, y0, x0, bw, bh)
        for i, ln in enumerate(box):
            canvas.put(y0 + i, x0, ln, bw)

    def shadow(self, canvas, y0, x0, w, h):
        """A dark edge to the right of and under a box drawn over the screen, as if it
        floated over it."""
        if self.renderer.crt.on:
            return
        canvas.fill(y0 + 1, x0 + w, 1, h, "status")
        canvas.fill(y0 + h, x0 + 1, w, 1, "status")
