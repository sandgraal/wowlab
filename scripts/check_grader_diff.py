"""CI gate: graders are activated by marker deletion only (M16-40, ADR-0013).

On a change that touches a load-bearing file (`luadata.py`, `guard.py`, and
`proposal.py` once it exists, under `lab/core/src/wowlab_core/`), every
grader that existed at the base revision may change in one way only: the
removal of complete `xfail(strict=True)` marker blocks. Any other change to
a grader (a line added or edited, a test deleted, `strict=False`, a `skip`
added, a marker only partly removed, the file deleted) fails the gate.

What a grader is. A tracked file at the base revision, under a `tests/`
directory and named `test_*.py`, that matches at least one of these, read
from its base and its head contents:

1. its name names a changed load-bearing module as a whole `_`-separated
   word (`test_guard.py`, `test_luadata_fixtures.py`,
   `test_m13_21_parity_misses_guard_drift.py`);
2. it imports a changed load-bearing module (`import wowlab_core.guard`,
   `from wowlab_core.guard import ...`, `from wowlab_core import guard`,
   including the parenthesised multi-line form);
3. it carries a strict xfail marker anywhere (a pending grader on `main`,
   whatever file it grades: AGENTS.md has those activated the same way).

A file that does not parse as Python is treated as a grader (fail closed).
New files are not graders here: the rule is about tests already on `main`.

What a marker block is, found in the base contents with `ast` (no code is
run): whole lines holding exactly one of

- a decorator `@<...>.mark.xfail(..., strict=True, ...)`, on one line or
  many;
- a decorator `@NAME`, where `NAME = <...>.mark.xfail(..., strict=True, ...)`
  is a module-level assignment;
- that module-level assignment itself (also `pytestmark = ...`);
- a keyword argument `marks=<strict xfail call or such a NAME>`, with an
  optional trailing comma.

The head contents pass when they equal the base contents with some set of
those blocks removed and nothing else changed, byte for byte.

Usage: check_grader_diff.py [--repo DIR] [--base REV] [--head REV]
(defaults `HEAD^1` and `HEAD`: on a pull request's merge commit, the base
branch and the merged result). Exit 0 when the gate passes or no
load-bearing file changed, 1 on a violation, 2 when git cannot answer.

Standard library only, Python 3.9 or later: CI runs it on the runner's own
`python3` with `-I`, from the base revision's copy, so a pull request cannot
loosen the check it is graded by.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

LIB = "lab/core/src/wowlab_core/"
# The ADR-0013 set: the two files it names and the 2026-10-21 amendment's
# `proposal.py`. A module that becomes a package (`guard/`) stays in the set.
LOAD_BEARING = ("luadata", "guard", "proposal")
PACKAGE = "wowlab_core"

# Bound on the removal search; a real grader needs a few thousand states.
MAX_STATES = 2_000_000

_WS = rb"[ \t\f]*"
_EOL = rb"(?:#[^\r\n]*)?(?:\r?\n)?"
_DECORATOR_PREFIX = re.compile(rb"\A" + _WS + rb"@" + _WS + rb"\Z")
_PLAIN_PREFIX = re.compile(rb"\A" + _WS + rb"\Z")
_LINE_END = re.compile(rb"\A" + _WS + _EOL + rb"\Z")
_KEYWORD_END = re.compile(rb"\A" + _WS + rb",?" + _WS + _EOL + rb"\Z")


# ─── the marker grammar ──────────────────────────────────────────────────────


def is_strict_xfail(node: ast.AST) -> bool:
    """`<anything>.mark.xfail(...)` or `mark.xfail(...)` with a literal `strict=True`."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "xfail"):
        return False
    owner = func.value
    if not (
        (isinstance(owner, ast.Attribute) and owner.attr == "mark")
        or (isinstance(owner, ast.Name) and owner.id == "mark")
    ):
        return False
    return any(
        kw.arg == "strict" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in node.keywords
    )


def split_lines(text: str) -> list[str]:
    """Lines as `ast` counts them: split after each `\\n`, line ends kept."""
    lines = text.split("\n")
    out = [line + "\n" for line in lines[:-1]]
    if lines[-1]:
        out.append(lines[-1])
    return out


def _whole_lines(
    lines: Sequence[str], node: ast.AST, prefix: re.Pattern[bytes], suffix: re.Pattern[bytes]
) -> tuple[int, int] | None:
    """The 0-based [start, end) line range `node` fills, or None if it shares a line."""
    lineno = getattr(node, "lineno", None)
    end_lineno = getattr(node, "end_lineno", None)
    col = getattr(node, "col_offset", None)
    end_col = getattr(node, "end_col_offset", None)
    if lineno is None or end_lineno is None or col is None or end_col is None:
        return None
    if not 1 <= lineno <= end_lineno <= len(lines):
        return None
    # `ast` column offsets count UTF-8 bytes.
    first = lines[lineno - 1].encode("utf-8", "surrogateescape")
    last = lines[end_lineno - 1].encode("utf-8", "surrogateescape")
    if not prefix.match(first[:col]) or not suffix.match(last[end_col:]):
        return None
    return lineno - 1, end_lineno


@dataclass
class Markers:
    """What one file's base contents say about strict xfail markers."""

    blocks: set[tuple[int, int]] = field(default_factory=set)
    any_marker: bool = False


def find_markers(text: str) -> Markers:
    """Every complete marker block in `text`. Raises SyntaxError and the like."""
    tree = ast.parse(text)
    lines = split_lines(text)
    found = Markers()
    aliases: set[str] = set()
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and is_strict_xfail(stmt.value)
        ):
            aliases.add(stmt.targets[0].id)
            block = _whole_lines(lines, stmt, _PLAIN_PREFIX, _LINE_END)
            if block:
                found.blocks.add(block)

    def marks(node: ast.AST) -> bool:
        return is_strict_xfail(node) or (isinstance(node, ast.Name) and node.id in aliases)

    for node in ast.walk(tree):
        if is_strict_xfail(node):
            found.any_marker = True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                if marks(dec):
                    block = _whole_lines(lines, dec, _DECORATOR_PREFIX, _LINE_END)
                    if block:
                        found.blocks.add(block)
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "marks" and marks(kw.value):
                    block = _whole_lines(lines, kw, _PLAIN_PREFIX, _KEYWORD_END)
                    if block:
                        found.blocks.add(block)
    return found


# ─── the diff check ──────────────────────────────────────────────────────────


class TooComplexError(Exception):
    """The removal search passed MAX_STATES; the gate fails closed."""


def removal_only(
    base: Sequence[str], head: Sequence[str], blocks: Iterable[tuple[int, int]]
) -> bool:
    """True when `head` is `base` with some of `blocks` removed and nothing else changed.

    A search over (base line, head line) pairs: a step either matches equal
    lines or skips one whole block at the current base line. Exact, whatever
    alignment a diff tool would have picked."""
    starts: dict[int, list[int]] = {}
    for start, end in blocks:
        starts.setdefault(start, []).append(end)
    n, m = len(base), len(head)
    stack = [(0, 0)]
    seen: set[tuple[int, int]] = set()
    while stack:
        state = stack.pop()
        if state in seen:
            continue
        seen.add(state)
        if len(seen) > MAX_STATES:
            raise TooComplexError
        i, j = state
        if i == n and j == m:
            return True
        for end in starts.get(i, ()):
            stack.append((end, j))
        if i < n and j < m and base[i] == head[j]:
            stack.append((i + 1, j + 1))
    return False


def _covered(start: int, end: int, blocks: set[tuple[int, int]]) -> bool:
    """[start, end) is exactly a run of whole blocks."""
    at = start
    while at < end:
        nxt = [e for s, e in blocks if s == at and e <= end]
        if not nxt:
            return False
        at = max(nxt)
    return at == end


def _show(line: str) -> str:
    # Indented and repr-escaped: a grader line can never start a GitHub
    # workflow command (`::...`) or carry a control character into the log.
    return repr(line)


def explain(base: Sequence[str], head: Sequence[str], blocks: set[tuple[int, int]]) -> list[str]:
    """The hunks that are not marker removal, for the log."""
    out: list[str] = []
    matcher = difflib.SequenceMatcher(None, base, head, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete" and _covered(i1, i2, blocks):
            continue
        if tag == "delete":
            what = "deletes lines that are not a complete xfail(strict=True) marker block"
        elif tag == "insert":
            what = "adds lines"
        else:
            what = "changes lines"
        out.append(f"  hunk at base line {i1 + 1}, head line {j1 + 1}: {what}")
        out.extend(f"      - {_show(line)}" for line in base[i1:i2])
        out.extend(f"      + {_show(line)}" for line in head[j1:j2])
    return out


@dataclass
class Verdict:
    ok: bool
    lines: list[str]


def _decode(data: bytes) -> str:
    text = data.decode("utf-8")
    if re.search(r"\r(?!\n)", text):
        # `ast` counts a lone CR as a line end and split_lines does not; the
        # line numbers would disagree, so refuse rather than guess.
        raise ValueError("a lone carriage return")
    return text


def check_file(base_data: bytes, head_data: bytes | None) -> Verdict:
    """Grade one grader's change from `base_data` to `head_data` (None: deleted)."""
    if head_data is None:
        return Verdict(False, ["  the grader is deleted"])
    if head_data == base_data:
        return Verdict(True, [])
    try:
        base_text = _decode(base_data)
        head_text = _decode(head_data)
    except ValueError as exc:
        return Verdict(False, [f"  not readable as UTF-8 Python source ({exc})"])
    try:
        blocks = find_markers(base_text).blocks
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        return Verdict(False, [f"  the base version does not parse ({type(exc).__name__})"])
    base_lines, head_lines = split_lines(base_text), split_lines(head_text)
    try:
        ok = removal_only(base_lines, head_lines, blocks)
    except TooComplexError:
        return Verdict(False, ["  the change is too large to check; treated as a violation"])
    if ok:
        removed = len(base_lines) - len(head_lines)
        return Verdict(True, [f"  marker removal only ({removed} line(s) removed)"])
    return Verdict(False, explain(base_lines, head_lines, blocks))


# ─── which files are graders ─────────────────────────────────────────────────


def load_bearing_module(path: str) -> str | None:
    """The load-bearing module `path` belongs to, or None."""
    if not path.startswith(LIB):
        return None
    rest = path[len(LIB) :]
    for name in LOAD_BEARING:
        if rest == f"{name}.py" or rest.startswith(f"{name}/"):
            return name
    return None


def is_test_file(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return "tests" in parts[:-1] and name.startswith("test_") and name.endswith(".py")


def imported_modules(text: str) -> set[str]:
    """Load-bearing modules `text` imports from `wowlab_core`."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if len(parts) >= 2 and parts[0] == PACKAGE and parts[1] in LOAD_BEARING:
                    found.add(parts[1])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            parts = node.module.split(".")
            if parts[0] != PACKAGE:
                continue
            if len(parts) >= 2 and parts[1] in LOAD_BEARING:
                found.add(parts[1])
            elif len(parts) == 1:
                found.update(a.name for a in node.names if a.name in LOAD_BEARING)
    return found


def grader_reason(path: str, contents: Sequence[bytes], modules: set[str]) -> str | None:
    """Why `path` is a grader of `modules`, or None. `contents`: its base and head bytes."""
    if not is_test_file(path):
        return None
    tokens = set(Path(path).stem.split("_"))
    named = sorted(tokens & modules)
    if named:
        return f"named for {', '.join(named)}"
    for data in contents:
        try:
            text = _decode(data)
            imports = imported_modules(text) & modules
            pending = find_markers(text).any_marker
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return "does not parse; treated as a grader"
        if imports:
            return f"imports {', '.join(sorted(imports))}"
        if pending:
            return "carries a strict xfail marker"
    return None


# ─── git ─────────────────────────────────────────────────────────────────────


class GitError(Exception):
    pass


def _git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args[:2])} failed: {proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    return proc.stdout


def _paths(raw: bytes) -> list[str]:
    return [p.decode("utf-8", "surrogateescape") for p in raw.split(b"\0") if p]


def _blob(repo: Path, rev: str, path: str, tracked: set[str]) -> bytes | None:
    if path not in tracked:
        return None
    return _git(repo, "cat-file", "blob", f"{rev}:{path}")


def run(repo: Path, base: str, head: str) -> tuple[int, list[str]]:
    base_sha = _git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").decode().strip()
    head_sha = _git(repo, "rev-parse", "--verify", f"{head}^{{commit}}").decode().strip()
    out = [f"grader diff gate (M16-40): base {base_sha} head {head_sha}"]
    changed = _paths(
        _git(repo, "diff", "--name-only", "--no-renames", "-z", base_sha, head_sha, "--")
    )
    modules = {m for m in map(load_bearing_module, changed) if m}
    if not modules:
        out.append("no load-bearing file changed; nothing to check")
        return 0, out
    touched = sorted(p for p in changed if load_bearing_module(p))
    out.append(f"load-bearing files changed: {', '.join(_show(p) for p in touched)}")
    base_tracked = set(_paths(_git(repo, "ls-tree", "-r", "-z", "--name-only", base_sha)))
    head_tracked = set(_paths(_git(repo, "ls-tree", "-r", "-z", "--name-only", head_sha)))
    failures = 0
    checked = 0
    for path in sorted(changed):
        if path not in base_tracked or not is_test_file(path):
            continue
        base_data = _blob(repo, base_sha, path, base_tracked)
        head_data = _blob(repo, head_sha, path, head_tracked)
        assert base_data is not None
        contents = [base_data] if head_data is None else [base_data, head_data]
        reason = grader_reason(path, contents, modules)
        if reason is None:
            continue
        checked += 1
        verdict = check_file(base_data, head_data)
        out.append(f"{'ok  ' if verdict.ok else 'FAIL'} {_show(path)} ({reason})")
        out.extend(verdict.lines)
        failures += not verdict.ok
    out.append(f"graders changed: {checked}; violations: {failures}")
    if failures:
        out.append(
            "A grader of a load-bearing file may change only by deleting complete "
            "xfail(strict=True) marker blocks (ADR-0013, AGENTS.md). A grader that is "
            "wrong is reported, and fixed by a separate [TEST] change."
        )
        return 1, out
    return 0, out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", default="HEAD^1")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    try:
        code, lines = run(args.repo, args.base, args.head)
    except GitError as exc:
        print(f"grader diff gate: {exc}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
