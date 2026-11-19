"""SPEC-001 review R11 regressions."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from tests.test_job_trace import studio  # noqa: F401 - fixture

ALL_REQUIRED = {
    "POSTGRES_PASSWORD": "pw",
    "VIDEO_EDITOR_API_TOKEN": "tok",
    "STUDIO_OWNER_LOGIN": "owner@example.com",
    "STUDIO_PUBLIC_HOST": "video-studio.example.ts.net",
    "TS_AUTHKEY": "tskey",
}


def _render_env():
    script = Path(__file__).resolve().parents[3] / "scripts" / "studio" / "render_env.py"
    spec = importlib.util.spec_from_file_location("render_env_r11", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_required_secret_writes_nothing(tmp_path) -> None:
    module = _render_env()
    (tmp_path / "api.env").write_text("VIDEO_EDITOR_API_TOKEN='old'\n")
    values = dict(ALL_REQUIRED)
    del values["TS_AUTHKEY"]
    with pytest.raises(SystemExit, match="TS_AUTHKEY"):
        module.write(tmp_path, module.derive(values))
    assert (tmp_path / "api.env").read_text() == "VIDEO_EDITOR_API_TOKEN='old'\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["api.env"]


def test_a_bad_value_in_a_later_file_leaves_earlier_files_untouched(tmp_path) -> None:
    module = _render_env()
    (tmp_path / "postgres.env").write_text("POSTGRES_PASSWORD='old'\n")
    values = {**ALL_REQUIRED, "TS_AUTHKEY": "bad'key"}
    with pytest.raises(SystemExit, match="single quote"):
        module.write(tmp_path, module.derive(values))
    assert (tmp_path / "postgres.env").read_text() == "POSTGRES_PASSWORD='old'\n"


@pytest.mark.parametrize("value", ["ends-with\\", "mid\\dle"])
def test_a_backslash_in_a_compose_value_is_refused(tmp_path, value) -> None:
    module = _render_env()
    with pytest.raises(SystemExit, match="backslash"):
        module.write(tmp_path, module.derive({**ALL_REQUIRED, "POSTGRES_PASSWORD": value}))


def test_complete_secrets_write_every_file_mode_600(tmp_path) -> None:
    module = _render_env()
    missing = module.write(tmp_path, module.derive(ALL_REQUIRED))
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == sorted(f"{service}.env" for service in module.LAYOUT)
    assert all((tmp_path / name).stat().st_mode & 0o777 == 0o600 for name in names)
    assert "TS_AUTHKEY" not in missing


def test_api_render_service_uses_configured_max_stage_attempts(session, tmp_path) -> None:
    from video_postproduction.api.dependencies import get_render_service
    from video_postproduction.config import Settings
    from video_postproduction.storage.local import LocalFilesystemStorage

    settings = Settings(max_stage_attempts=5, storage_root=tmp_path)
    service = get_render_service(
        session=session, storage=LocalFilesystemStorage(tmp_path), render_engine=object(), settings=settings
    )
    assert service.max_attempts == 5


def test_worker_render_service_uses_configured_max_stage_attempts(monkeypatch, tmp_path) -> None:
    from video_postproduction.jobs import tasks

    seen: dict[str, int] = {}

    class Recorder:
        def __init__(self, session, *, storage, engine, max_attempts=3, max_family=10) -> None:
            seen["max_attempts"] = max_attempts

        def run_job(self, job_id) -> None:
            return None

    class NoSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

        def get(self, model, key):
            return None

    from video_postproduction.config import Settings

    monkeypatch.setattr(tasks, "RenderService", Recorder)
    monkeypatch.setattr(tasks, "SessionLocal", NoSession)
    monkeypatch.setattr(tasks, "get_settings", lambda: Settings(max_stage_attempts=6, storage_root=tmp_path))
    monkeypatch.setattr(tasks, "_provider", lambda quality: None)
    tasks.run_stage.run("00000000-0000-0000-0000-000000000001", "render")
    assert seen["max_attempts"] == 6


def _fake_bin(tmp_path: Path, docker_exit: int) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "curl.log"
    (bin_dir / "docker").write_text(f"#!/bin/sh\nexit {docker_exit}\n")
    (bin_dir / "curl").write_text(f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done >> "{calls}"\n')
    for name in ("docker", "curl"):
        (bin_dir / name).chmod(0o755)
    return bin_dir, calls


@pytest.mark.skipif(shutil.which("flock") is None, reason="needs util-linux flock")
def test_backup_reports_a_failed_pg_dump(tmp_path) -> None:
    import os
    import subprocess

    bin_dir, calls = _fake_bin(tmp_path, docker_exit=1)
    home = tmp_path / "home"
    (home / "env").mkdir(parents=True)
    (home / "env" / "backup.env").write_text("HEARTBEAT_TOKEN=t\n")
    script = Path(__file__).resolve().parents[3] / "scripts" / "studio" / "studio.sh"
    result = subprocess.run(
        ["bash", str(script), "backup"],
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "STUDIO_HOME": str(home)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    sent = calls.read_text() if calls.exists() else ""
    assert "https://monitor.bookchaowalit.com/heartbeat/video-studio-backup" in sent
    assert '"ok": false' in sent
    assert not (home / "backup" / "studio.dump").exists()


def test_transcript_records_the_quality_it_was_made_with(session, tmp_path) -> None:
    from tests.test_queue_recovery import ingest_job, runner
    from tests.test_stage_runner import make_asset
    from video_postproduction.config import Settings
    from video_postproduction.jobs.stages import StageRunner
    from video_postproduction.models import Project, Transcript

    project, asset, storage = make_asset(session, tmp_path)
    asset.metadata_json = {"duration": 2, "video": {"width": 320, "height": 240, "fps": 30}}
    project.transcription_quality = "fast"
    job = ingest_job(session, asset, probe="succeeded", transcribe="queued", job_status="running")
    base = runner(session, storage)
    # The provider was built for "fast"; the project switched to "accurate" before the stage ran.
    session.get(Project, project.id).transcription_quality = "accurate"
    session.commit()
    StageRunner(
        session,
        storage=storage,
        probe_reader=base.probe_reader,
        transcription_provider=base.transcription_provider,
        settings=Settings(max_stage_attempts=3, transcription_qualities="fast,accurate"),
        queue=base.queue,
        transcription_quality="fast",
    ).run_transcribe(job.id)
    transcript = session.query(Transcript).filter_by(asset_id=asset.id).one()
    assert transcript.quality == "fast"


def test_render_trace_takes_quality_from_the_transcript_not_the_project(studio) -> None:  # noqa: F811
    from tests.test_job_trace import _render, _segment

    studio["project"].transcription_quality = "fast"  # changed after the accurate transcript was made
    studio["session"].commit()
    output = _render(studio, [_segment(studio["asset"].id, 12.5, 42.25)])
    trace_path = studio["storage"].local_path(f"projects/{output.project_id}/jobs/render-{output.id}/trace.json")
    import json

    transcribe = next(step for step in json.loads(trace_path.read_text())["steps"] if step["stage"] == "transcribe")
    assert transcribe["params"]["quality"] == "accurate"
