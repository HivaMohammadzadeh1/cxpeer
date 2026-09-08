"""Narrate the complete Claude-side journey through a real cxpeer session.

Run from a checkout with cxpeer installed. On a terminal it narrates: typed commands, a live
timer while Codex works, output revealed line by line. With --plain (or when stdout is not a
terminal) it prints each step as one block. --record writes a Markdown transcript.
"""

from __future__ import annotations

import argparse
import json
import secrets
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

from cxpeer import registry, wire

try:  # Support both ``python demo/journey.py`` and imports from the repository root.
    from ._peer import build_frames, is_answer, is_idle_notice, register_fake_peer, parse_peer_name, parse_tmux_session
except ImportError:
    from _peer import build_frames, is_answer, is_idle_notice, register_fake_peer, parse_peer_name, parse_tmux_session


TASK_ONE = "List the three largest source files under cxpeer/ with their line counts, one line each."
TASK_TWO = 'Run `cxpeer send --to claude-journey "hello from inside the Codex sandbox"` and reply with its exit code.'
TOTAL_STEPS = 6  # 7 with --second-registry


# ----- records and transcript (pure, unit-tested) -----

def step_record(number: int, title: str, command: str, output: str, elapsed: float, result: str) -> dict:
    """Create the serializable record used for console and Markdown transcripts."""
    return {"number": number, "title": title, "command": command, "output": output.rstrip("\n"),
            "elapsed": float(elapsed), "result": result}


def format_step_record(record: dict) -> str:
    """Format one numbered journey step for a plain terminal."""
    output = record["output"] or "(no output)"
    return (f"\n=== Step {record['number']}: {record['title']} ===\n"
            f"Command:\n{record['command']}\nOutput:\n{output}\n"
            f"Elapsed: {record['elapsed']:.2f}s\nResult: {record['result']}")


def mark_peer_line(output: str, peer_name: str) -> str:
    """Mark the line for ``peer_name`` while preserving every line of ``cxpeer list``."""
    return "\n".join(f">>> {line}" if peer_name in line else f"    {line}" for line in output.splitlines())


def focus_peer_lines(output: str, names: tuple[str, ...]) -> str:
    """Keep only the lines that mention one of ``names``; other sessions on the machine are not the story."""
    lines = output.splitlines()
    kept = [line for line in lines if any(name in line for name in names)]
    omitted = len(lines) - len(kept)
    if omitted:
        kept.append(f"({omitted} other live session{'s' if omitted != 1 else ''} on this machine omitted)")
    return "\n".join(kept)


def focus_doctor_output(output: str, names: tuple[str, ...]) -> str:
    """Hide live-bridges lines for sessions that are not part of the journey."""
    kept, omitted = [], 0
    for line in output.splitlines():
        if "live-bridges:" in line and not any(name in line for name in names):
            omitted += 1
            continue
        kept.append(line)
    if omitted:
        kept.append(f"ok  live-bridges: ({omitted} other bridge{'s' if omitted != 1 else ''} on this machine omitted)")
    return "\n".join(kept)


def write_markdown(path: Path, records: list[dict]) -> None:
    """Write the run as a Markdown transcript with command and output code fences."""
    parts = ["# cxpeer user journey", ""]
    for record in records:
        output = record["output"] or "(no output)"
        parts.extend([f"## Step {record['number']}: {record['title']}", "", "Command:", "```text", record["command"], "```",
                      "", "Output:", "```text", output, "```", "", f"Elapsed: {record['elapsed']:.2f}s",
                      f"Result: {record['result']}", ""])
    parts.extend(["## Summary", "", "| Step | Result | Seconds |", "| --- | --- | ---: |"])
    parts.extend(f"| {r['number']} | {r['result']} | {r['elapsed']:.2f} |" for r in records)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n")


# ----- narration -----

class Narrator:
    """Terminal presentation. Every method is a no-op-ish plain print when colour is off."""

    SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, live: bool, typing_delay: float = 0.014, line_delay: float = 0.035) -> None:
        self.live = live
        self.typing_delay = typing_delay if live else 0.0
        self.line_delay = line_delay if live else 0.0

    def c(self, text: str, *codes: str) -> str:
        if not self.live:
            return text
        table = {"bold": "1", "dim": "2", "cyan": "36", "yellow": "33", "green": "32", "red": "31", "magenta": "35", "blue": "34"}
        return "".join(f"\x1b[{table[k]}m" for k in codes) + text + "\x1b[0m"

    def banner(self, extra: str | None = None) -> None:
        lines = ["cxpeer · a Claude session talks to a Codex session",
                 "left: the Claude side, played by demo/journey.py",
                 "right: the Codex TUI receiving the messages"]
        if extra:
            lines.append(extra)
        width = max(len(l) for l in lines) + 4
        print(self.c("┌" + "─" * width + "┐", "cyan"))
        for i, l in enumerate(lines):
            print(self.c("│  ", "cyan") + self.c(l.ljust(width - 4), "bold" if i == 0 else "dim") + self.c("  │", "cyan"))
        print(self.c("└" + "─" * width + "┘", "cyan"))
        self.pause(1.2)

    total_steps = TOTAL_STEPS

    def step(self, n: int, title: str) -> None:
        print()
        print(self.c(f"── step {n} of {self.total_steps} · {title} ", "bold", "cyan") + self.c("─" * max(4, 70 - len(title)), "cyan"))
        self.pause(0.6)

    def command(self, text: str) -> None:
        sys.stdout.write(self.c("$ ", "dim"))
        sys.stdout.flush()
        for ch in text:
            sys.stdout.write(self.c(ch, "yellow"))
            sys.stdout.flush()
            time.sleep(self.typing_delay)
        print()
        self.pause(0.3)

    def send(self, to: str, text: str) -> None:
        print(self.c("→ ", "magenta") + self.c(f"to {to}: ", "magenta", "bold") + self.c(f'"{text}"', "magenta"))
        self.pause(0.4)

    def note(self, text: str) -> None:
        print(self.c("  " + text, "dim"))
        self.pause(0.3)

    def lines(self, text: str, highlight: str | None = None) -> None:
        for line in text.splitlines():
            if highlight and highlight in line:
                print(self.c("  " + line, "green", "bold"))
            elif line.startswith(("ok ", "ok\t")):
                print("  " + self.c(line[:2], "green") + line[2:])
            elif line.startswith("FAIL"):
                print("  " + self.c(line, "red"))
            else:
                print("  " + line)
            time.sleep(self.line_delay)

    def received(self, label: str, text: str, seconds: float) -> None:
        print(self.c("← ", "green") + self.c(f"{label} ", "green", "bold") + self.c(f"(+{seconds:.1f} s)", "dim"))
        self.lines(text)

    def wait(self, label: str, poll, timeout: float):
        """Call poll(0.2) until it returns a truthy value or timeout; show a live timer meanwhile."""
        started = time.monotonic()
        i = 0
        while True:
            found = poll(0.5)
            elapsed = time.monotonic() - started
            if found or elapsed >= timeout:
                if self.live:
                    sys.stdout.write("\r\x1b[2K")
                    sys.stdout.flush()
                return found
            if self.live:
                sys.stdout.write("\r" + self.c(f"  {self.SPIN[i % len(self.SPIN)]} {label} … {elapsed:5.1f} s", "dim"))
                sys.stdout.flush()
            i += 1

    def result(self, ok: bool, elapsed: float) -> None:
        mark = self.c("✓ ok", "green", "bold") if ok else self.c("✗ failed", "red", "bold")
        print(f"  {mark} " + self.c(f"{elapsed:.1f} s", "dim"))
        self.pause(0.8)

    def summary(self, records: list[dict], total: float) -> None:
        print()
        print(self.c("summary", "bold", "cyan"))
        for r in records:
            mark = self.c("✓", "green") if r["result"] == "ok" else self.c("✗", "red")
            print(f"  {mark} step {r['number']}  {r['title']:<44} {r['elapsed']:6.1f} s")
        print(self.c(f"  total {total:.1f} s", "dim"))

    def pause(self, seconds: float) -> None:
        if self.live:
            time.sleep(seconds)


# ----- the journey -----

def _run_command(argv: list[str], cwd: Path) -> tuple[int, str]:
    try:
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    except OSError as exc:
        return 127, str(exc)
    return result.returncode, "\n".join(part for part in (result.stdout, result.stderr) if part)


def _run_command_with_timer(narrator: Narrator, label: str, argv: list[str], cwd: Path, timeout: float) -> tuple[int, str]:
    box: dict = {}

    def target() -> None:
        box["result"] = _run_command(argv, cwd)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

    def poll(step: float):
        thread.join(step)
        return box.get("result")

    result = narrator.wait(label, poll, timeout + 15)
    return result if result else (124, f"timed out after {timeout + 15:.0f}s")


def _json_line(frame: dict) -> str:
    return json.dumps(frame, sort_keys=True)


def _wire_command(sock_path: str, token: str, first: dict, second: dict | None = None) -> str:
    """The frames as sent, for the transcript. The real token is sent, never printed."""
    auth = {"type": "auth", "token": "<peer token>"}
    lines = [f"connection 1 -> {sock_path}", _json_line(auth), _json_line(first)]
    if second is not None:
        lines.extend([f"connection 2 -> {sock_path}", _json_line(auth), _json_line(second)])
    return "\n".join(lines)


def _content(frame: dict) -> str:
    text, _, _ = wire.strip_envelope(frame.get("message", {}).get("content", ""))
    return text


def _is_relay(frame: dict) -> bool:
    return is_answer(frame) and "hello from inside" in _content(frame)


def run_journey(keep: bool, timeout: float, record_path: Path | None, repo_root: Path, narrator: Narrator,
                codex_home: str | None = None, second_registry: str | None = None) -> int:
    records: list[dict] = []
    narrator.total_steps = TOTAL_STEPS + (1 if second_registry else 0)
    step_no = 0

    def nxt() -> int:
        nonlocal step_no
        step_no += 1
        return step_no
    fake = register_fake_peer("claude-journey", str(repo_root))
    session: str | None = None
    peer_name: str | None = None
    started = time.monotonic()
    step4_ok = step5_ok = False

    def finish(record: dict) -> None:
        records.append(record)
        if narrator.live:
            narrator.result(record["result"] == "ok", record["elapsed"])
        else:
            print(format_step_record(record), flush=True)

    if narrator.live:
        extra = None
        if codex_home or second_registry:
            parts = []
            if codex_home:
                parts.append(f"Codex account: CODEX_HOME={codex_home}")
            if second_registry:
                parts.append(f"second Claude account registry: {second_registry}")
            extra = " · ".join(parts)
        narrator.banner(extra)
    try:
        # 1
        n = nxt()
        narrator.step(n, "Check the install")
        doctor_cmd = ["cxpeer", "doctor"] + (["--codex-home", codex_home] if codex_home else [])
        narrator.command(shlex.join(doctor_cmd))
        t0 = time.monotonic()
        code, output = _run_command(doctor_cmd, repo_root)
        output = focus_doctor_output(output, ("claude-journey", "codex-journey"))
        narrator.lines(output)
        finish(step_record(n, "Check the install", shlex.join(doctor_cmd), output, time.monotonic() - t0, "ok" if code == 0 else "failed"))

        # 2
        n = nxt()
        narrator.step(n, "Start a Codex peer from a Claude session")
        peer_name = f"codex-journey-{secrets.token_hex(2)}"
        home_args = ["--codex-home", codex_home] if codex_home else []
        spawn = ["cxpeer", "spawn", "codex", "--cwd", str(repo_root), "--peer-name", peer_name,
                 "--prompt", "Say READY and nothing else.", "--wait", "--timeout", str(max(1, int(timeout)))] + home_args
        shown = ["cxpeer", "spawn", "codex", "--peer-name", peer_name, "--prompt", "Say READY and nothing else.", "--wait"] + home_args
        narrator.command(shlex.join(shown))
        narrator.note("starts codex in tmux, clicks through its prompts, waits until the peer is registered"
                      + (" (that Codex account's own hooks, queue, and login)" if codex_home else ""))
        t0 = time.monotonic()
        code, output = _run_command_with_timer(narrator, "starting Codex", spawn, repo_root, timeout)
        try:
            session = parse_tmux_session(output)
            peer_name = parse_peer_name(output)
        except ValueError as exc:
            output = f"{output}\n{exc}" if output else str(exc)
            code = code or 1
        narrator.lines(output)
        finish(step_record(n, "Start a Codex peer from a Claude session", shlex.join(spawn), output, time.monotonic() - t0,
                           "ok" if code == 0 else "failed"))

        # 3
        n = nxt()
        narrator.step(n, "Claude sees the new peer")
        narrator.command("cxpeer list")
        narrator.note("the same list a Claude session gets from ListAgents")
        t0 = time.monotonic()
        code, list_output = _run_command(["cxpeer", "list"], repo_root)
        focused = focus_peer_lines(list_output, ("claude-journey", peer_name or "codex-journey"))
        marked = mark_peer_line(focused, peer_name) if peer_name else focused
        narrator.lines(focused, highlight=peer_name)
        list_ok = code == 0 and bool(peer_name) and peer_name in list_output
        finish(step_record(n, "Claude sees the new peer", "cxpeer list", marked, time.monotonic() - t0, "ok" if list_ok else "failed"))

        if second_registry:
            n = nxt()
            narrator.step(n, "A second Claude account sees it too")
            env_cmd = f"CXPEER_CLAUDE_SESSIONS_DIR={shlex.quote(second_registry)} cxpeer list"
            narrator.command(env_cmd)
            narrator.note("the other account's registry, read on its own; the bridge registered there as well")
            t0 = time.monotonic()
            code2, out2 = _run_command(["env", f"CXPEER_CLAUDE_SESSIONS_DIR={second_registry}", "cxpeer", "list"], repo_root)
            focused2 = focus_peer_lines(out2, ("claude-journey", peer_name or "codex-journey"))
            narrator.lines(focused2, highlight=peer_name)
            ok2 = code2 == 0 and bool(peer_name) and peer_name in out2
            finish(step_record(n, "A second Claude account sees it too", env_cmd,
                               mark_peer_line(focused2, peer_name) if peer_name else focused2, time.monotonic() - t0, "ok" if ok2 else "failed"))

        if code == 0 and peer_name:
            receiver = registry.resolve(peer_name)
            receiver_token = registry.token_for(receiver.sock)
            if not receiver_token:
                raise LookupError(f"could not read the token for {peer_name}")
            own_address = fake.address

            # 4
            n = nxt()
            narrator.step(n, "Claude gives Codex a real task")
            request, idle = build_frames(TASK_ONE, own_address, from_name="claude-journey")
            narrator.send(peer_name, TASK_ONE)
            narrator.note("auth line + message on the bridge socket, plus an idle subscription")
            t0 = time.monotonic()
            start_index = fake.peer.count()
            wire.send_frames(receiver.sock, receiver_token, [request], timeout=5.0)
            wire.send_frames(receiver.sock, receiver_token, [idle], timeout=5.0)
            answer = narrator.wait("Codex is working on it", lambda s: fake.peer.wait_for(is_answer, s, start_index), timeout)
            answer_at = time.monotonic() - t0
            answer_text = _content(answer[0]) if answer else "(no answer frame)"
            if answer:
                narrator.received("answer", answer_text, answer_at)
            notice = narrator.wait("waiting for the idle notice", lambda s: fake.peer.wait_for(is_idle_notice, s, start_index),
                                   max(0.0, timeout - answer_at))
            notice_at = time.monotonic() - t0
            notice_detail = notice[0].get("detail", "") if notice else "(no idle notice)"
            if notice:
                narrator.received("idle notice", notice_detail or "(no detail)", notice_at)
            step4_ok = bool(answer and notice)
            finish(step_record(n, "Claude gives Codex a real task", _wire_command(receiver.sock, receiver_token, request, idle),
                               f"answer (+{answer_at:.2f}s): {answer_text}\npeer_idle_notice (+{notice_at:.2f}s): {notice_detail}",
                               time.monotonic() - t0, "ok" if step4_ok else "failed"))

            # 5
            n = nxt()
            narrator.step(n, "Codex reaches out on its own")
            second, _ = build_frames(TASK_TWO, own_address, from_name="claude-journey")
            narrator.send(peer_name, TASK_TWO)
            narrator.note("Codex's sandbox blocks sockets, so its cxpeer send goes through a file outbox the bridge polls")
            t0 = time.monotonic()
            start_index = fake.peer.count()
            wire.send_frames(receiver.sock, receiver_token, [second], timeout=5.0)
            relay = narrator.wait("waiting for Codex to message us", lambda s: fake.peer.wait_for(_is_relay, s, start_index), timeout)
            relay_at = time.monotonic() - t0
            relay_text = _content(relay[0]) if relay else "(no relayed message)"
            if relay:
                narrator.received("from Codex, via cxpeer send", relay_text, relay_at)
            reply = narrator.wait("waiting for Codex's answer",
                                  lambda s: fake.peer.wait_for(lambda f: is_answer(f) and not _is_relay(f), s, start_index),
                                  max(0.0, timeout - relay_at))
            reply_at = time.monotonic() - t0
            reply_text = _content(reply[0]) if reply else "(no answer frame)"
            if reply:
                narrator.received("answer", reply_text, reply_at)
            step5_ok = bool(relay and reply)
            finish(step_record(n, "Codex reaches out on its own", _wire_command(receiver.sock, receiver_token, second),
                               f"relayed message (+{relay_at:.2f}s): {relay_text}\nanswer (+{reply_at:.2f}s): {reply_text}",
                               time.monotonic() - t0, "ok" if step5_ok else "failed"))
        else:
            for title in ("Claude gives Codex a real task", "Codex reaches out on its own"):
                finish(step_record(nxt(), title, "(not run)", "step 2 did not start a peer", 0.0, "failed"))
    except (LookupError, OSError, RuntimeError) as exc:
        finish(step_record(nxt(), "Claude gives Codex a real task", "(wire exchange failed)", str(exc), 0.0, "failed"))
        finish(step_record(nxt(), "Codex reaches out on its own", "(not run)", "wire exchange failed", 0.0, "failed"))
    finally:
        # last
        n = nxt()
        narrator.step(n, "Tear down")
        t0 = time.monotonic()
        commands: list[str] = []
        outputs: list[str] = []
        kill_ok = True
        if session and not keep:
            commands.append(shlex.join(["tmux", "kill-session", "-t", session]))
            narrator.command(commands[-1])
            kill = subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True, text=True)
            kill_ok = kill.returncode == 0
        elif session:
            commands.append(f"(kept tmux session {session})")
            narrator.note(commands[-1])
        commands.append("cxpeer status")
        narrator.command("cxpeer status")
        status_code, status_output = _run_command(["cxpeer", "status"], repo_root)
        status_output = focus_peer_lines(status_output, ("claude-journey", "codex-journey")) if status_output.strip() else status_output
        outputs.append(status_output)
        narrator.lines(status_output)
        fake.close()
        commands.append("deregister claude-journey")
        outputs.append("deregistered claude-journey")
        narrator.note("deregistered claude-journey")
        finish(step_record(n, "Tear down", "\n".join(commands), "\n".join(outputs), time.monotonic() - t0,
                           "ok" if kill_ok and status_code == 0 else "failed"))

    total = time.monotonic() - started
    if narrator.live:
        narrator.summary(records, total)
    else:
        print("\nSummary\n| Step | Result | Seconds |\n| --- | --- | ---: |")
        for r in records:
            print(f"| {r['number']} | {r['result']} | {r['elapsed']:.2f} |")
        print(f"Total elapsed: {total:.2f}s")
    if record_path:
        try:
            write_markdown(record_path, records)
        except OSError as exc:
            print(f"could not write transcript {record_path}: {exc}", file=sys.stderr)
    return 0 if step4_ok and step5_ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Narrate a complete cxpeer user journey")
    parser.add_argument("--keep", action="store_true", help="leave the spawned tmux session running")
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for each peer response")
    parser.add_argument("--record", type=Path, help="write a Markdown transcript to this path")
    parser.add_argument("--plain", action="store_true", help="no colours, typing, or timers (default when not a terminal)")
    parser.add_argument("--codex-home", help="run the Codex peer from this CODEX_HOME (a second Codex account)")
    parser.add_argument("--second-registry", help="a second Claude account's sessions dir to check the peer shows up in")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    live = sys.stdout.isatty() and not args.plain
    return run_journey(args.keep, args.timeout, args.record, Path(__file__).resolve().parents[1], Narrator(live),
                       codex_home=str(Path(args.codex_home).expanduser()) if args.codex_home else None,
                       second_registry=str(Path(args.second_registry).expanduser()) if args.second_registry else None)


if __name__ == "__main__":
    raise SystemExit(main())
