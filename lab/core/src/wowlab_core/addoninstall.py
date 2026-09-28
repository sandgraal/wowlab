"""addoninstall: put the lab-addon into a flavor and take it out again (§13.1, M11-02).

`wowlab addon install lab` copies the repository's `lab/addon/WowLab/` into
`<flavor>/Interface/AddOns/WowLab/` in one `guard` transaction (L2,
ADR-0021, ADR-0026): the client must be closed, a pre-write snapshot is
taken first, the change is journaled and `wowlab undo` reverses it. The
TOC's `## Interface: @WOWLAB_INTERFACE@` line is filled with the interface
version derived from the flavor's discovered version by the patch-number
rule (GLOSSARY, "Interface version"): `major * 10000 + minor * 100 + patch`.
The rule gave 16001 for 1.60.1, confirmed in game on 2026-09-22
(`docs/DATA_SOURCES.md`); M11-03 checks it again against the fourth value
`GetBuildInfo()` returns. Nothing about a flavor is known here (L6): the
version comes from `.build.info` through `install`.

What is copied: `WowLab.toc` and every `*.lua` file directly in the source
folder, nothing else (the README, the linter's configuration and its client
definitions stay in the repository). The TOC must list exactly those `.lua`
files, so what is copied is what the client loads.

Re-installing over a copy plans only the files whose bytes differ, and
deletes files under `Interface/AddOns/WowLab/` that are not part of the
sources (an older version's leftovers). `wowlab addon remove lab` deletes
every file under that folder through the gate, and a delete the gate
refuses refuses the whole removal. The folder itself stays: the gate
deletes files, not folders. Neither ever touches the addon's SavedVariables
(`WowLab.lua` under `WTF/`, which holds `WowLabDB` and each character's
`WowLabCharDB`): those are the captures. Profiles (§13.3) never touch the
folder or `WowLab.lua` either.

Reads of the install are listings, and reads of the files install left
alone as up to date, made without following any link, junction or reparse
point (L1); every byte written or deleted goes through `guard`, which checks
every path again. Inside the transaction, with the gate's locks held, the
folder is listed again and the up-to-date files are read again; any
difference from the plan raises `AddonChangedError`, so the gate rolls back.

The sources are found from this package: `lab/addon/WowLab/WowLab.toc` in
the workspace root, the nearest ancestor holding `.git` or a
`pyproject.toml` that declares the uv workspace, and nowhere else. A link in `lab`, `addon` or `WowLab` is
refused, and each source file is opened without following a link and
checked to be the regular file its `lstat` saw. Anywhere else
`AddonSourceError` says so.
"""

from __future__ import annotations

import os
import stat
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from wowlab_core import guard
from wowlab_core.toc import parse_toc

__all__ = [
    "ADDON_FOLDER",
    "ADDON_NAME",
    "FOLDER_LEFT_NOTE",
    "FOLDER_STAYS_NOTE",
    "INTERFACE_LINE",
    "INTERFACE_PLACEHOLDER",
    "LAB_ADDON",
    "LOAD_NOTE",
    "MAX_ENTRIES",
    "MAX_SOURCE_BYTES",
    "MAX_VERSION_DIGITS",
    "PATCH_NOTE",
    "REMOVE_REFUSED_NOTE",
    "SAVED_VARIABLES_NOTE",
    "SOURCE_PARTS",
    "STILL_INSTALLED_NOTE",
    "TOC_LEFT_NOTE",
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
MAX_VERSION_DIGITS = 9
"""A version part longer than this many digits is refused."""
_MAX_PYPROJECT_BYTES = 1 << 20

SAVED_VARIABLES_NOTE = (
    "The addon's SavedVariables are left alone: WowLab.lua (and the client's WowLab.lua.bak) "
    "in WTF/Account/<account>/SavedVariables/ (WowLabDB) and in each character's "
    "SavedVariables/ folder (WowLabCharDB) hold its captures. `wowlab addon install` and "
    "`remove` never touch them; delete them yourself if you no longer want them."
)
FOLDER_STAYS_NOTE = (
    f"The folder {ADDON_FOLDER}/ stays: the write gate deletes files, not folders. With no "
    f"{TOC_NAME} left in it, the client has no addon to load from it, and `wowlab addons list` "
    "shows it as a folder with no TOC. You can delete the folder yourself."
)
FOLDER_LEFT_NOTE = "It still holds the path(s) listed above as left alone."
"""Added to `FOLDER_STAYS_NOTE` when a removal leaves paths alone."""
TOC_LEFT_NOTE = (
    f"The folder {ADDON_FOLDER}/ stays and still holds a .toc listed above as left alone, so "
    "the client may still find an addon there."
)
"""Replaces `FOLDER_STAYS_NOTE` when a removal leaves a `.toc` alone."""
REMOVE_REFUSED_NOTE = (
    "Nothing was deleted: `wowlab addon remove lab` removes the folder's files all together or "
    "not at all, and the path(s) named above are not ones wowlab will delete."
)
"""Follows the gate's reason(s) when `remove` is refused."""
STILL_INSTALLED_NOTE = (
    "The lab-addon is still installed, so the client will load it at its next start; to stop "
    f"it loading, untick {ADDON_NAME} in the AddOns list at character select."
)
"""Follows `REMOVE_REFUSED_NOTE` when the refused removal found a `WowLab.toc`."""
LOAD_NOTE = (
    "Start the client. At character select, open AddOns, choose each character you will play "
    "(or all characters) in the drop-down, and check that WowLab is listed and ticked. If it "
    "is marked out of date now, the ## Interface: value wowlab derived does not match this "
    "client: report it. WowLab.lua appears under WTF/ only after your first logout or /reload."
)
PATCH_NOTE = (
    "The TOC says ## Interface: {interface}, derived from the client version {version} in "
    ".build.info (flavor folder {folder}). When a client update changes the first three "
    "numbers of that version, the AddOns list may mark WowLab out of date: close the client "
    "and run `wowlab addon install lab` again. An update that changes only the build number "
    "needs no reinstall."
)
"""`str.format` with `interface`, `version` and `folder`."""


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
    listed: tuple[str, ...]  # every entry the listing of the folder found (the re-check)
    notes: tuple[str, ...]


class RemovePlan(_Frozen):
    """What `remove` would delete, from a dry run of the gate."""

    flavor_path: str
    plan: tuple[guard.PlanItem, ...]  # deletes only
    left: tuple[LeftPath, ...]  # links and other entries that are not regular files
    folder_exists: bool
    listed: tuple[str, ...]  # every entry the listing of the folder found (the re-check)
    notes: tuple[str, ...]


class _FlavorLike(Protocol):
    @property
    def path(self) -> Path: ...

    @property
    def folder(self) -> str: ...

    @property
    def version(self) -> str | None: ...


class _Tx(Protocol):
    @property
    def plan(self) -> tuple[guard.PlanItem, ...]: ...

    def delete(self, rel_path: str) -> None: ...


# ─── reading without following links ─────────────────────────────────────────


class _UnreadableError(Exception):
    """A file that is not a regular file readable without following a link."""


def _is_link(path: Path, st: os.stat_result) -> bool:
    """A symlink, or on Windows a junction or other directory link."""
    if stat.S_ISLNK(st.st_mode):
        return True
    try:
        return stat.S_ISDIR(st.st_mode) and path.is_junction()
    except OSError:
        return True


def _read_regular(path: Path, limit: int) -> bytes:
    """The bytes of the regular file at `path`, opened without following a
    link, checked by `fstat` to be the file `lstat` saw, at most `limit`."""
    try:
        seen = path.lstat()
    except OSError as exc:
        raise _UnreadableError(f"cannot be read: {exc}") from exc
    if not stat.S_ISREG(seen.st_mode):
        raise _UnreadableError("is not a regular file")
    if seen.st_size > limit:
        raise _UnreadableError(f"is {seen.st_size} bytes, over {limit}")
    try:
        fd = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise _UnreadableError(f"cannot be opened without following a link: {exc}") from exc
    with os.fdopen(fd, "rb") as handle:
        st = os.fstat(handle.fileno())
        if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != (seen.st_dev, seen.st_ino):
            raise _UnreadableError("changed while it was opened")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise _UnreadableError(f"grew past {limit} bytes")
    return data


# ─── the sources ─────────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent


def _is_workspace_root(folder: Path) -> bool:
    """`.git` (a folder, or a worktree's file) or a `pyproject.toml` that
    declares the uv workspace."""
    try:
        if (folder / ".git").lstat():
            return True
    except OSError:
        pass
    try:
        data = _read_regular(folder / "pyproject.toml", _MAX_PYPROJECT_BYTES)
        tool = tomllib.loads(data.decode("utf-8")).get("tool", {})
    except (_UnreadableError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    uv = tool.get("uv", {}) if isinstance(tool, dict) else {}
    return isinstance(uv, dict) and "workspace" in uv


def _candidate(folder: Path) -> Path | None:
    """`folder/lab/addon/WowLab` if it holds a `WowLab.toc`, refusing a link
    in any of its components."""
    current = folder
    for part in SOURCE_PARTS:
        current = current / part
        try:
            st = current.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise AddonSourceError(f"cannot look at {current}: {exc}") from exc
        if _is_link(current, st):
            raise AddonSourceError(
                f"{current} is a link (a symlink or junction); the lab-addon's sources are "
                "read only from a real folder of the checkout"
            )
        if not stat.S_ISDIR(st.st_mode):
            return None
    try:
        st = (current / TOC_NAME).lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AddonSourceError(f"cannot look at {current / TOC_NAME}: {exc}") from exc
    if not stat.S_ISREG(st.st_mode):
        raise AddonSourceError(f"{current / TOC_NAME} is not a regular file")
    return current


def find_source(start: Path | None = None) -> Path:
    """The lab-addon's source folder: `lab/addon/WowLab/` in the workspace
    root, the nearest ancestor of `start` (default: this package's folder)
    holding `.git` or a `pyproject.toml` that declares the uv workspace. A
    `lab/addon/WowLab/` in any other folder is never used. Raises
    `AddonSourceError` when there is no workspace root above `start`, as
    when wowlab runs from an installed package rather than a checkout, or
    when the root holds no sources."""
    here = (start if start is not None else _HERE).resolve()
    for folder in (here, *here.parents):
        if not _is_workspace_root(folder):
            continue
        found = _candidate(folder)
        if found is None:
            raise AddonSourceError(
                f"the workspace root {folder} holds no lab-addon sources "
                f"({'/'.join(SOURCE_PARTS)}/{TOC_NAME}); nothing was changed"
            )
        return found
    raise AddonSourceError(
        f"no workspace root (a folder holding .git or the uv workspace's pyproject.toml) above "
        f"{here}, so the lab-addon's sources ({'/'.join(SOURCE_PARTS)}/{TOC_NAME}) were not "
        "found; `wowlab addon install lab` runs from a checkout of the wowlab repository "
        "(uv run wowlab ...)"
    )


def _read_source(path: Path) -> bytes:
    try:
        return _read_regular(path, MAX_SOURCE_BYTES)
    except _UnreadableError as exc:
        raise AddonSourceError(f"the lab-addon source {path} {exc}") from exc


def interface_version(version: str | None) -> int:
    """The interface version for a flavor version, by the patch-number rule:
    `"1.60.1.69913"` -> `16001`, `"12.1.5.65432"` -> `120105`. Every part of
    the version must be 1 to `MAX_VERSION_DIGITS` ASCII digits."""
    if version is None:
        raise AddonVersionError(
            "the flavor has no version in .build.info, so the TOC's ## Interface: cannot be "
            "filled; nothing was changed"
        )
    parts = version.split(".")
    if len(parts) < 3 or not all(
        p.isascii() and p.isdecimal() and len(p) <= MAX_VERSION_DIGITS for p in parts
    ):
        raise AddonVersionError(
            f"the flavor's version {version[:64]!r} is not major.minor.patch[.build] with at "
            f"most {MAX_VERSION_DIGITS} digits a part, so the TOC's ## Interface: cannot be "
            "filled; nothing was changed"
        )
    major, minor, patch = (int(p) for p in parts[:3])
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


def _listed(files: Sequence[str], left: Sequence[LeftPath]) -> tuple[str, ...]:
    return tuple(sorted([*files, *(lp.path for lp in left)]))


def _in_folder(rel: str) -> None:
    """Refuse a path outside the addon folder, or one with an empty, `.` or
    `..` component: nothing else is ever touched."""
    parts = rel.split("/")
    if not rel.startswith(ADDON_FOLDER + "/") or any(p in ("", ".", "..") for p in parts):
        raise AddonError(f"{rel!r} is not under {ADDON_FOLDER}/; wowlab refuses to touch it")


def _relisted(flavor_dir: Path, expected: tuple[str, ...]) -> None:
    """With the gate's locks held: the folder must list as it did for the plan."""
    files, left, _ = _installed(flavor_dir)
    if _listed(files, left) != expected:
        raise AddonChangedError(
            f"{ADDON_FOLDER}/ changed after the plan was made, so nothing was changed; run the "
            "command again to see the new plan"
        )


# ─── install ─────────────────────────────────────────────────────────────────


def _install_label(interface: int) -> str:
    return f"addon install {LAB_ADDON} (## Interface: {interface})"


def _deletes(tx: _Tx, paths: Iterable[str], left: list[LeftPath] | None) -> None:
    """Plan a delete of each path. With `left`, a path the gate refuses is
    set aside there (install's leftovers); without it the refusal raises."""
    for rel in paths:
        _in_folder(rel)
        if left is None:
            tx.delete(rel)
            continue
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
    present, found_left, _ = _installed(flavor.path)
    left = list(found_left)
    extras = [p for p in present if p not in files]
    with guard.transaction(
        flavor, label=_install_label(interface), store=store, dry_run=True
    ) as tx:
        for rel, data in files.items():
            _in_folder(rel)
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
        listed=_listed(present, found_left),
        notes=(
            PATCH_NOTE.format(interface=interface, version=version, folder=flavor.folder),
            LOAD_NOTE,
        ),
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
    return its journal record id (`wowlab undo` reverses it).

    Before anything is written, with the gate's locks held, the folder is
    listed again and every file the plan called up to date is read again;
    a difference, or a gate plan that differs from `plan`, raises
    `AddonChangedError` inside the transaction, so the gate rolls back."""
    files: Mapping[str, bytes] = addon_files(plan.interface, Path(plan.source))
    for item in plan.plan:
        _in_folder(item.path)
    for rel in plan.unchanged:
        _in_folder(rel)
    changed = AddonChangedError(
        "the lab-addon's sources or its installed files changed after the plan was made, so "
        "nothing was changed; run the command again to see the new plan"
    )
    with guard.transaction(flavor, label=_install_label(plan.interface), store=store) as tx:
        _relisted(flavor.path, plan.listed)
        for rel in plan.unchanged:
            try:
                current = _read_regular(flavor.path.joinpath(*rel.split("/")), MAX_SOURCE_BYTES)
            except _UnreadableError as exc:
                raise changed from exc
            if rel not in files or current != files[rel]:
                raise changed
        for item in plan.plan:
            if item.after is None:
                tx.delete(item.path)
            elif item.path not in files:
                raise changed
            else:
                tx.write(item.path, files[item.path])
        _checked(tx, plan.plan)
    return guard.history(store=store)[-1].id


# ─── remove ──────────────────────────────────────────────────────────────────

_REMOVE_LABEL = f"addon remove {LAB_ADDON}"


def _remove_notes(left: Sequence[LeftPath], exists: bool) -> tuple[str, ...]:
    notes = [SAVED_VARIABLES_NOTE]
    if any(lp.path.casefold().endswith(".toc") for lp in left):
        notes.append(TOC_LEFT_NOTE)
    elif exists:
        notes.append(FOLDER_STAYS_NOTE + (" " + FOLDER_LEFT_NOTE if left else ""))
    return tuple(notes)


def _toc_found(present: Sequence[str], left: Sequence[LeftPath]) -> bool:
    """Whether the listing found the addon's `WowLab.toc`, as a regular file
    or as an entry left alone (a link to a TOC still loads the addon),
    compared case-folded, as a case-insensitive volume would find it."""
    toc = f"{ADDON_FOLDER}/{TOC_NAME}".casefold()
    return any(rel.casefold() == toc for rel in (*present, *(lp.path for lp in left)))


def plan_remove(flavor: _FlavorLike, *, store: Path | None = None) -> RemovePlan:
    """What `remove` would delete: every regular file under the addon folder,
    from a dry run of the gate. SavedVariables are never part of it. When
    the gate refuses any delete, the removal is refused as a whole: a
    `guard.GuardError` (the CLI's exit 3) names every refused path with the
    gate's reason, followed by `REMOVE_REFUSED_NOTE` and, when a `WowLab.toc`
    was among the entries found (a regular file or one left alone, such as a
    link), `STILL_INSTALLED_NOTE`."""
    present, left, exists = _installed(flavor.path)
    refused: list[str] = []
    with guard.transaction(flavor, label=_REMOVE_LABEL, store=store, dry_run=True) as tx:
        for rel in present:
            _in_folder(rel)
            try:
                tx.delete(rel)
            except guard.PathNotAllowedError as exc:
                refused.append(str(exc))
        whole = tx.plan
    if refused:
        notes = [REMOVE_REFUSED_NOTE]
        if _toc_found(present, left):
            notes.append(STILL_INSTALLED_NOTE)
        raise guard.GuardError("\n".join([*refused, " ".join(notes)]))
    return RemovePlan(
        flavor_path=str(flavor.path),
        plan=whole,
        left=tuple(left),
        folder_exists=exists,
        listed=_listed(present, left),
        notes=_remove_notes(left, exists),
    )


def remove(plan: RemovePlan, flavor: _FlavorLike, *, store: Path | None = None) -> str:
    """Delete exactly the files `plan` lists, in one gate transaction, and
    return its journal record id (`wowlab undo` puts them back). The folder
    is listed again first, with the gate's locks held."""
    for item in plan.plan:
        _in_folder(item.path)
    with guard.transaction(flavor, label=_REMOVE_LABEL, store=store) as tx:
        _relisted(flavor.path, plan.listed)
        _deletes(tx, (item.path for item in plan.plan), None)
        _checked(tx, plan.plan)
    return guard.history(store=store)[-1].id
