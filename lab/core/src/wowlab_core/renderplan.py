"""renderplan: a recorded character's body and customization as the files and
rules a renderer needs (M15-09).

Spec: docs/LAB_PLAN.md §17.4, the body and customization bullets. Data:
ADR-0022 (tables keyed by the full build string; the build comes from the
caller or discovery, never from here, L6). The gear chain (§17.4's gear,
attachment and item-geoset bullets) is M15-13, which extends this model.

Inputs: the lab-addon's `identity` record (schema 4, M15-01) and its
`customization` record (`labaddon.customization_import`), the game tables of
one build (`RenderTables`, read through `gamedata`, the table-access path
this module uses), and optionally a `FileStatter` (`casc.CascSource`) that
says where each file stands in §17.1's four states.

What the tables are read as (community convention, wow.export and
wowdev.wiki; **[verify]** where marked):

- **Body.** `ChrRaceXChrModel` (race, `Sex`) → `ChrModel` (`DisplayID`,
  `CharComponentTextureLayoutID`) → `CreatureDisplayInfo.ModelID` →
  `CreatureModelData.FileDataID`, the body M2. The identity's `sex` is
  `UnitSex()` mapped 2 → 0 and 3 → 1 (`labaddon.UNIT_SEX_TO_ENUM`), read as
  `ChrRaceXChrModel.Sex`. Only `sex_raw` 2 has been captured (macos-70450);
  3 → 1 is from memory and **[verify]**.
- **Fallbacks.** A model whose display chain has no row (or a zero file id)
  is drawn through `ChrModel.ModelFallbackChrModelID`; a texture target the
  model's layout places nowhere (or a layout with no row) is placed through
  `TextureFallbackChrModelID`'s layout. Each hop is noted. At 70959 the
  models of the races `ChrRaces` flags playable (1 to 8, 95, 96) have no
  fallback ids; 23 models of other races do. The rule is from memory
  **[verify]**.
  `HelmVisFallbackChrModelID` belongs to the gear chain (M15-13).
- **Choices.** With a customization record, its choices, exactly: an option
  the model has and the record lacks is listed, never defaulted (the barber
  shop did not offer it). Without a record (it exists only after a barber
  visit with the addon on), or with one for another race or body type,
  each option of the model whose `Requirement` is 0 takes its choice with
  the lowest `OrderIndex` (then id), **[verify]** against the client; an
  option with a requirement is listed, not defaulted, since requirements
  are not evaluated here.
- **Elements.** `ChrCustomizationElement` rows of each chosen choice; a row
  whose `RelatedChrCustomizationChoiceID` is non-zero is taken only when
  that choice is among the chosen ones (face and skin materials depend on
  the skin colour, hair materials on the hair colour). A taken row is a
  geoset (`ChrCustomizationGeoset`: geoset id `GeosetType * 100 +
  GeosetID`; `Modifier` kept, not applied, **[verify]**) or a material
  (`ChrCustomizationMaterial` → `TextureFileData` by `MaterialResourcesID`,
  the diffuse row being `UsageType` 0, **[verify]**; at 70959 usage types
  are 0 and 2 only, and 2,439 resources have two or more usage-0 rows, of
  which the first in table order is taken and the rest listed). Every
  other kind (skinned model, bone set, display info, item geoset modifier,
  particle colour, geoset component link, voice, anim kit, conditional
  model) is listed as unsupported, as wow.export lists them.
  `ChrCustomizationCondModel` is not published for this build and is not
  fetched (§17.4).
- **Layers.** A material's `ChrModelTextureTargetID` is placed by the
  `ChrModelTextureLayer` row of the layout with that target (`_0` or `_1`):
  its `TextureType` (1 the body, 6 hair, 19 eyes, as the M2's texture
  types; LAB_FORMATS §12), `Layer` (draw order), `BlendMode` (the raw
  number; the compositor names it, M15-10) and section masks. A mask of -1
  covers the whole canvas; otherwise bit *n* of `TextureSectionTypeBitMask`
  is the `CharComponentTextureSections` row of `SectionType` *n* and bit *n*
  of `TextureSectionTypeBitMask2` is `SectionType` 32 + *n* (-1 or 0 there
  meaning none), **[verify]**.
- **Geosets.** The default set is §17.4's numeric rule (`DEFAULT_RULE`):
  geoset 0, every id of 100 or more with `id % 100 == 1`, and every id of
  group 32, with groups 17 and 35 hidden (stated numerically; wow.export's
  decimal-string quirk that also hides ids 17 and 35 is not ported). A
  customization geoset then replaces its whole group: every id of the
  group is hidden and the ids the chosen elements name are shown; geoset 0
  (the body) is never hidden. **[verify]** against wow.export. The ids a
  model has come from its skin (M15-08), so the plan carries the rule and
  the selections and `visible_geosets` applies them to the ids a skin lists.

Pure data: nothing here opens an install (L1) or the network; the tables
come from `gamedata` or from rows the caller hands over, and a file's state
from the statter the caller passes.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from wowlab_core import labaddon
from wowlab_core.casc import CascFileStat
from wowlab_core.errors import WowlabError

if TYPE_CHECKING:
    from wowlab_core.gamedata import GameData

__all__ = [
    "DEFAULT_RULE",
    "DEFAULT_SHOWN_GROUPS",
    "HIDDEN_GROUPS",
    "REQUIRED_TABLES",
    "SKIPPED_TABLES",
    "BodyModel",
    "ChosenChoice",
    "FileStatter",
    "GeosetPlan",
    "GeosetSelection",
    "LookAsOf",
    "OptionNotChosen",
    "PlanFile",
    "RenderPlan",
    "RenderPlanError",
    "RenderTables",
    "Section",
    "TextureLayer",
    "UnplacedMaterial",
    "UnsupportedElement",
    "build_plan",
    "is_default_geoset",
    "plan_for_record",
    "visible_geosets",
]

REQUIRED_TABLES: tuple[str, ...] = (
    "ChrRaceXChrModel",
    "ChrModel",
    "CreatureDisplayInfo",
    "CreatureModelData",
    "ChrCustomizationOption",
    "ChrCustomizationChoice",
    "ChrCustomizationElement",
    "ChrCustomizationGeoset",
    "ChrCustomizationMaterial",
    "TextureFileData",
    "ChrModelTextureLayer",
    "CharComponentTextureSections",
    "CharComponentTextureLayouts",
)
"""The tables a body plan is built from, by name (L6: table names only)."""

SKIPPED_TABLES: tuple[str, ...] = ("ChrCustomizationCondModel",)
"""Tables §17.4 names that are not fetched: not published for this game
version (wago.tools answers HTTP 400 at 70450 and at 70959)."""

DEFAULT_RULE = (
    "geoset 0, every geoset id of 100 or more whose id % 100 is 1, and every id of "
    "group 32; groups 17 and 35 hidden"
)
DEFAULT_SHOWN_GROUPS: frozenset[int] = frozenset({32})
HIDDEN_GROUPS: frozenset[int] = frozenset({17, 35})

# The element kinds a plan does not draw, by column, in table order, with the
# words the plan lists them by.
_UNSUPPORTED_KINDS: tuple[tuple[str, str], ...] = (
    ("ChrCustomizationSkinnedModelID", "skinned model"),
    ("ChrCustomizationBoneSetID", "bone set"),
    ("ChrCustomizationCondModelID", "conditional model"),
    ("ChrCustomizationDisplayInfoID", "display info"),
    ("ChrCustItemGeoModifyID", "item geoset modifier"),
    ("ChrCustomizationVoiceID", "voice"),
    ("AnimKitID", "anim kit"),
    ("ParticleColorID", "particle colour"),
    ("ChrCustGeoComponentLinkID", "geoset component link"),
)
_DIFFUSE_USAGE = 0  # TextureFileData.UsageType of the diffuse texture [verify]
_ALL_SECTIONS = -1
_SECTION_BITS = 32
_MAX_FALLBACK_HOPS = 8  # more hops than any row at 70959 needs (one)


class RenderPlanError(WowlabError):
    """No plan can be made: a table or column is missing, a cell that should
    be a whole number is not, or the character's race and body type name no
    model the tables can draw."""


class FileStatter(Protocol):
    """Where a file data id stands (`casc.CascSource.stat`)."""

    def stat(self, fdid: int, *, in_table: bool | None = None) -> CascFileStat: ...


# ─── the plan ────────────────────────────────────────────────────────────────


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BodyModel(_Frozen):
    """The body: the model the race and body type name (`chr_model_id`), the
    one whose display chain gave the file (`drawn_with_chr_model_id`: another
    when `ModelFallbackChrModelID` was followed), the chain's ids, and the
    texture layout with its size (from the model, or its texture fallback)."""

    race_id: int
    sex: int
    chr_model_id: int
    drawn_with_chr_model_id: int
    display_id: int
    creature_model_data_id: int
    file_data_id: int
    layout_id: int
    layout_from_chr_model_id: int
    layout_width: int | None
    layout_height: int | None


class ChosenChoice(_Frozen):
    """One option's choice the plan draws, and where it came from."""

    option_id: int
    choice_id: int
    source: Literal["record", "default"]


class OptionNotChosen(_Frozen):
    """An option of the model the plan holds no choice for, and why; or a
    recorded choice it does not draw (`choice_id` set)."""

    option_id: int
    choice_id: int | None = None
    reason: str


class GeosetSelection(_Frozen):
    """A geoset a taken element shows: `geoset_id` is `group * 100 + value`."""

    element_id: int
    choice_id: int
    geoset_row_id: int
    group: int
    value: int
    geoset_id: int
    modifier: int


class GeosetPlan(_Frozen):
    """The default rule and the customization's selections; apply them to the
    ids a skin lists with `visible_geosets`."""

    default_rule: str = DEFAULT_RULE
    default_shown_groups: tuple[int, ...] = tuple(sorted(DEFAULT_SHOWN_GROUPS))
    hidden_groups: tuple[int, ...] = tuple(sorted(HIDDEN_GROUPS))
    selections: tuple[GeosetSelection, ...] = ()

    @property
    def replaced_groups(self) -> tuple[int, ...]:
        return tuple(sorted({s.group for s in self.selections}))


class Section(_Frozen):
    """A rectangle of the texture layout's canvas, in pixels."""

    section_type: int
    x: int
    y: int
    width: int
    height: int


class TextureLayer(_Frozen):
    """One customization material, placed. `sections` empty with
    `whole_canvas` true means the layer covers the layout's whole canvas.
    `other_file_data_ids`: further diffuse rows of the same resource, not
    drawn. `layout_from_chr_model_id` names the model whose layout placed it
    (the texture fallback's, when followed)."""

    element_id: int
    choice_id: int
    material_id: int
    texture_target_id: int
    material_resources_id: int
    file_data_id: int
    usage_type: int
    other_file_data_ids: tuple[int, ...] = ()
    layer_row_id: int
    texture_type: int
    layer: int
    blend_mode: int
    section_mask: int
    section_mask2: int
    whole_canvas: bool
    sections: tuple[Section, ...]
    layout_id: int
    layout_from_chr_model_id: int


class UnplacedMaterial(_Frozen):
    """A taken material the plan cannot place or has no file for."""

    element_id: int
    choice_id: int
    material_id: int
    reason: str


class UnsupportedElement(_Frozen):
    """A taken element of a kind the plan does not draw."""

    element_id: int
    choice_id: int
    kind: str
    ref_id: int


class PlanFile(_Frozen):
    """A file the plan names, its role, and where it stands (§17.1's four
    states) when a statter was given (`stat` None: not checked)."""

    file_data_id: int
    role: Literal["body model", "texture"]
    stat: CascFileStat | None = None


class LookAsOf(_Frozen):
    """What the look rests on. `source` "record": the customization record,
    `as_of` its own words, `recorded_at`, `carried` and `loads_ago` as the
    addon kept them, `client_build` the record's client block; the record
    holds no date, so `saved_at` is the file's save time when the caller
    gives it. `source` "defaults": no record was used, `why` says why."""

    source: Literal["record", "defaults"]
    why: str | None = None
    as_of: str | None = None
    recorded_at: Literal["open", "applied"] | None = None
    carried: bool | None = None
    loads_ago: int | None = None
    client_build: str | None = None
    saved_at: datetime | None = None


class RenderPlan(_Frozen):
    """The body and its customization for one character at one build."""

    build: str
    class_id: int | None
    body: BodyModel
    look: LookAsOf
    choices: tuple[ChosenChoice, ...]
    not_chosen: tuple[OptionNotChosen, ...]
    geosets: GeosetPlan
    layers: tuple[TextureLayer, ...]
    unplaced: tuple[UnplacedMaterial, ...]
    unsupported: tuple[UnsupportedElement, ...]
    files: tuple[PlanFile, ...]
    skipped_tables: tuple[str, ...] = SKIPPED_TABLES
    notes: tuple[str, ...]


# ─── geosets ─────────────────────────────────────────────────────────────────


def is_default_geoset(geoset_id: int) -> bool:
    """§17.4's default rule, numerically."""
    group = geoset_id // 100
    if group in HIDDEN_GROUPS:
        return False
    return (
        geoset_id == 0
        or group in DEFAULT_SHOWN_GROUPS
        or (geoset_id >= 100 and geoset_id % 100 == 1)
    )


def visible_geosets(plan: GeosetPlan, available: Iterable[int]) -> list[int]:
    """The ids of `available` (a skin's section geoset ids) the plan shows,
    sorted: the default rule, then each group a selection names replaced by
    the ids selected in it. Geoset 0 is never hidden by a selection."""
    ids = sorted(set(available))
    chosen: dict[int, set[int]] = defaultdict(set)
    for s in plan.selections:
        chosen[s.group].add(s.geoset_id)
    out: list[int] = []
    for gid in ids:
        group = gid // 100
        if group in chosen and gid != 0:
            if gid in chosen[group]:
                out.append(gid)
        elif is_default_geoset(gid):
            out.append(gid)
    return out


# ─── tables ──────────────────────────────────────────────────────────────────


class _Row:
    __slots__ = ("_line", "_row", "_table")

    def __init__(self, table: str, line: int, row: Mapping[str, str]) -> None:
        self._table = table
        self._line = line
        self._row = row

    def number(self, column: str) -> int:
        try:
            text = self._row[column]
        except KeyError:
            raise RenderPlanError(f"{self._table}: no column {column!r}") from None
        try:
            return int(text)
        except ValueError:
            raise RenderPlanError(
                f"{self._table} row {self._line}: {column}={text!r} is not a whole number"
            ) from None

    def has(self, column: str) -> bool:
        return column in self._row


def _rows(table: str, rows: Iterable[Mapping[str, str]]) -> Iterator[_Row]:
    for line, row in enumerate(rows, start=1):
        yield _Row(table, line, row)


class RenderTables:
    """The rows a plan reads, indexed by the ids it looks up, for one build."""

    def __init__(self, build: str, tables: Mapping[str, Iterable[Mapping[str, str]]]) -> None:
        missing = [name for name in REQUIRED_TABLES if name not in tables]
        if missing:
            raise RenderPlanError(f"build {build}: tables missing: {missing}")
        self.build = build
        self.race_models: dict[tuple[int, int], list[int]] = defaultdict(list)
        for r in _rows("ChrRaceXChrModel", tables["ChrRaceXChrModel"]):
            key = (r.number("ChrRacesID"), r.number("Sex"))
            self.race_models[key].append(r.number("ChrModelID"))
        self.models: dict[int, _Row] = {
            r.number("ID"): r for r in _rows("ChrModel", tables["ChrModel"])
        }
        self.display_model: dict[int, int] = {
            r.number("ID"): r.number("ModelID")
            for r in _rows("CreatureDisplayInfo", tables["CreatureDisplayInfo"])
        }
        self.model_file: dict[int, int] = {
            r.number("ID"): r.number("FileDataID")
            for r in _rows("CreatureModelData", tables["CreatureModelData"])
        }
        self.options: dict[int, list[_Row]] = defaultdict(list)  # by ChrModelID
        self.option_of: dict[int, _Row] = {}
        for r in _rows("ChrCustomizationOption", tables["ChrCustomizationOption"]):
            self.options[r.number("ChrModelID")].append(r)
            self.option_of[r.number("ID")] = r
        self.choices: dict[int, list[_Row]] = defaultdict(list)  # by option id
        self.choice_option: dict[int, int] = {}
        for r in _rows("ChrCustomizationChoice", tables["ChrCustomizationChoice"]):
            self.choices[r.number("ChrCustomizationOptionID")].append(r)
            self.choice_option[r.number("ID")] = r.number("ChrCustomizationOptionID")
        self.elements: dict[int, list[_Row]] = defaultdict(list)  # by choice id
        for r in _rows("ChrCustomizationElement", tables["ChrCustomizationElement"]):
            self.elements[r.number("ChrCustomizationChoiceID")].append(r)
        self.geosets: dict[int, _Row] = {
            r.number("ID"): r
            for r in _rows("ChrCustomizationGeoset", tables["ChrCustomizationGeoset"])
        }
        self.materials: dict[int, _Row] = {
            r.number("ID"): r
            for r in _rows("ChrCustomizationMaterial", tables["ChrCustomizationMaterial"])
        }
        self.textures: dict[int, list[tuple[int, int]]] = defaultdict(list)  # resource -> rows
        for r in _rows("TextureFileData", tables["TextureFileData"]):
            self.textures[r.number("MaterialResourcesID")].append(
                (r.number("FileDataID"), r.number("UsageType"))
            )
        self.layers: dict[tuple[int, int], list[_Row]] = defaultdict(list)  # (layout, target)
        for r in _rows("ChrModelTextureLayer", tables["ChrModelTextureLayer"]):
            layout = r.number("CharComponentTextureLayoutsID")
            targets = {r.number("ChrModelTextureTargetID_0"), r.number("ChrModelTextureTargetID_1")}
            for target in sorted(targets - {0}):
                self.layers[(layout, target)].append(r)
        self.sections: dict[int, dict[int, Section]] = defaultdict(dict)
        for r in _rows("CharComponentTextureSections", tables["CharComponentTextureSections"]):
            self.sections[r.number("CharComponentTextureLayoutID")][r.number("SectionType")] = (
                Section(
                    section_type=r.number("SectionType"),
                    x=r.number("X"),
                    y=r.number("Y"),
                    width=r.number("Width"),
                    height=r.number("Height"),
                )
            )
        self.layouts: dict[int, tuple[int, int]] = {
            r.number("ID"): (r.number("Width"), r.number("Height"))
            for r in _rows("CharComponentTextureLayouts", tables["CharComponentTextureLayouts"])
        }

    @classmethod
    def from_gamedata(cls, data: GameData, build: str) -> RenderTables:
        """Read through `gamedata`'s never-overwrite cache (ADR-0022), one
        fetch per table not cached yet. `build` is the full version string."""
        return cls(build, {name: data.rows(name, build) for name in REQUIRED_TABLES})


# ─── building ────────────────────────────────────────────────────────────────


def _model_row(tables: RenderTables, model_id: int) -> _Row:
    row = tables.models.get(model_id)
    if row is None:
        raise RenderPlanError(f"build {tables.build}: ChrModel has no row {model_id}")
    return row


def _body_file(tables: RenderTables, model_id: int) -> tuple[int, int, int] | str:
    """(display id, creature model data id, file data id), or why not."""
    row = _model_row(tables, model_id)
    display = row.number("DisplayID")
    cmd = tables.display_model.get(display)
    if cmd is None:
        return f"CreatureDisplayInfo has no row {display}"
    fdid = tables.model_file.get(cmd)
    if fdid is None:
        return f"CreatureModelData has no row {cmd}"
    if fdid == 0:
        return f"CreatureModelData row {cmd} names no file"
    return display, cmd, fdid


def _follow(
    tables: RenderTables, start: int, column: str, ok: Callable[[int], bool], notes: list[str]
) -> int | None:
    """The first model from `start` along `column` for which `ok` holds."""
    seen = [start]
    current = start
    for _ in range(_MAX_FALLBACK_HOPS):
        if ok(current):
            return current
        nxt = _model_row(tables, current).number(column)
        if nxt == 0 or nxt in seen:
            return None
        notes.append(f"ChrModel {current}: followed {column} to {nxt}")
        seen.append(nxt)
        current = nxt
    return None


def _body(tables: RenderTables, race_id: int, sex: int, notes: list[str]) -> BodyModel:
    models = tables.race_models.get((race_id, sex), [])
    if not models:
        raise RenderPlanError(
            f"build {tables.build}: ChrRaceXChrModel has no row for race {race_id}, sex {sex}"
        )
    if len(models) > 1:
        notes.append(
            f"ChrRaceXChrModel names {len(models)} models for race {race_id}, sex {sex}: "
            f"{models}; the first is drawn"
        )
    model_id = models[0]
    first = _body_file(tables, model_id)
    drawn = _follow(
        tables,
        model_id,
        "ModelFallbackChrModelID",
        lambda m: not isinstance(_body_file(tables, m), str),
        notes,
    )
    if drawn is None:
        raise RenderPlanError(
            f"build {tables.build}: ChrModel {model_id} gives no body model ({first}) and no "
            "model fallback gives one"
        )
    found = _body_file(tables, drawn)
    assert not isinstance(found, str)
    display, cmd, fdid = found
    layout_model = _follow(
        tables,
        model_id,
        "TextureFallbackChrModelID",
        lambda m: _model_row(tables, m).number("CharComponentTextureLayoutID") in tables.layouts,
        notes,
    )
    layout_from = layout_model if layout_model is not None else model_id
    layout_id = _model_row(tables, layout_from).number("CharComponentTextureLayoutID")
    size = tables.layouts.get(layout_id)
    if size is None:
        notes.append(
            f"CharComponentTextureLayouts has no row {layout_id}: the canvas size is unknown"
        )
    return BodyModel(
        race_id=race_id,
        sex=sex,
        chr_model_id=model_id,
        drawn_with_chr_model_id=drawn,
        display_id=display,
        creature_model_data_id=cmd,
        file_data_id=fdid,
        layout_id=layout_id,
        layout_from_chr_model_id=layout_from,
        layout_width=None if size is None else size[0],
        layout_height=None if size is None else size[1],
    )


def _option_ids(tables: RenderTables, model_id: int) -> list[int]:
    rows = sorted(
        tables.options.get(model_id, []), key=lambda r: (r.number("OrderIndex"), r.number("ID"))
    )
    return [r.number("ID") for r in rows]


def _record_choices(
    tables: RenderTables, model_id: int, recorded: Mapping[int, int], without: Sequence[int]
) -> tuple[list[ChosenChoice], list[OptionNotChosen]]:
    options = _option_ids(tables, model_id)
    on_model = set(options)
    chosen: list[ChosenChoice] = []
    not_chosen: list[OptionNotChosen] = []
    for option_id in options:
        if option_id in recorded:
            choice_id = recorded[option_id]
            owner = tables.choice_option.get(choice_id)
            if owner is None:
                not_chosen.append(
                    OptionNotChosen(
                        option_id=option_id,
                        choice_id=choice_id,
                        reason=f"choice {choice_id} is unknown to build {tables.build} "
                        "(possibly a hotfix); not drawn",
                    )
                )
            elif owner != option_id:
                not_chosen.append(
                    OptionNotChosen(
                        option_id=option_id,
                        choice_id=choice_id,
                        reason=f"choice {choice_id} belongs to option {owner}, not this one; "
                        "not drawn",
                    )
                )
            else:
                chosen.append(
                    ChosenChoice(option_id=option_id, choice_id=choice_id, source="record")
                )
        elif option_id in without:
            not_chosen.append(
                OptionNotChosen(option_id=option_id, reason="the record holds no choice id for it")
            )
        else:
            not_chosen.append(
                OptionNotChosen(
                    option_id=option_id,
                    reason="not in the record (the barber shop did not list it); not defaulted",
                )
            )
    for option_id, choice_id in sorted(recorded.items()):
        if option_id not in on_model:
            not_chosen.append(
                OptionNotChosen(
                    option_id=option_id,
                    choice_id=choice_id,
                    reason=f"the record's option is not an option of ChrModel {model_id}; not drawn",
                )
            )
    return chosen, not_chosen


def _default_choices(
    tables: RenderTables, model_id: int
) -> tuple[list[ChosenChoice], list[OptionNotChosen]]:
    chosen: list[ChosenChoice] = []
    not_chosen: list[OptionNotChosen] = []
    for option_id in _option_ids(tables, model_id):
        requirement = tables.option_of[option_id].number("Requirement")
        if requirement != 0:
            not_chosen.append(
                OptionNotChosen(
                    option_id=option_id,
                    reason=f"the option has requirement {requirement}, which is not evaluated "
                    "here; not defaulted",
                )
            )
            continue
        rows = tables.choices.get(option_id, [])
        if not rows:
            not_chosen.append(
                OptionNotChosen(option_id=option_id, reason="the option has no choices")
            )
            continue
        first = min(rows, key=lambda r: (r.number("OrderIndex"), r.number("ID")))
        chosen.append(
            ChosenChoice(option_id=option_id, choice_id=first.number("ID"), source="default")
        )
    return chosen, not_chosen


def _sections(
    tables: RenderTables, layout_id: int, mask: int, mask2: int
) -> tuple[bool, tuple[Section, ...], list[int]]:
    """(whole canvas, the sections, section types the layout lacks)."""
    if mask == _ALL_SECTIONS:
        return True, (), []
    wanted = [n for n in range(_SECTION_BITS) if mask >> n & 1]
    if mask2 not in (_ALL_SECTIONS, 0):
        wanted += [_SECTION_BITS + n for n in range(_SECTION_BITS) if mask2 >> n & 1]
    have = tables.sections.get(layout_id, {})
    return False, tuple(have[n] for n in wanted if n in have), [n for n in wanted if n not in have]


def _place(
    tables: RenderTables, model_id: int, target: int, notes: list[str]
) -> tuple[_Row, int, int] | None:
    """The layer row placing `target`, its layout and the model whose layout
    it is (the texture fallback's when the model's own places nothing)."""

    def places(m: int) -> bool:
        layout = _model_row(tables, m).number("CharComponentTextureLayoutID")
        return bool(tables.layers.get((layout, target)))

    found = _follow(tables, model_id, "TextureFallbackChrModelID", places, notes)
    if found is None:
        return None
    layout = _model_row(tables, found).number("CharComponentTextureLayoutID")
    rows = tables.layers[(layout, target)]
    if len(rows) > 1:
        notes.append(
            f"layout {layout} has {len(rows)} layer rows for texture target {target}; the first "
            "is used"
        )
    return rows[0], layout, found


def _elements(
    tables: RenderTables,
    model_id: int,
    chosen: Sequence[ChosenChoice],
    notes: list[str],
) -> tuple[
    list[GeosetSelection], list[TextureLayer], list[UnplacedMaterial], list[UnsupportedElement]
]:
    selected = {c.choice_id for c in chosen}
    geosets: list[GeosetSelection] = []
    layers: list[TextureLayer] = []
    unplaced: list[UnplacedMaterial] = []
    unsupported: list[UnsupportedElement] = []
    for choice in chosen:
        for el in tables.elements.get(choice.choice_id, []):
            related = el.number("RelatedChrCustomizationChoiceID")
            if related and related not in selected:
                continue
            eid = el.number("ID")
            geo_id = el.number("ChrCustomizationGeosetID")
            if geo_id:
                g = tables.geosets.get(geo_id)
                if g is None:
                    notes.append(f"element {eid}: ChrCustomizationGeoset has no row {geo_id}")
                else:
                    group, value = g.number("GeosetType"), g.number("GeosetID")
                    geosets.append(
                        GeosetSelection(
                            element_id=eid,
                            choice_id=choice.choice_id,
                            geoset_row_id=geo_id,
                            group=group,
                            value=value,
                            geoset_id=group * 100 + value,
                            modifier=g.number("Modifier"),
                        )
                    )
            mat_id = el.number("ChrCustomizationMaterialID")
            if mat_id:
                layer = _layer(tables, model_id, eid, choice.choice_id, mat_id, notes)
                if isinstance(layer, TextureLayer):
                    layers.append(layer)
                else:
                    unplaced.append(layer)
            for column, kind in _UNSUPPORTED_KINDS:
                ref = el.number(column) if el.has(column) else 0
                if ref:
                    unsupported.append(
                        UnsupportedElement(
                            element_id=eid, choice_id=choice.choice_id, kind=kind, ref_id=ref
                        )
                    )
    layers.sort(key=lambda la: (la.texture_type, la.layer, la.element_id))
    return geosets, layers, unplaced, unsupported


def _layer(
    tables: RenderTables, model_id: int, eid: int, choice_id: int, mat_id: int, notes: list[str]
) -> TextureLayer | UnplacedMaterial:
    def refuse(reason: str) -> UnplacedMaterial:
        return UnplacedMaterial(
            element_id=eid, choice_id=choice_id, material_id=mat_id, reason=reason
        )

    mat = tables.materials.get(mat_id)
    if mat is None:
        return refuse(f"ChrCustomizationMaterial has no row {mat_id}")
    target = mat.number("ChrModelTextureTargetID")
    resources = mat.number("MaterialResourcesID")
    diffuse = [f for f, usage in tables.textures.get(resources, []) if usage == _DIFFUSE_USAGE]
    if not diffuse:
        return refuse(f"TextureFileData has no usage-{_DIFFUSE_USAGE} row for resource {resources}")
    placed = _place(tables, model_id, target, notes)
    if placed is None:
        return refuse(
            f"no ChrModelTextureLayer of this model's layout (or a texture fallback's) places "
            f"texture target {target}"
        )
    row, layout, layout_model = placed
    mask, mask2 = row.number("TextureSectionTypeBitMask"), row.number("TextureSectionTypeBitMask2")
    whole, sections, lacking = _sections(tables, layout, mask, mask2)
    if lacking:
        notes.append(f"element {eid}: layout {layout} has no section of type {lacking}")
    return TextureLayer(
        element_id=eid,
        choice_id=choice_id,
        material_id=mat_id,
        texture_target_id=target,
        material_resources_id=resources,
        file_data_id=diffuse[0],
        usage_type=_DIFFUSE_USAGE,
        other_file_data_ids=tuple(diffuse[1:]),
        layer_row_id=row.number("ID"),
        texture_type=row.number("TextureType"),
        layer=row.number("Layer"),
        blend_mode=row.number("BlendMode"),
        section_mask=mask,
        section_mask2=mask2,
        whole_canvas=whole,
        sections=sections,
        layout_id=layout,
        layout_from_chr_model_id=layout_model,
    )


def _files(
    body: BodyModel, layers: Sequence[TextureLayer], statter: FileStatter | None
) -> tuple[PlanFile, ...]:
    wanted: list[tuple[int, Literal["body model", "texture"]]] = [(body.file_data_id, "body model")]
    seen = {body.file_data_id}
    for layer in layers:
        if layer.file_data_id not in seen:
            seen.add(layer.file_data_id)
            wanted.append((layer.file_data_id, "texture"))
    return tuple(
        PlanFile(
            file_data_id=fdid,
            role=role,
            stat=None if statter is None else statter.stat(fdid, in_table=True),
        )
        for fdid, role in wanted
    )


def build_plan(
    tables: RenderTables,
    identity: labaddon.CharIdentity,
    customization: labaddon.CustomizationImport | None,
    *,
    no_customization_reason: str | None = None,
    statter: FileStatter | None = None,
    saved_at: datetime | None = None,
) -> RenderPlan:
    """The plan for a character: `identity` (`labaddon.identity_of`) and its
    customization record (`labaddon.customization_import`; None without one,
    `no_customization_reason` saying why). Race and body type come from the
    identity; when it lacks them, from the customization record, noted.
    `statter` (a `casc.CascSource`) gives each file's state; `saved_at` is
    the record file's save time, the only date it has."""
    notes: list[str] = []
    race_id, sex = identity.race_id, identity.sex
    if race_id is None or sex is None:
        if customization is None:
            raise RenderPlanError(
                "the record names no race or body type: "
                f"{identity.race_id_absent or identity.sex_absent}; and it holds no "
                "customization record to take them from"
            )
        notes.append(
            "race and body type taken from the customization record: the identity record "
            f"lacks them ({identity.race_id_absent or identity.sex_absent})"
        )
        race_id, sex = customization.race_id, customization.body_type
    if identity.sex_raw is not None and identity.sex_raw != 2:
        notes.append(
            f"UnitSex returned {identity.sex_raw}; only 2 (read as body type 0) has been "
            "captured, so the mapping of other values is unconfirmed"
        )
    body = _body(tables, race_id, sex, notes)

    use_record = customization is not None
    why: str | None = None
    if customization is not None and (customization.race_id, customization.body_type) != (
        race_id,
        sex,
    ):
        use_record = False
        why = (
            f"the customization record is for race {customization.race_id}, body type "
            f"{customization.body_type}, not the character's race {race_id}, body type {sex} "
            "(a race or body change since the last barber visit); the model's defaults are drawn"
        )
    elif customization is None:
        why = (
            "no customization record"
            + (f" ({no_customization_reason})" if no_customization_reason else "")
            + "; the model's default choices are drawn"
        )
    if customization is not None and use_record:
        if (
            customization.chr_model_id is not None
            and customization.chr_model_id != body.chr_model_id
        ):
            notes.append(
                f"the customization record names ChrModel {customization.chr_model_id}; the race "
                f"and body type name {body.chr_model_id}, which is drawn"
            )
        chosen, not_chosen = _record_choices(
            tables, body.chr_model_id, customization.choices, customization.without_choice
        )
        look = LookAsOf(
            source="record",
            as_of=customization.as_of,
            recorded_at=customization.recorded_at,
            carried=customization.carried,
            loads_ago=customization.loads_ago,
            client_build=customization.client_build,
            saved_at=saved_at,
        )
    else:
        chosen, not_chosen = _default_choices(tables, body.chr_model_id)
        look = LookAsOf(source="defaults", why=why, saved_at=saved_at)
        assert why is not None
        notes.append(why)

    geosets, layers, unplaced, unsupported = _elements(tables, body.chr_model_id, chosen, notes)
    return RenderPlan(
        build=tables.build,
        class_id=identity.class_id,
        body=body,
        look=look,
        choices=tuple(chosen),
        not_chosen=tuple(not_chosen),
        geosets=GeosetPlan(selections=tuple(geosets)),
        layers=tuple(layers),
        unplaced=tuple(unplaced),
        unsupported=tuple(unsupported),
        files=_files(body, layers, statter),
        notes=tuple(dict.fromkeys(notes)),
    )


def plan_for_record(
    tables: RenderTables,
    char: labaddon.CharDB,
    *,
    statter: FileStatter | None = None,
    saved_at: datetime | None = None,
) -> RenderPlan:
    """`build_plan` for a character's lab-addon record as read
    (`labaddon.read_char`)."""
    identity = labaddon.identity_of(char)
    try:
        look: labaddon.CustomizationImport | None = labaddon.customization_import(char)
        reason = None
    except labaddon.NoCustomizationError as exc:
        look = None
        reason = exc.reason or str(exc)
    return build_plan(
        tables,
        identity,
        look,
        no_customization_reason=reason,
        statter=statter,
        saved_at=saved_at,
    )
