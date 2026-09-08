"""doctor tests: each check's ok and FAIL/warn branch, the self-test happy path, and run()."""

from __future__ import annotations

import json

import pytest

from cxpeer import doctor, install


def _which(mapping):
    """A shutil.which stand-in: return the mapped path, else None."""
    return lambda name: mapping.get(name)


def _install_hooks_and_skill(home, monkeypatch):
    monkeypatch.setenv("CXPEER_CODEX_HOME", str(home))
    assert install.run(dry_run=False) == 0


# ----- format --------------------------------------------------------------


def test_format_covers_each_level():
    assert doctor._format(doctor.Check("ok", "n", "d")) == "ok  n: d"
    assert doctor._format(doctor.Check("warn", "n", "d")) == "warn n: d"
    assert doctor._format(doctor.Check("FAIL", "n", "d", "do x")) == "FAIL n: d -> do x"


# ----- check 1: claude -----------------------------------------------------


def test_check_claude_ok(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", _which({"claude": "/bin/claude"}))
    monkeypatch.setattr(doctor, "_cmd_output", lambda argv: "2.1.263 (Claude Code)")
    c = doctor.check_claude()
    assert c.level == "ok" and "2.1.263" in c.detail


def test_check_claude_warns_on_off_version(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", _which({"claude": "/bin/claude"}))
    monkeypatch.setattr(doctor, "_cmd_output", lambda argv: "2.0.9")
    assert doctor.check_claude().level == "warn"


def test_check_claude_warns_when_absent(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", _which({}))
    assert doctor.check_claude().level == "warn"


# ----- check 2 & 3: sessions dir, socket dir -------------------------------


def test_check_sessions_dir_ok(isolated_env):
    lines = doctor.check_sessions_dir()
    assert [c.level for c in lines] == ["ok"]


def test_check_sessions_dir_fails_when_missing(isolated_env, monkeypatch, tmp_path):
    monkeypatch.setenv("CXPEER_CLAUDE_SESSIONS_DIR", str(tmp_path / "no-sessions"))
    lines = doctor.check_sessions_dir()
    assert lines[0].level == "FAIL" and "Claude Code session" in lines[0].fix


def test_check_sessions_dir_warns_on_mode(isolated_env, monkeypatch, tmp_path):
    d = tmp_path / "loose"
    d.mkdir()
    d.chmod(0o755)
    monkeypatch.setenv("CXPEER_CLAUDE_SESSIONS_DIR", str(d))
    assert doctor.check_sessions_dir()[0].level == "warn"


def test_check_sessions_dir_one_line_per_registry(monkeypatch, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir(mode=0o700)
    b.mkdir(mode=0o700)
    monkeypatch.setenv("CXPEER_CLAUDE_SESSIONS_DIRS", f"{a}:{b}")
    lines = doctor.check_sessions_dir()
    assert [c.level for c in lines] == ["ok", "ok"]
    assert str(a) in lines[0].detail and str(b) in lines[1].detail


def test_check_sock_dir_ok(isolated_env):
    assert doctor.check_sock_dir().level == "ok"


def test_check_sock_dir_warns_when_missing(isolated_env, monkeypatch, tmp_path):
    monkeypatch.setenv("CXPEER_SOCK_DIR", str(tmp_path / "no-socks"))
    assert doctor.check_sock_dir().level == "warn"


# ----- check 4: codex ------------------------------------------------------


def test_check_codex_ok(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", _which({"codex": "/bin/codex"}))
    monkeypatch.setattr(doctor, "_cmd_output", lambda argv: "codex-cli 0.153.3")
    assert doctor.check_codex().level == "ok"


def test_check_codex_fails_when_old(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", _which({"codex": "/bin/codex"}))
    monkeypatch.setattr(doctor, "_cmd_output", lambda argv: "codex-cli 0.152.9")
    c = doctor.check_codex()
    assert c.level == "FAIL" and c.fix


def test_check_codex_fails_when_absent(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", _which({}))
    assert doctor.check_codex().level == "FAIL"


def test_check_codex_fails_on_unparseable(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", _which({"codex": "/bin/codex"}))
    monkeypatch.setattr(doctor, "_cmd_output", lambda argv: "not a version")
    assert doctor.check_codex().level == "FAIL"


# ----- check 5: hooks.json -------------------------------------------------


def test_check_hooks_ok(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    home.mkdir()
    _install_hooks_and_skill(home, monkeypatch)  # real hook_command uses sys.executable, which exists
    assert doctor.check_hooks(home).level == "ok"


def test_check_hooks_fails_when_absent(tmp_path):
    home = tmp_path / "empty"
    home.mkdir()
    c = doctor.check_hooks(home)
    assert c.level == "FAIL" and "install" in c.fix


def test_check_hooks_fails_when_entry_missing(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    home.mkdir()
    _install_hooks_and_skill(home, monkeypatch)
    data = json.loads((home / "hooks.json").read_text())
    del data["hooks"]["Stop"]
    (home / "hooks.json").write_text(json.dumps(data))
    assert doctor.check_hooks(home).level == "FAIL"


def test_check_hooks_fails_when_interpreter_missing(tmp_path):
    home = tmp_path / "codex"
    home.mkdir()
    hooks = {"hooks": {event: [{"matcher": "*", "hooks": [
        {"type": "command", "command": f"/no/such/python -m cxpeer hook {name}"}]}]
        for event, name in install.HOOK_EVENTS.items()}}
    (home / "hooks.json").write_text(json.dumps(hooks))
    c = doctor.check_hooks(home)
    assert c.level == "FAIL" and "interpreter" in c.detail


# ----- check 6: trust ------------------------------------------------------


def _write_trust(home, events):
    hooks_path = home / "hooks.json"
    hooks_path.write_text("{}")
    lines = ["[hooks.state]"]
    lines += [f'"{hooks_path}:{e}:0:0" = true' for e in events]
    (home / "config.toml").write_text("\n".join(lines) + "\n")


def test_check_trust_ok(tmp_path):
    home = tmp_path / "codex"
    home.mkdir()
    _write_trust(home, doctor.TRUST_EVENTS)
    assert doctor.check_trust(home).level == "ok"


def test_check_trust_fails_without_config(tmp_path):
    home = tmp_path / "codex"
    home.mkdir()
    c = doctor.check_trust(home)
    assert c.level == "FAIL" and "Trust all" in c.fix


def test_check_trust_fails_when_event_untrusted(tmp_path):
    home = tmp_path / "codex"
    home.mkdir()
    _write_trust(home, [e for e in doctor.TRUST_EVENTS if e != "interrupt"])
    c = doctor.check_trust(home)
    assert c.level == "FAIL" and "interrupt" in c.detail


# ----- check 7: skill ------------------------------------------------------


def test_check_skill_ok(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    home.mkdir()
    _install_hooks_and_skill(home, monkeypatch)
    assert doctor.check_skill(home).level == "ok"


def test_check_skill_warns_when_missing(tmp_path):
    home = tmp_path / "empty"
    home.mkdir()
    assert doctor.check_skill(home).level == "warn"


def test_check_skill_warns_when_stale(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    home.mkdir()
    _install_hooks_and_skill(home, monkeypatch)
    (home / "skills" / "cxpeer" / "SKILL.md").write_text("stale")
    assert doctor.check_skill(home).level == "warn"


# ----- check 8: tmux -------------------------------------------------------


def test_check_tmux_ok(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", _which({"tmux": "/bin/tmux"}))
    assert doctor.check_tmux().level == "ok"


def test_check_tmux_warns_when_absent(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", _which({}))
    assert doctor.check_tmux().level == "warn"


# ----- check 9: self-test (spawns a real bridge subprocess) ----------------


def test_self_test_happy_path(isolated_env, fake_codex):
    c = doctor.self_test()
    assert c.level == "ok", c.detail
    assert "queued a message" in c.detail


# ----- run() aggregation ---------------------------------------------------


def test_run_returns_1_on_any_failure(isolated_env, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(doctor.shutil, "which", _which({}))  # claude/codex/tmux all absent -> codex FAILs
    monkeypatch.setattr(doctor, "self_test", lambda: doctor.Check("ok", "self-test", "skipped"))
    empty_home = tmp_path / "empty"
    empty_home.mkdir()
    assert doctor.run(codex_home=empty_home) == 1
    out = capsys.readouterr().out
    assert "FAIL codex:" in out and "FAIL codex-hooks:" in out


def test_run_returns_0_when_only_warnings(monkeypatch, capsys):
    ok = doctor.Check("ok", "x", "fine")
    warn = doctor.Check("warn", "y", "meh")
    for name, value in {
        "check_claude": lambda: warn,
        "check_sessions_dir": lambda: ok,
        "check_sock_dir": lambda: ok,
        "check_codex": lambda: ok,
        "check_hooks": lambda home: ok,
        "check_trust": lambda home: ok,
        "check_skill": lambda home: warn,
        "check_tmux": lambda: warn,
        "self_test": lambda: ok,
        "check_live_bridges": lambda: [ok],
    }.items():
        monkeypatch.setattr(doctor, name, value)
    assert doctor.run(codex_home=None) == 0
    out = capsys.readouterr().out
    assert "warn y: meh" in out and "ok  x: fine" in out
