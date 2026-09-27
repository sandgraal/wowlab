# Probe from review of m10/04-luadata-parser; reproduces a refused `name = value`
# entry blaming the name as a bare identifier when the offender is later.
"""CONSTRUCTED hostile inputs (L8), labelled in the ids.

`docs/LAB_FORMATS.md` §4.3: "The error carries line, column and the
offending token." In `{ a --[[c]] = 1 }` the name `a` followed by `=` is a
legal hand-edited key (§4.1); the first token the grammar refuses is the
long comment `--[[`. `_Diagnoser.entry` decides whether a name is a key by
`peek()`, which stops at anything trivia cannot absorb (a long-comment
opener, a NUL byte) and returns it; that is not `=`, so the name is reported
as a bare identifier at its own column. The input is still refused (the
accept path is not affected); the position, token and message are wrong.

Positive controls: the same offenders after a bracketed key, where
`_Diagnoser.entry` skips trivia with `skip()` and names them correctly.
"""

from __future__ import annotations

import pytest

from wowlab_core.luadata import LuaDataError, parse

pytestmark = pytest.mark.parser

CASES = [
    pytest.param(
        b"X = { a --[[c]] = 1 }", 9, b"--[[", id="constructed-long-comment-after-name-key"
    ),
    pytest.param(
        b"X = { a --[==[c]==] = 1 }", 9, b"--[==[", id="constructed-level2-comment-after-name-key"
    ),
    pytest.param(b"X = { a \x00= 1 }", 9, b"\x00", id="constructed-nul-after-name-key"),
]
CONTROLS = [
    pytest.param(
        b'X = { ["a"] --[[c]] = 1 }', 13, b"--[[", id="constructed-long-comment-after-bracket-key"
    ),
    pytest.param(b'X = { ["a"] \x00= 1 }', 13, b"\x00", id="constructed-nul-after-bracket-key"),
]


@pytest.mark.parametrize(("source", "column", "token"), CASES)
def test_error_names_the_first_refused_token_after_a_name_key(
    source: bytes, column: int, token: bytes
) -> None:
    with pytest.raises(LuaDataError) as caught:
        parse(source)
    assert (caught.value.line, caught.value.column, caught.value.token) == (1, column, token)


@pytest.mark.parametrize(("source", "column", "token"), CONTROLS)
def test_positive_control_error_after_a_bracketed_key(
    source: bytes, column: int, token: bytes
) -> None:
    with pytest.raises(LuaDataError) as caught:
        parse(source)
    assert (caught.value.line, caught.value.column, caught.value.token) == (1, column, token)
