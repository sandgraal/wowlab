"""Content-addressed snapshot store (docs/LAB_PLAN.md §6.9, M10-10).

A snapshot is an immutable JSON manifest that names every file under a set of
explicit subtrees of a source tree, plus one zlib-compressed object per
distinct file content, keyed by SHA-256. The store lives under the user data
directory and never inside the tree it captures (L1). This module only ever
reads the source tree; putting bytes back is `guard`'s job (L2, ADR-0021).

Nothing here knows what a flavor is (L6). Callers say which subtrees to
capture; the defaults for an install arrive with `layout` integration in
M10-14.

Strings in a manifest file: JSON cannot carry a lone surrogate, and Python
hands us one for every undecodable POSIX file-name byte and every unpaired
UTF-16 unit in a Windows name. `manifest_bytes` therefore writes each code
point in U+D800..U+DFFF as NUL followed by four hex digits, and a literal NUL
as NUL `0000`; `Manifest` validation in JSON mode reverses it. A string with
neither (every ordinary path and label) is written unchanged.

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
import sys
import uuid
import zlib
from collections.abc import Callable, Iterable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePath, PurePosixPath
from typing import Any, BinaryIO, Literal, NamedTuple

import platformdirs
from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

__all__ = [
    "MANIFEST_FORMAT",
    "OBJECTS_DIR_ENTRY",
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
    "SnapshotListing",
    "SnapshotNotFoundError",
    "SnapshotStore",
    "StoreLocationError",
    "VerifyReport",
    "default_store_path",
    "manifest_bytes",
    "tree_fingerprint",
]

MANIFEST_FORMAT = 1

OBJECTS_DIR_ENTRY = "objects/"
"""What `verify` puts in `corrupt_objects` for `objects/` itself when it is a
link (M11-18). No object name or stray path under `objects/` ends in `/`."""

_CHUNK = 1 << 20
_ID_RE = re.compile(r"\A\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{8}\Z")
_ID_PREFIX_RE = re.compile(r"\A[0-9a-fTZ.\-]+\Z")
_SHA_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_MANIFEST_SUFFIX = ".json"
_ON_WINDOWS = sys.platform == "win32"
_REPARSE_NAME_SURROGATE = 0x20000000
"""The bit in a Windows reparse tag that says "this names another path"
(symlink, junction, WCI link, ...); cloud placeholders and dedup lack it."""


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


# ─── reading a file that must be a regular file (M11-11) ────────────────────
#
# Every read of a file this package did not just create itself goes through
# `open_regular_file`: guard's reads of install files and journal records, and
# the store's reads of captured files, objects and manifests. A path checked
# by `lstat` can be replaced before it is opened. A FIFO put there would block
# an ordinary open until a writer appeared, and guard makes these reads while
# it holds the store and install locks. A terminal put there could become the
# controlling terminal, and a symbolic link could send the read elsewhere. So
# the open never blocks, never adopts a terminal and never follows a final
# link, and the descriptor is then held to being a regular file by `fstat`
# before a byte is read. It is closed on every path out; `os.fdopen` only ever
# wraps a descriptor that passed that check.

_O_NOFOLLOW: int = getattr(os, "O_NOFOLLOW", 0)
"""0 on Windows, where a link is refused by `lstat` before the open and the
opened file compared with the path after it, as guard's lock-file open does."""


class UnsafeReadError(OSError):
    """A path opened to read is not a regular file, is a link, is not the file
    the caller looked at, or holds more bytes than the caller's bound."""


def _file_identity(st: os.stat_result) -> tuple[int, int]:
    return (st.st_dev, st.st_ino)


def _is_link_stat(st: os.stat_result) -> bool:
    """A symbolic link, or on Windows a reparse point that names another path
    (the name-surrogate bit: junctions, WCI links). Other reparse points
    (cloud-file placeholders, deduplicated files) hold real content and are
    read, as `create` walks them (`_is_link_like_dir`)."""
    if stat.S_ISLNK(st.st_mode):
        return True
    attributes = int(getattr(st, "st_file_attributes", 0))
    tag = int(getattr(st, "st_reparse_tag", 0))
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT) and bool(
        tag & _REPARSE_NAME_SURROGATE
    )


def _lstat_at(path: Path, dir_fd: int | None) -> os.stat_result:
    """`os.lstat(path)`, relative to `dir_fd` when one is given."""
    if dir_fd is None:
        return os.lstat(path)
    return os.stat(path, dir_fd=dir_fd, follow_symlinks=False)


def open_regular_file(
    path: Path, *, expect: os.stat_result | None = None, dir_fd: int | None = None
) -> tuple[int, os.stat_result]:
    """Open `path` read-only and return the descriptor with its `fstat`.

    The open uses `O_RDONLY | O_BINARY | O_NOFOLLOW | O_NONBLOCK | O_NOCTTY`
    (each where the platform has it), so it returns at once on a FIFO, never
    makes a terminal the controlling one, and refuses a final symbolic link
    (`ELOOP`). The descriptor must then be a regular file, and when `expect`
    is given (an earlier `lstat` of the path) the same file by device and
    inode; otherwise `UnsafeReadError`. Where the platform has no
    `O_NOFOLLOW`, a link or reparse point is refused by `lstat` before the
    open, and the path is checked after it to still name the opened file.
    The caller owns the descriptor returned; on any error it is closed here.
    A missing path raises `FileNotFoundError`. With `dir_fd` (M11-18), a
    relative `path` names an entry of that directory descriptor.
    """
    nofollow = _O_NOFOLLOW
    before: os.stat_result | None = None
    if not nofollow:
        before = _lstat_at(path, dir_fd)
        if _is_link_stat(before) or not stat.S_ISREG(before.st_mode):
            raise UnsafeReadError(f"{path} is not a regular file")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | nofollow
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOCTTY", 0)
    )
    fd = os.open(path, flags, dir_fd=dir_fd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise UnsafeReadError(f"{path} is not a regular file")
        if expect is not None and _file_identity(st) != _file_identity(expect):
            raise UnsafeReadError(f"{path} was replaced before it was opened")
        if before is not None:
            after = _lstat_at(path, dir_fd)
            if _is_link_stat(after) or _file_identity(after) != _file_identity(st):
                raise UnsafeReadError(f"{path} changed as it was opened")
    except BaseException:
        os.close(fd)
        raise
    return fd, st


def read_regular_file(
    path: Path,
    *,
    limit: int | None = None,
    expect: os.stat_result | None = None,
    dir_fd: int | None = None,
) -> bytes:
    """The bytes of `path`, opened as `open_regular_file` opens it (relative
    to `dir_fd` when one is given).

    With a `limit`, a file whose `fstat` size is over it is refused before
    any read, and at most `limit + 1` bytes are read, so one that grows past
    it while being read is refused too (`UnsafeReadError`). The descriptor is
    closed on every path.
    """
    fd, st = open_regular_file(path, expect=expect, dir_fd=dir_fd)
    try:
        if limit is not None and st.st_size > limit:
            raise UnsafeReadError(f"{path} holds {st.st_size} bytes, more than {limit}")
        budget = None if limit is None else limit + 1
        chunks: list[bytes] = []
        total = 0
        while budget is None or total < budget:
            want = _CHUNK if budget is None else min(_CHUNK, budget - total)
            chunk = os.read(fd, want)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if limit is not None and total > limit:
            raise UnsafeReadError(f"{path} holds more than {limit} bytes")
    finally:
        os.close(fd)
    return b"".join(chunks)


class _InflatesTooFarError(Exception):
    """An object produced one byte more than the size its entry records."""


def _inflate_fd(fd: int, limit: int | None) -> tuple[bytes, bool]:
    """Inflate the zlib stream read from `fd`.

    Returns the bytes and whether they were one complete stream with nothing
    after it. With a `limit` (M11-15) no call ever produces more than the
    room left under `limit + 1` bytes, and reaching `limit + 1` raises
    `_InflatesTooFarError`, so a small object that would inflate without end
    costs at most `limit + 1` bytes of output plus one compressed chunk.
    `zlib.error` and `OSError` propagate.
    """
    inflater = zlib.decompressobj()
    cap = None if limit is None else limit + 1
    parts: list[bytes] = []
    produced = 0
    while not inflater.eof:
        data = os.read(fd, _CHUNK)
        if not data:
            return b"".join(parts), False  # truncated
        while data and not inflater.eof:
            room = 0 if cap is None else cap - produced  # 0: no bound
            out = inflater.decompress(data, room)
            produced += len(out)
            if cap is not None and produced >= cap:
                raise _InflatesTooFarError
            parts.append(out)
            data = inflater.unconsumed_tail
    trailing = bool(inflater.unused_data) or bool(os.read(fd, 1))
    return b"".join(parts), not trailing


def _hash_object_fd(fd: int) -> str | None:
    """The SHA-256 of the object stream on `fd`, inflated a chunk at a time
    and never held whole; None when it does not decompress cleanly."""
    hasher = hashlib.sha256()
    inflater = zlib.decompressobj()
    try:
        while chunk := os.read(fd, _CHUNK):
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


_UNLINK_BY_DIR_FD = (
    bool(_O_NOFOLLOW)
    and hasattr(os, "O_DIRECTORY")
    and {os.open, os.stat, os.unlink, os.rmdir} <= os.supports_dir_fd
)
"""True where `gc` can delete an object through descriptors on `objects/` and
its shard, opened without following a link (POSIX). Elsewhere (Windows) each
component is checked by `lstat` just before the delete."""

_WRITE_BY_DIR_FD = (
    bool(_O_NOFOLLOW)
    and hasattr(os, "O_DIRECTORY")
    and {os.open, os.stat, os.mkdir, os.rename, os.unlink} <= os.supports_dir_fd
)
"""True where `create` writes into the store through descriptors on `tmp/`,
`manifests/`, `objects/` and the object shard, each opened without following
a link and checked against its `lstat` (M11-18): POSIX. `os.replace` is
`os.rename`'s twin and shares its `dir_fd` support (the set names only
`rename`). Elsewhere (Windows) each of those directories is checked by
`lstat` just before it is used, and one that is a link is refused."""


class _StoreDir(NamedTuple):
    """A directory of the store that `create` writes into (M11-18), checked
    not to be a link (a symlink, or on Windows a junction or other
    name-surrogate reparse point) and to be a directory."""

    path: Path
    fd: int | None
    """A descriptor on it opened with `O_DIRECTORY | O_NOFOLLOW` (POSIX), or
    None where there are no directory descriptors (Windows)."""
    checked: tuple[tuple[Path, os.stat_result], ...] = ()
    """Without a descriptor: each component below the store root with the
    `lstat` it passed, so a later step can ask again (`still_checked`)."""

    def still_checked(self) -> bool:
        """Every component in `checked` is, by `lstat` now, still the
        directory it was and not a link. Always true with a descriptor."""
        for path, st in self.checked:
            try:
                now = os.lstat(path)
            except OSError:
                return False
            if (
                _is_link_stat(now)
                or not stat.S_ISDIR(now.st_mode)
                or _file_identity(now) != _file_identity(st)
            ):
                return False
        return True

    def at(self, name: str) -> Path:
        """What to hand an `os` call together with `dir_fd=self.fd` to reach
        the entry `name`: the bare name against the descriptor, else the path."""
        return Path(name) if self.fd is not None else self.path / name


class _Staged(NamedTuple):
    """A file being written under the store's `tmp/` (M11-18)."""

    tmp: _StoreDir
    name: str
    out: BinaryIO


def _linked_store_dir(path: Path) -> SnapshotError:
    return SnapshotError(
        f"{path} is a link or not a directory; the snapshot store never writes through a "
        "link. Nothing was written there. Move it aside so the store can make a real "
        "directory in its place"
    )


def _checked_stream(fd: int) -> BinaryIO:
    """A buffered reader on a descriptor `open_regular_file` returned (and so
    checked); the descriptor is closed if the wrapping fails."""
    try:
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


class _ObjectFile(NamedTuple):
    """One entry under `objects/` as `SnapshotStore._object_files` lists it
    (M11-15): every stat is an `lstat`, so nothing here was followed."""

    name: str | None
    """SHA-256 name for a well-named item that is a regular file or a link;
    None for anything stray."""
    path: Path
    st: os.stat_result
    top_st: os.stat_result | None
    """`objects/` as listed; None for `objects/` itself."""
    shard_st: os.stat_result | None
    """The shard directory as listed; None for an entry directly in (or
    being) `objects/`."""


_NEEDS_ESCAPE_RE = re.compile("[\x00\ud800-\udfff]")
_ESCAPED_RE = re.compile("\x00([0-9A-F]{4})")


def _escape_text(text: str) -> str:
    return _NEEDS_ESCAPE_RE.sub(lambda m: f"\x00{ord(m.group()):04X}", text)


def _unescape_text(text: str) -> str:
    if "\x00" not in text:
        return text
    restored = _ESCAPED_RE.sub(lambda m: chr(int(m.group(1), 16)), text)
    if _escape_text(restored) != text:
        raise ValueError("malformed NUL escape in a manifest string")
    return restored


def _map_strings(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list | tuple):
        return [_map_strings(v, fn) for v in value]
    if isinstance(value, dict):
        return {k: _map_strings(v, fn) for k, v in value.items()}
    return value


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Entry(_Frozen):
    """One path in a snapshot. `path` is relative to the root, POSIX-style."""

    path: str
    """Relative to the root, `/`-separated, as the capturing platform named it.

    The rule is platform-neutral: no NUL and no empty, `.` or `..` part. A
    backslash or a colon is an ordinary character in a POSIX file name and is
    kept. Platform-specific path safety (what `a\\b` or `C:x` would mean on
    Windows) is `guard`'s job at restore time (M10-11), not the manifest's.
    """
    kind: Literal["file", "symlink"] = "file"
    sha256: str | None = None
    """Content hash; `None` for a symlink, which is recorded and never followed."""
    size: int = 0
    mode: int = 0
    """Permission bits only (`stat.S_IMODE`)."""
    mtime_ns: int = 0
    target: str | None = None
    """Link target text for a symlink, else `None`.

    On Windows a directory that is a junction, or any other name-surrogate
    reparse point (a WCI link, say), is recorded as a `symlink` entry with
    its `readlink` target. When `readlink` cannot describe it, `target` is
    `None`: a `symlink` entry with no target names a link whose destination
    is unknown, never a real path, and nothing will ever match or recreate it."""

    @field_validator("path")
    @classmethod
    def _path_stays_inside_the_root(cls, value: str) -> str:
        parts = value.split("/")
        if "\x00" in value or any(part in ("", ".", "..") for part in parts):
            raise ValueError(
                f"entry path must be relative, without NUL or an empty, '.' or '..' part: {value!r}"
            )
        return value


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
    purpose: Literal["profile"] | None = None
    """What the snapshot was taken for, when a caller says so: `"profile"`
    for `profiles.save` (M11-08, §13.3). `None` is left out of the manifest
    file, so a manifest without a purpose is byte-for-byte what it was
    before the field existed. Like the label, it is not in the fingerprint."""
    entries: tuple[Entry, ...]

    @model_validator(mode="before")
    @classmethod
    def _unescape_json_strings(cls, data: Any, info: ValidationInfo) -> Any:
        # JSON mode means "the bytes `manifest_bytes` wrote"; see the module docstring.
        return _map_strings(data, _unescape_text) if info.mode == "json" else data

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


class SnapshotListing(_Frozen):
    """What `SnapshotStore.list_lenient()` found: every manifest that loads,
    oldest first, and every file under `manifests/` that does not."""

    manifests: tuple[Manifest, ...]
    invalid: tuple[InvalidManifest, ...]

    @property
    def ok(self) -> bool:
        return not self.invalid


class VerifyReport(_Frozen):
    objects_checked: int
    manifests_checked: int
    corrupt_objects: tuple[str, ...]
    """Object names (or stray file paths under `objects/`) that failed re-hash;
    `OBJECTS_DIR_ENTRY` (`"objects/"`) when `objects/` itself is a link, so
    nothing under it was checked (M11-18; it was `"."` before)."""
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
    """Empty on a dry run; after a real run, `unreferenced` less any object
    whose path changed between the listing and the delete (then in `skipped`)."""
    removed_bytes: int = 0
    """The compressed size on disk of `removed` alone, as listed: 0 on a dry
    run, and less than `unreferenced_bytes` when an object was skipped at its
    delete (M11-18)."""
    skipped: tuple[str, ...] = ()
    """Entries under `objects/` that gc left alone because they are links
    (symlinks, and on Windows junctions and other name-surrogate reparse
    points) or not regular files, plus any object whose path changed before
    its delete, as store-relative POSIX paths, sorted (M11-15). Nothing is
    ever deleted through a link."""


def default_store_path() -> Path:
    """`<user data dir>/wowlab/store` (docs/LAB_PLAN.md §6.9)."""
    return platformdirs.user_data_path("wowlab") / "store"


def manifest_bytes(manifest: Manifest) -> bytes:
    """The one canonical encoding: sorted keys, fixed separators, ASCII, LF.

    `Manifest.model_validate_json` is the inverse, for every string Python can
    hold (lone surrogates included; see the module docstring).
    """
    data = manifest.model_dump()
    if data.get("purpose") is None:
        data.pop("purpose", None)  # absent, as in every manifest written before M11-08
    text = json.dumps(
        _map_strings(data, _escape_text),
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
    text = str(value)
    if text == "" or "\x00" in text:
        raise SnapshotError(f"{what} must be a non-empty path without NUL: {value!r}")
    pure = PurePath(value)
    if pure.is_absolute() or pure.drive or pure.root:
        raise SnapshotError(f"{what} must be relative to the root: {value!r}")
    posix = PurePosixPath(pure.as_posix())
    if ".." in posix.parts:
        raise SnapshotError(f"{what} must not contain '..': {value!r}")
    return posix.as_posix()  # "." for the root itself


def _is_junction(path: Path | os.DirEntry[str]) -> bool:
    """True for an NTFS junction (a mount-point reparse point); always False off Windows.

    CPython reports only `IO_REPARSE_TAG_SYMLINK` as a link. A junction
    `lstat`s as a directory, so `S_ISLNK` and `is_dir(follow_symlinks=False)`
    both miss it; every place that decides "link or directory" asks here too.
    Only junctions count: other reparse points (cloud-file placeholders,
    deduplicated files) hold real content and `readlink` cannot describe them.
    """
    return path.is_junction()


def _is_link_like_dir(path: Path | os.DirEntry[str]) -> bool:
    """True for a directory entry that names another path without being a symlink.

    That is a junction, or on Windows any directory whose reparse tag has the
    name-surrogate bit (a WCI link, say): all of them `lstat` as directories
    on 3.12. Non-surrogate reparse points (cloud-file placeholders,
    deduplicated files) hold real content and are walked as directories.
    Call it only for something already known to be a directory.
    """
    if _is_junction(path):
        return True
    if not _ON_WINDOWS:
        return False
    try:
        st = os.lstat(path)
    except OSError:
        return False  # gone, or unreadable: the walk reports it where it matters
    return bool(int(getattr(st, "st_reparse_tag", 0)) & _REPARSE_NAME_SURROGATE)


def _dir_link_target(path: Path) -> str | None:
    """`readlink` of a link-like directory, or `None` when it cannot say (see `Entry.target`)."""
    try:
        return str(path.readlink())
    except FileNotFoundError:
        raise  # removed while we walked; `_capture` skips it
    except (OSError, ValueError):
        return None


def _is_under(path: str, ancestor: str) -> bool:
    return ancestor == "." or path == ancestor or path.startswith(ancestor + "/")


def _is_within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def _same_dir(a: Path, b: Path) -> bool:
    """Identity, not spelling: true when both exist and are one directory."""
    try:
        return a.samefile(b)
    except (OSError, ValueError):
        return False


def _strictly_holds(outer: Path, inner: Path) -> bool:
    """`outer` is a proper ancestor of `inner` (both resolved): by spelling,
    or by identity with one of `inner`'s existing ancestors."""
    return outer in inner.parents or any(_same_dir(p, outer) for p in inner.parents)


def _overlap_error(store: Path, source: Path) -> StoreLocationError:
    return StoreLocationError(
        f"the store ({store}) and the source tree ({source}) must not contain each other"
    )


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
        purpose: Literal["profile"] | None = None,
        now: datetime | None = None,
    ) -> Manifest:
        """Capture `subtrees` of `root` and return the new manifest.

        `root` is only ever read. A subtree that does not exist is recorded
        in the manifest and contributes no entries. Symlinks, and on Windows
        junctions and other directory links (name-surrogate reparse points),
        are recorded as `symlink` entries with their target
        and never followed; a subtree that can only be reached through one
        below the root is refused. Anything that
        is neither a regular file, a directory nor a symlink is ignored. A
        file that cannot be read raises; a partial snapshot is never written.

        Every failure is a `SnapshotError`. Bad arguments and a name the
        manifest cannot carry fail before anything is stored. A failure part
        way through the walk (an unreadable file) can leave objects already
        stored for earlier files; they are complete, content-addressed and
        referenced by nothing, so they cost space only, are reused by the
        next `create`, and are collected by `gc`. No manifest and no file
        under `tmp/` is left behind.

        Subtrees and excludes are relative paths; `"."` is the explicit
        spelling of the whole root and the empty string is refused. They are
        recorded, and entry paths are built from them, exactly as given:
        nothing here folds case, so on a case-insensitive volume `wtf` and
        `WTF` capture the same files under different paths and `diff` would
        report every one as removed and added. Pass the spelling discovery
        found on disk.

        An object already in the store is re-hashed before it is reused and
        rewritten if it is damaged, so a new snapshot never depends on a bad
        object.

        Nothing is written through a link (M11-18): a store whose
        `objects/`, `manifests/` or `tmp/` is a link (on Windows also a
        junction) or not a directory is refused before the walk, and every
        object and the manifest are written through descriptors on those
        directories and the object shard, each opened without following a
        link (see "writing into the store's own directories" below), so a
        shard that is a link refuses the `create` when it is reached.

        `purpose` is recorded in the manifest (`"profile"` from
        `profiles.save`, M11-08); a manifest with none omits the field.

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
        self._refuse_linked_store_dirs()

        header: dict[str, Any] = {
            "created_at": f"{when:%Y-%m-%dT%H:%M:%S}.{when.microsecond:06d}Z",
            "label": label,
            "install_root": str(root),
            "flavor_folder": flavor_folder,
            "flavor_version": flavor_version,
            "subtrees": tuple(wanted),
            "excluded": tuple(excluded),
            "client_running": client_running,
            "purpose": purpose,
        }
        # Hold the caller's values to the model before anything is stored, so
        # a bad argument fails with a typed error and an untouched store.
        self._manifest(header, snapshot_id="", entries=())

        found: dict[str, Entry] = {}
        verified: set[str] = set()  # objects re-hashed during this create
        for subtree in wanted:
            for rel, abs_path in self._walk(root, subtree, excluded):
                if rel in found:
                    continue  # overlapping subtrees
                entry = self._capture(rel, abs_path, verified)
                if entry is not None:
                    found[rel] = entry
        entries = tuple(found[k] for k in sorted(found))

        fingerprint = tree_fingerprint(wanted, excluded, entries)
        snapshot_id = f"{when:%Y%m%dT%H%M%S}.{when.microsecond:06d}Z-{fingerprint}"
        manifest = self._manifest(header, snapshot_id=snapshot_id, entries=entries)

        data = manifest_bytes(manifest)
        name = self._manifest_path(snapshot_id).name
        self._check_publishable(manifest, data)
        with self._store_dir(("manifests",), create=True) as manifests:
            assert manifests is not None
            # Same microsecond, same tree. Identical bytes make this a no-op;
            # anything else would be a silent overwrite of an immutable file.
            # The existing one is read as every store file is (M11-11): never
            # blocking, never through a link, and no more than one byte past
            # the length it would need to be identical; and (M11-18) as an
            # entry of the `manifests/` just checked, by its descriptor.
            try:
                existing = read_regular_file(
                    manifests.at(name), limit=len(data), dir_fd=manifests.fd
                )
            except FileNotFoundError:
                existing = None
            except OSError as exc:
                raise SnapshotExistsError(
                    f"a different manifest already exists for {snapshot_id}: {exc}"
                ) from exc
            if existing is not None:
                if existing == data:
                    return manifest
                raise SnapshotExistsError(f"a different manifest already exists for {snapshot_id}")
            self._write_manifest(manifests, name, data)
        return manifest

    @staticmethod
    def _manifest(
        header: dict[str, Any], *, snapshot_id: str, entries: tuple[Entry, ...]
    ) -> Manifest:
        """Build a manifest; a model rejection is a `SnapshotError`, never pydantic's."""
        try:
            return Manifest(id=snapshot_id, entries=entries, **header)
        except ValidationError as exc:
            raise SnapshotError(f"cannot describe this snapshot: {exc}") from exc

    @staticmethod
    def _entry(abs_path: Path, **fields: Any) -> Entry:
        """Build an entry; a model rejection is a `SnapshotError` naming the file on disk."""
        try:
            return Entry(**fields)
        except ValidationError as exc:
            raise SnapshotError(f"cannot record {abs_path}: {exc}") from exc

    def _refuse_overlap(self, root: Path) -> None:
        """Refuse when either tree contains the other (L1).

        Spelling proves nothing on a case-insensitive or normalising volume,
        so directories are compared by identity (`samefile`: device and
        inode) along each side's resolved ancestor chain. The string check
        stays as a second net for paths that do not exist yet.
        """
        store = self.path.resolve()
        source = root.resolve()
        overlap = store == source or _same_dir(store, source)
        # The store may not exist yet; its existing ancestors do.
        overlap = overlap or _strictly_holds(source, store) or _strictly_holds(store, source)
        if overlap:
            raise _overlap_error(store, source)
        self._refuse_inside_any_install()

    def refuse_holding(self, root: Path) -> None:
        """Refuse, before anything is created, a store that is an ancestor of
        `root`: the one overlap `create` refuses that a store outside every
        install can have. Raises `StoreLocationError` with `create`'s message.

        Reads only, and creates nothing, here or at the store. The comparison
        is `create`'s: resolved paths, by spelling and by directory identity
        along `root`'s ancestor chain. A store inside `root`, or `root`
        itself, is not this method's case; `create` refuses it, and when
        `root` is an install so does the inside-any-install rule
        (`guard.store_lock(create=True)`) before creating anything. Added
        2026-09-28 (M10-19) so `wowlab snap create` can refuse the ancestor
        case before it takes the store lock, which creates `<store>/lock`.

        A store inside any install is not this method's case either, even
        when it also holds `root`: that refusal is L1's and the gate's
        (`guard.store_lock(create=True)`, exit 3 in the CLI), so this method
        steps aside and leaves it to them rather than masking it as an overlap.
        """
        try:
            self._refuse_inside_any_install()
        except StoreLocationError:
            return
        store = self.path.resolve()
        source = Path(root).absolute().resolve()
        if _strictly_holds(store, source):
            raise _overlap_error(store, source)

    def _refuse_inside_any_install(self) -> None:
        """Refuse a store inside any install, not only the one being captured
        (L1), before anything is created. The rule is `guard`'s, called here
        rather than copied (docs/LAB_PLAN.md §6.10, amended 2026-09-23;
        M10-17): the store is resolved (following links and junctions), and
        it and every existing ancestor are examined. A directory holding an
        entry named `.build.info` or `.flavor.info` (of any kind) is an
        install, and so is one that cannot be examined."""
        # Imported here: `guard` imports this module, so a module-level import
        # would be circular. `guard` is fully loaded by the time a store is used.
        from wowlab_core import guard

        try:
            guard._refuse_inside_any_install(self.path, "the store")
        except guard.GuardError as exc:
            raise StoreLocationError(str(exc)) from exc

    @staticmethod
    def _refuse_symlinked_parent(root: Path, subtree: str) -> None:
        """`lstat` protects only the last component; check the ones before it."""
        current = root
        for part in PurePosixPath(subtree).parts[:-1]:
            current = current / part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                return  # nothing there; the walk will find nothing either
            except (OSError, ValueError) as exc:
                raise SnapshotError(f"cannot inspect {current}: {exc}") from exc
            if stat.S_ISLNK(mode):
                raise SnapshotError(
                    f"subtree {subtree!r} passes through the symlink {current}; "
                    "symlinks are never followed"
                )
            if stat.S_ISDIR(mode) and _is_link_like_dir(current):
                raise SnapshotError(
                    f"subtree {subtree!r} passes through the junction or directory link "
                    f"{current}; these, like symlinks, are never followed"
                )

    def _walk(
        self, root: Path, subtree: str, excluded: Sequence[str]
    ) -> Iterator[tuple[str, Path]]:
        """Yield `(relative posix path, absolute path)` for every non-directory."""
        if any(_is_under(subtree, x) for x in excluded):
            return
        self._refuse_symlinked_parent(root, subtree)
        start = root if subtree == "." else root / subtree
        try:
            st = start.lstat()
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            raise SnapshotError(f"cannot inspect {start}: {exc}") from exc
        if not stat.S_ISDIR(st.st_mode) or _is_link_like_dir(start):
            yield subtree, start  # `_capture` records a junction as a link
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
                if child.is_dir(follow_symlinks=False) and not _is_link_like_dir(child):
                    stack.append((rel, Path(child.path)))
                else:
                    yield rel, Path(child.path)

    def _capture(self, rel: str, abs_path: Path, verified: set[str]) -> Entry | None:
        # Hold the path to the model before any byte is stored: a name the
        # manifest cannot carry fails here, typed, with no object written.
        self._entry(abs_path, path=rel)
        try:
            st = abs_path.lstat()
            if stat.S_ISLNK(st.st_mode):
                return self._entry(
                    abs_path,
                    path=rel,
                    kind="symlink",
                    mode=stat.S_IMODE(st.st_mode),
                    mtime_ns=st.st_mtime_ns,
                    target=str(abs_path.readlink()),
                )
            # A junction or other directory link is recorded as a symlink is:
            # its target, never followed.
            if stat.S_ISDIR(st.st_mode) and _is_link_like_dir(abs_path):
                return self._entry(
                    abs_path,
                    path=rel,
                    kind="symlink",
                    mode=stat.S_IMODE(st.st_mode),
                    mtime_ns=st.st_mtime_ns,
                    target=_dir_link_target(abs_path),
                )
            if not stat.S_ISREG(st.st_mode):
                return None
            # Opened without blocking or following a link, and held to a
            # regular file by fstat (M11-11): a FIFO or device swapped in
            # after the lstat is skipped, as one found by the lstat is.
            fd, st = open_regular_file(abs_path)
        except FileNotFoundError:
            return None  # removed while we walked
        except UnsafeReadError:
            return None  # no longer a regular file: replaced while we walked
        except OSError as exc:
            raise SnapshotError(f"cannot read {abs_path}: {exc}") from exc
        try:
            with _checked_stream(fd) as handle:
                digest, size = self._store_stream(handle, verified)
        except OSError as exc:
            raise SnapshotError(f"cannot read {abs_path}: {exc}") from exc
        return self._entry(
            abs_path,
            path=rel,
            kind="file",
            sha256=digest,
            size=size,
            mode=stat.S_IMODE(st.st_mode),
            mtime_ns=st.st_mtime_ns,
        )

    def _store_stream(self, stream: BinaryIO, verified: set[str]) -> tuple[str, int]:
        """Hash a readable, seekable binary file; store it if it is new.

        Pass one hashes. An object already stored under that hash is reused
        only after it re-hashes to its name (once per `create`, tracked in
        `verified`); a damaged one is rewritten. Only when the object is
        missing or damaged does pass two read again to compress, and the
        entry then describes what pass two read, so the manifest always
        names bytes that are really in the store.
        """
        hasher = hashlib.sha256()
        size = 0
        while chunk := stream.read(_CHUNK):
            hasher.update(chunk)
            size += len(chunk)
        digest = hasher.hexdigest()
        if self._reusable(digest, verified):
            return digest, size

        stream.seek(0)
        # `manifests/` exists before any object does, so a store holding
        # objects without it is broken, and `gc` refuses it (M11-15).
        with self._store_dir(("manifests",), create=True):
            pass
        hasher = hashlib.sha256()
        size = 0
        compressor = zlib.compressobj(6)
        with self._staged("obj") as staged:
            while chunk := stream.read(_CHUNK):
                hasher.update(chunk)
                size += len(chunk)
                staged.out.write(compressor.compress(chunk))
            staged.out.write(compressor.flush())
            digest = hasher.hexdigest()
            # The file may have changed between the passes, so ask again.
            if not self._reusable(digest, verified):
                with self._store_dir(("objects", digest[:2]), create=True) as shard:
                    assert shard is not None
                    self._publish(staged, shard, digest[2:])  # new, or healing a damaged one
                verified.add(digest)
        return digest, size

    def _reusable(self, digest: str, verified: set[str]) -> bool:
        """True when a sound object for `digest` is already in the store.

        `objects/` and the shard are first checked not to be links (M11-18):
        a link there refuses the whole `create` (`SnapshotError`), so an
        object is never reused from, nor written to, a directory outside the
        store. The object is opened by its path and then held to being the
        entry of that checked shard (looked up through the shard's
        descriptor, POSIX) with the same device and inode, so a shard
        swapped for a link after the check is not reused either.

        The object is re-hashed through one descriptor (never through a
        link, never blocking: M11-11) and its mtime freshened, so a
        concurrent gc grace period sees it, on that same descriptor where the
        platform can (`os.utime(fd)`, M11-15): a link swapped in at the path
        after the re-hash is never followed. Where it cannot (Windows, where
        `os.utime` takes no descriptor), the path is `utime`d only after
        `lstat` shows it is still the opened file and not a link, while the
        descriptor is still open; `os.open` there grants no delete sharing,
        so the file should not be renamed or removed in between, but that
        rests on the platform, not on this check. Either way, a
        path that no longer names the checked file is not reused, and the
        caller rewrites it (a rename replaces a link, never follows it).
        """
        if digest in verified:
            return True
        existing = self.object_path(digest)
        with self._store_dir(("objects", digest[:2]), create=False) as shard:
            if shard is None:
                return False  # no shard, so no object
            try:
                fd, st = open_regular_file(existing)
            except OSError:
                return False  # missing, a link, or not a regular file
            try:
                if not self._entry_is(shard, existing.name, st):
                    return False  # not the entry of the checked shard
                if _hash_object_fd(fd) != digest:
                    return False
                if os.utime in os.supports_fd:
                    os.utime(fd)
                elif not self._still_names(existing, st):
                    return False
                else:
                    os.utime(existing)
                if not self._entry_is(shard, existing.name, st):
                    return False
            finally:
                os.close(fd)
        verified.add(digest)
        return True

    @staticmethod
    def _still_names(path: Path, st: os.stat_result) -> bool:
        """`path` is, by `lstat`, not a link and the file `st` describes."""
        try:
            now = os.lstat(path)
        except OSError:
            return False
        return not _is_link_stat(now) and _file_identity(now) == _file_identity(st)

    @staticmethod
    def _entry_is(where: _StoreDir, name: str, st: os.stat_result) -> bool:
        """The entry `name` of the checked directory `where` is, by `lstat`
        (through `where`'s descriptor where there is one), not a link and the
        file `st` describes (M11-18). Without a descriptor, the directories
        on the way are first held again to their checked `lstat`."""
        if not where.still_checked():
            return False
        try:
            now = os.stat(where.at(name), dir_fd=where.fd, follow_symlinks=False)
        except OSError:
            return False
        return not _is_link_stat(now) and _file_identity(now) == _file_identity(st)

    def _check_publishable(self, manifest: Manifest, data: bytes) -> None:
        """Hold the bytes about to be written to everything `_load` demands."""
        try:
            loaded = self._parse(manifest.id, data)
        except ManifestIntegrityError as exc:
            raise SnapshotError(f"refusing to write a manifest that would not load: {exc}") from exc
        if loaded != manifest:
            raise SnapshotError(f"refusing to write {manifest.id}: it does not round-trip")

    def _write_manifest(self, manifests: _StoreDir, name: str, data: bytes) -> None:
        """Write `data` as the manifest file `name` in the checked `manifests/`:
        staged under `tmp/` and moved into place in one rename (M11-18)."""
        with self._staged("manifest") as staged:
            staged.out.write(data)
            self._publish(staged, manifests, name)

    # -- writing into the store's own directories (M11-18) ------------------
    #
    # `create` (and `set_label`) write only into `tmp/`, `manifests/` and an
    # object shard under `objects/`. The names are fixed hex digests and
    # manifest ids, so a link planted at one of those directories could not
    # make the store overwrite an arbitrary file, but it would put a
    # snapshot's objects or manifest outside the store, where `verify` (which
    # follows no link) reports them missing while a read through the link
    # still finds them. So each of those directories is opened with
    # `O_DIRECTORY | O_NOFOLLOW` relative to its parent's descriptor, checked
    # against the `lstat` taken just before, and written through that
    # descriptor: the staged file is created in `tmp/` by descriptor and moved
    # into place with `os.replace(..., src_dir_fd=, dst_dir_fd=)`. A directory
    # that is a link, or not a directory, refuses the write. The store root
    # itself is opened as named: the owner chooses where the store lives, and
    # it may sit below a linked directory (`/var` on macOS, say). Where there
    # are no directory descriptors (Windows), each directory is checked by
    # `lstat` (a junction or other name-surrogate reparse point counts as a
    # link) when it is first used and again just before the rename and the
    # reuse decision; the window between that last check and the operation
    # is narrowed there, not closed.
    #
    # What this does not cover: a directory renamed away (not replaced by a
    # link) after its descriptor was opened is written into where it now is.
    # One process at a time writes to a store (the module's concurrency
    # note, and guard's store lock); this guards against a link planted
    # beforehand, not against a concurrent writer moving the store around.

    def _refuse_linked_store_dirs(self) -> None:
        """Refuse, before `create` walks anything, a store whose `objects/`,
        `manifests/` or `tmp/` is a link or not a directory (M11-18). Missing
        ones are fine; `create` makes them."""
        for part in ("objects", "manifests", "tmp"):
            with self._store_dir((part,), create=False):
                pass

    @contextlib.contextmanager
    def _store_dir(self, parts: Sequence[str], *, create: bool) -> Iterator[_StoreDir | None]:
        """The store directory `self.path / parts...`, every component below
        the store root checked not to be a link and to be a directory; each is
        made first when `create`. None when one is missing and not `create`.
        `SnapshotError` when one is a link or not a directory, or cannot be
        inspected. Any descriptor is closed on the way out."""
        if not _WRITE_BY_DIR_FD:
            yield self._checked_dir_path(parts, create=create)
            return
        try:
            if create:
                self.path.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        except FileNotFoundError:
            if create:
                raise SnapshotError(f"the store {self.path} vanished as it was created") from None
            yield None
            return
        except OSError as exc:
            raise SnapshotError(f"cannot open the store {self.path}: {exc}") from exc
        found: _StoreDir | None = None
        path = self.path
        try:
            for part in parts:
                path = path / part
                child = self._open_child_dir(fd, part, path, create=create)
                if child is None:
                    break
                os.close(fd)
                fd = child
            else:
                found = _StoreDir(path, fd)
            yield found
        finally:
            os.close(fd)

    @staticmethod
    def _open_child_dir(parent_fd: int, name: str, path: Path, *, create: bool) -> int | None:
        """A descriptor on the directory `name` of `parent_fd`, opened with
        `O_DIRECTORY | O_NOFOLLOW` and holding the device and inode its
        `lstat` showed; see `_store_dir`."""
        try:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(name, dir_fd=parent_fd)  # never follows a link at `name`
            st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            if create:
                raise SnapshotError(f"{path} vanished as it was created") from None
            return None
        except OSError as exc:
            raise SnapshotError(f"cannot inspect {path}: {exc}") from exc
        if _is_link_stat(st) or not stat.S_ISDIR(st.st_mode):
            raise _linked_store_dir(path)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | _O_NOFOLLOW, dir_fd=parent_fd)
        except OSError as exc:  # swapped for a link or a file since the lstat
            raise _linked_store_dir(path) from exc
        if _file_identity(os.fstat(fd)) != _file_identity(st):
            os.close(fd)
            raise _linked_store_dir(path)
        return fd

    def _checked_dir_path(self, parts: Sequence[str], *, create: bool) -> _StoreDir | None:
        """`_store_dir` where there are no directory descriptors (Windows):
        each component is checked by `lstat` just before it is used."""
        path = self.path
        checked: list[tuple[Path, os.stat_result]] = []
        try:
            if create:
                path.mkdir(parents=True, exist_ok=True)
            elif not path.is_dir():
                return None
            for part in parts:
                path = path / part
                if create:
                    with contextlib.suppress(FileExistsError):
                        path.mkdir()  # never follows a link at `path`
                try:
                    st = os.lstat(path)
                except FileNotFoundError:
                    if create:
                        raise SnapshotError(f"{path} vanished as it was created") from None
                    return None
                if _is_link_stat(st) or not stat.S_ISDIR(st.st_mode):
                    raise _linked_store_dir(path)
                checked.append((path, st))
        except OSError as exc:
            raise SnapshotError(f"cannot inspect {path}: {exc}") from exc
        return _StoreDir(path, None, tuple(checked))

    @contextlib.contextmanager
    def _staged(self, prefix: str) -> Iterator[_Staged]:
        """A new file `tmp/<prefix>-<uuid>` open for writing, created in the
        checked `tmp/` exclusively and without following a link. It is
        removed on the way out unless `_publish` moved it into place."""
        with self._store_dir(("tmp",), create=True) as tmp:
            assert tmp is not None
            name = f"{prefix}-{uuid.uuid4().hex}"
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | _O_NOFOLLOW
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOCTTY", 0)
            )
            try:
                fd = os.open(tmp.at(name), flags, 0o666, dir_fd=tmp.fd)
            except OSError as exc:
                raise SnapshotError(f"cannot write in {tmp.path}: {exc}") from exc
            try:
                out = os.fdopen(fd, "wb")
            except BaseException:
                os.close(fd)
                raise
            try:
                with out:
                    yield _Staged(tmp, name, out)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp.at(name), dir_fd=tmp.fd)

    @staticmethod
    def _publish(staged: _Staged, dest: _StoreDir, name: str) -> None:
        """Flush, sync and close the staged file, then move it to `name` in
        the checked `dest` in one rename (replacing an entry there, never
        following one), through both directories' descriptors."""
        staged.out.flush()
        os.fsync(staged.out.fileno())
        staged.out.close()
        for where in (staged.tmp, dest):
            if not where.still_checked():  # always true with descriptors
                raise _linked_store_dir(where.path)
        os.replace(
            staged.tmp.at(staged.name),
            dest.at(name),
            src_dir_fd=staged.tmp.fd,
            dst_dir_fd=dest.fd,
        )

    # -- read --------------------------------------------------------------

    def _manifest_files(self) -> tuple[Path, ...]:
        """Every `*.json` under `manifests/` that is a regular file or a link,
        judged by `lstat` (M11-15): a link is listed but never followed, so
        `_load` refuses it and it is reported as a manifest that does not
        load (and `gc` refuses to run) rather than silently dropped. Other
        non-regular entries are skipped, as before."""
        if not self.manifests_dir.is_dir():
            return ()
        found: list[Path] = []
        with os.scandir(self.manifests_dir) as it:
            for item in it:
                if not item.name.endswith(_MANIFEST_SUFFIX):
                    continue
                try:
                    st = os.lstat(item.path)
                except FileNotFoundError:
                    continue  # removed while we listed
                if stat.S_ISREG(st.st_mode) or _is_link_stat(st):
                    found.append(Path(item.path))
        return tuple(sorted(found))

    def _load(self, path: Path) -> Manifest:
        """Read a manifest file and hold it to its own id. A link, a FIFO or
        anything else not a regular file does not load (M11-11)."""
        try:
            data = read_regular_file(path)
        except OSError as exc:
            raise ManifestIntegrityError(f"manifest {path.name} does not load: {exc}") from exc
        return self._parse(path.name.removesuffix(_MANIFEST_SUFFIX), data)

    @staticmethod
    def _parse(name: str, data: bytes) -> Manifest:
        """Validate manifest bytes that are, or are about to be, `<name>.json`.

        Entry paths are held to the root by the `Entry` model itself, so a
        manifest naming `../x`, an absolute path or a NUL never loads.
        """
        path = Path(name + _MANIFEST_SUFFIX)
        try:
            manifest = Manifest.model_validate_json(data)
        except (ValidationError, ValueError) as exc:
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
        """Every snapshot, oldest first.

        Raises `ManifestIntegrityError` if any file under `manifests/` fails
        to load — a store with one damaged manifest does not silently drop
        it from the listing. `verify()` degrades per-object instead; reach
        for it to find which snapshots are still restorable when this
        raises.
        """
        return tuple(self._load(p) for p in self._manifest_files())

    def list_lenient(self) -> SnapshotListing:
        """Every snapshot that loads, oldest first, and each manifest file
        that does not, named with the reason.

        The lenient sibling of `list()`: one damaged manifest does not hide
        the others, and it is never dropped silently either, since it is
        named in `invalid`. Reads only; a store that does not exist is an
        empty listing.
        """
        manifests: list[Manifest] = []
        invalid: list[InvalidManifest] = []
        for path in self._manifest_files():
            try:
                manifests.append(self._load(path))
            except ManifestIntegrityError as exc:
                invalid.append(InvalidManifest(name=path.name, reason=str(exc)))
        return SnapshotListing(manifests=tuple(manifests), invalid=tuple(invalid))

    def read_object(self, sha256: str, *, size: int | None = None) -> bytes:
        """Decompressed content of one object, checked against its name.

        `size` is the size the manifest entry naming this object records
        (M11-15). Inflation then stops at `size + 1` bytes: an object that
        would inflate past `size` is refused (`ObjectCorruptError`) before
        more than that is ever held, so a small object crafted to inflate
        without end cannot exhaust memory ahead of the hash check. Every
        caller in this package passes it; `None` leaves inflation unbounded,
        as it was before M11-15, and is for a caller holding a bare digest.
        The compressed file is read a chunk at a time, never whole.
        """
        path = self.object_path(sha256)
        if size is not None and size < 0:
            raise ObjectCorruptError(f"object {sha256}: the recorded size {size} is negative")
        try:
            # Never blocking and never through a link (M11-11): an object path
            # is predictable, so a FIFO or link planted there is refused.
            fd, _ = open_regular_file(path)
        except FileNotFoundError as exc:
            raise ObjectCorruptError(f"object {sha256} is missing") from exc
        except OSError as exc:
            raise ObjectCorruptError(f"object {sha256} cannot be read: {exc}") from exc
        try:
            data, clean = _inflate_fd(fd, size)
        except _InflatesTooFarError as exc:
            raise ObjectCorruptError(
                f"object {sha256} inflates past its recorded size of {size} bytes"
            ) from exc
        except OSError as exc:
            raise ObjectCorruptError(f"object {sha256} cannot be read: {exc}") from exc
        except zlib.error as exc:
            raise ObjectCorruptError(f"object {sha256} does not decompress: {exc}") from exc
        finally:
            os.close(fd)
        if not clean:
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
        return self.read_object(entry.sha256, size=entry.size)

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

    def list_tree(
        self,
        root: Path,
        subtrees: Iterable[str | PurePath],
        *,
        exclude: Iterable[str | PurePath] = (),
    ) -> tuple[tuple[str, Literal["file", "symlink", "other"]], ...]:
        """Every path `create` would walk under `subtrees` of `root`, less
        `exclude`, sorted, each with its kind by `lstat`: a regular `file`, a
        `symlink` (on Windows also a junction or other directory link, never
        followed), or `other` (what `create` ignores: a FIFO, a socket).

        Reads directory listings and `lstat` only: it opens no file, stores
        nothing and touches neither the store nor `root` (L1). Subtrees and
        excludes are held to `create`'s rules, and a subtree reached through
        a link below the root is refused as `create` refuses it. Added
        2026-09-28 (M11-08) so `profiles` can find the files added under a
        profile's subtrees since it was saved without taking a snapshot.
        """
        root = Path(root).absolute()
        wanted = sorted({_normalize_rel(s, "subtree") for s in subtrees})
        excluded = sorted({_normalize_rel(x, "exclude") for x in exclude})
        found: dict[str, Literal["file", "symlink", "other"]] = {}
        for subtree in wanted:
            for rel, abs_path in self._walk(root, subtree, excluded):
                if rel in found:
                    continue
                try:
                    st = abs_path.lstat()
                except FileNotFoundError:
                    continue  # removed while we walked
                except OSError as exc:
                    raise SnapshotError(f"cannot inspect {abs_path}: {exc}") from exc
                if stat.S_ISLNK(st.st_mode) or (
                    stat.S_ISDIR(st.st_mode) and _is_link_like_dir(abs_path)
                ):
                    found[rel] = "symlink"
                elif stat.S_ISREG(st.st_mode):
                    found[rel] = "file"
                else:
                    found[rel] = "other"
        return tuple(sorted(found.items()))

    # -- the one mutation --------------------------------------------------

    def set_label(self, snapshot_id: str, label: str) -> Manifest:
        """Rewrite the label of exactly this snapshot; nothing else changes.

        Guarded by id: the full id is required (no prefix), the manifest on
        disk must carry that id and still match its fingerprint, and the
        replacement differs from it in the `label` field alone. It is written
        as `create` writes a manifest (M11-18): never through a linked
        `manifests/` or `tmp/`, which are refused (`SnapshotError`).
        """
        path = self._manifest_path(snapshot_id)
        if not path.is_file():
            raise SnapshotNotFoundError(f"no snapshot {snapshot_id!r}")
        current = self._load(path)
        updated = current.model_copy(update={"label": label})
        if updated.model_dump(exclude={"label"}) != current.model_dump(exclude={"label"}):
            raise ManifestIntegrityError("label write would change another field")
        data = manifest_bytes(updated)
        self._check_publishable(updated, data)
        with self._store_dir(("manifests",), create=False) as manifests:
            if manifests is None:
                raise SnapshotNotFoundError(f"no snapshot {snapshot_id!r}")
            self._write_manifest(manifests, path.name, data)
        return updated

    # -- health ------------------------------------------------------------

    def _object_files(self) -> Iterator[_ObjectFile]:
        """Every entry under `objects/`, judged by `lstat`: nothing is
        followed (M11-15). A shard that is a link (a symlink, or on Windows a
        junction or other name-surrogate reparse point) or not a directory is
        one stray entry and is never listed into. An item gets its SHA-256
        name when it is well named and a regular file or a link (so `verify`
        reports a linked object by name; its re-hash refuses the link).
        `objects/` itself as a link yields one stray entry and nothing under
        it."""
        try:
            top = os.lstat(self.objects_dir)
        except FileNotFoundError:
            return
        if _is_link_stat(top):
            yield _ObjectFile(None, self.objects_dir, top, None, None)
            return
        if not stat.S_ISDIR(top.st_mode):
            return
        with os.scandir(self.objects_dir) as it:
            shards = sorted(it, key=lambda d: d.name)
        for shard in shards:
            shard_path = Path(shard.path)
            try:
                shard_st = os.lstat(shard_path)
            except FileNotFoundError:
                continue  # removed while we listed
            if _is_link_stat(shard_st) or not stat.S_ISDIR(shard_st.st_mode):
                yield _ObjectFile(None, shard_path, shard_st, top, None)
                continue
            try:
                with os.scandir(shard_path) as it:
                    items = sorted(it, key=lambda d: d.name)
            except FileNotFoundError:
                continue
            for item in items:
                item_path = Path(item.path)
                try:
                    st = os.lstat(item_path)
                except FileNotFoundError:
                    continue
                name = shard.name + item.name
                ok = (
                    (stat.S_ISREG(st.st_mode) or _is_link_stat(st))
                    and len(shard.name) == 2
                    and _SHA_RE.match(name)
                )
                yield _ObjectFile(name if ok else None, item_path, st, top, shard_st)

    @staticmethod
    def _rehash(path: Path) -> str | None:
        """The SHA-256 of an object's decompressed bytes; None when it is
        missing, is not a regular file (a link is not followed, a FIFO never
        blocks: M11-11), or does not decompress cleanly."""
        try:
            fd, _ = open_regular_file(path)
        except OSError:
            return None
        try:
            return _hash_object_fd(fd)
        finally:
            os.close(fd)

    def verify(self) -> VerifyReport:
        """Re-hash every object and hold every manifest to its id and objects."""
        corrupt: list[str] = []
        present: set[str] = set()
        checked = 0
        for obj in self._object_files():
            checked += 1
            if obj.name is None:
                corrupt.append(
                    # `objects/` itself as a link (M11-18): named, not ".".
                    OBJECTS_DIR_ENTRY
                    if obj.path == self.objects_dir
                    else obj.path.relative_to(self.objects_dir).as_posix()
                )
                continue
            present.add(obj.name)
            if self._rehash(obj.path) != obj.name:
                corrupt.append(obj.name)

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

        Nothing is followed and nothing is deleted through a link (M11-15).
        The listing is by `lstat`: a link, or anything that is not a regular
        file, anywhere under `objects/` (a shard, an item, `objects/`
        itself) is never a candidate, never listed into, and is named in
        `skipped`. A delete goes through descriptors on `objects/` and the
        shard opened without following a link, each checked to be the
        directory the listing saw, and removes the item only if it is still
        the regular file listed (POSIX); where the platform has no such
        descriptors (Windows), the item is first renamed into the store's
        `tmp/` and deleted there only if it is the file listed, else renamed
        back (`_remove_object_by_rename`). An object whose path changed since
        the listing is not deleted and is named in `skipped`.

        Refuses outright (`ManifestIntegrityError`) when `manifests/` is a
        link (on Windows also a junction) or not a directory, or is missing
        while `objects/` holds anything: a dangling link or an unmounted
        volume would otherwise make every object look unreferenced.
        """
        self._refuse_gc_without_manifests()
        referenced: set[str] = set()
        for manifest in self.list():  # raises ManifestIntegrityError
            referenced.update(e.sha256 for e in manifest.entries if e.sha256 is not None)

        cutoff = datetime.now(UTC).timestamp() - grace_seconds if grace_seconds > 0 else None
        candidates: list[tuple[str, _ObjectFile]] = []
        skipped: list[str] = []
        for obj in self._object_files():
            if _is_link_stat(obj.st) or not stat.S_ISREG(obj.st.st_mode):
                skipped.append(obj.path.relative_to(self.path).as_posix())
                continue
            if obj.name is None or obj.name in referenced or obj.shard_st is None:
                continue
            if cutoff is not None and obj.st.st_mtime > cutoff:
                continue
            candidates.append((obj.name, obj))
        candidates.sort(key=lambda c: c[0])

        removed: list[str] = []
        removed_bytes = 0
        if not dry_run:
            for name, obj in candidates:
                if self._remove_object(obj):
                    removed.append(name)
                    removed_bytes += obj.st.st_size
                else:
                    skipped.append(obj.path.relative_to(self.path).as_posix())
        return GcReport(
            dry_run=dry_run,
            unreferenced=tuple(name for name, _ in candidates),
            unreferenced_bytes=sum(obj.st.st_size for _, obj in candidates),
            removed=tuple(removed),
            removed_bytes=removed_bytes,
            skipped=tuple(sorted(skipped)),
        )

    def _remove_object(self, obj: _ObjectFile) -> bool:
        """Delete one listed object without following any link on the way;
        False, with nothing deleted, when `objects/`, its shard or the item
        is no longer what the listing saw. Only an error from the delete
        itself propagates."""
        assert obj.top_st is not None and obj.shard_st is not None
        shard = obj.path.parent
        if not _UNLINK_BY_DIR_FD:
            return self._remove_object_by_rename(obj)
        flags = os.O_RDONLY | os.O_DIRECTORY | _O_NOFOLLOW
        try:
            top_fd = os.open(self.objects_dir, flags)
        except OSError:
            return False
        try:
            if _file_identity(os.fstat(top_fd)) != _file_identity(obj.top_st):
                return False
            try:
                shard_fd = os.open(shard.name, flags, dir_fd=top_fd)
            except OSError:
                return False
            try:
                if _file_identity(os.fstat(shard_fd)) != _file_identity(obj.shard_st):
                    return False
                try:
                    now = os.stat(obj.path.name, dir_fd=shard_fd, follow_symlinks=False)
                except OSError:
                    return False
                if not stat.S_ISREG(now.st_mode) or _file_identity(now) != _file_identity(obj.st):
                    return False
                # `unlink` never follows the last component, and the shard is
                # held by descriptor, so this removes an entry of the shard.
                os.unlink(obj.path.name, dir_fd=shard_fd)
            finally:
                os.close(shard_fd)
            with contextlib.suppress(OSError):
                os.rmdir(shard.name, dir_fd=top_fd)  # only succeeds when empty
        finally:
            os.close(top_fd)
        return True

    def _remove_object_by_rename(self, obj: _ObjectFile) -> bool:
        """`_remove_object` where there are no directory descriptors (Windows).

        A path is only ever deleted once it is inside the store's own `tmp/`
        and proven to be the file the listing saw: the item is renamed into
        `tmp/` (a rename never follows its last component), the moved entry
        must then have the listing's device and inode and not be a link, and
        only then is it deleted there. Had a shard or `objects/` been swapped
        for a link after the checks, the rename moved whatever was behind it;
        that fails the identity check and is renamed straight back. The
        empty shard is removed the same way. False, with nothing deleted,
        when anything is not what the listing saw; `SnapshotError` if
        something moved into `tmp/` cannot be moved back (it is then named,
        still whole, in `tmp/`).

        `tmp/` itself is checked not to be a link only before the rename
        (`_parking_dir`), and there is no descriptor to hold it by. A `tmp/`
        swapped for a link to an outside directory at that last moment makes
        the rename move gc's own object (the file the listing saw, already
        proven unreferenced) into that outside directory, where it passes
        the identity check and is deleted: nothing but that object is
        deleted, but it is deleted from outside the store (M11-18; POSIX
        never takes this path).

        Leftovers `tmp/gc-<uuid>` get no cleaner, by design (M11-18). One
        exists only after a failed move back, which raises naming it, or a
        crash between the rename and the delete. What it holds is then either
        the store's own unreferenced object or whatever a swapped link made
        the rename move, which may be a file that belongs outside the store:
        deleting it unasked is the delete through a link this method exists
        to prevent. Nothing reads `tmp/` (it is never listed as objects or
        manifests), so a leftover costs only its space until the owner moves
        or deletes it by hand.
        """
        assert obj.top_st is not None and obj.shard_st is not None
        shard = obj.path.parent
        if not (
            self._same_dir_entry(self.objects_dir, obj.top_st)
            and self._same_dir_entry(shard, obj.shard_st)
            and self._still_names(obj.path, obj.st)
        ):
            return False
        parking = self._parking_dir()
        if parking is None:
            return False
        parked = parking / f"gc-{uuid.uuid4().hex}"
        try:
            obj.path.rename(parked)
        except OSError:
            return False
        if not self._still_names(parked, obj.st):
            self._unpark(parked, obj.path)
            return False
        parked.unlink()
        self._remove_empty_shard(shard, obj.shard_st, parking)
        return True

    def _remove_empty_shard(self, shard: Path, expect: os.stat_result, parking: Path) -> None:
        """Remove `shard` if it is empty and still the listed directory, by
        the same rename into `tmp/`, identity check and rename back."""
        try:
            with os.scandir(shard) as it:
                if any(True for _ in it):
                    return
        except OSError:
            return
        if not self._same_dir_entry(shard, expect):
            return
        parked = parking / f"gc-{uuid.uuid4().hex}"
        try:
            shard.rename(parked)
        except OSError:
            return
        if self._same_dir_entry(parked, expect):
            try:
                parked.rmdir()
                return
            except OSError:
                pass  # something arrived in it meanwhile: it goes back
        self._unpark(parked, shard)

    def _parking_dir(self) -> Path | None:
        """The store's `tmp/`, created if needed, when it is a directory and
        not a link; else None."""
        try:
            self.tmp_dir.mkdir(parents=True, exist_ok=True)
            st = os.lstat(self.tmp_dir)
        except OSError:
            return None
        if _is_link_stat(st) or not stat.S_ISDIR(st.st_mode):
            return None
        return self.tmp_dir

    @staticmethod
    def _unpark(parked: Path, original: Path) -> None:
        try:
            parked.rename(original)
        except OSError as exc:
            raise SnapshotError(
                f"gc moved {original} to {parked} to check it before a delete and could "
                f"not move it back ({exc}); nothing was deleted, and it is still at {parked}"
            ) from exc

    def _refuse_gc_without_manifests(self) -> None:
        """`gc` decides what is unreferenced from `manifests/`; refuse when
        that directory cannot be trusted to hold every manifest (M11-15)."""
        where = self.manifests_dir
        try:
            st = os.lstat(where)
        except FileNotFoundError:
            if self._objects_present():
                raise ManifestIntegrityError(
                    f"{where} is missing but the store holds objects; gc refuses rather than "
                    "treat every object as unreferenced. If this store really has no "
                    f"snapshots, create an empty {where} and run gc again"
                ) from None
            return
        except OSError as exc:
            raise ManifestIntegrityError(f"cannot inspect {where}: {exc}; gc refuses") from exc
        if _is_link_stat(st) or not stat.S_ISDIR(st.st_mode):
            raise ManifestIntegrityError(
                f"{where} is a link or not a directory; gc never decides what is "
                "unreferenced from a manifests directory it would have to follow, since a "
                "dangling link or an unmounted volume would make every object look unused"
            )

    def _objects_present(self) -> bool:
        """`objects/` exists and holds anything (or is not a plain directory)."""
        try:
            st = os.lstat(self.objects_dir)
        except FileNotFoundError:
            return False
        if _is_link_stat(st) or not stat.S_ISDIR(st.st_mode):
            return True
        with os.scandir(self.objects_dir) as it:
            return any(True for _ in it)

    @staticmethod
    def _same_dir_entry(path: Path, expect: os.stat_result) -> bool:
        """`path` is, by `lstat`, a directory that is not a link and is the
        one `expect` describes."""
        try:
            now = os.lstat(path)
        except OSError:
            return False
        return (
            stat.S_ISDIR(now.st_mode)
            and not _is_link_stat(now)
            and _file_identity(now) == _file_identity(expect)
        )
