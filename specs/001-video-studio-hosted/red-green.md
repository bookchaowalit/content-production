# SPEC-001 red/green log

ADR-0004: each behaviour is written test-first; the red run is recorded before the implementation.

## Migration 0004 (R-SIZE, R-QUEUE / R-LEASE repairs, R-RET-1) — 2026-11-19

- Test: `services/video-postproduction-api/tests/test_migration_0004.py` (SQLite always; Postgres when
  `VIDEO_TEST_POSTGRES_URL` is set).
- **Red** (before `0004_hosted_studio.py` existed): `test_0004_adds_columns_and_repairs_old_rows[sqlite]` failed —
  `size_bytes` reflected as `INTEGER`, not `BigInteger`. (The other two passed trivially because head was 0003.)
- **Green**: migration `0004_hosted_studio` + ORM columns. 6/6 pass on SQLite and on `postgres:16-alpine`
  (throwaway container), including storing 2,147,483,648 in `media_assets.size_bytes` on Postgres and the
  downgrade/upgrade round trip on an empty database. Full API suite still green.

## Migrate wrapper (R-REL-2) — 2026-11-19

- Test: `services/video-postproduction-api/tests/test_migrate_wrapper.py`.
- **Red**: collection error — `video_postproduction.migrate` did not exist.
- **Green**: `video_postproduction/migrate.py`. Empty DB → head; a DB at 0003 → head; a DB stamped with an
  unknown (newer) revision → returns `schema_newer_than_code`, logs a warning, leaves the revision untouched.
  The Compose/Dockerfile start command switches to it in the F4 step.

## Upload reservations and streaming receiver (R-INGEST, part 1) — 2026-11-19

- Tests: `tests/test_upload_reservations.py`, `tests/test_upload_stream.py`.
- **Red**: collection errors — `DiskReservations`, `BudgetReservation` and `api/upload_stream.py` did not exist.
- **Green**: `api/limits.py` (budget reserve/confirm/release, same-day return; thread-safe disk reservation with a
  20 % floor) and `api/upload_stream.py` (callback multipart parser, whole-body and file caps, file part written to
  a temp file in the given directory while hashing, temp removed on any refusal). 10/10 pass. The route switch
  comes next.

## Blocked stages, leases and recovery (R-QUEUE, R-LEASE) — 2026-11-19

- Test: `tests/test_queue_recovery.py` (12 cases) and the updated `tests/test_stage_runner.py`.
- **Red**: collection error — `recover_jobs` did not exist (and the old runner had no claim/lease).
- **Green**: `jobs/stages.py` claims with a conditional `queued → running` update that also needs the previous
  stage `succeeded`, guards every result write with the lease token, hands off to the next stage in the finishing
  commit, finishes the job with the last stage, skips later stages on failure, and retry brings them back;
  `jobs/recovery.py` `recover_jobs` replaces `recover_stale_jobs` (stale heartbeat takeover, last-attempt failure,
  resend of lost messages, blocked-stage heal). The stalled-worker case is driven by a provider that runs recovery
  mid-stage. Existing stage tests now set up probed jobs explicitly (`make_job(..., probed=True)`) and expect
  `retry_queued` after retry. Full suite: 183 passed, 4 skipped. Still open in this area: the heartbeat thread
  and calling `recover_jobs` periodically from the API process.

## Recovery loop in the API process (R-LEASE) — 2026-11-19

- Test: `tests/test_recovery_loop.py`.
- **Red**: collection error — `jobs/recovery_loop.py` did not exist; then the lifespan test failed until `main.py`
  started the loop.
- **Green**: `RecoveryLoop` runs `recover_jobs` at start and every `JOB_RECOVERY_INTERVAL_SECONDS` (default 300,
  0 = off), survives a failing pass (logged and reported); `create_app` starts it in the lifespan and stops it on
  shutdown.

## Spike: streaming proxy on the built standalone image (R-PROXY-2, R-PROXY-5) — 2026-11-19

Throwaway branch `spike/video-studio-proxy` (not merged). Built `apps/video-postproduction-web` as a Next 16.4.2
standalone image (Node 22) started with `node server-launch.mjs`; a fake API counted bytes and served Range.

| Check | Result |
|---|---|
| 2 GiB multipart POST through the proxy (full speed) | 201 in 4.3 s; upstream got all 2,147,483,844 bytes with `Content-Length` (no chunked); web container peak 86 MiB |
| 900 MB POST throttled to 20 Mbit/s with the launcher | 201 after 368 s, all bytes received |
| same upload with plain `node server.js` | **408 after 326 s** — Node's 300 s `requestTimeout` cuts it; the launcher is required |
| `Range: bytes=0-1023` | 206, `Content-Range` passed, `Cache-Control: private, no-store`, `Content-Disposition: inline` |
| HEAD | 200 with `Content-Length`, no body |
| Query string | re-serialised (`a%20b` → `a+b`, same meaning for the API) |
| Raw paths `../`, `%2e%2e`, `%2f`, `%5c`, `%00` | 400 from the launcher before Next |
| API token in page HTML | not present |

Findings that change the implementation (spec text already allows them):

1. The proxy uses `node:http` directly instead of `fetch` + an undici `Agent`: it keeps `Content-Length` on a streamed
   body and has no 300 s body timeout, and no extra dependency is needed.
2. Next's `proxy.ts` / middleware buffers request bodies (docs `proxyClientMaxBodySize`, default 10 MB), confirming
   R-TS-5: no middleware on `/api/v1/`.
3. The first throttled run returned 408 for both servers because the fake API was itself a Node server with the
   300 s default; uvicorn has no such limit, but the E2E test (AC-13) must use the real API.
