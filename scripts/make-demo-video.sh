#!/usr/bin/env bash
# Turn a journey cast into a README GIF and a captioned MP4.
#   scripts/make-demo-video.sh docs/journey.cast docs/images/journey <intro.png> <outro.png> <bands dir>
# Bands dir holds band-<step>.png (1920x120) named by step number. Needs agg, ffmpeg, python3.
set -euo pipefail
CAST=$1; OUT=$2; INTRO=$3; OUTRO=$4; BANDS=$5
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
python3 "$ROOT/scripts/compress-cast.py" "$CAST" "$TMP/c.cast" --wait-cap 4 --dump-steps "$TMP/steps.json"
agg --theme monokai --font-size 16 --fps-cap 10 --last-frame-duration 4 "$TMP/c.cast" "$OUT.gif" >/dev/null 2>&1
# base video: gif -> 1920x1080 letterboxed on the terminal background
# terminal fitted into the top 1920x960, caption strip below it
ffmpeg -loglevel error -y -i "$OUT.gif" -vf "scale=1920:960:force_original_aspect_ratio=decrease:flags=lanczos,pad=1920:1080:(ow-iw)/2:0:color=#272822,format=yuv420p" -r 30 "$TMP/base.mp4"
# caption overlays from the step times
python3 - "$TMP/steps.json" "$BANDS" > "$TMP/filter.txt" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); bands = sys.argv[2]
steps = d["steps"]; end = d["duration"] + 4
inputs, chain, prev = [], [], "[0:v]"
for i, s in enumerate(steps):
    nxt = steps[i + 1]["at"] if i + 1 < len(steps) else end
    inputs.append(f"{bands}/band-{s['step']}.png")
    chain.append(f"{prev}[{i+1}:v]overlay=0:960:enable='between(t,{s['at']:.2f},{nxt:.2f})'[v{i}]")
    prev = f"[v{i}]"
print("\n".join(inputs)); print("FILTER " + ";".join(chain) + f";{prev}null[out]" if chain else "FILTER [0:v]null[out]")
PY
mapfile -t LINES < "$TMP/filter.txt"
FILTER=${LINES[-1]#FILTER }
ARGS=(-i "$TMP/base.mp4"); for f in "${LINES[@]}"; do [[ $f == FILTER* ]] || ARGS+=(-i "$f"); done
ffmpeg -loglevel error -y "${ARGS[@]}" -filter_complex "$FILTER" -map "[out]" -r 30 -pix_fmt yuv420p "$TMP/main.mp4"
# title and outro cards
ffmpeg -loglevel error -y -loop 1 -t 3.5 -i "$INTRO" -vf "format=yuv420p" -r 30 "$TMP/intro.mp4"
ffmpeg -loglevel error -y -loop 1 -t 5 -i "$OUTRO" -vf "format=yuv420p" -r 30 "$TMP/outro.mp4"
ffmpeg -loglevel error -y -i "$TMP/intro.mp4" -i "$TMP/main.mp4" -i "$TMP/outro.mp4" -filter_complex "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]" -map "[v]" -movflags faststart -pix_fmt yuv420p -c:v libx264 -crf 22 -preset medium "$OUT.mp4"
echo "gif: $(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT.gif")s $(wc -c < "$OUT.gif") bytes; mp4: $(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT.mp4")s $(wc -c < "$OUT.mp4") bytes"
rm -rf "$TMP"
