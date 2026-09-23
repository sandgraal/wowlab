"""`toc` boundary and hostile inputs (constructed, L8).

Every input here is constructed, because no committed TOC carries it: a BOM,
LF or mixed endings, a lone CR, no final line break, invalid UTF-8, a
leading load condition and path variables (LAB_FORMATS §3, **[verify]**), an
unknown directive, an unparseable `## Interface:`, and comment shapes. The
real TOC is graded in `test_toc_fixtures.py`. Test ids carry `constructed`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core.toc import (
    Blank,
    Comment,
    Directive,
    FileLine,
    TocDocument,
    TocTooLargeError,
    parse_interface,
    parse_toc,
    read_toc,
)

CASES = {
    "empty": b"",
    "bom-lf": b"\xef\xbb\xbf## Title: X\nX.lua\n",
    "bom-only": b"\xef\xbb\xbf",
    "no-final-break": b"## Title: X\r\nX.lua",
    "mixed-endings": b"## Title: X\nX.lua\r\n\r\nY.lua\n",
    "lone-cr": b"## Title: X\rstill line one\nX.lua\n",
    "invalid-utf8": b"## Title: \xff\xfe\n\xc3(.lua\n",
    "only-blanks": b"\n\n  \t\n",
    "nul-byte": b"## Title: a\x00b\nX.lua\x00\n",
}


@pytest.mark.parser
@pytest.mark.parametrize("data", CASES.values(), ids=[f"constructed-{k}" for k in CASES])
def test_round_trip_and_json_constructed(data: bytes) -> None:
    doc = parse_toc(data)
    assert doc.to_bytes() == data
    assert TocDocument.model_validate_json(doc.model_dump_json()) == doc


@pytest.mark.parser
def test_bom_is_kept_outside_the_first_line_constructed() -> None:
    doc = parse_toc(CASES["bom-lf"])
    assert doc.bom is True
    first = doc.lines[0]
    assert isinstance(first, Directive)
    assert (first.key, first.value) == ("Title", "X")


@pytest.mark.parser
def test_endings_are_per_line_constructed() -> None:
    doc = parse_toc(CASES["mixed-endings"])
    assert [line.ending for line in doc.lines] == ["\n", "\r\n", "\r\n", "\n"]
    assert parse_toc(CASES["no-final-break"]).lines[-1].ending == ""
    lone = parse_toc(CASES["lone-cr"])
    assert len(lone.lines) == 2, "a lone CR is not a line break"


@pytest.mark.parser
def test_invalid_utf8_is_flagged_and_replaced_in_text_constructed() -> None:
    doc = parse_toc(CASES["invalid-utf8"])
    assert doc.decode_errors is True
    title = doc.lines[0]
    assert isinstance(title, Directive)
    assert "�" in title.value
    assert title.raw == b"## Title: \xff\xfe"


@pytest.mark.parser
@pytest.mark.parametrize(
    ("line", "key", "value"),
    [
        (b"## Title: Foo", "Title", "Foo"),
        (b"##Title:Foo", "Title", "Foo"),
        (b"## Title : Foo  ", "Title", "Foo"),
        (b"##\tNotes:\tA: B", "Notes", "A: B"),
        (b"## Title-deDE: Bar", "Title-deDE", "Bar"),
        (b"## X-Custom-Thing: 1", "X-Custom-Thing", "1"),
        (b"## Frobnicate: yes", "Frobnicate", "yes"),
        (b"## Empty:", "Empty", ""),
    ],
    ids=lambda v: f"constructed-{v!r}" if isinstance(v, bytes) else None,
)
def test_directive_shapes_constructed(line: bytes, key: str, value: str) -> None:
    doc = parse_toc(line + b"\r\n")
    (d,) = doc.lines
    assert isinstance(d, Directive)
    assert (d.key, d.value) == (key, value)
    assert doc.to_bytes() == line + b"\r\n"


@pytest.mark.parser
def test_unknown_directive_is_kept_in_order_constructed() -> None:
    data = b"## Interface: 1\n## Frobnicate: yes\n## Title: T\n## X-Mine: 2\nA.lua\n"
    doc = parse_toc(data)
    assert [d.key for d in doc.directives] == ["Interface", "Frobnicate", "Title", "X-Mine"]
    assert [d.known for d in doc.directives] == [True, False, True, True]
    assert doc.get("frobnicate") == "yes"
    assert doc.to_bytes() == data


@pytest.mark.parser
@pytest.mark.parametrize(
    "line",
    [b"# a comment", b"##", b"## ", b"## no colon here", b"## Some words: here", b"#: x"],
    ids=lambda v: f"constructed-{v!r}",
)
def test_comment_shapes_constructed(line: bytes) -> None:
    (c,) = parse_toc(line + b"\n").lines
    assert isinstance(c, Comment)


@pytest.mark.parser
def test_directives_after_blank_and_file_lines_constructed() -> None:
    """Parsing does not stop at the first blank (Baganator, LAB_FORMATS §3
    2026-09-22 amendment, [verify] against a committed fixture)."""
    doc = parse_toc(b"## Title: T\n\n## Notes: N\nA.lua\n## Author: Me\n")
    assert [d.key for d in doc.directives] == ["Title", "Notes", "Author"]
    assert [type(line) for line in doc.lines] == [Directive, Blank, Directive, FileLine, Directive]


@pytest.mark.parser
def test_duplicate_directive_get_returns_first_constructed() -> None:
    doc = parse_toc(b"## Title: One\n## title: Two\n")
    assert doc.get("Title") == "One"
    assert [d.value for d in doc.get_all("TITLE")] == ["One", "Two"]


@pytest.mark.parser
@pytest.mark.parametrize(
    ("line", "path", "conditions", "variables"),
    [
        (b"Foo.lua", "Foo.lua", [], []),
        (b"  Foo.lua  ", "Foo.lua", [], []),
        (b"Sub/Foo.xml", "Sub/Foo.xml", [], []),
        (
            b"[AllowLoadGameType mainline] Foo.lua",
            "Foo.lua",
            [("AllowLoadGameType mainline", "before")],
            [],
        ),
        (
            b"Foo.lua [AllowLoadGameType standard]",
            "Foo.lua",
            [("AllowLoadGameType standard", "after")],
            [],
        ),
        (
            b"[A x] Foo.lua [B y] [C z]",
            "Foo.lua",
            [("A x", "before"), ("B y", "after"), ("C z", "after")],
            [],
        ),
        (b"Locales\\[TextLocale].lua", "Locales\\[TextLocale].lua", [], ["TextLocale"]),
        (b"[Family]\\Bar.xml", "[Family]\\Bar.xml", [], ["Family"]),
        (
            b"[Family]\\Bar.xml [AllowLoad Game]",
            "[Family]\\Bar.xml",
            [("AllowLoad Game", "after")],
            ["Family"],
        ),
        (b"Unclosed [bracket.lua", "Unclosed [bracket.lua", [], []),
    ],
    ids=lambda v: f"constructed-{v!r}" if isinstance(v, bytes) else None,
)
def test_file_line_conditions_and_variables_constructed(
    line: bytes, path: str, conditions: list[tuple[str, str]], variables: list[str]
) -> None:
    (f,) = parse_toc(line + b"\r\n").lines
    assert isinstance(f, FileLine)
    assert f.path == path
    assert [(c.text, c.position) for c in f.conditions] == conditions
    assert list(f.variables) == variables
    assert f.raw == line


@pytest.mark.parser
@pytest.mark.parametrize(
    ("value", "versions", "unparsed"),
    [
        ("120105", (120105,), ()),
        (
            "120100, 16001, 50504, 38001, 20506, 11509",
            (120100, 16001, 50504, 38001, 20506, 11509),
            (),
        ),
        ("110000,  ,11507", (110000, 11507), ()),
        ("abc, 110000", (110000,), ("abc",)),
        ("11.0.0", (), ("11.0.0",)),
        ("\uff11\uff12\uff13", (), ("\uff11\uff12\uff13",)),  # fullwidth digits
        ("", (), ()),
    ],
    ids=lambda v: f"constructed-{v!r}" if isinstance(v, str) else None,
)
def test_interface_values_constructed(
    value: str, versions: tuple[int, ...], unparsed: tuple[str, ...]
) -> None:
    got = parse_interface(value)
    assert (got.versions, got.unparsed) == (versions, unparsed)
    assert got.ok is (bool(versions) and not unparsed)


@pytest.mark.parser
def test_unparseable_interface_is_reported_not_raised_constructed() -> None:
    doc = parse_toc(b"## Interface: soon\n")
    assert doc.interface is not None
    assert doc.interface.unparsed == ("soon",)
    assert parse_toc(b"## Title: T\n").interface is None


@pytest.mark.parser
def test_read_toc_refuses_an_oversized_file_constructed(tmp_path: Path) -> None:
    path = tmp_path / "Big.toc"
    path.write_bytes(b"## Title: T\n" + b"A.lua\n" * 100)
    with pytest.raises(TocTooLargeError):
        read_toc(path, max_bytes=64)
    assert read_toc(path).to_bytes() == path.read_bytes()


@pytest.mark.parser
@pytest.mark.parametrize(
    "line",
    [
        b"[c] " * 8_000 + b"Foo.lua",
        b"[a b]" + b" " * 40_000 + b"Foo.lua",
        b"Foo.lua" + b" " * 40_000 + b"[a]",
        b"##" + b" " * 40_000 + b"Key" + b"x" * 40_000,
        b"[" * 20_000 + b"]" * 20_000,
    ],
    ids=[
        "constructed-many-leading",
        "constructed-long-gap-leading",
        "constructed-long-gap-trailing",
        "constructed-no-colon-directive",
        "constructed-brackets",
    ],
)
def test_hostile_lines_parse_in_linear_time_constructed(line: bytes) -> None:
    import time

    start = time.perf_counter()
    doc = parse_toc(line + b"\n")
    assert time.perf_counter() - start < 1.0
    assert doc.to_bytes() == line + b"\n"
