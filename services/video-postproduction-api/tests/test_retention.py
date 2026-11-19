"""SPEC-001 F5: retention sweep (D6) and how purged media behaves."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from tests.test_queue_recovery import ingest_job
from tests.test_render_retry import FakeRenderEngine, RecordingQueue
from tests.test_render_service import make_edl_document
from video_postproduction.api.dependencies import (
    get_job_queue,
    get_render_service,
    get_session,
    get_settings,
    get_storage,
)
from video_postproduction.config import Settings
from video_postproduction.edl import validate_edl
from video_postproduction.main import app
from video_postproduction.models import EDL, MediaAsset, Project, RenderOutput
from video_postproduction.render.service import RenderService
from video_postproduction.retention import sweep
from video_postproduction.storage.local import LocalFilesystemStorage

NOW = datetime(2026, 12, 31, tzinfo=UTC)
OLD_SOURCE = NOW - timedelta(days=31)
OLD_RENDER = NOW - timedelta(days=91)


@pytest.fixture
def world(session, tmp_path):
    storage = LocalFilesystemStorage(tmp_path / "storage")
    project = Project(name="r", status="ready_for_edit")
    session.add(project)
    session.flush()

    def asset(name: str, created: datetime) -> MediaAsset:
        key = f"projects/{project.id}/original/{name}.mp4"
        storage.put_atomic(key, b"source", "video/mp4")
        row = MediaAsset(
            project_id=project.id,
            storage_key=key,
            original_filename=f"{name}.mp4",
            media_type="video/mp4",
            size_bytes=6,
            sha256=name[0] * 64,
            metadata_json={},
            status="transcribed",
            created_at=created,
        )
        session.add(row)
        session.flush()
        return row

    old, fresh, busy = (
        asset("old", OLD_SOURCE),
        asset("fresh", NOW - timedelta(days=5)),
        asset("busy", OLD_SOURCE),
    )
    ingest_job(session, busy, probe="running", job_status="running")

    edl = EDL(
        project_id=project.id,
        source_asset_id=fresh.id,
        version=1,
        schema_version="edl.v1",
        document=validate_edl(
            {
                **make_edl_document(project.id, fresh.id),
                "timeline": [
                    {
                        **make_edl_document(project.id, fresh.id)["timeline"][0],
                        "captions": {
                            "enabled": False,
                            "source": "transcript",
                            "format": "ass",
                            "preset": "minimal",
                        },
                    }
                ],
            }
        ).model_dump(mode="json"),
    )
    session.add(edl)
    session.flush()
    job_dir = f"projects/{project.id}/jobs/render-00000000-0000-0000-0000-000000000001"
    storage.put_atomic(f"{job_dir}/render.mp4", b"mp4", "video/mp4")
    storage.put_atomic(f"{job_dir}/trace.json", b"{}", "application/json")
    storage.put_atomic(f"{job_dir}/timeline.edl", b"TITLE", "text/plain")
    old_render = RenderOutput(
        project_id=project.id,
        edl_id=edl.id,
        storage_key=f"{job_dir}/render.mp4",
        status="succeeded",
        metadata_json={},
        created_at=OLD_RENDER,
    )
    session.add(old_render)
    session.commit()
    return {
        "storage": storage,
        "project": project,
        "old": old,
        "fresh": fresh,
        "busy": busy,
        "render": old_render,
        "job_dir": job_dir,
        "edl": edl,
    }


def _exists(storage, key: str) -> bool:
    return storage.local_path(key).is_file()


def test_dry_run_lists_exactly_the_expired_files(session, world) -> None:
    summary = sweep(session, world["storage"], Settings(), now=NOW, dry_run=True)
    assert sorted(summary.source_keys) == [world["old"].storage_key]
    assert summary.render_keys == [world["render"].storage_key]
    assert _exists(world["storage"], world["old"].storage_key)
    assert world["old"].purged_at is None


def test_sweep_purges_expired_media_and_keeps_traces_and_active_sources(session, world) -> None:
    summary = sweep(session, world["storage"], Settings(), now=NOW)
    storage = world["storage"]
    for row in (world["old"], world["fresh"], world["busy"], world["render"]):
        session.refresh(row)
    assert world["old"].purged_at is not None and not _exists(storage, world["old"].storage_key)
    assert world["fresh"].purged_at is None and _exists(storage, world["fresh"].storage_key)
    assert world["busy"].purged_at is None and _exists(
        storage, world["busy"].storage_key
    )  # active job
    assert world["render"].purged_at is not None and not _exists(
        storage, world["render"].storage_key
    )
    assert _exists(storage, f"{world['job_dir']}/trace.json")
    assert _exists(storage, f"{world['job_dir']}/timeline.edl")
    assert summary.skipped_active == 1


def test_an_interrupted_run_is_finished_by_the_next_one(session, world) -> None:
    world["old"].purged_at = NOW  # marked and committed, crashed before deleting
    session.commit()
    sweep(session, world["storage"], Settings(), now=NOW)
    assert not _exists(world["storage"], world["old"].storage_key)


def test_stale_temp_files_and_orphan_sources_are_removed(session, world) -> None:
    storage = world["storage"]
    stale = storage.temp_dir() / "upload-stale.part"
    live = storage.temp_dir() / "upload-live.part"
    stale.write_bytes(b"x")
    live.write_bytes(b"x")
    hours_ago = time.time() - 2 * 3600
    os.utime(stale, (hours_ago, hours_ago))
    orphan = f"projects/{world['project'].id}/original/orphan.mp4"
    storage.put_atomic(orphan, b"o", "video/mp4")
    four_hours = time.time() - 4 * 3600
    os.utime(storage.local_path(orphan), (four_hours, four_hours))

    summary = sweep(session, storage, Settings(), now=NOW)
    assert not stale.exists() and live.exists()
    assert not _exists(storage, orphan)
    assert summary.temp_files == 1 and summary.orphan_files == 1


def test_a_locked_temp_file_is_kept_however_old(session, world) -> None:
    """Review R11: an upload request that is still open holds a shared lock on its temp file."""
    import fcntl

    storage = world["storage"]
    open_upload = storage.temp_dir() / "upload-open.part"
    open_upload.write_bytes(b"x")
    hours_ago = time.time() - 5 * 3600
    os.utime(open_upload, (hours_ago, hours_ago))
    with open_upload.open("rb") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        summary = sweep(session, storage, Settings(), now=NOW)
        assert open_upload.exists() and summary.temp_files == 0
    summary = sweep(session, storage, Settings(), now=NOW)
    assert not open_upload.exists() and summary.temp_files == 1


def test_low_disk_is_reported(session, world, monkeypatch) -> None:
    from video_postproduction import retention

    monkeypatch.setattr(retention, "_disk_usage", lambda path: (100, 10))
    summary = sweep(session, world["storage"], Settings(), now=NOW)
    assert summary.free_ratio == pytest.approx(0.10)
    assert summary.ok is False


@pytest.fixture
def api(session, world):
    storage = world["storage"]
    settings = Settings(storage_root=storage.root)
    app.dependency_overrides.update(
        {
            get_session: lambda: session,
            get_storage: lambda: storage,
            get_settings: lambda: settings,
            get_render_service: lambda: RenderService(
                session, storage=storage, engine=FakeRenderEngine()
            ),
            get_job_queue: lambda: RecordingQueue(),
        }
    )
    sweep(session, storage, Settings(), now=NOW)
    try:
        yield world
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_purged_media_routes_answer_410(client, api) -> None:
    project_id, render = api["project"].id, api["render"]
    asset_stream = await client.get(f"/api/v1/projects/{project_id}/assets/{api['old'].id}/stream")
    assert asset_stream.status_code == 410
    assert asset_stream.json()["error"]["code"] == "media_purged"
    for url in (f"/api/v1/renders/{render.id}/stream", f"/api/v1/renders/{render.id}/download"):
        response = await client.get(url)
        assert response.status_code == 410
    job_name = api["job_dir"].rsplit("/", 1)[1]
    render_file = await client.get(
        f"/api/v1/projects/{project_id}/jobs/{job_name}/files/render.mp4"
    )
    assert render_file.status_code == 410
    trace = await client.get(f"/api/v1/projects/{project_id}/jobs/{job_name}/files/trace.json")
    assert trace.status_code == 200


@pytest.mark.asyncio
async def test_rendering_a_purged_source_answers_409(client, api, session) -> None:
    edl = api["edl"]
    edl.source_asset_id = api["old"].id
    session.commit()
    response = await client.post(
        f"/api/v1/projects/{api['project'].id}/renders", json={"edl_id": str(edl.id)}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "source_purged"


def test_worker_fails_a_stage_whose_source_was_purged(session, tmp_path) -> None:
    from tests.test_queue_recovery import runner, stage
    from tests.test_stage_runner import make_asset

    _, asset, storage = make_asset(session, tmp_path)
    asset.purged_at = NOW
    job = ingest_job(session, asset)
    runner(session, storage).run_probe(job.id)
    failed = stage(session, job, "probe")
    assert failed.status == "failed"
    assert failed.error_code == "source_purged"


def test_render_worker_fails_when_the_source_was_purged(session, world) -> None:
    from video_postproduction.models import ProcessingJob

    service = RenderService(session, storage=world["storage"], engine=FakeRenderEngine())
    output = service.enqueue(world["project"].id, world["edl"].id, RecordingQueue())
    world["fresh"].purged_at = NOW
    session.commit()
    result = service.run_job(output.job_id)
    session.refresh(result)
    job = session.get(ProcessingJob, output.job_id)
    assert result.status == "failed"
    assert job.status == "failed"
    assert job.error_code == "source_purged"


def test_cli_prints_one_json_line_and_exits_by_health(monkeypatch, capsys, session, tmp_path) -> None:
    import json
    from contextlib import contextmanager

    from video_postproduction import db, retention

    @contextmanager
    def fake_session():
        yield session

    monkeypatch.setattr(db, "SessionLocal", fake_session)
    monkeypatch.setattr(retention, "get_settings", lambda: Settings(storage_root=tmp_path / "s"))
    monkeypatch.setattr(retention, "_disk_usage", lambda path: (100, 50))
    assert retention.main(["sweep", "--dry-run"]) == 0
    line = json.loads(capsys.readouterr().out.strip())
    assert line["dry_run"] is True and line["ok"] is True

    monkeypatch.setattr(retention, "_disk_usage", lambda path: (100, 5))
    assert retention.main(["sweep"]) == 1
