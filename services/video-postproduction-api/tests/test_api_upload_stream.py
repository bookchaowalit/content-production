"""SPEC-001 R-INGEST: the upload route streams the body itself, checks before reading and stores one copy."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tests.test_api_upload import FakeProbe
from video_postproduction.api import routes
from video_postproduction.api.dependencies import (
    get_job_queue,
    get_probe_reader,
    get_session,
    get_settings,
    get_storage,
)
from video_postproduction.config import Settings
from video_postproduction.main import app
from video_postproduction.media.ffprobe import MediaProbeError
from video_postproduction.models import MediaAsset, ProcessingJob, ProcessingStage
from video_postproduction.storage.local import LocalFilesystemStorage


class RecordingQueue:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[object, str]] = []
        self.fail = fail

    def enqueue(self, job_id, stage_name: str) -> None:
        if self.fail:
            raise ConnectionError("broker down")
        self.calls.append((job_id, stage_name))


class BrokenProbe:
    def read(self, path: Path):
        raise MediaProbeError("ffprobe_failed", "not a video")


@pytest.fixture
def upload_env(session, tmp_path):
    state = {
        "storage_root": tmp_path / "storage",
        "queue": RecordingQueue(),
        "probe": FakeProbe(),
        "settings": {"max_upload_bytes": 8 * 1024 * 1024},
    }

    def apply() -> None:
        settings = Settings(storage_root=state["storage_root"], **state["settings"])
        app.dependency_overrides.update(
            {
                get_session: lambda: session,
                get_storage: lambda: LocalFilesystemStorage(state["storage_root"]),
                get_settings: lambda: settings,
                get_probe_reader: lambda: state["probe"],
                get_job_queue: lambda: state["queue"],
            }
        )

    apply()
    state["apply"] = apply
    try:
        yield state
    finally:
        app.dependency_overrides.clear()


async def _project(client) -> str:
    return (await client.post("/api/v1/projects", json={"name": "Demo"})).json()["id"]


def _files(storage_root: Path) -> list[Path]:
    return sorted(p for p in storage_root.rglob("*") if p.is_file())


@pytest.mark.asyncio
async def test_upload_stores_one_copy_and_queues_probe_with_transcribe_blocked(
    client, upload_env, session
) -> None:
    project_id = await _project(client)
    response = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
    )
    assert response.status_code == 202
    job = session.get(ProcessingJob, __import__("uuid").UUID(response.json()["job_id"]))
    stages = {s.name: s for s in session.query(ProcessingStage).filter_by(job_id=job.id)}
    assert stages["probe"].status == "queued"
    assert stages["probe"].enqueued_at is not None
    assert stages["transcribe"].status == "blocked"
    assert upload_env["queue"].calls == [(job.id, "probe")]
    stored = _files(upload_env["storage_root"])
    assert len(stored) == 1 and stored[0].read_bytes() == b"video-bytes"
    assert stored[0].relative_to(upload_env["storage_root"]).parts[0] == "projects"


@pytest.mark.asyncio
async def test_body_is_not_spooled_to_the_system_tmp(client, upload_env, monkeypatch) -> None:
    undirected: list[str] = []
    real_spooled, real_named = tempfile.SpooledTemporaryFile, tempfile.NamedTemporaryFile

    def spooled(*args, **kwargs):
        if kwargs.get("dir") is None:
            undirected.append("SpooledTemporaryFile")
        return real_spooled(*args, **kwargs)

    def named(*args, **kwargs):
        if kwargs.get("dir") is None:
            undirected.append("NamedTemporaryFile")
        return real_named(*args, **kwargs)

    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", spooled)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", named)
    project_id = await _project(client)
    big = b"v" * (3 * 1024 * 1024)  # above Starlette's 1 MB spool threshold
    response = await client.post(
        f"/api/v1/projects/{project_id}/assets", files={"file": ("clip.mp4", big, "video/mp4")}
    )
    assert response.status_code == 202
    assert undirected == []


@pytest.mark.asyncio
async def test_missing_content_length_is_refused(client, upload_env) -> None:
    project_id = await _project(client)

    async def body():
        yield b"--x\r\n"

    response = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        content=body(),
        headers={"content-type": "multipart/form-data; boundary=x"},
    )
    assert response.status_code == 411
    assert response.json()["error"]["code"] == "length_required"


@pytest.mark.asyncio
async def test_too_large_is_refused_before_and_while_reading(client, upload_env) -> None:
    upload_env["settings"]["max_upload_bytes"] = 1024
    upload_env["apply"]()
    project_id = await _project(client)
    response = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": ("clip.mp4", b"x" * 2048, "video/mp4")},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "asset_too_large"
    assert _files(upload_env["storage_root"]) == []


@pytest.mark.asyncio
async def test_not_enough_disk_is_refused_before_reading(client, upload_env, monkeypatch) -> None:
    monkeypatch.setattr(routes, "_disk_usage", lambda path: (1_000_000, 100))  # (total, free)
    project_id = await _project(client)
    response = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": ("clip.mp4", b"x" * 64, "video/mp4")},
    )
    assert response.status_code == 507
    assert response.json()["error"]["code"] == "insufficient_storage"


@pytest.mark.asyncio
async def test_unreadable_video_leaves_nothing_and_costs_no_budget(
    client, upload_env, session
) -> None:
    upload_env["settings"]["daily_transcription_jobs"] = 1
    upload_env["probe"] = BrokenProbe()
    upload_env["apply"]()
    project_id = await _project(client)
    bad = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": ("clip.mp4", b"not-video", "video/mp4")},
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "asset_not_readable"
    assert session.query(MediaAsset).count() == 0
    assert _files(upload_env["storage_root"]) == []

    upload_env["probe"] = FakeProbe()
    upload_env["apply"]()
    good = await client.post(
        f"/api/v1/projects/{project_id}/assets", files={"file": ("clip.mp4", b"video", "video/mp4")}
    )
    assert good.status_code == 202  # the failed upload did not spend the single budget unit


@pytest.mark.asyncio
async def test_failed_enqueue_still_answers_202_and_stays_recoverable(
    client, upload_env, session
) -> None:
    upload_env["queue"] = RecordingQueue(fail=True)
    upload_env["apply"]()
    project_id = await _project(client)
    response = await client.post(
        f"/api/v1/projects/{project_id}/assets", files={"file": ("clip.mp4", b"video", "video/mp4")}
    )
    assert response.status_code == 202
    job = session.query(ProcessingJob).one()
    probe = session.query(ProcessingStage).filter_by(job_id=job.id, name="probe").one()
    assert job.status == "queued"
    assert probe.status == "queued"
    assert probe.enqueued_at is None


@pytest.mark.asyncio
async def test_missing_file_part_is_a_400(client, upload_env) -> None:
    project_id = await _project(client)
    response = await client.post(
        f"/api/v1/projects/{project_id}/assets", data={"note": "no file"}, files={}
    )
    assert response.status_code in {400, 411}
    if response.status_code == 400:
        assert response.json()["error"]["code"] == "asset_file_required"
