"""SPEC-001 R-REL-2: the API start-up migrate step tolerates a schema newer than the image."""

from __future__ import annotations

import logging
import logging.config
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from video_postproduction import migrate
from video_postproduction.config import get_settings

CONFIG_PATH = Path(__file__).parents[1] / "alembic.ini"


@pytest.fixture
def database_url(monkeypatch, tmp_path) -> str:
    url = f"sqlite:///{(tmp_path / 'wrapper.db').as_posix()}"
    monkeypatch.chdir(CONFIG_PATH.parent)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(logging.config, "fileConfig", lambda *args, **kwargs: None)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def _current_revision(url: str) -> str | None:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            if not sa.inspect(connection).has_table("alembic_version"):
                return None
            return connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
    finally:
        engine.dispose()


def _head() -> str:
    return ScriptDirectory.from_config(Config(str(CONFIG_PATH))).get_current_head()


def test_empty_database_is_upgraded_to_head(database_url) -> None:
    assert migrate.run(str(CONFIG_PATH)) == "upgraded"
    assert _current_revision(database_url) == _head()


def test_known_old_revision_is_upgraded(database_url) -> None:
    from alembic import command

    command.upgrade(Config(str(CONFIG_PATH)), "0003_transcription_quality")
    assert migrate.run(str(CONFIG_PATH)) == "upgraded"
    assert _current_revision(database_url) == _head()


def test_newer_schema_starts_without_migrating(database_url, caplog) -> None:
    migrate.run(str(CONFIG_PATH))
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE alembic_version SET version_num = '9999_from_a_newer_release'")
        )
    engine.dispose()

    with caplog.at_level(logging.WARNING):
        assert migrate.run(str(CONFIG_PATH)) == "schema_newer_than_code"
    assert _current_revision(database_url) == "9999_from_a_newer_release"
    assert any("schema_newer_than_code" in record.getMessage() for record in caplog.records)


def test_sqlite_in_a_missing_directory_is_created(monkeypatch, tmp_path) -> None:
    url = f"sqlite:///{(tmp_path / 'new' / 'dir' / 'db.sqlite').as_posix()}"
    monkeypatch.chdir(CONFIG_PATH.parent)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(logging.config, "fileConfig", lambda *args, **kwargs: None)
    get_settings.cache_clear()
    try:
        assert migrate.run(str(CONFIG_PATH)) == "upgraded"
    finally:
        get_settings.cache_clear()
    assert _current_revision(url) == _head()


def test_worker_waits_until_the_schema_reaches_its_head(database_url) -> None:
    from alembic import command

    from video_postproduction.migrate import wait_for_schema

    command.upgrade(Config(str(CONFIG_PATH)), "0003_transcription_quality")
    checks: list[str | None] = []

    def fake_sleep(_seconds: float) -> None:
        # The API finishes its migration while the worker waits.
        checks.append(_current_revision(database_url))
        command.upgrade(Config(str(CONFIG_PATH)), "head")

    assert wait_for_schema(str(CONFIG_PATH), timeout=60, interval=5, sleep=fake_sleep) == "ready"
    assert checks == ["0003_transcription_quality"]


def test_worker_accepts_a_newer_schema(database_url) -> None:
    from video_postproduction.migrate import wait_for_schema

    migrate.run(str(CONFIG_PATH))
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE alembic_version SET version_num = '9999_from_a_newer_release'")
        )
    engine.dispose()
    assert wait_for_schema(str(CONFIG_PATH), timeout=1, interval=1, sleep=lambda s: None) == "ready"


def test_worker_gives_up_after_the_timeout(database_url) -> None:
    from video_postproduction.migrate import wait_for_schema

    slept: list[float] = []
    assert (
        wait_for_schema(str(CONFIG_PATH), timeout=10, interval=5, sleep=slept.append) == "timeout"
    )
    assert slept == [5, 5]
