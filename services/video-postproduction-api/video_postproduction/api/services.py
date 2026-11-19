"""Application services for project creation, upload, and response lookup."""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..jobs.queue import JobQueue
from ..media.ffprobe import FFprobeMetadataReader, MediaProbeError
from ..media.validation import UploadValidationError, validate_media_type
from ..models import MediaAsset, ProcessingJob, ProcessingStage, Project, Transcript
from ..storage.base import StoragePathError
from ..storage.local import LocalFilesystemStorage
from .errors import APIError
from .upload_stream import ReceivedFile

logger = logging.getLogger(__name__)


def create_project(
    session: Session,
    name: str,
    instructions: str | None,
    transcription_quality: str = "fast",
) -> Project:
    project = Project(
        name=name,
        instructions=instructions,
        transcription_quality=transcription_quality,
        status="created",
    )
    session.add(project)
    session.commit()
    return project


def enqueue_accurate_retranscription(
    session: Session,
    queue: JobQueue,
    project_id: UUID,
    asset_id: UUID,
) -> ProcessingJob:
    project = get_project_or_error(session, project_id)
    asset = get_asset_or_error(session, project_id, asset_id)
    # Same row lock as the retention sweep (R-RET-3): purge and queuing cannot interleave.
    session.execute(select(MediaAsset.id).where(MediaAsset.id == asset.id).with_for_update())
    session.refresh(asset)
    if asset.purged_at is not None:
        raise APIError(
            "source_purged", "The source video was removed by the retention policy.", 409
        )
    if project.transcription_quality != "fast":
        raise APIError(
            "transcription_not_fast", "The project is not using fast transcription.", 409
        )
    if asset.status != "transcribed":
        raise APIError("transcript_not_ready", "The asset does not have a ready transcript.", 409)
    transcript = session.scalar(
        select(Transcript)
        .where(Transcript.project_id == project_id, Transcript.asset_id == asset_id)
        .order_by(Transcript.created_at.desc())
    )
    if transcript is None or transcript.provider != "faster_whisper" or transcript.model != "small":
        raise APIError(
            "fast_transcript_not_found", "The asset has no fast Whisper transcript to improve.", 409
        )

    active_job = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.project_id == project_id,
            ProcessingJob.asset_id == asset_id,
            ProcessingJob.kind == "retranscription",
            ProcessingJob.status.in_(("queued", "running", "retry_queued")),
        )
    )
    if active_job is not None:
        raise APIError(
            "transcription_in_progress", "A transcription job is already in progress.", 409
        )

    previous_quality = project.transcription_quality
    previous_status = project.status
    job = ProcessingJob(
        project_id=project_id,
        asset_id=asset_id,
        kind="retranscription",
        status="queued",
        idempotency_key=f"retranscribe:{asset_id}:{uuid4()}",
    )
    session.add(job)
    session.flush()
    session.add(ProcessingStage(job_id=job.id, name="transcribe", status="queued"))
    project.transcription_quality = "accurate"
    project.status = "processing"
    session.commit()
    try:
        queue.enqueue(job.id, "transcribe")
    except Exception as exc:
        job.status = "failed"
        job.error_code = "queue_error"
        job.error_message = "The transcription job could not be queued."
        project.transcription_quality = previous_quality
        project.status = previous_status
        session.commit()
        raise APIError(
            "job_queue_unavailable", "The transcription job could not be queued.", 503
        ) from exc
    return job


def get_project_or_error(session: Session, project_id: UUID) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise APIError("project_not_found", "The project was not found.", 404)
    return project


def get_asset_or_error(session: Session, project_id: UUID, asset_id: UUID) -> MediaAsset:
    asset = session.scalar(
        select(MediaAsset).where(MediaAsset.id == asset_id, MediaAsset.project_id == project_id)
    )
    if asset is None:
        raise APIError("asset_not_found", "The media asset was not found.", 404)
    return asset


def get_job_or_error(session: Session, job_id: UUID) -> ProcessingJob:
    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise APIError("job_not_found", "The processing job was not found.", 404)
    return job


def ingest_received(
    session: Session,
    storage: LocalFilesystemStorage,
    probe_reader: FFprobeMetadataReader,
    queue: JobQueue,
    settings: Settings,
    project_id: UUID,
    received: ReceivedFile,
) -> tuple[MediaAsset, ProcessingJob]:
    """Turn a streamed upload into an asset and an ingest job (SPEC-001 R-INGEST, R-QUEUE).

    The temp file is renamed into place (one copy), probed, and only then are rows written, in one transaction:
    asset, job, ``probe`` queued and ``transcribe`` blocked. A failed enqueue leaves ``probe`` queued with no
    ``enqueued_at`` for recovery; the upload still succeeds.
    """

    # Lock the project row: quality is re-checked here and PATCH /projects/{id} takes the same lock.
    project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise APIError("project_not_found", "The project was not found.", 404)
    if project.transcription_quality not in settings.allowed_qualities:
        raise APIError(
            "quality_unavailable", "The project's transcription quality is not available here.", 422
        )
    try:
        normalized_type = validate_media_type(
            received.content_type, received.filename, settings.allowed_mime_types
        )
    except UploadValidationError as exc:
        raise APIError(
            "asset_type_not_supported", "The uploaded file type is not supported.", 415
        ) from exc

    asset_id = uuid4()
    storage_key = (
        f"projects/{project_id}/original/{asset_id}{Path(received.filename).suffix.lower()}"
    )
    try:
        stored = storage.adopt(
            received.path,
            storage_key,
            normalized_type,
            size_bytes=received.size_bytes,
            sha256=received.sha256,
        )
    except (OSError, StoragePathError) as exc:
        raise APIError(
            "asset_storage_error", "The uploaded file could not be stored.", 500
        ) from exc
    try:
        try:
            metadata = probe_reader.read(storage.local_path(storage_key))
        except MediaProbeError as exc:
            raise APIError(
                "asset_not_readable", "The uploaded file is not a readable video.", 422
            ) from exc
        asset = MediaAsset(
            id=asset_id,
            project_id=project_id,
            storage_key=stored.storage_key,
            original_filename=received.filename,
            media_type=normalized_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            metadata_json=asdict(metadata),
            status="uploaded",
        )
        job = ProcessingJob(
            project_id=project_id,
            asset_id=asset.id,
            kind="ingest",
            status="queued",
            idempotency_key=f"ingest:{asset.id}:{asset.sha256}",
        )
        session.add(asset)
        session.add(job)
        session.flush()
        probe = ProcessingStage(job_id=job.id, name="probe", status="queued")
        session.add(probe)
        session.add(ProcessingStage(job_id=job.id, name="transcribe", status="blocked"))
        session.commit()
    except BaseException:
        session.rollback()
        storage.delete(storage_key)
        raise
    try:
        queue.enqueue(job.id, "probe")
    except Exception:  # noqa: BLE001 - recover_jobs re-sends a queued stage without enqueued_at
        logger.warning("enqueue of probe for job %s failed; recovery will resend it", job.id)
    else:
        probe.enqueued_at = datetime.now(UTC)
        session.commit()
    return asset, job
