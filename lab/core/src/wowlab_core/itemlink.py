"""itemlink: the item-link string the lab-addon records (docs/LAB_PLAN.md §16.2, M14-05).

A pure, lossless decoder of text (L3, L4): it reads a string the addon
stored in `gear.slots[].link` (`labaddon.GearSlot.link`), evaluates
nothing, opens nothing, and keeps the input whole in `ItemLink.raw`.
`ItemLink.text()` rebuilds the link from its parts and equals `raw` for
every link `parse` accepts.

Shape (docs/LAB_FORMATS.md §4.2 and its amendments, 2026-09-28 and
2026-10-18):

    |c<colour>|Hitem:<payload>|h[<name>]|h|r

- `colour` is the text between `|c` and `|H`, kept as written. Two forms
  are known: the named form `nIQ<n>:` (`|cnIQ2:`, the only form in the
  committed captures) and the hex form `AARRGGBB` (`|cff1eff00`, the hex form
  other clients write **[verify]**, unobserved on Forever). Anything else is kept with form
  `other`; what a colour means is the slot table's business
  (`gear_slots.toml`), not the parser's.
- `payload` is split on `:` into `fields`, every one kept raw. Field 0 is
  the item id. Later fields are also named where community documentation
  (warcraft.wiki.gg, "ItemLink") names them, **[verify]** on Forever except
  where the 2026-10-18 amendment says otherwise: 1 enchant, 2 to 5 gems,
  6 suffix, 7 unique id, 8 link level, 9 specialization, 10 the modifier
  mask (older documentation: the upgrade type), 11 the item context, 12 the
  bonus-id count and that many bonus ids, then the modifier count and that
  many `type:value` pairs, three relic groups (a count and that many bonus
  ids each), the crafter GUID and the extra enchant. Any field past those
  is kept in `extra_fields`. A count that runs past the end of the payload
  stops the naming there: the rest are kept in `extra_fields` and
  `complete` is false.
- `name` is the bracketed text and nothing else. No name, icon or tooltip
  is ever looked up (§14.4).

The addon blanks every GUID-shaped run (`Player-<digits>-<hex>`) before it
stores a link (`lab/addon/WowLab/Gear.lua`, ADR-0026), and the record
reader refuses letters in the payload, so a link read from a `WowLab.lua`
never carries one. A link from elsewhere might: `guid_shaped` says so, and
`crafter` is the raw crafter field.

`parse` raises `ItemLinkError`, with the offset of the fault, for a string
that is not an item link of this shape (no `|c`, no `|H`, a link type
other than `item`, no closing `|h`, no `[name]`, or text after `|h` other
than `|r`), and for one longer than `MAX_LINK_CHARS`.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "MAX_LINK_CHARS",
    "ItemLink",
    "ItemLinkError",
    "LinkModifier",
    "parse",
]

# The record reader caps a link at 1024 characters (`labaddon.ItemLink`);
# a decoder handed anything far longer is refused before it is split.
MAX_LINK_CHARS = 4096

# A whole number as a field holds it: optional minus, at most 18 digits (a
# longer run stays raw, so no conversion meets CPython's digit limit).
_INT = re.compile(r"-?[0-9]{1,18}")
_NAMED = re.compile(r"n[A-Za-z0-9_]+:")
_HEX = re.compile(r"[0-9A-Fa-f]{8}")
_GUID = re.compile(r"Player-[0-9]+-[0-9A-Fa-f]+")

LINK_TYPE = "item"
_GEMS = 4
_RELICS = 3


class ItemLinkError(ValueError):
    """A string that is not an item link of the shape this module reads."""

    def __init__(self, message: str, offset: int) -> None:
        super().__init__(f"{message} (at character {offset})")
        self.offset = offset


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LinkModifier(_Model):
    """One `type:value` pair after the modifier count, raw and as numbers."""

    type: int | None
    value: int | None
    raw: tuple[str, str]


class ItemLink(_Model):
    """One item link, decoded. `raw` is the input; every named field is also
    in `fields` as written, and a named number is `None` when its field is
    empty or not a whole number."""

    raw: str
    colour: str  # between `|c` and `|H`, as written: "nIQ2:" or "ff1eff00"
    colour_form: Literal["named", "hex", "other"]
    link_type: str  # "item"
    fields: list[str]  # the payload split on ":", every field raw
    name: str  # the bracketed text
    tail: str  # after the closing `|h`: "|r"
    item_id: int | None
    enchant_id: int | None
    gem_ids: list[int]  # the gem fields that hold a number, in order
    suffix_id: int | None
    unique_id: int | None
    link_level: int | None
    spec_id: int | None
    modifiers_mask: int | None  # field 10; older documentation: upgrade type
    context: int | None
    bonus_ids: list[int]
    modifiers: list[LinkModifier]
    relic_bonus_ids: list[list[int]]  # one list per relic group read
    crafter: str | None  # the crafter field, raw; None when the walk did not reach it
    extra_enchant_id: int | None
    extra_fields: list[str]  # fields past the last named one, raw
    complete: bool  # every count fitted inside the payload
    guid_shaped: bool  # a `Player-<digits>-<hex>` run in any field

    def text(self) -> str:
        """The link rebuilt from its parts; equals `raw`."""
        return (
            f"|c{self.colour}|H{self.link_type}:{':'.join(self.fields)}|h[{self.name}]|h{self.tail}"
        )


def _int(field: str) -> int | None:
    return int(field) if _INT.fullmatch(field) else None


def _colour_form(colour: str) -> Literal["named", "hex", "other"]:
    if _NAMED.fullmatch(colour):
        return "named"
    if _HEX.fullmatch(colour):
        return "hex"
    return "other"


class _Walk:
    """The named fields after field 11, read in order, each count giving how
    many fields follow it. A count with no room left stops the walk."""

    def __init__(self, fields: list[str]) -> None:
        self.fields = fields
        self.at = 12
        self.complete = True

    def counted(self, width: int) -> list[str] | None:
        """A count field and `width` fields per counted entry; `None` when
        the payload ended before the count, or the count does not fit."""
        if self.at >= len(self.fields):
            return None
        raw_count = self.fields[self.at]
        count = 0 if raw_count == "" else _int(raw_count)
        start = self.at + 1
        if count is None or count < 0 or start + count * width > len(self.fields):
            self.complete = False
            return None
        self.at = start + count * width
        return self.fields[start : self.at]

    def one(self) -> str | None:
        if self.at >= len(self.fields):
            return None
        self.at += 1
        return self.fields[self.at - 1]


def _ints(fields: list[str]) -> list[int]:
    return [n for n in (_int(f) for f in fields) if n is not None]


def parse(raw: str) -> ItemLink:
    """`raw` decoded. Raises `ItemLinkError`."""
    if len(raw) > MAX_LINK_CHARS:
        raise ItemLinkError(f"longer than {MAX_LINK_CHARS} characters", MAX_LINK_CHARS)
    if not raw.startswith("|c"):
        raise ItemLinkError("an item link starts with a colour code |c", 0)
    h_at = raw.find("|H", 2)
    if h_at < 0:
        raise ItemLinkError("no |H after the colour code", 2)
    colour = raw[2:h_at]
    if colour == "" or "|" in colour:
        raise ItemLinkError("the colour code is empty or holds |", 2)
    body = h_at + 2
    end = raw.find("|h", body)
    if end < 0:
        raise ItemLinkError("no closing |h after the link data", body)
    link_type, colon, payload = raw[body:end].partition(":")
    if link_type != LINK_TYPE or not colon:
        raise ItemLinkError(f"the link type is {link_type!r}, not {LINK_TYPE!r}", body)
    name_at = end + 2
    if raw[name_at : name_at + 1] != "[":
        raise ItemLinkError("no [ after the link data's |h", name_at)
    close = raw.find("]|h", name_at)
    if close < 0:
        raise ItemLinkError("no ]|h closing the bracketed name", name_at)
    name = raw[name_at + 1 : close]
    tail = raw[close + 3 :]
    if tail != "|r":
        raise ItemLinkError("the link does not end with |r right after its |h", close + 3)

    fields = payload.split(":")

    def at(i: int) -> str:
        return fields[i] if i < len(fields) else ""

    walk = _Walk(fields)
    bonus = walk.counted(1)
    pairs = walk.counted(2) if bonus is not None else None
    relics: list[list[int]] = []
    if pairs is not None:
        for _ in range(_RELICS):
            group = walk.counted(1)
            if group is None:
                break
            relics.append(_ints(group))
    reached = pairs is not None and len(relics) == _RELICS and walk.complete
    crafter = walk.one() if reached else None
    extra_enchant = walk.one() if crafter is not None else None
    extra = fields[walk.at :]  # where the walk stopped: past the extra enchant, or a count
    return ItemLink(
        raw=raw,
        colour=colour,
        colour_form=_colour_form(colour),
        link_type=link_type,
        fields=fields,
        name=name,
        tail=tail,
        item_id=_int(at(0)),
        enchant_id=_int(at(1)),
        gem_ids=_ints([at(i) for i in range(2, 2 + _GEMS)]),
        suffix_id=_int(at(6)),
        unique_id=_int(at(7)),
        link_level=_int(at(8)),
        spec_id=_int(at(9)),
        modifiers_mask=_int(at(10)),
        context=_int(at(11)),
        bonus_ids=_ints(bonus or []),
        modifiers=[
            LinkModifier(type=_int(t), value=_int(v), raw=(t, v))
            for t, v in zip((pairs or [])[0::2], (pairs or [])[1::2], strict=True)
        ],
        relic_bonus_ids=relics,
        crafter=crafter,
        extra_enchant_id=_int(extra_enchant) if extra_enchant is not None else None,
        extra_fields=extra,
        complete=walk.complete,
        guid_shaped=any(_GUID.search(f) for f in fields),
    )
