# cxpeer implementation plan (one day, two engineers)

Spec: `docs/superpowers/specs/2026-09-07-cxpeer-design.md`. Read it first; module contracts live there.

Ground rules
- Repo: `~/funProjects/cxpeer`. Python 3.11+, zero runtime deps, `pytest` for tests.
- File ownership is strict. Only edit the files of your task. `git add` only your files, never `-A`.
- TDD per task: write the failing test, make it pass, run `pytest tests/<your test>` and paste the output in your update.
- Tests must never touch `~/.claude/sessions`, `/tmp/cc-socks`, `~/.cxpeer`, or the real `codex`. Use the env overrides from `cxpeer/paths.py` (Task 0).
- Report progress via SendMessage to `unclave-8b` when a task is done or blocked.

## Task 0 (owner: unclave-8b, done first): skeleton
`pyproject.toml`, `cxpeer/__init__.py`, `cxpeer/paths.py`, `tests/conftest.py` with the isolation fixtures
(`isolated_env` sets the four env vars; `fake_codex` writes a script that appends argv JSON lines to `$CXPEER_HOME/codex_calls.jsonl`).

## Task 1 (owner: unclave-25): `cxpeer/registry.py` + `tests/test_registry.py`
Implement every function in the spec's registry contract. Tests: register writes both files with the right
names and modes; `list_peers` marks a record alive only when `procStart` matches (use `os.getpid()` for alive,
a fake record with wrong procStart for dead); `token_for` finds the key by socket path hash; `resolve` by
name and by ref; `set_status` bumps `statusUpdatedAt`. Key name hash input is the literal socket path string.

## Task 2 (owner: unclave-25): `cxpeer/wire.py` + `tests/test_wire.py`
Envelope, user_frame, send_frames, read_lines, strip_envelope. Test send_frames against a UDS server started
inside the test; assert the auth line comes first and the user frame decodes with msgV 1, priority next.

## Task 3 (owner: unclave-25): `cxpeer/client.py` + `cxpeer/cli.py` (`list`, `send` only) + `tests/test_client.py`
`find_bridge` selection order from the spec; `send` speaks `cxpeer.relay` to a fake bridge socket in the test and
raises on `ok=false`; `list` output format `name [ref]  status  cwd`. Leave `bridge`, `hook`, `status`, `install`
subcommands as `NotImplementedError` stubs so unclave-8b can fill them in without conflicts.

## Task 4 (owner: unclave-8b): `cxpeer/bridge.py` + `tests/test_bridge.py`
Registration, auth gate, `user` -> `codex queue` with trailer, pending table, turn_started/turn_ended auto-reply
to a fake Claude UDS server, relay, ping, shutdown, watch-pid exit.

## Task 5 (owner: unclave-8b): `cxpeer/hooks.py`, `cli.py` (`hook`, `status`, `install`), `skills/cxpeer/SKILL.md`, `tests/test_hooks.py`
Hook handlers never fail the turn. `install` merges into `~/.codex/hooks.json` without duplicating.

## Task 6 (owner: unclave-8b): `scripts/e2e.sh`, `README.md`
tmux-driven manual e2e; README with install, how it works, limits.

Order: 0 -> (1,2,3 by unclave-25 in sequence) and (4,5 by unclave-8b in parallel, coding against the spec
contracts) -> integrate -> 6. Integration check: `pytest tests/` green, then e2e.
