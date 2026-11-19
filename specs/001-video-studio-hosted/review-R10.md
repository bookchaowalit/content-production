# SPEC-001 review round R10 — Codex on 3e24924..623bb08 (2026-11-19)

Each finding was checked against the code.

| # | Sev | Finding | Verified | Fix |
|---|---|---|---|---|
| 1 | P1 | Compose strips surrounding quotes of env-file values, so Postgres and `DATABASE_URL` could get different passwords | yes (`'"a"b$HOME c'` round-trip tested with `docker compose run … printenv`) | Compose env files write single-quoted literal values; a value containing `'` is refused; `backup.env` stays literal for `docker run --env-file` |
| 2 | P1 | Desktop recover stopped the worker with Compose's 10 s default, killing a running render | yes | `stop --timeout 1800 worker` (Celery warm shutdown), same as `studio.sh update` |
| 3 | P2 | Deadlock: retry locked project then asset while render enqueue held asset and needed the project (FK key-share on insert) | yes | lock order project → (EDL) → asset in render enqueue, render retry, accurate retranscription, ingest retry; test records the lock order |
| 4 | P2 | restic snapshots from a fresh container each get a new hostname, so `forget` never prunes | yes | `--host video-studio` on backup and forget |
| 5 | P2 | Schema wait counted only sleeps and could hang on a stuck connection | yes | `connect_timeout=5` for Postgres and a wall-clock deadline |
| 6 | P2 | A failed `enqueued_at` commit turned a committed upload into a 500 and refunded the budget | yes | best effort: rollback + log, upload succeeds; recovery may resend (duplicate is a no-op) |
| 7 | P3 | Multi-range answers (`multipart/byteranges`) were renamed to octet-stream | yes | type allowed through the proxy |

Disputed: none.
