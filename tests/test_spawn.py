"""Tests for cxpeer.spawn: start a detached tmux session running codex or claude."""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
from pathlib import Path

import pytest

from cxpeer import spawn

UPDATE = "Update available! 0.153.3 -> 0.154.0\n  1. Update now\n  2. Skip\n"
TRUST = "Do you trust the contents of this directory?\n  1. Yes, continue\n  2. No\n"
HOOKS = "These hooks are new or changed:\n  SessionStart ...\n  1. Review\n  2. Trust all and continue\n"
INPUT_BOX = "> Ask Codex to do anything\n"


class FakeTmux:
    """Records every tmux argv; serves canned capture-pane screens in order (the last one
    repeats); has-session succeeds after new-session created a marker. With FAKE_BRIDGE_STATE
    set, new-session also drops a bridge state file, as the SessionStart hook would."""

    def __init__(self, home: Path, bridges: Path):
        self.calls_file = home / "tmux_calls.log"
        self.screens = home / "screens"
        self.screens.mkdir()
        self.counter = home / "screen_counter"
        sessions = home / "tmux-sessions"
        sessions.mkdir()
        self.script = home / "fake-tmux"
        self.script.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\037' \"$@\" >> \"{self.calls_file}\"; printf '\\n' >> \"{self.calls_file}\"\n"
            'case "$1" in\n'
            f'  has-session) [ -e "{sessions}/$3" ] ;;\n'
            "  new-session)\n"
            '    if [ -n "$FAKE_TMUX_FAIL" ]; then echo "fake tmux: boom" >&2; exit 1; fi\n'
            f'    touch "{sessions}/$4"\n'
            '    if [ -n "$FAKE_BRIDGE_STATE" ]; then\n'
            "      printf '{\"pid\": %s, \"sock\": \"/x.sock\", \"token\": \"t\", \"name\": \"codex-proj-ab\", "
            "\"cwd\": \"%s\", \"thread\": \"thread-1\", \"started\": %s000}\\n' "
            f'"$$" "$(cd "$6" && pwd -P)" "$(date +%s)" > "{bridges}/thread-1.json"\n'
            "    fi ;;\n"
            "  capture-pane)\n"
            f'    n=$(cat "{self.counter}" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "{self.counter}"\n'
            f'    f="{self.screens}/$n.txt"\n'
            f'    [ -e "$f" ] || f="{self.screens}/$(ls "{self.screens}" | sort -n | tail -n 1)"\n'
            '    cat "$f" 2>/dev/null ;;\n'
            "esac\n"
        )
        self.script.chmod(self.script.stat().st_mode | stat.S_IXUSR)

    def set_screens(self, *screens: str) -> None:
        for i, text in enumerate(screens, 1):
            (self.screens / f"{i}.txt").write_text(text)

    def calls(self) -> list[list[str]]:
        if not self.calls_file.exists():
            return []
        return [l.split("\x1f")[:-1] for l in self.calls_file.read_text().splitlines() if l]

    def new_session(self) -> list[str]:
        found = [c for c in self.calls() if c[0] == "new-session"]
        assert len(found) == 1, found
        return found[0]

    def send_keys(self) -> list[list[str]]:
        return [c[3:] for c in self.calls() if c[0] == "send-keys"]


@pytest.fixture
def fake_tmux(isolated_env, monkeypatch) -> FakeTmux:
    bridges = isolated_env["home"] / "bridges"
    bridges.mkdir()
    ft = FakeTmux(isolated_env["home"], bridges)
    monkeypatch.setenv("CXPEER_TMUX_BIN", str(ft.script))
    monkeypatch.delenv("FAKE_BRIDGE_STATE", raising=False)
    return ft


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    c = FakeClock()
    monkeypatch.setattr(spawn, "_monotonic", c.monotonic)
    monkeypatch.setattr(spawn, "_sleep", c.sleep)
    return c


@pytest.fixture
def proj(tmp_path) -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    return d


# --- existing behaviour -------------------------------------------------------------------

def test_codex_default_name_and_output_line(fake_tmux, proj, capsys):
    rc = spawn.run("codex", str(proj), None, None, [])
    out, err = capsys.readouterr()
    assert rc == 0 and err == ""
    m = re.fullmatch(r"started (cx-codex-proj-[0-9a-f]{4}) \(codex\) in (.+); attach with: tmux attach -t \1\n", out)
    assert m, out
    session, cwd = m.group(1), m.group(2)
    assert cwd == str(proj)
    calls = fake_tmux.calls()
    assert calls[0] == ["has-session", "-t", session]
    assert calls[1] == ["new-session", "-d", "-s", session, "-c", str(proj), "codex"]


def test_explicit_name_is_prefixed_and_claude_gets_dash_n(fake_tmux, proj, capsys):
    rc = spawn.run("claude", str(proj), "reviewer", None, [])
    assert rc == 0
    assert capsys.readouterr().out.startswith("started cx-reviewer (claude) in ")
    ns = fake_tmux.new_session()
    assert ns[3] == "cx-reviewer"
    assert ns[-1] == "CXPEER_PEER_NAME=reviewer claude -n reviewer"


def test_claude_without_name_uses_session_name_for_dash_n(fake_tmux, proj):
    spawn.run("claude", str(proj), None, None, [])
    ns = fake_tmux.new_session()
    assert shlex.split(ns[-1]) == ["claude", "-n", ns[3]]


def test_prompt_and_extra_args_are_quoted_safely(fake_tmux, proj):
    prompt = 'say "hi there" and don\'t stop; echo $HOME'
    rc = spawn.run("codex", str(proj), None, prompt, ["--model", "gpt-5 codex"])
    assert rc == 0
    cmd = fake_tmux.new_session()[-1]
    assert cmd == shlex.join(["codex", "--model", "gpt-5 codex", prompt])
    assert shlex.split(cmd)[-1] == prompt


def test_duplicate_session_is_refused(fake_tmux, proj, capsys):
    assert spawn.run("codex", str(proj), "dup", None, []) == 0
    capsys.readouterr()
    rc = spawn.run("codex", str(proj), "dup", None, [])
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert "cx-dup" in err and "already exists" in err
    assert len([c for c in fake_tmux.calls() if c[0] == "new-session"]) == 1


def test_unknown_kind_returns_2_without_touching_tmux(fake_tmux, proj, capsys):
    rc = spawn.run("gemini", str(proj), None, None, [])
    out, err = capsys.readouterr()
    assert rc == 2
    assert out == ""
    assert "gemini" in err and err.count("\n") == 1
    assert fake_tmux.calls() == []


def test_missing_tmux_binary_returns_1(isolated_env, proj, monkeypatch, capsys):
    monkeypatch.setenv("CXPEER_TMUX_BIN", str(isolated_env["home"] / "no-such-tmux"))
    rc = spawn.run("codex", str(proj), None, None, [])
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert "no-such-tmux" in err and "tmux" in err


def test_tmux_failure_is_reported(fake_tmux, proj, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_TMUX_FAIL", "1")
    rc = spawn.run("codex", str(proj), None, None, [])
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert "boom" in err


def test_relative_cwd_is_made_absolute(fake_tmux, proj, monkeypatch, capsys):
    monkeypatch.chdir(proj.parent)
    spawn.run("codex", "proj", None, None, [])
    ns = fake_tmux.new_session()
    assert ns[5] == str(proj)
    assert ns[3].startswith("cx-codex-proj-")


# --- peer_name ----------------------------------------------------------------------------

def test_peer_name_is_injected_into_the_command_env_with_quoting(fake_tmux, proj):
    peer = "my peer's box"
    spawn.run("codex", str(proj), None, None, [], peer_name=peer)
    cmd = fake_tmux.new_session()[-1]
    assert cmd == f"CXPEER_PEER_NAME={shlex.quote(peer)} codex"
    assert shlex.split(cmd) == [f"CXPEER_PEER_NAME={peer}", "codex"]


def test_peer_name_overrides_name_for_the_env_but_not_the_session(fake_tmux, proj):
    spawn.run("codex", str(proj), "sess", None, [], peer_name="shown")
    ns = fake_tmux.new_session()
    assert ns[3] == "cx-sess"
    assert ns[-1] == "CXPEER_PEER_NAME=shown codex"


def test_no_name_and_no_peer_name_sets_no_env(fake_tmux, proj):
    spawn.run("codex", str(proj), None, "hi", [])
    assert fake_tmux.new_session()[-1] == "codex hi"


# --- wait ---------------------------------------------------------------------------------

def test_wait_answers_each_prompt_once_and_reports_the_bridge(fake_tmux, clock, proj, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_BRIDGE_STATE", "1")
    fake_tmux.set_screens(UPDATE, UPDATE, TRUST, HOOKS, INPUT_BOX)
    rc = spawn.run("codex", str(proj), "job", "do the task", [], wait=True, timeout=30)
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert fake_tmux.send_keys() == [["2", "Enter"], ["1", "Enter"], ["2", "Enter"]]
    assert out.splitlines()[-1] == "peer codex-proj-ab is up (thread thread-1); attach with: tmux attach -t cx-job"


def test_wait_without_prompt_sends_ready_then_a_second_enter(fake_tmux, clock, proj, monkeypatch):
    monkeypatch.setenv("FAKE_BRIDGE_STATE", "1")
    fake_tmux.set_screens(INPUT_BOX)
    rc = spawn.run("codex", str(proj), "job", None, [], wait=True, timeout=30)
    assert rc == 0
    assert fake_tmux.send_keys() == [["Say READY and nothing else.", "Enter"], ["Enter"]]
    assert 2 in clock.sleeps


def test_wait_times_out_when_the_input_box_never_appears(fake_tmux, clock, proj, capsys):
    fake_tmux.set_screens(UPDATE)
    rc = spawn.run("codex", str(proj), "job", "x", [], wait=True, timeout=5)
    out, err = capsys.readouterr()
    assert rc == 1
    assert fake_tmux.send_keys() == [["2", "Enter"]]
    assert "Update available" in err and "timed out" in err
    assert clock.t >= 5


def test_wait_ignores_a_bridge_started_before_the_spawn(fake_tmux, clock, proj, capsys):
    fake_tmux.set_screens(INPUT_BOX)
    stale = {"pid": os.getpid(), "sock": "/x.sock", "token": "t", "name": "old", "cwd": str(proj),
             "thread": "old-thread", "started": 0}
    (isolated_dir := Path(os.environ["CXPEER_HOME"]) / "bridges").mkdir(exist_ok=True)
    (isolated_dir / "old-thread.json").write_text(json.dumps(stale))
    rc = spawn.run("codex", str(proj), "job", "x", [], wait=True, timeout=5)
    out, err = capsys.readouterr()
    assert rc == 1
    assert "no bridge" in err and "Ask Codex" in err
    assert "peer old" not in out


def test_wait_matches_the_bridge_cwd_through_symlinks(fake_tmux, clock, proj, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_BRIDGE_STATE", "1")
    link = tmp_path / "link"
    link.symlink_to(proj)
    fake_tmux.set_screens(INPUT_BOX)
    rc = spawn.run("codex", str(link), "job", "x", [], wait=True, timeout=30)
    assert rc == 0, capsys.readouterr().err
    assert fake_tmux.new_session()[5] == str(link)


def test_wait_for_claude_only_waits_for_the_pane_to_settle(fake_tmux, clock, proj, capsys):
    fake_tmux.set_screens("loading", "ready", "ready")
    rc = spawn.run("claude", str(proj), "cl", None, [], wait=True, timeout=30)
    out, err = capsys.readouterr()
    assert rc == 0 and err == ""
    assert fake_tmux.send_keys() == []
    assert out.startswith("started cx-cl (claude)")
    assert 3 <= len([c for c in fake_tmux.calls() if c[0] == "capture-pane"]) <= 4
