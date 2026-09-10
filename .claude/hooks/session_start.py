#!/usr/bin/env python3
"""SessionStart: print where the project stands so the session can orient in one read.

Output (stdout) is added to the session context, so it is kept short:
branch and working-tree state, then the eligible ticket frontier from
docs/BACKLOG.md. A ticket counts as done when its heading is ticked
(`## [x] M1-02 — …`) or a merged PR title carries `(M1-02)` (best effort via
`gh`, skipped when offline).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import checkout_root, read_hook_input

# Ticket ids: M<milestone>-<nn>, with an optional T suffix for the grader
# ([TEST]) twin of an implementation ticket, e.g. M1-02T precedes M1-02.
HEADING = re.compile(r"^## \[( |x)\] (M\d+-\d+T?) — (.+?)\s*$")
DEPENDS = re.compile(r"\*\*Depends on:\*\*\s*(.*?)(?:·|$)")
TICKET = re.compile(r"M\d+-\d+T?")


def _run(args: list[str], cwd: Path, timeout: int = 8) -> str:
    try:
        out = subprocess.run(
            args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip()


def merged_ticket_ids(root: Path) -> set[str]:
    if shutil.which("gh") is None:
        return set()
    raw = _run(["gh", "pr", "list", "--state", "merged", "--limit", "300", "--json", "title"], root)
    if not raw:
        return set()
    try:
        titles = [str(item.get("title", "")) for item in json.loads(raw)]
    except (json.JSONDecodeError, AttributeError):
        return set()
    found: set[str] = set()
    for title in titles:
        found.update(m.group(1) for m in re.finditer(r"\((M\d+-\d+T?)\)", title))
    return found


def frontier(backlog_text: str, merged: set[str]) -> tuple[list[str], list[str], int]:
    """Return (eligible ticket lines, blocked ticket lines, done count)."""
    tickets: list[tuple[str, str, bool, set[str]]] = []
    current: tuple[str, str, bool] | None = None
    deps: set[str] = set()
    for line in backlog_text.splitlines():
        m = HEADING.match(line)
        if m:
            if current:
                tickets.append((current[0], current[1], current[2], deps))
            current = (m.group(2), m.group(3), m.group(1) == "x")
            deps = set()
            continue
        if current:
            d = DEPENDS.search(line)
            if d:
                deps = set(TICKET.findall(d.group(1)))
    if current:
        tickets.append((current[0], current[1], current[2], deps))
    done = {t[0] for t in tickets if t[2]} | merged
    eligible: list[str] = []
    blocked: list[str] = []
    for tid, title, _ticked, tdeps in tickets:
        if tid in done:
            continue
        missing = sorted(tdeps - done)
        if missing:
            blocked.append(f"{tid} {title} (needs {', '.join(missing)})")
        else:
            eligible.append(f"{tid} {title}")
    return eligible, blocked, len(done & {t[0] for t in tickets})


def main() -> None:
    data = read_hook_input()
    root = checkout_root(Path(str(data.get("cwd") or Path.cwd())).resolve())
    if root is None:
        return
    branch = _run(["git", "branch", "--show-current"], root) or "(detached)"
    status = _run(["git", "status", "--short"], root)
    dirty = len(status.splitlines())
    print(f"[bronze] branch={branch} dirty_files={dirty}")
    backlog = root / "docs" / "BACKLOG.md"
    if backlog.exists():
        eligible, blocked, done = frontier(
            backlog.read_text(encoding="utf-8"), merged_ticket_ids(root)
        )
        print(f"[bronze] backlog: {done} done, {len(eligible)} eligible, {len(blocked)} blocked")
        for line in eligible[:8]:
            print(f"  ready   {line}")
        for line in blocked[:4]:
            print(f"  blocked {line}")
    print("[bronze] conductor entry point: /conduct next  (docs/AGENT_WORKFLOW.md)")


if __name__ == "__main__":
    main()
