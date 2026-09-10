#!/usr/bin/env python3
"""PreToolUse(Bash): `git commit` only proceeds when `make lint` passes.

Runs in the checkout that is committing (the hook's `cwd`), which is what
makes it correct for agents working in worktrees. Skips silently when the
project environment does not exist yet (fresh clone, no `.venv`) because that
is a setup gap, not a code defect; CI still gates the PR.

The command is fixed (`make lint`, argv form, no shell) so that nothing in
the environment or the hook payload can change what runs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (
    checkout_root,
    command_position,
    deny,
    read_hook_input,
    split_segments,
)

EVENT = "PreToolUse"
LINT_ARGV = ["make", "lint"]


def _is_commit(command: str) -> bool:
    for seg in split_segments(command):
        i = command_position(seg, "git")
        if i is None:
            continue
        sub = next((a for a in seg[i + 1 :] if not a.startswith("-")), None)
        if sub == "commit":
            return True
    return False


def main() -> None:
    data = read_hook_input()
    if data.get("tool_name") != "Bash":
        return
    command = str(data.get("tool_input", {}).get("command", ""))
    if not command or not _is_commit(command):
        return
    root = checkout_root(Path(str(data.get("cwd") or Path.cwd())).resolve())
    if root is None or not (root / ".venv").exists() or not (root / "Makefile").exists():
        return
    try:
        result = subprocess.run(
            LINT_ARGV,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=220,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        deny(
            EVENT, "make lint could not run or timed out; run it yourself and fix what it reports."
        )
        return
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-40:])
        deny(EVENT, f"make lint failed; fix these before committing (never --no-verify):\n{tail}")


if __name__ == "__main__":
    main()
