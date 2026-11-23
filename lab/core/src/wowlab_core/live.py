"""live: what the Live view of `wowlab ui` watches and recaps (M22-05).

Reads only (L1). Nothing here writes a file, a cache or a lock, in the
install or anywhere else; the watcher keeps what it learned in memory.

**Watching.** `Watcher.poll` answers, cheaply, whether anything the Live
view shows can have changed:

- the account's SavedVariables: the newest modification time of a `.lua`
  file in an account's `SavedVariables/` or a character's (the game writes
  them at `/reload` and logout, never while you play);
- the newest combat log in the flavor's `Logs/` folder (the game writes it
  as you play, in chunks), its size, and the lines that change where the log
  is: `ZONE_CHANGE` and the arena's start and end lines. Only the bytes
  added since the last poll are read, at most `WATCH_TAIL_BYTES`; a log that
  grew by more than that is read from that far before its end, and a new or
  rewritten log the same way. Every other line is passed over by its event
  name, never tokenized.

The page polls the server, never the game files, and the server polls these
files only when the page asks (`/api/live`).

**The match recap** (`match_recap`): the latest stay in a place that is not
the open world (the rule `worldpvp.in_open_world` keeps) in the newest
combat logs, taken as a match when it shows an arena line, its instance id
is a map a match was kept on, or a player fought you there. Who you fought
is read by `worldpvp`'s own fight rule, counted inside that place
(`place=`); your killing blows are the `PARTY_KILL` lines by you or a unit
the log marks as yours whose target is a player, your deaths the
`UNIT_DIED` lines of your GUID. The match is over when the log left the
place (a later zone line, or a newer log), or showed the arena's end line.
The result comes from the arena's end line (the winning team against your
team on its start line, not yet confirmed), else from the crew book's match
on the same map near the same time, which the lab-addon saves only at
`/reload` or logout. That a battleground writes a zone line whose difficulty
is not 0, and which lines end a battleground, are not yet confirmed.

**The session recap** (`session_recap`): the character's lab-addon record
against the save before it (`charhistory`: gear, currencies, level), the
items Syndicator lists for the character against the copy in the snapshot
that holds that earlier save (not yet confirmed: Syndicator's layout is read
from one capture), the crew book's matches since the earlier save, and the
players who fought you in the open world since then, from the combat logs
written since (`worldpvp`).
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import threading
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wowlab_core import charhistory, combatlog, crewbook, itemlink, luadata, worldpvp
from wowlab_core.combatlog import Quoted, Record, Skipped, Unparsed

__all__ = [
    "POLL_SECONDS",
    "RECAP_LOGS",
    "RECAP_MAX_BYTES",
    "SESSION_LOG_BYTES",
    "WATCH_MAX_FILES",
    "WATCH_TAIL_BYTES",
    "ItemCount",
    "LiveWatch",
    "LogWatch",
    "MatchOpponent",
    "MatchRecap",
    "NewItem",
    "SavedWatch",
    "SessionMatch",
    "SessionOpponent",
    "SessionRecap",
    "Watcher",
    "earlier_row",
    "logs_folder",
    "match_recap",
    "new_items",
    "saved_watch",
    "session_recap",
    "syndicator_items",
    "world_since",
]

POLL_SECONDS = 3
"""How often the Live view asks the server (`/api/live`), in seconds."""
WATCH_TAIL_BYTES = 4 * 1024 * 1024
"""The most bytes of the newest log one poll reads."""
WATCH_MAX_FILES = 4000
"""The most SavedVariables files one poll looks at."""
RECAP_LOGS = 3
"""The newest combat logs a match recap looks in."""
RECAP_MAX_BYTES = 256 * 1024 * 1024
"""The most bytes of combat log a match recap reads; older logs past it are
not read."""
SESSION_LOG_BYTES = 256 * 1024 * 1024
"""The most bytes of combat log a session recap reads for the players who
fought you in the open world."""
_MAX_STAYS = 8  # places not the open world a match recap looks at, newest first
_RESULT_BEFORE = 120  # seconds before a stay's start a book match may be from
_RESULT_AFTER = 900  # and after its end (the addon reads the scoreboard at the end)
_ZONE_EVENTS = frozenset({"ZONE_CHANGE", "ARENA_MATCH_START", "ARENA_MATCH_END"})
_MATCH_EVENTS = worldpvp.WORLD_EVENTS
_SV_SUFFIX = ".lua"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ─── watching ────────────────────────────────────────────────────────────────


class SavedWatch(_Model):
    """The SavedVariables the game writes at `/reload` and logout: how many
    `.lua` files were looked at, the newest one (relative to the flavor
    folder) and its modification time, and how many were not looked at
    (past `WATCH_MAX_FILES`)."""

    files: int = 0
    newest: str | None = None
    newest_ns: int | None = None
    left_out: int = 0


class LogWatch(_Model):
    """The newest combat log: its name, size and modification time; where
    it is as of the last line read (the latest zone line's name, instance
    id and difficulty, None before any), whether an arena match is under
    way, and how many zone lines and arena end lines were read since the
    watch began on this log (`zone_changes`, `match_ends`; a log read from
    a point before its end counts from there). `in_world` is the open-world
    rule on where the log is, without the maps a match was kept on (None
    before any zone line)."""

    name: str
    size: int
    mtime_ns: int
    zone: str | None = None
    instance_id: int | None = None
    difficulty: int | None = None
    arena: bool = False
    in_world: bool | None = None
    zone_changes: int = 0
    match_ends: int = 0


class LiveWatch(_Model):
    """One poll: the flavor folder, its SavedVariables and its newest log
    (None when it has none), and what the poll cost: microseconds and the
    bytes of log read."""

    flavor_folder: str
    saved: SavedWatch
    log: LogWatch | None
    cost_us: int
    log_bytes_read: int


def _child(folder: Path, name: str) -> Path | None:
    """The sub-folder of `folder` called `name` in any case, never a link."""
    want = name.casefold()
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if entry.name.casefold() == want and entry.is_dir(follow_symlinks=False):
                    return Path(entry.path)
    except OSError:
        return None
    return None


def _dirs(folder: Path) -> list[Path]:
    try:
        with os.scandir(folder) as it:
            return sorted(Path(e.path) for e in it if e.is_dir(follow_symlinks=False))
    except OSError:
        return []


def saved_watch(flavor_path: Path, *, max_files: int = WATCH_MAX_FILES) -> SavedWatch:
    """The SavedVariables `.lua` files of every account of the flavor
    folder (the account's and each character's), by modification time,
    stat'ed and never opened. Links are not followed."""
    wtf = _child(flavor_path, "WTF")
    accounts = _child(wtf, "Account") if wtf is not None else None
    if accounts is None:
        return SavedWatch()
    folders: list[Path] = []
    for acct in _dirs(accounts):
        for sub in _dirs(acct):
            if sub.name.casefold() == "savedvariables":
                folders.append(sub)
                continue
            for char in _dirs(sub):
                sv = _child(char, "SavedVariables")
                if sv is not None:
                    folders.append(sv)
    seen = left = 0
    newest: tuple[int, str] | None = None
    for folder in folders:
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    if not entry.name.casefold().endswith(_SV_SUFFIX):
                        continue
                    if seen >= max_files:
                        left += 1
                        continue
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    seen += 1
                    rel = Path(entry.path).relative_to(flavor_path).as_posix()
                    if newest is None or (st.st_mtime_ns, rel) > newest:
                        newest = (st.st_mtime_ns, rel)
        except OSError:
            continue
    return SavedWatch(
        files=seen,
        newest=newest[1] if newest else None,
        newest_ns=newest[0] if newest else None,
        left_out=left,
    )


def logs_folder(flavor_path: Path) -> Path | None:
    """The flavor's `Logs/` folder, in any case, or None."""
    return _child(flavor_path, "Logs")


@dataclasses.dataclass
class _LogState:
    path: Path
    head: bytes
    offset: int
    state: worldpvp.ZoneState
    zone_changes: int = 0
    match_ends: int = 0


def _zone_state(fields: tuple[object, ...]) -> worldpvp.ZoneState:
    name = fields[1] if len(fields) > 1 else None
    text = name.text if isinstance(name, Quoted) else None
    if text is not None and len(text.encode("utf-8", "surrogatepass")) > worldpvp.ZONE_MAX_BYTES:
        text = None
    return worldpvp.ZoneState(
        zone=text,
        instance_id=_int(fields[0]) if fields else None,
        difficulty=_int(fields[2]) if len(fields) > 2 else None,
    )


def _int(value: object) -> int | None:
    if isinstance(value, str) and 0 < len(value) <= 13:
        body = value[1:] if value.startswith("-") else value
        if body.isascii() and body.isdigit():
            return int(value)
    return None


class Watcher:
    """What the Live view polls: one per server, its memory the only
    state. `poll` is safe from several threads at once; they take turns."""

    def __init__(self, *, tail_bytes: int = WATCH_TAIL_BYTES) -> None:
        self._lock = threading.Lock()
        self._log: _LogState | None = None
        self._tail = tail_bytes

    def poll(self, flavor_path: Path, flavor_folder: str) -> LiveWatch:
        with self._lock:
            began = time.perf_counter_ns()
            saved = saved_watch(flavor_path)
            log, read = self._log_watch(flavor_path)
            cost = (time.perf_counter_ns() - began) // 1000
            return LiveWatch(
                flavor_folder=flavor_folder,
                saved=saved,
                log=log,
                cost_us=cost,
                log_bytes_read=read,
            )

    def _log_watch(self, flavor_path: Path) -> tuple[LogWatch | None, int]:
        folder = logs_folder(flavor_path)
        newest = combatlog.newest_log(folder) if folder is not None else None
        if newest is None:
            self._log = None
            return None, 0
        try:
            size, head = combatlog.head(newest)
            mtime = newest.stat(follow_symlinks=False).st_mtime_ns
        except OSError:
            return None, 0
        held = self._log
        if held is None or held.path != newest or held.head != head or size < held.offset:
            held = _LogState(
                path=newest, head=head, offset=max(0, size - self._tail), state=worldpvp.ZoneState()
            )
            self._log = held
        elif size - held.offset > self._tail:
            held.offset = size - self._tail
        read = 0
        if size > held.offset:
            read = self._read_on(held, size)
        s = held.state
        known = s.zone is not None or s.instance_id is not None or s.difficulty is not None
        return (
            LogWatch(
                name=newest.name,
                size=size,
                mtime_ns=mtime,
                zone=s.zone,
                instance_id=s.instance_id,
                difficulty=s.difficulty,
                arena=s.arena,
                in_world=worldpvp.in_open_world(s) if known else None,
                zone_changes=held.zone_changes,
                match_ends=held.match_ends,
            ),
            read,
        )

    @staticmethod
    def _read_on(held: _LogState, size: int) -> int:
        """Read the lines added since `held.offset`: only the zone and
        arena lines are tokenized. A start inside a line (a read from
        before the end) passes that line over; a last line with no break
        waits for the next poll."""
        start = held.offset
        aligned = start == 0
        if not aligned:
            # Is `start` the beginning of a line? It is when the byte before it ends one.
            try:
                with held.path.open("rb") as f:
                    f.seek(start - 1)
                    aligned = f.read(1) == b"\n"
            except OSError:
                return 0
        end = start
        first = True
        try:
            for entry in combatlog.read_log(held.path, events=_ZONE_EVENTS, start=start):
                if entry.ending == "":
                    break  # still being written
                end = entry.offset + _length(entry)
                if first and not aligned:
                    first = False
                    continue
                first = False
                if isinstance(entry, Record):
                    _watch_line(held, entry)
                if end >= size:
                    break
        except OSError:
            return 0
        held.offset = max(held.offset, end)
        return end - start


def _watch_line(held: _LogState, rec: Record) -> None:
    if rec.event == "ZONE_CHANGE":
        held.state = _zone_state(rec.fields)
        held.zone_changes += 1
    elif rec.event == "ARENA_MATCH_START":
        held.state = held.state.model_copy(update={"arena": True})
    elif rec.event == "ARENA_MATCH_END":
        held.state = held.state.model_copy(update={"arena": False})
        held.match_ends += 1


def _length(entry: Record | Unparsed | Skipped) -> int:
    if isinstance(entry, Unparsed):
        return entry.length + len(entry.ending)
    try:
        size = len(entry.raw.encode("utf-8", "surrogateescape"))
    except UnicodeEncodeError:
        size = len(entry.raw.encode("utf-8", "surrogatepass"))
    return size + len(entry.ending)


# ─── the match recap ─────────────────────────────────────────────────────────


class MatchOpponent(_Model):
    """One player who fought you in the match, by `worldpvp`'s fight rule:
    the crew key and the name and realm it was made of, the unit name as
    the log wrote it, the damage lines between you, and whether you killed
    them or they killed you (in any of the match's encounters)."""

    key: str
    name: str
    realm: str
    unit_name: str | None
    hits: int
    killed_by_you: bool
    killed_you: bool


class MatchRecap(_Model):
    """The latest match the combat logs show.

    `log`: the log's file name. `zone`, `instance_id`, `difficulty`: from
    the zone line that began it. `start`, `end`: seconds since the epoch of
    its first and last line. `over`: the log left the place, or showed the
    arena's end line; `ended_by` says which (`left`, `match_end`, `new_log`,
    or None while it goes on). `arena`: an arena start line was read.
    `opponents`: who fought you, most hits first. `killing_blows`,
    `deaths`: yours, from the log. `result` (`won`, `lost` or None) and
    `result_from` (`log`, `crew_book`, or None). `book_foes`: the crew
    book's scoreboard of that match, when it was found. `fights`: damage
    lines between you and a player there; `lines`: lines read."""

    log: str
    zone: str | None
    instance_id: int | None
    difficulty: int | None
    start: int | None
    end: int | None
    over: bool
    ended_by: Literal["left", "match_end", "new_log"] | None
    arena: bool
    opponents: list[MatchOpponent]
    killing_blows: int
    deaths: int
    result: Literal["won", "lost"] | None
    result_from: Literal["log", "crew_book"] | None
    book_map: str | None = None
    book_foes: list[str] = Field(default_factory=list)
    fights: int
    lines: int


@dataclasses.dataclass
class _Stay:
    log: Path
    begin: int  # offset of the zone line
    stop: int  # offset where the next zone line begins, or the end read
    state: worldpvp.ZoneState
    start: int | None
    end: int | None = None
    arena: bool = False
    team: int | None = None  # ARENA_MATCH_START's team field
    winner: int | None = None  # ARENA_MATCH_END's winning team
    match_end: bool = False
    left: bool = False


def _stays(log: Path) -> list[_Stay]:
    """Every zone stay of a log, oldest first, read from its zone and arena
    lines only."""
    stays: list[_Stay] = []
    open_: _Stay | None = None
    last_end = 0
    for entry in combatlog.read_log(log, events=_ZONE_EVENTS):
        if entry.ending == "":
            break
        last_end = entry.offset + _length(entry)
        if not isinstance(entry, Record):
            continue
        when = worldpvp.seconds_of(entry.timestamp)
        if entry.event == "ZONE_CHANGE":
            if open_ is not None:
                open_.stop = entry.offset
                open_.left = True
            open_ = _Stay(
                log=log,
                begin=entry.offset,
                stop=last_end,
                state=_zone_state(entry.fields),
                start=when,
            )
            stays.append(open_)
        elif open_ is not None and entry.event == "ARENA_MATCH_START":
            open_.arena = True
            open_.team = _int(entry.fields[3]) if len(entry.fields) > 3 else None
        elif open_ is not None and entry.event == "ARENA_MATCH_END":
            open_.match_end = True
            open_.winner = _int(entry.fields[0]) if entry.fields else None
        if open_ is not None and not open_.left:
            open_.stop = last_end
    if open_ is not None and not open_.left:
        open_.stop = last_end
    return stays


def _span(log: Path, begin: int, stop: int) -> Iterator[Record | Unparsed | Skipped]:
    for entry in combatlog.read_log(log, events=_MATCH_EVENTS, start=begin):
        if entry.offset >= stop:
            return
        yield entry


@dataclasses.dataclass
class _Tally:
    owners: frozenset[str]
    names: dict[str, str] = dataclasses.field(default_factory=dict)  # player GUID -> unit name
    killing_blows: int = 0
    deaths: int = 0
    last: int | None = None
    lines: int = 0

    def see(
        self, entries: Iterable[Record | Unparsed | Skipped]
    ) -> Iterator[Record | Unparsed | Skipped]:
        for entry in entries:
            self.lines += 1
            if isinstance(entry, Record):
                when = worldpvp.seconds_of(entry.timestamp)
                if when is not None:
                    self.last = when if self.last is None else max(self.last, when)
                self._record(entry)
            yield entry

    def _record(self, rec: Record) -> None:
        f = rec.fields
        if len(f) < 6:
            return
        for guid, name in ((f[0], f[1]), (f[4], f[5])):
            if (
                isinstance(guid, str)
                and isinstance(name, Quoted)
                and guid.startswith("Player-")
                and (len(self.names) < worldpvp.MAX_NAMES or guid in self.names)
            ):
                self.names[guid] = name.text
        if rec.event == "PARTY_KILL" and len(f) >= 7:
            src, dst = f[0], f[4]
            flags = f[2]
            mine = isinstance(src, str) and src.upper() in self.owners
            if not mine and isinstance(flags, str) and flags.startswith("0x"):
                try:
                    value = int(flags, 16)
                except ValueError:
                    value = 0
                mine = value & combatlog.AFFILIATION_MASK == combatlog.AFFILIATION_MINE
            if (
                mine
                and isinstance(dst, str)
                and dst.startswith("Player-")
                and dst.upper() not in self.owners
            ):
                self.killing_blows += 1
        elif rec.event == "UNIT_DIED":
            dst = f[4]
            if isinstance(dst, str) and dst.upper() in self.owners:
                self.deaths += 1


def _book_match(
    stay: _Stay, end: int | None, book: Sequence[crewbook.CrewMatch]
) -> crewbook.CrewMatch | None:
    """The crew book's match on the stay's map nearest its end, within
    `_RESULT_BEFORE` before its start and `_RESULT_AFTER` after its end."""
    if stay.start is None:
        return None
    low = stay.start - _RESULT_BEFORE
    high = (end if end is not None else stay.start) + _RESULT_AFTER
    best: crewbook.CrewMatch | None = None
    for m in book:
        if not low <= m.at <= high:
            continue
        if stay.state.instance_id is not None and m.map_id is not None:
            if m.map_id != stay.state.instance_id:
                continue
        elif (
            stay.state.zone is None
            or m.map is None
            or m.map.casefold() != stay.state.zone.casefold()
        ):
            continue
        if best is None or abs(m.at - high) < abs(best.at - high):
            best = m
    return best


def match_recap(
    logs: Sequence[Path],
    owners: Mapping[str, str | None],
    *,
    pvp_maps: Iterable[int] = (),
    book: Sequence[crewbook.CrewMatch] = (),
    max_bytes: int = RECAP_MAX_BYTES,
) -> MatchRecap | None:
    """The latest match in `logs` (oldest first, as `combatlog.find_logs`
    gives them; the newest `RECAP_LOGS` are read, within `max_bytes`) for
    your characters' GUIDs `owners`, or None when they show none.
    `pvp_maps`: the instance ids a match was kept on; `book`: the crew
    book's matches, for the result of a battleground."""
    maps = frozenset(pvp_maps)
    upper = frozenset(g.upper() for g in owners)
    chosen = list(logs)[-RECAP_LOGS:]
    budget = max_bytes
    looked = 0
    for index in range(len(chosen) - 1, -1, -1):
        log = chosen[index]
        try:
            size = log.stat().st_size
        except OSError:
            continue
        if size > budget:
            break
        budget -= size
        try:
            stays = _stays(log)
        except OSError:
            continue
        for stay in reversed(stays):
            if worldpvp.in_open_world(stay.state, maps) and not stay.arena:
                continue
            looked += 1
            if looked > _MAX_STAYS:
                return None
            recap = _recap(stay, owners, upper, maps, book, newer=index < len(chosen) - 1)
            if recap is not None:
                return recap
    return None


def _recap(
    stay: _Stay,
    owners: Mapping[str, str | None],
    upper: frozenset[str],
    maps: frozenset[int],
    book: Sequence[crewbook.CrewMatch],
    *,
    newer: bool,
) -> MatchRecap | None:
    tally = _Tally(upper)
    here = stay.state

    def place(s: worldpvp.ZoneState) -> bool:
        return s.instance_id == here.instance_id and s.zone == here.zone

    try:
        scan = worldpvp.scan_entries(
            tally.see(_span(stay.log, stay.begin, stay.stop)),
            owners,
            start=stay.begin,
            place=place,
        )
    except OSError:
        return None
    pvp_map = here.instance_id is not None and here.instance_id in maps
    if not (stay.arena or pvp_map or scan.fights):
        return None  # a dungeon or raid with no player in it
    by_key: dict[str, MatchOpponent] = {}
    for e in scan.encounters:
        for p in e.players:
            if p.side != "foe":
                continue
            held = by_key.get(p.key)
            if held is None:
                by_key[p.key] = MatchOpponent(
                    key=p.key,
                    name=p.name,
                    realm=p.realm,
                    unit_name=tally.names.get(p.guid),
                    hits=p.hits,
                    killed_by_you=p.killed_by_you,
                    killed_you=p.killed_you,
                )
            else:
                by_key[p.key] = held.model_copy(
                    update={
                        "hits": held.hits + p.hits,
                        "killed_by_you": held.killed_by_you or p.killed_by_you,
                        "killed_you": held.killed_you or p.killed_you,
                    }
                )
    opponents = sorted(by_key.values(), key=lambda o: (-o.hits, o.key))
    ended_by: Literal["left", "match_end", "new_log"] | None
    if stay.match_end:
        ended_by = "match_end"
    elif stay.left:
        ended_by = "left"
    elif newer:
        ended_by = "new_log"
    else:
        ended_by = None
    result: Literal["won", "lost"] | None = None
    result_from: Literal["log", "crew_book"] | None = None
    if stay.match_end and stay.winner is not None and stay.team is not None:
        result, result_from = ("won" if stay.winner == stay.team else "lost"), "log"
    end = tally.last if tally.last is not None else stay.start
    found = _book_match(stay, end, book) if ended_by is not None else None
    if found is not None and result is None and found.won is not None:
        result, result_from = ("won" if found.won else "lost"), "crew_book"
    return MatchRecap(
        log=stay.log.name,
        zone=here.zone,
        instance_id=here.instance_id,
        difficulty=here.difficulty,
        start=stay.start,
        end=end,
        over=ended_by is not None,
        ended_by=ended_by,
        arena=stay.arena,
        opponents=opponents,
        killing_blows=tally.killing_blows,
        deaths=tally.deaths,
        result=result,
        result_from=result_from,
        book_map=found.map if found is not None else None,
        book_foes=list(found.foes) if found is not None else [],
        fights=scan.fights,
        lines=tally.lines,
    )


# ─── the session recap ───────────────────────────────────────────────────────


class ItemCount(_Model):
    """One item a character holds, as Syndicator lists it: the item id, its
    name from the link, and how many across every place Syndicator lists
    (bags, bank, equipped, mail and the rest)."""

    item_id: int
    name: str | None
    count: int


class NewItem(_Model):
    """An item the character holds more of than at the earlier save."""

    item_id: int
    name: str | None
    before: int
    after: int


class SessionMatch(_Model):
    """A crew book match since the earlier save."""

    at: int
    map: str | None
    kind: crewbook.MatchKind
    won: bool | None
    foes: list[str]


class SessionOpponent(_Model):
    """A player met since the earlier save: in how many of the book's
    matches they were on the other team, in how many open-world encounters
    they fought you, and whether you killed them or they killed you there."""

    key: str
    matches: int = 0
    encounters: int = 0
    killed_by_you: bool = False
    killed_you: bool = False


class SessionRecap(_Model):
    """What changed since the earlier save of the character's record.

    `saved_at_ns`: the save shown (the newest); `since_ns`: the earlier save
    it is compared with, None when wowlab has none (`not_compared` says
    why); `since_snapshot`: the snapshot holding it. Level before and after;
    the gear and currency changes `charhistory` reads. `new_items` from
    Syndicator (`items_note` says why there are none to show, when so).
    `matches`: the crew book's since then; `opponents`: who you met, from
    those matches and the open world (`world_note` says what was not read).
    """

    saved_at_ns: int
    since_ns: int | None
    since_snapshot: str | None
    not_compared: str | None
    level_before: int | None = None
    level_after: int | None = None
    gear_changes: list[charhistory.GearChange] = Field(default_factory=list)
    gear_not_compared: str | None = None
    currency_changes: list[charhistory.CurrencyChange] = Field(default_factory=list)
    currencies_not_compared: str | None = None
    new_items: list[NewItem] = Field(default_factory=list)
    items_note: str | None = None
    matches: list[SessionMatch] = Field(default_factory=list)
    opponents: list[SessionOpponent] = Field(default_factory=list)
    world_note: str | None = None


def _syndicator_data(data: bytes) -> object:
    """`SYNDICATOR_DATA` as plain values (`luadata`: data, never code)."""
    return luadata.parse(data).to_python().get("SYNDICATOR_DATA")


@dataclasses.dataclass
class _Held:
    count: int = 0
    name: str | None = None


def _walk_items(value: object, found: dict[int, _Held], depth: int = 0) -> None:
    """Every table under `value` with an `itemID`, as one stack each."""
    if depth > luadata.MAX_DEPTH:
        return
    if isinstance(value, dict):
        item = value.get("itemID")
        if isinstance(item, int) and not isinstance(item, bool):
            count = value.get("itemCount")
            n = count if isinstance(count, int) and not isinstance(count, bool) else 1
            held = found.setdefault(item, _Held())
            held.count += max(0, n)
            link = value.get("itemLink")
            if held.name is None and isinstance(link, str):
                with contextlib.suppress(ValueError):
                    held.name = itemlink.parse(link).name
            return
        for v in value.values():
            _walk_items(v, found, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _walk_items(v, found, depth + 1)


def syndicator_items(data: bytes, names: Iterable[str]) -> list[ItemCount] | str:
    """The items Syndicator's saved file lists for the character it keys by
    one of `names` (compared without case), or why there are none: the file
    cannot be read, or holds no such character. Every table under the
    character that has an `itemID` counts as one stack (`itemCount`, 1 when
    absent); the layout is read from one capture (not yet confirmed)."""
    try:
        root = _syndicator_data(data)
    except luadata.LuaDataError as exc:
        return f"Syndicator's file could not be read: {exc}"
    chars = root.get("Characters") if isinstance(root, dict) else None
    if not isinstance(chars, dict):
        return "Syndicator's file lists no characters"
    wanted = {n.casefold() for n in names}
    picked: object = None
    for key, value in chars.items():
        if isinstance(key, str) and key.casefold() in wanted:
            picked = value
            break
        details = value.get("details") if isinstance(value, dict) else None
        named = details.get("character") if isinstance(details, dict) else None
        if isinstance(named, str) and named.casefold() in wanted:
            picked = value
            break
    if not isinstance(picked, dict):
        return "Syndicator lists no character of this name"
    found: dict[int, _Held] = {}
    for part, value in picked.items():
        if part != "details":
            _walk_items(value, found)
    return [ItemCount(item_id=i, name=h.name, count=h.count) for i, h in sorted(found.items())]


def new_items(before: Sequence[ItemCount], after: Sequence[ItemCount]) -> list[NewItem]:
    """The items `after` holds more of than `before`, most gained first."""
    had = {i.item_id: i.count for i in before}
    out = [
        NewItem(item_id=i.item_id, name=i.name, before=had.get(i.item_id, 0), after=i.count)
        for i in after
        if i.count > had.get(i.item_id, 0)
    ]
    return sorted(out, key=lambda n: (-(n.after - n.before), n.item_id))


def earlier_row(history: charhistory.CharHistory) -> charhistory.HistoryRow | None:
    """The save the newest row of `history` was compared with, or None."""
    if not history.rows:
        return None
    now = history.rows[-1]
    if now.changed_from_ns is None:
        return None
    return next((r for r in history.rows if r.saved_at_ns == now.changed_from_ns), None)


def session_recap(
    history: charhistory.CharHistory,
    *,
    items_before: Sequence[ItemCount] | str | None = None,
    items_after: Sequence[ItemCount] | str | None = None,
    book: Sequence[crewbook.CrewMatch] = (),
    world: Sequence[worldpvp.WorldEncounter] = (),
    world_note: str | None = None,
) -> SessionRecap | None:
    """The newest save of `history` against the one before it, with the
    Syndicator item lists of the two (`items_before`, `items_after`, or why
    one could not be read), the crew book's matches and the open-world
    encounters (`world`, already read); those older than the earlier save
    are left out. None when `history` has no row."""
    if not history.rows:
        return None
    now = history.rows[-1]
    before = earlier_row(history)
    if before is None:
        return SessionRecap(
            saved_at_ns=now.saved_at_ns,
            since_ns=None,
            since_snapshot=None,
            not_compared="wowlab has no earlier save of this character to compare with",
            level_after=now.level,
            world_note=world_note,
        )
    since_s = before.saved_at_ns // 1_000_000_000
    now_s = now.saved_at_ns // 1_000_000_000
    matches = [
        SessionMatch(at=m.at, map=m.map, kind=m.kind, won=m.won, foes=list(m.foes))
        for m in sorted(book, key=lambda m: m.at)
        if since_s < m.at <= now_s
    ]
    met: dict[str, SessionOpponent] = {}
    for m in matches:
        for key in dict.fromkeys(m.foes):
            held = met.get(key) or SessionOpponent(key=key)
            met[key] = held.model_copy(update={"matches": held.matches + 1})
    for e in world:
        if not since_s < e.start <= now_s:
            continue
        for p in e.players:
            if p.side != "foe":
                continue
            held = met.get(p.key) or SessionOpponent(key=p.key)
            met[p.key] = held.model_copy(
                update={
                    "encounters": held.encounters + 1,
                    "killed_by_you": held.killed_by_you or p.killed_by_you,
                    "killed_you": held.killed_you or p.killed_you,
                }
            )
    items: list[NewItem] = []
    items_note: str | None = None
    if isinstance(items_after, str):
        items_note = items_after
    elif isinstance(items_before, str):
        items_note = items_before
    elif items_after is None:
        items_note = "Syndicator's saved file is not in this game account"
    elif items_before is None:
        items_note = "the snapshot with the earlier save holds no copy of Syndicator's file"
    else:
        items = new_items(items_before, items_after)
    return SessionRecap(
        saved_at_ns=now.saved_at_ns,
        since_ns=before.saved_at_ns,
        since_snapshot=before.snapshots[-1] if before.snapshots else None,
        not_compared=None,
        level_before=before.level,
        level_after=now.level,
        gear_changes=list(now.gear_changes),
        gear_not_compared=now.gear_not_compared,
        currency_changes=list(now.currency_changes),
        currencies_not_compared=now.currencies_not_compared,
        new_items=items,
        items_note=items_note,
        matches=matches,
        opponents=sorted(met.values(), key=lambda o: (-(o.matches + o.encounters), o.key)),
        world_note=world_note,
    )


def world_since(
    logs: Sequence[Path],
    owners: Mapping[str, str | None],
    since_ns: int,
    *,
    pvp_maps: Iterable[int] = (),
    max_bytes: int = SESSION_LOG_BYTES,
) -> tuple[list[worldpvp.WorldEncounter], str | None]:
    """The open-world encounters of the logs written since `since_ns`
    (oldest first), within `max_bytes` (the newest first), and what was
    not read, in words, or None."""
    budget = max_bytes
    picked: list[Path] = []
    skipped = 0
    for log in reversed(logs):
        try:
            st = log.stat()
        except OSError:
            continue
        if st.st_mtime_ns < since_ns:
            break
        if st.st_size > budget:
            skipped += 1
            continue
        budget -= st.st_size
        picked.append(log)
    found: list[worldpvp.WorldEncounter] = []
    unreadable = 0
    maps = list(pvp_maps)
    for log in reversed(picked):
        try:
            found.extend(worldpvp.read_world(log, owners, pvp_maps=maps).encounters)
        except OSError:
            unreadable += 1
    words = []
    if skipped:
        words.append(f"{skipped} larger combat log{'s' if skipped != 1 else ''} not read")
    if unreadable:
        words.append(f"{unreadable} combat log{'s' if unreadable != 1 else ''} could not be read")
    return found, "; ".join(words) or None
