"""Reverse guide drift test: docs/reverse/video-studio/davinci-resolve.md must match the engine.

Built from the committed fixture (``tests/fixtures/reverse/``) without FFmpeg:
base EDL → silence cut → timeline plan → ``build_resolve_guide``. Goldens are
the pure-Python outputs (EDL, interchange timelines, editor and burned
captions), so they are identical on a bare CI runner. Rewrite with
``REVERSE_GUIDES_UPDATE=1``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from production_trace.reverse import Golden, assert_reverse_guide, render_reverse_guide

from video_postproduction.captions.ass import render_ass_cues
from video_postproduction.captions.cues import render_editor_srt
from video_postproduction.edl import EDLDocument, validate_edl
from video_postproduction.flow import FLOW
from video_postproduction.interchange import write_interchange
from video_postproduction.jobtrace import editor_cues
from video_postproduction.manual import GuideContext, GuideFiles, build_resolve_guide
from video_postproduction.silence import silence_cut
from video_postproduction.timeline import SourceVideo, TimelinePlan, build_timeline_plan
from video_postproduction.transcription.models import NormalizedTranscript

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = "services/video-postproduction-api/tests/fixtures/reverse"
TARGET = ROOT / "docs" / "reverse" / "video-studio" / "davinci-resolve.md"
TITLE = "Video Studio demo"


def build_example(root: Path = ROOT) -> tuple[EDLDocument, TimelinePlan, dict[str, str], Any]:
    """Return the cut EDL, its plan, the deterministic job files, and the guide."""

    fixture = root / FIXTURE
    base = json.loads((fixture / "base-edl.json").read_text(encoding="utf-8"))
    options = base.pop("silence_cut")
    transcript = NormalizedTranscript.model_validate_json(
        (fixture / "transcript.json").read_text(encoding="utf-8")
    )
    source_info = json.loads((fixture / "source.json").read_text(encoding="utf-8"))
    edl = silence_cut(validate_edl(base), transcript, **options)
    source = SourceVideo.from_metadata(source_info["filename"], source_info["metadata"])
    plan = build_timeline_plan(edl, source, title=TITLE)
    cues = editor_cues(plan, transcript) or []
    files = {
        "edl.json": json.dumps(
            edl.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n",
        **write_interchange(plan),
        "captions.srt": render_editor_srt(cues),
        "captions-burned.ass": render_ass_cues(
            cues, edl.timeline[0].captions.preset, output_size=(edl.output.width, edl.output.height)
        ),
    }
    guide = build_resolve_guide(
        GuideContext(
            plan=plan,
            files=GuideFiles(
                video="render.mp4",
                burned_captions="captions-burned.ass",
                editor_captions="captions.srt",
            ),
            cues=cues,
        )
    )
    return edl, plan, files, guide


def build_text() -> str:
    _edl, _plan, files, guide = build_example()
    notes = {
        "edl.json": "EDL หลังตัดช่วงเงียบ (job IR)",
        "timeline.edl": "CMX3600",
        "timeline.fcpxml": "FCPXML 1.10",
        "timeline.otio": "OpenTimelineIO",
        "captions.srt": "ซับสำหรับโปรแกรมตัดต่อ",
        "captions-burned.ass": "ซับที่ engine เผาลงภาพ",
    }
    goldens = [
        Golden(name, hashlib.sha256(content.encode("utf-8")).hexdigest(), notes[name])
        for name, content in files.items()
    ]
    return render_reverse_guide(
        tool_id="video-studio",
        benchmark="DaVinci Resolve",
        example=FIXTURE,
        regenerate="cd services/video-postproduction-api && REVERSE_GUIDES_UPDATE=1 python -m pytest tests/test_reverse_guide.py",
        flow=FLOW,
        guide=guide,
        goldens=goldens,
    )


def test_davinci_resolve_reverse_guide_matches_engine() -> None:
    assert_reverse_guide(TARGET, build_text())


def test_example_is_a_multi_clip_silence_cut() -> None:
    edl, plan, _files, _guide = build_example()
    assert [segment.provenance["reason"] for segment in edl.timeline] == ["silence_cut"] * 3
    assert [(clip.source_in_frame, clip.source_out_frame) for clip in plan.clips] == [
        (8, 101),
        (158, 215),
        (266, 350),
    ]


def test_flow_keeps_ai_parts_behind_logic() -> None:
    labels = [part.label for part in FLOW]
    assert labels.count("AI-ASSIST") == 2 and labels.count("HUMAN-GATE") == 2
    for index, part in enumerate(FLOW):
        if part.label == "AI-ASSIST":
            assert FLOW[index + 1].label == "LOGIC" and part.fallback
