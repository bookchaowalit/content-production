"""API start-up migration step (SPEC-001 R-REL-2).

Migrations are additive only, so an image may start on a schema written by a newer release (a rollback). A known
revision is upgraded to this image's head; a revision this image does not know is left alone and logged as
``schema_newer_than_code``. Production rollback never downgrades.

Run as ``python -m video_postproduction.migrate``.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from .config import get_settings
from .db import _ensure_sqlite_parent

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "alembic.ini"


def _current_revision(database_url: str) -> str | None:
    _ensure_sqlite_parent(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def run(config_path: str | Path = DEFAULT_CONFIG) -> str:
    """Migrate the database; return ``"upgraded"`` or ``"schema_newer_than_code"``."""

    config = Config(str(config_path))
    script = ScriptDirectory.from_config(config)
    current = _current_revision(get_settings().database_url)
    if current is not None:
        try:
            known = script.get_revision(current) is not None
        except CommandError:
            known = False
        if not known:
            logger.warning(
                "schema_newer_than_code: database revision %s is not known to this image (head %s); starting without migrating",
                current,
                script.get_current_head(),
            )
            return "schema_newer_than_code"
    command.upgrade(config, "head")
    return "upgraded"


def wait_for_schema(
    config_path: str | Path = DEFAULT_CONFIG,
    *,
    timeout: float = 300,
    interval: float = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """SPEC-001 R-SCHEMA-WAIT: block until the database is at this image's head (or newer); ``ready`` / ``timeout``."""

    script = ScriptDirectory.from_config(Config(str(config_path)))
    head = script.get_current_head()
    waited = 0.0
    while True:
        try:
            current = _current_revision(get_settings().database_url)
        except SQLAlchemyError:
            current = None  # database not reachable yet: keep waiting (review R9)
        if current == head:
            return "ready"
        if current is not None:
            try:
                known = script.get_revision(current) is not None
            except CommandError:
                known = False
            if not known:
                return "ready"  # newer release's schema; additive, so this image can run on it
        if waited >= timeout:
            logger.error("schema_not_ready: database at %s, this worker needs %s", current, head)
            return "timeout"
        sleep(interval)
        waited += interval


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
