"""Video Studio flow: which part is rules/code, where a model proposes, where a person decides.

Used by the reverse guide (``docs/reverse/video-studio/``) and the parity
matrix ``Flow`` column (``docs/parity/video.md``). Rules:
``production_trace.reverse.validate_flow``.

Model calls today:

* highlight proposals go through ``brief-adapter-kit`` → AI gateway (shadow
  only; ``live`` needs owner approval);
* transcription runs a local faster-whisper model by default (no network). The
  optional ``openai_compatible`` provider posts audio straight to a configured
  OpenAI-compatible base URL, **not** through the gateway; this is a known gap
  recorded in the parity matrix.
"""

from __future__ import annotations

from production_trace.reverse import FlowPart, validate_flow

FLOW: tuple[FlowPart, ...] = (
    FlowPart(
        "Upload and probe the source (size, type, duration, fps, codecs, sha256)",
        "LOGIC",
        engine="video_postproduction.media.ffprobe:FFprobeMetadataReader",
        trace_stage="probe",
        manual_note="นำเข้าไฟล์ต้นฉบับใน Media Pool แล้วดู Clip Attributes ให้ตรงกับค่าในคู่มือ",
    ),
    FlowPart(
        "Transcribe speech (Thai / English) with word timings",
        "AI-ASSIST",
        engine="video_postproduction.transcription.faster_whisper:FasterWhisperProvider",
        trace_stage="transcribe",
        manual_note="ฟังแล้วพิมพ์ซับเอง หรือใช้ไฟล์ captions.srt ที่ให้มา",
        fallback="ปิดซับ (`captions.enabled=false`) หรือเขียน transcript.v1 JSON เอง; ไม่ต้องเรียกโมเดล",
    ),
    FlowPart(
        "Normalize the transcript (bounded, ordered, finite timestamps)",
        "LOGIC",
        engine="video_postproduction.transcription.normalize:normalize_provider_result",
        trace_stage="transcribe",
        manual_note="ตรวจว่าเวลาแต่ละ cue ไม่ซ้อนกันและอยู่ในความยาวคลิป",
    ),
    FlowPart(
        "Silence and filler cut from the transcript (optional)",
        "LOGIC",
        engine="video_postproduction.silence:silence_cut",
        trace_stage="validate",
        manual_note="ตัดช่วงเงียบ/คำเติมตามจุดตัดในคู่มือ (เวลาเดียวกับ engine ทุกจุด)",
    ),
    FlowPart(
        "Highlight proposal from a brief (segment-id ranges only)",
        "AI-ASSIST",
        engine="video_postproduction.highlights.provider:propose_highlights",
        trace_stage="highlight_proposal",
        manual_note="ดู transcript แล้วเลือกช่วงคลิปเองตาม brief (จำนวนคลิป, ความยาว, หัวข้อ)",
        fallback="ส่ง `ranges` เป็น segment id เอง (structured route, model_calls 0)",
    ),
    FlowPart(
        "Ground the proposal: known ids, segment-aligned bounds, durations, no overlap",
        "LOGIC",
        engine="video_postproduction.highlights.engine:build_proposal",
        trace_stage="highlight_proposal",
        manual_note="ทุกคลิปต้องเริ่ม/จบตรงขอบ segment ของ transcript และยาวตามที่ brief ขอ",
    ),
    FlowPart(
        "Confirm the proposal before it becomes an EDL",
        "HUMAN-GATE",
        engine="video_postproduction.highlights.engine:build_confirmed_edl",
        trace_stage="highlight_proposal",
        manual_note="เจ้าของงานยืนยันช่วงคลิปที่เลือก (fingerprint ต้องตรงกับข้อเสนอ)",
    ),
    FlowPart(
        "Validate the EDL and build the frame-exact timeline plan",
        "LOGIC",
        engine="video_postproduction.timeline:build_timeline_plan",
        trace_stage="plan",
        manual_note="ตั้งโปรเจกต์ ตัด และเรียงคลิปตาม timecode ในคู่มือทุกค่า",
    ),
    FlowPart(
        "Captions: Thai newmm line breaks, cues at record positions",
        "LOGIC",
        engine="video_postproduction.captions.cues:timeline_cues",
        trace_stage="captions",
        manual_note="นำเข้า captions.srt ที่ 00:00:00:00 แล้วตั้งสไตล์ตามคู่มือ ห้ามตัดบรรทัดเอง",
    ),
    FlowPart(
        "Interchange: CMX3600 EDL, FCPXML 1.10, OpenTimelineIO",
        "LOGIC",
        engine="video_postproduction.interchange:write_interchange",
        trace_stage="interchange",
        manual_note="ทางลัด: File ▸ Import ▸ Timeline… แทนการตัดเอง",
    ),
    FlowPart(
        "Render: trim/concat on plan frames, reframe, burn-in, loudness, H.264",
        "LOGIC",
        engine="video_postproduction.render.ffmpeg:build_ffmpeg_argv",
        trace_stage="render",
        manual_note="ตั้ง Deliver ตามคู่มือ แล้ว Render",
    ),
    FlowPart(
        "QA: container, dimensions, duration against the plan",
        "LOGIC",
        engine="video_postproduction.render.service:RenderService",
        trace_stage="qa",
        manual_note="ทำตาม checklist 'ตรวจผล' ท้ายคู่มือ",
    ),
    FlowPart(
        "Review the render before publishing",
        "HUMAN-GATE",
        engine="apps/video-postproduction-web (render preview)",
        trace_stage="publish",
        manual_note="ดูไฟล์ที่ได้ทั้งคลิปก่อนส่งงาน",
    ),
    FlowPart(
        "Publish: trace.json, MANUAL-STEPS.md, history.jsonl, artifacts with sha256",
        "LOGIC",
        engine="video_postproduction.jobtrace:write_editor_bundle",
        trace_stage="publish",
        manual_note="เก็บไฟล์ส่งออกไว้คู่กับ EDL ของงาน",
    ),
)

validate_flow(FLOW)

__all__ = ["FLOW"]
