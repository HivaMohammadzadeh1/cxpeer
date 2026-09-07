# cxpeer

cxpeer makes a running Codex CLI session show up as a peer in Claude Code's
cross-session messaging, so a Claude session can message it with SendMessage and
Codex's answer comes back automatically. It is a one-day proof of concept for macOS;
the design lives in `docs/superpowers/specs/2026-09-07-cxpeer-design.md`.

## Install

Install the package, then register the Codex hooks:

    uv tool install --editable .      # or: pipx install -e .
    cxpeer install

`cxpeer install` merges four hook entries into `~/.codex/hooks.json`
(`SessionStart`, `UserPromptSubmit`, `Stop`, `SessionEnd`) and writes the Codex skill
to `~/.codex/skills/cxpeer/SKILL.md`, which tells Codex when to run `cxpeer list` and
`cxpeer send`. It never duplicates its own entries and never touches hooks it did not
write. Run `cxpeer install --dry-run` first to see the exact files it would write.

Requires Python 3.11+ and zero runtime dependencies. `cxpeer spawn` also needs `tmux`.

## How it works

There is one bridge process per Codex session. It is the Codex session's identity
inside Claude's peer system.

    Claude session --SendMessage--> bridge socket --codex queue--> Codex TUI
    Claude session <---reply------- bridge <--Stop hook (turn_ended)-- Codex TUI

1. When a Codex session starts, the `SessionStart` hook spawns a bridge for that
   session and watches the Codex pid. The bridge writes the three files Claude Code's
   peer registry expects (a `<pid>.json` record, a key file, and a Unix socket under
   `/tmp/cc-socks`), so the Codex session appears in Claude's `ListAgents`.
2. A Claude session sends a message with SendMessage. It lands on the bridge's socket.
   The bridge runs `codex queue`, which auto-submits the text into the idle Codex TUI
   in about three seconds. No daemon and no polling.
3. The queued text carries a hidden `[cxpeer msg_id=...]` marker. When Codex submits
   that turn, the `UserPromptSubmit` hook reports the marker to the bridge; when the
   turn ends, the `Stop` hook hands the bridge Codex's final message. The bridge
   forwards it back to the Claude session that sent the original, as a reply.
4. From the Codex side, `cxpeer send --to NAME "text"` asks the bridge to relay a
   message to any peer; `cxpeer list` shows the names.
5. When the Codex session ends, the `SessionEnd` hook tells the bridge to deregister
   and exit. If Codex exits without firing the hook, the bridge notices its watched
   pid is gone and cleans up within a few seconds.

## Commands

- `cxpeer list` — one row per live peer: `name [ref]  status  cwd`.
- `cxpeer send --to NAME TEXT` — message a peer by name or ref. `TEXT` may be `-` to
  read from stdin. Routes through this session's bridge so the reply can come back; if
  there is no bridge it delivers directly and warns that replies cannot be routed.
- `cxpeer status` — list every bridge and whether it still answers.
- `cxpeer install [--dry-run]` — install or update the Codex hooks and skill.
- `cxpeer spawn codex|claude [--cwd DIR] [--name NAME] [--prompt TEXT] [-- ARGS...]` —
  start a new Codex or Claude session in a detached tmux session. Anything after `--`
  is passed to the underlying `codex`/`claude` command verbatim.
- `cxpeer bridge --thread ID --cwd DIR [--name NAME] [--watch-pid PID]` — run a bridge.
  Started by the `SessionStart` hook; you do not normally run it yourself.
- `cxpeer hook EVENT` — handle one Codex hook event, with the payload as JSON on stdin.
  Invoked by Codex through the installed hooks. It never fails a turn: on any error it
  logs and exits 0.

## Environment overrides

Every filesystem location has an override, so tests and alternate setups never touch
real state. The four in `cxpeer/paths.py`:

- `CXPEER_CLAUDE_SESSIONS_DIR` — Claude's peer registry dir (default `~/.claude/sessions`).
- `CXPEER_SOCK_DIR` — peer socket dir (default `/tmp/cc-socks`).
- `CXPEER_HOME` — cxpeer's own state: bridge records and logs (default `~/.cxpeer`).
- `CXPEER_CODEX_BIN` — the `codex` executable (default `codex`).

Two more, used by their commands: `CXPEER_CODEX_HOME` (default `~/.codex`, where
`install` writes) and `CXPEER_TMUX_BIN` (default `tmux`, used by `spawn`).

## Tests

    .venv/bin/python -m pytest tests -q

Tests are isolated by the fixtures in `tests/conftest.py` and never touch
`~/.claude/sessions`, `/tmp/cc-socks`, `~/.cxpeer`, or the real `codex`.

## Limits

- cxpeer builds on an undocumented Claude Code internal (`peerProtocol` 1). A Claude
  Code update can break it without warning. This is acceptable for a proof of concept
  and is not a dependency for anything else.
- macOS only. Liveness uses `ps -o lstart=` and the registry assumes a darwin pid
  domain; Windows is not supported.
- Out of scope: file attachments, idle notifications, multi-machine or bridge-to-bridge
  relays, Codex `exec` (non-interactive) mode, message history, and any retry beyond a
  single delivery attempt.
