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
import json
import math
import os
import re
import sqlite3
import stat
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wowlab_core import crewbook, crewedit, guard, snapshot
from wowlab_core.errors import WowlabError

# ─── constants ───────────────────────────────────────────────────────────────

SCHEMA = 3
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
MAX_MATCHES = 500
MATCH_SIDE_MAX = 100
"""At most this many keys on each side of one match: the largest
battleground side is 40 players, with room to spare."""
KEY_MAX_BYTES = 256
TEXT_MAX_BYTES = 256  # class, role, guild, instance name
NOTE_MAX_BYTES = 200  # §20.1, as `crewedit.NOTE_MAX_BYTES`
COUNTER_MAX = 10_000_000
LEVEL_MAX = 1_000
TIME_MIN = 1_072_915_200  # 2004-01-01 00:00 UTC
TIME_MAX = 4_102_444_800  # 2100-01-01 00:00 UTC
TIME_AHEAD = 30 * 86_400
"""How far past this computer's clock a time in a book may be: a later one
would pin a row's `last` for good, so its entry is left out."""
# What M20-11 records of each player (§21.2, as built; M20-12).
GUID_MAX_BYTES = 128
"""The game's id of a character is about 20 characters of ASCII."""
HONOR_LEVEL_MAX = 100_000
SCORE_FIELDS_MAX = 64
"""At most this many fields in one player's scoreboard line (the client's
row has about 20)."""
SCORE_NAME_MAX_BYTES = 64
SCORE_NUMBER_MAX = 10**15
"""The largest number, either sign, a scoreboard field may hold (damage
done in one long match stays far below it)."""

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
_SQL_SET_VERSION: dict[int, str] = {
    1: "PRAGMA user_version = 1",
    2: "PRAGMA user_version = 2",
    3: "PRAGMA user_version = 3",
}
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
    " marks_agreed INTEGER NOT NULL,"
    " rating_own INTEGER NOT NULL DEFAULT 0 CHECK (rating_own IN (0, 1)),"
    " note_own INTEGER NOT NULL DEFAULT 0 CHECK (note_own IN (0, 1)),"
    " guid TEXT,"
    " race TEXT,"
    " faction TEXT,"
    " spec TEXT,"
    " honor_level INTEGER,"
    " score TEXT,"
    " score_at INTEGER"
    ") STRICT",
    "CREATE INDEX players_last ON players (last_at)",
    "CREATE INDEX players_rating ON players (rating)",
    "CREATE INDEX players_faced ON players (faced)",
    "CREATE INDEX players_allied ON players (allied)",
    "CREATE INDEX players_guid ON players (guid)",
    "CREATE TABLE matches ("
    " match_id INTEGER PRIMARY KEY,"
    " at INTEGER NOT NULL,"
    " map TEXT,"
    " map_id INTEGER NOT NULL,"
    " kind TEXT NOT NULL CHECK (kind IN ('bg', 'arena')),"
    " won INTEGER CHECK (won IN (0, 1)),"
    " shuffle INTEGER NOT NULL DEFAULT 0 CHECK (shuffle IN (0, 1))"
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

# Statements that move the database from schema n to n + 1, after a copy of
# it is kept beside it (rule 6, `_migrate`).
#
# 1 -> 2 (M20-02, fix round 2): whether the rating, and separately the note,
# is the database's own (given at the desk, not yet agreed with the saved
# file). Schema 1 knew it for the row only (`marks_changed > marks_agreed`):
# such a row's two fields both start as its own, and the next sync gives up
# each one that equals the saved file's value (`_merged`); a row that agreed
# keeps neither.
#
#
# 2 -> 3 (M20-12): what M20-11 records of each player (the GUID, race,
# faction, spec, honor level and the latest scoreboard line, kept as JSON
# text with its time), an index on the GUID (one player met under two
# names, §21.3), and whether a match is a solo shuffle. A shuffle is an
# arena, so it is kept as `kind` `arena` with `shuffle` 1: a CHECK cannot be
# widened in place, and rebuilding `matches` would cascade into
# `match_players`. Every new column starts empty (0 for `shuffle`).
#
# Keyed by (schema it starts from, order within the step); each value one
# constant statement, so the static check reads every one.
_MIGRATIONS: dict[tuple[int, int], str] = {
    (1, 1): "ALTER TABLE players ADD COLUMN rating_own INTEGER NOT NULL DEFAULT 0"
    " CHECK (rating_own IN (0, 1))",
    (1, 2): "ALTER TABLE players ADD COLUMN note_own INTEGER NOT NULL DEFAULT 0"
    " CHECK (note_own IN (0, 1))",
    (1, 3): "UPDATE players SET rating_own = (marks_changed > marks_agreed),"
    " note_own = (marks_changed > marks_agreed)",
    (2, 1): "ALTER TABLE players ADD COLUMN guid TEXT",
    (2, 2): "ALTER TABLE players ADD COLUMN race TEXT",
    (2, 3): "ALTER TABLE players ADD COLUMN faction TEXT",
    (2, 4): "ALTER TABLE players ADD COLUMN spec TEXT",
    (2, 5): "ALTER TABLE players ADD COLUMN honor_level INTEGER",
    (2, 6): "ALTER TABLE players ADD COLUMN score TEXT",
    (2, 7): "ALTER TABLE players ADD COLUMN score_at INTEGER",
    (2, 8): "CREATE INDEX players_guid ON players (guid)",
    (2, 9): "ALTER TABLE matches ADD COLUMN shuffle INTEGER NOT NULL DEFAULT 0"
    " CHECK (shuffle IN (0, 1))",
}

_SQL_META_ALL = "SELECT name, value FROM meta"
_SQL_META_SET = (
    "INSERT INTO meta (name, value) VALUES (?, ?) "
    "ON CONFLICT (name) DO UPDATE SET value = excluded.value"
)
_SQL_PLAYERS_ALL = (
    "SELECT player_key, name, realm, class, level, role, guild, met, first_at, last_at,"
    " where_name, where_id, rating, note, met_seen, in_book, marks_changed, marks_agreed,"
    " rating_own, note_own, allied, faced, allied_won, faced_won, pvp_last, pvp_map,"
    " pvp_side, allied_seen, faced_seen, allied_won_seen, faced_won_seen, guid, race,"
    " faction, spec, honor_level, score, score_at"
    " FROM players"
)
_SQL_PLAYER_INSERT = (
    "INSERT INTO players (player_key, name, realm, class, level, role, guild, met, first_at,"
    " last_at, where_name, where_id, rating, note, met_seen, in_book, marks_changed,"
    " marks_agreed, rating_own, note_own, allied, faced, allied_won, faced_won, pvp_last,"
    " pvp_map, pvp_side, allied_seen, faced_seen, allied_won_seen, faced_won_seen, guid,"
    " race, faction, spec, honor_level, score, score_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
    " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_SQL_PLAYER_UPDATE = (
    "UPDATE players SET name = ?, realm = ?, class = ?, level = ?, role = ?, guild = ?,"
    " met = ?, first_at = ?, last_at = ?, where_name = ?, where_id = ?, rating = ?,"
    " note = ?, met_seen = ?, in_book = ?, marks_changed = ?, marks_agreed = ?,"
    " rating_own = ?, note_own = ?, allied = ?, faced = ?, allied_won = ?, faced_won = ?,"
    " pvp_last = ?, pvp_map = ?, pvp_side = ?, allied_seen = ?, faced_seen = ?,"
    " allied_won_seen = ?, faced_won_seen = ?, guid = ?, race = ?, faction = ?, spec = ?,"
    " honor_level = ?, score = ?, score_at = ?"
    " WHERE player_key = ?"
)
# Matches (§21.3 rule 3, M20-09): one row per match, told apart by `at` and
# `map_id` (a unique index), and one row per player in it.
_SQL_MATCH_IDS = "SELECT at, map_id FROM matches"
_SQL_MATCH_INSERT = (
    "INSERT INTO matches (at, map, map_id, kind, won, shuffle) VALUES (?, ?, ?, ?, ?, ?)"
)
_SQL_MATCH_PLAYER_INSERT = "INSERT INTO match_players (match_id, player_key, side) VALUES (?, ?, ?)"
_SQL_MATCHES_ALL = (
    "SELECT match_id, at, map, map_id, kind, won, shuffle FROM matches"
    " ORDER BY at DESC, match_id DESC"
)
_SQL_MATCHES_OF = (
    "SELECT match_id, at, map, map_id, kind, won, shuffle FROM matches WHERE match_id IN"
    " (SELECT match_id FROM match_players WHERE player_key = ?)"
    " ORDER BY at DESC, match_id DESC"
)
_SQL_MATCH_PLAYERS_OF = (
    "SELECT match_id, player_key, side FROM match_players WHERE match_id IN"
    " (SELECT match_id FROM match_players WHERE player_key = ?)"
    " ORDER BY match_id, rowid"
)
_SQL_MATCH_PLAYERS_ALL = (
    "SELECT match_id, player_key, side FROM match_players ORDER BY match_id, rowid"
)
_SQL_MATCH_KEYS = "SELECT DISTINCT player_key FROM match_players"
# `crew list --map`: a `LIKE` with `\` as its escape, the text's own `\`,
# `%` and `_` escaped (`_like`).
_SQL_KEYS_ON_MAP = (
    "SELECT DISTINCT match_players.player_key FROM match_players"
    " JOIN matches ON matches.match_id = match_players.match_id"
    " WHERE matches.map LIKE ? ESCAPE '\\'"
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
    # One player met under two names (§21.3, M20-12): the other stored keys
    # the database keeps with the same GUID, the game's id of a character
    # (a rename or a realm move gives a new key, not a new id). Rows are not
    # merged; each lists the others.
    also_known_as: list[str] = Field(default_factory=list)
    # The record with and against (§21.2, §21.3): matches on your side and
    # against you, and of each the ones your side won, as the database adds
    # them up across syncs; and the latest match the saved file named.
    allied: int = 0
    faced: int = 0
    allied_won: int = 0
    faced_won: int = 0
    pvp_last: int | None = None
    pvp_map: str | None = None
    pvp_side: crewbook.PvpSide | None = None


class CrewMatchRow(_Model):
    """One match wowlab's crew database keeps (§21.3): `at` (time() of the
    game's read), the map's name and id as the client gave them, `kind`
    (`bg`, `arena` or `shuffle`), `won` (whether your side won; None when
    the client gave no winner, and always for a shuffle), and the stored
    keys on your side (`team`) and against you (`foes`) that the database
    still holds. A solo shuffle has no sides: everyone in it is in `team`
    and `foes` is empty."""

    at: int
    map: str | None
    map_id: int
    kind: crewbook.MatchKind
    won: bool | None
    team: list[str]
    foes: list[str]


SyncState = Literal[
    "synced", "unchanged", "held", "declined", "skipped", "refused", "elsewhere", "not_run"
]
HoldReason = Literal["other_book", "put_back", "journal_unreadable", "journal_record_gone"]


class SyncPlan(_Model):
    """What a sync would change: players added and updated, ratings and
    notes changed on players the database already keeps (of those, the ones
    removed: `ratings_removed`, `notes_removed`), players the game's list no
    longer holds, and matches added (none from a schema 1 book).
    `ratings_kept` and `notes_kept` count the players the game's list had
    dropped and holds again whose rating (note) given at the desk the
    database kept, the saved file having none (M20-02)."""

    players_added: int
    players_updated: int
    ratings_changed: int
    notes_changed: int
    left_game_list: int
    matches_added: int = 0
    ratings_removed: int = 0
    notes_removed: int = 0
    ratings_kept: int = 0
    notes_kept: int = 0


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
    left_out: int = 0
    """Entries of the book not synced because they could not be read or are
    outside what a sync takes (each listed as unread); every other entry is
    synced."""
    matches_left_out: int = 0
    """Entries of the book's `matches` not synced because they could not be
    read or are outside what a sync takes; every other match is synced."""


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
    except FileNotFoundError:
        # Another run cleared it as a leftover: no copy this time, made at
        # the next sync instead.
        with contextlib.suppress(OSError):
            tmp.unlink()
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
            for name in sorted(k for k in _MIGRATIONS if k[0] == step):
                conn.execute(_MIGRATIONS[name])
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
    if st is not None and (not stat.S_ISREG(st.st_mode) or st.st_nlink > 1):
        # SQLite writes the file in place: a second hard link could be
        # anyone's file, so it is never opened.
        raise _not_ours(path)
    if st is None and not create:
        return None
    if create:
        # Made private, or set back to private when something widened it.
        _private_folder(path.parent)
        _private_file(path)
        _clear_temps(path)
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
    # Whether the rating, and the note, is the database's own: given at the
    # desk to a player the game's list had dropped, not since agreed with the
    # saved file (§21.3, decision 20). A field written through the saved
    # file is never the database's own.
    rating_own: int = 0
    note_own: int = 0
    # The four counters of §21.2 (each added up across syncs as `met` is,
    # beside the value the saved file showed at the last sync) and the
    # latest match the saved file named.
    allied: int = 0
    faced: int = 0
    allied_won: int = 0
    faced_won: int = 0
    pvp_last: int | None = None
    pvp_map: str | None = None
    pvp_side: str | None = None
    allied_seen: int = 0
    faced_seen: int = 0
    allied_won_seen: int = 0
    faced_won_seen: int = 0
    # What M20-11 records (M20-12): each as of the latest group or match
    # that gave it, and the latest scoreboard line as JSON text with its
    # time.
    guid: str | None = None
    race: str | None = None
    faction: str | None = None
    spec: str | None = None
    honor_level: int | None = None
    score: str | None = None
    score_at: int | None = None

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
            self.rating_own,
            self.note_own,
            self.allied,
            self.faced,
            self.allied_won,
            self.faced_won,
            self.pvp_last,
            self.pvp_map,
            self.pvp_side,
            self.allied_seen,
            self.faced_seen,
            self.allied_won_seen,
            self.faced_won_seen,
            self.guid,
            self.race,
            self.faction,
            self.spec,
            self.honor_level,
            self.score,
            self.score_at,
        )


def _rows(conn: sqlite3.Connection) -> dict[str, _Row]:
    return {r[0]: _Row(*r) for r in conn.execute(_SQL_PLAYERS_ALL)}


def _meta(conn: sqlite3.Connection) -> dict[str, object]:
    return {str(name): value for name, value in conn.execute(_SQL_META_ALL)}


def _score_text(score: crewbook.CrewScore | None) -> str | None:
    """A scoreboard line's fields as the database keeps them: JSON text, in
    the order the client gave them; None for no line."""
    if score is None:
        return None
    return json.dumps(score.values, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _score_of(text: str | None, at: int | None) -> crewbook.CrewScore | None:
    """The line a row keeps, read back; None for none, and for text that is
    not the JSON object a sync wrote (never raised: a row stays readable)."""
    if text is None:
        return None
    try:
        loaded = json.loads(text)
    except ValueError:
        return None
    if not isinstance(loaded, dict):
        return None
    values = {
        str(k): v
        for k, v in loaded.items()
        if isinstance(v, (bool, int, str)) or (isinstance(v, float) and math.isfinite(v))
    }
    return crewbook.CrewScore(at=at, values=values)


def _aliases(rows: Sequence[_Row]) -> dict[str, list[str]]:
    """For each stored key, the other keys with the same GUID (§21.3: one
    player met under two names), in the order of the keys."""
    by_guid: dict[str, list[str]] = {}
    for r in sorted(rows, key=lambda r: r.key):
        if crewbook.guid_shaped(r.guid):
            assert r.guid is not None
            by_guid.setdefault(r.guid, []).append(r.key)
    out: dict[str, list[str]] = {}
    for r in rows:
        if r.guid is not None and r.guid in by_guid:
            out[r.key] = [k for k in by_guid[r.guid] if k != r.key]
    return out


def _public(
    row: _Row, book: crewbook.CrewBook | None, aliases: dict[str, list[str]] | None = None
) -> CrewRow:
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
        guid=row.guid,
        race=row.race,
        faction=row.faction,
        spec=row.spec,
        honor_level=row.honor_level,
        score=_score_of(row.score, row.score_at),
        in_current_group=held.in_current_group if held is not None else False,
        other_keys=list(held.other_keys) if held is not None else [],
        in_game_list=bool(row.in_book),
        also_known_as=list((aliases or {}).get(row.key, [])),
        allied=row.allied,
        faced=row.faced,
        allied_won=row.allied_won,
        faced_won=row.faced_won,
        pvp_last=row.pvp_last,
        pvp_map=row.pvp_map,
        pvp_side="with"
        if row.pvp_side == "with"
        else "against"
        if row.pvp_side == "against"
        else None,
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
        rows = list(_rows(conn).values())
        aliases = _aliases(rows)
        return [_public(r, book, aliases) for r in rows]
    except sqlite3.Error as exc:
        raise CrewDbError(f"The crew database {path} cannot be read ({exc}).") from exc
    finally:
        conn.close()


def _read_with(
    flavor_folder: str, account: str, use: Callable[[sqlite3.Connection], object]
) -> object:
    """`use(conn)` on the database, or None when there is none."""
    path = database_path(flavor_folder, account)
    try:
        conn = _open(path, create=False)
    except sqlite3.OperationalError as exc:
        raise CrewDbError(f"The crew database {path} cannot be read now ({exc}).") from exc
    if conn is None:
        return None
    try:
        return use(conn)
    except sqlite3.Error as exc:
        raise CrewDbError(f"The crew database {path} cannot be read ({exc}).") from exc
    finally:
        conn.close()


def matches(flavor_folder: str, account: str, key: str | None = None) -> list[CrewMatchRow]:
    """The matches wowlab's crew database keeps for this book, newest first
    (`key`: only those the player was in, by stored key); empty when there
    is no database yet. Each names the stored keys the database still holds
    in it: a forgotten player is in none."""

    def use(conn: sqlite3.Connection) -> list[CrewMatchRow]:
        found = (
            conn.execute(_SQL_MATCHES_ALL).fetchall()
            if key is None
            else conn.execute(_SQL_MATCHES_OF, (key,)).fetchall()
        )
        sides: dict[int, tuple[list[str], list[str]]] = {r[0]: ([], []) for r in found}
        players = (
            conn.execute(_SQL_MATCH_PLAYERS_ALL)
            if key is None
            else conn.execute(_SQL_MATCH_PLAYERS_OF, (key,))
        )
        for match_id, player_key, side in players:
            if match_id in sides:
                sides[match_id][0 if side == "team" else 1].append(str(player_key))
        return [
            CrewMatchRow(
                at=int(at),
                map=_text(map_name),
                map_id=int(map_id),
                kind="shuffle" if shuffle else "arena" if kind == "arena" else "bg",
                won=None if won is None or shuffle else bool(won),
                team=sides[match_id][0],
                foes=sides[match_id][1],
            )
            for match_id, at, map_name, map_id, kind, won, shuffle in found
        ]

    got = _read_with(flavor_folder, account, use)
    return got if isinstance(got, list) else []


def _like(text: str) -> str:
    """`text` as a `LIKE` pattern that finds it anywhere, with `\\`, `%` and
    `_` in it escaped (§21.3, "SQL")."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def keys_on_map(flavor_folder: str, account: str, text: str) -> set[str]:
    """The stored keys of every player the database keeps in a match on a
    map whose name holds `text` (ASCII letters in any case)."""

    def use(conn: sqlite3.Connection) -> set[str]:
        return {str(r[0]) for r in conn.execute(_SQL_KEYS_ON_MAP, (_like(text),))}

    got = _read_with(flavor_folder, account, use)
    return got if isinstance(got, set) else set()


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
    ("guid", "game's id of the character", GUID_MAX_BYTES),
    ("race", "race", TEXT_MAX_BYTES),
    ("faction", "faction", TEXT_MAX_BYTES),
    ("spec", "specialization", TEXT_MAX_BYTES),
)
_TIMES = (("first", "first-grouped time"), ("last", "last-grouped time"))


def _refused(kind: str, rule: str) -> str:
    return (
        f"The crew book was not synced into wowlab's crew database: {kind} is outside what "
        f"wowlab accepts ({rule}). Nothing was synced; the database is as it was."
    )


def check_book(book: crewbook.CrewBook) -> str | None:
    """Why this book may not be synced at all, in the player's words naming
    the kind of field and never its content; None when the book itself is
    within bounds (§21.3, "The book is checked whole", as ruled on
    2026-11-19: a violation inside one player's entry makes only that entry
    unread, `entry_problems`)."""
    if len(book.players) + len(book.unread) > MAX_PLAYERS:
        return _refused("the number of players", f"at most {MAX_PLAYERS:,}")
    if len(book.matches) + len(book.matches_unread) > MAX_MATCHES:
        return _refused("the number of matches", f"at most {MAX_MATCHES:,}")
    if book.started is not None and not TIME_MIN <= book.started <= _latest():
        return _refused(
            "the book's start time",
            f"a date from 2004 to {TIME_AHEAD // 86_400} days after this computer's clock",
        )
    for name, value in (("count of skipped reads", book.skipped), ("load count", book.probe_loads)):
        if value is not None and not 0 <= value <= COUNTER_MAX:
            return _refused(f"the book's {name}", f"a whole number from 0 to {COUNTER_MAX:,}")
    return None


def _latest() -> int:
    """The latest time a sync takes: this computer's clock plus `TIME_AHEAD`."""
    return min(int(time.time()) + TIME_AHEAD, TIME_MAX)


_PVP_COUNTERS = (
    ("allied", "count of matches with you"),
    ("faced", "count of matches against you"),
    ("allied_won", "count of matches won with you"),
    ("faced_won", "count of matches you won against them"),
)


def _pvp_problem(pvp: crewbook.CrewPvp | None) -> str | None:
    """Why a player's match counters and latest match are outside what a
    sync takes (§21.3: each counter a whole number from 0 to
    `COUNTER_MAX`, the time within range, the map's name within its
    length), never quoting them."""
    if pvp is None:
        return None
    for field, kind in _PVP_COUNTERS:
        value = getattr(pvp, field)
        if value is not None and not 0 <= value <= COUNTER_MAX:
            return f"the {kind} is over {COUNTER_MAX:,}, so it is not synced"
    if pvp.pvp_last is not None and not TIME_MIN <= pvp.pvp_last <= _latest():
        return (
            f"the latest match's time is before 2004 or more than {TIME_AHEAD // 86_400} days "
            "after this computer's clock, so it is not synced"
        )
    if pvp.pvp_map is not None:
        size = _bytes_of(pvp.pvp_map)
        if size is None or size > TEXT_MAX_BYTES:
            return (
                f"the latest match's map is not text of at most {TEXT_MAX_BYTES} bytes, so it "
                "is not synced"
            )
    return None


def _entry_problem(p: crewbook.CrewPlayer, pvp: crewbook.CrewPvp | None = None) -> str | None:
    """Why one player's entry is outside what a sync takes, in wowlab's
    words (as the reader gives its reasons), never quoting the entry."""
    if not _key_ok(p.key):
        return (
            f"the key is not Name-Realm within {KEY_MAX_BYTES} bytes of text, so it is not synced"
        )
    for field, kind, limit in _TEXTS:
        text = getattr(p, field)
        if text is None:
            continue
        size = _bytes_of(text)
        if size is None or size > limit:
            return f"the {kind} is not text of at most {limit} bytes, so it is not synced"
    if p.met is not None and not 0 <= p.met <= COUNTER_MAX:
        return f"the count of groups is over {COUNTER_MAX:,}, so it is not synced"
    if p.level is not None and not 1 <= p.level <= LEVEL_MAX:
        return f"the level is over {LEVEL_MAX:,}, so it is not synced"
    for field, kind in _TIMES:
        value = getattr(p, field)
        if value is not None and not TIME_MIN <= value <= _latest():
            return (
                f"the {kind} is before 2004 or more than {TIME_AHEAD // 86_400} days after "
                "this computer's clock, so it is not synced"
            )
    if p.guid is not None and not crewbook.guid_shaped(p.guid):
        return "the game's id of the character is not in the game's shape, so it is not synced"
    if p.honor_level is not None and not 0 <= p.honor_level <= HONOR_LEVEL_MAX:
        return f"the honor level is over {HONOR_LEVEL_MAX:,}, so it is not synced"
    return _pvp_problem(pvp) or _score_problem(p.score)


def _score_problem(score: crewbook.CrewScore | None) -> str | None:
    """Why a player's scoreboard line is outside what a sync takes (its
    time, how many fields, each field's name, text and number), never
    quoting it."""
    if score is None:
        return None
    if score.at is not None and not TIME_MIN <= score.at <= _latest():
        return (
            f"the scoreboard line's time is before 2004 or more than {TIME_AHEAD // 86_400} "
            "days after this computer's clock, so it is not synced"
        )
    if len(score.values) > SCORE_FIELDS_MAX:
        return f"the scoreboard line holds more than {SCORE_FIELDS_MAX} fields, so it is not synced"
    for name, value in score.values.items():
        size = _bytes_of(name)
        if size is None or size > SCORE_NAME_MAX_BYTES:
            return (
                f"a scoreboard field's name is not text of at most {SCORE_NAME_MAX_BYTES} "
                "bytes, so it is not synced"
            )
        if isinstance(value, str):
            size = _bytes_of(value)
            if size is None or size > TEXT_MAX_BYTES:
                return (
                    f"a scoreboard field is not text of at most {TEXT_MAX_BYTES} bytes, so it "
                    "is not synced"
                )
        elif not isinstance(value, bool) and not -SCORE_NUMBER_MAX <= value <= SCORE_NUMBER_MAX:
            return (
                f"a scoreboard number is beyond {SCORE_NUMBER_MAX:,} either way, so it is not "
                "synced"
            )
    return None


def entry_problems(book: crewbook.CrewBook) -> list[crewbook.UnreadEntry]:
    """The entries the reader read but a sync does not take, each with its
    stored key and why. With the reader's own `unread`, they are what
    `crew list` lists as entries that could not be read."""
    out = []
    for p in book.players:
        why = _entry_problem(p, book.pvp.get(p.key))
        if why is not None:
            out.append(crewbook.UnreadEntry(key=p.key, reason=why))
    return out


def _match_problem(m: crewbook.CrewMatch) -> str | None:
    """Why one entry of `matches` is outside what a sync takes, in wowlab's
    words, never quoting it: its time, its map id (which tells it apart),
    its map's name, and every key in `team` and `foes` (Name-Realm, each
    once, on one side)."""
    if not TIME_MIN <= m.at <= _latest():
        return (
            f"its time is before 2004 or more than {TIME_AHEAD // 86_400} days after this "
            "computer's clock, so it is not synced"
        )
    if m.map_id is None:
        return "it has no map id, so it is not synced"
    if m.map is not None:
        size = _bytes_of(m.map)
        if size is None or size > TEXT_MAX_BYTES:
            return f"its map is not text of at most {TEXT_MAX_BYTES} bytes, so it is not synced"
    if len(m.team) > MATCH_SIDE_MAX or len(m.foes) > MATCH_SIDE_MAX:
        return f"a side of it lists more than {MATCH_SIDE_MAX} names, so it is not synced"
    keys = [*m.team, *m.foes]
    if not all(_key_ok(k) for k in keys):
        return (
            f"a name in it is not Name-Realm within {KEY_MAX_BYTES} bytes of text, so it is "
            "not synced"
        )
    if len(set(keys)) != len(keys):
        return "a name in it is listed twice, so it is not synced"
    if m.kind == "shuffle" and (m.foes or m.won is not None):
        return "it is a solo shuffle with sides or a result, so it is not synced"
    return None


def match_problems(book: crewbook.CrewBook) -> list[crewbook.UnreadMatch]:
    """The entries of `matches` the reader read but a sync does not take,
    each with its place in the list and why. With the reader's own
    `matches_unread`, they are the matches a sync leaves out."""
    out = []
    for m in book.matches:
        why = _match_problem(m)
        if why is not None:
            out.append(crewbook.UnreadMatch(position=m.position, reason=why))
    return out


def _unsynced(book: crewbook.CrewBook) -> frozenset[str]:
    """The keys of the entries the saved file holds that a sync does not
    take: the reader's unread ones and those outside a sync's bounds."""
    return frozenset([u.key for u in book.unread] + [u.key for u in entry_problems(book)])


def _usable(book: crewbook.CrewBook) -> tuple[crewbook.CrewBook, int, int]:
    """The book with only the entries and matches a sync takes, and how
    many of each it left out (the reader's unread ones included)."""
    kept = [p for p in book.players if _entry_problem(p, book.pvp.get(p.key)) is None]
    left_out = len(book.players) - len(kept) + len(book.unread)
    games = [m for m in book.matches if _match_problem(m) is None]
    games_out = len(book.matches) - len(games) + len(book.matches_unread)
    return book.model_copy(update={"players": kept, "matches": games}), left_out, games_out


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


def _added(total: int, before: int, seen: int, mode: Mode) -> int:
    """A counter's total after a sync (rule 2): added to by the difference
    from the value the saved file showed at the last sync (`before`), or by
    the whole new value when that is lower (the game dropped the player and
    started again) or the book is another one; from a book put back, the
    larger of the two, never counting twice."""
    if mode == "restored":
        return max(total, seen)
    if mode == "new_book" or seen < before:
        return total + seen
    return total + seen - before


def _with_pvp(new: _Row, row: _Row | None, pvp: crewbook.CrewPvp | None, mode: Mode) -> _Row:
    """`new` with the four match counters of §21.2 synced by rule 2 (each
    beside the value the saved file showed, as `met` is) and the latest
    match taken from the saved file when it is not older."""
    pvp = pvp or crewbook.CrewPvp()
    for field in ("allied", "faced", "allied_won", "faced_won"):
        value = getattr(pvp, field)
        seen = value if value is not None else 0
        total = getattr(row, field) if row is not None else 0
        before = getattr(row, field + "_seen") if row is not None else 0
        setattr(new, field, _added(total, before, seen, mode) if row is not None else seen)
        setattr(new, field + "_seen", seen)
    newer = pvp.pvp_last is not None and (new.pvp_last is None or pvp.pvp_last >= new.pvp_last)
    if row is None or newer:
        new.pvp_last = pvp.pvp_last if pvp.pvp_last is not None else new.pvp_last
        new.pvp_map = pvp.pvp_map if pvp.pvp_map is not None else new.pvp_map
        new.pvp_side = pvp.pvp_side if pvp.pvp_side is not None else new.pvp_side
    return new


_SEEN_FIELDS = ("guid", "race", "faction", "spec", "honor_level")


def _newest(*times: int | None) -> int | None:
    found = [t for t in times if t is not None]
    return max(found) if found else None


def _with_seen(
    new: _Row, row: _Row | None, p: crewbook.CrewPlayer, pvp: crewbook.CrewPvp | None
) -> _Row:
    """`new` with what M20-11 records of the player (§21.3, as built by
    M20-12): the latest value wins. The GUID, race, faction, spec and honor
    level come from the saved file where it holds them and its entry is not
    older than the row (by the later of its latest group and latest match);
    the scoreboard line where its time is not older than the row's. A field
    the saved file leaves out keeps the row's value."""
    pvp_last = pvp.pvp_last if pvp is not None else None
    file_newest = _newest(p.last, pvp_last)
    row_newest = _newest(row.last, row.pvp_last) if row is not None else None
    if row is None or row_newest is None or (file_newest is not None and file_newest >= row_newest):
        for field in _SEEN_FIELDS:
            value = getattr(p, field)
            if value is not None:
                setattr(new, field, value)
    score = p.score
    if score is not None and (
        row is None
        or row.score is None
        or row.score_at is None
        or (score.at is not None and score.at >= row.score_at)
    ):
        new.score = _score_text(score)
        new.score_at = score.at
    return new


def _merged(
    row: _Row | None,
    p: crewbook.CrewPlayer,
    mode: Mode,
    now: int,
    pvp: crewbook.CrewPvp | None = None,
) -> _Row:
    """The row after one player of the saved file (and their match
    counters, `pvp`) is synced into it."""
    seen = p.met if p.met is not None else 0
    name, _, realm = p.key.partition("-")
    if row is None:
        first = _Row(
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
        return _with_seen(_with_pvp(first, None, pvp, mode), None, p, pvp)
    new = dataclasses.replace(row, in_book=1, met_seen=seen)
    new = _with_seen(new, row, p, pvp)
    # Rule 2: counters are added to, never replaced.
    new.met = _added(row.met, row.met_seen, seen, mode)
    new = _with_pvp(new, row, pvp, mode)
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
    new.rating, new.rating_own = _mark(row.rating, row.rating_own, p.rating, mode)
    new.note, new.note_own = _mark(row.note, row.note_own, p.note, mode)
    if (new.rating, new.note) != (row.rating, row.note):
        new.marks_changed = now
    if (new.rating, new.note) == marks:
        new.marks_agreed = now
    return new


def _mark(kept: str | None, own: int, given: str | None, mode: Mode) -> tuple[str | None, int]:
    """One field (the rating or the note) after a sync: its value and whether
    it is still the database's own. Rule 2, the saved file's value (`given`),
    except a field the database holds as its own (decision 20): that one
    agrees again, and stops being its own, when the file says the same;
    from a held book it is kept (§21.3); in a normal sync it is kept where
    the file has none and gives way to the file's where it has one (rated or
    noted again in game)."""
    if own and given == kept:
        return kept, 0
    if own and (mode != "normal" or given is None):
        return kept, 1
    return given, 0


def _kept_on_return(old: _Row, new: _Row, p: crewbook.CrewPlayer) -> tuple[bool, bool]:
    """Whether a sync kept the database's own rating, and its own note, for
    a player the game's list had dropped and now holds again."""
    if old.in_book:
        return False, False
    return (
        bool(new.rating_own) and new.rating is not None and p.rating is None,
        bool(new.note_own) and new.note is not None and p.note is None,
    )


def _left(row: _Row) -> _Row:
    """A row whose player the saved file no longer holds."""
    return dataclasses.replace(
        row,
        in_book=0,
        met_seen=0,
        allied_seen=0,
        faced_seen=0,
        allied_won_seen=0,
        faced_won_seen=0,
    )


def _changes(
    rows: dict[str, _Row],
    book: crewbook.CrewBook,
    mode: Mode,
    now: int,
    unsynced: frozenset[str] = frozenset(),
) -> tuple[list[_Row], list[_Row], SyncPlan]:
    """(rows to insert, rows to update, the plan). `unsynced`: the keys of
    entries the saved file holds but this sync does not take (unread, or
    outside its bounds); such a player is still in the game's list, so
    nothing about their row changes, and above all their last shown counts
    are not reset (rule 2 adds the whole value only when the game dropped
    the player)."""
    added: list[_Row] = []
    updated: list[_Row] = []
    ratings = notes = left = ratings_gone = notes_gone = ratings_kept = notes_kept = 0
    keys = set()
    for p in book.players:
        keys.add(p.key)
        old = rows.get(p.key)
        new = _merged(old, p, mode, now, book.pvp.get(p.key))
        if old is None:
            added.append(new)
            continue
        rating_kept, note_kept = _kept_on_return(old, new, p)
        ratings_kept += rating_kept
        notes_kept += note_kept
        if _same(old, new):
            continue
        updated.append(new)
        ratings += old.rating != new.rating
        notes += old.note != new.note
        ratings_gone += old.rating is not None and new.rating is None
        notes_gone += old.note is not None and new.note is None
    for key, row in rows.items():
        seen = row.met_seen or row.allied_seen or row.faced_seen
        if key in unsynced:
            continue
        if key not in keys and (row.in_book or seen or row.allied_won_seen or row.faced_won_seen):
            updated.append(_left(row))
            left += bool(row.in_book)
    plan = SyncPlan(
        players_added=len(added),
        players_updated=sum(1 for r in updated if r.in_book),
        ratings_changed=ratings,
        notes_changed=notes,
        ratings_removed=ratings_gone,
        notes_removed=notes_gone,
        left_game_list=left,
        ratings_kept=ratings_kept,
        notes_kept=notes_kept,
    )
    return added, updated, plan


def kept_words(plan: SyncPlan) -> str:
    """The sentence a sync adds when it kept ratings or notes given at the
    desk to players the game's list had dropped and holds again (with a
    leading space; empty when it kept none). Counts only, never a name."""

    def players(n: int) -> str:
        return f"{n} player" if n == 1 else f"{n} players"

    parts = []
    if plan.ratings_kept:
        parts.append(f"your rating for {players(plan.ratings_kept)}")
    if plan.notes_kept:
        parts.append(f"your note for {players(plan.notes_kept)}")
    if not parts:
        return ""
    return (
        f" It kept {' and '.join(parts)} the game's list had dropped and holds again, "
        "which the game itself does not show."
    )


def _matches_words(n: int) -> str:
    """The sync sentence's count of matches added (with a leading comma;
    empty when none was)."""
    if not n:
        return ""
    return f", {n} {'match' if n == 1 else 'matches'} added"


def _same(a: _Row, b: _Row) -> bool:
    """Equal but for the agreement time, which every sync moves."""
    return dataclasses.replace(a, marks_agreed=0) == dataclasses.replace(b, marks_agreed=0)


def _identity(book: crewbook.CrewBook, target: Target) -> tuple[object, object]:
    return (book.started, target.account)


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _daily_copy(conn: sqlite3.Connection, path: Path) -> None:
    """Keep today's copy of the database (made once a day, before the first
    change), and the newest `DAILY_COPIES` in all. Called under `conn`'s
    write lock, before it changes anything: the copy is read through a
    second connection, which may read while the lock is held (a backup from
    the locking connection itself would wait on its own lock)."""
    del conn
    dest = path.with_name(f"{path.name}.daily-{_today()}")
    if not os.path.lexists(dest):
        reader = _connect(path)
        try:
            _copy(reader, dest)
        finally:
            reader.close()
    dated = sorted(
        (m.group(1), f)
        for f in path.parent.iterdir()
        if f.name.startswith(path.name) and (m := _DAILY.search(f.name[len(path.name) :]))
    )
    for _, old in dated[:-DAILY_COPIES]:
        with contextlib.suppress(FileNotFoundError):
            old.unlink()


def _new_matches(conn: sqlite3.Connection, book: crewbook.CrewBook) -> list[crewbook.CrewMatch]:
    """The book's matches the database does not keep yet, each once (rule 3:
    told apart by `at` and `map_id`, so a match the game's list dropped and
    named again, or that a book lists twice, is not added again)."""
    held = {(int(at), int(map_id)) for at, map_id in conn.execute(_SQL_MATCH_IDS)}
    out: list[crewbook.CrewMatch] = []
    for m in book.matches:
        if m.map_id is None or (m.at, m.map_id) in held:
            continue
        held.add((m.at, m.map_id))
        out.append(m)
    return out


def _write(
    conn: sqlite3.Connection,
    added: list[_Row],
    updated: list[_Row],
    meta: dict[str, object],
    games: Sequence[crewbook.CrewMatch] = (),
) -> None:
    conn.executemany(_SQL_PLAYER_INSERT, [(r.key, *r.values()) for r in added])
    conn.executemany(_SQL_PLAYER_UPDATE, [(*r.values(), r.key) for r in updated])
    for m in games:
        won = None if m.won is None else int(m.won)
        # A solo shuffle is an arena, marked as one (schema 3, `_MIGRATIONS`).
        shuffle = m.kind == "shuffle"
        kind = "arena" if shuffle else m.kind
        cursor = conn.execute(_SQL_MATCH_INSERT, (m.at, m.map, m.map_id, kind, won, int(shuffle)))
        match_id = cursor.lastrowid
        conn.executemany(
            _SQL_MATCH_PLAYER_INSERT,
            [(match_id, k, "team") for k in m.team] + [(match_id, k, "foes") for k in m.foes],
        )
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
    reread: Callable[[], bytes] | None = None,
) -> SyncOutcome:
    """Sync the crew book (`book`, parsed from the saved file's `data`)
    into wowlab's crew database (§21.3). Never raises for a held, refused,
    skipped or misplaced sync: the outcome says why. `by_hand` is
    `wowlab crew sync`: a held book is then shown as a plan to `confirm`
    (with the hold's sentence) and synced only on a yes. `reread` reads the
    saved file again: under the database's write lock, a sync whose saved
    file changed since `data` was read, or that the journal shows was
    written since, is skipped (a forget may have written it meanwhile)."""
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
    unsynced = _unsynced(book)
    book, left_out, matches_out = _usable(book)
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
        ident = _ident(path)
        outcome = _sync_open(
            conn,
            path,
            target,
            data,
            book,
            by_hand=by_hand,
            confirm=confirm,
            reread=reread,
            ident=ident,
            unsynced=unsynced,
        )
        if not (left_out or matches_out) or outcome.state not in ("synced", "unchanged"):
            return outcome
        words = outcome.words
        if left_out:
            entries = "1 entry" if left_out == 1 else f"{left_out} entries"
            verb = "was" if left_out == 1 else "were"
            words += f" {entries} of the book could not be read and {verb} not synced."
        if matches_out:
            games = "1 match" if matches_out == 1 else f"{matches_out} matches"
            verb = "was" if matches_out == 1 else "were"
            words += f" {games} of the book could not be read and {verb} not synced."
        return outcome.model_copy(
            update={"left_out": left_out, "matches_left_out": matches_out, "words": words}
        )
    except (_Replaced, FileNotFoundError):
        return SyncOutcome(
            state="skipped",
            words=(
                "wowlab's crew database was deleted or replaced while this sync ran (a forget "
                "of every player does that), so nothing was synced or made again; run the "
                "command again."
            ),
            database=str(path),
        )
    except _StaleBook:
        return SyncOutcome(
            state="skipped",
            words=(
                "The crew book changed while wowlab was syncing it (another wowlab command "
                "wrote it), so this sync was skipped and answers from the database as it is."
            ),
            database=str(path),
        )
    except sqlite3.OperationalError as exc:
        if _locked(exc):
            return _skipped(path)
        raise CrewDbError(f"The crew database {path} could not be synced ({exc}).") from exc
    except sqlite3.Error as exc:
        raise CrewDbError(f"The crew database {path} could not be synced ({exc}).") from exc
    finally:
        conn.close()


class _StaleBook(Exception):  # noqa: N818 - a signal, not an error
    """The saved file was written after this sync read it."""


class _Replaced(Exception):  # noqa: N818 - a signal, not an error
    """The database at the path is no longer the file this sync opened."""


def _ident(path: Path) -> tuple[int, int]:
    st = path.stat()
    return (st.st_dev, st.st_ino)


def _still(path: Path, ident: tuple[int, int] | None) -> None:
    """Under the lock: the file at `path` is still the one opened."""
    if ident is None:
        return
    try:
        now = _ident(path)
    except FileNotFoundError:
        raise _Replaced from None
    if now != ident:
        raise _Replaced


def _written_since(
    target: Target, data: bytes, newest: str | None, reread: Callable[[], bytes] | None
) -> bool:
    """Whether the saved file changed since `data` was read, or the journal
    holds a record after `newest` that wrote it (read under the lock)."""
    if reread is not None:
        try:
            if reread() != data:
                return True
        except (OSError, WowlabError):
            return True
    try:
        records = guard.history()
    except (guard.GuardError, OSError, ValueError):
        # Held already, and confirmed by hand: the bytes decide.
        return False
    ids = [r.id for r in records]
    later = records[ids.index(newest) + 1 :] if newest in ids else records
    if newest is not None and newest not in ids:
        return True
    want = target.file.replace("\\", "/").casefold()
    return any(
        not r.rolled_back
        and _same_folder(r.flavor_path, target.flavor_path)
        and any(c.path.replace("\\", "/").casefold() == want for c in r.paths)
        for r in later
    )


def _sync_open(
    conn: sqlite3.Connection,
    path: Path,
    target: Target,
    data: bytes,
    book: crewbook.CrewBook,
    *,
    by_hand: bool,
    confirm: Callable[[SyncPlan, str], bool] | None,
    reread: Callable[[], bytes] | None = None,
    ident: tuple[int, int] | None = None,
    unsynced: frozenset[str] = frozenset(),
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
                _still(path, ident)
                conn.execute(_SQL_META_SET, ("journal_record", newest))
        return SyncOutcome(
            state="unchanged",
            words="wowlab's crew database already holds everything in the crew book.",
            database=str(path),
        )
    mode: Mode = "new_book" if "other_book" in reasons else "restored" if reasons else "normal"
    now = time.time_ns()
    if reasons:
        _, _, plan = _changes(_rows(conn), book, mode, now, unsynced)
        plan = plan.model_copy(update={"matches_added": len(_new_matches(conn, book))})
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
    with _transaction(conn):
        # Under the write lock: the file is still the one opened, the book
        # was not written since it was read, and only then today's copy is
        # made, of the state this lock protects.
        _still(path, ident)
        if _written_since(target, data, newest, reread):
            raise _StaleBook
        if synced_before:
            _daily_copy(conn, path)
        rows = _rows(conn)
        added, updated, plan = _changes(rows, book, mode, now, unsynced)
        games = _new_matches(conn, book)
        plan = plan.model_copy(update={"matches_added": len(games)})
        _write(conn, added, updated, _meta_rows(target, book, data, newest), games)
    total = len(rows) + len(added)
    return SyncOutcome(
        state="synced",
        words=(
            f"Synced the crew book into wowlab's crew database: {plan.players_added} added, "
            f"{plan.players_updated} updated{_matches_words(plan.matches_added)}; it keeps "
            f"{total:,} {'player' if total == 1 else 'players'}.{kept_words(plan)}"
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
    _clear_temps(source)
    moves = [(source, dest)]
    journal = source.with_name(source.name + "-journal")
    if os.path.lexists(journal):  # a hot journal travels with its database
        moves.append((journal, dest.with_name(dest.name + "-journal")))
    for f in _copies_of(source):
        moves.append((f, dest.with_name(dest.name + f.name[len(source.name) :])))
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


def _copies_of(path: Path) -> list[Path]:
    """The copies wowlab keeps beside the database at `path`: daily ones
    and the one kept before a schema change."""
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


def _temps_of(path: Path) -> list[Path]:
    """Temporary files a copy of the database at `path` is made in
    (`.<name>….tmp`, and SQLite's `-journal` of one): a run killed while it
    made a copy leaves a whole copy of the database there."""
    try:
        entries = list(path.parent.iterdir())
    except OSError:
        return []
    return sorted(
        f
        for f in entries
        if f.name.startswith("." + path.name)
        and (f.name.endswith(".tmp") or f.name.endswith(".tmp-journal"))
    )


def _clear_temps(path: Path) -> None:
    """Delete every temporary copy left beside the database (a run in
    progress that loses its own is told by `_copy`, and makes none)."""
    for f in _temps_of(path):
        with contextlib.suppress(FileNotFoundError):
            f.unlink()


_SQL_COUNT_PLAYERS = "SELECT count(*), coalesce(sum(in_book), 0) FROM players"
_SQL_COUNT_MATCHES = "SELECT count(*) FROM matches"


class DatabaseSummary(_Model):
    """One crew database as `doctor` reports it (ADR-0036 term 4, §21.4):
    the game account folder it is for, its path, its size in bytes, how
    many copies wowlab keeps beside it, and how many players and matches it
    keeps (None, with `reason`, when it cannot be read). Never a row, and
    never `meta`'s install root."""

    account: str
    path: str
    size: int
    copies: int
    players: int | None
    not_in_game_list: int | None
    matches: int | None
    reason: str | None = None


def _counts(path: Path) -> tuple[int, int, int] | str:
    """(players, of them not in the game's list, matches), read-only; or
    why they could not be read, in wowlab's words."""
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=LOCK_WAIT_SECONDS)
    except sqlite3.Error:
        return "wowlab could not open it"
    try:
        if conn.execute(_SQL_READ_APP_ID).fetchone()[0] != APPLICATION_ID:
            return "it is not a crew database wowlab made"
        version = conn.execute(_SQL_READ_VERSION).fetchone()[0]
        if version > SCHEMA:
            return "a newer wowlab wrote it; update wowlab to read it"
        players, in_book = conn.execute(_SQL_COUNT_PLAYERS).fetchone()
        (matches,) = conn.execute(_SQL_COUNT_MATCHES).fetchone()
    except sqlite3.Error:
        return "wowlab could not read it now"
    finally:
        conn.close()
    return int(players), int(players) - int(in_book), int(matches)


def summaries(flavor_folder: str) -> list[DatabaseSummary]:
    """Every crew database wowlab keeps for this flavor folder, one per game
    account, as `doctor` reports them: read-only, never a row (§21.3, "It is
    the only copy"). Empty when there is none."""
    try:
        folder = _under_crew(crew_root() / _component(flavor_folder, "flavor"), depth=1)
        entries = sorted(folder.iterdir())
    except (CrewDbError, OSError):
        return []
    out: list[DatabaseSummary] = []
    for f in entries:
        if not f.name.endswith(SUFFIX) or f.name.startswith("."):
            continue
        try:
            st = os.lstat(f)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        counts = _counts(f)
        read = not isinstance(counts, str)
        out.append(
            DatabaseSummary(
                account=f.name[: -len(SUFFIX)],
                path=str(f),
                size=st.st_size,
                copies=len(_copies_of(f)),
                players=counts[0] if not isinstance(counts, str) else None,
                not_in_game_list=counts[1] if not isinstance(counts, str) else None,
                matches=counts[2] if not isinstance(counts, str) else None,
                reason=None if read else str(counts),
            )
        )
    return out


def copies(flavor_folder: str, account: str) -> list[Path]:
    """The copies wowlab keeps beside the database: daily ones and the one
    kept before a schema change."""
    return _copies_of(database_path(flavor_folder, account))


# ─── forget (§21.3, "Writing"; brought forward from M20-02 on 2026-11-19) ───

_SQL_FORGET_PLAYER = "DELETE FROM players WHERE player_key = ?"
_SQL_FORGET_IN_MATCHES = "DELETE FROM match_players WHERE player_key = ?"
_SQL_PLAYER_COLUMNS = "PRAGMA table_info(players)"
_SQL_SCORES = "SELECT player_key, score FROM players WHERE score IS NOT NULL"
_SQL_DROP_SCORE = "UPDATE players SET score = NULL, score_at = NULL WHERE player_key = ?"


def _gone(path: Path) -> None:
    """Remove a file and SQLite's rollback journal beside it."""
    for f in (path, path.with_name(path.name + "-journal")):
        with contextlib.suppress(FileNotFoundError):
            f.unlink()


def _has_score(conn: sqlite3.Connection) -> bool:
    """Whether this file's players table has the scoreboard line (a copy
    kept before schema 3 does not)."""
    return any(r[1] == "score" for r in conn.execute(_SQL_PLAYER_COLUMNS))


def _forget_rows(conn: sqlite3.Connection, keys: Sequence[str]) -> None:
    """Delete the players `keys` (those keys only: a GUID never adds one,
    §21.3) and take them out of every match; then drop, from every other
    player, a kept scoreboard line whose text holds one of them (the line,
    not the row), so no copy of the name stays in another player's data."""
    for key in keys:
        conn.execute(_SQL_FORGET_PLAYER, (key,))
        conn.execute(_SQL_FORGET_IN_MATCHES, (key,))
    if not _has_score(conn):
        return
    wanted = [crewbook.fold(k) for k in keys if crewbook.fold(k)]
    held = [(str(k), s) for k, s in conn.execute(_SQL_SCORES).fetchall()]
    for key, score in held:
        if any(w in text for w in wanted for text in _score_texts(score)):
            conn.execute(_SQL_DROP_SCORE, (key,))


def _score_texts(score: object) -> list[str]:
    """A kept scoreboard line as the texts a name could hide in, folded as
    names are compared (§20.2): the stored JSON as it is, and each field
    name and text value it decodes to (an escaped spelling included). Text
    that is not JSON is searched as it is."""
    if not isinstance(score, str):
        return []
    out = [crewbook.fold(score)]
    try:
        loaded = json.loads(score)
    except ValueError:
        return out
    if isinstance(loaded, dict):
        for k, v in loaded.items():
            out.append(crewbook.fold(str(k)))
            if isinstance(v, str):
                out.append(crewbook.fold(v))
    return out


def _forget_in(path: Path, keys: Sequence[str]) -> None:
    """Delete the players `keys` from one copy, with `secure_delete` on, in
    one transaction, and leave no rollback journal."""
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
        raise _not_ours(path)
    conn = _connect(path)
    try:
        if conn.execute(_SQL_READ_APP_ID).fetchone()[0] != APPLICATION_ID:
            raise _not_ours(path)
        _settings(conn)
        with _transaction(conn):
            _forget_rows(conn, keys)
    finally:
        conn.close()


def holds(flavor_folder: str, account: str) -> list[str]:
    """The keys of every player wowlab's crew database keeps for this book:
    its players, and any key a match it keeps names (a player the game's
    list dropped before a sync saw them may be in a match only)."""
    keys = [r.key for r in read(flavor_folder, account)]

    def use(conn: sqlite3.Connection) -> list[str]:
        return [str(r[0]) for r in conn.execute(_SQL_MATCH_KEYS)]

    in_matches = _read_with(flavor_folder, account, use)
    more = in_matches if isinstance(in_matches, list) else []
    return list(dict.fromkeys([*keys, *sorted(more)]))


class _Holding:
    """A change to wowlab's crew database made in the same command as a
    write of the saved file: `hold()` opens the database and takes its write
    lock before the saved file is written, so no sync can commit what it
    read before that write; `release()` lets go of it. Without a database
    there is nothing to hold."""

    _verb = "change"

    def __init__(self, flavor_folder: str, account: str) -> None:
        self.path = database_path(flavor_folder, account)
        self._conn: sqlite3.Connection | None = None

    def hold(self) -> None:
        """Open the database and take its write lock; a lock another wowlab
        holds past `LOCK_WAIT_SECONDS` is refused before anything is written."""
        try:
            conn = _open(self.path, create=False)
        except sqlite3.OperationalError as exc:
            raise self._busy(exc) from exc
        if conn is None:
            return
        try:
            conn.execute(_SQL_BEGIN)
        except sqlite3.OperationalError as exc:
            conn.close()
            raise self._busy(exc) from exc
        self._conn = conn

    def _busy(self, exc: Exception) -> CrewDbError:
        return CrewDbError(
            f"Another wowlab is using the crew database {self.path} ({exc}), so the "
            f"{self._verb} did not start. Nothing was changed; run it again."
        )

    def release(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):
                conn.execute(_SQL_ROLLBACK)
            conn.close()


class Forgetting(_Holding):
    """A forget of players (`keys`; None: every one) from wowlab's crew
    database and every copy it keeps. `hold()` takes the database's write
    lock before the saved file is written, so no sync can commit what it
    read before that write; `finish()` removes the players under that lock
    (or, for every player, deletes the database and its copies) and then
    rewrites each copy, listed only now, after the database was opened (and
    so after any copy a schema change made); `release()` lets go. Not
    undoable: snapshots do not cover the database."""

    _verb = "forget"

    def __init__(self, flavor_folder: str, account: str, keys: Sequence[str] | None) -> None:
        super().__init__(flavor_folder, account)
        self.keys = None if keys is None else list(keys)

    def finish(self) -> int:
        """Remove the players (or everything) and return how many files
        were rewritten or deleted. All of it happens while the database's
        write lock is held, so no sync copies or recreates what is being
        removed."""
        conn, self._conn = self._conn, None
        if self.keys is None:
            files = [self.path, *_copies_of(self.path), *_temps_of(self.path)]
            held_open: list[Path] = []
            for f in files:
                try:
                    _gone(f)
                except PermissionError:  # an open file on Windows: after the close
                    held_open.append(f)
            if conn is not None:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute(_SQL_ROLLBACK)
                conn.close()
            for f in held_open:
                _gone(f)
            return len(files)
        done = 0
        try:
            if conn is not None:
                _forget_rows(conn, self.keys)
                done += 1
            _clear_temps(self.path)
            for f in _copies_of(self.path):
                try:
                    _forget_in(f, self.keys)
                except (sqlite3.DatabaseError, CrewDbError, OSError):
                    _gone(f)  # a copy wowlab cannot open still may hold them: deleted
                done += 1
            if conn is not None:
                conn.execute(_SQL_COMMIT)
        except sqlite3.Error as exc:
            if conn is not None:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute(_SQL_ROLLBACK)
            raise CrewDbError(f"The crew database {self.path} was not changed ({exc}).") from exc
        finally:
            if conn is not None:
                conn.close()
        return done


def forget(flavor_folder: str, account: str, keys: Sequence[str] | None) -> int:
    """`Forgetting` in one step, for a forget that writes no game file."""
    forgetting = Forgetting(flavor_folder, account, keys)
    forgetting.hold()
    try:
        return forgetting.finish()
    finally:
        forgetting.release()


# ─── rate and note (§21.3, "Writing"; M20-02) ────────────────────────────────

_SQL_PLAYER_MARKS = "SELECT rating, note FROM players WHERE player_key = ?"
_SQL_SET_MARK: dict[str, str] = {
    "rating": "UPDATE players SET rating = ?, rating_own = ?, marks_changed = ?"
    " WHERE player_key = ?",
    "note": "UPDATE players SET note = ?, note_own = ?, marks_changed = ? WHERE player_key = ?",
}

MarkField = Literal["rating", "note"]


class RowChanged(CrewDbError):  # noqa: N818 - the player's words: the row changed
    """The player's row is not as the plan read it: nothing was written."""


def _mark_value(field: MarkField, value: str | None) -> str | None:
    """`value`, refused unless it is what the desk may store in `field`:
    good, bad or none for a rating; for a note, text that passes the same
    rule as a note in the saved file (`crewedit.check_note`)."""
    if field == "rating":
        if value not in (None, "good", "bad"):
            raise CrewDbError("A rating is good, bad or none. Nothing was changed.")
        return value
    if value is not None and crewedit.check_note(value) != value:
        raise CrewDbError("The note is not one wowlab stores as it is. Nothing was changed.")
    return value


def _set_mark(
    conn: sqlite3.Connection, key: str, field: MarkField, value: str | None, *, own: bool
) -> bool:
    """Set the player's rating or note, whether it is the database's own
    (a sync then keeps it, §21.3, decision 20), and the time it changed.
    Whether the database holds the player."""
    done = conn.execute(_SQL_SET_MARK[field], (value, int(own), time.time_ns(), key))
    return done.rowcount > 0


class Marking(_Holding):
    """A rating or note (`field`, `value`) on one player, written to wowlab's
    crew database after the saved file, in the same command. `hold()` takes
    the database's write lock before the saved file is written; `finish()`
    sets the row's field under that lock (a player the database does not
    keep yet is added by the next sync, from the saved file); `release()`
    lets go. The field it sets was written through the saved file, so it is
    never the database's own: a later sync of a book put back (an `undo` of
    this write) takes the file's value again. The other field is left as it
    is, its own or not (§21.3)."""

    def __init__(
        self,
        flavor_folder: str,
        account: str,
        key: str,
        field: MarkField,
        value: str | None,
    ) -> None:
        super().__init__(flavor_folder, account)
        self._verb = "rate" if field == "rating" else "note"
        self.key = key
        self.field: MarkField = field
        self.value = _mark_value(field, value)

    def finish(self) -> bool:
        """Set the field; whether the database held the player."""
        conn, self._conn = self._conn, None
        if conn is None:
            return False
        try:
            held = _set_mark(conn, self.key, self.field, self.value, own=False)
            conn.execute(_SQL_COMMIT)
        except sqlite3.Error as exc:
            with contextlib.suppress(sqlite3.Error):
                conn.execute(_SQL_ROLLBACK)
            raise CrewDbError(f"The crew database {self.path} was not changed ({exc}).") from exc
        finally:
            conn.close()
        return held


def marks(flavor_folder: str, account: str, key: str) -> tuple[str | None, str | None] | None:
    """The player's rating and note as wowlab's crew database keeps them;
    None when it does not keep the player (or there is no database)."""
    path = database_path(flavor_folder, account)
    try:
        conn = _open(path, create=False)
    except sqlite3.OperationalError as exc:
        raise CrewDbError(f"The crew database {path} cannot be read now ({exc}).") from exc
    if conn is None:
        return None
    try:
        row = conn.execute(_SQL_PLAYER_MARKS, (key,)).fetchone()
    except sqlite3.Error as exc:
        raise CrewDbError(f"The crew database {path} cannot be read ({exc}).") from exc
    finally:
        conn.close()
    return None if row is None else (_text(row[0]), _text(row[1]))


def mark_only(
    flavor_folder: str,
    account: str,
    key: str,
    field: MarkField,
    value: str | None,
    *,
    expect: tuple[str | None, str | None],
    file_value: str | None = None,
    in_file: bool = False,
) -> None:
    """Set a rating or note on a player only wowlab's crew database keeps
    (the game's list dropped them): no game file is written and nothing is
    journaled, so it cannot be undone. Refused (`RowChanged`) when the row's
    rating and note are no longer `expect`, as the plan read them.
    The value is the database's own (a sync keeps it, decision 20) unless
    the saved file holds the player (`in_file`) with the same value in that
    field (`file_value`): it then agrees with the file again."""
    value = _mark_value(field, value)
    path = database_path(flavor_folder, account)
    try:
        conn = _open(path, create=False)
    except sqlite3.OperationalError as exc:
        raise CrewDbError(
            f"Another wowlab is using the crew database {path} ({exc}). Nothing was changed; "
            "run it again."
        ) from exc
    if conn is None:
        raise RowChanged(
            f"wowlab keeps no crew database at {path} now, so nothing was changed; run the "
            "command again to see the new plan."
        )
    try:
        with _transaction(conn):
            row = conn.execute(_SQL_PLAYER_MARKS, (key,)).fetchone()
            if row is None or (_text(row[0]), _text(row[1])) != expect:
                raise RowChanged(
                    "The player's rating or note in wowlab's crew database changed after the "
                    "plan was made, or the player was forgotten, so nothing was written; run "
                    "the command again to see the new plan."
                )
            _set_mark(conn, key, field, value, own=not (in_file and value == file_value))
    except sqlite3.OperationalError as exc:
        raise CrewDbError(
            f"The crew database {path} was not changed ({exc}); run the command again."
        ) from exc
    finally:
        conn.close()


__all__ = [
    "APPLICATION_ID",
    "COUNTER_MAX",
    "DAILY_COPIES",
    "LOCK_WAIT_SECONDS",
    "MATCH_SIDE_MAX",
    "MAX_MATCHES",
    "MAX_PLAYERS",
    "SCHEMA",
    "AdoptOutcome",
    "CrewDbError",
    "CrewMatchRow",
    "CrewRow",
    "DatabaseSummary",
    "Forgetting",
    "MarkField",
    "Marking",
    "RowChanged",
    "SyncOutcome",
    "SyncPlan",
    "Target",
    "adopt",
    "check_book",
    "copies",
    "crew_root",
    "database_path",
    "entry_problems",
    "forget",
    "holds",
    "kept_words",
    "keys_on_map",
    "mark_only",
    "marks",
    "match_problems",
    "matches",
    "read",
    "summaries",
    "sync",
]
