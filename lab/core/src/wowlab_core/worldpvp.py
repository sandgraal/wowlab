"""worldpvp: the players who fought you in the open world, read from the
client's combat log (docs/LAB_PLAN.md §21.3, as built by M20-18).

Pure and read-only (L1): the log is read through `combatlog.read_log`,
which opens it read-only, one chunk at a time; nothing is written anywhere
and no database is touched. `crewdb` keeps what this finds.

**Who you are.** The GUIDs the lab-addon stored for your characters
(`owners`, one per character that has one). Never a guess from the flags
when a GUID is known; a caller with no GUID does not call this.

**A fight** is one damage line (`SWING_DAMAGE`, `RANGE_DAMAGE`,
`SPELL_DAMAGE`, `SPELL_PERIODIC_DAMAGE`) between you, or your pet or
guardian, and a hostile player, in either direction:

- *you*: a unit whose GUID is one of `owners`;
- *your pet or guardian*: a unit whose flags say affiliation "mine" and type
  pet or guardian while one of your characters is the one logging (the
  latest of them to appear with "mine" and "player"), or whose advanced
  block names one of your GUIDs as its owner (the block's first field is the
  unit, its second the owner, as the real log shows for the source of a
  swing and the target of a spell);
- *a hostile player*: a GUID in the client's player shape
  (`Player-<digits>-<8 hex digits>`) whose flags say reaction hostile and
  type player.

The flags describe a unit as the logging player sees it (docs/LAB_FORMATS.md
§8); the bits for hostile, pet and guardian are community documentation that
no fixture shows (not yet confirmed).

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
death (`UNIT_DIED` of your GUID while it was open, credited to the hostile
player who last damaged you at most `DEATH_WINDOW_SECONDS` before), and
your killing blows (`PARTY_KILL` by you or your pet on an opponent; that the
client writes it for a player is not yet confirmed).

**Names.** A unit name is `"<Name>-<Realm>-<Region>"` in the real log, the
region empty on Forever and the realm without its spaces. The key is
`<Name>-<Realm>`, the crew book's key for the same player (the addon builds
it from the realm with its spaces removed). A name with two parts is taken
as `<Name>-<Realm>` and a name with one part as on your own realm (both not
yet confirmed); realms with a hyphen or apostrophe are not confirmed either,
so the GUID is kept beside the key for linking.

**Bounds.** A line over 1 MiB is the truncated `Unparsed` the reader always
gives, never read as a fight. At most `MAX_OPEN` encounters are open at once
(the oldest is closed first), `MAX_ENCOUNTERS` are kept per read (the rest
counted in `left_out`), and `SIDE_MAX` players on each side of one (the
rest counted in the encounter's `players_left_out`). Memory is bounded by
those, whatever the log's size.
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
    "MAX_ENCOUNTERS",
    "MAX_OPEN",
    "SIDE_MAX",
    "WORLD_EVENTS",
    "Side",
    "WorldEncounter",
    "WorldPlayer",
    "WorldScan",
    "ZoneState",
    "player_key",
    "read_world",
    "scan_entries",
    "seconds_of",
]

ENCOUNTER_GAP_SECONDS = 300
"""Fights with the same player at most this far apart are one encounter."""
JOIN_SECONDS = 60
"""A fight with another player this soon after an encounter's last joins it."""
DEATH_WINDOW_SECONDS = 30
"""Your death is credited to the hostile player who last damaged you at
most this long before it."""
MAX_OPEN = 64
MAX_ENCOUNTERS = 20_000
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
_REACTION_MASK = 0x000000F0
_REACTION_FRIENDLY = 0x00000010
_REACTION_HOSTILE = 0x00000040
_TYPE_PET = 0x00001000
_TYPE_GUARDIAN = 0x00002000
_SWING_ADVANCED = 8  # the advanced block's first field on a swing
_SPELL_ADVANCED = 11  # and on a spell or ranged line (after id, name, school)
_PLAYER_GUID = re.compile(r"Player-[0-9]+-[0-9A-Fa-f]{8}", re.ASCII)
_TIME = re.compile(
    r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))? (\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?"
    r"(?:([+-])(\d{1,2})(?::?(\d{2}))?)?",
    re.ASCII,
)

Side = Literal["foe", "ally"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ZoneState(_Model):
    """Where the log says you are, as of the last line read: the latest
    `ZONE_CHANGE`'s zone name, instance id and difficulty (None before any),
    and whether an arena match is under way. Kept with a log's offset, so a
    read that continues knows where the log was."""

    zone: str | None = None
    instance_id: int | None = None
    difficulty: int | None = None
    arena: bool = False


class WorldPlayer(_Model):
    """One player in one encounter: the key (`Name-Realm`), the name and
    realm it was built from, the GUID the log gave, the side (`foe`: fought
    you; `ally`: a friendly player who damaged one of your opponents), the
    damage lines between them and you (a foe) or them and your opponents
    (an ally), and whether you killed them or they killed you."""

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
    the log was at that point (`state`), and counts: lines read, lines the
    reader could not read, fights counted, fights outside the open world
    (`outside_world`), fights before the log's first zone line (`no_zone`),
    fights whose time could not be read (`no_time`), and encounters past
    `MAX_ENCOUNTERS` (`left_out`)."""

    start: int
    end: int
    state: ZoneState
    encounters: list[WorldEncounter] = Field(default_factory=list)
    lines: int = 0
    unparsed: int = 0
    fights: int = 0
    outside_world: int = 0
    no_zone: int = 0
    no_time: int = 0
    left_out: int = 0


# ─── small readers ───────────────────────────────────────────────────────────


def seconds_of(timestamp: str) -> int | None:
    """A log timestamp as seconds since the epoch (UTC); None when it has no
    year. With an offset (`-4`, the real log's shape) the time is local time
    at that offset; without one, this computer's local time (not yet
    confirmed: no real log lacks it)."""
    m = _TIME.fullmatch(timestamp)
    if m is None or m.group(3) is None:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
    fields = (year, month, day, hour, minute, second, 0, 0, -1)
    try:
        if m.group(8) is None:
            return int(time.mktime(fields))
        shift = int(m.group(9)) * 3600 + int(m.group(10) or 0) * 60
        utc = calendar.timegm(fields)
    except (OverflowError, ValueError):
        return None
    return utc - shift if m.group(8) == "+" else utc + shift


def _flags(value: object) -> int | None:
    if isinstance(value, str) and value.startswith("0x") and 2 < len(value) <= 10:
        try:
            return int(value, 16)
        except ValueError:
            return None
    return None


def _bytes(text: str) -> int | None:
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def player_key(unit_name: str, home_realm: str | None) -> tuple[str, str, str] | None:
    """(key, name, realm) of a player's unit name as the log writes it
    (`<Name>-<Realm>-<Region>`); None when no key can be made of it. A
    name with no realm part is on `home_realm` (not yet confirmed)."""
    parts = unit_name.split("-")
    name = parts[0]
    if len(parts) >= 3:
        realm = "-".join(parts[1:-1])
    elif len(parts) == 2:
        realm = parts[1]
    else:
        realm = home_realm or ""
    if not realm and home_realm:
        realm = home_realm
    if not name or not realm or any(ord(c) < 0x20 or ord(c) == 0x7F for c in unit_name):
        return None
    key = f"{name}-{realm}"
    size = _bytes(key)
    if size is None or size > KEY_MAX_BYTES:
        return None
    return key, name, realm


def _realm_of(unit_name: str) -> str | None:
    parts = unit_name.split("-")
    if len(parts) >= 3:
        return "-".join(parts[1:-1]) or None
    if len(parts) == 2:
        return parts[1] or None
    return None


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

    def add(self, guid: str, who: tuple[str, str, str], side: Side) -> _Player | None:
        held = self.players.get(guid)
        if held is not None:
            if held.side == "ally" and side == "foe":  # a foe once is a foe
                self.sides["ally"] -= 1
                self.sides["foe"] += 1
                held.side = "foe"
            return held
        if self.sides[side] >= SIDE_MAX:
            self.left_out += 1
            return None
        key, name, realm = who
        player = _Player(key=key, name=name, realm=realm, guid=guid, side=side)
        self.players[guid] = player
        self.sides[side] += 1
        return player

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


class _Reader:
    def __init__(
        self,
        owners: Mapping[str, str | None],
        state: ZoneState | None,
        pvp_maps: frozenset[int],
    ) -> None:
        self.owners = {g.upper(): (r.replace(" ", "") if r else None) for g, r in owners.items()}
        self.spelled = {g.upper(): g for g in owners}  # each GUID as the lab-addon stored it
        self.state = state or ZoneState()
        self.pvp_maps = pvp_maps
        self.logging: str | None = None  # the owner GUID logging now, by its flags
        self.home: dict[str, str] = {}  # owner GUID -> realm, from its own unit name
        self.open: list[_Open] = []
        self.out: list[WorldEncounter] = []
        self.left_out = 0
        self.fights = self.outside = self.no_zone = self.no_time = 0

    # where -------------------------------------------------------------------

    def zone(self, fields: tuple[object, ...]) -> None:
        def number(value: object) -> int | None:
            if isinstance(value, str) and value.lstrip("-").isdigit() and len(value) <= 12:
                return int(value)
            return None

        name = fields[1] if len(fields) > 1 else None
        text = name.text if isinstance(name, Quoted) else None
        if text is not None and (_bytes(text) or ZONE_MAX_BYTES + 1) > ZONE_MAX_BYTES:
            text = None
        self.state = ZoneState(
            zone=text,
            instance_id=number(fields[0]) if fields else None,
            difficulty=number(fields[2]) if len(fields) > 2 else None,
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
        if len(fields) > adv + 1 and fields[adv] == guid:
            owner = fields[adv + 1]
            if isinstance(owner, str) and owner.upper() in self.owners:
                return owner.upper()
        return None

    def player(
        self, guid: object, name: object, flags: int | None, owner: str
    ) -> tuple[str, str, str] | None:
        if not isinstance(guid, str) or not _PLAYER_GUID.fullmatch(guid):
            return None
        if guid.upper() in self.owners or not isinstance(name, Quoted):
            return None
        if flags is None or flags & combatlog.TYPE_MASK != combatlog.TYPE_PLAYER:
            return None
        return player_key(name.text, self.home.get(owner) or self.owners.get(owner))

    def note_owner(self, guid: object, name: object, flags: int | None) -> None:
        if not isinstance(guid, str):
            return
        up = guid.upper()
        if up not in self.owners:
            return
        if (
            flags is not None
            and flags & combatlog.AFFILIATION_MASK == combatlog.AFFILIATION_MINE
            and flags & combatlog.TYPE_MASK == combatlog.TYPE_PLAYER
        ):
            self.logging = up
        if up not in self.home and isinstance(name, Quoted):
            realm = _realm_of(name.text)
            if realm:
                self.home[up] = realm

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
        if len(self.out) >= MAX_ENCOUNTERS:
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

    def damage(self, rec: Record, now: int | None) -> None:
        f = rec.fields
        if len(f) < 8:
            return
        adv = _SWING_ADVANCED if rec.event == "SWING_DAMAGE" else _SPELL_ADVANCED
        src, src_name, src_flags = f[0], f[1], _flags(f[2])
        dst, dst_name, dst_flags = f[4], f[5], _flags(f[6])
        self.note_owner(src, src_name, src_flags)
        self.note_owner(dst, dst_name, dst_flags)
        if not isinstance(src, str) or not isinstance(dst, str):
            return
        src_owner = self.owner_of(src, src_flags, f, adv)
        dst_owner = self.owner_of(dst, dst_flags, f, adv)
        hostile_src = src_flags is not None and src_flags & _REACTION_MASK == _REACTION_HOSTILE
        hostile_dst = dst_flags is not None and dst_flags & _REACTION_MASK == _REACTION_HOSTILE
        fight: tuple[str, str, tuple[str, str, str], bool] | None = None
        if src_owner is not None and dst_owner is None and hostile_dst:
            who = self.player(dst, dst_name, dst_flags, src_owner)
            if who is not None:
                fight = (src_owner, dst, who, False)
        elif dst_owner is not None and src_owner is None and hostile_src:
            who = self.player(src, src_name, src_flags, dst_owner)
            if who is not None:
                fight = (dst_owner, src, who, dst.upper() == dst_owner)
        if fight is None:
            self.ally(src, src_name, src_flags, dst, now)
            return
        owner, foe, who, hit_you = fight
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
        self.fights += 1
        self.close_before(now)
        e = self.encounter(owner, foe, now)
        e.last = max(e.last, now)
        p = e.add(foe, who, "foe")
        if p is not None:
            p.hits += 1
        if hit_you:
            e.last_hit = (now, foe)

    def ally(
        self, src: object, src_name: object, src_flags: int | None, dst: object, now: int | None
    ) -> None:
        """A friendly player's damage on an opponent of an open encounter."""
        if now is None or not self.open or not isinstance(dst, str):
            return
        if src_flags is None or src_flags & _REACTION_MASK != _REACTION_FRIENDLY:
            return
        for e in self.open:
            held = e.players.get(dst)
            if held is None or held.side != "foe" or now - e.last > ENCOUNTER_GAP_SECONDS:
                continue
            who = self.player(src, src_name, src_flags, e.owner)
            if who is None or not isinstance(src, str):
                return
            p = e.add(src, who, "ally")
            if p is not None:
                p.hits += 1
            return

    def died(self, rec: Record, now: int | None) -> None:
        f = rec.fields
        if len(f) < 5 or now is None or not isinstance(f[4], str):
            return
        dead = f[4]
        up = dead.upper()
        if up in self.owners:
            for e in self.open:
                if e.owner != up or now - e.last > ENCOUNTER_GAP_SECONDS:
                    continue
                e.died = True
                if e.last_hit is not None and now - e.last_hit[0] <= DEATH_WINDOW_SECONDS:
                    killer = e.players.get(e.last_hit[1])
                    if killer is not None:
                        killer.killed_you = True
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
    `end` is its offset, so the next read takes it whole."""
    reader = _Reader(owners, state, frozenset(pvp_maps))
    lines = unparsed = 0
    end = start
    for entry in entries:
        if entry.ending == "":
            end = entry.offset  # still being written: the next read takes it
            break
        lines += 1
        end = entry.offset + _length(entry)
        if isinstance(entry, Skipped):
            continue
        if isinstance(entry, Unparsed):
            unparsed += 1
            continue
        reader.entry(entry)
    reader.end()
    return WorldScan(
        start=start,
        end=end,
        state=reader.state,
        encounters=reader.out,
        lines=lines,
        unparsed=unparsed,
        fights=reader.fights,
        outside_world=reader.outside,
        no_zone=reader.no_zone,
        no_time=reader.no_time,
        left_out=reader.left_out,
    )


_DAMAGE_BYTES = frozenset(e.encode("ascii") for e in DAMAGE_EVENTS)
_PLAYER = b"Player-"


def _skipper(owners: Iterable[str]) -> Callable[[bytes, bytes], bool]:
    """Whether a damage line can be left untokenized: every `Player-` in it
    starts one of your GUIDs, so it names no other player and can be no
    fight and no ally's damage (a fight always names its other player's
    GUID). Any other line, and any line this cannot be sure of, is read."""
    mine = frozenset(g.upper().encode("ascii", "replace") for g in owners)

    def skip(event: bytes, data: bytes) -> bool:
        if event not in _DAMAGE_BYTES:
            return False
        at = data.find(_PLAYER)
        while at != -1:
            end = data.find(b",", at)
            if data[at : len(data) if end == -1 else end].upper() not in mine:
                return False
            at = data.find(_PLAYER, at + len(_PLAYER))
        return True

    return skip


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
    that name another player (`_skipper`)."""
    return scan_entries(
        combatlog.read_log(path, events=WORLD_EVENTS, start=start, skip=_skipper(owners)),
        owners,
        start=start,
        state=state,
        pvp_maps=pvp_maps,
    )
