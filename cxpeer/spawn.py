"""`cxpeer spawn`: start a new Codex or Claude session in a detached tmux session."""

from __future__ import annotations

import os
import secrets
import shlex
import subprocess
import sys

KINDS = ("codex", "claude")


def tmux_bin() -> str:
    return os.environ.get("CXPEER_TMUX_BIN") or "tmux"


def run(kind: str, cwd: str, name: str | None, prompt: str | None, extra_args: list[str]) -> int:
    if kind not in KINDS:
        print(f"cxpeer spawn: unknown kind {kind!r} (expected codex or claude)", file=sys.stderr)
        return 2
    cwd = os.path.abspath(cwd)
    session = f"cx-{name}" if name else f"cx-{kind}-{os.path.basename(cwd) or 'root'}-{secrets.token_hex(2)}"
    tmux = tmux_bin()

    try:
        if _tmux(tmux, "has-session", "-t", session).returncode == 0:
            print(f"cxpeer spawn: tmux session {session!r} already exists", file=sys.stderr)
            return 1
        command = shlex.join(_command(kind, name or session, prompt, extra_args))
        res = _tmux(tmux, "new-session", "-d", "-s", session, "-c", cwd, command)
    except FileNotFoundError:
        print(f"cxpeer spawn: tmux not found at {tmux!r}; install tmux or set CXPEER_TMUX_BIN", file=sys.stderr)
        return 1
    if res.returncode != 0:
        print(f"cxpeer spawn: tmux new-session failed: {res.stderr.strip()}", file=sys.stderr)
        return 1

    print(f"started {session} ({kind}) in {cwd}; attach with: tmux attach -t {session}")
    return 0


def _command(kind: str, claude_name: str, prompt: str | None, extra_args: list[str]) -> list[str]:
    argv = ["codex"] if kind == "codex" else ["claude", "-n", claude_name]
    argv += list(extra_args)
    if prompt is not None:
        argv.append(prompt)
    return argv


def _tmux(tmux: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([tmux, *args], capture_output=True, text=True)
