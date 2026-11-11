from datetime import date
from decimal import Decimal

from document_ir import DocumentRequest

SAMPLE_REQUEST = {
    "template_id": "invoice",
    "template_version": "1",
    "document_id": "doc-001",
    "currency": "THB",
    "title": "ใบเสนอราคา",
    "customer_name": "ลูกค้าทดสอบ",
    "issue_date": "2026-09-22",
    "line_items": [{"description": "บริการ", "quantity": "2", "unit_price": "10.00", "tax_rate": "7"}],
}


def test_request_round_trips_decimal_and_date():
    request = DocumentRequest.model_validate(SAMPLE_REQUEST)
    assert request.line_items[0].quantity == Decimal(2)
    assert request.issue_date == date(2026, 9, 22)


def test_line_item_decimal_schema_is_pinned_independent_of_pydantic():
    from document_ir.models import DECIMAL_STRING_PATTERN, LineItemInput

    properties = LineItemInput.model_json_schema()["properties"]
    for field in ("quantity", "unit_price", "tax_rate"):
        assert properties[field]["anyOf"] == [
            {"type": "number"},
            {"type": "string", "pattern": DECIMAL_STRING_PATTERN},
        ]


def test_withholding_rate_is_optional_and_left_out_of_dumps_when_unset():
    plain = DocumentRequest.model_validate(SAMPLE_REQUEST)
    assert "withholding_tax_rate" not in plain.model_dump(mode="json")
    with_rate = DocumentRequest.model_validate({**SAMPLE_REQUEST, "withholding_tax_rate": "3"})
    assert with_rate.withholding_tax_rate == Decimal(3)
    assert with_rate.model_dump(mode="json")["withholding_tax_rate"] == "3"


def test_withholding_rate_outside_thai_rates_is_an_issue():
    from document_ir import validate_request

    for rate in ("0", "15.5"):
        codes = [issue.code for issue in validate_request({**SAMPLE_REQUEST, "withholding_tax_rate": rate})]
        assert "withholding_tax_rate_out_of_range" in codes
    assert validate_request({**SAMPLE_REQUEST, "withholding_tax_rate": "15"}) == []
