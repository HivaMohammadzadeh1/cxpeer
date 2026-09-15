"""The context meter reads real transcript shapes from the tail of each file."""

from __future__ import annotations

import json
from pathlib import Path

from cxpeer import context, registry


def claude_line(total: int) -> str:
    return json.dumps({"type": "assistant", "message": {"usage": {
        "input_tokens": 10, "cache_read_input_tokens": total - 10, "cache_creation_input_tokens": 0}}})


def codex_line(input_tokens: int, window: int = 258400) -> str:
    return json.dumps({"timestamp": "t", "type": "event_msg", "payload": {"type": "token_count", "info": {
        "last_token_usage": {"input_tokens": input_tokens, "cached_input_tokens": input_tokens - 100, "output_tokens": 7},
        "model_context_window": window}}})


def test_claude_context_reads_the_last_assistant_usage(tmp_path):
    home = tmp_path / "claude"
    t = home / "projects" / "-Users-me-proj-a-b" / "sid-1.jsonl"
    t.parent.mkdir(parents=True)
    t.write_text("\n".join([json.dumps({"type": "user"}), claude_line(40_000), "not json", claude_line(59_254)]) + "\n")
    info = context.claude_context("/Users/me/proj/a.b", "sid-1", home)
    assert info == context.ContextInfo(tokens=59_254, window=None, turns=2, source=str(t))
    assert context.fmt(info) == "ctx=59k"


def test_claude_context_without_a_transcript_is_none(tmp_path):
    assert context.claude_context("/Users/me/proj", "nope", tmp_path) is None
    assert context.fmt(None) == "ctx=?"


def test_codex_context_finds_the_rollout_by_thread_and_reads_the_window(tmp_path):
    home = tmp_path / "codex"
    day = home / "sessions" / "2026" / "09" / "15"
    day.mkdir(parents=True)
    thread = "01a08e06-c10e-7842-80a7-718d0b0d7488"
    r = day / f"rollout-2026-09-15T09-00-00-{thread}.jsonl"
    r.write_text("\n".join([json.dumps({"type": "session_meta"}), codex_line(15_163), codex_line(22_396)]) + "\n")
    (day / "rollout-2026-09-15T09-00-01-other.jsonl").write_text(codex_line(999) + "\n")
    info = context.codex_context(thread, home)
    assert info == context.ContextInfo(tokens=22_396, window=258_400, turns=2, source=str(r))
    assert context.fmt(info) == "ctx=22k/258k (8%)"


def test_fmt_flags_a_nearly_full_window():
    info = context.ContextInfo(tokens=190_000, window=258_400, turns=1, source="x")
    assert context.fmt(info) == "ctx=190k/258k (73%) !"


def test_tail_read_skips_the_cut_first_line(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "TAIL_BYTES", 400)
    home = tmp_path / "claude"
    t = home / "projects" / "-p" / "s.jsonl"
    t.parent.mkdir(parents=True)
    t.write_text("\n".join([claude_line(1_000)] * 5 + [claude_line(7_000)]) + "\n")
    info = context.claude_context("/p", "s", home)
    assert info is not None and info.tokens == 7_000 and info.turns < 6


def test_for_peer_prefers_the_bridge_thread_over_the_claude_transcript(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(context, "codex_context", lambda thread, home=None: calls.append(("codex", thread)) or None)
    monkeypatch.setattr(context, "claude_context", lambda cwd, sid, home=None: calls.append(("claude", sid, str(home))) or None)
    codex = registry.Peer(name="codex-x", ref="r1", pid=1, sock="/tmp/1.sock", cwd="/p", status="idle", kind="interactive",
                          alive=True, session_id="fake", home="/h")
    claude = registry.Peer(name="c", ref="r2", pid=2, sock="/tmp/2.sock", cwd="/p", status="idle", kind="interactive",
                           alive=True, session_id="sid-2", home="/h")
    context.for_peer(codex, {"/tmp/1.sock": "thread-1"})
    context.for_peer(claude, {"/tmp/1.sock": "thread-1"})
    assert calls == [("codex", "thread-1"), ("claude", "sid-2", "/h")]
