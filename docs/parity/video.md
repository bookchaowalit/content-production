# Video Studio — feature parity with Premiere Pro, DaVinci Resolve, CapCut, and Descript

Scope: tool id `video-studio` — `services/video-postproduction-api` (FastAPI +
Celery worker: EDL, timeline plan, FFmpeg render, interchange, trace, manual
guide, highlight proposals), `services/video-postproduction` (pipeline CLI:
subtitles, overlay, replace-audio), `packages/video-postproduction-edl`, and the
web/desktop apps. The engine is a deterministic, transcript-driven batch editor
for one source clip at a time (talking-head and screen recordings → vertical
shorts). It is not a free-form NLE, so effects-heavy rows are P2/P3 or out of
scope rather than "next".

Status: **have** = implemented and tested; **partial** = a subset works;
**missing** = not implemented. Priority: P1 next, P2 later, P3 only on demand,
— = done or out of scope. "Pipeline" = `services/video-postproduction`.
Updated 2026-11-12 (replaces `services/video-postproduction-api/docs/PARITY.md`, 2026-10-03).

## Capability matrix

| Capability | Premiere Pro / DaVinci Resolve / CapCut / Descript | Engine status | Priority | Notes | Flow |
|---|---|---|---|---|---|
| Import + probe source (size, type, duration, fps, codecs) | Import / Media Pool / Import / Upload | have | — | upload limits, ffprobe validation, sha256; one source per EDL | LOGIC |
| Proxy / optimized media on ingest | Proxies / Optimized Media / auto / cloud | missing | P3 | renders straight from the original | LOGIC |
| Several source clips in one timeline | yes / yes / yes / yes | missing | P2 | `build_timeline_plan` needs exactly one source asset | LOGIC |
| Transcription Thai + English with word timings | Speech to Text / AI transcription (Studio) / Auto captions / Transcription | have | — | local faster-whisper (default, no network) or an OpenAI-compatible base URL. **Gap:** the OpenAI-compatible option calls the provider directly, not through `brief-adapter-kit` → AI gateway | AI-ASSIST |
| Correct the transcript by hand | Transcript panel / — / Edit captions / Edit transcript | missing | P2 | transcript is read-only in the API; fallback today is a hand-written `transcript.v1` | HUMAN-GATE |
| Trim one source range | yes / yes / yes / yes | have | — | frame-accurate in the guide | LOGIC |
| **Multi-clip render** (several ranges, any order) | yes / yes / yes / yes | **have (new)** | — | `trim`/`atrim` on the plan's frames + `concat`, then reframe, burn-in, loudness; verified frame-exact against MLT | LOGIC |
| **Silence / pause removal** | Text-based editing ▸ Remove pauses / Cut page smart tools / Remove pauses / Remove gaps | **have (new)** | — | `silence_cut` + `POST /projects/{id}/edl/silence-cut`; min gap, padding; deterministic from transcript timings (no audio analysis) | LOGIC |
| **Filler word removal** | Filler word detection / — / Remove filler words / Remove filler words | **have (new)** | — | word-level when the transcript has word timings; fixed Thai/English list (`um`, `uh`, `เอ่อ`, `อืม` …). Captions still show the whole segment text after a cut (word-level cues: P2) | LOGIC |
| Text-based editing (delete words in the transcript) | Text-Based Editing / — / Edit by transcript / core | partial | P2 | segment-id ranges → confirmed EDL (`highlights.cli propose`, `model_calls 0`); not in the API or web app; no word-level delete | LOGIC |
| Highlight / clip extraction from a brief | — / — / Long video to shorts / Clips (Underlord) | have (shadow; live by owner approval) | — | model picks segment-id ranges only, through `brief-adapter-kit` → AI gateway; 30-case eval `tests/fixtures/highlight_eval` | AI-ASSIST |
| Ground the highlight proposal | — | have | — | known ids, segment-aligned bounds, durations, no overlap, no model timestamps | LOGIC |
| Confirm the proposal before it becomes an EDL | — | have | — | `build_confirmed_edl` needs `confirmed=true` and a matching fingerprint | HUMAN-GATE |
| CMX3600 EDL / FCPXML 1.10 / OTIO export | Import EDL / Import AAF-XML-EDL-OTIO / — / Export to Premiere-Resolve-FCP | have | — | every render and export job; opened with OpenTimelineIO 0.18.1 (all three: exact frames) in [VERIFY.md](../reverse/video-studio/VERIFY.md) | LOGIC |
| FCP7 XML / AAF (Premiere's native import) | yes / yes / — / yes | missing | P2 | Premiere reads the CMX3600 EDL; FCPXML needs a converter | LOGIC |
| Burned-in captions, Thai `newmm` line breaks | Captions / Subtitle track / Auto captions / Captions | have | — | ASS on the output canvas; same cues as `captions.srt` | LOGIC |
| SRT / VTT caption files | Export SRT / Export subtitle / Export SRT / Export SRT-VTT | have | — | `captions.srt` per job (editor line breaks); VTT through the pipeline `subtitles` command | LOGIC |
| Caption style presets (colour, box, position) | many / many / many / many | partial | P2 | 4 presets change only the font size | LOGIC |
| Karaoke / word-by-word highlight | Essential Graphics / Text+ / yes / yes | missing | P2 | word timings exist; ASS `\k` not emitted | LOGIC |
| Caption translation | Translate captions / — / Translate / Translate | missing | P3 | would be a gateway proposal + human review | AI-ASSIST |
| Graphic overlay (logo, lower third) | yes / yes / yes / yes | partial | P2 | pipeline `overlay` with trace and guide; engine EDL rejects `graphic` | LOGIC |
| B-roll / picture-in-picture | yes / yes / yes / yes | missing | P2 | EDL kinds `asset`/`stock` reserved | LOGIC |
| Replace audio with a mastered track | yes / yes / yes / yes | partial | P2 | pipeline `replace-audio` with trace and guide; not in the engine EDL | LOGIC |
| Loudness normalize (LUFS target) | Loudness Auto-Match / Normalize Audio / Normalize / Studio Sound level | have | — | single-pass `loudnorm`; verified −14.0 LUFS both sides | LOGIC |
| Music bed + ducking under speech | Auto-ducking / Fairlight sidechain / Auto ducking / Ducking | missing | P2 | | LOGIC |
| Noise reduction / voice isolation | Enhance Speech / Voice Isolation (Studio) / Noise reduction / Studio Sound | missing | P2 | | LOGIC |
| 9:16 centre crop reframe | yes / yes / yes / yes | have | — | output now SAR 1:1 (fixed this pass) | LOGIC |
| 1:1 and 16:9 centre crop | yes / yes / yes / yes | partial | P2 | any size fits with letterbox (`source` profile); crop only for 9:16 | LOGIC |
| Auto-reframe / speaker tracking | Auto Reframe / Smart Reframe (Studio) / Auto reframe / Layouts | missing | P3 | needs a vision model; EDL `active_speaker` reserved | AI-ASSIST |
| Transitions, speed ramps, keyframed punch-in | yes / yes / yes / partial | missing | P2 | | LOGIC |
| Colour presets / LUT | Lumetri / Color page / Filters / — | missing | P2 | `color` operations rejected | LOGIC |
| Markers / chapters | yes / yes / — / chapters | partial | P2 | one FCPXML marker per segment; no chapter list | LOGIC |
| H.264 MP4 export | yes / yes / yes / yes | have | — | libx264 CRF 23, AAC | LOGIC |
| H.265 / ProRes, bitrate control | yes / yes / yes / partial | missing | P2 | fixed CRF; the guide gives the measured bitrate | LOGIC |
| Named platform presets (TikTok, Reels, Shorts, YouTube) | Export presets / Deliver presets / Export / Export presets | partial | P2 | size and profile come from the EDL; no named presets | LOGIC |
| Output frame-rate conversion | yes / yes / yes / yes | missing | P2 | keeps the source rate; trace warns when `output.fps` differs | LOGIC |
| Thumbnail / cover frame | — / — / Cover / — | missing | P3 | | LOGIC |
| Background render queue | Media Encoder / Render Queue / — / cloud | have | — | Celery worker, idempotent jobs | LOGIC |
| Versioned edits, job history, verify | auto-save / project versions / drafts / versions | have | — | EDL versions, `history.jsonl`, `python -m production_trace verify` | LOGIC |
| Review the render before publishing | Frame.io / Collaboration / — / comments | partial | P2 | the web app previews the render; no comments or approval record | HUMAN-GATE |
| Multi-user collaboration / comments | Team Projects / Collaboration (Studio) / Teams / Comments | — | — | not an editor | — |
| Make it by hand (reverse guide) | — | **have (new)** | — | [docs/reverse/video-studio/davinci-resolve.md](../reverse/video-studio/davinci-resolve.md), followed in MLT 7.42 ([VERIFY.md](../reverse/video-studio/VERIFY.md)); Resolve smoke test is an owner item | LOGIC |

Flow column: `LOGIC` rules/code (repeatable), `AI-ASSIST` a model proposes through the AI gateway and a `LOGIC` step validates it, `HUMAN-GATE` a person decides, `—` out of scope. Transcription is labelled `AI-ASSIST` (a model proposes text; `normalize_provider_result` validates it) even though its default runs locally; its OpenAI-compatible option is a direct provider call outside the gateway (reported, not hidden). Flow data: `services/video-postproduction-api/video_postproduction/flow.py`. See [the flow spec](../superpowers/specs/2026-11-12-reverse-guides-and-flow-labels-design.md).

Counts (42 rows): before this pass, with the same rows re-read against the
code — have 14, partial 9, missing 18, out of scope 1; open P1: 3 (multi-clip
render, silence removal, filler removal; the old matrix called the first P0).
After: **have 18, partial 8, missing 15, out of scope 1; open P1: 0.** P1s from
the 2026-10-03 matrix that are not core to a transcript-driven batch editor
(caption styles, karaoke, overlay/B-roll in the EDL, ducking, platform presets)
are re-scoped to P2; auto-reframe needs a vision model and is P3.

## This pass (2026-11-12)

Spec: [2026-11-12-video-studio-parity.md](../superpowers/specs/2026-11-12-video-studio-parity.md).

1. **Multi-clip render.** `build_ffmpeg_argv(..., plan=)` trims each clip on the
   plan's frames (`trim=start_frame/end_frame`, `atrim` at the same frame
   boundaries) and concatenates before the shared chain; multi-clip captions use
   frame-exact record positions for both the burn-in and `captions.srt`. Tests:
   `tests/test_multiclip_render.py` (argv, no-audio source, loudness after
   concat, refusals, cue positions, render service, real FFmpeg frame count).
2. **Silence and filler cut.** `video_postproduction/silence.py` and
   `POST /api/v1/projects/{id}/edl/silence-cut`. Tests:
   `tests/test_silence_cut.py`, `tests/test_api_silence_cut.py`.
3. **Square pixels for the 9:16 crop** (`setsar=1`), found by following the
   reverse guide. Test: `test_vertical_crop_output_has_square_pixels`.
4. **Flow + reverse guide.** `video_postproduction/flow.py`;
   `docs/reverse/video-studio/davinci-resolve.md` from
   `tests/fixtures/reverse/` (drift test `tests/test_reverse_guide.py`).

## Roadmap (P2, in order)

1. Word-level caption cues after silence/filler cuts; transcript correction in the API.
2. Caption styles (colour, box, position) and karaoke `\k`.
3. Graphic overlay, B-roll and replace-audio inside the engine EDL (pipeline recipes exist), then ducking.
4. Named platform presets; 1:1 / 16:9 crop; frame-rate conversion; H.265.
5. Several source clips per timeline; transitions; LUTs.
6. Route the OpenAI-compatible transcription provider through the AI gateway (or retire it).
