"""The radar guide card (press i on the Radar screen): what everything on the
radar means and what every control does."""

from .palettes import PALETTE_DIR
from .paths import pretty_path
from .text import wrap

# (heading, [(term, explanation)]) -- terms may be "" for a plain paragraph
RADAR_GUIDE = [
    ("What you're looking at", [
        ("", "skydrop shows radar two ways. Site radar is one NEXRAD (WSR-88D) radar's own products, "
             "the same Level 3 data the National Weather Service and Supercell Wx use: every elevation, "
             "velocity, dual-polarization fields, storm tracking and more. Mosaic is NOAA's national "
             "composite of all radars: smoother and wider, but with less detail. Switch with m or the "
             "Site radar / Mosaic chips."),
        ("Corner card", "radar ID, product, elevation angle and the time of the frame on screen."),
        ("Timeline", "each square is one scan in the loop; the highlighted one is on screen. 'latest' "
                     "means the newest scan; the loop replays the last few scans so you can see movement."),
        ("Your location", "the orange diamond. The yellow outline is your ZIP code."),
    ]),
    ("The toolbox", [
        ("Chips", "pick one of several: product, elevation, panes, site radar or mosaic. The filled, "
                  "brightly colored chip is the one in use; click another to switch."),
        ("■ □", "switches: the layers, smoothing, Auto and Inspect. ■ is on, □ is off; click to flip."),
        ("Keys", "one-shot actions (drawn as keycaps): ◀ ▶ step to another radar or scan, − + change a number, the "
                      "timeline keys play, pause and jump."),
        ("Auto", "on: the radar nearest the middle of the map is used, and it changes as you pan. Turn it "
                 "off, or step with ◀ ▶, to stay on one radar."),
    ]),
    ("Products (Level 3)", [
        ("REF  Reflectivity", "how much energy bounces back, in dBZ: how heavy the rain, snow or hail is. "
                              "Blue and green are light, yellow moderate, orange and red heavy, magenta "
                              "and white extreme (usually hail). Composite shows the highest value in the "
                              "whole column above each point."),
        ("VEL  Velocity", "wind blowing toward the radar (green) or away from it (red), measured along the "
                          "beam. Bright green right next to bright red is a 'couplet': rotation, the sign "
                          "of a possible tornado. Purple means range folded (the radar couldn't tell)."),
        ("SRM  Storm-relative motion", "velocity with the average storm motion taken out, so rotation "
                                       "inside a moving storm stands out better."),
        ("CC  Correlation coefficient", "how alike the targets in the beam are. Near 1 (red, pink) is "
                                        "uniform rain or snow; 0.8-0.95 is mixed: hail, melting snow. Low "
                                        "values (blue, purple) are non-weather: birds, bugs, smoke, ground "
                                        "clutter, or tornado debris. A low-CC blob on a velocity couplet is "
                                        "a debris signature: a tornado on the ground."),
        ("ZDR  Differential reflectivity", "the shape of what's in the beam. Positive (yellow to red) "
                                           "means flattened big raindrops; near zero is hail, snow or "
                                           "drizzle; very high values are huge drops or insects."),
        ("KDP  Specific differential phase", "how much liquid water is packed in. High KDP (yellow and "
                                             "warmer) means very heavy rain and a flash-flood risk."),
        ("HC  Hydrometeor classification", "the radar's best guess of what it sees: rain, heavy rain, big "
                                           "drops, hail, graupel, wet or dry snow, ice crystals, "
                                           "biological, clutter. Hybrid blends the lowest usable beams."),
        ("VIL  Vertically integrated liquid", "all the water in a storm's column, in kg/m². Above 40-50 "
                                              "points to strong storms and hail."),
        ("ET  Echo tops", "how tall the storms are, in thousands of feet. Tops above 40,000-50,000 ft "
                          "mean strong thunderstorms."),
        ("ACC  Precipitation", "Rate is how hard it's raining right now (inches per hour); 1-hour and "
                               "Storm total are what has already fallen, estimated by the radar."),
    ]),
    ("Elevation (tilts)", [
        ("", "The radar scans at several angles, lowest first: about 0.5°, 0.9°, 1.3°, 1.8°, 2.4° and "
             "3.1°; the scan strategy sets the exact angles and the corner card shows the real one. 0.5° "
             "sees closest to the ground near the radar, but the beam climbs with distance; Inspect "
             "shows how high it is. Higher tilts look into the middle of storms: hail cores and rotation "
             "aloft. VIL, echo tops, rainfall and composite use the whole scan, so they have no tilts. "
             "e goes up, E down, or click an Elevation chip."),
    ]),
    ("Radar status", [
        ("VCP", "the scan strategy number (for example 215 or 35)."),
        ("Precipitation mode", "fast scans for storms. Clear air mode scans slowly and very "
                               "sensitively, so it shows insects, birds and dust on calm days."),
        ("Status dot", "green: newest scan under 5 minutes old; yellow: under 30; red: older (the radar "
                       "may be down for maintenance; try another with n)."),
    ]),
    ("Storm symbols (site radar)", [
        ("○ R6", "a storm cell tracked by the radar's SCIT algorithm, with its ID."),
        ("Gray dots", "where the cell has been (past track)."),
        ("White line •─•", "where it's forecast to be every 15 minutes, up to an hour ahead. 'reaches "
                           "you ~7:52 PM' in the cell list means that track passes within 4 miles of home."),
        ("▲ yellow", "probable severe hail (POSH 50% or more), with the largest expected size."),
        ("△ green", "hail likely, probably not severe."),
        ("◎ yellow ring", "a mesocyclone: a rotating updraft found by the MDA algorithm; thicker is "
                          "stronger."),
        ("▼ red", "a tornado vortex signature (TVS): strong rotation near the ground. Outlined only: an "
                  "elevated TVS, rotation higher up."),
    ]),
    ("Warning boxes", [
        ("Red", "Tornado Warning. Thicker with OBSERVED: a tornado has been confirmed. Pink PDS: "
                "particularly dangerous situation. Purple: TORNADO EMERGENCY."),
        ("Yellow", "Severe Thunderstorm Warning; orange when a tornado is possible or the damage threat "
                   "is destructive."),
        ("Green", "Flash Flood Warning (pink for a flash flood emergency)."),
        ("Others", "orange: special marine warning; violet: snow squall; pink: extreme wind."),
        ("Dashed lines", "SPC watches: red for a tornado watch, gold for a severe thunderstorm watch."),
        ("White arrow", "where the warned storm is headed over the next 15 minutes, from the warning text."),
    ]),
    ("Layers", [
        ("Tracks", "storm cells, tracks, hail, mesocyclone and TVS symbols."),
        ("Warnings", "warning boxes and watch outlines."),
        ("Cities / Temps", "place names; the newest temperature at nearby weather stations."),
        ("Range", "rings every 50 km (31 mi) around the radar, and the edge of this product's coverage."),
        ("Sites", "every NEXRAD on the map; click one to switch to it."),
        ("Markers", "your own places, set in Settings (Map markers)."),
        ("Placefiles", "GRLevelX placefiles (spotters, lightning and more) from the addresses in Settings."),
    ]),
    ("Panes", [
        ("1, 2, 4", "linked maps showing the same place and time. The first pane shows the product you "
                    "pick; the others show the extras in Settings (Radar pane products), reflectivity, "
                    "velocity, correlation coefficient and differential reflectivity by default."),
    ]),
    ("Inspect and hover", [
        ("Click the map", "puts a crosshair there and shows the value, distance and direction from the "
                          "radar, how high the beam is, and where the point is. Or press x and move the "
                          "crosshair with the arrows; x or Esc stops."),
        ("Hover", "the value under the mouse pointer shows as you move it (Settings > Radar > Hover "
                  "readout). Over a placefile icon or line you also get its details."),
        ("Drag", "pans the map. The wheel zooms; a right or middle click recenters."),
    ]),
    ("Keys", [
        ("p P", "next / previous product"),
        ("e E", "elevation up / down"),
        ("n N", "next / previous radar (nearest to the map center first)"),
        ("m", "site radar or national mosaic"),
        ("v", "1, 2 or 4 panes"),
        ("x", "inspect crosshair (arrows move it)"),
        ("space , . < >", "play or pause, step back, step forward, first, latest"),
        ("+ -", "zoom in and out"),
        ("arrows", "pan the map"),
        ("c", "center on home and let the radar follow the map again"),
        ("a o", "storm symbols, smoothing"),
        ("l t w", "city names, temperatures, warning boxes"),
        ("d D", "range rings, radar sites on the map"),
        ("M F", "your markers, placefiles"),
        ("y b", "mosaic layer, map background"),
        ("s", "hear a summary of the radar"),
        ("i", "this guide (i or Esc closes it)"),
    ]),
    ("Your own colors", [
        ("", "Every product uses a GRLevelX-style color table (.pal), the format Supercell Wx and GRLevel3 "
             "use. To change one, put a file named ref.pal, vel.pal, cc.pal, zdr.pal, kdp.pal, hc.pal, "
             "et.pal, vil.pal, rate.pal or precip.pal in " + pretty_path(PALETTE_DIR) + "."),
    ]),
    ("Where the data comes from", [
        ("Radar products", "NOAA's open NEXRAD Level 3 archive on Amazon Web Services, with the NWS tgftp "
                           "server for the newest storm-tracking products."),
        ("Storm details", "the Iowa Environmental Mesonet (cell attributes, warning boxes, storm reports)."),
        ("Radar list and alerts", "the National Weather Service API."),
    ]),
]


def guide_lines(width, ascii_only=False):
    """The guide as styled lines wrapped to width."""
    out = []
    term_w = max(14, min(24, width // 4))
    for heading, rows in RADAR_GUIDE:
        if out:
            out.append([])
        out.append([(heading.upper(), "paneltitle")])
        for term, text in rows:
            if ascii_only:
                term = (term.replace("○", "o").replace("▲", "^").replace("△", "^").replace("◎", "O")
                        .replace("▼", "V").replace("•─•", "*-*"))
            if not term:
                out += [[(ln[0][0], "panel")] for ln in wrap(text, width, "panel", 0)]
                continue
            if len(term) > term_w:
                out.append([(term, "panellabel")])
                for ln in wrap(text, width - 4, "panel", 0):
                    out.append([("    ", "panel")] + [(t, "panel") for t, _s in ln])
                continue
            lines = wrap(text, width - term_w - 1, "panel", 0)
            for k, ln in enumerate(lines or [[("", "panel")]]):
                head = (term if k == 0 else "").ljust(term_w)[:term_w]
                out.append([(head + " ", "panellabel" if k == 0 else "panel")] + [(t, "panel") for t, _s in ln])
    return out
