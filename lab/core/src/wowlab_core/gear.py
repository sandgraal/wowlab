"""gear: one character's equipped gear, slot by slot (docs/LAB_PLAN.md §16.2, M14-05).

Reads nothing from an install itself: it takes the `gear` section of a
record `labaddon.read_char` already parsed, decodes each slot's link with
`itemlink`, and names the slot and the colour from the slot table. What
`wowlab char gear` prints and its `--json` (`cli.CharGearReport`) are built
from these models.

- The slot number is the client's (`INVSLOT_FIRST_EQUIPPED` to
  `INVSLOT_LAST_EQUIPPED`, as the record gives them); the slot's name comes
  from `gear_slots.toml`, shipped with the package and **[verify]** on
  Forever. Every number in the record's range is listed, filled or empty.
- Item level and the averages are the client's figures as recorded, never
  derived from bonus ids (GLOSSARY "Item level").
- Quality is the link's colour code as decoded; the word for it is the
  slot table's, community documentation, **[verify]**.
- The item name is the link's bracketed text; nothing is fetched.

The slot table is overridable as `binds coach`'s cost table is: a
`gear_slots.toml` in wowlab's user config folder, read through
`snapshot.read_regular_file`, replaces each value it sets.
"""

from __future__ import annotations

import re
import tomllib
from importlib import resources
from pathlib import Path
from typing import Literal

import platformdirs
from pydantic import BaseModel, ConfigDict

from wowlab_core import itemlink, labaddon, snapshot

__all__ = [
    "SLOTS_FILE",
    "EmptySlot",
    "GearAverage",
    "GearSlot",
    "GearSpec",
    "GearUpgrade",
    "SlotTable",
    "SlotTableError",
    "SlotTableSource",
    "average_of",
    "load_slot_table",
    "parse_slot_table",
    "rows",
    "shipped_slots_path",
    "spec_of",
    "user_slots_path",
]

SLOTS_FILE = "gear_slots.toml"
SLOTS_LIMIT = 1024 * 1024  # a slot table is about 2 KiB; a bigger file is refused unread
SLOT_MAX = 1000  # the client numbers equipment slots from 1 to 19 on Forever (M11-03)
_TEXT = re.compile(r"[\x20-\x7e]{1,64}")
_COLOUR = re.compile(r"[0-9A-Za-z:]{1,16}")  # the colour codes `labaddon.ItemLink` admits
_SECTIONS = ("slots", "quality")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ─── the slot table ──────────────────────────────────────────────────────────


class SlotTableError(ValueError):
    """A slot table that cannot be used, with the file it came from."""


class SlotTable(_Model):
    slots: dict[int, str]  # the client's slot number: its name
    quality: dict[str, str]  # a colour code as written between `|c` and `|H`: a quality word


class SlotTableSource(_Model):
    """The table in effect and where it came from."""

    source: Literal["default", "user"]  # "default": the shipped table, unchanged
    shipped: str
    user_file: str
    table: SlotTable


def shipped_slots_path() -> str:
    return str(resources.files("wowlab_core").joinpath(SLOTS_FILE))


def user_slots_path() -> Path:
    return platformdirs.user_config_path("wowlab") / SLOTS_FILE


def _text(where: str, value: object) -> str:
    if not isinstance(value, str) or not _TEXT.fullmatch(value):
        raise SlotTableError(
            f"{where}: a name is 1 to 64 printable ASCII characters, not {value!r}"
        )
    return value


def parse_slot_table(text: str, base: SlotTable | None = None) -> SlotTable:
    """A slot table from TOML text; with `base`, each value given replaces
    `base`'s and the rest keep theirs. Two keys naming one slot number (`1`
    and `01`) are refused. Raises `SlotTableError`."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SlotTableError(f"not valid TOML: {exc}") from exc
    unknown = sorted(set(data) - set(_SECTIONS))
    if unknown:
        raise SlotTableError(f"unknown table or key {', '.join(unknown)} (known: slots, quality)")
    slots = dict(base.slots) if base is not None else {}
    quality = dict(base.quality) if base is not None else {}
    for section in _SECTIONS:
        part = data.get(section, {})
        if not isinstance(part, dict):
            raise SlotTableError(f"[{section}] must be a table")
        seen: dict[str, str] = {}
        for key, value in part.items():
            if section == "slots":
                if not key.isascii() or not key.isdigit() or not 1 <= int(key) <= SLOT_MAX:
                    raise SlotTableError(
                        f"[slots]: {key!r} is not a slot number (a whole number from 1 to "
                        f"{SLOT_MAX})"
                    )
                number = str(int(key))
                if number in seen:
                    raise SlotTableError(f"[slots]: {seen[number]!r} and {key!r} are one slot")
                seen[number] = key
                slots[int(key)] = _text(f"[slots] {key}", value)
            else:
                if not _COLOUR.fullmatch(key):
                    raise SlotTableError(
                        f"[quality]: {key!r} is not a colour code as a link writes it "
                        "between |c and |H (nIQ2: or ff1eff00)"
                    )
                folded = key.casefold()
                if folded in seen:
                    raise SlotTableError(f"[quality]: {seen[folded]!r} and {key!r} are one code")
                seen[folded] = key
                quality[key] = _text(f"[quality] {key}", value)
    return SlotTable(slots=dict(sorted(slots.items())), quality=quality)


def load_slot_table() -> SlotTableSource:
    """The shipped table, with the player's own file over it when there is
    one. Raises `SlotTableError` naming the file at fault."""
    shipped = shipped_slots_path()
    text = resources.files("wowlab_core").joinpath(SLOTS_FILE).read_text(encoding="utf-8")
    try:
        table = parse_slot_table(text)
    except SlotTableError as exc:  # a packaging fault, not the player's
        raise SlotTableError(f"{shipped}: {exc}") from exc
    user = user_slots_path()
    if not user.exists() and not user.is_symlink():
        return SlotTableSource(source="default", shipped=shipped, user_file=str(user), table=table)
    try:
        data = snapshot.read_regular_file(user, limit=SLOTS_LIMIT)
        mine = parse_slot_table(data.decode("utf-8-sig"), table)
    except (OSError, snapshot.UnsafeReadError, UnicodeDecodeError, SlotTableError) as exc:
        raise SlotTableError(f"{user}: {exc}") from exc
    return SlotTableSource(source="user", shipped=shipped, user_file=str(user), table=mine)


def quality_word(colour: str, table: SlotTable) -> str | None:
    """The word for a colour code: exact, then with case folded (hex codes
    are written in either case)."""
    if colour in table.quality:
        return table.quality[colour]
    folded = colour.casefold()
    for code, word in table.quality.items():
        if code.casefold() == folded:
            return word
    return None


# ─── the rows ────────────────────────────────────────────────────────────────


class GearUpgrade(_Model):
    """The link's upgrade fields as the payload orders them, **[verify]**:
    field 10 (the modifier mask; older documentation: the upgrade type) and
    the `type:value` modifier pairs after the bonus ids."""

    mask: int | None
    modifiers: list[itemlink.LinkModifier]


class GearSlot(_Model):
    """One filled slot. Every field after `name` is decoded from `raw_link`
    and **[verify]** past `item_id` (docs/LAB_FORMATS.md §4.2, 2026-10-18)."""

    slot: int  # the client's slot number
    slot_name: str | None  # from the slot table; None when it has no name for the number
    raw_link: str  # the link as the record holds it
    item_id: int | None
    name: str  # the link's bracketed text
    colour: str  # the link's colour code, as written between |c and |H
    quality: str | None  # the slot table's word for `colour`; None when it has none
    item_level: int | None  # the client's figure as recorded
    item_level_api: str | None
    enchant_id: int | None
    gem_ids: list[int]
    suffix_id: int | None
    spec_id: int | None
    bonus_ids: list[int]
    upgrade: GearUpgrade
    crafter_removed: bool  # the addon blanked a crafter GUID from this link


class EmptySlot(_Model):
    slot: int
    slot_name: str | None


class GearAverage(_Model):
    """`GetAverageItemLevel()`'s three returns as recorded, or the addon's
    reason they are absent."""

    equipped: labaddon.Number | None
    overall: labaddon.Number | None
    pvp: labaddon.Number | None
    absent: str | None


class GearSpec(_Model):
    """The record's `spec` section: the id the client gave, or why not."""

    id: int | None
    index: int | None
    api: str | None
    absent: str | None  # the addon's reason, or "not in the file"


def rows(gear: labaddon.Gear, table: SlotTable) -> tuple[list[GearSlot], list[EmptySlot]]:
    """Each filled slot in the client's slot order, and every number from
    `first_slot` to `last_slot` with nothing in it. A link `itemlink` cannot
    read raises `itemlink.ItemLinkError` (the record reader admits none)."""
    filled: list[GearSlot] = []
    for slot in sorted(gear.slots, key=lambda s: s.slot):
        link = itemlink.parse(slot.link)
        filled.append(
            GearSlot(
                slot=slot.slot,
                slot_name=table.slots.get(slot.slot),
                raw_link=slot.link,
                item_id=link.item_id,
                name=link.name,
                colour=link.colour,
                quality=quality_word(link.colour, table),
                item_level=slot.item_level,
                item_level_api=slot.item_level_api,
                enchant_id=link.enchant_id,
                gem_ids=link.gem_ids,
                suffix_id=link.suffix_id,
                spec_id=link.spec_id,
                bonus_ids=link.bonus_ids,
                upgrade=GearUpgrade(mask=link.modifiers_mask, modifiers=link.modifiers),
                crafter_removed=slot.crafter_removed,
            )
        )
    taken = {s.slot for s in gear.slots}
    span = (
        range(gear.first_slot, gear.last_slot + 1)
        if gear.last_slot - gear.first_slot < SLOT_MAX
        else ()
    )
    empty = [EmptySlot(slot=n, slot_name=table.slots.get(n)) for n in span if n not in taken]
    return filled, empty


def average_of(gear: labaddon.Gear) -> GearAverage:
    average = gear.average
    if isinstance(average, labaddon.GearAverage):
        return GearAverage(
            equipped=average.equipped, overall=average.overall, pvp=average.pvp, absent=None
        )
    return GearAverage(equipped=None, overall=None, pvp=None, absent=average.absent)


def spec_of(char: labaddon.CharDB) -> GearSpec:
    spec = char.spec
    if isinstance(spec, labaddon.Spec):
        return GearSpec(id=spec.id, index=spec.index, api=spec.api, absent=None)
    if spec is None:
        return GearSpec(id=None, index=None, api=None, absent=labaddon.NOT_IN_FILE)
    return GearSpec(id=None, index=None, api=None, absent=spec.absent)
