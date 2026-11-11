"""Documents flow: which part is rules/code, where a model may propose, where a person decides.

Used by the reverse guide (``docs/reverse/documents-v1a/``) and the parity
matrix ``Flow`` column. Rules: ``production_trace.reverse.validate_flow``.
The only model call is the optional V1B brief adapter, which goes through
``brief_adapter_kit`` → Central AI Gateway (``provider.ProviderBriefAdapter``).
"""

from __future__ import annotations

from production_trace.reverse import FlowPart, validate_flow

FLOW: tuple[FlowPart, ...] = (
    FlowPart(
        "Brief → request fields (customer, date, currency, line items; never totals)",
        "AI-ASSIST",
        engine="document_production.provider:ProviderBriefAdapter",
        trace_stage="brief",
        manual_note="อ่าน brief แล้วกรอกชื่อลูกค้า วันที่ สกุลเงิน และรายการสินค้าเอง (ห้ามคำนวณยอดเอง)",
        fallback="`RuleBasedExtractor` (brief แบบมีป้าย `item: ...`, ไม่เรียก AI) หรือเขียน request JSON เอง",
    ),
    FlowPart(
        "Ground and validate the proposal: every value must appear in the brief, totals from a model are rejected",
        "LOGIC",
        engine="document_production.extraction:validate_extraction_result",
        trace_stage="brief",
        manual_note="เทียบทุกค่ากับ brief; ค่าที่หาไม่เจอให้ถือว่ายังขาด",
    ),
    FlowPart(
        "Scan import (PDF text, DOCX, local Tesseract OCR) → field proposals with page evidence",
        "LOGIC",
        engine="document_production.scan_import:run_scan_import",
        trace_stage="scan",
        manual_note="เปิดไฟล์สแกนแล้วคัดค่าแต่ละช่องพร้อมเลขหน้าที่พบ (Acrobat: Scan & OCR)",
    ),
    FlowPart(
        "Fill fields the brief or scan left missing or ambiguous (`needs_input`)",
        "HUMAN-GATE",
        engine="document_production.routing:route_brief",
        trace_stage="brief",
        manual_note="เจ้าของงานกรอกช่องที่ขาด/กำกวมเอง แล้วส่ง request ใหม่",
    ),
    FlowPart(
        "Validate the request (currency, ≤ 100 items, tax and withholding rates)",
        "LOGIC",
        engine="document_ir.validation:validate_request",
        trace_stage="validate",
        manual_note="ตรวจว่ามีทุกช่องที่จำเป็น และอัตราภาษีอยู่ในช่วงที่อนุญาต",
    ),
    FlowPart(
        "Decimal recipe: line totals, VAT, withholding tax, net payable (half-up to satang)",
        "LOGIC",
        engine="document_production.recipe:build_document_ir",
        trace_stage="recipe",
        manual_note="คัดลอกยอดจากตารางของเครื่องมือ ห้ามคำนวณใหม่ใน Word",
    ),
    FlowPart(
        "Render DOCX and PDF: styles, Table Grid, footer page number, amount in Thai words, Thai cluster-safe PDF lines",
        "LOGIC",
        engine="document_production.renderers:render_document",
        trace_stage="render",
        manual_note="ทำตามขั้นตอนทีละคลิกด้านล่าง แล้ว Export เป็น PDF",
    ),
    FlowPart(
        "QA: hashes, page count, critical text in both files, totals arithmetic",
        "LOGIC",
        engine="document_production.qa:qa_document",
        trace_stage="qa",
        manual_note="ทำตาม checklist 'ตรวจผล' ท้ายคู่มือ",
    ),
    FlowPart(
        "Publish manifest, trace, MANUAL-STEPS; replay by idempotency key",
        "LOGIC",
        engine="document_production.jobs:run_document_job",
        trace_stage="publish",
        manual_note="บันทึก document.docx และ document.pdf ไว้คู่กับ request",
    ),
)

validate_flow(FLOW)

__all__ = ["FLOW"]
