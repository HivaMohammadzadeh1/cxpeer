#!/usr/bin/env bash
# Right-hand pane of the journey recording: wait for the Codex tmux session that demo/journey.py
# spawns, attach to it so the viewer sees the message land, and exit when that session ends.
start=$(date +%s)
printf '\n  the Codex side\n  waiting for the journey to start a Codex session ...\n'
for _ in $(seq 1 180); do
  s=$(tmux list-sessions -F '#{session_name} #{session_created}' 2>/dev/null | awk -v t="$start" '$1 ~ /^cx-codex-/ && $2 >= t {print $1; exit}')
  if [ -n "$s" ]; then
    tmux set-option -t "$s" status off 2>/dev/null || true
    env TMUX= tmux attach -t "$s" 2>/dev/null
    printf '\n  Codex session ended.\n'
    sleep 4
    exit 0
  fi
  sleep 1
done
printf '\n  no Codex session appeared.\n'
exit 1
