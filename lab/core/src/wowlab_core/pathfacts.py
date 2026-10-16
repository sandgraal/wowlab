"""Facts about one path in an install, for `explain` (M13-23,
`docs/LAB_PLAN.md` §15.6): its spelling on disk, its size and modification
time, and what the snapshots of its flavor folder say about it.

Everything here reads. The install is read only through `lstat`, a listing
of each folder on the way to the path (to find its spelling on disk; a link
is never entered), and one `snapshot.open_regular_file` of the named path (no
link followed, a FIFO never blocks); the store only through
`SnapshotStore.list_lenient`, which never creates it. Only the one named file
is hashed, and only when the snapshot's copy has its size: a different size
already differs.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import snapshot

__all__ = [
    "PathStat",
    "SnapshotFacts",
    "SnapshotRef",
    "disk_spelling",
    "path_stat",
    "snapshot_facts",
]

_CHUNK = 1 << 20

Live = Literal["same", "differs", "missing", "unreadable"]


class PathStat(BaseModel):
    """`lstat` of the path itself, never of a link's target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["file", "folder", "other"]
    size: int | None  # bytes, for a file; None for a folder or anything else
    mtime: str  # UTC, ISO 8601 with a `Z` suffix
    mtime_ns: int


class SnapshotRef(BaseModel):
    """One snapshot, as `snap list` names it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    created_at: str
    label: str


class SnapshotFacts(BaseModel):
    """What the snapshots of this install's flavor folder say about one file.

    `snapshot_id`, `created_at`, `label`: the newest snapshot (by creation
    time, then id) whose captured subtrees cover the path, whether or not it
    holds the file; None when no readable snapshot covers it. `holds`:
    whether that snapshot holds it as a file (None without one). When it
    does, `path` is the file's spelling in that snapshot (relative to the
    flavor folder, what `snap restore --paths` takes) and `live` compares the
    file on disk with that copy by SHA-256:
    `missing` means the file is gone from the install, `unreadable` that it
    could not be opened as a regular file; `differs` is `live` as a yes or
    no, None when it could not be told. When that snapshot does not hold it,
    `last_held` is the newest snapshot that does, if any.

    `captured`: the path is under the subtrees `snap create` captures; a
    file outside them is in no snapshot `snap create` takes. `damaged`: how
    many manifest files could not be read; when it is not 0 the answer may be
    incomplete. `error`: the store could not be listed at all."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str | None
    created_at: str | None
    label: str | None
    holds: bool | None
    path: str | None
    live: Live | None
    differs: bool | None
    last_held: SnapshotRef | None
    captured: bool
    damaged: int
    error: str | None


def disk_spelling(base: Path, parts: Sequence[str]) -> tuple[str, ...]:
    """`parts` (relative to `base`) as the names are spelled on disk.

    A part is respelled only when the typed spelling names an entry (`lstat`
    succeeds: the volume folds case) and the folder's listing holds that
    entry under another spelling. From the first part that names nothing,
    or that is a link, the rest stay as typed: no link is entered."""
    out: list[str] = []
    current = base
    for i, part in enumerate(parts):
        try:
            st = (current / part).lstat()
        except OSError:
            return (*out, *parts[i:])
        name = part
        try:
            with os.scandir(current) as it:
                names = [e.name for e in it]
        except OSError:
            names = []
        if part not in names:
            folded = [n for n in names if n.casefold() == part.casefold()]
            if len(folded) == 1:
                name = folded[0]
        out.append(name)
        current = current / name
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return (*out, *parts[i + 1 :])
    return tuple(out)


def path_stat(path: Path) -> PathStat | None:
    """The size and modification time of `path`, by `lstat`; None when it
    does not exist or cannot be read. The caller has already refused a path
    that is a link or lies inside one."""
    try:
        st = path.lstat()
    except OSError:
        return None
    if stat.S_ISREG(st.st_mode):
        kind: Literal["file", "folder", "other"] = "file"
    elif stat.S_ISDIR(st.st_mode):
        kind = "folder"
    else:
        kind = "other"
    return PathStat(
        kind=kind,
        size=st.st_size if kind == "file" else None,
        mtime=_utc(st.st_mtime_ns),
        mtime_ns=st.st_mtime_ns,
    )


def _utc(ns: int) -> str:
    seconds, rest = divmod(ns, 1_000_000_000)
    when = datetime.fromtimestamp(seconds, tz=UTC) + timedelta(microseconds=rest // 1000)
    return when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _same_root(recorded: str, root: Path) -> bool:
    if Path(recorded) == root:
        return True
    try:
        return Path(recorded).resolve() == root.resolve()
    except (OSError, RuntimeError):
        return False


def _under(path: str, ancestor: str) -> bool:
    """`path` is `ancestor` or below it, case folded: a volume that folds
    case holds one file under every spelling."""
    p, a = path.casefold(), ancestor.casefold()
    return a == "." or p == a or p.startswith(a + "/")


def _covers(manifest: snapshot.Manifest, held: str) -> bool:
    return any(_under(held, s) for s in manifest.subtrees) and not any(
        _under(held, x) for x in manifest.excluded
    )


def _file_entry(manifest: snapshot.Manifest, held: str) -> snapshot.Entry | None:
    """The file `held` in `manifest`: by its exact spelling, else the one
    entry spelled the same with case folded."""
    entry = manifest.entry(held)
    if entry is None:
        folded = [e for e in manifest.entries if e.path.casefold() == held.casefold()]
        entry = folded[0] if len(folded) == 1 else None
    if entry is None or entry.kind != "file" or entry.sha256 is None:
        return None
    return entry


def _ref(m: snapshot.Manifest) -> SnapshotRef:
    return SnapshotRef(id=m.id, created_at=m.created_at, label=m.label)


def snapshot_facts(
    store: snapshot.SnapshotStore,
    install_root: Path,
    flavor_folder: str,
    rel: str,
    captured_subtrees: Sequence[str],
) -> SnapshotFacts:
    """What the snapshots of `flavor_folder` in `install_root` say about
    `rel` (relative to the flavor folder, spelled as on disk).
    `captured_subtrees`: what `snap create` captures, relative to the flavor
    folder."""
    captured = any(_under(rel, s) for s in captured_subtrees)
    try:
        listing = store.list_lenient()
    except (snapshot.SnapshotError, OSError) as exc:
        return SnapshotFacts(
            snapshot_id=None,
            created_at=None,
            label=None,
            holds=None,
            path=None,
            live=None,
            differs=None,
            last_held=None,
            captured=captured,
            damaged=0,
            error=str(exc) or type(exc).__name__,
        )
    held = f"{flavor_folder}/{rel}"
    covering = sorted(
        (
            m
            for m in listing.manifests
            if m.flavor_folder == flavor_folder
            and _same_root(m.install_root, install_root)
            and _covers(m, held)
        ),
        key=lambda m: (m.created_at, m.id),
    )
    damaged = len(listing.invalid)
    if not covering:
        return SnapshotFacts(
            snapshot_id=None,
            created_at=None,
            label=None,
            holds=None,
            path=None,
            live=None,
            differs=None,
            last_held=None,
            captured=captured,
            damaged=damaged,
            error=None,
        )
    latest = covering[-1]
    entry = _file_entry(latest, held)
    if entry is None:
        last = next((m for m in reversed(covering) if _file_entry(m, held) is not None), None)
        return SnapshotFacts(
            snapshot_id=latest.id,
            created_at=latest.created_at,
            label=latest.label,
            holds=False,
            path=None,
            live=None,
            differs=None,
            last_held=_ref(last) if last is not None else None,
            captured=captured,
            damaged=damaged,
            error=None,
        )
    live = _compare(install_root / flavor_folder / rel, entry)
    return SnapshotFacts(
        snapshot_id=latest.id,
        created_at=latest.created_at,
        label=latest.label,
        holds=True,
        path=entry.path.removeprefix(flavor_folder + "/"),
        live=live,
        differs=None if live == "unreadable" else live != "same",
        last_held=None,
        captured=captured,
        damaged=damaged,
        error=None,
    )


def _compare(path: Path, entry: snapshot.Entry) -> Live:
    """The file at `path` against the snapshot's `entry`: opened without
    following a link, read in chunks, never held whole."""
    try:
        fd, st = snapshot.open_regular_file(path)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    try:
        if st.st_size != entry.size:
            return "differs"
        hasher = hashlib.sha256()
        while chunk := os.read(fd, _CHUNK):
            hasher.update(chunk)
    except OSError:
        return "unreadable"
    finally:
        os.close(fd)
    return "same" if hasher.hexdigest() == entry.sha256 else "differs"
