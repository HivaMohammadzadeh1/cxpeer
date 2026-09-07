"""Black-box tests for the bridge process: raw sockets in, files and fake codex calls out."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import read_codex_calls

REPO = Path(__file__).resolve().parents[1]
THREAD = "01a07d69-6331-76e1-9603-3246a26168ce"


def proc_start(pid: int) -> str:
    out = subprocess.run(
        ["sh", "-c", f"LC_ALL=C TZ=UTC ps -o lstart= -p {pid}"], capture_output=True, text=True
    ).stdout
    return out.strip()


class FakeClaude:
    """A Claude-shaped peer: registry record + key file + a socket that records what it receives."""

    def __init__(self, sessions: Path, socks: Path, name: str, pid: int | None = None) -> None:
        self.pid = pid or os.getpid()
        self.name = name
        self.sock_path = str(socks / f"{self.pid}.sock")
        self.token = secrets.token_hex(16)
        self.frames: list[dict] = []
        self._got = threading.Condition()
        started = proc_start(self.pid)
        record = {
            "pid": self.pid, "sessionId": "11111111-2222-4333-8444-555555555555", "cwd": "/tmp",
            "startedAt": 1, "procStart": started, "version": "2.1.263", "peerProtocol": 1,
            "peerFeatures": [], "kind": "interactive", "entrypoint": "cli", "pidDomain": "darwin",
            "messagingSocketPath": self.sock_path, "name": name, "nameSource": "user",
            "nameSince": 1, "status": "idle", "updatedAt": 1, "statusUpdatedAt": 1,
        }
        (sessions / f"{self.pid}.json").write_text(json.dumps(record))
        digest = hashlib.sha256(self.sock_path.encode()).hexdigest()
        key = sessions / f"{self.pid}.{digest}.key"
        key.write_text(json.dumps({"peerToken": self.token, "procStart": started, "pidDomain": "darwin"}))
        key.chmod(0o600)
        Path(self.sock_path).unlink(missing_ok=True)
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.sock_path)
        self._server.listen(8)
        self._server.settimeout(0.2)
        self._stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while not self._stop:
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:  # server socket closed by close()
                return
            try:
                with conn, conn.makefile("r") as reader:
                    conn.settimeout(3)
                    lines = [json.loads(l) for l in reader if l.strip()]
            except (OSError, ValueError):
                continue
            with self._got:
                self.frames.extend(lines)
                self._got.notify_all()

    def wait_for_frames(self, count: int, timeout: float = 15.0) -> list[dict]:
        with self._got:
            self._got.wait_for(lambda: len(self.frames) >= count, timeout=timeout)
            return list(self.frames)

    def close(self) -> None:
        self._stop = True
        self._server.close()
        Path(self.sock_path).unlink(missing_ok=True)


def start_bridge(extra: list[str] | None = None) -> subprocess.Popen:
    argv = [sys.executable, "-m", "cxpeer.bridge", "--thread", THREAD, "--cwd", "/tmp/proj", *(extra or [])]
    return subprocess.Popen(argv, cwd=REPO, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def wait_for_state(home: Path, timeout: float = 10.0) -> dict:
    path = home / "bridges" / f"{THREAD}.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text())
        time.sleep(0.05)
    raise AssertionError(f"bridge state file never appeared at {path}")


def talk(sock_path: str, token: str, frames: list[dict], expect_reply: bool) -> dict | None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(5)
        conn.connect(sock_path)
        payload = [{"type": "auth", "token": token}, *frames]
        conn.sendall("".join(json.dumps(f) + "\n" for f in payload).encode())
        if not expect_reply:
            return None
        with conn.makefile("r") as reader:
            line = reader.readline()
    return json.loads(line) if line.strip() else None


def wait_pending(state: dict, count: int, timeout: float = 10.0) -> None:
    """Block until the bridge reports `count` pending requests; hook frames come seconds later in real use."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if talk(state["sock"], state["token"], [{"type": "cxpeer.ping"}], expect_reply=True)["pending"] == count:
            return
        time.sleep(0.05)
    raise AssertionError(f"bridge never reached pending={count}")


def wait_ping_field(state: dict, field: str, value, timeout: float = 10.0) -> None:
    """Block until the bridge's ping reports field == value (frames arrive on separate connections)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if talk(state["sock"], state["token"], [{"type": "cxpeer.ping"}], expect_reply=True).get(field) == value:
            return
        time.sleep(0.05)
    raise AssertionError(f"bridge never reported {field}={value}")


def wait_status(state: dict, status: str, timeout: float = 10.0) -> None:
    """Block until the bridge reports `status`; the Stop hook fires seconds after UserPromptSubmit in real use."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if talk(state["sock"], state["token"], [{"type": "cxpeer.ping"}], expect_reply=True)["status"] == status:
            return
        time.sleep(0.05)
    raise AssertionError(f"bridge never reported status={status}")


def claude_user_frame(sender: FakeClaude, text: str, msg_id: str = "m-1") -> dict:
    content = f'<cross-session-message from="uds:{sender.sock_path}" from-name="{sender.name}" from-mode="prompting">\n{text}\n</cross-session-message>'
    return {"msgV": 1, "msg_id": msg_id, "type": "user", "message": {"role": "user", "content": content},
            "priority": "next", "from": f"uds:{sender.sock_path}"}


@pytest.fixture
def bridge(isolated_env, fake_codex):
    proc = start_bridge()
    state = wait_for_state(isolated_env["home"])
    yield proc, state
    if proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture
def claude(isolated_env):
    peer = FakeClaude(isolated_env["sessions"], isolated_env["socks"], "claude-fake")
    yield peer
    peer.close()


def test_bridge_registers_as_a_peer_and_answers_ping(bridge, isolated_env):
    proc, state = bridge
    assert state["name"] == "codex-proj-ce"
    assert state["thread"] == THREAD
    sessions = isolated_env["sessions"]
    record = json.loads((sessions / f"{state['pid']}.json").read_text())
    assert record["messagingSocketPath"] == state["sock"]
    assert record["status"] == "idle" and record["peerProtocol"] == 1
    keys = list(sessions.glob(f"{state['pid']}.*.key"))
    assert len(keys) == 1 and oct(keys[0].stat().st_mode & 0o777) == "0o600"
    assert json.loads(keys[0].read_text())["peerToken"] == state["token"]

    reply = talk(state["sock"], state["token"], [{"type": "cxpeer.ping"}], expect_reply=True)
    assert reply == {"ok": True, "name": "codex-proj-ce", "thread": THREAD, "status": "idle", "pending": 0, "idle_subscribers": 0}


def test_wrong_token_is_dropped_but_bridge_stays_up(bridge):
    _, state = bridge
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(2)
        conn.connect(state["sock"])
        conn.sendall(b'{"type":"auth","token":"nope"}\n{"type":"cxpeer.ping"}\n')
        assert conn.recv(1024) == b""
    assert talk(state["sock"], state["token"], [{"type": "cxpeer.ping"}], expect_reply=True)["ok"] is True


def test_user_frame_is_queued_into_codex_with_reply_trailer(bridge, claude, fake_codex):
    _, state = bridge
    talk(state["sock"], state["token"], [claude_user_frame(claude, "Say PONG.")], expect_reply=False)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not read_codex_calls(fake_codex):
        time.sleep(0.05)
    calls = read_codex_calls(fake_codex)
    assert len(calls) == 1
    argv = calls[0]
    assert argv[:4] == ["queue", "--thread", THREAD, "--message"]
    text = argv[4]
    assert 'from-name="claude-fake"' in text and "Say PONG." in text
    assert "[cxpeer msg_id=m-1]" in text and "forwarded to claude-fake automatically" in text
    assert talk(state["sock"], state["token"], [{"type": "cxpeer.ping"}], expect_reply=True)["pending"] == 1


def test_turn_ended_forwards_answer_to_the_requesting_peer(bridge, claude, isolated_env):
    _, state = bridge
    talk(state["sock"], state["token"], [claude_user_frame(claude, "Say PONG.", "m-7")], expect_reply=False)
    wait_pending(state, 1)
    talk(state["sock"], state["token"], [{"type": "cxpeer.turn_started", "msg_id": "m-7"}], expect_reply=False)
    record = isolated_env["sessions"] / f"{state['pid']}.json"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and json.loads(record.read_text())["status"] != "busy":
        time.sleep(0.05)
    assert json.loads(record.read_text())["status"] == "busy"

    talk(state["sock"], state["token"], [{"type": "cxpeer.turn_ended", "last_assistant_message": "PONG"}], expect_reply=False)
    frames = claude.wait_for_frames(2)
    assert frames[0] == {"type": "auth", "token": claude.token}
    answer = frames[1]
    assert answer["type"] == "user" and answer["from"] == f"uds:{state['sock']}"
    content = answer["message"]["content"]
    assert content.startswith(f'<cross-session-message from="uds:{state["sock"]}" from-name="codex-proj-ce"')
    assert "\nPONG\n" in content
    assert talk(state["sock"], state["token"], [{"type": "cxpeer.ping"}], expect_reply=True) == {
        "ok": True, "name": "codex-proj-ce", "thread": THREAD, "status": "idle", "pending": 0, "idle_subscribers": 0}


def test_human_turn_does_not_answer_a_pending_peer_request(bridge, claude):
    _, state = bridge
    talk(state["sock"], state["token"], [claude_user_frame(claude, "Say PONG.", "m-9")], expect_reply=False)
    wait_pending(state, 1)
    talk(state["sock"], state["token"], [{"type": "cxpeer.turn_started", "msg_id": None}], expect_reply=False)
    wait_status(state, "busy")
    talk(state["sock"], state["token"], [{"type": "cxpeer.turn_ended", "last_assistant_message": "hi human"}], expect_reply=False)
    assert claude.wait_for_frames(1, timeout=1.5) == []
    assert talk(state["sock"], state["token"], [{"type": "cxpeer.ping"}], expect_reply=True)["pending"] == 1


def test_relay_delivers_an_envelope_to_a_named_peer(bridge, claude):
    _, state = bridge
    reply = talk(state["sock"], state["token"], [{"type": "cxpeer.relay", "to": "claude-fake", "text": "hello from codex"}], expect_reply=True)
    assert reply == {"ok": True, "to": "claude-fake"}
    frames = claude.wait_for_frames(2)
    assert frames[0]["token"] == claude.token
    assert "\nhello from codex\n" in frames[1]["message"]["content"]
    assert 'from-name="codex-proj-ce"' in frames[1]["message"]["content"]


def test_relay_to_unknown_peer_reports_error(bridge):
    _, state = bridge
    reply = talk(state["sock"], state["token"], [{"type": "cxpeer.relay", "to": "nobody", "text": "x"}], expect_reply=True)
    assert reply["ok"] is False and "nobody" in reply["error"]


def test_codex_queue_failure_is_reported_back_to_sender(isolated_env, claude, monkeypatch):
    failing = isolated_env["home"] / "failing-codex"
    failing.write_text("#!/bin/sh\necho 'No active session found' >&2\nexit 1\n")
    failing.chmod(0o700)
    monkeypatch.setenv("CXPEER_CODEX_BIN", str(failing))
    proc = start_bridge()
    try:
        state = wait_for_state(isolated_env["home"])
        talk(state["sock"], state["token"], [claude_user_frame(claude, "Say PONG.")], expect_reply=False)
        frames = claude.wait_for_frames(2)
        content = frames[1]["message"]["content"]
        assert "could not deliver your message to Codex session codex-proj-ce" in content
        assert "No active session found" in content
        assert talk(state["sock"], state["token"], [{"type": "cxpeer.ping"}], expect_reply=True)["pending"] == 0
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_shutdown_frame_deregisters_and_exits(bridge, isolated_env):
    proc, state = bridge
    assert talk(state["sock"], state["token"], [{"type": "cxpeer.shutdown"}], expect_reply=True) == {"ok": True}
    assert proc.wait(timeout=5) == 0
    assert not (isolated_env["sessions"] / f"{state['pid']}.json").exists()
    assert not list(isolated_env["sessions"].glob(f"{state['pid']}.*.key"))
    assert not Path(state["sock"]).exists()
    assert not (isolated_env["home"] / "bridges" / f"{THREAD}.json").exists()


def test_bridge_exits_when_watched_pid_dies(isolated_env, fake_codex):
    watched = subprocess.Popen(["sleep", "60"])
    proc = start_bridge(["--watch-pid", str(watched.pid)])
    try:
        state = wait_for_state(isolated_env["home"])
        watched.kill()
        watched.wait()
        assert proc.wait(timeout=12) == 0
        assert not (isolated_env["sessions"] / f"{state['pid']}.json").exists()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_stale_pending_request_expires_with_a_note(isolated_env, claude, fake_codex, monkeypatch):
    monkeypatch.setenv("CXPEER_PENDING_TTL_SECONDS", "1")
    proc = start_bridge()
    try:
        state = wait_for_state(isolated_env["home"])
        assert state["proc_start"]
        talk(state["sock"], state["token"], [claude_user_frame(claude, "Say PONG.", "m-old")], expect_reply=False)
        frames = claude.wait_for_frames(2, timeout=12)
        content = frames[1]["message"]["content"]
        assert "none was paired with your message (msg_id m-old)" in content
        assert talk(state["sock"], state["token"], [{"type": "cxpeer.ping"}], expect_reply=True)["pending"] == 0
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_outbox_request_is_relayed_and_answered_with_a_result_file(bridge, claude, isolated_env):
    _, state = bridge
    outbox = Path(state["outbox"])
    assert outbox.is_dir() and outbox == isolated_env["home"] / "outbox" / THREAD
    request = outbox / "req-1.json"
    tmp = request.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"id": "req-1", "to": "claude-fake", "text": "from the sandbox"}))
    os.replace(tmp, request)
    frames = claude.wait_for_frames(2)
    assert "\nfrom the sandbox\n" in frames[1]["message"]["content"]
    result = outbox / "req-1.result.json"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not result.exists():
        time.sleep(0.05)
    assert json.loads(result.read_text()) == {"ok": True, "to": "claude-fake"}
    assert not request.exists()


def test_outbox_request_to_unknown_peer_gets_error_result(bridge):
    _, state = bridge
    outbox = Path(state["outbox"])
    (outbox / "req-2.json").write_text(json.dumps({"id": "req-2", "to": "nobody", "text": "x"}))
    result = outbox / "req-2.result.json"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not result.exists():
        time.sleep(0.05)
    data = json.loads(result.read_text())
    assert data["ok"] is False and "nobody" in data["error"]


def test_peers_snapshot_lists_alive_peers_and_is_removed_on_shutdown(bridge, claude, isolated_env):
    proc, state = bridge
    peers_file = Path(state["peers_file"])
    snapshot = json.loads(peers_file.read_text())
    assert time.time() - snapshot["updated"] < 10
    names = {p["name"] for p in snapshot["peers"]}
    assert "codex-proj-ce" in names
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and "claude-fake" not in names:
        time.sleep(0.2)
        names = {p["name"] for p in json.loads(peers_file.read_text())["peers"]}
    assert "claude-fake" in names
    talk(state["sock"], state["token"], [{"type": "cxpeer.shutdown"}], expect_reply=True)
    proc.wait(timeout=5)
    assert not peers_file.exists()
    assert not Path(state["outbox"]).exists()


def test_interrupted_turn_tells_the_requester(bridge, claude):
    _, state = bridge
    talk(state["sock"], state["token"], [claude_user_frame(claude, "Say PONG.", "m-int")], expect_reply=False)
    wait_pending(state, 1)
    talk(state["sock"], state["token"], [{"type": "cxpeer.turn_started", "msg_id": "m-int"}], expect_reply=False)
    wait_status(state, "busy")
    talk(state["sock"], state["token"], [{"type": "cxpeer.turn_ended", "last_assistant_message": None, "reason": "interrupted"}], expect_reply=False)
    frames = claude.wait_for_frames(2)
    assert "interrupted before it answered" in frames[1]["message"]["content"]
    assert talk(state["sock"], state["token"], [{"type": "cxpeer.ping"}], expect_reply=True)["pending"] == 0


def subscribe_frame(sender: FakeClaude, msg_id: str = "sub-1") -> dict:
    return {"type": "control", "action": "notify_when_idle", "from": f"uds:{sender.sock_path}",
            "from_mode": "prompting", "msgV": 1, "msg_id": msg_id}


def test_idle_subscription_fires_after_the_turn_with_the_answer_as_detail(bridge, claude):
    _, state = bridge
    talk(state["sock"], state["token"], [claude_user_frame(claude, "Say PONG.", "m-s")], expect_reply=False)
    wait_pending(state, 1)
    talk(state["sock"], state["token"], [{"type": "cxpeer.turn_started", "msg_id": "m-s"}], expect_reply=False)
    wait_status(state, "busy")
    talk(state["sock"], state["token"], [subscribe_frame(claude, "sub-7")], expect_reply=False)
    wait_ping_field(state, "idle_subscribers", 1)
    assert claude.wait_for_frames(1, timeout=1.0) == []  # busy: nothing fires yet
    talk(state["sock"], state["token"], [{"type": "cxpeer.turn_ended", "last_assistant_message": "PONG  \n done"}], expect_reply=False)
    frames = claude.wait_for_frames(4)  # auth+answer, auth+notice (order between the two sends may vary)
    notices = [f for f in frames if f.get("action") == "peer_idle_notice"]
    assert len(notices) == 1
    n = notices[0]
    assert n["type"] == "control" and n["orig_msg_id"] == "sub-7" and n["state"] == "idle"
    assert n["from"] == f"uds:{state['sock']}" and n["detail"] == "PONG done"
    assert n["finished_at"].endswith("+00:00")
    assert talk(state["sock"], state["token"], [{"type": "cxpeer.ping"}], expect_reply=True)["idle_subscribers"] == 0


def test_idle_subscription_while_idle_fires_immediately(bridge, claude):
    _, state = bridge
    talk(state["sock"], state["token"], [subscribe_frame(claude, "sub-now")], expect_reply=False)
    frames = claude.wait_for_frames(2)
    assert frames[1]["action"] == "peer_idle_notice" and frames[1]["orig_msg_id"] == "sub-now"
    assert "detail" not in frames[1]


def test_shutdown_tells_subscribers_the_peer_exited(bridge, claude):
    proc, state = bridge
    talk(state["sock"], state["token"], [{"type": "cxpeer.turn_started", "msg_id": None}], expect_reply=False)
    wait_status(state, "busy")
    talk(state["sock"], state["token"], [subscribe_frame(claude, "sub-x")], expect_reply=False)
    talk(state["sock"], state["token"], [{"type": "cxpeer.shutdown"}], expect_reply=True)
    proc.wait(timeout=5)
    frames = claude.wait_for_frames(2)
    assert frames[1]["action"] == "peer_idle_notice" and frames[1]["state"] == "exited" and frames[1]["orig_msg_id"] == "sub-x"


def test_idle_subscription_waits_for_a_queued_message_to_be_answered(bridge, claude):
    _, state = bridge
    talk(state["sock"], state["token"], [claude_user_frame(claude, "Say PONG.", "m-q")], expect_reply=False)
    wait_pending(state, 1)
    talk(state["sock"], state["token"], [subscribe_frame(claude, "sub-q")], expect_reply=False)
    assert claude.wait_for_frames(1, timeout=1.0) == []  # queued but not started: no notice yet
    talk(state["sock"], state["token"], [{"type": "cxpeer.turn_started", "msg_id": "m-q"}], expect_reply=False)
    wait_status(state, "busy")
    talk(state["sock"], state["token"], [{"type": "cxpeer.turn_ended", "last_assistant_message": "PONG"}], expect_reply=False)
    frames = claude.wait_for_frames(4)
    notices = [f for f in frames if f.get("action") == "peer_idle_notice"]
    assert len(notices) == 1 and notices[0]["orig_msg_id"] == "sub-q" and notices[0]["detail"] == "PONG"


def test_second_bridge_for_the_same_thread_exits_and_leaves_the_first(bridge, isolated_env):
    proc, state = bridge
    second = start_bridge()
    assert second.wait(timeout=10) == 0
    assert proc.poll() is None
    assert json.loads((isolated_env["home"] / "bridges" / f"{THREAD}.json").read_text())["pid"] == state["pid"]
    assert talk(state["sock"], state["token"], [{"type": "cxpeer.ping"}], expect_reply=True)["ok"] is True


def test_idle_notice_waits_until_every_queued_request_is_answered(bridge, claude):
    _, state = bridge
    talk(state["sock"], state["token"], [claude_user_frame(claude, "first", "m-a")], expect_reply=False)
    wait_pending(state, 1)
    talk(state["sock"], state["token"], [claude_user_frame(claude, "second", "m-b")], expect_reply=False)
    wait_pending(state, 2)
    talk(state["sock"], state["token"], [subscribe_frame(claude, "sub-2")], expect_reply=False)
    talk(state["sock"], state["token"], [{"type": "cxpeer.turn_started", "msg_id": "m-a"}], expect_reply=False)
    wait_status(state, "busy")
    talk(state["sock"], state["token"], [{"type": "cxpeer.turn_ended", "last_assistant_message": "A"}], expect_reply=False)
    frames = claude.wait_for_frames(2)
    assert [f.get("action") for f in frames if f.get("type") == "control"] == []  # answer A only, no notice yet
    talk(state["sock"], state["token"], [{"type": "cxpeer.turn_started", "msg_id": "m-b"}], expect_reply=False)
    wait_status(state, "busy")
    talk(state["sock"], state["token"], [{"type": "cxpeer.turn_ended", "last_assistant_message": "B"}], expect_reply=False)
    frames = claude.wait_for_frames(6)
    notices = [f for f in frames if f.get("action") == "peer_idle_notice"]
    assert len(notices) == 1 and notices[0]["detail"] == "B"
