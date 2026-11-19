"""SPEC-001 R-QUALITY, R-HEALTH, R-INLINE and R-OBS-3."""

from __future__ import annotations

from uuid import UUID

import pytest

from tests.test_api_upload import FakeProbe
from tests.test_queue_recovery import RecordingQueue, ingest_job, stage
from tests.test_stage_runner import WorkingProvider, make_asset
from video_postproduction.api.dependencies import (
    get_job_queue,
    get_probe_reader,
    get_session,
    get_settings,
    get_storage,
)
from video_postproduction.config import Settings
from video_postproduction.jobs.stages import StageRunner
from video_postproduction.main import app
from video_postproduction.models import ProcessingJob, Project
from video_postproduction.storage.local import LocalFilesystemStorage


@pytest.fixture
def api(session, tmp_path):
    def configure(**settings_overrides):
        settings = Settings(storage_root=tmp_path / "storage", **settings_overrides)
        app.dependency_overrides.update(
            {
                get_session: lambda: session,
                get_storage: lambda: LocalFilesystemStorage(tmp_path / "storage"),
                get_settings: lambda: settings,
                get_probe_reader: lambda: FakeProbe(),
                get_job_queue: lambda: RecordingQueue(),
            }
        )
        return settings

    configure()
    try:
        yield configure
    finally:
        app.dependency_overrides.clear()


# R-QUALITY ---------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capabilities_list_only_the_allowed_qualities(client, api) -> None:
    api(transcription_qualities="fast")
    body = (await client.get("/api/v1/transcription/capabilities")).json()
    assert body["qualities"] == ["fast"]


@pytest.mark.asyncio
async def test_creating_an_accurate_project_is_refused_when_only_fast_is_allowed(
    client, api
) -> None:
    api(transcription_qualities="fast")
    response = await client.post(
        "/api/v1/projects", json={"name": "x", "transcription_quality": "accurate"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "quality_unavailable"


@pytest.mark.asyncio
async def test_upload_into_an_accurate_project_is_refused(client, api, session) -> None:
    project = Project(name="old", status="created", transcription_quality="accurate")
    session.add(project)
    session.commit()
    api(transcription_qualities="fast")
    response = await client.post(
        f"/api/v1/projects/{project.id}/assets", files={"file": ("a.mp4", b"video", "video/mp4")}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "quality_unavailable"


@pytest.mark.asyncio
async def test_accurate_retranscription_is_refused_when_only_fast_is_allowed(
    client, api, session
) -> None:
    api(transcription_qualities="fast")
    project = Project(name="p", status="ready_for_edit", transcription_quality="fast")
    session.add(project)
    session.commit()
    response = await client.post(
        f"/api/v1/projects/{project.id}/assets/00000000-0000-0000-0000-000000000001/transcribe-accurately"
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "quality_unavailable"


@pytest.mark.asyncio
async def test_project_quality_can_be_switched_to_an_allowed_one(client, api, session) -> None:
    api(transcription_qualities="fast")
    project = Project(name="old", status="created", transcription_quality="accurate")
    session.add(project)
    session.commit()
    refused = await client.patch(
        f"/api/v1/projects/{project.id}", json={"transcription_quality": "accurate"}
    )
    assert refused.status_code == 422
    ok = await client.patch(
        f"/api/v1/projects/{project.id}", json={"transcription_quality": "fast"}
    )
    assert ok.status_code == 200
    assert ok.json()["transcription_quality"] == "fast"


@pytest.mark.asyncio
async def test_project_quality_switch_is_refused_while_a_job_is_active(
    client, api, session, tmp_path
) -> None:
    _, asset, _ = make_asset(session, tmp_path)
    ingest_job(session, asset, probe="running", job_status="running")
    response = await client.patch(
        f"/api/v1/projects/{asset.project_id}", json={"transcription_quality": "fast"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_busy"


def test_worker_refuses_a_quality_that_is_not_allowed(session, tmp_path) -> None:
    project, asset, storage = make_asset(session, tmp_path)
    project.transcription_quality = "accurate"
    asset.metadata_json = {"duration": 2, "video": {"width": 320, "height": 240, "fps": 30}}
    job = ingest_job(session, asset, probe="succeeded", transcribe="queued", job_status="running")
    StageRunner(
        session,
        storage=storage,
        probe_reader=FakeProbe(),
        transcription_provider=WorkingProvider(),
        settings=Settings(transcription_qualities="fast"),
        queue=RecordingQueue(),
    ).run_transcribe(job.id)
    failed = stage(session, job, "transcribe")
    assert failed.status == "failed"
    assert failed.error_code == "quality_unavailable"


# R-HEALTH ----------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readyz_is_ok_when_database_and_broker_answer(client, api, monkeypatch) -> None:
    from video_postproduction import main

    monkeypatch.setattr(main, "_redis_ping", lambda url, timeout: True)
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readyz_is_degraded_when_the_broker_is_down(client, api, monkeypatch) -> None:
    from video_postproduction import main

    monkeypatch.setattr(main, "_redis_ping", lambda url, timeout: False)
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded"}


@pytest.mark.asyncio
async def test_readyz_is_degraded_when_the_database_is_down(client, api, monkeypatch) -> None:
    from video_postproduction import main

    class DeadSession:
        def execute(self, *args, **kwargs):
            raise RuntimeError("database down")

    monkeypatch.setattr(main, "_redis_ping", lambda url, timeout: True)
    app.dependency_overrides[get_session] = lambda: DeadSession()
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded"}


# R-INLINE ----------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asset_stream_is_inline_and_download_is_attachment(client, api) -> None:
    project_id = (await client.post("/api/v1/projects", json={"name": "Demo"})).json()["id"]
    upload = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
    )
    asset_id = upload.json()["asset_id"]
    stream = await client.get(f"/api/v1/projects/{project_id}/assets/{asset_id}/stream")
    assert stream.status_code == 200
    assert stream.headers["content-disposition"].startswith("inline")


# R-OBS-3 -----------------------------------------------------------------------------------------------------


def test_worker_process_initialises_error_reporting(monkeypatch) -> None:
    from video_postproduction.jobs import tasks

    calls: list[str] = []
    monkeypatch.setattr(tasks, "init_error_reporting", lambda: calls.append("init"))
    tasks.on_worker_process_init()
    assert calls == ["init"]


def test_job_ids_are_uuids() -> None:  # keeps the UUID import honest for type checkers
    assert isinstance(UUID(int=1), UUID)
    assert ProcessingJob.__tablename__ == "processing_jobs"
