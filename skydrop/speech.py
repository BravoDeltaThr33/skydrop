"""Read forecasts aloud.

Engines, best first: Piper (natural offline neural voices, used in-process
when the piper-tts package is importable), then the system's own: on Linux
pico2wave, espeak-ng, festival or spd-say; on macOS `say`; on Windows its
built-in voices (SAPI, through PowerShell). Audio plays through pw-play,
paplay, aplay or ffplay on Linux, afplay on macOS and Windows' own player
(winsound) on Windows. Text is spoken one sentence at a time so the screen can
show and highlight the sentence being read.
"""

import math
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import wave

from . import sources as S
from .net import USER_AGENT
from .config import intval
from .errlog import LOG
from .osdeps import MACOS, WINDOWS, background, powershell_command
from .paths import TTS_DIR, VOICES_DIR, ensure

if WINDOWS:
    ENGINES = ("piper", "sapi", "espeak")
    PLAYERS = ("winsound", "ffplay")
elif MACOS:
    ENGINES = ("piper", "say", "espeak")
    PLAYERS = ("afplay", "ffplay")
else:
    ENGINES = ("piper", "pico", "espeak", "festival", "spd")
    PLAYERS = ("pw-play", "paplay", "aplay", "ffplay")
ENGINE_CHOICES = ("auto",) + ENGINES + ("off",)
PLAYER_CHOICES = ("auto",) + PLAYERS
ENGINE_NAMES = {"piper": "Piper", "pico": "SVOX Pico", "espeak": "eSpeak NG", "festival": "Festival",
                "spd": "speech-dispatcher", "say": "macOS voice", "sapi": "Windows voice"}

# Windows: play a WAV file with the standard library's winsound, in a process of its own
# so it can be stopped like any other player.
_WINSOUND = "import sys, winsound; winsound.PlaySound(sys.argv[1], winsound.SND_FILENAME)"

# Windows: speak into a WAV file with the built-in voices (System.Speech); the text, file
# and rate arrive in environment variables, so nothing needs quoting.
_SAPI = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'en-*' } | Select-Object -First 1
if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }
$s.Rate = [int]$env:SKYDROP_RATE
$s.SetOutputToWaveFile($env:SKYDROP_WAV)
$s.Speak($env:SKYDROP_TEXT)
$s.Dispose()
"""


def engines_help():
    """What Settings says about the speech engines here."""
    if WINDOWS:
        return "auto uses Piper when its voice is downloaded, then Windows' own voice (sapi) or espeak-ng."
    if MACOS:
        return "auto uses Piper when its voice is downloaded, then the macOS voice (say) or espeak-ng."
    return "auto uses Piper when its voice is downloaded, then pico2wave, espeak-ng, festival or speech-dispatcher."


def no_player_hint():
    if WINDOWS:
        return "No audio player found (Windows' own player should always be there; try Audio player: auto)."
    if MACOS:
        return "No audio player found (macOS's afplay should always be there; try Audio player: auto)."
    return "No audio player found: install alsa-utils (aplay) or PipeWire (pw-play)."

VOICE_CHOICES = ["en_US-ryan-medium", "en_US-lessac-medium", "en_US-amy-medium", "en_US-joe-medium",
                 "en_US-norman-medium", "en_US-john-medium", "en_US-kristin-medium", "en_US-hfc_male-medium",
                 "en_US-hfc_female-medium", "en_US-ryan-high", "en_US-lessac-high", "en_GB-alan-medium"]


_PIPER = None


def _piper_available():
    """Is the piper-tts package installed? (checked without importing onnxruntime)"""
    global _PIPER
    if _PIPER is None:
        try:
            import importlib.util
            _PIPER = importlib.util.find_spec("piper") is not None
        except (ImportError, ValueError):
            _PIPER = False
    return _PIPER


def _clean_old_audio(max_age=86400):
    """Remove spoken-sentence files left behind (by a crash, or at exit)."""
    try:
        now = time.time()
        for f in os.listdir(TTS_DIR):
            if f.startswith(("u", "s")) and f.endswith(".wav"):
                p = os.path.join(TTS_DIR, f)
                mine = f.startswith(f"u{os.getpid()}-")
                if (max_age == 0 and mine) or now - os.path.getmtime(p) > max(max_age, 60):
                    os.remove(p)
    except OSError:
        pass


def voice_path(name):
    return os.path.join(VOICES_DIR, f"{name}.onnx")


def voice_url(cfg, name):
    lang, speaker, quality = name.split("-", 2)
    family = lang.split("_")[0]
    return f"{S.url(cfg, 'piper_voices')}/{family}/{lang}/{speaker}/{quality}/{name}.onnx"


def _have_player(name):
    if name == "winsound":
        return WINDOWS
    return bool(shutil.which(name))


def find_player(pref="auto"):
    if pref and pref != "auto" and _have_player(pref):
        return pref
    for p in PLAYERS:
        if _have_player(p):
            return p
    return None


def player_cmd(player, path):
    if player == "winsound":
        if getattr(sys, "frozen", False):     # skydrop.exe plays it itself (it takes no -c)
            return [sys.executable, "--play-wav", path]
        return [sys.executable, "-c", _WINSOUND, path]
    if player == "afplay":
        return ["afplay", path]
    if player == "pw-play":
        return ["pw-play", path]
    if player == "paplay":
        return ["paplay", path]
    if player == "aplay":
        return ["aplay", "-q", path]
    if player == "ffplay":
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]
    return [player, path]


_STATUS = [0.0, None]


def engine_status():
    """{engine: available?} (looked up at most every 15 seconds)."""
    if _STATUS[1] is not None and time.time() - _STATUS[0] < 15:
        return _STATUS[1]
    _STATUS[0], _STATUS[1] = time.time(), _engine_status()
    return _STATUS[1]


def _engine_status():
    st = {"piper": _piper_available(), "espeak": bool(shutil.which("espeak-ng") or shutil.which("espeak"))}
    if WINDOWS:
        st["sapi"] = bool(shutil.which("powershell") or shutil.which("pwsh"))
    elif MACOS:
        st["say"] = bool(shutil.which("say"))
    else:
        st.update(pico=bool(shutil.which("pico2wave")), festival=bool(shutil.which("text2wave")),
                  spd=bool(shutil.which("spd-say")))
    return st


# ----------------------------------------------------------------------------
# text clean-up for speech
# ----------------------------------------------------------------------------

_DIRS = {"N": "north", "NNE": "north northeast", "NE": "northeast", "ENE": "east northeast", "E": "east",
         "ESE": "east southeast", "SE": "southeast", "SSE": "south southeast", "S": "south",
         "SSW": "south southwest", "SW": "southwest", "WSW": "west southwest", "W": "west",
         "WNW": "west northwest", "NW": "northwest", "NNW": "north northwest"}
_WEEKDAYS = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday", "Fri": "Friday",
             "Sat": "Saturday", "Sun": "Sunday"}
_ABBREV = re.compile(r"\b(St|Mt|Ft|Dr|Mr|Mrs|Ms|Jr|Sr|No|vs|approx|Co|Twp|Hwy|Ave|Blvd|Rd|U\.S|a\.m|p\.m|e\.g|i\.e)\.\s")
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]


def _ordinal(n):
    from .util import ordinal
    return ordinal(n)


def normalize(text):
    s = str(text or "")
    s = s.replace("…", ". ").replace("—", ", ").replace("–", " to ")
    s = re.sub(r"[▲▸★✓•◆▌━─│■▪❚▶⚠]", " ", s)
    s = re.sub(r"(-?\d+)\s*°\s*F\b", r"\1 degrees", s)
    s = re.sub(r"(-?\d+)\s*°\s*C\b", r"\1 degrees Celsius", s)
    s = re.sub(r"(-?\d+)\s*°", r"\1 degrees", s)
    s = re.sub(r"\bmph\b", "miles per hour", s)
    s = re.sub(r"\bkm/h\b", "kilometers per hour", s)
    s = re.sub(r"\binHg\b", "inches of mercury", s)
    s = re.sub(r"\bhPa\b", "hectopascals", s)
    s = re.sub(r"(\d)\s*%", r"\1 percent", s)
    # "0.88 in" and "2 in." are inches; "75 to 80 in the valleys" is not
    s = re.sub(r"(\d\.\d+)\s*in\b|(\d)\s*in\b(?=\s*(?:[,;:)]|$|\.|of\b|per\b|\())",
               lambda m_: (m_.group(1) or m_.group(2)) + " inches", s)
    s = re.sub(r"(\d(?:\.\d+)?)\s*mi\b", r"\1 miles", s)
    s = re.sub(r"(\d(?:\.\d+)?)\s*km\b", r"\1 kilometers", s)
    s = re.sub(r"(\d(?:\.\d+)?)\s*mm\b", r"\1 millimeters", s)
    s = re.sub(r"(\d(?:\.\d+)?)\s*cm\b", r"\1 centimeters", s)
    s = re.sub(r"(\d(?:\.\d+)?)\s*ft\b", r"\1 feet", s)
    s = re.sub(r"(\d(?:\.\d+)?)\s*m\b(?!\.)", r"\1 meters", s)
    s = re.sub(r"\b(N|NNE|NE|ENE|E|ESE|SE|SSE|S|SSW|SW|WSW|W|WNW|NW|NNW)(?=\s+\d)",
               lambda m_: _DIRS[m_.group(1)], s)
    # dates only where the app writes them ("Wed 10/28"); "1/2 inch" stays a fraction
    s = re.sub(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\.?\s+(\d{1,2})/(\d{1,2})\b",
               lambda m_: f"{_WEEKDAYS[m_.group(1)]}, {_MONTHS[int(m_.group(2)) - 1]} {_ordinal(m_.group(3))}"
               if 1 <= int(m_.group(2)) <= 12 and 1 <= int(m_.group(3)) <= 31 else m_.group(0), s)
    s = re.sub(r"\b1/2\b", "one half", s)
    s = re.sub(r"\b1/4\b", "one quarter", s)
    s = re.sub(r"\b3/4\b", "three quarters", s)
    for a, b in (("AQI", "air quality index"), ("NWS", "National Weather Service"), ("SPC", "Storm Prediction Center"),
                 ("WPC", "Weather Prediction Center"), ("UV", "U V"), ("dBZ", "D B Z"), ("CAPE", "cape"),
                 ("ZIP", "zip"), ("CWOP", "citizen weather"), ("ASOS", "airport"), ("IEM", "Iowa Mesonet")):
        s = re.sub(rf"\b{a}\b", b, s)
    s = s.replace("--", " ")
    s = re.sub(r"\s+/\s+", " and ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def split_sentences(text, maxlen=240):
    out = []
    for para in re.split(r"\n\s*\n|\n", str(text or "")):
        para = para.strip()
        if not para:
            continue
        para = _ABBREV.sub(lambda m_: m_.group(1) + ".\x00", para)  # "St. Louis" isn't a sentence end
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", para)
        for p in parts:
            p = p.replace("\x00", " ").strip()
            while len(p) > maxlen:
                cut = max(p.rfind(", ", 0, maxlen), p.rfind("; ", 0, maxlen))
                if cut < 40:
                    cut = p.rfind(" ", 0, maxlen)
                if cut < 20:
                    break
                out.append(p[:cut + 1].strip())
                p = p[cut + 1:].strip()
            if p:
                if out and len(p) < 12 and not out[-1].endswith((".", "!", "?")):
                    out[-1] += " " + p
                else:
                    out.append(p)
    return out


# ----------------------------------------------------------------------------
# sounds
# ----------------------------------------------------------------------------

def make_tone(path, kind="chime"):
    rate = 22050
    frames = []
    if kind == "nwr":
        dur, freq = 2.0, 1050.0
        for i in range(int(rate * dur)):
            env = min(1.0, i / 400.0, (rate * dur - i) / 400.0)
            frames.append(int(0.28 * env * 32767 * math.sin(2 * math.pi * freq * i / rate)))
    else:
        for freq, dur in ((988.0, 0.42), (784.0, 0.62)):
            n = int(rate * dur)
            for i in range(n):
                env = math.exp(-4.0 * i / n) * min(1.0, i / 200.0)
                v = math.sin(2 * math.pi * freq * i / rate) + 0.3 * math.sin(4 * math.pi * freq * i / rate)
                frames.append(int(0.32 * env * 32767 * v / 1.3))
    ensure(os.path.dirname(path))
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack(f"<{len(frames)}h", *frames))
    return path


class _Utterance:
    """One read-aloud job: its own sentences, stop flag, audio cache and files, so a new
    reading can never pick up pieces of an older one that is still winding down."""

    def __init__(self, seq, title, sentences, cfg):
        self.seq, self.title, self.sentences, self.cfg = seq, title, sentences, cfg
        self.stop = threading.Event()
        self.jump = None
        self.index = 0
        self.state = "preparing"
        self.proc = None
        self.cache = {}
        self.spoke = False


class Speaker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.cur = None
        self.error = ""
        self.version = 0
        self._seq = 0
        self._voice = None
        self._voice_name = None
        self._voice_lock = threading.Lock()   # one Piper synthesis at a time
        self._bg = []                         # tone players, reaped as they finish
        self.download = None       # (name, done_bytes, total_bytes, error)
        ensure(TTS_DIR)
        _clean_old_audio()

    # -- state the screens read ----------------------------------------------
    @property
    def state(self):
        u = self.cur
        return u.state if u is not None else "idle"

    @property
    def active(self):
        return self.cur is not None

    @property
    def title(self):
        u = self.cur
        return u.title if u is not None else ""

    @property
    def sentences(self):
        u = self.cur
        return u.sentences if u is not None else []

    @property
    def index(self):
        u = self.cur
        return u.index if u is not None else -1

    def current(self):
        u = self.cur
        if u is not None and 0 <= u.index < len(u.sentences):
            return u.sentences[u.index]
        return ""

    # -- info ---------------------------------------------------------------
    def engine(self, cfg=None):
        cfg = cfg or self.cfg
        want = (cfg.get("voice_engine") or "auto").lower()
        st = engine_status()
        name = self.voice_name(cfg)
        if want == "off":
            return None
        if want != "auto":
            if want == "piper":
                return "piper" if st["piper"] and os.path.exists(voice_path(name)) else None
            return want if st.get(want) else None
        if st["piper"] and os.path.exists(voice_path(name)):
            return "piper"
        for e in ENGINES[1:]:
            if st.get(e):
                return e
        return None

    def voice_name(self, cfg=None):
        return ((cfg or self.cfg).get("voice") or VOICE_CHOICES[0]).strip()

    def player(self, cfg=None):
        return find_player((cfg or self.cfg).get("audio_player") or "auto")

    def describe(self, cfg=None):
        cfg = cfg or self.cfg
        e = self.engine(cfg)
        if e is None:
            want = (cfg.get("voice_engine") or "auto").lower()
            if want == "off":
                return "turned off in Settings"
            if _piper_available() and not os.path.exists(voice_path(self.voice_name(cfg))):
                return f"Piper is installed but voice {self.voice_name(cfg)} isn't downloaded (Settings > Voice)"
            if want not in ("auto", "piper"):
                return f"the {want} speech engine isn't installed"
            return "no speech engine found (install espeak-ng, or Piper with the installer)"
        name = f"Piper ({self.voice_name(cfg)})" if e == "piper" else ENGINE_NAMES.get(e, e)
        p = self.player(cfg)
        if e != "spd":
            name += f" via {p}" if p else " (no audio player found)"
        return name

    # -- control ------------------------------------------------------------
    def speak(self, title, text, overrides=None):
        """Start reading `text` (a string or list of strings). Returns False with
        self.error set when it can't (nothing to say, no engine, no audio player)."""
        self.stop()
        cfg = dict(self.cfg)
        cfg.update(overrides or {})
        if isinstance(text, (list, tuple)):
            text = "\n".join(t for t in text if t)
        sents = [x for x in split_sentences(text) if re.search(r"[A-Za-z0-9]", x)]
        problem = None
        if not sents:
            problem = "nothing to read here"
        elif self.engine(cfg) is None:
            problem = self.describe(cfg)
        elif self.engine(cfg) != "spd" and not self.player(cfg):
            problem = "no audio player found (install pipewire-bin, pulseaudio-utils or alsa-utils)"
        if problem:
            self.error = problem
            self.version += 1
            if sents:
                LOG.report("speech", "Read-aloud", problem)
            return False
        with self.lock:
            self._seq += 1
            utt = _Utterance(self._seq, title, sents, cfg)
            self.cur = utt
            self.error = ""
            self.version += 1
        threading.Thread(target=self._run, args=(utt,), daemon=True).start()
        return True

    def stop(self):
        """Stop at once (the old reading's thread winds down on its own)."""
        with self.lock:
            utt, self.cur = self.cur, None
            self.version += 1
        if utt is not None:
            utt.stop.set()
            self._kill(utt.proc)

    def skip(self, delta):
        utt = self.cur
        if utt is None:
            return
        utt.jump = max(0, min(len(utt.sentences) - 1, utt.index + delta))
        self._kill(utt.proc)

    def shutdown(self):
        self.stop()
        for p in self._bg:
            self._kill(p)
        _clean_old_audio(max_age=0)

    @staticmethod
    def _kill(proc):
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def play_file(self, path, cfg=None):
        player = self.player(cfg)
        if not player or not os.path.exists(path):
            return False
        self._bg = [p for p in self._bg if p.poll() is None]  # reap finished players
        try:
            self._bg.append(subprocess.Popen(player_cmd(player, path), stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                             **background()))
            return True
        except OSError:
            return False

    def tone(self, kind="chime"):
        if kind not in ("chime", "nwr"):
            return False
        path = os.path.join(TTS_DIR, f"{kind}.wav")
        if not os.path.exists(path):
            try:
                make_tone(path, kind)
            except OSError:
                return False
        return self.play_file(path)

    # -- synthesis ------------------------------------------------------------
    @staticmethod
    def _rate(cfg):
        return intval(cfg, "voice_rate", 50, 200) / 100.0

    def _load_piper(self, name):
        if self._voice is not None and self._voice_name == name:
            return self._voice
        from piper import PiperVoice
        self._voice = PiperVoice.load(voice_path(name))
        self._voice_name = name
        return self._voice

    def _synth(self, utt, idx, engine):
        if idx in utt.cache:
            return utt.cache[idx]
        text = normalize(utt.sentences[idx])
        path = os.path.join(TTS_DIR, f"u{os.getpid()}-{utt.seq}-{idx}.wav")
        rate = self._rate(utt.cfg)
        if engine == "piper":
            with self._voice_lock:
                if utt.stop.is_set():
                    return None
                voice = self._load_piper(self.voice_name(utt.cfg))
                with wave.open(path, "wb") as wf:
                    try:
                        from piper import SynthesisConfig  # piper-tts 1.3+
                    except ImportError:  # piper-tts 1.2 (Python 3.8)
                        voice.synthesize(text, wf, length_scale=1.0 / rate)
                    else:
                        voice.synthesize_wav(text, wf, syn_config=SynthesisConfig(length_scale=1.0 / rate))
        elif engine == "pico":
            subprocess.run(["pico2wave", "-l", "en-US", "-w", path, text], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30, check=True)
        elif engine == "espeak":
            exe = shutil.which("espeak-ng") or shutil.which("espeak")
            subprocess.run([exe, "-v", "en-us", "-s", str(int(165 * rate)), "-w", path, text],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=True)
        elif engine == "festival":
            subprocess.run(["text2wave", "-o", path], input=text.encode("utf-8"), stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=60, check=True)
        elif engine == "say":
            subprocess.run(["say", "-r", str(int(185 * rate)), "-o", path, "--data-format=LEI16@22050", "-f", "-"],
                           input=text.encode("utf-8"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=60, check=True)
        elif engine == "sapi":
            env = dict(os.environ, SKYDROP_TEXT=text, SKYDROP_WAV=path,
                       SKYDROP_RATE=str(max(-10, min(10, round(math.log2(rate) * 6)))))
            subprocess.run(powershell_command(_SAPI), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=60, check=True, **background())
            if not os.path.exists(path):
                raise RuntimeError("Windows' voice made no sound file")
        else:
            return None
        if utt.stop.is_set():
            return None
        utt.cache[idx] = path
        return path

    def _play(self, utt, cmd):
        try:
            utt.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, **background())
        except OSError as e:
            raise RuntimeError(f"couldn't start {cmd[0]}: {e}")
        if utt.stop.is_set():  # stopped while the player was starting
            self._kill(utt.proc)
        while utt.proc.poll() is None:
            if utt.stop.is_set() or utt.jump is not None:
                self._kill(utt.proc)
            time.sleep(0.05)
        return utt.proc.returncode

    def _run(self, utt):
        engine = self.engine(utt.cfg)
        player = self.player(utt.cfg)
        pre = None
        try:
            if engine == "piper":
                with self._voice_lock:
                    self._load_piper(self.voice_name(utt.cfg))
            i = 0
            while 0 <= i < len(utt.sentences) and not utt.stop.is_set():
                utt.index, utt.state = i, "speaking"
                self.version += 1
                if engine == "spd":
                    rc = self._play(utt, ["spd-say", "-w", "-r", str(int((self._rate(utt.cfg) - 1) * 100)),
                                          normalize(utt.sentences[i])])
                else:
                    if pre is not None:
                        pre.join()
                    path = self._synth(utt, i, engine)
                    nxt = i + 1
                    pre = None
                    if nxt < len(utt.sentences) and not utt.stop.is_set():
                        pre = threading.Thread(target=self._safe_synth, args=(utt, nxt, engine), daemon=True)
                        pre.start()
                    rc = self._play(utt, player_cmd(player, path)) if path else None
                if rc == 0:
                    utt.spoke = True
                if utt.stop.is_set():
                    break
                if utt.jump is not None:
                    i, utt.jump = utt.jump, None
                    continue
                i += 1
            if pre is not None:
                pre.join(timeout=5)
            if utt.spoke:
                LOG.resolve("speech")
        except Exception as e:  # report but never crash the app
            self.error = f"speech failed: {type(e).__name__}: {e}"
            LOG.report("speech", "Read-aloud", self.error)
        finally:
            for f in list(utt.cache.values()):
                try:
                    os.remove(f)
                except OSError:
                    pass
            with self.lock:
                if self.cur is utt:
                    self.cur = None
                self.version += 1

    def _safe_synth(self, utt, idx, engine):
        try:
            self._synth(utt, idx, engine)
        except Exception:
            pass

    # -- voices -----------------------------------------------------------------
    def download_voice(self, name=None, wait=False, progress=None):
        name = name or self.voice_name()
        if self.download and self.download[3] is None and self.download[1] < (self.download[2] or 1):
            return False
        if not S.enabled(self.cfg, "piper_voices"):
            self.download = (name, 0, 0, "Piper voices source is turned off")
            return False

        def work():
            ensure(VOICES_DIR)
            try:
                for ext in (".onnx.json", ".onnx"):
                    url = voice_url(self.cfg, name) + (".json" if ext == ".onnx.json" else "")
                    dest = os.path.join(VOICES_DIR, name + ext)
                    tmp = dest + ".part"
                    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(req, timeout=30) as resp, open(tmp, "wb") as fh:
                        total = int(resp.headers.get("Content-Length") or 0)
                        done = 0
                        while True:
                            chunk = resp.read(1 << 16)
                            if not chunk:
                                break
                            fh.write(chunk)
                            done += len(chunk)
                            if ext == ".onnx":
                                self.download = (name, done, total, None)
                                self.version += 1
                                if progress:
                                    progress(done, total)
                    os.replace(tmp, dest)
                self.download = (name, 1, 1, None)
                self._voice = None
            except Exception as e:
                self.download = (name, 0, 0, str(e))
            self.version += 1

        self.download = (name, 0, 0, None)
        if wait:
            work()
            return self.download[3] is None
        threading.Thread(target=work, daemon=True).start()
        return True
