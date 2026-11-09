"""addoncheck: what each installed addon's TOC says, against this client
and the character folders, and the SavedVariables no TOC declares (M16-34,
docs/LAB_PLAN.md §18.1 item 8; first slices of addon-audit and sv-health).

Read-only (L1) and offline: the addon folders and their TOCs come from
`layout`, each character's `AddOns.txt` from `wtfconfig.read_addons`, and
the client's interface version from the flavor's discovered version by the
patch-number rule (`addoninstall.interface_version`, GLOSSARY "Interface
version"); nothing about a flavor is known here (L6).

`check_addons` reports, for every folder under `Interface/AddOns/`:

- each TOC the client may read (those named after the folder; with a
  suffixed one, which loads depends on the game type, so each is checked);
- its `## Interface:` against the client's: `matches` when the list holds
  it, `differs` when it does not, `none` without the directive, `unknown`
  when the client's interface cannot be derived;
- each `## RequiredDeps:` and `## Dependencies:` item: `installed` when an
  addon folder of that name is present (ASCII case folded) and has a TOC
  named after it, `installed_no_toc` when the folder has none (the client
  does not load it), `blizzard` for a `Blizzard_*` name (taken to be one of
  the client's own addons, which load from game data, not from
  `Interface/AddOns/` **[verify]**, so their absence there says nothing),
  `missing` otherwise;
- with several TOCs the client loads one, by game type: the TOCs whose
  interface matches the client's are the ones counted (their dependencies
  alone make `missing_dependencies`); when none matches, all are counted and
  the addon is `no_toc_for_client` rather than `out_of_date`, which is for
  an addon whose one TOC differs (domain review of M16-34, round 1);
- `## LoadOnDemand:` (`1` is on), `## Version:`;
- the character folders whose `AddOns.txt` lists it, with the state word.
  An addon a file does not list is at the client's default, so a missing
  name is not "disabled" (LAB_FORMATS, amendment of 2026-09-22).

`orphan_saved_variables` picks, from SavedVariables files, those that no
installed TOC declares: the file's addon has no folder, or none of that
folder's TOCs declares `## SavedVariables:` (an account file) or
`## SavedVariablesPerCharacter:` (a character file). A `.lua.bak` follows its
addon. Blizzard's files (`SavedVariables.lua`, `Blizzard_*.lua`) are never
orphans, and neither are the lab-addon's own records (`WowLab.lua`), which
wowlab reads whether or not the lab-addon is installed: they are counted
apart. A folder whose TOCs could not all be read is not judged, and the
saved data of a folder with no TOC named after it (often an addon unzipped
one folder too deep: installed, and wanted) is left out and named apart.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import layout, snapshot, wtfconfig
from wowlab_core.addoninstall import AddonVersionError, interface_version
from wowlab_core.labaddon import ADDON_NAME as LAB_ADDON_NAME
from wowlab_core.layout import FsText
from wowlab_core.toc import TocDocument

__all__ = [
    "AddonCheck",
    "AddonsCheck",
    "AddonsTxt",
    "CharacterState",
    "Dependency",
    "Orphans",
    "TocCheck",
    "check_addons",
    "client_interface",
    "orphan_saved_variables",
]

_BLIZZARD_PREFIX = "blizzard_"
_ADDONS_TXT = "addons.txt"
_DEPENDENCY_KEYS = ("RequiredDeps", "Dependencies")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Dependency(_Model):
    name: FsText
    # `installed_no_toc`: the folder is there, but holds no TOC named after
    # it, so the client does not load it.
    status: Literal["installed", "installed_no_toc", "missing", "blizzard"]


class TocCheck(_Model):
    file: FsText  # the TOC's file name in the addon folder
    error: str | None  # why it could not be read; the fields below are then empty
    title: FsText | None  # `## Title:` as written, colour escapes included
    interface: FsText | None  # `## Interface:` as written
    interface_versions: tuple[int, ...]
    interface_status: Literal["matches", "differs", "none", "unknown", "unread"]
    dependencies: tuple[Dependency, ...]  # `## RequiredDeps:` then `## Dependencies:`
    load_on_demand: bool  # `## LoadOnDemand: 1`
    version: FsText | None  # `## Version:` as written


class CharacterState(_Model):
    """One character folder's `AddOns.txt` line for an addon."""

    account: FsText
    realm_folder: FsText
    character_folder: FsText
    state: FsText  # as written: `enabled` observed, `disabled` not yet seen
    enabled: bool | None  # None for a word other than `enabled` or `disabled`


class AddonCheck(_Model):
    name: FsText  # the folder name
    path: FsText  # relative to the flavor folder
    blizzard: bool  # named like one of Blizzard's own addons
    selection: Literal["single", "depends_on_game_type", "no_toc"]
    tocs: tuple[TocCheck, ...]  # the TOCs the client may read, by file name
    # Those of `tocs` the client is taken to load: the one TOC; with several,
    # those whose interface matches the client's, or all of them when none
    # does (or when the client's interface is not known).
    counted_tocs: tuple[FsText, ...]
    # `out_of_date`: one TOC, whose interface is not the client's;
    # `no_toc_for_client`: several TOCs, none for the client's interface.
    out_of_date: bool
    no_toc_for_client: bool
    missing_dependencies: tuple[FsText, ...]  # over `counted_tocs`, in order, once each
    characters: tuple[CharacterState, ...]  # the AddOns.txt files that list it


class AddonsTxt(_Model):
    """One character folder's `AddOns.txt`."""

    path: FsText  # relative to the flavor folder
    account: FsText
    realm_folder: FsText
    character_folder: FsText
    listed: int  # `<addon>: <state>` lines
    unknown_lines: int  # lines of no known shape, kept and not read
    error: str | None  # why it could not be read


class AddonsCheck(_Model):
    client_version: str | None  # the flavor's version, from `.build.info`
    client_interface: int | None  # by the patch-number rule; None when it cannot be derived
    addons: tuple[AddonCheck, ...]
    addons_txt: tuple[AddonsTxt, ...]


class Orphans(_Model):
    files: tuple[layout.SavedVariablesFile, ...]  # declared by no installed TOC
    lab_addon_records: tuple[layout.SavedVariablesFile, ...]  # left out (see the docstring)
    not_judged: tuple[FsText, ...]  # addon folders whose TOCs could not all be read
    # Addon folders that hold saved data but no TOC named after the folder
    # (often an addon unzipped one folder too deep): their files are left out.
    no_toc: tuple[FsText, ...] = ()


def client_interface(version: str | None) -> int | None:
    """The client's interface version for a flavor version, or None."""
    try:
        return interface_version(version)
    except AddonVersionError:
        return None


def _fold(name: str) -> str:
    return wtfconfig.fold_name(name)


def _client_tocs(addon: layout.Addon) -> tuple[layout.AddonToc, ...]:
    return tuple(t for t in addon.tocs if t.matches_folder)


def _check_toc(
    toc: layout.AddonToc, installed: dict[str, layout.Addon], interface: int | None
) -> TocCheck:
    doc = toc.document
    if doc is None:
        return TocCheck(
            file=toc.file,
            error=toc.error or "not read",
            title=None,
            interface=None,
            interface_versions=(),
            interface_status="unread",
            dependencies=(),
            load_on_demand=False,
            version=None,
        )
    declared = doc.interface
    status: Literal["matches", "differs", "none", "unknown", "unread"]
    if declared is None:
        status = "none"
    elif interface is None:
        status = "unknown"
    else:
        status = "matches" if interface in declared.versions else "differs"
    deps: list[Dependency] = []
    for key in _DEPENDENCY_KEYS:
        for name in doc.list_value(key):
            folded = _fold(name)
            found = installed.get(folded)
            dep_status: Literal["installed", "installed_no_toc", "missing", "blizzard"]
            if folded.startswith(_BLIZZARD_PREFIX):
                dep_status = "blizzard"
            elif found is None:
                dep_status = "missing"
            elif found.selection == "no_toc":
                dep_status = "installed_no_toc"
            else:
                dep_status = "installed"
            deps.append(Dependency(name=name, status=dep_status))
    lod = doc.get("LoadOnDemand")
    return TocCheck(
        file=toc.file,
        error=None,
        title=doc.get("Title"),
        interface=None if declared is None else declared.text,
        interface_versions=() if declared is None else declared.versions,
        interface_status=status,
        dependencies=tuple(deps),
        load_on_demand=lod is not None and lod.strip() == "1",
        version=doc.get("Version"),
    )


def _read_addons_txt(
    lay: layout.Layout, files: Iterable[layout.WtfFile]
) -> list[tuple[AddonsTxt, wtfconfig.AddonsDocument | None]]:
    out: list[tuple[AddonsTxt, wtfconfig.AddonsDocument | None]] = []
    for f in files:
        if f.scope != "character" or f.name.casefold() != _ADDONS_TXT:
            continue
        if f.account is None or f.realm_folder is None or f.character_folder is None:
            continue
        doc: wtfconfig.AddonsDocument | None = None
        error: str | None = None
        try:
            doc = wtfconfig.read_addons(lay.flavor_path.joinpath(*f.path.split("/")))
        except snapshot.UnsafeReadError as exc:
            error = _unsafe_words(exc)
        except OSError as exc:
            error = exc.strerror or type(exc).__name__
        out.append(
            (
                AddonsTxt(
                    path=f.path,
                    account=f.account,
                    realm_folder=f.realm_folder,
                    character_folder=f.character_folder,
                    listed=0 if doc is None else len(doc.states),
                    unknown_lines=0 if doc is None else len(doc.unknown_lines()),
                    error=error,
                ),
                doc,
            )
        )
    return out


def _unsafe_words(exc: snapshot.UnsafeReadError) -> str:
    """An `UnsafeReadError` without the absolute path it starts with."""
    text = str(exc)
    # The messages are "<path> is not a regular file" and "<path> holds <n>
    # bytes, more than <limit>". The path comes first and may hold the same
    # words, so each is matched from the end of the message.
    if text.endswith(" is not a regular file"):
        return "is not a regular file"
    at = text.rfind(" holds ")
    if at != -1 and re.fullmatch(r"holds \d+ bytes, more than \d+", text[at + 1 :]):
        return text[at + 1 :]
    return "not read"


def check_addons(lay: layout.Layout, version: str | None) -> AddonsCheck:
    """Every addon folder of `lay`, checked (see the module docstring).
    `version` is the flavor's discovered version."""
    interface = client_interface(version)
    addons = lay.addons()
    installed = {_fold(a.name): a for a in addons}
    walk = lay.wtf_walk()
    lists = _read_addons_txt(lay, walk.wtf_files)
    checks: list[AddonCheck] = []
    for addon in addons:
        tocs = tuple(_check_toc(t, installed, interface) for t in _client_tocs(addon))
        matching = [t for t in tocs if t.interface_status == "matches"]
        several = len(tocs) > 1
        counted = matching if several and matching else list(tocs)
        missing = tuple(
            dict.fromkeys(d.name for t in counted for d in t.dependencies if d.status == "missing")
        )
        characters: list[CharacterState] = []
        for meta, doc in lists:
            found = None if doc is None else doc.state_of(addon.name)
            if found is not None:
                characters.append(
                    CharacterState(
                        account=meta.account,
                        realm_folder=meta.realm_folder,
                        character_folder=meta.character_folder,
                        state=found.state,
                        enabled=found.enabled,
                    )
                )
        checks.append(
            AddonCheck(
                name=addon.name,
                path=addon.path,
                blizzard=addon.blizzard,
                selection=addon.selection,
                tocs=tocs,
                counted_tocs=tuple(t.file for t in counted),
                out_of_date=not several and any(t.interface_status == "differs" for t in tocs),
                no_toc_for_client=several
                and not matching
                and any(t.interface_status == "differs" for t in tocs),
                missing_dependencies=missing,
                characters=tuple(characters),
            )
        )
    return AddonsCheck(
        client_version=version,
        client_interface=interface,
        addons=tuple(checks),
        addons_txt=tuple(meta for meta, _ in lists),
    )


def _declares(docs: Sequence[TocDocument], scope: Literal["account", "character"]) -> bool:
    for doc in docs:
        names = doc.saved_variables if scope == "account" else doc.saved_variables_per_character
        if names:
            return True
    return False


def orphan_saved_variables(
    files: Iterable[layout.SavedVariablesFile], addons: Iterable[layout.Addon]
) -> Orphans:
    """The files among `files` that no TOC of `addons` declares (see the
    module docstring)."""
    by_name: dict[str, list[layout.Addon]] = {}
    for addon in addons:
        by_name.setdefault(_fold(addon.name), []).append(addon)
    orphans: list[layout.SavedVariablesFile] = []
    lab: list[layout.SavedVariablesFile] = []
    not_judged: dict[str, None] = {}
    no_toc: dict[str, None] = {}
    for f in files:
        if f.blizzard or f.addon is None or _fold(f.addon).startswith(_BLIZZARD_PREFIX):
            continue
        folders = by_name.get(_fold(f.addon), [])
        tocs = [t for a in folders for t in _client_tocs(a)]
        if folders and not tocs:
            for a in folders:
                no_toc[a.name] = None
            continue
        if any(t.document is None for t in tocs):
            for a in folders:
                not_judged[a.name] = None
            continue
        if _declares([t.document for t in tocs if t.document is not None], f.scope):
            continue
        if _fold(f.addon) == _fold(LAB_ADDON_NAME):
            lab.append(f)
        else:
            orphans.append(f)
    return Orphans(
        files=tuple(orphans),
        lab_addon_records=tuple(lab),
        not_judged=tuple(not_judged),
        no_toc=tuple(no_toc),
    )
