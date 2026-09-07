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

from . import paths, registry, wire

MSG_ID_RE = re.compile(r"\[cxpeer msg_id=([^\]\s]+)\]")
PEER_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
Lookup = Callable[[int], "tuple[int, str] | None"]

_sleep = time.sleep


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
    if state and _bridge_alive(state):
        _log(f"session-start: bridge for {thread} already running (pid {state['pid']})")
        return
    _spawn_bridge(thread, cwd, "session-start")


def _spawn_bridge(thread: str, cwd: str, why: str) -> None:
    """Start a detached bridge for this Codex thread. CXPEER_PEER_NAME (set by `cxpeer spawn`
    and inherited from codex) names the peer when it is a plain, safe name."""
    codex_pid = find_codex_pid(os.getppid())
    argv = [sys.executable, "-m", "cxpeer", "bridge", "--thread", thread, "--cwd", cwd,
            "--watch-pid", str(codex_pid)]
    peer_name = os.environ.get("CXPEER_PEER_NAME")
    if peer_name:
        if PEER_NAME_RE.match(peer_name):
            argv += ["--name", peer_name]
        else:
            _log(f"{why}: ignoring CXPEER_PEER_NAME {peer_name!r} (allowed: letters, digits, . _ - up to 40)")
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _log(f"{why}: spawned bridge pid {proc.pid} for {thread}, watching codex pid {codex_pid}")


def _user_prompt_submit(payload: dict) -> None:
    m = MSG_ID_RE.search(payload.get("prompt") or "")
    _send(payload, {"type": "cxpeer.turn_started", "msg_id": m.group(1) if m else None})


def _stop(payload: dict) -> None:
    _send(payload, {"type": "cxpeer.turn_ended",
                    "last_assistant_message": payload.get("last_assistant_message")})


def _interrupt(payload: dict) -> None:
    """Codex fires Interrupt instead of Stop when a turn is cancelled; end the turn so the
    bridge does not stay busy with the request pending."""
    _send(payload, {"type": "cxpeer.turn_ended", "last_assistant_message": None,
                    "reason": "interrupted"})


def _session_end(payload: dict) -> None:
    _send(payload, {"type": "cxpeer.shutdown"})


_HANDLERS = {
    "session-start": _session_start,
    "user-prompt-submit": _user_prompt_submit,
    "stop": _stop,
    "interrupt": _interrupt,
    "session-end": _session_end,
}


def _send(payload: dict, frame: dict) -> None:
    """Deliver one control frame to this thread's bridge, starting a new bridge first if the
    recorded one is missing or dead (a hook can outlive the bridge that was there at start)."""
    thread = payload["session_id"]
    kind = frame["type"]
    state = _read_bridge(thread)
    if state is None:
        # session-start may be starting the bridge right now; give it a moment before respawning
        state = _wait_for_bridge(thread)
    if state is None or not _bridge_alive(state):
        _log(f"{kind}: {'no bridge state' if state is None else 'bridge dead'} for thread {thread}; respawning")
        _spawn_bridge(thread, payload.get("cwd") or os.getcwd(), kind)
        state = _wait_for_bridge(thread)
        if state is None:
            _log(f"{kind}: respawned bridge for {thread} wrote no state within 3s; frame dropped")
            return
        _log(f"{kind}: respawned bridge {state.get('name')} for {thread}")
    wire.send_frames(state["sock"], state["token"], [frame])
    _log(f"{kind}: sent to bridge {state.get('name')} for {thread}")


def _wait_for_bridge(thread: str, attempts: int = 30, interval: float = 0.1) -> dict | None:
    """Poll for a live bridge state file, up to attempts * interval seconds (3s by default)."""
    for _ in range(attempts):
        state = _read_bridge(thread)
        if state and _bridge_alive(state):
            return state
        _sleep(interval)
    return None


def _read_bridge(thread: str) -> dict | None:
    path = paths.bridges_dir() / f"{thread}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _bridge_alive(state: dict) -> bool:
    """True when the pid in a bridge state file is still that bridge, not a recycled pid."""
    pid = state.get("pid")
    if not _alive(pid):
        return False
    expected = state.get("proc_start")
    if expected is None:  # state written before proc_start was recorded
        return True
    return registry.proc_start(int(pid)) == expected


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
