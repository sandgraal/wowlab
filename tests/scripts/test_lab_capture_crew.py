"""`scripts/lab_capture.py` captures a crew book like any other SavedVariables file (M21-01).

M19-04 refused any file naming `WowLabCrewDB` unless the book was empty
(ADR-0036 term 3). The owner lifted term 3 on 2026-11-23 (ADR-0036, note of
that date; ADR-0019, amendment of that date): a crew or PvP book, with other
players' names, realms, guilds, notes, ratings and match rows, may be
captured into the repository. These tests pin the new behaviour: a full book,
in the account-wide file beside `WowLabDB` as the addon writes it, goes
through the run under its own name, as a `.lua.bak` and when the automatic
selection picks it, byte for byte except each recorded player's `guid`
(`lab/addon/WowLab/Crew.lua`), which gets an invented GUID
(`Player-9998-<8 hex>`), one per distinct GUID, as another player's does in a
combat log and in `WowLabCharDB` (ADR-0019's GUID scrub, M20-13). Names,
realms, guilds, notes and ratings pass through unchanged; nothing is refused
or printed about the book. An SQLite file beside it is still refused
(`tests/scripts/test_lab_capture_sqlite.py` holds that rule's own tests).

CONSTRUCTED INPUT. Every tree and byte string here is synthetic, built in
`tmp_path` or inline; every player key, guild, note and map is invented. No
test needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_lab_capture import ACCOUNT, build_install, capture, lab_capture, outputs, tree_state

# Invented.
KEY_A = "Inventeda-Fakerealm"
KEY_B = "Inventedb-Fakerealm"
KEY_C = "Inventedc-Otherfakerealm"
GUILD = "Invented Guild Of Testing"
NOTE = "invented note for a test"
MAP = "Invented Test Valley"
# Invented player GUIDs, as the addon stores them (`entry.guid`), and what
# the capture writes in their place: numbered in order of first appearance.
GUID_A = "Player-77-0BADBEEF"
GUID_B = "Player-77-0C0FFEE0"
INVENTED_A = "Player-9998-00000001"
INVENTED_B = "Player-9998-00000002"


def _lua(*lines: str, eol: str = "\r\n") -> bytes:
    """A SavedVariables file as the client writes it: a leading blank line,
    no indentation, CRLF (LAB_FORMATS §4.2 amended)."""
    return (eol + eol.join(lines) + eol).encode()


# A schema 2 book (docs/LAB_PLAN.md §20.1, §21.2) with everything M19-04
# refused: players with GUIDs, guilds and notes, the current group, a match
# with both teams (player keys, as Crew.lua writes them), the scoreboard
# probe's block; after `WowLabDB`, in the account-wide file, as the TOC's
# `SavedVariables: WowLabDB, WowLabCrewDB` has the client write it.
BOOK = _lua(
    "WowLabDB = {",
    '["schema"] = 4,',
    "}",
    "WowLabCrewDB = {",
    '["schema"] = 2,',
    '["on"] = true,',
    '["started"] = 1794950000,',
    '["probe"] = {',
    '["loads"] = 12,',
    '["pvp"] = {',
    '["rows"] = 20,',
    '["name_string"] = true,',
    "},",
    "},",
    '["skipped"] = 0,',
    '["players"] = {',
    f'["{KEY_A}"] = {{',
    '["class"] = "MAGE",',
    f'["guid"] = "{GUID_A}",',
    '["level"] = 60,',
    f'["guild"] = "{GUILD}",',
    '["met"] = 3,',
    '["rating"] = "good",',
    f'["note"] = "{NOTE}",',
    "},",
    f'["{KEY_B}"] = {{',
    '["class"] = "PRIEST",',
    f'["guid"] = "{GUID_B}",',
    '["level"] = 58,',
    '["met"] = 1,',
    "},",
    "},",
    '["current"] = {',
    f'["{KEY_B}"] = true,',
    "},",
    '["matches"] = {',
    "{",
    '["at"] = 1795650000,',
    f'["map"] = "{MAP}",',
    '["map_id"] = 489,',
    '["kind"] = "bg",',
    '["won"] = true,',
    '["team"] = {',
    f'"{KEY_A}", -- [1]',
    "},",
    '["foes"] = {',
    f'"{KEY_C}", -- [1]',
    "},",
    "}, -- [1]",
    "},",
    '["matched"] = {',
    f'["{KEY_C}"] = 1,',
    "},",
    '["warned"] = 0,',
    "}",
)
# What the capture writes: the book with each GUID invented, nothing else changed.
CAPTURED = BOOK.replace(GUID_A.encode(), INVENTED_A.encode()).replace(
    GUID_B.encode(), INVENTED_B.encode()
)


@pytest.fixture
def install(tmp_path: Path) -> Path:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    return root


def _saved(install: Path, flavor: str = "_retail_") -> Path:
    return install / flavor / "WTF" / "Account" / ACCOUNT / "SavedVariables"


def _captured(out: Path, name: str) -> list[bytes]:
    return [c for p, c in outputs(out).items() if p.endswith(f"/SavedVariables/{name}")]


def _no_crew_refusal(printed: str) -> None:
    assert not [line for line in printed.splitlines() if line.startswith("REFUSED")], printed
    assert "crew book" not in printed.casefold()


def test_the_crew_rule_is_gone() -> None:
    for name in ("crew_book_problems", "crew_book_assignment_ok", "CREW_BOOK_NAME"):
        assert not hasattr(lab_capture, name), name
    assert lab_capture.SQLITE_LABEL  # the SQLite rule stays


def test_constructed_only_the_guids_differ() -> None:
    """The expected capture is the book with its two GUIDs invented, each
    once: putting them back gives the book byte for byte, and every name,
    realm, guild, note, rating and map is kept."""
    assert CAPTURED != BOOK
    for real, invented in ((GUID_A, INVENTED_A), (GUID_B, INVENTED_B)):
        assert BOOK.count(real.encode()) == 1 and real.encode() not in CAPTURED
        assert CAPTURED.count(invented.encode()) == 1 and invented.encode() not in BOOK
    back = CAPTURED.replace(INVENTED_A.encode(), GUID_A.encode())
    assert back.replace(INVENTED_B.encode(), GUID_B.encode()) == BOOK
    for kept in (KEY_A, KEY_B, KEY_C, GUILD, NOTE, MAP, '"good"'):
        assert kept.encode() in CAPTURED, kept


@pytest.mark.parametrize("name", ["WowLab.lua", "WowLab.lua.bak", "Renamed.lua"])
def test_constructed_a_crew_book_is_captured_unchanged_but_its_guids(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    for flavor in ("_retail_", "_classic_beta_"):
        (_saved(install, flavor) / name).write_bytes(BOOK)
    out = tmp_path / "incoming"
    before = tree_state(install)
    assert capture(install, out, "--sv", name) == 0
    assert _captured(out, name) == [CAPTURED, CAPTURED]
    captured = capsys.readouterr()
    _no_crew_refusal(captured.out + captured.err)
    assert tree_state(install) == before


def test_constructed_the_automatic_selection_may_take_a_crew_book(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing passes a crew book over: a book of a thousand players, the
    largest file, is what the automatic pick's "largest file" takes, and it
    is captured as it is but for its GUIDs, each invented once."""

    def book(guid: str) -> bytes:
        players = [
            line
            for n in range(1000)
            for line in (
                f'["Invented{n:04d}-Fakerealm"] = {{',
                f'["guid"] = "{guid % (n + 1)}",',
                f'["note"] = "{NOTE} {n}",',
                "},",
            )
        ]
        return _lua("WowLabCrewDB = {", '["players"] = {', *players, "},", "}")

    large = book("Player-77-%08X")
    for flavor in ("_retail_", "_classic_beta_"):
        (_saved(install, flavor) / "Large.lua").write_bytes(large)
    out = tmp_path / "incoming"
    assert capture(install, out) == 0
    expected = book("Player-9998-%08X")
    assert _captured(out, "Large.lua") == [expected, expected]
    captured = capsys.readouterr()
    _no_crew_refusal(captured.out + captured.err)


def test_constructed_an_sqlite_file_beside_a_crew_book_is_still_refused(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "built.sqlite"
    conn = sqlite3.connect(database, isolation_level=None)
    try:
        conn.execute("CREATE TABLE players (player_key TEXT PRIMARY KEY, note TEXT) STRICT")
        conn.execute("INSERT INTO players VALUES (?, ?)", (KEY_A, NOTE))
    finally:
        conn.close()
    saved = _saved(install)
    (saved / "WowLab.lua").write_bytes(BOOK)
    (saved / "Crew.lua").write_bytes(database.read_bytes())
    out = tmp_path / "incoming"
    assert capture(install, out, "--sv", "WowLab.lua", "--sv", "Crew.lua") == 1
    assert _captured(out, "WowLab.lua") == [CAPTURED]
    assert _captured(out, "Crew.lua") == []
    printed = capsys.readouterr()
    refusal = [
        line for line in (printed.out + printed.err).splitlines() if line.startswith("REFUSED")
    ]
    assert len(refusal) == 1
    assert refusal[0].endswith(f"/SavedVariables/Crew.lua: {lab_capture.SQLITE_LABEL}")
