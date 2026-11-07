"""Simple in-memory rate limiting for the public chat endpoints.

The live demo runs on a free LLM key, so one visitor (or bot) must not be able
to burn the whole daily quota. Three limits apply:
  * per IP per minute (sliding window)
  * per IP per day
  * a global daily cap across all visitors

State lives in memory and resets on restart, which is fine for a single
instance demo. Swap for Redis if you run several instances.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import date


class RateLimitExceeded(Exception):
    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


class RateLimiter:
    def __init__(self, per_minute: int, per_day: int, daily_cap: int, clock=time.time):
        self.per_minute = per_minute
        self.per_day = per_day
        self.daily_cap = daily_cap
        self._clock = clock
        self._lock = threading.Lock()
        self._recent: dict[str, deque[float]] = defaultdict(deque)
        self._day = self._today()
        self._daily: dict[str, int] = defaultdict(int)
        self._total = 0

    def _today(self) -> date:
        return date.fromtimestamp(self._clock())

    def _seconds_to_midnight(self) -> int:
        now = self._clock()
        t = time.localtime(now)
        return max(60, 86400 - (t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec))

    def check(self, ip: str) -> None:
        """Record one request for ``ip`` or raise RateLimitExceeded."""
        with self._lock:
            now = self._clock()
            today = self._today()
            if today != self._day:  # new day: reset daily counters
                self._day, self._total = today, 0
                self._daily.clear()
                self._recent.clear()

            if self.daily_cap and self._total >= self.daily_cap:
                raise RateLimitExceeded(
                    "The demo has reached its daily message limit. Please come back tomorrow.",
                    self._seconds_to_midnight())
            if self.per_day and self._daily[ip] >= self.per_day:
                raise RateLimitExceeded(
                    "You've reached today's message limit for this demo. Please come back tomorrow.",
                    self._seconds_to_midnight())

            window = self._recent[ip]
            while window and now - window[0] >= 60:
                window.popleft()
            if self.per_minute and len(window) >= self.per_minute:
                retry = int(60 - (now - window[0])) + 1
                raise RateLimitExceeded(
                    f"You're sending messages too quickly. Please wait {retry} seconds.", retry)

            window.append(now)
            self._daily[ip] += 1
            self._total += 1
