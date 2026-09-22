"""layout: typed, read-only walk of a flavor folder (docs/LAB_PLAN.md §6.2, M10-06).

Input is a flavor folder, usually from `wowlab_core.install` (`Layout.for_flavor`,
`layouts(install)`), and the install root that holds it. Output is the
inventory every later tool starts from: accounts with their realms and
characters, SavedVariables with scope and owning addon, addons with every
TOC, WTF files, and the other areas (caches, logs, screenshots, errors,
fonts, loose overrides under `Interface/`). `classify()` answers what any
path is from the file map (`wowlab_core.filemap`, `docs/LAB_FILE_MAP.md`).

Account folder shapes (LAB_FILE_MAP "Folder shape", GLOSSARY "Second name"):

- `WTF/Account/<ACCOUNT>/<Realm>/<Character>/`: the retail shape. The realm
  folder is the realm's display name.
- `WTF/Account/<ACCOUNT>/<digits>/<First>-<Second>/`: the Forever beta
  shape. A folder made only of ASCII digits is reported as a numeric folder
  (probably the realm's numeric id, **[verify]**), never as a realm name; its character folders are split at the first hyphen into first and
  second name (character names are believed not to contain one,
  **[verify]**). The retail-style `<Realm>/<First>/` twin that holds only
  `AddOns.txt` is an ordinary character folder of the first shape;
  `Character.twins` names the folders of the other shape with the same
  first name, a match by name only, because which realm name goes with which
  numeric folder is unknown.

Reads only (L1): directories are listed, files are `lstat`ed, and TOC files
are opened for reading. Nothing is created anywhere. Symlinks and Windows
junctions are never followed, whether they point inside the install or out;
each is reported in `symlinks` with its target text and whether that target
resolves inside the install, and is left out of the typed lists. Walks are
bounded in depth and entry count (`Limits`); a walk that hits a bound says so
in `truncated`. Nothing about a flavor is known here (L6): the folder comes
from the caller or from discovery.

Strings that come from file names are kept exactly as the operating system
returns them, which on POSIX can include lone surrogates for bytes that are
not UTF-8. JSON cannot carry those, so in JSON every such code point (and a
literal NUL) is written as NUL followed by four hex digits, the scheme
`snapshot` uses, and read back the same way:
`Inventory.model_validate_json(inv.model_dump_json()) == inv`.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    PlainSerializer,
    ValidationInfo,
)

from wowlab_core import filemap as _filemap
from wowlab_core.filemap import FileMapEntry
from wowlab_core.install import Flavor, Install
from wowlab_core.toc import MAX_TOC_BYTES, TocDocument, TocTooLargeError, read_toc

__all__ = [
    "Account",
    "Addon",
    "AddonToc",
    "Area",
    "Character",
    "Classified",
    "Inventory",
    "Layout",
    "Limits",
    "Other",
    "Override",
    "Realm",
    "SavedVariablesFile",
    "Symlink",
    "Unclassified",
    "WalkError",
    "WtfFile",
    "classify",
    "layouts",
]

# Client-written folder and file names inside a flavor folder, looked up
# case-insensitively. None of them names a flavor (L6).
_WTF = "WTF"
_ACCOUNT = "Account"
_SAVED_VARIABLES = "SavedVariables"
_OS_METADATA = "os-metadata"  # file-map entry id
_BLIZZARD_SV = "SavedVariables.lua"
_INTERFACE = "Interface"
_ADDONS = "AddOns"
_FONTS = "Fonts"
_SV_SUFFIX = ".lua"
_BAK_SUFFIX = ".lua.bak"
_TOC_SUFFIX = ".toc"
_BLIZZARD_ADDON_PREFIX = "Blizzard_"
# Areas reported by `other()` as counts (LAB_PLAN §6.2), in this order.
_AREAS = (
    "Cache",
    "Logs",
    "Screenshots",
    "Errors",
    _FONTS,
    "BlizzardInterfaceCode",
    "BlizzardInterfaceArt",
)


# ─── JSON-safe strings from file names ──────────────────────────────────────

_NEEDS_ESCAPE = re.compile("[\x00\ud800-\udfff]")
_ESCAPED = re.compile("\x00([0-9A-F]{4})")


def _escape(text: str) -> str:
    return _NEEDS_ESCAPE.sub(lambda m: f"\x00{ord(m.group()):04X}", text)


def _unescape(text: str) -> str:
    if "\x00" not in text:
        return text
    restored = _ESCAPED.sub(lambda m: chr(int(m.group(1), 16)), text)
    if _escape(restored) != text:
        raise ValueError("malformed NUL escape in a file-name string")
    return restored


def _from_json_text(value: Any, info: ValidationInfo) -> Any:
    return _unescape(value) if info.mode == "json" and isinstance(value, str) else value


# A string taken from a file name: lone surrogates allowed, escaped in JSON.
FsText = Annotated[
    str,
    BeforeValidator(_from_json_text),
    PlainSerializer(_escape, when_used="json"),
]
FsPath = Annotated[
    Path,
    BeforeValidator(_from_json_text),
    PlainSerializer(lambda p: _escape(str(p)), return_type=str, when_used="json"),
]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ─── models ──────────────────────────────────────────────────────────────────


class Limits(_Model):
    """Bounds on every walk. `max_depth` counts folders below the walk's start."""

    max_depth: int = 24
    max_entries: int = 1_000_000
    max_toc_bytes: int = MAX_TOC_BYTES


class Symlink(_Model):
    """A symlink or junction met by a walk: reported, never followed."""

    path: FsText  # relative to the flavor folder, `/`-separated
    target: FsText | None  # the link text as stored; None if it cannot be read
    inside_install: bool  # whether the target resolves inside the install root
    entry_id: str | None  # file-map entry for the link's own location


class WalkError(_Model):
    """A folder that could not be listed or an entry that could not be `lstat`ed."""

    path: FsText
    error: str


class Character(_Model):
    folder: FsText  # as found on disk
    path: FsText  # relative to the flavor folder
    realm_folder: FsText
    # `realm_name`: under a realm-name folder (`<Realm>/<Character>/`);
    # `numeric_folder`: under a digits-only folder (`<digits>/<First>-<Second>/`),
    # probably the realm's numeric id **[verify]**; not asserted to be a realm.
    shape: Literal["realm_name", "numeric_folder"]
    first_name: FsText  # the folder name, or the part before the first hyphen
    second_name: FsText | None  # after the first hyphen, numeric_folder shape only
    files: tuple[FsText, ...]  # regular files directly inside, sorted
    folders: tuple[FsText, ...]  # sub-folders, sorted (e.g. "SavedVariables")
    # Character folders of the other shape in the same account whose first
    # name matches (compared with case folded): a name match, not proof.
    twins: tuple[FsText, ...]


class Realm(_Model):
    folder: FsText
    path: FsText
    # `name`: a realm display name; `numeric`: a digits-only folder, probably
    # the realm's numeric id **[verify]**; never read as a realm name.
    kind: Literal["name", "numeric"]
    characters: tuple[Character, ...]


class Account(_Model):
    folder: FsText  # the account folder name (personal data; scrubbed in fixtures)
    path: FsText
    files: tuple[FsText, ...]  # regular files directly inside, sorted
    has_saved_variables: bool  # whether `SavedVariables/` exists
    realms: tuple[Realm, ...]


class SavedVariablesFile(_Model):
    path: FsText
    scope: Literal["account", "character"]
    # The owning addon, from the file name (`<Addon>.lua`, `<Addon>.lua.bak`);
    # None for Blizzard's own `SavedVariables.lua`.
    addon: FsText | None
    blizzard: bool  # Blizzard UI's own `SavedVariables.lua` (or its `.bak`)
    backup: bool  # a `.lua.bak` sibling, the previous write
    account: FsText
    realm_folder: FsText | None
    character_folder: FsText | None
    size: int
    mtime_ns: int
    entry_id: str | None


class WtfFile(_Model):
    """A regular file under `WTF/` other than SavedVariables."""

    path: FsText
    name: FsText
    # `machine`: directly in `WTF/`; `account`: directly in an account
    # folder; `character`: directly in a character folder; `other`: anywhere
    # else under `WTF/`.
    scope: Literal["machine", "account", "character", "other"]
    account: FsText | None
    realm_folder: FsText | None
    character_folder: FsText | None
    size: int
    mtime_ns: int
    entry_id: str | None


class AddonToc(_Model):
    file: FsText  # file name inside the addon folder
    # What follows the folder name and one `_` or `-` in the file stem
    # (`Foo_Mainline.toc` -> "Mainline"); None for `<Folder>.toc` and for a
    # TOC whose stem does not start with the folder name.
    suffix: FsText | None
    # Whether the stem is the folder name, optionally with a suffix. The
    # client loads only TOCs named after the folder.
    matches_folder: bool
    size: int
    document: TocDocument | None  # None when it could not be read
    error: str | None


class Addon(_Model):
    name: FsText  # the folder name
    path: FsText
    # A folder named like one of Blizzard's own addons (`Blizzard_*`). The
    # client loads its own addons from game data, not from here **[verify]**;
    # an interface export writes under `BlizzardInterfaceCode/` instead
    # **[verify]**, so one here was most likely copied in.
    blizzard: bool
    tocs: tuple[AddonToc, ...]  # every `*.toc` directly inside, sorted by file name
    # The TOC the client would read, if it loads the addon at all, when that
    # follows from the file names alone: the one `<Folder>.toc` when no
    # suffixed TOC is present. With suffixed TOCs the choice depends on the
    # flavor's game type, which is not encoded here (Forever's preferred
    # suffix is **[verify]**).
    selected_toc: FsText | None
    selection: Literal["single", "depends_on_game_type", "no_toc"]


class Override(_Model):
    """A loose file under `Interface/` outside `AddOns/`, or under `Fonts/`."""

    path: FsText
    area: Literal["interface", "fonts"]
    size: int
    mtime_ns: int
    entry_id: str | None


class Area(_Model):
    """One top-level area of the flavor folder, summarised."""

    name: str
    path: FsText | None  # as found on disk; None when absent
    present: bool
    symlink: bool  # the area folder itself is a symlink (not walked)
    files: int
    folders: int
    bytes: int
    truncated: bool


class Other(_Model):
    areas: tuple[Area, ...]
    overrides: tuple[Override, ...]


class Inventory(_Model):
    install_root: FsPath
    flavor_path: FsPath
    flavor_folder: FsText
    accounts: tuple[Account, ...]
    saved_variables: tuple[SavedVariablesFile, ...]
    addons: tuple[Addon, ...]
    wtf_files: tuple[WtfFile, ...]
    other: Other
    symlinks: tuple[Symlink, ...]
    # Files the file map classifies `os-metadata` (`.DS_Store`, `._*`, …):
    # listed here and kept out of every other list and count.
    os_metadata: tuple[FsText, ...]
    errors: tuple[WalkError, ...]
    truncated: bool


class Classified(_Model):
    """A path and its file-map entry."""

    status: Literal["classified"] = "classified"
    path: FsText  # relative to `base`, `/`-separated; "" is the base itself
    base: Literal["root", "flavor"]
    flavor_folder: FsText | None
    entry: FileMapEntry


class Unclassified(_Model):
    """A path the file map has no row for, or that is not inside the install."""

    status: Literal["unclassified"] = "unclassified"
    path: FsText
    base: Literal["root", "flavor"] | None
    flavor_folder: FsText | None
    reason: str


# ─── walking ─────────────────────────────────────────────────────────────────


@dataclass
class _Entry:
    rel: tuple[str, ...]  # relative to the flavor folder
    is_dir: bool
    size: int
    mtime_ns: int


@dataclass
class _Report:
    symlinks: list[Symlink] = field(default_factory=list)
    errors: list[WalkError] = field(default_factory=list)
    os_metadata: list[str] = field(default_factory=list)
    truncated: bool = False
    entries: int = 0


def _rel_text(rel: Iterable[str]) -> str:
    return "/".join(rel)


def _is_link(path: Path, st: os.stat_result) -> bool:
    if stat.S_ISLNK(st.st_mode):
        return True
    try:
        return path.is_junction()
    except OSError:
        return False


def _is_within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


class Layout:
    """A read-only view of one flavor folder.

    `flavor_path` is the flavor folder; `install_root` defaults to its
    parent (a flavor folder is a child of the install root). Every method
    reads the disk afresh; nothing is cached between calls.
    """

    def __init__(
        self,
        flavor_path: Path | str,
        *,
        install_root: Path | str | None = None,
        limits: Limits | None = None,
    ) -> None:
        self.flavor_path = Path(flavor_path).absolute()
        self.install_root = (
            Path(install_root).absolute() if install_root is not None else self.flavor_path.parent
        )
        if not _is_within(self.flavor_path, self.install_root):
            raise ValueError(f"{self.flavor_path} is not inside {self.install_root}")
        self.limits = limits if limits is not None else Limits()
        self._filemap = _filemap.load()
        self._os_entry = self._filemap.entry(_OS_METADATA)
        self._real_root: Path | None = None

    @classmethod
    def for_flavor(
        cls, flavor: Flavor, install_root: Path | str | None = None, limits: Limits | None = None
    ) -> Layout:
        """A layout for a discovered flavor (`install.Flavor`)."""
        return cls(flavor.path, install_root=install_root, limits=limits)

    @property
    def flavor_folder(self) -> str:
        return self.flavor_path.name

    # ── low-level reads ────────────────────────────────────────────────────

    def _abs(self, rel: Iterable[str]) -> Path:
        return self.flavor_path.joinpath(*rel)

    def _children(self, rel: tuple[str, ...], report: _Report) -> list[os.DirEntry[str]]:
        try:
            with os.scandir(self._abs(rel)) as it:
                return sorted(it, key=lambda e: e.name)
        except FileNotFoundError:
            return []
        except OSError as exc:
            report.errors.append(WalkError(path=_rel_text(rel), error=exc.strerror or str(exc)))
            return []

    def _child(self, rel: tuple[str, ...], name: str, report: _Report) -> str | None:
        """The on-disk spelling of `name` in folder `rel`, compared with case folded."""
        wanted = name.casefold()
        for entry in self._children(rel, report):
            if entry.name.casefold() == wanted:
                return entry.name
        return None

    def _symlink(self, rel: tuple[str, ...]) -> Symlink:
        path = self._abs(rel)
        try:  # the link text exactly as stored, not normalised by pathlib
            target: str | None = os.readlink(path)  # noqa: PTH115
        except (OSError, ValueError):
            target = None
        try:
            if self._real_root is None:
                self._real_root = self.install_root.resolve()
            inside = _is_within(path.resolve(), self._real_root)
        except (OSError, RuntimeError):
            inside = False
        found = self._filemap.classify(rel, "flavor", "any")
        return Symlink(
            path=_rel_text(rel),
            target=target,
            inside_install=inside,
            entry_id=found.id if found else None,
        )

    def _stat(
        self, rel: tuple[str, ...], entry: os.DirEntry[str], report: _Report
    ) -> _Entry | None:
        """`lstat` one entry; symlinks and junctions are reported and dropped."""
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as exc:
            report.errors.append(WalkError(path=_rel_text(rel), error=exc.strerror or str(exc)))
            return None
        if _is_link(Path(entry.path), st):
            report.symlinks.append(self._symlink(rel))
            return None
        if stat.S_ISDIR(st.st_mode):
            return _Entry(rel, True, 0, st.st_mtime_ns)
        if stat.S_ISREG(st.st_mode):
            return _Entry(rel, False, st.st_size, st.st_mtime_ns)
        return None  # FIFOs, sockets, devices: never opened, not inventoried

    def _list(self, rel: tuple[str, ...], report: _Report) -> list[_Entry]:
        """One folder's entries (no recursion), counted against the bound."""
        out: list[_Entry] = []
        for child in self._children(rel, report):
            if report.entries >= self.limits.max_entries:
                report.truncated = True
                break
            report.entries += 1
            found = self._stat((*rel, child.name), child, report)
            if found is not None:
                out.append(found)
        return out

    def _walk(self, top: tuple[str, ...], report: _Report) -> Iterator[_Entry]:
        """Depth-first, sorted, bounded walk below `top` (not including it)."""
        stack: list[tuple[tuple[str, ...], int]] = [(top, 0)]
        while stack:
            rel, depth = stack.pop()
            entries = self._list(rel, report)
            subdirs: list[tuple[str, ...]] = []
            for e in entries:
                yield e
                if e.is_dir:
                    subdirs.append(e.rel)
            if subdirs and depth >= self.limits.max_depth:
                report.truncated = True
                continue
            stack.extend((d, depth + 1) for d in reversed(subdirs))

    def _top(self, name: str, report: _Report) -> tuple[str, ...] | None:
        """A top-level folder of the flavor, if it is a real folder (not a link)."""
        return self._sub((), name, report)

    def _sub(self, rel: tuple[str, ...], name: str, report: _Report) -> tuple[str, ...] | None:
        """A sub-folder of `rel`, if it is a real folder (not a link)."""
        found = self._child(rel, name, report)
        if found is None:
            return None
        path = self._abs((*rel, found))
        try:
            st = path.lstat()
        except OSError as exc:
            report.errors.append(
                WalkError(path=_rel_text((*rel, found)), error=exc.strerror or str(exc))
            )
            return None
        if _is_link(path, st):
            report.symlinks.append(self._symlink((*rel, found)))
            return None
        return (*rel, found) if stat.S_ISDIR(st.st_mode) else None

    def _entry_id(self, rel: tuple[str, ...], is_dir: bool) -> str | None:
        found = self._filemap.classify(rel, "flavor", "dir" if is_dir else "file")
        return found.id if found else None

    def _os_metadata(self, e: _Entry, report: _Report) -> bool:
        """True (and recorded) when a file is operating-system metadata."""
        if e.is_dir or not self._os_entry.matches(e.rel, "file"):
            return False
        report.os_metadata.append(_rel_text(e.rel))
        return True

    # ── WTF ────────────────────────────────────────────────────────────────

    def _scan_wtf(
        self, report: _Report
    ) -> tuple[list[Account], list[SavedVariablesFile], list[WtfFile]]:
        accounts: list[Account] = []
        svs: list[SavedVariablesFile] = []
        wtf_files: list[WtfFile] = []
        wtf = self._top(_WTF, report)
        if wtf is None:
            return accounts, svs, wtf_files
        account_root = self._sub(wtf, _ACCOUNT, report)
        entries = [e for e in self._walk(wtf, report) if not self._os_metadata(e, report)]
        by_folder: dict[tuple[str, ...], list[_Entry]] = {}
        for e in entries:
            by_folder.setdefault(e.rel[:-1], []).append(e)

        def files_in(rel: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(e.rel[-1] for e in by_folder.get(rel, []) if not e.is_dir)

        def dirs_in(rel: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(e.rel[-1] for e in by_folder.get(rel, []) if e.is_dir)

        n_wtf = len(wtf)
        for e in entries:
            if e.is_dir:
                continue
            sub = e.rel[n_wtf:]
            sv = self._as_saved_variables(e, sub, account_root is not None)
            if sv is not None:
                svs.append(sv)
                continue
            wtf_files.append(self._as_wtf_file(e, sub, account_root is not None))

        if account_root is not None:
            for account_name in dirs_in(account_root):
                arel = (*account_root, account_name)
                realms: list[Realm] = []
                for realm_name in dirs_in(arel):
                    if realm_name.casefold() == _SAVED_VARIABLES.casefold():
                        continue
                    rrel = (*arel, realm_name)
                    numeric = realm_name.isascii() and realm_name.isdigit()
                    characters = []
                    for char_name in dirs_in(rrel):
                        if char_name.casefold() == _SAVED_VARIABLES.casefold():
                            continue  # not a character (review probe, M10-06)
                        crel = (*rrel, char_name)
                        first, sep, second = char_name.partition("-")
                        characters.append(
                            Character(
                                folder=char_name,
                                path=_rel_text(crel),
                                realm_folder=realm_name,
                                shape="numeric_folder" if numeric else "realm_name",
                                first_name=first if numeric and sep else char_name,
                                second_name=second if numeric and sep else None,
                                files=files_in(crel),
                                folders=dirs_in(crel),
                                twins=(),
                            )
                        )
                    realms.append(
                        Realm(
                            folder=realm_name,
                            path=_rel_text(rrel),
                            kind="numeric" if numeric else "name",
                            characters=tuple(characters),
                        )
                    )
                accounts.append(
                    Account(
                        folder=account_name,
                        path=_rel_text(arel),
                        files=files_in(arel),
                        has_saved_variables=any(
                            d.casefold() == _SAVED_VARIABLES.casefold() for d in dirs_in(arel)
                        ),
                        realms=_with_twins(realms),
                    )
                )
        return accounts, svs, wtf_files

    def _as_saved_variables(
        self, e: _Entry, sub: tuple[str, ...], has_accounts: bool
    ) -> SavedVariablesFile | None:
        """`sub` is relative to `WTF/`: `Account/<A>/…`."""
        if not has_accounts or len(sub) < 3 or sub[0].casefold() != _ACCOUNT.casefold():
            return None
        name = sub[-1]
        lower = name.casefold()
        account = sub[1]
        if len(sub) == 3 and lower in (_BLIZZARD_SV.casefold(), (_BLIZZARD_SV + ".bak").casefold()):
            return SavedVariablesFile(
                path=_rel_text(e.rel),
                scope="account",
                addon=None,
                blizzard=True,
                backup=lower.endswith(".bak"),
                account=account,
                realm_folder=None,
                character_folder=None,
                size=e.size,
                mtime_ns=e.mtime_ns,
                entry_id=self._entry_id(e.rel, False),
            )
        if sub[-2].casefold() != _SAVED_VARIABLES.casefold() or len(sub) not in (4, 6):
            return None
        if lower.endswith(_BAK_SUFFIX.casefold()):
            addon, backup = name[: -len(_BAK_SUFFIX)], True
        elif lower.endswith(_SV_SUFFIX):
            addon, backup = name[: -len(_SV_SUFFIX)], False
        else:
            return None
        if not addon:
            return None
        character = len(sub) == 6
        return SavedVariablesFile(
            path=_rel_text(e.rel),
            scope="character" if character else "account",
            addon=addon,
            blizzard=False,
            backup=backup,
            account=account,
            realm_folder=sub[2] if character else None,
            character_folder=sub[3] if character else None,
            size=e.size,
            mtime_ns=e.mtime_ns,
            entry_id=self._entry_id(e.rel, False),
        )

    def _as_wtf_file(self, e: _Entry, sub: tuple[str, ...], has_accounts: bool) -> WtfFile:
        in_accounts = has_accounts and len(sub) >= 3 and sub[0].casefold() == _ACCOUNT.casefold()
        account = realm = character = None
        scope: Literal["machine", "account", "character", "other"]
        if len(sub) == 1:
            scope = "machine"
        elif in_accounts and len(sub) == 3:
            scope, account = "account", sub[1]
        elif (
            in_accounts
            and len(sub) == 5
            and _SAVED_VARIABLES.casefold()
            not in (
                sub[2].casefold(),
                sub[3].casefold(),
            )
        ):
            scope, account, realm, character = "character", sub[1], sub[2], sub[3]
        else:
            scope = "other"
            if in_accounts:
                account = sub[1]
        return WtfFile(
            path=_rel_text(e.rel),
            name=sub[-1],
            scope=scope,
            account=account,
            realm_folder=realm,
            character_folder=character,
            size=e.size,
            mtime_ns=e.mtime_ns,
            entry_id=self._entry_id(e.rel, False),
        )

    # ── Interface ──────────────────────────────────────────────────────────

    def _scan_addons(self, report: _Report) -> list[Addon]:
        interface = self._top(_INTERFACE, report)
        if interface is None:
            return []
        addons_rel = self._sub(interface, _ADDONS, report)
        if addons_rel is None:
            return []
        out: list[Addon] = []
        for folder in self._list(addons_rel, report):
            self._os_metadata(folder, report)  # e.g. `Interface/AddOns/.DS_Store`
            if folder.is_dir:
                out.append(self._addon(folder.rel, report))
        return out

    def _addon(self, rel: tuple[str, ...], report: _Report) -> Addon:
        name = rel[-1]
        folded = name.casefold()
        tocs: list[AddonToc] = []
        for e in self._list(rel, report):
            fname = e.rel[-1]
            if self._os_metadata(e, report):
                continue  # e.g. an AppleDouble `._Foo.toc` is not a TOC
            if e.is_dir or not fname.casefold().endswith(_TOC_SUFFIX):
                continue
            stem = fname[: -len(_TOC_SUFFIX)]
            suffix: str | None = None
            head, sep, rest = (
                stem[: len(name)],
                stem[len(name) : len(name) + 1],
                stem[len(name) + 1 :],
            )
            if stem.casefold() == folded:
                matches = True
            elif head.casefold() == folded and sep in ("_", "-") and rest:
                matches, suffix = True, rest
            else:
                matches = False
            document: TocDocument | None = None
            error: str | None = None
            try:
                document = read_toc(self._abs(e.rel), max_bytes=self.limits.max_toc_bytes)
            except TocTooLargeError:
                error = f"larger than {self.limits.max_toc_bytes} bytes; not read"
            except OSError as exc:
                error = exc.strerror or str(exc)
            tocs.append(
                AddonToc(
                    file=fname,
                    suffix=suffix,
                    matches_folder=matches,
                    size=e.size,
                    document=document,
                    error=error,
                )
            )
        candidates = [t for t in tocs if t.matches_folder]
        plain = [t for t in candidates if t.suffix is None]
        selection: Literal["single", "depends_on_game_type", "no_toc"]
        if not candidates:
            selection, selected = "no_toc", None
        elif len(candidates) == 1 and plain:
            selection, selected = "single", plain[0].file
        else:
            selection, selected = "depends_on_game_type", None
        return Addon(
            name=name,
            path=_rel_text(rel),
            blizzard=folded.startswith(_BLIZZARD_ADDON_PREFIX.casefold()),
            tocs=tuple(tocs),
            selected_toc=selected,
            selection=selection,
        )

    def _scan_other(self, report: _Report) -> Other:
        areas: list[Area] = []
        overrides: list[Override] = []
        for name in _AREAS:
            found = self._child((), name, report)
            if found is None:
                areas.append(
                    Area(
                        name=name,
                        path=None,
                        present=False,
                        symlink=False,
                        files=0,
                        folders=0,
                        bytes=0,
                        truncated=False,
                    )
                )
                continue
            top = self._top(name, report)
            if top is None:  # a symlink (reported) or not a folder
                is_link = any(s.path == found for s in report.symlinks)
                areas.append(
                    Area(
                        name=name,
                        path=found,
                        present=True,
                        symlink=is_link,
                        files=0,
                        folders=0,
                        bytes=0,
                        truncated=False,
                    )
                )
                continue
            sub_report = _Report(entries=report.entries)
            files = folders = size = 0
            for e in self._walk(top, sub_report):
                if self._os_metadata(e, sub_report):
                    continue
                if e.is_dir:
                    folders += 1
                else:
                    files += 1
                    size += e.size
                    if name == _FONTS:
                        overrides.append(self._override(e, "fonts"))
            report.entries = sub_report.entries
            report.symlinks.extend(sub_report.symlinks)
            report.os_metadata.extend(sub_report.os_metadata)
            report.errors.extend(sub_report.errors)
            report.truncated = report.truncated or sub_report.truncated
            areas.append(
                Area(
                    name=name,
                    path=found,
                    present=True,
                    symlink=False,
                    files=files,
                    folders=folders,
                    bytes=size,
                    truncated=sub_report.truncated,
                )
            )
        interface = self._top(_INTERFACE, report)
        if interface is not None:
            for child in self._list(interface, report):
                if child.rel[-1].casefold() == _ADDONS.casefold():
                    continue
                if self._os_metadata(child, report):
                    continue
                if not child.is_dir:
                    overrides.append(self._override(child, "interface"))
                    continue
                for e in self._walk(child.rel, report):
                    if not e.is_dir and not self._os_metadata(e, report):
                        overrides.append(self._override(e, "interface"))
        return Other(areas=tuple(areas), overrides=tuple(overrides))

    def _override(self, e: _Entry, area: Literal["interface", "fonts"]) -> Override:
        return Override(
            path=_rel_text(e.rel),
            area=area,
            size=e.size,
            mtime_ns=e.mtime_ns,
            entry_id=self._entry_id(e.rel, False),
        )

    # ── public ─────────────────────────────────────────────────────────────

    def accounts(self) -> tuple[Account, ...]:
        """Account folders under `WTF/Account/`, each with realms and characters."""
        return tuple(self._scan_wtf(_Report())[0])

    def saved_variables(
        self, scope: Literal["account", "character"] | None = None
    ) -> tuple[SavedVariablesFile, ...]:
        """Every SavedVariables file, optionally of one scope, including
        Blizzard's `SavedVariables.lua` and `.lua.bak` siblings (flagged)."""
        found = self._scan_wtf(_Report())[1]
        return tuple(sv for sv in found if scope is None or sv.scope == scope)

    def wtf_files(self) -> tuple[WtfFile, ...]:
        """Every regular file under `WTF/` that is not a SavedVariables file."""
        return tuple(self._scan_wtf(_Report())[2])

    def addons(self) -> tuple[Addon, ...]:
        """Every folder under `Interface/AddOns/`, with every TOC parsed."""
        return tuple(self._scan_addons(_Report()))

    def other(self) -> Other:
        """Caches, logs, screenshots, errors, fonts, interface exports, and
        loose overrides under `Interface/` outside `AddOns/` and in `Fonts/`."""
        return self._scan_other(_Report())

    def inventory(self) -> Inventory:
        """Everything above in one pass, with every symlink met, every read
        error, and whether a bound was hit."""
        report = _Report()
        for e in self._list((), report):  # OS metadata at the flavor root
            self._os_metadata(e, report)
        accounts, svs, wtf_files = self._scan_wtf(report)
        addons = self._scan_addons(report)
        other = self._scan_other(report)
        seen: set[str] = set()
        symlinks = []
        for s in report.symlinks:
            if s.path not in seen:
                seen.add(s.path)
                symlinks.append(s)
        return Inventory(
            install_root=self.install_root,
            flavor_path=self.flavor_path,
            flavor_folder=self.flavor_folder,
            accounts=tuple(accounts),
            saved_variables=tuple(svs),
            addons=tuple(addons),
            wtf_files=tuple(wtf_files),
            other=other,
            symlinks=tuple(sorted(symlinks, key=lambda s: s.path)),
            os_metadata=tuple(sorted(set(report.os_metadata))),
            errors=tuple(report.errors),
            truncated=report.truncated,
        )

    def classify(
        self, path: Path | str, *, is_dir: bool | None = None
    ) -> Classified | Unclassified:
        """The file-map entry for `path`: absolute, or relative to the flavor
        folder. A path inside the install root but outside this flavor is
        matched against the install-root rows."""
        p = Path(path)
        absolute = p if p.is_absolute() else self.flavor_path / p
        return _classify(absolute, self.install_root, {self.flavor_folder}, _dir_hint(path, is_dir))


def _with_twins(realms: list[Realm]) -> tuple[Realm, ...]:
    """Fill `Character.twins`: folders of the other shape whose first names
    match with case folded (LAB_FILE_MAP "Folder shape"; a name match only)."""
    by_first: dict[tuple[str, str], list[str]] = {}
    for realm in realms:
        for c in realm.characters:
            by_first.setdefault((c.shape, c.first_name.casefold()), []).append(c.path)
    out = []
    for realm in realms:
        chars = []
        for c in realm.characters:
            other = "realm_name" if c.shape == "numeric_folder" else "numeric_folder"
            twins = tuple(sorted(by_first.get((other, c.first_name.casefold()), [])))
            chars.append(c.model_copy(update={"twins": twins}))
        out.append(realm.model_copy(update={"characters": tuple(chars)}))
    return tuple(out)


def _normalize(path: Path) -> Path:
    """Absolute, with `.` and `..` resolved lexically (symlinks untouched)."""
    parts: list[str] = []
    anchor = path.anchor
    for part in path.absolute().parts[1 if anchor else 0 :]:
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return Path(anchor, *parts) if anchor else Path(*parts)


def _dir_hint(path: Path | str | PurePath, is_dir: bool | None) -> bool | None:
    """`is_dir`, or True when the caller's path text ends in a separator."""
    if is_dir is None and isinstance(path, str) and path.endswith(("/", os.sep)):
        return True
    return is_dir


def _kind(path: Path, is_dir: bool | None) -> _filemap.Kind:
    if is_dir is not None:
        return "dir" if is_dir else "file"
    try:
        st = path.lstat()
    except OSError:
        return "any"
    if stat.S_ISDIR(st.st_mode) and not _is_link(path, st):
        return "dir"
    if stat.S_ISREG(st.st_mode):
        return "file"
    return "any"  # a symlink, never followed to find out, or something else


def _same_file(candidate: Path, known: Path) -> bool:
    """Whether `candidate` is the folder `known` itself, never through a link.

    Compares `lstat` results (`os.path.samestat`), and refuses a candidate
    that is a symlink or junction, so a case-only spelling is accepted on a
    case-insensitive volume (same entry) and a link is never followed.
    """
    try:
        st = candidate.lstat()
        if _is_link(candidate, st):
            return False
        return os.path.samestat(st, known.lstat())
    except OSError:
        return False


def _relative_parts(target: Path, root: Path) -> tuple[str, ...] | None:
    """`target`'s parts below `root`, or None when it is not inside.

    An exact prefix is inside. A prefix that differs only in case is inside
    when it is the same folder on disk (a case-insensitive volume); on a
    case-sensitive volume the two spellings are different folders.
    """
    if _is_within(target, root):
        return target.relative_to(root).parts
    t_parts, r_parts = target.parts, root.parts
    if len(t_parts) < len(r_parts):
        return None
    if any(a.casefold() != b.casefold() for a, b in zip(t_parts, r_parts, strict=False)):
        return None
    if not _same_file(Path(*t_parts[: len(r_parts)]), root):
        return None
    return t_parts[len(r_parts) :]


def _flavor_spelling(first: str, root: Path, flavor_folders: set[str]) -> str | None:
    """The discovered spelling of the flavor folder `first` names, if any:
    the same string, or one differing only in case that is the same folder
    on disk."""
    if first in flavor_folders:
        return first
    for folder in sorted(flavor_folders):
        if folder.casefold() == first.casefold() and _same_file(root / first, root / folder):
            return folder
    return None


def _classify(
    path: Path, root: Path, flavor_folders: set[str], is_dir: bool | None
) -> Classified | Unclassified:
    target = _normalize(path)
    root = _normalize(root)
    parts = _relative_parts(target, root)
    if parts is None:
        return Unclassified(
            path=str(target), base=None, flavor_folder=None, reason="not inside the install root"
        )
    rel = parts
    if not rel:
        return Unclassified(
            path="", base="root", flavor_folder=None, reason="the install root itself"
        )
    kind = _kind(target, is_dir)
    fm = _filemap.load()
    flavor = _flavor_spelling(rel[0], root, flavor_folders)
    if flavor is not None:
        sub = rel[1:]
        found = fm.classify(sub, "flavor", kind)
        if found is not None:
            return Classified(path=_rel_text(sub), base="flavor", flavor_folder=flavor, entry=found)
        return Unclassified(
            path=_rel_text(sub),
            base="flavor",
            flavor_folder=flavor,
            reason="no file-map row matches",
        )
    found = fm.classify(rel, "root", kind)
    if found is not None:
        return Classified(path=_rel_text(rel), base="root", flavor_folder=None, entry=found)
    return Unclassified(
        path=_rel_text(rel), base="root", flavor_folder=None, reason="no file-map row matches"
    )


def classify(
    path: Path | str | PurePath, install: Install, *, is_dir: bool | None = None
) -> Classified | Unclassified:
    """The file-map entry for any path inside `install`: absolute, or relative
    to the install root. Flavor folders are the ones discovery reported
    (`install.flavors`), never recognised by name (L6). `is_dir` overrides the
    `lstat` used to tell a folder from a file; a path that does not exist,
    or is a symlink, matches rows of either kind unless it ends in a
    separator."""
    p = Path(path)
    absolute = p if p.is_absolute() else Path(install.root) / p
    folders = {f.folder for f in install.flavors}
    return _classify(absolute, Path(install.root), folders, _dir_hint(path, is_dir))


def layouts(install: Install, limits: Limits | None = None) -> tuple[Layout, ...]:
    """One `Layout` per discovered flavor, in `install.flavors` order."""
    return tuple(Layout.for_flavor(f, install.root, limits) for f in install.flavors)
