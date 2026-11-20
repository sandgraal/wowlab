"""Schema 2 of the crew book's saved file at the desk (M20-03; docs/LAB_PLAN.md
§21.2): `crewbook` reads it, `crewedit` writes back the schema it read, and
`crewcount` counts a player a match names.

CONSTRUCTED INPUT, every test id says `constructed` (L8 as ADR-0036 term 3
and §20.5 allow it): no real schema 2 file exists yet (the addon's schema 2
is M20-05 and the owner's in-game check M20-06), and no real crew record may
enter the repository. Each book is built at run time by `_crew_build` from
§21.2's shape, appended to the captured account `WowLab.lua`. The four
invented keys are the only player keys. The M19 tests of these modules run
on a schema 1 and a schema 2 book through `_crew_build.book_schema`; these
are the cases only schema 2 has.
"""

from __future__ import annotations

from typing import Any

import pytest
from _cpu_clock import cpu_clock
from _crew_build import (
    KEY_A,
    KEY_B,
    KEY_C,
    KEY_D,
    MAP,
    MAP_ID,
    NOTE,
    T1,
    T2,
    account_file,
    book,
    many_keys,
    match,
    player,
    probe_run,
)

from wowlab_core import crewbook, crewcount, crewedit, luadata

pytestmark = pytest.mark.parser

PVP = {
    "allied": 4,
    "faced": 2,
    "allied_won": 3,
    "faced_won": 1,
    "pvp_last": T2,
    "pvp_map": MAP,
    "pvp_side": "against",
}


def _book2(players: dict[str, Any], **more: Any) -> bytes:
    """A schema 2 account file holding `players` and `more`."""
    return account_file(book(players, schema=2, **more))


def _crew(data: bytes) -> dict[str, Any]:
    """The book as loaded data."""
    value = luadata.parse(data).to_python()[crewbook.CREW_VARIABLE]
    assert isinstance(value, dict)
    return value


# ─── reading ─────────────────────────────────────────────────────────────────


def test_constructed_a_schema_2_book_reads_every_new_field() -> None:
    data = _book2(
        {KEY_A: player(**PVP), KEY_B: player()},
        matches=[match([KEY_A], [KEY_B, KEY_C]), match([KEY_B], [], won=None, kind="arena")],
        matched={"map_id": MAP_ID, "at": T2},
        probe={"loads": 3, "pvp": [probe_run(), probe_run(winner=1)]},
    )
    read = crewbook.parse_book(data)
    assert read.schema_number == 2 and read.warned == T1 and read.matched
    a, b = read.players
    assert a.pvp == crewbook.CrewPvp(**PVP) and a.other_keys == []
    assert b.pvp is None
    # Not in `--json` (the database, not the book, keeps the totals).
    assert "pvp" not in a.model_dump()
    first, second = read.matches
    assert (first.position, first.at, first.map, first.map_id, first.kind, first.won) == (
        1,
        T2,
        MAP,
        MAP_ID,
        "bg",
        True,
    )
    assert (first.team, first.foes) == ([KEY_A], [KEY_B, KEY_C])
    assert (second.kind, second.won, second.team, second.foes) == ("arena", None, [KEY_B], [])
    assert read.matches_unread == []
    assert read.match_keys() == [KEY_A, KEY_B, KEY_C]
    assert read.probe_loads == 3 and len(read.probe_pvp) == 2


def test_constructed_an_empty_schema_2_book_reads_as_the_migration_leaves_it() -> None:
    """The addon's migration adds nothing but the number and an empty
    `matches` (§21.2): no `warned`, no match, no probe block."""
    read = crewbook.parse_book(account_file(book({}, schema=2, matches=[], warned=None)))
    assert read.state == "on" and read.schema_number == 2
    assert (read.matches, read.matches_unread, read.warned, read.matched, read.probe_pvp) == (
        [],
        [],
        None,
        False,
        [],
    )


def test_constructed_a_schema_1_book_reads_exactly_as_before() -> None:
    """Schema 2's fields in a schema 1 book are keys the reader does not
    know, and its top-level ones are not read."""
    data = account_file(
        book(
            {KEY_A: player(**PVP)},
            schema=1,
            matches=[match([KEY_A])],
            probe={"loads": 12, "pvp": [probe_run()]},
        )
    )
    read = crewbook.parse_book(data)
    assert read.schema_number == 1
    (a,) = read.players
    assert a.pvp is None and a.other_keys == list(PVP)
    assert (read.matches, read.warned, read.matched, read.probe_pvp) == ([], None, False, [])


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("allied", -1, "allied is not a whole number of 0 or more"),
        ("faced_won", 1.5, "faced_won is not a whole number of 0 or more"),
        ("pvp_last", "invented", "pvp_last is not a time"),
        ("pvp_map", 7, "pvp_map is not text"),
        ("pvp_side", "invented", 'pvp_side is neither "with" nor "against"'),
    ],
    ids=[
        "negative_constructed",
        "fraction_constructed",
        "text_time_constructed",
        "map_constructed",
        "side_constructed",
    ],
)
def test_constructed_a_bad_counter_makes_only_that_entry_unread(
    field: str, value: object, reason: str
) -> None:
    read = crewbook.parse_book(_book2({KEY_A: player(**{field: value}), KEY_B: player()}))
    assert [p.key for p in read.players] == [KEY_B]
    assert [(u.key, u.reason) for u in read.unread] == [(KEY_A, reason)]
    assert "invented" not in read.unread[0].reason


def test_constructed_a_match_that_does_not_fit_is_listed_and_the_rest_read() -> None:
    marker = "invented value never echoed"
    matches = [
        match([KEY_A], at=marker),
        match([KEY_A], kind=marker),
        match([KEY_A], won=marker),
        match([KEY_A], [7]),
        match([KEY_A], map=7),
        5,
        match([KEY_B], [KEY_C]),
    ]
    read = crewbook.parse_book(_book2({KEY_A: player()}, matches=matches))
    assert [(u.position, u.reason) for u in read.matches_unread] == [
        (1, "at is not a time"),
        (2, 'kind is neither "bg" nor "arena"'),
        (3, "won is neither true nor false"),
        (4, "foes is not a list of names"),
        (5, "map is not text"),
        (6, "the entry is not a table"),
    ]
    assert [(m.position, m.team) for m in read.matches] == [(7, [KEY_B])]
    assert all(marker not in u.reason for u in read.matches_unread)


@pytest.mark.parametrize(
    ("more", "words"),
    [
        ({"matches": 3}, "match list"),
        ({"matched": 3}, "record of the match last counted"),
        ({"warned": "invented"}, "time of the last nearly-full line"),
    ],
    ids=["matches_constructed", "matched_constructed", "warned_constructed"],
)
def test_constructed_a_schema_2_top_level_that_does_not_fit_is_refused(
    more: dict[str, Any], words: str
) -> None:
    with pytest.raises(crewbook.CrewBookError) as caught:
        crewbook.parse_book(_book2({KEY_A: player()}, **more))
    assert str(caught.value) == f"The crew book's {words} is not readable. Nothing was read."
    assert "invented" not in str(caught.value) and "invented" not in caught.value.detail


def test_constructed_a_schema_3_book_is_still_refused_with_its_number() -> None:
    with pytest.raises(crewbook.CrewBookError) as caught:
        crewbook.parse_book(account_file(book({KEY_A: player()}, schema=3)))
    assert "book version 3; this wowlab reads versions 1 and 2" in str(caught.value)


# ─── the probe block: fixed names and values only ────────────────────────────


def _items(run: list[crewbook.ProbeItem]) -> dict[str, object]:
    return {i.name: i.value for i in run}


def test_constructed_the_probe_block_is_given_as_fixed_names_and_values() -> None:
    read = crewbook.parse_book(
        _book2({}, probe={"loads": 3, "pvp": [probe_run(), probe_run(winner=0)]})
    )
    entry, winner = (_items(run) for run in read.probe_pvp)
    assert entry == {
        "calls.GetScoreInfo": True,
        "calls.GetNumBattlefieldScores": True,
        "calls.GetBattlefieldScore": False,
        "positions.1": "string",
        "positions.2": "number",
        "positions.3": "nil",
        "fields.name": "string",
        "fields.faction": "number",
        "fields.guid": "string",
        "requested": True,
        "score_events": 3,
        "complete_events": 0,
        "rows": 20,
        "name_string": True,
        "name_secret": False,
        "name_dash": True,
        "first_side": 0,
        "own_side": 1,
    }
    assert winner["winner"] == 0


def test_constructed_a_name_planted_in_the_probe_block_is_shown_as_a_question_mark() -> None:
    """Every place in the block a name could be put: a call name, a field
    name, a value, a position, a field the run does not have, a run that is
    not a table. None of it comes out."""
    run = probe_run(
        calls={KEY_A: True, "GetScoreInfo": KEY_B},
        fields={KEY_C: "string", "name": KEY_D},
        positions={KEY_A: "string"},
        rows=KEY_B,
        first_side=1.5,
        own_side=2**60,
    )
    run[KEY_D] = 1
    read = crewbook.parse_book(_book2({}, probe={"loads": 3, "pvp": [run, KEY_C]}))
    first, second = read.probe_pvp
    items = _items(first)
    assert items["calls.?"] is True and items["calls.GetScoreInfo"] == "?"
    assert items["fields.?"] == "string" and items["fields.name"] == "?"
    assert items["positions.?"] == "string"
    assert items["rows"] == items["first_side"] == items["own_side"] == "?"
    assert items["?"] == 1
    assert second == [crewbook.ProbeItem(name="?", value="?")]
    shown = repr(read.probe_pvp)
    for key in (KEY_A, KEY_B, KEY_C, KEY_D):
        for part in (key, *key.split("-")):
            assert part not in shown


def test_constructed_a_large_probe_block_is_cut_to_its_bounds() -> None:
    run = probe_run(positions=["string"] * 500)
    read = crewbook.parse_book(_book2({}, probe={"loads": 3, "pvp": [run, run, run]}))
    assert len(read.probe_pvp) == crewbook.PROBE_RUNS_MAX
    assert all(len(r) <= crewbook.PROBE_ITEMS_MAX for r in read.probe_pvp)
    assert "positions.?" in _items(read.probe_pvp[0])


# ─── writing back: the schema read, every other byte kept ────────────────────

OTHER = "invented_unknown_field"


def _full(**more: Any) -> bytes:
    """A schema 2 book with every field §21.2 adds and one this wowlab does
    not know, in the book and in a match."""
    return _book2(
        {KEY_A: player(**PVP), KEY_B: player(note=NOTE), KEY_C: player(), KEY_D: player()},
        current=[KEY_A],
        matches=[
            match([KEY_A, KEY_B], [KEY_C], **{OTHER: 1}),
            match([KEY_B], [KEY_A, KEY_D], won=False),
            match([KEY_D], [KEY_A], won=None),
        ],
        matched={"map_id": MAP_ID, "at": T2},
        probe={"loads": 3, "pvp": [probe_run(), probe_run(winner=1)]},
        **{OTHER: {"kept": True}},
        **more,
    )


def _without_book(data: bytes) -> bytes:
    return data.split(crewbook.CREW_VARIABLE.encode("ascii"), 1)[0]


def test_constructed_forget_takes_the_key_out_of_every_match_and_keeps_the_rest() -> None:
    data = _full()
    out = crewedit.forget(data, [KEY_A])
    before, after = _crew(data), _crew(out)
    assert after["schema"] == 2
    assert KEY_A not in after["players"] and after["current"] == {}
    assert [(m["team"], m["foes"]) for m in after["matches"]] == [
        ([KEY_B], [KEY_C]),
        ([KEY_B], [KEY_D]),
        ([KEY_D], {}),
    ]
    # Every match kept, with its other fields; the rest of the book as it was.
    for old, new in zip(before["matches"], after["matches"], strict=True):
        assert {k: v for k, v in old.items() if k not in ("team", "foes")} == {
            k: v for k, v in new.items() if k not in ("team", "foes")
        }
    for name in ("probe", "warned", "matched", OTHER, "started", "on", "skipped"):
        assert after[name] == before[name], name
    assert after["players"] == {k: v for k, v in before["players"].items() if k != KEY_A}
    assert _without_book(out) == _without_book(data)
    assert crewedit.names_left(out, [KEY_A]) == []
    # The bytes of the parts the forget did not touch are the file's own.
    for part in (b'["probe"] = ', b'["matched"] = ', b'["warned"] = ', OTHER.encode()):
        start = data.index(part)
        end = (
            data.index(b"\r\n}", start) if part != b'["warned"] = ' else data.index(b"\r\n", start)
        )
        assert data[start:end] in out
    assert crewbook.parse_book(out).schema_number == 2


def test_constructed_forget_of_a_player_named_only_in_a_match() -> None:
    """A player the game dropped from `players` may still be named by a
    match: the desk finds and forgets them there."""
    data = _book2({KEY_B: player()}, matches=[match([KEY_B], [KEY_C])])
    assert crewedit.keys_held(data) == [KEY_B, KEY_C]
    out = crewedit.forget(data, [KEY_C])
    assert crewedit.names_left(out, [KEY_C]) == []
    assert [m["foes"] for m in _crew(out)["matches"]] == [{}]
    assert _crew(out)["players"] == _crew(data)["players"]


def test_constructed_forget_all_empties_matches_and_keeps_the_rest() -> None:
    data = _full()
    out = crewedit.forget_all(data)
    before, after = _crew(data), _crew(out)
    assert (after["players"], after["current"], after["matches"]) == ({}, {}, {})
    for name in ("schema", "probe", "warned", "matched", OTHER, "started", "on", "skipped"):
        assert after[name] == before[name], name
    assert crewedit.names_left(out, [KEY_A, KEY_B, KEY_C, KEY_D]) == []
    assert _without_book(out) == _without_book(data)


def test_constructed_forget_all_empties_matches_when_no_player_is_left() -> None:
    data = _book2({}, matches=[match([], [])])
    out = crewedit.forget_all(data)
    assert _crew(out)["matches"] == {}


@pytest.mark.parametrize(
    ("field", "value"),
    [("rating", "good"), ("rating", None), ("note", "invented note again"), ("note", None)],
    ids=["rate_constructed", "unrate_constructed", "note_constructed", "unnote_constructed"],
)
def test_constructed_rate_and_note_change_one_field_of_a_schema_2_book(
    field: str, value: str | None
) -> None:
    data = _full()
    out = crewedit.set_field(data, KEY_B, field, value)  # type: ignore[arg-type]
    before, after = _crew(data), _crew(out)
    assert after["schema"] == 2
    record = dict(before["players"][KEY_B])
    record.pop(field, None)
    if value is not None:
        record[field] = value
    assert after["players"][KEY_B] == record
    for name, kept in before.items():
        if name != "players":
            assert after[name] == kept, name
    # Outside the one record, the book's bytes are the file's own.
    head = data[: data.index(_entry(KEY_B))]
    assert out.startswith(head)
    tail = data[data.index(_entry(KEY_C)) :]
    assert out.endswith(tail)


def _entry(key: str) -> bytes:
    """The start of a player's entry as the client writes it."""
    return b"[" + b'"' + key.encode("utf-8") + b'"' + b"] = "


def test_constructed_a_schema_1_book_keeps_a_matches_field_it_does_not_know() -> None:
    """At schema 1 `matches` is a field this wowlab does not know: a forget
    leaves it byte for byte, as it leaves any other."""
    data = account_file(book({KEY_A: player()}, schema=1, matches=[match([KEY_A])]))
    out = crewedit.forget(data, [KEY_A])
    start = data.index(b'["matches"] = ')
    piece = data[start : data.index(b"\r\n}, -- [1]", start)]
    assert piece in out
    assert _crew(out)["schema"] == 1


# ─── counting ────────────────────────────────────────────────────────────────


def test_constructed_the_count_holds_a_player_named_only_in_a_match() -> None:
    data = _book2({KEY_A: player()}, matches=[match([KEY_A], [KEY_B])])
    found = crewcount.carried([("WowLab.lua", data)])
    assert found is not None and found.players == 2 and found.on


# ─── 20,000 players (§21.2's limit) ──────────────────────────────────────────

# No time target is set for a full book (§21.5: the 20,000 limit is
# [verify]; §21.7 decision 18). Measured on the development machine
# (2026-11-20, CPU time, about 8.8 MB): the read 2.2 s (the parse 1.1 s, within
# §6.4's 10 s per 50 MB; building the values and the reader the rest), a
# forget 6.4 s (two parses and a serialization, for the check of §20.3). The
# ceilings below are stated times with room for a slower machine, not §6.4's
# rate.
READ_CEILING_S = 10.0
FORGET_CEILING_S = 30.0


def test_constructed_twenty_thousand_players_are_read_and_forgotten_within_the_budget() -> None:
    keys = many_keys(20_000)
    players = {k: player(last=T1 - i, **PVP) for i, k in enumerate(keys)}
    matches = [match(keys[i : i + 5], keys[i + 5 : i + 10]) for i in range(0, 5000, 10)]
    data = _book2(players, matches=matches)
    clock, name = cpu_clock()
    start = clock()
    read = crewbook.parse_book(data)
    read_s = clock() - start
    start = clock()
    out = crewedit.forget(data, [keys[0]])
    forget_s = clock() - start
    assert len(read.players) == 20_000 and len(read.matches) == 500 and read.unread == []
    assert crewedit.keys_held(out).count(keys[0]) == 0
    print(f"{len(data)} bytes: read {read_s:.3f} s, forget {forget_s:.3f} s ({name})")
    assert read_s < READ_CEILING_S, f"read in {read_s:.3f} s ({name})"
    assert forget_s < FORGET_CEILING_S, f"forget in {forget_s:.3f} s ({name})"
