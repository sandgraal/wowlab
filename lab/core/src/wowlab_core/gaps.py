"""gaps: where the file map is thin in an install (M13-22,
`docs/LAB_PLAN.md` §15.6).

`find_gaps()` walks the install root and every flavor folder discovery
reported, classifies each path with the file map (`wowlab_core.filemap`),
and gathers two kinds of thin spot:

- a **gap**: a path no row matches;
- a path **described only by a catch-all row** (`FileMapEntry.catch_all`,
  such as `Cache/**` "not described here yet"): a row names it, but says
  nothing about what it is.

Each thin spot is a *place*: the topmost such path below its nearest parent
folder that a describing row matches (the install root, which has no row of
its own, is the parent of what sits directly in it). A folder that is a
place counts the files, folders, links and bytes below it rather than
listing them; a describing row matching again further down (`**/.DS_Store`
does) starts a place of its own there. Places are grouped by the parent's
row and the place's own name and kind, so one unknown file found in every
character folder is one group with a count, and the text output never needs
an account, realm or character folder name. Groups are ranked by file
count, then bytes. The JSON keeps every place, with its parent's path.

The install root itself is not a gap (no row describes it, and none is
meant to), and nothing outside it is looked at. A flavor folder is
recognised only by the name discovery reported (L6); a root folder named
like one that discovery did not take carries a note saying so.

Reads only (L1): folders are listed and entries `lstat`ed; no file is
opened, and nothing is created anywhere. A symlink, a Windows junction or
any other reparse point is never followed: it is named in `links`, and,
if its own location has no row, it is a gap of kind `link`. A folder whose
every descendant a describing row covers (`FileMap.covers`: `Data/**`,
`Interface/AddOns/<Addon>/**`) is not entered: it is named in
`not_entered` with that row, and what is inside it is labelled by the row,
not checked against it. The walk is bounded (`layout.Limits`: depth and
entry count) and says so in `truncated`. A folder that cannot be listed, or
an entry that cannot be `lstat`ed, is named in `errors`, and the walk
carries on.

Paths from file names keep their bytes: lone surrogates are escaped in
JSON as `layout` does (`layout.FsText`).
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import filemap as _filemap
from wowlab_core.install import Install

# `is_link`: the one link test of discovery, the inventory walk, `tree` and
# `explain`, defined in `install`, re-exported by `layout` (which this module
# imports) and exported here (M13-35, M13-37).
from wowlab_core.layout import FsText, Limits, is_link

__all__ = [
    "NOT_A_FLAVOR",
    "NOT_A_FLAVOR_LINK",
    "GapError",
    "GapGroup",
    "GapPlace",
    "GapReport",
    "NotEntered",
    "find_gaps",
    "is_link",
    "lstat_entry",
]

Base = Literal["root", "flavor"]
GapKind = Literal["file", "folder", "link", "other"]

# The note on a root folder (or link) named `_..._` that discovery did not
# take as a flavor folder (`Install.other_dirs`): its `.flavor.info` is
# missing, is not a regular file, or the folder itself is a link.
NOT_A_FLAVOR = "not taken as a flavor folder (no regular .flavor.info file)"
# The same, for a root link named `_..._`: discovery never takes a link,
# whatever it points at.
NOT_A_FLAVOR_LINK = "not taken as a flavor folder (a link; links are not followed)"
# Paths of files below a folder place kept as examples in the JSON.
EXAMPLES = 5


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GapPlace(_Model):
    """One thin spot: a path and what lies below it."""

    flavor_folder: FsText | None  # set when the group's base is "flavor"
    parent: FsText  # relative to the base; "" is the install root or the flavor folder
    path: FsText  # relative to the base, `/`-separated
    # Below a folder (the path itself for a file): regular files of this
    # kind of thin spot, their bytes, folders, links and other entries
    # (FIFOs, sockets, devices; never opened).
    files: int
    bytes: int
    folders: int
    links: int
    other: int
    # Up to `EXAMPLES` of the files counted below a folder, relative to the
    # base, in walk order; empty for a place that is not a folder.
    examples: list[FsText]


class GapGroup(_Model):
    """The places with the same parent row, name and kind."""

    base: Base
    # The parent folder's row; None for the install root, which has none.
    parent_entry_id: str | None
    name: FsText  # the place's own file or folder name
    kind: GapKind
    # In `catch_all`: the catch-all row that matches these places; None in `groups`.
    entry_id: str | None
    note: str | None  # NOT_A_FLAVOR or NOT_A_FLAVOR_LINK
    places: int
    files: int
    bytes: int
    folders: int
    links: int
    other: int
    gaps: list[GapPlace]  # ranked: most files first, then bytes, then path


class NotEntered(_Model):
    """A folder a describing row covers whole: labelled by it, not looked inside."""

    base: Base
    flavor_folder: FsText | None
    path: FsText  # relative to the base
    entry_id: str


class GapError(_Model):
    """A folder that could not be listed, or an entry not `lstat`ed."""

    path: FsText  # relative to the install root, `/`-separated; "." is the root
    error: str


class GapReport(_Model):
    """`wowlab filemap gaps --json`."""

    install_root: FsText
    flavor_folders: list[FsText]  # as discovery reported them
    # Paths with no row. Ranked: most files first, then bytes, then places, then name.
    groups: list[GapGroup]
    files: int  # regular files with no row
    bytes: int
    # Paths only a catch-all row matches, ranked the same way.
    catch_all: list[GapGroup]
    catch_all_files: int
    catch_all_bytes: int
    not_entered: list[NotEntered]  # in walk order
    # Every link met (symlink, junction, reparse point), relative to the
    # install root, sorted: never followed, whatever its row.
    links: list[FsText]
    errors: list[GapError]  # sorted by path
    entries: int  # entries `lstat`ed, counted against `limits.max_entries`
    truncated: bool  # a bound was hit: there may be thin spots the walk did not reach


# ─── the walk ────────────────────────────────────────────────────────────────


@dataclass
class _Place:
    flavor_folder: str | None
    parent: str
    path: str
    kind: GapKind
    files: int = 0
    bytes: int = 0
    folders: int = 0
    links: int = 0
    other: int = 0
    examples: list[str] = field(default_factory=list)

    def add(self, kind: GapKind, size: int, rel: str) -> None:
        if kind == "file":
            self.files += 1
            self.bytes += size
            if len(self.examples) < EXAMPLES:
                self.examples.append(rel)
        elif kind == "folder":
            self.folders += 1
        elif kind == "link":
            self.links += 1
        else:
            self.other += 1


@dataclass
class _Parent:
    base: Base
    flavor_folder: str | None
    rel: tuple[str, ...]
    entry_id: str | None


@dataclass
class _Frame:
    path: Path
    base: Base
    flavor_folder: str | None
    rel: tuple[str, ...]  # relative to the base
    depth: int  # folders below the install root
    parent: _Parent  # nearest folder a describing row matches
    gap: _Place | None  # the gap this folder lies in
    loose: _Place | None  # the catch-all-only place this folder lies in


_GroupKey = tuple[Base, str | None, str, GapKind, str | None]


@dataclass
class _Found:
    places: dict[_GroupKey, list[_Place]] = field(default_factory=dict)

    def start(
        self,
        parent: _Parent,
        rel: tuple[str, ...],
        kind: GapKind,
        entry_id: str | None,
        size: int,
    ) -> _Place:
        """A new place at `rel` (relative to the base). `rel` is usually a
        child of `parent`, but may lie deeper: a catch-all-only path inside
        a gap folder, or the reverse, keeps the nearest describing parent."""
        place = _Place(parent.flavor_folder, "/".join(parent.rel), "/".join(rel), kind)
        if kind != "folder":
            place.add(kind, size, place.path)
            place.examples = []
        key = (parent.base, parent.entry_id, rel[-1], kind, entry_id)
        self.places.setdefault(key, []).append(place)
        return place


def lstat_entry(entry: os.DirEntry[str]) -> os.stat_result:
    """`lstat` of one listed entry: never follows a link. One place, so a
    test can stand in Windows reparse-point attributes on any system."""
    return entry.stat(follow_symlinks=False)


def _kind(path: Path, st: os.stat_result) -> GapKind:
    if is_link(path, st):
        return "link"
    if stat.S_ISDIR(st.st_mode):
        return "folder"
    if stat.S_ISREG(st.st_mode):
        return "file"
    return "other"


_MAP_KIND: dict[GapKind, _filemap.Kind] = {
    "folder": "dir",
    "file": "file",
    "link": "any",  # never followed to find out
    "other": "any",
}


def _install_rel(base: Base, flavor: str | None, rel: tuple[str, ...]) -> str:
    parts = (flavor, *rel) if base == "flavor" and flavor is not None else rel
    return "/".join(parts) or "."


def _error(exc: OSError) -> str:
    return exc.strerror or str(exc)


def _reported(place: _Place, catch_all: bool) -> bool:
    """A catch-all folder place matters only for what is counted below it."""
    if not catch_all or place.kind != "folder":
        return True
    return bool(place.files or place.links or place.other)


def _groups(found: _Found, *, catch_all: bool, other_dirs: set[str]) -> list[GapGroup]:
    out = []
    for (base, parent_entry, name, kind, entry_id), places in found.places.items():
        kept = sorted(
            (p for p in places if _reported(p, catch_all)),
            key=lambda p: (-p.files, -p.bytes, p.flavor_folder or "", p.path),
        )
        if not kept:
            continue
        at_root = base == "root" and parent_entry is None
        out.append(
            GapGroup(
                base=base,
                parent_entry_id=parent_entry,
                name=name,
                kind=kind,
                entry_id=entry_id,
                note=(
                    (NOT_A_FLAVOR_LINK if kind == "link" else NOT_A_FLAVOR)
                    if at_root and name in other_dirs
                    else None
                ),
                places=len(kept),
                files=sum(p.files for p in kept),
                bytes=sum(p.bytes for p in kept),
                folders=sum(p.folders for p in kept),
                links=sum(p.links for p in kept),
                other=sum(p.other for p in kept),
                gaps=[
                    GapPlace(
                        flavor_folder=p.flavor_folder,
                        parent=p.parent,
                        path=p.path,
                        files=p.files,
                        bytes=p.bytes,
                        folders=p.folders,
                        links=p.links,
                        other=p.other,
                        examples=p.examples,
                    )
                    for p in kept
                ],
            )
        )
    out.sort(key=lambda g: (-g.files, -g.bytes, -g.places, g.name, g.parent_entry_id or ""))
    return out


def find_gaps(install: Install, limits: Limits | None = None) -> GapReport:
    """Every thin spot under `install`'s root, grouped and ranked (see the
    module docstring). Reads only; never raises for a path it cannot read."""
    bounds = limits if limits is not None else Limits()
    fm = _filemap.load()
    root = Path(install.root)
    flavors = {f.folder for f in install.flavors}
    gaps, loose = _Found(), _Found()
    not_entered: list[NotEntered] = []
    links: list[str] = []
    errors: list[GapError] = []
    entries = 0
    truncated = False

    top = _Parent("root", None, (), None)
    stack = [_Frame(root, "root", None, (), 0, top, None, None)]
    while stack:
        frame = stack.pop()
        try:
            with os.scandir(frame.path) as it:
                children = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            where = _install_rel(frame.base, frame.flavor_folder, frame.rel)
            errors.append(GapError(path=where, error=_error(exc)))
            continue
        below: list[_Frame] = []
        for child in children:
            if entries >= bounds.max_entries:
                truncated = True
                break
            entries += 1
            base, flavor, rel = frame.base, frame.flavor_folder, (*frame.rel, child.name)
            try:
                st = lstat_entry(child)
            except OSError as exc:
                errors.append(GapError(path=_install_rel(base, flavor, rel), error=_error(exc)))
                continue
            path = Path(child.path)
            kind = _kind(path, st)
            if base == "root" and not frame.rel and kind == "folder" and child.name in flavors:
                base, flavor, rel = "flavor", child.name, ()
            if kind == "link":
                links.append(_install_rel(base, flavor, rel))
            matched = fm.matches(rel, base, _MAP_KIND[kind])
            describing = next((e for e in matched if not e.catch_all), None)
            size = st.st_size if kind == "file" else 0
            text = "/".join(rel)
            parent, gap, rest = frame.parent, frame.gap, frame.loose
            if describing is not None:
                gap = rest = None
                if kind == "folder":
                    if fm.covers(rel, base):
                        not_entered.append(
                            NotEntered(
                                base=base, flavor_folder=flavor, path=text, entry_id=describing.id
                            )
                        )
                        continue
                    parent = _Parent(base, flavor, rel, describing.id)
            elif not matched:
                rest = None
                if gap is None:
                    gap = gaps.start(parent, rel, kind, None, size)
                else:
                    gap.add(kind, size, text)
            else:
                gap = None
                if rest is None:
                    rest = loose.start(parent, rel, kind, matched[0].id, size)
                else:
                    rest.add(kind, size, text)
            if kind == "folder":
                below.append(_Frame(path, base, flavor, rel, frame.depth + 1, parent, gap, rest))
        if below and frame.depth >= bounds.max_depth:
            truncated = True
            continue
        stack.extend(reversed(below))

    other_dirs = set(install.other_dirs)
    groups = _groups(gaps, catch_all=False, other_dirs=other_dirs)
    catch_all = _groups(loose, catch_all=True, other_dirs=other_dirs)
    return GapReport(
        install_root=str(root),
        flavor_folders=[f.folder for f in install.flavors],
        groups=groups,
        files=sum(g.files for g in groups),
        bytes=sum(g.bytes for g in groups),
        catch_all=catch_all,
        catch_all_files=sum(g.files for g in catch_all),
        catch_all_bytes=sum(g.bytes for g in catch_all),
        not_entered=not_entered,
        links=sorted(links),
        errors=sorted(errors, key=lambda e: e.path),
        entries=entries,
        truncated=truncated,
    )
