"""The CRT effects card (Settings, c): CRT mode itself, its phosphor, a bitmap-style font for
the terminal and the meters' look at the top, then every CRT effect with its level from 0
(off) to 10 - 5 the standard - as a meter. Every change shows at once and is saved as you
go; U puts back the levels the card opened with."""

from . import crt as C
from .term import (KEY_BACKSPACE, KEY_BTAB, KEY_DOWN, KEY_END, KEY_ENTER, KEY_ESC, KEY_HOME, KEY_LEFT, KEY_NPAGE,
                   KEY_PPAGE, KEY_RIGHT, KEY_UP)
from .text import corners, wrap

# the card's rows: five at the top (CRT mode, its phosphor, the font and its size - set in
# kitty itself, suggested elsewhere - and the meters' look), then every effect
TOP_ROWS = 5
ROWS = ("crt", "phosphor", "font", "fontsize", "meters") + tuple(C.EFFECT_KEYS)
LABELS = {"crt": "CRT effect", "phosphor": "Phosphor", "font": "Font", "fontsize": "Font size", "meters": "Meters"}
LABEL_W = 16


class CrtCard:
    def __init__(self, settings):
        self.settings = settings
        self.app = settings.app
        crt = self.app.renderer.crt
        self.cursor = TOP_ROWS if crt.on else 0
        self.offset = 0
        self.was = {}                 # effect -> its level before enter switched it off
        self.opened = C.levels_of(self.app.cfg)
        self.note = ""
        self.box = None               # (top, left, height, width) from the last draw
        self.hits = []                # (y, x0, x1, action) from the last draw
        self.last_click = None
        C.installed_font_families()   # fontconfig is asked meanwhile

    # -- what happens -----------------------------------------------------------------
    def close(self):
        self.settings.card = None
        self.settings.bump()

    def set_levels(self, levels, note=""):
        """New levels for the CRT effects (any left out: the standard), used at once and saved."""
        saved = self.settings.crt_change("crt_levels", C.levels_saved(levels))
        self.note = note if saved else "⚠ Not saved - see the status line."

    def set_level(self, key, level):
        levels = C.levels_of(self.app.cfg)
        level = max(0, min(C.LEVEL_TOP, int(level)))
        if levels[key] == level:
            return
        levels[key] = level
        word = C.level_word(level)
        self.set_levels(levels, f"{C.EFFECT_INFO[key][0]}: {level}" + (f" ({word})" if word else "") + ".")

    def try_effect(self, key):
        """T on the card: an effect that comes and goes, shown now."""
        got = self.app.renderer.crt.try_effect(key)
        if got == "alert":
            self.app.flash_alert("TEST ALERT: a new warning blinks like this until you press a key")
            got = "A test alert blinks on the status line."
        return got

    def step_font(self, delta):
        """The font before / after: saved, and in kitty set there at once."""
        keys = C.FONT_KEYS
        here = C.font_of(self.app.cfg)
        saved = self.settings.crt_change("crt_font", keys[(keys.index(here) + delta) % len(keys)])
        if not saved:
            return "⚠ Not saved - see the status line."
        return self.short(self.settings.apply_font())

    def copy_font_line(self):
        """The Font row's enter: in kitty the font set again; elsewhere the lines that set it,
        onto the clipboard."""
        if C.kitty_direct()[0]:
            return self.short(self.settings.apply_font())
        cfg = self.app.cfg
        key = C.font_of(cfg)
        kind = C.terminal_kind()
        lines = C.font_lines(key, C.font_size_of(cfg), kind)
        if not lines:
            return "The terminal's own font: nothing to copy."
        try:
            self.app.term.write(C.osc52("\n".join(lines)))
            self.app.term.flush()
        except Exception:
            return "⚠ The terminal took no clipboard text."
        return "Copied: paste it into the terminal's config." if kind else "Copied the font's name."

    def step_size(self, delta):
        return self.short(self.settings.step_font_size(delta))

    def reset_size(self):
        return self.short(self.settings.reset_font_size())

    @staticmethod
    def short(msg):
        """A status message cut down to the card's note (the status line has it whole)."""
        if not msg:
            return ""
        if msg.startswith("kitty: "):
            return msg.split(", set in ")[0] + ", set in kitty."
        return msg

    # -- keys and the mouse -------------------------------------------------------------
    def key(self, k):
        """Every key comes here while the card is open."""
        if not isinstance(k, str):
            return True
        settings = self.settings
        self.note = ""
        moves = {"j": 1, KEY_DOWN: 1, "k": -1, KEY_UP: -1, "\t": 1, KEY_BTAB: -1, KEY_NPAGE: 5, KEY_PPAGE: -5,
                 KEY_HOME: -99, "g": -99, KEY_END: 99, "G": 99}
        if k in moves:
            self.cursor = max(0, min(len(ROWS) - 1, self.cursor + moves[k]))
            settings.bump()
            return True
        if k in (KEY_ESC, "q", "Q", "c", KEY_BACKSPACE):
            self.close()
            return True
        if k in ("a", "A"):
            self.set_levels({}, "Every effect is back to the standard.")
            settings.bump()
            return True
        if k in ("u", "U"):
            self.set_levels(self.opened, "Back to how they were when the card opened.")
            settings.bump()
            return True
        row = ROWS[max(0, min(len(ROWS) - 1, self.cursor))]
        less = k in ("h", KEY_LEFT, "-")
        more = k in ("l", KEY_RIGHT, "+", "=")
        enter = k in (KEY_ENTER, "\r", "\n", " ")
        if row == "crt":
            if less or more or enter:
                want = not C.CRT_ON(self.app.cfg)
                settings.crt_toggle()
                if want and not self.app.renderer.crt.on:
                    self.note = "CRT mode needs color: skydrop was started with --no-color."
        elif row == "phosphor":
            if less or more or enter:
                settings.crt_cycle("crt_phosphor", -1 if less else 1, quiet=True)
                if not self.app.renderer.crt.on:
                    self.note = "Saved for when CRT effect is on."
        elif row == "font":
            if less or more:
                self.note = self.step_font(-1 if less else 1)
            elif enter:
                self.note = self.copy_font_line()
        elif row == "fontsize":
            if less or more:
                self.note = self.step_size(-1 if less else 1)
            elif enter or k in ("r", "R"):
                self.note = self.reset_size()
        elif row == "meters":
            if less or more or enter:
                settings.crt_cycle("meter_style", -1 if less else 1, quiet=True)
        else:
            level = C.levels_of(self.app.cfg)[row]
            new = None
            if less or more:
                new = max(0, min(C.LEVEL_TOP, level + (1 if more else -1)))
            elif len(k) == 1 and k.isdigit():
                new = int(k)
            elif enter:
                if level:
                    self.was[row] = level
                    new = 0
                else:
                    new = self.was.get(row) or C.STANDARD
            elif k in ("r", "R"):
                new = C.STANDARD
            elif k in ("t", "T"):
                self.note = self.try_effect(row) if row in C.CARD_TRY else ""
            if new is not None:
                self.set_level(row, new)
        settings.bump()
        return True

    def keys_for(self, row):
        """The card's keys for the row picked, as (key, what) pairs."""
        if row == "crt":
            pairs = [("enter", "switches it on / off")]
        elif row == "phosphor":
            pairs = [("h l", "the phosphor before / after")]
        elif row == "font":
            pairs = [("h l", "the font before / after"),
                     ("enter", "sets it in kitty again" if C.kitty_direct()[0] else "copies its lines")]
        elif row == "fontsize":
            pairs = [("h l", "smaller / larger"), ("enter", "back to its size")]
        elif row == "meters":
            pairs = [("h l", "the look before / after (every meter)")]
        else:
            pairs = [("h l", "less / more"), ("0-9", "sets it"), ("enter", "off / back")]
            if row in C.CARD_TRY:
                pairs.append(("T", C.CARD_TRY[row]))
            pairs.append(("R", "standard"))
        return pairs + [("A", "all standard"), ("U", "undo"), ("esc", "close")]

    def mouse(self, ev):
        if ev.kind in ("wheel_up", "wheel_down"):
            self.cursor = max(0, min(len(ROWS) - 1, self.cursor + (-3 if ev.kind == "wheel_up" else 3)))
            self.settings.bump()
            return True
        if ev.kind == "move":
            return None
        if ev.kind != "press" or ev.button != 0 or self.box is None:
            return True
        top, left, height, width = self.box
        if not (top <= ev.y < top + height and left <= ev.x < left + width):
            self.close()                                   # a click outside closes the card
            return True
        for y, x0, x1, action in self.hits:
            if ev.y == y and x0 <= ev.x < x1:
                action()
                break
        self.settings.bump()
        return True

    def click_row(self, index):
        """A row: the first click picks it; on the top rows a click on the picked one switches it,
        as enter."""
        again = self.last_click == index and self.cursor == index
        self.last_click = index
        self.cursor = index
        self.note = ""
        if again and index < TOP_ROWS:
            self.key(KEY_ENTER)

    def click_level(self, index, level):
        """A click on a meter's segment sets the effect to that level."""
        self.cursor = index
        self.note = ""
        self.set_level(ROWS[index], level)

    def click_step(self, index, delta):
        """A click on a top row's ◂ or ▸: the choice before / after."""
        self.cursor = index
        self.key("l" if delta > 0 else "h")

    # -- what it shows -------------------------------------------------------------------
    def draw(self, canvas, height, width):
        app = self.app
        th = app.theme
        cfg = app.cfg
        crt = app.renderer.crt
        rows = ROWS
        cursor = self.cursor = max(0, min(len(rows) - 1, self.cursor))
        levels = C.levels_of(cfg)
        phosphor = C.phosphor_of(cfg)
        box_width = min(70, width - 2)
        inner = box_width - 4
        crt_on = crt.on
        glass_here = crt.pictures if crt_on else app.gmode == "kitty"
        font = C.font_of(cfg)
        size = C.font_size_of(cfg)
        hilite = th.rgb("hilite")
        sel, seldim = "sel", ("rgb", th.rgb("dim"), hilite, "")
        selbar = ("rgb", th.rgb("title"), hilite, "b")
        header = ("rgb", th.rgb("title"), th.rgb("bg"), "bu")
        border = "box"
        look = C.meter_style(cfg)

        def value_spans(index, chosen):
            key = rows[index]
            main, note = (sel, seldim) if chosen else ("text", "dim")
            if key == "crt":
                if crt_on:
                    return [("on", main if chosen else "good"),
                            ("   the glass on top" if crt.pictures else "   no glass: it needs kitty or Ghostty",
                             note)]
                if C.CRT_ON(cfg):
                    return [("off", note), ("   it needs color (--no-color)", note)]
                return [("off", note), ("   enter switches it on", note)]
            if key == "phosphor":
                return [("◂ ", note), (C.PHOSPHOR_LABEL[phosphor], main), (" ▸", note)]
            if key == "font":
                spans = [("◂ ", note), (C.font_label(font), main), (" ▸", note)]
                word = C.font_word(font)
                if word:
                    spans.append(("   " + word, main if word == "in use" else note))
                return spans
            if key == "fontsize":
                spans = [("◂ ", note), (C.size_label(size), main), (" ▸", note)]
                if C.size_sharp(font, size):
                    spans.append(("   sharp", main if chosen else "good"))
                return spans
            if key == "meters":
                return [("◂ ", note), (C.METER_LABEL[look], main), (" ▸   ", note)] + C.meter_block(6, "accent")
            level = levels[key]
            shown = crt_on and (C.EFFECT_INFO[key][1] == "text" or glass_here)
            # the lit segments only; the rest of the scale is left empty
            spans = C.meter_block(level, "accent" if shown else "dim")
            if level < C.LEVEL_TOP:
                spans.append((" " * (C.LEVEL_TOP - level), main))
            spans.append((f" {level:>2}", main if level == C.STANDARD or chosen else "accent"))
            word = C.level_word(level)
            if word:
                spans.append((f"  {word}", note))
            return spans

        def pad(line, style):
            used = sum(len(t) for t, _s in line)
            if used < inner:
                line.append((" " * (inner - used), style))
            return line

        def build(roomy):
            lines, spots = [], {}
            for index, key in enumerate(rows):
                if index == TOP_ROWS or (index > TOP_ROWS and C.EFFECT_INFO[key][1] !=
                                         C.EFFECT_INFO[rows[index - 1]][1]):
                    if roomy:
                        lines.append([])
                    if C.EFFECT_INFO[key][1] == "text":
                        lines.append([(" On every screen", header)])
                    else:
                        lines.append([(" On the glass", header),
                                      ("  kitty, Ghostty" if glass_here or not crt_on else
                                       "  not in this terminal: it shows no pictures", "dim")])
                chosen = index == cursor
                base = sel if chosen else "text"
                label = LABELS.get(key) or C.EFFECT_INFO[key][0]
                line = [("▌" if chosen else " ", selbar if chosen else "text"), (f" {label:<{LABEL_W}} ", base)]
                line += value_spans(index, chosen)
                spots[index] = len(lines)
                lines.append(pad(line, base))
            return lines, spots

        # the picked row explained, then the keys
        key = rows[cursor]
        if key in ("crt", "phosphor", "meters"):
            from .settings import CRT_ROWS
            spec = next(r for r in CRT_ROWS if r[1] == {"crt": "crt", "phosphor": "crt_phosphor",
                                                        "meters": "meter_style"}[key])
            head = [(spec[2], "value")]
            text = spec[4]
        elif key == "font":
            head = [("Font", "value"), (" · ", "dim"), (C.font_label(font), "accent")]
            text = C.FONT_HELP + " " + C.font_advice(font, size=size)
        elif key == "fontsize":
            head = [("Font size", "value"), (" · ", "dim"), (C.size_label(size), "accent")]
            text = C.FONT_SIZE_HELP
        else:
            label, layer, text = C.EFFECT_INFO[key]
            head = [(label, "value"), (" · ", "dim"), (C.describe(key, levels[key], phosphor), "accent")]
            if layer == "glass" and crt_on and not crt.pictures:
                text += " Not in this terminal: the glass needs pictures (kitty, Ghostty)."
        about = [head] + wrap(text, inner, "dim", 0)
        keys, line = [], []
        for name, what in self.keys_for(key):
            piece = len(name) + 1 + len(what) + (3 if line else 0)
            if line and sum(len(t) for t, _ in line) + piece > inner:
                keys.append(line)
                line = []
            if line:
                line.append(("   ", "dim"))
            line += [(name, "accent"), (" " + what, "dim")]
        keys.append(line)
        rule = [("─" * inner, border)]
        room = height - 1 - 2               # above the status line, inside the borders
        lines, spots = build(True)
        tails = ([rule] + about + keys, [rule] + about[:1] + keys, about[:1] + keys, about[:1] + keys[:1],
                 keys[:1], [])
        if len(lines) + len(tails[0]) > room:
            lines, spots = build(False)
        tail = next((t for t in tails if len(lines) + len(t) <= room), [])
        view = max(1, min(room - len(tail), len(lines)))
        offset = self.offset
        at = spots[cursor]
        if at < offset + 1:
            offset = max(0, at - 1)
        elif at >= offset + view - 1:
            offset = at - view + 2
        offset = self.offset = max(0, min(offset, max(0, len(lines) - view)))
        box_height = view + len(tail) + 2
        top = max(0, (height - 1 - box_height) // 2)
        left = max(0, (width - box_width) // 2)
        asc = app.ascii
        h_, v_ = ("-", "|") if asc else ("─", "│")
        tl, tr, bl, br = corners(asc)
        for r in range(box_height):
            canvas.put(top + r, left, [(" " * box_width, "text")])
        canvas.put(top, left, [(tl + h_ * (box_width - 2) + tr, border)])
        canvas.put(top + box_height - 1, left, [(bl + h_ * (box_width - 2) + br, border)])
        for r in range(1, box_height - 1):
            canvas.put(top + r, left, [(v_, border)])
            canvas.put(top + r, left + box_width - 1, [(v_, border)])
        canvas.put(top, left + 2, [(" CRT effects "[:max(0, box_width - 4)], "title")])
        note = f" {self.note or 'saved as you go'} "[:max(0, box_width - 6)]
        canvas.put(top + box_height - 1, left + box_width - 2 - len(note), [(note, "accent" if self.note else "dim")])
        for r in range(view):
            index = offset + r
            if index < len(lines):
                canvas.put(top + 1 + r, left + 2, lines[index], inner)
        # the scrollbar, when the list is longer than its view
        total = len(lines)
        if total > view >= 3:
            span = max(1, int(view * view / total))
            position = int(offset * (view - span) / max(1, total - view))
            for r in range(view):
                mark = ("#" if asc else "█") if position <= r < position + span else (":" if asc else "░")
                canvas.put(top + 1 + r, left + box_width - 2, [(mark, border)])
        for r, ln in enumerate(tail):
            canvas.put(top + 1 + view + r, left + 2, ln, inner)
        self.box = (top, left, box_height, box_width)
        meter_x = left + 2 + 2 + LABEL_W + 1
        self.hits = []
        for index, at in spots.items():
            y = top + 1 + at - offset
            if not 0 <= at - offset < view:
                continue
            if 1 <= index < TOP_ROWS:
                name = {1: C.PHOSPHOR_LABEL[phosphor], 2: C.font_label(font), 3: C.size_label(size),
                        4: C.METER_LABEL[look]}[index]
                self.hits.append((y, meter_x, meter_x + 2, lambda i=index: self.click_step(i, -1)))
                self.hits.append((y, meter_x + 2 + len(name), meter_x + 4 + len(name),
                                  lambda i=index: self.click_step(i, 1)))
            elif index >= TOP_ROWS:
                for step in range(C.LEVEL_TOP):
                    self.hits.append((y, meter_x + step, meter_x + step + 1,
                                      lambda i=index, lv=step + 1: self.click_level(i, lv)))
            self.hits.append((y, left + 1, left + box_width - 1, lambda i=index: self.click_row(i)))
