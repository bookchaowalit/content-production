#!/usr/bin/env bash
# Hosted Video Studio operations on the homelab host (SPEC-001 F4, F6, F7).
#
#   studio.sh update <tag>    fetch + checkout tag, build images, stop worker, start api, then worker + web
#   studio.sh up | down       start / stop the stack at the current STUDIO_TAG
#   studio.sh retention       daily retention sweep under the maintenance lock, heartbeat video-studio-retention
#   studio.sh backup          pg_dump, then restic of dump + storage to R2, heartbeat video-studio-backup
#   studio.sh worker-ping     celery inspect ping, heartbeat video-studio-worker
#
# Layout: /srv/video-studio/{src,env,run,backup}. Never prints secret values.
set -euo pipefail

STUDIO_HOME=${STUDIO_HOME:-/srv/video-studio}
SRC="$STUDIO_HOME/src"
export STUDIO_ENV_DIR="${STUDIO_ENV_DIR:-$STUDIO_HOME/env}"
LOCK="$STUDIO_HOME/run/maintenance.lock"
MONITOR_URL=${MONITOR_URL:-https://monitor.bookchaowalit.com}
TAG_FILE="$STUDIO_HOME/run/STUDIO_TAG"
COMPOSE_DIR="$SRC/services/video-postproduction"

# Backups hold plaintext dumps before restic encrypts them: owner-only from creation (review R8).
umask 077
install -d -m 0700 "$STUDIO_HOME/run" "$STUDIO_HOME/backup"
RESTIC_IMAGE="restic/restic@sha256:9940e6c7421ab3a21ee9fdc4e91596d04c792b600d31b102696c75cbf3ac4481" # 0.19.1
export STUDIO_TAG="${STUDIO_TAG:-$(cat "$TAG_FILE" 2>/dev/null || true)}"

compose() {
  docker compose -f "$COMPOSE_DIR/docker-compose.yml" -f "$COMPOSE_DIR/docker-compose.prod.yml" "$@"
}

require_compose() {
  local version
  version=$(docker compose version --short | sed 's/^v//')
  # !reset / !override need Compose >= 2.24.4
  if [ "$(printf '%s\n2.24.4\n' "$version" | sort -V | head -1)" != "2.24.4" ]; then
    echo "docker compose $version is older than 2.24.4" >&2
    exit 1
  fi
}

heartbeat() { # job ok(true|false) detail
  local token
  token=$(sed -n 's/^HEARTBEAT_TOKEN=//p' "$STUDIO_ENV_DIR/backup.env")
  curl -fsS --max-time 15 -X POST "$MONITOR_URL/heartbeat/$1" \
    -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
    --data "{\"ok\": $2, \"detail\": \"$3\"}" >/dev/null || echo "heartbeat $1 not delivered" >&2
}

cmd_update() {
  local tag=${1:?usage: studio.sh update <tag>}
  require_compose
  git -C "$SRC" fetch --tags --quiet
  git -C "$SRC" -c advice.detachedHead=false checkout --quiet "refs/tags/$tag"
  export STUDIO_TAG=$tag
  docker build -q -f "$SRC/services/video-postproduction-api/Dockerfile" -t "video-studio-api:$tag" "$SRC"
  docker build -q -f "$SRC/services/video-postproduction/Dockerfile.web" -t "video-studio-web:$tag" "$SRC"
  python3 "$SRC/scripts/studio/check_config.py"
  # R-UPGRADE: stop the old web (no requests to a mismatched API) and the old worker (no hand-offs during the
  # migration) first; a failure here stops the update instead of continuing with two releases.
  compose stop --timeout 1800 web worker
  compose up -d --wait postgres redis tailscale api
  compose up -d --wait worker web
  echo "$tag" > "$TAG_FILE"
  echo "studio at $tag"
}

cmd_up() { require_compose; compose up -d --wait; }
cmd_down() { compose down; }

cmd_retention() {
  local out status=0
  exec 9>"$LOCK"
  if ! flock -w 7200 9; then
    heartbeat video-studio-retention false "maintenance lock busy for 2 h"
    exit 1
  fi
  out=$(compose run --rm --no-deps api python -m video_postproduction.retention sweep) || status=$?
  echo "$out"
  if [ "$status" -eq 0 ]; then
    heartbeat video-studio-retention true "swept"
  else
    heartbeat video-studio-retention false "sweep failed or disk under 20 percent"
  fi
  exit "$status"
}

cmd_backup() {
  local dump="$STUDIO_HOME/backup/studio.dump"
  exec 9>"$LOCK"
  flock 9
  # Dump first: every row in it has its file in the snapshot taken next (R-BAK-1). umask 077 keeps it 0600.
  compose exec -T postgres pg_dump -U video -Fc video_postproduction > "$dump.tmp"
  mv "$dump.tmp" "$dump"
  # restic runs in a container as root with the storage volume mounted read-only, so it can read files owned by
  # uid 10001 with mode 0600; the env file is read by Docker literally (no shell parsing of secrets).
  restic() {
    docker run --rm --env-file "$STUDIO_ENV_DIR/backup.env" \
      -v video-studio_video_storage:/data/storage:ro -v "$STUDIO_HOME/backup:/data/backup:ro" \
      -v video-studio-restic-cache:/root/.cache/restic "$RESTIC_IMAGE" "$@"
  }
  # A fixed --host keeps every snapshot in one group, so `forget` applies the retention policy (review R10).
  if restic backup --quiet --host video-studio --exclude /data/storage/tmp --exclude '/data/storage/**/attempt-*' /data/backup /data/storage \
    && restic forget --quiet --host video-studio --keep-daily 14 --keep-weekly 8 --keep-monthly 6 --prune; then
    heartbeat video-studio-backup true "$(restic stats --mode raw-data --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["total_size"])') bytes"
  else
    heartbeat video-studio-backup false "restic failed"
    exit 1
  fi
}

cmd_worker_ping() {
  if compose exec -T worker celery -A video_postproduction.jobs.tasks.celery_app inspect ping --timeout 10 >/dev/null; then
    heartbeat video-studio-worker true "pong"
  else
    heartbeat video-studio-worker false "no pong"
  fi
}

case "${1:-}" in
  update) shift; cmd_update "$@" ;;
  up) cmd_up ;;
  down) cmd_down ;;
  retention) cmd_retention ;;
  backup) cmd_backup ;;
  worker-ping) cmd_worker_ping ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
