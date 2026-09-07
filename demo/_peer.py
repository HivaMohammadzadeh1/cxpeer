"""Shared fake Claude peer and wire helpers for the runnable demos."""

from __future__ import annotations

import os
import re
import secrets
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cxpeer import registry, wire
from cxpeer.paths import sock_dir


_STARTED_RE = re.compile(r"^started\s+(\S+)\s+\(codex\)", re.MULTILINE)
_PEER_RE = re.compile(r"^peer\s+(\S+)\s+is up", re.MULTILINE)


def transcript_line(event: str, detail: str = "", now: datetime | None = None) -> str:
    """Format one UTC transcript line; kept separate so it is easy to test."""
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    suffix = f" {detail}" if detail else ""
    return f"[{stamp}] {event}{suffix}"


def build_frames(
    text: str,
    own_address: str,
    request_id: str | None = None,
    idle_id: str | None = None,
    from_name: str = "claude-demo",
) -> tuple[dict, dict]:
    """Build the request and idle-subscription frames sent by a fake Claude peer."""
    request_id = request_id or str(uuid.uuid4())
    idle_id = idle_id or str(uuid.uuid4())
    content = wire.envelope(text, own_address, from_name)
    user = wire.user_frame(content, own_address, request_id)
    idle = {
        "type": "control",
        "action": "notify_when_idle",
        "from": own_address,
        "from_mode": "prompting",
        "msgV": 1,
        "msg_id": idle_id,
    }
    return user, idle


def parse_peer_name(output: str) -> str:
    """Extract the registered peer name from ``cxpeer spawn --wait`` output."""
    match = _PEER_RE.search(output)
    if not match:
        raise ValueError("spawn output did not contain a registered peer name")
    return match.group(1)


def parse_tmux_session(output: str) -> str:
    """Extract the tmux session name printed by ``cxpeer spawn``."""
    match = _STARTED_RE.search(output)
    if not match:
        raise ValueError("spawn output did not contain a tmux session name")
    return match.group(1)


class DemoPeer:
    """A Claude-shaped UDS peer that records every JSON line it receives."""

    def __init__(self, sock_path: str) -> None:
        self.sock_path = sock_path
        self.frames: list[dict] = []
        self._condition = threading.Condition()
        self._server: socket.socket | None = None
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        Path(self.sock_path).unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.sock_path)
        os.chmod(self.sock_path, 0o600)
        server.listen(8)
        server.settimeout(0.2)
        self._server = server
        self._thread = threading.Thread(target=self._serve, name="claude-demo-peer", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        assert self._server is not None
        while not self._stopping.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                frames = wire.read_lines(conn, timeout=5.0)
            with self._condition:
                self.frames.extend(frames)
                self._condition.notify_all()

    def count(self) -> int:
        with self._condition:
            return len(self.frames)

    def wait_for(self, predicate, timeout: float, start_index: int = 0) -> list[dict]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                matching = [frame for frame in self.frames[start_index:] if predicate(frame)]
                if matching:
                    return matching
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)

    def close(self) -> None:
        self._stopping.set()
        if self._server is not None:
            self._server.close()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        Path(self.sock_path).unlink(missing_ok=True)


@dataclass
class RegisteredPeer:
    """A DemoPeer plus the Claude registry files that make it discoverable."""

    name: str
    cwd: str
    pid: int
    sock_path: str
    token: str
    peer: DemoPeer
    registered: bool = False

    @property
    def address(self) -> str:
        return f"uds:{self.sock_path}"

    def close(self) -> None:
        if self.registered:
            registry.deregister(self.pid)
            self.registered = False
        self.peer.close()


def register_fake_peer(name: str, cwd: str) -> RegisteredPeer:
    """Start and register a fake Claude peer in the current process."""
    pid = os.getpid()
    sock_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
    sock_path = registry.sock_path_for(pid)
    fake = RegisteredPeer(name, cwd, pid, sock_path, secrets.token_hex(16), DemoPeer(sock_path))
    fake.peer.start()
    try:
        registry.register(pid, name, cwd, sock_path, fake.token)
        fake.registered = True
    except Exception:
        fake.peer.close()
        raise
    return fake


def is_answer(frame: dict) -> bool:
    return frame.get("type") == "user" and isinstance(frame.get("message", {}).get("content"), str)


def is_idle_notice(frame: dict) -> bool:
    return frame.get("type") == "control" and frame.get("action") == "peer_idle_notice"
