#!/usr/bin/env bash
# Prepares the shared toolchain: one venv and one Kokoro model for every video.
set -euo pipefail

HOME_DIR="${EXPLAINER_HOME:-$HOME/.cache/explainer-video}"
VENV="$HOME_DIR/venv"
MODEL_DIR="$HOME_DIR/kokoro"
MODEL_URL=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0

missing=()
for cmd in python3 ffmpeg ffprobe latex dvisvgm fc-list; do
  command -v "$cmd" >/dev/null || missing+=("$cmd")
done
for font in "DejaVu Sans Mono" "Fira Sans"; do
  [[ -n "$(fc-list "$font" family)" ]] || missing+=("font: $font")
done
if (( ${#missing[@]} )); then
  printf 'missing: %s\n' "${missing[@]}" >&2
  exit 1
fi

mkdir -p "$MODEL_DIR"
if ! "$VENV/bin/python" -c "import manim, kokoro_onnx, soundfile" 2>/dev/null; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q manim kokoro-onnx soundfile
fi
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
  if [[ ! -s "$MODEL_DIR/$f" ]]; then
    curl -fL --progress-bar -o "$MODEL_DIR/$f.part" "$MODEL_URL/$f"
    mv "$MODEL_DIR/$f.part" "$MODEL_DIR/$f"
  fi
done
echo ready
