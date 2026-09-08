# cxpeer user journey

## Step 1: Check the install

Command:
```text
cxpeer doctor --codex-home /Users/hivamoh/.codex-alt
```

Output:
```text
ok  claude: version 2.1.263
ok  claude-sessions: /Users/hivamoh/.claude/sessions present (0700)
ok  claude-sessions: /Users/hivamoh/.claude-alt/sessions present (0700)
ok  socket-dir: /tmp/cc-socks present
ok  codex: version 0.153.3
ok  codex-hooks: all five cxpeer hooks present with a valid interpreter
ok  codex-trust: all five hooks trusted in config.toml
ok  codex-skill: /Users/hivamoh/.codex-alt/skills/cxpeer/SKILL.md up to date
ok  tmux: present
ok  self-test: bridge answered ping and queued a message in 1464 ms
ok  live-bridges: (2 other bridges on this machine omitted)
```

Elapsed: 2.07s
Result: ok

## Step 2: Start a Codex peer from a Claude session

Command:
```text
cxpeer spawn codex --cwd /Users/hivamoh/funProjects/cxpeer --peer-name codex-journey-751a --prompt 'Say READY and nothing else.' --wait --timeout 120 --codex-home /Users/hivamoh/.codex-alt
```

Output:
```text
started cx-codex-cxpeer-c841 (codex) in /Users/hivamoh/funProjects/cxpeer; attach with: tmux attach -t cx-codex-cxpeer-c841
peer codex-journey-751a is up (thread 01a07e7f-92a6-7cf1-bc39-b63ac2d7226e); attach with: tmux attach -t cx-codex-cxpeer-c841
```

Elapsed: 3.19s
Result: ok

## Step 3: Claude sees the new peer

Command:
```text
cxpeer list
```

Output:
```text
    claude-journey [a72b54]  idle  /Users/hivamoh/funProjects/cxpeer
>>> codex-journey-751a [d07d3d]  idle  /Users/hivamoh/funProjects/cxpeer
    (8 other live sessions on this machine omitted)
```

Elapsed: 0.23s
Result: ok

## Step 4: A second Claude account sees it too

Command:
```text
CXPEER_CLAUDE_SESSIONS_DIR=/Users/hivamoh/.claude-alt/sessions cxpeer list
```

Output:
```text
    claude-journey [a72b54]  idle  /Users/hivamoh/funProjects/cxpeer
>>> codex-journey-751a [d07d3d]  idle  /Users/hivamoh/funProjects/cxpeer
```

Elapsed: 0.15s
Result: ok

## Step 5: Claude gives Codex a real task

Command:
```text
connection 1 -> /tmp/cc-socks/82183.sock
{"token": "<peer token>", "type": "auth"}
{"from": "uds:/tmp/cc-socks/81793.sock", "message": {"content": "<cross-session-message from=\"uds:/tmp/cc-socks/81793.sock\" from-name=\"claude-journey\" from-mode=\"prompting\">\nList the three largest source files under cxpeer/ with their line counts, one line each.\n</cross-session-message>", "role": "user"}, "msgV": 1, "msg_id": "17015d26-f96d-41fa-8506-d7b542fded71", "priority": "next", "type": "user"}
connection 2 -> /tmp/cc-socks/82183.sock
{"token": "<peer token>", "type": "auth"}
{"action": "notify_when_idle", "from": "uds:/tmp/cc-socks/81793.sock", "from_mode": "prompting", "msgV": 1, "msg_id": "57aff1e4-d24a-4552-af8f-a2e2ea27eea8", "type": "control"}
```

Output:
```text
answer (+27.72s): cxpeer/bridge.py — 585 lines
cxpeer/doctor.py — 347 lines
cxpeer/client.py — 246 lines
peer_idle_notice (+27.84s): cxpeer/bridge.py — 585 lines cxpeer/doctor.py — 347 lines cxpeer/client.py — 246 lines
```

Elapsed: 27.88s
Result: ok

## Step 6: Codex reaches out on its own

Command:
```text
connection 1 -> /tmp/cc-socks/82183.sock
{"token": "<peer token>", "type": "auth"}
{"from": "uds:/tmp/cc-socks/81793.sock", "message": {"content": "<cross-session-message from=\"uds:/tmp/cc-socks/81793.sock\" from-name=\"claude-journey\" from-mode=\"prompting\">\nRun `cxpeer send --to claude-journey \"hello from inside the Codex sandbox\"` and reply with its exit code.\n</cross-session-message>", "role": "user"}, "msgV": 1, "msg_id": "1a2033c5-e557-4ff6-824e-f75663e780e2", "priority": "next", "type": "user"}
```

Output:
```text
relayed message (+16.27s): hello from inside the Codex sandbox
answer (+17.03s): 0
```

Elapsed: 17.06s
Result: ok

## Step 7: Tear down

Command:
```text
tmux kill-session -t cx-codex-cxpeer-c841
cxpeer status
deregister claude-journey
```

Output:
```text
(2 other live sessions on this machine omitted)
deregistered claude-journey
```

Elapsed: 2.08s
Result: ok

## Summary

| Step | Result | Seconds |
| --- | --- | ---: |
| 1 | ok | 2.07 |
| 2 | ok | 3.19 |
| 3 | ok | 0.23 |
| 4 | ok | 0.15 |
| 5 | ok | 27.88 |
| 6 | ok | 17.06 |
| 7 | ok | 2.08 |
