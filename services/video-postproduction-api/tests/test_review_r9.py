"""SPEC-001 review R9: schema wait survives a database that is not up yet; retry locks; render retry 409."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from video_postproduction import migrate


def test_wait_for_schema_retries_while_the_database_is_unreachable(monkeypatch) -> None:
    attempts: list[int] = []

    def flaky(_url: str):
        attempts.append(1)
        if len(attempts) < 3:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))
        # empty database: still waiting for the API to migrate

    monkeypatch.setattr(migrate, "_current_revision", flaky)
    slept: list[float] = []
    assert migrate.wait_for_schema(timeout=10, interval=5, sleep=slept.append) == "timeout"
    assert len(attempts) == 3
    assert slept == [5, 5]


def test_worker_init_exits_when_the_schema_never_becomes_ready(monkeypatch) -> None:
    from video_postproduction.jobs import tasks

    monkeypatch.setattr(migrate, "wait_for_schema", lambda **_: "timeout")
    with pytest.raises(SystemExit):
        tasks.on_worker_init()


def test_retry_route_locks_project_and_source(monkeypatch) -> None:
    import inspect

    from video_postproduction.api import routes

    source = inspect.getsource(routes.retry_job_route)
    assert "with_for_update" in source


@pytest.mark.asyncio
async def test_render_retry_of_a_purged_source_answers_source_purged(
    client, session, tmp_path
) -> None:
    from datetime import UTC, datetime

    from tests.test_render_retry import FakeRenderEngine, RecordingQueue, _fail_first_render
    from tests.test_render_retry import project_with_edl as fixture  # noqa: F401  (documentation of the setup)
    from video_postproduction.api.dependencies import get_job_queue, get_render_service, get_session
    from video_postproduction.main import app
    from video_postproduction.models import MediaAsset
    from video_postproduction.render.service import RenderService

    project, edl, storage = _setup(session, tmp_path)
    first = _fail_first_render(session, project, edl, storage)
    session.get(MediaAsset, edl.source_asset_id).purged_at = datetime(2026, 12, 1, tzinfo=UTC)
    session.commit()
    service = RenderService(session, storage=storage, engine=FakeRenderEngine())
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
    assert response.json()["error"]["code"] == "source_purged"


def _setup(session, tmp_path):
    from tests.test_render_service import make_edl_document
    from video_postproduction.edl import validate_edl
    from video_postproduction.models import EDL, MediaAsset, Project
    from video_postproduction.storage.local import LocalFilesystemStorage

    project = Project(name="r9", status="ready_for_edit")
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
        sha256="9" * 64,
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


@pytest.mark.asyncio
async def test_temp_upload_file_is_touched_while_bytes_keep_arriving(tmp_path, monkeypatch) -> None:
    import os

    from video_postproduction.api import upload_stream

    monkeypatch.setattr(upload_stream, "TOUCH_INTERVAL_SECONDS", 0)
    touched: list[str] = []
    real = os.utime
    monkeypatch.setattr(
        upload_stream.os,
        "utime",
        lambda path, *a, **k: (touched.append(str(path)), real(path, *a, **k))[1],
    )
    boundary = "b"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="pad"\r\n\r\n'.encode()
        + b"p" * 50
        + b"\r\n"
        + f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.mp4"\r\n\r\n'.encode()
        + b"v" * 10
        + b"\r\n"
        + f"--{boundary}--\r\n".encode()
    )

    async def chunks():
        for i in range(0, len(body), 5):
            yield body[i : i + 5]

    received = await upload_stream.receive_file(
        chunks(),
        f"multipart/form-data; boundary={boundary}",
        tmp_path,
        max_file_bytes=100,
        max_body_bytes=1000,
    )
    received.path.unlink()
    assert touched  # bytes of the non-file part still mark the upload as alive
