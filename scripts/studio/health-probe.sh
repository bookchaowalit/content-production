#!/usr/bin/env bash
# Real-path health probe (SPEC-001 R-OBS-2). Runs every 10 min on beelink, an owner tailnet node the grant allows:
# HTTPS through Tailscale Serve to the web /healthz, which checks the API /readyz (database + Redis).
#   STUDIO_URL=https://video-studio.<tailnet>.ts.net HEARTBEAT_TOKEN_FILE=/etc/video-studio/heartbeat.token health-probe.sh
set -euo pipefail

STUDIO_URL=${STUDIO_URL:?set STUDIO_URL}
MONITOR_URL=${MONITOR_URL:-https://monitor.bookchaowalit.com}
token=$(cat "${HEARTBEAT_TOKEN_FILE:?set HEARTBEAT_TOKEN_FILE}")

if body=$(curl -fsS --max-time 10 "$STUDIO_URL/healthz") && [ "$body" = '{"status":"ok"}' ]; then
  ok=true detail="ok"
else
  ok=false detail="studio not healthy over the tailnet"
fi
curl -fsS --max-time 15 -X POST "$MONITOR_URL/heartbeat/video-studio-health" \
  -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
  --data "{\"ok\": $ok, \"detail\": \"$detail\"}" >/dev/null
