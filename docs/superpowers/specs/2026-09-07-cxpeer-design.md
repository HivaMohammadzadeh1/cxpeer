# cxpeer: Claude Code <-> Codex peer messaging (POC)

Date: 2026-09-07. Status: approved design, one-day POC.

## Goal

Make running Codex CLI sessions show up as peers in Claude Code's cross-session
messaging (ListAgents / SendMessage), and let Codex send messages back. Event
driven: a Claude message lands in the Codex TUI without polling, and Codex's
final answer for that turn is forwarded back to the sender automatically.

## Verified facts this design relies on (2026-09-07, Claude Code 2.1.263, Codex 0.153.3)

1. A Claude peer is three files, all owned by the user:
   - `~/.claude/sessions/<pid>.json` registry record (fields below).
   - `~/.claude/sessions/<pid>.<sha256(socket path string)>.key` containing
     `{"peerToken": <32 hex>, "procStart": <ps lstart>, "pidDomain": "darwin"}`, mode 0600.
     The hash input is the literal string `/tmp/cc-socks/<pid>.sock` (not `/private/tmp`).
   - A Unix stream socket at `/tmp/cc-socks/<pid>.sock`, mode 0600, dir mode 0700.
2. Liveness: Claude compares the record's `procStart` with
   `LC_ALL=C TZ=UTC ps -o lstart= -p <pid>`. Dead pid = not listed.
3. Wire protocol, newline-delimited JSON, first line must be auth:
   ```
   {"type":"auth","token":"<peerToken of the RECEIVER>"}
   {"msgV":1,"msg_id":"<uuid4>","type":"user","message":{"role":"user","content":"<text>"},"priority":"next","from":"uds:/tmp/cc-socks/<SENDER pid>.sock"}
   ```
   `from` is the reply address. Claude wraps `content` itself before sending:
   ```
   <cross-session-message from="uds:/tmp/cc-socks/81548.sock" from-name="unclave-8b" from-mode="prompting">
   Say LOOP-OK and nothing else.
   </cross-session-message>
   ```
   Receivers render `content` verbatim, so senders must produce that envelope.
4. Any process that writes the three files appears in ListAgents and receives
   SendMessage frames (probe named `codex-probe` verified end to end).
5. `codex queue --thread <thread uuid> --message TEXT` auto-submits into an idle
   running Codex TUI in ~3 s via `~/.codex/queue_1.sqlite`. No daemon required.
   The thread uuid is in the rollout filename under `~/.codex/sessions/YYYY/MM/DD/`
   and in every hook payload as `session_id`.
6. Codex hooks are stable. `~/.codex/hooks.json` uses the Claude-style shape
   (`{"hooks": {"<Event>": [{"matcher": "*", "hooks": [{"type": "command", "command": "..."}]}]}}`).
   Events used here: `SessionStart`, `UserPromptSubmit` (payload has `prompt`),
   `Stop` (payload has `last_assistant_message`, `stop_hook_active`), `SessionEnd`.
   All payloads carry `session_id`, `cwd`, `hook_event_name`. Payload arrives on stdin.

Registry record written for a bridge (mirrors a real record; unknown fields are ignored):
```
{"pid": P, "sessionId": "<uuid4>", "cwd": "<codex cwd>", "startedAt": ms, "procStart": "<lstart>",
 "version": "2.1.263", "peerProtocol": 1, "peerFeatures": [], "kind": "interactive", "entrypoint": "cli",
 "pidDomain": "darwin", "messagingSocketPath": "/tmp/cc-socks/P.sock",
 "name": "codex-<dir>-<xx>", "nameSource": "user", "nameSince": ms,
 "status": "idle", "updatedAt": ms, "statusUpdatedAt": ms}
```

## Architecture

One Python package `cxpeer` (3.11+, zero runtime deps), one console script `cxpeer`.
Per Codex session there is one **bridge** process. It is the Codex session's
identity inside Claude's peer system.

```
Claude session ──SendMessage──> /tmp/cc-socks/<bridge pid>.sock ──codex queue──> Codex TUI
Claude session <──user frame─── bridge <──cxpeer.turn_ended (Stop hook)──────── Codex TUI
Claude session <──user frame─── bridge <──cxpeer.relay (cxpeer send) ─────────── Codex shell
```

### Modules and their contracts

`cxpeer/paths.py`
- `sessions_dir()` -> `~/.claude/sessions` (override `CXPEER_CLAUDE_SESSIONS_DIR`).
- `sock_dir()` -> `/tmp/cc-socks` (override `CXPEER_SOCK_DIR`).
- `state_dir()` -> `~/.cxpeer` (override `CXPEER_HOME`). Bridges write `state_dir()/bridges/<codex session_id>.json`.
- `codex_bin()` -> `"codex"` (override `CXPEER_CODEX_BIN`). Tests point this at a fake script.

`cxpeer/registry.py`
- `proc_start(pid: int) -> str | None` via `ps -o lstart=`.
- `sock_path_for(pid) -> str`, `key_name_for(pid, sock_path) -> str`.
- `register(pid, name, cwd, sock_path, token) -> None` writes record + key (atomic write, key mode 0600).
- `deregister(pid) -> None`, `set_status(pid, "busy"|"idle") -> None` (rewrite record, bump timestamps).
- `list_peers() -> list[Peer]` where `Peer(name, ref, pid, sock, cwd, status, kind, alive: bool)`;
  `alive` = record `procStart` equals current `proc_start(pid)`. `ref` = first 6 hex of sha256(sock).
- `token_for(sock_path) -> str | None` reads the matching key file for a peer socket.
- `resolve(name_or_ref) -> Peer` exact name match among alive peers, else ref match, else `LookupError`.

`cxpeer/wire.py`
- `envelope(text, from_addr, from_name) -> str` builds the `<cross-session-message ...>` block.
- `user_frame(content, from_addr, msg_id=None) -> dict` builds the outbound user frame (msgV 1, priority next).
- `send_frames(sock_path, token, frames: list[dict], timeout=5.0) -> None` connects, writes auth + frames, closes.
- `read_lines(conn, timeout=5.0) -> list[dict]` reads until EOF or timeout, JSON-decodes, skips bad lines.
- `strip_envelope(content) -> tuple[str, str | None, str | None]` returns (text, from_addr, from_name) if wrapped.

`cxpeer/bridge.py` (`cxpeer bridge --thread ID --cwd DIR [--watch-pid PID] [--name NAME]`)
- Registers itself (name default `codex-<basename(cwd)>-<first 2 hex of thread id>`), writes bridge state
  `{pid, sock, token, name, cwd, thread, started}` to `state_dir()/bridges/<thread>.json`.
- Serves the UDS. Every connection must start with an auth line matching its own token; else drop.
- Frame handling:
  - `type=user` from Claude: record `pending[msg_id] = {reply_to: from, from_name}` and run
    `codex queue --thread <thread> --message <content + trailer>`. Trailer:
    `\n\n[cxpeer msg_id=<msg_id>] Your final answer this turn is forwarded to <from_name> automatically. To message any session yourself run: cxpeer send --to <name> "text" (cxpeer list shows names).`
  - `type=cxpeer.turn_started` `{msg_id|null}`: `set_status busy`; `active = msg_id`.
  - `type=cxpeer.turn_ended` `{last_assistant_message}`: `set_status idle`; if `active` is a known
    pending id, send `user_frame(envelope(last_assistant_message, own_addr, own_name), own_addr)` to
    `pending[active].reply_to` using that peer's token, then drop it. `active = None`.
  - `type=cxpeer.relay` `{to, text}`: resolve peer, send envelope from own address, respond `{"ok":true}` or `{"ok":false,"error":"..."}`.
  - `type=cxpeer.ping`: respond `{"ok":true,"name":...,"thread":...,"pending":n}`.
  - `type=cxpeer.shutdown`: deregister, remove state, exit 0.
  - anything else: log and ignore.
- If `--watch-pid` is given, exit (with cleanup) within 5 s of that pid dying. Also cleans up on SIGTERM.
- Logs to `state_dir()/logs/<thread>.log`.

`cxpeer/client.py`
- `find_bridge(cwd=None, thread=None) -> BridgeInfo | None`: by thread if given, else newest bridge whose
  `cwd` is the current dir or an ancestor of it, else newest alive bridge, else None.
- `send(to, text, bridge) -> None` opens the bridge socket, auth, `cxpeer.relay`, raises on `ok=false`.
- `send_direct(to, text) -> None` fallback when no bridge: sends from address `uds:` + a placeholder;
  prints a warning that replies cannot be routed.

`cxpeer/hooks.py` (`cxpeer hook <session-start|user-prompt-submit|stop|session-end>`, JSON on stdin)
- `session-start`: spawn `cxpeer bridge --thread <session_id> --cwd <cwd> --watch-pid <os.getppid()>`
  fully detached (new session, devnull stdio). Idempotent: skip if a live bridge for that thread exists.
- `user-prompt-submit`: extract `msg_id` from a `[cxpeer msg_id=...]` marker in `prompt`, send `cxpeer.turn_started`.
- `stop`: send `cxpeer.turn_ended` with `last_assistant_message`.
- `session-end`: send `cxpeer.shutdown`.
- Hooks never fail the Codex turn: all errors are swallowed to the log, exit 0, empty stdout.

`cxpeer/cli.py`: argparse dispatch for `bridge`, `list`, `send`, `hook`, `status`, `install`.
- `install` merges the four hook entries into `~/.codex/hooks.json` (creating it if missing, never
  duplicating, never touching other entries) and writes `~/.codex/skills/cxpeer/SKILL.md` telling Codex
  when and how to use `cxpeer list` / `cxpeer send`. `--dry-run` prints the resulting files.
- `list` prints one row per alive peer: `name [ref]  status  cwd`.
- `send --to NAME TEXT` (TEXT may be `-` for stdin).

### Error handling
- Bridge refuses unauthenticated connections silently (matches Claude).
- `codex queue` failure: log, and reply to the sender with an envelope saying the message could not be
  delivered (so the Claude side is not left waiting).
- Sending to a dead peer raises `LookupError("no live peer named ...")`; the CLI prints it and exits 1.
- All file writes to the registry are atomic (write temp, rename).

### Out of scope for the POC
File attachments, `notify_when_idle`, multi-machine/bridge relays, Windows, Codex `exec` mode,
message history, retries beyond one attempt.

### Testing
Unit tests under `tests/` run with `pytest`, never touch the real registry: fixtures set
`CXPEER_CLAUDE_SESSIONS_DIR`, `CXPEER_SOCK_DIR`, `CXPEER_HOME` to tmp dirs and `CXPEER_CODEX_BIN` to a
fake script that appends its argv to a file. Bridge tests run the bridge as a subprocess with those env
vars and talk to it over its socket. A fake "Claude" UDS server in the test asserts the auto-reply frame.
Manual e2e: `scripts/e2e.sh` starts a Codex TUI in tmux, waits for the bridge, and sends a message from
this Claude session with SendMessage; expected result is Codex answering in the pane and the answer
arriving back here.

### Risk
This builds on an undocumented Claude Code internal (`peerProtocol` 1). A Claude update can break it.
Acceptable for a POC; not a dependency for anything else.
