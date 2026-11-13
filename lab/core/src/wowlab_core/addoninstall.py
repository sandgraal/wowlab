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

The installed TOC is the template with that line filled, a `## Version:`
line holding the running wowlab's version added after it, and the
template's own comment lines (`#` but not `##`) dropped, so the copy in the
game says which wowlab installed it and nothing about how it was made
(M16-06). `lab_addon_state` reads the installed TOC back, without following
a link, and compares its `## Interface:` with the one the flavor's
discovered version gives: that is how `wowlab doctor` says the installed
copy is out of date for the client (M16-06).

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

The sources are found from this package, in this order and nowhere else
(M11-02, widened by M13-14 so an installed wowlab can install its addon):

1. a checkout: only when this package's resolved folder is exactly
   `<root>/lab/core/src/wowlab_core` and `<root>` is the workspace root
   (it holds `.git` or a `pyproject.toml` that declares the uv workspace),
   `<root>/lab/addon/WowLab/WowLab.toc`;
2. otherwise the copy bundled in the installed package,
   `wowlab_core/_addon/WowLab/WowLab.toc` (the sdist copies
   `lab/addon/WowLab/` there, and the wheel is built from the sdist), used
   only when the package is a real folder on disk, never a zip import or
   another non-filesystem loader.

Nothing above the package is searched: an installed wowlab under some other
git repository or uv workspace, even one holding a `lab/addon/WowLab/`,
uses its own bundled copy.

A link in `lab`, `addon`, `WowLab` or `_addon` is refused, and each source
file is opened without following a link and checked to be the regular file
its `lstat` saw. Anywhere else `AddonSourceError` says so.
"""

from __future__ import annotations

import os
import stat
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from wowlab_core import __version__, guard, longpath
from wowlab_core.errors import WowlabError
from wowlab_core.toc import parse_toc

__all__ = [
    "ADDON_FOLDER",
    "ADDON_NAME",
    "BUNDLE_PARTS",
    "CHECKOUT_PACKAGE_PARTS",
    "FOLDER_LEFT_NOTE",
    "FOLDER_STAYS_NOTE",
    "INTERFACE_LINE",
    "INTERFACE_PLACEHOLDER",
    "LAB_ADDON",
    "LAB_ADDON_STATUSES",
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
    "VERSION_KEY",
    "AddonChangedError",
    "AddonError",
    "AddonSourceError",
    "AddonVersionError",
    "InstallPlan",
    "LabAddonState",
    "LeftPath",
    "RemovePlan",
    "addon_files",
    "fill_toc",
    "find_source",
    "install",
    "installed_toc",
    "interface_version",
    "lab_addon_state",
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
CHECKOUT_PACKAGE_PARTS = ("lab", "core", "src", "wowlab_core")
"""This package's folder in a checkout, relative to the repository root."""
BUNDLE_PARTS = ("_addon", ADDON_NAME)
"""The copy bundled in an installed package, relative to the `wowlab_core` folder."""
INTERFACE_PLACEHOLDER = "@WOWLAB_INTERFACE@"
INTERFACE_LINE = f"## Interface: {INTERFACE_PLACEHOLDER}"
"""The one TOC line install fills (ADR-0026: the repository holds no number)."""
VERSION_KEY = "Version"
"""The TOC directive install adds, holding the running wowlab's version (M16-06)."""
MAX_ADDON_VERSION_CHARS = 64
"""A wowlab version longer than this is not written into the TOC."""
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


class AddonError(WowlabError):
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
    addon_version: str  # what the installed TOC's ## Version: says (M16-06)
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


LabAddonStatus = Literal["not_installed", "current", "stale", "unknown"]
LAB_ADDON_STATUSES: tuple[LabAddonStatus, ...] = ("not_installed", "current", "stale", "unknown")


class LabAddonState(_Frozen):
    """The installed lab-addon's TOC against the client (M16-06).

    `status`: `not_installed` when the flavor has no `WowLab.toc` in the
    addon folder; `current` when its `## Interface:` lists the interface
    version the flavor's discovered version gives; `stale` when it does not
    (another client version's, or no usable value), which a reinstall
    fixes; `unknown` when either side cannot be known (the flavor's version
    gives no interface version, or the TOC cannot be read), with `detail`
    saying why."""

    status: LabAddonStatus
    # The installed TOC's `## Interface:` values; empty when not read or absent.
    installed: tuple[int, ...] = ()
    # The interface version the flavor's discovered version gives, or None.
    expected: int | None = None
    # The installed TOC's `## Version:` (the wowlab that installed it), if any.
    version: str | None = None
    detail: str | None = None


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
        return stat.S_ISDIR(st.st_mode) and longpath.is_junction(path)
    except OSError:
        return True


def _read_regular(path: Path, limit: int) -> bytes:
    """The bytes of the regular file at `path`, opened without following a
    link, checked by `fstat` to be the file `lstat` saw, at most `limit`.
    A path past Windows' plain limit is looked at and opened by its
    extended-length spelling (`longpath`, M13-44); a message names it plainly."""
    try:
        seen = longpath.lstat(path)
    except OSError as exc:
        raise _UnreadableError(f"cannot be read: {exc}") from exc
    if not stat.S_ISREG(seen.st_mode):
        raise _UnreadableError("is not a regular file")
    if seen.st_size > limit:
        raise _UnreadableError(f"is {seen.st_size} bytes, over {limit}")
    try:
        with longpath.plain_errors():
            fd = os.open(
                longpath.extended(path),
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
        if longpath.lstat(folder / ".git"):
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


def _candidate(folder: Path, parts: Sequence[str] = SOURCE_PARTS) -> Path | None:
    """`folder/<parts>` (default `lab/addon/WowLab`) if it holds a
    `WowLab.toc`, refusing a link in any of its components and a TOC that
    is not a regular file."""
    current = folder
    for part in parts:
        current = current / part
        try:
            st = longpath.lstat(current)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise AddonSourceError(f"cannot look at {current}: {exc}") from exc
        if _is_link(current, st):
            raise AddonSourceError(
                f"{current} is a link (a symlink or junction); the lab-addon's sources are "
                "read only from a real folder"
            )
        if not stat.S_ISDIR(st.st_mode):
            return None
    try:
        st = longpath.lstat(current / TOC_NAME)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AddonSourceError(f"cannot look at {current / TOC_NAME}: {exc}") from exc
    if not stat.S_ISREG(st.st_mode):
        raise AddonSourceError(f"{current / TOC_NAME} is not a regular file")
    return current


def _package() -> Traversable:
    """The installed `wowlab_core` package, as its loader presents it."""
    return resources.files("wowlab_core")


def _checkout_root(package_dir: Path) -> Path | None:
    """The repository root when `package_dir` (resolved) is exactly
    `<root>/lab/core/src/wowlab_core` and `<root>` is the workspace root,
    else None."""
    n = len(CHECKOUT_PACKAGE_PARTS)
    if len(package_dir.parts) <= n or package_dir.parts[-n:] != CHECKOUT_PACKAGE_PARTS:
        return None
    root = package_dir.parents[n - 1]
    return root if _is_workspace_root(root) else None


def find_source(start: Path | None = None, *, package: Traversable | None = None) -> Path:
    """The lab-addon's source folder, from a checkout first, else from the
    copy bundled in the installed package, and from nowhere else.

    `start` is this package's folder (default: the folder of this module).
    It is a checkout only when, resolved, it is exactly
    `<root>/lab/core/src/wowlab_core` and `<root>` holds `.git` or a
    `pyproject.toml` that declares the uv workspace; the sources are then
    `<root>/lab/addon/WowLab/`. Nothing above the package is searched: a
    `lab/addon/WowLab/` in any other folder is never used.

    Otherwise, or when that checkout holds no sources, the bundled copy is
    `_addon/WowLab/` in `package` (default: the installed `wowlab_core`,
    from `importlib.resources`), used only when `package` is a real folder
    on disk (a `pathlib.Path`): a zip import or any other loader is refused.
    The same no-link and regular-file checks apply to it.

    Raises `AddonSourceError` when neither is found."""
    here = (start if start is not None else _HERE).resolve()
    root = _checkout_root(here)
    if root is not None:
        found = _candidate(root)
        if found is not None:
            return found
        missing = (
            f"the checkout {root} holds no lab-addon sources ({'/'.join(SOURCE_PARTS)}/{TOC_NAME})"
        )
    else:
        missing = (
            f"wowlab is not running from a checkout ({here} is not "
            f"{'/'.join(CHECKOUT_PACKAGE_PARTS)} in a folder holding .git or the uv "
            "workspace's pyproject.toml)"
        )
    pkg = _package() if package is None else package
    bundle = "/".join(BUNDLE_PARTS)
    if not isinstance(pkg, Path):
        raise AddonSourceError(
            f"{missing}, and the installed wowlab package is not a real folder on disk "
            f"({type(pkg).__name__}, such as a zip import), so the lab-addon bundled in it "
            f"({bundle}/{TOC_NAME}) is not used; install wowlab as a regular package; "
            "nothing was changed"
        )
    pkg_dir = pkg.resolve()
    bundled = _candidate(pkg_dir, BUNDLE_PARTS)
    if bundled is not None:
        return bundled
    raise AddonSourceError(
        f"{missing}, and no copy is bundled in the installed package ({pkg_dir}/{bundle}/"
        f"{TOC_NAME}), so the lab-addon's sources were not found; `wowlab addon install lab` "
        "runs from a checkout of the wowlab repository (uv run wowlab ...) or from a wowlab "
        "release wheel, or one built from the sdist (`uv build`); nothing was changed"
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
            # True whichever way the version is unknown (M16-121).
            "the flavor's version is unknown, so the TOC's ## Interface: cannot be filled; "
            "nothing was changed"
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


def _addon_version(version: str) -> str:
    """`version` checked to be one line of printable ASCII a TOC can hold."""
    if (
        not version
        or len(version) > MAX_ADDON_VERSION_CHARS
        or not version.isascii()
        or not version.isprintable()
        or version != version.strip()
    ):
        raise AddonSourceError(
            f"the wowlab version {version[:MAX_ADDON_VERSION_CHARS]!r} cannot be written as "
            f"the TOC's ## {VERSION_KEY}:; nothing was changed"
        )
    return version


def _is_comment(line: bytes) -> bool:
    """A TOC comment: `#` but not a `##` directive."""
    text = line.removesuffix(b"\r")
    return text.startswith(b"#") and not text.startswith(b"##")


def _blank(line: bytes) -> bool:
    return not line.removesuffix(b"\r")


def installed_toc(template: bytes, interface: int, version: str) -> bytes:
    """The TOC as install writes it (M16-06): `fill_toc`'s, with the
    template's comment lines (`#` but not `##`) dropped, along with a blank
    line their removal would double, and `## Version: <version>` added after
    the `## Interface:` line, with that line's ending. Every other byte is
    kept."""
    filled = fill_toc(template, interface)
    version = _addon_version(version)
    if parse_toc(template).get(VERSION_KEY) is not None:
        raise AddonSourceError(
            f"{TOC_NAME} already holds a ## {VERSION_KEY}: line; install writes that one"
        )
    wanted = f"## Interface: {interface}".encode("ascii")
    kept: list[bytes] = []
    dropped = False
    for line in filled.split(b"\n"):
        if _is_comment(line):
            dropped = True
            continue
        if dropped and _blank(line) and kept and _blank(kept[-1]):
            dropped = False
            continue  # the blank line after the comment, when one is kept before it
        dropped = False
        kept.append(line)
        if line.removesuffix(b"\r") == wanted:
            ending = b"\r" if line.endswith(b"\r") else b""
            kept.append(f"## {VERSION_KEY}: {version}".encode("ascii") + ending)
    out = b"\n".join(kept)
    doc = parse_toc(out)
    found = doc.interface
    if (
        found is None
        or found.versions != (interface,)
        or not found.ok
        or doc.get(VERSION_KEY) != version
    ):
        raise AddonSourceError(
            f"{TOC_NAME} does not read back with ## Interface: {interface} and "
            f"## {VERSION_KEY}: {version}"
        )
    return out


def addon_files(
    interface: int, source: Path | None = None, *, version: str = __version__
) -> dict[str, bytes]:
    """Every file of the addon as install writes it: flavor-relative path to
    bytes, the TOC first (`installed_toc`: filled with `interface`, with
    `## Version: <version>`, the running wowlab's by default), then the
    `.lua` files in the order the TOC lists them."""
    folder = find_source() if source is None else source
    try:
        names = sorted(e.name for e in longpath.entries(folder))
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
    files = {f"{ADDON_FOLDER}/{TOC_NAME}": installed_toc(template, interface, version)}
    for name in listed:
        files[f"{ADDON_FOLDER}/{name}"] = _read_source(folder / name)
    return files


# ─── what is installed ───────────────────────────────────────────────────────


def _addon_folder(flavor_dir: Path) -> Path | None:
    """The addon folder, or None when it (or a folder on the way) is not
    there. A link or a non-folder on the way raises `AddonError`."""
    current = flavor_dir
    for part in ADDON_FOLDER.split("/"):
        current = current / part
        try:
            st = longpath.lstat(current)  # extended-length past the plain limit (M13-44)
        except FileNotFoundError:
            return None
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
    return current


def _installed(flavor_dir: Path) -> tuple[list[str], list[LeftPath], bool]:
    """The regular files under the addon folder (flavor-relative, sorted),
    the entries left alone, and whether the folder exists. Nothing is
    followed through a link; a link on the way to the folder is refused."""
    found = _addon_folder(flavor_dir)
    if found is None:
        return [], [], False
    current = found
    files: list[str] = []
    left: list[LeftPath] = []
    seen = 0
    pending = [current]
    while pending:
        folder = pending.pop()
        try:
            entries = sorted(longpath.entries(folder), key=lambda e: e.name)
        except OSError as exc:
            raise AddonError(f"cannot list {folder}: {exc}") from exc
        for entry in entries:
            seen += 1
            if seen > MAX_ENTRIES:
                raise AddonError(
                    f"{ADDON_FOLDER} holds more than {MAX_ENTRIES} entries; nothing was changed"
                )
            path = folder / entry.name  # plain; `entry.path` may be extended-length
            rel = path.relative_to(flavor_dir).as_posix()
            try:
                st = longpath.lstat(path)
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
    addon_version = _addon_version(__version__)
    files = addon_files(interface, folder, version=addon_version)
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
        addon_version=addon_version,
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
    files: Mapping[str, bytes] = addon_files(
        plan.interface, Path(plan.source), version=plan.addon_version
    )
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
    link), `STILL_INSTALLED_NOTE`. The error's text is the first refused
    path; each further one, and then the notes, is a note on the exception
    (`add_note`), which the CLI prints one line each (M11-30)."""
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
        error = guard.GuardError(refused[0])
        for line in [*refused[1:], " ".join(notes)]:
            error.add_note(line)
        raise error
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


# ─── the installed copy against the client (M16-06) ──────────────────────────


def _other_tocs(folder: Path) -> list[str]:
    """Every `*.toc` in the addon folder other than `WowLab.toc` (compared
    with case folded, as a case-insensitive file system names it), by name,
    from a listing that follows no link. `OSError` when it cannot be listed."""
    return sorted(
        e.name
        for e in longpath.entries(folder)
        if e.name.casefold().endswith(".toc") and e.name.casefold() != TOC_NAME.casefold()
    )


def _is_suffixed(name: str) -> bool:
    """`WowLab_<suffix>.toc` or `WowLab-<suffix>.toc` (case folded): a TOC
    the client may load instead of `WowLab.toc`, by game type."""
    stem = name.casefold().removesuffix(".toc")
    head = ADDON_NAME.casefold()
    return len(stem) > len(head) + 1 and stem.startswith(head) and stem[len(head)] in "_-"


def _others_detail(others: Sequence[str]) -> str:
    """Why `lab_addon_state` cannot judge the folder with these other TOCs."""
    suffixed = [n for n in others if _is_suffixed(n)]
    rest = [n for n in others if not _is_suffixed(n)]
    parts: list[str] = []
    if suffixed:
        parts.append(f"{', '.join(suffixed)}, which the client may load instead of {TOC_NAME}")
    if rest:
        verb = "is" if len(rest) == 1 else "are"
        parts.append(f"{', '.join(rest)}, which {verb} not wowlab's")
    return f"{ADDON_FOLDER}/ also holds " + "; and ".join(parts)


def lab_addon_state(flavor: _FlavorLike) -> LabAddonState:
    """The installed lab-addon's `## Interface:` against the one the
    flavor's discovered version gives (`interface_version`). Read only (L1):
    the folder is listed and the TOC opened without following a link, and a
    link on the way to it makes the answer `unknown`. So does any other
    `*.toc` in the folder (a suffixed `WowLab_<x>.toc`), which the client may
    load instead of `WowLab.toc` (M16-06, domain review)."""
    expected: int | None
    no_expected: str | None = None
    try:
        expected = interface_version(flavor.version)
    except AddonVersionError as exc:
        expected, no_expected = None, str(exc).removesuffix("; nothing was changed")
    toc = f"{ADDON_FOLDER}/{TOC_NAME}"
    try:
        folder = _addon_folder(flavor.path)
    except AddonError as exc:
        cause = exc.__cause__
        if isinstance(cause, OSError):
            detail = f"wowlab could not look at the way to {toc}: {cause.strerror or cause}"
        else:
            detail = f"the way to {toc} is a link or not a folder, so wowlab did not read it"
        return LabAddonState(status="unknown", expected=expected, detail=detail)
    if folder is None:
        return LabAddonState(status="not_installed", expected=expected)
    try:
        others = _other_tocs(folder)
    except OSError as exc:
        return LabAddonState(
            status="unknown",
            expected=expected,
            detail=f"wowlab could not list {ADDON_FOLDER}/: {exc.strerror or exc}",
        )
    installed: tuple[int, ...] = ()
    version: str | None = None
    try:
        data = _read_regular(folder / TOC_NAME, MAX_SOURCE_BYTES)
    except _UnreadableError as exc:
        if not isinstance(exc.__cause__, FileNotFoundError):
            return LabAddonState(status="unknown", expected=expected, detail=f"{toc} {exc}")
        if not others:
            return LabAddonState(status="not_installed", expected=expected)
    else:
        doc = parse_toc(data)
        installed = doc.interface.versions if doc.interface is not None else ()
        version = doc.get(VERSION_KEY)
    if others:
        return LabAddonState(
            status="unknown",
            installed=installed,
            expected=expected,
            version=version,
            detail=_others_detail(others),
        )
    if expected is None:
        return LabAddonState(
            status="unknown", installed=installed, version=version, detail=no_expected
        )
    return LabAddonState(
        status="current" if expected in installed else "stale",
        installed=installed,
        expected=expected,
        version=version,
    )
