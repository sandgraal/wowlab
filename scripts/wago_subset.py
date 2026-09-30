#!/usr/bin/env python3
"""Cut an ID-filtered subset out of a recorded wago.tools CSV (M12-01).

    uv run python scripts/wago_subset.py SOURCE --sha256 HEX --column NAME \
        (--value V ... | --values-from CSV --values-column COL ... | --no-values) \
        [--except V ...] [--max-bytes N] --out PATH

Writes the source's header line and every data line whose cell in the
column NAME is one of the given values, in the source's order, to PATH.
The rule is `docs/LAB_PLAN.md` §14.6: a recording of a table the repository
has no reason to hold whole is the header and whole, verbatim lines.

How it edits: it does not. Each line is copied as the bytes the source holds,
terminator included; nothing is parsed and re-serialized (the rule the scrub
tool follows). The CSV is parsed only to read one cell of each line, and a
line here is one CSV record: a quoted field holding a line break keeps its
record's physical lines together, so they are kept or dropped as one.

Refusals (exit 1):

- `--out` is inside a game install (a directory with `.build.info` in it or
  above it, symlinks resolved), or its folder cannot be resolved (a symlink
  loop): only `guard` writes into an install (L1, L2);
- the output file already exists (a committed fixture is never replaced);
- the source or `--values-from` is over `--max-bytes` (64 MiB unless given;
  at most 1 GiB, the table cap `gamedata` uses);
- the source's SHA-256 is not the one given (so the provenance row names
  exactly the download that was cut);
- the column is missing from the header or named twice;
- a line is not valid UTF-8, has an unterminated quote, cannot be read as
  CSV, or has a different number of fields from the header;
- the header line is longer than `csv.field_size_limit()` bytes (131072 by
  default) or has more than 4096 columns; a data line, counted in bytes with
  its terminator, is longer than the smaller of 1 MiB and the header's
  column count times (`csv.field_size_limit()` + 3). Both are checked before
  the line is decoded. (A data line within that count can still be refused
  by the CSV reader, whose limit is per field, in characters);
- the set of values holds more than 100000 distinct values (real use is a
  few hundred).

Everything, the printed description included, is worked out before the
output file is created. If writing it then fails part-way (a full disk), the
file this run created is removed, so a refusal or a failure never leaves a
file behind. Any other error ends in one line on stderr and exit 1.

Matching is exact text: the cell as the CSV reader gives it (quotes removed)
must equal a value character for character, so `7` does not match `007`.
`--value` must not be empty. `--values-from` takes the non-empty cells of the
named columns of another CSV, read once (the SHA-256 printed is of the bytes
the values came from); `--except` drops values from the set (`--except 0`
for "no spell"). An empty set must be asked for with `--no-values`: the output
is then the header alone.

On success it prints the filter, the counts and the values that matched no
line (the first 20 in numeric order, then how many more), for the provenance
row in `lab/core/tests/fixtures/README.md`. That text goes into a public
file, so every name and value from the input or the command line is printed
as is only when it is printable (`str.isprintable`: no control, format, bidi
or line-separator character); otherwise it is printed as its `ascii()`
escape. A line the terminal's encoding cannot represent is printed with
backslash escapes instead. Standard library only; it never touches the
network.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import re
import sys
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

_SHA256 = re.compile(r"[0-9a-f]{64}")
INSTALL_MARKER = ".build.info"  # at an install's root (docs/LAB_FILE_MAP.md)
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
MAX_BYTES_CEILING = 1024 * 1024 * 1024  # gamedata's table cap
MAX_COLUMNS = 4096  # the widest committed wago table has 62
MAX_LINE_BYTES = 1024 * 1024  # a wago line is a few hundred bytes
MAX_VALUES = 100_000
SHOWN_UNMATCHED = 20
_CHUNK = 1024 * 1024


class SubsetError(Exception):
    """A refusal: the message says why, and nothing was written."""


def _safe(text: str) -> str:
    """``text`` for a terminal and a public provenance row: as is when every
    character is printable, else its ``ascii()`` escape (quoted)."""
    return text if text.isprintable() else ascii(text)


def _emit(text: str, stream: TextIO) -> None:
    """Print ``text``; if ``stream``'s encoding cannot represent it, print it
    with backslash escapes instead of failing."""
    try:
        text.encode(stream.encoding or "utf-8")
    except (UnicodeEncodeError, LookupError):
        text = text.encode("ascii", "backslashreplace").decode("ascii")
    print(text, file=stream)


@dataclass(frozen=True)
class Record:
    """One CSV record: its verbatim bytes (terminator included) and where it starts."""

    raw: bytes
    line: int  # 1-based physical line number of the record's first line


@dataclass(frozen=True)
class Subset:
    data: bytes  # the header and the kept records, verbatim, in file order
    kept: int  # data records kept
    total: int  # data records in the source
    unmatched: list[str]  # values no line matched, in the order given


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


def line_cap(columns: int) -> int:
    """The most bytes a data line may have, terminator included: the smaller
    of ``MAX_LINE_BYTES`` and ``columns`` times (``csv.field_size_limit()`` + 3)."""
    return min(MAX_LINE_BYTES, columns * (csv.field_size_limit() + 3))


def table(data: bytes) -> Iterator[tuple[Record, list[str]]]:
    """The header record and its names, then every data record and its cells.

    Byte counts are checked before a line is decoded: the header line
    (terminator included) must be at most ``csv.field_size_limit()`` bytes,
    and every data line at most ``line_cap(columns)`` bytes. After decoding,
    the header may have at most ``MAX_COLUMNS`` columns and every data line
    must have exactly as many fields.
    """
    it = records(data)
    header = next(it, None)
    if header is None:
        raise SubsetError("the file is empty")
    limit = csv.field_size_limit()
    if len(header.raw) > limit:
        raise SubsetError(f"line 1: the header is {len(header.raw)} bytes, over {limit}")
    names = fields(header)
    if len(names) > MAX_COLUMNS:
        raise SubsetError(f"line 1: the header has {len(names)} columns, over {MAX_COLUMNS}")
    yield header, names
    cap = line_cap(len(names))
    for record in it:
        if len(record.raw) > cap:
            raise SubsetError(
                f"line {record.line}: {len(record.raw)} bytes, longer than the {cap} "
                f"a line of {len(names)} columns may have"
            )
        cells = fields(record)
        if len(cells) != len(names):
            raise SubsetError(
                f"line {record.line}: {len(cells)} fields, the header has {len(names)}"
            )
        yield record, cells


def column_index(header: list[str], column: str) -> int:
    found = [i for i, name in enumerate(header) if name == column]
    if not found:
        raise SubsetError(f"column {column!r} is not in the header")
    if len(found) > 1:
        raise SubsetError(f"column {column!r} is named {len(found)} times in the header")
    return found[0]


def select(data: bytes, column: str, wanted: Collection[str]) -> Subset:
    """The header and every record whose ``column`` cell is in ``wanted``
    (an ordered collection with fast membership, such as a dict's keys)."""
    rows = table(data)
    header, names = next(rows)
    index = column_index(names, column)
    seen: set[str] = set()
    out = bytearray(header.raw)
    kept = 0
    total = 0
    for record, cells in rows:
        total += 1
        if cells[index] in wanted:
            out += record.raw
            kept += 1
            seen.add(cells[index])
    unmatched = [v for v in wanted if v not in seen]
    return Subset(bytes(out), kept, total, unmatched)


def values_from(data: bytes, columns: Sequence[str]) -> dict[str, None]:
    """Distinct non-empty cells of ``columns`` in ``data``, in first-seen order."""
    rows = table(data)
    _, names = next(rows)
    indexes = [column_index(names, c) for c in columns]
    found: dict[str, None] = {}
    for _, cells in rows:
        for i in indexes:
            if cells[i] != "":
                found.setdefault(cells[i], None)
        if len(found) > MAX_VALUES:
            raise SubsetError(f"more than {MAX_VALUES} distinct values")
    return found


def refuse_install(out: Path) -> None:
    """Refuse an output path inside a game install, every symlink resolved."""
    try:
        parent = out.parent.resolve()
    except (OSError, RuntimeError) as exc:  # RuntimeError: a symlink loop (3.12)
        raise SubsetError(
            f"cannot resolve the folder of {_safe(str(out))} ({_safe(str(exc))}), "
            "so it cannot be shown to be outside a game install"
        ) from None
    for candidate in (parent, *parent.parents):
        if (candidate / INSTALL_MARKER).exists():
            raise SubsetError(
                f"{_safe(str(out))} is inside a game install ({_safe(str(candidate))}); "
                "nothing but guard writes into an install (L1, L2)"
            )


def read_capped(path: Path, max_bytes: int) -> bytes:
    """The file's bytes, read in chunks so nothing is allocated for bytes
    that are not there; refused once it passes ``max_bytes``."""
    buf = bytearray()
    with path.open("rb") as handle:
        while chunk := handle.read(min(_CHUNK, max_bytes + 1 - len(buf))):
            buf += chunk
            if len(buf) > max_bytes:
                raise SubsetError(
                    f"{_safe(path.name)} is over {max_bytes} bytes; "
                    "--max-bytes allows a larger file"
                )
    return bytes(buf)


def write_new(path: Path, data: bytes) -> None:
    """Create ``path`` and write ``data``; never replace a file. If the write
    fails part-way, remove the file this call created."""
    try:
        handle = path.open("xb")
    except FileExistsError:
        raise SubsetError(
            f"{_safe(str(path))}: already exists; a recorded fixture is never replaced"
        ) from None
    try:
        with handle:
            handle.write(data)
    except BaseException as exc:
        path.unlink(missing_ok=True)
        if isinstance(exc, OSError) and exc.filename is None:
            raise OSError(exc.errno, exc.strerror or str(exc), str(path)) from exc
        raise


def _numeric_key(value: str) -> tuple[int, int, str, str]:
    """Decimal text in numeric order without ``int()`` (which refuses more
    than 4300 digits), then everything else in text order."""
    if value.isascii() and value.isdecimal():
        digits = value.lstrip("0")
        return (0, len(digits), digits, value)
    return (1, 0, "", value)


def describe(
    source: Path,
    data: bytes,
    digest: str,
    column: str,
    values: Collection[str],
    origin: str,
    subset: Subset,
    out: Path,
) -> str:
    unmatched = sorted(subset.unmatched, key=_numeric_key)
    shown = ", ".join(_safe(v) for v in unmatched[:SHOWN_UNMATCHED])
    if len(unmatched) > SHOWN_UNMATCHED:
        shown = f"the first {SHOWN_UNMATCHED}: {shown}; {len(unmatched) - SHOWN_UNMATCHED} more"
    lines = [
        f"source: {_safe(source.name)}, {len(data)} bytes, sha256 {digest}, "
        f"{subset.total} data lines",
        f"filter: column {_safe(column)} in {len(values)} distinct values ({origin})",
        f"kept: the header and {subset.kept} of {subset.total} data lines, in file order, "
        f"to {_safe(out.name)} ({len(subset.data)} bytes, "
        f"sha256 {hashlib.sha256(subset.data).hexdigest()})",
        f"values with no line: {len(unmatched)}" + (f" ({shown})" if unmatched else ""),
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
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help="refuse a source or --values-from larger than this "
        f"(default {DEFAULT_MAX_BYTES}, at most {MAX_BYTES_CEILING})",
    )
    parser.add_argument("--out", type=Path, required=True, help="the subset; must not exist")
    args = parser.parse_args(argv)
    if bool(args.values_from) != bool(args.values_column):
        parser.error("--values-from and --values-column go together")
    has_values = bool(args.value) or bool(args.values_from)
    if args.no_values == has_values:
        parser.error("give --value or --values-from, or --no-values for an empty set")
    if "" in args.value:
        parser.error("--value must not be empty (an unset shell variable?)")
    if not _SHA256.fullmatch(args.sha256):
        parser.error("--sha256 must be 64 lower-case hex digits")
    if not 1 <= args.max_bytes <= MAX_BYTES_CEILING:
        parser.error(f"--max-bytes must be from 1 to {MAX_BYTES_CEILING}")
    return args


def run(args: argparse.Namespace) -> int:
    out: Path = args.out
    refuse_install(out)
    data = read_capped(args.source, args.max_bytes)
    digest = hashlib.sha256(data).hexdigest()
    if digest != args.sha256:
        raise SubsetError(
            f"{_safe(args.source.name)}: sha256 is {digest}, not {args.sha256}; "
            "cut only the download the provenance row names"
        )
    values: dict[str, None] = dict.fromkeys(args.value)
    origins: list[str] = []
    if args.value:
        origins.append(f"{len(values)} given with --value")
    if args.values_from is not None:
        ids = read_capped(args.values_from, args.max_bytes)
        try:
            found = values_from(ids, args.values_column)
        except SubsetError as exc:
            raise SubsetError(f"{_safe(args.values_from.name)}: {exc}") from None
        columns = ", ".join(_safe(c) for c in args.values_column)
        origins.append(
            f"the non-empty {columns} cells of {_safe(args.values_from.name)} "
            f"(sha256 {hashlib.sha256(ids).hexdigest()}): {len(found)} distinct"
        )
        values.update(found)
    if args.exclude:
        excluded = dict.fromkeys(args.exclude)
        dropped = sum(1 for v in excluded if v in values)
        for v in excluded:
            values.pop(v, None)
        shown = ", ".join(_safe(v) for v in excluded)
        origins.append(f"except {shown} ({dropped} dropped)")
    if args.no_values:
        origins.append("an empty set, asked for with --no-values")
    if len(values) > MAX_VALUES:
        raise SubsetError(f"{len(values)} distinct values, more than {MAX_VALUES}")
    subset = select(data, args.column, values.keys())
    text = describe(args.source, data, digest, args.column, values, "; ".join(origins), subset, out)
    write_new(out, subset.data)
    _emit(text, sys.stdout)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except SubsetError as exc:
        _emit(f"wago_subset: refused: {exc}", sys.stderr)
    except OSError as exc:
        where = _safe(str(exc.filename)) if exc.filename is not None else "?"
        _emit(f"wago_subset: {_safe(exc.strerror or str(exc))}: {where}", sys.stderr)
    except (RuntimeError, ValueError, MemoryError, OverflowError) as exc:
        detail = _safe(str(exc)) or "no detail"
        _emit(f"wago_subset: {type(exc).__name__}: {detail}", sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
