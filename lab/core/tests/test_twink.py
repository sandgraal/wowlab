"""`wowlab_core.twink` (M22-06): the configuration, the table reader and the
scoring rules, on the recorded tables of 1.60.1.70888 (`fixtures/wago/`,
the same recordings `test_cli_char_twink.py` serves).

Inputs labelled `constructed` are boundary or hostile cases (L8): damaged
configuration files, a table cell that is not a number, masks and ids at
their edges. Nothing here reads an install or the network.
"""

from __future__ import annotations

import csv
import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest

from wowlab_core import gear, links, twink

WAGO = Path(__file__).resolve().parent / "fixtures" / "wago"
BUILD = "1.60.1.70888"
VERSION = BUILD


def _recorded(table: str) -> Path:
    whole = WAGO / f"{table}.{BUILD}.csv"
    return whole if whole.is_file() else WAGO / f"{table}.{BUILD}.subset.csv"


def _rows(table: str) -> Iterator[dict[str, str]]:
    with _recorded(table).open(encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


@pytest.fixture(scope="module")
def tables() -> twink.TwinkTables:
    return twink.load_tables(BUILD, _rows)


@pytest.fixture(scope="module")
def config() -> twink.TwinkConfig:
    return twink.shipped_config()


# ─── the configuration ───────────────────────────────────────────────────────


def test_the_shipped_configuration(config: twink.TwinkConfig) -> None:
    assert config.brackets == [19, 29, 39, 49, 59]
    # Warlock first, affliction its first set (the owner's main).
    assert next(iter(config.weights)) == "WARLOCK"
    assert next(iter(config.weights["WARLOCK"])) == "affliction"
    assert set(config.classes) == set(config.weights) == set(config.planner.classes)
    assert config.classes["WARLOCK"].armor == [0, 1]
    assert config.classes["WARLOCK"].weapons == [7, 10, 15, 19]
    assert config.stats[45] == "spell_power" and config.stats[88] == "shadow_damage"
    assert config.slots[11] == [11, 12] and config.slots[17] == [16]
    assert 4 not in {n for slots in config.slots.values() for n in slots}  # no shirt
    assert config.planner.origin == "https://forevertwinking.com"
    assert config.planner.races[5] == "undead"
    assert config.filters.no_level_margin == 5
    assert "test" in config.filters.skip_words


def test_a_file_over_the_shipped_one_replaces_what_it_sets(config: twink.TwinkConfig) -> None:
    mine = twink.parse_config(
        "[brackets]\ncaps = [29, 19]\n\n"
        "[weights.WARLOCK.affliction]\nstamina = 2\n\n"
        "[weights.WARLOCK.mine]\nspell_power = 1.5\n\n"
        '[planner.races]\n5 = "forsaken"\n',
        config,
    )
    assert mine.brackets == [19, 29]
    assert mine.weights["WARLOCK"]["affliction"] == {"stamina": 2.0}  # the whole set
    assert list(mine.weights["WARLOCK"]) == ["affliction", "destruction", "demonology", "mine"]
    assert mine.weights["MAGE"] == config.weights["MAGE"]
    assert mine.planner.races[5] == "forsaken" and mine.planner.races[1] == "human"
    assert mine.stats == config.stats and mine.filters == config.filters


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("[nope]\n", "unknown table or key nope"),
        ("[weights.WARLOCK.x]\nluck = 1\n", "'luck' is not a stat name"),
        ("[weights.WARLOCK.x]\nstamina = 'a'\n", "is not a number"),
        ("[weights.WARLOCK.x]\nstamina = inf\n", "is not a number"),
        ("[weights.WARLOCK.x]\nstamina = 1e9\n", "beyond"),
        ("[weights.warlock.x]\nstamina = 1\n", "'warlock' is not allowed here"),
        ("[brackets]\ncaps = []\n", "list of 1 to 64 whole numbers"),
        ("[brackets]\ncaps = [29, 29]\n", "listed twice"),
        ("[brackets]\ncaps = [0]\n", "from 1 to 1000"),
        ('[stats]\n9 = "stamina"\n', "one name is given to two stats"),
        ('[stats]\n"x" = "y"\n', "is not a whole number"),
        ("[stats]\n" + '"' + "9" * 5000 + '" = "y"\n', "is not a whole number"),
        ("[slots]\n1 = [0]\n", "from 1 to"),
        ("[classes.WARLOCK]\narmor = [1]\n", "needs armor and weapons"),
        ('[planner]\norigin = "http://forevertwinking.com"\n', "is not https://<host>"),
        ('[planner]\norigin = "https://a.example/x"\n', "is not https://<host>"),
        ('[planner]\norigin = "https://u@a.example"\n', "is not https://<host>"),
        ('[planner]\npath = "planner"\n', "is not allowed here"),
        ('[planner.races]\n5 = "Un dead"\n', "is not allowed here"),
        ("[filters]\nno_level_margin = -1\n", "from 0 to"),
        ('[filters]\nskip_words = ["a b"]\n', "is not allowed here"),
        ("a = [" * 2000 + "]" * 2000, "not valid TOML"),
        ("x = \n", "not valid TOML"),
    ],
)
def test_a_damaged_file_is_refused_constructed(
    config: twink.TwinkConfig, text: str, words: str
) -> None:
    with pytest.raises(twink.TwinkConfigError, match=None) as caught:
        twink.parse_config(text, config)
    assert words in str(caught.value)


def test_a_user_file_is_read_and_named_when_damaged_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import platformdirs

    config_dir = tmp_path / "config" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: config_dir)
    assert twink.load_config().source == "default"
    config_dir.mkdir(parents=True)
    (config_dir / "twink.toml").write_text("[brackets]\ncaps = [39]\n", encoding="utf-8")
    loaded = twink.load_config()
    assert loaded.source == "user" and loaded.config.brackets == [39]
    (config_dir / "twink.toml").write_bytes(b"\xff\xfe")
    with pytest.raises(twink.TwinkConfigError, match=r"twink\.toml"):
        twink.load_config()


# ─── the tables ──────────────────────────────────────────────────────────────


def test_the_tables_as_read(tables: twink.TwinkTables) -> None:
    staff = tables.items[249392]
    assert (staff.name, staff.class_id, staff.subclass_id, staff.inventory_type) == (
        "Glimmering Staff",
        2,
        10,
        17,
    )
    assert (staff.item_level, staff.required_level, staff.quality) == (30, 25, 3)
    assert staff.flags_4 & 0x200
    # Gear the downloaded ItemSparse lacks (Willow Boots, a suffix item).
    assert 6537 not in tables.items
    assert tables.suffix_options[6537]  # its bonus tree is there all the same
    assert tables.gear_in_item - tables.gear_without_details == len(tables.items)
    owl = tables.suffixes[12720]
    assert owl.name == "of the Owl"
    assert sorted(owl.shares) == [(5, 6666), (6, 6666)]  # intellect and spirit
    assert 12720 in tables.suffix_options[15970]
    assert tables.budgets[30]["Superior"][0] > tables.budgets[30]["Good"][0] > 0
    # The Defilers' standing: an Undead (race 5, bit 4) starts at 0, not Hated.
    sides = dict(tables.factions[510])
    undead = next(mask for mask in sides if mask & (1 << 4))
    assert sides[undead] == 0


def test_a_cell_that_is_not_a_number_is_refused_constructed() -> None:
    def rows(table: str) -> Iterator[dict[str, str]]:
        if table == "Item":
            yield {"ID": "1", "ClassID": "x", "SubclassID": "0", "InventoryType": "1"}

    with pytest.raises(twink.TwinkError, match="Item has a row \\(1\\) whose ClassID is 'x'"):
        twink.load_tables(BUILD, rows)


# ─── the rules ───────────────────────────────────────────────────────────────


def test_the_stat_amounts_follow_the_budget(tables: twink.TwinkTables) -> None:
    staff = tables.items[249392]
    budget = tables.budgets[30]["Superior"][0]  # a two-handed staff: column 0
    amounts = twink._item_amounts(tables, staff)
    for stat, share in staff.shares:
        assert amounts[stat] >= twink._go_round(share * budget * 0.0001)
    # The caster flag: 20000 per 10000 of column 0, rounded down.
    assert amounts[45] == 20000 * budget // 10000


@pytest.mark.parametrize(
    ("value", "rounded"), [(0.5, 1), (1.5, 2), (2.5, 3), (2.4999, 2), (-0.5, -1), (0.0, 0)]
)
def test_rounding_is_half_away_from_zero(value: float, rounded: int) -> None:
    assert twink._go_round(value) == rounded


@pytest.mark.parametrize(
    ("mask", "row_id", "admits"),
    [
        (-1, 5, True),
        (0, 5, True),
        (1 << 4, 5, True),
        (1 << 3, 5, False),
        (1 << 63, 64, True),
        (1 << 3, None, True),
        (1 << 3, 65, True),
        (1 << 3, 0, True),
    ],
)
def test_masks_constructed(mask: int, row_id: int | None, admits: bool) -> None:
    assert twink._admits(mask, row_id) is admits


def test_names_of_test_and_retired_items(config: twink.TwinkConfig) -> None:
    f = config.filters
    for name in ("QATest +1000 Spell Dmg Ring", "Ring of Critical Testing", "Deprecated Cowl"):
        assert twink._skipped_name(name, f)
    for name in ("OLDMonster Hide", "[PH] Robe", "Test Sword (DND)"):
        assert twink._skipped_name(name, f)
    for name in ("Golden Ring", "Old Boots", "Testament of Hope", "Kobold Mask"):
        assert not twink._skipped_name(name, f)


def test_the_default_bracket(config: twink.TwinkConfig) -> None:
    assert twink.default_bracket(config, 13) == (19, "level")
    assert twink.default_bracket(config, 24) == (29, "level")
    assert twink.default_bracket(config, 29) == (29, "level")
    assert twink.default_bracket(config, 30) == (39, "level")
    assert twink.default_bracket(config, 60) == (59, "last")
    assert twink.default_bracket(config, None) == (19, "first")


def test_the_links(config: twink.TwinkConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    assert twink.wowhead_link(6537, None, VERSION) == "https://www.wowhead.com/forever/item=6537"
    # `?bonus=` is off until the owner's check confirms Wowhead reads it; the
    # switch is the shared link builder's since M21-03.
    assert links.WOWHEAD_BONUS is False
    assert twink.wowhead_link(6537, 12720, VERSION) == "https://www.wowhead.com/forever/item=6537"
    monkeypatch.setattr(links, "WOWHEAD_BONUS", True)
    assert (
        twink.wowhead_link(6537, 12720, VERSION)
        == "https://www.wowhead.com/forever/item=6537?bonus=12720"
    )
    assert twink.wowhead_link(6537, 0, VERSION) == "https://www.wowhead.com/forever/item=6537"
    monkeypatch.setattr(links, "WOWHEAD_BONUS", False)
    assert twink.wowhead_link(6537, None, None) is None
    assert twink.wowhead_link(6537, None, "9.9.9.1") is None
    assert twink.planner_link(config, "WARLOCK", 5, 29) == (
        "https://forevertwinking.com/planner?class=warlock&race=undead&bracket=l29",
        None,
    )
    link, why = twink.planner_link(config, "WARLOCK", 70, 29)
    assert link is None and why == "twink.toml has no planner word for race id 70"
    link, why = twink.planner_link(config, "DEATHKNIGHT", 5, 29)
    assert link is None and why == "twink.toml has no planner word for the class DEATHKNIGHT"
    link, why = twink.planner_link(config, "WARLOCK", None, 29)
    assert link is None and why is not None and "the record has no race" in why


def test_the_planner_origin_is_the_allowlists(config: twink.TwinkConfig) -> None:
    """M21-03: the shipped planner origin is `link_sites.toml`'s
    ForeverTwinking site, and one a player's twink.toml names elsewhere
    gets no link (constructed)."""
    assert config.planner.origin == links.origins()[twink.PLANNER_SITE]
    moved = config.model_copy(
        update={"planner": config.planner.model_copy(update={"origin": "https://evil.example"})}
    )
    link, why = twink.planner_link(moved, "WARLOCK", 5, 29)
    assert link is None and why == "https://evil.example is not a site wowlab links to"


def test_sources_are_not_known_yet() -> None:
    assert twink.item_sources([249392, 6537]) == {}


def test_a_class_the_configuration_lacks_is_refused_constructed(
    tables: twink.TwinkTables, config: twink.TwinkConfig
) -> None:
    with pytest.raises(twink.TwinkError, match="no gear types or no weights for the class MONK"):
        twink.build_panel(
            tables,
            config,
            filled=[],
            slot_names=gear.shipped_slot_table().slots,
            class_file="MONK",
            class_id=10,
            race_id=1,
            level=20,
            bracket=None,
            weights_name=None,
            weights_source="default",
            version=VERSION,
        )


def test_an_empty_record_has_every_slot_empty(
    tables: twink.TwinkTables, config: twink.TwinkConfig
) -> None:
    panel = twink.build_panel(
        tables,
        config,
        filled=[],
        slot_names=gear.shipped_slot_table().slots,
        class_file="WARLOCK",
        class_id=9,
        race_id=5,
        level=None,
        bracket=None,
        weights_name="destruction",
        weights_source="default",
        version=VERSION,
        top=1,
    )
    assert (panel.bracket, panel.bracket_from) == (19, "first")
    assert all(s.worn is None for s in panel.slots)
    assert all(len(s.candidates) <= 1 for s in panel.slots)
    assert all(s.gap == s.candidates[0].score for s in panel.slots if s.candidates)
    assert "The record has no level, so the first bracket is shown." in panel.notes


# ─── review round 1: levels, dual wield, binding, the user file ──────────────


def _item(item_id: int, **fields: object) -> twink._Item:
    """A constructed item (L8: a boundary case of the class rules)."""
    base: dict[str, object] = {
        "id": item_id,
        "name": f"Constructed {item_id}",
        "class_id": 2,
        "subclass_id": 15,  # dagger
        "inventory_type": 13,  # one-hand
        "item_level": 30,
        "required_level": 25,
        "quality": 3,
        "allowable_class": -1,
        "allowable_race": -1,
        "shares": ((7, 10000),),  # stamina
        "flags_4": 0,
        "bonding": 2,
        "min_faction": 0,
    }
    base.update(fields)
    return twink._Item(**base)  # type: ignore[arg-type]


def _panel(
    tables: twink.TwinkTables, config: twink.TwinkConfig, class_file: str, bracket: int
) -> twink.TwinkPanel:
    return twink.build_panel(
        tables,
        config,
        filled=[],
        slot_names={},
        class_file=class_file,
        class_id=None,
        race_id=5,
        level=bracket,
        bracket=bracket,
        weights_name=None,
        weights_source="default",
        version=VERSION,
        config_file="~/config/wowlab/twink.toml",
    )


def _with(tables: twink.TwinkTables, *items: twink._Item) -> twink.TwinkTables:
    merged = {**tables.items, **{i.id: i for i in items}}
    return dataclasses.replace(tables, items=merged)


def _slot_ids(panel: twink.TwinkPanel, n: int) -> list[int]:
    return [c.item_id for s in panel.slots if s.slot == n for c in s.candidates]


def test_a_one_hander_fills_the_off_hand_only_for_a_dual_wielder_constructed(
    tables: twink.TwinkTables, config: twink.TwinkConfig
) -> None:
    dagger = _item(900001, shares=((7, 40000),))
    off_dagger = _item(900002, inventory_type=22, shares=((7, 40000),))
    more = _with(tables, dagger, off_dagger)
    rogue_29 = _panel(more, config, "ROGUE", 29)  # dual wields from 10
    assert 900001 in _slot_ids(rogue_29, 16) and 900001 in _slot_ids(rogue_29, 17)
    assert 900002 in _slot_ids(rogue_29, 17) and 900002 not in _slot_ids(rogue_29, 16)
    warrior_19 = _panel(more, config, "WARRIOR", 19)  # from 20: not yet
    assert 900001 not in _slot_ids(warrior_19, 17)
    assert 900002 not in _slot_ids(warrior_19, 17)
    warlock_29 = _panel(more, config, "WARLOCK", 29)  # never
    assert {900001, 900002}.isdisjoint(_slot_ids(warlock_29, 17))


def test_the_class_levels_as_shipped(config: twink.TwinkConfig) -> None:
    assert config.classes["HUNTER"].armor_from == {3: 40}
    assert config.classes["SHAMAN"].armor_from == {3: 40}
    assert config.classes["PALADIN"].armor_from == {4: 40}
    assert config.classes["WARRIOR"].armor_from == {4: 40}
    assert config.classes["ROGUE"].dual_wield_from == 10
    assert config.classes["WARRIOR"].dual_wield_from == 20
    assert config.classes["HUNTER"].dual_wield_from == 20
    assert config.classes["WARLOCK"].dual_wield_from is None
    # Two-handed axes and maces come to a shaman through a talent only.
    assert {1, 5}.isdisjoint(config.classes["SHAMAN"].weapons)


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ('[classes.HUNTER]\narmor = [1]\nweapons = [2]\narmor_from = { "3" = 40 }\n', "is not one"),
        ("[classes.HUNTER]\narmor = [1]\nweapons = [2]\ndual_wield_from = 0\n", "not a level"),
        ("[classes.HUNTER]\narmor = [1]\nweapons = [2]\nlevel = 3\n", "nothing else"),
    ],
)
def test_class_levels_are_checked_constructed(
    config: twink.TwinkConfig, text: str, words: str
) -> None:
    with pytest.raises(twink.TwinkConfigError) as caught:
        twink.parse_config(text, config)
    assert words in str(caught.value)


def test_a_required_level_of_one_is_none_and_bind_on_pickup_is_marked_constructed(
    tables: twink.TwinkTables, config: twink.TwinkConfig
) -> None:
    cloth_head = {"class_id": 4, "subclass_id": 1, "inventory_type": 1, "required_level": 1}
    quest_reward = _item(900003, item_level=38, **cloth_head)
    near = _item(900004, item_level=33, bonding=1, shares=((7, 30000),), **cloth_head)
    panel = _panel(_with(tables, quest_reward, near), config, "WARLOCK", 29)
    head = [c for s in panel.slots if s.slot == 1 for c in s.candidates]
    assert 900003 not in [c.item_id for c in head]
    [kept] = [c for c in head if c.item_id == 900004]
    assert kept.binds_on_pickup is True


def test_the_notes_count_and_name_the_user_file_constructed(
    tables: twink.TwinkTables, config: twink.TwinkConfig
) -> None:
    ring = {"class_id": 4, "subclass_id": 0, "inventory_type": 11}
    test_ring = _item(900005, name="Ring of Critical Testing", **ring)
    panel = _panel(_with(tables, test_ring), config, "WARLOCK", 29)
    assert panel.by_name_left_out >= 1
    assert panel.config_file == "~/config/wowlab/twink.toml"
    joined = " ".join(panel.notes)
    assert "put the values to change in ~/config/wowlab/twink.toml" in joined
    assert "named as a test, placeholder or retired item" in joined
    one = " ".join(twink._notes(tables, 29, 29, twink._LeftOut(1, 1, 1), 5, None))
    assert "1 item that needs standing" in one and "was left out" in one
    assert "1 item named as a test" in one and "1 item with no required level was" in one
