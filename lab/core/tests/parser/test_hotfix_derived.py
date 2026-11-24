"""`hotfix` against the derived fixture of the owner's hotfix cache (M22-23;
ADR-0033 note of 2026-11-23, layer b): `fixtures/macos-71037/derived/
hotfix-items.json`, written by `scripts/hotfix_facts.py` from the real
`Cache/ADB/enUS/DBCache.bin` (header facts, counts, and the decoded rows of
five items; never a byte of the file).

What is checked without the install:

- each recorded row, written back with the layout (`_hotfix_build`, a
  constructed record), is exactly the size the real record had, and
  decodes to the same row: the layout's widths fit the real data;
- each item is what wowsims/forever's `leftover_db.json` (MIT; the five
  entries in `fixtures/wowsims/`, an independent reading of the same
  hotfixes) says it is: name, required level, quality, item level and a
  weapon's speed;
- the counts add up, and the record holds no bytes.

The real file is read again by `test_hotfix_live.py` (`@live`).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import gamedata, hotfix

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hotfix_build import Rec, cache, encode_row

pytestmark = pytest.mark.parser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FACTS: dict[str, Any] = json.loads(
    (FIXTURES / "macos-71037" / "derived" / "hotfix-items.json").read_text(encoding="utf-8")
)
WOWSIMS: dict[str, Any] = json.loads(
    (FIXTURES / "wowsims" / "leftover_db.a8bd7fd8.items.json").read_text(encoding="utf-8")
)
ITEMS = (6537, 6541, 6543, 14113, 2632)
TABLES = ("Item", "ItemSparse")


def _item(item_id: int) -> dict[str, Any]:
    [found] = [i for i in FACTS["items"] if i["id"] == item_id]
    return found


def test_the_record_is_of_one_build_and_holds_no_bytes() -> None:
    assert FACTS["kind"] == "hotfix-derived" and FACTS["schema"] == 1
    assert FACTS["header"] == {"version": hotfix.SUPPORTED_VERSION, "build": FACTS["build"]}
    assert FACTS["version"].rsplit(".", 1)[1] == str(FACTS["build"])
    assert FACTS["file"] == "Cache/ADB/enUS/DBCache.bin"
    assert sorted(i["id"] for i in FACTS["items"]) == sorted(ITEMS)
    text = json.dumps(FACTS)
    assert "/Users/" not in text and "\\\\" not in text
    assert not re.search(r'"data"', text)


def test_the_counts_add_up() -> None:
    for name, table in FACTS["tables"].items():
        assert table["hash"] == f"0x{hotfix.sstrhash(name):08x}"
        assert sum(table["by_status"].values()) == table["records"]
    overlay = FACTS["overlay"]
    assert overlay["applied"] is True and overlay["cache_build"] == FACTS["build"]
    for t in overlay["tables"]:
        assert t["records"] == FACTS["tables"][t["table"]]["records"]
        assert t["added"] + t["replaced"] <= FACTS["tables"][t["table"]]["by_status"]["1"]
    sparse = {t["table"]: t for t in overlay["tables"]}["ItemSparse"]
    # The owner's finding: thousands of item rows the downloaded tables lack.
    assert sparse["added"] > 4000


@pytest.mark.parametrize("item_id", ITEMS)
def test_a_recorded_row_written_back_decodes_to_itself(item_id: int) -> None:
    entry = _item(item_id)
    layouts = hotfix.layouts()
    records = []
    for name in ("Item", "ItemSparse"):
        row = entry[name]["row"]
        assert row is not None, f"{item_id} has no {name} row in the cache"
        data = encode_row(layouts[name], row)
        assert len(data) == entry[name]["size"], name
        records.append(Rec(name, item_id, data, push_id=entry[name]["records"][-1]["push_id"]))
    parsed = hotfix.parse(cache(FACTS["build"], records), layouts)
    for record in parsed.records:
        got = hotfix.decode_row(layouts[record.table], record)
        assert got.pop(hotfix.SOURCE_KEY) == hotfix.SOURCE_HOTFIX
        assert got == entry[record.table]["row"]


@pytest.mark.parametrize("item_id", ITEMS)
def test_each_item_is_what_wowsims_read_from_the_same_hotfixes(item_id: int) -> None:
    [sim] = [i for i in WOWSIMS["items"] if i["id"] == item_id]
    sparse = _item(item_id)["ItemSparse"]["row"]
    item = _item(item_id)["Item"]["row"]
    assert sparse["Display_lang"] == sim["name"]
    assert int(sparse["RequiredLevel"]) == sim["reqLvl"]
    assert int(sparse["OverallQualityID"]) == sim["quality"]
    assert int(sparse["ItemLevel"]) == sim["scalingOptions"]["0"]["ilvl"]
    if "weaponSpeed" in sim:
        assert int(item["ClassID"]) == 2  # a weapon
        assert int(sparse["ItemDelay"]) == round(sim["weaponSpeed"] * 1000)
    else:
        assert int(item["ClassID"]) == 4  # armour
    # Each of the five is a random-suffix item: no stats of its own (wowsims
    # lists only its armour), its stats from the suffix it rolled.
    assert sim["randomSuffixOptions"]
    assert all(int(sparse[f"StatPercentEditor_{n}"]) == 0 for n in range(10))
    assert set(sim["scalingOptions"]["0"].get("stats", {})) <= {"27"}  # wowsims' armour


def test_the_overlay_marks_the_five_as_hotfix_rows(tmp_path: Path) -> None:
    """The recorded rows, laid over a table that lacks them (constructed:
    an empty `ItemSparse` and `Item` of the fixture's build), come out
    marked as read from the hotfix cache."""
    layouts = {name: hotfix.layouts()[name] for name in TABLES}
    paths = {}
    for name, layout in layouts.items():
        path = tmp_path / f"{name}.csv"
        path.write_text(",".join(layout.columns) + "\n", encoding="utf-8")
        paths[name] = path
    records = [
        Rec(name, i, encode_row(layouts[name], _item(i)[name]["row"]))
        for i in ITEMS
        for name in layouts
    ]
    parsed = hotfix.parse(cache(FACTS["build"], records), layouts)
    overlay = gamedata.overlay_hotfixes(parsed, FACTS["version"], paths.get, tables=TABLES)
    rows = list(overlay.rows("ItemSparse", iter(())))
    assert sorted(int(r["ID"]) for r in rows) == sorted(ITEMS)
    assert {r[hotfix.SOURCE_KEY] for r in rows} == {hotfix.SOURCE_HOTFIX}
