"""renderplan: the gear chain (M15-13, docs/LAB_PLAN.md §17.4, the gear,
attachment and geoset bullets).

Nothing here touches the network or an install. The tables are the
recordings under `fixtures/wago/` at 1.60.1.70959 (`test_renderplan.py`'s
`_FixtureSource`: whole tables, or subsets cut by `scripts/wago_subset.py`
and read one after another); the fixture characters are the `macos-70450`
lab-addon records, whose gear is read as `char gear` reads it; the files'
states replay `fixtures/macos-70959/derived/renderplan-gear-files.json`,
recorded from the owner's install by `scripts/renderplan_facts.py`.

Tests whose id contains `constructed` use a constructed character (an
identity and equipped items chosen here, every item a real one of the
recorded tables) or constructed table rows, labelled as such (L8): the
fixture characters wear no helmet, no shoulders, no shield and no robe, and
at 70959 `ItemBonus` has no appearance (type 7) row and every item has one
`ItemModifiedAppearance` row, so a bonus id that selects a second
appearance exists only as constructed rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from test_renderplan import (
    BUILD,
    DANGLING,
    ORC,
    SANGRAAL,
    UNDEAD,
    _FixtureSource,
    _identity,
    _Replay,
    _rows,
    _sangraal_look,
    _States,
)

from wowlab_core import labaddon, renderplan
from wowlab_core.casc import STATE_WORDS, FileState
from wowlab_core.gamedata import GameData
from wowlab_core.renderplan import EquippedItem, RenderPlan, RenderTables

# Real items at 70959, named for the reader.
HELM_1280, SHOULDERS_905, SHIRT_3427, CHEST_2435 = 1280, 905, 3427, 2435
BELT_244228, BOOTS_51, CLOAK_2310, DAGGER_727, SHIELD_143, TABARD_746 = (
    244228,
    51,
    2310,
    727,
    143,
    746,
)
ROBE_56, PANTS_153, GLOVES_203, NO_LOOK_CLOAK_1354, SWORD_753 = 56, 153, 203, 1354, 753
NO_LOOK_CHEST_77 = 77
APPRENTICE_SHIRT_APPEARANCE = 65081  # item 6096's, display 2163


@pytest.fixture(scope="module")
def tables(tmp_path_factory: pytest.TempPathFactory) -> RenderTables:
    data = GameData(_FixtureSource(), cache_dir=tmp_path_factory.mktemp("gamedata"))
    return RenderTables.from_gamedata(data, BUILD)


def _wear(tables: RenderTables, items: Sequence[EquippedItem]) -> RenderPlan:
    """Constructed: the Undead body type 0 identity, no customization record,
    `items` worn, recorded by the tables' own build."""
    return renderplan.build_plan(tables, _identity(UNDEAD), None, gear=items, gear_build=BUILD)


def _e(slot: int, item_id: int | None, *bonus: int) -> EquippedItem:
    return EquippedItem(slot=slot, item_id=item_id, bonus_ids=bonus)


# ─── the fixture characters ────────────────────────────────────────────────


@pytest.fixture(scope="module")
def sangraal(tables: RenderTables) -> tuple[RenderPlan, _Replay]:
    replay = _Replay("Sangraal-Sett")
    return renderplan.plan_for_record(tables, labaddon.read_char(SANGRAAL), statter=replay), replay


@pytest.fixture(scope="module")
def dangling(tables: RenderTables) -> tuple[RenderPlan, _Replay]:
    replay = _Replay("Dangling-Participle")
    return renderplan.plan_for_record(tables, labaddon.read_char(DANGLING), statter=replay), replay


def test_the_record_gear_is_read_as_char_gear_reads_it() -> None:
    worn = renderplan.equipped_items(labaddon.read_char(SANGRAAL))
    assert not isinstance(worn, str)
    assert [(e.slot, e.item_id, e.bonus_ids) for e in worn] == [
        (5, 9749, (12720,)),
        (6, 6539, (12745,)),
        (7, 9747, (12748,)),
        (8, 14114, (12745,)),
        (9, 14122, (12745,)),
        (10, 6541, (12745,)),
        (11, 5351, ()),
        (15, 2310, ()),
        (16, 2632, (12720,)),
        (17, 15969, (12730,)),
        (18, 5071, ()),
    ]


def test_the_fixture_characters_appearances(sangraal: tuple[RenderPlan, _Replay]) -> None:
    """Per item: the ItemModifiedAppearance row, the appearance and the
    display. Its bonus ids carry no appearance row, so the modifier is 0."""
    plan, _ = sangraal
    assert [
        (
            i.slot,
            i.item_id,
            i.inventory_type,
            i.appearance_modifier,
            i.appearance_bonus_id,
            i.item_modified_appearance_id,
            i.item_appearance_id,
            i.item_display_info_id,
        )
        for i in plan.items
    ] == [
        (5, 9749, 5, 0, None, 159496, 64445, 6102),
        (6, 6539, 6, 0, None, 158534, 65250, 8347),
        (7, 9747, 7, 0, None, 159494, 64443, 6189),
        (8, 14114, 8, 0, None, 161248, 67091, 7750),
        (9, 14122, 9, 0, None, 161256, 64275, 10939),
        (10, 6541, 10, 0, None, 158536, 65252, 12443),
        (15, 2310, 16, 0, None, 156764, 63869, 23010),
        (16, 2632, 13, 0, None, 156930, 64002, 20473),
        (17, 15969, 23, 0, None, 162676, 63419, 24061),
    ]
    assert [(n.slot, n.item_id, n.kind, n.reason) for n in plan.items_not_drawn] == [
        (11, 5351, "no look", "the finger slot shows nothing on the body"),
        (18, 5071, "unsupported", "the ranged slot is not drawn yet"),
    ]
    assert plan.gear.model_dump() == {
        "source": "record",
        "why": None,
        "client_build": "1.60.1.70450",
        "saved_at": None,
    }


def test_no_recorded_bonus_list_has_an_appearance_row() -> None:
    """What Forever's data holds at 70959: `ItemBonus` (recorded whole) has
    no type-7 row, so no link's bonus ids change an item's look."""
    assert {r["Type"] for r in _rows("ItemBonus")} >= {"2", "5"}
    assert [r for r in _rows("ItemBonus") if r["Type"] == "7"] == []


def test_the_fixture_characters_geosets(sangraal: tuple[RenderPlan, _Replay]) -> None:
    """Row by row: slot, item, GeosetGroup index, group, value, geoset,
    the rule when another chose it, and who took a contested group."""
    plan, _ = sangraal
    gloves = "the gloves cover the sleeves: geoset group 8 is left at its default"
    assert [
        (s.slot, s.item_id, s.index, s.group, s.value, s.geoset_id, s.rule, s.overridden_by)
        for s in plan.geosets.item_selections
    ] == [
        (5, 9749, 0, 8, 0, 801, None, gloves),
        (5, 9749, 1, 10, 0, 1001, None, None),
        (5, 9749, 2, 13, 0, 1301, None, None),
        (5, 9749, 3, 22, 0, 2201, None, None),
        (5, 9749, 4, 28, 0, 2801, None, None),
        (6, 6539, 0, 18, 0, 1801, None, None),
        (7, 9747, 0, 11, 0, 1101, None, None),
        (7, 9747, 1, 9, 0, 901, None, None),
        (7, 9747, 2, 13, 0, 1301, None, "the chest item takes geoset group 13"),
        (8, 14114, 0, 5, 0, 501, None, None),
        (
            8,
            14114,
            1,
            20,
            0,
            2002,
            "the feet's value 0 selects 2002 (read as the foot covered, a community rule)",
            None,
        ),
        (10, 6541, 0, 4, 1, 402, None, None),
        (10, 6541, 1, 23, 0, 2301, None, None),
        (15, 2310, 0, 15, 5, 1506, None, None),
    ]
    assert plan.geosets.helmet_hidden == ()
    assert plan.geosets.item_groups == (4, 5, 9, 10, 11, 13, 15, 18, 20, 22, 23, 28)


def test_the_fixture_characters_models_and_textures(sangraal: tuple[RenderPlan, _Replay]) -> None:
    """The dagger at the main hand (1), the orb, a held item, at the off
    hand (2); the cloak's texture on the body's cape; neither weapon file
    has a component row."""
    plan, _ = sangraal
    assert [
        (m.slot, m.index, m.model_resources_id, m.file_data_id, m.match, m.kind, m.attachment_id)
        for m in plan.item_models
    ] == [
        (16, 0, 16443, 146176, "no component row", "attached", 1),
        (17, 0, 16005, 147020, "no component row", "attached", 2),
    ]
    assert [
        (t.slot, t.index, t.material_resources_id, t.file_data_id, t.match, t.target)
        for t in plan.item_textures
    ] == [
        (15, 0, 15427, 137297, "no component row", "cape"),
        (16, 0, 23089, 146095, "no component row", "item model"),
        (17, 0, 24627, 147025, "no component row", "item model"),
    ]
    assert all(m.attachment_geoset_values == (0,) * 6 for m in plan.item_models)
    assert plan.item_files_missing == ()


def test_the_fixture_characters_body_texture_pieces(sangraal: tuple[RenderPlan, _Replay]) -> None:
    """Each piece's file chosen by ComponentTextureFileData (the body type 0
    file where the resource has one per body type, else the file for any),
    placed in the body layout's section of its component section."""
    plan, _ = sangraal
    assert [
        (p.slot, p.material_res_row_id, p.component_section, p.file_data_id, p.match)
        for p in plan.item_pieces
    ] == [
        (5, 135927, 0, 151197, "any race, body type"),
        (5, 135929, 3, 162791, "any race, body type"),
        (5, 135931, 4, 160140, "any race, body type"),
        (6, 138429, 5, 157418, "any race and body type"),
        (7, 136099, 5, 157422, "any race, body type"),
        (7, 136101, 6, 154804, "any race, body type"),
        (8, 137390, 6, 154963, "any race and body type"),
        (8, 137391, 7, 152732, "any race and body type"),
        (9, 144107, 1, 148915, "any race and body type"),
        (10, 147827, 1, 148771, "any race and body type"),
        (10, 147828, 2, 153776, "any race and body type"),
    ]
    assert all(p.section is not None for p in plan.item_pieces)
    assert all(
        p.section is not None and p.section.section_type == p.component_section
        for p in plan.item_pieces
    )
    chest = plan.item_pieces[0].section
    assert chest is not None and (chest.x, chest.y, chest.width, chest.height) == (0, 0, 512, 256)


def test_the_fixture_characters_gear_files_and_their_states(
    sangraal: tuple[RenderPlan, _Replay],
) -> None:
    """After the body's files, per slot the item's files, each once; every
    one local on the owner's install at 70959."""
    plan, replay = sangraal
    assert [(f.file_data_id, f.role) for f in plan.files][8:] == [
        (151197, "item texture"),
        (162791, "item texture"),
        (160140, "item texture"),
        (157418, "item texture"),
        (157422, "item texture"),
        (154804, "item texture"),
        (154963, "item texture"),
        (152732, "item texture"),
        (148915, "item texture"),
        (148771, "item texture"),
        (153776, "item texture"),
        (137297, "item texture"),
        (146176, "item model"),
        (146095, "item texture"),
        (147020, "item model"),
        (147025, "item texture"),
    ]
    assert {f.stat.state for f in plan.files if f.stat is not None} == {"local"}
    assert all(f.stat is not None for f in plan.files)
    assert replay.asked == [(f.file_data_id, True) for f in plan.files]
    dagger = next(f for f in plan.files if f.file_data_id == 146176)
    assert dagger.stat is not None and dagger.stat.size == 4884


def test_the_second_fixture_character(dangling: tuple[RenderPlan, _Replay]) -> None:
    """Dangling-Participle (Orc, body type 0): a shirt under a vest (the
    vest takes groups 8 and 10), a staff at the main hand."""
    plan, _ = dangling
    assert [(i.slot, i.item_id, i.item_display_info_id) for i in plan.items] == [
        (4, 6096, 2163),
        (5, 4916, 12544),
        (6, 3363, 10061),
        (7, 3291, 11008),
        (8, 1427, 6019),
        (9, 281269, 2992),
        (10, 1377, 11051),
        (15, 6520, 23028),
        (16, 5778, 2397),
    ]
    assert plan.items_not_drawn == ()
    lost = [
        (s.slot, s.group, s.overridden_by) for s in plan.geosets.item_selections if s.overridden_by
    ]
    assert lost == [
        (4, 8, "the chest item takes geoset group 8"),
        (4, 10, "the chest item takes geoset group 10"),
        (7, 13, "the chest item takes geoset group 13"),
    ]
    assert [(m.slot, m.file_data_id, m.attachment_id) for m in plan.item_models] == [
        (16, 147530, 1)
    ]
    assert [(p.slot, p.component_section, p.file_data_id) for p in plan.item_pieces] == [
        (4, 3, 162713),
        (4, 4, 160062),
        (5, 3, 163009),
        (5, 4, 160346),
        (6, 5, 157554),
        (7, 5, 157573),
        (8, 7, 152658),
        (9, 1, 149056),
        (10, 1, 148916),
        (10, 2, 153827),
    ]
    assert [f.file_data_id for f in plan.files][6:] == [
        162713,
        160062,
        163009,
        160346,
        157554,
        157573,
        152658,
        149056,
        148916,
        153827,
        137296,
        147530,
        147532,
    ]
    assert {f.stat.state for f in plan.files if f.stat is not None} == {"local"}


def test_the_gear_plan_round_trips_as_json(sangraal: tuple[RenderPlan, _Replay]) -> None:
    plan, _ = sangraal
    assert RenderPlan.model_validate_json(plan.model_dump_json()) == plan


# ─── a constructed character in plate and cloth ─────────────────────────────


@pytest.fixture(scope="module")
def armoured(tables: RenderTables) -> RenderPlan:
    """Constructed: the Undead body type 0 identity wearing a helmet,
    shoulders, a shirt under a chest, a belt with a model, boots, a cloak,
    a dagger, a shield and a tabard (real items at 70959)."""
    return _wear(
        tables,
        [
            _e(1, HELM_1280),
            _e(3, SHOULDERS_905),
            _e(4, SHIRT_3427),
            _e(5, CHEST_2435),
            _e(6, BELT_244228),
            _e(8, BOOTS_51),
            _e(15, CLOAK_2310),
            _e(16, DAGGER_727),
            _e(17, SHIELD_143),
            _e(19, TABARD_746),
        ],
    )


def test_constructed_a_helmet_hides_the_hair_and_ears(
    tables: RenderTables, armoured: RenderPlan
) -> None:
    """Helmet 1280's display names helmet visibility 246 for body type 0;
    HelmetGeosetData's rows of 246 for race 5 hide groups 0 (hair) and 7
    (ears). Geoset 0, the body, stays."""
    hides = armoured.geosets.helmet_hidden
    assert [
        (h.helmet_geoset_vis_id, h.helmet_geoset_row_id, h.race_id, h.group) for h in hides
    ] == [
        (246, 8508, UNDEAD, 0),
        (246, 8548, UNDEAD, 7),
    ]
    assert {h.from_chr_model_id for h in hides} == {9}
    look = renderplan.build_plan(tables, _identity(UNDEAD), _sangraal_look())
    geosets = look.geosets.model_copy(update={"helmet_hidden": hides})
    available = [0, 1, 2, 5, 103, 701, 702, 2701, 2702]
    # Without the helmet the hairstyle (2) and the default ears show.
    assert renderplan.visible_geosets(look.geosets, available) == [0, 2, 103, 701, 2701]
    assert renderplan.visible_geosets(geosets, available) == [0, 103, 2701]


def test_constructed_the_helmet_model_is_the_race_and_body_types(armoured: RenderPlan) -> None:
    """ModelResourcesID 17420 has a file per race and body type; the Undead
    body type 0 file is drawn at the head (11)."""
    (helm,) = [m for m in armoured.item_models if m.slot == 1]
    assert (helm.file_data_id, helm.match, helm.kind, helm.attachment_id) == (
        137590,
        "race and body type",
        "attached",
        11,
    )
    assert helm.other_file_data_ids == ()


def test_constructed_shoulders_are_two_models_one_per_side(armoured: RenderPlan) -> None:
    """Both ModelResourcesID cells name 16402; its files are for any race and
    body type, one per side (PositionIndex 0 and 1), drawn at 6 and 5."""
    rows = [
        (m.index, m.file_data_id, m.position_index, m.attachment_id, m.match)
        for m in armoured.item_models
        if m.slot == 3
    ]
    assert rows == [
        (0, 143132, 0, 6, "any race and body type"),
        (1, 143436, 1, 5, "any race and body type"),
    ]


def test_constructed_the_chest_takes_the_sleeves_from_the_shirt(armoured: RenderPlan) -> None:
    """The shirt's sleeves (group 8, value 1) and the chest's (value 2): the
    chest's 803 is drawn, the shirt's listed as overridden."""
    sel = {(s.slot, s.group): s for s in armoured.geosets.item_selections}
    assert (sel[(5, 8)].geoset_id, sel[(5, 8)].overridden_by) == (803, None)
    assert (sel[(4, 8)].geoset_id, sel[(4, 8)].overridden_by) == (
        802,
        "the chest item takes geoset group 8",
    )
    assert sel[(4, 10)].overridden_by == "the chest item takes geoset group 10"
    available = [801, 802, 803, 1001, 1002]
    assert renderplan.visible_geosets(armoured.geosets, available) == [803, 1001]


def test_constructed_the_cloak_boots_and_tabard_geosets(armoured: RenderPlan) -> None:
    sel = {(s.slot, s.group): s.geoset_id for s in armoured.geosets.item_selections}
    assert (sel[(15, 15)], sel[(8, 5)], sel[(8, 20)], sel[(19, 12)]) == (1506, 502, 2002, 1202)


def test_constructed_weapons_shield_and_an_unplaced_belt_model(armoured: RenderPlan) -> None:
    """The dagger at the main hand (1), the shield (InventoryType 14) at the
    shield attachment (0), and the belt's model, which has no component row
    and which no attachment of the waist takes: not placed, noted (it may be
    attached at a point the plan does not list)."""
    rows = {
        m.slot: (m.file_data_id, m.kind, m.attachment_id, m.model_type)
        for m in armoured.item_models
        if m.slot in (6, 16, 17)
    }
    assert rows == {
        6: (8267903, "no attachment known", None, 0),
        16: (148132, "attached", 1, 0),
        17: (142813, "attached", 0, 0),
    }
    textures = {(t.slot, t.index): (t.file_data_id, t.target) for t in armoured.item_textures}
    assert textures[(17, 0)] == (142814, "item model")
    assert textures[(15, 0)] == (137297, "cape")
    assert textures[(1, 0)] == (137596, "item model")
    assert (
        "item 244228: model 8267903 has no component row and the waist slot lists no "
        "attachment; not placed"
    ) in armoured.notes


def test_constructed_a_fitted_model_with_no_attachment_is_a_collection_model() -> None:
    """Constructed row: the belt's file 8267903 given a ComponentModelFileData
    row for race 5, body type 0. Chosen through it, with no attachment of the
    waist: a collection model, and no note."""
    raw = {name: _rows(name) for name in renderplan.REQUIRED_TABLES}
    raw["ComponentModelFileData"] = [
        *raw["ComponentModelFileData"],
        {"ID": "8267903", "GenderIndex": "0", "ClassID": "0", "RaceID": "5", "PositionIndex": "-1"},
    ]
    plan = _wear(RenderTables(BUILD, raw), [_e(6, BELT_244228)])
    (belt,) = plan.item_models
    assert (belt.kind, belt.attachment_id, belt.match) == ("collection", None, "race and body type")
    assert not any("not placed" in n for n in plan.notes)


def test_constructed_a_one_hand_weapon_in_the_off_hand_is_at_the_left_hand(
    tables: RenderTables,
) -> None:
    plan = _wear(tables, [_e(17, SWORD_753)])
    assert [(m.file_data_id, m.attachment_id) for m in plan.item_models] == [(147867, 2)]


# ─── a robe, gloves, a second appearance, an item with none ────────────────


def test_constructed_a_robe_takes_the_trousers_and_gloves_the_sleeves(
    tables: RenderTables,
) -> None:
    """Robe 56 (InventoryType 20) sets group 13 to 1, as do pants 153: the
    chest's 1302 is drawn. Gloves 203 set group 4: the robe's sleeves
    (group 8) are left at the default, and so are the shirt's."""
    plan = _wear(tables, [_e(4, SHIRT_3427), _e(5, ROBE_56), _e(7, PANTS_153), _e(10, GLOVES_203)])
    assert [i.inventory_type for i in plan.items] == [4, 20, 7, 10]
    sel = {(s.slot, s.group): (s.geoset_id, s.overridden_by) for s in plan.geosets.item_selections}
    assert sel[(5, 13)] == (1302, None)
    assert sel[(7, 13)] == (1302, "the chest item takes geoset group 13")
    assert sel[(5, 8)] == (
        802,
        "the gloves cover the sleeves: geoset group 8 is left at its default",
    )
    assert sel[(4, 8)] == (802, "the chest item takes geoset group 8")
    assert sel[(10, 4)] == (402, None)
    available = [401, 402, 801, 802, 1301, 1302, 1101]
    assert renderplan.visible_geosets(plan.geosets, available) == [402, 801, 1101, 1302]


def test_constructed_an_item_with_no_appearance_row_is_listed(tables: RenderTables) -> None:
    """Items 77 and 1354 have an Item row and no ItemModifiedAppearance row at
    70959: listed, and the rest of the plan is made."""
    plan = _wear(tables, [_e(5, NO_LOOK_CHEST_77), _e(15, NO_LOOK_CLOAK_1354), _e(16, DAGGER_727)])
    assert [(n.slot, n.item_id, n.kind, n.reason) for n in plan.items_not_drawn] == [
        (5, 77, "no appearance", "ItemModifiedAppearance has no row for item 77"),
        (15, 1354, "no appearance", "ItemModifiedAppearance has no row for item 1354"),
    ]
    assert [i.item_id for i in plan.items] == [DAGGER_727]


def _with_bonuses(**changes: list[dict[str, str]]) -> RenderTables:
    raw = {name: _rows(name) for name in renderplan.REQUIRED_TABLES}
    for name, extra in changes.items():
        raw[name] = raw[name] + extra
    return RenderTables(BUILD, raw)


def _bonus(row: int, parent: int, modifier: int, priority: int) -> dict[str, str]:
    return {
        "ID": str(row),
        "Value_0": str(modifier),
        "Value_1": str(priority),
        "Value_2": "0",
        "Value_3": "0",
        "ParentItemBonusListID": str(parent),
        "Type": "7",
        "OrderIndex": "0",
    }


def test_constructed_bonus_ids_select_a_second_appearance() -> None:
    """Constructed rows: bonus list 999000 carries an appearance row
    (modifier 1, priority 0), 999002 another (modifier 2, priority 5); item
    3427 gets a second ItemModifiedAppearance row for modifier 1, showing
    appearance 65081. The lowest priority wins: modifier 1, whatever the
    order of the bonus ids. Without the bonus ids the item's own look."""
    t = _with_bonuses(
        ItemBonus=[_bonus(999001, 999000, 1, 0), _bonus(999003, 999002, 2, 5)],
        ItemModifiedAppearance=[
            {
                "ID": "999100",
                "ItemID": str(SHIRT_3427),
                "ItemAppearanceModifierID": "1",
                "ItemAppearanceID": str(APPRENTICE_SHIRT_APPEARANCE),
                "OrderIndex": "0",
                "TransmogSourceTypeEnum": "0",
                "Flags": "0",
            }
        ],
    )
    for bonus in ((999002, 999000), (999000, 999002)):
        plan = _wear(t, [_e(4, SHIRT_3427, *bonus)])
        (item,) = plan.items
        assert (
            item.appearance_modifier,
            item.appearance_bonus_id,
            item.item_modified_appearance_id,
            item.item_display_info_id,
        ) == (1, 999000, 999100, 2163)
    (own,) = _wear(t, [_e(4, SHIRT_3427)]).items
    assert (own.appearance_modifier, own.appearance_bonus_id, own.item_display_info_id) == (
        0,
        None,
        3686,
    )


def test_constructed_a_bonus_naming_an_unknown_modifier_falls_back() -> None:
    """Constructed: bonus list 999004 selects modifier 3, which the item has
    no row for: noted, and the default appearance drawn."""
    t = _with_bonuses(ItemBonus=[_bonus(999005, 999004, 3, 0)])
    plan = _wear(t, [_e(4, SHIRT_3427, 999004)])
    assert plan.items[0].item_display_info_id == 3686
    assert plan.items[0].appearance_bonus_id is None
    assert (
        "item 3427: bonus 999004 selects appearance modifier 3, which ItemModifiedAppearance "
        "does not list for the item; its default appearance is drawn"
    ) in plan.notes


# ─── fallbacks ──────────────────────────────────────────────────────────────


def _with_model_9(**cells: str) -> dict[str, list[dict[str, str]]]:
    raw = {name: _rows(name) for name in renderplan.REQUIRED_TABLES}
    raw["ChrModel"] = [{**r, **cells} if r["ID"] == "9" else r for r in raw["ChrModel"]]
    return raw


def test_constructed_helmet_visibility_falls_back_to_another_model() -> None:
    """Constructed: race 5's HelmetGeosetData rows removed and ChrModel 9
    given `HelmVisFallbackChrModelID` 3 (Orc body type 0): the Orc rows of
    visibility 246 hide, noted."""
    raw = _with_model_9(HelmVisFallbackChrModelID="3")
    raw["HelmetGeosetData"] = [r for r in raw["HelmetGeosetData"] if r["RaceID"] != "5"]
    plan = _wear(RenderTables(BUILD, raw), [_e(1, HELM_1280)])
    hides = plan.geosets.helmet_hidden
    assert {(h.race_id, h.from_chr_model_id) for h in hides} == {(ORC, 3)}
    assert 0 in {h.group for h in hides}
    assert "ChrModel 9: followed HelmVisFallbackChrModelID to 3" in plan.notes


def test_constructed_no_helmet_row_hides_nothing_and_says_so() -> None:
    raw = {name: _rows(name) for name in renderplan.REQUIRED_TABLES}
    raw["HelmetGeosetData"] = [r for r in raw["HelmetGeosetData"] if r["RaceID"] != "5"]
    plan = _wear(RenderTables(BUILD, raw), [_e(1, HELM_1280)])
    assert plan.geosets.helmet_hidden == ()
    assert (
        "item 1280: HelmetGeosetData has no row of visibility 246 for race 5 (or a fallback "
        "model's, or any race); nothing hidden"
    ) in plan.notes


def test_constructed_a_model_file_falls_back_to_another_model() -> None:
    """Constructed: the Undead helmet files' component rows removed and
    ChrModel 9 given `ModelFallbackChrModelID` 3: the Orc body type 0 file
    (137588) is drawn, matched through model 3."""
    raw = _with_model_9(ModelFallbackChrModelID="3")
    raw["ComponentModelFileData"] = [
        r for r in raw["ComponentModelFileData"] if r["ID"] not in ("137589", "137590")
    ]
    plan = _wear(RenderTables(BUILD, raw), [_e(1, HELM_1280)])
    (helm,) = plan.item_models
    assert (helm.file_data_id, helm.match, helm.matched_chr_model_id) == (
        137588,
        "fallback model",
        3,
    )


def test_constructed_a_model_with_no_file_for_the_race_is_listed() -> None:
    """Constructed: every component row of the helmet's files is for race 1
    only: no file is drawn, the model is listed, the plan is made."""
    raw = {name: _rows(name) for name in renderplan.REQUIRED_TABLES}
    raw["ComponentModelFileData"] = [
        {**r, "RaceID": "1"} if r["ID"].startswith("1375") else r
        for r in raw["ComponentModelFileData"]
    ]
    plan = _wear(RenderTables(BUILD, raw), [_e(1, HELM_1280)])
    assert plan.item_models == ()
    (missing,) = plan.item_files_missing
    assert (missing.slot, missing.what) == (1, "model 0 (ModelResourcesID 17420)")
    assert missing.reason.startswith("none of its 31 files is for race 5, body type 0")


# ─── edges ──────────────────────────────────────────────────────────────────


def test_constructed_unknown_slot_and_missing_item_id(tables: RenderTables) -> None:
    plan = _wear(tables, [_e(2, 5351), _e(5, None), _e(23, 9749), _e(14, 1)])
    assert [(n.slot, n.kind) for n in plan.items_not_drawn] == [
        (2, "no look"),
        (5, "no item id"),
        (14, "no look"),
        (23, "unknown slot"),
    ]
    assert plan.items == ()


def test_constructed_an_item_without_an_item_row_is_noted() -> None:
    raw = {name: _rows(name) for name in renderplan.REQUIRED_TABLES}
    raw["Item"] = [r for r in raw["Item"] if r["ID"] != str(SHIELD_143)]
    plan = _wear(RenderTables(BUILD, raw), [_e(17, SHIELD_143)])
    assert plan.items[0].inventory_type is None
    assert "item 143: Item has no row for it; its inventory type is unknown" in plan.notes
    # Not known to be a shield: drawn at the off hand.
    assert [m.attachment_id for m in plan.item_models] == [2]


def test_constructed_a_record_without_gear_draws_none_and_says_why(tables: RenderTables) -> None:
    record = labaddon.read_char(DANGLING).model_copy(update={"gear": None})
    plan = renderplan.plan_for_record(tables, record)
    assert plan.gear.source == "none"
    assert plan.gear.why is not None and plan.gear.why.startswith("the record has no gear")
    assert f"no gear is drawn: {plan.gear.why}" in plan.notes
    assert plan.items == () and plan.geosets.item_selections == ()


def test_build_plan_without_gear_draws_none_quietly(tables: RenderTables) -> None:
    plan = renderplan.build_plan(tables, _identity(UNDEAD), _sangraal_look(client_build=BUILD))
    assert plan.gear.model_dump() == {
        "source": "none",
        "why": "no gear was given",
        "client_build": None,
        "saved_at": None,
    }
    assert plan.notes == ()


def test_constructed_gear_files_in_each_of_the_four_states(
    tables: RenderTables, armoured: RenderPlan
) -> None:
    """Constructed states for the armoured character's files, items
    included: each listed as the statter said."""
    states: list[FileState] = ["local", "not_local", "not_in_build", "encrypted"]
    given = {f.file_data_id: states[i % 4] for i, f in enumerate(armoured.files)}
    plan = renderplan.build_plan(
        tables,
        _identity(UNDEAD),
        None,
        gear=[_e(i.slot, i.item_id) for i in armoured.items],
        gear_build=BUILD,
        statter=_States(given),
    )
    got = {f.file_data_id: (f.stat.state, f.stat.words) for f in plan.files if f.stat is not None}
    assert got == {fdid: (s, STATE_WORDS[s]) for fdid, s in given.items()}
    item_states = {got[f.file_data_id][0] for f in plan.files if f.role.startswith("item")}
    assert item_states == set(states)


def test_the_gear_notes_are_carried_whenever_gear_is_drawn(
    tables: RenderTables, armoured: RenderPlan
) -> None:
    """Transmog and hidden helmets and cloaks are not recorded: said once,
    whenever an item is drawn, and not when none is."""
    assert armoured.notes[-2:] == renderplan.GEAR_NOTES
    assert renderplan.GEAR_NOTES == (
        "each item is drawn with its own look; transmog is not recorded, so a transmogged item "
        "looks different in game",
        "a worn helmet or cloak is drawn even if the character hides it in game (not recorded)",
    )
    none = _wear(tables, [_e(11, 5351)])
    assert not any(n in none.notes for n in renderplan.GEAR_NOTES)


def test_constructed_a_walk_that_supplies_nothing_writes_no_followed_note() -> None:
    """Constructed: ChrModel 9 given `ModelFallbackChrModelID` and
    `TextureFallbackChrModelID` 3. The shoulders' files (race 0) and the
    chest's pieces (race 0) come from the any-race ranks, not from model 3:
    no "followed" note. The helmet's file is the Undead one: none either."""
    raw = _with_model_9(ModelFallbackChrModelID="3", TextureFallbackChrModelID="3")
    plan = _wear(
        RenderTables(BUILD, raw), [_e(1, HELM_1280), _e(3, SHOULDERS_905), _e(5, CHEST_2435)]
    )
    assert {m.match for m in plan.item_models if m.slot == 3} == {"any race and body type"}
    assert not any(": followed " in n for n in plan.notes)
