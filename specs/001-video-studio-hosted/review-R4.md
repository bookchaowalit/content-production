# SPEC-001 review round R4 — v0.7 (Tailscale only) (2026-11-19)

Reviewers: a fresh Claude reviewer subagent (Sonnet, no maker context) and Codex (`gpt-6.2-sol`, Stop hook) on
v0.7. The Stop hook also reported findings still open on the v0.6 commit `a1f4f6a`; those that still apply after
the Tailscale pivot are included (chunk/session findings are void because F2 chunked upload was removed). Each
finding was checked against the code at b2f126a. Outcome: **revise** → v0.8.

| # | Sev | Source | Finding | Verified | Change in v0.8 |
|---|---|---|---|---|---|
| 1 | P1 | both | `UploadFile = File(...)` makes FastAPI spool the whole multipart body to `/tmp` before any check; 507/411 "before the body" impossible; not one copy | yes (`routes.py:221-252`) | R-INGEST streams `request.stream()` with a callback multipart parser into `STORAGE_ROOT/tmp`; AC-9 checks `/tmp` |
| 2 | P1 | Codex | One-use auth key re-used on every start | accepted (`TS_AUTH_ONCE` default false) | `TS_AUTH_ONCE=true`, AC-25 force-recreate |
| 3 | P1 | Codex | No published port ≠ only Serve reaches `web:3000` (host, other containers) | yes (Compose networking) | R-TS-1b `network_mode: service:tailscale`, web on 127.0.0.1; AC-3 |
| 4 | P1/P2 | both | Node `requestTimeout` 300 s and undici 300 s timeouts cut slow uploads | accepted (Node / undici defaults) | launcher + undici Agent 2 h, throttled spike, AC-7 |
| 5 | P1 | Codex (v0.6) | Media to R2 contradicts "media stays at home" | yes (D4 vs R-BAK-1) | D7 open decision (encrypted R2 vs LAN-only media), AC-31 |
| 6 | P1 | Codex (v0.6) | A failed render of the same EDL version can never be rendered again | yes (`render/service.py:329-342`) | R-RERENDER, fact 21, AC-30 |
| 7 | P2 | Codex | Existing allow-all policy defeats the grant | accepted | whole-policy review before join, AC-1/AC-3 |
| 8 | P2 | Codex | Host guard breaks the desktop smoke on 13400 | yes (`smoke-runtime.js:8`) | `STUDIO_LOCAL_HOSTS`, AC-29 |
| 9 | P2 | Codex | Free-space check per request races with concurrent uploads | yes | in-process reservation, AC-9 |
| 10 | P2 | Codex | Loopback health check misses a broken Serve | yes | probe from beelink over the tailnet, AC-19 |
| 11 | P2 | Codex | `require-tests` dropped although `ci-node.yml` has it | yes (`ci-node.yml:48`) | kept for web; caller release bump |
| 12 | P2 | Claude | Checks in middleware could cap/buffer the body | accepted | R-TS-5 checks in route handler/layout only |
| 13 | P2 | Claude | undici may not send `Content-Length` with a stream body | unverified | spike checks it; chunked fallback in R-INGEST |
| 14 | P2 | Codex (v0.6) | Last-attempt lease expiry leaves the render row non-terminal | yes | render row failed in the same commit |
| 15 | P2 | Codex (v0.6) | Source file published but not committed is never cleaned | yes (write before commit) | sweep rule in R-INGEST |
| 16 | P2 | Codex (v0.6) | Export history line without directory kept | yes | reconcile removes it |
| 17 | P2 | Codex (v0.6) | AC-10 says "runs once" but a stage may run twice | yes | "committed exactly once" |
| 18 | P2 | Codex (v0.6) | ≥ 90 % for new modules has no gate | yes | per-module coverage gates, AC-27 |
| 19 | P3 | Claude | Failed uploads spend budget / limiter | yes (`dependencies.py:106-114`) | budget taken after commit |
| 20 | P3 | Claude | Unsure Tailscale details (Host forwarded, socket path, header strip, CT logs) | open | P2 host check list, CT log noted in R-TS-2 |
| 21 | P3 | Claude | Status line, `R-UP-8` leftover name | yes | fixed, renamed `R-SIZE` |

Disputed: none.
