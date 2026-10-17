"""bindscoach: the reports `wowlab binds coach` makes (M13-08, docs/LAB_PLAN.md §15.2).

Pure functions over what other modules already read: the player's own casts
in a combat log (`combatlog.cast_counts`) and the character's lab-addon
record (`labaddon`, schema 3, for the action bars, the bindings and the
spellbook). The one file this module reads itself is the cost table, from
the package or from wowlab's user config folder, never from an install;
nothing here writes anywhere (L1).

Key to spell. `bindings-cache.wtf` maps keys to binding commands, not to
spells, and what sits in an action slot is server state (§15.2), so a key
reaches a spell through two records of the lab-addon: `bindings` (the keys
`GetBindingKey` gave for each command) and `actionbars` (what each slot
held). A command reaches a slot through `BAR_PAGES`: the default UI's action
buttons, where button n of the bar showing page p is slot (p - 1) * 12 + n.
That numbering is the default UI's **[verify]**. The real record is
consistent with it and proves no more: `MULTIACTIONBAR1BUTTON1` to `3` hold
keys and slots 61 to 63 hold spells, and slots 49 and 50 fit page 5; no
`MULTIACTIONBAR5` to `7` command has a key in it, so bars 5 to 7 are
unconfirmed on Forever. The main bar is read at page 1 only. Its other pages
(`UNFOLLOWED_SLOTS`: page 2, and the pages forms, stances and stealth show)
are not followed, nor are the pet and stance bars, an addon's buttons, or a
key bound straight to a spell, macro or click (the record leaves those
commands out and counts them). Only spell slots count: an item's use logs
the item's spell, which no slot record ties to the item, and a macro slot
holds a macro index, not what the macro casts.

A cast matches the slots that hold its exact spell id. A cast at an id no
slot holds falls back to the slots whose spell the character's spellbook
names with the cast's name (compared without case), and says so on the
spell's line: the real log is older than the real record, and casts Shadow
Bolt at the id of its rank 2 while the bar, recorded later, holds rank 3.
A player who binds two ranks on purpose keeps both apart, since each rank's
id is on its own slot. Without a spellbook record, ids alone match, and the
report says so.

The reports:

- `frequent-on-high-cost`: a spell the player casts often (at least
  `frequent_share` of their casts and at least `frequent_min_casts`) whose
  every key is a high-cost key (`high_cost` or more). Casts at several ids
  (ranks) of one spell that matched the same slots press the same keys, so
  they are added up first; ranks bound on purpose to their own slots stay
  apart.
- `cast-without-bind`: a spell cast with no key reaching it: on an action
  slot no key reaches (clicked), or on no slot at all (cast from a macro, the
  spellbook, an addon's button, or a key bound straight to it). A spell held
  only on unfollowed main-bar pages is listed apart (`on_unfollowed_page`),
  not here.
- `bound-never-cast`: a slot a key reaches whose spell the log never shows
  the player casting: no cast at the slot's own id, and none matched to it by
  name. Cannot judge when the log has none of the player's casts, since then
  every bound spell would be listed.

"High-cost" is an opinion: the cost table is `binds_coach_costs.toml`,
shipped beside this module, which a player overrides key by key with a file
of the same name in wowlab's user config folder.

A record section whose own probes say values were dropped (a call raised, a
value was secret or untestable, a needed function was missing), or that
holds slots with no type, makes every report "cannot judge": a key or slot
dropped may be the one that changes the answer. Many binding commands left
out of the record (an action-bar addon binds through its own click
commands) make `cast-without-bind` and `frequent-on-high-cost` "cannot
judge" too, when they have anything to report: a key the coach cannot see
may reach the spell, and may be cheaper.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import platformdirs
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from wowlab_core import combatlog, labaddon, snapshot

__all__ = [
    "BAR_PAGES",
    "COSTS_FILE",
    "COSTS_LIMIT",
    "GET_RECORD",
    "LEFT_OUT_LIMIT",
    "MODIFIERS",
    "REPORTS",
    "UNFOLLOWED_SLOTS",
    "BoundNeverCast",
    "CannotJudge",
    "CastSpell",
    "CastWithoutBind",
    "CoachResult",
    "CostTable",
    "CostTableError",
    "Costs",
    "FrequentOnHighCost",
    "KeyCost",
    "OnUnfollowedPage",
    "Report",
    "coach",
    "command_slot",
    "key_cost",
    "load_costs",
    "parse_costs",
    "shipped_costs_path",
    "user_costs_path",
]

COSTS_FILE = "binds_coach_costs.toml"
COSTS_LIMIT = 1024 * 1024  # a cost table is a few KiB; a bigger file is refused unread

Report = Literal["frequent-on-high-cost", "cast-without-bind", "bound-never-cast"]
REPORTS: tuple[Report, ...] = ("frequent-on-high-cost", "cast-without-bind", "bound-never-cast")

# The default UI's action buttons: binding command prefix to the page its bar
# shows (Blizzard's ActionButton and MultiActionBar frames) **[verify]**. The
# real record is consistent with pages 5 and 6 only; see the module docstring.
BAR_PAGES: Mapping[str, int] = {
    "ACTIONBUTTON": 1,
    "MULTIACTIONBAR3BUTTON": 3,  # right bar
    "MULTIACTIONBAR4BUTTON": 4,  # right bar 2
    "MULTIACTIONBAR2BUTTON": 5,  # bottom right bar
    "MULTIACTIONBAR1BUTTON": 6,  # bottom left bar
    "MULTIACTIONBAR5BUTTON": 13,  # unconfirmed on Forever
    "MULTIACTIONBAR6BUTTON": 14,
    "MULTIACTIONBAR7BUTTON": 15,
}
_BUTTON = re.compile(r"(ACTIONBUTTON|MULTIACTIONBAR[1-7]BUTTON)([1-9]|1[0-2])")

# Main-bar pages the coach does not follow **[verify]**: page 2 (paging, as
# `ACTIONPAGE2` or SHIFT-MOUSEWHEEL show it) and pages 7 to 10 (the bars a
# form, stance or stealth swaps in), and pages 11 and 12 (slots 121 to 144,
# the possess, override or vehicle bars where the client has them; whether
# Forever has them is unknown). The main bar's keys reach them only while
# that page is shown.
UNFOLLOWED_SLOTS = frozenset(range(13, 25)) | frozenset(range(73, 145))

MODIFIERS = ("ALT", "CTRL", "SHIFT", "META")
_SIDES = ("L", "R")

# A bar's worth of binding commands left out of the record: past this, keys
# probably go through an action-bar addon's click commands, which the coach
# cannot read, so "cast with no bind" cannot be judged.
LEFT_OUT_LIMIT = 12
_BAR_ADDONS = "an action-bar addon such as Bartender, Dominos or ElvUI binds through these"
_UNFOLLOWED_DETAIL = (
    "on a main-bar page shown by a form, stance, stealth, paging, or a possess, override or "
    "vehicle bar, which the coach does not follow"
)

GET_RECORD = (
    "install or update the lab-addon with the client closed (wowlab addon install lab), log "
    "in on this character, then log out or /reload; schema 3 is needed"
)
_ACTIONBARS_NEEDS = frozenset({"GetActionInfo"})
_BINDINGS_NEEDS = frozenset({"GetNumBindings", "GetBinding", "GetBindingKey"})


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ─── the cost table ──────────────────────────────────────────────────────────


class CostTableError(ValueError):
    """A cost table that cannot be used, with the file it came from."""


class CostTable(_Model):
    """The cost of each key, the modifiers' costs and the two thresholds."""

    high_cost: int = Field(ge=0, le=1000)
    frequent_share: float = Field(gt=0, le=1)
    frequent_min_casts: int = Field(ge=1, le=1000)
    unlisted: int = Field(ge=0, le=1000)
    modifiers: dict[str, int]  # ALT, CTRL, SHIFT, META
    keys: dict[str, int]  # upper case, as the client writes key names


class Costs(_Model):
    """The table in effect and where it came from."""

    source: Literal["default", "user"]  # "default": the shipped table, unchanged
    shipped: str  # the shipped table's path
    user_file: str  # where a player's own table is read from
    table: CostTable


def shipped_costs_path() -> str:
    return str(resources.files("wowlab_core").joinpath(COSTS_FILE))


def user_costs_path() -> Path:
    return platformdirs.user_config_path("wowlab") / COSTS_FILE


_SECTIONS = {"coach", "modifiers", "keys"}
_COACH = {"high_cost", "frequent_share", "frequent_min_casts", "unlisted"}


def _cost(where: str, value: object, least: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not least <= value <= 1000:
        raise CostTableError(
            f"{where}: a cost is a whole number from {least} to 1000, not {value!r}"
        )
    return value


def _share(where: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= 1:
        raise CostTableError(
            f"{where}: a share is a fraction above 0 and at most 1 (0.05 is 5%), not {value!r}"
        )
    return float(value)


def _coach_value(name: str, value: object) -> int | float:
    where = f"[coach] {name}"
    if name == "frequent_share":
        return _share(where, value)
    return _cost(where, value, 1 if name == "frequent_min_casts" else 0)


def parse_costs(text: str, base: CostTable | None = None) -> CostTable:
    """A cost table from TOML text; with `base`, each value given replaces
    `base`'s and the rest keep theirs. Raises `CostTableError`."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CostTableError(f"not valid TOML: {exc}") from exc
    unknown = sorted(set(data) - _SECTIONS)
    if unknown:
        raise CostTableError(
            f"unknown table or key {', '.join(unknown)} (known: coach, modifiers, keys)"
        )
    merged: dict[str, Any] = (
        base.model_dump() if base is not None else {"modifiers": {}, "keys": {}}
    )
    merged["modifiers"] = dict(merged["modifiers"])
    merged["keys"] = dict(merged["keys"])
    for name, part in data.items():
        if not isinstance(part, dict):
            raise CostTableError(f"[{name}] must be a table")
        if name == "coach":
            extra = sorted(set(part) - _COACH)
            if extra:
                raise CostTableError(
                    f"[coach]: unknown key {', '.join(extra)} (known: {', '.join(sorted(_COACH))})"
                )
            for key, value in part.items():
                merged[key] = _coach_value(key, value)
        elif name == "modifiers":
            for mod, value in part.items():
                if mod.upper() not in MODIFIERS:
                    raise CostTableError(
                        f"[modifiers]: {mod!r} is not a modifier (known: {', '.join(MODIFIERS)})"
                    )
                merged["modifiers"][mod.upper()] = _cost(f"[modifiers] {mod}", value)
        else:
            for key, value in part.items():
                if not key or key != key.strip() or "-" in key[:-1]:
                    raise CostTableError(
                        f"[keys]: {key!r} is not one key (give modifiers their cost under "
                        "[modifiers])"
                    )
                merged["keys"][key.upper()] = _cost(f"[keys] {key}", value)
    missing = sorted(_COACH - set(merged))
    if missing:
        raise CostTableError(f"[coach]: {', '.join(missing)} not set")
    try:
        return CostTable.model_validate(merged, strict=True)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise CostTableError(f"[coach] {where}: {first['msg']}") from exc


def load_costs() -> Costs:
    """The shipped table, with the player's own file over it when there is one.
    Raises `CostTableError` naming the file at fault."""
    shipped = shipped_costs_path()
    text = resources.files("wowlab_core").joinpath(COSTS_FILE).read_text(encoding="utf-8")
    try:
        table = parse_costs(text)
    except CostTableError as exc:  # a packaging fault, not the player's
        raise CostTableError(f"{shipped}: {exc}") from exc
    user = user_costs_path()
    if not user.exists() and not user.is_symlink():
        return Costs(source="default", shipped=shipped, user_file=str(user), table=table)
    try:
        data = snapshot.read_regular_file(user, limit=COSTS_LIMIT)
        # utf-8-sig: an editor that saves a byte-order mark is not an error.
        mine = parse_costs(data.decode("utf-8-sig"), table)
    except (OSError, snapshot.UnsafeReadError, UnicodeDecodeError, CostTableError) as exc:
        raise CostTableError(f"{user}: {exc}") from exc
    return Costs(source="user", shipped=shipped, user_file=str(user), table=mine)


class KeyCost(_Model):
    key: str  # as the client wrote it
    cost: int


def key_cost(key: str, table: CostTable) -> KeyCost:
    """`key`'s cost: its base key's, plus each modifier's. A modifier is a
    prefix such as `CTRL-` (or `LCTRL-`); what follows the last one is the
    base key, which may itself be `-` (`CTRL--`)."""
    rest = key.upper()
    cost = 0
    while True:
        head, sep, tail = rest.partition("-")
        name = head[1:] if head[:1] in _SIDES and head[1:] in MODIFIERS else head
        if not sep or not tail or name not in MODIFIERS:
            break
        cost += table.modifiers.get(name, table.unlisted)
        rest = tail
    return KeyCost(key=key, cost=cost + table.keys.get(rest, table.unlisted))


# ─── the reports ─────────────────────────────────────────────────────────────

MatchedBy = Literal["spell id", "spell name", "none"]


class CastSpell(_Model):
    """One spell id the player cast under one name, how often, the action
    slots it was matched to and the keys that reach them. `matched_by`:
    "spell id" when a slot holds that id; "spell name" when none does and
    the spellbook names a slot's spell with the cast's name (`match_note`
    says what the bar holds); "none" when no slot was matched."""

    spell_name: str
    spell_id: int
    casts: int
    share: float  # of the player's casts
    matched_by: MatchedBy
    match_note: str | None
    keys: list[KeyCost]  # cheapest first
    slots: list[int]  # matched action slots, with or without a key


class FrequentOnHighCost(_Model):
    """A spell pressed often on high-cost keys: the casts of every id (rank)
    of it that matched the same slots, added up."""

    spell_name: str
    spell_ids: list[int]
    casts: int
    share: float
    match_note: str | None
    keys: list[KeyCost]  # every key reaching it, each high-cost
    cheapest: int


class CastWithoutBind(_Model):
    spell_name: str
    spell_id: int
    casts: int
    slots: list[int]  # matched action slots no key reaches; empty: on no slot
    detail: str


class OnUnfollowedPage(_Model):
    """A spell cast with no key the coach can follow, held only on main-bar
    pages it does not follow (`UNFOLLOWED_SLOTS`): not counted as no bind."""

    spell_name: str
    spell_id: int
    casts: int
    slots: list[int]
    detail: str


class BoundNeverCast(_Model):
    slot: int
    command: str
    keys: list[KeyCost]
    spell_id: int
    spell_name: str | None  # from the spellbook record; None when it does not name it


class CannotJudge(_Model):
    report: Report
    reason: str


class CoachResult(_Model):
    matched_by: Literal["spell id or spellbook name", "spell id"]
    spells: list[CastSpell]
    frequent_on_high_cost: list[FrequentOnHighCost]
    cast_without_bind: list[CastWithoutBind]
    on_unfollowed_page: list[OnUnfollowedPage]
    bound_never_cast: list[BoundNeverCast]
    cannot_judge: list[CannotJudge]
    caveats: list[str]  # what may make a report wrong without making it unjudgeable


def command_slot(command: str) -> int | None:
    """The action slot a binding command presses, by the default UI's
    numbering (`BAR_PAGES`), or None for any other command."""
    match = _BUTTON.fullmatch(command)
    if match is None:
        return None
    return (BAR_PAGES[match.group(1)] - 1) * 12 + int(match.group(2))


def _dropped(probes: Sequence[labaddon.ApiProbe], needs: frozenset[str]) -> list[str]:
    counted = [p for p in probes if p.api not in labaddon.LOOKED_UP]
    errors = sum(p.errors for p in counted)
    secret = sum(p.secret for p in counted)
    out: list[str] = []
    if errors or secret:
        out.append(
            f"{errors} call(s) raised an error and {secret} value(s) were dropped as secret "
            "or untestable"
        )
    missing = sorted(p.api for p in probes if p.api in needs and not p.present)
    if missing:
        out.append(f"{', '.join(missing)} not present")
    return out


def _sections(
    record: labaddon.CharDB | None, no_record: str
) -> tuple[labaddon.ActionBars, labaddon.Bindings] | str:
    """The record's action bars and bindings, or why they cannot be used."""
    if record is None:
        return no_record
    if not isinstance(record, labaddon.CharDBV3):
        return (
            f"no action-bar or bindings record (schema {record.schema_} has neither; {GET_RECORD})"
        )
    bars, binds = record.actionbars, record.bindings
    for name, section in (("action-bar", bars), ("bindings", binds)):
        if section is None:
            return f"no {name} record ({GET_RECORD})"
        if isinstance(section, labaddon.AbsentProbedSection):
            return f"no {name} record (the section is absent: {section.absent})"
    assert isinstance(bars, labaddon.ActionBars) and isinstance(binds, labaddon.Bindings)
    why = _dropped(bars.probes, _ACTIONBARS_NEEDS)
    untyped = sum(1 for s in bars.slots if s.type is None)
    if untyped:
        why.append(f"{untyped} slot(s) have no type")
    if why:
        return (
            f"the action-bar record is incomplete ({'; '.join(why)}), so a slot dropped may "
            "hold the spell"
        )
    why = _dropped(binds.probes, _BINDINGS_NEEDS)
    if why:
        return (
            f"the bindings record is incomplete ({'; '.join(why)}), so a key dropped may reach "
            "the spell"
        )
    return bars, binds


def _spellbook(
    record: labaddon.CharDB | None,
) -> tuple[dict[int, str], dict[int, str], str | None]:
    """Spell id to name and to subtext from the spellbook record, and why it is not there."""
    if not isinstance(record, labaddon.CharDBV3) or record.spellbook is None:
        return {}, {}, "no spellbook record"
    if isinstance(record.spellbook, labaddon.AbsentProbedSection):
        return {}, {}, f"no spellbook record (the section is absent: {record.spellbook.absent})"
    names: dict[int, str] = {}
    subtexts: dict[int, str] = {}
    for i in record.spellbook.list_:
        if i.spell_id is None:
            continue
        if i.spell_name is not None:
            names[i.spell_id] = i.spell_name
        if i.subtext is not None:
            subtexts[i.spell_id] = i.subtext
    return names, subtexts, None


def _cheapest(keys: list[KeyCost]) -> list[KeyCost]:
    return sorted(keys, key=lambda k: (k.cost, k.key))


def _held(spell_id: int, subtexts: Mapping[int, str]) -> str:
    sub = subtexts.get(spell_id)
    return f"{sub} ({spell_id})" if sub else str(spell_id)


def coach(
    counts: combatlog.CastCounts | None,
    record: labaddon.CharDB | None,
    costs: CostTable,
    *,
    no_record: str = f"no lab-addon record ({GET_RECORD})",
    no_log: str = "no combat log",
) -> CoachResult:
    """The three reports for one log's casts and one character's record."""
    names, subtexts, no_book = _spellbook(record)
    sections = _sections(record, no_record)
    total = counts.mine if counts is not None else 0
    cannot: list[CannotJudge] = []
    caveats: list[str] = []
    if no_book is not None:
        caveats.append(
            f"{no_book}: casts match slots by spell id only, so a spell cast at another rank's "
            "id than the one on the bar reads as cast with no bind"
        )
    if counts is not None and counts.sources > 1:
        caveats.append(
            f"the log holds casts by {counts.sources} of your characters (logging stayed on "
            "across a character change); their casts are added together and judged against "
            "this one character's record"
        )
    if counts is None:
        cannot.extend(CannotJudge(report=r, reason=no_log) for r in REPORTS)
    elif isinstance(sections, str):
        cannot.extend(CannotJudge(report=r, reason=sections) for r in REPORTS)
    elif total == 0:
        cannot.append(
            CannotJudge(
                report="bound-never-cast",
                reason="the log has none of your casts, so every bound spell would be listed",
            )
        )

    spell_slots: dict[int, int] = {}  # slot -> spell id
    keys_of: dict[int, list[KeyCost]] = {}  # slot -> keys reaching it
    command_of: dict[int, str] = {}
    left_out = 0
    keys_left_out = 0
    if not isinstance(sections, str):
        bars, binds = sections
        spell_slots = {s.slot: s.id for s in bars.slots if s.type == "spell" and s.id is not None}
        for b in binds.list_:
            slot = command_slot(b.command)
            if slot is None or not b.keys:
                continue
            command_of.setdefault(slot, b.command)
            keys_of.setdefault(slot, []).extend(key_cost(k, costs) for k in b.keys)
        left_out, keys_left_out = binds.commands_left_out, binds.keys_left_out

    spells: list[CastSpell] = []
    frequent: list[FrequentOnHighCost] = []
    unbound: list[CastWithoutBind] = []
    unfollowed: list[OnUnfollowedPage] = []
    matched_slots: set[int] = set()
    pressed: dict[tuple[str, tuple[int, ...]], list[CastSpell]] = {}
    for s in counts.spells if counts is not None else ():
        matched_by: MatchedBy = "none"
        note: str | None = None
        slots = sorted(slot for slot, i in spell_slots.items() if i == s.spell_id)
        if slots:
            matched_by = "spell id"
        else:  # no slot holds this id: fall back to the spellbook name
            folded = s.spell_name.casefold()
            slots = sorted(
                slot
                for slot, i in spell_slots.items()
                if (known := names.get(i)) is not None and known.casefold() == folded
            )
            if slots:
                matched_by = "spell name"
                held = sorted({spell_slots[slot] for slot in slots})
                note = (
                    f"matched by name: cast at {_held(s.spell_id, subtexts)}, the bar holds "
                    f"{', '.join(_held(i, subtexts) for i in held)}"
                )
        matched_slots.update(slots)
        keys = _cheapest([k for slot in slots for k in keys_of.get(slot, [])])
        share = s.casts / total if total else 0.0
        spells.append(
            CastSpell(
                spell_name=s.spell_name,
                spell_id=s.spell_id,
                casts=s.casts,
                share=share,
                matched_by=matched_by,
                match_note=note,
                keys=keys,
                slots=slots,
            )
        )
        if isinstance(sections, str):
            continue
        if not keys:
            followed = [slot for slot in slots if slot not in UNFOLLOWED_SLOTS]
            if slots and not followed:
                unfollowed.append(
                    OnUnfollowedPage(
                        spell_name=s.spell_name,
                        spell_id=s.spell_id,
                        casts=s.casts,
                        slots=slots,
                        detail=_UNFOLLOWED_DETAIL,
                    )
                )
                continue
            if followed:
                detail = (
                    "on action slot(s) no key reaches: clicked, or bound in a way the record "
                    "does not show"
                )
                paged = [slot for slot in slots if slot in UNFOLLOWED_SLOTS]
                if paged:
                    detail += (
                        f"; also on main-bar page slot(s) {', '.join(map(str, paged))}, which "
                        "the coach does not follow"
                    )
            else:
                detail = (
                    "on no action slot: cast from a macro, the spellbook or an addon's button, "
                    "with a key bound straight to it, or triggered by another spell or an item"
                )
            unbound.append(
                CastWithoutBind(
                    spell_name=s.spell_name,
                    spell_id=s.spell_id,
                    casts=s.casts,
                    slots=followed,
                    detail=detail,
                )
            )
        else:
            # Ranks of one spell that reach the same slots press the same keys:
            # their casts add up before the thresholds are applied.
            pressed.setdefault((s.spell_name.casefold(), tuple(slots)), []).append(spells[-1])

    for rows in pressed.values():
        casts = sum(r.casts for r in rows)
        share = casts / total if total else 0.0
        keys = rows[0].keys
        if (
            casts >= costs.frequent_min_casts
            and share >= costs.frequent_share
            and keys[0].cost >= costs.high_cost
        ):
            notes = [r.match_note for r in rows if r.match_note]
            frequent.append(
                FrequentOnHighCost(
                    spell_name=rows[0].spell_name,
                    spell_ids=sorted(r.spell_id for r in rows),
                    casts=casts,
                    share=share,
                    match_note="; ".join(notes) or None,
                    keys=keys,
                    cheapest=keys[0].cost,
                )
            )
    frequent.sort(key=lambda f: (-f.casts, f.spell_name.casefold(), f.spell_ids))

    if left_out >= LEFT_OUT_LIMIT:
        hidden = (
            f"the bindings record left out {left_out} binding commands that are not action "
            f"buttons ({_BAR_ADDONS}), so a key the coach cannot see may reach these spells"
        )
        if unbound:
            cannot.append(CannotJudge(report="cast-without-bind", reason=hidden))
            unbound = []
        if frequent:
            cannot.append(CannotJudge(report="frequent-on-high-cost", reason=hidden))
            frequent = []
    elif unbound and (left_out or keys_left_out):
        caveats.append(
            f"the bindings record left out {left_out} command(s) that are not plain names (a "
            f"key bound straight to a spell, macro or click; {_BAR_ADDONS}) and "
            f"{keys_left_out} key(s) that are not printable, so a spell listed as cast with no "
            "bind may have one of those"
        )

    never: list[BoundNeverCast] = []
    if not any(c.report == "bound-never-cast" for c in cannot):
        for slot in sorted(keys_of):
            spell_id = spell_slots.get(slot)
            if spell_id is None or slot in matched_slots:
                continue
            never.append(
                BoundNeverCast(
                    slot=slot,
                    command=command_of[slot],
                    keys=_cheapest(keys_of[slot]),
                    spell_id=spell_id,
                    spell_name=names.get(spell_id),
                )
            )
    spells.sort(key=lambda c: (-c.casts, c.spell_name.casefold(), c.spell_id))
    return CoachResult(
        matched_by="spell id" if no_book is not None else "spell id or spellbook name",
        spells=spells,
        frequent_on_high_cost=frequent,
        cast_without_bind=unbound,
        on_unfollowed_page=unfollowed,
        bound_never_cast=never,
        cannot_judge=cannot,
        caveats=caveats,
    )
