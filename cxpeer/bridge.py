"""The bridge: one process per Codex session, acting as that session's Claude Code peer.

Inbound Claude frames (`type=user`) are pushed into the Codex TUI with `codex queue`.
Codex hooks and the `cxpeer send` CLI talk to the same socket with `cxpeer.*` control
frames. Every connection must start with an auth line carrying this bridge's token.
"""

from __future__ import annotations

import argparse
import fcntl
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
from datetime import datetime, timezone
from pathlib import Path

from . import registry, wire
from .paths import bridges_dir, codex_bin, logs_dir, outbox_root, sock_dir

LOG = logging.getLogger("cxpeer.bridge")

CONNECTION_IDLE_SECONDS = 5.0
WATCH_INTERVAL_SECONDS = 4.0
PENDING_TTL_SECONDS = float(os.environ.get("CXPEER_PENDING_TTL_SECONDS") or 15 * 60)
RELAY_SEND_TIMEOUT_SECONDS = 2.0
CODEX_QUEUE_TIMEOUT_SECONDS = 30.0
UNDELIVERABLE_NOTE = "cxpeer: could not deliver your message to Codex session {name}: {error}"
REPLY_MAX_CHARS = int(os.environ.get("CXPEER_REPLY_MAX_CHARS") or 4000)
SHORT_ID = 8
EMPTY_TURN_NOTE = "(Codex ended the turn without a final message.)"
INTERRUPTED_NOTE = "(Codex's turn was interrupted before it answered.)"
UNANSWERED_NOTE = (
    "cxpeer: Codex session {name} finished turns but none was paired with your message "
    "(msg_id {msg_id}); no reply is coming for it."
)


def default_name(cwd: str, thread: str) -> str:
    # Thread ids are UUIDv7, so the leading hex is a timestamp shared by every session
    # started the same day; the trailing hex is what actually varies.
    suffix = thread.replace("-", "")[-2:] or "00"
    return f"codex-{Path(cwd).name or 'root'}-{suffix}"


def queue_text(content: str, msg_id: str, from_name: str, first: bool = True) -> str:
    """The text handed to `codex queue`: a compact header, the message, and the pairing marker.

    Claude's XML envelope is replaced by one short line; the marker carries a short id (the
    bridge pairs by prefix). The how-it-works hint goes out once per session; the skill has the rest.
    """
    text, _, envelope_name = wire.strip_envelope(content)
    name = envelope_name or from_name
    short = msg_id[:SHORT_ID]
    out = f"[peer message from {name} · id {short}]\n{text.strip()}\n[cxpeer msg_id={short}]"
    if first:
        out += (f"\nYour final message this turn is forwarded to {name} automatically; "
                f"to message a session yourself: cxpeer send --to NAME \"text\".")
    return out


def truncate_reply(text: str, limit: int = REPLY_MAX_CHARS) -> tuple[str, bool]:
    """Cap a forwarded reply; the caller stores the full text for `cxpeer read`."""
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip(), True


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


@dataclass
class IdleSubscription:
    """A Claude session that asked (SendMessage notify_when_idle) to hear when this peer goes idle."""

    orig_msg_id: str
    reply_sock: str
    from_mode: str | None


IDLE_DETAIL_MAX_CHARS = 300


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
        self.idle_subscriptions: dict[str, IdleSubscription] = {}  # keyed by reply socket, one-shot
        self.queued_count = 0
        self.chars_in = 0   # text queued into Codex
        self.chars_out = 0  # text forwarded to Claude peers
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._server: socket.socket | None = None
        self._lock_fd: int | None = None

    # ----- lifecycle -----

    @property
    def state_path(self) -> Path:
        return bridges_dir() / f"{self.thread}.json"

    @property
    def peers_path(self) -> Path:
        return bridges_dir() / f"{self.thread}.peers.json"

    @property
    def outbox(self) -> Path:
        return outbox_root() / self.thread

    @property
    def lock_path(self) -> Path:
        return bridges_dir() / f"{self.thread}.lock"

    @property
    def replies_dir(self) -> Path:
        return bridges_dir().parent / "replies" / self.thread

    def acquire_thread_lock(self) -> bool:
        """One bridge per Codex thread: hooks can race to spawn, the lock decides who stays."""
        bridges_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock_fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._lock_fd)
            self._lock_fd = None
            return False
        return True

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
        self.outbox.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._write_state()
        self._write_peers_snapshot()
        LOG.info("bridge %s up: pid=%s sock=%s thread=%s", self.name, self.pid, self.sock_path, self.thread)

    def serve_forever(self) -> None:
        assert self._server is not None, "start() before serve_forever()"
        next_watch = time.monotonic()
        while not self._stop.is_set():
            if time.monotonic() >= next_watch:
                next_watch = time.monotonic() + WATCH_INTERVAL_SECONDS
                if self.watch_pid is not None and not pid_alive(self.watch_pid):
                    LOG.info("watched pid %s is gone; shutting down", self.watch_pid)
                    break
                self._expire_pending()
                threading.Thread(target=self._write_peers_snapshot, daemon=True).start()
            self._drain_outbox()
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
        self._fire_idle_notices("exited", None)
        for step, action in (
            ("deregister", lambda: registry.deregister(self.pid)),
            ("remove state", lambda: self.state_path.unlink(missing_ok=True)),
            ("remove peers snapshot", lambda: self.peers_path.unlink(missing_ok=True)),
            ("remove outbox", self._remove_outbox),
            ("unlink socket", lambda: Path(self.sock_path).unlink(missing_ok=True)),
        ):
            try:
                action()
            except OSError as exc:
                LOG.error("cleanup %s failed: %s", step, exc)
        if self._server is not None:
            self._server.close()
        LOG.info("bridge %s down", self.name)

    def _write_peers_snapshot(self) -> None:
        """Alive peers, for `cxpeer list` inside the Codex sandbox where `ps` is blocked."""
        peers = [
            {"name": p.name, "ref": p.ref, "pid": p.pid, "sock": p.sock, "cwd": p.cwd, "status": p.status, "kind": p.kind}
            for p in registry.list_peers() if p.alive
        ]
        try:
            _write_json_atomic(self.peers_path, {"updated": time.time(), "peers": peers})
        except OSError as exc:
            LOG.error("peers snapshot failed: %s", exc)

    def _drain_outbox(self) -> None:
        """Claim send requests dropped in the outbox by sandboxed `cxpeer send`, one thread each."""
        try:
            requests = sorted(p for p in self.outbox.glob("*.json") if not p.name.endswith(".result.json"))
        except OSError as exc:
            LOG.error("outbox unreadable: %s", exc)
            return
        for path in requests:
            claimed = path.with_name(path.name + ".claimed")
            try:
                os.rename(path, claimed)  # atomic: a request is relayed at most once
            except OSError:
                continue
            threading.Thread(target=self._relay_outbox_request, args=(claimed,), daemon=True).start()

    def _relay_outbox_request(self, claimed: Path) -> None:
        request_id = claimed.name[: -len(".json.claimed")]
        request = _read_json(claimed)
        if not isinstance(request, dict) or request.get("id") != request_id:
            LOG.warning("dropping malformed outbox request %s", request_id)
            claimed.unlink(missing_ok=True)
            return
        result = self.on_relay({"type": "cxpeer.relay", "to": request.get("to"), "text": request.get("text")})
        try:
            _write_json_atomic(claimed.with_name(f"{request_id}.result.json"), result)
        except OSError as exc:
            LOG.error("could not write result for %s: %s", request_id, exc)
        claimed.unlink(missing_ok=True)

    def _remove_outbox(self) -> None:
        for path in self.outbox.glob("*"):
            path.unlink(missing_ok=True)
        self.outbox.rmdir()

    def _expire_pending(self) -> None:
        """Tell senders whose request never paired with a turn that no reply is coming."""
        cutoff = time.time() - PENDING_TTL_SECONDS
        with self._lock:
            stale = [r for r in self.pending.values() if r.queued_at < cutoff and r.msg_id != self.active]
            for request in stale:
                del self.pending[request.msg_id]
        for request in stale:
            LOG.warning("request %s from %s expired unanswered", request.msg_id, request.from_name)
            if request.reply_sock is not None:
                self._try_send(request.reply_sock, UNANSWERED_NOTE.format(name=self.name, msg_id=request.msg_id))
        if stale:
            with self._lock:
                drained = self.status == "idle" and self.active is None and not self.pending
            if drained:
                self._fire_idle_notices("idle", None)

    def _write_state(self) -> None:
        bridges_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
        state = {
            "pid": self.pid,
            "proc_start": registry.proc_start(self.pid),
            "sock": self.sock_path,
            "token": self.token,
            "name": self.name,
            "cwd": self.cwd,
            "thread": self.thread,
            "started": int(time.time() * 1000),
            "outbox": str(self.outbox),
            "peers_file": str(self.peers_path),
        }
        _write_json_atomic(self.state_path, state)

    # ----- connections -----

    def _serve_connection(self, conn: socket.socket) -> None:
        conn.settimeout(CONNECTION_IDLE_SECONDS)
        try:
            reader = conn.makefile("r", encoding="utf-8", errors="replace")
            first = reader.readline()
            if not first.strip():
                # Claude opens and closes a connection to check the socket is alive.
                LOG.debug("probe connection closed without data")
                return
            if not self._authenticated(first):
                LOG.warning("dropped connection: bad auth line")
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
            "control": self.on_control,
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
            first = self.queued_count == 0
            self.queued_count += 1
        LOG.info("user message %s from %s (%s)", msg_id, from_name, reply_sock)

        text = queue_text(content, msg_id, from_name, first=first)
        with self._lock:
            self.chars_in += len(text)
        error = self._queue_into_codex(text)
        if error is None:
            return None
        LOG.error("codex queue failed for %s: %s", msg_id, error)
        with self._lock:
            self.pending.pop(msg_id, None)
        if reply_sock is not None:
            self._try_send(reply_sock, UNDELIVERABLE_NOTE.format(name=self.name, error=error))
        return None

    def on_control(self, frame: dict) -> None:
        if frame.get("action") != "notify_when_idle":
            LOG.info("ignoring control action %r", frame.get("action"))
            return None
        reply_sock = reply_socket(frame.get("from"))
        orig = frame.get("msg_id")
        if reply_sock is None or not isinstance(orig, str):
            LOG.warning("ignoring notify_when_idle without a usable reply address or msg_id")
            return None
        mode = frame.get("from_mode") if isinstance(frame.get("from_mode"), str) else None
        with self._lock:
            self.idle_subscriptions[reply_sock] = IdleSubscription(orig, reply_sock, mode)
            # A message that was just queued into Codex has not started its turn yet, so
            # "idle" only counts when nothing is pending either (Claude holds notices the same way).
            idle_now = self.status == "idle" and self.active is None and not self.pending
        LOG.info("idle subscription from %s (orig %s)%s", reply_sock, orig, "; already idle" if idle_now else "")
        if idle_now:
            self._fire_idle_notices("idle", None)
        return None

    def _fire_idle_notices(self, state: str, detail: str | None) -> None:
        """One-shot: tell every subscriber this peer is idle (or gone), then forget them."""
        with self._lock:
            subscribers = list(self.idle_subscriptions.values())
            self.idle_subscriptions.clear()
        for sub in subscribers:
            frame = {
                "type": "control", "action": "peer_idle_notice", "msgV": 1, "msg_id": str(uuid.uuid4()),
                "orig_msg_id": sub.orig_msg_id, "state": state,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "from": self.address, "from_mode": "prompting",
            }
            if detail:
                frame["detail"] = " ".join(detail.split())[:IDLE_DETAIL_MAX_CHARS]
            token = registry.token_for(sub.reply_sock)
            if token is None:
                LOG.warning("idle notice for %s dropped: no auth key", sub.reply_sock)
                continue
            try:
                wire.send_frames(sub.reply_sock, token, [frame])
                LOG.info("idle notice (%s) sent to %s", state, sub.reply_sock)
            except OSError as exc:
                LOG.error("idle notice to %s failed: %s", sub.reply_sock, exc)

    def on_turn_started(self, frame: dict) -> None:
        self._set_status("busy")
        marker = frame.get("msg_id")
        with self._lock:
            self.active = self._pending_id_for(marker)
        LOG.info("turn started (active request: %s)", self.active)
        return None

    def _pending_id_for(self, marker: object) -> str | None:
        """The pending msg_id a hook marker refers to; markers carry the short id (prefix)."""
        if not isinstance(marker, str) or not marker:
            return None
        if marker in self.pending:
            return marker
        matches = [k for k in self.pending if k.startswith(marker)]
        return matches[0] if len(matches) == 1 else None

    def on_turn_ended(self, frame: dict) -> None:
        self._set_status("idle")
        with self._lock:
            request = self.pending.pop(self.active, None) if self.active else None
            self.active = None
        answer = frame.get("last_assistant_message")
        answer_text = answer if isinstance(answer, str) else ""
        with self._lock:
            still_pending = len(self.pending)
        # a forwarded answer needs no repeat in the idle notice; a human turn's text is the detail
        detail = f"answered {request.from_name} ({len(answer_text)} chars)" if request else (answer_text or None)
        if still_pending:
            LOG.info("holding idle notices: %d request(s) still queued", still_pending)
        else:
            self._fire_idle_notices("idle", detail)
        if request is None:
            LOG.info("turn ended; no peer request to answer")
            return None
        if frame.get("reason") == "interrupted":
            text = INTERRUPTED_NOTE
        elif answer_text.strip():
            text, cut = truncate_reply(answer_text)
            if cut:
                path = self._store_reply(request.msg_id, answer_text)
                text += (f"\n… truncated: {len(text)} of {len(answer_text)} chars shown; "
                         f"full text: cxpeer read {request.msg_id[:SHORT_ID]}" + (f" ({path})" if path else ""))
        else:
            text = EMPTY_TURN_NOTE
        if request.reply_sock is None:
            LOG.warning("request %s had no reply address; answer dropped", request.msg_id)
            return None
        LOG.info("forwarding answer for %s to %s (%d chars)", request.msg_id, request.from_name, len(text))
        with self._lock:
            self.chars_out += len(text)
        self._try_send(request.reply_sock, text)
        return None

    def _store_reply(self, msg_id: str, text: str) -> str | None:
        try:
            self.replies_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self.replies_dir / f"{msg_id}.md"
            path.write_text(text)
            return str(path)
        except OSError as exc:
            LOG.error("could not store the full reply for %s: %s", msg_id, exc)
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
            self._send(peer.sock, text, timeout=RELAY_SEND_TIMEOUT_SECONDS)
        except (LookupError, OSError) as exc:
            return {"ok": False, "error": f"send to {peer.name} failed: {exc}"}
        with self._lock:
            self.chars_out += len(text)
        LOG.info("relayed %d chars to %s", len(text), peer.name)
        return {"ok": True, "to": peer.name}

    def on_ping(self, frame: dict) -> dict:
        with self._lock:
            pending, subscribers = len(self.pending), len(self.idle_subscriptions)
            chars_in, chars_out = self.chars_in, self.chars_out
        return {"ok": True, "name": self.name, "thread": self.thread, "status": self.status,
                "pending": pending, "idle_subscribers": subscribers, "chars_in": chars_in, "chars_out": chars_out}

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

    def _send(self, peer_sock: str, text: str, timeout: float = 5.0) -> None:
        """Deliver `text` to a Claude peer socket as a message authored by this bridge."""
        token = registry.token_for(peer_sock)
        if token is None:
            raise LookupError(f"no auth key found for peer socket {peer_sock}")
        frame = wire.user_frame(wire.envelope(text, self.address, self.name), self.address)
        wire.send_frames(peer_sock, token, [frame], timeout=timeout)

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


def _write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


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
    if not bridge.acquire_thread_lock():
        LOG.info("another bridge already serves thread %s; exiting", thread)
        return 0
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
