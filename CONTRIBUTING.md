# Contributing

## Set up

1. Clone the repository and create a virtual environment:
   ```
   uv venv .venv --python 3.13
   uv pip install --python .venv/bin/python -e ".[dev]"
   ```
2. Run the tests:
   ```
   .venv/bin/python -m pytest tests -q
   ```
   The system `python3` on a Mac can be 3.8. cxpeer needs 3.11 or newer.
3. For a live check, `cxpeer doctor` runs a self-test against a real Claude Code registry, and `scripts/e2e.sh` starts a real Codex in tmux.

## How the tests fake Codex and Claude

No test touches `~/.claude/sessions`, `/tmp/cc-socks`, `~/.cxpeer`, `~/.codex`, or the real `codex` binary.

- `isolated_env` (tests/conftest.py) points every path from `cxpeer/paths.py` at a temporary directory through the `CXPEER_*` environment variables. Use it in every test that reads or writes state.
- `fake_codex` writes a shell script that records its arguments to `codex_calls.jsonl` and sets `CXPEER_CODEX_BIN` to it. A "queued" message is a line in that file.
- Claude peers are fake Unix-socket servers started inside the test (`FakeBridge` in tests/test_hooks.py, the fake Claude in tests/test_bridge.py). They read the auth line and the frames, and the test asserts on the decoded JSON.
- tmux is a shell script under `$CXPEER_HOME` that records its arguments, serves canned `capture-pane` screens, and marks sessions as existing (tests/test_spawn.py). A fake clock replaces the sleeps, so the 60-second paths run in milliseconds.
- Process spawns go through a `FakePopen` that records the arguments and can write a bridge state file to imitate a bridge that starts.

Write the failing test first. Run only the test files you touched while you work. Run the whole suite before you commit.

## File ownership

Several people, and several agent sessions, commit into the same working tree. To avoid conflicts:

- Edit only the files of your task. Keep a test file next to each module you change.
- Stage files by name: `git add cxpeer/spawn.py tests/test_spawn.py`. Never use `git add -A` or `git commit -a`.
- If your change needs a file that someone else owns, tell them what you need instead of editing it.

## Commits

- Plain imperative subject line: `Add cxpeer read`, `Fix stale bridge state after interrupt`.
- No attribution trailers (`Co-Authored-By`, `Signed-off-by`, session links).
- One deliverable per commit.

## Pull requests

Fill in the Summary and Test plan sections of the template. CI runs the suite on macOS and Linux. Both must be green.
