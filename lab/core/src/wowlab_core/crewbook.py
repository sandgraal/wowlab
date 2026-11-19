"""crewbook: read the lab-addon's crew book (docs/LAB_PLAN.md §20.2, M19-02).

The crew book is the account-wide table `WowLabCrewDB` that the lab-addon
keeps, when the player turns it on in game, in the account's
`SavedVariables/WowLab.lua` beside `WowLabDB` (§20.1, ADR-0036). This module
reads it into `CrewBook` and `CrewPlayer`: read-only (L1), through the same
root-anchored read `labaddon` uses for that file, parsed by `luadata` as data
(no Lua is run, L3). It writes nothing and imports no network module
(ADR-0036 term 1).

The four outcomes §20.2 states:

- No `WowLabCrewDB` in the file (or it is nil): the book was never turned
  on; `state` is `not_turned_on`.
- `on` absent or false: the book is turned off; what it holds is still read
  and kept (`state` is `off`).
- A `schema` this reader does not know: refused (`CrewBookError`) with the
  number; nothing is read.
- A player entry that does not fit the record of §20.1: listed in `unread`
  with its key and a reason in wowlab's words (never the value it holds);
  every other entry is read.

Everything read from the file other than numbers is other people's text
(names, realms, guilds, instance names) or the player's own (notes). Nothing
here prints it; the CLI shows it only through its control-character
escaping path. A key is opaque: it is kept as stored, never split or
rewritten (§20.6); `match` compares a folded form of it.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from wowlab_core import labaddon, luadata
from wowlab_core.errors import WowlabError

CREW_VARIABLE = "WowLabCrewDB"
# The `schema` numbers this reader knows (§20.1: the book has its own,
# separate from the per-character record's).
KNOWN_SCHEMAS: tuple[int, ...] = (1,)
# The largest time this reader accepts: the last second of the year 9999, so
# a date can always be formatted.
TIME_MAX = 253_402_300_799
_INT_MAX = 2**53

State = Literal["not_turned_on", "off", "on"]
Rating = Literal["good", "bad"]
SortBy = Literal["last", "met", "name"]
RatedFilter = Literal["good", "bad", "any"]


class CrewBookError(WowlabError, ValueError):
    """The crew book cannot be read: an unknown schema, or a table whose top
    level does not fit §20.1. The message (`str`) is in the player's words;
    `detail` names the field of the file, for `--json` and for code. Neither
    quotes text from the file."""

    def __init__(self, message: str, detail: str) -> None:
        super().__init__(message)
        self.detail = detail


_UNREADABLE = "Nothing was read."

# What each top-level field is, in the player's words.
_FIELD_WORDS = {
    "started": "start date",
    "skipped": "count of skipped reads",
    "loads": "load count",
    "players": "player list",
    "current": "list of the players counted in the group you were in at the last save",
    "probe": "load record",
    "on": "on/off setting",
}


def _unreadable(field: str, detail: str) -> CrewBookError:
    return CrewBookError(
        f"The crew book's {_FIELD_WORDS[field]} is not readable. {_UNREADABLE}", detail
    )


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CrewPlayer(_Model):
    """One player the book keeps, as stored (§20.1). `key` is the stored
    `Name-Realm` key, unchanged. A field the entry does not hold is None."""

    key: str
    class_token: str | None = None  # `class`, the class token as the client gives it
    level: int | None = None
    role: str | None = None  # the last role the group set, if it set one
    guild: str | None = None
    met: int | None = None  # groups shared
    first: int | None = None  # time() of the first group
    last: int | None = None  # time() of the latest
    where: str | None = None  # the instance's name as the client gave it
    where_id: int | None = None
    rating: Rating | None = None
    note: str | None = None
    # Whether the key is in the book's saved `current` set: counted in the
    # group the player was in at the last save.
    in_current_group: bool = False
    # Keys of the entry this reader does not know, kept by name (L4 spirit).
    other_keys: list[str] = Field(default_factory=list)


class UnreadEntry(_Model):
    """A player entry that does not fit §20.1's record: its key as stored
    and why, in wowlab's words. The value it holds is never quoted."""

    key: str
    reason: str


class CrewBook(_Model):
    """`WowLabCrewDB` as read (§20.2)."""

    state: State
    schema_number: int | None = None
    started: int | None = None  # time() when the book was (last) started
    probe_loads: int | None = None  # the book's own load probe (§20.1)
    skipped: int | None = None  # reads the addon skipped, counted per attempt
    current_count: int = 0  # keys in the saved `current` set
    players: list[CrewPlayer] = Field(default_factory=list)
    unread: list[UnreadEntry] = Field(default_factory=list)


# ─── reading ─────────────────────────────────────────────────────────────────


def _whole(value: object, low: int, high: int) -> int | None:
    """`value` as a whole number within [low, high], else None. A Lua
    number may come back as a float with no fraction."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        value = int(value)
    if isinstance(value, int) and low <= value <= high:
        return value
    return None


def _key_text(key: object) -> str:
    """A table key as text: a string as it is, a number as `[n]`."""
    if isinstance(key, str):
        return key
    return f"[{key!r}]" if isinstance(key, (int, float)) else "[?]"


_TEXT_FIELDS = {"class": "class_token", "role": "role", "guild": "guild", "where": "where"}
_NUMBER_FIELDS: dict[str, tuple[str, int, int]] = {
    "level": ("level", 1, 2**31 - 1),
    "met": ("met", 0, _INT_MAX),
    "first": ("first", 0, TIME_MAX),
    "last": ("last", 0, TIME_MAX),
    "where_id": ("where_id", -(2**31), 2**31 - 1),
}
_NUMBER_WORDS = {
    "level": "a whole number of 1 or more",
    "met": "a whole number of 0 or more",
    "first": "a time",
    "last": "a time",
    "where_id": "a whole number",
}


def _player(key: str, value: object, current: frozenset[str]) -> CrewPlayer | UnreadEntry:
    """One entry of `players`, or why it cannot be read."""
    if not key:
        return UnreadEntry(key=key, reason="the key is empty")
    if not isinstance(value, dict):
        return UnreadEntry(key=key, reason="the entry is not a table")
    fields: dict[str, Any] = {"key": key, "in_current_group": key in current}
    other: list[str] = []
    for name, item in value.items():
        if name in _TEXT_FIELDS:
            if not isinstance(item, str):
                return UnreadEntry(key=key, reason=f"{name} is not text")
            fields[_TEXT_FIELDS[name]] = item
        elif name in _NUMBER_FIELDS:
            field, low, high = _NUMBER_FIELDS[name]
            number = _whole(item, low, high)
            if number is None:
                return UnreadEntry(key=key, reason=f"{name} is not {_NUMBER_WORDS[name]}")
            fields[field] = number
        elif name == "rating":
            if item not in ("good", "bad"):
                return UnreadEntry(key=key, reason='rating is neither "good" nor "bad"')
            fields["rating"] = item
        elif name == "note":
            if not isinstance(item, str):
                return UnreadEntry(key=key, reason="note is not text")
            fields["note"] = item
        else:
            other.append(_key_text(name))
    fields["other_keys"] = other
    return CrewPlayer(**fields)


def _top_number(table: dict[Any, Any], name: str, high: int = _INT_MAX) -> int | None:
    if name not in table or table[name] is None:
        return None
    number = _whole(table[name], 0, high)
    if number is None:
        raise _unreadable(name, f"{CREW_VARIABLE}.{name} is not a whole number of 0 or more")
    return number


def _schema(table: dict[Any, Any]) -> int:
    known = ", ".join(str(s) for s in KNOWN_SCHEMAS)
    schema = table.get("schema")
    if schema is None:
        raise CrewBookError(
            f"This crew book has no version number, so wowlab cannot tell how to read it. "
            f"{_UNREADABLE}",
            f"{CREW_VARIABLE} has no schema; this reader knows schema {known}",
        )
    number = _whole(schema, 0, 9999)
    if number is None:
        raise CrewBookError(
            f"This crew book's version number is not readable. {_UNREADABLE}",
            f"{CREW_VARIABLE}.schema is not a whole number from 0 to 9999; "
            f"this reader knows schema {known}",
        )
    if number not in KNOWN_SCHEMAS:
        raise CrewBookError(
            "This crew book was written by another version of the lab-addon (book version "
            f"{number}; this wowlab reads version {known}). Nothing was read; updating wowlab "
            "may read it.",
            f"{CREW_VARIABLE} is schema {number}; this reader knows schema {known} only",
        )
    return number


def _keyed(value: object, name: str) -> list[tuple[object, object]]:
    """A table's entries as (key, value): an empty table may come back as a
    list, and a table keyed 1..n always does."""
    if value is None:
        return []
    if isinstance(value, dict):
        return list(value.items())
    if isinstance(value, list):
        return [(i + 1, v) for i, v in enumerate(value)]
    raise _unreadable(name, f"{CREW_VARIABLE}.{name} is not a table")


def load_book(value: object) -> CrewBook:
    """`WowLabCrewDB` as `luadata.LuaDocument.to_python` gives it (None when
    the file does not assign it)."""
    if value is None:
        return CrewBook(state="not_turned_on")
    if not isinstance(value, dict):
        raise CrewBookError(
            f"The crew book in this file is not readable. {_UNREADABLE}",
            f"{CREW_VARIABLE} is not a table",
        )
    schema = _schema(value)
    on = value.get("on")
    if on is not None and not isinstance(on, bool):
        raise _unreadable("on", f"{CREW_VARIABLE}.on is neither true nor false")
    probe = value.get("probe")
    loads: int | None = None
    if probe is not None:
        if not isinstance(probe, dict):
            raise _unreadable("probe", f"{CREW_VARIABLE}.probe is not a table")
        loads = _top_number(probe, "loads")
    current = frozenset(k for k, v in _keyed(value.get("current"), "current") if isinstance(k, str))
    players: list[CrewPlayer] = []
    unread: list[UnreadEntry] = []
    for key, entry in _keyed(value.get("players"), "players"):
        if not isinstance(key, str):
            unread.append(UnreadEntry(key=_key_text(key), reason="the key is not a name"))
            continue
        read = _player(key, entry, current)
        if isinstance(read, CrewPlayer):
            players.append(read)
        else:
            unread.append(read)
    return CrewBook(
        state="on" if on is True else "off",
        schema_number=schema,
        started=_top_number(value, "started", TIME_MAX),
        probe_loads=loads,
        skipped=_top_number(value, "skipped"),
        current_count=len(current),
        players=players,
        unread=unread,
    )


def parse_book(data: bytes) -> CrewBook:
    """The crew book in an account `WowLab.lua`, from its bytes."""
    values = luadata.parse(data).to_python()
    return load_book(values.get(CREW_VARIABLE))


def read_book(path: Path, *, root: Path | None = None) -> CrewBook:
    """Read the crew book from the account's `WowLab.lua` (read-only; writes
    nothing, L1), from the install root one folder at a time as `labaddon`
    reads the same file (M16-65)."""
    return parse_book(labaddon.read_file_bytes(path, root=root))


# ─── finding, filtering and sorting ──────────────────────────────────────────

# What `fold` leaves out: spaces and the two apostrophes a typed realm name
# may hold (U+0027, U+2019).
_IGNORED = str.maketrans("", "", " '" + chr(0x2019))


def fold(text: str) -> str:
    """`text` as typed names are compared (§20.2): Unicode NFC and case
    folding, with spaces and the apostrophes U+0027 and U+2019 left out."""
    folded = unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())
    return folded.translate(_IGNORED)


def match(players: Sequence[CrewPlayer], typed: str) -> list[CrewPlayer]:
    """The players `typed` names: the one whose stored key is exactly
    `typed`, else every one whose folded key equals the folded `typed`."""
    exact = [p for p in players if p.key == typed]
    if exact:
        return exact[:1]
    want = fold(typed)
    return [p for p in players if fold(p.key) == want]


def select(
    players: Iterable[CrewPlayer],
    *,
    rated: RatedFilter | None = None,
    noted: bool = False,
    search: str | None = None,
) -> list[CrewPlayer]:
    """The players that pass every filter given: a rating (`any`: either),
    a note, and a folded search text found in the key, the guild or the
    note."""
    want = fold(search) if search else ""
    out: list[CrewPlayer] = []
    for p in players:
        if rated is not None and (p.rating is None or (rated != "any" and p.rating != rated)):
            continue
        if noted and not p.note:
            continue
        if want and not any(want in fold(t) for t in (p.key, p.guild or "", p.note or "")):
            continue
        out.append(p)
    return out


def ordered(players: Iterable[CrewPlayer], by: SortBy = "last") -> list[CrewPlayer]:
    """`last`: newest first; `met`: most groups first; `name`: by the folded
    key. A missing value sorts last; ties go by the stored key."""
    items = sorted(players, key=lambda p: p.key)
    if by == "name":
        return sorted(items, key=lambda p: fold(p.key))
    if by == "met":
        return sorted(items, key=lambda p: -(p.met if p.met is not None else -1))
    return sorted(items, key=lambda p: -(p.last if p.last is not None else -1))
