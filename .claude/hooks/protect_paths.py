#!/usr/bin/env python3
"""PreToolUse(Edit|Write|MultiEdit|NotebookEdit): path-based write guard.

Everyone:  LICENSE and migrations already on origin/main are read-only.
Subagents: `.claude/**`, AGENTS.md, CLAUDE.md, docs/DECISIONS.md and
           docs/BACKLOG.md are conductor-only; `code-reviewer` may write only
           new probe files under `tests/review/`.

Paths are resolved relative to the checkout that contains them, so a file in
an agent worktree under `.claude/worktrees/<name>/` is judged by its path
inside that worktree.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import classify_edit, deny, read_hook_input, relative_to_checkout

EVENT = "PreToolUse"
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def main() -> None:
    data = read_hook_input()
    if data.get("tool_name") not in WRITE_TOOLS:
        return
    tool_input = data.get("tool_input", {})
    file_path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if not file_path:
        return
    cwd = str(data.get("cwd") or Path.cwd())
    agent_type = data.get("agent_type") or None
    root, rel = relative_to_checkout(file_path, cwd)
    reason = classify_edit(rel, root, str(agent_type) if agent_type else None)
    if reason:
        deny(EVENT, reason)


if __name__ == "__main__":
    main()
