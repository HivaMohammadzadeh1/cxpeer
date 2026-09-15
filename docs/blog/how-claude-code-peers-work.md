# Claude Code peers, from the outside: three files, one socket, two JSON lines (and how a Codex session became one)

Claude Code sessions can message each other. In build 2.1.263 a session has a `ListAgents` tool that shows the other Claude sessions running on your machine, and a `SendMessage` tool that delivers text to one of them. The message lands in the other session's queue and it answers on its next turn.

I use Codex CLI next to Claude Code most days, and I wanted my Codex sessions in that list. One long day later they were. The result is [cxpeer](https://github.com/HivaMohammadzadeh1/cxpeer), about 2,500 lines of standard-library Python with no daemon and no patching of either CLI.

None of what follows came from documentation. I got it by watching the files Claude Code writes while two sessions talked to each other, then writing the same files myself and checking whether they showed up. Everything below was verified against Claude Code 2.1.263 and Codex 0.153 on macOS.

## The registry is three files

A Claude Code session advertises itself by writing three things, all owned by you, all mode 0600.

```text
~/.claude/sessions/<pid>.json                       the record
~/.claude/sessions/<pid>.<sha256(socket path)>.key  the shared secret
/tmp/cc-socks/<pid>.sock                            the socket (dir is 0700)
```

The record is a flat JSON object. The fields that matter to another session are `pid`, `cwd`, `name`, `status`, `messagingSocketPath`, `peerProtocol` (currently 1), `peerFeatures`, and `procStart`. There are timestamps alongside them (`startedAt`, `updatedAt`, `statusUpdatedAt`, `nameSince`) and a `sessionId` that names the session's transcript. Unknown fields are ignored, which is how a fake record gets away with being a fake record.

The key file is the interesting one. Its name is the pid, a dot, then the sha256 of the socket path, then `.key`. The hash input is the literal string `/tmp/cc-socks/<pid>.sock`. On macOS `/tmp` is a symlink to `/private/tmp`, and if you resolve the path before hashing it you get a filename Claude will never look up. That cost me a while. Inside the file:

```json
{"peerToken": "<32 hex characters>", "procStart": "<ps lstart output>", "pidDomain": "darwin"}
```

`peerToken` is the bearer token for that socket. Anyone who can read the file can send that session a message, which is fine, because the file is 0600 and everything here runs as you.

Liveness is where the design is better than it had to be. Claude does not check whether the pid exists. It runs

```sh
LC_ALL=C TZ=UTC ps -o lstart= -p <pid>
```

and compares the output, byte for byte, with the `procStart` string stored in the record. If they differ, the peer is not listed.

That sidesteps pid reuse. A stale record left behind by a crashed session names a pid the kernel is free to hand to something else, and on a busy machine it eventually will. Checking existence would then route a message at an unrelated process, or at best show a peer that is not there. Comparing start times makes the check an identity check rather than a presence check: pid 81548 that started at this second is a different peer from pid 81548 that started an hour ago, and the second one is simply absent. The locale and timezone pinning exists because `lstart` formats differently otherwise, and a string comparison is unforgiving about that.

Claude also shows a short id per peer, the first six hex of the same sha256 of the socket path.

## The wire is two JSON lines

Newline-delimited JSON over the Unix stream socket. The first line on every connection must be an auth frame carrying the **receiver's** token, the one you read out of their key file. Then the message.

```json
{"type":"auth","token":"<receiver's peerToken>"}
{"msgV":1,"msg_id":"<uuid4>","type":"user","priority":"next","from":"uds:/tmp/cc-socks/<sender pid>.sock",
 "message":{"role":"user","content":"<cross-session-message from=\"uds:...\" from-name=\"codex-tests\" from-mode=\"prompting\">\nPONG\n</cross-session-message>"}}
```

Then close. There is no response on the same connection; a reply is a new connection in the other direction, which is what `from` is for. It is the reply address, and it is a socket path rather than a pid or a name.

`content` is rendered verbatim by the receiver. Claude wraps outbound text in that `<cross-session-message>` block itself before it hits the socket, so a sender that is not Claude has to build the envelope by hand or its message shows up with no attribution. `msg_id` is a uuid4 and is how you correlate a reply with the request that caused it.

One more frame type. A sender can subscribe to the receiver going idle:

```json
{"type":"control","action":"notify_when_idle","msgV":1,"msg_id":"...","from":"uds:...","from_mode":"prompting"}
```

It arrives as its own connection, not attached to a message. The peer answers, using the requester's token, with

```json
{"type":"control","action":"peer_idle_notice","orig_msg_id":"...","state":"idle","finished_at":"...","detail":"...","from":"uds:...","from_mode":"prompting"}
```

where `state` is `idle`, `exited`, or `unavailable`. Claude only sends the subscription to peers whose record advertises `peerFeatures: ["notify_idle"]`, so a peer opts in by listing the string. The notice surfaces in the requesting session at its next turn boundary, not the instant it arrives.

## Anything that writes those files is a peer

That is the whole trick. `ListAgents` reads the sessions directory and runs `ps`. It does not ask whether the process behind a record is Claude Code. My first probe was a 40-line script that wrote a record, wrote a key, bound a socket, and printed whatever it received. I called it `codex-probe`, and a Claude session listed it and messaged it end to end.

So cxpeer's job is to be that probe with a real implementation behind it. One bridge process per Codex session, about 650 lines, started by a Codex hook. It binds the socket, writes the record and the key, accepts connections, checks the auth line, and dispatches by frame type. Its state is a dict of pending requests, a pointer to the active one, the idle/busy flag, and the idle subscriptions, behind a lock. When the Codex pid goes away the bridge sees it and removes its own files.

## The Codex side is a queue, five hooks, and a marker

Getting a message *into* a running Codex TUI turned out to be a solved problem I had not noticed. `codex queue --thread <uuid> --message TEXT` writes into a sqlite queue that the TUI drains, and an idle TUI auto-submits the text in about three seconds. No daemon, no keystroke injection.

Getting the answer *out* is what the hooks are for. `cxpeer install` adds five entries to `~/.codex/hooks.json`, which uses the same shape as Claude's hooks. `SessionStart` starts the bridge, and it fires on the first prompt rather than at launch, because Codex creates the session lazily. `UserPromptSubmit` carries the prompt text. `Stop` carries `last_assistant_message`. `Interrupt` fires instead of `Stop` when a turn is cancelled. `SessionEnd` tells the bridge to deregister. Every payload carries `session_id`, which is the thread uuid `codex queue` wants. Codex asks once whether to trust newly installed hooks, and if you decline, nothing works.

The piece that holds it together is a marker. The bridge appends `[cxpeer msg_id=XXXXXXXX]` to the text it queues. `UserPromptSubmit` reports the marker back, so the bridge knows which pending request the coming turn belongs to, and `Stop` then hands it the answer to forward. A prompt you typed yourself has no marker, so your own turns are never forwarded to anyone. An interrupted turn tells the sender it was interrupted. A request that never gets a turn expires after 15 minutes with a note, rather than leaving the Claude side waiting forever.

Codex runs shell commands in a sandbox, and inside it `ps` cannot execute and connecting to a Unix socket raises `PermissionError`. Reads of the home directory work; writes work in `/tmp`, `$TMPDIR`, and the cwd. So `cxpeer send` from inside Codex writes `{id, to, text}` into `/tmp/cxpeer-<uid>/<thread>/`, the bridge polls that directory every second, relays, and drops `<id>.result.json` back. `cxpeer list` reads a peers snapshot the bridge rewrites every four seconds, and treats a snapshot older than 15 seconds as "no bridge". The client picks files over the socket on its own, when a connect raises `PermissionError` or `ps` fails, so the same command works from either side of the sandbox.

Measured on 2026-09-07: a `SendMessage` answered in 8 seconds end to end, most of it Codex thinking, and a `cxpeer send` from inside the sandbox arrived in Claude 10 seconds after the request.

## What it costs

Every message a session receives is paid for in tokens, and the first version was wasteful. Claude's `<cross-session-message>` envelope names a socket path, which no model needs to read. The bridge now rewrites it to `[peer message from NAME · id XXXXXXXX]` with a matching marker on the last line, and shortens message ids to 8 characters (the hook's marker is paired by prefix). The sentence explaining auto-forwarding rides on the first message of a Codex session only; later messages carry the marker and nothing else.

Measured on a 30-character message, counting the characters of framing Codex reads:

| | v0.3.0 | v0.4.0 first message | v0.4.0 later messages |
| --- | --- | --- | --- |
| Framing | 351 chars (~87 tokens) | 204 chars (~51 tokens) | 71 chars (~17 tokens) |
| Codex skill (loaded once) | 1445 bytes | 691 bytes | |
| Claude skill (loaded once) | 2319 bytes | 869 bytes | |

Three smaller things go with it. A forwarded reply longer than 4000 characters is cut, with the full text written to `~/.cxpeer/replies/<thread>/<id>.md` and a note saying `full text: cxpeer read <id>`, so a long answer costs tokens only when someone wants it. An idle notice for an answer that was already forwarded says `answered NAME (N chars)` instead of repeating the text. Both skills tell the model to send paths rather than file contents, since the two sessions share a filesystem and a path costs a dozen tokens where the contents cost thousands. `cxpeer status` prints characters queued in and forwarded out per bridge, with a rough token count, so a conversation's cost is visible after the fact.

The framing is the small part. The large part is that every peer message wakes a full turn, and a turn re-sends the whole thread as input. One of my Codex threads reported 148,228 input tokens on its last turn and 14.6 million over the session, from the `token_count` events Codex writes to its rollout file. Claude Code writes a usage block on every assistant message of its transcript. cxpeer 0.5.0 reads the tail of both files, so `cxpeer list` shows the live prompt size of every peer next to its name, and flags one that has filled most of its window.

The floor matters less than I expected. I measured the first turn of freshly spawned peers from their transcripts: a Claude peer starts at 49.6k tokens with my nine MCP servers and 45.8k without them; a Codex TUI peer starts at 17.5k and drops to 17.4k when its three MCP servers are removed, because Codex's floor is its built-in prompt. `cxpeer spawn --lean` applies those flags anyway (`-c mcp_servers={}` for Codex, `--strict-mcp-config --mcp-config "" --disable-slash-commands` for Claude, which still registers as a peer where `--bare` does not). The saving that matters is structural: a worker peer's thread stays short because it does one task, and the orchestrating session keeps only the summaries.

## What breaks

All of this rests on an undocumented internal. `peerProtocol` is 1 today; a Claude Code update can rename a field, move the sessions directory, or change how the key filename is derived, and the peer just stops appearing with no error anywhere. The mitigation is that the tests pin the contract, so it breaks loudly in CI rather than quietly at runtime, and `cxpeer doctor` re-checks the live contract on your machine: ten checks plus a self-test that starts a bridge and messages it.

It has only been used on macOS. Tests pass on Ubuntu in CI, but nobody has run a Linux peer by hand, and there is no Windows support.

The code and a recorded run are at https://github.com/HivaMohammadzadeh1/cxpeer. 196 tests, standard library only, one process per Codex session.
