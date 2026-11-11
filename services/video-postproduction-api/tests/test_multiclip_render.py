# ruff: noqa: F811  (the studio fixture is imported from test_job_trace)
"""Multi-clip render (spec 2026-11-12-video-studio-parity V1): trim/atrim + concat on plan frames."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from video_postproduction.edl import validate_edl
from video_postproduction.jobtrace import editor_cues
from video_postproduction.render.base import RenderCapabilityError
from video_postproduction.render.ffmpeg import build_ffmpeg_argv, summarize_ffmpeg_argv
from video_postproduction.timeline import SourceVideo, build_timeline_plan
from video_postproduction.transcription.models import NormalizedTranscript

from .test_job_trace import (  # noqa: F401  (studio is a fixture)
    SOURCE_METADATA,
    FakeEngine,
    _job_dir,
    _render,
    _segment,
    _transcript_json,
    make_document,
    studio,
)

IN = Path("in.mp4")
OUT = Path("out.mp4")


def _plan(studio, segments, metadata=SOURCE_METADATA):
    edl = validate_edl(make_document(studio["project"].id, studio["asset"].id, segments))
    return edl, build_timeline_plan(edl, SourceVideo.from_metadata("interview.mp4", metadata))


def _three(asset_id):
    return [
        _segment(asset_id, 10.0, 16.25, "a"),
        _segment(asset_id, 30.01, 33.0, "b"),
        _segment(asset_id, 60.0, 64.5, "c"),
    ]


def _graph(argv: list[str]) -> str:
    return argv[argv.index("-filter_complex") + 1]


def test_multi_clip_argv_trims_each_clip_on_plan_frames_then_concats(studio):
    edl, plan = _plan(studio, _three(studio["asset"].id))
    argv = build_ffmpeg_argv(
        edl, input_path=IN, output_path=OUT, caption_path=Path("c.ass"), plan=plan
    )
    graph = _graph(argv)
    for index, clip in enumerate(plan.clips):
        assert (
            f"[0:v:0]trim=start_frame={clip.source_in_frame}:end_frame={clip.source_out_frame},setpts=PTS-STARTPTS[v{index}]"
            in graph
        )
        start, end = clip.source_in_frame / 30, clip.source_out_frame / 30
        assert (
            f"[0:a:0]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS[a{index}]" in graph
        )
    assert "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[vcat][acat]" in graph
    assert "[vcat]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920,setsar=1,subtitles=" in graph
    assert [argv[i + 1] for i, item in enumerate(argv) if item == "-map"] == ["[vout]", "[aout]"]
    assert "-ss" not in argv and "-vf" not in argv
    # 30.01 s is frame 900.3 → the plan (and the render) start clip b at frame 900.
    assert plan.clips[1].source_in_frame == 900
    summary = summarize_ffmpeg_argv(argv)
    assert summary["inputs"] == 1 and "in.mp4" not in str(summary)
    assert any(
        part.endswith(",subtitles=<captions>[vout]") for part in summary["options"]["filter_complex"]
    )
    assert "c.ass" not in str(summary)


def test_multi_clip_argv_without_source_audio_concats_video_only(studio):
    edl, plan = _plan(studio, _three(studio["asset"].id), {**SOURCE_METADATA, "audio": None})
    argv = build_ffmpeg_argv(edl, input_path=IN, output_path=OUT, plan=plan)
    graph = _graph(argv)
    assert "atrim" not in graph and "concat=n=3:v=1:a=0[vcat]" in graph
    assert [argv[i + 1] for i, item in enumerate(argv) if item == "-map"] == ["[vout]"]


def test_multi_clip_loudness_runs_after_concat(studio):
    segments = _three(studio["asset"].id)
    for segment in segments:
        segment["audio"] = {"operations": [{"type": "loudness_normalize"}]}
    edl, plan = _plan(studio, segments)
    argv = build_ffmpeg_argv(edl, input_path=IN, output_path=OUT, plan=plan)
    assert "[acat]loudnorm=I=-14:TP=-1:LRA=11[aout]" in _graph(argv)
    assert argv[argv.index("-ar") + 1] == "48000"


def test_multi_clip_needs_a_plan_and_matching_visuals(studio):
    asset_id = studio["asset"].id
    edl, plan = _plan(studio, _three(asset_id))
    with pytest.raises(RenderCapabilityError, match="timeline plan"):
        build_ffmpeg_argv(edl, input_path=IN, output_path=OUT)
    mixed = _three(asset_id)
    mixed[1]["visual"] = {"kind": "a_roll", "reframe": None, "asset_refs": []}
    edl, plan = _plan(studio, mixed)
    with pytest.raises(RenderCapabilityError, match="same visual"):
        build_ffmpeg_argv(edl, input_path=IN, output_path=OUT, plan=plan)
    coloured = _three(asset_id)
    coloured[2]["color"] = {"operations": [{"type": "lut"}]}
    edl, plan = _plan(studio, coloured)
    with pytest.raises(RenderCapabilityError, match="color"):
        build_ffmpeg_argv(edl, input_path=IN, output_path=OUT, plan=plan)


def test_single_clip_argv_is_unchanged_by_the_plan(studio):
    edl, plan = _plan(studio, [_segment(studio["asset"].id, 12.5, 42.25)])
    assert build_ffmpeg_argv(edl, input_path=IN, output_path=OUT, plan=plan) == build_ffmpeg_argv(
        edl, input_path=IN, output_path=OUT
    )


def test_multi_clip_cues_are_frame_exact_on_the_record_timeline(studio):
    _edl, plan = _plan(studio, _three(studio["asset"].id))
    cues = editor_cues(plan, NormalizedTranscript.model_validate(_transcript_json()))
    assert cues is not None
    # s1 (10.0–16.0) sits in clip a from record 0; s3 (60–64) in clip c from record frame 278.
    first = next(cue for cue in cues if cue.clip_index == 1)
    assert first.start == pytest.approx(0.0) and first.end == pytest.approx(6.0)
    third = next(cue for cue in cues if cue.clip_index == 3)
    record_c = plan.clips[2].record_in_frame / 30
    assert third.start == pytest.approx(record_c) and third.end == pytest.approx(record_c + 4.0)


def test_render_service_renders_multi_clip_with_burned_cues_at_record_positions(studio):
    engine = FakeEngine(duration=13.73)
    output = _render(studio, _three(studio["asset"].id), engine)
    assert output.status == "succeeded"
    assert engine.caption_text is not None
    events = [line for line in engine.caption_text.splitlines() if line.startswith("Dialogue:")]
    starts = [line.split(",")[1] for line in events]
    # clip a: s1 at 0:00, s2 16.0–16.25 at 6.00; clip c: s3 at frame 278 = 9.27 s.
    assert starts == ["0:00:00.00", "0:00:06.00", "0:00:09.27"]
    job_dir = _job_dir(studio["storage"], output)
    srt = (job_dir / "captions.srt").read_text(encoding="utf-8")
    assert "00:00:09,267 --> 00:00:13,267" in srt


def _ffmpeg() -> str | None:
    return os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg")


def test_real_ffmpeg_multi_clip_render_has_plan_frame_count(studio, tmp_path):
    ffmpeg = _ffmpeg()
    if ffmpeg is None:
        pytest.skip("FFmpeg is required for the real multi-clip render check")
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "6",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    metadata = {
        **SOURCE_METADATA,
        "duration": 6.0,
        "video": {**SOURCE_METADATA["video"], "width": 320, "height": 180},
    }
    asset_id = studio["asset"].id
    segments = [
        _segment(asset_id, 0.5, 1.5, "a"),
        _segment(asset_id, 2.01, 3.0, "b"),
        _segment(asset_id, 4.0, 5.5, "c"),
    ]
    for segment in segments:
        segment["captions"]["enabled"] = False
    document = make_document(studio["project"].id, asset_id, segments, width=180, height=320)
    document["source_assets"][0]["duration"] = 6.0
    edl = validate_edl(document)
    plan = build_timeline_plan(edl, SourceVideo.from_metadata("source.mp4", metadata))
    output = tmp_path / "out.mp4"
    argv = build_ffmpeg_argv(
        edl, input_path=source, output_path=output, executable=ffmpeg, plan=plan
    )
    subprocess.run(argv, check=True, capture_output=True, timeout=120)
    hashes = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(output),
            "-map",
            "0:v",
            "-f",
            "framemd5",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout
    frames = [line for line in hashes.splitlines() if line and not line.startswith("#")]
    assert len(frames) == plan.record_frames == 30 + 30 + 45
    info = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(output)], check=False, capture_output=True, text=True
    ).stderr
    assert re.search(r"Audio: aac", info)


def test_vertical_crop_output_has_square_pixels(studio):
    """Found by following the reverse guide in MLT: crop 404 px → scale 1080 left SAR 404:405."""

    edl, plan = _plan(studio, [_segment(studio["asset"].id, 1.0, 5.0)])
    argv = build_ffmpeg_argv(edl, input_path=IN, output_path=OUT, plan=plan)
    assert argv[argv.index("-vf") + 1].split(",")[:3] == ["crop=ih*9/16:ih:(iw-ih*9/16)/2:0", "scale=1080:1920", "setsar=1"]
    assert plan.reframe.filters == ("crop=ih*9/16:ih:(iw-ih*9/16)/2:0", "scale=1080:1920", "setsar=1")
