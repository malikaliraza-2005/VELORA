"""In-process sliding-window rate limiter for login attempts (P3-T2).

Per process, which matches the single-API-container pilot (AD-19). Login tokens carry 256 bits
of entropy, so this is defence in depth, not the thing that makes guessing infeasible. PIN
attempts are limited durably in the database instead (`velora.identity.staff`).
"""

from __future__ import annotations

from collections import deque
from datetime import timedelta
from threading import Lock

from velora.clock import Clock


class RateLimiter:
    def __init__(self, limit: int, window: timedelta, clock: Clock) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """Record an attempt for `key`. False once the limit for the window is used up."""
        now = self._clock.now().timestamp()
        cutoff = now - self._window.total_seconds()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:
                self._prune(cutoff)
            return True

    def _prune(self, cutoff: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            del self._hits[key]
