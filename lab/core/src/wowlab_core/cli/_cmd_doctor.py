"""`wowlab doctor`: its checks, its first run, a failure wowlab cannot work out
(ADR-0037) and its next steps (M16-111)."""

import hashlib
import os
import shlex
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import Field

from wowlab_core import (
    addoninstall,
    combatlog,
    doctorredact,
    firstrun,
    gamedata,
    guard,
    install,
    labaddon,
    lastbackup,
    layout,
    longpath,
    process,
    snapshot,
    wtfconfig,
)
from wowlab_core.cli._cmd_log import (
    _logs_dir,
)
from wowlab_core.cli._common import (
    _BLOCKED_PLACE,
    EXIT_ERROR,
    EXIT_REFUSED,
    EXIT_USAGE,
    CliError,
    JsonOpt,
    NotesOpt,
    RootOpt,
    _client_names,
    _client_running,
    _discover,
    _emit,
    _given_flavor,
    _handled,
    _listed,
    _local_time,
    _no_flavors,
    _not_taken_lines,
    _note,
    _Out,
    _plural,
    _powershell_join,
    _roots,
    _safe,
    _say,
    _say_err,
    _say_notes,
    _say_prose,
    _select_flavor,
    _shell,
    app,
)

# ─── doctor ──────────────────────────────────────────────────────────────────


class UncheckedLocation(_Out):
    location: str
    reason: str


class Locations(_Out):
    """Where discovery looked when no install was named."""

    searched: list[str]
    could_not_check: list[UncheckedLocation]


class ClientReport(_Out):
    # `running` and `unknown` are both treated as running by the write gate.
    state: Literal["running", "unknown", "not_running", "could_not_check"]
    processes: list[process.ClientProcess]
    error: str | None


class GameDataReport(_Out):
    # `published`: the source lists this flavor's version; only `version` is
    # stated, since a match may come from another product's listing.
    status: Literal["published", "not_published", "no_version", "not_checked", "error"]
    version: str | None
    detail: str | None


class SyncCVar(_Out):
    file: str  # relative to the flavor folder
    account: str | None
    name: str
    # None: no line in this file. That is not "off" and not a known default:
    # a client may read such a setting as on, or may not have it at all
    # (docs/LAB_FORMATS.md §5, §7, amended 2026-11-12).
    value: str | None


class DoctorFlavor(_Out):
    folder: str
    product: str
    version: str | None
    build: int | None
    matching_rows: int
    game_data: GameDataReport
    sync_cvars: list[SyncCVar]
    # The installed lab-addon's ## Interface: against this client's (M16-06).
    lab_addon: addoninstall.LabAddonState


# What a file the rollback left holds as `doctor` reads it now (M16-84):
# the change's own bytes (`after`), what was there before it (`before`),
# anything else, or unknown (the record's flavor is not one doctor found, or
# the file could not be read). `not_written`: the change never wrote it (its
# only operation was refused), so undo leaves it as it is.
LeftNow = Literal["wowlab_change", "before_change", "changed_since", "unknown", "not_written"]


class LeftPath(_Out):
    path: str
    now: LeftNow
    # Whether the file is there now; None when doctor did not read it
    # (`unknown`, `not_written`).
    exists: bool | None


class LastTransaction(_Out):
    id: str
    created_at: str
    label: str
    # The write gate's closed set of states, so `doctor --redact` can keep it
    # as-is: a state names nobody (M16-85).
    state: guard._State
    paths: int
    # How many folders the change created (M16-84): with no `paths`, undo
    # still has those to remove, and records itself.
    created_dirs: int
    # The last name of the record's flavor folder, as journaled (M16-84).
    flavor_folder: str
    # The files the rollback of a `rollback_incomplete` record left as they
    # are, relative to the flavor folder, sorted (M16-84); empty otherwise,
    # and for a record written before the journal listed them.
    paths_left: list[LeftPath]
    # False when the change stopped before its pre-write snapshot, so it
    # changed no file (2026-11-11 (a)).
    snapshot_taken: bool = True
    # How many leftover temporary files of earlier changes it removed.
    temps_removed: int = 0
    # The ids of the newer records `undo` passes over, oldest first: changes
    # that stopped before they changed any file and were closed (2026-11-11
    # (a1), (a2)), so this record is the change to undo. Empty when this
    # record is the newest.
    passed_over: list[str] = Field(default_factory=list)


class StoreReport(_Out):
    path: str
    exists: bool
    snapshots: int
    damaged: list[snapshot.InvalidManifest]
    transactions: int | None
    last_transaction: LastTransaction | None
    journal_error: str | None
    # Who besides the player can open the store (M16-41); not checked on Windows.
    exposure: snapshot.StoreExposure


# Why wowlab cannot work out safely what a change did (ADR-0037): the
# journal does not load, or the pre-write snapshot of the change `undo`
# would act on cannot be read. `undo` refuses either and changes nothing.
CannotWorkOutCause = Literal["journal_unreadable", "snapshot_unreadable"]


class CannotWorkOut(_Out):
    """`doctor`'s one problem for a failure wowlab cannot work out (ADR-0037):
    the snapshots are kept, and the newest whole one is offered for a
    restore the player inspects first."""

    cause: CannotWorkOutCause
    # The journal record whose pre-write snapshot cannot be read; None when
    # the journal itself does not load.
    record_id: str | None
    # The newest snapshot `snap verify` finds whole (it loads, every object
    # it names is there and re-hashes to its name) and that was taken of a
    # flavor folder of the install `doctor` checked, so `snap restore` can
    # plan it there; of the change's own flavor when it has one. None when
    # there is none.
    snapshot_id: str | None
    # The command the problem ends with, with the caller's --root as given;
    # None with no whole snapshot.
    command: str | None


# What each "Next steps" item of `doctor` is about (M16-37).
# `backup` (M17-04) is listed only when the game updated since the newest
# backup of the flavor (`lastbackup`), as `stale`.
NextStepItem = Literal["install", "lab_addon", "characters", "combat_log", "backup"]
# `ok`: nothing to do; `missing`: not there yet; `stale`: the lab-addon is
# made for another client version, or the newest backup was taken on
# another one; `unknown`: wowlab cannot tell.
NextStepState = Literal["ok", "missing", "stale", "unknown"]


class NextStep(_Out):
    """One item of `doctor`'s "Next steps" (M16-37). The text output says
    each item from these fields, so the two never disagree."""

    item: NextStepItem
    # The flavor folder the item is about; None for `install`.
    flavor_folder: str | None
    state: NextStepState
    # `characters`: the character folders holding a lab-addon record;
    # `combat_log`: the combat logs under Logs/; None otherwise.
    count: int | None = None
    # `combat_log`: the newest log's modification time; None otherwise.
    newest_mtime_ns: int | None = None
    # The wowlab command the item names, quoted for this machine's shell,
    # with the caller's --root and, on an install with several flavors,
    # --flavor: the reinstall for `lab_addon`, `char list` for `characters`.
    command: str | None = None
    # What else the item says, in the player's words: why its state is
    # `unknown` or `stale`, or what a missing lab-addon means for the records.
    detail: str | None = None
    # The one command or in-game action that fixes it, as the text says it;
    # None when there is nothing to do or nothing wowlab can name.
    fix: str | None = None


class DoctorReport(_Out):
    """`wowlab doctor --json`."""

    install_root: str | None
    found_by: str | None
    install_error: str | None
    locations: Locations | None
    flavors: list[DoctorFlavor]
    other_dirs: list[str]
    client: ClientReport | None
    store: StoreReport
    problems: list[str]
    # Present on a first run only: the store held no snapshot of this install
    # (M13-04, `wowlab_core.firstrun`).
    first_run: firstrun.FirstRunReport | None = None
    # `Install.not_taken`: each name of `other_dirs` with why it was not
    # taken (M13-45). Left out of the JSON when empty, as there.
    not_taken: list[install.NotTaken] = Field(default_factory=list, exclude_if=lambda v: not v)
    # The root given is a folder that holds no .build.info, is not a flavor
    # folder, and has no install above it (`_could_be_an_install`): the text
    # names the Battle.net app's Scan and Repair (M16-13). Left out of the
    # JSON when false.
    build_info_missing: bool = Field(default=False, exclude_if=lambda v: not v)
    # What to do next (M16-37): the install when none was found, else for
    # each flavor the lab-addon, the characters recorded and a combat log.
    next_steps: list[NextStep] = Field(default_factory=list)
    # A failure wowlab cannot work out safely (ADR-0037): the journal does
    # not load, or the change undo would act on has no readable pre-write
    # snapshot. Its one entry of `problems` says the same. Left out of the
    # JSON when there is none.
    cannot_work_out: CannotWorkOut | None = Field(default=None, exclude_if=lambda v: v is None)


# The CVars that decide whether the server may replace local binds, macros
# and settings at login (docs/LAB_FILE_MAP.md, timing rules).
_SYNC_CVARS = ("synchronizeBindings", "synchronizeMacros", "synchronizeConfig")


@contextmanager
def _open_gamedata() -> Iterator[gamedata.GameData]:
    """The game-data client (the only network client, ADR-0022). Tests replace
    this with one that replays recordings."""
    source = gamedata.WagoSource()
    try:
        yield gamedata.GameData(source)
    finally:
        source.close()


def _sync_cvars(lay: layout.Layout) -> list[SyncCVar]:
    out: list[SyncCVar] = []
    for f in lay.wtf_files():
        name = f.name.casefold()
        is_machine = f.scope == "machine" and name == "config.wtf"
        is_account = f.scope == "account" and name == "config-cache.wtf"
        if not (is_machine or is_account):
            continue
        doc = _listed(wtfconfig.read_config, lay, f.path)
        for cvar in _SYNC_CVARS:
            found = doc.get(cvar)
            out.append(
                SyncCVar(
                    file=f.path,
                    account=f.account,
                    name=found.name if found else cvar,
                    value=found.value if found else None,
                )
            )
    return out


def _no_version_cause(folder: str, product: str, matching_rows: int) -> str:
    """Why a flavor's version is unknown, in one clause, in the three cases
    `_unknown_version` tells apart (M16-118): no product, no `.build.info`
    row for it, a row with a blank version. `game_data.detail` in --json and
    the `db2 --build` refusal both use it (M16-121)."""
    if not product:
        return f"{folder}'s .flavor.info gives no product code"
    if matching_rows >= 1:
        return f"the .build.info row for product {product} has no version in it"
    return f".build.info has no row for product {product}"


def _game_data(flavors: Sequence[install.Flavor], check_builds: bool) -> dict[str, GameDataReport]:
    reports: dict[str, GameDataReport] = {}
    pending: list[install.Flavor] = []
    for f in flavors:
        if f.version is None:
            reports[f.folder] = GameDataReport(
                status="no_version",
                version=None,
                detail=_no_version_cause(f.folder, f.product, f.matching_rows),
            )
        elif not check_builds:
            reports[f.folder] = GameDataReport(
                status="not_checked", version=f.version, detail="no --check-builds"
            )
        else:
            pending.append(f)
    if not pending:
        return reports
    try:
        with _open_gamedata() as data:
            for f in pending:
                try:
                    build = data.resolve_build(f)
                except gamedata.BuildNotPublished:
                    reports[f.folder] = GameDataReport(
                        status="not_published", version=f.version, detail=None
                    )
                else:
                    # Only `.version` is a fact about the install: a match may
                    # come from another product's listing (§6.6 amendment).
                    reports[f.folder] = GameDataReport(
                        status="published", version=build.version, detail=None
                    )
    except (gamedata.GameDataError, OSError) as exc:
        for f in pending:
            reports.setdefault(
                f.folder, GameDataReport(status="error", version=f.version, detail=str(exc))
            )
    return reports


def _client_report(inst: install.Install) -> ClientReport:
    try:
        names = sorted({n for f in inst.flavors for n in _client_names(f.path)})
        clients = process.running_clients(
            _roots(inst),
            flavor_folders=[f.folder for f in inst.flavors],
            extra_names=names,
        )
    except Exception as exc:  # the write gate treats any probe failure as running
        return ClientReport(
            state="could_not_check", processes=[], error=f"{type(exc).__name__}: {exc}"
        )
    state: Literal["running", "unknown", "not_running"] = "not_running"
    if any(c.state is process.ClientState.RUNNING for c in clients):
        state = "running"
    elif clients:
        state = "unknown"
    return ClientReport(state=state, processes=list(clients), error=None)


def _left_paths(r: guard.HistoryRecord, inst: install.Install | None) -> list[LeftPath]:
    """Each file the rollback of `r` left, with what it holds now. Reads
    only, and only inside the install doctor found, when the record's flavor
    folder is one of its flavors; the journal's own path is never opened."""
    written = {p.path: p for p in r.paths}
    folder = _journaled_flavor(r, inst)
    out: list[LeftPath] = []
    for rel in sorted(r.paths_left):
        change = written.get(rel)
        now: LeftNow
        exists: bool | None = None
        if change is None:
            now = "not_written"
        elif folder is None:
            now = "unknown"
        else:
            read, digest = _hash_now(folder[0], folder[0] / folder[1] / rel)
            if not read:
                now = "unknown"
            else:
                exists = digest is not None
                if digest == change.after:
                    now = "wowlab_change"
                elif digest == change.before:
                    now = "before_change"
                else:
                    now = "changed_since"
        out.append(LeftPath(path=rel, now=now, exists=exists))
    return out


def _journaled_flavor(
    r: guard.HistoryRecord, inst: install.Install | None
) -> tuple[Path, str] | None:
    """The resolved install root and the flavor folder name of the flavor
    `r` was written to, when it is one of `inst`'s (resolved as the write
    gate resolves a flavor before it journals it); else None."""
    if inst is None:
        return None
    journaled = Path(r.flavor_path)
    for f in inst.flavors:
        try:
            real = longpath.resolve(inst.root / f.folder, strict=True)
        except (OSError, RuntimeError):
            continue
        if real == journaled:
            return real.parent, real.name
    return None


def _hash_now(root: Path, path: Path) -> tuple[bool, str | None]:
    """(read, SHA-256 hex or None for absent) of `path` under `root`,
    opened as the snapshot reads an install (no link followed, regular file
    only); (False, None) when it cannot be read."""
    try:
        fd, _ = snapshot.open_regular_file_from_root(path, root)
    except FileNotFoundError:
        return True, None
    except (OSError, ValueError):
        return False, None
    try:
        hasher = hashlib.sha256()
        while chunk := os.read(fd, 1 << 20):
            hasher.update(chunk)
    except OSError:
        return False, None
    finally:
        os.close(fd)
    return True, hasher.hexdigest()


def _read_left(store: StoreReport, inst: install.Install | None) -> StoreReport:
    """`store` with what each file its last record's rollback left holds
    now, read in the install doctor found (M16-84). `_store_report` knows no
    install, so it says those it wrote as `unknown`; the journal is read
    again, and a record that is no longer the last one is left as it was."""
    t = store.last_transaction
    if t is None or not t.paths_left or inst is None:
        return store
    try:
        records = guard.history()
    except guard.GuardError:
        return store
    target = guard._undo_target(records)
    if target is None or target.id != t.id:
        return store
    left = _left_paths(target, inst)
    return store.model_copy(update={"last_transaction": t.model_copy(update={"paths_left": left})})


def _store_report() -> StoreReport:
    store = snapshot.SnapshotStore()
    listing = store.list_lenient()
    transactions: int | None = None
    last: LastTransaction | None = None
    journal_error: str | None = None
    try:
        records = guard.history()
    except guard.GuardError as exc:
        journal_error = str(exc)
    else:
        transactions = len(records)
        # The change `undo` acts on (2026-11-11 (a1)), not always the newest.
        r = guard._undo_target(records)
        if r is not None:
            last = LastTransaction(
                id=r.id,
                created_at=r.created_at,
                label=r.label,
                state=r.state,
                paths=len(r.paths),
                created_dirs=len(r.created_dirs),
                flavor_folder=Path(r.flavor_path).name,
                paths_left=_left_paths(r, None),
                snapshot_taken=bool(r.snapshot_id),
                temps_removed=len(r.temps_removed),
                passed_over=[k.id for k in guard._passed_over_after_target(records)],
            )
    return StoreReport(
        path=str(store.path),
        exists=store.path.is_dir(),
        snapshots=len(listing.manifests),
        damaged=list(listing.invalid),
        transactions=transactions,
        last_transaction=last,
        journal_error=journal_error,
        exposure=store.exposure(),
    )


def _doctor(
    root: Path | None,
    check_builds: bool,
    on_found: Callable[[install.Install | None], None] | None = None,
) -> DoctorReport:
    """The doctor report. `on_found` is called once discovery has finished
    (with None when no install was found), before any network request.
    Without `check_builds` no request is made at all (M16-07)."""
    problems: list[str] = []
    inst: install.Install | None = None
    found_by: str | None = None
    install_error: str | None = None
    build_info_missing = False
    locations: Locations | None = None
    try:
        inst, found_by = _discover(root)
    except install.InstallNotFoundError as exc:
        install_error = (
            "no install found at the default locations that could be checked"
            if exc.unchecked
            else "no install found at the default locations"
        )
        locations = Locations(
            searched=[str(p) for p in exc.searched],
            could_not_check=[
                UncheckedLocation(location=str(p), reason=why)
                for p, why in zip(exc.unchecked, exc.unchecked_reasons, strict=True)
            ],
        )
        problems.append(install_error)
    except install.InstallError as exc:
        install_error = str(exc)
        problems.append(install_error)
        build_info_missing = (
            isinstance(exc, install.NotAnInstallError)
            and exc.reason == "missing"
            and exc.root_exists
            and not exc.hint
            and _could_be_an_install(exc.root)
        )
    if on_found is not None:
        on_found(inst)

    flavors: list[DoctorFlavor] = []
    client: ClientReport | None = None
    steps: list[NextStep] = []
    # The --root the player gave, as given, for the commands the next
    # steps name; None when the install was found without one.
    given_root = str(root) if root is not None and str(root) != "" else None
    if inst is None:
        steps.append(_install_step(build_info_missing))
    else:
        game = _game_data(inst.flavors, check_builds)
        several = len(inst.flavors) > 1
        try:
            manifests, imported = lastbackup.store_listing(snapshot.SnapshotStore())
        except (OSError, snapshot.SnapshotError):
            # The store's own section says what is wrong with it.
            manifests, imported = (), frozenset()
        for f in inst.flavors:
            lay = layout.Layout.for_flavor(f, inst.root)
            try:
                sync = _sync_cvars(lay)
            except (OSError, wtfconfig.WtfLimitError) as exc:
                problems.append(f"{f.folder}: cannot read the config files: {exc}")
                sync = []
            lab_addon = addoninstall.lab_addon_state(f)
            flavors.append(
                DoctorFlavor(
                    folder=f.folder,
                    product=f.product,
                    version=f.version,
                    build=f.build,
                    matching_rows=f.matching_rows,
                    game_data=game[f.folder],
                    sync_cvars=sync,
                    lab_addon=lab_addon,
                )
            )
            steps += _flavor_steps(lay, f.folder, lab_addon, several, given_root)
            latest = lastbackup.latest_backup(manifests, inst.root, f.folder, imported)
            if lastbackup.game_updated(latest, f.version):
                steps.append(_backup_step(f.folder, several, given_root, latest, f.version))
        if not inst.flavors:
            # With folders not taken, the refusal's words, each reason
            # included, so the text and `--json` say why (M13-56).
            problems.append(
                _no_flavors(inst) if inst.not_taken else f"{inst.root} has no flavor folders"
            )
        client = _client_report(inst)

    store = _read_left(_store_report(), inst)
    if store.damaged:
        problems.append(f"{len(store.damaged)} damaged snapshot manifest(s)")
    unsure: CannotWorkOut | None = None
    if store.journal_error:
        unsure = _cannot_work_out("journal_unreadable", None, inst, given_root)
        problems.append(_cannot_work_out_problem(unsure))
    elif store.last_transaction is not None and _snapshot_unreadable(store.last_transaction):
        t = store.last_transaction
        unsure = _cannot_work_out("snapshot_unreadable", t, inst, given_root)
        problems.append(_cannot_work_out_problem(unsure, t))
    elif store.last_transaction is not None and store.last_transaction.state == "open":
        t = store.last_transaction
        problems.append(
            _interrupted(t.id, t.passed_over)
            if t.snapshot_taken
            else _stopped_before_any_file(t.id, removed=t.temps_removed > 0)
        )
    elif (
        store.last_transaction is not None and store.last_transaction.state == "rollback_incomplete"
    ):
        t = store.last_transaction
        problems.append(
            _rollback_unfinished(
                t.id,
                t.paths_left,
                t.flavor_folder,
                paths=t.paths,
                created_dirs=t.created_dirs,
                stopped=t.passed_over,
            )
        )
    return DoctorReport(
        install_root=str(inst.root) if inst is not None else None,
        found_by=found_by,
        install_error=install_error,
        locations=locations,
        flavors=flavors,
        other_dirs=list(inst.other_dirs) if inst is not None else [],
        client=client,
        store=store,
        problems=problems,
        not_taken=list(inst.not_taken) if inst is not None else [],
        build_info_missing=build_info_missing,
        next_steps=steps,
        cannot_work_out=unsure,
    )


# ─── doctor: a failure wowlab cannot work out (ADR-0037) ─────────────────────


def _offers_undo(t: LastTransaction) -> bool:
    """Whether `doctor` would offer `wowlab undo --expect` for `t` and that
    undo would restore from its pre-write snapshot: an `open` record that
    took one, or a `rollback_incomplete` record with files or folders to put
    back. Every other record needs no snapshot read (or no undo at all)."""
    if t.state == "open":
        return t.snapshot_taken
    if t.state == "rollback_incomplete":
        return bool(t.paths or t.created_dirs)
    return False


def _snapshot_unreadable(t: LastTransaction) -> bool:
    """Whether the change `undo` would act on needs its pre-write snapshot
    and that snapshot cannot be read, as `undo` reads it (by id, from the
    store): its manifest is missing or does not load, or a file the change
    replaced is not in it as the journal names it, or that file's object is
    missing or does not re-hash to its name. `undo` refuses such a record
    and changes nothing. Reads only."""
    if not _offers_undo(t):
        return False
    try:
        records = guard.history()
    except guard.GuardError:
        return False  # said as the journal's own problem
    target = guard._undo_target(records)
    if target is None or target.id != t.id or not target.snapshot_id:
        return False
    store = snapshot.SnapshotStore()
    try:
        manifest = store.show(target.snapshot_id)
        # As `undo` reads it (`guard._undo_record`): each file the change
        # replaced must be in the snapshot, as the journal names it, and its
        # object present and sound, before anything is written.
        prefix = Path(target.flavor_path).name + "/"
        for item in target.paths:
            if item.before is None:
                continue
            entry = manifest.entry(prefix + item.path)
            if entry is None or entry.kind != "file" or entry.sha256 != item.before:
                return True
            store.read_object(entry.sha256, size=entry.size)
    except (snapshot.SnapshotError, OSError):
        return True
    return False


def _newest_whole_snapshot(inst: install.Install, prefer: str | None) -> snapshot.Manifest | None:
    """The newest snapshot `snap verify` finds whole (its manifest loads,
    every object it names is in the store and re-hashes to its name) that
    `snap restore` would plan in the install `doctor` checked: taken of one
    of this install's flavor folders, in this install, as the write gate
    refuses a snapshot of another install. With `prefer` (the flavor folder
    of the change), the newest of that flavor's snapshots when there is one.
    None when there is none, or the store cannot be checked. Reads only;
    re-hashes every object, so it runs only for this problem."""
    store = snapshot.SnapshotStore()
    try:
        verified = store.verify()
        listing = store.list_lenient()
    except (snapshot.SnapshotError, OSError):
        return None
    corrupt = set(verified.corrupt_objects)
    if snapshot.OBJECTS_DIR_ENTRY in corrupt:
        return None  # nothing under objects/ was checked
    missing = {m.snapshot_id for m in verified.missing_objects}
    folders = {f.folder for f in inst.flavors}
    whole: list[snapshot.Manifest] = []
    for manifest in listing.manifests:  # oldest first
        if manifest.flavor_folder is None or manifest.flavor_folder not in folders:
            continue
        if not guard._local_absolute(manifest.install_root) or not guard._same_dir(
            Path(manifest.install_root), inst.root
        ):
            continue  # another install's: `snap restore` here refuses it
        if manifest.id in missing:
            continue
        if any(e.sha256 is not None and e.sha256 in corrupt for e in manifest.entries):
            continue
        whole.append(manifest)
    preferred = [m for m in whole if m.flavor_folder == prefer]
    for pick in (preferred, whole):
        if pick:
            return pick[-1]
    return None


def _cannot_work_out(
    cause: CannotWorkOutCause,
    record: LastTransaction | None,
    inst: install.Install | None,
    given_root: str | None,
) -> CannotWorkOut:
    """The answer for a failure wowlab cannot work out. The command offered
    is one the player can run as printed: a snapshot of the install `doctor`
    checked, with the caller's --root as given, quoted as `doctor`'s next
    steps quote it. With no install found there is none to offer."""
    whole = (
        None
        if inst is None
        else _newest_whole_snapshot(inst, record.flavor_folder if record is not None else None)
    )
    if whole is None or whole.flavor_folder is None or inst is None:
        return CannotWorkOut(
            cause=cause,
            record_id=record.id if record is not None else None,
            snapshot_id=None,
            command=None,
        )
    # No --flavor: `snap restore` takes the flavor from the snapshot.
    command = _wowlab_command(
        ["snap", "restore", whole.id, "--dry-run"], whole.flavor_folder, False, given_root
    )
    return CannotWorkOut(
        cause=cause,
        record_id=record.id if record is not None else None,
        snapshot_id=whole.id,
        command=command,
    )


def _cannot_work_out_problem(c: CannotWorkOut, t: LastTransaction | None = None) -> str:
    """The `doctor` problem for a failure wowlab cannot work out (ADR-0037):
    what it is, that wowlab cannot work out safely what the change did and
    undo changes nothing, that the snapshots are kept, and the newest whole
    one with the command to inspect its restore, last. With no whole
    snapshot it says so and offers no command."""
    if c.cause == "journal_unreadable" or t is None:
        lead = (
            "the journal of the write gate cannot be read, so wowlab cannot work out safely "
            "what its last change did"
        )
    else:
        lead = (
            f"{_subject(t.id, t.passed_over)} {_journal_state_words(t.state)} (record {t.id}), "
            "and the snapshot wowlab took before it cannot be read, so wowlab cannot work out "
            "safely what this change did"
        )
    lead += "; undo refuses and changes nothing. Your snapshots are kept"
    if c.snapshot_id is None or c.command is None:
        return lead + ", but none of them is whole, so wowlab has none to offer to restore."
    return (
        f"{lead}; the newest whole one is {c.snapshot_id}. To see what restoring it would "
        f"put back before you choose, run: {c.command}"
    )


# ─── doctor: next steps (M16-37) ─────────────────────────────────────────────

_RECORD_HOW = (
    "In game, with the lab-addon installed and enabled for the character (AddOns at "
    "character select), log in, then log out or type /reload."
)
_COMBATLOG_HOW = (
    "In game, type /combatlog to turn combat logging on; the game writes a log only while it is on."
)
# What a missing or out-of-date lab-addon means for the records: none
# while it is missing; an out-of-date one may still load ('Load out of date
# AddOns'), so that is hedged; and nothing when wowlab cannot tell.
_RECORDS_WAIT = {
    "missing": "they are not updated until the lab-addon above is fixed",
    "stale": "they may not be updated until the lab-addon above is fixed",
}


def _install_step(build_info_missing: bool) -> NextStep:
    """The one next step when no install was found. The how-to is the lines
    above it in the text (`firstrun.pointing_lines`, the Scan and Repair
    line), so this points there rather than saying it again."""
    if build_info_missing:
        fix = (
            "If this is the game's folder, run Scan and Repair as the lines above say; if "
            "not, name the folder that holds .build.info, as they also say."
        )
    else:
        fix = "Name the folder that holds .build.info, as the lines above say."
    return NextStep(item="install", flavor_folder=None, state="missing", fix=fix)


def _wowlab_command(args: Sequence[str], folder: str, several: bool, root: str | None) -> str:
    """A wowlab command for this flavor, as a player would type it: the
    caller's --root as given (only when one was given, as `labaddon.survey`
    names it for `char show`; on Windows a `\\\\?\\` root by its plain
    spelling), and --flavor when the install has several flavors, every
    argument quoted for this machine's shell."""
    argv = ["wowlab", *args]
    if root is not None:
        argv += ["--root", longpath.plain_text(root) if longpath.ON_WINDOWS else root]
    if several:
        argv += ["--flavor", folder]
    return _powershell_join(argv) if _shell() == "powershell" else shlex.join(argv)


def _lab_addon_step(
    folder: str, state: addoninstall.LabAddonState, several: bool, root: str | None = None
) -> NextStep:
    command = _wowlab_command(_REINSTALL.split()[1:], folder, several, root)
    fix = f"Close the game, then run `{command}`."
    if state.status == "current":
        return NextStep(item="lab_addon", flavor_folder=folder, state="ok", command=command)
    if state.status == "not_installed":
        return NextStep(
            item="lab_addon", flavor_folder=folder, state="missing", command=command, fix=fix
        )
    if state.status == "stale":
        detail = (
            "made for another client version"
            if state.installed
            else "its ## Interface: line is missing or unusable"
        )
        return NextStep(
            item="lab_addon",
            flavor_folder=folder,
            state="stale",
            command=command,
            detail=detail,
            fix=fix,
        )
    return NextStep(
        item="lab_addon",
        flavor_folder=folder,
        state="unknown",
        command=command,
        detail=state.detail,
    )


def _characters_step(
    lay: layout.Layout,
    folder: str,
    several: bool,
    root: str | None,
    lab_state: NextStepState,
) -> NextStep:
    """How many character folders hold a lab-addon record: from the listing
    alone (`budget=0` opens no file), so doctor reads no record. A place
    wowlab could not look inside, above the character folders or a
    character's own folder or SavedVariables/, may hold more: then the
    count is not the whole answer (`unknown`)."""
    command = _wowlab_command(["char", "list"], folder, several, root)
    found = labaddon.survey(lay, keep_records=False, budget=0)
    count = sum(1 for e in found.characters if e.place == "file")
    unseen = len(found.not_looked_at)
    unseen += sum(1 for e in found.characters if e.place in _BLOCKED_PLACE)
    notes: list[str] = []
    if unseen:
        it = "it" if unseen == 1 else "them"
        notes.append(
            f"{_plural(unseen, 'place')} wowlab could not look inside (`{command}` names {it})"
        )
    if count and lab_state in _RECORDS_WAIT:
        notes.append(_RECORDS_WAIT[lab_state])
    state: NextStepState = "unknown" if unseen else ("ok" if count else "missing")
    return NextStep(
        item="characters",
        flavor_folder=folder,
        state=state,
        count=count,
        command=command,
        detail="; ".join(notes) or None,
        fix=_RECORD_HOW if state == "missing" else None,
    )


def _combat_log_step(lay: layout.Layout, folder: str) -> NextStep:
    """Whether Logs/ holds a combat log, and when the newest was written: a
    listing and one stat, no file opened."""
    try:
        logs_dir = _logs_dir(lay)
        logs = combatlog.find_logs(logs_dir) if logs_dir is not None else []
    except OSError as exc:
        return NextStep(
            item="combat_log",
            flavor_folder=folder,
            state="unknown",
            detail=f"wowlab could not look in Logs/: {exc.strerror or exc}",
        )
    if not logs:
        return NextStep(
            item="combat_log", flavor_folder=folder, state="missing", count=0, fix=_COMBATLOG_HOW
        )
    try:
        newest: int | None = longpath.lstat(logs[-1]).st_mtime_ns
    except OSError:
        newest = None  # gone since the listing
    return NextStep(
        item="combat_log",
        flavor_folder=folder,
        state="ok",
        count=len(logs),
        newest_mtime_ns=newest,
    )


def _backup_step(
    folder: str,
    several: bool,
    root: str | None,
    latest: snapshot.Manifest | None,
    version: str | None,
) -> NextStep:
    """The game updated since the newest backup of the flavor (M17-04): the
    two versions, and the command that takes a new one."""
    command = _wowlab_command(["snap", "create"], folder, several, root)
    before = latest.flavor_version if latest is not None else None
    return NextStep(
        item="backup",
        flavor_folder=folder,
        state="stale",
        command=command,
        detail=f"your last snapshot was taken on version {before}; the game is now {version}",
        fix=f"Take a backup now: run `{command}`.",
    )


def _flavor_steps(
    lay: layout.Layout,
    folder: str,
    lab_addon: addoninstall.LabAddonState,
    several: bool,
    root: str | None,
) -> list[NextStep]:
    lab = _lab_addon_step(folder, lab_addon, several, root)
    return [
        lab,
        _characters_step(lay, folder, several, root, lab_state=lab.state),
        _combat_log_step(lay, folder),
    ]


def _next_step_answer(step: NextStep) -> str:
    """What an item of "Next steps" says before its fix: its answer, then
    its `detail` when it has one."""
    n = step.count or 0
    records = "Character folders with a lab-addon record"
    match step.item, step.state:
        case "install", _:
            said = "Install: not found"
        case "lab_addon", "ok":
            said = "Lab-addon: installed, made for this client"
        case "lab_addon", "missing":
            said = "Lab-addon: not installed"
        case "lab_addon", "stale":
            said = "Lab-addon: out of date for this client"
        case "lab_addon", _:
            said = "Lab-addon: wowlab cannot tell whether it fits this client"
        case "characters", "missing":
            said = f"{records}: none"
        case "characters", "unknown" if n == 0:
            said = f"{records}: none found"
        case "characters", _:
            said = (
                f"{records}: {n} (each as of that character's last logout or /reload with "
                f"the lab-addon on; `{step.command}` reads them)"
            )
        case "combat_log", "ok":
            said = f"Combat log: present ({n} under Logs/"
            if step.newest_mtime_ns is not None:
                said += f", the newest written {_local_time(step.newest_mtime_ns)}"
            said += ")"
        case "combat_log", "missing":
            said = "Combat log: none under Logs/"
        case "backup", _:
            said = "Backup: the game updated since your last snapshot"
        case _:
            said = "Combat log: wowlab cannot tell whether one is there"
    if step.detail:
        said += f"; {step.detail}"
    return said + "."


def _print_next_steps(steps: Sequence[NextStep]) -> None:
    """The "Next steps" block: each item's answer, then its fix. With
    several flavors, the items are grouped under each flavor's name. It
    says "nothing to do" only when every item is `ok`."""
    if not steps:
        return
    done = all(s.state == "ok" for s in steps)
    _say("Next steps: nothing to do." if done else "Next steps:")
    grouped = len({s.flavor_folder for s in steps if s.flavor_folder is not None}) > 1
    heading: str | None = None
    for step in steps:
        indent = "  "
        if grouped and step.flavor_folder is not None:
            if step.flavor_folder != heading:
                heading = step.flavor_folder
                _say(f"  {heading}:")
            indent = "    "
        text = _next_step_answer(step)
        if step.fix is not None:
            text += f" {step.fix}"
        # Each command, and a time, is kept on one line.
        keep = _commands_in(text)
        if step.newest_mtime_ns is not None:
            keep.append(_local_time(step.newest_mtime_ns))
        _say_prose(text, indent=indent, hang=indent + "  ", keep=keep)


def _commands_in(text: str) -> list[str]:
    """The commands between backticks in an item, each kept whole when wrapped."""
    return text.split("`")[1::2]


def _could_be_an_install(root: Path) -> bool:
    """Whether `root`, which holds no .build.info, could be an install that
    lost it (M16-13): a folder, and inside no install (no folder above it
    holds a regular .build.info). Reads only; any error is a no."""
    try:
        if not longpath.is_dir(root):
            return False
        return not any(longpath.is_file(p / install.BUILD_INFO) for p in root.parents)
    except OSError:
        return False


def _interrupted(record_id: str, stopped: Sequence[str] = ()) -> str:
    """The `doctor` problem for a most recent journal record still `open`
    (M16-13): a change that was killed, or is running now, so some files
    may hold its new bytes and others not. `undo --expect` names exactly
    that record, and refuses if another change is journaled first. With
    `stopped`, the newer records that stopped before they changed any file
    and were closed (2026-11-11 (a1), (a2)): it is no longer the newest
    record, so it is named as the change before them."""
    return (
        f"{_subject(record_id, stopped)} did not finish (record {record_id}): some files may "
        "be changed and others not. To put back what was there before it, run: "
        f"wowlab undo --expect {record_id}"
    )


def _subject(record_id: str, stopped: Sequence[str]) -> str:
    """Who a doctor problem is about: the last change wowlab made, or, past
    closed records that stopped before they changed any file (2026-11-11
    (a1), (a2)), the change before them, with their record ids."""
    if not stopped:
        return "the last change wowlab made"
    if len(stopped) == 1:
        return (
            f"a later change stopped before it changed any file (record {stopped[0]}), so "
            "the change before it"
        )
    return (
        f"later changes stopped before they changed any file (records {', '.join(stopped)}), "
        "so the change before them"
    )


def _stopped_before_any_file(record_id: str, *, removed: bool = False) -> str:
    """The `doctor` problem for a most recent `open` record that never took
    its pre-write snapshot (2026-11-11 (a)): the change was stopped before
    it changed any file. `removed`: it had removed leftover temporary files
    of an earlier change first, which is said only then. Undo closes it and
    restores nothing."""
    if removed:
        what = (
            f"stopped before it changed any file (record {record_id}): it only removed "
            "temporary files an earlier change left behind"
        )
    else:
        what = f"stopped before it changed anything (record {record_id})"
    return f"the last change wowlab made {what}. To close it, run: wowlab undo --expect {record_id}"


def _rollback_unfinished(
    record_id: str,
    left: Sequence[LeftPath] = (),
    flavor_folder: str = "",
    *,
    paths: int = 1,
    created_dirs: int = 0,
    stopped: Sequence[str] = (),
) -> str:
    """The `doctor` problem for a most recent journal record in
    `rollback_incomplete` (M16-71): the change failed and the write gate
    could not put every file back. Usually another program (the game, when
    it saves settings or addon data) changed a file mid-change, and the gate
    left that program's bytes in place; a file may also still hold the
    change. The undo puts back every file the change wrote or deleted (its
    `paths`, M16-86), the game's later saves included, but takes a snapshot
    of the files first, so undoing the undo next, before the player plays,
    brings them back (the wording of undo's own plan, M16-92).

    With `left` (M16-84) the problem names each file the rollback left, in
    `flavor_folder`, grouped by what doctor read there now (`LeftPath`). A
    file the change never wrote (its only operation was refused) is said to
    be left alone by undo, which acts only on `paths`. With `paths == 0`
    the change wrote nothing, and with `created_dirs == 0` too undo has
    nothing to do and records nothing (`undo` returns before planning when a
    record has no paths and no folders): the problem says so, and that the
    next change clears it, and offers no command (the one exception to "the
    command stays last"). With folders but no paths, undo removes the empty
    folders and records itself, so the command is offered, last. Names are kept as stored: the text output escapes
    them as it prints (`_say`), `--json` is ASCII, and `--redact` sees them
    unescaped. A record that lists no file gets the sentence without names.
    With `stopped`, the record is the change undos that stopped before they
    changed any file were undoing (2026-11-11 (a1)), and is named so."""
    subject = _subject(record_id, stopped)
    groups: dict[tuple[LeftNow, bool | None], list[str]] = {}
    for p in left:
        groups.setdefault((p.now, p.exists), []).append(p.path)
    said = [_left_clause(key, groups[key]) for key in _LEFT_ORDER if key in groups]
    where = f"in {flavor_folder}, " if flavor_folder else ""
    if paths == 0 and created_dirs:
        # No file journaled, but folders created (a write refused after its
        # folders were made): undo removes them if empty and records itself,
        # so this record stops being the last (M16-86's plan says the same).
        lead = (
            f"{subject} failed after it created folders and before it changed "
            f"any file (record {record_id}): "
        )
        named = (
            where + "; ".join(said) if said else "wowlab could not remove every folder it created"
        )
        return (
            lead
            + named
            + ". undo removes the folders it created if they are empty and records the undo, "
            "and wowlab doctor stops reporting this change: "
            f"wowlab undo --expect {record_id}"
        )
    if paths == 0:
        lead = f"{subject} failed before it changed any file (record {record_id}): "
        named = (
            where + "; ".join(said)
            if said
            else f"another program, {_THE_GAME}, changed a file while wowlab was working"
        )
        return (
            lead
            + named
            + ". wowlab changed no file, so there is nothing for undo to put back, and undo "
            "leaves this record as it is; wowlab doctor stops reporting it after wowlab's "
            "next change."
        )
    lead = f"{subject} failed, and putting the files back did not finish (record {record_id}): "
    tail = (
        "To put back what was there before the change, close the game and run this; it puts "
        "back every file the change wrote or deleted, including anything the game has saved "
        "to them since, but the undo keeps a copy and running wowlab undo again next, before "
        f"you play, brings them back: wowlab undo --expect {record_id}"
    )
    if not said:
        return (
            lead + f"another program, {_THE_GAME}, may have changed a file while wowlab was "
            "writing or since, or a file may still hold wowlab's change. " + tail
        )
    return lead + where + "; ".join(said) + ". " + tail


# Who else writes these files, without a timing that holds for only some of
# them (SavedVariables at logout or /reload, Config.wtf on exit and on some
# setting changes).
_THE_GAME = "such as the game when it saves your settings or addon data"

# The order the problem names the files in: what doctor read there, and
# whether the file is there at all (None: not read).
_LEFT_ORDER: tuple[tuple[LeftNow, bool | None], ...] = (
    ("wowlab_change", True),
    ("wowlab_change", False),
    ("changed_since", True),
    ("changed_since", False),
    ("unknown", None),
    ("before_change", True),
    ("before_change", False),
    ("not_written", None),
)


def _left_clause(key: tuple[LeftNow, bool | None], paths: Sequence[str]) -> str:
    """One group of `_rollback_unfinished`: the files, then what they hold.
    No semicolon inside one: the groups are joined with "; "."""
    now, exists = key
    one = len(paths) == 1
    names = paths[0] if one else f"{', '.join(paths[:-1])} and {paths[-1]}"
    it = "it" if one else "them"
    is_ = "is" if one else "are"
    holds = "holds" if one else "hold"
    if now == "wowlab_change":
        if exists:
            return f"{names} still {holds} wowlab's change"
        return f"{names} {is_} still deleted, as wowlab's change left {it}"
    if now == "changed_since":
        did = "changed" if exists else "deleted"
        return f"another program, {_THE_GAME}, has {did} {names} since wowlab's change"
    if now == "before_change":
        if exists:
            return f"{names} already {holds} what was there before the change"
        return f"{names} {is_} gone again, as before the change"
    if now == "not_written":
        return (
            f"wowlab did not write {names}, which another program changed while wowlab was "
            f"working, and undo leaves {it} as {'it is' if one else 'they are'}"
        )
    return (
        f"wowlab could not put back {names}, which {holds} either wowlab's change or what "
        f"another program, {_THE_GAME}, has written to {it} since"
    )


# A journal record's state as `doctor`'s text output says it (M16-85): the
# player-copy rules forbid printing the state names, which `--json` keeps.
# Every state the write gate journals is here; the words for the two that
# are problems match `_interrupted` and `_rollback_unfinished`.
_JOURNAL_STATE_WORDS: dict[str, str] = {
    "committed": "finished",
    "open": "did not finish",
    "rolled_back": "failed, and its files were put back",
    "rollback_incomplete": "failed, and putting the files back did not finish",
}


def _journal_state_words(state: str) -> str:
    """The words for a journal record's state; a state this version does not
    know (a journal written by a later one) is said as such, not by name."""
    return _JOURNAL_STATE_WORDS.get(state, "in a state this version of wowlab does not know")


# Where the Battle.net app repairs an install, a missing .build.info included.
_SCAN_AND_REPAIR = (
    "  If this is the game's folder and its .build.info went missing, the Battle.net app "
    "puts it back: choose the game, then the gear beside Play, then Scan and Repair."
)


def _print_game_data(g: GameDataReport) -> str:
    if g.status == "published":
        return f"wago.tools lists version {g.version}"
    if g.status == "not_published":
        return (
            f"wago.tools does not list version {g.version}; wowlab db2 cannot fetch tables for it"
        )
    if g.status == "no_version":
        # The flavor's line above says why (M16-118, M16-121); `detail` stays
        # in --json unchanged.
        return "no version to look up"
    if g.status == "not_checked":
        return "not checked (--check-builds asks wago.tools)"
    return f"could not check ({g.detail})"


_NUMBER_WORDS = {2: "two", 3: "three", 4: "four"}
# What a sync setting with no line means (M16-127). Not "the client's
# default": on one client, two such settings read as on and the third did
# not exist, so no default is promised and no flavor is named (L6).
_SYNC_UNSET = (
    "with no line, the client may treat a setting as on or may not have it at all, "
    "and wowlab cannot tell which"
)
_SYNC_UNSET_ONE = (
    "the client may treat it as on or may not have it at all; wowlab cannot tell which"
)
# What the sync lines themselves do not say (they say what sync replaces).
_SYNC_NOTE = (
    "Which other *-cache files server sync replaces at login, besides key bindings, macros "
    "and settings, is not known."
)
_STORE_VERIFY_NOTE = "wowlab snap verify re-hashes every object in the snapshot store."
_STORE_MODES_NOTE = (
    "On Windows, wowlab does not check who else can read the snapshot store; the folder "
    "takes its access from your user profile."
)


def _exposure_line(exposure: snapshot.StoreExposure) -> str | None:
    """The warning when other accounts on this computer can open the store
    (M16-41), or None."""
    if not exposure.readable_by_others:
        return None
    return (
        f"Other accounts on this computer can read the snapshot store "
        f"({exposure.count} of its files and folders). wowlab makes it private the next time it "
        "writes there, such as at the next snapshot."
    )


_REINSTALL = f"wowlab addon install {addoninstall.LAB_ADDON}"
_LOAD_OUT_OF_DATE = "Load out of date AddOns"
# What an out-of-date lab-addon means for the player (M16-06, domain review).
_STALE_EFFECT = (
    f"and not load it (unless '{_LOAD_OUT_OF_DATE}' is ticked), and then no new records are saved"
)


def _lab_addon_line(state: addoninstall.LabAddonState, reinstall: str = _REINSTALL) -> str | None:
    """The `doctor` line about the installed lab-addon (M16-06), or None
    when it is not installed. `reinstall` is the command its next step
    names (M16-37): with the caller's --root, and --flavor on an install
    with several flavors."""
    if state.status == "current":
        found = ", ".join(str(n) for n in state.installed)
        return f"  Lab-addon: installed, made for this client (## Interface: {found})"
    if state.status == "stale" and not state.installed:
        return (
            f"  Lab-addon: its {addoninstall.TOC_NAME} has no usable ## Interface: value, so "
            f"the game may treat it as out of date {_STALE_EFFECT}. Close the game and run "
            f"`{reinstall}`."
        )
    if state.status == "stale":
        found = ", ".join(str(n) for n in state.installed)
        return (
            f"  Lab-addon: made for another client version (## Interface: {found}; this "
            f"client is {state.expected}), so the game may list it as out of date "
            f"{_STALE_EFFECT}. Close the game and run `{reinstall}` to update it."
        )
    if state.status == "unknown":
        return (
            "  Lab-addon: installed; wowlab cannot tell whether it fits this client "
            f"({state.detail})"
        )
    return None


def _unknown_version(product: str | None, matching_rows: int) -> str:
    """Why a flavor's version is unknown (M16-118). Three cases: its
    `.flavor.info` gives no product (empty, or unreadable:
    `install._read_flavor_product` gives "" for both); `.build.info` has no
    row for its product (the Battle.net app keeps that file, a row per game
    version installed in the folder; the usual causes are hedged, not
    asserted); or a row matches and its Version cell is blank."""
    if not product:
        return (
            "  This folder's .flavor.info gives no product code (it is empty or could not "
            "be read), so no .build.info row can match it."
        )
    if matching_rows >= 1:
        return f"  The .build.info row for product {product} has no version in it."
    return (
        "  .build.info, where the Battle.net app lists the game versions installed here, "
        f"has no row for product {product}. Usually this folder was left behind by a beta "
        "that ended or was renamed, or by a game version that was uninstalled. wowlab still "
        "reads its settings and addons; lookups that need the game version cannot run."
    )


def _print_doctor(report: DoctorReport, show_notes: bool = False) -> None:
    """The doctor report as text, its problems and then its next steps
    last (M16-37), then its standing notes behind --notes (`_say_notes`). The first run's section, said only
    once, comes after it."""
    notes: list[str] = []
    if report.install_root is not None:
        _say(f"Install: {report.install_root} (found by {report.found_by})")
    else:
        _say(f"Install: {report.install_error}")
        if report.build_info_missing:
            _say_prose(_SCAN_AND_REPAIR, hang="  ", keep=["Battle.net app", "Scan and Repair"])
        if report.locations is not None:
            for loc in report.locations.searched:
                _say(f"  searched, no install there: {loc}")
            for unchecked in report.locations.could_not_check:
                _say(f"  could not check: {unchecked.location} ({unchecked.reason})")
            _say(f"  Pass --root, or set {install.ENV_ROOT}, to name the install folder.")
        for line in firstrun.pointing_lines():
            _say(line)
    for f in report.flavors:
        _say(
            f"Flavor {f.folder}: product {f.product or '(none)'}, version {f.version or 'unknown'}"
        )
        if f.version is None:
            _say_prose(_unknown_version(f.product, f.matching_rows), hang="    ", keep=[f.product])
        if f.matching_rows > 1:
            _say(
                f"  {f.matching_rows} rows of .build.info name this product; "
                "the first active one is shown"
            )
        _say(f"  Game data: {_print_game_data(f.game_data)}")
        reinstall = next(
            (
                s.command
                for s in report.next_steps
                if s.item == "lab_addon" and s.flavor_folder == f.folder and s.command
            ),
            _REINSTALL,
        )
        lab_line = _lab_addon_line(f.lab_addon, reinstall)
        if lab_line is not None:
            _say_prose(lab_line, hang="    ", keep=[reinstall, _LOAD_OUT_OF_DATE])
        if f.sync_cvars and all(sync.value is None for sync in f.sync_cvars):
            # Six lines saying "not set" were one fact (M16-16).
            files = list(dict.fromkeys(sync.file for sync in f.sync_cvars))
            names = len({sync.name for sync in f.sync_cvars})
            _say_prose(
                f"  Server sync: the {_NUMBER_WORDS.get(names, str(names))} sync settings wowlab "
                f"reads are not set in {' or '.join(files)} (when on, the server can replace "
                f"your bindings, macros or settings at login; {_SYNC_UNSET})",
                hang="    ",
                keep=files,
            )
        elif f.sync_cvars:
            _say_prose(
                "  Server sync (when on, the server can replace your bindings, macros or "
                "settings at login):",
                hang="    ",
            )
            for sync in f.sync_cvars:
                value = (
                    f'"{sync.value}"' if sync.value is not None else f"not set ({_SYNC_UNSET_ONE})"
                )
                _say(f"    {sync.file}: {sync.name} {value}")
    if any(f.sync_cvars for f in report.flavors):
        notes.append(_SYNC_NOTE)
    for line in _not_taken_lines(report.not_taken, report.other_dirs):
        _say(line)
    if report.client is not None:
        c = report.client
        if c.state == "not_running":
            _say("Client: not running")
        elif c.state == "could_not_check":
            _say(f"Client: could not check ({c.error}); the write gate treats this as running")
        else:
            label = "running" if c.state == "running" else "cannot tell (the write gate refuses)"
            _say(f"Client: {label}")
            for p in c.processes:
                what = p.name or (p.exe.name if p.exe else "?")
                where = f" in {p.flavor_folder}" if p.flavor_folder else ""
                _say(f"  pid {p.pid}: {p.state.value} {what}{where}")
    store = report.store
    _say(f"Snapshot store: {store.path}" + ("" if store.exists else " (not created yet)"))
    damaged = f"{len(store.damaged)} damaged" if store.damaged else "none damaged"
    _say(f"  {store.snapshots} snapshot(s) that load; {damaged}")
    for bad in store.damaged:
        _say(f"  damaged: {bad.name}: {bad.reason}")
    if store.journal_error:
        _say(f"  journal: {store.journal_error}")
    elif store.last_transaction is not None:
        t = store.last_transaction
        which = "to undo" if t.passed_over else "most recent"
        _say(
            f"  {store.transactions} transaction(s) in the journal; {which}: {t.id} "
            f'({_journal_state_words(t.state)}; {t.paths} file(s)) "{t.label}"'
        )
    else:
        _say("  no transactions in the journal")
    exposed = _exposure_line(store.exposure)
    if exposed is not None:
        _say_prose(f"  {exposed}", hang="    ")
    # On Windows the store's note says the modes are not checked as well;
    # one note about the store, so the footer counts the same everywhere.
    notes.append(
        _STORE_VERIFY_NOTE
        if store.exposure.checked
        else f"{_STORE_VERIFY_NOTE} {_STORE_MODES_NOTE}"
    )
    # Why the command exits 1, as `problems` says it in `--json` (M13-56):
    # without this, a run that skips the first run said nothing.
    if report.problems:
        _say("Problems:")
        for problem in report.problems:
            _say(f"  {problem}")
    # What to do next ends the report's answer (M16-37), before the footer.
    _print_next_steps(report.next_steps)
    # The footer ends the report (conductor's ruling, 2026-11-06): after the
    # problems, before the first run's one-time section.
    _say_notes(notes, show_notes)


def _doctor_redacted(root: Path | None, check_builds: bool, names: list[str]) -> DoctorReport:
    """`_doctor` through `doctorredact` (M13-09): every string redacted before it is printed."""
    try:
        extra = doctorredact.check_names(names)
    except ValueError as exc:
        raise CliError(str(exc), EXIT_USAGE) from None
    try:
        return doctorredact.redacted(
            lambda: _doctor(root, check_builds), root, extra, doctorredact.DOCTOR_FIELDS
        )
    except doctorredact.RedactedError as exc:
        raise CliError(str(exc)) from None


def _interactive() -> bool:
    """True when a first run may ask: input is a terminal. Tests replace it."""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (OSError, ValueError):
        return False


def _ask(question: str) -> bool:
    """A yes/no question on stderr; no answer (end of input) is a no."""
    try:
        return typer.confirm(_safe(question), default=False, err=True)
    except typer.Abort:
        _note("")
        return False


def _choose_flavor(labels: Sequence[str]) -> int | None:
    """Which of several flavors to snapshot: each is listed on stderr with its
    number, product code and version; an empty answer or end of input is none."""
    for n, flavor_label in enumerate(labels, 1):
        _say_err(f"  {n}) {flavor_label}")
    question = f"Snapshot which flavor? Enter 1 to {len(labels)}, or nothing for none"
    while True:
        try:
            answer = typer.prompt(question, default="", show_default=False, err=True)
        except typer.Abort:
            _note("")
            return None
        answer = str(answer).strip()
        if not answer:
            return None
        if answer.isdecimal() and 1 <= int(answer) <= len(labels):
            return int(answer) - 1
        _say_err(f"Not a number from 1 to {len(labels)}: {answer}")


def _client_descriptions(inst: install.Install) -> list[str]:
    """The client processes `doctor` finds, one short description each."""
    out: list[str] = []
    for p in _client_report(inst).processes:
        what = p.name or (p.exe.name if p.exe else "?")
        where = f" in {p.flavor_folder}" if p.flavor_folder else ""
        out.append(f"{what}, pid {p.pid}{where}")
    return out


def _first_run(
    inst: install.Install | None,
    store: snapshot.SnapshotStore,
    flavor: str | None,
    yes: bool,
    json_out: bool,
    say: Callable[[str], None],
) -> firstrun.FirstRunReport:
    """The first-run section of `doctor` (`firstrun.run`): every line through
    `say` (the same callable the intro used; nothing with --json), and a
    question only when input is a terminal and neither --json nor --yes is
    given."""
    named = inst is not None and _given_flavor(flavor) is not None
    chosen = _select_flavor(inst, flavor) if inst is not None and named else None
    asking = not json_out and not yes and _interactive()
    if not json_out:
        say("")

    def running(f: install.Flavor) -> bool | None:
        assert inst is not None
        return _client_running(inst, f)

    def names() -> list[str]:
        return _client_descriptions(inst) if inst is not None else []

    return firstrun.run(
        inst,
        store=store,
        chosen=chosen,
        yes=yes,
        client_running=running,
        say=say,
        choose=_choose_flavor if asking else None,
        confirm=_ask if asking else None,
        clients=names,
    )


# The one deprecation line of `doctor --offline` (M16-07), on stderr.
_OFFLINE_DEPRECATED = (
    "--offline is no longer needed and does nothing: doctor makes no network request "
    "unless --check-builds is given."
)


def _no_text(_line: str) -> None:
    """`say` with --json: the first-run text is not printed."""


@app.command(short_help="Check the install, the client and the snapshot store.")
@_handled
def doctor(
    root: RootOpt = None,
    check_builds: Annotated[
        bool,
        typer.Option(
            "--check-builds",
            help="Ask wago.tools whether it lists each installed version (downloads its "
            "public version list). Without it, doctor makes no network request.",
        ),
    ] = False,
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            hidden=True,
            help="Does nothing: doctor is offline unless --check-builds is given.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="On a first run, take the offered first snapshot without asking "
            "(with several flavors, name one with --flavor).",
        ),
    ] = False,
    flavor: Annotated[
        str | None,
        typer.Option(
            "--flavor",
            help="On a first run, the flavor folder to snapshot. Default: $WOWLAB_FLAVOR; "
            "implied when the install has only one.",
        ),
    ] = None,
    json_out: JsonOpt = False,
    redact: Annotated[
        bool,
        typer.Option(
            "--redact",
            help="Output to paste into an issue: character, realm and account names, BattleTags, "
            "GUIDs, email addresses, your login and computer names, your home folder and journal "
            "labels become placeholders.",
        ),
    ] = False,
    redact_name: Annotated[
        list[str] | None,
        typer.Option(
            "--redact-name",
            help="Another name to replace, such as your guild's (repeatable). Implies --redact.",
        ),
    ] = None,
    notes: NotesOpt = False,
) -> None:
    """Install, flavors, versions, whether the client runs, store health.
    It ends with next steps: whether the lab-addon is installed and made for
    this client, how many characters it has recorded, and whether a combat
    log is there, each with the command or in-game action that fixes it.

    On a first run (the snapshot store holds no snapshot of this install) it
    also says what wowlab will and will not do, shows what a first snapshot
    would hold (size, an estimated time, links, sync folders), offers one
    (asking when input is a terminal; --yes accepts; --json never asks),
    takes it into wowlab's own store, never into the install, verifies the
    store, and ends with how to undo.

    With --redact (or --redact-name) the first run is skipped: no intro, no
    scan, no question, no snapshot; --yes and --flavor are refused with it.

    \f
    JSON: DoctorReport."""
    if offline:
        if check_builds:
            raise CliError(
                "--offline and --check-builds contradict each other; doctor is offline "
                "unless --check-builds is given, so drop --offline",
                EXIT_USAGE,
            )
        _note(_OFFLINE_DEPRECATED)
    if redact or redact_name:
        if yes or flavor is not None:
            raise CliError(
                "--redact skips the first run, so --yes and --flavor do nothing with it; "
                "run `wowlab doctor` without --redact to take a first snapshot",
                EXIT_USAGE,
            )
        report = _doctor_redacted(root, check_builds, redact_name or [])
        if json_out:
            _emit(report)
        else:
            _print_doctor(report, notes)
        if report.problems:
            raise typer.Exit(EXIT_ERROR)
        return
    store = snapshot.SnapshotStore()
    found: list[install.Install | None] = []
    first = False
    # Every line of the first run, intro included, goes through this one
    # callable. --redact never reaches here: it skips the first run (above).
    say: Callable[[str], None] = _no_text if json_out else _say

    def on_found(inst: install.Install | None) -> None:
        nonlocal first
        found.append(inst)
        if inst is not None and _given_flavor(flavor) is not None:
            _select_flavor(inst, flavor)  # a bad --flavor fails before any request
        first = firstrun.is_first_run(store, inst)
        # With no install, the verdict on the folder comes first (M16-13):
        # the intro follows the report instead (no request is made then).
        if first and not json_out and inst is not None:
            for line in firstrun.intro_lines(store_path=store.path):
                say(line)
            say("")

    report = _doctor(root, check_builds, on_found)
    if not json_out:
        _print_doctor(report, notes)
        if first and found[0] is None:
            say("")  # the first run's section adds the blank line after it
            for line in firstrun.intro_lines(store_path=store.path):
                say(line)
    if first:
        result = _first_run(found[0], store, flavor, yes, json_out, say)
        problems = list(report.problems)
        if result.outcome == "failed":
            problems.append(f"first snapshot: {result.error}")
        report = report.model_copy(update={"first_run": result, "problems": problems})
    # The report is complete here, first run included, before it is printed.
    if json_out:
        _emit(report)
    if report.first_run is not None and report.first_run.refused:
        raise typer.Exit(EXIT_REFUSED)
    if report.problems:
        raise typer.Exit(EXIT_ERROR)
    if report.first_run is not None and report.first_run.outcome == "choose_flavor":
        raise typer.Exit(EXIT_USAGE)
