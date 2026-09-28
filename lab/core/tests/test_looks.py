"""looks: the customization model, loaded from the wago.tools recordings (M11-05).

Nothing here touches the network or an install: tables come through
`GameData` from a `Source` that serves the files under `fixtures/wago/`.

Real rows grade every rule the recorded build exercises. Tests whose id
contains `constructed` use a constructed look or a constructed requirement
row, labelled as such (L8): the recorded build has no requirement row with an
achievement, quest or item unlock, and no row with a region group or an
override-archive value on a choice a test can reach, so those rules can only
be shown on constructed rows.
"""

from __future__ import annotations

import ast
import csv
import gzip
import io
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

import pytest

from wowlab_core import looks
from wowlab_core.gamedata import GameData
from wowlab_core.looks import (
    REQUIRED_TABLES,
    Customizations,
    FindingKind,
    Look,
    LookCheck,
    LooksDataError,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "wago"
BUILD = "1.60.1.70009"  # the recorded build; tests may name it, the library may not (L6)

# Real ids in the recorded build, named for the reader.
HUMAN, ORC, NIGHT_ELF, UNDEAD = 1, 2, 4, 5
SKYBORNE_HIGH_ORDER, SKYBORNE_WINDSHAPER = 95, 96
WARRIOR, DEATH_KNIGHT, WARLOCK, DRUID = 1, 6, 9, 11
FOREVER_CLASSES = [1, 2, 3, 4, 5, 7, 8, 9, 11]
HUMAN_BODY_0_SKIN, HUMAN_BODY_0_FACE = 9, 10
HUMAN_BODY_1_SKIN = 14
# Choice 13 of Human body type 0's Skin Color: requirement 53, class mask 32
# (death knight, a class 70009's ChrClasses lacks), and it depends on Face
# being 20, 22 or 31.
DK_SKIN = 13
FACE_ALLOWED, FACE_OTHER = 20, 21
PLAIN_SKIN = 1  # choice 1 of option 9, requirement 141: no class or race limit
# Druid Bear Form (option 901, model 189, which no race's body type uses);
# choice 79128 "Brown": requirement 4287, druid only, Night Elf only.
BEAR_FORM, BEAR_BROWN = 901, 79128
# Flight Form (option 966, model 193); choice 15603: requirement 4241, druid,
# race mask 0xffffffff80000000 (Kul Tiran's bit 31 sign-extended upward).
FLIGHT_FORM, FLIGHT_SIGN_EXTENDED = 966, 15603
IMP_STYLE = 1528  # warlock Imp, model 148


def _read(name: str) -> bytes:
    plain = FIXTURES / f"{name}.{BUILD}.csv"
    if plain.exists():
        return plain.read_bytes()
    return gzip.decompress((FIXTURES / f"{name}.{BUILD}.csv.gz").read_bytes())


class _FixtureSource:
    """Serves the recorded tables; a table over the repository limit is
    stored gzip-compressed and served as the body wago sent."""

    name = "fixtures"

    def fetch_builds(self, dest: BinaryIO) -> str:
        dest.write(gzip.decompress((FIXTURES / "builds.2026-09-28.json.gz").read_bytes()))
        return "fixture:builds.2026-09-28.json.gz"

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        assert build == BUILD
        shutil.copyfileobj(io.BytesIO(_read(table)), dest)
        return f"fixture:{table}.{build}.csv"


@pytest.fixture(scope="module")
def model(tmp_path_factory: pytest.TempPathFactory) -> Customizations:
    data = GameData(_FixtureSource(), cache_dir=tmp_path_factory.mktemp("gamedata"))
    return Customizations.from_gamedata(data, BUILD)


def _rows(name: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(_read(name).decode("utf-8"), newline="")))


def _tables() -> dict[str, list[dict[str, str]]]:
    return {name: _rows(name) for name in REQUIRED_TABLES}


def _look(
    choices: dict[int, int], *, race: int = HUMAN, body: int = 0, cls: int | None = None
) -> Look:
    return Look(name="t", race_id=race, body_type=body, class_id=cls, choices=choices)


def _kinds(check: LookCheck) -> tuple[list[FindingKind], list[FindingKind]]:
    return [f.kind for f in check.refusals], sorted(f.kind for f in check.notes)


# ─── loading ────────────────────────────────────────────────────────────────


def test_the_recorded_build_is_in_the_recorded_listing(tmp_path: Path) -> None:
    """The tables are for a build the source listed when they were recorded."""
    data = GameData(_FixtureSource(), cache_dir=tmp_path)
    versions = {b.version for entries in data.builds().values() for b in entries}
    assert BUILD in versions


def test_model_loads_from_the_recorded_tables(model: Customizations) -> None:
    assert model.build == BUILD
    assert len(model.races) == 58
    assert sorted(model.classes) == FOREVER_CLASSES
    assert model.classes[DRUID].name == "Druid"
    assert len(model.options) == 1173
    assert len(model.choices) == 10447
    assert len(model.requirements) == 492
    assert len(model.categories) == 62
    assert len(model.models) == 127
    # every option and choice hangs together
    assert sum(len(o.choices) for o in model.options.values()) == len(model.choices)
    assert all(c.requirement_id in model.requirements for c in model.choices.values())


def test_loading_through_gamedata_caches_by_build(tmp_path: Path) -> None:
    data = GameData(_FixtureSource(), cache_dir=tmp_path)
    Customizations.from_gamedata(data, BUILD)
    for name in REQUIRED_TABLES:
        path = data.table_path(name, BUILD)
        assert path.read_bytes() == _read(name), f"{name}: the cache holds the bytes as served"
        assert BUILD in path.parts


def test_every_race_flagged_playable_has_options_for_every_body_type(
    model: Customizations,
) -> None:
    """Table flags (PlayableRaceBit set, not NPC-only), not a claim about what
    the Forever server lets anyone create."""
    playable = model.playable_races()
    assert playable, "the recorded build flags some races playable"
    for race in playable:
        assert race.body_types, f"{race.name} has no body type"
        for body in race.body_types:
            assert body.chr_model_id in model.models
            own = [
                o
                for o in model.options_for(race.id, body.body_type)
                if o.chr_model_id == body.chr_model_id
            ]
            assert own, f"{race.name} ({race.id}) body type {body.body_type} has no options"


def test_playable_races_in_the_recorded_build(model: Customizations) -> None:
    """What the 1.60.1.70009 tables flag: the eight original races and the two
    Skyborne. Later races carry the NPC-only flag in this build."""
    assert [r.id for r in model.playable_races()] == [1, 2, 3, 4, 5, 6, 7, 8, 95, 96]
    goblin = model.races[9]
    assert goblin.playable_race_bit >= 0 and not goblin.flagged_playable
    skyborne = model.races[SKYBORNE_HIGH_ORDER], model.races[SKYBORNE_WINDSHAPER]
    assert skyborne[0].body_types == skyborne[1].body_types
    assert (skyborne[0].alliance, skyborne[1].alliance) == (0, 1)


def test_options_follow_the_race_and_body_type(model: Customizations) -> None:
    male = [o.id for o in model.options_for(HUMAN, 0) if o.chr_model_id in model.linked_model_ids]
    female = [o.id for o in model.options_for(HUMAN, 1) if o.chr_model_id in model.linked_model_ids]
    assert HUMAN_BODY_0_SKIN in male and HUMAN_BODY_0_SKIN not in female
    assert HUMAN_BODY_1_SKIN in female and HUMAN_BODY_1_SKIN not in male
    assert male == sorted(male, key=lambda i: (model.options[i].order_index, i))
    assert model.options_for(HUMAN, 7) == []
    assert model.options_for(999999, 0) == []
    for option in model.options_for(HUMAN, 0):
        assert option.category_id in model.categories


def test_form_and_pet_options_follow_class_and_race(model: Customizations) -> None:
    """Druid forms and warlock demons sit on models no ChrRaceXChrModel row
    names; they are listed where a class- or race-restricted choice admits the
    look."""
    ne_druid = {o.id for o in model.options_for(NIGHT_ELF, 0, DRUID)}
    ne_warrior = {o.id for o in model.options_for(NIGHT_ELF, 0, WARRIOR)}
    human_warlock = {o.id for o in model.options_for(HUMAN, 0, WARLOCK)}
    assert BEAR_FORM in ne_druid and BEAR_FORM not in ne_warrior
    assert IMP_STYLE in human_warlock and IMP_STYLE not in ne_druid
    assert BEAR_FORM not in {o.id for o in model.options_for(HUMAN, 0, DRUID)}
    assert model.is_form_or_pet(model.options[BEAR_FORM])


def test_requirement_dependencies_are_grouped_by_option(model: Customizations) -> None:
    req = model.requirements[model.choices[DK_SKIN].requirement_id]
    assert req.class_restricted and req.class_mask == 1 << (DEATH_KNIGHT - 1)
    assert req.classes(model.classes) == (), "the build has no death knight"
    assert req.classes(range(1, 13)) == (DEATH_KNIGHT,)
    assert req.required_choices == ((HUMAN_BODY_0_FACE, (20, 22, 31)),)


# ─── checking looks: real rows ─────────────────────────────────────────────


def test_a_look_the_data_allows_passes(model: Customizations) -> None:
    check = model.check(
        _look({HUMAN_BODY_0_SKIN: DK_SKIN, HUMAN_BODY_0_FACE: FACE_ALLOWED}, cls=DEATH_KNIGHT)
    )
    assert not check.refused
    assert _kinds(check) == ([], [FindingKind.CLASS_NOT_IN_BUILD])
    assert check.notes[0].message == f"class 6 is not in build {BUILD}'s ChrClasses"
    assert check.build == BUILD
    plain = model.check(_look({HUMAN_BODY_0_SKIN: PLAIN_SKIN}, cls=WARRIOR))
    assert plain.refusals == () and plain.notes == ()


def test_class_mask_that_excludes_the_class_is_refused(model: Customizations) -> None:
    check = model.check(
        _look({HUMAN_BODY_0_SKIN: DK_SKIN, HUMAN_BODY_0_FACE: FACE_ALLOWED}, cls=WARRIOR)
    )
    assert _kinds(check) == ([FindingKind.CLASS_EXCLUDED], [])
    assert check.refusals[0].choice_id == DK_SKIN
    assert f"no class in build {BUILD}" in check.refusals[0].message


def test_class_mask_without_a_class_is_noted_not_refused(model: Customizations) -> None:
    check = model.check(_look({HUMAN_BODY_0_SKIN: DK_SKIN, HUMAN_BODY_0_FACE: FACE_ALLOWED}))
    assert _kinds(check) == ([], [FindingKind.CLASS_RESTRICTED])


def test_class_names_come_from_the_build(model: Customizations) -> None:
    check = model.check(_look({BEAR_FORM: BEAR_BROWN}, race=NIGHT_ELF, cls=WARRIOR))
    (refusal,) = check.refusals
    assert refusal.kind is FindingKind.CLASS_EXCLUDED
    assert refusal.message.endswith("(allows Druid (11))")


def test_dependency_set_to_another_choice_is_refused(model: Customizations) -> None:
    check = model.check(
        _look({HUMAN_BODY_0_SKIN: DK_SKIN, HUMAN_BODY_0_FACE: FACE_OTHER}, cls=DEATH_KNIGHT)
    )
    assert _kinds(check) == ([FindingKind.MISSING_DEPENDENCY], [FindingKind.CLASS_NOT_IN_BUILD])
    message = check.refusals[0].message
    assert "option 'Face' (10) being one of choices [20, 22, 31]; the look sets 21" in message


def test_dependency_on_an_unset_option_is_undecided_not_refused(model: Customizations) -> None:
    """Conductor ruling: the client always holds some choice there."""
    check = model.check(_look({HUMAN_BODY_0_SKIN: DK_SKIN}, cls=DEATH_KNIGHT))
    assert not check.refused
    undecided = [n for n in check.notes if n.kind is FindingKind.UNDECIDED_DEPENDENCY]
    assert [n.message.split(": ", 1)[1] for n in undecided] == [
        "depends on option 'Face' (10) being one of [20, 22, 31]; the look does not set option 10"
    ]


def test_dependency_on_an_options_requirement_is_a_condition(model: Customizations) -> None:
    """Undead Eyesight's own requirement names Eye Glow 'Glow': it decides
    whether the barber shop shows Eyesight, not whether the look is valid."""
    options = {o.name: o for o in model.options_for(UNDEAD, 0)}
    glow, sight = options["Eye Glow"], options["Eyesight"]
    no_glow = next(c.id for c in glow.choices if c.name == "None")
    both = next(c.id for c in sight.choices if c.name == "Both")
    check = model.check(_look({glow.id: no_glow, sight.id: both}, race=UNDEAD))
    assert _kinds(check) == ([], [FindingKind.CONDITION])
    assert "shown only when option 'Eye Glow'" in check.notes[0].message
    assert check.notes[0].message.endswith("[verify]")


def test_option_of_another_body_type_is_refused(model: Customizations) -> None:
    female_skin = model.options[HUMAN_BODY_1_SKIN].choices[0].id
    check = model.check(_look({HUMAN_BODY_1_SKIN: female_skin}, body=0))
    assert _kinds(check) == ([FindingKind.WRONG_RACE_OR_BODY_TYPE], [])


def test_option_of_another_race_is_refused(model: Customizations) -> None:
    check = model.check(_look({HUMAN_BODY_0_SKIN: PLAIN_SKIN}, race=ORC))
    assert _kinds(check) == ([FindingKind.WRONG_RACE_OR_BODY_TYPE], [])


def test_body_type_the_race_lacks_is_refused(model: Customizations) -> None:
    check = model.check(_look({}, body=7))
    assert _kinds(check) == ([FindingKind.WRONG_BODY_TYPE], [])


def test_druid_form_choice_is_checked_by_its_requirements(model: Customizations) -> None:
    """Bear Form sits on model 189, which no race's body type uses: a Night
    Elf druid's look with a Night-Elf-only choice for it is not refused."""
    check = model.check(_look({BEAR_FORM: BEAR_BROWN}, race=NIGHT_ELF, cls=DRUID))
    assert _kinds(check) == ([], [FindingKind.FORM_OR_PET_OPTION])
    orc = model.check(_look({BEAR_FORM: BEAR_BROWN}, race=ORC, cls=DRUID))
    assert _kinds(orc) == ([FindingKind.WRONG_RACE_OR_BODY_TYPE], [FindingKind.FORM_OR_PET_OPTION])


def test_race_mask_on_a_real_row(model: Customizations) -> None:
    """Flight Form choice 15603 carries Kul Tiran's bit 31 sign-extended into
    the high word: read literally, it excludes a Night Elf and admits the
    Skyborne (bit 32)."""
    look = {FLIGHT_FORM: FLIGHT_SIGN_EXTENDED}
    ne = model.check(_look(look, race=NIGHT_ELF, cls=DRUID))
    assert _kinds(ne) == ([FindingKind.WRONG_RACE_OR_BODY_TYPE], [FindingKind.FORM_OR_PET_OPTION])
    sky = model.check(_look(look, race=SKYBORNE_HIGH_ORDER, cls=DRUID))
    assert _kinds(sky) == ([], [FindingKind.FORM_OR_PET_OPTION])


def test_option_on_an_unlinked_character_model_is_noted_not_refused(
    model: Customizations,
) -> None:
    """Models 257-278 (Sex 0/1, the original models' display ids) have options
    but no ChrRaceXChrModel row: noted, never refused on the model."""
    option = next(
        o
        for o in model.options.values()
        if o.chr_model_id == 257 and model.requirements.get(o.requirement_id) is None
    )
    choice = next(
        c
        for c in option.choices
        if not model.requirements[c.requirement_id].class_restricted
        and not model.requirements[c.requirement_id].required_choices
    )
    assert not model.is_form_or_pet(option)
    check = model.check(_look({option.id: choice.id}))
    assert _kinds(check) == ([], [FindingKind.UNLINKED_MODEL_OPTION])
    assert option.id not in {o.id for o in model.options_for(HUMAN, 0)}


# ─── checking looks: constructed looks on real rows ────────────────────────


def test_choice_id_the_build_lacks_is_unknown_not_refused_constructed(
    model: Customizations,
) -> None:
    check = model.check(_look({HUMAN_BODY_0_SKIN: 99999999}))
    assert not check.refused
    (note,) = check.notes
    assert note.kind is FindingKind.UNKNOWN_TO_BUILD
    assert note.message == (f"choice 99999999 is unknown to build {BUILD} (possibly a hotfix)")


def test_option_id_the_build_lacks_is_unknown_not_refused_constructed(
    model: Customizations,
) -> None:
    check = model.check(_look({88888888: 1}))
    assert not check.refused
    assert [n.message for n in check.notes] == [
        f"option 88888888 is unknown to build {BUILD} (possibly a hotfix)"
    ]


def test_unknown_choice_on_a_depended_option_is_undecided_not_refused_constructed(
    model: Customizations,
) -> None:
    """The unknown id may be a hotfixed Face that satisfies the dependency."""
    check = model.check(
        _look({HUMAN_BODY_0_SKIN: DK_SKIN, HUMAN_BODY_0_FACE: 99999999}, cls=DEATH_KNIGHT)
    )
    assert not check.refused
    assert _kinds(check)[1] == sorted(
        [
            FindingKind.CLASS_NOT_IN_BUILD,
            FindingKind.UNKNOWN_TO_BUILD,
            FindingKind.UNDECIDED_DEPENDENCY,
        ]
    )


def test_choice_of_another_option_is_refused_constructed(model: Customizations) -> None:
    check = model.check(_look({HUMAN_BODY_0_SKIN: FACE_ALLOWED}))
    assert _kinds(check) == ([FindingKind.CHOICE_NOT_IN_OPTION], [])


def test_unknown_race_is_refused_constructed(model: Customizations) -> None:
    check = model.check(_look({}, race=99999))
    assert _kinds(check) == ([FindingKind.UNKNOWN_RACE], [])


def test_class_the_build_lacks_is_noted_constructed(model: Customizations) -> None:
    check = model.check(_look({HUMAN_BODY_0_SKIN: PLAIN_SKIN}, cls=12))
    assert _kinds(check) == ([], [FindingKind.CLASS_NOT_IN_BUILD])


def test_look_and_check_cross_the_boundary_as_json_constructed(model: Customizations) -> None:
    look = _look({HUMAN_BODY_0_SKIN: DK_SKIN, HUMAN_BODY_0_FACE: FACE_OTHER}, cls=WARRIOR)
    assert Look.model_validate_json(look.model_dump_json()) == look
    check = model.check(look)
    again = LookCheck.model_validate_json(check.model_dump_json())
    assert again == check and again.refused
    assert '"kind":"class_excluded"' in check.model_dump_json()


# ─── constructed requirement rows ──────────────────────────────────────────

_CONSTRUCTED_REQ = 90000001


def _with_requirement(tables: dict[str, list[dict[str, str]]], **cells: str) -> None:
    """Add a constructed ChrCustomizationReq row (all columns of the recording)."""
    row = dict.fromkeys(tables["ChrCustomizationReq"][0], "0")
    row.update(
        ID=str(_CONSTRUCTED_REQ),
        ReqSource_lang="",
        ReqType="3",
        ClassMask="-1",
        OverrideArchive="-1",
        RaceMasks_0="-1",
        RaceMasks_1="-1",
    )
    row.update(cells)
    tables["ChrCustomizationReq"].append(row)


def _point_choice_at_constructed(tables: dict[str, list[dict[str, str]]], choice_id: int) -> None:
    for row in tables["ChrCustomizationChoice"]:
        if row["ID"] == str(choice_id):
            row["ChrCustomizationReqID"] = str(_CONSTRUCTED_REQ)


@pytest.mark.parametrize(
    ("column", "value", "expected"),
    [
        ("ReqAchievementID", "12345", "needs achievement 12345"),
        ("ReqQuestID", "678", "needs quest 678"),
        ("ReqItemModifiedAppearanceID", "910", "needs item appearance 910"),
    ],
)
def test_unlock_is_needs_never_refused_constructed(column: str, value: str, expected: str) -> None:
    tables = _tables()
    _with_requirement(tables, **{column: value})
    _point_choice_at_constructed(tables, PLAIN_SKIN)
    model = Customizations.from_tables(BUILD, tables)

    check = model.check(_look({HUMAN_BODY_0_SKIN: PLAIN_SKIN}, cls=WARRIOR))
    assert not check.refused
    (note,) = check.notes
    assert note.kind is FindingKind.NEEDS_UNLOCK
    assert note.message.endswith(expected)


def test_unlock_note_carries_the_source_text_constructed() -> None:
    tables = _tables()
    _with_requirement(tables, ReqAchievementID="12345", ReqSource_lang="Achievement: Example")
    _point_choice_at_constructed(tables, PLAIN_SKIN)
    model = Customizations.from_tables(BUILD, tables)
    (note,) = model.check(_look({HUMAN_BODY_0_SKIN: PLAIN_SKIN})).notes
    assert note.message.endswith("needs achievement 12345 (Achievement: Example)")


def test_race_mask_that_excludes_the_race_is_refused_constructed() -> None:
    """The two Skyborne races share their models; a mask with bit 32 only
    (High Order's PlayableRaceBit) admits one and refuses the other."""
    tables = _tables()
    _with_requirement(tables, RaceMasks_0="0", RaceMasks_1="1")
    base = Customizations.from_tables(BUILD, _tables())
    model_id = base.races[SKYBORNE_HIGH_ORDER].model_for(0)
    assert model_id == base.races[SKYBORNE_WINDSHAPER].model_for(0)
    option = next(o for o in base.options_for(SKYBORNE_HIGH_ORDER, 0) if o.chr_model_id == model_id)
    choice = option.choices[0].id
    _point_choice_at_constructed(tables, choice)
    model = Customizations.from_tables(BUILD, tables)

    assert not model.check(_look({option.id: choice}, race=SKYBORNE_HIGH_ORDER)).refused
    check = model.check(_look({option.id: choice}, race=SKYBORNE_WINDSHAPER))
    assert _kinds(check) == ([FindingKind.WRONG_RACE_OR_BODY_TYPE], [])


def test_requirement_without_its_has_requirements_bit_gates_nothing_constructed() -> None:
    tables = _tables()
    _with_requirement(tables, ReqType="2", ClassMask="32", ReqAchievementID="12345")
    _point_choice_at_constructed(tables, PLAIN_SKIN)
    model = Customizations.from_tables(BUILD, tables)
    check = model.check(_look({HUMAN_BODY_0_SKIN: PLAIN_SKIN}, cls=WARRIOR))
    assert check.refusals == () and check.notes == ()


@pytest.mark.parametrize(
    ("archive", "text"),
    [
        ("0", "not in the regional override content set (OverrideArchive 0) [verify]"),
        ("1", "only in the regional override content set (OverrideArchive 1) [verify]"),
    ],
)
def test_region_and_archive_conditions_are_notes_constructed(archive: str, text: str) -> None:
    tables = _tables()
    _with_requirement(tables, RegionGroupMask="16", OverrideArchive=archive)
    _point_choice_at_constructed(tables, PLAIN_SKIN)
    model = Customizations.from_tables(BUILD, tables)
    check = model.check(_look({HUMAN_BODY_0_SKIN: PLAIN_SKIN}))
    assert _kinds(check) == ([], [FindingKind.CONDITION, FindingKind.CONDITION])
    assert check.notes[1].message.endswith(text)


def test_dependency_on_a_choice_the_build_lacks_is_undecided_constructed() -> None:
    tables = _tables()
    _with_requirement(tables)
    tables["ChrCustomizationReqChoice"].append(
        {
            "ID": "90000001",
            "ChrCustomizationChoiceID": "99999999",
            "ChrCustomizationReqID": str(_CONSTRUCTED_REQ),
        }
    )
    _point_choice_at_constructed(tables, PLAIN_SKIN)
    model = Customizations.from_tables(BUILD, tables)
    check = model.check(_look({HUMAN_BODY_0_SKIN: PLAIN_SKIN}))
    assert _kinds(check) == ([], [FindingKind.UNDECIDED_DEPENDENCY])


# ─── bad tables ────────────────────────────────────────────────────────────


def test_missing_table_is_refused_by_name_constructed() -> None:
    tables = _tables()
    del tables["ChrCustomizationReqChoice"]
    with pytest.raises(LooksDataError, match="ChrCustomizationReqChoice"):
        Customizations.from_tables(BUILD, tables)


def test_missing_column_is_refused_by_name_constructed() -> None:
    tables = _tables()
    for row in tables["ChrRaces"]:
        del row["PlayableRaceBit"]
    with pytest.raises(LooksDataError, match="ChrRaces: no column 'PlayableRaceBit'"):
        Customizations.from_tables(BUILD, tables)


def test_non_integer_cell_is_refused_with_its_row_constructed() -> None:
    tables = _tables()
    tables["ChrCustomizationChoice"][2]["OrderIndex"] = "x"
    with pytest.raises(LooksDataError, match=r"ChrCustomizationChoice row 3: OrderIndex='x'"):
        Customizations.from_tables(BUILD, tables)


def test_from_tables_accepts_one_shot_iterators() -> None:
    """`gamedata.rows` yields each table once; the model reads it once."""

    def once(name: str) -> Iterator[dict[str, str]]:
        yield from _rows(name)

    model = Customizations.from_tables(BUILD, {name: once(name) for name in REQUIRED_TABLES})
    assert len(model.choices) == 10447


# ─── L6 ─────────────────────────────────────────────────────────────────────


def _code_strings(source: str) -> list[str]:
    """String constants in code: docstrings (evidence, e.g. "on 1.60.1.70009")
    and comments are not code."""
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_module_code_names_no_build_string() -> None:
    strings = _code_strings(Path(looks.__file__).read_text(encoding="utf-8"))
    assert strings, "the scan sees the module's string constants"
    assert not [s for s in strings if re.search(r"\d+\.\d+\.\d+\.\d+", s)]


def test_build_string_scan_would_catch_a_hit() -> None:
    sample = '"""On 1.60.1.70009."""\nBUILD = "1.60.1.70009"\n'
    assert _code_strings(sample) == ["1.60.1.70009"]
