"""Talk to a bridge (or, failing that, a peer directly) from the Codex shell side.

`cxpeer send` finds the bridge for this Codex session and asks it to relay a message.
When there is no bridge, `send_direct` delivers straight to the peer, but warns that
replies cannot be routed back (there is no socket for the peer to answer to).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from cxpeer import registry, wire
from cxpeer.paths import bridges_dir

# Inside the Codex sandbox `ps` cannot run and sockets cannot be connected, so the
# client falls back to files: it reads the bridge's peers snapshot instead of `ps`,
# and relays a send by dropping a request file the bridge polls for.
SNAPSHOT_MAX_AGE_SECONDS = 15.0
OUTBOX_RESULT_TIMEOUT_SECONDS = 6.0
OUTBOX_POLL_SECONDS = 0.1


@dataclass
class BridgeInfo:
    pid: int
    sock: str
    token: str
    name: str
    cwd: str
    thread: str
    started: int = 0
    outbox: str = ""       # file drop for sandboxed sends; "" for pre-sandbox bridges
    peers_file: str = ""   # peers snapshot for sandboxed `list`; "" for pre-sandbox bridges


def _load_bridges() -> list[BridgeInfo]:
    d = bridges_dir()
    if not d.exists():
        return []
    out: list[BridgeInfo] = []
    for path in d.glob("*.json"):
        try:
            data = json.loads(path.read_text())
            out.append(
                BridgeInfo(
                    pid=data["pid"],
                    sock=data["sock"],
                    token=data["token"],
                    name=data["name"],
                    cwd=data["cwd"],
                    thread=data["thread"],
                    started=data.get("started", 0),
                    outbox=data.get("outbox", ""),
                    peers_file=data.get("peers_file", ""),
                )
            )
        except (ValueError, KeyError, OSError):
            continue
    return out


def _is_ancestor(ancestor: str, path: str) -> bool:
    a = os.path.abspath(ancestor)
    p = os.path.abspath(path)
    return p == a or p.startswith(a.rstrip("/") + "/")


def sandboxed() -> bool:
    """True inside Codex's sandbox, where `ps` cannot run and sockets cannot be connected.
    Detected by the CODEX_SANDBOX marker, or by `ps` being unable to read our own start time."""
    if os.environ.get("CODEX_SANDBOX"):
        return True
    return registry.proc_start(os.getpid()) is None


def _read_snapshot(bridge: BridgeInfo) -> dict | None:
    if not bridge.peers_file:
        return None
    try:
        snap = json.loads(Path(bridge.peers_file).read_text())
    except (OSError, ValueError):
        return None
    return snap if isinstance(snap, dict) else None


def _snapshot_fresh(bridge: BridgeInfo, max_age: float = SNAPSHOT_MAX_AGE_SECONDS) -> bool:
    """A bridge is alive, sandbox-side, if its peers snapshot was updated recently."""
    snap = _read_snapshot(bridge)
    if snap is None:
        return False
    updated = snap.get("updated")
    return isinstance(updated, (int, float)) and (time.time() - updated) <= max_age


def list_peers_snapshot(bridge: BridgeInfo, max_age: float = SNAPSHOT_MAX_AGE_SECONDS) -> list[dict] | None:
    """Alive peers from a bridge's file snapshot, or None if there is no fresh snapshot."""
    snap = _read_snapshot(bridge)
    if snap is None:
        return None
    updated = snap.get("updated")
    if not isinstance(updated, (int, float)) or (time.time() - updated) > max_age:
        return None
    peers = snap.get("peers")
    return peers if isinstance(peers, list) else None


def find_bridge(cwd: str | None = None, thread: str | None = None) -> BridgeInfo | None:
    """Pick the bridge to route through: by thread if given, else the newest live bridge
    whose cwd contains the current directory, else the newest live bridge, else None.
    Liveness comes from `ps`, or from a fresh peers snapshot when sandboxed (no `ps`)."""
    bridges = _load_bridges()
    if thread is not None:
        for b in bridges:
            if b.thread == thread:
                return b
        return None
    here = cwd or os.getcwd()
    if sandboxed():
        alive = [b for b in bridges if _snapshot_fresh(b)]
    else:
        alive_pids = {p.pid for p in registry.list_peers() if p.alive}
        alive = [b for b in bridges if b.pid in alive_pids]
    matching = [b for b in alive if _is_ancestor(b.cwd, here)]
    if matching:
        return max(matching, key=lambda b: b.started)
    if alive:
        return max(alive, key=lambda b: b.started)
    return None


def _request(sock_path: str, token: str, frame: dict, timeout: float = 5.0) -> dict:
    """Send auth + one frame, read exactly one response line, close. Do not wait for EOF:
    the bridge holds the connection open, so reading to EOF would stall for the full timeout."""
    auth = json.dumps({"type": "auth", "token": token}) + "\n"
    body = json.dumps(frame) + "\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(sock_path)
        s.sendall((auth + body).encode("utf-8"))
        line = _read_one_line(s, timeout)
    if line is None:
        raise RuntimeError(f"no response from bridge at {sock_path}")
    return line


def _read_one_line(s: socket.socket, timeout: float) -> dict | None:
    deadline = time.monotonic() + timeout
    buf = b""
    while b"\n" not in buf:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        s.settimeout(remaining)
        try:
            data = s.recv(65536)
        except (socket.timeout, TimeoutError):
            break
        if not data:
            break
        buf += data
    first = buf.split(b"\n", 1)[0].strip()
    if not first:
        return None
    try:
        return json.loads(first.decode("utf-8", errors="replace"))
    except ValueError:
        return None


def ping(bridge: BridgeInfo) -> dict:
    """Ask a bridge for its status; raise RuntimeError if it does not answer."""
    return _request(bridge.sock, bridge.token, {"type": "cxpeer.ping"})


def list_bridges() -> list[BridgeInfo]:
    """Every bridge with a state file, newest first, whether or not it is still alive."""
    return sorted(_load_bridges(), key=lambda b: b.started, reverse=True)


def send(to: str, text: str, bridge: BridgeInfo) -> None:
    """Ask the bridge to relay `text` to peer `to`. Raise on a failed relay.

    Uses the socket normally; inside the Codex sandbox (or if a connect is refused with
    PermissionError) it falls back to the file outbox the bridge polls."""
    if sandboxed():
        _send_via_outbox(to, text, bridge)
        return
    try:
        resp = _request(bridge.sock, bridge.token, {"type": "cxpeer.relay", "to": to, "text": text})
    except PermissionError:
        _send_via_outbox(to, text, bridge)
        return
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error") or f"relay to {to!r} failed")


def _send_via_outbox(to: str, text: str, bridge: BridgeInfo, timeout: float = OUTBOX_RESULT_TIMEOUT_SECONDS) -> None:
    """Drop a request file in the bridge's outbox and poll for its result file."""
    if not bridge.outbox:
        raise RuntimeError("this bridge predates the sandbox outbox; restart the Codex session to update it")
    outbox = Path(bridge.outbox)
    req_id = str(uuid.uuid4())
    req_path = outbox / f"{req_id}.json"
    result_path = outbox / f"{req_id}.result.json"
    tmp = outbox / f"{req_id}.json.tmp"
    tmp.write_text(json.dumps({"id": req_id, "to": to, "text": text}))
    os.replace(tmp, req_path)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = json.loads(result_path.read_text())
        except FileNotFoundError:
            time.sleep(OUTBOX_POLL_SECONDS)
            continue
        except (OSError, ValueError):
            time.sleep(OUTBOX_POLL_SECONDS)  # a partial write; the bridge writes atomically, so retry
            continue
        result_path.unlink(missing_ok=True)
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or f"relay to {to!r} failed")
        return
    req_path.unlink(missing_ok=True)
    raise RuntimeError("bridge did not answer within 6 s")


def send_direct(to: str, text: str) -> None:
    """Deliver straight to a peer when no bridge exists. Replies cannot be routed back."""
    peer = registry.resolve(to)  # raises LookupError if no live peer matches
    token = registry.token_for(peer.sock)
    if token is None:
        raise LookupError(f"no key for peer {to!r}")
    from_addr = "uds:cxpeer-no-bridge"
    content = wire.envelope(text, from_addr, "cxpeer")
    wire.send_frames(peer.sock, token, [wire.user_frame(content, from_addr)])
    print(
        "warning: no bridge for this session; a reply to this message cannot be routed back.",
        file=sys.stderr,
    )
