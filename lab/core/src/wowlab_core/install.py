"""install: find a World of Warcraft install and its flavors (docs/LAB_PLAN.md §6.1, M10-05).

Formats: `docs/LAB_FORMATS.md` §1 (`.build.info`) and §2 (`.flavor.info`),
including the dated amendments at the foot of that file. The 2026-09-22
amendment to LAB_PLAN §6.1 (M10-05 reviews) records where this module goes
beyond the original model.

Root resolution, first hit wins: the explicit argument, then the
`WOWLAB_WOW_ROOT` environment variable, then the platform defaults
(`default_roots`). An explicit root or a set environment variable is final:
if it is not an install, `NotAnInstallError` is raised rather than falling
through to a default. Only the defaults are searched: a default that is not
a directory, or cannot be checked (including a drive that is not ready), is
passed over, and one that is a directory holding an unusable `.build.info` is
reported. When no default holds an install, `InstallNotFoundError` lists the
locations that were searched (absent, or a directory without `.build.info`)
apart from the ones that could not be checked. There are no registry
reads and no Battle.net database reads; Linux (Wine, Lutris, Proton) is
reached through the explicit root or the environment variable only.

A directory is an install if it holds a readable, regular file
`.build.info`. A flavor is a child directory whose name starts and ends with
`_` and which holds a regular file `.flavor.info`. A child named `_*_`
without a regular `.flavor.info`, or one that is a link, or whose
`.flavor.info` is a link, is not taken as a flavor; its name is listed in
`other_dirs` (a regular file of that name is ignored), and with the reason
it was not taken in `not_taken` (M13-45). A link is what
`is_link` says, the one link test of `layout`, `tree`, `filemap gaps` and
`explain` (M13-35, M13-37): a symlink, a junction, or any other Windows
reparse point that names another path (a WCI link, a WSL symlink). Links are
not followed, whether the folder or its `.flavor.info`: nothing is listed or
read through one. `.flavor.info` is opened without following a link and must
be the file its `lstat` saw (`snapshot.open_regular_file`, M13-46), so one
replaced by a link between the check and the read is refused and its folder
listed in `other_dirs` too. The folder is held to be the directory the scan
`lstat`ed (M13-54): on POSIX `.flavor.info` is opened through a descriptor
on the folder (`O_DIRECTORY | O_NOFOLLOW`, checked against that `lstat`), and
on every platform the folder is `lstat`ed again before and after the read,
so one swapped for a link or another directory after the scan is refused
(`folder_changed`). `.build.info` is read as `snapshot.read_regular_file`
reads (never blocking on a FIFO or device put there after the check, and
the file the check saw). The flavor's product code comes
from `.flavor.info` and is joined to the `.build.info` row with the same
`Product`. Discovery never raises on a partial install: a flavor with no
matching row, or with an unreadable `.flavor.info`, is returned with
`version=None`, never dropped. The one error is a root without a readable,
regular `.build.info`.

Nothing about a flavor is known here (L6): folder names, product codes,
versions and builds all come from the files on disk.

Reads only (L1): files are opened for reading, directories are listed, and
nothing is created, locked or cached anywhere, inside the install or out.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wowlab_core import longpath, snapshot
from wowlab_core.snapshot import _is_link_stat as _snapshot_link

__all__ = [
    "BUILD_INFO",
    "ENV_ROOT",
    "FLAVOR_INFO",
    "NOT_A_FLAVOR",
    "NOT_A_FLAVOR_CHANGED",
    "NOT_A_FLAVOR_FOLDER_CHANGED",
    "NOT_A_FLAVOR_LINK",
    "NOT_A_FLAVOR_UNCHECKED",
    "NOT_TAKEN_LINK",
    "NO_READABLE_FLAVOR",
    "BuildInfo",
    "BuildInfoRow",
    "Flavor",
    "Install",
    "InstallError",
    "InstallNotFoundError",
    "NotAnInstallError",
    "NotTaken",
    "default_roots",
    "discover",
    "folder_note",
    "is_link",
    "not_taken_why",
    "parse_build_info",
    "parse_flavor_info",
    "read_install",
    "resolve_root",
]

BUILD_INFO = ".build.info"
FLAVOR_INFO = ".flavor.info"
ENV_ROOT = "WOWLAB_WOW_ROOT"

# Battle.net's default name for the shared install folder (GLOSSARY: Install,
# product, flavor). It names no flavor; an owner who chose another name or
# parent is reached only through the explicit root or `WOWLAB_WOW_ROOT`.
_INSTALL_DIR = "World of Warcraft"
_MACOS_PARENTS = ("/Applications",)
# Probed on every drive, in this order, under the drive root ("" is the root).
_WINDOWS_PARENTS = ("", "Program Files (x86)", "Program Files")
_SYSTEM_DRIVE_VAR = "SystemDrive"
_SYSTEM_DRIVE_FALLBACK = "C:"

# `.build.info` columns the library reads (LAB_FORMATS §1). Every other column
# is kept per row in `extra`.
_COL_PRODUCT = "Product"
_COL_VERSION = "Version"
_COL_BUILD_KEY = "Build Key"
_COL_BRANCH = "Branch"
_COL_ACTIVE = "Active"
_COL_TAGS = "Tags"
_KNOWN_COLUMNS = frozenset(
    {_COL_PRODUCT, _COL_VERSION, _COL_BUILD_KEY, _COL_BRANCH, _COL_ACTIVE, _COL_TAGS}
)
# `.flavor.info`'s one column (LAB_FORMATS §2).
_COL_FLAVOR = "Product Flavor"
# `.flavor.info` is two short lines; nothing past this prefix is ever needed.
_FLAVOR_INFO_READ_LIMIT = 4096

_BOM = "\ufeff"

Source = Literal["argument", "environment", "default"]
Reason = Literal["missing", "not_regular", "unreadable"]
# Why a child named `_*_` was not taken as a flavor folder (M13-45): it is a
# link (never followed); it holds no regular `.flavor.info`; its `lstat` was
# refused, so it could not be checked; or its `.flavor.info` was regular when
# the folder was scanned and was refused when it was read (M13-46: it became
# a link or something other than a regular file, or another file was put in
# its place), so it changed between the two; or the folder itself was no
# longer the directory the scan saw when it was read (M13-54: it became a link
# or another directory, or is gone). The last two are never worded as a link:
# what took its place may be a real folder or a regular file.
NotTakenReason = Literal[
    "link", "no_flavor_info", "not_checked", "flavor_info_changed", "folder_changed"
]

# The words for each reason (`NotTaken.note`), shown by `doctor`, `install
# show`, `explain`, the first run and the `--flavor` lookups; `tree
# --install` and `filemap gaps` give a link `NOT_A_FLAVOR_LINK` after their
# own link words, and a folder its note (`folder_note`). `gaps` re-exports
# the first two. Worded "not taken", never "is not a flavor": the client
# reads through a linked flavor folder like a folder; only wowlab declines to
# (M13-37 review, M13-45).
NOT_A_FLAVOR = "not taken as a flavor folder (no regular .flavor.info file)"
# For a link: no reason in brackets, since every place it is shown already
# says the entry is a link that is not followed (M13-37 review).
NOT_A_FLAVOR_LINK = "not taken as a flavor folder"
NOT_A_FLAVOR_UNCHECKED = "not taken as a flavor folder (it could not be checked)"
NOT_A_FLAVOR_CHANGED = (
    "not taken as a flavor folder (its .flavor.info changed as it was read; run the command again)"
)
NOT_A_FLAVOR_FOLDER_CHANGED = (
    "not taken as a flavor folder (it changed as it was read; run the command again)"
)
# Said wherever a linked `_*_` folder is the reason a flavor is missing.
NOT_TAKEN_LINK = "wowlab reads a flavor only from a real folder in the install"
# "<root> has no flavor folders" is literally false of an install whose
# `_retail_` is a junction; with a folder in `not_taken` this is said instead.
NO_READABLE_FLAVOR = "has no flavor folder wowlab can read (a real folder named _..._ holding a regular .flavor.info)"


# ─── errors ──────────────────────────────────────────────────────────────────


class InstallError(Exception):
    """Base class for everything this module raises on purpose."""


class NotAnInstallError(InstallError):
    """The chosen root has no readable, regular `.build.info`.

    `reason` is `missing`, `not_regular` (a directory, FIFO, device, ...) or
    `unreadable` (the operating system refused; `strerror` says why). A root
    that does not exist at all is `missing` with `root_exists=False`.
    """

    def __init__(
        self,
        root: Path,
        source: Source,
        reason: Reason = "missing",
        strerror: str | None = None,
        hint: str = "",
        *,
        root_exists: bool = True,
    ) -> None:
        self.root = root
        self.source = source
        self.reason = reason
        self.strerror = strerror
        self.root_exists = root_exists
        if not root_exists:
            super().__init__(f"{root} (from {source}) does not exist")
            return
        what = {
            "missing": f"it has no {BUILD_INFO}",
            "not_regular": f"{BUILD_INFO} is not a regular file",
            "unreadable": f"cannot read {BUILD_INFO} ({strerror})",
        }[reason]
        super().__init__(f"{root} (from {source}) is not a WoW install: {what}{hint}")


class InstallNotFoundError(InstallError):
    """No root was given and no platform default holds an install.

    `searched` are the defaults that were looked at and hold no install:
    nothing exists there, or it is a directory without `.build.info`.
    `unchecked` are the defaults that could not be checked: something that
    is not a directory is there, or looking raised an error (a drive that is
    not ready, a refusal); `unchecked_reasons` says why, in the same order.
    A location is in one list or the other, never both.
    """

    def __init__(
        self,
        searched: Sequence[Path],
        unchecked: Sequence[Path] = (),
        unchecked_reasons: Sequence[str] = (),
    ) -> None:
        self.searched = tuple(searched)
        self.unchecked = tuple(unchecked)
        reasons = tuple(unchecked_reasons)
        self.unchecked_reasons = reasons + ("",) * (len(self.unchecked) - len(reasons))
        if self.searched:
            where = ", ".join(str(p) for p in self.searched)
        elif self.unchecked:
            where = "nothing"
        else:
            where = "nothing on this platform"
        not_checked = ""
        if self.unchecked:
            items = ", ".join(
                f"{p} ({why})" if why else str(p)
                for p, why in zip(self.unchecked, self.unchecked_reasons, strict=True)
            )
            not_checked = f"; could not check: {items}"
        super().__init__(
            f"no WoW install at the locations searched by default (searched: {where}"
            f"{not_checked}); pass the install folder (the one that holds {BUILD_INFO}) "
            f"or set {ENV_ROOT}"
        )


# ─── models ──────────────────────────────────────────────────────────────────


class BuildInfoRow(BaseModel):
    """One product row of `.build.info`.

    A known column the header does not have is `None`; an empty cell is `""`.
    Every other column is in `extra` as `(name, value)` pairs in header
    order, the name without its `!TYPE:size` suffix (L4). A row shorter than
    the header is padded with empty cells; cells past the header's end are
    kept in `overflow`.
    """

    model_config = ConfigDict(frozen=True)

    product: str | None
    version: str | None
    build_key: str | None
    branch: str | None
    active: str | None  # the cell as written, e.g. "1"
    tags: str | None  # the cell as written; the `?` characters are literal
    extra: tuple[tuple[str, str], ...]
    overflow: tuple[str, ...] = ()

    @property
    def extra_map(self) -> Mapping[str, str]:
        """`extra` as a read-only mapping (names are unique by construction)."""
        return MappingProxyType(dict(self.extra))

    @property
    def is_active(self) -> bool:
        """True when `Active` is a non-zero decimal."""
        cell = (self.active or "").strip()
        return cell.isascii() and cell.isdecimal() and int(cell) != 0


class BuildInfo(BaseModel):
    """A parsed `.build.info`: the header cells as written and every row."""

    model_config = ConfigDict(frozen=True)

    columns: tuple[str, ...]  # header cells verbatim, e.g. "Build Key!HEX:16"
    rows: tuple[BuildInfoRow, ...]


class Flavor(BaseModel):
    """One flavor folder of an install, joined to its `.build.info` row."""

    model_config = ConfigDict(frozen=True)

    folder: str  # as found on disk
    # As found in .flavor.info; "" if .flavor.info is empty, has no product
    # code, or cannot be read.
    product: str
    # From .build.info; None if no row matches or its `Version` cell is empty.
    version: str | None
    # Last component of `version`, if it is a decimal. Not unique across
    # versions (GLOSSARY: Build); key on `version`.
    build: int | None
    build_key: str | None
    matching_rows: int  # how many .build.info rows name `product`
    path: Path


class NotTaken(BaseModel):
    """A child named `_*_` that discovery did not take as a flavor folder,
    and why (M13-45). `target` is a link's text as stored, when it can be
    read (a WCI link or a WSL symlink has none `os.readlink` can read); it
    is read from the link itself, never through it. `note` is the reason in
    the words every command prints."""

    model_config = ConfigDict(frozen=True)

    name: str
    reason: NotTakenReason
    target: str | None
    note: str


def not_taken_why(entry: NotTaken) -> str:
    """The reason alone, without "not taken as a flavor folder": "link to
    <target>, not followed" (or "link (where it points is unknown), not
    followed"); for any other reason, what the note says in brackets ("no
    regular .flavor.info file", "it could not be checked: <error>", "its
    .flavor.info changed as it was read; run the command again", "it changed
    as it was read; run the command again")."""
    if entry.reason == "link":
        to = f" to {entry.target}" if entry.target is not None else " (where it points is unknown)"
        return f"link{to}, not followed"
    return entry.note.removeprefix(f"{NOT_A_FLAVOR_LINK} (").removesuffix(")")


def _not_taken(
    name: str, reason: NotTakenReason, target: str | None = None, error: str | None = None
) -> NotTaken:
    """The entry with its note. `error`: the operating system's words for a
    refused `lstat` (`not_checked`), added to its note."""
    if reason == "link":
        why = not_taken_why(NotTaken(name=name, reason=reason, target=target, note=""))
        note = f"{why}; {NOT_A_FLAVOR_LINK}"
    elif reason == "not_checked" and error:
        note = f"{NOT_A_FLAVOR_LINK} (it could not be checked: {error})"
    else:
        notes = {
            "no_flavor_info": NOT_A_FLAVOR,
            "not_checked": NOT_A_FLAVOR_UNCHECKED,
            "flavor_info_changed": NOT_A_FLAVOR_CHANGED,
            "folder_changed": NOT_A_FLAVOR_FOLDER_CHANGED,
        }
        note = notes[reason]
    return NotTaken(name=name, reason=reason, target=target, note=note)


def folder_note(inst: Install, name: str) -> str:
    """The note `tree --install` and `filemap gaps` give a folder (not a
    link) of `other_dirs`: its `not_taken` note, unless discovery saw a link
    there, or the model carries no reasons; then `NOT_A_FLAVOR`."""
    entry = next((n for n in inst.not_taken if n.name == name), None)
    return entry.note if entry is not None and entry.reason != "link" else NOT_A_FLAVOR


class Install(BaseModel):
    """An install root, every flavor found under it, and its `.build.info`.

    JSON carries `raw_build_info` as URL-safe base64 (RFC 4648 §5, as pydantic
    writes it) and reads it back the same way,
    so `model_validate_json(model_dump_json())` is the identity.
    """

    model_config = ConfigDict(frozen=True, ser_json_bytes="base64", val_json_bytes="base64")

    root: Path
    flavors: tuple[Flavor, ...]  # sorted by folder
    # `.build.info` rows in file order. A row does not show that its flavor
    # folder exists or is complete; see `flavors`.
    products: tuple[BuildInfoRow, ...]
    # Children named `_*_` not taken as flavors (no regular .flavor.info, a
    # .flavor.info that is a link, or a folder that is a link, by `is_link`;
    # links are not followed; or one that changed as it was read), sorted.
    other_dirs: tuple[str, ...]
    raw_build_info: bytes  # the file, byte for byte (L4)
    # True when .build.info or any .flavor.info had bytes that are not UTF-8;
    # the parsed text fields then hold U+FFFD in their place.
    decode_errors: bool
    # The names of `other_dirs`, in its order, each with why it was not
    # taken (M13-45). Left out of the JSON when empty, so an install with no
    # such folder prints as before; read back as empty.
    not_taken: tuple[NotTaken, ...] = Field(default=(), exclude_if=lambda v: not v)

    @model_validator(mode="after")
    def _not_taken_names_other_dirs(self) -> Install:
        if self.not_taken and tuple(n.name for n in self.not_taken) != self.other_dirs:
            raise ValueError("not_taken must name the folders of other_dirs, in its order")
        return self


# ─── parsing ─────────────────────────────────────────────────────────────────


def _decode(data: bytes) -> tuple[str, bool]:
    """Text for parsing, and whether any byte was not UTF-8. The raw bytes
    are what is kept (L4); this text only feeds the parsed fields."""
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), True


def _lines(text: str) -> list[str]:
    """Split on LF, drop one trailing CR per line, skip blank lines.

    Not `str.splitlines()`: that also splits on form feeds, vertical tabs and
    Unicode separators, any of which could sit inside a cell.
    """
    text = text.removeprefix(_BOM)
    out: list[str] = []
    for line in text.split("\n"):
        line = line.removesuffix("\r")
        if line.strip():
            out.append(line)
    return out


def _column_name(cell: str) -> str:
    """`Build Key!HEX:16` -> `Build Key`; a cell without `!` is its own name."""
    return cell.split("!", 1)[0]


def parse_build_info(text: str) -> BuildInfo:
    """Parse `.build.info` text (LAB_FORMATS §1). Never raises on content.

    Columns are found by header name, never by position: LAB_FORMATS §1
    reports that their set and order vary by agent version (one capture so
    far). An empty text gives no columns and no rows. When a name appears
    twice in the header, the first occurrence is the one read; the later ones
    are kept in `extra` under `name#<index>`.
    """
    lines = _lines(text)
    if not lines:
        return BuildInfo(columns=(), rows=())
    columns = tuple(lines[0].split("|"))
    names: list[str] = []
    seen: set[str] = set()
    for index, cell in enumerate(columns):
        name = _column_name(cell)
        names.append(name if name not in seen else f"{name}#{index}")
        seen.add(name)

    rows: list[BuildInfoRow] = []
    for line in lines[1:]:
        cells = line.split("|")
        padded = cells + [""] * (len(names) - len(cells))
        by_name = dict(zip(names, padded, strict=False))
        rows.append(
            BuildInfoRow(
                product=by_name.get(_COL_PRODUCT),
                version=by_name.get(_COL_VERSION),
                build_key=by_name.get(_COL_BUILD_KEY),
                branch=by_name.get(_COL_BRANCH),
                active=by_name.get(_COL_ACTIVE),
                tags=by_name.get(_COL_TAGS),
                extra=tuple((k, v) for k, v in by_name.items() if k not in _KNOWN_COLUMNS),
                overflow=tuple(cells[len(names) :]),
            )
        )
    return BuildInfo(columns=columns, rows=tuple(rows))


def parse_flavor_info(text: str) -> str:
    """The product code in `.flavor.info` text (LAB_FORMATS §2), or `""`.

    The file is a one-column pipe table: header `Product Flavor!STRING:0`,
    then the code. If the header names no `Product Flavor` column, the first
    cell of the first row is taken.
    """
    lines = _lines(text)
    if len(lines) < 2:
        return ""
    names = [_column_name(c) for c in lines[0].split("|")]
    cells = lines[1].split("|")
    index = names.index(_COL_FLAVOR) if _COL_FLAVOR in names else 0
    return cells[index].strip() if index < len(cells) else ""


def _build_number(version: str) -> int | None:
    last = version.rsplit(".", 1)[-1].strip()
    return int(last) if last.isdecimal() and last.isascii() else None


# ─── discovery ───────────────────────────────────────────────────────────────


def _drive_root(drive: str) -> str:
    """`C:`, `c:\\` -> `C:\\`."""
    return drive.rstrip("\\/").upper() + "\\"


def default_roots(
    platform: str | None = None,
    drives: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[PurePath, ...]:
    """Locations searched when no root is given, in order (LAB_PLAN §6.1).

    macOS: `/Applications/World of Warcraft`. Windows: on every drive,
    `<drive>\\World of Warcraft`, then `<drive>\\Program Files (x86)\\World of
    Warcraft`, then `<drive>\\Program Files\\World of Warcraft`; the system
    drive (`%SystemDrive%`, else `C:`) first, then every other drive in the
    order `drives` lists them. Anything else: none (use the explicit root).

    `drives` defaults to `os.listdrives()` on Windows. Every listed drive is
    probed: telling fixed drives from removable or network ones needs a Win32
    call, which is out of scope for this package. `environ` defaults to
    `os.environ`.
    """
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return tuple(PurePosixPath(parent, _INSTALL_DIR) for parent in _MACOS_PARENTS)
    if platform != "win32":
        return ()
    env = os.environ if environ is None else environ
    system = env.get(_SYSTEM_DRIVE_VAR, "").strip() or _SYSTEM_DRIVE_FALLBACK
    if drives is None:
        listdrives = getattr(os, "listdrives", None)
        drives = listdrives() if listdrives is not None else []
    ordered: list[str] = []
    for drive in [system, *drives]:
        root = _drive_root(drive)
        if root not in ordered:
            ordered.append(root)
    return tuple(
        PureWindowsPath(drive, parent, _INSTALL_DIR)
        for drive in ordered
        for parent in _WINDOWS_PARENTS
    )


def _as_path(value: Path | str) -> Path:
    """Absolute, and plain where it was given in extended-length form
    (`\\\\?\\C:\\...`): `Install.root` and every flavor path are plain
    (M13-15), and every read below reaches a long path all the same."""
    return longpath.plain(Path(value).expanduser().absolute())


def is_link(path: Path, st: os.stat_result) -> bool:
    """A link that is never followed: a symlink, a junction, or a Windows
    reparse point with the name-surrogate bit (a reparse point that names
    another path, such as a WCI link or a WSL symlink), the rule `snapshot`
    uses (`snapshot._is_link_stat`). `st` is `path`'s `lstat`. The one link
    test of discovery, the inventory walk, `tree`, `filemap gaps` and
    `explain` (`layout.is_link` and `gaps.is_link` are this function;
    M13-35, M13-37). Other reparse points (OneDrive cloud placeholders,
    deduplicated files) hold real content and are walked, as discovery and
    `layout` take them (M13-04: an install under OneDrive is reported, never
    refused)."""
    if _snapshot_link(st):
        return True
    try:
        return longpath.is_junction(path)
    except OSError:
        return False


def _is_regular(path: Path) -> bool:
    """A regular file that is no link at the last component (`is_link`)."""
    try:
        st = longpath.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and not is_link(path, st)


def _flavor_folder_hint(root: Path) -> str:
    """A pointer to the parent when `root` is a flavor folder or sits in an install."""
    if root.parent == root:
        return ""
    if _is_regular(root / FLAVOR_INFO):
        return f"; this looks like the flavor folder {root.name!r}; pass its parent {root.parent}"
    try:
        parent_is_install = longpath.is_file(root.parent / BUILD_INFO)
    except OSError:
        return ""
    return f"; its parent {root.parent} is an install; pass that" if parent_is_install else ""


def _probe(root: Path, source: Source) -> os.stat_result:
    """Raise `NotAnInstallError` unless `root/.build.info` is a regular file;
    return its `stat` (a link to a regular file is followed, as always).

    A refusal from the operating system is `unreadable`, never `missing`. So
    is a root spelled `\\\\?\\...` that has no plain spelling (a volume GUID
    path, a `..` component, a trailing dot): `Install.root` is always plain
    (M13-15), and the write gate could not restore into such a root.
    """
    if longpath.ON_WINDOWS and str(root).startswith("\\\\?\\"):
        raise NotAnInstallError(
            root,
            source,
            "unreadable",
            "an extended-length path with no plain spelling; name the install as "
            "C:\\... or \\\\server\\share\\...",
        )
    try:
        st = longpath.stat(root / BUILD_INFO)
    except (FileNotFoundError, NotADirectoryError):
        try:
            root_exists = longpath.exists(root)
        except OSError:
            root_exists = True
        if not root_exists:
            raise NotAnInstallError(root, source, "missing", root_exists=False) from None
        raise NotAnInstallError(root, source, "missing", hint=_flavor_folder_hint(root)) from None
    except OSError as exc:
        raise NotAnInstallError(
            root, source, "unreadable", exc.strerror or str(exc), hint=_flavor_folder_hint(root)
        ) from exc
    if not stat.S_ISREG(st.st_mode):
        raise NotAnInstallError(root, source, "not_regular", hint=_flavor_folder_hint(root))
    return st


# What `NotAnInstallError` says when `.build.info` was not the file `_probe`
# checked by the time it was opened (M13-54).
_BUILD_INFO_CHANGED = "it changed as it was read; run the command again"


def _read_build_info(root: Path, source: Source, probed: os.stat_result) -> bytes:
    """`root/.build.info`, the file `_probe` saw (`probed`, its `stat`).

    Read by `snapshot.read_regular_file` with `probed` as `expect` (M13-54):
    the open never blocks, so a FIFO or device put there after the check is
    refused at once rather than waited on, and a file that is not the one
    checked is refused too. A `.build.info` that is a link to a regular file
    is taken as `_probe` takes it (followed, as before M13-54): the file it
    resolves to is opened, and held to be the file `probed` saw."""
    path = root / BUILD_INFO
    try:
        if _snapshot_link(longpath.lstat(path)):
            path = Path(longpath.realpath(path))
        return snapshot.read_regular_file(path, expect=probed)
    except snapshot.UnsafeReadError as exc:  # not the regular file checked
        raise NotAnInstallError(root, source, "unreadable", _BUILD_INFO_CHANGED) from exc
    except OSError as exc:  # refused, or gone since the check
        if exc.errno == errno.ELOOP:  # a link swapped in after the check
            raise NotAnInstallError(root, source, "unreadable", _BUILD_INFO_CHANGED) from exc
        reason: Reason = "missing" if isinstance(exc, FileNotFoundError) else "unreadable"
        raise NotAnInstallError(root, source, reason, exc.strerror or str(exc)) from exc


def _resolve(
    root: Path | str | None,
    environ: Mapping[str, str] | None,
    defaults: Sequence[PurePath | str] | None,
) -> tuple[Path, Source]:
    if root is not None and str(root) != "":
        chosen = _as_path(root)
        _probe(chosen, "argument")
        return chosen, "argument"
    env = os.environ if environ is None else environ
    from_env = env.get(ENV_ROOT, "")
    if from_env.strip():
        chosen = _as_path(from_env)
        _probe(chosen, "environment")
        return chosen, "environment"
    candidates = [Path(p) for p in (default_roots(environ=env) if defaults is None else defaults)]
    searched: list[Path] = []
    unchecked: list[Path] = []
    reasons: list[str] = []
    for candidate in candidates:
        # Absent (the name or a parent is missing) is searched and empty. A
        # drive that is not ready (winerror 21 and kin), a refusal, or
        # something there that is not a directory could not be checked: it is
        # passed over and reported as such, never as searched.
        try:
            st = longpath.stat(candidate)
        except (FileNotFoundError, NotADirectoryError):
            searched.append(candidate)
            continue
        except (OSError, ValueError) as exc:
            unchecked.append(candidate)
            reasons.append((exc.strerror or str(exc)) if isinstance(exc, OSError) else str(exc))
            continue
        if not stat.S_ISDIR(st.st_mode):
            unchecked.append(candidate)
            reasons.append("not a directory")
            continue
        searched.append(candidate)
        try:
            _probe(candidate.absolute(), "default")
        except NotAnInstallError as exc:
            if exc.reason == "missing":
                continue
            raise  # present but not usable: say so rather than skip it
        return candidate.absolute(), "default"
    raise InstallNotFoundError(searched, unchecked, reasons)


def resolve_root(
    root: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    defaults: Sequence[PurePath | str] | None = None,
) -> Path:
    """The install root to use: `root`, else `$WOWLAB_WOW_ROOT`, else the
    first default that holds a `.build.info`.

    `environ` defaults to `os.environ`; an empty variable counts as unset.
    `defaults` defaults to `default_roots()`. Raises `NotAnInstallError` when
    the argument or the variable names a directory without a regular
    `.build.info` (never falling through to a default), or when a default
    is a directory holding one that is not regular or cannot be read; raises
    `InstallNotFoundError` when no default holds one at all. A default that
    is not a directory, or cannot be checked (a drive that is not ready
    included), is passed over, and the error names it as not checked.
    """
    return _resolve(root, environ, defaults)[0]


_Refused = Literal["flavor_info_changed", "folder_changed"]


def _folder_fds() -> bool:
    """True where a flavor folder can be held by a descriptor that was opened
    without following a link, and `.flavor.info` opened relative to it
    (POSIX). Asked at each read, not fixed at import, so a test can stand in
    for Windows by setting `snapshot._O_NOFOLLOW` to 0."""
    return (
        bool(snapshot._O_NOFOLLOW)
        and hasattr(os, "O_DIRECTORY")
        and {os.open, os.stat} <= os.supports_dir_fd
    )


def _same_folder(directory: Path, scanned: os.stat_result) -> bool:
    """`directory` is, by `lstat` now, still the directory the scan saw
    (`scanned`): no link by `is_link`, a directory, the same device and inode."""
    try:
        now = longpath.lstat(directory)
    except OSError:
        return False
    return (
        not is_link(directory, now)
        and stat.S_ISDIR(now.st_mode)
        and (now.st_dev, now.st_ino) == (scanned.st_dev, scanned.st_ino)
    )


def _open_folder(directory: Path, scanned: os.stat_result) -> int | None:
    """A descriptor on `directory`, opened with `O_DIRECTORY | O_NOFOLLOW` and
    held to be the directory the scan saw by `fstat`; None where there are no
    folder descriptors (`_folder_fds`), or where the folder may be searched
    but not listed (an open for reading is refused, as `lstat` of a file in
    it is not), so the `lstat` each side of the read is all that holds it.
    Raises `OSError` when it is a link now (`ELOOP`, or `ENOTDIR` on some
    systems), not a directory, gone, or another directory."""
    if not _folder_fds():
        return None
    try:
        # Read-only, flags spelled in the call so the write-site scan sees them.
        # `_folder_fds` holds only where `os.O_NOFOLLOW` exists.
        fd = os.open(
            longpath.extended(directory),
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except PermissionError:
        return None
    try:
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) != (scanned.st_dev, scanned.st_ino):
            raise snapshot.UnsafeReadError(f"{directory} was replaced after it was scanned")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_flavor_product(directory: Path, scanned: os.stat_result) -> tuple[str, bool] | _Refused:
    """(product, decode errors) from `directory`'s `.flavor.info`, or why it
    was refused.

    The folder is the one the scan saw (M13-54), `scanned` being its `lstat`
    there. It is `lstat`ed again before and after the read, and must still be
    that directory and no link; on POSIX `.flavor.info` is also opened
    relative to a descriptor on the folder (`_open_folder`), so no link put
    in the folder's place is ever read through. A folder that fails any of
    these is `folder_changed`.

    The check and the read of `.flavor.info` name one file (M13-46): it is
    `lstat`ed, then opened by `snapshot.open_regular_file` with that `lstat`
    as `expect`, so a final link is never followed (`O_NOFOLLOW`, or on
    Windows an `lstat` each side of the open) and a file swapped in after
    the `lstat` is refused: `flavor_info_changed`, as is one that is a link
    or no longer a regular file. Any other failure (gone, refused by the
    operating system) is an unreadable `.flavor.info`: `("", False)`, as
    before."""
    if not _same_folder(directory, scanned):
        return "folder_changed"
    try:
        folder = _open_folder(directory, scanned)
    except OSError:  # a link now, another directory, or gone
        return "folder_changed"
    try:
        read = _read_flavor_info(directory, folder)
    finally:
        if folder is not None:
            os.close(folder)
    if not _same_folder(directory, scanned):
        return "folder_changed"
    return read


def _read_flavor_info(directory: Path, folder: int | None) -> tuple[str, bool] | _Refused:
    """`directory`'s `.flavor.info`, opened relative to the descriptor
    `folder` when there is one, else by its path (`_read_flavor_product`)."""
    path = Path(FLAVOR_INFO) if folder is not None else directory / FLAVOR_INFO
    try:
        if folder is not None:
            st = os.stat(path, dir_fd=folder, follow_symlinks=False)
            linked = _snapshot_link(st)  # no junctions where there are folder descriptors
        else:
            st = longpath.lstat(path)
            linked = is_link(path, st)
        if linked or not stat.S_ISREG(st.st_mode):
            return "flavor_info_changed"
        fd, _ = snapshot.open_regular_file(path, expect=st, dir_fd=folder)
    except snapshot.UnsafeReadError:
        return "flavor_info_changed"
    except OSError as exc:
        if exc.errno == errno.ELOOP:  # a link swapped in after the lstat
            return "flavor_info_changed"
        return "", False
    try:
        chunks: list[bytes] = []
        left = _FLAVOR_INFO_READ_LIMIT
        while left > 0 and (chunk := os.read(fd, left)):
            chunks.append(chunk)
            left -= len(chunk)
    except OSError:
        return "", False
    finally:
        os.close(fd)
    text, errors = _decode(b"".join(chunks))
    return parse_flavor_info(text), errors


def _matching(rows: Sequence[BuildInfoRow], product: str) -> list[BuildInfoRow]:
    if not product:
        return []
    return [r for r in rows if (r.product or "").strip() == product]


def _match_row(matches: Sequence[BuildInfoRow]) -> BuildInfoRow | None:
    """When several rows name the product, the first active one, else the
    first. **[verify]** Neither case appears in a capture and which row the
    client uses is unknown; this is a deterministic tie-break, not client
    behaviour."""
    if not matches:
        return None
    return next((r for r in matches if r.is_active), matches[0])


def _link_text(path: Path) -> str | None:
    """A link's text as stored, read from the link itself; None when it
    cannot be read (a WCI link or a WSL symlink on Windows). Spelled as
    `layout.link_facts` spells it (M13-43): not through `Path`, so a
    relative text is kept exactly, and on Windows without the
    extended-length prefix an absolute text is answered with."""
    try:
        target = os.readlink(longpath.extended(path))  # noqa: PTH115
    except (OSError, ValueError):
        return None
    return longpath.plain_text(target) if longpath.ON_WINDOWS else target


def _scan_children(root: Path) -> tuple[list[tuple[Path, os.stat_result]], list[NotTaken]]:
    """(flavor directories, each with its `lstat`, which the read holds it to;
    the other `_*_` directories or links, each with why it was not taken,
    sorted by name).

    A child is judged by its `lstat` alone (`is_link`): a link is named and
    never followed, whatever it points at, so nothing under it is read."""
    try:
        children = longpath.iterdir(root)
    except OSError:
        return [], []
    flavors: list[tuple[Path, os.stat_result]] = []
    others: list[NotTaken] = []
    for child in children:
        name = child.name
        if len(name) < 2 or not (name.startswith("_") and name.endswith("_")):
            continue
        try:
            st = longpath.lstat(child)
        except (FileNotFoundError, NotADirectoryError):
            continue  # gone since the listing: not there, so not named
        except OSError as exc:
            # refused: named, as it could not be checked
            others.append(_not_taken(name, "not_checked", error=exc.strerror or str(exc)))
            continue
        if is_link(child, st):
            others.append(_not_taken(name, "link", _link_text(child)))
        elif stat.S_ISDIR(st.st_mode):
            if _is_regular(child / FLAVOR_INFO):
                flavors.append((child, st))
            else:
                others.append(_not_taken(name, "no_flavor_info"))
    return flavors, sorted(others, key=lambda n: n.name)


def _flavor(
    directory: Path, scanned: os.stat_result, rows: Sequence[BuildInfoRow]
) -> tuple[Flavor, bool] | _Refused:
    """The flavor in `directory` (`scanned`: its `lstat` at the scan), or why
    it was refused at the read (`_read_flavor_product`)."""
    read = _read_flavor_product(directory, scanned)
    if isinstance(read, str):
        return read
    product, errors = read
    matches = _matching(rows, product)
    row = _match_row(matches)
    version = ((row.version or "").strip() or None) if row is not None else None
    build_key = ((row.build_key or "").strip() or None) if row is not None else None
    flavor = Flavor(
        folder=directory.name,
        product=product,
        version=version,
        build=_build_number(version) if version is not None else None,
        build_key=build_key,
        matching_rows=len(matches),
        path=directory,
    )
    return flavor, errors


def _read(root: Path, source: Source) -> Install:
    # Checked first, then read as the file checked: a FIFO or device named
    # `.build.info` could block a read (M13-54).
    raw = _read_build_info(root, source, _probe(root, source))
    text, errors = _decode(raw)
    rows = parse_build_info(text).rows
    directories, others = _scan_children(root)
    flavors: list[Flavor] = []
    for directory, scanned in directories:
        found = _flavor(directory, scanned, rows)
        if isinstance(found, str):  # it or its .flavor.info changed after the scan
            others.append(_not_taken(directory.name, found))
            continue
        flavor, flavor_errors = found
        flavors.append(flavor)
        errors = errors or flavor_errors
    flavors.sort(key=lambda f: f.folder)
    others.sort(key=lambda n: n.name)
    return Install(
        root=root,
        flavors=tuple(flavors),
        products=rows,
        other_dirs=tuple(n.name for n in others),
        raw_build_info=raw,
        decode_errors=errors,
        not_taken=tuple(others),
    )


def read_install(root: Path | str) -> Install:
    """Read the install at exactly `root`, with no resolution. Raises
    `NotAnInstallError` if it has no readable, regular `.build.info`; never
    raises on anything else it finds."""
    return _read(_as_path(root), "argument")


def discover(
    root: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    defaults: Sequence[PurePath | str] | None = None,
) -> Install:
    """Resolve the root (`resolve_root`) and read the install there."""
    chosen, source = _resolve(root, environ, defaults)
    return _read(chosen, source)
