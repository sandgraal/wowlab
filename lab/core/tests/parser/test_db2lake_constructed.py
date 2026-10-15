"""db2lake's type inference and CSV checks on constructed inputs (M12-03).

Constructed on purpose (L8: boundary and hostile inputs, labelled
`constructed` in every test id): the recordings (graded in
`test_db2lake_fixtures.py`) hold no `-0`, no 64-bit edge, no exponent and no
hostile header. The rule is ADR-0028's, with the details the §14.2 amendment
of 2026-09-30 settles. Everything lives under `tmp_path`; no request is made.
"""

from __future__ import annotations

import hashlib
import sqlite3
import tracemalloc
from pathlib import Path
from typing import BinaryIO

import pytest
from _cpu_clock import cpu_clock

from wowlab_core.db2lake import MAX_RECORD_BYTES, Cell, Lake
from wowlab_core.gamedata import GameData, MalformedTable

pytestmark = pytest.mark.parser

BUILD = "9.9.9.99999"  # constructed; no real build is named here
TABLE = "Constructed"


class _Tables:
    """A `Source` serving constructed CSV bytes by table name."""

    name = "constructed"

    def __init__(self, tables: dict[str, bytes]) -> None:
        self.tables = tables
        self.requests: list[str] = []

    def fetch_builds(self, dest: BinaryIO) -> str:
        raise AssertionError("not needed")

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        self.requests.append(table)
        dest.write(self.tables[table])
        return f"constructed:{table}"


def _lake(tmp_path: Path, body: bytes, **kwargs: int) -> Lake:
    data = GameData(_Tables({TABLE: body}), cache_dir=tmp_path / "cache")
    return Lake(BUILD, gamedata=data, lake_dir=tmp_path / "lake", **kwargs)


def _column(tmp_path: Path, *cells: str) -> tuple[str, list[Cell]]:
    """Load a one-column table (plus an ID) and return the column's type and values."""
    lines = ["ID,Value"] + [f"{i},{cell}" for i, cell in enumerate(cells, start=1)]
    with _lake(tmp_path, ("\r\n".join(lines) + "\r\n").encode()) as lake:
        kinds = {c.name: c.type for c in lake.schema(TABLE).columns}
        values = [row["Value"] for row in lake.rows(TABLE)]
    return kinds["Value"], values


# ── the rule ────────────────────────────────────────────────────────────────


INTEGER_COLUMNS = [
    ("zero", ["0"], [0]),
    ("minus-zero", ["-0", "0", "1"], [0, 0, 1]),
    ("negative", ["-1", "-250"], [-1, -250]),
    ("i64-max", ["9223372036854775807"], [2**63 - 1]),
    ("i64-min", ["-9223372036854775808"], [-(2**63)]),
    ("i64-edges-and-small", ["9223372036854775807", "-9223372036854775808", "7"], None),
    ("with-empty", ["1", "", "3"], [1, None, 3]),
    ("quoted-empty", ["5", '""'], [5, None]),
]


@pytest.mark.parametrize(
    ("cells", "expected"),
    [pytest.param(c, e, id=f"constructed-{n}") for n, c, e in INTEGER_COLUMNS],
)
def test_integer_columns(tmp_path: Path, cells: list[str], expected: list[Cell] | None) -> None:
    kind, values = _column(tmp_path, *cells)
    assert kind == "INTEGER"
    assert all(v is None or type(v) is int for v in values)
    if expected is not None:
        assert values == expected
    else:
        assert values == [int(c) for c in cells]


REAL_COLUMNS = [
    ("fractions", ["0.5", "-1.25", "1.20000004768"], [0.5, -1.25, 1.20000004768]),
    ("integers-and-a-fraction", ["2", "0.5", "-3"], [2.0, 0.5, -3.0]),
    ("minus-zero-point-zero", ["-0.0", "1"], [0.0, 1.0]),
    ("trailing-zero", ["1.50"], [1.5]),
    ("i64-max-plus-one", ["9223372036854775808", "1"], [9.223372036854775808e18, 1.0]),
    ("i64-min-minus-one", ["-9223372036854775809"], [-9.223372036854775809e18]),
    ("u64-max", ["18446744073709551615", "0"], [1.8446744073709552e19, 0.0]),
    ("with-empty", ["0.25", ""], [0.25, None]),
]


@pytest.mark.parametrize(
    ("cells", "expected"),
    [pytest.param(c, e, id=f"constructed-{n}") for n, c, e in REAL_COLUMNS],
)
def test_real_columns(tmp_path: Path, cells: list[str], expected: list[Cell]) -> None:
    kind, values = _column(tmp_path, *cells)
    assert kind == "REAL"
    assert all(v is None or type(v) is float for v in values)
    assert values == expected


TEXT_CELLS = [
    ("leading-zero", "007"),
    ("double-zero", "00"),
    ("leading-zero-fraction", "00.5"),
    ("plus", "+1"),
    ("leading-space", " 1"),
    ("trailing-space", "1 "),
    ("exponent", "1e5"),
    ("upper-exponent", "1E-05"),
    ("bare-fraction", ".5"),
    ("bare-point", "5."),
    ("minus-bare-fraction", "-.5"),
    ("underscore", "1_000"),
    ("inf", "inf"),
    ("nan", "nan"),
    ("hex", "0x10"),
    ("arabic-indic-seven", chr(0x0667)),
    ("fullwidth-one", chr(0xFF11)),
    ("minus-only", "-"),
    ("double-minus", "--1"),
    ("two-points", "1.2.3"),
    ("comma-decimal", '"1,5"'),
    ("overflows-a-double", "1" + "0" * 400),
    ("longer-than-int-digit-limit", "9" * 5000),
]


@pytest.mark.parametrize("cell", [pytest.param(c, id=f"constructed-{n}") for n, c in TEXT_CELLS])
def test_a_cell_that_is_not_a_decimal_number_makes_the_column_text(
    tmp_path: Path, cell: str
) -> None:
    kind, values = _column(tmp_path, "1", cell, "", "2.5")
    assert kind == "TEXT"
    expected = cell[1:-1] if cell.startswith('"') else cell
    assert values == ["1", expected, "", "2.5"], "numbers in a TEXT column stay their text"


@pytest.mark.parametrize(
    "cells",
    [
        pytest.param(["", ""], id="constructed-all-empty"),
        pytest.param(["", '""', ""], id="constructed-all-empty-some-quoted"),
    ],
)
def test_a_column_with_no_non_empty_cell_is_text(tmp_path: Path, cells: list[str]) -> None:
    """The owner's ruling of 2026-09-30: an all-empty column keeps its empty
    strings (TEXT), rather than ADR-0028's words read literally (INTEGER)."""
    kind, values = _column(tmp_path, *cells)
    assert kind == "TEXT"
    assert values == [""] * len(cells)


def test_constructed_numeric_looking_text_column(tmp_path: Path) -> None:
    """Numbers until the last row; the whole column is TEXT and keeps every
    cell's text, zero padding included."""
    kind, values = _column(tmp_path, "12", "0034", "-5", "Ab")
    assert kind == "TEXT"
    assert values == ["12", "0034", "-5", "Ab"]


def test_constructed_empty_cells_are_null_or_empty_string_by_type(tmp_path: Path) -> None:
    body = b'I,R,T,E\n1,0.5,a,\n,,,""\n'
    with _lake(tmp_path, body) as lake:
        kinds = [(c.name, c.type) for c in lake.schema(TABLE).columns]
        rows = list(lake.rows(TABLE))
        stored = list(
            lake.query(
                'SELECT typeof("I"), typeof("R"), typeof("T"), typeof("E") FROM "Constructed"'
            )
        )
    assert kinds == [("I", "INTEGER"), ("R", "REAL"), ("T", "TEXT"), ("E", "TEXT")]
    assert rows == [
        {"I": 1, "R": 0.5, "T": "a", "E": ""},
        {"I": None, "R": None, "T": "", "E": ""},
    ]
    assert stored == [("integer", "real", "text", "text"), ("null", "null", "text", "text")]


def test_constructed_tables_are_strict(tmp_path: Path) -> None:
    """A value of the wrong type is refused by SQLite, not stored beside the
    others (the lake's second line behind the inference)."""
    with _lake(tmp_path, b"ID\n1\n") as lake:
        lake.load(TABLE)
    connection = sqlite3.connect(tmp_path / "lake" / f"{BUILD}.sqlite")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="cannot store TEXT value in INTEGER"):
            connection.execute("INSERT INTO \"Constructed\" VALUES ('x')")
    finally:
        connection.close()


def test_constructed_header_only_table_loads_with_no_rows(tmp_path: Path) -> None:
    with _lake(tmp_path, b"ID,Name_lang\r\n") as lake:
        schema = lake.schema(TABLE)
        assert list(lake.rows(TABLE)) == []
    assert [(c.name, c.type) for c in schema.columns] == [("ID", "TEXT"), ("Name_lang", "TEXT")]
    assert schema.row_count == 0


# ── names from outside ─────────────────────────────────────────────────────


HOSTILE_NAMES = [
    'Quote"Inside',
    '"',
    'x" INTEGER); DROP TABLE _lake_tables; --',
    "semi;colon",
    "with space",
    "line\nbreak",
    "",
    "rowid",
    "_rowid_",
    "oid",
    "Ünïcödé",
    "ünïcödé",
    "[bracketed]",
    "`backtick`",
    "?1",
    ":name",
]


def test_constructed_hostile_column_names_are_quoted_and_kept(tmp_path: Path) -> None:
    """Every header name is an identifier from outside: each is quoted, none
    runs, and each comes back unchanged. `rowid`, `_rowid_` and `oid` shadow
    SQLite's row id; rows still come back in CSV order. `Ü…` and `ü…` differ
    beyond ASCII, where SQLite compares names exactly."""
    quoted = [n.replace('"', '""') for n in HOSTILE_NAMES]
    header = ",".join(f'"{n}"' for n in quoted)
    body = f"{header}\n" + "".join(
        ",".join(str(row * 100 + i) for i in range(len(HOSTILE_NAMES))) + "\n" for row in (3, 1, 2)
    )
    with _lake(tmp_path, body.encode()) as lake:
        schema = lake.schema(TABLE)
        rows = list(lake.rows(TABLE))
        tables = [t.name for t in lake.tables()]
    assert [c.name for c in schema.columns] == HOSTILE_NAMES
    assert [list(row) for row in rows] == [HOSTILE_NAMES] * 3
    column = HOSTILE_NAMES.index("rowid")
    assert [row["rowid"] for row in rows] == [300 + column, 100 + column, 200 + column], (
        "CSV order, not the shadowing column's order"
    )
    assert tables == [TABLE]


@pytest.mark.parametrize(
    "shadowed",
    [
        pytest.param(["rowid"], id="constructed-only-rowid"),
        pytest.param(["oid"], id="constructed-only-oid"),
        pytest.param(["_rowid_"], id="constructed-only-underscore-rowid"),
        pytest.param(["ROWID", "_rowid_"], id="constructed-rowid-and-underscore-rowid"),
        pytest.param(["rowid", "Oid"], id="constructed-rowid-and-oid"),
    ],
)
def test_rows_come_back_in_csv_order_whichever_row_id_names_are_shadowed(
    tmp_path: Path, shadowed: list[str]
) -> None:
    """Each shadowing column holds values in the opposite order to the file,
    so ordering by the column instead of the row id is seen."""
    header = ",".join([*shadowed, "Value"])
    body = (
        header
        + "\n"
        + "".join(
            ",".join([str(key)] * len(shadowed) + [label]) + "\n"
            for key, label in ((3, "first"), (2, "second"), (1, "third"))
        )
    )
    with _lake(tmp_path, body.encode()) as lake:
        values = [row["Value"] for row in lake.rows(TABLE)]
    assert values == ["first", "second", "third"]


@pytest.mark.parametrize(
    ("header", "match"),
    [
        pytest.param(b"ID,Name,ID\n", "column names repeat", id="constructed-repeated"),
        pytest.param(b"ID,Name,id\n", "ASCII case is ignored", id="constructed-repeated-case"),
        pytest.param(b"ID,a\x00b\n", "NUL", id="constructed-nul"),
        pytest.param(b"\n", "no columns", id="constructed-empty-header"),
    ],
)
def test_a_header_the_lake_cannot_hold_is_malformed_and_writes_no_table(
    tmp_path: Path, header: bytes, match: str
) -> None:
    with _lake(tmp_path, header + b"1,2,3\n") as lake:
        with pytest.raises(MalformedTable, match=match):
            lake.load(TABLE)
        assert lake.tables() == []
        created = list(lake.query("SELECT name FROM sqlite_master WHERE name = ?", (TABLE,)))
    assert created == []


@pytest.mark.parametrize(
    ("body", "match"),
    [
        pytest.param(b"ID,Name\n1,a\n2\n", "1 fields, header has 2", id="constructed-short-row"),
        pytest.param(b"ID,Name\n1,a,b\n", "3 fields", id="constructed-long-row"),
        pytest.param(b"ID,Name\n1,\xff\n", "not UTF-8", id="constructed-bad-utf8"),
        pytest.param(b'ID,Name\n1,"open\n', "line", id="constructed-unclosed-quote"),
        pytest.param(b'ID,Name\n1,"a"b\n', "line 2", id="constructed-text-after-quote"),
    ],
)
def test_a_malformed_row_loads_nothing(tmp_path: Path, body: bytes, match: str) -> None:
    """Found by the first pass, before the transaction begins; a file that
    goes bad between the passes is rolled back (`test_db2lake.py`)."""
    with _lake(tmp_path, body + b"3,c\n" * 10) as lake:
        with pytest.raises(MalformedTable, match=match):
            lake.load(TABLE)
        assert lake.tables() == []
        assert list(lake.query("SELECT name FROM sqlite_master WHERE name = ?", (TABLE,))) == []


def test_constructed_quoted_fields_keep_commas_quotes_and_newlines(tmp_path: Path) -> None:
    body = b'ID,Text\n1,"a, b"\n2,"say ""hi"""\n3,"two\r\nlines"\n'
    with _lake(tmp_path, body) as lake:
        rows = list(lake.rows(TABLE))
        assert lake.schema(TABLE).row_count == 3
    assert [r["Text"] for r in rows] == ["a, b", 'say "hi"', "two\r\nlines"]


# ── bounds ─────────────────────────────────────────────────────────────────


def _columns(n: int) -> bytes:
    return (",".join(f"C{i}" for i in range(n)) + "\n" + ",".join(["0"] * n) + "\n").encode()


def test_constructed_the_widest_table_sqlite_allows_loads(tmp_path: Path) -> None:
    with _lake(tmp_path, _columns(2000)) as lake:
        schema = lake.schema(TABLE)
    assert len(schema.columns) == 2000 and schema.row_count == 1


@pytest.mark.parametrize("width", [2001, 30_000])
def test_constructed_a_header_wider_than_sqlite_allows_is_malformed_and_quick(
    tmp_path: Path, width: int
) -> None:
    """Refused by the column count before any per-name work (the repeat
    check was quadratic in the width)."""
    body = _columns(width)
    with _lake(tmp_path, body) as lake:
        clock, name = cpu_clock()
        started = clock()
        with pytest.raises(MalformedTable, match="more than SQLite's limit of 2000"):
            lake.load(TABLE)
        elapsed = clock() - started
        assert lake.tables() == []
    assert elapsed < 1, f"{elapsed:.2f} {name} s"


def test_constructed_repeats_in_a_wide_header_are_found_in_linear_time(tmp_path: Path) -> None:
    names = [f"C{i}" for i in range(1999)] + ["C0"]
    body = (",".join(names) + "\n").encode()
    with _lake(tmp_path, body) as lake, pytest.raises(MalformedTable, match="repeat"):
        lake.load(TABLE)


def test_constructed_a_record_longer_than_the_limit_is_refused_before_decoding(
    tmp_path: Path,
) -> None:
    """A 20 MB line: refused having read a little over `MAX_RECORD_BYTES`,
    not the line (the peak is traced across the load)."""
    body = b"ID,Text\n1," + b"x" * (20 << 20) + b"\n"
    data = GameData(_Tables({TABLE: body}), cache_dir=tmp_path / "cache")
    data.table(TABLE, BUILD)
    del body
    with Lake(BUILD, gamedata=data, lake_dir=tmp_path / "lake") as lake:
        tracemalloc.start()
        try:
            with pytest.raises(MalformedTable, match=f"longer than {MAX_RECORD_BYTES} bytes"):
                lake.load(TABLE)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    assert peak < 4 * MAX_RECORD_BYTES, peak


def test_constructed_a_multi_line_record_longer_than_the_limit_is_refused(tmp_path: Path) -> None:
    """Each physical line is short, and so is each field (under csv's own
    field limit); ten quoted fields of many lines make one record over it."""
    field = b'"' + b"line of text\n" * 9000 + b'"'  # 117,002 bytes
    body = b"A,B,C,D,E,F,G,H,I,J\n" + b",".join([field] * 10) + b"\n"
    assert len(body) > MAX_RECORD_BYTES
    with _lake(tmp_path, body) as lake, pytest.raises(MalformedTable, match="longer than"):
        lake.load(TABLE)


def test_constructed_a_record_just_under_the_limit_loads(tmp_path: Path) -> None:
    cell = "y" * 100_000  # under csv's own field limit of 131,072
    record = ",".join([cell] * 10)
    assert len(record) + 3 < MAX_RECORD_BYTES
    body = ("A,B,C,D,E,F,G,H,I,J\n" + record + "\n").encode()
    with _lake(tmp_path, body) as lake:
        (row,) = list(lake.rows(TABLE))
    assert row["J"] == cell


# ── size ───────────────────────────────────────────────────────────────────


def _big_csv(path: Path, rows: int) -> None:
    """Ten columns in wago's shapes: ids, flags, a sparse id, fixed-point
    floats, names, an empty text column, a numeric-looking text column."""
    header = "ID,ParentID,Flags,SpellID,PosX,Scale,Name_lang,Description_lang,Code,Extra\n"
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(header)
        chunk: list[str] = []
        for i in range(1, rows + 1):
            chunk.append(
                f"{i},{i // 7},{i * 2654435761 % 2**32},{'' if i % 3 else i * 11},"
                f"{(i % 2001) - 1000},{(i % 97) / 8:.11g},Name number {i},,"
                f"{'X' if i == rows else str(i % 1000)},{-i}\n"
            )
            if len(chunk) == 10_000:
                handle.write("".join(chunk))
                chunk.clear()
        handle.write("".join(chunk))


def _warm_lake(tmp_path: Path, csv_path: Path, **kwargs: int) -> Lake:
    """A lake whose table is already in the gamedata cache, so only the load
    itself is measured."""
    data = GameData(_Tables({TABLE: csv_path.read_bytes()}), cache_dir=tmp_path / "cache")
    data.table(TABLE, BUILD)
    return Lake(BUILD, gamedata=data, lake_dir=tmp_path / "lake", **kwargs)


def test_constructed_200k_rows_load_within_the_cpu_budget(tmp_path: Path) -> None:
    """The ticket's target: 200,000 rows by 10 columns in under 10 s of the
    process's CPU time (`_cpu_clock`, M11-26), measured around the load
    alone (both passes, the inserts and the commit)."""
    rows = 200_000
    source = tmp_path / "m12-03-big.csv"
    _big_csv(source, rows)
    with _warm_lake(tmp_path / "timed", source) as lake:
        clock, name = cpu_clock()
        started = clock()
        result = lake.load(TABLE)
        elapsed = clock() - started
        kinds = [c.type for c in lake.schema(TABLE).columns]
        last = list(lake.query('SELECT * FROM "Constructed" WHERE "ID" = ?', (rows,)))
    assert result.loaded and result.table.row_count == rows
    assert kinds == ["INTEGER"] * 5 + ["REAL", "TEXT", "TEXT", "TEXT", "INTEGER"]
    assert last[0][8] == "X" and last[0][9] == -rows
    assert result.table.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    print(
        f"db2lake: {rows} rows x 10 columns, {source.stat().st_size} bytes: {elapsed:.2f} {name} s"
    )
    assert elapsed < 10, f"{elapsed:.2f} {name} s for {rows} rows"


def _peak_load_bytes(tmp_path: Path, rows: int, batch_rows: int) -> int:
    source = tmp_path / f"m12-03-{rows}.csv"
    _big_csv(source, rows)
    with _warm_lake(tmp_path / f"lake-{rows}-{batch_rows}", source, batch_rows=batch_rows) as lake:
        tracemalloc.start()
        try:
            lake.load(TABLE)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    return peak


def test_constructed_load_memory_follows_the_batch_not_the_file(tmp_path: Path) -> None:
    """Python's peak allocation during a load (tracemalloc; SQLite's own page
    cache is not traced and is bounded by its `cache_size`): four times the
    rows at the same batch size stays within a small margin of the smaller
    file, and is far below what holding the table would take (about 500 bytes
    a row as Python objects); a bigger batch costs more."""
    small = _peak_load_bytes(tmp_path, 10_000, 500)
    large = _peak_load_bytes(tmp_path, 40_000, 500)
    bigger_batch = _peak_load_bytes(tmp_path, 40_000, 20_000)
    print(f"db2lake peak: 10k rows {small}, 40k rows {large}, 40k rows batch 20k {bigger_batch}")
    assert large < small * 1.25 + 64 * 1024, (small, large)
    assert large < 40_000 * 50, "a fraction of what the whole table would need"
    assert bigger_batch > large * 4, (large, bigger_batch)


def _peak_for(tmp_path: Path, body: bytes) -> int:
    data = GameData(_Tables({TABLE: body}), cache_dir=tmp_path / "cache")
    data.table(TABLE, BUILD)
    with Lake(BUILD, gamedata=data, lake_dir=tmp_path / "lake") as lake:
        tracemalloc.start()
        try:
            lake.load(TABLE)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    return peak


def _wide(rows: int) -> bytes:
    header = ",".join(f"C{i}" for i in range(2000))
    return (header + "\n" + (",".join(["0"] * 2000) + "\n") * rows).encode()


def _long_text(rows: int) -> bytes:
    return ("A,B,C,D\n" + (",".join(["t" * 5000] * 4) + "\n") * rows).encode()


def test_constructed_a_wide_table_is_batched_by_cells_not_rows(tmp_path: Path) -> None:
    """2,000 columns: a 5,000-row batch would hold 10 million cells. The
    batch is cut at 50,000 cells, so four times the rows cost no more."""
    small = _peak_for(tmp_path / "small", _wide(100))
    large = _peak_for(tmp_path / "large", _wide(400))
    print(f"db2lake wide peak: 100 rows {small}, 400 rows {large}")
    assert large < small * 1.25 + 256 * 1024, (small, large)
    assert large < 16 << 20, large


def test_constructed_long_text_is_batched_by_bytes_not_rows(tmp_path: Path) -> None:
    """20 KB rows: a 5,000-row batch would hold 100 MB. The batch is cut at
    4 MiB of CSV, so twice the rows cost no more."""
    small = _peak_for(tmp_path / "small", _long_text(1000))
    large = _peak_for(tmp_path / "large", _long_text(2000))
    print(f"db2lake long-text peak: 1000 rows {small}, 2000 rows {large}")
    assert large < small * 1.25 + 256 * 1024, (small, large)
    assert large < 16 << 20, large
