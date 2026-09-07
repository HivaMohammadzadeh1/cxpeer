# cxpeer user journey

## Step 1: Check the install

Command:
```text
cxpeer doctor
```

Output:
```text
ok  claude: version 2.1.263
ok  claude-sessions: /Users/hivamoh/.claude/sessions present (0700)
ok  socket-dir: /tmp/cc-socks present
ok  codex: version 0.153.3
ok  codex-hooks: all five cxpeer hooks present with a valid interpreter
ok  codex-trust: all five hooks trusted in config.toml
ok  codex-skill: /Users/hivamoh/.codex/skills/cxpeer/SKILL.md up to date
ok  tmux: present
ok  self-test: bridge answered ping and queued a message in 1460 ms
ok  live-bridges: (2 other bridges on this machine omitted)
```

Elapsed: 2.04s
Result: ok

## Step 2: Start a Codex peer from a Claude session

Command:
```text
cxpeer spawn codex --cwd /Users/hivamoh/funProjects/cxpeer --peer-name codex-journey-2655 --prompt 'Say READY and nothing else.' --wait --timeout 120
```

Output:
```text
started cx-codex-cxpeer-49f1 (codex) in /Users/hivamoh/funProjects/cxpeer; attach with: tmux attach -t cx-codex-cxpeer-49f1
peer codex-journey-2655 is up (thread 01a07e22-d9c7-7e82-a337-ad19d9d9a913); attach with: tmux attach -t cx-codex-cxpeer-49f1
```

Elapsed: 2.22s
Result: ok

## Step 3: Claude sees the new peer

Command:
```text
cxpeer list
```

Output:
```text
    claude-journey [a26353]  idle  /Users/hivamoh/funProjects/cxpeer
>>> codex-journey-2655 [440408]  idle  /Users/hivamoh/funProjects/cxpeer
    (8 other live sessions on this machine omitted)
```

Elapsed: 0.22s
Result: ok

## Step 4: Claude gives Codex a real task

Command:
```text
connection 1 -> /tmp/cc-socks/47146.sock
{"token": "<peer token>", "type": "auth"}
{"from": "uds:/tmp/cc-socks/46739.sock", "message": {"content": "<cross-session-message from=\"uds:/tmp/cc-socks/46739.sock\" from-name=\"claude-journey\" from-mode=\"prompting\">\nList the three largest source files under cxpeer/ with their line counts, one line each.\n</cross-session-message>", "role": "user"}, "msgV": 1, "msg_id": "9fb25990-93ab-4b42-ae32-c16debeaef07", "priority": "next", "type": "user"}
connection 2 -> /tmp/cc-socks/47146.sock
{"token": "<peer token>", "type": "auth"}
{"action": "notify_when_idle", "from": "uds:/tmp/cc-socks/46739.sock", "from_mode": "prompting", "msgV": 1, "msg_id": "70940764-09e7-42ee-96db-4a833959d305", "type": "control"}
```

Output:
```text
answer (+14.59s): cxpeer/bridge.py — 585 lines
cxpeer/doctor.py — 340 lines
cxpeer/client.py — 246 lines
peer_idle_notice (+14.71s): cxpeer/bridge.py — 585 lines cxpeer/doctor.py — 340 lines cxpeer/client.py — 246 lines
```

Elapsed: 14.75s
Result: ok

## Step 5: Codex reaches out on its own

Command:
```text
connection 1 -> /tmp/cc-socks/47146.sock
{"token": "<peer token>", "type": "auth"}
{"from": "uds:/tmp/cc-socks/46739.sock", "message": {"content": "<cross-session-message from=\"uds:/tmp/cc-socks/46739.sock\" from-name=\"claude-journey\" from-mode=\"prompting\">\nRun `cxpeer send --to claude-journey \"hello from inside the Codex sandbox\"` and reply with its exit code.\n</cross-session-message>", "role": "user"}, "msgV": 1, "msg_id": "bc1155dc-528b-4712-a15e-b3664956c428", "priority": "next", "type": "user"}
```

Output:
```text
relayed message (+11.83s): hello from inside the Codex sandbox
answer (+13.57s): Exit code: 0.
```

Elapsed: 13.60s
Result: ok

## Step 6: Tear down

Command:
```text
tmux kill-session -t cx-codex-cxpeer-49f1
cxpeer status
deregister claude-journey
```

Output:
```text
codex-journey-2655  pid=47146  thread=01a07e22-d9c7-7e82-a337-ad19d9d9a913  idle  pending=0  /Users/hivamoh/funProjects/cxpeer
(2 other live sessions on this machine omitted)
deregistered claude-journey
```

Elapsed: 2.07s
Result: ok

## Summary

| Step | Result | Seconds |
| --- | --- | ---: |
| 1 | ok | 2.04 |
| 2 | ok | 2.22 |
| 3 | ok | 0.22 |
| 4 | ok | 14.75 |
| 5 | ok | 13.60 |
| 6 | ok | 2.07 |
