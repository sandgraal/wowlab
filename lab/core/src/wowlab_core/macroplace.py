"""macroplace: where each macro of `macros-cache.txt` may sit on the action bars
(M14-07, docs/LAB_PLAN.md §16.2).

Pure functions over what other modules already read: the macros of one
account's and, optionally, one character's `macros-cache.txt`
(`wtfconfig.parse_macros`, lossless, L4), the character's lab-addon
`actionbars` record (`labaddon`, schema 3) and the keys `keymap` joins to
the action slots. Nothing here opens a file, and nothing writes anywhere (L1).

An action slot that holds a macro keeps an id only, never the macro's name
or body (ADR-0026's no-names rule). With no subtype the id is the macro
index **[verify]**; with one, it is the spell or item id the macro shows now
**[verify]** (§15.2, `docs/LAB_FORMATS.md` 2026-10-16, item 7). Which file
record a macro index names is not known, so the placement this module gives
is a candidate, never a fact:

- `unconfirmed`: the slots with no subtype whose id is the index the
  candidate numbering (`candidate_index`) gives the record, and the keys
  `keymap` says reach those slots;
- `none`: no slot is a candidate (no action-bar record, an index no macro
  slot holds, or a record the numbering gives no index);
- `confirmed`: in the model and emitted by nothing here. It is kept for the
  ticket that follows the owner's capture (`docs/handoffs/M14-macro-place.md`)
  and the dated amendment to `docs/LAB_FORMATS.md` §7 that records the rule
  the capture settles.

The candidate numbering is Retail's, from memory **[verify]**: account
macros are indices 1 to `ACCOUNT_MACROS` in the order of the account file's
records, and character macros follow from `ACCOUNT_MACROS + 1` in the order
of the character file's records. In the one capture with macro slots (build
70425) the character file's 29 records had ids `...01` to `...1D` in file
order, and the two macro slots held 149 and 140, which this numbering reads
as the character file's 29th and 20th records; that capture did not show
which macros the owner had put there.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import keymap, labaddon

__all__ = [
    "ACCOUNT_MACROS",
    "CANDIDATE_RULE",
    "MacroScope",
    "PlacedSlot",
    "Placement",
    "Status",
    "candidate_index",
    "place",
]

# How many macro indices account macros take before character macros start,
# in the candidate numbering: Retail's MAX_ACCOUNT_MACROS, from memory
# **[verify]** on Forever (docs/LAB_FORMATS.md, 2026-10-16, item 7).
ACCOUNT_MACROS = 120

MacroScope = Literal["account", "character"]
Status = Literal["confirmed", "unconfirmed", "none"]

CANDIDATE_RULE = (
    "An action slot holding a macro keeps an id only, never the macro's name or body. With no "
    "subtype the id is taken to be the macro index [verify], and which macro of "
    "macros-cache.txt an index names is not known [verify]: the candidate numbering is "
    f"Retail's, from memory (account macros 1 to {ACCOUNT_MACROS} in the account file's order, "
    f"character macros from {ACCOUNT_MACROS + 1} in the character file's order). Every slot "
    "given here is a candidate only."
)

_ACTIONBARS_NEEDS = frozenset({"GetActionInfo"})


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PlacedSlot(_Model):
    """An action slot a macro may sit at, with the bar and page the default
    UI shows it on (`keymap.slot_page_note`)."""

    slot: int
    page_note: str
    unfollowed_page: bool  # on a main-bar page that is not followed (`keymap.UNFOLLOWED_SLOTS`)


class Placement(_Model):
    """Where one macro may sit. `index` is the macro index the candidate
    numbering gives the record (None: it gives none); `slots` are the
    macro slots with no subtype holding that index, and `keys` the
    `keymap` rows whose command presses one of them; `reason` says why, in
    words."""

    status: Status
    index: int | None
    slots: list[PlacedSlot]
    keys: list[keymap.KeyRow]
    reason: str


def candidate_index(scope: MacroScope, position: int) -> int | None:
    """The macro index the candidate numbering gives the record at
    `position` (1-based, in its file's order) of the `scope` file, or None
    for an account record past `ACCOUNT_MACROS`."""
    if position < 1:
        raise ValueError(f"position {position} is not 1-based")
    if scope == "account":
        return position if position <= ACCOUNT_MACROS else None
    return ACCOUNT_MACROS + position


def _bars(
    section: labaddon.ActionBars | labaddon.AbsentProbedSection | None, no_bars: str
) -> tuple[labaddon.ActionBars | None, str | None]:
    """The action bars, or why there are none; and, with bars, what they may lack."""
    if section is None:
        return None, no_bars
    if isinstance(section, labaddon.AbsentProbedSection):
        return None, f"the action-bar section is absent: {section.absent}"
    why = keymap.dropped(section.probes, _ACTIONBARS_NEEDS)
    untyped = sum(1 for s in section.slots if s.type is None)
    if untyped:
        why.append(f"{untyped} slot(s) have no type")
    lacks = (
        f"the action-bar record is incomplete ({'; '.join(why)}), so a macro slot may be one "
        "of those dropped"
        if why
        else None
    )
    return section, lacks


def _words(slots: Sequence[int]) -> str:
    return ", ".join(str(s) for s in slots)


def place(
    macros: Sequence[tuple[MacroScope, int]],
    actionbars: labaddon.ActionBars | labaddon.AbsentProbedSection | None,
    keys: Sequence[keymap.KeyRow],
    *,
    no_bars: str = f"no action-bar record ({keymap.GET_RECORD})",
) -> list[Placement]:
    """One placement per `(scope, position)` in `macros`, in that order.
    `actionbars` is the character's record section (None: there is none,
    and `no_bars` says why); `keys` are `keymap`'s rows for the same
    character."""
    bars, why = _bars(actionbars, no_bars)
    if bars is None:
        assert why is not None
        reason = f"{keymap.CANNOT_PLACE}: {why}"
        return [
            Placement(
                status="none",
                index=candidate_index(scope, position),
                slots=[],
                keys=[],
                reason=reason,
            )
            for scope, position in macros
        ]
    by_index: dict[int, list[int]] = {}
    subtyped: list[int] = []
    for s in sorted(bars.slots, key=lambda s: s.slot):
        if s.type != "macro":
            continue
        if s.sub_type:
            subtyped.append(s.slot)
        elif s.id is not None:
            by_index.setdefault(s.id, []).append(s.slot)
    reaching: Mapping[int, list[keymap.KeyRow]] = _keys_by_slot(keys)
    walked = f"slots {bars.first_slot} to {bars.last_slot}"
    out: list[Placement] = []
    for scope, position in macros:
        index = candidate_index(scope, position)
        found = by_index.get(index, []) if index is not None else []
        if found:
            reason = (
                f"{scope} macro {position} in its file's order is macro index {index} in the "
                f"candidate numbering [verify], and {'slot' if len(found) == 1 else 'slots'} "
                f"{_words(found)} {'holds' if len(found) == 1 else 'hold'} a macro with index "
                f"{index} and no subtype"
            )
            out.append(
                Placement(
                    status="unconfirmed",
                    index=index,
                    slots=[
                        PlacedSlot(
                            slot=n,
                            page_note=keymap.slot_page_note(n),
                            unfollowed_page=n in keymap.UNFOLLOWED_SLOTS,
                        )
                        for n in found
                    ],
                    keys=[row for n in found for row in reaching.get(n, [])],
                    reason=reason,
                )
            )
            continue
        if index is None:
            parts = [
                f"{scope} macro {position} is past the {ACCOUNT_MACROS} indices the candidate "
                "numbering gives account macros [verify]"
            ]
        elif by_index or subtyped:
            parts = [
                f"no macro slot with no subtype on the action bars ({walked}) holds index "
                f"{index}, which the candidate numbering [verify] gives {scope} macro "
                f"{position}; under another numbering it may be on one"
            ]
        else:
            parts = [f"the action-bar record has no macro slot ({walked})"]
        if subtyped:
            parts.append(
                f"{_words(subtyped)}: macro {'slot' if len(subtyped) == 1 else 'slots'} with a "
                "subtype, whose id is the spell or item the macro shows [verify], may hold any "
                "macro"
            )
        if why is not None:
            parts.append(why)
        out.append(
            Placement(status="none", index=index, slots=[], keys=[], reason="; ".join(parts))
        )
    return out


def _keys_by_slot(keys: Sequence[keymap.KeyRow]) -> dict[int, list[keymap.KeyRow]]:
    out: dict[int, list[keymap.KeyRow]] = {}
    for row in keys:
        if row.slot is not None:
            out.setdefault(row.slot, []).append(row)
    return out
