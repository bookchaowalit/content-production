---
title: Video Studio parity pass — multi-clip render, silence and filler cut, reverse guide
date: 2026-11-12
status: accepted (owner goal 2026-11-12, reverse guides spec)
owner: Product owner (Book Dev)
tool: video-studio (services/video-postproduction-api)
---

# Video Studio parity pass

Matrix: [docs/parity/video.md](../../parity/video.md). Rules for flows and
reverse guides: [2026-11-12 reverse guides spec](2026-11-12-reverse-guides-and-flow-labels-design.md).

## Problem

The engine plans, exports and documents timelines with any number of clips,
but the renderer refuses more than one segment. So a confirmed highlight
proposal, or any edit that removes the pauses from a talk, can be opened in
Resolve but not rendered. Descript, CapCut and Premiere all remove pauses and
filler words from the transcript; the engine has the word timings to do it
deterministically but does not.

## V1 Multi-clip render (P1)

- `build_ffmpeg_argv(edl, ..., plan=TimelinePlan)` renders N ≥ 2 segments of
  one source in one FFmpeg run: per clip `trim=start_frame=IN:end_frame=OUT`
  and `atrim` at the same frame boundaries (`IN/fps`, `OUT/fps`), then
  `concat`, then the existing reframe, caption burn-in and loudness chain.
  `IN`/`OUT` are the plan's frames, so the render cuts on exactly the frames
  that `MANUAL-STEPS.md`, `timeline.edl/.fcpxml/.otio` name.
- N ≥ 2 without a plan, segments with different visual instructions, colour
  operations, or non-`a_roll` visuals are refused before FFmpeg starts
  (`RenderCapabilityError`). One segment keeps the existing `-ss/-t` argv.
- A source without audio concatenates video only (`concat=...:a=0`).
- Captions: for N ≥ 2 the cue windows are frame-exact (`IN/fps`, `OUT/fps`,
  record start `record_in_frame/fps`), the editor `captions.srt` and the burned
  file are built from the same cues, so the burn-in and the editor file agree
  with the concatenated picture.

AC-V1a argv for 3 clips contains one `trim` and one `atrim` per clip with the
plan's frames, one `concat=n=3:v=1:a=1`, and maps `[vout]`/`[aout]`.
AC-V1b a real FFmpeg render of a 3-clip EDL has exactly `plan.record_frames`
video frames (skipped when no FFmpeg is found).
AC-V1c the render service renders a multi-clip EDL and the burned ASS has the
cues at record positions.

## V2 Silence and filler cut (P1)

`video_postproduction.silence.silence_cut(edl, transcript, *, min_gap_s=0.6,
padding_s=0.15, remove_fillers=True)` returns a new `EDLDocument`:

1. Speech intervals come from word timings when a transcript segment has
   words, otherwise from the segment. With `remove_fillers`, words that are
   only a filler (`um`, `uh`, `uhm`, `erm`, `er`, `hmm`, `mm`, `เอ่อ`, `เอิ่ม`,
   `อ่า`, `อืม`, `อ้า`; case and punctuation ignored) are not speech.
2. Each interval is padded by `padding_s` on both sides, clipped to the base
   segment's range, and intervals closer than `min_gap_s` are merged.
3. Each kept interval becomes a segment that copies the base segment's
   visual, caption, audio and colour settings, id `<base>_k<nn>`, provenance
   `{"reason": "silence_cut", "base_segment": ..., "min_gap_s": ...,
   "padding_s": ..., "fillers_removed": n}`. Times are rounded to 1 ms.
4. No speech in the range → `SilenceCutError("no_speech")`. Bounds:
   `0.1 ≤ min_gap_s ≤ 5`, `0 ≤ padding_s ≤ 1` (`SilenceCutError("invalid_options")`).

API: `POST /api/v1/projects/{id}/edl/silence-cut` (write access) with
`{"min_gap_s", "padding_s", "remove_fillers"}` saves the result as the next
EDL version (`status=draft`). 409 `edl_not_ready` / `transcript_not_ready`,
422 `silence_cut_no_speech` / `silence_cut_invalid`.

AC-V2a gaps longer than `min_gap_s` become cuts, shorter gaps stay.
AC-V2b a filler word between two words is cut out when it leaves a gap
≥ `min_gap_s` with padding; with `remove_fillers=False` it stays.
AC-V2c deterministic (same input → same EDL) and validates with `validate_edl`.
AC-V2d the route saves version n+1 and refuses without a transcript.

## V3 Flow and reverse guide

`video_postproduction/flow.py` declares `FLOW` (validated by
`production_trace.reverse.validate_flow`). The reverse guide
`docs/reverse/video-studio/davinci-resolve.md` is rendered from the committed
fixture `services/video-postproduction-api/tests/fixtures/reverse/` (probe
facts, synthetic transcript, base EDL → silence cut → plan →
`build_resolve_guide`), with goldens = sha256 of `edl.json`, the three
timelines, `captions.srt` and `captions-burned.ass`. Drift test:
`tests/test_reverse_guide.py`.

## Out of scope this pass

Caption styles beyond font size, karaoke, overlays/B-roll inside the engine
EDL, ducking, transitions, LUTs, named platform presets: P2 in the matrix.
