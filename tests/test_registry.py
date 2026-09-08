"""Registry tests: record + key file layout, liveness, lookup. Never touches the real registry."""

from __future__ import annotations

import hashlib
import json
import os
import stat

import pytest

from cxpeer import registry
from cxpeer.paths import sessions_dir


def test_register_writes_record_and_key(isolated_env):
    pid = os.getpid()
    sock = registry.sock_path_for(pid)
    registry.register(pid, "codex-proj-ab", "/work/proj", sock, "a" * 32)

    record_path = sessions_dir() / f"{pid}.json"
    assert record_path.exists()
    record = json.loads(record_path.read_text())
    assert record["name"] == "codex-proj-ab"
    assert record["cwd"] == "/work/proj"
    assert record["messagingSocketPath"] == sock
    assert record["status"] == "idle"
    assert record["peerProtocol"] == 1
    assert record["pid"] == pid
    assert record["procStart"] == registry.proc_start(pid)

    key_path = sessions_dir() / registry.key_name_for(pid, sock)
    assert key_path.exists()
    key = json.loads(key_path.read_text())
    assert key["peerToken"] == "a" * 32
    assert key["procStart"] == record["procStart"]
    assert key["pidDomain"] == "darwin"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_key_name_hashes_literal_socket_path():
    # The hash input is the exact socket path string, not its /private realpath.
    pid = 4242
    sock = "/tmp/cc-socks/4242.sock"
    expected = f"{pid}.{hashlib.sha256(sock.encode()).hexdigest()}.key"
    assert registry.key_name_for(pid, sock) == expected


def test_list_peers_alive_reflects_procstart(isolated_env):
    live = os.getpid()
    registry.register(live, "codex-live", "/work", registry.sock_path_for(live), "a" * 32)

    # A record whose stored procStart no longer matches the running process is dead.
    stale = 1  # launchd: exists, but we overwrite its stored procStart to force a mismatch.
    registry.register(stale, "codex-stale", "/work", registry.sock_path_for(stale), "b" * 32)
    stale_path = sessions_dir() / f"{stale}.json"
    rec = json.loads(stale_path.read_text())
    rec["procStart"] = "Thu Jan  1 00:00:00 1970"
    stale_path.write_text(json.dumps(rec))

    peers = {p.name: p for p in registry.list_peers()}
    assert peers["codex-live"].alive is True
    assert peers["codex-stale"].alive is False
    assert peers["codex-live"].pid == live
    assert peers["codex-live"].sock == registry.sock_path_for(live)
    assert peers["codex-live"].ref == registry.ref_for(registry.sock_path_for(live))


def test_list_peers_marks_missing_process_dead(isolated_env):
    # A pid with no process is dead even if the record stored a procStart.
    ghost = 2147480000
    registry.register(ghost, "codex-ghost", "/work", registry.sock_path_for(ghost), "c" * 32)
    ghost_path = sessions_dir() / f"{ghost}.json"
    rec = json.loads(ghost_path.read_text())
    rec["procStart"] = "Thu Jan  1 00:00:00 1970"  # non-null, but the pid is gone
    ghost_path.write_text(json.dumps(rec))

    peers = {p.name: p for p in registry.list_peers()}
    assert peers["codex-ghost"].alive is False


def test_token_for_reads_matching_key(isolated_env):
    pid = os.getpid()
    sock = registry.sock_path_for(pid)
    registry.register(pid, "codex-x", "/work", sock, "c" * 32)
    assert registry.token_for(sock) == "c" * 32
    assert registry.token_for("/tmp/cc-socks/999999.sock") is None


def test_resolve_by_name_and_ref(isolated_env):
    pid = os.getpid()
    sock = registry.sock_path_for(pid)
    registry.register(pid, "codex-resolveme", "/work", sock, "d" * 32)
    ref = registry.ref_for(sock)

    assert registry.resolve("codex-resolveme").pid == pid
    assert registry.resolve(ref).pid == pid
    with pytest.raises(LookupError):
        registry.resolve("nope-not-here")


def test_resolve_ignores_dead_peers(isolated_env):
    stale = 1
    registry.register(stale, "codex-dead", "/work", registry.sock_path_for(stale), "e" * 32)
    p = sessions_dir() / f"{stale}.json"
    rec = json.loads(p.read_text())
    rec["procStart"] = "nope"
    p.write_text(json.dumps(rec))
    with pytest.raises(LookupError):
        registry.resolve("codex-dead")


def test_set_status_bumps_timestamps(isolated_env, monkeypatch):
    pid = os.getpid()
    sock = registry.sock_path_for(pid)
    monkeypatch.setattr(registry, "_now_ms", lambda: 1000)
    registry.register(pid, "codex-s", "/work", sock, "f" * 32)
    rec0 = json.loads((sessions_dir() / f"{pid}.json").read_text())
    assert rec0["status"] == "idle"
    assert rec0["statusUpdatedAt"] == 1000

    monkeypatch.setattr(registry, "_now_ms", lambda: 2500)
    registry.set_status(pid, "busy")
    rec1 = json.loads((sessions_dir() / f"{pid}.json").read_text())
    assert rec1["status"] == "busy"
    assert rec1["statusUpdatedAt"] == 2500
    assert rec1["updatedAt"] == 2500


def test_deregister_removes_record_and_key(isolated_env):
    pid = os.getpid()
    sock = registry.sock_path_for(pid)
    registry.register(pid, "codex-d", "/work", sock, "a" * 32)
    assert (sessions_dir() / f"{pid}.json").exists()
    registry.deregister(pid)
    assert not (sessions_dir() / f"{pid}.json").exists()
    assert not (sessions_dir() / registry.key_name_for(pid, sock)).exists()


# --- multiple Claude accounts (CXPEER_CLAUDE_SESSIONS_DIRS) -----------------


def test_register_writes_every_registry_with_identical_tokens(isolated_env, tmp_path, monkeypatch):
    a = tmp_path / "acct-a" / "sessions"
    b = tmp_path / "acct-b" / "sessions"  # neither exists yet: register creates them 0700
    monkeypatch.setenv("CXPEER_CLAUDE_SESSIONS_DIRS", f"{a}:{b}")
    pid = os.getpid()
    sock = registry.sock_path_for(pid)
    registry.register(pid, "codex-multi", "/work", sock, "a" * 32)

    tokens = []
    for d in (a, b):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
        rec = json.loads((d / f"{pid}.json").read_text())
        assert rec["name"] == "codex-multi" and rec["messagingSocketPath"] == sock
        key = json.loads((d / registry.key_name_for(pid, sock)).read_text())
        tokens.append(key["peerToken"])
    assert tokens == ["a" * 32, "a" * 32]


def test_list_and_resolve_see_a_peer_only_in_the_second_registry(isolated_env, tmp_path, monkeypatch):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    pid = os.getpid()
    sock = registry.sock_path_for(pid)
    # Register while only the second account's registry is active.
    monkeypatch.setenv("CXPEER_CLAUDE_SESSIONS_DIRS", str(b))
    registry.register(pid, "codex-in-b", "/work", sock, "b" * 32)
    assert not (a / f"{pid}.json").exists()

    # With both active, the peer from b is listed, resolvable, and its token is found.
    monkeypatch.setenv("CXPEER_CLAUDE_SESSIONS_DIRS", f"{a}:{b}")
    peers = {p.name: p for p in registry.list_peers()}
    assert "codex-in-b" in peers and peers["codex-in-b"].alive is True
    assert registry.resolve("codex-in-b").pid == pid
    assert registry.token_for(sock) == "b" * 32


def test_list_peers_dedups_by_socket_first_registry_wins(isolated_env, tmp_path, monkeypatch):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("CXPEER_CLAUDE_SESSIONS_DIRS", f"{a}:{b}")
    pid = os.getpid()
    sock = registry.sock_path_for(pid)
    registry.register(pid, "codex-dup", "/work", sock, "c" * 32)  # same record in both
    dup = [p for p in registry.list_peers() if p.sock == sock]
    assert len(dup) == 1  # one socket -> one peer


def test_deregister_clears_every_registry(isolated_env, tmp_path, monkeypatch):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("CXPEER_CLAUDE_SESSIONS_DIRS", f"{a}:{b}")
    pid = os.getpid()
    sock = registry.sock_path_for(pid)
    registry.register(pid, "codex-x", "/work", sock, "d" * 32)
    assert (a / f"{pid}.json").exists() and (b / f"{pid}.json").exists()
    registry.deregister(pid)
    for d in (a, b):
        assert not (d / f"{pid}.json").exists()
        assert not list(d.glob(f"{pid}.*.key"))


def test_set_status_updates_every_registry_where_present(isolated_env, tmp_path, monkeypatch):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("CXPEER_CLAUDE_SESSIONS_DIRS", f"{a}:{b}")
    pid = os.getpid()
    sock = registry.sock_path_for(pid)
    registry.register(pid, "codex-s", "/work", sock, "e" * 32)
    registry.set_status(pid, "busy")
    for d in (a, b):
        assert json.loads((d / f"{pid}.json").read_text())["status"] == "busy"


def test_token_for_finds_key_in_any_registry(isolated_env, tmp_path, monkeypatch):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    pid = os.getpid()
    sock = registry.sock_path_for(pid)
    monkeypatch.setenv("CXPEER_CLAUDE_SESSIONS_DIRS", str(a))  # key written only into a
    registry.register(pid, "codex-a", "/work", sock, "f" * 32)
    monkeypatch.setenv("CXPEER_CLAUDE_SESSIONS_DIRS", f"{b}:{a}")  # b first, key is in a
    assert registry.token_for(sock) == "f" * 32


def test_register_skips_readonly_registry_without_raising(isolated_env, tmp_path, monkeypatch, capsys):
    if os.geteuid() == 0:
        pytest.skip("root bypasses directory permissions")
    good = tmp_path / "good"
    readonly = tmp_path / "readonly"
    good.mkdir()
    readonly.mkdir(mode=0o500)  # no write bit: register must skip it, not crash
    monkeypatch.setenv("CXPEER_CLAUDE_SESSIONS_DIRS", f"{good}:{readonly}")
    pid = os.getpid()
    sock = registry.sock_path_for(pid)
    try:
        registry.register(pid, "codex-ro", "/work", sock, "a" * 32)  # must not raise
        assert (good / f"{pid}.json").exists()
        assert not (readonly / f"{pid}.json").exists()
        assert "skipping unwritable registry" in capsys.readouterr().err
    finally:
        readonly.chmod(0o700)  # let tmp_path cleanup remove it
