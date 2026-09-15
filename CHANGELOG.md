# Changelog

All notable changes to cxpeer are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Version numbers follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0.0, a minor release
can change the CLI and the wire protocol.

## [Unreleased]

## [0.5.0] - 2026-09-15

### Added
- A context meter. `cxpeer list` shows each peer's last prompt size (`ctx=22k/258k (8%)` for Codex, `ctx=163k` for Claude), read from the tail of the transcripts both tools already write; `cxpeer status` shows it per bridge. A `!` marks a Codex peer past 70% of its window.
- `cxpeer spawn --lean` starts Codex with `-c mcp_servers={}` and Claude with `--strict-mcp-config --mcp-config "" --disable-slash-commands`. Both still register as peers; `--bare` does not. A trivial lean Codex turn costs about 3.7k tokens instead of 15k.
- A landing page at https://hivam.org/cxpeer/ (GitHub Pages from `docs/`), a social preview card, and a draft write-up of the peer protocol in `docs/blog/`.
- CHANGELOG, ROADMAP, CONTRIBUTING refresh, issue and pull request templates, repository topics, and GitHub Discussions.

## [0.4.0] - 2026-09-10

### Added
- `cxpeer read <id>` prints the full text of a reply that was forwarded capped.
- `cxpeer status` shows characters in and out per bridge.
- A "Context budget" section in the README with the measured numbers.

### Changed
- Compact peer-message framing. A message costs Codex about 17 tokens of framing instead of 87.
- Replies longer than 4000 characters are forwarded capped, with a note that points to `cxpeer read <id>`.
- Both skills are about a third of their old size. They teach token-lean habits: answer in the final message, send file paths instead of file contents, one task per message.

## [0.3.0] - 2026-09-07

### Added
- Several Codex accounts on one machine. `--codex-home` for `cxpeer install`, `cxpeer doctor`, and `cxpeer spawn`. `install.installed_homes()` finds `~/.codex-*` and `~/.codex_*` homes.
- Several Claude accounts on one machine. The registry writes and reads every `~/.claude*` registry.
- A recorded two-account user journey (`docs/user-journey-accounts.md`) and a demo video with captions.
- One bridge per Codex thread, enforced with a file lock. Hooks wait a grace period before they respawn a bridge.

### Changed
- Idle notices are held until every queued request has been answered.

## [0.2.0] - 2026-09-07

### Added
- `cxpeer doctor`: environment, hook, and trust checks, plus a live self-test.
- `cxpeer spawn --wait`, `--peer-name`, and `--timeout`. The command answers the Codex startup prompts, waits for the bridge, and names the peer.
- Idle notices. The bridge honours `notify_when_idle` subscriptions from Claude Code.
- Self-healing hooks. A hook respawns a missing or dead bridge before it sends a frame.
- The Codex `Interrupt` hook. A cancelled turn ends the request instead of leaving the bridge busy.
- A file outbox, so `cxpeer send` and `cxpeer list` work from inside the Codex sandbox without sockets or `ps`.
- `scripts/e2e.sh` starts a real Codex in tmux, answers its prompts, and waits for the bridge.
- Linux CI (ubuntu-latest) next to macOS.
- A runnable demo (`demo/README.md`) and a recorded 41-second user journey (`docs/user-journey.md`).

### Changed
- License changed from MIT to PolyForm Noncommercial 1.0.0.
- Unanswered requests expire with a note to the sender. The relay timeout is shorter.

## [0.1.0] - 2026-09-07

First working version. A Claude Code session messaged a live Codex TUI and got the answer back in 8 seconds.

### Added
- The bridge process. One per Codex session. It registers as a Claude Code peer, forwards messages with `codex queue`, and sends the final answer back when the turn ends.
- Codex hooks (`SessionStart`, `UserPromptSubmit`, `Stop`, `SessionEnd`) and `cxpeer install` to register them.
- `cxpeer list`, `cxpeer send`, `cxpeer status`, and `cxpeer spawn` (codex or claude in a detached tmux session).
- Claude Code plugin packaging (`.claude-plugin/`), a Claude-side skill, and a Codex-side skill.
- macOS CI and the MIT license (replaced in 0.2.0).

[Unreleased]: https://github.com/HivaMohammadzadeh1/cxpeer/compare/v0.5.0...master
[0.5.0]: https://github.com/HivaMohammadzadeh1/cxpeer/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/HivaMohammadzadeh1/cxpeer/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/HivaMohammadzadeh1/cxpeer/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/HivaMohammadzadeh1/cxpeer/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/HivaMohammadzadeh1/cxpeer/releases/tag/v0.1.0
