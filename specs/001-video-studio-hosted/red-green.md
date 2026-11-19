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

## Review R7 fixes — 2026-11-19

- **Red** (new tests before the fixes): `test_retranscription_job_runs_without_a_probe_stage`,
  `test_recovery_leaves_render_stages_alone`, `test_takeover_is_refused_when_the_worker_finished_meanwhile`,
  `test_heartbeat_thread_keeps_a_long_stage_alive`, the retranscription seed in `test_migration_0004`,
  `test_body_without_the_closing_boundary_is_refused`, `test_oversized_part_headers_are_refused`,
  `test_sqlite_in_a_missing_directory_is_created` — all failed.
- **Green**: fixes listed in `review-R7.md` #1–#11. Full API suite 194 passed, 4 skipped; migration tests pass on
  Postgres 16 again.

## Streaming upload route (R-INGEST, part 2) — 2026-11-19

- Test: `tests/test_api_upload_stream.py` (8 cases).
- **Red**: 7 of 8 failed against the old `UploadFile` route — transcribe was created `queued`, no 411, no 507,
  unreadable video still spent budget, failed enqueue answered 503 and marked the job failed, the body was
  spooled through `tempfile.SpooledTemporaryFile` / `NamedTemporaryFile` without a directory (container `/tmp`).
- **Green**: the route is async and reads `request.stream()` through `receive_file` into `STORAGE_ROOT/tmp`; checks
  before reading: project exists, `Content-Length` (411 / 413), disk reservation (507), budget reservation (429);
  `ingest_received` renames the temp file into place (`LocalFilesystemStorage.adopt`, one copy), probes, then writes
  asset + job + `probe` queued + `transcribe` blocked in one commit; a failed enqueue leaves `probe` queued without
  `enqueued_at` (202). Budget is confirmed only after the commit. The old `upload_asset` is removed. Full API
  suite: 202 passed, 4 skipped.

## Quality, readiness, inline stream, worker Sentry (R-QUALITY, R-HEALTH, R-INLINE, R-OBS-3) — 2026-11-19

- Test: `tests/test_quality_health_inline.py` (13 cases).
- **Red**: 12 failed (no `qualities` in capabilities, no `quality_unavailable` anywhere, no `PATCH /projects/{id}`,
  the worker loaded a disallowed model, no `/readyz`, asset stream sent `attachment`, no worker init hook).
- **Green**: `TRANSCRIPTION_QUALITIES` setting; 422 `quality_unavailable` on create, upload into a project whose
  quality is not offered, accurate retranscription and retry; the worker fails the stage with
  `quality_unavailable` instead of loading the model; `PATCH /api/v1/projects/{id}` switches quality (409
  `project_busy` while a job is active); `/readyz` checks `SELECT 1` and Redis `PING` and returns only
  `{"status": ...}`; asset stream is `inline`; Celery `worker_process_init` calls `init_error_reporting()`. Two
  capability assertions in `test_api_projects.py` now include `qualities`. Full API suite: 215 passed, 4 skipped.

## Render again after a failure (R-RERENDER) — 2026-11-19

- Test: `tests/test_render_retry.py` (5 cases).
- **Red**: collection error (no `RenderSuperseded` / `RenderService.retry`); then the route test answered 200 for
  a superseded render.
- **Green**: `RenderService.enqueue` locks the EDL row, looks at the whole key family (`render:<asset>:<edl>:<v>`
  and `…:retry<n>`), returns the newest render unless it failed, otherwise creates `…:retry<n>`;
  `RenderService.retry` retries in place only when the job is the newest of its family, resetting render row, stage
  and job in one commit; `POST /jobs/{id}/retry` on a render job uses it (409 `render_superseded`). Full API suite:
  220 passed, 4 skipped. Render stages still take no lease (recovery leaves them alone, R-LEASE).

## Web: launcher guard, same-origin proxy, relative URLs, quality UI (F1, F2, R-QUALITY UI) — 2026-11-19

- Tests: `apps/video-postproduction-web/server/guard.test.mjs` (17), `lib/server/proxy.test.ts` (11), new cases in
  `lib/api.test.ts` (5); desktop `tests/studio-csp.test.js` updated.
- **Red**: guard and proxy suites failed to load (modules missing); the relative-URL and quality client cases
  failed against the old `lib/api.ts`.
- **Green**:
  - `server/guard.mjs`: `loadConfig` (fail closed on unset/unknown mode or missing owner/host), `checkRequest`
    (raw path 400 → Host 421 → `/healthz` exempt → owner identity 401 in `tailscale` mode → same-origin check 403 for
    writes, in both modes), `patchCreateServer` (guards every request at the HTTP entry; `requestTimeout` 2 h,
    idle `timeout` 10 min). `server-launch.mjs` uses it and refuses to start on bad settings.
  - `lib/server/proxy.ts` (node:http): server token only, header allow-lists both ways, content-type allow-list
    (others → `application/octet-stream` + `attachment`), `no-store` + `nosniff` + CSP sandbox, HEAD → null body,
    client abort / upstream error handled (499 / 502), query re-serialised, bad segments 400 without an upstream
    call. `app/api/v1/[...path]/route.ts` re-checks with the guard; `app/healthz` calls API `/readyz`;
    `instrumentation.ts` validates settings.
  - `lib/api.ts` uses relative URLs; `NEXT_PUBLIC_VIDEO_POSTPRODUCTION_API_ORIGIN` removed from the app,
    `Dockerfile.web`, Compose and the desktop smoke override; desktop CSP is `connect-src 'self'`; desktop bundle
    lists the new web files.
  - Quality: capabilities carry `qualities`; the create form offers only those; the editor offers
    "เปลี่ยนเป็น fast" for a project on an unavailable quality (`PATCH /projects/{id}`).
  - `Dockerfile.web`: standalone output, `node server-launch.mjs`, uid/gid 10001, healthcheck on `/healthz` with an
    allowed Host. Next bumped 16.4.2 → 16.4.6 (GHSA-mfr8-8qww-v76h, GHSA-vc7v-qv4w-wrv8, moderate; the app does not
    use `use cache`, bumped anyway; `npm audit --omit=dev` clean).
- Built image smoke (fake API): page without identity 401, owner 200, wrong Host 421, `/healthz` ok, cross-site
  POST 403, same-origin 50 MB POST through the proxy with the token added, raw `%2e%2e` 400, Docker health
  `healthy`, process uid/gid 10001, unset mode → exit 1. Web: 50 tests, `tsc` clean, build ok. Desktop: 109 tests.

## Retention sweep and purged media (F5, R-RET-1…6) — 2026-11-19

- Test: `tests/test_retention.py` (9 cases).
- **Red**: collection error (no `video_postproduction.retention`); then the route cases returned 404 / 202 and the
  workers ran on purged sources.
- **Green**: `retention.sweep` marks expired sources (30 d) and renders (90 d) with `purged_at`, commits, then
  deletes the files; skips sources with an unfinished job or render; finishes files of rows an interrupted run
  marked; removes temp files older than 1 h (an active upload writes at least every 10 min — the web idle timeout —
  so no marker files are needed) and source files without a row older than 3 h; reports free-disk ratio and
  `ok:false` under 20 %. CLI `python -m video_postproduction.retention sweep [--dry-run]` prints one JSON line and
  exits 1 when not ok. Purged media: asset stream, render stream/download and `job_file_route` for `render.mp4` →
  410 `media_purged`; render enqueue, accurate retranscription and retry of a purged source → 409 `source_purged`;
  probe, transcribe and render workers fail the stage with `source_purged`. Silence cut stays allowed (it edits the
  EDL from the transcript only). Full API suite: 232 passed, 4 skipped.

## Worker schema wait, API image on uv + non-root (R-SCHEMA-WAIT, R-NONROOT, R-CI-2) — 2026-11-19

- Tests: three `wait_for_schema` cases in `tests/test_migrate_wrapper.py`.
- **Red**: `wait_for_schema` did not exist.
- **Green**: `migrate.wait_for_schema` waits (5 s steps, 5 min) until the database is at this image's head or
  newer; Celery `worker_init` refuses to start on timeout. Base Compose: worker `depends_on` API healthy, API uses
  the image CMD (migrate wrapper, then uvicorn), cache volume at `/app/cache`.
- API image: `uv.lock` (90 packages) with `solo-empire-production-trace` as a path source, `uv sync --frozen
  --no-dev --no-editable --extra transcription --extra sentry`; entrypoint chowns `/app/storage` and `/app/cache`
  once when their owner is not 10001, then `setpriv` to 10001:10001; `HF_HOME`, `PYTHAINLP_DATA` and `HOME` under
  `/app/cache`.
- Image checks (Docker, Postgres 16, Redis 7): on a volume holding a root-owned mode-600 file from the old image,
  the API ran as uid/gid 10001, the file became 10001-owned and stayed readable, migration reached
  `0004_hosted_studio`, `/readyz` answered ok; the worker started as 10001 after the schema check and answered
  `celery inspect ping`. The first run failed because `pythainlp` writes to `$HOME` (`/root`), fixed by `HOME` /
  `PYTHAINLP_DATA`. Desktop bundle lists `uv.lock`, `docker-entrypoint.sh` and migration 0004; desktop 109 tests,
  compose tests pass.

## Production overlay, host scripts, CI gates (F4, F6 host side, F8) — 2026-11-19

- Tests: `services/video-postproduction/tests/test_prod_overlay.py` (2), `services/video-postproduction-api/tests/
  test_check_module_coverage.py` (3), retention CLI case; `scripts/studio/bundle_smoke.sh`.
- **Red**: overlay tests failed (no `check_config.py`); module-gate tests failed (no script); the first bundle
  smoke run failed — the staged desktop bundle drops the executable bit, so `docker-entrypoint.sh` could not run.
- **Green**:
  - `docker-compose.prod.yml`: no published ports, `tailscale` service (pinned 1.104.7 digest, userspace,
    `TS_AUTH_ONCE`, Serve config, health endpoint), web in its network namespace on 127.0.0.1, per-service
    `env_file` + `environment: !override`, restart and limits, images by `STUDIO_TAG`; `tailscale/serve.json`
    (443 → 127.0.0.1:3000, Funnel off).
  - `scripts/studio/check_config.py` renders the merged config with sentinel env files and fails when a secret
    reaches another service, a base default shadows it, a port is published, or limits/tags are missing.
  - `scripts/studio/render_env.py` (Infisical → per-service mode-600 files, never prints values),
    `studio.sh` (update with worker stop first, up/down, retention, backup, worker-ping; heartbeats), and
    `health-probe.sh` (beelink, real HTTPS path); `deploy/systemd/` units and timers; `docs/runbooks/video-studio.md`.
  - API image copies the entrypoint with `--chmod=0755`; bundle smoke then passed: stack built and started from the
    staged bundle, `/readyz` ok, web `/healthz` ok, worker ping, api/worker/web all uid 10001.
  - CI: API job on platform-ci-workflows v1.6.3 with `coverage-threshold: 79` (baseline 80 %) and the per-module
    90 % gate (new modules 93.65–100 %); web job with `require-tests`, `vitest --coverage` and per-file 90 %
    thresholds (proxy 100 %, guard 100 % lines; total 93.8 %); new jobs `video-studio-postgres`,
    `video-studio-config`, `video-studio-bundle-smoke`, `video-studio-api-image`, `video-studio-web-image`
    (image-scan v1.6.3).
- Deviations recorded for spec v1.2: no upload marker files (a live upload writes at least every 10 min, the
  web idle timeout, so temp files older than 1 h are safe to delete); silence cut on a purged source is allowed
  (it only edits the EDL from the transcript). Still open for P3: the restore fix-up/reconcile command and drill,
  the four `HEARTBEATS` entries in `platform-uptime-worker`.

## Review R8 fixes — 2026-11-19

- **Red** (tests first): `tests/test_review_r8.py` (5) failed — transaction held during the stream, no
  `purged_at` in asset responses, raw password in `DATABASE_URL`, no Postgres connect timeout, no readiness
  statement timeout. Web: `server/launch.test.mjs`, `app/api/v1/[...path]/route.test.ts`, `app/healthz/route.test.ts`
  were added as characterisation tests for code that already existed (they passed on first run).
  `lib/server/report-error.test.ts` was written in the same step as `report-error.ts`, so no red run was recorded
  for it.
- **Green**: fixes in `review-R8.md` #1–#13. API 244 passed / 4 skipped; web 59 (per-file gates on proxy, guard,
  launch, report-error, both routes); desktop 109; compose + overlay 4; `check_config.py` ok with the kernel-mode
  rule; containerised restic read a 0600 uid-10001 file and a password with spaces/`$`/quotes; bundle smoke ok.
