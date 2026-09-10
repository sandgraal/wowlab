#!/usr/bin/env python3
"""PreToolUse(Edit|Write|MultiEdit|NotebookEdit): path-based write guard.

Everyone:  LICENSE and migrations already on origin/main are read-only.
Subagents: `.claude/**`, AGENTS.md, CLAUDE.md, docs/DECISIONS.md and
           docs/BACKLOG.md are conductor-only; `code-reviewer` may write only
           new probe files under `tests/review/`.

Paths are resolved against the project directory (or the worktree under
`.claude/worktrees/<name>/` that contains them), with symlinks resolved, so
neither a symlink nor a nested `git init` can re-root a protected path.
Fail-closed: an unparseable payload or an internal error denies the call.
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
    if data is None:
        deny(EVENT, "unparseable hook payload; refusing to guess.")
        return
    if data.get("tool_name") not in WRITE_TOOLS:
        return
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        deny(EVENT, "write tool call without a tool_input mapping; refusing.")
        return
    file_path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if not file_path:
        deny(EVENT, "write tool call without a file path; refusing.")
        return
    cwd = str(data.get("cwd") or Path.cwd())
    agent_type = data.get("agent_type") or None
    root, rel = relative_to_checkout(file_path, cwd)
    exists = (root / rel).exists() if root is not None else Path(rel).exists()
    reason = classify_edit(rel, root, str(agent_type) if agent_type else None, exists=exists)
    if reason:
        deny(EVENT, reason)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        deny(EVENT, f"protect_paths.py failed internally ({type(exc).__name__}: {exc}); refusing.")
