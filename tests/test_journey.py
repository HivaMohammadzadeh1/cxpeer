from __future__ import annotations

from demo.journey import format_step_record, mark_peer_line, step_record, write_markdown


def test_step_record_formatting(isolated_env):
    record = step_record(2, "Start peer", "cxpeer spawn codex", "peer codex-journey-ab12 is up", 1.25, "ok")
    rendered = format_step_record(record)
    assert "=== Step 2: Start peer ===" in rendered
    assert "Command:\ncxpeer spawn codex" in rendered
    assert "Elapsed: 1.25s" in rendered
    assert "Result: ok" in rendered


def test_markdown_transcript_writer(tmp_path, isolated_env):
    records = [step_record(1, "Check install", "cxpeer doctor", "doctor output", 0.5, "ok")]
    path = tmp_path / "docs" / "user-journey.md"
    write_markdown(path, records)
    text = path.read_text()
    assert "## Step 1: Check install" in text
    assert "```text\ncxpeer doctor\n```" in text
    assert "```text\ndoctor output\n```" in text
    assert "| 1 | ok | 0.50 |" in text


def test_peer_line_marker_marks_only_new_peer(isolated_env):
    output = "claude-journey [abc123]  idle  /tmp\ncodex-journey-ab12 [def456]  idle  /repo\n"
    marked = mark_peer_line(output, "codex-journey-ab12")
    assert ">>> codex-journey-ab12 [def456]" in marked
    assert "    claude-journey [abc123]" in marked
