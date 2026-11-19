"""SPEC-001 R-INGEST: in-process budget and disk reservations taken before an upload body is read."""

from __future__ import annotations

import threading

from video_postproduction.api.limits import DailyBudget, DiskReservations


class Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_budget_reservation_counts_until_released() -> None:
    budget = DailyBudget(clock=Clock())
    first = budget.reserve(limit=1)
    assert first is not None
    assert budget.reserve(limit=1) is None  # one unit left: a concurrent upload is refused
    first.release()
    second = budget.reserve(limit=1)
    assert second is not None
    second.confirm()
    assert budget.reserve(limit=1) is None
    second.release()  # releasing after confirm is a no-op
    assert budget.reserve(limit=1) is None


def test_budget_release_after_the_day_ended_does_not_touch_the_new_day() -> None:
    clock = Clock(now=86_400 * 10 + 100)
    budget = DailyBudget(clock=clock)
    held = budget.reserve(limit=1)
    assert held is not None
    clock.now += 86_400
    assert budget.take(limit=1) is True
    held.release()
    assert budget.take(limit=1) is False


def test_budget_release_is_idempotent() -> None:
    budget = DailyBudget(clock=Clock())
    held = budget.reserve(limit=2)
    other = budget.reserve(limit=2)
    assert held is not None and other is not None
    held.release()
    held.release()
    assert budget.reserve(limit=2) is not None
    assert budget.reserve(limit=2) is None


def test_disk_reservation_keeps_twenty_percent_free_across_concurrent_uploads() -> None:
    disk = DiskReservations()
    total, free = 1_000, 600  # 20 % floor = 200, so 400 bytes may be reserved in total
    first = disk.reserve(300, free_bytes=free, total_bytes=total)
    assert first is not None
    assert disk.reserve(150, free_bytes=free, total_bytes=total) is None
    assert disk.reserve(100, free_bytes=free, total_bytes=total) is not None
    first.release()
    first.release()
    assert disk.reserve(250, free_bytes=free, total_bytes=total) is not None


def test_disk_reservation_is_thread_safe() -> None:
    disk = DiskReservations()
    granted: list[object] = []
    lock = threading.Lock()

    def worker() -> None:
        held = disk.reserve(10, free_bytes=1_000, total_bytes=1_000)
        if held is not None:
            with lock:
                granted.append(held)

    threads = [threading.Thread(target=worker) for _ in range(200)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(granted) == 80  # (1000 - 200) / 10
