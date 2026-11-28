#!/usr/bin/env bash
# Install the `art` workstation toolkit without sudo (WSL/Linux, NVIDIA optional).
#   ./install.sh            # venv + art command + fonts + resvg (+ GPU libs when nvidia-smi works)
#   ART_GPU=0 ./install.sh  # skip torch/diffusers
# Puts everything under ~/.local/opt and ~/.local/bin; re-running is safe.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
opt="$HOME/.local/opt"
bin="$HOME/.local/bin"
venv="$opt/art-venv"
fonts="$HOME/.local/share/fonts/google"
mkdir -p "$opt" "$bin" "$fonts"
command -v uv >/dev/null || { echo "need uv (https://docs.astral.sh/uv/)"; exit 1; }

gpu_smi="$(command -v nvidia-smi || true)"
[ -z "$gpu_smi" ] && [ -x /usr/lib/wsl/lib/nvidia-smi ] && gpu_smi=/usr/lib/wsl/lib/nvidia-smi
gpu="${ART_GPU:-auto}"
if [ "$gpu" = auto ]; then gpu=0; [ -n "$gpu_smi" ] && "$gpu_smi" -L >/dev/null 2>&1 && gpu=1; fi

[ -x "$venv/bin/python" ] || uv venv "$venv" --python 3.12 -q
export VIRTUAL_ENV="$venv"
uv pip install -q -e "$here[dev,extras]"
if [ "$gpu" = 1 ]; then
  uv pip install -q torch torchvision --index-url https://download.pytorch.org/whl/cu128 \
    --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match
  # same CUDA index again, so resolving the gpu extra never swaps torch for the CPU wheel from PyPI
  uv pip install -q -e "$here[gpu]" --index-url https://download.pytorch.org/whl/cu128 \
    --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match
fi

# `art` wrapper: WSL keeps libcuda/nvidia-smi in /usr/lib/wsl/lib
cat > "$bin/art" <<EOF
#!/bin/sh
export PATH=/usr/lib/wsl/lib:$bin:\$PATH
exec "$venv/bin/art" "\$@"
EOF
cat > "$bin/art-python" <<EOF
#!/bin/sh
export PATH=/usr/lib/wsl/lib:\$PATH
exec "$venv/bin/python" "\$@"
EOF
chmod +x "$bin/art" "$bin/art-python"
[ -x /usr/lib/wsl/lib/nvidia-smi ] && ln -sf /usr/lib/wsl/lib/nvidia-smi "$bin/nvidia-smi"

# resvg (text/template renderer)
if ! command -v resvg >/dev/null; then
  ver=v0.50.0
  tmp="$(mktemp -d)"
  curl -fsSL "https://github.com/linebender/resvg/releases/download/$ver/resvg-linux-x86_64.tar.gz" | tar -xz -C "$tmp"
  mkdir -p "$opt/resvg" && mv "$tmp/resvg" "$opt/resvg/resvg" && ln -sf "$opt/resvg/resvg" "$bin/resvg"
  rm -rf "$tmp"
fi

# Thai + Latin fonts (OFL, Google Fonts)
base=https://raw.githubusercontent.com/google/fonts/main/ofl
for p in "notosansthai/NotoSansThai%5Bwdth,wght%5D.ttf" "notoserifthai/NotoSerifThai%5Bwdth,wght%5D.ttf" \
         "notosans/NotoSans%5Bwdth,wght%5D.ttf" "ibmplexsansthai/IBMPlexSansThai-Regular.ttf" \
         "ibmplexsansthai/IBMPlexSansThai-Bold.ttf" "prompt/Prompt-Regular.ttf" "prompt/Prompt-Bold.ttf" \
         "kanit/Kanit-Regular.ttf" "kanit/Kanit-Bold.ttf" "inter/Inter%5Bopsz,wght%5D.ttf"; do
  name="$(basename "$p" | sed 's/%5B/[/;s/%5D/]/')"
  [ -f "$fonts/$name" ] || curl -fsSL -o "$fonts/$name" "$base/$p" || echo "font skipped: $name"
done
fc-cache -f >/dev/null 2>&1 || true

echo "art installed (gpu=$gpu). Optional runtimes: blender (art 3d), ffmpeg, inkscape (art 3d logo --text)."
for t in blender ffmpeg inkscape resvg; do printf '  %-9s %s\n' "$t" "$(command -v $t || echo missing)"; done
