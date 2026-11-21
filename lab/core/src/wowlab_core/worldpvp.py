"""worldpvp: the players who fought you in the open world, read from the
client's combat log (docs/LAB_PLAN.md §21.3, as built by M20-18).

Pure and read-only (L1): the log is read through `combatlog.read_log`,
which opens it read-only, one chunk at a time; nothing is written anywhere
and no database is touched. `crewdb` keeps what this finds.

**What the log can see.** Only what the client wrote while `/combatlog` was
on, and only what it showed you: your own hits always, other players' only
within the client's range (not yet confirmed how far). The client writes in
chunks, so a read while you play lags behind it. Only damage is read:
players who only stunned, feared or missed you are not counted.

**Who you are.** The GUIDs the lab-addon stored for your characters
(`owners`, one per character that has one). Never a guess from the flags
when a GUID is known; a caller with no GUID does not call this. Who is
writing the log (`ZoneState.logging`) is the latest of your GUIDs to appear
with flags "mine" and "player"; it is kept with the log's offset, so a read
that continues knows it.

**A fight** is one damage line (`SWING_DAMAGE`, `RANGE_DAMAGE`,
`SPELL_DAMAGE`, `SPELL_PERIODIC_DAMAGE`) between you, or your pet or
guardian, and a hostile player or that player's pet or guardian:

- *you*: a unit whose GUID is one of `owners`;
- *your pet or guardian*: a unit whose flags say affiliation "mine" and type
  pet or guardian while one of your characters is the one logging, or whose
  advanced block names one of your GUIDs as its owner (the block's first
  field is the unit, its second the owner; the real log shows the block
  describing the source of a swing and the target of a spell);
- *a hostile player*: a GUID in the client's player shape
  (`Player-<digits>-<8 hex digits>`) whose flags say reaction hostile and
  type player, and not affiliation party or raid (a charmed or
  mind-controlled member of your group is no opponent);
- *their pet or guardian*: a hostile unit of type pet or guardian hitting
  you or your pet whose advanced block names its owner (a swing), when the
  log has named that owner on another line; a hit whose owner the log does
  not name is counted in `pet_hits_no_owner`, never guessed.

The flags describe a unit as the logging player sees it (docs/LAB_FORMATS.md
§8); the bits for hostile, party, raid, pet and guardian are community
documentation that no fixture shows (not yet confirmed).

**Where.** Only a fight in the open world counts: after a `ZONE_CHANGE`
line whose difficulty (its third field) is 0, as the one real log shows for
open-world Tirisfal Glades (`ZONE_CHANGE,0,"Tirisfal Glades",0`), outside an
`ARENA_MATCH_START` .. `ARENA_MATCH_END` span, and on no map that the crew
database keeps a battleground or arena match on (`pvp_maps`: the match's
`map_id` is the instance id the client gives, which is `ZONE_CHANGE`'s first
field by community documentation). That battlegrounds log a difficulty
other than 0, and the arena lines themselves, are not yet confirmed. A
fight before the log's first zone line is not counted (`no_zone`). A duel
cannot be told from the log, so a duel in the open world is counted.

**An encounter.** A fight joins the open encounter of the same character of
yours that already holds the same opponent, when its last fight was at most
`ENCOUNTER_GAP_SECONDS` before; else the one whose last fight was at most
`JOIN_SECONDS` before (several players at once); else it starts one. An
encounter holds the opponents (`foe`), the friendly players who damaged one
of its opponents while it was open (`ally`), its start, end and zone, your
death and your killing blows (`PARTY_KILL` by you or your pet on an
opponent; that the client writes it for a player is not yet confirmed).

**Your death** is a `UNIT_DIED` of your GUID. Its killing hit is the damage
line to you whose overkill field (the third field after the advanced block)
is 0 or more, at most `KILLING_HIT_SECONDS` before: the real log shows 0 on
the killing swing and -1 on every other hit. A killing hit from an opponent
in the encounter credits them and marks the encounter `died`; one from
anything else credits no one. With no killing hit in the log, the hostile
player who last damaged you at most `DEATH_WINDOW_SECONDS` before is
credited. A death later than that, with no killing hit from an opponent,
is not the encounter's.

**Names.** A unit name is `"<Name>-<Realm>-<Region>"` in the real log, the
region empty on Forever and the realm without its spaces. The key is
`<Name>-<Realm>` with the realm part normalized in one place
(`normalize_realm`: spaces and hyphens removed), as the lab-addon builds the
key of a player on your own realm. For a player on another realm the addon
keeps the realm the client gives, and whether that holds spaces or hyphens
is not yet confirmed, so the GUID is kept beside the key for linking. A
name with two parts is taken as `<Name>-<Realm>` and one with one part as
on your own realm (both not yet confirmed).

**Bounds.** A line over 1 MiB is the truncated `Unparsed` the reader always
gives, never read as a fight. A line whose fields cannot be read is skipped
and counted (`bad_lines`): nothing in a line's content stops a read. At
most `MAX_OPEN` encounters are open at once (the oldest is closed first),
and `SIDE_MAX` players on each side of one. Once a read holds `MAX_ENCOUNTERS`
encounters or `MAX_READ_PLAYERS` players it stops at the first line with no
encounter open (`more`), so a caller keeps them and reads on from `end`
and no encounter is cut in two; at twice either bound (encounters going on
without a pause) it stops before the next line that would start one; memory is bounded
by those whatever the log's size. At most `MAX_NAMES` player names are
remembered for naming a pet's owner, and the latest `SAVED_NAMES` of them
are kept with the offset (`ZoneState.names`).
"""

from __future__ import annotations

import calendar
import dataclasses
import re
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wowlab_core import combatlog
from wowlab_core.combatlog import Quoted, Record, Skipped, Unparsed

__all__ = [
    "DAMAGE_EVENTS",
    "DEATH_WINDOW_SECONDS",
    "ENCOUNTER_GAP_SECONDS",
    "JOIN_SECONDS",
    "KILLING_HIT_SECONDS",
    "MAX_ENCOUNTERS",
    "MAX_NAMES",
    "MAX_OPEN",
    "MAX_READ_PLAYERS",
    "SAVED_NAMES",
    "SIDE_MAX",
    "WORLD_EVENTS",
    "Side",
    "WorldEncounter",
    "WorldPlayer",
    "WorldScan",
    "ZoneState",
    "normalize_realm",
    "player_key",
    "read_world",
    "scan_entries",
    "seconds_of",
]

ENCOUNTER_GAP_SECONDS = 300
"""Fights with the same player at most this far apart are one encounter."""
JOIN_SECONDS = 60
"""A fight with another player this soon after an encounter's last joins it."""
KILLING_HIT_SECONDS = 5
"""A killing hit (overkill 0 or more) at most this long before your death is
its cause."""
DEATH_WINDOW_SECONDS = 30
"""With no killing hit in the log, your death is credited to the hostile
player who last damaged you at most this long before it."""
MAX_OPEN = 64
MAX_ENCOUNTERS = 20_000
MAX_READ_PLAYERS = 20_000
"""A read stops when its encounters hold this many players (one per player
per encounter); the caller keeps them and reads on."""
MAX_NAMES = 50_000
SAVED_NAMES = 2_000
"""At most this many other players' names (the latest seen) are kept with a
log's offset, for naming a pet's owner on a read that continues."""
SIDE_MAX = 100
"""At most this many players on each side of one encounter (as a match's)."""
KEY_MAX_BYTES = 256
ZONE_MAX_BYTES = 256

DAMAGE_EVENTS = frozenset({"SWING_DAMAGE", "RANGE_DAMAGE", "SPELL_DAMAGE", "SPELL_PERIODIC_DAMAGE"})
WORLD_EVENTS = frozenset(
    {
        "COMBAT_LOG_VERSION",
        "ZONE_CHANGE",
        "ARENA_MATCH_START",
        "ARENA_MATCH_END",
        "UNIT_DIED",
        "PARTY_KILL",
        *DAMAGE_EVENTS,
    }
)
"""The events read (`events=` on `combatlog.read_log`): every other line is
not tokenized."""

# Unit-flag fields (the client's COMBATLOG_OBJECT_* values, community
# documentation; the real log shows the owner 0x511 and an NPC 0x10a48).
_AFFILIATION_PARTY = 0x00000002
_AFFILIATION_RAID = 0x00000004
_REACTION_MASK = 0x000000F0
_REACTION_FRIENDLY = 0x00000010
_REACTION_HOSTILE = 0x00000040
_TYPE_PET = 0x00001000
_TYPE_GUARDIAN = 0x00002000
_SWING_ADVANCED = 8  # the advanced block's first field on a swing
_SPELL_ADVANCED = 11  # and on a spell or ranged line (after id, name, school)
_ADVANCED_FIELDS = 19  # as the real log shows on every line that has the block
_PLAYER_GUID = re.compile(r"Player-[0-9]+-[0-9A-Fa-f]{8}", re.ASCII)
_INT = re.compile(r"-?[0-9]{1,12}", re.ASCII)
_HEX = re.compile(r"0x[0-9A-Fa-f]{1,8}", re.ASCII)
_TIME = re.compile(
    r"([0-9]{1,2})/([0-9]{1,2})(?:/([0-9]{4}))? ([0-9]{1,2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]+)?"
    r"(?:([+-])([0-9]{1,2})(?::?([0-9]{2}))?)?",
    re.ASCII,
)
_REALM_DROPPED = re.compile(r"[ \t\n\r\f\v-]")

Side = Literal["foe", "ally"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ZoneState(_Model):
    """Where the log was as of the last line read: the latest
    `ZONE_CHANGE`'s zone name, instance id and difficulty (None before any),
    whether an arena match is under way, which of your GUIDs is writing the
    log (`logging`, as the reader compares them: upper case), and the realm
    each of your characters' own unit names gave (`homes`), and the latest
    `SAVED_NAMES` other players' unit names by GUID (`names`), so a pet's
    owner named before the offset is still known. Kept with a log's offset,
    so a read that continues knows where the log was."""

    zone: str | None = None
    instance_id: int | None = None
    difficulty: int | None = None
    arena: bool = False
    logging: str | None = None
    homes: dict[str, str] = Field(default_factory=dict)
    names: dict[str, str] = Field(default_factory=dict)


class WorldPlayer(_Model):
    """One player in one encounter: the key (`Name-Realm`), the name and
    realm it was built from, the GUID the log gave, the side (`foe`: fought
    you; `ally`: a friendly player who damaged one of your opponents), the
    damage lines between them (or their pet) and you (a foe) or them and
    your opponents (an ally), and whether you killed them or they killed
    you."""

    key: str
    name: str
    realm: str
    guid: str
    side: Side
    hits: int
    killed_by_you: bool = False
    killed_you: bool = False


class WorldEncounter(_Model):
    """One encounter: which of your characters (`owner_guid`), when it
    started and ended (seconds since the epoch, UTC), the zone and instance
    id, whether you died in it, and its players."""

    owner_guid: str
    start: int
    end: int
    zone: str | None
    instance_id: int | None
    died: bool
    players: list[WorldPlayer]
    players_left_out: int = 0


class WorldScan(_Model):
    """What one read of a log found: the encounters (oldest first), where
    the read started and the offset just past the last whole line it read
    (`end`; a line still being written is left for the next read), where
    the log was at that point (`state`), whether it stopped at a bound with
    more to read (`more`), and counts: lines read, lines the tokenizer could
    not read, lines whose fields could not be read (`bad_lines`), fights
    counted, fights outside the open world (`outside_world`), fights before
    the log's first zone line (`no_zone`), fights whose time could not be
    read (`no_time`), hits on you from a hostile pet whose owner the log
    does not name (`pet_hits_no_owner`), and encounters not kept
    (`left_out`)."""

    start: int
    end: int
    state: ZoneState
    encounters: list[WorldEncounter] = Field(default_factory=list)
    more: bool = False
    lines: int = 0
    unparsed: int = 0
    bad_lines: int = 0
    fights: int = 0
    outside_world: int = 0
    no_zone: int = 0
    no_time: int = 0
    pet_hits_no_owner: int = 0
    left_out: int = 0


# ─── small readers ───────────────────────────────────────────────────────────


def seconds_of(timestamp: str) -> int | None:
    """A log timestamp as seconds since the epoch (UTC); None when it has no
    year or cannot be a time. With an offset (`-4`, the real log's shape)
    the time is local time at that offset; without one, this computer's
    local time (not yet confirmed: no real log lacks it)."""
    m = _TIME.fullmatch(timestamp)
    if m is None or m.group(3) is None:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour <= 24 and minute <= 59 and second <= 61):
        return None
    fields = (year, month, day, hour, minute, second, 0, 0, -1)
    try:
        if m.group(7) is None:
            return int(time.mktime(fields))
        shift = int(m.group(8)) * 3600 + int(m.group(9) or 0) * 60
        utc = calendar.timegm(fields)
    except (OverflowError, ValueError):
        return None
    return utc - shift if m.group(7) == "+" else utc + shift


def _int(value: object) -> int | None:
    """A bare decimal field (ASCII digits, an optional `-`, at most 12
    digits), or None."""
    if isinstance(value, str) and _INT.fullmatch(value):
        return int(value)
    return None


def _flags(value: object) -> int | None:
    if isinstance(value, str) and _HEX.fullmatch(value):
        return int(value, 16)
    return None


def _bytes(text: str) -> int | None:
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _name_ok(text: str) -> bool:
    """A unit name worth remembering: valid UTF-8 within a key's length."""
    size = _bytes(text)
    return size is not None and size <= KEY_MAX_BYTES


def normalize_realm(text: str) -> str:
    """A realm as wowlab keys it: spaces and hyphens removed, as the
    lab-addon keys a player on your own realm (`homeRealm` in Crew.lua).
    The one place a realm from the log, or your own stored realm, is
    normalized."""
    return _REALM_DROPPED.sub("", text)


def _parts(unit_name: str) -> tuple[str, str]:
    """(name, realm normalized) of a unit name as the log writes it; the
    realm empty when the name has no realm part."""
    parts = unit_name.split("-")
    if len(parts) >= 3:
        realm = "".join(parts[1:-1])  # the last part is the region
    elif len(parts) == 2:
        realm = parts[1]
    else:
        realm = ""
    return parts[0], normalize_realm(realm)


def player_key(unit_name: str, home_realm: str | None) -> tuple[str, str, str] | None:
    """(key, name, realm) of a player's unit name as the log writes it
    (`<Name>-<Realm>-<Region>`); None when no key can be made of it. A
    name with no realm part is on `home_realm` (not yet confirmed)."""
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in unit_name):
        return None
    name, realm = _parts(unit_name)
    if not realm and home_realm:
        realm = normalize_realm(home_realm)
    if not name or not realm:
        return None
    key = f"{name}-{realm}"
    size = _bytes(key)
    if size is None or size > KEY_MAX_BYTES:
        return None
    return key, name, realm


# ─── encounters being built ──────────────────────────────────────────────────


@dataclasses.dataclass
class _Player:
    key: str
    name: str
    realm: str
    guid: str
    side: Side
    hits: int = 0
    killed_by_you: bool = False
    killed_you: bool = False


@dataclasses.dataclass
class _Open:
    owner: str
    start: int
    last: int
    zone: str | None
    instance_id: int | None
    players: dict[str, _Player] = dataclasses.field(default_factory=dict)  # by GUID
    sides: dict[str, int] = dataclasses.field(default_factory=lambda: {"foe": 0, "ally": 0})
    left_out: int = 0
    died: bool = False
    # (time, foe GUID) of the latest hostile player damage to you.
    last_hit: tuple[int, str] | None = None

    def done(self, owner_guid: str) -> WorldEncounter:
        return WorldEncounter(
            owner_guid=owner_guid,
            start=self.start,
            end=self.last,
            zone=self.zone,
            instance_id=self.instance_id,
            died=self.died,
            players=[
                WorldPlayer(
                    key=p.key,
                    name=p.name,
                    realm=p.realm,
                    guid=p.guid,
                    side=p.side,
                    hits=p.hits,
                    killed_by_you=p.killed_by_you,
                    killed_you=p.killed_you,
                )
                for p in sorted(self.players.values(), key=lambda p: (p.side != "foe", p.key))
            ],
            players_left_out=self.left_out,
        )


Who = tuple[str, str, str]


class _Full(Exception):  # noqa: N818 - a signal, not an error
    """The read holds its bound: a line that would start an encounter is
    left for the next part, so no encounter is cut in two."""


class _Reader:
    def __init__(
        self,
        owners: Mapping[str, str | None],
        state: ZoneState | None,
        pvp_maps: frozenset[int],
    ) -> None:
        self.owners = {
            g.upper(): (normalize_realm(r) or None if r else None) for g, r in owners.items()
        }
        self.spelled = {g.upper(): g for g in owners}  # each GUID as the lab-addon stored it
        start = state or ZoneState()
        self.state = start.model_copy(update={"logging": None, "homes": {}})
        self.pvp_maps = pvp_maps
        held = start.logging.upper() if start.logging else None
        self.logging: str | None = held if held in self.owners else None
        self.homes: dict[str, str] = {
            g.upper(): r for g, r in start.homes.items() if g.upper() in self.owners
        }
        # Another player's GUID -> unit name, for a pet's owner; latest last.
        self.names: dict[str, str] = {
            g: n for g, n in start.names.items() if _PLAYER_GUID.fullmatch(g) and _name_ok(n)
        }
        self.open: list[_Open] = []
        self.out: list[WorldEncounter] = []
        self.held = 0  # players in the encounters kept and open
        self.left_out = 0
        self.fights = self.outside = self.no_zone = self.no_time = self.pet_no_owner = 0
        # Your killing hit, by your GUID: (time, the opponent's GUID or None).
        self.killing: dict[str, tuple[int, str | None]] = {}

    def full(self) -> bool:
        """At the bound: the read stops at the next line with no encounter
        open."""
        return len(self.out) >= MAX_ENCOUNTERS or self.held >= MAX_READ_PLAYERS

    def hard(self) -> bool:
        """Twice the bound (encounters started while others stayed open):
        the read stops before the next line that starts one, whatever is
        open."""
        return len(self.out) >= 2 * MAX_ENCOUNTERS or self.held >= 2 * MAX_READ_PLAYERS

    def final_state(self) -> ZoneState:
        spelled = self.spelled.get
        return self.state.model_copy(
            update={
                "logging": spelled(self.logging, self.logging) if self.logging else None,
                "homes": {spelled(g, g): r for g, r in self.homes.items()},
                "names": dict(list(self.names.items())[-SAVED_NAMES:]),
            }
        )

    # where -------------------------------------------------------------------

    def zone(self, fields: tuple[object, ...]) -> None:
        name = fields[1] if len(fields) > 1 else None
        text = name.text if isinstance(name, Quoted) else None
        if text is not None and (_bytes(text) or ZONE_MAX_BYTES + 1) > ZONE_MAX_BYTES:
            text = None
        self.state = ZoneState(
            zone=text,
            instance_id=_int(fields[0]) if fields else None,
            difficulty=_int(fields[2]) if len(fields) > 2 else None,
            arena=False,
        )

    def in_world(self) -> bool | None:
        s = self.state
        if s.difficulty is None and s.instance_id is None and s.zone is None:
            return None
        return (
            s.difficulty == 0
            and not s.arena
            and (s.instance_id is None or s.instance_id not in self.pvp_maps)
        )

    # who ---------------------------------------------------------------------

    def owner_of(
        self, guid: str, flags: int | None, fields: tuple[object, ...], adv: int
    ) -> str | None:
        """The owner GUID this unit is (you) or belongs to (your pet or
        guardian), or None."""
        up = guid.upper()
        if up in self.owners:
            return up
        if flags is not None and flags & combatlog.AFFILIATION_MASK == combatlog.AFFILIATION_MINE:
            kind = flags & combatlog.TYPE_MASK
            if kind in (_TYPE_PET, _TYPE_GUARDIAN) and self.logging is not None:
                return self.logging
        owner = self.adv_owner(guid, fields, adv)
        if owner is not None and owner.upper() in self.owners:
            return owner.upper()
        return None

    @staticmethod
    def adv_owner(guid: str, fields: tuple[object, ...], adv: int) -> str | None:
        """The owner field of the advanced block when the block describes
        `guid`."""
        if len(fields) > adv + 1 and fields[adv] == guid:
            owner = fields[adv + 1]
            if isinstance(owner, str):
                return owner
        return None

    def home_of(self, owner: str) -> str | None:
        return self.homes.get(owner) or self.owners.get(owner)

    def foe(self, guid: object, name: object, flags: int | None, owner: str) -> Who | None:
        """A hostile player as a key, or None."""
        if not isinstance(guid, str) or not _PLAYER_GUID.fullmatch(guid):
            return None
        if guid.upper() in self.owners or not isinstance(name, Quoted) or flags is None:
            return None
        if flags & combatlog.TYPE_MASK != combatlog.TYPE_PLAYER:
            return None
        if flags & combatlog.AFFILIATION_MASK in (_AFFILIATION_PARTY, _AFFILIATION_RAID):
            return None  # in your group: charmed or mind-controlled, not an opponent
        return player_key(name.text, self.home_of(owner))

    def friend(self, guid: object, name: object, flags: int | None, owner: str) -> Who | None:
        if not isinstance(guid, str) or not _PLAYER_GUID.fullmatch(guid):
            return None
        if guid.upper() in self.owners or not isinstance(name, Quoted) or flags is None:
            return None
        if flags & combatlog.TYPE_MASK != combatlog.TYPE_PLAYER:
            return None
        if flags & _REACTION_MASK != _REACTION_FRIENDLY:
            return None
        return player_key(name.text, self.home_of(owner))

    def note_unit(self, guid: object, name: object, flags: int | None) -> None:
        if not isinstance(guid, str):
            return
        up = guid.upper()
        if up not in self.owners:
            if (
                isinstance(name, Quoted)
                and _PLAYER_GUID.fullmatch(guid)
                and _name_ok(name.text)
                and (guid in self.names or len(self.names) < MAX_NAMES)
            ):
                self.names.pop(guid, None)  # the latest seen goes last
                self.names[guid] = name.text
            return
        if (
            flags is not None
            and flags & combatlog.AFFILIATION_MASK == combatlog.AFFILIATION_MINE
            and flags & combatlog.TYPE_MASK == combatlog.TYPE_PLAYER
        ):
            self.logging = up
        if up not in self.homes and isinstance(name, Quoted):
            _, realm = _parts(name.text)
            if realm:
                self.homes[up] = realm

    # encounters --------------------------------------------------------------

    def close_before(self, now: int) -> None:
        keep: list[_Open] = []
        for e in self.open:
            if now - e.last > ENCOUNTER_GAP_SECONDS:
                self.finish(e)
            else:
                keep.append(e)
        self.open = keep

    def finish(self, e: _Open) -> None:
        if len(self.out) >= 2 * MAX_ENCOUNTERS:
            self.left_out += 1
            return
        self.out.append(e.done(self.spelled.get(e.owner, e.owner)))

    def encounter(self, owner: str, foe: str, now: int) -> _Open:
        mine = [e for e in self.open if e.owner == owner]
        for e in mine:
            if foe in e.players and e.players[foe].side == "foe":
                return e
        for e in mine:
            if now - e.last <= JOIN_SECONDS:
                return e
        if self.hard() or (self.full() and not self.open):
            raise _Full  # a new encounter waits for the next part
        if len(self.open) >= MAX_OPEN:
            oldest = min(self.open, key=lambda e: e.last)
            self.open.remove(oldest)
            self.finish(oldest)
        e = _Open(
            owner=owner,
            start=now,
            last=now,
            zone=self.state.zone,
            instance_id=self.state.instance_id,
        )
        self.open.append(e)
        return e

    def add(self, e: _Open, guid: str, who: Who, side: Side) -> _Player | None:
        held = e.players.get(guid)
        if held is not None:
            if held.side == "ally" and side == "foe":  # a foe once is a foe
                e.sides["ally"] -= 1
                e.sides["foe"] += 1
                held.side = "foe"
            return held
        if e.sides[side] >= SIDE_MAX:
            e.left_out += 1
            return None
        key, name, realm = who
        player = _Player(key=key, name=name, realm=realm, guid=guid, side=side)
        e.players[guid] = player
        e.sides[side] += 1
        self.held += 1
        return player

    def damage(self, rec: Record, now: int | None) -> None:
        f = rec.fields
        if len(f) < 8:
            return
        adv = _SWING_ADVANCED if rec.event == "SWING_DAMAGE" else _SPELL_ADVANCED
        src, src_name, src_flags = f[0], f[1], _flags(f[2])
        dst, dst_name, dst_flags = f[4], f[5], _flags(f[6])
        self.note_unit(src, src_name, src_flags)
        self.note_unit(dst, dst_name, dst_flags)
        if not isinstance(src, str) or not isinstance(dst, str):
            return
        src_owner = self.owner_of(src, src_flags, f, adv)
        dst_owner = self.owner_of(dst, dst_flags, f, adv)
        hostile_src = src_flags is not None and src_flags & _REACTION_MASK == _REACTION_HOSTILE
        hostile_dst = dst_flags is not None and dst_flags & _REACTION_MASK == _REACTION_HOSTILE
        fight: tuple[str, str, Who] | None = None
        pet_unnamed = False
        if src_owner is not None and dst_owner is None and hostile_dst:
            who = self.foe(dst, dst_name, dst_flags, src_owner)
            if who is not None:
                fight = (src_owner, dst, who)
        elif dst_owner is not None and src_owner is None and hostile_src:
            who = self.foe(src, src_name, src_flags, dst_owner)
            if who is not None:
                fight = (dst_owner, src, who)
            elif src_flags is not None and src_flags & combatlog.TYPE_MASK in (
                _TYPE_PET,
                _TYPE_GUARDIAN,
            ):
                fight, pet_unnamed = self.pets_owner(src, f, adv, dst_owner)
        you_hit = dst_owner is not None and dst.upper() == dst_owner and src_owner is None
        if you_hit and now is not None:
            overkill = self.overkill(f, adv, src, dst)
            if overkill is not None and overkill >= 0:
                self.killing[dst_owner or ""] = (now, fight[1] if fight is not None else None)
        if fight is None:
            if pet_unnamed and self.in_world():
                self.pet_no_owner += 1
            self.ally(src, src_name, src_flags, dst, now)
            return
        owner, foe, who = fight
        if now is None:
            self.no_time += 1
            return
        world = self.in_world()
        if world is None:
            self.no_zone += 1
            return
        if not world:
            self.outside += 1
            return
        self.close_before(now)
        e = self.encounter(owner, foe, now)  # may stop the part: count after it
        self.fights += 1
        e.last = max(e.last, now)
        p = self.add(e, foe, who, "foe")
        if p is not None:
            p.hits += 1
        if you_hit:
            e.last_hit = (now, foe)

    def pets_owner(
        self, pet: str, f: tuple[object, ...], adv: int, you: str
    ) -> tuple[tuple[str, str, Who] | None, bool]:
        """A hostile pet's or guardian's hit on you or your pet as a fight
        with its owner, when the advanced block names the owner and the log
        has named that player; else (None, True): not guessed."""
        owner = self.adv_owner(pet, f, adv)
        if owner is None or not _PLAYER_GUID.fullmatch(owner) or owner.upper() in self.owners:
            return None, True
        name = self.names.get(owner)
        if name is None:
            return None, True
        who = player_key(name, self.home_of(you))
        if who is None:
            return None, True
        return (you, owner, who), False

    @staticmethod
    def overkill(f: tuple[object, ...], adv: int, src: str, dst: str) -> int | None:
        """The damage line's overkill field: the third after the advanced
        block (or after the spell fields, with no block)."""
        block = len(f) >= adv + _ADVANCED_FIELDS and f[adv] in (src, dst)
        at = adv + (_ADVANCED_FIELDS if block else 0) + 2
        return _int(f[at]) if len(f) > at else None

    def ally(
        self, src: object, src_name: object, src_flags: int | None, dst: object, now: int | None
    ) -> None:
        """A friendly player's damage on an opponent of an open encounter."""
        if now is None or not self.open or not isinstance(dst, str):
            return
        for e in self.open:
            held = e.players.get(dst)
            if held is None or held.side != "foe" or now - e.last > ENCOUNTER_GAP_SECONDS:
                continue
            who = self.friend(src, src_name, src_flags, e.owner)
            if who is None or not isinstance(src, str):
                return
            p = self.add(e, src, who, "ally")
            if p is not None:
                p.hits += 1
            return

    def died(self, rec: Record, now: int | None) -> None:
        f = rec.fields
        if len(f) < 5 or now is None or not isinstance(f[4], str):
            return
        up = f[4].upper()
        if up not in self.owners:
            return
        hit = self.killing.pop(up, None)
        killing = hit if hit is not None and 0 <= now - hit[0] <= KILLING_HIT_SECONDS else None
        for e in self.open:
            if e.owner != up or now - e.last > ENCOUNTER_GAP_SECONDS:
                continue
            recent = e.last_hit is not None and now - e.last_hit[0] <= DEATH_WINDOW_SECONDS
            killer: _Player | None = None
            if killing is not None:
                if killing[1] is not None:
                    killer = e.players.get(killing[1])
                    if killer is not None and killer.side != "foe":
                        killer = None
                e.died = killer is not None or recent
            elif recent:
                assert e.last_hit is not None
                killer = e.players.get(e.last_hit[1])
                e.died = True
            if killer is not None:
                killer.killed_you = True
            if e.died:
                e.last = max(e.last, now)
            e.last_hit = None
            return

    def kill(self, rec: Record, now: int | None) -> None:
        f = rec.fields
        if len(f) < 8 or now is None or not isinstance(f[0], str) or not isinstance(f[4], str):
            return
        owner = self.owner_of(f[0], _flags(f[2]), f, len(f))
        if owner is None:
            return
        for e in self.open:
            held = e.players.get(f[4])
            if e.owner == owner and held is not None and held.side == "foe":
                held.killed_by_you = True
                e.last = max(e.last, now)
                return

    # the whole read ----------------------------------------------------------

    def entry(self, entry: Record) -> None:
        event = entry.event
        if event == "ZONE_CHANGE":
            self.zone(entry.fields)
            return
        if event == "ARENA_MATCH_START":
            self.state = self.state.model_copy(update={"arena": True})
            return
        if event == "ARENA_MATCH_END":
            self.state = self.state.model_copy(update={"arena": False})
            return
        if event in DAMAGE_EVENTS:
            self.damage(entry, seconds_of(entry.timestamp))
        elif event == "UNIT_DIED":
            self.died(entry, seconds_of(entry.timestamp))
        elif event == "PARTY_KILL":
            self.kill(entry, seconds_of(entry.timestamp))

    def end(self) -> None:
        for e in sorted(self.open, key=lambda e: e.start):
            self.finish(e)
        self.open = []
        self.out.sort(key=lambda e: (e.start, e.owner_guid))


def _length(entry: Record | Unparsed | Skipped) -> int:
    if isinstance(entry, Unparsed):
        return entry.length + len(entry.ending)
    raw = entry.raw
    try:
        size = len(raw.encode("utf-8", "surrogateescape"))
    except UnicodeEncodeError:
        size = len(raw.encode("utf-8", "surrogatepass"))
    return size + len(entry.ending)


def scan_entries(
    entries: Iterable[Record | Unparsed | Skipped],
    owners: Mapping[str, str | None],
    *,
    start: int = 0,
    state: ZoneState | None = None,
    pvp_maps: Iterable[int] = (),
) -> WorldScan:
    """The open-world encounters in a log's lines (as `combatlog.read_log`
    yields them, from `start`), for your characters' GUIDs `owners` (each
    with its realm as the lab-addon stored it, or None). `state` is where
    the log was at `start` (a read that continues); `pvp_maps` the instance
    ids of the battlegrounds and arenas the crew database keeps matches on.
    A last line with no line break (still being written) is not read, and
    `end` is its offset, so the next read takes it whole. A read that
    holds `MAX_ENCOUNTERS` or `MAX_READ_PLAYERS` stops at the first line
    with no encounter open, and at twice either bound before the line that
    would start one (`more`, `end` that line's offset)."""
    reader = _Reader(owners, state, frozenset(pvp_maps))
    lines = unparsed = bad = 0
    end = start
    more = False
    for entry in entries:
        if entry.ending == "":
            end = entry.offset  # still being written: the next read takes it
            break
        if reader.full() and not reader.open:
            end, more = entry.offset, True
            break
        if isinstance(entry, Record):
            try:
                reader.entry(entry)
            except _Full:
                end, more = entry.offset, True  # this line starts the next part
                break
            except Exception:  # a line's content never stops a read
                bad += 1
        elif isinstance(entry, Unparsed):
            unparsed += 1
        lines += 1
        end = entry.offset + _length(entry)
    reader.end()
    return WorldScan(
        start=start,
        end=end,
        state=reader.final_state(),
        encounters=reader.out,
        more=more,
        lines=lines,
        unparsed=unparsed,
        bad_lines=bad,
        fights=reader.fights,
        outside_world=reader.outside,
        no_zone=reader.no_zone,
        no_time=reader.no_time,
        pet_hits_no_owner=reader.pet_no_owner,
        left_out=reader.left_out,
    )


# ─── the byte test (`skip=`) ─────────────────────────────────────────────────

_DAMAGE_BYTES = frozenset(e.encode("ascii") for e in DAMAGE_EVENTS)
_PLAYER = b"Player-"
_HEX_BYTES = re.compile(rb"0x[0-9A-Fa-f]{1,8}")
_MINE_PLAYER = (combatlog.AFFILIATION_MASK | combatlog.TYPE_MASK, 0x1 | 0x400)


class _Skipper:
    """Which damage lines can be left untokenized: every `Player-` in the
    line is one of your GUIDs (so it names no other player and can be no
    fight and no ally's damage), the line is not a hit on you (your killing
    hit is read from those), and reading it would teach the reader nothing:
    it does not change who is logging, and it is not the first line naming
    one of your characters with a realm. The skipper follows those two
    facts line by line in the order the reader sees them; a line it cannot
    split for sure (a quoted name holding a comma) is read, and makes who
    is logging unknown to it until the next line that says, so that line
    is read too."""

    def __init__(self, owners: Iterable[str], state: ZoneState | None) -> None:
        # As the reader starts from `state` (`_Reader.__init__`).
        self.mine = frozenset(g.upper().encode("ascii", "replace") for g in owners)
        held = state.logging.upper().encode("ascii", "replace") if state and state.logging else None
        self.logging = held if held in self.mine else None
        homes = state.homes if state else {}
        self.named = {g.upper().encode("ascii", "replace") for g in homes} & self.mine

    def _learns(self, guid: bytes, name: bytes, flags: bytes) -> bool:
        up = guid.upper()
        if up not in self.mine:
            return False
        learned = False
        if up not in self.named and b"-" in name and name.split(b"-")[1].strip(b'"'):
            self.named.add(up)
            learned = True
        value = int(flags, 16) if _HEX_BYTES.fullmatch(flags) else None
        if value is not None and value & _MINE_PLAYER[0] == _MINE_PLAYER[1] and up != self.logging:
            self.logging = up
            learned = True
        return learned

    def __call__(self, event: bytes, data: bytes) -> bool:
        if event not in _DAMAGE_BYTES:
            return False
        sep = data.find(b"  ")
        f = data[sep + 2 :].split(b",", 9)
        if len(f) < 9 or not (_quoted(f[2]) and _quoted(f[6])):
            self.logging = None
            return False
        learned = self._learns(f[1], f[2], f[3])
        learned = self._learns(f[5], f[6], f[7]) or learned
        if learned or f[5].upper() in self.mine:
            return False
        at = data.find(_PLAYER)
        while at != -1:
            end = data.find(b",", at)
            if data[at : len(data) if end == -1 else end].upper() not in self.mine:
                return False
            at = data.find(_PLAYER, at + len(_PLAYER))
        return True


def _quoted(field: bytes) -> bool:
    return field == b"nil" or (len(field) >= 2 and field[:1] == b'"' and field[-1:] == b'"')


def read_world(
    path: Path | str,
    owners: Mapping[str, str | None],
    *,
    start: int = 0,
    state: ZoneState | None = None,
    pvp_maps: Iterable[int] = (),
) -> WorldScan:
    """`scan_entries` on the log at `path`, read-only from `start`, with
    only `WORLD_EVENTS` tokenized, and of their damage lines only those
    `_Skipper` cannot leave out. It finds what a read of every line finds."""
    skip: Callable[[bytes, bytes], bool] = _Skipper(owners, state)
    return scan_entries(
        combatlog.read_log(path, events=WORLD_EVENTS, start=start, skip=skip),
        owners,
        start=start,
        state=state,
        pvp_maps=pvp_maps,
    )
