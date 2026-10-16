"""macrocheck: the rules `wowlab macros check` applies (M13-06, docs/LAB_PLAN.md §15.2).

Pure functions over what other modules already read: the macros of one
account's and, optionally, one character's `macros-cache.txt`
(`wtfconfig.parse_macros`, lossless, L4) and the character's lab-addon
record (`labaddon`, schema 3 for the spellbook and the action bars). Nothing
here opens a file, and nothing writes anywhere (L1).

The rules:

- `length`: a body over `LENGTH_LIMIT`. Whether the client counts characters
  or UTF-8 bytes is **[verify]**, so the check counts UTF-8 bytes (never
  fewer than characters, so it misses nothing a character count would
  flag) of the body lines joined by LF with no final break (the text the
  client is believed to show, `wtfconfig.Macro`), and says so
  (`LENGTH_UNIT`); each macro carries both counts.
- `spell-not-in-spellbook`: a spell name after `/cast`, `/castsequence`,
  `/castrandom`, `#show` or `#showtooltip` that no item of the character's
  spellbook record carries, compared without case **[verify]**.
  Conditions (`[...]`), `!`, `reset=` and the `;` and `,` separators are
  taken off; a number or `item:` is not a name. A name ending in a
  parenthesised rank (`Spell(Rank 3)`, `Spell()`) is "cannot judge": whether
  an explicit rank goes stale is **[verify]**. `/use` is not checked: it
  names items as often as spells. A name the spellbook does not carry may
  still be an item, a pet spell or a spell the record does not list; the
  finding says only what the record holds.
- `not-on-action-bar`: a macro on none of the action slots the record
  walked. A macro slot holds a macro index whose numbering against the ids in
  `macros-cache.txt` is **[verify]** (`docs/LAB_FORMATS.md`, 2026-10-16), so
  the rule judges only when the record has no macro slot at all; otherwise
  it is "cannot judge", with the least number of macros that must be on no
  bar.
- `duplicate-name`, `duplicate-body`: names or bodies that are byte for byte
  the same, across both files.
- `unbalanced-brackets`: a line whose `[` and `]` do not pair up. Chat and
  script commands are not checked (their text is free; `CHAT_COMMANDS`).
- `showtooltip-not-first`: a `#showtooltip` line that is not the body's
  first line.

Without a schema-3 spellbook or action-bar record, those two rules are
"cannot judge" and the others still run.
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
LENGTH_UNIT = "UTF-8 bytes of the body lines joined by LF, with no final line break"

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
        "/run",
        "/script",
        "/dump",
        "/print",
    }
)

_SPELL_COMMANDS = frozenset({"/cast", "/castsequence", "/castrandom", "#show", "#showtooltip"})
_LIST_COMMANDS = frozenset({"/castsequence", "/castrandom"})
_COMMAND = re.compile(r"\s*([/#][^\s\[\]]+)(.*)", re.DOTALL)
_CONDITIONS = re.compile(r"\s*(?:\[[^\[\]]*\]\s*)*")
_RESET = re.compile(r"reset=\S*\s*", re.IGNORECASE)
_RANK = re.compile(r".*\(\s*(?:[Rr]ank\s*\d+)?\s*\)")
_NOT_A_NAME = re.compile(r"\d+(?:\s+\d+)?|item:\S*", re.IGNORECASE)


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
    # which; None when it judged each macro or had no record.
    on_no_bar_at_least: int | None


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


def _spellbook(record: labaddon.CharDB | None) -> labaddon.Spellbook | str:
    if not isinstance(record, labaddon.CharDBV3) or record.spellbook is None:
        return "no spellbook record"
    if isinstance(record.spellbook, labaddon.AbsentProbedSection):
        return f"no spellbook record (the section is absent: {record.spellbook.absent})"
    return record.spellbook


def _actionbars(record: labaddon.CharDB | None) -> labaddon.ActionBars | str:
    if not isinstance(record, labaddon.CharDBV3):
        return "no spellbook record"
    if record.actionbars is None:
        return "no action-bar record"
    if isinstance(record.actionbars, labaddon.AbsentProbedSection):
        return f"no action-bar record (the section is absent: {record.actionbars.absent})"
    return record.actionbars


def _check_spells(
    macros: Sequence[SourceMacro],
    book: labaddon.Spellbook,
    findings: list[Finding],
    unjudged: list[CannotJudge],
) -> None:
    known = {item.spell_name.casefold() for item in book.list_ if item.spell_name is not None}
    for m in macros:
        for k, text in enumerate(m.macro.body_lines):
            for name in spell_names(text):
                line = _body_line(m, k)
                if _RANK.fullmatch(name):
                    unjudged.append(
                        CannotJudge(
                            rule="spell-not-in-spellbook",
                            macro=_ref(m),
                            name=m.macro.name,
                            line=line,
                            reason="names an explicit rank; whether one goes stale is [verify]",
                        )
                    )
                elif name.casefold() not in known:
                    findings.append(
                        Finding(
                            rule="spell-not-in-spellbook",
                            macro=_ref(m),
                            name=m.macro.name,
                            line=line,
                            detail=f"{name!r} is not in the spellbook record",
                        )
                    )


def _check_bars(
    macros: Sequence[SourceMacro],
    bars: labaddon.ActionBars,
    findings: list[Finding],
    unjudged: list[CannotJudge],
) -> int | None:
    slots = [s for s in bars.slots if s.type == "macro"]
    walked = f"action slots {bars.first_slot} to {bars.last_slot}"
    if not slots:
        for m in macros:
            findings.append(
                Finding(
                    rule="not-on-action-bar",
                    macro=_ref(m),
                    name=m.macro.name,
                    line=m.macro.header_index + 1,
                    detail=f"the action-bar record has no macro slot in {walked}",
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
        f"at least {at_least} of the {len(macros)} macros are on no action bar"
        if at_least
        else "so which macros are on one cannot be told"
    )
    unjudged.append(
        CannotJudge(
            rule="not-on-action-bar",
            reason=(
                f"the action-bar record has {len(slots)} macro slot(s) in {walked}, and how a "
                f"slot's macro index maps to the ids in macros-cache.txt is [verify]; {count}"
            ),
        )
    )
    return at_least


def _same_name(m: SourceMacro) -> bytes:
    return _encoded(m.macro.name)


def _same_body(m: SourceMacro) -> bytes:
    return _encoded(body_text(m.macro))


_DUPLICATES: tuple[tuple[Rule, str, Callable[[SourceMacro], bytes]], ...] = (
    ("duplicate-name", "name", _same_name),
    ("duplicate-body", "body", _same_body),
)


def _check_duplicates(macros: Sequence[SourceMacro], findings: list[Finding]) -> None:
    for rule, what, key in _DUPLICATES:
        first: dict[bytes, SourceMacro] = {}
        for m in macros:
            other = first.setdefault(key(m), m)
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
            if (parsed is None or parsed[0] not in CHAT_COMMANDS) and not _balanced(text):
                findings.append(
                    Finding(
                        rule="unbalanced-brackets",
                        macro=_ref(m),
                        name=m.macro.name,
                        line=line,
                        detail=f"body line {k + 1}: its [ and ] do not pair up",
                    )
                )
            if k > 0 and parsed is not None and parsed[0] == "#showtooltip":
                findings.append(
                    Finding(
                        rule="showtooltip-not-first",
                        macro=_ref(m),
                        name=m.macro.name,
                        line=line,
                        detail=f"#showtooltip is on body line {k + 1}, not the first",
                    )
                )


def check(macros: Sequence[SourceMacro], record: labaddon.CharDB | None) -> CheckResult:
    """Apply every rule. `record` is the character's lab-addon record, or
    None when there is none (or no character was chosen)."""
    checked: list[CheckedMacro] = []
    findings: list[Finding] = []
    unjudged: list[CannotJudge] = []
    for m in macros:
        text = body_text(m.macro)
        size = len(_encoded(text))
        checked.append(
            CheckedMacro(
                scope=m.scope,
                file=m.file,
                id=m.macro.macro_id,
                name=m.macro.name,
                line=m.macro.header_index + 1,
                bytes=size,
                characters=len(text),
                complete=m.macro.complete,
            )
        )
        if size > LENGTH_LIMIT:
            findings.append(
                Finding(
                    rule="length",
                    macro=_ref(m),
                    name=m.macro.name,
                    line=m.macro.header_index + 1,
                    detail=(
                        f"the body is {size} bytes ({len(text)} characters), over {LENGTH_LIMIT}"
                    ),
                )
            )
    book = _spellbook(record)
    if isinstance(book, str):
        unjudged.append(CannotJudge(rule="spell-not-in-spellbook", reason=book))
    else:
        _check_spells(macros, book, findings, unjudged)
    bars = _actionbars(record)
    at_least: int | None = None
    if isinstance(bars, str):
        unjudged.append(CannotJudge(rule="not-on-action-bar", reason=bars))
    else:
        at_least = _check_bars(macros, bars, findings, unjudged)
    _check_duplicates(macros, findings)
    _check_lines(macros, findings)
    order = {rule: i for i, rule in enumerate(RULES)}
    findings.sort(key=lambda f: (order[f.rule], f.macro.scope, f.macro.line, f.line))
    return CheckResult(
        macros=checked, findings=findings, cannot_judge=unjudged, on_no_bar_at_least=at_least
    )
