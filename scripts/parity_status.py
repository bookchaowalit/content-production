"""Parity / reverse-guide status for every tool in catalog/production-tools.yaml.

Usage: uv run python scripts/parity_status.py [--json]

Per tool it reports what the 2026-11-12 parity goal checks mechanically:
parity matrix present, ``Flow`` column present, open P1 rows, reverse guides
(``docs/reverse/<tool>/*.md``), and a passing row in ``VERIFY.md``. Owner
acceptance of an open P1 is recorded in the matrix as ``P1 (owner-accepted)``
and is not counted as open.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog" / "production-tools.yaml"
PARITY_DIR = ROOT / "docs" / "parity"
REVERSE_DIR = ROOT / "docs" / "reverse"

PARITY_DOC = {
    "documents-v1a": "documents.md",
    "data-viz-v1a": "data-viz.md",
    "marketing-v1a": "marketing.md",
    "web-v1a": "web.md",
    "workflow-v1a": "workflow.md",
    "video-studio": "video.md",
    "audio-batch-v1a": "audio-batch.md",
    "scene3d-v1a": "scene3d.md",
    "graphics-v1a": "graphics.md",
    "spreadsheet-v1a": "spreadsheet.md",
    "data-pipeline-v1a": "data-pipeline.md",
    "cad-v1a": "cad.md",
    "bim-v1a": "bim.md",
    "audio-master-v1a": "audio.md",
    "game-v1a": "game.md",
}
_OPEN_P1 = re.compile(r"\|\s*\**(missing|partial)[^|]*\|\s*P1\s*\|")
_PASS = re.compile(r"\*\*pass", re.IGNORECASE)


@dataclass(frozen=True)
class ToolStatus:
    tool_id: str
    parity_doc: str
    parity: bool
    flow_column: bool
    open_p1: int
    reverse_guides: tuple[str, ...]
    verified: bool

    @property
    def done(self) -> bool:
        return self.parity and self.flow_column and self.open_p1 == 0 and bool(self.reverse_guides) and self.verified


def catalog_tool_ids(path: Path = CATALOG) -> list[str]:
    return re.findall(r"^  - id:\s*([a-z0-9-]+)\s*$", path.read_text(encoding="utf-8"), flags=re.MULTILINE)


def tool_status(tool_id: str, root: Path = ROOT) -> ToolStatus:
    doc_name = PARITY_DOC.get(tool_id, f"{tool_id}.md")
    doc = root / "docs" / "parity" / doc_name
    text = doc.read_text(encoding="utf-8") if doc.is_file() else ""
    reverse = root / "docs" / "reverse" / tool_id
    guides = tuple(sorted(path.name for path in reverse.glob("*.md") if path.name != "VERIFY.md")) if reverse.is_dir() else ()
    verify = reverse / "VERIFY.md"
    return ToolStatus(
        tool_id=tool_id,
        parity_doc=f"docs/parity/{doc_name}",
        parity=bool(text),
        flow_column=bool(re.search(r"^\| Capability \|.*\| Flow \|\s*$", text, flags=re.MULTILINE)),
        open_p1=len(_OPEN_P1.findall(text)),
        reverse_guides=guides,
        verified=verify.is_file() and bool(_PASS.search(verify.read_text(encoding="utf-8"))),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parity / reverse-guide status per catalog tool.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    statuses = [tool_status(tool_id) for tool_id in catalog_tool_ids()]
    if args.json:
        print(json.dumps([{**asdict(status), "done": status.done} for status in statuses], indent=2))
        return 0
    print("| tool | parity | Flow | open P1 | reverse guides | verified | done |")
    print("|---|---|---|---|---|---|---|")
    for status in statuses:
        mark = lambda value: "yes" if value else "no"
        print(
            f"| {status.tool_id} | {mark(status.parity)} | {mark(status.flow_column)} | {status.open_p1} | "
            f"{', '.join(status.reverse_guides) or '—'} | {mark(status.verified)} | {mark(status.done)} |"
        )
    print(f"\n{sum(status.done for status in statuses)}/{len(statuses)} tools done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
