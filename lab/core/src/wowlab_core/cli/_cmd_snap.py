"""`wowlab snap create`, `list`, `show`, `diff`, `verify`, `gc`, `export` and
`import` (M16-111)."""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import Field

from wowlab_core import (
    firstrun,
    guard,
    install,
    layout,
    luadata,
    profiles,
    snaparchive,
    snapshot,
    wtfdiff,
)
from wowlab_core.cli._changes import (
    _DETAIL_INDENT,
    _detail_lines,
    _is_saved_variables,
    _leaves,
    _stored,
    _sv_summary,
)
from wowlab_core.cli._cmd_doctor import (
    _STORE_MODES_NOTE,
    _exposure_line,
)
from wowlab_core.cli._cmd_filemap import (
    _stored_target,
)
from wowlab_core.cli._cmd_sv import (
    _is_character_lab_file,
)
from wowlab_core.cli._common import (
    _KIND_WORDS,
    EXIT_ERROR,
    EXIT_USAGE,
    CliError,
    FlavorOpt,
    JsonOpt,
    NotesOpt,
    RootOpt,
    YesOpt,
    _bytes,
    _client_running,
    _confirm,
    _discover,
    _emit,
    _emit_manifest,
    _given_flavor,
    _handled,
    _note,
    _Out,
    _pre_write_ids,
    _safe,
    _say,
    _say_notes,
    _say_prose,
    _select_flavor,
    snap_app,
)

# ─── snap ────────────────────────────────────────────────────────────────────


class SnapshotSummary(_Out):
    id: str
    created_at: str
    label: str
    install_root: str
    flavor_folder: str | None
    flavor_version: str | None
    client_running: bool | None
    files: int
    bytes: int


class SnapListReport(_Out):
    """`wowlab snap list --json`. The command exits 1 when `damaged` is not empty."""

    snapshots: list[SnapshotSummary]
    damaged: list[snapshot.InvalidManifest]


_REF_HELP = (
    "A snapshot id, a unique start or end of one, latest, latest-user (the newest "
    "one you took), or ~N (N before the newest). Snapshots from before wowlab "
    "recorded who took each one count as yours unless the change history names "
    "them as automatic copies; if that history cannot be read, they all count as yours."
)


def _resolve_ref(store: snapshot.SnapshotStore, reference: str) -> str:
    """The full id `reference` names (`snapshot.resolve_reference`)."""
    ids = _pre_write_ids(store) if reference == "latest-user" else frozenset[str]()
    return snapshot.resolve_reference(store, reference, ids)


def _summary(m: snapshot.Manifest) -> SnapshotSummary:
    return SnapshotSummary(
        id=m.id,
        created_at=m.created_at,
        label=m.label,
        install_root=m.install_root,
        flavor_folder=m.flavor_folder,
        flavor_version=m.flavor_version,
        client_running=m.client_running,
        files=sum(1 for e in m.entries if e.kind == "file"),
        bytes=sum(e.size for e in m.entries if e.kind == "file"),
    )


# `snap gc` leaves objects younger than this, even when no manifest refers to
# them. Every `snap create` now holds the store lock, the first one included
# (`guard.store_lock(create=True)`, M10-17), so gc cannot run in the middle of
# one; the grace period stays as defence in depth for a create that holds no
# store lock: any library caller of `SnapshotStore.create`.
GC_GRACE_SECONDS = 3600.0


@snap_app.command("create", short_help="Snapshot settings, addons and saved data into the store.")
@_handled
def snap_create(
    message: Annotated[str, typer.Option("-m", "--message", help="A label for the snapshot.")] = "",
    screenshots: Annotated[
        bool,
        typer.Option(
            "--screenshots",
            help="Also capture Screenshots/ (kept for show and diff; a restore never "
            "writes them back).",
        ),
    ] = False,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Snapshot a flavor's WTF/, Interface/ and Fonts/ into the store (reads the
    install only). With several flavors and no --flavor, takes one snapshot of
    each flavor and says which flavor each one is. Labels starting with
    `profile:` or `deleted-profile:` are `wowlab profile`'s and are refused.

    \f
    JSON: the manifest (snapshot.Manifest) when one flavor is snapshotted;
    SnapCreateReport, one manifest per flavor, when several are."""
    if message.startswith(profiles.RESERVED_LABEL_PREFIXES):
        raise CliError(
            f"labels starting with {' or '.join(profiles.RESERVED_LABEL_PREFIXES)} belong to "
            "`wowlab profile`; use `wowlab profile save` or choose another label",
            EXIT_USAGE,
        )
    inst, _ = _discover(root)
    if _given_flavor(flavor) is None and len(inst.flavors) > 1:
        _snap_every_flavor(inst, message, screenshots, json_out)
        return
    chosen = _select_flavor(inst, flavor)
    manifest = _snap_one(inst, chosen, message, screenshots, several=False)
    if json_out:
        _emit_manifest(manifest)
        return
    _say_created(manifest, inst, chosen)


class SnapCreateReport(_Out):
    """`wowlab snap create --json` on an install with several flavors and no
    `--flavor` (M16-124): one manifest per flavor, in the order discovery
    lists the flavors. Each manifest's `flavor_folder` says which flavor it
    is. With one flavor, or one named, the command prints the bare manifest
    instead."""

    snapshots: list[snapshot.Manifest]


def _snap_one(
    inst: install.Install,
    chosen: install.Flavor,
    message: str,
    screenshots: bool,
    *,
    several: bool,
) -> snapshot.Manifest:
    """One snapshot of `chosen` into wowlab's own store: the install is only
    read (L1)."""
    lay = layout.Layout.for_flavor(chosen, inst.root)
    running = _client_running(inst, chosen)
    # The one sequence a first run of `doctor` uses too (M13-04): refuse a
    # store that holds the install with nothing created (M10-19), then take
    # the store lock, which refuses a store inside any install before
    # creating anything (L1), then capture.
    manifest = firstrun.snapshot_flavor(
        snapshot.SnapshotStore(),
        inst,
        chosen,
        lay,
        label=message,
        screenshots=screenshots,
        client_running=running,
    )
    if running is not False:
        if several:
            _note(
                "{}: wowlab could not confirm the client was closed: SavedVariables on disk "
                "are as of its last logout or /reload, and the client overwrites them at its "
                "next logout, /reload or exit.",
                chosen.folder,
            )
        else:
            _note(
                "wowlab could not confirm the client was closed: SavedVariables on disk are as "
                "of its last logout or /reload, and the client overwrites them at its next "
                "logout, /reload or exit."
            )
    return manifest


def _say_created(
    manifest: snapshot.Manifest, inst: install.Install, chosen: install.Flavor
) -> None:
    s = _summary(manifest)
    _say(f"Snapshot {manifest.id}")
    _say(f"  {chosen.folder} ({chosen.version or 'no version'}) in {inst.root}")
    if manifest.label:
        _say(f"  label: {manifest.label}")
    _say(f"  captured: {', '.join(manifest.subtrees)}")
    _say(f"  {s.files} file(s), {_bytes(s.bytes)}")


def _snap_every_flavor(
    inst: install.Install, message: str, screenshots: bool, json_out: bool
) -> None:
    """`snap create` with several flavors and none named: one snapshot of
    each, in discovery's order (owner decision, 2026-11-12, M16-124). A
    snapshot only reads the install, so taking every flavor's is safe; a
    write still needs the player to name its flavor. An error stops the run,
    and its notes name the snapshots already taken."""
    taken: list[tuple[install.Flavor, snapshot.Manifest]] = []
    if not json_out:
        _say(f"{len(inst.flavors)} flavors in {inst.root}: one snapshot of each.")
    for chosen in inst.flavors:
        try:
            manifest = _snap_one(inst, chosen, message, screenshots, several=True)
        except Exception as exc:
            if taken:
                done = ", ".join(f"{m.id} ({f.folder})" for f, m in taken)
                exc.add_note(
                    f"{chosen.folder} was not snapshotted; snapshots already taken: {done}"
                )
            else:
                exc.add_note(f"{chosen.folder} was not snapshotted; no snapshot was taken")
            raise
        taken.append((chosen, manifest))
        if not json_out:
            _say_created(manifest, inst, chosen)
    if json_out:
        _emit(SnapCreateReport(snapshots=[m for _, m in taken]))


@snap_app.command("list", short_help="Every snapshot, oldest first.")
@_handled
def snap_list(json_out: JsonOpt = False) -> None:
    """Every snapshot, oldest first; names each damaged manifest and then exits 1.

    \f
    JSON: SnapListReport."""
    listing = snapshot.SnapshotStore().list_lenient()
    report = SnapListReport(
        snapshots=[_summary(m) for m in listing.manifests], damaged=list(listing.invalid)
    )
    if json_out:
        _emit(report)
    else:
        if not report.snapshots:
            _say("No snapshots.")
        for s in report.snapshots:
            running = (
                "  (client was running)"
                if s.client_running
                else "  (client state not known)"
                if s.client_running is None
                else ""
            )
            label = f'  "{s.label}"' if s.label else ""
            version = s.flavor_version or "no version"
            _say(f"{s.id}  {s.flavor_folder}  {version}  {s.files} files{label}{running}")
    for bad in report.damaged:
        _note("damaged manifest {}: {}", bad.name, bad.reason)
    if report.damaged:
        raise typer.Exit(EXIT_ERROR)


@snap_app.command("show", short_help="One snapshot and every path in it.")
@_handled
def snap_show(
    snapshot_id: Annotated[str, typer.Argument(metavar="ID", help=_REF_HELP)],
    json_out: JsonOpt = False,
) -> None:
    """One snapshot and every path in it.

    \f
    JSON: the manifest (snapshot.Manifest), with its kind."""
    store = snapshot.SnapshotStore()
    manifest = store.show(_resolve_ref(store, snapshot_id))
    # The effective kind, in the text and the JSON alike: one written before
    # kinds existed is what the journal tells, and an imported one is said
    # to be so whatever kind its manifest claims (M16-04).
    pre_write = _pre_write_ids(store) if manifest.kind is None else frozenset[str]()
    kind = snapshot.kind_of(manifest, pre_write, store.imported_ids())
    manifest = manifest.model_copy(update={"kind": kind})
    if json_out:
        _emit_manifest(manifest)
        return
    _say(f"Snapshot {manifest.id}")
    _say(f"  taken:    {manifest.created_at}")
    _say(f"  kind:     {kind}, {_KIND_WORDS[kind]}")
    if manifest.label:
        _say(f"  label:    {manifest.label}")
    _say(f"  install:  {manifest.install_root}")
    _say(f"  flavor:   {manifest.flavor_folder} ({manifest.flavor_version or 'no version'})")
    _say(f"  captured: {', '.join(manifest.subtrees)}")
    if manifest.client_running:
        _say(
            "  The client was running when this was taken: its SavedVariables are as of its "
            "last logout or /reload, not the moment of the snapshot."
        )
    elif manifest.client_running is None:
        _say("  Whether the client was running was not known when this was taken.")
    for e in manifest.entries:
        if e.kind == "symlink":
            _say(f"  {e.path} -> {_stored_target(e.target)} (link, recorded, not followed)")
        else:
            _say(f"  {e.path}  {_bytes(e.size)}  {(e.sha256 or '')[:12]}")


class LuaChange(_Out):
    path: str  # `Var.key[3]` style
    before: str | None  # None: added
    after: str | None  # None: removed


class ChangedFile(_Out):
    path: str
    before: snapshot.Entry
    after: snapshot.Entry
    lua: list[LuaChange] | None  # set for a SavedVariables file when both sides parse
    lua_note: str | None  # why there is no structural diff for a SavedVariables file


class DiffReport(_Out):
    """`wowlab snap diff --json`."""

    a: str
    b: str
    a_version: str | None  # the flavor version each snapshot was taken on
    b_version: str | None
    added: list[snapshot.Entry]
    removed: list[snapshot.Entry]
    changed: list[ChangedFile]
    mode_changed: list[snapshot.Change]
    # The text output, line by line, as the terminal shows it: under a changed
    # bindings, macros or CVar file the keys, macros and CVars by name, under
    # a SavedVariables file one summary line (M16-10).
    plan_lines: list[str] = Field(default_factory=list)


def _lua_diff(
    store: snapshot.SnapshotStore, change: snapshot.Change
) -> tuple[list[LuaChange] | None, str | None]:
    if not _is_saved_variables(change.path):
        return None, None
    if change.before.sha256 is None or change.after.sha256 is None:
        return None, "one side is a link"
    # The parser's own bound (M16-72): a side over it would be refused by
    # `luadata.parse` anyway, so it is never inflated into memory first.
    largest = max(change.before.size, change.after.size)
    if largest > luadata.MAX_FILE_BYTES:
        return None, (
            f"not compared as data: one side holds {largest} bytes, "
            f"more than the {luadata.MAX_FILE_BYTES} wowlab reads as data"
        )
    try:
        # By content hash: the store checks the bytes against the name.
        before = _leaves(store.read_object(change.before.sha256, size=change.before.size))
        after = _leaves(store.read_object(change.after.sha256, size=change.after.size))
    except luadata.LuaDataError as exc:
        return None, f"not compared as data: one side does not parse ({exc})"
    except snapshot.SnapshotError as exc:
        # A side the store cannot supply has not parsed either: keep the
        # plain changed-path entry, and say why there is no more.
        return None, f"contents unreadable: {exc}"
    changes = [
        LuaChange(path=p, before=before.get(p), after=v)
        for p, v in after.items()
        if before.get(p) != v
    ]
    changes.extend(
        LuaChange(path=p, before=v, after=None) for p, v in before.items() if p not in after
    )
    return changes, None


@snap_app.command("diff", short_help="What changed between two snapshots.")
@_handled
def snap_diff(
    a: Annotated[str, typer.Argument(metavar="A", help="The older snapshot. " + _REF_HELP)],
    b: Annotated[str, typer.Argument(metavar="B", help="The newer snapshot. " + _REF_HELP)],
    json_out: JsonOpt = False,
) -> None:
    """Files added, removed and changed from A to B; for SavedVariables that
    parse, which values changed.

    \f
    JSON: DiffReport."""
    store = snapshot.SnapshotStore()
    diff = store.diff(_resolve_ref(store, a), _resolve_ref(store, b))
    a_version = store.show(diff.a).flavor_version
    b_version = store.show(diff.b).flavor_version
    changed = []
    for c in diff.changed:
        lua, note = _lua_diff(store, c)
        changed.append(
            ChangedFile(path=c.path, before=c.before, after=c.after, lua=lua, lua_note=note)
        )
    report = DiffReport(
        a=diff.a,
        b=diff.b,
        a_version=a_version,
        b_version=b_version,
        added=list(diff.added),
        removed=list(diff.removed),
        changed=changed,
        mode_changed=list(diff.mode_changed),
    )
    lines = [f"From {diff.a} to {diff.b}:"]
    if a_version != b_version:
        lines.append(
            f"  ({diff.a} was taken on {_version(a_version)}; {diff.b} on {_version(b_version)})"
        )
    if diff.is_empty:
        lines.append("  no differences")
    lines.extend(f"  added    {e.path}" for e in report.added)
    lines.extend(f"  removed  {e.path}" for e in report.removed)
    for cf in report.changed:
        lines.append(f"  changed  {cf.path}")
        lines.extend(_diff_detail_lines(store, cf))
    lines.extend(
        f"  mode     {m.path} ({m.before.mode:o} -> {m.after.mode:o})" for m in report.mode_changed
    )
    report = report.model_copy(update={"plan_lines": [_safe(line) for line in lines]})
    if json_out:
        _emit(report)
        return
    for line in lines:
        _say(line)


def _diff_detail_lines(store: snapshot.SnapshotStore, cf: ChangedFile) -> list[str]:
    """The lines under one changed file of `snap diff`: every key, macro or
    CVar by name; for SavedVariables the summary line, then each value."""
    if _is_saved_variables(cf.path):
        before_size, after_size = _bytes(cf.before.size), _bytes(cf.after.size)
        lines: list[str] = []
        if cf.lua is None:
            if cf.lua_note:
                lines.append(f"{_DETAIL_INDENT}({cf.lua_note})")
            lines.append(_sv_summary(None, 0, 0, before_size, after_size))
            return lines
        removed = sum(1 for lc in cf.lua if lc.after is None)
        added = sum(1 for lc in cf.lua if lc.before is None)
        lines.append(
            _sv_summary(removed, added, len(cf.lua) - removed - added, before_size, after_size)
        )
        for lc in cf.lua:
            if lc.before is None:
                lines.append(f"{_DETAIL_INDENT}+ {lc.path} = {lc.after}")
            elif lc.after is None:
                lines.append(f"{_DETAIL_INDENT}- {lc.path} = {lc.before}")
            else:
                lines.append(f"{_DETAIL_INDENT}~ {lc.path}: {lc.before} -> {lc.after}")
        return lines
    if wtfdiff.kind_of(cf.path) is None:
        return []
    if cf.before.sha256 is None or cf.after.sha256 is None:
        return []  # a link on one side: no bytes to compare
    before = _stored(store, cf.before.sha256, cf.before.size)
    after = _stored(store, cf.after.sha256, cf.after.size)
    return _detail_lines(cf.path, before, after, limit=None)


@snap_app.command("verify", short_help="Check every stored file and snapshot.")
@_handled
def snap_verify(json_out: JsonOpt = False, notes_out: NotesOpt = False) -> None:
    """Re-hash every stored object and check every manifest. Exits 1 on any
    problem.

    \f
    JSON: snapshot.VerifyReport."""
    report = snapshot.SnapshotStore().verify()
    if json_out:
        _emit(report)
    else:
        _say(
            f"{report.objects_checked} object(s) and {report.manifests_checked} manifest(s) checked"
        )
        for name in report.corrupt_objects:
            if name == snapshot.OBJECTS_DIR_ENTRY:
                _say(
                    f"  {name} is a link: nothing under it was checked. snap create "
                    "refuses to write through it, but reads (restore, undo, snap diff) "
                    "still follow it; move it aside and put the real directory back"
                )
            else:
                _say(f"  corrupt object: {name}")
        for miss in report.missing_objects:
            _say(f"  missing object {miss.sha256[:12]} for {miss.path} in {miss.snapshot_id}")
        for bad in report.invalid_manifests:
            _say(f"  damaged manifest {bad.name}: {bad.reason}")
        if report.ok:
            _say("  all sound")
        exposed = _exposure_line(report.exposure)
        if exposed is not None:
            _say_prose(f"  {exposed}", hang="    ")
        # A standing caveat on Windows, so behind --notes (M16-41, fix round 1).
        _say_notes([] if report.exposure.checked else [_STORE_MODES_NOTE], notes_out)
    if not report.ok:
        raise typer.Exit(EXIT_ERROR)


@snap_app.command("gc", short_help="Remove stored files no snapshot needs any more (asks first).")
@_handled
def snap_gc(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Only list what would be removed.")
    ] = False,
    yes: YesOpt = False,
    json_out: JsonOpt = False,
) -> None:
    """Remove stored objects that nothing keeps, once they are older than an
    hour (in case a snapshot is being written). Kept: every
    object a snapshot refers to, and what each committed write left in a
    character's WowLab.lua (the lab-addon's per-character file, in any
    install), which the SavedVariables loader check of `sv merge` compares
    from. Anything else a write left, such as what a `snap restore` or
    profile apply wrote from a snapshot since deleted, is collected. Holds
    the store lock for the whole run, so no transaction or `snap create`
    runs meanwhile.

    \f
    JSON: snapshot.GcReport."""
    store = snapshot.SnapshotStore()
    if not store.path.is_dir():
        report = snapshot.GcReport(
            dry_run=dry_run, unreferenced=(), unreferenced_bytes=0, removed=()
        )
        if json_out:
            _emit(report)
        else:
            _say(f"No snapshot store at {store.path} yet; nothing to collect.")
        return
    with guard.store_lock(store.path):
        # What committed writes left in a character's WowLab.lua, the only
        # journal-named objects the loader check reads (M11-24, narrowed in
        # M11-31): every such write, not only the latest, since the check
        # walks back over older spans. Read under the store lock, so no
        # transaction adds a record between this and the removal.
        written = {
            change.after
            for record in guard.history(store=store.path)
            if record.state == "committed"
            for change in record.paths
            if change.after is not None and _is_character_lab_file(change.path)
        }
        report = store.gc(dry_run=True, grace_seconds=GC_GRACE_SECONDS, keep=written)
        if report.unreferenced and not dry_run:
            if not json_out:
                _say(
                    f"{len(report.unreferenced)} object(s) no snapshot refers to and the "
                    f"loader check does not need, {_bytes(report.unreferenced_bytes)} on disk."
                )
            _confirm("Remove them?", yes)
            report = store.gc(dry_run=False, grace_seconds=GC_GRACE_SECONDS, keep=written)
    # `dry_run` says whether this was a dry run: a run that was not, and found
    # nothing to remove, did not list as a dry run (M16-21).
    report = report.model_copy(update={"dry_run": dry_run})
    if json_out:
        _emit(report)
        return
    if report.skipped:
        _say(
            f"Left alone {len(report.skipped)} entr{'y' if len(report.skipped) == 1 else 'ies'} "
            "in the store that are links or not regular files, or changed before they "
            "could be removed (a link is never followed or deleted through): "
            + ", ".join(report.skipped)
        )
    if not report.unreferenced:
        _say(
            "Nothing to collect: every stored object is referred to by a snapshot, or is "
            "what a committed write left in a character's WowLab.lua (the loader check of "
            "`sv merge` compares from it), or is younger than an hour (kept in case a "
            "snapshot is being written)."
        )
    elif report.dry_run:
        _say(
            f"Would remove {len(report.unreferenced)} object(s), "
            f"{_bytes(report.unreferenced_bytes)} on disk (dry run)."
        )
    else:
        _say(f"Removed {len(report.removed)} object(s), {_bytes(report.removed_bytes)}.")


@snap_app.command("export", short_help="Write one snapshot to a file you can carry elsewhere.")
@_handled
def snap_export(
    snapshot_id: Annotated[str, typer.Argument(metavar="ID", help=_REF_HELP)],
    file: Annotated[
        Path,
        typer.Argument(
            metavar="FILE",
            help="The new file to write, outside any game install. An existing file is "
            "never replaced.",
        ),
    ],
    json_out: JsonOpt = False,
) -> None:
    """Write one snapshot, with every file it holds, into a single new FILE
    that `wowlab snap import` reads on another computer. Reads the store
    only and opens nothing in a game install; FILE inside an install is
    refused.

    \f
    JSON: snaparchive.ExportReport."""
    store = snapshot.SnapshotStore()
    report = snaparchive.export_snapshot(store, _resolve_ref(store, snapshot_id), file)
    if json_out:
        _emit(report)
        return
    _say(f"Exported snapshot {report.id} to {report.file}")
    _say(
        f"  {report.files} file(s), {_bytes(report.bytes)}; "
        f"the archive is {_bytes(report.archive_bytes)}"
    )


@snap_app.command("import", short_help="Add a snapshot from a file snap export wrote.")
@_handled
def snap_import(
    file: Annotated[
        Path, typer.Argument(metavar="FILE", help="A file written by wowlab snap export.")
    ],
    json_out: JsonOpt = False,
) -> None:
    """Add the snapshot in FILE to this computer's store. Every file in it
    is checked against its recorded size and content hash first; a damaged
    or unexpected archive adds nothing. Changes nothing in a game install:
    put the snapshot back with `wowlab snap restore`.

    \f
    JSON: snaparchive.ImportReport."""
    store = snapshot.SnapshotStore()
    with guard.store_lock(store.path, create=True):
        report = snaparchive.import_snapshot(store, file)
    if json_out:
        _emit(report)
        return
    if report.added:
        _say(f"Imported snapshot {report.id}")
    else:
        _say(f"Snapshot {report.id} is already in the store; nothing was added.")
    _say(f"  taken:   {report.created_at}, on the computer it came from")
    _say(
        f"  of:      {report.flavor_folder} ({report.flavor_version or 'no version'}) "
        f"in {report.install_root}"
    )
    if report.label:
        _say(f"  label:   {report.label}")
    _say(f"  files:   {report.files}")
    _say(
        f"To put it into an install on this computer: wowlab snap restore {report.id} "
        "--into-root <install folder> (add --into-flavor <folder> if its folder has "
        "another name there)."
    )


def _version(version: str | None) -> str:
    return version if version is not None else "no recorded version"


def _plan_line(item: guard.PlanItem) -> str:
    if item.before is None:
        return f"  create   {item.path}  ({_bytes(item.size or 0)})"
    if item.after is None:
        return f"  delete   {item.path}"
    return f"  replace  {item.path}  ({_bytes(item.size or 0)})"
