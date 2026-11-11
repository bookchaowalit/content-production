"""Silence and filler cut (spec 2026-11-12-video-studio-parity V2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from video_postproduction.edl import validate_edl
from video_postproduction.silence import SilenceCutError, silence_cut
from video_postproduction.transcription.models import NormalizedTranscript

PROJECT = UUID("00000000-0000-0000-0000-000000000001")
ASSET = UUID("00000000-0000-0000-0000-000000000002")


def _edl(start: float = 0.0, end: float = 20.0) -> Any:
    return validate_edl(
        {
            "version": 1,
            "project_id": str(PROJECT),
            "source_assets": [
                {"asset_id": str(ASSET), "storage_key": "source.mp4", "duration": 20.0}
            ],
            "timeline": [
                {
                    "id": "segment_0001",
                    "source": {"asset_id": str(ASSET), "start": start, "end": end},
                    "visual": {
                        "kind": "a_roll",
                        "reframe": {"mode": "center", "aspect_ratio": "9:16"},
                        "asset_refs": [],
                    },
                    "captions": {
                        "enabled": True,
                        "source": "transcript",
                        "format": "ass",
                        "preset": "bold",
                    },
                    "audio": {"operations": [{"type": "loudness_normalize"}]},
                    "color": {"operations": []},
                    "provenance": {"reason": "initial source range"},
                }
            ],
            "assets": [],
            "output": {"width": 1080, "height": 1920, "fps": 30, "format": "mp4"},
            "capabilities": {"required": ["cut", "captions", "center_reframe"]},
        }
    )


def _transcript(segments: list[dict[str, Any]]) -> NormalizedTranscript:
    return NormalizedTranscript.model_validate(
        {
            "provider": "deterministic",
            "model": "test",
            "language": "en",
            "duration": 20.0,
            "segments": segments,
        }
    )


def _ranges(edl: Any) -> list[tuple[float, float]]:
    return [(segment.source.start, segment.source.end) for segment in edl.timeline]


def test_long_gaps_become_cuts_and_short_gaps_stay():
    transcript = _transcript(
        [
            {"id": "s1", "start": 1.0, "end": 3.0, "text": "one"},
            {"id": "s2", "start": 3.4, "end": 5.0, "text": "two"},  # 0.4 s gap: kept
            {"id": "s3", "start": 8.0, "end": 9.5, "text": "three"},  # 3 s gap: cut
        ]
    )
    result = silence_cut(_edl(), transcript, min_gap_s=0.6, padding_s=0.15)
    assert _ranges(result) == [(0.85, 5.15), (7.85, 9.65)]
    first = result.timeline[0]
    assert first.id == "segment_0001_k01" and result.timeline[1].id == "segment_0001_k02"
    assert first.captions.preset == "bold" and first.audio.operations == [
        {"type": "loudness_normalize"}
    ]
    assert first.visual.reframe is not None and first.visual.reframe.aspect_ratio == "9:16"
    assert first.provenance == {
        "reason": "silence_cut",
        "base_segment": "segment_0001",
        "min_gap_s": 0.6,
        "padding_s": 0.15,
        "fillers_removed": 0,
    }
    assert validate_edl(result.model_dump(mode="json")) == result


def _words(*items: tuple[str, float, float]) -> list[dict[str, Any]]:
    return [{"text": text, "start": start, "end": end} for text, start, end in items]


def test_filler_words_are_cut_when_they_leave_a_long_enough_gap():
    words = _words(
        ("So", 1.0, 1.4),
        ("um,", 1.5, 2.6),
        ("pricing", 2.7, 3.5),
        ("เอ่อ", 3.6, 4.6),
        ("ok", 4.7, 5.0),
    )
    transcript = _transcript(
        [{"id": "s1", "start": 1.0, "end": 5.0, "text": "So um, pricing เอ่อ ok", "words": words}]
    )
    result = silence_cut(_edl(), transcript, min_gap_s=0.6, padding_s=0.1)
    assert _ranges(result) == [(0.9, 1.5), (2.6, 3.6), (4.6, 5.1)]
    assert result.timeline[0].provenance["fillers_removed"] == 2
    kept = silence_cut(_edl(), transcript, min_gap_s=0.6, padding_s=0.1, remove_fillers=False)
    assert _ranges(kept) == [(0.9, 5.1)]


def test_cut_stays_inside_the_base_segment_range():
    transcript = _transcript(
        [
            {"id": "s1", "start": 0.0, "end": 2.0, "text": "a"},
            {"id": "s2", "start": 9.0, "end": 12.0, "text": "b"},
        ]
    )
    result = silence_cut(_edl(1.0, 10.0), transcript, min_gap_s=0.6, padding_s=0.5)
    assert _ranges(result) == [(1.0, 2.5), (8.5, 10.0)]


def test_silence_cut_is_deterministic():
    transcript = _transcript(
        [
            {"id": "s1", "start": 1.0, "end": 3.0, "text": "a"},
            {"id": "s2", "start": 6.0, "end": 7.0, "text": "b"},
        ]
    )
    assert silence_cut(_edl(), transcript).model_dump(mode="json") == silence_cut(
        _edl(), transcript
    ).model_dump(mode="json")


def test_no_speech_and_bad_options_are_refused():
    with pytest.raises(SilenceCutError) as error:
        silence_cut(_edl(0.0, 10.0), _transcript([{"id": "s1", "start": 15.0, "end": 16.0, "text": "a"}]))
    assert error.value.code == "no_speech"
    only_fillers = _transcript(
        [{"id": "s1", "start": 1.0, "end": 2.0, "text": "um", "words": _words(("um", 1.0, 2.0))}]
    )
    with pytest.raises(SilenceCutError, match="no_speech"):
        silence_cut(_edl(), only_fillers)
    for options in ({"min_gap_s": 0.05}, {"min_gap_s": 6}, {"padding_s": -0.1}, {"padding_s": 1.5}):
        with pytest.raises(SilenceCutError) as error:
            silence_cut(
                _edl(),
                _transcript([{"id": "s1", "start": 1.0, "end": 2.0, "text": "a"}]),
                **options,
            )
        assert error.value.code == "invalid_options"
