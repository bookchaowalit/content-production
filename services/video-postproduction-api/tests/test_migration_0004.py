"""SPEC-001 migration 0004: hosted-studio columns and repair of rows written by the old pipeline.

Runs on SQLite by default. Set VIDEO_TEST_POSTGRES_URL (a throwaway database) to run it on Postgres as well,
which is what R-CI-3 does in CI.
"""

from __future__ import annotations

import logging.config
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from video_postproduction.config import get_settings

CONFIG_PATH = Path(__file__).parents[1] / "alembic.ini"
NOW = datetime(2026, 11, 1, tzinfo=UTC)

projects = sa.table(
    "projects",
    sa.column("id", sa.Uuid()),
    sa.column("name", sa.String()),
    sa.column("status", sa.String()),
    sa.column("transcription_quality", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
assets = sa.table(
    "media_assets",
    sa.column("id", sa.Uuid()),
    sa.column("project_id", sa.Uuid()),
    sa.column("storage_key", sa.String()),
    sa.column("original_filename", sa.String()),
    sa.column("media_type", sa.String()),
    sa.column("size_bytes", sa.BigInteger()),
    sa.column("sha256", sa.String()),
    sa.column("metadata", sa.JSON()),
    sa.column("status", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
jobs = sa.table(
    "processing_jobs",
    sa.column("id", sa.Uuid()),
    sa.column("project_id", sa.Uuid()),
    sa.column("asset_id", sa.Uuid()),
    sa.column("kind", sa.String()),
    sa.column("status", sa.String()),
    sa.column("progress", sa.Integer()),
    sa.column("idempotency_key", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("started_at", sa.DateTime(timezone=True)),
)
stages = sa.table(
    "processing_stages",
    sa.column("id", sa.Uuid()),
    sa.column("job_id", sa.Uuid()),
    sa.column("name", sa.String()),
    sa.column("status", sa.String()),
    sa.column("attempt", sa.Integer()),
    sa.column("metadata", sa.JSON()),
    sa.column("started_at", sa.DateTime(timezone=True)),
    sa.column("heartbeat_at", sa.DateTime(timezone=True)),
)
# processing_stages as it is before 0004 (no heartbeat/lease columns yet).
old_stages = sa.table(
    "processing_stages",
    sa.column("id", sa.Uuid()),
    sa.column("job_id", sa.Uuid()),
    sa.column("name", sa.String()),
    sa.column("status", sa.String()),
    sa.column("attempt", sa.Integer()),
    sa.column("metadata", sa.JSON()),
    sa.column("started_at", sa.DateTime(timezone=True)),
)
edls = sa.table(
    "edls",
    sa.column("id", sa.Uuid()),
    sa.column("project_id", sa.Uuid()),
    sa.column("source_asset_id", sa.Uuid()),
    sa.column("version", sa.Integer()),
    sa.column("schema_version", sa.String()),
    sa.column("document", sa.JSON()),
    sa.column("status", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
renders = sa.table(
    "render_outputs",
    sa.column("id", sa.Uuid()),
    sa.column("project_id", sa.Uuid()),
    sa.column("edl_id", sa.Uuid()),
    sa.column("job_id", sa.Uuid()),
    sa.column("storage_key", sa.String()),
    sa.column("status", sa.String()),
    sa.column("metadata", sa.JSON()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _database_urls() -> list[str]:
    urls = ["sqlite"]
    if os.environ.get("VIDEO_TEST_POSTGRES_URL"):
        urls.append("postgres")
    return urls


@pytest.fixture(params=_database_urls())
def database_url(request, tmp_path) -> str:
    if request.param == "sqlite":
        return f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    url = os.environ["VIDEO_TEST_POSTGRES_URL"]
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    engine.dispose()
    return url


@pytest.fixture
def alembic_config(monkeypatch, database_url) -> Config:
    monkeypatch.chdir(CONFIG_PATH.parent)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(logging.config, "fileConfig", lambda *args, **kwargs: None)
    get_settings.cache_clear()
    yield Config(str(CONFIG_PATH))
    get_settings.cache_clear()


def _seed_old_rows(connection) -> dict[str, UUID]:
    ids = {
        name: uuid4()
        for name in (
            "project",
            "asset",
            "queued",
            "running",
            "finished",
            "retx",
            "render_job",
            "render",
            "edl",
        )
    }
    connection.execute(
        projects.insert().values(
            id=ids["project"],
            name="p",
            status="ready_for_edit",
            transcription_quality="fast",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    connection.execute(
        assets.insert().values(
            id=ids["asset"],
            project_id=ids["project"],
            storage_key="projects/p/a.mp4",
            original_filename="a.mp4",
            media_type="video/mp4",
            size_bytes=10,
            sha256="0" * 64,
            metadata={},
            status="probed",
            created_at=NOW,
            updated_at=NOW,
        )
    )

    def job(key: str, kind: str, status: str) -> None:
        connection.execute(
            jobs.insert().values(
                id=ids[key],
                project_id=ids["project"],
                asset_id=ids["asset"],
                kind=kind,
                status=status,
                progress=0,
                idempotency_key=f"{kind}:{key}",
                created_at=NOW,
                started_at=NOW,
            )
        )

    def stage(job_key: str, name: str, status: str) -> None:
        connection.execute(
            old_stages.insert().values(
                id=uuid4(),
                job_id=ids[job_key],
                name=name,
                status=status,
                attempt=0,
                metadata={},
                started_at=NOW,
            )
        )

    # Freshly ingested job: old code creates both stages queued.
    job("queued", "ingest", "queued")
    stage("queued", "probe", "queued")
    stage("queued", "transcribe", "queued")
    # Probe running under the old worker, transcribe waiting.
    job("running", "ingest", "running")
    stage("running", "probe", "running")
    stage("running", "transcribe", "queued")
    # Last stage committed succeeded, job commit never happened (fact 19).
    job("finished", "ingest", "running")
    stage("finished", "probe", "succeeded")
    stage("finished", "transcribe", "succeeded")
    # Accurate retranscription with the same two-commit gap.
    job("retx", "retranscription", "running")
    stage("retx", "transcribe", "succeeded")
    # Render whose output row committed succeeded before stage and job (fact 13).
    job("render_job", "render", "running")
    stage("render_job", "render", "running")
    connection.execute(
        edls.insert().values(
            id=ids["edl"],
            project_id=ids["project"],
            source_asset_id=ids["asset"],
            version=1,
            schema_version="edl.v1",
            document={},
            status="draft",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    connection.execute(
        renders.insert().values(
            id=ids["render"],
            project_id=ids["project"],
            edl_id=ids["edl"],
            job_id=ids["render_job"],
            storage_key="projects/p/jobs/render-x/render.mp4",
            status="succeeded",
            metadata={},
            created_at=NOW,
            updated_at=NOW,
        )
    )
    return ids


def _stage_rows(connection, job_id: UUID) -> dict[str, sa.Row]:
    rows = connection.execute(
        sa.select(stages.c.name, stages.c.status, stages.c.heartbeat_at).where(
            stages.c.job_id == job_id
        )
    ).all()
    return {row.name: row for row in rows}


def _job_status(connection, job_id: UUID) -> str:
    return connection.execute(sa.select(jobs.c.status).where(jobs.c.id == job_id)).scalar_one()


def test_0004_adds_columns_and_repairs_old_rows(alembic_config, database_url) -> None:
    command.upgrade(alembic_config, "0003_transcription_quality")
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            ids = _seed_old_rows(connection)
        command.upgrade(alembic_config, "head")

        inspector = sa.inspect(engine)
        asset_columns = {c["name"]: c for c in inspector.get_columns("media_assets")}
        stage_columns = {c["name"] for c in inspector.get_columns("processing_stages")}
        render_columns = {c["name"] for c in inspector.get_columns("render_outputs")}
        assert isinstance(asset_columns["size_bytes"]["type"], sa.BigInteger)
        assert "purged_at" in asset_columns
        assert "purged_at" in render_columns
        assert {"enqueued_at", "heartbeat_at", "lease_token"} <= stage_columns
        assert "quality" in {c["name"] for c in inspector.get_columns("transcripts")}  # review R11

        with engine.connect() as connection:
            queued = _stage_rows(connection, ids["queued"])
            assert queued["probe"].status == "queued"
            assert queued["transcribe"].status == "blocked"

            running = _stage_rows(connection, ids["running"])
            assert running["probe"].status == "running"
            assert running["probe"].heartbeat_at is not None
            assert running["transcribe"].status == "blocked"

            assert _job_status(connection, ids["finished"]) == "succeeded"
            assert _job_status(connection, ids["retx"]) == "succeeded"

            render_stage = _stage_rows(connection, ids["render_job"])["render"]
            assert render_stage.status == "succeeded"
            assert _job_status(connection, ids["render_job"]) == "succeeded"
    finally:
        engine.dispose()


def test_0004_stores_sizes_above_int32(alembic_config, database_url) -> None:
    command.upgrade(alembic_config, "head")
    engine = sa.create_engine(database_url)
    try:
        project_id, asset_id = uuid4(), uuid4()
        with engine.begin() as connection:
            connection.execute(
                projects.insert().values(
                    id=project_id,
                    name="big",
                    status="created",
                    transcription_quality="fast",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            connection.execute(
                assets.insert().values(
                    id=asset_id,
                    project_id=project_id,
                    storage_key="projects/big/a.mp4",
                    original_filename="a.mp4",
                    media_type="video/mp4",
                    size_bytes=2_147_483_648,
                    sha256="0" * 64,
                    metadata={},
                    status="uploaded",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        with engine.connect() as connection:
            stored = connection.execute(
                sa.select(assets.c.size_bytes).where(assets.c.id == asset_id)
            ).scalar_one()
        assert stored == 2_147_483_648
    finally:
        engine.dispose()


def test_0004_downgrade_round_trip_on_empty_database(alembic_config) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0003_transcription_quality")
    command.upgrade(alembic_config, "head")
