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


class BudgetReservation:
    """One reserved unit of a :class:`DailyBudget`: confirm it on success, release it on failure."""

    def __init__(self, budget: DailyBudget, day: int) -> None:
        self._budget = budget
        self._day = day
        self._open = True

    def confirm(self) -> None:
        self._open = False

    def release(self) -> None:
        if self._open:
            self._open = False
            self._budget._give_back(self._day)


class DailyBudget:
    """Counts units per UTC day and refuses once the day's limit is reached.

    ``reserve`` holds a unit before work starts (SPEC-001 R-INGEST); a released unit goes back only to the day it was
    taken from.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._day = -1
        self._used = 0

    def _roll(self) -> int:
        day = int(self._clock() // 86_400)
        if day != self._day:
            self._day, self._used = day, 0
        return day

    def reserve(self, *, limit: int) -> BudgetReservation | None:
        if limit <= 0:
            return None
        with self._lock:
            day = self._roll()
            if self._used >= limit:
                return None
            self._used += 1
            return BudgetReservation(self, day)

    def take(self, *, limit: int) -> bool:
        reservation = self.reserve(limit=limit)
        if reservation is None:
            return False
        reservation.confirm()
        return True

    def _give_back(self, day: int) -> None:
        with self._lock:
            if day == self._day and self._used > 0:
                self._used -= 1

    def reset(self) -> None:
        with self._lock:
            self._day, self._used = -1, 0


class DiskReservation:
    def __init__(self, owner: DiskReservations, nbytes: int) -> None:
        self._owner = owner
        self._nbytes = nbytes
        self._open = True

    def release(self) -> None:
        if self._open:
            self._open = False
            self._owner._give_back(self._nbytes)


class DiskReservations:
    """Bytes promised to uploads in progress, so concurrent uploads cannot together eat the free-space floor."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reserved = 0

    def reserve(
        self, nbytes: int, *, free_bytes: int, total_bytes: int, min_free_ratio: float = 0.2
    ) -> DiskReservation | None:
        floor = int(total_bytes * min_free_ratio)
        with self._lock:
            if free_bytes - self._reserved - nbytes < floor:
                return None
            self._reserved += nbytes
            return DiskReservation(self, nbytes)

    def _give_back(self, nbytes: int) -> None:
        with self._lock:
            self._reserved = max(0, self._reserved - nbytes)

    def reset(self) -> None:
        with self._lock:
            self._reserved = 0


write_limiter = FixedWindowLimiter()
transcription_budget = DailyBudget()
upload_disk = DiskReservations()


def reset_limits() -> None:
    write_limiter.reset()
    transcription_budget.reset()
    upload_disk.reset()
