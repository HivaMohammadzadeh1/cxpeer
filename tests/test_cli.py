"""CLI wiring: each subcommand reaches its module with the right arguments."""

from __future__ import annotations

import io
import json
import time

import pytest

from cxpeer import cli, client


def test_hook_passes_stdin_payload_to_hooks_run(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.hooks, "run", lambda event, payload: seen.update(event=event, payload=payload) or 0)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "t1", "cwd": "/x"})))
    assert cli.main(["hook", "stop"]) == 0
    assert seen == {"event": "stop", "payload": {"session_id": "t1", "cwd": "/x"}}


def test_hook_with_unparseable_stdin_still_exits_zero(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.hooks, "run", lambda event, payload: seen.update(payload=payload) or 0)
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert cli.main(["hook", "session-end"]) == 0
    assert seen == {"payload": {}}


def test_hook_rejects_unknown_event():
    with pytest.raises(SystemExit):
        cli.main(["hook", "explode"])


def test_install_forwards_dry_run(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.install, "run", lambda dry_run: seen.update(dry_run=dry_run) or 0)
    assert cli.main(["install", "--dry-run"]) == 0
    assert seen == {"dry_run": True}


def test_bridge_forwards_arguments(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.bridge, "run", lambda thread, cwd, name=None, watch_pid=None: seen.update(
        thread=thread, cwd=cwd, name=name, watch_pid=watch_pid) or 0)
    assert cli.main(["bridge", "--thread", "t1", "--cwd", "/p", "--watch-pid", "42"]) == 0
    assert seen == {"thread": "t1", "cwd": "/p", "name": None, "watch_pid": 42}


def test_status_without_bridges_says_so(isolated_env, capsys):
    assert cli.main(["status"]) == 0
    assert "no bridges" in capsys.readouterr().out


def test_status_reports_unreachable_bridge(isolated_env, capsys):
    bridges = isolated_env["home"] / "bridges"
    bridges.mkdir()
    (bridges / "t9.json").write_text(json.dumps({
        "pid": 1, "sock": str(isolated_env["socks"] / "missing.sock"), "token": "x",
        "name": "codex-dead-9", "cwd": "/p", "thread": "t9", "started": 5}))
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("codex-dead-9  pid=1  thread=t9  unreachable")


def test_spawn_forwards_extra_args_after_double_dash(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.spawn, "run", lambda kind, cwd, name, prompt, extra, peer_name=None, wait=False, timeout=60.0: seen.update(
        kind=kind, cwd=cwd, name=name, prompt=prompt, extra=extra, peer_name=peer_name, wait=wait, timeout=timeout) or 0)
    assert cli.main(["spawn", "claude", "--cwd", "/p", "--name", "intern", "--prompt", "hi there",
                     "--", "--permission-mode", "auto"]) == 0
    assert seen == {"kind": "claude", "cwd": "/p", "name": "intern", "prompt": "hi there",
                    "extra": ["--permission-mode", "auto"], "peer_name": None, "wait": False, "timeout": 60.0}


def test_spawn_forwards_wait_and_peer_name(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.spawn, "run", lambda kind, cwd, name, prompt, extra, peer_name=None, wait=False, timeout=60.0: seen.update(
        peer_name=peer_name, wait=wait, timeout=timeout) or 0)
    assert cli.main(["spawn", "codex", "--peer-name", "codex-tests", "--wait", "--timeout", "90"]) == 0
    assert seen == {"peer_name": "codex-tests", "wait": True, "timeout": 90.0}


def test_list_sandboxed_reads_snapshot(monkeypatch, capsys):
    rows = [
        {"name": "codex-a", "ref": "abc123", "status": "idle", "cwd": "/w/a"},
        {"name": "codex-b", "ref": "def456", "status": "busy", "cwd": "/w/b"},
    ]
    snapshot_bridge = client.BridgeInfo(
        pid=1, sock="/x", token="t" * 32, name="br", cwd="/w", thread="t", started=1,
        peers_file="/does/not/matter",
    )
    monkeypatch.setattr(cli.client, "sandboxed", lambda: True)
    monkeypatch.setattr(cli.client, "find_bridge", lambda cwd=None, thread=None: snapshot_bridge)
    monkeypatch.setattr(cli.client, "list_peers_snapshot", lambda bridge: rows)
    assert cli.main(["list"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "codex-a [abc123]  idle  /w/a",
        "codex-b [def456]  busy  /w/b",
    ]


def test_list_sandboxed_without_snapshot_warns_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(cli.client, "sandboxed", lambda: True)
    monkeypatch.setattr(cli.client, "find_bridge", lambda cwd=None, thread=None: None)
    assert cli.main(["list"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no fresh bridge snapshot" in captured.err


def test_send_sandboxed_without_bridge_reports_blocked_sockets(monkeypatch, capsys):
    monkeypatch.setattr(cli.client, "find_bridge", lambda cwd=None, thread=None: None)
    monkeypatch.setattr(cli.client, "sandboxed", lambda: True)
    assert cli.main(["send", "--to", "codex-x", "hi"]) == 1
    assert "sockets are blocked in this sandbox" in capsys.readouterr().err


def test_doctor_dispatches(monkeypatch):
    monkeypatch.setattr(cli.doctor, "run", lambda: 0)
    assert cli.main(["doctor"]) == 0
