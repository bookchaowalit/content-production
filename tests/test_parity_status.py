from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("parity_status", ROOT / "scripts" / "parity_status.py")
assert _spec and _spec.loader
status_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = status_module
_spec.loader.exec_module(status_module)


def test_every_catalog_tool_has_a_parity_doc_mapping() -> None:
    ids = status_module.catalog_tool_ids()
    assert len(ids) == 15
    assert set(ids) == set(status_module.PARITY_DOC)


def test_status_reads_flow_p1_guides_and_verify(tmp_path: Path) -> None:
    parity = tmp_path / "docs" / "parity"
    parity.mkdir(parents=True)
    (parity / "graphics.md").write_text(
        "| Capability | X | Engine status | Priority | Notes | Flow |\n|---|---|---|---|---|---|\n"
        "| a | b | missing | P1 | n | LOGIC |\n| c | d | partial | P1 (owner-accepted) | n | LOGIC |\n",
        encoding="utf-8",
    )
    status = status_module.tool_status("graphics-v1a", tmp_path)
    assert status.parity and status.flow_column and status.open_p1 == 1 and not status.done
    reverse = tmp_path / "docs" / "reverse" / "graphics-v1a"
    reverse.mkdir(parents=True)
    (reverse / "figma.md").write_text("guide", encoding="utf-8")
    (reverse / "VERIFY.md").write_text("| r | **Pass** |", encoding="utf-8")
    (parity / "graphics.md").write_text(
        "| Capability | X | Engine status | Priority | Notes | Flow |\n|---|---|---|---|---|---|\n| a | b | have | — | n | LOGIC |\n",
        encoding="utf-8",
    )
    status = status_module.tool_status("graphics-v1a", tmp_path)
    assert status.reverse_guides == ("figma.md",) and status.verified and status.done


def test_graphics_meets_the_goal_checks() -> None:
    assert status_module.tool_status("graphics-v1a").done
