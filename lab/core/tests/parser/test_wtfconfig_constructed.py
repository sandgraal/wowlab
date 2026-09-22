"""`wtfconfig` boundary and hostile cases on constructed input (L8).

Every input in this file is constructed, not captured, and every test id
says so. They cover what the real corpus does not contain: duplicate CVars,
multi-line macro bodies, the quoted CLICK bind form, unclassifiable lines,
mixed and missing line endings, and bytes that are not UTF-8. The real-file
grading is in `test_wtfconfig_fixtures.py`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from wowlab_core.wtfconfig import (
    BindingsDocument,
    BindLine,
    ConfigDocument,
    MacroBodyLine,
    MacroEndLine,
    MacroHeaderLine,
    MacrosDocument,
    SetLine,
    Unknown,
    parse_bindings,
    parse_config,
    parse_macros,
    split_lines,
)

pytestmark = pytest.mark.parser


def _round_trips(data: bytes) -> None:
    for parse in (parse_config, parse_bindings, parse_macros):
        doc = parse(data)
        assert b"".join(line.raw for line in doc.lines) == data, parse.__name__
        assert doc.to_bytes() == data, parse.__name__
        assert type(doc).model_validate(doc.model_dump()) == doc, parse.__name__


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\n",
        b"\r\n",
        b"\n\n\r\n",
        b"no ending",
        b"a\rb\n",  # lone CR is text
        b"a\r",  # CR at end of file with no LF
        b"a\r\r\n",  # CR before a CRLF
        b"\r",
        b"x\x00",  # trailing NUL, as in the edit-mode caches
        b'\xef\xbb\xbfSET a "1"\n',  # BOM
        b'SET a "1"\nSET b "2"\r\nbind A B\r\nVER 3 01 "n" "i"\n/x\r\nEND',
        bytes(range(256)) * 3,
    ],
    ids=lambda d: f"constructed-{d[:24]!r}",
)
def test_constructed_any_bytes_round_trip(data: bytes) -> None:
    _round_trips(data)


def test_constructed_each_line_keeps_its_own_ending() -> None:
    data = b'SET a "1"\r\nSET b "2"\nSET c "3"'
    assert list(split_lines(data)) == [
        (b'SET a "1"', b"\r\n"),
        (b'SET b "2"', b"\n"),
        (b'SET c "3"', b""),
    ]
    doc = parse_config(data)
    assert [line.ending for line in doc.lines] == [b"\r\n", b"\n", b""]
    assert [c.value for c in doc.cvars] == ["1", "2", "3"]


def test_constructed_lone_cr_is_not_a_break() -> None:
    assert list(split_lines(b"a\rb\n")) == [(b"a\rb", b"\n")]
    doc = parse_config(b'SET a "x\ry"\n')
    assert isinstance(doc.lines[0], Unknown), "a CR in a value is not the SET grammar"
    doc = parse_config(b'SET a "1"\r')  # CR, then end of file
    assert isinstance(doc.lines[0], Unknown)
    assert (doc.lines[0].text, doc.lines[0].ending) == (b'SET a "1"\r', b"")


# ---------------------------------------------------------------- Config.wtf


def test_constructed_duplicate_cvars_report_the_last_as_effective() -> None:
    data = b'SET gxWindow "1"\nSET other "x"\nSET GXWINDOW "0"\nSET gxwindow "2"\n'
    doc = parse_config(data)
    assert doc.to_bytes() == data
    assert [c.name for c in doc.cvars] == ["gxWindow", "other", "GXWINDOW", "gxwindow"]
    (dup,) = doc.duplicates()
    assert dup.folded_name == "gxwindow"
    assert [(c.name, c.value, c.index) for c in dup.entries] == [
        ("gxWindow", "1", 0),
        ("GXWINDOW", "0", 2),
        ("gxwindow", "2", 3),
    ]
    assert (dup.effective.name, dup.effective.value) == ("gxwindow", "2")
    assert doc["GxWindow"] == "2"
    effective = doc.effective()
    assert list(effective) == ["gxwindow", "other"], "first-appearance order"
    assert effective["gxwindow"].index == 3


def test_constructed_lookup_folds_ascii_case_only() -> None:
    doc = parse_config('SET Ünit "1"\n'.encode())
    assert doc.get("Ünit") is not None
    assert doc.get("ünit") is None, "non-ASCII letters are not folded"
    assert doc.get("ÜNIT") is not None


@pytest.mark.parametrize(
    "line",
    [
        b'SET a "has " quote"',  # embedded quote: never observed
        b'SET a "unterminated',
        b"SET a unquoted",
        b'SET  a "two spaces"',
        b'SET a  "two spaces"',
        b'SET a "1" ',  # trailing space
        b' SET a "1"',  # leading space
        b'set a "1"',  # keyword case
        b"SET",
        b'SET "1"',
        b"-- comment",
        b"",
    ],
    ids=lambda d: f"constructed-{d!r}",
)
def test_constructed_unmatched_config_lines_are_unknown(line: bytes) -> None:
    data = b'SET before "1"\n' + line + b'\nSET after "2"\n'
    doc = parse_config(data)
    assert doc.to_bytes() == data
    assert isinstance(doc.lines[1], Unknown)
    assert doc.lines[1].text == line
    assert [c.name for c in doc.cvars] == ["before", "after"]
    assert [c.index for c in doc.cvars] == [0, 2], "unknown lines keep their place"


@pytest.mark.parametrize(
    ("line", "name", "value"),
    [
        (b'SET CACHE-WQST-X "7760"', "CACHE-WQST-X", "7760"),
        (b'SET a ""', "a", ""),
        (b'SET a "\x02\x01\x7f"', "a", "\x02\x01\x7f"),
        (b'SET a "with spaces  inside "', "a", "with spaces  inside "),
        (b'SET a\x02b "1"', "a\x02b", "1"),
        (b'SET a "\xff\xfe"', "a", "\udcff\udcfe"),
        (b'SET "q" "1"', '"q"', "1"),  # a name is any non-space run
    ],
    ids=lambda d: f"constructed-{d!r}" if isinstance(d, bytes) else "",
)
def test_constructed_set_grammar(line: bytes, name: str, value: str) -> None:
    doc = parse_config(line + b"\n")
    assert isinstance(doc.lines[0], SetLine)
    assert (doc.lines[0].name, doc.lines[0].value) == (name, value)
    assert value.encode("utf-8", "surrogateescape") in line


# ------------------------------------------------------------------ bindings


def test_constructed_click_binding_is_quoted() -> None:
    data = b'bind BUTTON4 "CLICK SomeAddonButton:LeftButton"\r\nbind CTRL-1 ACTIONBUTTON1\r\n'
    doc = parse_bindings(data)
    assert doc.to_bytes() == data
    first, second = doc.bindings
    assert (first.key, first.action, first.quoted) == (
        "BUTTON4",
        "CLICK SomeAddonButton:LeftButton",
        True,
    )
    line = doc.lines[0]
    assert isinstance(line, BindLine)
    assert line.action_text == '"CLICK SomeAddonButton:LeftButton"'
    assert (second.key, second.action, second.quoted) == ("CTRL-1", "ACTIONBUTTON1", False)


def test_constructed_unmatched_binding_lines_are_unknown() -> None:
    data = (
        b"BINDINGMODE 1\r\n\r\nbind A\r\nbind  A B\r\nbind A  B\r\nBIND A B\r\n"
        b'bind A "x" y\r\nbind A B\r\n'
    )
    doc = parse_bindings(data)
    assert doc.to_bytes() == data
    kinds = [line.kind for line in doc.lines]
    assert kinds == ["unknown"] * 6 + ["bind", "bind"]
    assert doc.bindings[0].action == '"x" y'
    assert doc.bindings[0].quoted is False
    assert [b.index for b in doc.bindings] == [6, 7]


# -------------------------------------------------------------------- macros


def test_constructed_multi_line_macro_body_is_intact() -> None:
    data = (
        b'VER 3 0000000000000001 "Macro name" "INV_MISC_QUESTIONMARK"\r\n'
        b"#showtooltip\r\n"
        b"/cast [mod:shift] Spell Two; Spell One\n"  # a different ending mid-body
        b"\r\n"  # an empty body line
        b"/use 13\r\n"
        b"END\r\n"
    )
    doc = parse_macros(data)
    assert doc.to_bytes() == data
    (macro,) = doc.macros
    assert (macro.macro_id, macro.name, macro.icon) == (
        "0000000000000001",
        "Macro name",
        "INV_MISC_QUESTIONMARK",
    )
    assert macro.body == ("#showtooltip\r\n/cast [mod:shift] Spell Two; Spell One\n\r\n/use 13\r\n")
    assert macro.body_lines == (
        "#showtooltip",
        "/cast [mod:shift] Spell Two; Spell One",
        "",
        "/use 13",
    )
    assert macro.complete


def test_constructed_body_runs_to_exactly_end() -> None:
    data = (
        b'VER 3 01 "a" "1"\n'
        b"END \n"  # trailing space: body
        b"end\n"  # case: body
        b'VER 3 02 "inner" "2"\n'  # a header shape inside a body is body
        b"END\n"
        b"between records\n"
        b'VER 3 03 "b" "3"\n'
        b"END"
    )
    doc = parse_macros(data)
    assert doc.to_bytes() == data
    assert [line.kind for line in doc.lines] == [
        "macro-header",
        "macro-body",
        "macro-body",
        "macro-body",
        "macro-end",
        "unknown",
        "macro-header",
        "macro-end",
    ]
    first, second = doc.macros
    assert first.body_lines == ("END ", "end", 'VER 3 02 "inner" "2"')
    assert (second.name, second.body, second.header_index) == ("b", "", 6)


def test_constructed_record_without_end_is_incomplete() -> None:
    data = b'VER 3 01 "a" "1"\r\n/cast X\r\n'
    doc = parse_macros(data)
    assert doc.to_bytes() == data
    (macro,) = doc.macros
    assert macro.body == "/cast X\r\n"
    assert not macro.complete


@pytest.mark.parametrize(
    "line",
    [
        b'VER 3 01 "a"',
        b'VER 3 0x01 "a" "1"',
        b'VER three 01 "a" "1"',
        b'VER 3 01 "a" "1" ',
        b'VER 3 01 "a" "1 2"',  # a space in the icon
        b'VER 3 01 "a" "1"2"',  # a quote in the icon
        b"END",  # outside a record
        b"/cast Orphan",
    ],
    ids=lambda d: f"constructed-{d!r}",
)
def test_constructed_lines_outside_records_are_unknown(line: bytes) -> None:
    doc = parse_macros(line + b"\r\n")
    assert isinstance(doc.lines[0], Unknown)
    assert doc.macros == ()


def test_constructed_macro_name_may_contain_a_quote() -> None:
    data = b'VER 3 01 "say "hi" now" "134400"\r\n/say hi\r\nEND\r\n'
    doc = parse_macros(data)
    assert doc.to_bytes() == data
    (macro,) = doc.macros
    assert (macro.name, macro.icon) == ('say "hi" now', "134400")
    (macro,) = parse_macros(b'VER 3 01 "a" "b" "c"\n').macros
    assert (macro.name, macro.icon) == ('a" "b', "c"), "anchored on the icon"


def test_constructed_unknown_lines_are_listed_with_their_index() -> None:
    config = parse_config(b'SET a "1"\nset b "2"\nSET  c "3"\nSET d "4"\n')
    assert config.unknown_lines() == ((1, b'set b "2"'), (2, b'SET  c "3"'))
    assert config.get("b") is None, "a hand-edited line is not seen by the views"
    bindings = parse_bindings(b"BINDINGMODE 0\r\nbind A B\r\n")
    assert bindings.unknown_lines() == ((0, b"BINDINGMODE 0"),)
    macros = parse_macros(b'x\nVER 3 01 "a" "1"\nbody\nEND\n\n')
    assert macros.unknown_lines() == ((0, b"x"), (4, b""))
    assert parse_config(b'SET a "1"\n').unknown_lines() == ()


def test_constructed_macro_name_and_icon_keep_their_bytes() -> None:
    data = 'VER 3 00000000000000AF "Größe mit Leer" "134400"\n/say hi\nEND\n'.encode()
    (macro,) = parse_macros(data).macros
    assert (macro.macro_id, macro.name, macro.icon) == (
        "00000000000000AF",
        "Größe mit Leer",
        "134400",
    )


# ----------------------------------------------------- model self-consistency


def test_constructed_typed_lines_validate_their_text() -> None:
    SetLine(text=b'SET a "1"', ending=b"\n")
    with pytest.raises(ValidationError):
        SetLine(text=b"not a set line", ending=b"\n")
    with pytest.raises(ValidationError):
        BindLine(text=b"bind A", ending=b"\n")
    with pytest.raises(ValidationError):
        MacroHeaderLine(text=b"VER", ending=b"\n")
    with pytest.raises(ValidationError):
        MacroBodyLine(text=b"END", ending=b"\n")
    with pytest.raises(ValidationError):
        MacroEndLine(text=b"END ", ending=b"\n")
    with pytest.raises(ValidationError):
        Unknown(text=b"a\nb", ending=b"\n")
    with pytest.raises(ValidationError):
        Unknown(text=b"a\r", ending=b"\n")
    with pytest.raises(ValidationError):
        Unknown(text=b"a", ending=b"\r")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Unknown(text="str, not bytes", ending=b"\n")  # type: ignore[arg-type]


def test_constructed_documents_reject_a_line_sequence_that_would_not_reparse() -> None:
    with pytest.raises(ValidationError):
        ConfigDocument(lines=(Unknown(text=b"a", ending=b""), Unknown(text=b"b", ending=b"\n")))
    with pytest.raises(ValidationError):
        ConfigDocument(lines=(Unknown(text=b"", ending=b""),))
    with pytest.raises(ValidationError):
        MacrosDocument(lines=(MacroBodyLine(text=b"/x", ending=b"\n"),))
    with pytest.raises(ValidationError):
        MacrosDocument(
            lines=(
                MacroHeaderLine(text=b'VER 3 01 "a" "1"', ending=b"\n"),
                Unknown(text=b"x", ending=b"\n"),
            )
        )
    with pytest.raises(ValidationError):  # a VER line inside a record is body
        MacrosDocument(
            lines=(
                MacroHeaderLine(text=b'VER 3 01 "a" "1"', ending=b"\n"),
                MacroHeaderLine(text=b'VER 3 02 "b" "1"', ending=b"\n"),
            )
        )
    with pytest.raises(ValidationError):  # END outside a record is Unknown
        MacrosDocument(lines=(MacroEndLine(text=b"END", ending=b"\n"),))
    with pytest.raises(ValidationError):  # SET text in an Unknown line
        ConfigDocument(lines=(Unknown(text=b'SET a "1"', ending=b"\n"),))
    # Unknown lines the parser would not type are fine, including in bindings
    # and macros documents, whose grammars differ.
    ConfigDocument(lines=(Unknown(text=b"bind A B", ending=b"\n"),))
    BindingsDocument(lines=(Unknown(text=b'SET a "1"', ending=b"\n"),))
    MacrosDocument(lines=(Unknown(text=b"END", ending=b"\n"),))


def test_constructed_documents_are_frozen() -> None:
    doc = parse_config(b'SET a "1"\n')
    with pytest.raises(ValidationError):
        doc.lines = ()  # type: ignore[misc]


def test_constructed_unobserved_binding_line_kinds() -> None:
    # LAB_FORMATS §6 note 2026-09-22: all [verify], none observed.
    data = b"bind A NONE\r\nbind B \r\nbind C\r\nBINDINGMODE 1\r\nmodifiedclick SELFCAST ALT\r\n"
    doc = parse_bindings(data)
    assert doc.to_bytes() == data
    assert [line.kind for line in doc.lines] == ["bind"] + ["unknown"] * 4
    assert [(b.key, b.action) for b in doc.bindings] == [("A", "NONE")]
