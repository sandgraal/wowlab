#!/usr/bin/env python3
"""SessionStart: print where the project stands so the session can orient in one read.

Output (stdout) is added to the session context, so it is kept short:
branch and working-tree state, then the eligible ticket frontier from
docs/BACKLOG.md. A ticket counts as done when its heading is ticked
(`## [x] M10-04 — …`) or a merged PR title carries `(M10-04)` (best effort via
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
# ([TEST]) twin of an implementation ticket, e.g. M10-04T precedes M10-04.
HEADING = re.compile(r"^## \[( |x)\] (M\d+-\d+T?) — (.+?)\s*$")
DEPENDS = re.compile(r"\*\*Depends on:\*\*\s*(.*?)(?:·|$)")
TICKET = re.compile(r"M\d+-\d+T?")
# A ticket whose Size line carries `**owner**` is the owner's to do or to
# decide; the conductor never dispatches it (the legend at the top of
# docs/BACKLOG.md; the stop rule is .claude/skills/start-ticket/SKILL.md).
OWNER = re.compile(r"\*\*Size:\*\*.*\*\*owner\*\*")


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


def frontier(backlog_text: str, merged: set[str]) -> tuple[list[str], list[str], int, list[str]]:
    """Return (eligible ticket lines, blocked ticket lines, done count, owner lines).

    An owner ticket (`**owner**` on its Size line) is listed on its own,
    never as eligible: it waits for the owner, not for a dispatch.
    """
    tickets: list[tuple[str, str, bool, set[str], bool]] = []
    current: tuple[str, str, bool] | None = None
    deps: set[str] = set()
    owner = False
    for line in backlog_text.splitlines():
        m = HEADING.match(line)
        if m:
            if current:
                tickets.append((current[0], current[1], current[2], deps, owner))
            current = (m.group(2), m.group(3), m.group(1) == "x")
            deps = set()
            owner = False
            continue
        if current:
            d = DEPENDS.search(line)
            if d:
                deps = set(TICKET.findall(d.group(1)))
            if OWNER.search(line):
                owner = True
    if current:
        tickets.append((current[0], current[1], current[2], deps, owner))
    done = {t[0] for t in tickets if t[2]} | merged
    eligible: list[str] = []
    blocked: list[str] = []
    owners: list[str] = []
    for tid, title, _ticked, tdeps, is_owner in tickets:
        if tid in done:
            continue
        missing = sorted(tdeps - done)
        if missing:
            blocked.append(f"{tid} {title} (needs {', '.join(missing)})")
        elif is_owner:
            owners.append(f"{tid} {title}")
        else:
            eligible.append(f"{tid} {title}")
    return eligible, blocked, len(done & {t[0] for t in tickets}), owners


def main() -> None:
    data = read_hook_input() or {}
    root = checkout_root(Path(str(data.get("cwd") or Path.cwd())).resolve())
    if root is None:
        return
    branch = _run(["git", "branch", "--show-current"], root) or "(detached)"
    status = _run(["git", "status", "--short"], root)
    dirty = len(status.splitlines())
    print(f"[wowlab] branch={branch} dirty_files={dirty}")
    backlog = root / "docs" / "BACKLOG.md"
    if backlog.exists():
        eligible, blocked, done, owners = frontier(
            backlog.read_text(encoding="utf-8"), merged_ticket_ids(root)
        )
        print(
            f"[wowlab] backlog: {done} done, {len(eligible)} eligible, "
            f"{len(blocked)} blocked, {len(owners)} owner"
        )
        # Every eligible ticket is printed: the conductor dispatches the
        # whole frontier at once, so a cut list would hide work.
        for line in eligible:
            print(f"  ready   {line}")
        for line in owners:
            print(f"  owner   {line}")
        for line in blocked[:8]:
            print(f"  blocked {line}")
    print("[wowlab] conductor entry point: /conduct next  (docs/AGENT_WORKFLOW.md)")


if __name__ == "__main__":
    main()
