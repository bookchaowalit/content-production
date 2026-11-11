"""Turntable encode: stills → GIF/MP4 as a content-addressed, traced job.

Validation and the FFmpeg command are checked without FFmpeg. Encoding runs
against a real FFmpeg (``FFMPEG_BIN`` or ``ffmpeg`` on PATH) and is skipped
when none is installed.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from book_production_platform.production_trace import read_history
from book_production_platform.scene3d import runtime
from book_production_platform.scene3d import turntable_encode as encode
from book_production_platform.scene3d.errors import SceneInputError
from book_production_platform.scene3d.jobs import JobResult


def ffmpeg_executable() -> Path:
    found = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg")
    if not found or not Path(found).is_file():
        pytest.skip("FFmpeg is required; install it or set FFMPEG_BIN")
    return Path(found)


def write_frames(root: Path, count: int = 4, *, size: tuple[int, int] = (96, 64), alpha: bool = False) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    frames = []
    for index in range(count):
        mode = "RGBA" if alpha else "RGB"
        image = Image.new(mode, size, (0, 0, 0, 0) if alpha else (230, 232, 236))
        x = 8 + index * 12
        ImageDraw.Draw(image).rectangle((x, 16, x + 20, 48), fill=(20, 90, 160, 255) if alpha else (20, 90, 160))
        path = root / f"frame-{index}.png"
        image.save(path, format="PNG")
        frames.append(path)
    return frames


def test_frames_are_validated_before_ffmpeg_runs(tmp_path: Path) -> None:
    good = write_frames(tmp_path / "good", 2)
    with pytest.raises(SceneInputError) as error:
        encode._load_frames([])
    assert error.value.code == "invalid_turntable_frames"
    with pytest.raises(SceneInputError):
        encode._load_frames(good * 5)  # 10 frames > 8
    other = write_frames(tmp_path / "other", 1, size=(64, 64))
    with pytest.raises(SceneInputError):
        encode._load_frames([good[0], other[0]])
    jpeg = tmp_path / "frame.jpg"
    Image.new("RGB", (96, 64)).save(jpeg, format="JPEG")
    with pytest.raises(SceneInputError):
        encode._load_frames([jpeg])
    link = tmp_path / "link.png"
    link.symlink_to(good[0])
    with pytest.raises(SceneInputError):
        encode._load_frames([link])
    assert [item[2] for item in encode._load_frames(good)] == [(96, 64), (96, 64)]


def test_unknown_preset_and_bad_timeout_are_refused(tmp_path: Path) -> None:
    frames = write_frames(tmp_path / "f", 1)
    with pytest.raises(SceneInputError) as error:
        encode.encode_turntable(frames, tmp_path / "out", preset="turntable-webm.v1", ffmpeg_bin=Path("ffmpeg"))
    assert error.value.code == "invalid_turntable_preset"
    with pytest.raises(SceneInputError) as error:
        encode.encode_turntable(frames, tmp_path / "out", preset="turntable-gif.v1", ffmpeg_bin=Path("ffmpeg"), timeout_s=0)
    assert error.value.code == "invalid_timeout"


def test_ffmpeg_arguments_are_fixed_per_preset() -> None:
    gif = encode.ffmpeg_arguments("turntable-gif.v1", "frame-%03d.png", "turntable.gif")
    assert gif[gif.index("-framerate") + 1] == "4" and gif[-2:] == ["gif", "turntable.gif"]
    assert "palettegen=stats_mode=full" in gif[gif.index("-vf") + 1] and gif[gif.index("-loop") + 1] == "0"
    mp4 = encode.ffmpeg_arguments("turntable-mp4.v1", "frame-%03d.png", "turntable.mp4")
    for flag, value in (("-c:v", "libx264"), ("-pix_fmt", "yuv420p"), ("-crf", "18"), ("-movflags", "+faststart")):
        assert mp4[mp4.index(flag) + 1] == value
    assert "-shell" not in mp4 and all(isinstance(argument, str) for argument in mp4)


def test_missing_ffmpeg_is_a_stable_error_and_is_traced(tmp_path: Path) -> None:
    frames = write_frames(tmp_path / "f", 2)
    with pytest.raises(SceneInputError) as error:
        encode.encode_turntable(frames, tmp_path / "out", preset="turntable-gif.v1", ffmpeg_bin=tmp_path / "no-ffmpeg")
    assert error.value.code == "ffmpeg_unavailable"
    assert [entry["status"] for entry in read_history(tmp_path / "out")] == ["failed"]


@pytest.mark.parametrize("preset", sorted(encode.ENCODE_PRESETS))
def test_encode_publishes_verified_output_and_replays(tmp_path: Path, preset: str) -> None:
    ffmpeg = ffmpeg_executable()
    frames = write_frames(tmp_path / "frames", 4, alpha=True)
    output_root = tmp_path / "out"

    result = encode.encode_turntable(frames, output_root, preset=preset, ffmpeg_bin=ffmpeg, frame_angles=[0, 90, 180, 270])

    spec = encode.ENCODE_PRESETS[preset]
    assert result.status == "accepted" and not result.replay
    assert sorted(path.name for path in result.artifact_dir.iterdir()) == sorted(["encode-manifest.json", f"turntable.{spec['extension']}"])
    manifest: dict[str, Any] = json.loads((result.artifact_dir / "encode-manifest.json").read_text(encoding="utf-8"))
    assert manifest["fingerprint"] == result.fingerprint and manifest["preset"] == preset
    assert manifest["output"]["frames"] == 4 * spec["loops"]
    assert manifest["flattened_on"] == "#FFFFFF"
    assert [frame["angle_degrees"] for frame in manifest["frames"]] == [0, 90, 180, 270]
    assert manifest["ffmpeg"]["version"].startswith("ffmpeg version ")
    if spec["format"] == "gif":
        with Image.open(result.artifact) as gif:
            assert gif.n_frames == 4 and gif.size == (96, 64)
            first = gif.convert("RGB").getpixel((2, 2))
        assert all(channel > 240 for channel in first)  # transparent pixels became white

    history = read_history(output_root)
    assert [entry["status"] for entry in history] == ["accepted"]
    trace = json.loads((output_root / history[0]["trace"]).read_text(encoding="utf-8"))
    operations = [step["operation"] for step in trace["steps"]]
    assert operations == ["turntable_frames", "ffmpeg_version", "flatten_frames", "ffmpeg", "turntable_output", "content_addressed"]
    assert (output_root / history[0]["trace"]).with_name("MANUAL-STEPS.md").is_file()

    again = encode.encode_turntable(frames, output_root, preset=preset, ffmpeg_bin=ffmpeg)
    assert again.replay and again.fingerprint == result.fingerprint and again.artifact_hashes == result.artifact_hashes
    assert len(read_history(output_root)) == 1  # replays write no trace

    result.artifact.write_bytes(result.artifact.read_bytes() + b"tamper")
    with pytest.raises(SceneInputError) as error:
        encode.encode_turntable(frames, output_root, preset=preset, ffmpeg_bin=ffmpeg)
    assert error.value.code == "replay_integrity_error"


def test_different_frames_get_a_different_fingerprint(tmp_path: Path) -> None:
    ffmpeg = ffmpeg_executable()
    first = encode.encode_turntable(write_frames(tmp_path / "a", 2), tmp_path / "out", preset="turntable-gif.v1", ffmpeg_bin=ffmpeg)
    second = encode.encode_turntable(write_frames(tmp_path / "b", 3), tmp_path / "out", preset="turntable-gif.v1", ffmpeg_bin=ffmpeg)
    assert first.fingerprint != second.fingerprint


def test_render_composes_each_angle_then_encodes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ffmpeg = ffmpeg_executable()
    input_root = tmp_path / "input"
    input_root.mkdir()
    scene = {
        "schema_version": "scene-ir.v1", "asset": {"path": "product.glb"}, "recipe": "product-hero.studio.v1",
        "composition": "three-quarter-fit.v1", "lighting": "softbox-three-point.v1", "background": "transparent-shadow.v1",
        "material_preset": "gloss.v1", "render_profile": "square-1024.v1",
    }
    (input_root / "scene.json").write_text(json.dumps(scene), encoding="utf-8")
    composed: list[str] = []

    def fake_compose(scene_path: Path, _input_root: Path, output_root: Path, _blender: Path, _timeout: int) -> JobResult:
        value = json.loads(scene_path.read_text(encoding="utf-8"))
        assert {**value, "composition": scene["composition"]} == scene
        composed.append(value["composition"])
        job = output_root / value["composition"]
        job.mkdir(parents=True)
        write_frames(job, 1, size=(64, 64), alpha=True)[0].rename(job / "render.png")
        return JobResult("accepted", value["composition"], job, {}, replay=False)

    monkeypatch.setattr(runtime, "compose_scene", fake_compose)
    result = encode.render_turntable(
        input_root / "scene.json", input_root, tmp_path / "jobs", steps=4,
        blender_bin=tmp_path / "blender", ffmpeg_bin=ffmpeg, preset="turntable-gif.v1",
    )

    assert composed == ["orbit-fit-000.v1", "orbit-fit-090.v1", "orbit-fit-180.v1", "orbit-fit-270.v1"]
    assert [frame["angle_degrees"] for frame in result["frames"]] == [0, 90, 180, 270]
    assert Path(result["artifact"]).is_file() and result["model_calls"] == 0
    assert Path(result["artifact_dir"]).parent == tmp_path / "jobs" / "turntables"


def test_cli_reports_rejections_as_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    frames = write_frames(tmp_path / "f", 1)
    code = encode.main(["encode", "--frame", str(frames[0]), "--output-root", str(tmp_path / "out"), "--ffmpeg", str(tmp_path / "missing")])
    assert code == 2
    assert json.loads(capsys.readouterr().err) == {"status": "rejected", "error": "ffmpeg_unavailable"}


@pytest.mark.parametrize("preset", sorted(encode.ENCODE_PRESETS))
def test_manual_command_is_shell_safe(preset: str) -> None:
    import shlex

    guide = encode._guide(preset, 8, flattened=True)
    command = next(step.value for step in guide.steps if step.tool == "Terminal")
    assert shlex.split(command) == ["ffmpeg", *encode.ffmpeg_arguments(preset, "frame-%03d.png", f"turntable.{encode.ENCODE_PRESETS[preset]['extension']}")]


def test_a_frame_rewritten_after_validation_is_refused(tmp_path: Path) -> None:
    frame = write_frames(tmp_path / "f", 1)[0]
    _path, digest, _size, _mode = encode._load_frames([frame])[0]
    write_frames(tmp_path / "g", 2)[1].replace(frame)
    with pytest.raises(SceneInputError) as error:
        encode._flatten(frame, digest, tmp_path / "out.png")
    assert error.value.code == "source_changed"


def test_symlinked_receipt_folder_is_refused(tmp_path: Path) -> None:
    ffmpeg = ffmpeg_executable()
    output_root = tmp_path / "out"
    output_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (output_root / ".receipts").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SceneInputError) as error:
        encode.encode_turntable(write_frames(tmp_path / "f", 2), output_root, preset="turntable-gif.v1", ffmpeg_bin=ffmpeg)
    assert error.value.code == "unsafe_output_path"
    assert list(outside.iterdir()) == []


def test_concurrent_encodes_of_the_same_frames_publish_once(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    ffmpeg = ffmpeg_executable()
    frames = write_frames(tmp_path / "f", 4)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: encode.encode_turntable(frames, tmp_path / "out", preset="turntable-gif.v1", ffmpeg_bin=ffmpeg), range(3)))
    assert len({result.fingerprint for result in results}) == 1
    assert sorted(result.replay for result in results) == [False, True, True]


def test_png_with_a_broken_pixel_stream_is_refused_at_validation(tmp_path: Path) -> None:
    import struct
    import zlib

    frame = write_frames(tmp_path / "f", 1)[0]
    data = bytearray(frame.read_bytes())
    start = data.index(b"IDAT") - 4
    length = struct.unpack(">I", data[start : start + 4])[0]
    body = bytes(b ^ 0x5A for b in data[start + 8 : start + 8 + length])  # garbage deflate stream
    crc = struct.pack(">I", zlib.crc32(b"IDAT" + body) & 0xFFFFFFFF)  # but a valid chunk CRC
    data[start + 8 : start + 12 + length] = body + crc
    frame.write_bytes(bytes(data))
    with pytest.raises(SceneInputError) as error:
        encode._load_frames([frame])
    assert error.value.code == "invalid_turntable_frames"


@pytest.mark.parametrize("replacement", ["symlink", "file"])
def test_replay_refuses_a_job_folder_that_is_not_a_real_directory(tmp_path: Path, replacement: str) -> None:
    ffmpeg = ffmpeg_executable()
    frames = write_frames(tmp_path / "f", 2)
    output_root = tmp_path / "out"
    result = encode.encode_turntable(frames, output_root, preset="turntable-gif.v1", ffmpeg_bin=ffmpeg)
    copy = tmp_path / "copy"
    shutil.copytree(result.artifact_dir, copy)
    shutil.rmtree(result.artifact_dir)
    if replacement == "symlink":
        result.artifact_dir.symlink_to(copy, target_is_directory=True)
    else:
        result.artifact_dir.write_bytes(b"not a folder")
    with pytest.raises(SceneInputError) as error:
        encode.encode_turntable(frames, output_root, preset="turntable-gif.v1", ffmpeg_bin=ffmpeg)
    assert error.value.code == "replay_integrity_error"


def test_decompression_bomb_header_is_a_stable_input_error(tmp_path: Path) -> None:
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    # 20000 × 20000 RGB header: far above Pillow's bomb limit, so Image.open itself raises.
    header = struct.pack(">IIBBBBB", 20000, 20000, 8, 2, 0, 0, 0)
    bomb = tmp_path / "bomb.png"
    bomb.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b""))
    with pytest.raises(SceneInputError) as error:
        encode._load_frames([bomb])
    assert error.value.code == "invalid_turntable_frames"


def test_frame_hash_comes_from_the_validated_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib

    frame = write_frames(tmp_path / "f", 1)[0]
    expected = hashlib.sha256(frame.read_bytes()).hexdigest()
    monkeypatch.setattr(encode, "_sha256_file", lambda _path: pytest.fail("frames must not be re-read for hashing"))
    assert encode._load_frames([frame])[0][1] == expected
