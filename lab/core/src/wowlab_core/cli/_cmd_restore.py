"""`wowlab snap restore` and `wowlab undo` (M16-111)."""

import hashlib
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from wowlab_core import (
    guard,
    install,
    layout,
    profiles,
    snapshot,
    wtfdiff,
)
from wowlab_core.cli._changes import (
    _CHANGED_LINE,
    _CHANGED_UNKNOWN_STATE,
    _DETAIL_PLAN_LIMIT,
    _NOTHING_TO_PUT_BACK,
    _NOTHING_TO_PUT_BACK_UNFINISHED,
    _STOPPED_BEFORE_ANY_FILE,
    AccountMapped,
    IntoReport,
    NotRestoredPath,
    RestoreReport,
    SkippedPath,
    UndoReport,
    _added_since,
    _changed_executables,
    _client_managed,
    _detail_lines,
    _disk_now,
    _executable_note,
    _left_alone_lines,
    _names_for_whole,
    _not_restored_lines,
    _plan_footer,
    _plan_item_lines,
    _relogin_notes,
    _screenshot_note,
    _set_aside,
    _skipped_lines,
    _stored,
    _unchanged,
    _Unread,
    _wants_detail,
)
from wowlab_core.cli._cmd_doctor import (
    _journal_state_words,
)
from wowlab_core.cli._cmd_snap import (
    _REF_HELP,
    _plan_line,
    _resolve_ref,
    _version,
)
from wowlab_core.cli._common import (
    EXIT_ERROR,
    EXIT_REFUSED,
    CliError,
    JsonOpt,
    RootOpt,
    YesOpt,
    _confirm,
    _discover,
    _emit,
    _flavor_list,
    _handled,
    _safe,
    _say,
    _say_err,
    app,
    snap_app,
)

IntoFlavorOpt = Annotated[
    str | None,
    typer.Option(
        "--into-flavor",
        metavar="FOLDER",
        help="Restore into this flavor folder of the install instead of the one the snapshot "
        "was taken of. When a beta launches, the game's folder gets a new name (you do not "
        "rename it): name the new folder here.",
    ),
]
MapAccountOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--map-account",
        metavar="SOURCE=TARGET",
        help="With --into-flavor or --into-root: put the snapshot's account folder SOURCE "
        "into the target's account folder TARGET (folder names under WTF/Account/); repeat "
        "for each. Needed unless each side has exactly one account folder.",
    ),
]


def _one_folder_name(value: str, option: str) -> str:
    """`value` as one plain folder name, or a refusal naming `option`."""
    if (
        value in ("", ".", "..")
        or value != value.strip()
        or any(c in value for c in "/\\:\x00")
        or any(ord(c) < 32 for c in value)
    ):
        raise CliError(f"{option} takes one folder name, not {value!r}")
    return value


def _account_pairs(pairs: Sequence[str] | None) -> dict[str, str] | None:
    """`--map-account SOURCE=TARGET` values as a mapping; None when none was given."""
    if not pairs:
        return None
    mapping: dict[str, str] = {}
    for pair in pairs:
        source, sep, target = pair.partition("=")
        if not sep or "=" in target or not source or not target:
            raise CliError(
                f"--map-account takes SOURCE=TARGET, two account folder names, not {pair!r}"
            )
        _one_folder_name(source, "--map-account")
        _one_folder_name(target, "--map-account")
        if source in mapping:
            raise CliError(f"--map-account names account folder {source!r} twice")
        mapping[source] = target
    return mapping


def _into_target(chosen: install.Flavor, accounts: dict[str, str] | None) -> guard.Target:
    try:
        return guard.Target(flavor=chosen, accounts=accounts)
    except ValidationError as exc:
        why = "; ".join(str(e.get("msg", "")) for e in exc.errors())
        raise CliError(f"--map-account is refused: {why}") from exc


def _into_report(manifest: snapshot.Manifest, accounts: Sequence[tuple[str, str]]) -> IntoReport:
    return IntoReport(
        from_flavor_folder=manifest.flavor_folder or "",
        from_install_root=manifest.install_root,
        accounts=[AccountMapped(snapshot=a, target=b) for a, b in accounts],
        unmapped=list(profiles.unmapped_folders(manifest, dict(accounts))),
    )


def _into_lines(into: IntoReport, chosen: install.Flavor) -> list[str]:
    """The plan's lines that name both folders and every mapped account folder."""
    lines = [
        f"  From {into.from_flavor_folder} in {into.from_install_root} into {chosen.folder} "
        f"in {chosen.path.parent}."
    ]
    lines += [
        f"  Account folder {a.snapshot} goes into account folder {a.target}." for a in into.accounts
    ]
    lines += [
        f"  WTF/Account/{name}/ is not taken as an account folder (no character folder in "
        "it), so it is not mapped: its files go back under the same name."
        for name in into.unmapped
    ]
    return lines


@snap_app.command(
    "restore", short_help="Put back files from a snapshot (asks first; undo reverses it)."
)
@_handled
def snap_restore(
    snapshot_id: Annotated[str, typer.Argument(metavar="ID", help=_REF_HELP)],
    paths: Annotated[
        list[str] | None,
        typer.Option(
            "--paths",
            help="Only this path, relative to the flavor folder; repeat for more. "
            "If the snapshot has no file at a path you name, inside a folder it saved, the "
            "file on disk at that path is deleted. A named path is "
            "restored even when its file-map row says wowlab leaves it alone, except an "
            "executable, which never is. Nor is a screenshot: wowlab never writes "
            "Screenshots/.",
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the plan and change nothing.")
    ] = False,
    yes: YesOpt = False,
    root: RootOpt = None,
    into_flavor: IntoFlavorOpt = None,
    into_root: Annotated[
        Path | None,
        typer.Option(
            "--into-root",
            metavar="PATH",
            help="Restore into the install at PATH (the folder that holds .build.info) "
            "instead of the one the snapshot was taken in, such as a reinstall in another "
            "folder or a second install on this machine. Into the flavor folder of the same "
            "name unless --into-flavor names one.",
        ),
    ] = None,
    map_account: MapAccountOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Put back files from a snapshot, through the write gate: the client must be
    closed, a pre-write snapshot is taken first, and `wowlab undo` reverses it.

    Without --paths, files added under the snapshot's folders since it was
    taken are never deleted: the plan lists the ones under WTF, Interface
    and Fonts under "Left alone".

    Without --paths, files whose file-map Edit is `no` (client-written
    backups such as `.lua.bak` and `.old`, `Interface/AddOns/Blizzard_*`,
    file-browser metadata) are left alone and counted in the plan; name one
    with --paths to restore it. An executable in an addon folder (its row is
    `addon-executable`) whose content changed or that went missing is left
    alone and counted too, but --paths cannot restore it: wowlab never
    writes one, so reinstall the addon to get it back. Screenshots in a snapshot taken with
    `snap create --screenshots` are never restored (wowlab never writes
    Screenshots/, not even with --paths): a whole restore leaves them in the
    snapshot, counts them, and restores the rest.

    When a beta launches, the game's folder gets a new name: restore into
    the new folder with --into-flavor, or into another install folder with
    --into-root. --paths are then spelled as `snap show` lists them.

    \f
    JSON: RestoreReport."""
    into = into_flavor is not None or into_root is not None
    if map_account and not into:
        raise CliError("--map-account is used with --into-flavor or --into-root")
    if into_root is not None and root is not None:
        raise CliError("--into-root names the install to restore into; leave out --root")
    if into_flavor is not None:
        _one_folder_name(into_flavor, "--into-flavor")
    accounts_given = _account_pairs(map_account)
    store = snapshot.SnapshotStore()
    manifest = store.show(_resolve_ref(store, snapshot_id))
    inst, _ = _discover(into_root if into_root is not None else root)
    wanted_folder = into_flavor if into_flavor is not None else manifest.flavor_folder
    if wanted_folder is None:
        raise CliError(f"snapshot {manifest.id} names no flavor folder; it cannot be restored")
    match = [f for f in inst.flavors if f.folder == wanted_folder]
    if not match and into:
        raise CliError(
            f"{inst.root} has no flavor folder {wanted_folder!r} (flavors: {_flavor_list(inst)})"
        )
    if not match:
        raise CliError(
            f"snapshot {manifest.id} is of flavor folder {manifest.flavor_folder!r}, which "
            f"{inst.root} does not have (flavors: {_flavor_list(inst)}). Flavor folders are "
            "renamed between beta and launch; to restore into the new one, name it with "
            "--into-flavor FOLDER."
        )
    chosen = match[0]
    target = _into_target(chosen, accounts_given) if into else None
    into_report: IntoReport | None = None
    back: dict[str, str] = {}
    say = _say_err if json_out else _say
    # With --json the plan goes to stdout as JSON at the end; when a prompt
    # will ask first, the text plan goes to stderr so the question is not blind.
    prompting = json_out and not yes and not dry_run
    show = _say if not json_out else _say_err
    label = f"restore {manifest.id}"
    if paths:
        label += " (" + ", ".join(paths) + ")"
    named = list(paths) if paths else None  # no --paths: everything the snapshot holds
    lay = layout.Layout.for_flavor(chosen, inst.root)
    folder = chosen.folder
    rest: list[snapshot.Entry] = list(manifest.entries)
    aside: list[NotRestoredPath] = []
    names: list[str] | None = None
    executables: list[SkippedPath] = []
    left_alone: tuple[str, ...] = ()  # a whole restore's added files, never touched
    with guard.transaction(chosen, label=label, dry_run=True) as tx:
        if target is not None:
            # The gate maps the account folders, or refuses, before anything
            # here reads the target. The plan reads the snapshot as the
            # target spells it; the gate is always given the snapshot's names.
            accounts = tx.restore(manifest.id, [], into=target)
            into_report = _into_report(manifest, accounts)
            back = {b: a for a, b in accounts}
            manifest = profiles.mapped_view(manifest, folder, inst.root, dict(accounts))
            rest = list(manifest.entries)

        def by_name(names: list[str]) -> list[str]:
            return [profiles.map_account(n, back) for n in names]

        if named is not None:
            try:
                tx.restore(manifest.id, named, into=target)
            except guard.PathNotAllowedError as exc:
                _screenshot_note(lay, manifest, folder, named, exc)
                _executable_note(lay, named, exc)
                raise
        else:
            # The gate's snapshot checks (another install, another flavor)
            # come before anything here reads the disk.
            tx.restore(manifest.id, [], into=target)
            # Screenshots, which the gate never writes, are set aside; the
            # gate would otherwise refuse the whole restore.
            rest, aside = _set_aside(lay, manifest, inst.root, folder)
            # An Edit "no" file named like an executable whose content changed
            # or that went missing is skipped by its row: the gate never
            # writes one, and would refuse the whole restore for it (M13-38).
            rest, executables = _changed_executables(lay, rest, inst.root, folder)
            try:
                if not aside and not executables:
                    tx.restore(manifest.id, into=target)
                    left_alone = tx.left_alone
                else:
                    if any(not e.path.startswith(folder + "/") for e in rest):
                        # refused: a path outside this flavor
                        tx.restore(manifest.id, into=target)
                    # Restored by name, so the gate lists nothing it leaves
                    # alone: the same list, by the same rule, read here.
                    left_alone = _added_since(store, manifest, inst.root, folder)
                    names, left = _names_for_whole(rest, inst.root, folder)
                    tx.restore(manifest.id, by_name(names), into=target)
            except guard.PathNotAllowedError as exc:
                # An executable the gate still refuses (its bytes are the
                # snapshot's but its mode is not, or a link or a folder
                # stands where it was) refuses the whole restore; say what
                # the owner can do, as --paths does (M13-48).
                prefix = folder + "/"
                held = [e.path.removeprefix(prefix) for e in rest if e.path.startswith(prefix)]
                _executable_note(lay, held, exc)
                raise
        whole_plan = tx.plan
    skipped: list[SkippedPath] = []
    plan = list(whole_plan)
    if named is None:
        plan, skipped = _client_managed(lay, whole_plan)
        skipped = sorted([*skipped, *executables], key=lambda s: s.path)
    # With something left out, the rest is restored by name: every entry the
    # gate would check in a whole restore but the skipped ones, so the gate
    # writes exactly the kept paths and a later change still shows in its
    # plan. The unnamed links and executables are read again before commit.
    wanted = named
    left_out: list[snapshot.Entry] = []
    if named is None and (skipped or aside):
        if names is None:
            names, left = _names_for_whole(rest, inst.root, folder)
        left_out = left
        skipped_paths = {s.path for s in skipped}
        wanted = [n for n in names if n not in skipped_paths]

    def report(*, applied: bool, transaction: str | None = None) -> RestoreReport:
        notes = _relogin_notes([i.path for i in plan]) if applied else []
        return RestoreReport(
            snapshot_id=manifest.id,
            flavor_path=str(chosen.path),
            snapshot_version=manifest.flavor_version,
            flavor_version=chosen.version,
            paths=named,
            plan=plan,
            skipped=skipped,
            not_restored=aside,
            dry_run=dry_run,
            applied=applied,
            transaction=transaction,
            notes=notes,
            into=into_report,
            plan_lines=[_safe(line) for line in plan_lines],
        )

    shots_differ = any(a.disk != "same" for a in aside)
    plan_lines: list[str] = []
    if plan or skipped or aside or left_alone:
        plan_lines.append(
            f"Restore from snapshot {manifest.id} into {chosen.path}: {len(plan)} change(s)"
        )
        if into_report is not None:
            plan_lines.extend(_into_lines(into_report, chosen))
        if manifest.flavor_version != chosen.version:
            plan_lines.append(
                f"  Taken on version {_version(manifest.flavor_version)}; {folder} is "
                f"now on {_version(chosen.version)}."
            )
        detailed = False
        for item in plan:
            plan_lines.append(_plan_line(item))
            detail = _plan_item_lines(store, chosen.path, item)
            plan_lines.extend(detail)
            detailed = detailed or (bool(detail) and wtfdiff.kind_of(item.path) is not None)
        plan_lines.extend(_skipped_lines(skipped))
        if aside:
            plan_lines.extend(_not_restored_lines(manifest.id, aside))
        plan_lines.extend(_left_alone_lines(left_alone))
        plan_lines.extend(_plan_footer(detailed, [i.path for i in plan]))
    if not json_out or (prompting and plan):
        for line in plan_lines:
            show(line)
    if not plan:
        if json_out:
            _emit(report(applied=False))
        elif skipped and shots_differ:
            _say(
                "Nothing to restore but the skipped files and the screenshots wowlab never "
                "writes; nothing was changed."
            )
        elif skipped:
            _say("Nothing to restore but the skipped files; nothing was changed.")
        elif shots_differ:
            _say("Nothing to restore but the screenshots wowlab never writes; nothing was changed.")
        else:
            _say(f"Nothing to restore: {folder} already matches snapshot {manifest.id}.")
        return
    if dry_run:
        if json_out:
            _emit(report(applied=False))
        else:
            _say("Dry run: nothing was changed.")
        return
    try:
        _confirm("Apply these changes?", yes)
    except typer.Exit:
        if json_out:
            _emit(report(applied=False))
        raise
    with guard.transaction(chosen, label=label) as tx:
        if wanted is None or named is not None:
            tx.restore(manifest.id, wanted, into=target)
        else:
            tx.restore(manifest.id, by_name(wanted), into=target)
        moved = any(not _unchanged(inst.root, folder, e) for e in left_out)
        if tx.plan != tuple(plan) or moved:
            # Raising inside the transaction makes the gate roll it back.
            raise CliError(
                "the files changed after the plan was shown, so the restore was rolled "
                "back; run the command again to see the new plan"
            )
    record = guard.history()[-1].id
    done = report(applied=True, transaction=record)
    if json_out:
        _emit(done)
        return
    say(f"Restored {len(plan)} file(s). `wowlab undo` puts back what was there before.")
    for n in done.notes:
        if n not in plan_lines:  # the plan above already said it
            say(n)


def _undo_lines(last: guard.HistoryRecord) -> list[str]:
    """`undo`'s text plan (`_undo_plan`, its lines only)."""
    return _undo_plan(last)[0]


def _undo_plan(last: guard.HistoryRecord) -> tuple[list[str], list[str]]:
    """`undo`'s text plan, and the paths it lists: only the steps the undo will take. A path whose
    disk already holds what was there before that transaction (a file a
    rollback already put back or removed) is skipped by the undo, so it is
    not listed; one wowlab cannot read is listed. Under each file, what the
    undo changes by name: from what the install holds at that path now
    (read read-only through the guarded opener, whatever it holds) to what
    was there before that transaction (in its pre-write snapshot), since the
    undo replaces the file whatever is on disk. When the disk no longer
    holds what that transaction wrote, a line says the comparison includes
    those later changes and who probably made them, by the record's state
    (`_CHANGED_LINE`; a state this version does not know gets
    `_CHANGED_UNKNOWN_STATE`, which guesses no one). In every state it also says that the undo
    replaces those changes, that its own snapshot keeps a copy of them
    first, and that a second undo, run next, brings them back."""
    store = snapshot.SnapshotStore()
    flavor_path = Path(last.flavor_path)
    changed = _CHANGED_LINE.get(last.state, _CHANGED_UNKNOWN_STATE)
    lines = [
        f'Undo {last.id} ("{last.label}", {last.created_at}; {_journal_state_words(last.state)})',
        f"  in {last.flavor_path}:",
    ]
    detailed = False
    listed: list[str] = []
    for p in last.paths:
        now = _disk_now(flavor_path, p.path)
        now_sha = (
            None if now is None or isinstance(now, _Unread) else hashlib.sha256(now).hexdigest()
        )
        if not isinstance(now, _Unread) and now_sha == p.before:
            continue  # already as it was before: the undo leaves it alone
        listed.append(p.path)
        if p.before is None:
            lines.append(f"  delete     {p.path}  (created by that transaction)")
        elif p.after is None:
            lines.append(f"  recreate   {p.path}  (deleted by that transaction)")
        else:
            lines.append(f"  put back   {p.path}  (as it was before that transaction)")
        if _wants_detail(p.path):
            back = _stored(store, p.before)
            detail = _detail_lines(p.path, now, back, limit=_DETAIL_PLAN_LIMIT)
            if detail and not isinstance(now, _Unread) and now_sha != p.after:
                lines.append(changed)
            lines.extend(detail)
            detailed = detailed or (bool(detail) and wtfdiff.kind_of(p.path) is not None)
    folders = [d for d in last.created_dirs if os.path.lexists(flavor_path / d)]
    lines.extend(f"  remove folder {d}  (created by that transaction, if empty)" for d in folders)
    if not listed and not folders:
        unfinished = last.state in ("open", "rollback_incomplete")
        if not last.snapshot_id:
            lines.append(_STOPPED_BEFORE_ANY_FILE)
        else:
            lines.append(_NOTHING_TO_PUT_BACK_UNFINISHED if unfinished else _NOTHING_TO_PUT_BACK)
    lines.extend(_plan_footer(detailed, listed))
    return lines, listed


def _closed_unsnapshotted(closed: guard.HistoryRecord, now: guard.HistoryRecord | None) -> str:
    """What `undo` says after closing a change that stopped before it
    changed any file (2026-11-11 (a)): no file changed, nothing to reverse.
    When it was an undo itself, the change it was undoing is the next
    target again, and the line names it with its own command (a1). When the
    new target is a change that did not finish, the line names that (a2)."""
    if closed.undoes is not None and now is not None and now.id == closed.undoes:
        return (
            "Closed; that undo stopped before it changed any file. The change it was "
            f"undoing, {now.id}, is not undone yet: wowlab undo --expect {now.id}"
        )
    if now is not None and now.state in ("open", "rollback_incomplete"):
        return (
            "Closed; that change stopped before it changed any file. The change before it, "
            f"{now.id}, did not finish: wowlab undo --expect {now.id}"
        )
    return (
        "Closed; that change stopped before it changed any file, so there was nothing to put "
        "back and nothing for `wowlab undo` to reverse."
    )


def _undone_unlisted(written: Sequence[guard.PathChange], listed: Sequence[str]) -> list[str]:
    """The result's lines for each file the undo wrote that its plan did not
    list: it changed after the plan read it, so the undo put it back too.
    Named once, after "Undone.", so the login caveat that may follow has a
    file on screen to point at (M16-105). `written` is the undo's own
    journal record, whose `before` is the disk the undo found."""
    shown = set(listed)
    lines: list[str] = []
    for p in written:
        if p.path in shown:
            continue
        shown.add(p.path)
        if p.after is None:
            verb = "also deleted   "
        elif p.before is None:
            verb = "also recreated "
        else:
            verb = "also put back  "
        lines.append(f"  {verb}{p.path}  (it changed after the plan was shown)")
    return lines


@app.command(short_help="Undo the last change wowlab made to your game files.")
@_handled
def undo(
    yes: YesOpt = False,
    expect: Annotated[
        str | None,
        typer.Option(
            "--expect",
            metavar="ID",
            help="Undo only if the change to undo is this journal record (undone in an "
            "earlier run's --json): else refused, exit 3, nothing undone. For a plan shown "
            "by one run and confirmed by another.",
        ),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """Undo the most recent change wowlab made to your game files: a
    `snap restore`, `profile apply`, `sv merge`, `addon install` or
    `addon remove`, or an earlier undo. It shows the plan and asks first, then
    puts your files back as they were before that change, from the snapshot
    taken before it; files it added are deleted.

    Undo steps back one change only. An undo is itself a change, so running
    `wowlab undo` a second time redoes what the first one undid. To go further
    back, pick an older snapshot with `snap list` and put it back with
    `snap restore`.

    Only the change the plan shows is undone: if another change is recorded
    after the plan was shown, nothing is undone.

    A change or undo that was stopped before it changed any file is closed
    and then skipped: the next `wowlab undo` offers the change before it.

    \f
    JSON: UndoReport."""
    records = guard.history()
    # The undo target (2026-11-11 (a1), (a2)): the newest record that is not
    # passed over, so a closed change that stopped before it changed any file
    # is skipped.
    target = guard._undo_target(records)
    if expect is not None and (target is None or target.id != expect):
        # Refused before any plan. Closing a record in place can move the
        # target, so this read is not final; `guard.undo` compares again
        # under its store lock, below.
        newest = (
            target.id
            if target is not None
            else "none: nothing in the journal can be undone"
            if records
            else "none: the journal is empty"
        )
        raise CliError(
            f"the change to undo is {newest}, not the expected {expect}: "
            "another change was journaled after the plan was made, so nothing was undone; "
            "run the command again to see the new plan",
            EXIT_REFUSED,
        )
    if target is None:
        raise CliError(
            "nothing to undo: every change in the write gate's journal stopped before it "
            "changed any file"
            if records
            else "nothing to undo: the write gate's journal is empty"
        )
    last = target

    def report(
        *, applied: bool, transaction: str | None = None, written: Sequence[str] = ()
    ) -> UndoReport:
        return UndoReport(
            undone=last.id,
            label=last.label,
            state=last.state,
            created_at=last.created_at,
            flavor_path=last.flavor_path,
            plan=list(last.paths),
            created_dirs=list(last.created_dirs),
            applied=applied,
            transaction=transaction,
            # From what the undo wrote (its own journal record), not every path
            # of the record undone: a file the disk already held as it was
            # before is skipped by the undo, so it is not listed in the plan
            # and gets no note; one changed after the plan read it is
            # rewritten, so it does (M16-102).
            notes=_relogin_notes(written) if applied else [],
            plan_lines=[_safe(line) for line in plan_lines],
            written_paths=list(written),
            unlisted_written=[w for w in written if w not in set(listed)],
        )

    plan_lines: list[str] = []
    listed: list[str] = []
    # An `open` record that names no file still needs closing (a change
    # killed before it changed anything): it goes on to the plan and
    # `guard.undo`, which closes it, so `doctor` stops reporting it.
    if not last.paths and not last.created_dirs and last.state != "open":
        if json_out:
            _emit(report(applied=False))
        else:
            _say(f'The most recent transaction, {last.id} ("{last.label}"), changed no files.')
        return
    plan_lines, listed = _undo_plan(last)
    # With --json, the text plan goes to stderr when a prompt will ask first.
    show = _say if not json_out else _say_err
    if not json_out or not yes:
        for line in plan_lines:
            show(line)
    try:
        _confirm("Undo it?", yes)
    except typer.Exit:
        if json_out:
            _emit(report(applied=False))
        raise
    now = guard.history()
    again = guard._undo_target(now)
    if again is None or again.id != last.id:
        raise CliError(
            "the journal changed after the plan was shown; nothing was undone, run the "
            "command again to see the new plan",
            EXIT_ERROR if expect is None else EXIT_REFUSED,
        )
    # With --expect, `last.id` is `expect` (checked above).
    guard.undo(expected_id=last.id)
    after = guard.history()
    if len(after) == len(now):
        # Closed in place: the change stopped before it changed any file, so
        # the undo restored nothing and journaled no record of its own.
        done = report(applied=True)
        if json_out:
            _emit(done)
            return
        _say(_closed_unsnapshotted(last, guard._undo_target(after)))
        return
    undone = after[-1]
    done = report(applied=True, transaction=undone.id, written=[p.path for p in undone.paths])
    if json_out:
        _emit(done)
        return
    if not undone.paths:
        # Judged from the undo's own record, not the plan: a file rewritten
        # after the plan read it is put back and listed there. No file
        # changed, so no login note applies (`--json` keeps them).
        _say(
            "Undone; no file needed changing. This undo is journaled, but there is nothing "
            "for `wowlab undo` to reverse."
        )
        return
    _say("Undone. This undo is journaled too: `wowlab undo` again reverses it.")
    for line in _undone_unlisted(undone.paths, listed):
        _say(line)
    for n in done.notes:
        if n not in plan_lines:  # the plan above already said it
            _say(n)
