"""Level packs: an ordered list of GameIR levels with a checked difficulty curve.

A pack file (``game-level-pack.v1``) lists 2–12 GameIR files, relative to the
pack file, in play order::

    {"schema_version": "game-level-pack.v1", "pack_id": "forest-run",
     "title": "Forest Run", "difficulty_order": "ascending",
     "levels": [{"file": "level-1.json"}, {"file": "level-2.json"}]}

``plan_level_pack`` validates every level (the same validator as
``game build``), requires distinct scene ids and one template, computes the
level metrics, and, for ``difficulty_order: "ascending"``, refuses a pack whose
difficulty score ever drops (``difficulty_not_ascending``). No Unity is needed.

``build_level_pack`` then builds each level as its own ordinary ``game build``
job (content-addressed, replayable, traced) and publishes a pack folder
``<output_root>/packs/<fingerprint>/`` with ``pack-manifest.json`` (level order,
each level's job fingerprint and metrics, the Build Profiles scene order) and
``difficulty-curve.svg``. A pack trace with a Unity guide explains how to put
the scenes into one project in that order. Moving from one level to the next at
runtime still has to be wired by hand: the template has no scene-flow component yet.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from book_production_platform.game_ir import (
    GameInputError,
    GameIR,
    game_ir_sha256,
    parse_game_ir,
    unity_asset_path,
    validate_game_ir,
)
from book_production_platform.production_trace import ManualGuide, TraceRecorder

from .errors import GameJobError
from .jobs import _prepare_output_root, compose_game_scene, fingerprint_lock
from .metrics import LevelMetrics, level_metrics
from .tracing import trace_job_dir, trace_root

PACK_SCHEMA = "game-level-pack.v1"
PACK_MANIFEST_SCHEMA = "game-level-pack.manifest.v1"
PACK_RECEIPT_SCHEMA = "game-level-pack.receipt.v1"
PACK_VERSION = "game-level-pack.v1a"
TOOL_ID = "game-v1a-pack"
MAX_PACK_BYTES = 16 * 1024
MAX_LEVEL_BYTES = 256 * 1024
MIN_LEVELS = 2
MAX_LEVELS = 12
PACKS_DIR = "packs"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PackLevelRef(_Strict):
    file: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.json$")]


class LevelPack(_Strict):
    schema_version: Literal["game-level-pack.v1"]
    pack_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)]
    title: Annotated[str, Field(min_length=1, max_length=80)]
    difficulty_order: Literal["ascending", "free"] = "ascending"
    levels: Annotated[
        tuple[PackLevelRef, ...], BeforeValidator(lambda value: tuple(value) if isinstance(value, list) else value)
    ] = Field(min_length=MIN_LEVELS, max_length=MAX_LEVELS)


@dataclass(frozen=True)
class PlannedLevel:
    index: int
    file: str
    path: Path
    game_ir: GameIR
    game_ir_sha256: str
    metrics: LevelMetrics


@dataclass(frozen=True)
class PackPlan:
    pack: LevelPack
    pack_sha256: str
    levels: tuple[PlannedLevel, ...]
    curve: tuple[float, ...]
    warnings: tuple[str, ...]

    def summary(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack.pack_id,
            "title": self.pack.title,
            "difficulty_order": self.pack.difficulty_order,
            "levels": [
                {
                    "index": level.index,
                    "file": level.file,
                    "scene_id": level.game_ir.scene_id,
                    "schema_version": level.game_ir.schema_version,
                    "game_ir_sha256": level.game_ir_sha256,
                    "difficulty_score": level.metrics.difficulty_score,
                    "difficulty_band": level.metrics.difficulty_band,
                    "route_steps": level.metrics.route_steps,
                    "objectives": level.metrics.objective_count,
                    "hazards": level.metrics.hazard_count,
                }
                for level in self.levels
            ],
            "difficulty_curve": list(self.curve),
            "warnings": list(self.warnings),
        }


def _read_bounded(path: Path, limit: int, code: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            raise GameJobError(code, "file is missing, a symlink, or too large")
        data = path.read_bytes()
    except OSError as error:
        raise GameJobError(code, "file cannot be read") from error
    if len(data) > limit:
        raise GameJobError(code, "file is too large")
    return data


def parse_level_pack(payload: bytes) -> LevelPack:
    try:
        return LevelPack.model_validate(json.loads(payload.decode("utf-8")))
    except (UnicodeDecodeError, ValueError, ValidationError, RecursionError) as error:
        raise GameJobError("invalid_level_pack", "level pack is not a valid game-level-pack.v1") from error


def plan_level_pack(pack_path: Path) -> PackPlan:
    """Validate the pack and every level; compute metrics and the difficulty curve (no Unity)."""

    pack_path = Path(pack_path)
    payload = _read_bounded(pack_path, MAX_PACK_BYTES, "invalid_level_pack")
    pack = parse_level_pack(payload)
    root = pack_path.parent
    files = [ref.file for ref in pack.levels]
    if len(set(files)) != len(files) or any(PurePosixPath(name).name != name for name in files):
        raise GameJobError("invalid_level_pack", "level files must be distinct names next to the pack")
    levels: list[PlannedLevel] = []
    for index, name in enumerate(files):
        path = root / name
        data = _read_bounded(path, MAX_LEVEL_BYTES, "invalid_level_pack")
        try:
            level = validate_game_ir(parse_game_ir(data))
        except GameInputError as error:
            raise GameJobError(f"level_{index + 1}_{error.code}", f"level {index + 1} ({name}) is invalid") from error
        levels.append(PlannedLevel(index + 1, name, path, level, game_ir_sha256(level), level_metrics(level)))
    scene_ids = [level.game_ir.scene_id for level in levels]
    if len(set(scene_ids)) != len(scene_ids):
        raise GameJobError("duplicate_scene_id", "every level in a pack needs its own scene_id")
    audio_paths: dict[str, str] = {}
    for planned in levels:
        for asset in planned.game_ir.assets:
            unity_path = unity_asset_path(asset)
            previous = audio_paths.get(unity_path)
            if previous is not None and previous != asset.sha256:
                # One project holds every scene, so two different sounds cannot share a path.
                raise GameJobError("pack_asset_conflict", f"{unity_path} differs between levels; give the assets distinct ids")
            audio_paths[unity_path] = asset.sha256
    if len({level.game_ir.template for level in levels}) != 1:
        raise GameJobError("invalid_level_pack", "all levels must use one template")
    curve = tuple(level.metrics.difficulty_score for level in levels)
    drops = [index + 1 for index in range(1, len(curve)) if curve[index] < curve[index - 1]]
    warnings: list[str] = []
    if drops:
        if pack.difficulty_order == "ascending":
            raise GameJobError("difficulty_not_ascending", f"difficulty drops at level {drops[0]}")
        warnings.extend(f"difficulty_drops_at_level_{index}" for index in drops)
    warnings.extend(
        f"same_difficulty_at_level_{index + 1}" for index in range(1, len(curve)) if curve[index] == curve[index - 1]
    )
    pack_sha = hashlib.sha256(
        json.dumps({"pack": pack.model_dump(mode="json"), "levels": [level.game_ir_sha256 for level in levels]},
                   sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return PackPlan(pack, pack_sha, tuple(levels), curve, tuple(warnings))


def render_difficulty_curve(plan: PackPlan) -> bytes:
    """A small, byte-deterministic SVG of difficulty score per level (0–100)."""

    width, height, pad = 64 * len(plan.levels) + 64, 240, 32
    step = (width - 2 * pad) / max(1, len(plan.levels) - 1)

    def point(index: int, score: float) -> tuple[float, float]:
        return round(pad + index * step, 1), round(height - pad - score / 100 * (height - 2 * pad), 1)

    points = [point(index, score) for index, score in enumerate(plan.curve)]
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" data-pack="{plan.pack.pack_id}">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" stroke="#888"/>',
    ]
    for band, score in (("easy|medium", 30), ("medium|hard", 55)):
        y = point(0, score)[1]
        out.append(f'<line x1="{pad}" y1="{y}" x2="{width - pad}" y2="{y}" stroke="#ddd" stroke-dasharray="4 4" data-band="{band}"/>')
    out.append('<polyline fill="none" stroke="#3a6ea5" stroke-width="2" points="' + " ".join(f"{x},{y}" for x, y in points) + '"/>')
    for level, (x, y) in zip(plan.levels, points, strict=True):
        out.append(
            f'<circle cx="{x}" cy="{y}" r="4" fill="#3a6ea5" data-level="{level.index}" data-scene-id="{level.game_ir.scene_id}" '
            f'data-score="{level.metrics.difficulty_score}"/>'
        )
        out.append(f'<text x="{x}" y="{height - 12}" font-size="11" text-anchor="middle" font-family="sans-serif">{level.index}</text>')
    out.append("</svg>")
    return ("\n".join(out) + "\n").encode("utf-8")


@dataclass(frozen=True)
class PackResult:
    status: str
    fingerprint: str
    artifact_dir: Path
    artifact_hashes: dict[str, str]
    replay: bool
    levels: tuple[dict[str, Any], ...]
    model_calls: int = 0


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_replay(final_dir: Path, receipt_path: Path, fingerprint: str) -> dict[str, str]:
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if final_dir.is_symlink() or not final_dir.is_dir():
            raise GameJobError("replay_integrity_error", "pack folder is not a directory")
        names = {entry.name for entry in final_dir.iterdir()}
        if (
            not isinstance(receipt, dict) or receipt.get("schema_version") != PACK_RECEIPT_SCHEMA
            or receipt.get("fingerprint") != fingerprint or not isinstance(receipt.get("hashes"), dict)
            or set(receipt["hashes"]) != names
        ):
            raise GameJobError("replay_integrity_error", "pack receipt does not match")
        for name, expected in receipt["hashes"].items():
            path = final_dir / name
            if path.is_symlink() or not path.is_file() or _sha256_file(path) != expected:
                raise GameJobError("replay_integrity_error", "pack artifact changed")
    except GameJobError:
        raise
    except (OSError, ValueError) as error:
        raise GameJobError("replay_integrity_error", "pack folder cannot be read") from error
    return dict(receipt["hashes"])


def build_pack_guide(plan: PackPlan, scene_order: list[str]) -> ManualGuide:
    guide = ManualGuide(
        program="Unity",
        program_version="6000.6.2f1",
        alternatives=["Godot 4 (SceneTree.change_scene_to_file)"],
        title=f"Level pack `{plan.pack.pack_id}` · {len(plan.levels)} ด่าน",
        summary="รวมฉากของทุกด่านที่เครื่องมือ build แยกไว้ เข้า project เดียวตามลำดับเล่น แล้วต่อการไปด่านถัดไปเอง",
        estimated_minutes=float(10 + 5 * len(plan.levels)),
        setup=[
            "เปิด project ของด่านที่ 1 (โฟลเดอร์ `project/` ใน artifact ของด่านนั้น) ด้วย Unity 6000.6.2f1",
            "ถ้าทำแต่ละด่านเอง ให้ทำตาม `MANUAL-STEPS.md` ของด่านนั้นก่อน",
        ],
    )
    for level, scene in zip(plan.levels, scene_order, strict=True):
        guide.layer(f"ด่าน {level.index}", "Scene", content=f"{level.metrics.difficulty_band} · score {level.metrics.difficulty_score}", position=scene)
    s = "รวมฉาก"
    for level, scene in zip(plan.levels[1:], scene_order[1:], strict=True):
        audio = [unity_asset_path(asset) for asset in level.game_ir.assets]
        if audio:
            guide.step(
                f"คัดลอกฉากด่านที่ {level.index} และไฟล์เสียงที่ฉากใช้ จาก project ของด่านนั้นมาไว้ที่ path เดียวกัน (พร้อม .meta ทุกไฟล์)",
                target="Project ▸ Assets/Scenes + Assets/BookProduction/Audio", value="; ".join([scene, *audio]),
                note="ต้องเอา .meta มาด้วย เพราะฉากอ้างไฟล์เสียงด้วย GUID ใน .meta",
                section=s,
            )
        else:
            guide.step(
                f"คัดลอกฉากด่านที่ {level.index} จาก project ของด่านนั้นมาไว้ที่ path เดียวกัน (พร้อมไฟล์ .meta)",
                target="Project ▸ Assets/Scenes", value=scene, section=s,
            )
    guide.step(
        "เรียงฉากใน build ตามลำดับเล่น (ลากเรียงให้ด่าน 1 อยู่บนสุด)",
        tool="File ▸ Build Profiles ▸ Scene List ▸ Add Open Scenes / ลากไฟล์ .unity เข้า",
        value=" → ".join(scene_order), section=s,
    )
    s = "ไปด่านถัดไป"
    guide.step(
        "เมื่อชนะ ให้โหลดฉากถัดไปตามลำดับใน Scene List",
        tool="สคริปต์ของคุณเอง ▸ SceneManager.LoadScene(SceneManager.GetActiveScene().buildIndex + 1)",
        note="template ยังไม่มี component ไปด่านถัดไป: เรียกเมื่อ GameProgress ▸ Is Won เป็น on; ด่านสุดท้ายให้จบเกม",
        section=s,
    )
    guide.checks = [
        f"Scene List มี {len(scene_order)} ฉาก เรียง {' → '.join(scene_order)}",
        "ความยากไล่จากง่ายไปยาก: " + " → ".join(f"{level.metrics.difficulty_score}" for level in plan.levels),
        "เล่นจบด่าน 1 แล้วไปด่าน 2 จนถึงด่านสุดท้าย",
    ]
    return guide


def build_level_pack(
    pack_path: Path,
    output_root: Path,
    unity_bin: Path,
    *,
    timeout_s: int = 120,
    asset_root: Path | None = None,
) -> PackResult:
    """Build every level as its own job, then publish the pack manifest content-addressed."""

    plan = plan_level_pack(pack_path)
    output_root = _prepare_output_root(output_root)  # the same normalized root for levels and the pack
    trace = TraceRecorder(tool_id=TOOL_ID, engine_version=PACK_VERSION, recipe_version=PACK_VERSION, schema_version=PACK_SCHEMA)
    trace.record_step("validate", "plan_level_pack", levels=len(plan.levels), curve=list(plan.curve), warnings=list(plan.warnings))
    try:
        return _build(plan, output_root, unity_bin, timeout_s, asset_root, trace, compose_game_scene)
    except (GameJobError, GameInputError) as error:
        _trace_failure(trace, output_root, error.code)
        raise


def _trace_failure(trace: TraceRecorder, output_root: Path, code: str) -> None:
    try:
        if output_root.is_symlink() or (output_root.exists() and not output_root.is_dir()):
            return
        trace.record_step("outcome", "failed", note=code)
        trace.finish(status="failed", job_dir=trace_job_dir(output_root, trace.job_id), output_root=trace_root(output_root))
    except OSError:
        pass


def _finish_replay(trace: TraceRecorder, output_root: Path, final_dir: Path, fingerprint: str) -> None:
    """A replay is recorded too (no guide: the accepted run already wrote one)."""

    try:
        trace.record_step("publish", "replay_verified", fingerprint=fingerprint)
        trace_dir = trace_job_dir(output_root, trace.job_id)
        trace_dir.mkdir(parents=True, exist_ok=True)
        for name, media_type in (("pack-manifest.json", "application/json"), ("difficulty-curve.svg", "image/svg+xml")):
            trace.artifact(final_dir / name, media_type=media_type)
            trace.artifacts[-1]["file"] = os.path.relpath(final_dir / name, trace_dir).replace(os.sep, "/")
        trace.finish(status="replayed", job_dir=trace_dir, output_root=trace_root(output_root))
    except OSError:
        pass


def _require_planned_level(artifact_dir: Path, level: PlannedLevel) -> None:
    """The level job must have built exactly the GameIR the curve was checked on."""

    try:
        manifest = json.loads((Path(artifact_dir) / "qa-manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise GameJobError("source_changed", f"level {level.index} build cannot be matched to the plan") from error
    if not isinstance(manifest, dict) or manifest.get("normalized_game_ir_sha256") != level.game_ir_sha256:
        raise GameJobError("source_changed", f"level {level.index} ({level.file}) changed after the pack was checked")


def _build(
    plan: PackPlan,
    output_root: Path,
    unity_bin: Path,
    timeout_s: int,
    asset_root: Path | None,
    trace: TraceRecorder,
    compose: Any,
) -> PackResult:
    built: list[dict[str, Any]] = []
    for level in plan.levels:
        with trace.step("build", "game_build_level", index=level.index, scene_id=level.game_ir.scene_id):
            result = compose(level.path, output_root, unity_bin, timeout_s=timeout_s, asset_root=asset_root)
            _require_planned_level(result.artifact_dir, level)
        built.append({
            "index": level.index,
            "file": level.file,
            "scene_id": level.game_ir.scene_id,
            "scene_path": f"Assets/Scenes/{level.game_ir.scene_id}.unity",
            "game_ir_sha256": level.game_ir_sha256,
            "job_fingerprint": result.fingerprint,
            "replay": result.replay,
            "metrics": level.metrics.to_dict(),
        })
    fingerprint = hashlib.sha256(json.dumps(
        {"pack_version": PACK_VERSION, "pack_sha256": plan.pack_sha256, "jobs": [item["job_fingerprint"] for item in built]},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    packs = output_root / PACKS_DIR
    if packs.is_symlink() or (packs.exists() and not packs.is_dir()):
        raise GameJobError("unsafe_output_path", "pack output path is unsafe")
    packs.mkdir(parents=True, exist_ok=True)
    receipts = packs / ".receipts"
    if receipts.is_symlink() or (receipts.exists() and not receipts.is_dir()):
        raise GameJobError("unsafe_output_path", "pack receipt path is unsafe")
    final_dir = packs / fingerprint
    receipt_path = receipts / f"{fingerprint}.json"
    levels_out = tuple({key: value for key, value in item.items() if key != "replay"} for item in built)
    # One publish per fingerprint at a time; a second caller waits, then replays.
    with fingerprint_lock(packs, fingerprint, time.monotonic() + timeout_s):
        if final_dir.exists() or final_dir.is_symlink():
            hashes = _verify_replay(final_dir, receipt_path, fingerprint)
            _finish_replay(trace, output_root, final_dir, fingerprint)
            return PackResult("accepted", fingerprint, final_dir, hashes, True, levels_out)

        scene_order = [item["scene_path"] for item in built]
        manifest = {
            "schema_version": PACK_MANIFEST_SCHEMA,
            "status": "accepted",
            "fingerprint": fingerprint,
            "pack_version": PACK_VERSION,
            "pack_id": plan.pack.pack_id,
            "title": plan.pack.title,
            "difficulty_order": plan.pack.difficulty_order,
            "pack_sha256": plan.pack_sha256,
            "levels": list(levels_out),
            "difficulty_curve": list(plan.curve),
            "warnings": list(plan.warnings),
            "build_profile_scene_order": scene_order,
            "scene_flow": "manual",
            "model_calls": 0,
        }
        staging = Path(tempfile.mkdtemp(prefix=".pack-", dir=packs))
        try:
            (staging / "pack-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (staging / "difficulty-curve.svg").write_bytes(render_difficulty_curve(plan))
            hashes = {entry.name: _sha256_file(entry) for entry in staging.iterdir()}
            with trace.step("publish", "content_addressed", fingerprint=fingerprint):
                receipts.mkdir(exist_ok=True)
                temporary = receipts / f".{fingerprint}.{uuid.uuid4().hex}.tmp"
                temporary.write_text(json.dumps({"schema_version": PACK_RECEIPT_SCHEMA, "fingerprint": fingerprint, "hashes": hashes}, sort_keys=True), encoding="utf-8")
                os.replace(temporary, receipt_path)
                os.rename(staging, final_dir)
        except OSError as error:
            raise GameJobError("publication_failed", "level pack could not be published") from error
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    try:
        trace_dir = trace_job_dir(output_root, trace.job_id)
        trace_dir.mkdir(parents=True, exist_ok=True)
        for name, media_type in (("pack-manifest.json", "application/json"), ("difficulty-curve.svg", "image/svg+xml")):
            trace.artifact(final_dir / name, media_type=media_type)
            trace.artifacts[-1]["file"] = os.path.relpath(final_dir / name, trace_dir).replace(os.sep, "/")
        trace.finish(
            status="accepted", job_dir=trace_dir, output_root=trace_root(output_root),
            guide=build_pack_guide(plan, scene_order),
            qa={"status": "passed", "checks": ["levels_valid", "unique_scene_ids", "difficulty_curve", "levels_built"]},
        )
    except OSError:
        pass
    return PackResult("accepted", fingerprint, final_dir, hashes, False, levels_out)


__all__ = [
    "MAX_LEVELS",
    "MIN_LEVELS",
    "PACK_SCHEMA",
    "LevelPack",
    "PackPlan",
    "PackResult",
    "build_level_pack",
    "build_pack_guide",
    "parse_level_pack",
    "plan_level_pack",
    "render_difficulty_curve",
]
