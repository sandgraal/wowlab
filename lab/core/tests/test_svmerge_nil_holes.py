"""`svmerge` on a positional `nil` hole (M18-01; `docs/LAB_FORMATS.md` §4.1,
amendment of 2026-11-14).

A positional `nil` takes its index and stores nothing there (Lua 5.1
§2.5.7). Pending positional entries are stored at the closing brace (or 50
at a time), so an entry `[k] = v` appended after a hole at `k` would be
cleared by it when the file loads. A value merged into a hole is therefore
written in the hole's place. A taken array part is written so that ours'
entries after it keep their keys (security and code review of #564).

The real files with holes are graded in `test_sv_nil_holes_m18_01.py`. Here
every input is constructed (labelled so in the test ids), except that the
auction-house case merges the real M18-01T capture with a constructed edit
of it.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import luadata, svmerge


def _doc(*lines: str) -> bytes:
    return b"\r\n" + b"".join(line.encode("ascii") + b"\r\n" for line in lines)


OURS = _doc("X = {", '"a",', '"b",', "nil,", "nil,", '"e",', "}")
THEIRS = _doc("X = {", '"a",', '"b",', "nil,", '"D",', '"e",', "}")
FILLED = _doc("X = {", '"a",', '"b",', "nil,", '"D",', '"e",', "}")
LOADED = {"X": {1: "a", 2: "b", 4: "D", 5: "e"}}


@pytest.mark.parametrize(
    "options",
    [
        {"base": True},
        {"keys": ["X[4]"]},
        {"keys": ["X[4]"], "replace": True},
        {"keys": ["X[4]"], "take": "theirs"},
    ],
    ids=[
        "constructed-three-way",
        "constructed-key",
        "constructed-key-replace",
        "constructed-key-take-theirs",
    ],
)
def test_a_value_for_a_hole_is_written_in_its_place(options: dict[str, Any]) -> None:
    """Key 4 is outside the array part (`1..2`) on both sides; theirs'
    `"D"` replaces ours' hole entry rather than being appended after it."""
    kwargs = dict(options)
    if kwargs.pop("base", False):
        kwargs["base"] = luadata.parse(OURS)
    result = svmerge.merge(luadata.parse(OURS), luadata.parse(THEIRS), **kwargs)
    out = luadata.serialize(result.document)
    assert out == FILLED
    assert [t.path for t in result.taken] == ["X[4]"]
    assert list(result.conflicts) == [] and list(result.absent) == []
    reread = luadata.parse(out)
    assert reread.to_python() == LOADED
    assert luadata.serialize(reread) == out


def test_two_way_a_key_ours_holds_as_a_hole_is_absent_from_ours_constructed() -> None:
    result = svmerge.merge(luadata.parse(OURS), luadata.parse(THEIRS))
    assert [(a.path, a.missing_from) for a in result.absent] == [("X[4]", "ours")]
    assert list(result.conflicts) == [] and list(result.taken) == []
    assert luadata.serialize(result.document) == OURS


def test_key_placement_fills_a_hole_after_a_gap_constructed() -> None:
    """`keys` placement into a hole ours holds past a gap (the array part
    is `1..1`; keys 2 and 3 are holes; key 4 is a value): the hole at
    `[3]` takes theirs' value in place and `[4]` keeps its number."""
    ours = luadata.parse(_doc("Y = {", '"a",', "nil,", "nil,", '"d",', "}"))
    theirs = luadata.parse(_doc("Y = {", '"a",', "nil,", '"C",', '"d",', "}"))
    result = svmerge.merge(ours, theirs, keys=["Y[3]"])
    out = luadata.serialize(result.document)
    assert out == _doc("Y = {", '"a",', "nil,", '"C",', '"d",', "}")
    assert luadata.parse(out).to_python() == {"Y": {1: "a", 3: "C", 4: "d"}}
    assert [t.path for t in result.taken] == ["Y[3]"]


def test_a_hole_clears_an_earlier_keyed_value_constructed() -> None:
    """`{[1] = "x", nil}`: the positional `nil` is stored at the closing
    brace, after `[1] = "x"`, so key 1 holds nothing; theirs' `"y"` at key
    1, added since the base, is written in the hole's place."""
    ours = luadata.parse(_doc("X = {", '[1] = "x",', "nil,", "}"))
    theirs = luadata.parse(_doc("X = {", '"y",', "}"))
    assert ours.to_python() == {"X": {}}
    result = svmerge.merge(ours, theirs, base=luadata.parse(_doc("X = {", "}")))
    out = luadata.serialize(result.document)
    assert luadata.parse(out).to_python() == {"X": ["y"]}


def test_keys_order_and_holes_constructed() -> None:
    """`_Keys`: a hole is not loaded, `order()` skips it, and `holes` maps
    its key to the `nil` entry."""
    table = luadata.parse(OURS).assignments[0].value
    assert isinstance(table, luadata.LuaTable)
    keys = svmerge._Keys(table)
    assert keys.order() == [0, 1, 4]
    assert keys.holes == {("n", 3.0): 2, ("n", 4.0): 3}
    assert ("n", 3.0) not in keys.loaded and ("n", 4.0) not in keys.loaded
    assert keys.npos == 5
    assert svmerge._border(keys) == 2


# ── a taken list keeps the keys of ours' entries after a hole ────────────────
# (security review of #564: `written` assumed every positional entry of ours
# is a key 1..span; after a hole, ours holds positional entries past it.)

LIST_TAKES = [
    (
        "constructed-two-way-take-theirs",
        'X = {"a", "b", nil, "d"}',
        'X = {"x"}',
        {"take": "theirs"},
        {1: "x", 4: "d"},
    ),
    (
        "constructed-three-way-base-is-ours",
        'X = {"a", "b", nil, "d"}',
        'X = {"x", nil, nil, "d"}',
        {"base": True},
        {1: "x", 4: "d"},
    ),
    (
        "constructed-two-way-take-theirs-longer",
        'X = {"a", nil, "c"}',
        'X = {"x", "y"}',
        {"take": "theirs"},
        ["x", "y", "c"],
    ),
]


@pytest.mark.parametrize(
    ("ours", "theirs", "options", "expected"),
    [case[1:] for case in LIST_TAKES],
    ids=[case[0] for case in LIST_TAKES],
)
def test_a_taken_list_keeps_the_keys_after_a_hole(
    ours: str, theirs: str, options: dict[str, Any], expected: object
) -> None:
    kwargs = dict(options)
    if kwargs.pop("base", False):
        kwargs["base"] = luadata.parse(_doc(ours))
    result = svmerge.merge(luadata.parse(_doc(ours)), luadata.parse(_doc(theirs)), **kwargs)
    out = luadata.serialize(result.document)
    reread = luadata.parse(out)
    assert luadata.serialize(reread) == out
    loaded = reread.to_python()["X"]
    assert loaded == expected
    if "base" in options:
        assert list(result.conflicts) == []


def test_a_taken_list_keeps_d_at_key_4_constructed() -> None:
    """The review's two reproductions, by key: `X[4]` is `"d"` after either."""
    ours = _doc('X = {"a", "b", nil, "d"}')
    for theirs, kwargs in (
        (_doc('X = {"x"}'), {"take": "theirs"}),
        (_doc('X = {"x", nil, nil, "d"}'), {"base": luadata.parse(ours)}),
    ):
        result = svmerge.merge(luadata.parse(ours), luadata.parse(theirs), **kwargs)  # type: ignore[arg-type]
        loaded = luadata.parse(luadata.serialize(result.document)).to_python()["X"]
        assert isinstance(loaded, dict)
        assert loaded[4] == "d" and 3 not in loaded and 2 not in loaded


REAL_AH = (
    Path(__file__).resolve().parent
    / "fixtures/macos-70938/forever/WTF/Account/LABACCOUNTA/1/Sandgraal-Rizzen"
    / "SavedVariables/Blizzard_AuctionHouseUI.lua"
).read_bytes()
FOUR_FALSE = b'["filters"] = {\r\n' + b"false,\r\n" * 4 + b"nil,\r\n"


@pytest.mark.parametrize(
    ("theirs_filters", "options"),
    [
        (b'["filters"] = {\r\n' + b"false,\r\n" * 2 + b"nil,\r\n", {"take": "theirs"}),
        (b'["filters"] = {\r\n' + b"false,\r\n" * 2 + b"nil,\r\n", {"base": True}),
        (b'["filters"] = {\r\n' + b"false,\r\n" * 3 + b"nil,\r\n", {"take": "theirs"}),
    ],
    ids=[
        "real-shape-constructed-edit-two-false-take-theirs",
        "real-shape-constructed-edit-two-false-three-way",
        "real-shape-constructed-edit-three-false-take-theirs",
    ],
)
def test_a_shorter_list_taken_on_the_real_auction_house_file_keeps_the_trues(
    theirs_filters: bytes, options: dict[str, Any]
) -> None:
    """The real `g_auctionHouseFilters["filters"]` (false x4, the hole, true
    x7, M18-01T capture) against a constructed edit of it whose list before
    the hole is shorter: ours' trues at 6..12 keep their keys."""
    assert REAL_AH.count(FOUR_FALSE) == 1
    theirs_src = REAL_AH.replace(FOUR_FALSE, theirs_filters)
    ours, theirs = luadata.parse(REAL_AH), luadata.parse(theirs_src)
    kwargs = dict(options)
    if kwargs.pop("base", False):
        kwargs["base"] = luadata.parse(REAL_AH)
    result = svmerge.merge(ours, theirs, **kwargs)
    out = luadata.serialize(result.document)
    reread = luadata.parse(out)
    assert luadata.serialize(reread) == out
    got = _as_dict(reread.to_python()["g_auctionHouseFilters"]["filters"])  # type: ignore[index]
    assert all(got[k] is True for k in range(6, 13)), got
    expected = _expected(
        _as_dict(ours.to_python()["g_auctionHouseFilters"]["filters"]),  # type: ignore[index]
        _as_dict(theirs.to_python()["g_auctionHouseFilters"]["filters"]),  # type: ignore[index]
        "base" in options,
    )
    assert got == expected


# ── randomized: the merged table against a Lua 5.1 model ────────────────────


def _as_dict(value: object) -> dict[object, object]:
    if isinstance(value, list):
        return dict(enumerate(value, 1))
    assert isinstance(value, dict)
    return dict(value)


def _model_border(d: dict[object, object]) -> int:
    n = 0
    while n + 1 in d:
        n += 1
    return n


def _expected(ours: dict[object, object], theirs: dict[object, object], three: bool) -> object:
    """What the merge loads as, under §13.4 with M16-12, from what each side
    loads as (`to_python`, the Lua 5.1 model): the array part `1..span` is
    theirs' (taken whole, or equal to ours'); every other key is theirs'
    where both hold it, ours' where only ours does, and theirs' where only
    theirs does in a three-way merge (added), absent in a two-way one."""
    span = max(_model_border(ours), _model_border(theirs))
    out = {k: theirs[k] for k in range(1, span + 1) if k in theirs}
    for k in [*ours, *theirs]:
        if (isinstance(k, int) and 1 <= k <= span) or k in out:
            continue
        if k in theirs and (k in ours or three):
            out[k] = theirs[k]
        elif k in ours:
            out[k] = ours[k]
    return out


def _random_table(rng: random.Random) -> str:
    items = [rng.choice(['"a"', '"b"', '"c"', "nil", "nil"]) for _ in range(rng.randint(0, 9))]
    for _ in range(rng.randint(0, 2)):
        items.insert(rng.randint(0, len(items)), f'[{rng.randint(1, 10)}] = "k"')
    return "X = {" + ", ".join(items) + "}"


@pytest.mark.parametrize("three", [False, True], ids=["constructed-two-way", "constructed-three"])
def test_randomized_merges_with_holes_load_as_the_model_says(three: bool) -> None:
    rng = random.Random(564)
    for _ in range(400):
        ours_src, theirs_src = _doc(_random_table(rng)), _doc(_random_table(rng))
        ours, theirs = luadata.parse(ours_src), luadata.parse(theirs_src)
        if three:
            result = svmerge.merge(ours, theirs, base=luadata.parse(ours_src))
        else:
            result = svmerge.merge(ours, theirs, take="theirs")
        out = luadata.serialize(result.document)
        reread = luadata.parse(out)
        assert luadata.serialize(reread) == out
        expected = _expected(
            _as_dict(ours.to_python()["X"]), _as_dict(theirs.to_python()["X"]), three
        )
        assert _as_dict(reread.to_python()["X"]) == expected, (ours_src, theirs_src, out)


@pytest.mark.parametrize(
    ("data", "where"),
    [
        (b"X = {nil = 1}", (1, 6)),
        (b"\r\nX = {\r\nnil = 1,\r\n}\r\n", (3, 1)),
    ],
    ids=["constructed-one-line", "constructed-own-line"],
)
def test_nil_as_a_name_key_is_refused_at_the_nil(data: bytes, where: tuple[int, int]) -> None:
    """`nil` is a keyword, never a name key: refused at the `nil`, as before
    holes were read (code review of #564), not at the `=` after it."""
    with pytest.raises(luadata.LuaDataError) as caught:
        luadata.parse(data)
    assert (caught.value.line, caught.value.column) == where
    assert caught.value.token == b"nil"
