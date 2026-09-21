"""Content-addressed snapshot store (docs/LAB_PLAN.md §6.9, M10-10).

A snapshot is an immutable JSON manifest that names every file under a set of
explicit subtrees of a source tree, plus one zlib-compressed object per
distinct file content, keyed by SHA-256. The store lives under the user data
directory and never inside the tree it captures (L1). This module only ever
reads the source tree; putting bytes back is `guard`'s job (L2, ADR-0021).

Nothing here knows what a flavor is (L6). Callers say which subtrees to
capture; the defaults for an install arrive with `layout` integration in
M10-14.

Concurrency: one process at a time writes to a store. `gc` takes a
`grace_seconds` so an object written (or reused, which refreshes its mtime)
by a `create` that has not yet written its manifest is not collected.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import uuid
import zlib
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePath, PurePosixPath
from typing import BinaryIO, Literal

import platformdirs
from pydantic import BaseModel, ConfigDict, ValidationError

__all__ = [
    "MANIFEST_FORMAT",
    "Change",
    "Entry",
    "GcReport",
    "InvalidManifest",
    "Manifest",
    "ManifestIntegrityError",
    "MissingObject",
    "ObjectCorruptError",
    "SnapshotDiff",
    "SnapshotError",
    "SnapshotExistsError",
    "SnapshotNotFoundError",
    "SnapshotStore",
    "StoreLocationError",
    "VerifyReport",
    "default_store_path",
    "manifest_bytes",
    "tree_fingerprint",
]

MANIFEST_FORMAT = 1

_CHUNK = 1 << 20
_ID_RE = re.compile(r"\A\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{8}\Z")
_ID_PREFIX_RE = re.compile(r"\A[0-9a-fTZ.\-]+\Z")
_SHA_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_MANIFEST_SUFFIX = ".json"


class SnapshotError(Exception):
    """Base class for every error this module raises."""


class StoreLocationError(SnapshotError):
    """The store and the source tree overlap (L1)."""


class SnapshotNotFoundError(SnapshotError):
    """No snapshot, or more than one, matches the id or prefix given."""


class SnapshotExistsError(SnapshotError):
    """A different manifest already exists under the id being created."""


class ManifestIntegrityError(SnapshotError):
    """A manifest on disk does not parse or does not match its own id."""


class ObjectCorruptError(SnapshotError):
    """An object is missing, does not decompress, or does not hash to its name."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Entry(_Frozen):
    """One path in a snapshot. `path` is relative to the root, POSIX-style."""

    path: str
    kind: Literal["file", "symlink"] = "file"
    sha256: str | None = None
    """Content hash; `None` for a symlink, which is recorded and never followed."""
    size: int = 0
    mode: int = 0
    """Permission bits only (`stat.S_IMODE`)."""
    mtime_ns: int = 0
    target: str | None = None
    """Link target text for a symlink, else `None`."""


class Manifest(_Frozen):
    """An immutable snapshot manifest. Only `label` can ever be rewritten."""

    format: int = MANIFEST_FORMAT
    id: str
    created_at: str
    """UTC, ISO 8601 with microseconds, `Z` suffix."""
    label: str = ""
    install_root: str
    flavor_folder: str | None = None
    flavor_version: str | None = None
    subtrees: tuple[str, ...]
    excluded: tuple[str, ...] = ()
    client_running: bool | None = None
    """`True` means SavedVariables on disk were stale relative to the live
    session when this was taken; `None` means the caller did not probe."""
    entries: tuple[Entry, ...]

    @property
    def fingerprint(self) -> str:
        """The tree fingerprint carried in the id (its last eight hex digits)."""
        return self.id.rsplit("-", 1)[-1]

    def entry(self, path: str) -> Entry | None:
        for e in self.entries:
            if e.path == path:
                return e
        return None


class Change(_Frozen):
    path: str
    before: Entry
    after: Entry


class SnapshotDiff(_Frozen):
    """What differs going from snapshot `a` to snapshot `b`.

    `changed` is a content change (hash, kind or link target). A file whose
    bytes are the same but whose permission bits differ is in `mode_changed`.
    An mtime-only difference is not a difference.
    """

    a: str
    b: str
    added: tuple[Entry, ...]
    removed: tuple[Entry, ...]
    changed: tuple[Change, ...]
    mode_changed: tuple[Change, ...]

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed or self.mode_changed)


class MissingObject(_Frozen):
    snapshot_id: str
    path: str
    sha256: str


class InvalidManifest(_Frozen):
    name: str
    reason: str


class VerifyReport(_Frozen):
    objects_checked: int
    manifests_checked: int
    corrupt_objects: tuple[str, ...]
    """Object names (or stray file paths under `objects/`) that failed re-hash."""
    missing_objects: tuple[MissingObject, ...]
    invalid_manifests: tuple[InvalidManifest, ...]

    @property
    def ok(self) -> bool:
        return not (self.corrupt_objects or self.missing_objects or self.invalid_manifests)


class GcReport(_Frozen):
    dry_run: bool
    unreferenced: tuple[str, ...]
    """SHA-256 names of objects no manifest references, sorted."""
    unreferenced_bytes: int
    """Their compressed size on disk."""
    removed: tuple[str, ...]
    """Empty on a dry run; equal to `unreferenced` after a real run."""


def default_store_path() -> Path:
    """`<user data dir>/wowlab/store` (docs/LAB_PLAN.md §6.9)."""
    return platformdirs.user_data_path("wowlab") / "store"


def manifest_bytes(manifest: Manifest) -> bytes:
    """The one canonical encoding: sorted keys, fixed separators, ASCII, LF."""
    text = json.dumps(
        manifest.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return text.encode("ascii") + b"\n"


def tree_fingerprint(
    subtrees: Iterable[str], excluded: Iterable[str], entries: Iterable[Entry]
) -> str:
    """Eight hex digits over what was captured: paths, content, modes.

    Timestamps, the label and the root's location are left out, so two
    snapshots with the same fingerprint hold the same tree.
    """
    payload = {
        "subtrees": list(subtrees),
        "excluded": list(excluded),
        "entries": [[e.path, e.kind, e.sha256, e.size, e.mode, e.target] for e in entries],
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:8]


def _normalize_rel(value: str | PurePath, what: str) -> str:
    pure = PurePath(value)
    if pure.is_absolute() or pure.drive or pure.root:
        raise SnapshotError(f"{what} must be relative to the root: {value!r}")
    posix = PurePosixPath(pure.as_posix())
    if ".." in posix.parts:
        raise SnapshotError(f"{what} must not contain '..': {value!r}")
    return posix.as_posix()  # "." for the root itself


def _is_under(path: str, ancestor: str) -> bool:
    return ancestor == "." or path == ancestor or path.startswith(ancestor + "/")


def _is_within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


class SnapshotStore:
    """A store at `path`, by default under the user data directory.

    Reading methods never create the store. `create` makes the directories it
    needs on first use.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path: Path = Path(path) if path is not None else default_store_path()

    # -- layout ------------------------------------------------------------

    @property
    def objects_dir(self) -> Path:
        return self.path / "objects"

    @property
    def manifests_dir(self) -> Path:
        return self.path / "manifests"

    @property
    def tmp_dir(self) -> Path:
        return self.path / "tmp"

    def object_path(self, sha256: str) -> Path:
        if not _SHA_RE.match(sha256):
            raise SnapshotError(f"not a SHA-256 hex digest: {sha256!r}")
        return self.objects_dir / sha256[:2] / sha256[2:]

    def _manifest_path(self, snapshot_id: str) -> Path:
        if not _ID_RE.match(snapshot_id):
            raise SnapshotError(f"not a snapshot id: {snapshot_id!r}")
        return self.manifests_dir / (snapshot_id + _MANIFEST_SUFFIX)

    # -- create ------------------------------------------------------------

    def create(
        self,
        root: Path,
        subtrees: Iterable[str | PurePath],
        *,
        label: str = "",
        exclude: Iterable[str | PurePath] = (),
        flavor_folder: str | None = None,
        flavor_version: str | None = None,
        client_running: bool | None = None,
        now: datetime | None = None,
    ) -> Manifest:
        """Capture `subtrees` of `root` and return the new manifest.

        `root` is only ever read. A subtree that does not exist is recorded
        in the manifest and contributes no entries. Symlinks are recorded
        with their target and never followed. Anything that is neither a
        regular file, a directory nor a symlink is ignored. A file that
        cannot be read raises; a partial snapshot is never written.

        `now` fixes the clock (timezone-aware); it exists for tests and for
        callers that want one timestamp across several records.
        """
        root = Path(root).absolute()
        if not root.is_dir():
            raise SnapshotError(f"source root is not a directory: {root}")
        self._refuse_overlap(root)

        wanted = sorted({_normalize_rel(s, "subtree") for s in subtrees})
        if not wanted:
            raise SnapshotError("at least one subtree is required")
        excluded = sorted({_normalize_rel(x, "exclude") for x in exclude})
        if "." in excluded:
            raise SnapshotError("excluding the root itself captures nothing")

        when = datetime.now(UTC) if now is None else now
        if when.tzinfo is None:
            raise SnapshotError("`now` must be timezone-aware")
        when = when.astimezone(UTC)

        found: dict[str, Entry] = {}
        for subtree in wanted:
            for rel, abs_path in self._walk(root, subtree, excluded):
                if rel in found:
                    continue  # overlapping subtrees
                entry = self._capture(rel, abs_path)
                if entry is not None:
                    found[rel] = entry
        entries = tuple(found[k] for k in sorted(found))

        fingerprint = tree_fingerprint(wanted, excluded, entries)
        snapshot_id = f"{when:%Y%m%dT%H%M%S}.{when.microsecond:06d}Z-{fingerprint}"
        manifest = Manifest(
            id=snapshot_id,
            created_at=f"{when:%Y-%m-%dT%H:%M:%S}.{when.microsecond:06d}Z",
            label=label,
            install_root=str(root),
            flavor_folder=flavor_folder,
            flavor_version=flavor_version,
            subtrees=tuple(wanted),
            excluded=tuple(excluded),
            client_running=client_running,
            entries=entries,
        )

        data = manifest_bytes(manifest)
        target = self._manifest_path(snapshot_id)
        if target.exists():
            # Same microsecond, same tree. Identical bytes make this a no-op;
            # anything else would be a silent overwrite of an immutable file.
            if target.read_bytes() == data:
                return manifest
            raise SnapshotExistsError(f"a different manifest already exists for {snapshot_id}")
        self._write_atomic(target, data)
        return manifest

    def _refuse_overlap(self, root: Path) -> None:
        store = self.path.resolve()
        source = root.resolve()
        if _is_within(store, source) or _is_within(source, store):
            raise StoreLocationError(
                f"the store ({store}) and the source tree ({source}) must not contain each other"
            )

    def _walk(
        self, root: Path, subtree: str, excluded: Sequence[str]
    ) -> Iterator[tuple[str, Path]]:
        """Yield `(relative posix path, absolute path)` for every non-directory."""
        if any(_is_under(subtree, x) for x in excluded):
            return
        start = root if subtree == "." else root / subtree
        try:
            st = start.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(st.st_mode):
            yield subtree, start
            return
        stack: list[tuple[str, Path]] = [(subtree, start)]
        while stack:
            rel_dir, abs_dir = stack.pop()
            try:
                with os.scandir(abs_dir) as it:
                    children = sorted(it, key=lambda d: d.name)
            except FileNotFoundError:
                continue  # removed while we walked
            except OSError as exc:
                raise SnapshotError(f"cannot list {abs_dir}: {exc}") from exc
            for child in children:
                rel = child.name if rel_dir == "." else f"{rel_dir}/{child.name}"
                if any(_is_under(rel, x) for x in excluded):
                    continue
                if child.is_dir(follow_symlinks=False):
                    stack.append((rel, Path(child.path)))
                else:
                    yield rel, Path(child.path)

    def _capture(self, rel: str, abs_path: Path) -> Entry | None:
        try:
            st = abs_path.lstat()
            if stat.S_ISLNK(st.st_mode):
                return Entry(
                    path=rel,
                    kind="symlink",
                    mode=stat.S_IMODE(st.st_mode),
                    mtime_ns=st.st_mtime_ns,
                    target=str(abs_path.readlink()),
                )
            if not stat.S_ISREG(st.st_mode):
                return None
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(abs_path, flags)
        except FileNotFoundError:
            return None  # removed while we walked
        except OSError as exc:
            raise SnapshotError(f"cannot read {abs_path}: {exc}") from exc
        try:
            with os.fdopen(fd, "rb") as handle:
                st = os.fstat(handle.fileno())
                if not stat.S_ISREG(st.st_mode):
                    return None
                digest, size = self._store_stream(handle)
        except OSError as exc:
            raise SnapshotError(f"cannot read {abs_path}: {exc}") from exc
        return Entry(
            path=rel,
            kind="file",
            sha256=digest,
            size=size,
            mode=stat.S_IMODE(st.st_mode),
            mtime_ns=st.st_mtime_ns,
        )

    def _store_stream(self, stream: BinaryIO) -> tuple[str, int]:
        """Hash a readable, seekable binary file; store it if it is new.

        Pass one hashes. Only when the object is missing does pass two read
        again to compress, and the entry then describes what pass two read,
        so the manifest always names bytes that are really in the store.
        """
        hasher = hashlib.sha256()
        size = 0
        while chunk := stream.read(_CHUNK):
            hasher.update(chunk)
            size += len(chunk)
        digest = hasher.hexdigest()
        existing = self.object_path(digest)
        if existing.is_file():
            os.utime(existing)  # freshen, so a concurrent gc grace period sees it
            return digest, size

        stream.seek(0)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.tmp_dir / f"obj-{uuid.uuid4().hex}"
        hasher = hashlib.sha256()
        size = 0
        compressor = zlib.compressobj(6)
        try:
            with tmp.open("xb") as out:
                while chunk := stream.read(_CHUNK):
                    hasher.update(chunk)
                    size += len(chunk)
                    out.write(compressor.compress(chunk))
                out.write(compressor.flush())
                out.flush()
                os.fsync(out.fileno())
            digest = hasher.hexdigest()
            final = self.object_path(digest)
            if final.is_file():
                os.utime(final)
            else:
                final.parent.mkdir(parents=True, exist_ok=True)
                tmp.replace(final)
        finally:
            tmp.unlink(missing_ok=True)
        return digest, size

    def _write_atomic(self, target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.tmp_dir / f"manifest-{uuid.uuid4().hex}"
        try:
            with tmp.open("xb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)

    # -- read --------------------------------------------------------------

    def _manifest_files(self) -> tuple[Path, ...]:
        if not self.manifests_dir.is_dir():
            return ()
        return tuple(
            sorted(
                p
                for p in self.manifests_dir.iterdir()
                if p.name.endswith(_MANIFEST_SUFFIX) and p.is_file()
            )
        )

    def _load(self, path: Path) -> Manifest:
        """Parse a manifest file and hold it to its own id."""
        name = path.name.removesuffix(_MANIFEST_SUFFIX)
        try:
            manifest = Manifest.model_validate_json(path.read_bytes())
        except (OSError, ValidationError, ValueError) as exc:
            raise ManifestIntegrityError(f"manifest {path.name} does not load: {exc}") from exc
        if manifest.format != MANIFEST_FORMAT:
            raise ManifestIntegrityError(
                f"manifest {path.name} has format {manifest.format}, expected {MANIFEST_FORMAT}"
            )
        if manifest.id != name or not _ID_RE.match(manifest.id):
            raise ManifestIntegrityError(f"manifest {path.name} carries id {manifest.id!r}")
        actual = tree_fingerprint(manifest.subtrees, manifest.excluded, manifest.entries)
        if actual != manifest.fingerprint:
            raise ManifestIntegrityError(
                f"manifest {path.name} was altered: entries hash to {actual}, "
                f"id says {manifest.fingerprint}"
            )
        return manifest

    def resolve_id(self, id_or_prefix: str) -> str:
        """Return the full id for an exact id or a unique prefix of one."""
        if _ID_RE.match(id_or_prefix) and self._manifest_path(id_or_prefix).is_file():
            return id_or_prefix
        if not _ID_PREFIX_RE.match(id_or_prefix):
            raise SnapshotNotFoundError(f"not a snapshot id or prefix: {id_or_prefix!r}")
        matches = [
            p.name.removesuffix(_MANIFEST_SUFFIX)
            for p in self._manifest_files()
            if p.name.startswith(id_or_prefix)
        ]
        if not matches:
            raise SnapshotNotFoundError(f"no snapshot matches {id_or_prefix!r}")
        if len(matches) > 1:
            raise SnapshotNotFoundError(
                f"{id_or_prefix!r} is ambiguous: {', '.join(matches[:5])}"
                + (" …" if len(matches) > 5 else "")
            )
        return matches[0]

    def show(self, snapshot_id: str) -> Manifest:
        """Load one manifest by id or unique id prefix."""
        return self._load(self._manifest_path(self.resolve_id(snapshot_id)))

    def list(self) -> tuple[Manifest, ...]:
        """Every snapshot, oldest first."""
        return tuple(self._load(p) for p in self._manifest_files())

    def read_object(self, sha256: str) -> bytes:
        """Decompressed content of one object, checked against its name."""
        path = self.object_path(sha256)
        inflater = zlib.decompressobj()
        try:
            data = inflater.decompress(path.read_bytes()) + inflater.flush()
        except FileNotFoundError as exc:
            raise ObjectCorruptError(f"object {sha256} is missing") from exc
        except (OSError, zlib.error) as exc:
            raise ObjectCorruptError(f"object {sha256} does not decompress: {exc}") from exc
        if not inflater.eof or inflater.unused_data:
            raise ObjectCorruptError(f"object {sha256} is truncated or has trailing bytes")
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ObjectCorruptError(f"object {sha256} does not hash to its name")
        return data

    def read_file(self, snapshot_id: str, path: str) -> bytes:
        """Content of `path` as captured in a snapshot."""
        manifest = self.show(snapshot_id)
        entry = manifest.entry(path)
        if entry is None:
            raise SnapshotError(f"{path!r} is not in snapshot {manifest.id}")
        if entry.sha256 is None:
            raise SnapshotError(f"{path!r} is a symlink in snapshot {manifest.id}")
        return self.read_object(entry.sha256)

    # -- compare -----------------------------------------------------------

    def diff(self, a: str, b: str) -> SnapshotDiff:
        """Added, removed and changed paths going from `a` to `b`."""
        first = self.show(a)
        second = self.show(b)
        before = {e.path: e for e in first.entries}
        after = {e.path: e for e in second.entries}
        changed: list[Change] = []
        mode_changed: list[Change] = []
        for path in sorted(before.keys() & after.keys()):
            old, new = before[path], after[path]
            if (old.kind, old.sha256, old.target) != (new.kind, new.sha256, new.target):
                changed.append(Change(path=path, before=old, after=new))
            elif old.mode != new.mode:
                mode_changed.append(Change(path=path, before=old, after=new))
        return SnapshotDiff(
            a=first.id,
            b=second.id,
            added=tuple(after[p] for p in sorted(after.keys() - before.keys())),
            removed=tuple(before[p] for p in sorted(before.keys() - after.keys())),
            changed=tuple(changed),
            mode_changed=tuple(mode_changed),
        )

    # -- the one mutation --------------------------------------------------

    def set_label(self, snapshot_id: str, label: str) -> Manifest:
        """Rewrite the label of exactly this snapshot; nothing else changes.

        Guarded by id: the full id is required (no prefix), the manifest on
        disk must carry that id and still match its fingerprint, and the
        replacement differs from it in the `label` field alone.
        """
        path = self._manifest_path(snapshot_id)
        if not path.is_file():
            raise SnapshotNotFoundError(f"no snapshot {snapshot_id!r}")
        current = self._load(path)
        updated = current.model_copy(update={"label": label})
        if updated.model_dump(exclude={"label"}) != current.model_dump(exclude={"label"}):
            raise ManifestIntegrityError("label write would change another field")
        self._write_atomic(path, manifest_bytes(updated))
        return updated

    # -- health ------------------------------------------------------------

    def _object_files(self) -> Iterator[tuple[str | None, Path]]:
        """Every file under `objects/` with its SHA-256 name, or `None` if stray."""
        if not self.objects_dir.is_dir():
            return
        for shard in sorted(self.objects_dir.iterdir()):
            if not shard.is_dir():
                yield None, shard
                continue
            for item in sorted(shard.iterdir()):
                name = shard.name + item.name
                ok = item.is_file() and len(shard.name) == 2 and _SHA_RE.match(name)
                yield (name if ok else None), item

    @staticmethod
    def _rehash(path: Path) -> str | None:
        hasher = hashlib.sha256()
        inflater = zlib.decompressobj()
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(_CHUNK):
                    data: bytes = chunk
                    while data:
                        hasher.update(inflater.decompress(data, _CHUNK))
                        data = inflater.unconsumed_tail
                hasher.update(inflater.flush())
        except (OSError, zlib.error):
            return None
        if not inflater.eof or inflater.unused_data:
            return None
        return hasher.hexdigest()

    def verify(self) -> VerifyReport:
        """Re-hash every object and hold every manifest to its id and objects."""
        corrupt: list[str] = []
        present: set[str] = set()
        checked = 0
        for name, path in self._object_files():
            checked += 1
            if name is None:
                corrupt.append(path.relative_to(self.objects_dir).as_posix())
                continue
            present.add(name)
            if self._rehash(path) != name:
                corrupt.append(name)

        invalid: list[InvalidManifest] = []
        missing: list[MissingObject] = []
        files = self._manifest_files()
        for path in files:
            try:
                manifest = self._load(path)
            except ManifestIntegrityError as exc:
                invalid.append(InvalidManifest(name=path.name, reason=str(exc)))
                continue
            for entry in manifest.entries:
                if entry.sha256 is not None and entry.sha256 not in present:
                    missing.append(
                        MissingObject(snapshot_id=manifest.id, path=entry.path, sha256=entry.sha256)
                    )
        return VerifyReport(
            objects_checked=checked,
            manifests_checked=len(files),
            corrupt_objects=tuple(sorted(corrupt)),
            missing_objects=tuple(missing),
            invalid_manifests=tuple(invalid),
        )

    def gc(self, *, dry_run: bool = True, grace_seconds: float = 0.0) -> GcReport:
        """Find objects no manifest references; remove them unless `dry_run`.

        Dry-run is the default. Refuses outright when any manifest fails to
        load, because that manifest's objects would look unreferenced.
        Objects modified within the last `grace_seconds` are left alone and
        not reported. Only well-formed object files are ever candidates.
        """
        referenced: set[str] = set()
        for manifest in self.list():  # raises ManifestIntegrityError
            referenced.update(e.sha256 for e in manifest.entries if e.sha256 is not None)

        cutoff = datetime.now(UTC).timestamp() - grace_seconds if grace_seconds > 0 else None
        candidates: list[tuple[str, Path, int]] = []
        for name, path in self._object_files():
            if name is None or name in referenced:
                continue
            st = path.stat()
            if cutoff is not None and st.st_mtime > cutoff:
                continue
            candidates.append((name, path, st.st_size))
        candidates.sort()

        removed: list[str] = []
        if not dry_run:
            for name, path, _ in candidates:
                path.unlink()
                removed.append(name)
                with contextlib.suppress(OSError):
                    path.parent.rmdir()  # only succeeds when the shard is empty
        return GcReport(
            dry_run=dry_run,
            unreferenced=tuple(name for name, _, _ in candidates),
            unreferenced_bytes=sum(size for _, _, size in candidates),
            removed=tuple(removed),
        )
