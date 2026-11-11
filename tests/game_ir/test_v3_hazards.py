"""GameIR v3 moving hazards: contract, timing model, safe-run proof, and downstream outputs."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from book_production_platform.game_ir import (
    GameInputError,
    canonical_game_ir_bytes,
    parse_game_ir,
    to_unity_worker_json,
    validate_game_ir,
)
from book_production_platform.game_ir.hazards import (
    MAX_HAZARD_PERIOD_TICKS,
    danger_table,
    hazard_cells_at,
    hazard_sequence,
    hazards_period_ticks,
    safe_run,
)
from book_production_platform.game_production.errors import GameJobError
from book_production_platform.game_production.level_map import (
    ascii_map,
    render_level_svg,
)
from book_production_platform.game_production.manual import build_unity_guide
from book_production_platform.game_production.metrics import level_metrics
from book_production_platform.game_production.qa import (
    verify_gameplay_trace,
    verify_worker_report,
)

# 9×5 room. A guard patrols the middle column x=4 (rows 0–4) that the player must cross.
#   y=4  . . . . ~ . . . .
#   y=3  . . . . ~ . . . E
#   y=2  P . o . ~ . o . .
#   y=1  . . . . ~ . . . .
#   y=0  . . . . ~ . . . .
BASE: dict[str, Any] = {
    "schema_version": "game-ir.v3",
    "scene_id": "guard-room",
    "template": "top-down-collect.v1",
    "world": {"width": 9, "height": 5, "tile_size": 1.0},
    "player": {"id": "runner", "spawn": [0, 2], "speed": 4.0},
    "walls": [],
    "objectives": [{"id": "coin-a", "cell": [2, 2]}, {"id": "coin-b", "cell": [6, 2]}],
    "exit": {"id": "door", "cell": [8, 3]},
    "hazards": [{"id": "guard", "path": [[4, 0], [4, 1], [4, 2], [4, 3], [4, 4]], "step_ticks": 2}],
}


def v3(**overrides: Any) -> dict[str, Any]:
    return {**json.loads(json.dumps(BASE)), **overrides}


def parse(value: dict[str, Any]) -> Any:
    return parse_game_ir(json.dumps(value).encode("utf-8"))


def test_v3_with_a_hazard_parses_validates_and_keeps_path_order() -> None:
    hazards = [
        {"id": "zz-late", "path": [[6, 4], [7, 4]], "step_ticks": 1},
        *BASE["hazards"],
    ]
    level = validate_game_ir(parse(v3(hazards=hazards)))
    assert [hazard.id for hazard in level.hazards] == ["guard", "zz-late"]  # sorted by id
    assert level.hazards[0].path == ((4, 0), (4, 1), (4, 2), (4, 3), (4, 4))  # motion order kept
    canonical = json.loads(canonical_game_ir_bytes(level))
    assert canonical["hazards"][0]["path"][0] == [4, 0]


@pytest.mark.parametrize("version", ["game-ir.v1", "game-ir.v2"])
def test_older_versions_refuse_hazards_and_never_serialize_the_field(version: str) -> None:
    with pytest.raises(GameInputError):
        parse(v3(schema_version=version))
    plain = {key: value for key, value in BASE.items() if key != "hazards"}
    level = parse({**plain, "schema_version": version})
    assert "hazards" not in json.loads(canonical_game_ir_bytes(level))
    assert "hazards" not in json.loads(to_unity_worker_json(level))


@pytest.mark.parametrize(
    "hazard",
    [
        {"id": "h", "path": [[4, 0]], "step_ticks": 1},  # one cell is not a patrol
        {"id": "h", "path": [[4, 0], [4, 2]], "step_ticks": 1},  # not neighbours
        {"id": "h", "path": [[4, 0], [5, 1]], "step_ticks": 1},  # diagonal
        {"id": "h", "path": [[4, 0], [4, 1], [4, 0]], "step_ticks": 1},  # repeated cell
        {"id": "h", "path": [[4, 0], [4, 1]], "step_ticks": 0},
        {"id": "h", "path": [[4, 0], [4, 1]], "step_ticks": 9},
        {"id": "h", "path": [[8, 4], [9, 4]], "step_ticks": 1},  # outside the world
        {"id": "h", "path": [[1, 2], [2, 2]], "step_ticks": 1},  # over an objective
        {"id": "h", "path": [[0, 1], [0, 2]], "step_ticks": 1},  # over the spawn
        {"id": "h", "path": [[8, 2], [8, 3]], "step_ticks": 1},  # over the exit
        {"id": "door", "path": [[4, 0], [4, 1]], "step_ticks": 1},  # id clash with the exit
    ],
)
def test_bad_hazards_are_refused(hazard: dict[str, Any]) -> None:
    with pytest.raises(GameInputError):
        validate_game_ir(parse(v3(hazards=[hazard])))


def test_hazard_over_a_wall_is_refused() -> None:
    value = v3(walls=[{"id": "block", "cells": [[4, 0]]}])
    with pytest.raises(GameInputError) as error:
        validate_game_ir(parse(value))
    assert error.value.code == "invalid_game_geometry"


def test_too_many_hazards_and_long_paths_fail_the_contract() -> None:
    many = [{"id": f"h{i}", "path": [[i % 9, 0], [i % 9, 1]], "step_ticks": 1} for i in range(9)]
    with pytest.raises(GameInputError):
        parse(v3(hazards=many))
    long = [{"id": "h", "path": [[x, 0] for x in range(17)], "step_ticks": 1}]
    with pytest.raises(GameInputError):
        parse(v3(world={"width": 20, "height": 5, "tile_size": 1.0}, hazards=long))


def test_ping_pong_timing() -> None:
    hazard = parse(v3()).hazards[0]
    assert hazard_sequence(hazard) == ((4, 0), (4, 1), (4, 2), (4, 3), (4, 4), (4, 3), (4, 2), (4, 1))
    assert hazards_period_ticks([hazard]) == 16
    assert hazard_cells_at(hazard, 0) == ((4, 0), (4, 1))
    assert hazard_cells_at(hazard, 1) == ((4, 0), (4, 1))  # two ticks per cell
    assert hazard_cells_at(hazard, 8) == ((4, 4), (4, 3))
    assert hazard_cells_at(hazard, 15) == ((4, 1), (4, 0))
    assert hazard_cells_at(hazard, 16) == hazard_cells_at(hazard, 0)
    table = danger_table([hazard], 16)
    assert table[0] == frozenset({(4, 0), (4, 1)})


def test_safe_run_never_shares_a_cell_with_a_hazard() -> None:
    level = validate_game_ir(parse(v3()))
    run = safe_run(level)
    assert run is not None
    table = danger_table(level.hazards, hazards_period_ticks(level.hazards))
    tick = 0
    previous = level.player.spawn
    for leg in run.legs:
        assert leg.cells[0] == previous
        for current, following in zip(leg.cells, leg.cells[1:], strict=False):
            danger = table[tick % len(table)]
            assert current not in danger and following not in danger
            assert abs(current[0] - following[0]) + abs(current[1] - following[1]) <= 1
            tick += 1
        previous = leg.cells[-1]
    assert tick == run.total_ticks
    assert previous == level.exit.cell
    assert sorted(run.order) == [0, 1]


def test_a_wall_of_hazards_that_never_opens_is_refused() -> None:
    # Two guards on the same column, out of phase, keep every crossing cell dangerous.
    walls = [{"id": "frame", "cells": [[4, 3], [4, 4]]}]
    hazards = [
        {"id": "g1", "path": [[4, 0], [4, 1], [4, 2]], "step_ticks": 1},
        {"id": "g2", "path": [[4, 2], [4, 1], [4, 0]], "step_ticks": 1},
    ]
    with pytest.raises(GameInputError) as error:
        validate_game_ir(parse(v3(walls=walls, hazards=hazards)))
    assert error.value.code == "unsafe_hazard_schedule"


def test_slow_guard_on_the_same_column_leaves_a_gap() -> None:
    walls = [{"id": "frame", "cells": [[4, 3], [4, 4]]}]
    hazards = [{"id": "g1", "path": [[4, 0], [4, 1], [4, 2]], "step_ticks": 3}]
    level = validate_game_ir(parse(v3(walls=walls, hazards=hazards)))
    assert safe_run(level) is not None


def test_caps_on_world_size_and_period() -> None:
    big = v3(world={"width": 33, "height": 5, "tile_size": 1.0})
    with pytest.raises(GameInputError) as error:
        validate_game_ir(parse(big))
    assert error.value.code == "resource_cap_exceeded"
    # Periods 2·(n−1)·step: 14·7 = 98 and 10·3 = 30 give lcm 1470 > MAX_HAZARD_PERIOD_TICKS.
    hazards = [
        {"id": "a", "path": [[x, 0] for x in range(8)], "step_ticks": 7},
        {"id": "b", "path": [[x, 4] for x in range(2, 8)], "step_ticks": 3},
    ]
    level = parse(v3(world={"width": 9, "height": 5, "tile_size": 1.0}, hazards=hazards))
    assert hazards_period_ticks(level.hazards) > MAX_HAZARD_PERIOD_TICKS
    with pytest.raises(GameInputError) as error:
        validate_game_ir(level)
    assert error.value.code == "resource_cap_exceeded"


def test_worker_dto_carries_hazard_timing_for_unity() -> None:
    dto = json.loads(to_unity_worker_json(parse(v3())))
    assert dto["hazards"] == [{
        "id": "guard",
        "path": [{"x": 4, "y": y} for y in range(5)],
        "step_ticks": 2,
        "seconds_per_cell": 0.5,  # 2 ticks × 1.0 tile / 4.0 speed
    }]


def test_metrics_map_and_guide_describe_hazards() -> None:
    level = validate_game_ir(parse(v3()))
    stats = level_metrics(level)
    assert stats.hazard_count == 1 and stats.hazard_period_ticks == 16
    assert stats.safe_route_ticks >= stats.route_steps
    assert stats.hazard_wait_ticks == stats.safe_route_ticks - stats.route_steps
    values = stats.to_dict()
    assert {"hazard_count", "safe_route_ticks", "hazard_wait_ticks", "safe_route_seconds"} <= set(values)
    plain = level_metrics(parse({**{k: v for k, v in BASE.items() if k != "hazards"}, "schema_version": "game-ir.v2"}))
    assert "hazard_count" not in plain.to_dict()

    assert ascii_map(level).count("~") == 5
    svg = render_level_svg(level).decode("utf-8")
    assert 'data-hazard="guard"' in svg and 'data-step-ticks="2"' in svg

    guide = build_unity_guide(level, stats)
    values_text = " | ".join(step.value for step in guide.steps)
    assert "Seconds Per Cell = 0.5" in values_text and "hazard.png" in values_text
    assert any(layer.name == "Hazard-guard" for layer in guide.layers)
    assert any("Hazard 1" in check for check in guide.checks)


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_gameplay_trace_needs_one_hit_per_hazard(tmp_path: Path) -> None:
    level = validate_game_ir(parse(v3()))
    good = ["exit_blocked:door", "player_moved", "hazard_hit:guard", "objective_collected:coin-a", "objective_collected:coin-b", "game_won:door"]
    events, _ = verify_gameplay_trace(_write(tmp_path / "ok.json", {"events": good}), level)
    assert Counter(events)["hazard_hit:guard"] == 1
    for bad in (
        [item for item in good if not item.startswith("hazard_hit")],
        [*good[:3], "hazard_hit:guard", *good[3:]],
        [*good[:2], "hazard_hit:ghost", *good[3:]],
    ):
        with pytest.raises(GameJobError):
            verify_gameplay_trace(_write(tmp_path / "bad.json", {"events": bad}), level)


def test_worker_report_must_count_the_hazards(tmp_path: Path) -> None:
    level = validate_game_ir(parse(v3()))
    report = {
        "status": "verified", "unity_version": "6000.6.2f1", "scene_path": "Assets/Scenes/guard-room.unity",
        "player_count": 1, "wall_collider_count": 0, "objective_count": 2, "exit_count": 1,
        "unique_entity_ids": True, "external_resource_count": 0, "disallowed_component_count": 0,
        "trusted_template_no_network_references": True, "music_source_count": 0,
        "collect_sound_configured": False, "hazard_count": 1,
    }
    folder = tmp_path / ".book-production"
    folder.mkdir()
    _write(folder / "worker-report.json", report)
    assert verify_worker_report(tmp_path, level, "6000.6.2f1")["hazard_count"] == 1
    _write(folder / "worker-report.json", {**report, "hazard_count": 0})
    with pytest.raises(GameJobError):
        verify_worker_report(tmp_path, level, "6000.6.2f1")


def test_v3_schema_documents_hazards() -> None:
    schema = json.loads(
        (Path(__file__).parents[2] / "src" / "book_production_platform" / "game_ir" / "game-ir.v3.schema.json").read_text(encoding="utf-8")
    )
    assert schema["properties"]["schema_version"]["const"] == "game-ir.v3"
    assert schema["properties"]["hazards"]["maxItems"] == 8
    hazard = schema["$defs"]["HazardIR"]
    assert hazard["properties"]["path"]["maxItems"] == 16
    assert hazard["properties"]["step_ticks"]["maximum"] == 8
    assert schema["x-hazardLimits"]["max_period_ticks"] == MAX_HAZARD_PERIOD_TICKS


def test_hazard_level_is_refused_until_the_template_has_hazard_slots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from book_production_platform.game_production import jobs

    scene = tmp_path / "template.unity"
    scene.write_bytes(b"--- !u!1 &1\\nGameObject: m_Name: ExitSlot\\n")
    assert jobs.template_has_hazard_slots(scene) is False
    scene.write_bytes(b"m_Name: HazardSlot-000\\n  entityId: hazard-slot-000\\n")
    assert jobs.template_has_hazard_slots(scene) is True
    assert jobs.template_has_hazard_slots(tmp_path / "missing.unity") is False

    game = tmp_path / "guard-room.json"
    game.write_text(json.dumps(v3()), encoding="utf-8")
    monkeypatch.setattr(jobs, "template_has_hazard_slots", lambda: False)
    unity = tmp_path / "Unity.exe"
    unity.write_bytes(b"x")
    with pytest.raises(GameJobError) as error:
        jobs.compose_game_scene(game, tmp_path / "out", unity)
    assert error.value.code == "unsupported_game_feature"


def test_v1_brief_proposals_never_carry_a_hazards_key() -> None:
    from book_production_platform.game_production.provider import ground_layout
    from tests.game_production.test_brief_adapter import BRIEF, layout

    result = ground_layout(layout(), BRIEF)
    assert result.status == "complete" and result.game_ir is not None
    assert result.game_ir["schema_version"] == "game-ir.v1"
    v1_schema = json.loads(
        (Path(__file__).parents[2] / "src" / "book_production_platform" / "game_ir" / "game-ir.v1.schema.json").read_text(encoding="utf-8")
    )
    assert set(result.game_ir) <= set(v1_schema["properties"])
    assert "hazards" not in result.game_ir
