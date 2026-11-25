"""desktable: the records wowlab sends to the game for the tooltip line
(M22-04).

Hovering a player in game adds one line (the lab-addon's `Crew.lua`):
fights, killing blows each way, matches won, and the Nemesis, Rival team and
kill-on-sight marks. The addon counts the matches its own crew book records,
live; what only wowlab knows (the open-world fights read from combat logs,
the history its crew database kept after the game's list dropped it, the
two marks, whose rules live in `nemesis`, and Spy's kill-on-sight lists) it
reads from `WowLabDeskDB`, a table this module builds and the CLI writes
into the account's `WowLab.lua` through the write gate (L2). The game loads
that table at login or `/reload`; the addon only reads it.

The table (schema 1; `docs/LAB_FORMATS.md`, "WowLabDeskDB"):

    WowLabDeskDB = {
      ["schema"] = 1,
      ["day"] = "2026-11-24",    -- the day wowlab wrote it, the computer's date
      ["count"] = 2,             -- players in `players`
      ["players"] = {
        ["<stored key>"] = {
          ["fights"] = 4, ["kills"] = 1, ["deaths"] = 3, ["matches"] = 2,
          ["won"] = 1, ["book_faced"] = 2, ["book_won"] = 1,
          ["nemesis"] = true, ["rival"] = true, ["kos"] = true,
        },
      },
    }

Every key is written as the client writes keys (`["name"] = `), laid out
as the rest of the file is (`luadata.serialize`); the client writes the
table again in its own layout at the next logout.

`fights` is the whole record (`nemesis.Record.fights`), `kills` and
`deaths` its personal part, `matches` its fights from battleground and arena
matches and `won` the ones your side won. `book_faced` and `book_won` are
the crew book's own counters for the player (`faced`, `faced_won`) in the
file as written, so the addon adds only the matches it records after the
write: the difference, or the whole counter when it is lower (the game
dropped the player and started again), as `crewdb` adds them up. A zero or
false field is left out. A player with neither a record nor a kill-on-sight
mark has no row.

Nothing here reads or writes a file (L1, L2: the CLI hands the bytes to
`guard`) and no Lua is run (L3): the file is parsed as data, the table is
put in as one top-level assignment, and every other assignment keeps its
bytes (L4). Every result is checked before it is returned: parsed again, it
must hold the new table and every other variable as it was.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict

from wowlab_core import crewbook, luadata, nemesis
from wowlab_core.errors import WowlabError
from wowlab_core.luadata import (
    Assignment,
    Entry,
    KeyStyle,
    LuaBool,
    LuaDocument,
    LuaNumber,
    LuaString,
    LuaTable,
    LuaValue,
)
from wowlab_core.luaquote import lua_quote

DESK_VARIABLE: Final = "WowLabDeskDB"
DESK_SCHEMA: Final = 1
# The most players the table holds. A row is about 90 bytes with a few
# fields and about 160 with every one (measured on constructed rows), so
# about 0.5 to 0.8 MB at the cap with short keys, and up to about 1.4 MB
# with long non-ASCII ones (keys are at most 256 bytes, the crew database's
# rule). The game loads the file whole at login, so the table's bytes are
# capped too: `with_table` refuses one over MAX_TABLE_BYTES.
MAX_PLAYERS: Final = 5000
MAX_TABLE_BYTES: Final = 4 * 1024 * 1024
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII)
# The fields of a row, in the order they are written.
_NUMBERS: Final = ("fights", "kills", "deaths", "matches", "won", "book_faced", "book_won")
_MARKS: Final = ("nemesis", "rival", "kos")


class DeskTableError(WowlabError, ValueError):
    """The table cannot be put in. The message is in the player's words and
    quotes nothing from the file."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DeskRow(_Model):
    """One player's row: the stored key, the record's counts and marks, and
    the crew book's counters as the file being written holds them."""

    key: str
    fights: int = 0
    kills: int = 0
    deaths: int = 0
    matches: int = 0
    won: int = 0
    nemesis: bool = False
    rival: bool = False
    kos: bool = False
    book_faced: int = 0
    book_won: int = 0


class DeskTableRead(_Model):
    """A `WowLabDeskDB` as a file holds it now: its schema, the day it was
    written and how many players it holds (None for a field it does not
    hold as this module writes it)."""

    schema_number: int | None = None
    day: str | None = None
    players: int = 0


def _whole(value: int | None) -> int:
    return value if value is not None and value > 0 else 0


def row_for(
    key: str,
    record: nemesis.Record | None,
    pvp: crewbook.CrewPvp | None,
    *,
    kos: bool,
) -> DeskRow | None:
    """The row of one player: from their record, their crew book counters
    and whether a Spy kill-on-sight list holds them; None when they have
    neither a record nor the mark."""
    if record is None and not kos:
        return None
    faced = _whole(pvp.faced if pvp is not None else None)
    won = min(_whole(pvp.faced_won if pvp is not None else None), faced)
    if record is None:
        return DeskRow(key=key, kos=kos, book_faced=faced, book_won=won)
    return DeskRow(
        key=key,
        fights=record.fights,
        kills=record.personal.kills,
        deaths=record.personal.deaths,
        matches=record.sources.get("match", 0),
        won=record.team.wins,
        nemesis=record.personal.nemesis,
        rival=record.team.rival,
        kos=kos,
        book_faced=faced,
        book_won=won,
    )


def rows_from(
    records: Iterable[tuple[str, nemesis.Record | None, int | None]],
    pvp: Mapping[str, crewbook.CrewPvp],
    kos: Collection[str],
    *,
    limit: int = MAX_PLAYERS,
) -> tuple[list[DeskRow], int]:
    """The rows to send, by stored key, and how many were left out past
    `limit`. `records` is each player wowlab's crew database keeps: the key,
    the record and the start of their latest fight. A player only a Spy
    kill-on-sight list holds gets a row too. Past `limit`, the marked
    players (Nemesis, Rival team, kill on sight) are kept first, then the
    latest fight first; the rows come back in key order, so the same
    records always make the same bytes."""
    found: dict[str, tuple[DeskRow, int]] = {}
    wanted = set(kos)
    for key, record, last in records:
        row = row_for(key, record, pvp.get(key), kos=key in wanted)
        if row is not None and key not in found:
            found[key] = (row, last or 0)
    for key in sorted(wanted - found.keys()):
        row = row_for(key, None, pvp.get(key), kos=True)
        assert row is not None
        found[key] = (row, 0)
    ranked = sorted(
        found.values(),
        key=lambda x: (not (x[0].nemesis or x[0].rival or x[0].kos), -x[1], x[0].key),
    )
    kept = [row for row, _ in ranked[: max(limit, 0)]]
    return sorted(kept, key=lambda r: r.key), max(len(ranked) - limit, 0)


# ─── the table as Lua data ───────────────────────────────────────────────────


def _string_key_entry(key: str, value: LuaValue) -> Entry:
    """`["key"] = value,`, as the client writes every key of a saved
    table, laid out as the file's other entries are."""
    return Entry(
        lead=None,
        style=KeyStyle.STRING,
        key=LuaString(None, lua_quote(key.encode("utf-8", "surrogateescape"))),
        key_close_lead=None,
        eq_lead=None,
        value=value,
        sep_lead=None,
        sep=None,
        comment=None,
        duplicate=False,
    )


def _table(entries: Iterable[Entry]) -> LuaTable:
    return LuaTable(lead=None, entries=tuple(entries), close_lead=None)


def _number(n: int) -> LuaNumber:
    return LuaNumber(None, str(n))


def _row_value(row: DeskRow) -> LuaTable:
    fields: list[Entry] = []
    for name in _NUMBERS:
        n = getattr(row, name)
        if n:
            fields.append(_string_key_entry(name, _number(n)))
    for name in _MARKS:
        if getattr(row, name):
            fields.append(_string_key_entry(name, LuaBool(None, True)))
    return _table(fields)


def _row_python(row: DeskRow) -> dict[str, object]:
    out: dict[str, object] = {n: getattr(row, n) for n in _NUMBERS if getattr(row, n)}
    out.update({n: True for n in _MARKS if getattr(row, n)})
    return out


def _check_day(day: str) -> None:
    if not _DAY.fullmatch(day):
        raise DeskTableError(f"{day!r} is not a day written as YYYY-MM-DD; nothing was changed.")


def table_value(rows: Iterable[DeskRow], day: str) -> LuaTable:
    """`WowLabDeskDB`'s table for these rows, written on `day`."""
    _check_day(day)
    listed = list(rows)
    players = _table(_string_key_entry(r.key, _row_value(r)) for r in listed)
    return _table(
        [
            _string_key_entry("schema", _number(DESK_SCHEMA)),
            _string_key_entry("day", LuaString(None, lua_quote(day.encode("ascii")))),
            _string_key_entry("count", _number(len(listed))),
            _string_key_entry("players", players),
        ]
    )


def table_python(rows: Iterable[DeskRow], day: str) -> dict[str, object]:
    """The table as `luadata.LuaDocument.to_python` gives it back."""
    listed = list(rows)
    return {
        "schema": DESK_SCHEMA,
        "day": day,
        "count": len(listed),
        "players": {r.key: _row_python(r) for r in listed},
    }


def _desk_index(document: LuaDocument) -> int | None:
    found = [i for i, a in enumerate(document.assignments) if a.name == DESK_VARIABLE]
    if len(found) > 1:
        raise DeskTableError(
            "This file sets the records for the game more than once, which the game is not "
            "known to write; wowlab does not change it. Nothing was changed."
        )
    return found[0] if found else None


def with_table(data: bytes, rows: Iterable[DeskRow], day: str) -> bytes:
    """The bytes of `data` (an account's `WowLab.lua`) with `WowLabDeskDB`
    set to these rows: the old table's value replaced where the file has
    one (its leading bytes kept), else the table added after the last
    variable. Every other variable keeps its bytes. The same bytes when the
    file already holds exactly this table."""
    listed = list(rows)
    document = luadata.parse(data)
    loaded = document.to_python()
    index = _desk_index(document)
    value = table_value(listed, day)
    assignments = list(document.assignments)
    if index is None:
        assignments.append(Assignment(lead=None, name=DESK_VARIABLE, eq_lead=None, value=value))
    else:
        assignments[index] = assignments[index]._replace(value=value)
    want = table_python(listed, day)
    if index is not None and loaded.get(DESK_VARIABLE) == want:
        return data
    alone = luadata.serialize(
        LuaDocument((Assignment(lead=None, name=DESK_VARIABLE, eq_lead=None, value=value),), None)
    )
    if len(alone) > MAX_TABLE_BYTES:
        raise DeskTableError(
            f"The records would take {len(alone):,} bytes, more than the "
            f"{MAX_TABLE_BYTES:,} wowlab writes into a file the game loads whole at login; "
            "nothing was changed."
        )
    out = luadata.serialize(document._replace(assignments=tuple(assignments)))
    expected = {k: v for k, v in loaded.items() if k != DESK_VARIABLE}
    expected[DESK_VARIABLE] = want
    return _checked(out, expected)


def _checked(out: bytes, expected: dict[str, object]) -> bytes:
    """`out`, when it loads back as `expected`, every variable of it."""
    if luadata.parse(out).to_python() != expected:  # pragma: no cover - a serializer bug
        raise DeskTableError(
            "The records could not be changed without changing something else in the file; "
            "nothing was changed."
        )
    return out


# ─── a forget reaches the table too ──────────────────────────────────────────


def _key_text(entry: Entry) -> str | None:
    """A keyed entry's key as text, else None."""
    if entry.style == KeyStyle.NAME and isinstance(entry.key, str):
        return entry.key
    if entry.style == KeyStyle.STRING and isinstance(entry.key, LuaString):
        return entry.key.value
    return None


def _field_at(table: LuaTable, name: str) -> int | None:
    found = [i for i, e in enumerate(table.entries) if _key_text(e) == name]
    if len(found) > 1:
        raise DeskTableError(
            f"The records for the game hold their {name} twice, which wowlab never writes; "
            "wowlab does not change them. Nothing was changed."
        )
    return found[0] if found else None


def _trivia(slot: bytes | None) -> bool:
    return slot is not None and slot.strip(b" \t\r\n\f\v") != b""


def _dropped(table: LuaTable, drop: Collection[int]) -> LuaTable:
    """`table` without the entries at `drop`, laid out as `crewedit` takes
    entries out: where one held a comment, the slot after it is laid out
    again, so no comment that stood with it stays."""
    out: list[Entry] = []
    close = table.close_lead
    again = False
    for i, entry in enumerate(table.entries):
        if i in drop:
            again = again or _trivia(entry.lead) or entry.comment is not None
            continue
        if again:
            entry = entry._replace(lead=None)
            again = False
        out.append(entry)
    if again:
        close = None
    return table._replace(entries=tuple(out), close_lead=close)


def keys_held(data: bytes) -> list[str]:
    """Every player key the table in `data` holds, in file order; empty
    when the file holds no table, or one whose players are not a table."""
    value = luadata.parse(data).to_python().get(DESK_VARIABLE)
    players = value.get("players") if isinstance(value, dict) else None
    return [k for k in players if isinstance(k, str)] if isinstance(players, dict) else []


def without(data: bytes, keys: Collection[str] | None) -> bytes:
    """The bytes of `data` with the rows of `keys` (stored keys; each also
    matched under the crew book's folding rule, `crewbook.fold`) taken out
    of the table's `players`, and `count` set to the rows left; with `keys`
    None, every row (a forget of every player). Every other variable, and
    every other field of the table, keeps its bytes. The same bytes when
    the file holds no table or none of them. A table whose `players` is not
    a table is left as it is (a forget then finds the name still there and
    refuses)."""
    document = luadata.parse(data)
    index = _desk_index(document)
    if index is None:
        return data
    table = document.assignments[index].value
    if not isinstance(table, LuaTable):
        return data
    at = _field_at(table, "players")
    players = table.entries[at].value if at is not None else None
    if at is None or not isinstance(players, LuaTable):
        return data
    wanted = None if keys is None else {crewbook.fold(k) for k in keys}
    drop = {
        i
        for i, e in enumerate(players.entries)
        if wanted is None or ((t := _key_text(e)) is not None and crewbook.fold(t) in wanted)
    }
    if not drop:
        return data
    kept = _dropped(players, drop)
    entries = list(table.entries)
    entries[at] = entries[at]._replace(value=kept)
    count_at = _field_at(table, "count")
    if count_at is not None:
        old = entries[count_at].value
        lead = old.lead if isinstance(old, LuaNumber) else None
        entries[count_at] = entries[count_at]._replace(
            value=LuaNumber(lead, str(len(kept.entries)))
        )
    assignments = list(document.assignments)
    assignments[index] = assignments[index]._replace(value=table._replace(entries=tuple(entries)))
    out = luadata.serialize(document._replace(assignments=tuple(assignments)))
    loaded = document.to_python()
    expected = dict(loaded)
    whole = loaded[DESK_VARIABLE]
    assert isinstance(whole, dict)
    desk = dict(whole)
    held = desk.get("players")
    assert isinstance(held, dict)
    desk["players"] = {
        k: v for k, v in held.items() if not (wanted is None or crewbook.fold(str(k)) in wanted)
    }
    if count_at is not None:
        desk["count"] = len(kept.entries)
    expected[DESK_VARIABLE] = desk
    return _checked(out, expected)


def read_table(data: bytes) -> DeskTableRead | None:
    """The `WowLabDeskDB` the file holds, None when it holds none."""
    value = luadata.parse(data).to_python().get(DESK_VARIABLE)
    if value is None:
        return None
    if not isinstance(value, dict):
        return DeskTableRead()
    schema = value.get("schema")
    day = value.get("day")
    players = value.get("players")
    return DeskTableRead(
        schema_number=schema if isinstance(schema, int) and not isinstance(schema, bool) else None,
        day=day if isinstance(day, str) and _DAY.fullmatch(day) else None,
        players=len(players) if isinstance(players, dict) else 0,
    )
