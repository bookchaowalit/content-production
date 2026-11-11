"""Level packs: pack contract, difficulty curve, per-level builds, and the pack manifest.

Unity is replaced by the fake stages of ``test_jobs``; each level goes through
the real ``compose_game_scene`` pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.game_production.cli import game_main
from book_production_platform.game_production.errors import GameJobError
from book_production_platform.game_production.level_pack import (
    build_level_pack,
    plan_level_pack,
    render_difficulty_curve,
)
from book_production_platform.production_trace import read_history
from tests.game_production.test_jobs import game_ir, install_fake_stages


def easy(scene_id: str = "level-one") -> dict[str, Any]:
    value = game_ir(scene_id=scene_id)
    value["objectives"] = [{"id": "amber-shard", "cell": [2, 1]}]
    value["walls"] = []
    value["exit"] = {"id": "portal", "cell": [3, 1]}
    return value


def harder(scene_id: str = "level-two") -> dict[str, Any]:
    value = game_ir(scene_id=scene_id)
    value["world"] = {"width": 16, "height": 12, "tile_size": 1.0}
    value["walls"] = [{"id": "maze", "cells": [[4, y] for y in range(10)] + [[9, y] for y in range(2, 12)]}]
    value["objectives"] = [{"id": f"shard-{index}", "cell": cell} for index, cell in enumerate([[2, 10], [7, 0], [7, 11], [14, 1], [12, 10]])]
    value["exit"] = {"id": "portal", "cell": [15, 11]}
    return value


def write_pack(root: Path, levels: list[dict[str, Any]], **overrides: Any) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    refs = []
    for index, level in enumerate(levels, start=1):
        name = f"level-{index}.json"
        (root / name).write_text(json.dumps(level), encoding="utf-8")
        refs.append({"file": name})
    pack = {"schema_version": "game-level-pack.v1", "pack_id": "starter-pack", "title": "Starter Pack", "levels": refs, **overrides}
    path = root / "pack.json"
    path.write_text(json.dumps(pack), encoding="utf-8")
    return path


def test_plan_reports_an_ascending_curve(tmp_path: Path) -> None:
    plan = plan_level_pack(write_pack(tmp_path, [easy(), harder()]))
    assert [level.game_ir.scene_id for level in plan.levels] == ["level-one", "level-two"]
    assert plan.curve[0] < plan.curve[1]
    summary = plan.summary()
    assert summary["difficulty_curve"] == list(plan.curve) and summary["warnings"] == []
    svg = render_difficulty_curve(plan).decode("utf-8")
    assert svg.count("<circle") == 2 and 'data-scene-id="level-two"' in svg
    assert render_difficulty_curve(plan) == render_difficulty_curve(plan_level_pack(tmp_path / "pack.json"))


def test_descending_difficulty_is_refused_unless_the_order_is_free(tmp_path: Path) -> None:
    with pytest.raises(GameJobError) as error:
        plan_level_pack(write_pack(tmp_path / "a", [harder("one"), easy("two")]))
    assert error.value.code == "difficulty_not_ascending"
    plan = plan_level_pack(write_pack(tmp_path / "b", [harder("one"), easy("two")], difficulty_order="free"))
    assert plan.warnings == ("difficulty_drops_at_level_2",)


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda root: write_pack(root, [easy()]), "invalid_level_pack"),  # one level is not a pack
        (lambda root: write_pack(root, [easy(f"l{index}") for index in range(13)]), "invalid_level_pack"),
        (lambda root: write_pack(root, [easy("same"), harder("same")]), "duplicate_scene_id"),
        (lambda root: write_pack(root, [easy(), {**harder(), "scene_id": "Bad Id"}]), "level_2_invalid_game_ir"),
        (lambda root: write_pack(root, [easy(), harder()], pack_id="Bad"), "invalid_level_pack"),
        (lambda root: write_pack(root, [easy(), harder()], code="x"), "invalid_level_pack"),
    ],
)
def test_bad_packs_are_refused_before_unity(tmp_path: Path, mutate: Any, code: str) -> None:
    path = mutate(tmp_path)
    with pytest.raises(GameJobError) as error:
        plan_level_pack(path)
    assert error.value.code == code


def test_level_files_must_sit_next_to_the_pack(tmp_path: Path) -> None:
    path = write_pack(tmp_path, [easy(), harder()])
    pack = json.loads(path.read_text(encoding="utf-8"))
    for name in ("../level-1.json", "sub/level-1.json", "level-1.txt"):
        path.write_text(json.dumps({**pack, "levels": [{"file": name}, {"file": "level-2.json"}]}), encoding="utf-8")
        with pytest.raises(GameJobError):
            plan_level_pack(path)
    (tmp_path / "link.json").symlink_to(tmp_path / "level-1.json")
    path.write_text(json.dumps({**pack, "levels": [{"file": "link.json"}, {"file": "level-2.json"}]}), encoding="utf-8")
    with pytest.raises(GameJobError):
        plan_level_pack(path)


def test_build_runs_each_level_job_then_publishes_and_replays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unity, calls = install_fake_stages(monkeypatch, tmp_path)
    pack = write_pack(tmp_path / "input", [easy(), harder()])
    output = tmp_path / "output"

    result = build_level_pack(pack, output, unity)

    assert result.status == "accepted" and not result.replay
    assert sorted(path.name for path in result.artifact_dir.iterdir()) == ["difficulty-curve.svg", "pack-manifest.json"]
    manifest = json.loads((result.artifact_dir / "pack-manifest.json").read_text(encoding="utf-8"))
    assert manifest["build_profile_scene_order"] == ["Assets/Scenes/level-one.unity", "Assets/Scenes/level-two.unity"]
    assert [level["index"] for level in manifest["levels"]] == [1, 2]
    for level in manifest["levels"]:
        assert (output / level["job_fingerprint"]).is_dir() or any(output.rglob(level["job_fingerprint"]))
    assert manifest["scene_flow"] == "manual"
    unity_runs = len(calls)
    assert unity_runs > 0

    history = read_history(output / ".trace")
    pack_entries = [entry for entry in history if entry["tool_id"] == "game-v1a-pack"]
    assert [entry["status"] for entry in pack_entries] == ["accepted"]
    manual = (output / ".trace" / pack_entries[0]["trace"]).with_name("MANUAL-STEPS.md").read_text(encoding="utf-8")
    assert "Assets/Scenes/level-one.unity → Assets/Scenes/level-two.unity" in manual

    again = build_level_pack(pack, output, unity)
    assert again.replay and again.fingerprint == result.fingerprint and len(calls) == unity_runs

    (result.artifact_dir / "difficulty-curve.svg").write_text("<svg/>", encoding="utf-8")
    with pytest.raises(GameJobError) as error:
        build_level_pack(pack, output, unity)
    assert error.value.code == "replay_integrity_error"


def test_failed_level_build_fails_the_pack_and_is_traced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unity, _ = install_fake_stages(monkeypatch, tmp_path, fail_stage="compile")
    output = tmp_path / "output"
    with pytest.raises(GameJobError):
        build_level_pack(write_pack(tmp_path / "input", [easy(), harder()]), output, unity)
    assert not (output / "packs").exists() or not [entry for entry in (output / "packs").iterdir() if not entry.name.startswith(".")]
    assert [entry["status"] for entry in read_history(output / ".trace") if entry["tool_id"] == "game-v1a-pack"] == ["failed"]


def test_cli_plan_only_needs_no_unity(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack = write_pack(tmp_path, [easy(), harder()])
    assert game_main(["pack", "--pack", str(pack), "--plan-only"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "planned" and len(report["levels"]) == 2
    assert game_main(["pack", "--pack", str(pack)]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_cli_arguments"


def test_a_level_edited_between_plan_and_build_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from book_production_platform.game_production import level_pack

    unity, _ = install_fake_stages(monkeypatch, tmp_path)
    pack = write_pack(tmp_path / "input", [easy(), harder()])
    real_compose = level_pack.compose_game_scene

    def compose_after_edit(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.name == "level-2.json":
            edited = harder()
            edited["player"]["speed"] = 6.0  # still valid, but not what the curve was checked on
            path.write_text(json.dumps(edited), encoding="utf-8")
        return real_compose(path, *args, **kwargs)

    monkeypatch.setattr(level_pack, "compose_game_scene", compose_after_edit)
    with pytest.raises(GameJobError) as error:
        build_level_pack(pack, tmp_path / "output", unity)
    assert error.value.code == "source_changed"


def test_concurrent_builds_of_one_pack_publish_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures import ThreadPoolExecutor

    unity, _ = install_fake_stages(monkeypatch, tmp_path)
    pack = write_pack(tmp_path / "input", [easy(), harder()])
    build_level_pack(pack, tmp_path / "warm", unity)  # level jobs exist, so threads race only on the pack
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: build_level_pack(pack, tmp_path / "warm", unity), range(3)))
    assert len({result.fingerprint for result in results}) == 1 and all(result.replay for result in results)
    output = tmp_path / "fresh"
    with ThreadPoolExecutor(max_workers=2) as pool:
        fresh = list(pool.map(lambda _: build_level_pack(pack, output, unity), range(2)))
    assert sorted(result.replay for result in fresh) == [False, True]


def test_pack_guide_copies_audio_with_the_scene(tmp_path: Path) -> None:
    from book_production_platform.game_production.level_pack import build_pack_guide

    second = harder()
    second.update({
        "schema_version": "game-ir.v2",
        "assets": [{"asset_id": "theme", "kind": "audio", "filename": "theme.ogg", "sha256": "a" * 64}],
        "audio": {"music": "theme"},
    })
    plan = plan_level_pack(write_pack(tmp_path, [easy(), second]))
    guide = build_pack_guide(plan, ["Assets/Scenes/level-one.unity", "Assets/Scenes/level-two.unity"])
    copy = next(step for step in guide.steps if "ด่านที่ 2" in step.action)
    assert "Assets/BookProduction/Audio/theme.ogg" in copy.value and ".meta" in copy.action


def test_pack_runs_share_the_game_trace_root_and_replays_are_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unity, _ = install_fake_stages(monkeypatch, tmp_path)
    pack = write_pack(tmp_path / "input", [easy(), harder()])
    output = tmp_path / "output"
    build_level_pack(pack, output, unity)
    build_level_pack(pack, output, unity)
    assert not (output / "history.jsonl").exists()
    history = read_history(output / ".trace")
    assert {entry["tool_id"] for entry in history} == {"game-v1a", "game-v1a-pack"}
    assert [entry["status"] for entry in history if entry["tool_id"] == "game-v1a-pack"] == ["accepted", "replayed"]


def test_audio_that_would_overwrite_another_level_is_refused(tmp_path: Path) -> None:
    def with_music(value: dict[str, Any], digest: str) -> dict[str, Any]:
        return {
            **value, "schema_version": "game-ir.v2",
            "assets": [{"asset_id": "music", "kind": "audio", "filename": "theme.wav", "sha256": digest}],
            "audio": {"music": "music"},
        }

    with pytest.raises(GameJobError) as error:
        plan_level_pack(write_pack(tmp_path / "a", [with_music(easy(), "a" * 64), with_music(harder(), "b" * 64)]))
    assert error.value.code == "pack_asset_conflict"
    plan_level_pack(write_pack(tmp_path / "b", [with_music(easy(), "a" * 64), with_music(harder(), "a" * 64)]))


def test_tilde_output_root_is_expanded_once_for_levels_and_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unity, _ = install_fake_stages(monkeypatch, tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    result = build_level_pack(write_pack(tmp_path / "input", [easy(), harder()]), Path("~/jobs"), unity)
    assert result.artifact_dir.parent == (tmp_path / "home" / "jobs" / "packs").resolve()
    assert not (tmp_path / "~").exists()
