"""`cxpeer install`: merge the four hook entries into Codex's hooks.json and install the skill."""

from __future__ import annotations

import json
import os
import shlex
import sys
from importlib.resources import files
from pathlib import Path

HOOK_EVENTS = {
    "SessionStart": "session-start",
    "UserPromptSubmit": "user-prompt-submit",
    "Stop": "stop",
    "SessionEnd": "session-end",
    "Interrupt": "interrupt",
}


def skill_text() -> str:
    """The Codex skill shipped inside the package (cxpeer/data/SKILL.md)."""
    return files("cxpeer").joinpath("data/SKILL.md").read_text(encoding="utf-8")


def codex_home(explicit: Path | None = None) -> Path:
    """Explicit path beats CXPEER_CODEX_HOME beats ~/.codex."""
    if explicit is not None:
        return Path(explicit)
    return Path(os.environ.get("CXPEER_CODEX_HOME") or Path.home() / ".codex")


resolve_codex_home = codex_home  # alias for use where a parameter is also named codex_home


def installed_homes() -> list[Path]:
    """The default home first, then every ~/.codex-* or ~/.codex_* directory that has a
    config.toml, sorted by name. One entry per Codex account on this machine."""
    default = codex_home()
    extras = sorted(
        p for pattern in (".codex-*", ".codex_*") for p in Path.home().glob(pattern)
        if p.is_dir() and (p / "config.toml").is_file() and p != default
    )
    return [default, *extras]


def hook_command(name: str) -> str:
    """Invoke the hook through the interpreter cxpeer is installed in, so PATH does not matter."""
    return f"{shlex.quote(sys.executable)} -m cxpeer hook {name}"


def merge_hooks(data: dict) -> dict:
    """Add one cxpeer group per event, updating ours if present. Other entries are untouched."""
    hooks = data.setdefault("hooks", {})
    for event, name in HOOK_EVENTS.items():
        groups = hooks.setdefault(event, [])
        ours = _find_ours(groups, name)
        if ours is None:
            groups.append({"matcher": "*", "hooks": [{"type": "command", "command": hook_command(name)}]})
        else:
            ours["command"] = hook_command(name)
    return data


def _find_ours(groups: list, name: str) -> dict | None:
    for g in groups:
        for h in g.get("hooks", []):
            if str(h.get("command", "")).endswith(f"cxpeer hook {name}"):
                return h
    return None


def run(dry_run: bool, codex_home: Path | None = None) -> int:
    home = resolve_codex_home(codex_home)
    hooks_path = home / "hooks.json"
    skill_path = home / "skills" / "cxpeer" / "SKILL.md"

    data: dict = {"hooks": {}}
    if hooks_path.exists():
        try:
            data = json.loads(hooks_path.read_text())
            if not isinstance(data, dict):
                raise ValueError("top level is not an object")
        except ValueError as exc:
            print(f"{hooks_path}: not valid JSON ({exc}); leaving it alone", file=sys.stderr)
            return 1
    try:
        skill = skill_text()
    except OSError as exc:
        print(f"cxpeer package data SKILL.md is missing ({exc})", file=sys.stderr)
        return 1

    merged = json.dumps(merge_hooks(data), indent=2) + "\n"

    if dry_run:
        print(f"# {hooks_path}\n{merged}")
        print(f"# {skill_path}\n{skill}")
        return 0

    _write_atomic(hooks_path, merged)
    _write_atomic(skill_path, skill)
    print(f"Wrote {hooks_path} ({len(HOOK_EVENTS)} cxpeer hook entries)")
    print(f"Wrote {skill_path}")
    return 0


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
