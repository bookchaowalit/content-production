"""Thai amount in words and cluster-safe line breaking (spec 2026-11-12-documents-parity D1, D3)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from document_production.recipe import build_document_ir
from document_production.renderers import _font_name, render_document
from document_production.thai_text import (
    THAI_COMBINING,
    THAI_FOLLOWING,
    THAI_LEADING,
    baht_text,
    wrap_lines,
)
from pypdf import PdfReader
from test_recipe import sample_request


@pytest.mark.parametrize(
    ("amount", "words"),
    [
        ("0", "ศูนย์บาทถ้วน"),
        ("1", "หนึ่งบาทถ้วน"),
        ("11", "สิบเอ็ดบาทถ้วน"),
        ("21", "ยี่สิบเอ็ดบาทถ้วน"),
        ("101", "หนึ่งร้อยเอ็ดบาทถ้วน"),
        ("1000000", "หนึ่งล้านบาทถ้วน"),
        ("11000000", "สิบเอ็ดล้านบาทถ้วน"),
        ("16050.00", "หนึ่งหมื่นหกพันห้าสิบบาทถ้วน"),
        ("15600", "หนึ่งหมื่นห้าพันหกร้อยบาทถ้วน"),
        ("0.50", "ห้าสิบสตางค์"),
        ("0.01", "หนึ่งสตางค์"),
        ("0.21", "ยี่สิบเอ็ดสตางค์"),
        ("1.005", "หนึ่งบาทหนึ่งสตางค์"),
        ("1234567.89", "หนึ่งล้านสองแสนสามหมื่นสี่พันห้าร้อยหกสิบเจ็ดบาทแปดสิบเก้าสตางค์"),
        ("1000000000000", "หนึ่งล้านล้านบาทถ้วน"),
    ],
)
def test_baht_text_reads_thai_amounts(amount, words):
    assert baht_text(Decimal(amount)) == words


def test_baht_text_rejects_negative_amounts():
    with pytest.raises(ValueError, match="negative"):
        baht_text(Decimal(-1))


def _width(text: str) -> float:
    # Combining marks take no horizontal space, like in a real font.
    return float(sum(0 if char in THAI_COMBINING else 1 for char in text))


def _assert_cluster_safe(lines: list[str]) -> None:
    for line in lines:
        assert line, lines
        assert line[0] not in THAI_COMBINING | THAI_FOLLOWING, lines
        assert line[-1] not in THAI_LEADING, lines


def test_wrap_lines_keeps_short_text_on_one_line():
    assert wrap_lines("ขอบคุณที่ใช้บริการ", 100, _width) == ["ขอบคุณที่ใช้บริการ"]
    assert wrap_lines("Plain English text", 100, _width) == ["Plain English text"]


def test_wrap_lines_breaks_space_free_thai_between_clusters_only():
    text = "ขอบคุณที่ใช้บริการ" * 6
    for width in (5, 7, 11, 13, 17):
        lines = wrap_lines(text, width, _width)
        assert len(lines) > 1
        assert "".join(lines) == text
        assert all(_width(line) <= width for line in lines), (width, lines)
        _assert_cluster_safe(lines)


def test_wrap_lines_prefers_spaces_and_drops_the_break_space():
    lines = wrap_lines("บริการ วางระบบ เดือนกันยายน", 8, _width)
    assert lines[:2] == ["บริการ", "วางระบบ"]
    assert "".join(lines[2:]) == "เดือนกันยายน"
    assert all(not line.startswith(" ") and not line.endswith(" ") for line in lines)
    _assert_cluster_safe(lines)


def test_pdf_breaks_a_long_thai_note_between_clusters(tmp_path):
    if _font_name() == "Helvetica":
        pytest.skip("no Thai-capable PDF font in this environment (set DOCUMENT_PRODUCTION_FONT)")
    note = "ขอบคุณที่ใช้บริการ" * 12
    ir = build_document_ir(sample_request(notes=note, title="ใบแจ้งหนี้"))
    render_document(ir, tmp_path)
    text = PdfReader(str(tmp_path / "document.pdf")).pages[0].extract_text()
    note_lines = [line for line in text.splitlines() if line and set(line) <= set(note)]
    assert len(note_lines) >= 2
    assert "".join(note_lines) == note
    _assert_cluster_safe(note_lines)
