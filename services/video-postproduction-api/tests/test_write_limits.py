"""Rate limit on write routes and a daily cap on queued transcription jobs (fail closed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from video_postproduction.api.dependencies import (
    get_job_queue,
    get_probe_reader,
    get_session,
    get_settings,
    get_storage,
)
from video_postproduction.api.limits import DailyBudget, FixedWindowLimiter
from video_postproduction.config import Settings
from video_postproduction.jobs.queue import InlineJobQueue
from video_postproduction.main import app
from video_postproduction.media.models import MediaMetadata, VideoStreamMetadata
from video_postproduction.storage.local import LocalFilesystemStorage


class FakeProbe:
    def read(self, path: Path) -> MediaMetadata:
        return MediaMetadata(
            duration=2,
            format_name="mp4",
            size_bytes=10,
            bitrate=None,
            video=VideoStreamMetadata(codec_name="h264", width=320, height=240, fps=30),
        )


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_fixed_window_limiter_blocks_after_the_limit_and_resets():
    clock = Clock(1_000.0)
    limiter = FixedWindowLimiter(clock=clock)

    results = [limiter.hit("client-a", limit=2, window_seconds=60) for _ in range(3)]
    other_client = limiter.hit("client-b", limit=2, window_seconds=60)
    clock.now += 61
    after_window = limiter.hit("client-a", limit=2, window_seconds=60)

    assert results == [True, True, False]
    assert other_client is True
    assert after_window is True


def test_fixed_window_limiter_with_zero_limit_always_refuses():
    assert FixedWindowLimiter().hit("client", limit=0, window_seconds=60) is False


def test_daily_budget_is_per_utc_day_and_fails_closed():
    clock = Clock(86_400.0 * 10 + 5)  # 00:00:05 UTC on day 10
    budget = DailyBudget(clock=clock)

    spent = [budget.take(limit=2) for _ in range(3)]
    clock.now += 86_400
    next_day = budget.take(limit=2)

    assert spent == [True, True, False]
    assert next_day is True
    assert DailyBudget().take(limit=0) is False


@pytest.fixture
def upload_api(session, tmp_path):
    def configure(**overrides: object) -> None:
        settings = Settings(
            storage_root=tmp_path / "storage",
            max_upload_bytes=1024,
            video_editor_allow_unauthenticated=True,
            **overrides,  # type: ignore[arg-type]
        )
        storage = LocalFilesystemStorage(tmp_path / "storage")
        queue = InlineJobQueue()
        app.dependency_overrides.update(
            {
                get_session: lambda: session,
                get_storage: lambda: storage,
                get_settings: lambda: settings,
                get_probe_reader: lambda: FakeProbe(),
                get_job_queue: lambda: queue,
            }
        )

    try:
        yield configure
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_write_routes_are_rate_limited(client, upload_api):
    upload_api(write_rate_limit_per_minute=2)

    statuses = [
        (await client.post("/api/v1/projects", json={"name": f"Demo {i}"})).status_code
        for i in range(3)
    ]
    limited = await client.post("/api/v1/projects", json={"name": "Demo 4"})

    assert statuses == [201, 201, 429]
    assert limited.json()["error"]["code"] == "rate_limited"


@pytest.mark.asyncio
async def test_uploads_stop_when_the_daily_transcription_budget_is_spent(client, upload_api):
    upload_api(daily_transcription_jobs=1)
    project = await client.post("/api/v1/projects", json={"name": "Demo"})
    project_id = project.json()["id"]

    first = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": ("one.mp4", b"sample-video-bytes", "video/mp4")},
    )
    second = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": ("two.mp4", b"sample-video-bytes", "video/mp4")},
    )

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "transcription_budget_exhausted"
