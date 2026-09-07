"""Claude Code peer wire protocol: newline-delimited JSON over a Unix socket.

First line on every connection is an auth frame carrying the receiver's token.
Message content is wrapped in a <cross-session-message> envelope that receivers
render verbatim, so senders build that envelope themselves.
"""

from __future__ import annotations

import json
import re
import socket
import time
import uuid

FROM_MODE = "prompting"

_ENVELOPE_RE = re.compile(
    r"^<cross-session-message\s+([^>]*)>\n(.*)\n</cross-session-message>\s*$",
    re.DOTALL,
)
_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')


def envelope(text: str, from_addr: str, from_name: str) -> str:
    """Wrap text the way Claude Code does before delivering a cross-session message."""
    return (
        f'<cross-session-message from="{from_addr}" from-name="{from_name}" '
        f'from-mode="{FROM_MODE}">\n{text}\n</cross-session-message>'
    )


def strip_envelope(content: str) -> tuple[str, str | None, str | None]:
    """Return (text, from_addr, from_name) if content is enveloped, else (content, None, None)."""
    m = _ENVELOPE_RE.match(content)
    if not m:
        return content, None, None
    attrs = dict(_ATTR_RE.findall(m.group(1)))
    return m.group(2), attrs.get("from"), attrs.get("from-name")


def user_frame(content: str, from_addr: str, msg_id: str | None = None) -> dict:
    """Outbound user message frame (peerProtocol 1). `from_addr` is the reply address."""
    return {
        "msgV": 1,
        "msg_id": msg_id or str(uuid.uuid4()),
        "type": "user",
        "message": {"role": "user", "content": content},
        "priority": "next",
        "from": from_addr,
    }


def send_frames(sock_path: str, token: str, frames: list[dict], timeout: float = 5.0) -> None:
    """Connect to a peer socket, write the auth line then each frame, and close."""
    payload = "".join(json.dumps(f) + "\n" for f in [{"type": "auth", "token": token}, *frames])
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(sock_path)
        s.sendall(payload.encode("utf-8"))


def read_lines(conn: socket.socket, timeout: float = 5.0) -> list[dict]:
    """Read newline-delimited JSON until EOF or the deadline; skip lines that do not decode."""
    deadline = time.monotonic() + timeout
    buf = b""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        conn.settimeout(remaining)
        try:
            data = conn.recv(65536)
        except (socket.timeout, TimeoutError):
            break
        if not data:
            break
        buf += data
    out = []
    for line in buf.decode("utf-8", errors="replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
