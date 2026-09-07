"""Filesystem locations. Every path has an env override so tests never touch real state."""

from __future__ import annotations

import os
from pathlib import Path


def sessions_dir() -> Path:
    """Claude Code's peer registry directory."""
    return Path(os.environ.get("CXPEER_CLAUDE_SESSIONS_DIR") or Path.home() / ".claude" / "sessions")


def sock_dir() -> Path:
    """Directory Claude Code expects peer sockets in."""
    return Path(os.environ.get("CXPEER_SOCK_DIR") or "/tmp/cc-socks")


def state_dir() -> Path:
    """cxpeer's own state: bridge records and logs."""
    return Path(os.environ.get("CXPEER_HOME") or Path.home() / ".cxpeer")


def bridges_dir() -> Path:
    return state_dir() / "bridges"


def logs_dir() -> Path:
    return state_dir() / "logs"


def outbox_root() -> Path:
    """Where sandboxed Codex shells drop send requests for their bridge; /tmp is writable there."""
    return Path(os.environ.get("CXPEER_OUTBOX_ROOT") or f"/tmp/cxpeer-{os.getuid()}")


def codex_bin() -> str:
    """The codex executable. Tests point this at a fake script."""
    return os.environ.get("CXPEER_CODEX_BIN") or "codex"
