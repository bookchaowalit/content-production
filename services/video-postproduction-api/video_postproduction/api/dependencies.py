"""FastAPI dependency graph for storage, probing, jobs, and database access."""

from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..config import Settings
from ..config import get_settings as load_settings
from ..db import get_session
from ..jobs.queue import CeleryJobQueue, JobQueue
from ..jobs.stages import StageRunner
from ..jobs.tasks import celery_app
from ..media.ffprobe import FFprobeMetadataReader
from ..render.ffmpeg import FFmpegRenderEngine
from ..render.service import RenderService
from ..storage.base import StorageProvider
from ..storage.local import LocalFilesystemStorage
from ..transcription.base import TranscriptionProvider
from ..transcription.faster_whisper import FasterWhisperProvider
from ..transcription.openai_compatible import OpenAICompatibleTranscriptionProvider
from .access import token_is_strong, token_matches
from .errors import APIError
from .limits import transcription_budget, write_limiter

security = HTTPBearer(auto_error=False)


def get_settings() -> Settings:
    return load_settings()


def get_storage(settings: Settings = Depends(get_settings)) -> StorageProvider:
    return LocalFilesystemStorage(settings.storage_root)


def get_probe_reader(settings: Settings = Depends(get_settings)) -> FFprobeMetadataReader:
    return FFprobeMetadataReader(settings.ffprobe_executable)


def get_render_engine(
    settings: Settings = Depends(get_settings),
    probe_reader: FFprobeMetadataReader = Depends(get_probe_reader),
) -> FFmpegRenderEngine:
    return FFmpegRenderEngine(settings.ffmpeg_executable, probe_reader=probe_reader)


def get_render_service(
    session: Session = Depends(get_session),
    storage: StorageProvider = Depends(get_storage),
    render_engine: FFmpegRenderEngine = Depends(get_render_engine),
) -> RenderService:
    return RenderService(session, storage=storage, engine=render_engine)


def get_job_queue() -> JobQueue:
    return CeleryJobQueue(celery_app)


def get_transcription_provider(settings: Settings = Depends(get_settings)) -> TranscriptionProvider:
    if settings.transcription_provider == "faster_whisper":
        return FasterWhisperProvider(settings)
    if settings.transcription_provider == "openai_compatible":
        return OpenAICompatibleTranscriptionProvider(settings)
    raise APIError("provider_not_configured", "The transcription provider is not configured.", 503)


def get_stage_runner(
    session: Session = Depends(get_session),
    storage: StorageProvider = Depends(get_storage),
    probe_reader: FFprobeMetadataReader = Depends(get_probe_reader),
    transcription_provider: TranscriptionProvider = Depends(get_transcription_provider),
    settings: Settings = Depends(get_settings),
    queue: JobQueue = Depends(get_job_queue),
) -> StageRunner:
    return StageRunner(
        session,
        storage=storage,
        probe_reader=probe_reader,
        transcription_provider=transcription_provider,
        settings=settings,
        queue=queue,
    )


def require_write_access(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    settings: Settings = Depends(get_settings),
) -> None:
    """Fail closed: writes need a strong token unless local use opted out explicitly."""

    token = settings.video_editor_api_token
    if token is None and settings.video_editor_allow_unauthenticated:
        pass
    elif token is None or not token_is_strong(token):
        raise APIError("auth_not_configured", "Write access is not configured on this server.", 503)
    elif credentials is None or not token_matches(token, credentials.credentials):
        raise APIError("unauthorized", "Authentication is required.", 401)
    client = request.client.host if request.client else "unknown"
    if not write_limiter.hit(client, limit=settings.write_rate_limit_per_minute, window_seconds=60):
        raise APIError("rate_limited", "Too many write requests. Try again in a minute.", 429)


def require_transcription_budget(settings: Settings = Depends(get_settings)) -> None:
    """Daily cap on queued transcription jobs; refuses once spent (resets at 00:00 UTC)."""

    if not transcription_budget.take(limit=settings.daily_transcription_jobs):
        raise APIError(
            "transcription_budget_exhausted",
            "The daily transcription budget is used up. Try again tomorrow.",
            429,
        )
