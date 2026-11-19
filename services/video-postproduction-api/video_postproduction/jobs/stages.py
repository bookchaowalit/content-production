"""Probe and transcript stage state transitions."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import Settings
from ..edl import validate_edl
from ..error_reporting import report_exception
from ..media.ffprobe import FFprobeMetadataReader, MediaProbeError
from ..media.models import AudioStreamMetadata, MediaMetadata, VideoStreamMetadata
from ..models import (
    EDL,
    MediaAsset,
    ProcessingJob,
    ProcessingStage,
    Project,
    Transcript,
    TranscriptSegment,
)
from ..observability import emit_job_event
from ..storage.base import StorageProvider
from ..transcription.base import TranscriptionProvider, TranscriptionProviderError
from ..transcription.models import NormalizedTranscript, TranscriptValidationError
from .queue import JobQueue

_logger = logging.getLogger(__name__)

# Ingest stage order (SPEC-001 R-QUEUE): a stage may start only after the previous one succeeded.
# A retranscription job has only the transcribe stage, so the order applies to ingest jobs.
PREVIOUS_STAGE = {"transcribe": "probe"}
NEXT_STAGE = {"probe": "transcribe"}
ORDERED_KINDS = frozenset({"ingest"})
# Job kinds whose stages take leases; render stages join once RenderService uses leases.
LEASED_KINDS = frozenset({"ingest", "retranscription"})


def previous_stage(job_kind: str, stage_name: str) -> str | None:
    return PREVIOUS_STAGE.get(stage_name) if job_kind in ORDERED_KINDS else None


def next_stage(job_kind: str, stage_name: str) -> str | None:
    return NEXT_STAGE.get(stage_name) if job_kind in ORDERED_KINDS else None


class LeaseLost(Exception):
    """Raised when this worker no longer holds the stage lease (SPEC-001 R-LEASE); nothing may be written."""


def _now() -> datetime:
    return datetime.now(UTC)


def _metadata_to_json(metadata: MediaMetadata) -> dict[str, Any]:
    return asdict(metadata)


def _metadata_from_json(raw: dict[str, Any]) -> MediaMetadata:
    video_raw = raw.get("video") or {}
    audio_raw = raw.get("audio")
    audio = None
    if isinstance(audio_raw, dict):
        audio = AudioStreamMetadata(
            codec_name=audio_raw.get("codec_name"),
            sample_rate=audio_raw.get("sample_rate"),
            channels=audio_raw.get("channels"),
        )
    return MediaMetadata(
        duration=float(raw.get("duration", 0)),
        format_name=raw.get("format_name"),
        size_bytes=raw.get("size_bytes"),
        bitrate=raw.get("bitrate"),
        video=VideoStreamMetadata(
            codec_name=video_raw.get("codec_name"),
            width=int(video_raw.get("width", 0)),
            height=int(video_raw.get("height", 0)),
            fps=video_raw.get("fps"),
            pix_fmt=video_raw.get("pix_fmt"),
        ),
        audio=audio,
    )


class StageRunner:
    """Run one persisted stage with safe failure and retry state transitions."""

    def __init__(
        self,
        session: Session,
        *,
        storage: StorageProvider,
        probe_reader: FFprobeMetadataReader,
        transcription_provider: TranscriptionProvider,
        settings: Settings | None = None,
        queue: JobQueue | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.probe_reader = probe_reader
        self.transcription_provider = transcription_provider
        self.settings = settings or Settings()
        self.queue = queue

    def _get_job(self, job_id: UUID) -> ProcessingJob:
        job = self.session.get(ProcessingJob, job_id)
        if job is None:
            raise ValueError("processing job not found")
        return job

    def _get_stage(self, job_id: UUID, stage_name: str) -> ProcessingStage:
        stage = self.session.scalar(
            select(ProcessingStage).where(
                ProcessingStage.job_id == job_id,
                ProcessingStage.name == stage_name,
            )
        )
        if stage is None:
            raise ValueError(f"processing stage not found: {stage_name}")
        return stage

    def _claim(self, job: ProcessingJob, stage: ProcessingStage) -> str | None:
        """Take the stage lease with a conditional update; None when the stage is not ready to run."""

        previous = previous_stage(job.kind, stage.name)
        if previous is not None and self._get_stage(job.id, previous).status != "succeeded":
            return None
        token = str(uuid4())
        now = _now()
        claimed = self.session.execute(
            update(ProcessingStage)
            .where(ProcessingStage.id == stage.id, ProcessingStage.status == "queued")
            .values(
                status="running",
                lease_token=token,
                attempt=ProcessingStage.attempt + 1,
                started_at=now,
                heartbeat_at=now,
                finished_at=None,
                error_code=None,
                error_message=None,
            )
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            self.session.rollback()
            return None
        job.status = "running"
        job.started_at = job.started_at or now
        job.error_code = None
        job.error_message = None
        self.session.commit()
        self.session.refresh(stage)
        emit_job_event("stage_started", job_id=job.id, stage=stage.name, status=stage.status)
        return token

    def _guard(self, stage: ProcessingStage, token: str) -> None:
        """Re-check the lease inside the current transaction; the row stays locked until commit."""

        held = self.session.execute(
            update(ProcessingStage)
            .where(
                ProcessingStage.id == stage.id,
                ProcessingStage.status == "running",
                ProcessingStage.lease_token == token,
            )
            .values(heartbeat_at=_now())
            .execution_options(synchronize_session=False)
        )
        if held.rowcount != 1:
            self.session.rollback()
            emit_job_event(
                "stage_lease_lost", job_id=stage.job_id, stage=stage.name, status="lease_lost"
            )
            raise LeaseLost(stage.name)

    @contextmanager
    def _heartbeat(self, stage: ProcessingStage, token: str) -> Iterator[threading.Event]:
        """Refresh the lease heartbeat from a separate session while slow work runs (SPEC-001 R-LEASE).

        The yielded event is set when the lease was lost; the caller then stops before writing anything.
        """

        lost = threading.Event()
        interval = self.settings.stage_heartbeat_seconds
        if interval <= 0:
            yield lost
            return
        stop = threading.Event()
        bind = self.session.get_bind()

        def beat() -> None:
            while not stop.wait(interval):
                try:
                    with Session(bind) as other:
                        held = other.execute(
                            update(ProcessingStage)
                            .where(
                                ProcessingStage.id == stage.id,
                                ProcessingStage.status == "running",
                                ProcessingStage.lease_token == token,
                            )
                            .values(heartbeat_at=_now())
                        )
                        other.commit()
                    if held.rowcount != 1:
                        lost.set()
                        return
                except Exception:  # noqa: BLE001 - a missed beat is retried; recovery tolerates 3 misses
                    _logger.warning("heartbeat for stage %s failed", stage.id)

        thread = threading.Thread(target=beat, name=f"heartbeat-{stage.name}", daemon=True)
        thread.start()
        try:
            yield lost
        finally:
            stop.set()
            thread.join(interval + 5)

    def _finish_stage(
        self, job: ProcessingJob, stage: ProcessingStage, token: str, *, progress: int
    ) -> ProcessingStage | None:
        """Finish a stage and hand off to the next one (or finish the job) in one guarded commit."""

        self._guard(stage, token)
        now = _now()
        stage.status = "succeeded"
        stage.finished_at = now
        stage.lease_token = None
        stage.error_code = None
        stage.error_message = None
        job.progress = progress
        job.error_code = None
        job.error_message = None
        following = None
        next_name = next_stage(job.kind, stage.name)
        if next_name is not None:
            following = self._get_stage(job.id, next_name)
            if following.status == "blocked":
                following.status = "queued"
                following.enqueued_at = None
        else:
            job.status = "succeeded"
            job.finished_at = now
        self.session.commit()
        emit_job_event("stage_finished", job_id=job.id, stage=stage.name, status=stage.status)
        return following if following is not None and following.status == "queued" else None

    def _send(self, job: ProcessingJob, stage: ProcessingStage) -> None:
        """Enqueue after the commit; a failure leaves ``enqueued_at`` empty for recovery (R-QUEUE)."""

        if self.queue is None:
            return
        try:
            self.queue.enqueue(job.id, stage.name)
        except Exception:  # noqa: BLE001 - broker errors are recovered by recover_jobs
            _logger.warning(
                "enqueue of %s for job %s failed; recovery will resend it", stage.name, job.id
            )
            return
        self.session.execute(
            update(ProcessingStage)
            .where(ProcessingStage.id == stage.id, ProcessingStage.status == "queued")
            .values(enqueued_at=_now())
            .execution_options(synchronize_session=False)
        )
        self.session.commit()

    def _fail_stage(
        self,
        job: ProcessingJob,
        stage: ProcessingStage,
        token: str,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        self._guard(stage, token)
        fail_job(self.session, job, stage, error_code=error_code, error_message=error_message)
        self.session.commit()
        emit_job_event(
            "stage_failed",
            job_id=job.id,
            stage=stage.name,
            status=stage.status,
            fields={"error_code": error_code},
        )

    def run_probe(self, job_id: UUID) -> None:
        job = self._get_job(job_id)
        stage = self._get_stage(job_id, "probe")
        token = self._claim(job, stage)
        if token is None:
            return
        try:
            asset = self.session.get(MediaAsset, job.asset_id)
            if asset is None:
                self._fail_stage(
                    job,
                    stage,
                    token,
                    error_code="asset_not_found",
                    error_message="Media asset not found",
                )
                return
            try:
                with self._heartbeat(stage, token) as lost:
                    metadata = self.probe_reader.read(self.storage.local_path(asset.storage_key))
                if lost.is_set():
                    raise LeaseLost(stage.name)
            except LeaseLost:
                raise
            except MediaProbeError as exc:
                self._fail_stage(
                    job,
                    stage,
                    token,
                    error_code="media_probe_error",
                    error_message=exc.safe_message,
                )
                return
            except Exception:  # noqa: BLE001 - convert unexpected stage failures to safe state
                self._fail_stage(
                    job,
                    stage,
                    token,
                    error_code="stage_error",
                    error_message="Media probe stage failed",
                )
                return
            self._guard(stage, token)
            asset.metadata_json = _metadata_to_json(metadata)
            asset.status = "probed"
            following = self._finish_stage(job, stage, token, progress=50)
        except LeaseLost:
            return
        if following is not None:
            self._send(job, following)

    @staticmethod
    def _transcript_fingerprint(asset: MediaAsset, transcript: NormalizedTranscript) -> str:
        material = {
            "asset_sha256": asset.sha256,
            "stage": "transcribe",
            "provider": transcript.provider,
            "model": transcript.model,
            "language": transcript.language,
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()

    @staticmethod
    def _initial_edl(
        project_id: UUID, asset: MediaAsset, metadata: MediaMetadata
    ) -> dict[str, Any]:
        duration = metadata.duration
        if duration <= 0:
            raise ValueError("media duration must be positive for an initial EDL")
        end = min(duration, 10.0)
        fps = metadata.video.fps or 30.0
        document: dict[str, Any] = {
            "version": 1,
            "project_id": str(project_id),
            "source_assets": [
                {
                    "asset_id": str(asset.id),
                    "storage_key": asset.storage_key,
                    "duration": duration,
                }
            ],
            "timeline": [
                {
                    "id": "segment_0001",
                    "source": {
                        "asset_id": str(asset.id),
                        "start": 0.0,
                        "end": end,
                    },
                    "visual": {
                        "kind": "a_roll",
                        "reframe": {"mode": "center", "aspect_ratio": "9:16"},
                        "asset_refs": [],
                    },
                    "captions": {
                        "enabled": True,
                        "source": "transcript",
                        "format": "ass",
                        "preset": "minimal",
                    },
                    "audio": {"operations": []},
                    "color": {"operations": []},
                    "provenance": {"reason": "initial source range"},
                }
            ],
            "assets": [],
            "output": {"width": 1080, "height": 1920, "fps": fps, "format": "mp4"},
            "capabilities": {"required": ["cut", "captions", "center_reframe"]},
        }
        return validate_edl(document).model_dump(mode="json")

    def run_transcribe(self, job_id: UUID) -> None:
        job = self._get_job(job_id)
        stage = self._get_stage(job_id, "transcribe")
        token = self._claim(job, stage)
        if token is None:
            return
        try:
            self._run_transcribe(job, stage, token)
        except LeaseLost:
            return

    def _run_transcribe(self, job: ProcessingJob, stage: ProcessingStage, token: str) -> None:
        asset = self.session.get(MediaAsset, job.asset_id)
        project = self.session.get(Project, job.project_id)
        if asset is None or project is None:
            self._fail_stage(
                job,
                stage,
                token,
                error_code="asset_not_found",
                error_message="Media asset not found",
            )
            return
        try:
            metadata = _metadata_from_json(asset.metadata_json)
            with self._heartbeat(stage, token) as lost:
                transcript = self.transcription_provider.transcribe(
                    self.storage.local_path(asset.storage_key),
                    metadata,
                    language=self.settings.transcription_language,
                )
            if lost.is_set():
                raise LeaseLost(stage.name)
            # Re-check (and lock) the lease before reading or writing any transcript rows.
            self._guard(stage, token)
            fingerprint = self._transcript_fingerprint(asset, transcript)
            persisted_transcript = self.session.scalar(
                select(Transcript).where(
                    Transcript.asset_id == asset.id,
                    Transcript.content_fingerprint == fingerprint,
                )
            )
            if persisted_transcript is None:
                persisted_transcript = Transcript(
                    project_id=project.id,
                    asset_id=asset.id,
                    provider=transcript.provider,
                    model=transcript.model,
                    language=transcript.language,
                    duration=transcript.duration,
                    normalized_json=transcript.model_dump(mode="json"),
                    content_fingerprint=fingerprint,
                )
                self.session.add(persisted_transcript)
                self.session.flush()
                for ordinal, segment in enumerate(transcript.segments):
                    self.session.add(
                        TranscriptSegment(
                            transcript_id=persisted_transcript.id,
                            ordinal=ordinal,
                            start=segment.start,
                            end=segment.end,
                            text=segment.text,
                            words_json=[word.model_dump(mode="json") for word in segment.words],
                        )
                    )
            self._guard(stage, token)
            existing_edl = self.session.scalar(
                select(EDL)
                .where(EDL.project_id == project.id, EDL.source_asset_id == asset.id)
                .order_by(EDL.version.desc())
            )
            if existing_edl is None:
                self.session.add(
                    EDL(
                        project_id=project.id,
                        source_asset_id=asset.id,
                        version=1,
                        schema_version="edl.v1",
                        document=self._initial_edl(project.id, asset, metadata),
                        status="draft",
                    )
                )
            asset.status = "transcribed"
            project.status = "ready_for_edit"
            self._finish_stage(job, stage, token, progress=100)
        except LeaseLost:
            raise
        except TranscriptionProviderError as exc:
            self._fail_stage(
                job,
                stage,
                token,
                error_code="transcription_provider_error",
                error_message=exc.safe_message,
            )
        except TranscriptValidationError as exc:
            # Validation messages describe structure only (ranges, types), never transcript text.
            _logger.warning("transcript validation failed for job %s: %s", job.id, exc)
            self._fail_stage(
                job,
                stage,
                token,
                error_code="transcript_invalid",
                error_message=f"Transcript from provider failed validation: {exc}",
            )
        except (
            Exception
        ) as exc:  # convert unexpected stage failures to safe state, keeping the traceback in logs
            _logger.exception("transcription stage failed unexpectedly for job %s", job.id)
            report_exception(exc)
            self._fail_stage(
                job,
                stage,
                token,
                error_code="stage_error",
                error_message="Transcription stage failed",
            )

    def retry_stage(self, job_id: UUID, stage_name: str) -> ProcessingJob:
        job = self._get_job(job_id)
        stage = self._get_stage(job_id, stage_name)
        if stage.status != "failed":
            raise ValueError("only failed stages can be retried")
        if stage.attempt >= self.settings.max_stage_attempts:
            raise ValueError("stage retry limit reached")
        stage.status = "queued"
        stage.error_code = None
        stage.error_message = None
        stage.finished_at = None
        stage.enqueued_at = None
        following = next_stage(job.kind, stage_name)
        while following is not None:
            later = self._get_stage(job_id, following)
            if later.status == "skipped":
                later.status = "blocked"
            following = next_stage(job.kind, following)
        job.status = "retry_queued"
        job.error_code = None
        job.error_message = None
        job.finished_at = None
        self.session.commit()
        self._send(job, stage)
        return job


def fail_job(
    session: Session,
    job: ProcessingJob,
    stage: ProcessingStage,
    *,
    error_code: str,
    error_message: str,
) -> None:
    """Terminal failure in the caller's transaction: stage and job failed, later blocked stages skipped."""

    now = _now()
    stage.status = "failed"
    stage.finished_at = now
    stage.lease_token = None
    stage.error_code = error_code
    stage.error_message = error_message
    job.status = "failed"
    job.finished_at = now
    job.error_code = error_code
    job.error_message = error_message
    for later in session.scalars(
        select(ProcessingStage).where(
            ProcessingStage.job_id == job.id, ProcessingStage.status == "blocked"
        )
    ):
        later.status = "skipped"
