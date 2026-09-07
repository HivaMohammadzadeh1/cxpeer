"""The bridge: one process per Codex session, acting as that session's Claude Code peer.

Inbound Claude frames (`type=user`) are pushed into the Codex TUI with `codex queue`.
Codex hooks and the `cxpeer send` CLI talk to the same socket with `cxpeer.*` control
frames. Every connection must start with an auth line carrying this bridge's token.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import registry, wire
from .paths import bridges_dir, codex_bin, logs_dir, sock_dir

LOG = logging.getLogger("cxpeer.bridge")

CONNECTION_IDLE_SECONDS = 5.0
WATCH_INTERVAL_SECONDS = 5.0
CODEX_QUEUE_TIMEOUT_SECONDS = 30.0
UNDELIVERABLE_NOTE = "cxpeer: could not deliver your message to Codex session {name}: {error}"
EMPTY_TURN_NOTE = "(Codex ended the turn without a final message.)"


def default_name(cwd: str, thread: str) -> str:
    # Thread ids are UUIDv7, so the leading hex is a timestamp shared by every session
    # started the same day; the trailing hex is what actually varies.
    suffix = thread.replace("-", "")[-2:] or "00"
    return f"codex-{Path(cwd).name or 'root'}-{suffix}"


def queue_text(content: str, msg_id: str, from_name: str) -> str:
    """The text handed to `codex queue`: the peer's message plus how replies work."""
    return (
        f"{content}\n\n[cxpeer msg_id={msg_id}] Your final answer this turn is forwarded to "
        f"{from_name} automatically. To message any session yourself run: "
        f'cxpeer send --to <name> "text" (cxpeer list shows names).'
    )


def reply_socket(from_addr: object) -> str | None:
    """`uds:/path.sock` -> `/path.sock`; anything else is not a reply address we can use."""
    if isinstance(from_addr, str) and from_addr.startswith("uds:/"):
        return from_addr[len("uds:"):]
    return None


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class PendingRequest:
    msg_id: str
    reply_sock: str | None
    from_name: str
    queued_at: float


class Bridge:
    def __init__(
        self,
        thread: str,
        cwd: str,
        name: str | None = None,
        watch_pid: int | None = None,
        codex: str | None = None,
    ) -> None:
        self.thread = thread
        self.cwd = cwd
        self.name = name or default_name(cwd, thread)
        self.watch_pid = watch_pid
        self.codex = codex or codex_bin()
        self.pid = os.getpid()
        self.token = secrets.token_hex(16)
        self.sock_path = registry.sock_path_for(self.pid)
        self.address = f"uds:{self.sock_path}"
        self.status = "idle"
        self.pending: dict[str, PendingRequest] = {}
        self.active: str | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._server: socket.socket | None = None

    # ----- lifecycle -----

    @property
    def state_path(self) -> Path:
        return bridges_dir() / f"{self.thread}.json"

    def start(self) -> None:
        sock_dir().mkdir(mode=0o700, exist_ok=True)
        Path(self.sock_path).unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.sock_path)
        os.chmod(self.sock_path, 0o600)
        server.listen(16)
        server.settimeout(1.0)
        self._server = server
        registry.register(self.pid, self.name, self.cwd, self.sock_path, self.token)
        self._write_state()
        LOG.info("bridge %s up: pid=%s sock=%s thread=%s", self.name, self.pid, self.sock_path, self.thread)

    def serve_forever(self) -> None:
        assert self._server is not None, "start() before serve_forever()"
        next_watch = time.monotonic()
        while not self._stop.is_set():
            if self.watch_pid is not None and time.monotonic() >= next_watch:
                next_watch = time.monotonic() + WATCH_INTERVAL_SECONDS
                if not pid_alive(self.watch_pid):
                    LOG.info("watched pid %s is gone; shutting down", self.watch_pid)
                    break
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError as exc:
                LOG.error("accept failed: %s", exc)
                break
            threading.Thread(target=self._serve_connection, args=(conn,), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def cleanup(self) -> None:
        for step, action in (
            ("deregister", lambda: registry.deregister(self.pid)),
            ("remove state", lambda: self.state_path.unlink(missing_ok=True)),
            ("unlink socket", lambda: Path(self.sock_path).unlink(missing_ok=True)),
        ):
            try:
                action()
            except OSError as exc:
                LOG.error("cleanup %s failed: %s", step, exc)
        if self._server is not None:
            self._server.close()
        LOG.info("bridge %s down", self.name)

    def _write_state(self) -> None:
        bridges_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
        state = {
            "pid": self.pid,
            "sock": self.sock_path,
            "token": self.token,
            "name": self.name,
            "cwd": self.cwd,
            "thread": self.thread,
            "started": int(time.time() * 1000),
        }
        tmp = self.state_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, self.state_path)

    # ----- connections -----

    def _serve_connection(self, conn: socket.socket) -> None:
        conn.settimeout(CONNECTION_IDLE_SECONDS)
        try:
            reader = conn.makefile("r", encoding="utf-8", errors="replace")
            if not self._authenticated(reader.readline()):
                LOG.warning("dropped connection: bad or missing auth line")
                return
            for line in reader:
                frame = self._parse(line)
                if frame is None:
                    continue
                response = self._dispatch(frame)
                if response is not None:
                    conn.sendall((json.dumps(response) + "\n").encode())
                if frame.get("type") == "cxpeer.shutdown":
                    break
        except (TimeoutError, OSError) as exc:
            LOG.debug("connection closed: %s", exc)
        finally:
            conn.close()

    def _authenticated(self, line: str) -> bool:
        frame = self._parse(line)
        return (
            frame is not None
            and frame.get("type") == "auth"
            and secrets.compare_digest(str(frame.get("token", "")), self.token)
        )

    @staticmethod
    def _parse(line: str) -> dict | None:
        line = line.strip()
        if not line:
            return None
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            LOG.warning("ignoring non-JSON line")
            return None
        if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
            LOG.warning("ignoring frame without a string type")
            return None
        return frame

    def _dispatch(self, frame: dict) -> dict | None:
        handlers = {
            "user": self.on_user,
            "cxpeer.turn_started": self.on_turn_started,
            "cxpeer.turn_ended": self.on_turn_ended,
            "cxpeer.relay": self.on_relay,
            "cxpeer.ping": self.on_ping,
            "cxpeer.shutdown": self.on_shutdown,
        }
        handler = handlers.get(frame["type"])
        if handler is None:
            LOG.info("ignoring frame type %r", frame["type"])
            return None
        return handler(frame)

    # ----- frame handlers -----

    def on_user(self, frame: dict) -> None:
        message = frame.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content:
            LOG.warning("ignoring user frame without string content")
            return None
        msg_id = frame.get("msg_id") if isinstance(frame.get("msg_id"), str) else str(uuid.uuid4())
        reply_sock = reply_socket(frame.get("from"))
        _, _, from_name = wire.strip_envelope(content)
        from_name = from_name or str(frame.get("from") or "unknown")
        with self._lock:
            self.pending[msg_id] = PendingRequest(msg_id, reply_sock, from_name, time.time())
        LOG.info("user message %s from %s (%s)", msg_id, from_name, reply_sock)

        error = self._queue_into_codex(queue_text(content, msg_id, from_name))
        if error is None:
            return None
        LOG.error("codex queue failed for %s: %s", msg_id, error)
        with self._lock:
            self.pending.pop(msg_id, None)
        if reply_sock is not None:
            self._try_send(reply_sock, UNDELIVERABLE_NOTE.format(name=self.name, error=error))
        return None

    def on_turn_started(self, frame: dict) -> None:
        self._set_status("busy")
        msg_id = frame.get("msg_id")
        with self._lock:
            self.active = msg_id if isinstance(msg_id, str) and msg_id in self.pending else None
        LOG.info("turn started (active request: %s)", self.active)
        return None

    def on_turn_ended(self, frame: dict) -> None:
        self._set_status("idle")
        with self._lock:
            request = self.pending.pop(self.active, None) if self.active else None
            self.active = None
        if request is None:
            LOG.info("turn ended; no peer request to answer")
            return None
        answer = frame.get("last_assistant_message")
        text = answer if isinstance(answer, str) and answer.strip() else EMPTY_TURN_NOTE
        if request.reply_sock is None:
            LOG.warning("request %s had no reply address; answer dropped", request.msg_id)
            return None
        LOG.info("forwarding answer for %s to %s", request.msg_id, request.from_name)
        self._try_send(request.reply_sock, text)
        return None

    def on_relay(self, frame: dict) -> dict:
        to, text = frame.get("to"), frame.get("text")
        if not isinstance(to, str) or not to or not isinstance(text, str) or not text:
            return {"ok": False, "error": "relay needs non-empty string fields 'to' and 'text'"}
        try:
            peer = registry.resolve(to)
        except LookupError as exc:
            return {"ok": False, "error": str(exc)}
        if peer.sock == self.sock_path:
            return {"ok": False, "error": f"{peer.name} is this bridge; refusing to message itself"}
        try:
            self._send(peer.sock, text)
        except (LookupError, OSError) as exc:
            return {"ok": False, "error": f"send to {peer.name} failed: {exc}"}
        LOG.info("relayed %d chars to %s", len(text), peer.name)
        return {"ok": True, "to": peer.name}

    def on_ping(self, frame: dict) -> dict:
        with self._lock:
            pending = len(self.pending)
        return {"ok": True, "name": self.name, "thread": self.thread, "status": self.status, "pending": pending}

    def on_shutdown(self, frame: dict) -> dict:
        LOG.info("shutdown requested")
        self.stop()
        return {"ok": True}

    # ----- helpers -----

    def _queue_into_codex(self, text: str) -> str | None:
        """Run `codex queue`; return None on success or a one-line error description."""
        argv = [self.codex, "queue", "--thread", self.thread, "--message", text]
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=CODEX_QUEUE_TIMEOUT_SECONDS)
        except FileNotFoundError:
            return f"codex binary not found: {self.codex}"
        except subprocess.TimeoutExpired:
            return f"codex queue timed out after {CODEX_QUEUE_TIMEOUT_SECONDS:.0f}s"
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            return f"codex queue exited {result.returncode}: {detail[-1] if detail else 'no output'}"
        return None

    def _send(self, peer_sock: str, text: str) -> None:
        """Deliver `text` to a Claude peer socket as a message authored by this bridge."""
        token = registry.token_for(peer_sock)
        if token is None:
            raise LookupError(f"no auth key found for peer socket {peer_sock}")
        frame = wire.user_frame(wire.envelope(text, self.address, self.name), self.address)
        wire.send_frames(peer_sock, token, [frame])

    def _try_send(self, peer_sock: str, text: str) -> None:
        # Server-thread boundary: the peer may have exited, and there is nobody left to
        # raise to, so the failure is logged with its cause instead of killing the thread.
        try:
            self._send(peer_sock, text)
        except (LookupError, OSError) as exc:
            LOG.error("send to %s failed: %s", peer_sock, exc)

    def _set_status(self, status: str) -> None:
        with self._lock:
            self.status = status
            try:
                registry.set_status(self.pid, status)
            except OSError as exc:
                LOG.error("status update to %s failed: %s", status, exc)


def _configure_logging(thread: str) -> None:
    logs_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    handler = logging.FileHandler(logs_dir() / f"{thread}.log")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger("cxpeer").addHandler(handler)
    logging.getLogger("cxpeer").setLevel(logging.INFO)


def run(thread: str, cwd: str, name: str | None = None, watch_pid: int | None = None) -> int:
    """Run a bridge in the foreground until shutdown, SIGTERM, or the watched pid exits."""
    _configure_logging(thread)
    bridge = Bridge(thread, cwd, name=name, watch_pid=watch_pid)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: bridge.stop())
    bridge.start()
    try:
        bridge.serve_forever()
    finally:
        bridge.cleanup()
    return 0


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--thread", required=True, help="Codex thread/session uuid")
    parser.add_argument("--cwd", required=True, help="Codex session working directory")
    parser.add_argument("--name", help="peer name shown to Claude (default codex-<dir>-<xx>)")
    parser.add_argument("--watch-pid", type=int, help="exit when this pid (the Codex TUI) is gone")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cxpeer bridge", description=__doc__.splitlines()[0])
    add_arguments(parser)
    args = parser.parse_args(argv)
    return run(args.thread, args.cwd, name=args.name, watch_pid=args.watch_pid)


if __name__ == "__main__":
    sys.exit(main())
