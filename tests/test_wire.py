"""Tests for cxpeer.wire: envelope, user_frame, send_frames, read_lines, strip_envelope."""

from __future__ import annotations

import json
import socket
import threading
import uuid
from pathlib import Path

import pytest

from cxpeer import wire

ADDR = "uds:/tmp/cc-socks/81548.sock"
NAME = "unclave-8b"


def test_envelope_matches_claude_format():
    out = wire.envelope("Say LOOP-OK and nothing else.", ADDR, NAME)
    assert out == (
        '<cross-session-message from="uds:/tmp/cc-socks/81548.sock" '
        'from-name="unclave-8b" from-mode="prompting">\n'
        "Say LOOP-OK and nothing else.\n"
        "</cross-session-message>"
    )


def test_user_frame_has_protocol_fields_and_fresh_uuid4():
    frame = wire.user_frame("hello", ADDR)
    assert frame["msgV"] == 1
    assert frame["type"] == "user"
    assert frame["priority"] == "next"
    assert frame["from"] == ADDR
    assert frame["message"] == {"role": "user", "content": "hello"}
    assert uuid.UUID(frame["msg_id"]).version == 4


def test_user_frame_keeps_explicit_msg_id():
    frame = wire.user_frame("hello", ADDR, msg_id="abc-123")
    assert frame["msg_id"] == "abc-123"


def _serve_one(sock_path: Path, received: list[bytes]) -> threading.Thread:
    """Accept one connection, read to EOF, stash the raw bytes."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    srv.listen(1)
    ready = threading.Event()

    def run():
        ready.set()
        conn, _ = srv.accept()
        with conn:
            chunks = []
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                chunks.append(data)
        received.append(b"".join(chunks))
        srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    ready.wait(2)
    return t


def test_send_frames_writes_auth_line_first_then_frames(isolated_env):
    sock_path = isolated_env["socks"] / "peer.sock"
    received: list[bytes] = []
    t = _serve_one(sock_path, received)

    frame = wire.user_frame("hi there", ADDR)
    wire.send_frames(str(sock_path), "tok-secret", [frame])
    t.join(5)
    assert not t.is_alive()

    lines = received[0].decode().split("\n")
    assert lines[-1] == "", "stream must end with a newline"
    decoded = [json.loads(l) for l in lines if l]
    assert decoded[0] == {"type": "auth", "token": "tok-secret"}
    assert decoded[1]["msgV"] == 1
    assert decoded[1]["priority"] == "next"
    assert decoded[1]["message"]["content"] == "hi there"
    assert len(decoded) == 2


def test_send_frames_raises_when_socket_missing(isolated_env):
    missing = isolated_env["socks"] / "nope.sock"
    with pytest.raises(OSError):
        wire.send_frames(str(missing), "tok", [{"type": "cxpeer.ping"}])


def test_read_lines_decodes_until_eof_and_skips_bad_lines():
    a, b = socket.socketpair()
    with a, b:
        a.sendall(b'{"type":"auth","token":"x"}\nnot json\n{"ok":true}\n')
        a.close()
        got = wire.read_lines(b, timeout=2.0)
    assert got == [{"type": "auth", "token": "x"}, {"ok": True}]


def test_read_lines_returns_partial_on_timeout():
    a, b = socket.socketpair()
    with a, b:
        a.sendall(b'{"n":1}\n')
        got = wire.read_lines(b, timeout=0.2)
    assert got == [{"n": 1}]


def test_strip_envelope_round_trips():
    text = "first line\nsecond line"
    wrapped = wire.envelope(text, ADDR, NAME)
    assert wire.strip_envelope(wrapped) == (text, ADDR, NAME)


def test_strip_envelope_passes_plain_text_through():
    assert wire.strip_envelope("just text") == ("just text", None, None)
