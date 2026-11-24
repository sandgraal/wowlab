"""`lookup`: search and item sources over QuestieDB and the game tables
(M22-08).

QuestieDB is a CONSTRUCTED slice (`_questie_build.py`; no byte of the
third-party data is committed, as the ticket rules). The game tables are
the real recordings `fixtures/wago/ItemSparse.1.60.1.71037.subset.csv` and
`AreaTable.1.60.1.71037.subset.csv` (provenance rows in
`fixtures/README.md`), placed in a cache under `tmp_path`. Nothing reads or
writes a real install, and nothing is fetched.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from _questie_build import FLAVOR, VERSION, place, slice_toc, toc_text

from wowlab_core import gamedata, lookup

WAGO = Path(__file__).resolve().parent / "fixtures" / "wago"


@pytest.fixture
def flavor(tmp_path: Path) -> Path:
    path = tmp_path / "World of Warcraft" / FLAVOR
    place(path, slice_toc())
    return path


def _cache(tmp_path: Path, *tables: str) -> Path:
    cache = tmp_path / "gamedata"
    folder = cache / "tables" / VERSION
    folder.mkdir(parents=True, exist_ok=True)
    for table in tables:
        shutil.copy2(WAGO / f"{table}.{VERSION}.subset.csv", folder / f"{table}.csv")
    return cache


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    return _cache(tmp_path, "ItemSparse", "AreaTable")


def _index(flavor: Path, cache: Path, version: str | None = VERSION) -> lookup.LookupIndex:
    return lookup.LookupIndex.for_flavor(flavor, version, cache_dir=cache)


# ─── search ──────────────────────────────────────────────────────────────────


def test_search_ranks_an_exact_name_then_a_start_then_a_word_constructed(
    flavor: Path, cache: Path
) -> None:
    index = _index(flavor, cache)
    hits, total = index.search("sharptalon")
    assert total == 3
    assert [(h.kind, h.id) for h in hits] == [("npc", 12676), ("item", 16305), ("quest", 2)]
    hits, _ = index.search("Claw")
    # A word that starts with the query, items before quests at one rank.
    assert [(h.kind, h.id) for h in hits] == [("item", 16305), ("quest", 2)]
    # Inside a word only: one rank, so items, NPCs, quests, objects in turn.
    hits, _ = index.search("arptal")
    assert [h.id for h in hits] == [16305, 12676, 2]


def test_search_by_id_and_by_kind_and_the_limit_constructed(flavor: Path, cache: Path) -> None:
    index = _index(flavor, cache)
    hits, _ = index.search("2")
    # The quest whose id is 2 first; then the names holding a 2 (none here).
    assert [(h.kind, h.id) for h in hits][:1] == [("quest", 2)]
    hits, total = index.search("innkeeper", kinds=["npc"], limit=1)
    assert total == 2 and len(hits) == 1 and hits[0].kind == "npc"
    assert index.search("innkeeper", kinds=["item"]) == ([], 0)
    assert index.search("   ") == ([], 0)
    assert index.search("SHARPTALON'S CLAW", kinds=["quest"])[0][0].id == 2


def test_a_game_table_item_quest_db_lacks_is_found_constructed(flavor: Path, cache: Path) -> None:
    index = _index(flavor, cache)
    (hit,) = index.search("guild tabard")[0]
    assert (hit.kind, hit.id, hit.name, hit.sources) == (
        "item",
        5976,
        "Guild Tabard",
        ["game_table"],
    )
    assert hit.item is not None
    assert (hit.item.in_questiedb, hit.item.in_game_table, hit.item.quality) == (False, True, 1)
    assert hit.item.drops == [] and hit.item.vendors == []


# ─── item sources ────────────────────────────────────────────────────────────


def test_an_item_that_drops_and_starts_a_quest_constructed(flavor: Path, cache: Path) -> None:
    sources = _index(flavor, cache).item_sources(16305)
    assert sources is not None
    assert (sources.name, sources.item_level, sources.required_level) == (
        "Sharptalon's Claw",
        20,
        20,
    )
    assert sources.quality == 2  # from the recorded ItemSparse row
    assert sources.in_questiedb and sources.in_game_table
    (drop,) = sources.drops
    assert (drop.npc.id, drop.npc.name, drop.npc.level_min, drop.chances) == (
        12676,
        "Sharptalon",
        31,
        {},
    )
    (where,) = drop.npc.places
    assert (where.zone_id, where.zone, where.count, where.points) == (
        331,
        "Ashenvale",
        1,
        [(74.98, 70.06)],
    )
    assert sources.starts_quest is not None
    assert (sources.starts_quest.id, sources.starts_quest.level, sources.starts_quest.zone) == (
        2,
        30,
        "Ashenvale",
    )


def test_vendors_and_quest_rewards_with_their_places_constructed(flavor: Path, cache: Path) -> None:
    sources = _index(flavor, cache).item_sources(159)
    assert sources is not None
    assert [(v.name, v.subname, v.friendly_to) for v in sources.vendors] == [
        ("Innkeeper Farley", "Innkeeper", "A"),
        ("Innkeeper Grosk", "Innkeeper", "H"),
    ]
    grosk = sources.vendors[1]
    # Places by how many spots each zone has.
    assert [(p.zone, p.count) for p in grosk.places] == [("Orgrimmar", 2), ("Durotar", 1)]
    (reward,) = sources.quest_rewards
    assert (reward.id, reward.name, reward.zone) == (8, "A Rogue's Deal", "Elwynn Forest")


def test_drop_chances_objects_and_containers_constructed(flavor: Path, cache: Path) -> None:
    sources = _index(flavor, cache).item_sources(2589)
    assert sources is not None
    (drop,) = sources.drops
    assert drop.npc.name == "Flesh Eater"
    # In the order Questie consults them: the server table, then Wowhead's.
    assert list(drop.chances.items()) == [("cmangosData", 10.0), ("wowheadData", 12.5)]
    # Its correction points at Wowhead's figure, which Questie then shows.
    assert (drop.chance, drop.chance_from, drop.correction) == (12.5, "wowheadData", None)
    assert [p.zone for p in drop.npc.places] == ["Duskwood"]
    assert [q.name for q in sources.related_quests] == ["A Rogue's Deal"]
    (box,) = sources.object_drops
    assert (box.name, box.places[0].zone) == ("Old Lion Statue", "Elwynn Forest")
    assert [(c.id, c.name) for c in sources.containers] == [(6827, "Box of Supplies")]


def test_the_figure_questie_shows_follows_its_corrections_constructed(
    flavor: Path, cache: Path
) -> None:
    sources = _index(flavor, cache).item_sources(725)
    assert sources is not None
    by_npc = {d.npc.id: d for d in sources.drops}
    # A correction that is a figure wins over both tables.
    flesh = by_npc[3]
    assert (flesh.chance, flesh.chance_from, flesh.correction) == (50.0, "correction", 50.0)
    assert flesh.chances == {"wowheadData": 45.04}
    # A correction that points at the server table shows that table's figure.
    sharp = by_npc[12676]
    assert (sharp.chance, sharp.chance_from) == (80.0, "cmangosData")
    assert list(sharp.chances) == ["cmangosData", "wowheadData"]
    # Sorted by the figure Questie shows.
    assert [d.npc.id for d in sources.drops] == [12676, 3]


def test_no_correction_takes_the_server_figure_then_wowheads_constructed(
    tmp_path: Path, cache: Path
) -> None:
    flavor = tmp_path / "plain" / FLAVOR
    place(flavor, slice_toc())
    corrections = flavor / "Interface" / "AddOns" / "QuestieDB" / "support" / "Forever"
    (corrections / "DropTables" / "itemDropCorrections.lua").write_bytes(b"-- none\n")
    sources = _index(flavor, cache).item_sources(725)
    assert sources is not None
    by_npc = {d.npc.id: d for d in sources.drops}
    assert (by_npc[12676].chance, by_npc[12676].chance_from) == (80.0, "cmangosData")
    assert (by_npc[3].chance, by_npc[3].chance_from) == (45.04, "wowheadData")


def test_an_item_handed_over_with_a_quest_constructed(flavor: Path, cache: Path) -> None:
    sources = _index(flavor, cache).item_sources(745)
    assert sources is not None
    (given,) = sources.given_by_quests
    assert (given.id, given.name, given.level, given.zone) == (
        54,
        "Report to Goldshire",
        0,
        "Elwynn Forest",
    )


def test_an_item_no_source_knows_is_none_constructed(flavor: Path, cache: Path) -> None:
    assert _index(flavor, cache).item_sources(99999) is None


def test_the_module_function_answers_several_items_at_once_constructed(
    flavor: Path, cache: Path
) -> None:
    got = lookup.item_sources(flavor, VERSION, [16305, 5976, 99999], cache_dir=cache)
    assert sorted(got) == [5976, 16305]
    assert got[16305].drops[0].npc.name == "Sharptalon"
    assert got[5976].name == "Guild Tabard"


# ─── NPCs, quests, objects ───────────────────────────────────────────────────


def test_an_npc_with_its_place_and_quests_constructed(flavor: Path, cache: Path) -> None:
    detail = _index(flavor, cache).npc(1000)
    assert detail is not None
    assert (detail.npc.name, detail.npc.subname, detail.npc.level_min) == (
        "Innkeeper Farley",
        "Innkeeper",
        30,
    )
    assert [p.zone for p in detail.npc.places] == ["Elwynn Forest"]
    assert [q.id for q in detail.starts] == [8] and [q.id for q in detail.ends] == [8]


def test_spawns_in_an_array_and_a_zone_with_no_name_constructed(flavor: Path, cache: Path) -> None:
    detail = _index(flavor, cache).npc(1002)
    assert detail is not None
    (where,) = detail.npc.places
    assert (where.zone_id, where.zone, where.points) == (1, None, [(10.0, 20.0)])


def test_a_quest_with_givers_finishers_rewards_and_zone_constructed(
    flavor: Path, cache: Path
) -> None:
    index = _index(flavor, cache)
    claw = index.quest(2)
    assert claw is not None
    assert [i.name for i in claw.started_by_items] == ["Sharptalon's Claw"]
    assert [n.name for n in claw.finished_by_npcs] == ["Senani Thunderheart"]
    deal = index.quest(8)
    assert deal is not None
    assert [n.name for n in deal.started_by_npcs] == ["Innkeeper Farley"]
    # The items whose rows name the quest as a reward, in id order.
    assert [i.name for i in deal.rewards] == ["Worn Shortsword", "Refreshing Spring Water"]
    trial = index.quest(9)
    assert trial is not None and trial.quest.zone_id is None and trial.quest.zone is None
    assert trial.quest.sort_id == -61


def test_an_npc_with_no_spot_has_an_estimated_zone_constructed(flavor: Path, cache: Path) -> None:
    detail = _index(flavor, cache).npc(1003)
    assert detail is not None
    (where,) = detail.npc.places
    assert (where.zone, where.estimated, where.count, where.points) == ("Durotar", True, 0, [])


def test_an_area_questiedb_numbers_itself_is_never_named_from_the_zone_table_constructed(
    flavor: Path, cache: Path
) -> None:
    detail = _index(flavor, cache).npc(11441)
    assert detail is not None
    (where,) = detail.npc.places
    assert (where.zone_id, where.zone, where.own_area, where.points) == (
        10022,
        "Dire Maul Gordok Commons",
        True,
        [(-1.0, -1.0)],
    )
    # A zone the game table names keeps the game's name.
    farley = _index(flavor, cache).npc(1000)
    assert farley is not None and farley.npc.places[0].own_area is False


def test_without_the_zone_table_an_own_area_is_only_a_number_constructed(
    flavor: Path, tmp_path: Path
) -> None:
    detail = _index(flavor, _cache(tmp_path, "ItemSparse")).npc(11441)
    assert detail is not None
    (where,) = detail.npc.places
    assert (where.zone, where.own_area) == (None, False)


@pytest.mark.parametrize(
    ("interfaces", "read", "chosen_by"),
    [
        (("11508", "11508"), "QuestieDB_A.toc", "name_order"),
        (("11508", "16001"), "QuestieDB_B.toc", "interface"),
    ],
)
def test_several_tocs_with_data_pick_by_interface_then_name_constructed(
    tmp_path: Path,
    cache: Path,
    interfaces: tuple[str, str],
    read: str,
    chosen_by: str,
) -> None:
    flavor = tmp_path / "two" / FLAVOR
    for name, interface in zip(("QuestieDB_A.toc", "QuestieDB_B.toc"), interfaces, strict=True):
        toc = slice_toc().replace(b"## Interface: 11508", f"## Interface: {interface}".encode())
        place(flavor, toc, name=name)
    # An empty third TOC holds no store and is not counted.
    (flavor / "Interface" / "AddOns" / "QuestieDB" / "QuestieDB.toc").write_bytes(b"## Title: x\n")
    state = _index(flavor, cache, version="1.60.1.71037").questie
    assert state.info is not None and state.info.toc == read
    assert state.tocs == ["QuestieDB_A.toc", "QuestieDB_B.toc"] and state.chosen_by == chosen_by


def test_one_toc_is_chosen_by_only_constructed(flavor: Path, cache: Path) -> None:
    state = _index(flavor, cache).questie
    assert state.tocs == ["QuestieDB_Forever.toc"] and state.chosen_by == "only"


def test_an_object_with_its_place_and_quest_constructed(flavor: Path, cache: Path) -> None:
    detail = _index(flavor, cache).object(31)
    assert detail is not None
    assert detail.object.places[0].points == [(79.4, 46.83)]
    assert [q.name for q in detail.starts] == ["A Rogue's Deal"]


# ─── sources and their states ────────────────────────────────────────────────


def test_states_when_everything_is_read_constructed(flavor: Path, cache: Path) -> None:
    index = _index(flavor, cache)
    index.item_sources(2589)
    state = index.questie
    assert state.state == "read" and state.info is not None and state.info.version == "1.1.4"
    assert [x.part for x in state.left_out] == [
        "the corrections Questie picks by the character's faction and class",
        "names in other languages",
        "the drop corrections Tbc in support\\Forever\\DropTables\\itemDropCorrections.lua",
        "the drop corrections Cata in support\\Forever\\DropTables\\itemDropCorrections.lua",
    ]
    assert state.unreadable_rows == 0
    assert [(t.table, t.state) for t in index.game_tables()] == [
        ("ItemSparse", "cached"),
        ("AreaTable", "cached"),
    ]


def test_without_the_game_tables_zones_are_ids_and_the_tables_missing_constructed(
    flavor: Path, tmp_path: Path
) -> None:
    index = _index(flavor, tmp_path / "empty")
    sources = index.item_sources(16305)
    assert sources is not None
    assert sources.quality is None and not sources.in_game_table
    assert sources.drops[0].npc.places[0].zone is None
    assert [(t.table, t.state) for t in index.game_tables()] == [
        ("ItemSparse", "missing"),
        ("AreaTable", "missing"),
    ]
    assert index.search("guild tabard") == ([], 0)


def test_no_version_means_no_game_tables_constructed(flavor: Path, cache: Path) -> None:
    index = _index(flavor, cache, version=None)
    assert [t.state for t in index.game_tables()] == ["no_version", "no_version"]
    assert index.search("sharptalon")[1] == 3


def test_a_malformed_cached_table_is_an_error_not_a_crash_constructed(
    flavor: Path, tmp_path: Path
) -> None:
    cache = _cache(tmp_path, "AreaTable")
    (cache / "tables" / VERSION / "ItemSparse.csv").write_bytes(b"ID,Display_lang\n1,a,b\n")
    index = _index(flavor, cache)
    assert index.search("guild tabard") == ([], 0)
    states = {t.table: t for t in index.game_tables()}
    assert states["ItemSparse"].state == "error" and "fields" in (states["ItemSparse"].detail or "")
    assert states["AreaTable"].state == "cached"


def test_no_questiedb_still_searches_the_item_table_constructed(
    tmp_path: Path, cache: Path
) -> None:
    flavor = tmp_path / "bare" / FLAVOR
    flavor.mkdir(parents=True)
    index = _index(flavor, cache)
    assert index.questie.state == "not_installed"
    (hit,) = index.search("linen")[0]
    assert (hit.id, hit.sources) == (2589, ["game_table"])
    assert index.npc(12676) is None and index.quest(2) is None and index.object(31) is None


def test_a_questiedb_with_no_store_is_not_baked_constructed(tmp_path: Path, cache: Path) -> None:
    flavor = tmp_path / "source-mode" / FLAVOR
    place(flavor, b"## Title: QuestieDB\n## X-Mode: source\nsrc\\api.lua\n")
    assert _index(flavor, cache).questie.state == "not_baked"


def test_an_unreadable_questiedb_toc_is_said_constructed(
    tmp_path: Path, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flavor = tmp_path / "big" / FLAVOR
    place(flavor, toc_text({}))
    monkeypatch.setattr(lookup.questiedb, "MAX_STORE_BYTES", 8)
    state = _index(flavor, cache).questie
    assert state.state == "unreadable" and "larger than 8 bytes" in (state.detail or "")


def test_the_index_reads_the_cache_and_never_fetches_constructed(
    flavor: Path, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("no fetch from a lookup")

    monkeypatch.setattr(gamedata.GameData, "table", refuse)
    monkeypatch.setattr(gamedata, "WagoSource", refuse)
    index = _index(flavor, cache)
    index.search("a")
    index.item_sources(159)
