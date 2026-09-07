"""Run a small, real-process cxpeer conversation without a Claude session."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
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
) -> tuple[dict, dict]:
    """Build the request and idle-subscription frames sent by the fake Claude peer."""
    request_id = request_id or str(uuid.uuid4())
    idle_id = idle_id or str(uuid.uuid4())
    content = wire.envelope(text, own_address, "claude-demo")
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

    def wait_for(self, predicate, timeout: float) -> list[dict]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                matching = [frame for frame in self.frames if predicate(frame)]
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


def _answer(frame: dict) -> bool:
    return frame.get("type") == "user" and isinstance(frame.get("message", {}).get("content"), str)


def _idle_notice(frame: dict) -> bool:
    return frame.get("type") == "control" and frame.get("action") == "peer_idle_notice"


def _run_status() -> None:
    subprocess.run(["cxpeer", "status"], check=False)


def _kill_session(session: str | None) -> None:
    if session:
        subprocess.run(["tmux", "kill-session", "-t", session], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_demo(message: str, keep: bool, timeout: float, repo_root: Path) -> int:
    pid = os.getpid()
    sock_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
    sock_path = registry.sock_path_for(pid)
    token = secrets.token_hex(16)
    peer = DemoPeer(sock_path)
    session: str | None = None
    answer_arrived = False
    registered = False
    started = time.monotonic()

    try:
        peer.start()
        registry.register(pid, "claude-demo", str(repo_root), sock_path, token)
        registered = True
        print(transcript_line("registered", f"name=claude-demo address=uds:{sock_path}"), flush=True)

        peer_name = f"codex-demo-{secrets.token_hex(2)}"
        spawn_timeout = max(1, int(timeout))
        command = [
            "cxpeer", "spawn", "codex", "--cwd", str(repo_root),
            "--peer-name", peer_name, "--prompt", "Say READY and nothing else.",
            "--wait", "--timeout", str(spawn_timeout),
        ]
        try:
            result = subprocess.run(
                command, cwd=repo_root, capture_output=True, text=True,
                timeout=timeout + 10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(transcript_line("spawn failed", str(exc)), flush=True)
            return 1

        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        try:
            session = parse_tmux_session(output)
            registered_name = parse_peer_name(output)
        except ValueError as exc:
            print(transcript_line("spawn failed", str(exc)), flush=True)
            if output:
                print(output, file=sys.stderr, end="")
            return 1
        if result.returncode != 0:
            print(transcript_line("spawn failed", f"exit={result.returncode}"), flush=True)
            if output:
                print(output, file=sys.stderr, end="")
            return 1
        print(transcript_line("peer up", f"name={registered_name} tmux={session}"), flush=True)

        own_address = f"uds:{sock_path}"
        request, idle = build_frames(message, own_address)
        receiver = registry.resolve(registered_name)
        receiver_token = registry.token_for(receiver.sock)
        if not receiver_token:
            raise RuntimeError(f"could not read token for peer {registered_name!r}")
        wire.send_frames(receiver.sock, receiver_token, [request], timeout=5.0)
        print(transcript_line("sent", f"message={message!r}"), flush=True)
        wire.send_frames(receiver.sock, receiver_token, [idle], timeout=5.0)

        deadline = time.monotonic() + timeout
        answer_frames: list[dict] = []
        idle_frames: list[dict] = []
        while time.monotonic() < deadline and (not answer_frames or not idle_frames):
            remaining = max(0.0, deadline - time.monotonic())
            if not answer_frames:
                answer_frames = peer.wait_for(_answer, min(remaining, 0.5))
            if not idle_frames:
                idle_frames = peer.wait_for(_idle_notice, min(remaining, 0.5))

        if answer_frames:
            content = answer_frames[0]["message"]["content"]
            answer_text, _, _ = wire.strip_envelope(content)
            print(transcript_line("answer", answer_text), flush=True)
            answer_arrived = True
        if idle_frames:
            print(transcript_line("idle notice", idle_frames[0].get("detail", "")), flush=True)
        return 0 if answer_arrived else 1
    finally:
        _run_status()
        if not keep:
            _kill_session(session)
        if registered:
            registry.deregister(pid)
        peer.close()
        print(transcript_line("elapsed", f"{time.monotonic() - started:.1f}s"), flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a cxpeer demo with a fake Claude peer")
    parser.add_argument("--message", default="Reply with one sentence about what you can see in this directory.")
    parser.add_argument("--keep", action="store_true", help="leave the spawned tmux session running")
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for spawn and the answer")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_demo(args.message, args.keep, args.timeout, Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    raise SystemExit(main())
