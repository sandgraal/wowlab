# Probe from review of m16/12-svmerge-rulings; reproduces a list taken from theirs with a hole renumbering ours' later positional entries, so the written table loads keys theirs never had and `removed` misnames what went.
"""Constructed (L8: boundary cases; small documents, no fixture).

docs/LAB_PLAN.md §13.4, 2026-11-04 amendment (M16-12): "`--take theirs`
takes the whole list: ours' keys 1..n become theirs'", and "every removal
is listed". When theirs' array part has a hole inside the span (theirs
`{[1] = "a", [3] = "c"}`, ours `{"a", "b", "c"}`), `_Merger.table` deletes
ours' positional entry for key 2. Deleting a positional entry renumbers
every positional entry after it, so ours' `"c"` moves from key 3 to key 2:
the merged table loads `{1 = "a", 2 = "c"}`, not theirs' `{1 = "a",
3 = "c"}`, and `removed` lists `[2]` while key 3 is the one that went.
The same happens three-way. The positive control (no hole) passes.
"""

from __future__ import annotations

import pytest

from wowlab_core import luadata, svmerge


def _merged(ours: bytes, theirs: bytes, base: bytes | None = None) -> object:
    result = svmerge.merge(
        luadata.parse(ours),
        luadata.parse(theirs),
        base=None if base is None else luadata.parse(base),
        take="theirs",
    )
    return luadata.parse(luadata.serialize(result.document)).to_python()


def test_control_a_shorter_list_without_a_hole_is_taken_constructed() -> None:
    assert _merged(b'X = { "a", "b", "c" }\n', b'X = { "a" }\n') == {"X": ["a"]}


@pytest.mark.parametrize(
    ("ours", "theirs"),
    [
        (b'X = { "a", "b", "c" }\n', b'X = { [1] = "a", [3] = "c" }\n'),
        (b'X = { "a", "b", "c", "d" }\n', b'X = { "a", [3] = "c" }\n'),
        (b'X = { "a", "b", "c" }\n', b'X = { [2] = "b", [3] = "c" }\n'),
    ],
    ids=["hole-at-2", "hole-and-shorter", "hole-at-1"],
)
def test_two_way_take_theirs_gives_theirs_keys_constructed(ours: bytes, theirs: bytes) -> None:
    assert _merged(ours, theirs) == luadata.parse(theirs).to_python()


def test_three_way_one_sided_change_with_a_hole_gives_theirs_keys_constructed() -> None:
    ours = b'X = { "a", "b", "c" }\n'
    theirs = b'X = { [2] = "b", [3] = "c" }\n'
    assert _merged(ours, theirs, base=ours) == {"X": {2: "b", 3: "c"}}
