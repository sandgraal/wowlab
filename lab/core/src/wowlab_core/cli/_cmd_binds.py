"""`wowlab binds list` and `binds coach` (M16-111)."""

import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Literal

import typer

from wowlab_core import (
    bindscoach,
    combatlog,
    labaddon,
    layout,
    luadata,
    wtfconfig,
)
from wowlab_core.cli._cmd_cvar import (
    _ACCOUNT_BINDINGS,
    _NO_CHARACTER_BINDINGS,
    _no_file,
)
from wowlab_core.cli._cmd_log import (
    _BATCHES,
    _NO_LOG,
    _logs_dir,
    _rel,
)
from wowlab_core.cli._common import (
    EXIT_USAGE,
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
    _home_words,
    _is_lab_file,
    _lab_char_file,
    _listed,
    _open,
    _Out,
    _player_copy,
    _plural,
    _say,
    _say_note,
    _say_notes,
    _say_prose,
    _select_account,
    _select_character,
    _wtf_file,
    binds_app,
)


def _bindings_note(scope: Scope, found: bool) -> str:
    if scope == "character" and not found:
        return _NO_CHARACTER_BINDINGS
    if scope == "account":
        return _ACCOUNT_BINDINGS
    return "(the server can replace this file at login when binding sync is on; see wowlab doctor)"


class BindItem(_Out):
    key: str
    action: str
    line: int


class BindsReport(_Out):
    """`wowlab binds list --json`."""

    file: str | None
    scope: Scope
    account: str | None
    character: str | None
    bindings: list[BindItem]
    unknown_lines: int
    notes: list[str]  # the caveats the text output prints


@binds_app.command("list", short_help="Key bindings of the account or of one character.")
@_handled
def binds_list(
    account: AccountOpt = None,
    character: CharacterOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Key bindings of the account, or of one character with --character.

    \f
    JSON: BindsReport."""
    _, _, lay = _open(root, flavor)
    scope: Scope = "character" if character is not None else "account"
    target, found = _wtf_file(lay, "bindings-cache.wtf", scope, account, character)
    doc = _listed(wtfconfig.read_bindings, lay, found.path) if found else None
    report = BindsReport(
        file=found.path if found else None,
        scope=target.scope,
        account=target.account,
        character=target.character,
        bindings=[BindItem(key=b.key, action=b.action, line=b.index + 1) for b in doc.bindings]
        if doc
        else [],
        unknown_lines=len(doc.unknown_lines()) if doc else 0,
        notes=[_bindings_note(scope, doc is not None)],
    )
    if json_out:
        _emit(report)
        return
    if doc is None:
        _say(_no_file("bindings-cache.wtf", target))
        if scope == "character":
            _say_note(_NO_CHARACTER_BINDINGS)
        return
    _say(f"{report.file}:")
    for b in report.bindings:
        _say(f"  {b.key:<24} {b.action}")
    if report.unknown_lines:
        _say(f"  {report.unknown_lines} other line(s) are not bind lines and are not shown")
    _say_note(report.notes[0])


_COMBATLOG_ON = (
    "Casts are counted only from a combat log, which the client writes only while combat "
    "logging is on: type /combatlog in game before you play (whether it stays on after "
    "logout has not been checked)."
)
_COACH_LOG_SCAN = 10  # the newest logs looked through for the character's casts
# Bytes of logs older than the newest read while looking for the character's
# casts; past this the scan stops, and says so.
_COACH_LOG_BUDGET = 256 * 1024 * 1024
_COACH_NOTES = [
    "Without --character, the character judged is the one whose lab-addon record has the "
    "newest schema, and of those the one written last: a newer schema records more.",
    f"Without --log, the newest of the {_COACH_LOG_SCAN} newest combat logs under Logs/ with "
    "casts by the character judged is read (older logs are looked through only up to "
    f"{_COACH_LOG_BUDGET // (1024 * 1024)} MiB); if none has any, the newest is read and a "
    "warning says whose casts it holds. A log names a character <First>-<Realm>-. A Forever "
    "character folder is <First>-<Second>, whose second name is not a realm; a retail one is "
    "<Name> under its realm's folder. So the two are matched by first name only, and two of "
    "your characters with one first name are not told apart. Your casts are the log's "
    "SPELL_CAST_SUCCESS records whose source flags say yours and a player (not a pet); the "
    "name only chooses the log.",
    "The action bars and key bindings are the lab-addon's record as of its last save (logout "
    "or /reload), not the session in progress; the log may be older or newer than it. A key "
    "reaches a spell through the default UI's action buttons only: the main bar at page 1, "
    "and the bottom, right and extra bars. Its other pages (forms, stances, stealth, paging), "
    "the pet and stance bars, an addon's buttons, items and macros are not followed.",
    "An auto-repeating spell (a wand's Shoot, Auto Shot) may be logged once per shot rather "
    "than once per key press; that has not been checked against a real log, so its count "
    "may be high.",
]
_SPAN_TIME = re.compile(r"\d{1,2}:\d{2}:\d{2}")


# "first name shared": the log's caster has the judged character's first name,
# and so does another character folder of the account.
# "no caster name": your casts carry no quoted name to compare.
CasterMatch = Literal[
    "matches", "first name shared", "does not match", "no casts", "no caster name", "no character"
]


class CoachLog(_Out):
    file: str | None  # relative to the flavor folder; None: no combat log under Logs/
    # "caster name": the newest of the newest logs with casts by the character
    # judged; "newest": no log had any, or there is no character to look for.
    chosen_by: Literal["caster name", "newest", "--log"] | None
    character_name: str | None  # the first name looked for, from the character folder
    casters: list[str]  # the source names of your casts in this log, as written
    caster_match: CasterMatch | None  # None: no combat log
    warning: str | None  # said when the log's casts may not be the character's
    # Older logs skipped (unreadable) or not looked at (the byte budget), in one line.
    scan_note: str | None
    counts: combatlog.CastCounts | None


class BindsCoachReport(_Out):
    """`wowlab binds coach --json`. Findings alone leave the exit code at 0."""

    flavor_folder: str
    account: str
    character: str | None  # "<realm folder>/<character folder>" of the record read
    # "newest schema": the record with the newest schema, then the one written last.
    chosen_by: Literal["--character", "newest schema"] | None
    chosen_because: str | None  # why that character, without --character
    record_file: str | None  # the character's WowLab.lua: action bars, bindings, spellbook
    record_schema: int | None
    record_error: str | None  # why no WowLab.lua was read
    log: CoachLog
    costs: bindscoach.Costs
    commands_unseen: int | None  # binding commands the record counts and leaves out
    keys_unseen: int | None  # keys the record counts and leaves out
    unseen: str | None  # said before any finding; None without a usable bindings record
    matched_by: Literal["spell id or spellbook name", "spell id"]
    spells: list[bindscoach.CastSpell]
    frequent_on_high_cost: list[bindscoach.FrequentOnHighCost]
    cast_without_bind: list[bindscoach.CastWithoutBind]
    on_unfollowed_page: list[bindscoach.OnUnfollowedPage]
    bound_never_cast: list[bindscoach.BoundNeverCast]
    # True when "bound, never cast" is withheld for a log under the minimum
    # sample (costs.table.never_cast_min_casts); its cannot_judge row says so.
    never_cast_withheld: bool
    cannot_judge: list[bindscoach.CannotJudge]
    caveats: list[str]
    notes: list[str]


def _coach_record(
    lay: layout.Layout, acct: layout.Account, character: str | None
) -> tuple[
    layout.SavedVariablesFile | None,
    Literal["--character", "latest"] | None,
    labaddon.CharDB | None,
    str | None,
]:
    """The character's WowLab.lua as `char show` chooses it, or why there is
    none to read. A --character naming no folder stays a usage error; a
    record that is missing or unreadable is reported, and the casts are still
    counted."""
    files = [f for f in lay.saved_variables() if f.account == acct.folder and _is_lab_file(f)]
    try:
        target, how, _ = _lab_char_file(acct, files, character)
    except CliError as exc:
        if exc.code == EXIT_USAGE:
            raise
        return None, None, None, str(exc)
    try:
        return target, how, _listed(labaddon.read_char, lay, target.path), None
    except (labaddon.LabAddonError, luadata.LuaDataError, OSError) as exc:
        return target, how, None, str(exc)


def _coach_log(lay: layout.Layout, name: str | None) -> Path | None:
    """The combat log to read: the one `--log` names, directly under Logs/,
    or the newest. A name that is a path, or not a combat log there, is a
    usage error; only regular files count, never a link."""
    logs = _logs_dir(lay)
    if name is None:
        return combatlog.newest_log(logs) if logs is not None else None
    if "/" in name or "\\" in name or name in {"", ".", ".."}:
        raise CliError(f"--log takes a file name under Logs/, not a path: {name!r}", EXIT_USAGE)
    found = combatlog.find_logs(logs) if logs is not None else []
    match = [p for p in found if p.name == name] or [
        p for p in found if p.name.casefold() == name.casefold()
    ]
    if len(match) != 1:
        listing = ", ".join(p.name for p in found) or "none"
        raise CliError(
            f"no combat log {name!r} under Logs/ (combat logs there: {listing})", EXIT_USAGE
        )
    return match[0]


Folder = tuple[str, str]  # (realm folder, character folder)
_CoachRanked = tuple[
    int,  # the record's schema; -1 when it could not be read
    int,  # the folder's newest WowLab.lua, by modification time
    Folder,
    layout.SavedVariablesFile | None,
    labaddon.CharDB | None,
    str | None,  # why no record was read
]


def _coach_default_record(
    lay: layout.Layout, acct: layout.Account
) -> tuple[
    layout.SavedVariablesFile | None,
    Literal["newest schema"] | None,
    labaddon.CharDB | None,
    str | None,
    str | None,
    Folder | None,
]:
    """Without --character, `binds coach` judges the character whose
    WowLab.lua has the newest schema, then the one written last (ties: the
    last by folder name), and says why (docs/LAB_PLAN.md §16.2, amended
    2026-11-04). Every character's record is read to learn its schema; one
    that cannot be read ranks below every one that can. The last value is the
    character folder judged, also when its record could not be used. The
    `char` commands keep their own rule."""
    files = [f for f in lay.saved_variables() if f.account == acct.folder and _is_lab_file(f)]
    folders: dict[Folder, list[layout.SavedVariablesFile]] = {}
    for f in files:
        if f.scope == "character" and f.realm_folder and f.character_folder:
            folders.setdefault((f.realm_folder, f.character_folder), []).append(f)
    if not folders:
        target, how, record, error = _coach_record(lay, acct, None)
        return target, None if how is None else "newest schema", record, error, None, None
    ranked: list[_CoachRanked] = []
    for folder, mine in folders.items():
        newest = max(f.mtime_ns for f in mine)
        try:
            target = _chosen_lab_file(mine)
        except CliError as exc:
            ranked.append((-1, newest, folder, None, None, str(exc)))
            continue
        try:
            record = _listed(labaddon.read_char, lay, target.path)
        except (labaddon.LabAddonError, luadata.LuaDataError, OSError) as exc:
            ranked.append((-1, newest, folder, target, None, str(exc)))
            continue
        ranked.append((record.schema_, newest, folder, target, record, None))
    ranked.sort(key=lambda r: (r[0], r[1], r[2]), reverse=True)
    _, _, folder, target, record, error = ranked[0]
    return target, "newest schema", record, error, _coach_because(ranked), folder


def _coach_because(ranked: Sequence[_CoachRanked]) -> str:
    """Why the first of `ranked` is the character judged, in a player's words."""
    schema, mtime = ranked[0][0], ranked[0][1]
    if len(ranked) == 1:
        return "the only character with a lab-addon record"
    if schema < 0:
        why = f"no character's record could be read ({len(ranked)} tried); the one written last"
    else:
        same = [r for r in ranked if r[0] == schema]
        why = (
            f"its record has the newest schema ({schema}) of the {len(ranked)} characters' records"
        )
        if len(same) > 1:
            why += f", and of the {len(same)} at that schema it was written last"
        later = [r for r in ranked if r[1] > mtime]
        if later:
            last = max(later, key=lambda r: (r[1], r[2]))
            at = f"schema {last[0]}" if last[0] >= 0 else "a record that could not be read"
            why += f"; {'/'.join(last[2])} was written later, at {at}"
    tied = [r for r in ranked[1:] if (r[0], r[1]) == (schema, mtime)]
    if tied:
        why += f"; tied on that with {'/'.join(tied[0][2])}, it is the last by folder name"
    return why


def _first_name(name: str) -> str:
    """The part before the first hyphen, normalised for comparison: a combat
    log names a character `<First>-<Realm>-`, and a character folder is
    `<First>` or `<First>-<Second>`. NFC first, since a macOS folder name
    may be stored decomposed."""
    return unicodedata.normalize("NFC", name.split("-", 1)[0]).casefold()


def _caster_match(counts: combatlog.CastCounts, who: str | None) -> CasterMatch:
    """Whether the casts counted are by a character with `who`'s first name
    (`who`: a character folder name)."""
    if who is None:
        return "no character"
    if not counts.mine:
        return "no casts"
    if not counts.source_names:
        return "no caster name"
    wanted = _first_name(who)
    if any(_first_name(n) == wanted for n in counts.source_names):
        return "matches"
    return "does not match"


def _namesakes(acct: layout.Account, folder: Folder) -> list[str]:
    """The account's other character folders with `folder`'s first name, as
    "<realm folder>/<folder>". A retail-style twin that holds only
    AddOns.txt (the Forever `<Realm>/<First>/` beside `<digits>/<First>-<Second>/`)
    is the same character's, not another one, and is left out."""
    wanted = _first_name(folder[1])
    found: list[str] = []
    for realm in acct.realms:
        for c in realm.characters:
            if (c.realm_folder, c.folder) == folder or _first_name(c.folder) != wanted:
                continue
            if c.twins and not c.folders and [f.casefold() for f in c.files] == ["addons.txt"]:
                continue
            found.append(f"{c.realm_folder}/{c.folder}")
    return sorted(found)


def _read_counts(path: Path) -> combatlog.CastCounts:
    return combatlog.cast_counts(combatlog.read_log(path, events=combatlog.CAST_COUNT_EVENTS))


def _shared_warning(first: str, namesakes: Sequence[str]) -> str:
    return (
        f"{', '.join(namesakes)} also {'has' if len(namesakes) == 1 else 'have'} the first name "
        f"{first}, and the coach matches a log to a character by first name only: these casts "
        "may be another of your characters'."
    )


def _coach_pick_log(
    lay: layout.Layout, acct: layout.Account, name: str | None, who: Folder | None
) -> CoachLog:
    """The combat log `binds coach` reads, and whether its casts are by the
    character judged (`who`, its folder). With --log, that log, with a
    warning when its casts are someone else's. Otherwise the newest of the
    `_COACH_LOG_SCAN` newest logs with casts by `who`'s first name, read newest
    first and stopping at the first; older logs are read up to
    `_COACH_LOG_BUDGET` bytes in all, and one that cannot be read is skipped.
    When none has (or there is no character), the newest, with the warning.
    A match on a first name another of the account's characters shares is
    "first name shared", with its own warning."""
    folder_name = who[1] if who is not None else None
    first = folder_name.split("-", 1)[0] if folder_name is not None else None
    namesakes = _namesakes(acct, who) if who is not None else []

    def matched(counts: combatlog.CastCounts) -> CasterMatch:
        match = _caster_match(counts, folder_name)
        return "first name shared" if match == "matches" and namesakes else match

    def out(
        path: Path,
        how: Literal["caster name", "newest", "--log"],
        counts: combatlog.CastCounts,
        warning: str | None,
        scan_note: str | None = None,
    ) -> CoachLog:
        match = matched(counts)
        if match == "first name shared" and first is not None:
            warning = _shared_warning(first, namesakes)
        return CoachLog(
            file=_rel(lay, path),
            chosen_by=how,
            character_name=first,
            casters=list(counts.source_names),
            caster_match=match,
            warning=warning,
            scan_note=scan_note,
            counts=counts,
        )

    if name is not None:
        path = _coach_log(lay, name)
        assert path is not None
        counts = _read_counts(path)
        warning = None
        if matched(counts) == "does not match":
            warning = (
                f"this log's casts are by {', '.join(counts.source_names)}, not {first}, the "
                "character judged: the findings below may be about another character."
            )
        return out(path, "--log", counts, warning)
    logs_dir = _logs_dir(lay)
    logs = combatlog.find_logs(logs_dir) if logs_dir is not None else []
    if not logs:
        return CoachLog(
            file=None,
            chosen_by=None,
            character_name=first,
            casters=[],
            caster_match=None,
            warning=None,
            scan_note=None,
            counts=None,
        )
    newest = logs[-1]
    newest_counts = _read_counts(newest)  # the log read anyway: its errors stay errors
    if who is None:
        return out(newest, "newest", newest_counts, None)
    if matched(newest_counts) in ("matches", "first name shared"):
        return out(newest, "caster name", newest_counts, None)
    read, used = 1, 0
    skipped: list[str] = []  # could not be read
    too_big: list[str] = []  # larger than what is left of the budget
    for path in list(reversed(logs))[1:_COACH_LOG_SCAN]:
        try:
            size = path.stat().st_size
            if used + size > _COACH_LOG_BUDGET:
                too_big.append(path.name)
                continue
            counts = _read_counts(path)
        except OSError:
            skipped.append(path.name)
            continue
        used += size
        read += 1
        if matched(counts) in ("matches", "first name shared"):
            return out(path, "caster name", counts, None, _scan_note(skipped, too_big))
    warning = None
    if matched(newest_counts) == "does not match":  # with no casts, its reports say so
        whose = ", ".join(newest_counts.source_names)
        if len(logs) == 1:
            where = f"the only combat log has no casts by {first}, the character judged"
        elif read == 1:
            where = (
                f"the newest combat log, the only one read, has no casts by {first}, the "
                "character judged"
            )
        else:
            where = (
                f"none of the {read} combat logs read has casts by {first}, the character "
                "judged, so the newest is read"
            )
        warning = (
            f"{where}; its casts are by {whose}: the findings below may be about another character."
        )
    return out(newest, "newest", newest_counts, warning, _scan_note(skipped, too_big))


def _scan_note(skipped: Sequence[str], too_big: Sequence[str]) -> str | None:
    """One line on the older logs not read, or None."""
    parts = []
    if skipped:
        parts.append(
            f"{_plural(len(skipped), 'older combat log')} could not be read and "
            f"{'was' if len(skipped) == 1 else 'were'} skipped ({', '.join(skipped)})"
        )
    if too_big:
        parts.append(
            f"{_plural(len(too_big), 'older combat log')} ({', '.join(too_big)}) "
            f"{'was' if len(too_big) == 1 else 'were'} not read, since older logs are looked "
            f"through only up to {_COACH_LOG_BUDGET // (1024 * 1024)} MiB in all; --log FILE "
            "reads one"
        )
    if not parts:
        return None
    line = "; ".join(parts)
    return f"{line[:1].upper()}{line[1:]}."


def _key_words(keys: Sequence[bindscoach.KeyCost]) -> str:
    return ", ".join(f"{k.key} (cost {k.cost})" for k in keys)


def _clock(stamp: str | None) -> str | None:
    found = _SPAN_TIME.search(stamp) if stamp is not None else None
    return found.group() if found else None


LogNameOpt = Annotated[
    str | None,
    typer.Option(
        "--log",
        help="A combat log's file name under Logs/ to read instead of the newest one.",
    ),
]


@binds_app.command(
    "coach", short_help="Spells you cast often on costly keys, and keys you never use."
)
@_handled
def binds_coach(
    account: AccountOpt = None,
    character: CharacterOpt = None,
    log: LogNameOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes_out: NotesOpt = False,
) -> None:
    """Coach your key bindings from a combat log: spells you cast often on
    high-cost keys, spells you cast with no key, and keys bound to spells you did not
    cast in that log (only from a stated number of your casts up). Keys reach spells
    through the lab-addon's record of the action bars and bindings of one character:
    --character, or the one whose record has the newest schema, then the one written
    last. The log is --log, or the newest with that character's casts. Combat logging
    must be on (/combatlog) for a log to exist. What a key costs is an opinion, from a
    table you can override. Reads only.

    \f
    JSON: BindsCoachReport."""
    _, chosen, lay = _open(root, flavor)
    acct = _select_account(lay, account)
    how: Literal["--character", "newest schema"] | None
    because: str | None = None
    folder: Folder | None = None
    if character is not None:
        target, given, record, record_error = _coach_record(lay, acct, character)
        how = None if given is None else "--character"
        named = _select_character(acct, character)
        folder = (named.realm_folder, named.folder)
    else:
        target, how, record, record_error, because, folder = _coach_default_record(lay, acct)
    if target is not None and target.realm_folder and target.character_folder:
        folder = (target.realm_folder, target.character_folder)
    if log is not None:
        _coach_log(lay, log)  # a bad --log is a usage error before the table or a log is read
    try:
        costs = bindscoach.load_costs()
    except bindscoach.CostTableError as exc:
        raise CliError(f"the key-cost table cannot be used: {exc}") from exc
    coach_log = _coach_pick_log(lay, acct, log, folder)
    counts = coach_log.counts
    if record_error is not None:
        no_record = f"no lab-addon record: {record_error}"
    else:
        no_record = f"no lab-addon record ({bindscoach.GET_RECORD})"
    result = bindscoach.coach(
        counts, record, costs.table, no_record=no_record, no_log=f"{_NO_LOG}. {_COMBATLOG_ON}"
    )
    report = BindsCoachReport(
        flavor_folder=chosen.folder,
        account=acct.folder,
        character=f"{target.realm_folder}/{target.character_folder}" if target else None,
        chosen_by=how,
        chosen_because=because,
        record_file=target.path if target else None,
        record_schema=record.schema_ if record is not None else None,
        record_error=record_error,
        log=coach_log,
        costs=costs,
        commands_unseen=result.commands_unseen,
        keys_unseen=result.keys_unseen,
        unseen=result.unseen,
        matched_by=result.matched_by,
        spells=result.spells,
        frequent_on_high_cost=result.frequent_on_high_cost,
        cast_without_bind=result.cast_without_bind,
        on_unfollowed_page=result.on_unfollowed_page,
        bound_never_cast=result.bound_never_cast,
        never_cast_withheld=result.never_cast_withheld,
        cannot_judge=result.cannot_judge,
        caveats=result.caveats,
        notes=[_COMBATLOG_ON, f"{_BATCHES[:1].upper()}{_BATCHES[1:]}.", *_COACH_NOTES],
    )
    if json_out:
        _emit(report)
        return
    _say_coach(report, notes_out, _account_words(acct))


_SHORT_REASON = re.compile(r" \(|: |\. |, so ")


def _short_reason(reason: str) -> str:
    """A cannot-judge reason up to its first explanation, for the count line
    it replaces; the whole reason is said once below the counts."""
    m = _SHORT_REASON.search(reason)
    return reason[: m.start()] if m else reason


def _say_coach(
    report: BindsCoachReport, show_notes: bool = False, keep: Sequence[str] = ()
) -> None:
    counts = report.log.counts
    against = report.record_file or "no lab-addon record"
    if report.log.file is None or counts is None:
        _say(f"Combat log: {_NO_LOG}")
    else:
        versions = ", ".join(counts.versions) or "none"
        span = (
            f"; your casts from {counts.first_timestamp} to {counts.last_timestamp}, log time"
            if counts.first_timestamp is not None
            else "; none of your casts"
        )
        newest = {
            # Not the judged character's log, perhaps: said here, so the
            # caveat slot is free (domain review, M16-16).
            "newest": " (the newest, whichever character wrote it; choose another with --log)",
            "--log": " (--log)",
            "caster name": f" (the newest with casts by {report.log.character_name})",
            None: "",
        }[report.log.chosen_by]
        _say_prose(
            f"Combat log: {report.log.file}{newest} ({_plural(counts.lines, 'line')}; "
            f"COMBAT_LOG_VERSION {versions}{span}), judged against {against}",
            keep=[report.log.file, report.record_file],
        )
        held = [*keep, report.log.file, *report.log.casters]
        if report.log.warning is not None:
            _say_prose(f"Warning: {report.log.warning}", indent="  ", hang="    ", keep=held)
        if report.log.scan_note is not None:
            _say_prose(f"Note: {report.log.scan_note}", indent="  ", hang="    ", keep=held)
        _say(
            f"  Your casts: {counts.mine} of {counts.cast_events} SPELL_CAST_SUCCESS record(s) "
            f"(picked by the source flags: yours, and a player); {counts.others} by other units"
        )
        if counts.other_version:
            _say(
                f"  {counts.other_version} cast(s) under a COMBAT_LOG_VERSION other than "
                f"{combatlog.CAST_LAYOUT_VERSION}, or before any, were not counted: their "
                "layout is not known"
            )
        if counts.not_read:
            _say(f"  {counts.not_read} cast(s) without the expected fields were not counted")
        if counts.unparsed:
            _say(f"  {_plural(counts.unparsed, 'line')} the tokenizer could not read")
    if report.record_file is None:
        why = report.record_error or bindscoach.GET_RECORD
        _say_prose(f"Action bars and bindings: no lab-addon record ({why})", keep=keep)
    elif report.record_error is not None:
        _say(f"Action bars and bindings: {report.record_file} not read ({report.record_error})")
    else:
        picked = ""
        if report.chosen_by == "newest schema":
            because = report.chosen_because or "the newest record schema, then written last"
            picked = f" ({because}; choose another with --character)"
        _say_prose(
            f"Action bars and bindings: {report.record_file} "
            f"(schema {report.record_schema}){picked}",
            keep=[*keep, report.record_file],
        )
    if report.unseen is not None:
        _say_prose(_player_copy(report.unseen))
    costs = report.costs
    table = costs.table
    # No absolute path: the tables' places are in --json and --notes (M16-16).
    if costs.source == "default":
        _say_prose(
            "Key costs: the shipped default table, an opinion that assumes a QWERTY keyboard, "
            "the left hand on W A S D and a mouse with two side buttons"
        )
    else:
        _say_prose(
            f"Key costs: your {bindscoach.COSTS_FILE} in wowlab's user config folder, over the "
            "shipped default"
        )
    _say(
        f"  High-cost: {table.high_cost} or more. Frequent: at least {table.frequent_share:.0%} "
        f"of your casts and at least {_plural(table.frequent_min_casts, 'cast')}."
    )
    # A report the coach cannot judge says so where its count would be: a 0
    # there read as "nothing wrong" (M16-16).
    unjudged: dict[str, str] = {}
    for c in report.cannot_judge:
        unjudged.setdefault(c.report, c.reason)
    # Without bars or bindings the coach cannot see a key: "no key" would
    # claim one (domain review, M16-16).
    no_key = "key unknown" if "cast-without-bind" in unjudged else "no key"
    _say(f"Your casts by spell (matched to slots by {report.matched_by}):")
    if not report.spells:
        _say("  none")
    for s in report.spells:
        keys = _key_words(s.keys) if s.keys else no_key
        note = f"; {s.match_note}" if s.match_note else ""
        _say(f"  {s.casts:>6}  {s.spell_name} ({s.spell_id}): {keys}{note}")

    def count(rule: str, n: int) -> str:
        if rule in unjudged:
            return f"cannot judge ({_short_reason(unjudged[rule])})"
        return str(n)

    frequent = count("frequent-on-high-cost", len(report.frequent_on_high_cost))
    _say_prose(f"Frequent spells on high-cost keys: {frequent}", keep=keep)
    for f in report.frequent_on_high_cost:
        note = f"; {f.match_note}" if f.match_note else ""
        _say(
            f"  {f.spell_name} ({', '.join(map(str, f.spell_ids))}): "
            f"{_plural(f.casts, 'cast')} ({f.share:.0%}), "
            f"{_key_words(f.keys)}{note}"
        )
    _say_prose(
        f"Spells cast with no bind: {count('cast-without-bind', len(report.cast_without_bind))}"
    )
    for u in report.cast_without_bind:
        where = f" (slots {', '.join(map(str, u.slots))})" if u.slots else ""
        _say_prose(
            f"{u.spell_name} ({u.spell_id}): {_plural(u.casts, 'cast')}{where}: {u.detail}",
            indent="  ",
            hang="    ",
        )
    if report.on_unfollowed_page:
        _say(
            "Spells only on main-bar pages the coach does not follow: "
            f"{len(report.on_unfollowed_page)}"
        )
        for p in report.on_unfollowed_page:
            _say(
                f"  {p.spell_name} ({p.spell_id}): {_plural(p.casts, 'cast')} (slots "
                f"{', '.join(map(str, p.slots))}): {p.detail}"
            )
    mine = counts.mine if counts is not None else 0
    start = _clock(counts.first_timestamp) if counts is not None else None
    end = _clock(counts.last_timestamp) if counts is not None else None
    span = f", {start} to {end} log time" if start and end else ""
    least = table.never_cast_min_casts
    withheld = report.never_cast_withheld
    if withheld:
        _say_prose(
            f"Binds to spells not cast in this log: withheld, this log has {mine} of your casts, "
            f"under the minimum sample of {least}"
        )
    elif "bound-never-cast" in unjudged:
        _say_prose(
            "Binds to spells not cast in this log: "
            f"{count('bound-never-cast', len(report.bound_never_cast))}"
        )
    else:
        _say_prose(
            f"Binds to spells not cast in this log ({_plural(mine, 'cast')}{span}; judged from "
            f"{_plural(least, 'cast')} up): {len(report.bound_never_cast)}"
        )
    for b in report.bound_never_cast:
        name = b.spell_name or "a spell the spellbook record does not name"
        _say(f"  {_key_words(b.keys)}: slot {b.slot}, {name} ({b.spell_id})")
    # Each reason once, in full, when a count line gave it short; the
    # withheld sample has its own line above.
    shown = [c for c in report.cannot_judge if not (withheld and c.report == "bound-never-cast")]
    for reason in dict.fromkeys(c.reason for c in shown):
        if _short_reason(reason) != reason:
            _say_prose(f"Cannot judge because: {reason}", keep=keep)
    # The one caveat is the first of the coach's own for this result; any
    # other joins the standing notes.
    caveats = [f"{c[:1].upper()}{c[1:]}." for c in report.caveats]
    standing = [*caveats[1:], *report.notes]
    if costs.source == "default":
        standing.append(
            f"To change what a key costs, put the values to change in "
            f"{_home_words(costs.user_file)} (create the folder if it is missing); a value there "
            "replaces the shipped one."
        )
    _say_notes(standing, show_notes, caveats[0] if caveats else None)
