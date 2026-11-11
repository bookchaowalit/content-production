"""Turntable encode: rendered orbit stills → one GIF or MP4, as its own traced job.

The stills come from ordinary ``scene compose`` jobs (one per ``orbit-fit-NNN``
composition, see :mod:`scene3d.turntable`). This job only encodes them with a
local FFmpeg under a fixed preset; it never changes the stills. Output is
content-addressed like a compose job: ``<output_root>/<fingerprint>/`` holds
the animation and ``encode-manifest.json``, ``.receipts/<fingerprint>.json``
pins their hashes, and a repeat with the same frames, preset and FFmpeg build
returns ``replay`` after checking those hashes. Every fresh job writes
``traces/<job_id>/trace.json`` and an FFmpeg ``MANUAL-STEPS.md``.

Transparent stills (``transparent*.v1`` backgrounds) are flattened onto white
first: H.264 has no alpha and GIF alpha is one bit, which would cut the soft
shadow. The manifest records this as ``flattened_on``.

CLI::

    python -m book_production_platform.scene3d.turntable_encode encode \\
        --frame f0.png --frame f1.png ... --output-root out --preset turntable-gif.v1 --ffmpeg ffmpeg
    python -m book_production_platform.scene3d.turntable_encode render \\
        --scene scene.json --input-root input --output-root jobs --steps 8 \\
        --blender blender --ffmpeg ffmpeg --preset turntable-mp4.v1
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from book_production_platform.production_trace import ManualGuide, TraceRecorder

from .errors import SceneInputError
from .jobs import fingerprint_lock
from .turntable import ALLOWED_STEPS, write_turntable

TOOL_ID = "scene3d-turntable-encode"
ENCODER_VERSION = "scene3d-turntable-encode.v1"
MANIFEST_SCHEMA = "scene3d.turntable-encode.v1"
RECEIPT_SCHEMA = "scene3d.turntable-receipt.v1"
TRACE_DIR = "traces"
MAX_FRAMES = max(ALLOWED_STEPS)
MAX_FRAME_BYTES = 64 * 1024 * 1024
MAX_FRAME_SIDE = 4096
MAX_TIMEOUT_SECONDS = 300
FLATTEN_COLOR = (255, 255, 255)
# Fixed encode presets. ``loops`` repeats the frame list so a short orbit plays
# for a few seconds in players that do not loop MP4.
ENCODE_PRESETS: dict[str, dict[str, Any]] = {
    "turntable-gif.v1": {"format": "gif", "extension": "gif", "media_type": "image/gif", "fps": 4, "loops": 1, "max_side": 512},
    "turntable-mp4.v1": {"format": "mp4", "extension": "mp4", "media_type": "video/mp4", "fps": 4, "loops": 3, "crf": 18},
}


@dataclass(frozen=True)
class EncodeResult:
    status: str
    fingerprint: str
    artifact_dir: Path
    artifact: Path
    artifact_hashes: dict[str, str]
    replay: bool
    model_calls: int = 0


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def ffmpeg_identity(ffmpeg_bin: Path, timeout_s: int = 15) -> str:
    """The first line of ``ffmpeg -version``; part of the fingerprint, like Blender's build."""

    try:
        result = subprocess.run(
            [str(ffmpeg_bin), "-hide_banner", "-version"],
            check=False, capture_output=True, text=True, timeout=timeout_s, shell=False, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as error:
        raise SceneInputError("ffmpeg_version_timeout", "FFmpeg version check timed out") from error
    except OSError as error:
        raise SceneInputError("ffmpeg_unavailable", "FFmpeg executable is unavailable") from error
    first = result.stdout.splitlines()[0].strip() if result.stdout else ""
    if result.returncode != 0 or not first.startswith("ffmpeg version "):
        raise SceneInputError("ffmpeg_unavailable", "FFmpeg version check failed")
    return first


def _load_frames(frames: Sequence[Path]) -> list[tuple[Path, str, tuple[int, int], str]]:
    """Validate the stills: 1–8 PNG files, same size, within the caps. Returns (path, sha256, size, mode)."""

    if not 1 <= len(frames) <= MAX_FRAMES:
        raise SceneInputError("invalid_turntable_frames", f"between 1 and {MAX_FRAMES} frames are required")
    loaded = []
    for frame in frames:
        path = Path(frame)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FRAME_BYTES:
            raise SceneInputError("invalid_turntable_frames", "frame is missing, a symlink, or too large")
        try:
            data = path.read_bytes()  # one read: the checks below and the hash see the same bytes
        except OSError as error:
            raise SceneInputError("invalid_turntable_frames", "frame cannot be read") from error
        if len(data) > MAX_FRAME_BYTES:
            raise SceneInputError("invalid_turntable_frames", "frame is too large")
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "PNG":
                    raise SceneInputError("invalid_turntable_frames", "frames must be PNG")
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                size, mode = image.size, image.mode
                if max(size) > MAX_FRAME_SIDE or mode not in {"RGB", "RGBA"}:
                    raise SceneInputError("invalid_turntable_frames", "frame size or colour mode is not supported")
                image.load()  # decode the pixels: verify() checks chunks and CRCs, not the IDAT stream
        except SceneInputError:
            raise
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
            raise SceneInputError("invalid_turntable_frames", "frame is not a readable PNG") from error
        loaded.append((path, _sha256_bytes(data), size, mode))
    if len({item[2] for item in loaded}) != 1:
        raise SceneInputError("invalid_turntable_frames", "all frames must have the same size")
    return loaded


def encode_fingerprint(frame_hashes: Sequence[str], preset: str, ffmpeg_version: str) -> str:
    return _sha256_bytes(_canonical({
        "encoder_version": ENCODER_VERSION,
        "preset": preset,
        "frames": list(frame_hashes),
        "ffmpeg_version": ffmpeg_version,
    }))


def ffmpeg_arguments(preset: str, pattern: str, output: str) -> list[str]:
    """FFmpeg arguments (after the executable) for a numbered RGB PNG sequence."""

    spec = ENCODE_PRESETS[preset]
    head = ["-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-framerate", str(spec["fps"]), "-i", pattern, "-map_metadata", "-1"]
    if spec["format"] == "gif":
        side = spec["max_side"]
        graph = (
            f"scale='min({side},iw)':'min({side},ih)':force_original_aspect_ratio=decrease:flags=lanczos,"
            "split[a][b];[a]palettegen=stats_mode=full[p];[b][p]paletteuse=dither=sierra2_4a"
        )
        return [*head, "-vf", graph, "-loop", "0", "-f", "gif", output]
    return [
        *head, "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "medium",
        "-crf", str(spec["crf"]), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-fflags", "+bitexact", "-flags:v", "+bitexact", "-f", "mp4", output,
    ]


def _run_ffmpeg(command: list[str], timeout_s: int) -> None:
    try:
        result = subprocess.run(command, check=False, capture_output=True, timeout=timeout_s, shell=False, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as error:
        raise SceneInputError("deadline_exceeded", "FFmpeg exceeded its deadline") from error
    except OSError as error:
        raise SceneInputError("ffmpeg_unavailable", "FFmpeg could not be started") from error
    if result.returncode != 0:
        raise SceneInputError("ffmpeg_failed", "FFmpeg could not encode the turntable")


def _decoded_frame_count(ffmpeg_bin: Path, path: Path, timeout_s: int) -> int:
    try:
        result = subprocess.run(
            [str(ffmpeg_bin), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path), "-map", "0:v:0", "-f", "framecrc", "-"],
            check=False, capture_output=True, text=True, timeout=timeout_s, shell=False, stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        raise SceneInputError("invalid_turntable_output", "encoded turntable cannot be decoded") from error
    if result.returncode != 0:
        raise SceneInputError("invalid_turntable_output", "encoded turntable cannot be decoded")
    return sum(1 for line in result.stdout.splitlines() if line and not line.startswith("#"))


def verify_output(path: Path, preset: str, *, frames: int, size: tuple[int, int], ffmpeg_bin: Path, timeout_s: int) -> dict[str, Any]:
    """Check the container, frame count and size of the encoded file."""

    spec = ENCODE_PRESETS[preset]
    expected_frames = frames * spec["loops"]
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise SceneInputError("invalid_turntable_output", "FFmpeg did not write an output")
    head = path.read_bytes()[:12]
    if spec["format"] == "gif":
        if head[:6] != b"GIF89a":
            raise SceneInputError("invalid_turntable_output", "output is not a GIF")
        with Image.open(path) as image:
            width, height, count = image.width, image.height, getattr(image, "n_frames", 1)
        side = spec["max_side"]
        scale = min(1.0, side / size[0], side / size[1])
        if count != expected_frames or max(width, height) > side or abs(width - size[0] * scale) > 1 or abs(height - size[1] * scale) > 1:
            raise SceneInputError("invalid_turntable_output", "GIF frame count or size is wrong")
        return {"frames": count, "size": [width, height]}
    if head[4:8] != b"ftyp":
        raise SceneInputError("invalid_turntable_output", "output is not an MP4")
    count = _decoded_frame_count(ffmpeg_bin, path, timeout_s)
    if count != expected_frames:
        raise SceneInputError("invalid_turntable_output", "MP4 frame count is wrong")
    return {"frames": count, "size": [size[0] - size[0] % 2, size[1] - size[1] % 2]}


def _flatten(source: Path, expected_sha256: str, target: Path) -> bool:
    """Write ``source`` as an RGB PNG; alpha is composited onto white. Returns True if it had alpha.

    The bytes are read once and must still hash to ``expected_sha256`` (the value in the
    fingerprint), so a frame rewritten after validation cannot slip into the animation.
    """

    data = source.read_bytes()
    if _sha256_bytes(data) != expected_sha256:
        raise SceneInputError("source_changed", "a frame changed while the job was running")
    with Image.open(io.BytesIO(data)) as image:
        if image.mode == "RGBA":
            canvas = Image.new("RGBA", image.size, (*FLATTEN_COLOR, 255))
            canvas.alpha_composite(image)
            canvas.convert("RGB").save(target, format="PNG")
            return True
        image.convert("RGB").save(target, format="PNG")
        return False


def _write_atomic(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _verify_replay(final_dir: Path, receipt_path: Path, fingerprint: str) -> dict[str, str]:
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SceneInputError("replay_integrity_error", "turntable receipt is unreadable") from error
    if not isinstance(receipt, dict) or receipt.get("schema_version") != RECEIPT_SCHEMA or receipt.get("fingerprint") != fingerprint:
        raise SceneInputError("replay_integrity_error", "turntable receipt does not match")
    hashes = receipt.get("hashes")
    if final_dir.is_symlink() or not final_dir.is_dir():
        raise SceneInputError("replay_integrity_error", "turntable job folder is not a directory")
    try:
        if not isinstance(hashes, dict) or set(hashes) != {entry.name for entry in final_dir.iterdir()}:
            raise SceneInputError("replay_integrity_error", "turntable job folder changed")
        for name, expected in hashes.items():
            path = final_dir / name
            if path.is_symlink() or not path.is_file() or _sha256_file(path) != expected:
                raise SceneInputError("replay_integrity_error", "turntable artifact changed")
    except OSError as error:
        raise SceneInputError("replay_integrity_error", "turntable job folder cannot be read") from error
    return dict(hashes)


def _guide(preset: str, frames: int, flattened: bool) -> ManualGuide:
    spec = ENCODE_PRESETS[preset]
    pattern = "frame-%03d.png"
    output = f"turntable.{spec['extension']}"
    guide = ManualGuide(
        program="FFmpeg",
        alternatives=["Blender Video Sequencer", "Photoshop ▸ Timeline", "ezgif.com"],
        title=f"Turntable {preset} · {frames} ภาพ",
        summary="รวมภาพนิ่งรอบสินค้าเป็นไฟล์เคลื่อนไหวด้วยคำสั่งเดียวกับเครื่องมือ",
        estimated_minutes=5.0,
        setup=["ติดตั้ง FFmpeg รุ่นเดียวกับที่ระบุใน `encode-manifest.json` (ค่า `ffmpeg.version`)"],
    )
    section = "เตรียมภาพ"
    guide.step("เรียง `render.png` ของแต่ละมุมตามลำดับ 0°, 45°, 90° … แล้วตั้งชื่อเรียงเลข", value="frame-000.png, frame-001.png, …", section=section)
    if flattened:
        guide.step("ภาพมีพื้นหลังโปร่งใส: วางบนพื้นขาวแล้วบันทึกเป็น PNG RGB", tool="Photoshop ▸ Layer ▸ Flatten Image (พื้นขาว)", value="#FFFFFF", section=section)
    if spec["loops"] > 1:
        guide.step(f"ทำสำเนาชุดภาพต่อท้ายให้ครบ {spec['loops']} รอบ แล้วเรียงเลขต่อกัน", value=f"{frames * spec['loops']} ภาพ", section=section)
    section = "Encode"
    command = shlex.join(["ffmpeg", *ffmpeg_arguments(preset, pattern, output)])
    guide.step("รันคำสั่ง", tool="Terminal", value=command, section=section)
    guide.checks = [
        f"`{output}` มี {frames * spec['loops']} เฟรม ที่ {spec['fps']} fps",
        "เปิดไฟล์แล้วสินค้าหมุนครบรอบโดยไม่กระตุกย้อน",
    ]
    return guide


def encode_turntable(
    frames: Sequence[Path],
    output_root: Path,
    *,
    preset: str,
    ffmpeg_bin: Path,
    timeout_s: int = MAX_TIMEOUT_SECONDS,
    frame_angles: Sequence[int] | None = None,
) -> EncodeResult:
    """Encode the stills with ``preset``, publish content-addressed, and trace the job."""

    if preset not in ENCODE_PRESETS:
        raise SceneInputError("invalid_turntable_preset", f"preset must be one of {sorted(ENCODE_PRESETS)}")
    if not 1 <= timeout_s <= MAX_TIMEOUT_SECONDS:
        raise SceneInputError("invalid_timeout", f"timeout must be between 1 and {MAX_TIMEOUT_SECONDS} seconds")
    output_root = Path(output_root)
    trace = TraceRecorder(tool_id=TOOL_ID, engine_version=ENCODER_VERSION, recipe_version=preset, schema_version=MANIFEST_SCHEMA)
    try:
        return _encode(frames, output_root, preset, Path(ffmpeg_bin), timeout_s, frame_angles, trace)
    except SceneInputError as error:
        _trace_failure(trace, output_root, error.code)
        raise


def _trace_failure(trace: TraceRecorder, output_root: Path, code: str) -> None:
    try:
        if output_root.is_symlink() or (output_root.exists() and not output_root.is_dir()):
            return
        trace.record_step("outcome", "failed", note=code)
        trace.finish(status="failed", job_dir=output_root / TRACE_DIR / trace.job_id, output_root=output_root)
    except OSError:
        pass


def _encode(
    frames: Sequence[Path],
    output_root: Path,
    preset: str,
    ffmpeg_bin: Path,
    timeout_s: int,
    frame_angles: Sequence[int] | None,
    trace: TraceRecorder,
) -> EncodeResult:
    spec = ENCODE_PRESETS[preset]
    with trace.step("validate", "turntable_frames", preset=preset, frames=len(frames)):
        loaded = _load_frames(frames)
    for index, (path, _digest, _size, _mode) in enumerate(loaded):
        trace.input_file(f"frame-{index:03d}", path, role="still")
    with trace.step("validate", "ffmpeg_version"):
        version = ffmpeg_identity(ffmpeg_bin)
    frame_hashes = [digest for _path, digest, _size, _mode in loaded]
    fingerprint = encode_fingerprint(frame_hashes, preset, version)
    if output_root.is_symlink() or (output_root.exists() and not output_root.is_dir()):
        raise SceneInputError("unsafe_output_path", "output root is unsafe")
    output_root.mkdir(parents=True, exist_ok=True)
    final_dir = output_root / fingerprint
    receipt_path = output_root / ".receipts" / f"{fingerprint}.json"
    artifact_name = f"turntable.{spec['extension']}"
    receipts = receipt_path.parent
    if receipts.is_symlink() or receipt_path.is_symlink() or (receipts.exists() and not receipts.is_dir()):
        raise SceneInputError("unsafe_output_path", "receipt path is unsafe")
    # One encode per fingerprint at a time: a second caller waits, then replays the published job.
    with fingerprint_lock(output_root, fingerprint, timeout_s):
        if final_dir.exists() or final_dir.is_symlink():
            hashes = _verify_replay(final_dir, receipt_path, fingerprint)
            return EncodeResult("accepted", fingerprint, final_dir, final_dir / artifact_name, hashes, replay=True)

        size = loaded[0][2]
        staging = Path(tempfile.mkdtemp(prefix=".turntable-", dir=output_root))
        try:
            sequence = staging / "sequence"
            sequence.mkdir()
            with trace.step("prepare", "flatten_frames", flatten_color=list(FLATTEN_COLOR), loops=spec["loops"]) as prepare:
                flattened = False
                for index, (path, digest, *_rest) in enumerate(loaded):
                    flattened = _flatten(path, digest, sequence / f"still-{index:03d}.png") or flattened
                position = 0
                for _loop in range(spec["loops"]):
                    for index in range(len(loaded)):
                        shutil.copyfile(sequence / f"still-{index:03d}.png", sequence / f"frame-{position:03d}.png")
                        position += 1
                prepare.outputs = [f"{position} frames"]
            encoded = staging / artifact_name
            arguments = ffmpeg_arguments(preset, str(sequence / "frame-%03d.png"), str(encoded))
            with trace.step("encode", "ffmpeg", ffmpeg_version=version, arguments=ffmpeg_arguments(preset, "frame-%03d.png", artifact_name)):
                _run_ffmpeg([str(ffmpeg_bin), *arguments], timeout_s)
            with trace.step("verify", "turntable_output") as verify_step:
                checked = verify_output(encoded, preset, frames=len(loaded), size=size, ffmpeg_bin=ffmpeg_bin, timeout_s=timeout_s)
                verify_step.outputs = [artifact_name]

            job_dir = staging / "job"
            job_dir.mkdir()
            os.replace(encoded, job_dir / artifact_name)
            manifest = {
                "schema_version": MANIFEST_SCHEMA,
                "status": "accepted",
                "fingerprint": fingerprint,
                "encoder_version": ENCODER_VERSION,
                "preset": preset,
                "encode": {key: value for key, value in spec.items() if key not in {"extension", "media_type"}},
                "frames": [
                    {"index": index, "sha256": digest, "angle_degrees": frame_angles[index] if frame_angles else None}
                    for index, digest in enumerate(frame_hashes)
                ],
                "source_size": list(size),
                "flattened_on": "#FFFFFF" if flattened else None,
                "ffmpeg": {"version": version},
                "output": {"file": artifact_name, "media_type": spec["media_type"], "sha256": _sha256_file(job_dir / artifact_name), **checked},
                "model_calls": 0,
            }
            (job_dir / "encode-manifest.json").write_bytes(json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
            hashes = {entry.name: _sha256_file(entry) for entry in job_dir.iterdir()}
            with trace.step("publish", "content_addressed", fingerprint=fingerprint) as publish:
                receipts.mkdir(exist_ok=True)
                if receipts.is_symlink() or not receipts.is_dir():
                    raise SceneInputError("unsafe_output_path", "receipt path is unsafe")
                try:
                    os.rename(job_dir, final_dir)
                except OSError as error:
                    if final_dir.exists():
                        hashes = _verify_replay(final_dir, receipt_path, fingerprint)
                        return EncodeResult("accepted", fingerprint, final_dir, final_dir / artifact_name, hashes, replay=True)
                    raise SceneInputError("publication_failed", "turntable could not be published") from error
                _write_atomic(receipt_path, json.dumps({"schema_version": RECEIPT_SCHEMA, "fingerprint": fingerprint, "hashes": hashes}, sort_keys=True).encode("utf-8"))
                publish.outputs = sorted(hashes)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    try:
        trace_dir = output_root / TRACE_DIR / trace.job_id
        trace_dir.mkdir(parents=True, exist_ok=True)
        for name, media_type in ((artifact_name, spec["media_type"]), ("encode-manifest.json", "application/json")):
            trace.artifact(final_dir / name, media_type=media_type)
            trace.artifacts[-1]["file"] = os.path.relpath(final_dir / name, trace_dir).replace(os.sep, "/")
        trace.finish(
            status="accepted", job_dir=trace_dir, output_root=output_root,
            guide=_guide(preset, len(loaded), manifest["flattened_on"] is not None),
            qa={"status": "passed", "checks": ["frames", "ffmpeg_version", "container", "frame_count", "size"]},
        )
    except OSError:
        pass
    return EncodeResult("accepted", fingerprint, final_dir, final_dir / artifact_name, hashes, replay=False)


def render_turntable(
    scene_path: Path,
    input_root: Path,
    output_root: Path,
    *,
    steps: int,
    blender_bin: Path,
    ffmpeg_bin: Path,
    preset: str,
    timeout_s: int = MAX_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Compose every orbit still as its own scene job, then encode them as one turntable job."""

    from .runtime import compose_scene

    if preset not in ENCODE_PRESETS:
        raise SceneInputError("invalid_turntable_preset", f"preset must be one of {sorted(ENCODE_PRESETS)}")
    output_root = Path(output_root)
    with tempfile.TemporaryDirectory(prefix="turntable-scenes-") as scenes_dir:
        spec = write_turntable(Path(scene_path), Path(scenes_dir), steps=steps)
        frames = []
        for entry in spec["frames"]:
            result = compose_scene(Path(scenes_dir) / entry["scene"], Path(input_root), output_root / "frames", Path(blender_bin), timeout_s)
            frames.append({**entry, "fingerprint": result.fingerprint, "replay": result.replay, "render": result.artifact_dir / "render.png"})
    encoded = encode_turntable(
        [frame["render"] for frame in frames], output_root / "turntables",
        preset=preset, ffmpeg_bin=Path(ffmpeg_bin), timeout_s=timeout_s,
        frame_angles=[frame["angle_degrees"] for frame in frames],
    )
    return {
        "status": encoded.status,
        "fingerprint": encoded.fingerprint,
        "replay": encoded.replay,
        "artifact": str(encoded.artifact),
        "artifact_dir": str(encoded.artifact_dir),
        "frames": [
            {"angle_degrees": frame["angle_degrees"], "composition": frame["composition"], "fingerprint": frame["fingerprint"], "replay": frame["replay"]}
            for frame in frames
        ],
        "model_calls": 0,
    }


def _executable(value: Path | None, variable: str, name: str) -> Path:
    if value is not None:
        return value
    found = os.environ.get(variable) or shutil.which(name)
    if not found:
        raise SceneInputError(f"{name}_unavailable", f"set {variable} or pass --{name}")
    return Path(found)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scene3d-turntable-encode", description="encode turntable stills into a GIF or MP4")
    commands = parser.add_subparsers(dest="command", required=True)
    encode = commands.add_parser("encode", help="encode existing stills (in order)")
    encode.add_argument("--frame", type=Path, action="append", required=True)
    encode.add_argument("--output-root", type=Path, required=True)
    render = commands.add_parser("render", help="compose every orbit angle, then encode")
    render.add_argument("--scene", type=Path, required=True)
    render.add_argument("--input-root", type=Path, required=True)
    render.add_argument("--output-root", type=Path, required=True)
    render.add_argument("--steps", type=int, default=8, choices=ALLOWED_STEPS)
    render.add_argument("--blender", type=Path)
    for sub in (encode, render):
        sub.add_argument("--preset", default="turntable-gif.v1", choices=sorted(ENCODE_PRESETS))
        sub.add_argument("--ffmpeg", type=Path)
        sub.add_argument("--timeout-s", type=int, default=MAX_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    try:
        ffmpeg_bin = _executable(args.ffmpeg, "FFMPEG_BIN", "ffmpeg")
        if args.command == "encode":
            result = encode_turntable(args.frame, args.output_root, preset=args.preset, ffmpeg_bin=ffmpeg_bin, timeout_s=args.timeout_s)
            payload: dict[str, Any] = {
                "status": result.status, "fingerprint": result.fingerprint, "replay": result.replay,
                "artifact": str(result.artifact), "artifact_hashes": result.artifact_hashes, "model_calls": 0,
            }
        else:
            payload = render_turntable(
                args.scene, args.input_root, args.output_root, steps=args.steps,
                blender_bin=_executable(args.blender, "BLENDER_BIN", "blender"), ffmpeg_bin=ffmpeg_bin,
                preset=args.preset, timeout_s=args.timeout_s,
            )
    except SceneInputError as error:
        print(json.dumps({"status": "rejected", "error": error.code}), file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
