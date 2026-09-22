"""install: find a World of Warcraft install and its flavors (docs/LAB_PLAN.md §6.1, M10-05).

Formats: `docs/LAB_FORMATS.md` §1 (`.build.info`) and §2 (`.flavor.info`),
including the dated amendments at the foot of that file.

Root resolution, first hit wins: the explicit argument, then the
`WOWLAB_WOW_ROOT` environment variable, then the platform defaults
(`default_roots`). An explicit root or a set environment variable is final:
if it has no `.build.info`, `NotAnInstallError` is raised rather than falling
through to a default. Only the defaults are searched. There are no registry
reads and no Battle.net database reads; Linux (Wine, Lutris, Proton) is
reached through the explicit root or the environment variable only.

A directory is an install if it holds a regular file `.build.info`. A flavor
is a child directory whose name starts and ends with `_` and which holds a
regular file `.flavor.info`. The flavor's product code comes from that file
and is joined to the `.build.info` row with the same `Product`. Discovery
never raises on a partial install: a flavor with no matching row, or with an
unreadable `.flavor.info`, is returned with `version=None`, never dropped. The
one error is a root without `.build.info`.

Nothing about a flavor is known here (L6): folder names, product codes,
versions and builds all come from the files on disk.

Reads only (L1): files are opened for reading, directories are listed, and
nothing is created, locked or cached anywhere, inside the install or out.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

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

# The install folder's name under each default parent (LAB_PLAN §6.1). This is
# where Battle.net puts every product; it names no flavor.
_INSTALL_DIR = "World of Warcraft"
_MACOS_PARENTS = ("/Applications",)
_WINDOWS_PARENTS = ("Program Files (x86)", "Program Files")
_SYSTEM_DRIVE = "C:\\"

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


# ─── errors ──────────────────────────────────────────────────────────────────


class InstallError(Exception):
    """Base class for everything this module raises on purpose."""


class NotAnInstallError(InstallError):
    """The chosen root has no `.build.info`, so it is not an install."""

    def __init__(self, root: Path, source: str) -> None:
        self.root = root
        self.source = source  # "argument", "environment" or "default"
        super().__init__(f"{root} (from {source}) is not a WoW install: it has no {BUILD_INFO}")


class InstallNotFoundError(InstallError):
    """No root was given and no platform default holds an install."""

    def __init__(self, searched: Sequence[Path]) -> None:
        self.searched = tuple(searched)
        where = ", ".join(str(p) for p in self.searched) or "no default locations on this platform"
        super().__init__(f"no WoW install found (searched: {where}); pass a root or set {ENV_ROOT}")


# ─── models ──────────────────────────────────────────────────────────────────


class BuildInfoRow(BaseModel):
    """One product row of `.build.info`.

    A known column the header does not have is `None`; an empty cell is `""`.
    Every other column is in `extra`, keyed by its name without the
    `!TYPE:size` suffix, in header order (L4). A row shorter than the header
    is padded with empty cells; cells past the header's end are kept in
    `overflow`.
    """

    model_config = ConfigDict(frozen=True)

    product: str | None
    version: str | None
    build_key: str | None
    branch: str | None
    active: str | None  # the cell as written, e.g. "1"
    tags: str | None  # the cell as written; the `?` characters are literal
    extra: dict[str, str]
    overflow: tuple[str, ...] = ()

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
    product: str  # as found in .flavor.info; "" if that file has no product code
    version: str | None  # from .build.info; None if no row matches
    build: int | None  # last component of `version`, if it is a decimal
    build_key: str | None
    path: Path


class Install(BaseModel):
    """An install root, every flavor found under it, and its `.build.info`."""

    model_config = ConfigDict(frozen=True)

    root: Path
    flavors: tuple[Flavor, ...]  # sorted by folder
    products: tuple[BuildInfoRow, ...]  # `.build.info` rows in file order
    raw_build_info: str  # the file as read (L4); undecodable bytes surrogate-escaped


# ─── parsing ─────────────────────────────────────────────────────────────────


def _decode(data: bytes) -> str:
    # surrogateescape keeps undecodable bytes recoverable: encoding the result
    # with the same handler gives back `data` exactly (L4).
    return data.decode("utf-8", errors="surrogateescape")


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

    Columns are found by header name, never by position, because their set
    and order vary by agent version. An empty text gives no columns and no
    rows. When a name appears twice in the header, the first occurrence is
    the one read; the later ones are kept in `extra` under `name#<index>`.
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
                extra={k: v for k, v in by_name.items() if k not in _KNOWN_COLUMNS},
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


def default_roots(
    platform: str | None = None, drives: Sequence[str] | None = None
) -> tuple[PurePath, ...]:
    """Platform default install locations, in search order (LAB_PLAN §6.1).

    macOS: `/Applications/World of Warcraft`. Windows: `Program Files (x86)`
    then `Program Files`, on the system drive first and then on every other
    drive root. Anything else: none (use the explicit root).

    `drives` defaults to `os.listdrives()` on Windows. Telling fixed drives
    from removable or network ones needs a Win32 call this package does not
    make, so every listed drive is searched.
    """
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return tuple(PurePosixPath(parent, _INSTALL_DIR) for parent in _MACOS_PARENTS)
    if platform != "win32":
        return ()
    if drives is None:
        listdrives = getattr(os, "listdrives", None)
        drives = listdrives() if listdrives is not None else [_SYSTEM_DRIVE]
    ordered: list[str] = [_SYSTEM_DRIVE]
    for drive in drives:
        if drive.upper() not in (d.upper() for d in ordered):
            ordered.append(drive)
    return tuple(
        PureWindowsPath(drive, parent, _INSTALL_DIR)
        for drive in ordered
        for parent in _WINDOWS_PARENTS
    )


def _has_build_info(root: Path) -> bool:
    try:
        return (root / BUILD_INFO).is_file()
    except OSError:
        return False


def _as_path(value: Path | str) -> Path:
    return Path(value).expanduser().absolute()


def _resolve(
    root: Path | str | None,
    environ: Mapping[str, str] | None,
    defaults: Sequence[PurePath | str] | None,
) -> tuple[Path, str]:
    if root is not None and str(root) != "":
        chosen = _as_path(root)
        if not _has_build_info(chosen):
            raise NotAnInstallError(chosen, "argument")
        return chosen, "argument"
    env = os.environ if environ is None else environ
    from_env = env.get(ENV_ROOT, "")
    if from_env.strip():
        chosen = _as_path(from_env)
        if not _has_build_info(chosen):
            raise NotAnInstallError(chosen, "environment")
        return chosen, "environment"
    candidates = [Path(p) for p in (default_roots() if defaults is None else defaults)]
    for candidate in candidates:
        if _has_build_info(candidate):
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
    the argument or the variable names a directory without `.build.info`
    (never falling through to a default), and `InstallNotFoundError` when no
    default holds one.
    """
    return _resolve(root, environ, defaults)[0]


def _read_flavor_product(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            data = handle.read(_FLAVOR_INFO_READ_LIMIT)
    except OSError:
        return ""
    return parse_flavor_info(_decode(data))


def _match_row(rows: Sequence[BuildInfoRow], product: str) -> BuildInfoRow | None:
    """The row for `product`; when several rows name it, the first active one,
    else the first one."""
    if not product:
        return None
    matches = [r for r in rows if (r.product or "").strip() == product]
    if not matches:
        return None
    return next((r for r in matches if r.is_active), matches[0])


def _flavor_dirs(root: Path) -> list[Path]:
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    found: list[Path] = []
    for child in children:
        name = child.name
        if len(name) < 2 or not (name.startswith("_") and name.endswith("_")):
            continue
        try:
            if child.is_dir() and (child / FLAVOR_INFO).is_file():
                found.append(child)
        except OSError:
            continue
    return found


def _flavor(directory: Path, rows: Sequence[BuildInfoRow]) -> Flavor:
    product = _read_flavor_product(directory / FLAVOR_INFO)
    row = _match_row(rows, product)
    version = ((row.version or "").strip() or None) if row is not None else None
    build_key = ((row.build_key or "").strip() or None) if row is not None else None
    return Flavor(
        folder=directory.name,
        product=product,
        version=version,
        build=_build_number(version) if version is not None else None,
        build_key=build_key,
        path=directory,
    )


def _read(root: Path, source: str) -> Install:
    try:
        raw = _decode((root / BUILD_INFO).read_bytes())
    except OSError as exc:  # absent, a directory, unreadable, or gone since the check
        raise NotAnInstallError(root, source) from exc
    rows = parse_build_info(raw).rows
    flavors = sorted((_flavor(d, rows) for d in _flavor_dirs(root)), key=lambda f: f.folder)
    return Install(root=root, flavors=tuple(flavors), products=rows, raw_build_info=raw)


def read_install(root: Path | str) -> Install:
    """Read the install at exactly `root`, with no resolution. Raises
    `NotAnInstallError` if it has no readable `.build.info`; never raises on
    anything else it finds."""
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
