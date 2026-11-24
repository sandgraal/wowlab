"""hotfix: the client's own hotfix cache, read as data (M22-23).

`Cache/ADB/<locale>/DBCache.bin` in a flavor folder holds rows of the game
tables the server has sent the client, at login or when the client first
needed a row it did not ship with (an item it showed), as the client last
saved them (GLOSSARY, "Hotfix cache"). wago.tools' exports lack them, so about
half of the weapons and armour in `Item` have no `ItemSparse` row there
(`docs/DATA_SOURCES.md`, 2026-11-23). This module reads the file and
decodes the rows of the tables `hotfix_layouts.toml` describes (`Item`,
`ItemSparse` and `TraitNodeEntry`) that a caller asks for;
`gamedata.HotfixOverlay` lays them over the cached tables of the same build (ADR-0022, ADR-0033 note of 2026-11-23).

The format (`docs/LAB_FORMATS.md` §16), as wowsims/forever (MIT) reads it
in `tools/db2tool/wdc/hotfix.go` (commit
a8bd7fd83a3a7c5102012ed87d86a71d175b3a89, a Go translation of DBCD's
hotfix reader and wow.tools.local's table hash; read on GitHub, not
copied), checked on the owner's file at build 71037:

- a 44-byte header: `XFTH`, the version (int32, 9), the build (int32),
  then 32 bytes wowlab does not read;
- records to the end of the file, each a 32-byte header (`XFTH`, region,
  push id, unique id (int32 each), the table's hash (uint32), the row id
  (int32), the data size (int32), a status byte, three zero bytes) and
  then `data size` bytes of row data;
- the table hash is `sstrhash` of the table's name in capitals;
- the header's build is that of the client that last wrote the file;
  records carried over from an earlier build keep their bytes;
- status 1 is a row; what 2, 3 and 4 mean is **[verify]**: `gamedata`
  takes 2 and 3 as the row removed and leaves anything else alone, where
  wowsims removes the row on any status but 1 (the two rules differ on
  two non-gear rows of the owner's file at 71037).

What this module holds to:

- **L1.** The file is opened read-only, from the install root one
  component at a time (`snapshot.read_regular_file`), read whole within
  `MAX_FILE_BYTES` and closed; nothing is written anywhere.
- **Hostile input.** Every length and offset is checked against the bytes
  read before it is used; a negative or overlong size, a missing record
  magic or a short header raises `HotfixError` with the offset. At most
  `MAX_RECORDS` records are walked and `MAX_KEPT` kept.
- **No key, no decryption.** Only the records of the tables asked for are
  kept; every other record (among them the client's encryption keys) is
  stepped over by its size and its bytes are never looked at. Nothing is
  decrypted.
- **No flavor and no build in code (L6).** The build is read from the
  file; the layouts name no build and are used only when they match the
  build's own table columns (`gamedata`).
"""

from __future__ import annotations

import functools
import math
import os
import re
import stat
import struct
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import snapshot, wtfconfig
from wowlab_core.errors import WowlabError

__all__ = [
    "CACHE_FILE",
    "HEADER_BYTES",
    "LAYOUTS_FILE",
    "MAGIC",
    "MAX_FILE_BYTES",
    "MAX_KEPT",
    "MAX_RECORDS",
    "MAX_TABLE_ROWS",
    "RECORD_HEADER_BYTES",
    "SOURCE_HOTFIX",
    "SOURCE_KEY",
    "STATUS_REMOVED",
    "STATUS_ROW",
    "SUPPORTED_VERSION",
    "Field",
    "HotfixCache",
    "HotfixError",
    "HotfixRecord",
    "LayoutError",
    "OverlayReport",
    "OverlayTable",
    "TableLayout",
    "decode_row",
    "find_caches",
    "game_locale",
    "layouts",
    "parse",
    "parse_layouts",
    "read",
    "sstrhash",
    "table_names",
]

MAGIC = b"XFTH"
SUPPORTED_VERSION = 9
HEADER_BYTES = 44  # magic, version, build, 32 bytes not read
RECORD_HEADER_BYTES = 32  # magic, six int32 fields, the status byte, three zero bytes
CACHE_FILE = "DBCache.bin"
LAYOUTS_FILE = "hotfix_layouts.toml"
# The owner's file was 8 MB at build 71037 (106,407 records, 18,999 of them
# Item, ItemSparse or TraitNodeEntry); these leave room for about four times
# that and bound what a crafted file can cost on every `doctor` run
# (security review of M22-23: at 128 MiB a crafted cache cost about 4 GiB).
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_RECORDS = MAX_FILE_BYTES // RECORD_HEADER_BYTES
MAX_KEPT = 200_000
# Rows of one table `gamedata` decodes at most (9,532 ItemSparse records in
# the owner's file); past it the overlay is refused.
MAX_TABLE_ROWS = 50_000
STATUS_ROW = 1
STATUS_REMOVED = frozenset({2, 3})
# The key a row read from the cache carries beside its columns, and its
# value; no table column can be named so (a column name is a word).
SOURCE_KEY = "wowlab:source"
SOURCE_HOTFIX = "hotfix cache"

_HEADER = struct.Struct("<4sii")
_RECORD = struct.Struct("<4siiiIiiB3s")
_FIELD = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]{0,63}):(i8|u8|i16|u16|i32|u32|f32|str)(?::([0-9]{1,3}))?"
)
_TABLE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_SCALAR: Mapping[str, struct.Struct] = {
    "i8": struct.Struct("<b"),
    "u8": struct.Struct("<B"),
    "i16": struct.Struct("<h"),
    "u16": struct.Struct("<H"),
    "i32": struct.Struct("<i"),
    "u32": struct.Struct("<I"),
    "f32": struct.Struct("<f"),
}
_ARRAY_MAX = 64
_FIELDS_MAX = 512
_LAYOUTS_LIMIT = 256 * 1024
# SStrHash's table (wow.tools.local, as wowsims/forever's hotfix.go has it).
_SSTR = (
    0x486E26EE, 0xDCAA16B3, 0xE1918EEF, 0x202DAFDB,
    0x341C7DC7, 0x1C365303, 0x40EF2D37, 0x65FD5E49,
    0xD6057177, 0x904ECE93, 0x1C38024F, 0x98FD323B,
    0xE3061AE7, 0xA39B0FA1, 0x9797F25F, 0xE4444563,
)  # fmt: skip
_U32 = 0xFFFFFFFF


class HotfixError(WowlabError):
    """The hotfix cache cannot be read, or a row of it cannot be decoded."""


class LayoutError(ValueError):
    """`hotfix_layouts.toml` is not what this module reads."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def sstrhash(name: str) -> int:
    """The hash the cache keys a table on: SStrHash of the name in capitals
    (wow.tools.local; wowsims/forever `SStrHash`). ASCII names only."""
    v, x = 0x7FED7FED, 0xEEEEEEEE
    for c in name.upper().encode("ascii"):
        v = (v + x) & _U32
        v ^= (_SSTR[(c >> 4) & 0xF] - _SSTR[c & 0xF]) & _U32
        x = (x * 33 + v + c + 3) & _U32
    return v


# ─── the file ────────────────────────────────────────────────────────────────


class HotfixRecord(_Model):
    """One record of a table asked for, as the file holds it."""

    table: str
    record_id: int
    # Server pushes count up through the file; -1 and ids from 0x01000000 up
    # are rows the client fetched when it needed them (the reference; the
    # owner's file is consistent with it).
    push_id: int
    unique_id: int
    region_id: int
    status: int  # 1 a row; 2, 3 and 4 [verify] (`gamedata` takes 2 and 3 as removed)
    data: bytes
    offset: int  # of the record's header in the file


class HotfixCache(_Model):
    """What `parse` read: the header, how many records the file holds, and
    the records of the tables asked for, in file order."""

    version: int
    build: int
    records_in_file: int
    records: list[HotfixRecord]


def parse(data: bytes, tables: Iterable[str]) -> HotfixCache:
    """The cache in `data`, keeping the records of `tables` (by name).

    Raises `HotfixError`, naming the offset, for a file that is not an
    `XFTH` cache of `SUPPORTED_VERSION` or whose records do not fit it."""
    wanted: dict[int, str] = {}
    for name in tables:
        if not _TABLE.fullmatch(name):
            raise ValueError(f"not a table name: {name!r}")
        wanted[sstrhash(name)] = name
    size = len(data)
    if size < _HEADER.size or data[:4] != MAGIC:
        raise HotfixError("not a hotfix cache: it does not start with XFTH")
    _, version, build = _HEADER.unpack_from(data, 0)
    if version != SUPPORTED_VERSION:
        raise HotfixError(
            f"a hotfix cache of format version {version}; wowlab reads version "
            f"{SUPPORTED_VERSION} only"
        )
    if size < HEADER_BYTES:
        raise HotfixError(f"the hotfix cache ends inside its {HEADER_BYTES}-byte header")
    records: list[HotfixRecord] = []
    walked = 0
    pos = HEADER_BYTES
    while pos < size:
        if walked >= MAX_RECORDS:
            raise HotfixError(f"the hotfix cache holds more than {MAX_RECORDS} records")
        if size - pos < RECORD_HEADER_BYTES:
            raise HotfixError(f"byte {pos}: the hotfix cache ends inside a record header")
        magic, region, push, unique, table_hash, record_id, length, status, _pad = (
            _RECORD.unpack_from(data, pos)
        )
        if magic != MAGIC:
            raise HotfixError(f"byte {pos}: a record does not start with XFTH")
        start = pos + RECORD_HEADER_BYTES
        if length < 0 or length > size - start:
            raise HotfixError(
                f"byte {pos}: a record says it holds {length} bytes; "
                f"{size - start} are left in the file"
            )
        kept = wanted.get(table_hash)
        if kept is not None:
            if len(records) >= MAX_KEPT:
                raise HotfixError(f"the hotfix cache holds more than {MAX_KEPT} rows wowlab reads")
            # Every value is already an int or bytes of the type the model
            # declares: no validation per record (security review of M22-23).
            records.append(
                HotfixRecord.model_construct(
                    table=kept,
                    record_id=record_id,
                    push_id=push,
                    unique_id=unique,
                    region_id=region,
                    status=status,
                    data=data[start : start + length],
                    offset=pos,
                )
            )
        walked += 1
        pos = start + length
    return HotfixCache(version=version, build=build, records_in_file=walked, records=records)


def read(path: Path, tables: Iterable[str], *, root: Path | None = None) -> HotfixCache:
    """`parse` of the file at `path`, opened read-only (from the install
    root `root` one component at a time, when given: no link on the way is
    followed) and closed. Raises `HotfixError`, or `OSError` when the file
    cannot be opened or read (`snapshot.UnsafeReadError` for a link, a
    file that is not regular, or one over `MAX_FILE_BYTES`)."""
    data = snapshot.read_regular_file(path, limit=MAX_FILE_BYTES, root=root)
    return parse(data, tables)


def _real_folder(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)  # lstat: a link is not a folder
    except OSError:
        return False


def find_caches(flavor_path: Path, prefer: str | None = None) -> list[Path]:
    """The `Cache/ADB/<locale>/DBCache.bin` files of a flavor folder: the
    one of the locale `prefer` names first when there is one, then newest
    first (by modification time, then by path). Lists folders only; opens
    nothing. A `Cache`, `ADB` or locale folder or a file that is a link is
    passed over (none is followed), so nothing outside the folder is
    looked at (security review of M22-23)."""
    cache = flavor_path / "Cache"
    adb = cache / "ADB"
    if not (_real_folder(cache) and _real_folder(adb)):
        return []
    found: list[tuple[int, str, Path]] = []
    try:
        with os.scandir(adb) as entries:
            locales = [e for e in entries if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []
    for locale in locales:
        candidate = Path(locale.path) / CACHE_FILE
        try:
            st = candidate.lstat()
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):  # lstat: a link is not regular
            found.append((st.st_mtime_ns, str(candidate), candidate))
    found.sort(key=lambda t: (t[2].parent.name != prefer, -t[0], t[1]))
    return [p for _, _, p in found]


def game_locale(flavor_path: Path, root: Path | None = None) -> str | None:
    """The `textLocale` setting of the flavor folder's `WTF/Config.wtf`
    (the language the game shows its text in), or None when the file or the
    setting is not there or cannot be read. Read-only. Whether the client
    keeps its hotfix cache under that locale's folder is **[verify]**."""
    try:
        doc = wtfconfig.read_config(flavor_path / "WTF" / "Config.wtf", root=root)
    except (OSError, ValueError):
        return None
    cvar = doc.get("textLocale")
    if cvar is None or not _TABLE.fullmatch(cvar.value):
        return None
    return cvar.value


# ─── the layouts ─────────────────────────────────────────────────────────────


class Field(_Model):
    name: str
    kind: Literal["i8", "u8", "i16", "u16", "i32", "u32", "f32", "str"]
    count: int  # 0: not an array


class TableLayout(_Model):
    table: str
    fields: list[Field]

    @property
    def inline_id(self) -> bool:
        """Whether the row's data holds its own ID (the first field)."""
        return self.fields[0].name == "ID"

    @property
    def columns(self) -> list[str]:
        """The columns the table's export has, `ID` first."""
        out = [] if self.inline_id else ["ID"]
        for f in self.fields:
            out.extend(f"{f.name}_{i}" for i in range(f.count)) if f.count else out.append(f.name)
        return out


def parse_layouts(text: str) -> dict[str, TableLayout]:
    """The layouts in `hotfix_layouts.toml`'s text. Raises `LayoutError`."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise LayoutError(f"{LAYOUTS_FILE}: {exc}") from exc
    tables = data.get("tables")
    if set(data) != {"tables"} or not isinstance(tables, dict) or not tables:
        raise LayoutError(f"{LAYOUTS_FILE}: one [tables.<name>] table per table, and nothing else")
    out: dict[str, TableLayout] = {}
    for name, body in tables.items():
        if not _TABLE.fullmatch(name):
            raise LayoutError(f"{LAYOUTS_FILE}: {name!r} is not a table name")
        specs = body.get("fields") if isinstance(body, dict) else None
        if not isinstance(specs, list) or set(body) != {"fields"} or not specs:
            raise LayoutError(f"{LAYOUTS_FILE}: [tables.{name}] has one key, fields, a list")
        if len(specs) > _FIELDS_MAX:
            raise LayoutError(f"{LAYOUTS_FILE}: [tables.{name}] has over {_FIELDS_MAX} fields")
        fields: list[Field] = []
        for spec in specs:
            m = _FIELD.fullmatch(spec) if isinstance(spec, str) else None
            if m is None:
                raise LayoutError(f"{LAYOUTS_FILE}: [tables.{name}]: {spec!r} is not <name>:<type>")
            count = int(m.group(3)) if m.group(3) else 0
            if m.group(3) and not 1 <= count <= _ARRAY_MAX:
                raise LayoutError(f"{LAYOUTS_FILE}: [tables.{name}]: {spec!r}: 1 to {_ARRAY_MAX}")
            fields.append(Field(name=m.group(1), kind=m.group(2), count=count))  # type: ignore[arg-type]
        layout = TableLayout(table=name, fields=fields)
        first = fields[0]
        if any(f.name == "ID" for f in fields[1:]) or (
            first.name == "ID" and (first.kind != "i32" or first.count)
        ):
            raise LayoutError(f"{LAYOUTS_FILE}: [tables.{name}]: an inline ID is first, ID:i32")
        columns = layout.columns
        if len(set(columns)) != len(columns):
            raise LayoutError(f"{LAYOUTS_FILE}: [tables.{name}] names a column twice")
        out[name] = layout
    return out


@functools.cache
def layouts() -> dict[str, TableLayout]:
    """The shipped layouts, by table name."""
    ref = resources.files("wowlab_core").joinpath(LAYOUTS_FILE)
    raw = ref.read_bytes()
    if len(raw) > _LAYOUTS_LIMIT:
        raise LayoutError(f"{LAYOUTS_FILE} holds over {_LAYOUTS_LIMIT} bytes")
    return parse_layouts(raw.decode("utf-8"))


def _float_text(value: float) -> str:
    """A float32 as the table exports write one: up to eleven decimals,
    trailing zeros dropped (0.20000000298, 1)."""
    if not math.isfinite(value):
        return repr(value)
    text = f"{value:.11f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def decode_row(layout: TableLayout, record: HotfixRecord) -> dict[str, str]:
    """A row of the cache as the table's export holds one: each column's
    text by name, `ID` first (the record's row id), and `SOURCE_KEY` last.
    Raises `HotfixError` when the data is not exactly one row of the
    layout: too short, a string without its ending zero byte or not UTF-8,
    or bytes left over."""
    data = record.data
    end = len(data)
    pos = 0
    row: dict[str, str] = {"ID": str(record.record_id)}

    def fail(why: str) -> HotfixError:
        return HotfixError(
            f"byte {record.offset}: the {layout.table} row {record.record_id} {why}; "
            "the hotfix cache's rows do not match the layout wowlab knows"
        )

    for f in layout.fields:
        names = [f"{f.name}_{i}" for i in range(f.count)] if f.count else [f.name]
        for column in names:
            if f.kind == "str":
                stop = data.find(b"\0", pos)
                if stop < 0:
                    raise fail(f"has no end to its {column} text")
                try:
                    row[column] = data[pos:stop].decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise fail(f"has {column} text that is not UTF-8 ({exc.reason})") from None
                pos = stop + 1
                continue
            unpack = _SCALAR[f.kind]
            if end - pos < unpack.size:
                raise fail(f"ends before its {column} value")
            (value,) = unpack.unpack_from(data, pos)
            pos += unpack.size
            if column == "ID" and value != record.record_id:
                raise fail(f"holds the ID {value}")
            row[column] = _float_text(value) if f.kind == "f32" else str(value)
    if pos != end:
        raise fail(f"holds {end - pos} bytes more than one row")
    row[SOURCE_KEY] = SOURCE_HOTFIX
    return row


# ─── what an overlay did ─────────────────────────────────────────────────────


class OverlayTable(_Model):
    """One table's part of an overlay. `added`: rows the cached table did not
    have; `replaced`: rows it had, now the cache's; `changed`: of those, the
    ones whose values differ from the cached table's; `removed`: rows the
    cache removed (status 2 or 3, whose meaning is **[verify]**);
    `left_alone`: records neither a row nor a removal (another status, or a
    row with no data)."""

    table: str
    records: int
    added: int
    replaced: int
    changed: int = 0
    removed: int
    left_alone: int

    @property
    def differs(self) -> bool:
        """Whether the cache changed anything in this table."""
        return bool(self.added or self.changed or self.removed)


class OverlayReport(_Model):
    """What the hotfix cache did to the cached tables of one build.

    `applied` is False when nothing was overlaid; `refused` then says why
    (no cache, a cache of another build, a layout that does not match the
    build's tables, a row that does not decode). `file` is the cache read,
    relative to the flavor folder; `cache_build` the build its header names."""

    file: str | None
    cache_build: int | None
    tables_build: str
    applied: bool
    refused: str | None
    tables: list[OverlayTable]

    @property
    def rows_added(self) -> int:
        return sum(t.added for t in self.tables)

    @property
    def rows_from_cache(self) -> int:
        return sum(t.added + t.replaced for t in self.tables)


def table_names(names: Sequence[str]) -> list[str]:
    """Of `names`, those the shipped layouts describe, in order."""
    known = layouts()
    return [n for n in names if n in known]
