"""Web V1A flow: which part is rules/code, where a model may propose, where a person decides.

Used by the reverse guide (``docs/reverse/web-v1a/``) and the parity matrix
``Flow`` column (``docs/parity/web.md``). Rules: ``production_trace.reverse.validate_flow``.
The only model call is the V1B brief adapter, which goes through
``brief_adapter_kit`` (``GatewayProposalRunner`` + ``GatewayTransport``) to the AI gateway.
"""

from __future__ import annotations

from production_trace.reverse import FlowPart, validate_flow

FLOW: tuple[FlowPart, ...] = (
    FlowPart(
        "Brief → PageRequest proposal (registered sections, plain copy only)",
        "AI-ASSIST",
        engine="web_production.provider:ProviderPageAdapter",
        trace_stage="brief",
        manual_note="อ่าน brief แล้วคัดชื่อ/หัวข้อ/ราคา/คำถามจาก brief ลงช่องของ page JSON เอง (ห้ามแต่งตัวเลข ราคา หรือคำโฆษณาเพิ่ม)",
        fallback="เขียน page JSON เอง แล้วใช้ `python -m web_production.cli build` (ไม่เรียก AI)",
    ),
    FlowPart(
        "Ground the proposal: names, features, prices verbatim in the brief; no markup/URL; else needs_input",
        "LOGIC",
        engine="web_production.provider:ground_page_proposal",
        trace_stage="brief",
        manual_note="เทียบทุกค่าที่พิมพ์กับ brief; ค่าที่ brief ไม่ได้บอกให้ถามลูกค้า",
    ),
    FlowPart(
        "Switch the adapter from shadow to live after a passing shadow report",
        "HUMAN-GATE",
        engine="web_production.provider_config:parse_provider_config",
        trace_stage="brief",
        manual_note="เจ้าของงานเท่านั้นที่ตั้ง mode=live หลังอ่านรายงาน brief-shadow",
    ),
    FlowPart(
        "Validate the request (links, form action, required text)",
        "LOGIC",
        engine="web_ir.validation:validate_request",
        trace_stage="validate",
        manual_note="ลิงก์และ action ของฟอร์มต้องเป็น path แบบ relative / fragment / mailto ตามที่คู่มือให้เท่านั้น",
    ),
    FlowPart(
        "Compile the fixed landing-v1 template to PageIR",
        "LOGIC",
        engine="web_ir.validation:compile_page",
        trace_stage="recipe",
        manual_note="สร้าง element ตามตารางโครงสร้างในข้อ 2 ตามลำดับ",
    ),
    FlowPart(
        "Verify the hero image hash and ship it inside the bundle",
        "LOGIC",
        engine="web_production.jobs:_hero_image_bytes",
        trace_stage="assets",
        manual_note="อัปโหลดไฟล์รูปเดียวกับที่เครื่องมือใช้ (sha256 ตรงกับตาราง golden) และใส่ alt",
    ),
    FlowPart(
        "Render escaped HTML and responsive CSS (breakpoints 991/767 px, Thai line height)",
        "LOGIC",
        engine="web_production.render:render_html",
        trace_stage="render",
        manual_note="ตั้งสไตล์ทุกค่าตามขั้นตอนทีละคลิก รวม breakpoint Tablet และ Mobile landscape",
    ),
    FlowPart(
        "QA: landmarks, one H1, alt, safe links, SEO/OG meta, breakpoints, WCAG AA contrast, Thai line height, form labels",
        "LOGIC",
        engine="web_production.qa:run_page_qa",
        trace_stage="qa",
        manual_note="ทำตาม checklist 'ตรวจผล' ท้ายคู่มือ และ Audit panel ของ Webflow",
    ),
    FlowPart(
        "Write bundle, manifest, trace, MANUAL-STEPS; replay by idempotency key",
        "LOGIC",
        engine="web_production.jobs:run_page_job",
        trace_stage="publish",
        manual_note="Export code ▸ Download ZIP แล้วเก็บคู่กับ page JSON",
    ),
    FlowPart(
        "Approve the copy, host the static files, answer the form action",
        "HUMAN-GATE",
        engine="— (no deploy endpoint by design)",
        trace_stage="publish",
        manual_note="ลูกค้าหรือเจ้าของงานอนุมัติข้อความ แล้วนำไฟล์ไปวางบน host เอง; host ต้องรับ POST ที่ action ของฟอร์ม",
    ),
)

validate_flow(FLOW)

__all__ = ["FLOW"]
