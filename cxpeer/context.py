"""Context meter: how big each peer's prompt is, read from the transcripts both tools already write.

Claude Code appends a usage block to every assistant message in
~/.claude/projects/<cwd with non-alphanumerics as dashes>/<sessionId>.jsonl; Codex writes
token_count events into ~/.codex/sessions/YYYY/MM/DD/rollout-<stamp>-<thread>.jsonl. Only the
tail of each file is read, so a 30 MB transcript costs the same as a small one.
"""

from __future__ import annotations

import glob
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

TAIL_BYTES = 512_000
WARN_SHARE = 0.7  # flag a peer whose prompt fills this much of its window
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")


@dataclass(frozen=True)
class ContextInfo:
    tokens: int          # prompt size of the last turn: fresh plus cached input tokens
    window: int | None   # model context window when the transcript states it (Codex does)
    turns: int           # assistant turns seen in the tail that was read
    source: str          # transcript path

    @property
    def share(self) -> float | None:
        return self.tokens / self.window if self.window else None


def _tail_lines(path: Path) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as fh:
        fh.seek(max(0, size - TAIL_BYTES))
        lines = fh.read().decode("utf-8", "ignore").splitlines()
    if size > TAIL_BYTES and lines:
        lines = lines[1:]  # the first line of a mid-file read is cut
    return lines


def _records(lines: Iterable[str]) -> Iterator[dict]:
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            yield rec


def claude_transcript(cwd: str, session_id: str, config_home: Path | None = None) -> Path | None:
    home = config_home or Path.home() / ".claude"
    path = home / "projects" / _NON_ALNUM.sub("-", cwd) / f"{session_id}.jsonl"
    return path if path.is_file() else None


def claude_context(cwd: str, session_id: str, config_home: Path | None = None) -> ContextInfo | None:
    path = claude_transcript(cwd, session_id, config_home)
    if path is None:
        return None
    last, turns = None, 0
    for rec in _records(_tail_lines(path)):
        if rec.get("type") != "assistant":
            continue
        usage = (rec.get("message") or {}).get("usage") or {}
        total = sum(int(usage.get(k) or 0) for k in
                    ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        if total:
            last, turns = total, turns + 1
    if last is None:
        return None
    return ContextInfo(tokens=last, window=None, turns=turns, source=str(path))


def codex_rollout(thread: str, codex_home: Path | None = None) -> Path | None:
    homes = [codex_home] if codex_home else [Path(p) for p in sorted(glob.glob(str(Path.home() / ".codex*")))]
    for home in homes:
        hits = sorted(glob.glob(str(home / "sessions" / "*" / "*" / "*" / f"rollout-*-{thread}.jsonl")))
        if hits:
            return Path(hits[-1])
    return None


def codex_context(thread: str, codex_home: Path | None = None) -> ContextInfo | None:
    path = codex_rollout(thread, codex_home)
    if path is None:
        return None
    last, window, turns = None, None, 0
    for rec in _records(_tail_lines(path)):
        payload = rec.get("payload") or {}
        if payload.get("type") != "token_count":
            continue
        info = payload.get("info") or {}
        total = int((info.get("last_token_usage") or {}).get("input_tokens") or 0)  # includes cached
        if total:
            last, turns = total, turns + 1
        window = info.get("model_context_window") or window
    if last is None:
        return None
    return ContextInfo(tokens=last, window=window, turns=turns, source=str(path))


def for_peer(peer, threads_by_sock: dict[str, str]) -> ContextInfo | None:
    """The meter for a registry peer: a bridge's Codex thread when it has one, else the Claude transcript."""
    thread = threads_by_sock.get(peer.sock)
    if thread:
        return codex_context(thread)
    if not peer.session_id:
        return None
    return claude_context(peer.cwd, peer.session_id, Path(peer.home) if peer.home else None)


def fmt(info: ContextInfo | None) -> str:
    """`ctx=23k/258k (9%)` for Codex, `ctx=161k` for Claude, `ctx=?` when there is no transcript yet."""
    if info is None:
        return "ctx=?"
    text = f"ctx={round(info.tokens / 1000)}k"
    if info.window:
        text += f"/{round(info.window / 1000)}k ({int(info.tokens * 100 / info.window)}%)"
    if info.share is not None and info.share >= WARN_SHARE:
        text += " !"
    return text
