"""In-memory fixed-window rate limiter. Keys are hashed; nothing is persisted."""

import hashlib
import secrets
import threading
import time


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float, max_entries: int = 10_000):
        self.limit = limit
        self.window = window_seconds
        self.max_entries = max_entries
        self._salt = secrets.token_bytes(16)
        self._buckets: dict[str, list[float]] = {}  # key -> [count, reset_at]
        self._lock = threading.Lock()

    def _key(self, raw: str) -> str:
        return hashlib.sha256(self._salt + raw.encode()).hexdigest()[:24]

    def check(self, raw_key: str, now: float | None = None) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.time() if now is None else now
        key = self._key(raw_key)
        with self._lock:
            if len(self._buckets) >= self.max_entries:
                for k in [k for k, b in self._buckets.items() if b[1] <= now]:
                    del self._buckets[k]
                while len(self._buckets) >= self.max_entries:
                    del self._buckets[next(iter(self._buckets))]
            bucket = self._buckets.get(key)
            if not bucket or bucket[1] <= now:
                bucket = [0, now + self.window]
                self._buckets[key] = bucket
            bucket[0] += 1
            if bucket[0] <= self.limit:
                return True, 0
            return False, max(1, int(bucket[1] - now + 0.999))
