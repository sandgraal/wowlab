"""questiedb: the QuestieDB addon's data, read as data (M22-08).

QuestieDB is the database the Questie addon reads: every quest, NPC, object
and item it knows, with where each one is. The player's own installed copy
is read here, read-only (L1), and nothing in it is executed (L3). Its form,
from the owner's install of QuestieDB 1.1.4 on 2026-11-23
(`docs/LAB_FORMATS.md` §14):

- **The baked store.** The addon's TOC carries the data as `## X-...`
  directives, which the client hands to the addon through
  `C_AddOns.GetAddOnMetadata`, and the addon decodes with the client's own
  `C_EncodingUtil`; no Lua is compiled for it. A directive whose value is
  exactly `~N~` is a chunk header: the value is directives `<key>-1` to
  `<key>-N` joined. `X-<Kind>-IDS` (Kind: `Item`, `Npc`, `Quest`,
  `Object`) is base64 of a zlib stream of a CBOR array of ids.
  `X-<Kind>-<id>-S` is base64 of a CBOR row of that entity's scalar fields,
  keyed by field number (a CBOR map, or an array when the numbers run 1..n),
  with `p`, the presence mask: bit `n - 1` set when table field `n` is
  stored. `X-<Kind>-<id>-<n>` is base64 of CBOR, table field `n`. Strings
  are CBOR byte strings. The field numbers are the addon's own
  (`src/meta/<kind>Meta.lua`); the ones read here are named below.
- **The drop tables.** Files the TOC lists under a `DropTables` folder hold
  drop chances as Lua long strings of the form `Module.field = [[return
  {...}]]`, which Questie compiles with `loadstring` in game. Only that
  shape is taken: the text between `return` and the closing bracket is a
  table constructor and is parsed by `luadata`, which accepts literals only
  and rejects everything else with a position.
- **The drop corrections.** A drop file with no such string is Questie's
  corrections, `Module.Section = { [item] = { [npc] = value } }` per game
  version. Under the conductor's ruling of 2026-11-23 (L3), a narrow token
  recognizer reads them: `DropKeys.WOWHEAD` and `DropKeys.PSERVER` (also
  spelled `DropDB.correctionKeys.…`), Questie's pointers at one table's
  figure, become -1 and -2, the numbers Questie gives them, and the rest
  must be a literal for `luadata`. Nothing is evaluated; a section holding
  anything else is left out by name. Only the first section is used, the
  one Questie applies on every game version; later ones are listed as left
  out.
- **QuestieDB's own zone list.** `constants.zoneIDs = { NAME = number }` in
  a file the TOC lists under an `enum` folder, read with `luadata` when it
  is a literal. It names the areas QuestieDB numbers itself (the wings of an
  instance), which the game's zone table does not have.

What is not read, and why, is listed (`left_out`): the corrections Questie
picks by the character's faction and class (Lua functions), the names in
other languages (`X-l10n-...`, which this reader does not need: the stored
names are English), the drop corrections of later game versions, and any
drop file or section that is not data.

CBOR (RFC 8949) is decoded by a narrow reader of the subset the store uses:
integers, byte and text strings, arrays and maps of definite length, false,
true, null and floats. A tag, an indefinite length, `undefined`, a simple
value or a reserved code is refused with its offset, as are a length longer
than the bytes left, nesting deeper than `MAX_CBOR_DEPTH`, a map key that is
not an integer or a string, a key given twice, and bytes after the value.
One row that cannot be read is counted and skipped; the rest are read.
"""

from __future__ import annotations

import base64
import binascii
import re
import struct
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import longpath, luadata, toc
from wowlab_core.errors import WowlabError

__all__ = [
    "ADDON_FOLDER",
    "KINDS",
    "MAX_CBOR_DEPTH",
    "MAX_IDS",
    "MAX_IDS_BYTES",
    "MAX_STORE_BYTES",
    "MAX_SUPPORT_FILE_BYTES",
    "REFERENCE_PSERVER",
    "REFERENCE_WOWHEAD",
    "CborError",
    "DropCorrections",
    "DropData",
    "DropTable",
    "Kind",
    "LeftOut",
    "QuestieDBError",
    "QuestieStore",
    "StoreInfo",
    "decode_cbor",
    "find_toc",
    "open_store",
    "read_drop_tables",
    "read_zone_names",
]

ADDON_FOLDER = "QuestieDB"
"""The addon's folder name under `Interface/AddOns/`, matched with ASCII case
folded. An addon's name, not anything about a flavor (L6)."""

Kind = Literal["Item", "Npc", "Quest", "Object"]
KINDS: tuple[Kind, ...] = ("Item", "Npc", "Quest", "Object")

MAX_STORE_BYTES = 256 * 1024 * 1024
"""The largest TOC read as a store. The owner's is 18.8 MB."""
MAX_SUPPORT_FILE_BYTES = 32 * 1024 * 1024
"""The largest drop-table file read. The owner's is 572 KB."""
MAX_IDS_BYTES = 4 * 1024 * 1024
"""The most bytes one id header may inflate to. The owner's largest inflates
to about 120 KB (23,978 ids); this is over 30 times that."""
MAX_IDS = 1_000_000
"""The most ids one header may declare. The owner's largest has 23,978."""
MAX_CBOR_DEPTH = 32
"""The deepest CBOR nesting accepted. The store's deepest is 5."""
_MAX_CHUNKS = 65536

_CHUNK_HEADER = re.compile(r"~([0-9]{1,9})~")
_ROW_KEY = re.compile(r"X-([A-Za-z]+)-([0-9]+)-(S|[0-9]+)")
# `Module.field = [[return` at the start of a line, with any level of `=`.
_LONG_RETURN = re.compile(
    rb"(?m)^[ \t]*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*"
    rb"\[(=*)\[[ \t\r\n]*return(?=[ \t\r\n{])"
)


class QuestieDBError(WowlabError):
    """QuestieDB's data could not be read."""


class CborError(QuestieDBError):
    """A CBOR value outside the subset the store uses, or malformed."""

    def __init__(self, offset: int, message: str) -> None:
        super().__init__(f"CBOR at byte {offset}: {message}")
        self.offset = offset


# ─── CBOR ────────────────────────────────────────────────────────────────────


class _Cbor:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def _take(self, n: int) -> bytes:
        end = self.pos + n
        if end > len(self.data):
            raise CborError(self.pos, f"needs {n} bytes, {len(self.data) - self.pos} left")
        out = self.data[self.pos : end]
        self.pos = end
        return out

    def _argument(self, info: int, at: int) -> int:
        if info < 24:
            return info
        if info == 24:
            return self._take(1)[0]
        if info == 25:
            return int(struct.unpack(">H", self._take(2))[0])
        if info == 26:
            return int(struct.unpack(">I", self._take(4))[0])
        if info == 27:
            return int(struct.unpack(">Q", self._take(8))[0])
        if info == 31:
            raise CborError(at, "an indefinite length is not read")
        raise CborError(at, f"reserved additional information {info}")

    def _count(self, n: int, per: int, at: int) -> int:
        # Each item takes at least one byte: a count the bytes left cannot
        # hold is refused before anything is allocated for it.
        if n * per > len(self.data) - self.pos:
            raise CborError(at, f"declares {n} items with {len(self.data) - self.pos} bytes left")
        return n

    def value(self, depth: int) -> object:
        at = self.pos
        if depth > MAX_CBOR_DEPTH:
            raise CborError(at, f"nested deeper than {MAX_CBOR_DEPTH}")
        initial = self._take(1)[0]
        major, info = initial >> 5, initial & 31
        if major == 7:
            return self._simple(info, at)
        n = self._argument(info, at)
        if major == 0:
            return n
        if major == 1:
            return -1 - n
        if major == 2:
            return self._take(n)
        if major == 3:
            raw = self._take(n)
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                raise CborError(at, "a text string that is not UTF-8") from None
        if major == 4:
            return [self.value(depth + 1) for _ in range(self._count(n, 1, at))]
        if major == 5:
            out: dict[object, object] = {}
            for _ in range(self._count(n, 2, at)):
                key_at = self.pos
                key = self.value(depth + 1)
                if type(key) not in (int, bytes, str):
                    raise CborError(key_at, "a map key that is not an integer or a string")
                if key in out:
                    raise CborError(key_at, "a map key given twice")
                out[key] = self.value(depth + 1)
            return out
        raise CborError(at, "a tag is not read")

    def _simple(self, info: int, at: int) -> object:
        if info == 20:
            return False
        if info == 21:
            return True
        if info == 22:
            return None
        if info == 25:
            return float(struct.unpack(">e", self._take(2))[0])
        if info == 26:
            return float(struct.unpack(">f", self._take(4))[0])
        if info == 27:
            return float(struct.unpack(">d", self._take(8))[0])
        if info == 23:
            raise CborError(at, "undefined is not read")
        raise CborError(at, f"simple value {info} is not read")


def decode_cbor(data: bytes) -> object:
    """One CBOR value, the whole of `data`: `int`, `float`, `bytes`, `str`,
    `list`, `dict`, `bool` or None. Raises `CborError` outside the subset
    (module docstring)."""
    reader = _Cbor(data)
    out = reader.value(0)
    if reader.pos != len(data):
        raise CborError(reader.pos, f"{len(data) - reader.pos} bytes after the value")
    return out


# ─── the store ───────────────────────────────────────────────────────────────


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StoreInfo(_Model):
    """Which QuestieDB was read: the TOC file, and what its own directives
    say (`## Version`, `## X-Flavor`, `## X-Mode`, `## X-BUILD-TIME`,
    `## X-BUILD-COMMIT`), each as written or None."""

    folder: str
    toc: str
    version: str | None = None
    flavor: str | None = None
    mode: str | None = None
    built: str | None = None
    commit: str | None = None


class LeftOut(_Model):
    """A part of the addon that is not read, in a player's words, and why."""

    part: str
    why: str


class DropTable(_Model):
    """One drop-chance table: `name` is the Lua field it is assigned to
    (`wowheadData`, `cmangosData`), `file` the TOC's path of its file, and
    `chances` item id -> NPC id -> percent."""

    name: str
    file: str
    chances: dict[int, dict[int, float]]


class DropCorrections(_Model):
    """Questie's own drop corrections, the section it applies on every game
    version (the first in its file): item id -> NPC id -> a percent, or
    `REFERENCE_WOWHEAD` (-1) / `REFERENCE_PSERVER` (-2) for "use that
    table's figure". `name` is the section's Lua field, `file` its file."""

    name: str
    file: str
    chances: dict[int, dict[int, float]]


class DropData(_Model):
    """What the drop files gave: the literal tables, the corrections, and
    what was left out."""

    tables: list[DropTable]
    corrections: DropCorrections | None = None
    left_out: list[LeftOut] = []


def _fold(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii").lower()


def find_toc(addons_dir: Path) -> tuple[Path | None, list[Path]]:
    """The QuestieDB addon folder's TOCs under `addons_dir`
    (`Interface/AddOns`): `(folder, tocs)`, the TOCs named after the folder
    (`QuestieDB.toc`, `QuestieDB_<suffix>.toc`, `QuestieDB-<suffix>.toc`) in
    name order. `(None, [])` when no such folder is there. Reads names only."""
    try:
        entries = sorted(addons_dir.iterdir())
    except OSError:
        return None, []
    wanted = _fold(ADDON_FOLDER)
    folders = [e for e in entries if _fold(e.name) == wanted and e.is_dir()]
    if not folders:
        return None, []
    folder = folders[0]
    stem = _fold(folder.name)
    tocs: list[Path] = []
    for entry in sorted(folder.iterdir()):
        name = _fold(entry.name)
        if not name.endswith(".toc") or not entry.is_file():
            continue
        base = name[: -len(".toc")]
        if base == stem or (base.startswith(stem) and base[len(stem) : len(stem) + 1] in "_-"):
            if base != stem and len(base) == len(stem) + 1:
                continue
            tocs.append(entry)
    return folder, tocs


class QuestieStore:
    """The baked store of one QuestieDB TOC, as directive key -> value (the
    first of a key given twice, as `toc.TocDocument.get` takes it), and the
    files the TOC lists. Decoding is on demand; nothing is cached but the
    id lists."""

    def __init__(self, path: Path, document: toc.TocDocument) -> None:
        self.path = path
        self.meta: dict[str, str] = {}
        for directive in document.directives:
            self.meta.setdefault(directive.key, directive.value)
        self.files: tuple[str, ...] = tuple(f.path for f in document.files)
        self._ids: dict[str, list[int]] = {}
        self.unreadable: dict[str, int] = {}

    @property
    def baked(self) -> bool:
        """Whether every kind's id header is here (the store's own shape)."""
        return all(f"X-{kind}-IDS" in self.meta for kind in KINDS)

    def info(self) -> StoreInfo:
        get = self.meta.get
        return StoreInfo(
            folder=self.path.parent.name,
            toc=self.path.name,
            version=get("Version"),
            flavor=get("X-Flavor"),
            mode=get("X-Mode"),
            built=get("X-BUILD-TIME"),
            commit=get("X-BUILD-COMMIT"),
        )

    def stored(self, key: str) -> str | None:
        """The value of `key`, its chunks joined when it is a chunk header.
        Raises `QuestieDBError` when a declared chunk is missing."""
        value = self.meta.get(key)
        if value is None or not value.startswith("~"):
            return value
        header = _CHUNK_HEADER.fullmatch(value)
        if header is None:
            return value
        count = int(header.group(1))
        if not 1 <= count <= _MAX_CHUNKS:
            raise QuestieDBError(f"{key} declares {count} parts")
        parts: list[str] = []
        for i in range(1, count + 1):
            part = self.meta.get(f"{key}-{i}")
            if part is None:
                raise QuestieDBError(f"{key} declares {count} parts but part {i} is missing")
            parts.append(part)
        return "".join(parts)

    def _decoded(self, key: str) -> object:
        text = self.stored(key)
        if text is None:
            return None
        try:
            raw = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            raise QuestieDBError(f"{key} is not base64") from None
        return decode_cbor(raw)

    def ids(self, kind: Kind) -> list[int]:
        """Every id of `kind`, in the header's order. Raises `QuestieDBError`
        when the header is missing or malformed."""
        if kind in self._ids:
            return self._ids[kind]
        key = f"X-{kind}-IDS"
        text = self.stored(key)
        if text is None:
            raise QuestieDBError(f"{key} is missing")
        try:
            packed = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            raise QuestieDBError(f"{key} is not base64") from None
        inflate = zlib.decompressobj()
        try:
            raw = inflate.decompress(packed, MAX_IDS_BYTES)
        except zlib.error as exc:
            raise QuestieDBError(f"{key} is not a zlib stream: {exc}") from None
        if inflate.unconsumed_tail:
            raise QuestieDBError(f"{key} inflates past {MAX_IDS_BYTES} bytes, so it is not read")
        if not inflate.eof:
            raise QuestieDBError(f"{key} is cut short")
        # The array's declared length is checked before any id is built.
        head = _Cbor(raw)
        if raw and raw[0] >> 5 == 4:
            declared = head._argument(head._take(1)[0] & 31, 0)
            if declared > MAX_IDS:
                raise QuestieDBError(f"{key} declares {declared} ids, more than {MAX_IDS}")
        value = decode_cbor(raw)
        if not isinstance(value, list) or not all(type(v) is int and v > 0 for v in value):
            raise QuestieDBError(f"{key} is not a list of ids")
        ids: list[int] = list(value)
        self._ids[kind] = ids
        return ids

    def row(self, kind: Kind, entity: int) -> dict[object, object] | None:
        """The scalar row of one entity, keyed by field number (and `b"p"`,
        the presence mask), or None when it has none or it cannot be read
        (counted in `unreadable`)."""
        try:
            value = self._decoded(f"X-{kind}-{entity}-S")
        except QuestieDBError:
            self.unreadable[kind] = self.unreadable.get(kind, 0) + 1
            return None
        if value is None:
            return None
        if isinstance(value, list):
            return {i: v for i, v in enumerate(value, start=1) if v is not None}
        if isinstance(value, dict):
            return value
        self.unreadable[kind] = self.unreadable.get(kind, 0) + 1
        return None

    def table(self, kind: Kind, entity: int, field: int, row: dict[object, object]) -> object:
        """Table field `field` of one entity, when its row's presence mask
        says it is stored; None otherwise or when it cannot be read (counted
        in `unreadable`)."""
        mask = row.get(b"p")
        if type(mask) is not int or not (mask >> (field - 1)) & 1:
            return None
        try:
            return self._decoded(f"X-{kind}-{entity}-{field}")
        except QuestieDBError:
            self.unreadable[kind] = self.unreadable.get(kind, 0) + 1
            return None

    def names(self, kind: Kind) -> Iterator[tuple[int, str, dict[object, object]]]:
        """`(id, name, row)` for every entity of `kind` with a readable row
        and a name (field 1), in id-header order."""
        for entity in self.ids(kind):
            row = self.row(kind, entity)
            if row is None:
                continue
            name = row.get(1)
            if isinstance(name, bytes):
                yield entity, name.decode("utf-8", "replace"), row
            elif isinstance(name, str):
                yield entity, name, row

    def left_out(self) -> list[LeftOut]:
        """The parts of this addon the store reader leaves out, read from
        what the TOC lists and holds."""
        out: list[LeftOut] = []
        folded = [_fold(f).replace("/", "\\") for f in self.files]
        if any("\\corrections\\" in f or f.startswith("corrections\\") for f in folded):
            out.append(
                LeftOut(
                    part="the corrections Questie picks by the character's faction and class",
                    why="they are Lua code, which wowlab never runs (the ones for every "
                    "character are already in the data it reads), so a quest given on "
                    "both sides lists both factions' givers, and some quests show no zone",
                )
            )
        if any(k.startswith("X-l10n-") and k != "X-l10n-Version" for k in self.meta):
            out.append(
                LeftOut(
                    part="names in other languages",
                    why="only the English names the data holds are searched",
                )
            )
        return out


def open_store(path: Path) -> QuestieStore:
    """Read one QuestieDB TOC, read-only (L1). Raises `QuestieDBError` when
    it cannot be read or is larger than `MAX_STORE_BYTES`."""
    try:
        document = toc.read_toc(path, max_bytes=MAX_STORE_BYTES)
    except toc.TocTooLargeError:
        raise QuestieDBError(f"{path.name} is larger than {MAX_STORE_BYTES} bytes") from None
    except OSError as exc:
        raise QuestieDBError(f"{path.name} could not be read: {exc.strerror or exc}") from None
    return QuestieStore(path, document)


# ─── the drop tables ─────────────────────────────────────────────────────────


def _close_bracket(data: bytes, start: int, level: bytes) -> int:
    end = data.find(b"]" + level + b"]", start)
    if end < 0:
        raise QuestieDBError("a long string is not closed")
    return end


def _keyed(value: object) -> object:
    """A table `luadata` gave as a list (its keys ran 1..n) as the dict of
    those keys, so an item or NPC id 1 reads like any other."""
    return dict(enumerate(value, start=1)) if isinstance(value, list) else value


def _chances(value: object) -> dict[int, dict[int, float]] | None:
    value = _keyed(value)
    if not isinstance(value, dict):
        return None
    out: dict[int, dict[int, float]] = {}
    for item, by_npc in value.items():
        by_npc = _keyed(by_npc)
        if type(item) is not int or not isinstance(by_npc, dict):
            return None
        row: dict[int, float] = {}
        for npc, chance in by_npc.items():
            if type(npc) is not int or type(chance) not in (int, float):
                return None
            row[npc] = float(chance)
        out[item] = row
    return out


def _literal_tables(data: bytes, file: str) -> tuple[list[DropTable], list[str]]:
    """The `Module.field = [[return {...}]]` tables of one file, and the
    fields of that shape whose text is not one literal table."""
    tables: list[DropTable] = []
    refused: list[str] = []
    for match in _LONG_RETURN.finditer(data):
        field = match.group(2).decode("ascii")
        try:
            end = _close_bracket(data, match.end(), match.group(3))
        except QuestieDBError:
            refused.append(field)
            continue
        body = data[match.end() : end]
        try:
            # The constructor alone, as the value of one assignment: `luadata`
            # takes literals only and refuses anything else (L3).
            value = luadata.parse(b"t =" + body).to_python().get("t")
        except luadata.LuaDataError:
            refused.append(field)
            continue
        chances = _chances(value)
        if chances is None:
            refused.append(field)
            continue
        tables.append(DropTable(name=field, file=file, chances=chances))
    return tables, refused


def _listed_files(store: QuestieStore, folder: str) -> Iterator[tuple[str, Path]]:
    """`(as listed, path)` of each `.lua` file the TOC lists inside a folder
    named `folder` (ASCII case folded); a listed path that climbs (`..`) is
    never read."""
    base = store.path.parent
    for listed in store.files:
        parts = [p for p in re.split(r"[\\/]", listed) if p]
        if not parts or not any(_fold(p) == folder for p in parts[:-1]):
            continue
        if any(p in (".", "..") for p in parts) or not parts[-1].lower().endswith(".lua"):
            continue
        yield listed, base.joinpath(*parts)


def _read_listed(path: Path) -> bytes | None:
    try:
        with longpath.open_read(path) as handle:
            data = handle.read(MAX_SUPPORT_FILE_BYTES + 1)
    except OSError:
        return None
    return data if len(data) <= MAX_SUPPORT_FILE_BYTES else None


def _constructor_end(data: bytes, start: int) -> int:
    """The index after the `}` that closes the table constructor opening at
    `data[start]` (`{`), skipping quoted strings and line comments; -1 when
    it is not closed, or holds a long bracket (`[[`, `--[[`), which this
    scanner does not follow. It only finds the end; `luadata` reads what is
    inside."""
    depth = 0
    i = start
    n = len(data)
    while i < n:
        c = data[i]
        if c == 0x7B:  # {
            depth += 1
        elif c == 0x7D:  # }
            depth -= 1
            if depth == 0:
                return i + 1
        elif c in (0x22, 0x27):  # a quoted string
            quote = c
            i += 1
            while i < n and data[i] != quote:
                if data[i] in (0x0A, 0x0D):
                    return -1
                i += 2 if data[i] == 0x5C else 1
        elif data.startswith(b"--", i):
            if re.match(rb"--\[=*\[", data[i : i + 64]):
                return -1
            end = data.find(b"\n", i)
            if end < 0:
                return -1
            i = end
        elif re.match(rb"\[=*\[", data[i : i + 64]):
            return -1
        i += 1
    return -1


def _assigned_constructors(data: bytes, field: bytes | None = None) -> Iterator[tuple[str, bytes]]:
    """`(field, constructor bytes)` of each `Module.field = {` at the start of
    a line (only `field` when given), in file order; a constructor that
    cannot be delimited is given as empty bytes.

    Linear in the file: a match inside the constructor just read is part of
    it and is passed over, and after the first constructor that does not
    close (its scan reached the end of the file) every later match is given
    as empty bytes without another scan, so a file of unclosed lines costs
    one pass, not one per line."""
    name = re.escape(field) if field is not None else rb"[A-Za-z_][A-Za-z0-9_]*"
    pattern = re.compile(rb"(?m)^[ \t]*[A-Za-z_][A-Za-z0-9_]*\.(" + name + rb")[ \t]*=[ \t]*\{")
    after = 0
    unclosed = False
    for match in pattern.finditer(data):
        found = match.group(1).decode("ascii")
        if unclosed:
            yield found, b""
            continue
        if match.start() < after:
            continue
        start = match.end() - 1
        end = _constructor_end(data, start)
        if end < 0:
            unclosed = True
            yield found, b""
            continue
        after = end
        yield found, data[start:end]


# A drop correction that points at a table instead of giving a figure: the two
# names Questie's own `DropDB.correctionKeys` defines, recognised as tokens,
# never evaluated (the conductor's ruling of 2026-11-23 under L3). Their values
# in Questie are negative (-1, -2), so they cannot be mistaken for a figure.
_REFERENCE = re.compile(rb"=([ \t]*)(?:DropDB\.correctionKeys|DropKeys)\.(WOWHEAD|PSERVER)\b")
_REFERENCE_VALUES = {b"WOWHEAD": b"-1", b"PSERVER": b"-2"}
REFERENCE_WOWHEAD = -1.0
REFERENCE_PSERVER = -2.0


def _corrections(body: bytes) -> dict[int, dict[int, float]] | None:
    """One correction section read as data: each reference token becomes its
    negative number and the rest must be a literal (`luadata`); None when it
    is not one."""
    literal = _REFERENCE.sub(lambda m: b"=" + m.group(1) + _REFERENCE_VALUES[m.group(2)], body)
    try:
        value = luadata.parse(b"t =" + literal).to_python().get("t")
    except luadata.LuaDataError:
        return None
    return _chances(value)


def read_drop_tables(store: QuestieStore) -> DropData:
    """The drop chances of the files the store's TOC lists under a
    `DropTables` folder: the literal tables, the corrections of the first
    correction section (the one Questie applies on every game version), and
    what was left out of them."""
    tables: list[DropTable] = []
    corrections: DropCorrections | None = None
    left: list[LeftOut] = []
    for listed, path in _listed_files(store, "droptables"):
        data = _read_listed(path)
        if data is None:
            left.append(LeftOut(part=f"the drop file {listed}", why="it could not be read"))
            continue
        found, refused = _literal_tables(data, listed)
        tables.extend(found)
        for field in refused:
            left.append(
                LeftOut(
                    part=f"the drop table {field} in {listed}",
                    why="its text is not plain data, so reading it would mean running it",
                )
            )
        if found or refused:
            continue
        sections = list(_assigned_constructors(data))
        read_any = False
        for section, body in sections:
            values = _corrections(body) if body else None
            if values is None:
                left.append(
                    LeftOut(
                        part=f"the drop corrections {section} in {listed}",
                        why="they hold more than figures and table references, so reading "
                        "them would mean running them",
                    )
                )
                continue
            if corrections is None:
                corrections = DropCorrections(name=section, file=listed, chances=values)
                read_any = True
            elif values:
                left.append(
                    LeftOut(
                        part=f"the drop corrections {section} in {listed}",
                        why="Questie applies them only on later game versions",
                    )
                )
        if not sections and not read_any:
            left.append(
                LeftOut(
                    part=f"the drop file {listed}", why="it is Lua code, which wowlab never runs"
                )
            )
    return DropData(tables=tables, corrections=corrections, left_out=left)


def _area_words(name: str) -> str:
    """`DIRE_MAUL_GORDOK_COMMONS` as `Dire Maul Gordok Commons`; a word with
    a digit (`AQ40`) stays as written."""
    return " ".join(w if any(c.isdigit() for c in w) else w.capitalize() for w in name.split("_"))


def read_zone_names(store: QuestieStore) -> tuple[dict[int, str], list[LeftOut]]:
    """QuestieDB's own names of its zone ids, from the `zoneIDs` table of a
    file the TOC lists under an `enum` folder, when that table is a literal
    (`NAME = number`): id -> the name in words (the first name of an id).
    It numbers some areas itself, such as the wings of an instance, with ids
    the game's zone table does not have."""
    out: dict[int, str] = {}
    left: list[LeftOut] = []
    for listed, path in _listed_files(store, "enum"):
        if _fold(path.name) != "zones.lua":
            continue
        data = _read_listed(path)
        if data is None:
            left.append(
                LeftOut(part=f"QuestieDB's own zone names in {listed}", why="it could not be read")
            )
            continue
        bodies = list(_assigned_constructors(data, b"zoneIDs"))
        if not bodies:
            left.append(
                LeftOut(
                    part=f"QuestieDB's own zone names in {listed}",
                    why="it holds no zone list wowlab reads",
                )
            )
        for _, body in bodies:
            try:
                value = luadata.parse(b"t =" + body).to_python().get("t") if body else None
            except luadata.LuaDataError:
                value = None
            if not isinstance(value, dict):
                left.append(
                    LeftOut(
                        part=f"QuestieDB's own zone names in {listed}",
                        why="the table is not plain data",
                    )
                )
                continue
            for name, number in value.items():
                if isinstance(name, str) and type(number) is int and number not in out:
                    out[number] = _area_words(name)
    return out, left
