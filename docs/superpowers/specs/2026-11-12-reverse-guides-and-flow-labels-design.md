---
title: Reverse guides and flow labels for every production tool
date: 2026-11-12
status: accepted (owner goal 2026-11-12)
owner: Product owner (Book Dev)
---

# Reverse guides and flow labels

## Problem

Every tool already writes a per-job `MANUAL-STEPS.md` (a `ManualGuide` built
from the validated IR), but:

1. There is no committed, reviewable reference guide per tool and benchmark
   program, so nobody can read "how do I make this by hand in Figma / Word /
   Resolve" without running a job first.
2. Nothing says which part of a tool's flow is deterministic logic, which part
   an AI model may propose, and where a person must decide. Parity matrices
   list capabilities but not this split.
3. A hand-written reference would drift from the engine.

## Decision

### R1 Flow labels

Each part of a tool's flow carries exactly one label:

| Label | Meaning | Rule |
|---|---|---|
| `LOGIC` | rules / code, same input gives the same output | the default; the flow must complete with `model_calls: 0` using only `LOGIC` and `HUMAN-GATE` parts |
| `AI-ASSIST` | a model proposes something (brief → IR, layout, copy draft, highlight pick) | only through `brief-adapter-kit` → AI gateway; shadow eval first, `live` only by owner approval; its output always passes a `LOGIC` validation part before use; it always has a `LOGIC` fallback |
| `HUMAN-GATE` | a person decides (approve client text, pick an option, review) | recorded in the trace or review files |

Each tool declares its flow as data (`FLOW` in the tool module, or a
`flow.py` next to its `manual.py`): an ordered list of `FlowPart(part,
label, engine, trace_stage, manual_note, fallback)`. `production_trace.reverse.validate_flow`
rejects a flow that has no `LOGIC` part, an `AI-ASSIST` part without a
`fallback`, or an `AI-ASSIST` part that is not followed by a `LOGIC` part.

Parity matrices get a `Flow` column with the same labels (or `—` for out of
scope, with the reason in Notes).

### R2 Reverse guide file

`docs/reverse/<tool-id>/<program-slug>.md`, rendered by
`production_trace.reverse.render_reverse_guide` from:

- tool id, benchmark program, the committed example input it was built from,
  and the command that regenerates it;
- the flow table (R1), each part mapped to the engine function and trace stage,
  and for `AI-ASSIST` parts what a person decides instead when working by hand;
- the engine's own `ManualGuide` for that example, rendered with
  `render_manual_markdown` (numbered steps, real menu paths and shortcuts,
  exact values);
- golden outputs: file name and sha256 of the deterministic artifacts the guide
  reproduces, and a checklist.

The text is generated; nobody edits it by hand.

### R3 Drift test

Each tool has a test that rebuilds the guide from the committed example (no
external runtime: the guide is built from the IR, goldens from deterministic
outputs) and compares it with the committed file through
`production_trace.reverse.assert_reverse_guide`. With
`REVERSE_GUIDES_UPDATE=1` the test rewrites the file instead, so a change to a
tool's guide is a reviewed diff.

### R4 Verification evidence

When a person (or an agent driving the real program) follows a reverse guide,
the result is recorded in `docs/reverse/<tool-id>/VERIFY.md`: date, program and
version, who/what followed it, the comparison method (pixel diff, measured
values, cell values) and the result. A tool counts as verified only with a row
there.

## Acceptance criteria

- AC1 `validate_flow` raises `FlowError` with a stable code for: empty flow
  (`flow_empty`), no `LOGIC` part (`flow_no_logic`), unknown label
  (`flow_label_unknown`), `AI-ASSIST` without fallback (`flow_ai_no_fallback`),
  `AI-ASSIST` not followed by `LOGIC` (`flow_ai_unvalidated`).
- AC2 `render_reverse_guide` output is deterministic (same inputs → same bytes),
  contains the flow table, the manual steps, and the golden table, and has no
  absolute local paths.
- AC3 `assert_reverse_guide` fails with a diff hint when the committed file
  differs or is missing, and writes the file when `REVERSE_GUIDES_UPDATE=1`.
- AC4 Both copies of `production_trace` (package and vendored) stay identical.
- AC5 Every tool in `catalog/production-tools.yaml` has at least one reverse
  guide with a drift test, and its parity matrix has the `Flow` column.

## Out of scope

- Driving the benchmark programs automatically in CI.
- Translating program UI labels (they stay as the program shows them).
