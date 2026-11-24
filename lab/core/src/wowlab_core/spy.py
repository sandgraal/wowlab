"""spy: read what the Spy addon saves (M22-01).

Spy is a third-party addon that warns of enemy players nearby and keeps a
list of every player it has seen. It saves two tables (`docs/LAB_FORMATS.md`,
"Spy"):

- `SpyDB`, account-wide, in `WTF/Account/<acct>/SavedVariables/Spy.lua`:
  the kill-on-sight lists Spy shares between the account's characters
  (`kosData`), the ones it removed (`removeKOSData`), the player's notes
  (`playerNotes`) and tags (`playerTags`), and Spy's own settings.
- `SpyPerCharDB`, per character, in the character folder's
  `SavedVariables/Spy.lua`: `PlayerData` (every player seen, keyed by
  `<First>-<Second>`), `KOSData` (kill on sight: key to the time added),
  `IgnoreData` (key to true), `Encounters` (a list of fights) and `version`.

This module reads both through `luadata` (no Lua is run, L3) into typed
rows, read-only (L1): it writes nothing anywhere and imports no network
module. A `PlayerData` field this reader does not know, or a known one
holding a value of another type, is kept in the row's `other` (L4: what is
not understood is kept, not dropped). A row that is not a table is listed
in `unread` with its key and why. A file that is not data (a function, a
call) is refused by `luadata` with a position; the survey names it and
reads the others. A `version` other than the ones this reader knows is read
as version 1.1 is, with a warning.

Every row records the file and the character it came from (`SpySource`).
Everything read from the file other than numbers is other players' text
(names, guilds, zones) or the player's own (notes): nothing here prints it;
the CLI shows it only through its control-character escaping path.
"""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from wowlab_core import layout, luadata
from wowlab_core.errors import WowlabError

FILE_NAME: Final = "Spy.lua"
ACCOUNT_VARIABLE: Final = "SpyDB"
CHARACTER_VARIABLE: Final = "SpyPerCharDB"
# The `SpyPerCharDB.version` values this reader knows: Spy's own
# `Spy.DatabaseVersion`, as the owner's install showed it on 2026-11-23.
KNOWN_VERSIONS: Final[tuple[str, ...]] = ("1.1",)
# The largest time accepted: the last second of the year 9999.
TIME_MAX: Final = 253_402_300_799
_INT_MAX: Final = 2**53

# A plain value as JSON can hold it: what an unknown field keeps.
type Plain = str | int | float | bool | list[Plain] | dict[str, Plain] | None

EncounterResult = Literal["win", "loss", "probable_win"]
# Spy's one-letter results (`Encounters.lua`: W win, L loss, P probable win).
_RESULTS: Final[dict[str, EncounterResult]] = {"W": "win", "L": "loss", "P": "probable_win"}
SpyTag = Literal["spare", "rival"]


class SpyError(WowlabError, ValueError):
    """A Spy file whose top level is not the shape Spy saves (its table is
    not a table). The message is in the player's words and quotes nothing
    from the file."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SpySource(_Model):
    """Where a row was read: the file (relative to the flavor folder,
    `/`-separated), its game account folder and, for a character's file,
    the character as `<realm or digits folder>/<character folder>` (None
    for the account file). `flavor_folder` is set by `survey`."""

    file: str
    account: str
    character: str | None = None
    flavor_folder: str | None = None


class SpyPlayer(_Model):
    """One `PlayerData` row: a player Spy still keeps. Spy keeps hostile
    players only, and drops a row not seen for 90 days by default (Spy's
    purge option, `SpySettings.purge_days`) or once the player is seen as
    friendly, unless it counted a killing blow or a death against them (and
    Spy is not set to purge those too). `key` is the table key, as
    stored (`<First>-<Second>` on Forever); `name` is the row's own `name`
    field.

    `time` is when Spy last saw them (Unix seconds). The place is the one
    Spy last recorded, which can be from another moment: Spy records the
    recording character's own position, never the other player's. `time`
    is set at every sighting; outside an instance the place is written only
    when Spy first notices the player after losing sight of them, and not at
    all when the game gives no position (0,0); inside one Spy writes the
    instance's name as `zone` and "" as `sub_zone` and leaves `map_id`,
    `map_x` and `map_y` as they were, so the place can be older than
    `time`. A row another Spy user shared (only when the
    character's Spy profile takes shared data, off by default) carries the
    sender's place, `is_enemy` True and the time it arrived.

    `is_enemy` is whether the game called the player hostile to the
    recording character when Spy last looked (`UnitIsEnemy`), not their
    faction; marking a player kill on sight, or a shared row, sets it too.
    `wins` is Spy's count of your killing blows on them; `loses` the deaths
    Spy put on them (some guessed from who had you targeted); both absent
    until counted. `rank` is a Classic-client field the Forever port never
    writes. `reasons` are the kill-on-sight reasons the player chose (a
    reason's text, or True). `kos` and `kos_added` come from the same
    file's `KOSData`, `ignored` from its `IgnoreData`; Spy's own `kos`
    mark on the row (1 while on the list) is kept in `other`. `other` keeps
    every field this reader does not know, and every known field whose
    value is of another type, as read, each key written once
    (`_key_text`)."""

    key: str
    source: SpySource
    name: str | None = None
    class_token: str | None = None
    race: str | None = None
    level: int | None = None
    guild: str | None = None
    faction: str | None = None
    is_enemy: bool | None = None
    is_guess: bool | None = None
    rank: int | None = None
    zone: str | None = None
    sub_zone: str | None = None
    map_id: int | None = None
    map_x: float | None = None
    map_y: float | None = None
    time: int | None = None
    wins: int | None = None
    loses: int | None = None
    reasons: dict[str, str | bool] = Field(default_factory=dict)
    kos: bool = False
    kos_added: int | None = None
    ignored: bool = False
    other: dict[str, Plain] = Field(default_factory=dict)


class SpyKos(_Model):
    """One `KOSData` entry: a player on this character's kill-on-sight
    list, and when it was added (Unix seconds; None when the value is not
    a time)."""

    key: str
    source: SpySource
    added: int | None = None


class SpyIgnored(_Model):
    """One `IgnoreData` entry: a player Spy is told to ignore."""

    key: str
    source: SpySource


class SpyEncounter(_Model):
    """One `Encounters` entry, a fight Spy counted: when (`time`), against
    whom (`name`), the result (`result`: Spy's letter W, L or P as a word;
    None for a letter this reader does not know, kept in `result_code`),
    how it was counted (`how`: kill, pet, group, probable, recap or
    targeting),
    the opponent's class, level (when Spy knew it) and guild, and where the
    recording character stood (`map_id`, `x`, `y`). `position` is the
    entry's place in the list, from 1."""

    position: int
    source: SpySource
    time: int | None = None
    name: str | None = None
    result: EncounterResult | None = None
    result_code: str | None = None
    how: str | None = None
    class_token: str | None = None
    level: int | None = None
    guild: str | None = None
    map_id: int | None = None
    x: float | None = None
    y: float | None = None
    other: dict[str, Plain] = Field(default_factory=dict)


class SpySharedKos(_Model):
    """One entry of `SpyDB.kosData`: a player on the kill-on-sight list
    that one of the account's characters shares through the account file.
    `kept_under` is the list's first key: Spy's realm label, which is
    `Region <realm type>` when the client reports region-wide unique names
    and the realm's name otherwise; `faction` the sharing character's faction and
    `character` the sharing character as Spy names it ("First Second")."""

    key: str
    source: SpySource
    kept_under: str
    faction: str
    character: str
    added: int | None = None


class SpyNote(_Model):
    """One of the player's own notes about a player, from `SpyDB.playerNotes`."""

    key: str
    source: SpySource
    kept_under: str
    text: str


class SpyPlayerTag(_Model):
    """One of the player's own tags on a player (`spare`: do not kill;
    `rival`: a fair fight), from `SpyDB.playerTags`. A tag Spy would not
    show is kept as read in `other_tag`."""

    key: str
    source: SpySource
    kept_under: str
    tag: SpyTag | None = None
    other_tag: str | None = None


class SpyUnread(_Model):
    """An entry that does not fit what Spy saves: the table it is in
    (`table`), its key or place, and why, in wowlab's words. Never the
    value it holds."""

    source: SpySource
    table: str
    key: str
    reason: str


class SpyCharacterData(_Model):
    """A character's `SpyPerCharDB`. `present` is False when the file holds
    no `SpyPerCharDB` (Spy has saved nothing for this character). `version`
    is the table's `version` as read; `warning` says, in the player's
    words, when it is absent or one this reader does not know.
    `other_keys` names the top-level keys this reader does not know."""

    source: SpySource
    present: bool
    version: str | None = None
    warning: str | None = None
    players: list[SpyPlayer] = Field(default_factory=list)
    kos: list[SpyKos] = Field(default_factory=list)
    ignored: list[SpyIgnored] = Field(default_factory=list)
    encounters: list[SpyEncounter] = Field(default_factory=list)
    unread: list[SpyUnread] = Field(default_factory=list)
    other_keys: list[str] = Field(default_factory=list)


class SpySettings(_Model):
    """The Spy options of one character's profile that decide what its
    list holds, each Spy's default when the profile does not set it (as Spy
    itself reads them). `character` is the character as `profileKeys` names
    it ("First Second"). `purge_days` is how long a player not seen is
    kept (`PurgeData`: 1, 5, 10, 30, 60 or 90 days; 90 by default; None for
    a value this reader does not know, kept in `purge_setting`).
    `purge_counted` (`PurgeWinLossData`, off by default) drops players Spy
    counted a killing blow or a death against too; `purge_kos` (`PurgeKoS`,
    off by default) drops kill-on-sight players too; `uses_shared_data`
    (`UseData`, off by default) takes the rows other Spy users share."""

    character: str
    purge_days: int | None = 90
    purge_setting: str | None = None
    purge_counted: bool = False
    purge_kos: bool = False
    uses_shared_data: bool = False


class SpyAccountData(_Model):
    """The account's `SpyDB`: the shared kill-on-sight lists, the player's
    notes and tags. `present` is False when the file holds no `SpyDB`.
    `other_keys` names the top-level keys read only by name (Spy's
    settings and its own lists, such as `profiles` and `watchData`).
    `settings` holds, for each character `profileKeys` names, the Spy
    options that decide what its list holds (`SpySettings`);
    `uses_shared_data` names the ones whose profile takes the rows other
    Spy users share (`UseData`, off by default): their rows' places may be
    other users'."""

    source: SpySource
    present: bool
    settings: list[SpySettings] = Field(default_factory=list)
    uses_shared_data: list[str] = Field(default_factory=list)
    shared_kos: list[SpySharedKos] = Field(default_factory=list)
    notes: list[SpyNote] = Field(default_factory=list)
    tags: list[SpyPlayerTag] = Field(default_factory=list)
    unread: list[SpyUnread] = Field(default_factory=list)
    other_keys: list[str] = Field(default_factory=list)


class SpyFileError(_Model):
    """A Spy file that could not be read, and why."""

    source: SpySource
    error: str


class SpySurvey(_Model):
    """Every Spy file of one flavor folder, read: each account's `SpyDB`,
    each character's `SpyPerCharDB`, and each file that could not be read."""

    accounts: list[SpyAccountData] = Field(default_factory=list)
    characters: list[SpyCharacterData] = Field(default_factory=list)
    errors: list[SpyFileError] = Field(default_factory=list)

    @property
    def players(self) -> list[SpyPlayer]:
        return [p for c in self.characters for p in c.players]

    @property
    def warnings(self) -> list[str]:
        return [c.warning for c in self.characters if c.warning is not None]


# ─── values ──────────────────────────────────────────────────────────────────


def _whole(value: object, low: int = -_INT_MAX, high: int = _INT_MAX) -> int | None:
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


def _real(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _key_text(key: object) -> str:
    """A table key as text, one text per key (L4: no two keys of a table
    share one): a string as it is, unless it starts with `[`, when it is
    written as `["..."]` with its text JSON-quoted; a number as `[n]`; a
    boolean as `[true]` or `[false]`. A number's or boolean's text starts
    with `[` and a digit, `-`, `t` or `f`, an escaped string's with `["`, so
    the key `[1]` and the string key `"[1]"` stay apart."""
    if isinstance(key, str):
        return f"[{json.dumps(key, ensure_ascii=False)}]" if key.startswith("[") else key
    if isinstance(key, bool):
        return "[true]" if key else "[false]"
    return f"[{key!r}]" if isinstance(key, (int, float)) else "[?]"


def _plain(value: object) -> Plain:
    """`value` as JSON can hold it: dict keys as text (`_key_text`), and a
    number that is not finite as its spelling."""
    if isinstance(value, dict):
        return {_key_text(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _entries(value: object) -> list[tuple[object, object]]:
    """A table's entries in order: a `dict`'s items, a `list`'s from 1."""
    if isinstance(value, dict):
        return list(value.items())
    if isinstance(value, list):
        return list(enumerate(value, 1))
    return []


_NOT_A_TABLE = "not a table"


# ─── a character's file ──────────────────────────────────────────────────────


# PlayerData's fields: Lua name -> (model field, reader). A value the reader
# returns None for (another type) is kept in `other`.
def _int(value: object) -> int | None:
    return _whole(value)


def _seconds(value: object) -> int | None:
    return _whole(value, 0, TIME_MAX)


def _count(value: object) -> int | None:
    return _whole(value, 0)


type _Reader = Callable[[object], object]

_PLAYER_FIELDS: Final[dict[str, tuple[str, _Reader]]] = {
    "name": ("name", _text),
    "class": ("class_token", _text),
    "race": ("race", _text),
    "level": ("level", _int),
    "guild": ("guild", _text),
    "faction": ("faction", _text),
    "isEnemy": ("is_enemy", _flag),
    "isGuess": ("is_guess", _flag),
    "rank": ("rank", _int),
    "zone": ("zone", _text),
    "subZone": ("sub_zone", _text),
    "mapID": ("map_id", _int),
    "mapX": ("map_x", _real),
    "mapY": ("map_y", _real),
    "time": ("time", _seconds),
    "wins": ("wins", _count),
    "loses": ("loses", _count),
}

_ENCOUNTER_FIELDS: Final[dict[str, tuple[str, _Reader]]] = {
    "t": ("time", _seconds),
    "n": ("name", _text),
    "k": ("how", _text),
    "c": ("class_token", _text),
    "l": ("level", _int),
    "g": ("guild", _text),
    "m": ("map_id", _int),
    "x": ("x", _real),
    "y": ("y", _real),
}


def _reasons(value: object) -> dict[str, str | bool] | None:
    """A kill-on-sight `reason` table: reason text -> True, or the "other"
    reason -> its text. None when it is not that shape."""
    if not isinstance(value, dict):
        return None
    out: dict[str, str | bool] = {}
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, (str, bool)):
            return None
        out[k] = v
    return out


def _player(
    key: str,
    value: dict[object, object],
    source: SpySource,
    kos: dict[str, int | None],
    ignored: set[str],
) -> SpyPlayer:
    fields: dict[str, object] = {}
    other: dict[str, Plain] = {}
    for k, v in value.items():
        name = k if isinstance(k, str) else None
        if name is not None and name in _PLAYER_FIELDS:
            target, read = _PLAYER_FIELDS[name]
            got = read(v)
            if got is not None:
                fields[target] = got
                continue
        elif name == "reason":
            reasons = _reasons(v)
            if reasons is not None:
                fields["reasons"] = reasons
                continue
        other[_key_text(k)] = _plain(v)
    return SpyPlayer.model_validate(
        {
            "key": key,
            "source": source,
            **fields,
            "kos": key in kos,
            "kos_added": kos.get(key),
            "ignored": key in ignored,
            "other": other,
        }
    )


def _encounter(position: int, value: dict[object, object], source: SpySource) -> SpyEncounter:
    fields: dict[str, object] = {}
    other: dict[str, Plain] = {}
    for k, v in value.items():
        name = k if isinstance(k, str) else None
        if name == "r" and isinstance(v, str):
            fields["result_code"] = v
            if v in _RESULTS:
                fields["result"] = _RESULTS[v]
            continue
        if name is not None and name in _ENCOUNTER_FIELDS:
            target, read = _ENCOUNTER_FIELDS[name]
            got = read(v)
            if got is not None:
                fields[target] = got
                continue
        other[_key_text(k)] = _plain(v)
    return SpyEncounter.model_validate(
        {"position": position, "source": source, **fields, "other": other}
    )


def _version_warning(version: object) -> str | None:
    if version is None:
        return (
            "Spy's saved data for this character has no version number; wowlab read it as "
            f"version {KNOWN_VERSIONS[-1]} is."
        )
    if version not in KNOWN_VERSIONS:
        known = ", ".join(KNOWN_VERSIONS)
        said = (
            f"as version {version:.40}"
            if isinstance(version, str)
            else "with a version that is not text"
        )
        return (
            f"Spy saved this character's data {said}, which wowlab does not know (it knows "
            f"{known}); it was read as version {KNOWN_VERSIONS[-1]} is, so a field may be "
            "misread."
        )
    return None


_CHARACTER_TABLES: Final = ("PlayerData", "KOSData", "IgnoreData", "Encounters", "version")


def load_character(value: object, source: SpySource) -> SpyCharacterData:
    """`SpyPerCharDB` as `luadata.LuaDocument.to_python` gives it (None when
    the file does not assign it)."""
    if value is None:
        return SpyCharacterData(source=source, present=False)
    if not isinstance(value, dict):
        raise SpyError(
            "This Spy file does not hold the table Spy saves for a character, so it was not read."
        )
    unread: list[SpyUnread] = []

    def table(name: str) -> list[tuple[object, object]]:
        got = value.get(name)
        if got is None:
            return []
        if not isinstance(got, (dict, list)):
            unread.append(SpyUnread(source=source, table=name, key=name, reason=_NOT_A_TABLE))
            return []
        return _entries(got)

    kos: dict[str, int | None] = {}
    kos_rows: list[SpyKos] = []
    for k, v in table("KOSData"):
        key = _key_text(k)
        added = _seconds(v)
        kos[key] = added
        kos_rows.append(SpyKos(key=key, source=source, added=added))
    ignored: set[str] = set()
    ignored_rows: list[SpyIgnored] = []
    for k, _ in table("IgnoreData"):
        key = _key_text(k)
        ignored.add(key)
        ignored_rows.append(SpyIgnored(key=key, source=source))
    players: list[SpyPlayer] = []
    for k, v in table("PlayerData"):
        key = _key_text(k)
        if not isinstance(v, dict):
            # An empty table is `{}`; a non-empty list is not a player row.
            unread.append(
                SpyUnread(source=source, table="PlayerData", key=key, reason=_NOT_A_TABLE)
            )
            continue
        players.append(_player(key, v, source, kos, ignored))
    encounters: list[SpyEncounter] = []
    for position, (_, v) in enumerate(table("Encounters"), 1):
        if not isinstance(v, dict):
            unread.append(
                SpyUnread(
                    source=source, table="Encounters", key=f"[{position}]", reason=_NOT_A_TABLE
                )
            )
            continue
        encounters.append(_encounter(position, v, source))
    version = value.get("version")
    return SpyCharacterData(
        source=source,
        present=True,
        version=version if isinstance(version, str) else None,
        warning=_version_warning(version),
        players=players,
        kos=kos_rows,
        ignored=ignored_rows,
        encounters=encounters,
        unread=unread,
        other_keys=[_key_text(k) for k in value if k not in _CHARACTER_TABLES],
    )


def parse_character(data: bytes, source: SpySource) -> SpyCharacterData:
    """A character's `Spy.lua`, from its bytes. A file that is not data is
    refused by `luadata` (`LuaDataError`, with a position)."""
    return load_character(luadata.parse(data).to_python().get(CHARACTER_VARIABLE), source)


# ─── the account's file ──────────────────────────────────────────────────────

_ACCOUNT_TABLES: Final = ("kosData", "playerNotes", "playerTags")


# Spy's `PurgeData` choices, in days (`Spy.lua`, the options table).
_PURGE_DAYS: Final[dict[str, int]] = {
    "OneDay": 1,
    "FiveDays": 5,
    "TenDays": 10,
    "ThirtyDays": 30,
    "SixtyDays": 60,
    "NinetyDays": 90,
}


def _settings(value: dict[object, object]) -> list[SpySettings]:
    """Each character `profileKeys` names whose profile is in `profiles`,
    with the options that decide what its list holds; a value of another
    type than Spy writes counts as Spy's default, as Spy's own checks
    treat it."""
    keys = value.get("profileKeys")
    profiles = value.get("profiles")
    if not isinstance(keys, dict) or not isinstance(profiles, dict):
        return []
    out: list[SpySettings] = []
    for character, profile_name in keys.items():
        profile = profiles.get(profile_name) if isinstance(profile_name, str) else None
        if not isinstance(character, str) or not isinstance(profile, dict):
            continue
        purge = profile.get("PurgeData")
        days: int | None = 90
        if isinstance(purge, str):
            days = _PURGE_DAYS.get(purge)
        out.append(
            SpySettings(
                character=character,
                purge_days=days,
                purge_setting=purge if isinstance(purge, str) and days is None else None,
                purge_counted=profile.get("PurgeWinLossData") is True,
                purge_kos=profile.get("PurgeKoS") is True,
                uses_shared_data=profile.get("UseData") is True,
            )
        )
    return out


def settings_for(accounts: Sequence[SpyAccountData], source: SpySource) -> SpySettings | None:
    """The Spy options of the character of `source`, from its account's
    `SpyDB` (the character's folder name and Spy's name for it compared
    folded, "First-Second" and "First Second" alike); None when the account
    file names no profile for it."""
    if source.character is None:
        return None
    label = fold(source.character.rsplit("/", 1)[-1])
    for a in accounts:
        if a.source.account != source.account or a.source.flavor_folder != source.flavor_folder:
            continue
        for s in a.settings:
            if fold(s.character) == label:
                return s
    return None


def uses_shared_data(accounts: Sequence[SpyAccountData], source: SpySource) -> bool:
    """Whether the character of `source` takes rows other Spy users share."""
    found = settings_for(accounts, source)
    return found is not None and found.uses_shared_data


def load_account(value: object, source: SpySource) -> SpyAccountData:
    """`SpyDB` as `luadata.LuaDocument.to_python` gives it (None when the
    file does not assign it)."""
    if value is None:
        return SpyAccountData(source=source, present=False)
    if not isinstance(value, dict):
        raise SpyError(
            "This Spy file does not hold the table Spy saves for the account, so it was not read."
        )
    unread: list[SpyUnread] = []

    def nested(name: str, value: object, path: str) -> list[tuple[str, object]]:
        if not isinstance(value, (dict, list)):
            unread.append(SpyUnread(source=source, table=name, key=path, reason=_NOT_A_TABLE))
            return []
        return [(_key_text(k), v) for k, v in _entries(value)]

    shared: list[SpySharedKos] = []
    if "kosData" in value:
        for under, by_faction in nested("kosData", value["kosData"], "kosData"):
            for faction, by_char in nested("kosData", by_faction, under):
                for char, players in nested("kosData", by_char, f"{under}/{faction}"):
                    for key, entry in nested("kosData", players, f"{under}/{faction}/{char}"):
                        added = (
                            _whole(entry.get("added"), 0, TIME_MAX)
                            if isinstance(entry, dict)
                            else None
                        )
                        shared.append(
                            SpySharedKos(
                                key=key,
                                source=source,
                                kept_under=under,
                                faction=faction,
                                character=char,
                                added=added,
                            )
                        )
    notes: list[SpyNote] = []
    if "playerNotes" in value:
        for under, by_player in nested("playerNotes", value["playerNotes"], "playerNotes"):
            for key, text in nested("playerNotes", by_player, under):
                if isinstance(text, str):
                    notes.append(SpyNote(key=key, source=source, kept_under=under, text=text))
                else:
                    unread.append(
                        SpyUnread(
                            source=source,
                            table="playerNotes",
                            key=f"{under}/{key}",
                            reason="not text",
                        )
                    )
    tags: list[SpyPlayerTag] = []
    if "playerTags" in value:
        for under, by_player in nested("playerTags", value["playerTags"], "playerTags"):
            for key, tag in nested("playerTags", by_player, under):
                if tag in ("spare", "rival"):
                    tags.append(SpyPlayerTag(key=key, source=source, kept_under=under, tag=tag))
                elif isinstance(tag, str):
                    tags.append(
                        SpyPlayerTag(key=key, source=source, kept_under=under, other_tag=tag)
                    )
                else:
                    unread.append(
                        SpyUnread(
                            source=source,
                            table="playerTags",
                            key=f"{under}/{key}",
                            reason="not text",
                        )
                    )
    settings = _settings(value)
    return SpyAccountData(
        source=source,
        present=True,
        settings=settings,
        uses_shared_data=[s.character for s in settings if s.uses_shared_data],
        shared_kos=shared,
        notes=notes,
        tags=tags,
        unread=unread,
        other_keys=[_key_text(k) for k in value if k not in _ACCOUNT_TABLES],
    )


def parse_account(data: bytes, source: SpySource) -> SpyAccountData:
    """The account's `Spy.lua`, from its bytes. A file that is not data is
    refused by `luadata` (`LuaDataError`, with a position)."""
    return load_account(luadata.parse(data).to_python().get(ACCOUNT_VARIABLE), source)


# ─── every file of a flavor ──────────────────────────────────────────────────


def is_spy_file_name(name: str) -> bool:
    """`Spy.lua` in any spelling of its case (never `Spy.lua.bak`)."""
    return name.casefold() == FILE_NAME.casefold()


def spy_files(
    files: Iterable[layout.SavedVariablesFile], account: str | None = None
) -> list[layout.SavedVariablesFile]:
    """The `Spy.lua` files among a layout's SavedVariables files, the
    account ones first, each group by path; only `account`'s when given."""
    found = [
        f
        for f in files
        if not f.backup
        and is_spy_file_name(f.path.rsplit("/", 1)[-1])
        and (account is None or f.account == account)
    ]
    return sorted(found, key=lambda f: (f.scope != "account", f.path))


_READ_ERRORS = (WowlabError, luadata.LuaDataError, OSError)


def source_of(f: layout.SavedVariablesFile, flavor_folder: str | None = None) -> SpySource:
    character = (
        f"{f.realm_folder}/{f.character_folder}"
        if f.scope == "character" and f.realm_folder is not None and f.character_folder
        else None
    )
    return SpySource(
        file=f.path, account=f.account, character=character, flavor_folder=flavor_folder
    )


def survey(lay: layout.Layout, *, account: str | None = None) -> SpySurvey:
    """Every `Spy.lua` in one flavor folder (only `account`'s when given),
    each read read-only from the install root one folder at a time
    (`luadata.read`); a file that cannot be read is listed in `errors` with
    the reason, and the others are read."""
    accounts: list[SpyAccountData] = []
    characters: list[SpyCharacterData] = []
    errors: list[SpyFileError] = []
    for f in spy_files(lay.saved_variables(), account):
        source = source_of(f, lay.flavor_folder)
        try:
            values = luadata.read(lay.flavor_path / f.path, root=lay.install_root).to_python()
            if f.scope == "account":
                accounts.append(load_account(values.get(ACCOUNT_VARIABLE), source))
            else:
                characters.append(load_character(values.get(CHARACTER_VARIABLE), source))
        except _READ_ERRORS as exc:
            errors.append(SpyFileError(source=source, error=str(exc)))
    return SpySurvey(accounts=accounts, characters=characters, errors=errors)


# ─── finding ─────────────────────────────────────────────────────────────────

_IGNORED = str.maketrans("", "", " '" + chr(0x2019) + "-")


def fold(text: str) -> str:
    """`text` as typed names are compared: case folded, with spaces,
    hyphens and the apostrophes U+0027 and U+2019 left out, so "Sonja
    Frayle", "sonja-frayle" and "Sonja-Frayle" are the same name (Spy's
    account lists write the two names with a space, its character lists
    with a hyphen)."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold()).translate(
        _IGNORED
    )


def named[T: (SpyPlayer, SpyKos, SpyIgnored, SpySharedKos, SpyNote, SpyPlayerTag)](
    rows: Sequence[T], typed: str
) -> list[T]:
    """The rows whose key is `typed` exactly, else every one whose folded
    key equals the folded `typed`."""
    exact = [r for r in rows if r.key == typed]
    if exact:
        return exact
    want = fold(typed)
    return [r for r in rows if fold(r.key) == want]


def encounters_with(rows: Sequence[SpyEncounter], typed: str) -> list[SpyEncounter]:
    """The fights against the player `typed` names, by the entry's `name`."""
    want = fold(typed)
    return [e for e in rows if e.name is not None and (e.name == typed or fold(e.name) == want)]


def read_character(path: Path, source: SpySource, *, root: Path | None = None) -> SpyCharacterData:
    """Read a character's `Spy.lua` (read-only; writes nothing, L1)."""
    return load_character(luadata.read(path, root=root).to_python().get(CHARACTER_VARIABLE), source)


def read_account(path: Path, source: SpySource, *, root: Path | None = None) -> SpyAccountData:
    """Read the account's `Spy.lua` (read-only; writes nothing, L1)."""
    return load_account(luadata.read(path, root=root).to_python().get(ACCOUNT_VARIABLE), source)
