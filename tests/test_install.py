"""Tests for cxpeer.install: merge hook entries into hooks.json and install the Codex skill."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import pytest

from cxpeer import install

PKG_SKILL = files("cxpeer").joinpath("data/SKILL.md")
EVENTS = ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"]
EXISTING = {
    "hooks": {
        "PreToolUse": [
            {"matcher": "*", "hooks": [{"type": "command", "command": "curl -sS http://127.0.0.1:3003/hook -d @-"}]}
        ]
    }
}


@pytest.fixture
def codex_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "codex"
    home.mkdir()
    (home / "hooks.json").write_text(json.dumps(EXISTING, indent=2))
    monkeypatch.setenv("CXPEER_CODEX_HOME", str(home))
    return home


def _cxpeer_groups(hooks_json: dict, event: str) -> list[dict]:
    return [g for g in hooks_json["hooks"].get(event, []) if any("cxpeer hook" in h["command"] for h in g["hooks"])]


def test_install_adds_four_hooks_and_keeps_unrelated_entry(codex_home, capsys):
    assert install.run(dry_run=False) == 0
    data = json.loads((codex_home / "hooks.json").read_text())
    assert data["hooks"]["PreToolUse"] == EXISTING["hooks"]["PreToolUse"]
    for event, cmd_event in zip(EVENTS, ["session-start", "user-prompt-submit", "stop", "session-end"]):
        groups = _cxpeer_groups(data, event)
        assert len(groups) == 1, event
        assert groups[0]["matcher"] == "*"
        assert groups[0]["hooks"][0]["type"] == "command"
        assert groups[0]["hooks"][0]["command"].endswith(f"cxpeer hook {cmd_event}")
    assert "hooks.json" in capsys.readouterr().out


def test_install_twice_does_not_duplicate(codex_home):
    install.run(dry_run=False)
    first = (codex_home / "hooks.json").read_text()
    install.run(dry_run=False)
    assert (codex_home / "hooks.json").read_text() == first
    data = json.loads(first)
    assert all(len(_cxpeer_groups(data, e)) == 1 for e in EVENTS)
    assert len(data["hooks"]["PreToolUse"]) == 1


def test_install_writes_skill_file_from_package_data(codex_home):
    install.run(dry_run=False)
    installed = codex_home / "skills" / "cxpeer" / "SKILL.md"
    assert installed.read_text() == PKG_SKILL.read_text()


def test_install_creates_hooks_json_when_missing(tmp_path, monkeypatch):
    home = tmp_path / "fresh"
    monkeypatch.setenv("CXPEER_CODEX_HOME", str(home))
    assert install.run(dry_run=False) == 0
    data = json.loads((home / "hooks.json").read_text())
    assert set(data["hooks"]) == set(EVENTS)


def test_dry_run_prints_result_and_writes_nothing(codex_home, capsys):
    before = (codex_home / "hooks.json").read_text()
    assert install.run(dry_run=True) == 0
    out = capsys.readouterr().out
    assert "SessionStart" in out and "cxpeer hook stop" in out
    assert "SKILL.md" in out
    assert (codex_home / "hooks.json").read_text() == before
    assert not (codex_home / "skills").exists()


def test_invalid_hooks_json_is_left_alone(codex_home, capsys):
    (codex_home / "hooks.json").write_text("{not json")
    assert install.run(dry_run=False) == 1
    assert (codex_home / "hooks.json").read_text() == "{not json"
    assert "hooks.json" in capsys.readouterr().err


def test_skill_text_covers_the_commands_and_the_trust_rule():
    text = PKG_SKILL.read_text()
    assert text.startswith("---\nname: cxpeer\n")
    assert "cxpeer list" in text
    assert 'cxpeer send --to NAME "text"' in text
    assert "forwarded" in text
    assert "not authority" in text
