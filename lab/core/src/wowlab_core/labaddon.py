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
- Reasons are the one text that is clipped instead of refused (M11-27): a
  reason longer than REASON_LIMIT characters, or holding any character
  outside printable ASCII (a long Lua error the addon stored), is escaped
  byte by byte (`\\xHH`, a backslash as `\\\\`) and truncated by
  `clip_reason` to printable ASCII of at most REASON_LIMIT characters, and
  `<field>_clipped` records the original length in bytes and what was done, so one reason no longer hides every other section. Every other
  text field keeps its shape check. Printable ASCII is still the only text
  that reaches the terminal or the JSON unescaped.
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
- Schema 2 (M11-29) is schema 1 with two keys on `customization`:
  `chr_model_id_absent` (at most one of it and `chr_model_id`) and
  `events_received` (event name to a count), on a present or an absent
  record. Schema 1 files are read exactly as before: `chr_model_id_absent`
  in one is refused (text in an unknown key), and `events_received` is kept
  as an unknown key, as it always was.
- A section the owner switched off (M11-21) is an absent record with the
  addon's reason; `WowLabCharDB.skip` is kept as a list of section keys, and
  a key this reader does not know is kept but ignored (`skip_known`).

`survey` (§14.4, M12-09) reads every character's `WowLab.lua` that `layout`
finds, in both folder shapes, into `CharacterFile` entries: the character
folder's name and shape, the file's modification time, and the record (or
only its summary) or the reason it could not be read, so one unreadable file
never hides the others; what it could not look inside above the character
folders is in `AllCharacters.not_looked_at`. `read_all` is its list of
entries alone. `choose_lab_file` is the rule for which file in a folder is
the character's (`wowlab char show` applies it to regular files only), and
`summary` is each one's row in `wowlab char list`.

`describe` and `describe_account` turn a record into the text `wowlab char
show` prints. The wording follows the domain review of the M11-03 capture:
the equipped average is "as the client reports it", never a UI figure;
Legacy candidates with nothing spent read "present, nothing spent, N points
available"; an empty list reads "none recorded"; a trait config's `type` is
the raw client enum number; professions are named by skill line.

Reads never write (L1): `read_char` and `read_account` open one file with
`snapshot.read_regular_file` (read-only, no final link followed, no blocking
on a FIFO, bounded by `luadata.MAX_FILE_BYTES`) and write nothing anywhere;
`survey` lists folders through `layout` and reads each file the same way,
within `MAX_SURVEY_BYTES` for all of them together.
Nothing here names a flavor, a product or a build (L6).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Self, TypeVar

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    SerializerFunctionWrapHandler,
    StringConstraints,
    Tag,
    ValidationError,
    ValidationInfo,
    model_serializer,
    model_validator,
)

from wowlab_core import layout, luadata, snapshot
from wowlab_core.layout import FsText

__all__ = [
    "ACCOUNT_SCHEMAS",
    "ACCOUNT_VARIABLE",
    "ADDON_NAME",
    "A_FOLDER",
    "CHAR_SCHEMAS",
    "CHAR_VARIABLE",
    "LAB_FILE_NAME",
    "MAX_IMPORT_CHOICES",
    "MAX_SURVEY_BYTES",
    "NOT_FOLLOWED",
    "NOT_REGULAR",
    "OPEN_RECORD_NOTE",
    "PAID_CHANGE_NOTE",
    "REASON_LIMIT",
    "SECTION_KEYS",
    "SWITCHED_OFF",
    "AbsentConfig",
    "AbsentCurrency",
    "AbsentCustomizationV2",
    "AbsentRecord",
    "AbsentSection",
    "AccountDB",
    "AccountDBV1",
    "AccountDBV2",
    "AllCharacters",
    "CharDB",
    "CharDBV1",
    "CharDBV2",
    "CharacterFile",
    "ClassTalents",
    "ClippedReason",
    "Customization",
    "CustomizationImport",
    "CustomizationV2",
    "LabAddonError",
    "LegacyConfig",
    "LegacyTalents",
    "NoCustomizationError",
    "NotLookedAt",
    "Place",
    "choose_lab_file",
    "clip_reason",
    "customization_import",
    "customization_loads_ago",
    "describe",
    "describe_account",
    "is_lab_file_name",
    "legacy_headline",
    "load_account",
    "load_char",
    "parse_account",
    "parse_char",
    "read_account",
    "read_all",
    "read_char",
    "skip_known",
    "summary",
    "survey",
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

OPEN_RECORD_NOTE = (
    "Recorded when the barber shop opened: a change applied during that visit may not be in "
    "it [verify]."
)

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

# Every integer the addon can write is a Lua 5.1 double holding a whole
# number, exact up to 2**53. A larger one (a hand-edited file) is refused, so
# no later step formats a number past CPython's 4300-digit limit.
INT_BOUND = 2**53
Int = Annotated[int, Field(ge=-INT_BOUND, le=INT_BOUND)]
# How many times something happened: a whole number from 0.
Count = Annotated[int, Field(ge=0, le=INT_BOUND)]

# A Lua number: every number is a double in Lua 5.1; `to_python` gives an
# int for integer spelling and a float otherwise. Never a boolean or text,
# never infinite or NaN.
Number = Int | Annotated[float, Field(allow_inf_nan=False)]

# A reason as the models hold it: printable ASCII only, at most REASON_LIMIT
# characters. What the addon wrote is clipped to this shape first (see
# `clip_reason`), so a reason is never the one string that refuses a file.
REASON_LIMIT = 1024
Reason = Annotated[str, StringConstraints(pattern=r"^[\x20-\x7e]{1,1024}$")]
# The item name inside a link: no control, format (Cf) or line/paragraph
# separator (Zl, Zp) characters, and no "]".
ItemLink = Annotated[
    str,
    StringConstraints(
        max_length=1024,
        pattern=(
            r"^\|c[0-9A-Za-z:]{1,16}\|Hitem:[0-9:\-]*"
            r"\|h\[[^\x00-\x1f\x7f-\x9f\]\p{Cf}\p{Zl}\p{Zp}]*\]\|h\|r$"
        ),
    ),
]
# The export string; "" is what an addon from before M11-22 wrote when the
# client returned an empty string, read as no export (never refused).
TalentExport = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9+/=]{0,4096}$")]
EventName = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")]
_EVENT_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}")


def _event_count_keys(value: Any) -> Any:
    """`events_received` (M11-29) is the one table read from a file whose keys
    are data. A refusal names where it happened, and pydantic's location would
    carry the key itself, so every key is checked here first: a key that is
    not an event name is refused without echoing it."""
    if isinstance(value, dict):
        for key in value:
            if not (isinstance(key, str) and _EVENT_NAME.fullmatch(key)):
                words = (
                    f"a key of {len(key)} characters that is not an event name"
                    if isinstance(key, str)
                    else _key_words(key)
                )
                raise ValueError(f"the table holds {words}")
    return value


# Event name to how many times it reached a section (M11-29).
EventCounts = Annotated[dict[EventName, Count], BeforeValidator(_event_count_keys)]
FoundBy = Annotated[str, StringConstraints(pattern=r"^(type|system):[A-Za-z0-9_.]{1,128}$")]
SkipKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.]{0,63}$")]
ClientVersion = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,6}(\.[0-9]{1,6}){0,5}$")]
ClientBuild = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,10}$")]

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
# Keys inside an unknown key's tables: a plain name, or an integer as JSON
# writes it (`-1` becomes "-1"), so the `--json` output validates again.
_NESTED_NAME = re.compile(r"[A-Za-z0-9_]{1,64}|-[0-9]{1,16}")


def _in_bound(n: int) -> bool:
    return -INT_BOUND <= n <= INT_BOUND


def _key_words(key: object) -> str:
    """A key for an error message, without echoing text from the file."""
    if isinstance(key, bool):
        return "a boolean key"
    if isinstance(key, int):
        return f"the number key {key}" if _in_bound(key) else "a number key out of range"
    if isinstance(key, float):
        return "a number key"
    if isinstance(key, str):
        return f"a key of {len(key)} characters that is not a plain name"
    return "a key that is not a name"


def _check_unknown(path: str, value: object) -> None:
    """An unknown key's value may hold numbers, booleans and tables of them."""
    if value is None or isinstance(value, bool | float):
        return
    if isinstance(value, int):
        if not _in_bound(value):
            raise ValueError(f"the unknown key {path} holds a number out of range")
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
                (isinstance(key, int) and _in_bound(key))
                or (isinstance(key, str) and _NESTED_NAME.fullmatch(key))
            ):
                raise ValueError(f"the unknown key {path} holds {_key_words(key)}")
            _check_unknown(f"{path}.{key}", item)
        return
    raise ValueError(f"the unknown key {path} holds a value of an unexpected kind")


# ─── reasons the reader had to clip (M11-27) ─────────────────────────────────

# The validation context key `load_char` and `load_account` set: the data came
# from a `WowLab.lua`, so a `<reason>_clipped` key in it is refused (only this
# reader writes one, into the model and the --json output).
_FROM_FILE = "wowlab_from_file"
CLIPPED_SUFFIX = "_clipped"
_PRINTABLE = re.compile(r"[\x20-\x7e]{1,1024}")
_NOT_PRINTABLE_BYTE = re.compile(rb"[^\x20-\x7e]")


class ClippedReason(BaseModel):
    """What this reader did to a reason that was not printable ASCII of 1 to
    REASON_LIMIT characters (a long Lua error the addon stored, say), so the
    reason reads instead of refusing the file. The reason is taken as the
    bytes the file holds (`luadata` decodes a string as UTF-8 with
    `surrogateescape`, so encoding it back the same way gives those bytes).
    `original_length`: the reason's length in bytes as the file holds it.
    `escaped`: it held a byte outside printable ASCII (0x20 to 0x7E), so every
    such byte is written as `\\xHH` and every backslash as `\\\\`; when False
    no escape was applied and a backslash is itself. `truncated`: the text
    shown stops before the end of the reason, at the last whole byte or
    escape that fits in REASON_LIMIT characters. The file is never changed."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    original_length: Annotated[int, Field(ge=1, le=INT_BOUND)]
    escaped: bool
    truncated: bool

    @model_validator(mode="after")
    def _something_done(self) -> Self:
        if not (self.escaped or self.truncated):
            raise ValueError("a clipped reason is escaped, truncated or both")
        return self


def _char_bytes(ch: str) -> bytes:
    """One character as UTF-8; a surrogate `surrogateescape` cannot map back
    to a byte (only reachable from a caller other than `luadata`) is written
    as its three-byte surrogate encoding."""
    try:
        return ch.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        return ch.encode("utf-8", "surrogatepass")


def _file_bytes(value: str) -> bytes:
    """The bytes a reason came from in the file (see `ClippedReason`)."""
    try:
        return value.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        return b"".join(_char_bytes(ch) for ch in value)


# Each byte as it is shown in an escaped reason: printable ASCII as itself,
# a backslash doubled, every other byte as \xHH.
_BYTE_ESCAPES: tuple[str, ...] = tuple(
    "\\\\" if b == 0x5C else chr(b) if 0x20 <= b <= 0x7E else f"\\x{b:02x}" for b in range(256)
)


def clip_reason(value: str) -> tuple[str, ClippedReason | None]:
    """A reason as the models hold it, and what was done to it (None when
    nothing was: printable ASCII of 1 to REASON_LIMIT characters). An empty
    reason is returned as it is; the model refuses it. The reason is encoded
    once (its size is already bounded by `luadata.MAX_FILE_BYTES`); then at
    most REASON_LIMIT bytes are looked at and at most REASON_LIMIT characters
    are built."""
    if not value or _PRINTABLE.fullmatch(value):
        return value, None
    data = _file_bytes(value)
    escaped = _NOT_PRINTABLE_BYTE.search(data) is not None
    if not escaped:  # printable ASCII, only too long
        text = data[:REASON_LIMIT].decode("ascii")
        return text, ClippedReason(original_length=len(data), escaped=False, truncated=True)
    out: list[str] = []
    size = 0
    used = 0
    for byte in data[:REASON_LIMIT]:
        piece = _BYTE_ESCAPES[byte]
        if size + len(piece) > REASON_LIMIT:
            break
        out.append(piece)
        size += len(piece)
        used += 1
    clip = ClippedReason(original_length=len(data), escaped=True, truncated=used < len(data))
    return "".join(out), clip


class _Record(BaseModel):
    """Every table the addon writes: exact types, unknown keys kept (checked).

    `reason_fields` names the fields that hold a reason; each has a
    `<name>_clipped` field, set only when `clip_reason` changed the text and
    left out of a dump when unset."""

    model_config = ConfigDict(
        strict=True,
        extra="allow",
        frozen=True,
        validate_by_name=False,
        validate_by_alias=True,
        serialize_by_alias=True,
    )

    reason_fields: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="before")
    @classmethod
    def _keys(cls, data: Any, info: ValidationInfo) -> Any:
        if not isinstance(data, dict):
            return data
        if cls.reason_fields:
            data = cls._clip(data, bool(info.context and info.context.get(_FROM_FILE)))
        # Only the name the addon writes: a Python-side name (`schema_`,
        # `list_`, `class_`) in the file is an unknown key, checked as one.
        known = {field.alias or name for name, field in cls.model_fields.items()}
        for key, value in data.items():
            if not isinstance(key, str) or not _NAME.fullmatch(key):
                raise ValueError(f"the table holds {_key_words(key)}")
            if key not in known:
                _check_unknown(key, value)
        return data

    @classmethod
    def _clip(cls, data: dict[Any, Any], from_file: bool) -> dict[Any, Any]:
        data = dict(data)
        for name in cls.reason_fields:
            flag = name + CLIPPED_SUFFIX
            if from_file and flag in data:
                raise ValueError(f"{flag} is written by this reader, never by the addon")
            value = data.get(name)
            if isinstance(value, str):
                text, clip = clip_reason(value)
                if clip is not None:
                    data[name] = text
                    data[flag] = clip
        return data

    @model_serializer(mode="wrap")
    def _dump(self, handler: SerializerFunctionWrapHandler) -> Any:
        out = handler(self)
        if isinstance(out, dict):
            for name in self.reason_fields:
                flag = name + CLIPPED_SUFFIX
                if flag in out and out[flag] is None:
                    del out[flag]
        return out


# ─── absent records ──────────────────────────────────────────────────────────


class _Absent(_Record):
    """`{ absent = "<reason>" }`: the addon could not provide this.
    `absent_clipped`: set when the reader clipped the reason (M11-27)."""

    reason_fields: ClassVar[tuple[str, ...]] = ("absent",)

    absent: Reason
    absent_clipped: ClippedReason | None = None


class AbsentRecord(_Absent):
    """An absent value below a section (the gear average, the client block, a
    tree's currencies)."""


class AbsentSection(_Absent):
    """A whole section absent with the addon's reason. `events_unregistered`
    (M11-22): the change events the client did not know, empty when all
    registered; missing on a switched-off section and in files from before
    M11-22."""

    events_unregistered: Lst[EventName] | None = None


class AbsentConfig(_Absent):
    """One trait config `C_Traits.GetConfigInfo` returned nothing for."""

    id: Int | None = None


class AbsentCurrency(_Absent):
    """One currency `C_CurrencyInfo.GetCurrencyInfo` returned nothing for."""

    id: Int | None = None


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
    interface: Int | None = None


class Probe(_Record):
    """`probe`: the addon's load counter (§13.1). `lost`: this load found a
    `WowLabCharDB` without a probe."""

    loads: Int
    lost: bool | None = None


# ─── gear ────────────────────────────────────────────────────────────────────


class GearSlot(_Record):
    """One filled slot, identified by `slot` (the list is dense, the slot
    numbers sparse)."""

    slot: Int
    link: ItemLink
    crafter_removed: bool
    item_level: Int | None = None
    item_level_api: (
        Literal["C_Item.GetCurrentItemLevel", "C_Item.GetDetailedItemLevelInfo"] | None
    ) = None


class GearAverage(_Record):
    """`GetAverageItemLevel()`'s three returns as the client reports them."""

    overall: Number | None = None
    equipped: Number | None = None
    pvp: Number | None = None


class Gear(_Section):
    first_slot: Int
    last_slot: Int
    slots: Lst[GearSlot]
    average: Annotated[Annotated[GearAverage, Tag(_P)] | Annotated[AbsentRecord, Tag(_A)], _SPLIT]


# ─── spec ────────────────────────────────────────────────────────────────────


class Spec(_Section):
    """The spec as the client exposes it, found by testing for the API."""

    api: Literal["C_SpecializationInfo", "GetSpecialization"]
    index: Int | None = None
    id: Int | None = None


# ─── talents ─────────────────────────────────────────────────────────────────


class TraitCurrency(_Record):
    """`C_Traits.GetTreeCurrencyInfo`, one currency, raw: `quantity` unspent,
    `spent`, and `max_quantity` the client's figure (for class talents it
    matched the points earned at the character's level in M11-03, not the
    tree's final cap)."""

    id: Int | None = None
    quantity: Int | None = None
    max_quantity: Int | None = None
    spent: Int | None = None


class TraitNode(_Record):
    id: Int
    ranks_purchased: Int | None = None
    active_rank: Int | None = None
    current_rank: Int | None = None
    max_ranks: Int | None = None
    is_visible: bool | None = None
    entries: Lst[Int]
    sub_tree: Int | None = None
    active_entry: Int | None = None
    active_entry_rank: Int | None = None


TreeCurrencies = Annotated[
    Annotated[Lst[TraitCurrency], Tag(_P)] | Annotated[AbsentRecord, Tag(_A)], _SPLIT
]


class TraitTree(_Record):
    id: Int
    nodes: Lst[TraitNode]
    system_id: Int | None = None
    currencies: TreeCurrencies


class TraitConfig(_Record):
    """A `C_Traits` config dump; `type` is the raw client enum number."""

    id: Int
    type: Int | None = None
    trees: Lst[TraitTree]


class LegacyConfig(TraitConfig):
    """A Legacy candidate config and how the addon found it."""

    found_by: Lst[FoundBy]


class AbsentLegacyConfig(AbsentConfig):
    found_by: Lst[FoundBy]


class ClassTalents(_Section):
    """`talents.class`: the active class config, the export string and the
    last selected saved loadout's id, each possibly missing (nil) or absent
    with a reason."""

    reason_fields: ClassVar[tuple[str, ...]] = ("export_absent", "last_selected_config_absent")

    config: Annotated[Annotated[TraitConfig, Tag(_P)] | Annotated[AbsentConfig, Tag(_A)], _SPLIT]
    export: TalentExport | None = None
    export_absent: Reason | None = None
    export_absent_clipped: ClippedReason | None = None
    last_selected_config: Int | None = None
    last_selected_config_absent: Reason | None = None
    last_selected_config_absent_clipped: ClippedReason | None = None

    @model_validator(mode="after")
    def _one_of_each(self) -> Self:
        # The addon writes at most one of each value and its `_absent` reason
        # (exactly one since M11-22; neither in captures made before it).
        # An empty `export` (before M11-22) counts as a value here.
        if self.export is not None and self.export_absent is not None:
            raise ValueError("both export and export_absent are set")
        if self.last_selected_config is not None and self.last_selected_config_absent is not None:
            raise ValueError("both last_selected_config and last_selected_config_absent are set")
        return self


class LegacyTalents(_Section):
    """`talents.legacy`: every trait config the addon found by type (any type
    but Invalid, Combat and Profession; `skipped_types` names those of them
    the client's enum has, when the type search ran) or by a client system
    id (not type-checked), less the active class config when the client gave
    its id, each with `found_by`. The file does not record which id was left
    out."""

    legacy_ui: bool
    player_level: Int | None = None
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
    option: Int
    choice_index: Int | None = None
    choice: Int | None = None


class Customization(_Section):
    """The last barber-shop record, carried from session to session."""

    as_of: Literal["last barber-shop visit with the addon enabled"]
    recorded_at: Literal["open", "applied"] | None = None
    recorded_load: Int | None = None
    carried: bool | None = None
    choices: Lst[CustomizationChoice]
    race_id: Int | None = None
    sex: Int | None = None
    chr_model_id: Int | None = None


class CustomizationV2(Customization):
    """The customization record in schema 2 (M11-29): schema 1's record plus
    two keys.

    `chr_model_id_absent`: which outcome the `C_BarberShop.GetViewingChrModel`
    call had when the section gathered (missing, raised an error, returned
    nil, returned no number). It says how the call went, not why the client
    gave no id. At most one of it and `chr_model_id`; `carry` keeps only the
    number, so a carried record may hold neither.

    `events_received`: each event the section registered in the session that
    saved the file (change events and count-only events), with how many times
    it reached the section's handler, 0 included; an event the client refused
    has no entry. Missing on a switched-off section."""

    reason_fields: ClassVar[tuple[str, ...]] = ("chr_model_id_absent",)

    chr_model_id_absent: Reason | None = None
    chr_model_id_absent_clipped: ClippedReason | None = None
    events_received: EventCounts | None = None

    @model_validator(mode="after")
    def _model_or_reason(self) -> Self:
        # The addon writes at most one of them (exactly one on a gathered
        # record; neither on a carried one).
        if self.chr_model_id is not None and self.chr_model_id_absent is not None:
            raise ValueError("both chr_model_id and chr_model_id_absent are set")
        return self


class AbsentCustomizationV2(AbsentSection):
    """The customization section absent with the addon's reason, in schema 2:
    `events_received` as on `CustomizationV2` (a never-gathered section, or a
    gather that returned an absent record)."""

    events_received: EventCounts | None = None


# ─── collections ─────────────────────────────────────────────────────────────


class Mounts(_Section):
    collected: Lst[Int]
    filtered: bool


class ToyFilter(_Record):
    collected_shown: bool | None = None
    uncollected_shown: bool | None = None
    unusable_shown: bool | None = None


class Toys(_Section):
    collected: Lst[Int]
    filtered: bool
    filter: ToyFilter


class PetSpecies(_Record):
    species: Int
    count: Int | None = None


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
    id: Int
    quantity: Int | None = None
    max_quantity: Int | None = None
    max_weekly_quantity: Int | None = None
    earned_this_week: Int | None = None
    can_earn_per_week: bool | None = None
    total_earned: Int | None = None
    use_total_earned_for_max: bool | None = None
    account_wide: bool | None = None


class Currencies(_Section):
    """The currency panel read through its filter and collapsed headers.
    Since M11-22: `rows`, the row count `GetCurrencyListSize` gave (header
    rows included; missing when the call failed), and `headers`, every
    header row seen. Both are missing in files written before M11-22."""

    list_: Lst[
        Annotated[Annotated[Currency, Tag(_P)] | Annotated[AbsentCurrency, Tag(_A)], _SPLIT]
    ] = Field(alias="list")
    filtered: bool
    headers_collapsed: Int
    filter: Int | None = None
    rows: Int | None = None
    headers: Int | None = None


class Profession(_Record):
    """One profession, identified by `skill_line`; `position` is where
    `GetProfessions` returned it, not an identity."""

    position: Int
    skill_line: Int | None = None
    rank: Int | None = None
    max_rank: Int | None = None
    modifier: Int | None = None


class Professions(_Section):
    list_: Lst[Profession] = Field(alias="list")


# ─── documents ───────────────────────────────────────────────────────────────


class _CharDB(_Record):
    """What every schema of `WowLabCharDB` shares; never validated itself. A
    schema narrows `schema_` to its number and may narrow a section to a
    subclass of schema 1's model (a narrowed field keeps its place, so the
    JSON key order is the same for every schema)."""

    schema_: int = Field(alias="schema")
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


class CharDBV1(_CharDB):
    """`WowLabCharDB`, schema 1. A section the file does not hold is `None`
    (the addon wrote no record for it, as when a session ended before the
    addon's write); a section it holds is present or absent with a reason."""

    schema_: Literal[1] = Field(alias="schema")


class CharDBV2(_CharDB):
    """`WowLabCharDB`, schema 2 (M11-29): schema 1 with two more keys on
    `customization` (`CustomizationV2`, `AbsentCustomizationV2`). Every other
    section is read exactly as in schema 1."""

    schema_: Literal[2] = Field(alias="schema")
    customization: (
        Annotated[
            Annotated[CustomizationV2, Tag(_P)] | Annotated[AbsentCustomizationV2, Tag(_A)],
            _SPLIT,
        ]
        | None
    ) = None


class AccountDBV1(_Record):
    """`WowLabDB`, schema 1: holds only `schema` (every section is per
    character in schema 1); anything else is kept as an unknown key."""

    schema_: Literal[1] = Field(alias="schema")


class AccountDBV2(_Record):
    """`WowLabDB`, schema 2: as schema 1, only the number differs (the addon
    writes one schema number into both variables)."""

    schema_: Literal[2] = Field(alias="schema")


# A character or account document of any schema this reader knows.
CharDB = CharDBV1 | CharDBV2
AccountDB = AccountDBV1 | AccountDBV2

CHAR_SCHEMAS: Mapping[int, type[CharDBV1] | type[CharDBV2]] = {1: CharDBV1, 2: CharDBV2}
ACCOUNT_SCHEMAS: Mapping[int, type[AccountDBV1] | type[AccountDBV2]] = {
    1: AccountDBV1,
    2: AccountDBV2,
}


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
    if not 0 <= schema <= 9999:  # checked before the number is ever formatted
        raise LabAddonError(
            f"{variable}.schema is out of range; this reader knows schema {known} only"
        )
    model = schemas.get(schema)
    if model is None:
        raise LabAddonError(
            f"{variable} is schema {schema}, and this reader knows schema {known} only: "
            "it was written by another version of the lab-addon; nothing was read"
        )
    try:
        return model.model_validate(value, context={_FROM_FILE: True})
    except ValidationError as exc:
        raise LabAddonError(
            f"{variable} does not fit the schema-{schema} model: {_explain(variable, exc)}"
        ) from None


def load_char(value: object) -> CharDB:
    """`WowLabCharDB` as `luadata.LuaDocument.to_python` gives it."""
    return _schema_model(CHAR_VARIABLE, value, CHAR_SCHEMAS)


def load_account(value: object) -> AccountDB:
    """`WowLabDB` as `luadata.LuaDocument.to_python` gives it."""
    return _schema_model(ACCOUNT_VARIABLE, value, ACCOUNT_SCHEMAS)


def _variable(data: bytes, variable: str) -> object:
    values = luadata.parse(data).to_python()
    if variable not in values:
        names = sorted(values)
        shown = [name if len(name) <= 64 else name[:64] + "..." for name in names[:10]]
        found = ", ".join(shown) or "nothing"
        if len(names) > 10:
            found += f" and {len(names) - 10} more"
        raise LabAddonError(
            f"the file assigns no {variable} (it assigns {len(names)} names: {found})"
        )
    value = values[variable]
    if value is None:
        raise LabAddonError(f"the file sets {variable} to nil")
    return value


def parse_char(data: bytes) -> CharDB:
    """A character's `WowLab.lua`, from its bytes."""
    return load_char(_variable(data, CHAR_VARIABLE))


def parse_account(data: bytes) -> AccountDB:
    """The account's `WowLab.lua`, from its bytes."""
    return load_account(_variable(data, ACCOUNT_VARIABLE))


def _read(path: Path) -> bytes:
    return snapshot.read_regular_file(Path(path), limit=luadata.MAX_FILE_BYTES)


def read_char(path: Path) -> CharDB:
    """Read a character's `WowLab.lua` (read-only; writes nothing, L1)."""
    return parse_char(_read(path))


def read_account(path: Path) -> AccountDB:
    """Read the account's `WowLab.lua` (read-only; writes nothing, L1)."""
    return parse_account(_read(path))


# ─── every character (§14.4, M12-09) ─────────────────────────────────────────


LAB_FILE_NAME = f"{ADDON_NAME}.lua"  # a character's file in its SavedVariables/

_WTF = "WTF"
_ACCOUNT = "Account"
_SAVED_VARIABLES = "SavedVariables"
# File-map entries for what a name directly in an account folder can be when
# it is not a file the map names: a realm-name folder or a digits folder.
_REALM_ENTRIES = frozenset({"realm-folder", "numeric-folder"})
# The most `survey` reads in one run, all files together (M12-09). A real
# `WowLab.lua` is tens of KB to a few MB, so a real install stays far below
# it; it bounds the time and memory a folder of large or hard-linked copies
# can cost. Each file is also bounded on its own (`luadata.MAX_FILE_BYTES`).
MAX_SURVEY_BYTES = 256 * 1024 * 1024

NOT_FOLLOWED = "a link, not followed, as wowlab never follows links"
NOT_REGULAR = "not a regular file (a FIFO, socket or device), so it was not opened"
A_FOLDER = "a folder, not a file"

# What `CharacterFile.file` names.
Place = Literal["file", "saved_variables_folder", "character_folder"]


class CharacterFile(BaseModel):
    """One character folder's `WowLab.lua` as `survey` found it: its
    `summary` (and, unless the caller asked not to keep it, the parsed
    `record`), or the reason it could not be read (`error`), never both.

    `label` is the character folder's name as it is on disk (§14.4), and
    `realm_folder` the folder above it: a realm name (`shape` `realm_name`)
    or, on Forever, a digits folder (`numeric_folder`), which is not a realm
    name. A row is a folder, not proof of a character: a renamed or
    transferred character (and a deleted one **[verify]**) leaves its folder
    and last save behind (GLOSSARY, Identity). `wowlab char show --character`
    takes `<realm_folder>/<label>` for a folder the layout lists; a linked
    character folder (`place` `character_folder`) is not one of them.

    `file` is the file read, relative to the flavor folder and `/`-separated
    (`place` `file`), or, when wowlab could not look inside the character
    folder or its SavedVariables/, that folder (`place` says which).
    `mtime_ns` is the file's modification time as the walk saw it: the
    client's last save, unless something wrote the file since (a wowlab snap
    restore, undo or sv merge, or a copy); the addon records no time of its
    own. It is None when no regular file was looked at. An `error` never
    holds an absolute path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: FsText
    account: FsText
    realm_folder: FsText
    shape: Literal["realm_name", "numeric_folder"]
    file: FsText
    place: Place = "file"
    mtime_ns: int | None
    summary: str | None = None
    record: CharDBV1 | CharDBV2 | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _read_or_error(self) -> Self:
        if (self.summary is None) == (self.error is None):
            raise ValueError(
                "a CharacterFile holds either a summary (the file was read) or an error, "
                "not both or neither"
            )
        if self.record is not None and self.error is not None:
            raise ValueError("a CharacterFile with an error holds no record")
        return self

    @property
    def character(self) -> str:
        """`<realm or digits folder>/<character folder>`."""
        return f"{self.realm_folder}/{self.label}"


class NotLookedAt(BaseModel):
    """A place where character folders can be and that wowlab could not look
    inside: a folder it could not list, a link it does not follow, or the
    walk's bound (`path` `WTF`). Not tied to one character. `path` is
    relative to the flavor folder; the flavor folder itself is named by its
    own name. `account` is the account folder it is in, None above them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: FsText
    reason: str
    account: FsText | None = None


class AllCharacters(BaseModel):
    """What `survey` found: one entry per character folder, and every place
    it could not look inside that no character folder accounts for."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    characters: list[CharacterFile]
    not_looked_at: list[NotLookedAt]


def is_lab_file_name(name: str) -> bool:
    """`WowLab.lua` in any spelling of its case (never `WowLab.lua.bak`)."""
    return name.casefold() == LAB_FILE_NAME.casefold()


def _name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def choose_lab_file(paths: list[str]) -> str | None:
    """The one character `WowLab.lua` among `paths` (`/`-separated, all in
    one character folder): the file named exactly `WowLab.lua`, else the one
    file whose name differs from it only in case; None when there is none.
    Names are compared whole, extension included. Two with the exact name,
    or none with it and several case variants, raise `LabAddonError`: which
    one the client reads is not known, so none is read. `survey`, `wowlab
    char show` and `looks import-char` all choose with this rule (M12-09),
    but the two commands give it regular files only, while `survey` also
    gives it a link, FIFO or folder of that name. So for such an entry they
    can still answer differently: the client opens the exact name and
    follows links, and on a case-sensitive volume (under Wine, say) `char
    show` can read a case variant the client does not."""
    named = [p for p in paths if is_lab_file_name(_name(p))]
    exact = [p for p in named if _name(p) == LAB_FILE_NAME]
    match = sorted(exact or named)
    if len(match) > 1:
        shown = ", ".join("/".join(p.split("/")[-2:]) for p in match)
        spelled = "" if exact else f", none spelled exactly {LAB_FILE_NAME}"
        raise LabAddonError(
            f"the character folder holds {len(match)} files named like {LAB_FILE_NAME} "
            f"({shown}{spelled}); which one the client reads is not known, so none was read"
        )
    return match[0] if match else None


def _os_error(exc: OSError, path: Path) -> str:
    """Why the file could not be opened or read, without its absolute path
    (the entry names the file): the system's own words when it gave some,
    else `snapshot`'s refusal (not a regular file, over the size bound, ...)
    with the path it names worded as "the file"."""
    if exc.strerror:
        return exc.strerror
    return str(exc).replace(str(path), "the file")


class _Reader:
    """Reads the files one `survey` run chooses, within `budget` bytes for
    all of them together, and each (device, inode) once."""

    def __init__(self, flavor_path: Path, budget: int, keep_records: bool) -> None:
        self.flavor_path = flavor_path
        self.budget = budget
        self.left = budget
        self.keep_records = keep_records
        self.full = False
        self.seen: dict[tuple[int, int], str] = {}  # (device, inode) -> path read first

    def read(self, rel: str) -> tuple[CharDB | None, str | None, str | None]:
        """(record, summary, error) for the file `rel`: a file the reader
        cannot read is an error, never an exception (any other exception is
        a bug). The record is None unless `keep_records`."""
        path = self.flavor_path / rel
        try:
            st = os.lstat(path)
        except OSError as exc:
            return None, None, _os_error(exc, path)
        key = (st.st_dev, st.st_ino)
        if st.st_ino and key in self.seen:
            return None, None, f"the same file as {self.seen[key]} (a hard link), read once above"
        if self.full or st.st_size > self.left:
            self.full = True  # every file after this one gets the same answer
            bound = (
                f"the listing's total size bound was reached ({self.budget:,} bytes for all "
                "files together)"
            )
            return None, None, bound
        if st.st_ino:
            self.seen[key] = rel
        try:
            data = snapshot.read_regular_file(path, limit=luadata.MAX_FILE_BYTES, expect=st)
        except OSError as exc:
            return None, None, _os_error(exc, path)
        self.left -= len(data)
        try:
            record = parse_char(data)
        except LabAddonError as exc:
            return None, None, str(exc)
        except luadata.LuaLimitError as exc:
            return None, None, f"beyond what the SavedVariables parser will hold ({exc.message})"
        except luadata.LuaDataError as exc:
            return None, None, f"not SavedVariables data the parser accepts: {exc}"
        return (record if self.keep_records else None), summary(record), None


# Where a place the walk could not look inside sits, for `read_all`.
_Place = Literal["above", "character", "saved_variables", "file"]


def _place(path: str, entry_id: str | None, *, is_link: bool) -> tuple[_Place, list[str]] | None:
    """Whether `path` (a link, a read error, a FIFO or a folder the walk met)
    can hide a character's `WowLab.lua`, and where: above every character
    folder (`WTF/`, `WTF/Account/`, an account folder, a realm or digits
    folder), at a character folder, at its SavedVariables/, or at the file.
    None for anything else (another addon's file, a character's other files,
    the account's own SavedVariables/ and everything in it). The flavor
    folder itself (`""`) is the caller's to name. A link directly in an account folder
    counts only where the file map would take it for a realm or digits
    folder, so a linked `config-cache.wtf` hides nothing."""
    parts = path.split("/")
    if parts[0].casefold() != _WTF.casefold():
        return None
    if len(parts) == 1:
        return "above", parts
    if parts[1].casefold() != _ACCOUNT.casefold():
        return None
    below = parts[2:]  # account, realm, character, SavedVariables, file
    if len(below) <= 1:
        return "above", parts
    if below[1].casefold() == _SAVED_VARIABLES.casefold():
        return None  # the account's own SavedVariables/ and anything in it
    if len(below) == 2:
        if is_link and entry_id not in _REALM_ENTRIES:
            return None
        return "above", parts
    if below[2].casefold() == _SAVED_VARIABLES.casefold():
        return None  # never a character folder (layout's rule)
    if len(below) == 3:
        return "character", parts
    if below[3].casefold() != _SAVED_VARIABLES.casefold():
        return None
    if len(below) == 4:
        return "saved_variables", parts
    if len(below) == 5 and is_lab_file_name(below[4]):
        return "file", parts
    return None


class _Folder:
    """What `survey` gathered for one character folder."""

    def __init__(self, account: str, realm: layout.Realm, label: str) -> None:
        self.account = account
        self.realm = realm
        self.label = label
        # (path, place, reason): the character folder or its SavedVariables/
        self.blocked: list[tuple[str, Place, str]] = []
        self.files: dict[str, layout.SavedVariablesFile] = {}  # regular files, by path
        self.odd: dict[str, str] = {}  # other entries named like the file: path -> reason

    def key(self) -> tuple[str, ...]:
        return _order_key(self.account, self.realm.folder, self.label)

    def entry(
        self,
        file: str,
        mtime_ns: int | None,
        *,
        place: Place = "file",
        record: CharDB | None = None,
        summary: str | None = None,
        error: str | None = None,
    ) -> CharacterFile:
        return CharacterFile(
            label=self.label,
            account=self.account,
            realm_folder=self.realm.folder,
            shape="numeric_folder" if self.realm.kind == "numeric" else "realm_name",
            file=file,
            place=place,
            mtime_ns=mtime_ns,
            summary=summary,
            record=record,
            error=error,
        )

    def read(self, reader: _Reader) -> CharacterFile | None:
        """The folder's entry, or None when it holds no `WowLab.lua`."""
        if self.blocked:
            path, place, reason = min(self.blocked)
            return self.entry(path, None, place=place, error=reason)
        try:
            chosen = choose_lab_file([*self.files, *self.odd])
        except LabAddonError as exc:
            first = min([*self.files, *self.odd])
            found = self.files.get(first)
            return self.entry(first, found.mtime_ns if found else None, error=str(exc))
        if chosen is None:
            return None
        if chosen in self.odd:
            return self.entry(chosen, None, error=self.odd[chosen])
        found = self.files[chosen]
        record, words, error = reader.read(chosen)
        return self.entry(chosen, found.mtime_ns, record=record, summary=words, error=error)


def _order_key(account: str, realm_folder: str, label: str) -> tuple[str, ...]:
    return (
        account.casefold(),
        account,
        realm_folder.casefold(),
        realm_folder,
        label.casefold(),
        label,
    )


def read_all(lay: layout.Layout, *, account: str | None = None) -> list[CharacterFile]:
    """`survey(lay, account=account).characters`: one entry per character
    folder with a `WowLab.lua`, readable or not. It leaves out the places
    above the character folders that could not be looked inside; anything
    that shows the owner a list of characters uses `survey`, which has them."""
    return survey(lay, account=account).characters


def survey(
    lay: layout.Layout,
    *,
    account: str | None = None,
    keep_records: bool = True,
    budget: int = MAX_SURVEY_BYTES,
) -> AllCharacters:
    """Every character `WowLab.lua` in the flavor (§14.4, M12-09), from one
    walk of `WTF/` (`Layout.wtf_walk`).

    Covers every character folder under each account folder (`account`: that
    folder only, as the layout spells it), of both shapes: `<Realm>/<Name>/`
    and Forever's `<digits>/<First>-<Second>/`. A folder whose SavedVariables/
    holds a `WowLab.lua` (chosen by `choose_lab_file`) gives one entry; a
    folder without one gives none, so Forever's retail-style `<Realm>/<First>/`
    twin, which holds only `AddOns.txt`, is not listed. `.lua.bak` siblings
    are never read.

    Whatever wowlab could not look at is named, never passed over: a file it
    cannot read (not the addon's variable, a schema this reader does not
    know, a value that does not fit the model, not data the parser accepts,
    an error opening it), a `WowLab.lua` that is a link, a FIFO or other
    non-regular entry, or a folder, and a character folder or SavedVariables/
    that is a link or could not be listed, each give that character an entry
    with the reason. A place above the character folders that could not be
    looked inside (the flavor folder itself, and a link or unlistable folder
    at `WTF/`, `WTF/Account/`, an account, realm or digits folder) and a
    walk that hit its bound are in `not_looked_at`. None of these stops the
    others. Links are never followed and non-regular entries never opened.
    Any other exception is a bug and is raised.

    The run reads at most `budget` bytes in all (`MAX_SURVEY_BYTES`); once a
    file would pass it, that file and every one after it in the order below
    get an error instead of being read. A file already read in this run
    (the same device and inode: a hard link) is not read again; its entry
    says so. With `keep_records` False the entries hold each file's
    `summary` but not its parsed record, so memory stays bounded by the
    largest single file.

    The order is by account, then realm folder, then character folder, each
    compared with case folded and then as spelled: stable across runs and
    independent of modification times. Read only (L1): each file is read as
    `read_char` reads it, and nothing is written anywhere."""
    walk = lay.wtf_walk()
    folders: dict[tuple[str, str, str], _Folder] = {}
    realms: dict[tuple[str, str], layout.Realm] = {}
    for acct in walk.accounts:
        for realm in acct.realms:
            realms[(acct.folder, realm.folder)] = realm
            for character in realm.characters:
                key = (acct.folder, realm.folder, character.folder)
                folders[key] = _Folder(acct.folder, realm, character.folder)
    not_looked_at: list[NotLookedAt] = []

    def unseen(path: str, reason: str, parts: list[str]) -> None:
        in_account = parts[2] if len(parts) > 2 else None
        not_looked_at.append(NotLookedAt(path=path, reason=reason, account=in_account))

    def note(path: str, reason: str, entry_id: str | None = None, is_link: bool = False) -> None:
        found = _place(path, entry_id, is_link=is_link)
        if found is None:
            return
        place, parts = found
        if account is not None and len(parts) > 2 and parts[2] != account:
            return
        if place == "above":
            unseen(path, reason, parts)
            return
        key = (parts[2], parts[3], parts[4])
        folder = folders.get(key)
        if folder is None:  # a linked character folder: not in the typed lists
            realm = realms.get((parts[2], parts[3]))
            if realm is None:
                unseen(path, reason, parts)
                return
            folder = folders[key] = _Folder(parts[2], realm, parts[4])
        if place == "character":
            folder.blocked.append((path, "character_folder", reason))
        elif place == "saved_variables":
            folder.blocked.append((path, "saved_variables_folder", reason))
        else:
            folder.odd[path] = reason

    for link in walk.symlinks:
        note(link.path, NOT_FOLLOWED, link.entry_id, is_link=True)
    for error in walk.errors:
        if error.path == "":  # the flavor folder itself could not be listed
            not_looked_at.append(NotLookedAt(path=lay.flavor_folder, reason=error.error))
        else:
            note(error.path, error.error)
    for path in walk.not_regular + walk.folders:
        # Only an entry with the file's own name can stand in for it; a FIFO
        # or folder anywhere else hides no character.
        found = _place(path, None, is_link=False)
        if found is not None and found[0] == "file":
            note(path, NOT_REGULAR if path in walk.not_regular else A_FOLDER)
    for f in walk.saved_variables:
        if f.scope != "character" or f.backup or not is_lab_file_name(_name(f.path)):
            continue
        folder = folders.get((f.account, f.realm_folder or "", f.character_folder or ""))
        if folder is not None:
            folder.files[f.path] = f
    if walk.truncated:
        limits = lay.limits
        not_looked_at.append(
            NotLookedAt(
                path=_WTF,
                reason=(
                    f"the walk stopped at its bound ({limits.max_entries} entries, "
                    f"{limits.max_depth} folders deep), so what lies past it was not looked at"
                ),
            )
        )
    reader = _Reader(lay.flavor_path, budget, keep_records)
    entries = []
    for folder in sorted(folders.values(), key=_Folder.key):
        if account is not None and folder.account != account:
            continue
        entry = folder.read(reader)
        if entry is not None:
            entries.append(entry)
    return AllCharacters(
        characters=entries,
        not_looked_at=sorted(not_looked_at, key=lambda n: n.path),
    )


# ─── derived facts ───────────────────────────────────────────────────────────


def skip_known(char: CharDB) -> tuple[list[str], list[str]]:
    """The skip list split into section keys this reader knows and the rest
    (kept in the record, ignored here)."""
    known: list[str] = []
    ignored: list[str] = []
    for key in char.skip or []:
        (known if key in SECTION_KEYS else ignored).append(key)
    return known, ignored


def customization_loads_ago(char: CharDB) -> int | None:
    """How many logins or reloads ago the customization record was made:
    `probe.loads` minus `recorded_load`; None when either is missing, when
    the probe was lost on this load (the count restarted at 1), or when the
    difference is negative."""
    record = char.customization
    if not isinstance(record, Customization) or record.recorded_load is None:
        return None
    if char.probe is None or char.probe.lost:
        return None
    ago = char.probe.loads - record.recorded_load
    return ago if ago >= 0 else None


# More choices than any model's options (about 20 on 70009's playable
# models); a record over it is refused rather than saved (M11-23 security review).
MAX_IMPORT_CHOICES = 256


class NoCustomizationError(LabAddonError):
    """The file holds no customization record a look can be made from.
    `reason` is the addon's own absent reason when it wrote one."""

    def __init__(self, message: str, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason


class CustomizationImport(BaseModel):
    """A character's customization record, as `looks import-char` turns it
    into a look (M11-23). `body_type` is the record's `sex` (the barber
    shop's `Enum.UnitSex`, 0 or 1), read as `ChrRaceXChrModel.Sex`: the
    M11-23 capture's choices all sit on the model that row gives for its race
    and sex 0 **[verify for 1]**. `without_choice` lists options the record
    holds with no choice id; they are left out of `choices`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    race_id: int
    body_type: int
    chr_model_id: int | None
    choices: dict[int, int]
    without_choice: list[int]
    recorded_at: Literal["open", "applied"] | None
    as_of: str
    carried: bool
    loads_ago: int | None
    client_build: str | None  # "<version>.<build>" from the client block, when both are there


def customization_import(char: CharDB) -> CustomizationImport:
    """The customization record as look material, or `NoCustomizationError`
    (with the addon's reason when the section is absent with one). Pure: the
    record is already read."""
    record = char.customization
    if record is None:
        raise NoCustomizationError(f"customization: {NOT_IN_FILE}")
    if not isinstance(record, Customization):
        clipped = (
            " (the addon's reason was clipped; see char show)"
            if record.absent_clipped is not None
            else ""
        )
        raise NoCustomizationError(
            f"customization is absent, with the addon's reason: {record.absent}{clipped}",
            record.absent,
        )
    if record.race_id is None:
        raise NoCustomizationError("the customization record names no race (race_id)")
    if record.sex is None:
        raise NoCustomizationError("the customization record names no body type (sex)")
    if record.sex not in (0, 1):
        raise NoCustomizationError(
            f"the customization record's sex is {record.sex}, not 0 or 1 as the barber shop "
            "writes it; no body type is taken from it"
        )
    if len(record.choices) > MAX_IMPORT_CHOICES:
        raise NoCustomizationError(
            f"the customization record lists {len(record.choices)} choices, more than the "
            f"{MAX_IMPORT_CHOICES} an import takes (a barber shop lists about 20 options)"
        )
    choices: dict[int, int] = {}
    without: list[int] = []
    for entry in record.choices:
        if entry.option in choices or entry.option in without:
            raise NoCustomizationError(
                f"the customization record lists option {entry.option} twice"
            )
        if entry.choice is None:
            without.append(entry.option)
        else:
            choices[entry.option] = entry.choice
    client = char.client
    build = (
        f"{client.version}.{client.build}"
        if isinstance(client, Client) and client.version is not None and client.build is not None
        else None
    )
    return CustomizationImport(
        race_id=record.race_id,
        body_type=record.sex,
        chr_model_id=record.chr_model_id,
        choices=choices,
        without_choice=without,
        recorded_at=record.recorded_at,
        as_of=record.as_of,
        carried=bool(record.carried),
        loads_ago=customization_loads_ago(char),
        client_build=build,
    )


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
NOT_IN_FILE = (
    "not in the file (the addon's logout write did not reach it in the session that saved "
    "this file)"
)
MAX_QUANTITY_NOTE = (
    "  max_quantity is the client's figure; for class talents it has matched the points "
    "earned at the character's level (M11-03), not the tree's final cap."
)
ALL_EVENTS_REGISTERED = "all its change events registered"
EVENTS_RECEIVED = (
    "times each registered event reached the section in the session that saved this file"
)
MODEL_OUTCOME_NOTE = (
    "how the call went when the section gathered, not why no model was named; "
    "the body type comes from sex"
)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _yes(value: bool | None) -> str:
    if value is None:
        return "not reported"
    return "yes" if value else "no"


def _num(value: Number | None) -> str:
    return "not returned" if value is None else str(value)


def _clip_words(clip: ClippedReason | None) -> str:
    """What the reader did to a reason, after it (printable ASCII only)."""
    if clip is None:
        return ""
    size = f"the reason is {_count(clip.original_length, 'byte')} in the file"
    if not clip.escaped:
        return f" [{size}; shown cut to its first {REASON_LIMIT} characters; the file is unchanged]"
    cut = f", and cut to at most {REASON_LIMIT} characters" if clip.truncated else ""
    return (
        f" [{size}; shown with each byte outside printable ASCII written as \\xHH and each "
        f"backslash as \\\\{cut}; the file is unchanged]"
    )


def _reason(text: str, clip: ClippedReason | None) -> str:
    return f"absent ({text}){_clip_words(clip)}"


def _received(counts: dict[str, int]) -> str:
    """`events_received` (schema 2) as `EVENT n` pairs, by event name."""
    if not counts:
        return NONE_RECORDED
    return ", ".join(f"{name} {counts[name]}" for name in sorted(counts))


def _absent(record: _Absent, *, events_shown: bool = True) -> str:
    text = _reason(record.absent, record.absent_clipped)
    # Declared fields only (M11-29 security review): a model keeps an unknown
    # key and would hand it back as an attribute, so `getattr` would read a
    # kept `events_unregistered` or `events_received` of any shape, from any
    # absent record of any schema. `events_unregistered` is declared on
    # AbsentSection; `events_received` only on schema 2's
    # AbsentCustomizationV2.
    events = record.events_unregistered if isinstance(record, AbsentSection) else None
    if events is not None and events_shown:
        if events:
            text += f"; events the client did not know: {', '.join(events)}"
        else:
            text += f"; {ALL_EVENTS_REGISTERED}"
    received = record.events_received if isinstance(record, AbsentCustomizationV2) else None
    if received is not None and events_shown:
        text += f"; {EVENTS_RECEIVED}: {_received(received)}"
    return text


def _events(section: _Section) -> list[str]:
    """Since M11-22 every section that registered events carries the list,
    empty when all registered; a file from before M11-22 has no key."""
    events = section.events_unregistered
    if events is None:
        return []
    if not events:
        return [f"  {ALL_EVENTS_REGISTERED}"]
    return [
        "  events the client did not know (so the section was not refreshed on that change): "
        + ", ".join(events)
    ]


_LINK = re.compile(r"\|Hitem:([0-9]*)[^|]*\|h\[([^\]]*)\]")


def _item(link: str) -> str:
    match = _LINK.search(link)
    if match is None:
        return "(full link in --json)"
    item = f"item {match.group(1)}" if match.group(1) else "no item id in the link"
    return f"{match.group(2)} ({item}; full link in --json)"


def _gear(gear: Gear) -> list[str]:
    lines = [
        f"Gear (slots {gear.first_slot} to {gear.last_slot} as the client numbers them, "
        f"{len(gear.slots)} filled)"
    ]
    if not gear.slots:
        lines.append(f"  slots: {NONE_RECORDED}")
    for slot in sorted(gear.slots, key=lambda s: s.slot):
        if slot.item_level is None:
            level = "item level not returned"
        else:
            api = "" if slot.item_level_api is None else f" ({slot.item_level_api})"
            level = f"item level {slot.item_level}{api}"
        crafter = "; crafter GUID removed from the link" if slot.crafter_removed else ""
        lines.append(f"  slot {slot.slot}: {_item(slot.link)}, {level}{crafter}")
    average = gear.average
    if isinstance(average, GearAverage):
        lines.append(
            f"  GetAverageItemLevel: equipped {_num(average.equipped)}, overall "
            f"{_num(average.overall)} (best owned, bags included; Retail meaning, from memory), "
            f"pvp {_num(average.pvp)}, as the client returns them; not the mean of the item "
            "levels above, and how the client computes them is not known"
        )
    else:
        lines.append(f"  GetAverageItemLevel: {_absent(average)}")
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
        f"max_quantity {_num(c.max_quantity)}"
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
        lines.append(MAX_QUANTITY_NOTE)
    if talents.export:
        lines.append(f"  export string: {talents.export}")
    elif talents.export == "":
        lines.append("  export string: the client returned an empty string")
    elif talents.export_absent is not None:
        lines.append(
            f"  export string: {_reason(talents.export_absent, talents.export_absent_clipped)}"
        )
    else:
        lines.append("  export string: not returned by the client")
    if talents.last_selected_config is not None:
        lines.append(f"  last selected saved loadout: {talents.last_selected_config} (raw)")
    elif talents.last_selected_config_absent is not None:
        reason = _reason(
            talents.last_selected_config_absent, talents.last_selected_config_absent_clipped
        )
        lines.append(f"  last selected saved loadout: {reason}")
    else:
        lines.append("  last selected saved loadout: not returned by the client")
    return lines + _events(talents)


def _legacy_pool(config: LegacyConfig) -> tuple[list[TraitCurrency], list[int | None]]:
    """The trait currencies of one Legacy candidate, each counted once:
    `C_Traits.GetTreeCurrencyInfo` reports one pool under every tree that
    spends it. Returns the pooled rows, and the ids whose trees report
    different values (those are not added up)."""
    pooled: list[TraitCurrency] = []
    disagree: list[int | None] = []
    by_id: dict[int | None, list[TraitCurrency]] = {}
    for tree in config.trees:
        if isinstance(tree.currencies, AbsentRecord):
            continue
        for currency in tree.currencies:
            by_id.setdefault(currency.id, []).append(currency)
    for currency_id, rows in by_id.items():
        if len({(r.quantity, r.spent, r.max_quantity) for r in rows}) == 1:
            pooled.append(rows[0])
        else:
            disagree.append(currency_id)
    return pooled, disagree


def _legacy_points(config: LegacyConfig) -> str:
    """Ranks, points spent and points available for one candidate config."""
    ranks = sum(n.active_rank or 0 for t in config.trees for n in t.nodes)
    pooled, disagree = _legacy_pool(config)
    spent = sum(cur.spent or 0 for cur in pooled)
    if ranks == 0 and spent == 0:
        spent_words = "nothing spent"
    else:
        spent_words = f"{_count(ranks, 'rank')} active, {_count(spent, 'point')} spent"
    if disagree:
        return (
            f"{spent_words}, points available not added up: currency {_num(disagree[0])} "
            "reports different values on different trees (see below)"
        )
    quantities = [cur.quantity for cur in pooled if cur.quantity is not None]
    if not quantities:
        return f"{spent_words}, points available not reported"
    return f"{spent_words}, {_count(sum(quantities), 'point')} available"


def legacy_headline(legacy: LegacyTalents) -> str:
    """One line for the Legacy candidates, never "empty" or "locked": the
    addon lists candidates by elimination, and below the unlock level the
    client still returns the trees, with nothing spent and a max_quantity of
    0. Each trait currency counts once per config (see `_legacy_pool`), and
    two candidate configs are never added together (M11-27): the addon does
    not say which one the Legacy panel uses, so each gets its own figures."""
    if not legacy.configs:
        return f"Legacy candidates: {NONE_RECORDED}"
    if not any(isinstance(c, LegacyConfig) for c in legacy.configs):
        return "Legacy candidates: present, every config absent with a reason"
    single = _single_config(legacy)
    if single is not None:
        return f"Legacy candidates: present, {_legacy_points(single)}"
    each = "; ".join(
        f"config {c.id}: {_legacy_points(c)}"
        if isinstance(c, LegacyConfig)
        else f"config {_num(c.id)}: absent with a reason (see below)"
        for c in legacy.configs
    )
    level = "" if legacy.player_level is None else f" (level {legacy.player_level})"
    return (
        f"Legacy candidates: present, {len(legacy.configs)} configs{level}; the file does not "
        "say which one is the Legacy system, so each has its own figures (not added together): "
        f"{each}"
    )


def _single_config(legacy: LegacyTalents) -> LegacyConfig | None:
    """The one candidate, when the file lists exactly one and it has figures."""
    if len(legacy.configs) == 1 and isinstance(legacy.configs[0], LegacyConfig):
        return legacy.configs[0]
    return None


def _legacy(legacy: LegacyTalents) -> list[str]:
    opener = f"panel opener ToggleLegacySystemUI present: {_yes(legacy.legacy_ui)}"
    # The headline names the level itself when it gives each config's figures.
    each = _single_config(legacy) is None and any(
        isinstance(c, LegacyConfig) for c in legacy.configs
    )
    level = "" if each or legacy.player_level is None else f" (level {legacy.player_level})"
    lines = [f"{legacy_headline(legacy)}{level}"]
    if len(legacy.configs) > 1:
        # M11-32: with several configs listed, nothing picks one out, even
        # when every one of them is absent with a reason. A config found by a
        # client system id is not type-checked, and the active class config
        # is left out only when the client gave its id; `talents.legacy`
        # does not record which id that was, so the line cannot say more.
        lines.append(
            "  the addon lists every trait config it found by type (except the types below) or "
            "by a client system id, less the active class talents when the client gave their "
            f"id; which of these is the Legacy system is not recorded; {opener}"
        )
    elif legacy.configs:
        lines.append(f"  which config is the Legacy system is inferred by elimination; {opener}")
    else:
        lines.append(f"  no candidate config listed; {opener}")
    skipped = ", ".join(legacy.skipped_types) or NONE_RECORDED
    lines.append(f"  config types not searched: {skipped}")
    for config in legacy.configs:
        found = ", ".join(config.found_by) or NONE_RECORDED
        if isinstance(config, AbsentLegacyConfig):
            lines.append(f"  config {_num(config.id)} {_absent(config)}; found by {found}")
            continue
        lines.append(f"  {_config_head(config)}, found by {found}")
        if not config.trees:
            lines.append(f"    trees: {NONE_RECORDED}")
        lines.extend(f"    {_tree(tree)}" for tree in config.trees)
    return lines + _events(legacy)


def _loads_ago_words(ago: int | None) -> str:
    if ago is None:
        return "an unknown number of logins or reloads ago"
    if ago == 0:
        return "in the session that saved this file"
    if ago == 1:
        return "1 login or reload ago"
    return f"{ago} logins or reloads ago"


def _customization(record: Customization, char: CharDB) -> list[str]:
    when = _loads_ago_words(customization_loads_ago(char))
    facts = []
    if record.recorded_at is not None:
        facts.append(f"recorded at {record.recorded_at}")
    if record.carried:
        facts.append("carried from an earlier session")
    if record.chr_model_id is not None:
        facts.append(f"model {record.chr_model_id}")
    if record.race_id is not None:
        facts.append(f"race {record.race_id}")
    if record.sex is not None:
        facts.append(f"sex {record.sex} (the client's raw value)")
    tail = f" ({', '.join(facts)})" if facts else ""
    lines = [
        f"Customization: as of the last barber-shop visit with the addon enabled, {when}{tail}"
    ]
    v2 = record if isinstance(record, CustomizationV2) else None
    if v2 is not None and v2.chr_model_id is None and v2.chr_model_id_absent is not None:
        reason = _reason(v2.chr_model_id_absent, v2.chr_model_id_absent_clipped)
        lines.append(f"  model: {reason}, {MODEL_OUTCOME_NOTE}")
    if not record.choices:
        lines.append(f"  choices: {NONE_RECORDED}")
    for choice in record.choices:
        index = "" if choice.choice_index is None else f" (index {choice.choice_index})"
        lines.append(f"  option {choice.option}: choice {_num(choice.choice)}{index}")
    if record.recorded_at == "open":
        lines.append(f"  {OPEN_RECORD_NOTE}")
    lines.append(f"  {PAID_CHANGE_NOTE}")
    lines += _events(record)
    if v2 is not None and v2.events_received is not None:
        lines.append(f"  {EVENTS_RECEIVED}: {_received(v2.events_received)}")
    return lines


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
    switches = toys.filter
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


def _panel_words(record: Currencies) -> str:
    if record.filter is None:
        filter_words = "filter value not returned"
    else:
        filter_words = f"filter value {record.filter} (the client's raw value; meaning not known)"
    how = (
        f"read through the currency panel: {filter_words}, "
        f"{_count(record.headers_collapsed, 'collapsed header')}"
    )
    if record.rows is None:
        how += (
            "; the file does not say how many rows the panel listed, so an empty list may also "
            "mean rows the addon could not read"
        )
    elif record.headers is None:
        how += f"; {_count(record.rows, 'row')} listed, header rows included"
    else:
        unread = record.rows - record.headers - len(record.list_)
        how += (
            f"; {_count(record.rows, 'row')} listed, {_count(record.headers, 'header row')}, "
            f"{_count(unread, 'row')} with no id the addon could read"
        )
    return how


def _currencies(record: Currencies) -> list[str]:
    how = _panel_words(record)
    if not record.list_:
        return [f"Currencies: {NONE_RECORDED} ({how})", *_events(record)]
    lines = [f"Currencies: {len(record.list_)} ({how})"]
    for entry in record.list_:
        if isinstance(entry, AbsentCurrency):
            lines.append(f"  currency {_num(entry.id)}: {_absent(entry)}")
            continue
        lines.append(
            f"  currency {entry.id}: quantity {_num(entry.quantity)}, max_quantity "
            f"{_num(entry.max_quantity)}, weekly max {_num(entry.max_weekly_quantity)}, "
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
            f"  skill line {_num(entry.skill_line)}: skill {_num(entry.rank)} of "
            f"{_num(entry.max_rank)} (the current cap), modifier {_num(entry.modifier)}"
        )
    return lines + _events(record)


APPEARANCES_EVENTS_NOTE = (
    "  events_unregistered is in the file, though the addon registers no events for this "
    "section (kept in --json)"
)


def _appearances(record: AbsentSection | None) -> list[str]:
    """`collections.appearances` registers no events (M11-20), so its line
    says nothing about events; a list in the file gets a note and stays in
    --json."""
    if record is None:
        return [f"Appearances: {NOT_IN_FILE}"]
    lines = [f"Appearances: {_absent(record, events_shown=False)}"]
    if record.events_unregistered is not None:
        lines.append(APPEARANCES_EVENTS_NOTE)
    return lines


def _section(label: str, record: object, present: Any) -> list[str]:
    if record is None:
        return [f"{label}: {NOT_IN_FILE}"]
    if isinstance(record, _Absent):
        return [f"{label}: {_absent(record)}"]
    lines: list[str] = present(record)
    return lines


def describe(char: CharDB) -> list[str]:
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
        lines.append(f"Client: {NOT_IN_FILE}")
    if char.probe is None:
        lines.append("Probe: not in the file")
    else:
        lost = (
            "; lost: WowLabCharDB loaded without a probe count, so the count restarted at 1 "
            "on this load"
            if char.probe.lost
            else ""
        )
        lines.append(
            f"Probe: loads {char.probe.loads} (each login or /reload with the addon enabled "
            f"adds 1 to the value the file held){lost}"
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
    lines += _appearances(collections.appearances if collections else None)
    lines += _section("Currencies", char.currencies, _currencies)
    lines += _section("Professions", char.professions, _professions)
    unknown = unknown_keys(char)
    if unknown:
        lines.append("")
        lines.append(f"Keys this reader does not know (kept in --json): {', '.join(unknown)}")
    return lines


def describe_account(account: AccountDB) -> list[str]:
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


def summary(char: CharDB) -> str:
    """One character's row in `wowlab char list` (M12-09): the schema, the
    client that saved the file (its full version, which need not be the
    installed build) and the spec id; an absent section's reason is left to
    `wowlab char show`."""
    client = char.client
    if isinstance(client, Client):
        if client.version and client.build:
            client_words = f"saved by client {client.version}.{client.build}"
        else:
            client_words = (
                f"saved by client version {client.version or 'not returned'}, "
                f"build {client.build or 'not returned'}"
            )
    elif isinstance(client, AbsentRecord):
        client_words = "client absent (the reason is in char show)"
    else:
        client_words = "client not in the file"
    spec = char.spec
    if isinstance(spec, Spec):
        spec_words = "spec id not returned" if spec.id is None else f"spec id {spec.id}"
    elif isinstance(spec, AbsentSection):
        spec_words = "spec absent (the reason is in char show)"
    else:
        spec_words = "spec not in the file"
    return f"schema {char.schema_}, {client_words}, {spec_words}"
