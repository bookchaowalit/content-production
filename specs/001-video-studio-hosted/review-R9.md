# SPEC-001 review round R9 — Codex on P1 code up to c0a45ad (2026-11-19)

Each finding was checked against the code.

| # | Sev | Finding | Verified | Fix |
|---|---|---|---|---|
| 1 | P1 | New desktop shell's CSP blocks a stack still running the old web (which calls :8000), while the shell says ready | yes (`health.js` checks title only) | CSP keeps the loopback API origin during the transition (same allowance as before SPEC-001); `recover` stops the worker, then rebuilds |
| 2 | P1 | Ruff PLW1510 in `test_prod_overlay.py` | yes | `check=False` on the three calls |
| 3 | P1 | `wait_for_schema` raised on an unreachable DB, and Celery could start the worker anyway | yes | DB errors count as "not ready yet" and are retried until the timeout; on timeout `worker_init` exits |
| 4 | P2 | Compose interpolates `$` in `env_file` values | yes (checked: `a$HOME` became the home path) | `render_env.py` writes `$$` for Compose files; `backup.env` (read by `docker run --env-file`) stays literal |
| 5 | P2 | No `restic init` step before the backup timer | yes | runbook step 4b |
| 6 | P2 | Retry route reads quality and purge state without locks | yes | `SELECT … FOR UPDATE` on project and source |
| 7 | P2 | Accurate retranscription marked failed on a broker outage, so recovery skipped it | yes | stays queued without `enqueued_at` (202); `recover_jobs` resends it |
| 8 | P2 | A slow multipart field could leave the temp file untouched for over an hour | yes | the receiver touches the temp file at least once a minute while bytes arrive |
| 9 | P3 | Render retry of a purged source answered `job_not_retryable` | yes | 409 `source_purged` (and 503 on a broker error) |
| 10 | P3 | Migrate-wrapper tests never ran on Postgres | yes | fixture runs on Postgres when `VIDEO_TEST_POSTGRES_URL` is set |

Disputed: none.
