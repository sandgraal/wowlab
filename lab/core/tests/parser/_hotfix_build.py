"""Constructed hotfix caches for the tests (M22-23; L8: constructed, labelled
by every test that uses them).

`cache` writes the bytes of an `XFTH` file as `docs/LAB_FORMATS.md` §13
describes it; `encode_row` writes one row's data the way the client does
(fields in layout order at their widths, strings ending in a zero byte), the
inverse of `hotfix.decode_row`, so a derived row (values read from a real
file, never its bytes) can be turned back into a record. No byte here comes
from a real file.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from wowlab_core import hotfix

_PACK = {
    "i8": "<b",
    "u8": "<B",
    "i16": "<h",
    "u16": "<H",
    "i32": "<i",
    "u32": "<I",
    "f32": "<f",
}


@dataclass(frozen=True)
class Rec:
    """One record: the table's name (or a raw hash), the row id, its data."""

    table: str | int
    record_id: int
    data: bytes = b""
    status: int = 1
    push_id: int = 1
    unique_id: int = 0
    region_id: int = 91
    magic: bytes = hotfix.MAGIC
    size: int | None = None  # the size the header claims, when not len(data)


def header(build: int, *, version: int = 9, magic: bytes = hotfix.MAGIC) -> bytes:
    return magic + struct.pack("<ii", version, build) + bytes(32)


def record(r: Rec) -> bytes:
    table_hash = hotfix.sstrhash(r.table) if isinstance(r.table, str) else r.table
    size = len(r.data) if r.size is None else r.size
    return (
        r.magic
        + struct.pack(
            "<iiiIiiB3s",
            r.region_id,
            r.push_id,
            r.unique_id,
            table_hash,
            r.record_id,
            size,
            r.status,
            b"\0\0\0",
        )
        + r.data
    )


def cache(build: int, records: Iterable[Rec], *, version: int = 9) -> bytes:
    return header(build, version=version) + b"".join(record(r) for r in records)


def encode_row(layout: hotfix.TableLayout, row: Mapping[str, str]) -> bytes:
    """A row's data in `layout`: the inverse of `hotfix.decode_row` (the ID
    is not in it). Every column of the layout but `ID` must be in `row`."""
    out = bytearray()
    for f in layout.fields:
        names = [f"{f.name}_{i}" for i in range(f.count)] if f.count else [f.name]
        for column in names:
            text = row[column]
            if f.kind == "str":
                out += text.encode("utf-8") + b"\0"
            elif f.kind == "f32":
                out += struct.pack("<f", float(text))
            else:
                out += struct.pack(_PACK[f.kind], int(text))
    return bytes(out)


def blank_row(layout: hotfix.TableLayout, **values: str) -> dict[str, str]:
    """Every column of `layout` but `ID` empty or zero, then `values`."""
    row: dict[str, str] = {}
    for f in layout.fields:
        names = [f"{f.name}_{i}" for i in range(f.count)] if f.count else [f.name]
        for column in names:
            row[column] = "" if f.kind == "str" else "0"
    row.update(values)
    return row
