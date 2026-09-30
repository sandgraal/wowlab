"""The Wave 3 recordings (M12-01) join to each other and to the captures.

Graded on the committed wago.tools recordings and the committed, scrubbed
lab-addon captures only; nothing here makes a request (ADR-0012). What each
file is, and how the subsets were cut, is in the fixture index rows.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from wowlab_core import labaddon

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
WAGO = FIXTURES / "wago"
B70058 = "1.60.1.70058"
B70009 = "1.60.1.70009"
CHAR = "forever/WTF/Account/90000001#6/1/{}/SavedVariables/WowLab.lua"
CAPTURE_70058 = FIXTURES / "macos-70058" / CHAR.format("Labchard-Labrealmg")
CAPTURES_70009 = (
    FIXTURES / "macos" / CHAR.format("Labchard-Labrealmg"),
    FIXTURES / "macos" / CHAR.format("Labcharb-Labrealmf"),
)
# The captures the CurrencyTypes, SkillLine and ChrSpecialization subsets were
# cut for (the index rows): every committed per-character lab-addon file.
CAPTURES = (CAPTURE_70058, *CAPTURES_70009)

TRAIT_TABLES = (
    "TraitTree",
    "TraitNode",
    "TraitNodeEntry",
    "TraitDefinition",
    "TraitNodeXTraitNodeEntry",
    "TraitEdge",
    "TraitCond",
    "TraitCurrency",
    "TraitTreeXTraitCurrency",
)
SPELL_COLUMNS = ("SpellID", "OverridesSpellID", "VisibleSpellID")
# Spells `TraitDefinition` names that the full `SpellName` download at 70058
# (832295 bytes, sha256 b9a0d125…, the SpellName subset's index row) has no
# line for: `wago_subset.py` printed them as "values with no line". The subset
# cannot hold a row the source lacks; the set is pinned so a new one is seen.
NOT_IN_SPELLNAME_70058 = frozenset({"20234", "22571", "407669", "426158", "1309389", "1310310"})
MAX_FILE_BYTES = 512 * 1024


def _rows(name: str) -> list[dict[str, str]]:
    with (WAGO / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _header(name: str) -> list[str]:
    with (WAGO / name).open(newline="", encoding="utf-8") as handle:
        return next(csv.reader(handle))


def _char(path: Path) -> labaddon.CharDB:
    return labaddon.read_char(path)


def _class_trees(char: labaddon.CharDB) -> list[labaddon.TraitTree]:
    assert char.talents is not None
    section = char.talents.class_
    assert isinstance(section, labaddon.ClassTalents), "the capture has class talents"
    assert isinstance(section.config, labaddon.TraitConfig)
    return list(section.config.trees)


def _legacy_trees(char: labaddon.CharDB) -> list[labaddon.TraitTree]:
    assert char.talents is not None
    section = char.talents.legacy
    if not isinstance(section, labaddon.LegacyTalents):
        return []
    return [
        tree
        for config in section.configs
        if isinstance(config, labaddon.LegacyConfig)
        for tree in config.trees
    ]


def _nodes_of(tree_id: int, build: str) -> set[int]:
    return {
        int(r["ID"]) for r in _rows(f"TraitNode.{build}.csv") if r["TraitTreeID"] == str(tree_id)
    }


# ─── every file ──────────────────────────────────────────────────────────────


@pytest.mark.parser
def test_every_wago_recording_is_under_the_repository_file_limit() -> None:
    sizes = {p.name: p.stat().st_size for p in WAGO.iterdir() if p.is_file()}
    assert sizes
    over = {name: size for name, size in sizes.items() if size > MAX_FILE_BYTES}
    assert not over, f"over 512 KB: {over}"


@pytest.mark.parser
@pytest.mark.parametrize("table", TRAIT_TABLES)
def test_the_70009_trait_tables_are_byte_identical_to_70058(table: str) -> None:
    """What the 70009 index rows state; a cross-build test that expects a
    difference between the two builds would find none."""
    a = (WAGO / f"{table}.{B70009}.csv").read_bytes()
    b = (WAGO / f"{table}.{B70058}.csv").read_bytes()
    assert a == b


@pytest.mark.parser
def test_the_table_counts_are_the_plans() -> None:
    # docs/LAB_PLAN.md §14.1 (2026-09-29): 17 trees, 558 nodes, 96 edges.
    assert len(_rows(f"TraitTree.{B70058}.csv")) == 17
    assert len(_rows(f"TraitNode.{B70058}.csv")) == 558
    assert len(_rows(f"TraitEdge.{B70058}.csv")) == 96
    header = _header(f"TraitTree.{B70058}.csv")
    assert "Field_10_0_0_45697_006" in header and "Field_10_0_0_45697_007" in header


@pytest.mark.parser
def test_the_400_body_for_a_table_the_build_lacks_is_json_naming_the_table() -> None:
    body = (WAGO / f"TraitSubTree.{B70058}.400.json").read_bytes()
    assert json.loads(body) == {"errors": "Table not found."}


# ─── SpellName subset ────────────────────────────────────────────────────────


def _spell_ids() -> set[str]:
    return {
        r[c] for r in _rows(f"TraitDefinition.{B70058}.csv") for c in SPELL_COLUMNS if r[c] != "0"
    }


@pytest.mark.parser
def test_every_spell_the_trait_definitions_name_has_its_spellname_row() -> None:
    wanted = _spell_ids()
    assert "" not in wanted
    subset = {r["ID"] for r in _rows(f"SpellName.{B70058}.subset.csv")}
    missing = wanted - subset
    assert missing == NOT_IN_SPELLNAME_70058, (
        "every SpellID, OverridesSpellID and VisibleSpellID has its SpellName row, "
        f"except the ones the full download lacks; new gaps: {sorted(missing - NOT_IN_SPELLNAME_70058)}"
    )


@pytest.mark.parser
def test_the_spellname_subset_holds_only_the_named_spells_once_each() -> None:
    rows = _rows(f"SpellName.{B70058}.subset.csv")
    assert _header(f"SpellName.{B70058}.subset.csv") == ["ID", "Name_lang"]
    ids = [r["ID"] for r in rows]
    assert len(ids) == len(set(ids)) == 523
    assert set(ids) <= _spell_ids()


# ─── the capture's trees in the tables ───────────────────────────────────────


@pytest.mark.parser
def test_every_tree_in_the_70058_capture_is_in_the_recorded_trait_tree() -> None:
    char = _char(CAPTURE_70058)
    captured = {t.id for t in _class_trees(char)} | {t.id for t in _legacy_trees(char)}
    assert captured == {1116, 1118, 1187, 1188, 1189}, "what the fixture index says"
    recorded = {int(r["ID"]) for r in _rows(f"TraitTree.{B70058}.csv")}
    assert captured <= recorded


@pytest.mark.parser
def test_the_70058_class_tree_nodes_are_the_recorded_trait_nodes() -> None:
    (tree,) = _class_trees(_char(CAPTURE_70058))
    captured = {n.id for n in tree.nodes}
    assert len(captured) == 52
    recorded = {int(r["ID"]) for r in _rows(f"TraitNode.{B70058}.csv")}
    assert captured <= recorded, f"not in TraitNode: {sorted(captured - recorded)}"
    assert captured == _nodes_of(tree.id, B70058), "and they are all of the tree's nodes"


@pytest.mark.parser
@pytest.mark.parametrize("path", CAPTURES_70009, ids=lambda p: p.parts[-3])
def test_the_70009_captures_trees_and_class_nodes_are_in_the_70009_tables(path: Path) -> None:
    char = _char(path)
    trees = _class_trees(char) + _legacy_trees(char)
    recorded = {int(r["ID"]) for r in _rows(f"TraitTree.{B70009}.csv")}
    assert {t.id for t in trees} <= recorded
    (tree,) = _class_trees(char)
    assert {n.id for n in tree.nodes} == _nodes_of(tree.id, B70009)


# ─── the subsets cut by the captures' ids ────────────────────────────────────


def _currency_ids() -> set[int]:
    found: set[int] = set()
    for path in CAPTURES:
        section = _char(path).currencies
        if isinstance(section, labaddon.Currencies):
            found |= {c.id for c in section.list_ if isinstance(c, labaddon.Currency)}
    return found


def _skill_lines() -> set[int]:
    found: set[int] = set()
    for path in CAPTURES:
        section = _char(path).professions
        if isinstance(section, labaddon.Professions):
            found |= {p.skill_line for p in section.list_ if p.skill_line is not None}
    return found


def _specs() -> set[int]:
    found: set[int] = set()
    for path in CAPTURES:
        section = _char(path).spec
        if isinstance(section, labaddon.Spec) and section.id is not None:
            found.add(section.id)
    return found


@pytest.mark.parser
@pytest.mark.parametrize(
    ("table", "ids"),
    [
        pytest.param("CurrencyTypes", _currency_ids, id="CurrencyTypes"),
        pytest.param("SkillLine", _skill_lines, id="SkillLine"),
        pytest.param("ChrSpecialization", _specs, id="ChrSpecialization"),
    ],
)
def test_each_capture_subset_holds_exactly_the_captures_ids(table: str, ids: object) -> None:
    assert callable(ids)
    wanted = ids()
    rows = _rows(f"{table}.{B70058}.subset.csv")
    got = [int(r["ID"]) for r in rows]
    assert len(got) == len(set(got))
    assert set(got) == wanted
    assert "ID" in _header(f"{table}.{B70058}.subset.csv")
