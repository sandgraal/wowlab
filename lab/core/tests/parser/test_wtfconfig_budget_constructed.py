"""`wtfconfig` byte and line budgets and the leading BOM (M16-25).

CONSTRUCTED inputs only (L8: hostile and boundary cases), and every test id
says so. No real capture has a BOM or comes near a budget: the Forever
`Config.wtf` is 3.7 KiB in 130 lines, and whether the client ever writes a
BOM into a WTF file is not observed **[verify]**. The real-file grading is
in `test_wtfconfig_fixtures.py`; the serial RSS probe of a document at the
budget is `test_wtfconfig_budget_rss_constructed.py`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from wowlab_core import wtfconfig
from wowlab_core.wtfconfig import (
    BINDINGS_BUDGET,
    BOM,
    CONFIG_BUDGET,
    MACROS_BUDGET,
    BindLine,
    ConfigDocument,
    MacroHeaderLine,
    SetLine,
    Unknown,
    WtfBudget,
    WtfLimitError,
)

pytestmark = pytest.mark.parser

Parse = Callable[[bytes], Any]
Read = Callable[[Path], Any]

# (id, parser, reader, budget, a typed line of the type)
TYPES: list[tuple[str, Parse, Read, WtfBudget, bytes]] = [
    ("config", wtfconfig.parse_config, wtfconfig.read_config, CONFIG_BUDGET, b'SET a "1"'),
    ("bindings", wtfconfig.parse_bindings, wtfconfig.read_bindings, BINDINGS_BUDGET, b"bind A B"),
    (
        "macros",
        wtfconfig.parse_macros,
        wtfconfig.read_macros,
        MACROS_BUDGET,
        b'VER 3 01 "n" "1"',
    ),
]


def _cases(*fields: int) -> list[Any]:
    return [pytest.param(*(t[i] for i in fields), id=f"constructed-{t[0]}") for t in TYPES]


# ── the BOM ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("parse", "typed"), _cases(1, 4))
def test_constructed_bom_is_a_prefix_token_not_line_one(parse: Parse, typed: bytes) -> None:
    data = BOM + typed + b"\r\nother\n"
    doc = parse(data)
    assert doc.bom == BOM
    assert doc.lines[0].text == typed, "the BOM is not part of line 1"
    assert isinstance(doc.lines[0], SetLine | BindLine | MacroHeaderLine)
    assert doc.to_bytes() == data, "L4: byte for byte"
    assert doc.bom + b"".join(line.raw for line in doc.lines) == data
    assert type(doc).model_validate(doc.model_dump()) == doc


@pytest.mark.parametrize("parse", _cases(1))
def test_constructed_no_bom_is_an_empty_prefix(parse: Parse) -> None:
    doc = parse(b"x\n")
    assert doc.bom == b""
    assert doc.to_bytes() == b"x\n"


@pytest.mark.parametrize("parse", _cases(1))
@pytest.mark.parametrize(
    "data",
    [BOM, BOM + b"\n", BOM + BOM + b'SET a "1"\n', BOM[:2] + b"x", b"x" + BOM, BOM + b"\r\n" + BOM],
    ids=lambda d: f"constructed-{d!r}",
)
def test_constructed_bom_edges_round_trip(parse: Parse, data: bytes) -> None:
    doc = parse(data)
    assert doc.to_bytes() == data
    assert doc.bom == (BOM if data.startswith(BOM) else b"")
    rest = data[len(doc.bom) :]
    assert b"".join(line.raw for line in doc.lines) == rest


def test_constructed_only_the_first_bom_is_the_prefix() -> None:
    doc = wtfconfig.parse_config(BOM + BOM + b'SET a "1"\n')
    assert doc.bom == BOM
    assert isinstance(doc.lines[0], Unknown), "a second BOM is line 1's own text"
    assert doc.lines[0].text == BOM + b'SET a "1"'


def test_constructed_bom_cvar_is_found_on_line_one() -> None:
    doc = wtfconfig.parse_config(BOM + b'SET gxApi "metal"\nSET b "2"\n')
    found = doc.get("GXAPI")
    assert found is not None
    assert (found.name, found.value, found.index) == ("gxApi", "metal", 0)
    assert doc.unknown_lines() == ()


def test_constructed_document_without_bom_cannot_start_with_one() -> None:
    with pytest.raises(ValidationError, match="BOM"):
        ConfigDocument(lines=(Unknown(text=BOM + b"x", ending=b"\n"),))
    ConfigDocument(lines=(Unknown(text=BOM + b"x", ending=b"\n"),), bom=BOM)
    with pytest.raises(ValidationError):
        ConfigDocument(lines=(), bom=b"\xef\xbb")  # type: ignore[arg-type]


def test_constructed_counts_skip_the_bom_as_the_parsers_do() -> None:
    config = BOM + b'SET a "1"\nSET b "2"\n'
    assert wtfconfig.count_cvar_names(config) == len(wtfconfig.parse_config(config).effective())
    assert wtfconfig.count_cvar_names(config) == 2
    macros = BOM + b'VER 3 01 "n" "1"\n/x\nEND\n'
    assert wtfconfig.count_macro_records(macros) == len(wtfconfig.parse_macros(macros).macros)
    assert wtfconfig.count_macro_records(macros) == 1


# ── byte budget ─────────────────────────────────────────────────────────────


def _filled(size: int, line: bytes = b"x" * 255 + b"\n") -> bytes:
    """`size` bytes of 256-byte lines (fewer lines than any line budget)."""
    whole, rest = divmod(size, len(line))
    return line * whole + b"x" * rest


@pytest.mark.parametrize(("parse", "budget"), _cases(1, 3))
def test_constructed_exactly_the_byte_budget_parses(parse: Parse, budget: WtfBudget) -> None:
    data = _filled(budget.max_bytes)
    assert len(data) == budget.max_bytes
    assert parse(data).to_bytes() == data


@pytest.mark.parametrize(("parse", "budget"), _cases(1, 3))
def test_constructed_one_byte_over_is_refused_with_a_position(
    parse: Parse, budget: WtfBudget
) -> None:
    data = _filled(budget.max_bytes + 1)
    with pytest.raises(WtfLimitError) as caught:
        parse(data)
    err = caught.value
    assert err.kind == "bytes"
    assert (err.limit, err.size, err.offset) == (
        budget.max_bytes,
        budget.max_bytes + 1,
        len(data) - 1,
    )
    # The first byte past the budget, counted independently.
    assert err.line == data.count(b"\n", 0, err.offset) + 1
    assert err.column == err.offset - (data.rfind(b"\n", 0, err.offset) + 1) + 1
    assert str(err).startswith(f"line {err.line}, column {err.column}: ")
    assert isinstance(err, ValueError)


def test_constructed_byte_refusal_on_line_one_counts_columns_after_the_bom() -> None:
    data = BOM + b"x" * CONFIG_BUDGET.max_bytes  # one long line, BOM first
    with pytest.raises(WtfLimitError) as caught:
        wtfconfig.parse_config(data)
    err = caught.value
    assert (err.offset, err.line) == (CONFIG_BUDGET.max_bytes, 1)
    assert err.column == CONFIG_BUDGET.max_bytes - len(BOM) + 1


# ── line budget ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("parse", "budget"), _cases(1, 3))
@pytest.mark.parametrize("last", [b"\n", b""], ids=["constructed-ending", "constructed-no-ending"])
def test_constructed_exactly_the_line_budget_parses(
    parse: Parse, budget: WtfBudget, last: bytes
) -> None:
    data = BOM + b"\n" * (budget.max_lines - 1) + b"x" + last
    doc = parse(data)
    assert len(doc.lines) == budget.max_lines, "the BOM is not a line"
    assert doc.to_bytes() == data


@pytest.mark.parametrize(("parse", "budget"), _cases(1, 3))
@pytest.mark.parametrize("last", [b"\n", b"x"], ids=["constructed-empty", "constructed-unended"])
def test_constructed_one_line_over_is_refused_at_that_line(
    parse: Parse, budget: WtfBudget, last: bytes
) -> None:
    data = BOM + b"ab\n" * budget.max_lines + last
    with pytest.raises(WtfLimitError) as caught:
        parse(data)
    err = caught.value
    assert err.kind == "lines"
    assert (err.limit, err.line, err.column) == (budget.max_lines, budget.max_lines + 1, 1)
    assert err.offset == len(BOM) + 3 * budget.max_lines, "the first byte of the line past it"
    assert err.size == len(data)


def test_constructed_budgets_are_far_above_the_real_files() -> None:
    """Per file type; the real files are a few KiB (fixtures/README.md)."""
    for budget in (CONFIG_BUDGET, BINDINGS_BUDGET, MACROS_BUDGET):
        assert budget.max_bytes >= 1024 * 1024
        assert budget.max_lines >= 10_000
    # A macros file at its most: 150 records (120 account and 30 character
    # slots are believed [verify]) of up to 258 lines each (`VER`, a
    # 255-character body of line breaks alone, `END`).
    assert MACROS_BUDGET.max_lines >= 150 * 258


# ── the readers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("read", "budget"), _cases(2, 3))
def test_constructed_reader_of_a_giant_file_reads_at_most_the_budget_plus_one(
    read: Read, budget: WtfBudget, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "giant.wtf"
    size = budget.max_bytes * 3
    path.write_bytes(_filled(size))
    seen: list[int] = []
    real = os.read

    def counted(fd: int, n: int) -> bytes:
        chunk = real(fd, n)
        seen.append(len(chunk))
        return chunk

    monkeypatch.setattr(os, "read", counted)
    with pytest.raises(WtfLimitError) as caught:
        read(path)
    assert sum(seen) == budget.max_bytes + 1
    err = caught.value
    assert (err.kind, err.offset, err.size) == ("bytes", budget.max_bytes, size)
    assert f"{size} bytes" in err.message


@pytest.mark.parametrize(("read", "typed"), _cases(2, 4))
def test_constructed_reader_keeps_the_bom(read: Read, typed: bytes, tmp_path: Path) -> None:
    path = tmp_path / "bom.wtf"
    data = BOM + typed + b"\n"
    path.write_bytes(data)
    doc = read(path)
    assert (doc.bom, doc.lines[0].text, doc.to_bytes()) == (BOM, typed, data)
