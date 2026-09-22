"""L2, repository-wide: nothing under `lab/` writes files except the gate (M10-11).

`wowlab_core.guard` is the only code that writes into an install (ADR-0021).
Two other modules write, and only under the user data directory: `snapshot`
(its store) and `gamedata` (its cache). Tests write their synthetic trees in
`tmp_path`. Everywhere else under `lab/`, a write site is a finding unless it
is listed in `ALLOWED` below with the reason it cannot reach an install.

A write site is:

- a reference to `write_text`, `write_bytes`, `unlink`, `rmtree`, `os.replace`,
  `os.rename`, `os.remove`, `shutil.copy*` or `shutil.move` in code (comments
  and strings do not count, and a reference counts even when it is not
  called, so passing `os.unlink` as a callback is caught);
- a name imported from `os` or `shutil` that writes (`from os import replace`);
- an `open(...)`, `io.open`, `os.fdopen` or `Path.open(...)` whose mode writes
  (`w`, `a`, `x` or `+`), or whose mode is not a literal; an `os.open` whose
  flags write (`O_WRONLY`, `O_RDWR`, `O_CREAT`, `O_APPEND`, `O_TRUNC`) or are
  not spelled with `os.O_*` names.

The scanner is graded first on constructed inline sources (writers it must
find, reads it must pass) and on the exempt modules, which are full of real
write sites, so an empty result on the rest of the tree means something.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
LAB = REPO / "lab"

# Files that write on purpose, and where.
EXEMPT_FILES: dict[str, str] = {
    "lab/core/src/wowlab_core/guard.py": "the write gate into an install (ADR-0021)",
    "lab/core/src/wowlab_core/snapshot.py": "its own store under the user data directory (§6.9)",
    "lab/core/src/wowlab_core/gamedata.py": "its own cache under the user data directory (§6.6)",
}

# (repo-relative file, the offending source line stripped) -> why it is safe.
# Empty today; an entry that no longer matches a hit fails the test too.
ALLOWED: dict[tuple[str, str], str] = {}

REFERENCED_NAMES = frozenset({"write_text", "write_bytes", "unlink", "rmtree"})
OS_WRITERS = frozenset(
    {
        "replace",
        "rename",
        "renames",
        "remove",
        "removedirs",
        "rmdir",
        "unlink",
        "mkdir",
        "makedirs",
        "link",
        "symlink",
        "truncate",
        "ftruncate",
        "chmod",
        "lchmod",
        "fchmod",
        "chown",
        "lchown",
        "utime",
        "mkfifo",
        "mknod",
    }
)
SHUTIL_WRITERS = frozenset(
    {
        "copy",
        "copy2",
        "copyfile",
        "copyfileobj",
        "copymode",
        "copystat",
        "copytree",
        "move",
        "rmtree",
    }
)
WRITE_FLAGS = frozenset({"O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC"})


def _code_tokens(source: str) -> list[tokenize.TokenInfo]:
    return [
        t
        for t in tokenize.generate_tokens(io.StringIO(source).readline)
        if t.type not in (tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE)
    ]


def _token_hits(source: str) -> list[tuple[int, str]]:
    """Referenced names from the ticket's list, in code only."""
    hits: list[tuple[int, str]] = []
    tokens = _code_tokens(source)
    for i, tok in enumerate(tokens):
        if tok.type != tokenize.NAME:
            continue
        before = tokens[i - 2].string if i >= 2 and tokens[i - 1].string == "." else None
        if tok.string in REFERENCED_NAMES:
            hits.append((tok.start[0], tok.string))
        elif before == "os" and tok.string in OS_WRITERS:
            hits.append((tok.start[0], f"os.{tok.string}"))
        elif before == "shutil" and (tok.string in SHUTIL_WRITERS or tok.string.startswith("copy")):
            hits.append((tok.start[0], f"shutil.{tok.string}"))
    return hits


def _mode_writes(node: ast.expr | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return any(c in node.value for c in "wax+")
    return True  # not a literal: cannot be shown to be read-only


def _flags_write(node: ast.expr) -> bool:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
            if sub.value.id != "os":
                return True
            names.add(sub.attr)
        elif isinstance(sub, ast.Name) and sub.id not in ("os", "getattr"):
            return True
    return bool(names & WRITE_FLAGS)


def _call_mode(call: ast.Call, position: int) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == "mode":
            return kw.value
    return call.args[position] if len(call.args) > position else None


def _ast_hits(source: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module in ("os", "shutil"):
            writers = OS_WRITERS if node.module == "os" else SHUTIL_WRITERS
            for alias in node.names:
                if alias.name in writers or alias.name.startswith("copy"):
                    hits.append((node.lineno, f"from {node.module} import {alias.name}"))
            continue
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            if _mode_writes(_call_mode(node, 1)):
                hits.append((node.lineno, "open(write mode)"))
        elif isinstance(func, ast.Attribute):
            owner = func.value.id if isinstance(func.value, ast.Name) else None
            if owner == "os" and func.attr == "open":
                flags = node.args[1] if len(node.args) > 1 else None
                if flags is None or _flags_write(flags):
                    hits.append((node.lineno, "os.open(write flags)"))
            elif owner == "os" and func.attr == "fdopen":
                if _mode_writes(_call_mode(node, 1)):
                    hits.append((node.lineno, "os.fdopen(write mode)"))
            elif owner == "io" and func.attr == "open":
                if _mode_writes(_call_mode(node, 1)):
                    hits.append((node.lineno, "io.open(write mode)"))
            elif func.attr == "open" and owner not in ("os", "io"):
                if _mode_writes(_call_mode(node, 0)):
                    hits.append((node.lineno, ".open(write mode)"))
            elif (
                func.attr in ("replace", "rename")
                and owner not in ("os",)
                and len(node.args) == 1
                and not node.keywords
            ):
                # Path.replace / Path.rename take one argument; str.replace takes two.
                hits.append((node.lineno, f".{func.attr}(target)"))
            elif func.attr in ("touch", "mkdir", "rmdir", "symlink_to", "hardlink_to", "chmod"):
                if owner not in ("os",):
                    hits.append((node.lineno, f".{func.attr}()"))
    return hits


def scan(source: str) -> list[tuple[int, str]]:
    """Every write site in one module's source: (line, what)."""
    return sorted(set(_token_hits(source)) | set(_ast_hits(source)))


# ─── the scanner, on constructed sources ─────────────────────────────────────

CONSTRUCTED_WRITERS: tuple[tuple[str, str], ...] = (
    ("write-text", "p.write_text('x')\n"),
    ("write-bytes", "p.write_bytes(b'x')\n"),
    ("unlink", "p.unlink()\n"),
    ("unlink-as-callback", "cleanup(os.unlink)\n"),
    ("rmtree", "shutil.rmtree(d)\n"),
    ("os-replace", "os.replace(a, b)\n"),
    ("os-rename", "os.rename(a, b)\n"),
    ("os-remove", "os.remove(a)\n"),
    ("shutil-copy", "shutil.copy(a, b)\n"),
    ("shutil-copy2", "shutil.copy2(a, b)\n"),
    ("shutil-copytree", "shutil.copytree(a, b)\n"),
    ("shutil-move", "shutil.move(a, b)\n"),
    ("from-os-import", "from os import replace\n"),
    ("from-shutil-import", "from shutil import copyfile\n"),
    ("open-w", "open(p, 'w')\n"),
    ("open-ab", "open(p, mode='ab')\n"),
    ("open-plus", "open(p, 'r+b')\n"),
    ("open-x", "open(p, 'x')\n"),
    ("open-variable-mode", "open(p, m)\n"),
    ("io-open-w", "io.open(p, 'w')\n"),
    ("path-open-wb", "p.open('wb')\n"),
    ("path-open-mode-kw", "p.open(mode='a')\n"),
    ("os-open-creat", "os.open(p, os.O_WRONLY | os.O_CREAT)\n"),
    ("os-open-variable-flags", "os.open(p, flags)\n"),
    ("os-fdopen-w", "os.fdopen(fd, 'w')\n"),
    ("path-replace", "tmp.replace(target)\n"),
    ("path-rename", "tmp.rename(target)\n"),
    ("path-touch", "p.touch()\n"),
    ("path-mkdir", "p.mkdir(parents=True)\n"),
    ("path-symlink-to", "p.symlink_to(q)\n"),
)

CONSTRUCTED_READERS: tuple[tuple[str, str], ...] = (
    ("open-default", "open(p)\n"),
    ("open-rb", "open(p, 'rb')\n"),
    ("path-open-rb", "p.open('rb')\n"),
    ("path-open-default", "p.open()\n"),
    ("os-open-rdonly", "os.open(p, os.O_RDONLY | getattr(os, 'O_BINARY', 0))\n"),
    ("str-replace", "s.replace('a', 'b')\n"),
    ("read-bytes", "p.read_bytes()\n"),
    ("comment", "# p.unlink() would be wrong here\n"),
    ("docstring", "'''write_text, unlink and rmtree are not called here'''\n"),
)


@pytest.mark.parametrize(
    "source", [pytest.param(s, id=f"constructed-{name}") for name, s in CONSTRUCTED_WRITERS]
)
def test_constructed_scanner_finds_a_write_site(source: str) -> None:
    assert scan(source), f"missed: {source!r}"


@pytest.mark.parametrize(
    "source", [pytest.param(s, id=f"constructed-{name}") for name, s in CONSTRUCTED_READERS]
)
def test_constructed_scanner_passes_a_read(source: str) -> None:
    assert scan(source) == []


def test_scanner_sees_the_gates_own_writes() -> None:
    """Positive control on real code: the exempt modules are full of hits."""
    for rel in EXEMPT_FILES:
        assert scan((REPO / rel).read_text(encoding="utf-8")), rel


# ─── the tree ────────────────────────────────────────────────────────────────


def _library_files() -> list[Path]:
    return sorted(
        p
        for p in LAB.rglob("*.py")
        if "tests" not in p.relative_to(LAB).parts
        and p.relative_to(REPO).as_posix() not in EXEMPT_FILES
    )


def test_nothing_under_lab_writes_outside_the_gate() -> None:
    """L2: every write site outside guard, snapshot, gamedata and tests is
    allowlisted with a reason, and every allowlist entry is still needed."""
    assert _library_files(), "positive control: there is library code to scan"
    for rel in EXEMPT_FILES:
        assert (REPO / rel).is_file(), f"exempt file {rel} no longer exists"

    found: dict[tuple[str, str], list[str]] = {}
    for path in _library_files():
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        rel = path.relative_to(REPO).as_posix()
        for lineno, what in scan(source):
            found.setdefault((rel, lines[lineno - 1].strip()), []).append(f"{lineno}: {what}")

    unexplained = {key: sites for key, sites in found.items() if key not in ALLOWED}
    assert unexplained == {}, (
        "write sites outside guard.py (L2, ADR-0021); route the write through "
        "`wowlab_core.guard` or allowlist it here with the reason it cannot "
        f"reach an install: {unexplained}"
    )
    stale = sorted(set(ALLOWED) - set(found))
    assert stale == [], f"allowlist entries that match nothing: {stale}"
    assert all(reason.strip() for reason in ALLOWED.values()), "every entry needs a reason"
