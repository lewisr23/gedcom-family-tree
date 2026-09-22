"""A small in-process rate limiter.

The map export is the expensive endpoint: it spends real geocoding quota, which
on a free tier is a fixed daily budget shared by everyone using the site. One
visitor refreshing it in a loop could burn the lot in an hour, so it gets a
tighter allowance than the rest.

In process and per instance, like the session store. Good enough for a single
container; move to Redis at the same time you move sessions.
"""
import os
import threading
import time


class RateLimiter:
    """Fixed window counter, keyed by caller."""

    def __init__(self, limit, window_seconds):
        self.limit = limit
        self.window = window_seconds
        self._lock = threading.Lock()
        self._hits = {}  # key -> list of timestamps

    def check(self, key):
        """Return (allowed, seconds_until_retry)."""
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if t > cutoff]
            if len(hits) >= self.limit:
                retry = int(self.window - (now - hits[0])) + 1
                self._hits[key] = hits
                return False, retry
            hits.append(now)
            self._hits[key] = hits

            # Opportunistic cleanup so abandoned keys do not accumulate.
            if len(self._hits) > 2048:
                for k in [k for k, v in self._hits.items()
                          if not any(t > cutoff for t in v)]:
                    del self._hits[k]
            return True, 0


# Uploads and PDF exports are cheap and local: generous limits, purely to stop
# a runaway script. The map is metered separately because it costs money.
uploads = RateLimiter(
    limit=int(os.environ.get('RATE_UPLOADS', 30)), window_seconds=60)
exports = RateLimiter(
    limit=int(os.environ.get('RATE_EXPORTS', 60)), window_seconds=60)
maps = RateLimiter(
    limit=int(os.environ.get('RATE_MAPS', 5)), window_seconds=60 * 60)


def client_key(request):
    """Identify the caller for limiting purposes.

    Behind a platform proxy (Fly, Railway, Render) the socket address is the
    proxy, so prefer the forwarded client address when the platform sets it.
    """
    forwarded = request.headers.get('x-forwarded-for')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.client.host if request.client else 'unknown'
