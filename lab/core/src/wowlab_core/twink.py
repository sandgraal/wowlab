"""twink: the gap between what a character wears and the best gear it could
wear in a battleground level bracket (M22-06).

Reads nothing from an install itself. It takes the `gear` section of a
record `labaddon.read_char` already parsed (through `gear.rows`), the
character's class, race and level, and the game tables of one build, read
through `gamedata` (ADR-0022: keyed by the full build string, which the
caller gives; L6). What `wowlab char twink` prints and its `--json`
(`cli.CharTwinkReport`) are built from these models.

The rules, each **[verify]** on Forever:

- **Candidates.** An item of `Item` with an `ItemSparse` row, armour
  (ClassID 4) or a weapon (ClassID 2) of a type the class can use
  (`twink.toml`, `[classes]`), whose `AllowableClass` and `AllowableRace`
  masks admit the character (bit `id - 1`; -1 and 0 admit everyone), with a
  `RequiredLevel` at or below the bracket's cap, uncommon, rare or epic
  (`OverallQualityID` 2 to 4: only those have a stat budget), and with a
  name, that is not a test or retired item by its name (`[filters]`), whose
  `MinFactionID` faction does not start the character's race at Hated
  (`Faction.ReputationRaceMasks<n>` and `ReputationBase_<n>`: the other
  side's battleground factions), and that is not an item with no required
  level and an item level more than `no_level_margin` above the cap (most
  are raid or dungeon drops out of reach at the cap).
  About half of the armour and weapons in `Item` have no `ItemSparse`
  row in wago.tools' export (at 70888 and 71037: 11231 of 19079; why is not
  known), so they cannot be candidates; the report counts them.
- **Stats.** The game tables hold no stat amounts, only each stat's share
  of the item's budget (`StatPercentEditor`, per 10000). The amount is that
  share of `RandPropPoints` at the item's level, in the column of its
  quality (`Good`, `Superior`, `Epic`) and of its slot type (0 to 4, by
  weapon type or armour slot), rounded half away from zero. A caster weapon
  (`Flags_4` bit 0x200) adds spell power, 20000 per 10000 of column 0; a
  healer's (0x400) 37600 healing and a third of that as spell damage. These
  rules are WoWSims' Forever sim's (wowsims/forever, MIT, commit
  57df2a7a5a2bb085bfce15890a33d655bb7fd66f, `tools/database/dbc/item.go`),
  which reports that the caster rule reproduces the in-game tooltip.
- **Random suffixes.** On Forever a random suffix is a bonus list (GLOSSARY,
  "Random suffix"). An item's suffixes are the bonus lists of its bonus tree
  (`ItemXBonusTree` → `ItemBonusTreeNode`, item context 0) that carry stat
  rows (`ItemBonus` type 2: `Value_0` the stat, `Value_1` its share per
  10000 of the item's budget) and a name (type 5: `Value_0` an
  `ItemNameDescription` row), WoWSims' rule. Each suffix is scored on its
  own; a candidate is the item with its best suffix, the others listed.
- **Worn items.** Each filled slot's item is scored the same way, with the
  link's bonus id that is a suffix list as its suffix. An item without an
  `ItemSparse` row is listed unscored.
- **Score.** The sum of each stat's amount times its weight (`twink.toml`,
  `[weights.<class>.<set>]`; a stat without a weight counts 0). Weapon
  damage and armour are not scored.
- **Sources.** Where an item comes from (a quest, a drop) is M22-08's
  index; until it lands, `item_sources` knows none.

The configuration is overridable as `gear_slots.toml` is: a `twink.toml` in
wowlab's user config folder, read through `snapshot.read_regular_file`,
replaces each value it sets.
"""

from __future__ import annotations

import math
import re
import tomllib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit

import platformdirs
from pydantic import BaseModel, ConfigDict

from wowlab_core import dressing, gear, snapshot

__all__ = [
    "CONFIG_FILE",
    "TABLES",
    "ClassGear",
    "Planner",
    "TwinkCandidate",
    "TwinkConfig",
    "TwinkConfigError",
    "TwinkConfigSource",
    "TwinkError",
    "TwinkPanel",
    "TwinkSlot",
    "TwinkStat",
    "TwinkSuffix",
    "TwinkTables",
    "TwinkWorn",
    "build_panel",
    "default_bracket",
    "item_sources",
    "load_config",
    "load_tables",
    "parse_config",
    "planner_link",
    "shipped_config",
    "shipped_config_path",
    "user_config_path",
    "wowhead_link",
]

CONFIG_FILE = "twink.toml"
CONFIG_LIMIT = 1024 * 1024  # the shipped file is about 8 KiB; a bigger one is refused unread
TABLES: tuple[str, ...] = (
    "Item",
    "ItemSparse",
    "RandPropPoints",
    "ItemXBonusTree",
    "ItemBonusTreeNode",
    "ItemBonus",
    "ItemNameDescription",
    "Faction",
)
"""The game tables the panel is read from, by name (L6: table names only)."""

TOP_DEFAULT = 5
TOP_MAX = 20
LEVEL_MAX = 1000
_SECTIONS = ("brackets", "stats", "slots", "filters", "classes", "weights", "planner")
_SPLIT = re.compile(r"[\s,:()]+")
_SKIP = re.compile(r"[\x21-\x7e]{1,32}")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,47}")
_CLASS = re.compile(r"[A-Z]{1,32}")
_WORD = re.compile(r"[a-z0-9-]{1,32}")
_PATH = re.compile(r"(?:/[a-z0-9-]{1,64}){1,4}")
_ID_MAX = 2_147_483_647
_WEIGHT_MAX = 1000.0

# ─── the game's rules (WoWSims' Forever sim, module docstring; [verify]) ─────

_ITEM_CLASS_WEAPON = 2
_ITEM_CLASS_ARMOR = 4
_QUALITY_COLUMN: Mapping[int, str] = {2: "Good", 3: "Superior", 4: "Epic"}
_BONUS_STAT = 2  # ItemBonus.Type: Value_0 the stat, Value_1 its share per 10000
_BONUS_SUFFIX = 5  # ItemBonus.Type: Value_0 the ItemNameDescription of the name's end
_CASTER_SPELL_POWER = 0x200  # ItemSparse.Flags_4
_CASTER_HEALING = 0x400
_CASTER_SPELL_POWER_SHARE = 20000
_CASTER_HEALING_SHARE = 37600
_STAT_HEALING, _STAT_SPELL_DAMAGE, _STAT_SPELL_POWER = 41, 42, 45
_STATS_PER_ITEM = 10  # StatModifier_bonusStat_0..9
# Weapon subclasses whose budget is column 0 (two-handed) or 4 (ranged).
_WEAPON_TWO_HAND = frozenset({1, 5, 6, 8, 10})
_WEAPON_RANGED = frozenset({2, 3, 16, 18, 19})
# Armour inventory types by budget column.
_ARMOR_COLUMN: Mapping[int, int] = {
    **dict.fromkeys((1, 5, 7, 20), 0),  # head, chest, legs, robe
    **dict.fromkeys((3, 6, 8, 10, 12), 1),  # shoulders, waist, feet, hands, trinket
    **dict.fromkeys((2, 22, 23, 11, 16, 9, 14, 28), 2),  # neck, off hand, finger, back, ...
}
_TWO_HAND_TYPE = 17  # Item.InventoryType of a two-handed weapon
_FACTION_SIDES = 4  # Faction.ReputationRaceMasks0..3 and ReputationBase_0..3
_HATED = -42000  # the lowest standing: a race that starts there cannot earn the faction's


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TwinkError(ValueError):
    """The panel cannot be built: a table that cannot be read, or a class the
    configuration does not know."""


class TwinkConfigError(ValueError):
    """A `twink.toml` that cannot be used, with the file it came from."""


# ─── the configuration ───────────────────────────────────────────────────────


class ClassGear(_Model):
    armor: list[int]  # Item.SubclassID of armour (ClassID 4)
    weapons: list[int]  # Item.SubclassID of weapons (ClassID 2)


class Planner(_Model):
    origin: str
    path: str
    classes: dict[str, str]  # class token: the planner's word
    races: dict[int, str]  # race id: the planner's word


class Filters(_Model):
    skip_words: list[str]  # case-folded whole words of a test or retired item's name
    skip_prefixes: list[str]  # case-sensitive word prefixes, likewise
    no_level_margin: int  # an item with no required level above cap + this is left out


class TwinkConfig(_Model):
    brackets: list[int]  # each bracket's top level, ascending
    stats: dict[int, str]  # stat id: its name
    slots: dict[int, list[int]]  # Item.InventoryType: equipment slots
    filters: Filters
    classes: dict[str, ClassGear]
    weights: dict[str, dict[str, dict[str, float]]]  # class: set: stat name: weight
    planner: Planner


class TwinkConfigSource(_Model):
    """The configuration in effect and where it came from."""

    source: Literal["default", "user"]
    shipped: str
    user_file: str
    config: TwinkConfig


def shipped_config_path() -> str:
    return str(resources.files("wowlab_core").joinpath(CONFIG_FILE))


def user_config_path() -> Path:
    return platformdirs.user_config_path("wowlab") / CONFIG_FILE


def _table(where: str, value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TwinkConfigError(f"{where} must be a table")
    return value


def _number_key(where: str, key: str, top: int = _ID_MAX) -> int:
    digits = key.lstrip("0")
    if not key.isascii() or not key.isdigit() or len(digits) > len(str(top)):
        raise TwinkConfigError(f"{where}: {key!r} is not a whole number from 0 to {top}")
    number = int(digits or "0")
    if number > top:
        raise TwinkConfigError(f"{where}: {key!r} is not a whole number from 0 to {top}")
    return number


def _ints(where: str, value: object, low: int, high: int) -> list[int]:
    if not isinstance(value, list) or not value or len(value) > 64:
        raise TwinkConfigError(f"{where} must be a list of 1 to 64 whole numbers")
    out: list[int] = []
    for item in value:
        if type(item) is not int or not low <= item <= high:
            raise TwinkConfigError(f"{where}: {item!r} is not a whole number from {low} to {high}")
        if item in out:
            raise TwinkConfigError(f"{where}: {item} is listed twice")
        out.append(item)
    return out


def _word(where: str, value: object, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise TwinkConfigError(f"{where}: {value!r} is not allowed here")
    return value


def _weights(where: str, value: object, names: set[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for stat, weight in _table(where, value).items():
        if stat not in names:
            raise TwinkConfigError(f"{where}: {stat!r} is not a stat name of [stats]")
        if type(weight) not in (int, float) or not math.isfinite(weight):
            raise TwinkConfigError(f"{where}.{stat}: {weight!r} is not a number")
        if abs(weight) > _WEIGHT_MAX:
            raise TwinkConfigError(f"{where}.{stat}: {weight!r} is beyond ±{_WEIGHT_MAX:g}")
        out[stat] = float(weight)
    return out


def _words(where: str, value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 256:
        raise TwinkConfigError(f"{where} must be a list of at most 256 words")
    return [_word(where, w, _SKIP) for w in value]


def _filters(data: dict[str, Any], base: Filters | None) -> Filters:
    unknown = sorted(set(data) - {"skip_words", "skip_prefixes", "no_level_margin"})
    if unknown:
        raise TwinkConfigError(f"[filters]: unknown key {', '.join(unknown)}")
    words = base.skip_words if base is not None else []
    prefixes = base.skip_prefixes if base is not None else []
    margin = base.no_level_margin if base is not None else None
    if "skip_words" in data:
        words = [w.casefold() for w in _words("[filters] skip_words", data["skip_words"])]
    if "skip_prefixes" in data:
        prefixes = _words("[filters] skip_prefixes", data["skip_prefixes"])
    if "no_level_margin" in data:
        margin = data["no_level_margin"]
        if type(margin) is not int or not 0 <= margin <= LEVEL_MAX:
            raise TwinkConfigError(
                f"[filters] no_level_margin: {margin!r} is not a whole number from 0 to {LEVEL_MAX}"
            )
    if margin is None:
        raise TwinkConfigError("[filters] no_level_margin is missing")
    return Filters(skip_words=words, skip_prefixes=prefixes, no_level_margin=margin)


def _planner(data: dict[str, Any], base: Planner | None) -> Planner:
    origin = base.origin if base is not None else None
    path = base.path if base is not None else None
    classes = dict(base.classes) if base is not None else {}
    races = dict(base.races) if base is not None else {}
    unknown = sorted(set(data) - {"origin", "path", "classes", "races"})
    if unknown:
        raise TwinkConfigError(f"[planner]: unknown key {', '.join(unknown)}")
    if "origin" in data:
        origin = data["origin"]
        parts = urlsplit(origin) if isinstance(origin, str) else None
        if (
            parts is None
            or parts.scheme != "https"
            or not parts.hostname
            or f"https://{parts.hostname}" != origin
        ):
            raise TwinkConfigError(f"[planner] origin: {origin!r} is not https://<host>")
    if "path" in data:
        path = _word("[planner] path", data["path"], _PATH)
    for key, word in _table("[planner.classes]", data.get("classes", {})).items():
        classes[_word("[planner.classes]", key, _CLASS)] = _word(
            f"[planner.classes] {key}", word, _WORD
        )
    for key, word in _table("[planner.races]", data.get("races", {})).items():
        races[_number_key("[planner.races]", key, LEVEL_MAX)] = _word(
            f"[planner.races] {key}", word, _WORD
        )
    if not isinstance(origin, str) or not isinstance(path, str):
        raise TwinkConfigError("[planner] needs an origin and a path")
    return Planner(origin=origin, path=path, classes=classes, races=races)


def parse_config(text: str, base: TwinkConfig | None = None) -> TwinkConfig:
    """A configuration from TOML text; with `base`, each value given replaces
    `base`'s (a weight set as a whole) and the rest keep theirs. Raises
    `TwinkConfigError`."""
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError) as exc:
        raise TwinkConfigError(f"not valid TOML: {exc}") from exc
    unknown = sorted(set(data) - set(_SECTIONS))
    if unknown:
        raise TwinkConfigError(
            f"unknown table or key {', '.join(unknown)} (known: {', '.join(_SECTIONS)})"
        )
    brackets = list(base.brackets) if base is not None else []
    stats = dict(base.stats) if base is not None else {}
    slots = {k: list(v) for k, v in base.slots.items()} if base is not None else {}
    classes = dict(base.classes) if base is not None else {}
    weights = {k: dict(v) for k, v in base.weights.items()} if base is not None else {}
    part = _table("[brackets]", data.get("brackets", {}))
    if set(part) - {"caps"}:
        raise TwinkConfigError("[brackets]: the one key is caps")
    if "caps" in part:
        brackets = sorted(_ints("[brackets] caps", part["caps"], 1, LEVEL_MAX))
    for key, name in _table("[stats]", data.get("stats", {})).items():
        stats[_number_key("[stats]", key)] = _word(f"[stats] {key}", name, _NAME)
    if len(set(stats.values())) != len(stats):
        raise TwinkConfigError("[stats]: one name is given to two stats")
    for key, value in _table("[slots]", data.get("slots", {})).items():
        slots[_number_key("[slots]", key, 1000)] = _ints(f"[slots] {key}", value, 1, gear.SLOT_MAX)
    for key, value in _table("[classes]", data.get("classes", {})).items():
        token = _word("[classes]", key, _CLASS)
        table = _table(f"[classes.{key}]", value)
        if set(table) != {"armor", "weapons"}:
            raise TwinkConfigError(f"[classes.{key}] needs armor and weapons, and nothing else")
        classes[token] = ClassGear(
            armor=_ints(f"[classes.{key}] armor", table["armor"], 0, 1000),
            weapons=_ints(f"[classes.{key}] weapons", table["weapons"], 0, 1000),
        )
    names = set(stats.values())
    for key, sets in _table("[weights]", data.get("weights", {})).items():
        token = _word("[weights]", key, _CLASS)
        mine = weights.setdefault(token, {})
        for set_name, value in _table(f"[weights.{key}]", sets).items():
            _word(f"[weights.{key}]", set_name, _NAME)
            mine[set_name] = _weights(f"[weights.{key}.{set_name}]", value, names)
    for token, sets in weights.items():
        for set_name, table in sets.items():
            missing = sorted(set(table) - names)
            if missing:
                raise TwinkConfigError(
                    f"[weights.{token}.{set_name}]: {', '.join(missing)} is not a stat name"
                )
    planner = _planner(
        _table("[planner]", data.get("planner", {})), base.planner if base is not None else None
    )
    filters = _filters(
        _table("[filters]", data.get("filters", {})), base.filters if base is not None else None
    )
    if not brackets:
        raise TwinkConfigError("[brackets] caps is missing")
    return TwinkConfig(
        brackets=brackets,
        stats=dict(sorted(stats.items())),
        slots=dict(sorted(slots.items())),
        filters=filters,
        classes=classes,
        weights=weights,
        planner=planner,
    )


def shipped_config() -> TwinkConfig:
    """The configuration shipped with the package. Raises `TwinkConfigError`
    naming the file (a packaging fault, not the player's)."""
    text = resources.files("wowlab_core").joinpath(CONFIG_FILE).read_text(encoding="utf-8")
    try:
        return parse_config(text)
    except TwinkConfigError as exc:
        raise TwinkConfigError(f"{shipped_config_path()}: {exc}") from exc


def load_config() -> TwinkConfigSource:
    """The shipped configuration, with the player's own file over it when
    there is one. Raises `TwinkConfigError` naming the file at fault."""
    shipped = shipped_config_path()
    config = shipped_config()
    user = user_config_path()
    if not user.exists() and not user.is_symlink():
        return TwinkConfigSource(
            source="default", shipped=shipped, user_file=str(user), config=config
        )
    try:
        data = snapshot.read_regular_file(user, limit=CONFIG_LIMIT)
        mine = parse_config(data.decode("utf-8-sig"), config)
    except (OSError, snapshot.UnsafeReadError, UnicodeDecodeError, TwinkConfigError) as exc:
        raise TwinkConfigError(f"{user}: {exc}") from exc
    return TwinkConfigSource(source="user", shipped=shipped, user_file=str(user), config=mine)


# ─── the tables ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Item:
    id: int
    name: str
    class_id: int
    subclass_id: int
    inventory_type: int
    item_level: int
    required_level: int
    quality: int
    allowable_class: int
    allowable_race: int
    shares: tuple[tuple[int, int], ...]  # (stat, share per 10000) of the item's own stats
    flags_4: int
    min_faction: int  # ItemSparse.MinFactionID: the faction whose standing it needs, or 0


@dataclass(frozen=True, slots=True)
class _Suffix:
    id: int
    name: str | None
    shares: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class TwinkTables:
    """What the panel needs of the game tables of one build."""

    build: str
    items: Mapping[int, _Item]
    gear_without_details: int  # armour and weapons in Item with no ItemSparse row
    gear_in_item: int  # armour and weapons in Item
    budgets: Mapping[int, Mapping[str, tuple[int, ...]]]  # item level: quality column: 5 values
    suffix_options: Mapping[int, tuple[int, ...]]  # item id: its suffix bonus lists
    suffixes: Mapping[int, _Suffix]  # bonus list id: the suffix
    factions: Mapping[int, tuple[tuple[int, int], ...]]  # faction: (race mask, base standing)


def _int(table: str, row: Mapping[str, str], column: str) -> int:
    text = row.get(column)
    try:
        if text is None:
            raise ValueError
        return int(text)
    except ValueError:
        raise TwinkError(
            f"the game table {table} has a row ({row.get('ID', '?')}) whose {column} is "
            f"{text!r}, not a whole number"
        ) from None


def load_tables(build: str, rows: Callable[[str], Iterable[Mapping[str, str]]]) -> TwinkTables:
    """The panel's view of the tables `rows` gives by name (`TABLES`), for
    `build`. Raises `TwinkError` for a cell that is not a number."""
    kinds: dict[int, tuple[int, int, int]] = {}
    for row in rows("Item"):
        kinds[_int("Item", row, "ID")] = (
            _int("Item", row, "ClassID"),
            _int("Item", row, "SubclassID"),
            _int("Item", row, "InventoryType"),
        )
    items: dict[int, _Item] = {}
    for row in rows("ItemSparse"):
        item_id = _int("ItemSparse", row, "ID")
        kind = kinds.get(item_id)
        if kind is None or kind[0] not in (_ITEM_CLASS_WEAPON, _ITEM_CLASS_ARMOR):
            continue
        shares = []
        for n in range(_STATS_PER_ITEM):
            stat = _int("ItemSparse", row, f"StatModifier_bonusStat_{n}")
            share = _int("ItemSparse", row, f"StatPercentEditor_{n}")
            if stat >= 0 and share > 0:
                shares.append((stat, share))
        low = _int("ItemSparse", row, "AllowableRace_0")
        high = _int("ItemSparse", row, "AllowableRace_1")
        items[item_id] = _Item(
            id=item_id,
            name=(row.get("Display_lang") or "").strip(),
            class_id=kind[0],
            subclass_id=kind[1],
            inventory_type=kind[2],
            item_level=_int("ItemSparse", row, "ItemLevel"),
            required_level=_int("ItemSparse", row, "RequiredLevel"),
            quality=_int("ItemSparse", row, "OverallQualityID"),
            allowable_class=_int("ItemSparse", row, "AllowableClass"),
            allowable_race=-1 if low == -1 and high == -1 else ((high << 32) | (low & 0xFFFFFFFF)),
            shares=tuple(shares),
            flags_4=_int("ItemSparse", row, "Flags_4"),
            min_faction=_int("ItemSparse", row, "MinFactionID"),
        )
    gear_ids = [i for i, k in kinds.items() if k[0] in (_ITEM_CLASS_WEAPON, _ITEM_CLASS_ARMOR)]
    budgets: dict[int, dict[str, tuple[int, ...]]] = {}
    for row in rows("RandPropPoints"):
        budgets[_int("RandPropPoints", row, "ID")] = {
            column: tuple(_int("RandPropPoints", row, f"{column}_{n}") for n in range(5))
            for column in _QUALITY_COLUMN.values()
        }
    factions: dict[int, tuple[tuple[int, int], ...]] = {}
    for row in rows("Faction"):
        sides = []
        for n in range(_FACTION_SIDES):
            low = _int("Faction", row, f"ReputationRaceMasks{n}_0")
            high = _int("Faction", row, f"ReputationRaceMasks{n}_1")
            mask = (high << 32) | (low & 0xFFFFFFFF)
            if mask:
                sides.append((mask, _int("Faction", row, f"ReputationBase_{n}")))
        factions[_int("Faction", row, "ID")] = tuple(sides)
    names = {
        _int("ItemNameDescription", row, "ID"): (row.get("Description_lang") or "").strip()
        for row in rows("ItemNameDescription")
    }
    lists: dict[int, list[tuple[int, int, int, int]]] = {}
    for row in rows("ItemBonus"):
        lists.setdefault(_int("ItemBonus", row, "ParentItemBonusListID"), []).append(
            (
                _int("ItemBonus", row, "OrderIndex"),
                _int("ItemBonus", row, "Type"),
                _int("ItemBonus", row, "Value_0"),
                _int("ItemBonus", row, "Value_1"),
            )
        )
    suffixes: dict[int, _Suffix] = {}
    for list_id, entries in lists.items():
        entries.sort()
        stat_rows = [(v0, v1) for _, kind, v0, v1 in entries if kind == _BONUS_STAT]
        name_rows = [v0 for _, kind, v0, _ in entries if kind == _BONUS_SUFFIX]
        if stat_rows and name_rows:
            suffixes[list_id] = _Suffix(
                id=list_id, name=names.get(name_rows[0]) or None, shares=tuple(stat_rows)
            )
    nodes: dict[int, list[int]] = {}
    for row in rows("ItemBonusTreeNode"):
        child = _int("ItemBonusTreeNode", row, "ChildItemBonusListID")
        if _int("ItemBonusTreeNode", row, "ItemContext") == 0 and child in suffixes:
            nodes.setdefault(_int("ItemBonusTreeNode", row, "ParentItemBonusTreeID"), []).append(
                child
            )
    options: dict[int, list[int]] = {}
    for row in rows("ItemXBonusTree"):
        mine = options.setdefault(_int("ItemXBonusTree", row, "ItemID"), [])
        for child in nodes.get(_int("ItemXBonusTree", row, "ItemBonusTreeID"), []):
            if child not in mine:
                mine.append(child)
    return TwinkTables(
        build=build,
        items=items,
        gear_without_details=sum(1 for i in gear_ids if i not in items),
        gear_in_item=len(gear_ids),
        budgets=budgets,
        suffix_options={k: tuple(sorted(v)) for k, v in options.items() if v},
        suffixes=suffixes,
        factions=factions,
    )


# ─── scoring ─────────────────────────────────────────────────────────────────


class TwinkStat(_Model):
    stat: int  # the client's stat number
    name: str  # its name in twink.toml, else "stat <n>"
    amount: int


class TwinkSuffix(_Model):
    id: int  # the bonus list id
    name: str | None  # "of the Owl", from ItemNameDescription
    score: float
    stats: list[TwinkStat]  # the suffix's own stats


class TwinkCandidate(_Model):
    item_id: int
    name: str  # ItemSparse.Display_lang, without the suffix
    quality: int  # ItemSparse.OverallQualityID
    item_level: int
    required_level: int
    inventory_type: int
    two_hand: bool  # it also takes the off hand
    suffix: TwinkSuffix | None  # its best suffix, when it has suffixes
    other_suffixes: list[TwinkSuffix]  # every other suffix, best first
    score: float
    stats: list[TwinkStat]  # the item's own and its suffix's, summed per stat
    wowhead: str | None
    source: str | None  # where it comes from (M22-08); None: not known yet
    worn: bool  # the character wears this item, with this suffix, in this slot


class TwinkWorn(_Model):
    item_id: int | None
    name: str  # the link's bracketed text
    suffix: TwinkSuffix | None
    score: float | None  # None: not scored, `unscored` says why
    unscored: str | None
    stats: list[TwinkStat]
    required_level: int | None
    above_cap: bool  # its required level is above the bracket's cap
    wowhead: str | None


class TwinkSlot(_Model):
    slot: int
    slot_name: str | None
    worn: TwinkWorn | None  # None: the slot is empty
    candidates: list[TwinkCandidate]
    gap: float | None  # the best candidate's score less the worn item's
    gap_words: str


class TwinkPanel(_Model):
    """The twink panel for one character and bracket."""

    bracket: int  # the bracket's top level
    brackets: list[int]
    bracket_from: Literal["asked", "level", "first", "last"]
    level: int | None
    class_file: str
    race_id: int | None
    weights_name: str
    weight_sets: list[str]
    weights: dict[str, float]
    weights_source: Literal["default", "user"]
    tables_build: str
    slots: list[TwinkSlot]
    planner: str | None
    planner_absent: str | None
    gear_without_details: int
    gear_in_item: int
    no_level_left_out: int  # items with no required level, too far above the cap
    other_side_left_out: int  # items needing standing the character's race cannot earn
    words: str  # the one line that opens the text output and the panel
    notes: list[str]


def _go_round(value: float) -> int:
    """Half away from zero, as Go's `math.Round`."""
    whole = math.trunc(value)
    if abs(value - whole) >= 0.5:
        whole += 1 if value > 0 else -1
    return int(whole)


def _column(item: _Item) -> int | None:
    """The `RandPropPoints` column (0 to 4) of the item's slot type."""
    if item.class_id == _ITEM_CLASS_WEAPON:
        if item.subclass_id in _WEAPON_TWO_HAND:
            return 0
        if item.subclass_id in _WEAPON_RANGED:
            return 4
        return 3
    if item.class_id == _ITEM_CLASS_ARMOR:
        return _ARMOR_COLUMN.get(item.inventory_type)
    return None


def _budget(tables: TwinkTables, item: _Item, column: int) -> int:
    quality = _QUALITY_COLUMN.get(item.quality)
    row = tables.budgets.get(item.item_level)
    if quality is None or row is None:
        return 0
    return row[quality][column]


def _amounts(shares: Iterable[tuple[int, int]], budget: int) -> dict[int, int]:
    out: dict[int, int] = {}
    for stat, share in shares:
        if share > 0 and budget > 0:
            out[stat] = out.get(stat, 0) + _go_round(share * budget * 0.0001)
    return out


def _item_amounts(tables: TwinkTables, item: _Item) -> dict[int, int]:
    column = _column(item)
    if column is None:
        return {}
    out = _amounts(item.shares, _budget(tables, item, column))
    first = _budget(tables, item, 0)
    if item.flags_4 & _CASTER_HEALING:
        healing = _CASTER_HEALING_SHARE * first // 10000
        out[_STAT_HEALING] = out.get(_STAT_HEALING, 0) + healing
        out[_STAT_SPELL_DAMAGE] = out.get(_STAT_SPELL_DAMAGE, 0) + healing // 3
    elif item.flags_4 & _CASTER_SPELL_POWER:
        power = _CASTER_SPELL_POWER_SHARE * first // 10000
        out[_STAT_SPELL_POWER] = out.get(_STAT_SPELL_POWER, 0) + power
    return out


def _suffix_amounts(tables: TwinkTables, item: _Item, suffix: _Suffix) -> dict[int, int]:
    column = _column(item)
    if column is None:
        return {}
    return _amounts(suffix.shares, _budget(tables, item, column))


def _stats(amounts: Mapping[int, int], config: TwinkConfig) -> list[TwinkStat]:
    return [
        TwinkStat(stat=s, name=config.stats.get(s, f"stat {s}"), amount=a)
        for s, a in sorted(amounts.items())
        if a
    ]


def _score(amounts: Mapping[int, int], weights: Mapping[int, float]) -> float:
    return round(sum(weights.get(s, 0.0) * a for s, a in amounts.items()), 2)


def _summed(*parts: Mapping[int, int]) -> dict[int, int]:
    out: dict[int, int] = {}
    for part in parts:
        for stat, amount in part.items():
            out[stat] = out.get(stat, 0) + amount
    return out


def _admits(mask: int, row_id: int | None) -> bool:
    """Whether a class or race mask admits the row id (bit `id - 1`; -1 and
    0 admit everyone, as does an id the record lacks)."""
    if mask in (-1, 0) or row_id is None or not 1 <= row_id <= 64:
        return True
    return bool(mask & (1 << (row_id - 1)))


def wowhead_link(item_id: int, bonus: int | None, version: str | None) -> str | None:
    """The item's page on the site `dressing_sites.toml` names, in the room
    of the client's version (`/forever/item=<id>`), with `?bonus=<id>` for a
    suffix [verify]; None when no room covers the version."""
    try:
        sites = dressing.load_sites()
    except dressing.SitesError:
        return None
    room = dressing.room_for(sites, version)
    if room is None:
        return None
    base = sites.rooms[room].removesuffix("/dressing-room")
    url = f"{sites.site.origin}{base}/item={item_id}"
    return url if bonus is None else f"{url}?{urlencode({'bonus': bonus})}"


def planner_link(
    config: TwinkConfig, class_file: str, race_id: int | None, bracket: int
) -> tuple[str | None, str | None]:
    """The "Plan on ForeverTwinking" address, or None and why."""
    planner = config.planner
    klass = planner.classes.get(class_file)
    if klass is None:
        return None, f"{CONFIG_FILE} has no planner word for the class {class_file}"
    race = planner.races.get(race_id) if race_id is not None else None
    if race is None:
        which = "the record has no race" if race_id is None else f"race id {race_id}"
        return None, f"{CONFIG_FILE} has no planner word for {which}"
    query = urlencode({"class": klass, "race": race, "bracket": f"l{bracket}"})
    return f"{planner.origin}{planner.path}?{query}", None


def item_sources(item_ids: Iterable[int]) -> dict[int, str]:
    """Where each item comes from (a quest, a drop), by item id. The hook
    M22-08's lookup index fills; until then none is known."""
    del item_ids
    return {}


def default_bracket(
    config: TwinkConfig, level: int | None
) -> tuple[int, Literal["level", "first", "last"]]:
    """The bracket a character of `level` twinks at: the lowest cap at or
    above it; with no level, the first; above every cap, the last."""
    if level is None:
        return config.brackets[0], "first"
    for cap in config.brackets:
        if level <= cap:
            return cap, "level"
    return config.brackets[-1], "last"


def _skipped_name(name: str, filters: Filters) -> bool:
    """Whether a name marks a test, placeholder or retired item."""
    for word in _SPLIT.split(name):
        if word.casefold() in filters.skip_words:
            return True
        if any(word.startswith(p) for p in filters.skip_prefixes):
            return True
    return False


@dataclass(frozen=True, slots=True)
class _Option:
    item: _Item
    suffix: TwinkSuffix | None
    others: list[TwinkSuffix]
    score: float
    amounts: dict[int, int]


def _options(
    tables: TwinkTables, item: _Item, config: TwinkConfig, weights: Mapping[int, float]
) -> _Option:
    """The item scored with each of its suffixes, best first."""
    own = _item_amounts(tables, item)
    scored: list[tuple[float, TwinkSuffix, dict[int, int]]] = []
    for list_id in tables.suffix_options.get(item.id, ()):
        suffix = tables.suffixes[list_id]
        extra = _suffix_amounts(tables, item, suffix)
        total = _summed(own, extra)
        entry = TwinkSuffix(
            id=list_id,
            name=suffix.name,
            score=_score(extra, weights),
            stats=_stats(extra, config),
        )
        scored.append((_score(total, weights), entry, total))
    if not scored:
        return _Option(item, None, [], _score(own, weights), own)
    scored.sort(key=lambda t: (-t[0], t[1].id))
    best = scored[0]
    return _Option(item, best[1], [t[1] for t in scored[1:]], best[0], best[2])


def _standing_open(tables: TwinkTables, item: _Item, race_id: int | None) -> bool:
    """Whether the character's race can earn the standing the item needs:
    not when the faction's race masks put the race at a base standing of
    Hated's floor (the other side's battleground factions) [verify]."""
    if item.min_faction == 0 or race_id is None or not 1 <= race_id <= 64:
        return True
    for mask, base in tables.factions.get(item.min_faction, ()):
        if mask & (1 << (race_id - 1)):
            return base > _HATED
    return True


def _usable(item: _Item, gear_types: ClassGear, class_id: int | None, race_id: int | None) -> bool:
    if item.class_id == _ITEM_CLASS_ARMOR and item.subclass_id not in gear_types.armor:
        return False
    if item.class_id == _ITEM_CLASS_WEAPON and item.subclass_id not in gear_types.weapons:
        return False
    return _admits(item.allowable_class, class_id) and _admits(item.allowable_race, race_id)


def _worn(
    tables: TwinkTables,
    slot: gear.GearSlot,
    config: TwinkConfig,
    weights: Mapping[int, float],
    bracket: int,
    version: str | None,
) -> TwinkWorn:
    item = tables.items.get(slot.item_id) if slot.item_id is not None else None
    suffix_id = next((b for b in slot.bonus_ids if b in tables.suffixes), None)
    link = wowhead_link(slot.item_id, suffix_id, version) if slot.item_id is not None else None
    if item is None:
        why = (
            "the link has no item id"
            if slot.item_id is None
            else "not in the game tables wowlab downloads"
        )
        return TwinkWorn(
            item_id=slot.item_id,
            name=slot.name,
            suffix=None,
            score=None,
            unscored=why,
            stats=[],
            required_level=None,
            above_cap=False,
            wowhead=link,
        )
    own = _item_amounts(tables, item)
    suffix = None
    total = own
    if suffix_id is not None:
        found = tables.suffixes[suffix_id]
        extra = _suffix_amounts(tables, item, found)
        total = _summed(own, extra)
        suffix = TwinkSuffix(
            id=suffix_id,
            name=found.name,
            score=_score(extra, weights),
            stats=_stats(extra, config),
        )
    unscored = None
    if item.quality not in _QUALITY_COLUMN and item.shares:
        unscored = "the game tables give no stat budget for an item of this quality"
    return TwinkWorn(
        item_id=item.id,
        name=slot.name,
        suffix=suffix,
        score=None if unscored else _score(total, weights),
        unscored=unscored,
        stats=_stats(total, config),
        required_level=item.required_level,
        above_cap=item.required_level > bracket,
        wowhead=link,
    )


def _gap_words(best: TwinkCandidate | None, worn: TwinkWorn | None, gap: float | None) -> str:
    if best is None:
        return "no candidate in the game tables for this slot"
    if worn is None:
        return f"the slot is empty; the best candidate scores {best.score:g}"
    if gap is None:
        return "the worn item is not scored, so the gap is not known"
    if gap <= 0:
        return "the worn item is as good as the best candidate, or better"
    return f"the best candidate scores {gap:g} more"


def build_panel(
    tables: TwinkTables,
    config: TwinkConfig,
    *,
    filled: Sequence[gear.GearSlot],
    slot_names: Mapping[int, str],
    class_file: str,
    class_id: int | None,
    race_id: int | None,
    level: int | None,
    bracket: int | None,
    weights_name: str | None,
    weights_source: Literal["default", "user"],
    version: str | None,
    top: int = TOP_DEFAULT,
) -> TwinkPanel:
    """The panel. `filled` is `gear.rows`' first list; `version` the client's
    (it picks the Wowhead room). Raises `TwinkError` for a class or weight
    set the configuration does not know, or a bracket it does not list."""
    gear_types = config.classes.get(class_file)
    sets = config.weights.get(class_file, {})
    if gear_types is None or not sets:
        raise TwinkError(
            f"{CONFIG_FILE} has no gear types or no weights for the class {class_file}; add "
            f"[classes.{class_file}] and a [weights.{class_file}.<name>] table to your own "
            f"{CONFIG_FILE}"
        )
    if weights_name is None:
        weights_name = next(iter(sets))
    elif weights_name not in sets:
        raise TwinkError(
            f"no weight set {weights_name!r} for {class_file}; the sets are {', '.join(sets)}"
        )
    bracket_from: Literal["asked", "level", "first", "last"] = "asked"
    if bracket is None:
        bracket, bracket_from = default_bracket(config, level)
    elif bracket not in config.brackets:
        raise TwinkError(
            f"{bracket} is not a bracket; the brackets end at levels "
            f"{', '.join(str(b) for b in config.brackets)}"
        )
    by_name = {name: stat for stat, name in config.stats.items()}
    weights = {by_name[name]: w for name, w in sets[weights_name].items()}
    by_slot: dict[int, list[_Option]] = {}
    no_level_left_out = 0
    other_side_left_out = 0
    for item in tables.items.values():
        slots = config.slots.get(item.inventory_type)
        if (
            not slots
            or not item.name
            or item.quality not in _QUALITY_COLUMN
            or item.required_level > bracket
            or not _usable(item, gear_types, class_id, race_id)
            or _skipped_name(item.name, config.filters)
        ):
            continue
        if not _standing_open(tables, item, race_id):
            other_side_left_out += 1
            continue
        if item.required_level == 0 and item.item_level > bracket + config.filters.no_level_margin:
            no_level_left_out += 1
            continue
        option = _options(tables, item, config, weights)
        if option.score <= 0:
            continue
        for n in slots:
            by_slot.setdefault(n, []).append(option)
    sources = item_sources({o.item.id for opts in by_slot.values() for o in opts})
    worn_by_slot = {s.slot: s for s in filled}
    out: list[TwinkSlot] = []
    every_slot = sorted({n for slots in config.slots.values() for n in slots})
    for n in every_slot:
        ranked = sorted(
            by_slot.get(n, []), key=lambda o: (-o.score, -o.item.item_level, o.item.id)
        )[:top]
        worn_slot = worn_by_slot.get(n)
        worn = (
            _worn(tables, worn_slot, config, weights, bracket, version)
            if worn_slot is not None
            else None
        )
        candidates = [
            TwinkCandidate(
                item_id=o.item.id,
                name=o.item.name,
                quality=o.item.quality,
                item_level=o.item.item_level,
                required_level=o.item.required_level,
                inventory_type=o.item.inventory_type,
                two_hand=o.item.inventory_type == _TWO_HAND_TYPE,
                suffix=o.suffix,
                other_suffixes=o.others,
                score=o.score,
                stats=_stats(o.amounts, config),
                wowhead=wowhead_link(o.item.id, o.suffix.id if o.suffix else None, version),
                source=sources.get(o.item.id),
                worn=worn is not None
                and worn.item_id == o.item.id
                and (worn.suffix.id if worn.suffix else None)
                == (o.suffix.id if o.suffix else None),
            )
            for o in ranked
        ]
        best = candidates[0] if candidates else None
        gap: float | None = None
        if best is not None:
            if worn is None:
                gap = best.score
            elif worn.score is not None:
                gap = round(best.score - worn.score, 2)
        out.append(
            TwinkSlot(
                slot=n,
                slot_name=slot_names.get(n),
                worn=worn,
                candidates=candidates,
                gap=gap,
                gap_words=_gap_words(best, worn, gap),
            )
        )
    planner, planner_absent = planner_link(config, class_file, race_id, bracket)
    notes = _notes(
        tables,
        level,
        bracket,
        no_level_left_out,
        config.filters.no_level_margin,
        other_side_left_out,
    )
    words = f"Level {bracket} bracket, weights {weights_name}"
    if level is not None and level > bracket:
        words += f": this character is level {level}, above the bracket's top level"
    return TwinkPanel(
        bracket=bracket,
        brackets=list(config.brackets),
        bracket_from=bracket_from,
        level=level,
        class_file=class_file,
        race_id=race_id,
        weights_name=weights_name,
        weight_sets=list(sets),
        weights=dict(sets[weights_name]),
        weights_source=weights_source,
        tables_build=tables.build,
        slots=out,
        planner=planner,
        planner_absent=planner_absent,
        gear_without_details=tables.gear_without_details,
        gear_in_item=tables.gear_in_item,
        no_level_left_out=no_level_left_out,
        other_side_left_out=other_side_left_out,
        words=words,
        notes=notes,
    )


SOURCE_NOTE = "Where each item comes from (a quest, a drop) is not known yet."
SCORE_NOTE = (
    "A score is each stat times its weight, summed. The stat amounts are worked out from the "
    "game tables' stat budget, as WoWSims' Forever sim does, and are not yet confirmed against "
    "the game's tooltips. Weapon damage, armour and set bonuses are not scored, and a weapon type "
    "still needs its skill trained."
)
SUFFIX_NOTE = (
    'An item with random suffixes ("of the Owl") is scored with each suffix; the best is shown '
    "and the others listed. Which suffix an item drops with is chance."
)
TWO_HAND_NOTE = (
    "A two-handed weapon also takes the off hand: compare it with the main hand and off hand "
    "together."
)


def _notes(
    tables: TwinkTables,
    level: int | None,
    bracket: int,
    no_level: int,
    margin: int,
    other_side: int,
) -> list[str]:
    notes = [SCORE_NOTE, SUFFIX_NOTE, TWO_HAND_NOTE, SOURCE_NOTE]
    if other_side:
        notes.append(
            f"{other_side} items that need standing with the other side's factions (such as "
            "the other side's battleground rewards) were left out."
        )
    if tables.gear_without_details:
        notes.append(
            f"{tables.gear_without_details} of the {tables.gear_in_item} weapons and armour "
            "pieces the game tables list have no stats in the copy wowlab downloads, so they are "
            "never candidates, and a worn one is not scored."
        )
    if no_level:
        notes.append(
            f"{no_level} items with no required level were left out because their item level "
            f"is more than {margin} above the bracket's top level: most are raid or dungeon "
            f"drops a level {bracket} character cannot reach."
        )
    if level is None:
        notes.append("The record has no level, so the first bracket is shown.")
    elif level > bracket:
        notes.append(
            f"This character is level {level}; a bracket's battlegrounds take characters up to "
            "its top level only."
        )
    return notes
