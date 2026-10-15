# Probe from review of m12/03-db2lake; reproduces Lake.query creating a file inside a game install (ATTACH, VACUUM INTO) despite query_only.
"""`Lake.query` runs a statement on the lake's read-write connection, whose
only guard outside a load is `PRAGMA query_only = ON`. `query_only` stops
changes to the attached databases; it does not stop SQLite from opening, and
so creating, another file: `ATTACH DATABASE '<path>'` creates `<path>` (the
connection was opened `mode=rwc`, and an attachment inherits that), and
`VACUUM INTO '<path>'` creates `<path>` before it fails on `query_only`. Either
path can be inside a game install.

What should be true: L1 (`AGENTS.md`): no module but `guard` creates any file
inside an install. The allowlist entry that admits `import sqlite3` in
`db2lake.py` (`lab/core/tests/test_write_sites.py`, `_LAKE_DIR`) says the lake
writes "only the lake directory ... and <build>.sqlite in it (with SQLite's
-journal beside it)", and the §14.2 amendment of 2026-09-30 presents
`query_only` as what keeps `Lake.query` read-only. SQLite's authorizer sees
both statements as `SQLITE_ATTACH` (action 24) with the file name, so the
lake's connection can refuse them outside `Lake.attach`.

All inputs are constructed (labelled): a two-line CSV served through a
`Source` stub, and a synthetic install (a folder holding `.build.info`) under
`tmp_path`. Nothing touches a real install, the user data directory or the
network.

Positive controls: on a bare `sqlite3` connection with `query_only` on, each
statement does create its file (so the tree comparison can see one, and
`query_only` alone is not a guard); the lake's own `Lake.attach` of another
build still works (a fix must keep it).
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import BinaryIO

import pytest

from wowlab_core.db2lake import Lake, LakeError
from wowlab_core.gamedata import GameData

BUILD = "9.9.9.1"  # constructed
OTHER = "9.9.9.2"  # constructed
TABLE = "Constructed"


class _Table:
    name = "constructed"

    def fetch_builds(self, dest: BinaryIO) -> str:
        raise AssertionError("not needed")

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        dest.write(b"ID,Value\n1,2\n")
        return f"constructed:{table}"


def _lake(tmp_path: Path, build: str = BUILD) -> Lake:
    data = GameData(_Table(), cache_dir=tmp_path / "cache")
    return Lake(build, gamedata=data, lake_dir=tmp_path / "lake")


def _install(tmp_path: Path) -> Path:
    root = tmp_path / "install"
    (root / "_flavor_").mkdir(parents=True)
    (root / ".build.info").write_bytes(b"constructed\n")
    return root


def _tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


STATEMENTS = [
    pytest.param("ATTACH DATABASE ? AS probe", id="constructed-attach"),
    pytest.param("VACUUM INTO ?", id="constructed-vacuum-into"),
]


@pytest.mark.parametrize("sql", STATEMENTS)
def test_query_creates_no_file_inside_an_install(tmp_path: Path, sql: str) -> None:
    install = _install(tmp_path)
    before = _tree(install)
    target = install / "_flavor_" / "probe.sqlite"
    with _lake(tmp_path) as lake:
        lake.load(TABLE)
        # A refusal is the expected outcome; the file check decides.
        with contextlib.suppress(LakeError, ValueError):
            list(lake.query(sql, (str(target),)))
    assert _tree(install) == before, (
        f"{sql!r} through Lake.query created {sorted(set(_tree(install)) - set(before))} "
        "inside the install (L1)"
    )


@pytest.mark.parametrize("sql", STATEMENTS)
def test_positive_control_query_only_alone_lets_sqlite_create_the_file(
    tmp_path: Path, sql: str
) -> None:
    main = tmp_path / "main.sqlite"
    plain = tmp_path / "plain"
    plain.mkdir()
    target = plain / "probe.sqlite"
    connection = sqlite3.connect(f"{main.as_uri()}?mode=rwc", uri=True, isolation_level=None)
    try:
        connection.execute("CREATE TABLE t (x INTEGER) STRICT")
        connection.execute("PRAGMA query_only = ON")
        with contextlib.suppress(sqlite3.Error):
            connection.execute(sql, (str(target),))
    finally:
        connection.close()
    assert _tree(plain) == ["probe.sqlite"]


def test_positive_control_lake_attach_still_works(tmp_path: Path) -> None:
    with _lake(tmp_path, OTHER) as other:
        other.load(TABLE)
    with _lake(tmp_path) as lake:
        lake.load(TABLE)
        alias = lake.attach(OTHER)
        rows = list(lake.query(f'SELECT "Value" FROM "{alias}"."{TABLE}"'))
    assert rows == [(2,)]
