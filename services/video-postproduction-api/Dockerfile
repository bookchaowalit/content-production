# Video Studio API and worker image (SPEC-001 R-NONROOT, R-CI-2).
FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    HF_HOME=/app/cache/huggingface \
    PYTHAINLP_DATA=/app/cache/pythainlp \
    HOME=/app/cache \
    STORAGE_ROOT=/app/storage

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv

# Shared stdlib-only job history/trace package (path dependency in pyproject.toml / uv.lock).
COPY packages/production-trace /app/packages/production-trace
WORKDIR /app/services/video-postproduction-api
COPY services/video-postproduction-api/pyproject.toml services/video-postproduction-api/uv.lock ./
RUN uv sync --frozen --no-dev --no-editable --no-install-project --extra transcription --extra sentry
COPY services/video-postproduction-api/video_postproduction ./video_postproduction
COPY services/video-postproduction-api/migrations ./migrations
COPY services/video-postproduction-api/alembic.ini ./
RUN uv sync --frozen --no-dev --no-editable --extra transcription --extra sentry \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/storage /app/cache/huggingface \
    && chown 10001:10001 /app/storage /app/cache /app/cache/huggingface
COPY services/video-postproduction-api/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["sh", "-c", "python -m video_postproduction.migrate && exec uvicorn video_postproduction.main:app --host 0.0.0.0 --port 8000"]
