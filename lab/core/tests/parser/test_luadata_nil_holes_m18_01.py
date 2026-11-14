"""Graders for a positional `nil` hole in a SavedVariables table (M18-01T).

Written before the implementation (ADR-0013: `luadata.py` is load-bearing).
Every grader of behaviour that does not exist yet carries one `@XFAIL` line,
which M18-01 deletes and does not otherwise edit. Graders without the marker
pass today and must keep passing: they pin what the amendment leaves
refused, so accepting a hole cannot loosen it.

Sources: `docs/LAB_FORMATS.md` §4.1 (amendment of 2026-11-14) and §4.2/§4.4,
`docs/LAB_PLAN.md` §6.4 (the document model, `to_python()`, amendment items
4, 6 and 7), the Lua 5.1 reference manual §2.5.7 (table constructors: each
positional field takes the next consecutive integer key from 1, whatever
its value), and L3, L4, L8.

What the amendment says, as graded here:

- a positional `nil` inside a table parses; it is an entry of style
  `positional`, key `None`, whose value is a `LuaNil` (the hole: a value
  with no content), with its own lead and separator, never an error;
- it takes its index like any other positional entry, so the entries after
  it keep their numbers. `to_python()` takes the value Lua 5.1 would load
  (§6.4 item 6), and Lua stores nothing at a hole's key, so the key is
  absent; a table becomes a list only when its keys are exactly `1..n`
  (§6.4), so a hole inside the array part makes it a dict, and a trailing
  hole leaves a list;
- `serialize(parse(x)) == x` byte for byte, and the document alone rebuilds
  `x` (§6.4 amendment item 7);
- a top-level `X = nil` stays accepted; inside a table, a keyed `nil`
  (`["k"] = nil`, `k = nil`, `[1] = nil`, `[true] = nil`) and `nil` as a key
  stay refused with a position.

Real fixtures (L8): the character `Blizzard_AuctionHouseUI.lua` and the
account `Blizzard_CombatLog.lua` of the M18-01T capture (Forever
1.60.1.70938, `macos-70938/`, rows in `fixtures/README.md`). Every other
corpus-wide grader takes them too, through `_luadata_oracle.graded()`.
Constructed inputs (labelled `constructed` in the test id): the boundary
cases (a leading hole, two holes in a row, a trailing hole, a hole as the
only entry, and the other spellings the grammar allows), and the refusals.
Two holes in a row are constructed because the account `Auctionator.lua`
that holds them was refused by the scrub tool (see the README).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from _luadata_oracle import (
    FIXTURES,
    NIL_HOLE_FIXTURES,
    POSITIONAL,
    document_tokens,
    load,
    rebuild,
    source_tokens,
)

pytestmark = pytest.mark.parser

XFAIL = pytest.mark.xfail(strict=True, reason="M18-01 not implemented")

AUCTION_HOUSE, COMBAT_LOG = NIL_HOLE_FIXTURES
REAL_IDS = ["character-Blizzard_AuctionHouseUI", "account-Blizzard_CombatLog"]

# (line, column) of each `nil` hole in the real file, read off the capture.
REAL_HOLES = {
    AUCTION_HOUSE: [(10, 1)],
    COMBAT_LOG: [(28, 1), (272, 1)],
}


@pytest.fixture
def luadata() -> Any:
    return load()


def _doc(*lines: str, eol: bytes = b"\r\n") -> bytes:
    """A document in the client's shape: a leading blank line, then `lines`."""
    return eol + b"".join(line.encode("ascii") + eol for line in lines)


def _faithful(luadata: Any, data: bytes) -> Any:
    """Parse; the document alone rebuilds `data` with the same tokens, and
    `serialize` gives the same bytes back. Returns the document."""
    doc = luadata.parse(data)
    assert rebuild(luadata, doc) == data
    assert document_tokens(luadata, doc) == source_tokens(data)
    out = luadata.serialize(doc)
    assert isinstance(out, bytes)
    assert out == data
    return doc


def _value(doc: Any, *steps: object) -> Any:
    """The value at `steps` under the first assignment: a str is a string
    key, an int a positional index (counted over positional entries, holes
    included, as Lua 5.1 §2.5.7 numbers them)."""
    value = doc.assignments[0].value
    for step in steps:
        if isinstance(step, int):
            positional = [e for e in value.entries if e.style == POSITIONAL]
            value = positional[step - 1].value
        else:
            (value,) = [
                e.value
                for e in value.entries
                if e.style != POSITIONAL and getattr(e.key, "value", e.key) == step
            ]
    return value


def _holes(luadata: Any, table: Any) -> list[int]:
    """1-based indices of the positional entries of `table` that are holes."""
    positional = [e for e in table.entries if e.style == POSITIONAL]
    return [i for i, e in enumerate(positional, 1) if isinstance(e.value, luadata.LuaNil)]


def _all_tables(luadata: Any, value: Any) -> list[Any]:
    out = []
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, luadata.LuaTable):
            out.append(v)
            stack.extend(e.value for e in v.entries)
    return out


def _line_col(data: bytes, offset: int) -> tuple[int, int]:
    before = data[:offset]
    return before.count(b"\n") + 1, offset - (before.rfind(b"\n") + 1) + 1


def _rejected(luadata: Any, data: bytes, candidates: list[tuple[int, int, str]]) -> None:
    """Refused with a positioned `LuaDataError` (not a bound error) at one of
    `candidates`: (line, column, prefix of the token)."""
    with pytest.raises(luadata.LuaDataError) as info:
        luadata.parse(data)
    err = info.value
    assert not isinstance(err, luadata.LuaLimitError), err
    got = err.token or ""
    if isinstance(got, bytes):
        got = got.decode("utf-8", "surrogateescape")
    assert isinstance(got, str)
    assert any(
        (err.line, err.column) == (line, column) and got.startswith(prefix)
        for line, column, prefix in candidates
    ), f"line {err.line}, column {err.column}, token {got!r}; expected one of {candidates}"


# ── the corpus (no marker: reads bytes, never imports luadata) ──────────────


def test_nil_hole_fixtures_are_what_the_graders_assume() -> None:
    """Guards the graders below against a mislabelled path: each file holds
    its holes as `nil,` on their own line at the recorded places, in the
    client's layout (CRLF, a leading blank line, no indentation)."""
    for name, holes in REAL_HOLES.items():
        data = (FIXTURES / name).read_bytes()
        assert data.startswith(b"\r\n") and data.endswith(b"\r\n"), name
        assert data.count(b"\n") == data.count(b"\r\n"), name
        found = []
        start = 0
        while (at := data.find(b"\r\nnil,\r\n", start)) != -1:
            found.append(_line_col(data, at + 2))
            start = at + 2
        assert found == holes, name
        assert data.count(b"nil") == len(holes), name


# ── the real files ──────────────────────────────────────────────────────────


@XFAIL
@pytest.mark.parametrize("name", NIL_HOLE_FIXTURES, ids=REAL_IDS)
def test_real_nil_hole_round_trips_byte_for_byte(luadata: Any, name: str) -> None:
    """L4: `serialize(parse(x)) == x`, and the document alone gives `x`
    back with every token in source order."""
    _faithful(luadata, (FIXTURES / name).read_bytes())


@XFAIL
@pytest.mark.parametrize("name", NIL_HOLE_FIXTURES, ids=REAL_IDS)
def test_real_nil_hole_reads_from_a_copy_and_round_trips(
    luadata: Any, name: str, tmp_path: Path
) -> None:
    data = (FIXTURES / name).read_bytes()
    copy = tmp_path / Path(name).name
    copy.write_bytes(data)
    doc = luadata.read(copy)
    assert luadata.serialize(doc) == data
    assert copy.read_bytes() == data


@XFAIL
@pytest.mark.parametrize("name", NIL_HOLE_FIXTURES, ids=REAL_IDS)
def test_real_nil_hole_is_a_positional_entry_holding_luanil(luadata: Any, name: str) -> None:
    """The hole is reported as a value with no content: a positional entry
    (no key) whose value is a `LuaNil`, with the client's `,` after it. Only
    the recorded holes are `LuaNil` inside a table."""
    data = (FIXTURES / name).read_bytes()
    doc = luadata.parse(data)
    nils = []
    for assignment in doc.assignments:
        for table in _all_tables(luadata, assignment.value):
            for e in table.entries:
                if isinstance(e.value, luadata.LuaNil):
                    nils.append(e)
    assert len(nils) == len(REAL_HOLES[name])
    for e in nils:
        assert e.style == POSITIONAL
        assert e.key is None
        assert e.sep == b","
        assert e.duplicate is False


@XFAIL
def test_real_auction_house_filters_keep_their_indices(luadata: Any) -> None:
    """`g_auctionHouseFilters["filters"]` is `false` x4, the hole, `true` x7:
    the hole is index 5 and the `true`s keep 6 to 12."""
    doc = luadata.parse((FIXTURES / AUCTION_HOUSE).read_bytes())
    filters = _value(doc, "filters")
    assert isinstance(filters, luadata.LuaTable)
    assert len(filters.entries) == 12
    assert all(e.style == POSITIONAL for e in filters.entries)
    assert _holes(luadata, filters) == [5]
    values = [
        None if isinstance(e.value, luadata.LuaNil) else e.value.value for e in filters.entries
    ]
    assert values == [False] * 4 + [None] + [True] * 7
    assert [a.name for a in doc.assignments] == ["g_auctionHouseFilters", "g_activeBidAuctionIDs"]


@XFAIL
def test_real_auction_house_to_python_takes_what_lua_loads(luadata: Any) -> None:
    """Lua 5.1 stores nothing at the hole's key 5, so the keys are not
    exactly `1..n` and the table is a dict; 6 to 12 keep their numbers."""
    py = luadata.parse((FIXTURES / AUCTION_HOUSE).read_bytes()).to_python()
    filters = dict.fromkeys(range(1, 5), False) | dict.fromkeys(range(6, 13), True)
    assert py == {
        "g_auctionHouseFilters": {"maxLevel": 0, "minLevel": 0, "filters": filters},
        "g_activeBidAuctionIDs": {},
    }
    assert type(py["g_auctionHouseFilters"]["filters"]) is dict


@XFAIL
@pytest.mark.parametrize("which", [1, 2])
def test_real_combat_log_hole_is_index_3_between_colour_tables(luadata: Any, which: int) -> None:
    """Each filter's `["colors"]["schoolColoring"]` holds two colour tables,
    the hole, a third colour table at index 4, then number keys `[0]`, `[8]`, …"""
    doc = luadata.parse((FIXTURES / COMBAT_LOG).read_bytes())
    school = _value(doc, "filters", which, "colors", "schoolColoring")
    assert isinstance(school, luadata.LuaTable)
    assert _holes(luadata, school) == [3]
    positional = [e for e in school.entries if e.style == POSITIONAL]
    assert len(positional) == 4
    assert all(isinstance(e.value, luadata.LuaTable) for i, e in enumerate(positional) if i != 2)
    py = doc.to_python()["Blizzard_CombatLog_Filters"]["filters"]
    py_school = py[which - 1 if type(py) is list else which]["colors"]["schoolColoring"]
    assert type(py_school) is dict
    assert 3 not in py_school
    assert py_school[4] == {"a": 1, "b": 0, "g": 0.5, "r": 1}
    assert py_school[2] == {"a": 1, "b": 0.5, "g": 0.9, "r": 1}
    assert 0 in py_school and 8 in py_school


# ── constructed boundaries ──────────────────────────────────────────────────

# (id, document, holes in the table `X` as 1-based positional indices,
#  what to_python() gives for `X`)
BOUNDARIES = [
    ("constructed-leading-hole", _doc("X = {", "nil,", '"b",', "}"), [1], {2: "b"}),
    (
        "constructed-two-holes-in-a-row",
        _doc("X = {", '"a",', "nil,", "nil,", '"d",', "}"),
        [2, 3],
        {1: "a", 4: "d"},
    ),
    ("constructed-trailing-hole", _doc("X = {", '"a",', "nil,", "}"), [2], ["a"]),
    ("constructed-hole-only-entry", _doc("X = {", "nil,", "}"), [1], {}),
    (
        "constructed-hole-in-the-middle",
        _doc("X = {", '"a",', "nil,", '"c",', "}"),
        [2],
        {1: "a", 3: "c"},
    ),
    ("constructed-hole-one-line", _doc('X = {"a", nil, "c"}'), [2], {1: "a", 3: "c"}),
    ("constructed-hole-without-separator", _doc('X = {"a", nil}'), [2], ["a"]),
    ("constructed-hole-only-one-line", _doc("X = {nil}"), [1], {}),
    ("constructed-hole-semicolons", _doc('X = {"a"; nil; "c";}'), [2], {1: "a", 3: "c"}),
    (
        "constructed-hole-then-keyed",
        _doc("X = {", "nil,", '"b",', '["k"] = 1,', "}"),
        [1],
        {2: "b", "k": 1},
    ),
    (
        "constructed-hole-tabs-comments-lf",
        _doc("X = {", '\t"a", -- [1]', "\tnil, -- [2]", '\t"c", -- [3]', "}", eol=b"\n"),
        [2],
        {1: "a", 3: "c"},
    ),
    (
        "constructed-hole-numbers-and-booleans",
        _doc("X = {", "0,", "nil,", "1.50,", "nil,", "true,", "}"),
        [2, 4],
        {1: 0, 3: 1.5, 5: True},
    ),
]


@XFAIL
@pytest.mark.parametrize(
    ("data", "holes"), [(d, h) for _i, d, h, _p in BOUNDARIES], ids=[i for i, *_ in BOUNDARIES]
)
def test_constructed_hole_round_trips_and_keeps_its_place(
    luadata: Any, data: bytes, holes: list[int]
) -> None:
    doc = _faithful(luadata, data)
    table = doc.assignments[0].value
    assert isinstance(table, luadata.LuaTable)
    assert _holes(luadata, table) == holes
    for e in table.entries:
        if isinstance(e.value, luadata.LuaNil):
            assert e.style == POSITIONAL and e.key is None


@XFAIL
@pytest.mark.parametrize(
    ("data", "expected"),
    [(d, p) for _i, d, _h, p in BOUNDARIES],
    ids=[i for i, *_ in BOUNDARIES],
)
def test_constructed_hole_to_python_takes_what_lua_loads(
    luadata: Any, data: bytes, expected: Any
) -> None:
    """Lua 5.1 §2.5.7 numbers every positional field; §6.4 item 6 takes the
    value Lua would load, which is nothing at a hole's key. The entries
    after a hole keep their numbers."""
    got = luadata.parse(data).to_python()["X"]
    assert got == expected
    assert type(got) is type(expected)


@XFAIL
def test_constructed_hole_in_a_nested_table_keeps_the_outer_indices(luadata: Any) -> None:
    data = _doc("X = {", "{", "nil,", "1,", "},", "nil,", "{", "2,", "nil,", "},", "}")
    doc = _faithful(luadata, data)
    outer = doc.assignments[0].value
    assert _holes(luadata, outer) == [2]
    assert _holes(luadata, _value(doc, 1)) == [1]
    assert _holes(luadata, _value(doc, 3)) == [2]
    assert doc.to_python() == {"X": {1: {2: 1}, 3: [2]}}


@XFAIL
def test_constructed_top_level_nil_and_a_hole_in_one_file(luadata: Any) -> None:
    """A top-level `X = nil` stays accepted beside a table with a hole."""
    doc = _faithful(luadata, _doc("X = nil", "Y = {", "nil,", "1,", "}"))
    assert isinstance(doc.assignments[0].value, luadata.LuaNil)
    assert doc.to_python() == {"X": None, "Y": {2: 1}}


@XFAIL
def test_constructed_ten_thousand_holes_parse_and_round_trip(luadata: Any) -> None:
    """Boundary: a long run of holes is data, not a depth or recursion
    problem; the entry after them is index 10 001."""
    data = _doc("X = {", *(["nil,"] * 10_000), '"last",', "}")
    doc = _faithful(luadata, data)
    assert len(_holes(luadata, doc.assignments[0].value)) == 10_000
    assert doc.to_python() == {"X": {10_001: "last"}}


@XFAIL
def test_constructed_serialize_writes_a_positional_luanil_as_nil(luadata: Any) -> None:
    """The serializer side: a document whose positional entry is changed to
    a `LuaNil` writes `nil` there, and reads back as the same hole."""
    doc = luadata.parse(_doc("X = {", '"a",', '"b",', '"c",', "}"))
    table = doc.assignments[0].value
    entries = list(table.entries)
    old = entries[1].value
    entries[1] = entries[1]._replace(value=luadata.LuaNil(lead=old.lead))
    changed = doc._replace(
        assignments=(doc.assignments[0]._replace(value=table._replace(entries=tuple(entries))),)
    )
    out = luadata.serialize(changed)
    assert out == _doc("X = {", '"a",', "nil,", '"c",', "}")
    assert luadata.parse(out).to_python() == {"X": {1: "a", 3: "c"}}


@XFAIL
def test_constructed_keyed_nil_after_a_hole_is_refused_at_the_keyed_nil(luadata: Any) -> None:
    """Accepting the hole on line 3 does not loosen line 4: the refusal is
    at the keyed `nil`, not at the hole."""
    _rejected(luadata, _doc("X = {", "nil,", '["k"] = nil,', "}"), [(4, 9, "nil")])


# ── still refused (no marker: true today, and must stay true) ───────────────

STILL_REFUSED = [
    ("constructed-nil-value-string-key", _doc("X = {", '["k"] = nil,', "}"), [(3, 9, "nil")]),
    ("constructed-nil-value-name-key", _doc("X = {", "k = nil,", "}"), [(3, 5, "nil")]),
    ("constructed-nil-value-number-key", _doc("X = {", "[1] = nil,", "}"), [(3, 7, "nil")]),
    ("constructed-nil-value-boolean-key", _doc("X = {", "[true] = nil,", "}"), [(3, 10, "nil")]),
    ("constructed-nil-value-one-line", _doc('X = {["k"] = nil}'), [(2, 14, "nil")]),
    ("constructed-nil-key", _doc("X = {", "[nil] = 1,", "}"), [(3, 2, "nil")]),
    ("constructed-nil-key-to-nil", _doc("X = {", "[nil] = nil,", "}"), [(3, 2, "nil")]),
    # `nil` is a keyword, so `nil = 1` is no name key: refused at `nil`, or,
    # once `nil` is read as a hole, at the `=` that follows it.
    ("constructed-nil-as-name-key", _doc("X = {", "nil = 1,", "}"), [(3, 1, "nil"), (3, 5, "=")]),
]


@pytest.mark.parametrize(
    ("data", "candidates"),
    [(d, c) for _i, d, c in STILL_REFUSED],
    ids=[i for i, *_ in STILL_REFUSED],
)
def test_keyed_nil_and_nil_as_a_key_stay_refused_with_a_position(
    luadata: Any, data: bytes, candidates: list[tuple[int, int, str]]
) -> None:
    """§4.1 (amendment of 2026-11-14): inside a table, a keyed `nil` and
    `nil` as a key are unobserved and stay rejected, with line, column and
    the offending token."""
    _rejected(luadata, data, candidates)


def test_top_level_nil_stays_accepted(luadata: Any) -> None:
    doc = _faithful(luadata, _doc("X = nil"))
    assert isinstance(doc.assignments[0].value, luadata.LuaNil)
    assert doc.to_python() == {"X": None}
