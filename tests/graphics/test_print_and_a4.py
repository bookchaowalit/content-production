"""A4 print size and the press PDF (bleed, crop marks, CMYK), without a browser."""

from __future__ import annotations

import io
import json
import os
import re
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageCms

from book_production_platform.graphics_ir import GraphicIR, TextNode, iter_nodes
from book_production_platform.graphics_production import export as export_module
from book_production_platform.graphics_production import jobs, print_pdf
from book_production_platform.graphics_production.cli import main
from book_production_platform.graphics_production.composer import compose
from book_production_platform.graphics_production.export import (
    ExportError,
    export_batch,
)
from book_production_platform.graphics_production.jobs import expand_variants, run_batch
from book_production_platform.graphics_production.print_pdf import (
    CmykProfile,
    PrintError,
    PrintProfile,
    add_bleed,
    check_profile,
    press_pdf,
)
from book_production_platform.graphics_production.provider import VARIANT_IDS
from book_production_platform.graphics_production.templates import compile_template
from book_production_platform.graphics_production.themes import (
    PRINT_VARIANTS,
    SOCIAL_VARIANTS,
    VARIANT_SIZES,
    Context,
    load_themes,
    parse_theme,
)
from tests.graphics.test_jobs import item
from tests.graphics.test_themes import request

# --- A4 ------------------------------------------------------------------------------------


def test_a4_exports_to_exactly_a4_at_300_dpi_with_scale_2() -> None:
    width, height = VARIANT_SIZES["a4"]
    assert (width * 2, height * 2) == (2480, 3508)
    assert round(width * 2 / 300 * 25.4) == 210 and round(height * 2 / 300 * 25.4) == 297


def test_themes_laid_out_in_canvas_units_come_in_a4_and_fixed_pixel_themes_do_not() -> None:
    for theme in load_themes().values():
        expected = "portrait" in theme.variants and theme.ir_version != "graphic-ir.v1"
        assert ("a4" in theme.variants) == expected, theme.theme_id
    for theme_id in ("quote_card", "product_card", "announcement"):  # fixed 1080 px coordinates
        assert "a4" not in load_themes()[theme_id].variants


def _theme(vars_: dict[str, object], items: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "schema_version": "graphic-theme.v1", "theme_id": "probe", "variants": ["square", "portrait"],
        "slots": {"headline": {"required": True, "labels": ["headline"]}}, "evidence": ["probe"],
        "vars": vars_,
        "layers": [{"id": "Text", "items": items or [{"motif": "text", "id": "h", "slot": "headline", "x": "W * 0.1",
                                                        "y": "H * 0.1", "width": "W * 0.8", "font_size": 40}]}],
    }


def test_a4_uses_the_portrait_entry_of_a_size_table_and_its_own_entry_when_given() -> None:
    theme = parse_theme(_theme({"P": {"by_variant": {"portrait": 7, "default": 1}},
                                "Q": {"by_variant": {"portrait": 7, "a4": 9, "default": 1}}}))
    assert "a4" in theme.variants
    a4 = SimpleNamespace(request=SimpleNamespace(variant="a4"))
    assert Context._choose(a4, theme.variables["P"]) == 7  # type: ignore[arg-type]
    assert Context._choose(a4, theme.variables["Q"]) == 9  # type: ignore[arg-type]
    square = SimpleNamespace(request=SimpleNamespace(variant="square"))
    assert Context._choose(square, theme.variables["P"]) == 1  # type: ignore[arg-type]


def test_theme_without_a_portrait_layout_does_not_get_a4() -> None:
    raw = _theme({})
    raw["variants"] = ["square", "landscape"]
    assert "a4" not in parse_theme(raw).variants


def _text_anchors(ir: GraphicIR) -> dict[str, float]:
    """Each text node's anchor (left edge, centre, or right edge by alignment) as a fraction of the canvas width.

    The anchor, not the width, is compared: display text sized from the canvas height is wider on the
    taller A4 page by design, but it must stay anchored where the portrait layout puts it.
    """

    width = ir.canvas.width
    anchors = {}
    for node in iter_nodes(list(ir.nodes)):
        if isinstance(node, TextNode):
            offset = {"left": 0.0, "center": 0.5, "right": 1.0}[node.align]
            anchors[node.node_id] = (node.x + node.width * offset) / width
    return anchors


@pytest.mark.parametrize("theme_id", sorted(t for t, theme in load_themes().items() if "a4" in theme.variants))
def test_a4_layout_is_the_portrait_layout_scaled(theme_id: str) -> None:
    """Same items in the same order; every text box sits at the same fraction of the canvas width."""

    portrait = compile_template(request(theme_id, "portrait"))
    a4 = compile_template(request(theme_id, "a4"))
    assert a4.canvas.width == 1240 and a4.canvas.height == 1754
    assert [node.node_id for node in portrait.nodes] == [node.node_id for node in a4.nodes]
    portrait_anchors, a4_anchors = _text_anchors(portrait), _text_anchors(a4)
    assert portrait_anchors.keys() == a4_anchors.keys()
    for node_id, anchor in portrait_anchors.items():
        assert a4_anchors[node_id] == pytest.approx(anchor, abs=0.01), node_id


def test_make_all_means_the_screen_sizes_and_a4_is_asked_for_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                      capsys: pytest.CaptureFixture[str]) -> None:
    captured: list[list[str]] = []

    def fake_run_batch(items: list[dict[str, object]], *args: object, **kwargs: object) -> object:
        captured.append([str(entry["variant"]) for entry in items])
        raise SystemExit(0)

    monkeypatch.setattr("book_production_platform.graphics_production.cli.run_batch", fake_run_batch)
    for variants in (["all"], ["a4"], ["all", "a4", "square"]):
        args = ["make", "--theme", "bold_editorial", "--set", "headline=Hi", "--output", str(tmp_path),
                "--idempotency-key", "k"]
        for variant in variants:
            args += ["--variant", variant]
        with pytest.raises(SystemExit):
            main(args)
    assert captured == [["square", "portrait", "story", "landscape"], ["a4"], ["square", "portrait", "story", "landscape", "a4"]]


def test_expand_variants_gives_a4_its_canvas() -> None:
    base = {key: value for key, value in item().items() if key not in ("variant", "canvas")}
    expanded = expand_variants([{**base, "variants": ["a4"]}])
    assert expanded[0]["canvas"] == {"width": 1240, "height": 1754}


def test_composer_and_brief_adapter_stay_on_screen_sizes() -> None:
    assert set(VARIANT_IDS) == set(SOCIAL_VARIANTS)
    assert PRINT_VARIANTS == ("a4",)
    with pytest.raises(ValueError, match="compose_variant_unsupported:a4"):
        compose({"content": {"headline": "x"}, "brand": {"primary": "#000000", "accent": "#ffffff"}}, ["a4"])


# --- bleed ---------------------------------------------------------------------------------


def test_bleed_mirrors_the_edges_and_fills_the_corners() -> None:
    image = Image.new("RGB", (6, 4), "white")
    image.putpixel((0, 0), (255, 0, 0))  # top-left
    image.putpixel((5, 3), (0, 0, 255))  # bottom-right
    image.putpixel((1, 0), (0, 255, 0))  # top row, second column
    grown = add_bleed(image, 2)
    assert grown.size == (10, 8)
    assert grown.crop((2, 2, 8, 6)).tobytes() == image.tobytes()
    assert grown.getpixel((3, 1)) == (0, 255, 0)  # mirrored up across the top edge
    assert grown.getpixel((1, 1)) == (255, 0, 0)  # corner: mirrored up, then left
    assert grown.getpixel((8, 6)) == (0, 0, 255)


def test_bleed_cannot_exceed_the_artwork() -> None:
    with pytest.raises(PrintError, match="bleed_larger_than_artwork"):
        add_bleed(Image.new("RGB", (4, 4)), 5)


# --- profiles ------------------------------------------------------------------------------


@pytest.mark.parametrize("bleed", [-1, 10.5])
def test_bleed_range_is_checked(bleed: float) -> None:
    with pytest.raises(PrintError, match="bleed_mm"):
        check_profile(PrintProfile(bleed_mm=bleed))


def test_an_rgb_profile_is_refused_for_cmyk(tmp_path: Path) -> None:
    srgb = tmp_path / "srgb.icc"
    srgb.write_bytes(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    with pytest.raises(PrintError, match="icc_profile_not_cmyk_output"):
        check_profile(PrintProfile(cmyk_icc=srgb))


def test_an_unreadable_profile_is_refused(tmp_path: Path) -> None:
    junk = tmp_path / "junk.icc"
    junk.write_bytes(b"not a profile")
    with pytest.raises(PrintError, match="icc_profile_unreadable"):
        check_profile(PrintProfile(cmyk_icc=junk))
    with pytest.raises(PrintError, match="icc_profile_unreadable"):
        check_profile(PrintProfile(cmyk_icc=tmp_path / "missing.icc"))


# --- the PDF -------------------------------------------------------------------------------


def _png(width: int, height: int, color: str = "#e03030") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _box(pdf: bytes, name: str) -> list[float]:
    match = re.search(rb"/" + name.encode() + rb" \[([^\]]+)\]", pdf)
    assert match, name
    return [float(value) for value in match.group(1).split()]


def _streams(pdf: bytes) -> list[tuple[bytes, bytes]]:
    return [(head, zlib.decompress(body)) for head, body in re.findall(rb"<< (.*?) /Length \d+ >>\nstream\n(.*?)\nendstream", pdf, re.DOTALL)]


def test_rgb_press_pdf_has_trim_bleed_and_media_boxes_and_crop_marks() -> None:
    dpi = 300
    pdf, info = press_pdf(_png(2480, 3508), dpi=dpi, profile=PrintProfile(bleed_mm=3, crop_marks=True), cmyk=None, title="a4")
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    trim, bleed, media = _box(pdf, "TrimBox"), _box(pdf, "BleedBox"), _box(pdf, "MediaBox")
    assert trim[2] - trim[0] == pytest.approx(2480 * 72 / dpi, abs=0.01)  # 595.2 pt = 210 mm
    assert trim[3] - trim[1] == pytest.approx(3508 * 72 / dpi, abs=0.01)
    bleed_pt = 35 * 72 / dpi  # 3 mm at 300 DPI is 35 px
    assert trim[0] - bleed[0] == pytest.approx(bleed_pt, abs=0.01)
    assert media[0] == 0 and bleed[0] > 0  # room for the marks outside the bleed
    assert info["trim_size_mm"] == [209.97, 297.01] and info["bleed_px"] == 35 and info["color"] == "rgb"
    assert info["pdfx_claimed"] is False and info["icc_profile"] is None
    streams = _streams(pdf)
    image_head, pixels = next((head, body) for head, body in streams if b"/Subtype /Image" in head)
    assert b"/Width 2550" in image_head and b"/Height 3578" in image_head and b"/DeviceRGB" in image_head
    assert len(pixels) == 2550 * 3578 * 3
    content = next(body for head, body in streams if b"/Subtype" not in head)
    assert content.count(b" l S") == 8 and b"0 0 0 RG" in content
    # Every crop mark starts outside the bleed box.
    for x0, y0, x1, y1 in re.findall(rb"([\d.]+) ([\d.]+) m ([\d.]+) ([\d.]+) l S", content):
        xs, ys = (float(x0), float(x1)), (float(y0), float(y1))
        outside_x = max(xs) <= bleed[0] + 1e-6 or min(xs) >= bleed[2] - 1e-6
        outside_y = max(ys) <= bleed[1] + 1e-6 or min(ys) >= bleed[3] - 1e-6
        assert outside_x or outside_y


def test_press_pdf_without_marks_or_bleed_has_media_equal_to_trim() -> None:
    pdf, info = press_pdf(_png(100, 50), dpi=100, profile=PrintProfile(crop_marks=False), cmyk=None, title="t")
    assert _box(pdf, "MediaBox") == _box(pdf, "TrimBox") == [0, 0, 72, 36]
    assert info["bleed_method"] is None


def test_cmyk_press_pdf_has_device_cmyk_pixels_registration_marks_and_output_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = CmykProfile(Path("press.icc"), b"ICCDATA", "Press Coated (Test)", "ab" * 32)
    monkeypatch.setattr(print_pdf, "to_cmyk", lambda image, profile: image.convert("CMYK"))
    pdf, info = press_pdf(_png(40, 20), dpi=72, profile=PrintProfile(bleed_mm=2, crop_marks=True), cmyk=fake, title="ป้าย")
    assert b"/OutputIntents [<< /Type /OutputIntent /S /GTS_PDFX" in pdf and b"(Press Coated \\(Test\\))" in pdf
    assert b"/Title <FEFF" in pdf  # Thai title as UTF-16
    streams = _streams(pdf)
    image_head, pixels = next((head, body) for head, body in streams if b"/Subtype /Image" in head)
    assert b"/DeviceCMYK" in image_head and len(pixels) == (40 + 2 * 6) * (20 + 2 * 6) * 4
    assert any(head.startswith(b"/N 4") and body == b"ICCDATA" for head, body in streams)
    assert b"1 1 1 1 K" in next(body for head, body in streams if b"/Subtype" not in head and not head.startswith(b"/N"))
    assert info["color"] == "cmyk" and info["icc_profile"]["sha256"] == "ab" * 32


def _cmyk_profile() -> Path | None:
    candidates = [os.environ.get("GRAPHICS_TEST_CMYK_ICC", ""), "/mnt/c/Windows/System32/spool/drivers/color/RSWOP.icm",
                  "C:/Windows/System32/spool/drivers/color/RSWOP.icm"]
    return next((Path(path) for path in candidates if path and Path(path).is_file()), None)


@pytest.mark.skipif(_cmyk_profile() is None, reason="no CMYK ICC profile on this host (set GRAPHICS_TEST_CMYK_ICC)")
def test_real_cmyk_conversion_keeps_white_paper_white_and_red_inked() -> None:
    profile = check_profile(PrintProfile(cmyk_icc=_cmyk_profile()))
    assert profile is not None
    image = Image.new("RGB", (2, 1), "white")
    image.putpixel((1, 0), (224, 48, 48))
    converted = print_pdf.to_cmyk(image, profile)
    assert converted.mode == "CMYK"
    assert sum(converted.getpixel((0, 0))) <= 3  # paper white: no ink
    cyan, magenta, yellow, _black = converted.getpixel((1, 0))
    assert magenta > 150 and yellow > 120 and cyan < 60


# --- export integration ------------------------------------------------------------------


@pytest.fixture
def stubbed_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(jobs, "render_png", lambda ir, *, asset_root=None: _png(ir.canvas.width, ir.canvas.height))
    monkeypatch.setattr(jobs, "run_graphic_qa", lambda svg, png, ir: ())
    monkeypatch.setattr(export_module, "_render_scaled", lambda path, width, height, scale: _png(width * scale, height * scale))
    batch = run_batch([item("quote-001")], tmp_path / "batch", "press")
    assert batch.manifest_path is not None
    return batch.manifest_path


def test_press_export_records_the_print_settings_and_keeps_plain_exports_apart(stubbed_batch: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    plain = export_batch(stubbed_batch, out, scale=1, dpi=150)
    press = export_batch(stubbed_batch, out, scale=1, dpi=150, press=PrintProfile(bleed_mm=3, crop_marks=True))
    assert plain.manifest_path != press.manifest_path
    manifest = json.loads(press.manifest_path.read_text(encoding="utf-8"))
    assert manifest["recipe"] == "graphics-press-export.v1" and manifest["export_id"].endswith("-b3mm-marks")
    png, pdf = manifest["items"][0]["artifacts"]
    assert "print" not in png
    assert pdf["filename"] == "quote-001-press-b3mm-marks.pdf" and pdf["print"]["bleed_mm"] == 3
    assert (out / "quote-001-print.pdf").is_file() and (out / pdf["filename"]).is_file()
    assert "Marks and Bleeds" in (out / "MANUAL-STEPS.md").read_text(encoding="utf-8")
    default = export_batch(stubbed_batch, tmp_path / "default", scale=1, dpi=150, press=PrintProfile())
    assert json.loads(default.manifest_path.read_text(encoding="utf-8"))["recipe"] == "graphics-print-export.v1"


def test_press_export_refuses_a_bad_profile_before_writing(stubbed_batch: Path, tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="bleed_mm"):
        export_batch(stubbed_batch, tmp_path / "bad", press=PrintProfile(bleed_mm=20))
    assert not (tmp_path / "bad").exists()


def test_cli_press_export(stubbed_batch: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["export", "--manifest", str(stubbed_batch), "--output", str(tmp_path / "cli"), "--scale", "1", "--dpi", "150",
                 "--bleed-mm", "3", "--crop-marks"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["items"] == 1
    junk = tmp_path / "junk.icc"
    junk.write_bytes(b"x")
    code = main(["export", "--manifest", str(stubbed_batch), "--output", str(tmp_path / "cli2"), "--cmyk-icc", str(junk)])
    assert code != 0
    assert "icc_profile_unreadable" in capsys.readouterr().err


def test_campaign_all_means_the_screen_sizes(tmp_path: Path) -> None:
    from book_production_platform.graphics_production.campaign import (
        CampaignError,
        load_campaign,
    )

    plan = {"schema_version": "graphic-campaign.v1", "campaign_id": "c", "brand": {"primary": "#14213D", "accent": "#FCA311"},
            "variants": ["all"], "posts": [{"id": "p1", "content": {"headline": "Hi"}}]}
    assert load_campaign(plan, tmp_path).variants == list(SOCIAL_VARIANTS)
    with pytest.raises(CampaignError, match="unknown_variant"):
        load_campaign({**plan, "variants": ["a4"]}, tmp_path)


def test_campaign_brief_refuses_a_size_the_campaign_cannot_make() -> None:
    from book_production_platform.graphics_production.campaign_brief import (
        plan_problems,
    )

    brief = "โพสต์ลดราคา ขนาด A4 และ square"
    plan = {"posts": [{"id": "p1", "content": {"headline": "ลดราคา"}}], "variants": ["a4"]}
    assert any(problem.startswith("variants_invalid") for problem in plan_problems(plan, brief, []))
    assert not any(problem.startswith("variants_invalid") for problem in plan_problems({**plan, "variants": ["square"]}, brief, []))


def test_a4_uses_its_own_variant_conditions_instead_of_portraits() -> None:
    from book_production_platform.graphics_production.themes import _condition_holds

    def ctx(variant: str, layers: list[dict[str, object]]) -> SimpleNamespace:
        return SimpleNamespace(request=SimpleNamespace(variant=variant, assets={}, content={}),
                               theme=SimpleNamespace(layers=tuple(layers)))

    portrait_only = [{"id": "L", "items": [{"id": "p", "when": {"variant": ["portrait"]}}]}]
    both = [{"id": "L", "items": [{"id": "p", "when": {"variant": ["portrait"]}}, {"id": "a", "when": {"variant": "a4"}}]}]
    assert _condition_holds({"variant": ["portrait"]}, ctx("a4", portrait_only))  # type: ignore[arg-type]
    assert not _condition_holds({"variant": ["portrait"]}, ctx("a4", both))  # type: ignore[arg-type]
    assert _condition_holds({"variant": "a4"}, ctx("a4", both))  # type: ignore[arg-type]
    assert not _condition_holds({"variant": ["portrait"]}, ctx("square", portrait_only))  # type: ignore[arg-type]
