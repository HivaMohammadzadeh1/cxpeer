"""paths.sessions_dirs(): explicit list vs explicit single vs auto-discovery of alt accounts."""

from __future__ import annotations

from pathlib import Path

from cxpeer import paths


def _clear(monkeypatch):
    monkeypatch.delenv("CXPEER_CLAUDE_SESSIONS_DIRS", raising=False)
    monkeypatch.delenv("CXPEER_CLAUDE_SESSIONS_DIR", raising=False)


def test_explicit_list_is_used_verbatim(monkeypatch, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    monkeypatch.setenv("CXPEER_CLAUDE_SESSIONS_DIRS", f"{a}:{b}")
    assert paths.sessions_dirs() == [a, b]


def test_single_override_skips_discovery(monkeypatch, tmp_path):
    _clear(monkeypatch)
    only = tmp_path / "only" / "sessions"
    monkeypatch.setenv("CXPEER_CLAUDE_SESSIONS_DIR", str(only))
    # A discoverable alt account exists, but the explicit single override wins and skips discovery.
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    (home / ".claude-alt" / "sessions").mkdir(parents=True)
    assert paths.sessions_dirs() == [only]


def test_discovery_finds_alt_accounts_with_default_first(monkeypatch, tmp_path):
    _clear(monkeypatch)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    primary = home / ".claude" / "sessions"
    alt_dash = home / ".claude-work" / "sessions"
    alt_underscore = home / ".claude_test" / "sessions"
    for d in (primary, alt_dash, alt_underscore):
        d.mkdir(parents=True)

    result = paths.sessions_dirs()
    assert result[0] == primary  # the default account always comes first
    assert result[1:] == sorted([alt_dash, alt_underscore])  # discovered ones sorted, deduped


def test_discovery_ignores_dirs_without_a_sessions_subdir(monkeypatch, tmp_path):
    _clear(monkeypatch)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    (home / ".claude-empty").mkdir(parents=True)  # no sessions/ inside -> not a registry
    assert paths.sessions_dirs() == [home / ".claude" / "sessions"]


def test_primary_stays_unchanged(monkeypatch, tmp_path):
    # sessions_dir() must not move: it is still the single primary registry.
    d = tmp_path / "primary"
    monkeypatch.setenv("CXPEER_CLAUDE_SESSIONS_DIR", str(d))
    assert paths.sessions_dir() == d
    assert paths.sessions_dirs()[0] == d
