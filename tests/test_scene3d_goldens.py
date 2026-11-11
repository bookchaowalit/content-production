"""Golden renders of every Scene3D preset on the pinned Blender 5.2.2 LTS.

Each case runs the full ``compose_scene`` job (build, fresh reopen, EEVEE
render, QA, publish) on the synthetic bottle from the benchmark, then compares
a 128×128 thumbnail with ``tests/fixtures/scene3d/goldens/<case>.png``. EEVEE is
not bit-identical across GPUs and drivers, so the check is not a hash: at most
1 % of thumbnail pixels may differ by 16 levels or more in any channel. A
re-render on the same machine differs by 0 %; swapping gloss for the source
material changes 2.4 % and dropping the shadow catcher 5.8 %, so a lost
highlight or shadow fails while driver noise passes.

Skipped without Blender (set ``BLENDER_BIN``). Refresh the goldens after an
intended look change with ``SCENE3D_UPDATE_GOLDENS=1`` and review the images.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from book_production_platform.scene3d.benchmark import _synthetic_glb
from book_production_platform.scene3d.runtime import compose_scene
from tests.test_blender_scene import blender_executable

GOLDENS = Path(__file__).parent / "fixtures" / "scene3d" / "goldens"
THUMBNAIL = (128, 128)
PIXEL_TOLERANCE = 16  # levels (0–255) in the worst channel
MAX_CHANGED_PERCENT = 1.0
# (case, lighting, background, material) — every P1 preset appears at least once.
CASES = [
    ("baseline", "softbox-three-point.v1", "studio-neutral.v1", "preserve-source.v1"),
    ("high-key-white-matte", "high-key-white.v1", "studio-white.v1", "matte.v1"),
    ("dramatic-rim-transparent", "dramatic-rim.v1", "transparent.v1", "preserve-source.v1"),
    ("studio-hdri-gloss", "studio-hdri.v1", "studio-neutral.v1", "gloss.v1"),
    ("shadow-catcher-brushed-metal", "softbox-three-point.v1", "transparent-shadow.v1", "brushed-metal.v1"),
]


def _thumbnail(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGBA").resize(THUMBNAIL, Image.Resampling.LANCZOS)


def changed_percent(actual: Image.Image, expected: Image.Image) -> float:
    """Share of pixels whose worst channel (RGBA) differs by ``PIXEL_TOLERANCE`` or more."""

    bands = ImageChops.difference(actual, expected).split()
    worst = bands[0]
    for band in bands[1:]:
        worst = ImageChops.lighter(worst, band)
    histogram = worst.histogram()
    return 100.0 * sum(histogram[PIXEL_TOLERANCE:]) / (actual.width * actual.height)


@pytest.mark.parametrize(("case", "lighting", "background", "material"), CASES, ids=[case[0] for case in CASES])
def test_preset_render_matches_its_golden(tmp_path: Path, case: str, lighting: str, background: str, material: str) -> None:
    blender = blender_executable()
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "product.glb").write_bytes(_synthetic_glb("bottle", [0.20, 0.58, 0.36], "golden"))
    scene = {
        "schema_version": "scene-ir.v1", "asset": {"path": "product.glb"}, "recipe": "product-hero.studio.v1",
        "composition": "three-quarter-fit.v1", "lighting": lighting, "background": background,
        "material_preset": material, "render_profile": "square-1024.v1",
    }
    (input_root / "scene.json").write_text(json.dumps(scene), encoding="utf-8")

    result = compose_scene(input_root / "scene.json", input_root, tmp_path / "jobs", blender)

    render = result.artifact_dir / "render.png"
    with Image.open(render) as image:
        assert image.size == (1024, 1024)
        assert image.mode == ("RGBA" if background.startswith("transparent") else "RGB")
        if background == "transparent-shadow.v1":
            alpha = image.getchannel("A")
            # Soft shadow: partly transparent ground pixels, fully clear corners.
            partial = sum(count for value, count in enumerate(alpha.histogram()) if 0 < value < 255)
            assert partial > 5000 and alpha.getpixel((5, 5)) == 0
    actual = _thumbnail(render)
    golden = GOLDENS / f"{case}.png"
    if os.environ.get("SCENE3D_UPDATE_GOLDENS") == "1":
        GOLDENS.mkdir(parents=True, exist_ok=True)
        actual.save(golden, format="PNG", optimize=True)
    assert golden.is_file(), f"missing golden {golden.name}; run with SCENE3D_UPDATE_GOLDENS=1"
    with Image.open(golden) as expected:
        changed = changed_percent(actual, expected.convert("RGBA"))
    assert changed <= MAX_CHANGED_PERCENT, f"{case}: {changed:.2f}% of pixels differ from the golden"


def test_goldens_differ_from_each_other_beyond_the_tolerance() -> None:
    images = {}
    for case, *_ in CASES:
        with Image.open(GOLDENS / f"{case}.png") as image:
            assert image.size == THUMBNAIL
            images[case] = image.convert("RGBA")
    names = sorted(images)
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            assert changed_percent(images[first], images[second]) > 5 * MAX_CHANGED_PERCENT, (first, second)


def test_golden_cases_cover_every_p1_preset() -> None:
    used = {value for case in CASES for value in case[1:]}
    assert {"high-key-white.v1", "dramatic-rim.v1", "studio-hdri.v1"} <= used
    assert {"studio-white.v1", "transparent.v1", "transparent-shadow.v1"} <= used
    assert {"matte.v1", "gloss.v1", "brushed-metal.v1"} <= used
