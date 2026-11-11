# video-studio — reverse guide verification

Rule: [reverse guides spec R4](../../superpowers/specs/2026-11-12-reverse-guides-and-flow-labels-design.md).

| Date | Guide | Program | Followed by | Method | Result |
|---|---|---|---|---|---|
| 2026-11-12 | [davinci-resolve.md](davinci-resolve.md) | MLT `melt` 7.42.0 (Docker `mltframework/melt:latest`, the engine of Shotcut / Kdenlive) — the guide's alternative to Resolve, which is not installed here; OpenTimelineIO 0.18.1 for the shortcut imports | Claude agent: [`follow_guide_mlt.py`](evidence/follow_guide_mlt.py) reads every value from the guide text and turns each numbered step into one MLT operation (project profile, Append whole clip, 6× Razor, 4× Ripple Delete last-first, check Record IN/OUT, Inspector Zoom → `affine`, Import Subtitle + track style → `subtitle_feed` + `subtitle`, Normalize → `dynamic_loudness`, Deliver H.264/AAC) | Engine render (`build_ffmpeg_argv`, FFmpeg n9.0.2) vs melt export of the same synthetic source: frame count, per-frame Y PSNR above the caption band, ±1-frame offset test, caption on/off frames, EBU R128 loudness; interchange files opened with OTIO adapters `otio_json`, `cmx_3600`, `fcpx_xml` | **Pass with tolerance** (below) |

## 2026-11-12 run

Source: synthetic, no people — `ffmpeg -f lavfi -i "testsrc2=size=1280x720:rate=30,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p" -f lavfi -i "sine=frequency=440:sample_rate=48000,aformat=channel_layouts=stereo" -t 12 -c:v libx264 -colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv -g 30 -c:a aac -shortest studio-demo.mp4`
(probe facts = [`tests/fixtures/reverse/source.json`](../../../services/video-postproduction-api/tests/fixtures/reverse/source.json)).
The job files written by [`engine_render.py`](evidence/engine_render.py) hash to the goldens in `davinci-resolve.md`; the engine argv is in [`engine-argv.txt`](evidence/engine-argv.txt).
Evidence: [follow log](evidence/follow-log.txt) · [MLT project](evidence/manual.mlt) · [measurements](evidence/compare.json) ([script](evidence/compare.py)) ·
[OTIO import](evidence/otio-import.json) ([script](evidence/otio_import.py)) ·
[first/last frame of every clip, engine top / MLT bottom](evidence/clip-edges-engine-top-mlt-bottom.png) ·
[caption band, frame 100](evidence/caption-band-frame100-engine-top-mlt-bottom.png).

| Check | Engine | Followed in MLT | Result |
|---|---|---|---|
| Clips (source frames) after Razor + Ripple Delete | 8–101, 158–215, 266–350 | 8–101, 158–215, 266–350 | exact |
| Video frames / duration | 234 / 7.80 s | 234 / 7.81 s (container) | exact frames |
| Pixel aspect | 1:1 (after fix 1) | 1:1 | exact |
| Y PSNR above the caption band, aligned frames | — | min 33.94 dB, mean 34.75 dB | colour/scaler difference only |
| Same, MLT frame n vs engine frame n±1 | — | mean 24.76 / 24.69 dB; aligned wins on **every** frame | no off-by-one at any cut |
| Yellow/blue edge column and flat luma (row 200, frame 50) | x = 539–541; Y 222 / 29 | x = 539–541; Y 222 / 29 | exact |
| Caption on-screen frames (cue 1 / 2 / 3) | 4–87 / 93–144 / 154–228 | 4–87 / 93–144 / 154–228 | exact, = `captions.srt` |
| Integrated loudness (EBU R128) | −14.0 LUFS | −14.0 LUFS | exact |
| `timeline.otio` / `timeline.edl` / `timeline.fcpxml` in OTIO | — | 3 clips each, source and record frames = guide | exact |

Tolerance: picture PSNR ≥ 33.9 dB on every frame (different scaler and the
0.03 % zoom rounding: Resolve/MLT zoom 3.160 vs FFmpeg's 404 px even-width crop).

### Findings fixed

1. **Non-square pixels in the engine render.** The engine's 9:16 crop
   (`crop` to an even 404 px, then `scale=1080:1920`) left the output tagged
   SAR 404:405, so players show it 0.25 % narrower than the NLE export (SAR
   1:1). The renderer and `TimelinePlan.reframe.filters` now end the chain with
   `setsar=1` (test `tests/test_multiclip_render.py::test_vertical_crop_output_has_square_pixels`).
2. **Untagged sources change colour in an NLE.** A first run with an untagged
   BT.601 source gave 28.4 dB because MLT converts it as BT.709 while FFmpeg
   passes YUV through. This is the source, not the guide: the example source is
   now tagged BT.709, like phone and screen recordings.

### Not covered by this run

- DaVinci Resolve itself (not installed): **owner smoke test** — on a machine with
  Resolve 19, follow `davinci-resolve.md` steps 1–46 with the source above, or use
  step 47 (`File ▸ Import ▸ Timeline…` → `timeline.fcpxml`), render, and run
  `evidence/compare.py` against the engine render; add a row here.
- Caption glyph size and font: the machine has no Noto Sans Thai, so both sides
  fell back to Sarabun, and MLT's `subtitle` `size` unit renders larger than
  libass at 52 px (the guide already says to match the reference height). Only
  timing, line breaks and position were compared.
- Captions after a filler cut still show the whole transcript segment text
  (cue 2 shows "เอ่อ"); word-level cues are a P2 row in the parity matrix.
- Premiere Pro and CapCut alternatives in the glossary.
