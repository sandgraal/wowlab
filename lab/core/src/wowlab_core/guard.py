"""guard: the one write gate into a game install (docs/LAB_PLAN.md §6.10, M10-11).

Every byte the Lab writes inside an install goes through this module (L2,
ADR-0021). A write happens inside a transaction::

    with guard.transaction(flavor, label="try new keybinds") as tx:
        tx.write("WTF/Config.wtf", data)
        tx.delete("WTF/Account/ACCT/bindings-cache.wtf")
        tx.restore(snapshot_id, paths=["WTF/Config.wtf"])

What entering a transaction does, in this order, before the body runs:

1. The flavor is validated: an absolute path to a directory that is not a
   link, holding a regular `.flavor.info`, whose parent (the install root)
   holds a regular `.build.info` (§6.1). A flavor path read back from the
   journal by `undo()` is validated the same way; it is untrusted.
2. The store (a directory path; `None` means `snapshot.default_store_path()`)
   must not overlap the install in either direction (L1), and neither may
   the two lock files nor `locks/` (below), compared after resolving. None
   of the store, `<store>/lock`, `locks/` and the install lock may be inside
   any install at all (§6.10 as amended 2026-09-23): each is resolved, and it
   and every existing ancestor are examined; a directory holding an entry
   named `.build.info` or `.flavor.info` is an install, and so is one that
   cannot be examined. All of this runs before anything is created.
3. One writer at a time (§6.10 as amended 2026-09-22, item 1): the store
   lock `<store>/lock` is taken, then the install lock
   `<user data dir>/locks/<key>.lock`, `<key>` the SHA-256 hex of
   `"<st_dev>:<st_ino>"` of the validated install root, so every spelling of
   one install (a symlink, a case or Unicode variant, a junction) maps to one
   lock. Both are exclusive and non-blocking: `fcntl.flock(LOCK_EX |
   LOCK_NB)` on POSIX; on Windows `msvcrt.locking(LK_NBLCK, 1)` on the one
   byte at offset 2^30, far past the end of the empty file, because Windows
   byte-range locks are mandatory and a lock at offset 0 would fail every
   read of the file (the same offset unlocks; nothing is written through the
   descriptor).
   A lock this process already holds is refused without asking the system,
   so a nested transaction is refused too. A held lock is `GuardBusyError`;
   any other failure to open or lock is a plain `GuardError`; nothing ever
   proceeds unlocked. Lock files are opened `O_CREAT | O_RDWR | O_NOFOLLOW`
   (on Windows a link or other reparse point is refused before the open and
   the opened file checked against the path after it), must be regular files
   by `fstat` with one link (a hard link is refused), and are never
   truncated, written or deleted. They are held until the transaction exits,
   whatever way it exits, and the system drops them when the process ends. A
   forked child that unwinds the parent's `with` neither unlocks, rolls back
   nor journals: it only closes its copies of the descriptors. The exclusion
   covers the processes of one OS user that share one user data directory on
   one machine.
4. `wowlab_core.process` is asked, with its default probe, about the install
   root, the flavor folder and the executable names found in the flavor
   folder. `running`, `unknown`, and any exception count as running (§6.7 as
   amended).
5. A pre-write snapshot of the flavor's `WTF/`, `Interface/` and `Fonts/` is
   taken (§6.9 defaults; `Interface/` holds `AddOns/` and the loose overrides).
6. A journal record naming that snapshot is written under the store, in
   `journal/`, and fsynced (journal format 2; format-1 records still read).
   The journal is first read in full, under the locks and before the
   snapshot: one that cannot be (an I/O error, a damaged record) is a
   `GuardError` with nothing written, since `undo()` could not reverse the
   change. A path read from a journal record or a manifest is looked up
   only when it is a local absolute path (absolute, not `\\\\` or `//`, a
   drive letter on Windows); anything else names another install.
7. Leftover temp files are cleaned up (item 2): the temp paths named by this
   flavor's earlier records, from the most recent one whose cleanup finished
   (that one included), are removed if the name is exactly guard's temp
   pattern, the path passes the write rules below and the walk finds a
   regular file there. A path that fails a lexical rule is never looked up.
   Each removal is journaled and fsynced before the unlink; a path left
   alone is listed in the record's `temps_left`; an absent one nowhere.

A dry run takes the locks (the lock files and their directories are the only
things it writes) and skips 5 to 7.

Each operation then checks the path lexically (the allowlist, executables,
Windows spellings), walks it on disk without following any link, junction or
other reparse point, refuses a name that differs only in case or Unicode form
from one on disk, and refuses a name the file system resolves to something it
does not list (an 8.3 short name). A path whose disk state differs from what
guard expects there is refused with `ChangedSinceSnapshotError` (item 3): at
its first touch the pre-write snapshot's entry (content hash and kind, or
absent against present), later guard's own record of the path (the hash of
its last write there that landed, else the hash read at first touch). The
path, its `before` hash and the name of the temp file about to be created are
written to the journal and fsynced before the install is touched
(write-ahead), so a process killed at any point can still be undone, and its
temp file found, from the journal alone. The new bytes go to that temp file in
the same directory, which is fsynced; the target is then read again and must
still hash to what guard expects (for a create: still be absent), and as the
last step before the call its identity, size and mtime (by `lstat`) must be
what the walk found. Only then is the temp file `os.replace`d over the target
(a create on Windows uses `os.rename`, which never replaces). A delete makes
the same re-check before its unlink. The chain of directories from the
install root down is re-checked (identity, and not a link) before the temp
file is opened, after it is opened (and the temp file must be where it was
created), and again before the rename or unlink.

After a `ChangedSinceSnapshotError` nothing more is written for that path,
every later operation raises `GuardError` without touching the disk, and the
transaction rolls back on exit and cannot commit, even if the caller caught
the error. A path whose only operation was refused is not journaled as
changed, keeps what is on disk, and is left alone by `undo()`.

Mutations are made by absolute path, on every platform, so the same code runs
on Windows (where there are no directory descriptors). The residual window is
the moment between the last re-check and the rename or unlink; a process of
the same user that swaps a directory for a link in exactly that moment can
redirect one rename (whose unguessable temp-file source then does not exist,
so it fails) or one unlink, and a process that rewrites the target in exactly
that moment has its bytes replaced. Such a process can already write the
install directly; the checks exist so links planted in advance, ordinary
races and edits made while a transaction is open never carry a write out of
the install or overwrite bytes no snapshot holds.

On an exception of any kind inside the body, every touched path whose disk
content is still guard's own record of it is put back to the bytes its
journaled `before` hash names, and every directory the transaction created
is removed again. Those bytes come from the pre-write snapshot's object
store, looked up by that hash (the store checks content against the name, so
a manifest cannot substitute other bytes). A path that already holds its
pre-transaction bytes needs nothing; a path someone else changed is left as
it is, named in the record's note and in the error, and the record ends
`rollback_incomplete`. Rollback therefore never overwrites content that no
snapshot holds and this transaction did not write. The record says
`rolled_back` only if all of that succeeded. A `restore` that fails after
changing a path leaves the transaction unable to commit: it rolls back on
exit even if the caller catches the error.

`undo()` is itself a transaction, under the same two locks: it reads the most
recent journal record without a lock only to find its flavor, takes the
store lock (never creating the store), reads the journal again and plans only
from that, takes the install lock of the flavor that record names, and then
restores only the paths the record journaled from its pre-write snapshot. It
refuses before writing anything if a journaled path is not allowed, if the
snapshot holds a link there, or if the snapshot entry disagrees with the
journal's `before`. Snapshot manifests and the journal are untrusted input
at rollback, undo, restore and cleanup time: every path is checked again on
the restoring platform and a link is never created. Restored and undone files
never gain permission bits: an existing file keeps no more than its current
bits and the recorded bits; a recreated one gets its recorded bits less the
umask and never an execute bit.

`store_lock()` takes the store lock alone, for a caller that must keep
transactions off a store while it works on it.

This module never lists processes itself, never reads the environment, and
has no switch that skips the locks, the client check, the snapshot or the
allowlist.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import errno
import hashlib
import json
import logging
import os
import re
import stat
import sys
import threading
import unicodedata
import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from types import TracebackType
from typing import Literal, NoReturn, Protocol

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from wowlab_core import process
from wowlab_core import snapshot as _snapshots
from wowlab_core.snapshot import (
    Entry,
    Manifest,
    SnapshotError,
    SnapshotStore,
)

__all__ = [
    "ChangedSinceSnapshotError",
    "ClientRunningError",
    "GuardBusyError",
    "GuardError",
    "HistoryRecord",
    "PathChange",
    "PathNotAllowedError",
    "PlanItem",
    "history",
    "store_lock",
    "transaction",
    "undo",
]

_log = logging.getLogger(__name__)

# ─── the allowlist ───────────────────────────────────────────────────────────

# Subtrees of a flavor folder the client treats as user configuration
# (ADR-0023). Spelled as the client spells them; a respelling is refused.
_ROOTS = ("WTF", "Interface", "Fonts")
_ADDONS = "AddOns"

# A path with any component ending in one of these, compared
# case-insensitively, is an executable and is never written (L7).
_EXECUTABLE_SUFFIXES = (
    ".exe",
    ".dll",
    ".dylib",
    ".so",
    ".scr",
    ".com",
    ".bat",
    ".cmd",
    ".ps1",
    ".sh",
    ".command",
    ".app",
    ".bundle",
    ".plugin",
    ".framework",
    ".vbs",
    ".js",
    ".wsf",
    ".hta",
    ".msi",
    ".lnk",
    ".cpl",
    ".jar",
)
_VERSIONED_SHARED_LIBRARY = re.compile(r"\.so(\.\d+)+\Z", re.IGNORECASE)

# Device names Windows reserves in every directory, with or without an
# extension. Refused on every platform so a path means the same thing on each.
_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"{device}{n}" for device in ("com", "lpt") for n in (*"0123456789", "¹", "²", "³")}
)

# Characters that are not part of a plain name on Windows (`:` is a drive or
# an alternate data stream, `\\` a separator), refused on every platform.
_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*\\\x7f') | frozenset(chr(n) for n in range(32))
_MAX_NAME_BYTES = 255

# ─── the journal ─────────────────────────────────────────────────────────────

_JOURNAL_DIR = "journal"
_JOURNAL_FORMAT = 2
# Format 1 is what the M10-11 gate wrote: no temp paths, no cleanup.
_READABLE_FORMATS = frozenset({1, _JOURNAL_FORMAT})
_RECORD_NAME = re.compile(r"\A(\d{8})-[0-9a-f]{8}\.json\Z")
_MAX_RECORD_BYTES = 64 << 20
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_TEMP_PREFIX = ".wowlab-"
# The only name the leftover cleanup ever removes (§6.10 amended, item 2):
# what `_temp_name` makes, matched against the name as the directory lists it.
_TEMP_NAME = re.compile(r"\.wowlab-[0-9a-f]{32}\.tmp")
_CHUNK = 1 << 20

# ─── the locks ───────────────────────────────────────────────────────────────

_STORE_LOCK = "lock"
_LOCKS_DIR = "locks"

_State = Literal["open", "committed", "rolled_back", "rollback_incomplete"]
# How a written file gets its permission bits: "keep" the current file's (a
# caller's write), "exact" the recorded ones (rollback, from what this
# transaction read), or "clamp" to the recorded ones (restore and undo, from
# an untrusted snapshot).
_ModeRule = tuple[Literal["keep", "exact", "clamp"], int]
_Identity = tuple[int, int]
# A change restore or undo makes: path, its parts, the content hash to put
# there (None deletes), and the recorded mode the result is clamped to.
_Op = tuple[str, tuple[str, ...], str | None, int]


# ─── errors ──────────────────────────────────────────────────────────────────


class GuardError(Exception):
    """Base of every error this module raises on purpose."""


class ClientRunningError(GuardError):
    """The client is running, or whether it is could not be determined."""


class PathNotAllowedError(GuardError):
    """The path is outside the allowlist, or reaches outside it on disk."""


class GuardBusyError(GuardError):
    """Another wowlab call holds the store or the install (one writer at a
    time). Nothing was written or journaled; try again once it has finished."""


class ChangedSinceSnapshotError(GuardError):
    """A file changed after the pre-write snapshot, or after this transaction
    last read or wrote it. Nothing was written for it, the transaction cannot
    commit, and the caller should retry it."""


class _ChangedUnderneathError(GuardError):
    """A directory on the path changed while a rename or unlink ran: the
    change may have landed somewhere else, so the transaction cannot commit."""


# ─── public data ─────────────────────────────────────────────────────────────


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PathChange(_Frozen):
    """One path a transaction touched: SHA-256 hex before and after, `None`
    for absent. `path` is relative to the flavor folder, as the caller spelled it."""

    path: str
    before: str | None
    after: str | None


class HistoryRecord(_Frozen):
    """One transaction as the journal holds it."""

    id: str
    created_at: str
    label: str
    snapshot_id: str
    """The pre-write snapshot: what `undo()` restores from."""
    flavor_path: str
    state: _State
    """`open` for a transaction that is running or was killed."""
    rolled_back: bool
    """True only when every touched path holds its pre-transaction bytes again."""
    paths: tuple[PathChange, ...]
    created_dirs: tuple[str, ...]
    temps_removed: tuple[str, ...]
    """Leftover temp files of earlier transactions this one removed on enter
    (flavor-relative, as the journal named them)."""
    temps_left: tuple[str, ...]
    """Temp paths earlier records name that this one left alone: they failed a
    check, or their removal failed."""


class PlanItem(_Frozen):
    """One path a transaction changes (or, in a dry run, would change)."""

    path: str
    before: str | None
    after: str | None
    size: int | None
    """Bytes written; `None` for a delete."""


# ─── the journal on disk ─────────────────────────────────────────────────────


def _hash_or_none(value: str | None) -> str | None:
    if value is not None and not _SHA256.match(value):
        raise ValueError(f"not a SHA-256 hex digest: {value!r}")
    return value


class _JournalPath(_Frozen):
    path: str
    before: str | None
    after: str | None

    @field_validator("before", "after")
    @classmethod
    def _hex(cls, value: str | None) -> str | None:
        return _hash_or_none(value)


class _JournalRecord(_Frozen):
    format: int
    id: str
    created_at: str
    label: str
    snapshot_id: str
    install_root: str
    flavor_path: str
    flavor_version: str | None
    state: _State
    paths: tuple[_JournalPath, ...]
    created_dirs: tuple[str, ...]
    note: str | None
    # Format 2. `temps` names every temp file this transaction created (each
    # written here and fsynced before the file existed); the other three are
    # the cleanup it ran on enter.
    temps: tuple[str, ...] = ()
    temps_removed: tuple[str, ...] = ()
    temps_left: tuple[str, ...] = ()
    cleanup_finished: bool = False

    @field_validator("format")
    @classmethod
    def _known_format(cls, value: int) -> int:
        if value not in _READABLE_FORMATS:
            raise ValueError(f"journal format {value}, expected one of {sorted(_READABLE_FORMATS)}")
        return value

    @model_validator(mode="after")
    def _format_1_ran_no_cleanup(self) -> _JournalRecord:
        if self.format == 1 and (
            self.temps or self.temps_removed or self.temps_left or self.cleanup_finished
        ):
            raise ValueError("a format-1 record names no temp paths and ran no cleanup")
        return self


def _journal_dir(store_path: Path) -> Path:
    return store_path / _JOURNAL_DIR


def _load_journal(store_path: Path) -> list[_JournalRecord]:
    """Every record under the store, oldest first. Reads only; a missing store
    or journal is an empty history. A damaged record is an error, never
    skipped: `undo()` acts on the most recent one."""
    directory = _journal_dir(store_path)
    try:
        names = sorted(p.name for p in directory.iterdir())
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise GuardError(f"cannot read the journal in {directory}: {exc}") from exc
    records: list[_JournalRecord] = []
    for name in names:
        if not _RECORD_NAME.match(name):
            continue  # a temp file of an interrupted journal write
        path = directory / name
        try:
            st = path.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_size > _MAX_RECORD_BYTES:
                raise GuardError("not a regular file of a sane size")
            raw = json.loads(path.read_bytes().decode("ascii"))
            record = _JournalRecord.model_validate(raw)
        except (OSError, ValueError, GuardError) as exc:  # ValidationError is a ValueError
            raise GuardError(f"journal record {path} is damaged: {exc}") from exc
        if record.id != name.removesuffix(".json"):
            raise GuardError(f"journal record {path} carries the id {record.id!r}")
        records.append(record)
    return records


def _next_record_id(store_path: Path) -> str:
    directory = _journal_dir(store_path)
    try:
        numbers = [
            int(m.group(1)) for p in directory.iterdir() if (m := _RECORD_NAME.match(p.name))
        ]
    except FileNotFoundError:
        numbers = []
    except OSError as exc:
        raise GuardError(f"cannot read the journal in {directory}: {exc}") from exc
    return f"{max(numbers, default=0) + 1:08d}-{uuid.uuid4().hex[:8]}"


def _write_record(store_path: Path, record: _JournalRecord) -> None:
    """Replace the record's file atomically; the bytes are fsynced before the
    rename and the directory after it, so what the journal says is on disk."""
    directory = _journal_dir(store_path)
    text = json.dumps(record.model_dump(), ensure_ascii=True, sort_keys=True, indent=1)
    data = (text + "\n").encode("ascii")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f".{record.id}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        fd = os.open(tmp, flags, 0o600)
        done = False
        try:
            try:
                _write_all(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            tmp.replace(directory / f"{record.id}.json")
            done = True
        finally:
            if not done:
                with contextlib.suppress(OSError):
                    tmp.unlink()
    except OSError as exc:
        raise GuardError(f"cannot write the journal in {directory}: {exc}") from exc
    _fsync_dir(directory)


def _public(record: _JournalRecord) -> HistoryRecord:
    return HistoryRecord(
        id=record.id,
        created_at=record.created_at,
        label=record.label,
        snapshot_id=record.snapshot_id,
        flavor_path=record.flavor_path,
        state=record.state,
        rolled_back=record.state == "rolled_back",
        paths=tuple(PathChange(path=p.path, before=p.before, after=p.after) for p in record.paths),
        created_dirs=record.created_dirs,
        temps_removed=record.temps_removed,
        temps_left=record.temps_left,
    )


# ─── small helpers ───────────────────────────────────────────────────────────


def _now() -> str:
    when = datetime.datetime.now(datetime.UTC)
    return f"{when:%Y-%m-%dT%H:%M:%S}.{when.microsecond:06d}Z"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fold(text: str) -> str:
    """Comparison key: blind to case and to Unicode normalisation form."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


def _identity(st: os.stat_result) -> _Identity:
    return (st.st_dev, st.st_ino)


def _is_link(st: os.stat_result) -> bool:
    """A symbolic link, or on Windows any reparse point (junctions included:
    `S_ISLNK` is false for those)."""
    attributes = int(getattr(st, "st_file_attributes", 0))
    return stat.S_ISLNK(st.st_mode) or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _fsync_dir(directory: Path) -> None:
    """Make a rename or unlink in `directory` durable. POSIX only; Windows has
    no directory handles to fsync through `os`."""
    if os.name != "posix":
        return
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _store_path(store: object) -> Path:
    if store is None:
        return _snapshots.default_store_path().absolute()
    if not isinstance(store, Path):
        raise GuardError(
            f"store is a directory path, not a {type(store).__name__}; "
            "guard builds its own SnapshotStore"
        )
    return store.absolute()


# ─── one writer at a time ────────────────────────────────────────────────────

# The lock files this process holds, by identity: a lock already held here is
# refused without asking the system (a nested transaction, a second thread).
_HELD_LOCKS: set[_Identity] = set()
_HELD_LOCKS_MUTEX = threading.Lock()

# Windows byte-range locks are mandatory: a lock on a byte a reader asks for
# fails the read. So the one locked byte sits far past the end of the empty
# lock file (the technique SQLite uses). Fixed: guards locking different
# offsets would not exclude each other (§6.10 as reworded 2026-09-23).
_WINDOWS_LOCK_OFFSET = 1 << 30
_INSTALL_MARKERS = (".build.info", ".flavor.info")


def _install_lock_path(place: _Place) -> Path:
    """`<user data dir>/locks/<key>.lock`, the key from the install root's
    identity as guard validated it, never its spelling. The user data
    directory is `wowlab_core.snapshot`'s, read at call time."""
    device, inode = place.root_id
    key = hashlib.sha256(f"{device}:{inode}".encode("ascii")).hexdigest()
    user_data = _snapshots.default_store_path().absolute().parent
    return user_data / _LOCKS_DIR / f"{key}.lock"


def _refuse_inside_any_install(path: Path, what: str) -> None:
    """§6.10 as amended 2026-09-23: `path` (resolved, following links and
    junctions) and every existing ancestor are examined; a directory holding
    an entry named `.build.info` or `.flavor.info` (of any kind) is an
    install, and so is one that cannot be examined. Runs before anything at
    `path` is created."""
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise GuardError(f"cannot resolve {what} {path}: {exc}; refusing it") from exc
    for candidate in (resolved, *resolved.parents):
        try:
            st = candidate.lstat()
        except FileNotFoundError:
            continue  # not created yet
        except (OSError, ValueError) as exc:
            raise GuardError(
                f"{what} {path}: cannot examine {candidate} ({exc}); it counts as an install"
            ) from exc
        if not stat.S_ISDIR(st.st_mode):
            continue
        for marker in _INSTALL_MARKERS:
            try:
                (candidate / marker).lstat()
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as exc:
                raise GuardError(
                    f"{what} {path}: cannot examine {candidate} ({exc}); it counts as an install"
                ) from exc
            raise GuardError(
                f"{what} {path} is inside an install ({candidate} holds {marker}); "
                "guard creates nothing there"
            )


@dataclass(frozen=True)
class _HeldLock:
    fd: int
    identity: _Identity
    path: Path
    pid: int
    """The process that took it: a forked child never unlocks the parent's lock."""


def _plain_dir(path: Path, *, parents: bool) -> None:
    """Make `path` if it is missing, then require a real directory there: not
    a symbolic link, not a junction or any other reparse point."""
    try:
        path.mkdir(parents=parents, exist_ok=True)
        st = path.lstat()
        junction = path.is_junction()
    except OSError as exc:
        raise GuardError(f"cannot make the lock directory {path}: {exc}") from exc
    if junction or _is_link(st) or not stat.S_ISDIR(st.st_mode):
        raise GuardError(f"{path} is not a plain directory (links and junctions are refused)")


def _os_lock(fd: int, path: Path, what: str) -> None:
    """The system lock (§6.10 amended, item 1): exclusive and non-blocking."""
    busy = GuardBusyError(
        f"{what} ({path}) is held by another wowlab call; one writer at a time, "
        "so try again when it has finished"
    )
    if sys.platform == "win32":
        import msvcrt

        try:
            os.lseek(fd, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            # A byte another handle has locked is refused with EACCES.
            if exc.errno in (errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK)):
                raise busy from exc
            raise GuardError(f"cannot lock {what} ({path}): {exc}") from exc
    else:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise busy from exc
            raise GuardError(f"cannot lock {what} ({path}): {exc}") from exc


def _os_unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


def _take_lock(path: Path, what: str) -> _HeldLock:
    """Open the lock file (creating it, never following a link, never
    truncating or writing it) and lock it. `GuardBusyError` when it is held,
    here or elsewhere; `GuardError` for anything else."""
    if os.name == "nt":
        # No O_NOFOLLOW on Windows: refuse a link or reparse point before the
        # open, and check after it that the file opened is the one at the path.
        try:
            before = path.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise GuardError(f"cannot inspect the lock file {path}: {exc}") from exc
        else:
            if _is_link(before) or not stat.S_ISREG(before.st_mode):
                raise GuardError(f"the lock file {path} is not a regular file")
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)  # a FIFO planted there never blocks the open
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_BINARY", 0)
    )
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise GuardError(f"cannot open the lock file {path}: {exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise GuardError(f"the lock file {path} is not a regular file")
        if st.st_nlink != 1:
            raise GuardError(
                f"the lock file {path} has {st.st_nlink} links; a hard link is refused"
            )
        if os.name == "nt":
            after = path.lstat()
            if _is_link(after) or _identity(after) != _identity(st):
                raise GuardError(f"the lock file {path} changed as it was opened")
        identity = _identity(st)
        with _HELD_LOCKS_MUTEX:
            if identity in _HELD_LOCKS:
                raise GuardBusyError(
                    f"{what} ({path}) is already held by this process (a transaction, "
                    "undo or store_lock is open); try again when it has finished"
                )
            _os_lock(fd, path, what)
            _HELD_LOCKS.add(identity)
    except BaseException as exc:
        os.close(fd)
        if isinstance(exc, OSError):
            raise GuardError(f"cannot lock {what} ({path}): {exc}") from exc
        raise
    return _HeldLock(fd=fd, identity=identity, path=path, pid=os.getpid())


def _release_lock(lock: _HeldLock) -> None:
    """Unlock and close. In a forked child only the descriptor is closed: an
    unlock there would release the lock the parent still holds (a `flock`
    lock belongs to the open file description the two share)."""
    if os.getpid() != lock.pid:
        with contextlib.suppress(OSError):
            os.close(lock.fd)
        return
    try:
        _os_unlock(lock.fd)
    except OSError as exc:  # closing the descriptor releases it anyway
        _log.warning("could not unlock %s: %s", lock.path, exc)
    finally:
        with contextlib.suppress(OSError):
            os.close(lock.fd)
        with _HELD_LOCKS_MUTEX:
            _HELD_LOCKS.discard(lock.identity)


class _Locks:
    """The locks one call holds, released together, last taken first."""

    __slots__ = ("_held",)

    def __init__(self) -> None:
        self._held: list[_HeldLock] = []

    def take_store(self, store_path: Path, *, create: bool) -> None:
        """`<store>/lock`. With `create`, a missing store directory is made
        first (a transaction's store); otherwise it must exist. Neither the
        store nor its lock file may be inside any install."""
        _refuse_inside_any_install(store_path, "the store")
        _refuse_inside_any_install(store_path / _STORE_LOCK, "the store lock")
        if create:
            try:
                store_path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise GuardError(f"cannot make the store {store_path}: {exc}") from exc
        self._held.append(_take_lock(store_path / _STORE_LOCK, "the store lock"))

    def take_install(self, place: _Place) -> None:
        path = _install_lock_path(place)
        _refuse_inside_any_install(path.parent, "the lock directory")
        _refuse_inside_any_install(path, "the install lock")
        _plain_dir(path.parent.parent, parents=True)
        _plain_dir(path.parent, parents=False)
        self._held.append(_take_lock(path, "the install lock"))

    def cover(self, store_path: Path, place: _Place) -> bool:
        """True when these locks, taken by this process, include this store's
        lock and this install's lock (by the identity of each lock file)."""
        held = {lock.identity for lock in self._held if lock.pid == os.getpid()}
        for path in (store_path / _STORE_LOCK, _install_lock_path(place)):
            try:
                st = path.lstat()
            except OSError:
                return False
            if _is_link(st) or _identity(st) not in held:
                return False
        return True

    def release(self) -> None:
        while self._held:
            _release_lock(self._held.pop())


# ─── the flavor ──────────────────────────────────────────────────────────────


class _FlavorLike(Protocol):
    """What `guard` reads from a flavor (`install.Flavor`, docs/LAB_PLAN.md §6.1)."""

    @property
    def path(self) -> Path: ...


@dataclass(frozen=True)
class _Place:
    """A validated flavor: resolved paths and the identities they had."""

    install_root: Path
    flavor_dir: Path
    folder: str
    given_root: Path
    version: str | None
    root_id: _Identity
    flavor_id: _Identity


def _require_regular_file(path: Path, what: str) -> None:
    try:
        st = path.lstat()
    except OSError as exc:
        raise GuardError(f"not a flavor of an install: {what} {path} is missing ({exc})") from exc
    if not stat.S_ISREG(st.st_mode) or _is_link(st):
        raise GuardError(f"not a flavor of an install: {what} {path} is not a regular file")


def _disk_name(root: Path, real: Path) -> str:
    """The flavor folder's name as the install root lists it."""
    names = [p.name for p in root.iterdir()]
    if real.name in names:
        return real.name
    for name in names:
        if _fold(name) == _fold(real.name) and (root / name).samefile(real):
            return name
    raise GuardError(f"{real} is not listed in {root}")


def _place(path_value: object, version: object) -> _Place:
    """Validate a flavor path the way §6.1 defines a flavor. Raises GuardError."""
    if not isinstance(path_value, str | os.PathLike):
        raise GuardError(f"a flavor needs a path, not {type(path_value).__name__}")
    given = Path(path_value)
    if not given.is_absolute():
        raise GuardError(f"a flavor path must be absolute: {given}")
    try:
        st = given.lstat()
        if _is_link(st) or not stat.S_ISDIR(st.st_mode):
            raise GuardError(f"the flavor folder {given} is not a directory (links are refused)")
        real = given.resolve(strict=True)
        root = real.parent
        if root == real:
            raise GuardError(f"the flavor folder {given} has no install root above it")
        _require_regular_file(real / ".flavor.info", ".flavor.info")
        _require_regular_file(root / ".build.info", ".build.info")
        folder = _disk_name(root, real)
        flavor_dir = root / folder
        root_st = root.lstat()
        flavor_st = flavor_dir.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise GuardError(f"not a flavor of an install: {given} ({exc})") from exc
    if (
        _is_link(flavor_st)
        or not stat.S_ISDIR(flavor_st.st_mode)
        or not stat.S_ISDIR(root_st.st_mode)
    ):
        raise GuardError(f"not a flavor of an install: {given}")
    return _Place(
        install_root=root,
        flavor_dir=flavor_dir,
        folder=folder,
        given_root=given.parent,
        version=version if isinstance(version, str) else None,
        root_id=_identity(root_st),
        flavor_id=_identity(flavor_st),
    )


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return a.samefile(b)
    except (OSError, ValueError):
        return False


def _store_places(store_path: Path) -> list[Path]:
    """Every place under the store that guard or the snapshot store writes
    into, existing or not: the store itself, its lock file, `journal/`,
    `objects/` (and each object shard already in it), `manifests/` and
    `tmp/`. A link at any of them would carry those writes wherever it
    points."""
    places = [store_path, store_path / _STORE_LOCK, _journal_dir(store_path)]
    for name in ("objects", "manifests", "tmp"):
        places.append(store_path / name)
    objects = store_path / "objects"
    with contextlib.suppress(OSError):
        places.extend(sorted(objects.iterdir()))
    return places


def _overlaps(path: Path, root: Path) -> bool:
    """True when `path` (resolved) and `root` contain one another, by spelling
    or by directory identity along either side's ancestors."""
    if path == root or root in path.parents or path in root.parents:
        return True
    if any(_same_dir(p, root) for p in (path, *path.parents)):
        return True
    return any(_same_dir(p, path) for p in (root, *root.parents))


def _refuse_store_overlap(store_path: Path, place: _Place) -> None:
    """L1: the store is never inside the install and never contains it, no
    directory it writes into is a link that reaches the install, and neither
    the lock files nor `locks/` do (§6.10 amended, item 1). Runs before any
    lock file or lock directory is created."""
    root = place.install_root
    install_lock = _install_lock_path(place)
    for candidate in (*_store_places(store_path), install_lock.parent, install_lock):
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as exc:
            raise GuardError(f"cannot resolve the store path {candidate}: {exc}") from exc
        if _overlaps(resolved, root):
            raise GuardError(
                f"{candidate} reaches the install ({root}); the store ({store_path}), the "
                "lock files and the install must not contain each other"
            )
    # Not inside any install either, whether or not it is this one (§6.10 as
    # amended 2026-09-23): checked for all four before any of them is made.
    for candidate, what in (
        (store_path, "the store"),
        (store_path / _STORE_LOCK, "the store lock"),
        (install_lock.parent, "the lock directory"),
        (install_lock, "the install lock"),
    ):
        _refuse_inside_any_install(candidate, what)


def _local_absolute(text: str) -> bool:
    """A path read from a journal record or a manifest may be looked up only
    when it is a local absolute path: absolute, not `\\\\` or `//` (a UNC or
    device path), and on Windows with a drive letter. Anything else is
    treated as another install, without a lookup."""
    if not text or "\x00" in text or text.startswith(("\\\\", "//")):
        return False
    if os.name == "nt":
        spelled = PureWindowsPath(text)
        return re.fullmatch(r"[A-Za-z]:", spelled.drive) is not None and bool(spelled.root)
    return text.startswith("/")


def _client_names(flavor_dir: Path) -> list[str]:
    """Executable names in the flavor folder, for `process` (`extra_names`)."""
    names: list[str] = []
    for child in sorted(flavor_dir.iterdir()):
        folded = child.name.casefold()
        if folded.endswith(".exe"):
            names.append(child.name)
        elif folded.endswith(".app"):
            names.append(child.name[: -len(".app")])
            with contextlib.suppress(OSError):
                names.extend(p.name for p in (child / "Contents" / "MacOS").iterdir())
    return names


def _refuse_if_client_running(place: _Place) -> None:
    """§6.7 as amended: the default probe, given the install root, the flavor
    folder and the executable names found there. Unknown is running, and so
    is any exception on the way to an answer."""
    try:
        roots = list(dict.fromkeys((place.given_root, place.install_root)))
        state = process.client_state(
            roots, flavor_folders=[place.folder], extra_names=_client_names(place.flavor_dir)
        )
    except Exception as exc:
        raise ClientRunningError(
            f"could not tell whether the client is running ({type(exc).__name__}: {exc}); "
            "treating it as running"
        ) from exc
    if state is not process.ClientState.NOT_RUNNING:
        raise ClientRunningError(f"the game client is {state.value}; close it and try again")


# ─── paths ───────────────────────────────────────────────────────────────────


def _is_executable(name: str) -> bool:
    folded = name.casefold()
    return folded.endswith(_EXECUTABLE_SUFFIXES) or bool(_VERSIONED_SHARED_LIBRARY.search(name))


def _check_rel(
    rel: object, *, directory: bool = False, allow_executable: bool = False
) -> tuple[str, ...]:
    """Hold a flavor-relative path to the allowlist, lexically. Returns its parts.

    A file path is `/`-separated, has at least two parts, starts with an
    allowlist root spelled exactly, contains no executable component and no
    name that means something else on Windows. `directory=True` also accepts
    the allowlist roots themselves (for directories a transaction created).
    `allow_executable=True` skips only the executable rule; it is used to
    find an unchanged entry that restore may leave alone, never to write.
    """
    if not isinstance(rel, str):
        raise PathNotAllowedError(f"a path is a str, not {type(rel).__name__}")
    if rel == "":
        raise PathNotAllowedError("the empty path is the flavor folder itself")
    if rel.startswith("/"):
        raise PathNotAllowedError(f"absolute paths are refused: {rel!r}")
    bad = sorted({c for c in rel if c in _FORBIDDEN_CHARACTERS})
    if bad:
        raise PathNotAllowedError(f"{rel!r} holds characters that are not a plain name: {bad!r}")
    try:
        os.fsencode(rel)
    except UnicodeError as exc:
        raise PathNotAllowedError(f"{rel!r} cannot be a file name here: {exc}") from exc
    parts = tuple(rel.split("/"))
    for part in parts:
        if part in ("", ".", ".."):
            raise PathNotAllowedError(f"{rel!r} is not a plain relative path")
        if part != part.rstrip(". "):
            raise PathNotAllowedError(f"{rel!r}: a name may not end in a dot or a space")
        if part.split(".", 1)[0].rstrip(" ").casefold() in _RESERVED_NAMES:
            raise PathNotAllowedError(f"{rel!r}: {part!r} is a reserved device name on Windows")
        if len(part.encode("utf-8", "surrogateescape")) > _MAX_NAME_BYTES:
            raise PathNotAllowedError(f"{rel!r}: a name is longer than {_MAX_NAME_BYTES} bytes")
        if _is_executable(part) and not allow_executable:
            raise PathNotAllowedError(f"{rel!r} is an executable; guard never writes one")
    if parts[0] not in _ROOTS:
        raise PathNotAllowedError(
            f"{rel!r} is outside the allowlist ({', '.join(r + '/' for r in _ROOTS)})"
        )
    if parts[0] == "Interface" and len(parts) > 1:
        if parts[1] != _ADDONS and _fold(parts[1]) == _fold(_ADDONS):
            raise PathNotAllowedError(f"{rel!r}: spell the addon folder {_ADDONS!r}")
        if parts[1] == _ADDONS and len(parts) < 3 and not directory:
            raise PathNotAllowedError(f"{rel!r} is an allowlist root, not a file")
    if len(parts) < 2 and not directory:
        raise PathNotAllowedError(f"{rel!r} is an allowlist root, not a file")
    return parts


@dataclass(frozen=True)
class _Found:
    """Where a path is on disk, from a walk that followed no link."""

    rel: str
    path: Path
    chain: tuple[tuple[Path, _Identity], ...]
    """Every existing directory from the install root down to the parent."""
    missing: tuple[str, ...]
    """Flavor-relative parent directories that do not exist yet, outermost first."""
    st: os.stat_result | None
    """The target, when it exists."""


def _verify(chain: Iterable[tuple[Path, _Identity]], rel: str) -> None:
    """Each directory is still the one the walk found, and still not a link."""
    for path, expected in chain:
        try:
            st = path.lstat()
        except OSError as exc:
            raise GuardError(f"{rel}: {path} changed during the transaction ({exc})") from exc
        if _is_link(st) or not stat.S_ISDIR(st.st_mode) or _identity(st) != expected:
            raise GuardError(f"{rel}: {path} changed during the transaction")


def _verify_after(chain: Iterable[tuple[Path, _Identity]], rel: str) -> None:
    """`_verify` after a rename or unlink has already happened."""
    try:
        _verify(chain, rel)
    except GuardError as exc:
        raise _ChangedUnderneathError(f"{exc}; the change may have landed elsewhere") from exc


def _entry(directory: Path, name: str, rel: str) -> os.stat_result | None:
    """`lstat` of `directory/name`, or None when absent. Refuses a link, a
    name that only matches an existing one by case or Unicode form, and a
    name the file system resolves although the directory does not list it."""
    try:
        names = {p.name for p in directory.iterdir()}
    except OSError as exc:
        raise GuardError(f"{rel}: cannot list {directory}: {exc}") from exc
    path = directory / name
    if name not in names:
        clash = sorted(n for n in names if _fold(n) == _fold(name))
        if clash:
            raise PathNotAllowedError(
                f"{rel}: {name!r} collides with {clash[0]!r} already in {directory}"
            )
        try:
            path.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise GuardError(f"{rel}: cannot inspect {path}: {exc}") from exc
        raise PathNotAllowedError(f"{rel}: {path} resolves to a file listed under another name")
    try:
        st = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise GuardError(f"{rel}: cannot inspect {path}: {exc}") from exc
    if _is_link(st):
        raise PathNotAllowedError(f"{rel}: {path} is a link; guard never follows one")
    return st


def _look(place: _Place, parts: Sequence[str], *, want_dir: bool = False) -> _Found:
    """Walk `parts` below the flavor folder, following nothing."""
    rel = "/".join(parts)
    chain: list[tuple[Path, _Identity]] = [
        (place.install_root, place.root_id),
        (place.flavor_dir, place.flavor_id),
    ]
    _verify(chain, rel)
    current = place.flavor_dir
    missing: list[str] = []
    for depth, name in enumerate(parts[:-1], start=1):
        current = current / name
        if missing:
            missing.append("/".join(parts[:depth]))
            continue
        st = _entry(current.parent, name, rel)
        if st is None:
            missing.append("/".join(parts[:depth]))
        elif stat.S_ISDIR(st.st_mode):
            chain.append((current, _identity(st)))
        else:
            raise PathNotAllowedError(f"{rel}: {'/'.join(parts[:depth])} is not a directory")
    target = current / parts[-1]
    st = None if missing else _entry(current, parts[-1], rel)
    if st is not None:
        if want_dir and not stat.S_ISDIR(st.st_mode):
            raise PathNotAllowedError(f"{rel} is not a directory")
        if not want_dir and not stat.S_ISREG(st.st_mode):
            kind = "a directory" if stat.S_ISDIR(st.st_mode) else "not a regular file"
            raise PathNotAllowedError(f"{rel} is {kind}; the gate writes files")
    return _Found(rel=rel, path=target, chain=tuple(chain), missing=tuple(missing), st=st)


def _read(found: _Found) -> bytes | None:
    """The target's bytes, read through a descriptor that is checked to be the
    file the walk found; None when it does not exist."""
    if found.st is None:
        return None
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(found.path, flags)
    except OSError as exc:
        raise GuardError(f"cannot read {found.rel}: {exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or _identity(st) != _identity(found.st):
            raise GuardError(f"{found.rel} changed while it was being read")
        chunks: list[bytes] = []
        while chunk := os.read(fd, _CHUNK):
            chunks.append(chunk)
    except OSError as exc:
        raise GuardError(f"cannot read {found.rel}: {exc}") from exc
    finally:
        os.close(fd)
    return b"".join(chunks)


def _same_state(a: os.stat_result | None, b: os.stat_result | None) -> bool:
    if a is None or b is None:
        return a is b
    return (_identity(a), a.st_size, a.st_mtime_ns) == (_identity(b), b.st_size, b.st_mtime_ns)


def _modes(rule: _ModeRule, current: os.stat_result | None) -> tuple[int, int | None]:
    """(mode to create the temp file with, mode to fchmod it to or None)."""
    kind, recorded = rule
    if current is None:
        if kind == "keep":
            return 0o666, None  # a new file, as the umask says
        if kind == "exact":
            return 0o600, recorded & 0o777
        return recorded & 0o666, None  # recreated: recorded bits, less umask, never exec
    now = stat.S_IMODE(current.st_mode) & 0o777
    if kind == "keep":
        return 0o600, now
    if kind == "exact":
        return 0o600, recorded & 0o777
    return 0o600, recorded & now


def _temp_name() -> str:
    """A fresh temp-file name: `.wowlab-<32 lowercase hex>.tmp` (`_TEMP_NAME`)."""
    return f"{_TEMP_PREFIX}{uuid.uuid4().hex}.tmp"


def _changed(rel: str, why: str) -> ChangedSinceSnapshotError:
    return ChangedSinceSnapshotError(
        f"{rel} {why}; nothing was written for it and this transaction cannot commit. "
        "Retry the transaction: it will read the file as it is now"
    )


def _stat_or_none(path: Path, rel: str) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise GuardError(f"cannot inspect {rel}: {exc}") from exc


def _recheck(
    found: _Found, chain: Sequence[tuple[Path, _Identity]], expect_hash: str | None
) -> None:
    """Item 3, just before a replace or unlink: the target still holds the
    hash guard last read or wrote there (for a create: it is still absent),
    the directory chain is unchanged, and, as the last step, `lstat` shows the
    identity, size and mtime the walk found."""
    rel = found.rel
    now = _stat_or_none(found.path, rel)
    if expect_hash is None:
        if now is not None:
            raise _changed(rel, "appeared after guard found it absent")
    else:
        if now is None:
            raise _changed(rel, "was removed after guard read it")
        if _is_link(now) or not stat.S_ISREG(now.st_mode):
            raise _changed(rel, "is no longer a regular file")
        data = _read(dataclasses.replace(found, st=now))
        if data is None or _sha(data) != expect_hash:
            raise _changed(rel, "changed after guard read it")
    _verify(chain, rel)
    if not _same_state(_stat_or_none(found.path, rel), found.st):
        raise _changed(rel, "was replaced or touched after guard read it")


def _replace(
    found: _Found,
    chain: Sequence[tuple[Path, _Identity]],
    data: bytes,
    rule: _ModeRule,
    tmp_name: str,
    expect_hash: str | None,
) -> None:
    """Temp file `tmp_name` (already journaled) in the same directory, fsync,
    re-check, atomic replace. A create on Windows uses `os.rename`, which
    never replaces: a file that appeared after the re-check is refused."""
    rel = found.rel
    create_mode, chmod_to = _modes(rule, found.st)
    tmp = found.path.parent / tmp_name
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    _verify(chain, rel)
    try:
        fd = os.open(tmp, flags, create_mode)
    except OSError as exc:
        raise GuardError(f"cannot write {rel}: {exc}") from exc
    replaced = False
    try:
        try:
            opened = os.fstat(fd)
            # Where the temp file really is: a parent swapped for a link
            # between the check and the open shows here.
            _verify(chain, rel)
            if _identity(tmp.lstat()) != _identity(opened):
                raise GuardError(f"{rel}: the temp file is not where it was created")
            _write_all(fd, data)
            if chmod_to is not None and os.name == "posix":
                os.fchmod(fd, chmod_to)
            os.fsync(fd)
        finally:
            os.close(fd)
        _recheck(found, chain, expect_hash)
        if found.st is None and os.name == "nt":
            try:
                tmp.rename(found.path)  # os.rename: never replaces on Windows
            except FileExistsError as exc:
                raise _changed(rel, "appeared after guard found it absent") from exc
        else:
            tmp.replace(found.path)
        replaced = True
        _verify_after(chain, rel)
    except OSError as exc:
        raise GuardError(f"cannot write {rel}: {exc}") from exc
    finally:
        if not replaced:
            with contextlib.suppress(OSError):
                tmp.unlink()
    _fsync_dir(found.path.parent)


def _mutate(
    place: _Place,
    parts: Sequence[str],
    data: bytes | None,
    rule: _ModeRule,
    expect: _Found,
    created: list[str],
    *,
    expect_hash: str | None,
    tmp_name: str | None,
) -> None:
    """Put `data` at `parts` (None deletes), after walking the path again and
    checking the target is still what `expect` found and still hashes to
    `expect_hash`. A write goes through the temp file `tmp_name`, which the
    caller has already journaled. Directories made on the way are appended
    to `created` as they are made."""
    if _check_rel("/".join(parts)) != tuple(parts):
        raise PathNotAllowedError(f"{'/'.join(parts)!r} is not a path the gate writes")
    found = _look(place, parts)
    rel = found.rel
    if not _same_state(found.st, expect.st):
        raise _changed(rel, "changed between guard's read and its write")
    if data is None:
        if found.st is None:
            return
        _recheck(found, found.chain, expect_hash)
        try:
            found.path.unlink()
        except OSError as exc:
            raise GuardError(f"cannot delete {rel}: {exc}") from exc
        _verify_after(found.chain, rel)
        _fsync_dir(found.path.parent)
        return
    chain = list(found.chain)
    for rel_dir in found.missing:
        directory = place.flavor_dir.joinpath(*rel_dir.split("/"))
        _verify(chain, rel)
        try:
            directory.mkdir()
            st = directory.lstat()
        except OSError as exc:
            raise GuardError(f"cannot create {rel_dir} for {rel}: {exc}") from exc
        created.append(rel_dir)
        if _is_link(st) or not stat.S_ISDIR(st.st_mode):
            raise GuardError(f"{rel}: {rel_dir} changed as it was created")
        chain.append((directory, _identity(st)))
    if tmp_name is None:
        raise GuardError(f"{rel}: a write needs a journaled temp-file name")
    _replace(found, chain, data, rule, tmp_name, expect_hash)


def _remove_dir(place: _Place, rel_dir: str) -> None:
    """Remove a directory this gate created, if it is still there. Only an
    empty directory is removed; anything else raises GuardError."""
    parts = _check_rel(rel_dir, directory=True)
    found = _look(place, parts, want_dir=True)
    if found.st is None:
        return
    _verify(found.chain, rel_dir)
    try:
        found.path.rmdir()
    except OSError as exc:
        raise GuardError(f"cannot remove {rel_dir}: {exc}") from exc


# ─── the transaction ─────────────────────────────────────────────────────────


class _Transaction:
    """What `transaction()` returns. Dead after exit."""

    __slots__ = (
        "_before",
        "_broken",
        "_cleanup_finished",
        "_created",
        "_dry_run",
        "_flavor",
        "_given_locks",
        "_journal",
        "_label",
        "_last",
        "_order",
        "_overlay",
        "_own_locks",
        "_pid",
        "_place",
        "_plan",
        "_planned_dirs",
        "_pre_entries",
        "_record",
        "_refused",
        "_status",
        "_store_path",
        "_temps",
        "_temps_left",
        "_temps_removed",
    )

    def __init__(
        self,
        flavor: object,
        *,
        label: str,
        store_path: Path,
        dry_run: bool,
        place: _Place | None = None,
        locks: _Locks | None = None,
    ) -> None:
        self._flavor = flavor
        self._label = label
        self._store_path = store_path
        self._dry_run = dry_run
        self._place = place
        # Locks the caller already holds for this store and install (`undo()`);
        # without them the transaction takes its own on enter.
        self._given_locks = locks
        # The process that opened it: a forked child neither writes, commits
        # nor unlocks through it.
        self._pid = os.getpid()
        self._own_locks: _Locks | None = None
        self._status: Literal["new", "open", "closed"] = "new"
        self._record: _JournalRecord | None = None
        self._order: list[str] = []
        self._journal: dict[str, tuple[str | None, str | None]] = {}
        # First-touch state of each journaled path: its hash (None: absent)
        # and its mode.
        self._before: dict[str, tuple[str | None, int]] = {}
        # Guard's own record of each journaled path (item 3 as clarified): the
        # hash of its last write there that landed, else the hash read at its
        # first touch. Two hashes when a mutation's outcome is unknown.
        self._last: dict[str, tuple[str | None, ...]] = {}
        # Paths whose only operation was refused, with what guard read there.
        self._refused: dict[str, str | None] = {}
        # The pre-write snapshot's entries: flavor-relative path to (kind, hash).
        self._pre_entries: dict[str, tuple[str, str | None]] = {}
        self._created: list[str] = []
        self._planned_dirs: list[str] = []
        self._plan: dict[str, PlanItem] = {}
        self._overlay: dict[str, bytes | None] = {}
        self._broken: str | None = None
        self._temps: list[str] = []
        self._temps_removed: list[str] = []
        self._temps_left: list[str] = []
        self._cleanup_finished = False

    # -- public surface ----------------------------------------------------

    @property
    def plan(self) -> tuple[PlanItem, ...]:
        """Every path changed so far (in a dry run: that would be changed),
        once each, with its first `before` and its last `after`."""
        return tuple(self._plan.values())

    def write(self, rel_path: str, data: bytes) -> None:
        """Replace or create the file at `rel_path` (relative to the flavor
        folder, `/`-separated) with `data`. Missing parent directories inside
        the allowlist are created."""
        self._alive()
        parts = _check_rel(rel_path)
        if not isinstance(data, bytes | bytearray | memoryview):
            raise TypeError(f"data is bytes, not {type(data).__name__}")
        self._apply(rel_path, parts, bytes(data), ("keep", 0))

    def delete(self, rel_path: str) -> None:
        """Delete the file at `rel_path`."""
        self._alive()
        parts = _check_rel(rel_path)
        self._apply(rel_path, parts, None, ("keep", 0))

    def restore(self, snapshot_id: str, paths: Iterable[str] | None = None) -> None:
        """Put back the files of a snapshot of this flavor: every file it holds,
        or only `paths` (a named path the snapshot's subtrees cover but that it
        does not hold is deleted). Every entry is checked against the gate
        and the disk before anything is written, so a refused entry leaves
        nothing behind. With `paths=None` any entry the gate refuses stops the
        whole restore, even an unchanged one; an unchanged link is the one
        exception, since leaving it needs no write. A failure after a path has
        changed makes the transaction roll back on exit."""
        self._alive()
        if not isinstance(snapshot_id, str):
            raise GuardError(f"a snapshot id is a str, not {type(snapshot_id).__name__}")
        store = SnapshotStore(self._store_path)
        try:
            manifest = store.show(snapshot_id)
        except SnapshotError as exc:
            raise GuardError(f"cannot read snapshot {snapshot_id!r}: {exc}") from exc
        assert self._place is not None
        _refuse_foreign_snapshot(manifest, self._place)
        prefix = self._place.folder + "/"
        wanted: list[tuple[str, Entry | None]] = []
        if paths is None:
            for entry in manifest.entries:
                if not entry.path.startswith(prefix):
                    raise PathNotAllowedError(
                        f"snapshot {manifest.id} holds {entry.path!r}, which is not in this flavor"
                    )
                wanted.append((entry.path.removeprefix(prefix), entry))
        else:
            if isinstance(paths, str | bytes):
                raise GuardError("paths is an iterable of paths, not a single string")
            for rel in paths:
                _check_rel(rel)
                named = manifest.entry(prefix + rel)
                if named is None and not _covers(manifest, prefix + rel):
                    raise GuardError(f"{rel!r} is not in snapshot {manifest.id}")
                wanted.append((rel, named))
        ops = self._restore_ops(store, manifest, wanted, named=paths is not None)
        self._run_all(store, ops)

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> _Transaction:
        if self._status != "new":
            raise GuardError("a transaction is entered once")
        self._status = "closed"  # until the gate below has let it open
        place = self._place
        if place is None:
            place = _place(
                getattr(self._flavor, "path", None), getattr(self._flavor, "version", None)
            )
        _refuse_store_overlap(self._store_path, place)
        own: _Locks | None = None
        try:
            if self._given_locks is None:
                own = _Locks()
                own.take_store(self._store_path, create=True)
                own.take_install(place)
            elif not self._given_locks.cover(self._store_path, place):
                raise GuardError(
                    "the locks handed to this transaction are not this store's and this "
                    "install's; guard never proceeds unlocked"
                )
            _refuse_if_client_running(place)
            self._place = place
            if not self._dry_run:
                self._open_record(place)
        except BaseException:
            if own is not None:
                own.release()
            raise
        self._own_locks = own
        self._status = "open"
        return self

    def _open_record(self, place: _Place) -> None:
        """Pre-write snapshot, journal record, then the leftover cleanup."""
        # Read under the store lock, so no other writer's record can appear
        # before this one is written.
        candidates = self._temp_candidates(place)
        store = SnapshotStore(self._store_path)
        try:
            manifest = store.create(
                place.install_root,
                [f"{place.folder}/{root}" for root in _ROOTS],
                label=f"guard pre-write: {self._label}",
                flavor_folder=place.folder,
                flavor_version=place.version,
                client_running=False,
            )
        except SnapshotError as exc:
            raise GuardError(f"the pre-write snapshot failed; nothing was written: {exc}") from exc
        prefix = place.folder + "/"
        self._pre_entries = {
            e.path.removeprefix(prefix): (e.kind, e.sha256)
            for e in manifest.entries
            if e.path.startswith(prefix)
        }
        # With nothing to consider the cleanup is finished as the record is.
        self._cleanup_finished = not candidates
        self._record = _JournalRecord(
            format=_JOURNAL_FORMAT,
            id=_next_record_id(self._store_path),
            created_at=_now(),
            label=self._label,
            snapshot_id=manifest.id,
            install_root=str(place.install_root),
            flavor_path=str(place.flavor_dir),
            flavor_version=place.version,
            state="open",
            paths=(),
            created_dirs=(),
            note=None,
            cleanup_finished=self._cleanup_finished,
        )
        _write_record(self._store_path, self._record)
        if candidates:
            self._clean_up(place, candidates)

    def _temp_candidates(self, place: _Place) -> list[str]:
        """The temp paths item 2 considers: those named by this flavor's
        records (by identity) from the most recent one whose cleanup finished,
        that one included. A journal that cannot be read in full raises
        `GuardError` here, under the locks and before the pre-write snapshot:
        a change `undo()` could not reverse is not written (§6.10 as amended
        2026-09-23)."""
        try:
            records = _load_journal(self._store_path)
        except GuardError as exc:
            raise GuardError(f"{exc}; nothing was written or journaled") from exc
        mine = [r for r in records if _record_is_of(r, place)]
        start = max((i for i, r in enumerate(mine) if r.cleanup_finished), default=0)
        return list(dict.fromkeys(p for r in mine[start:] for p in (*r.temps, *r.temps_left)))

    def _clean_up(self, place: _Place, candidates: Sequence[str]) -> None:
        """Remove the leftover temp files `candidates` name (item 2). Nothing
        here stops the transaction except a journal that cannot be written."""
        for rel in candidates:
            if not _TEMP_NAME.fullmatch(rel.rsplit("/", 1)[-1]):
                self._temps_left.append(rel)
                continue
            try:
                parts = _check_rel(rel)
            except PathNotAllowedError:
                self._temps_left.append(rel)  # never looked up
                continue
            try:
                found = _look(place, parts)
            except GuardError:
                self._temps_left.append(rel)  # a link, a directory, a collision...
                continue
            if found.st is None:
                continue  # absent: someone else removed it, or its directory
            self._temps_removed.append(rel)
            self._journal_write("open")  # durable before the unlink
            try:
                _verify(found.chain, rel)
                now = _stat_or_none(found.path, rel)
                if (
                    now is None
                    or _is_link(now)
                    or not stat.S_ISREG(now.st_mode)
                    or not _same_state(now, found.st)
                ):
                    raise GuardError(f"{rel} changed before its removal")
                try:
                    found.path.unlink()
                except OSError as exc:
                    raise GuardError(f"cannot remove {rel}: {exc}") from exc
                _verify_after(found.chain, rel)
            except GuardError as exc:
                _log.warning("leftover temp file left in place: %s", exc)
                self._temps_removed.remove(rel)
                self._temps_left.append(rel)
                self._journal_write("open")
                continue
            _fsync_dir(found.path.parent)
        self._cleanup_finished = True
        self._journal_write("open")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        if self._status != "open":
            return False
        self._status = "closed"
        try:
            if os.getpid() != self._pid:
                # A forked child unwinding the parent's `with`: the record and
                # the locks are the parent's. Touch neither disk nor journal.
                raise GuardError(
                    "this transaction belongs to process "
                    f"{self._pid}; a forked child cannot commit or roll it back"
                )
            return self._close(exc)
        finally:
            if self._own_locks is not None:
                self._own_locks.release()
                self._own_locks = None

    def _close(self, exc: BaseException | None) -> Literal[False]:
        if self._dry_run:
            return False
        if exc is None and self._broken is None:
            self._journal_write("committed")
            return False
        problems = self._rollback()
        state: _State = "rollback_incomplete" if problems else "rolled_back"
        note = "; ".join(problems) if problems else None
        if problems:
            _log.error("rollback of %r did not finish: %s", self._label, note)
        try:
            self._journal_write(state, note)
        except Exception:
            if exc is None:
                raise
            _log.exception("could not record the rollback of %r", self._label)
        if exc is not None:
            if problems:
                exc.add_note(f"wowlab guard: the rollback did not finish: {note}")
            return False
        raise GuardError(
            f"{self._broken}; the transaction was rolled back"
            + (f", but not completely: {note}" if problems else "")
        )

    # -- internals ---------------------------------------------------------

    def _alive(self) -> None:
        """The transaction takes operations: open, and not broken. A broken
        one (a refused or half-done change) touches nothing more."""
        if self._status != "open":
            raise GuardError("this transaction is not open")
        if os.getpid() != self._pid:
            raise GuardError(f"this transaction belongs to process {self._pid}, not this one")
        if self._broken is not None:
            raise GuardError(
                f"this transaction can only roll back ({self._broken}); nothing more is written"
            )

    def _journal_write(self, state: _State, note: str | None = None) -> None:
        assert self._record is not None
        self._record = self._record.model_copy(
            update={
                "state": state,
                "note": note,
                "paths": tuple(
                    _JournalPath(
                        path=rel, before=self._journal[rel][0], after=self._journal[rel][1]
                    )
                    for rel in self._order
                ),
                "created_dirs": tuple(self._planned_dirs),
                "temps": tuple(self._temps),
                "temps_removed": tuple(self._temps_removed),
                "temps_left": tuple(self._temps_left),
                "cleanup_finished": self._cleanup_finished,
            }
        )
        _write_record(self._store_path, self._record)

    def _new_temp(self, parts: Sequence[str]) -> str:
        """Name a temp file beside `parts` and add it to what the next journal
        write records (item 2: journaled and fsynced before it exists)."""
        name = _temp_name()
        self._temps.append("/".join((*parts[:-1], name)))
        return name

    def _matches_snapshot(self, rel: str, digest: str | None) -> bool:
        """The disk (`digest`, None for absent) agrees with the pre-write
        snapshot's entry for `rel`: the same content in a regular file, or
        absent on both sides."""
        entry = self._pre_entries.get(rel)
        if digest is None:
            return entry is None
        return entry == ("file", digest)

    def _refuse(self, rel: str, read: str | None, *, first: bool, why: str) -> NoReturn:
        """Item 3: refuse `rel` before anything is written for it."""
        error = _changed(rel, why)
        if first:
            self._refused[rel] = read
        self._broken = str(error)
        raise error

    def _unjournal(self, rel: str, previous: tuple[str | None, str | None] | None) -> None:
        """A change refused after the write-ahead journal named it: nothing
        landed. The path's first operation is taken out of the record (so
        `undo()` leaves it alone); a later one goes back to its previous
        `after`."""
        if previous is None:
            read = self._before[rel][0]
            self._order.remove(rel)
            del self._journal[rel], self._before[rel], self._last[rel]
            self._refused[rel] = read
        else:
            self._journal[rel] = previous
        with contextlib.suppress(GuardError):
            self._journal_write("open")

    def _current(self, rel: str, parts: Sequence[str]) -> bytes | None:
        """What the path holds now (in a dry run, after the planned changes)."""
        if self._dry_run and rel in self._overlay:
            return self._overlay[rel]
        assert self._place is not None
        return _read(_look(self._place, parts))

    def _note_plan(
        self, rel: str, before: str | None, after: str | None, data: bytes | None
    ) -> None:
        size = None if data is None else len(data)
        first = self._plan.get(rel)
        self._plan[rel] = PlanItem(
            path=rel, before=before if first is None else first.before, after=after, size=size
        )

    def _apply(self, rel: str, parts: Sequence[str], data: bytes | None, rule: _ModeRule) -> None:
        """One journaled change: `data` at `rel`, or a delete when it is None."""
        assert self._place is not None
        after = None if data is None else _sha(data)
        if self._dry_run:
            current = self._current(rel, parts)
            if data is None and current is None:
                raise GuardError(f"{rel} does not exist")
            self._note_plan(rel, None if current is None else _sha(current), after, data)
            self._overlay[rel] = data
            return
        found = _look(self._place, parts)
        current = _read(found)
        mode = 0 if found.st is None else stat.S_IMODE(found.st.st_mode)
        before = None if current is None else _sha(current)
        previous = self._journal.get(rel)
        # Item 3: the disk must be what guard expects there. At the first
        # touch that is the pre-write snapshot, so rollback and undo only ever
        # need bytes the snapshot holds; later, guard's own record of the path.
        if previous is None:
            if not self._matches_snapshot(rel, before):
                self._refuse(rel, before, first=True, why="changed after the pre-write snapshot")
        elif before not in self._last[rel]:
            self._refuse(
                rel, before, first=False, why="changed after this transaction last read or wrote it"
            )
        if data is None and current is None:
            raise GuardError(f"{rel} does not exist")
        if previous is None:
            self._order.append(rel)
            self._before[rel] = (before, mode)
            self._journal[rel] = (before, after)
            self._last[rel] = (before,)
        else:
            self._journal[rel] = (previous[0], after)
        self._planned_dirs.extend(d for d in found.missing if d not in self._planned_dirs)
        tmp_name = None if data is None else self._new_temp(parts)
        try:
            # Write-ahead: the path, its `before` and the temp file's name are
            # durable before anything in the install changes.
            self._journal_write("open")
            _mutate(
                self._place,
                parts,
                data,
                rule,
                found,
                self._created,
                expect_hash=before,
                tmp_name=tmp_name,
            )
        except _ChangedUnderneathError as exc:
            # The change happened, but maybe not where it was meant to: the
            # record keeps it, and the transaction can only roll back.
            self._broken = str(exc)
            self._last[rel] = (before, after)
            raise
        except ChangedSinceSnapshotError as exc:
            self._unjournal(rel, previous)
            self._broken = str(exc)
            raise
        except Exception:
            # The path did not change: the record says so again.
            self._journal[rel] = (
                self._journal[rel][0],
                before if previous is None else previous[1],
            )
            with contextlib.suppress(GuardError):
                self._journal_write("open")
            raise
        except BaseException as exc:
            # Interrupted inside the mutation: it may or may not have landed,
            # and a caller that catches this must not commit a guessed `after`.
            self._last[rel] = (before, after)
            self._broken = f"{rel}: interrupted while it changed ({type(exc).__name__})"
            raise
        self._last[rel] = (after,)
        self._note_plan(rel, before, after, data)

    def _restore_ops(
        self,
        store: SnapshotStore,
        manifest: Manifest,
        wanted: Sequence[tuple[str, Entry | None]],
        *,
        named: bool,
    ) -> list[_Op]:
        """Check every entry against the gate and the disk before anything is
        written; return the changes that are needed. Objects are read to
        prove they are sound, one at a time, and not held."""
        ops: list[_Op] = []
        for rel, entry in wanted:
            try:
                parts = _check_rel(rel)
            except PathNotAllowedError:
                # A whole-snapshot restore leaves alone a file the gate never
                # writes (an addon's build.sh) when disk already matches it.
                if named or entry is None or not self._unchanged_executable(rel, entry):
                    raise
                continue
            if entry is not None and entry.kind != "file":
                if not named and self._same_link(parts, entry):
                    continue  # an unchanged link needs no write, and none is made
                raise PathNotAllowedError(
                    f"{rel!r} is a link in snapshot {manifest.id}; restore never creates one"
                )
            current = self._current(rel, parts)
            wanted_hash = None if entry is None else entry.sha256
            if (None if current is None else _sha(current)) == wanted_hash:
                continue
            if entry is not None:
                _object(store, manifest, entry)
            ops.append((rel, parts, wanted_hash, 0 if entry is None else entry.mode))
        return ops

    def _unchanged_executable(self, rel: str, entry: Entry) -> bool:
        """True when `rel` is refused only as an executable, and a walk that
        follows nothing finds a regular file with the entry's bytes and mode.
        Names refused for any other reason are never opened."""
        assert self._place is not None
        if entry.kind != "file" or entry.sha256 is None:
            return False
        try:
            parts = _check_rel(rel, allow_executable=True)
            found = _look(self._place, parts)
            if found.st is None or stat.S_IMODE(found.st.st_mode) != entry.mode:
                return False
            current = _read(found)
        except GuardError:
            return False
        return current is not None and _sha(current) == entry.sha256

    def _same_link(self, parts: Sequence[str], entry: Entry) -> bool:
        """True when the path on disk is already a link (a symlink or a
        junction) with the entry's target. An entry whose target could not be
        read when it was captured (`target=None`) never matches."""
        assert self._place is not None
        if entry.target is None:
            return False
        found = _look(self._place, parts[:-1], want_dir=True) if len(parts) > 1 else None
        if found is not None and found.st is None:
            return False
        path = self._place.flavor_dir.joinpath(*parts)
        try:
            linked = path.is_symlink() or path.is_junction()
            return linked and str(path.readlink()) == entry.target
        except OSError:
            return False

    def _run_all(self, store: SnapshotStore, ops: Sequence[_Op]) -> None:
        """Apply `ops` with clamped modes. A failure after one of them has
        landed leaves the transaction unable to commit: it rolls back on exit
        even if the caller catches the error."""
        applied = 0
        try:
            for rel, parts, digest, mode in ops:
                data = None if digest is None else _fetch(store, digest, rel)
                self._apply(rel, parts, data, ("clamp", mode))
                applied += 1
        except Exception as exc:
            if applied:
                self._broken = f"a restore stopped part way ({type(exc).__name__}: {exc})"
            raise

    def _rollback(self) -> list[str]:
        """Put every touched path back to its pre-transaction bytes (from the
        pre-write snapshot, by hash) where the disk still holds guard's own
        record of it; leave any path someone else changed, and say so; remove
        the directories this transaction created."""
        assert self._place is not None
        problems: list[str] = []
        store = SnapshotStore(self._store_path)
        for rel in reversed(self._order):
            before, mode = self._before[rel]
            parts = tuple(rel.split("/"))
            try:
                found = _look(self._place, parts)
                current = _read(found)
                now = None if current is None else _sha(current)
                if now == before:
                    continue  # already its pre-transaction content
                if now not in self._last[rel]:
                    problems.append(
                        f"{rel}: changed by something else after this transaction last wrote "
                        "it; left as it is"
                    )
                    continue
                data = None if before is None else _fetch(store, before, rel)
                tmp_name = None
                if data is not None:
                    tmp_name = self._new_temp(parts)
                    self._journal_write("open")  # the temp file's name, before it exists
                _mutate(
                    self._place,
                    parts,
                    data,
                    ("exact", mode),
                    found,
                    [],
                    expect_hash=now,
                    tmp_name=tmp_name,
                )
            except Exception as exc:
                problems.append(f"{rel}: {exc}")
        for rel, read in self._refused.items():
            # Refused before anything landed: kept as it is, but a disk that
            # moved since guard read it is not "rolled back".
            try:
                current = _read(_look(self._place, tuple(rel.split("/"))))
                if (None if current is None else _sha(current)) != read:
                    problems.append(
                        f"{rel}: changed by something else while this transaction ran; "
                        "left as it is"
                    )
            except Exception as exc:
                problems.append(f"{rel}: {exc}")
        for rel_dir in reversed(self._created):
            try:
                _remove_dir(self._place, rel_dir)
            except Exception as exc:
                problems.append(f"{rel_dir}/: {exc}")
        return problems

    def _undo(self, ops: Sequence[_Op], created_dirs: Sequence[str]) -> None:
        store = SnapshotStore(self._store_path)
        for rel, parts, digest, mode in ops:
            current = self._current(rel, parts)
            if (None if current is None else _sha(current)) != digest:
                data = None if digest is None else _fetch(store, digest, rel)
                self._apply(rel, parts, data, ("clamp", mode))
        assert self._place is not None
        for rel_dir in reversed(created_dirs):
            with contextlib.suppress(GuardError):
                _remove_dir(self._place, rel_dir)  # only if empty: best effort


def _record_is_of(record: _JournalRecord, place: _Place) -> bool:
    """The record's install root and flavor folder are `place`'s, by identity
    (a record names both as guard resolved them, so equal spellings are
    the common case and need no lookup). A path that is not local and
    absolute names another install and is never looked up."""
    if not (_local_absolute(record.flavor_path) and _local_absolute(record.install_root)):
        return False
    if record.flavor_path == str(place.flavor_dir) and record.install_root == str(
        place.install_root
    ):
        return True
    return _same_dir(Path(record.flavor_path), place.flavor_dir) and _same_dir(
        Path(record.install_root), place.install_root
    )


def _refuse_foreign_snapshot(manifest: Manifest, place: _Place) -> None:
    """A snapshot restores only into the flavor of the install it was taken
    from; restoring across installs or flavors is not a thing the gate does.
    An install root that is not a local absolute path is never looked up."""
    if (
        manifest.flavor_folder != place.folder
        or not _local_absolute(manifest.install_root)
        or not _same_dir(Path(manifest.install_root), place.install_root)
    ):
        raise GuardError(
            f"snapshot {manifest.id} was taken of {manifest.flavor_folder!r} in "
            f"{manifest.install_root}, not of {place.folder!r} in {place.install_root}"
        )


def _covers(manifest: Manifest, entry_path: str) -> bool:
    return any(entry_path.startswith(subtree + "/") for subtree in manifest.subtrees)


def _fetch(store: SnapshotStore, digest: str, rel: str) -> bytes:
    """An object by its hash; the store checks the bytes hash to the name."""
    try:
        return store.read_object(digest)
    except SnapshotError as exc:
        raise GuardError(f"the store cannot supply the bytes of {rel}: {exc}") from exc


def _object(store: SnapshotStore, manifest: Manifest, entry: Entry) -> bytes:
    if entry.sha256 is None:
        raise GuardError(f"{entry.path!r} in snapshot {manifest.id} has no content")
    try:
        return store.read_object(entry.sha256)
    except SnapshotError as exc:
        raise GuardError(f"snapshot {manifest.id} cannot supply {entry.path!r}: {exc}") from exc


# ─── the public API ──────────────────────────────────────────────────────────


def transaction(
    flavor: _FlavorLike,
    *,
    label: str = "",
    store: Path | None = None,
    dry_run: bool = False,
) -> _Transaction:
    """A write transaction against one flavor folder; use it with `with`.

    `flavor` is anything with a `path` (the flavor folder; `install.Flavor`).
    `store` is the snapshot store directory; `None` means the default under
    the user data directory. With `dry_run` nothing is written but the lock
    files: the same checks run under the same locks, and `tx.plan` holds what
    would change.

    Entering raises `GuardBusyError` while another transaction, `undo()` or
    `store_lock()` holds this store or this install (in this process too).
    An operation on a file that changed after the pre-write snapshot raises
    `ChangedSinceSnapshotError`; the transaction then cannot commit.
    """
    if not isinstance(label, str):
        raise GuardError(f"label is a str, not {type(label).__name__}")
    if not isinstance(dry_run, bool):
        raise GuardError(f"dry_run is a bool, not {type(dry_run).__name__}")
    return _Transaction(flavor, label=label, store_path=_store_path(store), dry_run=dry_run)


def history(*, store: Path | None = None) -> tuple[HistoryRecord, ...]:
    """Every transaction recorded in the store's journal, oldest first."""
    return tuple(_public(record) for record in _load_journal(_store_path(store)))


def undo(*, store: Path | None = None) -> None:
    """Undo the most recent transaction, as a transaction of its own.

    Only the paths that transaction journaled are restored, from its pre-write
    snapshot. Everything read from the store is checked first: the flavor it
    names, every path, and every snapshot entry against the journal's
    `before`. The undo is journaled like any transaction, so a second
    `undo()` undoes it. It holds the store lock from before it reads the
    record it plans from, and the install lock of that record's flavor
    (`GuardBusyError` if either is held); a store that does not exist is a
    `GuardError` and is not created.
    """
    store_path = _store_path(store)
    # Unlocked, only to find the flavor (and so which install to check).
    first = _last_record(store_path)
    place = _record_place(first)
    _refuse_store_overlap(store_path, place)
    locks = _Locks()
    try:
        locks.take_store(store_path, create=False)
        # Under the store lock no other writer can add a record: plan only
        # from what is read now.
        last = _last_record(store_path)
        place = _record_place(last, install=place)
        _refuse_store_overlap(store_path, place)
        locks.take_install(place)
        _undo_record(store_path, last, place, locks)
    finally:
        locks.release()


def _last_record(store_path: Path) -> _JournalRecord:
    records = _load_journal(store_path)
    if not records:
        raise GuardError(f"nothing to undo: the journal in {store_path} is empty")
    return records[-1]


def _record_place(record: _JournalRecord, *, install: _Place | None = None) -> _Place:
    """The flavor a journal record names, validated like a caller's. With
    `install`, it must be in that install (by identity). Paths that are not
    local and absolute are refused before any lookup."""
    for value in (record.flavor_path, record.install_root):
        if not _local_absolute(value):
            raise GuardError(
                f"journal record {record.id} names {value!r}, which is not a local absolute "
                "path; nothing was undone"
            )
    place = _place(Path(record.flavor_path), record.flavor_version)
    if not _same_dir(Path(record.install_root), place.install_root):
        raise GuardError(
            f"journal record {record.id} names the install {record.install_root}, "
            f"but its flavor is in {place.install_root}"
        )
    if install is not None and place.root_id != install.root_id:
        raise GuardError(
            f"the most recent journal record changed to {record.id}, which belongs to "
            f"{place.install_root}, not {install.install_root}; nothing was undone"
        )
    return place


def _undo_record(store_path: Path, last: _JournalRecord, place: _Place, locks: _Locks) -> None:
    """Plan the undo of `last` and run it as a transaction, under `locks`."""
    snapshots = SnapshotStore(store_path)
    try:
        manifest = snapshots.show(last.snapshot_id)
    except SnapshotError as exc:
        raise GuardError(f"cannot read the pre-write snapshot {last.snapshot_id!r}: {exc}") from exc
    _refuse_foreign_snapshot(manifest, place)
    prefix = place.folder + "/"
    ops: list[_Op] = []
    for item in last.paths:
        parts = _check_rel(item.path)
        entry = manifest.entry(prefix + item.path)
        if item.before is None:
            if entry is not None:
                raise GuardError(
                    f"{item.path!r} was created by the transaction, "
                    f"but snapshot {manifest.id} holds it"
                )
            ops.append((item.path, parts, None, 0))
            continue
        if entry is None or entry.kind != "file" or entry.sha256 != item.before:
            raise GuardError(
                f"snapshot {manifest.id} does not hold the bytes the journal names for {item.path!r}"
            )
        _object(snapshots, manifest, entry)  # sound before anything is written
        ops.append((item.path, parts, item.before, entry.mode))
    created_dirs = list(last.created_dirs)
    for rel_dir in created_dirs:
        _check_rel(rel_dir, directory=True)
    with _Transaction(
        None,
        label=f"undo: {last.label}",
        store_path=store_path,
        dry_run=False,
        place=place,
        locks=locks,
    ) as tx:
        tx._undo(ops, created_dirs)


@contextlib.contextmanager
def store_lock(store: Path | None = None) -> Iterator[None]:
    """Hold the store lock alone, for the duration of the `with` block.

    The same lock a transaction or `undo()` takes on `<store>/lock`, with the
    same mechanism and the same in-process refusal: `GuardBusyError` while
    one is open on this store, here or in another process, and transactions
    on the store are busy while this is held. The store directory must exist
    (`None` means the default store); nothing is ever created but the lock
    file. The store lock has no install, so no install is checked.
    """
    store_path = _store_path(store)
    try:
        st = store_path.stat()
    except FileNotFoundError:
        raise GuardError(
            f"there is no store at {store_path}; store_lock never creates one"
        ) from None
    except OSError as exc:
        raise GuardError(f"cannot inspect the store {store_path}: {exc}") from exc
    if not stat.S_ISDIR(st.st_mode):
        raise GuardError(f"the store {store_path} is not a directory")
    locks = _Locks()
    locks.take_store(store_path, create=False)
    try:
        yield
    finally:
        locks.release()
