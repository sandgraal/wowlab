"""practice: one stretch of your own play, read from the combat log (M22-20).

For a training-dummy session or any fight: how often you cast each spell,
how much of the time your global cooldown was running, how often you used
each spell with a cooldown out of the times you could have, how long each
buff was on you, and the damage you and your companions did. It feeds the
keybind coach and the coach's combat-log route.

Pure and read-only (L1): the log is read through `combatlog.read_log`, which
opens it read-only, one chunk at a time; nothing is written anywhere.

**The field readers** carry four traps that each produced a wrong number in
Sparxx947/gse-makrowerkstatt (MIT), recorded in the headers of its
`werkzeug/puppe-messen.py`, `log-casts.py` and `log-sequenz.py` (commit
c60d296, read 2026-11-23; cited, not vendored). Each is graded on a real
Forever log slice (`test_practice_traps.py`):

1. *A swing has no spell fields.* `SWING_DAMAGE` writes no spell id, name or
   school, so its amount is three fields earlier than a spell's: after the
   event name, field 27 on a swing and 30 on `SPELL_DAMAGE`,
   `SPELL_PERIODIC_DAMAGE` and `RANGE_DAMAGE` when the advanced block is
   there (`damage_amount`). Reading 30 everywhere reads a swing's school.
2. *A companion is found two ways* (`Companions`): a `SPELL_SUMMON` whose
   source is the player names the unit summoned, which may never act and so
   never names its owner (a totem, a campfire); the advanced block's owner
   slot names a pet that was out before the log began, for which there is
   no `SPELL_SUMMON`. Either alone misses companions. GUIDs only, never
   names: other players summon units of the same name.
3. *The advanced block does not always describe the source.* It describes
   the target on `SPELL_DAMAGE`, `SPELL_PERIODIC_DAMAGE` and
   `SWING_DAMAGE_LANDED` (and on `RANGE_DAMAGE` too, by the source's
   account; no committed slice holds one **[verify]**), and the source on
   `SPELL_CAST_SUCCESS` and `SWING_DAMAGE`. So an owner is read only from a block whose unit field is
   the line's source (`owner_named`): read from a damage line, a creature
   hitting your pet would become your companion.
4. *Other players hit the same target.* Damage counts only when its source
   is the player or one of the player's companions; what others did to the
   same targets is counted apart (`others_on_targets`) and never added.

The advanced block is found by its shape, not by the header's
`ADVANCED_LOG_ENABLED`: the launch client writes `0` there and still writes
the block on every line that has one (two real logs of build 1.60.1.71037).

**Who you are.** A unit whose flags say affiliation "mine", controller
"player" and type "player", as `combatlog.cast_counts` reads them.

**A segment** is a run of your activity (your cast starts and casts, and
your or your companions' damage) with no pause longer than `gap` seconds
(`DEFAULT_GAP_SECONDS`, the source's 10 s), ended early by a new
`COMBAT_LOG_VERSION` line (logging was switched off and on) or by another of
your characters acting. Each keeps the line numbers it spans, so the summary
reads exactly those lines.

**The global cooldown.** A cast with a cast time keeps you busy from the
start of the cast to its success; every cast also starts the global
cooldown, which is the spell's `StartRecoveryTime` in the build's
`SpellCooldowns` table when that table is cached, and is assumed to be 1.5 s
for every cast when it is not. A spell with no row in the table is counted
as not on the global cooldown (`no_gcd_data`): auto-repeat shots, food and
bandages have none. Haste and talents that shorten it are not read (not yet
confirmed whether Forever applies any). A channelled spell counts only its
global cooldown: the log writes no channel's end.

**Cooldown use.** For a spell whose cooldown (the larger of `RecoveryTime`
and `CategoryRecoveryTime`) is longer than its global cooldown, the casts
in the segment out of the most the segment allowed (its length over the
cooldown, plus one for the first cast). Talents that shorten a cooldown are
not read. Spells you cast elsewhere in the log and not in this segment are
listed with no casts.

**Buff uptime.** The share of the segment each buff on you was up, from its
applied, refreshed, dose and removed lines. A buff removed with no earlier
applied line in the log was on you when logging began
(`on_before_log`); a buff still on at the segment's end counts to its end.

**Times** are read from the timestamp to the millisecond, with its UTC
offset; a line with no year in its timestamp is not read (`no_time`).

**Bounds.** At most `MAX_SEGMENTS` segments are listed (`segments_left_out`
counts the rest), `MAX_COMPANIONS` companions are remembered, and
`MAX_SPELLS` spells and buffs are kept in a summary; memory does not grow
with the log beyond these.
"""

from __future__ import annotations

import calendar
import itertools
import re
import statistics
from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import combatlog
from wowlab_core.combatlog import Quoted, Record, Skipped, Unparsed

__all__ = [
    "ADVANCED_FIELDS",
    "ASSUMED_GCD_MS",
    "DAMAGE_EVENTS",
    "DEFAULT_GAP_SECONDS",
    "MAX_COMPANIONS",
    "MAX_SEGMENTS",
    "MAX_SPELLS",
    "PAUSE_SECONDS",
    "PRACTICE_EVENTS",
    "TIMING_TABLE",
    "BuffUptime",
    "Companions",
    "CooldownUse",
    "DamageDone",
    "DamageSource",
    "GcdUse",
    "PracticeSegment",
    "PracticeSummary",
    "SegmentScan",
    "SpellTiming",
    "SpellUse",
    "advanced_at",
    "damage_amount",
    "damage_done",
    "find_segments",
    "moment",
    "owner_named",
    "summarize",
    "timings_from_rows",
]

DEFAULT_GAP_SECONDS = 10.0
"""A pause in your activity longer than this ends a segment (the source's
`LUECKE`)."""
PAUSE_SECONDS = 3.0
"""Time off the global cooldown and not casting, longer than this, is a
pause (the source's "Pausen über 3 s")."""
ASSUMED_GCD_MS = 1500
"""The global cooldown assumed for every cast when the build's cooldown
table is not cached."""
TIMING_TABLE = "SpellCooldowns"
"""The build-keyed table the global cooldown and cooldowns are read from."""
MAX_SEGMENTS = 2_000
MAX_COMPANIONS = 10_000
MAX_SPELLS = 500
MAX_TARGET_NAMES = 5
ADVANCED_FIELDS = 19
"""The advanced block's width on every real line that carries it."""

# After the event name: source GUID, name, flags, raid flags; target GUID,
# name, flags, raid flags; then the prefix (spell id, name, school on a
# SPELL_ or RANGE_ line; nothing on a swing); then the advanced block; then
# the suffix, whose first field on a damage line is the amount.
_SOURCE, _SOURCE_NAME, _SOURCE_FLAGS = 0, 1, 2
_TARGET, _TARGET_NAME = 4, 5
_BASE = 8
_SPELL_ID, _SPELL_NAME = 8, 9
_AURA_TYPE = 11
_PREFIX: dict[str, int] = {"SWING": 0, "SPELL": 3, "RANGE": 3, "ENVIRONMENTAL": 1}

DAMAGE_EVENTS = frozenset({"SWING_DAMAGE", "RANGE_DAMAGE", "SPELL_DAMAGE", "SPELL_PERIODIC_DAMAGE"})
"""The damage lines totalled. `SWING_DAMAGE_LANDED` repeats a swing's amount
(LAB_FORMATS §8), so counting it too would count melee twice."""
_CAST_SUCCESS = "SPELL_CAST_SUCCESS"
_CAST_START = "SPELL_CAST_START"
_CAST_FAILED = "SPELL_CAST_FAILED"
_SUMMON = "SPELL_SUMMON"
_AURA_ON = frozenset({"SPELL_AURA_APPLIED", "SPELL_AURA_REFRESH", "SPELL_AURA_APPLIED_DOSE"})
_AURA_OFF = "SPELL_AURA_REMOVED"
_HEADER = "COMBAT_LOG_VERSION"
PRACTICE_EVENTS = frozenset(
    {
        _HEADER,
        _CAST_SUCCESS,
        _CAST_START,
        _CAST_FAILED,
        _SUMMON,
        _AURA_OFF,
        *_AURA_ON,
        *DAMAGE_EVENTS,
    }
)
"""The events read (`events=` on `combatlog.read_log`): every other line is
not tokenized."""

_ZERO_GUID = "0000000000000000"
_GUID = re.compile(r"[A-Za-z]+(?:-[0-9A-Fa-f]+)+|0{16}", re.ASCII)
_INT = re.compile(r"-?[0-9]{1,12}", re.ASCII)
_SPELL = re.compile(r"[0-9]{1,10}", re.ASCII)
_HEX = re.compile(r"0x[0-9A-Fa-f]{1,8}", re.ASCII)
_TIME = re.compile(
    r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{4}) ([0-9]{1,2}):([0-9]{2}):([0-9]{2})(\.[0-9]{1,6})?"
    r"(?:([+-])([0-9]{1,2})(?::?([0-9]{2}))?)?",
    re.ASCII,
)
_LONGEST_CAST = 10.0  # a start more than this before its success is not its cast


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ─── field readers (the four traps) ──────────────────────────────────────────


def moment(timestamp: str) -> float | None:
    """A log timestamp as seconds since the epoch (UTC), to the millisecond;
    None with no year or a time that cannot be. With no UTC offset the time is
    read as UTC: only differences within one log are used."""
    m = _TIME.fullmatch(timestamp)
    if m is None:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour <= 23 and minute <= 59 and second <= 60):
        return None
    try:
        whole = calendar.timegm((year, month, day, hour, minute, second, 0, 0, -1))
    except (OverflowError, ValueError):
        return None
    fraction = float(m.group(7)) if m.group(7) else 0.0
    shift = 0
    if m.group(8) is not None:
        shift = int(m.group(9)) * 3600 + int(m.group(10) or 0) * 60
        shift = -shift if m.group(8) == "+" else shift
    return whole + fraction + shift


def _prefix(event: str) -> int | None:
    head = event.split("_", 1)[0]
    return _PREFIX.get(head)


def advanced_at(rec: Record) -> int | None:
    """Where the record's advanced block starts in `fields`, or None when the
    record has none. Found by shape: two GUID-shaped fields (the unit and its
    owner) where the block would start, and room for the whole block."""
    prefix = _prefix(rec.event)
    if prefix is None:
        return None
    at = _BASE + prefix
    f = rec.fields
    if len(f) < at + ADVANCED_FIELDS:
        return None
    unit, owner = f[at], f[at + 1]
    if not isinstance(unit, str) or not isinstance(owner, str):
        return None
    if not _GUID.fullmatch(unit) or not _GUID.fullmatch(owner):
        return None
    return at


def damage_amount(rec: Record) -> int | None:
    """The amount of a damage line (`DAMAGE_EVENTS`): the first field after
    the advanced block, or after the prefix when the line has no block. A
    swing has no spell prefix, so its amount is three fields earlier than a
    spell's (trap 1). None for any other line or an amount that is not a
    whole number."""
    if rec.event not in DAMAGE_EVENTS:
        return None
    prefix = _prefix(rec.event)
    if prefix is None:  # pragma: no cover - every damage event has a prefix
        return None
    adv = advanced_at(rec)
    at = adv + ADVANCED_FIELDS if adv is not None else _BASE + prefix
    if len(rec.fields) <= at:
        return None
    value = rec.fields[at]
    if not isinstance(value, str) or not _INT.fullmatch(value):
        return None
    return int(value)


def owner_named(rec: Record) -> tuple[str, str] | None:
    """(unit, owner) when the record's advanced block describes its source
    and names an owner: the source is the owner's pet or guardian. A block
    describing the target (every spell-damage line) is never read for an
    owner (trap 3)."""
    adv = advanced_at(rec)
    if adv is None:
        return None
    f = rec.fields
    source, unit, owner = f[_SOURCE], f[adv], f[adv + 1]
    if not isinstance(source, str) or unit != source or not isinstance(owner, str):
        return None
    if owner == _ZERO_GUID or owner.upper() == source.upper():
        return None
    return source, owner


class Companions:
    """The units that belong to a player, by GUID (trap 2): those a
    `SPELL_SUMMON` of theirs names, and those whose own advanced block names
    them as owner. Feed it every record, in order (`see`); at most
    `MAX_COMPANIONS` are remembered (`left_out` counts the rest)."""

    def __init__(self) -> None:
        self._owner: dict[str, str] = {}
        self.by_summon: dict[str, str] = {}  # unit -> owner, as SPELL_SUMMON named it
        self.by_owner_slot: dict[str, str] = {}  # unit -> owner, as an advanced block named it
        self.left_out = 0

    def _keep(self, table: dict[str, str], unit: str, owner: str) -> None:
        u, o = unit.upper(), owner.upper()
        if u not in self._owner and len(self._owner) >= MAX_COMPANIONS:
            self.left_out += 1
            return
        self._owner[u] = o
        table.setdefault(u, o)

    def see(self, rec: Record) -> None:
        f = rec.fields
        if rec.event == _SUMMON and len(f) > _TARGET:
            owner, unit = f[_SOURCE], f[_TARGET]
            if isinstance(owner, str) and isinstance(unit, str) and unit != _ZERO_GUID:
                self._keep(self.by_summon, unit, owner)
            return
        named = owner_named(rec)
        if named is not None:
            self._keep(self.by_owner_slot, *named)

    def owner_of(self, guid: str) -> str | None:
        """The owner GUID (upper case) of a companion, or None."""
        return self._owner.get(guid.upper())

    def of(self, owner: str) -> set[str]:
        """Every unit known to belong to `owner` (upper case GUIDs)."""
        up = owner.upper()
        return {u for u, o in self._owner.items() if o == up}


class DamageSource(_Model):
    """One unit's damage in a total: you, or a companion of yours."""

    guid: str
    name: str | None
    you: bool
    amount: int
    share: float  # of the total, 0 to 1


class DamageDone(_Model):
    """The damage one player and their companions did (`damage_done`, and a
    summary's `damage`): the total, per source, and what other units did to
    the same targets, never added (trap 4)."""

    total: int = 0
    lines: int = 0
    by_source: tuple[DamageSource, ...] = ()
    targets: int = 0  # distinct units hit
    others_on_targets: int = 0  # other units' damage on those targets, left out
    others_lines: int = 0


class _Tally:
    """Damage by one player and their companions, while lines are fed."""

    def __init__(self, guid: str, companions: Companions) -> None:
        self.guid = guid.upper()
        self.companions = companions
        self.total = self.lines = 0
        self.by: dict[str, int] = {}
        self.names: dict[str, tuple[str, str | None]] = {}  # as written, unit name
        self.targets: set[str] = set()
        self.others: dict[str, int] = {}  # target -> other units' damage on it
        self.others_lines: dict[str, int] = {}

    def mine(self, guid: str) -> bool:
        up = guid.upper()
        return up == self.guid or self.companions.owner_of(up) == self.guid

    def feed(self, rec: Record) -> bool:
        """Count one damage line; True when it was the player's or a
        companion's."""
        amount = damage_amount(rec)
        f = rec.fields
        if amount is None or len(f) <= _TARGET:
            return False
        source, target = f[_SOURCE], f[_TARGET]
        if not isinstance(source, str) or not isinstance(target, str):
            return False
        tgt = target.upper()
        if not self.mine(source):
            if tgt in self.others or len(self.others) < MAX_COMPANIONS:
                self.others[tgt] = self.others.get(tgt, 0) + amount
                self.others_lines[tgt] = self.others_lines.get(tgt, 0) + 1
            return False
        if self.mine(target):  # your pet's hit on you, or yours on it: not damage done
            return False
        up = source.upper()
        self.total += amount
        self.lines += 1
        self.by[up] = self.by.get(up, 0) + amount
        if up not in self.names:
            name = f[_SOURCE_NAME]
            self.names[up] = (source, name.text if isinstance(name, Quoted) else None)
        if len(self.targets) < MAX_COMPANIONS:
            self.targets.add(tgt)
        return True

    def done(self) -> DamageDone:
        total = self.total
        sources = sorted(self.by.items(), key=lambda kv: (-kv[1], kv[0]))
        return DamageDone(
            total=total,
            lines=self.lines,
            by_source=tuple(
                DamageSource(
                    guid=self.names[g][0],
                    name=self.names[g][1],
                    you=g == self.guid,
                    amount=a,
                    share=round(a / total, 4) if total else 0.0,
                )
                for g, a in sources[:MAX_SPELLS]
            ),
            targets=len(self.targets),
            others_on_targets=sum(self.others.get(t, 0) for t in self.targets),
            others_lines=sum(self.others_lines.get(t, 0) for t in self.targets),
        )


def damage_done(entries: Iterable[combatlog.Entry | Skipped], guid: str) -> DamageDone:
    """The damage `guid` and its companions did over `entries` (a log's
    lines in order): the four traps' reader in one call. Companions are
    found over all of `entries` first, so a pet's damage written before the
    line that names its owner counts too (the summary, which reads one line
    at a time, counts a companion from that line on). Holds the records in
    memory: for a slice, not a whole raid night."""
    companions = Companions()
    records = [e for e in entries if isinstance(e, Record)]
    for rec in records:
        companions.see(rec)
    tally = _Tally(guid, companions)
    for rec in records:
        if rec.event in DAMAGE_EVENTS:
            tally.feed(rec)
    return tally.done()


# ─── segments ────────────────────────────────────────────────────────────────


def _flags(value: object) -> int | None:
    if isinstance(value, str) and _HEX.fullmatch(value):
        return int(value, 16)
    return None


def _is_player(flags: int | None) -> bool:
    return (
        flags is not None
        and flags & combatlog.AFFILIATION_MASK == combatlog.AFFILIATION_MINE
        and flags & combatlog.CONTROL_MASK == combatlog.CONTROL_PLAYER
        and flags & combatlog.TYPE_MASK == combatlog.TYPE_PLAYER
    )


class PracticeSegment(_Model):
    """One run of your activity: its number in the log (from 1), whose it
    is (the GUID and the unit name as written), its first and last active
    lines (1-based line numbers) and times (as written), its length in
    seconds, your casts, your and your companions' damage, and the names of
    up to `MAX_TARGET_NAMES` units it hit, in order of first hit."""

    index: int
    guid: str
    name: str | None
    first_line: int
    last_line: int
    start: str
    end: str
    seconds: float
    casts: int
    damage: int
    targets: tuple[str, ...] = ()


class SegmentScan(_Model):
    """The segments of a log (`find_segments`), oldest first, and counts:
    lines read, lines that would not tokenize, lines whose time could not be
    read, segments past `MAX_SEGMENTS`, and companions past
    `MAX_COMPANIONS`."""

    segments: tuple[PracticeSegment, ...] = ()
    lines: int = 0
    unparsed: int = 0
    no_time: int = 0
    segments_left_out: int = 0
    companions_left_out: int = 0
    gap: float = DEFAULT_GAP_SECONDS


class _Open:
    def __init__(self, guid: str, name: str | None, line: int, ts: str, t: float) -> None:
        self.guid = guid
        self.name = name
        self.first_line = self.last_line = line
        self.start = self.end = ts
        self.t0 = self.t1 = t
        self.casts = 0
        self.damage = 0
        self.targets: dict[str, None] = {}


def _source_name(rec: Record) -> str | None:
    name = rec.fields[_SOURCE_NAME] if len(rec.fields) > _SOURCE_NAME else None
    return name.text if isinstance(name, Quoted) else None


def find_segments(
    entries: Iterable[combatlog.Entry | Skipped],
    gap: float = DEFAULT_GAP_SECONDS,
    guids: Iterable[str] | None = None,
) -> SegmentScan:
    """The segments of your activity in a log (`entries`: its lines in
    order, as `combatlog.read_log` yields them, with or without
    `events=PRACTICE_EVENTS`). Reads one line at a time. `guids`: read the
    activity of these units instead of the player's own (found by the
    flags); a test reads another player's in a real log this way."""
    companions = Companions()
    fixed = guids is not None
    # upper GUID -> as written, for the players whose activity counts
    players: dict[str, str] = {g.upper(): g for g in guids or ()}
    done: list[PracticeSegment] = []
    left_out = lines = unparsed = no_time = 0
    cur: _Open | None = None

    def close() -> None:
        nonlocal cur, left_out
        if cur is None:
            return
        if len(done) >= MAX_SEGMENTS:
            left_out += 1
        else:
            done.append(
                PracticeSegment(
                    index=len(done) + 1,
                    guid=cur.guid,
                    name=cur.name,
                    first_line=cur.first_line,
                    last_line=cur.last_line,
                    start=cur.start,
                    end=cur.end,
                    seconds=round(cur.t1 - cur.t0, 3),
                    casts=cur.casts,
                    damage=cur.damage,
                    targets=tuple(cur.targets),
                )
            )
        cur = None

    for line, entry in enumerate(entries, 1):
        lines += 1
        if isinstance(entry, Skipped):
            continue
        if isinstance(entry, Unparsed):
            unparsed += 1
            continue
        rec = entry
        if rec.event == _HEADER:
            close()
            continue
        companions.see(rec)
        f = rec.fields
        if len(f) <= _TARGET_NAME:
            continue
        source = f[_SOURCE]
        if not isinstance(source, str):
            continue
        if not fixed and _is_player(_flags(f[_SOURCE_FLAGS])):
            players.setdefault(source.upper(), source)
        who: str | None = None
        cast = rec.event == _CAST_SUCCESS
        amount: int | None = None
        if (cast or rec.event == _CAST_START) and source.upper() in players:
            who = source.upper()
        elif rec.event in DAMAGE_EVENTS:
            amount = damage_amount(rec)
            owner = source.upper() if source.upper() in players else companions.owner_of(source)
            target = f[_TARGET]
            hits_own = isinstance(target, str) and (
                target.upper() == owner or companions.owner_of(target) == owner
            )
            if amount is not None and owner in players and not hits_own:
                who = owner
        if who is None:
            continue
        t = moment(rec.timestamp)
        if t is None:
            no_time += 1
            continue
        if cur is not None and (cur.guid.upper() != who or t - cur.t1 > gap or t < cur.t1):
            close()
        if cur is None:
            name = _source_name(rec) if source.upper() == who else None
            cur = _Open(players[who], name, line, rec.timestamp, t)
        if cur.name is None and source.upper() == who:
            cur.name = _source_name(rec)
        cur.last_line, cur.end, cur.t1 = line, rec.timestamp, t
        if cast:
            cur.casts += 1
        elif amount is not None:
            cur.damage += amount
            target_name = f[_TARGET_NAME]
            if isinstance(target_name, Quoted) and len(cur.targets) < MAX_TARGET_NAMES:
                cur.targets.setdefault(target_name.text, None)
    close()
    return SegmentScan(
        segments=tuple(done),
        lines=lines,
        unparsed=unparsed,
        no_time=no_time,
        segments_left_out=left_out,
        companions_left_out=companions.left_out,
        gap=gap,
    )


# ─── the summary ─────────────────────────────────────────────────────────────


class SpellTiming(_Model):
    """A spell's global cooldown and cooldown, in milliseconds, from the
    build's `SpellCooldowns` row."""

    gcd_ms: int
    cooldown_ms: int


def timings_from_rows(rows: Iterable[Mapping[str, str]]) -> dict[int, SpellTiming]:
    """Spell id -> timing, from `SpellCooldowns` rows. A spell with several
    rows takes the one for difficulty 0, else its first. A row whose numbers
    do not read is skipped."""
    out: dict[int, SpellTiming] = {}
    base: set[int] = set()
    for row in rows:
        try:
            spell = int(row.get("SpellID") or "")
            gcd = int(row.get("StartRecoveryTime") or "0")
            cd = max(
                int(row.get("RecoveryTime") or "0"), int(row.get("CategoryRecoveryTime") or "0")
            )
            difficulty = int(row.get("DifficultyID") or "0")
        except ValueError:
            continue
        if spell <= 0 or gcd < 0 or cd < 0:
            continue
        if spell in base or (spell in out and difficulty != 0):
            continue
        out[spell] = SpellTiming(gcd_ms=gcd, cooldown_ms=cd)
        if difficulty == 0:
            base.add(spell)
    return out


class SpellUse(_Model):
    """Your casts of one spell in the segment: how many, per minute, the
    median time between two of them (None under two casts), its global
    cooldown in milliseconds (None when not known), and, for a spell with a
    cooldown longer than its global cooldown, the cooldown and its use."""

    spell_id: int
    spell_name: str
    casts: int
    per_minute: float
    median_gap: float | None = None
    gcd_ms: int | None = None
    cooldown_ms: int | None = None


class CooldownUse(_Model):
    """One spell with a cooldown: casts out of the most the segment allowed
    (its length over the cooldown, plus one)."""

    spell_id: int
    spell_name: str
    cooldown_ms: int
    casts: int
    possible: int
    share: float  # casts / possible, at most 1


class GcdUse(_Model):
    """How much of the segment you were casting or on the global cooldown
    (`busy_seconds`, `share`), the casts that started it, those with no
    global cooldown (`off_gcd`), those whose spell has no row in the table
    (`no_gcd_data`), the pauses longer than `PAUSE_SECONDS` and the longest,
    and whether every cast was assumed to start a 1.5 s global cooldown
    (`assumed`: the build's table is not cached)."""

    busy_seconds: float
    share: float
    on_gcd: int
    off_gcd: int
    no_gcd_data: int
    pauses: int
    longest_pause: float
    assumed: bool


class BuffUptime(_Model):
    """One buff on you: the seconds it was up in the segment and the share
    of the segment, and whether it was on you before the log began."""

    spell_id: int
    spell_name: str
    seconds: float
    share: float
    on_before_log: bool = False


class PracticeSummary(_Model):
    """One segment, summarized (`summarize`)."""

    segment: PracticeSegment
    seconds: float  # the length the rates use: the segment's, at least 1 s
    casts: int
    spells: tuple[SpellUse, ...]
    gcd: GcdUse
    cooldowns: tuple[CooldownUse, ...]
    buffs: tuple[BuffUptime, ...]
    damage: DamageDone
    dps: float
    timings: Literal["table", "assumed"]
    spells_left_out: int = 0
    no_time: int = 0


def _union(intervals: list[tuple[float, float]], lo: float, hi: float) -> list[tuple[float, float]]:
    """The intervals clipped to [lo, hi] and merged, in order."""
    out: list[tuple[float, float]] = []
    for a, b in sorted((max(a, lo), min(b, hi)) for a, b in intervals):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


class _Aura:
    def __init__(self, name: str) -> None:
        self.name = name
        self.since: float | None = None
        self.up = 0.0
        self.before = False


class _Casts:
    """Your casts while lines are fed: per spell, the global cooldown and the
    time each cast kept you busy."""

    def __init__(self, timings: Mapping[int, SpellTiming] | None) -> None:
        self.timings = timings
        self.times: dict[tuple[int, str], list[float]] = {}
        self.elsewhere: dict[tuple[int, str], None] = {}
        self.busy: list[tuple[float, float]] = []
        self.on_gcd = self.off_gcd = self.no_data = self.left_out = 0
        self.started: dict[int, float] = {}

    def gcd(self, spell: int) -> float:
        if self.timings is None:
            self.on_gcd += 1
            return ASSUMED_GCD_MS / 1000
        timing = self.timings.get(spell)
        if timing is None:
            self.no_data += 1
            return 0.0
        if timing.gcd_ms > 0:
            self.on_gcd += 1
        else:
            self.off_gcd += 1
        return timing.gcd_ms / 1000

    def success(self, key: tuple[int, str], t: float) -> None:
        began = self.started.pop(key[0], None)
        if key not in self.times and len(self.times) >= MAX_SPELLS:
            self.left_out += 1
            return
        self.times.setdefault(key, []).append(t)
        cast_time = t - began if began is not None and 0 < t - began <= _LONGEST_CAST else 0.0
        start = t - cast_time
        end = max(t, start + self.gcd(key[0]))
        if end > start:
            self.busy.append((start, end))

    def seen_elsewhere(self, key: tuple[int, str]) -> None:
        if key not in self.times and len(self.elsewhere) < MAX_SPELLS:
            self.elsewhere.setdefault(key, None)


class _Buffs:
    """The buffs on you while lines are fed, and how long each was up
    between `t0` and `t1`."""

    def __init__(self, t0: float, t1: float) -> None:
        self.t0, self.t1 = t0, t1
        self.auras: dict[int, _Aura] = {}
        self.log_start: float | None = None
        self.left_out = 0

    def restart(self) -> None:
        """Logging was switched off and on: what was on before is not known."""
        for aura in self.auras.values():
            aura.since = None
        self.log_start = None

    def add(self, aura: _Aura, a: float, b: float) -> None:
        lo, hi = max(a, self.t0), min(b, self.t1)
        if hi > lo:
            aura.up += hi - lo

    def feed(self, spell: int, name: str, off: bool, t: float) -> None:
        aura = self.auras.get(spell)
        if aura is None:
            if len(self.auras) >= MAX_SPELLS:
                self.left_out += 1
                return
            aura = self.auras[spell] = _Aura(name)
        if not off:
            if aura.since is None:
                aura.since = t
            return
        if aura.since is not None:
            self.add(aura, aura.since, t)
        elif self.log_start is not None:
            aura.before = True
            self.add(aura, self.log_start, t)
        aura.since = None

    def done(self) -> tuple[BuffUptime, ...]:
        length = self.t1 - self.t0
        for aura in self.auras.values():
            if aura.since is not None:
                self.add(aura, aura.since, self.t1)
                aura.since = None
        return tuple(
            sorted(
                (
                    BuffUptime(
                        spell_id=sid,
                        spell_name=a.name,
                        seconds=round(a.up, 3),
                        share=round(min(1.0, a.up / length), 4) if length > 0 else 0.0,
                        on_before_log=a.before,
                    )
                    for sid, a in self.auras.items()
                    if a.up > 0
                ),
                key=lambda b: (-b.seconds, b.spell_id),
            )
        )


def _spell(rec: Record) -> tuple[int, str] | None:
    f = rec.fields
    if len(f) <= _SPELL_NAME:
        return None
    sid, name = f[_SPELL_ID], f[_SPELL_NAME]
    if not isinstance(sid, str) or not _SPELL.fullmatch(sid) or not isinstance(name, Quoted):
        return None
    return int(sid), name.text


def _gcd_use(casts: _Casts, t0: float, t1: float, rate: float) -> GcdUse:
    merged = _union(casts.busy, t0, t1)
    busy = sum(b - a for a, b in merged)
    edges = [t0, *(x for ab in merged for x in ab), t1]
    idle = [edges[i + 1] - edges[i] for i in range(0, len(edges) - 1, 2)]
    return GcdUse(
        busy_seconds=round(busy, 3),
        share=round(min(1.0, busy / rate), 4),
        on_gcd=casts.on_gcd,
        off_gcd=casts.off_gcd,
        no_gcd_data=casts.no_data,
        pauses=sum(1 for g in idle if g > PAUSE_SECONDS),
        longest_pause=round(max(idle, default=0.0), 3),
        assumed=casts.timings is None,
    )


def _spell_uses(
    casts: _Casts, length: float, rate: float
) -> tuple[list[SpellUse], list[CooldownUse]]:
    timings = casts.timings
    spells: list[SpellUse] = []
    cooldowns: list[CooldownUse] = []
    for sid, name in [*casts.times, *(k for k in casts.elsewhere if k not in casts.times)]:
        times = casts.times.get((sid, name), [])
        timing = timings.get(sid) if timings is not None else None
        cooldown = (
            timing.cooldown_ms
            if timing is not None and timing.cooldown_ms > timing.gcd_ms
            else None
        )
        if times:
            gaps = [b - a for a, b in itertools.pairwise(times)]
            gcd_ms = timing.gcd_ms if timing is not None else None
            spells.append(
                SpellUse(
                    spell_id=sid,
                    spell_name=name,
                    casts=len(times),
                    per_minute=round(len(times) * 60 / rate, 2),
                    median_gap=round(statistics.median(gaps), 3) if gaps else None,
                    gcd_ms=ASSUMED_GCD_MS if timings is None else gcd_ms,
                    cooldown_ms=cooldown,
                )
            )
        if cooldown is not None:
            possible = int(length * 1000 // cooldown) + 1
            cooldowns.append(
                CooldownUse(
                    spell_id=sid,
                    spell_name=name,
                    cooldown_ms=cooldown,
                    casts=len(times),
                    possible=possible,
                    share=round(min(1.0, len(times) / possible), 4),
                )
            )
    spells.sort(key=lambda s: (-s.casts, s.spell_id, s.spell_name))
    cooldowns.sort(key=lambda c: (c.share, c.spell_id))
    return spells, cooldowns


def summarize(
    entries: Iterable[combatlog.Entry | Skipped],
    segment: PracticeSegment,
    timings: Mapping[int, SpellTiming] | None = None,
) -> PracticeSummary:
    """The summary of `segment` (from `find_segments` on the same log),
    reading `entries` (the whole log again, in order, with or without
    `events=PRACTICE_EVENTS`) one line at a time. `timings`: spell id ->
    timing from the build's `SpellCooldowns` (`timings_from_rows`), or None
    when it is not cached: every cast then starts a 1.5 s global cooldown
    and no cooldown use is given."""
    guid = segment.guid.upper()
    t0 = moment(segment.start)
    t1 = moment(segment.end)
    if t0 is None or t1 is None or t1 < t0:
        raise ValueError("the segment's start and end are not times of one log")
    companions = Companions()
    tally = _Tally(guid, companions)
    casts = _Casts(timings)
    buffs = _Buffs(t0, t1)
    no_time = 0
    for line, entry in enumerate(entries, 1):
        if not isinstance(entry, Record):
            continue
        rec = entry
        inside = segment.first_line <= line <= segment.last_line
        after = line > segment.last_line
        if rec.event == _HEADER:
            if not after:
                buffs.restart()
                casts.started.clear()
            continue
        f = rec.fields
        if len(f) <= _TARGET_NAME:
            continue
        source, target = f[_SOURCE], f[_TARGET]
        mine = isinstance(source, str) and source.upper() == guid
        if after:
            spell = _spell(rec) if mine and rec.event == _CAST_SUCCESS else None
            if spell is not None:
                casts.seen_elsewhere(spell)
            continue
        companions.see(rec)
        t = moment(rec.timestamp)
        if t is None:
            no_time += 1 if inside else 0
            continue
        if buffs.log_start is None:
            buffs.log_start = t
        if rec.event in DAMAGE_EVENTS:
            if inside:
                tally.feed(rec)
            continue
        spell = _spell(rec)
        if spell is None:
            continue
        if rec.event in _AURA_ON or rec.event == _AURA_OFF:
            kind = f[_AURA_TYPE] if len(f) > _AURA_TYPE else None
            if isinstance(target, str) and target.upper() == guid and kind == "BUFF":
                buffs.feed(spell[0], spell[1], rec.event == _AURA_OFF, t)
        elif not mine:
            continue
        elif rec.event == _CAST_START:
            casts.started[spell[0]] = t
        elif rec.event == _CAST_FAILED:
            casts.started.pop(spell[0], None)
        elif rec.event == _CAST_SUCCESS:
            if inside:
                casts.success(spell, t)
            else:
                casts.started.pop(spell[0], None)
                casts.seen_elsewhere(spell)
    length = t1 - t0
    rate = max(length, 1.0)
    spells, cooldowns = _spell_uses(casts, length, rate)
    damage = tally.done()
    return PracticeSummary(
        segment=segment,
        seconds=round(rate, 3),
        casts=sum(len(v) for v in casts.times.values()),
        spells=tuple(spells),
        gcd=_gcd_use(casts, t0, t1, rate),
        cooldowns=tuple(cooldowns),
        buffs=buffs.done(),
        damage=damage,
        dps=round(damage.total / rate, 1),
        timings="assumed" if timings is None else "table",
        spells_left_out=casts.left_out + buffs.left_out,
        no_time=no_time,
    )
