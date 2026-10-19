"""keymap: what each key does for one character (M14-06, docs/LAB_PLAN.md §16.2).

Pure functions over what other modules already read: the account and
character `bindings-cache.wtf` (`wtfconfig.BindingsDocument`, lossless, L4)
and three schema-3 sections of the character's lab-addon record
(`labaddon`): `bindings` (the keys `GetBindingKey` gave for each command),
`actionbars` (what each action slot held) and `spellbook` (spell names and
rank subtexts). Nothing here opens a file, and nothing writes anywhere (L1).
`bindscoach` takes its slot rule and its spellbook lookup from here.

Key to slot. A binding names a command such as `ACTIONBUTTON3`, not a spell,
and what sits in an action slot is server state (§15.2). A command reaches a
slot through `BAR_PAGES`: the default UI's action buttons, where button n of
the bar showing page p is slot (p - 1) * 12 + n (docs/GLOSSARY.md "Action
slot"). That numbering is the default UI's **[verify]**. The real record is
consistent with it and proves no more: `MULTIACTIONBAR1BUTTON1` to `3` hold
keys and slots 61 to 63 hold spells, and slots 49 and 50 fit page 5; no
`MULTIACTIONBAR5` to `7` command has had a key in a capture, so bars 5 to 7
are unconfirmed. The main bar is read at page 1 only, the page shown with no
form, stance or stealth active. Its other pages (`UNFOLLOWED_SLOTS`: page 2, and the pages forms, stances and
stealth show, and the possess, override or vehicle bars) are not followed,
nor are the pet and stance bars, an addon's buttons, or a key bound straight
to a spell, macro or click (the record leaves those commands out and counts
them).

Two sources of keys. The bindings file is what the client last wrote; the
record is what `GetBindingKey` returned at the lab-addon's last save. Neither
is taken as the truth (§16.4): each row is one key on one command, with the
sources that said so, and where the file and the record disagree (a key in a
file the record does not have on that command, or a key the record has on a
command while a file has it on another) the row says so rather than
choosing. A key the record has and no file line names is not a
disagreement: the file may hold only the changes from the client's
defaults, or may not have been rewritten since the key was set
(docs/LAB_FORMATS.md, 2026-10-16, item 8, **[verify]**), so the row is
`not in a file`. A file line `bind KEY NONE` may clear the key
**[verify]**: when the record has no command on that key, the row is
`cleared`, not a disagreement.

What a slot holds is the record's `type`, `id` and `sub_type`, kept as the
client gave them. A spell is named from the spellbook record, with its rank
subtext; a macro, item, flyout or equipment set is given by id only (a macro
slot never carries the macro's name or body, and an equipment-set slot keeps
no id, the set's id being its name **[verify]**). Without a usable action-bar record, the slot a command presses is
still given, and what it holds is "cannot place".
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import labaddon, wtfconfig

__all__ = [
    "BAR_PAGES",
    "CANNOT_PLACE",
    "GET_RECORD",
    "NOT_IN_A_FILE_NOTE",
    "UNFOLLOWED_SLOTS",
    "Agreement",
    "Holds",
    "KeyMap",
    "KeyRow",
    "Source",
    "UnreachedSlot",
    "command_slot",
    "dropped",
    "held",
    "join",
    "join_record",
    "slot_page_note",
    "spell_names",
    "spellbook",
]

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
_BAR_NAMES: Mapping[str, str] = {
    "ACTIONBUTTON": "main bar",
    "MULTIACTIONBAR3BUTTON": "right bar",
    "MULTIACTIONBAR4BUTTON": "right bar 2",
    "MULTIACTIONBAR2BUTTON": "bottom right bar",
    "MULTIACTIONBAR1BUTTON": "bottom left bar",
    "MULTIACTIONBAR5BUTTON": "bar 5",
    "MULTIACTIONBAR6BUTTON": "bar 6",
    "MULTIACTIONBAR7BUTTON": "bar 7",
}
_UNCONFIRMED_BARS = frozenset(
    {"MULTIACTIONBAR5BUTTON", "MULTIACTIONBAR6BUTTON", "MULTIACTIONBAR7BUTTON"}
)

# Main-bar pages that are not followed **[verify]**: page 2 (paging, as
# `ACTIONPAGE2` or SHIFT-MOUSEWHEEL show it) and pages 7 to 10 (the bars a
# form, stance or stealth swaps in), and pages 11 and 12 (slots 121 to 144,
# the possess, override or vehicle bars where the client has them; whether
# Forever has them is unknown). The main bar's keys reach them only while
# that page is shown.
UNFOLLOWED_SLOTS = frozenset(range(13, 25)) | frozenset(range(73, 145))

GET_RECORD = (
    "install or update the lab-addon with the client closed (wowlab addon install lab), log "
    "in on this character, then log out or /reload; schema 3 is needed"
)
CANNOT_PLACE = "cannot place: no action-bar record"
NOT_IN_A_FILE_NOTE = (
    "not in a bindings file: the file may hold only the changes from the client's defaults, "
    "or may not have been rewritten since the key was set [verify]"
)
# What else shows a main-bar page than page 1 (`UNFOLLOWED_SLOTS`).
_OTHER_PAGES = "paging, a form, stance or stealth, or a possess, override or vehicle bar [verify]"
_PET = re.compile(r"BONUSACTIONBUTTON([1-9]|10)")
_STANCE = re.compile(r"SHAPESHIFTBUTTON([1-9]|10)")
_PAGE = re.compile(r"ACTIONPAGE([1-6])")

# The record keeps a command only by a plain upper-case name and a key only
# as printable ASCII without a space (`labaddon.BindingCommand`,
# `labaddon.BindingKey`); it counts the rest.
_KEPT_COMMAND = re.compile(r"[A-Z0-9_]{1,128}")
_KEPT_KEY = re.compile(r"[\x21-\x7e]{1,64}")
_CLEARED = "NONE"

_ACTIONBARS_NEEDS = frozenset({"GetActionInfo"})
_BINDINGS_NEEDS = frozenset({"GetNumBindings", "GetBinding", "GetBindingKey"})


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ─── the slot rule and the spellbook ─────────────────────────────────────────


def command_slot(command: str) -> int | None:
    """The action slot a binding command presses, by the default UI's
    numbering (`BAR_PAGES`), or None for any other command."""
    match = _BUTTON.fullmatch(command)
    if match is None:
        return None
    return (BAR_PAGES[match.group(1)] - 1) * 12 + int(match.group(2))


def _command_page_note(command: str) -> str | None:
    match = _BUTTON.fullmatch(command)
    if match is None:
        return None
    prefix, button = match.group(1), match.group(2)
    page = BAR_PAGES[prefix]
    where = f"{_BAR_NAMES[prefix]} button {button}, page {page}"
    if prefix == "ACTIONBUTTON":
        return (
            f"{where}, the page shown with no form, stance or stealth active; {_OTHER_PAGES} "
            "shows another page, and the key then presses that page's slot (not followed)"
        )
    if prefix in _UNCONFIRMED_BARS:
        return f"{where} (unconfirmed: no capture has had a key on this bar)"
    return where


def _command_words(command: str) -> str:
    """What a binding command that presses no followed action slot does."""
    if command == _CLEARED:
        return "clears the key (NONE) [verify]"
    if match := _PET.fullmatch(command):
        return f"pet bar button {match.group(1)} (not followed) [verify]"
    if match := _STANCE.fullmatch(command):
        return f"stance bar button {match.group(1)} (not followed) [verify]"
    if match := _PAGE.fullmatch(command):
        return f"shows main-bar page {match.group(1)}"
    return "a binding command, not an action slot"


def slot_page_note(slot: int) -> str:
    """Which bar and page a slot is on, by the default UI's numbering
    (`BAR_PAGES`, `UNFOLLOWED_SLOTS`)."""
    page, button = (slot - 1) // 12 + 1, (slot - 1) % 12 + 1
    for prefix, shown in BAR_PAGES.items():
        if shown == page:
            where = f"{_BAR_NAMES[prefix]} button {button}, page {page}"
            if prefix in _UNCONFIRMED_BARS:
                return f"{where} (unconfirmed: no capture has had a key on this bar)"
            return where
    if slot in UNFOLLOWED_SLOTS:
        return (
            f"page {page}, button {button}: a main-bar page shown by {_OTHER_PAGES}, which is "
            "not followed; "
            f"the main bar's button {button} key presses it only while that page is shown"
        )
    return f"page {page}, button {button}: no bar of the default UI shows this page"


def dropped(probes: Sequence[labaddon.ApiProbe], needs: frozenset[str]) -> list[str]:
    """What a section's probes say was dropped: calls that raised, values
    dropped as secret or untestable, and the needed functions missing."""
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


def spell_names(
    section: labaddon.Spellbook | labaddon.AbsentProbedSection | None,
) -> tuple[dict[int, str], dict[int, str], str | None]:
    """Spell id to name and to subtext from a spellbook section, and why it is not there."""
    if section is None:
        return {}, {}, "no spellbook record"
    if isinstance(section, labaddon.AbsentProbedSection):
        return {}, {}, f"no spellbook record (the section is absent: {section.absent})"
    names: dict[int, str] = {}
    subtexts: dict[int, str] = {}
    for i in section.list_:
        if i.spell_id is None:
            continue
        if i.spell_name is not None:
            names[i.spell_id] = i.spell_name
        if i.subtext is not None:
            subtexts[i.spell_id] = i.subtext
    return names, subtexts, None


def spellbook(
    record: labaddon.CharDB | None,
) -> tuple[dict[int, str], dict[int, str], str | None]:
    """`spell_names` of a record's spellbook; a record before schema 3 has none."""
    if not isinstance(record, labaddon.CharDBV3):
        return {}, {}, "no spellbook record"
    return spell_names(record.spellbook)


def held(spell_id: int, subtexts: Mapping[int, str]) -> str:
    """A spell id with its rank subtext before it, when the spellbook gives one."""
    sub = subtexts.get(spell_id)
    return f"{sub} ({spell_id})" if sub else str(spell_id)


# ─── the join ────────────────────────────────────────────────────────────────

Source = Literal["account file", "character file", "record"]
_SOURCES: tuple[Source, ...] = ("account file", "character file", "record")
# Whether the files and the record agree on one key on one command: both have
# it; only a file has it, or only the record while a file has the key on
# another command (a disagreement); only the record, with no file line for the
# key (not a disagreement); or a file line `bind KEY NONE` and the record has
# no command on that key (cleared).
Agreement = Literal["both", "file only", "record only", "not in a file", "cleared"]


class Holds(_Model):
    """What an action slot held, as the record gives it. `name` and `subtext`
    come from the spellbook record, for a spell only."""

    type: str | None  # the client's word: spell, macro, item, flyout, ...
    id: int | None  # None when the client gave no number (an equipment set)
    sub_type: str | None
    name: str | None
    subtext: str | None


class KeyRow(_Model):
    """One key on one binding command. `slot` is the action slot the
    command presses on the page `BAR_PAGES` gives its bar (None: not an
    action button);
    `holds` is what the record says that slot held (None: an empty slot, no
    action-bar record, or not an action button); `does` says it in words.
    `agreement` is None when there is no bindings record to compare with."""

    key: str
    command: str
    slot: int | None
    page_note: str | None
    holds: Holds | None
    does: str
    sources: list[Source]
    agreement: Agreement | None
    disagrees: bool  # agreement is "file only" or "record only"; not "not in a file"
    note: str | None


class UnreachedSlot(_Model):
    """A filled slot no key in any source reaches on the page `BAR_PAGES`
    gives its bar."""

    slot: int
    page_note: str
    unfollowed_page: bool  # on a main-bar page that is not followed (`UNFOLLOWED_SLOTS`)
    holds: Holds
    does: str


class KeyMap(_Model):
    keys: list[KeyRow]
    unreached_slots: list[UnreachedSlot]  # empty without an action-bar record
    commands_left_out: int | None  # counts from the bindings record; None without one
    keys_left_out: int | None
    cannot_place: str | None  # why there is no action-bar record to place keys with
    cannot_compare: str | None  # why there is no bindings record to compare the files with
    notes: list[str]


def _holds(slot: labaddon.ActionSlot, names: Mapping[int, str], subs: Mapping[int, str]) -> Holds:
    spell = slot.type == "spell" and slot.id is not None
    return Holds(
        type=slot.type,
        id=slot.id,
        sub_type=slot.sub_type,
        name=names.get(slot.id) if spell and slot.id is not None else None,
        subtext=subs.get(slot.id) if spell and slot.id is not None else None,
    )


def _does(holds: Holds) -> str:
    """What pressing a slot that holds this does, in words."""
    kind, ident = holds.type, holds.id
    number = f" {ident}" if ident is not None else ""
    if kind == "spell":
        if holds.name is None:
            return f"casts spell{number} (the spellbook record does not name it)"
        rank = f"{holds.subtext}, " if holds.subtext else ""
        return f"casts {holds.name} ({rank}spell{number})"
    if kind == "macro":
        if holds.sub_type:
            return (
                f"runs a macro that shows {holds.sub_type}{number} now [verify]; the record "
                "keeps no macro name or body"
            )
        return (
            f"runs macro{number} (a macro index [verify]); the record keeps no macro name or body"
        )
    if kind == "item":
        return f"uses item{number}"
    if kind == "flyout":
        return f"opens flyout{number}"
    if kind == "equipmentset":
        return "equips an equipment set (the record keeps no id: a set's id is its name [verify])"
    if kind is None:
        return f"holds something{number} the record gave no type for"
    sub = f" ({holds.sub_type})" if holds.sub_type else ""
    return f"holds {kind}{number}{sub}"


def _usable_bars(
    section: labaddon.ActionBars | labaddon.AbsentProbedSection | None, no_record: str
) -> tuple[labaddon.ActionBars | None, str | None, str | None]:
    """The action bars, why they cannot place keys, and a note when they are incomplete."""
    if section is None:
        return None, no_record, None
    if isinstance(section, labaddon.AbsentProbedSection):
        return None, f"the action-bar section is absent: {section.absent}", None
    why = dropped(section.probes, _ACTIONBARS_NEEDS)
    untyped = sum(1 for s in section.slots if s.type is None)
    if untyped:
        why.append(f"{untyped} slot(s) have no type")
    note = (
        f"The action-bar record is incomplete ({'; '.join(why)}): a slot shown empty may "
        "have been filled."
        if why
        else None
    )
    return section, None, note


def _usable_bindings(
    section: labaddon.Bindings | labaddon.AbsentProbedSection | None, no_record: str
) -> tuple[labaddon.Bindings | None, str | None, str | None]:
    if section is None:
        return None, no_record, None
    if isinstance(section, labaddon.AbsentProbedSection):
        return None, f"the bindings section is absent: {section.absent}", None
    why = dropped(section.probes, _BINDINGS_NEEDS)
    note = (
        f"The bindings record is incomplete ({'; '.join(why)}): a key shown only in a file "
        "may be one the record dropped."
        if why
        else None
    )
    return section, None, note


def _file_note(
    key: str, command: str, record_keys: Mapping[str, list[str]], who: str = "the record"
) -> str:
    if not _KEPT_COMMAND.fullmatch(command):
        return (
            f"{who} leaves out commands that are not a plain upper-case name and only counts "
            "them (commands_left_out)"
        )
    if not _KEPT_KEY.fullmatch(key):
        return (
            f"{who} leaves out keys that are not printable ASCII without a space and only "
            "counts them (keys_left_out)"
        )
    others = record_keys.get(key)
    if others:
        return f"{who} has {key} on {', '.join(others)}"
    return f"{who} has no command on {key}"


def join(
    *,
    account: wtfconfig.BindingsDocument | None,
    character: wtfconfig.BindingsDocument | None,
    bindings: labaddon.Bindings | labaddon.AbsentProbedSection | None,
    actionbars: labaddon.ActionBars | labaddon.AbsentProbedSection | None,
    spellbook: labaddon.Spellbook | labaddon.AbsentProbedSection | None,
    no_bars: str = f"no action-bar record ({GET_RECORD})",
    no_bindings: str = f"no bindings record ({GET_RECORD})",
) -> KeyMap:
    """Every key of the two files and of the record's bindings section,
    joined to the action slots and the spellbook. `no_bars` and
    `no_bindings` say why a section given as None is missing."""
    bars, cannot_place, bars_note = _usable_bars(actionbars, no_bars)
    binds, cannot_compare, binds_note = _usable_bindings(bindings, no_bindings)
    names, subtexts, no_book = spell_names(spellbook)

    filled = {s.slot: s for s in bars.slots} if bars is not None else {}
    # (key, command) -> the sources that have it, in `_SOURCES` order
    pairs: dict[tuple[str, str], list[Source]] = {}
    file_keys: dict[str, list[tuple[str, Source]]] = {}
    files: tuple[tuple[Source, wtfconfig.BindingsDocument | None], ...] = (
        ("account file", account),
        ("character file", character),
    )
    for scope, doc in files:
        for b in doc.bindings if doc is not None else ():
            have = pairs.setdefault((b.key, b.action), [])
            if scope not in have:
                have.append(scope)
            file_keys.setdefault(b.key, []).append((b.action, scope))
    record_keys: dict[str, list[str]] = {}
    for rb in binds.list_ if binds is not None else ():
        for k in rb.keys:
            have = pairs.setdefault((k, rb.command), [])
            if "record" not in have:
                have.append("record")
                record_keys.setdefault(k, []).append(rb.command)

    rows: list[KeyRow] = []
    for (key, command), sources in pairs.items():
        slot = command_slot(command)
        holds: Holds | None = None
        if slot is None:
            does = _command_words(command)
        elif bars is None:
            does = CANNOT_PLACE
        elif slot in filled:
            holds = _holds(filled[slot], names, subtexts)
            does = _does(holds)
        elif bars.first_slot <= slot <= bars.last_slot:
            does = "empty slot (nothing recorded in it)"
        else:
            does = f"not walked by the record (slots {bars.first_slot} to {bars.last_slot})"
        agreement: Agreement | None = None
        note: str | None = None
        if binds is not None:
            in_file = any(s != "record" for s in sources)
            if in_file and "record" in sources:
                agreement = "both"
            elif in_file:
                if command == _CLEARED and key not in record_keys:
                    agreement = "cleared"
                    note = "the record has no command on this key either"
                else:
                    agreement = "file only"
                    note = _file_note(key, command, record_keys)
            else:
                others = [(c, s) for c, s in file_keys.get(key, []) if c != command]
                if others:
                    agreement = "record only"
                    note = "; ".join(f"the {s} has {key} on {c}" for c, s in others)
                else:
                    agreement = "not in a file"
                    note = NOT_IN_A_FILE_NOTE
        rows.append(
            KeyRow(
                key=key,
                command=command,
                slot=slot,
                page_note=_command_page_note(command),
                holds=holds,
                does=does,
                sources=[s for s in _SOURCES if s in sources],
                agreement=agreement,
                disagrees=agreement in ("file only", "record only"),
                note=note,
            )
        )
    rows.sort(key=lambda r: (r.slot is None, r.slot or 0, r.command, r.key))

    reached = {r.slot for r in rows if r.slot is not None}
    unreached = [
        UnreachedSlot(
            slot=s.slot,
            page_note=slot_page_note(s.slot),
            unfollowed_page=s.slot in UNFOLLOWED_SLOTS,
            holds=(h := _holds(s, names, subtexts)),
            does=_does(h),
        )
        for s in sorted(filled.values(), key=lambda s: s.slot)
        if s.slot not in reached
    ]

    notes = [
        "A key reaches an action slot through the default UI's action buttons: button n of the "
        "bar showing page p is slot (p - 1) x 12 + n [verify]. The main bar is read at page 1 "
        "only, the page shown with no form, stance or stealth active; the pages shown by "
        f"{_OTHER_PAGES}, the pet and stance bars and an addon's buttons are not followed.",
    ]
    if cannot_compare is None:
        notes.append(
            "The bindings files are what the client last wrote; the record is what "
            "GetBindingKey returned at the lab-addon's last save. Neither is taken as the "
            "truth: each row names the sources that have that key on that command, and says "
            "where they disagree."
        )
    else:
        notes.append(
            "Without a bindings record only the files' lines are listed; a file may leave out "
            "keys at the client's defaults [verify], so keys not listed may still be bound."
        )
    if bars_note is not None:
        notes.append(bars_note)
    if binds_note is not None:
        notes.append(binds_note)
    if no_book is not None and bars is not None:
        notes.append(f"{no_book}: spells on the bars are given by id only.")
    return KeyMap(
        keys=rows,
        unreached_slots=unreached,
        commands_left_out=binds.commands_left_out if binds is not None else None,
        keys_left_out=binds.keys_left_out if binds is not None else None,
        cannot_place=cannot_place,
        cannot_compare=cannot_compare,
        notes=notes,
    )


def join_record(
    *,
    account: wtfconfig.BindingsDocument | None,
    character: wtfconfig.BindingsDocument | None,
    record: labaddon.CharDB | None,
    no_record: str = f"no lab-addon record ({GET_RECORD})",
) -> KeyMap:
    """`join` with the three sections taken from a character's record.
    `no_record` says why there is none; a record before schema 3 has none
    of the three, and a schema-3 record may lack any of them."""
    if not isinstance(record, labaddon.CharDBV3):
        if record is None:
            no_bars = no_bindings = no_record
        else:
            no_bars = f"no action-bar record (schema {record.schema_} has none; {GET_RECORD})"
            no_bindings = f"no bindings record (schema {record.schema_} has none; {GET_RECORD})"
        return join(
            account=account,
            character=character,
            bindings=None,
            actionbars=None,
            spellbook=None,
            no_bars=no_bars,
            no_bindings=no_bindings,
        )
    return join(
        account=account,
        character=character,
        bindings=record.bindings,
        actionbars=record.actionbars,
        spellbook=record.spellbook,
    )
