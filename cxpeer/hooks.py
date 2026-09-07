"""Codex hook handlers (`cxpeer hook <event>`).

A hook must never fail a Codex turn: every handler swallows errors into
logs_dir()/hooks.log, returns 0, and writes nothing to stdout.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from typing import Callable

from . import paths, wire

MSG_ID_RE = re.compile(r"\[cxpeer msg_id=([^\]\s]+)\]")
Lookup = Callable[[int], "tuple[int, str] | None"]


def run(event: str, payload: dict) -> int:
    """Dispatch one hook event. Always returns 0."""
    try:
        handler = _HANDLERS.get(event)
        if handler is None:
            _log(f"{event}: unknown event, ignored")
        else:
            handler(payload)
    except Exception as exc:  # a hook failure must never reach Codex
        _log(f"{event}: {type(exc).__name__}: {exc}")
    return 0


def _session_start(payload: dict) -> None:
    thread = payload["session_id"]
    cwd = payload.get("cwd") or os.getcwd()
    state = _read_bridge(thread)
    if state and _alive(state.get("pid")):
        _log(f"session-start: bridge for {thread} already running (pid {state['pid']})")
        return
    codex_pid = find_codex_pid(os.getppid())
    argv = [sys.executable, "-m", "cxpeer", "bridge", "--thread", thread, "--cwd", cwd,
            "--watch-pid", str(codex_pid)]
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _log(f"session-start: spawned bridge pid {proc.pid} for {thread}, watching codex pid {codex_pid}")


def _user_prompt_submit(payload: dict) -> None:
    m = MSG_ID_RE.search(payload.get("prompt") or "")
    _send(payload, {"type": "cxpeer.turn_started", "msg_id": m.group(1) if m else None})


def _stop(payload: dict) -> None:
    _send(payload, {"type": "cxpeer.turn_ended",
                    "last_assistant_message": payload.get("last_assistant_message")})


def _session_end(payload: dict) -> None:
    _send(payload, {"type": "cxpeer.shutdown"})


_HANDLERS = {
    "session-start": _session_start,
    "user-prompt-submit": _user_prompt_submit,
    "stop": _stop,
    "session-end": _session_end,
}


def _send(payload: dict, frame: dict) -> None:
    thread = payload["session_id"]
    state = _read_bridge(thread)
    if state is None:
        _log(f"{frame['type']}: no bridge state for thread {thread}")
        return
    wire.send_frames(state["sock"], state["token"], [frame])
    _log(f"{frame['type']}: sent to bridge {state.get('name')} for {thread}")


def _read_bridge(thread: str) -> dict | None:
    path = paths.bridges_dir() / f"{thread}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _alive(pid: object) -> bool:
    try:
        os.kill(int(pid), 0)  # type: ignore[arg-type]
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError, OverflowError):
        return False
    return True


def find_codex_pid(start_pid: int, lookup: Lookup | None = None, max_levels: int = 5) -> int:
    """Walk up from start_pid (inclusive) at most max_levels; return the first pid whose
    command mentions codex, else start_pid."""
    lookup = lookup or ps_lookup
    pid = start_pid
    for _ in range(max_levels):
        info = lookup(pid)
        if info is None:
            break
        ppid, comm = info
        if "codex" in comm.lower():
            return pid
        if ppid <= 1:
            break
        pid = ppid
    return start_pid


def ps_lookup(pid: int) -> tuple[int, str] | None:
    """(ppid, comm) for pid via `ps`, or None if ps fails or the pid is gone."""
    try:
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    parts = out.stdout.strip().split(None, 1)
    if out.returncode != 0 or not parts:
        return None
    return int(parts[0]), (parts[1] if len(parts) > 1 else "")


def _log(msg: str) -> None:
    try:
        d = paths.logs_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "hooks.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} pid={os.getpid()} {msg}\n")
    except Exception:
        pass
