#!/usr/bin/env python3
"""Cut an ID-filtered subset out of a recorded wago.tools CSV (M12-01).

    uv run python scripts/wago_subset.py SOURCE --sha256 HEX --column NAME \
        (--value V ... | --values-from CSV --values-column COL ... | --no-values) \
        [--except V ...] --out PATH

Writes the source's header line and every data line whose cell in the
column NAME is one of the given values, in the source's order, to PATH.
The rule is `docs/LAB_PLAN.md` §14.6: a recording of a table the repository
has no reason to hold whole is the header and whole, verbatim lines.

How it edits: it does not. Each line is copied as the bytes the source holds,
terminator included; nothing is parsed and re-serialized (the rule the scrub
tool follows). The CSV is parsed only to read one cell of each line, and a
line here is one CSV record: a quoted field holding a line break keeps its
record's physical lines together, so they are kept or dropped as one.

Refusals (exit 1, nothing written): the source's SHA-256 is not the one given
(so the provenance row names exactly the download that was cut); the column is
missing from the header or named twice; a line is not valid UTF-8, has an
unterminated quote, cannot be read as CSV, or has a different number of
fields from the header; the output file already exists (a committed fixture is
never replaced).

Matching is exact text: the cell as the CSV reader gives it (quotes removed)
must equal a value character for character, so `7` does not match `007`.
`--values-from` takes the non-empty cells of the named columns of another CSV;
`--except` drops values from the set (`--except 0` for "no spell"). An empty
set must be asked for with `--no-values`: the output is then the header alone.

On success it prints the filter, the counts and the values that matched no
line, for the provenance row in `lab/core/tests/fixtures/README.md`.
Standard library only; it never touches the network or a game install.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import re
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

_SHA256 = re.compile(r"[0-9a-f]{64}")


class SubsetError(Exception):
    """A refusal: the message says why, and nothing was written."""


@dataclass(frozen=True)
class Record:
    """One CSV record: its verbatim bytes (terminator included) and where it starts."""

    raw: bytes
    line: int  # 1-based physical line number of the record's first line


@dataclass(frozen=True)
class Subset:
    header: Record
    kept: list[Record]
    total: int  # data records in the source
    unmatched: list[str]  # values no line matched, in the order given

    def data(self) -> bytes:
        return self.header.raw + b"".join(r.raw for r in self.kept)


def records(data: bytes) -> Iterator[Record]:
    """Split ``data`` into CSV records, byte for byte.

    A record ends at a line feed outside quotes. RFC 4180 escapes a quote
    inside a quoted field by doubling it, so a record is complete exactly
    when it holds an even number of quote bytes. Joining the pieces gives
    back ``data`` unchanged.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        raise SubsetError("the file starts with a byte-order mark; wago.tools sends none")
    start = 0  # first byte of the record being read
    scan = 0  # first byte of its next physical line
    quotes = 0  # quote bytes in the record so far
    line = 1  # physical line number at `start`
    size = len(data)
    while scan < size:
        end = data.find(b"\n", scan)
        end = size if end == -1 else end + 1
        quotes += data.count(b'"', scan, end)
        scan = end
        if quotes % 2 == 0:
            yield Record(data[start:end], line)
            line += data.count(b"\n", start, end)
            start = end
            quotes = 0
    if start < size:
        raise SubsetError(f"line {line}: unterminated quote at the end of the file")


def fields(record: Record) -> list[str]:
    """The cells of one record, read with the standard CSV reader (strict)."""
    try:
        text = record.raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SubsetError(f"line {record.line}: not valid UTF-8 ({exc.reason})") from None
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise SubsetError(f"line {record.line}: not readable as CSV ({exc})") from None
    if not rows:
        raise SubsetError(f"line {record.line}: an empty line")
    if len(rows) != 1:
        raise SubsetError(f"line {record.line}: expected one CSV record, read {len(rows)}")
    return rows[0]


def column_index(header: list[str], column: str) -> int:
    found = [i for i, name in enumerate(header) if name == column]
    if not found:
        raise SubsetError(f"column {column!r} is not in the header")
    if len(found) > 1:
        raise SubsetError(f"column {column!r} is named {len(found)} times in the header")
    return found[0]


def select(data: bytes, column: str, values: Sequence[str]) -> Subset:
    """The header and every record whose ``column`` cell is in ``values``."""
    it = records(data)
    header = next(it, None)
    if header is None:
        raise SubsetError("the source is empty")
    names = fields(header)
    index = column_index(names, column)
    wanted = set(values)
    seen: set[str] = set()
    kept: list[Record] = []
    total = 0
    for record in it:
        total += 1
        cells = fields(record)
        if len(cells) != len(names):
            raise SubsetError(
                f"line {record.line}: {len(cells)} fields, the header has {len(names)}"
            )
        if cells[index] in wanted:
            kept.append(record)
            seen.add(cells[index])
    unmatched = [v for v in dict.fromkeys(values) if v not in seen]
    return Subset(header, kept, total, unmatched)


def values_from(path: Path, columns: Sequence[str]) -> list[str]:
    """Distinct non-empty cells of ``columns`` in ``path``, in first-seen order."""
    it = records(path.read_bytes())
    header = next(it, None)
    if header is None:
        raise SubsetError(f"{path.name}: the file is empty")
    names = fields(header)
    indexes = [column_index(names, c) for c in columns]
    found: dict[str, None] = {}
    for record in it:
        cells = fields(record)
        if len(cells) != len(names):
            raise SubsetError(
                f"{path.name}: line {record.line}: {len(cells)} fields, the header has {len(names)}"
            )
        for i in indexes:
            if cells[i] != "":
                found.setdefault(cells[i], None)
    return list(found)


def _numeric_key(value: str) -> tuple[int, int, str]:
    return (0, int(value), "") if value.isdecimal() and value.isascii() else (1, 0, value)


def describe(
    source: Path,
    data: bytes,
    digest: str,
    column: str,
    values: Sequence[str],
    origin: str,
    subset: Subset,
    out: Path,
) -> str:
    kept = len(subset.kept)
    unmatched = sorted(subset.unmatched, key=_numeric_key)
    lines = [
        f"source: {source.name}, {len(data)} bytes, sha256 {digest}, {subset.total} data lines",
        f"filter: column {column} in {len(values)} distinct values ({origin})",
        f"kept: the header and {kept} of {subset.total} data lines, in file order, "
        f"to {out.name} ({len(subset.data())} bytes, "
        f"sha256 {hashlib.sha256(subset.data()).hexdigest()})",
        f"values with no line: {len(unmatched)}"
        + (f" ({', '.join(unmatched)})" if unmatched else ""),
    ]
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wago_subset.py",
        description="Keep the header and the whole, verbatim lines of a recorded CSV "
        "whose cell in one column is in a set of values (docs/LAB_PLAN.md §14.6).",
    )
    parser.add_argument("source", type=Path, help="the recorded CSV, as downloaded")
    parser.add_argument(
        "--sha256", required=True, help="the SHA-256 the source must have (lower-case hex)"
    )
    parser.add_argument("--column", required=True, help="the column whose value selects a line")
    parser.add_argument("--value", action="append", default=[], help="a value to keep (repeat)")
    parser.add_argument(
        "--values-from", type=Path, help="a CSV whose --values-column cells are values to keep"
    )
    parser.add_argument(
        "--values-column", action="append", default=[], help="a column of --values-from (repeat)"
    )
    parser.add_argument(
        "--except",
        dest="exclude",
        action="append",
        default=[],
        help="a value to drop from the set (repeat)",
    )
    parser.add_argument(
        "--no-values", action="store_true", help="the set is empty: write the header only"
    )
    parser.add_argument("--out", type=Path, required=True, help="the subset; must not exist")
    args = parser.parse_args(argv)
    if bool(args.values_from) != bool(args.values_column):
        parser.error("--values-from and --values-column go together")
    has_values = bool(args.value) or bool(args.values_from)
    if args.no_values == has_values:
        parser.error("give --value or --values-from, or --no-values for an empty set")
    if not _SHA256.fullmatch(args.sha256):
        parser.error("--sha256 must be 64 lower-case hex digits")
    return args


def run(args: argparse.Namespace) -> int:
    data = args.source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != args.sha256:
        raise SubsetError(
            f"{args.source.name}: sha256 is {digest}, not {args.sha256}; "
            "cut only the download the provenance row names"
        )
    values = list(dict.fromkeys(args.value))
    origins: list[str] = []
    if args.value:
        origins.append(f"{len(values)} given with --value")
    if args.values_from is not None:
        found = values_from(args.values_from, args.values_column)
        from_digest = hashlib.sha256(args.values_from.read_bytes()).hexdigest()
        origins.append(
            f"the non-empty {', '.join(args.values_column)} cells of {args.values_from.name} "
            f"(sha256 {from_digest}): {len(found)} distinct"
        )
        values = list(dict.fromkeys([*values, *found]))
    if args.exclude:
        excluded = set(args.exclude)
        dropped = sum(1 for v in values if v in excluded)
        values = [v for v in values if v not in excluded]
        origins.append(f"except {', '.join(dict.fromkeys(args.exclude))} ({dropped} dropped)")
    if args.no_values:
        origins.append("an empty set, asked for with --no-values")
    subset = select(data, args.column, values)
    out: Path = args.out
    try:
        with out.open("xb") as handle:
            handle.write(subset.data())
    except FileExistsError:
        raise SubsetError(f"{out}: already exists; a recorded fixture is never replaced") from None
    print(describe(args.source, data, digest, args.column, values, "; ".join(origins), subset, out))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except SubsetError as exc:
        print(f"wago_subset: refused: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"wago_subset: {exc.strerror or exc}: {exc.filename}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
