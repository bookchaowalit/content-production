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
