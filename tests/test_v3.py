"""Unit tests for the 3.0 additions: placefiles, alerts and the alarm, the SAME
encoder, Settings fields, toolbox layout, the plain-text fallback, retries and the watch."""

import math
import os
import sys
import tempfile
import time
import unittest
import wave

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp(prefix="wt-cache-"))
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="wt-config-"))

from skydrop import alerts, placefile, sounds, ui  # noqa: E402
from skydrop.canvas import to_ascii  # noqa: E402
from skydrop.text import seglen  # noqa: E402


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t))


def _stamp(t):
    return time.strftime("%y%m%dT%H%MZ", time.gmtime(t))


def _feat(event, phen, sig, etn, ugc, geom=None, office="KFWD", extra=None):
    now = time.time()
    params = {"VTEC": [f"/O.NEW.{office}.{phen}.{sig}.{etn:04d}.{_stamp(now - 60)}-{_stamp(now + 3600)}/"]}
    params.update(extra or {})
    return {"properties": {"id": f"urn:{office}{phen}{sig}{etn}", "event": event, "status": "Actual",
                           "messageType": "Alert", "sent": _iso(now - 60), "effective": _iso(now - 60),
                           "expires": _iso(now + 3600), "areaDesc": "Somewhere, TX",
                           "geocode": {"UGC": ugc, "SAME": ["048113"]}, "parameters": params},
            "geometry": geom}


HOME = {"lat": 32.74, "lon": -96.82}
PTS = {"county": "https://api.weather.gov/zones/county/TXC113",
       "forecastZone": "https://api.weather.gov/zones/forecast/TXZ119"}


def _square(lat, lon, d):
    return {"type": "Polygon", "coordinates": [[[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d],
                                                [lon - d, lat + d], [lon - d, lat - d]]]}


class PlacefileTests(unittest.TestCase):
    SAMPLE = """; test
Title: Test file
RefreshSeconds: 45
Threshold: 999
Color: 255 255 0
Font: 1, 12, 1, "Arial"
IconFile: 1, 32, 32, 16, 16, "icons.png"
Place: 32.80, -97.00, Test Place
Text: 32.90, -96.60, 1, "Big\\ntext", "Hover"
Icon: 32.60, -96.50, 45, 1, 10, "Line one\\n" "line two"
Line: 3, 0, "A line"
32.70, -97.20
32.75, -97.00
End:
Polygon:
32.50, -97.10, 255, 0, 0, 90
32.55, -96.95
32.45, -96.90
End:
Threshold: 10
Text: 32.70, -96.70, 1, "zoomed in", ""
Object: 32.85, -96.75
  Threshold: 999
  Color: 255 128 0
  Icon: 0, 0, 0, 1, 2, "obj"
  Line: 2, 0, "object line"
  0, 0
  30, -30
  End:
End:
TimeRange: 2026-10-31T00:00:00Z 2026-10-31T00:10:00Z
Place: 32.60, -96.80, Timed
Bogus: line
"""

    def test_parse(self):
        pf = placefile.parse(self.SAMPLE, "https://example.com/pf/test.txt")
        self.assertEqual(pf["title"], "Test file")
        self.assertEqual(pf["refresh"], 45)
        self.assertEqual(pf["bad"], 1)
        self.assertTrue(pf["timed"])
        self.assertEqual(pf["icon_files"][1]["src"], "https://example.com/pf/icons.png")
        kinds = [i["k"] for i in pf["items"]]
        self.assertEqual(kinds, ["place", "text", "icon", "line", "poly", "text", "icon", "line", "place"])
        icon = pf["items"][2]
        self.assertEqual(icon["hover"], "Line one\nline two")
        self.assertEqual(icon["angle"], 45.0)
        obj_line = pf["items"][7]
        self.assertEqual(obj_line["at"], (32.85, -96.75))
        self.assertEqual(obj_line["color"], (255, 128, 0, 255))
        self.assertEqual(pf["items"][8]["color"], (255, 255, 0, 255))   # object state restored
        self.assertEqual(pf["items"][5]["thr"], 10.0)

    def test_fields(self):
        self.assertEqual(placefile._fields('-17, 13, 1, " 55 "'), ["-17", "13", "1", " 55 "])
        self.assertEqual(placefile._fields('0,0,0,1,2,"a\\n" "b"'), ["0", "0", "0", "1", "2", "a\nb"])

    def test_urls(self):
        got = placefile.urls("https://a.com/x.txt, https://b.com/y.php?a=1,2\n/home/me/z.txt;~/w.txt")
        self.assertEqual(got, ["https://a.com/x.txt", "https://b.com/y.php?a=1,2", "/home/me/z.txt", "~/w.txt"])

    def test_thresholds(self):
        self.assertTrue(placefile.shown(999, 500))
        self.assertTrue(placefile.shown(0, 500))
        self.assertTrue(placefile.shown(50, 40))
        self.assertFalse(placefile.shown(50, 60))
        self.assertTrue(placefile.shown(-50, 60))
        self.assertFalse(placefile.shown(-50, 40))

    def test_hover(self):
        class R:
            hover = [("pt", 10, 10, 5, "point"), ("line", [(0, 50), (100, 50)], 4, "line")]
        self.assertEqual(placefile.hover_at(R, 12, 12), "point")
        self.assertEqual(placefile.hover_at(R, 60, 52), "line")
        self.assertIsNone(placefile.hover_at(R, 60, 70))
        self.assertEqual(placefile.hover_at(R, 60, 70, tol=20), "line")


class AlertTests(unittest.TestCase):
    def test_vtec_and_motion(self):
        v = alerts.vtec_parse("/O.NEW.KFWD.TO.W.0012.261031T0100Z-261031T0145Z/")
        self.assertEqual((v["office"], v["phen"], v["sig"], v["etn"], v["action"]), ("KFWD", "TO", "W", 12, "NEW"))
        mo = alerts.motion_parse("2026-10-31T01:00:00-00:00...storm...225DEG...35KT...32.62,-96.95")
        self.assertEqual(round(mo["toward"]), 45)          # from the southwest, heading northeast
        self.assertEqual(mo["kt"], 35)

    def test_in_zip_and_alarm(self):
        tor = _feat("Tornado Warning", "TO", "W", 12, ["TXC113"], _square(32.75, -96.80, 0.12),
                    extra={"tornadoDetection": ["OBSERVED"]})
        far = _feat("Tornado Warning", "TO", "W", 13, ["TXC121"], _square(33.6, -96.8, 0.1))
        rows = alerts.build_area_alerts({"features": [tor, far]}, None, [], HOME, PTS, None)
        near = next(a for a in rows if a["etn"] == 12)
        away = next(a for a in rows if a["etn"] == 13)
        self.assertTrue(near["in_zip"])
        self.assertFalse(away["in_zip"])
        events = alerts.alarm_events(rows, {"tornado_alarm": "on", "warning_alarm": "chime"})
        self.assertEqual([(k, a["etn"]) for k, a, _key in events], [("tornado", 12)])
        self.assertIn("OBSERVED", alerts.threat_text(near))

    def test_watch_distance_and_areas(self):
        watch = _feat("Tornado Watch", "TO", "A", 612, ["TXC121"], office="KWNS")
        outline = {"features": [{"properties": {"num": 612, "type": "TOR"}, "geometry": _square(33.4, -96.7, 0.2)}]}
        adv = _feat("Wind Advisory", "WI", "Y", 3, ["TXZ120"])
        rows = alerts.build_area_alerts({"features": [watch, adv]}, None, [], HOME, PTS, None, None, 250, outline)
        w = next(a for a in rows if a["phen"] == "TO")
        self.assertAlmostEqual(w["dist"], 31.8, delta=2.0)
        self.assertTrue(alerts.in_area(w, "50"))
        self.assertFalse(alerts.in_area(w, "25"))
        a = next(a for a in rows if a["phen"] == "WI")
        self.assertFalse(alerts.in_area(a, "200"))
        self.assertTrue(alerts.in_area(a, "state"))

    def test_claim_once(self):
        key = f"TEST.{time.time()}"
        self.assertTrue(alerts.AlertWatcher.claim(key))
        self.assertFalse(alerts.AlertWatcher.claim(key))


def _goertzel(samples, freq, rate):
    k = 2 * math.cos(2 * math.pi * freq / rate)
    s1 = s2 = 0.0
    for x in samples:
        s1, s2 = x + k * s1 - s2, s1
    return s1 * s1 + s2 * s2 - k * s1 * s2


def _demod_same(samples, rate):
    """Decode the first SAME burst: AFSK bits (2083.3 Hz mark = 1), bytes LSB first."""
    spb = rate / sounds.BAUD
    start = next(i for i, x in enumerate(samples) if abs(x) > 1000)
    bits = []
    pos = start
    while pos + spb < len(samples) and len(bits) < 8 * 120:
        win = samples[int(pos):int(pos + spb)]
        bits.append(1 if _goertzel(win, sounds.MARK, rate) > _goertzel(win, sounds.SPACE, rate) else 0)
        pos += spb
    data = bytes(sum(b << i for i, b in enumerate(bits[j:j + 8])) for j in range(0, len(bits) - 7, 8))
    return data.lstrip(b"\xab").split(b"\x00")[0].decode("ascii", "replace")


class SameTests(unittest.TestCase):
    def test_header(self):
        h = sounds.same_header("TOR", ["048113"], 45, issued=time.mktime((2026, 10, 31, 1, 0, 0, 0, 0, 0)),
                               station="KFWD/NWS")
        self.assertTrue(h.startswith("ZCZC-WXR-TOR-048113+0045-"))
        self.assertTrue(h.endswith("-KFWD/NWS-"))

    def test_burst_decodes(self):
        path, secs = sounds.alarm_file("noaa", "TOR", ["048113"], 45, "KFWD/NWS")
        with wave.open(path) as w:
            rate = w.getframerate()
            raw = w.readframes(int(rate * 2.0))
        import array
        samples = array.array("h", raw)
        if sys.byteorder == "big":
            samples.byteswap()
        text = _demod_same(list(samples), rate)
        self.assertTrue(text.startswith("ZCZC-WXR-TOR-048113+"), text)
        self.assertGreater(secs, 8)


class SettingsTests(unittest.TestCase):
    def test_new_field_types(self):
        from skydrop.settings import FIELD_BY_KEY, parse_field, with_unit
        self.assertEqual(parse_field(FIELD_BY_KEY["radar_site"], "kfws"), ("KFWS", None))
        self.assertEqual(parse_field(FIELD_BY_KEY["radar_site"], ""), ("auto", None))
        self.assertIsNotNone(parse_field(FIELD_BY_KEY["radar_site"], "FWS")[1])
        self.assertEqual(parse_field(FIELD_BY_KEY["radar_pane_products"], "vel, cc"), ("vel,cc", None))
        self.assertIsNotNone(parse_field(FIELD_BY_KEY["radar_pane_products"], "vel,nope")[1])
        self.assertEqual(parse_field(FIELD_BY_KEY["radar_panes"], "4"), (4, None))
        self.assertEqual(parse_field(FIELD_BY_KEY["radar_min_dbz"], "-10"), (-10, None))
        self.assertEqual(parse_field(FIELD_BY_KEY["refresh_seconds"], "30"), (30, None))
        self.assertIsNotNone(parse_field(FIELD_BY_KEY["refresh_seconds"], "5")[1])
        self.assertEqual(with_unit("radar_opacity", 90), "90%")
        self.assertEqual(with_unit("ticker_area", "state"), "state")

    def test_every_field_has_a_default(self):
        from skydrop.config import DEFAULTS
        from skydrop.settings import FIELDS
        self.assertEqual([it[1] for it in FIELDS if it[0] == "fld" and it[1] not in DEFAULTS], [])


class ToolboxTests(unittest.TestCase):
    def test_buttons_kinds_and_hits(self):
        lines, hits = ui.buttons([("Smooth", "on", "t"), ("Opacity", "lbl", None), ("−", "act", "o-"),
                                  ("90%", "val", None), ("+", "act", "o+")], 40)
        text = "".join(t for t, _s in lines[0])
        for _row, x0, x1, action in hits:
            self.assertEqual(text[x0:x1].strip(), {"t": "■ Smooth", "o-": "−", "o+": "+"}[action])
        styles = {s for _t, s in lines[0]}
        self.assertTrue({"tgonmk", "btnlbl", "btnact", "btnval"} <= styles)

    def test_stepper_wraps_whole(self):
        lines, _hits = ui.buttons([("Loop", "lbl", None), ("−", "act", "a"), ("8", "val", None), ("+", "act", "b"),
                                   ("Speed", "lbl", None), ("−", "act", "c"), ("4", "val", None), ("+", "act", "d")], 20)
        self.assertEqual(len(lines), 2)
        self.assertTrue("".join(t for t, _s in lines[1]).strip().startswith("Speed"))
        self.assertTrue(all(seglen(ln) <= 20 for ln in lines))

    def test_grid_aligns(self):
        lines, hits = ui.buttons([(k, "opt", k) for k in ("REF", "VEL", "CC", "ZDR", "HC", "ET")], 40, grid=3)
        self.assertEqual(len(lines), 2)
        self.assertEqual([h[1] for h in hits if h[0] == 0], [h[1] for h in hits if h[0] == 1])


class LayoutTests(unittest.TestCase):
    def test_columns_balance(self):
        blocks = [[[("a", "text")]] * n for n in (10, 2, 3, 9, 4)]
        out, where = ui.columns(blocks, 2, 30)
        self.assertLessEqual(len(out), 17)
        self.assertEqual(where[0], (0, 0))
        self.assertEqual(sorted(set(c for c, _r in where.values())), [0, 1])
        self.assertFalse(any(st == "faint" for row in out for _t, st in row))     # a gap, no bar

    def test_runin(self):
        from skydrop.screens import runin
        rows = runin("Tonight", "Mostly cloudy, with a low around 60. East wind around 5 mph." * 2, 40)
        self.assertTrue(all(seglen(r) <= 41 for r in rows))
        self.assertTrue("".join(t for t, _s in rows[0]).startswith("  Tonight  Mostly"))


class MiscTests(unittest.TestCase):
    def test_to_ascii(self):
        self.assertEqual("".join(to_ascii(c) for c in "━▸■□◀▶é°"), "->#o<>eo")

    def test_transient(self):
        import socket
        import urllib.error
        from skydrop.net import _transient
        self.assertTrue(_transient(urllib.error.URLError(ConnectionResetError())))
        self.assertFalse(_transient(urllib.error.URLError(socket.gaierror())))
        self.assertFalse(_transient(socket.timeout("timed out")))

    @unittest.skipUnless(sys.platform.startswith("linux"), "the .desktop login entry is Linux's")
    def test_autostart_file(self):
        from skydrop import watch
        self.assertEqual(watch.set_autostart(True), "")
        with open(watch.AUTOSTART_PATH) as fh:
            body = fh.read()
        self.assertIn("--watch", body)
        self.assertIn("Type=Application", body)
        self.assertEqual(watch.set_autostart(False), "")
        self.assertFalse(os.path.exists(watch.AUTOSTART_PATH))

    def test_despeckle(self):
        from skydrop import gfx, radar
        if not gfx.HAVE_PIL:
            self.skipTest("Pillow missing")
        m = gfx.Image.new("L", (8, 6), 0)
        px = m.load()
        px[1, 1] = 30
        for x, y in ((5, 2), (6, 2), (5, 3), (6, 3)):
            px[x, y] = 40
        out = radar._despeckle(m).load()
        self.assertEqual(out[1, 1], 0)
        self.assertEqual(out[5, 2], 40)

    def test_storm_warnings_dedupe(self):
        from skydrop.radar import storm_warnings
        ring = [(-96.9, 32.6), (-96.7, 32.6), (-96.7, 32.8), (-96.9, 32.8), (-96.9, 32.6)]
        snap = {"sbw": [{"phen": "TO", "name": "Tornado Warning", "rings": [ring]}],
                "area_alerts": [{"src": "NWS", "sig": "W", "phen": "TO", "rings": [ring], "event": "Tornado Warning",
                                 "tags": {}},
                                {"src": "NWS", "sig": "W", "phen": "SV", "rings": [ring], "event": "Severe "
                                 "Thunderstorm Warning", "tags": {}}]}
        got = storm_warnings(snap)
        self.assertEqual(sorted(w["phen"] for w in got), ["SV", "TO"])


if __name__ == "__main__":
    unittest.main()


class ThemeTests(unittest.TestCase):
    def test_themes_and_renames(self):
        from skydrop.theme import THEMES, Theme, theme_name
        self.assertEqual(THEMES, ["weatherstar", "midnight", "retro8s", "star4000", "magnetic"])
        self.assertEqual(theme_name("amber"), "weatherstar")
        self.assertEqual(theme_name("green"), "weatherstar")
        self.assertEqual(theme_name("nope"), "weatherstar")
        self.assertTrue(Theme("magnetic").light)
        self.assertFalse(Theme("retro8s").light)

    def test_every_style_reads_in_every_theme(self):
        from skydrop.theme import THEMES, Theme, contrast
        quiet = {"faint", "box", "btndis", "tgoffmk", "tabkey", "dim", "paneldim", "inputdim", "tgoff", "btnlbl"}
        for name in THEMES:
            th = Theme(name)
            for style, (fg, bg, *_rest) in th.styles.items():
                need = 2.2 if style in quiet else 3.0
                self.assertGreaterEqual(contrast(fg, bg), need, f"{name}:{style}")

    def test_charts_follow_the_theme(self):
        from skydrop import charts
        from skydrop.theme import Theme
        dark, light = charts.Ink(Theme("weatherstar")), charts.Ink(Theme("magnetic"))
        self.assertGreater(sum(dark.text), 600)      # white values on a dark page
        self.assertLess(sum(light.text), 100)        # black values on the light board
        self.assertNotEqual(dark.temp(20), light.temp(20))

    def test_light_icons_get_a_rim(self):
        from skydrop import gfx, icons
        if not gfx.HAVE_PIL:
            self.skipTest("no Pillow")
        bg = (208, 226, 242)
        plain = icons.pixel_icon("cloudy", True, 60, 40, bg)
        rimmed = icons.pixel_icon("cloudy", True, 60, 40, bg, outline=(0, 0, 0))
        def dark(im):
            raw = im.convert("RGB").tobytes()
            return sum(1 for i in range(0, len(raw), 3) if sum(raw[i:i + 3]) < 150)
        self.assertGreater(dark(rimmed), dark(plain) + 20)
        # nothing wraps around to the opposite edge
        top = [rimmed.getpixel((x, 0)) for x in range(60)]
        self.assertTrue(all(max(abs(a - b) for a, b in zip(px, bg)) < 40 for px in top))


class RenderTests(unittest.TestCase):
    def _renderer(self):
        from skydrop.canvas import ImageRegistry, Renderer
        from skydrop.theme import Theme

        class Out:
            def __init__(self):
                self.data = []

            def write(self, s):
                self.data.append(s)

            def flush(self):
                pass

        out = Out()
        reg = ImageRegistry()
        reg.mode = "kitty"
        return out, Renderer(out, Theme(), "truecolor", "kitty"), reg

    def test_full_redraw_sends_pictures_again(self):
        """Ghostty drops every kitty image on ESC[2J, so a full redraw must transmit again."""
        from skydrop import gfx
        from skydrop.canvas import Canvas
        if not gfx.HAVE_PIL:
            self.skipTest("no Pillow")
        out, r, reg = self._renderer()
        pic = gfx.Image.new("RGB", (40, 40), (200, 0, 0))

        def frame():
            reg.begin_frame()
            c = Canvas(20, 6)
            for i, ln in enumerate(reg.block("pic", "same-key", 4, 2, lambda mode, w, h: pic)):
                c.put(i, 0, ln)
            out.data = []
            r.present(c, reg)
            return "".join(out.data)

        first = frame()
        self.assertIn("a=t", first)                 # transmitted
        again = frame()
        self.assertNotIn("a=t", again)              # nothing changed: nothing sent
        r.invalidate()                              # theme change, resize, Ctrl-L
        full = frame()
        self.assertIn("\x1b[2J", full)
        self.assertIn("a=d,d=A", full)              # free everything the terminal held
        self.assertIn("a=t", full)                  # and send the picture again
        self.assertLess(full.index("a=d,d=A"), full.index("a=t"))

    def test_wide_raindrop(self):
        from skydrop.canvas import Canvas
        out, r, reg = self._renderer()
        c = Canvas(12, 1)
        end = c.put(0, 0, [(" 💧 sky", "text")])
        self.assertEqual(end, 7)                    # 1 + 2 (the drop) + 1 + 3
        self.assertEqual(c.chars[0][1:4], ["💧", "", " "])
        r.present(c, reg)
        self.assertEqual("".join(out.data).count("💧"), 1)
        c2 = Canvas(3, 1)
        c2.put(0, 0, [("ab💧", "text")])            # no room for both halves
        self.assertEqual(c2.chars[0], ["a", "b", " "])


class SettingsKeyTests(unittest.TestCase):
    def test_nice_steps(self):
        from skydrop.settings import nice_steps
        st = nice_steps(10, 3600)
        self.assertEqual(st[0], 10)
        self.assertEqual(st[-1], 3600)
        self.assertIn(30, st)
        self.assertIn(60, st)
        self.assertTrue(all(isinstance(x, int) for x in st))
        self.assertEqual(nice_steps(1, 10), [1, 2, 3, 4, 5, 6, 8, 10])

    def test_legend_and_shortcuts(self):
        from skydrop.settings import BUTTON_KEYS, FIELDS, SHORTCUTS
        buttons = {it[1] for it in FIELDS if it[0] == "btn"}
        for action in ("save", "test", "revert", "restore", "test_alarm"):
            self.assertIn(action, buttons)
            self.assertIn(BUTTON_KEYS[action], SHORTCUTS)
            self.assertEqual(SHORTCUTS[BUTTON_KEYS[action]], action)
        self.assertEqual(SHORTCUTS["\x13"], "save")   # Ctrl-S


class RenameTests(unittest.TestCase):
    def test_radar_map_follows_theme_but_data_layers_dont(self):
        from skydrop.radar import View
        a = View(32.7, -96.7, 120, 400, 300, mapcolors=((1, 1, 1),) * 8)
        b = View(32.7, -96.7, 120, 400, 300, mapcolors=((2, 2, 2),) * 8)
        self.assertNotEqual(a.key(), b.key())
        self.assertNotEqual(a.base_key(), b.base_key())
        self.assertEqual(a.geo_key(), b.geo_key())
