"""Shared helpers for the Claude Code hook scripts in this directory.

Constraints: Python 3.9+ and the standard library only. Hooks run on whatever
`python3` a contributor's machine has, before any project environment exists,
so nothing here may import from the project or from third-party packages.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

# Contributor machines without a package manager keep user binaries here.
# Appended, not prepended: the hooks themselves call git/gh/make and must not
# resolve them from a user-writable directory ahead of the system ones.
_HOME_BIN = str(Path.home() / ".local" / "bin")
if _HOME_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + _HOME_BIN

# Paths only the conductor (main session) may change. Subagents report the
# need instead. Shell writes to these are blocked for everyone so that edits
# always go through Edit/Write, where the hook can see them. Matched
# case-insensitively: the default macOS filesystem is case-insensitive.
HARNESS_PATHS = (".claude/", "AGENTS.md", "CLAUDE.md", "docs/DECISIONS.md", "docs/BACKLOG.md")
PROTECTED_ALWAYS = ("LICENSE",)
MIGRATIONS_DIR = "api/migrations/versions/"
REVIEW_DIR_MARKER = "/tests/review/"

# Directory names where destructive deletes are routine and safe (matched as
# whole path segments of the normalised path), plus temp roots as prefixes.
SCRATCH_SEGMENTS = frozenset(
    {
        "node_modules",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".next",
        "dist",
        "build",
        "coverage",
        "htmlcov",
        "scratchpad",
        "playwright-report",
        "test-results",
    }
)
SCRATCH_PREFIXES = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")


def read_hook_input() -> dict[str, Any] | None:
    """Parse the JSON payload Claude Code writes to stdin. None when unusable."""
    raw = sys.stdin.read()
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


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


# ─── paths ───────────────────────────────────────────────────────────────────

_VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def expand_vars(token: str, cwd: str) -> tuple[str, bool]:
    """Expand the shell variables a path can plausibly use.

    Returns (expanded, fully_resolved). Unknown variables are left in place
    and reported as unresolved so the caller can refuse to guess.
    """
    known = {
        "CLAUDE_PROJECT_DIR": os.environ.get("CLAUDE_PROJECT_DIR", ""),
        "PWD": cwd,
        "HOME": str(Path.home()),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }
    unresolved = False

    def sub(m: re.Match[str]) -> str:
        nonlocal unresolved
        val = known.get(m.group(1))
        if not val:
            unresolved = True
            return m.group(0)
        return val

    out = _VAR.sub(sub, token)
    if out.startswith("~"):
        out = str(Path.home()) + out[1:]
    return out, not unresolved


def _real_lenient(p: Path) -> Path:
    """realpath() for a path that may not exist yet: resolve the deepest existing ancestor."""
    p = Path(os.path.normpath(str(p)))
    missing: list[str] = []
    cur = p
    while not cur.exists() and cur != cur.parent:
        missing.append(cur.name)
        cur = cur.parent
    real = cur.resolve()
    for name in reversed(missing):
        real = real / name
    return real


def checkout_root(start: Path) -> Path | None:
    """Nearest ancestor holding a `.git` (directory in a checkout, file in a worktree)."""
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def project_dir() -> Path | None:
    value = os.environ.get("CLAUDE_PROJECT_DIR", "")
    return Path(value).resolve() if value else None


def relative_to_checkout(file_path: str, cwd: str) -> tuple[Path | None, str]:
    """Return (checkout root, posix path relative to it) for a file that may not exist yet.

    The root is the main checkout (`CLAUDE_PROJECT_DIR`) or, for a path under
    `.claude/worktrees/<name>/`, that worktree — decided from the project
    directory, not from the nearest `.git`, so a stray `git init` inside a
    subdirectory cannot re-root a protected path. Symlinks are resolved on
    the deepest existing ancestor. Outside the project (tests, ad-hoc
    worktrees) the nearest `.git` is used.
    """
    p = Path(file_path)
    if not p.is_absolute():
        p = Path(cwd) / p
    p = _real_lenient(p)
    root: Path | None = None
    pd = project_dir()
    if pd is not None:
        try:
            parts = p.relative_to(pd).parts
        except ValueError:
            parts = ()
        if parts:
            if len(parts) >= 3 and parts[0] == ".claude" and parts[1] == "worktrees":
                root = pd / parts[0] / parts[1] / parts[2]
            else:
                root = pd
    if root is None:
        root = checkout_root(p.parent) or checkout_root(_real_lenient(Path(cwd)))
    if root is None:
        return None, p.as_posix()
    try:
        return root, p.relative_to(root).as_posix()
    except ValueError:
        return None, p.as_posix()  # outside every checkout we know about


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


def is_scratch_path(target: str, cwd: str) -> bool:
    p = Path(target)
    if not p.is_absolute():
        p = Path(cwd) / p
    norm = os.path.normpath(str(p))
    if norm.startswith(SCRATCH_PREFIXES):
        return True
    return any(seg in SCRATCH_SEGMENTS for seg in Path(norm).parts)


def _under(rel: str, prefix: str) -> bool:
    """Case-insensitive 'rel is prefix or lives under prefix (directory)'."""
    r, pre = rel.lower(), prefix.lower()
    if pre.endswith("/"):
        return r == pre.rstrip("/") or r.startswith(pre)
    return r == pre


def classify_edit(
    rel: str, root: Path | None, agent_type: str | None, exists: bool = False
) -> str | None:
    """Reason an Edit/Write must be denied, or None when it is allowed."""
    if any(_under(rel, p) for p in PROTECTED_ALWAYS):
        return f"{rel} is fixed (Apache-2.0). Changing the license is an owner decision."
    if _under(rel, MIGRATIONS_DIR) and root is not None and is_on_origin_main(root, rel):
        return (
            f"{rel} is already merged. Never edit a merged migration; "
            "create a new revision (/migration)."
        )
    if agent_type:
        if agent_type == "code-reviewer":
            if root is None or REVIEW_DIR_MARKER not in "/" + rel:
                return (
                    "code-reviewer writes only new probe files under tests/review/ inside "
                    "the checkout. Report other changes as findings for the implementer."
                )
            if exists:
                return "code-reviewer adds new probe files; it never edits an existing one."
            return None
        for prefix in HARNESS_PATHS:
            if _under(rel, prefix):
                return (
                    f"{prefix} is conductor-only. Subagents never edit the harness, "
                    "ADRs, or the backlog; describe the needed change in your final report."
                )
    return None


def shell_write_denial(rel: str, root: Path | None) -> str | None:
    """Reason a shell-level write/delete/move of `rel` must be denied."""
    if any(_under(rel, p) for p in PROTECTED_ALWAYS):
        return f"{rel} is fixed and may not be rewritten from the shell."
    for prefix in HARNESS_PATHS:
        if _under(rel, prefix):
            return (
                f"shell write into {prefix} is blocked; use the Edit/Write tools so the "
                "path guard can see the change (harness edits are conductor-only)."
            )
    if _under(rel, MIGRATIONS_DIR) and root is not None and is_on_origin_main(root, rel):
        return f"{rel} is a merged migration; write a new revision instead."
    return None


# ─── shell parsing ───────────────────────────────────────────────────────────

_OPERATORS = {";", "&&", "||", "|", "&", "(", ")"}
_WRAPPERS = {
    "sudo",
    "xargs",
    "exec",
    "time",
    "nice",
    "env",
    "command",
    "do",
    "then",
    "else",
    "timeout",
    "nohup",
    "caffeinate",
}
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def strip_heredocs(command: str) -> str:
    """Remove heredoc bodies so their prose cannot break tokenisation or masquerade as tokens."""
    lines = command.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _HEREDOC.search(line)
        out.append(line)
        i += 1
        if m:
            terminator = m.group(2)
            while i < len(lines) and lines[i].strip() != terminator:
                i += 1
            i += 1  # skip the terminator line
    return "\n".join(out)


def split_segments(command: str) -> list[list[str]] | None:
    """Split a shell command into simple commands (token lists), honouring quotes.

    Heredoc bodies are removed first. Redirect operators (`>`, `>>`, `>|`,
    `<`) are kept as their own tokens so callers can inspect their targets.
    Returns None when the command still cannot be tokenised; guards treat
    that as a reason to deny, never to skip.
    """
    lexer = shlex.shlex(strip_heredocs(command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    segments: list[list[str]] = [[]]
    for tok in tokens:
        if tok in _OPERATORS:
            if segments[-1]:
                segments.append([])
            continue
        if tok and set(tok) <= {">", "<", "&", "|"} and tok not in (">", ">>", "<"):
            tok = tok.replace("&", "").replace("|", "")
            if not tok:
                continue
        segments[-1].append(tok)
    return [s for s in segments if s]


def command_position(seg: list[str], name: str) -> int | None:
    """Index of `name` when it is the command of this segment (allowing wrappers)."""
    for i, tok in enumerate(seg):
        base = tok.rsplit("/", 1)[-1]
        if base == name:
            return i
        if base in _WRAPPERS or tok.startswith("-") or "=" in tok or tok.isdigit():
            continue
        return None
    return None


_GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


def git_subcommand(args: list[str]) -> tuple[str | None, list[str], list[str]]:
    """Split git args into (subcommand, global options, subcommand args).

    Skips values of `-C <dir>` and `-c <k=v>` so `git -c x=y commit` is still a
    commit and `git -C . push` is still a push.
    """
    i = 0
    globals_: list[str] = []
    while i < len(args):
        a = args[i]
        if a in _GIT_OPTS_WITH_VALUE:
            globals_.extend(args[i : i + 2])
            i += 2
            continue
        if a.startswith("-"):
            globals_.append(a)
            i += 1
            continue
        return a, globals_, args[i + 1 :]
    return None, globals_, []


def short_flag_present(args: list[str], letter: str) -> bool:
    """True if a short flag cluster (`-nm`, `-fu`) contains `letter`."""
    return any(a.startswith("-") and not a.startswith("--") and letter in a[1:] for a in args)


def long_flag_present(args: list[str], flag: str, min_prefix: int = 8) -> bool:
    """True if `flag` or an unambiguous git-style prefix of it (>= min_prefix chars) is present."""
    return any(len(a) >= min_prefix and flag.startswith(a) for a in args)


def inner_commands(seg: list[str]) -> list[str]:
    """Command strings passed to `bash -c`, `sh -c`, `eval`, … for recursive inspection."""
    for shell in ("bash", "sh", "zsh", "dash"):
        i = command_position(seg, shell)
        if i is None:
            continue
        rest = seg[i + 1 :]
        for j, tok in enumerate(rest):
            if tok.startswith("-") and "c" in tok and j + 1 < len(rest):
                return [rest[j + 1]]
    i = command_position(seg, "eval")
    if i is not None:
        return [" ".join(seg[i + 1 :])]
    return []
