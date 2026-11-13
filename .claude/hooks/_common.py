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
# need instead. Shell writes into the harness proper (HARNESS_CODE) are
# blocked for everyone, so that a stranger's PR text can never steer a
# session into pulling a hook or an agent definition from another branch
# unseen (ADR-0031): edits go through Edit/Write, where the hook can see
# them. The two conductor documents (CONDUCTOR_DOCS) are blocked from the
# shell for subagents only; the conductor may write them any way it likes,
# since they land through a reviewed PR (owner decision, 2026-10-17).
# Matched case-insensitively: the default macOS filesystem is case-insensitive.
HARNESS_CODE = (".claude/", "AGENTS.md", "CLAUDE.md")
CONDUCTOR_DOCS = ("docs/DECISIONS.md", "docs/BACKLOG.md")
HARNESS_PATHS = HARNESS_CODE + CONDUCTOR_DOCS
PROTECTED_ALWAYS = ("LICENSE",)
# code-reviewer probes live in the `tests/review/` directory of a workspace
# member (today: lab/core/tests/review/).
REVIEW_DIR_MARKER = "/tests/review/"

# Directory names where destructive deletes are routine and safe (matched as
# whole path segments of the normalised path), plus temp roots as prefixes.
SCRATCH_SEGMENTS = frozenset(
    {
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "dist",
        "build",
        "coverage",
        "htmlcov",
        "scratchpad",
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

# `$NAME` or `${NAME}`, nothing else: `${NAME#x}`, `${NAME:+x}`, `$1`, `$@`
# and the like are left in place and make the token unresolved (a review of
# 2026-11-13 found `${PWD:+.claude}` resolving to a harmless path while bash
# wrote into `.claude/`).
_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def expand_vars(token: str, cwd: str, assigned: dict[str, str] | None = None) -> tuple[str, bool]:
    """Expand the shell variables a path can plausibly use.

    `assigned` holds variables the same command line set earlier to a value
    the hook could resolve (see `assignments`); they win over the defaults,
    as they would in the shell.

    Returns (expanded, fully_resolved). Unknown variables are left in place
    and reported as unresolved so the caller can refuse to guess.
    """
    known = {
        "CLAUDE_PROJECT_DIR": os.environ.get("CLAUDE_PROJECT_DIR", ""),
        "PWD": cwd,
        "HOME": str(Path.home()),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }
    if assigned:
        known.update(assigned)
    unresolved = False

    def sub(m: re.Match[str]) -> str:
        nonlocal unresolved
        val = known.get(m.group(1) or m.group(2))
        if not val:
            unresolved = True
            return m.group(0)
        return val

    out = _VAR.sub(sub, token)
    if out == "~" or out.startswith("~/"):
        out = str(Path.home()) + out[1:]
    elif out.startswith("~"):
        # `~+` is $PWD, `~-` is $OLDPWD, `~name` another home: not followed.
        unresolved = True
    # Any `$` the pattern did not consume is an expansion the hook cannot
    # follow (an operator form, a positional or special parameter, a
    # substitution the lexer split apart).
    if "$" in _VAR.sub("", token):
        unresolved = True
    return out, not unresolved


_ASSIGN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.DOTALL)
_UNSAFE_VALUE = re.compile(r"[\s$*?\[\]{}~`\\]")


# Every bash builtin and reserved word. As a segment's command word, any of
# them except _SAFE_BUILTINS turns assignment tracking off for the line:
# they can set, clear or defer a change to a variable (`declare`, `read`,
# `trap`, `eval`, ...), wrap another builtin (`builtin`, `command`), or open
# a compound command. External programs cannot change this shell's
# variables, so they are allowed. An allowlist, after review of 2026-11-13
# found a blocklist kept leaking.
_SHELL_WORDS = frozenset(
    {
        "alias", "bg", "bind", "break", "builtin", "caller", "cd", "command",
        "compgen", "complete", "compopt", "continue", "declare", "dirs",
        "disown", "echo", "enable", "eval", "exec", "exit", "export", "false",
        "fc", "fg", "getopts", "hash", "help", "history", "jobs", "kill",
        "let", "local", "logout", "mapfile", "popd", "printf", "pushd", "pwd",
        "read", "readarray", "readonly", "return", "set", "shift", "shopt",
        "source", "suspend", "test", "times", "trap", "true", "type",
        "typeset", "ulimit", "umask", "unalias", "unset", "wait", ".", ":",
        "[", "if", "then", "else", "elif", "fi", "case", "esac", "for",
        "select", "while", "until", "do", "done", "in", "function", "time",
        "{", "}", "!", "[[", "]]", "coproc",
    }
)  # fmt: skip
_SAFE_BUILTINS = frozenset({"echo", "cd", "true", "false", "test", "[", "pwd", ":", "printf"})
_TRACKING_STOP = frozenset({"&&", "||", "|", "&", "|&"})
_COMMAND_WORD = re.compile(r"[A-Za-z0-9_./+-][A-Za-z0-9_./+@%,:=-]*")


def _segment_allows_tracking(seg: list[str]) -> bool:
    """A segment tracking may sit beside: plain assignments, or one simple command."""
    if not seg:
        return True
    if seg[0] == "export":
        return len(seg) > 1 and all(_ASSIGN.fullmatch(w) for w in seg[1:])
    if all(_ASSIGN.fullmatch(w) for w in seg):
        return not any(w.startswith("IFS=") for w in seg)
    word = seg[0]
    # A leading redirect or a prefix assignment (`X=1 declare ...`) can hide
    # the real command word, so refuse both.
    if "=" in word or not _COMMAND_WORD.fullmatch(word) or word[0].isdigit():
        return False
    if word in _SHELL_WORDS and word not in _SAFE_BUILTINS:
        return False
    if word == "printf" and any(w.startswith("-v") for w in seg[1:]):
        return False
    return not (word == "cd" and "-" in seg[1:])


def assignment_prefix(command: str) -> int:
    """How many leading segments of `command` may set a tracked variable.

    Owner decision, 2026-11-13, narrowed twice the same day after review
    found bypasses: an assignment counts only in the plain `;`-separated
    start of a line, before any `&&`, `||`, `|` or `&` (after one of those an
    assignment may not run, or runs in a subshell). The answer is 0, and
    nothing is tracked, when the line has a newline, a subshell, or any
    segment that is not plain assignments or one simple command whose word
    is an external program or a builtin that cannot change a variable (see
    `_segment_allows_tracking`).
    """
    stripped = strip_heredocs(command)
    if "\n" in stripped.strip():
        return 0
    lexer = shlex.shlex(stripped, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return 0
    if any(t in ("(", ")") for t in tokens):
        return 0
    segments = split_segments(command) or []
    if not all(_segment_allows_tracking(seg) for seg in segments):
        return 0
    # Count the non-empty segments before the first stop operator, the way
    # `split_segments` counts them. The segment just before `&&` or `||`
    # always runs in this shell; the one before `|`, `|&` or `&` runs in a
    # subshell, so it does not count.
    count = 0
    open_segment = False
    for tok in tokens:
        if tok in _TRACKING_STOP:
            if open_segment and tok in ("&&", "||"):
                count += 1
            return count
        if tok == ";":
            if open_segment:
                count += 1
            open_segment = False
        elif tok in _OPERATORS or set(tok) <= {";"}:
            return 0  # `;;` or anything else this count does not model
        else:
            open_segment = True
    return len(segments)


def assignments(
    seg: list[str], cwd: str, assigned: dict[str, str], record: bool = True
) -> dict[str, str]:
    """The variables after this segment runs, when it only assigns them.

    Owner decision, 2026-11-13: `S=/some/dir; echo x > $S/f` should be
    inspected, not refused. Only a segment that is nothing but `NAME=value`
    words (optionally after `export`) sets anything; a prefix assignment
    (`NAME=value cmd`) lasts for `cmd` alone and is not recorded. A value
    the hook cannot resolve (`$(...)`, a backtick, an unknown variable)
    removes the name, so a later use of it is still refused. With
    `record=False` (a segment past `assignment_prefix`) every name the
    segment assigns is removed instead. Quotes are already gone (the
    segment comes from `split_segments`), and the value is expanded the way
    the shell expands an assignment: variables and a leading `~`, never
    globs.
    """
    words = seg[1:] if seg and seg[0] == "export" else seg
    if not words or not all(_ASSIGN.fullmatch(w) for w in words):
        return assigned
    out = dict(assigned)
    for w in words:
        m = _ASSIGN.fullmatch(w)
        assert m is not None
        name, raw = m.group(1), m.group(2)
        if not record or "`" in raw or "$(" in raw:
            out.pop(name, None)
            continue
        value, ok = expand_vars(raw, cwd, out)
        # A `$` left over is a substitution the lexer split apart
        # (`S=$(cmd)` tokenises as `S=$`, `(`, ...): never a known value.
        # An unquoted use of the name is word-split and globbed by bash, so
        # a value with whitespace or a glob character could name a
        # different path than the one checked (review of 2026-11-13).
        if ok and value and not _UNSAFE_VALUE.search(value):
            out[name] = value
        else:
            out.pop(name, None)
    return out


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


def shell_write_denial(rel: str, agent_type: str | None = None) -> str | None:
    """Reason a shell-level write/delete/move of `rel` must be denied.

    `agent_type` is the hook payload's field: set for a subagent, absent for
    the conductor (main session)."""
    if any(_under(rel, p) for p in PROTECTED_ALWAYS):
        return f"{rel} is fixed and may not be rewritten from the shell."
    for prefix in HARNESS_CODE:
        if _under(rel, prefix):
            return (
                f"shell write into {prefix} is blocked; use the Edit/Write tools so the "
                "path guard can see the change (harness edits are conductor-only)."
            )
    if agent_type:
        for prefix in CONDUCTOR_DOCS:
            if _under(rel, prefix):
                return (
                    f"shell write into {prefix} is blocked: it is conductor-only. "
                    "Describe the needed change in your final report."
                )
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
