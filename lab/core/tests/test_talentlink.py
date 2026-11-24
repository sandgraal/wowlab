"""`talentlink`: "Open my talents" on WoW Forever Builds (M21-07).

Graded on real data: the game tables recorded from wago.tools at
1.60.1.70888 (`fixtures/wago/TraitNode`, `TraitNodeGroup`,
`TraitNodeGroupXTraitNode`, and the classic `Talent` and `TalentTab` the
layout does not use; provenance rows in the fixture index) and the lab-addon
record of the owner's warlock in the `macos-70888` capture (Sandgraal
Rizzen, level 13: 2 points in Suppression and 2 in Improved Corruption).
The expected address is the `link_sites.toml` example built from that
record, **[verify]** until the owner opens it (M21-O2).

The site's own layout was read by hand on 2026-11-24 (its nine class pages,
`docs/DATA_SOURCES.md`); what the tests keep of it is derived, never its
text: each class's talent count per tree. Cases labelled `constructed`
change one value of the real record, or build a table to break a rule (L8).
Nothing is fetched and no install is read or written: the cache is a folder
in `tmp_path`.
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest

from wowlab_core import gamedata, labaddon, links, talentlink

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WAGO = FIXTURES / "wago"
BUILD = "1.60.1.70888"  # the recorded tables' and the record's game version
RECORD = (
    FIXTURES
    / "macos-70888"
    / "forever"
    / "WTF"
    / "Account"
    / "LABACCOUNTA"
    / "1"
    / "Sandgraal-Rizzen"
    / "SavedVariables"
    / "WowLab.lua"
)
EXPECTED = "https://wowforeverbuilds.com/talents/warlock?b=022&race=undead"  # [verify] M21-O2
# Each class's trait tree at 1.60.1.70888 (tests may name ids, L6).
CLASS_TREES = {
    "warrior": 1117,
    "paladin": 1100,
    "hunter": 1091,
    "rogue": 1111,
    "priest": 1114,
    "shaman": 1082,
    "mage": 1112,
    "warlock": 1116,
    "druid": 1089,
}
# The site's talents per tree, in its order, read from its pages on
# 2026-11-24 (their footer: "Data: Live client 1.60.1.70707, last reviewed Nov 1,
# 2026"; the calculator script names 1.60.1.70862).
SITE_SIZES = {
    "warrior": [17, 18, 18],
    "paladin": [17, 17, 17],
    "hunter": [16, 16, 18],
    "rogue": [17, 19, 18],
    "priest": [18, 17, 17],
    "shaman": [16, 18, 16],
    "mage": [18, 17, 18],
    "warlock": [17, 19, 16],
    "druid": [16, 20, 16],
}
# Nodes the 70888 tables hold that the site does not: each placed far
# outside the drawn grid (hunter Marksmanship and Survival, priest Holy).
PARKED = {"hunter": {1: 105003, 2: 104982}, "priest": {1: 105865}}
WARLOCK_TREE = 1116
SUPPRESSION, IMPROVED_CORRUPTION, FIRST_ROW_FIRST = 105925, 105924, 105921


def _rows(table: str) -> list[dict[str, str]]:
    with (WAGO / f"{table}.{BUILD}.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def layout() -> talentlink.Layout:
    return talentlink.layout_of(
        _rows("TraitNode"), _rows("TraitNodeGroup"), _rows("TraitNodeGroupXTraitNode")
    )


def _record(*replacements: tuple[str, str]) -> labaddon.CharDB:
    data = RECORD.read_bytes()
    for old, new in replacements:
        assert data.count(old.encode()) == 1, old
        data = data.replace(old.encode(), new.encode())
    return labaddon.parse_char(data)


def _rank_of(node: int, rank: int) -> tuple[str, str]:
    """The record's current rank of `node` (2 in the capture) set to `rank`
    (the file's lines end in CRLF)."""
    return (
        f'["current_rank"] = 2,\r\n["id"] = {node},',
        f'["current_rank"] = {rank},\r\n["id"] = {node},',
    )


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    return tmp_path / "m21-07-cache"


def _cached(cache: Path, build: str = BUILD, tables: tuple[str, ...] = talentlink.TABLES) -> None:
    """The recorded tables placed in the cache under `build` (constructed)."""
    folder = cache / "tables" / build
    folder.mkdir(parents=True, exist_ok=True)
    for table in tables:
        shutil.copy2(WAGO / f"{table}.{BUILD}.csv", folder / f"{table}.csv")


# ─── the layout ──────────────────────────────────────────────────────────────


def test_the_layout_tables_are_the_ones_gamedata_names() -> None:
    assert talentlink.TABLES == gamedata.TALENT_TABLES
    assert talentlink.TABLES == ("TraitNode", "TraitNodeGroup", "TraitNodeGroupXTraitNode")


def test_every_class_tree_splits_into_the_sites_three_trees(layout: talentlink.Layout) -> None:
    for klass, tree in CLASS_TREES.items():
        assert tree not in layout.unsplit, klass
        tabs = layout.trees[tree]
        sizes = [len(tab) for tab in tabs]
        parked = PARKED.get(klass, {})
        expected = [n + (1 if i in parked else 0) for i, n in enumerate(SITE_SIZES[klass])]
        assert sizes == expected, klass
        # The extra nodes sort after every talent of their tree, so with no
        # rank in them the code is the site's.
        for i, node in parked.items():
            assert tabs[i][-1] == node, klass


def test_the_warlock_trees_are_affliction_demonology_destruction_left_to_right(
    layout: talentlink.Layout,
) -> None:
    affliction, demonology, destruction = layout.trees[WARLOCK_TREE]
    # Row 0 of Affliction, left to right: Improved Life Tap, Suppression,
    # Improved Corruption (the record's entries 130655 and 130654 are
    # definitions 135456 and 135455: spells 18174 and 17810).
    assert affliction[:3] == [FIRST_ROW_FIRST, SUPPRESSION, IMPROVED_CORRUPTION]
    assert affliction[-1] == 105909  # Wrack, the last row's one talent
    assert len(demonology) == 19 and len(destruction) == 16
    assert all(layout.tree_of[n] == WARLOCK_TREE for tab in layout.trees[WARLOCK_TREE] for n in tab)


def test_a_row_takes_nodes_a_few_units_off_it(layout: talentlink.Layout) -> None:
    """At 70888 Affliction's third row holds one node at PosY 3320 and three
    at 3330; by raw position the one at 3320 (column 2) would come first."""
    nodes = {int(r["ID"]): r for r in _rows("TraitNode")}
    affliction = layout.trees[WARLOCK_TREE][0]
    third = [n for n in affliction if nodes[n]["PosY"] in ("3320", "3330")]
    assert [nodes[n]["PosX"] for n in third] == ["1020", "1620", "2220", "2820"]
    assert nodes[third[2]]["PosY"] == "3320"


def test_the_classic_talent_tables_are_not_forevers_layout() -> None:
    """Why the layout is read from the trait tables (the ticket named
    `Talent` and `TalentTab`): at 70888 they hold the classic Affliction
    tree. Its first row has nothing in column 0, where the game draws
    Improved Life Tap (classic: row 1, column 2), so the classic order would
    write the record's two talents as `22`, Improved Life Tap and
    Suppression on the site."""
    tabs = {r["ID"]: r for r in _rows("TalentTab")}
    assert tabs["302"]["Name_lang"] == "Affliction" and tabs["302"]["OrderIndex"] == "0"
    talent = {r["ID"]: r for r in _rows("Talent") if r["TabID"] == "302"}
    assert (talent["1007"]["TierID"], talent["1007"]["ColumnIndex"]) == ("1", "2")
    assert not any(r["TierID"] == "0" and r["ColumnIndex"] == "0" for r in talent.values())
    by_spell = {r["SpellRank_0"]: r for r in talent.values()}
    ordered = sorted(talent.values(), key=lambda r: (int(r["TierID"]), int(r["ColumnIndex"])))
    ranks = {by_spell["18174"]["ID"]: 2, by_spell["17810"]["ID"]: 2}  # Suppression, Imp. Corruption
    classic = "".join(str(ranks.get(r["ID"], 0)) for r in ordered).rstrip("0")
    assert classic == "22"


def test_groups_that_do_not_split_a_tree_leave_it_unsplit_constructed() -> None:
    nodes = [
        {"ID": str(n), "TraitTreeID": "7", "PosX": str(100 * n), "PosY": "0"} for n in (1, 2, 3)
    ]
    groups = [{"ID": "70", "TraitTreeID": "7"}, {"ID": "71", "TraitTreeID": "7"}]
    overlapping = [
        {"TraitNodeGroupID": "70", "TraitNodeID": "1"},
        {"TraitNodeGroupID": "70", "TraitNodeID": "2"},
        {"TraitNodeGroupID": "71", "TraitNodeID": "2"},
        {"TraitNodeGroupID": "71", "TraitNodeID": "3"},
    ]
    made = talentlink.layout_of(nodes, groups, overlapping)
    assert 7 in made.unsplit and 7 not in made.trees
    # A node no group holds leaves it unsplit too.
    partial = talentlink.layout_of(nodes, groups[:1], overlapping[:2])
    assert 7 in partial.unsplit
    with pytest.raises(ValueError, match="trees wowlab can read"):
        talentlink.code_of({1: 1}, made, build=BUILD)


def test_rows_with_damaged_numbers_are_skipped_constructed() -> None:
    nodes = [
        {"ID": "1", "TraitTreeID": "7", "PosX": "0", "PosY": "0"},
        {"ID": "2", "TraitTreeID": "7", "PosX": "x", "PosY": "0"},
        {"ID": "3", "TraitTreeID": "7", "PosX": "600", "PosY": ""},
    ]
    groups = [{"ID": "70", "TraitTreeID": "7"}]
    members = [{"TraitNodeGroupID": "70", "TraitNodeID": n} for n in ("1", "2", "3")]
    made = talentlink.layout_of(nodes, groups, members)
    assert made.trees == {7: [[1]]} and set(made.tree_of) == {1}


# ─── the code ────────────────────────────────────────────────────────────────


def test_the_owners_warlock_gives_022(layout: talentlink.Layout) -> None:
    record = labaddon.read_char(RECORD)
    ranks = talentlink.ranks_of(record)
    assert ranks == {IMPROVED_CORRUPTION: 2, SUPPRESSION: 2}
    assert talentlink.code_of(ranks, layout, build=BUILD) == "022"


def test_trailing_zeros_and_empty_trees_are_cut_and_inner_ones_kept(
    layout: talentlink.Layout,
) -> None:
    affliction, demonology, destruction = layout.trees[WARLOCK_TREE]
    assert talentlink.code_of({destruction[0]: 2}, layout, build=BUILD) == "--2"
    assert talentlink.code_of({demonology[2]: 5}, layout, build=BUILD) == "-005"
    both = {affliction[0]: 2, demonology[0]: 1}
    assert talentlink.code_of(both, layout, build=BUILD) == "2-1"


def test_an_unknown_talent_gives_no_code_and_names_it_constructed(
    layout: talentlink.Layout,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"talent 999999 of the record is not in the game tables of 1\.60\.1\.70888",
    ):
        talentlink.code_of({SUPPRESSION: 2, 999999: 1}, layout, build=BUILD)
    with pytest.raises(ValueError, match=r"talents 999998, 999999 of the record are not in"):
        talentlink.code_of({999999: 1, 999998: 1}, layout, build=BUILD)


def test_a_rank_over_nine_gives_no_code_constructed(layout: talentlink.Layout) -> None:
    with pytest.raises(ValueError, match="one digit"):
        talentlink.code_of({SUPPRESSION: 10}, layout, build=BUILD)


def test_talents_of_two_trees_give_no_code_constructed(layout: talentlink.Layout) -> None:
    warrior_node = layout.trees[CLASS_TREES["warrior"]][0][0]
    with pytest.raises(ValueError, match="more than one"):
        talentlink.code_of({SUPPRESSION: 1, warrior_node: 1}, layout, build=BUILD)


# ─── the link ────────────────────────────────────────────────────────────────


def test_the_recorded_example_is_the_owners_record(cache: Path) -> None:
    _cached(cache)
    made = talentlink.talents_link(labaddon.read_char(RECORD), version=BUILD, cache_dir=cache)
    assert isinstance(made.link, links.Link)
    assert made.link.url == EXPECTED
    assert made.ranks == "022" and made.tables_build == made.record_build == BUILD
    assert made.table_note is None and made.download == [] and made.download_build is None
    examples = [e.address for e in links.load_link_sites().example if e.link == "talents"]
    assert EXPECTED in examples


def test_without_the_tables_there_is_no_link_and_the_download_is_named(cache: Path) -> None:
    made = talentlink.talents_link(labaddon.read_char(RECORD), version=BUILD, cache_dir=cache)
    assert isinstance(made.link, links.NoLink)
    assert made.link.why == f"the game's talent tables for {BUILD} are not downloaded"
    assert made.download == list(talentlink.TABLES) and made.download_build == BUILD
    assert not cache.exists()  # nothing was written


def test_a_partly_cached_build_asks_only_for_the_missing_tables(cache: Path) -> None:
    _cached(cache, tables=("TraitNode",))
    made = talentlink.talents_link(labaddon.read_char(RECORD), version=BUILD, cache_dir=cache)
    assert isinstance(made.link, links.NoLink)
    assert made.download == ["TraitNodeGroup", "TraitNodeGroupXTraitNode"]


def test_the_installs_tables_stand_in_and_the_note_says_so_constructed(cache: Path) -> None:
    other = "1.60.1.71037"
    _cached(cache, build=other)
    made = talentlink.talents_link(labaddon.read_char(RECORD), version=other, cache_dir=cache)
    assert isinstance(made.link, links.Link) and made.link.url == EXPECTED
    assert made.tables_build == other and made.record_build == BUILD
    assert made.table_note == (
        f"The record was saved by {BUILD}; its talents were placed with the game tables of "
        f"{other}, whose layout may differ."
    )


def test_a_record_without_class_talents_asks_for_no_download_constructed(cache: Path) -> None:
    record = labaddon.read_char(RECORD).model_copy(update={"talents": None})
    made = talentlink.talents_link(record, version=BUILD, cache_dir=cache)
    assert isinstance(made.link, links.NoLink)
    assert made.link.why.startswith("the class talent record is ")
    assert made.download == []


def test_no_points_spent_gives_no_link_constructed(cache: Path) -> None:
    _cached(cache)
    record = _record(_rank_of(SUPPRESSION, 0), _rank_of(IMPROVED_CORRUPTION, 0))
    made = talentlink.talents_link(record, version=BUILD, cache_dir=cache)
    assert isinstance(made.link, links.NoLink)
    assert made.link.why == "the record holds no talent points spent"


def test_an_unknown_talent_in_the_record_gives_no_link_constructed(cache: Path) -> None:
    _cached(cache)
    record = _record(('["id"] = 105924,', '["id"] = 999999,'))
    made = talentlink.talents_link(record, version=BUILD, cache_dir=cache)
    assert isinstance(made.link, links.NoLink)
    assert "999999" in made.link.why and made.tables_build == BUILD
    assert made.download == []


def test_a_client_with_no_room_asks_for_no_download_constructed(cache: Path) -> None:
    made = talentlink.talents_link(
        labaddon.read_char(RECORD), version="11.0.2.56000", cache_dir=cache
    )
    assert isinstance(made.link, links.NoLink)
    assert "no pages wowlab knows for this game version" in made.link.why
    assert made.download == []


def test_a_malformed_build_is_passed_over_constructed(cache: Path) -> None:
    _cached(cache)
    record = labaddon.read_char(RECORD)
    made = talentlink.cached_layout(["not a build", BUILD], cache_dir=cache)
    assert made.build == BUILD and made.layout is not None
    none = talentlink.cached_layout(["not a build", None], cache_dir=cache)
    assert none.layout is None and none.download == [] and none.why
    assert "cannot be used" in none.why
    assert talentlink.ranks_of(record)  # the record itself is unchanged


def test_the_module_fetches_nothing(cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_fetch(*_a: object, **_k: object) -> object:
        raise AssertionError("talentlink fetched a table")

    monkeypatch.setattr(gamedata.GameData, "table", no_fetch)
    made = talentlink.talents_link(labaddon.read_char(RECORD), version=BUILD, cache_dir=cache)
    assert isinstance(made.link, links.NoLink)
