"""art: one command for image work on this workstation.

  art draft "prompt" [-n 8] [--model sd-turbo|sdxl-turbo] [--size 512] [--seed 1]
      Local GPU (RTX 3050), free. Writes every image plus contact.png.
  art final "prompt" [--aspect 1:1|3:2|2:3] [--res draft|standard|high] [-n 1] [--bg transparent] [--yes]
      ai-gateway gpt-image-1.5 (subject claude-art, 3 USD/month cap). Always prices with dry_run first.
  art text IMAGE --title "..." [--subtitle "..."] [--font Prompt] [--pos bottom|center|top]
      Overlays crisp text (Thai included) with resvg instead of letting a model draw letters.

  art template poster|split|card --data '{"title":"..."}' --size square,story,og [--brand NAME]
  art batch spec.yaml        # jobs x sizes, report.json + contact.png, contrast/overflow warnings
  art check DIR|FILE.png
  art 3d turntable model.glb [--gif] | art 3d shot model.glb | art 3d logo --text "ชื่อแบรนด์" --color "#f59e0b"

Install: packages/art-cli/install.sh. Outputs go to ./art-out/<time>-<slug>/ unless -o is given. The gateway token is read from
Infisical /ai-media CLAUDE_ART_TOKEN at run time (or $CLAUDE_ART_TOKEN) and never printed.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

GATEWAY = os.environ.get("ART_GATEWAY_URL", "https://api.bookchaowalit.com")
MODELS = {"sd-turbo": "stabilityai/sd-turbo", "sdxl-turbo": "stabilityai/sdxl-turbo"}


def out_dir(arg: str | None, prompt: str) -> Path:
    if arg:
        p = Path(arg)
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower())[:40].strip("-") or "art"
        p = Path("art-out") / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{slug}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def contact_sheet(paths: list[Path], dest: Path) -> None:
    from PIL import Image, ImageDraw

    ims = [Image.open(p).convert("RGB") for p in paths]
    w = 256
    cols = min(4, len(ims))
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * w), "white")
    d = ImageDraw.Draw(sheet)
    for i, im in enumerate(ims):
        im = im.resize((w, w * im.height // im.width))
        x, y = (i % cols) * w, (i // cols) * w
        sheet.paste(im, (x, y))
        d.rectangle([x, y, x + 28, y + 22], fill="black")
        d.text((x + 6, y + 5), str(i + 1), fill="white")
    sheet.save(dest)


# ---------------------------------------------------------------- draft (local GPU)
def cmd_draft(a: argparse.Namespace) -> None:
    import torch
    from diffusers import AutoPipelineForText2Image

    size = a.size or 512
    pipe = AutoPipelineForText2Image.from_pretrained(
        MODELS[a.model], torch_dtype=torch.float16, variant="fp16"
    ).to("cuda")
    pipe.set_progress_bar_config(disable=True)
    d = out_dir(a.out, a.prompt)
    paths = []
    seed0 = a.seed if a.seed is not None else int(time.time()) % 100000
    t = time.time()
    for i in range(a.n):
        g = torch.Generator("cuda").manual_seed(seed0 + i)
        img = pipe(a.prompt, num_inference_steps=a.steps, guidance_scale=0.0,
                   width=size, height=size, generator=g).images[0]
        p = d / f"{i + 1:02d}-seed{seed0 + i}.png"
        img.save(p)
        paths.append(p)
    contact_sheet(paths, d / "contact.png")
    (d / "prompt.json").write_text(json.dumps(
        {"engine": "local", "model": MODELS[a.model], "prompt": a.prompt, "seed0": seed0,
         "steps": a.steps, "size": size}, ensure_ascii=False, indent=2))
    print(f"{a.n} drafts in {time.time() - t:.1f}s -> {d}/contact.png")


# ---------------------------------------------------------------- final (ai-gateway)
def gateway_token() -> str:
    tok = os.environ.get("CLAUDE_ART_TOKEN", "").strip()
    if tok:
        return tok
    pid = subprocess.run(
        ["bash", "-c", "source ~/.config/solo-empire/environment.sh >/dev/null 2>&1; printf %s \"$INFISICAL_PROJECT_ID\""],
        capture_output=True, text=True, check=False).stdout.strip()
    r = subprocess.run(["infisical", "secrets", "get", "CLAUDE_ART_TOKEN", "--projectId", pid, "--env", "dev",
                        "--path", "/ai-media", "--plain", "--silent"], capture_output=True, text=True, check=False)
    tok = r.stdout.strip()
    if r.returncode or not tok:
        sys.exit("could not read CLAUDE_ART_TOKEN from Infisical /ai-media")
    return tok


def call(method: str, path: str, tok: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        GATEWAY + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "art-cli/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except ValueError:
            return e.code, {"error": e.reason}


def find_urls(obj, found=None) -> list[str]:
    found = [] if found is None else found
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "url" and isinstance(v, str) and v.startswith("https://"):
                found.append(v)
            else:
                find_urls(v, found)
    elif isinstance(obj, list):
        for v in obj:
            find_urls(v, found)
    return found


def cmd_final(a: argparse.Namespace) -> None:
    tok = gateway_token()
    body = {"kind": "image", "models": ["openai-image"], "prompt": a.prompt, "aspect": a.aspect, "resolution": a.res, "n": a.n,
            "route": "claude-art", "privacy_class": "internal", "purpose": "claude-art final"}
    if a.bg:
        body["params"] = {"background": a.bg}
    st, dry = call("POST", "/api/ai/media/requests", tok, {**body, "dry_run": True})
    if st != 200:
        sys.exit(f"dry run refused ({st}): {json.dumps(dry, ensure_ascii=False)}")
    cost = dry["reserved_usd"]
    print(f"price: {cost} USD for {a.n} image(s) via {dry['routing']['selected']}")
    if cost > a.max_usd:
        sys.exit(f"over --max-usd {a.max_usd}; not created")
    if not a.yes and sys.stdin.isatty() and input("create? [y/N] ").strip().lower() != "y":
        sys.exit("cancelled")
    st, job = call("POST", "/api/ai/media/requests", tok, {**body, "expected_reserved_usd": cost})
    if st != 202:
        sys.exit(f"create refused ({st}): {json.dumps(job, ensure_ascii=False)}")
    jid = job["id"]
    print(f"queued {jid}")
    deadline = time.time() + 300
    while True:
        time.sleep(3)
        st, s = call("GET", f"/api/ai/media/requests/{jid}", tok)
        status = s.get("status")
        if status in ("completed", "failed", "cancelled", "partial") or time.time() > deadline:
            break
    urls = find_urls(s)
    d = out_dir(a.out, a.prompt)
    paths = []
    for i, u in enumerate(urls):
        p = d / f"final-{i + 1:02d}.png"
        with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "art-cli/1.0"}), timeout=60) as r:
            p.write_bytes(r.read())
        paths.append(p)
    (d / "prompt.json").write_text(json.dumps(
        {"engine": "ai-gateway", "request_id": jid, "status": status, "settled_usd": s.get("settled_usd"),
         **{k: v for k, v in body.items()}}, ensure_ascii=False, indent=2))
    print(f"status {status}, settled {s.get('settled_usd')} USD, {len(paths)} file(s) -> {d}")
    if not paths:
        sys.exit(1)


# ---------------------------------------------------------------- text overlay
def cmd_text(a: argparse.Namespace) -> None:
    from PIL import Image

    src = Path(a.image)
    im = Image.open(src)
    W, H = im.size
    mime = "image/png" if src.suffix.lower() == ".png" else "image/jpeg"
    b64 = base64.b64encode(src.read_bytes()).decode()
    ts = a.title_size or round(W * 0.075)
    ss = a.sub_size or round(ts * 0.5)
    pad = round(W * 0.06)
    block = ts + (ss * 1.5 if a.subtitle else 0)
    if a.pos == "top":
        y0 = pad + ts
    elif a.pos == "center":
        y0 = (H - block) / 2 + ts
    else:
        y0 = H - pad - block + ts
    anchor, x = {"left": ("start", pad), "center": ("middle", W / 2), "right": ("end", W - pad)}[a.align]
    shade = ""
    if a.scrim:
        top = a.pos == "top"
        shade = (f'<linearGradient id="g" x1="0" y1="{0 if top else 1}" x2="0" y2="{1 if top else 0}">'
                 f'<stop offset="0" stop-color="#000" stop-opacity="0.6"/><stop offset="0.55" stop-color="#000" stop-opacity="0"/>'
                 f'</linearGradient>')
    esc = html.escape
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{W}" height="{H}">',
        f'<defs>{shade}<filter id="s"><feDropShadow dx="0" dy="{max(1, ts // 30)}" stdDeviation="{max(1, ts // 18)}" flood-opacity="0.45"/></filter></defs>',
        f'<image width="{W}" height="{H}" xlink:href="data:{mime};base64,{b64}"/>',
        f'<rect width="{W}" height="{H}" fill="url(#g)"/>' if a.scrim and a.pos != "center" else "",
        f'<g font-family="{esc(a.font)}" fill="{esc(a.color)}" text-anchor="{anchor}" filter="url(#s)">',
        f'<text x="{x}" y="{y0}" font-size="{ts}" font-weight="700">{esc(a.title)}</text>',
        f'<text x="{x}" y="{y0 + ss * 1.5}" font-size="{ss}" font-weight="400">{esc(a.subtitle)}</text>' if a.subtitle else "",
        "</g></svg>",
    ]
    out = Path(a.out) if a.out else src.with_name(src.stem + "-text.png")
    svg = out.with_suffix(".svg")
    svg.write_text("".join(parts), encoding="utf-8")
    fonts = str(Path.home() / ".local/share/fonts")
    r = subprocess.run(["resvg", "--use-fonts-dir", fonts, "--resources-dir", str(src.parent.resolve()), str(svg), str(out)],
                       capture_output=True, text=True, check=False)
    if r.returncode:
        sys.exit(r.stderr.strip())
    if not a.keep_svg:
        svg.unlink()
    print(f"-> {out}")


# ---------------------------------------------------------------- template / batch / check
def _lum(rgb):
    def ch(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _hex(c: str):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _resvg(svg: Path, png: Path) -> None:
    r = subprocess.run(["resvg", "--use-fonts-dir", str(Path.home() / ".local/share/fonts"), str(svg), str(png)],
                       capture_output=True, text=True, check=False)
    if r.returncode:
        sys.exit(f"resvg failed for {svg}: {r.stderr.strip()}")


def render_job(job: dict, brand_name: str | None, out: Path, base: Path) -> list[dict]:
    from PIL import Image

    from . import templates as T

    brand = T.load_brand(job.get("brand", brand_name))
    data = dict(job.get("data", {}))
    for k in ("image", "logo"):
        if data.get(k) and not Path(data[k]).expanduser().is_absolute():
            data[k] = str((base / data[k]).resolve())
    tpl = T.TEMPLATES.get(job.get("template", "poster"))
    if not tpl:
        sys.exit(f"unknown template {job.get('template')}; use {', '.join(T.TEMPLATES)}")
    name = job.get("name") or re.sub(r"[^a-z0-9]+", "-", data.get("title", "art").lower())[:30].strip("-") or "art"
    results = []
    for size in job.get("sizes", ["square"]):
        label, W, H = T.parse_size(size)
        lay = tpl(data, brand, W, H)
        stem = out / f"{name}-{label}"
        svg, png = stem.with_suffix(".svg"), stem.with_suffix(".png")
        svg.write_text(lay.svg, encoding="utf-8")
        _resvg(svg, png)
        bg_svg = out / f".{name}-{label}.bg.svg"
        bg_png = bg_svg.with_suffix(".png")
        bg_svg.write_text(T.strip_text(lay.svg), encoding="utf-8")
        _resvg(bg_svg, bg_png)
        warn = check_boxes(lay.boxes, Image.open(bg_png).convert("RGB"), brand, W, H)
        bg_svg.unlink(); bg_png.unlink()
        if not job.get("keep_svg"):
            svg.unlink()
        im = Image.open(png)
        if im.size != (W, H):
            warn.append(f"size {im.size} != {(W, H)}")
        kb = png.stat().st_size // 1024
        if kb > 5000:
            warn.append(f"file {kb} KB > 5 MB")
        results.append({"file": str(png), "template": job.get("template", "poster"), "size": f"{W}x{H}", "kb": kb, "warnings": warn})
    return results


def check_boxes(boxes, bg, brand, W, H) -> list[str]:
    warn = []
    u = min(W, H)
    for bx in boxes:
        if bx.fit and bx.fit.truncated:
            warn.append(f"{bx.role}: text cut to {len(bx.fit.lines)} lines (shorten it)")
        elif bx.fit and bx.fit.shrunk_to_min:
            warn.append(f"{bx.role}: font shrunk to minimum {bx.size}px")
        if bx.y + bx.h > H + 1 or bx.y < -1:
            warn.append(f"{bx.role}: outside the canvas")
        if bx.role in ("cta", "eyebrow"):
            ratio = _contrast(_hex(brand["colors"]["on_accent"]), _hex(brand["colors"]["accent"]))
        else:
            x0, y0 = max(0, int(bx.x)), max(0, int(bx.y))
            x1, y1 = min(W, int(bx.x + bx.w)), min(H, int(bx.y + bx.h))
            if x1 <= x0 or y1 <= y0:
                continue
            region = bg.crop((x0, y0, x1, y1)).resize((48, 24))
            fg = _hex(bx.color)
            vals = sorted(_contrast(fg, px) for px in region.getdata())
            ratio = vals[len(vals) // 10]  # 10th percentile: the worst tenth of the background
        need = 3.0 if bx.size >= u * 0.045 or bx.role == "title" else 4.5
        if ratio < need:
            warn.append(f"{bx.role}: contrast {ratio:.1f} < {need} (add scrim, change colour or image)")
    return warn


def _report(results: list[dict], out: Path) -> None:
    from PIL import Image

    (out / "report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    pngs = [Path(r["file"]) for r in results]
    if len(pngs) > 1:
        sheet_h = 360
        ims = [Image.open(p).convert("RGB") for p in pngs]
        ims = [im.resize((max(1, im.width * sheet_h // im.height), sheet_h)) for im in ims]
        total = sum(im.width for im in ims) + 12 * (len(ims) + 1)
        sheet = Image.new("RGB", (total, sheet_h + 24), "#e5e7eb")
        x = 12
        for im in ims:
            sheet.paste(im, (x, 12)); x += im.width + 12
        sheet.save(out / "contact.png")
    bad = 0
    for r in results:
        flag = "OK " if not r["warnings"] else "WARN"
        bad += bool(r["warnings"])
        print(f"{flag} {Path(r['file']).name} {r['size']} {r['kb']}KB" + ("".join(f"\n     - {w}" for w in r["warnings"])))
    print(f"{len(results)} file(s), {bad} with warnings -> {out}" + (" (contact.png)" if len(pngs) > 1 else ""))


def cmd_template(a: argparse.Namespace) -> None:
    data = json.loads(a.data) if a.data.strip().startswith("{") else json.loads(Path(a.data).read_text())
    job = {"template": a.template, "data": data, "sizes": a.size.split(","), "name": a.name, "keep_svg": a.keep_svg}
    out = out_dir(a.out, data.get("title", a.template))
    _report(render_job(job, a.brand, out, Path.cwd()), out)


def cmd_batch(a: argparse.Namespace) -> None:
    spec_path = Path(a.spec)
    raw = spec_path.read_text()
    if spec_path.suffix in (".yaml", ".yml"):
        import yaml
        spec = yaml.safe_load(raw)
    else:
        spec = json.loads(raw)
    out = Path(a.out or spec.get("out") or out_dir(None, spec_path.stem))
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for job in spec["jobs"]:
        results += render_job(job, spec.get("brand"), out, spec_path.parent.resolve())
    _report(results, out)


def cmd_check(a: argparse.Namespace) -> None:
    from PIL import Image

    for f in a.files:
        p = Path(f)
        rep = p / "report.json" if p.is_dir() else None
        if rep and rep.exists():
            for r in json.loads(rep.read_text()):
                ok = Path(r["file"]).exists()
                print(("OK  " if ok and not r["warnings"] else "WARN"), Path(r["file"]).name, r["size"], *r["warnings"], "" if ok else "(missing)")
            continue
        im = Image.open(p)
        kb = p.stat().st_size // 1024
        print(f"{p.name}: {im.size[0]}x{im.size[1]} {im.mode} {kb}KB" + ("  WARN >5MB" if kb > 5000 else ""))


# ---------------------------------------------------------------- 3d (Blender)
def _bg_canvas(bg: str, size):
    from PIL import Image

    if "," in bg:  # vertical gradient "top,bottom"
        top, bot = (_hex(c) for c in bg.split(","))
        g = Image.new("RGB", (1, 256))
        for y in range(256):
            t = y / 255
            g.putpixel((0, y), tuple(round(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
        return g.resize(size)
    return Image.new("RGB", size, _hex(bg))


def _composite(src: Path, dst: Path, bg: str, canvas=None):
    from PIL import Image

    im = Image.open(src).convert("RGBA")
    if bg == "transparent":
        im.save(dst)
        return
    base = (canvas or _bg_canvas(bg, im.size)).copy().convert("RGBA")
    base.alpha_composite(im)
    base.convert("RGB").save(dst)


def _text_svg(text: str, font: str, path: Path) -> None:
    """Thai-safe: Inkscape (Pango) shapes the text and converts it to paths for Blender."""
    raw = path.with_name("text-raw.svg")
    raw.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="2000" height="400"><text x="20" y="300" '
        f'font-family="{html.escape(font)}" font-weight="700" font-size="240">{html.escape(text)}</text></svg>',
        encoding="utf-8")
    r = subprocess.run(["inkscape", str(raw), "--export-text-to-path", "--export-plain-svg",
                        "--export-area-drawing", "-o", str(path)], capture_output=True, text=True, check=False)
    raw.unlink()
    if r.returncode or not path.exists():
        sys.exit(f"inkscape failed: {r.stderr.strip()[:300]}")


def cmd_3d(a: argparse.Namespace) -> None:
    import shutil

    from . import templates as T

    _label, W, H = T.parse_size(a.size)
    src = a.input or a.text or "logo"
    d = out_dir(a.out, Path(src).stem if a.input else src)
    raw = d / "raw"
    if raw.exists():
        shutil.rmtree(raw)
    raw.mkdir()
    job = {"mode": a.kind, "out_dir": str(raw), "size": [W, H], "frames": a.frames, "samples": a.samples,
           "engine": a.engine, "elevation": a.elevation, "lens": a.lens}
    if a.color:
        job["color"] = a.color
    if a.metallic is not None:
        job["metallic"] = a.metallic
    if a.roughness is not None:
        job["roughness"] = a.roughness
    if a.kind == "logo":
        if a.text:
            svg = d / "text.svg"
            _text_svg(a.text, a.font, svg)
        elif a.input and a.input.lower().endswith(".svg"):
            svg = Path(a.input).resolve()
        else:
            sys.exit("logo needs an .svg input or --text")
        job["svg"], job["depth"] = str(svg), a.depth
        job["mode"] = "logo"
    else:
        if not a.input:
            sys.exit(f"{a.kind} needs a model file (glb/gltf/obj/fbx/stl/ply/usd/blend)")
        job["model"] = str(Path(a.input).resolve())
    if a.swing is not None:
        job["swing"] = a.swing
    if a.kind == "shot" and a.angles:
        job["angles"] = [[float(x) for x in pair.split(":")] for pair in a.angles.split(",")]
    script = Path(__file__).resolve().with_name("blender_scene.py")
    t = time.time()
    proc = subprocess.run(["blender", "-b", "--factory-startup", "--python-exit-code", "1", "--python", str(script),
                           "--", json.dumps(job)], capture_output=True, text=True, check=False)
    info = [l[6:] for l in proc.stdout.splitlines() if l.startswith("ART3D ")]
    if proc.returncode:
        tail = [l for l in (proc.stdout + proc.stderr).splitlines() if l.strip()][-15:]
        sys.exit("blender failed:\n" + "\n".join(tail))
    print(f"blender {time.time() - t:.0f}s: " + "; ".join(info))

    canvas = None if a.bg == "transparent" else _bg_canvas(a.bg, (W, H))
    outs = []
    if a.kind == "shot":
        for f in sorted(raw.glob("shot_*.png")):
            dst = d / f.name
            _composite(f, dst, a.bg, canvas)
            outs.append(dst)
        if len(outs) > 1:
            contact_sheet(outs, d / "contact.png")
    else:
        frames = d / "frames"
        frames.mkdir(exist_ok=True)
        for f in sorted(raw.glob("f_*.png")):
            _composite(f, frames / f.name, a.bg, canvas)
        allf = sorted(frames.glob("f_*.png"))
        swinging = a.kind == "logo" if a.swing is None else bool(a.swing)
        shutil.copy(allf[len(allf) // 4] if swinging else allf[0], d / "poster.png")  # swing passes 0 deg at 1/4
        pattern = str(frames / "f_%04d.png")
        if a.bg == "transparent":
            video = d / "turntable.webm"
            cmd = ["ffmpeg", "-v", "error", "-y", "-framerate", str(a.fps), "-i", pattern, "-c:v", "libvpx-vp9",
                   "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "30", str(video)]
        else:
            video = d / "turntable.mp4"
            enc = ["-c:v", "h264_nvenc", "-cq", "19", "-preset", "p6"]
            cmd = ["ffmpeg", "-v", "error", "-y", "-framerate", str(a.fps), "-i", pattern, *enc,
                   "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video)]
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if r.returncode and "nvenc" in " ".join(cmd):
            cmd[cmd.index("h264_nvenc"):cmd.index("h264_nvenc") + 5] = ["libx264", "-crf", "18", "-preset", "slow"]
            r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if r.returncode:
            sys.exit(f"ffmpeg failed: {r.stderr.strip()[:300]}")
        outs += [video, d / "poster.png"]
        if a.gif:
            gif = d / "turntable.gif"
            vf = f"fps={min(a.fps, 15)},scale={min(W, 540)}:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(a.fps), "-i", pattern, "-vf", vf, str(gif)], check=True)
            outs.append(gif)
        if not a.keep_frames:
            shutil.rmtree(frames)
    if not a.keep_frames:
        shutil.rmtree(raw)
    for o in outs:
        print(f"-> {o} ({o.stat().st_size // 1024} KB)")
    print(f"total {time.time() - t:.0f}s")


def main() -> None:
    ap = argparse.ArgumentParser(prog="art", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("draft", help="local GPU drafts (free)")
    d.add_argument("prompt")
    d.add_argument("-n", type=int, default=8)
    d.add_argument("--model", choices=list(MODELS), default="sd-turbo")
    d.add_argument("--steps", type=int, default=2)
    d.add_argument("--size", type=int)
    d.add_argument("--seed", type=int)
    d.add_argument("-o", "--out")
    d.set_defaults(fn=cmd_draft)

    f = sub.add_parser("final", help="ai-gateway gpt-image-1.5 (paid, dry_run first)")
    f.add_argument("prompt")
    f.add_argument("--aspect", choices=["1:1", "3:2", "2:3"], default="1:1")
    f.add_argument("--res", choices=["draft", "standard", "high"], default="standard")
    f.add_argument("-n", type=int, default=1)
    f.add_argument("--bg", choices=["auto", "transparent", "opaque"])
    f.add_argument("--max-usd", type=float, default=0.25, help="refuse when the dry-run price is higher")
    f.add_argument("--yes", action="store_true", help="skip the confirm prompt")
    f.add_argument("-o", "--out")
    f.set_defaults(fn=cmd_final)

    t = sub.add_parser("text", help="overlay text (Thai ok) with resvg")
    t.add_argument("image")
    t.add_argument("--title", required=True)
    t.add_argument("--subtitle")
    t.add_argument("--font", default="Prompt")
    t.add_argument("--color", default="#ffffff")
    t.add_argument("--pos", choices=["top", "center", "bottom"], default="bottom")
    t.add_argument("--align", choices=["left", "center", "right"], default="center")
    t.add_argument("--title-size", type=int)
    t.add_argument("--sub-size", type=int)
    t.add_argument("--no-scrim", dest="scrim", action="store_false")
    t.add_argument("--keep-svg", action="store_true")
    t.add_argument("-o", "--out")
    t.set_defaults(fn=cmd_text)

    tp = sub.add_parser("template", help="brand layout from JSON data (no model needed)")
    tp.add_argument("template", choices=["poster", "split", "card"])
    tp.add_argument("--data", required=True, help="JSON string or file: title, subtitle, eyebrow, cta, image, logo, footer")
    tp.add_argument("--size", default="square", help="comma list: square,portrait,story,og,landscape,thumb,a4 or WxH")
    tp.add_argument("--brand", help="brand name in brands/ or a JSON path")
    tp.add_argument("--name")
    tp.add_argument("--keep-svg", action="store_true")
    tp.add_argument("-o", "--out")
    tp.set_defaults(fn=cmd_template)

    bp = sub.add_parser("batch", help="many jobs x sizes from one YAML/JSON spec")
    bp.add_argument("spec")
    bp.add_argument("-o", "--out")
    bp.set_defaults(fn=cmd_batch)

    cp = sub.add_parser("check", help="re-check rendered files or an output folder (report.json)")
    cp.add_argument("files", nargs="+")
    cp.set_defaults(fn=cmd_check)

    g = sub.add_parser("3d", help="Blender studio renders: turntable video, product shots, 3D logo/text")
    g.add_argument("kind", choices=["turntable", "shot", "logo"])
    g.add_argument("input", nargs="?", help="model (glb/gltf/obj/fbx/stl/ply/usd/blend) or logo .svg")
    g.add_argument("--text", help="logo: text to extrude (Thai ok, shaped by Inkscape)")
    g.add_argument("--font", default="Prompt")
    g.add_argument("--size", default="square")
    g.add_argument("--bg", default="#f1f5f9", help="hex, 'top,bottom' gradient, or transparent")
    g.add_argument("--color", help="material colour (logo, or models without materials)")
    g.add_argument("--metallic", type=float)
    g.add_argument("--roughness", type=float)
    g.add_argument("--depth", type=float, default=0.08, help="logo thickness as a share of its width")
    g.add_argument("--frames", type=int, default=72)
    g.add_argument("--fps", type=int, default=24)
    g.add_argument("--samples", type=int, default=48)
    g.add_argument("--engine", choices=["cycles", "eevee"], default="cycles")
    g.add_argument("--elevation", type=float, default=18)
    g.add_argument("--lens", type=float, default=50)
    g.add_argument("--swing", type=float, help="rock +-deg instead of a full spin (logo default 30)")
    g.add_argument("--angles", help="shot: az:el list, e.g. -35:20,0:8,35:35")
    g.add_argument("--gif", action="store_true")
    g.add_argument("--keep-frames", action="store_true")
    g.add_argument("-o", "--out")
    g.set_defaults(fn=cmd_3d)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
