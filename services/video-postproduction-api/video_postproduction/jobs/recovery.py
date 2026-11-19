"""Job recovery (SPEC-001 R-LEASE / R-QUEUE).

Runs in the API process at start-up and every few minutes:

(a) a running stage whose heartbeat is stale loses its lease; it is queued again, or on its last attempt the stage
    and the job fail together;
(b) a queued stage of an unfinished job whose message may have been lost is sent again;
(c) a blocked stage whose previous stage already succeeded is queued (heals a hand-off made by an old worker).

A stage with a live heartbeat is never taken over, however long it has been running.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import ProcessingJob, ProcessingStage
from ..observability import emit_job_event
from .queue import JobQueue
from .stages import LEASED_KINDS, fail_job, previous_stage

ACTIVE_JOB_STATES = ("queued", "running", "retry_queued")
_logger = logging.getLogger(__name__)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def take_over_stale_stage(
    session: Session,
    stage_id: object,
    seen_token: str | None,
    seen_heartbeat: datetime | None,
    *,
    retry: bool,
) -> bool:
    """Clear a stale lease only if the row is still exactly what recovery read (conditional update)."""

    conditions = [ProcessingStage.id == stage_id, ProcessingStage.status == "running"]
    conditions.append(
        ProcessingStage.lease_token.is_(None) if seen_token is None else ProcessingStage.lease_token == seen_token
    )
    conditions.append(
        ProcessingStage.heartbeat_at.is_(None) if seen_heartbeat is None else ProcessingStage.heartbeat_at == seen_heartbeat
    )
    values: dict[str, object] = {"lease_token": None}
    if retry:
        values.update(
            status="queued",
            enqueued_at=None,
            error_code="job_lease_expired",
            error_message="Stage lease expired before completion",
        )
    result = session.execute(
        update(ProcessingStage).where(*conditions).values(**values).execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def recover_jobs(
    session: Session,
    settings: Settings,
    queue: JobQueue | None,
    *,
    now: datetime | None = None,
) -> int:
    """Repair stale, lost and blocked stages; return how many stages were touched."""

    current = now or datetime.now(UTC)
    stale_before = current - timedelta(seconds=settings.stage_lease_timeout_seconds)
    resend_before = current - timedelta(seconds=settings.job_lease_seconds)
    touched = 0
    to_send: list[ProcessingStage] = []

    rows = session.execute(
        select(ProcessingStage, ProcessingJob)
        .join(ProcessingJob, ProcessingJob.id == ProcessingStage.job_id)
        .where(ProcessingJob.status.in_(ACTIVE_JOB_STATES), ProcessingJob.kind.in_(LEASED_KINDS))
    ).all()
    by_job: dict[object, dict[str, ProcessingStage]] = {}
    for stage, _job in rows:
        by_job.setdefault(stage.job_id, {})[stage.name] = stage
    jobs = {job.id: job for _stage, job in rows}

    for job_id, stages in by_job.items():
        job = jobs[job_id]
        for stage in stages.values():
            heartbeat = _aware(stage.heartbeat_at) or _aware(stage.started_at)
            if stage.status == "running" and (heartbeat is None or heartbeat < stale_before):
                last_attempt = stage.attempt >= settings.max_stage_attempts
                if not take_over_stale_stage(
                    session, stage.id, stage.lease_token, stage.heartbeat_at, retry=not last_attempt
                ):
                    continue  # the worker finished or beat in the meantime
                session.refresh(stage)
                touched += 1
                if last_attempt:
                    fail_job(session, job, stage, error_code="lease_expired", error_message="Stage lease expired")
                    emit_job_event(
                        "stage_failed",
                        job_id=job.id,
                        stage=stage.name,
                        status="failed",
                        fields={"error_code": "lease_expired"},
                    )
                    break
                job.status = "retry_queued"
        if job.status == "failed":
            continue
        running = any(stage.status == "running" for stage in stages.values())
        for stage in stages.values():
            previous = previous_stage(job.kind, stage.name)
            if (
                stage.status == "blocked"
                and previous is not None
                and previous in stages
                and stages[previous].status == "succeeded"
            ):
                stage.status = "queued"
                stage.enqueued_at = None
                touched += 1
            if stage.status != "queued" or running:
                continue
            if (
                previous is not None
                and previous in stages
                and stages[previous].status != "succeeded"
            ):
                continue
            sent = _aware(stage.enqueued_at)
            if sent is None or sent < resend_before:
                to_send.append(stage)

    session.commit()
    if queue is None:
        return touched
    for stage in to_send:
        try:
            queue.enqueue(stage.job_id, stage.name)
        except Exception:  # noqa: BLE001 - try again on the next pass
            _logger.warning(
                "resend of %s for job %s failed; next recovery pass retries",
                stage.name,
                stage.job_id,
            )
            continue
        stage.enqueued_at = datetime.now(UTC)
        touched += 1
    session.commit()
    return touched
