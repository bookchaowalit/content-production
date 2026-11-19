"""Retention sweep (SPEC-001 F5, owner decision D6).

Deletes source media older than 30 days and rendered MP4 older than 90 days, keeping every row, trace and
interchange file. Crash-safe: a row is marked ``purged_at`` and committed first, then its file is deleted, and every
run also deletes files of rows already marked. Sources still used by an unfinished job are skipped.

Also removes stale upload temp files (untouched for an hour and not locked by an open upload request) and source
files left without a row by a crash between publish and commit.

Run as ``python -m video_postproduction.retention sweep [--dry-run]``; prints one JSON summary line.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .models import EDL, MediaAsset, ProcessingJob, RenderOutput
from .storage.local import LocalFilesystemStorage

logger = logging.getLogger(__name__)

SOURCE_DAYS = 30
RENDER_DAYS = 90
TEMP_MAX_AGE_SECONDS = 3600
ORPHAN_MAX_AGE_SECONDS = 3 * 3600
MIN_FREE_RATIO = 0.2
ACTIVE_JOB_STATES = ("queued", "running", "retry_queued")
ACTIVE_RENDER_STATES = ("queued", "rendering")


@dataclass
class SweepSummary:
    dry_run: bool
    source_keys: list[str] = field(default_factory=list)
    render_keys: list[str] = field(default_factory=list)
    finished_interrupted: int = 0
    skipped_active: int = 0
    temp_files: int = 0
    orphan_files: int = 0
    free_ratio: float = 1.0
    ok: bool = True


def _disk_usage(path: Path) -> tuple[int, int]:
    usage = shutil.disk_usage(path)
    return usage.total, usage.free


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _source_in_use(session: Session, asset: MediaAsset) -> bool:
    active_job = session.scalar(
        select(ProcessingJob.id).where(
            ProcessingJob.asset_id == asset.id, ProcessingJob.status.in_(ACTIVE_JOB_STATES)
        )
    )
    if active_job is not None:
        return True
    active_render = session.scalar(
        select(RenderOutput.id)
        .join(EDL, EDL.id == RenderOutput.edl_id)
        .where(EDL.source_asset_id == asset.id, RenderOutput.status.in_(ACTIVE_RENDER_STATES))
    )
    return active_render is not None


def _delete(storage: LocalFilesystemStorage, key: str) -> bool:
    path = storage.local_path(key)
    if path.is_file():
        path.unlink()
        return True
    return False


def temp_in_use(path: Path) -> bool:
    """True while an upload request still holds its shared lock on ``path`` (review R11)."""
    try:
        with path.open("rb") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except FileNotFoundError:
        return False
    return False


def sweep(
    session: Session,
    storage: LocalFilesystemStorage,
    settings: Settings,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> SweepSummary:
    current = now or datetime.now(UTC)
    summary = SweepSummary(dry_run=dry_run)

    # Sources past retention: lock each row so queuing new work on it waits for this decision (R-RET-3).
    source_cutoff = current - timedelta(days=SOURCE_DAYS)
    for asset in session.scalars(
        select(MediaAsset).where(MediaAsset.purged_at.is_(None)).with_for_update()
    ).all():
        if _aware(asset.created_at) >= source_cutoff:
            continue
        if _source_in_use(session, asset):
            summary.skipped_active += 1
            continue
        summary.source_keys.append(asset.storage_key)
        if not dry_run:
            asset.purged_at = current
    render_cutoff = current - timedelta(days=RENDER_DAYS)
    for output in session.scalars(
        select(RenderOutput).where(
            RenderOutput.purged_at.is_(None),
            RenderOutput.status == "succeeded",
            RenderOutput.storage_key.is_not(None),
        )
    ).all():
        if _aware(output.created_at) >= render_cutoff:
            continue
        summary.render_keys.append(output.storage_key or "")
        if not dry_run:
            output.purged_at = current
    if dry_run:
        session.rollback()
    else:
        session.commit()  # mark first (R-RET-4) …

    if not dry_run:
        # … then delete, including files of rows an interrupted run already marked.
        planned = set(summary.source_keys) | set(summary.render_keys)
        marked = list(
            session.scalars(select(MediaAsset.storage_key).where(MediaAsset.purged_at.is_not(None)))
        )
        marked += [
            key
            for key in session.scalars(
                select(RenderOutput.storage_key).where(
                    RenderOutput.purged_at.is_not(None), RenderOutput.storage_key.is_not(None)
                )
            )
            if key
        ]
        for key in marked:
            if _delete(storage, key) and key not in planned:
                summary.finished_interrupted += 1

        now_ts = time.time()
        for temp in storage.temp_dir().glob("*"):
            if temp.is_file() and now_ts - temp.stat().st_mtime > TEMP_MAX_AGE_SECONDS and not temp_in_use(temp):
                temp.unlink(missing_ok=True)
                summary.temp_files += 1

        known = set(session.scalars(select(MediaAsset.storage_key)))
        projects_root = storage.root / "projects"
        if projects_root.is_dir():
            for source in projects_root.glob("*/original/*"):
                key = source.relative_to(storage.root).as_posix()
                if (
                    source.is_file()
                    and key not in known
                    and now_ts - source.stat().st_mtime > ORPHAN_MAX_AGE_SECONDS
                ):
                    source.unlink(missing_ok=True)
                    summary.orphan_files += 1

    total, free = _disk_usage(storage.root)
    summary.free_ratio = free / total if total else 0.0
    summary.ok = summary.free_ratio >= MIN_FREE_RATIO
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m video_postproduction.retention")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("sweep")
    run.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    from .db import SessionLocal

    settings = get_settings()
    with SessionLocal() as session:
        summary = sweep(
            session, LocalFilesystemStorage(settings.storage_root), settings, dry_run=args.dry_run
        )
    print(json.dumps(asdict(summary), sort_keys=True))
    return 0 if summary.ok else 1


if __name__ == "__main__":
    sys.exit(main())
