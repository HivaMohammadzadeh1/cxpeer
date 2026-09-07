"""Client + CLI (list, send) tests. Fake bridge sockets stand in for a real bridge."""

from __future__ import annotations

import json
import os
import socket
import threading
import time

import pytest

from cxpeer import cli, client, registry
from cxpeer.registry import Peer


# --- helpers ---------------------------------------------------------------


def _write_bridge(home, thread, pid, sock, cwd, name, started, token="t" * 32,
                  outbox="", peers_file=""):
    bdir = home / "bridges"
    bdir.mkdir(exist_ok=True)
    (bdir / f"{thread}.json").write_text(
        json.dumps(
            {
                "pid": pid,
                "sock": sock,
                "token": token,
                "name": name,
                "cwd": cwd,
                "thread": thread,
                "started": started,
                "outbox": outbox,
                "peers_file": peers_file,
            }
        )
    )


def _sandbox_bridge(outbox="", peers_file=""):
    return client.BridgeInfo(
        pid=1, sock="/x.sock", token="t" * 32, name="b", cwd="/w", thread="t",
        started=1, outbox=outbox, peers_file=peers_file,
    )


class FakeOutbox:
    """Stand in for the bridge draining its outbox: answer each request atomically, then delete it."""

    def __init__(self, outbox, response):
        self.outbox = outbox
        self.response = response  # a dict, or a callable(request_dict) -> dict
        self.seen: list[dict] = []
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        while not self._stop.is_set():
            for path in sorted(self.outbox.glob("*.json")):
                if path.name.endswith(".result.json"):
                    continue
                try:
                    req = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue
                self.seen.append(req)
                resp = self.response(req) if callable(self.response) else self.response
                tmp = self.outbox / f"{req['id']}.result.json.tmp"
                tmp.write_text(json.dumps(resp))
                os.replace(tmp, self.outbox / f"{req['id']}.result.json")
                path.unlink(missing_ok=True)
            time.sleep(0.02)

    def close(self):
        self._stop.set()


def _alive(*pids):
    """A list_peers() stand-in: every listed pid is alive."""
    return [
        Peer(f"p{pid}", "r" * 6, pid, f"/s/{pid}.sock", "/", "idle", "interactive", True)
        for pid in pids
    ]


class FakeBridge:
    """A one-shot UDS server: reads auth + one request frame, replies `response`, holds open."""

    def __init__(self, sock_path, response):
        self.sock_path = sock_path
        self.response = response
        self.received: list[dict] = []
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(sock_path)
        self._srv.listen(1)
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self):
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5)
            buf = b""
            while buf.count(b"\n") < 2:
                try:
                    data = conn.recv(65536)
                except OSError:
                    break
                if not data:
                    break
                buf += data
            self.received = [json.loads(x) for x in buf.decode().split("\n") if x.strip()]
            conn.sendall((json.dumps(self.response) + "\n").encode())
            # keep the connection open briefly, as a real bridge does
            try:
                conn.recv(1)
            except OSError:
                pass

    def close(self):
        self._srv.close()


# --- find_bridge -----------------------------------------------------------


def test_find_bridge_by_thread(isolated_env, monkeypatch):
    home = isolated_env["home"]
    _write_bridge(home, "thread-a", 111, "/tmp/a.sock", "/work/a", "codex-a", 100)
    _write_bridge(home, "thread-b", 222, "/tmp/b.sock", "/work/b", "codex-b", 200)
    monkeypatch.setattr(registry, "list_peers", lambda: [])  # liveness irrelevant when thread is named
    b = client.find_bridge(thread="thread-b")
    assert b is not None and b.pid == 222 and b.thread == "thread-b"
    assert client.find_bridge(thread="nope") is None


def test_find_bridge_prefers_cwd_ancestor(isolated_env, monkeypatch):
    home = isolated_env["home"]
    _write_bridge(home, "t1", 111, "/tmp/1.sock", "/work", "codex-anc", 100)
    _write_bridge(home, "t2", 222, "/tmp/2.sock", "/elsewhere", "codex-else", 300)
    monkeypatch.setattr(registry, "list_peers", lambda: _alive(111, 222))
    b = client.find_bridge(cwd="/work/sub/dir")
    assert b.pid == 111  # cwd-ancestor match beats a newer, unrelated bridge


def test_find_bridge_newest_among_cwd_matches(isolated_env, monkeypatch):
    home = isolated_env["home"]
    _write_bridge(home, "t1", 111, "/tmp/1.sock", "/work", "codex-old", 100)
    _write_bridge(home, "t2", 222, "/tmp/2.sock", "/work", "codex-new", 400)
    monkeypatch.setattr(registry, "list_peers", lambda: _alive(111, 222))
    assert client.find_bridge(cwd="/work").pid == 222


def test_find_bridge_falls_back_to_newest_alive(isolated_env, monkeypatch):
    home = isolated_env["home"]
    _write_bridge(home, "t1", 111, "/tmp/1.sock", "/nowhere1", "codex-1", 100)
    _write_bridge(home, "t2", 222, "/tmp/2.sock", "/nowhere2", "codex-2", 500)
    monkeypatch.setattr(registry, "list_peers", lambda: _alive(111, 222))
    assert client.find_bridge(cwd="/unrelated").pid == 222


def test_find_bridge_excludes_dead(isolated_env, monkeypatch):
    home = isolated_env["home"]
    _write_bridge(home, "t1", 111, "/tmp/1.sock", "/work", "codex-dead", 100)
    monkeypatch.setattr(registry, "list_peers", lambda: [])  # nothing alive
    assert client.find_bridge(cwd="/work") is None


# --- send ------------------------------------------------------------------


def _bridge_info(sock_path, token="t" * 32):
    return client.BridgeInfo(
        pid=os.getpid(), sock=sock_path, token=token, name="b", cwd="/work", thread="t", started=1
    )


def test_send_relays_auth_then_frame(isolated_env):
    sock_path = str(isolated_env["socks"] / "bridge.sock")
    fb = FakeBridge(sock_path, {"ok": True})
    try:
        client.send("codex-target", "hello there", _bridge_info(sock_path))
        fb._t.join(2)
        assert fb.received[0] == {"type": "auth", "token": "t" * 32}
        assert fb.received[1] == {
            "type": "cxpeer.relay",
            "to": "codex-target",
            "text": "hello there",
        }
    finally:
        fb.close()


def test_send_raises_on_ok_false(isolated_env):
    sock_path = str(isolated_env["socks"] / "bridge2.sock")
    fb = FakeBridge(sock_path, {"ok": False, "error": "no live peer named 'x'"})
    try:
        with pytest.raises(RuntimeError) as ei:
            client.send("x", "hi", _bridge_info(sock_path))
        assert "no live peer named" in str(ei.value)
    finally:
        fb.close()


# --- CLI -------------------------------------------------------------------


def test_list_prints_only_alive(isolated_env, monkeypatch, capsys):
    monkeypatch.setattr(client, "sandboxed", lambda: False)
    peers = [
        Peer("codex-a", "abc123", 111, "/tmp/a.sock", "/work/a", "idle", "interactive", True),
        Peer("codex-b", "def456", 222, "/tmp/b.sock", "/work/b", "busy", "interactive", False),
    ]
    monkeypatch.setattr(registry, "list_peers", lambda: peers)
    assert cli.main(["list"]) == 0
    assert capsys.readouterr().out.splitlines() == ["codex-a [abc123]  idle  /work/a"]


def test_cli_send_via_bridge(isolated_env, monkeypatch):
    sock_path = str(isolated_env["socks"] / "b.sock")
    fb = FakeBridge(sock_path, {"ok": True})
    try:
        monkeypatch.setattr(client, "sandboxed", lambda: False)
        monkeypatch.setattr(client, "find_bridge", lambda cwd=None, thread=None: _bridge_info(sock_path))
        assert cli.main(["send", "--to", "codex-x", "hey"]) == 0
        fb._t.join(2)
        assert fb.received[1] == {"type": "cxpeer.relay", "to": "codex-x", "text": "hey"}
    finally:
        fb.close()


def test_cli_send_stdin(isolated_env, monkeypatch):
    sock_path = str(isolated_env["socks"] / "c.sock")
    fb = FakeBridge(sock_path, {"ok": True})
    try:
        monkeypatch.setattr(client, "sandboxed", lambda: False)
        monkeypatch.setattr(client, "find_bridge", lambda cwd=None, thread=None: _bridge_info(sock_path))
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("from stdin"))
        assert cli.main(["send", "--to", "codex-x", "-"]) == 0
        fb._t.join(2)
        assert fb.received[1]["text"] == "from stdin"
    finally:
        fb.close()


def test_cli_send_no_bridge_no_peer_exits_1(isolated_env, monkeypatch, capsys):
    monkeypatch.setattr(client, "sandboxed", lambda: False)
    monkeypatch.setattr(client, "find_bridge", lambda cwd=None, thread=None: None)
    monkeypatch.setattr(registry, "list_peers", lambda: [])  # send_direct.resolve finds nothing
    assert cli.main(["send", "--to", "ghost", "hello"]) == 1
    assert "no live peer" in capsys.readouterr().err


# --- sandbox: detection, snapshot liveness, file outbox --------------------


def test_sandboxed_detects_ps_failure(isolated_env, monkeypatch):
    monkeypatch.delenv("CODEX_SANDBOX", raising=False)
    monkeypatch.setattr(client.registry, "proc_start", lambda pid: None)
    assert client.sandboxed() is True
    monkeypatch.setattr(client.registry, "proc_start", lambda pid: "Sun Sep  7 12:00:00 2026")
    assert client.sandboxed() is False


def test_sandboxed_env_marker(isolated_env, monkeypatch):
    monkeypatch.setattr(client.registry, "proc_start", lambda pid: "Sun Sep  7 12:00:00 2026")
    monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
    assert client.sandboxed() is True


def test_find_bridge_sandboxed_uses_fresh_snapshot(isolated_env, tmp_path, monkeypatch):
    home = isolated_env["home"]
    (home / "bridges").mkdir(parents=True, exist_ok=True)
    peers_file = home / "bridges" / "t1.peers.json"
    peers_file.write_text(json.dumps({"updated": time.time(), "peers": []}))
    _write_bridge(home, "t1", 111, "/tmp/1.sock", "/work", "codex-1", 100,
                  outbox=str(tmp_path / "o1"), peers_file=str(peers_file))
    monkeypatch.setattr(client, "sandboxed", lambda: True)
    b = client.find_bridge(cwd="/work/sub")
    assert b is not None and b.thread == "t1"


def test_find_bridge_sandboxed_ignores_stale_snapshot(isolated_env, tmp_path, monkeypatch):
    home = isolated_env["home"]
    (home / "bridges").mkdir(parents=True, exist_ok=True)
    peers_file = home / "bridges" / "t1.peers.json"
    peers_file.write_text(json.dumps({"updated": time.time() - 60, "peers": []}))  # older than 15 s
    _write_bridge(home, "t1", 111, "/tmp/1.sock", "/work", "codex-1", 100,
                  outbox=str(tmp_path / "o1"), peers_file=str(peers_file))
    monkeypatch.setattr(client, "sandboxed", lambda: True)
    assert client.find_bridge(cwd="/work") is None


def test_list_peers_snapshot_fresh_and_stale(isolated_env, tmp_path):
    pf = tmp_path / "peers.json"
    row = {"name": "codex-a", "ref": "abc123", "pid": 1, "sock": "/s", "cwd": "/w",
           "status": "idle", "kind": "interactive"}
    pf.write_text(json.dumps({"updated": time.time(), "peers": [row]}))
    bridge = _sandbox_bridge(peers_file=str(pf))
    rows = client.list_peers_snapshot(bridge)
    assert rows and rows[0]["name"] == "codex-a"

    pf.write_text(json.dumps({"updated": time.time() - 60, "peers": [row]}))
    assert client.list_peers_snapshot(bridge) is None
    assert client.list_peers_snapshot(_sandbox_bridge()) is None  # no peers_file at all


def test_send_via_outbox_success(isolated_env, tmp_path, monkeypatch):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    monkeypatch.setattr(client, "sandboxed", lambda: True)
    fake = FakeOutbox(outbox, {"ok": True, "to": "codex-x"})
    try:
        client.send("codex-x", "hello", _sandbox_bridge(outbox=str(outbox)))  # no raise
        assert fake.seen[0]["to"] == "codex-x" and fake.seen[0]["text"] == "hello"
        assert not list(outbox.glob("*.json"))  # request and result both cleaned up
    finally:
        fake.close()


def test_send_via_outbox_raises_on_error(isolated_env, tmp_path, monkeypatch):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    monkeypatch.setattr(client, "sandboxed", lambda: True)
    fake = FakeOutbox(outbox, {"ok": False, "error": "no live peer named 'zzz'"})
    try:
        with pytest.raises(RuntimeError) as ei:
            client.send("zzz", "hi", _sandbox_bridge(outbox=str(outbox)))
        assert "no live peer named" in str(ei.value)
    finally:
        fake.close()


def test_send_via_outbox_times_out(isolated_env, tmp_path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    with pytest.raises(RuntimeError) as ei:
        client._send_via_outbox("x", "hi", _sandbox_bridge(outbox=str(outbox)), timeout=0.3)
    assert "did not answer" in str(ei.value)
    assert not list(outbox.glob("*.json"))  # our request is cleaned up on timeout


def test_send_falls_back_to_outbox_on_permission_error(isolated_env, tmp_path, monkeypatch):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    monkeypatch.setattr(client, "sandboxed", lambda: False)  # not detected, but the socket is refused

    def refuse(*a, **k):
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(client, "_request", refuse)
    fake = FakeOutbox(outbox, {"ok": True, "to": "codex-x"})
    try:
        client.send("codex-x", "hey", _sandbox_bridge(outbox=str(outbox)))
        assert fake.seen[0]["text"] == "hey"
    finally:
        fake.close()


def test_send_via_outbox_needs_outbox_field(isolated_env):
    with pytest.raises(RuntimeError) as ei:
        client._send_via_outbox("x", "hi", _sandbox_bridge(outbox=""))
    assert "predates" in str(ei.value)
