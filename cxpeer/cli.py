"""cxpeer command line."""

from __future__ import annotations

import argparse
import json
import sys

from cxpeer import bridge, client, hooks, install, registry, spawn

HOOK_EVENTS = ("session-start", "user-prompt-submit", "stop", "interrupt", "session-end")


def _cmd_list(args: argparse.Namespace) -> int:
    for p in registry.list_peers():
        if p.alive:
            print(f"{p.name} [{p.ref}]  {p.status}  {p.cwd}")
    return 0


def _cmd_send(args: argparse.Namespace) -> int:
    text = sys.stdin.read() if args.text == "-" else args.text
    target = client.find_bridge()
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
        except (OSError, RuntimeError) as e:
            state = f"unreachable ({e})"
        print(f"{b.name}  pid={b.pid}  thread={b.thread}  {state}  {b.cwd}")
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    return install.run(dry_run=args.dry_run)


def _cmd_spawn(args: argparse.Namespace) -> int:
    extra = args.extra[1:] if args.extra[:1] == ["--"] else args.extra
    return spawn.run(args.kind, args.cwd, args.name, args.prompt, extra)


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
    p_install.set_defaults(func=_cmd_install)

    p_spawn = sub.add_parser("spawn", help="start a new codex or claude session in a detached tmux session")
    p_spawn.add_argument("kind", choices=("codex", "claude"))
    p_spawn.add_argument("--cwd", default=".", help="working directory for the new session (default: here)")
    p_spawn.add_argument("--name", help="tmux session suffix and, for claude, the session name")
    p_spawn.add_argument("--prompt", help="initial prompt for the new session")
    p_spawn.add_argument("extra", nargs="*", help="extra args after --, passed to codex/claude verbatim")
    p_spawn.set_defaults(func=_cmd_spawn)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
