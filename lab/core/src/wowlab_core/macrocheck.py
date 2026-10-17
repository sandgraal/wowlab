"""macrocheck: the rules `wowlab macros check` applies (M13-06, docs/LAB_PLAN.md §15.2).

Pure functions over what other modules already read: the macros of one
account's and, optionally, one character's `macros-cache.txt`
(`wtfconfig.parse_macros`, lossless, L4) and the character's lab-addon
record (`labaddon`, schema 3 for the spellbook and the action bars). Nothing
here opens a file, and nothing writes anywhere (L1).

The rules:

- `length`: a body over `LENGTH_LIMIT`. Whether the client counts characters
  or UTF-8 bytes is **[verify]**. The body is the body lines joined by LF
  with no final break (the text the client is believed to show,
  `wtfconfig.Macro`); each macro carries both counts. Over 255 characters is
  a finding; over 255 bytes but not over 255 characters is "cannot judge"
  (`LENGTH_UNIT` says what was counted).
- `spell-not-in-spellbook`: a spell name after `/cast`, `/castsequence`,
  `/castrandom`, `#show` or `#showtooltip` that no learned item of the
  character's spellbook record carries, compared without case **[verify]**.
  Conditions (`[...]`), `!`, `reset=` and the `;` and `,` separators are
  taken off; a number or `item:` is not a name. A name only on a
  not-yet-learned item (the record's `item_types["FutureSpell"]`) is a
  finding that says so. A name ending in a parenthesised suffix
  (`Spell(Rank 3)`, `Spell(Rang 3)`, `Spell()`) is an explicit rank and
  "cannot judge": whether one goes stale is **[verify]**. `/use` is not
  checked: it names items as often as spells. A name the spellbook does not
  carry may still be an item, a pet, mount or toy spell, or a spell learned
  later; the finding says so.
- `not-on-action-bar`: a macro on none of the action slots the record
  walked, for the one character whose record it is. A macro slot holds a
  macro index whose numbering against the ids in `macros-cache.txt` is
  **[verify]** (`docs/LAB_FORMATS.md`, 2026-10-16), so the rule judges only
  when the record has no macro slot at all; otherwise it is "cannot judge",
  with the least number of macros that must be on no bar.
- `duplicate-name`, `duplicate-body`: names or bodies that are byte for byte
  the same, across both files. A blank name (empty or spaces only) is a
  choice, not a clash: it is left out and counted (`blank_names`).
- `unbalanced-brackets`: a command line whose `[` and `]` do not pair up.
  Chat and script commands (`CHAT_COMMANDS`, English names only) and lines
  that are not commands are free text and not checked.
- `showtooltip-not-first`: a `#showtooltip` line, or a `#show` line (the same
  rule assumed, **[verify]**), that is not the body's first line.

A record section whose own probes say values were dropped (a call raised, a
value was secret or untestable, a needed function was missing) or that holds
items or slots with the value missing makes its rule "cannot judge" for
every macro, with the counts: a missing name or slot may be one of those
dropped. Without a schema-3 spellbook or action-bar record, those two rules
are "cannot judge" and the others still run.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import labaddon
from wowlab_core.wtfconfig import Macro

__all__ = [
    "CHAT_COMMANDS",
    "LENGTH_LIMIT",
    "LENGTH_UNIT",
    "NO_RECORD",
    "RULES",
    "CannotJudge",
    "CheckResult",
    "CheckedMacro",
    "Finding",
    "MacroRef",
    "MacroScope",
    "Rule",
    "SourceMacro",
    "body_text",
    "check",
    "spell_names",
]

LENGTH_LIMIT = 255
LENGTH_UNIT = (
    "characters and UTF-8 bytes of the body lines joined by LF, with no final line break; "
    "over 255 characters is a finding, over 255 bytes only is cannot judge"
)

Rule = Literal[
    "length",
    "spell-not-in-spellbook",
    "not-on-action-bar",
    "duplicate-name",
    "duplicate-body",
    "unbalanced-brackets",
    "showtooltip-not-first",
]
RULES: tuple[Rule, ...] = (
    "length",
    "spell-not-in-spellbook",
    "not-on-action-bar",
    "duplicate-name",
    "duplicate-body",
    "unbalanced-brackets",
    "showtooltip-not-first",
)
MacroScope = Literal["account", "character"]

# Commands whose text is free (chat, emotes, scripts): their brackets mean
# nothing to the client, so `unbalanced-brackets` does not read them. English
# names only; a localized alias is checked like any other command.
CHAT_COMMANDS = frozenset(
    {
        "/say",
        "/s",
        "/yell",
        "/y",
        "/emote",
        "/e",
        "/me",
        "/party",
        "/p",
        "/raid",
        "/ra",
        "/rw",
        "/raidwarning",
        "/guild",
        "/g",
        "/officer",
        "/o",
        "/whisper",
        "/w",
        "/tell",
        "/t",
        "/reply",
        "/r",
        "/instance",
        "/i",
        "/bg",
        "/battleground",
        "/channel",
        "/csay",
        "/bn",
        "/afk",
        "/dnd",
        "/run",
        "/script",
        "/dump",
        "/print",
        *(f"/{n}" for n in range(1, 11)),
    }
)

_SPELL_COMMANDS = frozenset({"/cast", "/castsequence", "/castrandom", "#show", "#showtooltip"})
_LIST_COMMANDS = frozenset({"/castsequence", "/castrandom"})
# `#showtooltip` must be the first line; `#show` is assumed to follow the
# same rule [verify].
_FIRST_LINE = {"#showtooltip": "#showtooltip", "#show": "#show (the same rule assumed, [verify])"}
_COMMAND = re.compile(r"\s*([/#][^\s\[\]]+)(.*)", re.DOTALL)
_CONDITIONS = re.compile(r"\s*(?:\[[^\[\]]*\]\s*)*")
_RESET = re.compile(r"reset=\S*\s*", re.IGNORECASE)
# A trailing parenthesised suffix: a rank in any client language (`Rank 3`,
# `Rang 3`), or empty.
_SUFFIX = re.compile(r"(.*)\(([^()]*)\)\s*", re.DOTALL)
_NOT_A_NAME = re.compile(r"\d+(?:\s+\d+)?|item:\S*", re.IGNORECASE)

_GET_RECORD = (
    "enable the lab-addon (wowlab addon install lab), log in on this character, then log "
    "out or /reload; schema 3 is needed"
)
NO_RECORD = f"no spellbook record ({_GET_RECORD})"
_MAY_BE = (
    "it may be an item, a pet, mount or toy spell, or a spell learned after the record was saved"
)
_SPELLBOOK_NEEDS = frozenset(
    {"C_SpellBook.GetSpellBookItemInfo", "C_SpellBook.GetSpellBookItemName"}
)
_ACTIONBARS_NEEDS = frozenset({"GetActionInfo"})


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceMacro(_Model):
    """One macro as read, with where it came from."""

    scope: MacroScope
    file: str  # relative to the flavor folder
    macro: Macro


class MacroRef(_Model):
    """Which macro: its file's scope, its id as written and its `VER` line."""

    scope: MacroScope
    id: str
    line: int  # 1-based line of the VER header in its file


class CheckedMacro(_Model):
    """One macro the rules read, with both length counts."""

    scope: MacroScope
    file: str
    id: str
    name: str
    line: int
    bytes: int
    characters: int
    complete: bool  # False: the file ends before this macro's END


class Finding(_Model):
    """One rule broken by one macro. `line` is the 1-based line in the macro's
    file the finding is about (a body line, or the header); `same_as` is the
    first macro of a duplicate pair."""

    rule: Rule
    macro: MacroRef
    name: str
    line: int
    detail: str
    same_as: MacroRef | None = None


class CannotJudge(_Model):
    """A rule the check could not apply: to every macro (`macro` is None) or
    to one line of one macro."""

    rule: Rule
    macro: MacroRef | None = None
    name: str | None = None
    line: int | None = None
    reason: str


class CheckResult(_Model):
    macros: list[CheckedMacro]
    findings: list[Finding]
    cannot_judge: list[CannotJudge]
    # The least number of macros on no action bar when the rule cannot say
    # which; None when it judged each macro or could not judge at all.
    on_no_bar_at_least: int | None
    # Macros whose name is empty or spaces only: left out of duplicate-name.
    blank_names: int


def body_text(macro: Macro) -> str:
    """The text the client is believed to show **[verify]**."""
    return "\n".join(macro.body_lines)


def _encoded(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


def _ref(m: SourceMacro) -> MacroRef:
    return MacroRef(scope=m.scope, id=m.macro.macro_id, line=m.macro.header_index + 1)


def _body_line(m: SourceMacro, k: int) -> int:
    return m.macro.header_index + 2 + k


def _command(line: str) -> tuple[str, str] | None:
    match = _COMMAND.fullmatch(line)
    if match is None:
        return None
    return match.group(1).casefold(), match.group(2)


def _after(pattern: re.Pattern[str], text: str) -> str:
    """`text` without what `pattern` matches at its start, if anything."""
    match = pattern.match(text)
    return text if match is None else text[match.end() :]


def spell_names(line: str) -> list[str]:
    """The spell names a body line names, as written (conditions, `!` and
    `reset=` taken off); empty for a line that names none or whose
    brackets do not pair up (that is its own rule)."""
    parsed = _command(line)
    if parsed is None or parsed[0] not in _SPELL_COMMANDS:
        return []
    command, rest = parsed
    if not _balanced(rest):
        return []
    names: list[str] = []
    for clause in rest.split(";"):
        text = _after(_CONDITIONS, clause)
        listed = command in _LIST_COMMANDS
        parts = _after(_RESET, text.lstrip()).split(",") if listed else [text]
        for part in parts:
            name = part.strip().lstrip("!").strip()
            if name and _NOT_A_NAME.fullmatch(name) is None:
                names.append(name)
    return names


def _balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch == "[":
            if depth:
                return False
            depth = 1
        elif ch == "]":
            if not depth:
                return False
            depth = 0
    return depth == 0


# ─── the record ──────────────────────────────────────────────────────────────


def _schema_words(record: labaddon.CharDB) -> str:
    return (
        f"no spellbook record (schema {record.schema_} has no spellbook or action-bar record; "
        f"{_GET_RECORD})"
    )


def _dropped(probes: Sequence[labaddon.ApiProbe], needs: frozenset[str]) -> list[str]:
    """What a section's own probes say it may be missing: calls that raised,
    values dropped as secret or untestable, a needed function not there."""
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


def _spellbook(record: labaddon.CharDB | None, no_record: str) -> labaddon.Spellbook | str:
    if record is None:
        return no_record
    if not isinstance(record, labaddon.CharDBV3):
        return _schema_words(record)
    if record.spellbook is None:
        return NO_RECORD
    if isinstance(record.spellbook, labaddon.AbsentProbedSection):
        return f"no spellbook record (the section is absent: {record.spellbook.absent})"
    book = record.spellbook
    flyout = (book.item_types or {}).get("Flyout")
    why = _dropped(book.probes, _SPELLBOOK_NEEDS)
    unnamed = sum(
        1 for i in book.list_ if i.spell_name is None and (flyout is None or i.type != flyout)
    )
    if unnamed:
        why.append(f"{unnamed} item(s) have no name")
    if why:
        return (
            f"the spellbook record is incomplete ({'; '.join(why)}), so a name it lacks may be "
            "one of those dropped"
        )
    return book


def _actionbars(record: labaddon.CharDB | None, no_record: str) -> labaddon.ActionBars | str:
    if record is None:
        return no_record
    if not isinstance(record, labaddon.CharDBV3):
        return _schema_words(record)
    if record.actionbars is None:
        return f"no action-bar record ({_GET_RECORD})"
    if isinstance(record.actionbars, labaddon.AbsentProbedSection):
        return f"no action-bar record (the section is absent: {record.actionbars.absent})"
    bars = record.actionbars
    why = _dropped(bars.probes, _ACTIONBARS_NEEDS)
    untyped = sum(1 for s in bars.slots if s.type is None)
    if untyped:
        why.append(f"{untyped} slot(s) have no type")
    if why:
        return (
            f"the action-bar record is incomplete ({'; '.join(why)}), so a macro slot may be "
            "one of those dropped"
        )
    return bars


# ─── the rules ───────────────────────────────────────────────────────────────


def _rank_words(name: str, book: labaddon.Spellbook) -> str:
    match = _SUFFIX.fullmatch(name)
    assert match is not None
    base, rank = match.group(1).strip(), match.group(2).strip()
    pairs = {
        (i.spell_name.casefold(), (i.subtext or "").casefold())
        for i in book.list_
        if i.spell_name is not None
    }
    held = "has" if (base.casefold(), rank.casefold()) in pairs else "does not have"
    return (
        "names an explicit rank; whether one goes stale is [verify]; the spellbook record "
        f"{held} {base!r} with subtext {rank!r}"
    )


def _check_spells(
    macros: Sequence[SourceMacro],
    book: labaddon.Spellbook,
    findings: list[Finding],
    unjudged: list[CannotJudge],
) -> None:
    future = (book.item_types or {}).get("FutureSpell")
    learned: set[str] = set()
    unlearned: set[str] = set()
    for item in book.list_:
        if item.spell_name is not None:
            is_future = future is not None and item.type == future
            (unlearned if is_future else learned).add(item.spell_name.casefold())
    for m in macros:
        for k, text in enumerate(m.macro.body_lines):
            for name in spell_names(text):
                line = _body_line(m, k)
                folded = name.casefold()
                if _SUFFIX.fullmatch(name):
                    unjudged.append(
                        CannotJudge(
                            rule="spell-not-in-spellbook",
                            macro=_ref(m),
                            name=m.macro.name,
                            line=line,
                            reason=_rank_words(name, book),
                        )
                    )
                    continue
                if folded in learned:
                    continue
                detail = (
                    f"{name!r} is in the spellbook record as not yet learned"
                    if folded in unlearned
                    else f"{name!r} is not in the spellbook record ({_MAY_BE})"
                )
                findings.append(
                    Finding(
                        rule="spell-not-in-spellbook",
                        macro=_ref(m),
                        name=m.macro.name,
                        line=line,
                        detail=detail,
                    )
                )


def _check_bars(
    macros: Sequence[SourceMacro],
    bars: labaddon.ActionBars,
    whose: str,
    findings: list[Finding],
    unjudged: list[CannotJudge],
) -> int | None:
    slots = [s for s in bars.slots if s.type == "macro"]
    walked = f"{whose} action bars (slots {bars.first_slot} to {bars.last_slot})"
    if not slots:
        for m in macros:
            findings.append(
                Finding(
                    rule="not-on-action-bar",
                    macro=_ref(m),
                    name=m.macro.name,
                    line=m.macro.header_index + 1,
                    detail=f"on none of {walked}: the record has no macro slot there",
                )
            )
        return None
    # A macro slot with no sub_type holds a macro index; one with a sub_type
    # holds the spell or item the macro shows [verify]. Distinct macros on
    # the bars are at most the distinct indices plus the other slots.
    bound = len({s.id for s in slots if s.sub_type is None}) + sum(
        1 for s in slots if s.sub_type is not None
    )
    at_least = max(0, len(macros) - bound)
    count = (
        f"at least {at_least} of the {len(macros)} macros are on none of {whose} action bars"
        if at_least
        else "so which macros are on one cannot be told"
    )
    unjudged.append(
        CannotJudge(
            rule="not-on-action-bar",
            reason=(
                f"the record has {len(slots)} macro slot(s) on {walked}, and how a slot's "
                f"macro index maps to the ids in macros-cache.txt is [verify]; {count}"
            ),
        )
    )
    return at_least


def _same_name(m: SourceMacro) -> bytes | None:
    # A blank name is a choice (the button shows no label), not a clash.
    return _encoded(m.macro.name) if m.macro.name.strip() else None


def _same_body(m: SourceMacro) -> bytes | None:
    return _encoded(body_text(m.macro))


_DUPLICATES: tuple[tuple[Rule, str, Callable[[SourceMacro], bytes | None]], ...] = (
    ("duplicate-name", "name", _same_name),
    ("duplicate-body", "body", _same_body),
)


def _check_duplicates(macros: Sequence[SourceMacro], findings: list[Finding]) -> None:
    for rule, what, key in _DUPLICATES:
        first: dict[bytes, SourceMacro] = {}
        for m in macros:
            k = key(m)
            if k is None:
                continue
            other = first.setdefault(k, m)
            if other is m:
                continue
            findings.append(
                Finding(
                    rule=rule,
                    macro=_ref(m),
                    name=m.macro.name,
                    line=m.macro.header_index + 1,
                    detail=(
                        f"the same {what} as {other.scope} macro {other.macro.macro_id} "
                        f"(line {other.macro.header_index + 1} of {other.file})"
                    ),
                    same_as=_ref(other),
                )
            )


def _check_lines(macros: Sequence[SourceMacro], findings: list[Finding]) -> None:
    for m in macros:
        for k, text in enumerate(m.macro.body_lines):
            parsed = _command(text)
            line = _body_line(m, k)
            # A line that is not a command is said in chat, so it is free
            # text, as a chat command's is.
            if parsed is not None and parsed[0] not in CHAT_COMMANDS and not _balanced(text):
                findings.append(
                    Finding(
                        rule="unbalanced-brackets",
                        macro=_ref(m),
                        name=m.macro.name,
                        line=line,
                        detail=(
                            f"body line {k + 1}: its [ and ] do not pair up (a chat command "
                            "with a localized name is checked as a command)"
                        ),
                    )
                )
            if k > 0 and parsed is not None and parsed[0] in _FIRST_LINE:
                findings.append(
                    Finding(
                        rule="showtooltip-not-first",
                        macro=_ref(m),
                        name=m.macro.name,
                        line=line,
                        detail=f"{_FIRST_LINE[parsed[0]]} is on body line {k + 1}, not the first",
                    )
                )


def _check_length(
    m: SourceMacro, findings: list[Finding], unjudged: list[CannotJudge]
) -> CheckedMacro:
    text = body_text(m.macro)
    size, chars = len(_encoded(text)), len(text)
    line = m.macro.header_index + 1
    if chars > LENGTH_LIMIT:
        findings.append(
            Finding(
                rule="length",
                macro=_ref(m),
                name=m.macro.name,
                line=line,
                detail=f"the body is {size} bytes ({chars} characters), over {LENGTH_LIMIT}",
            )
        )
    elif size > LENGTH_LIMIT:
        unjudged.append(
            CannotJudge(
                rule="length",
                macro=_ref(m),
                name=m.macro.name,
                line=line,
                reason=(
                    f"{size} bytes, {chars} characters: over {LENGTH_LIMIT} only if the client "
                    "counts bytes [verify]"
                ),
            )
        )
    return CheckedMacro(
        scope=m.scope,
        file=m.file,
        id=m.macro.macro_id,
        name=m.macro.name,
        line=line,
        bytes=size,
        characters=chars,
        complete=m.macro.complete,
    )


def check(
    macros: Sequence[SourceMacro],
    record: labaddon.CharDB | None,
    *,
    character: str | None = None,
    no_record: str = NO_RECORD,
) -> CheckResult:
    """Apply every rule. `record` is the character's lab-addon record, or
    None; `no_record` is then why, the two lab-addon rules' reason.
    `character` names whose action bars the record holds."""
    findings: list[Finding] = []
    unjudged: list[CannotJudge] = []
    checked = [_check_length(m, findings, unjudged) for m in macros]
    book = _spellbook(record, no_record)
    if isinstance(book, str):
        unjudged.append(CannotJudge(rule="spell-not-in-spellbook", reason=book))
    else:
        _check_spells(macros, book, findings, unjudged)
    bars = _actionbars(record, no_record)
    at_least: int | None = None
    if isinstance(bars, str):
        unjudged.append(CannotJudge(rule="not-on-action-bar", reason=bars))
    else:
        whose = f"{character}'s" if character else "the character's"
        at_least = _check_bars(macros, bars, whose, findings, unjudged)
    _check_duplicates(macros, findings)
    _check_lines(macros, findings)
    order = {rule: i for i, rule in enumerate(RULES)}
    findings.sort(key=lambda f: (order[f.rule], f.macro.scope, f.macro.line, f.line))
    unjudged.sort(key=lambda c: order[c.rule])
    return CheckResult(
        macros=checked,
        findings=findings,
        cannot_judge=unjudged,
        on_no_bar_at_least=at_least,
        blank_names=sum(1 for m in macros if not m.macro.name.strip()),
    )
