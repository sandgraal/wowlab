"""db2lake: one build's cached game tables, typed, in SQLite.

Spec: docs/LAB_PLAN.md §14.2. Decision: ADR-0028. The tables come from
``gamedata`` (ADR-0022), which stays the only network client.

The rules this module exists to hold:

- **One file per full build string**, ``<user data>/wowlab/db2lake/<build>.sqlite``.
  It is derived from the CSV tables ``gamedata`` has cached: deleting it loses
  nothing, and loading the same tables again rebuilds the same content. The
  file records its build (``_lake_meta``); a file that names another build is
  refused, so a copied or renamed file cannot answer for the wrong build. A
  file that holds anything db2lake does not write (another database, a view, a
  trigger) is refused and never written into.
- **L1.** The lake directory, and the database file with every symlink
  resolved, are refused when they or a folder above them hold ``.build.info``
  or ``.flavor.info``: at construction, before and after the directory is
  created, and before every connection. SQLite writes the file and its
  journal in place, so a file or journal with a second hard link (or a
  journal that is a symlink) is refused too. Another build's file is attached
  read-only (``mode=ro``). The connection's authorizer refuses every ATTACH,
  DETACH (and so ``VACUUM INTO``) and PRAGMA but the module's own, so no
  statement run through the lake opens another file.
- **L5.** A table is loaded once, on demand, in one transaction, from the CSV
  ``GameData.table`` returns. ``_lake_tables`` records its name, the SHA-256
  of the bytes that were loaded and the row count. A loaded table is never
  replaced: a second load is a no-op when the cached CSV still has that
  SHA-256, and ``SourceChanged`` (citing L5) when it does not. A CSV whose
  bytes differ from the SHA-256 its fetch record (the sidecar) gives is not
  loaded at all.
- **Types** follow ADR-0028's rule, column by column over the whole table: a
  column whose every non-empty cell is a canonical decimal integer (an
  optional minus, no leading zero except for ``0``, within signed 64 bits) is
  INTEGER; one whose every non-empty cell is another finite decimal number is
  REAL; every other column is TEXT, including one with no non-empty cell (the
  owner's ruling of 2026-09-30). The details the code settles are in the
  §14.2 amendment of 2026-09-30. An empty cell is NULL in an INTEGER or REAL
  column and the empty string in a TEXT column. Tables are ``STRICT``, so a
  value of the wrong type is an error, never a silently mixed column.
- **Names** are wago's, unchanged. A header the lake cannot hold (a repeated
  name, one repeated when ASCII case is ignored as SQLite ignores it, a NUL,
  more columns than SQLite allows) is ``gamedata.MalformedTable``, as is a
  record longer than ``MAX_RECORD_BYTES``.

Trust boundary. The CSV comes from the network through ``gamedata``, which
checks the response before caching it; its header and cells are still data
from outside. A table name comes from the caller and is data too: it must
pass ``gamedata``'s key rule (``GameData.table_path``) before anything else
looks at it. Every identifier that reaches SQL (table and column names, the
attach alias) is double-quoted with embedded quotes doubled, and one holding
a NUL is refused; every value (cells, hashes, names in ``_lake_tables``, the
attached file's URI) is a bound parameter, never text in a statement. A lake
file is treated as possibly planted by someone else: triggers and views are
switched off on the connection, the schema is untrusted and SQLite's
defensive mode is on. ``Lake.query`` runs SQL the *library* writes, under
the authorizer, ``query_only`` and a deadline; it is not the surface for SQL
typed by the owner (that is ``wowlab db2 sql``, M12-04, with its own guards).

Reading is two streaming passes over the CSV (the first infers the types and
hashes the bytes, the second inserts in batches and hashes them again), so
memory is bounded by a batch (rows, cells and bytes) and by one record, not
by the table. A file that changes between the passes is refused and nothing
is written. Every statement runs under a progress handler, so Ctrl-C stops
it (``KeyboardInterrupt``) and ``query`` can stop at a deadline or an
operation budget (``QueryStopped``).
"""

from __future__ import annotations

import contextlib
import csv
import errno
import hashlib
import math
import os
import re
import sqlite3
import stat
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Final, Literal, NamedTuple, cast

import platformdirs
from pydantic import BaseModel, ConfigDict, ValidationError

from wowlab_core.gamedata import GameData, MalformedTable
from wowlab_core.install import BUILD_INFO, FLAVOR_INFO

if TYPE_CHECKING:
    from hashlib import _Hash

__all__ = [
    "BATCH_ROWS",
    "LAKE_FORMAT",
    "MAX_RECORD_BYTES",
    "QUERY_DEADLINE_SECONDS",
    "Cell",
    "Column",
    "ColumnType",
    "Lake",
    "LakeError",
    "LakeLocationError",
    "LoadResult",
    "LoadedTable",
    "QueryStopped",
    "SourceChanged",
    "TableSchema",
    "attach_alias",
    "default_lake_dir",
]

# Rows per executemany at most. A batch is also cut at _BATCH_CELLS cells
# (so a wide table takes fewer rows) and at _BATCH_BYTES bytes of CSV (so long
# text does too). SQLite's own page cache (default 2 MB) spills to the file
# inside the transaction, so neither grows with the table.
BATCH_ROWS: Final = 5000
_BATCH_CELLS: Final = 50_000
_BATCH_BYTES: Final = 4 << 20
# One CSV record, all its physical lines together. Refused before it is
# decoded. The longest record in the recordings is under 2 KB; wago's widest
# tables hold a few hundred columns.
MAX_RECORD_BYTES: Final = 1 << 20
LAKE_FORMAT: Final = 1
# STRICT tables arrived in SQLite 3.37.0 (2021-11-27).
_MIN_SQLITE: Final = (3, 37, 0)
# How long a load waits for another process that holds the lake's write lock.
_BUSY_TIMEOUT_SECONDS: Final = 30.0
# The progress handler runs every this many SQLite virtual-machine steps: often
# enough that Ctrl-C and a deadline act within milliseconds, rarely enough to
# cost nothing measurable.
_PROGRESS_STEPS: Final = 10_000
QUERY_DEADLINE_SECONDS: Final = 300.0

ColumnType = Literal["INTEGER", "REAL", "TEXT"]
Cell = int | float | str | None

_BUILD_STRING = re.compile(r"[0-9]+(?:\.[0-9]+){3}")
# ADR-0028: an optional minus, no leading zero except for `0`. ASCII digits
# only; `int()` and `float()` would also take "+7", " 7", "7_0" and non-ASCII
# digits such as ARABIC-INDIC DIGIT SEVEN.
_INTEGER_TEXT = re.compile(r"-?(?:0|[1-9][0-9]*)")
# "Another finite decimal number": the same integer part, then an optional
# fraction. Decimal notation only (wago writes fixed-point, up to 11 decimals,
# in every recording), so `007`, `.5`, `5.`, `+5`, `1e5`, `inf` are text.
_DECIMAL_TEXT = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")
_I64_MIN: Final = -(2**63)
_I64_MAX: Final = 2**63 - 1
# 19 characters is the shortest text that can overflow ("9223372036854775808");
# 20 is the longest that cannot ("-9223372036854775808").
_ALWAYS_FITS: Final = 19
_LONGEST_I64: Final = 20

# Column states while inferring, in the order a column can only move along.
_EMPTY, _INTEGER, _REAL, _TEXT = 0, 1, 2, 3
# A column with no non-empty cell keeps its empty strings: TEXT (the owner's
# ruling of 2026-09-30 on ADR-0028, whose words read literally gave INTEGER).
_ALL_EMPTY: Final[ColumnType] = "TEXT"
_TYPE_NAMES: Final[dict[int, ColumnType]] = {_INTEGER: "INTEGER", _REAL: "REAL", _TEXT: "TEXT"}

_META = "_lake_meta"
_TABLES = "_lake_tables"
# The exact statements, as SQLite keeps them in sqlite_master: a file whose
# metadata tables were made any other way is not a lake this module wrote.
_META_SQL = f"CREATE TABLE {_META} (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL) STRICT"
_TABLES_SQL = (
    f"CREATE TABLE {_TABLES} (name TEXT NOT NULL COLLATE NOCASE PRIMARY KEY, "
    "sha256 TEXT NOT NULL, row_count INTEGER NOT NULL) STRICT"
)
_LAKE_OBJECTS: Final = frozenset(
    {
        ("table", _META, _META, _META_SQL),
        ("index", f"sqlite_autoindex_{_META}_1", _META, None),
        ("table", _TABLES, _TABLES, _TABLES_SQL),
        ("index", f"sqlite_autoindex_{_TABLES}_1", _TABLES, None),
    }
)
_RESERVED_PREFIXES = ("_lake_", "sqlite_")
# The names SQLite gives a rowid table's row id; a column may shadow any of them.
_ROWID_NAMES = ("rowid", "_rowid_", "oid")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
# The files SQLite keeps beside a database and writes in place.
_COMPANIONS = ("-journal", "-wal", "-shm")
# A lake file may have been planted: no triggers, no views, an untrusted
# schema, defensive mode, and no two-argument fts3_tokenizer().
_DBCONFIG: Final = (
    (sqlite3.SQLITE_DBCONFIG_ENABLE_TRIGGER, False),
    (sqlite3.SQLITE_DBCONFIG_ENABLE_VIEW, False),
    (sqlite3.SQLITE_DBCONFIG_TRUSTED_SCHEMA, False),
    (sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True),
    (sqlite3.SQLITE_DBCONFIG_ENABLE_FTS3_TOKENIZER, False),
)
# PRAGMAs any statement may use (read-only introspection). Everything else,
# `query_only` included, only when the module issues it.
_OPEN_PRAGMAS = frozenset({"table_info"})
# ERROR_CANT_RESOLVE_FILENAME: Windows' word for a symlink loop.
_CANT_RESOLVE_FILENAME = 1921
_CHUNK = 1 << 16


# ─── errors ──────────────────────────────────────────────────────────────────


class LakeError(Exception):
    """Base class for everything this module raises on purpose. A table the
    lake cannot hold is ``gamedata.MalformedTable``, as for ``GameData.rows``."""


class LakeLocationError(LakeError):
    """The lake directory or file is somewhere it must never be (L1), or is a
    file SQLite would write through into somewhere else (a hard link)."""


class QueryStopped(LakeError):  # noqa: N818 - says what happened, like gamedata's errors
    """``Lake.query`` stopped a statement at its deadline or operation budget."""


class SourceChanged(LakeError):  # noqa: N818
    """The cached CSV is not the bytes a table was, or is about to be, loaded
    from. Nothing in the lake is replaced (L5)."""

    def __init__(self, table: str, build: str, expected: str, found: str, detail: str) -> None:
        self.table = table
        self.build = build
        self.expected = expected
        self.found = found
        super().__init__(
            f"table {table!r} at build {build!r}: {detail} "
            f"(expected SHA-256 {expected}, found {found}). "
            "Game data is keyed by build and never overwritten (L5): the lake does not "
            "load these bytes, and a table it already holds stays as it was loaded."
        )


# ─── models ──────────────────────────────────────────────────────────────────


class Column(BaseModel):
    """One column: wago's name, unchanged, and the type inferred for this build."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: ColumnType


class LoadedTable(BaseModel):
    """A row of ``_lake_tables``: what was loaded, from which bytes."""

    model_config = ConfigDict(frozen=True)

    name: str
    sha256: str  # of the cached CSV the rows came from
    row_count: int


class LoadResult(BaseModel):
    """What ``Lake.load`` did. ``loaded`` is False when the table was already
    there (the no-op); ``fetched`` is True when the CSV was not in the
    ``gamedata`` cache before the call, so ``GameData.table`` downloaded it."""

    model_config = ConfigDict(frozen=True)

    table: LoadedTable
    loaded: bool
    fetched: bool


class TableSchema(BaseModel):
    """A loaded table's columns, in wago's order, with their inferred types."""

    model_config = ConfigDict(frozen=True)

    build: str
    name: str
    sha256: str
    row_count: int
    columns: tuple[Column, ...]


# ─── locations ───────────────────────────────────────────────────────────────


def default_lake_dir() -> Path:
    """``<user data dir>/wowlab/db2lake``."""
    return platformdirs.user_data_path("wowlab") / "db2lake"


def _refuse_install(path: Path) -> Path:
    """Resolve ``path`` once and return it; raise ``LakeLocationError`` if it
    is an install or inside one (it or a folder above it holds
    ``.build.info`` or ``.flavor.info``), or cannot be resolved (a symlink
    loop). Reads only."""
    try:
        resolved = path.resolve()
    except (RuntimeError, OSError) as exc:
        raise LakeLocationError(f"{path} cannot be resolved ({exc})") from None
    try:
        # A non-strict resolve() does not see every loop; a strict walk does.
        # Only a loop is refused here: a missing tail is a file not made yet.
        os.path.realpath(path, strict=True)
    except OSError as exc:
        if exc.errno == errno.ELOOP or getattr(exc, "winerror", 0) == _CANT_RESOLVE_FILENAME:
            raise LakeLocationError(f"{path} cannot be resolved ({exc})") from None
    for candidate in (resolved, *resolved.parents):
        for marker in (BUILD_INFO, FLAVOR_INFO):
            if (candidate / marker).exists():
                raise LakeLocationError(
                    f"{path} is inside a game install ({candidate} holds {marker}); "
                    "the db2lake never lives in an install (L1)"
                )
    return resolved


def _refuse_linked(resolved: Path) -> None:
    """SQLite writes a database and its journal in place, through whatever
    they are. Refuse a database file with a second hard link, and a journal,
    WAL or shared-memory file beside it that is a link of either kind: a write
    to any of them would land in the other name's file, which may be in an
    install. Missing files are fine; SQLite creates them."""
    for candidate in (resolved, *(resolved.with_name(resolved.name + s) for s in _COMPANIONS)):
        try:
            info = os.lstat(candidate)
        except FileNotFoundError:
            continue
        if candidate != resolved and stat.S_ISLNK(info.st_mode):
            raise LakeLocationError(
                f"{candidate} is a symbolic link; SQLite would write through it (L1)"
            )
        if info.st_nlink > 1:
            raise LakeLocationError(
                f"{candidate} has {info.st_nlink} hard links; SQLite writes it in place, so "
                "a write would change the other names' file too (L1)"
            )


def _checked_file(path: Path) -> Path:
    """The database file to connect to: resolved once, outside any install,
    with no hard link to anything else. Connect to what this returns."""
    resolved = _refuse_install(path)
    _refuse_linked(resolved)
    return resolved


def _prepare_dir(path: Path) -> None:
    """Create the lake directory, refusing an install before the mkdir and
    again after it (a symlink along the way resolves differently once the
    directory exists)."""
    _refuse_install(path)
    path.mkdir(parents=True, exist_ok=True)
    _refuse_install(path)


def _uri(resolved: Path, mode: Literal["ro", "rwc"]) -> str:
    """A SQLite URI for an already resolved path (percent-encoded by pathlib,
    so a ``?`` or ``#`` in a folder name stays part of the path)."""
    return f"{resolved.as_uri()}?mode={mode}"


def _check_build(build: str) -> None:
    if not _BUILD_STRING.fullmatch(build):
        raise ValueError(f"not a full build string (a.b.c.d): {build!r}")


def attach_alias(build: str) -> str:
    """The schema name another build's lake is attached under: ``b`` and the
    build string's digits (``1.60.1.70009`` is ``b160170009``)."""
    _check_build(build)
    return "b" + build.replace(".", "")


def _check_sqlite() -> None:
    if sqlite3.sqlite_version_info < _MIN_SQLITE:
        raise LakeError(
            f"db2lake needs SQLite {'.'.join(map(str, _MIN_SQLITE))} or later for STRICT "
            f"tables; this Python has {sqlite3.sqlite_version}"
        )


# ─── SQL text ────────────────────────────────────────────────────────────────


def _quote(name: str) -> str:
    """``name`` as a SQL identifier: double-quoted, embedded quotes doubled.
    A NUL is refused: SQLite ends a statement's text there."""
    if "\x00" in name:
        raise ValueError(f"an identifier holds a NUL: {name!r}")
    return '"' + name.replace('"', '""') + '"'


def _fold(name: str) -> str:
    """How SQLite compares names: ASCII letters without case, the rest exactly."""
    return name.translate(_ASCII_LOWER)


def _order_by(columns: Sequence[str]) -> str:
    """``ORDER BY`` the row id (CSV order), under a name no column shadows."""
    taken = {_fold(name) for name in columns}
    for candidate in _ROWID_NAMES:
        if candidate not in taken:
            return f" ORDER BY {candidate}"
    # All three shadowed: a rowid table's full scan is in rowid order anyway.
    return ""


# ─── the CSV ─────────────────────────────────────────────────────────────────


class _Reader:
    """One CSV file: its physical lines, every byte hashed as read, each
    record held to ``MAX_RECORD_BYTES`` before it is decoded. ``offset`` is
    how many bytes have been read (the batch's byte budget reads it)."""

    def __init__(self, path: Path, handle: BinaryIO, digest: _Hash, max_columns: int) -> None:
        self.path = path
        self.offset = 0
        self._handle = handle
        self._digest = digest
        self._max_columns = max_columns
        self._line = 0
        self._record_start = 0

    def _lines(self) -> Iterator[str]:
        """Line endings kept. Bad UTF-8 is reported with a line and a byte
        offset, as ``gamedata`` reports it."""
        while True:
            budget = MAX_RECORD_BYTES - (self.offset - self._record_start)
            line = self._handle.readline(budget + 1)
            if not line:
                return
            if len(line) > budget:
                raise MalformedTable(
                    f"{self.path}: line {self._line + 1}: a record longer than "
                    f"{MAX_RECORD_BYTES} bytes; no game table has one"
                )
            self._line += 1
            self._digest.update(line)
            try:
                text = line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise MalformedTable(
                    f"{self.path}: line {self._line}, byte offset {self.offset + exc.start}: "
                    f"not UTF-8 ({exc.reason})"
                ) from exc
            self.offset += len(line)
            yield text

    def records(self) -> Iterator[list[str]]:
        """The header, then every record, checked as ``GameData.rows`` checks
        them: strict CSV, UTF-8, every record as wide as the header."""
        reader = csv.reader(self._lines(), strict=True)
        width = -1
        try:
            for record in reader:
                self._record_start = self.offset  # csv reads no further than the record
                if width < 0:
                    _check_header(self.path, record, self._max_columns)
                    width = len(record)
                elif len(record) != width:
                    raise MalformedTable(
                        f"{self.path}: line {reader.line_num}: {len(record)} fields, "
                        f"header has {width}"
                    )
                yield record
        except csv.Error as exc:
            raise MalformedTable(f"{self.path}: line {reader.line_num}: {exc}") from exc
        if width < 0:
            raise MalformedTable(f"{self.path}: no header line")


def _check_header(path: Path, header: list[str], max_columns: int) -> None:
    if not header:
        raise MalformedTable(f"{path}: line 1: the header names no columns")
    if len(header) > max_columns:
        raise MalformedTable(
            f"{path}: line 1: {len(header)} columns, more than SQLite's limit of {max_columns}"
        )
    repeated = sorted(name for name, n in Counter(header).items() if n > 1)
    if repeated:
        raise MalformedTable(
            f"{path}: line 1: column names repeat: {repeated}; a table cannot hold both"
        )
    folds = Counter(_fold(name) for name in header)
    clashes = sorted(name for name in header if folds[_fold(name)] > 1)
    if clashes:
        raise MalformedTable(
            f"{path}: line 1: column names repeat when ASCII case is ignored, as SQLite "
            f"ignores it: {clashes}"
        )
    with_nul = [name for name in header if "\x00" in name]
    if with_nul:
        raise MalformedTable(f"{path}: line 1: a column name holds a NUL: {with_nul!r}")


def _is_integer(cell: str) -> bool:
    return _INTEGER_TEXT.fullmatch(cell) is not None and (
        len(cell) < _ALWAYS_FITS
        or (len(cell) <= _LONGEST_I64 and _I64_MIN <= int(cell) <= _I64_MAX)
    )


def _is_decimal(cell: str) -> bool:
    return _DECIMAL_TEXT.fullmatch(cell) is not None and math.isfinite(float(cell))


class _Scan(NamedTuple):
    header: list[str]
    types: list[ColumnType]
    row_count: int
    sha256: str


def _scan(path: Path, max_columns: int) -> _Scan:
    """Pass one: the header, each column's type, the row count, the SHA-256."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        records = _Reader(path, handle, digest, max_columns).records()
        header = next(records)
        states = [_EMPTY] * len(header)
        undecided = list(range(len(header)))  # columns not yet TEXT
        count = 0
        for record in records:
            count += 1
            became_text = False
            for i in undecided:
                cell = record[i]
                if not cell:
                    continue
                if states[i] <= _INTEGER and _is_integer(cell):
                    states[i] = _INTEGER
                elif _is_decimal(cell):
                    states[i] = _REAL
                else:
                    states[i] = _TEXT
                    became_text = True
            if became_text:
                undecided = [i for i in undecided if states[i] != _TEXT]
    types = [_TYPE_NAMES.get(state, _ALL_EMPTY) for state in states]
    return _Scan(header, types, count, digest.hexdigest())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


# ─── the lake ────────────────────────────────────────────────────────────────


@dataclass
class _Budget:
    """What one ``query`` may spend: a monotonic deadline and a count of
    SQLite virtual-machine steps. ``stopped`` says which ran out."""

    deadline: float | None
    max_operations: int | None
    operations: int = 0
    stopped: str | None = None


class Lake:
    """The typed tables of one full build, loaded on demand from ``gamedata``.

    ``rows`` and ``schema`` load a table the lake lacks (``GameData.table``
    fetches it if the cache lacks it too). ``load`` is the explicit form and
    always compares the cached CSV with what was loaded; ``rows`` and
    ``schema`` compare once per ``Lake`` object. ``tables`` never creates the
    file; ``load``, ``rows``, ``schema``, ``attach`` and ``query`` open it,
    and so create it (holding only the metadata) if it is not there.
    """

    def __init__(
        self,
        build: str,
        *,
        gamedata: GameData | None = None,
        lake_dir: Path | None = None,
        batch_rows: int = BATCH_ROWS,
    ) -> None:
        _check_build(build)
        if batch_rows < 1:
            raise ValueError("batch_rows must be at least 1")
        self._build = build
        self._dir = Path(lake_dir) if lake_dir is not None else default_lake_dir()
        _refuse_install(self._dir)
        self._path = self._dir / f"{build}.sqlite"
        self._gamedata = gamedata if gamedata is not None else GameData()
        self._batch_rows = batch_rows
        self._conn: sqlite3.Connection | None = None
        self._resolved = self._path
        self._attached: dict[str, str] = {}  # alias -> build
        self._verified: set[str] = set()  # tables compared with the cache by this object
        # (authorizer action, first argument) pairs allowed for the module's
        # own next statement; see _authorize.
        self._permits: set[tuple[int, str | None]] = set()
        self._budget: _Budget | None = None

    @property
    def build(self) -> str:
        return self._build

    @property
    def lake_dir(self) -> Path:
        return self._dir

    @property
    def path(self) -> Path:
        """This build's database file."""
        return self._path

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._attached.clear()

    def __enter__(self) -> Lake:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # guards on the connection ---------------------------------------------

    def _authorize(
        self,
        action: int,
        arg1: str | None,
        _arg2: str | None,
        _schema: str | None,
        _source: str | None,
    ) -> int:
        """SQLite's authorizer, consulted as each statement is prepared (the
        statement cache is off, so on every execution). ATTACH (which is also
        how ``VACUUM INTO`` opens its file), DETACH and PRAGMA are refused
        unless the module is issuing that very statement, except the
        read-only ``table_info``."""
        if action == sqlite3.SQLITE_PRAGMA:
            name = (arg1 or "").lower()
            allowed = name in _OPEN_PRAGMAS or (action, name) in self._permits
        elif action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
            allowed = (action, arg1) in self._permits
        else:
            allowed = True
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY

    @contextlib.contextmanager
    def _permit(self, action: int, arg: str | None) -> Iterator[None]:
        self._permits.add((action, arg))
        try:
            yield
        finally:
            self._permits.discard((action, arg))

    def _set_query_only(self, conn: sqlite3.Connection, on: bool) -> None:
        with self._permit(sqlite3.SQLITE_PRAGMA, "query_only"):
            conn.execute(f"PRAGMA query_only = {'ON' if on else 'OFF'}")

    def _progress(self) -> int:
        """Runs every ``_PROGRESS_STEPS`` steps of any statement. Being Python,
        it is also where a pending Ctrl-C is raised, which stops the statement
        (see ``_sqlite_errors``)."""
        budget = self._budget
        if budget is None:
            return 0
        budget.operations += _PROGRESS_STEPS
        if budget.max_operations is not None and budget.operations > budget.max_operations:
            budget.stopped = f"its budget of {budget.max_operations} operations"
            return 1
        if budget.deadline is not None and time.monotonic() > budget.deadline:
            budget.stopped = "its deadline"
            return 1
        return 0

    @contextlib.contextmanager
    def _sqlite_errors(self, path: Path | None = None) -> Iterator[None]:
        """SQLite's own failures as ``LakeError``, naming the file concerned
        and saying what is known: damaged, busy, or just the error. A stopped
        statement is ``QueryStopped`` when a budget ran out and otherwise
        Ctrl-C, raised again as ``KeyboardInterrupt``."""
        where = path or self._path
        try:
            yield
        except sqlite3.Error as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            if code == sqlite3.SQLITE_INTERRUPT:
                budget = self._budget
                if budget is not None and budget.stopped is not None:
                    raise QueryStopped(f"the statement was stopped at {budget.stopped}") from exc
                raise KeyboardInterrupt from exc
            if code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                advice = (
                    "It is derived from the game data cache (ADR-0028): delete it and "
                    "load its tables again."
                )
                raise LakeError(f"{where}: damaged or not a database ({exc}). {advice}") from exc
            if code == sqlite3.SQLITE_AUTH:
                raise LakeError(
                    f"refused ({exc}): no statement run through the lake may ATTACH, DETACH, "
                    "VACUUM INTO or use a PRAGMA other than table_info, since each can open, "
                    "create or change a file (L1)"
                ) from exc
            if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise LakeError(
                    f"{where}: busy ({exc}); another process is writing it. Try again."
                ) from exc
            raise LakeError(f"{where}: SQLite: {exc}") from exc

    # connection -----------------------------------------------------------

    def _open(self, resolved: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(
            _uri(resolved, "rwc"),
            uri=True,
            isolation_level=None,  # transactions are explicit
            timeout=_BUSY_TIMEOUT_SECONDS,
            # Every statement prepared afresh, so the authorizer sees each one
            # with the permits of that moment.
            cached_statements=0,
        )
        try:
            for option, value in _DBCONFIG:
                conn.setconfig(option, value)
            conn.set_authorizer(self._authorize)
            conn.set_progress_handler(self._progress, _PROGRESS_STEPS)
        except BaseException:
            conn.close()
            raise
        return conn

    def _connect(self) -> sqlite3.Connection:
        """The connection, opened on first use. Every call looks again for a
        linked journal beside the file: even a read may open one (a hot
        journal is rolled back), so this runs before each operation."""
        if self._conn is not None:
            _refuse_linked(self._resolved)
            return self._conn
        _check_sqlite()
        _prepare_dir(self._dir)
        resolved = _checked_file(self._path)
        conn = self._open(resolved)
        self._resolved = resolved
        try:
            with self._sqlite_errors():
                self._init(conn)
                self._set_query_only(conn, True)  # off only inside a write
        except BaseException:
            conn.close()
            raise
        self._conn = conn
        return conn

    def _init(self, conn: sqlite3.Connection) -> None:
        """A new, empty file gets the metadata tables. Any other file must be
        exactly a lake: it is checked, never repaired or written into."""
        empty = "SELECT count(*) FROM sqlite_master"
        if conn.execute(empty).fetchone()[0] == 0:
            with self._writing(conn):
                if conn.execute(empty).fetchone()[0] == 0:  # not made meanwhile
                    conn.execute(_META_SQL)
                    conn.execute(_TABLES_SQL)
                    conn.execute(
                        f"INSERT INTO {_META} (key, value) VALUES (?, ?), (?, ?)",
                        ("format", str(LAKE_FORMAT), "build", self._build),
                    )
        _check_lake(conn, "main", self._build, self._path)

    @contextlib.contextmanager
    def _writing(self, conn: sqlite3.Connection) -> Iterator[None]:
        """One write transaction: all of it commits, or none of it does."""
        _refuse_linked(self._resolved)  # a journal planted since the connect
        self._set_query_only(conn, False)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        finally:
            self._set_query_only(conn, True)

    # tables ---------------------------------------------------------------

    def _check_name(self, name: str) -> None:
        """A table name is the caller's data: it must be a name ``gamedata``
        would cache (its key rule raises ``ValueError``) and not one the lake
        or SQLite keeps for itself."""
        self._gamedata.table_path(name, self._build)
        if _fold(name).startswith(_RESERVED_PREFIXES):
            raise ValueError(f"{name!r} is not a game table name the lake can hold (reserved)")

    def _loaded(self, conn: sqlite3.Connection, name: str) -> LoadedTable | None:
        row = conn.execute(
            f"SELECT name, sha256, row_count FROM {_TABLES} WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        table = self._record(row[0], row[1], row[2])
        if table.name != name:
            raise LakeError(
                f"{name!r}: the lake holds {table.name!r}, which SQLite treats as the same "
                "name (it ignores ASCII case in names)"
            )
        return table

    def _record(self, name: object, sha256: object, row_count: object) -> LoadedTable:
        try:
            return LoadedTable.model_validate(
                {"name": name, "sha256": sha256, "row_count": row_count}
            )
        except ValidationError as exc:
            raise LakeError(f"{self._path}: a {_TABLES} row db2lake did not write: {exc}") from exc

    def tables(self) -> list[LoadedTable]:
        """What ``_lake_tables`` records, by name. No file yet: none, and no
        file is created."""
        if self._conn is None and not self._path.exists():
            return []
        with self._sqlite_errors():
            conn = self._connect()
            return [
                self._record(name, sha256, row_count)
                for name, sha256, row_count in conn.execute(
                    f"SELECT name, sha256, row_count FROM {_TABLES} ORDER BY name"
                )
            ]

    def _verify(self, table: LoadedTable) -> None:
        """Compare a loaded table with the cached CSV, if the cache still has
        one; without one there is nothing to compare and the lake stands."""
        source = self._gamedata.table_path(table.name, self._build)
        if source.is_file():
            found = _sha256_file(source)
            if found != table.sha256:
                raise SourceChanged(
                    table.name,
                    self._build,
                    table.sha256,
                    found,
                    "the cached CSV is not the one this table was loaded from",
                )
        self._verified.add(table.name)

    def load(self, name: str) -> LoadResult:
        """Load ``name`` if the lake lacks it; otherwise compare the cached CSV
        with what was loaded (a no-op when they match, ``SourceChanged`` when
        they do not)."""
        self._check_name(name)
        with self._sqlite_errors():
            conn = self._connect()
            existing = self._loaded(conn, name)
            if existing is not None:
                self._verify(existing)
                return LoadResult(table=existing, loaded=False, fetched=False)
            fetched = not self._gamedata.table_path(name, self._build).exists()
            source = self._gamedata.table(name, self._build)
            max_columns = conn.getlimit(sqlite3.SQLITE_LIMIT_COLUMN)
            scan = _scan(source, max_columns)
            sidecar = self._gamedata.sidecar(name, self._build)
            if sidecar is not None and sidecar.sha256 != scan.sha256:
                raise SourceChanged(
                    name,
                    self._build,
                    sidecar.sha256,
                    scan.sha256,
                    "the cached CSV is not the file its fetch record describes",
                )
            _refuse_install(self._path)
            with self._writing(conn):
                raced = self._loaded(conn, name)  # another process, since the check above
                if raced is not None:
                    if raced.sha256 != scan.sha256:
                        raise SourceChanged(
                            name,
                            self._build,
                            raced.sha256,
                            scan.sha256,
                            "another process loaded it from other bytes",
                        )
                    table = raced
                    loaded = False
                else:
                    self._create(conn, name, source, scan, max_columns)
                    table = LoadedTable(name=name, sha256=scan.sha256, row_count=scan.row_count)
                    loaded = True
        self._verified.add(name)
        return LoadResult(table=table, loaded=loaded, fetched=fetched)

    def _create(
        self, conn: sqlite3.Connection, name: str, source: Path, scan: _Scan, max_columns: int
    ) -> None:
        """Pass two, inside the load's transaction: the table, its rows in
        batches, and its ``_lake_tables`` row."""
        table = _quote(name)
        columns = ", ".join(
            f"{_quote(column)} {kind}" for column, kind in zip(scan.header, scan.types, strict=True)
        )
        conn.execute(f"CREATE TABLE {table} ({columns}) STRICT")
        insert = f"INSERT INTO {table} VALUES ({', '.join(['?'] * len(scan.header))})"
        integers = [i for i, kind in enumerate(scan.types) if kind == "INTEGER"]
        reals = [i for i, kind in enumerate(scan.types) if kind == "REAL"]
        rows_per_batch = max(1, min(self._batch_rows, _BATCH_CELLS // len(scan.header)))
        digest = hashlib.sha256()
        count = 0
        try:
            with source.open("rb") as handle:
                reader = _Reader(source, handle, digest, max_columns)
                records = reader.records()
                if next(records) != scan.header:
                    raise ValueError("the header changed")
                batch: list[list[Cell]] = []
                started = reader.offset
                for record in records:
                    row = cast(list[Cell], record)
                    for i in integers:
                        row[i] = int(record[i]) if record[i] else None
                    for i in reals:
                        row[i] = float(record[i]) if record[i] else None
                    batch.append(row)
                    if len(batch) >= rows_per_batch or reader.offset - started >= _BATCH_BYTES:
                        conn.executemany(insert, batch)
                        count += len(batch)
                        batch = []
                        started = reader.offset
                if batch:
                    conn.executemany(insert, batch)
                    count += len(batch)
        except (ValueError, OverflowError, sqlite3.IntegrityError) as exc:
            # Pass one checked every cell, so only a file that changed since
            # gets here; the transaction rolls back.
            try:
                now = _sha256_file(source)
            except OSError as gone:
                now = f"unreadable ({gone})"
            raise SourceChanged(
                name, self._build, scan.sha256, now, f"the cached CSV changed while loading ({exc})"
            ) from exc
        found = digest.hexdigest()
        if found != scan.sha256 or count != scan.row_count:
            raise SourceChanged(
                name, self._build, scan.sha256, found, "the cached CSV changed while it was loaded"
            )
        conn.execute(
            f"INSERT INTO {_TABLES} (name, sha256, row_count) VALUES (?, ?, ?)",
            (name, scan.sha256, count),
        )

    def _ensure(self, conn: sqlite3.Connection, name: str) -> LoadedTable:
        self._check_name(name)
        existing = self._loaded(conn, name)
        if existing is None:
            return self.load(name).table
        if name not in self._verified:
            self._verify(existing)
        return existing

    def _columns(self, conn: sqlite3.Connection, name: str) -> list[tuple[str, str]]:
        return [
            (column, kind)
            for column, kind in conn.execute(
                "SELECT name, type FROM pragma_table_info(?, 'main') ORDER BY cid", (name,)
            )
        ]

    def schema(self, name: str) -> TableSchema:
        """Columns in wago's order, each with its inferred type, and the row
        count. Loads the table if the lake lacks it."""
        with self._sqlite_errors():
            conn = self._connect()
            table = self._ensure(conn, name)
            columns = self._columns(conn, name)
        try:
            return TableSchema(
                build=self._build,
                name=table.name,
                sha256=table.sha256,
                row_count=table.row_count,
                columns=tuple(
                    Column.model_validate({"name": column, "type": kind})
                    for column, kind in columns
                ),
            )
        except ValidationError as exc:
            raise LakeError(
                f"{self._path}: table {name!r} is not one db2lake wrote: {exc}"
            ) from exc

    def rows(self, name: str) -> Iterator[dict[str, Cell]]:
        """Every row, in CSV order, as a dict of ``int | float | str | None``.
        Loads the table if the lake lacks it (before returning, so a load
        error is raised here, not on the first ``next``)."""
        with self._sqlite_errors():
            conn = self._connect()
            self._ensure(conn, name)
            order = _order_by([column for column, _ in self._columns(conn, name)])
            cursor = conn.execute(f"SELECT * FROM main.{_quote(name)}{order}")
        return self._dicts(cursor)

    def _dicts(self, cursor: sqlite3.Cursor) -> Iterator[dict[str, Cell]]:
        names = [entry[0] for entry in cursor.description]
        with self._sqlite_errors():
            for row in cursor:
                yield dict(zip(names, row, strict=True))

    # other builds and SQL -------------------------------------------------

    def attach(self, build: str) -> str:
        """Attach ``build``'s lake read-only under ``attach_alias(build)`` and
        return the alias; a join across builds is then plain SQL. The other
        lake must exist (load a table into it first) and must be a lake that
        records that build. Attaching the same build again is a no-op."""
        alias = attach_alias(build)
        if build == self._build:
            raise LakeError(f"{build} is this lake's own build; its tables are in main")
        held = self._attached.get(alias)
        if held == build:
            return alias
        if held is not None:
            raise LakeError(f"builds {held!r} and {build!r} would both attach as {alias}")
        other = self._dir / f"{build}.sqlite"
        resolved = _checked_file(other)
        if not resolved.is_file():
            raise LakeError(f"no lake for build {build} at {other}: load a table into it first")
        with self._sqlite_errors():
            conn = self._connect()
        with self._sqlite_errors(other):
            with self._permit(sqlite3.SQLITE_ATTACH, None):
                conn.execute(f"ATTACH DATABASE ? AS {_quote(alias)}", (_uri(resolved, "ro"),))
            try:
                _check_lake(conn, alias, build, other)
            except BaseException:
                with self._permit(sqlite3.SQLITE_DETACH, alias):
                    conn.execute(f"DETACH DATABASE {_quote(alias)}")
                raise
        self._attached[alias] = build
        return alias

    def query(
        self,
        sql: str,
        parameters: Sequence[Cell] = (),
        *,
        deadline_seconds: float | None = QUERY_DEADLINE_SECONDS,
        max_operations: int | None = None,
    ) -> Iterator[tuple[Cell, ...]]:
        """Run one statement the library wrote and yield its rows. Values go
        in ``parameters``, never in ``sql``. The connection is ``query_only``
        outside loads and its authorizer refuses ATTACH, DETACH, ``VACUUM
        INTO`` and PRAGMAs (``table_info`` aside), so no statement here opens
        or writes a file. The statement stops with ``QueryStopped`` after
        ``deadline_seconds`` of wall clock from this call (time between rows
        included) or ``max_operations`` SQLite steps, and with
        ``KeyboardInterrupt`` on Ctrl-C. Not for SQL typed by the owner: that
        surface (M12-04) adds its own guards. Loads nothing."""
        budget = _Budget(
            deadline=None if deadline_seconds is None else time.monotonic() + deadline_seconds,
            max_operations=max_operations,
        )
        with self._sqlite_errors():
            conn = self._connect()
        with self._budgeted(budget), self._sqlite_errors():
            cursor = conn.execute(sql, parameters)
        return self._tuples(cursor, budget)

    @contextlib.contextmanager
    def _budgeted(self, budget: _Budget) -> Iterator[None]:
        self._budget = budget
        try:
            yield
        finally:
            self._budget = None

    def _tuples(self, cursor: sqlite3.Cursor, budget: _Budget) -> Iterator[tuple[Cell, ...]]:
        step: Callable[[], tuple[Cell, ...] | None] = cursor.fetchone
        while True:
            with self._budgeted(budget), self._sqlite_errors():
                row = step()
            if row is None:
                return
            yield tuple(row)


def _check_lake(conn: sqlite3.Connection, schema: str, build: str, path: Path) -> None:
    """Refuse a database that is not exactly a lake db2lake wrote for
    ``build``: its metadata tables as this module creates them, one STRICT
    table per ``_lake_tables`` row, and nothing else (no view, trigger or
    other table or index). Reads only."""
    where = _quote(schema)
    objects = set(conn.execute(f"SELECT type, name, tbl_name, sql FROM {where}.sqlite_master"))
    if not _LAKE_OBJECTS.issubset(objects):
        raise LakeError(
            f"{path}: not a db2lake file (its metadata tables are missing or not as db2lake "
            "makes them); it is left as it is"
        )
    loaded = {name for (name,) in conn.execute(f"SELECT name FROM {where}.{_TABLES}")}
    tables = set()
    for kind, name, owner, sql in sorted(objects - _LAKE_OBJECTS, key=lambda o: (o[1], o[0])):
        if (
            kind == "table"
            and name in loaded
            and owner == name
            and isinstance(sql, str)
            and sql.startswith(f"CREATE TABLE {_quote(name)} (")
            and sql.endswith(") STRICT")
        ):
            tables.add(name)
            continue
        raise LakeError(
            f"{path}: not a db2lake file: it holds {kind} {name!r}, which db2lake does not "
            "make; it is left as it is"
        )
    if tables != loaded:
        raise LakeError(
            f"{path}: records tables it does not hold: {sorted(loaded - tables)}; "
            "it is left as it is"
        )
    meta = dict(conn.execute(f"SELECT key, value FROM {where}.{_META}"))
    if meta.get("format") != str(LAKE_FORMAT) or set(meta) != {"format", "build"}:
        raise LakeError(f"{path}: not a db2lake file of format {LAKE_FORMAT} ({meta!r})")
    if meta.get("build") != build:
        raise LakeError(
            f"{path}: holds build {meta.get('build')!r}, not {build!r}; a lake answers "
            "for the build it was loaded for (L5)"
        )
