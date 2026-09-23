# Probe from review of m10/04-luadata-parser (fix round 1); reproduces a bare
# identifier after `=` being reported at a later long comment or NUL instead.
"""CONSTRUCTED hostile inputs (L8), labelled in the ids.

`docs/LAB_FORMATS.md` §4.3: "The error carries line, column and the
offending token." After `=`, a bare identifier is refused whatever follows
it (as a value it is either a bare identifier or a call, both refused), so
it is the first offending token. Fix round 1 made `_Diagnoser.value` look
past the name with `skip()`/`token()`, which raise on a long comment or a
NUL, so `X = foo --[[x]]` now reports the long comment at column 9 instead of
`foo` at column 5. The earlier `peek()` comment named this rule ("a
lookahead must not report a later error before the one being diagnosed").

Positive control: after a name that may still be a key (`{ foo --[[x]] = 1
}`), the long comment is the first token the grammar cannot place, and it
is reported (the round-0 probe
`test_m10_04_name_key_error_names_the_wrong_token.py` requires that).
"""

from __future__ import annotations

import pytest

from wowlab_core.luadata import LuaDataError, parse

pytestmark = pytest.mark.parser

CASES = [
    pytest.param(b"X = foo --[[x]]", 5, b"foo", id="constructed-bare-value-then-long-comment"),
    pytest.param(b"X = foo \x00", 5, b"foo", id="constructed-bare-value-then-nul"),
    pytest.param(b"X = { a = foo --[[x]] }", 11, b"foo", id="constructed-keyed-bare-value"),
]
CONTROLS = [
    pytest.param(
        b"X = { foo --[[x]] = 1 }", 11, b"--[[", id="constructed-name-key-then-long-comment"
    ),
]


@pytest.mark.parametrize(("source", "column", "token"), CASES)
def test_bare_identifier_value_is_the_reported_token(
    source: bytes, column: int, token: bytes
) -> None:
    with pytest.raises(LuaDataError) as caught:
        parse(source)
    assert (caught.value.line, caught.value.column, caught.value.token) == (1, column, token)


@pytest.mark.parametrize(("source", "column", "token"), CONTROLS)
def test_positive_control_name_that_may_be_a_key(source: bytes, column: int, token: bytes) -> None:
    with pytest.raises(LuaDataError) as caught:
        parse(source)
    assert (caught.value.line, caught.value.column, caught.value.token) == (1, column, token)
