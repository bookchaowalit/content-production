# art-cli

`art` is a workstation command for cheap image work. The model (or a person) picks parameters; the
tool draws. It keeps AI cost down in two ways: an agent sends a short JSON spec instead of writing
SVG/HTML, and it reads one contact sheet plus OK/WARN lines instead of opening every image.

| Command | What | Cost |
|---|---|---|
| `art draft "prompt" -n 8` | SD-Turbo on the local NVIDIA GPU, every image + `contact.png` | free |
| `art template poster\|split\|card --data '{..}' --size square,story,og` | brand layout with real-font measuring and Thai word wrap | free |
| `art batch spec.yaml` | jobs × sizes, `report.json` + `contact.png` | free |
| `art check OUT_DIR` | re-print size, overflow and WCAG contrast warnings | free |
| `art text img.png --title ..` | text overlay on an existing image | free |
| `art final "prompt"` | ai-gateway image (`/api/ai/media/requests`), dry-run price first, `--max-usd` cap | paid |
| `art 3d turntable\|shot\|logo` | Blender Cycles studio renders, mp4/gif, Thai 3D text via Inkscape | free |

Template data keys: `title`, `subtitle`, `eyebrow`, `cta`, `image`, `logo`, `footer`. Sizes: `square`,
`portrait`, `story`, `og`, `landscape`, `thumb`, `a4` or `WxH`. Brands are JSON files in
`art_cli/brands/` (`--brand NAME`) or any path; missing keys fall back to `default.json`.

## Install

```bash
./install.sh            # ~/.local/opt/art-venv, ~/.local/bin/art, resvg, Thai/Latin OFL fonts
ART_GPU=0 ./install.sh  # without torch/diffusers
```

Runtimes found on `PATH`: `resvg` (required for templates), `blender` and `ffmpeg` (`art 3d`),
`inkscape` (`art 3d logo --text`). On WSL the wrapper adds `/usr/lib/wsl/lib` for CUDA.

## How it decides quality

- Text width is measured with the real font file (Pillow + raqm); Thai is split with pythainlp
  `newmm`, so lines never break inside a word. Too long → shrink to a floor → ellipsis + WARN.
- Contrast: each picture is also rendered without its text group; the worst 10 % of background
  pixels under every text box must reach WCAG 3:1 (titles, large text) or 4.5:1.
- `art final` reads the gateway token from `$CLAUDE_ART_TOKEN` or Infisical `/ai-media`
  `CLAUDE_ART_TOKEN` at run time and never prints it. Spend is capped per subject by the gateway.
- `art 3d` normalises any glb/gltf/obj/fbx/stl/ply/usd/blend model, lights it with three area lights
  over a shadow-catcher floor, fits the camera to width and height, renders RGBA on CUDA with GPU
  denoising, then composites the background. Logos use the Standard view so brand colours stay exact.

## Test

```bash
uv pip install -e ".[dev]" && python -m pytest
```

## Known gaps

- `split` ignores `logo` (poster and card place it); add a panel slot when a client needs it.
- `art check DIR` re-reads the absolute paths stored in `report.json`, so a moved output folder
  reports files as missing; re-run `art batch` there instead.
