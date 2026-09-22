"""install: find a World of Warcraft install and its flavors (docs/LAB_PLAN.md §6.1, M10-05).

Formats: `docs/LAB_FORMATS.md` §1 (`.build.info`) and §2 (`.flavor.info`),
including the dated amendments at the foot of that file. The 2026-09-22
amendment to LAB_PLAN §6.1 (M10-05 reviews) records where this module goes
beyond the original model.

Root resolution, first hit wins: the explicit argument, then the
`WOWLAB_WOW_ROOT` environment variable, then the platform defaults
(`default_roots`). An explicit root or a set environment variable is final:
if it is not an install, `NotAnInstallError` is raised rather than falling
through to a default. Only the defaults are searched. There are no registry
reads and no Battle.net database reads; Linux (Wine, Lutris, Proton) is
reached through the explicit root or the environment variable only.

A directory is an install if it holds a readable, regular file
`.build.info`. A flavor is a child directory whose name starts and ends with
`_` and which holds a regular file `.flavor.info`. A child named `_*_`
without a regular `.flavor.info`, or one that is a symlink, is not a flavor;
its name is listed in `other_dirs` (a regular file of that name is ignored).
Symlinked `_*_` folders are not followed. The flavor's product code comes
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

import os
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "BUILD_INFO",
    "ENV_ROOT",
    "FLAVOR_INFO",
    "BuildInfo",
    "BuildInfoRow",
    "Flavor",
    "Install",
    "InstallError",
    "InstallNotFoundError",
    "NotAnInstallError",
    "default_roots",
    "discover",
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


# ─── errors ──────────────────────────────────────────────────────────────────


class InstallError(Exception):
    """Base class for everything this module raises on purpose."""


class NotAnInstallError(InstallError):
    """The chosen root has no readable, regular `.build.info`.

    `reason` is `missing`, `not_regular` (a directory, FIFO, device, ...) or
    `unreadable` (the operating system refused; `strerror` says why).
    """

    def __init__(
        self,
        root: Path,
        source: Source,
        reason: Reason = "missing",
        strerror: str | None = None,
        hint: str = "",
    ) -> None:
        self.root = root
        self.source = source
        self.reason = reason
        self.strerror = strerror
        what = {
            "missing": f"it has no {BUILD_INFO}",
            "not_regular": f"{BUILD_INFO} is not a regular file",
            "unreadable": f"cannot read {BUILD_INFO} ({strerror})",
        }[reason]
        super().__init__(f"{root} (from {source}) is not a WoW install: {what}{hint}")


class InstallNotFoundError(InstallError):
    """No root was given and no platform default holds an install."""

    def __init__(self, searched: Sequence[Path]) -> None:
        self.searched = tuple(searched)
        where = ", ".join(str(p) for p in self.searched) or "nothing on this platform"
        super().__init__(
            f"no WoW install at the default locations (searched: {where}); pass the "
            f"install folder (the one that holds {BUILD_INFO}) or set {ENV_ROOT}"
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
    # Children named `_*_` that are not flavors (no regular .flavor.info, or
    # a symlink, which is not followed), sorted.
    other_dirs: tuple[str, ...]
    raw_build_info: bytes  # the file, byte for byte (L4)
    # True when .build.info or any .flavor.info had bytes that are not UTF-8;
    # the parsed text fields then hold U+FFFD in their place.
    decode_errors: bool


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
    """Platform default install locations, in search order (LAB_PLAN §6.1).

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
    return Path(value).expanduser().absolute()


def _flavor_folder_hint(root: Path) -> str:
    """A pointer to the parent when `root` looks like a flavor folder."""
    try:
        looks_like_flavor = (root / FLAVOR_INFO).is_file() or (root.parent / BUILD_INFO).is_file()
    except OSError:
        return ""
    if not looks_like_flavor or root.parent == root:
        return ""
    return f"; this looks like the flavor folder {root.name!r}; pass its parent {root.parent}"


def _probe(root: Path, source: Source) -> None:
    """Raise `NotAnInstallError` unless `root/.build.info` is a regular file.

    A refusal from the operating system is `unreadable`, never `missing`.
    """
    try:
        st = (root / BUILD_INFO).stat()
    except (FileNotFoundError, NotADirectoryError):
        raise NotAnInstallError(root, source, "missing", hint=_flavor_folder_hint(root)) from None
    except OSError as exc:
        raise NotAnInstallError(
            root, source, "unreadable", exc.strerror or str(exc), hint=_flavor_folder_hint(root)
        ) from exc
    if not stat.S_ISREG(st.st_mode):
        raise NotAnInstallError(root, source, "not_regular", hint=_flavor_folder_hint(root))


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
    for candidate in candidates:
        try:
            _probe(candidate.absolute(), "default")
        except NotAnInstallError as exc:
            if exc.reason == "missing":
                continue
            raise  # present but not usable: say so rather than skip it
        return candidate.absolute(), "default"
    raise InstallNotFoundError(candidates)


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
    holds one that is not regular or cannot be read; raises
    `InstallNotFoundError` when no default holds one at all.
    """
    return _resolve(root, environ, defaults)[0]


def _read_flavor_product(path: Path) -> tuple[str, bool]:
    try:
        with path.open("rb") as handle:
            data = handle.read(_FLAVOR_INFO_READ_LIMIT)
    except OSError:
        return "", False
    text, errors = _decode(data)
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


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _scan_children(root: Path) -> tuple[list[Path], list[str]]:
    """(flavor directories, names of other `_*_` directories or symlinks)."""
    try:
        children = list(root.iterdir())
    except OSError:
        return [], []
    flavors: list[Path] = []
    others: list[str] = []
    for child in children:
        name = child.name
        if len(name) < 2 or not (name.startswith("_") and name.endswith("_")):
            continue
        try:
            if _is_link(child):
                others.append(name)
            elif child.is_dir():
                if (child / FLAVOR_INFO).is_file():
                    flavors.append(child)
                else:
                    others.append(name)
        except OSError:
            others.append(name)
    return flavors, sorted(others)


def _flavor(directory: Path, rows: Sequence[BuildInfoRow]) -> tuple[Flavor, bool]:
    product, errors = _read_flavor_product(directory / FLAVOR_INFO)
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
    # Checked first: a FIFO or device named `.build.info` could block a read.
    _probe(root, source)
    try:
        raw = (root / BUILD_INFO).read_bytes()
    except OSError as exc:  # refused, or gone since the check
        reason: Reason = "missing" if isinstance(exc, FileNotFoundError) else "unreadable"
        raise NotAnInstallError(root, source, reason, exc.strerror or str(exc)) from exc
    text, errors = _decode(raw)
    rows = parse_build_info(text).rows
    directories, others = _scan_children(root)
    flavors: list[Flavor] = []
    for directory in directories:
        flavor, flavor_errors = _flavor(directory, rows)
        flavors.append(flavor)
        errors = errors or flavor_errors
    flavors.sort(key=lambda f: f.folder)
    return Install(
        root=root,
        flavors=tuple(flavors),
        products=rows,
        other_dirs=tuple(others),
        raw_build_info=raw,
        decode_errors=errors,
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
