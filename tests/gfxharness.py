"""A pyte terminal that also understands the kitty graphics protocol and sixel,
so screenshots show the pictures the app sends."""

import base64
import io
import os
import re
import sys
import threading
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from PIL import Image  # noqa: E402

from harness import FakeTerm  # noqa: E402

SEQ = re.compile(r"\x1b_G[^\x1b]*\x1b\\|\x1bP[^\x1b]*\x1b\\")


def sixel_decode(data):
    """Decode a sixel body (after the 'q') into an RGB image."""
    w = h = 0
    m = re.match(r'"(\d+);(\d+);(\d+);(\d+)', data)
    if m:
        w, h = int(m.group(3)), int(m.group(4))
        data = data[m.end():]
    img = Image.new("RGB", (max(1, w), max(1, h)), (255, 0, 255))
    P = img.load()
    pal = {}
    x = y = 0
    color = 0
    i, n = 0, len(data)
    num = re.compile(r"[0-9;]*")
    while i < n:
        c = data[i]
        if c == "#":
            mm = num.match(data, i + 1)
            parts = [int(p) if p else 0 for p in mm.group(0).split(";")]
            i = mm.end()
            if len(parts) >= 5 and parts[1] == 2:
                pal[parts[0]] = tuple(int(round(v * 255 / 100)) for v in parts[2:5])
            color = parts[0]
            continue
        rep = 1
        if c == "!":
            mm = re.compile(r"\d+").match(data, i + 1)
            rep = int(mm.group(0))
            i = mm.end()
            c = data[i]
        if c == "$":
            x = 0
        elif c == "-":
            x = 0
            y += 6
        elif "?" <= c <= "~":
            bits = ord(c) - 63
            col = pal.get(color, (255, 255, 255))
            if bits:
                for k in range(6):
                    if bits >> k & 1 and y + k < h:
                        for xx in range(x, min(w, x + rep)):
                            P[xx, y + k] = col
            x += rep
        i += 1
    return img


class GfxTerm(FakeTerm):
    def __init__(self, cols, rows, mode="kitty", cell=(10, 20), ghostty=True):
        super().__init__(cols, rows, True)
        self.mode = mode
        # Ghostty (like the kitty spec allows) throws away every image, data included,
        # when the screen is erased with ESC[2J; model that so stale re-placements show up.
        self.ghostty = ghostty
        self.caps.kitty = mode == "kitty"
        self.caps.sixel = mode == "sixel"
        self.caps.name = f"pyte + {mode} test terminal"
        self.cell = cell
        self.k_images = {}
        self.k_frames = {}
        self.k_place = {}
        self.k_pending = None
        self.sixels = {}          # (y, x) -> sixel body (latest)
        self.stats = {"apc": 0, "dcs": 0, "bytes": 0, "transmits": 0, "places": 0, "deletes": 0}
        self.problems = []
        self.glock = threading.RLock()

    def cell_px(self):
        return self.cell

    def flush(self):
        with self.glock:
            self._flush()

    def _flush(self):
        data = "".join(self.out)
        self.out = []
        self.stats["bytes"] += len(data)
        if data:
            self.stats["writes"] = self.stats.get("writes", 0) + 1
        pos = 0
        for m in SEQ.finditer(data):
            if m.start() > pos:
                self._plain(data[pos:m.start()])
            seq = m.group(0)
            if seq.startswith("\x1b_G"):
                self.stats["apc"] += 1
                self._apc(seq[3:-2])
            else:
                self.stats["dcs"] += 1
                self._dcs(seq[2:-2])
            pos = m.end()
        if pos < len(data):
            self._plain(data[pos:])

    def _plain(self, text):
        if self.ghostty and "\x1b[2J" in text:
            head, _, tail = text.rpartition("\x1b[2J")
            self.stream.feed(head + "\x1b[2J")
            self.k_images.clear()
            self.k_place.clear()
            self.stats["ed2_wipes"] = self.stats.get("ed2_wipes", 0) + 1
            text = tail
        self.stream.feed(text)

    def _apc(self, body):
        ctrl, _, payload = body.partition(";")
        kv = dict(p.split("=", 1) for p in ctrl.split(",") if "=" in p)
        a = kv.get("a")
        if a in ("t", "T", "f") or (a is None and self.k_pending is not None):
            if a in ("t", "T", "f"):
                if kv.get("f", "32") not in ("100", "32", "24"):
                    self.problems.append(f"unexpected format {kv.get('f')}")
                self.k_pending = (int(kv["i"]), a, kv, [payload])
            else:
                self.k_pending[3].append(payload)
            if kv.get("m", "0") == "0":
                iid, kind, head, chunks = self.k_pending
                self.k_pending = None
                try:
                    data = base64.b64decode("".join(chunks))
                    fmt = head.get("f", "32")
                    if fmt == "100":
                        pic = Image.open(io.BytesIO(data)).convert("RGBA")
                    else:
                        if head.get("o") == "z":
                            data = zlib.decompress(data)
                        size = (int(head["s"]), int(head["v"]))
                        pic = Image.frombytes("RGBA" if fmt == "32" else "RGB", size, data).convert("RGBA")
                    if kind == "f":
                        if iid not in self.k_frames:
                            self.problems.append(f"a frame for unknown image {iid}")
                        else:
                            self.k_frames[iid].append(pic)
                    else:
                        self.k_images[iid] = pic
                        self.k_frames[iid] = [pic]
                    self.stats["transmits"] += 1
                except Exception as e:
                    self.problems.append(f"bad picture data for image {iid}: {e}")
        elif a == "a":
            iid = int(kv["i"])
            frames = self.k_frames.get(iid)
            if not frames:
                self.problems.append(f"animation of unknown image {iid}")
            elif "c" in kv:
                n = int(kv["c"])
                if not 1 <= n <= len(frames):
                    self.problems.append(f"image {iid} has no frame {n}")
                else:
                    self.k_images[iid] = frames[n - 1]
                    self.stats["frames"] = self.stats.get("frames", 0) + 1
        elif a == "p":
            iid, pid = int(kv["i"]), int(kv.get("p", 0))
            if iid not in self.k_images:
                self.problems.append(f"placement of unknown image {iid}")
            crop = tuple(int(kv[k]) if k in kv else None for k in ("x", "y", "w", "h")) \
                if any(k in kv for k in ("x", "y", "w", "h")) else None
            self.k_place[(iid, pid)] = (self.screen.cursor.y, self.screen.cursor.x, int(kv.get("c", 0)),
                                        int(kv.get("r", 0)), int(kv.get("z", 0)), crop, int(kv.get("Y", 0)),
                                        int(kv.get("X", 0)))
            self.stats["places"] += 1
        elif a == "d":
            self.stats["deletes"] += 1
            d = kv.get("d", "a")
            if d in ("a", "A"):
                self.k_place.clear()
                if d == "A":
                    self.k_images.clear()
                    self.k_frames.clear()
            elif d in ("i", "I"):
                iid = int(kv["i"])
                pid = int(kv["p"]) if "p" in kv else None
                for key in list(self.k_place):
                    if key[0] == iid and (pid is None or key[1] == pid):
                        del self.k_place[key]
                if d == "I":
                    self.k_images.pop(iid, None)
                    self.k_frames.pop(iid, None)
        elif a == "q":
            pass
        else:
            self.problems.append(f"unknown kitty command {ctrl}")

    def _dcs(self, body):
        k = body.find("q")
        if k < 0:
            return
        self.sixels[(self.screen.cursor.y, self.screen.cursor.x)] = body[k + 1:]

    def snapshot(self, path, scale=1):
        with self.glock:
            return self._snapshot(path, scale)

    def _snapshot(self, path, scale=1):
        img = super().snapshot(path, scale)
        cw, ch = 9 * scale, 18 * scale
        if self.mode == "kitty":
            img = img.convert("RGBA")
            tcw, tch = self.cell
            for (iid, _pid), (y, x, c, r, z, crop, yoff, xoff) in sorted(self.k_place.items(),
                                                                       key=lambda kv: kv[1][4]):
                pic = self.k_images.get(iid)
                if pic is None:
                    continue
                if crop:
                    sx, sy, sw, sh = crop
                    sx, sy = sx or 0, sy or 0
                    sw = pic.size[0] - sx if sw is None else sw
                    sh = pic.size[1] - sy if sh is None else sh
                    pic = pic.crop((sx, sy, sx + sw, sy + sh))
                if c and r:
                    size = (c * cw, r * ch)
                else:                       # its own size in pixels, cells as wide as the terminal's
                    size = (max(1, round(pic.size[0] * cw / tcw)), max(1, round(pic.size[1] * ch / tch)))
                layer = pic.resize(size, Image.BILINEAR if z > 0 else Image.BICUBIC)
                py = y * ch + int(yoff * ch / max(1, tch))
                if z > 0:
                    px = x * cw + int(xoff * cw / max(1, tcw))
                    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
                    over.paste(layer, (px, py))
                    img.alpha_composite(over)
                else:
                    img.paste(layer.convert("RGB"), (x * cw, py))
            img = img.convert("RGB")
        elif self.mode == "sixel":
            shown = getattr(getattr(self.app, "renderer", None), "sixel", None)
            for slot, (key, y, x, cols, rows, w, h, *_rest) in (shown.shown.items() if shown else []):
                body = self.sixels.get((y, x))
                if body is None:
                    self.problems.append(f"no sixel drawn for {slot} at {y},{x}")
                    continue
                pic = sixel_decode(body)
                img.paste(pic.resize((cols * cw, rows * ch)), (x * cw, y * ch))
        img.save(path)
        return img
