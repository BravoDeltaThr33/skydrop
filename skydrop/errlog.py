"""Active errors: every problem since skydrop started that hasn't cleared yet.

Anything can report() a problem under a key; the same key reporting again
updates its message, last-seen time and count. When that part of the app
works again it calls resolve(key) and the entry leaves the log. The Settings
screen lists the log as "Active errors".
"""

import threading
import time


class ErrorLog:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = {}
        self.version = 0
        self.cleared = 0
        self.started = time.time()

    def report(self, key, label, message):
        message = " ".join(str(message).split()) or "unknown problem"
        now = time.time()
        with self.lock:
            it = self.items.get(key)
            if it is None:
                self.items[key] = {"key": key, "label": label, "msg": message, "first": now, "last": now,
                                   "count": 1}
            else:
                if it["msg"] != message:
                    it["msg"] = message
                it["label"] = label
                it["last"] = now
                it["count"] += 1
            self.version += 1

    def resolve(self, key):
        with self.lock:
            if self.items.pop(key, None) is not None:
                self.cleared += 1
                self.version += 1

    def resolve_prefix(self, prefix):
        with self.lock:
            gone = [k for k in self.items if k.startswith(prefix)]
            for k in gone:
                del self.items[k]
            if gone:
                self.cleared += len(gone)
                self.version += 1

    def active(self):
        with self.lock:
            return sorted((dict(v) for v in self.items.values()), key=lambda it: it["first"])

    def __len__(self):
        with self.lock:
            return len(self.items)


LOG = ErrorLog()
