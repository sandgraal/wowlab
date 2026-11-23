"""`wowlab lookup` (M22-08): where an item drops, which quest gives it, who
sells it, and where an NPC, object or quest is.

It searches the flavor's own QuestieDB addon, read as data (`questiedb`,
L1, L3), and the item and zone tables cached for the flavor's version
(`lookup.LOOKUP_TABLES`, ADR-0022). `--fetch` downloads a table not cached
yet through `gamedata`, the only network client, one request each and never
one already cached (L5); without it nothing is fetched.
"""

from typing import Annotated, Literal

import typer

from wowlab_core import gamedata, lookup
from wowlab_core.cli._cmd_doctor import _open_gamedata
from wowlab_core.cli._common import (
    EXIT_ERROR,
    FlavorOpt,
    JsonOpt,
    NotesOpt,
    RootOpt,
    _discover,
    _emit,
    _handled,
    _Out,
    _plural,
    _say,
    _say_notes,
    _say_prose,
    _select_flavor,
    app,
)

# ─── lookup ──────────────────────────────────────────────────────────────────

# How many entries of one list (vendors, drops, places) a hit's text shows.
_SHOWN = 8
_FACTIONS = {"A": "Alliance", "H": "Horde", "AH": "both factions"}
_LOOKUP_KIND_WORDS = {"item": "Item", "npc": "NPC", "quest": "Quest", "object": "Object"}


class LookupHitReport(lookup.LookupHit):
    """One match (`lookup.LookupHit`), with `lines`, what the text output
    prints under its heading, and `heading`, that heading."""

    heading: str
    lines: list[str]


class LookupReport(_Out):
    """`wowlab lookup --json`: the matches for `query` among `kinds`, best
    first (`hits`, at most `--limit`), how many matched in all (`total`),
    the flavor folder and game version searched, what became of QuestieDB
    (`questiedb`: its state, the TOC read, the parts not read and why) and
    of each game table (`game_tables`: `cached`, `fetched`, `missing`,
    `error` or `no_version`), `words`, the sentence the text output opens
    with, and `notes`, the standing notes `--notes` prints."""

    query: str
    kinds: list[lookup.SearchKind]
    flavor: str
    version: str | None
    questiedb: lookup.QuestieState
    game_tables: list[lookup.GameTableState]
    total: int
    hits: list[LookupHitReport]
    words: str
    notes: list[str]


def _matches(n: int) -> str:
    return "1 match" if n == 1 else f"{n} matches"


def _spots(places: list[lookup.Place]) -> int:
    return sum(p.count for p in places)


def _levels(low: int | None, high: int | None) -> str | None:
    if low is None and high is None:
        return None
    if low is None or high is None or low == high:
        return f"level {low if low is not None else high}"
    return f"level {low}-{high}"


def _point(p: tuple[float, float]) -> str:
    return f"{p[0]:.1f}, {p[1]:.1f}"


def _zone(zone: str | None, zone_id: int) -> str:
    return zone or f"zone {zone_id}"


def _places(places: list[lookup.Place]) -> str | None:
    """`in Ashenvale at 75.0, 70.1 (and 3 more spots)`, up to three zones."""
    if not places:
        return None
    parts: list[str] = []
    for place in places[:3]:
        where = _zone(place.zone, place.zone_id)
        mapped = [p for p in place.points if p[0] >= 0 and p[1] >= 0]
        if mapped:
            more = place.count - 1
            spot = f" at {_point(mapped[0])}"
            if more > 0:
                spot += f" (and {_plural(more, 'more spot')})"
            where += spot
        elif place.points:
            # QuestieDB writes -1, -1 for a spawn inside an instance, which
            # has no spot on the zone's map.
            where += " (inside)"
        parts.append(where)
    text = "in " + "; ".join(parts)
    if len(places) > 3:
        text += f"; and {_plural(len(places) - 3, 'more zone')}"
    return text


def _npc(n: lookup.NpcRef, *, faction: bool = False) -> str:
    bits = [n.name or f"NPC {n.id}"]
    detail = [d for d in (n.subname, _levels(n.level_min, n.level_max)) if d]
    if faction and n.friendly_to in _FACTIONS:
        detail.append(_FACTIONS[n.friendly_to])
    if detail:
        bits.append(f"({', '.join(detail)})")
    where = _places(n.places)
    if where:
        bits.append(where)
    return " ".join(bits)


def _object(o: lookup.ObjectRef) -> str:
    where = _places(o.places)
    name = o.name or f"object {o.id}"
    return f"{name} {where}" if where else name


def _quest(q: lookup.QuestRef) -> str:
    detail = [d for d in (f"level {q.level}" if q.level else None, q.zone) if d]
    name = q.name or f"quest {q.id}"
    return f"{name} ({', '.join(detail)})" if detail else name


def _item(i: lookup.ItemRef) -> str:
    return i.name or f"item {i.id}"


def _hit_list(label: str, entries: list[str]) -> list[str]:
    """One line per entry, the label on each, at most `_SHOWN`."""
    lines = [f"{label} {e}" for e in entries[:_SHOWN]]
    if len(entries) > _SHOWN:
        lines.append(f"... and {len(entries) - _SHOWN} more ({label.lower()})")
    return lines


def _chance(d: lookup.DropSource) -> str:
    if not d.chances:
        return ""
    words = ", ".join(f"{v:g}%" for v in sorted(set(d.chances.values()), reverse=True))
    return f", {words} chance"


def _item_lines(s: lookup.ItemSources | None) -> list[str]:
    if s is None:
        return []
    lines: list[str] = []
    lines += _hit_list("Drops from", [_npc(d.npc) + _chance(d) for d in s.drops])
    objects = sorted(s.object_drops, key=lambda o: -_spots(o.places))
    lines += _hit_list("Found in the object", [_object(o) for o in objects])
    lines += _hit_list("Found in", [_item(i) for i in s.containers])
    lines += _hit_list("Reward of the quest", [_quest(q) for q in s.quest_rewards])
    vendors = sorted(s.vendors, key=lambda n: -_spots(n.places))
    lines += _hit_list("Sold by", [_npc(n, faction=True) for n in vendors])
    if s.starts_quest is not None:
        lines.append(f"Starts the quest {_quest(s.starts_quest)}")
    if not lines:
        if s.in_questiedb:
            lines.append("QuestieDB names no drop, vendor or quest for it.")
        else:
            lines.append("Only the game's item table knows it; QuestieDB has no entry for it.")
    return lines


def _npc_lines(d: lookup.NpcDetail | None) -> list[str]:
    if d is None:
        return []
    lines: list[str] = []
    n = d.npc
    about = [x for x in (n.subname, _levels(n.level_min, n.level_max)) if x]
    if n.friendly_to in _FACTIONS:
        about.append(f"friendly to {_FACTIONS[n.friendly_to]}")
    if about:
        text = "; ".join(about)
        lines.append(text[:1].upper() + text[1:])
    where = _places(n.places)
    lines.append(f"Found {where}" if where else "QuestieDB keeps no place for it.")
    lines += _hit_list("Gives the quest", [_quest(q) for q in d.starts])
    lines += _hit_list("Takes the quest", [_quest(q) for q in d.ends])
    return lines


def _object_lines(d: lookup.ObjectDetail | None) -> list[str]:
    if d is None:
        return []
    where = _places(d.object.places)
    lines = [f"Found {where}" if where else "QuestieDB keeps no place for it."]
    lines += _hit_list("Starts the quest", [_quest(q) for q in d.starts])
    lines += _hit_list("Ends the quest", [_quest(q) for q in d.ends])
    return lines


def _quest_lines(d: lookup.QuestDetail | None) -> list[str]:
    if d is None:
        return []
    q = d.quest
    about = [
        x
        for x in (
            f"Level {q.level}" if q.level else None,
            f"needs level {q.required_level}" if q.required_level else None,
            f"in {q.zone}" if q.zone else (f"in zone {q.zone_id}" if q.zone_id else None),
        )
        if x
    ]
    lines = ["; ".join(about)] if about else []
    lines += _hit_list("Given by", [_npc(n, faction=True) for n in d.started_by_npcs])
    lines += _hit_list("Starts at the object", [_object(o) for o in d.started_by_objects])
    lines += _hit_list("Starts from the item", [_item(i) for i in d.started_by_items])
    lines += _hit_list("Turned in to", [_npc(n, faction=True) for n in d.finished_by_npcs])
    lines += _hit_list("Turned in at the object", [_object(o) for o in d.finished_by_objects])
    lines += _hit_list("Rewards", [_item(i) for i in d.rewards])
    return lines


def _heading(hit: lookup.LookupHit) -> str:
    head = f"{_LOOKUP_KIND_WORDS[hit.kind]}: {hit.name} ({hit.id})"
    item = hit.item
    if item is not None:
        extra = []
        if item.item_level:
            extra.append(f"item level {item.item_level}")
        if item.required_level and item.required_level > 1:
            extra.append(f"needs level {item.required_level}")
        if extra:
            head += ", " + ", ".join(extra)
    return head


def _report_hit(hit: lookup.LookupHit) -> LookupHitReport:
    lines = (
        _item_lines(hit.item)
        + _npc_lines(hit.npc)
        + _quest_lines(hit.quest)
        + _object_lines(hit.object)
    )
    return LookupHitReport(**hit.model_dump(), heading=_heading(hit), lines=lines)


def _fetch(
    data: gamedata.GameData, version: str | None, index: lookup.LookupIndex
) -> dict[str, lookup.GameTableState]:
    """Download each lookup table not cached for `version`; what became of
    each one that was tried."""
    out: dict[str, lookup.GameTableState] = {}
    if version is None:
        return out
    try:
        missing = gamedata.missing_tables(lookup.LOOKUP_TABLES, version, cache_dir=data.cache_dir)
    except ValueError as exc:
        return {
            t: lookup.GameTableState(table=t, state="error", detail=str(exc))
            for t in lookup.LOOKUP_TABLES
        }
    stopped: str | None = None
    for table in missing:
        if stopped is not None:
            out[table] = lookup.GameTableState(table=table, state="error", detail=stopped)
            continue
        try:
            data.table(table, version)
        except (
            gamedata.SourceUnavailable,
            gamedata.CacheLocationError,
            gamedata.BuildNotPublished,
        ) as exc:
            stopped = str(exc)
            out[table] = lookup.GameTableState(table=table, state="error", detail=stopped)
        except (gamedata.GameDataError, OSError) as exc:
            out[table] = lookup.GameTableState(table=table, state="error", detail=str(exc))
        else:
            index.mark_fetched(table)
    return out


_NOTE_SOURCE = (
    "Where things are comes from QuestieDB, the addon installed for this game version, read as "
    "data: it says what Questie's authors recorded, which the game itself may not match."
)
_NOTE_CHANCE = (
    "Drop chances are shown only where QuestieDB's drop tables have one; those cover the items "
    "quests ask for."
)
_NOTE_POINTS = "Spots are map coordinates, as the in-game map shows them: across, then down."


def _notes(questie: lookup.QuestieState) -> list[str]:
    notes = [_NOTE_SOURCE, _NOTE_CHANCE, _NOTE_POINTS]
    notes += [f"Not read: {left.part}, because {left.why}." for left in questie.left_out]
    if questie.unreadable_rows:
        notes.append(
            f"{_plural(questie.unreadable_rows, 'entry')} of QuestieDB could not be read and "
            "were left out."
        )
    return notes


def _caveat(questie: lookup.QuestieState, tables: list[lookup.GameTableState]) -> str | None:
    if questie.state == "not_installed":
        return "QuestieDB is not installed for this game version, so only the game's item table was searched."
    if questie.state in ("not_baked", "unreadable"):
        why = f" ({questie.detail})" if questie.detail else ""
        return f"QuestieDB could not be read{why}, so only the game's item table was searched."
    if any(t.state == "missing" for t in tables):
        return (
            "The item and zone tables are not downloaded for this game version, so zones show "
            "as numbers: --fetch downloads them from wago.tools."
        )
    failed = [t for t in tables if t.state == "error"]
    if failed:
        return f"The {failed[0].table} table could not be read: {failed[0].detail}"
    return None


@app.command(
    "lookup", short_help="Where an item drops, who sells it, and where an NPC or quest is."
)
@_handled
def lookup_cmd(
    query: Annotated[
        str,
        typer.Argument(
            metavar="TEXT",
            help="A name, or part of one (case ignored), or an id.",
        ),
    ],
    kind: Annotated[
        Literal["item", "npc", "quest", "object"] | None,
        typer.Option("--kind", help="Only items, NPCs, quests or objects."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=100, help="How many matches to show."),
    ] = 10,
    fetch: Annotated[
        bool,
        typer.Option(
            "--fetch",
            help="First download the game's item and zone tables when they are not here yet, "
            "from wago.tools, sending your game version.",
        ),
    ] = False,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes: NotesOpt = False,
) -> None:
    """Where an item drops or which quest gives it, who sells it, and where
    an NPC, object or quest is: from the QuestieDB addon installed for this
    game version, read as data, and the game's item and zone tables when
    they are downloaded. Nothing is downloaded unless --fetch is given.

    \f
    JSON: LookupReport."""
    inst, _ = _discover(root)
    chosen = _select_flavor(inst, flavor)
    index = lookup.LookupIndex.for_flavor(chosen.path, chosen.version)
    fetched: dict[str, lookup.GameTableState] = {}
    if fetch:
        with _open_gamedata() as data:
            fetched = _fetch(data, chosen.version, index)
    kinds: list[lookup.SearchKind] = (
        [kind] if kind is not None else ["item", "npc", "quest", "object"]
    )
    hits, total = index.search(query, kinds=kinds, limit=limit)
    tables = [fetched.get(t.table, t) for t in index.game_tables()]
    questie = index.questie
    shown = [_report_hit(h) for h in hits]
    searched = questie.state == "read" or any(t.state in ("cached", "fetched") for t in tables[:1])
    asked = '"' + query.strip() + '"'
    if not query.strip():
        words = "Nothing to look up: give a name, part of one, or an id."
    elif not searched:
        words = (
            f"Nothing to search for {asked}: QuestieDB is not read and the game's item "
            "table is not downloaded for this game version."
        )
    elif total == 0:
        words = f"No match for {asked}."
    elif total > len(shown):
        best = "the best one is" if len(shown) == 1 else f"the best {len(shown)} are"
        words = f"{_matches(total)} for {asked}; {best} shown."
    else:
        words = f"{_matches(total)} for {asked}."
    report = LookupReport(
        query=query,
        kinds=kinds,
        flavor=chosen.folder,
        version=chosen.version,
        questiedb=questie,
        game_tables=tables,
        total=total,
        hits=shown,
        words=words,
        notes=_notes(questie),
    )
    if json_out:
        _emit(report)
    else:
        _say_prose(words)
        for hit in shown:
            _say("")
            _say_prose(hit.heading)
            for line in hit.lines:
                _say_prose(line, indent="  ", hang="    ")
        _say("")
        _say_notes(report.notes, notes, _caveat(questie, tables))
    if not searched or not query.strip():
        raise typer.Exit(EXIT_ERROR)
