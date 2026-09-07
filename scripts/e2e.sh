#!/usr/bin/env bash
# scripts/e2e.sh: bring up a real Codex session as a cxpeer peer and say how to talk to it.
#
#   scripts/e2e.sh [DIR]   start codex in tmux session cx-e2e at DIR (default: current dir),
#                          answer its startup prompts, send a first prompt, wait for the bridge
#   scripts/e2e.sh down    kill the tmux session and show cxpeer status afterwards
#   scripts/e2e.sh --help  show this text
#
# Needs cxpeer, codex, and tmux on PATH. Override the tmux session name with CXPEER_E2E_SESSION.
set -euo pipefail

SESSION="${CXPEER_E2E_SESSION:-cx-e2e}"
PROMPT='Say READY and nothing else.'
TIMEOUT=30

usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "e2e: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 not found on PATH${2:-}"; }
pane() { tmux capture-pane -p -t "$SESSION" 2>/dev/null || true; }
keys() { tmux send-keys -t "$SESSION" "$@"; }

up() {
  local dir="$1"
  [[ -d "$dir" ]] || die "not a directory: $dir"
  dir="$(cd "$dir" && pwd)"
  local real; real="$(cd "$dir" && pwd -P)"
  need cxpeer " (install: uv tool install git+https://github.com/HivaMohammadzadeh1/cxpeer)"
  need codex
  need tmux
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    die "tmux session $SESSION already exists; run '$0 down' first, or look at it: tmux attach -t $SESSION"
  fi
  local bin; bin="$(dirname "$(command -v cxpeer)")"

  echo "starting codex in tmux session $SESSION at $dir (cxpeer from $bin)"
  tmux new-session -d -s "$SESSION" -x 150 -y 42 -c "$dir" "PATH=$bin:\$PATH codex"

  # Codex may ask up to three things on startup. Answer each once, stop at the input box.
  local i screen update_done='' trust_done='' hooks_done=''
  for ((i = 0; i < TIMEOUT; i++)); do
    screen="$(pane)"
    if grep -q "Ask Codex to do anything" <<<"$screen"; then break; fi
    if [[ -z $update_done ]] && grep -q "Update available" <<<"$screen"; then
      echo "  update prompt: skipping"; keys 2 Enter; update_done=1
    elif [[ -z $trust_done ]] && grep -q "Do you trust the contents" <<<"$screen"; then
      echo "  directory trust prompt: trusting"; keys 1 Enter; trust_done=1
    elif [[ -z $hooks_done ]] && grep -q "hooks are new or changed" <<<"$screen"; then
      echo "  hooks trust prompt: trust all and continue"; keys 2 Enter; hooks_done=1
    fi
    sleep 1
  done
  grep -q "Ask Codex to do anything" <<<"$(pane)" \
    || die "codex input box did not appear within ${TIMEOUT}s; inspect with: tmux attach -t $SESSION"

  # The SessionStart hook fires on the first prompt, not at launch. The second Enter is
  # needed because the first one after typing did not submit in practice.
  echo "sending first prompt so the SessionStart hook starts the bridge"
  keys "$PROMPT" Enter
  sleep 2
  keys Enter

  local line=''
  for ((i = 0; i < TIMEOUT; i++)); do
    line="$(cxpeer status 2>/dev/null | awk -v a="$dir" -v b="$real" '/pid=/ && ($NF == a || $NF == b)' | head -n 1)"
    [[ -n $line ]] && break
    sleep 1
  done
  if [[ -z $line ]]; then
    echo "cxpeer status:"; cxpeer status || true
    die "no bridge for $dir after ${TIMEOUT}s; check ~/.cxpeer/logs/hooks.log and: tmux attach -t $SESSION"
  fi

  local name="${line%% *}"
  echo
  echo "bridge:    $line"
  echo "peer name: $name"
  echo "attach:    tmux attach -t $SESSION"
  echo "From a Claude Code session, run ListAgents and SendMessage to $name; the reply arrives as a cross-session message."
}

down() {
  need cxpeer
  need tmux
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION"
    echo "killed tmux session $SESSION; the bridge watches the codex pid and exits within ~5s"
  else
    echo "no tmux session named $SESSION"
  fi
  sleep 5
  cxpeer status
}

[[ $# -le 1 ]] || die "too many arguments (see --help)"
case "${1:-}" in
  -h|--help) usage ;;
  down) down ;;
  -*) die "unknown option: $1 (see --help)" ;;
  *) up "${1:-$PWD}" ;;
esac
