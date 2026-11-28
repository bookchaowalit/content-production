"""Layout templates for `art template` / `art batch`.

Each template turns (data, brand, size) into an SVG string plus a list of text boxes, so the
caller can render with resvg and `check` can measure contrast and overflow without a model
looking at the picture. Text is measured with the real font files (PIL + raqm) and wrapped
on Thai word boundaries (pythainlp newmm).
"""
from __future__ import annotations

import base64
import html
import json
import mimetypes
import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

SIZES = {
    "square": (1080, 1080),
    "portrait": (1080, 1350),
    "story": (1080, 1920),
    "og": (1200, 630),
    "landscape": (1920, 1080),
    "thumb": (1280, 720),
    "a4": (2480, 3508),
}
BRANDS_DIR = Path(__file__).with_name("brands")


def parse_size(s: str) -> tuple[str, int, int]:
    if s in SIZES:
        return (s, *SIZES[s])
    m = re.fullmatch(r"(\d+)x(\d+)", s)
    if not m:
        raise SystemExit(f"unknown size {s!r}; use one of {', '.join(SIZES)} or WxH")
    return (s, int(m[1]), int(m[2]))


def load_brand(name_or_path: str | None) -> dict:
    p = Path(name_or_path) if name_or_path and Path(name_or_path).exists() else BRANDS_DIR / f"{name_or_path or 'default'}.json"
    if not p.exists():
        raise SystemExit(f"brand not found: {name_or_path} (looked in {BRANDS_DIR})")
    base = json.loads((BRANDS_DIR / "default.json").read_text())
    b = json.loads(p.read_text())
    base["fonts"].update(b.get("fonts", {}))
    base["colors"].update(b.get("colors", {}))
    base.update({k: v for k, v in b.items() if k not in ("fonts", "colors")})
    return base


# ------------------------------------------------------------------ text measuring
@lru_cache(maxsize=None)
def font_file(family: str, bold: bool) -> str:
    q = f"{family}:bold" if bold else family
    try:
        return subprocess.run(["fc-match", "-f", "%{file}", q], capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:  # no fontconfig: measure with Pillow's built-in font
        return ""


@lru_cache(maxsize=None)
def _font(family: str, bold: bool, size: int):
    from PIL import ImageFont

    path = font_file(family, bold)
    if not path:
        return ImageFont.load_default(size)
    f = ImageFont.truetype(path, size)
    if "[" in path:  # variable font: pick the weight axis
        try:
            f.set_variation_by_axes([700 if bold else 400] + [100] * (len(f.get_variation_axes()) - 1))
        except Exception:
            pass
    return f


def text_width(s: str, family: str, bold: bool, size: int) -> float:
    return _font(family, bold, size).getlength(s)


def tokens(s: str) -> list[str]:
    if re.search(r"[฀-๿]", s):
        from pythainlp.tokenize import word_tokenize

        return word_tokenize(s, engine="newmm", keep_whitespace=True)
    return re.findall(r"\S+|\s+", s)


def wrap(s: str, family: str, bold: bool, size: int, max_w: float) -> list[str]:
    lines: list[str] = []
    for para in s.split("\n"):
        cur = ""
        for t in tokens(para):
            trial = cur + t
            if cur and text_width(trial.rstrip(), family, bold, size) > max_w:
                lines.append(cur.rstrip())
                cur = t.lstrip()
            else:
                cur = trial
        lines.append(cur.rstrip())
    return lines


@dataclass
class Fit:
    size: int
    lines: list[str]
    shrunk_to_min: bool = False
    truncated: bool = False


def fit(s: str, family: str, bold: bool, max_w: float, max_lines: int, size_max: int, size_min: int) -> Fit:
    size = size_max
    while size >= size_min:
        lines = wrap(s, family, bold, size, max_w)
        if len(lines) <= max_lines and all(text_width(l, family, bold, size) <= max_w for l in lines):
            return Fit(size, lines, shrunk_to_min=size == size_min and size_min < size_max)
        size -= max(1, size // 25)
    size = size_min
    lines = wrap(s, family, bold, size, max_w)
    cut = lines[:max_lines]
    while cut and text_width(cut[-1] + "…", family, bold, size) > max_w:
        cut[-1] = cut[-1][:-1]
    if len(lines) > max_lines:
        cut[-1] = cut[-1].rstrip() + "…"
    return Fit(size, cut, shrunk_to_min=True, truncated=len(lines) > max_lines)


# ------------------------------------------------------------------ svg helpers
@dataclass
class Box:
    role: str
    x: float
    y: float
    w: float
    h: float
    color: str
    size: int
    fit: Fit | None = None


@dataclass
class Layout:
    svg: str
    boxes: list[Box] = field(default_factory=list)
    text_group_id: str = "txt"


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def data_uri(path: str) -> str:
    p = Path(path).expanduser()
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def text_lines(f: Fit, x: float, y_top: float, family: str, bold: bool, color: str, lh: float = 1.25, anchor: str = "start") -> tuple[str, float]:
    out = []
    y = y_top + f.size * 0.95
    for line in f.lines:
        out.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{esc(family)}" font-size="{f.size}" '
                   f'font-weight="{700 if bold else 400}" fill="{color}" text-anchor="{anchor}">{esc(line)}</text>')
        y += f.size * lh
    return "".join(out), f.size * lh * len(f.lines)


def pill(label: str, x: float, y: float, size: int, family: str, bg: str, fg: str) -> tuple[str, float, float]:
    pad_x, h = size * 1.1, size * 2.3
    w = text_width(label, family, True, size) + pad_x * 2
    svg = (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{h / 2:.1f}" fill="{bg}"/>'
           f'<text x="{x + w / 2:.1f}" y="{y + h / 2 + size * 0.36:.1f}" font-family="{esc(family)}" font-size="{size}" '
           f'font-weight="700" fill="{fg}" text-anchor="middle">{esc(label)}</text>')
    return svg, w, h


def tag(label: str, x: float, y: float, size: int, family: str, bg: str, fg: str) -> tuple[str, float, float]:
    pad_x, h = size * 0.7, size * 1.9
    w = text_width(label, family, True, size) + pad_x * 2
    svg = (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{size * 0.3:.1f}" fill="{bg}"/>'
           f'<text x="{x + pad_x:.1f}" y="{y + h / 2 + size * 0.36:.1f}" font-family="{esc(family)}" font-size="{size}" '
           f'font-weight="700" fill="{fg}">{esc(label)}</text>')
    return svg, w, h


def header(W: int, H: int, defs: str = "") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{W}" height="{H}" viewBox="0 0 {W} {H}"><defs>{defs}</defs>')


def bg_image(href: str, x: float, y: float, w: float, h: float) -> str:
    return f'<image x="{x}" y="{y}" width="{w}" height="{h}" preserveAspectRatio="xMidYMid slice" xlink:href="{href}"/>'


def text_block(d: dict, b: dict, x: float, y: float, max_w: float, max_h: float, u: float,
               fg: str, muted: str, accent: str, title_max: float) -> tuple[str, list[Box], float]:
    """Eyebrow, title, subtitle and CTA stacked from (x, y). Returns svg, boxes and used height."""
    fh, fb = b["fonts"]["heading"], b["fonts"]["body"]
    parts, boxes, cy = [], [], y
    if d.get("eyebrow"):
        label = d["eyebrow"].upper() if d["eyebrow"].isascii() else d["eyebrow"]
        size = round(u * 0.024)
        s, w, hh = tag(label, x, cy, size, fb, accent, b["colors"]["on_accent"])
        parts.append(s); boxes.append(Box("eyebrow", x, cy, w, hh, b["colors"]["on_accent"], size)); cy += hh + u * 0.022
    title_lines = 3 if max_w / u > 0.6 else 4
    f = fit(d.get("title", ""), fh, True, max_w, title_lines, round(u * title_max), round(u * 0.045))
    s, hh = text_lines(f, x, cy, fh, True, fg, lh=1.18)
    parts.append(s); boxes.append(Box("title", x, cy, max_w, hh, fg, f.size, f)); cy += hh
    if d.get("subtitle"):
        cy += u * 0.018
        f = fit(d["subtitle"], fb, False, max_w, 3, round(u * 0.036), round(u * 0.024))
        s, hh = text_lines(f, x, cy, fb, False, muted, lh=1.4)
        parts.append(s); boxes.append(Box("subtitle", x, cy, max_w, hh, muted, f.size, f)); cy += hh
    if d.get("cta"):
        cy += u * 0.035
        s, w, hh = pill(d["cta"], x, cy, round(u * 0.03), fb, accent, b["colors"]["on_accent"])
        parts.append(s); boxes.append(Box("cta", x, cy, w, hh, b["colors"]["on_accent"], round(u * 0.03))); cy += hh
    return "".join(parts), boxes, cy - y


def footer(d: dict, b: dict, W: int, H: int, u: float, color: str, pad: float) -> str:
    label = d.get("footer", b.get("footer", ""))
    if not label:
        return ""
    return (f'<text x="{W - pad:.1f}" y="{H - pad * 0.6:.1f}" font-family="{esc(b["fonts"]["body"])}" '
            f'font-size="{round(u * 0.024)}" fill="{color}" text-anchor="end" opacity="0.85">{esc(label)}</text>')


def logo(d: dict, pad: float, u: float) -> str:
    if not d.get("logo"):
        return ""
    s = u * 0.09
    return f'<image x="{pad:.1f}" y="{pad:.1f}" width="{s:.1f}" height="{s:.1f}" preserveAspectRatio="xMinYMin meet" xlink:href="{data_uri(d["logo"])}"/>'


# ------------------------------------------------------------------ layouts
def poster(d: dict, b: dict, W: int, H: int) -> Layout:
    """Full-bleed image (or brand gradient), dark scrim, text bottom-left."""
    c, u = b["colors"], min(W, H)
    pad = u * 0.07
    defs = (f'<linearGradient id="bgg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c["bg"]}"/>'
            f'<stop offset="1" stop-color="{c["bg2"]}"/></linearGradient>'
            f'<linearGradient id="scrim" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#000" stop-opacity="0.82"/>'
            f'<stop offset="0.7" stop-color="#000" stop-opacity="0.6"/><stop offset="1" stop-color="#000" stop-opacity="0"/></linearGradient>')
    parts = [header(W, H, defs), f'<rect width="{W}" height="{H}" fill="url(#bgg)"/>']
    if d.get("image"):
        parts.append(bg_image(data_uri(d["image"]), 0, 0, W, H))
    max_w = (W - 2 * pad) if W <= H * 1.2 else W * 0.62
    txt, boxes, used = text_block(d, b, pad, 0, max_w, H, u, c["fg"], c["muted"], c["accent"], 0.105 if W <= H * 1.2 else 0.095)
    y0 = H - pad - used - (u * 0.04 if d.get("footer", b.get("footer")) else 0)
    sh = min(H, H - y0 + pad + H * 0.18)
    if d.get("image"):
        parts.append(f'<rect x="0" y="{H - sh:.1f}" width="{W}" height="{sh:.1f}" fill="url(#scrim)"/>')
    for bx in boxes:
        bx.y += y0
    parts.append(logo(d, pad, u))
    parts.append(f'<g id="txt" transform="translate(0 {y0:.1f})">{txt}</g>')
    parts.append(footer(d, b, W, H, u, c["muted"], pad))
    parts.append("</svg>")
    return Layout("".join(parts), boxes)


def split(d: dict, b: dict, W: int, H: int) -> Layout:
    """Image on one side, text on a light panel on the other (stacked when portrait)."""
    c, u = b["colors"], min(W, H)
    landscape = W > H * 1.1
    pad = u * 0.075
    parts = [header(W, H), f'<rect width="{W}" height="{H}" fill="{c["panel"]}"/>']
    if landscape:
        iw = W * 0.48
        img_box, tx, ty, tw, th = (W - iw, 0, iw, H), pad, pad, W - iw - pad * 2, H - pad * 2
    else:
        ih = H * 0.52
        img_box, tx, ty, tw, th = (0, 0, W, ih), pad, ih + pad * 0.8, W - pad * 2, H - ih - pad * 1.6
    if d.get("image"):
        parts.append(bg_image(data_uri(d["image"]), *img_box))
    else:
        parts.append(f'<rect x="{img_box[0]}" y="{img_box[1]}" width="{img_box[2]}" height="{img_box[3]}" fill="{c["bg"]}"/>')
    parts.append(f'<rect x="{tx:.1f}" y="{ty:.1f}" width="{u * 0.07:.1f}" height="{u * 0.008:.1f}" fill="{c["accent"]}"/>')
    txt, boxes, used = text_block(d, b, tx, 0, tw, th, u, c["ink"], c["ink_muted"], c["accent"], 0.085)
    y0 = ty + u * 0.04 + max(0, (th - u * 0.04 - used) / 2) if landscape else ty + u * 0.04
    for bx in boxes:
        bx.y += y0
    parts.append(f'<g id="txt" transform="translate(0 {y0:.1f})">{txt}</g>')
    parts.append(footer(d, b, W, H, u, c["ink_muted"], pad))
    parts.append("</svg>")
    return Layout("".join(parts), boxes)


def card(d: dict, b: dict, W: int, H: int) -> Layout:
    """No photo: brand gradient, accent bar, big centred-left text. Quotes, announcements, stats."""
    c, u = b["colors"], min(W, H)
    pad = u * 0.09
    defs = (f'<linearGradient id="bgg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c["bg"]}"/>'
            f'<stop offset="1" stop-color="{c["bg2"]}"/></linearGradient>')
    parts = [header(W, H, defs), f'<rect width="{W}" height="{H}" fill="url(#bgg)"/>',
             f'<circle cx="{W * 0.92:.1f}" cy="{H * 0.1:.1f}" r="{u * 0.32:.1f}" fill="{c["accent"]}" opacity="0.12"/>',
             f'<rect x="{pad:.1f}" y="{pad:.1f}" width="{u * 0.012:.1f}" height="{u * 0.12:.1f}" fill="{c["accent"]}"/>']
    tw = W - pad * 2 if W <= H * 1.2 else W * 0.7
    txt, boxes, used = text_block(d, b, pad, 0, tw, H - pad * 2, u, c["fg"], c["muted"], c["accent"], 0.12)
    y0 = max(pad * 1.6, (H - used) / 2)
    for bx in boxes:
        bx.y += y0
    parts.append(logo(d, W - pad - u * 0.09, u) if d.get("logo") else "")
    parts.append(f'<g id="txt" transform="translate(0 {y0:.1f})">{txt}</g>')
    parts.append(footer(d, b, W, H, u, c["muted"], pad))
    parts.append("</svg>")
    return Layout("".join(parts), boxes)


TEMPLATES = {"poster": poster, "split": split, "card": card}


def strip_text(svg: str) -> str:
    """The same picture without the text group, for contrast sampling."""
    return re.sub(r'<g id="txt".*?</g>', "", svg, flags=re.S)
