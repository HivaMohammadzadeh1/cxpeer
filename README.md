<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/cxpeer-logo-dark.png">
    <img alt="cxpeer" src="docs/images/cxpeer-logo-light.png" width="380">
  </picture>
</p>

<h3 align="center">Codex CLI sessions as Claude Code peers</h3>

<p align="center">
<a href="https://github.com/HivaMohammadzadeh1/cxpeer/actions/workflows/test.yml"><img alt="CI" src="https://github.com/HivaMohammadzadeh1/cxpeer/actions/workflows/test.yml/badge.svg"></a>
<a href="LICENSE"><img alt="License: PolyForm Noncommercial 1.0.0" src="https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue.svg"></a>
<a href="pyproject.toml"><img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue.svg"></a>
<a href="https://hivam.org/cxpeer/"><img alt="Site" src="https://img.shields.io/badge/site-hivam.org%2Fcxpeer-0B7A63.svg"></a>
</p>

Claude Code sessions can message each other. cxpeer adds your Codex CLI sessions to that
list. A Claude session sends a task with `SendMessage`, Codex works on it in its own
context, and the final answer comes back when the turn ends. Codex can message Claude
sessions too, from inside its sandbox. No daemon, no polling.

<p align="center"><img src="docs/images/journey.gif" width="900" alt="A Claude session spawns a Codex peer, sends it a task, and gets the answer back; Codex then messages Claude from inside its sandbox"></p>
<p align="center">Left: the Claude side. Right: the Codex TUI. Real run, thinking time compressed. <a href="docs/images/journey.mp4">MP4 with captions</a>.</p>

## Install

Requirements: macOS or Linux, Python 3.11+, Codex CLI 0.153+, Claude Code 2.1.263+. `cxpeer spawn` needs tmux.

```sh
uv tool install git+https://github.com/HivaMohammadzadeh1/cxpeer   # or pipx
cxpeer install    # adds five hooks and a skill under ~/.codex
cxpeer doctor     # ten checks and a live self-test
```

The next time Codex starts it asks whether to trust the new hooks. Answer yes. Run
`cxpeer install` again after upgrading.

## Use

From a Claude Code session:

```sh
cxpeer spawn codex --cwd ~/proj --peer-name codex-tests --prompt "run the test suite and report" --wait
```

`ListAgents` now shows `codex-tests`. `SendMessage` to it as you would to any session;
each Codex turn's final message comes back as a cross-session message. Add
`notify_when_idle` to get an idle notice instead of asking whether it is done.

Any `codex` you start yourself becomes a peer too, named `codex-<dir>-<xx>`, as soon as
you submit its first prompt.

Optional Claude Code plugin with a skill that teaches Claude the workflow:

```sh
claude plugin marketplace add HivaMohammadzadeh1/cxpeer
claude plugin install cxpeer@cxpeer
```

## How it works

Claude Code finds peers through three files: a record in `~/.claude/sessions/`, a key
file with a token, and a Unix socket. Anything that writes those three is a peer. One
bridge process per Codex session does exactly that.

![how a Claude session talks to a Codex session](docs/images/topology.png)

1. The SessionStart hook starts the bridge. A per-thread lock keeps it to one per session.
2. A message from Claude lands on the bridge's socket. The bridge runs `codex queue`, and
   the idle TUI submits the text within about three seconds.
3. The queued text carries a `[cxpeer msg_id=…]` marker. The UserPromptSubmit hook reports
   it, so the bridge knows which request the turn belongs to. The Stop hook hands over
   Codex's last message and the bridge forwards it to whoever asked.
4. SessionEnd deregisters the peer. If Codex dies without it, the bridge notices the pid
   is gone. If the bridge dies, the next hook event starts a new one.

![message sequence](docs/images/sequence.png)

Prompts typed by a human carry no marker and are never forwarded. An interrupted turn
tells the sender. A request that never gets a turn expires after 15 minutes with a note.

### Inside the bridge

`cxpeer/bridge.py` is one process, standard library only. The main thread binds the
socket, registers, and accepts connections. Each connection gets a thread that checks
the auth line and dispatches by frame type. State is a table of pending requests, the
active one, the idle flag, and the idle subscriptions, behind a lock.

![bridge internals](docs/images/bridge-internals.png)

![request states](docs/images/request-states.png)

Codex's sandbox blocks `ps` and Unix sockets, so `cxpeer send` and `cxpeer list` inside
Codex use files instead: an outbox under `/tmp/cxpeer-<uid>/` that the bridge polls
every second, and a peers snapshot it rewrites every four seconds.

The on-disk contract, as of Claude Code 2.1.263:

```text
~/.claude/sessions/<pid>.json                       record: name, cwd, status, socket path, procStart
~/.claude/sessions/<pid>.<sha256(socket path)>.key  {"peerToken": <32 hex>, "procStart": ..., "pidDomain": "darwin"}
/tmp/cc-socks/<pid>.sock                            the socket
```

Two JSON lines per message: the receiver's token, then the message. `from` is the reply address.

```json
{"type":"auth","token":"<receiver's peerToken>"}
{"msgV":1,"msg_id":"<uuid4>","type":"user","priority":"next","from":"uds:/tmp/cc-socks/<sender pid>.sock",
 "message":{"role":"user","content":"<cross-session-message from=\"uds:...\" from-name=\"codex-tests\" from-mode=\"prompting\">\nPONG\n</cross-session-message>"}}
```

## Context budget

Every message a session receives is paid for on every later turn, so the bridge keeps
its framing small and big text out of the conversation.

- Claude's XML envelope becomes one line: `[peer message from NAME · id XXXXXXXX]`.
  Framing per message is 71 characters, about 17 tokens, after the first message.
- Replies over 4000 characters are cut and stored; `cxpeer read <id>` prints the rest.
- Both skills tell the model to send file paths, not file contents.
- `cxpeer list` shows each peer's live prompt size, read from the transcripts both tools
  already write: `ctx=22k/258k (8%)` for Codex, `ctx=163k` for Claude.

Measured first turn of a freshly spawned peer, with and without `--lean`
(no MCP servers, and for Claude no slash commands):

| Peer | Normal | `--lean` |
| --- | --- | --- |
| Claude Code | 49.6k tokens | 45.8k tokens |
| Codex TUI | 17.5k tokens | 17.4k tokens |

The floor is each tool's built-in prompt. The saving that matters is structural: a peer
does one task and stays small, and the session you drive keeps only the summaries.

## Commands

| Command | What it does |
| --- | --- |
| `cxpeer list` | live peers with their prompt size |
| `cxpeer send --to NAME TEXT` | message a peer; `-` reads stdin; works inside the Codex sandbox |
| `cxpeer spawn codex\|claude [--lean] [--cwd DIR] [--peer-name N] [--prompt TEXT] [--wait] [--codex-home DIR] [-- ARGS…]` | start a peer in tmux, optionally wait until it registers |
| `cxpeer status` | every bridge: reachable, requests waiting, characters in and out, context size |
| `cxpeer read ID` | the full text of a reply that was forwarded cut |
| `cxpeer doctor [--codex-home DIR]` | ten checks plus a live self-test |
| `cxpeer install [--dry-run] [--codex-home DIR]` | install or update the hooks and skill |

## Configuration

| Variable | Default |
| --- | --- |
| `CXPEER_HOME` | `~/.cxpeer` (bridge records, replies, logs) |
| `CXPEER_CODEX_HOME` | `~/.codex` |
| `CXPEER_CLAUDE_SESSIONS_DIRS` | `~/.claude/sessions` plus every `~/.claude-*/sessions` |
| `CXPEER_SOCK_DIR` | `/tmp/cc-socks` |
| `CXPEER_OUTBOX_ROOT` | `/tmp/cxpeer-<uid>` |
| `CXPEER_CODEX_BIN`, `CXPEER_TMUX_BIN` | `codex`, `tmux` |
| `CXPEER_PENDING_TTL_SECONDS` | `900` |
| `CXPEER_REPLY_MAX_CHARS` | `4000` |

Logs: `~/.cxpeer/logs/hooks.log` and `~/.cxpeer/logs/<thread>.log`.

## Several accounts

The bridge registers each Codex peer in every Claude registry on the machine, so two
Claude Code accounts see the same peers. Codex accounts are separate homes:

```sh
cxpeer install --codex-home ~/.codex-work
cxpeer spawn codex --codex-home ~/.codex-work --peer-name codex-work --prompt "..." --wait
```

## Caveats

- The peer registry and wire format are undocumented Claude Code internals. An update can
  change them; `cxpeer doctor` and the tests check the current contract.
- Tested by hand on macOS. Linux passes CI. No Windows.
- Not done: file attachments, more than one machine, message history.

## Contributing

```sh
uv venv .venv --python 3.13 && uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest tests -q
```

No test touches your real setup. See [CONTRIBUTING.md](CONTRIBUTING.md) for how the
tests fake Codex and Claude, and [ROADMAP.md](ROADMAP.md) for what is next.

## License

[PolyForm Noncommercial 1.0.0](LICENSE). Free for personal use, research, teaching,
nonprofits, and government. Commercial use needs a separate license.

Questions: open an [issue](https://github.com/HivaMohammadzadeh1/cxpeer/issues) or find
[Hiva Mohammadzadeh](https://github.com/HivaMohammadzadeh1) on GitHub.
