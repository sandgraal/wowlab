"""Shared helpers for the Claude Code hook scripts in this directory.

Constraints: Python 3.9+ and the standard library only. Hooks run on whatever
`python3` a contributor's machine has, before any project environment exists,
so nothing here may import from the project or from third-party packages.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

# Contributor machines without a package manager keep user binaries here.
_HOME_BIN = str(Path.home() / ".local" / "bin")
if _HOME_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = _HOME_BIN + os.pathsep + os.environ.get("PATH", "")

# Paths only the conductor (main session) may change. Subagents report the
# need instead. Shell writes to these are blocked for everyone so that edits
# always go through Edit/Write, where the hook can see them.
HARNESS_PATHS = (".claude/", "AGENTS.md", "CLAUDE.md", "docs/DECISIONS.md", "docs/BACKLOG.md")
PROTECTED_ALWAYS = ("LICENSE",)
MIGRATIONS_DIR = "api/migrations/versions/"
REVIEW_DIR_MARKER = "/tests/review/"

# Directories where `rm -rf` is routine and safe.
SCRATCH_MARKERS = (
    "node_modules",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".next",
    "/dist",
    "/build",
    "coverage",
    "htmlcov",
    "/tmp/",
    "scratchpad",
    "playwright-report",
    "test-results",
)


def read_hook_input() -> dict[str, Any]:
    """Parse the JSON payload Claude Code writes to stdin. Empty dict on failure."""
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def deny(event: str, reason: str) -> None:
    """Block the tool call: JSON decision on stdout, human reason on stderr, exit 2."""
    payload = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(payload))
    print(f"Blocked by {Path(sys.argv[0]).name}: {reason}", file=sys.stderr)
    sys.exit(2)


def checkout_root(start: Path) -> Path | None:
    """Root of the git checkout containing `start`.

    Walks up to the nearest `.git`, which is a directory in the main checkout
    and a file inside a worktree. That distinction matters: a file at
    `.claude/worktrees/x/api/foo.py` must resolve relative to the worktree
    root (`api/foo.py`), not to the main checkout (`.claude/...`).
    """
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def relative_to_checkout(file_path: str, cwd: str) -> tuple[Path | None, str]:
    """Return (checkout root, posix path relative to it) for a file that may not exist yet."""
    p = Path(file_path)
    if not p.is_absolute():
        p = Path(cwd) / p
    p = Path(os.path.normpath(str(p)))
    root = checkout_root(p.parent) or checkout_root(Path(cwd))
    if root is None:
        return None, p.as_posix()
    try:
        return root, p.relative_to(root).as_posix()
    except ValueError:
        return root, p.as_posix()


def is_on_origin_main(root: Path, rel: str) -> bool:
    """True if `rel` exists on origin/main (i.e. it has been merged)."""
    try:
        result = subprocess.run(
            ["git", "cat-file", "-e", f"origin/main:{rel}"],
            cwd=str(root),
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def classify_edit(rel: str, root: Path | None, agent_type: str | None) -> str | None:
    """Reason an Edit/Write must be denied, or None when it is allowed."""
    if rel in PROTECTED_ALWAYS:
        return f"{rel} is fixed (Apache-2.0). Changing the license is an owner decision."
    if rel.startswith(MIGRATIONS_DIR) and root is not None and is_on_origin_main(root, rel):
        return (
            f"{rel} is already merged. Never edit a merged migration; "
            "create a new revision (/migration)."
        )
    if agent_type:
        if agent_type == "code-reviewer":
            if REVIEW_DIR_MARKER not in "/" + rel:
                return (
                    "code-reviewer writes only new probe files under tests/review/. "
                    "Report other changes as findings for the implementer."
                )
            return None
        for prefix in HARNESS_PATHS:
            if rel == prefix or rel.startswith(prefix):
                return (
                    f"{prefix} is conductor-only. Subagents never edit the harness, "
                    "ADRs, or the backlog; describe the needed change in your final report."
                )
    return None


def shell_write_denial(rel: str, root: Path | None) -> str | None:
    """Reason a shell-level write (redirect, tee, sed -i, cp) to `rel` must be denied."""
    if rel in PROTECTED_ALWAYS:
        return f"{rel} is fixed and may not be rewritten from the shell."
    for prefix in HARNESS_PATHS:
        if rel == prefix or rel.startswith(prefix):
            return (
                f"shell write into {prefix} is blocked; use the Edit/Write tools so the "
                "path guard can see the change (harness edits are conductor-only)."
            )
    if rel.startswith(MIGRATIONS_DIR) and root is not None and is_on_origin_main(root, rel):
        return f"{rel} is a merged migration; write a new revision instead."
    return None


_OPERATORS = {";", "&&", "||", "|", "&", "(", ")"}


def split_segments(command: str) -> list[list[str]]:
    """Split a shell command into simple commands (token lists), honouring quotes.

    Redirect operators (`>`, `>>`, `<`) are kept as their own tokens so callers
    can inspect their targets. Returns [] when the command cannot be tokenised
    (unbalanced quotes); callers decide whether that is allow or deny.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = [[]]
    for tok in tokens:
        if tok in _OPERATORS:
            if segments[-1]:
                segments.append([])
            continue
        # punctuation_chars glues runs like '>>' or '|&'; split redirects back out.
        if tok and set(tok) <= {">", "<", "&"} and tok not in (">", ">>", "<"):
            tok = tok.replace("&", "")
            if not tok:
                continue
        segments[-1].append(tok)
    return [s for s in segments if s]


def command_position(seg: list[str], name: str) -> int | None:
    """Index of `name` when it is the command of this segment (allowing wrappers)."""
    wrappers = {"sudo", "xargs", "exec", "time", "nice", "env", "command", "do", "then", "else"}
    for i, tok in enumerate(seg):
        base = tok.rsplit("/", 1)[-1]
        if base == name:
            if all(t in wrappers or t.startswith("-") or "=" in t for t in seg[:i]):
                return i
            return None
        if base in wrappers or tok.startswith("-") or "=" in tok:
            continue
        return None
    return None
