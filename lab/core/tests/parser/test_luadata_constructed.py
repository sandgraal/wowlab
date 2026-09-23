"""Graders for `wowlab_core.luadata` on constructed inputs (M10-04T; L3, L4).

Every input in this file is constructed, which the file name puts in every
test id: hostile and boundary cases (`docs/LAB_FORMATS.md` §4.3 and the
bounds of `docs/LAB_PLAN.md` §6.4), and the grammar points of §4.1 and §4.2
(with the 2026-09-22 amendments to both) that no real capture shows yet:
tab indentation, comments in every position, `;` separators, an Ace3
profile, `[number]`, bare-name and boolean keys, single-quoted, non-ASCII
and invalid-UTF-8 strings, the Lua 5.1 escapes, raw control bytes,
duplicates, non-finite spellings, number text fidelity, a multi-MB
document. The real captures are graded in `test_luadata_fixtures.py`,
whose docstring pins the seam these tests use. Every grader carries one
marker line that M10-04 deletes; nothing else here is the implementer's.

The client's Lua is taken to be Lua 5.1 (**[verify]** for Forever), so
escapes, key equality and the positional flush follow Lua 5.1.

Positions: a document here starts with the client's blank line, so its
first assignment is on line 2. Where §4.3 does not say which of two tokens
is "the offending token" (a call reported at the callee or at its `(`, an
unterminated construct at its opening or where it breaks off, a bad escape
at its backslash or at the string's quote), both positions are accepted and
nothing else is.
"""

from __future__ import annotations

import ast
import math
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from _luadata_oracle import (
    BOOLEAN,
    NAME,
    NUMBER,
    POSITIONAL,
    STRING,
    document_tokens,
    load,
    path,
    rebuild,
    source_tokens,
    tables,
)

pytestmark = pytest.mark.parser

MiB = 1024 * 1024
EOLS = [b"\r\n", b"\n", b"\r"]
EOL_IDS = ["crlf", "lf", "cr"]


@pytest.fixture
def luadata() -> Any:
    return load()


def _doc(*lines: str | bytes, eol: bytes = b"\r\n") -> bytes:
    """A document in the client's shape: a leading blank line, then `lines`,
    each ended by `eol`."""
    body = [line.encode("utf-8") if isinstance(line, str) else line for line in lines]
    return eol + b"".join(line + eol for line in body)


def _only_value(luadata: Any, data: bytes) -> Any:
    doc = luadata.parse(data)
    [assignment] = doc.assignments
    return assignment.value


def _faithful(luadata: Any, data: bytes) -> Any:
    """Parse, and check the document alone rebuilds `data` with the same
    tokens. Returns the document."""
    doc = luadata.parse(data)
    assert rebuild(luadata, doc) == data
    assert document_tokens(luadata, doc) == source_tokens(data)
    return doc


def _rejected(luadata: Any, data: bytes, candidates: list[tuple[int, int, str]]) -> Any:
    """`data` is refused with a positioned `LuaDataError` (not a bound
    error) at one of `candidates`: (line, column, prefix of the token)."""
    with pytest.raises(luadata.LuaDataError) as info:
        luadata.parse(data)
    err = info.value
    assert not isinstance(err, luadata.LuaLimitError), err
    assert isinstance(err.line, int)
    assert isinstance(err.column, int)
    token = err.token or ""
    if isinstance(token, bytes):
        token = token.decode("utf-8", "surrogateescape")
    assert isinstance(token, str)
    assert any(
        (err.line, err.column) == (line, column) and token.startswith(prefix)
        for line, column, prefix in candidates
    ), f"line {err.line}, column {err.column}, token {token!r}; expected one of {candidates}"
    return err


# ── §4.2 as the reference writes it: tabs and `-- [n]` (optional style) ─────

EXAMPLE_LINES = [
    "MyAddonDB = {",
    '\t["profileKeys"] = {',
    '\t\t["Name - Realm"] = "Default",',
    "\t},",
    '\t["list"] = {',
    '\t\t"first", -- [1]',
    '\t\t"second", -- [2]',
    "\t},",
    "\t[42] = true,",
    '\t["scale"] = 0.8500000238418579,',
    "}",
    'OtherVar = "text"',
]

EXAMPLE_PY = {
    "MyAddonDB": {
        "profileKeys": {"Name - Realm": "Default"},
        "list": ["first", "second"],
        42: True,
        "scale": 0.8500000238418579,
    },
    "OtherVar": "text",
}


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("eol", EOLS, ids=EOL_IDS)
def test_reference_example_rebuilds_byte_for_byte(luadata: Any, eol: bytes) -> None:
    """The §4.2 example, tab-indented with `-- [n]` comments, in each line
    ending: the document alone gives it back."""
    doc = _faithful(luadata, _doc(*EXAMPLE_LINES, eol=eol))
    assert doc.to_python() == EXAMPLE_PY


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_reference_example_key_styles_and_comments(luadata: Any) -> None:
    doc = luadata.parse(_doc(*EXAMPLE_LINES))
    assert [a.name for a in doc.assignments] == ["MyAddonDB", "OtherVar"]
    root = doc.assignments[0].value
    assert [e.style for e in root.entries] == [STRING, STRING, NUMBER, STRING]
    assert [e.comment for e in root.entries] == [None, None, None, None]
    number_key = root.entries[2].key
    assert isinstance(number_key, luadata.LuaNumber)
    assert (number_key.raw, number_key.as_int()) == ("42", 42)
    items = path(luadata, doc, "MyAddonDB", "list")
    assert [e.style for e in items.entries] == [POSITIONAL, POSITIONAL]
    assert [e.key for e in items.entries] == [None, None]
    assert [e.value.data for e in items.entries] == [b"first", b"second"]
    assert [e.comment for e in items.entries] == [b"-- [1]", b"-- [2]"]
    assert path(luadata, doc, "MyAddonDB", "scale").raw == "0.8500000238418579"


ACE3_LINES = [
    "MyAceAddonDB = {",
    '\t["namespaces"] = {',
    '\t\t["Minimap"] = {',
    '\t\t\t["profiles"] = {',
    '\t\t\t\t["Default"] = {',
    '\t\t\t\t\t["hide"] = true,',
    "\t\t\t\t},",
    "\t\t\t},",
    "\t\t},",
    "\t},",
    '\t["profileKeys"] = {',
    '\t\t["Labchara - Labrealma"] = "Default",',
    '\t\t["Labcharb - Labrealma"] = "Healer",',
    "\t},",
    '\t["profiles"] = {',
    '\t\t["Default"] = {',
    '\t\t\t["scale"] = 1.25,',
    '\t\t\t["tracked"] = {',
    "\t\t\t\t12345, -- [1]",
    "\t\t\t\t67890, -- [2]",
    "\t\t\t},",
    "\t\t},",
    '\t\t["Healer"] = {',
    "\t\t},",
    "\t},",
    "}",
]


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_ace3_profile_rebuilds_and_converts(luadata: Any) -> None:
    """An AceDB-shaped document (namespaces, profileKeys, profiles), as the
    corpus lacks one."""
    doc = _faithful(luadata, _doc(*ACE3_LINES))
    assert max(d for d, _t in tables(luadata, doc)) == 5
    py = doc.to_python()["MyAceAddonDB"]
    assert py["namespaces"]["Minimap"]["profiles"]["Default"] == {"hide": True}
    assert py["profileKeys"] == {
        "Labchara - Labrealma": "Default",
        "Labcharb - Labrealma": "Healer",
    }
    assert py["profiles"]["Default"]["tracked"] == [12345, 67890]
    assert py["profiles"]["Default"]["scale"] == 1.25
    assert py["profiles"]["Healer"] == {}
    tracked = path(luadata, doc, "MyAceAddonDB", "profiles", "Default", "tracked")
    assert [e.comment for e in tracked.entries] == [b"-- [1]", b"-- [2]"]


# ── trivia: comments and spacing in every position (§6.4 amendment item 7) ──

EVERYWHERE = [
    "-- header",
    "",
    "X = { -- after the opening brace",
    "  -- on its own line inside the table",
    '  "a" ; -- after a separator',
    '  "b" -- between a value and its separator',
    "  ,",
    '  [ "k" ]  =  1 ,',
    "  name=2;",
    "  [3]=true -- after a value with no separator",
    "} -- after a top-level value",
    "-- between assignments",
    "Y   =\t's'",
    "-- footer",
]


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("eol", EOLS, ids=EOL_IDS)
def test_comments_and_spacing_everywhere_rebuild_byte_for_byte(luadata: Any, eol: bytes) -> None:
    """A comment the client never writes is still kept (L4), wherever it
    is; mixed `,`/`;`, odd spacing and each line ending survive too."""
    data = b"".join(line.encode() + eol for line in EVERYWHERE)
    doc = _faithful(luadata, data)
    assert doc.assignments[0].lead == b"-- header" + eol + eol
    assert doc.tail == eol + b"-- footer" + eol
    table = doc.assignments[0].value
    assert [e.sep or b"" for e in table.entries] == [b";", b",", b",", b";", b""]
    assert table.entries[0].comment == b"-- after a separator"
    assert table.entries[4].comment == b"-- after a value with no separator"
    assert doc.to_python() == {"X": {1: "a", 2: "b", "k": 1, "name": 2, 3: True}, "Y": "s"}


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_line_comments_that_look_like_brackets(luadata: Any) -> None:
    """`-- [1]` and `--[1]` are line comments; so is `--[=x` (no second
    `[`, so not a long-comment opener)."""
    doc = _faithful(luadata, _doc("X = {", '"a", -- [1]', '"b", --[1]', '"c", --[=x', "}"))
    table = doc.assignments[0].value
    assert [e.comment for e in table.entries] == [b"-- [1]", b"--[1]", b"--[=x"]


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_trailing_comments_are_kept_verbatim_on_their_entry(luadata: Any) -> None:
    """From `--` to the line break, trailing spaces included; on a
    table-valued entry the comment follows its closing `},`."""
    doc = _faithful(
        luadata,
        _doc(
            "X = {",
            '"a", -- [1]',
            '["k"] = 1, --   spaced  text  ',
            "[5] = true,",
            '["t"] = {',
            '"inner", -- [1]',
            "}, -- end of t",
            "}",
        ),
    )
    table = doc.assignments[0].value
    assert [e.comment for e in table.entries] == [
        b"-- [1]",
        b"--   spaced  text  ",
        None,
        b"-- end of t",
    ]
    assert [e.comment for e in table.entries[3].value.entries] == [b"-- [1]"]


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("data", [b"", b"\r\n", b"\n\n", b"-- only a comment\r\n", b"\r"], ids=repr)
def test_document_with_no_assignment(luadata: Any, data: bytes) -> None:
    """§4.1: a document is any sequence of blanks, comments and assignments,
    none included; all its bytes are the tail."""
    doc = luadata.parse(data)
    assert list(doc.assignments) == []
    assert doc.tail == data or (data == b"" and not doc.tail)
    assert rebuild(luadata, doc) == data
    assert doc.to_python() == {}


# ── key styles and order ────────────────────────────────────────────────────

KEY_STYLE_LINES = [
    "X = {",
    '"pos", -- [1]',
    '["str"] = 1,',
    "['sq'] = 2,",
    "[7] = 3,",
    "[-1.5] = 4,",
    "[0x10] = 5,",
    "name_key = 6,",
    "[true] = 7,",
    "[false] = 8,",
    "}",
]


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_every_key_style_is_kept(luadata: Any) -> None:
    doc = _faithful(luadata, _doc(*KEY_STYLE_LINES))
    table = doc.assignments[0].value
    assert [e.style for e in table.entries] == [
        POSITIONAL,
        STRING,
        STRING,
        NUMBER,
        NUMBER,
        NUMBER,
        NAME,
        BOOLEAN,
        BOOLEAN,
    ]
    keys = table.entries
    assert keys[0].key is None
    assert (keys[1].key.data, keys[1].key.raw) == (b"str", b'"str"')
    assert (keys[2].key.data, keys[2].key.raw) == (b"sq", b"'sq'")
    assert keys[3].key.raw == "7"
    assert keys[4].key.raw == "-1.5"
    assert keys[4].key.as_float() == -1.5
    assert (keys[5].key.raw, keys[5].key.as_int()) == ("0x10", 16)
    assert keys[6].key == "name_key"
    assert isinstance(keys[7].key, luadata.LuaBool)
    assert keys[7].key.value is True
    assert isinstance(keys[8].key, luadata.LuaBool)
    assert keys[8].key.value is False
    assert [e.value.raw for e in keys[1:]] == [str(n) for n in range(1, 9)]


ORDER = [
    '["zeta"] = 1,',
    "[3] = 2,",
    '"first positional",',
    '["alpha"] = 3,',
    "mid = 4,",
    "[-2] = 5,",
    '["Beta"] = 6,',
    '"second positional",',
    "[0.25] = 7,",
    '["beta"] = 8,',
    '[""] = 9,',
    "[1e+15] = 10,",
    "aardvark = 11,",
    '["10"] = 12,',
    "[10] = 13,",
]


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_key_order_is_source_order(luadata: Any) -> None:
    """Order is the file's, not sorted by key, style or kind."""
    doc = _faithful(luadata, _doc("X = {", *ORDER, "}"))
    table = doc.assignments[0].value
    assert len(table.entries) == len(ORDER)
    keyed = [e.value.raw for e in table.entries if e.style != POSITIONAL]
    assert keyed == [str(n) for n in range(1, 14)]
    positional = [e.value.data for e in table.entries if e.style == POSITIONAL]
    assert positional == [b"first positional", b"second positional"]
    assert [e.style for e in table.entries].index(POSITIONAL) == 2


# ── numbers ─────────────────────────────────────────────────────────────────
#
# Every Lua 5.1 number is a double. The document keeps each number's text;
# `as_int()` reads the integer the text spells, which for 2**53 + 1 is not
# the double the client loads; `to_python()` gives `int` or `float` by
# spelling, not by a client type.

NUMBER_TEXTS = [
    "0",
    "-0",
    "-0.0",
    "42",
    "-260",
    "0.5",
    "0.8500000238418579",
    "0.0117647058823529",
    "1.0",
    "100.000",
    "1e+15",
    "1E-07",
    "2.5e10",
    "0x1F",
    "0XfF",
    "-0x10",
    "9007199254740993",
    "1.7976931348623157e+308",
    "5e-324",
]


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("text", NUMBER_TEXTS)
def test_number_source_text_is_kept(luadata: Any, text: str) -> None:
    """As a top-level value, a table value, a positional entry and a key.
    Several of these (`100.000`, `1E-07`, `0x1F`, `-0`, `9007199254740993`)
    are not what `repr(float(text))` prints, so normalising text fails."""
    doc = _faithful(
        luadata,
        _doc(f"X = {text}", "Y = {", f'["v"] = {text},', f"{text},", f"[{text}] = true,", "}"),
    )
    top = doc.assignments[0].value
    assert isinstance(top, luadata.LuaNumber)
    assert top.raw == text
    table = doc.assignments[1].value
    assert [e.value.raw for e in table.entries[:2]] == [text, text]
    assert table.entries[2].style == NUMBER
    assert table.entries[2].key.raw == text


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", 0),
        ("42", 42),
        ("-260", -260),
        ("0x1F", 31),
        ("0XfF", 255),
        ("-0x10", -16),
        ("9007199254740993", 9007199254740993),
    ],
)
def test_number_as_int(luadata: Any, text: str, expected: int) -> None:
    """Exact, from the text: 2**53 + 1 matches the text, not the double the
    client would load for it."""
    value = _only_value(luadata, _doc(f"X = {text}")).as_int()
    assert type(value) is int
    assert value == expected


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.8500000238418579", 0.8500000238418579),
        ("0.0117647058823529", 0.0117647058823529),
        ("1e+15", 1e15),
        ("1E-07", 1e-07),
        ("2.5e10", 2.5e10),
        ("-260", -260.0),
        ("0x1F", 31.0),
        ("100.000", 100.0),
        ("5e-324", 5e-324),
        ("1.7976931348623157e+308", 1.7976931348623157e308),
    ],
)
def test_number_as_float(luadata: Any, text: str, expected: float) -> None:
    value = _only_value(luadata, _doc(f"X = {text}")).as_float()
    assert type(value) is float
    assert value == expected


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("text", ["-0", "-0.0"])
def test_negative_zero_keeps_its_sign(luadata: Any, text: str) -> None:
    number = _only_value(luadata, _doc(f"X = {text}"))
    assert number.raw == text
    assert math.copysign(1.0, number.as_float()) == -1.0


NON_FINITE = ["inf", "-inf", "nan", "-nan", "1.#INF", "-1.#INF", "1.#IND", "-1.#IND"]
NON_FINITE += ["-nan(ind)", "1.#QNAN", "INF", "NaN", "math.huge"]


def _within(line: int, column: int, text: str) -> list[tuple[int, int, str]]:
    """Any position inside `text`, starting at `column`, with the token
    starting at that character."""
    return [(line, column + i, ch) for i, ch in enumerate(text)]


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("text", NON_FINITE)
def test_unlisted_non_finite_spelling_raises_with_position(luadata: Any, text: str) -> None:
    """§4.2: no fixture has shown a non-finite spelling, so none is listed
    and each raises with line and column (§6.4). These are spellings
    reported for various clients; nothing here says what the Forever client
    writes or what it would load. A spelling a capture shows is listed by an
    amendment, which also takes it out of this table."""
    _rejected(luadata, _doc(f"X = {text}"), _within(2, 5, text))
    _rejected(luadata, _doc("X = {", f'["v"] = {text},', "}"), _within(3, 9, text))


# ── strings are bytes (§6.4 amendment items 1 to 3) ─────────────────────────

ESCAPES = [
    ("quote", rb'"a\"b"', b'a"b'),
    ("backslash", rb'"C:\\x"', b"C:\\x"),
    ("newline", rb'"l1\nl2"', b"l1\nl2"),
    ("carriage-return", rb'"cr\r"', b"cr\r"),
    ("bell-backspace-formfeed-vtab", rb'"\a\b\f\v"', b"\x07\x08\x0c\x0b"),
    ("tab", rb'"tab\there"', b"tab\there"),
    ("apostrophe", rb'"it\'s"', b"it's"),
    ("apostrophe-single-quoted", rb"'\''", b"'"),
    ("quote-single-quoted", rb"'\"'", b'"'),
    ("decimal-1-digit", rb'"\1"', b"\x01"),
    ("decimal-3-digits", rb'"\001"', b"\x01"),
    ("decimal-zero", rb'"\000"', b"\x00"),
    ("decimal-127", rb'"\127"', b"\x7f"),
    ("decimal-stops-at-3-digits", rb'"\0651"', b"A1"),
    ("decimal-tab-and-newline", rb'"\9\10"', b"\t\n"),
    ("decimal-utf8-pair", rb'"\195\169"', b"\xc3\xa9"),
    ("decimal-lone-high-byte", rb'"\233"', b"\xe9"),
    ("decimal-255", rb'"\255"', b"\xff"),
    ("raw-quote-in-single-quotes", b"'a\"b'", b'a"b'),
    ("raw-apostrophe", b'"it\'s"', b"it's"),
    ("empty", b'""', b""),
]


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize(
    ("literal", "data"), [(lit, d) for _i, lit, d in ESCAPES], ids=[i for i, _l, _d in ESCAPES]
)
def test_string_escapes_decode_to_bytes_and_raw_is_kept(
    luadata: Any, literal: bytes, data: bytes
) -> None:
    """Lua 5.1 escapes with Lua 5.1 meanings; `\\ddd` is one byte."""
    doc = _faithful(luadata, _doc(b"X = " + literal))
    string = doc.assignments[0].value
    assert isinstance(string, luadata.LuaString)
    assert string.data == data
    assert string.value == data.decode("utf-8", "surrogateescape")
    assert string.raw == literal


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_decimal_escapes_make_bytes_not_code_points(luadata: Any) -> None:
    assert _only_value(luadata, _doc(r'X = "\195\169"')).value == "é"
    assert _only_value(luadata, _doc(r'X = "\233"')).value == "\udce9"


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("brk", [b"\r\n", b"\n\r", b"\n", b"\r"], ids=["crlf", "lfcr", "lf", "cr"])
def test_backslash_line_break_is_one_newline_and_one_line(luadata: Any, brk: bytes) -> None:
    """A backslash before a line break decodes to `"\\n"` and counts one
    line, whichever of CRLF, LFCR, LF or CR it is; the error on the next
    line after it reports that line."""
    literal = b'"a\\' + brk + b'b"'
    doc = _faithful(luadata, b"\r\nX = " + literal + b"\r\n")
    string = doc.assignments[0].value
    assert (string.data, string.raw) == (b"a\nb", literal)
    _rejected(luadata, b"\r\nX = " + literal + b"\r\nY = foo\r\n", [(4, 5, "foo")])


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_invalid_utf8_is_kept_as_bytes(luadata: Any) -> None:
    """A name cut mid-character: parsed, kept, rebuilt byte for byte."""
    doc = _faithful(luadata, b'\r\nX = {\r\n["Gr\xc3"] = "Gr\xc3",\r\n}\r\n')
    [only] = doc.assignments[0].value.entries
    assert (only.key.data, only.key.value, only.key.raw) == (b"Gr\xc3", "Gr\udcc3", b'"Gr\xc3"')
    assert (only.value.data, only.value.value) == (b"Gr\xc3", "Gr\udcc3")
    assert doc.to_python() == {"X": {"Gr\udcc3": "Gr\udcc3"}}


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_raw_control_bytes_in_a_string_are_kept(luadata: Any) -> None:
    """A raw tab, 0x02, DEL or ESC inside a literal is kept (the same client
    writes raw 0x02 into config-cache.wtf). A raw NUL is rejected instead
    (§4.3; **[verify]**), see the rejection table."""
    literal = b'"a\tb\x02c\x7f\x1b"'
    string = _faithful(luadata, _doc(b"X = " + literal)).assignments[0].value
    assert (string.data, string.raw) == (b"a\tb\x02c\x7f\x1b", literal)


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_non_ascii_strings_are_raw_utf8(luadata: Any) -> None:
    """UTF-8 is written raw (§4.2); the corpus has no non-ASCII string."""
    doc = _faithful(luadata, _doc("X = {", '["Ælfrïc"] = "Grüße — 名前 🐉",', "}"))
    [only] = doc.assignments[0].value.entries
    assert only.key.value == "Ælfrïc"
    assert only.key.raw == '"Ælfrïc"'.encode()
    assert only.value.value == "Grüße — 名前 🐉"
    assert only.value.data == "Grüße — 名前 🐉".encode()


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_double_dash_inside_a_string_is_not_a_comment(luadata: Any) -> None:
    doc = _faithful(luadata, _doc("X = {", '["url"] = "a--b",', '["k"] = "--", -- real', "}"))
    first, second = doc.assignments[0].value.entries
    assert (first.value.data, first.comment) == (b"a--b", None)
    assert (second.value.data, second.comment) == (b"--", b"-- real")


# ── the accepted superset ───────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b'X={["a"]=1,[2]=true,b="c"}', {"X": {"a": 1, 2: True, "b": "c"}}),
        (b'\nX = {\n["a"] = 1,\n}\n', {"X": {"a": 1}}),
        (b'\rX = {\r["a"] = 1,\r}\r', {"X": {"a": 1}}),
        (b"X = 1\r\n", {"X": 1}),
        (b"\r\nX = 1", {"X": 1}),
        (b"X = {1; 2; 3}", {"X": [1, 2, 3]}),
        (b"X = {1, 2; 3,}", {"X": [1, 2, 3]}),
        (b'X = {"a", "b"}', {"X": ["a", "b"]}),
        (
            b'\r\nA = "s"\r\nB = 1\r\nC = true\r\nD = false\r\n',
            {"A": "s", "B": 1, "C": True, "D": False},
        ),
        (b' X\t=\t{ [ "a" ] = 1 , } ', {"X": {"a": 1}}),
        (b"\r\nX = 1\r\n\r\n\r\nY = 2\r\n", {"X": 1, "Y": 2}),
        (b"\r\nX = 1\r\n-- note\r\nY = 2\r\n", {"X": 1, "Y": 2}),
        (b'X = {[-1] = "m"}', {"X": {-1: "m"}}),
        (b"X = {{1, 2}, {3}}", {"X": [[1, 2], [3]]}),
        (b'X = {a = 1, _b2 = "x"}', {"X": {"a": 1, "_b2": "x"}}),
        (b"_G2 = 1\r\nx_Y = 2\r\n", {"_G2": 1, "x_Y": 2}),
        (b"X = 'it'", {"X": "it"}),
    ],
    ids=[
        "compact",
        "lf-endings",
        "cr-endings",
        "no-leading-blank-line",
        "no-final-line-ending",
        "semicolons",
        "mixed-separators",
        "no-trailing-separator",
        "top-level-scalars",
        "spaces-and-tabs-between-tokens",
        "blank-lines-between-assignments",
        "comment-line-between-assignments",
        "negative-number-key",
        "nested-positional",
        "bare-name-keys",
        "identifier-shapes",
        "single-quoted-top-level",
    ],
)
def test_accepted_grammar(luadata: Any, data: bytes, expected: dict[str, Any]) -> None:
    assert _faithful(luadata, data).to_python() == expected


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("data", [b"X = {}", b"\r\nX = {\r\n}\r\n"], ids=["inline", "two-lines"])
def test_empty_table(luadata: Any, data: bytes) -> None:
    """An empty table converts to `{}` (§6.4 amendment item 4)."""
    doc = _faithful(luadata, data)
    table = doc.assignments[0].value
    assert isinstance(table, luadata.LuaTable)
    assert len(table.entries) == 0
    converted = doc.to_python()["X"]
    assert converted == {}
    assert type(converted) is dict


# ── duplicates ──────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize(
    ("body", "flags"),
    [
        ('["a"] = 1, ["a"] = 2', [False, True]),
        ('["a"] = 1, a = 2', [False, True]),
        ("a = 1, a = 2", [False, True]),
        ('"x", [1] = "y"', [False, True]),
        ('[1] = "y", "x"', [False, True]),
        ('[2] = "x", "a", "b"', [False, False, True]),
        ('[1] = "x", [1.0] = "y"', [False, True]),
        ('[16] = "x", [0x10] = "y"', [False, True]),
        ("[true] = 1, [true] = 2", [False, True]),
        ('[1] = "x", ["1"] = "y"', [False, False]),
        ("[true] = 1, [1] = 2", [False, False]),
        ('["a"] = 1, ["b"] = 2, ["a"] = 3, ["a"] = 4', [False, False, True, True]),
        ('["t"] = {["a"] = 1}, ["u"] = {["a"] = 1}', [False, False]),
    ],
    ids=[
        "string-twice",
        "string-then-name",
        "name-twice",
        "positional-then-bracketed-1",
        "bracketed-1-then-positional",
        "bracketed-2-then-second-positional",
        "integer-then-integral-float",
        "decimal-then-hex",
        "boolean-twice",
        "number-and-string-differ",
        "boolean-and-number-differ",
        "three-of-one-key",
        "same-key-in-sibling-tables",
    ],
)
def test_duplicates_are_kept_in_order_and_the_later_is_flagged(
    luadata: Any, body: str, flags: list[bool]
) -> None:
    doc = _faithful(luadata, _doc("X = {" + body + "}"))
    table = doc.assignments[0].value
    assert [e.duplicate for e in table.entries] == flags
    for _d, inner in list(tables(luadata, doc))[1:]:
        assert [e.duplicate for e in inner.entries] == [False] * len(inner.entries)


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_duplicate_top_level_assignments_are_kept(luadata: Any) -> None:
    """The client's loader runs the file top to bottom, so `to_python()`
    shows what it would see: the last one."""
    doc = _faithful(luadata, _doc("X = 1", "Y = 2", "X = 3"))
    assert [(a.name, a.value.raw) for a in doc.assignments] == [("X", "1"), ("Y", "2"), ("X", "3")]
    assert doc.to_python() == {"X": 3, "Y": 2}


# ── to_python ───────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('[1] = "a", [2] = "b"', ["a", "b"]),
        ('[2] = "b", [1] = "a"', ["a", "b"]),
        ('"a", "b", [3] = "c"', ["a", "b", "c"]),
        ('[1] = "a", [3] = "c"', {1: "a", 3: "c"}),
        ('[0] = "z", [1] = "a"', {0: "z", 1: "a"}),
        ('"a", ["x"] = 1', {1: "a", "x": 1}),
        ('[1.5] = "x"', {1.5: "x"}),
        ('["a"] = 1, ["a"] = 2', {"a": 2}),
        ('[1] = "a", [1] = "b"', ["b"]),
        ("a = 1, b = true", {"a": 1, "b": True}),
        ('["1"] = "s"', {"1": "s"}),
    ],
    ids=[
        "keys-1-2-bracketed",
        "keys-1-2-out-of-order",
        "positional-then-bracketed-3",
        "gap-is-a-dict",
        "zero-key-is-a-dict",
        "mixed-is-a-dict",
        "float-key",
        "duplicate-string-key-last-wins",
        "duplicate-bracketed-key-last-wins",
        "bare-names",
        "string-1-is-not-an-index",
    ],
)
def test_to_python_lists_only_for_keys_exactly_1_to_n(
    luadata: Any, body: str, expected: Any
) -> None:
    got = luadata.parse(_doc("X = {" + body + "}")).to_python()["X"]
    assert got == expected
    assert type(got) is type(expected)


def _batch(count: int, key: int) -> bytes:
    items = ", ".join(f'"p{i}"' for i in range(1, count + 1))
    return _doc("X = {" + items + f', [{key}] = "y"' + "}")


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize(
    ("count", "key", "winner"),
    [
        (1, 1, "p1"),
        (49, 1, "p1"),
        (50, 1, "y"),
        (51, 1, "y"),
        (50, 50, "y"),
        (51, 51, "p51"),
        (100, 100, "y"),
        (101, 101, "p101"),
    ],
    ids=lambda v: str(v),
)
def test_positional_against_bracketed_follows_lua_51_flushes(
    luadata: Any, count: int, key: int, winner: str
) -> None:
    """§6.4 amendment item 6 (**[verify]**): Lua 5.1 stores pending
    positional entries 50 at a time, before the next field once 50 are
    pending and at the closing brace, while a bracketed entry is stored when
    it is reached. So a bracketed key wins over a positional entry already
    flushed, and loses to one still pending. The later entry in the source
    is flagged either way."""
    doc = luadata.parse(_batch(count, key))
    table = doc.assignments[0].value
    assert [e.duplicate for e in table.entries] == [False] * count + [True]
    got = doc.to_python()["X"]
    assert type(got) is list
    assert len(got) == count
    assert got[key - 1] == winner


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_bracketed_before_positional_loses(luadata: Any) -> None:
    """`[1]` is stored when reached; the positional `"x"` is stored at the
    closing brace, after it."""
    assert luadata.parse(_doc('X = {[1] = "y", "x"}')).to_python() == {"X": ["x"]}
    assert luadata.parse(_doc('X = {[2] = "x", "a", "b"}')).to_python() == {"X": ["a", "b"]}


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_to_python_keeps_a_top_level_nil(luadata: Any) -> None:
    """§6.4 amendment item 4: `X = nil` differs from a file that never
    names `X`."""
    with_nil = luadata.parse(_doc("X = nil", "Y = 1")).to_python()
    assert "X" in with_nil
    assert with_nil["X"] is None
    assert "X" not in luadata.parse(_doc("Y = 1")).to_python()


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize(
    ("text", "expected", "kind"),
    [("42", 42, int), ("-7", -7, int), ("0x1F", 31, int), ("0.5", 0.5, float), ("1.0", 1.0, float)],
)
def test_to_python_number_type_follows_the_spelling(
    luadata: Any, text: str, expected: Any, kind: type
) -> None:
    """Every Lua 5.1 number is a double; `int` or `float` here follows how
    the number is written (`1.0` is a float, `0x1F` an int)."""
    got = luadata.parse(_doc(f"X = {text}")).to_python()["X"]
    assert got == expected
    assert type(got) is kind


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_to_python_is_one_way(luadata: Any) -> None:
    """A fresh copy each call: changing it changes neither the document nor
    the next call's result."""
    data = _doc(*EXAMPLE_LINES)
    doc = luadata.parse(data)
    first = doc.to_python()
    first["MyAddonDB"]["list"].append("third")
    first["MyAddonDB"]["profileKeys"]["Name - Realm"] = "Other"
    first["OtherVar"] = "changed"
    assert doc.to_python() == EXAMPLE_PY
    assert rebuild(luadata, doc) == data


# ── §4.3 rejections ─────────────────────────────────────────────────────────

QUOTE_OR_BACKSLASH = [(2, 6, "\\"), (2, 5, '"')]

REJECTIONS: list[tuple[str, bytes, list[tuple[int, int, str]]]] = [
    ("function-value", _doc("X = function() end"), [(2, 5, "function")]),
    ("function-in-table", _doc("X = {", '["f"] = function() end,', "}"), [(3, 9, "function")]),
    ("function-positional", _doc("X = {", "function() end,", "}"), [(3, 1, "function")]),
    ("call-value", _doc("X = f(1)"), [(2, 5, "f"), (2, 6, "(")]),
    ("call-in-table", _doc("X = {", '["k"] = print("x"),', "}"), [(3, 9, "print"), (3, 14, "(")]),
    ("call-string-sugar", _doc('X = f"s"'), [(2, 5, "f"), (2, 6, '"')]),
    ("call-table-sugar", _doc("X = f{}"), [(2, 5, "f"), (2, 6, "{")]),
    ("method-call", _doc("X = a:b()"), [(2, 5, "a"), (2, 6, ":")]),
    ("call-statement", _doc('print("x")'), [(2, 1, "print"), (2, 6, "(")]),
    (
        "setmetatable-value",
        _doc("X = setmetatable({}, {})"),
        [(2, 5, "setmetatable"), (2, 17, "(")],
    ),
    (
        "setmetatable-statement",
        _doc("X = {}", "setmetatable(X, {})"),
        [(3, 1, "setmetatable"), (3, 13, "(")],
    ),
    ("concat-strings", _doc('X = "a" .. "b"'), [(2, 9, ".")]),
    ("concat-numbers", _doc("X = 1 .. 2"), [(2, 7, ".")]),
    ("add", _doc("X = 1 + 2"), [(2, 7, "+")]),
    ("subtract", _doc("X = 5 - 3"), [(2, 7, "-")]),
    ("multiply", _doc("X = 2 * 3"), [(2, 7, "*")]),
    ("divide", _doc("X = 10 / 2"), [(2, 8, "/")]),
    ("zero-over-zero", _doc("X = 0/0"), [(2, 6, "/")]),
    ("modulo", _doc("X = 7 % 2"), [(2, 7, "%")]),
    ("power", _doc("X = 2 ^ 3"), [(2, 7, "^")]),
    ("length", _doc("X = #t"), [(2, 5, "#")]),
    ("unary-minus-on-a-name", _doc("X = -x"), [(2, 5, "-"), (2, 6, "x")]),
    ("parenthesised", _doc("X = (1)"), [(2, 5, "(")]),
    ("arithmetic-in-table", _doc("X = {", '["k"] = 1 + 2,', "}"), [(3, 11, "+")]),
    ("equal", _doc("X = 1 == 1"), [(2, 7, "=")]),
    ("not-equal", _doc("X = 1 ~= 2"), [(2, 7, "~")]),
    ("less", _doc("X = 1 < 2"), [(2, 7, "<")]),
    ("less-or-equal", _doc("X = 1 <= 2"), [(2, 7, "<")]),
    ("greater", _doc("X = 1 > 2"), [(2, 7, ">")]),
    ("greater-or-equal", _doc("X = 1 >= 2"), [(2, 7, ">")]),
    ("and", _doc("X = true and false"), [(2, 10, "and")]),
    ("or", _doc("X = nil or 1"), [(2, 9, "or")]),
    ("not", _doc("X = not true"), [(2, 5, "not")]),
    ("bare-identifier", _doc("X = foo"), [(2, 5, "foo")]),
    ("global-reference", _doc("X = {}", "Y = X"), [(3, 5, "X")]),
    ("dotted-name", _doc("X = math.huge"), [(2, 5, "math")]),
    ("bare-identifier-positional", _doc("X = {", "foo,", "}"), [(3, 1, "foo")]),
    ("bare-identifier-value", _doc("X = {", '["k"] = bar,', "}"), [(3, 9, "bar")]),
    ("bare-identifier-key", _doc("X = {", "[foo] = 1,", "}"), [(3, 2, "foo")]),
    ("nil-positional", _doc("X = {", "nil,", "}"), [(3, 1, "nil")]),
    ("nil-value-string-key", _doc("X = {", '["k"] = nil,', "}"), [(3, 9, "nil")]),
    ("nil-value-name-key", _doc("X = {", "k = nil,", "}"), [(3, 5, "nil")]),
    ("nil-key", _doc("X = {", "[nil] = 1,", "}"), [(3, 2, "nil")]),
    ("table-key", _doc("X = {", "[{}] = 1,", "}"), [(3, 2, "{")]),
    ("long-bracket-string", _doc("X = [[text]]"), [(2, 5, "[")]),
    ("long-bracket-level-2", _doc("X = [==[text]==]"), [(2, 5, "[")]),
    ("long-bracket-in-table", _doc("X = {", '["k"] = [[v]],', "}"), [(3, 9, "[")]),
    ("long-comment-after-value", _doc("X = 1 --[[ c ]]"), [(2, 7, "--")]),
    ("long-comment-level-2-own-line", _doc("--[==[ c ]==]", "X = 1"), [(2, 1, "--")]),
    ("long-comment-in-table", _doc("X = {", '"a", --[[ c', "]]", "}"), [(3, 6, "--")]),
    ("local", _doc("local X = 1"), [(2, 1, "local")]),
    ("return", _doc("return {}"), [(2, 1, "return")]),
    ("do-block", _doc("do end"), [(2, 1, "do")]),
    ("if", _doc("if true then end"), [(2, 1, "if")]),
    ("for", _doc("for i = 1, 2 do end"), [(2, 1, "for")]),
    ("while", _doc("while true do end"), [(2, 1, "while")]),
    ("field-assignment", _doc("X.y = 1"), [(2, 2, ".")]),
    ("index-assignment", _doc("X[1] = 2"), [(2, 2, "[")]),
    ("multiple-assignment", _doc("X, Y = 1, 2"), [(2, 2, ",")]),
    ("keyword-as-name-nil", _doc("nil = 1"), [(2, 1, "nil")]),
    ("keyword-as-name-true", _doc("true = 1"), [(2, 1, "true")]),
    ("keyword-as-key", _doc("X = {", "end = 1,", "}"), [(3, 1, "end")]),
    ("missing-name", _doc("= 1"), [(2, 1, "=")]),
    ("comma-after-top-level-value", _doc("X = 1,"), [(2, 6, ",")]),
    ("empty-entry", _doc("X = {1,,2}"), [(2, 8, ",")]),
    ("leading-separator", _doc("X = {,1}"), [(2, 6, ",")]),
    ("missing-separator", _doc("X = {1 2}"), [(2, 8, "2")]),
    ("unterminated-string-at-end", b'\r\nX = "abc', [(2, 5, '"'), (2, 9, "")]),
    ("unterminated-string-at-crlf", _doc('X = "abc', 'def"'), [(2, 5, '"'), (2, 9, "")]),
    ("raw-lf-in-string", b'\r\nX = "a\nb"\r\n', [(2, 5, '"'), (2, 7, "")]),
    ("raw-cr-in-string", b'\r\nX = "a\rb"\r\n', [(2, 5, '"'), (2, 7, "")]),
    ("unterminated-table", _doc("X = {", '["a"] = 1,'), [(2, 5, "{"), (4, 1, "")]),
    (
        "unterminated-outer-table",
        _doc("X = {", '["a"] = {', '["b"] = 1,', "},"),
        [(2, 5, "{"), (6, 1, "")],
    ),
    ("nul-between-tokens", b"\r\nX = 1\x00\r\n", [(2, 6, "\x00")]),
    ("nul-in-string", b'\r\nX = "a\x00b"\r\n', [(2, 7, "\x00"), (2, 5, '"')]),
    ("nul-after-last-line", b"\r\nX = 1\r\n\x00", [(3, 1, "\x00")]),
    ("lone-surrogate-escape", _doc(r'X = "\u{D800}"'), QUOTE_OR_BACKSLASH),
    ("hex-escape", _doc(r'X = "\x41"'), QUOTE_OR_BACKSLASH),
    ("z-escape", _doc(r'X = "\z"'), QUOTE_OR_BACKSLASH),
    ("decimal-escape-above-255", _doc(r'X = "\256"'), QUOTE_OR_BACKSLASH),
    ("unknown-letter-escape-q", _doc(r'X = "\q"'), QUOTE_OR_BACKSLASH),
    ("unknown-letter-escape-e", _doc(r'X = "\e"'), QUOTE_OR_BACKSLASH),
    ("unknown-escape-question-mark", _doc(r'X = "\?"'), QUOTE_OR_BACKSLASH),
    ("unknown-escape-pipe", _doc(r'X = "\|"'), QUOTE_OR_BACKSLASH),
    ("unknown-escape-space", _doc(r'X = "\ "'), QUOTE_OR_BACKSLASH),
    ("unknown-escape-high-byte", _doc(b'X = "\\\xc3\xa9"'), QUOTE_OR_BACKSLASH),
]


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize(
    ("data", "candidates"),
    [(d, c) for _i, d, c in REJECTIONS],
    ids=[i for i, _d, _c in REJECTIONS],
)
def test_rejected_with_line_column_and_token(
    luadata: Any, data: bytes, candidates: list[tuple[int, int, str]]
) -> None:
    """§4.3 and §6.4 "Rejected" (as amended): never evaluated, refused with
    a position."""
    _rejected(luadata, data, candidates)


# ── bounds ──────────────────────────────────────────────────────────────────


def _nested(depth: int, form: str) -> bytes:
    if form == "positional":
        return _doc("X = " + "{" * depth + "}" * depth)
    if form == "keyed":
        return _doc("X = " + '{["a"] = ' * depth + "1" + "}" * depth)
    if form == "client-lines":
        return _doc("X = {", *(["{"] * (depth - 1)), *(["},"] * (depth - 1)), "}")
    if form == "unterminated":
        return _doc("X = " + "{" * depth)
    raise AssertionError(form)


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("form", ["positional", "keyed", "client-lines"])
def test_depth_under_the_bound_parses(luadata: Any, form: str) -> None:
    """190 tables deep, inside §6.4's 200. Whether exactly 200 is the last
    accepted depth is not graded (where depth starts counting is not
    specified); 190 and 210 are clear of that question."""
    doc = _faithful(luadata, _nested(190, form))
    assert max(d for d, _t in tables(luadata, doc)) == 190


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("form", ["positional", "keyed", "client-lines", "unterminated"])
def test_depth_over_the_bound_raises(luadata: Any, form: str) -> None:
    """Unterminated input deeper than the bound raises the depth error, not
    an end-of-input error."""
    with pytest.raises(luadata.LuaLimitError) as info:
        luadata.parse(_nested(210, form))
    assert isinstance(info.value, luadata.LuaDataError)
    token = info.value.token or ""
    assert token.startswith(b"{" if isinstance(token, bytes) else "{")


def _chain(err: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    todo: list[BaseException | None] = [err]
    while todo:
        e = todo.pop()
        if e is None or any(e is s for s in seen):
            continue
        seen.append(e)
        todo += [e.__cause__, e.__context__]
    return seen


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("form", ["positional", "keyed", "client-lines", "unterminated"])
def test_ten_thousand_deep_raises_without_recursion_error(luadata: Any, form: str) -> None:
    """§6.4: iterative or depth-guarded, so the bound is hit before the
    interpreter's recursion limit, and no `RecursionError` is raised,
    escapes, or is converted (none in the exception chain)."""
    data = _nested(10_000, form)
    try:
        luadata.parse(data)
    except luadata.LuaLimitError as err:
        assert not [e for e in _chain(err) if isinstance(e, RecursionError)]
        token = err.token or ""
        assert token.startswith(b"{" if isinstance(token, bytes) else "{")
        if form != "client-lines":
            assert err.line == 2
    except RecursionError:  # pragma: no cover - the failure being graded
        pytest.fail("RecursionError escaped the parser")
    else:  # pragma: no cover - the failure being graded
        pytest.fail("a 10 000-deep table parsed")


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_string_over_the_bound_raises(luadata: Any) -> None:
    """Longer than §6.4's 64 MB whether MB means 10**6 or 2**20 bytes."""
    data = b'\r\nX = "' + b"a" * (64 * MiB + 1) + b'"\r\n'
    with pytest.raises(luadata.LuaLimitError):
        luadata.parse(data)


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_string_under_the_bound_is_kept_whole(luadata: Any) -> None:
    """Shorter than 64 MB under either reading: parsed, not truncated."""
    size = 63_000_000
    data = b'\r\nX = "' + b"a" * size + b'"\r\n'
    string = _only_value(luadata, data)
    assert len(string.data) == size
    assert len(string.raw) == size + 2


def _oversized() -> bytes:
    """A valid document padded with whitespace past 256 MB under either
    reading of MB, so only the size bound can refuse it."""
    return b"\r\nX = 1\r\n" + b" " * (256 * MiB)


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_file_over_the_bound_raises_from_bytes(luadata: Any) -> None:
    with pytest.raises(luadata.LuaLimitError):
        luadata.parse(_oversized())


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_file_over_the_bound_raises_from_read(luadata: Any, tmp_path: Path) -> None:
    target = tmp_path / "Huge.lua"
    target.write_bytes(_oversized())
    try:
        with pytest.raises(luadata.LuaLimitError):
            luadata.read(target)
    finally:
        target.unlink()


RECORD = (
    "{",
    '["itemCount"] = 1,',
    '["itemID"] = {id},',
    '["isBound"] = true,',
    '["quality"] = 1,',
    '["itemLink"] = "|cnIQ1:|Hitem:{id}::::::::7:1490:::::::::|h[Apprentice\'s Skinning Satchel]|h|r",',
    '["iconTexture"] = 133634,',
    "},",
)


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_multi_megabyte_document_parses_whole(luadata: Any) -> None:
    """The corpus has no multi-MB file. A ~4 MB document in the Forever
    client's layout parses completely and rebuilds byte for byte. The time
    limit only catches a super-linear parser (60 s for 4 MB is ~40 times
    slower than §6.4's 50 MB in 10 s); the target itself is measured in
    M10-04's PR on a constructed input (§6.4 amendment item 5), not graded
    here."""
    count = 20_000
    lines = ["BIG = {"]
    for i in range(count):
        lines += [line.replace("{id}", str(200_000 + i)) for line in RECORD]
    lines.append("}")
    data = _doc(*lines)
    assert len(data) > 4 * 10**6
    started = time.perf_counter()
    doc = luadata.parse(data)
    elapsed = time.perf_counter() - started
    records = doc.assignments[0].value.entries
    assert len(records) == count
    assert records[-1].value.entries[1].value.raw == str(200_000 + count - 1)
    assert rebuild(luadata, doc) == data
    assert elapsed < 60, f"{elapsed:.1f} s for {len(data)} bytes"


# ── L3: data, never code ────────────────────────────────────────────────────

ALLOWED_THIRD_PARTY = {"pydantic", "pydantic_core", "typing_extensions", "annotated_types"}
FORBIDDEN = {"ctypes", "subprocess", "multiprocessing", "socket", "urllib", "http", "importlib"}
FORBIDDEN |= {"code", "codeop", "runpy", "cffi", "lupa", "lunatic", "slpp", "luadata", "luaparser"}


def _imported_modules(node: ast.AST) -> list[str]:
    """Every module an import statement names, relative imports resolved
    against `wowlab_core` (so `from .guard import x` is `wowlab_core.guard`)."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level:
            base = "wowlab_core" + (f".{node.module}" if node.module else "")
        else:
            base = node.module or ""
        return [base] + [f"{base}.{alias.name}" for alias in node.names]
    return []


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_module_imports_no_interpreter_and_evaluates_nothing(luadata: Any) -> None:
    """L3 and M10-04's "no third-party parser": only the standard library,
    Pydantic and `wowlab_core` (never `guard`: a reader does not write);
    no `eval`, `exec`, `compile` or `__import__`."""
    tree = ast.parse(Path(luadata.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            top = module.split(".")[0]
            assert top in sys.stdlib_module_names | ALLOWED_THIRD_PARTY | {"wowlab_core"}, module
            assert top not in FORBIDDEN, module
            assert not (module == "wowlab_core.guard" or module.startswith("wowlab_core.guard.")), (
                module
            )
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            banned = {"eval", "exec", "__import__"}
            if isinstance(func, ast.Name):
                banned.add("compile")
            assert name not in banned, ast.unparse(node)
