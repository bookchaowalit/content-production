"""In-process write rate limit and daily transcription-job budget.

Both are per process (one API container in the Compose stack); a multi-replica deployment
would need a shared store such as Redis. A limit of 0 refuses everything (fail closed).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class FixedWindowLimiter:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[float, int]] = {}

    def hit(self, key: str, *, limit: int, window_seconds: float) -> bool:
        if limit <= 0:
            return False
        now = self._clock()
        with self._lock:
            started, count = self._windows.get(key, (now, 0))
            if now - started >= window_seconds:
                started, count = now, 0
            if count >= limit:
                self._windows[key] = (started, count)
                return False
            self._windows[key] = (started, count + 1)
            if len(self._windows) > 10_000:
                cutoff = now - window_seconds
                self._windows = {k: v for k, v in self._windows.items() if v[0] >= cutoff}
            return True

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


class DailyBudget:
    """Counts units per UTC day and refuses once the day's limit is reached."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._day = -1
        self._used = 0

    def take(self, *, limit: int) -> bool:
        if limit <= 0:
            return False
        day = int(self._clock() // 86_400)
        with self._lock:
            if day != self._day:
                self._day, self._used = day, 0
            if self._used >= limit:
                return False
            self._used += 1
            return True

    def reset(self) -> None:
        with self._lock:
            self._day, self._used = -1, 0


write_limiter = FixedWindowLimiter()
transcription_budget = DailyBudget()


def reset_limits() -> None:
    write_limiter.reset()
    transcription_budget.reset()
