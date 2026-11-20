"""`scripts/lab_capture.py` refuses any SQLite file (M20-02).

`docs/LAB_PLAN.md` §21.4 term 3, ADR-0036 term 3: wowlab's crew database
keeps other players' names and the player's notes, and no SQLite file is a
format the Lab's fixtures need. The capture tool refuses a file, whatever its
name or kind, that holds the SQLite database header or starts with a rollback
journal's header, with one fixed reason that quotes nothing; the refusal
line names the file.

CONSTRUCTED INPUT. Every database and journal is built at run time by the
standard library's `sqlite3` in `tmp_path`, holding the invented keys of
`tests/repo/test_crew_book_out_of_repo.py` (`CONSTRUCTED_KEYS`) only. No test
needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_lab_capture import ACCOUNT, build_install, capture, lab_capture, outputs, tree_state

KEYS = ("Inventeda-Fakerealm", "Inventedb-Fakerealm")
NOTE = "invented note for a test"


def _database(folder: Path) -> Path:
    path = folder / "planted.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.execute("PRAGMA application_id = 1464615746")
        conn.execute("CREATE TABLE players (player_key TEXT PRIMARY KEY, note TEXT) STRICT")
        conn.executemany("INSERT INTO players VALUES (?, ?)", [(k, NOTE) for k in KEYS])
    finally:
        conn.close()
    return path


def _journal(folder: Path) -> bytes:
    """A rollback journal, read while a transaction holds it."""
    own = folder / "journal"
    own.mkdir()
    path = _database(own)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM players WHERE player_key = ?", (KEYS[0],))
        data = path.with_name(path.name + "-journal").read_bytes()
        conn.execute("ROLLBACK")
    finally:
        conn.close()
    return data


def _kinds(folder: Path) -> dict[str, bytes]:
    db = _database(folder).read_bytes()
    journal = _journal(folder)
    return {
        "database": db,
        "journal": journal,
        "synced-journal": lab_capture.SQLITE_JOURNAL_SIGNATURE + journal[8:],
        "database-inside": b"-- text first\r\n" + db,
    }


@pytest.mark.parametrize("kind", ["database", "journal", "synced-journal", "database-inside"])
def test_constructed_sqlite_bytes_are_refused(tmp_path: Path, kind: str) -> None:
    assert lab_capture.sqlite_problems(_kinds(tmp_path)[kind]) == [lab_capture.SQLITE_LABEL]


def test_text_and_an_empty_file_are_not_sqlite() -> None:
    assert lab_capture.sqlite_problems(b"") == []
    assert lab_capture.sqlite_problems(b'WowLabDB = {\r\n["schema"] = 4,\r\n}\r\n') == []
    assert lab_capture.sqlite_problems(bytes(64)) == []  # zeros alone are no journal


@pytest.fixture
def install(tmp_path: Path) -> Path:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    return root


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("WowLab.lua", "database"),  # a renamed copy
        ("WowLab.lua.bak", "journal"),
        ("Crew.lua", "synced-journal"),
        ("Things.lua", "database-inside"),
    ],
)
def test_constructed_an_sqlite_file_is_refused_by_the_capture_run(
    install: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    kind: str,
) -> None:
    folder = tmp_path / "built"
    folder.mkdir()
    saved = install / "_retail_" / "WTF" / "Account" / ACCOUNT / "SavedVariables"
    (saved / name).write_bytes(_kinds(folder)[kind])
    out = tmp_path / "incoming"
    before = tree_state(install)
    assert capture(install, out, "--sv", name) == 1
    written = outputs(out)
    assert not [p for p in written if p.endswith(f"/SavedVariables/{name}")]
    assert all(b"SQLite format 3" not in content for content in written.values())
    captured = capsys.readouterr()
    printed = captured.out + captured.err
    refusal = [line for line in printed.splitlines() if line.startswith("REFUSED")]
    assert len(refusal) == 1, printed
    assert refusal[0].endswith(f"/SavedVariables/{name}: {lab_capture.SQLITE_LABEL}")
    for secret in (*KEYS, NOTE, "Inventeda", "Fakerealm"):
        assert secret not in printed
    assert tree_state(install) == before
