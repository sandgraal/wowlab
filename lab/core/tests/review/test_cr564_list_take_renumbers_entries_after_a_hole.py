# Probe from review of m18/01-luadata-nil-holes; reproduces a taken array part renumbering ours' positional entries that sit after a nil hole
"""Constructed (L8: boundary inputs, labelled; no real file is needed).

`_Merger.take_list` replaces ours' keys 1..span with theirs' and
`_Merger.written` starts counting at 0, on the assumption (svmerge.py, the
`npos = 0` line in `written`) that every positional entry of ours is a key
1..span. A positional `nil` hole (LAB_FORMATS §4.1, amendment of
2026-11-14) breaks that: in `{"a", nil, "c"}` the border is 1, so the span
can be 1 while two positional entries (the hole and `"c"`) follow the list.
When theirs' list is shorter than the positional entries it replaces, those
later entries move down: key 3, unchanged on every side, loads as key 2.
The module docstring promises the taken list is "never renumbered".

The positive control is the same merge with no hole: ours' list is fully
inside the span, so nothing follows it and nothing moves.
"""

from __future__ import annotations

import pytest

from wowlab_core import luadata, svmerge


def _merged(ours: bytes, theirs: bytes, **kwargs: object) -> object:
    result = svmerge.merge(luadata.parse(ours), luadata.parse(theirs), **kwargs)  # type: ignore[arg-type]
    return luadata.parse(luadata.serialize(result.document)).to_python()


OURS = b'X = {"a", nil, "c"}\n'


@pytest.mark.parametrize(
    ("theirs", "kwargs"),
    [
        (b'X = {nil, nil, "c"}\n', {"base": luadata.parse(OURS)}),
        (b'X = {[3] = "c"}\n', {"base": luadata.parse(OURS)}),
        (b'X = {nil, nil, "c"}\n', {"take": "theirs"}),
    ],
    ids=["constructed-three-way", "constructed-three-way-keyed", "constructed-two-way-take"],
)
def test_key_after_a_hole_keeps_its_number_when_the_list_is_taken(
    theirs: bytes, kwargs: dict[str, object]
) -> None:
    """Theirs drops key 1 only; key 3 is `"c"` on every side and must load
    as key 3. Today the merged file loads `{2: "c"}`."""
    assert _merged(OURS, theirs, **kwargs) == {"X": {3: "c"}}


def test_positive_control_no_hole_list_take() -> None:
    ours = b'X = {"a", "b", "c"}\n'
    got = _merged(ours, b'X = {"a"}\n', base=luadata.parse(ours))
    assert got == {"X": ["a"]}
