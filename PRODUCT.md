# Content Production (Book Production Platform)

Status as of 2026-10-22. This page describes only what is in this repository today.

## Purpose

A local-first toolkit that turns structured input (JSON IRs, CSV/XLSX, briefs) into finished
files through deterministic engines: 3D product scenes, graphics batches, spreadsheets, SQL/data
pipelines, CAD parts, BIM quantity takeoff, audio masters, Unity game prototypes, documents
(DOCX/PDF invoices), landing pages, marketing packages, form-to-document workflows and video
post-production (transcribe → EDL → render). Rules and code do the work; a model is used only
in the optional "brief adapter" step that turns a free-text brief into an IR proposal.

## Users

The owner (solo operator) running the tools on a workstation. There are no external users and
no hosted deployment.

## What exists and works

- `book-production` CLI (`src/book_production_platform/`): scene, graphics, game, spreadsheet,
  audio, cad, data, bim, plus `doctor` (offline runtime readiness report).
- Services under `services/` (FastAPI or CLI packages, each with its own tests):
  document, data, marketing, web and workflow production APIs, production hub (artifact
  registry), production pipelines (monthly report chain), video post-production API + worker
  and its Docker Compose runtime.
- Apps under `apps/`: production console (Next.js), video studio web (Next.js) and video studio
  desktop (Electron, Windows packaging).
- CI (shared `platform-ci-workflows` v1.1.1): the full root pytest suite with Ruff and strict mypy
  on the typed modules (`scripts/check.py`), one job per service/package/app, and the security
  workflow. Blender, FreeCAD and Unity are not installed in CI, so their real-runtime tests skip.

## How to run

- Platform CLI: `uv sync --extra test` (from the committed `uv.lock`), then
  `uv run book-production doctor` and `uv run python scripts/check.py`.
- Each service/app: see its own README (install from that folder, run its tests).
- Video post-production stack: `services/video-postproduction/README.md` (Docker Compose,
  bound to `127.0.0.1`).

## AI

- **No service calls an LLM provider directly with a provider key.** Every generative step goes
  through `brief-adapter-kit` (`packages/brief-adapter-kit`, vendored copy in `src/`), whose only
  network code is `POST {gateway}/api/ai/generate` on the central Solo Empire AI gateway
  (contract `solo-empire.ai-gateway.v1`) with a bearer token read from an env var named in a
  gitignored provider config. The gateway owns the provider keys and model choice; this repo
  does not pin a model id (the reply's `model` field is recorded).
- Guards in the kit: SQLite budget ledger with per-job (≤ 2 calls), per-day and per-month
  caps reserved before each call, privacy class gate (`private`/`restricted` never dispatched),
  prompt-injection abstain, `mode=shadow` by default (`live` only by owner approval).
- Demo / no-key mode: without a provider config every adapter answers
  `provider_not_configured` and the deterministic structured routes still work with
  `model_calls: 0`.
- Evals: per-tool `brief-shadow` eval sets with held-out gates exist (for example
  `services/video-postproduction-api/tests/fixtures/highlight_eval`, 30 cases, gate in that
  README). gap: no recorded live shadow-run results against the real gateway in this repo.
- Video transcription (`services/video-postproduction-api`): default `faster_whisper` runs a
  local Whisper model (no network call after the model download). The optional
  `openai_compatible` provider posts audio to a configured base URL and sends no credential.
  Write routes fail closed without a 24+ character `VIDEO_EDITOR_API_TOKEN` (or the explicit
  loopback opt-in), are rate-limited per client, and transcription jobs have a daily cap
  (in-process counters; see that service's README).
- No key-shaped strings were found in the git history (scan on 2026-10-22).

## Observability

`services/video-postproduction-api` (the only service with a container image) has opt-in
Sentry: `sentry` extra + `SENTRY_DSN`, errors only, allowlist scrubber. gap: the two Next.js
apps (`apps/production-console-web`, `apps/video-postproduction-web`) have no Sentry; they are
local-only UIs with no host.

## Limits

- Local tool only; no live URL, no auth for multiple users, no hosted runtime.
- Many products are V1A (bounded first versions); see each README section for the exact scope
  and error codes.
- Typecheck debt recorded in `.github/workflows/ci.yml`: `video-postproduction` and
  `video-postproduction-api` run without mypy in CI (20 errors remain in the API package).
- Only the root platform package has a `uv.lock`; services and packages install from their
  `pyproject.toml` ranges.

## Disposition

Lab / personal production toolkit (no tier decided in the registry yet). Keep.
