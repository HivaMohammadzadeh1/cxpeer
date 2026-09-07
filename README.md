# cxpeer

cxpeer makes a running Codex CLI session show up as a peer in Claude Code's
cross-session messaging, so a Claude session can message it with SendMessage and
Codex's answer comes back automatically. It is a one-day proof of concept for macOS;
the design lives in `docs/superpowers/specs/2026-09-07-cxpeer-design.md`.

## Install

Install the package, then register the Codex hooks:

    uv tool install git+https://github.com/HivaMohammadzadeh1/cxpeer   # or: pipx install git+https://github.com/HivaMohammadzadeh1/cxpeer
    cxpeer install

From a checkout, use `uv tool install --editable .` or `pipx install -e .` instead.

`cxpeer install` merges five hook entries into `~/.codex/hooks.json`
(`SessionStart`, `UserPromptSubmit`, `Stop`, `Interrupt`, `SessionEnd`) and writes the
Codex skill to `~/.codex/skills/cxpeer/SKILL.md`, which tells Codex when to run
`cxpeer list` and `cxpeer send`. It never duplicates its own entries and never touches
hooks it did not write. Run `cxpeer install --dry-run` first to see the exact files it
would write. Run `cxpeer install` again after upgrading cxpeer; hook entries added in a
newer version are not picked up otherwise.

The next time Codex starts it asks once to trust the new hooks ("4 hooks are new or
changed"). Choose "Trust all and continue"; without that the hooks do not run and no
bridge is created.

Requires Python 3.11+ and zero runtime dependencies. `cxpeer spawn` also needs `tmux`.

### Claude Code side (optional)

The Codex side needs the hooks above. A Claude Code session needs nothing extra: Codex
peers appear in `ListAgents` like any other session. A small skill that explains how to
start and message Codex peers ships as a Claude Code plugin:

    claude plugin marketplace add HivaMohammadzadeh1/cxpeer
    claude plugin install cxpeer@cxpeer

Inside a running Claude session the same two are `/plugin marketplace add
HivaMohammadzadeh1/cxpeer` and `/plugin install cxpeer@cxpeer`.

## How it works

There is one bridge process per Codex session. It is the Codex session's identity
inside Claude's peer system.

    Claude session --SendMessage--> bridge socket --codex queue--> Codex TUI
    Claude session <---reply------- bridge <--Stop hook (turn_ended)-- Codex TUI

1. When a Codex session starts, the `SessionStart` hook spawns a bridge for that
   session and watches the Codex pid. The bridge writes the three files Claude Code's
   peer registry expects (a `<pid>.json` record, a key file, and a Unix socket under
   `/tmp/cc-socks`), so the Codex session appears in Claude's `ListAgents` as
   `codex-<dir>-<xx>`. Codex creates the session lazily, so this happens when you
   submit the first prompt, not when the TUI opens.
2. A Claude session sends a message with SendMessage. It lands on the bridge's socket.
   The bridge runs `codex queue`, which auto-submits the text into the idle Codex TUI
   in about three seconds. No daemon and no polling.
3. The queued text carries a `[cxpeer msg_id=...]` marker. When Codex submits that
   turn, the `UserPromptSubmit` hook reports the marker to the bridge; when the turn
   ends, the `Stop` hook hands the bridge Codex's final message. The bridge forwards
   it back to the Claude session that sent the original, as a reply. A prompt typed by
   the human carries no marker, so a human turn is never forwarded to a peer. If the
   turn is interrupted, the `Interrupt` hook makes the bridge tell the sender that
   instead. A request that never pairs with a turn expires after 15 minutes with a
   note to the sender.
4. From the Codex side, `cxpeer send --to NAME "text"` asks the bridge to relay a
   message to any peer; `cxpeer list` shows the names. Codex runs shell commands in a
   sandbox where `ps` cannot execute and Unix sockets cannot be opened, so inside Codex
   these two commands do not touch the bridge socket: `send` drops a request file into
   `/tmp/cxpeer-<uid>/<thread>/`, which the bridge polls every second and answers with a
   result file, and `list` reads a peers snapshot the bridge refreshes every four
   seconds. Outside the sandbox the same commands use the socket directly.
5. When the Codex session ends, the `SessionEnd` hook tells the bridge to deregister
   and exit. If Codex exits without firing the hook, the bridge notices its watched
   pid is gone and cleans up within a few seconds.

## Commands

- `cxpeer list` — one row per live peer: `name [ref]  status  cwd`.
- `cxpeer send --to NAME TEXT` — message a peer by name or ref. `TEXT` may be `-` to
  read from stdin. Routes through this session's bridge so the reply can come back
  (over the socket, or through the file outbox when sockets are blocked); if there is
  no bridge it delivers directly and warns that replies cannot be routed.
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
- `CXPEER_OUTBOX_ROOT` — where sandboxed `cxpeer send` drops requests (default
  `/tmp/cxpeer-<uid>`; `/tmp` is writable inside the Codex sandbox, `~/.cxpeer` is not).

Three more, used by their commands: `CXPEER_CODEX_HOME` (default `~/.codex`, where
`install` writes), `CXPEER_TMUX_BIN` (default `tmux`, used by `spawn`), and
`CXPEER_PENDING_TTL_SECONDS` (default 900, how long the bridge waits for a turn to pair
with a request before telling the sender to give up).

Logs: `~/.cxpeer/logs/hooks.log` for the hooks and `~/.cxpeer/logs/<thread>.log` per
bridge. When a message does not arrive, read those first.

## Tests

    .venv/bin/python -m pytest tests -q

Tests are isolated by the fixtures in `tests/conftest.py` and never touch
`~/.claude/sessions`, `/tmp/cc-socks`, `~/.cxpeer`, or the real `codex`.

For a live check against a real Codex TUI, `scripts/e2e.sh [DIR]` starts one in a tmux
session, answers Codex's startup prompts, sends a first prompt so the bridge appears,
and prints the peer name to message from a Claude session. `scripts/e2e.sh down` tears
it down.

## Limits

- cxpeer builds on an undocumented Claude Code internal (`peerProtocol` 1). A Claude
  Code update can break it without warning. This is acceptable for a proof of concept
  and is not a dependency for anything else.
- macOS only. Liveness uses `ps -o lstart=` and the registry assumes a darwin pid
  domain; Windows is not supported.
- Out of scope: file attachments, idle notifications, multi-machine or bridge-to-bridge
  relays, Codex `exec` (non-interactive) mode, message history, and any retry beyond a
  single delivery attempt.
- Peer messages are plain text between processes of the same user. The outbox files
  under `/tmp/cxpeer-<uid>` are created with the writer's umask; treat them as readable
  by anything running as you.
