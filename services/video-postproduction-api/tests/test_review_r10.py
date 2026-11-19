"""SPEC-001 review R10: lock order project → asset, schema-wait deadline, enqueued_at commit failure, env quoting."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError


def _locked_tables(session):
    order: list[str] = []

    @event.listens_for(session, "do_orm_execute")
    def capture(state):
        statement = state.statement
        if getattr(statement, "_for_update_arg", None) is not None:

            def tables(clause):
                if hasattr(clause, "left") and hasattr(clause, "right"):
                    yield from tables(clause.left)
                    yield from tables(clause.right)
                else:
                    yield getattr(clause, "name", None)

            of = statement._for_update_arg.of
            targets = (
                [getattr(t, "name", None) for t in of]
                if of
                else [name for clause in statement.get_final_froms() for name in tables(clause)]
            )
            for name in targets:
                if name in {"projects", "media_assets"} and name not in order:
                    order.append(name)

    return order


def _assert_project_first(order: list[str]) -> None:
    assert "media_assets" in order
    assert "projects" in order
    assert order.index("projects") < order.index("media_assets"), order


def test_render_enqueue_and_retry_lock_project_before_asset(session, tmp_path) -> None:
    from tests.test_render_retry import FakeRenderEngine, RecordingQueue, _fail_first_render
    from tests.test_review_r9 import _setup
    from video_postproduction.render.service import RenderService

    project, edl, storage = _setup(session, tmp_path)
    first = _fail_first_render(session, project, edl, storage)
    service = RenderService(session, storage=storage, engine=FakeRenderEngine())

    order = _locked_tables(session)
    service.retry(first.job_id, RecordingQueue())
    _assert_project_first(order)

    order.clear()
    service.enqueue(project.id, edl.id, RecordingQueue())
    _assert_project_first(order)


def test_accurate_retranscription_locks_project_before_asset(session, tmp_path) -> None:
    from tests.test_queue_recovery import RecordingQueue
    from tests.test_stage_runner import make_asset
    from video_postproduction.api.services import enqueue_accurate_retranscription
    from video_postproduction.models import Transcript

    project, asset, _ = make_asset(session, tmp_path)
    asset.status = "transcribed"
    session.add(
        Transcript(
            project_id=project.id,
            asset_id=asset.id,
            provider="faster_whisper",
            model="small",
            language="th",
            duration=2,
            normalized_json={"segments": []},
            content_fingerprint="f",
        )
    )
    session.commit()
    order = _locked_tables(session)
    enqueue_accurate_retranscription(session, RecordingQueue(), project.id, asset.id)
    _assert_project_first(order)


def test_schema_wait_counts_real_time_not_only_sleeps(monkeypatch) -> None:
    from video_postproduction import migrate

    now = [0.0]

    def slow_check(_url):
        now[0] += 400  # one connection attempt hangs for 400 s

    monkeypatch.setattr(migrate, "_current_revision", slow_check)
    result = migrate.wait_for_schema(
        timeout=300, interval=5, sleep=lambda s: None, clock=lambda: now[0]
    )
    assert result == "timeout"


def test_enqueued_at_commit_failure_does_not_fail_the_upload(
    session, tmp_path, monkeypatch
) -> None:
    from tests.test_api_upload import FakeProbe
    from tests.test_queue_recovery import RecordingQueue
    from video_postproduction.api.services import ingest_received
    from video_postproduction.api.upload_stream import ReceivedFile
    from video_postproduction.config import Settings
    from video_postproduction.models import Project
    from video_postproduction.storage.local import LocalFilesystemStorage

    storage = LocalFilesystemStorage(tmp_path / "storage")
    project = Project(name="p", status="created")
    session.add(project)
    session.commit()
    temp = storage.temp_dir() / "upload-x.part"
    temp.write_bytes(b"video")
    received = ReceivedFile(
        path=temp, filename="a.mp4", content_type="video/mp4", size_bytes=5, sha256="0" * 64
    )

    real_commit = session.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 2:  # the enqueued_at commit after the broker accepted the message
            raise OperationalError("COMMIT", {}, Exception("connection lost"))
        return real_commit()

    monkeypatch.setattr(session, "commit", flaky_commit)
    asset, job = ingest_received(
        session,
        storage,
        FakeProbe(),
        RecordingQueue(),
        Settings(storage_root=storage.root),
        project.id,
        received,
    )
    assert asset.id is not None and job.id is not None


def _render_env():
    script = Path(__file__).resolve().parents[3] / "scripts" / "studio" / "render_env.py"
    spec = importlib.util.spec_from_file_location("render_env", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compose_env_values_are_single_quoted_and_literal(tmp_path) -> None:
    module = _render_env()
    module.write(tmp_path, module.derive({"POSTGRES_PASSWORD": '"a"b$HOME c', "TS_AUTHKEY": "k"}), required=set())
    assert "POSTGRES_PASSWORD='\"a\"b$HOME c'" in (tmp_path / "postgres.env").read_text()


def test_a_single_quote_in_a_secret_is_refused(tmp_path) -> None:
    module = _render_env()
    with pytest.raises(SystemExit, match="single quote"):
        module.write(tmp_path, module.derive({"POSTGRES_PASSWORD": "it's"}), required=set())
