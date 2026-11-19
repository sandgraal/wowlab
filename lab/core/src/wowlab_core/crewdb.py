"""crewdb: wowlab's own database of the crew book (docs/LAB_PLAN.md §21.3,
ADR-0039, M20-01).

The crew book in the game's `WowLab.lua` is bounded and drops its oldest
unrated players when full; this database keeps everything a sync has seen,
for good. One SQLite file per book, at
`<user data>/wowlab/crew/<flavor folder>/<account folder>.sqlite`, made on
the first sync, never inside an install (L1) and never in the repository.
It is primary data, not a cache: nothing here is shared with `db2lake`,
whose tables can always be fetched again.

What holds it (§21.3):

- **The file.** `STRICT` tables, a fixed `application_id`, `secure_delete`
  on and the rollback journal (no write-ahead log). The folder is 0700 and
  every file 0600 whatever the umask (POSIX; on Windows the user's folder
  is private by its inherited access list). The path is built from the
  discovered folder names only, each a single path component, and must
  resolve under wowlab's `crew/` folder.
- **SQL.** Every statement is a module-level constant string (or a value of
  a module-level dict of constant strings) with bound parameters; nothing
  read from a book or typed by the player is ever part of a statement. A
  static test reads this file and fails on a statement built any other way.
- **The sync** (rules 1 to 6): a player in the saved file is added or
  updated, a player only in the database stays; rating and note come from
  the saved file; `first` takes the earlier time and `last` the later; each
  counter is added to by the difference from the value the saved file
  showed at the last sync, or by the whole value when the saved file's is
  lower (the game dropped the player and started again); one transaction;
  an unreadable book syncs nothing (the caller never gets this far); a copy
  is kept before the database's own schema changes.
- **What a sync will not take.** The book is checked whole first
  (`check_book`), and a book that is not the one the database knows, or
  that wowlab's own journal shows was put back (`snap restore`, `undo`,
  `sv merge`) since the last sync, is held: synced only by hand, after a
  plan and a yes.
- **Copies.** The three newest daily copies are kept beside the file.
- **Two syncs at once** are settled by SQLite's lock: the second waits
  `LOCK_WAIT_SECONDS` and then answers from the database as it is.

It reads the install only through the bytes its caller hands it, reads
`guard`'s journal through `guard.history()` (never writing it) and imports
no network module (ADR-0036 term 1).
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import os
import re
import sqlite3
import stat
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wowlab_core import crewbook, guard, snapshot
from wowlab_core.errors import WowlabError

# ─── constants ───────────────────────────────────────────────────────────────

SCHEMA = 1
"""The database's own schema (`PRAGMA user_version`)."""
APPLICATION_ID = 0x574C4342  # "WLCB": wowlab crew book
"""`PRAGMA application_id` of every crew database; `_SQL_SET_APP_ID` holds
the same number, which a test checks."""
FOLDER = "crew"
SUFFIX = ".sqlite"
LOCK_WAIT_SECONDS = 5.0
"""How long a sync waits for another one's lock before it answers from the
database as it is."""
DAILY_COPIES = 3

# What a sync will take (§21.3, "The book is checked whole").
MAX_PLAYERS = 20_000
KEY_MAX_BYTES = 256
TEXT_MAX_BYTES = 256  # class, role, guild, instance name
NOTE_MAX_BYTES = 200  # §20.1, as `crewedit.NOTE_MAX_BYTES`
COUNTER_MAX = 10_000_000
LEVEL_MAX = 1_000
TIME_MIN = 1_072_915_200  # 2004-01-01 00:00 UTC
TIME_MAX = 4_102_444_800  # 2100-01-01 00:00 UTC

_PRIVATE_MODES = os.name == "posix"
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)
_DAILY = re.compile(r"\.daily-([0-9]{4}-[0-9]{2}-[0-9]{2})\Z")
_BEFORE_SCHEMA = re.compile(r"\.before-schema-[0-9]+\Z")
# The labels the crew write commands give their write-gate changes:
# "crew forget (3 players)", "crew rate (1 player)", "crew note (1 player)".
_CREW_WRITE = re.compile(r"crew (forget|rate|note) \([0-9]+ players?\)\Z")

# ─── SQL: constant strings only ──────────────────────────────────────────────

_SQL_READ_APP_ID = "PRAGMA application_id"
_SQL_READ_VERSION = "PRAGMA user_version"
_SQL_COUNT_OBJECTS = "SELECT count(*) FROM sqlite_master"
_SQL_SECURE_DELETE = "PRAGMA secure_delete = ON"
_SQL_JOURNAL_MODE = "PRAGMA journal_mode = DELETE"
_SQL_FOREIGN_KEYS = "PRAGMA foreign_keys = ON"
_SQL_TRUSTED_SCHEMA = "PRAGMA trusted_schema = OFF"
_SQL_SET_APP_ID = "PRAGMA application_id = 1464615746"
_SQL_SET_VERSION: dict[int, str] = {1: "PRAGMA user_version = 1"}
_SQL_BEGIN = "BEGIN IMMEDIATE"
_SQL_COMMIT = "COMMIT"
_SQL_ROLLBACK = "ROLLBACK"

_SQL_CREATE: tuple[str, ...] = (
    "CREATE TABLE players ("
    " player_key TEXT PRIMARY KEY NOT NULL,"
    " name TEXT NOT NULL,"
    " realm TEXT NOT NULL,"
    " class TEXT,"
    " level INTEGER,"
    " role TEXT,"
    " guild TEXT,"
    " met INTEGER NOT NULL DEFAULT 0,"
    " first_at INTEGER,"
    " last_at INTEGER,"
    " where_name TEXT,"
    " where_id INTEGER,"
    " rating TEXT CHECK (rating IN ('good', 'bad')),"
    " note TEXT,"
    " allied INTEGER NOT NULL DEFAULT 0,"
    " faced INTEGER NOT NULL DEFAULT 0,"
    " allied_won INTEGER NOT NULL DEFAULT 0,"
    " faced_won INTEGER NOT NULL DEFAULT 0,"
    " pvp_last INTEGER,"
    " pvp_map TEXT,"
    " pvp_side TEXT CHECK (pvp_side IN ('with', 'against')),"
    " met_seen INTEGER NOT NULL DEFAULT 0,"
    " allied_seen INTEGER NOT NULL DEFAULT 0,"
    " faced_seen INTEGER NOT NULL DEFAULT 0,"
    " allied_won_seen INTEGER NOT NULL DEFAULT 0,"
    " faced_won_seen INTEGER NOT NULL DEFAULT 0,"
    " in_book INTEGER NOT NULL DEFAULT 0,"
    " marks_changed INTEGER NOT NULL,"
    " marks_agreed INTEGER NOT NULL"
    ") STRICT",
    "CREATE INDEX players_last ON players (last_at)",
    "CREATE INDEX players_rating ON players (rating)",
    "CREATE INDEX players_faced ON players (faced)",
    "CREATE INDEX players_allied ON players (allied)",
    "CREATE TABLE matches ("
    " match_id INTEGER PRIMARY KEY,"
    " at INTEGER NOT NULL,"
    " map TEXT,"
    " map_id INTEGER NOT NULL,"
    " kind TEXT NOT NULL CHECK (kind IN ('bg', 'arena')),"
    " won INTEGER CHECK (won IN (0, 1))"
    ") STRICT",
    "CREATE UNIQUE INDEX matches_at_map ON matches (at, map_id)",
    "CREATE TABLE match_players ("
    " match_id INTEGER NOT NULL REFERENCES matches (match_id) ON DELETE CASCADE,"
    " player_key TEXT NOT NULL,"
    " side TEXT NOT NULL CHECK (side IN ('team', 'foes')),"
    " PRIMARY KEY (match_id, player_key)"
    ") STRICT",
    "CREATE INDEX match_players_match ON match_players (match_id)",
    "CREATE INDEX match_players_key ON match_players (player_key)",
    "CREATE TABLE meta (name TEXT PRIMARY KEY NOT NULL, value ANY) STRICT",
)

# Statements that move the database from schema n to n + 1. None yet: the
# copy kept before a schema change (rule 6) is made by `_migrate`.
_MIGRATIONS: dict[int, tuple[str, ...]] = {}

_SQL_META_ALL = "SELECT name, value FROM meta"
_SQL_META_SET = (
    "INSERT INTO meta (name, value) VALUES (?, ?) "
    "ON CONFLICT (name) DO UPDATE SET value = excluded.value"
)
_SQL_PLAYERS_ALL = (
    "SELECT player_key, name, realm, class, level, role, guild, met, first_at, last_at,"
    " where_name, where_id, rating, note, met_seen, in_book, marks_changed, marks_agreed"
    " FROM players"
)
_SQL_PLAYER_INSERT = (
    "INSERT INTO players (player_key, name, realm, class, level, role, guild, met, first_at,"
    " last_at, where_name, where_id, rating, note, met_seen, in_book, marks_changed,"
    " marks_agreed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_SQL_PLAYER_UPDATE = (
    "UPDATE players SET name = ?, realm = ?, class = ?, level = ?, role = ?, guild = ?,"
    " met = ?, first_at = ?, last_at = ?, where_name = ?, where_id = ?, rating = ?,"
    " note = ?, met_seen = ?, in_book = ?, marks_changed = ?, marks_agreed = ?"
    " WHERE player_key = ?"
)

# ─── errors and models ───────────────────────────────────────────────────────


class CrewDbError(WowlabError):
    """The crew database cannot be used: a path outside wowlab's `crew/`
    folder, a file that is not a crew database, one written by a newer
    wowlab. The message is in the player's words and quotes nothing from a
    book."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Target(_Model):
    """The book a sync reads and the database it fills: the install root
    (resolved), the flavor folder and its path, the game account folder, and
    `WowLab.lua`'s path relative to the flavor folder."""

    install_root: str
    flavor_folder: str
    flavor_path: str
    account: str
    file: str


class CrewRow(crewbook.CrewPlayer):
    """One player as wowlab's crew database keeps them: the fields of the
    book's record (`CrewPlayer`), and whether the game's list held them at
    the last sync (`in_game_list`; false: the game dropped or forgot them,
    and only the database keeps them)."""

    in_game_list: bool = True


SyncState = Literal[
    "synced", "unchanged", "held", "declined", "skipped", "refused", "elsewhere", "not_run"
]
HoldReason = Literal["other_book", "put_back", "journal_unreadable", "journal_record_gone"]


class SyncPlan(_Model):
    """What a sync would change: players added and updated, ratings and
    notes changed on players the database already keeps, players the game's
    list no longer holds, and matches added (none from a schema 1 book)."""

    players_added: int
    players_updated: int
    ratings_changed: int
    notes_changed: int
    left_game_list: int
    matches_added: int = 0


class SyncOutcome(_Model):
    """What a sync did. `state`: `synced` (written), `unchanged` (nothing
    new), `held` (not run: see `held`), `declined` (held, shown by hand and
    not confirmed), `skipped` (another sync held the lock), `refused` (the
    book failed the whole-book check), `elsewhere` (a database for this book
    exists for another install root or flavor folder: `crew sync --adopt`),
    `not_run` (no book to sync). `words` is one sentence in the player's
    words; `plan` what it changed or would change; `database` the file."""

    state: SyncState
    words: str
    held: list[HoldReason] = Field(default_factory=list)
    plan: SyncPlan | None = None
    database: str | None = None


class AdoptOutcome(_Model):
    """What `crew sync --adopt` did: the database it took (`moved_from`,
    None when it stayed where it was) and the install root and flavor folder
    it was filled from before (`was_root`, `was_flavor_folder`)."""

    database: str
    moved_from: str | None
    was_root: str | None
    was_flavor_folder: str | None
    words: str


# ─── the path ────────────────────────────────────────────────────────────────


def crew_root() -> Path:
    """`<user data>/wowlab/crew`, read at call time. On Windows the local,
    not the roaming, application data folder (`platformdirs`' default)."""
    return snapshot.user_data_dir() / FOLDER


def _component(name: str, what: str) -> str:
    bad = (
        not name
        or name in (".", "..")
        or "\x00" in name
        or "/" in name
        or "\\" in name
        or (os.altsep is not None and os.altsep in name)
    )
    if bad:
        raise CrewDbError(
            f"The {what} folder's name cannot be part of a path in wowlab's crew folder, so "
            "wowlab keeps no crew database for it. Nothing was written."
        )
    return name


def _under_crew(path: Path, *, depth: int) -> Path:
    """`path`, refused unless it resolves to exactly `depth` folders below
    the resolved `crew/` folder (a link anywhere on the way is refused)."""
    root = Path(os.path.realpath(crew_root()))
    real = Path(os.path.realpath(path))
    try:
        rel = real.relative_to(root)
    except ValueError:
        rel = None
    if rel is None or len(rel.parts) != depth or real != root.joinpath(*path.parts[-depth:]):
        raise CrewDbError(
            f"{path} would lead outside wowlab's crew folder ({crew_root()}), so wowlab does "
            "not use it. Nothing was written."
        )
    return path


def database_path(flavor_folder: str, account: str) -> Path:
    """The crew database of one game account in one flavor folder, built
    from the two discovered folder names only and checked to resolve under
    `crew/` (§21.3)."""
    path = (
        crew_root()
        / _component(flavor_folder, "flavor")
        / (_component(account, "game account") + SUFFIX)
    )
    return _under_crew(path, depth=2)


def _private_folder(folder: Path) -> None:
    """Make `crew/` and `folder` (one below it) 0700 whatever the umask."""
    snapshot.make_private_dirs(folder)
    if _PRIVATE_MODES:
        for f in (crew_root(), folder):
            st = os.lstat(f)
            if not stat.S_ISDIR(st.st_mode):
                raise CrewDbError(f"{f} is not a folder. Nothing was written.")
            if stat.S_IMODE(st.st_mode) != 0o700:
                f.chmod(0o700)


def _private_file(path: Path) -> None:
    """Make a new, empty file at `path` with mode 0600 whatever the umask;
    one already there is set to 0600."""
    with contextlib.suppress(FileExistsError):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_BINARY, 0o600)
        os.close(fd)
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise CrewDbError(f"{path} is not a regular file. Nothing was written.")
    if _PRIVATE_MODES and stat.S_IMODE(st.st_mode) != 0o600:
        path.chmod(0o600)


# ─── opening ─────────────────────────────────────────────────────────────────


def _newer(path: Path, version: int) -> CrewDbError:
    return CrewDbError(
        f"The crew database {path} was written by a newer wowlab (its version {version}; this "
        f"wowlab reads version {SCHEMA}). Nothing was read or written: update wowlab to use it."
    )


def _not_ours(path: Path) -> CrewDbError:
    return CrewDbError(
        f"{path} is not a crew database wowlab made, so wowlab does not read or change it. "
        "Nothing was written."
    )


def _connect(path: Path) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(path, timeout=LOCK_WAIT_SECONDS, isolation_level=None)
    except sqlite3.Error as exc:
        raise CrewDbError(f"The crew database {path} cannot be opened ({exc}).") from exc
    return conn


def _check(conn: sqlite3.Connection, path: Path) -> int:
    """The database's schema, after its `application_id` is checked; 0 for
    an empty file. Reads only."""
    try:
        app_id = conn.execute(_SQL_READ_APP_ID).fetchone()[0]
        version = conn.execute(_SQL_READ_VERSION).fetchone()[0]
        objects = conn.execute(_SQL_COUNT_OBJECTS).fetchone()[0]
    except sqlite3.DatabaseError as exc:
        if "locked" in str(exc):
            raise
        raise _not_ours(path) from exc
    if app_id == 0 and version == 0 and objects == 0:
        return 0
    if app_id != APPLICATION_ID:
        raise _not_ours(path)
    if version > SCHEMA:
        raise _newer(path, version)
    return int(version)


def _settings(conn: sqlite3.Connection) -> None:
    conn.execute(_SQL_SECURE_DELETE)
    conn.execute(_SQL_JOURNAL_MODE).fetchall()
    conn.execute(_SQL_FOREIGN_KEYS)
    conn.execute(_SQL_TRUSTED_SCHEMA)


@contextlib.contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """One transaction under the write lock (`BEGIN IMMEDIATE`): committed
    when the block ends, rolled back on any error."""
    conn.execute(_SQL_BEGIN)
    try:
        yield
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            conn.execute(_SQL_ROLLBACK)
        raise
    conn.execute(_SQL_COMMIT)


def _copy(conn: sqlite3.Connection, dest: Path) -> None:
    """A whole copy of the database at `dest`, 0600, put in place at once."""
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    _private_file(tmp)
    try:
        out = sqlite3.connect(tmp, isolation_level=None)
        try:
            conn.backup(out)
        finally:
            out.close()
        if _PRIVATE_MODES:
            tmp.chmod(0o600)
        tmp.replace(dest)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _migrate(conn: sqlite3.Connection, path: Path, version: int) -> None:
    """Bring a database of an older schema up to `SCHEMA`, after a copy of
    it is kept beside it (rule 6)."""
    if version >= SCHEMA:
        return
    _copy(conn, path.with_name(f"{path.name}.before-schema-{version}"))
    with _transaction(conn):
        for step in range(version, SCHEMA):
            for statement in _MIGRATIONS[step]:
                conn.execute(statement)
            conn.execute(_SQL_SET_VERSION[step + 1])


def _create(conn: sqlite3.Connection) -> None:
    with _transaction(conn):
        if conn.execute(_SQL_COUNT_OBJECTS).fetchone()[0]:
            return  # another wowlab made it meanwhile
        conn.execute(_SQL_SET_APP_ID)
        for statement in _SQL_CREATE:
            conn.execute(statement)
        conn.execute(_SQL_SET_VERSION[SCHEMA])


def _open(path: Path, *, create: bool) -> sqlite3.Connection | None:
    """The database at `path`, checked and at `SCHEMA`; None when there is
    none and `create` is false. A newer or foreign file is refused before
    anything is written."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        st = None
    if st is not None and not stat.S_ISREG(st.st_mode):
        raise _not_ours(path)
    if st is None and not create:
        return None
    if create:
        # Made private, or set back to private when something widened it.
        _private_folder(path.parent)
        _private_file(path)
    conn = _connect(path)
    try:
        version = _check(conn, path)
        _settings(conn)
        if version == 0:
            _create(conn)
        else:
            _migrate(conn, path, version)
    except BaseException:
        conn.close()
        raise
    return conn


# ─── reading ─────────────────────────────────────────────────────────────────


@dataclasses.dataclass
class _Row:
    key: str
    name: str
    realm: str
    class_token: str | None
    level: int | None
    role: str | None
    guild: str | None
    met: int
    first: int | None
    last: int | None
    where: str | None
    where_id: int | None
    rating: str | None
    note: str | None
    met_seen: int
    in_book: int
    marks_changed: int
    marks_agreed: int

    def values(self) -> tuple[object, ...]:
        return (
            self.name,
            self.realm,
            self.class_token,
            self.level,
            self.role,
            self.guild,
            self.met,
            self.first,
            self.last,
            self.where,
            self.where_id,
            self.rating,
            self.note,
            self.met_seen,
            self.in_book,
            self.marks_changed,
            self.marks_agreed,
        )


def _rows(conn: sqlite3.Connection) -> dict[str, _Row]:
    return {r[0]: _Row(*r) for r in conn.execute(_SQL_PLAYERS_ALL)}


def _meta(conn: sqlite3.Connection) -> dict[str, object]:
    return {str(name): value for name, value in conn.execute(_SQL_META_ALL)}


def _public(row: _Row, book: crewbook.CrewBook | None) -> CrewRow:
    kept = {p.key: p for p in book.players} if book is not None else {}
    held = kept.get(row.key)
    return CrewRow(
        key=row.key,
        class_token=row.class_token,
        level=row.level,
        role=row.role,
        guild=row.guild,
        met=row.met,
        first=row.first,
        last=row.last,
        where=row.where,
        where_id=row.where_id,
        rating="good" if row.rating == "good" else "bad" if row.rating == "bad" else None,
        note=row.note,
        in_current_group=held.in_current_group if held is not None else False,
        other_keys=list(held.other_keys) if held is not None else [],
        in_game_list=bool(row.in_book),
    )


def read(flavor_folder: str, account: str, book: crewbook.CrewBook | None = None) -> list[CrewRow]:
    """Every player wowlab's crew database keeps for this book; empty when
    there is no database yet. Writes nothing but what SQLite itself needs
    to finish a change an earlier run left half done. `book`, when given,
    lends each player still in it its saved-file-only facts (in the group
    at the last save, unknown fields)."""
    path = database_path(flavor_folder, account)
    try:
        conn = _open(path, create=False)
    except sqlite3.OperationalError as exc:
        raise CrewDbError(f"The crew database {path} cannot be read now ({exc}).") from exc
    if conn is None:
        return []
    try:
        return [_public(r, book) for r in _rows(conn).values()]
    except sqlite3.Error as exc:
        raise CrewDbError(f"The crew database {path} cannot be read ({exc}).") from exc
    finally:
        conn.close()


# ─── the whole-book check ────────────────────────────────────────────────────


def _bytes_of(text: str) -> int | None:
    """UTF-8 length, or None for text that is not valid Unicode."""
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _key_ok(key: str) -> bool:
    """`Name-Realm`: a `-` with text after it (§20.1: a key ending in `-` is
    never written), within `KEY_MAX_BYTES`. The key is otherwise opaque and
    kept as stored (§20.2)."""
    size = _bytes_of(key)
    return "-" in key and not key.endswith("-") and size is not None and size <= KEY_MAX_BYTES


_TEXTS = (
    ("class_token", "class", TEXT_MAX_BYTES),
    ("role", "role", TEXT_MAX_BYTES),
    ("guild", "guild", TEXT_MAX_BYTES),
    ("where", "instance name", TEXT_MAX_BYTES),
    ("note", "note", NOTE_MAX_BYTES),
)
_TIMES = (("first", "first-grouped time"), ("last", "last-grouped time"))


def _refused(kind: str, rule: str) -> str:
    return (
        f"The crew book was not synced into wowlab's crew database: {kind} is outside what "
        f"wowlab accepts ({rule}). Nothing was synced; the database is as it was."
    )


def check_book(book: crewbook.CrewBook) -> str | None:
    """Why this book may not be synced, in the player's words naming the
    kind of field and never its content; None when every field is within
    bounds (§21.3, "The book is checked whole")."""
    if book.unread:
        return _refused("a player's entry", "every field of the form the lab-addon writes")
    if len(book.players) > MAX_PLAYERS:
        return _refused("the number of players", f"at most {MAX_PLAYERS:,}")
    if book.started is not None and not TIME_MIN <= book.started <= TIME_MAX:
        return _refused("the book's start time", "a date from 2004 to 2099")
    for name, value in (("count of skipped reads", book.skipped), ("load count", book.probe_loads)):
        if value is not None and not 0 <= value <= COUNTER_MAX:
            return _refused(f"the book's {name}", f"a whole number from 0 to {COUNTER_MAX:,}")
    for p in book.players:
        if not _key_ok(p.key):
            return _refused(
                "a player's name",
                f"Name-Realm, not ending in a dash, at most {KEY_MAX_BYTES} bytes of text",
            )
        for field, kind, limit in _TEXTS:
            text = getattr(p, field)
            if text is None:
                continue
            size = _bytes_of(text)
            if size is None or size > limit:
                return _refused(f"a player's {kind}", f"at most {limit} bytes of text")
        if p.met is not None and not 0 <= p.met <= COUNTER_MAX:
            return _refused("a player's count of groups", f"from 0 to {COUNTER_MAX:,}")
        if p.level is not None and not 1 <= p.level <= LEVEL_MAX:
            return _refused("a player's level", f"from 1 to {LEVEL_MAX:,}")
        for field, kind in _TIMES:
            value = getattr(p, field)
            if value is not None and not TIME_MIN <= value <= TIME_MAX:
                return _refused(f"a player's {kind}", "a date from 2004 to 2099")
    return None


# ─── the hold ────────────────────────────────────────────────────────────────

_HOLD_WORDS: dict[HoldReason, str] = {
    "other_book": (
        "the crew book in WowLab.lua is not the one wowlab's crew database was filled from "
        "(it was started at another time, or is another game account's)"
    ),
    "put_back": (
        "wowlab put WowLab.lua back since the last sync (a snapshot restore, an undo or a "
        "merge), so the book may be older than the database"
    ),
    "journal_unreadable": (
        "wowlab cannot read its own record of changes, so it cannot tell whether WowLab.lua "
        "was put back since the last sync"
    ),
    "journal_record_gone": (
        "wowlab's record of changes no longer holds the change it saw at the last sync, so it "
        "cannot tell whether WowLab.lua was put back since then"
    ),
}


def _held_words(reasons: Sequence[HoldReason]) -> str:
    why = "; and ".join(_HOLD_WORDS[r] for r in reasons)
    return (
        f"The crew book was not synced into wowlab's crew database: {why}. The database is as "
        "it was; run `wowlab crew sync` to see what a sync would change and choose."
    )


def _same_folder(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _wrote_book(record: guard.HistoryRecord, target: Target) -> bool:
    """Whether a journal record changed this account's `WowLab.lua` and is
    not a crew write command's own (an undo counts whatever it undoes)."""
    if record.rolled_back:
        return False
    if record.undoes is None and _CREW_WRITE.fullmatch(record.label):
        return False
    if not _same_folder(record.flavor_path, target.flavor_path):
        return False
    want = target.file.replace("\\", "/").casefold()
    return any(
        c.path.replace("\\", "/").casefold() == want and c.before != c.after for c in record.paths
    )


def _journal(
    target: Target, since: str | None, synced_before: bool
) -> tuple[list[HoldReason], str | None]:
    """Hold reasons from `guard`'s journal, and its newest record's id."""
    try:
        records = guard.history()
    except (guard.GuardError, OSError, ValueError):
        return (["journal_unreadable"] if synced_before else []), None
    newest = records[-1].id if records else None
    if not synced_before:
        return [], newest
    later: Sequence[guard.HistoryRecord] = records
    if since is not None:
        ids = [r.id for r in records]
        if since not in ids:
            return ["journal_record_gone"], newest
        later = records[ids.index(since) + 1 :]
    if any(_wrote_book(r, target) for r in later):
        return ["put_back"], newest
    return [], newest


# ─── the sync ────────────────────────────────────────────────────────────────

Mode = Literal["normal", "restored", "new_book"]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _number(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _merged(row: _Row | None, p: crewbook.CrewPlayer, mode: Mode, now: int) -> _Row:
    """The row after one player of the saved file is synced into it."""
    seen = p.met if p.met is not None else 0
    name, _, realm = p.key.partition("-")
    if row is None:
        return _Row(
            p.key,
            name,
            realm,
            p.class_token,
            p.level,
            p.role,
            p.guild,
            seen,
            p.first,
            p.last,
            p.where,
            p.where_id,
            p.rating,
            p.note,
            seen,
            1,
            now,
            now,
        )
    new = dataclasses.replace(row, in_book=1, met_seen=seen)
    # Rule 2: counters are added to, never replaced.
    if mode == "restored":
        new.met = max(row.met, seen)
    elif mode == "new_book" or seen < row.met_seen:
        new.met = row.met + seen
    else:
        new.met = row.met + seen - row.met_seen
    firsts = [t for t in (row.first, p.first) if t is not None]
    lasts = [t for t in (row.last, p.last) if t is not None]
    new.first = min(firsts) if firsts else None
    new.last = max(lasts) if lasts else None
    if row.last is None or (p.last is not None and p.last >= row.last):
        for field in ("class_token", "level", "role", "guild", "where", "where_id"):
            value = getattr(p, field)
            if value is not None:
                setattr(new, field, value)
    marks = (p.rating, p.note)
    kept = mode == "restored" and row.marks_changed > row.marks_agreed
    if not kept and marks != (row.rating, row.note):
        new.rating, new.note = marks
        new.marks_changed = now
    if (new.rating, new.note) == marks:
        new.marks_agreed = now
    return new


def _left(row: _Row) -> _Row:
    """A row whose player the saved file no longer holds."""
    return dataclasses.replace(row, in_book=0, met_seen=0)


def _changes(
    rows: dict[str, _Row], book: crewbook.CrewBook, mode: Mode, now: int
) -> tuple[list[_Row], list[_Row], SyncPlan]:
    """(rows to insert, rows to update, the plan)."""
    added: list[_Row] = []
    updated: list[_Row] = []
    ratings = notes = left = 0
    keys = set()
    for p in book.players:
        keys.add(p.key)
        old = rows.get(p.key)
        new = _merged(old, p, mode, now)
        if old is None:
            added.append(new)
            continue
        if _same(old, new):
            continue
        updated.append(new)
        ratings += old.rating != new.rating
        notes += old.note != new.note
    for key, row in rows.items():
        if key not in keys and (row.in_book or row.met_seen):
            updated.append(_left(row))
            left += bool(row.in_book)
    plan = SyncPlan(
        players_added=len(added),
        players_updated=sum(1 for r in updated if r.in_book),
        ratings_changed=ratings,
        notes_changed=notes,
        left_game_list=left,
    )
    return added, updated, plan


def _same(a: _Row, b: _Row) -> bool:
    """Equal but for the agreement time, which every sync moves."""
    return dataclasses.replace(a, marks_agreed=0) == dataclasses.replace(b, marks_agreed=0)


def _identity(book: crewbook.CrewBook, target: Target) -> tuple[object, object]:
    return (book.started, target.account)


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _daily_copy(conn: sqlite3.Connection, path: Path) -> None:
    """Keep today's copy of the database (made once a day, before the first
    change), and the newest `DAILY_COPIES` in all."""
    dest = path.with_name(f"{path.name}.daily-{_today()}")
    if not os.path.lexists(dest):
        _copy(conn, dest)
    dated = sorted(
        (m.group(1), f)
        for f in path.parent.iterdir()
        if f.name.startswith(path.name) and (m := _DAILY.search(f.name[len(path.name) :]))
    )
    for _, old in dated[:-DAILY_COPIES]:
        with contextlib.suppress(FileNotFoundError):
            old.unlink()


def _write(
    conn: sqlite3.Connection,
    added: list[_Row],
    updated: list[_Row],
    meta: dict[str, object],
) -> None:
    conn.executemany(_SQL_PLAYER_INSERT, [(r.key, *r.values()) for r in added])
    conn.executemany(_SQL_PLAYER_UPDATE, [(*r.values(), r.key) for r in updated])
    conn.executemany(_SQL_META_SET, list(meta.items()))


def _locked(exc: sqlite3.OperationalError) -> bool:
    return "locked" in str(exc) or "busy" in str(exc)


def _skipped(path: Path) -> SyncOutcome:
    return SyncOutcome(
        state="skipped",
        words=(
            f"Another wowlab was syncing the crew database, so this one did not sync after "
            f"waiting {LOCK_WAIT_SECONDS:g} seconds and answers from the database as it is."
        ),
        database=str(path),
    )


def _elsewhere(path: Path, root: object, flavor_folder: object, account: str) -> SyncOutcome:
    return SyncOutcome(
        state="elsewhere",
        words=(
            f"wowlab already keeps a crew database for game account {account}, filled from "
            f"flavor folder {flavor_folder} of the install at {root} ({path}). It does not sync "
            "this book into it or start a second one: run `wowlab crew sync --adopt` to make it "
            "this install's. Nothing was synced."
        ),
        database=str(path),
    )


def _meta_rows(
    target: Target, book: crewbook.CrewBook, data: bytes, journal: str | None
) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "last_sync": int(time.time()),
        "book_sha256": hashlib.sha256(data).hexdigest(),
        "book_started": book.started,
        "account": target.account,
        "flavor_folder": target.flavor_folder,
        "install_root": target.install_root,
        "journal_record": journal,
    }


def sync(
    target: Target,
    data: bytes,
    book: crewbook.CrewBook,
    *,
    by_hand: bool = False,
    confirm: Callable[[SyncPlan, str], bool] | None = None,
) -> SyncOutcome:
    """Sync the crew book (`book`, parsed from the saved file's `data`)
    into wowlab's crew database (§21.3). Never raises for a held, refused,
    skipped or misplaced sync: the outcome says why. `by_hand` is
    `wowlab crew sync`: a held book is then shown as a plan to `confirm`
    (with the hold's sentence) and synced only on a yes."""
    path = database_path(target.flavor_folder, target.account)
    if book.state == "not_turned_on":
        return SyncOutcome(
            state="not_run",
            words="The crew book is not turned on, so nothing was synced.",
            database=str(path),
        )
    why = check_book(book)
    if why is not None:
        return SyncOutcome(state="refused", words=why, database=str(path))
    exists = os.path.lexists(path)
    if not exists:
        orphan = _orphans(target)
        if orphan:
            root, folder, found = orphan[0]
            return _elsewhere(found, root, folder, target.account)
    try:
        conn = _open(path, create=True)
    except sqlite3.OperationalError as exc:
        if _locked(exc):
            return _skipped(path)
        raise CrewDbError(f"The crew database {path} cannot be opened ({exc}).") from exc
    assert conn is not None
    try:
        return _sync_open(conn, path, target, data, book, by_hand=by_hand, confirm=confirm)
    except sqlite3.OperationalError as exc:
        if _locked(exc):
            return _skipped(path)
        raise CrewDbError(f"The crew database {path} could not be synced ({exc}).") from exc
    except sqlite3.Error as exc:
        raise CrewDbError(f"The crew database {path} could not be synced ({exc}).") from exc
    finally:
        conn.close()


def _sync_open(
    conn: sqlite3.Connection,
    path: Path,
    target: Target,
    data: bytes,
    book: crewbook.CrewBook,
    *,
    by_hand: bool,
    confirm: Callable[[SyncPlan, str], bool] | None,
) -> SyncOutcome:
    meta = _meta(conn)
    synced_before = "install_root" in meta
    if synced_before and meta.get("install_root") != target.install_root:
        return _elsewhere(path, meta.get("install_root"), meta.get("flavor_folder"), target.account)
    reasons, newest = _journal(target, _text(meta.get("journal_record")), synced_before)
    if synced_before and (meta.get("book_started"), meta.get("account")) != _identity(book, target):
        reasons = ["other_book", *reasons]
    sha = hashlib.sha256(data).hexdigest()
    if not reasons and meta.get("book_sha256") == sha:
        if meta.get("journal_record") != newest:
            with _transaction(conn):
                conn.execute(_SQL_META_SET, ("journal_record", newest))
        return SyncOutcome(
            state="unchanged",
            words="wowlab's crew database already holds everything in the crew book.",
            database=str(path),
        )
    mode: Mode = "new_book" if "other_book" in reasons else "restored" if reasons else "normal"
    now = time.time_ns()
    if reasons:
        _, _, plan = _changes(_rows(conn), book, mode, now)
        words = _held_words(reasons)
        if not by_hand:
            return SyncOutcome(
                state="held", words=words, held=reasons, plan=plan, database=str(path)
            )
        if confirm is None or not confirm(plan, words):
            return SyncOutcome(
                state="declined",
                words="Nothing was synced; the crew database is as it was.",
                held=reasons,
                plan=plan,
                database=str(path),
            )
    if synced_before:
        _daily_copy(conn, path)
    with _transaction(conn):
        rows = _rows(conn)
        added, updated, plan = _changes(rows, book, mode, now)
        _write(conn, added, updated, _meta_rows(target, book, data, newest))
    total = len(rows) + len(added)
    return SyncOutcome(
        state="synced",
        words=(
            f"Synced the crew book into wowlab's crew database: {plan.players_added} added, "
            f"{plan.players_updated} updated; it keeps {total:,} "
            f"{'player' if total == 1 else 'players'}."
        ),
        held=reasons,
        plan=plan,
        database=str(path),
    )


# ─── another root, a renamed flavor folder: --adopt ─────────────────────────


def _peek(path: Path) -> dict[str, object] | None:
    """`meta` of a crew database, read-only; None when it is not one."""
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=LOCK_WAIT_SECONDS)
    except sqlite3.Error:
        return None
    try:
        if conn.execute(_SQL_READ_APP_ID).fetchone()[0] != APPLICATION_ID:
            return None
        return _meta(conn)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _orphans(target: Target) -> list[tuple[object, object, Path]]:
    """Crew databases of this game account under another flavor folder
    whose own install folder (the root and flavor folder they were filled
    from) is gone: a flavor folder renamed, or an install moved. Each is
    (install root, flavor folder, path)."""
    root = crew_root()
    try:
        folders = sorted(root.iterdir())
    except OSError:
        return []
    found: list[tuple[object, object, Path]] = []
    for folder in folders:
        if folder.name == target.flavor_folder or not stat.S_ISDIR(os.lstat(folder).st_mode):
            continue
        candidate = folder / (target.account + SUFFIX)
        try:
            if not stat.S_ISREG(os.lstat(candidate).st_mode):
                continue
        except OSError:
            continue
        meta = _peek(candidate)
        if meta is None:
            continue
        was_root, was_folder = meta.get("install_root"), meta.get("flavor_folder")
        if not isinstance(was_root, str) or not isinstance(was_folder, str):
            continue
        if not Path(was_root, was_folder).is_dir():
            found.append((was_root, was_folder, candidate))
    return found


def _move_with_copies(source: Path, dest: Path) -> None:
    """Move a database and every copy wowlab keeps of it; nothing is left
    behind and nothing at `dest` is replaced."""
    _private_folder(dest.parent)
    moves = [(source, dest)]
    for f in source.parent.iterdir():
        rest = f.name[len(source.name) :]
        if f.name.startswith(source.name) and (_DAILY.search(rest) or _BEFORE_SCHEMA.search(rest)):
            moves.append((f, dest.with_name(dest.name + rest)))
    for _, to in moves:
        if os.path.lexists(to):
            raise CrewDbError(f"{to} is already there, so nothing was moved.")
    for frm, to in moves:
        frm.replace(to)


def adopt(target: Target) -> AdoptOutcome:
    """`wowlab crew sync --adopt`: make the crew database of this book's
    game account, filled from another install root or from a flavor folder
    since renamed, this install's. Takes only a file from wowlab's own
    `crew/` folder carrying the `application_id`; moves it (no copy is left
    behind); never merges two databases."""
    path = database_path(target.flavor_folder, target.account)
    moved_from: Path | None = None
    if os.path.lexists(path):
        meta = _peek(path)
        if meta is None:
            raise _not_ours(path)
        was_root, was_folder = meta.get("install_root"), meta.get("flavor_folder")
        if was_root == target.install_root:
            raise CrewDbError(
                f"The crew database {path} is already this install's, so there is nothing to "
                "adopt. Nothing was changed."
            )
    else:
        orphans = _orphans(target)
        if not orphans:
            raise CrewDbError(
                f"wowlab keeps no other crew database for game account {target.account}, so "
                "there is nothing to adopt. Nothing was changed."
            )
        if len(orphans) > 1:
            listed = "; ".join(str(p) for _, _, p in orphans)
            raise CrewDbError(
                f"{len(orphans)} crew databases could be this book's ({listed}); wowlab never "
                "merges two, so it adopts none. Nothing was changed."
            )
        was_root, was_folder, moved_from = orphans[0]
        _under_crew(moved_from, depth=2)
        _move_with_copies(moved_from, path)
    conn = _open(path, create=False)
    assert conn is not None
    try:
        with _transaction(conn):
            conn.executemany(
                _SQL_META_SET,
                [("install_root", target.install_root), ("flavor_folder", target.flavor_folder)],
            )
    except sqlite3.OperationalError as exc:
        raise CrewDbError(f"The crew database {path} could not be adopted ({exc}).") from exc
    finally:
        conn.close()
    return AdoptOutcome(
        database=str(path),
        moved_from=str(moved_from) if moved_from is not None else None,
        was_root=_text(was_root),
        was_flavor_folder=_text(was_folder),
        words=(
            f"wowlab's crew database for game account {target.account} is now this install's "
            f"({path})" + (f", moved from {moved_from}" if moved_from is not None else "") + "."
        ),
    )


def copies(flavor_folder: str, account: str) -> list[Path]:
    """The copies wowlab keeps beside the database: daily ones and the one
    kept before a schema change."""
    path = database_path(flavor_folder, account)
    try:
        entries = list(path.parent.iterdir())
    except OSError:
        return []
    return sorted(
        f
        for f in entries
        if f.name.startswith(path.name)
        and (
            _DAILY.search(f.name[len(path.name) :])
            or _BEFORE_SCHEMA.search(f.name[len(path.name) :])
        )
    )


__all__ = [
    "APPLICATION_ID",
    "COUNTER_MAX",
    "DAILY_COPIES",
    "LOCK_WAIT_SECONDS",
    "MAX_PLAYERS",
    "SCHEMA",
    "AdoptOutcome",
    "CrewDbError",
    "CrewRow",
    "SyncOutcome",
    "SyncPlan",
    "Target",
    "adopt",
    "check_book",
    "copies",
    "crew_root",
    "database_path",
    "read",
    "sync",
]
