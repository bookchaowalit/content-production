"""Withholding tax and the amount-in-words line (spec 2026-11-12-documents-parity D1, D2)."""

from __future__ import annotations

import pytest
from document_production.manual import build_word_guide
from document_production.qa import qa_document
from document_production.recipe import RequestValidationError, build_document_ir
from document_production.renderers import (
    _font_name,
    amount_in_words_line,
    render_document,
)
from docx import Document
from pypdf import PdfReader
from test_recipe import sample_request

ITEM = [{"description": "บริการวางระบบ", "quantity": "1", "unit_price": "15000.00", "tax_rate": "7"}]


def _thai_font_or_skip() -> None:
    if _font_name() == "Helvetica":
        pytest.skip("no Thai-capable PDF font in this environment (set DOCUMENT_PRODUCTION_FONT)")


def test_withholding_tax_is_on_the_amount_before_vat():
    ir = build_document_ir(sample_request(line_items=ITEM, withholding_tax_rate="3"))
    assert ir.derived_totals == {
        "subtotal": "15000.00",
        "tax_total": "1050.00",
        "grand_total": "16050.00",
        "withholding_tax": "450.00",
        "net_payable": "15600.00",
    }
    assert ir.assumptions["withholding_tax_rate"] == "3"


def test_documents_without_withholding_keep_the_old_totals():
    ir = build_document_ir(sample_request(line_items=ITEM))
    assert list(ir.derived_totals) == ["subtotal", "tax_total", "grand_total"]
    assert "withholding_tax_rate" not in ir.assumptions


@pytest.mark.parametrize("rate", ["0", "-1", "20"])
def test_withholding_rate_outside_thai_rates_is_rejected(rate):
    with pytest.raises(RequestValidationError, match="withholding_tax_rate_out_of_range"):
        build_document_ir(sample_request(line_items=ITEM, withholding_tax_rate=rate))


def test_amount_in_words_only_for_thai_baht_documents():
    thai = build_document_ir(sample_request(line_items=ITEM))
    assert amount_in_words_line(thai) == "grand_total (ตัวอักษร): (หนึ่งหมื่นหกพันห้าสิบบาทถ้วน)"
    assert amount_in_words_line(build_document_ir(sample_request(line_items=ITEM, currency="USD"))) is None
    assert amount_in_words_line(build_document_ir(sample_request(line_items=ITEM, locale="en-US"))) is None


def test_docx_and_pdf_carry_withholding_and_amount_in_words(tmp_path):
    _thai_font_or_skip()
    ir = build_document_ir(sample_request(line_items=ITEM, withholding_tax_rate="3"))
    artifacts = render_document(ir, tmp_path)
    words = "grand_total (ตัวอักษร): (หนึ่งหมื่นหกพันห้าสิบบาทถ้วน)"
    docx_text = "\n".join(paragraph.text for paragraph in Document(str(tmp_path / "document.docx")).paragraphs)
    pdf_text = " ".join(page.extract_text() for page in PdfReader(str(tmp_path / "document.pdf")).pages)
    for text in (docx_text, pdf_text):
        assert words in text
        assert "withholding_tax: 450.00 THB" in text and "net_payable: 15600.00 THB" in text
    assert qa_document(ir, artifacts, tmp_path).status == "passed"


def test_qa_requires_the_amount_in_words_line(tmp_path):
    _thai_font_or_skip()
    ir = build_document_ir(sample_request(line_items=ITEM))
    artifacts = render_document(ir, tmp_path)
    # Same IR but a different grand total: the words in the files no longer match.
    tampered = ir.model_copy(update={"derived_totals": {**ir.derived_totals, "grand_total": "16051.00"}})
    issues = qa_document(tampered, artifacts, tmp_path).issues
    assert any(issue["code"] == "document_text_missing" and "ตัวอักษร" in issue["message"] for issue in issues)


def test_qa_flags_a_net_payable_that_does_not_add_up(tmp_path):
    _thai_font_or_skip()
    ir = build_document_ir(sample_request(line_items=ITEM, withholding_tax_rate="3"))
    artifacts = render_document(ir, tmp_path)
    tampered = ir.model_copy(update={"derived_totals": {**ir.derived_totals, "net_payable": "16050.00"}})
    issues = qa_document(tampered, artifacts, tmp_path).issues
    assert any(issue["code"] == "net_payable_mismatch" for issue in issues)


def test_word_guide_has_the_withholding_and_words_steps():
    ir = build_document_ir(sample_request(line_items=ITEM, withholding_tax_rate="3"))
    values = [step.value for step in build_word_guide(ir).steps]
    assert "withholding_tax: 450.00 THB" in values
    assert "net_payable: 15600.00 THB" in values
    assert "grand_total (ตัวอักษร): (หนึ่งหมื่นหกพันห้าสิบบาทถ้วน)" in values
