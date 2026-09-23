"""CONSTRUCTED hostile inputs (L8): top-level assignments count against the
`wowlab_core.luadata` cost budget (`docs/LAB_PLAN.md` §6.4, amendment of
2026-09-23; M10-04 fix round 2, security finding 1).

Before the budget counted them, `a=1\\n` repeated to `MAX_FILE_BYTES` was 67
million assignments and about 10.8 GB. Every input here is constructed, and
the test ids say so. No install, no fixture tree.
"""

from __future__ import annotations

import pytest

from wowlab_core import luadata

pytestmark = pytest.mark.parser


def test_constructed_repeated_assignments_past_the_budget_are_refused() -> None:
    """64 MiB of `a=1\\n` (16.8 million assignments, all identical, so each is
    shared whole) is inside `MAX_FILE_BYTES` but over `MAX_COST`: refused
    with a position on an assignment, not parsed."""
    data = b"\r\n" + b"a=1\n" * (16 * 1024 * 1024)
    with pytest.raises(luadata.LuaLimitError) as caught:
        luadata.parse(data)
    err = caught.value
    assert err.token.startswith(b"a=1")
    assert err.column == 1
    assert err.line > 2


@pytest.mark.parametrize(
    "item",
    [b"a=1\n", b"a = 'x'\n", b"a=nil\n", b"a={}\n"],
    ids=["constructed-number", "constructed-string", "constructed-nil", "constructed-table"],
)
def test_constructed_assignments_are_charged_like_entries(
    item: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the budget lowered, a run of top-level assignments crosses it at
    an assignment and raises; the first few fit."""
    monkeypatch.setattr(luadata, "MAX_COST", 20_000)
    fits = luadata.parse(b"\r\n" + item * 10)
    assert len(fits.assignments) == 10
    with pytest.raises(luadata.LuaLimitError) as caught:
        luadata.parse(b"\r\n" + item * 1000)
    assert caught.value.token.startswith(item[:1])
    assert caught.value.column == 1


def test_constructed_distinct_assignments_are_charged_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinct assignments build their own objects, so fewer of them fit the
    same budget than identical ones."""
    monkeypatch.setattr(luadata, "MAX_COST", 100_000)

    def fitting(item: bytes | None) -> int:
        lines = [item or b"v%06d=%06d\n" % (i, i) for i in range(5000)]
        data = b"\r\n" + b"".join(lines)
        with pytest.raises(luadata.LuaLimitError) as caught:
            luadata.parse(data)
        return caught.value.line - 2

    assert fitting(None) < fitting(b"v000000=000000\n")
