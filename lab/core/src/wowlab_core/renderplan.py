"""renderplan: a recorded character's body, customization and gear as the
files and rules a renderer needs (M15-09; the gear chain M15-13).

Spec: docs/LAB_PLAN.md §17.4, the body, customization, gear, attachment and
geoset bullets. Data: ADR-0022 (tables keyed by the full build string; the
build comes from the caller or discovery, never from here, L6).

Inputs: the lab-addon's `identity` record (schema 4, M15-01), its
`customization` record (`labaddon.customization_import`) and its `gear`
section as `char gear` reads it (`equipped_items`: slot, item id, bonus
ids), the game tables of one build (`RenderTables`, read through
`gamedata`, the table-access path this module uses), and optionally a
`FileStatter` (`casc.CascSource`) that says where each file stands in
§17.1's four states.

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
  `TextureFallbackChrModelID`'s layout. Each hop is noted; a fallback id
  naming no `ChrModel` row ends the walk (a material it was to place is
  listed as unplaced, with a note). At 70959 the models of the playable
  races (1 to 8, 95, 96 by `ChrRaces` at 70058, `looks`' rule; `ChrRaces`
  is not recorded at 70959) have no fallback ids; 23 models of other races
  do. The rule is from memory **[verify]**.
  `HelmVisFallbackChrModelID` is the gear's (below).
- **Choices.** With a customization record, its choices, exactly: an option
  the model has and the record lacks is listed, never defaulted (the barber
  shop did not list it when the record was taken, or the option is newer
  than the record's build). When the record's client build is not the
  tables' build, a note says choice ids can change between builds. Without
  a record (it exists only after a barber visit with the addon on), or with
  one for another race or body type, each option of the model whose
  `Requirement` is 0 takes its first choice (lowest `OrderIndex`, then id)
  as a stand-in, not the character's look; whether the client's own
  default is the first choice is **[verify]**. An option with a
  requirement is listed, not defaulted: requirements (the option's and the
  choice's) are not evaluated here; `looks` has the evaluator.
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
  covers the whole canvas of the layer's texture type (body 1, hair 6 and
  eyes 19 are separate textures; hair is never painted onto the body),
  **[verify]**; otherwise bit *n* of `TextureSectionTypeBitMask`
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

The gear (M15-13), slot by slot in the client's slot order:

- **Slots.** The record's slot numbers are the client's equipment slots
  (`INVSLOT_*`: 1 head, 3 shoulders, 4 shirt, 5 chest, 6 waist, 7 legs, 8
  feet, 9 wrists, 10 hands, 15 back, 16 main hand, 17 off hand, 18 ranged,
  19 tabard). Neck, fingers and trinkets (2, 11 to 14) show nothing on the
  body and are listed as such; the ranged slot (18: bow, gun, crossbow,
  wand, thrown; Classic-era characters carry one, Sangraal-Sett a wand) is
  listed as unsupported in this wave. Any other number is listed.
- **Appearance.** Item id and bonus ids → `ItemBonus` rows of type 7 of
  each bonus list (`ParentItemBonusListID` is the bonus id): `Value_0` the
  appearance modifier, `Value_1` the priority, the lowest priority winning
  (ties: the earlier bonus id). From TrinityCore, **[verify]** against a
  real link. At 70959 `ItemBonus` has no type-7 row at all, so every item
  draws its default: `ItemModifiedAppearance` with modifier 0, else the
  lowest (then `OrderIndex`, then id); every item there has exactly one row,
  modifier 0. A modifier no row lists is noted and the default drawn. →
  `ItemAppearance.ItemDisplayInfoID` → `ItemDisplayInfo`. A missing row
  lists the item as not drawn (the plan is still made). Transmog is not
  rendered: the addon records none, so the item's own look is drawn.
- **Geosets.** Per slot, `GeosetGroup_<n>` selects in the slot's n-th group
  (`SLOT_GEOSET_GROUPS`, the wow.export table after WoW Model Viewer,
  **[verify]**) geoset `group * 100 + 1 + value` (the `+1` **[verify]**
  against wow.export's `calculateEquipmentGeosets`); a value in a cell the
  slot has no group for is noted, not drawn. The feet's group 20 with value
  0 selects 2002 (the "feet `0 → 2`" rule, read as: a worn shoe covers the
  foot; whether it is group 20's or group 5's is **[verify]**). Where two
  slots select one group the priority decides: chest over shirt for groups
  8 (sleeves) and 10, chest over legs for 13 (a robe's skirt over the
  trousers); the first slot of the priority whose value is not 0 takes the
  group, the first when all are 0 (whether a 0 claims the group is
  **[verify]**). Gloves whose group-4 value is not 0 leave group 8 at its
  default ("gloves over chest over shirt for group 8", read as gloves
  covering the sleeves, **[verify]**). The losers stay in the plan with
  `overridden_by`. An item's group replaces the customization's.
- **Helmet.** The head item's `HelmetGeosetVis_<body type>` (0 and 1,
  **[verify]**) → the `HelmetGeosetData` rows of that visibility id for the
  race (through `HelmVisFallbackChrModelID`'s race, then race 0, when it
  has none): each `HideGeosetGroup` hides its whole group but geoset 0 (0
  the hair, 7 the ears). `RaceBitSelection` is kept, not applied; the
  column wago.tools names `Field_10_0_0_46047_003` (-1 or 32 at 70959) is
  not read. `HelmetGeosetVisData` is not published and not needed.
- **Models.** `ModelResourcesID_<n>` → `ModelFileData` files, filtered by
  `ComponentModelFileData` (`_pick_file`): race and body type, then the
  `ModelFallbackChrModelID` walk, then the race with either body type,
  then any race (`RaceID` 0) with the body type, then any race and either
  body type (`GenderIndex` 2 or 3: at 70959 the model rows of race 0 are
  all 2 and the texture rows mostly 3, **[verify]**); a class must be 0 or
  the character's; a shoulder's model n is side `PositionIndex` n. A file
  with no component row (weapons, shields at 70959) is drawn as is. The
  model is drawn at `SLOT_ATTACHMENTS` (head 11, shoulders 6 and 5, back 12,
  main hand 1, off hand 2, a shield, `InventoryType` 14, at 0) in the bind
  pose **[verify]**; a model no attachment takes (a belt's, a chest's) is a
  collection model, skinned to the body and drawn at the origin
  **[verify]**. `ModelType` and `AttachmentGeosetGroup` are kept, not
  applied (every `AttachmentGeosetGroup` cell is 0 at 70959).
- **Textures.** `ModelMaterialResourcesID_<n>` → the diffuse
  `TextureFileData` rows (usage 0, as for the customization) filtered by
  `ComponentTextureFileData` with the texture fallback; with no model at n
  the back slot's texture is the cloak's, drawn on the body's cape geosets
  **[verify]**. Body-texture pieces: `ItemDisplayInfoMaterialRes` rows of
  the display, each component section placed in the body layout's section
  of the same type (**[verify]**; at 70959 sections 0 to 7 appear), the
  file filtered the same way. The order pieces are painted in is the
  compositor's (M15-10).

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

from wowlab_core import itemlink, labaddon
from wowlab_core.casc import CascFileStat
from wowlab_core.errors import WowlabError

if TYPE_CHECKING:
    from wowlab_core.gamedata import GameData

__all__ = [
    "BODY_TABLES",
    "DEFAULT_RULE",
    "DEFAULT_SHOWN_GROUPS",
    "GEAR_TABLES",
    "HIDDEN_GROUPS",
    "REQUIRED_TABLES",
    "SHIELD_ATTACHMENT",
    "SKIPPED_TABLES",
    "SLOT_ATTACHMENTS",
    "SLOT_GEOSET_GROUPS",
    "BodyModel",
    "ChosenChoice",
    "EquippedItem",
    "FileStatter",
    "GearAsOf",
    "GearFileMissing",
    "GearItem",
    "GearNotDrawn",
    "GeosetPlan",
    "GeosetSelection",
    "HelmetHide",
    "ItemGeosetSelection",
    "ItemModel",
    "ItemTexture",
    "ItemTexturePiece",
    "LookAsOf",
    "Match",
    "OptionNotChosen",
    "PlanFile",
    "RenderPlan",
    "RenderPlanError",
    "RenderTables",
    "Role",
    "Section",
    "TextureLayer",
    "UnplacedMaterial",
    "UnsupportedElement",
    "build_plan",
    "equipped_items",
    "is_default_geoset",
    "plan_for_record",
    "visible_geosets",
]

BODY_TABLES: tuple[str, ...] = (
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
"""The tables the body and its customization are built from."""

GEAR_TABLES: tuple[str, ...] = (
    "Item",
    "ItemBonus",
    "ItemModifiedAppearance",
    "ItemAppearance",
    "ItemDisplayInfo",
    "ItemDisplayInfoMaterialRes",
    "ModelFileData",
    "ComponentModelFileData",
    "ComponentTextureFileData",
    "HelmetGeosetData",
)
"""The further tables the gear is built from (`TextureFileData` is shared)."""

REQUIRED_TABLES: tuple[str, ...] = BODY_TABLES + GEAR_TABLES
"""The tables a plan is built from, by name (L6: table names only)."""

SKIPPED_TABLES: tuple[str, ...] = ("ChrCustomizationCondModel", "HelmetGeosetVisData")
"""Tables §17.4 names that are not fetched: not published for this game
version (wago.tools answers HTTP 400 at 70450 and at 70959 for the first, at
70959 for the second; §17.4: the second is not needed)."""

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
_STAND_IN = (
    "each option's first choice (lowest OrderIndex) is drawn as a stand-in, not the "
    "character's look"
)

# ─── the gear's rules (community convention: wow.export after WoW Model
# Viewer, TrinityCore for ItemBonus; each [verify], module docstring) ──────────

_BONUS_APPEARANCE = 7  # ItemBonus.Type: Value_0 the appearance modifier, Value_1 the priority
_GEOSET_GROUP_CELLS = 6  # GeosetGroup_0..5 and AttachmentGeosetGroup_0..5
_NO_POSITION = -1  # ComponentModelFileData.PositionIndex of a file for either side
_ANY_BODY_TYPE = frozenset({2, 3})  # GenderIndex of a file for either body type [verify]
_INVTYPE_SHIELD = 14  # Item.InventoryType of a shield

# The client's equipment slot numbers (INVSLOT_*), the same in every flavor
# the Lab has read; the words are for the plan's reasons.
SLOT_HEAD, SLOT_SHOULDER, SLOT_SHIRT, SLOT_CHEST, SLOT_WAIST, SLOT_LEGS = 1, 3, 4, 5, 6, 7
SLOT_FEET, SLOT_WRIST, SLOT_HANDS, SLOT_BACK = 8, 9, 10, 15
SLOT_MAIN_HAND, SLOT_OFF_HAND, SLOT_RANGED, SLOT_TABARD = 16, 17, 18, 19
_SLOT_WORDS: dict[int, str] = {
    SLOT_HEAD: "head",
    2: "neck",
    SLOT_SHOULDER: "shoulders",
    SLOT_SHIRT: "shirt",
    SLOT_CHEST: "chest",
    SLOT_WAIST: "waist",
    SLOT_LEGS: "legs",
    SLOT_FEET: "feet",
    SLOT_WRIST: "wrists",
    SLOT_HANDS: "hands",
    11: "finger",
    12: "finger",
    13: "trinket",
    14: "trinket",
    SLOT_BACK: "back",
    SLOT_MAIN_HAND: "main hand",
    SLOT_OFF_HAND: "off hand",
    SLOT_RANGED: "ranged",
    SLOT_TABARD: "tabard",
}
_NO_LOOK_SLOTS = frozenset({2, 11, 12, 13, 14})

SLOT_GEOSET_GROUPS: Mapping[int, tuple[int, ...]] = {
    SLOT_HEAD: (27, 21),
    SLOT_SHOULDER: (26,),
    SLOT_SHIRT: (8, 10),
    SLOT_CHEST: (8, 10, 13, 22, 28),
    SLOT_WAIST: (18,),
    SLOT_LEGS: (11, 9, 13),
    SLOT_FEET: (5, 20),
    SLOT_WRIST: (),
    SLOT_HANDS: (4, 23),
    SLOT_BACK: (15,),
    SLOT_MAIN_HAND: (),
    SLOT_OFF_HAND: (),
    SLOT_TABARD: (12,),
}
"""Per drawn slot, the geoset group `GeosetGroup_<n>` selects, by n."""

SLOT_ATTACHMENTS: Mapping[int, tuple[int, ...]] = {
    SLOT_HEAD: (11,),
    SLOT_SHOULDER: (6, 5),
    SLOT_BACK: (12,),
    SLOT_MAIN_HAND: (1,),
    SLOT_OFF_HAND: (2,),
}
"""Per slot, the body attachment `ModelResourcesID_<n>`'s model is drawn at,
by n; a model with none is a collection model. A shield in the off hand is
drawn at `SHIELD_ATTACHMENT` instead."""
SHIELD_ATTACHMENT = 0

# The groups two slots select, the slots in priority order: the first whose
# value is not 0 takes the group.
_GROUP_PRIORITY: Mapping[int, tuple[int, ...]] = {
    8: (SLOT_CHEST, SLOT_SHIRT),
    10: (SLOT_CHEST, SLOT_SHIRT),
    13: (SLOT_CHEST, SLOT_LEGS),
}
_SLEEVES, _GLOVES = 8, 4
_FEET_GROUP, _FEET_BARE_VALUE, _FEET_COVERED = 20, 0, 2


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


class Section(_Frozen):
    """A rectangle of the texture layout's canvas, in pixels."""

    section_type: int
    x: int
    y: int
    width: int
    height: int


class TextureLayer(_Frozen):
    """One customization material, placed. `sections` empty with
    `whole_canvas` true (mask -1) means the layer covers the whole canvas of
    its texture type: body 1, hair 6 and eyes 19 are separate textures, so
    a hair layer is never painted onto the body (**[verify]**).
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
    role: Literal["body model", "texture", "item model", "item texture"]
    stat: CascFileStat | None = None


# ─── the gear ────────────────────────────────────────────────────────────────


class EquippedItem(_Frozen):
    """One filled equipment slot as `char gear` reads it: the client's slot
    number, the link's item id (None when the link has none) and its bonus
    ids in link order."""

    slot: int
    item_id: int | None
    bonus_ids: tuple[int, ...] = ()


class GearItem(_Frozen):
    """An equipped item whose look was found: the appearance modifier (from
    an `ItemBonus` type-7 row of `appearance_bonus_id`, else the item's
    default), and the chain's ids. `inventory_type` is `Item.InventoryType`
    (None when `Item` has no row)."""

    slot: int
    item_id: int
    bonus_ids: tuple[int, ...]
    inventory_type: int | None
    appearance_modifier: int
    appearance_bonus_id: int | None
    item_modified_appearance_id: int
    item_appearance_id: int
    item_display_info_id: int


class GearNotDrawn(_Frozen):
    """An equipped item the plan does not draw, and why. `kind`: "no look"
    (a slot with nothing on the body: neck, fingers, trinkets), "unsupported"
    (the ranged slot, in this wave), "no appearance" (a row of the chain is
    missing), "unknown slot", or "no item id"."""

    slot: int
    item_id: int | None
    kind: Literal["no look", "unsupported", "no appearance", "unknown slot", "no item id"]
    reason: str


class ItemGeosetSelection(_Frozen):
    """A geoset an item's display selects: `GeosetGroup_<index>` holds
    `value`, the slot's table names `group`, and the geoset is `group * 100
    + 1 + value` (`rule` says when another rule chose it). `overridden_by`
    set: another item takes the group, and this selection is not drawn."""

    slot: int
    item_id: int
    item_display_info_id: int
    index: int
    group: int
    value: int
    geoset_id: int
    rule: str | None = None
    overridden_by: str | None = None


class HelmetHide(_Frozen):
    """A geoset group a helmet hides: the `HelmetGeosetData` row of the
    display's helmet visibility id for the race (`race_id`; another model's
    race when `from_chr_model_id` names a `HelmVisFallbackChrModelID`)."""

    slot: int
    item_id: int
    item_display_info_id: int
    helmet_geoset_vis_id: int
    helmet_geoset_row_id: int
    race_id: int
    group: int
    race_bit_selection: int
    from_chr_model_id: int


Match = Literal[
    "race and body type",
    "fallback model",
    "race, any body type",
    "any race, body type",
    "any race and body type",
    "no component row",
]


class ItemModel(_Frozen):
    """One model an item draws: `ModelResourcesID_<index>` → the
    `ModelFileData` file the component filter chose (`match`, and the
    model it matched when a fallback did), the rest listed. `kind`
    "attached": drawn at `attachment_id` of the body; "collection": skinned
    to the body, drawn at the origin. `attachment_geoset_values` are the
    display's `AttachmentGeosetGroup` cells, kept, not applied."""

    slot: int
    item_id: int
    item_display_info_id: int
    index: int
    model_resources_id: int
    file_data_id: int
    other_file_data_ids: tuple[int, ...] = ()
    match: Match
    matched_chr_model_id: int | None = None
    position_index: int | None = None
    kind: Literal["attached", "collection"]
    attachment_id: int | None
    model_type: int
    attachment_geoset_values: tuple[int, ...] = ()


class ItemTexture(_Frozen):
    """A model texture of an item: `ModelMaterialResourcesID_<index>` →
    `TextureFileData`. `target` "item model": the item's model of the same
    index; "cape": the back slot's texture with no model, drawn on the
    body's cape geosets; "no model": the display names no model there."""

    slot: int
    item_id: int
    item_display_info_id: int
    index: int
    material_resources_id: int
    file_data_id: int
    other_file_data_ids: tuple[int, ...] = ()
    match: Match
    matched_chr_model_id: int | None = None
    target: Literal["item model", "cape", "no model"]


class ItemTexturePiece(_Frozen):
    """A piece of an item painted onto the body texture: an
    `ItemDisplayInfoMaterialRes` row, its component section, the file the
    component filter chose, and the body layout's section of that type
    (None: the layout has none). The order pieces are painted in is the
    compositor's."""

    slot: int
    item_id: int
    item_display_info_id: int
    material_res_row_id: int
    component_section: int
    material_resources_id: int
    file_data_id: int
    other_file_data_ids: tuple[int, ...] = ()
    match: Match
    matched_chr_model_id: int | None = None
    section: Section | None


class GearFileMissing(_Frozen):
    """A model or texture an item names for which no file was found."""

    slot: int
    item_id: int
    item_display_info_id: int
    what: str
    reason: str


class GearAsOf(_Frozen):
    """What the gear rests on. `source` "record": the record's gear section,
    as of its last save (logout or `/reload`): `saved_at` when the caller
    gives it, `client_build` the record's client block. "none": no gear was
    drawn, `why` says why."""

    source: Literal["record", "none"]
    why: str | None = None
    client_build: str | None = None
    saved_at: datetime | None = None


class GeosetPlan(_Frozen):
    """The default rule and the customization's selections; apply them to the
    ids a skin lists with `visible_geosets`."""

    default_rule: str = DEFAULT_RULE
    default_shown_groups: tuple[int, ...] = tuple(sorted(DEFAULT_SHOWN_GROUPS))
    hidden_groups: tuple[int, ...] = tuple(sorted(HIDDEN_GROUPS))
    selections: tuple[GeosetSelection, ...] = ()
    item_selections: tuple[ItemGeosetSelection, ...] = ()
    helmet_hidden: tuple[HelmetHide, ...] = ()

    @property
    def replaced_groups(self) -> tuple[int, ...]:
        """The groups the customization replaces."""
        return tuple(sorted({s.group for s in self.selections}))

    @property
    def item_groups(self) -> tuple[int, ...]:
        """The groups the drawn items replace."""
        return tuple(sorted({s.group for s in self.item_selections if s.overridden_by is None}))

    @property
    def hidden_by_helmet(self) -> tuple[int, ...]:
        return tuple(sorted({h.group for h in self.helmet_hidden}))


class LookAsOf(_Frozen):
    """What the look rests on. `source` "record": the customization record,
    `as_of` its own words, `recorded_at`, `carried` and `loads_ago` as the
    addon kept them, `client_build` the record's client block. The record
    holds no date. `saved_at`: when the file was last saved (logout or
    `/reload`), when the caller gives it. It is a latest-possible date for
    the record, not the date of the barber visit; with `carried` true the
    record is older. `source` "defaults": no record was used, `why` says
    why."""

    source: Literal["record", "defaults"]
    why: str | None = None
    as_of: str | None = None
    recorded_at: Literal["open", "applied"] | None = None
    carried: bool | None = None
    loads_ago: int | None = None
    client_build: str | None = None
    saved_at: datetime | None = None


_NO_GEAR_GIVEN = "no gear was given"


class RenderPlan(_Frozen):
    """The body, its customization and its gear for one character at one
    build. `files` lists every file the plan names, once, in the order
    body model, customization textures, then per slot the item models, their
    textures and the body-texture pieces."""

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
    gear: GearAsOf = GearAsOf(source="none", why=_NO_GEAR_GIVEN)
    items: tuple[GearItem, ...] = ()
    items_not_drawn: tuple[GearNotDrawn, ...] = ()
    item_models: tuple[ItemModel, ...] = ()
    item_textures: tuple[ItemTexture, ...] = ()
    item_pieces: tuple[ItemTexturePiece, ...] = ()
    item_files_missing: tuple[GearFileMissing, ...] = ()
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
    sorted: the default rule; then each group a customization selection
    names replaced by the ids selected in it; then each group a drawn item
    selection names replaced the same way (an item's group wins over the
    customization's); then every group a helmet hides, hidden. Geoset 0 is
    never hidden."""
    ids = sorted(set(available))
    chosen: dict[int, set[int]] = defaultdict(set)
    for s in plan.selections:
        chosen[s.group].add(s.geoset_id)
    worn: dict[int, set[int]] = defaultdict(set)
    for i in plan.item_selections:
        if i.overridden_by is None:
            worn[i.group].add(i.geoset_id)
    for group, gids in worn.items():
        chosen[group] = gids
    hidden = set(plan.hidden_by_helmet)
    out: list[int] = []
    for gid in ids:
        group = gid // 100
        if gid != 0 and group in hidden:
            continue
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


class _Display:
    """The `ItemDisplayInfo` columns a plan reads, as numbers."""

    __slots__ = (
        "attachment_groups",
        "geoset_groups",
        "helmet_vis",
        "model_materials",
        "model_resources",
        "model_types",
    )

    def __init__(self, r: _Row) -> None:
        def cells(prefix: str, count: int) -> tuple[int, ...]:
            return tuple(r.number(f"{prefix}_{i}") for i in range(count))

        self.model_resources = cells("ModelResourcesID", 2)
        self.model_materials = cells("ModelMaterialResourcesID", 2)
        self.model_types = cells("ModelType", 2)
        self.geoset_groups = cells("GeosetGroup", _GEOSET_GROUP_CELLS)
        self.attachment_groups = cells("AttachmentGeosetGroup", _GEOSET_GROUP_CELLS)
        self.helmet_vis = cells("HelmetGeosetVis", 2)


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
        self._index_gear(tables)

    def _index_gear(self, tables: Mapping[str, Iterable[Mapping[str, str]]]) -> None:
        # The gear tables are large (ModelFileData is over 120,000 rows at
        # 70959): only the columns read are kept, as numbers.
        self.model_races: dict[int, list[int]] = defaultdict(list)  # ChrModel -> races
        for (race, _sex), model_ids in self.race_models.items():
            for m in model_ids:
                if race not in self.model_races[m]:
                    self.model_races[m].append(race)
        self.item_inventory: dict[int, int] = {
            r.number("ID"): r.number("InventoryType") for r in _rows("Item", tables["Item"])
        }
        # ItemBonus type 7 (appearance) rows by bonus list: (row, modifier, priority).
        self.appearance_bonuses: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
        for r in _rows("ItemBonus", tables["ItemBonus"]):
            if r.number("Type") == _BONUS_APPEARANCE:
                self.appearance_bonuses[r.number("ParentItemBonusListID")].append(
                    (r.number("ID"), r.number("Value_0"), r.number("Value_1"))
                )
        # ItemModifiedAppearance by item: (row, modifier, appearance, order index).
        self.item_appearances: dict[int, list[tuple[int, int, int, int]]] = defaultdict(list)
        for r in _rows("ItemModifiedAppearance", tables["ItemModifiedAppearance"]):
            self.item_appearances[r.number("ItemID")].append(
                (
                    r.number("ID"),
                    r.number("ItemAppearanceModifierID"),
                    r.number("ItemAppearanceID"),
                    r.number("OrderIndex"),
                )
            )
        self.appearance_display: dict[int, int] = {
            r.number("ID"): r.number("ItemDisplayInfoID")
            for r in _rows("ItemAppearance", tables["ItemAppearance"])
        }
        self.displays: dict[int, _Display] = {
            r.number("ID"): _Display(r) for r in _rows("ItemDisplayInfo", tables["ItemDisplayInfo"])
        }
        # ItemDisplayInfoMaterialRes by display: (row, component section, resource).
        self.display_pieces: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
        for r in _rows("ItemDisplayInfoMaterialRes", tables["ItemDisplayInfoMaterialRes"]):
            self.display_pieces[r.number("ItemDisplayInfoID")].append(
                (r.number("ID"), r.number("ComponentSection"), r.number("MaterialResourcesID"))
            )
        self.model_files: dict[int, list[int]] = defaultdict(list)  # resources -> files
        for r in _rows("ModelFileData", tables["ModelFileData"]):
            self.model_files[r.number("ModelResourcesID")].append(r.number("FileDataID"))
        # ComponentModelFileData by file: (race, gender index, class, position).
        self.component_models: dict[int, tuple[int, int, int, int]] = {
            r.number("ID"): (
                r.number("RaceID"),
                r.number("GenderIndex"),
                r.number("ClassID"),
                r.number("PositionIndex"),
            )
            for r in _rows("ComponentModelFileData", tables["ComponentModelFileData"])
        }
        # ComponentTextureFileData by file: (race, gender index, class, no position).
        self.component_textures: dict[int, tuple[int, int, int, int]] = {
            r.number("ID"): (
                r.number("RaceID"),
                r.number("GenderIndex"),
                r.number("ClassID"),
                _NO_POSITION,
            )
            for r in _rows("ComponentTextureFileData", tables["ComponentTextureFileData"])
        }
        # HelmetGeosetData by visibility id: (row, race, hidden group, race bits).
        self.helmet_rows: dict[int, list[tuple[int, int, int, int]]] = defaultdict(list)
        for r in _rows("HelmetGeosetData", tables["HelmetGeosetData"]):
            self.helmet_rows[r.number("HelmetGeosetVisDataID")].append(
                (
                    r.number("ID"),
                    r.number("RaceID"),
                    r.number("HideGeosetGroup"),
                    r.number("RaceBitSelection") if r.has("RaceBitSelection") else 0,
                )
            )

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
    """The first model from `start` along `column` for which `ok` holds; None
    when the walk ends (no id, a cycle, an id with no `ChrModel` row, noted,
    or more hops than any row needs)."""
    seen = [start]
    current = start
    for _ in range(_MAX_FALLBACK_HOPS):
        if ok(current):
            return current
        nxt = _model_row(tables, current).number(column)
        if nxt == 0 or nxt in seen:
            return None
        if nxt not in tables.models:
            notes.append(f"ChrModel {current}: {column} names {nxt}, which has no ChrModel row")
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
                    reason="not in the record (the barber shop did not list it when the record "
                    "was taken, or the option is newer than the record's build); not defaulted",
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


Role = Literal["body model", "texture", "item model", "item texture"]


def _files(
    body: BodyModel,
    layers: Sequence[TextureLayer],
    worn: _Worn,
    statter: FileStatter | None,
) -> tuple[PlanFile, ...]:
    candidates: list[tuple[int, Role]] = [(body.file_data_id, "body model")]
    candidates += [(la.file_data_id, "texture") for la in layers]
    for slot in sorted({i.slot for i in worn.items}):
        candidates += [(m.file_data_id, "item model") for m in worn.models if m.slot == slot]
        candidates += [(t.file_data_id, "item texture") for t in worn.textures if t.slot == slot]
        candidates += [(p.file_data_id, "item texture") for p in worn.pieces if p.slot == slot]
    wanted: list[tuple[int, Role]] = []
    seen: set[int] = set()
    for fdid, role in candidates:
        if fdid not in seen:
            seen.add(fdid)
            wanted.append((fdid, role))
    return tuple(
        PlanFile(
            file_data_id=fdid,
            role=role,
            stat=None if statter is None else statter.stat(fdid, in_table=True),
        )
        for fdid, role in wanted
    )


# ─── the gear ────────────────────────────────────────────────────────────────


class _Worn:
    """What the gear chain found, gathered while walking the slots."""

    def __init__(self) -> None:
        self.items: list[GearItem] = []
        self.not_drawn: list[GearNotDrawn] = []
        self.geosets: list[ItemGeosetSelection] = []
        self.helmet: list[HelmetHide] = []
        self.models: list[ItemModel] = []
        self.textures: list[ItemTexture] = []
        self.pieces: list[ItemTexturePiece] = []
        self.missing: list[GearFileMissing] = []


def _slot_word(slot: int) -> str:
    return _SLOT_WORDS.get(slot, f"slot {slot}")


def _model_sex(tables: RenderTables, model_id: int) -> int | None:
    row = _model_row(tables, model_id)
    return row.number("Sex") if row.has("Sex") else None


def _pick_file(
    tables: RenderTables,
    files: Sequence[int],
    component: Mapping[int, tuple[int, int, int, int]],
    body: BodyModel,
    class_id: int | None,
    position: int | None,
    fallback_column: str,
    notes: list[str],
) -> tuple[int, tuple[int, ...], Match, int | None] | str:
    """The file a resource draws for this character: (file, the other files
    of the same rank, the rank, the fallback model matched), or why none.
    A file with a component row is eligible when its class is 0 or the
    character's and, for a side (`position`), its position is that side or
    either. Ranks, best first: the race and body type; a fallback model's
    (along `fallback_column`); the race with either body type; any race
    with the body type; any race and either body type. When no file has a
    component row, the first is drawn."""
    if not files:
        return "the resource names no file"
    rowed = [f for f in files if f in component]
    if not rowed:
        return files[0], tuple(files[1:]), "no component row", None

    def eligible(f: int) -> bool:
        _race, _gender, cls, pos = component[f]
        if cls not in (0, class_id or 0):
            return False
        return position is None or pos in (position, _NO_POSITION)

    pool = [f for f in rowed if eligible(f)]

    def exact(races: Sequence[int], sex: int | None) -> list[int]:
        return [f for f in pool if component[f][0] in races and component[f][1] == sex]

    def of_model(m: int) -> list[int]:
        if m == body.chr_model_id:
            return exact([body.race_id], body.sex)
        return exact(tables.model_races.get(m, []), _model_sex(tables, m))

    found = _follow(tables, body.chr_model_id, fallback_column, lambda m: bool(of_model(m)), notes)
    if found is not None:
        got = of_model(found)
        if found == body.chr_model_id:
            return got[0], tuple(got[1:]), "race and body type", None
        return got[0], tuple(got[1:]), "fallback model", found
    tiers: tuple[tuple[Match, list[int]], ...] = (
        (
            "race, any body type",
            [
                f
                for f in pool
                if component[f][0] == body.race_id and component[f][1] in _ANY_BODY_TYPE
            ],
        ),
        (
            "any race, body type",
            [f for f in pool if component[f][0] == 0 and component[f][1] == body.sex],
        ),
        (
            "any race and body type",
            [f for f in pool if component[f][0] == 0 and component[f][1] in _ANY_BODY_TYPE],
        ),
        ("no component row", [f for f in files if f not in component]),
    )
    for rank, got in tiers:
        if got:
            return got[0], tuple(got[1:]), rank, None
    side = "" if position is None else f", side {position}"
    return (
        f"none of its {len(files)} files is for race {body.race_id}, body type {body.sex}"
        f"{side} (or a fallback model's, or any race or body type)"
    )


def _appearance(
    tables: RenderTables, item_id: int, bonus_ids: Sequence[int], notes: list[str]
) -> tuple[tuple[int, int, int, int], int | None] | str:
    """The `ItemModifiedAppearance` row drawn and the bonus list that chose
    it (None: the item's default), or why there is none."""
    rows = tables.item_appearances.get(item_id, [])
    if not rows:
        return f"ItemModifiedAppearance has no row for item {item_id}"
    bonus = [
        (priority, n, list_id, modifier)
        for n, list_id in enumerate(bonus_ids)
        for _row, modifier, priority in tables.appearance_bonuses.get(list_id, [])
    ]
    if bonus:
        _priority, _n, list_id, modifier = min(bonus)
        matched = [r for r in rows if r[1] == modifier]
        if matched:
            return min(matched, key=lambda r: (r[3], r[0])), list_id
        notes.append(
            f"item {item_id}: bonus {list_id} selects appearance modifier {modifier}, which "
            "ItemModifiedAppearance does not list for the item; its default appearance is drawn"
        )
    zero = [r for r in rows if r[1] == 0]
    return min(zero or rows, key=lambda r: (r[1], r[3], r[0])), None


def _item_geosets(
    slot: int, item_id: int, display_id: int, d: _Display, notes: list[str]
) -> list[ItemGeosetSelection]:
    groups = SLOT_GEOSET_GROUPS.get(slot, ())
    out: list[ItemGeosetSelection] = []
    for index, value in enumerate(d.geoset_groups):
        if index >= len(groups):
            if value:
                notes.append(
                    f"item {item_id}: ItemDisplayInfo {display_id} sets GeosetGroup_{index} to "
                    f"{value}, which no geoset group of the {_slot_word(slot)} slot takes; not drawn"
                )
            continue
        group = groups[index]
        rule = None
        geoset = group * 100 + 1 + value
        if slot == SLOT_FEET and group == _FEET_GROUP and value == _FEET_BARE_VALUE:
            geoset = group * 100 + _FEET_COVERED
            rule = f"the feet's value 0 selects {geoset}, the foot covered"
        out.append(
            ItemGeosetSelection(
                slot=slot,
                item_id=item_id,
                item_display_info_id=display_id,
                index=index,
                group=group,
                value=value,
                geoset_id=geoset,
                rule=rule,
            )
        )
    return out


def _resolve_geosets(selections: Sequence[ItemGeosetSelection]) -> list[ItemGeosetSelection]:
    """Where two slots select one group, the first slot of the group's
    priority whose value is not 0 takes it (the first in priority when all
    are 0); gloves whose group-4 value is not 0 leave group 8 at its
    default."""
    by_group: dict[int, list[ItemGeosetSelection]] = defaultdict(list)
    for s in selections:
        by_group[s.group].append(s)
    lost: dict[tuple[int, int, int], str] = {}
    for group, sels in by_group.items():
        if len({s.slot for s in sels}) < 2:
            continue
        order = _GROUP_PRIORITY.get(group, ())
        ranked = sorted(
            sels,
            key=lambda s: (order.index(s.slot) if s.slot in order else len(order), s.slot, s.index),
        )
        winner = next((s for s in ranked if s.value != 0), ranked[0])
        for s in ranked:
            if s is not winner:
                lost[(s.slot, s.group, s.index)] = (
                    f"the {_slot_word(winner.slot)} item takes geoset group {group}"
                )
    gloves = any(s.slot == SLOT_HANDS and s.group == _GLOVES and s.value != 0 for s in selections)
    if gloves:
        for s in by_group.get(_SLEEVES, []):
            lost.setdefault(
                (s.slot, s.group, s.index),
                f"the gloves cover the sleeves: geoset group {_SLEEVES} is left at its default",
            )
    return [
        s.model_copy(update={"overridden_by": lost[(s.slot, s.group, s.index)]})
        if (s.slot, s.group, s.index) in lost
        else s
        for s in selections
    ]


def _helmet(
    tables: RenderTables,
    body: BodyModel,
    item: GearItem,
    d: _Display,
    notes: list[str],
) -> list[HelmetHide]:
    if body.sex not in (0, 1):
        notes.append(
            f"item {item.item_id}: body type {body.sex} names no helmet visibility; nothing hidden"
        )
        return []
    vis = d.helmet_vis[body.sex]
    if not vis:
        return []
    rows = tables.helmet_rows.get(vis, [])

    def of_model(m: int) -> list[tuple[int, int, int, int]]:
        races = [body.race_id] if m == body.chr_model_id else tables.model_races.get(m, [])
        return [r for r in rows if r[1] in races]

    found = _follow(
        tables, body.chr_model_id, "HelmVisFallbackChrModelID", lambda m: bool(of_model(m)), notes
    )
    if found is not None:
        chosen, source = of_model(found), found
    else:
        chosen, source = [r for r in rows if r[1] == 0], body.chr_model_id
    if not chosen:
        notes.append(
            f"item {item.item_id}: HelmetGeosetData has no row of visibility {vis} for race "
            f"{body.race_id} (or a fallback model's, or any race); nothing hidden"
        )
        return []
    out: list[HelmetHide] = []
    seen: set[int] = set()
    for row_id, race, group, bits in chosen:
        if group in seen:
            continue
        seen.add(group)
        out.append(
            HelmetHide(
                slot=item.slot,
                item_id=item.item_id,
                item_display_info_id=item.item_display_info_id,
                helmet_geoset_vis_id=vis,
                helmet_geoset_row_id=row_id,
                race_id=race,
                group=group,
                race_bit_selection=bits,
                from_chr_model_id=source,
            )
        )
    return out


def _diffuse(tables: RenderTables, resources: int) -> list[int]:
    return [f for f, usage in tables.textures.get(resources, []) if usage == _DIFFUSE_USAGE]


def _item_files(
    tables: RenderTables,
    body: BodyModel,
    class_id: int | None,
    item: GearItem,
    d: _Display,
    worn: _Worn,
    notes: list[str],
) -> None:
    did, slot = item.item_display_info_id, item.slot
    attachments = SLOT_ATTACHMENTS.get(slot, ())

    def missing(what: str, reason: str) -> None:
        worn.missing.append(
            GearFileMissing(
                slot=slot,
                item_id=item.item_id,
                item_display_info_id=did,
                what=what,
                reason=reason,
            )
        )

    for index, resources in enumerate(d.model_resources):
        if not resources:
            continue
        position = index if slot == SLOT_SHOULDER else None
        picked = _pick_file(
            tables,
            tables.model_files.get(resources, []),
            tables.component_models,
            body,
            class_id,
            position,
            "ModelFallbackChrModelID",
            notes,
        )
        what = f"model {index} (ModelResourcesID {resources})"
        if isinstance(picked, str):
            missing(what, picked)
            continue
        fdid, others, match, matched = picked
        attachment = attachments[index] if index < len(attachments) else None
        if (
            slot == SLOT_OFF_HAND
            and attachment is not None
            and item.inventory_type == _INVTYPE_SHIELD
        ):
            attachment = SHIELD_ATTACHMENT
        worn.models.append(
            ItemModel(
                slot=slot,
                item_id=item.item_id,
                item_display_info_id=did,
                index=index,
                model_resources_id=resources,
                file_data_id=fdid,
                other_file_data_ids=others,
                match=match,
                matched_chr_model_id=matched,
                position_index=position,
                kind="attached" if attachment is not None else "collection",
                attachment_id=attachment,
                model_type=d.model_types[index],
                attachment_geoset_values=d.attachment_groups,
            )
        )
    for index, resources in enumerate(d.model_materials):
        if not resources:
            continue
        picked = _pick_file(
            tables,
            _diffuse(tables, resources),
            tables.component_textures,
            body,
            class_id,
            None,
            "TextureFallbackChrModelID",
            notes,
        )
        what = f"model texture {index} (MaterialResourcesID {resources})"
        if isinstance(picked, str):
            missing(what, picked)
            continue
        fdid, others, match, matched = picked
        target: Literal["item model", "cape", "no model"] = "item model"
        if not d.model_resources[index]:
            target = "cape" if slot == SLOT_BACK else "no model"
        if target == "no model":
            notes.append(
                f"item {item.item_id}: ItemDisplayInfo {did} names a texture for model {index} "
                "and no model there"
            )
        worn.textures.append(
            ItemTexture(
                slot=slot,
                item_id=item.item_id,
                item_display_info_id=did,
                index=index,
                material_resources_id=resources,
                file_data_id=fdid,
                other_file_data_ids=others,
                match=match,
                matched_chr_model_id=matched,
                target=target,
            )
        )
    layout = tables.sections.get(body.layout_id, {})
    for row_id, section, resources in tables.display_pieces.get(did, []):
        picked = _pick_file(
            tables,
            _diffuse(tables, resources),
            tables.component_textures,
            body,
            class_id,
            None,
            "TextureFallbackChrModelID",
            notes,
        )
        what = f"body-texture piece {row_id} (section {section}, MaterialResourcesID {resources})"
        if isinstance(picked, str):
            missing(what, picked)
            continue
        fdid, others, match, matched = picked
        placed = layout.get(section)
        if placed is None:
            notes.append(
                f"item {item.item_id}: layout {body.layout_id} has no section of type {section}; "
                f"piece {row_id} is not placed"
            )
        worn.pieces.append(
            ItemTexturePiece(
                slot=slot,
                item_id=item.item_id,
                item_display_info_id=did,
                material_res_row_id=row_id,
                component_section=section,
                material_resources_id=resources,
                file_data_id=fdid,
                other_file_data_ids=others,
                match=match,
                matched_chr_model_id=matched,
                section=placed,
            )
        )


def _gear(
    tables: RenderTables,
    body: BodyModel,
    class_id: int | None,
    equipped: Sequence[EquippedItem],
    notes: list[str],
) -> _Worn:
    worn = _Worn()
    geosets: list[ItemGeosetSelection] = []
    for e in sorted(equipped, key=lambda e: e.slot):

        def skip(
            kind: Literal["no look", "unsupported", "no appearance", "unknown slot", "no item id"],
            reason: str,
            e: EquippedItem = e,
        ) -> None:
            worn.not_drawn.append(
                GearNotDrawn(slot=e.slot, item_id=e.item_id, kind=kind, reason=reason)
            )

        if e.slot in _NO_LOOK_SLOTS:
            skip("no look", f"the {_slot_word(e.slot)} slot shows nothing on the body")
            continue
        if e.slot == SLOT_RANGED:
            skip("unsupported", "the ranged slot is not drawn yet")
            continue
        if e.slot not in SLOT_GEOSET_GROUPS:
            skip("unknown slot", f"slot {e.slot} is not an equipment slot the plan knows")
            continue
        if e.item_id is None:
            skip("no item id", "the item link holds no item id")
            continue
        inventory = tables.item_inventory.get(e.item_id)
        if inventory is None:
            notes.append(f"item {e.item_id}: Item has no row for it; its inventory type is unknown")
        found = _appearance(tables, e.item_id, e.bonus_ids, notes)
        if isinstance(found, str):
            skip("no appearance", found)
            continue
        (ima_id, modifier, appearance_id, _order), bonus_id = found
        display_id = tables.appearance_display.get(appearance_id)
        if display_id is None:
            skip("no appearance", f"ItemAppearance has no row {appearance_id}")
            continue
        d = tables.displays.get(display_id)
        if d is None:
            skip("no appearance", f"ItemDisplayInfo has no row {display_id}")
            continue
        item = GearItem(
            slot=e.slot,
            item_id=e.item_id,
            bonus_ids=e.bonus_ids,
            inventory_type=inventory,
            appearance_modifier=modifier,
            appearance_bonus_id=bonus_id,
            item_modified_appearance_id=ima_id,
            item_appearance_id=appearance_id,
            item_display_info_id=display_id,
        )
        worn.items.append(item)
        geosets += _item_geosets(e.slot, e.item_id, display_id, d, notes)
        if e.slot == SLOT_HEAD:
            worn.helmet += _helmet(tables, body, item, d, notes)
        _item_files(tables, body, class_id, item, d, worn, notes)
    worn.geosets = _resolve_geosets(geosets)
    return worn


def equipped_items(char: labaddon.CharDB) -> tuple[EquippedItem, ...] | str:
    """The record's equipped items as `char gear` reads them (slot, item id,
    bonus ids), or why it holds none. A link the reader cannot decode raises
    `RenderPlanError` (the record reader admits none)."""
    section = char.gear
    if section is None:
        return f"the record has no gear: {labaddon.NOT_IN_FILE}"
    if isinstance(section, labaddon.AbsentSection):
        return f"the lab-addon recorded the gear as absent ({section.absent})"
    out: list[EquippedItem] = []
    for slot in sorted(section.slots, key=lambda s: s.slot):
        try:
            link = itemlink.parse(slot.link)
        except itemlink.ItemLinkError as exc:
            raise RenderPlanError(
                f"slot {slot.slot}: an item link wowlab cannot read: {exc}"
            ) from exc
        out.append(
            EquippedItem(slot=slot.slot, item_id=link.item_id, bonus_ids=tuple(link.bonus_ids))
        )
    return tuple(out)


def build_plan(
    tables: RenderTables,
    identity: labaddon.CharIdentity,
    customization: labaddon.CustomizationImport | None,
    *,
    no_customization_reason: str | None = None,
    gear: Sequence[EquippedItem] | None = None,
    no_gear_reason: str | None = None,
    gear_build: str | None = None,
    statter: FileStatter | None = None,
    saved_at: datetime | None = None,
) -> RenderPlan:
    """The plan for a character: `identity` (`labaddon.identity_of`) and its
    customization record (`labaddon.customization_import`; None without one,
    `no_customization_reason` saying why). Race and body type come from the
    identity; when it lacks them, from the customization record, noted.
    `gear`: the equipped items (`equipped_items`); None draws no gear
    (`no_gear_reason` saying why, as a note, when the record holds none).
    `gear_build`: the client build that recorded them. `statter` (a
    `casc.CascSource`) gives each file's state; `saved_at` is the record
    file's save time, the only date it has."""
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
            f"(the race or body type changed after the record was taken); {_STAND_IN}"
        )
    elif customization is None:
        why = (
            "no customization record"
            + (f" ({no_customization_reason})" if no_customization_reason else "")
            + f"; {_STAND_IN}"
        )
    if customization is not None and use_record:
        built = customization.client_build
        if built is not None and built != tables.build:
            notes.append(
                f"the record was written by {built} and is read against the tables of "
                f"{tables.build}; choice ids can change between builds"
            )
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

    if gear is None:
        worn = _Worn()
        gear_as_of = GearAsOf(source="none", why=no_gear_reason or _NO_GEAR_GIVEN)
        if no_gear_reason is not None:
            notes.append(f"no gear is drawn: {no_gear_reason}")
    else:
        if gear_build is not None and gear_build != tables.build:
            notes.append(
                f"the gear was recorded by {gear_build} and is read against the tables of "
                f"{tables.build}; an item's appearance can change between builds"
            )
        worn = _gear(tables, body, identity.class_id, gear, notes)
        gear_as_of = GearAsOf(source="record", client_build=gear_build, saved_at=saved_at)
    return RenderPlan(
        build=tables.build,
        class_id=identity.class_id,
        body=body,
        look=look,
        choices=tuple(chosen),
        not_chosen=tuple(not_chosen),
        geosets=GeosetPlan(
            selections=tuple(geosets),
            item_selections=tuple(worn.geosets),
            helmet_hidden=tuple(worn.helmet),
        ),
        layers=tuple(layers),
        unplaced=tuple(unplaced),
        unsupported=tuple(unsupported),
        gear=gear_as_of,
        items=tuple(worn.items),
        items_not_drawn=tuple(worn.not_drawn),
        item_models=tuple(worn.models),
        item_textures=tuple(worn.textures),
        item_pieces=tuple(worn.pieces),
        item_files_missing=tuple(worn.missing),
        files=_files(body, layers, worn, statter),
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
    (`labaddon.read_char`), its gear included."""
    identity = labaddon.identity_of(char)
    try:
        look: labaddon.CustomizationImport | None = labaddon.customization_import(char)
        reason = None
    except labaddon.NoCustomizationError as exc:
        look = None
        reason = exc.reason or str(exc)
    worn = equipped_items(char)
    return build_plan(
        tables,
        identity,
        look,
        no_customization_reason=reason,
        gear=None if isinstance(worn, str) else worn,
        no_gear_reason=worn if isinstance(worn, str) else None,
        gear_build=labaddon.client_build(char),
        statter=statter,
        saved_at=saved_at,
    )
