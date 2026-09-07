# Demo

`demo/demo.py` plays the Claude side without a Claude Code session. It registers a
temporary peer named `claude-demo`, starts a real Codex peer with `cxpeer spawn --wait`,
sends it one message plus an idle subscription, and waits for the answer and the idle
notice on its own socket. It prints a timestamped transcript and tears everything down.

Run it from a checkout after `cxpeer install` (and `cxpeer doctor` is green):

```sh
.venv/bin/python demo/demo.py
```

Flags: `--message TEXT`, `--keep` (leave the Codex session running), `--timeout S`.
Exit code 0 only if the answer arrived.

## Sample run

2026-09-07, macOS, Codex 0.153.3, Claude Code 2.1.263. The two `codex-demo` lines in
the middle are `cxpeer status`; the second bridge belongs to another Codex session that
was open at the time.

```text
[2026-09-07T21:52:45+00:00] registered name=claude-demo address=uds:/tmp/cc-socks/20439.sock
[2026-09-07T21:52:48+00:00] peer up name=codex-demo-28f1 tmux=cx-codex-cxpeer-7a36
[2026-09-07T21:52:48+00:00] sent message='Reply with one sentence about what you can see in this directory.'
[2026-09-07T21:52:55+00:00] answer I can see a cxpeer project containing the Python package, tests, scripts, documentation, plans, and image assets.
[2026-09-07T21:52:55+00:00] idle notice I can see a cxpeer project containing the Python package, tests, scripts, documentation, plans, and image assets.
codex-demo-28f1  pid=20714  thread=01a07ddc-047a-7421-b163-a3f2e08d1f02  idle  pending=0  /Users/hivamoh/funProjects/cxpeer
codex-demo  pid=9343  thread=01a07dc5-26ab-7963-b11b-9e29c8920ea2  idle  pending=0  /Users/hivamoh/funProjects/cxpeer
[2026-09-07T21:52:55+00:00] elapsed 9.8s
```
