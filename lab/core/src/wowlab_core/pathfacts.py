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
import re
import stat
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import snapshot
from wowlab_core.verdict import _fold  # the write gate's fold: NFC, casefold, NFC

__all__ = [
    "PathStat",
    "SnapshotFacts",
    "SnapshotRef",
    "disk_spelling",
    "path_stat",
    "snapshot_facts",
]

_CHUNK = 1 << 20
# A manifest file's name: its snapshot id, which sorts by creation time.
_MANIFEST_NAME = re.compile(r"\A(\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{8})\.json\Z")

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
    flavor folder, what `snap restore --paths` takes), `confirmed` says
    whether that spelling is known to name this file (False: it differs from
    the path, by the write gate's fold, neither name is on disk, and nothing
    on this drive shows whether it treats the two names as one), and `live`
    compares the file on disk with that copy by SHA-256:
    `missing` means the file is gone from the install, `unreadable` that it
    could not be opened as a regular file; `differs` is `live` as a yes or
    no, None when it could not be told. When that snapshot does not hold it,
    `last_held` is the newest snapshot that does, if any, with
    `last_held_path` and `last_held_confirmed` saying of its copy what `path`
    and `confirmed` say (all three None without one).

    `captured`: the path is under the subtrees `snap create` captures; a
    file outside them is in no snapshot `snap create` takes. `damaged`: how
    many manifest files could not be read; when it is not 0 the answer may be
    incomplete and `snapshot_id` is the latest readable one. `damaged_newer`:
    one of those may be newer than `snapshot_id` (its name says so, or does
    not say). `error`: the store could not be listed at all.

    A spelling that differs from the snapshot's by case or Unicode
    normalization names the same file only when the volume says so: both
    spellings `lstat` to the same file. On a case-sensitive volume two such
    names are two files."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str | None
    created_at: str | None
    label: str | None
    holds: bool | None
    path: str | None
    confirmed: bool | None
    live: Live | None
    differs: bool | None
    last_held: SnapshotRef | None
    captured: bool
    damaged: int
    damaged_newer: bool
    error: str | None
    # M13-32, after the fields above so the JSON only grows.
    last_held_path: str | None = None
    last_held_confirmed: bool | None = None
    # M13-27: the facts above in words, the line `explain` prints after
    # "In the latest snapshot", which `wowlab ui` shows verbatim. `explain`
    # sets it; `snapshot_facts` leaves it None.
    words: str | None = None


def disk_spelling(base: Path, parts: Sequence[str]) -> tuple[str, ...]:
    """`parts` (relative to `base`) as the names are spelled on disk.

    A part is respelled only when the typed spelling names an entry (`lstat`
    succeeds: the volume folds case or Unicode normalization) and the
    folder's listing holds that same entry (same device and inode) under a
    spelling equal to it after the write gate's fold. From the first part
    that names nothing, or that is a link, the rest stay as typed: no link
    is entered."""
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
            folded = [n for n in names if _fold(n) == _fold(part)]
            if len(folded) == 1 and _same_entry(current / folded[0], st):
                name = folded[0]
        out.append(name)
        current = current / name
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return (*out, *parts[i + 1 :])
    return tuple(out)


def _same_entry(path: Path, st: os.stat_result) -> bool:
    try:
        other = path.lstat()
    except OSError:
        return False
    return (other.st_dev, other.st_ino) == (st.st_dev, st.st_ino)


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


_LINK = "link"
_Difference = Literal["case", "normalization"]


class _Volume:
    """Whether two spellings (relative to the install root) name one entry
    on this volume.

    Equal spellings do. Spellings that differ after the gate's fold never do.
    Otherwise: when both exist, they are one entry if both `lstat` to the
    same device and inode, each part `lstat`-ed in turn so that no folder
    link is followed; a spelling reached through a link is never the same
    entry. When one does not exist, the nearest existing folder on the way
    decides: if another spelling of it, differing as the two do (in case, or
    in Unicode normalization, or each in turn when they differ in both),
    names the same folder, this volume folds that difference and they are
    one entry; if it names nothing, they are two. When no folder on the way
    can tell, or the two differ in more than case and normalization (the
    gate's fold takes "Straße" for "STRASSE"), the answer is None: unknown.
    Each path is `lstat`-ed at most once."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.seen: dict[tuple[str, ...], tuple[int, int] | str | None] = {}

    def _id(self, parts: tuple[str, ...]) -> tuple[int, int] | str | None:
        """The entry's (device, inode); `_LINK` when a folder on the way is
        a link or not a folder; None when it does not exist."""
        if parts not in self.seen:
            self.seen[parts] = self._walk(parts)
        return self.seen[parts]

    def _walk(self, parts: tuple[str, ...]) -> tuple[int, int] | str | None:
        if parts[:-1]:
            above = self._id(parts[:-1])
            if above is None or above == _LINK:
                return above
            try:
                st_above = self.root.joinpath(*parts[:-1]).lstat()
            except OSError:
                return None
            # A symbolic link, or on Windows a junction: the store's own rule.
            if snapshot._is_link_stat(st_above) or not stat.S_ISDIR(st_above.st_mode):
                return _LINK
        try:
            st = self.root.joinpath(*parts).lstat()
        except OSError:
            return None
        return (st.st_dev, st.st_ino)

    def same(self, a: str, b: str) -> bool | None:
        if a == b:
            return True
        if _fold(a) != _fold(b):
            return False
        pa, pb = tuple(a.split("/")), tuple(b.split("/"))
        ia, ib = self._id(pa), self._id(pb)
        if _LINK in (ia, ib):
            return False
        if ia is not None and ib is not None:
            return ia == ib
        if ia is not None or ib is not None:
            # One exists and the other spelling does not name it: on a volume
            # that folds this difference, it would.
            return False
        if _nfc(a) == _nfc(b):
            return self._folds(pa, "normalization")
        if len(a) == len(b) and a == _nfc(a) and b == _nfc(b) and a.lower() == b.lower():
            # Case only, letter for letter, both already NFC: a Kelvin sign
            # or U+0130 is no plain case difference (NTFS keeps them apart).
            return self._folds(pa, "case")
        if _nfc(a).lower() == _nfc(b).lower():
            # They differ in both: one name only where the volume folds both
            # (NTFS folds case and keeps normalization apart).
            case = self._folds(pa, "case")
            norm = self._folds(pa, "normalization")
            if case is False or norm is False:
                return False
            return True if case is True and norm is True else None
        # The gate's fold reaches further than upper and lower case ("Straße"
        # and "STRASSE" are one name to it); no volume is probed for that.
        return None

    def _folds(self, parts: tuple[str, ...], difference: _Difference) -> bool | None:
        """Whether this volume folds `difference` (upper and lower case, or
        Unicode normalization), judged at the nearest existing folder above
        `parts` that has another spelling to try. On Windows and Linux case
        sensitivity can be set per folder, and this check respells a folder's
        name, so it reads the folder that lists that name: at best the
        file's grandparent, never the folder that holds the file."""
        for i in range(len(parts) - 1, 0, -1):
            folder = parts[:i]
            ident = self._id(folder)
            if ident is None:
                continue
            if ident == _LINK:
                return None
            name = folder[-1]
            if difference == "normalization":
                variants = sorted({unicodedata.normalize(f, name) for f in ("NFC", "NFD")} - {name})
            else:
                variants = [v for v in [_case_variant(name)] if v is not None]
            for variant in variants:
                other = self._id((*folder[:-1], variant))
                return other == ident
        return None

    def under(self, path: str, ancestor: str) -> bool:
        """`path` is `ancestor` or below it (an unknown answer counts)."""
        if ancestor == ".":
            return True
        head = path.split("/")[: len(ancestor.split("/"))]
        return self.same("/".join(head), ancestor) is not False


def _case_variant(name: str) -> str | None:
    """Another spelling of `name` that differs from it in upper and lower
    case only, letter for letter (never "SS" for "ß"); None when it has none."""
    for variant in (name.swapcase(), name.upper(), name.lower()):
        if variant != name and len(variant) == len(name) and variant.lower() == name.lower():
            return variant
    return None


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _covers(manifest: snapshot.Manifest, held: str, volume: _Volume) -> bool:
    return any(volume.under(held, s) for s in manifest.subtrees) and not any(
        volume.under(held, x) for x in manifest.excluded
    )


def _file_entry(
    manifest: snapshot.Manifest, held: str, volume: _Volume
) -> tuple[snapshot.Entry, bool] | None:
    """The file `held` in `manifest` and whether its spelling is confirmed
    to name the same file: by its exact spelling (confirmed), else the one
    entry the volume says is, or may be, the same file."""
    entry = manifest.entry(held)
    confirmed = True
    if entry is None:
        alike = [e for e in manifest.entries if _fold(e.path) == _fold(held)]
        if len(alike) != 1:
            return None
        same = volume.same(held, alike[0].path)
        if same is False:
            return None
        entry, confirmed = alike[0], same is True
    if entry.kind != "file" or entry.sha256 is None:
        return None
    return entry, confirmed


def _newer(name: str, snapshot_id: str | None) -> bool:
    """Whether the unreadable manifest file `name` may be newer than
    `snapshot_id`: its name is a later id, or no id at all."""
    m = _MANIFEST_NAME.match(name)
    return snapshot_id is None or m is None or m.group(1) > snapshot_id


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
    captured = any(
        _fold(rel) == _fold(s) or _fold(rel).startswith(_fold(s) + "/") for s in captured_subtrees
    )
    volume = _Volume(install_root)
    try:
        listing = store.list_lenient()
    except (snapshot.SnapshotError, OSError) as exc:
        return SnapshotFacts(
            snapshot_id=None,
            created_at=None,
            label=None,
            holds=None,
            path=None,
            confirmed=None,
            live=None,
            differs=None,
            last_held=None,
            captured=captured,
            damaged=0,
            damaged_newer=False,
            error=str(exc) or type(exc).__name__,
        )
    held = f"{flavor_folder}/{rel}"
    covering = sorted(
        (
            m
            for m in listing.manifests
            if m.flavor_folder == flavor_folder
            and _same_root(m.install_root, install_root)
            and _covers(m, held, volume)
        ),
        key=lambda m: (m.created_at, m.id),
    )
    damaged = len(listing.invalid)
    latest_id = covering[-1].id if covering else None
    damaged_newer = any(_newer(bad.name, latest_id) for bad in listing.invalid)
    if not covering:
        return SnapshotFacts(
            snapshot_id=None,
            created_at=None,
            label=None,
            holds=None,
            path=None,
            confirmed=None,
            live=None,
            differs=None,
            last_held=None,
            captured=captured,
            damaged=damaged,
            damaged_newer=damaged_newer,
            error=None,
        )
    latest = covering[-1]
    found = _file_entry(latest, held, volume)
    if found is None:
        last = next(
            (
                (m, hit)
                for m in reversed(covering)
                if (hit := _file_entry(m, held, volume)) is not None
            ),
            None,
        )
        return SnapshotFacts(
            snapshot_id=latest.id,
            created_at=latest.created_at,
            label=latest.label,
            holds=False,
            path=None,
            confirmed=None,
            live=None,
            differs=None,
            last_held=_ref(last[0]) if last is not None else None,
            captured=captured,
            damaged=damaged,
            damaged_newer=damaged_newer,
            error=None,
            last_held_path=(
                last[1][0].path.removeprefix(flavor_folder + "/") if last is not None else None
            ),
            last_held_confirmed=last[1][1] if last is not None else None,
        )
    entry, confirmed = found
    live = _compare(install_root / flavor_folder / rel, entry)
    return SnapshotFacts(
        snapshot_id=latest.id,
        created_at=latest.created_at,
        label=latest.label,
        holds=True,
        path=entry.path.removeprefix(flavor_folder + "/"),
        confirmed=confirmed,
        live=live,
        differs=None if live == "unreadable" else live != "same",
        last_held=None,
        captured=captured,
        damaged=damaged,
        damaged_newer=damaged_newer,
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
