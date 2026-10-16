"""gaps: the paths in an install that have no file-map row (M13-22,
`docs/LAB_PLAN.md` §15.6).

`find_gaps()` walks the install root and every flavor folder discovery
reported, classifies each path with the file map (`wowlab_core.filemap`),
and gathers the ones no row matches. A path with no row is grouped under its
nearest classified parent folder (the install root, which has no row of its
own, groups what sits directly in it). Inside a group, a folder with no row
is one gap that counts the files, folders and bytes below it; the paths in
it are not listed one by one, unless a row matches one of them again
(`**/.DS_Store` does), which then becomes a parent of its own. Groups, and
the gaps inside each, are ranked by file count, then bytes.

The install root itself is not a gap (no row describes it, and none is
meant to), and nothing outside it is looked at. A flavor folder is
recognised only by the name discovery reported (L6).

Reads only (L1): folders are listed and entries `lstat`ed; no file is
opened, and nothing is created anywhere. A symlink, a Windows junction or
any other reparse point is never followed: it is named in `links`, and, if
its own location has no row, it is a gap of kind `link`. A folder whose
every descendant a row already covers (`FileMap.covers`, `Data/**` for
one) is not walked. The walk is bounded (`layout.Limits`: depth and entry
count) and says so in `truncated`. A folder that cannot be listed, or an
entry that cannot be `lstat`ed, is named in `errors`, and the walk carries
on.

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
from wowlab_core.layout import FsText, Limits

__all__ = ["Gap", "GapError", "GapGroup", "GapReport", "find_gaps"]

Base = Literal["root", "flavor"]
GapKind = Literal["file", "folder", "link", "other"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Gap(_Model):
    """One path with no file-map row, and what lies below it."""

    path: FsText  # relative to the group's base, `/`-separated
    # `other`: a FIFO, socket or device, never opened.
    kind: GapKind
    # Below a folder (the path itself for a file): regular files with no
    # row, their bytes, folders with no row, links, and other entries.
    files: int
    bytes: int
    folders: int
    links: int
    other: int


class GapGroup(_Model):
    """The gaps whose nearest classified parent is one folder."""

    base: Base
    flavor_folder: FsText | None  # set when `base` is "flavor"
    # The parent, relative to `base`: "" is the install root (base "root")
    # or the flavor folder itself (base "flavor").
    parent: FsText
    # The parent's file-map row; None only for the install root, which has none.
    entry_id: str | None
    files: int
    bytes: int
    gaps: list[Gap]  # ranked: most files first, then most bytes, then path


class GapError(_Model):
    """A folder that could not be listed, or an entry not `lstat`ed."""

    path: FsText  # relative to the install root, `/`-separated
    error: str


class GapReport(_Model):
    """`wowlab filemap gaps --json`."""

    install_root: FsText
    flavor_folders: list[FsText]  # as discovery reported them
    groups: list[GapGroup]  # ranked: most files first, then most bytes, then parent
    files: int  # regular files with no row, in every group
    bytes: int
    # Every link met (symlink, junction, reparse point), relative to the
    # install root, sorted: never followed, whatever its row.
    links: list[FsText]
    errors: list[GapError]  # sorted by path
    entries: int  # entries `lstat`ed, counted against `limits.max_entries`
    truncated: bool  # a bound was hit: there may be gaps the walk did not reach


# ─── the walk ────────────────────────────────────────────────────────────────


@dataclass
class _Gap:
    path: str
    kind: GapKind
    files: int = 0
    bytes: int = 0
    folders: int = 0
    links: int = 0
    other: int = 0


@dataclass
class _Group:
    base: Base
    flavor_folder: str | None
    parent: str
    entry_id: str | None
    gaps: list[_Gap] = field(default_factory=list)


@dataclass
class _Frame:
    path: Path
    base: Base
    flavor_folder: str | None
    rel: tuple[str, ...]  # relative to the base
    depth: int  # folders below the install root
    group: tuple[Base, str | None, tuple[str, ...]]
    gap: _Gap | None  # the gap this folder lies in, if it has no row


def _is_link(path: Path, st: os.stat_result) -> bool:
    """A symlink, or any Windows reparse point (junctions included)."""
    attributes = int(getattr(st, "st_file_attributes", 0))
    if stat.S_ISLNK(st.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    try:
        return path.is_junction()
    except OSError:
        return False


def _kind(path: Path, st: os.stat_result) -> GapKind:
    if _is_link(path, st):
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


def _install_rel(frame_base: Base, flavor: str | None, rel: tuple[str, ...]) -> str:
    parts = (flavor, *rel) if frame_base == "flavor" and flavor is not None else rel
    return "/".join(parts)


def _parent_text(group: GapGroup) -> str:
    """The group's parent relative to the install root, for ordering."""
    rel = tuple(group.parent.split("/")) if group.parent else ()
    return _install_rel(group.base, group.flavor_folder, rel)


def _error(exc: OSError) -> str:
    return exc.strerror or str(exc)


def _rank(files: int, size: int, path: str) -> tuple[int, int, str]:
    return (-files, -size, path)


def find_gaps(install: Install, limits: Limits | None = None) -> GapReport:
    """Every path under `install`'s root with no file-map row, grouped and
    ranked (see the module docstring). Reads only; never raises for a path
    it cannot read."""
    bounds = limits if limits is not None else Limits()
    fm = _filemap.load()
    root = Path(install.root)
    flavors = {f.folder for f in install.flavors}
    root_key: tuple[Base, str | None, tuple[str, ...]] = ("root", None, ())
    groups: dict[tuple[Base, str | None, tuple[str, ...]], _Group] = {
        root_key: _Group("root", None, "", None)
    }
    links: list[str] = []
    errors: list[GapError] = []
    entries = 0
    truncated = False

    stack = [_Frame(root, "root", None, (), 0, root_key, None)]
    while stack:
        frame = stack.pop()
        try:
            with os.scandir(frame.path) as it:
                children = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            errors.append(
                GapError(
                    path=_install_rel(frame.base, frame.flavor_folder, frame.rel),
                    error=_error(exc),
                )
            )
            continue
        below: list[_Frame] = []
        for child in children:
            if entries >= bounds.max_entries:
                truncated = True
                break
            entries += 1
            base, flavor, rel = frame.base, frame.flavor_folder, (*frame.rel, child.name)
            try:
                st = child.stat(follow_symlinks=False)
            except OSError as exc:
                errors.append(GapError(path=_install_rel(base, flavor, rel), error=_error(exc)))
                continue
            path = Path(child.path)
            kind = _kind(path, st)
            if base == "root" and not frame.rel and kind == "folder" and child.name in flavors:
                base, flavor, rel = "flavor", child.name, ()
            if kind == "link":
                links.append(_install_rel(base, flavor, rel))
            entry = fm.classify(rel, base, _MAP_KIND[kind])
            size = st.st_size if kind == "file" else 0
            gap = frame.gap
            group = frame.group
            if entry is not None:
                gap = None
                if kind == "folder":
                    if fm.covers(rel, base):
                        continue  # a row covers everything below: no gap there
                    group = (base, flavor, rel)
                    groups.setdefault(group, _Group(base, flavor, "/".join(rel), entry.id))
            elif gap is None:
                gap = _Gap("/".join(rel), kind, files=int(kind == "file"), bytes=size)
                gap.links = int(kind == "link")
                gap.other = int(kind == "other")
                groups[group].gaps.append(gap)
            else:
                gap.files += kind == "file"
                gap.bytes += size
                gap.folders += kind == "folder"
                gap.links += kind == "link"
                gap.other += kind == "other"
            if kind == "folder":
                below.append(_Frame(path, base, flavor, rel, frame.depth + 1, group, gap))
        if below and frame.depth >= bounds.max_depth:
            truncated = True
            continue
        stack.extend(reversed(below))

    ranked = []
    for g in groups.values():
        if not g.gaps:
            continue
        gaps = sorted(g.gaps, key=lambda x: _rank(x.files, x.bytes, x.path))
        ranked.append(
            GapGroup(
                base=g.base,
                flavor_folder=g.flavor_folder,
                parent=g.parent,
                entry_id=g.entry_id,
                files=sum(x.files for x in gaps),
                bytes=sum(x.bytes for x in gaps),
                gaps=[
                    Gap(
                        path=x.path,
                        kind=x.kind,
                        files=x.files,
                        bytes=x.bytes,
                        folders=x.folders,
                        links=x.links,
                        other=x.other,
                    )
                    for x in gaps
                ],
            )
        )
    ranked.sort(key=lambda g: _rank(g.files, g.bytes, _parent_text(g)))
    return GapReport(
        install_root=str(root),
        flavor_folders=[f.folder for f in install.flavors],
        groups=ranked,
        files=sum(g.files for g in ranked),
        bytes=sum(g.bytes for g in ranked),
        links=sorted(links),
        errors=sorted(errors, key=lambda e: e.path),
        entries=entries,
        truncated=truncated,
    )
