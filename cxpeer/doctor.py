"""`cxpeer doctor`: the one command to run when nothing shows up in ListAgents.

Each check prints one line: `ok  <name>: <detail>`, `warn <name>: <detail>`, or
`FAIL <name>: <detail> -> <what to do>`. `run` returns 0 when nothing failed (warnings
are fine) and 1 otherwise. The last check actually starts a throwaway bridge, pings it,
and makes it queue a message, so a green doctor means the whole path works end to end.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from cxpeer import client, install, paths, wire

# Codex records hook trust under [hooks.state] with snake_case event names.
TRUST_EVENTS = ("session_start", "user_prompt_submit", "stop", "interrupt", "session_end")
CODEX_MIN_VERSION = (0, 153)
CLAUDE_EXPECTED = (2, 1)

_PREFIX = {"ok": "ok  ", "warn": "warn ", "FAIL": "FAIL "}


@dataclass
class Check:
    level: str  # "ok" | "warn" | "FAIL"
    name: str
    detail: str
    fix: str = ""  # only shown for FAIL


def _format(c: Check) -> str:
    line = f"{_PREFIX[c.level]}{c.name}: {c.detail}"
    if c.level == "FAIL" and c.fix:
        line += f" -> {c.fix}"
    return line


def _cmd_output(argv: list[str]) -> str | None:
    """stdout+stderr of a command, or None if it cannot run or exits non-zero."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return (r.stdout + r.stderr).strip()


def _dotted(text: str) -> tuple[int, int, int] | None:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _codex_version(text: str) -> tuple[int, int, int] | None:
    m = re.search(r"codex-cli\s+(\d+)\.(\d+)\.(\d+)", text)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return _dotted(text)  # tolerate a bare "0.153.3"


# ----- individual checks ---------------------------------------------------


def check_claude() -> Check:
    binary = shutil.which("claude")
    if binary is None:
        return Check("warn", "claude", "not found on PATH; a Claude Code session is where peers appear")
    ver = _dotted(_cmd_output([binary, "--version"]) or "")
    if ver is None:
        return Check("warn", "claude", "installed, but `claude --version` did not parse")
    text = ".".join(map(str, ver))
    if ver[:2] != CLAUDE_EXPECTED:
        return Check("warn", "claude", f"version {text}; cxpeer was verified against 2.1.x")
    return Check("ok", "claude", f"version {text}")


def check_sessions_dir() -> list[Check]:
    """One line per Claude account registry cxpeer writes into."""
    out: list[Check] = []
    for d in paths.sessions_dirs():
        if not d.exists():
            out.append(Check("FAIL", "claude-sessions", f"{d} does not exist", "start a Claude Code session once"))
            continue
        mode = stat.S_IMODE(d.stat().st_mode)
        if mode != 0o700:
            out.append(Check("warn", "claude-sessions", f"{d} is mode {oct(mode)}, expected 0700"))
        else:
            out.append(Check("ok", "claude-sessions", f"{d} present (0700)"))
    return out


def check_sock_dir() -> Check:
    d = paths.sock_dir()
    if not d.exists():
        return Check("warn", "socket-dir", f"{d} missing; Claude Code creates it on its first session")
    return Check("ok", "socket-dir", f"{d} present")


def check_codex() -> Check:
    binary = shutil.which("codex")
    if binary is None:
        return Check("FAIL", "codex", "not found on PATH", "install Codex CLI 0.153 or newer")
    out = _cmd_output([binary, "--version"]) or ""
    ver = _codex_version(out)
    if ver is None:
        return Check("FAIL", "codex", f"could not parse a version from {out!r}", "install Codex CLI 0.153 or newer")
    text = ".".join(map(str, ver))
    if ver[:2] < CODEX_MIN_VERSION:
        return Check("FAIL", "codex", f"version {text} is older than 0.153", "upgrade Codex CLI")
    return Check("ok", "codex", f"version {text}")


def _hook_command(data: dict, event: str, name: str) -> str | None:
    for group in data.get("hooks", {}).get(event, []):
        for h in group.get("hooks", []):
            cmd = str(h.get("command", ""))
            if cmd.endswith(f"cxpeer hook {name}"):
                return cmd
    return None


def check_hooks(codex_home: Path) -> Check:
    hooks_path = codex_home / "hooks.json"
    if not hooks_path.exists():
        return Check("FAIL", "codex-hooks", f"{hooks_path} does not exist", "run cxpeer install")
    try:
        data = json.loads(hooks_path.read_text())
    except (OSError, ValueError):
        return Check("FAIL", "codex-hooks", f"{hooks_path} is not readable JSON", "run cxpeer install")

    missing, bad_interp = [], []
    for event, name in install.HOOK_EVENTS.items():
        cmd = _hook_command(data, event, name)
        if cmd is None:
            missing.append(name)
            continue
        parts = shlex.split(cmd)
        if not parts or not os.path.exists(parts[0]):
            bad_interp.append(name)

    if missing:
        return Check("FAIL", "codex-hooks", f"missing cxpeer entries: {', '.join(sorted(missing))}", "run cxpeer install")
    if bad_interp:
        return Check("FAIL", "codex-hooks",
                     f"interpreter not on disk for: {', '.join(sorted(bad_interp))}", "run cxpeer install")
    return Check("ok", "codex-hooks", "all five cxpeer hooks present with a valid interpreter")


def check_trust(codex_home: Path) -> Check:
    config = codex_home / "config.toml"
    hooks_path = codex_home / "hooks.json"
    fix = "start codex once and choose Trust all and continue"
    try:
        text = config.read_text()
    except OSError:
        return Check("FAIL", "codex-trust", f"{config} has no [hooks.state] trust entries", fix)
    missing = [e for e in TRUST_EVENTS
               if not re.search(re.escape(str(hooks_path)) + ":" + e + r":\d+:\d+", text)]
    if missing:
        return Check("FAIL", "codex-trust", f"hooks not trusted for: {', '.join(missing)}", fix)
    return Check("ok", "codex-trust", "all five hooks trusted in config.toml")


def check_skill(codex_home: Path) -> Check:
    skill_path = codex_home / "skills" / "cxpeer" / "SKILL.md"
    if not skill_path.exists():
        return Check("warn", "codex-skill", f"{skill_path} not installed; run cxpeer install")
    try:
        if skill_path.read_text() != install.skill_text():
            return Check("warn", "codex-skill", "installed skill is stale; run cxpeer install")
    except OSError as exc:
        return Check("warn", "codex-skill", f"could not compare skill ({exc}); run cxpeer install")
    return Check("ok", "codex-skill", f"{skill_path} up to date")


def check_tmux() -> Check:
    if shutil.which("tmux") is None:
        return Check("warn", "tmux", "not on PATH; only cxpeer spawn needs it")
    return Check("ok", "tmux", "present")


# ----- self test -----------------------------------------------------------


class _SelfTestError(Exception):
    pass


def _write_fake_codex(directory: Path, calls: Path) -> Path:
    script = directory / "fake-codex"
    script.write_text(
        "#!/bin/sh\n"
        f"printf 'CALL %s\\n' \"$1\" >> {shlex.quote(str(calls))}\n"
        "echo 'doctor fake codex'\n"
        "exit 0\n"
    )
    script.chmod(0o755)
    return script


def _wait_for_json(path: Path, deadline: float) -> dict | None:
    while time.monotonic() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except (OSError, ValueError):
                pass
        time.sleep(0.05)
    return None


def _wait_for_queue_call(calls: Path, deadline: float) -> bool:
    while time.monotonic() < deadline:
        try:
            if "CALL queue" in calls.read_text():
                return True
        except OSError:
            pass
        time.sleep(0.05)
    return False


def self_test() -> Check:
    """Start a throwaway bridge in an isolated environment, ping it, make it queue a
    message via a fake codex, and shut it down. Proves the whole path in-process."""
    tmp = Path(tempfile.mkdtemp(prefix="cxpeer-doctor-"))
    socks = Path(f"/tmp/cxpeer-doc-{secrets.token_hex(3)}")  # short: UDS paths cap near 104 bytes
    thread = f"doctor-{secrets.token_hex(4)}"
    proc = None
    step = "set up"
    started = time.monotonic()
    try:
        sessions, home, outbox = tmp / "sessions", tmp / "home", tmp / "outbox"
        for d in (sessions, home, outbox, socks):
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
        calls = tmp / "codex_calls.txt"
        fake = _write_fake_codex(tmp, calls)
        env = {
            **os.environ,
            "CXPEER_CLAUDE_SESSIONS_DIR": str(sessions),
            "CXPEER_SOCK_DIR": str(socks),
            "CXPEER_HOME": str(home),
            "CXPEER_OUTBOX_ROOT": str(outbox),
            "CXPEER_CODEX_BIN": str(fake),
        }

        step = "spawn bridge"
        proc = subprocess.Popen(
            [sys.executable, "-m", "cxpeer", "bridge", "--thread", thread, "--cwd", str(tmp),
             "--watch-pid", str(os.getpid())],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        step = "wait for state file"
        state = _wait_for_json(home / "bridges" / f"{thread}.json", time.monotonic() + 5.0)
        if state is None:
            raise _SelfTestError("bridge never wrote its state file")
        sock, token = state["sock"], state["token"]

        step = "ping"
        if not client._request(sock, token, {"type": "cxpeer.ping"}).get("ok"):
            raise _SelfTestError("bridge did not answer ping with ok")

        step = "queue a message"
        wire.send_frames(sock, token, [wire.user_frame("doctor self-test", "uds:" + sock)])
        if not _wait_for_queue_call(calls, time.monotonic() + 5.0):
            raise _SelfTestError("bridge did not call codex queue")

        step = "shutdown"
        try:
            client._request(sock, token, {"type": "cxpeer.shutdown"})
        except OSError:
            pass
        proc.wait(timeout=5)

        elapsed = int((time.monotonic() - started) * 1000)
        return Check("ok", "self-test", f"bridge answered ping and queued a message in {elapsed} ms")
    except (_SelfTestError, OSError, subprocess.SubprocessError, KeyError) as exc:
        return Check("FAIL", "self-test", f"failed at step '{step}': {exc}",
                     "check ~/.cxpeer/logs/<thread>.log")
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
        for p in socks.glob("*"):
            p.unlink(missing_ok=True)
        try:
            socks.rmdir()
        except OSError:
            pass


def check_live_bridges() -> list[Check]:
    bridges = client.list_bridges()
    if not bridges:
        return [Check("ok", "live-bridges", "none registered (start a Codex session to create one)")]
    out = []
    for b in bridges:
        try:
            info = client.ping(b)
            out.append(Check("ok", "live-bridges",
                             f"{b.name} (thread {b.thread}): status={info.get('status', '?')} "
                             f"pending={info.get('pending', '?')}"))
        except (OSError, RuntimeError) as exc:
            out.append(Check("warn", "live-bridges", f"{b.name} (thread {b.thread}) unreachable: {exc}"))
    return out


def run(codex_home: Path | None = None) -> int:
    home = codex_home or install.codex_home()
    checks: list[Check] = []
    for result in (
        check_claude(),
        check_sessions_dir(),
        check_sock_dir(),
        check_codex(),
        check_hooks(home),
        check_trust(home),
        check_skill(home),
        check_tmux(),
        self_test(),
    ):
        checks.extend(result if isinstance(result, list) else [result])
    checks.extend(check_live_bridges())

    failed = 0
    for c in checks:
        print(_format(c))
        if c.level == "FAIL":
            failed += 1
    return 1 if failed else 0
