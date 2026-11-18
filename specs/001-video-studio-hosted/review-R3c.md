# SPEC-001 review round R3c — Codex on 5b004ef / 26fcc7c / 27a69aa (2026-11-18)

Reviewer: Codex (`gpt-6.2-sol`, Stop hook). It ran on three v0.5 commits: `5b004ef` and `26fcc7c` were amended
into `27a69aa` (the only one on the branch); their findings overlap. The union was checked against the code at
b2f126a. Outcome: **revise** → v0.6.

| # | Sev | Finding | Verified | Change in v0.6 |
|---|---|---|---|---|
| 1 | P1 | A worker that lost its lease can still write results | yes (no fencing in v0.5) | `lease_token` on claim, every write guarded, attempt-scoped files; AC-10 |
| 2 | P1 | Restore reconcile drops traces of renders that failed before the backup | yes (README: failed jobs write a job dir) | keep `succeeded` and `failed` renders; only non-terminal ones are reset; AC-21 |
| 3 | P2 | Render, stage and job disagree after restore; re-render path unclear | yes | reset in one commit; re-render via the existing route; AC-21 |
| 4 | P2 | Old-schema rows have no heartbeat; job left `running` after last stage `succeeded` | yes (`stages.py:308-312`) | migration 0004 repair; AC-10 |
| 5 | P2 | Lease expiry on the last attempt re-queues forever | yes | stage + job `failed`/`lease_expired`; AC-10 |
| 6 | P2 | Resume compares only received chunks; unsent chunks may differ | yes | whole-file hash before create, stored on the session, compared on resume; AC-13 |
| 7 | P2 | History lines of renders with no row kept | yes | removed by prefix `render-`; AC-21 |
| 8 | P2 | Export kept without checking it is complete | yes | keep only when `trace.json` verifies |
| 9 | P2 | Files published but not committed are never cleaned | yes (`render/service.py:240-268`) | attempt path + sweep rule |
| 10 | P2 | Chunks of aborted/failed sessions can stay | yes | cleanup at start and in the sweep |
| 11 | P2 | Failed enqueue marks the job failed, so recovery cannot help | yes (`services.py:189-196`) | stage stays `queued`, `queue_error` path removed; AC-10 |
| 12 | P2 | First `complete` after expiry not refused | yes | 410 `upload_expired`; AC-13 |
| 13 | P2 | Vitest coverage needs `@vitest/coverage-v8` | yes (`package.json` has `vitest ^5.0.5` only) | dependency in R-CI-4 |

Disputed: none.
