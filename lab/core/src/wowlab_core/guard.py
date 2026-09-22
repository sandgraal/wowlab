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
   must not overlap the install in either direction (L1).
3. `wowlab_core.process` is asked, with its default probe, about the install
   root, the flavor folder and the executable names found in the flavor
   folder. `running`, `unknown`, and any exception count as running (§6.7 as
   amended).
4. A pre-write snapshot of the flavor's `WTF/`, `Interface/` and `Fonts/` is
   taken (§6.9 defaults; `Interface/` holds `AddOns/` and the loose overrides).
5. A journal record naming that snapshot is written under the store, in
   `journal/`, and fsynced.

Each operation then checks the path lexically (the allowlist, executables,
Windows spellings), walks it on disk without following any link, junction or
other reparse point, refuses a name that differs only in case or Unicode form
from one on disk, and refuses a name the file system resolves to something it
does not list (an 8.3 short name). The path and its `before` hash are written
to the journal and fsynced before the install is touched (write-ahead), so a
process killed at any point can still be undone from the journal alone. The
new bytes go to a temp file in the same directory, which is fsynced, then
`os.replace`d over the target. The chain of directories from the install root
down is re-checked (identity, and not a link) before the temp file is
opened, after it is opened (and the temp file must be where it was created),
and again before the rename or unlink.

Mutations are made by absolute path, on every platform, so the same code runs
on Windows (where there are no directory descriptors). The residual window is
the moment between the last re-check and the rename or unlink; a process of
the same user that swaps a directory for a link in exactly that moment can
redirect one rename (whose unguessable temp-file source then does not exist,
so it fails) or one unlink. Such a process can already write the install
directly; the checks exist so links planted in advance and ordinary races
never carry a write out of the install.

On an exception of any kind inside the body, every touched path is put back
to the bytes its journaled `before` hash names, and every directory the
transaction created is removed again. Those bytes come from the pre-write
snapshot's object store, looked up by that hash (the store checks content
against the name, so a manifest cannot substitute other bytes); when the
snapshot's entry for the path does not name that hash (the file changed
after the snapshot, or the snapshot lies), the bytes read at first touch are
kept in memory instead. The record says `rolled_back` only if all of that
succeeded. A `restore` that fails after changing a path leaves the
transaction unable to commit: it rolls back on exit even if the caller
catches the error.

`undo()` is itself a transaction: it takes the most recent journal record,
restores only the paths it journaled from its pre-write snapshot, and refuses
before writing anything if a journaled path is not allowed, if the snapshot
holds a link there, or if the snapshot entry disagrees with the journal's
`before`. Snapshot manifests and the journal are untrusted input at rollback,
undo and restore time: every path is checked again on the restoring platform
and a link is never created. Restored and undone files never gain permission
bits: an existing file keeps no more than its current bits and the recorded
bits; a recreated one gets its recorded bits less the umask and never an
execute bit.

This module never lists processes itself, never reads the environment, and
has no switch that skips the client check, the snapshot or the allowlist.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import logging
import os
import re
import stat
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, field_validator

from wowlab_core import process
from wowlab_core.snapshot import (
    Entry,
    Manifest,
    SnapshotError,
    SnapshotStore,
    default_store_path,
)

__all__ = [
    "ClientRunningError",
    "GuardError",
    "HistoryRecord",
    "PathChange",
    "PathNotAllowedError",
    "PlanItem",
    "history",
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
_JOURNAL_FORMAT = 1
_RECORD_NAME = re.compile(r"\A(\d{8})-[0-9a-f]{8}\.json\Z")
_MAX_RECORD_BYTES = 64 << 20
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_TEMP_PREFIX = ".wowlab-"
_CHUNK = 1 << 20

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

    @field_validator("format")
    @classmethod
    def _known_format(cls, value: int) -> int:
        if value != _JOURNAL_FORMAT:
            raise ValueError(f"journal format {value}, expected {_JOURNAL_FORMAT}")
        return value


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
        return default_store_path().absolute()
    if not isinstance(store, Path):
        raise GuardError(
            f"store is a directory path, not a {type(store).__name__}; "
            "guard builds its own SnapshotStore"
        )
    return store.absolute()


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


def _refuse_store_overlap(store_path: Path, place: _Place) -> None:
    """L1: the store is never inside the install, and never contains it."""
    try:
        store = store_path.resolve()
    except (OSError, RuntimeError) as exc:
        raise GuardError(f"cannot resolve the store path {store_path}: {exc}") from exc
    root = place.install_root
    overlap = store == root or root in store.parents or store in root.parents
    overlap = overlap or any(_same_dir(p, root) for p in (store, *store.parents))
    overlap = overlap or any(_same_dir(p, store) for p in (root, *root.parents))
    if overlap:
        raise GuardError(
            f"the store ({store}) and the install ({root}) must not contain each other"
        )


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


def _check_rel(rel: object, *, directory: bool = False) -> tuple[str, ...]:
    """Hold a flavor-relative path to the allowlist, lexically. Returns its parts.

    A file path is `/`-separated, has at least two parts, starts with an
    allowlist root spelled exactly, contains no executable component and no
    name that means something else on Windows. `directory=True` also accepts
    the allowlist roots themselves (for directories a transaction created).
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
        if _is_executable(part):
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


def _replace(
    found: _Found, chain: Sequence[tuple[Path, _Identity]], data: bytes, rule: _ModeRule
) -> None:
    """Temp file in the same directory, fsync, re-check, atomic replace."""
    rel = found.rel
    create_mode, chmod_to = _modes(rule, found.st)
    tmp = found.path.parent / f"{_TEMP_PREFIX}{uuid.uuid4().hex}.tmp"
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
        _verify(chain, rel)
        tmp.replace(found.path)
        replaced = True
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
) -> None:
    """Put `data` at `parts` (None deletes), after walking the path again and
    checking the target is still what `expect` found. Directories made on the
    way are appended to `created` as they are made."""
    found = _look(place, parts)
    rel = found.rel
    if not _same_state(found.st, expect.st):
        raise GuardError(f"{rel} changed during the transaction")
    if data is None:
        if found.st is None:
            return
        _verify(found.chain, rel)
        try:
            found.path.unlink()
        except OSError as exc:
            raise GuardError(f"cannot delete {rel}: {exc}") from exc
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
    _replace(found, chain, data, rule)


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
        "_created",
        "_dry_run",
        "_flavor",
        "_journal",
        "_label",
        "_order",
        "_overlay",
        "_place",
        "_plan",
        "_planned_dirs",
        "_pre_hashes",
        "_record",
        "_status",
        "_store_path",
    )

    def __init__(
        self,
        flavor: object,
        *,
        label: str,
        store_path: Path,
        dry_run: bool,
        place: _Place | None = None,
    ) -> None:
        self._flavor = flavor
        self._label = label
        self._store_path = store_path
        self._dry_run = dry_run
        self._place = place
        self._status: Literal["new", "open", "closed"] = "new"
        self._record: _JournalRecord | None = None
        self._order: list[str] = []
        self._journal: dict[str, tuple[str | None, str | None]] = {}
        # First-touch state of each path: its hash (None: absent), its bytes
        # when the pre-write snapshot cannot supply them, and its mode.
        self._before: dict[str, tuple[str | None, bytes | None, int]] = {}
        self._pre_hashes: dict[str, str | None] = {}
        self._created: list[str] = []
        self._planned_dirs: list[str] = []
        self._plan: dict[str, PlanItem] = {}
        self._overlay: dict[str, bytes | None] = {}
        self._broken: str | None = None

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
        _refuse_if_client_running(place)
        self._place = place
        if not self._dry_run:
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
                raise GuardError(
                    f"the pre-write snapshot failed; nothing was written: {exc}"
                ) from exc
            prefix = place.folder + "/"
            self._pre_hashes = {
                e.path.removeprefix(prefix): e.sha256
                for e in manifest.entries
                if e.path.startswith(prefix) and e.kind == "file"
            }
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
            )
            _write_record(self._store_path, self._record)
        self._status = "open"
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        if self._status != "open":
            return False
        self._status = "closed"
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
        if self._status != "open":
            raise GuardError("this transaction is not open")

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
            }
        )
        _write_record(self._store_path, self._record)

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
        if data is None and current is None:
            raise GuardError(f"{rel} does not exist")
        before = None if current is None else _sha(current)
        previous = self._journal.get(rel)
        if previous is None:
            self._order.append(rel)
            # The pre-write snapshot holds these bytes when its entry names this
            # hash; rollback then reads the object by that hash, which the store
            # verifies. Otherwise (a file changed since the snapshot, or a
            # snapshot that lies) the bytes are kept here.
            held = None if before is None or self._pre_hashes.get(rel) == before else current
            self._before[rel] = (before, held, mode)
            self._journal[rel] = (before, after)
        else:
            self._journal[rel] = (previous[0], after)
        self._planned_dirs.extend(d for d in found.missing if d not in self._planned_dirs)
        # Write-ahead: the path and its `before` are durable before it changes.
        self._journal_write("open")
        try:
            _mutate(self._place, parts, data, rule, found, self._created)
        except Exception:
            # The path did not change: the record says so again.
            self._journal[rel] = (
                self._journal[rel][0],
                before if previous is None else previous[1],
            )
            with contextlib.suppress(GuardError):
                self._journal_write("open")
            raise
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
            parts = _check_rel(rel)
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

    def _same_link(self, parts: Sequence[str], entry: Entry) -> bool:
        """True when the path on disk is already a link with the entry's target."""
        assert self._place is not None
        found = _look(self._place, parts[:-1], want_dir=True) if len(parts) > 1 else None
        if found is not None and found.st is None:
            return False
        path = self._place.flavor_dir.joinpath(*parts)
        try:
            return path.is_symlink() and str(path.readlink()) == entry.target
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
        """Put every touched path back from the bytes read when it was first
        touched; remove the directories this transaction created."""
        assert self._place is not None
        problems: list[str] = []
        store = SnapshotStore(self._store_path)
        for rel in reversed(self._order):
            before, held, mode = self._before[rel]
            parts = tuple(rel.split("/"))
            try:
                found = _look(self._place, parts)
                current = _read(found)
                if (None if current is None else _sha(current)) == before:
                    continue
                data = held if held is not None or before is None else _fetch(store, before, rel)
                _mutate(self._place, parts, data, ("exact", mode), found, [])
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
    the user data directory. With `dry_run` nothing is written anywhere: the
    same checks run, and `tx.plan` holds what would change.
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
    `undo()` undoes it.
    """
    store_path = _store_path(store)
    records = _load_journal(store_path)
    if not records:
        raise GuardError(f"nothing to undo: the journal in {store_path} is empty")
    last = records[-1]
    place = _place(Path(last.flavor_path), last.flavor_version)
    _refuse_store_overlap(store_path, place)
    snapshots = SnapshotStore(store_path)
    try:
        manifest = snapshots.show(last.snapshot_id)
    except SnapshotError as exc:
        raise GuardError(f"cannot read the pre-write snapshot {last.snapshot_id!r}: {exc}") from exc
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
        None, label=f"undo: {last.label}", store_path=store_path, dry_run=False, place=place
    ) as tx:
        tx._undo(ops, created_dirs)
