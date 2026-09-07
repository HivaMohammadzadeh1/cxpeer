"""Run a small, real-process cxpeer conversation without a Claude session."""

from __future__ import annotations

import argparse
import secrets
import subprocess
import sys
import time
from pathlib import Path

from cxpeer import registry, wire

try:  # Support both ``python demo/demo.py`` and imports from the repository root.
    from ._peer import DemoPeer, build_frames, is_answer, is_idle_notice, parse_peer_name, parse_tmux_session, register_fake_peer, transcript_line
except ImportError:
    from _peer import DemoPeer, build_frames, is_answer, is_idle_notice, parse_peer_name, parse_tmux_session, register_fake_peer, transcript_line


def _run_status() -> None:
    subprocess.run(["cxpeer", "status"], check=False)


def _kill_session(session: str | None) -> None:
    if session:
        subprocess.run(["tmux", "kill-session", "-t", session], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_demo(message: str, keep: bool, timeout: float, repo_root: Path) -> int:
    fake = register_fake_peer("claude-demo", str(repo_root))
    pid = fake.pid
    sock_path = fake.sock_path
    peer = fake.peer
    session: str | None = None
    answer_arrived = False
    started = time.monotonic()

    try:
        print(transcript_line("registered", f"name=claude-demo address=uds:{sock_path}"), flush=True)

        peer_name = f"codex-demo-{secrets.token_hex(2)}"
        spawn_timeout = max(1, int(timeout))
        command = [
            "cxpeer", "spawn", "codex", "--cwd", str(repo_root),
            "--peer-name", peer_name, "--prompt", "Say READY and nothing else.",
            "--wait", "--timeout", str(spawn_timeout),
        ]
        try:
            result = subprocess.run(
                command, cwd=repo_root, capture_output=True, text=True,
                timeout=timeout + 10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(transcript_line("spawn failed", str(exc)), flush=True)
            return 1

        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        try:
            session = parse_tmux_session(output)
            registered_name = parse_peer_name(output)
        except ValueError as exc:
            print(transcript_line("spawn failed", str(exc)), flush=True)
            if output:
                print(output, file=sys.stderr, end="")
            return 1
        if result.returncode != 0:
            print(transcript_line("spawn failed", f"exit={result.returncode}"), flush=True)
            if output:
                print(output, file=sys.stderr, end="")
            return 1
        print(transcript_line("peer up", f"name={registered_name} tmux={session}"), flush=True)

        own_address = f"uds:{sock_path}"
        request, idle = build_frames(message, own_address)
        receiver = registry.resolve(registered_name)
        receiver_token = registry.token_for(receiver.sock)
        if not receiver_token:
            raise RuntimeError(f"could not read token for peer {registered_name!r}")
        wire.send_frames(receiver.sock, receiver_token, [request], timeout=5.0)
        print(transcript_line("sent", f"message={message!r}"), flush=True)
        wire.send_frames(receiver.sock, receiver_token, [idle], timeout=5.0)

        deadline = time.monotonic() + timeout
        answer_frames: list[dict] = []
        idle_frames: list[dict] = []
        while time.monotonic() < deadline and (not answer_frames or not idle_frames):
            remaining = max(0.0, deadline - time.monotonic())
            if not answer_frames:
                answer_frames = peer.wait_for(is_answer, min(remaining, 0.5))
            if not idle_frames:
                idle_frames = peer.wait_for(is_idle_notice, min(remaining, 0.5))

        if answer_frames:
            content = answer_frames[0]["message"]["content"]
            answer_text, _, _ = wire.strip_envelope(content)
            print(transcript_line("answer", answer_text), flush=True)
            answer_arrived = True
        if idle_frames:
            print(transcript_line("idle notice", idle_frames[0].get("detail", "")), flush=True)
        return 0 if answer_arrived else 1
    finally:
        _run_status()
        if not keep:
            _kill_session(session)
        fake.close()
        print(transcript_line("elapsed", f"{time.monotonic() - started:.1f}s"), flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a cxpeer demo with a fake Claude peer")
    parser.add_argument("--message", default="Reply with one sentence about what you can see in this directory.")
    parser.add_argument("--keep", action="store_true", help="leave the spawned tmux session running")
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for spawn and the answer")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_demo(args.message, args.keep, args.timeout, Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    raise SystemExit(main())
