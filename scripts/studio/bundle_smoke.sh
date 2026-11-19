#!/usr/bin/env bash
# SPEC-001 R-CI-2 / AC-24: build and start the stack from the staged desktop runtime bundle (not the checkout),
# then check API readiness, the web health route and a worker ping. Leaves nothing behind.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
PROJECT=studio-bundle-smoke

(cd "$REPO/apps/video-postproduction-desktop" && node scripts/stage-runtime.js >/dev/null)
BUNDLE="$REPO/apps/video-postproduction-desktop/dist/video-runtime"
COMPOSE=(docker compose -p "$PROJECT" -f "$BUNDLE/services/video-postproduction/docker-compose.yml")

cleanup() { "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT

"${COMPOSE[@]}" build --quiet
"${COMPOSE[@]}" up -d --wait --wait-timeout 300
"${COMPOSE[@]}" exec -T api python -c \
  "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5).status == 200"
"${COMPOSE[@]}" exec -T web wget -qO- --header "Host: 127.0.0.1:3400" http://127.0.0.1:3000/healthz | grep -q '"ok"'
"${COMPOSE[@]}" exec -T worker celery -A video_postproduction.jobs.tasks.celery_app inspect ping --timeout 10 >/dev/null
for service in api worker web; do
  uid=$("${COMPOSE[@]}" exec -T "$service" sh -c 'grep "^Uid:" /proc/1/status | cut -f2')
  [ "$uid" = "10001" ] || { echo "$service runs as uid $uid" >&2; exit 1; }
done
echo "bundle smoke ok"
