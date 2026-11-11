# Game V1A — feature parity with the Unity and Godot editors

Scope: `game_production` + `game_ir` (tool id `game-v1a`). The engine is a
deterministic scene composer for one template, `top-down-collect.v1`: a strict
GameIR grid (player, wall cells, objectives, exit, optional local audio) is
compiled by a pinned Unity 6 (6000.6.2f1) Editor in batch mode into an editable
project, verified (scene hierarchy report, EditMode + PlayMode tests, gameplay
trace, fresh-process reopen), and published content-addressed. `game export`
builds a Web or Windows player from an accepted project. It is not a general
game editor.

Status: **have** = implemented and tested; **partial** = a subset works;
**missing** = not implemented. Priority: P1 next, P2 later, P3 only on demand,
— = out of scope.

Honesty note: Unity is not installed on the host used for this change. Trace,
guide, metrics, and map are verified at the GameIR level with the existing fake
Unity stages; the Unity Editor click steps are derived from `SceneCompiler.cs`
and are not runtime-verified.

## Capability matrix

| Capability | Unity / Godot | Engine status | Priority | Notes |
|---|---|---|---|---|
| 2D top-down scene from data | yes | have | — | one GameObject per entity at `CellCenter`, scale = tile |
| Grid / tilemap | Tilemap + Grid / TileMapLayer | partial | P2 | grid is in the IR; Unity scene uses GameObjects (no Tilemap). Guide offers a Grid with Cell Size = tile as a snapping aid |
| Player controller | Input System / CharacterBody2D | have | — | WASD/arrows, speed from IR, Rigidbody2D continuous |
| Collectibles + win condition | scripting | have | — | `GameProgress`, `Collectible`, `ExitGate`, gameplay events |
| Walls / colliders / world bounds | yes | have | — | BoxCollider2D walls + 4 boundaries |
| Camera framing | Cinemachine / Camera2D | partial | P3 | fixed orthographic size `max(w, h) × tile × 0.6` |
| Audio (music + collect SFX) | yes | have | — | GameIR v2, local hashed assets |
| Automated tests (EditMode / PlayMode) | Test Runner / GUT | have | — | required for acceptance |
| Player builds (Web, Windows) | Build Profiles / Export | have | — | `game export` |
| Reachability validation | — | have | — | BFS: every objective and the exit reachable from spawn |
| Level metrics / difficulty | Analytics, custom tools | **have (new)** | — | `level-metrics.json`: shortest collect-all route (exact up to 12 objectives, heuristic above), steps, est. seconds, dead ends, junctions, detour ratio, heuristic difficulty band |
| Level map preview | Scene view / editor screenshots | **have (new)** | — | `level-map.svg` with route, `ascii_map` in the guide |
| Traceability per run | — | **have (new)** | — | `trace.json` (validate, assets, runtime/template hashes, Unity stages + test totals, gameplay QA, publish), `history.jsonl`, `ir.json`; compose and export, success and failure |
| "How would I do this by hand" | tutorials | **have (new)** | — | Unity 6 guide: every entity's name, cell, exact Transform position, components and values; Play mode check in route order; Build Profiles guide for export |
| Moving hazards / enemies | yes | **partial (new)** | P1 | GameIR v3 `hazards` (ping-pong patrol, `step_ticks` per cell), time-expanded safe-run proof (`unsafe_hazard_schedule`), metrics/map/guide/QA done and tested. Unity `HazardPatrol`, compiler slots and PlayMode tests are written but **not run**: the composer template scene still has no hazard slots, so v3 levels with hazards are refused with `unsupported_game_feature` until `BuildComposerTemplate` is run on a licensed Unity. V1B adapter does not propose hazards |
| Multiple levels / scene flow | Build Settings scene list / SceneTree | **partial (new)** | P1 | `game pack`: `game-level-pack.v1` (2–12 GameIR files in play order), per-level metrics, difficulty curve with an ascending check (`difficulty_not_ascending`), each level built as its own `game build` job, content-addressed pack manifest + `difficulty-curve.svg` + Unity guide for one project in Build Profiles order. Missing: a runtime scene-flow component (next level on win) and a single combined Unity project |
| Doors / keys / switches | scripting | missing | P2 | |
| Animation / sprite sheets | Animator / AnimationPlayer | missing | P3 | palette sprites only |
| UI (HUD, score, menus) | uGUI / UI Toolkit / Control | missing | P2 | |
| Physics puzzles, tile properties | yes | missing | P3 | |
| Arbitrary scripting | C# / GDScript | — | — | out of scope: trusted template scripts only |
| Mobile / console targets | yes | missing | P3 | Web + Windows only |
| AI brief → GameIR (V1B) | — | have | — | allowlist unchanged: no new entity types were added in this change |

## Implemented in this change

1. Trace + history + Unity manual guide for every compose and export run with
   valid input; artifacts hashed in place for `production_trace verify`.
2. Deterministic level metrics with an optimal collect-all route
   (`game_production/metrics.py`).
3. Top-down level map SVG + ASCII map (`game_production/level_map.py`).
4. AI provenance sidecar from `game brief`, honoured only on a hash match.

## Moving hazards (GameIR v3) — status

- Model: `hazards: [{id, path: [cells], step_ticks}]`, at most 8 hazards and
  16 path cells each. Paths use 4-neighbour cells that are unique, open, and
  not the spawn, an objective or the exit. One tick is `tile_size / player.speed`.
  World side is at most 32 and the joint hazard period at most 240 ticks.
- Proof (`game_ir/hazards.py`): conservative grid model. During a tick, both cells
  a hazard moves between are dangerous; the player may wait or step and must
  avoid both. The validator checks that a run collecting everything and reaching
  the exit exists. Off-path cells are always safe, so a greedy order is complete.
- Metrics: `safe_route_ticks` and `hazard_wait_ticks`, plus a hazard term in the
  difficulty score. These keys exist only for hazard levels, so v1/v2 metrics are
  byte-identical.
- Unity (written, not yet run): `HazardPatrol` runs a kinematic trigger on fixed
  time, and a hit sends the player to spawn. The template has 8 hazard slots and
  `hazard.png`. PlayMode touches each hazard once (`hazard_hit:<id>` in the
  gameplay trace, which QA requires) and checks the patrol path and timing.
- To finish: on a licensed Unity 6000.6.2f1, run
  `BookProduction.Editor.SceneCompiler.BuildComposerTemplate` to rebuild
  `Source/scene-composer-template.unity`, then build a hazard level end to end.
  The template hash is part of every game fingerprint, so existing jobs will
  rebuild rather than replay.

## Roadmap (prioritised)

1. P1 — Finish hazards on Unity (template rebuild + real build), then V1B
   allowlist + eval cases.
2. P1 — Level packs, Unity half: a `LevelFlow` runtime component (load the next
   Build Profiles scene on win) and one combined project with every scene,
   verified by a PlayMode test that plays through the pack.
3. P2 — Tilemap output option (walls as a Tilemap with a CompositeCollider2D)
   to match how designers usually build grid levels.
4. P2 — HUD with collected / total counter.
5. P3 — Camera follow for worlds larger than one screen.
