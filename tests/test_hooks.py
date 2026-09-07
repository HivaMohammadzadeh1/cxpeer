"""Tests for cxpeer.hooks: Codex hook handlers that talk to the bridge and never fail a turn."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from cxpeer import hooks, paths

SID = "11111111-2222-4333-8444-555555555555"


class FakeBridge:
    """A UDS server that accepts one connection, reads to EOF, and stores the decoded lines."""

    def __init__(self, sock_path: Path, token: str):
        self.token = token
        self.lines: list[dict] = []
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(str(sock_path))
        self._srv.listen(1)
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        conn, _ = self._srv.accept()
        buf = b""
        with conn:
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                buf += data
        self.lines = [json.loads(l) for l in buf.decode().split("\n") if l]
        self._srv.close()

    def wait(self):
        self._t.join(5)
        assert not self._t.is_alive(), "bridge never got a connection"
        return self.lines


def _write_state(sid: str, **fields) -> Path:
    bridges = paths.bridges_dir()
    bridges.mkdir(parents=True, exist_ok=True)
    p = bridges / f"{sid}.json"
    p.write_text(json.dumps(fields))
    return p


@pytest.fixture
def bridge(isolated_env) -> FakeBridge:
    sock = isolated_env["socks"] / "bridge.sock"
    fb = FakeBridge(sock, token="tok-bridge")
    _write_state(SID, pid=os.getpid(), sock=str(sock), token="tok-bridge", name="codex-x-11",
                 cwd="/tmp", thread=SID, started=0)
    return fb


def test_user_prompt_submit_sends_turn_started_with_msg_id(bridge, capsys):
    rc = hooks.run("user-prompt-submit", {
        "session_id": SID, "cwd": "/tmp", "hook_event_name": "UserPromptSubmit",
        "prompt": "do the thing\n\n[cxpeer msg_id=abc-123] Your final answer is forwarded.",
    })
    assert rc == 0
    assert capsys.readouterr().out == ""
    lines = bridge.wait()
    assert lines[0] == {"type": "auth", "token": "tok-bridge"}
    assert lines[1] == {"type": "cxpeer.turn_started", "msg_id": "abc-123"}


def test_user_prompt_submit_without_marker_sends_null_msg_id(bridge):
    hooks.run("user-prompt-submit", {"session_id": SID, "prompt": "plain prompt"})
    lines = bridge.wait()
    assert lines[1] == {"type": "cxpeer.turn_started", "msg_id": None}


def test_stop_sends_turn_ended_with_last_message(bridge):
    hooks.run("stop", {"session_id": SID, "last_assistant_message": "LOOP-OK", "stop_hook_active": False})
    lines = bridge.wait()
    assert lines[0]["type"] == "auth"
    assert lines[1] == {"type": "cxpeer.turn_ended", "last_assistant_message": "LOOP-OK"}


def test_session_end_sends_shutdown(bridge):
    hooks.run("session-end", {"session_id": SID})
    lines = bridge.wait()
    assert lines[1] == {"type": "cxpeer.shutdown"}


def test_missing_bridge_is_logged_and_still_returns_zero(isolated_env, capsys):
    rc = hooks.run("stop", {"session_id": "no-such-thread", "last_assistant_message": "x"})
    assert rc == 0
    assert capsys.readouterr().out == ""
    log = (paths.logs_dir() / "hooks.log").read_text()
    assert "no-such-thread" in log


def test_dead_bridge_socket_is_logged_and_still_returns_zero(isolated_env, capsys):
    _write_state(SID, pid=os.getpid(), sock=str(isolated_env["socks"] / "gone.sock"), token="t",
                 name="n", cwd="/tmp", thread=SID, started=0)
    rc = hooks.run("stop", {"session_id": SID, "last_assistant_message": "x"})
    assert rc == 0
    assert capsys.readouterr().out == ""
    assert "stop" in (paths.logs_dir() / "hooks.log").read_text()


def test_unknown_event_returns_zero_silently(isolated_env, capsys):
    assert hooks.run("bogus-event", {"session_id": SID}) == 0
    assert capsys.readouterr().out == ""


def test_run_returns_zero_even_when_payload_is_garbage(isolated_env, capsys):
    assert hooks.run("user-prompt-submit", {}) == 0
    assert capsys.readouterr().out == ""


class FakePopen:
    calls: list[tuple[list[str], dict]] = []

    def __init__(self, argv, **kw):
        FakePopen.calls.append((list(argv), kw))
        self.pid = 4242


@pytest.fixture
def popen(monkeypatch):
    """Capture the bridge spawn; stub ps so subprocess.run is never reached."""
    FakePopen.calls = []
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(hooks, "ps_lookup", lambda pid: None)
    return FakePopen


def test_session_start_spawns_detached_bridge(isolated_env, popen, capsys):
    rc = hooks.run("session-start", {"session_id": SID, "cwd": "/work/proj"})
    assert rc == 0
    assert capsys.readouterr().out == ""
    assert len(popen.calls) == 1
    argv, kw = popen.calls[0]
    assert argv[:4] == [sys.executable, "-m", "cxpeer", "bridge"]
    opts = dict(zip(argv[4::2], argv[5::2]))
    assert opts["--thread"] == SID
    assert opts["--cwd"] == "/work/proj"
    assert opts["--watch-pid"] == str(os.getppid())  # ps stubbed out: falls back to the parent pid
    assert kw["start_new_session"] is True
    assert kw["stdin"] == subprocess.DEVNULL
    assert kw["stdout"] == subprocess.DEVNULL
    assert kw["stderr"] == subprocess.DEVNULL


def test_session_start_skips_spawn_when_bridge_alive(isolated_env, popen):
    _write_state(SID, pid=os.getpid(), sock="/x.sock", token="t", name="n", cwd="/tmp", thread=SID, started=0)
    hooks.run("session-start", {"session_id": SID, "cwd": "/tmp"})
    assert popen.calls == []


def test_session_start_respawns_when_recorded_pid_is_dead(isolated_env, popen):
    _write_state(SID, pid=2**22 - 1, sock="/x.sock", token="t", name="n", cwd="/tmp", thread=SID, started=0)
    hooks.run("session-start", {"session_id": SID, "cwd": "/tmp"})
    assert len(popen.calls) == 1


def test_find_codex_pid_walks_up_to_first_codex_ancestor():
    tree = {100: (90, "/bin/sh"), 90: (80, "/opt/homebrew/bin/codex"), 80: (1, "launchd")}
    assert hooks.find_codex_pid(100, lookup=lambda pid: tree.get(pid)) == 90


def test_find_codex_pid_falls_back_to_start_after_five_levels():
    tree = {i: (i - 1, "sh") for i in range(100, 90, -1)}
    tree[90] = (1, "codex")  # 10 levels up: out of reach
    assert hooks.find_codex_pid(100, lookup=lambda pid: tree.get(pid)) == 100


def test_ps_lookup_reads_real_parent_and_comm():
    ppid, comm = hooks.ps_lookup(os.getpid())
    assert ppid == os.getppid()
    assert "python" in comm.lower()


def test_ps_lookup_returns_none_for_bogus_pid():
    assert hooks.ps_lookup(2**22 - 1) is None
