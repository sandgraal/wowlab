"""lookup: where an item comes from, and where an NPC, object or quest is
(M22-08).

Two sources, read-only (L1) and local:

1. **QuestieDB**, the player's own installed copy, read as data by
   `questiedb` (nothing executed, L3): every item's drops, vendors, quest
   rewards and containers; every NPC's and object's places; every quest's
   givers, finishers and zone; drop chances where its drop tables have
   them.
2. **The game tables** cached for the flavor's exact version by `gamedata`
   (ADR-0022): `ItemSparse` (item names, item level, required level,
   quality) and `AreaTable` (zone names). This module only reads the cache
   (`gamedata.cached_rows`); `wowlab lookup --fetch` downloads a missing
   table through `gamedata`, the only network client.

`LookupIndex.search` matches names (ASCII case folded, Unicode case
folded): an exact name first, then a name that starts with the query, then
one with a word that does, then any that holds it; a query of digits also
matches that id. `LookupIndex.item_sources` is what the twink view (M22-06)
asks for each candidate item; `item_sources` opens an index and asks for
several at once.

Ids and field numbers are QuestieDB's (`questiedb`); nothing here names a
flavor (L6).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import gamedata, questiedb
from wowlab_core.questiedb import Kind

__all__ = [
    "LOOKUP_TABLES",
    "DropSource",
    "GameTableState",
    "ItemRef",
    "ItemSources",
    "LookupHit",
    "LookupIndex",
    "NpcDetail",
    "NpcRef",
    "ObjectDetail",
    "ObjectRef",
    "Place",
    "QuestDetail",
    "QuestRef",
    "QuestieState",
    "SearchKind",
    "item_sources",
]

LOOKUP_TABLES: tuple[str, ...] = ("ItemSparse", "AreaTable")
"""The game tables a lookup reads: item names and levels, zone names. Table
names, nothing about a flavor (L6)."""

SearchKind = Literal["item", "npc", "quest", "object"]
_KIND: dict[SearchKind, Kind] = {"item": "Item", "npc": "Npc", "quest": "Quest", "object": "Object"}
_ORDER: tuple[SearchKind, ...] = ("item", "npc", "quest", "object")
# How many points of each zone a place keeps; `count` says how many there are.
_POINTS = 5

# QuestieDB's field numbers (`src/meta/<kind>Meta.lua` in the addon).
_ITEM_NAME, _ITEM_NPC_DROPS, _ITEM_OBJECT_DROPS, _ITEM_ITEM_DROPS = 1, 2, 3, 4
_ITEM_START_QUEST, _ITEM_QUEST_REWARDS, _ITEM_LEVEL, _ITEM_REQUIRED_LEVEL = 5, 6, 9, 10
_ITEM_VENDORS = 14
_NPC_MIN_LEVEL, _NPC_MAX_LEVEL, _NPC_RANK, _NPC_SPAWNS, _NPC_ZONE = 4, 5, 6, 7, 9
_NPC_QUEST_STARTS, _NPC_QUEST_ENDS, _NPC_FRIENDLY, _NPC_SUBNAME = 10, 11, 13, 14
_QUEST_STARTED_BY, _QUEST_FINISHED_BY, _QUEST_REQUIRED_LEVEL, _QUEST_LEVEL = 2, 3, 4, 5
_QUEST_ZONE_OR_SORT = 17
_OBJECT_QUEST_STARTS, _OBJECT_QUEST_ENDS, _OBJECT_SPAWNS, _OBJECT_ZONE = 2, 3, 4, 5
# The groups of a quest's starters and finishers: NPCs, objects, items.
_BY_NPC, _BY_OBJECT, _BY_ITEM = 1, 2, 3


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Place(_Model):
    """Where something is in one zone: the zone's id (an AreaTable id), its
    name when the zone table is downloaded, how many points QuestieDB keeps
    there, and the first few as map percentages `[x, y]`."""

    zone_id: int
    zone: str | None = None
    count: int
    points: list[tuple[float, float]]


class ItemRef(_Model):
    id: int
    name: str | None = None


class QuestRef(_Model):
    id: int
    name: str | None = None
    level: int | None = None
    required_level: int | None = None
    zone_id: int | None = None
    zone: str | None = None


class NpcRef(_Model):
    """An NPC: name, title (`subname`, such as "Weapon Vendor"), levels,
    which factions it is friendly to (`A`, `H`, `AH`, or None for neither),
    and its places."""

    id: int
    name: str | None = None
    subname: str | None = None
    level_min: int | None = None
    level_max: int | None = None
    friendly_to: str | None = None
    places: list[Place] = []


class ObjectRef(_Model):
    id: int
    name: str | None = None
    places: list[Place] = []


class DropSource(_Model):
    """An NPC that drops the item, and the drop chance in percent from each
    of QuestieDB's drop tables that has one (`wowheadData`, `cmangosData`);
    empty when none does (they cover quest items only)."""

    npc: NpcRef
    chances: dict[str, float] = {}


class ItemSources(_Model):
    """Where one item comes from. `name`, `item_level`, `required_level` and
    `quality` (the game's quality id) come from QuestieDB or, where it has
    none, the item table; `in_questiedb` and `in_game_table` say which of
    the two know the item."""

    id: int
    name: str | None = None
    item_level: int | None = None
    required_level: int | None = None
    quality: int | None = None
    in_questiedb: bool = False
    in_game_table: bool = False
    drops: list[DropSource] = []
    object_drops: list[ObjectRef] = []
    containers: list[ItemRef] = []
    quest_rewards: list[QuestRef] = []
    vendors: list[NpcRef] = []
    starts_quest: QuestRef | None = None


class NpcDetail(_Model):
    npc: NpcRef
    rank: int | None = None
    starts: list[QuestRef] = []
    ends: list[QuestRef] = []


class ObjectDetail(_Model):
    object: ObjectRef
    starts: list[QuestRef] = []
    ends: list[QuestRef] = []


class QuestDetail(_Model):
    """A quest: where it is, who gives it and who takes it, and the items it
    rewards (the items whose QuestieDB row names it)."""

    quest: QuestRef
    started_by_npcs: list[NpcRef] = []
    started_by_objects: list[ObjectRef] = []
    started_by_items: list[ItemRef] = []
    finished_by_npcs: list[NpcRef] = []
    finished_by_objects: list[ObjectRef] = []
    rewards: list[ItemRef] = []


class LookupHit(_Model):
    """One match: its kind, id and name, which sources know it
    (`questiedb`, `game_table`), and the detail of its kind."""

    kind: SearchKind
    id: int
    name: str
    sources: list[Literal["questiedb", "game_table"]]
    item: ItemSources | None = None
    npc: NpcDetail | None = None
    quest: QuestDetail | None = None
    object: ObjectDetail | None = None


QuestieStateName = Literal["read", "not_installed", "not_baked", "unreadable"]


class QuestieState(_Model):
    """What became of QuestieDB: `read`; `not_installed` (no QuestieDB
    folder, or none with a TOC named after it); `not_baked` (its TOC holds
    no data store wowlab reads); `unreadable` (`detail` says why). `info`
    names the TOC read; `left_out` the parts not read; `unreadable_rows`
    counts entities whose stored data could not be decoded."""

    state: QuestieStateName
    info: questiedb.StoreInfo | None = None
    detail: str | None = None
    left_out: list[questiedb.LeftOut] = []
    unreadable_rows: int = 0


GameTableStateName = Literal["cached", "fetched", "missing", "error", "no_version"]


class GameTableState(_Model):
    """One game table: `cached` (read from the download cache), `fetched`
    (downloaded now), `missing` (not downloaded for this version),
    `error` (`detail` says why) or `no_version` (the flavor has no game
    version to key it by)."""

    table: str
    state: GameTableStateName
    detail: str | None = None


# ─── reading the shapes ──────────────────────────────────────────────────────


def _int(value: object) -> int | None:
    return value if type(value) is int else None


def _text(value: object) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value if isinstance(value, str) else None


def _ids(value: object) -> list[int]:
    if isinstance(value, list):
        return [v for v in value if type(v) is int]
    return []


def _numbered(value: object) -> dict[int, object]:
    """A Lua table with number keys, as CBOR gives it: an array is keys
    1..n (a null is a hole), a map keeps its integer keys."""
    if isinstance(value, list):
        return {i: v for i, v in enumerate(value, start=1) if v is not None}
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if type(k) is int and v is not None}
    return {}


def _point(value: object) -> tuple[float, float] | None:
    if isinstance(value, list) and len(value) >= 2:
        x, y = value[0], value[1]
        if type(x) in (int, float) and type(y) in (int, float):
            return (float(x), float(y))
    return None


class _Rows:
    """One QuestieDB store's rows, decoded once each."""

    def __init__(self, store: questiedb.QuestieStore) -> None:
        self.store = store
        self.rows: dict[Kind, dict[int, dict[object, object]]] = {}
        self.names: dict[Kind, dict[int, str]] = {}

    def load(self, kind: Kind) -> None:
        if kind in self.rows:
            return
        rows: dict[int, dict[object, object]] = {}
        names: dict[int, str] = {}
        for entity, name, row in self.store.names(kind):
            rows[entity] = row
            names[entity] = name
        self.rows[kind] = rows
        self.names[kind] = names

    def row(self, kind: Kind, entity: int) -> dict[object, object] | None:
        self.load(kind)
        return self.rows[kind].get(entity)

    def table(self, kind: Kind, entity: int, field: int) -> object:
        row = self.row(kind, entity)
        if row is None:
            return None
        return self.store.table(kind, entity, field, row)


# ─── the index ───────────────────────────────────────────────────────────────


class _GameTables:
    """The cached game tables for one version, read once each."""

    def __init__(self, version: str | None, cache_dir: Path | None) -> None:
        self.version = version
        self.cache_dir = cache_dir
        self.states: dict[str, GameTableState] = {}
        self._items: dict[int, tuple[str, int | None, int | None, int | None]] | None = None
        self._zones: dict[int, str] | None = None

    def _rows(self, table: str) -> Iterator[dict[str, str]] | None:
        if self.version is None:
            self.states[table] = GameTableState(table=table, state="no_version")
            return None
        try:
            rows = gamedata.cached_rows(table, self.version, cache_dir=self.cache_dir)
        except ValueError as exc:
            self.states[table] = GameTableState(table=table, state="error", detail=str(exc))
            return None
        if rows is None:
            self.states[table] = GameTableState(table=table, state="missing")
            return None
        self.states.setdefault(table, GameTableState(table=table, state="cached"))
        return rows

    def _failed(self, table: str, detail: str) -> None:
        self.states[table] = GameTableState(table=table, state="error", detail=detail)

    def items(self) -> dict[int, tuple[str, int | None, int | None, int | None]]:
        """Item id -> (name, item level, required level, quality)."""
        if self._items is not None:
            return self._items
        self._items = {}
        rows = self._rows("ItemSparse")
        if rows is None:
            return self._items
        try:
            for row in rows:
                entity = _number(row.get("ID"))
                name = row.get("Display_lang")
                if entity is None or not name:
                    continue
                self._items[entity] = (
                    name,
                    _number(row.get("ItemLevel")),
                    _number(row.get("RequiredLevel")),
                    _number(row.get("OverallQualityID")),
                )
        except (gamedata.MalformedTable, OSError) as exc:
            self._items = {}
            self._failed("ItemSparse", str(exc))
        return self._items

    def zones(self) -> dict[int, str]:
        """Area id -> zone name."""
        if self._zones is not None:
            return self._zones
        self._zones = {}
        rows = self._rows("AreaTable")
        if rows is None:
            return self._zones
        try:
            for row in rows:
                entity = _number(row.get("ID"))
                name = row.get("AreaName_lang")
                if entity is not None and name:
                    self._zones[entity] = name
        except (gamedata.MalformedTable, OSError) as exc:
            self._zones = {}
            self._failed("AreaTable", str(exc))
        return self._zones


def _number(text: str | None) -> int | None:
    if text is None:
        return None
    text = text.strip()
    if text.isascii() and text.lstrip("-").isdigit():
        return int(text)
    return None


def _fold(text: str) -> str:
    return text.casefold()


class LookupIndex:
    """Search and detail over QuestieDB and the game tables of one flavor.
    Build one with `LookupIndex.for_flavor`; every read is lazy and cached for the
    life of the index."""

    def __init__(
        self,
        store: questiedb.QuestieStore | None,
        questie: QuestieState,
        version: str | None,
        *,
        cache_dir: Path | None = None,
    ) -> None:
        self._rows = _Rows(store) if store is not None else None
        self._questie = questie
        self._game = _GameTables(version, cache_dir)
        self._drops: list[questiedb.DropTable] | None = None
        self._drop_left: list[questiedb.LeftOut] = []
        self._rewards: dict[int, list[int]] | None = None

    @classmethod
    def for_flavor(
        cls, flavor_path: Path, version: str | None, *, cache_dir: Path | None = None
    ) -> LookupIndex:
        """The index of one flavor folder: its QuestieDB (read-only) and the
        game tables cached for `version`. Never raises for a missing or
        unreadable QuestieDB; `questie` says what became of it."""
        folder, tocs = questiedb.find_toc(flavor_path / "Interface" / "AddOns")
        if folder is None or not tocs:
            return cls(None, QuestieState(state="not_installed"), version, cache_dir=cache_dir)
        store: questiedb.QuestieStore | None = None
        failure: str | None = None
        for path in tocs:
            try:
                candidate = questiedb.open_store(path)
            except questiedb.QuestieDBError as exc:
                failure = str(exc)
                continue
            if candidate.baked:
                store = candidate
                break
        if store is None:
            state: QuestieStateName = "unreadable" if failure else "not_baked"
            return cls(
                None, QuestieState(state=state, detail=failure), version, cache_dir=cache_dir
            )
        return cls(
            store,
            QuestieState(state="read", info=store.info(), left_out=store.left_out()),
            version,
            cache_dir=cache_dir,
        )

    # ── what was read ──

    @property
    def questie(self) -> QuestieState:
        """QuestieDB's state, with the parts left out and the rows that
        could not be read so far."""
        state = self._questie
        if self._rows is None:
            return state
        left = list(state.left_out) + self._drop_left
        unreadable = sum(self._rows.store.unreadable.values())
        return state.model_copy(update={"left_out": left, "unreadable_rows": unreadable})

    def game_tables(self) -> list[GameTableState]:
        """Each game table's state, for the ones read so far, in
        `LOOKUP_TABLES` order; a table not read yet is reported `missing` or
        `cached` from the cache alone."""
        out: list[GameTableState] = []
        for table in LOOKUP_TABLES:
            state = self._game.states.get(table)
            if state is None:
                rows = self._game._rows(table)
                state = self._game.states[table]
                del rows
            out.append(state)
        return out

    def mark_fetched(self, table: str) -> None:
        """Record that `table` was downloaded by this run."""
        self._game.states[table] = GameTableState(table=table, state="fetched")

    # ── QuestieDB helpers ──

    def _zone(self, zone_id: int | None) -> str | None:
        if zone_id is None or zone_id <= 0:
            return None
        return self._game.zones().get(zone_id)

    def _places(self, value: object) -> list[Place]:
        out: list[Place] = []
        for zone_id, points in _numbered(value).items():
            kept = [
                p for p in (_point(v) for v in (points if isinstance(points, list) else [])) if p
            ]
            out.append(
                Place(
                    zone_id=zone_id,
                    zone=self._zone(zone_id),
                    count=len(kept),
                    points=kept[:_POINTS],
                )
            )
        out.sort(key=lambda p: -p.count)
        return out

    def _name(self, kind: Kind, entity: int) -> str | None:
        if self._rows is None:
            return None
        self._rows.load(kind)
        return self._rows.names[kind].get(entity)

    def _quest_ref(self, entity: int) -> QuestRef:
        row = self._rows.row("Quest", entity) if self._rows is not None else None
        if row is None:
            return QuestRef(id=entity)
        zone = _int(row.get(_QUEST_ZONE_OR_SORT))
        zone_id = zone if zone is not None and zone > 0 else None
        return QuestRef(
            id=entity,
            name=_text(row.get(1)),
            level=_int(row.get(_QUEST_LEVEL)),
            required_level=_int(row.get(_QUEST_REQUIRED_LEVEL)),
            zone_id=zone_id,
            zone=self._zone(zone_id),
        )

    def _npc_ref(self, entity: int) -> NpcRef:
        if self._rows is None:
            return NpcRef(id=entity)
        row = self._rows.row("Npc", entity)
        if row is None:
            return NpcRef(id=entity)
        places = self._places(self._rows.table("Npc", entity, _NPC_SPAWNS))
        if not places:
            zone = _int(row.get(_NPC_ZONE))
            if zone is not None and zone > 0:
                places = [Place(zone_id=zone, zone=self._zone(zone), count=0, points=[])]
        return NpcRef(
            id=entity,
            name=_text(row.get(1)),
            subname=_text(row.get(_NPC_SUBNAME)),
            level_min=_int(row.get(_NPC_MIN_LEVEL)),
            level_max=_int(row.get(_NPC_MAX_LEVEL)),
            friendly_to=_text(row.get(_NPC_FRIENDLY)),
            places=places,
        )

    def _object_ref(self, entity: int) -> ObjectRef:
        if self._rows is None:
            return ObjectRef(id=entity)
        row = self._rows.row("Object", entity)
        if row is None:
            return ObjectRef(id=entity)
        places = self._places(self._rows.table("Object", entity, _OBJECT_SPAWNS))
        if not places:
            zone = _int(row.get(_OBJECT_ZONE))
            if zone is not None and zone > 0:
                places = [Place(zone_id=zone, zone=self._zone(zone), count=0, points=[])]
        return ObjectRef(id=entity, name=_text(row.get(1)), places=places)

    def _item_ref(self, entity: int) -> ItemRef:
        name = self._name("Item", entity)
        if name is None:
            known = self._game.items().get(entity)
            name = known[0] if known else None
        return ItemRef(id=entity, name=name)

    def drop_tables(self) -> list[questiedb.DropTable]:
        """QuestieDB's literal drop tables, read once."""
        if self._drops is None:
            if self._rows is None:
                self._drops = []
            else:
                self._drops, self._drop_left = questiedb.read_drop_tables(self._rows.store)
        return self._drops

    def _reward_index(self) -> dict[int, list[int]]:
        """Quest id -> the items whose row names it as a reward."""
        if self._rewards is not None:
            return self._rewards
        self._rewards = {}
        if self._rows is None:
            return self._rewards
        self._rows.load("Item")
        for entity in self._rows.rows["Item"]:
            for quest in _ids(self._rows.table("Item", entity, _ITEM_QUEST_REWARDS)):
                self._rewards.setdefault(quest, []).append(entity)
        return self._rewards

    # ── details ──

    def item_sources(self, item_id: int) -> ItemSources | None:
        """Where one item comes from, or None when neither source knows it."""
        known = self._game.items().get(item_id)
        row = self._rows.row("Item", item_id) if self._rows is not None else None
        if row is None and known is None:
            return None
        name = _text(row.get(_ITEM_NAME)) if row is not None else None
        out = ItemSources(
            id=item_id,
            name=name or (known[0] if known else None),
            item_level=(_int(row.get(_ITEM_LEVEL)) if row is not None else None)
            or (known[1] if known else None),
            required_level=(_int(row.get(_ITEM_REQUIRED_LEVEL)) if row is not None else None)
            or (known[2] if known else None),
            quality=known[3] if known else None,
            in_questiedb=row is not None,
            in_game_table=known is not None,
        )
        if row is None or self._rows is None:
            return out
        rows = self._rows

        def table(field: int) -> list[int]:
            return _ids(rows.table("Item", item_id, field))

        drops: list[DropSource] = []
        tables = self.drop_tables()
        for npc in table(_ITEM_NPC_DROPS):
            chances = {
                t.name: t.chances[item_id][npc] for t in tables if npc in t.chances.get(item_id, {})
            }
            drops.append(DropSource(npc=self._npc_ref(npc), chances=chances))
        drops.sort(
            key=lambda d: (-max(d.chances.values(), default=0.0), d.npc.name or "", d.npc.id)
        )
        start = _int(row.get(_ITEM_START_QUEST))
        return out.model_copy(
            update={
                "drops": drops,
                "object_drops": [self._object_ref(o) for o in table(_ITEM_OBJECT_DROPS)],
                "containers": [self._item_ref(i) for i in table(_ITEM_ITEM_DROPS)],
                "quest_rewards": [self._quest_ref(q) for q in table(_ITEM_QUEST_REWARDS)],
                "vendors": [self._npc_ref(n) for n in table(_ITEM_VENDORS)],
                "starts_quest": self._quest_ref(start) if start else None,
            }
        )

    def npc(self, npc_id: int) -> NpcDetail | None:
        if self._rows is None or self._rows.row("Npc", npc_id) is None:
            return None
        row = self._rows.row("Npc", npc_id) or {}
        starts = _ids(self._rows.table("Npc", npc_id, _NPC_QUEST_STARTS))
        ends = _ids(self._rows.table("Npc", npc_id, _NPC_QUEST_ENDS))
        return NpcDetail(
            npc=self._npc_ref(npc_id),
            rank=_int(row.get(_NPC_RANK)),
            starts=[self._quest_ref(q) for q in starts],
            ends=[self._quest_ref(q) for q in ends],
        )

    def object(self, object_id: int) -> ObjectDetail | None:
        if self._rows is None or self._rows.row("Object", object_id) is None:
            return None
        starts = _ids(self._rows.table("Object", object_id, _OBJECT_QUEST_STARTS))
        ends = _ids(self._rows.table("Object", object_id, _OBJECT_QUEST_ENDS))
        return ObjectDetail(
            object=self._object_ref(object_id),
            starts=[self._quest_ref(q) for q in starts],
            ends=[self._quest_ref(q) for q in ends],
        )

    def quest(self, quest_id: int) -> QuestDetail | None:
        if self._rows is None or self._rows.row("Quest", quest_id) is None:
            return None
        started = _numbered(self._rows.table("Quest", quest_id, _QUEST_STARTED_BY))
        finished = _numbered(self._rows.table("Quest", quest_id, _QUEST_FINISHED_BY))
        return QuestDetail(
            quest=self._quest_ref(quest_id),
            started_by_npcs=[self._npc_ref(n) for n in _ids(started.get(_BY_NPC))],
            started_by_objects=[self._object_ref(o) for o in _ids(started.get(_BY_OBJECT))],
            started_by_items=[self._item_ref(i) for i in _ids(started.get(_BY_ITEM))],
            finished_by_npcs=[self._npc_ref(n) for n in _ids(finished.get(_BY_NPC))],
            finished_by_objects=[self._object_ref(o) for o in _ids(finished.get(_BY_OBJECT))],
            rewards=[self._item_ref(i) for i in self._reward_index().get(quest_id, [])],
        )

    # ── search ──

    def _candidates(self, kind: SearchKind) -> Iterator[tuple[int, str, bool, bool]]:
        """`(id, name, in QuestieDB, in the game table)` for every entity of
        `kind` with a name."""
        questie: dict[int, str] = {}
        if self._rows is not None:
            self._rows.load(_KIND[kind])
            questie = self._rows.names[_KIND[kind]]
        game: dict[int, tuple[str, int | None, int | None, int | None]] = {}
        if kind == "item":
            game = self._game.items()
        for entity, name in questie.items():
            yield entity, name, True, entity in game
        for entity, row in game.items():
            if entity not in questie:
                yield entity, row[0], False, True

    def search(
        self, query: str, *, kinds: Sequence[SearchKind] = _ORDER, limit: int = 10
    ) -> tuple[list[LookupHit], int]:
        """The best `limit` matches for `query` among `kinds`, each with its
        detail, and how many matched in all."""
        wanted = _fold(query.strip())
        if not wanted:
            return [], 0
        number = int(wanted) if wanted.isascii() and wanted.isdigit() else None
        ranked: list[tuple[int, int, int, str, int, SearchKind, str, bool, bool]] = []
        for order, kind in enumerate(k for k in _ORDER if k in kinds):
            for entity, name, in_q, in_g in self._candidates(kind):
                folded = _fold(name)
                if number is not None and entity == number:
                    rank = 0
                elif folded == wanted:
                    rank = 1
                elif folded.startswith(wanted):
                    rank = 2
                elif f" {wanted}" in f" {folded}":
                    rank = 3
                elif wanted in folded:
                    rank = 4
                else:
                    continue
                ranked.append((rank, order, len(name), folded, entity, kind, name, in_q, in_g))
        ranked.sort()
        hits = [self._hit(r[5], r[4], r[6], r[7], r[8]) for r in ranked[: max(limit, 0)]]
        return hits, len(ranked)

    def _hit(self, kind: SearchKind, entity: int, name: str, in_q: bool, in_g: bool) -> LookupHit:
        sources: list[Literal["questiedb", "game_table"]] = []
        if in_q:
            sources.append("questiedb")
        if in_g:
            sources.append("game_table")
        hit = LookupHit(kind=kind, id=entity, name=name, sources=sources)
        if kind == "item":
            return hit.model_copy(update={"item": self.item_sources(entity)})
        if kind == "npc":
            return hit.model_copy(update={"npc": self.npc(entity)})
        if kind == "quest":
            return hit.model_copy(update={"quest": self.quest(entity)})
        return hit.model_copy(update={"object": self.object(entity)})


def item_sources(
    flavor_path: Path,
    version: str | None,
    item_ids: Iterable[int],
    *,
    cache_dir: Path | None = None,
) -> dict[int, ItemSources]:
    """Where each of `item_ids` comes from, for one flavor folder and its
    game version (the twink view's call, M22-06). An item neither source
    knows is left out of the result."""
    index = LookupIndex.for_flavor(flavor_path, version, cache_dir=cache_dir)
    out: dict[int, ItemSources] = {}
    for item in item_ids:
        found = index.item_sources(item)
        if found is not None:
            out[item] = found
    return out
