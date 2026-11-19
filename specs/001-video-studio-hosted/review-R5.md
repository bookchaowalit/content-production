# SPEC-001 review round R5 — Codex on commit 5f1c4d3 (v0.8) (2026-11-19)

Each finding was checked against the code at b2f126a. Outcome: **revise** → v0.9.

| # | Sev | Finding | Verified | Change in v0.9 |
|---|---|---|---|---|
| 1 | P1 | Old worker running during migration 0004 enqueues a `blocked` stage that nobody claims | yes (`stages.py:175-178`) | R-UPGRADE stop-old-worker order + upgrade test |
| 2 | P1 | Spike uses a launcher the shipped image does not | yes (v0.8 text) | one start command `node server-launch.mjs`, bundle file, AC-32 |
| 3 | P1 | Render row committed apart from stage/job; lease recovery can fail a finished render | yes (`render/service.py:262-268`) | one guarded terminal commit for render + stage + job; AC-10 |
| 4 | P2 | Sweep can delete the temp file of a slow upload in progress | yes (1 h vs 2 h) | active markers, 3 h orphan age; AC-9 |
| 5 | P2 | Budget check before + take after lets two uploads pass with one unit | yes (`limits.py`) | atomic reservation, release on failure; AC-9 |
| 6 | P2 | `blocked` stages of failed jobs keep sources forever | yes | `skipped` on failure; R-RET-3 by job state; AC-10 |
| 7 | P2 | Retry lookup uses the exact old key | yes (`render/service.py:332-341`) | key family + newest row under lock; AC-30 |
| 8 | P2 | Web healthcheck on `/` fails the Host guard | yes (`docker-compose.yml:116-121`) | `/healthz` with an allowed Host; AC-32 |
| 9 | P2 | `production_trace verify` passes incomplete or failed exports | yes (`trace.py:272-286`) | status + required files + hashes; AC-21 |
| 10 | P2 | `--include … --fail-under` is an average, not per module | accepted (coverage.py semantics) | per-file script, 2-decimal check; AC-27 |
| 11 | P2 | Path check after Next normalisation | accepted | raw path check + image HTTP test; AC-6 |
| 12 | P2 | Spike code could skip test-first | yes (ADR-0004) | throwaway branch, not merged |

Disputed: none.
