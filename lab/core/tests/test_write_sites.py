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
  not spelled with `os.O_*` names;
- an aliased or star import of `os` or `shutil`, a `getattr(os, ...)` or
  `getattr(shutil, ...)`, and `__import__` / `importlib.import_module` of a
  writing module (each hides the names above from a reader);
- any import of a module whose job is writing files or running programs
  (`tempfile`, `subprocess`, `sqlite3`, `zipfile`, `tarfile`, `shelve`, `dbm`,
  `logging.handlers`), and `logging.FileHandler`, `io.FileIO`, `extractall`,
  `unpack_archive` and `make_archive` referenced anywhere;
- `.replace(target)` or `.rename(target)` with one argument, positional or
  keyword (Path's forms; `str.replace` takes two);
- the same `os` writers reached through `posix` or `nt` (what `os`
  re-exports), and the `os` functions that start programs (`system`, `popen`,
  `spawn*`, `exec*`, `posix_spawn*`, `fork`) or write otherwise (`write`,
  `chflags`, `setxattr`, `removexattr`, ...);
- `open`, `builtins.open` or `io.open` referenced without being called
  (`f = open`), `from io import open` and `from builtins import open`, calls
  to `exec`, `eval` and `compile`, and imports of `mmap` and `ctypes` (the
  latter is also the no-FFI rule, L7).

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
        "chflags",
        "lchflags",
        "setxattr",
        "removexattr",
        "write",
        "writev",
        "pwrite",
        "pwritev",
        # Launchers: a program started from here can write anything.
        "system",
        "popen",
        "posix_spawn",
        "posix_spawnp",
        "fork",
        "forkpty",
    }
)
# `posix` (POSIX) and `nt` (Windows) are what `os` re-exports; same writers.
OS_OWNERS = ("os", "posix", "nt")


def _os_writer(name: str) -> bool:
    """An `os` function that writes, or starts a program (`spawn*`, `exec*`)."""
    return name in OS_WRITERS or name.startswith(("spawn", "exec"))


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
# Modules whose job is writing files (or running programs that do).
WRITING_MODULES = frozenset(
    {
        "tempfile",
        "subprocess",
        "sqlite3",
        "zipfile",
        "tarfile",
        "shelve",
        "dbm",
        "logging.handlers",
        "mmap",
        "ctypes",  # also the no-FFI rule (L7)
    }
)
# Builtins that run code built at run time, which no scanner can read.
CODE_RUNNERS = frozenset({"exec", "eval", "compile"})
# Names that write wherever they appear.
WRITING_NAMES = frozenset({"FileHandler", "FileIO", "extractall", "unpack_archive", "make_archive"})
HIDING_OWNERS = (*OS_OWNERS, "shutil", "io", "builtins")


def _writing_module(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in WRITING_MODULES)


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
        if tok.string in REFERENCED_NAMES or tok.string in WRITING_NAMES:
            hits.append((tok.start[0], tok.string))
        elif before == "logging" and tok.string == "handlers":
            hits.append((tok.start[0], "logging.handlers"))
        elif before in OS_OWNERS and _os_writer(tok.string):
            hits.append((tok.start[0], f"{before}.{tok.string}"))
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


def _import_hits(node: ast.Import) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for alias in node.names:
        if _writing_module(alias.name):
            hits.append((node.lineno, f"import {alias.name}"))
        elif alias.name in (*OS_OWNERS, "shutil", "io", "builtins") and alias.asname:
            hits.append((node.lineno, f"import {alias.name} as {alias.asname}"))
    return hits


def _import_from_hits(node: ast.ImportFrom) -> list[tuple[int, str]]:
    module = node.module or ""
    if _writing_module(module):
        return [(node.lineno, f"from {module} import ...")]
    hits: list[tuple[int, str]] = []
    for alias in node.names:
        name = alias.name
        if module in OS_OWNERS:
            flagged = name == "*" or _os_writer(name)
        elif module == "shutil":
            flagged = name == "*" or name in SHUTIL_WRITERS or name.startswith("copy")
        elif module == "logging":
            flagged = name in ("*", "handlers") or name.endswith("Handler")
        elif module == "io":
            flagged = name in ("*", "FileIO", "open")
        elif module == "builtins":
            flagged = name in ("*", "open") or name in CODE_RUNNERS
        else:
            flagged = False
        if flagged:
            hits.append((node.lineno, f"from {module} import {name}"))
    return hits


def _ast_hits(source: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    tree = ast.parse(source)
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for node in ast.walk(tree):
        # `open` or `builtins.open` handed around instead of called (`f = open`)
        # escapes the mode check below.
        if (
            isinstance(node, ast.Name)
            and node.id == "open"
            and isinstance(node.ctx, ast.Load)
            and id(node) not in called
        ):
            hits.append((node.lineno, "open referenced, not called"))
            continue
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "open"
            and isinstance(node.value, ast.Name)
            and node.value.id in ("builtins", "io")
            and id(node) not in called
        ):
            hits.append((node.lineno, f"{node.value.id}.open referenced, not called"))
            continue
        if isinstance(node, ast.Import):
            hits.extend(_import_hits(node))
            continue
        if isinstance(node, ast.ImportFrom):
            hits.extend(_import_from_hits(node))
            continue
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        first = node.args[0] if node.args else None
        if isinstance(func, ast.Name) and func.id in CODE_RUNNERS:
            hits.append((node.lineno, f"{func.id}(...)"))
            continue
        if isinstance(func, ast.Name) and func.id == "getattr":
            # A literal name that does not write (`getattr(os, "O_BINARY", 0)`)
            # is a read; any other name, or one that is not a literal, is not.
            name = node.args[1] if len(node.args) > 1 else None
            literal = name.value if isinstance(name, ast.Constant) else None
            writes = not isinstance(literal, str) or (
                _os_writer(literal)
                or literal in SHUTIL_WRITERS | REFERENCED_NAMES | WRITING_NAMES | CODE_RUNNERS
                or literal.startswith("copy")
                or literal in ("open", "fdopen")
            )
            if isinstance(first, ast.Name) and first.id in HIDING_OWNERS and writes:
                hits.append((node.lineno, f"getattr({first.id}, ...)"))
            continue
        if (isinstance(func, ast.Name) and func.id == "__import__") or (
            isinstance(func, ast.Attribute) and func.attr == "import_module"
        ):
            literal = first.value if isinstance(first, ast.Constant) else None
            if not isinstance(literal, str) or literal in HIDING_OWNERS or _writing_module(literal):
                hits.append((node.lineno, "dynamic import"))
            continue
        if isinstance(func, ast.Name) and func.id == "open":
            if _mode_writes(_call_mode(node, 1)):
                hits.append((node.lineno, "open(write mode)"))
        elif isinstance(func, ast.Attribute):
            owner = func.value.id if isinstance(func.value, ast.Name) else None
            if owner in OS_OWNERS and func.attr == "open":
                flags = node.args[1] if len(node.args) > 1 else None
                if flags is None or _flags_write(flags):
                    hits.append((node.lineno, f"{owner}.open(write flags)"))
            elif owner in OS_OWNERS and func.attr == "fdopen":
                if _mode_writes(_call_mode(node, 1)):
                    hits.append((node.lineno, "os.fdopen(write mode)"))
            elif owner in ("io", "builtins") and func.attr == "open":
                if _mode_writes(_call_mode(node, 1)):
                    hits.append((node.lineno, f"{owner}.open(write mode)"))
            elif func.attr == "open" and owner not in ("os", "io", "builtins"):
                if _mode_writes(_call_mode(node, 0)):
                    hits.append((node.lineno, ".open(write mode)"))
            elif (
                func.attr in ("replace", "rename")
                and owner not in ("os",)
                and len(node.args) + len(node.keywords) == 1
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
    ("import-os-as", "import os as o\no.replace(a, b)\n"),
    ("import-shutil-as", "import shutil as sh\n"),
    ("from-os-import-star", "from os import *\n"),
    ("from-shutil-import-star", "from shutil import *\n"),
    ("getattr-os", "getattr(os, 'rep' + 'lace')(a, b)\n"),
    ("getattr-shutil", "getattr(shutil, name)(a, b)\n"),
    ("getattr-os-literal-writer", "getattr(os, 'unlink')(p)\n"),
    ("dunder-import", "__import__('shutil').move(a, b)\n"),
    ("import-module", "importlib.import_module('os')\n"),
    ("import-tempfile", "import tempfile\n"),
    ("from-tempfile", "from tempfile import NamedTemporaryFile\n"),
    ("import-subprocess", "import " + "subprocess\n"),
    ("import-sqlite3", "import sqlite3\n"),
    ("import-zipfile", "import zipfile\n"),
    ("import-tarfile", "import tarfile\n"),
    ("import-shelve", "import shelve\n"),
    ("import-dbm", "import dbm.dumb\n"),
    ("import-logging-handlers", "import logging.handlers\n"),
    ("from-logging-handlers", "from logging.handlers import RotatingFileHandler\n"),
    ("from-logging-import-handlers", "from logging import handlers\n"),
    ("logging-filehandler", "logging.FileHandler(p)\n"),
    ("from-logging-filehandler", "from logging import FileHandler\n"),
    ("io-fileio", "io.FileIO(p, 'w')\n"),
    ("from-io-fileio", "from io import FileIO\n"),
    ("extractall", "archive.extractall(d)\n"),
    ("unpack-archive", "unpack_archive(a, d)\n"),
    ("path-replace-keyword", "tmp.replace(target=t)\n"),
    ("path-rename-keyword", "tmp.rename(target=t)\n"),
    ("os-system", "os.system(command)\n"),
    ("os-popen", "os.popen(command)\n"),
    ("os-spawnv", "os.spawnv(os.P_WAIT, program, args)\n"),
    ("os-execv", "os.execv(program, args)\n"),
    ("os-posix-spawn", "os.posix_spawn(program, args, env)\n"),
    ("os-fork", "pid = os.fork()\n"),
    ("from-io-import-open-as", "from io import open as o\no(p, m)\n"),
    ("from-io-import-open", "from io import open\n"),
    ("from-builtins-import-open", "from builtins import open as o\n"),
    ("open-referenced", "f = open\nf(p, 'w')\n"),
    ("builtins-open-via-variable", "f = builtins.open\nf(p, 'w')\n"),
    ("builtins-open-call", "builtins.open(p, 'w')\n"),
    ("nt-remove", "import nt\nnt.remove(p)\n"),
    ("posix-unlink-imported", "from posix import unlink\n"),
    ("nt-open-flags", "nt.open(p, nt.O_WRONLY)\n"),
    ("os-chflags", "os.chflags(p, 0)\n"),
    ("os-setxattr", "os.setxattr(p, 'user.x', b'1')\n"),
    ("os-removexattr", "os.removexattr(p, 'user.x')\n"),
    ("os-write", "os.write(fd, data)\n"),
    ("import-mmap", "import mmap\n"),
    ("exec-call", "exec(source)\n"),
    ("eval-call", "eval(source)\n"),
    ("compile-call", "compile(source, name, 'exec')\n"),
    ("import-ctypes", "import ctypes\n"),
    ("from-ctypes", "from ctypes import windll\n"),
)

CONSTRUCTED_READERS: tuple[tuple[str, str], ...] = (
    ("open-default", "open(p)\n"),
    ("open-rb", "open(p, 'rb')\n"),
    ("path-open-rb", "p.open('rb')\n"),
    ("path-open-default", "p.open()\n"),
    ("os-open-rdonly", "os.open(p, os.O_RDONLY | getattr(os, 'O_BINARY', 0))\n"),
    ("str-replace", "s.replace('a', 'b')\n"),
    ("str-replace-count", "s.replace('a', 'b', 1)\n"),
    ("import-os", "import os\n"),
    ("import-logging", "import logging\nlog = logging.getLogger(__name__)\n"),
    ("getattr-other", "getattr(st, 'st_file_attributes', 0)\n"),
    ("getattr-os-flag", "getattr(os, 'O_NOFOLLOW', 0)\n"),
    ("getattr-os-binary", 'getattr(os, "O_BINARY", 0)\n'),
    ("getattr-os-listdrives", 'listdrives = getattr(os, "listdrives", None)\n'),
    ("re-compile", "pattern = re.compile(r'x')\n"),
    ("path-open-read-method", "handle = path.open\n"),
    ("os-read", "os.read(fd, 1024)\n"),
    ("import-module-other", "importlib.import_module('wowlab_core.snapshot')\n"),
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
