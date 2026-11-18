# SPEC-001 — Hosted Video Studio on the homelab

Standard: ADR-0004 — TDD + SDD.

## Document control

| Field | Value |
|---|---|
| Spec ID / version | SPEC-001 / 0.5 |
| Status | R1 revise → v0.2; R2 revise → v0.3; Codex on 8f7ff36 revise → v0.4; Codex on 5f6708a revise → v0.5; awaiting R3, then owner approval |
| Repository / path | bookchaowalit/content-production / `specs/001-video-studio-hosted/spec.md` |
| Owner (maker) | Claude Code |
| Reviewer (checker) | Fresh Claude reviewer subagent (no maker context) + Codex at Stop |
| Approver | Owner (bookchaowalit) |
| Requirement links | Knowledge store `plans/content-production-deploy/PLAN.md` (owner approved D1–D6 as recommended, 2026-11-18) |
| Related docs | `PRODUCT.md`; `services/video-postproduction/README.md`; `services/video-postproduction-api/README.md`; `apps/video-postproduction-desktop/` (`scripts/runtime-bundle-manifest.js`, `src/runtime/compose.js`, `src/security/studio-csp.js`); ADR-0004, ADR-0007 (design floor), ADR-0014; PRJ-DSO-02; `platform-uptime-worker` `src/targets.ts` |
| Main-only | no — branch `spec/video-studio-hosted`, then `feat/video-studio-hosted` |
| Created / approved | 2026-11-18 / — |
| Review log | `review-R1.md`, `review-R2.md`, `review-R3a.md`, `review-R3b.md` |

## 1. Problem and intent

- **Problem:** the Video Studio (upload → transcribe → EDL → 9:16 render → export) only runs on a workstation
  through Docker Compose bound to `127.0.0.1`. The owner cannot use it away from that machine, and nothing
  watches, backs up or restores it.
- **Outcome:** the owner opens `https://studio.bookchaowalit.com` from any device, signs in with Cloudflare Access,
  and uses the same studio. It runs on the homelab Fedora host, exposes no port on the LAN, keeps media at home, is
  watched by the uptime monitor, and is backed up to R2 with a tested restore.
- **Out of scope:** other production APIs, `production-console-web`, the `book-production` CLI engines, multi-user
  accounts and any cloud host.
- **R-DESK** The Electron desktop app is not deployed, but it builds the same Dockerfiles and base Compose file from
  its runtime bundle and shows the same web app in a window with its own CSP. Every change here keeps it working:
  bundle builds, `compose ps` parsing, the renderer CSP, upload and render from the desktop window.

### Owner decisions (2026-11-18, from the plan)

| ID | Decision |
|---|---|
| D1 | One user: the owner. Access control = Cloudflare Access allow-list (owner email). No identity/tenancy. |
| D2 | Host = homelab Fedora (`192.168.152.12`, Ryzen 7 4700U 8C, 15 GB RAM, 151 GB free disk). |
| D3 | Lower `ci-runner-local-2` memory limit 6 → 4 GB to make room for the studio. |
| D4 | Transcription stays local (`faster_whisper`, CPU). No media leaves the house. Hosted runs offer `fast` (`small` model) only (R-QUALITY). |
| D5 | Hostname `studio.bookchaowalit.com`. |
| D6 | Retention: source media 30 days, rendered MP4 90 days after creation. Metadata, traces and interchange files are kept. |

### Facts found while writing this spec (2026-11-18, main b2f126a)

1. The web app (`apps/video-postproduction-web/lib/api.ts:152-157`) calls the API **from the browser** at
   `NEXT_PUBLIC_VIDEO_POSTPRODUCTION_API_ORIGIN` and sends **no `Authorization` header**; media URLs
   (`lib/api.ts:333-342`) point at the same origin.
2. The API write gate (`api/dependencies.py:87-103`) fails closed; the Compose stack sets
   `VIDEO_EDITOR_ALLOW_UNAUTHENTICATED=true`. **Reads are open.** Write limit 60/min per `request.client.host`;
   the limiter and the daily transcription budget (`api/limits.py`) are in-process and reset on restart.
3. Upload is one multipart POST (`api/routes.py:221-252`), cap `MAX_UPLOAD_BYTES` = 2 GiB. `upload_asset`
   (`api/services.py`) copies to a temp file, `put_atomic` copies again, then commits asset + job + stages
   (`services.py:183-188`) **before** `queue.enqueue` (`:189-196`); a new asset id every call.
4. `media_assets.size_bytes` is `INTEGER` (`migrations/versions/0001_v1_core.py:36`), one byte short of the cap.
5. Cloudflare free plan: request body ≤ 100 MB; proxy write timeout 30 s (also on Tunnel hostnames); no response
   headers after 100 s → 524; MP4 is cacheable by default.
6. `/healthz` (`main.py:42-44`) is a constant `ok`.
7. Sentry is initialised only in `create_app()` (`main.py:28`), never in the Celery worker.
8. Quality `accurate` makes the worker load Whisper `medium` from the project's stored quality
   (`jobs/tasks.py:29-37`); the create form offers it unconditionally (`project-create-form.tsx:115`).
9. Stream routes send `Content-Disposition: attachment` (`routes.py:290-301`); the API registers GET only.
10. Both Dockerfiles run as root; the model cache is mounted at `/root/.cache/huggingface`; temp files are mode 600
    owned by root. The API image installs `packages/production-trace` separately and the API with extras
    `[transcription,sentry]`.
11. The desktop runtime bundle lists exact files (`runtime-bundle-manifest.js:4-30`); `compose.js:98-123` runs
    `compose ps --all` and throws `unexpected Compose service` for any service outside `SERVICE_NAMES`
    (postgres, redis, api, worker, web). The desktop renderer CSP (`studio-csp.js:3-16`) has
    `script-src 'self' 'unsafe-inline'` and no `wasm-unsafe-eval`, so WebAssembly cannot compile.
12. `StageRunner.retry_stage` accepts only `failed` stages (`jobs/stages.py:334-338`); `recover_stale_jobs`
    (`jobs/recovery.py`) moves stale `running` jobs to `retry_queued` but nothing re-sends a `queued` job whose
    broker message was lost.
13. A render is stored once: `render_outputs.storage_key` is `location.key("render.mp4")` inside the job directory
    (`render/service.py:171-172,240-241,262`), the same file `job_file_route` serves. Every writer (ingest, render)
    writes its files **before** committing the row that points at them.
14. `platform-uptime-worker` answers 404 for a heartbeat name not in `HEARTBEATS`.
15. Fedora runs `ci-runner-local` and `ci-runner-local-2` (6 GB / 7 CPU each) and an unused `cloudflared`. Fedora
    ships zram swap by default (to be confirmed on the host).
16. CI (`.github/workflows/ci.yml`) runs the service/app tests but sets no `require-tests` or `coverage-threshold`.
17. A finished stage queues the next one from the worker (`jobs/stages.py` `run_probe` commits, then
    `queue.enqueue(job.id, "transcribe")`); a crash between the two leaves the job `running` with no stage running.
18. A project's quality can only go `fast → accurate` (`api/services.py` `enqueue_accurate_retranscription`); there is
    no route to set it back.
19. `run_transcribe` commits the stage `succeeded` and then, in a second commit, the job `succeeded`
    (`jobs/stages.py:308-312`). `recover_stale_jobs` has no caller at runtime and judges a lease from
    `job.started_at`, which would also take over a long transcription that is still running.
20. Exports create no database row: `ExportService.export()` writes `export-<id>/` files and a `history.jsonl` line
    only (`exports.py:81-83,120-144`). A `render_outputs` row exists from enqueue; the trace and history line are
    written before `storage_key` and `succeeded` are committed (`render/service.py:261-268,353-361`).

## 2. Requirements and design

### F1 — Same-origin proxy in the web app

- **R-PROXY-1** Route handler `apps/video-postproduction-web/app/api/v1/[...path]/route.ts` (GET, HEAD, POST,
  PATCH, PUT, DELETE) forwards to `VIDEO_POSTPRODUCTION_API_INTERNAL_URL` (default `http://api:8000`), adding
  `Authorization: Bearer <VIDEO_EDITOR_API_TOKEN>` from server-side env only, and no `Authorization` header when the
  token is unset. HEAD is sent upstream as GET; the upstream body is cancelled (`AbortController`) as soon as headers
  arrive, and the client gets headers only.
- **R-PROXY-2** Streaming both ways (`duplex: "half"`, unbuffered response). The first delivery step is a spike that
  runs the **built standalone image with its production command** (`node server.js`) and proves an 8 MiB PUT, a
  `Range` 206 and a HEAD. The Next docs in `node_modules/next/dist/docs` are read first (web `AGENTS.md`).
- **R-PROXY-3** Forwarded request headers: `content-type`, `content-length`, `range`, `if-range`, `if-none-match`,
  `accept`. Returned headers: `content-type`, `content-length`, `content-range`, `accept-ranges`,
  `content-disposition`, `etag`, `retry-after`, `x-request-id`. Everything else is dropped.
- **R-PROXY-4** Every proxied response sets `Cache-Control: private, no-store` and `X-Content-Type-Options:
  nosniff`. A Cloudflare Cache Rule bypasses cache for the hostname.
- **R-PROXY-5** The upstream URL is built on a fixed base from decoded segments; each must match
  `^[A-Za-z0-9_.-]+$` and not be `.` / `..` (refuses `%2e%2e`, encoded `/` `\`, NUL); else 400 `invalid_path`.
  The query string is parsed and re-serialised.
- **R-PROXY-6** In **both** access modes, a non-GET/HEAD request is refused with 403 `cross_origin_refused` unless
  `Sec-Fetch-Site` is `same-origin`, or (header absent) `Origin` is in the allowed set: `STUDIO_PUBLIC_ORIGIN` when
  set, else `http://127.0.0.1:3400` and `http://localhost:3400`. A request with neither header is refused. Scripts
  and smoke tests that write without a browser keep using the API port directly, which the **base** Compose file
  still publishes on `127.0.0.1:8000` for local and desktop use (the prod overlay removes it).
- **R-PROXY-7** `STUDIO_ACCESS_MODE`:
  - `required` (prod overlay): `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, `STUDIO_OWNER_EMAIL`,
    `STUDIO_PUBLIC_ORIGIN` must all be set or the server refuses to start (`instrumentation.ts` `register()` throws;
    the proxy answers 503 `access_not_configured` if reached). Each proxied request needs `Cf-Access-Jwt-Assertion`
    verified with `jose` (pinned): `alg` RS256 only, key by `kid` from `https://<team>/cdn-cgi/access/certs`
    (cached ≤ 1 h, one refetch on unknown `kid`), `iss = https://<team>`, `aud` contains `CF_ACCESS_AUD`,
    `exp`/`nbf` with 60 s leeway, `email` = `STUDIO_OWNER_EMAIL` (case-insensitive); else 401 `access_required`.
  - `off` (set explicitly in the base Compose file, and in a committed `.env.development` with
    `VIDEO_POSTPRODUCTION_API_INTERNAL_URL=http://127.0.0.1:8000` for `npm run dev`; Next loads that file only in
    development): no JWT check, logged once at start. The web `DOCPACK` runbook and browser smoke are updated.
  - Unset or any other value: refuse to start.
- **R-PROXY-8** `lib/api.ts` uses relative `/api/v1/...` URLs. `NEXT_PUBLIC_VIDEO_POSTPRODUCTION_API_ORIGIN` is
  removed from the web app, `Dockerfile.web`, the base Compose file and the desktop smoke override; `lib/api.test.ts`,
  web/desktop `DOCPACK`, the runtime README and the desktop CSP (`studio-csp.js`, which no longer needs the
  `:8000` entries) are updated.
- **R-HEALTH** API `GET /readyz`: `SELECT 1` and Redis `PING`, 2 s timeout each → 200 `{"status":"ok"}` /
  503 `{"status":"degraded"}`. `/healthz` stays liveness. Web `GET /healthz` (not proxied, no Access or CSRF
  check) calls `/readyz` with a 3 s timeout and returns the same two bodies only.
- **R-INLINE** Stream routes send `Content-Disposition: inline`; `/download` stays `attachment`.

### F2 — Chunked, resumable upload

Migration 0004 adds `upload_sessions` (`id` UUID, `project_id`, `filename`, `media_type`, `size_bytes` BIGINT,
`chunk_bytes`, `status` `open|assembling|completed|failed`, `error_code`, `asset_id`, `job_id`, `budget_day`,
`budget_returned`, `assembly_token`, `assembly_heartbeat_at`, `created_at`, `expires_at`) and `upload_chunks`
(`upload_id`, `index`, `size_bytes`, `sha256`). Chunks live in `STORAGE_ROOT/uploads/{upload_id}/{index:05d}.part`;
every ingest temp file lives in `STORAGE_ROOT/tmp/` (same filesystem as the final file, never `/tmp`).

- **R-BUDGET** The daily transcription budget moves from process memory to the database: table
  `transcription_budget` (`day` UTC date PK, `used`), updated with a conditional `UPDATE … WHERE used < limit`.
  Every route that queues transcription (single upload, session create, retranscribe, retry) uses it, so API
  restarts no longer reset it and the retention sweep can return units. A unit is returned only to the day it was
  taken (`budget_day`); a return after that day has ended is a no-op.
- **R-UP-1 create** `POST /api/v1/projects/{project_id}/uploads` `{filename, media_type, size_bytes}`: validates
  name, MIME, `0 < size_bytes ≤ MAX_UPLOAD_BYTES`, and the project's quality (R-QUALITY). Deletes expired sessions
  (lazy). 429 `too_many_uploads` if the project has 3 or the server 6 live sessions (`open` and not expired, or
  `assembling` whatever its `expires_at`). Disk reservation: `free − Σ 2 × size_bytes(live sessions) − 2 × size_bytes ≥ 20 % of the volume`,
  else 507 `insufficient_storage`. Takes one budget unit. → 201 `{id, chunk_bytes: 8 MiB, chunk_count,
  expires_at}` (24 h).
- **R-UP-MODE** `UPLOAD_MODE` = `both` (default; local and desktop) or `chunked_only` (prod overlay). In
  `chunked_only` the single-request route answers 405 `use_chunked_upload`. In both modes the single-request route
  applies the same disk reservation and live-upload count as R-UP-1 before reading the body.
- **R-UP-2 chunk** `PUT /api/v1/uploads/{upload_id}/chunks/{index}`, raw octet stream, exactly `chunk_bytes`
  except the last; atomic write. Same bytes → 200; different bytes → 409 `chunk_conflict`; not `open` → 409
  `upload_not_open`; expired → 410 `upload_expired`. Chunk PUTs skip the 60/min write limiter and use
  `UPLOAD_CHUNK_RATE_LIMIT_PER_MINUTE` (default 600), answering 429 with `Retry-After` (passed through by
  R-PROXY-3). 8 MiB fits the 30 s write timeout at ≥ 2.5 Mbit/s upstream.
- **R-UP-3 status** `GET /api/v1/uploads/{upload_id}` → `{status, received: [{index, sha256}], chunk_count, size_bytes, chunk_bytes,
  expires_at, asset_id, job_id, error_code}`.
- **R-UP-4 complete** `POST /api/v1/uploads/{upload_id}/complete` `{sha256}`; takes no budget. A conditional update
  `open → assembling` with a new `assembly_token` lets exactly one caller win; every caller gets 202 with the
  R-UP-3 body; on `assembling` / `completed` it returns the current state and never creates a second asset.
  Missing chunks → 409 `upload_incomplete`.
- **R-UP-5 assemble** runs after the response in a FastAPI background task that opens **its own** DB session,
  storage and queue objects (request-scoped dependencies are closed by then). It refreshes
  `assembly_heartbeat_at` every 30 s. `ingest_asset(db, source, filename, media_type, expected_sha256,
  upload_session=…)` streams the ordered chunks into one temp file while hashing, checks size and sha256, then in
  **one transaction** — guarded by `WHERE assembly_token = <mine>` — inserts asset, job, stages and sets the
  session `completed` with `asset_id` / `job_id`. Both this success commit and every failure write are guarded by
  `WHERE status = 'assembling' AND assembly_token = <mine>`; if the guard matches no row (taken over, aborted) the
  attempt only deletes its own temp file. Mismatch → session `failed` / `upload_checksum_mismatch`; an FFprobe refusal → `failed` /
  `asset_not_readable`; in both cases the temp file is deleted, no `media_assets` / job row exists and the budget is
  returned. After commit: enqueue the `probe` stage, then set its `enqueued_at`; then delete the chunks. Chunks of
  `completed` sessions that are still on disk (crash, broker down) are deleted at API start and by the sweep,
  without touching the result or the budget. The
  single-request route uses the same `ingest_asset` (one copy instead of two).
- **R-QUEUE** Stage states become `blocked → queued → running → succeeded | failed`. Ingest creates `probe`
  `queued` and `transcribe` `blocked`; migration 0004 turns any `queued` stage whose previous stage has not
  `succeeded` into `blocked`. Every hand-off sets the next stage `blocked → queued` with `enqueued_at = NULL` **in
  the same commit** that finishes the previous stage; the last stage's `succeeded` and the job's `succeeded` (or
  `failed`) are one commit. After the commit the worker enqueues and sets `enqueued_at`. The worker claims with a
  conditional update `queued → running` that also requires the previous stage `succeeded`, so a duplicate or
  early message is a no-op.
- **R-LEASE** A running stage refreshes `processing_stages.heartbeat_at` every 60 s from a worker thread.
  `recover_jobs` runs at API start and every 5 min in the API process: (a) a `running` stage whose heartbeat is
  older than 3 min goes back to `queued` (attempt counted, `max_stage_attempts` respected) and its job to
  `retry_queued`; (b) every `queued` stage of a non-terminal job whose `enqueued_at` is null or older than
  `JOB_LEASE_SECONDS` is re-sent. A long stage with a live heartbeat is never taken over. The old
  `recover_stale_jobs` is replaced. Tests stop the worker after the claim, after the probe → transcribe hand-off and
  before the final commit, and run against rows created by the old schema.
- **R-UP-6 takeover** an `assembling` session whose heartbeat is older than 2 min returns to `open` and its
  `assembly_token` is cleared **in the same update** (the old attempt can no longer commit; the next `complete`
  issues a new token); temp files in `STORAGE_ROOT/tmp/` older than 1 h with no live session are deleted by the
  sweep.
- **R-UP-7 abort / expiry** `DELETE /api/v1/uploads/{upload_id}` is a conditional transition from `open` or
  `failed` to `aborted`, then deletes chunks; on `assembling` → 409 `upload_busy`; on `completed` → 409
  `upload_completed` (the result stays readable, no budget return). Expiry cleanup applies only to `open` / `failed`
  sessions; an `assembling` session keeps its reservation and slot until it ends or is taken over. Abort, expiry and failure return the budget unit once (`budget_returned`, R-BUDGET); a `completed`
  session never returns it.
- **R-UP-8 sizes** migration 0004 alters `media_assets.size_bytes` to BIGINT; a Postgres test stores 2,147,483,648.
- **R-UP-9 client** The web app always uses chunks: incremental SHA-256 in a Web Worker with a **pure-JS** library
  (`@noble/hashes`, pinned; no WebAssembly, so the desktop CSP needs no `wasm-unsafe-eval`), one chunk at a time,
  3 retries per chunk honouring `Retry-After`, progress bar, then `complete` and polling. If a poll shows `open`
  with every chunk received (assembly was interrupted, R-UP-6), the client sends `complete` again with the same
  hash. It stores
  `{upload_id, project_id, name, size, lastModified}` in `localStorage`; after a reload it offers "อัปโหลดต่อ —
  เลือกไฟล์เดิม"; a file that does not match name, size and `lastModified` is refused with a message. On a match the client
  hashes the file locally per chunk and compares the digests of the chunks the server already has (R-UP-3 returns
  them as `received: [{index, sha256}]`); any difference refuses the file as "ไม่ใช่ไฟล์เดิม" and the session
  stays `open`. Only then are the missing chunks sent.

### F3 — Production Compose overlay and images

- **R-COMPOSE-1** `services/video-postproduction/docker-compose.prod.yml`, used as
  `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` (Compose ≥ 2.24.4 for `!reset` /
  `!override`, checked by the deploy script). Every service gets `ports: !reset []`. New service `cloudflared`
  (pinned digest, `tunnel run`) reaches `web:3000`.
- **R-COMPOSE-2** Secrets reach only the service that needs them. `render-env.sh` writes one file per service under
  `/srv/video-studio/env/` (mode 600): `api.env` (`VIDEO_EDITOR_API_TOKEN`, `DATABASE_URL`, `SENTRY_DSN`), `worker.env` (`DATABASE_URL`,
  `SENTRY_DSN` — no API token: the worker never serves HTTP), `web.env` (token, Access variables, `SENTRY_DSN`), `postgres.env` (`POSTGRES_PASSWORD`),
  `cloudflared.env` (`TUNNEL_TOKEN`); backup keys go to `backup.env`, read only by the host backup script. The overlay
  uses `environment: !override` with the full non-secret mapping for each service (so base defaults such as
  `VIDEO_EDITOR_API_TOKEN: ${…:-}` cannot shadow the env file) and `env_file` per service. Non-secret prod values:
  `VIDEO_EDITOR_ALLOW_UNAUTHENTICATED=false`, `CORS_ALLOWED_ORIGINS=""`, `TRANSCRIPTION_PROVIDER=faster_whisper`,
  `TRANSCRIPTION_QUALITIES=fast`, `STUDIO_ACCESS_MODE=required`. `scripts/studio/check-config.sh` renders
  `docker compose config` with dummy values and fails if any secret name appears in a service outside its list
  (runs in CI).
- **R-QUALITY** API setting `TRANSCRIPTION_QUALITIES` (default `fast,accurate`). `GET /transcription/capabilities`
  adds `qualities`. A quality not listed → 422 `quality_unavailable` on create, retranscribe, upload (single or
  session) into a project whose stored quality is not listed, and retry. The worker checks again before loading a
  model and fails the stage with `quality_unavailable` (no silent downgrade). `PATCH /api/v1/projects/{project_id}`
  `{transcription_quality}` (write gate) sets any listed quality while no job of the project is `queued` /
  `running` / `retry_queued` (else 409 `project_busy`). The UI shows only listed qualities and offers
  "เปลี่ยนเป็น fast" on a project whose stored quality is not listed.
- **R-COMPOSE-3** `restart: unless-stopped`; Celery `--concurrency=1`. Limits:

  | Service | mem_limit | cpus |
  |---|---|---|
  | api | 768 MB | 1.0 |
  | worker | 2.25 GB | 4.0 |
  | postgres | 512 MB | 1.0 |
  | redis | 256 MB (`--maxmemory 128mb`) | 0.5 |
  | web | 512 MB | 1.0 |
  | cloudflared | 128 MB | 0.5 |

  ≈ 4.4 GB; with D3 the runner caps are 10 GB, worst case ≈ 14.4 GB of 15 GB, relying on zram (checked in AC-12).
  Fallback if AC-12 fails: stop `ci-runner-local-2` while rendering (owner decision).
- **R-SCHEMA-WAIT** The worker `depends_on` the API with `condition: service_healthy` (the API is healthy only after
  its migrate step), and at start the worker checks that the database revision is at least its own head; if not it
  waits (retry every 5 s, up to 5 min) and only then consumes the queue. Test: start a new worker on an old schema
  with a message waiting in Redis.
- **R-NONROOT** Both images run their process as uid/gid 10001. API image: `HF_HOME=/app/cache/huggingface`. Its
  entrypoint starts as root only to fix ownership: if the top-level owner of `/app/storage` or `/app/cache` is not
  10001 it runs `chown -R 10001:10001` on it once, then `exec setpriv --reuid 10001 --regid 10001 --init-groups`
  the real command. No extra Compose service is added (the desktop `compose ps` parser stays valid). The base
  Compose file mounts `whisper_cache` at `/app/cache/huggingface` (the model is re-downloaded once). Web image runs
  `node server.js` from Next standalone output as 10001.
- **R-IMAGE** Images are tagged `video-studio-api:<tag>` / `video-studio-web:<tag>` via `STUDIO_TAG`; the
  previous tag's images stay on the host.

### F4 — Host, tunnel and Access

- **R-HOST-1** Checkout at `/srv/video-studio/src` (git tag); per-service env files from Infisical `/video-studio`
  (R-COMPOSE-2), never printed. Keys: `VIDEO_EDITOR_API_TOKEN`, `POSTGRES_PASSWORD`, `TUNNEL_TOKEN`,
  `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, `STUDIO_OWNER_EMAIL`, `STUDIO_PUBLIC_ORIGIN`, `SENTRY_DSN_API`,
  `SENTRY_DSN_WEB`, `R2_BACKUP_ACCESS_KEY_ID`, `R2_BACKUP_SECRET_ACCESS_KEY`, `RESTIC_PASSWORD`, `HEARTBEAT_TOKEN`.
- **R-HOST-2** Remotely managed tunnel `video-studio`: `studio.bookchaowalit.com` → `http://web:3000`, proxied
  CNAME. Access application on the hostname (Allow: owner email, session 24 h) and on `/healthz` (Bypass). Cache
  Rule bypass for the hostname. The WAF scanner rule applies.
- **R-HOST-3** systemd `video-studio.service` (`compose up -d` / `down`), enabled.
- D3: `ci-runner-local-2` recreated with a 4 GB limit (recorded in the homelab runbook).

### F5 — Retention sweep (D6)

- **R-RET-1** `python -m video_postproduction.retention sweep [--dry-run]`; migration 0004 adds nullable
  `purged_at` to `media_assets` and `render_outputs`.
- **R-RET-2** Deleted: the source file of a `media_assets` row older than 30 days; the `render.mp4` file
  (`render_outputs.storage_key`) of a row older than 90 days; expired upload
  sessions with their chunks; stale temp files (R-UP-6). Kept: all rows, `trace.json`, `MANUAL-STEPS.md`,
  `edl.json`, `timeline.edl` / `.fcpxml` / `.otio`, `captions*.srt|ass`, `history.jsonl`.
- **R-RET-3** In-flight protection: the sweep locks each candidate asset row (`SELECT … FOR UPDATE`) and skips it
  while any job, stage or render that uses it is `queued`, `running` or `retry_queued`. Every path that queues work
  on a source (retranscribe, retry, silence cut, render) locks the same row and checks `purged_at` first, so the two
  are serialised. If the worker still opens a purged source it fails the stage with `source_purged`.
- **R-RET-4** Crash-safe order: set `purged_at` and commit, then delete the files; each run also deletes files of
  rows already marked (finishes an interrupted run). A marked row is purged even if a file still exists.
- **R-RET-5** Purged media: asset stream, render stream, render download and `job_file_route` for `render.mp4` →
  410 `media_purged`; queuing work on a purged source → 409 `source_purged`. The editor shows
  "ไฟล์ถูกลบตามระยะเก็บแล้ว". `production_trace verify` on a purged render reports `render.mp4` missing (expected).
- **R-RET-6** Daily 03:30 BKK systemd timer (`docker compose run --rm api python -m …`) under the host maintenance
  lock (R-BAK-1, waits up to 2 h, then exits `ok:false`), one JSON summary line, heartbeat
  `video-studio-retention`, `ok:false` when free disk on the storage volume is under 20 %.

### F6 — Observability and backups (PRJ-DSO-02)

- **R-OBS-1** `platform-uptime-worker` `src/targets.ts`: target `video-studio` →
  `https://studio.bookchaowalit.com/healthz`, plus `HEARTBEATS` `video-studio-worker` (3 h), `video-studio-backup`
  (36 h), `video-studio-retention` (36 h); the monitor is deployed before AC-19 is checked.
- **R-OBS-2** Hourly systemd timer: `celery inspect ping` → heartbeat `video-studio-worker`.
- **R-OBS-3** Worker calls `init_error_reporting()` in Celery `worker_process_init`. Web: errors-only Sentry from the
  web-starter `lib/sentry.ts` pattern (no traces, no replay, no bodies, URLs without query strings).
- **R-BAK-1** Nightly 02:45 BKK `scripts/studio/backup.sh` holds `flock /srv/video-studio/run/maintenance.lock`
  (shared with retention): `pg_dump -Fc`, then `restic backup` of the dump plus the storage volume **excluding
  `uploads/` and `tmp/`** to R2 `video-studio-backup` (bucket-scoped token), then `restic forget --keep-daily 14
  --keep-weekly 8 --keep-monthly 6 --prune`; logs repository size; heartbeat `video-studio-backup`. In-flight
  uploads are not backed up (accepted). The dump is taken **before** the file snapshot; because every writer writes
  files before committing their row (fact 13) and only retention deletes media (under the same lock), every row in
  the dump has its file in the snapshot. The reverse — a file or `history.jsonl` line from a job that committed after
  the dump — is possible and is cleaned by the restore reconcile (R-BAK-3). A test asserts that no code path other
  than retention deletes media files.
- **R-BAK-2** Size is bounded by D6; R2 is free to 10 GB, then about $0.015 / GB-month; the logged size shows growth.
- **R-BAK-3** Restore (`docs/runbooks/video-studio.md`, `scripts/studio/restore.sh`) into a throwaway Compose
  project (other project name, no ports, no tunnel), then a fix-up step: delete `upload_sessions` that are `open` or
  `assembling`; set `running` jobs to `retry_queued` and `running` stages to `queued` (as `jobs/recovery.py` does);
  clear `enqueued_at` on every `queued` stage. R-QUEUE then re-sends them when the API starts (Redis is not
  restored). Reconcile, by kind: **exports** have no rows by design, so `export-*` directories and their history lines are
  always kept; **renders** follow the dumped `render_outputs.status` — a row that is not `succeeded` has its
  job-directory files and history lines removed and is set `failed` / `interrupted_by_restore` (the owner re-renders);
  a render directory with no row at all is removed; **source media** files with no `media_assets` row are removed.
  The counts are printed. Then check one project's asset,
  transcript, EDL and render hash against `trace.json`. Once before release, then monthly.

### F7 — Release, update and rollback

- **R-REL-1** Release = annotated tag `studio-vX.Y.Z` on `main`; the note records the Alembic head. Update:
  `git fetch --tags`, checkout the tag, `compose build`, `up -d`.
- **R-REL-2** Migrations from 0004 on are **additive only** (new tables, nullable columns, widening types); code of
  release N must work on the schema of release N+1. The API start command becomes `python -m
  video_postproduction.migrate`: if the database revision is known to this image it runs `upgrade head`; if it is
  newer than this image's head it logs `schema_newer_than_code` and starts. Production rollback **never
  downgrades** (so the BIGINT and `purged_at` data stay). `downgrade()` functions exist for development and CI.
- **R-REL-3** Rollback: checkout the previous tag in `/srv/video-studio/src` (its Compose files too), set
  `STUDIO_TAG` to it, `up -d`, check web `/healthz` 200 and open one project through the hostname. The earliest
  valid rollback target is `studio-v0.1.0` (the first release with the migrate wrapper and the overlay).
- **R-REL-4** Runbook: start/stop, update, rollback, disk full, host reboot, token rotation (API, tunnel), restore.

### F8 — CI, lockfile, desktop bundle and gates

- **R-CI-1** Image job for both Dockerfiles with `platform-ci-workflows` image-scan v1.6.3 (blocking high/critical),
  SHA-pinned.
- **R-CI-2** `services/video-postproduction-api/uv.lock` with `production-trace` declared as a path source
  (`[tool.uv.sources]`); the Dockerfile runs `uv sync --frozen --no-dev --extra transcription --extra sentry`. The
  lockfile and `apps/video-postproduction-web/instrumentation.ts` are added to `REQUIRED_FILES`; a CI step stages the desktop runtime bundle, builds both images from it,
  starts the base stack, and checks `/readyz` 200 and a worker ping (not only the build).
- **R-CI-3** Migration tests on a Postgres service container: `upgrade head`, BIGINT (R-UP-8), and the
  newer-schema start (R-REL-2).
- **R-CI-4** ADR-0004 gates, using the inputs each reusable workflow really has: the API job (`ci-python.yml`)
  sets `coverage-threshold` (pytest exits non-zero when no test is collected); the web job runs
  `npx vitest run --coverage --coverage.reporter=json-summary` so `coverage/coverage-summary.json` exists, and sets
  `coverage-threshold` (plus `require-tests` where that workflow offers it). Thresholds = measured baseline on
  `main` rounded down; new modules (proxy, upload sessions, retention, migrate, recovery) reach ≥ 90 % lines.

## 3. Acceptance criteria

Tests: `services/video-postproduction-api/tests/` (`test_upload_sessions.py`, `test_budget.py`, `test_queue_recovery.py`,
`test_retention.py`, `test_readyz.py`, `test_quality.py`, `test_migrations_pg.py`, `test_migrate_wrapper.py`),
`apps/video-postproduction-web/tests/` (`proxy.test.ts`, `access.test.ts`, `upload-client.test.ts`),
`apps/video-postproduction-desktop/tests/` (existing compose/CSP tests extended). Manual checks go to `evidence.md`.

| ID | Req | Given / When / Then | Proof |
|---|---|---|---|
| AC-1 | R-HOST-2 | Given no Access session, when the hostname is opened, then Access login shows; owner login loads the studio; another email is refused. | manual, screenshots |
| AC-2 | R-PROXY-1/3 | Given a sentinel token, when pages and proxied API/media responses are fetched, then the sentinel is in no body or header; with no token, no upstream request has `Authorization`. | `proxy.test.ts::token_never_leaves_server`, `::no_header_without_token` |
| AC-3 | R-COMPOSE-1 | Given the prod stack on Fedora, when `ss -ltnp` and `docker compose ps` run, then no studio port listens. | evidence |
| AC-4 | R-PROXY-7 | `required`: no JWT / wrong alg / iss / aud / expired / other email → 401; valid owner JWT passes; a missing variable stops start-up. Unset mode stops start-up. | `access.test.ts` |
| AC-5 | R-PROXY-6 | In `required` and in `off`: a write with `Sec-Fetch-Site` same-site/cross-site, a foreign `Origin`, or neither header → 403; a same-origin write passes. | `proxy.test.ts::csrf_required_*`, `::csrf_off_*` |
| AC-6 | R-PROXY-5 | Paths with `..`, `%2e%2e`, `%2f`, `%5c`, NUL → 400 and nothing upstream. | `proxy.test.ts::invalid_path_*` |
| AC-7 | R-PROXY-1/2, R-INLINE | On the built standalone image: `Range: bytes=0-1023` → 206 + `Content-Range`; HEAD → headers only and the upstream read is cancelled; stream is `inline`; `<video>` seeks. | spike + `proxy.test.ts::range`, `::head_cancels`, E2E |
| AC-8 | R-PROXY-4 | Every proxied response has `private, no-store`; on prod a render stream shows `cf-cache-status` `BYPASS`/`DYNAMIC`. | `proxy.test.ts::no_store`, evidence |
| AC-9 | R-UP-1/2/4 | Same chunk bytes → 200, different → 409; 4th live session → 429; two concurrent sessions that together exceed the disk rule → second 507; two concurrent `complete` → one asset, one job. | `test_upload_sessions.py` |
| AC-10 | R-UP-5 | Wrong sha256 → session `failed`, no `media_assets` row, budget back. An unreadable video with a matching hash → `failed`/`asset_not_readable`, no rows, budget back. Kill after the ingest commit (chunks of the `completed` session are gone after restart), after the probe→transcribe hand-off, after a claim, and before the final commit → after restart each stage runs once and the job ends `succeeded`; a duplicate or early `transcribe` message is a no-op; a long stage with a live heartbeat is not taken over; rows from the old schema migrate to `blocked` correctly. | `test_upload_sessions.py::checksum_mismatch_*`, `::ffprobe_reject`, `test_queue_recovery.py` |
| AC-11 | R-UP-6/7, R-BUDGET | Stale heartbeat → takeover clears the token; the slow original's success and failure writes are refused, also after a DELETE; an `assembling` session past `expires_at` still counts in the reservation; an API restart mid-assembly ends `completed` after the client re-sends `complete`; DELETE on `assembling` → 409 and on `completed` → 409 with the result kept; abort/expiry/failure return budget once and only to the same UTC day; budget survives API restart; the sweep can return a unit. | `test_upload_sessions.py::takeover_*`, `test_budget.py` |
| AC-12 | R-COMPOSE-3 | Given both runners busy and zram active (`zramctl` output recorded), a 10-min 1080p render finishes, no `OOMKilled=true`, no OOM in `dmesg`. | evidence |
| AC-13 | R-UP-2/9 | Through the hostname with client upstream throttled to 3 Mbit/s, a 1.5 GB upload has every chunk < 30 s; a 429 through the proxy carries `Retry-After` and the client waits; reload → resume prompt; wrong file refused, including one with the same name, size and `lastModified` but different bytes (session stays `open`); right file sends only missing chunks. | E2E evidence + `upload-client.test.ts::resume_*`, `::retry_after` |
| AC-14 | R-UP-8 | 2,147,483,648 bytes round-trips on Postgres. | `test_migrations_pg.py::bigint_size` |
| AC-15 | F1–F3 | On prod: upload → transcribe (Thai, fast) → edit EDL → render → download; `production_trace verify` passes. | E2E evidence |
| AC-16 | R-QUALITY | With `fast` only: create, retranscribe, upload into an `accurate` project, and retry → 422; a queued `accurate` job fails with `quality_unavailable` at the worker; `PATCH` to `fast` makes the project upload again (409 while busy); UI shows no Accurate option and offers the switch. | `test_quality.py`, UI test |
| AC-17 | R-HEALTH | Postgres or Redis stopped → web `/healthz` 503 degraded; both up → 200. | `test_readyz.py`, evidence |
| AC-18 | R-RET-1…5 | Dry-run lists exactly the expired files; a real run deletes the render file and then render stream, download and `job_file_route` all answer 410 while `trace.json` stays, keeps the kept files, sets `purged_at`; purged routes → 410/409; an asset with a queued job is skipped; a run killed after commit is finished next run. | `test_retention.py` |
| AC-19 | R-RET-6, R-OBS-1/2, R-BAK-1 | Within 24 h of release the target and three heartbeats are green; retention with < 20 % free → `ok:false`; retention started during a backup waits for the lock. | monitor `/status`, `test_retention.py::low_disk`, evidence |
| AC-20 | R-OBS-3 | A worker task error and a web server error each produce one scrubbed Sentry event. | fake-transport tests + one live check each |
| AC-21 | R-BAK-1/3 | A drill whose backup ran while a stage was `running`, with one open upload and a render that finished between dump and file snapshot, plus an export made before the backup, restores DB + media from R2; fix-up and reconcile run (the in-progress render is `failed`/`interrupted_by_restore` with its files and history lines removed; the export and its history line stay); the interrupted job completes; hashes match `trace.json`; backup size is logged. | drill log |
| AC-22 | R-REL-2/3 | CI: an image of head N starts on a schema at N+1 and logs `schema_newer_than_code`. Host: release `studio-v0.1.1` (with an additive rehearsal migration) is rolled back to `studio-v0.1.0` in ≤ 10 min; `/healthz` 200; a project opens through the hostname; no data lost. | `test_migrate_wrapper.py`, evidence |
| AC-23 | R-NONROOT, R-CI-1 | Both processes run as 10001; image-scan clean of high/critical; a local stack with root-owned volumes from b2f126a upgrades and plays old media. | CI, evidence |
| AC-24 | R-DESK, R-PROXY-8, R-CI-2 | CI builds and starts the stack from the staged desktop bundle (`/readyz` 200, worker ping); desktop `compose ps` parsing passes on it; the web image built from the bundle refuses to start with `STUDIO_ACCESS_MODE` unset; on Windows, the desktop window uploads (chunked, hash via pure JS under its CSP) and renders. | CI, desktop tests, evidence |
| AC-25 | R-HOST-3 | After a Fedora reboot the stack is back and `/healthz` 200 with no manual step. | evidence |
| AC-26 | R-COMPOSE-2 | `check-config.sh` passes in CI: each secret appears only in its services (the API token only in `api` and `web`, never `worker`); an empty base default cannot override an env file value. | CI |
| AC-27 | R-CI-4 | API `coverage-threshold` and web coverage summary + threshold are enforced in CI; new modules ≥ 90 %. | CI |
| AC-29 | R-UP-MODE | In `chunked_only` the multipart route answers 405; in `both` it refuses with 507 when the reservation fails. | `test_upload_sessions.py::upload_mode_*` |
| AC-30 | R-SCHEMA-WAIT | A new worker started on an old schema with a waiting message does not consume it until the migration is done, then runs it. | `test_migrate_wrapper.py::worker_waits` |
| AC-31 | R-PROXY-7 (dev) | `npm run dev` with the committed `.env.development` serves the studio against a local API on `127.0.0.1:8000`; a production build ignores that file. | web test + evidence |
| AC-28 | ADR-0007 floor | Project list, new project (progress, resume prompt, wrong-file message) and editor: reflow without horizontal scroll at 320 / 375 / 390 / 768 / 1440 px; upload, resume and cancel work by keyboard alone with visible focus; design-floor checker passes. Before and after deploy. | screenshots, keyboard walk-through log, checker output |

## 4. Threats and how this spec handles them

| Threat | Handling |
|---|---|
| Someone finds the hostname | Access in front (AC-1); proxy re-verifies JWT and owner email, fail-closed (AC-4) |
| Cross-site write while signed in | Same-origin check in both modes (AC-5) |
| Token theft from the browser | Token server-side only (AC-2) |
| LAN device reaches API / DB / secrets | No published ports in prod (AC-3); secrets only in their service (AC-26) |
| Open API reads | API reachable only through the proxy behind Access |
| Path traversal via the proxy | Segment allow-list, fixed base (AC-6) |
| CDN keeps private media | `no-store` + Cache Rule (AC-8) |
| Huge / malicious upload | Size, MIME, quality and disk checks at create; checksum before any row; FFprobe; caps; budget |
| Disk fills | Reservation (AC-9), retention, `ok:false` < 20 % (AC-19) |
| Data loss / inconsistent restore | Locked backups, excluded transient dirs, restore fix-up, drill (AC-21) |
| Lost queue messages | `enqueued_at` + recovery (AC-10) |
| CI and studio fight for RAM | Limits, D3, zram (AC-12) |

## 5. Risks and open points

1. Home power / internet outage takes the studio down; accepted, the monitor alerts.
2. Whisper `small` Thai quality is today's local quality; better STT is a later spec.
3. Client upstream below ~2.5 Mbit/s can still time out an 8 MiB chunk; adaptive chunk size is a follow-up if
   AC-13 shows the need.
4. The in-process write limiter sees one client (the web container); acceptable for one user.
5. In-flight uploads are not in backups (R-BAK-1).
6. Owner actions in P2 (each with its dashboard URL when asked): tunnel + Access applications + Cache Rule, R2
   bucket token, Sentry web project, D3 runner change.

## 6. Delivery order (TDD; red/green log in `red-green.md`)

1. Spike on the built standalone image (R-PROXY-2). If it fails, stop and revise this spec.
2. Migration 0004 + migrate wrapper + Postgres tests (R-UP-8, R-REL-2, R-CI-3).
3. R-BUDGET, `ingest_asset` refactor, R-QUEUE, then F2 sessions — tests first.
4. R-QUALITY, R-HEALTH, R-INLINE, R-OBS-3.
5. F1 proxy, Access, CSRF, relative URLs, chunked client, desktop CSP — tests first.
6. F5 retention — tests first.
7. F3 overlay, per-service env, non-root entrypoint, F8 CI, `uv.lock`, desktop bundle, gates.
8. Codex review, merge to `main`, tag `studio-v0.1.0`.
9. F4 host / tunnel / Access, F6 monitor entries and backups, D3.
10. Rehearsal release `studio-v0.1.1` and rollback (AC-22); evidence for all ACs in `evidence.md`.
