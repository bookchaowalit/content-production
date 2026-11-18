# SPEC-001 review round R2 (2026-11-18)

Reviewers: the same Claude reviewer subagent (R2 pass on v0.2) and Codex (`gpt-6.2-sol`, Stop hook, fresh run on
v0.2). Each finding was checked against the code at b2f126a. Outcome: **revise** → v0.3.

| # | Sev | Source | Finding | Verified | Change in v0.3 |
|---|---|---|---|---|---|
| 1 | P1 | Claude | `volume-init` service breaks desktop `compose ps` parsing | yes (`compose.js:98-123`, `SERVICE_NAMES`) | entrypoint chown + `setpriv`, no new service (R-NONROOT) |
| 2 | P1 | both | Rollback target b2f126a cannot run hosted; downgrade of BIGINT fails with real data; R-REL-2/3 contradict | yes | additive-only + migrate wrapper, never downgrade in prod, target ≥ v0.1.0, AC-22 rehearsal v0.1.1→v0.1.0 |
| 3 | P1 | Codex | Commit-then-enqueue gap and crash between ingest and session update | yes (`services.py:183-196`) | one guarded transaction, `enqueued_at` + R-QUEUE, AC-10 |
| 4 | P1 | Codex | `hash-wasm` blocked by desktop CSP (no `wasm-unsafe-eval`) | yes (`studio-csp.js:3-16`) | pure-JS `@noble/hashes`, AC-24 desktop upload |
| 5 | P1 | Codex | One shared env file leaks every secret to every service; base `environment` overrides env file | yes (Compose semantics, base file) | per-service env files, `environment: !override`, `check-config.sh`, AC-26 |
| 6 | P2 | Claude | Disk check per session is TOCTOU; temp location unclear | yes (`services.py` temp in `/tmp`) | reservation sum, temp in `STORAGE_ROOT/tmp`, AC-9 |
| 7 | P2 | Claude | Background task uses closed request session; no fencing on takeover | yes (`dependencies.py` yield session) | own session, heartbeat + token guard, AC-11 |
| 8 | P2 | Claude | Two render copies; only one purged | yes (`render/service.py:262`, `jobtrace.py:61`) | R-RET-2 both copies, AC-18 |
| 9 | P2 | Claude | CSRF rule unclear in `off` mode; HEAD reads whole upstream body | yes | R-PROXY-6 both modes + direct API port locally; HEAD cancels upstream |
| 10 | P2 | Codex | Backup vs API chunk deletes | yes | exclude `uploads/` `tmp/`, restore fix-up deletes open sessions |
| 11 | P2 | Codex | Redis queue not restored | yes (`stages.py:334-338` failed-only retry) | restore fix-up + R-QUEUE, AC-21 |
| 12 | P2 | Codex | Budget returns from retention can't reach in-process counter; day boundary | yes (`limits.py`) | R-BUDGET in DB, same-day return, AC-11 |
| 13 | P2 | Codex | Retention can delete a source that a queued job still needs | yes | row lock + skip active, worker `source_purged`, AC-18 |
| 14 | P2 | Codex | Proxy strips `Retry-After` | yes | added to R-PROXY-3, AC-13 |
| 15 | P2 | Codex | Fast-only not enforced for existing `accurate` projects and queued jobs | yes (`tasks.py:29-37`) | R-QUALITY at upload/retry + worker check, AC-16 |
| 16 | P2 | Codex | Spike on `next start` ≠ standalone entrypoint | yes | spike and AC-7 on the built standalone image |
| 17 | P2 | Codex | `uv sync --frozen` drops extras and `production-trace` | yes (Dockerfile) | extras + path source, CI starts stack from bundle |
| 18 | P2 | Codex | ADR-0004 test/coverage gates not enabled | yes (`ci.yml`) | R-CI-4, AC-27 |
| 19 | P2 | Codex | UX floor misses 320/390 reflow and keyboard | accepted (ADR-0007) | AC-28 |
| 20 | P3 | Claude | Missing ACs (env allow-list, web Sentry, backup size, lock, wrong-file resume); R-DESK undefined | yes | R-DESK defined; AC-19/20/21/24/26 extended |
| 21 | P3 | Claude | zram unconfirmed | yes | AC-12 records `zramctl` |

Disputed: none.
