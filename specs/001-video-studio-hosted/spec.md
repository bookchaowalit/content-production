# SPEC-001 — Hosted Video Studio on the homelab (Tailscale only)

Standard: ADR-0004 — TDD + SDD.

## Document control

| Field | Value |
|---|---|
| Spec ID / version | SPEC-001 / 1.0 |
| Status | **Approved** by the owner 2026-11-19 ("อนุมัติ เริ่ม P1") after nine review rounds (v0.1–v0.10, logs below); D7 decided as recommended |
| Repository / path | bookchaowalit/content-production / `specs/001-video-studio-hosted/spec.md` |
| Owner (maker) | Claude Code |
| Reviewer (checker) | Fresh Claude reviewer subagent (no maker context) + Codex at Stop |
| Approver | Owner (bookchaowalit) |
| Requirement links | Knowledge store `plans/content-production-deploy/PLAN.md` (D1–D6 approved 2026-11-18; D5 replaced 2026-11-19) |
| Related docs | `PRODUCT.md`; `services/video-postproduction/README.md`; `services/video-postproduction-api/README.md`; `apps/video-postproduction-desktop/` (`scripts/runtime-bundle-manifest.js`, `src/runtime/compose.js`, `src/security/studio-csp.js`); ADR-0004, ADR-0007, ADR-0014; PRJ-DSO-02; `platform-uptime-worker` `src/targets.ts` |
| Main-only | no — branch `spec/video-studio-hosted`, then `feat/video-studio-hosted` |
| Created / approved | 2026-11-18 / 2026-11-19 |
| Review log | `review-R1.md`, `review-R2.md`, `review-R3a.md`, `review-R3b.md`, `review-R3c.md`, `review-R4.md`, `review-R5.md`, `review-R6.md` |

## 1. Problem and intent

- **Problem:** the Video Studio (upload → transcribe → EDL → 9:16 render → export) only runs on a workstation
  through Docker Compose bound to `127.0.0.1`. The owner cannot use it away from that machine, nothing watches,
  backs up or restores it, and its job pipeline can lose work on a crash (facts 12, 17, 19).
- **Outcome:** from any of the owner's devices on the personal tailnet, `https://video-studio.<tailnet>.ts.net`
  opens the same studio. It runs on the homelab Fedora host, is reachable **only** over Tailscale (no public
  hostname, no LAN port), keeps media at home, recovers its jobs after a crash, is watched by heartbeats, and is
  backed up to R2 with a tested restore.
- **Out of scope:** public internet access (Cloudflare, Funnel), chunked or resumable upload, other production
  APIs, `production-console-web`, the `book-production` CLI engines, multi-user accounts, any cloud host.
- **R-DESK** The Electron desktop app is not deployed, but it builds the same Dockerfiles and base Compose file from
  its runtime bundle and shows the web app in a window with its own CSP. Every change keeps it working: bundle
  builds, `compose ps` parsing, the renderer CSP, upload and render from the desktop window.

### Owner decisions

| ID | Decision |
|---|---|
| D1 | One user: the owner. |
| D2 | Host = homelab Fedora (`192.168.152.12`, Ryzen 7 4700U 8C, 15 GB RAM, 151 GB free disk). |
| D3 | Lower `ci-runner-local-2` memory limit 6 → 4 GB to make room for the studio. |
| D4 | Transcription stays local (`faster_whisper`, CPU). No media leaves the house. Hosted runs offer `fast` (`small` model) only (R-QUALITY). |
| D5 | **(2026-11-19, replaces the Cloudflare hostname)** Access only through Tailscale: a tagged node `video-studio` on the personal tailnet, HTTPS by Tailscale Serve, owner devices only by tailnet policy. |
| D6 | Retention: source media 30 days, rendered MP4 90 days after creation. Metadata, traces and interchange files are kept. |
| D7 | **(2026-11-19, approved with the spec)** Off-site backup of media is an explicit exception to "media stays at home" (D4): source media and renders go to R2 **only as restic ciphertext** (key `RESTIC_PASSWORD` in Infisical, never on R2). Alternative if the owner refuses: R2 gets only the DB dump, traces and interchange files, and media is backed up on the LAN to the beelink Samba share (`/mnt/backup-vzdump` disk), losing off-site copies of media. |

### Facts found while writing this spec (2026-11-18, main b2f126a)

1. The web app (`apps/video-postproduction-web/lib/api.ts:152-157`) calls the API **from the browser** at
   `NEXT_PUBLIC_VIDEO_POSTPRODUCTION_API_ORIGIN` and sends **no `Authorization` header**; media URLs
   (`lib/api.ts:333-342`) point at the same origin.
2. The API write gate (`api/dependencies.py:87-103`) fails closed; the Compose stack sets
   `VIDEO_EDITOR_ALLOW_UNAUTHENTICATED=true`. **Reads are open.** Write limit 60/min per `request.client.host`;
   the limiter and the daily transcription budget (`api/limits.py`) are in-process and reset on restart.
3. Upload is one multipart POST (`api/routes.py:221-252`), cap `MAX_UPLOAD_BYTES` = 2 GiB. `upload_asset`
   (`api/services.py`) copies to a temp file in `/tmp`, `put_atomic` copies again, commits asset + job + stages
   (`services.py:183-188`) **before** `queue.enqueue` (`:189-196`), and marks the job `failed` / `queue_error` when
   the enqueue fails.
4. `media_assets.size_bytes` is `INTEGER` (`migrations/versions/0001_v1_core.py:36`), one byte short of the cap.
5. Fedora is not on the tailnet; it is reached through the subnet router (beelink / asus-pve). `sudo` on Fedora
   needs a password, so host packages are an owner action; containers are not.
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
    (postgres, redis, api, worker, web). The desktop renderer CSP (`studio-csp.js:3-16`) allows `connect-src` /
    `media-src` to `127.0.0.1:8000`.
12. `StageRunner.retry_stage` accepts only `failed` stages (`jobs/stages.py:334-338`). `recover_stale_jobs`
    (`jobs/recovery.py`) has no caller at runtime and judges a lease from `job.started_at`, which would also take
    over a long transcription that is still running.
13. A render is stored once: `render_outputs.storage_key` is `location.key("render.mp4")` inside the job directory
    (`render/service.py:171-172,240-241,262`), the file `job_file_route` also serves. Ingest and render write files
    **before** committing the row that points at them; the render trace and history line are written before
    `storage_key` / `succeeded` are committed (`render/service.py:261-268`); a `render_outputs` row exists from
    enqueue (`:353-361`). Failed jobs also write a job directory.
14. Exports create no database row: `ExportService.export()` writes `export-<id>/` files and a `history.jsonl` line
    (`exports.py:81-83,120-144`).
15. `platform-uptime-worker` answers 404 for a heartbeat name not in `HEARTBEATS`; it runs on Cloudflare and cannot
    reach the tailnet, so it can only receive heartbeats from the host.
16. Fedora runs `ci-runner-local` and `ci-runner-local-2` (6 GB / 7 CPU each). Fedora ships zram swap by default
    (to be confirmed on the host).
17. `run_probe` commits, then enqueues `transcribe` (`jobs/stages.py`); a crash between the two leaves the job
    `running` with no stage running. Ingest creates `probe` and `transcribe` both `queued`.
18. A project's quality can only go `fast → accurate` (`api/services.py` `enqueue_accurate_retranscription`).
19. `run_transcribe` commits the stage `succeeded`, then the job `succeeded` in a second commit
    (`jobs/stages.py:308-312`).
20. CI (`.github/workflows/ci.yml`) sets no `coverage-threshold`; the web job runs `npx vitest run` (no coverage
    report); `ci-python.yml` has no `require-tests` input.
21. `RenderService.enqueue` returns the existing `render_outputs` row for the same `render:<asset>:<edl>:<version>`
    idempotency key whatever its status (`render/service.py:329-342`), so a failed render of an EDL version can
    never be rendered again.

## 2. Requirements and design

### F1 — Tailscale ingress

- **R-TS-1** A new Compose service `tailscale` (image `tailscale/tailscale`, pinned digest) in the **prod overlay
  only**: `TS_HOSTNAME=video-studio`, `TS_USERSPACE=true`, `TS_STATE_DIR=/var/lib/tailscale` (named volume
  `tailscale_state`), `TS_AUTHKEY` from its env file (one-use, pre-authorised, tagged `tag:video-studio`, not
  ephemeral), `TS_AUTH_ONCE=true` (the one-use key is used only for the first login; later starts reuse the state volume),
  `TS_ENABLE_HEALTH_CHECK=true` with `TS_LOCAL_ADDR_PORT=127.0.0.1:9002` (container healthcheck on `/healthz`),
  `TS_SERVE_CONFIG=/config/serve.json` (read-only mount) serving `https://${TS_CERT_DOMAIN}:443` →
  `http://127.0.0.1:3000`. Funnel is not enabled. No host package, no `sudo`, no published port.
- **R-TS-1b** Only Serve can reach the web server: in the prod overlay `web` uses `network_mode: service:tailscale`
  and listens on `HOSTNAME=127.0.0.1` port 3000, so it shares the tailscale container's network namespace and is
  unreachable from the host, the LAN and the other containers. It still reaches `api:8000` through that namespace's
  Compose network. The base file (local, desktop) is unchanged.
- **R-TS-2** Tailnet policy (owner action, recorded in the runbook): `tagOwners` for `tag:video-studio`; tagged
  nodes have no key expiry; a grant lets only the owner's user reach `tag:video-studio` on TCP 443;
  `tag:video-studio` may reach nothing. **Before the node joins**, the whole policy is reviewed: the default
  allow-all rule and any ACL/grant whose `dst` covers `*`, `tag:video-studio` or the node are removed or narrowed,
  and the policy file is saved to the runbook evidence. MagicDNS and HTTPS certificates must be on. The `.ts.net`
  name will appear in public certificate-transparency logs (accepted; it is unreachable off the tailnet).
- **R-TS-3** Owner identity check (defence in depth): with `STUDIO_ACCESS_MODE=tailscale` the web app requires the
  `Tailscale-User-Login` header that Tailscale Serve adds for requests from tailnet users, and it must equal
  `STUDIO_OWNER_LOGIN` (case-insensitive); otherwise 401 `access_required` on pages and proxied calls. Requests from
  tagged devices carry no user header and are refused. The header is trustworthy only because `web` has no other
  ingress (R-COMPOSE-1); the P2 host check also confirms that a client-sent `Tailscale-User-Login` is replaced, not
  passed through, by Serve — if it is passed through, go-live **stops** and the spec is revised with the owner
  (there is no silent fallback) — which `Host` value Serve forwards (R-PROXY-6a is set to match it), and that
  `tailscale status --json` finds the socket inside the container.
- **R-TS-5** All checks (identity, `Host`, same-origin) run inside the route handler and the root layout /
  `instrumentation.ts`, **not** in Next middleware or `proxy.ts`, so no request body passes a middleware body
  limit. The spike runs with every check enabled.
- **R-TS-4** `STUDIO_ACCESS_MODE` is `tailscale` (prod overlay) or `off` (base Compose file and a committed
  `.env.development` with `VIDEO_POSTPRODUCTION_API_INTERNAL_URL=http://127.0.0.1:8000` for `npm run dev`; Next
  loads that file only in development). With `tailscale`, `STUDIO_OWNER_LOGIN` and `STUDIO_PUBLIC_HOST` must be set
  or the server refuses to start (`instrumentation.ts` `register()` throws). Unset or any other value: refuse to
  start. The web `DOCPACK` runbook and browser smoke are updated.

### F2 — Same-origin proxy in the web app

- **R-PROXY-1** Route handler `apps/video-postproduction-web/app/api/v1/[...path]/route.ts` (GET, HEAD, POST,
  PATCH, PUT, DELETE) forwards to `VIDEO_POSTPRODUCTION_API_INTERNAL_URL` (default `http://api:8000`), adding
  `Authorization: Bearer <VIDEO_EDITOR_API_TOKEN>` from server-side env only, and no `Authorization` header when the
  token is unset (local stack). HEAD is sent upstream as GET; the upstream body is cancelled (`AbortController`) as
  soon as headers arrive.
- **R-PROXY-2** Streaming both ways (`duplex: "half"`, unbuffered). The first delivery step is a spike that runs the
  **built web image with its production start command** (`node server-launch.mjs`, R-NONROOT) and proves a 2 GiB POST streams through
  with flat memory, a `Range` 206 and a HEAD. The Next docs in `node_modules/next/dist/docs` are read first (web
  `AGENTS.md`). Timeouts on the whole path are set explicitly: the web image's only start command is a small launcher (`server-launch.mjs`, R-NONROOT) that
  wraps Next's `http.createServer` so `requestTimeout` is 2 h (Node's default 300 s would cut a slow upload), then
  loads `server.js`; the proxy's `fetch` uses an undici `Agent` with `headersTimeout` and `bodyTimeout` of 2 h;
  uvicorn keeps its defaults (no request-body timeout). The spike uploads with the client throttled to 20 Mbit/s so
  the request lasts longer than 5 min, and checks that the API receives a correct `Content-Length` from undici. If
  any of this cannot be made to work, the cap is lowered and AC-13 adjusted before P1 goes on.
- **R-PROXY-3** Forwarded request headers: `content-type`, `content-length`, `range`, `if-range`, `if-none-match`,
  `accept`. Returned headers: `content-type`, `content-length`, `content-range`, `accept-ranges`,
  `content-disposition`, `etag`, `retry-after`, `x-request-id`. Everything else is dropped in both directions.
- **R-PROXY-4** Every proxied response sets `Cache-Control: private, no-store` and `X-Content-Type-Options: nosniff`.
- **R-PROXY-5** `server-launch.mjs` checks the **raw `req.url`** of every request under `/api/` at the HTTP entry,
  before Next sees it: `..`, `%2e`, `%2f`, `%5c` or NUL → 400 `invalid_path` and the request never reaches Next or
  the API. The route handler then builds the upstream URL on a fixed base from decoded segments; each must match `^[A-Za-z0-9_.-]+$`
  and not be `.` / `..` (refuses `%2e%2e`, encoded `/` `\`, NUL); else 400 `invalid_path`. The query string is
  parsed and re-serialised.
- **R-PROXY-6** Cross-site and rebinding protection, in every mode. Tailnet access is network-level, so any page
  open in the owner's browser could otherwise send writes to the studio. (a) A request whose `Host` is not
  `STUDIO_PUBLIC_HOST` (prod) or one of `STUDIO_LOCAL_HOSTS` (off; default `127.0.0.1:3400,localhost:3400`; the
  desktop native-smoke override sets `127.0.0.1:13400`) → 421 `wrong_host`. (b) A non-GET/HEAD
  request is refused with 403 `cross_origin_refused` unless `Sec-Fetch-Site` is `same-origin`, or (header absent)
  `Origin` equals `https://<STUDIO_PUBLIC_HOST>` (prod) or `http://` + one of `STUDIO_LOCAL_HOSTS` (off); neither header → refused.
  Non-browser scripts write to the API port directly; the **base** Compose file keeps the API on `127.0.0.1:8000`
  for local and desktop use (the prod overlay removes it).
- **R-PROXY-7** `lib/api.ts` uses relative `/api/v1/...` URLs. `NEXT_PUBLIC_VIDEO_POSTPRODUCTION_API_ORIGIN` is
  removed from the web app, `Dockerfile.web`, the base Compose file and the desktop smoke override; `lib/api.test.ts`,
  web/desktop `DOCPACK`, the runtime README and the desktop CSP (no more `:8000` entries) are updated.
- **R-HEALTH** API `GET /readyz`: `SELECT 1` and Redis `PING`, 2 s timeout each → 200 `{"status":"ok"}` /
  503 `{"status":"degraded"}`. `/healthz` stays liveness. Web `GET /healthz` (no identity or CSRF check, same `Host`
  check) calls `/readyz` with a 3 s timeout and returns the same two bodies only.
- **R-INLINE** Stream routes send `Content-Disposition: inline`; `/download` stays `attachment`.

### F3 — Upload and job pipeline robustness

- **R-INGEST** The upload route stops using `UploadFile = File(...)` (FastAPI would parse and spool the whole
  multipart body to the container's `/tmp` before any check runs). It reads `request.stream()` with a streaming
  multipart parser (`python-multipart`'s callback `MultipartParser`) and writes the file part straight into
  `STORAGE_ROOT/tmp/` while hashing; one copy, then FFprobe, then one transaction inserts asset, job and stages.
  Before reading the body, at the start of the handler: no `Content-Length` → 411 (if the spike shows undici
  cannot send it, chunked bodies are accepted and `MAX_UPLOAD_BYTES` is used as the estimate); `Content-Length >
  MAX_UPLOAD_BYTES` + 1 MiB of multipart overhead → 413; then an **in-process reservation** (one API replica;
  a lock-protected sum of the bytes reserved by uploads in progress): `free − reserved − Content-Length ≥ 20 %`
  of the volume, else 507 `insufficient_storage`. While streaming, the **whole request body** (all multipart parts) is counted against `MAX_UPLOAD_BYTES` + 1 MiB
  and the file part against `MAX_UPLOAD_BYTES`; passing either → 413 and the temp file is deleted. A chunked body
  (no `Content-Length`, only if the spike forces the fallback) reserves that same whole-body cap. The reservation is released when the request ends. The transcription budget
  is **reserved** atomically before reading (in-process, like the disk reservation), confirmed after the ingest
  commit and released on any failure (a 411/413/422/507 or an unreadable file costs nothing; with one unit left only
  one of two concurrent uploads proceeds). A failure deletes the temp file and leaves no row. Each upload in progress registers its temp path in `STORAGE_ROOT/tmp/active/<id>`, touched every 60 s by a timer
  for as long as the request is open (not only when bytes arrive) and deleted when the request ends. The sweep removes a temp file only when it has no active marker or the marker is older than
  10 min, and source files under the media prefix with no `media_assets` row only when older than 3 h (longer than
  the 2 h upload limit). An API start clears markers left by the previous process.
- **R-SIZE sizes** Migration 0004 alters `media_assets.size_bytes` to BIGINT; a Postgres test stores 2,147,483,648.
- **R-QUEUE** Stage states become `blocked → queued → running → succeeded | failed`. Ingest creates `probe`
  `queued` and `transcribe` `blocked`; migration 0004 turns any `queued` stage whose previous stage has not
  `succeeded` into `blocked`. Every hand-off sets the next stage `blocked → queued` with `enqueued_at = NULL` **in
  the same commit** that finishes the previous stage. Terminal commits are single transactions guarded by the
  lease (R-LEASE): the last stage's `succeeded`, the job's `succeeded` and, for a render, the `render_outputs`
  row's `succeeded` + `storage_key` + metadata are one commit; a failure sets the stage, the job, every remaining
  `blocked` stage (→ `skipped`) and the render row to their terminal states in one commit. A retry of a failed stage
  (`retry_stage`) sets it `queued`, every `skipped` stage after it back to `blocked`, and the job `retry_queued`, in
  one commit. After the commit the worker (or the API, for the first stage) enqueues and sets
  `enqueued_at`. A failed enqueue leaves the stage `queued` with `enqueued_at = NULL`; the job is **not** marked
  failed (the `queue_error` path is removed) and the API answers 202. The worker claims with a conditional update
  `queued → running` that also requires the previous stage `succeeded`, so a duplicate or early message is a no-op.
- **R-LEASE** Each claim writes a new `processing_stages.lease_token`. Every later worker write for that stage —
  heartbeat, output rows, finish, hand-off, failure — is a conditional update `WHERE id = <stage> AND status =
  'running' AND lease_token = <mine>`; when it matches no row the worker stops and discards its work. Output files
  are written under an attempt-scoped path (`…/attempt-<lease_token>/`) and moved to their final key only after a
  guarded check, then committed; files left by a crash between move and commit are removed by the sweep
  (job-directory media of a render whose row is not `succeeded` and whose stage has no live lease). A running stage
  refreshes `heartbeat_at` every 60 s from a worker thread. `recover_jobs` runs at API start and every 5 min in the
  API process: (a) a `running` stage whose heartbeat is older than 3 min loses its lease (token cleared); if attempts
  remain it goes back to `queued` and its job to `retry_queued`; on the last attempt stage, job and (for a render stage) the
  `render_outputs` row become `failed` / `lease_expired` in one commit; (b) every `queued` stage of a non-terminal job whose `enqueued_at` is null or older
  than `JOB_LEASE_SECONDS` is re-sent; (c) a `blocked` stage whose previous stage `succeeded`, in a non-terminal
  job, is set `queued` (self-heals a hand-off made by an old worker during an upgrade). A long stage with a live heartbeat is never taken over. `recover_stale_jobs`
  is replaced. Migration 0004 repairs old rows: a `running` stage gets `heartbeat_at = now()`; a job whose last
  stage is `succeeded` but which is still `running` becomes `succeeded`; a render job whose `render_outputs` row is
  already `succeeded` with a `storage_key` gets its stage and job set `succeeded`. Lease recovery never changes a
  `succeeded` render row.
- **R-UPGRADE** Upgrading to the release with migration 0004 stops the old worker first (`compose stop worker`,
  waiting for its current task up to 30 min, then `kill`), then starts the new API (migrate), then the new worker;
  `scripts/studio/update.sh` does this for every release and is the only documented update path (R-REL-1). The
  desktop runtime's rebuild/recovery action (`src/runtime/manager.js`, today `up --detach --build`) also stops the
  worker before `up`. Recovery rule (c) covers any path that still skips this. Messages left in Redis by the old worker are harmless
  because the new claim checks stage state and recovery re-sends `queued` stages. Test: upgrade a stack from
  b2f126a with a job in `probe` and one queued in Redis; both finish.
- **R-SCHEMA-WAIT** The worker `depends_on` the API with `condition: service_healthy` (healthy only after migrate),
  and at start checks that the database revision is at least its own head; if not it waits (every 5 s, up to
  5 min) before consuming the queue.
- **R-RERENDER** `RenderService.enqueue` returns an existing render only when it is not `failed`. The lookup covers
  the whole key family (`render:<asset>:<edl>:<version>` and every `…:retry<n>`) and takes the newest row under a
  row lock on the EDL: if the newest is not `failed` it is returned; if it is `failed` a new job and render row are
  created with `:retry<n+1>`. So the owner can render the same EDL version again after a failure, a lease expiry or
  a restore, and repeated requests never create duplicates. `POST /jobs/{job_id}/retry` on a render job goes
  through the same locked lookup: if a newer render of the family exists it answers 409 `render_superseded`;
  otherwise it retries that job in place.
- **R-QUALITY** API setting `TRANSCRIPTION_QUALITIES` (default `fast,accurate`). `GET /transcription/capabilities`
  adds `qualities`. A quality not listed → 422 `quality_unavailable` on create, retranscribe, upload into a project
  whose stored quality is not listed, and retry. The worker checks again before loading a model and fails the stage
  with `quality_unavailable`. `PATCH /api/v1/projects/{project_id}` `{transcription_quality}` (write gate) sets any
  listed quality while no job of the project is `queued` / `running` / `retry_queued` (else 409 `project_busy`). The
  UI shows only listed qualities and offers "เปลี่ยนเป็น fast" on a project whose stored quality is not listed.

### F4 — Production Compose overlay and images

- **R-COMPOSE-1** `services/video-postproduction/docker-compose.prod.yml`, used as
  `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` (Compose ≥ 2.24.4 for `!reset` /
  `!override`, checked by the deploy script). Every service gets `ports: !reset []`; the only ingress is the
  `tailscale` service (R-TS-1).
- **R-COMPOSE-2** Secrets reach only the service that needs them. `scripts/studio/render-env.sh` writes one file per
  service under `/srv/video-studio/env/` (mode 600) from Infisical `/video-studio`, never printing values:
  `api.env` (`VIDEO_EDITOR_API_TOKEN`, `DATABASE_URL`, `SENTRY_DSN`), `worker.env` (`DATABASE_URL`, `SENTRY_DSN`; no
  API token), `web.env` (`VIDEO_EDITOR_API_TOKEN`, `STUDIO_OWNER_LOGIN`, `STUDIO_PUBLIC_HOST`, `SENTRY_DSN`),
  `postgres.env` (`POSTGRES_PASSWORD`), `tailscale.env` (`TS_AUTHKEY`); `backup.env` (R2 keys, `RESTIC_PASSWORD`,
  `HEARTBEAT_TOKEN`) is read only by host scripts. The overlay uses `environment: !override` with the full
  non-secret mapping per service (so base defaults like `VIDEO_EDITOR_API_TOKEN: ${…:-}` cannot shadow the env file)
  and `env_file` per service. Non-secret prod values: `VIDEO_EDITOR_ALLOW_UNAUTHENTICATED=false`,
  `CORS_ALLOWED_ORIGINS=""`, `TRANSCRIPTION_PROVIDER=faster_whisper`, `TRANSCRIPTION_QUALITIES=fast`,
  `STUDIO_ACCESS_MODE=tailscale`. `scripts/studio/check-config.sh` renders `docker compose config` with dummy values
  and fails if a secret appears in a service outside its list (runs in CI).
- **R-COMPOSE-3** `restart: unless-stopped`; Celery `--concurrency=1`. Limits:

  | Service | mem_limit | cpus |
  |---|---|---|
  | api | 768 MB | 1.0 |
  | worker | 2.25 GB | 4.0 |
  | postgres | 512 MB | 1.0 |
  | redis | 256 MB (`--maxmemory 128mb`) | 0.5 |
  | web | 512 MB | 1.0 |
  | tailscale | 128 MB | 0.5 |

  ≈ 4.4 GB; with D3 the runner caps are 10 GB, worst case ≈ 14.4 GB of 15 GB, relying on zram (AC-12). Fallback if
  AC-12 fails: stop `ci-runner-local-2` while rendering (owner decision).
- **R-NONROOT** Both images run their process as uid/gid 10001. API image: `HF_HOME=/app/cache/huggingface`; its
  entrypoint starts as root only to `chown -R 10001:10001` `/app/storage` or `/app/cache` when their top-level
  owner differs, then `exec setpriv --reuid 10001 --regid 10001 --init-groups` the command. No extra Compose service
  (the desktop parser stays valid). The base file mounts `whisper_cache` at `/app/cache/huggingface` (model
  re-downloaded once). Web image runs `node server-launch.mjs` (R-PROXY-2; it loads Next's standalone `server.js`) as 10001 — the same
  command the spike uses. `server-launch.mjs` is added to the desktop bundle `REQUIRED_FILES`.
- **R-IMAGE** Images are tagged `video-studio-api:<tag>` / `video-studio-web:<tag>` via `STUDIO_TAG`; the previous
  tag's images stay on the host.
- **R-HOST** Checkout at `/srv/video-studio/src` (git tag); systemd `video-studio.service` (`compose up -d` /
  `down`) enabled so the stack returns after a reboot; D3 runner change recorded in the homelab runbook.

### F5 — Retention sweep (D6)

- **R-RET-1** `python -m video_postproduction.retention sweep [--dry-run]`; migration 0004 adds nullable
  `purged_at` to `media_assets` and `render_outputs`.
- **R-RET-2** Deleted: the source file of a `media_assets` row older than 30 days; the `render.mp4`
  (`render_outputs.storage_key`) of a row older than 90 days; stale temp files and attempt directories (R-INGEST,
  R-LEASE). Kept: all rows, `trace.json`, `MANUAL-STEPS.md`, `edl.json`, `timeline.edl` / `.fcpxml` / `.otio`,
  `captions*.srt|ass`, `history.jsonl`.
- **R-RET-3** In-flight protection: the sweep locks each candidate asset row (`SELECT … FOR UPDATE`) and skips it
  while a **non-terminal** job that uses it exists (job `queued`, `running` or `retry_queued`) or a render using
  it is `queued` / `rendering`; `blocked` stages of terminal jobs do not exist (they become `skipped`). Every path that
  queues work on a source locks the same row and checks `purged_at` first. A worker that still opens a purged source
  fails the stage with `source_purged`.
- **R-RET-4** Crash-safe order: set `purged_at` and commit, then delete; each run also deletes files of rows already
  marked. A marked row is purged even if its file still exists.
- **R-RET-5** Purged media: asset stream, render stream, render download and `job_file_route` for `render.mp4` →
  410 `media_purged`; queuing work on a purged source → 409 `source_purged`. The editor shows
  "ไฟล์ถูกลบตามระยะเก็บแล้ว". `production_trace verify` on a purged render reports `render.mp4` missing (expected).
- **R-RET-6** Daily 03:30 BKK systemd timer (`docker compose run --rm api python -m …`) under the host maintenance
  lock (R-BAK-1; waits up to 2 h, then exits `ok:false`), one JSON summary line, heartbeat `video-studio-retention`,
  `ok:false` when free disk on the storage volume is under 20 %.

### F6 — Observability and backups (PRJ-DSO-02)

- **R-OBS-1** The uptime worker cannot reach the tailnet (fact 15), so everything is a heartbeat. `HEARTBEATS` in
  `platform-uptime-worker` `src/targets.ts`: `video-studio-health` (30 min), `video-studio-worker` (3 h),
  `video-studio-backup` (36 h), `video-studio-retention` (36 h); the monitor is deployed before AC-19 is checked.
- **R-OBS-2** The health probe uses the real path: a timer every 10 min on **beelink** (already an owner tailnet
  node, allowed by the R-TS-2 grant) requests `https://video-studio.<tailnet>.ts.net/healthz` (TLS verified,
  10 s timeout) and reports `video-studio-health` (200 → ok; anything else, a TLS error or a timeout → `ok:false`).
  A broken Serve config, a stopped `tailscaled`, a down web or API all turn it red. The container healthchecks
  are kept for `compose ps`: tailscale uses `TS_ENABLE_HEALTH_CHECK`; the web healthcheck moves from `/` to
  `/healthz` and sends a `Host` the guard allows in that mode (`STUDIO_PUBLIC_HOST` in prod, the first
  `STUDIO_LOCAL_HOSTS` entry locally); `/healthz` needs no identity header (R-HEALTH). An hourly timer on Fedora runs `celery inspect
  ping` → `video-studio-worker`.
- **R-OBS-3** The worker calls `init_error_reporting()` in Celery `worker_process_init`. Web: errors-only Sentry from
  the web-starter `lib/sentry.ts` pattern (no traces, no replay, no bodies, URLs without query strings).
- **R-BAK-1** Nightly 02:45 BKK `scripts/studio/backup.sh` holds `flock /srv/video-studio/run/maintenance.lock`
  (shared with retention): `pg_dump -Fc`, **then** `restic backup` of the dump plus the storage volume excluding
  `tmp/` and `attempt-*` directories to R2 `video-studio-backup` (bucket-scoped token), then `restic forget
  --keep-daily 14 --keep-weekly 8 --keep-monthly 6 --prune`; logs repository size; heartbeat `video-studio-backup`.
  Because writers write files before committing rows (fact 13) and only retention deletes media (under the same
  lock), every row in the dump has its file in the snapshot. Files or history lines from work that committed after
  the dump are handled by the restore reconcile. A test asserts that no code path other than retention deletes media.
- **R-BAK-2** Size is bounded by D6; R2 is free to 10 GB, then about $0.015 / GB-month; the logged size shows growth.
- **R-BAK-3** Restore (`docs/runbooks/video-studio.md`, `scripts/studio/restore.sh`) into a throwaway Compose project
  (other project name, no ports, no `tailscale` service). Fix-up: `running` jobs → `retry_queued`, `running` stages →
  `queued`, `enqueued_at` cleared on every `queued` stage; `recover_jobs` re-sends them when the API starts (Redis is
  not restored). Reconcile by kind (job directories are `render-<id>` or `export-<id>`): an `export-*` directory and
  its history line are kept when its `trace.json` passes `production_trace verify` and either has `status =
  failed` (kept as evidence) or has `status = accepted` and every required export file exists on disk (`edl.json`,
  `timeline.edl`, `timeline.fcpxml`, `timeline.otio`, and `captions.srt` when the trace records captions, plus the
  `manual_guide.file` — checked separately because `verify` covers only `artifacts`); otherwise both are removed; an
  `export-*` history line whose directory is missing is removed; renders follow the
  dumped `render_outputs.status` — `succeeded` and `failed` kept, a non-terminal row has its job-directory files and
  history lines removed and becomes `failed` / `interrupted_by_restore` together with its stage, so render, stage and
  job agree (the owner renders again, R-RERENDER); a `render-*` directory or history line with no row is
  removed; source files with no `media_assets` row are removed. Counts are printed. Then one project's asset,
  transcript, EDL and render hash are checked against `trace.json`. Once before release, then monthly.

### F7 — Release, update and rollback

- **R-REL-1** Release = annotated tag `studio-vX.Y.Z` on `main`; the note records the Alembic head. Update:
  `scripts/studio/update.sh <tag>` (fetch, checkout, build, stop worker, `up -d` api, then worker; R-UPGRADE).
- **R-REL-2** Migrations from 0004 on are **additive only**; code of release N must work on the schema of N+1. The
  API start command becomes `python -m video_postproduction.migrate`: a known database revision → `upgrade head`; a
  revision newer than this image's head → log `schema_newer_than_code` and start. Production rollback never
  downgrades. `downgrade()` exists for development and CI.
- **R-REL-3** Rollback: checkout the previous tag (its Compose files too), set `STUDIO_TAG`, `up -d`, check web
  `/healthz` 200 and open one project over the tailnet. The earliest valid target is `studio-v0.1.0`.
- **R-REL-4** Runbook: start/stop, update, rollback, disk full, host reboot, rotating the API token and the Tailscale
  node (re-auth with a new key, state volume kept), restore.

### F8 — CI, lockfile, desktop bundle and gates

- **R-CI-1** Image job for both Dockerfiles with `platform-ci-workflows` image-scan v1.6.3 (blocking high/critical),
  SHA-pinned.
- **R-CI-2** `services/video-postproduction-api/uv.lock` with `production-trace` as a path source
  (`[tool.uv.sources]`); the Dockerfile runs `uv sync --frozen --no-dev --extra transcription --extra sentry`. The
  lockfile and `apps/video-postproduction-web/instrumentation.ts` are added to `REQUIRED_FILES`; a CI step stages the
  desktop bundle, builds both images from it, starts the base stack, and checks `/readyz` 200 and a worker ping.
- **R-CI-3** Migration tests on a Postgres service container: `upgrade head`, BIGINT (R-SIZE), old-row repair
  (R-QUEUE, R-LEASE) and the newer-schema start (R-REL-2).
- **R-CI-4** ADR-0004 gates with the inputs each workflow has: the API job (`ci-python.yml`) sets
  `coverage-threshold` (pytest fails when no test is collected); the web job sets `require-tests: true`, adds
  `@vitest/coverage-v8` at the same major as `vitest` (5.x), uses the workflow's `auto` test mode (which writes the
  Vitest coverage summary) and sets `coverage-threshold`. Callers move to the `platform-ci-workflows` release that
  has these inputs (SHA-pinned; today's pin is v1.1.1). Thresholds = measured baseline on `main` rounded down. New modules get their own gate, **per file**: the API job runs `scripts/check_module_coverage.py`, which reads
  `coverage json` and fails if any listed new module is below 90.00 % (two decimals, no rounding up), and `vitest.config.ts` lists the new web files (proxy route, identity, `server-launch.mjs`) in
  `coverage.include` — so a file no test loads is reported at 0 % — with per-file `thresholds` of 90 % lines.

## 3. Acceptance criteria

Tests: `services/video-postproduction-api/tests/` (`test_ingest.py`, `test_queue_recovery.py`, `test_retention.py`,
`test_readyz.py`, `test_quality.py`, `test_migrations_pg.py`, `test_migrate_wrapper.py`, `test_render_retry.py`),
`apps/video-postproduction-web/tests/` (`proxy.test.ts`, `identity.test.ts`), desktop compose/CSP tests extended.
Manual checks go to `evidence.md`.

| ID | Req | Given / When / Then | Proof |
|---|---|---|---|
| AC-1 | R-TS-1/2 | From an owner device on the tailnet, `https://video-studio.<tailnet>.ts.net` loads the studio with a valid certificate; from a tailnet device not allowed by policy (tested before go-live) and from the public internet it does not connect. | manual, screenshots, policy file |
| AC-2 | R-PROXY-1/3 | With a sentinel token, pages and proxied API/media responses never contain it in a body or header; with no token, no upstream request has `Authorization`. | `proxy.test.ts::token_never_leaves_server`, `::no_header_without_token` |
| AC-3 | R-COMPOSE-1, R-TS-1b | On Fedora, `ss -ltnp` and `docker compose ps` show no studio port; from the host and from the `api` container, `web:3000` / the tailscale container's IP on 3000 do not answer; `tailscale serve status` shows only 443 → `127.0.0.1:3000`, Funnel off; the reviewed tailnet policy is saved. | evidence |
| AC-4 | R-TS-3/4 | In `tailscale` mode: no `Tailscale-User-Login`, another login, or a tagged-device request → 401; the owner login passes. Missing `STUDIO_OWNER_LOGIN` / `STUDIO_PUBLIC_HOST`, or unset mode → server refuses to start. | `identity.test.ts` |
| AC-5 | R-PROXY-6 | In both modes: wrong `Host` → 421; a write with `Sec-Fetch-Site` same-site/cross-site, a foreign `Origin`, or neither header → 403; a same-origin write passes. | `proxy.test.ts::host_*`, `::csrf_*` |
| AC-6 | R-PROXY-5 | Paths with `..`, `%2e%2e`, `%2f`, `%5c`, NUL → 400 and nothing upstream, by unit test of the launcher **and** by raw HTTP requests (`curl --path-as-is`) against the built image; the API access log shows none of them. | `proxy.test.ts::invalid_path_*`, image HTTP test |
| AC-7 | R-PROXY-2, R-TS-5, R-INLINE | On the built standalone image with all checks on: a 2 GiB POST throttled to 20 Mbit/s (> 5 min) completes, web RSS stays under 300 MB, the API sees the right `Content-Length` (browser and desktop window); `Range: bytes=0-1023` → 206 + `Content-Range`; HEAD → headers only, upstream read cancelled; stream is `inline`; `<video>` seeks. | spike + `proxy.test.ts::range`, `::head_cancels`, evidence |
| AC-8 | R-PROXY-4 | Every proxied response has `private, no-store`. | `proxy.test.ts::no_store` |
| AC-9 | R-INGEST | Not enough free space → 507 before the body is read (also for two concurrent uploads that together exceed it); with one budget unit left, of two concurrent uploads one proceeds and one gets 429 before its body is read; a sweep during a 15-min throttled upload, including a 12-min pause with no bytes, does not touch its temp file; a chunked request whose non-file parts exceed the cap → 413; no `Content-Length` → 411; too large → 413; an unreadable video → no rows, no temp file, no budget spent; bytes in the container's `/tmp` do not grow during an upload; a 2,147,483,648-byte size round-trips on Postgres (R-SIZE). | `test_ingest.py`, `test_migrations_pg.py::bigint_size` |
| AC-10 | R-QUEUE, R-LEASE | Kill after the ingest commit, after the probe→transcribe hand-off, after a claim, and before the final commit → after restart each stage's results are committed exactly once (a stage may run again, its outputs are not duplicated) and the job ends `succeeded`; a duplicate or early `transcribe` message is a no-op; a long stage with a live heartbeat is not taken over; a stalled worker that wakes after losing its lease writes nothing; the last attempt's lease expiry ends the job `failed`; a failed enqueue leaves the job recoverable; old-schema rows are repaired; a worker stopped right after the render's terminal commit leaves render, stage and job all `succeeded` (lease recovery does not touch them); a failed `probe` leaves `transcribe` `skipped` and the source is retention-eligible; the R-UPGRADE test passes, and so does an upgrade that skips stopping the worker (rule c heals it); retrying a failed `probe` runs `transcribe` afterwards; a render whose row was `succeeded` under the old schema stays `succeeded` after migration and lease expiry. | `test_queue_recovery.py`, `test_migrations_pg.py::repair_*` |
| AC-11 | R-SCHEMA-WAIT | A new worker started on an old schema with a waiting message does not consume it until migrate is done, then runs it. | `test_migrate_wrapper.py::worker_waits` |
| AC-12 | R-COMPOSE-3 | With both CI runners busy and zram active (`zramctl` recorded), a 10-min 1080p render finishes, no `OOMKilled=true`, no OOM in `dmesg`. | evidence |
| AC-13 | R-PROXY-2, R-INGEST | From the owner's laptop over the tailnet (away from home, via DERP or direct), a 1.5 GB video uploads in one request and appears in the editor. | evidence |
| AC-15 | F1–F4 | Over the tailnet: upload → transcribe (Thai, fast) → edit EDL → render → download; `production_trace verify` passes. | evidence |
| AC-16 | R-QUALITY | With `fast` only: create, retranscribe, upload into an `accurate` project, and retry → 422; a queued `accurate` job fails with `quality_unavailable` at the worker; `PATCH` to `fast` makes the project upload again (409 while busy); UI shows no Accurate option and offers the switch. | `test_quality.py`, UI test |
| AC-17 | R-HEALTH | Postgres or Redis stopped → web `/healthz` 503 degraded; both up → 200. | `test_readyz.py`, evidence |
| AC-18 | R-RET-1…5 | Dry-run lists exactly the expired files; a real run deletes the render file and then render stream, download and `job_file_route` answer 410 while `trace.json` stays; kept files stay; `purged_at` set; purged routes → 410/409; an asset with active work is skipped; a run killed after commit is finished next run. | `test_retention.py` |
| AC-19 | R-OBS-1/2, R-RET-6, R-BAK-1 | Within 24 h of release the four heartbeats are green; stopping the `tailscale` container, breaking the Serve config, or stopping the API each turns `video-studio-health` `ok:false` within 10 min; retention with < 20 % free → `ok:false`; retention during a backup waits for the lock. | monitor `/status`, `test_retention.py::low_disk`, evidence |
| AC-20 | R-OBS-3 | A worker task error and a web server error each produce one scrubbed Sentry event. | fake-transport tests + one live check each |
| AC-21 | R-BAK-1/3 | A drill whose backup ran while a stage was `running`, with a render that finished between dump and file snapshot, plus an export, an export whose trace verifies but misses `MANUAL-STEPS.md`, and a failed render made before the backup, restores DB + media from R2; fix-up and reconcile run (the in-progress render becomes `failed`/`interrupted_by_restore` with files and history removed; the complete export, a failed export's trace and the failed render's trace stay, the incomplete export is removed; render, stage and job agree); the interrupted job completes; a new render of the same EDL version succeeds (R-RERENDER); hashes match `trace.json`; backup size is logged. | drill log |
| AC-22 | R-REL-2/3 | CI: an image of head N starts on a schema at N+1 and logs `schema_newer_than_code`. Host: rehearsal release `studio-v0.1.1` (one additive migration) rolled back to `studio-v0.1.0` in ≤ 10 min; `/healthz` 200; a project opens over the tailnet; no data lost. | `test_migrate_wrapper.py`, evidence |
| AC-32 | R-NONROOT, R-HEALTH | `docker compose ps` shows `web` healthy in prod and locally (healthcheck on `/healthz` with an allowed `Host`); the running web container's command is `node server-launch.mjs`. | evidence, desktop bundle test |
| AC-23 | R-NONROOT, R-CI-1 | Both processes run as 10001; image-scan clean of high/critical; a local stack with root-owned volumes from b2f126a upgrades and plays old media. | CI, evidence |
| AC-24 | R-DESK, R-PROXY-7, R-CI-2 | CI builds and starts the stack from the staged desktop bundle (`/readyz` 200, worker ping); the desktop `compose ps` parsing passes on it; the web image from the bundle refuses to start with `STUDIO_ACCESS_MODE` unset; on Windows the desktop window uploads and renders. | CI, desktop tests, evidence |
| AC-25 | R-HOST, R-TS-1 | After a Fedora reboot and after `compose up --force-recreate`, the stack and the same tailnet node (no new key) are back and `/healthz` is 200 with no manual step. | evidence |
| AC-26 | R-COMPOSE-2 | `check-config.sh` passes in CI: each secret only in its services (API token only in `api` and `web`; `TS_AUTHKEY` only in `tailscale`); an empty base default cannot override an env file value. | CI |
| AC-27 | R-CI-4 | API `coverage-threshold`, web `require-tests` + coverage threshold, and the per-file ≥ 90 % new-module gates fail CI when not met — including one module at 89.99 % while the average is above 90 %, and a new web file that no test loads. | CI, `test_check_module_coverage.py` |
| AC-30 | R-RERENDER | A failed render of an EDL version can be enqueued again and succeeds; enqueuing again while the retry runs or after it succeeded returns the retry, not a new job; two concurrent requests create one retry; `/jobs/{old}/retry` after a newer retry exists → 409. | `test_render_retry.py` |
| AC-31 | D7 | The restic repository on R2 contains no readable media (object names and contents are restic packs only), and the drill restores media with the Infisical key. | drill log |
| AC-28 | ADR-0007 floor | Project list, new project (upload progress) and editor reflow without horizontal scroll at 320 / 375 / 390 / 768 / 1440 px; upload, render and download work by keyboard alone with visible focus; design-floor checker passes. Before and after deploy. | screenshots, keyboard log, checker output |
| AC-29 | R-TS-4 (dev), R-PROXY-6 | `npm run dev` with the committed `.env.development` serves the studio against a local API on `127.0.0.1:8000`; a production build ignores that file; the desktop native smoke on `127.0.0.1:13400` passes. | web test, desktop smoke, evidence |

(AC-14 merged into AC-9.)

## 4. Threats and how this spec handles them

| Threat | Handling |
|---|---|
| Access from the internet | No public hostname, Funnel off, no published port (AC-1, AC-3) |
| Another tailnet device or a shared node | Tailnet grant to the owner only (R-TS-2); owner login re-checked by the web app (AC-4) |
| A web page in the owner's browser sends writes (tailnet auth is network-level) | `Host` check + same-origin check (AC-5) |
| Token theft from the browser | Token server-side only (AC-2) |
| LAN device reaches API / DB / secrets | No published ports in prod; secrets only in their service (AC-26) |
| Path traversal via the proxy | Segment allow-list, fixed base (AC-6) |
| Huge or malicious upload | Size cap, free-space check before the body, FFprobe, budget (AC-9) |
| Lost or duplicated jobs | Blocked stages, leases with tokens, recovery (AC-10) |
| Disk fills | Free-space check, retention, `ok:false` < 20 % (AC-9, AC-19) |
| Data loss / inconsistent restore | Locked backups, reconcile, drill (AC-21) |
| CI and studio fight for RAM | Limits, D3, zram (AC-12) |

## 5. Risks and open points

1. Home power / internet outage takes the studio down; accepted, heartbeats alert.
2. Whisper `small` Thai quality is today's local quality; better STT is a later spec.
3. Devices without Tailscale cannot use the studio (accepted by D5). Upload speed away from home depends on the
   home downstream and on whether Tailscale gets a direct path or relays through DERP.
4. The in-process write limiter sees one client (the web container); acceptable for one user.
5. Owner actions in P2 (each with its admin URL when asked): review the whole tailnet policy and remove allow-all,
   tag + grant, a one-use tagged auth
   key into Infisical `/video-studio`, confirm MagicDNS + HTTPS, R2 bucket token, Sentry web project, D3 runner
   change.

## 6. Delivery order (TDD; red/green log in `red-green.md`)

1. Spike on the built standalone image (R-PROXY-2), including a 2 GiB throttled POST. The spike is a throwaway
   branch `spike/video-studio-proxy`; its findings go to `red-green.md` and it is **not merged**. The real proxy,
   launcher and ingest are then written test-first (red run recorded) in step 3 and 5. If the spike fails, stop and
   revise this spec.
2. Migration 0004 + migrate wrapper + Postgres tests (R-SIZE, R-REL-2, repairs).
3. R-INGEST, R-QUEUE, R-LEASE, R-SCHEMA-WAIT — tests first.
4. R-QUALITY, R-HEALTH, R-INLINE, R-OBS-3.
5. F2 proxy, F1 identity mode, relative URLs, desktop CSP — tests first.
6. F5 retention — tests first.
7. F4 overlay with `tailscale` service, per-service env, non-root entrypoint, F8 CI, `uv.lock`, desktop bundle, gates.
8. Codex review, merge to `main`, tag `studio-v0.1.0`.
9. Host setup (owner tailnet actions first), F6 monitor entries and backups, D3.
10. Rehearsal release `studio-v0.1.1` and rollback (AC-22); evidence for all ACs in `evidence.md`.
