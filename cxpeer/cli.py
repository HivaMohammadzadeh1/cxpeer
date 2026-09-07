"""cxpeer command line.

Only `list` and `send` are implemented here (Task 3). `bridge`, `hook`, `status`, and
`install` are declared so the command surface is stable, but raise NotImplementedError;
they are wired up in cxpeer.bridge / cxpeer.hooks and the install task.
"""

from __future__ import annotations

import argparse
import sys

from cxpeer import client, registry


def _cmd_list(args: argparse.Namespace) -> int:
    for p in registry.list_peers():
        if p.alive:
            print(f"{p.name} [{p.ref}]  {p.status}  {p.cwd}")
    return 0


def _cmd_send(args: argparse.Namespace) -> int:
    text = sys.stdin.read() if args.text == "-" else args.text
    bridge = client.find_bridge()
    try:
        if bridge is None:
            client.send_direct(args.to, text)
        else:
            client.send(args.to, text, bridge)
    except (LookupError, RuntimeError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def _stub(name: str):
    def _run(args: argparse.Namespace) -> int:
        raise NotImplementedError(f"cxpeer {name} is not wired into the CLI yet")

    return _run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cxpeer", description="Codex CLI sessions as Claude Code peers")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list live peers").set_defaults(func=_cmd_list)

    p_send = sub.add_parser("send", help="send a message to a peer")
    p_send.add_argument("--to", required=True, help="peer name or ref")
    p_send.add_argument("text", help="message text, or - to read stdin")
    p_send.set_defaults(func=_cmd_send)

    # Declared so the surface is stable; implemented by other tasks.
    sub.add_parser("bridge", help="run a bridge (see cxpeer.bridge)").set_defaults(func=_stub("bridge"))
    p_hook = sub.add_parser("hook", help="handle a Codex hook (see cxpeer.hooks)")
    p_hook.add_argument("event", nargs="?")
    p_hook.set_defaults(func=_stub("hook"))
    sub.add_parser("status", help="show bridge status").set_defaults(func=_stub("status"))
    sub.add_parser("install", help="install Codex hooks + skill").set_defaults(func=_stub("install"))

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
