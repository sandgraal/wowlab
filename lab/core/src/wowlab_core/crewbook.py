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

Schema 1 (§20.1) and schema 2 (§21.2, M20-03) are read; at schema 2 also
what M20-11 records (M20-12): each player's GUID, race, faction, spec,
honor level and latest scoreboard line (`CrewScore`), and a solo shuffle's
`kind`. Schema 2 adds the
per-player match counters (`CrewBook.pvp`, by key), `matches` (an entry that does
not fit is listed in `matches_unread` with its place and a reason, the rest
read), `matched`, `warned`, and the scoreboard probe's `probe.pvp`, which is
given only as fixed names and values (`ProbeItem`) and never refuses the
book. A schema 1 book is read exactly as before: a schema 2 field in it is
a key this reader does not know.

Everything read from the file other than numbers is other people's text
(names, realms, guilds, instance names) or the player's own (notes). Nothing
here prints it; the CLI shows it only through its control-character
escaping path. A key is opaque: it is kept as stored, never split or
rewritten (§20.6); `match` compares a folded form of it.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from wowlab_core import labaddon, luadata
from wowlab_core.errors import WowlabError

CREW_VARIABLE = "WowLabCrewDB"
# The `schema` numbers this reader knows (§20.1: the book has its own,
# separate from the per-character record's). Schema 2 (§21.2) adds the
# per-player match counters, `matches`, `matched`, `warned` and the
# scoreboard probe's `probe.pvp`; a schema 1 book is read exactly as before.
KNOWN_SCHEMAS: tuple[int, ...] = (1, 2)
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
    "matches": "match list",
    "matched": "record of the match last counted",
    "warned": "time of the last nearly-full line",
}


def _unreadable(field: str, detail: str) -> CrewBookError:
    return CrewBookError(
        f"The crew book's {_FIELD_WORDS[field]} is not readable. {_UNREADABLE}", detail
    )


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


PvpSide = Literal["with", "against"]


class CrewPvp(_Model):
    """A schema 2 player's match counters and latest match (§21.2), each
    None when the entry does not hold it (left out until the first match)."""

    allied: int | None = None  # matches on your side
    faced: int | None = None  # matches against you
    allied_won: int | None = None  # of `allied`, the ones your side won
    faced_won: int | None = None  # of `faced`, the ones your side won
    pvp_last: int | None = None  # time() of the latest match
    pvp_map: str | None = None  # the map's name, as the client gave it
    pvp_side: PvpSide | None = None


ScoreValue = bool | int | float | str


class CrewScore(_Model):
    """The latest match's scoreboard line of one player (§21.2, as built by
    M20-11): `at`, the time of the match, and every other field of the row
    the addon kept, by the client's own field name (`values`: a number, text
    or true/false). The row's `name` and `guid` are never among them (the
    player's entry holds both); a field this reader does not keep (not
    plain, or one of those two) is named in `other_keys`."""

    at: int | None = None
    values: dict[str, ScoreValue] = Field(default_factory=dict)
    other_keys: list[str] = Field(default_factory=list)


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
    # Schema 2, as built by M20-11 (§21.2): what the client gives of the
    # player, each as of the latest group or match that gave it. `guid` is
    # the game's id of the character; `faction` is the faction's name as a
    # group member's unit gives it (a scoreboard's side is in `score`).
    guid: str | None = None
    race: str | None = None
    faction: str | None = None
    spec: str | None = None
    honor_level: int | None = None
    score: CrewScore | None = None
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


MatchKind = Literal["bg", "arena", "shuffle"]


class CrewMatch(_Model):
    """One entry of a schema 2 book's `matches` (§21.2): `at` (time() of
    the read), the map's name and id, `kind`, `won` (None when the client
    gave no winner) and the keys of `team` and `foes`, as stored. A solo
    shuffle (`kind` `shuffle`) lists everyone in `team` and has no `won`.
    A key in a match may have no player entry (the book, full of rated and
    noted players, refused it)."""

    position: int  # its place in the list, from 1
    at: int
    map: str | None = None
    map_id: int | None = None
    kind: MatchKind
    won: bool | None = None
    team: list[str] = Field(default_factory=list)
    foes: list[str] = Field(default_factory=list)
    other_keys: list[str] = Field(default_factory=list)


class UnreadMatch(_Model):
    """An entry of `matches` that does not fit §21.2: its place in the list
    and why, in wowlab's words. The value it holds is never quoted."""

    position: int
    reason: str


# Lua's fixed set of type names (`type`), the only text the scoreboard
# probe stores as a value (§21.2).
LUA_TYPE_NAMES = ("nil", "boolean", "number", "string", "table", "function", "userdata", "thread")
ProbeWord = Literal[
    "nil", "boolean", "number", "string", "table", "function", "userdata", "thread", "?"
]


class ProbeItem(_Model):
    """One value of the scoreboard probe's block (`probe.pvp`, §21.2), as
    `doctor` may print it: `name` is built only from fixed lists
    (`PROBE_CALLS`, `PROBE_SCORE_FIELDS`, `PROBE_RUN_FIELDS`, a position
    number) with `?` in place of anything else; `value` is a whole number,
    true or false, or one of Lua's type names, and `?` for anything else.
    Nothing here is text from the file."""

    name: str
    value: bool | int | ProbeWord


class CrewBook(_Model):
    """`WowLabCrewDB` as read (§20.2; schema 2, §21.2)."""

    state: State
    schema_number: int | None = None
    started: int | None = None  # time() when the book was (last) started
    probe_loads: int | None = None  # the book's own load probe (§20.1)
    skipped: int | None = None  # reads the addon skipped, counted per attempt
    current_count: int = 0  # keys in the saved `current` set
    players: list[CrewPlayer] = Field(default_factory=list)
    unread: list[UnreadEntry] = Field(default_factory=list)
    # Schema 2 only (§21.2); empty, None or false for a schema 1 book.
    # The match counters of each read player that holds any, by stored key
    # (kept beside `players`, so the record of §20.1 and its `--json` stay
    # as they were; a schema 1 entry keeps such a key in `other_keys`).
    pvp: dict[str, CrewPvp] = Field(default_factory=dict)
    matches: list[CrewMatch] = Field(default_factory=list)
    matches_unread: list[UnreadMatch] = Field(default_factory=list)
    warned: int | None = None  # time() of the last nearly-full line
    matched: bool = False  # whether the once-per-match marker holds anything
    # The scoreboard probe's runs (after entry, after the winner), each as
    # `doctor` may print it.
    probe_pvp: list[list[ProbeItem]] = Field(default_factory=list)

    def match_keys(self) -> list[str]:
        """Every key the read matches name in `team` or `foes`, once each, in
        file order."""
        out: dict[str, None] = {}
        for m in self.matches:
            for key in (*m.team, *m.foes):
                out.setdefault(key, None)
        return list(out)


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


# Schema 2's per-player fields (§21.2): four counters, a time, a map's name
# and a side.
_PVP_NUMBERS: dict[str, tuple[int, int, str]] = {
    "allied": (0, _INT_MAX, "a whole number of 0 or more"),
    "faced": (0, _INT_MAX, "a whole number of 0 or more"),
    "allied_won": (0, _INT_MAX, "a whole number of 0 or more"),
    "faced_won": (0, _INT_MAX, "a whole number of 0 or more"),
    "pvp_last": (0, TIME_MAX, "a time"),
}


def _pvp_field(name: str, item: object, pvp: dict[str, Any]) -> str | None:
    """Read one schema 2 player field into `pvp`; why it cannot be, else None."""
    if name in _PVP_NUMBERS:
        low, high, words = _PVP_NUMBERS[name]
        number = _whole(item, low, high)
        if number is None:
            return f"{name} is not {words}"
        pvp[name] = number
    elif name == "pvp_map":
        if not isinstance(item, str):
            return "pvp_map is not text"
        pvp[name] = item
    else:
        if item not in ("with", "against"):
            return 'pvp_side is neither "with" nor "against"'
        pvp[name] = item
    return None


_PVP_FIELDS = frozenset({*_PVP_NUMBERS, "pvp_map", "pvp_side"})

# The game's id of a player character as the client gives it:
# `Player-<server id>-<8 hex digits>` (the capture tool's matcher, with the
# hex part's length the client writes). A value of any other shape is never
# stored or used to link two names (§21.3, M20-12).
GUID_SHAPE = re.compile(r"Player-[0-9]+-[0-9A-Fa-f]{8}", re.ASCII)


def guid_shaped(text: str | None) -> bool:
    """Whether `text` is a player GUID in the client's shape."""
    return text is not None and GUID_SHAPE.fullmatch(text) is not None


# What M20-11 keeps of each player (§21.2, as built): four texts, a whole
# number and the latest scoreboard line.
_M20_11_TEXTS = ("guid", "race", "faction", "spec")
# The row's own fields the line never holds: the entry keeps them.
_SCORE_LEFT_OUT = frozenset({"name", "guid"})


def _plain(value: object) -> bool:
    """A number, text or true/false (a float only when finite)."""
    if isinstance(value, float):
        return math.isfinite(value)
    return isinstance(value, (bool, int, str))


def _score(item: object) -> CrewScore | str:
    """`score` as read, or why it cannot be (never quoting it)."""
    if isinstance(item, list):  # a table with no named field
        return CrewScore(other_keys=[f"[{i}]" for i in range(1, len(item) + 1)])
    if not isinstance(item, dict):
        return "score is not a table"
    at: int | None = None
    values: dict[str, ScoreValue] = {}
    other: list[str] = []
    for name, value in item.items():
        if name == "at":
            at = _whole(value, 0, TIME_MAX)
            if at is None:
                return "the time of score is not a time"
        elif isinstance(name, str) and name not in _SCORE_LEFT_OUT and _plain(value):
            values[name] = cast(ScoreValue, value)
        else:
            other.append(_key_text(name))
    return CrewScore(at=at, values=values, other_keys=other)


def _m20_11_field(name: str, item: object, fields: dict[str, Any]) -> str | None:
    """Read one field M20-11 adds into `fields`; why it cannot be, else None."""
    if name in _M20_11_TEXTS:
        if not isinstance(item, str):
            return f"{name} is not text"
        fields[name] = item
    elif name == "honor_level":
        number = _whole(item, 0, _INT_MAX)
        if number is None:
            return "honor_level is not a whole number of 0 or more"
        fields[name] = number
    else:
        score = _score(item)
        if isinstance(score, str):
            return score
        fields[name] = score
    return None


_M20_11_FIELDS = frozenset({*_M20_11_TEXTS, "honor_level", "score"})


def _player(
    key: str,
    value: object,
    current: frozenset[str],
    schema: int = 1,
    counters: dict[str, CrewPvp] | None = None,
) -> CrewPlayer | UnreadEntry:
    """One entry of `players`, or why it cannot be read; a schema 2 entry's
    match counters go into `counters` by key. At schema 1 the fields of
    §21.2 are keys this reader does not know, as they were."""
    if not key:
        return UnreadEntry(key=key, reason="the key is empty")
    if not isinstance(value, dict):
        return UnreadEntry(key=key, reason="the entry is not a table")
    fields: dict[str, Any] = {"key": key, "in_current_group": key in current}
    other: list[str] = []
    pvp: dict[str, Any] = {}
    for name, item in value.items():
        if schema >= 2 and name in _PVP_FIELDS:
            why = _pvp_field(name, item, pvp)
            if why is not None:
                return UnreadEntry(key=key, reason=why)
        elif schema >= 2 and name in _M20_11_FIELDS:
            why = _m20_11_field(name, item, fields)
            if why is not None:
                return UnreadEntry(key=key, reason=why)
        elif name in _TEXT_FIELDS:
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
    if pvp and counters is not None:
        counters[key] = CrewPvp(**pvp)
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
        *head, last = (str(s) for s in KNOWN_SCHEMAS)
        reads = f"versions {', '.join(head)} and {last}" if head else f"version {last}"
        raise CrewBookError(
            "This crew book was written by another version of the lab-addon (book version "
            f"{number}; this wowlab reads {reads}). Nothing was read; updating wowlab "
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
    counters: dict[str, CrewPvp] = {}
    for key, entry in _keyed(value.get("players"), "players"):
        if not isinstance(key, str):
            unread.append(UnreadEntry(key=_key_text(key), reason="the key is not a name"))
            continue
        read = _player(key, entry, current, schema, counters)
        if isinstance(read, CrewPlayer):
            players.append(read)
        else:
            unread.append(read)
    more: dict[str, Any] = {}
    if schema >= 2:
        more = _schema_2(value, probe if isinstance(probe, dict) else None)
        more["pvp"] = counters
    return CrewBook(
        state="on" if on is True else "off",
        schema_number=schema,
        started=_top_number(value, "started", TIME_MAX),
        probe_loads=loads,
        skipped=_top_number(value, "skipped"),
        current_count=len(current),
        players=players,
        unread=unread,
        **more,
    )


# ─── schema 2 (§21.2) ────────────────────────────────────────────────────────


def _schema_2(value: dict[Any, Any], probe: dict[Any, Any] | None) -> dict[str, Any]:
    """The top-level fields schema 2 adds, as `CrewBook` fields."""
    matched = value.get("matched")
    if matched is not None and not isinstance(matched, (dict, list)):
        raise _unreadable("matched", f"{CREW_VARIABLE}.matched is not a table")
    matches: list[CrewMatch] = []
    unread: list[UnreadMatch] = []
    for position, (key, entry) in enumerate(_keyed(value.get("matches"), "matches"), 1):
        read = _match(position, key, entry)
        if isinstance(read, CrewMatch):
            matches.append(read)
        else:
            unread.append(read)
    pvp = probe.get("pvp") if probe is not None else None
    return {
        "matches": matches,
        "matches_unread": unread,
        "warned": _top_number(value, "warned", TIME_MAX),
        "matched": bool(matched),
        "probe_pvp": probe_runs(pvp),
    }


_MATCH_KINDS = ("bg", "arena", "shuffle")


def _keys_of(value: object) -> list[str] | None:
    """A list of keys (`team`, `foes`) as stored; None when it is not a list
    of non-empty text. An empty table is an empty list."""
    if isinstance(value, dict) and not value:
        return []
    if not isinstance(value, list):
        return None
    if not all(isinstance(k, str) and k for k in value):
        return None
    return list(value)


def _match(position: int, key: object, value: object) -> CrewMatch | UnreadMatch:
    """One entry of `matches`, or why it cannot be read (never quoting it)."""

    def bad(reason: str) -> UnreadMatch:
        return UnreadMatch(position=position, reason=reason)

    if key != position:
        return bad("the entry is not in the list's order")
    if not isinstance(value, dict):
        return bad("the entry is not a table")
    fields: dict[str, Any] = {"position": position}
    other: list[str] = []
    for name, item in value.items():
        if name == "at":
            fields["at"] = _whole(item, 0, TIME_MAX)
            if fields["at"] is None:
                return bad("at is not a time")
        elif name == "map":
            if not isinstance(item, str):
                return bad("map is not text")
            fields["map"] = item
        elif name == "map_id":
            fields["map_id"] = _whole(item, -(2**31), 2**31 - 1)
            if fields["map_id"] is None:
                return bad("map_id is not a whole number")
        elif name == "kind":
            if item not in _MATCH_KINDS:
                return bad('kind is not "bg", "arena" or "shuffle"')
            fields["kind"] = item
        elif name == "won":
            if not isinstance(item, bool):
                return bad("won is neither true nor false")
            fields["won"] = item
        elif name in ("team", "foes"):
            keys = _keys_of(item)
            if keys is None:
                return bad(f"{name} is not a list of names")
            fields[name] = keys
        else:
            other.append(_key_text(name))
    if "at" not in fields:
        return bad("the entry has no time")
    if "kind" not in fields:
        return bad("the entry has no kind")
    fields["other_keys"] = other
    return CrewMatch(**fields)


# The probe's fixed lists (§21.2, as M20-04 pinned `pvpSnapshot`): the
# candidate calls it records as present or not, the names of one run's own
# fields, and the game's scoreboard row fields `doctor` may name. Any other
# name in the block is shown as `?`.
PROBE_CALLS = (
    "GetScoreInfo",
    "GetNumBattlefieldScores",
    "GetBattlefieldScore",
    "RequestBattlefieldScoreData",
    "GetBattlefieldWinner",
    "GetBattlefieldArenaFaction",
    "IsActiveBattlefieldArena",
)
PROBE_RUN_FIELDS = (
    "requested",
    "score_events",
    "complete_events",
    "rows",
    "name_string",
    "name_secret",
    "name_dash",
    "first_side",
    "own_side",
    "winner",
)
# The fields a scoreboard row table is believed to hold (§21.2, [verify]
# until the owner's battleground): the game's own vocabulary, not anyone's
# data.
PROBE_SCORE_FIELDS = (
    "name",
    "guid",
    "killingBlows",
    "honorableKills",
    "deaths",
    "honorGained",
    "faction",
    "raceName",
    "className",
    "classToken",
    "damageDone",
    "healingDone",
    "rating",
    "ratingChange",
    "prematchMMR",
    "mmrChange",
    "postmatchMMR",
    "talentSpec",
    "honorLevel",
    "roleAssigned",
    "stats",
)
# At most this many runs (the probe stores two) and this many values per
# run are given; the rest of a larger block is not.
PROBE_RUNS_MAX = 2
PROBE_ITEMS_MAX = 128
_PROBE_POSITIONS_MAX = 64
_UNKNOWN: Final = "?"


def _probe_value(value: object) -> bool | int | ProbeWord:
    """A value of the block as `doctor` may print it: true or false, a
    whole number, one of Lua's type names, else `?`."""
    if isinstance(value, bool):
        return value
    number = _whole(value, -_INT_MAX, _INT_MAX)
    if number is not None:
        return number
    if isinstance(value, str) and value in LUA_TYPE_NAMES:
        return cast(ProbeWord, value)  # one of ProbeWord's names
    return _UNKNOWN


def _probe_name(group: str, name: object, known: tuple[str, ...]) -> str:
    return f"{group}.{name}" if isinstance(name, str) and name in known else f"{group}.?"


def _probe_run(run: object) -> list[ProbeItem]:
    """One run of the probe as fixed names and values (`ProbeItem`)."""
    if not isinstance(run, dict):
        return [ProbeItem(name=_UNKNOWN, value=_UNKNOWN)]
    items: list[ProbeItem] = []
    for name, value in run.items():
        if name in ("calls", "fields") and isinstance(value, (dict, list)):
            known = PROBE_CALLS if name == "calls" else PROBE_SCORE_FIELDS
            for inner, item in _keyed(value, name):
                items.append(
                    ProbeItem(name=_probe_name(name, inner, known), value=_probe_value(item))
                )
        elif name == "positions" and isinstance(value, (dict, list)):
            for at, item in _keyed(value, name):
                index = _whole(at, 1, _PROBE_POSITIONS_MAX)
                label = f"positions.{index}" if index is not None else "positions.?"
                items.append(ProbeItem(name=label, value=_probe_value(item)))
        elif isinstance(name, str) and name in PROBE_RUN_FIELDS:
            items.append(ProbeItem(name=name, value=_probe_value(value)))
        else:
            items.append(ProbeItem(name=_UNKNOWN, value=_probe_value(value)))
    return items[:PROBE_ITEMS_MAX]


def probe_runs(value: object) -> list[list[ProbeItem]]:
    """`probe.pvp` (§21.2: a list of the run after entry and the run after a
    winner was reported) as `doctor` may print it. Never refuses: a block
    that does not fit is shown as `?`, since it is a diagnostic, not the
    book's records."""
    if value is None:
        return []
    if not isinstance(value, (dict, list)):
        return [[ProbeItem(name=_UNKNOWN, value=_UNKNOWN)]]
    entries = list(value.items()) if isinstance(value, dict) else list(enumerate(value, 1))
    return [_probe_run(run) for _, run in entries[:PROBE_RUNS_MAX]]


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


def match[P: CrewPlayer](players: Sequence[P], typed: str) -> list[P]:
    """The players `typed` names: the one whose stored key is exactly
    `typed`, else every one whose folded key equals the folded `typed`."""
    exact = [p for p in players if p.key == typed]
    if exact:
        return exact[:1]
    want = fold(typed)
    return [p for p in players if fold(p.key) == want]


def select[P: CrewPlayer](
    players: Iterable[P],
    *,
    rated: RatedFilter | None = None,
    noted: bool = False,
    search: str | None = None,
) -> list[P]:
    """The players that pass every filter given: a rating (`any`: either),
    a note, and a folded search text found in the key, the guild or the
    note."""
    want = fold(search) if search else ""
    out: list[P] = []
    for p in players:
        if rated is not None and (p.rating is None or (rated != "any" and p.rating != rated)):
            continue
        if noted and not p.note:
            continue
        if want and not any(want in fold(t) for t in (p.key, p.guild or "", p.note or "")):
            continue
        out.append(p)
    return out


def ordered[P: CrewPlayer](players: Iterable[P], by: SortBy = "last") -> list[P]:
    """`last`: newest first; `met`: most groups first; `name`: by the folded
    key. A missing value sorts last; ties go by the stored key."""
    items = sorted(players, key=lambda p: p.key)
    if by == "name":
        return sorted(items, key=lambda p: fold(p.key))
    if by == "met":
        return sorted(items, key=lambda p: -(p.met if p.met is not None else -1))
    return sorted(items, key=lambda p: -(p.last if p.last is not None else -1))
