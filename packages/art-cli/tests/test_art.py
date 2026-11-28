import json
import shutil

import pytest
from art_cli import cli
from art_cli import templates as T
from PIL import Image

FONT = "IBM Plex Sans Thai"


def test_parse_size_presets_and_custom():
    assert T.parse_size("og") == ("og", 1200, 630)
    assert T.parse_size("800x600") == ("800x600", 800, 600)
    with pytest.raises(SystemExit):
        T.parse_size("huge")


def test_brand_merges_over_default(tmp_path):
    p = tmp_path / "acme.json"
    p.write_text(json.dumps({"colors": {"accent": "#ff0000"}, "footer": "acme.example"}))
    b = T.load_brand(str(p))
    assert b["colors"]["accent"] == "#ff0000"
    assert b["colors"]["bg"] == T.load_brand(None)["colors"]["bg"]  # untouched keys come from default
    assert b["footer"] == "acme.example"


def test_thai_wraps_on_word_boundaries():
    text = "โปรโมชั่นพิเศษสำหรับลูกค้าใหม่ลดราคาทุกเมนู"
    lines = T.wrap(text, FONT, True, 40, 260)
    assert len(lines) > 1
    assert "".join(lines) == text  # nothing lost or duplicated
    words = set(T.tokens(text))
    for line in lines:  # every break falls between pythainlp words
        assert any(line.startswith(w) for w in words)


def test_fit_shrinks_then_truncates():
    short = T.fit("สวัสดี", FONT, True, 600, 2, 80, 30)
    assert short.size == 80 and not short.truncated
    long = T.fit("คำ " * 200, FONT, True, 300, 2, 80, 30)
    assert long.truncated and long.size == 30 and long.lines[-1].endswith("…")
    assert len(long.lines) == 2


def test_contrast_matches_wcag_reference():
    assert round(cli._contrast((0, 0, 0), (255, 255, 255)), 1) == 21.0
    assert round(cli._contrast(cli._hex("#777777"), (255, 255, 255)), 2) == 4.48


def test_check_boxes_flags_low_contrast_and_cut_text():
    brand = T.load_brand(None)
    bg = Image.new("RGB", (400, 200), (250, 250, 250))
    cut = T.fit("คำ " * 200, FONT, True, 300, 2, 80, 30)
    boxes = [T.Box("subtitle", 10, 10, 300, 50, "#eeeeee", 20),
             T.Box("title", 10, 80, 300, 60, "#111111", 40, cut)]
    warn = cli.check_boxes(boxes, bg, brand, 400, 200)
    assert any(w.startswith("subtitle: contrast") for w in warn)
    assert any("text cut" in w for w in warn)
    assert not any(w.startswith("title: contrast") for w in warn)


@pytest.mark.parametrize("name", sorted(T.TEMPLATES))
def test_templates_produce_svg_with_text_group(name):
    lay = T.TEMPLATES[name]({"title": "ทดสอบ template", "subtitle": "รายละเอียด", "eyebrow": "ใหม่", "cta": "ดูเลย"},
                            T.load_brand(None), 1200, 630)
    assert lay.svg.startswith("<svg") and lay.svg.endswith("</svg>")
    assert '<g id="txt"' in lay.svg and '<g id="txt"' not in T.strip_text(lay.svg)
    assert {b.role for b in lay.boxes} >= {"title", "subtitle", "eyebrow", "cta"}
    assert all(0 <= b.y and b.y + b.h <= 630 for b in lay.boxes)


@pytest.mark.skipif(not shutil.which("resvg"), reason="resvg not installed")
def test_render_job_writes_png_and_report(tmp_path):
    img = tmp_path / "bg.png"
    Image.new("RGB", (64, 64), (30, 30, 30)).save(img)
    job = {"name": "t", "template": "poster", "sizes": ["og", "square"],
           "data": {"title": "คาเฟ่ลอยฟ้า", "subtitle": "เปิดทุกวัน", "image": "bg.png"}}
    res = cli.render_job(job, None, tmp_path, tmp_path)
    assert [r["size"] for r in res] == ["1200x630", "1080x1080"]
    for r in res:
        assert Image.open(r["file"]).size == tuple(map(int, r["size"].split("x")))
        assert r["warnings"] == []
    assert not list(tmp_path.glob(".*.bg.*"))  # contrast scratch files cleaned up


def test_find_urls_walks_nested_attempts():
    body = {"attempts": [{"outputs": [{"url": "https://r2.example/a.png"}, {"url": "http://insecure"}]}]}
    assert cli.find_urls(body) == ["https://r2.example/a.png"]


def test_safe_name_never_leaves_the_folder():
    assert cli._safe_name("../../etc/passwd") == "etc-passwd"
    assert cli._safe_name("..") == "art"
    assert "/" not in cli._safe_name("a/b\\c")


def test_fit_cuts_a_single_word_wider_than_the_box():
    f = T.fit("Supercalifragilisticexpialidocious" * 3, FONT, True, 120, 2, 40, 20)
    assert f.truncated
    assert all(T.text_width(line, FONT, True, f.size) <= 120 for line in f.lines)


def test_check_boxes_flags_horizontal_overflow():
    bg = Image.new("RGB", (400, 200), (0, 0, 0))
    warn = cli.check_boxes([T.Box("title", 300, 10, 200, 40, "#ffffff", 40)], bg, T.load_brand(None), 400, 200)
    assert any("outside the canvas" in w for w in warn)


def test_split_warns_when_copy_is_taller_than_its_panel():
    lay = T.split({"eyebrow": "x", "title": "คำ " * 60, "subtitle": "ยาว " * 80, "cta": "ok"}, T.load_brand(None), 600, 640)
    assert lay.warnings and "taller than its panel" in lay.warnings[0]


def test_final_refuses_paid_work_without_yes_when_not_interactive(monkeypatch):
    import argparse
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    a = argparse.Namespace(max_usd=0.25, yes=False)
    with pytest.raises(SystemExit, match="--yes"):
        cli.cmd_final(a)


def test_gateway_must_be_https(monkeypatch):
    monkeypatch.setattr(cli, "GATEWAY", "http://evil.example")
    with pytest.raises(SystemExit, match="https"):
        cli._gateway_base()
    monkeypatch.setattr(cli, "GATEWAY", "https://api.example/")
    assert cli._gateway_base() == "https://api.example"


@pytest.mark.skipif(not shutil.which("resvg"), reason="resvg not installed")
def test_batch_names_do_not_collide_or_escape(tmp_path):
    taken: set[str] = set()
    a = cli.render_job({"name": "../x", "template": "card", "sizes": ["og", "1200x630"], "data": {"title": "หนึ่ง"}},
                       None, tmp_path, tmp_path, taken)
    b = cli.render_job({"name": "../x", "template": "card", "sizes": ["og"], "data": {"title": "สอง"}},
                       None, tmp_path, tmp_path, taken)
    files = [r["file"] for r in a + b]
    assert len(set(files)) == 3
    assert all(str(tmp_path) in f for f in files)
    assert not list(tmp_path.glob(".art-*"))  # temp svgs/pngs cleaned up
