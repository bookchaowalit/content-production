"""Scene3D P1 presets without Blender: HDRI asset pin, material overrides, shadow catcher, guide, trace.

The Blender-side node code runs against small fakes of the ``bpy`` objects it
touches; the real look is covered by ``test_scene3d_goldens`` when Blender
5.2.2 LTS is available.
"""

from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.production_trace import read_history
from book_production_platform.scene3d import runtime, scene_builder
from book_production_platform.scene3d.errors import SceneInputError
from book_production_platform.scene3d.manual import build_blender_guide
from book_production_platform.scene3d.models import parse_scene_ir
from book_production_platform.scene3d.provider import PRESET_REGISTRY
from book_production_platform.scene3d.scene_builder import (
    BACKGROUND_PRESETS,
    ENGINE_ONLY_PRESETS,
    HDRI_PRESETS,
    LIGHTING_PRESETS,
    MATERIAL_ID,
    MATERIAL_PRESETS,
    _apply_material_preset,
    _load_validated_scene_ir,
    hdri_path,
)
from tests.test_scene_ir import valid_scene
from tests.test_scene_jobs import create_inputs, install_fake_workers

ROOT = Path(__file__).resolve().parents[1]
NEW_PRESETS = [
    ("lighting", "studio-hdri.v1"),
    ("background", "transparent-shadow.v1"),
    ("material_preset", "matte.v1"),
    ("material_preset", "gloss.v1"),
    ("material_preset", "brushed-metal.v1"),
]


@pytest.mark.parametrize(("field", "value"), NEW_PRESETS)
def test_p1_presets_pass_scene_ir_and_the_blender_allowlist(tmp_path: Path, field: str, value: str) -> None:
    scene = {**valid_scene(), field: value}
    assert getattr(parse_scene_ir(scene), field) == value
    path = tmp_path / "scene.json"
    path.write_text(json.dumps(scene), encoding="utf-8")
    assert _load_validated_scene_ir(path)[field] == value


@pytest.mark.parametrize(("field", "value"), NEW_PRESETS)
def test_p1_presets_are_engine_only_for_the_brief_adapter(field: str, value: str) -> None:
    assert value in ENGINE_ONLY_PRESETS[field]
    assert value not in PRESET_REGISTRY[field]
    assert PRESET_REGISTRY["material_preset"] == (MATERIAL_ID,)


def test_bundled_hdri_matches_its_pin_and_is_packaged() -> None:
    preset = HDRI_PRESETS["studio-hdri.v1"]
    path = hdri_path("studio-hdri.v1")
    assert path is not None and path.name == preset["file"]
    assert path.stat().st_size <= scene_builder.MAX_HDRI_BYTES
    assert preset["license"] == "CC0-1.0"
    assert LIGHTING_PRESETS["studio-hdri.v1"] == ()
    assert hdri_path("softbox-three-point.v1") is None
    package_data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]
    assert "scene3d/assets/*.exr" in package_data["book_production_platform"]
    readme = (path.parent / "README.md").read_text(encoding="utf-8")
    assert preset["sha256"] in readme and preset["source"] in readme


def test_changed_or_missing_hdri_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = hdri_path("studio-hdri.v1")
    assert source is not None
    copy = tmp_path / source.name
    shutil.copyfile(source, copy)
    with copy.open("r+b") as handle:
        handle.seek(-1, 2)
        last = handle.read(1)
        handle.seek(-1, 2)
        handle.write(bytes([last[0] ^ 0xFF]))
    monkeypatch.setattr(scene_builder, "ASSETS_DIR", tmp_path)
    with pytest.raises(ValueError, match="hdri_asset_mismatch"):
        hdri_path("studio-hdri.v1")
    copy.unlink()
    with pytest.raises(ValueError, match="hdri_asset_unavailable"):
        hdri_path("studio-hdri.v1")


def test_shadow_catcher_background_is_transparent_with_a_fading_ground() -> None:
    preset = BACKGROUND_PRESETS["transparent-shadow.v1"]
    catcher = preset["ground"]["shadow_catcher"]
    assert preset["film_transparent"] is True
    assert 0 < catcher["fade_from"] < catcher["fade_to"] <= preset["ground"]["size"] / 2
    assert catcher["ao_distance"] > 0 and catcher["strength"] > 0
    # The plain cut-out keeps its old behaviour (no ground at all).
    assert BACKGROUND_PRESETS["transparent.v1"]["ground"] is None


# --- material overrides against small bpy fakes ------------------------------------------


class _Socket:
    def __init__(self, value: float = 0.5) -> None:
        self.default_value = value
        self.links: list[object] = []


class _Node:
    def __init__(self, kind: str) -> None:
        self.type = kind
        self.inputs = {name: _Socket() for name in ("Base Color", "Roughness", "Metallic", "Coat Weight", "Coat Roughness", "Anisotropic")}


class _Links:
    def __init__(self, owner: _Tree) -> None:
        self.owner = owner
        self.removed: list[object] = []

    def remove(self, link: object) -> None:
        self.removed.append(link)
        for node in self.owner.nodes:
            for socket in node.inputs.values():
                if link in socket.links:
                    socket.links.remove(link)


class _Tree:
    def __init__(self, nodes: list[_Node]) -> None:
        self.nodes = nodes
        self.links = _Links(self)


class _Material:
    def __init__(self, name: str, nodes: list[_Node] | None = None) -> None:
        self.name = name
        self.use_nodes = False
        self.node_tree = _Tree(nodes if nodes is not None else [_Node("BSDF_PRINCIPLED")])


class _Slot:
    def __init__(self, material: _Material | None) -> None:
        self.material = material


class _Mesh:
    def __init__(self, slots: list[_Slot]) -> None:
        self.material_slots = slots
        self.data = self
        self.materials = self

    def append(self, material: _Material) -> None:
        self.material_slots.append(_Slot(material))


class _Bpy:
    def __init__(self) -> None:
        self.data = self
        self.materials = self
        self.created: list[_Material] = []

    def new(self, name: str) -> _Material:
        material = _Material(name)
        self.created.append(material)
        return material


def test_material_preset_overrides_finish_and_cuts_texture_links() -> None:
    shared = _Material("Shared")
    roughness_map = object()
    shared.node_tree.nodes[0].inputs["Roughness"].links.append(roughness_map)
    base_texture = object()
    shared.node_tree.nodes[0].inputs["Base Color"].links.append(base_texture)
    bpy = _Bpy()
    meshes = [_Mesh([_Slot(shared)]), _Mesh([_Slot(shared), _Slot(None)]), _Mesh([])]

    changed = _apply_material_preset(bpy, meshes, "brushed-metal.v1")

    assert changed == 3  # the shared material once, plus a default for the empty slot and the bare mesh
    principled = shared.node_tree.nodes[0].inputs
    expected = MATERIAL_PRESETS["brushed-metal.v1"]
    assert expected is not None
    assert {name: principled[name].default_value for name in expected} == expected
    assert principled["Roughness"].links == [] and shared.node_tree.links.removed == [roughness_map]
    assert principled["Base Color"].links == [base_texture]  # colour and its texture stay
    assert [material.name for material in bpy.created] == ["Product Default", "Product Default"]


def test_preserve_source_touches_nothing_and_unsupported_materials_are_refused() -> None:
    material = _Material("Kept")
    assert _apply_material_preset(_Bpy(), [_Mesh([_Slot(material)])], MATERIAL_ID) == 0
    assert material.node_tree.nodes[0].inputs["Roughness"].default_value == 0.5
    unlit = _Material("Unlit", [_Node("EMISSION")])
    with pytest.raises(ValueError, match="material_preset_unsupported"):
        _apply_material_preset(_Bpy(), [_Mesh([_Slot(unlit)])], "matte.v1")


# --- guide and trace ---------------------------------------------------------------------


def test_guide_covers_hdri_shadow_catcher_and_material_values() -> None:
    scene = {**valid_scene(), "lighting": "studio-hdri.v1", "background": "transparent-shadow.v1", "material_preset": "gloss.v1"}
    guide = build_blender_guide(parse_scene_ir(scene))
    values = " | ".join(step.value for step in guide.steps)
    actions = " | ".join(step.action for step in guide.steps)
    catcher = BACKGROUND_PRESETS["transparent-shadow.v1"]["ground"]["shadow_catcher"]

    assert HDRI_PRESETS["studio-hdri.v1"]["file"] in values and "Is Camera Ray" in values
    assert f"AO Distance = {catcher['ao_distance']:g}" in values and "Blended" in values
    assert "PNG, RGBA, 8, 15%" in values
    gloss = MATERIAL_PRESETS["gloss.v1"]
    assert gloss is not None
    for name, value in gloss.items():
        assert any(step.action == f"ตั้ง {name}" and step.value == f"{value:g}" for step in guide.steps)
    assert "ไม่ต้องเพิ่มไฟ" in actions
    assert not [layer for layer in guide.layers if layer.kind.startswith("Light")]
    assert any(layer.kind == "Mesh Plane (shadow catcher)" for layer in guide.layers)


def test_build_trace_records_hdri_material_and_shadow_catcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene_path, input_root, output_root, blender_bin = create_inputs(tmp_path)
    scene = {**json.loads(scene_path.read_text(encoding="utf-8")), "lighting": "studio-hdri.v1", "background": "transparent-shadow.v1", "material_preset": "matte.v1"}
    scene_path.write_text(json.dumps(scene), encoding="utf-8")
    install_fake_workers(monkeypatch)

    runtime.compose_scene(scene_path, input_root, output_root, blender_bin)

    entry = read_history(output_root)[-1]
    trace: dict[str, Any] = json.loads((output_root / entry["trace"]).read_text(encoding="utf-8"))
    build = next(step for step in trace["steps"] if step["operation"] == "blender_build_scene")["params"]
    assert build["hdri"]["sha256"] == HDRI_PRESETS["studio-hdri.v1"]["sha256"]
    assert build["hdri"]["license"] == "CC0-1.0"
    assert build["material_overrides"] == MATERIAL_PRESETS["matte.v1"]
    assert build["shadow_catcher"] == BACKGROUND_PRESETS["transparent-shadow.v1"]["ground"]["shadow_catcher"]
    assert build["lights"] == []


def test_compose_refuses_a_changed_hdri_before_blender_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene_path, input_root, output_root, blender_bin = create_inputs(tmp_path)
    scene = {**json.loads(scene_path.read_text(encoding="utf-8")), "lighting": "studio-hdri.v1"}
    scene_path.write_text(json.dumps(scene), encoding="utf-8")
    calls = install_fake_workers(monkeypatch)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / HDRI_PRESETS["studio-hdri.v1"]["file"]).write_bytes(b"not the pinned hdri")
    monkeypatch.setattr(scene_builder, "ASSETS_DIR", assets)

    with pytest.raises(SceneInputError) as error:
        runtime.compose_scene(scene_path, input_root, output_root, blender_bin)

    assert error.value.code == "hdri_asset_mismatch"
    assert calls == []
    assert [entry["status"] for entry in read_history(output_root)] == ["failed"]
