#!/usr/bin/env bash
# Record demo/journey.py side by side with the Codex TUI it spawns.
#   scripts/record-journey.sh [docs/journey.cast]   then   agg docs/journey.cast docs/images/journey.gif
# Needs asciinema, tmux, cxpeer (installed and `cxpeer install` done).
set -euo pipefail
OUT=${1:-docs/journey.cast}
SESSION=cxrec
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=python3

if [ -z "${CXREC_INNER:-}" ]; then
  for t in asciinema tmux cxpeer; do command -v "$t" >/dev/null || { echo "record-journey: $t not found" >&2; exit 1; }; done
  exec asciinema rec --overwrite --idle-time-limit 3 --window-size 170x46 --title cxpeer-journey \
    -c "CXREC_INNER=1 $0 $OUT" "$OUT"
fi

tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" -x 170 -y 46 -c "$ROOT" \
  "$PY demo/journey.py --record docs/user-journey.md; sleep 6"
tmux split-window -h -t "$SESSION" -l 74 -c "$ROOT" "$ROOT/scripts/attach-codex-pane.sh"
tmux select-pane -t "$SESSION:0.0"
tmux set-option -t "$SESSION" status off
exec env TMUX= tmux attach -t "$SESSION"
