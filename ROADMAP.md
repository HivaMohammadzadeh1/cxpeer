# Roadmap

Three horizons. An item moves up when it has an owner and a design note. Open an issue or a
discussion to argue for a different order.

## Next (0.5.0)

- Lean spawn. `cxpeer spawn --lean` starts Codex with a smaller instruction set and a shorter skill for short, well-defined jobs.
- Context meter. `cxpeer status` reads the real context use from Claude transcripts and Codex rollouts instead of a character count.
- Message batching. Messages that arrive while Codex is busy are delivered as one prompt at the start of the next turn.

## Later

- Peer rotation by context size. When a peer's context passes a threshold, cxpeer starts a fresh peer and routes new work to it.
- `cxpeer ask`. One-shot workers through `codex exec`: send a question, get the answer, keep no session.
- Per-thread ledger and `cxpeer history`. Every message in and out of a bridge is written to a ledger and can be listed per thread.

## Ideas

- Other CLIs as peers, for example Gemini CLI and OpenCode, behind the same bridge contract.
- A Linux systemd user service that keeps bridges alive across shells.
