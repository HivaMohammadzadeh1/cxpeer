"""Narrate the complete Claude-side journey through a real cxpeer session."""

from __future__ import annotations

import argparse
import json
import secrets
import shlex
import subprocess
import sys
import time
from pathlib import Path

from cxpeer import registry, wire

try:  # Support both ``python demo/journey.py`` and imports from the repository root.
    from ._peer import (
        build_frames,
        is_answer,
        is_idle_notice,
        register_fake_peer,
        parse_peer_name,
        parse_tmux_session,
    )
except ImportError:
    from _peer import (
        build_frames,
        is_answer,
        is_idle_notice,
        register_fake_peer,
        parse_peer_name,
        parse_tmux_session,
    )


TASK_ONE = "List the three largest source files under cxpeer/ with their line counts, one line each."
TASK_TWO = 'Run `cxpeer list`, then run `cxpeer send --to claude-journey "hello from inside the Codex sandbox"`, and reply with both exit codes.'


def step_record(
    number: int,
    title: str,
    command: str,
    output: str,
    elapsed: float,
    result: str,
) -> dict:
    """Create the serializable record used for console and Markdown transcripts."""
    return {
        "number": number,
        "title": title,
        "command": command,
        "output": output.rstrip("\n"),
        "elapsed": float(elapsed),
        "result": result,
    }


def format_step_record(record: dict) -> str:
    """Format one numbered journey step for the terminal."""
    output = record["output"] or "(no output)"
    return (
        f"\n=== Step {record['number']}: {record['title']} ===\n"
        f"Command:\n{record['command']}\n"
        f"Output:\n{output}\n"
        f"Elapsed: {record['elapsed']:.2f}s\n"
        f"Result: {record['result']}"
    )


def mark_peer_line(output: str, peer_name: str) -> str:
    """Mark the line for ``peer_name`` while preserving every line of ``cxpeer list``."""
    lines = output.splitlines()
    return "\n".join(
        f">>> {line}" if peer_name in line else f"    {line}"
        for line in lines
    )


def write_markdown(path: Path, records: list[dict]) -> None:
    """Write the run as a Markdown transcript with command and output code fences."""
    parts = ["# cxpeer user journey", ""]
    for record in records:
        output = record["output"] or "(no output)"
        parts.extend(
            [
                f"## Step {record['number']}: {record['title']}",
                "",
                "Command:",
                "```text",
                record["command"],
                "```",
                "",
                "Output:",
                "```text",
                output,
                "```",
                "",
                f"Elapsed: {record['elapsed']:.2f}s",
                f"Result: {record['result']}",
                "",
            ]
        )
    parts.append("## Summary")
    parts.extend(["", "| Step | Result | Seconds |", "| --- | --- | ---: |"])
    parts.extend(
        f"| {r['number']} | {r['result']} | {r['elapsed']:.2f} |"
        for r in records
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n")


def _run_command(argv: list[str], cwd: Path) -> tuple[int, str]:
    try:
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    except OSError as exc:
        return 127, str(exc)
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    return result.returncode, output


def _json_line(frame: dict) -> str:
    return json.dumps(frame, sort_keys=True)


def _wire_command(sock_path: str, token: str, first: dict, second: dict | None = None) -> str:
    auth = {"type": "auth", "token": token}
    lines = [
        f"connection 1 -> {sock_path}",
        _json_line(auth),
        _json_line(first),
    ]
    if second is not None:
        lines.extend(["connection 2 -> " + sock_path, _json_line(auth), _json_line(second)])
    return "\n".join(lines)


def _content(frame: dict) -> str:
    raw = frame.get("message", {}).get("content", "")
    text, _, _ = wire.strip_envelope(raw)
    return text


def _is_relay(frame: dict) -> bool:
    return is_answer(frame) and "hello from inside" in _content(frame)


def _print_record(records: list[dict], record: dict) -> None:
    records.append(record)
    print(format_step_record(record), flush=True)


def _summary(records: list[dict]) -> None:
    print("\nSummary")
    print("| Step | Result | Seconds |")
    print("| --- | --- | ---: |")
    for record in records:
        print(f"| {record['number']} | {record['result']} | {record['elapsed']:.2f} |")


def run_journey(keep: bool, timeout: float, record_path: Path | None, repo_root: Path) -> int:
    records: list[dict] = []
    fake = register_fake_peer("claude-journey", str(repo_root))
    session: str | None = None
    peer_name: str | None = None
    started = time.monotonic()
    step4_ok = False
    step5_ok = False

    try:
        step_started = time.monotonic()
        code, output = _run_command(["cxpeer", "doctor"], repo_root)
        _print_record(records, step_record(1, "Check the install", "cxpeer doctor", output, time.monotonic() - step_started,
                                           "ok" if code == 0 else "failed"))

        peer_name = f"codex-journey-{secrets.token_hex(2)}"
        spawn_command = [
            "cxpeer", "spawn", "codex", "--cwd", str(repo_root), "--peer-name", peer_name,
            "--prompt", "Say READY and nothing else.", "--wait", "--timeout", str(max(1, int(timeout))),
        ]
        step_started = time.monotonic()
        code, output = _run_command(spawn_command, repo_root)
        try:
            session = parse_tmux_session(output)
            peer_name = parse_peer_name(output)
        except ValueError as exc:
            output = f"{output}\n{exc}" if output else str(exc)
            code = code or 1
        _print_record(records, step_record(2, "Start a Codex peer from a Claude session",
                                           shlex.join(spawn_command), output, time.monotonic() - step_started,
                                           "ok" if code == 0 else "failed"))

        step_started = time.monotonic()
        code, list_output = _run_command(["cxpeer", "list"], repo_root)
        marked = mark_peer_line(list_output, peer_name) if peer_name else list_output
        list_ok = code == 0 and bool(peer_name) and peer_name in list_output
        _print_record(records, step_record(3, "Claude sees the new peer", "cxpeer list", marked,
                                           time.monotonic() - step_started, "ok" if list_ok else "failed"))

        if code == 0 and peer_name:
            receiver = registry.resolve(peer_name)
            receiver_token = registry.token_for(receiver.sock)
            if receiver_token:
                own_address = fake.address

                step_started = time.monotonic()
                request, idle = build_frames(TASK_ONE, own_address, from_name="claude-journey")
                frame_command = _wire_command(receiver.sock, receiver_token, request, idle)
                frame_start = fake.peer.count()
                wire.send_frames(receiver.sock, receiver_token, [request], timeout=5.0)
                wire.send_frames(receiver.sock, receiver_token, [idle], timeout=5.0)
                wait_started = time.monotonic()
                answer_frames = fake.peer.wait_for(is_answer, timeout, frame_start)
                answer_elapsed = time.monotonic() - wait_started
                idle_frames = fake.peer.wait_for(is_idle_notice, max(0.0, timeout - answer_elapsed), frame_start)
                idle_elapsed = time.monotonic() - wait_started
                answer_text = _content(answer_frames[0]) if answer_frames else "(no answer frame)"
                notice_detail = idle_frames[0].get("detail", "(no idle notice)") if idle_frames else "(no idle notice)"
                step4_ok = bool(answer_frames and idle_frames)
                _print_record(records, step_record(
                    4, "Claude gives Codex a real task", frame_command,
                    f"answer (+{answer_elapsed:.2f}s): {answer_text}\n"
                    f"peer_idle_notice (+{idle_elapsed:.2f}s): {notice_detail}",
                    time.monotonic() - step_started, "ok" if step4_ok else "failed",
                ))

                step_started = time.monotonic()
                second_request, _ = build_frames(TASK_TWO, own_address, from_name="claude-journey")
                frame_command = _wire_command(receiver.sock, receiver_token, second_request)
                frame_start = fake.peer.count()
                wire.send_frames(receiver.sock, receiver_token, [second_request], timeout=5.0)
                wait_started = time.monotonic()
                relay_frames = fake.peer.wait_for(_is_relay, timeout, frame_start)
                relay_elapsed = time.monotonic() - wait_started
                answer_frames = fake.peer.wait_for(
                    lambda frame: is_answer(frame) and not _is_relay(frame),
                    max(0.0, timeout - relay_elapsed), frame_start,
                )
                answer_elapsed = time.monotonic() - wait_started
                relay_text = _content(relay_frames[0]) if relay_frames else "(no relayed message)"
                answer_text = _content(answer_frames[0]) if answer_frames else "(no answer frame)"
                step5_ok = bool(relay_frames and answer_frames)
                _print_record(records, step_record(
                    5, "Codex reaches out on its own", frame_command,
                    f"relayed message (+{relay_elapsed:.2f}s): {relay_text}\n"
                    f"answer (+{answer_elapsed:.2f}s): {answer_text}",
                    time.monotonic() - step_started, "ok" if step5_ok else "failed",
                ))
            else:
                _print_record(records, step_record(4, "Claude gives Codex a real task", "(token unavailable)",
                                                   "could not read the Codex peer token", 0.0, "failed"))
                _print_record(records, step_record(5, "Codex reaches out on its own", "(not run)",
                                                   "step 4 did not start", 0.0, "failed"))
        else:
            _print_record(records, step_record(4, "Claude gives Codex a real task", "(not run)",
                                               "step 2 did not start a peer", 0.0, "failed"))
            _print_record(records, step_record(5, "Codex reaches out on its own", "(not run)",
                                               "step 4 did not start", 0.0, "failed"))
    except (LookupError, OSError, RuntimeError) as exc:
        _print_record(records, step_record(4, "Claude gives Codex a real task", "(wire exchange failed)", str(exc),
                                           0.0, "failed"))
        _print_record(records, step_record(5, "Codex reaches out on its own", "(not run)",
                                           "wire exchange failed", 0.0, "failed"))
    finally:
        step_started = time.monotonic()
        teardown_command: list[str] = []
        teardown_output: list[str] = []
        kill_ok = True
        if session and not keep:
            teardown_command.append(shlex.join(["tmux", "kill-session", "-t", session]))
            kill = subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True, text=True)
            kill_ok = kill.returncode == 0
            if kill.stdout or kill.stderr:
                teardown_output.append("\n".join(part for part in (kill.stdout, kill.stderr) if part))
        elif session:
            teardown_command.append(f"(kept tmux session {session})")
        else:
            teardown_command.append("(no tmux session to kill)")
        teardown_command.append("cxpeer status")
        status_code, status_output = _run_command(["cxpeer", "status"], repo_root)
        teardown_output.append(status_output)
        fake.close()
        teardown_command.append("deregister claude-journey")
        teardown_output.append("deregistered claude-journey")
        _print_record(records, step_record(7, "Tear down", "\n".join(teardown_command), "\n".join(teardown_output),
                                           time.monotonic() - step_started,
                                           "ok" if kill_ok and status_code == 0 else "failed"))

    _summary(records)
    if record_path:
        try:
            write_markdown(record_path, records)
        except OSError as exc:
            print(f"could not write transcript {record_path}: {exc}", file=sys.stderr)
    print(f"Total elapsed: {time.monotonic() - started:.2f}s")
    return 0 if step4_ok and step5_ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Narrate a complete cxpeer user journey")
    parser.add_argument("--keep", action="store_true", help="leave the spawned tmux session running")
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for each peer response")
    parser.add_argument("--record", type=Path, help="write a Markdown transcript to this path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_journey(args.keep, args.timeout, args.record, Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    raise SystemExit(main())
