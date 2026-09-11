"""cxpeer command line."""

from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path

from cxpeer import bridge, client, doctor, hooks, install, paths, registry, spawn

HOOK_EVENTS = ("session-start", "user-prompt-submit", "stop", "interrupt", "session-end")


def _cmd_list(args: argparse.Namespace) -> int:
    if client.sandboxed():
        return _list_from_snapshot()
    for p in registry.list_peers():
        if p.alive:
            print(f"{p.name} [{p.ref}]  {p.status}  {p.cwd}")
    return 0


def _list_from_snapshot() -> int:
    # `ps` is blocked in the Codex sandbox, so read peers from the bridge's snapshot file.
    target = client.find_bridge()
    peers = client.list_peers_snapshot(target) if target is not None else None
    if not peers:
        print("cxpeer list: no fresh bridge snapshot in this sandbox", file=sys.stderr)
        return 0
    for p in peers:
        print(f"{p['name']} [{p['ref']}]  {p['status']}  {p['cwd']}")
    return 0


def _cmd_send(args: argparse.Namespace) -> int:
    text = sys.stdin.read() if args.text == "-" else args.text
    target = client.find_bridge()
    if target is None and client.sandboxed():
        # No bridge and sockets are blocked, so send_direct cannot work either.
        print("no bridge for this session and sockets are blocked in this sandbox; "
              "start Codex with the cxpeer hooks installed", file=sys.stderr)
        return 1
    try:
        if target is None:
            client.send_direct(args.to, text)
        else:
            client.send(args.to, text, target)
    except (LookupError, RuntimeError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def _cmd_bridge(args: argparse.Namespace) -> int:
    return bridge.run(args.thread, args.cwd, name=args.name, watch_pid=args.watch_pid)


def _cmd_hook(args: argparse.Namespace) -> int:
    # Codex treats a non-zero exit as a hook failure, so nothing here may raise.
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return hooks.run(args.event, payload)


def _cmd_status(args: argparse.Namespace) -> int:
    bridges = client.list_bridges()
    if not bridges:
        print("no bridges (start a Codex session with the cxpeer hooks installed)")
        return 0
    for b in bridges:
        try:
            info = client.ping(b)
            state = f"{info.get('status', '?')}  pending={info.get('pending', '?')}"
            if "chars_in" in info:
                state += f"  in={info['chars_in']}c(~{info['chars_in'] // 4}t)  out={info['chars_out']}c(~{info['chars_out'] // 4}t)"
        except (OSError, RuntimeError) as e:
            state = f"unreachable ({e})"
        print(f"{b.name}  pid={b.pid}  thread={b.thread}  {state}  {b.cwd}")
    return 0


def _cmd_read(args: argparse.Namespace) -> int:
    """Print the full text of a reply the bridge truncated; ids may be the 8-char prefix."""
    root = Path(paths.state_dir()) / "replies"
    matches = sorted(root.glob(f"*/{args.msg_id}*.md")) if root.exists() else []
    if not matches:
        print(f"no stored reply matches {args.msg_id!r} under {root}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print("ambiguous id; matches: " + ", ".join(m.stem for m in matches), file=sys.stderr)
        return 1
    sys.stdout.write(matches[0].read_text())
    return 0


def _codex_home(args: argparse.Namespace) -> Path | None:
    return Path(args.codex_home).expanduser() if args.codex_home else None


def _cmd_install(args: argparse.Namespace) -> int:
    return install.run(dry_run=args.dry_run, codex_home=_codex_home(args))


def _cmd_doctor(args: argparse.Namespace) -> int:
    return doctor.run(codex_home=_codex_home(args))


def _cmd_spawn(args: argparse.Namespace) -> int:
    extra = args.extra[1:] if args.extra[:1] == ["--"] else args.extra
    return spawn.run(args.kind, args.cwd, args.name, args.prompt, extra,
                     peer_name=args.peer_name, wait=args.wait, timeout=args.timeout,
                     codex_home=str(_codex_home(args)) if args.codex_home else None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cxpeer", description="Codex CLI sessions as Claude Code peers")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list live peers").set_defaults(func=_cmd_list)

    p_send = sub.add_parser("send", help="send a message to a peer")
    p_send.add_argument("--to", required=True, help="peer name or ref")
    p_send.add_argument("text", help="message text, or - to read stdin")
    p_send.set_defaults(func=_cmd_send)

    p_bridge = sub.add_parser("bridge", help="run the bridge for one Codex session (started by the SessionStart hook)")
    bridge.add_arguments(p_bridge)
    p_bridge.set_defaults(func=_cmd_bridge)

    p_hook = sub.add_parser("hook", help="handle a Codex hook event; payload JSON on stdin")
    p_hook.add_argument("event", choices=HOOK_EVENTS)
    p_hook.set_defaults(func=_cmd_hook)

    sub.add_parser("status", help="show every bridge and whether it answers").set_defaults(func=_cmd_status)

    p_install = sub.add_parser("install", help="add the cxpeer hooks and skill to ~/.codex")
    p_install.add_argument("--dry-run", action="store_true", help="print what would be written")
    p_install.add_argument("--codex-home", help="install into this Codex home instead of ~/.codex (a second Codex account)")
    p_install.set_defaults(func=_cmd_install)

    p_read = sub.add_parser("read", help="print the full text of a reply that was forwarded truncated")
    p_read.add_argument("msg_id", help="the id from the truncation note (8-char prefix is enough)")
    p_read.set_defaults(func=_cmd_read)

    p_doctor = sub.add_parser("doctor", help="diagnose why peers are not showing up in ListAgents; runs a self-test")
    p_doctor.add_argument("--codex-home", help="check this Codex home instead of ~/.codex (a second Codex account)")
    p_doctor.set_defaults(func=_cmd_doctor)

    p_spawn = sub.add_parser("spawn", help="start a new codex or claude session in a detached tmux session")
    p_spawn.add_argument("kind", choices=("codex", "claude"))
    p_spawn.add_argument("--cwd", default=".", help="working directory for the new session (default: here)")
    p_spawn.add_argument("--name", help="tmux session suffix and, for claude, the session name")
    p_spawn.add_argument("--prompt", help="initial prompt for the new session")
    p_spawn.add_argument("--peer-name", help="name the session shows in ListAgents (default: --name)")
    p_spawn.add_argument("--wait", action="store_true",
                         help="answer Codex's startup prompts and wait until the peer is registered")
    p_spawn.add_argument("--timeout", type=float, default=60.0, help="seconds --wait allows (default 60)")
    p_spawn.add_argument("--codex-home", help="start codex with this CODEX_HOME (a second Codex account)")
    p_spawn.add_argument("extra", nargs="*", help="extra args after --, passed to codex/claude verbatim")
    p_spawn.set_defaults(func=_cmd_spawn)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
