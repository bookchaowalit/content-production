# SPEC-001 review round R1 (2026-11-18)

Reviewers: fresh Claude reviewer subagent (Sonnet, no maker context) and Codex (`gpt-6.2-sol`, read-only, Stop
hook). Both reviewed v0.1. Every finding was checked against the code at b2f126a before it was accepted.
Outcome: **revise** → v0.2.

| # | Sev | Source | Finding | Verified | Change in v0.2 |
|---|---|---|---|---|---|
| 1 | P1 | Codex | Rollback by tag fails: old image runs `alembic upgrade head` on an unknown revision | yes (`docker-compose.yml` api command) | R-REL-2, R-REL-3, AC-22 |
| 2 | P1 | Codex | 32 MiB chunk exceeds Cloudflare 30 s proxy write timeout at 3 Mbit/s | accepted (Cloudflare docs) | 8 MiB chunks, AC-13 throttled test, risk 3 |
| 3 | P1 | both | Proxy adds token to cross-site writes (CSRF; Access cookie is same-site) | yes (`routes.py:198-217` POST without body) | R-PROXY-6, AC-5 |
| 4 | P1 | Codex | Desktop runtime bundle would miss a new `uv.lock` | yes (`runtime-bundle-manifest.js:4-30`) | R-CI-2, AC-24 |
| 5 | P1 | Claude | `complete` doing concat + hash + FFprobe + ingest in one request exceeds 100 s | yes (`services.py` two copies) | async assemble R-UP-4/5, single copy |
| 6 | P1 | both | sha256 needed at session create contradicts hashing while uploading; checked after rows are created | yes | sha256 at `complete`, verified before any row, AC-10 |
| 7 | P1 | Claude | RAM budget leaves nothing for OS/page cache | yes (arithmetic) | worker 2.25 GB, zram, host-level AC-12, fallback |
| 8 | P1 | both | Access check fail-open when env missing; `iss`/`alg`/`kid` unspecified | yes | `STUDIO_ACCESS_MODE`, `jose`, AC-4 |
| 9 | P1 | Claude | Non-root breaks existing volumes and model cache path | yes (`NamedTemporaryFile` 600 root, `/root/.cache`) | R-NONROOT `volume-init`, `HF_HOME`, AC-23 |
| 10 | P2 | Codex | Backup and retention can interleave → dump rows point to deleted files | yes | shared flock, R-BAK-1 |
| 11 | P2 | Codex | Retried `complete` creates duplicate asset/job | yes (`services.py` new id per call) | atomic state, ids stored on session, AC-9 |
| 12 | P2 | Codex | `media_assets.size_bytes` INTEGER < 2 GiB cap | yes (`0001_v1_core.py:36`) | BIGINT, AC-14 |
| 13 | P2 | Codex | Chunk PUTs hit the 60/min write limit | yes (`dependencies.py:101-103`) | separate chunk limiter, R-UP-2 |
| 14 | P2 | Codex | Resume after reload has no file handle | yes | localStorage + re-pick file, R-UP-9 |
| 15 | P2 | Codex | `WHISPER_MODEL=small` does not stop `accurate` → `medium` | yes (`tasks.py:29-37`) | R-QUALITY, AC-16 |
| 16 | P2 | Codex | `--env-file` does not pass variables into containers | yes | R-COMPOSE-2 `env_file` |
| 17 | P2 | Codex | Cloudflare may cache private MP4 | accepted | R-PROXY-4, Cache Rule, AC-8 |
| 18 | P2 | Codex | `/healthz` is constant ok | yes (`main.py:42-44`) | `/readyz`, AC-17 |
| 19 | P2 | Codex | Heartbeat names must be registered in the monitor | yes (`targets.ts` HEARTBEATS) | R-OBS-1 |
| 20 | P2 | Codex | Worker never initialises Sentry | yes (only `main.py:28`) | R-OBS-3, AC-20 |
| 21 | P2 | Codex | Retention not crash-safe | yes | mark-then-delete, R-RET-3 |
| 22 | P2 | Codex | AC not linked to requirements/tests (ADR-0004) | yes | §3 rewritten with Req + G/W/T + test ids |
| 23 | P2 | Claude | Budget not returned; expired sessions block cap; no host disk check | yes | R-UP-1, R-UP-7, AC-11 |
| 24 | P2 | Claude | Real table names; purged media on retry/render/job files; backup size | yes (`models.py:44,161`) | R-RET-1…4, R-BAK-2 |
| 25 | P2 | Claude | `attachment` on stream, no HEAD, query string, `Bearer undefined` | yes (`routes.py:290-301`) | R-INLINE, R-PROXY-1/5, spike step |
| 26 | P2 | Claude | Desktop fixtures/docs still use `NEXT_PUBLIC_…` | yes | R-PROXY-8 |
| 27 | P2 | Claude | AC-2 build-grep meaningless; missing ACs | yes | AC-2 runtime sentinel, AC-17…25 |
| 28 | P3 | Claude | Compose ≥ 2.24.4 for `!reset`; CORS default interpolation | yes | R-COMPOSE-1/2 |

Disputed: none.
