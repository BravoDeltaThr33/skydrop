"""HTTP with per-URL TTL caching (memory, plus an optional disk cache for
things that rarely change, like station lists and map layers).

Responses are checked (JSON parses, images look like images) before they
are cached, so a captive-portal page or a cut-off download is never kept.
API keys in URLs never reach error text.
"""

import gzip
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from . import APP, VERSION
from .paths import DISK_CACHE_DIR

USER_AGENT = f"{APP}/{VERSION} (terminal weather app)"
MEM_CACHE_BYTES = 64 * 1024 * 1024
DISK_CACHE_BYTES = 400 * 1024 * 1024


def _reason(err):
    """A network failure in plain words."""
    text = str(err)
    if isinstance(err, (socket.timeout, TimeoutError)) or "timed out" in text:
        return "the connection timed out"
    if isinstance(err, socket.gaierror):
        return "can't look up the server (offline, or a DNS problem)"
    if isinstance(err, ConnectionRefusedError):
        return "the server refused the connection"
    if isinstance(err, ConnectionResetError):
        return "the connection was reset"
    if isinstance(err, ssl.SSLCertVerificationError):
        return f"the server's certificate couldn't be verified ({getattr(err, 'verify_message', '') or text})"
    if isinstance(err, OSError) and getattr(err, "strerror", None):
        return err.strerror
    return text


def _transient(err):
    """A failure worth one quick retry: a reset or dropped connection, or a busy gateway.

    Not a timeout (that already cost the full wait, and the next refresh tries again),
    and not a refusal, a DNS failure or a bad certificate (retrying can't fix those)."""
    if isinstance(err, urllib.error.HTTPError):
        return err.code in (500, 502, 503, 504)
    if isinstance(err, urllib.error.URLError):
        err = getattr(err, "reason", err)
    if isinstance(err, (socket.timeout, TimeoutError)) or "timed out" in str(err):
        return False
    if isinstance(err, (socket.gaierror, ConnectionRefusedError, ssl.SSLCertVerificationError)):
        return False
    return isinstance(err, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
                            http.client.HTTPException, EOFError, ssl.SSLError))


class ApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

    def __str__(self):
        msg = self.args[0] if self.args else ""
        return f"HTTP {self.code}: {msg}" if self.code else str(msg)

    @property
    def bad_key(self):
        return self.code == 401 and "invalid api key" in str(self).lower()


class SourceOff(ApiError):
    """Raised when a request targets a source the user switched off."""

    def __init__(self, name):
        super().__init__(0, f"{name} is turned off in Settings")


def _decode_body(raw, headers):
    enc = ""
    try:
        enc = (headers.get("Content-Encoding") or "").lower()
    except AttributeError:
        pass
    if enc == "gzip" or raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError, zlib.error):
            raise ApiError(0, "the response was cut off or corrupted")
    return raw


_KEY_RE = re.compile(r"(appid|apikey|api_key|key|token)=[^&\s]+", re.I)


def scrub(text):
    return _KEY_RE.sub(r"\1=…", str(text))


def _error_message(text):
    """OpenWeather sends {cod, message}; NWS sends problem+json; others vary."""
    try:
        j = json.loads(text)
    except ValueError:
        flat = re.sub(r"<[^>]+>", " ", text)
        return " ".join(flat.split())[:160]
    if not isinstance(j, dict):
        return None
    msg = None
    for key in ("message", "detail", "title", "reason", "error"):
        val = j.get(key)
        if val and not isinstance(val, (bool, dict, list)):
            msg = " ".join(str(val).split())[:220]
            break
    params = j.get("parameters")
    if msg and isinstance(params, list) and params:
        msg += f" (check: {', '.join(str(p) for p in params)})"
    return msg


def parse_json(raw):
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise ApiError(0, "response wasn't JSON")


def check_image(raw):
    """Reject anything that isn't a PNG, JPEG, GIF or WebP picture (e.g. an HTML error page)."""
    if raw[:8] == b"\x89PNG\r\n\x1a\n" or raw[:3] == b"\xff\xd8\xff" or raw[:4] == b"GIF8" or \
            (raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"):
        return raw
    raise ApiError(0, "response wasn't an image")


class Http:
    def __init__(self, timeout=15):
        self.timeout = timeout
        self.cache = {}          # key -> (fetched_at, raw, expires_at, validators)
        self.cache_bytes = 0
        self.lock = threading.Lock()
        self.bytes_in = 0
        self.requests = 0
        self._puts = 0

    def clear(self):
        with self.lock:
            self.cache.clear()
            self.cache_bytes = 0

    def _remember(self, key, ts, raw, expires, validators=None):
        """Store a response in memory (lock held), keeping the cache bounded."""
        old = self.cache.pop(key, None)
        if old is not None:
            self.cache_bytes -= len(old[1])
        self.cache[key] = (ts, raw, expires, validators)
        self.cache_bytes += len(raw)
        self._puts += 1
        now = time.time()
        if self._puts % 64 == 0 or self.cache_bytes > MEM_CACHE_BYTES:
            for k in [k for k, v in self.cache.items() if v[2] < now]:
                self.cache_bytes -= len(self.cache.pop(k)[1])
        if self.cache_bytes > MEM_CACHE_BYTES:
            for k in sorted(self.cache, key=lambda k: self.cache[k][0]):
                self.cache_bytes -= len(self.cache.pop(k)[1])
                if self.cache_bytes <= MEM_CACHE_BYTES * 0.75:
                    break

    # -- disk cache -----------------------------------------------------------
    @staticmethod
    def _disk_path(key, ttl):
        h = hashlib.sha1(repr(key).encode("utf-8")).hexdigest()
        return os.path.join(DISK_CACHE_DIR, h[:2], f"{h}-{int(ttl)}")

    def _disk_get(self, key, ttl):
        path = self._disk_path(key, ttl)
        try:
            st = os.stat(path)
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            return None, None
        age = time.time() - st.st_mtime
        return raw, max(0.0, age)

    def _disk_put(self, key, ttl, raw):
        path = self._disk_path(key, ttl)
        tmp = None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".part-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
            os.replace(tmp, path)
            tmp = None
        except OSError:
            pass
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def _disk_drop(self, key, ttl):
        try:
            os.remove(self._disk_path(key, ttl))
        except OSError:
            pass

    # -- requests ---------------------------------------------------------------
    def fetch(self, url, headers=None, ttl=0, force=False, on_fetch=None, min_age=20,
              data=None, disk_ttl=0, stale_ok=True, parse=None):
        """Return the response, run through parse() when given (raw bytes otherwise).

        parse() also validates: a response it rejects is never cached."""
        parse = parse or (lambda raw: raw)
        hdrs = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
        hdrs.update(headers or {})
        body = None
        if data is not None:
            body = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        key = (url, hdrs.get("Accept", ""), body)
        now = time.time()
        with self.lock:
            hit = self.cache.get(key)
        if hit is not None:
            age = now - hit[0]
            if (age < ttl and not force) or (force and age < min_age):
                return parse(hit[1])
        disk_val = disk_raw = None
        if disk_ttl:
            disk_raw, dage = self._disk_get(key, disk_ttl)
            if disk_raw is not None:
                try:
                    disk_val = parse(disk_raw)
                except ApiError:  # a bad file from an older version: forget it
                    self._disk_drop(key, disk_ttl)
                    disk_raw = disk_val = None
            if disk_raw is not None and dage < disk_ttl and not force:
                with self.lock:
                    self._remember(key, now - min(dage, ttl or 0), disk_raw, now + max(ttl, min_age))
                return disk_val
        # Ask "has it changed?" when we hold an earlier copy with an ETag or date:
        # an unchanged answer (304) costs a few hundred bytes instead of the whole body.
        validators = None
        if hit is not None and hit[3] and body is None:
            etag, modified = hit[3]
            if etag:
                hdrs["If-None-Match"] = etag
            if modified:
                hdrs["If-Modified-Since"] = modified
        req = urllib.request.Request(url, headers=hdrs, data=body)

        def attempt():
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                got = _decode_body(resp.read(), resp.headers)
                tags = (resp.headers.get("ETag"), resp.headers.get("Last-Modified"))
            return got, (tags if any(tags) else None)

        try:
            try:
                raw, validators = attempt()
            except Exception as first:
                if not _transient(first):
                    raise
                time.sleep(0.8)  # a dropped connection or a busy gateway: one quick retry
                raw, validators = attempt()
            val = parse(raw)
        except urllib.error.HTTPError as e:
            if e.code == 304 and hit is not None:
                now = time.time()
                with self.lock:
                    self.requests += 1
                    self._remember(key, now, hit[1], now + max(ttl, min_age), hit[3])
                if disk_ttl:
                    self._disk_put(key, disk_ttl, hit[1])
                return parse(hit[1])
            try:
                text = _decode_body(e.read(), e.headers).decode("utf-8", "replace")
            except Exception:
                text = ""
            msg = _error_message(text) or str(e.reason or "request failed")
            if e.code == 429:
                msg = f"rate limited ({msg})"
            if disk_raw is not None and stale_ok and e.code >= 500:
                return disk_val
            raise ApiError(e.code, scrub(msg))
        except ApiError:
            if disk_raw is not None and stale_ok:
                return disk_val
            raise
        except urllib.error.URLError as e:
            if disk_raw is not None and stale_ok:
                return disk_val
            raise ApiError(0, scrub(f"network error: {_reason(getattr(e, 'reason', e))}"))
        except (socket.timeout, TimeoutError):
            if disk_raw is not None and stale_ok:
                return disk_val
            raise ApiError(0, "request timed out")
        except (http.client.HTTPException, OSError, EOFError, zlib.error, ValueError) as e:
            # IncompleteRead, a dropped connection, an invalid URL ...
            if disk_raw is not None and stale_ok:
                return disk_val
            raise ApiError(0, scrub(f"network error: {type(e).__name__}: {e}"))
        now = time.time()
        with self.lock:
            self.bytes_in += len(raw)
            self.requests += 1
            if ttl or disk_ttl or min_age:
                self._remember(key, now, raw, now + max(ttl, min_age), validators)
        if disk_ttl:
            self._disk_put(key, disk_ttl, raw)
        if on_fetch:
            on_fetch()
        return val

    def get_json(self, url, headers=None, **kw):
        return self.fetch(url, headers, parse=parse_json, **kw)

    def get_text(self, url, headers=None, **kw):
        return self.fetch(url, headers, **kw).decode("utf-8", "replace")

    def get_bytes(self, url, headers=None, **kw):
        return self.fetch(url, headers, **kw)

    def get_image(self, url, headers=None, **kw):
        return self.fetch(url, headers, parse=check_image, **kw)

    def post_json(self, url, payload, headers=None, **kw):
        return self.fetch(url, headers, data=payload, parse=parse_json, **kw)


def with_params(url, **params):
    """Set query parameters on a URL."""
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query.update({k: str(v) for k, v in params.items() if v is not None})
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def rebase(url, base, default):
    """Point an absolute API link at the configured host if it differs."""
    if url and base != default and url.startswith(default):
        return base + url[len(default):]
    return url


def prune_disk_cache(max_age_days=45):
    """Delete disk cache files that are long past their lifetime, and keep the
    whole cache under DISK_CACHE_BYTES (oldest files go first)."""
    now = time.time()
    files = []
    try:
        for root, _dirs, names in os.walk(DISK_CACHE_DIR):
            for f in names:
                p = os.path.join(root, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                age = now - st.st_mtime
                ttl = None
                if "-" in f and not f.startswith(".part-"):
                    try:
                        ttl = int(f.rsplit("-", 1)[1])
                    except ValueError:
                        ttl = None
                if f.startswith(".part-"):
                    limit = 3600                       # an interrupted write
                elif ttl is None:
                    limit = 7 * 86400                  # files from older versions
                else:
                    limit = min(max(ttl * 3, 2 * 86400), max_age_days * 86400)
                if age > limit:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                else:
                    files.append((st.st_mtime, st.st_size, p))
    except OSError:
        return
    total = sum(s for _m, s, _p in files)
    if total > DISK_CACHE_BYTES:
        for _m, s, p in sorted(files):
            try:
                os.remove(p)
            except OSError:
                continue
            total -= s
            if total <= DISK_CACHE_BYTES * 0.8:
                break
