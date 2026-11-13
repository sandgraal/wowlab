"""`wowlab char show`, `list`, `gear`, `binds` and `macros` (M16-111), and
`history` (M17-02)."""

import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import Field

from wowlab_core import (
    addoninstall,
    charhistory,
    dressing,
    gamedata,
    gear,
    install,
    itemlink,
    keymap,
    labaddon,
    lastbackup,
    layout,
    luadata,
    macrocheck,
    macroplace,
    snapshot,
    wtfconfig,
)
from wowlab_core.cli._cmd_binds import (
    _bindings_note,
    _coach_record,
)
from wowlab_core.cli._cmd_cvar import (
    _ACCOUNT_BINDINGS,
    _NO_CHARACTER_BINDINGS,
)
from wowlab_core.cli._cmd_doctor import (
    _REINSTALL,
)
from wowlab_core.cli._cmd_macros import (
    _MACRO_FILE_TEXT,
    _MACROS_CHECK_NOTES,
    _MACROS_LIMIT,
    _macro_notes,
)
from wowlab_core.cli._common import (
    _BLOCKED_PLACE,
    _SAVED_AT,
    EXIT_ERROR,
    EXIT_USAGE,
    AccountOpt,
    CharacterOpt,
    CliError,
    FlavorOpt,
    JsonOpt,
    NotesOpt,
    RootOpt,
    _account_words,
    _discover,
    _emit,
    _first_name_folder,
    _given_flavor,
    _handled,
    _held_words,
    _hidden_account,
    _home_words,
    _is_lab_file,
    _lab_char_file,
    _listed,
    _local_time,
    _note,
    _open_char,
    _Out,
    _player_copy,
    _plural,
    _safe,
    _say,
    _say_note,
    _say_notes,
    _say_prose,
    _select_account,
    _select_character,
    _select_flavor,
    _wtf_file,
    char_app,
)

# ─── char ────────────────────────────────────────────────────────────────────


_CHAR_NOTES = [
    "WowLab.lua is written at logout, /reload or a clean exit (a crash writes nothing): "
    "this is the character as of that save, not the session in progress.",
    "The addon records no time; the time shown is the file's modification time.",
]


class CharShowReport(_Out):
    """`wowlab char show --json`: the character's `WowLabCharDB` as the model
    for its schema reads it (unknown keys kept), and the account `WowLabDB`."""

    flavor_folder: str
    account: str
    character: str  # "<realm folder>/<character folder>"
    chosen_by: Literal["--character", "latest"]
    file: str  # relative to the flavor folder
    mtime_ns: int
    account_file: str | None
    account_record: (
        labaddon.AccountDBV1
        | labaddon.AccountDBV2
        | labaddon.AccountDBV3
        | labaddon.AccountDBV4
        | None
    )
    record: labaddon.CharDBV1 | labaddon.CharDBV2 | labaddon.CharDBV3 | labaddon.CharDBV4
    # The race, body type and class (M15-01): the record's `identity`
    # (schema 4), every value absent with a reason for schema 1 to 3.
    identity: labaddon.CharIdentity
    skip_known: list[str]  # section keys in `skip` this reader knows
    skip_ignored: list[str]  # the rest of `skip`, kept in `record` and ignored
    customization_loads_ago: int | None
    unknown_keys: list[str]
    notes: list[str]
    # The installed client's version from .build.info, and whether the client
    # that saved the record is older (M16-06; None when either is unknown).
    installed_version: str | None = None
    older_client: bool | None = None
    # The installed lab-addon against this client, as `doctor` reads it
    # (M16-06): what the text's advice for an older record rests on.
    lab_addon: addoninstall.LabAddonState | None = None


@char_app.command("show", short_help="What the lab-addon recorded for one character.")
@_handled
def char_show(
    character: CharacterOpt = None,
    account: AccountOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """What the lab-addon recorded for one character, from its WowLab.lua.
    Without --character, the character whose WowLab.lua was written last.

    \f
    JSON: CharShowReport."""
    _, chosen, lay = _open_char(root, flavor, account)
    acct = _select_account(lay, account)
    files = [f for f in lay.saved_variables() if f.account == acct.folder and _is_lab_file(f)]
    target, how, tie = _lab_char_file(acct, files, character)
    record = _listed(labaddon.read_char, lay, target.path)
    account_files = [f for f in files if f.scope == "account"]
    account_file = account_files[0] if account_files else None
    account_record = (
        _listed(labaddon.read_account, lay, account_file.path) if account_file else None
    )
    known, ignored = labaddon.skip_known(record)
    report = CharShowReport(
        flavor_folder=chosen.folder,
        account=acct.folder,
        character=f"{target.realm_folder}/{target.character_folder}",
        chosen_by=how,
        file=target.path,
        mtime_ns=target.mtime_ns,
        account_file=account_file.path if account_file else None,
        account_record=account_record,
        record=record,
        identity=labaddon.identity_of(record),
        skip_known=known,
        skip_ignored=ignored,
        customization_loads_ago=labaddon.customization_loads_ago(record),
        unknown_keys=labaddon.unknown_keys(record),
        notes=_CHAR_NOTES,
        installed_version=chosen.version,
        older_client=labaddon.older_client(labaddon.client_build(record), chosen.version),
        lab_addon=addoninstall.lab_addon_state(chosen),
    )
    if json_out:
        _emit(report)
        return
    _say(f"Character: {report.character} in account {acct.folder}{_picked_words(how, tie)}")
    _say(f"File: {target.path}")
    _say(f"Written: {_local_time(target.mtime_ns)} (the file's modification time)")
    if report.older_client:
        _say_prose(
            _older_record_line(chosen.version, report.lab_addon), hang="  ", keep=[_REINSTALL]
        )
    # describe() renders its own words with `_player_copy` before a value
    # read from the record goes in; the line then prints as it is.
    for line in labaddon.describe(record, render=_player_copy):
        _say(line)
    _say()
    if account_record is None:
        _say(f"Account {labaddon.ACCOUNT_VARIABLE}: no account {labaddon.ADDON_NAME}.lua")
    else:
        for line in labaddon.describe_account(account_record):
            _say(line)
    for note in _CHAR_NOTES:
        _say_note(note)


def _older_record_line(installed: str | None, state: addoninstall.LabAddonState | None) -> str:
    """`char show`'s line for a record an older client saved (M16-06): how
    to save a fresh one, and first, when the installed lab-addon is out of
    date for this client or not installed, that it may not load (its state
    as `doctor` reads it)."""
    head = f"This record was saved by an older client than the installed {installed}"
    fresh = (
        "with the lab-addon enabled for this character, log in on it, then log out or "
        "/reload, to save a fresh one."
    )
    status = state.status if state is not None else None
    if status == "stale":
        return (
            f"{head}, and the installed lab-addon is out of date for this client, so the game "
            f"may not load it (`wowlab doctor` says why). Close the game, run `{_REINSTALL}`, "
            f"then, {fresh}"
        )
    if status == "not_installed":
        return (
            f"{head}, and the lab-addon is not installed. Close the game, run `{_REINSTALL}`, "
            f"then, {fresh}"
        )
    return f"{head}: {fresh}"


def _picked_words(how: Literal["--character", "latest"], tie: str) -> str:
    """How `char show` and `looks import-char` say which file they took."""
    if how != "latest":
        return ""
    return (
        " (the WowLab.lua with the newest modification time; a wowlab restore also sets it"
        f"{tie}; choose another with --character)"
    )


class CharListFlavor(_Out):
    """One flavor `char list` listed (M16-114): its folder, its version
    from .build.info (None when no row names it) and how many character
    folders it holds, `<Realm>/<First>/` folders left out."""

    folder: str
    version: str | None
    characters: int
    # With --account (M16-116): True, the account folder was found in this
    # flavor and read; False, this flavor has no such account folder (it is
    # still listed, with `characters` 0); None, no --account was given, or
    # wowlab could not look where that folder would be (see `not_looked_at`).
    account_found: bool | None = None


class CharListReport(_Out):
    """`wowlab char list --json`: every character folder with a `WowLab.lua`
    in the flavor (or in `--account`), as `labaddon.survey` gives them, each
    with its record and summary or the reason it could not be read, and
    every place where character folders can be that wowlab could not look
    inside. With `--brief` every `record` is None and the rest is the same:
    a record runs to tens of KB, and the UI's picker needs none. The command
    exits 1 when any character has an `error` or `not_looked_at` is not
    empty."""

    # The flavor folder listed; None when every flavor of an install with
    # several is listed (no --flavor, no $WOWLAB_FLAVOR; M16-114): each row
    # then names its own `flavor`, as it does with one.
    flavor_folder: str | None
    # Each flavor listed, in discovery order, with its version and how many
    # character folders it holds (M16-114); with --account, a flavor without
    # that account too, marked `account_found: false` (M16-116).
    flavors: list[CharListFlavor] = []  # noqa: RUF012 - a pydantic field (`_Out`)
    # --account as the layout spells the account folder, or as the place wowlab
    # could not look inside spells it; as typed when neither names it (such as
    # an unlistable WTF/Account/); None: every account.
    account: layout.FsText | None
    characters: list[labaddon.CharacterFile]
    not_looked_at: list[labaddon.NotLookedAt]
    notes: list[str]
    # The installed client's version from .build.info, which each row's
    # `older_client` compares with (M16-06).
    installed_version: str | None = None


_CHAR_LIST_NOTES = [
    _CHAR_NOTES[0],
    "The addon records no time; the time shown is the file's modification time: the "
    "client's last save, unless something wrote the file since (a wowlab snap restore, undo "
    "or sv merge, or a copy).",
    "One row per folder: a renamed or transferred character (and a deleted one "
    "[verify]) keeps its old folder and last save here.",
]


_INSTALL_ADVICE = (
    "install the lab-addon with `wowlab addon install lab`, log in on the character, then log "
    "out or /reload"
)
# The one note `char list` adds when a row shows a folder's counts.
_BARE_NOTE = (
    "Counts are what each file held at the client's last exit; with settings sync on, the "
    "server may replace these files at login."
)
# How a `<Realm>/<First>/` folder with a digits-shape twin reads (the shapes
# decide it, never the flavor; the contents claim is the file map's).
_FIRST_NAME_FOLDER = (
    "a <Realm>/<First> folder: on Forever it holds only AddOns.txt [verify], and the "
    "lab-addon writes nothing here"
)


def _shows_counts(facts: labaddon.FolderFacts) -> bool:
    """Whether a folder's row shows its counts: always for a character
    folder, and for a `<Realm>/<First>/` folder when it holds anything
    counted."""
    nothing = (f"no {labaddon.CONFIG_FILE_NAME}", f"no {labaddon.MACROS_FILE_NAME}", 0)
    held = (facts.cvars_absent, facts.macros_absent, facts.saved_variables)
    return not facts.first_name_folder or held != nothing


def _bare_words(facts: labaddon.FolderFacts) -> str:
    """What a folder without a `WowLab.lua` holds, for its `char list` row:
    wowlab's own words in player words (`_player_copy`), the twin folders'
    names (read from the install) put in as they are."""
    cvars = facts.cvars_absent if facts.cvars is None else _plural(facts.cvars, "CVar")
    macros = facts.macros_absent if facts.macros is None else _plural(facts.macros, "macro")
    counts = _player_copy(
        f"{cvars}, {macros}, {_plural(facts.saved_variables, 'SavedVariables file')}"
    )
    if not facts.first_name_folder:
        words = counts
    else:
        words = _player_copy(_FIRST_NAME_FOLDER)
        if _shows_counts(facts):
            words += f"; it holds {counts}"
    if facts.twins:
        words += "; same first name as " + ", ".join(facts.twins)
    return words


def _row_words(e: labaddon.CharacterFile, installed: str | None = None) -> str:
    if e.no_lab_record is not None:
        return _bare_words(e.no_lab_record)
    if e.summary is not None:
        if e.older_client:
            # The summary names the saving client; this says it is the older one (M16-06).
            return f"{e.summary}; older than the installed client {installed}"
        return e.summary
    if e.place in _BLOCKED_PLACE:
        return f"not read: could not look inside {_BLOCKED_PLACE[e.place]} ({e.error})"
    if e.same_as is not None:  # skipped on purpose: the same bytes, read on another row
        return f"{e.error}"
    return f"not read: {e.error}"


def _unseen_words(n: int) -> str:
    return (
        f"wowlab could not look inside {_plural(n, 'place')} where character folders can be "
        f"(named on stderr); any character folder inside {'it' if n == 1 else 'them'} is not "
        "listed"
    )


# ─── char list --summary: the account hub's table (M17-01) ───────────────────


class CharSummaryProfession(_Out):
    """One profession as the record holds it: its skill line, the name of
    that line in the `SkillLine` table cached for the installed version
    (None when none is cached or it has no such row), and the skill and its
    current cap."""

    skill_line: int | None
    name: str | None
    rank: int | None
    max_rank: int | None


class CharSummaryRow(_Out):
    """One character folder in `char list --summary --json` (M17-01), as the
    UI's Account tab draws it. `state` is `recorded` (the folder's
    `WowLab.lua` was read), `no_lab_record` (the folder holds none: `advice`
    and `fix_command` say what puts one there) or `not_read` (`error` says
    why). For a recorded row each value is either set or its `<value>_absent`
    says why the record leaves it out; for the other states every value and
    every reason is None. Names come only from game tables already cached
    for the flavor's installed version (`version`); nothing is fetched."""

    flavor: layout.FsText | None
    version: str | None  # the flavor's installed version, which the names are read for
    account: layout.FsText
    realm_folder: layout.FsText
    label: layout.FsText  # the character folder's name
    shape: Literal["realm_name", "numeric_folder"]
    file: layout.FsText
    # The file's modification time: the client's last logout or /reload,
    # unless something wrote the file since. None when no file was read.
    mtime_ns: int | None
    state: Literal["recorded", "no_lab_record", "not_read"]
    error: str | None = None
    advice: str | None = None
    fix_command: str | None = None
    record_schema: int | None = None
    client_build: str | None = None
    older_client: bool | None = None
    level: int | None = None
    level_absent: str | None = None
    class_id: int | None = None
    class_file: str | None = None
    class_name: str | None = None
    class_absent: str | None = None
    race_id: int | None = None
    race_name: str | None = None
    race_absent: str | None = None
    spec_id: int | None = None
    spec_name: str | None = None
    spec_absent: str | None = None
    # `GetAverageItemLevel`'s equipped figure, as recorded in `gear.average`.
    item_level: labaddon.Number | None = None
    item_level_absent: str | None = None
    professions: list[CharSummaryProfession] | None = None
    professions_absent: str | None = None


class CharSummaryBackup(_Out):
    """For one flavor of `char list --summary --json` (M17-04, the Account
    tab's backup line): the client's version as discovery reads it, the
    newest backup of the flavor in wowlab's store (`lastbackup`: one the
    player took, the first run's, or the write gate's before a change; never
    a profile or an imported one), or None for each `snapshot_*` field when
    there is none, and whether the game updated since that backup."""

    flavor: layout.FsText
    version: str | None
    snapshot_id: str | None = None
    created_at: str | None = None  # UTC, ISO 8601, as the manifest records it
    snapshot_version: str | None = None  # the client's version the backup recorded
    game_updated: bool = False


class CharSummaryReport(_Out):
    """`wowlab char list --summary --json` (M17-01): one row per character
    folder `char list` lists, every flavor of an install with several unless
    --flavor names one, the `<Realm>/<First>/` folders left out (the
    lab-addon never writes there). The flavors, the account and the places
    wowlab could not look inside are `char list --json`'s. The command exits
    1 when a row is `not_read` or `not_looked_at` is not empty."""

    flavor_folder: str | None
    flavors: list[CharListFlavor]
    account: layout.FsText | None
    rows: list[CharSummaryRow]
    not_looked_at: list[labaddon.NotLookedAt]
    notes: list[str]
    # Each listed flavor's backup line (M17-04), in the order of `flavors`;
    # empty, with `backup_error` saying why, when the store cannot be read.
    backups: list[CharSummaryBackup] = Field(default_factory=list)
    backup_error: str | None = None


_FIX_COMMAND = "wowlab addon install lab"
_SUMMARY_ADVICE = (
    f"run `{_FIX_COMMAND}`, then log in on this character and log out once (or /reload)"
)
_SUMMARY_NOTES = [
    "Last saved is the file's modification time: the character's last logout or /reload, "
    "unless something wrote the file since (a wowlab snap restore, undo or sv merge, or a copy).",
    _CHAR_NOTES[0],
    "Names of classes, races, specs and professions come from game tables wowlab has already "
    "downloaded for the installed version (wowlab db2 fetch); otherwise the ids are shown.",
    "Item level is the client's equipped average as recorded.",
    _CHAR_LIST_NOTES[2],
]


def _section_absent(section: object) -> str:
    """Why a record holds no such section: the addon's reason, or that the
    file does not hold it."""
    if section is None:
        return labaddon.NOT_IN_FILE
    reason = getattr(section, "absent", None)
    return str(reason) if reason is not None else labaddon.NOT_IN_FILE


def _skill_names(version: str | None, lines: set[int]) -> dict[int, str]:
    """Each skill line's name in the `SkillLine` table cached for `version`."""
    found: dict[int, str] = {}
    if version is None or not lines:
        return found
    try:
        rows = gamedata.cached_rows("SkillLine", version)
        if rows is None:
            return found
        for row in rows:
            raw = row.get("ID") or ""
            if not raw.isdigit() or int(raw) not in lines:
                continue
            name = (row.get("DisplayName_lang") or "").strip()
            if name:
                found[int(raw)] = name
    except (ValueError, OSError, gamedata.GameDataError):
        return {}
    return found


def _summary_row(e: labaddon.CharacterFile, version: str | None) -> CharSummaryRow:
    """The hub's row for one `survey` row read with its record."""
    base: dict[str, object] = {
        "flavor": e.flavor,
        "version": version,
        "account": e.account,
        "realm_folder": e.realm_folder,
        "label": e.label,
        "shape": e.shape,
        "file": e.file,
        "mtime_ns": e.mtime_ns,
    }
    if e.no_lab_record is not None:
        return CharSummaryRow.model_validate(
            base
            | {
                "state": "no_lab_record",
                "advice": _SUMMARY_ADVICE,
                "fix_command": _FIX_COMMAND,
            }
        )
    record = e.record
    if record is None:
        error = e.error or "the record was not kept"
        if e.place in _BLOCKED_PLACE:
            error = f"could not look inside {_BLOCKED_PLACE[e.place]} ({error})"
        return CharSummaryRow.model_validate(base | {"state": "not_read", "error": error})
    identity = labaddon.identity_of(record)
    spec = gear.spec_of(record)
    names = _char_names(version, spec, identity)
    level = dressing.level_of(record)
    out: dict[str, object] = base | {
        "state": "recorded",
        "record_schema": e.record_schema,
        "client_build": e.client_build,
        "older_client": e.older_client,
        "level": level,
        "level_absent": labaddon.level_absent_of(record) if level is None else None,
        "class_id": identity.class_id,
        "class_file": identity.class_file,
        "class_name": names.class_name,
        "class_absent": identity.class_id_absent,
        "race_id": identity.race_id,
        "race_name": names.race,
        "race_absent": identity.race_id_absent,
        "spec_id": spec.id,
        "spec_name": names.spec,
        "spec_absent": None if spec.id is not None else (spec.absent or "not returned"),
    }
    section = record.gear
    if isinstance(section, labaddon.Gear):
        average = gear.average_of(section)
        out["item_level"] = average.equipped
        if average.equipped is None:
            out["item_level_absent"] = average.absent or "not returned"
    else:
        out["item_level_absent"] = _section_absent(section)
    held = record.professions
    if isinstance(held, labaddon.Professions):
        lines = {p.skill_line for p in held.list_ if p.skill_line is not None}
        named = _skill_names(version, lines)
        out["professions"] = [
            CharSummaryProfession(
                skill_line=p.skill_line,
                name=named.get(p.skill_line) if p.skill_line is not None else None,
                rank=p.rank,
                max_rank=p.max_rank,
            )
            for p in held.list_
        ]
    else:
        out["professions_absent"] = _section_absent(held)
    return CharSummaryRow.model_validate(out)


def _summary_value(value: object, name: str | None = None) -> str:
    """A hub value in the text form: the name, else the id, else "not
    recorded"."""
    if name:
        return name
    if value is not None:
        return f"id {value}"
    return "not recorded"


def _profession_words(p: CharSummaryProfession) -> str:
    name = p.name or (
        f"skill line {p.skill_line}" if p.skill_line is not None else "skill line not recorded"
    )
    rank = "skill not recorded" if p.rank is None else str(p.rank)
    if p.max_rank is not None:
        rank += f" of {p.max_rank}"
    return f"{name} ({rank})"


def _summary_lines(row: CharSummaryRow) -> list[str]:
    """The text form of one hub row: what it records, then when it was saved."""
    if row.state == "no_lab_record":
        return [f"no lab record: {row.advice}"]
    if row.state == "not_read":
        return [f"not read: {row.error}"]
    level = "not recorded" if row.level is None else str(row.level)
    klass = _summary_value(row.class_id, row.class_name or row.class_file)
    race = _summary_value(row.race_id, row.race_name)
    spec = _summary_value(row.spec_id, row.spec_name)
    ilvl = "not recorded" if row.item_level is None else f"{float(row.item_level):.1f}"
    first = f"level {level}, class {klass}, race {race}, spec {spec}, item level {ilvl}"
    if row.professions is None:
        professions = "professions: not recorded"
    elif not row.professions:
        professions = "professions: none"
    else:
        professions = "professions: " + ", ".join(_profession_words(p) for p in row.professions)
    when = "time unknown" if row.mtime_ns is None else _local_time(row.mtime_ns)
    return [first, professions, f"last logout or /reload: {when}"]


def _summary_backups(
    root: Path, flavors: Sequence[install.Flavor]
) -> tuple[list[CharSummaryBackup], str | None]:
    """Each flavor's backup line (M17-04): reads wowlab's store only."""
    try:
        manifests, imported = lastbackup.store_listing(snapshot.SnapshotStore())
    except (OSError, snapshot.SnapshotError) as exc:
        return [], f"wowlab could not read its snapshot store: {exc}"
    lines: list[CharSummaryBackup] = []
    for f in flavors:
        latest = lastbackup.latest_backup(manifests, root, f.folder, imported)
        lines.append(
            CharSummaryBackup(
                flavor=f.folder,
                version=f.version,
                snapshot_id=latest.id if latest is not None else None,
                created_at=latest.created_at if latest is not None else None,
                snapshot_version=latest.flavor_version if latest is not None else None,
                game_updated=lastbackup.game_updated(latest, f.version),
            )
        )
    return lines, None


def _say_summary(
    report: CharSummaryReport, every: bool, show_notes: bool, typed: str | None = None
) -> None:
    """`char list --summary`'s text: each flavor's characters, account by
    account, three lines each. `typed` is --account as given: a flavor that
    has no such account folder is headed so, as the plain list heads it."""
    if not report.rows:
        where = f"account {report.account}" if report.account is not None else "any account"
        if every:
            where += " of any flavor"
        if report.not_looked_at:
            text = (
                f"No character folder was found in {where}, but "
                f"{_unseen_words(len(report.not_looked_at))}."
            )
        else:
            text = f"No character folder was found in {where} ({_INSTALL_ADVICE})."
        _say(_player_copy(text, keep=[report.account]))
    for flavor in report.flavors:
        rows = [r for r in report.rows if r.flavor == flavor.folder]
        if every:
            heading = f"Flavor {flavor.folder}"
            if flavor.version:
                heading += f" ({flavor.version})"
            if flavor.account_found is False:
                heading += f": no account {typed if typed is not None else report.account}"
            elif not rows:
                heading += _player_copy(": no character folder")
            _say(heading)
        for account in dict.fromkeys(r.account for r in rows):
            mine = [r for r in rows if r.account == account]
            _say(f"Account {account}: " + _plural(len(mine), "character folder"))
            for row in mine:
                _say(f"  {row.realm_folder}/{row.label}")
                for line in _summary_lines(row):
                    _say_prose(line, indent="    ", hang="      ")
    if report.rows and report.not_looked_at:
        _say(f"{_unseen_words(len(report.not_looked_at))}.")
    _say_notes(report.notes, show_notes)


@char_app.command("list", short_help="Every character with a lab-addon record.")
@_handled
def char_list(
    account: Annotated[
        str | None,
        typer.Option("--account", help="Only this account folder under WTF/Account/."),
    ] = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    brief: Annotated[
        bool,
        typer.Option(
            "--brief",
            help="With --json, leave out each character's saved record: the folders, "
            "time, summary and any error stay. Text output is the same either way.",
        ),
    ] = False,
    summary: Annotated[
        bool,
        typer.Option(
            "--summary",
            help="One entry per character: level, class, race, spec, item level, "
            "professions and when it was last saved, or what to do when it has no lab "
            "record.",
        ),
    ] = False,
    notes_out: Annotated[
        bool,
        typer.Option(
            "--notes",
            help="With --summary, also print the standing notes; without it, one line "
            "after the answer counts them. The plain list always prints its notes.",
        ),
    ] = False,
) -> None:
    """Every character folder, in every account unless --account names one.
    With several flavors and no --flavor, every flavor's, under its name.
    For a folder with a WowLab.lua: when the file was last written, and its
    schema, the client that saved it and the spec, or why it could not be
    read. For one without: "no lab record", and how many CVars, macros and
    SavedVariables files its other files hold. With --summary, what each
    character's record says instead (the Account tab of wowlab ui shows the
    same); --notes then prints the standing notes. A file or
    place wowlab could not read or look inside is named on stderr and the
    command then exits 1; the others are still listed.

    \f
    JSON: CharListReport; with --summary, CharSummaryReport."""
    inst, _ = _discover(root)
    every = _given_flavor(flavor) is None and len(inst.flavors) > 1
    chosen_ones = list(inst.flavors) if every else [_select_flavor(inst, flavor)]
    entries: list[labaddon.CharacterFile] = []
    unseen: list[labaddon.NotLookedAt] = []
    blocks: list[tuple[install.Flavor, list[labaddon.CharacterFile]]] = []
    acct_folder: str | None = None
    missed: CliError | None = None
    # Each flavor in order, with its rows, or None when it has no such account.
    shown: list[tuple[install.Flavor, list[labaddon.CharacterFile] | None]] = []
    # The flavors whose --account folder wowlab could not look for (M16-116).
    unsure: set[str] = set()
    for chosen in chosen_ones:
        try:
            folder, mine, hidden, looked = _char_list_flavor(
                inst, chosen, account, root, summary or (json_out and not brief)
            )
        except CliError as exc:
            # With every flavor listed, a flavor without that account is
            # passed over; the error stands when none has it.
            if not every or exc.code != EXIT_USAGE:
                raise
            missed = missed or exc
            shown.append((chosen, None))
            continue
        acct_folder = folder if acct_folder is None else acct_folder
        if not looked:
            unsure.add(chosen.folder)
        mine = [e.model_copy(update={"flavor": chosen.folder}) for e in mine]
        hidden = [n.model_copy(update={"flavor": chosen.folder}) for n in hidden]
        entries += mine
        unseen += hidden
        blocks.append((chosen, mine))
        shown.append((chosen, mine))
    if missed is not None and not blocks:
        raise missed
    # Rows of character folders, apart from the `<Realm>/<First>/` folders.
    folders = [e for e in entries if not _first_name_folder(e)]
    bare_rows = [e for e in folders if e.no_lab_record is not None]
    counted = any(e.no_lab_record is not None and _shows_counts(e.no_lab_record) for e in entries)
    notes = _CHAR_LIST_NOTES + ([_BARE_NOTE] if counted else [])
    one = None if every else chosen_ones[0]
    report = CharListReport(
        flavor_folder=one.folder if one is not None else None,
        flavors=[
            CharListFlavor(
                folder=f.folder,
                version=f.version,
                characters=sum(1 for e in mine or [] if not _first_name_folder(e)),
                account_found=(None if account is None or f.folder in unsure else mine is not None),
            )
            for f, mine in shown
        ],
        account=acct_folder,
        characters=entries,
        not_looked_at=unseen,
        notes=notes,
        installed_version=one.version if one is not None else None,
    )
    if summary:
        versions = {f.folder: f.version for f, _ in shown}
        hub = CharSummaryReport(
            flavor_folder=report.flavor_folder,
            flavors=report.flavors,
            account=report.account,
            rows=[
                _summary_row(e, versions.get(e.flavor or ""))
                for e in entries
                if not _first_name_folder(e)
            ],
            not_looked_at=unseen,
            notes=_SUMMARY_NOTES,
        )
        backups, backup_error = _summary_backups(inst.root, [f for f, _ in shown])
        hub = hub.model_copy(update={"backups": backups, "backup_error": backup_error})
        if json_out:
            _emit(hub)
        else:
            _say_summary(hub, every, notes_out, account)
    elif json_out:
        _emit(report)
    elif not entries:
        where = f"account {acct_folder}" if acct_folder is not None else "any account"
        if every:
            where += " of any flavor"
        if unseen:
            text = f"No character folder was found in {where}, but {_unseen_words(len(unseen))}."
        else:
            text = f"No character folder was found in {where} ({_INSTALL_ADVICE})."
        _say(_player_copy(text, keep=[acct_folder]))
    else:
        names = {e.character: _safe(e.character) for e in entries}
        width = max(len(name) for name in names.values())
        for chosen, rows in shown:
            if every:
                # Each flavor's rows under its folder's name and version (M16-114).
                heading = f"Flavor {chosen.folder}"
                if chosen.version:
                    heading += f" ({chosen.version})"
                if rows is None:
                    heading += f": no account {account}"
                elif not rows:
                    heading += _player_copy(": no character folder")
                _say(heading)
            _char_list_rows(rows or [], names, width, chosen.version)
        # No install advice while a place went unseen: it may hold the files.
        if bare_rows and len(bare_rows) == len(folders) and not unseen:
            _say_note(
                f"No character folder here has a {labaddon.LAB_FILE_NAME} ({_INSTALL_ADVICE})."
            )
        if unseen:
            _say(f"{_unseen_words(len(unseen))}.")
        for note in notes:
            _say_note(note)
    for e in entries:
        if e.error is None:
            continue
        file = f"{e.flavor}/{e.file}" if every and e.flavor else e.file
        if e.place in _BLOCKED_PLACE:
            _note(
                "wowlab: could not look inside {} ({}): {}",
                file,
                _BLOCKED_PLACE[e.place],
                e.error,
            )
        elif e.same_as is not None:
            _note("wowlab: {} is {}", file, e.error)
        else:
            _note("wowlab: could not read {}: {}", file, e.error)
    for place in unseen:
        path = f"{place.flavor}/{place.path}" if every and place.flavor else place.path
        _note("wowlab: could not look inside {}: {}", path, place.reason)
    if unseen or any(e.error is not None for e in entries):
        raise typer.Exit(EXIT_ERROR)


def _char_list_flavor(
    inst: install.Install,
    chosen: install.Flavor,
    account: str | None,
    root: Path | None,
    keep_records: bool,
) -> tuple[str | None, list[labaddon.CharacterFile], list[labaddon.NotLookedAt], bool]:
    """One flavor's part of `char list`: the account folder as named, the
    rows, the places not looked inside, and False when the --account folder
    was not found but could be in one of those places (M16-116)."""
    lay = layout.Layout.for_flavor(chosen, inst.root)
    acct_folder: str | None = None
    hidden: list[labaddon.NotLookedAt] | None = None
    if account is not None:
        try:
            acct_folder = _select_account(lay, account).folder
        except CliError as exc:
            # No such account folder in the layout's lists: say so only if
            # nothing wowlab could not look inside could be (or hold) it.
            hidden = _hidden_account(lay, account) if exc.code == EXIT_USAGE else []
            if not hidden:
                raise
            acct_folder = next((n.account for n in hidden if n.account), account)
    if hidden is not None:
        return acct_folder, [], hidden, False
    # A refused row's `char show` advice repeats --root only when it was
    # given: otherwise $WOWLAB_WOW_ROOT or discovery finds the install
    # again, and no absolute path lands in text that gets pasted.
    found = labaddon.survey(
        lay,
        account=acct_folder,
        keep_records=keep_records,
        root=None if root is None else str(root),
        every_folder=True,
        installed_version=chosen.version,
    )
    return acct_folder, found.characters, found.not_looked_at, True


def _char_list_rows(
    entries: Sequence[labaddon.CharacterFile],
    names: dict[str, str],
    width: int,
    installed: str | None,
) -> None:
    """`char list`'s text rows for one flavor, account by account."""
    for folder in dict.fromkeys(e.account for e in entries):
        mine = [e for e in entries if e.account == folder]
        twins = sum(1 for e in mine if _first_name_folder(e))
        blocked = sum(1 for e in mine if e.place in _BLOCKED_PLACE)
        bare = sum(1 for e in mine if e.no_lab_record is not None) - twins
        total = len(mine) - twins
        heading = (
            f": {_plural(total, 'character folder')}: "
            f"{total - blocked - bare} with a {labaddon.LAB_FILE_NAME}"
        )
        if bare:
            heading += f", {bare} without"
        if blocked:
            heading += f", {blocked} that wowlab could not look inside"
        if twins:
            heading += f"; and {_plural(twins, '<Realm>/<First> folder')}"
        # The account folder's name is the install's; the rest is wowlab's.
        _say(f"Account {folder}" + _player_copy(heading))
        for e in mine:
            if e.no_lab_record is not None:
                when = "no lab record"
            elif e.mtime_ns is None:
                when = "time unknown"
            else:
                when = f"written {_local_time(e.mtime_ns)}"
            row = _row_words(e, installed)
            _say(f"  {names[e.character]:<{width}}  {when}  {row}")


class CharNames(_Out):
    """The spec, race and class names `char gear --json` gives beside their
    ids (M16-31): each the `Name_lang` (or, for a body type of 1, the
    non-empty female form) of the row with that id in the game-data table
    already cached for `build`, the installed client's version. A name is
    None when the install has no version, no such table is cached, the
    table has no row for the id or cannot be read, or the record holds no
    id; the page then shows the id. Nothing is fetched: `wowlab db2 fetch`
    fills the cache."""

    build: str | None
    spec: str | None
    race: str | None
    class_name: str | None


class CharGearReport(_Out):
    """`wowlab char gear --json`: one character's equipped gear from the
    lab-addon's `gear` record, slot by slot in the client's slot order, each
    link decoded by `itemlink` with the raw link beside it, the slot names and
    quality words from the slot table (`gear_slots.toml`, a default unless
    the notes say otherwise), and `GetAverageItemLevel` as recorded."""

    character: str  # "<realm folder>/<character folder>"
    file: str  # relative to the flavor folder
    mtime_ns: int
    spec: gear.GearSpec
    # The race, body type and class (M15-01), as `char show --json` gives it.
    identity: labaddon.CharIdentity
    # The character's level as the record holds it (the talent block's
    # `player_level`, read at the save), or None (M16-31).
    level: int | None
    level_absent: str | None  # why there is no level; None when there is one
    names: CharNames
    average: gear.GearAverage
    slots: list[gear.GearSlot]
    empty_slots: list[gear.EmptySlot]
    notes: list[str]
    # The flavor folder read, as the other `char` reports name it; just
    # before `dressing`, which stays last (M16-114).
    flavor_folder: str
    # The outside dressing room's address for this character, or the item ids
    # to paste into one when there is none (M15-02, `dressing.build`).
    dressing: dressing.DressingLink | dressing.DressingItems


_GEAR_NAMES_NOTE = (
    "Item names are each link's bracketed text; nothing is fetched. The link fields past the "
    "item id (enchant, gems, suffix, specialization, bonus ids) are named as community "
    'documentation names them [verify]; "upgrade" (field 10 and the modifier pairs) is the '
    "older documentation's name for those fields, and the upgrade track itself is carried in "
    "bonus ids."
)
_GEAR_LEVEL_NOTE = (
    "Item levels and the averages are the client's figures as recorded, never derived from "
    "bonus ids. The averages are GetAverageItemLevel's returns, not the mean of the item "
    "levels listed, and how the client computes them is not known."
)
_GEAR_OVERALL_NOTE = (
    "overall: on Retail, the best items owned, bags included (community documentation, "
    "[verify] on Forever)."
)
# Said by the page as the Spec row's title (M16-31); in the text, behind --notes.
_GEAR_SPEC_NOTE = "Spec is the client's specialization, not the talent points spent."


def _gear_table_note(source: gear.SlotTableSource) -> str:
    if source.source == "default":
        return (
            f"Slot names and quality words: the shipped default {gear.SLOTS_FILE}, community "
            "documentation for Retail, [verify] on Forever; a gear_slots.toml in wowlab's user "
            "config folder replaces any value it sets."
        )
    return (
        f"Slot names and quality words: your {gear.SLOTS_FILE} in wowlab's user config folder, "
        "over the shipped default."
    )


# The cached tables the names are read from, with the column of the female
# form of the name in each (M16-31). Table and column names, not game data.
_NAME_TABLES = {
    "spec": ("ChrSpecialization", "FemaleName_lang"),
    "race": ("ChrRaces", "Name_female_lang"),
    "class_name": ("ChrClasses", "Name_female_lang"),
}


def _cached_name(table: str, female: str, build: str, row_id: int, sex: int | None) -> str | None:
    """The name of row `row_id` in `table` as cached for `build`, or None."""
    try:
        rows = gamedata.cached_rows(table, build)
        if rows is None:
            return None
        for row in rows:
            if row.get("ID") != str(row_id):
                continue
            name = (row.get(female) or "").strip() if sex == 1 else ""
            name = name or (row.get("Name_lang") or "").strip()
            return name or None
    except (ValueError, OSError, gamedata.GameDataError):
        return None
    return None


def _char_names(
    version: str | None, spec: gear.GearSpec, identity: labaddon.CharIdentity
) -> CharNames:
    """Names for the record's ids from the tables cached for `version`."""
    ids = {"spec": spec.id, "race": identity.race_id, "class_name": identity.class_id}
    found: dict[str, str | None] = dict.fromkeys(ids)
    if version is not None:
        for field, row_id in ids.items():
            if row_id is not None:
                table, female = _NAME_TABLES[field]
                found[field] = _cached_name(table, female, version, row_id, identity.sex)
    return CharNames(
        build=version, spec=found["spec"], race=found["race"], class_name=found["class_name"]
    )


def _named(label: str, name: str | None, row_id: int | None, absent: str | None) -> str:
    """One header line as the UI's Character tab draws it (M16-31): the name
    with its id, else the id, else why there is none."""
    if row_id is not None:
        ident = f"{label.lower()} id {row_id}"
        return f"{label}: {name} ({ident})" if name else f"{label}: {ident}"
    return f"{label}: not recorded: {absent or 'not returned'}"


def _header_lines(report: CharGearReport) -> list[str]:
    """Level, class, race and spec, with the page's fallbacks."""
    ident = report.identity
    names = report.names
    level = (
        f"Level: {report.level}"
        if report.level is not None
        else f"Level: not recorded: {report.level_absent or 'not returned'}"
    )
    # One reason said once: a race missing for the class's reason says so.
    race_absent = ident.race_id_absent
    if ident.class_id is None and race_absent is not None and race_absent == ident.class_id_absent:
        race_absent = "as for Class"
    spec = report.spec
    spec_line = _named("Spec", None, spec.id, spec.absent)
    if spec.id is not None:
        index = "index not returned" if spec.index is None else f"index {spec.index}"
        where = f"{index}, from {spec.api}"
        spec_line = (
            f"Spec: {names.spec} (spec id {spec.id}, {where})"
            if names.spec
            else f"Spec: spec id {spec.id} ({where})"
        )
    return [
        level,
        _named(
            "Class", names.class_name or ident.class_file, ident.class_id, ident.class_id_absent
        ),
        _named("Race", names.race, ident.race_id, race_absent),
        spec_line,
    ]


def _no_gear(record: labaddon.CharDB, path: str) -> str | None:
    """Why `record` has no gear to show, or None when it has."""
    section = record.gear
    if section is None:
        return f"no gear in {path}: {labaddon.NOT_IN_FILE}"
    if isinstance(section, labaddon.AbsentSection):
        why = section.absent
        if why == labaddon.SWITCHED_OFF:
            why += (
                "; type /wowlab unskip gear in the game on this character, then /reload; the "
                "gear is in the file from the save after that (the next /reload or logout)"
            )
        return f"no gear in {path}: the lab-addon recorded it as absent ({why})"
    return None


def _slot_label(n: int, name: str | None) -> str:
    return f"slot {n:>2} {name if name is not None else '(no name in the slot table)'}"


def _gear_line(s: gear.GearSlot) -> str:
    quality = s.quality if s.quality is not None else f"colour {s.colour} not in the slot table"
    level = "item level not returned" if s.item_level is None else f"item level {s.item_level}"
    item = "no item id" if s.item_id is None else f"item {s.item_id}"
    parts = [f"{s.name}: {quality}, {level}, {item}"]
    if s.enchant_id:
        parts.append(f"enchant {s.enchant_id}")
    if s.gem_ids:
        parts.append("gems " + ", ".join(str(g) for g in s.gem_ids))
    if s.suffix_id:
        parts.append(f"suffix {s.suffix_id}")
    if s.bonus_ids:
        parts.append("bonus ids " + ", ".join(str(b) for b in s.bonus_ids))
    if s.crafter_removed:
        parts.append("crafter GUID removed from the link")
    return "; ".join(parts)


def _num_words(value: labaddon.Number | None) -> str:
    return "not returned" if value is None else str(value)


@char_app.command("gear", short_help="One character's equipped gear and item levels.")
@_handled
def char_gear(
    character: CharacterOpt = None,
    account: AccountOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes_out: NotesOpt = False,
) -> None:
    """The equipped gear the lab-addon recorded for one character, slot by slot
    in the client's order, with the item levels and averages the client gave.
    Without --character, the character whose WowLab.lua was written last. Names
    are the links' own text; nothing is fetched. Slot names and quality words
    come from a table you can override. A record without gear exits 1. Reads
    only.

    \f
    JSON: CharGearReport."""
    _, chosen, lay = _open_char(root, flavor, account)
    acct = _select_account(lay, account)
    files = [f for f in lay.saved_variables() if f.account == acct.folder and _is_lab_file(f)]
    target, how, tie = _lab_char_file(acct, files, character)
    record = _listed(labaddon.read_char, lay, target.path)
    missing = _no_gear(record, target.path)
    if missing is not None:
        raise CliError(missing)
    section = record.gear
    assert isinstance(section, labaddon.Gear)  # _no_gear said so
    try:
        table = gear.load_slot_table()
    except gear.SlotTableError as exc:
        raise CliError(f"the slot table cannot be used: {exc}") from exc
    try:
        filled, empty = gear.rows(section, table.table)
    except itemlink.ItemLinkError as exc:  # the record reader admits no such link
        raise CliError(f"{target.path}: an item link wowlab cannot read: {exc}") from exc
    notes = list(_CHAR_NOTES)
    if how == "latest":
        notes.append(
            "The character is the one whose WowLab.lua has the newest modification time; "
            "choose another with --character."
        )
    notes += [_gear_table_note(table), _GEAR_NAMES_NOTE, _GEAR_LEVEL_NOTE, _GEAR_OVERALL_NOTE]
    identity = labaddon.identity_of(record)
    spec = gear.spec_of(record)
    level = dressing.level_of(record)
    report = CharGearReport(
        flavor_folder=chosen.folder,
        character=f"{target.realm_folder}/{target.character_folder}",
        file=target.path,
        mtime_ns=target.mtime_ns,
        spec=spec,
        identity=identity,
        level=level,
        level_absent=labaddon.level_absent_of(record) if level is None else None,
        names=_char_names(chosen.version, spec, identity),
        average=gear.average_of(section),
        slots=filled,
        empty_slots=empty,
        notes=notes,
        dressing=dressing.build(identity, filled, version=chosen.version, level=level),
    )
    if json_out:
        _emit(report)
        return
    held = [*_account_words(acct), target.path]
    _say_prose(
        f"Character: {report.character} in account {acct.folder}{_picked_words(how, tie)}",
        keep=held,
    )
    _say(f"File: {target.path}")
    _say(f"Written: {_local_time(target.mtime_ns)} ({_SAVED_AT})")
    for line in _header_lines(report):
        _say(line)
    _say(
        f"Gear (slots {section.first_slot} to {section.last_slot} as the client numbers them, "
        f"{len(filled)} filled):"
    )
    by_slot: list[tuple[int, str, str]] = [
        (s.slot, _slot_label(s.slot, s.slot_name), _gear_line(s)) for s in filled
    ]
    by_slot += [(e.slot, _slot_label(e.slot, e.slot_name), "empty") for e in empty]
    width = max((len(label) for _, label, _ in by_slot), default=0)
    for _, label, what in sorted(by_slot, key=lambda row: row[0]):
        _say(f"  {label:<{width}}  {what}")
    average = report.average
    if average.absent is not None:
        _say(f"Averages: absent ({average.absent})")
    else:
        _say(
            f"Averages (GetAverageItemLevel, as the client returns them, not the mean of the "
            f"levels above): equipped {_num_words(average.equipped)}, overall "
            f"{_num_words(average.overall)}, pvp {_num_words(average.pvp)}"
        )
    apis = sorted({s.item_level_api for s in filled if s.item_level_api is not None})
    if apis:
        _say(f"Item levels from {', '.join(apis)}.")
    # No absolute path: the shipped table's place is in --json (M16-16).
    if table.source == "default":
        _say_prose(
            "Slot names and quality words: the shipped default table, community "
            "documentation for Retail, not yet confirmed on Forever"
        )
    else:
        _say_prose(
            f"Slot names and quality words: your {gear.SLOTS_FILE} in wowlab's user config "
            "folder, over the shipped default"
        )
    standing = [
        # The marker stands for an adjective here; said in words (M16-15).
        note.replace("[verify] on Forever", "not yet confirmed on Forever")
        for note in (_GEAR_NAMES_NOTE, _GEAR_LEVEL_NOTE, _GEAR_OVERALL_NOTE, _GEAR_SPEC_NOTE)
    ]
    standing.append(
        f"To use your own slot names or quality words, put the values to change in "
        f"{_home_words(table.user_file)} (create the folder if it is missing); a value there "
        "replaces the shipped one."
    )
    _say_notes([*standing, *_CHAR_NOTES], notes_out, keep=[_home_words(table.user_file)])


class CharBindsFile(_Out):
    """One `bindings-cache.wtf` `char binds` looked for."""

    scope: Literal["account", "character"]
    path: str | None  # relative to the flavor folder; None: there is no such file
    mtime_ns: int | None
    bind_lines: int
    unknown_lines: int  # lines that are not bind lines: kept, not read
    note: str


class CharBindsReport(_Out):
    """`wowlab char binds --json`: every key of the account and character
    `bindings-cache.wtf` and of the lab-addon's bindings record, with the
    action slot each presses and what the record says the slot holds
    (`keymap.KeyRow`), the filled slots no key reaches, and the record's two
    left-out counts with the sentence the text form prints for them
    (`left_out_note`). Without a usable action-bar record `cannot_place` says
    why, and the slot rows say "cannot place: no action-bar record"."""

    flavor_folder: str
    account: str
    character: str | None  # "<realm folder>/<character folder>"; None: no character chosen
    chosen_by: Literal["--character", "latest"] | None
    files: list[CharBindsFile]
    record_file: str | None  # the character's WowLab.lua: bindings, action bars, spellbook
    record_mtime_ns: int | None
    record_schema: int | None
    record_error: str | None  # why no WowLab.lua was read
    keys: list[keymap.KeyRow]
    unreached_slots: list[keymap.UnreachedSlot]
    commands_left_out: int | None  # counts in the record's bindings section; None without one
    keys_left_out: int | None
    left_out_note: str | None  # the two counts in the words the text form prints; None without them
    cannot_place: str | None
    cannot_compare: str | None
    notes: list[str]


def _char_binds_file(
    lay: layout.Layout, acct: layout.Account, folder: tuple[str, str] | None
) -> tuple[CharBindsFile, wtfconfig.BindingsDocument | None]:
    """The account file (`folder` None) or one character folder's file."""
    files = [
        f
        for f in lay.wtf_files()
        if f.account == acct.folder and f.name.casefold() == "bindings-cache.wtf"
    ]
    if folder is None:
        found = [f for f in files if f.scope == "account"]
        scope: Literal["account", "character"] = "account"
    else:
        found = [
            f
            for f in files
            if f.scope == "character" and (f.realm_folder, f.character_folder) == folder
        ]
        scope = "character"
    target = found[0] if found else None
    doc = _listed(wtfconfig.read_bindings, lay, target.path) if target else None
    note = _ACCOUNT_BINDINGS if scope == "account" else _NO_CHARACTER_BINDINGS
    if scope == "character" and doc is not None:
        note = _bindings_note("character", True)
    return (
        CharBindsFile(
            scope=scope,
            path=target.path if target else None,
            mtime_ns=target.mtime_ns if target else None,
            bind_lines=len(doc.bindings) if doc else 0,
            unknown_lines=len(doc.unknown_lines()) if doc else 0,
            note=note,
        ),
        doc,
    )


@char_app.command("binds", short_help="What each key does for one character.")
@_handled
def char_binds(
    character: CharacterOpt = None,
    account: AccountOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes_out: NotesOpt = False,
) -> None:
    """What each key does for one character: the account and character
    bindings-cache.wtf and the keys the lab-addon recorded, each key
    with the action slot its command presses (the main bar at page 1, the page
    shown with no form, stance or stealth active) and what the record says
    that slot holds, where the files and the record
    disagree, and the filled slots no key reaches. Without --character, the
    character whose WowLab.lua was written last. Reads only.

    \f
    JSON: CharBindsReport."""
    _, chosen, lay = _open_char(root, flavor, account)
    acct = _select_account(lay, account)
    target, how, record, record_error = _coach_record(lay, acct, character)
    folder: tuple[str, str] | None = None
    if target is not None and target.realm_folder and target.character_folder:
        folder = (target.realm_folder, target.character_folder)
    elif character is not None:
        char = _select_character(acct, character)
        folder = (char.realm_folder, char.folder)
    if folder is None:
        # The latest folder's files could not be told apart (case variants):
        # it is still the character `char show` would take, as in `char macros`.
        lab_files = [
            f
            for f in lay.saved_variables()
            if f.account == acct.folder and f.scope == "character" and _is_lab_file(f)
        ]
        top = max(lab_files, key=lambda f: (f.mtime_ns, f.path), default=None)
        if top is not None and top.realm_folder and top.character_folder:
            folder, how = (top.realm_folder, top.character_folder), "latest"
    account_file, account_doc = _char_binds_file(lay, acct, None)
    files = [account_file]
    character_doc = None
    if folder is not None:
        character_file, character_doc = _char_binds_file(lay, acct, folder)
        files.append(character_file)
    no_record = (
        f"no lab-addon record: {record_error}"
        if record_error is not None
        else f"no lab-addon record ({keymap.GET_RECORD})"
    )
    found = keymap.join_record(
        account=account_doc, character=character_doc, record=record, no_record=no_record
    )
    report = CharBindsReport(
        flavor_folder=chosen.folder,
        account=acct.folder,
        character=f"{folder[0]}/{folder[1]}" if folder is not None else None,
        chosen_by=how if how is not None else ("--character" if character is not None else None),
        files=files,
        record_file=target.path if target else None,
        record_mtime_ns=target.mtime_ns if target else None,
        record_schema=record.schema_ if record is not None else None,
        record_error=record_error,
        keys=found.keys,
        unreached_slots=found.unreached_slots,
        commands_left_out=found.commands_left_out,
        keys_left_out=found.keys_left_out,
        left_out_note=_binds_left_out_note(found.commands_left_out, found.keys_left_out),
        cannot_place=found.cannot_place,
        cannot_compare=found.cannot_compare,
        notes=[*found.notes, _CHAR_NOTES[0]],
    )
    if json_out:
        _emit(report)
        return
    _say_char_binds(report, notes_out, _account_words(acct))


def _binds_left_out_note(commands: int | None, keys: int | None) -> str | None:
    """The record's two left-out counts as one sentence, which `char binds`
    prints and `wowlab ui`'s Keys panel shows; None without both counts."""
    if commands is None or keys is None:
        return None
    return (
        "Left out of the bindings record, counted with no list: "
        f"{_plural(commands, 'command')} not named in plain upper case "
        "(a key bound straight to a spell, macro, item or click may be one), and "
        f"{_plural(keys, 'key')} not printable ASCII."
    )


def _source_words(row: keymap.KeyRow) -> str:
    files = ", ".join(s for s in row.sources if s != "record")
    if row.agreement is None:
        return ", ".join(row.sources)
    if row.agreement == "both":
        return f"{files} and record agree"
    if row.agreement == "cleared":
        return f"{files}: cleared; the record has no command on the key"
    if row.agreement == "file only":
        return f"{files} only: disagrees with the record"
    if row.agreement == "not in a file":
        return "record only (no file line for this key)"
    return "record only: disagrees with the files"


def _say_record(
    path: str | None,
    error: str | None,
    schema: int | None,
    mtime_ns: int | None,
    keep: Sequence[str],
) -> None:
    """The Record line of `char binds` and `char macros`, wrapped, its paths
    and folder names kept whole."""
    held = [*keep, path]
    if path is None:
        _say_prose(f"Record: none ({error or keymap.GET_RECORD})", keep=held)
    elif error is not None:
        _say_prose(f"Record: {path} not read ({error})", keep=held)
    else:
        assert mtime_ns is not None
        _say_prose(
            f"Record: {path} (schema {schema}), written {_local_time(mtime_ns)} ({_SAVED_AT})",
            keep=held,
        )


def _file_note(label: str, note: str) -> str:
    """A bindings file's parenthesised note (`_bindings_note`) as a sentence
    of its own under the file's label, for behind --notes."""
    text = note.removeprefix("(").removesuffix(")").removeprefix("account bindings; ")
    return f"{label}: {text}."


def _say_char_binds(
    report: CharBindsReport, show_notes: bool = False, keep: Sequence[str] = ()
) -> None:
    standing: list[str] = []
    if report.character is None:
        _say(
            f"Character: none in account {report.account} has a {labaddon.LAB_FILE_NAME}; "
            "the account bindings only (choose a character with --character)"
        )
    else:
        picked = " (written last; choose another with --character)" * (report.chosen_by == "latest")
        _say(f"Character: {report.character} in account {report.account}{picked}")
    _say_record(
        report.record_file, report.record_error, report.record_schema, report.record_mtime_ns, keep
    )
    for f in report.files:
        label = "Account bindings" if f.scope == "account" else "Character bindings"
        # What each file is goes behind --notes: the same words on every run.
        standing.append(_file_note(label, f.note))
        if f.path is None or f.mtime_ns is None:
            _say(f"{label}: no bindings-cache.wtf")
            continue
        unknown = f"; {_plural(f.unknown_lines, 'other line')}, kept" * bool(f.unknown_lines)
        _say(
            f"{label}: {f.path} ({_plural(f.bind_lines, 'bind line')}{unknown}), written "
            f"{_local_time(f.mtime_ns)}"
        )
    # Without a record read, the Record line above already says why.
    read = report.record_file is not None and report.record_error is None
    if read and isinstance(report.record_schema, int) and report.record_schema < 3:
        _say_prose(
            "Cannot place keys on action slots or compare the files with the record: schema "
            f"{report.record_schema} has no action-bar or bindings record ({keymap.GET_RECORD})"
        )
    elif read:
        if report.cannot_place is not None:
            _say_prose(f"Cannot place keys on action slots: {report.cannot_place}", keep=keep)
        if report.cannot_compare is not None:
            _say_prose(
                f"Cannot compare the files with the record: {report.cannot_compare}", keep=keep
            )
    tally = ""
    if report.cannot_compare is None:
        count = {a: sum(1 for k in report.keys if k.agreement == a) for a in _AGREEMENTS}
        tally = (
            f" ({count['both']} in a file and the record, {count['file only']} only in a file, "
            f"{count['record only']} only in the record with a file line putting the key on "
            f"another command, {count['not in a file']} only in the record with no file line "
            f"for the key, {count['cleared']} cleared)"
        )
    # Said inline, not as the caveat: what a main-bar key does depends on the
    # page a form, stance or stealth shows (domain review, M16-16).
    if any(k.command.startswith("ACTIONBUTTON") and k.slot is not None for k in report.keys):
        tally = (
            f" (main-bar keys at page 1, with no form, stance or stealth; {tally[2:]}"
            if tally
            else " (main-bar keys at page 1, with no form, stance or stealth)"
        )
    _say_prose(f"Keys: {len(report.keys)}{tally}")
    generic = False
    pad = f"  {'':<16} {'':<26} {'':>4}  "
    for k in report.keys:
        slot = str(k.slot) if k.slot is not None else "-"
        does = _player_copy(k.does, _held_words(k.holds))
        _say(f"  {k.key:<16} {k.command:<26} {slot:>4}  {does}  ({_source_words(k)})")
        if k.page_note is not None and "unconfirmed" in k.page_note:
            _say(f"{pad}{k.page_note}")
        if k.note == keymap.NOT_IN_A_FILE_NOTE:
            generic = True
        elif k.note is not None and k.disagrees:
            _say(f"{pad}{k.note}")
    if generic:
        standing.append(
            f"Only in the record, no file line for the key: {keymap.WHY_NOT_IN_A_FILE}."
        )
    if report.cannot_place is None:
        reached = [u for u in report.unreached_slots if not u.unfollowed_page]
        paged = [u for u in report.unreached_slots if u.unfollowed_page]
        _say(f"Filled slots no key reaches on the page its bar shows by default: {len(reached)}")
        for u in reached:
            _say(
                f"  slot {u.slot:>3}  {_player_copy(u.does, _held_words(u.holds))}  ({u.page_note})"
            )
        if paged:
            _say(f"Filled slots on main-bar pages that are not followed: {len(paged)}")
            for u in paged:
                _say(
                    f"  slot {u.slot:>3}  {_player_copy(u.does, _held_words(u.holds))}  ({u.page_note})"
                )
    # The one caveat: what the record left out, else that keys at the
    # client's defaults are not listed. Both say the key list may be short.
    caveat = report.left_out_note or next(
        (n for n in report.notes if n == keymap.NO_BINDINGS_NOTE), None
    )
    _say_notes([*standing, *report.notes], show_notes, caveat)


_AGREEMENTS: tuple[keymap.Agreement, ...] = (
    "both",
    "file only",
    "record only",
    "not in a file",
    "cleared",
)


class CharMacrosFile(_Out):
    """One `macros-cache.txt` `char macros` looked for."""

    scope: macrocheck.MacroScope
    path: str | None  # relative to the flavor folder; None: there is no such file
    mtime_ns: int | None
    macros: int
    unknown_lines: int  # lines outside any macro: kept, not read


class CharMacro(_Out):
    """One macro record (`macros list`'s `MacroItem`, with its scope, file
    and `VER` line), where it may sit (`macroplace.Placement`), and what
    `macros check`'s rules said about it, in the checker's words."""

    scope: macrocheck.MacroScope
    file: str  # relative to the flavor folder
    position: int  # 1-based, in its file's order
    line: int  # 1-based line of the VER header in its file
    id: str
    name: str
    icon: str
    body_lines: list[str]
    complete: bool  # False: the file ends before this macro's END
    bytes: int
    characters: int
    placement: macroplace.Placement
    findings: list[macrocheck.Finding]
    cannot_judge: list[macrocheck.CannotJudge]


class CharMacrosReport(_Out):
    """`wowlab char macros --json`: every record of the account and character
    `macros-cache.txt`, each with the action slots the candidate numbering
    puts it at and the keys that reach them (`macroplace`, never more than
    `unconfirmed`), and the `macros check` findings for it. `cannot_judge`
    holds what the checker could not judge for any macro."""

    flavor_folder: str
    account: str
    character: str | None  # "<realm folder>/<character folder>"; None: no character chosen
    chosen_by: Literal["--character", "latest"] | None
    files: list[CharMacrosFile]
    bindings_files: list[CharBindsFile]  # where the keys come from, with the record
    record_file: str | None  # the character's WowLab.lua: action bars, bindings, spellbook
    record_mtime_ns: int | None
    record_schema: int | None
    record_error: str | None  # why no WowLab.lua was read
    macros: list[CharMacro]
    cannot_judge: list[macrocheck.CannotJudge]
    # Why a key bound in the client may be missing from the placements' keys
    # (`keymap.KeyMap.keys_incomplete`); None when the bindings record is complete.
    keys_incomplete: str | None
    notes: list[str]


def _char_macros_file(
    lay: layout.Layout, acct: layout.Account, folder: tuple[str, str] | None
) -> tuple[CharMacrosFile, list[macrocheck.SourceMacro]]:
    """The account file (`folder` None) or one character folder's file, read
    as `macros check` reads it."""
    scope: macrocheck.MacroScope = "account" if folder is None else "character"
    who = None if folder is None else f"{folder[0]}/{folder[1]}"
    _, found = _wtf_file(lay, "macros-cache.txt", scope, acct.folder, who)
    if found is None:
        return CharMacrosFile(scope=scope, path=None, mtime_ns=None, macros=0, unknown_lines=0), []
    data = _listed(snapshot.read_regular_file, lay, found.path, limit=_MACROS_LIMIT)
    doc = wtfconfig.parse_macros(data)
    return (
        CharMacrosFile(
            scope=scope,
            path=found.path,
            mtime_ns=found.mtime_ns,
            macros=len(doc.macros),
            unknown_lines=len(doc.unknown_lines()),
        ),
        [macrocheck.SourceMacro(scope=scope, file=found.path, macro=m) for m in doc.macros],
    )


@char_app.command("macros", short_help="One character's macros, their action slots and keys.")
@_handled
def char_macros(
    character: CharacterOpt = None,
    account: AccountOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes_out: NotesOpt = False,
) -> None:
    """The macros of the account and one character's macros-cache.txt, each
    with the action slots it may sit at and the keys that reach them, and
    what macros check finds in it. Which file macro a macro slot names is
    not known, so a slot is only ever "may sit at slot n (unconfirmed)".
    The slots and keys come from the lab-addon record. Without
    --character, the character whose WowLab.lua was written last. Reads
    only.

    \f
    JSON: CharMacrosReport."""
    _, chosen, lay = _open_char(root, flavor, account)
    acct = _select_account(lay, account)
    target, how, record, record_error = _coach_record(lay, acct, character)
    folder: tuple[str, str] | None = None
    if target is not None and target.realm_folder and target.character_folder:
        folder = (target.realm_folder, target.character_folder)
    elif character is not None:
        char = _select_character(acct, character)
        folder = (char.realm_folder, char.folder)
    lab_files = [
        f
        for f in lay.saved_variables()
        if f.account == acct.folder and f.scope == "character" and _is_lab_file(f)
    ]
    if folder is None and lab_files:
        # The latest folder's files could not be told apart (case variants):
        # it is still the character `char show` would take.
        top = max(lab_files, key=lambda f: (f.mtime_ns, f.path))
        if top.realm_folder and top.character_folder:
            folder, how = (top.realm_folder, top.character_folder), "latest"
    unread = folder is not None and any(
        (f.realm_folder, f.character_folder) == folder for f in lab_files
    )
    who = f"{folder[0]}/{folder[1]}" if folder is not None else None
    files: list[CharMacrosFile] = []
    sources: list[macrocheck.SourceMacro] = []
    for where in (None, folder) if folder is not None else (None,):
        found, read = _char_macros_file(lay, acct, where)
        files.append(found)
        sources.extend(read)
    account_bindings, account_doc = _char_binds_file(lay, acct, None)
    bindings_files = [account_bindings]
    character_doc = None
    if folder is not None:
        character_bindings, character_doc = _char_binds_file(lay, acct, folder)
        bindings_files.append(character_bindings)
    no_record = (
        f"no lab-addon record: {record_error}"
        if record_error is not None
        else f"no lab-addon record ({keymap.GET_RECORD})"
    )
    keys = keymap.join_record(
        account=account_doc, character=character_doc, record=record, no_record=no_record
    )
    if isinstance(record, labaddon.CharDBV3):
        bars, no_bars = record.actionbars, f"no action-bar record ({keymap.GET_RECORD})"
    elif record is not None:
        bars = None
        no_bars = f"no action-bar record (schema {record.schema_} has none; {keymap.GET_RECORD})"
    else:
        bars, no_bars = None, no_record
    if record is None and (target is not None or unread):
        # `macros check`'s words for a record it found and could not read.
        place = target.path if target is not None else "the WowLab.lua"
        no_book = f"no spellbook record: {place} was not read"
    elif folder is None:
        no_book = "no spellbook record (no character chosen; use --character)"
    else:
        no_book = macrocheck.NO_RECORD
    checked = macrocheck.check(sources, record, character=who, no_record=no_book)
    positions: dict[macrocheck.MacroScope, int] = {"account": 0, "character": 0}
    order: list[tuple[macroplace.MacroScope, int]] = []
    for s in sources:
        positions[s.scope] += 1
        order.append((s.scope, positions[s.scope]))
    placed = macroplace.place(order, bars, keys.keys, no_bars=no_bars)
    macros: list[CharMacro] = []
    for s, (_, position), placement, counted in zip(
        sources, order, placed, checked.macros, strict=True
    ):
        ref = macrocheck.MacroRef(scope=s.scope, id=s.macro.macro_id, line=counted.line)
        macros.append(
            CharMacro(
                scope=s.scope,
                file=s.file,
                position=position,
                line=counted.line,
                id=s.macro.macro_id,
                name=s.macro.name,
                icon=s.macro.icon,
                body_lines=list(s.macro.body_lines),
                complete=s.macro.complete,
                bytes=counted.bytes,
                characters=counted.characters,
                placement=placement,
                findings=[f for f in checked.findings if f.macro == ref],
                cannot_judge=[c for c in checked.cannot_judge if c.macro == ref],
            )
        )
    report = CharMacrosReport(
        flavor_folder=chosen.folder,
        account=acct.folder,
        character=who,
        chosen_by=how if how is not None else ("--character" if character is not None else None),
        files=files,
        bindings_files=bindings_files,
        record_file=target.path if target else None,
        record_mtime_ns=target.mtime_ns if target else None,
        record_schema=record.schema_ if record is not None else None,
        record_error=record_error,
        macros=macros,
        cannot_judge=[c for c in checked.cannot_judge if c.macro is None],
        keys_incomplete=keys.keys_incomplete,
        notes=[
            macroplace.CANDIDATE_RULE,
            *keys.notes,
            f"Length: counted in {macrocheck.LENGTH_UNIT}; the client's limit is "
            f"{macrocheck.LENGTH_LIMIT}, and whether it counts characters or bytes is [verify].",
            *_MACROS_CHECK_NOTES,
        ],
    )
    if json_out:
        _emit(report)
        return
    _say_char_macros(report, notes_out, _account_words(acct))


def _placement_lines(p: macroplace.Placement, keys_incomplete: str | None) -> list[str]:
    lines = [f"placement: {p.status}: {p.reason}"]
    for s in p.slots:
        lines.append(f"may sit at slot {s.slot} ({p.status}): {s.page_note}")
        reach = [k for k in p.keys if k.slot == s.slot]
        if not reach:
            among = f", among the keys read ({keys_incomplete})" if keys_incomplete else ""
            lines.append(f"  no key reaches it on the page its bar shows by default{among}")
        for k in reach:
            lines.append(f"  key {k.key} ({k.command}; {_source_words(k)})")
    return lines


def _say_char_macros(
    report: CharMacrosReport, show_notes: bool = False, keep: Sequence[str] = ()
) -> None:
    if report.character is None:
        _say(
            f"Character: none in account {report.account} has a {labaddon.LAB_FILE_NAME}; "
            "the account macros only (choose a character with --character)"
        )
    else:
        picked = " (written last; choose another with --character)" * (report.chosen_by == "latest")
        _say(f"Character: {report.character} in account {report.account}{picked}")
    _say_record(
        report.record_file, report.record_error, report.record_schema, report.record_mtime_ns, keep
    )
    for f in report.files:
        label = f"{f.scope.capitalize()} macros"
        if f.path is None or f.mtime_ns is None:
            _say(f"{label}: no macros-cache.txt")
            continue
        unknown = f", {f.unknown_lines} line(s) outside any macro not read" * bool(f.unknown_lines)
        _say(
            f"{label}: {_plural(f.macros, 'macro')} in {f.path}{unknown}, written "
            f"{_local_time(f.mtime_ns)}"
        )
    for b in report.bindings_files:
        label = "Account bindings" if b.scope == "account" else "Character bindings"
        if b.path is None or b.mtime_ns is None:
            _say(f"{label}: no bindings-cache.wtf")
        else:
            _say(f"{label}: {b.path}, written {_local_time(b.mtime_ns)}")
    may_sit = sum(1 for m in report.macros if m.placement.status != "none")
    found = sum(len(m.findings) for m in report.macros)
    _say(
        f"Macros: {len(report.macros)} ({may_sit} with a candidate slot, "
        f"{len(report.macros) - may_sit} with none; {_plural(found, 'finding')})"
    )
    for m in report.macros:
        cut = " (the file ends before this macro's END)" * (not m.complete)
        _say(
            f'  {m.scope} macro {m.position} "{m.name}" (id {m.id}, icon {m.icon}, line {m.line}, '
            f"{_plural(m.characters, 'character')}){cut}"
        )
        for line in m.body_lines:
            _say(f"      {line}")
        for line in _placement_lines(m.placement, report.keys_incomplete):
            # The rule's own words carry the spec's markers (domain F8).
            _say_prose(_player_copy(line), indent="    ", hang="        ", keep=keep)
        for finding in m.findings:
            _say_prose(
                f"finding {finding.rule} (line {finding.line}): {finding.detail}",
                indent="    ",
                hang="        ",
            )
        for c in m.cannot_judge:
            _say_prose(
                f"cannot judge {c.rule} (line {c.line}): {c.reason}", indent="    ", hang="        "
            )
    _say(f"Cannot judge for any macro: {len(report.cannot_judge)}")
    for c in report.cannot_judge:
        _say_prose(f"{c.rule}: {_player_copy(c.reason)}", indent="  ", hang="    ", keep=keep)
    # The one caveat: with a slot shown, that every slot is a candidate only;
    # otherwise that macros-cache.txt may be stale or replaced by the server,
    # so "0 macros" may be wrong (domain review, M16-16).
    caveat = macroplace.CANDIDATE_RULE if may_sit else _MACRO_FILE_TEXT
    _say_notes(_macro_notes(report.notes), show_notes, caveat, keep=keep)


# ─── char history (M17-02) ───────────────────────────────────────────────────


class CharHistoryReport(_Out):
    """`wowlab char history --json`: one character over time, one row per
    distinct save of its WowLab.lua (by the file's modification time), oldest
    first, from every snapshot in the store and the file in the install;
    each row with what changed from the row before. Copies that could not be
    read, and snapshots whose manifest does not load, are listed with the
    reason."""

    flavor_folder: str
    account: str
    character: str  # "<realm folder>/<character folder>"
    chosen_by: Literal["--character", "latest"]
    file: str  # relative to the flavor folder
    # The installed client's version: the build whose cached tables named
    # specs and currencies (None when the install has none).
    names_build: str | None
    rows: list[charhistory.HistoryRow]
    unreadable: list[charhistory.UnreadableCopy]
    damaged_snapshots: list[snapshot.InvalidManifest]
    snapshots_searched: int
    snapshots_holding: int
    notes: list[str]


_HISTORY_NOTES = [
    *_CHAR_NOTES,
    "One row per save: copies with the same save time are one record, however many "
    "snapshots hold it. A restore gives the file a new time, so a restored record shows "
    "again as a later row, marked with the save whose contents it has.",
    "Item names are each link's bracketed text, and item levels and the average are the "
    "client's figures as recorded; nothing is fetched.",
    "A gear change is a slot whose item differs: another item id; the same item id as a "
    "different instance (another enchant, gems, bonus ids or modifiers in its link); or the "
    "same instance whose item level, the client's figure, changed. A link also carries the "
    "character's level and spec, and its name and colour are display text; none of these is "
    "counted as a change to the item.",
    "Currencies are what the currency panel listed through your filter and collapsed "
    "headers at that save; a currency under a collapsed header is not listed, so a row "
    "says when the panel was read differently.",
    "Spec and currency names come from game-data tables already downloaded for the "
    "installed client (wowlab db2 fetch), even for older saves; without them the id is "
    "shown.",
]
_HISTORY_FEW = (
    "To see this character change over time, take a snapshot after you play "
    "(wowlab snap create): each logout or /reload saves a new record, and each snapshot "
    "keeps the one on disk."
)


def _history_names(
    version: str | None,
) -> tuple[Callable[[int, int | None], str | None], Callable[[int], str | None]]:
    """Spec names (by the body type, as `char show` and `char gear` name
    them) and currency names from the tables cached for `version`."""
    cache: dict[tuple[str, int, int | None], str | None] = {}

    def lookup(table: str, female: str, row_id: int, sex: int | None) -> str | None:
        if version is None:
            return None
        key = (table, row_id, sex)
        if key not in cache:
            cache[key] = _cached_name(table, female, version, row_id, sex)
        return cache[key]

    def spec(row_id: int, sex: int | None) -> str | None:
        table, female = _NAME_TABLES["spec"]
        return lookup(table, female, row_id, sex)

    def currency(row_id: int) -> str | None:
        return lookup("CurrencyTypes", "Name_lang", row_id, None)

    return spec, currency


def _history_item(item: charhistory.HistoryItem | None) -> str:
    if item is None:
        return "empty"
    level = (
        "item level not returned" if item.item_level is None else f"item level {item.item_level}"
    )
    return f"{item.name} ({level})"


def _history_head(row: charhistory.HistoryRow) -> str:
    level = f"level {row.level}" if row.level is not None else "level not recorded"
    if row.item_level is not None:
        ilvl = f"equipped item level {row.item_level}"
    elif row.item_level_absent == "the client returned no equipped average":
        ilvl = "equipped item level not returned"
    else:
        ilvl = "equipped item level not recorded"
    spec = row.spec
    if spec.id is None:
        spec_words = "spec not recorded"
    elif row.spec_name:
        spec_words = f"spec {row.spec_name} (spec id {spec.id})"
    else:
        spec_words = f"spec id {spec.id}"
    return f"{_local_time(row.saved_at_ns)}  {level}, {ilvl}, {spec_words}"


def _gear_change_words(g: charhistory.GearChange) -> str:
    slot = f"slot {g.slot} {g.slot_name}" if g.slot_name else f"slot {g.slot}"
    if g.before is not None and g.after is not None and g.differs != ["item id"]:
        parts = [d for d in g.differs if d != "item level"]
        level = (
            f"item level {_num_words(g.before.item_level)} to "
            f"{_num_words(g.after.item_level)} (the client's figure)"
            if "item level" in g.differs
            else None
        )
        if parts:
            what = (
                f"{_history_item(g.after)}, same item id, a different instance "
                f"({', '.join(parts)}); before: {_history_item(g.before)}"
            )
            if level is not None:
                what += f"; {level}"
        else:
            what = f"{g.after.name}, same instance, {level}"
    else:
        what = f"{_history_item(g.before)} to {_history_item(g.after)}"
    return f"  gear: {slot}: {what}"


def _currency_value(n: int | None) -> str:
    return "not listed" if n is None else str(n)


def _history_lines(row: charhistory.HistoryRow) -> list[str]:
    where: list[str] = []
    if row.snapshots:
        where.append(f"{_plural(len(row.snapshots), 'snapshot')} ({', '.join(row.snapshots)})")
    if row.live:
        where.append("the file in the install")
    lines = [f"  from {' and '.join(where)}"]
    if row.same_bytes_as_ns is not None:
        lines.append(
            f"  same contents as the save of {_local_time(row.same_bytes_as_ns)}: likely "
            "restored, so the time is when it was put back"
        )
    if row.changed_from_ns is None:
        lines.append("  the oldest record wowlab has")
        return lines
    if row.gear_not_compared is not None:
        lines.append(f"  gear not compared: {row.gear_not_compared}")
    lines += [_gear_change_words(g) for g in row.gear_changes]
    if row.currencies_not_compared is not None:
        lines.append(f"  currencies not compared: {row.currencies_not_compared}")
    for c in row.currency_changes:
        name = f"{c.name} (currency {c.id})" if c.name else f"currency {c.id}"
        lines.append(
            f"  currency: {name}: {_currency_value(c.before)} to {_currency_value(c.after)}"
        )
    if row.currency_panel_changed is not None:
        lines.append(f"  {row.currency_panel_changed}")
    if (
        not row.gear_changes
        and not row.currency_changes
        and row.gear_not_compared is None
        and row.currencies_not_compared is None
    ):
        lines.append("  no gear or currency changes")
    return lines


@char_app.command("history", short_help="One character over time, from your snapshots.")
@_handled
def char_history(
    character: CharacterOpt = None,
    account: AccountOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes_out: NotesOpt = False,
) -> None:
    """One character over time: the WowLab.lua every snapshot holds, and the
    one in the install, one row per save with its level, equipped item level
    and spec, and the gear and currencies that changed from the save before.
    Without --character, the character whose WowLab.lua was written last.
    A snapshot whose copy cannot be read is listed with the reason. Reads
    only; take snapshots with `wowlab snap create` to build history.

    \f
    JSON: CharHistoryReport."""
    _, chosen, lay = _open_char(root, flavor, account)
    acct = _select_account(lay, account)
    files = [f for f in lay.saved_variables() if f.account == acct.folder and _is_lab_file(f)]
    target, how, tie = _lab_char_file(acct, files, character)
    try:
        table = gear.load_slot_table()
    except gear.SlotTableError as exc:
        raise CliError(f"the slot table cannot be used: {exc}") from exc

    live_path = lay.flavor_path / target.path
    # The save time and the bytes from one file: its lstat now, and the read
    # refused if the file opened is not that one (a logout replacing it in
    # between), as the survey reads it. When the lstat fails, the read says why.
    try:
        live_st: os.stat_result | None = os.lstat(live_path)
    except OSError:
        live_st = None
    live_ns = live_st.st_mtime_ns if live_st is not None else target.mtime_ns

    def read_live() -> bytes:
        try:
            return snapshot.read_regular_file(
                live_path, limit=luadata.MAX_FILE_BYTES, expect=live_st, root=lay.install_root
            )
        except snapshot.LinkedPathError as exc:
            raise exc.changed_while_read(lay.flavor_path) from exc

    spec_name, currency_name = _history_names(chosen.version)
    found = charhistory.collect(
        snapshot.SnapshotStore(),
        f"{chosen.folder}/{target.path}",
        live=(live_ns, read_live),
        table=table.table,
        spec_name=spec_name,
        currency_name=currency_name,
    )
    report = CharHistoryReport(
        flavor_folder=chosen.folder,
        account=acct.folder,
        character=f"{target.realm_folder}/{target.character_folder}",
        chosen_by=how,
        file=target.path,
        names_build=chosen.version,
        rows=found.rows,
        unreadable=found.unreadable,
        damaged_snapshots=found.damaged_snapshots,
        snapshots_searched=found.snapshots_searched,
        snapshots_holding=found.snapshots_holding,
        notes=_HISTORY_NOTES,
    )
    if json_out:
        _emit(report)
    else:
        _say_char_history(report, acct, how, tie, notes_out)
    if not report.rows:
        raise typer.Exit(EXIT_ERROR)


def _history_summary(report: CharHistoryReport) -> str:
    """The line above the rows: how many saves, and where they came from:
    only the copies that made a row (a copy that could not be read is
    listed below the rows, not counted here)."""
    n = len(report.rows)
    if not n:
        return "No save of this character could be read."
    read = len({s for r in report.rows for s in r.snapshots})
    where = f"from {read} of {_plural(report.snapshots_searched, 'snapshot')}"
    if any(r.live for r in report.rows):
        where += " and the file in the install"
    elif any(u.where == "install" for u in report.unreadable):
        where += " (the install's own copy could not be read; see below)"
    return f"{_plural(n, 'save')}, {where}, oldest first:"


def _say_char_history(
    report: CharHistoryReport,
    acct: layout.Account,
    how: Literal["--character", "latest"],
    tie: str,
    notes_out: bool,
) -> None:
    held = [*_account_words(acct), report.file]
    _say_prose(
        f"Character: {report.character} in account {acct.folder}{_picked_words(how, tie)}",
        keep=held,
    )
    n = len(report.rows)
    _say(_history_summary(report))
    for row in report.rows:
        _say(_history_head(row))
        for line in _history_lines(row):
            _say(line)
    if report.unreadable:
        bad_count = len(report.unreadable)
        copies = "1 copy" if bad_count == 1 else f"{bad_count} copies"
        _say(f"Could not read {copies}" + (" (the others were read):" if n else ":"))
    for bad in report.unreadable:
        where = (
            "the file in the install" if bad.where == "install" else f"snapshot {bad.snapshot_id}"
        )
        _say(f"  {where}: {bad.reason}")
    if report.damaged_snapshots:
        _say(
            f"{_plural(len(report.damaged_snapshots), 'snapshot')} did not load, so wowlab "
            "cannot tell whether they hold this character:"
        )
    for damaged in report.damaged_snapshots:
        _say(f"  snapshot file {damaged.name} does not load: {damaged.reason}")
    caveat = _HISTORY_FEW if n < 2 else None
    _say_notes(report.notes, notes_out, caveat, keep=held)
