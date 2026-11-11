"""Follow docs/reverse/video-studio/davinci-resolve.md in MLT (melt 7.42), step by step.

Every value is read from the guide text (not from the engine): each numbered
Resolve step becomes one MLT operation on the project, the timeline playlist,
or a filter. Usage: python follow_guide_mlt.py GUIDE.md WORKDIR  -> WORKDIR/manual.mlt
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from xml.sax.saxutils import quoteattr

guide = Path(sys.argv[1]).read_text(encoding="utf-8")
work = Path(sys.argv[2])
log: list[str] = []


def step(pattern: str) -> list[str]:
    """Values (`ค่า: `...``) of the numbered steps whose action matches ``pattern``."""

    found = re.findall(rf"^\d+\. {pattern}.*?ค่า: `([^`]*)`", guide, flags=re.MULTILINE)
    if not found:
        raise SystemExit(f"guide step not found: {pattern}")
    return found


def tc_frames(label: str, fps: int) -> int:
    hours, minutes, seconds, frames = (int(part) for part in label.split(":"))
    return ((hours * 60 + minutes) * 60 + seconds) * fps + frames


# Project Settings
width, height = (int(v) for v in step("ตั้งความละเอียดไทม์ไลน์")[0].split(" x "))
fps = int(step("ตั้ง Timeline frame rate")[0])
scaling = step("ตั้งวิธีย่อภาพที่ขนาดไม่ตรง")[0]
assert scaling == "Scale entire image to fit", scaling  # MLT's default: fit with aspect, centred
log.append(f"Project: {width}x{height} @ {fps} fps, {scaling}")

# Import media + Append the whole clip at 00:00:00:00
source = step("นำเข้าไฟล์ต้นฉบับเข้า Media Pool")[0]
source_frames = 12 * fps  # Media Pool ▸ Clip Attributes of the synthetic source: 00:00:12:00
timeline: list[list[int]] = [[0, source_frames]]  # entries as [source_in, source_out) on V1+A1
log.append(f"Append {source} [0, {source_frames})")

# Blade at every cut point (timeline still equals source timecode)
for value in step(r"จุดตัดที่ \d+"):
    cut = tc_frames(value.split(" ")[0], fps)
    for index, (start, end) in enumerate(timeline):
        if start < cut < end:
            timeline[index : index + 1] = [[start, cut], [cut, end]]
            break
    log.append(f"Razor at {value.split(' ')[0]} (frame {cut})")

# Ripple delete the removed pieces, last first
for value in step(r"ลบชิ้นที่ \d+"):
    start_label, end_label = (part.strip() for part in value.split("→"))
    start = tc_frames(start_label.split(" ")[0], fps)
    end = source_frames if end_label == "จบไฟล์" else tc_frames(end_label.split(" ")[0], fps)
    timeline.remove([start, end])
    log.append(f"Ripple Delete [{start}, {end})")

# Check the clip order (Record IN/OUT) against the guide
record = 0
for value, (start, end) in zip(step(r"คลิปที่ \d+ \("), timeline, strict=True):
    match = re.match(r"Record IN (\S+) → OUT (\S+)", value)
    assert match and tc_frames(match[1], fps) == record and tc_frames(match[2], fps) == record + end - start, value
    record += end - start
log.append(f"Clips on V1: {timeline} ({record} frames)")

# Reframe: Inspector ▸ Transform ▸ Zoom about the frame centre, Position 0, 0
zoom = float(step("ขยายภาพให้เต็มเฟรมแนวตั้ง")[0])
zoom_w, zoom_h = width * zoom, height * zoom
rect = f"{(width - zoom_w) / 2:.3f} {(height - zoom_h) / 2:.3f} {zoom_w:.3f} {zoom_h:.3f} 1"
log.append(f"Zoom {zoom} -> affine rect {rect}")

# Subtitles: import captions.srt at 00:00:00:00 with the track style from the guide
srt = step("นำเข้าไฟล์ซับเข้า Media Pool")[0]
style = step("ตั้งสไตล์ทั้งแทร็ก")[0]
font = re.search(r"Font ([^;]+);", style)[1]
size = int(re.search(r"Size (\d+)", style)[1])
stroke = int(re.search(r"ขนาด (\d+)", step("ตั้งขอบตัวอักษรและเงา")[0])[1])
position = step("ตั้งตำแหน่ง")[0]
margin_bottom = int(re.search(r"ขอบล่าง (\d+) px", position)[1])
margin_side = int(re.search(r"ซ้าย/ขวา (\d+) px", position)[1])
log.append(f"Subtitles {srt}: {font} {size}px, stroke {stroke}, bottom {margin_bottom}px, sides {margin_side}px")

# Deliver: normalize loudness, H.264 MP4, AAC
target = float(re.search(r"Target Loudness (-?\d+(?:\.\d+)?) LKFS", step("ตั้ง normalize ความดังตอน Deliver")[0])[1])
audio = step("ตั้งเสียง")[0]
bitrate = re.search(r"(\d+) kb/s", audio)[1]
sample_rate = re.search(r"(\d+) Hz", audio)[1]
log.append(f"Deliver: H.264 MP4 {width}x{height} {fps} fps, AAC {bitrate}k {sample_rate} Hz, loudness {target} LUFS, burn in")

entries = "\n".join(f'    <entry producer="source" in="{start}" out="{end - 1}"/>' for start, end in timeline)
xml = f"""<?xml version="1.0" encoding="utf-8"?>
<mlt LC_NUMERIC="C">
  <profile width="{width}" height="{height}" frame_rate_num="{fps}" frame_rate_den="1" progressive="1"
           sample_aspect_num="1" sample_aspect_den="1" display_aspect_num="{width}" display_aspect_den="{height}" colorspace="709"/>
  <producer id="source"><property name="resource">{source}</property></producer>
  <playlist id="V1">
{entries}
  </playlist>
  <tractor id="timeline">
    <track producer="V1"/>
    <filter><property name="mlt_service">affine</property><property name="transition.rect">{rect}</property>
      <property name="transition.distort">0</property><property name="transition.fill">1</property></filter>
    <filter><property name="mlt_service">subtitle_feed</property><property name="resource">{srt}</property>
      <property name="feed">0</property></filter>
    <filter><property name="mlt_service">subtitle</property><property name="feed">0</property>
      <property name="family">{font}</property><property name="size">{size}</property>
      <property name="fgcolour">#ffffffff</property><property name="olcolour">#ff000000</property>
      <property name="outline">{stroke}</property><property name="halign">center</property><property name="valign">bottom</property>
      <property name="geometry">{margin_side} 0 {width - 2 * margin_side} {height - margin_bottom} 1</property></filter>
    <filter><property name="mlt_service">dynamic_loudness</property><property name="target_loudness">{target}</property></filter>
  </tractor>
</mlt>
"""
(work / "manual.mlt").write_text(xml, encoding="utf-8")
(work / "follow-log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
print("\n".join(log))
print(quoteattr(f"vcodec=libx264 acodec=aac ab={bitrate}k ar={sample_rate}"))
