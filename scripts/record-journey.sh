#!/usr/bin/env bash
# Record demo/journey.py side by side with the Codex TUI it spawns.
#   scripts/record-journey.sh [docs/journey.cast]   then   agg docs/journey.cast docs/images/journey.gif
#   JOURNEY_ARGS="--codex-home ~/.codex-work --second-registry ~/.claude-work/sessions" for the two-account run;
#   TRANSCRIPT=docs/user-journey-accounts.md to write the transcript elsewhere.
# Needs asciinema, tmux, cxpeer (installed and `cxpeer install` done).
set -euo pipefail
OUT=${1:-docs/journey.cast}
SESSION=cxrec
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=python3

if [ -z "${CXREC_INNER:-}" ]; then
  for t in asciinema tmux cxpeer; do command -v "$t" >/dev/null || { echo "record-journey: $t not found" >&2; exit 1; }; done
  exec asciinema rec --overwrite --idle-time-limit 3 --window-size 170x46 --title cxpeer-journey \
    -c "CXREC_INNER=1 JOURNEY_ARGS='${JOURNEY_ARGS:-}' TRANSCRIPT='${TRANSCRIPT:-docs/user-journey.md}' $0 $OUT" "$OUT"
fi

tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" -x 170 -y 46 -c "$ROOT" \
  "$PY demo/journey.py --record ${TRANSCRIPT:-docs/user-journey.md} ${JOURNEY_ARGS:-}; sleep 6"
tmux split-window -h -t "$SESSION" -l 74 -c "$ROOT" "$ROOT/scripts/attach-codex-pane.sh"
tmux select-pane -t "$SESSION:0.0"
tmux set-option -t "$SESSION" status off
exec env TMUX= tmux attach -t "$SESSION"
