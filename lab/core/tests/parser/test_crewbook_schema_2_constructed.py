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
    lua_value,
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
    assert read.pvp == {KEY_A: crewbook.CrewPvp(**PVP)} and a.other_keys == []
    assert b.key not in read.pvp
    # Beside the record, not in it: `crew list --json` is unchanged.
    assert set(a.model_dump()) == set(crewbook.CrewPlayer.model_fields)
    assert "pvp" not in crewbook.CrewPlayer.model_fields
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
    assert read.pvp == {} and a.other_keys == list(PVP)
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
        (2, 'kind is not "bg", "arena" or "shuffle"'),
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


def test_constructed_a_schema_4_book_is_still_refused_with_its_number() -> None:
    # M21-12 reads schema 3; the next number is still refused.
    with pytest.raises(crewbook.CrewBookError) as caught:
        crewbook.parse_book(account_file(book({KEY_A: player()}, schema=4)))
    assert "book version 4; this wowlab reads versions 1, 2 and 3" in str(caught.value)


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
    # (`.get`, not a subscript: the repository's crew scan reads a quoted
    # subscript as a book key.)
    assert items.get("calls.?") is True and items.get("calls.GetScoreInfo") == "?"
    assert items.get("fields.?") == "string" and items.get("fields.name") == "?"
    assert items.get("positions.?") == "string"
    assert items.get("rows") == items.get("first_side") == items.get("own_side") == "?"
    assert items.get("?") == 1
    assert second == [crewbook.ProbeItem(name="?", value="?")]
    shown = repr(read.probe_pvp)
    for key in (KEY_A, KEY_B, KEY_C, KEY_D):
        for part in (key, *key.split("-")):
            assert part not in shown


def test_constructed_a_large_probe_block_is_cut_to_its_bounds() -> None:
    run = probe_run(positions=list(("string",) * 500))
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
# unconfirmed; §21.7 decision 18), so this test times nothing. Measured once
# on the development machine (2026-11-20, CPU time, about 8.8 MB): the read
# 2.2 s (the parse 1.1 s, within §6.4's 10 s per 50 MB; building the values
# and the reader the rest), a forget 6.4 s (two parses and a serialization,
# for the check of §20.3).


def test_constructed_twenty_thousand_players_are_read_and_one_forgotten() -> None:
    keys = many_keys(20_000)
    players = {k: player(last=T1 - i, **PVP) for i, k in enumerate(keys)}
    matches = [match(keys[i : i + 5], keys[i + 5 : i + 10]) for i in range(0, 5000, 10)]
    data = _book2(players, matches=matches)
    read = crewbook.parse_book(data)
    assert len(read.players) == 20_000 and len(read.matches) == 500 and read.unread == []
    out = crewedit.forget(data, [keys[0]])
    assert keys[0] not in crewedit.keys_held(out)
    assert len(crewedit.keys_held(out)) == 20_000 - 1


# ─── values that are not a key where a key is expected (fix round 1) ─────────

# A list where a player key is expected may hold something else in a damaged
# or hand-edited file: a nested table (it once made every forget raise
# TypeError), a number, a boolean. Each is kept and never compared; nothing
# raises. (A table used as a key is not data `luadata` reads at all.)
ODD_VALUES: dict[str, list[Any]] = {
    "nested_table_constructed": [[KEY_C]],
    "number_constructed": [7],
    "boolean_constructed": [True],
}
PLACES = ["team", "foes", "current", "matched", "matches"]


def _odd_book(where: str, odd: list[Any]) -> bytes:
    """A schema 2 book holding `odd` in `where`: a match's `team` or
    `foes`, the `current` list, the `matched` marker, or `matches` itself."""
    players = {KEY_A: player(), KEY_B: player()}
    good = match([KEY_A], [KEY_B])
    if where == "team":
        return _book2(players, matches=[good, match(odd, [KEY_B])])
    if where == "foes":
        return _book2(players, matches=[good, match([KEY_A], [KEY_B, *odd])])
    if where == "current":
        data = _book2(players, matches=[good])
        empty = b'["current"] = {\r\n}'
        assert data.count(empty) == 1
        return data.replace(empty, b'["current"] = ' + lua_value(odd))
    if where == "matched":
        return _book2(players, matches=[good], matched=odd)
    assert where == "matches"
    return _book2(players, matches=[good, *odd])


@pytest.mark.parametrize("odd", list(ODD_VALUES), ids=list(ODD_VALUES))
@pytest.mark.parametrize("where", PLACES)
def test_constructed_an_odd_value_breaks_no_reader_count_or_edit(where: str, odd: str) -> None:
    data = _odd_book(where, ODD_VALUES[odd])
    before = _crew(data)
    read = crewbook.parse_book(data)
    assert [p.key for p in read.players] == [KEY_A, KEY_B]
    found = crewcount.carried([("WowLab.lua", data)])
    assert found is not None and found.unreadable == 0 and found.players >= 2
    held = crewedit.keys_held(data)
    assert KEY_A in held and KEY_B in held
    # A player the odd value is not is forgotten everywhere; the odd value,
    # and every part of the book the forget does not change, stay.
    out = crewedit.forget(data, [KEY_B])
    assert crewedit.names_left(out, [KEY_B]) == []
    after = _crew(out)
    for name in ("schema", "matched", "probe", "warned"):
        assert after.get(name) == before.get(name), name
    emptied = _crew(crewedit.forget_all(data))
    assert emptied.get("matched") == before.get("matched")
    rated = _crew(crewedit.set_field(data, KEY_A, "rating", "good"))
    assert rated.get("matches") == before.get("matches")
    assert rated.get("current") == before.get("current")


def test_constructed_a_key_inside_a_nested_table_is_never_a_silent_survivor() -> None:
    """The forget does not reach into a nested table; `names_left` finds the
    name there, which makes `crew forget` refuse with nothing written (the
    command's own test is in `test_cli_crew_write.py`)."""
    data = _odd_book("team", [[KEY_A]])
    out = crewedit.forget(data, [KEY_A])
    assert KEY_A not in _crew(out).get("players", {})
    assert crewedit.names_left(out, [KEY_A]) != []


def test_constructed_a_table_used_as_a_key_is_refused_by_the_parser() -> None:
    """Not data the client writes: `luadata` refuses it with a position, so
    every read and edit refuses the file before anything is planned."""
    data = _book2({KEY_A: player()}, matches=[match([KEY_A])])
    team = b'["team"] = {'
    assert team in data
    data = data.replace(team, b"[{}] = 1,\r\n" + team, 1)
    with pytest.raises(luadata.LuaDataError):
        crewbook.parse_book(data)
    with pytest.raises(luadata.LuaDataError):
        crewedit.forget(data, [KEY_A])
    with pytest.raises(luadata.LuaDataError):
        crewedit.forget_all(data)
    with pytest.raises(luadata.LuaDataError):
        crewedit.keys_held(data)
    found = crewcount.carried([("WowLab.lua", data)])
    assert found is not None and found.unreadable == 1


# ─── M20-11: the shape the addon writes, as built (§21.2) ────────────────────

# Constructed: an obviously invented GUID and a scoreboard line of invented
# numbers; the addon writes these fields from M20-11 on.
_GUID_A = "Player-0000-00000000"
_GUID_B = "Player-0000-00000001"
_SCORE_A = {
    "name": KEY_A,
    "guid": _GUID_A,
    "faction": 1,
    "damageDone": 1000,
    "healingDone": 10,
    "killingBlows": 2,
    "deaths": 1,
    "honorGained": 0,
    "rating": 0,
    "ratingChange": 0,
    "talentSpec": "Frost",
    "at": T2,
}
_M20_11 = {
    "guid": _GUID_A,
    "race": "Gnome",
    "faction": "Alliance",
    "spec": "Frost",
    "honor_level": 3,
    "score": _SCORE_A,
}


def _as_built() -> bytes:
    return _book2(
        {KEY_A: player(**PVP, **_M20_11), KEY_B: player(**PVP, guid=_GUID_B)},
        matches=[match([KEY_B], [KEY_A])],
        matched={"map_id": MAP_ID, "at": T2},
    )


def test_constructed_the_desk_reads_the_m20_11_shape() -> None:
    """M20-12: the fields M20-11 adds are read, nothing unread and no
    unknown field left; the scoreboard line keeps every plain field but the
    row's `name` and `guid`, which the entry holds."""
    read = crewbook.parse_book(_as_built())
    assert read.unread == [] and read.matches_unread == []
    a = next(p for p in read.players if p.key == KEY_A)
    b = next(p for p in read.players if p.key == KEY_B)
    assert a.other_keys == [] and b.other_keys == []
    assert (a.guid, a.race, a.faction, a.spec, a.honor_level) == (
        _GUID_A,
        "Gnome",
        "Alliance",
        "Frost",
        3,
    )
    assert b.guid == _GUID_B and b.score is None
    assert a.score is not None and a.score.at == T2
    assert "name" not in a.score.values and "guid" not in a.score.values
    assert sorted(a.score.other_keys) == ["guid", "name"]
    assert a.score.values.get("damageDone") == 1000
    assert a.score.values.get("talentSpec") == "Frost"
    assert [(m.team, m.foes) for m in read.matches] == [([KEY_B], [KEY_A])]
    assert read.matched is True


def test_constructed_a_schema_1_book_keeps_the_m20_11_fields_unknown() -> None:
    """At schema 1 the fields are keys this reader does not know, as every
    schema 2 field is there."""
    data = account_file(book({KEY_A: player(**_M20_11)}))
    read = crewbook.parse_book(data)
    assert read.schema_number == 1 and read.unread == []
    assert set(read.players[0].other_keys) == set(_M20_11)
    assert read.players[0].guid is None


def test_constructed_a_solo_shuffle_and_a_key_with_no_entry_are_read() -> None:
    """`kind = "shuffle"` (everyone in `team`, no `won`) and a match key the
    book has no entry for (refused when full) land in neither unread list."""
    shuffle = match([KEY_A, KEY_C, KEY_D], [], won=None, kind="shuffle")
    data = _book2({KEY_A: player()}, matches=[shuffle, match([KEY_D], [KEY_C])])
    read = crewbook.parse_book(data)
    assert read.unread == [] and read.matches_unread == []
    assert [(m.kind, m.won, m.team, m.foes) for m in read.matches] == [
        ("shuffle", None, [KEY_A, KEY_C, KEY_D], []),
        ("bg", True, [KEY_D], [KEY_C]),
    ]
    assert [p.key for p in read.players] == [KEY_A]


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("guid", 7, "guid is not text"),
        ("race", True, "race is not text"),
        ("faction", 1, "faction is not text"),
        ("spec", 2, "spec is not text"),
        ("honor_level", -1, "honor_level is not a whole number of 0 or more"),
        ("honor_level", "invented", "honor_level is not a whole number of 0 or more"),
        ("score", 5, "score is not a table"),
        ("score", {"at": "invented"}, "the time of score is not a time"),
    ],
    ids=lambda v: str(v),
)
def test_constructed_an_m20_11_field_of_the_wrong_type_makes_only_that_entry_unread(
    field: str, value: object, reason: str
) -> None:
    read = crewbook.parse_book(_book2({KEY_A: player(**{field: value}), KEY_B: player()}))
    assert [p.key for p in read.players] == [KEY_B]
    assert [(u.key, u.reason) for u in read.unread] == [(KEY_A, reason)]


def test_constructed_a_scoreboard_line_keeps_plain_values_only() -> None:
    """A table in the line (the battleground's own columns) is named, not
    kept; text, numbers and true/false are kept in the order given."""
    line = {"at": T2, "damageDone": 12.5, "stats": {"x": 1}, "isOwn": False, "className": "Mage"}
    read = crewbook.parse_book(_book2({KEY_A: player(score=line)}))
    score = read.players[0].score
    assert score is not None
    assert list(score.values.items()) == [
        ("damageDone", 12.5),
        ("isOwn", False),
        ("className", "Mage"),
    ]
    (only,) = score.other_keys
    assert only == "stats"


def test_constructed_forget_reaches_what_m20_11_keeps_of_a_player() -> None:
    """`score` and `guid` sit in the player's own entry, so forget removes
    them with it and no copy of the name or GUID is left."""
    data = _as_built()
    out = crewedit.forget(data, [KEY_A])
    assert crewedit.names_left(out, [KEY_A]) == []
    after = _crew(out)
    assert KEY_A not in after["players"]
    assert _GUID_A.encode() not in out
    assert after["players"][KEY_B]["guid"] == _GUID_B
