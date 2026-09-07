from __future__ import annotations

from datetime import datetime, timezone

from demo.demo import build_frames, parse_peer_name, transcript_line


def test_transcript_line_formatting(isolated_env):
    now = datetime(2026, 9, 7, 12, 34, 56, tzinfo=timezone.utc)
    assert transcript_line("registered", "name=claude-demo", now) == (
        "[2026-09-07T12:34:56+00:00] registered name=claude-demo"
    )


def test_build_frames_uses_cxpeer_wire_contract(isolated_env):
    user, idle = build_frames("Say PONG.", "uds:/tmp/claude.sock", "msg-1", "idle-1")
    assert user == {
        "msgV": 1,
        "msg_id": "msg-1",
        "type": "user",
        "message": {
            "role": "user",
            "content": (
                '<cross-session-message from="uds:/tmp/claude.sock" from-name="claude-demo" '
                'from-mode="prompting">\nSay PONG.\n</cross-session-message>'
            ),
        },
        "priority": "next",
        "from": "uds:/tmp/claude.sock",
    }
    assert idle == {
        "type": "control",
        "action": "notify_when_idle",
        "from": "uds:/tmp/claude.sock",
        "from_mode": "prompting",
        "msgV": 1,
        "msg_id": "idle-1",
    }


def test_parse_peer_name_from_spawn_output(isolated_env):
    output = (
        "started cx-codex-demo-ab12 (codex) in /tmp/project; attach with: tmux attach -t cx-codex-demo-ab12\n"
        "peer codex-demo-ab12 is up (thread 1234); attach with: tmux attach -t cx-codex-demo-ab12\n"
    )
    assert parse_peer_name(output) == "codex-demo-ab12"
