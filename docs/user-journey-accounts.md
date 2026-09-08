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
ok  self-test: bridge answered ping and queued a message in 1417 ms
ok  live-bridges: (2 other bridges on this machine omitted)
```

Elapsed: 2.03s
Result: ok

## Step 2: Start a Codex peer from a Claude session

Command:
```text
cxpeer spawn codex --cwd /Users/hivamoh/funProjects/cxpeer --peer-name codex-journey-1654 --prompt 'Say READY and nothing else.' --wait --timeout 120 --codex-home /Users/hivamoh/.codex-alt
```

Output:
```text
started cx-codex-cxpeer-2968 (codex) in /Users/hivamoh/funProjects/cxpeer; attach with: tmux attach -t cx-codex-cxpeer-2968
peer codex-journey-1654 is up (thread 01a081ad-33e1-7e60-8b83-5316a2f84055); attach with: tmux attach -t cx-codex-cxpeer-2968
```

Elapsed: 2.19s
Result: ok

## Step 3: Claude sees the new peer

Command:
```text
cxpeer list
```

Output:
```text
    claude-journey [eb39c4]  idle  /Users/hivamoh/funProjects/cxpeer
>>> codex-journey-1654 [4ebff6]  idle  /Users/hivamoh/funProjects/cxpeer
    (8 other live sessions on this machine omitted)
```

Elapsed: 0.24s
Result: ok

## Step 4: A second Claude account sees it too

Command:
```text
CXPEER_CLAUDE_SESSIONS_DIR=/Users/hivamoh/.claude-alt/sessions cxpeer list
```

Output:
```text
    claude-journey [eb39c4]  idle  /Users/hivamoh/funProjects/cxpeer
>>> codex-journey-1654 [4ebff6]  idle  /Users/hivamoh/funProjects/cxpeer
```

Elapsed: 0.17s
Result: ok

## Step 5: Claude gives Codex a real task

Command:
```text
connection 1 -> /tmp/cc-socks/992.sock
{"token": "<peer token>", "type": "auth"}
{"from": "uds:/tmp/cc-socks/461.sock", "message": {"content": "<cross-session-message from=\"uds:/tmp/cc-socks/461.sock\" from-name=\"claude-journey\" from-mode=\"prompting\">\nList the three largest source files under cxpeer/ with their line counts, one line each.\n</cross-session-message>", "role": "user"}, "msgV": 1, "msg_id": "7cccde45-5ce6-4b1c-b889-0b9d4de55764", "priority": "next", "type": "user"}
connection 2 -> /tmp/cc-socks/992.sock
{"token": "<peer token>", "type": "auth"}
{"action": "notify_when_idle", "from": "uds:/tmp/cc-socks/461.sock", "from_mode": "prompting", "msgV": 1, "msg_id": "9ec61673-c111-496c-978c-0658a2840fc4", "type": "control"}
```

Output:
```text
answer (+16.33s): cxpeer/bridge.py — 585 lines
cxpeer/doctor.py — 347 lines
cxpeer/client.py — 246 lines
peer_idle_notice (+16.45s): cxpeer/bridge.py — 585 lines cxpeer/doctor.py — 347 lines cxpeer/client.py — 246 lines
```

Elapsed: 16.49s
Result: ok

## Step 6: Codex reaches out on its own

Command:
```text
connection 1 -> /tmp/cc-socks/992.sock
{"token": "<peer token>", "type": "auth"}
{"from": "uds:/tmp/cc-socks/461.sock", "message": {"content": "<cross-session-message from=\"uds:/tmp/cc-socks/461.sock\" from-name=\"claude-journey\" from-mode=\"prompting\">\nRun `cxpeer send --to claude-journey \"hello from inside the Codex sandbox\"` and reply with its exit code.\n</cross-session-message>", "role": "user"}, "msgV": 1, "msg_id": "da349d8d-e1b1-47f3-82a2-3418a3a5e5cd", "priority": "next", "type": "user"}
```

Output:
```text
relayed message (+7.63s): hello from inside the Codex sandbox
answer (+9.11s): Exit code: 0
```

Elapsed: 9.14s
Result: ok

## Step 7: Tear down

Command:
```text
tmux kill-session -t cx-codex-cxpeer-2968
cxpeer status
deregister claude-journey
```

Output:
```text
(2 other live sessions on this machine omitted)
deregistered claude-journey
```

Elapsed: 2.07s
Result: ok

## Summary

| Step | Result | Seconds |
| --- | --- | ---: |
| 1 | ok | 2.03 |
| 2 | ok | 2.19 |
| 3 | ok | 0.24 |
| 4 | ok | 0.17 |
| 5 | ok | 16.49 |
| 6 | ok | 9.14 |
| 7 | ok | 2.07 |
