"""SPEC-001 review R8: connection release during uploads, row locks, readiness timeout, purged flag, DB URL."""

from __future__ import annotations

import pytest

from tests.test_api_upload import FakeProbe
from tests.test_api_upload_stream import RecordingQueue
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
from video_postproduction.storage.local import LocalFilesystemStorage


@pytest.fixture
def upload_api(session, tmp_path):
    storage = LocalFilesystemStorage(tmp_path / "storage")
    settings = Settings(storage_root=tmp_path / "storage")
    app.dependency_overrides.update(
        {
            get_session: lambda: session,
            get_storage: lambda: storage,
            get_settings: lambda: settings,
            get_probe_reader: lambda: FakeProbe(),
            get_job_queue: lambda: RecordingQueue(),
        }
    )
    try:
        yield storage
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_no_database_transaction_is_held_while_the_body_streams(
    client, upload_api, session, monkeypatch
) -> None:
    seen: list[bool] = []
    real = routes.receive_file

    async def spy(*args, **kwargs):
        seen.append(session.in_transaction())
        return await real(*args, **kwargs)

    monkeypatch.setattr(routes, "receive_file", spy)
    project_id = (await client.post("/api/v1/projects", json={"name": "p"})).json()["id"]
    response = await client.post(
        f"/api/v1/projects/{project_id}/assets", files={"file": ("a.mp4", b"video", "video/mp4")}
    )
    assert response.status_code == 202
    assert seen == [False]


@pytest.mark.asyncio
async def test_asset_responses_say_when_the_file_was_purged(client, upload_api, session) -> None:
    from datetime import UTC, datetime

    from video_postproduction.models import MediaAsset

    project_id = (await client.post("/api/v1/projects", json={"name": "p"})).json()["id"]
    upload = await client.post(
        f"/api/v1/projects/{project_id}/assets", files={"file": ("a.mp4", b"video", "video/mp4")}
    )
    asset = session.get(MediaAsset, __import__("uuid").UUID(upload.json()["asset_id"]))
    body = (await client.get(f"/api/v1/projects/{project_id}/assets/{asset.id}")).json()
    assert body["purged_at"] is None
    asset.purged_at = datetime(2026, 12, 1, tzinfo=UTC)
    session.commit()
    body = (await client.get(f"/api/v1/projects/{project_id}/assets/{asset.id}")).json()
    assert body["purged_at"] is not None


def test_database_url_password_is_percent_encoded() -> None:
    import importlib.util
    from pathlib import Path

    from sqlalchemy.engine import make_url

    script = Path(__file__).resolve().parents[3] / "scripts" / "studio" / "render_env.py"
    spec = importlib.util.spec_from_file_location("render_env", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    values = module.derive({"POSTGRES_PASSWORD": "p@ss:w/rd#1 %", "STUDIO_PUBLIC_HOST": "h"})
    url = make_url(values["DATABASE_URL"])
    assert url.password == "p@ss:w/rd#1 %"
    assert url.host == "postgres"


def test_postgres_engines_have_a_connect_timeout(monkeypatch) -> None:
    from video_postproduction import db

    captured = {}
    monkeypatch.setattr(
        db, "create_engine", lambda url, **kwargs: captured.update(kwargs) or object()
    )
    db.make_engine(Settings(database_url="postgresql+psycopg://u:p@h/d"))
    assert captured["connect_args"]["connect_timeout"] == 5


def test_readyz_sets_a_statement_timeout_on_postgres() -> None:
    from video_postproduction.main import READY_STATEMENTS

    assert READY_STATEMENTS["postgresql"][0].startswith("SET LOCAL statement_timeout")


def test_render_env_escapes_dollar_for_compose_but_not_for_docker_run(tmp_path) -> None:
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "studio" / "render_env.py"
    spec = importlib.util.spec_from_file_location("render_env", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    values = module.derive(
        {"POSTGRES_PASSWORD": "a$HOME", "RESTIC_PASSWORD": "r$x", "STUDIO_PUBLIC_HOST": "h", "TS_AUTHKEY": "k"}
    )
    module.write(tmp_path, values, required=set())
    assert "POSTGRES_PASSWORD='a$HOME'" in (tmp_path / "postgres.env").read_text()
    assert "RESTIC_PASSWORD=r$x" in (tmp_path / "backup.env").read_text()
    assert oct((tmp_path / "postgres.env").stat().st_mode & 0o777) == "0o600"
