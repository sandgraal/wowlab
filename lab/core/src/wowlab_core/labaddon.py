"""labaddon: read what the lab-addon wrote (docs/LAB_PLAN.md §13.1, M11-04).

The lab-addon (`lab/addon/WowLab/`, ADR-0026) writes `WowLab.lua` into the
account's `SavedVariables/` (`WowLabDB`) and each character's
(`WowLabCharDB`), as a versioned table. This module reads those files through
`luadata` (data only, no Lua is run, L3) into Pydantic models, one per
section, keyed by schema version (`CHAR_SCHEMAS`, `ACCOUNT_SCHEMAS`). A
document whose `schema` this reader does not know is refused with a message
that names it; nothing is guessed from a newer layout.

What the models accept (M11-03 capture, `docs/LAB_FORMATS.md` amendment of
2026-09-28, and the #97 security review):

- Exact types. A number field takes a Lua number only (not a boolean, not a
  string), an id or count field an integer only, a boolean field a boolean
  only. Text is accepted only in the fields the addon writes as text: item
  links, the talent export string, absent reasons, event names, and the
  enum-like fields (`recorded_at`, `as_of`, `item_level_api`, `spec.api`,
  `found_by`, `skipped_types`, the skip list, `client.version`,
  `client.build`), each checked against the shape the addon writes. A
  hand-edited or tampered capture cannot pass free text through.
- Unknown keys are kept (L4 spirit) and come back in `model_extra` and in
  JSON, provided each is a plain name (`[A-Za-z_][A-Za-z0-9_]*`, at most 64
  characters) and its value holds no text anywhere: numbers, booleans and
  tables of them only.
- An empty Lua table reads back from `luadata.to_python` as a dict; wherever
  the addon writes a list, an empty dict is read as an empty list.
- A key the addon did not write means the client returned nil: the field is
  optional and `None`, which is never the same as "absent with a reason".
- "Absent with a reason" (`{ absent = "<reason>" }`) is accepted at every
  level the addon writes it: a whole section, the gear average, the client
  block, one trait config, a tree's currencies, one currency; plus
  `export_absent` and `last_selected_config_absent` on `talents.class`, and
  `events_unregistered` on a present or an absent section (M11-22).
- A section the owner switched off (M11-21) is an absent record with the
  addon's reason; `WowLabCharDB.skip` is kept as a list of section keys, and
  a key this reader does not know is kept but ignored (`skip_known`).

`describe` and `describe_account` turn a record into the text `wowlab char
show` prints. The wording follows the domain review of the M11-03 capture:
the equipped average is "as the client reports it", never a UI figure;
Legacy candidates with nothing spent read "present, nothing spent, N points
available"; an empty list reads "none recorded"; a trait config's `type` is
the raw client enum number; professions are named by skill line.

Reads never write (L1): `read_char` and `read_account` open one file with
`snapshot.read_regular_file` (read-only, no final link followed, no blocking
on a FIFO, bounded by `luadata.MAX_FILE_BYTES`) and write nothing anywhere.
Nothing here names a flavor, a product or a build (L6).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, Self, TypeVar

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    StringConstraints,
    Tag,
    ValidationError,
    model_validator,
)

from wowlab_core import luadata, snapshot

__all__ = [
    "ACCOUNT_SCHEMAS",
    "ACCOUNT_VARIABLE",
    "ADDON_NAME",
    "CHAR_SCHEMAS",
    "CHAR_VARIABLE",
    "PAID_CHANGE_NOTE",
    "SECTION_KEYS",
    "SWITCHED_OFF",
    "AbsentConfig",
    "AbsentCurrency",
    "AbsentRecord",
    "AbsentSection",
    "AccountDBV1",
    "CharDBV1",
    "ClassTalents",
    "LabAddonError",
    "LegacyConfig",
    "LegacyTalents",
    "customization_loads_ago",
    "describe",
    "describe_account",
    "legacy_headline",
    "load_account",
    "load_char",
    "parse_account",
    "parse_char",
    "read_account",
    "read_char",
    "skip_known",
    "unknown_keys",
]

ADDON_NAME = "WowLab"  # the addon folder and its SavedVariables file stem
CHAR_VARIABLE = "WowLabCharDB"
ACCOUNT_VARIABLE = "WowLabDB"

# The addon's section keys (§13.1, M11-21), in the order §13.1 lists them.
SECTION_KEYS: tuple[str, ...] = (
    "gear",
    "spec",
    "talents.class",
    "talents.legacy",
    "customization",
    "collections.mounts",
    "collections.toys",
    "collections.pets",
    "collections.appearances",
    "currencies",
    "professions",
)

# The addon's absent reason for a section in the skip list (Core.lua).
SWITCHED_OFF = "switched off by the owner"

PAID_CHANGE_NOTE = (
    "A paid appearance change that keeps the race is invisible to the addon: "
    "this record may be out of date."
)


class LabAddonError(ValueError):
    """A `WowLab.lua` this reader refuses: not the addon's variable, a schema
    it does not know, or a value that does not fit the schema's model."""


# ─── value shapes ────────────────────────────────────────────────────────────

_T = TypeVar("_T")


def _empty_list(value: Any) -> Any:
    """An empty Lua table comes back from `to_python` as `{}`."""
    if isinstance(value, dict) and not value:
        return []
    return value


# A list the addon writes; an empty table read back as a dict is an empty list.
Lst = Annotated[list[_T], BeforeValidator(_empty_list)]

# A Lua number: every number is a double in Lua 5.1; `to_python` gives an
# int for integer spelling and a float otherwise. Never a boolean or text.
Number = int | float

Reason = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
ItemLink = Annotated[
    str,
    StringConstraints(
        max_length=1024,
        pattern=r"^\|c[0-9A-Za-z:]{1,16}\|Hitem:[0-9:\-]*\|h\[[^\x00-\x1f\x7f\]]*\]\|h\|r$",
    ),
]
TalentExport = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9+/=]{1,4096}$")]
EventName = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")]
FoundBy = Annotated[str, StringConstraints(pattern=r"^(type|system):[A-Za-z0-9_.]{1,128}$")]
SkipKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.]{0,63}$")]
ClientVersion = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,6}(\.[0-9]{1,6}){0,5}$")]
ClientBuild = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,10}$")]

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_NESTED_NAME = re.compile(r"[A-Za-z0-9_]{1,64}")


def _key_words(key: object) -> str:
    """A key for an error message, without echoing text from the file."""
    if isinstance(key, bool):
        return "a boolean key"
    if isinstance(key, int):
        return f"the number key {key}"
    if isinstance(key, float):
        return "a number key"
    if isinstance(key, str):
        return f"a key of {len(key)} characters that is not a plain name"
    return "a key that is not a name"


def _check_unknown(path: str, value: object) -> None:
    """An unknown key's value may hold numbers, booleans and tables of them."""
    if value is None or isinstance(value, bool | int | float):
        return
    if isinstance(value, str):
        raise ValueError(
            f"the unknown key {path} holds text; text is accepted only in the fields "
            "the addon writes as text"
        )
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check_unknown(f"{path}[{index}]", item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, bool) or not (
                isinstance(key, int) or (isinstance(key, str) and _NESTED_NAME.fullmatch(key))
            ):
                raise ValueError(f"the unknown key {path} holds {_key_words(key)}")
            _check_unknown(f"{path}.{key}", item)
        return
    raise ValueError(f"the unknown key {path} holds a value of an unexpected kind")


class _Record(BaseModel):
    """Every table the addon writes: exact types, unknown keys kept (checked)."""

    model_config = ConfigDict(
        strict=True,
        extra="allow",
        frozen=True,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )

    @model_validator(mode="before")
    @classmethod
    def _keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        known: set[str] = set()
        for name, field in cls.model_fields.items():
            known.add(name)
            if field.alias:
                known.add(field.alias)
        for key, value in data.items():
            if not isinstance(key, str) or not _NAME.fullmatch(key):
                raise ValueError(f"the table holds {_key_words(key)}")
            if key not in known:
                _check_unknown(key, value)
        return data


# ─── absent records ──────────────────────────────────────────────────────────


class _Absent(_Record):
    """`{ absent = "<reason>" }`: the addon could not provide this."""

    absent: Reason


class AbsentRecord(_Absent):
    """An absent value below a section (the gear average, the client block, a
    tree's currencies)."""


class AbsentSection(_Absent):
    """A whole section absent with the addon's reason. `events_unregistered`:
    the change events the client did not know, when the addon attached them
    (a gather that failed, or since M11-22 a section never gathered)."""

    events_unregistered: Lst[EventName] | None = None


class AbsentConfig(_Absent):
    """One trait config `C_Traits.GetConfigInfo` returned nothing for."""

    id: int | None = None


class AbsentCurrency(_Absent):
    """One currency `C_CurrencyInfo.GetCurrencyInfo` returned nothing for."""

    id: int | None = None


_P = "<present>"
_A = "<absent>"


def _kind(value: Any) -> str:
    if isinstance(value, dict):
        return _A if "absent" in value else _P
    return _A if isinstance(value, _Absent) else _P


_SPLIT = Discriminator(_kind)


class _Section(_Record):
    """A present section. `events_unregistered`: change events the client did
    not know, so the section was only gathered at entering the world."""

    events_unregistered: Lst[EventName] | None = None


# ─── client, probe ───────────────────────────────────────────────────────────


class Client(_Record):
    """`GetBuildInfo()`: version, build (text, as the client gives it) and interface."""

    version: ClientVersion | None = None
    build: ClientBuild | None = None
    interface: int | None = None


class Probe(_Record):
    """`probe`: the addon's load counter (§13.1). `lost`: this load found a
    `WowLabCharDB` without a probe."""

    loads: int
    lost: bool | None = None


# ─── gear ────────────────────────────────────────────────────────────────────


class GearSlot(_Record):
    """One filled slot, identified by `slot` (the list is dense, the slot
    numbers sparse)."""

    slot: int
    link: ItemLink
    crafter_removed: bool
    item_level: int | None = None
    item_level_api: (
        Literal["C_Item.GetCurrentItemLevel", "C_Item.GetDetailedItemLevelInfo"] | None
    ) = None


class GearAverage(_Record):
    """`GetAverageItemLevel()`'s three returns as the client reports them."""

    overall: Number | None = None
    equipped: Number | None = None
    pvp: Number | None = None


class Gear(_Section):
    first_slot: int | None = None
    last_slot: int | None = None
    slots: Lst[GearSlot]
    average: (
        Annotated[Annotated[GearAverage, Tag(_P)] | Annotated[AbsentRecord, Tag(_A)], _SPLIT] | None
    ) = None


# ─── spec ────────────────────────────────────────────────────────────────────


class Spec(_Section):
    """The spec as the client exposes it, found by testing for the API."""

    api: Literal["C_SpecializationInfo", "GetSpecialization"]
    index: int | None = None
    id: int | None = None


# ─── talents ─────────────────────────────────────────────────────────────────


class TraitCurrency(_Record):
    """`C_Traits.GetTreeCurrencyInfo`, one currency: `quantity` unspent,
    `spent`, `max_quantity` the cap, raw."""

    id: int | None = None
    quantity: int | None = None
    max_quantity: int | None = None
    spent: int | None = None


class TraitNode(_Record):
    id: int
    ranks_purchased: int | None = None
    active_rank: int | None = None
    current_rank: int | None = None
    max_ranks: int | None = None
    is_visible: bool | None = None
    entries: Lst[int]
    sub_tree: int | None = None
    active_entry: int | None = None
    active_entry_rank: int | None = None


TreeCurrencies = Annotated[
    Annotated[Lst[TraitCurrency], Tag(_P)] | Annotated[AbsentRecord, Tag(_A)], _SPLIT
]


class TraitTree(_Record):
    id: int
    nodes: Lst[TraitNode]
    system_id: int | None = None
    currencies: TreeCurrencies


class TraitConfig(_Record):
    """A `C_Traits` config dump; `type` is the raw client enum number."""

    id: int
    type: int | None = None
    trees: Lst[TraitTree]


class LegacyConfig(TraitConfig):
    """A Legacy candidate config and how the addon found it."""

    found_by: Lst[FoundBy]


class AbsentLegacyConfig(AbsentConfig):
    found_by: Lst[FoundBy] | None = None


class ClassTalents(_Section):
    """`talents.class`: the active class config, the export string and the
    last selected saved loadout's id, each possibly missing (nil) or absent
    with a reason."""

    config: Annotated[Annotated[TraitConfig, Tag(_P)] | Annotated[AbsentConfig, Tag(_A)], _SPLIT]
    export: TalentExport | None = None
    export_absent: Reason | None = None
    last_selected_config: int | None = None
    last_selected_config_absent: Reason | None = None

    @model_validator(mode="after")
    def _one_of_each(self) -> Self:
        # The addon writes at most one of each value and its `_absent` reason
        # (exactly one since M11-22; neither in captures made before it).
        if self.export is not None and self.export_absent is not None:
            raise ValueError("both export and export_absent are set")
        if self.last_selected_config is not None and self.last_selected_config_absent is not None:
            raise ValueError("both last_selected_config and last_selected_config_absent are set")
        return self


class LegacyTalents(_Section):
    """`talents.legacy`: every trait config that is neither the active class
    config nor Combat nor Profession, each with `found_by`."""

    legacy_ui: bool
    player_level: int | None = None
    skipped_types: Lst[Literal["Invalid", "Combat", "Profession"]]
    configs: Lst[
        Annotated[Annotated[LegacyConfig, Tag(_P)] | Annotated[AbsentLegacyConfig, Tag(_A)], _SPLIT]
    ]


class Talents(_Record):
    class_: (
        Annotated[Annotated[ClassTalents, Tag(_P)] | Annotated[AbsentSection, Tag(_A)], _SPLIT]
        | None
    ) = Field(default=None, alias="class")
    legacy: (
        Annotated[Annotated[LegacyTalents, Tag(_P)] | Annotated[AbsentSection, Tag(_A)], _SPLIT]
        | None
    ) = None


# ─── customization ───────────────────────────────────────────────────────────


class CustomizationChoice(_Record):
    option: int
    choice_index: int | None = None
    choice: int | None = None


class Customization(_Section):
    """The last barber-shop record, carried from session to session."""

    as_of: Literal["last barber-shop visit with the addon enabled"] | None = None
    recorded_at: Literal["open", "applied"] | None = None
    recorded_load: int | None = None
    carried: bool | None = None
    choices: Lst[CustomizationChoice]
    race_id: int | None = None
    sex: int | None = None
    chr_model_id: int | None = None


# ─── collections ─────────────────────────────────────────────────────────────


class Mounts(_Section):
    collected: Lst[int]
    filtered: bool


class ToyFilter(_Record):
    collected_shown: bool | None = None
    uncollected_shown: bool | None = None
    unusable_shown: bool | None = None


class Toys(_Section):
    collected: Lst[int]
    filtered: bool
    filter: ToyFilter | None = None


class PetSpecies(_Record):
    species: int
    count: int | None = None


class Pets(_Section):
    species: Lst[PetSpecies]
    filtered: bool
    default_filters: bool | None = None


class Collections(_Record):
    mounts: (
        Annotated[Annotated[Mounts, Tag(_P)] | Annotated[AbsentSection, Tag(_A)], _SPLIT] | None
    ) = None
    toys: Annotated[Annotated[Toys, Tag(_P)] | Annotated[AbsentSection, Tag(_A)], _SPLIT] | None = (
        None
    )
    pets: Annotated[Annotated[Pets, Tag(_P)] | Annotated[AbsentSection, Tag(_A)], _SPLIT] | None = (
        None
    )
    # Never gathered in schema 1 (M11-20): always absent with a reason.
    appearances: AbsentSection | None = None


# ─── currencies, professions ─────────────────────────────────────────────────


class Currency(_Record):
    id: int
    quantity: int | None = None
    max_quantity: int | None = None
    max_weekly_quantity: int | None = None
    earned_this_week: int | None = None
    can_earn_per_week: bool | None = None
    total_earned: int | None = None
    use_total_earned_for_max: bool | None = None
    account_wide: bool | None = None


class Currencies(_Section):
    """The currency panel read through its filter and collapsed headers.
    `rows` (M11-22): the row count `GetCurrencyListSize` gave."""

    list_: Lst[
        Annotated[Annotated[Currency, Tag(_P)] | Annotated[AbsentCurrency, Tag(_A)], _SPLIT]
    ] = Field(alias="list")
    filtered: bool
    headers_collapsed: int
    filter: int | None = None
    rows: int | None = None


class Profession(_Record):
    """One profession, identified by `skill_line`; `position` is where
    `GetProfessions` returned it, not an identity."""

    position: int
    skill_line: int | None = None
    rank: int | None = None
    max_rank: int | None = None
    modifier: int | None = None


class Professions(_Section):
    list_: Lst[Profession] = Field(alias="list")


# ─── documents ───────────────────────────────────────────────────────────────


class CharDBV1(_Record):
    """`WowLabCharDB`, schema 1. A section the file does not hold is `None`
    (the addon wrote no record for it, as when a session ended before the
    addon's write); a section it holds is present or absent with a reason."""

    schema_: Literal[1] = Field(alias="schema")
    probe: Probe | None = None
    client: (
        Annotated[Annotated[Client, Tag(_P)] | Annotated[AbsentRecord, Tag(_A)], _SPLIT] | None
    ) = None
    skip: Lst[SkipKey] | None = None
    gear: Annotated[Annotated[Gear, Tag(_P)] | Annotated[AbsentSection, Tag(_A)], _SPLIT] | None = (
        None
    )
    spec: Annotated[Annotated[Spec, Tag(_P)] | Annotated[AbsentSection, Tag(_A)], _SPLIT] | None = (
        None
    )
    talents: Talents | None = None
    customization: (
        Annotated[Annotated[Customization, Tag(_P)] | Annotated[AbsentSection, Tag(_A)], _SPLIT]
        | None
    ) = None
    collections: Collections | None = None
    currencies: (
        Annotated[Annotated[Currencies, Tag(_P)] | Annotated[AbsentSection, Tag(_A)], _SPLIT] | None
    ) = None
    professions: (
        Annotated[Annotated[Professions, Tag(_P)] | Annotated[AbsentSection, Tag(_A)], _SPLIT]
        | None
    ) = None


class AccountDBV1(_Record):
    """`WowLabDB`, schema 1: holds only `schema` (every section is per
    character in schema 1); anything else is kept as an unknown key."""

    schema_: Literal[1] = Field(alias="schema")


CHAR_SCHEMAS: Mapping[int, type[CharDBV1]] = {1: CharDBV1}
ACCOUNT_SCHEMAS: Mapping[int, type[AccountDBV1]] = {1: AccountDBV1}


# ─── loading ─────────────────────────────────────────────────────────────────


def _loc(loc: tuple[int | str, ...]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        elif part.startswith("<") or part in ("list[...]", "function-before[_empty_list(), list]"):
            continue
        else:
            out += f".{part}" if out else part
    return out


def _explain(variable: str, exc: ValidationError) -> str:
    """Where and why, from the error's location and message only: the input
    value is never echoed (it may be text a tampered file carries)."""
    errors = exc.errors(include_input=False, include_url=False)
    parts = []
    for error in errors[:3]:
        where = _loc(tuple(error["loc"]))
        parts.append(
            f"{variable}.{where}: {error['msg']}" if where else f"{variable}: {error['msg']}"
        )
    more = f" (and {len(errors) - 3} more)" if len(errors) > 3 else ""
    return "; ".join(parts) + more


def _schema_model[M: BaseModel](variable: str, value: object, schemas: Mapping[int, type[M]]) -> M:
    if not isinstance(value, dict):
        raise LabAddonError(f"{variable} is not a table")
    schema = value.get("schema")
    known = ", ".join(str(s) for s in sorted(schemas))
    if schema is None:
        raise LabAddonError(f"{variable} has no schema number; this reader knows schema {known}")
    if isinstance(schema, bool) or not isinstance(schema, int):
        raise LabAddonError(
            f"{variable}.schema is not a whole number; this reader knows schema {known}"
        )
    model = schemas.get(schema)
    if model is None:
        raise LabAddonError(
            f"{variable} is schema {schema}, and this reader knows schema {known} only: "
            "it was written by another version of the lab-addon; nothing was read"
        )
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise LabAddonError(
            f"{variable} does not fit the schema-{schema} model: {_explain(variable, exc)}"
        ) from None


def load_char(value: object) -> CharDBV1:
    """`WowLabCharDB` as `luadata.LuaDocument.to_python` gives it."""
    return _schema_model(CHAR_VARIABLE, value, CHAR_SCHEMAS)


def load_account(value: object) -> AccountDBV1:
    """`WowLabDB` as `luadata.LuaDocument.to_python` gives it."""
    return _schema_model(ACCOUNT_VARIABLE, value, ACCOUNT_SCHEMAS)


def _variable(data: bytes, variable: str) -> object:
    values = luadata.parse(data).to_python()
    if variable not in values:
        found = ", ".join(sorted(values)) or "nothing"
        raise LabAddonError(f"the file assigns no {variable} (it assigns: {found})")
    value = values[variable]
    if value is None:
        raise LabAddonError(f"the file sets {variable} to nil")
    return value


def parse_char(data: bytes) -> CharDBV1:
    """A character's `WowLab.lua`, from its bytes."""
    return load_char(_variable(data, CHAR_VARIABLE))


def parse_account(data: bytes) -> AccountDBV1:
    """The account's `WowLab.lua`, from its bytes."""
    return load_account(_variable(data, ACCOUNT_VARIABLE))


def _read(path: Path) -> bytes:
    return snapshot.read_regular_file(Path(path), limit=luadata.MAX_FILE_BYTES)


def read_char(path: Path) -> CharDBV1:
    """Read a character's `WowLab.lua` (read-only; writes nothing, L1)."""
    return parse_char(_read(path))


def read_account(path: Path) -> AccountDBV1:
    """Read the account's `WowLab.lua` (read-only; writes nothing, L1)."""
    return parse_account(_read(path))


# ─── derived facts ───────────────────────────────────────────────────────────


def skip_known(char: CharDBV1) -> tuple[list[str], list[str]]:
    """The skip list split into section keys this reader knows and the rest
    (kept in the record, ignored here)."""
    known: list[str] = []
    ignored: list[str] = []
    for key in char.skip or []:
        (known if key in SECTION_KEYS else ignored).append(key)
    return known, ignored


def customization_loads_ago(char: CharDBV1) -> int | None:
    """How many logins or reloads ago the customization record was made:
    `probe.loads` minus `recorded_load`; None when either is missing or the
    difference is negative (a reset probe)."""
    record = char.customization
    if not isinstance(record, Customization) or record.recorded_load is None:
        return None
    if char.probe is None:
        return None
    ago = char.probe.loads - record.recorded_load
    return ago if ago >= 0 else None


def _extra_paths(value: object, path: str) -> Iterator[str]:
    if isinstance(value, BaseModel):
        for key in value.model_extra or {}:
            yield f"{path}.{key}" if path else key
        for name, field in type(value).model_fields.items():
            key = field.alias or name
            yield from _extra_paths(getattr(value, name), f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _extra_paths(item, f"{path}[{index}]")


def unknown_keys(record: BaseModel) -> list[str]:
    """Every key the models do not know, as a dotted path (kept in JSON)."""
    return list(_extra_paths(record, ""))


# ─── text ────────────────────────────────────────────────────────────────────

NONE_RECORDED = "none recorded"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _yes(value: bool | None) -> str:
    if value is None:
        return "not reported"
    return "yes" if value else "no"


def _num(value: Number | None) -> str:
    return "not returned" if value is None else str(value)


def _absent(record: _Absent) -> str:
    text = f"absent ({record.absent})"
    events = getattr(record, "events_unregistered", None)
    if events:
        text += f"; events the client did not know: {', '.join(events)}"
    return text


def _events(section: _Section) -> list[str]:
    if section.events_unregistered:
        return [
            "  events the client did not know (gathered only on entering the world): "
            + ", ".join(section.events_unregistered)
        ]
    return []


_LINK = re.compile(r"\|Hitem:([0-9]+)[^|]*\|h\[([^\]]*)\]")


def _item(link: str) -> str:
    match = _LINK.search(link)
    if match is None:
        return link
    return f"item {match.group(1)} [{match.group(2)}]"


def _gear(gear: Gear) -> list[str]:
    span = ""
    if gear.first_slot is not None and gear.last_slot is not None:
        span = f"slots {gear.first_slot} to {gear.last_slot} as the client numbers them, "
    lines = [f"Gear ({span}{len(gear.slots)} filled)"]
    if not gear.slots:
        lines.append(f"  slots: {NONE_RECORDED}")
    for slot in sorted(gear.slots, key=lambda s: s.slot):
        level = (
            "item level not returned"
            if slot.item_level is None
            else f"item level {slot.item_level}"
        )
        crafter = "; crafter GUID removed from the link" if slot.crafter_removed else ""
        lines.append(f"  slot {slot.slot:>2}: {_item(slot.link)}, {level}{crafter}")
    average = gear.average
    if isinstance(average, GearAverage):
        lines.append(
            f"  equipped average {_num(average.equipped)}, as the client reports it "
            f"(GetAverageItemLevel; how the client computes it is not known); "
            f"overall {_num(average.overall)}, pvp {_num(average.pvp)}"
        )
    elif isinstance(average, AbsentRecord):
        lines.append(f"  average item level: {_absent(average)}")
    else:
        lines.append("  average item level: not in the file")
    return lines + _events(gear)


def _spec(spec: Spec) -> list[str]:
    ident = "id not returned" if spec.id is None else f"id {spec.id}"
    index = "index not returned" if spec.index is None else f"index {spec.index}"
    return [f"Spec: {ident} ({index}, from {spec.api})", *_events(spec)]


def _currency_words(currencies: list[TraitCurrency] | AbsentRecord) -> str:
    if isinstance(currencies, AbsentRecord):
        return f"trait currencies {_absent(currencies)}"
    if not currencies:
        return f"trait currencies {NONE_RECORDED}"
    return "; ".join(
        f"trait currency {_num(c.id)}: {_num(c.spent)} spent, {_num(c.quantity)} unspent, "
        f"cap {_num(c.max_quantity)}"
        for c in currencies
    )


def _tree(tree: TraitTree) -> str:
    system = "" if tree.system_id is None else f" (system {tree.system_id})"
    if tree.nodes:
        ranks = sum(n.active_rank or 0 for n in tree.nodes)
        nodes = f"{len(tree.nodes)} nodes, {_count(ranks, 'rank')} active"
        if ranks == 0:
            nodes = f"{len(tree.nodes)} nodes, every one at rank 0"
    else:
        nodes = f"nodes {NONE_RECORDED}"
    return f"tree {tree.id}{system}: {nodes}; {_currency_words(tree.currencies)}"


def _config_head(config: TraitConfig) -> str:
    kind = "type not returned" if config.type is None else f"type {config.type}"
    return f"config {config.id}, {kind} (the client's raw enum number)"


def _class_talents(talents: ClassTalents) -> list[str]:
    config = talents.config
    if isinstance(config, AbsentConfig):
        lines = [f"Class talents: config {_num(config.id)} {_absent(config)}"]
    else:
        lines = [f"Class talents: {_config_head(config)}"]
        if not config.trees:
            lines.append(f"  trees: {NONE_RECORDED}")
        lines.extend(f"  {_tree(tree)}" for tree in config.trees)
    if talents.export is not None:
        lines.append(f"  export string: {talents.export}")
    elif talents.export_absent is not None:
        lines.append(f"  export string: absent ({talents.export_absent})")
    else:
        lines.append("  export string: not returned by the client")
    if talents.last_selected_config is not None:
        lines.append(f"  last selected saved loadout: {talents.last_selected_config} (raw)")
    elif talents.last_selected_config_absent is not None:
        lines.append(
            f"  last selected saved loadout: absent ({talents.last_selected_config_absent})"
        )
    else:
        lines.append("  last selected saved loadout: not returned by the client")
    return lines + _events(talents)


def legacy_headline(legacy: LegacyTalents) -> str:
    """One line for the Legacy candidates, never "empty" or "locked": the
    addon lists candidates by elimination, and below the unlock level the
    client still returns the trees, with nothing spent and a cap of 0."""
    if not legacy.configs:
        return f"Legacy candidates: {NONE_RECORDED}"
    configs = [c for c in legacy.configs if isinstance(c, LegacyConfig)]
    if not configs:
        return "Legacy candidates: present, every config absent with a reason"
    nodes = [n for c in configs for t in c.trees for n in t.nodes]
    currencies = [
        cur
        for c in configs
        for t in c.trees
        if not isinstance(t.currencies, AbsentRecord)
        for cur in t.currencies
    ]
    ranks = sum(n.active_rank or 0 for n in nodes)
    spent = sum(cur.spent or 0 for cur in currencies)
    if ranks == 0 and spent == 0:
        spent_words = "nothing spent"
    else:
        spent_words = f"{_count(ranks, 'rank')} active, {_count(spent, 'point')} spent"
    quantities = [cur.quantity for cur in currencies if cur.quantity is not None]
    available = (
        f"{sum(quantities)} points available" if quantities else "points available not reported"
    )
    return f"Legacy candidates: present, {spent_words}, {available}"


def _legacy(legacy: LegacyTalents) -> list[str]:
    level = "" if legacy.player_level is None else f" (level {legacy.player_level})"
    lines = [f"{legacy_headline(legacy)}{level}"]
    lines.append(
        "  which config is the Legacy system is inferred by elimination; "
        f"panel opener ToggleLegacySystemUI present: {_yes(legacy.legacy_ui)}"
    )
    skipped = ", ".join(legacy.skipped_types) or NONE_RECORDED
    lines.append(f"  config types not searched: {skipped}")
    for config in legacy.configs:
        found = ", ".join(config.found_by or []) or NONE_RECORDED
        if isinstance(config, AbsentLegacyConfig):
            lines.append(f"  config {_num(config.id)} {_absent(config)}; found by {found}")
            continue
        lines.append(f"  {_config_head(config)}, found by {found}")
        if not config.trees:
            lines.append(f"    trees: {NONE_RECORDED}")
        lines.extend(f"    {_tree(tree)}" for tree in config.trees)
    return lines + _events(legacy)


def _customization(record: Customization, char: CharDBV1) -> list[str]:
    ago = customization_loads_ago(char)
    if ago is None:
        when = "an unknown number of logins or reloads ago"
    else:
        when = f"{ago} login{'' if ago == 1 else 's'} or reloads ago"
    facts = []
    if record.recorded_at is not None:
        facts.append(f"recorded at {record.recorded_at}")
    if record.carried:
        facts.append("carried from an earlier session")
    for label, value in (
        ("model", record.chr_model_id),
        ("race", record.race_id),
        ("sex", record.sex),
    ):
        if value is not None:
            facts.append(f"{label} {value}")
    tail = f" ({', '.join(facts)})" if facts else ""
    lines = [
        f"Customization: as of the last barber-shop visit with the addon enabled, {when}{tail}"
    ]
    if not record.choices:
        lines.append(f"  choices: {NONE_RECORDED}")
    for choice in record.choices:
        index = "" if choice.choice_index is None else f" (index {choice.choice_index})"
        lines.append(f"  option {choice.option}: choice {_num(choice.choice)}{index}")
    lines.append(f"  {PAID_CHANGE_NOTE}")
    return lines + _events(record)


def _ids(ids: list[int], noun: str) -> str:
    if not ids:
        return NONE_RECORDED
    return f"{len(ids)} {noun} (ids in --json)"


def _mounts(mounts: Mounts) -> list[str]:
    how = (
        "the journal read through its filters" if mounts.filtered else "the journal read unfiltered"
    )
    return [f"Mounts: {_ids(mounts.collected, 'collected')} ({how})", *_events(mounts)]


def _toys(toys: Toys) -> list[str]:
    how = "read through the toy box's filter" if toys.filtered else "read unfiltered"
    switches = toys.filter or ToyFilter()
    return [
        f"Toys: {_ids(toys.collected, 'collected')} ({how}: collected shown "
        f"{_yes(switches.collected_shown)}, uncollected shown "
        f"{_yes(switches.uncollected_shown)}, unusable shown {_yes(switches.unusable_shown)})",
        *_events(toys),
    ]


def _pets(pets: Pets) -> list[str]:
    how = "read through the pet journal's filters" if pets.filtered else "read unfiltered"
    count = NONE_RECORDED if not pets.species else f"{len(pets.species)} species (ids in --json)"
    return [
        f"Pets: {count} ({how}; default filters: {_yes(pets.default_filters)})",
        *_events(pets),
    ]


def _currencies(record: Currencies) -> list[str]:
    rows = "" if record.rows is None else f", {record.rows} rows listed, headers included"
    how = (
        f"read through the currency panel: filter {_num(record.filter)}, "
        f"{record.headers_collapsed} collapsed headers{rows}"
    )
    if not record.list_:
        return [f"Currencies: {NONE_RECORDED} ({how})", *_events(record)]
    lines = [f"Currencies: {len(record.list_)} ({how})"]
    for entry in record.list_:
        if isinstance(entry, AbsentCurrency):
            lines.append(f"  currency {_num(entry.id)}: {_absent(entry)}")
            continue
        lines.append(
            f"  currency {entry.id}: quantity {_num(entry.quantity)}, cap "
            f"{_num(entry.max_quantity)}, weekly cap {_num(entry.max_weekly_quantity)}, "
            f"earned this week {_num(entry.earned_this_week)}, account-wide "
            f"{_yes(entry.account_wide)}"
        )
    return lines + _events(record)


def _professions(record: Professions) -> list[str]:
    if not record.list_:
        return [f"Professions: {NONE_RECORDED}", *_events(record)]
    lines = ["Professions (by skill line)"]
    for entry in record.list_:
        lines.append(
            f"  skill line {_num(entry.skill_line)}: rank {_num(entry.rank)} of "
            f"{_num(entry.max_rank)}, modifier {_num(entry.modifier)}"
        )
    return lines + _events(record)


def _section(label: str, record: object, present: Any) -> list[str]:
    if record is None:
        return [f"{label}: not in the file"]
    if isinstance(record, _Absent):
        return [f"{label}: {_absent(record)}"]
    lines: list[str] = present(record)
    return lines


def describe(char: CharDBV1) -> list[str]:
    """The text lines `wowlab char show` prints for a character record."""
    lines: list[str] = []
    client = char.client
    if isinstance(client, Client):
        lines.append(
            f"Client: version {client.version or 'not returned'}, build "
            f"{client.build or 'not returned'}, interface {_num(client.interface)}"
        )
    elif isinstance(client, AbsentRecord):
        lines.append(f"Client: {_absent(client)}")
    else:
        lines.append("Client: not in the file")
    if char.probe is None:
        lines.append("Probe: not in the file")
    else:
        lost = "; lost: the file loaded without a probe on this load" if char.probe.lost else ""
        lines.append(
            f"Probe: loads {char.probe.loads} (each login or /reload adds 1 to the value the "
            f"file held){lost}"
        )
    known, ignored = skip_known(char)
    lines.append(f"Switched off by the owner (skip): {', '.join(known) or NONE_RECORDED}")
    if ignored:
        lines.append(
            f"  ignored in the skip list (not a section key this reader knows): {', '.join(ignored)}"
        )
    lines.append("")
    lines += _section("Gear", char.gear, _gear)
    lines += _section("Spec", char.spec, _spec)
    talents = char.talents
    lines += _section("Class talents", talents.class_ if talents else None, _class_talents)
    lines += _section("Legacy candidates", talents.legacy if talents else None, _legacy)
    lines += _section(
        "Customization",
        char.customization,
        lambda record: _customization(record, char),
    )
    collections = char.collections
    lines += _section("Mounts", collections.mounts if collections else None, _mounts)
    lines += _section("Toys", collections.toys if collections else None, _toys)
    lines += _section("Pets", collections.pets if collections else None, _pets)
    lines += _section("Appearances", collections.appearances if collections else None, lambda r: [])
    lines += _section("Currencies", char.currencies, _currencies)
    lines += _section("Professions", char.professions, _professions)
    unknown = unknown_keys(char)
    if unknown:
        lines.append("")
        lines.append(f"Keys this reader does not know (kept in --json): {', '.join(unknown)}")
    return lines


def describe_account(account: AccountDBV1) -> list[str]:
    """The text lines for the account's `WowLabDB`."""
    extra = unknown_keys(account)
    if not extra:
        return [
            f"Account {ACCOUNT_VARIABLE}: schema {account.schema_}, nothing else "
            "(this schema keeps every section per character)"
        ]
    return [
        f"Account {ACCOUNT_VARIABLE}: schema {account.schema_}, also holds "
        f"{', '.join(extra)} (kept in --json; account data reflects whichever character "
        "logged out last)"
    ]
