"""renderplan: the body and its customization (M15-09, docs/LAB_PLAN.md §17.4).

Nothing here touches the network or an install. Tables come through
`GameData` from a `Source` that serves the recordings under `fixtures/wago/`
(`*.1.60.1.70959*`); characters are the `macos-70450` lab-addon records
(Sangraal-Sett: Undead, body type 0, with a customization record;
Dangling-Participle: Orc, body type 0, without one), and the files' states
replay `fixtures/macos-70959/derived/renderplan-files.json`, recorded from
the owner's install by `scripts/renderplan_facts.py` (ADR-0033's derived
layer; the live layer is `test_renderplan_live.py`).

Tests whose id contains `constructed` use a constructed character (an
identity and a customization record built here) or constructed table rows,
labelled as such (L8): the recorded characters are both body type 0, every
file they name is local, and no playable model has a fallback id.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import pytest

from wowlab_core import labaddon, renderplan
from wowlab_core.casc import STATE_WORDS, CascFileStat, FileState
from wowlab_core.gamedata import GameData
from wowlab_core.renderplan import RenderPlan, RenderPlanError, RenderTables

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WAGO = FIXTURES / "wago"
BUILD = "1.60.1.70959"  # the recorded build; tests may name it, the library may not (L6)
CHARS = FIXTURES / "macos-70450" / "forever" / "WTF" / "Account" / "90000001#6" / "1"
SANGRAAL = CHARS / "Sangraal-Sett" / "SavedVariables" / "WowLab.lua"
DANGLING = CHARS / "Dangling-Participle" / "SavedVariables" / "WowLab.lua"
DERIVED: dict[str, object] = json.loads(
    (FIXTURES / "macos-70959" / "derived" / "renderplan-files.json").read_text(encoding="utf-8")
)

# Real ids at 70959, named for the reader.
ORC, UNDEAD = 2, 5
UNDEAD_SKIN_918, UNDEAD_SKIN_913 = 918, 913
UNDEAD_FACE_923 = 923
UNDEAD_SKIN_TYPE, UNDEAD_SKIN_TYPE_6529 = 567, 6529


def _path(table: str) -> Path:
    for name in (f"{table}.{BUILD}.csv", f"{table}.{BUILD}.subset.csv"):
        if (WAGO / name).is_file():
            return WAGO / name
    raise AssertionError(f"no recording of {table}")


class _FixtureSource:
    """Serves the recorded tables (whole or subset) as wago's body."""

    name = "fixtures"

    def fetch_builds(self, dest: BinaryIO) -> str:
        raise AssertionError("no listing is needed")

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        assert build == BUILD
        assert table not in renderplan.SKIPPED_TABLES
        with _path(table).open("rb") as handle:
            shutil.copyfileobj(handle, dest)
        return f"fixture:{table}.{build}"


def _rows(table: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(_path(table).read_text(encoding="utf-8"), newline="")))


@pytest.fixture(scope="module")
def tables(tmp_path_factory: pytest.TempPathFactory) -> RenderTables:
    data = GameData(_FixtureSource(), cache_dir=tmp_path_factory.mktemp("gamedata"))
    return RenderTables.from_gamedata(data, BUILD)


class _Replay:
    """The derived fixture's states, as `CascSource.stat` gave them."""

    def __init__(self, label: str) -> None:
        chars = DERIVED["characters"]
        assert isinstance(chars, list)
        (char,) = [c for c in chars if c["label"] == label]
        self.files: dict[int, dict[str, object]] = {f["file_data_id"]: f for f in char["files"]}
        self.asked: list[tuple[int, bool | None]] = []

    def stat(self, fdid: int, *, in_table: bool | None = None) -> CascFileStat:
        self.asked.append((fdid, in_table))
        f = self.files[fdid]
        return CascFileStat.model_validate(
            {
                "fdid": fdid,
                "state": f["state"],
                "words": f["words"],
                "content_key": f["content_hash"],
                "size": f["size"],
                "note": f["note"],
            }
        )


class _States:
    """Constructed: a state per file data id."""

    def __init__(self, states: Mapping[int, FileState]) -> None:
        self.states = states

    def stat(self, fdid: int, *, in_table: bool | None = None) -> CascFileStat:
        state = self.states[fdid]
        return CascFileStat(fdid=fdid, state=state, words=STATE_WORDS[state])


def _identity(race: int, sex_raw: int = 2, cls: int = 9) -> labaddon.CharIdentity:
    return labaddon.CharIdentity(
        race_id=race,
        sex=labaddon.UNIT_SEX_TO_ENUM[sex_raw],
        sex_raw=sex_raw,
        class_id=cls,
        class_file="WARLOCK",
    )


def _sangraal_look(**changes: object) -> labaddon.CustomizationImport:
    look = labaddon.customization_import(labaddon.read_char(SANGRAAL))
    return look.model_copy(update=changes)


def _layers(plan: RenderPlan) -> list[tuple[object, ...]]:
    return [
        (
            la.element_id,
            la.choice_id,
            la.texture_target_id,
            la.file_data_id,
            la.texture_type,
            la.layer,
            la.blend_mode,
            la.layer_row_id,
            None if la.whole_canvas else tuple(s.section_type for s in la.sections),
        )
        for la in plan.layers
    ]


# ─── the fixture character ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def sangraal(tables: RenderTables) -> tuple[RenderPlan, _Replay]:
    replay = _Replay("Sangraal-Sett")
    saved = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    plan = renderplan.plan_for_record(
        tables, labaddon.read_char(SANGRAAL), statter=replay, saved_at=saved
    )
    return plan, replay


def test_the_body_of_the_fixture_character(sangraal: tuple[RenderPlan, _Replay]) -> None:
    """Undead body type 0: ChrRaceXChrModel 9 → ChrModel 9 → display 54041 →
    model data 7233 → file 959310; layout 111, 2048 by 1024."""
    plan, _ = sangraal
    assert plan.build == BUILD
    assert plan.class_id == 9
    assert plan.body.model_dump() == {
        "race_id": UNDEAD,
        "sex": 0,
        "chr_model_id": 9,
        "drawn_with_chr_model_id": 9,
        "display_id": 54041,
        "creature_model_data_id": 7233,
        "file_data_id": 959310,
        "layout_id": 111,
        "layout_from_chr_model_id": 9,
        "layout_width": 2048,
        "layout_height": 1024,
    }
    # The record is of 70450, the tables of 70959: the build gap is the one note.
    assert plan.notes == (
        "the record was written by 1.60.1.70450 and is read against the tables of "
        "1.60.1.70959; choice ids can change between builds",
    )


def test_constructed_a_record_of_the_tables_build_has_no_build_note(
    tables: RenderTables,
) -> None:
    """Constructed: Sangraal-Sett's record with its client build set to the
    tables' build."""
    plan = renderplan.build_plan(tables, _identity(UNDEAD), _sangraal_look(client_build=BUILD))
    assert plan.notes == ()


def test_the_fixture_characters_choices_are_the_records(
    sangraal: tuple[RenderPlan, _Replay],
) -> None:
    """Every recorded choice, in the model's option order; the options the
    record lacks are listed, not defaulted."""
    plan, _ = sangraal
    assert [(c.option_id, c.choice_id, c.source) for c in plan.choices] == [
        (58, 918, "record"),
        (59, 923, "record"),
        (60, 941, "record"),
        (61, 956, "record"),
        (563, 6287, "record"),
        (62, 980, "record"),
        (534, 5330, "record"),
    ]
    assert [(n.option_id, n.choice_id) for n in plan.not_chosen] == [
        (567, None),
        (6346, None),
        (8530, None),
    ]
    assert {n.reason for n in plan.not_chosen} == {
        "not in the record (the barber shop did not list it when the record was taken, or "
        "the option is newer than the record's build); not defaulted"
    }


def test_the_fixture_characters_geosets(sangraal: tuple[RenderPlan, _Replay]) -> None:
    """Hair style 941 shows geoset 2; jaw 980 selects 200 and 103."""
    plan, _ = sangraal
    rows = [
        (g.element_id, g.choice_id, g.geoset_row_id, g.group, g.value, g.geoset_id, g.modifier)
        for g in plan.geosets.selections
    ]
    assert rows == [
        (345, 941, 2452, 0, 2, 2, 0),
        (413, 980, 413, 2, 0, 200, -1),
        (415, 980, 415, 1, 3, 103, -1),
    ]
    assert plan.geosets.replaced_groups == (0, 1, 2)


def test_the_fixture_characters_layers(sangraal: tuple[RenderPlan, _Replay]) -> None:
    """Row by row: element, choice, texture target, file, texture type,
    layer, blend mode, layer row, sections (None: the whole canvas)."""
    plan, _ = sangraal
    assert _layers(plan) == [
        (6289, 918, 1, 959287, 1, 0, 1, 121, None),
        (6290, 918, 13, 959281, 1, 2, 15, 123, (5,)),
        (6317, 923, 5, 959239, 1, 4, 15, 125, (10,)),
        (28394, 941, 12, 3457610, 1, 6, 15, 127, (9,)),
        (28995, 980, 8, 3457561, 1, 8, 15, 129, (9,)),
        (26963, 956, 10, 3457574, 6, 1, 1, 122, None),
        (26181, 5330, 25, 3484667, 19, 9, 1, 130, None),
    ]
    section = plan.layers[1].sections[0]
    assert (section.x, section.y, section.width, section.height) == (512, 384, 512, 256)
    assert {la.layout_id for la in plan.layers} == {111}
    assert all(la.other_file_data_ids == () for la in plan.layers)
    assert plan.unplaced == ()


def test_the_fixture_characters_unsupported_elements(
    sangraal: tuple[RenderPlan, _Replay],
) -> None:
    """Face 923 carries a bone set (16586): listed, not drawn."""
    plan, _ = sangraal
    assert [(u.element_id, u.choice_id, u.kind, u.ref_id) for u in plan.unsupported] == [
        (16586, UNDEAD_FACE_923, "bone set", 229)
    ]


def test_the_fixture_characters_files_and_their_states(
    sangraal: tuple[RenderPlan, _Replay],
) -> None:
    """The body model first, then each texture once; every one local on the
    owner's install at 70959, asked as an id a game table names."""
    plan, replay = sangraal
    assert [(f.file_data_id, f.role) for f in plan.files] == [
        (959310, "body model"),
        (959287, "texture"),
        (959281, "texture"),
        (959239, "texture"),
        (3457610, "texture"),
        (3457561, "texture"),
        (3457574, "texture"),
        (3484667, "texture"),
    ]
    assert all(f.stat is not None and f.stat.state == "local" for f in plan.files)
    assert plan.files[0].stat is not None and plan.files[0].stat.size == 28775314
    assert replay.asked == [(f.file_data_id, True) for f in plan.files]
    assert DERIVED["version"] == DERIVED["tables_build"] == BUILD


def test_the_look_carries_what_the_record_rests_on(sangraal: tuple[RenderPlan, _Replay]) -> None:
    """The record holds no date: its own words, its load count, its client
    build, and the file's save time the caller gives."""
    plan, _ = sangraal
    assert plan.look.model_dump() == {
        "source": "record",
        "why": None,
        "as_of": "last barber-shop visit with the addon enabled",
        "recorded_at": "open",
        "carried": True,
        "loads_ago": 1,
        "client_build": "1.60.1.70450",
        "saved_at": datetime(2026, 10, 19, 12, 0, tzinfo=UTC),
    }
    assert plan.skipped_tables == ("ChrCustomizationCondModel",)


def test_the_plan_round_trips_as_json(sangraal: tuple[RenderPlan, _Replay]) -> None:
    plan, _ = sangraal
    assert RenderPlan.model_validate_json(plan.model_dump_json()) == plan


# ─── without a customization record ───────────────────────────────────────


def test_a_real_character_without_a_record_gets_the_defaults(tables: RenderTables) -> None:
    """Dangling-Participle never visited the barber with the addon on: each
    option without a requirement takes its lowest-order choice, and the
    note carries the addon's reason."""
    replay = _Replay("Dangling-Participle")
    plan = renderplan.plan_for_record(tables, labaddon.read_char(DANGLING), statter=replay)
    assert (plan.body.chr_model_id, plan.body.file_data_id, plan.body.layout_id) == (3, 917116, 105)
    assert plan.look.source == "defaults"
    assert plan.look.why is not None
    assert "no barber-shop visit recorded with the addon enabled" in plan.look.why
    assert plan.notes == (plan.look.why,)
    assert [(c.option_id, c.choice_id, c.source) for c in plan.choices] == [
        (19, 353, "default"),
        (20, 384, "default"),
        (21, 402, "default"),
        (22, 415, "default"),
        (9493, 78923, "default"),
        (824, 9266, "default"),
        (877, 9825, "default"),
    ]
    assert [(n.option_id, "requirement 12" in n.reason) for n in plan.not_chosen] == [(8524, True)]
    assert [g.geoset_id for g in plan.geosets.selections] == [0, 100, 200]
    assert [f.file_data_id for f in plan.files] == [
        917116,
        916966,
        916834,
        916943,
        918869,
        3492828,
    ]
    assert {f.stat.state for f in plan.files if f.stat is not None} == {"local"}


def test_constructed_character_without_a_record_gets_defaults_and_the_note(
    tables: RenderTables,
) -> None:
    """Constructed: an Undead identity and no customization record at all."""
    plan = renderplan.build_plan(tables, _identity(UNDEAD), None)
    assert plan.look.source == "defaults"
    assert plan.look.why == (
        "no customization record; each option's first choice (lowest OrderIndex) is drawn "
        "as a stand-in, not the character's look"
    )
    assert plan.look.why in plan.notes
    assert all(c.source == "default" for c in plan.choices)
    # Eyesight (requirement 4103) and Eye Style (12) are not defaulted.
    assert [n.option_id for n in plan.not_chosen] == [6346, 8530]
    assert plan.files[0].stat is None  # no statter: not checked


def test_constructed_record_for_another_race_is_stale(tables: RenderTables) -> None:
    """Constructed: the record is Undead, the identity Orc (a race change
    since the barber visit): the defaults, and why."""
    plan = renderplan.build_plan(tables, _identity(ORC), _sangraal_look())
    assert plan.body.chr_model_id == 3
    assert plan.look.source == "defaults"
    assert plan.look.why is not None and "race 5" in plan.look.why
    assert "(the race or body type changed after the record was taken)" in plan.look.why


# ─── related choices ──────────────────────────────────────────────────────


def test_constructed_face_material_follows_the_skin_choice(tables: RenderTables) -> None:
    """Constructed: Sangraal-Sett's record with skin colour 913 instead of
    918. Face 923's material rows each depend on one skin colour; only the
    one for 913 (element 6312) is taken."""
    look = _sangraal_look(choices={**_sangraal_look().choices, 58: UNDEAD_SKIN_913})
    plan = renderplan.build_plan(tables, _identity(UNDEAD), look)
    face = [la for la in plan.layers if la.choice_id == UNDEAD_FACE_923]
    assert [(la.element_id, la.file_data_id) for la in face] == [(6312, 959234)]
    real = renderplan.build_plan(tables, _identity(UNDEAD), _sangraal_look())
    assert [la.element_id for la in real.layers if la.choice_id == UNDEAD_FACE_923] == [6317]


def test_constructed_without_the_related_choice_the_element_is_not_taken(
    tables: RenderTables,
) -> None:
    """Constructed: the record without its skin colour. No face material is
    taken (every one depends on a skin colour); the bone set, which depends
    on nothing, still is."""
    choices = {k: v for k, v in _sangraal_look().choices.items() if k != 58}
    plan = renderplan.build_plan(tables, _identity(UNDEAD), _sangraal_look(choices=choices))
    assert [la for la in plan.layers if la.choice_id == UNDEAD_FACE_923] == []
    assert [u.element_id for u in plan.unsupported] == [16586]
    first = plan.not_chosen[0]
    assert (first.option_id, first.choice_id) == (58, None)


# ─── unsupported element kinds ────────────────────────────────────────────


def test_constructed_unsupported_kind_is_listed(tables: RenderTables) -> None:
    """Constructed: Sangraal-Sett's record plus Skin Type 6529, whose real
    elements carry three geosets and nine item geoset modifiers. The
    modifiers are listed as unsupported, the geosets taken."""
    choices = {**_sangraal_look().choices, UNDEAD_SKIN_TYPE: UNDEAD_SKIN_TYPE_6529}
    plan = renderplan.build_plan(tables, _identity(UNDEAD), _sangraal_look(choices=choices))
    mods = [u for u in plan.unsupported if u.choice_id == UNDEAD_SKIN_TYPE_6529]
    assert [(u.element_id, u.kind, u.ref_id) for u in mods] == [
        (64816 + i, "item geoset modifier", 2 + i) for i in range(9)
    ]
    assert [
        (g.geoset_row_id, g.geoset_id)
        for g in plan.geosets.selections
        if g.choice_id == UNDEAD_SKIN_TYPE_6529
    ] == [(2109, 3002), (203, 1902), (2107, 2902)]


def test_constructed_every_unsupported_kind_is_named(tables: RenderTables) -> None:
    """Constructed element rows, one per kind the plan does not draw."""
    raw = {name: _rows(name) for name in renderplan.REQUIRED_TABLES}
    template = dict(raw["ChrCustomizationElement"][0])
    columns = [c for c, _ in renderplan._UNSUPPORTED_KINDS]
    extra = []
    for i, column in enumerate(columns):
        row = {
            k: ("0" if k not in ("ID", "ChrCustomizationChoiceID") else v)
            for k, v in template.items()
        }
        row.update(
            {"ID": str(900000 + i), "ChrCustomizationChoiceID": str(UNDEAD_SKIN_918), column: "7"}
        )
        extra.append(row)
    raw["ChrCustomizationElement"] = raw["ChrCustomizationElement"] + extra
    plan = renderplan.build_plan(RenderTables(BUILD, raw), _identity(UNDEAD), _sangraal_look())
    kinds = [u.kind for u in plan.unsupported if u.element_id >= 900000]
    assert kinds == [kind for _, kind in renderplan._UNSUPPORTED_KINDS]


# ─── fallbacks ────────────────────────────────────────────────────────────


def _with_model_9(**cells: str) -> dict[str, list[dict[str, str]]]:
    raw = {name: _rows(name) for name in renderplan.REQUIRED_TABLES}
    raw["ChrModel"] = [{**r, **cells} if r["ID"] == "9" else r for r in raw["ChrModel"]]
    return raw


def test_constructed_missing_display_row_falls_back_to_the_model_fallback(
    tables: RenderTables,
) -> None:
    """Constructed: ChrModel 9 given `ModelFallbackChrModelID` 3 and its
    display row removed. The body is drawn with model 3's file, noted; the
    options and layout stay model 9's."""
    raw = _with_model_9(ModelFallbackChrModelID="3")
    raw["CreatureDisplayInfo"] = [r for r in raw["CreatureDisplayInfo"] if r["ID"] != "54041"]
    plan = renderplan.build_plan(RenderTables(BUILD, raw), _identity(UNDEAD), _sangraal_look())
    assert (plan.body.chr_model_id, plan.body.drawn_with_chr_model_id) == (9, 3)
    assert (plan.body.display_id, plan.body.file_data_id) == (51894, 917116)
    assert plan.body.layout_id == 111
    assert "ChrModel 9: followed ModelFallbackChrModelID to 3" in plan.notes
    assert [c.choice_id for c in plan.choices][:2] == [918, 923]


def test_constructed_missing_layout_falls_back_to_the_texture_fallback() -> None:
    """Constructed: ChrModel 9's layout set to one with no row or layer and
    `TextureFallbackChrModelID` 3: the layers are placed by model 3's
    layout 105, noted."""
    raw = _with_model_9(CharComponentTextureLayoutID="999", TextureFallbackChrModelID="3")
    plan = renderplan.build_plan(RenderTables(BUILD, raw), _identity(UNDEAD), _sangraal_look())
    assert (plan.body.layout_id, plan.body.layout_from_chr_model_id) == (105, 3)
    assert plan.body.file_data_id == 959310
    assert {(la.layout_id, la.layout_from_chr_model_id) for la in plan.layers} == {(105, 3)}
    assert "ChrModel 9: followed TextureFallbackChrModelID to 3" in plan.notes


def test_constructed_texture_fallback_to_a_missing_model_leaves_materials_unplaced() -> None:
    """Constructed: ChrModel 9's layout set to one with no row and its
    `TextureFallbackChrModelID` to 424242, which has no ChrModel row. The
    plan is still made: the body drawn, every material unplaced, noted."""
    raw = _with_model_9(CharComponentTextureLayoutID="999", TextureFallbackChrModelID="424242")
    plan = renderplan.build_plan(RenderTables(BUILD, raw), _identity(UNDEAD), _sangraal_look())
    assert plan.body.file_data_id == 959310
    assert (plan.body.layout_id, plan.body.layout_width) == (999, None)
    assert plan.layers == ()
    assert len(plan.unplaced) == 7
    assert all("places texture target" in u.reason for u in plan.unplaced)
    assert (
        "ChrModel 9: TextureFallbackChrModelID names 424242, which has no ChrModel row"
        in plan.notes
    )
    assert [f.role for f in plan.files] == ["body model"]


def test_constructed_no_body_and_no_fallback_is_refused() -> None:
    """Constructed: the display row removed and no fallback id."""
    raw = {name: _rows(name) for name in renderplan.REQUIRED_TABLES}
    raw["CreatureDisplayInfo"] = [r for r in raw["CreatureDisplayInfo"] if r["ID"] != "54041"]
    with pytest.raises(RenderPlanError, match="ChrModel 9 gives no body model"):
        renderplan.build_plan(RenderTables(BUILD, raw), _identity(UNDEAD), None)


def test_constructed_fallback_cycle_ends() -> None:
    """Constructed: 9 falls back to 3 and 3 to 9, neither drawable."""
    raw = _with_model_9(ModelFallbackChrModelID="3")
    raw["ChrModel"] = [
        {**r, "ModelFallbackChrModelID": "9"} if r["ID"] == "3" else r for r in raw["ChrModel"]
    ]
    raw["CreatureDisplayInfo"] = []
    with pytest.raises(RenderPlanError, match="no model fallback gives one"):
        renderplan.build_plan(RenderTables(BUILD, raw), _identity(UNDEAD), None)


# ─── the four states ──────────────────────────────────────────────────────


def test_constructed_a_file_in_each_of_the_four_states(tables: RenderTables) -> None:
    """Constructed states for the fixture character's files: each listed as
    the statter said, with §17.1's words."""
    plan0 = renderplan.build_plan(tables, _identity(UNDEAD), _sangraal_look())
    ids = [f.file_data_id for f in plan0.files]
    states: list[FileState] = ["local", "not_local", "not_in_build", "encrypted"]
    given = {fdid: states[i % 4] for i, fdid in enumerate(ids)}
    plan = renderplan.build_plan(
        tables, _identity(UNDEAD), _sangraal_look(), statter=_States(given)
    )
    got = {f.file_data_id: (f.stat.state, f.stat.words) for f in plan.files if f.stat is not None}
    assert got == {fdid: (s, STATE_WORDS[s]) for fdid, s in given.items()}
    assert {s for s, _ in got.values()} == set(states)
    assert got[ids[1]][1] == "not in your client's local files"
    assert got[ids[2]][1] == "not part of this game version's files"


# ─── identity ─────────────────────────────────────────────────────────────


def test_constructed_identity_without_race_takes_the_records() -> None:
    """Constructed: a schema-3-like identity (every value absent) with a
    customization record: race and body type from the record, noted."""
    raw = {name: _rows(name) for name in renderplan.REQUIRED_TABLES}
    absent = labaddon._all_absent("this record predates the identity section")
    plan = renderplan.build_plan(RenderTables(BUILD, raw), absent, _sangraal_look())
    assert plan.body.chr_model_id == 9
    assert plan.class_id is None
    assert any(
        n.startswith("race and body type taken from the customization record") for n in plan.notes
    )
    with pytest.raises(RenderPlanError, match="names no race or body type"):
        renderplan.build_plan(RenderTables(BUILD, raw), absent, None)


def test_constructed_a_race_with_no_model_is_refused(tables: RenderTables) -> None:
    with pytest.raises(RenderPlanError, match="no row for race 999, sex 0"):
        renderplan.build_plan(tables, _identity(999), None)


def test_constructed_unitsex_3_is_noted_as_unconfirmed() -> None:
    """Constructed: sex_raw 3 (body type 1) has never been captured. The
    Undead body type 1 model (10) is not in the recorded slices, so a row
    for it is constructed from model 9's display chain."""
    raw = {name: _rows(name) for name in renderplan.REQUIRED_TABLES}
    raw["ChrModel"] = [
        {**r, "DisplayID": "54041", "CharComponentTextureLayoutID": "111"} if r["ID"] == "10" else r
        for r in raw["ChrModel"]
    ]
    plan = renderplan.build_plan(RenderTables(BUILD, raw), _identity(UNDEAD, sex_raw=3), None)
    assert (plan.body.sex, plan.body.chr_model_id) == (1, 10)
    assert any("UnitSex returned 3" in n and "unconfirmed" in n for n in plan.notes)


def test_constructed_record_choice_for_another_option_is_not_drawn(tables: RenderTables) -> None:
    """Constructed: option 58 holding face 923, an unknown choice, and an
    option of another model."""
    look = _sangraal_look(choices={58: UNDEAD_FACE_923, 59: 99999999, 19: 353})
    plan = renderplan.build_plan(tables, _identity(UNDEAD), look)
    assert plan.choices == ()
    reasons = {(n.option_id, n.choice_id): n.reason for n in plan.not_chosen}
    assert "belongs to option 59" in reasons[(58, UNDEAD_FACE_923)]
    assert "unknown to build" in reasons[(59, 99999999)]
    assert "not an option of ChrModel 9" in reasons[(19, 353)]


# ─── tables ───────────────────────────────────────────────────────────────


def test_a_missing_table_is_refused() -> None:
    raw = {name: _rows(name) for name in renderplan.REQUIRED_TABLES if name != "TextureFileData"}
    with pytest.raises(RenderPlanError, match="tables missing: \\['TextureFileData'\\]"):
        RenderTables(BUILD, raw)


def test_constructed_a_cell_that_is_not_a_number_is_refused() -> None:
    raw = {name: _rows(name) for name in renderplan.REQUIRED_TABLES}
    raw["ChrRaceXChrModel"] = [{**raw["ChrRaceXChrModel"][0], "Sex": "x"}]
    with pytest.raises(RenderPlanError, match="ChrRaceXChrModel row 1: Sex='x'"):
        RenderTables(BUILD, raw)


def test_the_skipped_table_is_never_fetched(tables: RenderTables) -> None:
    """`_FixtureSource` asserts it; the plan says what was skipped."""
    assert "ChrCustomizationCondModel" not in renderplan.REQUIRED_TABLES


# ─── geosets ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("geoset", "shown"),
    [
        (0, True),
        (1, False),
        (17, False),
        (101, True),
        (102, False),
        (201, True),
        (1701, False),
        (3200, True),
        (3201, True),
        (3202, True),
        (3501, False),
        (2101, True),
    ],
)
def test_the_default_rule_numerically(geoset: int, shown: bool) -> None:
    assert renderplan.is_default_geoset(geoset) is shown


def test_visible_geosets_replaces_each_selected_group(sangraal: tuple[RenderPlan, _Replay]) -> None:
    """Sangraal-Sett's selections over a constructed id list: groups 0, 1
    and 2 replaced (geoset 0 kept), the rest by the default rule."""
    plan, _ = sangraal
    available = [0, 1, 2, 3, 101, 102, 103, 200, 201, 202, 1701, 3201, 3202, 3501, 2101, 2102]
    assert renderplan.visible_geosets(plan.geosets, available) == [0, 2, 103, 200, 2101, 3201, 3202]


def test_constructed_a_hairstyle_17_is_shown_when_chosen() -> None:
    """Constructed: wow.export's string compare would hide geoset 17 in
    group 0; the numeric rule shows it when chosen."""
    sel = renderplan.GeosetSelection(
        element_id=1, choice_id=1, geoset_row_id=1, group=0, value=17, geoset_id=17, modifier=0
    )
    plan = renderplan.GeosetPlan(selections=(sel,))
    assert renderplan.visible_geosets(plan, [0, 2, 17, 35]) == [0, 17]
