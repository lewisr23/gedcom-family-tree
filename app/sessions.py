"""Per-visitor tree storage.

Trees are held in memory only, keyed by a signed cookie, and dropped once a
session goes idle. Nothing is written to disk. That is a deliberate design
choice rather than a shortcut: a GEDCOM carries names, dates of birth and
addresses for people who are often still living and who never agreed to be on
anyone's server, so the safest thing to hold is nothing.

Single process only. Running more than one worker means a session's tree lives
in whichever worker took the upload, and a later request routed elsewhere finds
nothing. Move this behind Redis before scaling out.
"""
import hashlib
import hmac
import os
import secrets
import threading
import time

COOKIE_NAME = "tree_session"

# How long a tree survives without being touched. Long enough to browse and
# export at leisure, short enough that an abandoned tab does not keep someone's
# family data resident for the rest of the day.
SESSION_TTL_SECONDS = int(os.environ.get('SESSION_TTL_SECONDS', 30 * 60))

# How many trees the process will hold at once.
#
# Measured: the app idles at about 57 MB with FastAPI and ReportLab loaded, and
# each held tree adds roughly 2 MB for a 144 person file. 50 sessions is
# therefore around 160 MB, which leaves plenty of room on a 512 MB instance for
# concurrent PDF generation. Raise it if you give the app more memory; the cap
# exists so that traffic cannot grow memory without bound.
MAX_SESSIONS = int(os.environ.get('MAX_SESSIONS', 50))

# Signing key for the session cookie. Generated per process if unset, which
# means restarting the server invalidates outstanding sessions. That is fine:
# the trees they pointed at died with the process anyway.
SECRET_KEY = os.environ.get('SESSION_SECRET') or secrets.token_hex(32)


def _sign(session_id):
    mac = hmac.new(SECRET_KEY.encode(), session_id.encode(), hashlib.sha256)
    return mac.hexdigest()[:32]


def make_cookie_value(session_id):
    return f"{session_id}.{_sign(session_id)}"


def read_cookie_value(raw):
    """Return the session id from a cookie, or None if it fails its signature."""
    if not raw or '.' not in raw:
        return None
    session_id, _, signature = raw.rpartition('.')
    if not session_id or not hmac.compare_digest(signature, _sign(session_id)):
        return None
    return session_id


class SessionStore:
    """Thread safe map of session id to parsed tree.

    Endpoints run in a threadpool, so every access takes the lock.
    """

    def __init__(self, ttl=SESSION_TTL_SECONDS, max_sessions=MAX_SESSIONS):
        self.ttl = ttl
        self.max_sessions = max_sessions
        self._lock = threading.Lock()
        self._entries = {}  # session_id -> [parser, last_seen]

    def _evict_locked(self, now):
        stale = [sid for sid, (_, seen) in self._entries.items()
                 if now - seen > self.ttl]
        for sid in stale:
            del self._entries[sid]

        # Still over the cap after dropping stale ones: shed the least recently
        # used, so a burst of new visitors cannot exhaust memory.
        while len(self._entries) > self.max_sessions:
            oldest = min(self._entries, key=lambda s: self._entries[s][1])
            del self._entries[oldest]

    def new_session(self):
        return secrets.token_urlsafe(24)

    def put(self, session_id, parser):
        now = time.monotonic()
        with self._lock:
            self._entries[session_id] = [parser, now]
            self._evict_locked(now)

    def get(self, session_id):
        """The session's tree, or None. Reading counts as activity."""
        if not session_id:
            return None
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is None:
                return None
            if now - entry[1] > self.ttl:
                del self._entries[session_id]
                return None
            entry[1] = now
            return entry[0]

    def drop(self, session_id):
        with self._lock:
            self._entries.pop(session_id, None)

    def sweep(self):
        """Drop expired sessions. Called periodically so that an idle server
        does not sit holding family data nobody is looking at."""
        with self._lock:
            self._evict_locked(time.monotonic())

    def __len__(self):
        with self._lock:
            return len(self._entries)


store = SessionStore()
