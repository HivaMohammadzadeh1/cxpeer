"""CLI wiring: each subcommand reaches its module with the right arguments."""

from __future__ import annotations

import io
import json

import pytest

from cxpeer import cli


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
    monkeypatch.setattr(cli.spawn, "run", lambda kind, cwd, name, prompt, extra: seen.update(
        kind=kind, cwd=cwd, name=name, prompt=prompt, extra=extra) or 0)
    assert cli.main(["spawn", "claude", "--cwd", "/p", "--name", "intern", "--prompt", "hi there",
                     "--", "--permission-mode", "auto"]) == 0
    assert seen == {"kind": "claude", "cwd": "/p", "name": "intern", "prompt": "hi there",
                    "extra": ["--permission-mode", "auto"]}
