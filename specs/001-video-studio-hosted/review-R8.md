# SPEC-001 review round R8 — Codex and the security-guidance plugin on P1 code (2026-11-19)

Scope: `feat/video-studio-hosted` 3e24924..4c659f6. Each finding was checked against the code.

| # | Sev | Source | Finding | Verified | Fix |
|---|---|---|---|---|---|
| 1 | HIGH | plugin | In userspace mode tailscaled forwards tailnet connections to any localhost port, so web on 127.0.0.1:3000 is reachable without Serve and the identity header can be forged | yes (userspace netstack behaviour) | `TS_USERSPACE=false` + `NET_ADMIN` + `/dev/net/tun`; `check_config.py` enforces it; AC-3 checks port 3000 from a tailnet peer |
| 2 | P1 | Codex | restic as `bookchaowalit` cannot read the Docker volume (root-owned path, 0600 files of uid 10001) | yes | restic runs in a pinned container (0.19.1) with the volume read-only; tested on a 0600 uid-10001 file |
| 3 | P2 | both | `pg_dump` file created 0644 | yes | `umask 077`, `install -d -m 0700` |
| 4 | P2 | Codex | Raw password in `DATABASE_URL` breaks on `@`, `/`, `#` | yes | percent-encoded; test parses it back |
| 5 | P2 | Codex | `source backup.env` parses secrets as shell | yes | `docker run --env-file` (literal); tested with a password containing space, `$` and quotes |
| 6 | P2 | Codex | Render enqueue/retry and retranscription do not take the asset lock retention takes | yes | `SELECT … FOR UPDATE` on the asset, re-check `purged_at` |
| 7 | P2 | Codex | Upload holds a DB transaction (pooled connection) for the whole body stream | yes | rollback after the project read; test checks no transaction during the stream |
| 8 | P2 | Codex | `update` keeps the old web serving while the new API starts; `|| true` hides a failed stop | yes | stop web and worker first, no `|| true` |
| 9 | P2 | Codex | Quality switch and new jobs can overtake each other | yes | PATCH and ingest lock the project row; ingest re-checks quality |
| 10 | P2 | Codex | `/readyz` can hang on a stuck Postgres | yes | `connect_timeout=5` on Postgres engines, `SET LOCAL statement_timeout = 2000` in readiness |
| 11 | P2 | Codex | Editor shows a dead player for purged media | yes | `purged_at` in asset responses; editor shows the retention notice |
| 12 | P2 | Codex | Web Sentry not wired | yes | `onRequestError` → errors-only envelope (exception type + route path, no message, request data or query) |
| 13 | P2 | Codex | Route handler and launcher outside the coverage gate | yes | launcher logic moved to `server/launch.mjs` (tested in-process); route, healthz, launch, report-error in per-file 90 % gates |
| 14 | P2 | Codex | Sweep can delete the temp file of a loopback upload paused for over an hour | **disputed** | the sweep runs only on the hosted host (systemd timer); there the API is reachable only through the web proxy, whose idle timeout (10 min) ends a stalled upload long before the 1 h age limit. Local and desktop stacks never run the sweep. |
| 15 | MEDIUM | plugin | Backup directory and dump permissions | yes | same as #3 |
