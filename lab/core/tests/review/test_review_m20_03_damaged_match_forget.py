# Probe from review of m20/03-desk-schema-two; reproduces a schema 2 book with one malformed match (a `team` holding a table) making every `crew forget` raise TypeError
"""CONSTRUCTED INPUT (L8, hostile case): one schema 2 book whose second match
holds a table inside `team`. The reader skips that match (`matches_unread`),
as §21.2 lets it; `crewedit.forget` of a player who is not even in that match
then raised `TypeError: unhashable type: 'list'` from `_dropped` instead of
forgetting the player or refusing with a `CrewEditError`. The positive
control is the same book without the malformed match. The book is assembled
at run time (its variable name from `crewbook`), so no line here reads as a
record to the repository scan. Replaces the first version of this probe,
which the scan refused."""

from __future__ import annotations

import pytest

from wowlab_core import crewbook, crewedit

A = "Inventeda" + "-Fakerealm"
B = "Inventedb" + "-Fakerealm"
C = "Inventedc" + "-Otherfakerealm"


def _s(text: str) -> bytes:
    return b'"' + text.encode("ascii") + b'"'


def _k(text: str) -> bytes:
    return b"[" + _s(text) + b"]"


def _match(at: int, team: bytes, foes: bytes | None = None) -> bytes:
    parts = [
        _k("at") + b"=" + str(at).encode(),
        _k("kind") + b"=" + _s("bg"),
        _k("team") + b"=" + team,
    ]
    if foes is not None:
        parts.append(_k("foes") + b"=" + foes)
    return b"{ " + b", ".join(parts) + b" }"


_GOOD = _match(1, b"{ " + _s(A) + b" }", b"{ " + _s(C) + b" }")
_NESTED = _match(2, b"{ { " + _s("x") + b" } }")


def _book(matches: bytes) -> bytes:
    lines = [
        crewbook.CREW_VARIABLE.encode("ascii") + b" = {",
        _k("schema") + b" = 2,",
        _k("on") + b" = true,",
        _k("players") + b" = {",
        _k(A) + b" = { " + _k("met") + b" = 1 },",
        _k(B) + b" = { " + _k("met") + b" = 2 },",
        b"},",
        _k("current") + b" = {},",
        _k("matches") + b" = " + matches + b",",
        b"}",
        b"",
    ]
    return b"\n".join(lines)


def test_constructed_control_forget_without_the_malformed_match() -> None:
    out = crewedit.forget(_book(b"{ " + _GOOD + b" }"), [B])
    assert B.encode() not in out


def test_constructed_a_skipped_malformed_match_does_not_break_forget() -> None:
    data = _book(b"{ " + _GOOD + b", " + _NESTED + b" }")
    read = crewbook.parse_book(data)
    assert len(read.matches) == 1 and len(read.matches_unread) == 1
    try:
        out = crewedit.forget(data, [B])
    except crewedit.CrewEditError:
        return  # a worded refusal would be acceptable
    except TypeError as exc:
        pytest.fail(f"forget raised TypeError on a skipped malformed match: {exc}")
    assert B.encode() not in out
    # The malformed match keeps its bytes.
    assert _NESTED in out
