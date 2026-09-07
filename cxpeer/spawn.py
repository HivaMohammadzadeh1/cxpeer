"""`cxpeer spawn`: start a new Codex or Claude session in a detached tmux session."""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time

from . import paths

KINDS = ("codex", "claude")
INPUT_BOX = "Ask Codex to do anything"
READY_PROMPT = "Say READY and nothing else."
# Codex startup prompts, in the order they tend to appear, and the key that answers each.
PROMPTS = (
    ("update", re.compile(r"Update available"), "2"),
    ("trust", re.compile(r"Do you trust the contents"), "1"),
    ("hooks", re.compile(r"hooks? (is|are) new or changed"), "2"),
)

_sleep = time.sleep
_monotonic = time.monotonic


def tmux_bin() -> str:
    return os.environ.get("CXPEER_TMUX_BIN") or "tmux"


def run(kind: str, cwd: str, name: str | None, prompt: str | None, extra_args: list[str],
        peer_name: str | None = None, wait: bool = False, timeout: float = 60.0,
        codex_home: str | None = None) -> int:
    if kind not in KINDS:
        print(f"cxpeer spawn: unknown kind {kind!r} (expected codex or claude)", file=sys.stderr)
        return 2
    cwd = os.path.abspath(cwd)
    session = f"cx-{name}" if name else f"cx-{kind}-{os.path.basename(cwd) or 'root'}-{secrets.token_hex(2)}"
    if peer_name is None:
        peer_name = name
    tmux = tmux_bin()
    start_ms = int(time.time()) * 1000  # whole seconds, so a bridge started this second counts

    try:
        if _tmux(tmux, "has-session", "-t", session).returncode == 0:
            print(f"cxpeer spawn: tmux session {session!r} already exists", file=sys.stderr)
            return 1
        command = shlex.join(_command(kind, name or session, prompt, extra_args))
        if peer_name:
            command = f"CXPEER_PEER_NAME={shlex.quote(peer_name)} {command}"
        if codex_home and kind == "codex":  # a second Codex account: its own auth, config, hooks, queue
            command = f"CODEX_HOME={shlex.quote(os.path.abspath(codex_home))} {command}"
        res = _tmux(tmux, "new-session", "-d", "-s", session, "-c", cwd, command)
        if res.returncode != 0:
            print(f"cxpeer spawn: tmux new-session failed: {res.stderr.strip()}", file=sys.stderr)
            return 1
        print(f"started {session} ({kind}) in {cwd}; attach with: tmux attach -t {session}")
        if not wait:
            return 0
        if kind == "claude":
            _wait_settled(tmux, session, timeout)
            return 0
        return _wait_codex(tmux, session, cwd, prompt, start_ms, timeout)
    except FileNotFoundError:
        print(f"cxpeer spawn: tmux not found at {tmux!r}; install tmux or set CXPEER_TMUX_BIN", file=sys.stderr)
        return 1


def _command(kind: str, claude_name: str, prompt: str | None, extra_args: list[str]) -> list[str]:
    argv = ["codex"] if kind == "codex" else ["claude", "-n", claude_name]
    argv += list(extra_args)
    if prompt is not None:
        argv.append(prompt)
    return argv


def _wait_codex(tmux: str, session: str, cwd: str, prompt: str | None, start_ms: int, timeout: float) -> int:
    """Answer Codex's startup prompts, make sure a first prompt is submitted, then wait for the bridge."""
    deadline = _monotonic() + timeout
    answered: set[str] = set()
    while True:
        screen = _pane(tmux, session)
        if INPUT_BOX in screen:
            break
        for key, pattern, answer in PROMPTS:
            if key not in answered and pattern.search(screen):
                answered.add(key)
                _tmux(tmux, "send-keys", "-t", session, answer, "Enter")
                break
        if _monotonic() >= deadline:
            return _timed_out(f"codex in {session} never showed its input box", screen, session, timeout)
        _sleep(1)

    if prompt is None:  # codex creates its thread, and so the bridge, only on the first prompt
        _tmux(tmux, "send-keys", "-t", session, READY_PROMPT, "Enter")
        _sleep(2)
        _tmux(tmux, "send-keys", "-t", session, "Enter")

    while True:
        state = _find_bridge(cwd, start_ms)
        if state:
            print(f"peer {state['name']} is up (thread {state['thread']}); attach with: tmux attach -t {session}")
            return 0
        if _monotonic() >= deadline:
            return _timed_out(f"no bridge appeared for {cwd} (see ~/.cxpeer/logs/hooks.log)",
                              _pane(tmux, session), session, timeout)
        _sleep(1)


def _wait_settled(tmux: str, session: str, timeout: float) -> None:
    """Return once two consecutive pane captures are identical and non-empty, or at the deadline."""
    deadline = _monotonic() + timeout
    prev = None
    while _monotonic() < deadline:
        screen = _pane(tmux, session)
        if screen.strip() and screen == prev:
            return
        prev = screen
        _sleep(1)


def _find_bridge(cwd: str, start_ms: int) -> dict | None:
    """Newest bridge state whose cwd is `cwd` (through symlinks) and that started at or after start_ms."""
    want = os.path.realpath(cwd)
    bridges = paths.bridges_dir()
    if not bridges.is_dir():
        return None
    best = None
    for path in bridges.glob("*.json"):
        if path.name.endswith(".peers.json"):
            continue
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(state, dict) or os.path.realpath(str(state.get("cwd", ""))) != want:
            continue
        started = int(state.get("started") or 0)
        if started < start_ms:
            continue
        if best is None or started > int(best.get("started") or 0):
            best = state
    return best


def _timed_out(what: str, screen: str, session: str, timeout: float) -> int:
    print(f"cxpeer spawn: timed out after {timeout:g}s: {what}; attach with: tmux attach -t {session}\n"
          f"last screen:\n{screen.rstrip()}", file=sys.stderr)
    return 1


def _pane(tmux: str, session: str) -> str:
    return _tmux(tmux, "capture-pane", "-p", "-t", session).stdout


def _tmux(tmux: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([tmux, *args], capture_output=True, text=True)
