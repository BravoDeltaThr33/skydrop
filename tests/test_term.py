import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skydrop import term as T
from skydrop.canvas import Canvas, Renderer, ImageRegistry
from skydrop.theme import Theme
import pyte

class FakeTerm:
    def __init__(self): self.out=[]
    def write(self, s): self.out.append(s)
    def flush(self): pass

import codecs, time

def new_term():
    t = T.Terminal.__new__(T.Terminal)
    t.buf = b""; t.paste=None; t.paste_at=0; t.responses=[]; t.esc_at=0
    t._decoder = codecs.getincrementaldecoder("utf-8")("replace")
    return t

def parse(data):
    t = new_term()
    t.buf = data
    ev = t._parse(final=True)
    return ev, t.responses

def test_split_reads():
    # a paste end marker split across two reads must still end the paste
    t = new_term()
    t.buf = b"\x1b[200~hello \xc3"
    assert t._parse(final=False) == []
    t.buf += b"\xa9 world\x1b[20"
    assert t._parse(final=False) == []
    t.buf += b"1~q"
    ev = t._parse(final=True)
    assert isinstance(ev[0], T.Paste) and ev[0].text == "hello \u00e9 world" and ev[1] == "q", ev
    # an escape sequence that arrives in pieces isn't turned into keystrokes
    t = new_term(); t.esc_at = time.time()
    t.buf = b"\x1b[<0;10;"
    assert t._parse(final=True) == [] and t.buf == b"\x1b[<0;10;"
    t.buf += b"5M"
    ev = t._parse(final=True)
    assert len(ev) == 1 and ev[0].kind == "press", ev
    # Alt+] (ESC ]) with nothing after it doesn't swallow later typing
    t = new_term(); t.esc_at = time.time() - 1
    t.buf = b"\x1b]ab"
    assert t._parse(final=True) == ["]", "a", "b"] or t._parse(final=True) == ["a", "b"]
    # sideways scrolling is ignored rather than read as scroll-down
    ev, _ = parse(b"\x1b[<66;3;4M\x1b[<67;3;4M")
    assert ev == [], ev

def test_parse():
    ev,_ = parse(b"\x1b[A\x1b[B\x1bOC\x1b[5~\x1b[6~\x1b[3~\x1b[Z q\r\x7f\x03")
    assert ev == [T.KEY_UP, T.KEY_DOWN, T.KEY_RIGHT, T.KEY_PPAGE, T.KEY_NPAGE, T.KEY_DC, T.KEY_BTAB, " ", "q", "\n", T.KEY_BACKSPACE, "\x03"], ev
    ev,_ = parse(b"\x1b")
    assert ev == ["\x1b"], ev
    ev,_ = parse("hé☃".encode())
    assert ev == ["h","é","☃"], ev
    ev,_ = parse(b"\x1b[<0;10;5M\x1b[<0;10;5m\x1b[<64;3;4M\x1b[<65;3;4M")
    assert [ (e.kind,e.x,e.y) for e in ev] == [("press",9,4),("release",9,4),("wheel_up",2,3),("wheel_down",2,3)], ev
    ev,_ = parse(b"\x1b[200~paste me\x1b[201~x")
    assert isinstance(ev[0], T.Paste) and ev[0].text=="paste me" and ev[1]=="x", ev
    ev,resp = parse(b"\x1b_Gi=31;OK\x1b\\\x1b[?62;4;22c\x1bP>|kitty(0.35)\x1b\\k")
    assert ev == ["k"], ev
    assert len(resp)==3, resp
    ev,_ = parse(b"\x1b[1;5A\x1b[H\x1b[F\x1b[15~")
    assert ev == [T.KEY_UP, T.KEY_HOME, T.KEY_END, T.KEY_F[5]], ev
    # Shift (or Ctrl, Alt) with left and right arrows: their own keys (Settings moves columns)
    ev,_ = parse(b"\x1b[1;2C\x1b[1;2D\x1b[1;5C\x1b[C")
    assert ev == [T.KEY_SRIGHT, T.KEY_SLEFT, T.KEY_SRIGHT, T.KEY_RIGHT], ev
    print("parse ok")

def test_render():
    th = Theme()
    ft = FakeTerm()
    r = Renderer(ft, th, "truecolor", "blocks")
    reg = ImageRegistry()
    c = Canvas(40, 6)
    c.put(0, 0, [("Hello ", "title"), ("World", "value")])
    c.put(2, 5, [("°F ▀▄█ ⠿", "text")])
    r.present(c, reg)
    s = "".join(ft.out)
    scr = pyte.Screen(40, 6); st = pyte.Stream(scr); st.feed(s)
    assert scr.display[0].startswith("Hello World"), scr.display
    assert scr.display[2][5:13] == "°F ▀▄█ ⠿", repr(scr.display[2])
    # diff: change one word
    ft.out.clear()
    c2 = Canvas(40, 6)
    c2.put(0, 0, [("Hello ", "title"), ("Earth", "value")])
    c2.put(2, 5, [("°F ▀▄█ ⠿", "text")])
    r.present(c2, reg)
    s2 = "".join(ft.out)
    st.feed(s2)
    assert scr.display[0].startswith("Hello Earth"), scr.display
    assert len(s2) < len(s) / 3, (len(s2), len(s))
    # 256 and 16 color modes produce SGR
    for mode in ("256", "16"):
        ft.out.clear(); r.configure(color_mode=mode); r.present(c2, reg)
        out="".join(ft.out); assert "\x1b[" in out
    print("render ok", len(s), len(s2))

if __name__ == "__main__":
    test_parse(); test_render()
