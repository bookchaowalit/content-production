"""Celery task entry points with fresh-session boundaries."""

from __future__ import annotations

from uuid import UUID

from celery import Celery  # type: ignore[import-untyped]
from celery.signals import worker_init, worker_process_init  # type: ignore[import-untyped]

from ..config import get_settings
from ..db import SessionLocal
from ..error_reporting import init_error_reporting
from ..media.ffprobe import FFprobeMetadataReader
from ..models import ProcessingJob, Project
from ..render.ffmpeg import FFmpegRenderEngine
from ..render.service import RenderService
from ..storage.local import LocalFilesystemStorage
from ..transcription.faster_whisper import FasterWhisperProvider
from ..transcription.openai_compatible import OpenAICompatibleTranscriptionProvider
from .queue import CeleryJobQueue
from .stages import StageRunner

settings = get_settings()
celery_app = Celery(
    "video-postproduction-api",
    broker=settings.redis_url,
    backend=settings.redis_url,
)


def _provider(transcription_quality: str = "fast"):
    current = get_settings()
    if current.transcription_provider == "faster_whisper":
        model_by_quality = {"fast": "small", "accurate": "medium"}
        try:
            model_name = model_by_quality[transcription_quality]
        except KeyError as exc:
            raise ValueError(f"unsupported transcription quality: {transcription_quality}") from exc
        return FasterWhisperProvider(current, model_name=model_name)
    if current.transcription_provider == "openai_compatible":
        return OpenAICompatibleTranscriptionProvider(current)
    raise ValueError(f"unsupported transcription provider: {current.transcription_provider}")


@worker_init.connect
def on_worker_init(**_kwargs: object) -> None:
    """SPEC-001 R-SCHEMA-WAIT: do not consume the queue before the API has migrated the database."""

    from ..migrate import wait_for_schema

    if wait_for_schema() != "ready":
        raise SystemExit("database schema is not ready for this worker")


@worker_process_init.connect
def on_worker_process_init(**_kwargs: object) -> None:
    """SPEC-001 R-OBS-3: the worker reports errors to Sentry with the same scrubber as the API."""

    init_error_reporting()


@celery_app.task(name="video_postproduction.jobs.tasks.run_stage")
def run_stage(job_id: str, stage_name: str) -> None:
    """Run one stage using a new database session for each task invocation."""

    current = get_settings()
    with SessionLocal() as session:
        job = session.get(ProcessingJob, UUID(job_id))
        project = session.get(Project, job.project_id) if job is not None else None
        transcription_quality = project.transcription_quality if project is not None else "fast"
        runner = StageRunner(
            session,
            storage=LocalFilesystemStorage(current.storage_root),
            probe_reader=FFprobeMetadataReader(current.ffprobe_executable),
            transcription_provider=_provider(transcription_quality),
            settings=current,
            queue=CeleryJobQueue(celery_app),
            transcription_quality=transcription_quality,
        )
        if stage_name == "probe":
            runner.run_probe(UUID(job_id))
        elif stage_name == "transcribe":
            runner.run_transcribe(UUID(job_id))
        elif stage_name == "render":
            render_service = RenderService(
                session,
                storage=LocalFilesystemStorage(current.storage_root),
                engine=FFmpegRenderEngine(
                    current.ffmpeg_executable,
                    probe_reader=FFprobeMetadataReader(current.ffprobe_executable),
                ),
                max_attempts=current.max_stage_attempts,
            )
            render_service.run_job(UUID(job_id))
        else:
            raise ValueError(f"unsupported job stage: {stage_name}")
