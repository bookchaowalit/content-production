# Scene3D V1A — feature parity with Blender and KeyShot

Scope: `scene3d` (tool id `scene3d-v1a`). The engine takes one preflighted
local GLB and a `scene-ir.v1` made only of registered presets, builds an
editable studio scene in the pinned Blender 5.2.2 LTS, reopens it in a fresh
process, and renders a PNG. It is a product-hero recipe, not a general 3D
editor: SceneIR has no free numbers, so every look is a named preset.

Status: **have** / **partial** / **missing**; priority P1 next, P2 later,
P3 on demand, — out of scope.

## Capability matrix

| Capability | Blender / KeyShot | Engine status | Priority | Notes |
|---|---|---|---|---|
| Import GLB/glTF 2.0 | yes | have | — | self-contained GLB only, strict preflight (no external buffers, URLs, animation, unapproved extensions) |
| Import FBX / OBJ / USD / STEP | yes | missing | P2 | convert to GLB upstream for now |
| Auto-fit product to frame | KeyShot "fit", Blender manual | have | — | `*-fit.v1` compositions back the camera off to the bounding sphere × 1.15 |
| Camera angle presets | manual / camera presets | have | — | three-quarter, three-quarter-fit, front, side, top, plus 8 orbit angles |
| Free camera position / focal length | yes | — | — | by design SceneIR has no free numbers; lens fixed at 55 mm |
| Turntable (360° set of angles) | KeyShot turntable animation, Blender orbit rig | **have (new: encode)** | — | `scene3d.turntable` writes one SceneIR per 45° step (1/2/4/8 frames); `scene3d.turntable_encode render` composes each angle as its own job and encodes a GIF (`turntable-gif.v1`) or H.264 MP4 (`turntable-mp4.v1`) as a separate content-addressed, traced FFmpeg job. Stepped orbit (8 stills), not a smooth 36-frame spin |
| Lighting presets | KeyShot light presets / HDRIs | have | — | `softbox-three-point.v1`, `high-key-white.v1`, `dramatic-rim.v1` (area disk lights), `studio-hdri.v1` |
| HDRI environment lighting | core in KeyShot | **have (new)** | — | `studio-hdri.v1`: Poly Haven *Studio Small 09* 1k EXR (CC0), sha256-pinned, 4 MiB cap, packed into `scene.blend`; camera rays still see the background preset. One HDRI only; no rotation/strength choice (P3) |
| Background presets | backdrop / environment colour | have | — | `studio-neutral.v1`, `studio-white.v1`, `transparent.v1`, `transparent-shadow.v1` |
| Transparent background (cut-out PNG) | Film ▸ Transparent / KeyShot alpha | have | — | RGBA PNG, no ground plane |
| Shadow catcher under transparent product | Blender shadow catcher / KeyShot ground shadows | **partial (new)** | P2 | `transparent-shadow.v1`: EEVEE has no shadow catcher, so the ground is transparent except an ambient-occlusion contact shadow kept in alpha (fades out 1.5–3 m from the product axis). Directional cast shadows from the key light are not caught; a Cycles shadow-catcher profile would add them |
| Material override presets (matte, gloss, metal) | KeyShot material library | **have (new)** | — | `matte.v1`, `gloss.v1` (clear coat), `brushed-metal.v1` (anisotropic) override the finish of every Principled BSDF and keep base colour and its texture; `preserve-source.v1` unchanged. No recolour or per-part assignment (P2) |
| Render engine | EEVEE, Cycles / KeyShot CPU+GPU | partial | P2 | EEVEE 64 samples in production; Cycles CPU is a benchmark candidate only |
| Resolution profiles | any | partial | P2 | `square-1024.v1`, `landscape-1920x1080.v1`; no 4K or 4:5 |
| Depth of field | yes | missing | P3 | |
| Render passes / AOVs, multi-object scenes, props | yes | missing | P3 | |
| Editable source file | `.blend` / `.bip` | have | — | `scene.blend` with packed textures |
| Replay / content addressing | — | have | — | fingerprint of IR + asset + Blender build; tamper rejected |
| History, provenance, redo by hand | — | have | — | `trace.json` + `history.jsonl` + Blender `MANUAL-STEPS.md` (HDRI node setup, shadow-catcher nodes and material values included); FFmpeg `MANUAL-STEPS.md` for turntable encodes |
| Golden renders | — | **have (new)** | — | `tests/test_scene3d_goldens.py`: five full compose jobs cover every lighting, background and material preset on Blender 5.2.2 LTS; 128 px thumbnails, ≤1 % of pixels may differ by ≥16 levels (a gloss→source swap changes 2.4 %, a lost shadow 5.8 %). Runs when `BLENDER_BIN` is set; not in CI (no Blender there) |

## Implemented in this pass (P1)

1. **Render check on the pinned build.** Blender 5.2.2 LTS (Linux x64, sha256
   checked against `blender-5.2.2.sha256`) rendered every preset through the
   real `compose_scene` job. `high-key-white.v1`, `dramatic-rim.v1`,
   `studio-white.v1` and `transparent.v1` looked as intended, so the earlier
   "runtime-dependent, not verified" note no longer applies. Golden thumbnails are in
   `tests/fixtures/scene3d/goldens/`.
2. **Shadow catcher**: `transparent-shadow.v1` (new id; `transparent.v1` is
   unchanged so old fingerprints still replay).
3. **Material overrides**: `matte.v1`, `gloss.v1`, `brushed-metal.v1`
   (`scene_builder.MATERIAL_PRESETS`). A product whose materials have no
   Principled BSDF is refused with `material_preset_unsupported`.
4. **HDRI**: `studio-hdri.v1` with the bundled CC0 EXR
   (`scene3d/assets/README.md` records source, author, licence and sha256).
   A changed or missing file is refused before Blender starts
   (`hdri_asset_mismatch` / `hdri_asset_unavailable`).
5. **Turntable encode**: `scene3d.turntable_encode` (`encode` for existing
   stills, `render` to compose and encode). The output folder is
   content-addressed by frame hashes, preset and FFmpeg version, has its own
   receipt, and returns a replay when nothing changed. Transparent stills are
   flattened onto white and recorded as such.

The V1B brief adapter still does not propose any of these presets
(`scene_builder.ENGINE_ONLY_PRESETS`), because its 30-case eval set does not cover
them. Structured SceneIR can use them directly.

## Roadmap (prioritised)

1. P2 — Cycles shadow-catcher profile, so transparent cut-outs also keep the key
   light's cast shadow.
2. P2 — 4K and 4:5 render profiles, and a Cycles production profile.
3. P2 — FBX/OBJ intake through a converter that runs the same preflight.
4. P2 — Material recolour and per-part material assignment (needs a part-naming
   contract in SceneIR).
5. P2 — Smooth turntable (e.g. 36 frames at 10°) once render time allows;
   today 36 frames at about 25 s each take about 15 minutes on CPU EEVEE.
6. P2 — Run the golden renders in CI, which needs Blender on a runner plus a GPU or
   software GL that EEVEE accepts.
7. P3 — HDRI rotation and strength variants, depth of field, render passes.
