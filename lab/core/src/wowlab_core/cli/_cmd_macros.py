"""`wowlab macros list` and `macros check` (M16-111)."""

from collections.abc import Sequence

from wowlab_core import (
    labaddon,
    layout,
    luadata,
    macrocheck,
    snapshot,
    wtfconfig,
)
from wowlab_core.cli._cmd_cvar import (
    _MACRO_SYNC,
    _no_file,
)
from wowlab_core.cli._common import (
    AccountOpt,
    CharacterOpt,
    CliError,
    FlavorOpt,
    JsonOpt,
    NotesOpt,
    RootOpt,
    Scope,
    _account_words,
    _chosen_lab_file,
    _emit,
    _handled,
    _is_lab_file,
    _listed,
    _open,
    _Out,
    _player_copy,
    _plural,
    _say,
    _say_notes,
    _say_prose,
    _select_account,
    _select_character,
    _Target,
    _wtf_file,
    macros_app,
)


class MacroItem(_Out):
    id: str
    name: str
    icon: str
    body_lines: list[str]
    complete: bool


class MacrosReport(_Out):
    """`wowlab macros list --json`."""

    file: str | None
    scope: Scope
    account: str | None
    character: str | None
    macros: list[MacroItem]
    unknown_lines: int
    notes: list[str]  # the caveats the text output prints


@macros_app.command("list", short_help="Account macros, or one character's.")
@_handled
def macros_list(
    account: AccountOpt = None,
    character: CharacterOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Account macros, or one character's with --character.

    \f
    JSON: MacrosReport."""
    _, _, lay = _open(root, flavor)
    scope: Scope = "character" if character is not None else "account"
    target, found = _wtf_file(lay, "macros-cache.txt", scope, account, character)
    doc = _listed(wtfconfig.read_macros, lay, found.path) if found else None
    report = MacrosReport(
        file=found.path if found else None,
        scope=target.scope,
        account=target.account,
        character=target.character,
        macros=[
            MacroItem(
                id=m.macro_id,
                name=m.name,
                icon=m.icon,
                body_lines=list(m.body_lines),
                complete=m.complete,
            )
            for m in doc.macros
        ]
        if doc
        else [],
        unknown_lines=len(doc.unknown_lines()) if doc else 0,
        notes=[_MACRO_SYNC],
    )
    if json_out:
        _emit(report)
        return
    if doc is None:
        _say(_no_file("macros-cache.txt", target))
        return
    _say(f"{report.file}:")
    if not report.macros:
        _say("  no macros")
    for m in report.macros:
        cut = "" if m.complete else "  (the file ends before this macro's END)"
        _say(f"  {m.name}  (id {m.id}, icon {m.icon}){cut}")
        for line in m.body_lines:
            _say(f"    {line}")
    if report.unknown_lines:
        _say(f"  {report.unknown_lines} line(s) outside any macro are not shown")
    _say(_MACRO_SYNC)


# The largest macros-cache.txt `macros check` reads: far above any real one (a
# full set of macros at the limit is a few hundred KiB), so a planted giant
# is refused before it is read.
_MACROS_LIMIT = 16 * 1024 * 1024

_MACRO_FILE_NOTE = (
    "macros-cache.txt is what the client last wrote; the server can replace it at login when "
    "macro sync is on. In a real capture (docs/LAB_FORMATS.md, 2026-10-16) a macro made in a "
    "finished session was missing from it (whether the client rewrites it at logout is "
    "[verify]), so a macro may be missing here, or still listed after you deleted it."
)
# The note in text: its marker as the sentence's own words, wherever it is
# said ("whether ... is not yet confirmed"), as char gear does for its own.
_MACRO_FILE_TEXT = _MACRO_FILE_NOTE.replace("logout is [verify])", "logout is not yet confirmed)")


def _macro_notes(notes: Sequence[str]) -> list[str]:
    """`notes` with the macros-cache.txt note in its text words."""
    return [_MACRO_FILE_TEXT if n == _MACRO_FILE_NOTE else n for n in notes]


_MACROS_CHECK_NOTES = [
    _MACRO_FILE_NOTE,
    "The spellbook and action bars are the lab-addon's record as of its last save "
    "(logout or /reload), not the session in progress. It reads the player's spellbook "
    "only, so an item, a pet, mount or toy spell after /cast reads as not in the spellbook.",
]


class MacrosFile(_Out):
    scope: macrocheck.MacroScope
    file: str | None  # relative to the flavor folder; None when there is no such file
    macros: int
    unknown_lines: int  # lines outside any macro, not checked


class MacrosCheckReport(_Out):
    """`wowlab macros check --json`. Findings alone leave the exit code at 0."""

    flavor_folder: str
    account: str
    character: str | None  # "<realm folder>/<character folder>"; None: account macros only
    files: list[MacrosFile]
    record_file: str | None  # the character's WowLab.lua the spellbook and bars come from
    record_schema: int | None
    record_error: str | None  # why the WowLab.lua could not be read
    length_limit: int
    length_unit: str  # what the length rule counted
    macros: list[macrocheck.CheckedMacro]
    findings: list[macrocheck.Finding]
    cannot_judge: list[macrocheck.CannotJudge]
    on_no_bar_at_least: int | None
    blank_names: int  # macros named with nothing but spaces: left out of duplicate-name
    notes: list[str]


def _check_record(
    lay: layout.Layout, acct: layout.Account, character: str | None
) -> tuple[str | None, labaddon.CharDB | None, str | None]:
    """The character's WowLab.lua: its path, its record, or why it could not
    be read (the other rules still run, so none of this is an error)."""
    if character is None:
        return None, None, None
    char = _select_character(acct, character)
    mine = [
        f
        for f in lay.saved_variables()
        if f.account == acct.folder
        and f.scope == "character"
        and f.realm_folder == char.realm_folder
        and f.character_folder == char.folder
        and _is_lab_file(f)
    ]
    if not mine:
        return None, None, None
    try:
        target = _chosen_lab_file(mine)
    except CliError as exc:  # several files and no single choice (case variants)
        return None, None, str(exc)
    try:
        return target.path, _listed(labaddon.read_char, lay, target.path), None
    except (labaddon.LabAddonError, luadata.LuaDataError, OSError) as exc:
        return target.path, None, str(exc)


@macros_app.command("check", short_help="Check macros for common mistakes.")
@_handled
def macros_check(
    account: AccountOpt = None,
    character: CharacterOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes_out: NotesOpt = False,
) -> None:
    """Check the account's macros, and one character's with --character, for
    a body over 255, a spell not in the spellbook, a macro on no action bar,
    duplicate names or bodies, unbalanced [ ] and a #showtooltip (or #show,
    the same rule assumed, not yet confirmed) that is not the first line. The
    spellbook and action bars come from the character's lab-addon record;
    without it, or when the record says it dropped values, those
    two rules say "cannot judge". Reads only.

    \f
    JSON: MacrosCheckReport."""
    _, chosen, lay = _open(root, flavor)
    acct = _select_account(lay, account)
    scopes: list[macrocheck.MacroScope] = (
        ["account"] if character is None else ["account", "character"]
    )
    files: list[MacrosFile] = []
    macros: list[macrocheck.SourceMacro] = []
    target: _Target | None = None
    for scope in scopes:
        target, found = _wtf_file(lay, "macros-cache.txt", scope, acct.folder, character)
        if found is None:
            files.append(MacrosFile(scope=scope, file=None, macros=0, unknown_lines=0))
            continue
        data = _listed(snapshot.read_regular_file, lay, found.path, limit=_MACROS_LIMIT)
        doc = wtfconfig.parse_macros(data)
        files.append(
            MacrosFile(
                scope=scope,
                file=found.path,
                macros=len(doc.macros),
                unknown_lines=len(doc.unknown_lines()),
            )
        )
        macros.extend(
            macrocheck.SourceMacro(scope=scope, file=found.path, macro=m) for m in doc.macros
        )
    record_file, record, record_error = _check_record(lay, acct, character)
    who = target.character if target is not None and character is not None else None
    if record_error is not None:
        no_record = f"no spellbook record: {record_file or 'the WowLab.lua'} was not read"
    elif character is None:
        no_record = "no spellbook record (no character chosen; use --character)"
    else:
        no_record = macrocheck.NO_RECORD
    result = macrocheck.check(macros, record, character=who, no_record=no_record)
    report = MacrosCheckReport(
        flavor_folder=chosen.folder,
        account=acct.folder,
        character=who,
        files=files,
        record_file=record_file,
        record_schema=record.schema_ if record is not None else None,
        record_error=record_error,
        length_limit=macrocheck.LENGTH_LIMIT,
        length_unit=macrocheck.LENGTH_UNIT,
        macros=result.macros,
        findings=result.findings,
        cannot_judge=result.cannot_judge,
        on_no_bar_at_least=result.on_no_bar_at_least,
        blank_names=result.blank_names,
        notes=_MACROS_CHECK_NOTES,
    )
    if json_out:
        _emit(report)
        return
    for f in report.files:
        if f.file is None:
            _say(f"{f.scope.capitalize()} macros: no macros-cache.txt")
        else:
            extra = f", {f.unknown_lines} line(s) outside any macro not checked"
            _say(
                f"{f.scope.capitalize()} macros: {_plural(f.macros, 'macro')} in {f.file}"
                + (extra if f.unknown_lines else "")
            )
    held = [*_account_words(acct), report.record_file]
    if report.record_error is not None:
        place = report.record_file or "the WowLab.lua"
        _say_prose(
            f"Spellbook and action bars: {place} not read ({report.record_error})", keep=held
        )
    elif report.record_file is None:
        hint = "use --character" if character is None else macrocheck.GET_RECORD
        _say_prose(f"Spellbook and action bars: no lab-addon record ({hint})", keep=held)
    else:
        _say(f"Spellbook and action bars: {report.record_file} (schema {report.record_schema})")
    if report.blank_names:
        _say(
            f"Blank names: {_plural(report.blank_names, 'macro')} named with nothing but spaces, "
            "left out of duplicate-name"
        )
    _say(f"Findings: {len(report.findings)}")
    for finding in report.findings:
        _say_prose(
            f"{finding.rule}: {finding.macro.scope} macro {finding.macro.id} "
            f'"{finding.name}" (line {finding.line}): {_player_copy(finding.detail)}',
            indent="  ",
            hang="    ",
        )
    _say(f"Cannot judge: {len(report.cannot_judge)}")
    for c in report.cannot_judge:
        if c.macro is None:
            _say_prose(f"{c.rule}: {_player_copy(c.reason)}", indent="  ", hang="    ")
        else:
            _say_prose(
                f"{c.rule}: {c.macro.scope} macro {c.macro.id} "
                f'"{c.name}" (line {c.line}): {_player_copy(c.reason)}',
                indent="  ",
                hang="    ",
            )
    # Built so the marker reads as words: "... in bytes (not yet confirmed)."
    length = (
        f"Length: counted in {report.length_unit}; the client's limit is "
        f"{report.length_limit}, counted in characters or in bytes [verify]."
    )
    # The one caveat: the length rule's unknown when a body is over the
    # limit; otherwise that macros-cache.txt may be stale or replaced.
    over = any(
        m.characters > report.length_limit or m.bytes > report.length_limit for m in report.macros
    )
    caveat = length if over else _MACRO_FILE_TEXT
    _say_notes([length, *_macro_notes(report.notes)], notes_out, caveat, keep=held)
