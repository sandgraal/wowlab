"""`svmerge` on a positional `nil` hole (M18-01; `docs/LAB_FORMATS.md` §4.1,
amendment of 2026-11-14).

A positional `nil` takes its index and stores nothing there (Lua 5.1
§2.5.7). Pending positional entries are stored at the closing brace (or 50
at a time), so an entry `[k] = v` appended after a hole at `k` would be
cleared by it when the file loads. A value merged into a hole is therefore
written in the hole's place. The real files with holes are graded in
`test_sv_nil_holes_m18_01.py`; every input here is constructed (labelled so
in the test ids) to reach a hole outside the array part.
"""

from __future__ import annotations

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
