"""Silence and filler cut: keep only the spoken ranges of each EDL segment (deterministic).

Spec: ``docs/superpowers/specs/2026-11-12-video-studio-parity.md`` (V2). Speech
comes from word timings when a transcript segment has words, otherwise from the
segment itself. Filler words (a fixed Thai/English list) are not speech when
``remove_fillers`` is set. Each speech interval is padded, clipped to the base
segment's range, and intervals closer than ``min_gap_s`` are merged; every kept
interval becomes one segment that copies the base segment's settings.

No model is involved: the same EDL and transcript always give the same cut.
"""

from __future__ import annotations

import math
import unicodedata

from .edl import EDLDocument, TimelineSegment, validate_edl
from .transcription.models import NormalizedTranscript

FILLERS = frozenset(
    {"um", "umm", "uh", "uhm", "erm", "er", "hmm", "mm", "เอ่อ", "เอิ่ม", "อ่า", "อืม", "อ้า"}
)
MIN_GAP_LIMITS = (0.1, 5.0)
PADDING_LIMITS = (0.0, 1.0)


class SilenceCutError(ValueError):
    """The cut cannot be made; ``code`` is stable (``no_speech``, ``invalid_options``)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def is_filler(text: str) -> bool:
    """A word that is only a filler, ignoring case, spaces and punctuation."""

    # Strip spaces and punctuation only: Thai vowel and tone marks (category Mn) stay.
    token = "".join(
        ch for ch in unicodedata.normalize("NFC", text) if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    ).casefold()
    return token in FILLERS


def _speech(
    transcript: NormalizedTranscript, *, remove_fillers: bool
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    intervals: list[tuple[float, float]] = []
    fillers: list[tuple[float, float]] = []
    for segment in transcript.segments:
        if segment.words:
            for word in segment.words:
                if remove_fillers and is_filler(word.text):
                    fillers.append((word.start, word.end))
                    continue
                if word.text.strip():
                    intervals.append((word.start, word.end))
        elif remove_fillers and is_filler(segment.text):
            fillers.append((segment.start, segment.end))
        elif segment.text.strip():
            intervals.append((segment.start, segment.end))
    return sorted(intervals), fillers


def _keep(
    speech: list[tuple[float, float]],
    start: float,
    end: float,
    *,
    min_gap_s: float,
    padding_s: float,
) -> list[tuple[float, float]]:
    kept: list[tuple[float, float]] = []
    for word_start, word_end in speech:
        low, high = max(start, word_start - padding_s), min(end, word_end + padding_s)
        if word_end <= start or word_start >= end or high <= low:
            continue
        if kept and low - kept[-1][1] < min_gap_s:
            kept[-1] = (kept[-1][0], max(kept[-1][1], high))
        else:
            kept.append((low, high))
    return [(round(low, 3), round(high, 3)) for low, high in kept if round(high, 3) > round(low, 3)]


def _bounded(name: str, value: float, limits: tuple[float, float]) -> float:
    low, high = limits
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise SilenceCutError("invalid_options", f"{name} must be between {low:g} and {high:g}")
    return float(value)


def silence_cut(
    edl: EDLDocument,
    transcript: NormalizedTranscript,
    *,
    min_gap_s: float = 0.6,
    padding_s: float = 0.15,
    remove_fillers: bool = True,
) -> EDLDocument:
    """Return a new EDL whose segments keep only the spoken parts of each base segment."""

    min_gap_s = _bounded("min_gap_s", min_gap_s, MIN_GAP_LIMITS)
    padding_s = _bounded("padding_s", padding_s, PADDING_LIMITS)
    speech, fillers = _speech(transcript, remove_fillers=remove_fillers)
    timeline: list[TimelineSegment] = []
    for base in edl.timeline:
        ranges = _keep(
            speech, base.source.start, base.source.end, min_gap_s=min_gap_s, padding_s=padding_s
        )
        removed = sum(1 for low, high in fillers if high > base.source.start and low < base.source.end)
        for number, (start, end) in enumerate(ranges, start=1):
            timeline.append(
                base.model_copy(
                    deep=True,
                    update={
                        "id": f"{base.id}_k{number:02d}",
                        "source": base.source.model_copy(update={"start": start, "end": end}),
                        "provenance": {
                            "reason": "silence_cut",
                            "base_segment": base.id,
                            "min_gap_s": min_gap_s,
                            "padding_s": padding_s,
                            "fillers_removed": removed,
                        },
                    },
                )
            )
    if not timeline:
        raise SilenceCutError("no_speech", "the transcript has no speech inside the EDL ranges")
    document = edl.model_dump(mode="json")
    document["timeline"] = [segment.model_dump(mode="json") for segment in timeline]
    return validate_edl(document)


__all__ = ["FILLERS", "SilenceCutError", "is_filler", "silence_cut"]
