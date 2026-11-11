"""Cell-by-cell comparison of the engine bulk CSV and the CSV that Calc saved."""

import csv
import hashlib
import json
import sys
from pathlib import Path

engine_path, calc_path = Path(sys.argv[1]), Path(sys.argv[2])
engine = list(csv.reader(engine_path.read_text(encoding="utf-8").splitlines(keepends=True)))
calc = list(csv.reader(calc_path.read_text(encoding="utf-8").splitlines(keepends=True)))
width = max(len(row) for row in engine + calc)
cells = differ = 0
diffs = []
for r in range(max(len(engine), len(calc))):
    for c in range(width):
        a = engine[r][c] if r < len(engine) and c < len(engine[r]) else ""
        b = calc[r][c] if r < len(calc) and c < len(calc[r]) else ""
        cells += 1
        if a != b:
            differ += 1
            diffs.append({"cell": f"{chr(65 + c)}{r + 1}", "engine": a, "calc": b})
print(json.dumps({
    "engine_rows": len(engine), "calc_rows": len(calc), "columns": width, "cells_compared": cells, "cells_differing": differ,
    "engine_sha256": hashlib.sha256(engine_path.read_bytes()).hexdigest(), "calc_sha256": hashlib.sha256(calc_path.read_bytes()).hexdigest(),
    "byte_identical": engine_path.read_bytes() == calc_path.read_bytes(), "diffs": diffs[:20],
}, ensure_ascii=False, indent=1))
