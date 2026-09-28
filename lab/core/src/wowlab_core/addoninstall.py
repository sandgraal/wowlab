"""addoninstall: put the lab-addon into a flavor and take it out again (§13.1, M11-02).

`wowlab addon install lab` copies the repository's `lab/addon/WowLab/` into
`<flavor>/Interface/AddOns/WowLab/` in one `guard` transaction (L2,
ADR-0021, ADR-0026): the client must be closed, a pre-write snapshot is
taken first, the change is journaled and `wowlab undo` reverses it. The
TOC's `## Interface: @WOWLAB_INTERFACE@` line is filled with the interface
version derived from the flavor's discovered version by the patch-number
rule (GLOSSARY, "Interface version"): `major * 10000 + minor * 100 + patch`.
Nothing about a flavor is known here (L6): the version comes from
`.build.info` through `install`.

What is copied: `WowLab.toc` and every `*.lua` file directly in the source
folder, nothing else (the README, the linter's configuration and its client
definitions stay in the repository). The TOC must list exactly those `.lua`
files, so what is copied is what the client loads.

Re-installing over a copy plans only the files whose bytes differ, and
deletes files under `Interface/AddOns/WowLab/` that are not part of the
sources (an older version's leftovers). `wowlab addon remove lab` deletes
every file under that folder through the gate. The folder itself stays,
empty: the gate deletes files, not folders. Neither ever touches the
addon's SavedVariables (`WowLab.lua` under `WTF/`, which holds `WowLabDB`
and each character's `WowLabCharDB`): those are the captures. Profiles
(§13.3) never touch the folder or `WowLab.lua` either.

Reads of the install are listings only, made without following any link,
junction or reparse point (L1); every byte written or deleted goes through
`guard`, which checks every path again.

The sources are found from this package: the nearest ancestor folder of it
holding `lab/addon/WowLab/WowLab.toc`, which is the repository when wowlab
runs from a checkout. Anywhere else `AddonSourceError` says so.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from wowlab_core import guard
from wowlab_core.toc import parse_toc

__all__ = [
    "ADDON_FOLDER",
    "ADDON_NAME",
    "FOLDER_STAYS_NOTE",
    "INTERFACE_LINE",
    "INTERFACE_PLACEHOLDER",
    "LAB_ADDON",
    "LOAD_NOTE",
    "MAX_ENTRIES",
    "MAX_SOURCE_BYTES",
    "SAVED_VARIABLES_NOTE",
    "SOURCE_PARTS",
    "TOC_NAME",
    "AddonChangedError",
    "AddonError",
    "AddonSourceError",
    "AddonVersionError",
    "InstallPlan",
    "LeftPath",
    "RemovePlan",
    "addon_files",
    "fill_toc",
    "find_source",
    "install",
    "interface_version",
    "plan_install",
    "plan_remove",
    "remove",
]

LAB_ADDON = "lab"
"""The name `wowlab addon install|remove` takes for the lab-addon."""
ADDON_NAME = "WowLab"
ADDON_FOLDER = f"Interface/AddOns/{ADDON_NAME}"
"""Where the addon goes, relative to the flavor folder."""
TOC_NAME = f"{ADDON_NAME}.toc"
SOURCE_PARTS = ("lab", "addon", ADDON_NAME)
"""The source folder, relative to the repository root."""
INTERFACE_PLACEHOLDER = "@WOWLAB_INTERFACE@"
INTERFACE_LINE = f"## Interface: {INTERFACE_PLACEHOLDER}"
"""The one TOC line install fills (ADR-0026: the repository holds no number)."""
MAX_SOURCE_BYTES = 1 << 20
"""A source file larger than this is refused (the addon's files are a few KiB)."""
MAX_ENTRIES = 1000
"""More entries than this under the installed folder is refused, not walked."""

SAVED_VARIABLES_NOTE = (
    "The addon's SavedVariables are left alone: WowLab.lua under WTF/ (WowLabDB, and "
    "WowLabCharDB in each character's folder) holds its captures. wowlab never deletes it; "
    "remove it yourself if you no longer want it."
)
FOLDER_STAYS_NOTE = (
    f"The folder {ADDON_FOLDER}/ itself stays, empty: the write gate deletes files, not "
    "folders. With no TOC in it there is no addon for the client to load."
)
LOAD_NOTE = (
    "The client reads Interface/AddOns/ when it starts; check that WowLab is enabled in the "
    "AddOns list at character select."
)


class AddonError(Exception):
    """Base class for every error this module raises (the CLI's exit 1)."""


class AddonSourceError(AddonError):
    """The lab-addon's sources are missing or not what install expects."""


class AddonVersionError(AddonError):
    """The flavor's version gives no interface version by the patch-number rule."""


class AddonChangedError(AddonError):
    """The files changed between the plan and the change; the gate rolled back."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LeftPath(_Frozen):
    """Something under the addon folder that install or remove leaves alone."""

    path: str  # relative to the flavor folder
    reason: str


class InstallPlan(_Frozen):
    """What `install` would change, from a dry run of the gate."""

    flavor_path: str
    flavor_version: str
    interface: int
    source: str  # the source folder read
    files: tuple[str, ...]  # every file the addon consists of, flavor-relative
    plan: tuple[guard.PlanItem, ...]  # writes of new or changed files, then deletes
    unchanged: tuple[str, ...]  # files already holding the bytes install would write
    left: tuple[LeftPath, ...]


class RemovePlan(_Frozen):
    """What `remove` would delete, from a dry run of the gate."""

    flavor_path: str
    plan: tuple[guard.PlanItem, ...]  # deletes only
    left: tuple[LeftPath, ...]
    folder_exists: bool


class _FlavorLike(Protocol):
    @property
    def path(self) -> Path: ...

    @property
    def version(self) -> str | None: ...


# ─── the sources ─────────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent


def find_source(start: Path | None = None) -> Path:
    """The lab-addon's source folder: `lab/addon/WowLab/` in the nearest
    ancestor of `start` (default: this package's folder) that holds one with
    a `WowLab.toc`. Raises `AddonSourceError` when there is none, as when
    wowlab runs from an installed package rather than a checkout."""
    here = (start if start is not None else _HERE).resolve()
    for folder in (here, *here.parents):
        candidate = folder.joinpath(*SOURCE_PARTS)
        if (candidate / TOC_NAME).is_file():
            return candidate
    raise AddonSourceError(
        f"the lab-addon's sources ({'/'.join(SOURCE_PARTS)}/{TOC_NAME}) were not found in any "
        f"folder above {here}; `wowlab addon install lab` runs from a checkout of the "
        "wowlab repository (uv run wowlab ...)"
    )


def _read_source(path: Path) -> bytes:
    try:
        st = path.lstat()
    except OSError as exc:
        raise AddonSourceError(f"cannot read the lab-addon source {path}: {exc}") from exc
    if not stat.S_ISREG(st.st_mode):
        raise AddonSourceError(f"the lab-addon source {path} is not a regular file")
    if st.st_size > MAX_SOURCE_BYTES:
        raise AddonSourceError(
            f"the lab-addon source {path} is {st.st_size} bytes, over {MAX_SOURCE_BYTES}"
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise AddonSourceError(f"cannot read the lab-addon source {path}: {exc}") from exc
    if len(data) > MAX_SOURCE_BYTES:
        raise AddonSourceError(f"the lab-addon source {path} grew past {MAX_SOURCE_BYTES} bytes")
    return data


def interface_version(version: str | None) -> int:
    """The interface version for a flavor version, by the patch-number rule:
    `"1.60.1.69913"` -> `16001`, `"12.1.5.65432"` -> `120105`."""
    if version is None:
        raise AddonVersionError(
            "the flavor has no version in .build.info, so the TOC's ## Interface: cannot be "
            "filled; nothing was changed"
        )
    parts = version.split(".")
    numbers = parts[:3]
    if len(numbers) < 3 or not all(p.isascii() and p.isdecimal() for p in numbers):
        raise AddonVersionError(
            f"the flavor's version {version!r} is not major.minor.patch[.build], so the TOC's "
            "## Interface: cannot be filled; nothing was changed"
        )
    major, minor, patch = (int(p) for p in numbers)
    if minor > 99 or patch > 99:
        raise AddonVersionError(
            f"the flavor's version {version!r} has a minor or patch number over 99, which the "
            "patch-number rule cannot express; nothing was changed"
        )
    return major * 10000 + minor * 100 + patch


def fill_toc(template: bytes, interface: int) -> bytes:
    """The TOC with its one `INTERFACE_LINE` filled with `interface`. Every
    other byte is kept, line endings included."""
    if isinstance(interface, bool) or not isinstance(interface, int) or interface <= 0:
        raise AddonVersionError(f"an interface version is a positive integer, not {interface!r}")
    wanted = INTERFACE_LINE.encode("ascii")
    lines = template.split(b"\n")
    hits = [i for i, line in enumerate(lines) if line.removesuffix(b"\r") == wanted]
    if len(hits) != 1:
        raise AddonSourceError(
            f"{TOC_NAME} must hold the line {INTERFACE_LINE!r} exactly once "
            f"(found {len(hits)} times)"
        )
    i = hits[0]
    lines[i] = lines[i].replace(
        INTERFACE_PLACEHOLDER.encode("ascii"), str(interface).encode("ascii")
    )
    filled = b"\n".join(lines)
    if b"@WOWLAB_" in filled:
        raise AddonSourceError(f"{TOC_NAME} holds another @WOWLAB_...@ placeholder")
    found = parse_toc(filled).interface
    if found is None or found.versions != (interface,) or not found.ok:
        raise AddonSourceError(f"{TOC_NAME} does not read back with ## Interface: {interface}")
    return filled


def addon_files(interface: int, source: Path | None = None) -> dict[str, bytes]:
    """Every file of the addon as install writes it: flavor-relative path to
    bytes, the TOC first (filled with `interface`), then the `.lua` files in
    the order the TOC lists them."""
    folder = find_source() if source is None else source
    try:
        names = sorted(e.name for e in os.scandir(folder))
    except OSError as exc:
        raise AddonSourceError(f"cannot list the lab-addon sources in {folder}: {exc}") from exc
    if TOC_NAME not in names:
        raise AddonSourceError(f"no {TOC_NAME} in {folder}")
    template = _read_source(folder / TOC_NAME)
    toc = parse_toc(template)
    listed = [line.path for line in toc.files]
    lua = sorted(n for n in names if n.endswith(".lua"))
    for path in listed:
        if "/" in path or "\\" in path or not path.endswith(".lua"):
            raise AddonSourceError(
                f"{TOC_NAME} lists {path!r}; install copies only .lua files in its own folder"
            )
    if sorted(listed) != lua or len(set(listed)) != len(listed):
        raise AddonSourceError(
            f"{TOC_NAME} must list every .lua file in {folder} once and nothing else "
            f"(listed: {', '.join(listed) or 'none'}; present: {', '.join(lua) or 'none'})"
        )
    files = {f"{ADDON_FOLDER}/{TOC_NAME}": fill_toc(template, interface)}
    for name in listed:
        files[f"{ADDON_FOLDER}/{name}"] = _read_source(folder / name)
    return files


# ─── what is installed ───────────────────────────────────────────────────────


def _is_link(path: Path, st: os.stat_result) -> bool:
    """A symlink, or on Windows a junction or other directory link."""
    if stat.S_ISLNK(st.st_mode):
        return True
    try:
        return stat.S_ISDIR(st.st_mode) and path.is_junction()
    except OSError:
        return True


def _installed(flavor_dir: Path) -> tuple[list[str], list[LeftPath], bool]:
    """The regular files under the addon folder (flavor-relative, sorted),
    the entries left alone, and whether the folder exists. Nothing is
    followed through a link; a link on the way to the folder is refused."""
    current = flavor_dir
    for part in ADDON_FOLDER.split("/"):
        current = current / part
        try:
            st = current.lstat()
        except FileNotFoundError:
            return [], [], False
        except OSError as exc:
            raise AddonError(f"cannot look at {current}: {exc}") from exc
        rel = current.relative_to(flavor_dir).as_posix()
        if _is_link(current, st):
            raise AddonError(
                f"{rel} is a link (a symlink or junction); wowlab never writes through one. "
                "Nothing was changed; remove the link yourself if you want wowlab to manage "
                "the addon there"
            )
        if not stat.S_ISDIR(st.st_mode):
            raise AddonError(f"{rel} is not a folder; nothing was changed")
    files: list[str] = []
    left: list[LeftPath] = []
    seen = 0
    pending = [current]
    while pending:
        folder = pending.pop()
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError as exc:
            raise AddonError(f"cannot list {folder}: {exc}") from exc
        for entry in entries:
            seen += 1
            if seen > MAX_ENTRIES:
                raise AddonError(
                    f"{ADDON_FOLDER} holds more than {MAX_ENTRIES} entries; nothing was changed"
                )
            path = Path(entry.path)
            rel = path.relative_to(flavor_dir).as_posix()
            try:
                st = path.lstat()
            except OSError as exc:
                left.append(LeftPath(path=rel, reason=f"cannot be examined: {exc}"))
                continue
            if _is_link(path, st):
                left.append(
                    LeftPath(path=rel, reason="a link (symlink or junction); never followed")
                )
            elif stat.S_ISDIR(st.st_mode):
                pending.append(path)
            elif stat.S_ISREG(st.st_mode):
                files.append(rel)
            else:
                left.append(
                    LeftPath(path=rel, reason="not a regular file; wowlab never removes it")
                )
    return sorted(files), left, True


# ─── install ─────────────────────────────────────────────────────────────────


def _install_label(interface: int) -> str:
    return f"addon install {LAB_ADDON} (## Interface: {interface})"


class _Tx(Protocol):
    @property
    def plan(self) -> tuple[guard.PlanItem, ...]: ...

    def delete(self, rel_path: str) -> None: ...


def _deletes(tx: _Tx, paths: list[str], left: list[LeftPath]) -> None:
    for rel in paths:
        try:
            tx.delete(rel)
        except guard.PathNotAllowedError as exc:
            left.append(LeftPath(path=rel, reason=str(exc)))


def plan_install(
    flavor: _FlavorLike, *, store: Path | None = None, source: Path | None = None
) -> InstallPlan:
    """What `install` would change, from a dry run of the gate (the same
    checks under the same locks; nothing is written in the install).

    Raises `guard.GuardError` when the gate refuses (the client running or
    unknown, a path it will not write), `AddonVersionError` when the flavor's
    version gives no interface version, and `AddonSourceError` when the
    sources are missing or inconsistent."""
    interface = interface_version(flavor.version)
    version = str(flavor.version)  # not None: interface_version refused that
    folder = find_source() if source is None else source
    files = addon_files(interface, folder)
    present, left, _ = _installed(flavor.path)
    extras = [p for p in present if p not in files]
    with guard.transaction(
        flavor, label=_install_label(interface), store=store, dry_run=True
    ) as tx:
        for rel, data in files.items():
            tx.write(rel, data)
        _deletes(tx, extras, left)
        whole = tx.plan
    return InstallPlan(
        flavor_path=str(flavor.path),
        flavor_version=version,
        interface=interface,
        source=str(folder),
        files=tuple(files),
        plan=tuple(i for i in whole if i.before != i.after),
        unchanged=tuple(i.path for i in whole if i.before == i.after),
        left=tuple(left),
    )


def _checked(tx: _Tx, plan: tuple[guard.PlanItem, ...]) -> None:
    if tx.plan != plan:
        # Raising inside the transaction makes the gate roll it back.
        raise AddonChangedError(
            "the files changed after the plan was made, so the change was rolled back; run "
            "the command again to see the new plan"
        )


def install(plan: InstallPlan, flavor: _FlavorLike, *, store: Path | None = None) -> str:
    """Make exactly the changes `plan` lists, in one gate transaction, and
    return its journal record id (`wowlab undo` reverses it). If the gate's
    own plan differs from `plan`, `AddonChangedError` is raised inside the
    transaction, so the gate rolls it back."""
    files: Mapping[str, bytes] = addon_files(plan.interface, Path(plan.source))
    with guard.transaction(flavor, label=_install_label(plan.interface), store=store) as tx:
        for item in plan.plan:
            if item.after is None:
                tx.delete(item.path)
            else:
                tx.write(item.path, files[item.path])
        _checked(tx, plan.plan)
    return guard.history(store=store)[-1].id


# ─── remove ──────────────────────────────────────────────────────────────────

_REMOVE_LABEL = f"addon remove {LAB_ADDON}"


def plan_remove(flavor: _FlavorLike, *, store: Path | None = None) -> RemovePlan:
    """What `remove` would delete: every regular file under the addon folder,
    from a dry run of the gate. SavedVariables are never part of it."""
    present, left, exists = _installed(flavor.path)
    with guard.transaction(flavor, label=_REMOVE_LABEL, store=store, dry_run=True) as tx:
        _deletes(tx, present, left)
        whole = tx.plan
    return RemovePlan(
        flavor_path=str(flavor.path), plan=whole, left=tuple(left), folder_exists=exists
    )


def remove(plan: RemovePlan, flavor: _FlavorLike, *, store: Path | None = None) -> str:
    """Delete exactly the files `plan` lists, in one gate transaction, and
    return its journal record id (`wowlab undo` puts them back)."""
    with guard.transaction(flavor, label=_REMOVE_LABEL, store=store) as tx:
        for item in plan.plan:
            tx.delete(item.path)
        _checked(tx, plan.plan)
    return guard.history(store=store)[-1].id
