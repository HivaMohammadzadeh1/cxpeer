# cxpeer

[![CI](https://github.com/HivaMohammadzadeh1/cxpeer/actions/workflows/test.yml/badge.svg)](https://github.com/HivaMohammadzadeh1/cxpeer/actions/workflows/test.yml)
License: [PolyForm Noncommercial 1.0.0](LICENSE)

Makes a running Codex CLI session show up as a peer in Claude Code's cross-session
messaging. A Claude session can `SendMessage` it, and Codex's answer comes back when
the turn ends. Codex can message Claude sessions too.

It works by writing the three files Claude Code uses to find peers (a record, a key,
a Unix socket) on behalf of the Codex session, pushing inbound messages into Codex
with `codex queue`, and reading turn boundaries from Codex's hooks. No daemon.

![how a Claude session talks to a Codex session](docs/images/topology.png)

macOS, Python 3.11+, Codex CLI 0.153+, Claude Code 2.1.263+. Zero dependencies.
`cxpeer spawn` needs tmux.

## Install

```sh
uv tool install git+https://github.com/HivaMohammadzadeh1/cxpeer   # or pipx
cxpeer install
cxpeer doctor
```

`cxpeer install` adds five hook entries to `~/.codex/hooks.json` (SessionStart,
UserPromptSubmit, Stop, Interrupt, SessionEnd) and writes a small skill to
`~/.codex/skills/cxpeer/SKILL.md` so Codex knows about `cxpeer send` and `cxpeer list`.
It leaves hooks it did not write alone. `--dry-run` shows what it would write. Run it
again after upgrading.

The next time Codex starts it asks whether to trust the new hooks. Pick "Trust all and
continue", otherwise the hooks never run and no bridge is created.

`cxpeer doctor` checks both CLIs, the hooks, the trust record, and then starts a bridge
in a temp dir and messages it. If it is all green the peer path works. Run it first
whenever a peer does not show up.

From a checkout: `uv tool install --editable .` or `pipx install -e .`.

## Use

From a Claude Code session:

```sh
cxpeer spawn codex --cwd ~/proj --peer-name codex-tests --prompt "run the test suite and report" --wait
```

This starts Codex in a detached tmux session, clicks through its startup prompts,
submits the prompt, and returns once the peer is registered. Then `ListAgents` shows
`codex-tests`, and `SendMessage` to it works like any other session. The final message
of each Codex turn comes back as a cross-session message. Add `notify_when_idle` to the
SendMessage and you also get an idle notice when the turn ends.

You can also just run `codex` yourself. The bridge appears when you submit the first
prompt (Codex creates the session lazily) and the peer is named `codex-<dir>-<xx>`.

From inside Codex, `cxpeer list` prints the live peers and
`cxpeer send --to NAME "text"` messages one of them.

There is a Claude Code plugin with a skill that explains the above to Claude:

```sh
claude plugin marketplace add HivaMohammadzadeh1/cxpeer
claude plugin install cxpeer@cxpeer
```

## How it works

One bridge process per Codex session.

![message sequence](docs/images/sequence.png)

1. The SessionStart hook starts a bridge and tells it the Codex pid to watch.
2. A Claude session sends a message. It lands on the bridge's socket. The bridge runs
   `codex queue`, and the TUI picks the text up when idle, about three seconds later.
3. The queued text ends with a `[cxpeer msg_id=...]` marker. The UserPromptSubmit hook
   reports the marker back, so the bridge knows which request this turn belongs to.
   When the turn ends, the Stop hook hands over Codex's last message and the bridge
   forwards it to whoever asked. A prompt typed by a human has no marker, so it is never
   forwarded anywhere. An interrupted turn tells the sender that. A request that never
   gets a turn expires after 15 minutes, with a note.
4. `cxpeer send` from inside Codex asks the bridge to relay to a peer.
5. `notify_when_idle` subscriptions get one notice when the turn ends (or the session
   exits).
6. SessionEnd tells the bridge to deregister. If Codex dies without the hook, the bridge
   sees the pid go away and cleans up. If the bridge dies first, the next hook event
   starts a new one.

Timing from a real run on 2026-09-07: a SendMessage was answered in 8 s, mostly Codex
thinking. A `cxpeer send` from inside Codex arrived in Claude 10 s after the request.

## Commands

- `cxpeer list`: live peers, one per line.
- `cxpeer send --to NAME TEXT`: message a peer. `TEXT` can be `-` for stdin. Goes
  through the local bridge so the reply has a route back.
- `cxpeer status`: every bridge, whether it answers, requests waiting for a turn.
- `cxpeer doctor`: the checks described above.
- `cxpeer install [--dry-run]`: install or update the hooks and skill.
- `cxpeer spawn codex|claude [--cwd DIR] [--name N] [--peer-name N] [--prompt TEXT]
  [--wait] [--timeout S] [-- ARGS...]`: start a session in tmux. Args after `--` go to
  the child command as is.
- `cxpeer bridge --thread ID --cwd DIR [--name N] [--watch-pid PID]`: run a bridge.
  The hook does this; you rarely will.
- `cxpeer hook EVENT`: what the hooks call. Reads the payload from stdin, never exits
  non-zero.

## Inside the bridge

About 400 lines in `cxpeer/bridge.py`, stdlib only. The main thread binds the socket,
registers, and accepts connections. Each connection gets a thread that checks the auth
line and dispatches frames by type. State is one dict of pending requests, an "active"
pointer, and the idle/busy status, behind a lock.

![bridge internals](docs/images/bridge-internals.png)

The bridge never reads Codex's transcript. Pairing an answer with its request goes
through the marker only:

![request states](docs/images/request-states.png)

The on-disk contract, as of Claude Code 2.1.263:

```text
~/.claude/sessions/<pid>.json                       record: name, cwd, status, socket path, procStart
~/.claude/sessions/<pid>.<sha256(socket path)>.key  {"peerToken": <32 hex>, "procStart": ..., "pidDomain": "darwin"}  (0600)
/tmp/cc-socks/<pid>.sock                            the socket (0600)
```

Two JSON lines per message. First the receiver's token, then the message. `from` is
where replies go.

```json
{"type":"auth","token":"<receiver's peerToken>"}
{"msgV":1,"msg_id":"<uuid4>","type":"user","priority":"next","from":"uds:/tmp/cc-socks/<sender pid>.sock",
 "message":{"role":"user","content":"<cross-session-message from=\"uds:...\" from-name=\"codex-tests\" from-mode=\"prompting\">\nPONG\n</cross-session-message>"}}
```

Idle subscriptions are `{"type":"control","action":"notify_when_idle",...}` frames and
the bridge answers with `{"type":"control","action":"peer_idle_notice","state":"idle",...}`.

## The Codex sandbox

Codex runs shell commands in a sandbox. Inside it, `ps` cannot execute and Unix
sockets cannot be opened, but reading the home directory works and writes to `/tmp`,
`$TMPDIR`, and the cwd are allowed. So `cxpeer send` and `cxpeer list` inside Codex do
not touch the socket at all:

- `send` writes `{id, to, text}` to `/tmp/cxpeer-<uid>/<thread>/`. The bridge polls
  that directory every second, relays, and writes `<id>.result.json` back.
- `list` reads a peers snapshot the bridge rewrites every four seconds. Older than 15 s
  means no bridge.

The client switches to files on its own when a socket connect raises PermissionError
or `ps` fails. Outside the sandbox the same commands use the socket. A bridge started
by an older cxpeer has no outbox; a sandboxed `send` says so, restart Codex to fix it.

## Configuration

All paths can be overridden, which is how the tests stay away from real state.

| Variable | Default |
| --- | --- |
| `CXPEER_CLAUDE_SESSIONS_DIR` | `~/.claude/sessions` |
| `CXPEER_SOCK_DIR` | `/tmp/cc-socks` |
| `CXPEER_HOME` | `~/.cxpeer` (bridge records, snapshots, logs) |
| `CXPEER_OUTBOX_ROOT` | `/tmp/cxpeer-<uid>` |
| `CXPEER_CODEX_BIN` | `codex` |
| `CXPEER_CODEX_HOME` | `~/.codex` |
| `CXPEER_TMUX_BIN` | `tmux` |
| `CXPEER_PENDING_TTL_SECONDS` | `900` |

Logs go to `~/.cxpeer/logs/hooks.log` and `~/.cxpeer/logs/<thread>.log`.

## Development

```sh
uv venv .venv --python 3.13 && uv pip install -e ".[dev]" --python .venv/bin/python
.venv/bin/python -m pytest tests -q
```

The fixtures in `tests/conftest.py` point every path at a temp dir and swap `codex`
for a script that records its arguments, so nothing touches your real setup. The bridge
tests run it as a subprocess and talk to it over its socket. CI runs on macOS and
Ubuntu.

`scripts/e2e.sh [DIR]` starts a real Codex in tmux, gets it past the startup prompts,
and prints the peer name. `scripts/e2e.sh down` kills it.

Design notes: `docs/superpowers/specs/2026-09-07-cxpeer-design.md`.

## Caveats

- This sits on an undocumented Claude Code internal (`peerProtocol` 1). An update can
  break it. The tests pin the current contract, so at least it breaks loudly.
- Only used on macOS so far. Tests pass on Linux in CI but nobody has run a Linux peer
  by hand. No Windows.
- Messages are plain text between processes running as you. Outbox files in
  `/tmp/cxpeer-<uid>` get your umask, so anything running as your user can read them.
- Not done: file attachments, multiple machines, `codex exec` mode, message history,
  retries.

## License

PolyForm Noncommercial 1.0.0. Free for personal use, research, teaching, nonprofits,
and government. No warranty. Commercial use needs a separate license; open an issue or
contact [Hiva Mohammadzadeh](https://github.com/HivaMohammadzadeh1).
