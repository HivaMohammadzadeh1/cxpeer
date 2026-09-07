"""Tests for cxpeer.spawn: start a detached tmux session running codex or claude."""

from __future__ import annotations

import re
import shlex
import stat
from pathlib import Path

import pytest

from cxpeer import spawn


@pytest.fixture
def fake_tmux(isolated_env, monkeypatch):
    """A stand-in tmux. Records argv to tmux_calls.log; has-session succeeds when a marker
    file exists under tmux-sessions/, and new-session creates that marker. FAKE_TMUX_FAIL makes
    new-session exit 1."""
    home = isolated_env["home"]
    calls = home / "tmux_calls.jsonl"
    sessions = home / "tmux-sessions"
    sessions.mkdir()
    script = home / "fake-tmux"
    script.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\037' \"$@\" >> \"{calls}\"; printf '\\n' >> \"{calls}\"\n"
        'case "$1" in\n'
        f'  has-session) [ -e "{sessions}/$3" ] ;;\n'
        '  new-session) if [ -n "$FAKE_TMUX_FAIL" ]; then echo "fake tmux: boom" >&2; exit 1; fi;\n'
        f'               touch "{sessions}/$4" ;;\n'
        "esac\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("CXPEER_TMUX_BIN", str(script))
    return calls


def _calls(calls: Path) -> list[list[str]]:
    """Each line is one tmux invocation: argv joined by the unit separator (\\x1f)."""
    if not calls.exists():
        return []
    return [l.split("\x1f")[:-1] for l in calls.read_text().splitlines() if l]


def _new_session(calls: Path) -> list[str]:
    found = [c for c in _calls(calls) if c[0] == "new-session"]
    assert len(found) == 1, found
    return found[0]


@pytest.fixture
def proj(tmp_path) -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    return d


def test_codex_default_name_and_output_line(fake_tmux, proj, capsys):
    rc = spawn.run("codex", str(proj), None, None, [])
    out, err = capsys.readouterr()
    assert rc == 0 and err == ""
    m = re.fullmatch(r"started (cx-codex-proj-[0-9a-f]{4}) \(codex\) in (.+); attach with: tmux attach -t \1\n", out)
    assert m, out
    session, cwd = m.group(1), m.group(2)
    assert cwd == str(proj)
    calls = _calls(fake_tmux)
    assert calls[0] == ["has-session", "-t", session]
    assert calls[1] == ["new-session", "-d", "-s", session, "-c", str(proj), "codex"]


def test_explicit_name_is_prefixed_and_claude_gets_dash_n(fake_tmux, proj, capsys):
    rc = spawn.run("claude", str(proj), "reviewer", None, [])
    assert rc == 0
    assert capsys.readouterr().out.startswith("started cx-reviewer (claude) in ")
    ns = _new_session(fake_tmux)
    assert ns[3] == "cx-reviewer"
    assert shlex.split(ns[-1]) == ["claude", "-n", "reviewer"]


def test_claude_without_name_uses_session_name_for_dash_n(fake_tmux, proj):
    spawn.run("claude", str(proj), None, None, [])
    ns = _new_session(fake_tmux)
    assert shlex.split(ns[-1]) == ["claude", "-n", ns[3]]


def test_prompt_and_extra_args_are_quoted_safely(fake_tmux, proj):
    prompt = 'say "hi there" and don\'t stop; echo $HOME'
    rc = spawn.run("codex", str(proj), None, prompt, ["--model", "gpt-5 codex"])
    assert rc == 0
    cmd = _new_session(fake_tmux)[-1]
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
    assert len([c for c in _calls(fake_tmux) if c[0] == "new-session"]) == 1


def test_unknown_kind_returns_2_without_touching_tmux(fake_tmux, proj, capsys):
    rc = spawn.run("gemini", str(proj), None, None, [])
    out, err = capsys.readouterr()
    assert rc == 2
    assert out == ""
    assert "gemini" in err and err.count("\n") == 1
    assert _calls(fake_tmux) == []


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
    ns = _new_session(fake_tmux)
    assert ns[5] == str(proj)
    assert ns[3].startswith("cx-codex-proj-")
