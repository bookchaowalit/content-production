"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.dependencies import get_session
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
# Readiness query per dialect; Postgres gets a statement timeout so a stuck server answers degraded (review R8).
READY_STATEMENTS = {
    "postgresql": ("SET LOCAL statement_timeout = 2000", "SELECT 1"),
    "default": ("SELECT 1",),
}
API_VERSION = "1"


def _redis_ping(url: str, timeout: float) -> bool:
    import redis

    try:
        return bool(
            redis.Redis.from_url(url, socket_timeout=timeout, socket_connect_timeout=timeout).ping()
        )
    except Exception:  # noqa: BLE001 - any broker error means not ready
        return False


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

    @app.get("/readyz")
    def ready(session: Annotated[Session, Depends(get_session)]) -> JSONResponse:
        """SPEC-001 R-HEALTH: database and broker answer; the body never carries details."""

        try:
            dialect = session.get_bind().dialect.name
            for statement in READY_STATEMENTS.get(dialect, READY_STATEMENTS["default"]):
                session.execute(text(statement))
            session.rollback()
            database_ok = True
        except Exception:  # noqa: BLE001 - any database error means not ready
            database_ok = False
        broker_ok = _redis_ping(runtime_settings.redis_url, 2.0)
        if database_ok and broker_ok:
            return JSONResponse({"status": "ok"})
        return JSONResponse({"status": "degraded"}, status_code=503)

    app.include_router(router)
    return app


app = create_app()
