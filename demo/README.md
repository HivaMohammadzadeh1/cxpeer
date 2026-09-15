# Demo

`demo/journey.py` plays the Claude side of the loop without a Claude Code session: it
registers a temporary peer, spawns a real Codex peer with `cxpeer spawn --wait`, sends it
two tasks, and waits for the answers and the idle notices on its own socket. The
recording in the README is this script next to the Codex TUI it spawned.

Run it from a checkout after `cxpeer install` and a green `cxpeer doctor`:

```sh
.venv/bin/python demo/journey.py            # narrated
.venv/bin/python demo/journey.py --plain    # no narration
```

To re-record the video: `scripts/record-journey.sh` (asciinema, two tmux panes), then
`scripts/make-demo-video.sh docs/journey.cast docs/images/journey` (agg and ffmpeg).
