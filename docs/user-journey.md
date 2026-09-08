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
ok  claude-sessions: /Users/hivamoh/.claude-alt/sessions present (0700)
ok  socket-dir: /tmp/cc-socks present
ok  codex: version 0.153.3
ok  codex-hooks: all five cxpeer hooks present with a valid interpreter
ok  codex-trust: all five hooks trusted in config.toml
ok  codex-skill: /Users/hivamoh/.codex/skills/cxpeer/SKILL.md up to date
ok  tmux: present
ok  self-test: bridge answered ping and queued a message in 1487 ms
ok  live-bridges: (2 other bridges on this machine omitted)
```

Elapsed: 2.14s
Result: ok

## Step 2: Start a Codex peer from a Claude session

Command:
```text
cxpeer spawn codex --cwd /Users/hivamoh/funProjects/cxpeer --peer-name codex-journey-26dc --prompt 'Say READY and nothing else.' --wait --timeout 120
```

Output:
```text
started cx-codex-cxpeer-15f2 (codex) in /Users/hivamoh/funProjects/cxpeer; attach with: tmux attach -t cx-codex-cxpeer-15f2
peer codex-journey-26dc is up (thread 01a081aa-13e2-7b42-94ed-cff214be3473); attach with: tmux attach -t cx-codex-cxpeer-15f2
```

Elapsed: 3.22s
Result: ok

## Step 3: Claude sees the new peer

Command:
```text
cxpeer list
```

Output:
```text
    claude-journey [024c76]  idle  /Users/hivamoh/funProjects/cxpeer
>>> codex-journey-26dc [a571e8]  idle  /Users/hivamoh/funProjects/cxpeer
    (8 other live sessions on this machine omitted)
```

Elapsed: 0.24s
Result: ok

## Step 4: Claude gives Codex a real task

Command:
```text
connection 1 -> /tmp/cc-socks/97461.sock
{"token": "<peer token>", "type": "auth"}
{"from": "uds:/tmp/cc-socks/97057.sock", "message": {"content": "<cross-session-message from=\"uds:/tmp/cc-socks/97057.sock\" from-name=\"claude-journey\" from-mode=\"prompting\">\nList the three largest source files under cxpeer/ with their line counts, one line each.\n</cross-session-message>", "role": "user"}, "msgV": 1, "msg_id": "7f92018c-4e3d-4102-b68a-0d4e2b503fc7", "priority": "next", "type": "user"}
connection 2 -> /tmp/cc-socks/97461.sock
{"token": "<peer token>", "type": "auth"}
{"action": "notify_when_idle", "from": "uds:/tmp/cc-socks/97057.sock", "from_mode": "prompting", "msgV": 1, "msg_id": "3e189f71-1ed0-4787-9da3-2fd72f797431", "type": "control"}
```

Output:
```text
answer (+18.50s): cxpeer/bridge.py: 585 lines
cxpeer/doctor.py: 347 lines
cxpeer/client.py: 246 lines
peer_idle_notice (+18.61s): cxpeer/bridge.py: 585 lines cxpeer/doctor.py: 347 lines cxpeer/client.py: 246 lines
```

Elapsed: 18.65s
Result: ok

## Step 5: Codex reaches out on its own

Command:
```text
connection 1 -> /tmp/cc-socks/97461.sock
{"token": "<peer token>", "type": "auth"}
{"from": "uds:/tmp/cc-socks/97057.sock", "message": {"content": "<cross-session-message from=\"uds:/tmp/cc-socks/97057.sock\" from-name=\"claude-journey\" from-mode=\"prompting\">\nRun `cxpeer send --to claude-journey \"hello from inside the Codex sandbox\"` and reply with its exit code.\n</cross-session-message>", "role": "user"}, "msgV": 1, "msg_id": "39d48e87-b681-43ea-b37c-4cf104eca7ca", "priority": "next", "type": "user"}
```

Output:
```text
relayed message (+7.26s): hello from inside the Codex sandbox
answer (+9.42s): Exit code: 0
```

Elapsed: 9.46s
Result: ok

## Step 6: Tear down

Command:
```text
tmux kill-session -t cx-codex-cxpeer-15f2
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
| 1 | ok | 2.14 |
| 2 | ok | 3.22 |
| 3 | ok | 0.24 |
| 4 | ok | 18.65 |
| 5 | ok | 9.46 |
| 6 | ok | 2.07 |
