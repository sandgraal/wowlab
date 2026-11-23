"""Constructed QuestieDB slices for the lookup tests (M22-08).

CONSTRUCTED INPUT. QuestieDB is a third-party addon's data, so no byte of
the owner's copy is committed (the ticket's rule, as M21-09 kept Ketho's
lists out): what is committed is the shape, as `docs/LAB_FORMATS.md` §13
records it from the owner's QuestieDB 1.1.4 for Forever on 2026-11-23, and
an owner-run `@live` test (`test_questiedb_live.py`) reads the real one.

A slice is a TOC whose `## X-...` directives carry a small set of entities
encoded as the store encodes them: base64 of CBOR (strings as byte strings,
a row as a map, or an array when its keys run 1..n), a scalar row per
entity under `-S` with `p`, its presence mask, each table field under its
own key, and each kind's ids as base64 of zlib of a CBOR array. Names and
ids follow the game's where the real data has them, so the recorded wago
subsets (`fixtures/wago/*.1.60.1.71037.subset.csv`) name the same items and
zones; every other value is invented for the test.
"""

from __future__ import annotations

import base64
import struct
import zlib
from collections.abc import Iterable, Mapping
from pathlib import Path

VERSION = "1.60.1.71037"  # the recorded subsets' build; tests may name it (L6)
FLAVOR = "_forever_"  # a flavor folder name; tests may name it (L6)

Row = Mapping[int, object]
Entities = Mapping[str, Mapping[int, Row]]


def cbor(value: object) -> bytes:
    """The CBOR of `value`, definite lengths, the shortest argument; `str`
    as a byte string, as the store writes it; `float` as a double."""
    if value is None:
        return b"\xf6"
    if value is True:
        return b"\xf5"
    if value is False:
        return b"\xf4"
    if isinstance(value, int):
        return _head(0, value) if value >= 0 else _head(1, -1 - value)
    if isinstance(value, float):
        return b"\xfb" + struct.pack(">d", value)
    if isinstance(value, str):
        value = value.encode("utf-8")
    if isinstance(value, bytes):
        return _head(2, len(value)) + value
    if isinstance(value, (list, tuple)):
        return _head(4, len(value)) + b"".join(cbor(v) for v in value)
    if isinstance(value, dict):
        return _head(5, len(value)) + b"".join(cbor(k) + cbor(v) for k, v in value.items())
    raise TypeError(type(value))


def _head(major: int, n: int) -> bytes:
    if n < 24:
        return bytes([major << 5 | n])
    for info, fmt in ((24, ">B"), (25, ">H"), (26, ">I"), (27, ">Q")):
        try:
            return bytes([major << 5 | info]) + struct.pack(fmt, n)
        except struct.error:
            continue
    raise ValueError(n)


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def row_lines(kind: str, entity: int, row: Row, *, as_array: bool = False) -> list[str]:
    """The directives of one entity: tables (lists, dicts) under their own
    keys, the rest in the `-S` row with the presence mask."""
    scalars: dict[object, object] = {}
    lines: list[str] = []
    mask = 0
    for field, value in row.items():
        if isinstance(value, (list, dict)):
            mask |= 1 << (field - 1)
            lines.append(f"## X-{kind}-{entity}-{field}: {b64(cbor(value))}")
        else:
            scalars[field] = value
    if as_array:
        top = max(scalars, default=0)
        assert isinstance(top, int)
        array = [scalars.get(i) for i in range(1, top + 1)]
        return [f"## X-{kind}-{entity}-S: {b64(cbor(array))}", *lines]
    if mask:
        scalars[b"p"] = mask
    return [f"## X-{kind}-{entity}-S: {b64(cbor(scalars))}", *lines]


def chunked(key: str, value: str, parts: int) -> list[str]:
    """`key` as a chunk header and its value cut in `parts` pieces."""
    size = -(-len(value) // parts)
    pieces = [value[i : i + size] for i in range(0, len(value), size)]
    return [f"## {key}: ~{len(pieces)}~", *(f"## {key}-{i}: {p}" for i, p in enumerate(pieces, 1))]


def toc_text(
    entities: Entities,
    *,
    files: Iterable[str] = (),
    extra: Iterable[str] = (),
    arrays: Iterable[tuple[str, int]] = (),
    chunk_ids: int = 1,
) -> bytes:
    """A whole baked TOC: the addon's own directives, the files it lists,
    every kind's id header (cut in `chunk_ids` parts) and every entity."""
    as_array = set(arrays)
    lines = [
        "## Interface: 11508",
        "## Title: QuestieDB",
        "## Version: 1.1.4",
        "## X-Contract-Version: 4",
        "## X-Flavor: Forever",
        "## X-Mode: baked",
        "## X-BUILD-TIME: 2026-11-01T00:00:00Z",
        "## X-BUILD-COMMIT: 0000000000000000000000000000000000000000",
        "",
        *files,
        "",
    ]
    for kind in ("Item", "Npc", "Quest", "Object"):
        rows = entities.get(kind, {})
        header = b64(zlib.compress(cbor(sorted(rows))))
        if chunk_ids > 1:
            lines += chunked(f"X-{kind}-IDS", header, chunk_ids)
        else:
            lines.append(f"## X-{kind}-IDS: {header}")
        for entity, row in rows.items():
            lines += row_lines(kind, entity, row, as_array=(kind, entity) in as_array)
    lines += list(extra)
    return ("\n".join(lines) + "\n").encode("utf-8")


# ─── the slice the lookup tests read ─────────────────────────────────────────

# Field numbers: QuestieDB's (`src/meta/<kind>Meta.lua`).
SLICE: dict[str, dict[int, dict[int, object]]] = {
    "Item": {
        # name, npcDrops, startQuest, itemLevel, requiredLevel
        16305: {1: "Sharptalon's Claw", 2: [12676], 5: 2, 9: 20, 10: 20},
        # name, questRewards, itemLevel, requiredLevel, vendors
        159: {1: "Refreshing Spring Water", 6: [8], 9: 5, 10: 1, 14: [1000, 1001]},
        # name, npcDrops, objectDrops, itemDrops (a container), itemLevel,
        # relatedQuests
        2589: {1: "Linen Cloth", 2: [3], 3: [31], 4: [6827], 9: 5, 15: [8]},
        # name, itemLevel: handed over with quest 54 (its sourceItemId)
        745: {1: "Marshal McBride's Documents", 9: 1},
        # name, npcDrops, itemLevel: dropped where only the corrections or
        # one table give a chance
        725: {1: "Gnoll Paw", 2: [3, 12676], 9: 1},
        # name, questRewards, itemLevel, requiredLevel
        25: {1: "Worn Shortsword", 6: [8], 9: 2, 10: 1},
        6827: {1: "Box of Supplies", 9: 1},
    },
    "Npc": {
        # name, minLevel, maxLevel, spawns, zoneID, friendlyToFaction
        12676: {1: "Sharptalon", 4: 31, 5: 31, 7: {331: [[74.98, 70.06]]}, 9: 331},
        # name, levels, spawns, zoneID, questEnds, friendlyToFaction
        12696: {
            1: "Senani Thunderheart",
            4: 30,
            5: 30,
            7: {331: [[73.78, 61.46]]},
            9: 331,
            11: [2],
            13: "H",
        },
        3: {1: "Flesh Eater", 4: 24, 5: 25, 7: {10: [[25.06, 38.2], [25.37, 36.04]]}, 9: 10},
        1000: {
            1: "Innkeeper Farley",
            4: 30,
            5: 30,
            7: {12: [[43.77, 65.8]]},
            9: 12,
            10: [8],
            11: [8],
            13: "A",
            14: "Innkeeper",
        },
        1001: {
            1: "Innkeeper Grosk",
            4: 30,
            5: 30,
            7: {14: [[51.5, 41.6]], 1637: [[54.1, 68.4], [54.2, 68.5]]},
            9: 14,
            13: "H",
            14: "Innkeeper",
        },
        # Spawns in zone 1 only: a CBOR array, its one zone at index 1; zone 1
        # is not in the recorded zone subset.
        1002: {1: "Lost Wanderer", 4: 5, 5: 5, 7: [[[10.0, 20.0]]]},
        # No spawn: only QuestieDB's estimate of its zone (field 9).
        1003: {1: "Wandering Merchant", 4: 20, 5: 20, 9: 14},
        # Inside an instance wing QuestieDB numbers itself (10022), with the
        # -1, -1 point it writes for a spawn inside an instance.
        11441: {1: "Gordok Brute", 4: 57, 5: 58, 7: {10022: [[-1, -1]]}, 9: 10022},
    },
    "Quest": {
        # name, startedBy {items}, finishedBy [[npcs]], requiredLevel, level, zone
        2: {1: "Sharptalon's Claw", 2: {3: [16305]}, 3: [[12696]], 4: 20, 5: 30, 17: 331},
        8: {1: "A Rogue's Deal", 2: [[1000]], 3: [[1000]], 4: 1, 5: 5, 17: 12},
        # A quest sorted by category (a negative zoneOrSort): no zone.
        9: {1: "The Class Trial", 4: 10, 5: 10, 17: -61},
        # sourceItemId 745; a level QuestieDB does not know (0)
        54: {1: "Report to Goldshire", 4: 1, 5: 0, 11: 745, 17: 12},
    },
    "Object": {
        # name, questStarts, spawns, zoneID
        31: {1: "Old Lion Statue", 2: [8], 4: {12: [[79.4, 46.83]]}, 5: 12},
    },
}

DROPS_FILE = "support\\Forever\\DropTables\\classicItemDrops.lua"
CORRECTIONS_FILE = "support\\Forever\\DropTables\\itemDropCorrections.lua"
FIX_FILE = "src\\corrections\\Forever\\foreverItemFixes.lua"
ZONES_FILE = "src\\corrections\\enum\\zones.lua"

# QuestieDB's own zone list: AreaTable ids and the areas it numbers itself.
ZONES_LUA = b"""-- Shared zone symbols.
local _, LibQuestieDB = ...
local constants = LibQuestieDB.Enum

constants.zoneIDs = {
  ELWYNN_FOREST = 12,
  DIRE_MAUL_GORDOK_COMMONS = 10022,
}
"""

DROPS_LUA = b"""---@class QuestieClassicItemDrops
local QuestieClassicItemDrops = QuestieLoader:CreateModule("QuestieClassicItemDrops")

QuestieClassicItemDrops.wowheadData = [[return {
    [2589] = { -- Linen Cloth
        [3] = 12.5,
    },
    [725] = { -- Gnoll Paw
        [3] = 45.04,
        [12676] = 37.6,
    },
}]]

QuestieClassicItemDrops.cmangosData = [[return {
    [2589] = { -- Linen Cloth
        [3] = 10,
    },
    [725] = { -- Gnoll Paw
        [12676] = 80,
    },
}]]
"""

CORRECTIONS_LUA = b"""---@class QuestieItemDropCorrections
local QuestieItemDropCorrections = QuestieLoader:CreateModule("QuestieItemDropCorrections")
local DropKeys = DropDB.correctionKeys
-- [itemID] = { -- Item Name
--    [npcID] = DropDB.correctionKeys.WOWHEAD,
-- }

QuestieItemDropCorrections.Era = {
    [2589] = { -- Linen Cloth
        [3] = DropKeys.WOWHEAD,
    },
    [725] = { -- Gnoll Paw
        [3] = 50,
        [12676] = DropKeys.PSERVER,
    },
}

QuestieItemDropCorrections.Tbc = { -- only on later game versions
    [2589] = { [3] = 1.5 },
}

QuestieItemDropCorrections.Wotlk = {
}

QuestieItemDropCorrections.Cata = {
    [725] = { [3] = math.max(1, 2) },
}
"""


def place(flavor: Path, toc: bytes, *, folder: str = "QuestieDB", name: str | None = None) -> Path:
    """Write a slice's TOC (and the drop files it lists) under the flavor
    folder's `Interface/AddOns/<folder>/`; the TOC's path."""
    addon = flavor / "Interface" / "AddOns" / folder
    (addon / "support" / "Forever" / "DropTables").mkdir(parents=True, exist_ok=True)
    (addon / "support" / "Forever" / "DropTables" / "classicItemDrops.lua").write_bytes(DROPS_LUA)
    (addon / "support" / "Forever" / "DropTables" / "itemDropCorrections.lua").write_bytes(
        CORRECTIONS_LUA
    )
    (addon / "src" / "corrections" / "enum").mkdir(parents=True, exist_ok=True)
    (addon / "src" / "corrections" / "enum" / "zones.lua").write_bytes(ZONES_LUA)
    path = addon / (name or f"{folder}_Forever.toc")
    path.write_bytes(toc)
    return path


def slice_toc(**kwargs: object) -> bytes:
    """The lookup tests' slice, listing the drop files and one correction."""
    return toc_text(
        SLICE,
        files=[ZONES_FILE, FIX_FILE, DROPS_FILE, CORRECTIONS_FILE],
        extra=["## X-l10n-Version: 1", "## X-l10n-deDE-Item: ~1~", "## X-l10n-deDE-Item-1: AA=="],
        **kwargs,  # type: ignore[arg-type]
    )
