"""SPEC-001 R-RERENDER: a failed render of an EDL version can be rendered again, without duplicates."""

from __future__ import annotations

import pytest

from tests.test_render_service import FakeRenderEngine, make_edl_document
from video_postproduction.edl import validate_edl
from video_postproduction.models import (
    EDL,
    MediaAsset,
    ProcessingJob,
    ProcessingStage,
    Project,
    RenderOutput,
)
from video_postproduction.render.base import RenderProcessError
from video_postproduction.render.service import RenderLimitReached, RenderService, RenderSuperseded
from video_postproduction.storage.local import LocalFilesystemStorage


class RecordingQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str]] = []

    def enqueue(self, job_id, stage_name: str) -> None:
        self.calls.append((job_id, stage_name))


class FailingEngine(FakeRenderEngine):
    def render(self, edl, *, input_path, output_path, caption_path=None):
        self.calls += 1
        raise RenderProcessError("ffmpeg_failed", "FFmpeg failed")


@pytest.fixture
def project_with_edl(session, tmp_path):
    project = Project(name="retry", status="ready_for_edit")
    session.add(project)
    session.flush()
    storage = LocalFilesystemStorage(tmp_path / "storage")
    storage.put_atomic("projects/demo/source.mp4", b"source", "video/mp4")
    asset = MediaAsset(
        project_id=project.id,
        storage_key="projects/demo/source.mp4",
        original_filename="source.mp4",
        media_type="video/mp4",
        size_bytes=6,
        sha256="f" * 64,
        metadata_json={},
        status="transcribed",
    )
    session.add(asset)
    session.flush()
    raw = make_edl_document(project.id, asset.id)
    raw["timeline"][0]["captions"]["enabled"] = False
    edl = EDL(
        project_id=project.id,
        source_asset_id=asset.id,
        version=1,
        schema_version="edl.v1",
        document=validate_edl(raw).model_dump(mode="json"),
    )
    session.add(edl)
    session.commit()
    return project, edl, storage


def _fail_first_render(session, project, edl, storage):
    queue = RecordingQueue()
    failing = RenderService(session, storage=storage, engine=FailingEngine())
    first = failing.enqueue(project.id, edl.id, queue)
    with pytest.raises(RenderProcessError):
        failing.run_job(first.job_id)
    session.refresh(first)
    assert first.status == "failed"
    return first


def test_failed_render_can_be_enqueued_again_and_succeeds(session, project_with_edl) -> None:
    project, edl, storage = project_with_edl
    first = _fail_first_render(session, project, edl, storage)

    service = RenderService(session, storage=storage, engine=FakeRenderEngine())
    retry = service.enqueue(project.id, edl.id, RecordingQueue())
    assert retry.id != first.id
    retry_job = session.get(ProcessingJob, retry.job_id)
    assert retry_job.idempotency_key.endswith(":retry1")
    assert service.run_job(retry.job_id).status == "succeeded"


def test_enqueue_again_returns_the_running_or_finished_retry(session, project_with_edl) -> None:
    project, edl, storage = project_with_edl
    _fail_first_render(session, project, edl, storage)
    service = RenderService(session, storage=storage, engine=FakeRenderEngine())
    retry = service.enqueue(project.id, edl.id, RecordingQueue())
    again = service.enqueue(project.id, edl.id, RecordingQueue())
    assert again.id == retry.id
    service.run_job(retry.job_id)
    after_success = service.enqueue(project.id, edl.id, RecordingQueue())
    assert after_success.id == retry.id
    assert session.query(RenderOutput).count() == 2


def test_in_place_retry_of_an_old_render_is_refused_once_superseded(
    session, project_with_edl
) -> None:
    project, edl, storage = project_with_edl
    first = _fail_first_render(session, project, edl, storage)
    service = RenderService(session, storage=storage, engine=FakeRenderEngine())
    service.enqueue(project.id, edl.id, RecordingQueue())
    with pytest.raises(RenderSuperseded):
        service.retry(first.job_id, RecordingQueue())


def test_in_place_retry_resets_render_stage_and_job_together(session, project_with_edl) -> None:
    project, edl, storage = project_with_edl
    first = _fail_first_render(session, project, edl, storage)
    queue = RecordingQueue()
    service = RenderService(session, storage=storage, engine=FakeRenderEngine())
    service.retry(first.job_id, queue)
    session.refresh(first)
    job = session.get(ProcessingJob, first.job_id)
    render_stage = session.query(ProcessingStage).filter_by(job_id=job.id, name="render").one()
    assert (first.status, render_stage.status, job.status) == ("queued", "queued", "retry_queued")
    assert first.error is None
    assert queue.calls == [(job.id, "render")]
    assert service.run_job(job.id).status == "succeeded"


@pytest.mark.asyncio
async def test_retry_route_refuses_a_superseded_render(client, session, project_with_edl) -> None:
    from video_postproduction.api.dependencies import get_job_queue, get_render_service, get_session
    from video_postproduction.main import app

    project, edl, storage = project_with_edl
    first = _fail_first_render(session, project, edl, storage)
    service = RenderService(session, storage=storage, engine=FakeRenderEngine())
    service.enqueue(project.id, edl.id, RecordingQueue())
    app.dependency_overrides.update(
        {
            get_session: lambda: session,
            get_render_service: lambda: service,
            get_job_queue: lambda: RecordingQueue(),
        }
    )
    try:
        response = await client.post(f"/api/v1/jobs/{first.job_id}/retry", json={"stage": "render"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "render_superseded"


def test_in_place_retry_respects_the_attempt_limit(session, project_with_edl) -> None:
    project, edl, storage = project_with_edl
    first = _fail_first_render(session, project, edl, storage)
    stage_row = session.query(ProcessingStage).filter_by(job_id=first.job_id, name="render").one()
    stage_row.attempt = 3
    session.commit()
    service = RenderService(session, storage=storage, engine=FakeRenderEngine(), max_attempts=3)
    with pytest.raises(ValueError, match="retry limit"):
        service.retry(first.job_id, RecordingQueue())


def test_render_family_is_capped(session, project_with_edl) -> None:
    project, edl, storage = project_with_edl
    failing = RenderService(session, storage=storage, engine=FailingEngine(), max_family=2)
    for _ in range(2):
        output = failing.enqueue(project.id, edl.id, RecordingQueue())
        with pytest.raises(RenderProcessError):
            failing.run_job(output.job_id)
    with pytest.raises(RenderLimitReached):
        failing.enqueue(project.id, edl.id, RecordingQueue())


def test_failed_dispatch_on_retry_leaves_the_render_failed(session, project_with_edl) -> None:
    class DownQueue:
        def enqueue(self, job_id, stage_name):
            raise ConnectionError("broker down")

    project, edl, storage = project_with_edl
    first = _fail_first_render(session, project, edl, storage)
    service = RenderService(session, storage=storage, engine=FakeRenderEngine())
    with pytest.raises(RuntimeError):
        service.retry(first.job_id, DownQueue())
    session.refresh(first)
    job = session.get(ProcessingJob, first.job_id)
    assert (first.status, job.status, job.error_code) == ("failed", "failed", "queue_error")
