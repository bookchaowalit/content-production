# SPEC-001 review round R6 — Codex on 47e2e26 (amended) and 069b929 (v0.9) (2026-11-19)

`47e2e26` was amended into `069b929`; the two reports overlap. The union was checked against the code at b2f126a.
Outcome: **revise** → v0.10.

| # | Sev | Finding | Verified | Change in v0.10 |
|---|---|---|---|---|
| 1 | P1 | Spike still says `node server.js` | yes (v0.9 text) | spike uses `node server-launch.mjs` |
| 2 | P1 | Old-schema render `succeeded` while stage/job `running` can be failed by lease recovery | yes (`render/service.py:262-268`) | migration repair + recovery never touches a succeeded render |
| 3 | P1 | Other upgrade paths (`up -d`, desktop recovery `up --detach --build`) skip stopping the worker | yes (`manager.js:332-342`) | `update.sh` only path, desktop stops worker, recovery rule (c) self-heals |
| 4 | P2 | Retry of a failed `probe` leaves `transcribe` `skipped` | yes (`stages.py:334-351`) | retry resets later `skipped` → `blocked` |
| 5 | P2 | Identity fallback to `off` breaks the hostname and is silent | yes | go-live stops instead |
| 6 | P2 | Marker refreshed only by arriving bytes | yes | timer for the request lifetime |
| 7 | P2 | Chunked fallback has no whole-body cap | yes | whole-body count + same cap for reservation |
| 8 | P2 | Restore removes failed exports' traces | yes (`exports.py` `finish_failure`) | failed exports kept as evidence |
| 9 | P2 | `verify` does not check `MANUAL-STEPS.md` | yes (`trace.py:203-213,272-286`) | manual guide checked separately |
| 10 | P2 | `/jobs/{id}/retry` bypasses the render retry rule | yes (`routes.py:325-342`) | same locked lookup, 409 `render_superseded` |
| 11 | P2 | Raw path checked too late (inside Next) | accepted | check in `server-launch.mjs` at the HTTP entry |
| 12 | P2 | Vitest reports only loaded files | accepted (Vitest default) | `coverage.include` for new files |

Disputed: none.
