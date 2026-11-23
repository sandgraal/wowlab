"""`wowlab_core.twink` (M22-06): the configuration, the table reader and the
scoring rules, on the recorded tables of 1.60.1.70888 (`fixtures/wago/`,
the same recordings `test_cli_char_twink.py` serves).

Inputs labelled `constructed` are boundary or hostile cases (L8): damaged
configuration files, a table cell that is not a number, masks and ids at
their edges. Nothing here reads an install or the network.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

import pytest

from wowlab_core import gear, twink

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


def test_the_links(config: twink.TwinkConfig) -> None:
    assert twink.wowhead_link(6537, None, VERSION) == "https://www.wowhead.com/forever/item=6537"
    assert (
        twink.wowhead_link(6537, 12720, VERSION)
        == "https://www.wowhead.com/forever/item=6537?bonus=12720"
    )
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
