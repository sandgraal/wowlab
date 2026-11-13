"""`wowlab addon install` and `addon remove` (M16-111)."""

from collections.abc import Callable, Sequence
from typing import Annotated

import typer

from wowlab_core import (
    addoninstall,
    guard,
)
from wowlab_core.cli._cmd_snap import (
    _plan_line,
)
from wowlab_core.cli._common import (
    EXIT_USAGE,
    CliError,
    FlavorOpt,
    JsonOpt,
    RootOpt,
    YesOpt,
    _confirm,
    _discover,
    _emit,
    _handled,
    _Out,
    _say,
    _say_err,
    _select_flavor,
    addon_app,
)

# ─── addon ───────────────────────────────────────────────────────────────────


class AddonInstallReport(_Out):
    """`wowlab addon install lab --json`: the plan, and with `--yes` what was done."""

    addon: str  # the addon folder's name
    flavor_path: str
    flavor_version: str  # the discovered version the interface was derived from
    interface: int  # what the installed TOC's ## Interface: says
    addon_version: str  # what the installed TOC's ## Version: says: this wowlab's (M16-06)
    source: str  # the source folder read
    files: list[str]  # every file of the addon, relative to the flavor folder
    plan: list[guard.PlanItem]  # new or changed files, then deletes of files not in the sources
    unchanged: list[str]  # files that already hold the bytes install writes
    left: list[addoninstall.LeftPath]  # entries under the folder left alone
    dry_run: bool
    applied: bool
    transaction: str | None  # the journal record of the install
    notes: list[str]  # LOAD_NOTE only when applied


class AddonRemoveReport(_Out):
    """`wowlab addon remove lab --json`: the plan, and with `--yes` what was done."""

    addon: str
    flavor_path: str
    plan: list[guard.PlanItem]  # deletes only
    left: list[addoninstall.LeftPath]
    dry_run: bool
    applied: bool
    transaction: str | None  # the journal record of the removal
    notes: list[str]


AddonArg = Annotated[
    str,
    typer.Argument(metavar="NAME", help="The addon: `lab` (the lab-addon) is the only one."),
]
DryRunOpt = Annotated[bool, typer.Option("--dry-run", help="Print the plan and change nothing.")]


def _lab_addon(name: str) -> None:
    if name != addoninstall.LAB_ADDON:
        raise CliError(
            f"no addon {name!r}: `{addoninstall.LAB_ADDON}` (the lab-addon) is the only one "
            "wowlab installs",
            EXIT_USAGE,
        )


def _print_left(show: Callable[[str], None], left: Sequence[addoninstall.LeftPath]) -> None:
    if left:
        show(f"  Left alone ({len(left)} path(s)):")
        for lp in left:
            show(f"    {lp.path}  ({lp.reason})")


@addon_app.command("install", short_help="Copy the lab-addon into the game (asks first).")
@_handled
def addon_install(
    name: AddonArg,
    dry_run: DryRunOpt = False,
    yes: YesOpt = False,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Copy the lab-addon into the flavor's Interface/AddOns/WowLab/, through the
    write gate: the client must be closed, a pre-write snapshot is taken first, and
    `wowlab undo` reverses it.

    The files come from the wowlab you are running: its source checkout when
    it runs from one, else the copy installed with it, and nothing else; the
    plan names the folder it read.

    The TOC's ## Interface: is filled from the flavor's discovered version by the
    patch-number rule; after a client update that changes the first three parts of
    the version, run this again (`wowlab doctor` says when). Its ## Version: is this
    wowlab's version. Only WowLab.toc and the .lua files it lists are
    copied. Over an existing copy only changed files are written, and files not in
    the sources are deleted.

    \f
    JSON: AddonInstallReport."""
    _lab_addon(name)
    inst, _ = _discover(root)
    chosen = _select_flavor(inst, flavor)
    plan = addoninstall.plan_install(chosen)
    prompting = json_out and not yes and not dry_run
    show = _say if not json_out else _say_err
    target = addoninstall.ADDON_FOLDER

    def report(*, applied: bool, transaction: str | None = None) -> AddonInstallReport:
        return AddonInstallReport(
            addon=addoninstall.ADDON_NAME,
            flavor_path=str(chosen.path),
            flavor_version=plan.flavor_version,
            interface=plan.interface,
            addon_version=plan.addon_version,
            source=plan.source,
            files=list(plan.files),
            plan=list(plan.plan),
            unchanged=list(plan.unchanged),
            left=list(plan.left),
            dry_run=dry_run,
            applied=applied,
            transaction=transaction,
            # The load note is for after an install that happened, as in the text.
            notes=[n for n in plan.notes if applied or n != addoninstall.LOAD_NOTE],
        )

    if not json_out or (prompting and plan.plan):
        show(
            f"Install the lab-addon into {chosen.path / target}: {len(plan.plan)} change(s), "
            f"## Interface: {plan.interface}, ## Version: {plan.addon_version}"
        )
        show(f"  from {plan.source}")
        for item in plan.plan:
            line = _plan_line(item)
            if item.after is None:
                line += "  (not part of the lab-addon's sources)"
            show(line)
        if plan.unchanged and plan.plan:
            show(f"  {len(plan.unchanged)} file(s) already up to date.")
        _print_left(show, plan.left)
        # The load note is for after an install that happened.
        for n in plan.notes:
            if n != addoninstall.LOAD_NOTE:
                show(n)
    if not plan.plan:
        if json_out:
            _emit(report(applied=False))
        else:
            _say(
                f"Nothing to install: {target}/ already holds this lab-addon with "
                f"## Interface: {plan.interface}."
            )
        return
    if dry_run:
        if json_out:
            _emit(report(applied=False))
        else:
            _say("Dry run: nothing was changed.")
        return
    try:
        _confirm("Install it?", yes)
    except typer.Exit:
        if json_out:
            _emit(report(applied=False))
        raise
    record = addoninstall.install(plan, chosen)
    done = report(applied=True, transaction=record)
    if json_out:
        _emit(done)
        return
    _say(
        f"Installed the lab-addon: {len(plan.plan)} change(s). `wowlab undo` puts back what "
        "was there before."
    )
    if addoninstall.LOAD_NOTE in plan.notes:
        _say(addoninstall.LOAD_NOTE)


@addon_app.command("remove", short_help="Delete the lab-addon's files from the game (asks first).")
@_handled
def addon_remove(
    name: AddonArg,
    dry_run: DryRunOpt = False,
    yes: YesOpt = False,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Delete the lab-addon's files from the flavor's Interface/AddOns/WowLab/, through
    the write gate: the client must be closed, a pre-write snapshot is taken first,
    and `wowlab undo` puts them back. A file the gate will not delete refuses the
    whole removal (exit 3).

    Its SavedVariables (WowLab.lua and WowLab.lua.bak under WTF/, holding WowLabDB
    and each character's WowLabCharDB) are never touched: they are the captures.
    The folder stays; the gate deletes files, not folders.

    \f
    JSON: AddonRemoveReport."""
    _lab_addon(name)
    inst, _ = _discover(root)
    chosen = _select_flavor(inst, flavor)
    plan = addoninstall.plan_remove(chosen)
    prompting = json_out and not yes and not dry_run
    show = _say if not json_out else _say_err
    target = addoninstall.ADDON_FOLDER

    def report(*, applied: bool, transaction: str | None = None) -> AddonRemoveReport:
        return AddonRemoveReport(
            addon=addoninstall.ADDON_NAME,
            flavor_path=str(chosen.path),
            plan=list(plan.plan),
            left=list(plan.left),
            dry_run=dry_run,
            applied=applied,
            transaction=transaction,
            notes=list(plan.notes),
        )

    if not json_out or (prompting and plan.plan):
        if plan.plan or plan.left:
            show(f"Remove the lab-addon from {chosen.path / target}: {len(plan.plan)} file(s)")
        for item in plan.plan:
            show(_plan_line(item))
        _print_left(show, plan.left)
        for n in plan.notes:
            show(n)
    if not plan.plan:
        if json_out:
            _emit(report(applied=False))
        else:
            _say(f"Nothing to remove: no lab-addon files in {target}/.")
        return
    if dry_run:
        if json_out:
            _emit(report(applied=False))
        else:
            _say("Dry run: nothing was changed.")
        return
    try:
        _confirm("Remove it?", yes)
    except typer.Exit:
        if json_out:
            _emit(report(applied=False))
        raise
    record = addoninstall.remove(plan, chosen)
    done = report(applied=True, transaction=record)
    if json_out:
        _emit(done)
        return
    _say(f"Removed the lab-addon: {len(plan.plan)} file(s) deleted. `wowlab undo` puts them back.")
