"""`gamedata.HotfixOverlay` (M22-23): the hotfix cache laid over the cached
tables of one build, on constructed caches and tables (L8: labelled
`constructed`; the real cache is graded by the parser suite's derived
fixture and the `@live` test).

The rules: only a cache of the tables' build is laid over them, and a
mismatch is refused with its reason; records apply in ascending push id;
status 1 with data adds or replaces a row, 2 and 3 remove one, anything
else is left alone; every row taken from the cache says so. Nothing here
reads a real install; the flavor folders are built in `tmp_path`.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

from wowlab_core import gamedata, hotfix

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

from _hotfix_build import Rec, blank_row, cache, encode_row

BUILD = "1.60.1.71037"
NUMBER = 71037
ITEM = hotfix.layouts()["Item"]
SPARSE = hotfix.layouts()["ItemSparse"]
ITEMS = ("Item", "ItemSparse")


def _write_table(folder: Path, layout: hotfix.TableLayout, rows: list[dict[str, str]]) -> Path:
    path = folder / f"{layout.table}.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(layout.columns)
        for row in rows:
            writer.writerow([row[c] for c in layout.columns])
    return path


def _sparse_row(item_id: int, name: str) -> dict[str, str]:
    return {"ID": str(item_id), **blank_row(SPARSE, Display_lang=name)}


def _item_row(item_id: int) -> dict[str, str]:
    return {"ID": str(item_id), **blank_row(ITEM, ClassID="4", InventoryType="8")}


@pytest.fixture
def tables(tmp_path: Path) -> dict[str, Path]:
    folder = tmp_path / "tables"
    folder.mkdir()
    return {
        "Item": _write_table(folder, ITEM, [_item_row(n) for n in (1, 2, 3, 4, 5)]),
        "ItemSparse": _write_table(
            folder, SPARSE, [_sparse_row(1, "One"), _sparse_row(2, "Two"), _sparse_row(5, "Five")]
        ),
    }


def _data(name: str) -> bytes:
    return encode_row(SPARSE, blank_row(SPARSE, Display_lang=name))


def _cache(build: int, records: list[Rec]) -> hotfix.HotfixCache:
    return hotfix.parse(cache(build, records), ("Item", "ItemSparse"))


def _rows(overlay: gamedata.HotfixOverlay, path: Path) -> list[dict[str, str]]:
    return list(overlay.rows(path.stem, gamedata._iter_rows(path)))


def test_statuses_and_push_order_constructed(tables: dict[str, Path]) -> None:
    records = [
        Rec("ItemSparse", 2, _data("Two, fixed"), push_id=10),  # replaces
        Rec("ItemSparse", 9, _data("Nine"), push_id=10),  # adds
        Rec("ItemSparse", 7, _data("Seven"), push_id=10),  # adds
        Rec("ItemSparse", 5, status=2, push_id=10),  # removes
        Rec("ItemSparse", 1, status=4, push_id=10),  # left alone
        Rec("ItemSparse", 1, status=1, push_id=10),  # a row with no data: left alone
        # Applied by push id, not file order: 8 is removed, then added.
        Rec("ItemSparse", 8, _data("Eight"), push_id=30),
        Rec("ItemSparse", 8, status=3, push_id=20),
        # And 2's later push wins over its earlier one.
        Rec("ItemSparse", 2, _data("Two, older"), push_id=-1),
        Rec("Item", 3, status=3, push_id=10),  # removes
        Rec("Item", 6, encode_row(ITEM, blank_row(ITEM, ClassID="2")), push_id=10),
    ]
    overlay = gamedata.overlay_hotfixes(
        _cache(NUMBER, records), BUILD, tables.get, tables=ITEMS, file="x.bin"
    )
    report = overlay.report()
    assert report.applied and report.refused is None
    assert (report.file, report.cache_build, report.tables_build) == ("x.bin", NUMBER, BUILD)
    counts = {t.table: t for t in report.tables}
    assert counts["ItemSparse"].model_dump() == {
        "table": "ItemSparse",
        "records": 9,
        "added": 3,
        "replaced": 1,
        "removed": 1,
        "left_alone": 2,
    }
    assert (counts["Item"].added, counts["Item"].removed) == (1, 1)
    assert report.rows_added == 4 and report.rows_from_cache == 5
    sparse = _rows(overlay, tables["ItemSparse"])
    assert [(r["ID"], r["Display_lang"]) for r in sparse] == [
        ("1", "One"),
        ("2", "Two, fixed"),
        ("7", "Seven"),
        ("8", "Eight"),
        ("9", "Nine"),
    ]
    marked = [r["ID"] for r in sparse if r.get(hotfix.SOURCE_KEY) == hotfix.SOURCE_HOTFIX]
    assert marked == ["2", "7", "8", "9"]
    assert [r["ID"] for r in _rows(overlay, tables["Item"])] == ["1", "2", "4", "5", "6"]
    # A table the cache has nothing for passes through untouched.
    assert list(overlay.rows("Faction", iter([{"ID": "1"}]))) == [{"ID": "1"}]


def test_a_cache_of_another_build_is_refused_with_its_reason_constructed(
    tables: dict[str, Path],
) -> None:
    records = [Rec("ItemSparse", 9, _data("Nine"))]
    overlay = gamedata.overlay_hotfixes(_cache(71100, records), BUILD, tables.get, tables=ITEMS)
    report = overlay.report()
    assert not report.applied and report.tables == []
    assert report.cache_build == 71100
    assert report.refused == (
        "the hotfix cache is of build 71100 and the game tables of 1.60.1.71037; wowlab lays "
        "a hotfix cache only over the game tables of its own build"
    )
    rows = _rows(overlay, tables["ItemSparse"])
    assert [r["ID"] for r in rows] == ["1", "2", "5"]
    assert all(hotfix.SOURCE_KEY not in r for r in rows)


def test_other_refusals_constructed(tables: dict[str, Path], tmp_path: Path) -> None:
    good = _cache(NUMBER, [Rec("ItemSparse", 9, _data("Nine"))])
    assert gamedata.overlay_hotfixes(None, BUILD, tables.get).report().refused == (
        "this game folder has no hotfix cache"
    )
    missing = gamedata.overlay_hotfixes(
        good, BUILD, {"Item": tables["Item"]}.get, tables=ITEMS
    ).report()
    assert missing.refused == "the game table ItemSparse of 1.60.1.71037 is not in wowlab's cache"
    # A table whose columns are not the layout's: another layout, not decoded.
    other = tmp_path / "other"
    other.mkdir()
    (other / "ItemSparse.csv").write_text("ID,Display_lang\n1,One\n", encoding="utf-8")
    wrong = {"Item": tables["Item"], "ItemSparse": other / "ItemSparse.csv"}
    refused = gamedata.overlay_hotfixes(good, BUILD, wrong.get, tables=ITEMS).report().refused
    assert refused is not None and "are not the ones wowlab decodes" in refused
    # A row that does not decode refuses the whole overlay.
    bad = _cache(NUMBER, [Rec("ItemSparse", 9, b"short\0")])
    refused = gamedata.overlay_hotfixes(bad, BUILD, tables.get, tables=ITEMS).report().refused
    assert refused is not None and "the ItemSparse row 9" in refused
    given = gamedata.overlay_hotfixes(good, BUILD, tables.get, refused="it is locked").report()
    assert (given.applied, given.refused, given.cache_build) == (False, "it is locked", NUMBER)
    with pytest.raises(ValueError, match="not a full build string"):
        gamedata.overlay_hotfixes(good, "71037", tables.get)
    with pytest.raises(ValueError, match="no hotfix layout for Spell"):
        gamedata.overlay_hotfixes(good, BUILD, tables.get, tables=["Spell"])
    # Every table with a layout, by default: TraitNodeEntry is not given here.
    default = gamedata.overlay_hotfixes(good, BUILD, tables.get).report()
    assert default.refused == (
        "the game table TraitNodeEntry of 1.60.1.71037 is not in wowlab's cache"
    )


def test_flavor_hotfixes_reads_the_flavor_folder_constructed(
    tables: dict[str, Path], tmp_path: Path
) -> None:
    root = tmp_path / "install"
    flavor = root / "_flavor_"
    folder = flavor / "Cache" / "ADB" / "enUS"
    folder.mkdir(parents=True)
    assert gamedata.flavor_hotfixes(flavor, root, BUILD, tables.get).report().file is None
    path = folder / hotfix.CACHE_FILE
    data = cache(NUMBER, [Rec("ItemSparse", 9, _data("Nine"))])
    path.write_bytes(data)
    report = gamedata.flavor_hotfixes(flavor, root, BUILD, tables.get, tables=ITEMS).report()
    assert report.applied and report.file == "Cache/ADB/enUS/DBCache.bin"
    assert report.rows_added == 1
    assert path.read_bytes() == data  # read, never written (L1)
    path.write_bytes(b"XFTH" + bytes(4))
    broken = gamedata.flavor_hotfixes(flavor, root, BUILD, tables.get, tables=ITEMS).report()
    assert not broken.applied
    assert broken.refused is not None
    assert broken.refused.startswith("Cache/ADB/enUS/DBCache.bin cannot be read: ")


def test_cached_table_path_never_fetches(tmp_path: Path) -> None:
    assert gamedata.cached_table_path("Item", BUILD, cache_dir=tmp_path) is None
    folder = tmp_path / "tables" / BUILD
    folder.mkdir(parents=True)
    (folder / "Item.csv").write_text("ID\n", encoding="utf-8")
    assert gamedata.cached_table_path("Item", BUILD, cache_dir=tmp_path) == folder / "Item.csv"
