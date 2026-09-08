#!/usr/bin/env python3
"""Compress an asciicast v3 recording of demo/journey.py for viewing.

Waits where only the spinner line changes are capped (default 4 s each), the blank tail
after the summary is dropped, and the times at which each "step N of M" header appears are
printed as JSON on stdout for caption timing.

  scripts/compress-cast.py in.cast out.cast [--wait-cap 4] [--dump-steps steps.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys

SPINNER = re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] ")
WAIT_END = re.compile(r"← |✓ ok|✗ failed")
STEP = re.compile(r"step (\d+) of (\d+) · (.+?) \x1b")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--wait-cap", type=float, default=4.0, help="max seconds a wait (spinner until the reply) may take on screen")
    ap.add_argument("--dump-steps", help="write {duration, steps:[{step, of, title, at}]} JSON here")
    args = ap.parse_args()

    lines = open(args.src, encoding="utf-8").read().splitlines()
    header, events = lines[0], [json.loads(l) for l in lines[1:] if l.strip()]

    # drop the tail after the summary (tmux exiting clears the screen)
    summary = max((i for i, e in enumerate(events) if e[1] == "o" and "total " in e[2]), default=len(events) - 1)
    cut = len(events)
    for i in range(summary + 1, len(events)):
        e = events[i]
        if e[1] != "o" or "\x1b[H\x1b[J" in e[2] or "[exited]" in e[2] or "\x1b[?1049l" in e[2]:
            cut = i
            break
    events = events[:cut]

    # find wait spans: from the first spinner frame until the left pane prints a reply or a result
    spans = []
    i = 0
    while i < len(events):
        if events[i][1] == "o" and SPINNER.search(events[i][2]):
            j = i + 1
            while j < len(events) and not (events[j][1] == "o" and WAIT_END.search(events[j][2])):
                j += 1
            spans.append((i, j))
            i = j + 1
        else:
            i += 1

    scale = [1.0] * len(events)
    for a, b in spans:
        total = sum(events[k][0] for k in range(a, b))
        if total > args.wait_cap:
            f = args.wait_cap / total
            for k in range(a, b):
                scale[k] = f

    out, steps, seen, t = [], [], set(), 0.0
    for k, e in enumerate(events):
        d = e[0] * scale[k]
        t += d
        out.append([round(d, 4), e[1], e[2]])
        if e[1] == "o":
            for m in STEP.finditer(e[2]):
                n = int(m.group(1))
                if n not in seen:
                    seen.add(n)
                    steps.append({"step": n, "of": int(m.group(2)), "title": m.group(3).strip(), "at": round(t, 2)})

    with open(args.dst, "w", encoding="utf-8") as fh:
        fh.write(header + "\n" + "\n".join(json.dumps(e, ensure_ascii=False) for e in out) + "\n")
    if args.dump_steps:
        json.dump({"duration": round(t, 2), "steps": steps}, open(args.dump_steps, "w"), indent=1)
    print(f"{len(events)} events, {len(spans)} waits capped, {t:.1f}s on screen, {len(steps)} steps", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
