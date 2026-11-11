from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("reverse_image_diff", ROOT / "scripts" / "reverse_image_diff.py")
assert _spec and _spec.loader
diff_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = diff_module
_spec.loader.exec_module(diff_module)


def _png(path: Path, size: tuple[int, int], box: tuple[int, int, int, int] | None = None) -> Path:
    image = Image.new("RGB", size, "white")
    if box:
        image.paste((0, 0, 0), box)
    image.save(path)
    return path


def test_identical_images_have_no_difference(tmp_path: Path) -> None:
    a = _png(tmp_path / "a.png", (10, 10))
    b = _png(tmp_path / "b.png", (10, 10))
    result = diff_module.compare(a, b, {"top": (0, 0, 10, 5)})
    assert result["whole_percent"] == 0.0 and result["regions"] == {"top": 0.0}


def test_region_difference_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = _png(tmp_path / "a.png", (10, 10))
    b = _png(tmp_path / "b.png", (10, 10), (0, 0, 10, 5))
    out = tmp_path / "diff.png"
    code = diff_module.main([str(a), str(b), "--region", "top=0,0,10,5", "--region", "bottom=0,5,10,10", "--diff", str(out), "--json"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["whole_percent"] == 50.0
    assert result["regions"] == {"bottom": 0.0, "top": 100.0}
    assert out.is_file()


def test_size_mismatch_exits_2(tmp_path: Path) -> None:
    a = _png(tmp_path / "a.png", (10, 10))
    b = _png(tmp_path / "b.png", (12, 10))
    assert diff_module.main([str(a), str(b)]) == 2


def test_alpha_difference_counts(tmp_path: Path) -> None:
    Image.new("RGBA", (4, 4), (0, 0, 0, 255)).save(tmp_path / "opaque.png")
    Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(tmp_path / "clear.png")
    result = diff_module.compare(tmp_path / "opaque.png", tmp_path / "clear.png", {})
    assert result["whole_percent"] == 100.0
