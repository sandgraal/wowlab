"""No SQLite file is tracked in the repository (M20-02).

`docs/LAB_PLAN.md` §21.4 term 3, ADR-0036 term 3, ADR-0039: wowlab's crew
database (`<user data>/wowlab/crew/<flavor folder>/<account folder>.sqlite`)
keeps other players' names, realms, guilds and the player's notes, and never
enters the repository. Every tracked file goes through the unpacking of
`test_crew_book_out_of_repo.py` (every member of a gzip, bzip2 or xz stream or
a zip archive, by content, recursively), and each member is refused:

- when it holds the SQLite database header (`SQLite format 3` and a NUL)
  anywhere: a database whatever its name, and a rollback journal or a
  write-ahead log, which hold copies of the database's first page;
- when it starts with a rollback journal's header;
- when its tracked name (or a member's) holds `.sqlite` in any casing: the
  database, `-journal`, `.daily-<date>` and `.before-schema-<n>` copies;

outside the listed `db2lake` test paths (`DB2LAKE_TEST_PATHS`), whatever its
`application_id`. And everywhere, those paths included: a database header
followed, at its offset 68, by the crew database's `application_id`.

CONSTRUCTED INPUT. Every planted database and journal is built at run time
by the standard library's `sqlite3` in `tmp_path`, holding the invented keys
of M19-04 only, and fed to `scan` in memory; nothing is written to the tree.
"""

from __future__ import annotations

import gzip
import io
import shutil
import sqlite3
import zipfile
from collections.abc import Iterable
from pathlib import Path

import pytest
from test_crew_book_out_of_repo import _members, tracked

# The paths `db2lake`'s tests may keep an SQLite file at. Empty: `db2lake`
# (ADR-0028) is not built; its ticket lists its test paths here.
DB2LAKE_TEST_PATHS: frozenset[str] = frozenset()
# The header every SQLite database starts with (assembled, so this file does
# not hold it).
SIGNATURE = b"SQLite format " + b"3\x00"
JOURNAL_SIGNATURE = bytes.fromhex("d9d505f920a163d7")
APP_ID_OFFSET = 68
# `crewdb.APPLICATION_ID` (0x574C4342), as SQLite stores it (big-endian).
CREW_APP_ID = (0x574C4342).to_bytes(4, "big")
SUFFIX = ".sqlite"

# Invented, from CONSTRUCTED_KEYS in tests/repo/test_crew_book_out_of_repo.py.
KEYS = (
    "Inventeda-Fakerealm",
    "Inventedb-Fakerealm",
    "Inventedc-Otherfakerealm",
    "Inventedd-Otherfakerealm",
)


def _journal(data: bytes) -> bool:
    """Whether `data` has a rollback journal's header: its signature, or
    the eight zero bytes it holds until it is first synced, then a sector
    size and a page size that are powers of two from 512 to 65536, and at
    least one sector and one page record after it. A journal written before
    a commit began holds no database header, so it is found only here."""
    if len(data) < 28 or data[:8] not in (JOURNAL_SIGNATURE, bytes(8)):
        return False
    sector = int.from_bytes(data[20:24], "big")
    page = int.from_bytes(data[24:28], "big")

    def size(n: int) -> bool:
        return 512 <= n <= 65536 and n & (n - 1) == 0

    return size(sector) and size(page) and len(data) >= sector + page + 8


def _crew_app_id(data: bytes) -> bool:
    """Whether a database header in `data` carries the crew database's
    `application_id`."""
    at = data.find(SIGNATURE)
    while at >= 0:
        if data[at + APP_ID_OFFSET : at + APP_ID_OFFSET + 4] == CREW_APP_ID:
            return True
        at = data.find(SIGNATURE, at + 1)
    return False


def scan(entries: Iterable[tuple[str, bytes]]) -> list[str]:
    """Every problem in (tracked path, bytes) pairs, by path only."""
    problems: list[str] = []
    for path, data in entries:
        allowed = path in DB2LAKE_TEST_PATHS
        for label, content in _members(path, data):
            if _crew_app_id(content):
                problems.append(f"{label}: an SQLite file carrying the crew database's id")
                continue
            if allowed:
                continue
            if SIGNATURE in content:
                problems.append(f"{label}: an SQLite database (by its header)")
            elif _journal(content):
                problems.append(f"{label}: an SQLite rollback journal (by its header)")
            elif SUFFIX in label.casefold():
                problems.append(f"{label}: an SQLite file (by its name)")
    return problems


# ─── the repository ──────────────────────────────────────────────────────────


def test_no_sqlite_file_is_tracked() -> None:
    entries = tracked()
    assert len(entries) > 100
    assert scan(entries) == []


def test_the_db2lake_paths_are_tests() -> None:
    for path in DB2LAKE_TEST_PATHS:
        assert "/tests/" in f"/{path}" and "db2lake" in path, path


def test_the_id_is_crewdbs() -> None:
    from wowlab_core import crewdb

    assert crewdb.APPLICATION_ID.to_bytes(4, "big") == CREW_APP_ID
    assert SIGNATURE == b"SQLite format 3\x00"


# ─── planted, constructed ────────────────────────────────────────────────────


def _database(folder: Path, *, app_id: int | None = 0x574C4342) -> Path:
    """A database built the way the crew database is (rollback journal,
    `secure_delete`), holding the four invented keys."""
    path = folder / "planted.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode = DELETE")
        if app_id is not None:
            conn.execute(f"PRAGMA application_id = {app_id:d}")
        conn.execute("CREATE TABLE players (player_key TEXT PRIMARY KEY) STRICT")
        conn.executemany("INSERT INTO players VALUES (?)", [(k,) for k in KEYS])
    finally:
        conn.close()
    return path


def _hot_journal(folder: Path) -> bytes:
    """The bytes of a rollback journal, read while a transaction holds it."""
    path = _database(folder)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM players WHERE player_key = ?", (KEYS[0],))
        journal = path.with_name(path.name + "-journal").read_bytes()
        conn.execute("ROLLBACK")
    finally:
        conn.close()
    return journal


def test_constructed_a_planted_database_fails(tmp_path: Path) -> None:
    data = _database(tmp_path).read_bytes()
    assert scan([("lab/core/tests/fixtures/planted.sqlite", data)]) != []


def test_constructed_a_planted_rollback_journal_fails(tmp_path: Path) -> None:
    """A journal read mid-transaction (its header not yet synced, so zero
    where the signature goes), and the same with the signature a journal
    left by a crash during a commit holds; both under a journal's name and
    renamed. Neither holds the database header: the journal's own header
    finds them."""
    journal = _hot_journal(tmp_path)
    assert KEYS[0].encode() in journal and SIGNATURE not in journal
    synced = JOURNAL_SIGNATURE + journal[len(JOURNAL_SIGNATURE) :]
    for data in (journal, synced):
        for name in ("x/planted.sqlite-journal", "x/renamed.bin"):
            assert scan([(name, data)]) != [], name


@pytest.mark.parametrize(
    "name", ["docs/notes.txt", "lab/core/tests/fixtures/README.md", "a/b/WowLab.lua"]
)
def test_constructed_a_renamed_copy_fails(tmp_path: Path, name: str) -> None:
    data = _database(tmp_path).read_bytes()
    problems = scan([(name, data)])
    assert problems and all(p.startswith(name) for p in problems)
    for key in KEYS:
        assert all(key not in p for p in problems)


def test_constructed_a_daily_copy_by_name_fails(tmp_path: Path) -> None:
    db = _database(tmp_path)
    copy = tmp_path / "planted.sqlite.daily-2026-11-19"
    shutil.copyfile(db, copy)
    assert scan([(f"x/{copy.name}", copy.read_bytes())]) != []
    assert scan([("x/empty.SQLite3", b"")]) != []  # by name alone, in any casing


def test_constructed_a_compressed_database_fails(tmp_path: Path) -> None:
    data = _database(tmp_path).read_bytes()
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as archive:
        archive.writestr("inner.bin", gzip.compress(data))
    assert scan([("x/bundle.zip", packed.getvalue())]) != []


def test_constructed_any_application_id_fails_outside_the_listed_paths(tmp_path: Path) -> None:
    for app_id in (None, 7):
        folder = tmp_path / f"id-{app_id}"
        folder.mkdir()
        data = _database(folder, app_id=app_id).read_bytes()
        assert scan([("x/other.db", data)]) != [], app_id


def test_constructed_the_crew_id_fails_even_in_a_listed_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed = "lab/db2lake/tests/fixtures/tables.sqlite"
    monkeypatch.setattr(
        __import__(__name__, fromlist=["DB2LAKE_TEST_PATHS"]),
        "DB2LAKE_TEST_PATHS",
        frozenset({listed}),
    )
    other = tmp_path / "other"
    other.mkdir()
    assert scan([(listed, _database(other, app_id=7).read_bytes())]) == []
    crew = tmp_path / "crew"
    crew.mkdir()
    assert scan([(listed, _database(crew).read_bytes())]) != []
