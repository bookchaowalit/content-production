"""Marketing flow: which part is rules/code, where a model may propose, where a person decides.

Used by the reverse guide (``docs/reverse/marketing-v1a/``) and the parity
matrix ``Flow`` column. Rules: ``production_trace.reverse.validate_flow``. The
only model call is the V1B brief adapter, which goes through
``brief_adapter_kit`` (``GatewayTransport`` → AI gateway); the build path makes
zero model calls.
"""

from __future__ import annotations

from production_trace.reverse import FlowPart, validate_flow

FLOW: tuple[FlowPart, ...] = (
    FlowPart(
        "Brief → per-channel copy with claims and evidence quotes",
        "AI-ASSIST",
        engine="marketing_production.provider:ProviderCopyAdapter",
        trace_stage="ai",
        manual_note="อ่าน brief แล้วเขียนข้อความต่อช่องทางเอง ใช้เฉพาะตัวเลข/ลิงก์/คำอ้างที่มีใน brief",
        fallback="เขียน `assets` ใน campaign JSON เอง แล้วใช้ `build` (ไม่เรียก AI)",
    ),
    FlowPart(
        "Ground the proposal: numbers, links and claim markers must appear in the brief; length and forbidden words",
        "LOGIC",
        engine="marketing_production.provider:ground_copy_proposal",
        trace_stage="ai",
        manual_note="เทียบทุกตัวเลข ลิงก์ และคำอ้าง (ที่สุด, อันดับ 1, รางวัล …) กับ brief; ไม่มีใน brief = ตัดออก",
    ),
    FlowPart(
        "Validate the request and compile CampaignIR (channels, assets, email fields, dates, timezone, UTM)",
        "LOGIC",
        engine="marketing_ir.validation:compile_campaign",
        trace_stage="validate",
        manual_note="ทุกช่องทางต้องมีชิ้นงาน; subject/preheader ≤ 150 ตัวอักษร; วันที่ต้องมีจริง",
    ),
    FlowPart(
        "Channel rules: platform and engine copy limits, forbidden words, claims, markup, hashtags and mentions",
        "LOGIC",
        engine="marketing_production.channel_tools:asset_checks",
        trace_stage="rules",
        manual_note="นับตัวอักษรตามตาราง 'ตรวจผล'; ข้อสังเกตระดับ review ส่งให้ผู้อนุมัติ",
    ),
    FlowPart(
        "UTM tracked links, A/B groups with sample-size and duration plan",
        "LOGIC",
        engine="marketing_production.channel_tools:tracked_links",
        trace_stage="links",
        manual_note="คัดลอกลิงก์และค่าแผน A/B จากขั้นตอนด้านล่างตามตัวอักษร",
    ),
    FlowPart(
        "Write the package (calendar, checks) and the bulk CSV export",
        "LOGIC",
        engine="marketing_production.channel_tools:bulk_csv",
        trace_stage="render",
        manual_note="สร้างแบบร่างในแต่ละโปรแกรม และกรอกตาราง bulk ใน LibreOffice Calc ทีละเซลล์",
    ),
    FlowPart(
        "Package QA: reopen, schema, publish/spend disabled, zero model calls",
        "LOGIC",
        engine="marketing_production.qa:run_package_qa",
        trace_stage="qa",
        manual_note="ทำตาม checklist 'ตรวจผล' ท้ายคู่มือ",
    ),
    FlowPart(
        "Publish manifest, trace, MANUAL-STEPS; replay by idempotency key",
        "LOGIC",
        engine="marketing_production.jobs:run_campaign_job",
        trace_stage="publish",
        manual_note="เก็บไฟล์ CSV และแบบร่างคู่กับ campaign_id",
    ),
    FlowPart(
        "Approval: approve / request changes / reject, bound to the package sha256",
        "HUMAN-GATE",
        engine="marketing_production.approval:record_approval",
        trace_stage="review",
        manual_note="ผู้อนุมัติตรวจแบบร่างแล้วบันทึกผล; คนกดเผยแพร่เองหลังอนุมัติเท่านั้น",
    ),
)

validate_flow(FLOW)

__all__ = ["FLOW"]
