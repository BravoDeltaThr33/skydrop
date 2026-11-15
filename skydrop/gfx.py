"""Image helpers: Pillow compatibility, kitty graphics and sixel encoders,
and converters that turn pictures into half-block text for terminals
without pixel graphics.
"""

import base64
import io
import os
import re
import struct
import zlib

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops, ImageStat  # noqa: F401
    HAVE_PIL = True
except Exception:  # pragma: no cover - Pillow missing
    Image = ImageDraw = ImageFont = ImageFilter = ImageChops = ImageStat = None
    HAVE_PIL = False


def _resample(name):
    if not HAVE_PIL:
        return None
    enum = getattr(Image, "Resampling", None)
    if enum is not None:
        return getattr(enum, name)
    return getattr(Image, name)


def _transform(name):
    if not HAVE_PIL:
        return None
    enum = getattr(Image, "Transform", None)
    if enum is not None and hasattr(enum, name):
        return getattr(enum, name)
    return getattr(Image, name)


LANCZOS = _resample("LANCZOS")
MESH = _transform("MESH")
AFFINE = _transform("AFFINE")
BICUBIC = _resample("BICUBIC")
BILINEAR = _resample("BILINEAR")
NEAREST = _resample("NEAREST")
BOX = _resample("BOX")

# The labels in pictures are set in DejaVu Sans: the copy that comes with skydrop (in
# skydrop/fonts, so they look the same on every system), else the system's.
BUNDLED_FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_FONT_DIRS = [BUNDLED_FONTS,
              "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/dejavu-sans-fonts",
              "/usr/share/fonts/TTF", "/usr/share/fonts/truetype/noto", "/usr/share/fonts/noto",
              "/usr/share/fonts/google-noto", "/usr/share/fonts/truetype/liberation",
              "/usr/share/fonts/liberation-sans", "/usr/share/fonts/liberation-sans-fonts",
              "/usr/share/fonts/truetype/freefont", "/usr/share/fonts/gnu-free",
              "/usr/local/share/fonts", os.path.expanduser("~/.local/share/fonts"),
              os.path.expanduser("~/.fonts")]
try:
    from .osdeps import LINUX as _LINUX, font_dirs as _font_dirs
    if not _LINUX:
        _FONT_DIRS += [d for d in _font_dirs() if d not in _FONT_DIRS]
except ImportError:
    pass
_FONT_FILES = {
    (True, False): ["DejaVuSans-Bold.ttf", "NotoSans-Bold.ttf", "LiberationSans-Bold.ttf", "FreeSansBold.ttf",
                    "Arial Bold.ttf", "arialbd.ttf"],
    (False, False): ["DejaVuSans.ttf", "NotoSans-Regular.ttf", "LiberationSans-Regular.ttf", "FreeSans.ttf",
                     "Arial.ttf", "arial.ttf"],
    (True, True): ["DejaVuSansCondensed-Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSansNarrow-Bold.ttf",
                   "NotoSans-Bold.ttf", "LiberationSans-Bold.ttf", "FreeSansBold.ttf", "Arial Narrow Bold.ttf",
                   "Arial Bold.ttf", "arialbd.ttf"],
    (False, True): ["DejaVuSansCondensed.ttf", "DejaVuSans.ttf", "LiberationSansNarrow-Regular.ttf",
                    "NotoSans-Regular.ttf", "LiberationSans-Regular.ttf", "FreeSans.ttf", "Arial Narrow.ttf",
                    "Arial.ttf", "arial.ttf"],
}
_font_cache = {}
_font_path = {}


def _find_font(style):
    """Path of the best font file for (bold, condensed), searched once."""
    if style in _font_path:
        return _font_path[style]
    found = None
    for name in _FONT_FILES[style]:
        for d in _FONT_DIRS:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                found = p
                break
        if found:
            break
    if found is None:
        # Pillow also looks in the usual XDG font folders by bare file name.
        for name in _FONT_FILES[style]:
            try:
                ImageFont.truetype(name, 12)
                found = name
                break
            except (OSError, ValueError):
                continue
    _font_path[style] = found
    return found


def font(size, bold=True, condensed=False):
    """A TrueType font if one can be found, else Pillow's built-in font."""
    size = max(6, int(size))
    key = (size, bold, condensed)
    if key in _font_cache:
        return _font_cache[key]
    f = None
    if HAVE_PIL:
        path = _find_font((bold, condensed))
        if path:
            try:
                f = ImageFont.truetype(path, size)
            except (OSError, ValueError):
                f = None
        if f is None:
            try:
                f = ImageFont.load_default(size=size)
            except (TypeError, OSError, ValueError):
                f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def text_width(draw, text, fnt):
    try:
        return draw.textlength(text, font=fnt)
    except AttributeError:  # very old Pillow
        return draw.textsize(text, font=fnt)[0]


def draw_text(draw, xy, text, fnt, fill, anchor="la", stroke=0, stroke_fill=(0, 0, 0)):
    try:
        draw.text(xy, text, font=fnt, fill=fill, anchor=anchor, stroke_width=stroke, stroke_fill=stroke_fill)
    except (TypeError, ValueError):
        x, y = xy
        w = text_width(draw, text, fnt)
        if anchor[0] == "m":
            x -= w / 2
        elif anchor[0] == "r":
            x -= w
        draw.text((x, y - (fnt.size if hasattr(fnt, "size") else 10)), text, font=fnt, fill=fill)


# ----------------------------------------------------------------------------
# painting helpers: gradients, masks, soft shadows and glows (RGBA layers)
# ----------------------------------------------------------------------------

def vgrad(W, H, top, bottom, y0=0, y1=None):
    """W x H RGBA, `top` color at row y0 shading to `bottom` at row y1 (held beyond both)."""
    y1 = H if y1 is None else y1
    span = max(1.0, y1 - y0)
    col = Image.new("RGBA", (1, H))
    col.putdata([tuple(int(round(a + (b - a) * max(0.0, min(1.0, (y - y0) / span)))) for a, b in zip(top, bottom))
                 + (255,) for y in range(H)])
    return col.resize((W, H), NEAREST)


def radial_mask(W, H, gx, gy, R):
    """An L image: 0 at (gx, gy), rising to 255 at distance R and beyond."""
    size = max(2, int(2 * R))
    g = Image.radial_gradient("L").resize((size, size), BILINEAR)
    g = g.point(lambda v: min(255, int(v * 255 / 180.3)))          # Pillow's reaches 255 at its corners
    out = Image.new("L", (W, H), 255)
    out.paste(g, (int(round(gx - size / 2)), int(round(gy - size / 2))))
    return out


def radial(W, H, gx, gy, R, inner, outer):
    """W x H RGBA, `inner` at (gx, gy) shading to `outer` at distance R."""
    return Image.composite(Image.new("RGBA", (W, H), tuple(outer[:3]) + (255,)),
                               Image.new("RGBA", (W, H), tuple(inner[:3]) + (255,)), radial_mask(W, H, gx, gy, R))


def paint(layer, mask, fill):
    """Lay `fill` (an RGBA image, or a color) over `layer` where `mask` is."""
    if not isinstance(fill, Image.Image):
        fill = Image.new("RGBA", layer.size, tuple(fill[:3]) + (255,))
    src = fill.copy()
    src.putalpha(ImageChops.multiply(fill.getchannel("A"), mask))
    layer.alpha_composite(src)


def frame_fade(W, H):
    """255 inside, fading to 0 over the outer edge of the picture: a glow that reaches the
    edge melts away instead of stopping in a straight line."""
    m = max(2, int(min(W, H) * 0.14))
    ramp = Image.linear_gradient("L").resize((1, m))              # 0 at the top
    out = Image.new("L", (W, H), 255)
    edge = ramp.resize((W, m))
    out = ImageChops.multiply(out, _pad(edge, W, H, (0, 0)))
    out = ImageChops.multiply(out, _pad(edge.transpose(Image.FLIP_TOP_BOTTOM), W, H, (0, H - m)))
    side = ramp.rotate(90, expand=True).transpose(Image.FLIP_LEFT_RIGHT).resize((m, H))   # 0 at the left
    out = ImageChops.multiply(out, _pad(side, W, H, (0, 0)))
    out = ImageChops.multiply(out, _pad(side.transpose(Image.FLIP_LEFT_RIGHT), W, H, (W - m, 0)))
    return out


def _pad(part, W, H, at):
    out = Image.new("L", (W, H), 255)
    out.paste(part, at)
    return out


def soft(layer, mask, color, alpha, blur, dx=0, dy=0):
    """A soft shadow or glow: the mask blurred, moved by (dx, dy), in `color` at `alpha`,
    fading out toward the picture's edges."""
    m = mask
    if dx or dy:
        m = Image.new("L", mask.size, 0)
        m.paste(mask, (int(round(dx)), int(round(dy))))
    if blur > 0:
        m = m.filter(ImageFilter.GaussianBlur(blur))
    m = ImageChops.multiply(m, frame_fade(*layer.size))
    glow = Image.new("RGBA", layer.size, tuple(color[:3]) + (255,))
    glow.putalpha(m.point(lambda v: int(v * alpha)))
    layer.alpha_composite(glow)



def png_bytes(img, level=1):
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=level)
    return buf.getvalue()


# ----------------------------------------------------------------------------
# kitty graphics protocol
# ----------------------------------------------------------------------------

def kitty_transmit(image_id, img):
    """Upload an image (kept by the terminal until deleted)."""
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    data = base64.standard_b64encode(png_bytes(img))
    chunks = [data[i:i + 4096] for i in range(0, len(data), 4096)] or [b""]
    out = []
    for k, chunk in enumerate(chunks):
        more = 1 if k < len(chunks) - 1 else 0
        if k == 0:
            out.append(f"\x1b_Ga=t,f=100,t=d,i={image_id},q=2,m={more};")
        else:
            out.append(f"\x1b_Gm={more};")
        out.append(chunk.decode("ascii"))
        out.append("\x1b\\")
    return "".join(out)


def kitty_place(image_id, placement_id, cols, rows):
    return f"\x1b_Ga=p,i={image_id},p={placement_id},c={cols},r={rows},C=1,q=2\x1b\\"


def kitty_delete_placement(image_id, placement_id):
    return f"\x1b_Ga=d,d=i,i={image_id},p={placement_id},q=2\x1b\\"


def kitty_delete_image(image_id):
    return f"\x1b_Ga=d,d=I,i={image_id},q=2\x1b\\"


KITTY_CLEAR_PLACEMENTS = "\x1b_Ga=d,d=a,q=2\x1b\\"
KITTY_CLEAR_ALL = "\x1b_Ga=d,d=A,q=2\x1b\\"


# ----------------------------------------------------------------------------
# sixel
# ----------------------------------------------------------------------------

_RLE = re.compile(rb"(.)\1{3,}")
_LANE_TABLES = {}
_FILL_CACHE = {}


def _lane_table(color, bit):
    key = (color, bit)
    t = _LANE_TABLES.get(key)
    if t is None:
        arr = bytearray(256)
        arr[color] = 1 << bit
        t = bytes(arr)
        _LANE_TABLES[key] = t
    return t


def _fill63(width):
    v = _FILL_CACHE.get(width)
    if v is None:
        v = int.from_bytes(b"\x3f" * width, "big")
        _FILL_CACHE[width] = v
    return v


def sixel_encode(img, colors=255):
    """Encode an RGB image as a sixel string.

    Each band of six pixel rows is built per palette color by summing
    translated rows as big integers (one byte lane per column), which keeps
    the work in C even though this is plain Python."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    try:
        q = img.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    except AttributeError:
        q = img.quantize(colors=colors, method=0)
    pal = q.getpalette() or []
    # Keep the background exact: the corner pixel's palette entry gets its true color,
    # so a picture's edges melt into the page instead of showing a faint box.
    ci = q.getpixel((0, 0))
    if isinstance(ci, int) and len(pal) >= ci * 3 + 3:
        pal[ci * 3:ci * 3 + 3] = list(img.getpixel((0, 0)))[:3]
    data = q.tobytes()
    used = sorted(set(data))
    out = [f'\x1bP0;1;0q"1;1;{w};{h}']
    for c in used:
        r, g, b = pal[c * 3:c * 3 + 3] if len(pal) >= c * 3 + 3 else (0, 0, 0)
        out.append(f"#{c};2;{round(r * 100 / 255)};{round(g * 100 / 255)};{round(b * 100 / 255)}")
    fill = _fill63(w)
    for y0 in range(0, h, 6):
        rows = [data[(y0 + k) * w:(y0 + k + 1) * w] for k in range(6) if y0 + k < h]
        present = set()
        for row in rows:
            present.update(set(row))
        first = True
        for c in sorted(present):
            acc = fill
            for k, row in enumerate(rows):
                acc += int.from_bytes(row.translate(_lane_table(c, k)), "big")
            s = acc.to_bytes(w, "big").rstrip(b"?")
            if not s:
                continue
            s = _RLE.sub(lambda m: b"!%d%s" % (len(m.group(0)), m.group(1)), s)
            out.append(("" if first else "$") + f"#{c}" + s.decode("ascii"))
            first = False
        out.append("-")
    out.append("\x1b\\")
    return "".join(out)


# ----------------------------------------------------------------------------
# text renderings of pictures
# ----------------------------------------------------------------------------

def halfblock_lines(img, cols=None, rows=None):
    """Picture -> lines of '▀' cells (top pixel = fg, bottom pixel = bg)."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    if cols and rows and img.size != (cols, rows * 2):
        img = img.resize((cols, rows * 2), LANCZOS)
    w, h = img.size
    px = img.load()
    out = []
    for r in range(h // 2):
        line = []
        for c in range(w):
            top, bot = px[c, 2 * r], px[c, 2 * r + 1]
            if top == bot:
                seg = (" ", ("rgb", top, top, ""))
            else:
                seg = ("▀", ("rgb", top, bot, ""))
            if line and line[-1][1] == seg[1]:
                line[-1] = (line[-1][0] + seg[0], seg[1])
            else:
                line.append(seg)
        out.append(line)
    return out


_ASCII_RAMP = " .:-=+*#%@"


def ascii_lines(img, cols, rows, colored=True):
    """Picture -> brightness ramp characters, optionally colored."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    img = img.resize((cols, rows), BOX or LANCZOS)
    px = img.load()
    out = []
    for r in range(rows):
        line = []
        for c in range(cols):
            rgb = px[c, r]
            lum = (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 255000
            ch = _ASCII_RAMP[min(len(_ASCII_RAMP) - 1, int(lum * len(_ASCII_RAMP)))]
            st = ("rgb", rgb, None, "") if colored else "text"
            if line and line[-1][1] == st:
                line[-1] = (line[-1][0] + ch, st)
            else:
                line.append((ch, st))
        out.append(line)
    return out


# ----------------------------------------------------------------------------
# tiny PNG decoder (used only when Pillow is missing)
# ----------------------------------------------------------------------------

def decode_png(data):
    """8-bit, non-interlaced PNG -> (width, height, list of RGBA rows)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, pal, trns = 8, [], None, None
    w = h = depth = ctype = interlace = 0
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            w, h, depth, ctype, _c, _f, interlace = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"PLTE":
            pal = [tuple(chunk[i:i + 3]) for i in range(0, len(chunk), 3)]
        elif kind == b"tRNS":
            trns = chunk
        elif kind == b"IDAT":
            idat.append(chunk)
        elif kind == b"IEND":
            break
    if depth != 8 or interlace:
        raise ValueError("unsupported PNG")
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(b"".join(idat))
    stride = w * bpp
    prev = bytearray(stride)
    rows = []
    p = 0
    for _y in range(h):
        ft = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if pa <= pb and pa <= pc else b if pb <= pc else c
                line[i] = (line[i] + pr) & 255
        prev = line
        if ctype == 6:
            rows.append([tuple(line[i:i + 4]) for i in range(0, stride, 4)])
        elif ctype == 2:
            rows.append([tuple(line[i:i + 3]) + (255,) for i in range(0, stride, 3)])
        elif ctype == 3:
            tr = trns or b""
            rows.append([pal[v] + ((tr[v] if v < len(tr) else 255),) for v in line])
        elif ctype == 0:
            rows.append([(v, v, v, 255) for v in line])
        elif ctype == 4:
            rows.append([(line[i], line[i], line[i], line[i + 1]) for i in range(0, stride, 2)])
    return w, h, rows
