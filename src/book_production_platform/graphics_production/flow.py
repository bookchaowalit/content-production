"""Graphics flow: which part is rules/code, where a model may propose, where a person decides.

Used by the reverse guide (``docs/reverse/graphics-v1a/``) and the parity
matrix ``Flow`` column. Rules: ``production_trace.reverse.validate_flow``.
"""

from __future__ import annotations

from book_production_platform.production_trace.reverse import FlowPart, validate_flow

FLOW: tuple[FlowPart, ...] = (
    FlowPart(
        "Brief → batch proposal (template, size, slot text)",
        "AI-ASSIST",
        engine="graphics_production.provider:ProviderBriefAdapter",
        trace_stage="brief",
        manual_note="อ่าน brief แล้วเลือก template/ขนาดเอง และคัดข้อความจาก brief ทีละช่อง (ห้ามแต่งเพิ่ม)",
        fallback="เขียน batch JSON เอง หรือ `book-production graphics make` จากข้อความบรรทัดคำสั่ง",
    ),
    FlowPart(
        "Ground the proposal: text must come from the brief, no overflow, known template",
        "LOGIC",
        engine="graphics_production.provider:ground_proposal",
        trace_stage="brief",
        manual_note="เทียบข้อความทุกช่องกับ brief; ช่องที่ยาวเกินให้ตัดหรือเปลี่ยน template",
    ),
    FlowPart(
        "Plain-language design edit (optional)",
        "AI-ASSIST",
        engine="graphics_production.design_edit:apply_change",
        trace_stage="recipe",
        manual_note="ปรับ palette / layer / item override เองจากรายการ `graphics controls`",
        fallback="ใส่ `design` overrides ใน batch JSON เอง (ไม่เรียก AI)",
    ),
    FlowPart(
        "Validate the request and compile the template to GraphicIR",
        "LOGIC",
        engine="graphics_production.templates:compile_template",
        trace_stage="recipe",
        manual_note="ใช้ค่าตำแหน่ง/ขนาด/สีจากตารางโครงสร้างด้านล่างทุกค่า",
    ),
    FlowPart(
        "Thai-safe line breaking and shrink-to-fit text",
        "LOGIC",
        engine="graphics_production.layout:fit_text",
        trace_stage="recipe",
        manual_note="พิมพ์ข้อความตามบรรทัดที่ให้ทีละบรรทัด อย่าให้โปรแกรมตัดบรรทัดเอง",
    ),
    FlowPart(
        "Render editable SVG with named layers, rasterise PNG",
        "LOGIC",
        engine="graphics_production.render:render_svg",
        trace_stage="render",
        manual_note="สร้าง Frame และ layer ตามลำดับในขั้นตอนทีละคลิก แล้ว export PNG 1x",
    ),
    FlowPart(
        "QA: overflow, overlap, safe zone, WCAG contrast advisory",
        "LOGIC",
        engine="graphics_production.qa:run_graphic_qa",
        trace_stage="qa",
        manual_note="ทำตาม checklist 'ตรวจผล' ท้ายคู่มือ",
    ),
    FlowPart(
        "Review: pick / reject options, feedback teaches the composer",
        "HUMAN-GATE",
        engine="graphics_production.review:build_review_page",
        trace_stage="review",
        manual_note="ลูกค้าหรือเจ้าของงานอนุมัติข้อความและแบบที่เลือก",
    ),
    FlowPart(
        "Publish manifest, trace, MANUAL-STEPS; replay by idempotency key",
        "LOGIC",
        engine="graphics_production.jobs:run_batch",
        trace_stage="publish",
        manual_note="ตั้งชื่อ Frame ตาม request_id แล้วเก็บไฟล์ไว้คู่กับ batch",
    ),
    FlowPart(
        "Print export: PNG @Nx, raster or press PDF (bleed, crop marks, CMYK)",
        "LOGIC",
        engine="graphics_production.export:export_batch",
        trace_stage="render",
        manual_note="Export PNG ตาม scale แล้วตั้ง DPI ใน Photoshop (คู่มือ export แยก)",
    ),
)

validate_flow(FLOW)

__all__ = ["FLOW"]
