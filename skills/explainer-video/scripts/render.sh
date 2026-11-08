#!/usr/bin/env bash
# Renders VIDEO_DIR/scenes/*.py (one Narrated scene per file) in file-name order.
#   render.sh preview VIDEO_DIR [SCENE_FILE...]  480p15 renders and contact sheets in VIDEO_DIR/build/sheets
#   render.sh final VIDEO_DIR SLUG               1080p60 video, loudness-normalised, with chapters,
#                                                at $EXPLAINER_VIDEOS/<YYYYMMDD-HHMM>-SLUG.mp4
#                                                EXPLAINER_CHAPTERS_FILE=on also writes a YouTube chapter list next to it
set -euo pipefail

SCRIPTS="$(dirname "$(readlink -f "$0")")"
HOME_DIR="${EXPLAINER_HOME:-$HOME/.cache/explainer-video}"
# One folder for every video of this skill; the time prefix sorts them by date.
VIDEOS="${EXPLAINER_VIDEOS:-$HOME/Videos/explainer-video}"
export MANIM="$HOME_DIR/venv/bin/manim"
export PYTHONPATH="$SCRIPTS${PYTHONPATH:+:$PYTHONPATH}"
# Each process may load the 300 MB voice model, so parallelism stays below the core count.
half=$(( $(nproc) / 2 ))
JOBS="${JOBS:-$(( half < 1 ? 1 : half > 8 ? 8 : half ))}"

mode="${1:?usage: render.sh preview|final VIDEO_DIR ...}"
VIDEO_DIR="$(readlink -f "${2:?missing VIDEO_DIR}")"
shift 2
cd "$VIDEO_DIR"

scene_files() {
  if (( $# )); then readlink -f "$@"; else find "$VIDEO_DIR/scenes" -maxdepth 1 -name '*.py' | sort; fi
}

render() {
  export QUALITY=$1 ROOT=$2
  mkdir -p "$ROOT"
  shift 2
  printf '%s\n' "$@" | xargs -P "$JOBS" -n 1 bash -c '
    n=$(basename "$1" .py)
    rm -rf "${ROOT:?}/$n"
    if "$MANIM" -q"$QUALITY" --disable_caching --media_dir "$ROOT/$n" "$1" -a >"$ROOT/$n.log" 2>&1; then
      echo "rendered $n"
    else
      echo "FAILED $n: see $ROOT/$n.log" >&2; exit 1
    fi' _
}

scene_mp4() {
  find "$1/$(basename "$2" .py)/videos" -name '*.mp4' -not -path '*partial_movie_files*' | head -n 1
}

preview() {
  mapfile -t files < <(scene_files "$@")
  render l "$VIDEO_DIR/build/preview" "${files[@]}"
  mkdir -p build/sheets
  for f in "${files[@]}"; do
    n=$(basename "$f" .py)
    rm -f build/sheets/"$n"-*.png
    ffmpeg -v error -i "$(scene_mp4 "$VIDEO_DIR/build/preview" "$f")" \
      -vf "fps=1/2,scale=640:-1,drawtext=text='%{pts\:hms}':x=w-tw-8:y=8:fontsize=18:fontcolor=yellow,tile=4x3" \
      build/sheets/"$n"-%02d.png
  done
  echo "sheets: $VIDEO_DIR/build/sheets (one frame every 2 s, timestamp top-right)"
}

final() {
  local out="$VIDEOS/$(date +%Y%m%d-%H%M)-${1:?missing SLUG}.mp4"
  mkdir -p "$VIDEOS"
  mapfile -t files < <(scene_files)
  render h "$VIDEO_DIR/build/final" "${files[@]}"

  # Manim ends a scene's audio at its last sound. Padding every scene to its video length
  # keeps the voice in sync after the join.
  mkdir -p build/join
  : > build/join/list.txt
  printf ';FFMETADATA1\n' > build/join/chapters.txt
  : > build/join/youtube.txt
  local start=0
  for f in "${files[@]}"; do
    n=$(basename "$f" .py)
    src=$(scene_mp4 "$VIDEO_DIR/build/final" "$f")
    dst="$VIDEO_DIR/build/join/$n.mp4"
    if [[ -n "$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$src")" ]]; then
      ffmpeg -y -v error -i "$src" -c:v copy -af apad -shortest -ar 48000 -ac 2 -c:a aac -b:a 192k "$dst"
    else
      ffmpeg -y -v error -i "$src" -f lavfi -i anullsrc=r=48000:cl=stereo -c:v copy -shortest -c:a aac -b:a 192k "$dst"
    fi
    echo "file '$dst'" >> build/join/list.txt
    dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$dst")
    title=$(sed -n 's/^TITLE = "\(.*\)"$/\1/p' "$f" | head -n 1)
    end=$(awk -v s="$start" -v d="$dur" 'BEGIN { printf "%d", s + d * 1000 }')
    printf '[CHAPTER]\nTIMEBASE=1/1000\nSTART=%s\nEND=%s\ntitle=%s\n' "$start" "$end" "${title:-$n}" >> build/join/chapters.txt
    awk -v s="$start" -v t="${title:-$n}" 'BEGIN { s = int(s / 1000); printf "%d:%02d %s\n", s / 60, s % 60, t }' >> build/join/youtube.txt
    start=$end
  done

  # Two-pass loudnorm: the first pass measures, the second applies one linear gain to -16 LUFS.
  local m
  m=$(ffmpeg -v info -f concat -safe 0 -i build/join/list.txt -vn \
        -af loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json -f null - 2>&1 | sed -n '/^{/,/^}/p')
  read -r mi mtp mlra mth off < <("$HOME_DIR/venv/bin/python" -c '
import json, sys
j = json.loads(sys.stdin.read())
print(j["input_i"], j["input_tp"], j["input_lra"], j["input_thresh"], j["target_offset"])' <<<"$m")
  ffmpeg -y -v error -f concat -safe 0 -i build/join/list.txt -i build/join/chapters.txt \
    -map 0:v -map 0:a -map_metadata 1 -map_chapters 1 -c:v copy \
    -af "loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=$mi:measured_TP=$mtp:measured_LRA=$mlra:measured_thresh=$mth:offset=$off:linear=true" \
    -ar 48000 -c:a aac -b:a 192k -movflags +faststart "$out"
  echo "video: $out"
  # The chapters are already in the mp4. The text file is only for a YouTube description.
  if [[ "${EXPLAINER_CHAPTERS_FILE:-off}" == on ]]; then
    cp build/join/youtube.txt "${out%.mp4}.chapters.txt"
    echo "chapters file: ${out%.mp4}.chapters.txt"
  fi
  echo "chapters:"
  cat build/join/youtube.txt
}

case "$mode" in
  preview) preview "$@" ;;
  final) final "$@" ;;
  *) echo "unknown mode: $mode" >&2; exit 2 ;;
esac
