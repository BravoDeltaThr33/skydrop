"""Colors. Styles are named roles resolved through the active palette.

The default "weatherstar" palette follows the late-80s/90s Weather Channel
"Local on the 8s" look: a deep royal-blue field, gold headings, white values,
sky-blue labels and orange highlights. "retro8s", "star4000" and "magnetic" take
after other TV weather of the era; "magnetic" is the one light theme.
"""

# palette roles -> RGB
PALETTES = {
    "weatherstar": {
        "chip": (30, 46, 142), "key": (84, 98, 158),
        "bg": (11, 22, 92), "bg2": (18, 34, 122), "bar": (7, 12, 56), "hilite": (40, 62, 178),
        "head1": (58, 20, 112), "head2": (22, 56, 168), "ldl": (26, 54, 160), "ldl2": (16, 34, 120),
        "text": (238, 241, 255), "value": (255, 255, 255), "label": (150, 196, 255),
        "dim": (128, 148, 214), "faint": (62, 82, 164), "title": (255, 214, 0),
        "accent": (255, 154, 31), "good": (124, 240, 140), "warn": (255, 214, 94),
        "bad": (255, 112, 112), "rain": (72, 214, 245), "snow": (220, 236, 255),
        "alertbg": (200, 16, 46), "watchbg": (255, 140, 0), "advisbg": (255, 222, 100),
        "ink": (11, 22, 92),
        "t_frigid": (205, 165, 255), "t_cold": (98, 170, 255), "t_cool": (128, 226, 255),
        "t_mild": (255, 246, 160), "t_warm": (255, 214, 80), "t_hot": (255, 160, 68),
        "t_scorch": (255, 92, 92),
        "aqi1": (0, 228, 0), "aqi2": (255, 255, 0), "aqi3": (255, 126, 0), "aqi4": (255, 48, 48),
        "aqi5": (196, 96, 220), "aqi6": (220, 70, 110),
    },
    "midnight": {
        "chip": (34, 36, 56), "key": (78, 82, 110),
        "bg": (12, 12, 18), "bg2": (24, 24, 34), "bar": (0, 0, 0), "hilite": (44, 48, 86),
        "head1": (36, 20, 64), "head2": (16, 40, 96), "ldl": (24, 30, 64), "ldl2": (14, 16, 36),
        "text": (226, 228, 236), "value": (255, 255, 255), "label": (140, 178, 228),
        "dim": (120, 126, 150), "faint": (72, 78, 104), "title": (255, 204, 64),
        "accent": (255, 150, 50), "good": (120, 230, 140), "warn": (250, 210, 100),
        "bad": (255, 110, 110), "rain": (80, 200, 240), "snow": (220, 236, 255),
        "alertbg": (190, 20, 40), "watchbg": (240, 130, 0), "advisbg": (240, 214, 100),
        "ink": (12, 12, 18),
        "t_frigid": (200, 160, 255), "t_cold": (96, 164, 250), "t_cool": (124, 220, 250),
        "t_mild": (250, 240, 160), "t_warm": (250, 210, 80), "t_hot": (250, 156, 66),
        "t_scorch": (250, 90, 90),
        "aqi1": (0, 220, 0), "aqi2": (240, 240, 0), "aqi3": (250, 126, 0), "aqi4": (250, 50, 50),
        "aqi5": (190, 96, 210), "aqi6": (210, 70, 110),
    },
    # The Weather Channel's "Retro 8s" extended forecast: steel and sky-blue panels,
    # gold headings, a navy-to-orange title bar.
    "retro8s": {
        "chip": (46, 92, 160), "key": (112, 138, 184),
        "bg": (22, 56, 110), "bg2": (34, 76, 138), "bar": (9, 22, 56), "hilite": (62, 110, 182),
        "head1": (10, 24, 68), "head2": (196, 88, 18), "ldl": (18, 42, 94), "ldl2": (10, 26, 64),
        "text": (236, 242, 255), "value": (255, 255, 255), "label": (170, 212, 255),
        "dim": (152, 178, 216), "faint": (82, 116, 172), "title": (255, 204, 51),
        "accent": (255, 150, 44), "good": (132, 238, 152), "warn": (255, 216, 96),
        "bad": (255, 124, 112), "rain": (112, 222, 255), "snow": (230, 240, 255),
        "alertbg": (196, 18, 40), "watchbg": (250, 140, 20), "advisbg": (250, 218, 100),
        "ink": (10, 22, 54),
        "head_label": (255, 228, 178),
        "t_frigid": (214, 178, 255), "t_cold": (134, 192, 255), "t_cool": (142, 232, 255),
        "t_mild": (255, 246, 170), "t_warm": (255, 214, 84), "t_hot": (255, 166, 76),
        "t_scorch": (255, 112, 102),
        "aqi1": (40, 230, 40), "aqi2": (255, 255, 40), "aqi3": (255, 140, 20), "aqi4": (255, 70, 70),
        "aqi5": (214, 120, 240), "aqi6": (240, 90, 130),
        "map_top": (8, 22, 52), "map_bot": (20, 50, 98), "map_water": (24, 62, 132),
        "map_county": (84, 122, 176), "map_road": (184, 92, 58), "map_state": (222, 232, 252),
        "map_glow": (120, 162, 224), "map_tint": (122, 142, 172),
    },
    # The WeatherSTAR 4000 hourly forecast: royal blue-violet panels under red-orange
    # bars, yellow numbers and white labels.
    "star4000": {
        "chip": (58, 50, 150), "key": (122, 118, 192),
        "bg": (30, 22, 106), "bg2": (44, 38, 138), "bar": (138, 38, 12), "hilite": (72, 62, 172),
        "head1": (168, 40, 12), "head2": (204, 88, 16), "ldl": (36, 30, 128), "ldl2": (22, 16, 86),
        "text": (220, 224, 255), "value": (255, 234, 64), "label": (240, 242, 255),
        "dim": (156, 154, 214), "faint": (86, 80, 170), "title": (255, 172, 42),
        "accent": (255, 128, 48), "good": (124, 240, 144), "warn": (255, 222, 96),
        "bad": (255, 112, 102), "rain": (112, 212, 255), "snow": (232, 236, 255),
        "alertbg": (206, 20, 30), "watchbg": (255, 150, 0), "advisbg": (255, 226, 110),
        "ink": (22, 12, 60),
        "head_title": (255, 240, 120), "head_label": (255, 236, 200), "head_text": (255, 255, 255),
        "bar_text": (255, 226, 196), "bar_key": (255, 240, 100), "bar_good": (170, 255, 170),
        "bar_warn": (255, 236, 130), "bar_bad": (255, 200, 190), "bar_field": (255, 240, 220),
        "accent_bg": (255, 132, 40),
        "t_frigid": (210, 172, 255), "t_cold": (120, 180, 255), "t_cool": (128, 230, 255),
        "t_mild": (255, 248, 168), "t_warm": (255, 220, 70), "t_hot": (255, 164, 66),
        "t_scorch": (255, 100, 96),
        "aqi1": (40, 230, 40), "aqi2": (255, 255, 40), "aqi3": (255, 140, 20), "aqi4": (255, 70, 70),
        "aqi5": (214, 120, 240), "aqi6": (240, 90, 130),
        "map_top": (14, 10, 56), "map_bot": (28, 26, 112), "map_water": (34, 46, 156),
        "map_county": (98, 96, 196), "map_road": (204, 94, 44), "map_state": (232, 228, 255),
        "map_glow": (144, 124, 240), "map_tint": (128, 120, 180),
    },
    # The 1970s BBC magnetic weather board: a pale blue board, dark lettering, bold
    # yellow and black magnets. The one light theme: bands stay dark, the page is light.
    "magnetic": {
        "light": True,
        "chip": (186, 206, 228), "key": (56, 66, 90),
        "bg": (208, 226, 242), "bg2": (230, 240, 250), "bar": (20, 40, 90), "hilite": (255, 222, 96),
        "head1": (18, 36, 88), "head2": (38, 84, 158), "ldl": (26, 50, 108), "ldl2": (16, 34, 80),
        "text": (22, 32, 54), "value": (0, 0, 0), "label": (30, 76, 146),
        "dim": (76, 94, 124), "faint": (122, 144, 174), "title": (170, 20, 30),
        "accent": (186, 84, 0), "good": (0, 122, 46), "warn": (150, 92, 0),
        "bad": (186, 16, 28), "rain": (0, 90, 170), "snow": (64, 90, 146),
        "alertbg": (196, 16, 38), "watchbg": (240, 130, 0), "advisbg": (255, 212, 84),
        "ink": (8, 10, 18),
        "accent_bg": (255, 204, 0), "title_bg": (255, 204, 0), "good_bg": (84, 186, 104),
        "warn_bg": (255, 196, 54),
        "sel_text": (8, 10, 18), "chip_text": (24, 58, 112), "key_text": (255, 255, 255),
        "alert_text": (255, 255, 255),
        "head_title": (255, 206, 0), "head_text": (255, 255, 255), "head_label": (196, 216, 246),
        "head_white": (255, 255, 255),
        "brand_fg": (8, 10, 18), "brand_bg": (255, 206, 0),
        "bar_text": (196, 214, 244), "bar_key": (255, 206, 0), "bar_good": (120, 232, 146), "bar_dim": (132, 154, 198),
        "bar_warn": (255, 214, 96), "bar_bad": (255, 140, 140), "bar_field": (232, 238, 250),
        "ldl_value": (255, 255, 255), "ldl_title": (255, 206, 0), "ldl_label": (190, 210, 242),
        "speak_text": (232, 238, 250), "speak_title": (255, 206, 0),
        "t_frigid": (110, 46, 170), "t_cold": (22, 78, 196), "t_cool": (0, 114, 146),
        "t_mild": (92, 100, 0), "t_warm": (166, 94, 0), "t_hot": (196, 62, 0),
        "t_scorch": (176, 0, 24),
        "aqi1": (0, 136, 0), "aqi2": (140, 120, 0), "aqi3": (200, 96, 0), "aqi4": (196, 20, 20),
        "aqi5": (126, 40, 156), "aqi6": (126, 0, 40),
        "map_top": (34, 80, 136), "map_bot": (48, 102, 162), "map_water": (26, 64, 122),
        "map_county": (122, 160, 204), "map_road": (150, 78, 60), "map_state": (246, 248, 255),
        "map_glow": (190, 214, 246), "map_tint": (120, 150, 182),
        "p_rain1": (44, 136, 44), "p_rain2": (8, 112, 40), "p_rain3": (0, 84, 30), "p_storm": (178, 106, 0),
        "p_snow1": (98, 120, 162), "p_snow2": (70, 94, 142), "p_snow3": (44, 66, 116),
        "p_sleet": (184, 50, 130), "p_ice": (156, 12, 112), "p_mix": (104, 60, 180),
    },
}

# Roles a palette may set; anything it leaves out falls back to the role named here.
# Text drawn on the dark bands (title bar, status bar, crawl) has its own roles so a
# light theme can keep dark bands with light lettering over a light page.
DERIVED = {
    "head_title": "title", "head_text": "text", "head_label": "label", "head_white": (255, 255, 255),
    "brand_fg": "ink", "brand_bg": "title",
    "bar_text": "label", "bar_key": "title", "bar_good": "good", "bar_warn": "warn", "bar_bad": "bad",
    "bar_dim": "dim",
    "bar_field": "text",
    "ldl_value": "value", "ldl_title": "title", "ldl_label": "label",
    "speak_text": "text", "speak_title": "title",
    "alert_text": "value",
    "accent_bg": "accent", "title_bg": "title", "good_bg": "good", "warn_bg": "warn",
    "sel_text": "title", "chip_text": "label", "key_text": "value",
    # the radar map underneath the echoes
    "map_top": (7, 14, 58), "map_bot": (16, 32, 104), "map_water": (30, 56, 150), "map_county": (78, 104, 186),
    "map_road": (176, 62, 80), "map_state": (214, 222, 250), "map_glow": (110, 130, 230),
    "map_tint": (120, 130, 175),
    # precipitation by type, in weather radar's colors: rain in greens (light to heavy),
    # thunderstorms gold, snow white, sleet pink, freezing rain magenta, a wintry mix violet
    "p_rain1": (150, 232, 140), "p_rain2": (64, 206, 92), "p_rain3": (0, 168, 64), "p_storm": (255, 200, 48),
    "p_snow1": (184, 200, 226), "p_snow2": (232, 238, 252), "p_snow3": (255, 255, 255),
    "p_sleet": (255, 156, 216), "p_ice": (240, 84, 204), "p_mix": (182, 146, 255),
}

# what each theme is, for Settings and --help
DESCRIPTIONS = {
    "weatherstar": "royal blue, gold and white, like the classic Local on the 8s",
    "midnight": "near-black with soft colors, easy on the eyes at night",
    "retro8s": "steel and sky blue panels with gold headings and a navy-to-orange title bar",
    "star4000": "WeatherSTAR 4000 blue-violet with red-orange bars and yellow numbers",
    "magnetic": "a light theme: the pale blue 1970s TV weather board with yellow and black magnets",
}

# old theme names -> what to use now
RENAMED = {"amber": "weatherstar", "green": "weatherstar"}

THEMES = list(PALETTES)

# name: (fg role, bg role, attrs, fg16, bg16, attrs16)
#   attrs: b bold, d dim, i italic, u underline, r reverse
#   16-color fallback indexes: 0 black 1 red 2 green 3 yellow 4 blue 5 magenta 6 cyan 7 white, +8 bright
STYLE_SPECS = {
    "text": ("text", "bg", "", 7, 4, ""),
    "value": ("value", "bg", "b", 15, 4, "b"),
    "label": ("label", "bg", "", 14, 4, ""),
    "dim": ("dim", "bg", "", 7, 4, ""),
    "faint": ("faint", "bg", "", 6, 4, ""),
    "title": ("title", "bg", "b", 11, 4, "b"),
    "accent": ("accent", "bg", "b", 11, 4, ""),
    "good": ("good", "bg", "", 10, 4, "b"),
    "warn": ("warn", "bg", "", 11, 4, ""),
    "bad": ("bad", "bg", "b", 9, 4, "b"),
    "rain": ("rain", "bg", "", 14, 4, "b"),
    "snow": ("snow", "bg", "b", 15, 4, "b"),
    "alert": ("alert_text", "alertbg", "b", 15, 1, "b"),
    "stalert": ("alert_text", "alertbg", "b", 15, 1, "b"),     # a new warning on the status line (blinks in CRT mode)
    "watch": ("ink", "watchbg", "b", 0, 3, "b"),
    "advis": ("ink", "advisbg", "", 0, 3, ""),
    "head": ("head_title", "head1", "b", 11, 5, "b"),
    "headdim": ("head_text", "head1", "", 15, 5, ""),
    "status": ("bar_text", "bar", "", 7, 0, ""),
    "stkey": ("bar_key", "bar", "b", 11, 0, "b"),
    "stgood": ("bar_good", "bar", "b", 10, 0, "b"),
    "stwarn": ("bar_warn", "bar", "b", 11, 0, "b"),
    "stbad": ("bar_bad", "bar", "b", 9, 0, "b"),
    "tab": ("bar_text", "bar", "", 14, 0, ""),           # the tabs sit on a dark band under the title bar
    "tabkey": ("bar_dim", "bar", "", 7, 0, ""),
    "tabon": ("ink", "accent_bg", "b", 0, 3, "b"),
    "tabalert": ("bar_bad", "bar", "b", 9, 0, "b"),
    "sel": ("sel_text", "hilite", "b", 11, 0, "b"),
    "field": ("bar_field", "bar", "", 7, 0, ""),
    "input": ("value", "bg", "u", 15, 4, "u"),          # a field you type into: an underlined line
    "inputdim": ("dim", "bg", "u", 7, 4, "u"),
    "fieldon": ("ink", "title_bg", "b", 0, 3, ""),
    "panel": ("text", "bg2", "", 7, 4, ""),
    "paneltitle": ("title", "bg2", "b", 11, 4, "b"),
    "panellabel": ("label", "bg2", "", 14, 4, ""),
    "paneldim": ("dim", "bg2", "", 7, 4, ""),
    "box": ("faint", "bg", "", 6, 4, ""),
    "card": ("cardline", "bg", "", 6, 4, ""),          # a card's rounded edge
    "ldl": ("ldl_value", "ldl", "", 15, 4, "b"),
    "ldllabel": ("ldl_title", "ldl", "b", 11, 4, "b"),
    "ldldim": ("ldl_label", "ldl", "", 14, 4, ""),
    "ldlalert": ("alert_text", "alertbg", "b", 15, 1, "b"),
    "speak": ("speak_text", "ldl2", "", 7, 0, ""),
    "speakhi": ("ink", "title_bg", "b", 0, 3, "b"),
    "speaklabel": ("speak_title", "ldl2", "b", 11, 0, "b"),
    "badge": ("ink", "good_bg", "b", 0, 2, "b"),
    # radar toolbox controls: each kind looks different, so they never run together
    "btnsel": ("ink", "accent_bg", "b", 0, 3, "b"),       # the chosen one of several: filled amber
    "btnopt": ("chip_text", "chip", "", 14, 0, ""),        # the other choices: quiet chips
    "btnact": ("key_text", "key", "b", 0, 7, "b"),        # one-shot keys (◀ ▶ + −): grey keycaps
    "btnval": ("value", "bg", "b", 15, 4, "b"),        # a value between keys: plain bold text
    "btnlbl": ("dim", "bg", "", 7, 4, ""),             # a caption
    "btndis": ("faint", "bg", "", 8, 4, ""),
    "tgon": ("value", "bg", "b", 15, 4, "b"),          # switches: a check box, no fill
    "tgonmk": ("good", "bg", "b", 10, 4, "b"),
    "tgoff": ("dim", "bg", "", 7, 4, ""),
    "tgoffmk": ("faint", "bg", "", 7, 4, ""),
    "badgewarn": ("ink", "warn_bg", "b", 0, 3, "b"),
    "t_frigid": ("t_frigid", "bg", "b", 13, 4, "b"),
    "t_cold": ("t_cold", "bg", "b", 12, 4, "b"),
    "t_cool": ("t_cool", "bg", "b", 14, 4, "b"),
    "t_mild": ("t_mild", "bg", "b", 15, 4, "b"),
    "t_warm": ("t_warm", "bg", "b", 11, 4, "b"),
    "t_hot": ("t_hot", "bg", "b", 3, 4, "b"),
    "t_scorch": ("t_scorch", "bg", "b", 9, 4, "b"),
    "aqi1": ("aqi1", "bg", "b", 10, 4, "b"),
    "aqi2": ("aqi2", "bg", "b", 11, 4, "b"),
    "aqi3": ("aqi3", "bg", "b", 3, 4, "b"),
    "aqi4": ("aqi4", "bg", "b", 9, 4, "b"),
    "aqi5": ("aqi5", "bg", "b", 13, 4, "b"),
    "aqi6": ("aqi6", "bg", "b", 5, 4, "b"),
    "p_rain1": ("p_rain1", "bg", "b", 10, 4, "b"),
    "p_rain2": ("p_rain2", "bg", "b", 10, 4, "b"),
    "p_rain3": ("p_rain3", "bg", "b", 2, 4, "b"),
    "p_storm": ("p_storm", "bg", "b", 11, 4, "b"),
    "p_snow1": ("p_snow1", "bg", "", 7, 4, ""),
    "p_snow2": ("p_snow2", "bg", "b", 15, 4, "b"),
    "p_snow3": ("p_snow3", "bg", "b", 15, 4, "b"),
    "p_sleet": ("p_sleet", "bg", "b", 13, 4, "b"),
    "p_ice": ("p_ice", "bg", "b", 5, 4, "b"),
    "p_mix": ("p_mix", "bg", "b", 12, 4, "b"),
}

# Approximation of the standard 16-color palette, for RGB -> 16 mapping.
ANSI16 = [(0, 0, 0), (170, 0, 0), (0, 170, 0), (170, 110, 0), (0, 0, 170), (170, 0, 170),
          (0, 170, 170), (190, 190, 190), (85, 85, 85), (255, 85, 85), (85, 255, 85),
          (255, 255, 85), (85, 85, 255), (255, 85, 255), (85, 255, 255), (255, 255, 255)]


# relative humidity along the whole spectrum (the Now screen's humidity line): red for the
# driest air, then orange, yellow, green, cyan and blue, to violet for the most humid
HUMIDITY_STOPS = ((0.0, (255, 54, 54)), (100 / 6, (255, 140, 0)), (200 / 6, (255, 228, 0)),
                  (50.0, (48, 220, 72)), (400 / 6, (0, 212, 232)), (500 / 6, (64, 132, 255)),
                  (100.0, (192, 96, 255)))
# a style for every 5%, rh0 (0%) to rh20 (100%), each made to stand out on the theme's cards
HUMIDITY_STYLES = tuple(f"rh{k}" for k in range(21))


def theme_name(name):
    """A theme name that exists: old names map to their replacement, unknown ones to the default."""
    name = RENAMED.get(name, name)
    return name if name in PALETTES else "weatherstar"


# Settings, Display: Background - the page's color under every theme but the light one
# (its lettering is dark): the theme's own, one of these, any #RRGGBB typed in, or the
# terminal's own (its background, and any transparency, shows through)
BACKGROUNDS = [("theme", "the theme's own"), ("#000000", "black"), ("#06102A", "deep blue"),
               ("#03060E", "blue black"), ("#0A0A10", "ink"), ("#0B0B0F", "near black"),
               ("#101820", "dark slate"), ("#1A1A1A", "charcoal"), ("terminal", "the terminal's own")]
BACKGROUND_LABEL = dict(BACKGROUNDS)


def background_of(value):
    """A Background setting as skydrop keeps it: "theme", "terminal" or "#RRGGBB" (None:
    not one of those)."""
    text = str(value if value is not None else "theme").strip()
    low = text.lower()
    if low in ("", "theme", "the theme's own"):
        return "theme"
    if low in ("terminal", "the terminal's own", "default", "none"):
        return "terminal"
    for key, label in BACKGROUNDS:
        if low == label:
            return key
    digits = text[1:] if text.startswith("#") else text
    if len(digits) == 6 and all(c in "0123456789abcdefABCDEF" for c in digits):
        return "#" + digits.upper()
    return None


def hex_rgb(text):
    return tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))


def full_palette(name, bg=None):
    pal = dict(PALETTES[theme_name(name)])
    if bg is not None:
        pal["bg"] = tuple(bg)
    for role, src in DERIVED.items():
        if role not in pal:
            pal[role] = src if isinstance(src, tuple) else pal[src]
    # cards' edges: a shade brighter than the faint rules, so the boxes read crisply
    pal.setdefault("cardline", blend(pal["faint"], pal["dim"], 0.45))
    # a card's fill: the theme's panel color (a background of your own: a shade off it),
    # so cards stand on the page like the panels of a TV weather display; every other
    # row of a table a touch lighter again
    pal.setdefault("card_bg", blend(pal["bg"], pal["text"], 0.07) if bg is not None else pal["bg2"])
    pal.setdefault("stripe", blend(pal["card_bg"], pal["text"], 0.045))
    return pal


class Theme:
    def __init__(self, name="weatherstar", background=None, terminal_bg=None):
        """`background`: Settings, Display: Background (the theme's own by default);
        `terminal_bg`: the terminal's own background, when it said what it is."""
        self.name = theme_name(name)
        self.light = bool(PALETTES[self.name].get("light"))
        self.background = background_of(background) or "theme"
        # the terminal's own: cells on the page leave the background to the terminal
        # (the renderer sends its default); pictures and blends use its color, if known
        self.default_bg = self.background == "terminal" and not self.light
        bg = None
        if not self.light and self.background == "terminal":
            bg = tuple(terminal_bg) if terminal_bg else (0, 0, 0)
        elif not self.light and self.background.startswith("#"):
            bg = hex_rgb(self.background)
        self.pal = full_palette(self.name, bg)
        self.styles = {}
        for sname, (fg, bgr, attrs, f16, b16, a16) in STYLE_SPECS.items():
            if bg is not None and bgr == "bg":
                b16 = rgb_to_16(bg)          # the 16-color page follows it too
            self.styles[sname] = (self.pal[fg], self.pal[bgr], attrs, f16, b16, a16)
        # relative humidity's colors, every 5%, each made to stand out on this theme's cards
        # and its page
        page16 = self.styles["text"][4]
        for k, sname in enumerate(HUMIDITY_STYLES):
            rgb = humidity_rgb(k * 100.0 / (len(HUMIDITY_STYLES) - 1), self.pal["card_bg"], self.pal["bg"])
            self.styles[sname] = (rgb, self.pal["bg"], "b", rgb_to_16(rgb), page16, "b")

    def flatten(self, flat):
        """CRT mode: a tube's glass has no panels, so cards and table stripes take the page's
        color (and pictures in cards are drawn on it); off again, they get theirs back."""
        if not hasattr(self, "_card"):
            self._card = (self.pal["card_bg"], self.pal["stripe"])
        self.pal["card_bg"], self.pal["stripe"] = (self.pal["bg"], self.pal["bg"]) if flat else self._card

    def rgb(self, role):
        return self.pal.get(role, self.pal["text"])

    def spec(self, style):
        """Resolve a style (name or ("rgb", fg, bg, attrs)) -> (fg, bg, attrs, f16, b16, a16).
        ("onpanel", style) is the style inside a card: what would show the page shows the
        card's fill instead; ("stripe", style) the same on a table's lighter rows."""
        if isinstance(style, str):
            return self.styles.get(style) or self.styles["text"]
        if isinstance(style, tuple) and len(style) == 3 and style[0] == "fade":
            # an area chart's fill: the style's color part of the way from the card's fill
            fg, bg, attrs, f16, b16, a16 = self.spec(style[1])
            return (blend(self.pal["card_bg"], fg, style[2]), bg, attrs, f16, b16, a16)
        if isinstance(style, tuple) and len(style) == 3 and style[0] == "over":
            # a guide drawn across a filled cell: the guide's color on the fill's
            gfg, _bg, gattrs, g16, _b16, ga16 = self.spec(style[1])
            ffg, _bg2, _a, f16, _b, _a2 = self.spec(style[2])
            return (gfg, ffg, gattrs, g16, f16, ga16)
        if isinstance(style, tuple) and len(style) == 2 and style[0] in ("onpanel", "stripe"):
            inner = style[1]
            fill = self.pal["card_bg" if style[0] == "onpanel" else "stripe"]
            if isinstance(inner, tuple) and len(inner) == 2 and inner[0] == "raised":
                face = self.spec(inner[1])[0]
                edge = tuple(round(b + (f - b) * 0.45) for f, b in zip(face, fill))
                return (edge, face, "", None, None, "")
            fg, bg, attrs, f16, b16, a16 = self.spec(inner)
            if bg is None or tuple(bg) == self.pal["bg"] or (style[0] == "stripe" and tuple(bg) == self.pal["card_bg"]):
                bg = fill
            return (fg, bg, attrs, f16, b16, a16)
        if isinstance(style, tuple) and style and style[0] == "rgb":
            fg = style[1] if style[1] is not None else self.pal["text"]
            bg = style[2] if style[2] is not None else self.pal["bg"]
            attrs = style[3] if len(style) > 3 else ""
            return (fg, bg, attrs, None, None, attrs)
        if isinstance(style, tuple) and len(style) == 2 and style[0] == "rev":
            # a meter segment: the cell reversed, so the style's color fills it and the thin
            # corner the character draws takes the background's
            fg, bg, _attrs, f16, b16, _a16 = self.spec(style[1])
            return (fg, bg, "r", f16, b16, "r")
        if isinstance(style, tuple) and len(style) == 2 and style[0] == "raised":
            # a raised meter block: the cell in the style's color, the thin edge the character
            # draws (down its right side, along its foot) a darker shade of it
            face = self.spec(style[1])[0]
            base = self.pal["bg"]
            edge = tuple(round(b + (f - b) * 0.45) for f, b in zip(face, base))
            return (edge, face, "", None, None, "")
        return self.styles["text"]

    def fg_on(self, style, bg_rgb):
        """Same foreground as a named style but drawn over a different background."""
        fg, _bg, attrs = self.spec(style)[:3]
        return ("rgb", fg, bg_rgb, attrs)


# ----------------------------------------------------------------------------
# RGB -> palette index mapping
# ----------------------------------------------------------------------------

_CUBE = [0, 95, 135, 175, 215, 255]
_cache256 = {}
_cache16 = {}


def _cube_index(v):
    if v < 48:
        return 0
    if v < 115:
        return 1
    return min(5, (v - 35) // 40)


def rgb_to_256(rgb):
    hit = _cache256.get(rgb)
    if hit is not None:
        return hit
    r, g, b = rgb
    ri, gi, bi = _cube_index(r), _cube_index(g), _cube_index(b)
    cr, cg, cb = _CUBE[ri], _CUBE[gi], _CUBE[bi]
    cube_idx = 16 + 36 * ri + 6 * gi + bi
    cube_err = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2
    avg = (r + g + b) // 3
    gi2 = max(0, min(23, (avg - 3) // 10))
    gv = 8 + 10 * gi2
    gray_err = (gv - r) ** 2 + (gv - g) ** 2 + (gv - b) ** 2
    idx = 232 + gi2 if gray_err < cube_err else cube_idx
    _cache256[rgb] = idx
    return idx


def rgb_to_16(rgb):
    hit = _cache16.get(rgb)
    if hit is not None:
        return hit
    r, g, b = rgb
    best, besti = None, 0
    for i, (cr, cg, cb) in enumerate(ANSI16):
        d = (cr - r) ** 2 * 3 + (cg - g) ** 2 * 4 + (cb - b) ** 2 * 2
        if best is None or d < best:
            best, besti = d, i
    _cache16[rgb] = besti
    return besti


def luminance(rgb):
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    """WCAG contrast ratio of two colors (1 to 21)."""
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def blend(a, b, t):
    """Linear blend of two RGB tuples."""
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t), int(a[2] + (b[2] - a[2]) * t))


def humidity_rgb(rh, *backs):
    """The color of a relative humidity (%) along the spectrum: red for the driest air, then
    orange, yellow, green, cyan and blue, to violet for the most humid. Given the colors
    it's drawn on (a card, the page), a color that wouldn't stand out from each of them (a
    contrast under 3) is made lighter on dark ones, darker on light ones."""
    rh = max(0.0, min(100.0, float(rh)))
    rgb = HUMIDITY_STOPS[-1][1]
    for (h0, c0), (h1, c1) in zip(HUMIDITY_STOPS, HUMIDITY_STOPS[1:]):
        if rh <= h1:
            rgb = blend(c0, c1, (rh - h0) / (h1 - h0))
            break
    if not backs:
        return rgb
    toward = (0, 0, 0) if luminance(backs[0]) > 0.18 else (255, 255, 255)
    out, t = rgb, 0.0
    while min(contrast(out, b) for b in backs) < 3.0 and t < 1.0:
        t = min(1.0, t + 0.05)
        out = blend(rgb, toward, t)
    return out


def humidity_style(rh):
    """The style for a relative humidity (%): its color along the spectrum, to the nearest 5%."""
    if rh is None:
        return "dim"
    return HUMIDITY_STYLES[int(round(max(0.0, min(100.0, float(rh))) / 5.0))]


# ----------------------------------------------------------------------------
# value -> style helpers
# ----------------------------------------------------------------------------

def temp_style(c):
    if c is None:
        return "dim"
    f = c * 9 / 5 + 32
    for lim, style in ((20, "t_frigid"), (40, "t_cold"), (55, "t_cool"),
                       (72, "t_mild"), (85, "t_warm"), (95, "t_hot")):
        if f < lim:
            return style
    return "t_scorch"


def pop_style(p):
    if p is None:
        return "dim"
    if p >= 30:
        return "rain"
    return "text" if p >= 10 else "dim"


def wind_style(ms):
    if ms is None:
        return "dim"
    if ms >= 13.4:
        return "bad"
    return "warn" if ms >= 8.9 else "text"


def ink_on(rgb):
    """Text that reads on a background of this colour: near-black on light ones, white on
    the rest."""
    r, g, b = rgb[:3]
    return (16, 16, 28) if 0.299 * r + 0.587 * g + 0.114 * b > 150 else (255, 255, 255)


def alert_style(event):
    e = (event or "").lower()
    if "warning" in e or "emergency" in e:
        return "alert"
    if "watch" in e:
        return "watch"
    return "advis"


def temp_rgb(theme, c):
    return theme.spec(temp_style(c))[0]
