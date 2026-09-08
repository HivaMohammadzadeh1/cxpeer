"""Claude Code's peer registry: write and read the <pid>.json record + key file a peer needs.

A peer is visible to Claude's ListAgents/SendMessage when three files exist under
``sessions_dir()``: the JSON record, a key file named ``<pid>.<sha256(socket path)>.key``,
and the Unix socket itself (created by the bridge, not here). Liveness is decided by
comparing the record's ``procStart`` against ``ps -o lstart=`` for the pid.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from cxpeer.paths import sessions_dirs, sock_dir

# Mirrors a real Claude record so the peer is indistinguishable; unknown fields are ignored.
CLAUDE_VERSION = "2.1.263"
PID_DOMAIN = "darwin"


@dataclass
class Peer:
    name: str
    ref: str
    pid: int
    sock: str
    cwd: str
    status: str
    kind: str
    alive: bool


def _now_ms() -> int:
    return int(time.time() * 1000)


def proc_start(pid: int) -> str | None:
    """The process start time Claude uses for liveness, or None if the pid is gone.

    Must match Claude byte for byte, so the locale and timezone are pinned exactly as
    Claude runs it: ``LC_ALL=C TZ=UTC ps -o lstart= -p <pid>``.
    """
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
        )
    except Exception:
        return None
    line = out.stdout.strip()
    return line or None


def sock_path_for(pid: int) -> str:
    return str(sock_dir() / f"{pid}.sock")


def key_name_for(pid, sock_path: str) -> str:
    """Key filename Claude looks up: the pid, then the sha256 of the literal socket path string."""
    digest = hashlib.sha256(sock_path.encode()).hexdigest()
    return f"{pid}.{digest}.key"


def ref_for(sock_path: str) -> str:
    """The 6-hex short id Claude shows for a peer, derived from its socket path."""
    return hashlib.sha256(sock_path.encode()).hexdigest()[:6]


def _atomic_write(path: Path, data: str, mode: int = 0o600) -> None:
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(data)
        os.chmod(str(tmp), mode)  # defeat umask so the mode is exactly what we asked for
        os.replace(str(tmp), str(path))
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def register(pid: int, name: str, cwd: str, sock_path: str, token: str) -> None:
    """Write the record and key file into every account's registry so each Claude Code
    account on this machine sees the peer. Records are byte-identical across accounts.
    A registry that cannot be written is logged and skipped, never fatal."""
    now = _now_ms()
    started = proc_start(pid)
    record = {
        "pid": pid,
        "sessionId": str(uuid.uuid4()),
        "cwd": cwd,
        "startedAt": now,
        "procStart": started,
        "version": CLAUDE_VERSION,
        "peerProtocol": 1,
        "peerFeatures": ["notify_idle"],  # Claude only sends idle subscriptions to peers that advertise this
        "kind": "interactive",
        "entrypoint": "cli",
        "pidDomain": PID_DOMAIN,
        "messagingSocketPath": sock_path,
        "name": name,
        "nameSource": "user",
        "nameSince": now,
        "status": "idle",
        "updatedAt": now,
        "statusUpdatedAt": now,
    }
    record_json = json.dumps(record)
    key_json = json.dumps({"peerToken": token, "procStart": started, "pidDomain": PID_DOMAIN})
    key_name = key_name_for(pid, sock_path)
    for d in sessions_dirs():
        try:
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
            _atomic_write(d / f"{pid}.json", record_json, 0o600)
            _atomic_write(d / key_name, key_json, 0o600)
        except OSError as exc:
            print(f"cxpeer: skipping unwritable registry {d}: {exc}", file=sys.stderr)


def deregister(pid: int) -> None:
    """Remove the record and key from every account's registry."""
    for d in sessions_dirs():
        try:
            (d / f"{pid}.json").unlink(missing_ok=True)
            for key in d.glob(f"{pid}.*.key"):
                key.unlink(missing_ok=True)
        except OSError as exc:
            print(f"cxpeer: could not clean registry {d}: {exc}", file=sys.stderr)


def set_status(pid: int, status: str) -> None:
    """Rewrite the record with a new status and bumped timestamps, in every registry
    where it exists (same timestamp everywhere)."""
    now = _now_ms()
    for d in sessions_dirs():
        path = d / f"{pid}.json"
        if not path.exists():
            continue
        record = _read_json(path)
        if record is None:
            continue
        record["status"] = status
        record["updatedAt"] = now
        record["statusUpdatedAt"] = now
        try:
            _atomic_write(path, json.dumps(record), 0o600)
        except OSError as exc:
            print(f"cxpeer: could not update status in {d}: {exc}", file=sys.stderr)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def list_peers() -> list[Peer]:
    """Every registry record across all accounts as a Peer, deduplicated by socket path
    (the first account wins). ``alive`` is True only when the pid's current procStart
    matches the record; a gone pid (procStart None) is always dead."""
    peers: list[Peer] = []
    seen_socks: set[str] = set()
    for d in sessions_dirs():
        for path in sorted(d.glob("*.json")):
            rec = _read_json(path)
            if not rec:
                continue
            sock = rec.get("messagingSocketPath")
            pid = rec.get("pid")
            if not sock or pid is None or sock in seen_socks:
                continue
            seen_socks.add(sock)
            current = proc_start(pid)
            alive = current is not None and current == rec.get("procStart")
            peers.append(
                Peer(
                    name=rec.get("name") or f"pid-{pid}",
                    ref=ref_for(sock),
                    pid=pid,
                    sock=sock,
                    cwd=rec.get("cwd") or "",
                    status=rec.get("status") or "idle",
                    kind=rec.get("kind") or "interactive",
                    alive=alive,
                )
            )
    return peers


def token_for(sock_path: str) -> str | None:
    """The peerToken for a peer's socket, read from its key file in any account's registry."""
    name = os.path.basename(sock_path)
    if not name.endswith(".sock"):
        return None
    pid = name[: -len(".sock")]
    key_name = key_name_for(pid, sock_path)
    for d in sessions_dirs():
        key = _read_json(d / key_name)
        if key and key.get("peerToken"):
            return key.get("peerToken")
    return None


def resolve(name_or_ref: str) -> Peer:
    """Find a live peer by exact name, then by ref. Raise LookupError if none match."""
    alive = [p for p in list_peers() if p.alive]
    for p in alive:
        if p.name == name_or_ref:
            return p
    for p in alive:
        if p.ref == name_or_ref:
            return p
    raise LookupError(f"no live peer named {name_or_ref!r}")
