# Probe from review of m20/03-desk-schema-two; reproduces a schema 2 book with one malformed match (a `team` holding a table) making every `crew forget` raise TypeError
"""CONSTRUCTED INPUT (L8, hostile case): one schema 2 book whose second match
holds a table inside `team`. The reader skips that match (`matches_unread`),
as §21.2 lets it; `crewedit.forget` of a player who is not even in that match
then raises `TypeError: unhashable type: 'list'` from `_dropped` instead of
forgetting the player or refusing with a `CrewEditError`. The positive
control is the same book without the malformed match."""

from __future__ import annotations

import pytest

from wowlab_core import crewbook, crewedit

A = b"Inventeda-Fakerealm"
B = b"Inventedb-Fakerealm"
C = b"Inventedc-Otherfakerealm"

_GOOD = b'{ ["at"]=1, ["kind"]="bg", ["team"]={ "%s" }, ["foes"]={ "%s" } }' % (A, C)
_NESTED = b'{ ["at"]=2, ["kind"]="bg", ["team"]={ { "x" } } }'


def _book(matches: bytes) -> bytes:
    return (
        b'WowLabCrewDB = {\n["schema"] = 2,\n["on"] = true,\n["players"] = {\n'
        b'["%s"] = {\n["met"] = 1,\n},\n["%s"] = {\n["met"] = 2,\n},\n},\n'
        b'["current"] = {\n},\n["matches"] = %s,\n}\n' % (A, B, matches)
    )


def test_constructed_control_forget_without_the_malformed_match() -> None:
    out = crewedit.forget(_book(b"{ %s }" % _GOOD), [B.decode()])
    assert B not in out


def test_constructed_a_skipped_malformed_match_does_not_break_forget() -> None:
    data = _book(b"{ %s, %s }" % (_GOOD, _NESTED))
    read = crewbook.parse_book(data)
    assert len(read.matches) == 1 and len(read.matches_unread) == 1
    try:
        out = crewedit.forget(data, [B.decode()])
    except crewedit.CrewEditError:
        return  # a worded refusal would be acceptable
    except TypeError as exc:
        pytest.fail(f"forget raised TypeError on a skipped malformed match: {exc}")
    assert B not in out
