"""Facts about one path in an install, for `explain` (M13-23,
`docs/LAB_PLAN.md` §15.6): its size and modification time, and the latest
snapshot that holds it, with whether the live file still matches.

Everything here reads. The install is read only through `lstat` and one
`snapshot.open_regular_file` of the named path (no link followed, a FIFO
never blocks); the store only through `SnapshotStore.list_lenient`, which
never creates it. Only the one named file is hashed, and only when a
snapshot holds a file of its size there: a different size already differs.
"""

from __future__ import annotations

import hashlib
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import snapshot

__all__ = ["PathStat", "SnapshotFacts", "path_stat", "snapshot_facts"]

_CHUNK = 1 << 20

Live = Literal["same", "differs", "missing", "unreadable"]


class PathStat(BaseModel):
    """`lstat` of the path itself, never of a link's target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["file", "folder", "other"]
    size: int | None  # bytes, for a file; None for a folder or anything else
    mtime: str  # UTC, ISO 8601 with a `Z` suffix
    mtime_ns: int


class SnapshotFacts(BaseModel):
    """The latest snapshot of this install's flavor folder that holds the
    path as a file, and how the live file compares with it.

    `snapshot_id` is None when no snapshot holds the path (or the store could
    not be read: `error` says why). `live` compares the live file's SHA-256
    with the snapshot's: `missing` means the file is gone from the install,
    `unreadable` that it could not be opened as a regular file. `differs` is
    `live` as a yes or no; None when it could not be told."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str | None
    created_at: str | None
    label: str | None
    live: Live | None
    differs: bool | None
    error: str | None


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


def snapshot_facts(
    store: snapshot.SnapshotStore,
    install_root: Path,
    flavor_folder: str,
    rel: str,
) -> SnapshotFacts:
    """The latest snapshot (by creation time, then id) of `flavor_folder` in
    `install_root` that holds `rel` (relative to the flavor folder) as a file,
    and whether the live file matches it."""
    try:
        manifests = store.list_lenient().manifests
    except (snapshot.SnapshotError, OSError) as exc:
        return SnapshotFacts(
            snapshot_id=None,
            created_at=None,
            label=None,
            live=None,
            differs=None,
            error=str(exc) or type(exc).__name__,
        )
    held = f"{flavor_folder}/{rel}"
    latest: tuple[snapshot.Manifest, snapshot.Entry] | None = None
    for m in manifests:
        if m.flavor_folder != flavor_folder or not _same_root(m.install_root, install_root):
            continue
        entry = m.entry(held)
        if entry is None or entry.kind != "file" or entry.sha256 is None:
            continue
        if latest is None or (m.created_at, m.id) > (latest[0].created_at, latest[0].id):
            latest = (m, entry)
    if latest is None:
        return SnapshotFacts(
            snapshot_id=None, created_at=None, label=None, live=None, differs=None, error=None
        )
    manifest, entry = latest
    live = _compare(install_root / flavor_folder / rel, entry)
    return SnapshotFacts(
        snapshot_id=manifest.id,
        created_at=manifest.created_at,
        label=manifest.label,
        live=live,
        differs=None if live == "unreadable" else live != "same",
        error=None,
    )


def _compare(path: Path, entry: snapshot.Entry) -> Live:
    """The live file at `path` against the snapshot's `entry`: opened
    without following a link, read in chunks, never held whole."""
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
