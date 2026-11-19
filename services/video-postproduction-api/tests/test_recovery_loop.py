"""SPEC-001 R-LEASE: recover_jobs runs at API start and then periodically, and a failing pass does not stop it."""

from __future__ import annotations

import threading

import pytest

from video_postproduction.jobs.recovery_loop import RecoveryLoop


def test_runs_at_start_then_on_every_interval_and_survives_errors() -> None:
    calls: list[int] = []
    third = threading.Event()

    def recover() -> None:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("database briefly unavailable")
        if len(calls) >= 3:
            third.set()

    loop = RecoveryLoop(recover, interval_seconds=0.01)
    loop.start()
    try:
        assert third.wait(timeout=2)
    finally:
        loop.stop()
    count = len(calls)
    assert count >= 3
    assert not loop.is_alive()


def test_zero_interval_disables_the_loop() -> None:
    calls: list[int] = []
    loop = RecoveryLoop(lambda: calls.append(1), interval_seconds=0)
    loop.start()
    loop.stop()
    assert calls == []


@pytest.mark.asyncio
async def test_app_lifespan_starts_and_stops_recovery(monkeypatch) -> None:
    from video_postproduction import main
    from video_postproduction.config import Settings

    events: list[str] = []

    class FakeLoop:
        def __init__(self, recover, *, interval_seconds):
            events.append(f"init:{interval_seconds}")

        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    monkeypatch.setattr(main, "RecoveryLoop", FakeLoop)
    app = main.create_app(Settings(job_recovery_interval_seconds=300))
    async with app.router.lifespan_context(app):
        assert events == ["init:300", "start"]
    assert events[-1] == "stop"
