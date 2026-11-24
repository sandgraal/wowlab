"""`wowlab_core.crewbook` on constructed crew books (M19-02; L8 as ADR-0036
term 3 and docs/LAB_PLAN.md §20.5 allow it: CONSTRUCTED INPUT, every test id
says `constructed`).

No real crew record may enter the repository, so each book here is built
at run time by `_crew_build` from §20.1's record shape and appended to the
captured account `WowLab.lua` of `fixtures/macos-70888/`. The owner's
`@live` test (`test_crewbook_live.py`) is the other half of the grading.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
import unicodedata
from pathlib import Path

import pytest
from _cpu_clock import cpu_clock
from _crew_build import (
    GUILD,
    KEY_A,
    KEY_B,
    KEY_C,
    KEY_D,
    NOTE,
    T0,
    T1,
    WHERE,
    account_file,
    book,
    book_schema,  # noqa: F401  (autouse: each test at schema 1 and 2, M20-03)
    install,
    many_keys,
    player,
)

from wowlab_core import crewbook, labaddon, luadata

pytestmark = pytest.mark.parser

APOSTROPHE = chr(0x2019)


# ─── the four outcomes ───────────────────────────────────────────────────────


def test_constructed_a_file_with_no_crew_table_is_not_turned_on() -> None:
    read = crewbook.parse_book(account_file(None))
    assert read.state == "not_turned_on"
    assert read.players == [] and read.unread == [] and read.schema_number is None
    # The account record beside it still reads as before.
    assert labaddon.parse_account(account_file(None)).schema_ == 4


def test_constructed_a_nil_crew_table_is_not_turned_on() -> None:
    nil = crewbook.CREW_VARIABLE.encode("ascii") + b" = nil\r\n"
    assert crewbook.parse_book(account_file(nil)).state == "not_turned_on"


@pytest.mark.parametrize("on", [False, None], ids=["false_constructed", "absent_constructed"])
def test_constructed_on_false_or_absent_is_turned_off_with_what_it_keeps(on: bool | None) -> None:
    read = crewbook.parse_book(account_file(book({KEY_A: player(), KEY_B: player()}, on=on)))
    assert read.state == "off"
    assert [p.key for p in read.players] == [KEY_A, KEY_B]


def test_constructed_a_book_that_is_on_reads_every_field(
    book_schema: int,  # noqa: F811  (the fixture, requested by name)
) -> None:
    data = account_file(
        book(
            {KEY_A: player(rating="bad", note=NOTE), KEY_B: player(where=None, guild=None)},
            current=[KEY_A],
            skipped=4,
        )
    )
    read = crewbook.parse_book(data)
    assert read.state == "on" and read.schema_number == book_schema
    assert (read.started, read.probe_loads, read.skipped, read.current_count) == (T0, 12, 4, 1)
    a, b = read.players
    assert a.model_dump() == {
        "key": KEY_A,
        "class_token": "MAGE",
        "level": 60,
        "role": "HEALER",
        "guild": GUILD,
        "met": 3,
        "first": T0,
        "last": T1,
        "where": WHERE,
        "where_id": 2291,
        "rating": "bad",
        "note": NOTE,
        # What M20-11 records (M20-12): none in this book.
        "guid": None,
        "race": None,
        "faction": None,
        "spec": None,
        "honor_level": None,
        "score": None,
        # What the game gave for the GUID (M21-12, schema 3): none here.
        "resolved_name": None,
        "resolved_realm": None,
        "resolved_eighth": None,
        "resolved_at": None,
        "in_current_group": True,
        "other_keys": [],
    }
    assert (b.where, b.where_id, b.guild, b.rating, b.note) == (None, None, None, None, None)
    assert not b.in_current_group


def test_constructed_an_empty_book_reads_with_no_player() -> None:
    read = crewbook.parse_book(account_file(book({})))
    assert read.state == "on" and read.players == [] and read.unread == []


# M20-03: schema 2 is read now; M21-12: schema 3 too, so 4 is the next one
# refused.
@pytest.mark.parametrize("schema", [4, 0, 9999])
def test_constructed_an_unknown_schema_is_refused_with_its_number(schema: int) -> None:
    data = account_file(book({KEY_A: player()}, schema=schema))
    with pytest.raises(crewbook.CrewBookError) as caught:
        crewbook.parse_book(data)
    assert str(caught.value) == (
        "This crew book was written by another version of the lab-addon (book version "
        f"{schema}; this wowlab reads versions 1, 2 and 3). Nothing was read; updating wowlab "
        "may "
        "read it."
    )
    assert (
        caught.value.detail
        == f"WowLabCrewDB is schema {schema}; this reader knows schema 1, 2, 3 only"
    )


@pytest.mark.parametrize(
    "schema",
    ["1", 1.5, 10**6],
    ids=["text_constructed", "fraction_constructed", "huge_constructed"],
)
def test_constructed_a_schema_that_is_no_number_is_refused(schema: object) -> None:
    with pytest.raises(crewbook.CrewBookError) as caught:
        crewbook.parse_book(account_file(book({}, schema=schema)))
    assert str(caught.value) == "This crew book's version number is not readable. Nothing was read."
    assert "not a whole number" in caught.value.detail


def test_constructed_malformed_entries_are_listed_as_unread_and_the_rest_read() -> None:
    marker = "invented value never echoed"
    data = account_file(
        book(
            {
                KEY_A: player(level=marker),
                KEY_B: player(rating=marker),
                KEY_C: player(met=-1),
                KEY_D: player(note=7),
            }
        )
    )
    read = crewbook.parse_book(data)
    assert read.players == []
    assert {u.key: u.reason for u in read.unread} == {
        KEY_A: "level is not a whole number of 1 or more",
        KEY_B: 'rating is neither "good" nor "bad"',
        KEY_C: "met is not a whole number of 0 or more",
        KEY_D: "note is not text",
    }
    # The rest are read: one good entry beside a bad one.
    mixed = crewbook.parse_book(account_file(book({KEY_A: player(level=0), KEY_B: player()})))
    assert [p.key for p in mixed.players] == [KEY_B]
    assert [u.key for u in mixed.unread] == [KEY_A]
    assert all(marker not in u.reason for u in read.unread)


def test_constructed_an_entry_that_is_not_a_table_is_unread() -> None:
    data = account_file(book({KEY_A: 5, KEY_B: player()}))
    read = crewbook.parse_book(data)
    reason = "the entry is not a table"
    assert [u.reason for u in read.unread] == [reason]
    assert [p.key for p in read.players] == [KEY_B]


def test_constructed_unknown_fields_are_kept_by_name() -> None:
    read = crewbook.parse_book(account_file(book({KEY_A: player(extra_field=1)})))
    extra = "extra_field"
    assert read.players[0].other_keys == [extra]


def test_constructed_a_top_level_that_does_not_fit_is_refused() -> None:
    with pytest.raises(crewbook.CrewBookError, match="player list is not readable") as caught:
        crewbook.parse_book(account_file(book(3)))
    assert caught.value.detail.endswith(".players is not a table")
    with pytest.raises(crewbook.CrewBookError, match="on/off setting is not readable") as caught:
        crewbook.parse_book(account_file(book({}, on="yes")))  # type: ignore[arg-type]
    assert caught.value.detail.endswith(".on is neither true nor false")
    for error in (caught.value,):
        assert "WowLabCrewDB" not in str(error)


# ─── hostile text ────────────────────────────────────────────────────────────


def hostile(base: str, size: int = 0) -> str:
    """`base` with ESC, a CSI sequence, U+009B, U+202E, `|`, markup and a
    line break put in, assembled at run time; padded to `size` bytes."""
    name, realm = base.split("-")
    text = (
        name
        + "\x1b]0;x\x07"
        + "\x1b[31m"
        + chr(0x9B)
        + "2J"
        + chr(0x202E)
        + "|cffff0000|r"
        + "<b onclick=x>"
        + "\n"
        + "-"
        + realm
    )
    pad = size - len(text.encode("utf-8"))
    return text + "x" * pad if pad > 0 else text


def test_constructed_hostile_names_guilds_notes_and_places_are_read_as_stored() -> None:
    key = hostile(KEY_A, 10_000)
    assert len(key.encode("utf-8")) == 10_000
    guild, note, where = hostile(KEY_B), hostile(KEY_C), hostile(KEY_D)
    data = account_file(book({key: player(guild=guild, note=note, where=where)}, current=[key]))
    read = crewbook.parse_book(data)
    (p,) = read.players
    assert (p.key, p.guild, p.note, p.where) == (key, guild, note, where)
    assert p.in_current_group
    assert crewbook.match(read.players, key) == [p]


# ─── 5,000 players within the read budget ────────────────────────────────────

# §6.4: a 50 MB SavedVariables file parses in under 10 s. A full book is
# about 1.3 MB; its budget is that rate for its size, never under 1 s.
RATE_S_PER_BYTE = 10.0 / (50 * 1024 * 1024)


def test_constructed_five_thousand_players_read_within_the_budget() -> None:
    keys = many_keys(5000)
    players = {
        k: player(
            rating="good" if i % 7 == 0 else None,
            note=NOTE if i % 11 == 0 else None,
            last=T0 + i,
        )
        for i, k in enumerate(keys)
    }
    data = account_file(book(players, current=keys[:40]))
    clock, name = cpu_clock()
    start = clock()
    read = crewbook.parse_book(data)
    elapsed = clock() - start
    budget = max(1.0, RATE_S_PER_BYTE * len(data))
    assert len(read.players) == 5000 and read.unread == []
    assert elapsed < budget, f"{len(data)} bytes read in {elapsed:.3f} s ({name}), budget {budget}"
    shown = crewbook.ordered(crewbook.select(read.players, rated="good"), "last")
    assert len(shown) == len(range(0, 5000, 7))
    assert shown[0].last == T0 + 4998


# ─── matching, filtering, sorting ────────────────────────────────────────────


def _players(*keys: str) -> list[crewbook.CrewPlayer]:
    return [crewbook.CrewPlayer(key=k) for k in keys]


def test_constructed_a_typed_name_matches_after_nfc_casefold_spaces_and_apostrophes() -> None:
    name, realm = KEY_C.split("-")
    stored = f"{name}-{realm[:5]} {realm[5:]}'e"  # a realm with a space and an apostrophe
    composed = unicodedata.normalize("NFC", "e" + chr(0x301))
    players = _players(stored, KEY_A)
    for typed in (
        stored,
        stored.upper(),
        stored.replace(" ", ""),
        stored.replace("'", APOSTROPHE),
        stored.replace("'", ""),
        stored.lower().replace(" ", "  "),
    ):
        assert [p.key for p in crewbook.match(players, typed)] == [stored], typed
    decomposed = _players(KEY_A.replace("e", "e" + chr(0x301), 1))
    assert crewbook.match(decomposed, KEY_A.replace("e", composed, 1)) == decomposed


def test_constructed_an_exact_key_wins_and_several_folded_matches_are_all_returned() -> None:
    lower = KEY_A.lower()
    players = _players(KEY_A, lower)
    assert [p.key for p in crewbook.match(players, KEY_A)] == [KEY_A]
    assert [p.key for p in crewbook.match(players, KEY_A.upper())] == [KEY_A, lower]
    assert crewbook.match(players, KEY_B) == []


def test_constructed_select_and_order() -> None:
    a = crewbook.CrewPlayer(key=KEY_A, rating="good", last=T1, met=1)
    b = crewbook.CrewPlayer(key=KEY_B, rating="bad", note="Invented", last=T0, met=9)
    c = crewbook.CrewPlayer(key=KEY_C, guild=GUILD, met=None, last=None)
    every = [c, b, a]
    assert crewbook.select(every, rated="any") == [b, a]
    assert crewbook.select(every, rated="bad") == [b]
    assert crewbook.select(every, noted=True) == [b]
    assert crewbook.select(every, search="guild OF testing") == [c]
    assert crewbook.select(every, search="otherfake realm") == [c]
    assert [p.key for p in crewbook.ordered(every)] == [KEY_A, KEY_B, KEY_C]
    assert [p.key for p in crewbook.ordered(every, "met")] == [KEY_B, KEY_A, KEY_C]
    assert [p.key for p in crewbook.ordered(every, "name")] == [KEY_A, KEY_B, KEY_C]


# ─── read-only, root-anchored, no network ────────────────────────────────────


def test_constructed_reading_changes_nothing(tmp_path: Path) -> None:
    root = install(tmp_path, book({KEY_A: player()}))
    target = (
        root / "_forever_" / "WTF" / "Account" / "LABACCOUNTA" / "SavedVariables" / "WowLab.lua"
    )
    before = (target.read_bytes(), target.stat().st_mtime_ns, sorted(target.parent.iterdir()))
    read = crewbook.read_book(target, root=root)
    assert [p.key for p in read.players] == [KEY_A]
    assert (target.read_bytes(), target.stat().st_mtime_ns, sorted(target.parent.iterdir())) == (
        before
    )


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_constructed_a_linked_account_folder_is_refused(tmp_path: Path) -> None:
    root = install(tmp_path, book({KEY_A: player()}))
    accounts = root / "_forever_" / "WTF" / "Account"
    (accounts / "LABACCOUNTA").rename(tmp_path / "elsewhere")
    (accounts / "LABACCOUNTA").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    target = accounts / "LABACCOUNTA" / "SavedVariables" / "WowLab.lua"
    with pytest.raises(OSError):
        crewbook.read_book(target, root=root)


_NETWORK = {"socket", "ssl", "http", "urllib", "ftplib", "smtplib", "requests", "httpx"}


def test_constructed_crewbook_imports_no_network_module() -> None:
    source = Path(crewbook.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            if node.module == "wowlab_core":
                imported.update(f"wowlab_core.{a.name}" for a in node.names)
    assert not {m.split(".")[0] for m in imported} & _NETWORK
    assert "wowlab_core.gamedata" not in imported
    # In a fresh interpreter, importing it loads no network client.
    code = textwrap.dedent(
        """
        import sys
        import wowlab_core.crewbook
        print(sorted(m for m in ("wowlab_core.gamedata", "urllib.request", "http.client")
                     if m in sys.modules))
        """
    )
    done = subprocess.run(
        [sys.executable, "-P", "-c", code], capture_output=True, text=True, timeout=120, check=True
    )
    assert done.stdout.strip() == "[]"


def test_constructed_the_book_keeps_the_file_round_trip() -> None:
    """The constructed file is data `luadata` reads losslessly (L4)."""
    data = account_file(book({hostile(KEY_A): player(note=hostile(KEY_B))}))
    assert luadata.serialize(luadata.parse(data)) == data


def test_constructed_a_broken_current_set_is_named_in_player_words() -> None:
    raw = {"schema": 1, "on": True, "current": 3}
    with pytest.raises(crewbook.CrewBookError) as caught:
        crewbook.load_book(raw)
    assert str(caught.value) == (
        "The crew book's list of the players counted in the group you were in at the last "
        "save is not readable. Nothing was read."
    )
