"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.errors import (
    APIError,
    api_error_handler,
    http_error_handler,
    internal_error_handler,
    validation_error_handler,
)
from .api.routes import router
from .config import Settings, get_settings
from .error_reporting import init_error_reporting
from .jobs.recovery_loop import RecoveryLoop
from .schemas import HealthResponse

SERVICE_NAME = "video-postproduction-api"
API_VERSION = "1"


def _recover_once(settings: Settings) -> None:
    from .api.dependencies import get_job_queue
    from .db import SessionLocal
    from .jobs.recovery import recover_jobs

    with SessionLocal() as session:
        recover_jobs(session, settings, get_job_queue())


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    init_error_reporting()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # SPEC-001 R-LEASE: recover stale, lost and blocked stages at start and then periodically.
        loop = RecoveryLoop(
            lambda: _recover_once(runtime_settings),
            interval_seconds=runtime_settings.job_recovery_interval_seconds,
        )
        loop.start()
        try:
            yield
        finally:
            loop.stop()

    app = FastAPI(title="AI Post-Production API", version=API_VERSION, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=runtime_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(APIError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, internal_error_handler)

    @app.get("/healthz", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(service=SERVICE_NAME, status="ok", version=API_VERSION)

    app.include_router(router)
    return app


app = create_app()
