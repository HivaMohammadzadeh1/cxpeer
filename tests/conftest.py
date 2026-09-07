"""Isolation fixtures. No test may touch the real Claude registry, socket dir, cxpeer home, or codex."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Point every cxpeer path at tmp dirs. Socket dir is short because UDS paths cap at ~104 bytes."""
    sessions = tmp_path / "sessions"
    home = tmp_path / "cxhome"
    sessions.mkdir(mode=0o700)
    home.mkdir(mode=0o700)
    socks = Path(f"/tmp/cxpeer-test-{os.getpid()}-{tmp_path.name[-6:]}")
    socks.mkdir(mode=0o700, exist_ok=True)
    monkeypatch.setenv("CXPEER_CLAUDE_SESSIONS_DIR", str(sessions))
    monkeypatch.setenv("CXPEER_SOCK_DIR", str(socks))
    monkeypatch.setenv("CXPEER_HOME", str(home))
    yield {"sessions": sessions, "socks": socks, "home": home}
    for p in socks.iterdir():
        p.unlink(missing_ok=True)
    socks.rmdir()


@pytest.fixture
def fake_codex(isolated_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in `codex` that appends its argv as a JSON line to $CXPEER_HOME/codex_calls.jsonl."""
    home = isolated_env["home"]
    calls = home / "codex_calls.jsonl"
    script = home / "fake-codex"
    script.write_text(
        "#!/bin/sh\n"
        f"python3 -c 'import json,sys; open(\"{calls}\",\"a\").write(json.dumps(sys.argv[1:])+\"\\n\")' \"$@\"\n"
        "echo \"Queued message fake for thread $3.\"\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("CXPEER_CODEX_BIN", str(script))
    return calls


def read_codex_calls(calls: Path) -> list[list[str]]:
    if not calls.exists():
        return []
    return [json.loads(line) for line in calls.read_text().splitlines() if line.strip()]
