"""`wowlab profile save`, `list`, `show`, `delete` and `apply` (M16-111)."""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import Field

from wowlab_core import (
    guard,
    profiles,
    snapshot,
    wtfdiff,
)
from wowlab_core.cli._changes import (
    _SCOPE_LINE,
    IntoReport,
    _plan_item_lines,
)
from wowlab_core.cli._cmd_filemap import (
    _stored_target,
)
from wowlab_core.cli._cmd_restore import (
    MapAccountOpt,
    _account_pairs,
    _into_lines,
    _into_report,
    _into_target,
    _one_folder_name,
)
from wowlab_core.cli._cmd_snap import (
    SnapshotSummary,
    _plan_line,
    _summary,
    _version,
)
from wowlab_core.cli._common import (
    EXIT_ERROR,
    EXIT_USAGE,
    CliError,
    FlavorOpt,
    JsonOpt,
    RootOpt,
    YesOpt,
    _bytes,
    _client_running,
    _confirm,
    _discover,
    _emit,
    _flavor_list,
    _handled,
    _note,
    _open,
    _Out,
    _safe,
    _say,
    _say_err,
    profile_app,
)

# ─── profile ─────────────────────────────────────────────────────────────────


class ProfileSummary(_Out):
    name: str
    snapshot_id: str
    created_at: str
    presets: list[str]  # as given at save; informational
    install_root: str
    flavor_folder: str | None
    flavor_version: str | None
    client_running: bool | None
    subtrees: int
    files: int
    bytes: int


class ProfileListReport(_Out):
    """`wowlab profile list --json`. The command exits 1 when `damaged` is not empty."""

    profiles: list[ProfileSummary]
    damaged: list[snapshot.InvalidManifest]


class ProfileReport(_Out):
    """`wowlab profile save --json` and `wowlab profile show --json`."""

    profile: ProfileSummary
    subtrees: list[str]  # relative to the install root, as the snapshot records them
    excluded: list[str]
    entries: list[snapshot.Entry]
    notes: list[str]


class ProfileDeleteReport(_Out):
    """`wowlab profile delete --json`: the snapshot stays, relabelled."""

    name: str
    relabelled: list[SnapshotSummary]  # usually one; every snapshot labelled as this profile


class ProfileApplyReport(_Out):
    """`wowlab profile apply --json`: the plan, and with `--yes` what was done."""

    profile: str
    snapshot_id: str
    flavor_path: str
    snapshot_version: str | None  # the flavor version the profile was saved on
    flavor_version: str | None  # the flavor's version now
    plan: list[guard.PlanItem]  # writes, then deletes (after None) of files added since
    added: list[str]  # the deletes: files added under the profile's subtrees since the save
    skipped: list[profiles.SkippedPath]  # file-map Edit `no`: left alone
    left: list[profiles.LeftPath]  # added since, but not deletable: left alone
    cache_files: list[profiles.CacheFile]  # every *-cache* file in the plan, with the note
    dry_run: bool
    applied: bool
    transaction: str | None  # the journal record of the applied profile
    notes: list[str]
    # With --into-flavor: where the profile was saved, and the account
    # folders mapped. Left out of the JSON otherwise.
    into: IntoReport | None = Field(default=None, exclude_if=lambda v: v is None)
    # The text plan, line by line, as the terminal shows it before the
    # prompt, with keys, macros and CVars by name (M16-10).
    plan_lines: list[str] = Field(default_factory=list)


def _profile_summary(p: profiles.Profile) -> ProfileSummary:
    m = p.manifest
    return ProfileSummary(
        name=p.name,
        snapshot_id=m.id,
        created_at=m.created_at,
        presets=list(p.presets),
        install_root=m.install_root,
        flavor_folder=m.flavor_folder,
        flavor_version=m.flavor_version,
        client_running=m.client_running,
        subtrees=len(m.subtrees),
        files=sum(1 for e in m.entries if e.kind == "file"),
        bytes=sum(e.size for e in m.entries if e.kind == "file"),
    )


def _profile_report(p: profiles.Profile) -> ProfileReport:
    m = p.manifest
    scope = profiles.PRESET_SCOPE_NOTE if p.presets else profiles.SUBTREE_SCOPE_NOTE
    notes = [scope, profiles.SERVER_SIDE_NOTE, *profiles.linked_root_notes(m)]
    if m.client_running is not False:
        notes.append(
            "wowlab could not confirm the client was closed when this was saved: "
            "SavedVariables in it are as of the client's last logout or /reload."
        )
    return ProfileReport(
        profile=_profile_summary(p),
        subtrees=list(m.subtrees),
        excluded=list(m.excluded),
        entries=list(m.entries),
        notes=notes,
    )


def _print_profile(report: ProfileReport, *, saved: bool) -> None:
    s = report.profile
    _say(f"{'Saved profile' if saved else 'Profile'} {s.name} (snapshot {s.snapshot_id})")
    _say(f"  taken:    {s.created_at}")
    _say(f"  install:  {s.install_root}")
    _say(f"  flavor:   {s.flavor_folder} ({s.flavor_version or 'no version'})")
    if s.presets:
        _say(f"  presets:  {', '.join(s.presets)}")
    _say(f"  {s.subtrees} subtree(s), {s.files} file(s), {_bytes(s.bytes)}")
    for e in report.entries:
        if e.kind == "symlink":
            _say(f"  {e.path} -> {_stored_target(e.target)} (link, recorded, not followed)")
        else:
            _say(f"  {e.path}  {_bytes(e.size)}")
    for n in report.notes:
        _say(n)


PresetOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--preset",
        help="A preset: "
        + "; ".join(f"{name}: {p.summary}" for name, p in profiles.presets().items())
        + ". Repeat for more.",
    ),
]
SubtreeOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--subtree",
        help="A file or folder relative to the flavor folder, under WTF/, Interface/ or "
        "Fonts/ and narrower than WTF/Account/ (for a whole area use snap create and snap "
        "restore); files added anywhere under it since the save, including in character "
        "folders created since, are deleted by an apply. "
        "Repeat for more.",
    ),
]
NameArg = Annotated[str, typer.Argument(metavar="NAME", help="The profile's name.")]


@profile_app.command("save", short_help="Save a profile of your UI settings files.")
@_handled
def profile_save(
    name: NameArg,
    preset: PresetOpt = None,
    subtree: SubtreeOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Save a profile: a snapshot of the preset's (or the subtrees') files, labelled
    with NAME (reads the install only).

    A preset covers every account and every character folder that exists now,
    not only the character you play, and applying the profile later deletes
    files created in those places since the save. An explicit --subtree is a
    whole file or folder: files added anywhere under it since are deleted by
    an apply. The lab-addon (Interface/AddOns/WowLab/ and WowLab.lua) is never
    in a profile. A saved subtree that is itself a link (a symlinked
    Interface/AddOns, say) is held as the link, not what is behind it, and the
    output says so. Action-bar contents and talents are not in these files (the
    server keeps them; not yet verified on this client), so no profile saves or
    restores them.

    \f
    JSON: ProfileReport."""
    if bool(preset) == bool(subtree):
        raise CliError("give --preset or --subtree (not both)", EXIT_USAGE)
    try:
        profiles.check_name(name)
    except profiles.ProfileError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    inst, chosen, lay = _open(root, flavor)
    try:
        selection = profiles.select(lay, preset_names=preset or (), subtrees=subtree or ())
    except profiles.ProfileError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    running = _client_running(inst, chosen)
    saved = profiles.save(
        snapshot.SnapshotStore(),
        Path(inst.root),
        chosen,
        name,
        selection,
        preset_names=preset or (),
        client_running=running,
    )
    if running is not False:
        _note(
            "wowlab could not confirm the client was closed: SavedVariables on disk are as of "
            "its last logout or /reload, and the client overwrites them at its next logout, "
            "/reload or exit."
        )
    report = _profile_report(saved)
    if json_out:
        _emit(report)
    else:
        _print_profile(report, saved=True)


@profile_app.command("list", short_help="Every profile, oldest first.")
@_handled
def profile_list(json_out: JsonOpt = False) -> None:
    """Every profile, oldest first; names each damaged manifest and then exits 1.

    \f
    JSON: ProfileListReport."""
    found = profiles.listing(snapshot.SnapshotStore())
    report = ProfileListReport(
        profiles=[_profile_summary(p) for p in found.profiles], damaged=list(found.invalid)
    )
    if json_out:
        _emit(report)
    else:
        if not report.profiles:
            _say("No profiles.")
        for s in report.profiles:
            presets = f"  presets: {', '.join(s.presets)}" if s.presets else ""
            version = s.flavor_version or "no version"
            _say(
                f"{s.name}  {s.snapshot_id}  {s.flavor_folder}  {version}  {s.files} files{presets}"
            )
    for bad in report.damaged:
        _note("damaged manifest {}: {}", bad.name, bad.reason)
    if report.damaged:
        raise typer.Exit(EXIT_ERROR)


@profile_app.command("show", short_help="One profile and every file in it.")
@_handled
def profile_show(name: NameArg, json_out: JsonOpt = False) -> None:
    """One profile and every file in it, with the notes `save` gave (a saved
    subtree that was a link is held as the link).

    \f
    JSON: ProfileReport."""
    report = _profile_report(profiles.find(snapshot.SnapshotStore(), name))
    if json_out:
        _emit(report)
    else:
        _print_profile(report, saved=False)


@profile_app.command("delete", short_help="Delete a profile (its snapshot stays).")
@_handled
def profile_delete(name: NameArg, json_out: JsonOpt = False) -> None:
    """Delete a profile. Its snapshot stays in the store (snapshots are immutable),
    relabelled `deleted-profile:NAME`. Changes nothing in the install.

    \f
    JSON: ProfileDeleteReport."""
    relabelled = profiles.delete(snapshot.SnapshotStore(), name)
    report = ProfileDeleteReport(name=name, relabelled=[_summary(m) for m in relabelled])
    if json_out:
        _emit(report)
        return
    for m in relabelled:
        _say(f"Deleted profile {name}; snapshot {m.id} stays in the store, labelled {m.label!r}.")


def _apply_plan_lines(
    store: snapshot.SnapshotStore, flavor_path: Path, plan: profiles.ApplyPlan
) -> list[str]:
    # Into another flavor folder, a file the profile does not hold was not
    # "added since": it is the target folder's own.
    extra = (
        "added since the profile was saved"
        if plan.source_flavor_path is None
        else "in the target folder, not in the profile"
    )
    lines: list[str] = []
    detailed = False
    for item in plan.plan:
        line = _plan_line(item)
        if item.after is None:
            line += f"  ({extra})"
        lines.append(line)
        detail = _plan_item_lines(store, flavor_path, item)
        lines.extend(detail)
        detailed = detailed or (bool(detail) and wtfdiff.kind_of(item.path) is not None)
    if detailed:
        # The login caveat is the command's own login note, shown after the plan.
        lines.append(_SCOPE_LINE)
    if plan.skipped:
        lines.append(
            f'  Skipped {len(plan.skipped)} file(s) wowlab leaves alone (file-map Edit "no": '
            "client-written backups, wowlab's leftover temp files, Blizzard_* folders, "
            "file-browser metadata, addon executables):"
        )
        for sk in plan.skipped:
            undone = f"  ({extra}; not deleted)"
            lines.append(
                f"    {sk.path}  [{sk.entry_id}]" + (undone if sk.action == "delete" else "")
            )
    if plan.left:
        lines.append(f"  Left alone ({len(plan.left)} path(s)):")
        lines.extend(f"    {lp.path}  ({lp.reason})" for lp in plan.left)
    if plan.cache_files:
        lines.append("  *-cache files in this change:")
        lines.extend(f"    {c.path}: {c.note}" for c in plan.cache_files)
    lines.extend(plan.notes[1:])  # the scope note (first) is printed under the header
    return lines


@profile_app.command(
    "apply", short_help="Put a profile's files back (asks first; undo reverses it)."
)
@_handled
def profile_apply(
    name: NameArg,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the plan and change nothing.")
    ] = False,
    yes: YesOpt = False,
    root: RootOpt = None,
    into_flavor: Annotated[
        str | None,
        typer.Option(
            "--into-flavor",
            metavar="FOLDER",
            help="Apply to this flavor folder of the install instead of the one the profile "
            "was saved from. When a beta launches, the game's folder gets a new name (you do "
            "not rename it): name the new folder here.",
        ),
    ] = None,
    map_account: MapAccountOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Return the profile's files to their saved bytes, through the write gate: the
    client must be closed, a pre-write snapshot is taken first, and `wowlab undo`
    reverses it.

    A profile covers every account and every character folder that existed when
    it was saved, not only the character you play. Its files are written back,
    and files created in those places since the save are deleted: for example
    macros or character-specific key bindings made since on any of those
    characters, and with the addons preset, addons installed since (code and
    settings). Addon updates made since are undone. `--dry-run` shows the plan
    without asking. Files whose file-map Edit is `no` (client-written backups,
    Blizzard_* folders, file-browser metadata, addon executables) are left
    alone. Every *-cache* file is listed: the server may replace it at your
    next login, so the result is proven only by logging in.

    The lab-addon (Interface/AddOns/WowLab/ and WowLab.lua) is always left
    alone, and so is anything the write gate will not write or delete (an
    executable); both are listed. Action-bar contents and talents are not in
    these files (the server keeps them; not yet verified on this client), so no
    profile saves or restores them.

    When a beta launches, the game's folder gets a new name: apply the
    profile to the new folder with --into-flavor.

    \f
    JSON: ProfileApplyReport."""
    if map_account and into_flavor is None:
        raise CliError("--map-account is used with --into-flavor")
    if into_flavor is not None:
        _one_folder_name(into_flavor, "--into-flavor")
    accounts_given = _account_pairs(map_account)
    store = snapshot.SnapshotStore()
    profile = profiles.find(store, name)
    manifest = profile.manifest
    inst, _ = _discover(root)
    wanted_folder = into_flavor if into_flavor is not None else manifest.flavor_folder
    match = [f for f in inst.flavors if f.folder == wanted_folder]
    if into_flavor is not None and not match:
        raise CliError(
            f"{inst.root} has no flavor folder {into_flavor!r} (flavors: {_flavor_list(inst)})"
        )
    if manifest.flavor_folder is None or not match:
        raise CliError(
            f"profile {name} is of flavor folder {manifest.flavor_folder!r}, which "
            f"{inst.root} does not have (flavors: {_flavor_list(inst)}). Flavor folders are "
            "renamed between beta and launch; to apply the profile to the new one, name it "
            "with --into-flavor FOLDER."
        )
    chosen = match[0]
    target = None if into_flavor is None else _into_target(chosen, accounts_given)
    plan = profiles.plan_apply(profile, chosen, Path(inst.root), store=store, into=target)
    into_report = None if target is None else _into_report(manifest, plan.accounts)
    prompting = json_out and not yes and not dry_run
    show = _say if not json_out else _say_err
    login_note = profiles.login_note(i.path for i in plan.plan)

    def report(*, applied: bool, transaction: str | None = None) -> ProfileApplyReport:
        notes = [profiles.SERVER_SIDE_NOTE, *plan.notes]
        if plan.plan:
            notes.append(login_note)
        return ProfileApplyReport(
            profile=name,
            snapshot_id=manifest.id,
            flavor_path=str(chosen.path),
            snapshot_version=manifest.flavor_version,
            flavor_version=chosen.version,
            plan=list(plan.plan),
            added=list(plan.added),
            skipped=list(plan.skipped),
            left=list(plan.left),
            cache_files=list(plan.cache_files),
            dry_run=dry_run,
            applied=applied,
            transaction=transaction,
            notes=notes,
            into=into_report,
            plan_lines=[_safe(line) for line in plan_lines],
        )

    plan_lines: list[str] = []
    if plan.plan or plan.skipped or plan.left:
        plan_lines.append(
            f"Apply profile {name} (snapshot {manifest.id}) to {chosen.path}: "
            f"{len(plan.plan)} change(s)"
        )
        if into_report is not None:
            plan_lines.extend(_into_lines(into_report, chosen))
        plan_lines.append(f"  {plan.notes[0]}")
        if manifest.flavor_version != chosen.version:
            plan_lines.append(
                f"  Saved on version {_version(manifest.flavor_version)}; {chosen.folder} is "
                f"now on {_version(chosen.version)}."
            )
        plan_lines.extend(_apply_plan_lines(store, chosen.path, plan))
        if plan.plan:
            plan_lines.append(login_note)
    if not json_out or (prompting and plan.plan):
        for line in plan_lines:
            show(line)
    if not plan.plan:
        if json_out:
            _emit(report(applied=False))
        elif plan.skipped or plan.left:
            _say("Nothing to apply but the files left alone; nothing was changed.")
        else:
            _say(f"Nothing to apply: the files of profile {name} already match it.")
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
    record = profiles.apply(plan, chosen, store=store)
    done = report(applied=True, transaction=record)
    if json_out:
        _emit(done)
        return
    _say(
        f"Applied profile {name}: {len(plan.plan)} change(s). `wowlab undo` puts back what "
        "was there before."
    )
    if profiles.MACROS_NOTE in plan.notes:
        _say(profiles.MACROS_NOTE)
    # The login note was shown with the plan, before the prompt; it is not repeated here.
