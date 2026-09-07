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
ok  self-test: bridge answered ping and queued a message in 1356 ms
ok  live-bridges: codex-demo (thread 01a07dc5-26ab-7963-b11b-9e29c8920ea2): status=idle pending=0
```

Elapsed: 1.56s
Result: ok

## Step 2: Start a Codex peer from a Claude session

Command:
```text
cxpeer spawn codex --cwd /Users/hivamoh/funProjects/cxpeer --peer-name codex-journey-6a46 --prompt 'Say READY and nothing else.' --wait --timeout 120
```

Output:
```text
started cx-codex-cxpeer-ff5e (codex) in /Users/hivamoh/funProjects/cxpeer; attach with: tmux attach -t cx-codex-cxpeer-ff5e
peer codex-journey-6a46 is up (thread 01a07dfc-d7d2-79c1-ad66-de40bf4ba84d); attach with: tmux attach -t cx-codex-cxpeer-ff5e
```

Elapsed: 2.10s
Result: ok

## Step 3: Claude sees the new peer

Command:
```text
cxpeer list
```

Output:
```text
    claude-journey [9b16bf]  idle  /Users/hivamoh/funProjects/cxpeer
>>> codex-journey-6a46 [085176]  busy  /Users/hivamoh/funProjects/cxpeer
    (7 other live sessions on this machine omitted)
```

Elapsed: 0.09s
Result: ok

## Step 4: Claude gives Codex a real task

Command:
```text
connection 1 -> /tmp/cc-socks/31088.sock
{"token": "<peer token>", "type": "auth"}
{"from": "uds:/tmp/cc-socks/30810.sock", "message": {"content": "<cross-session-message from=\"uds:/tmp/cc-socks/30810.sock\" from-name=\"claude-journey\" from-mode=\"prompting\">\nList the three largest source files under cxpeer/ with their line counts, one line each.\n</cross-session-message>", "role": "user"}, "msgV": 1, "msg_id": "f9f4f0ff-e714-4b8e-8384-c4c71ffa8a8c", "priority": "next", "type": "user"}
connection 2 -> /tmp/cc-socks/31088.sock
{"token": "<peer token>", "type": "auth"}
{"action": "notify_when_idle", "from": "uds:/tmp/cc-socks/30810.sock", "from_mode": "prompting", "msgV": 1, "msg_id": "fa2a7467-9053-425d-a641-a7750b7ab664", "type": "control"}
```

Output:
```text
answer (+18.84s): cxpeer/bridge.py — 585 lines  
cxpeer/doctor.py — 340 lines  
cxpeer/client.py — 246 lines
peer_idle_notice (+18.84s): cxpeer/bridge.py — 585 lines cxpeer/doctor.py — 340 lines cxpeer/client.py — 246 lines
```

Elapsed: 18.84s
Result: ok

## Step 5: Codex reaches out on its own

Command:
```text
connection 1 -> /tmp/cc-socks/31088.sock
{"token": "<peer token>", "type": "auth"}
{"from": "uds:/tmp/cc-socks/30810.sock", "message": {"content": "<cross-session-message from=\"uds:/tmp/cc-socks/30810.sock\" from-name=\"claude-journey\" from-mode=\"prompting\">\nRun `cxpeer list`, then run `cxpeer send --to claude-journey \"hello from inside the Codex sandbox\"`, and reply with both exit codes.\n</cross-session-message>", "role": "user"}, "msgV": 1, "msg_id": "072305bc-62f1-4c2c-a97c-f6829af396eb", "priority": "next", "type": "user"}
```

Output:
```text
relayed message (+16.44s): hello from inside the Codex sandbox
answer (+18.08s): `cxpeer list`: exit code 0  
`cxpeer send`: exit code 0
```

Elapsed: 18.08s
Result: ok

## Step 7: Tear down

Command:
```text
tmux kill-session -t cx-codex-cxpeer-ff5e
cxpeer status
deregister claude-journey
```

Output:
```text
codex-journey-6a46  pid=31088  thread=01a07dfc-d7d2-79c1-ad66-de40bf4ba84d  idle  pending=0  /Users/hivamoh/funProjects/cxpeer
codex-demo  pid=9343  thread=01a07dc5-26ab-7963-b11b-9e29c8920ea2  idle  pending=0  /Users/hivamoh/funProjects/cxpeer

deregistered claude-journey
```

Elapsed: 0.20s
Result: ok

## Summary

| Step | Result | Seconds |
| --- | --- | ---: |
| 1 | ok | 1.56 |
| 2 | ok | 2.10 |
| 3 | ok | 0.09 |
| 4 | ok | 18.84 |
| 5 | ok | 18.08 |
| 7 | ok | 0.20 |
