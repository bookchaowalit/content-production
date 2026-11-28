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
