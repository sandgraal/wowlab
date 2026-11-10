"""One snapshot as one file, to carry to another computer (M16-04).

`export_snapshot` writes a snapshot of the store as a zip archive at a path
the player names; `import_snapshot` adds one such archive to a store. The
archive is the store's own data and nothing more:

- `manifest.json`: the manifest file, byte for byte as the store holds it
  (`snapshot.manifest_bytes`);
- `objects/<sha256>`: the content of each file the manifest names, once per
  distinct content, uncompressed by the store and deflated by the zip;
- the zip comment `ARCHIVE_COMMENT`, which says what the file is.

Export reads the store only. It writes one new file, created exclusively
(an existing file is never replaced) and never through a link at its last
component, and it refuses a path inside any install before anything is
created there (L1; the rule is `guard`'s, called rather than copied). It
opens nothing in an install. The archive holds what the store holds, so on
POSIX it is private as the store's own files are (M16-41): created 0600 and
set to exactly 0600 whatever the umask (M16-67). On Windows it keeps the
access its folder gives it.

An archive to import is untrusted input: it may come from anyone. Nothing
in it is ever extracted to a path. Every entry name must be exactly
`manifest.json` or `objects/` and 64 lowercase hex digits, each at most
once; anything else (a path with `..`, an absolute path, a directory, a
link, an encrypted entry, an unknown compression, a second entry of one
name) refuses the whole archive. The manifest is held to the store's rules
(`snapshot.parse_manifest`), the archive must hold exactly the objects the
manifest names, and each object's size is bounded (`MAX_OBJECT_BYTES`), as
are their total (`MAX_TOTAL_BYTES`), the manifest (`MAX_MANIFEST_BYTES`)
and the entry count (`MAX_ENTRIES`). Every object is read at most one byte
past its recorded size and hashed before the manifest is added
(`SnapshotStore.add_snapshot`); a corrupt archive adds nothing.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import stat
import struct
import zipfile
import zlib
from datetime import datetime
from pathlib import Path
from typing import IO

from pydantic import BaseModel, ConfigDict

from wowlab_core import snapshot
from wowlab_core.snapshot import (
    ManifestIntegrityError,
    ObjectCorruptError,
    SnapshotError,
    SnapshotStore,
)

__all__ = [
    "ARCHIVE_COMMENT",
    "MAX_DIRECTORY_BYTES",
    "MAX_ENTRIES",
    "MAX_MANIFEST_BYTES",
    "MAX_OBJECT_BYTES",
    "MAX_TOTAL_BYTES",
    "ArchiveError",
    "ExportReport",
    "ImportReport",
    "export_snapshot",
    "import_snapshot",
]

ARCHIVE_COMMENT = b"wowlab snapshot archive 1"
MANIFEST_NAME = "manifest.json"
_OBJECT_NAME = re.compile(r"\Aobjects/([0-9a-f]{64})\Z")

MAX_MANIFEST_BYTES = 64 << 20
"""The largest manifest an import reads (64 MiB: about half a million entries)."""
MAX_OBJECT_BYTES = 1 << 30
"""The largest single file an import accepts (1 GiB)."""
MAX_TOTAL_BYTES = 16 << 30
"""The most file content one import adds (16 GiB)."""
MAX_ENTRIES = 250_000
"""The most entries an archive to import may hold: one per distinct file
content, far more than a WTF and AddOns folder hold."""
_LARGEST_DIRECTORY_ENTRY = 46 + len("objects/") + 64 + 28
"""A central-directory entry of an exported archive: 46 fixed bytes, the
longest name, and a Zip64 extra field at most."""
MAX_DIRECTORY_BYTES = MAX_ENTRIES * _LARGEST_DIRECTORY_ENTRY
"""The largest zip central directory an import lets `zipfile` read (about
35 MiB): room for `MAX_ENTRIES` entries as an export writes them. This is
the bound that holds, since `zipfile` reads entries until the directory
runs out: a hostile directory of the shortest possible entries (46 bytes)
still makes at most `MAX_DIRECTORY_BYTES // 46` (about 793,000) of them."""

_CHUNK = 1 << 20
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)  # every entry's timestamp: the same snapshot, the same bytes
_O_NOFOLLOW: int = getattr(os, "O_NOFOLLOW", 0)


class ArchiveError(SnapshotError):
    """The file is not a snapshot archive this version can import, or holds
    something an archive must not. Nothing was added."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ExportReport(_Frozen):
    """`wowlab snap export --json`."""

    id: str
    file: str
    files: int
    """File entries in the snapshot."""
    objects: int
    """Distinct contents written into the archive."""
    bytes: int
    """Their total size, uncompressed."""
    archive_bytes: int


class ImportReport(_Frozen):
    """`wowlab snap import --json`."""

    id: str
    added: bool
    """False when the store already held this exact snapshot."""
    file: str
    created_at: str
    label: str
    install_root: str
    flavor_folder: str | None
    flavor_version: str | None
    files: int
    objects: int


# ─── export ──────────────────────────────────────────────────────────────────


def _refuse_destination(store: SnapshotStore, dest: Path) -> tuple[Path, Path]:
    """`dest` made absolute, and as resolved; refused inside any install
    (guard's rule, which raises `GuardError`) and inside the store."""
    from wowlab_core import guard  # guard imports snapshot; imported at use

    absolute = Path(dest).absolute()
    resolved = guard._refuse_inside_any_install(absolute, "the export file")
    store_root = store.path.resolve()
    if resolved == store_root or store_root in resolved.parents:
        raise SnapshotError(f"{absolute} is inside the snapshot store; name a file outside it")
    return absolute, resolved


_CREATE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | _O_NOFOLLOW
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_NOCTTY", 0)
)
_ARCHIVE_MODE = snapshot._PRIVATE_FILE_MODE if snapshot._PRIVATE_MODES else 0o666
"""The mode the archive is created with: 0600 on POSIX, as a store file
(M16-67). Elsewhere the default, so the file keeps its folder's access."""
_BY_DIR_FD = hasattr(os, "O_DIRECTORY") and {os.open, os.stat, os.unlink} <= os.supports_dir_fd
"""POSIX: the export file is created, checked and removed through a
descriptor on the folder that was checked, never by its path again."""


def _same_file(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


_WALK_FLAGS = getattr(os, "O_PATH", os.O_RDONLY) | getattr(os, "O_DIRECTORY", 0)
"""How each folder above the export's is opened for the walk: `O_PATH` where
there is one (Linux), so a folder that can be passed through but not listed
is still examined."""


def _refuse_install_above(dir_fd: int, target: Path) -> None:
    """Refuse when the folder `dir_fd` holds, or any folder above it, is an
    install by the rule `guard` applies to a path (an entry named one of
    `guard._INSTALL_MARKERS`, of any kind; a folder that cannot be examined
    counts as one). Walked through descriptors, from the held folder up by
    `..`, so it examines the folders the file will really be created in,
    whatever any path names by now."""
    from wowlab_core import guard  # guard imports snapshot; imported at use

    fd = os.dup(dir_fd)
    try:
        while True:
            for marker in guard._INSTALL_MARKERS:
                try:
                    os.stat(marker, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise guard.GuardError(
                        f"the export file {target}: a folder above it cannot be examined "
                        f"({exc.strerror or exc}); it counts as an install"
                    ) from exc
                raise guard.GuardError(
                    f"the export file {target} would be inside an install (a folder above it "
                    f"holds {marker}); name a file outside every install"
                )
            try:
                parent = os.open("..", _WALK_FLAGS, dir_fd=fd)
            except OSError as exc:
                raise guard.GuardError(
                    f"the export file {target}: a folder above it cannot be examined "
                    f"({exc.strerror or exc}); it counts as an install"
                ) from exc
            if _same_file(os.fstat(parent), os.fstat(fd)):
                os.close(parent)
                return  # the root of the file system
            os.close(fd)
            fd = parent
    finally:
        os.close(fd)


def _open_checked_folder(target: Path, resolved: Path) -> int | None:
    """A descriptor on the folder the new file goes in (POSIX); None
    elsewhere.

    It is opened as `resolved.parent`, the folder `_refuse_destination`
    found by resolving the path, with `O_NOFOLLOW`, so a folder swapped for
    a link since that check is refused (`ELOOP`). Then the folder it holds
    and every folder above it are examined through descriptors
    (`_refuse_install_above`), so a swap anywhere on the way, before or
    after the open, cannot put the file inside an install: on POSIX the
    window is closed, not narrowed. (A folder renamed into an install later
    still moves the file with it; one writer per folder is assumed.)
    Without descriptors (Windows) only the path checks run, before the file
    is created and again after: there the window is narrowed, not closed."""
    if not _BY_DIR_FD:
        return None
    try:
        dir_fd = os.open(resolved.parent, os.O_RDONLY | os.O_DIRECTORY | _O_NOFOLLOW)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise SnapshotError(
                f"the folder of {target} changed as it was checked; nothing was written"
            ) from exc
        raise SnapshotError(f"cannot open the folder of {target}: {exc.strerror or exc}") from exc
    try:
        _refuse_install_above(dir_fd, target)
    except BaseException:
        os.close(dir_fd)
        raise
    return dir_fd


def _remove_own(target: Path, dir_fd: int | None, created: os.stat_result) -> None:
    """Remove the file this export created, and nothing else: only when its
    name (in the held folder, POSIX; by path elsewhere) still names that
    very file by device and inode and is not a link. Anything else at the
    name, such as an install's own file reached through a folder swapped
    for a link, is left alone, and so is the export's file, then."""
    try:
        if dir_fd is not None:
            now = os.stat(target.name, dir_fd=dir_fd, follow_symlinks=False)
        else:
            now = os.lstat(target)
    except OSError:
        return
    if stat.S_ISLNK(now.st_mode) or not _same_file(now, created):
        return
    with contextlib.suppress(OSError):
        if dir_fd is not None:
            os.unlink(target.name, dir_fd=dir_fd)
        else:
            target.unlink()


def export_snapshot(store: SnapshotStore, snapshot_id: str, dest: Path) -> ExportReport:
    """Write snapshot `snapshot_id` (a full id or unique prefix) of `store`
    to a new archive at `dest`.

    Every object is read back from the store and checked against its name
    and recorded size first (`read_object`), so an archive never carries a
    damaged object. `dest` must not exist; it is created exclusively and
    without following a link at its last component, and removed again if
    the export fails part way, but only while its name still names the file
    this export created (`_remove_own`). Refused before anything is
    created: a `dest` inside any install (`guard.GuardError`) or inside the
    store. On POSIX the file is created through a descriptor on its folder,
    held to be the folder that was checked. On POSIX the archive is 0600
    whatever the umask, as the store's own files are (M16-67)."""
    manifest = store.show(snapshot_id)
    data = snapshot.manifest_bytes(manifest)
    sizes: dict[str, int] = {}
    for entry in manifest.entries:
        if entry.kind == "file" and entry.sha256 is not None:
            sizes.setdefault(entry.sha256, entry.size)
    target, resolved = _refuse_destination(store, dest)
    dir_fd = _open_checked_folder(target, resolved)
    try:
        try:
            if dir_fd is not None:
                fd = os.open(target.name, _CREATE_FLAGS, _ARCHIVE_MODE, dir_fd=dir_fd)
            else:
                fd = os.open(target, _CREATE_FLAGS, _ARCHIVE_MODE)
        except FileExistsError:
            raise SnapshotError(
                f"{target} already exists; name a new file, or move that one aside"
            ) from None
        except OSError as exc:
            raise SnapshotError(f"cannot create {target}: {exc.strerror or exc}") from exc
        created = os.fstat(fd)
        try:
            with os.fdopen(fd, "wb") as out:
                if not stat.S_ISREG(created.st_mode):
                    raise SnapshotError(f"{target} is not a regular file")
                with contextlib.suppress(OSError):  # best effort, as the store's files
                    snapshot._set_private(fd, created, exact=True)  # 0600 whatever the umask
                # Asked again now that it exists: the name the player gave
                # must still lead outside every install, or the export is
                # refused before a byte is written. The cleanup below then
                # removes only this export's own file (`_remove_own`).
                _refuse_destination(store, target)
                with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                    zf.comment = ARCHIVE_COMMENT
                    zf.writestr(_member(MANIFEST_NAME), data)
                    for digest in sorted(sizes):
                        zf.writestr(
                            _member(f"objects/{digest}"),
                            store.read_object(digest, size=sizes[digest]),
                        )
                out.flush()
                os.fsync(out.fileno())
                archive_bytes = os.fstat(out.fileno()).st_size
        except BaseException:
            _remove_own(target, dir_fd, created)
            raise
    finally:
        if dir_fd is not None:
            os.close(dir_fd)
    return ExportReport(
        id=manifest.id,
        file=str(target),
        files=sum(1 for e in manifest.entries if e.kind == "file"),
        objects=len(sizes),
        bytes=sum(sizes.values()),
        archive_bytes=archive_bytes,
    )


def _member(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


# ─── import ──────────────────────────────────────────────────────────────────


class _MemberSource:
    """One archive entry as `add_snapshot` reads it: zip and inflate errors
    (a bad CRC, a truncated stream) become `ObjectCorruptError`."""

    def __init__(self, stream: IO[bytes], digest: str) -> None:
        self._stream = stream
        self._digest = digest

    def read(self, size: int, /) -> bytes:
        try:
            return self._stream.read(size)
        except (zipfile.BadZipFile, zlib.error, EOFError, OSError, ValueError) as exc:
            raise ObjectCorruptError(f"object {self._digest} does not read back: {exc}") from exc

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._stream.close()


def _is_link_entry(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return bool(mode) and stat.S_IFMT(mode) not in (0, stat.S_IFREG)


def _check_entries(
    infos: list[zipfile.ZipInfo], max_entries: int
) -> tuple[zipfile.ZipInfo, dict[str, zipfile.ZipInfo]]:
    """The manifest entry and the object entries by SHA-256, each name held
    to the archive's two forms and seen once; else `ArchiveError`."""
    if len(infos) > max_entries:
        raise ArchiveError(f"the archive holds {len(infos)} entries, more than {max_entries}")
    seen: set[str] = set()
    manifest_info: zipfile.ZipInfo | None = None
    objects: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        name = info.filename
        if name in seen:
            raise ArchiveError(f"the archive holds the entry {name!r} twice")
        seen.add(name)
        match = _OBJECT_NAME.match(name)
        if name != MANIFEST_NAME and match is None:
            raise ArchiveError(f"the archive holds an entry this format does not have: {name!r}")
        if info.flag_bits & 0x1:
            raise ArchiveError(f"the entry {name!r} is encrypted")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ArchiveError(f"the entry {name!r} uses a compression this format does not")
        if _is_link_entry(info):
            raise ArchiveError(f"the entry {name!r} is a link or not a plain file")
        if match is None:
            manifest_info = info
        else:
            objects[match.group(1)] = info
    if manifest_info is None:
        raise ArchiveError(f"the archive holds no {MANIFEST_NAME}")
    return manifest_info, objects


def _read_manifest(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    if info.file_size > limit:
        raise ArchiveError(f"{MANIFEST_NAME} records {info.file_size} bytes, more than {limit}")
    try:
        with zf.open(info, mode="r") as stream:
            data = stream.read(limit + 1)
    except (zipfile.BadZipFile, zlib.error, EOFError, OSError, ValueError) as exc:
        raise ArchiveError(f"{MANIFEST_NAME} does not read back: {exc}") from exc
    if len(data) > limit:
        raise ArchiveError(f"{MANIFEST_NAME} holds more than {limit} bytes")
    return data


_EOCD = struct.Struct("<4s4H2LH")  # signature, disks, counts, size, offset, comment length
_EOCD_SIG = b"PK\x05\x06"
_ZIP64_LOCATOR = struct.Struct("<4sLQL")
_ZIP64_LOCATOR_SIG = b"PK\x06\x07"
_ZIP64_EOCD = struct.Struct("<4sQ2H2L4Q")
_ZIP64_EOCD_SIG = b"PK\x06\x06"


def _read_at(handle: IO[bytes], offset: int, size: int) -> bytes:
    handle.seek(offset)
    return handle.read(size)


def _bound(
    count: int, cd_size: int, cd_offset: int, file_size: int, limits: tuple[int, int]
) -> None:
    max_entries, max_directory_bytes = limits
    if count > max_entries:
        raise ArchiveError(f"the archive holds {count} entries, more than {max_entries}")
    if cd_size > max_directory_bytes or cd_offset + cd_size > file_size:
        raise ArchiveError(
            f"the archive's directory is {cd_size} bytes, more than the file or the "
            f"{max_directory_bytes} one import reads"
        )


def _check_end_record(
    handle: IO[bytes], file_size: int, max_entries: int, max_directory_bytes: int
) -> None:
    """Read the end-of-central-directory record, and every Zip64 record
    `zipfile` could take in its place, before `zipfile` reads the directory,
    and refuse an archive any of whose counts or directory sizes is over the
    bounds, or whose comment is not `ARCHIVE_COMMENT`.

    An archive `export_snapshot` writes ends in that record followed by
    exactly that comment, so the record is looked for there and nowhere
    else. CPython's `zipfile` follows a Zip64 locator whenever its signature
    sits just before that record, whatever the record's own fields say, and
    then reads the Zip64 record at the locator's offset or, failing that,
    the one just before the locator. So whenever the locator is there, both
    places are read, and every record found at either is held to the bounds
    as the classic record is; a locator with neither is refused.

    The directory size is the bound that holds: `zipfile` parses entries
    until the directory's bytes run out, whatever count a record claims, so
    `MAX_DIRECTORY_BYTES` is sized to `MAX_ENTRIES` (see there) and the
    count is checked as well, not instead."""
    at = file_size - _EOCD.size - len(ARCHIVE_COMMENT)
    if at < 0:
        raise ArchiveError("the file is too short to be a snapshot archive")
    raw = _read_at(handle, at, _EOCD.size + len(ARCHIVE_COMMENT))
    sig, disk, cd_disk, here, count, cd_size, cd_offset, comment_len = _EOCD.unpack(
        raw[: _EOCD.size]
    )
    if (
        sig != _EOCD_SIG
        or comment_len != len(ARCHIVE_COMMENT)
        or raw[_EOCD.size :] != ARCHIVE_COMMENT
    ):
        raise ArchiveError("the file is not a snapshot archive written by wowlab snap export")
    # `zipfile` finds the end record by the last end signature in the file's
    # tail (`rfind`): one anywhere after this record's first byte would make
    # it read a different record from the one bounded here. And an export is
    # one disk, so its disk fields are 0.
    if _EOCD_SIG in raw[1:] or disk != 0 or cd_disk != 0:
        raise ArchiveError("the archive's end record is not one wowlab snap export writes")
    limits = (max_entries, max_directory_bytes)
    _bound(max(here, count), cd_size, cd_offset, file_size, limits)
    locator_at = at - _ZIP64_LOCATOR.size
    if locator_at < 0:
        return
    lsig, ldisk, zip64_at, disks = _ZIP64_LOCATOR.unpack(
        _read_at(handle, locator_at, _ZIP64_LOCATOR.size)
    )
    if lsig != _ZIP64_LOCATOR_SIG:
        return
    if ldisk != 0 or disks > 1:
        raise ArchiveError("the archive's Zip64 locator names another disk")
    found = 0
    for place in {zip64_at, locator_at - _ZIP64_EOCD.size}:
        if place < 0 or place + _ZIP64_EOCD.size > locator_at:
            continue
        fields = _ZIP64_EOCD.unpack(_read_at(handle, place, _ZIP64_EOCD.size))
        if fields[0] != _ZIP64_EOCD_SIG:
            continue
        if fields[4] != 0 or fields[5] != 0:
            raise ArchiveError("the archive's Zip64 record names another disk")
        found += 1
        _bound(max(fields[6], fields[7]), fields[8], fields[9], file_size, limits)
    if not found:
        raise ArchiveError("the archive's Zip64 record is missing or out of place")


def import_snapshot(
    store: SnapshotStore,
    path: Path,
    *,
    max_object_bytes: int = MAX_OBJECT_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_manifest_bytes: int = MAX_MANIFEST_BYTES,
    max_entries: int = MAX_ENTRIES,
    max_directory_bytes: int = MAX_DIRECTORY_BYTES,
    now: datetime | None = None,
) -> ImportReport:
    """Add the snapshot in the archive at `path` to `store`.

    The archive is read only: opened as `snapshot.open_regular_file` opens
    a store file (never through a link, never blocking on a FIFO), and no
    entry is ever extracted to a path. Every check in the module docstring
    runs before the store is touched; then `add_snapshot` streams and
    hashes every object before the manifest is added. A refusal is an
    `ArchiveError`, a `ManifestIntegrityError`, an `ObjectCorruptError` or a
    `SnapshotExistsError` (a different snapshot under the same id), and
    adds nothing. Callers hold the store lock (`guard.store_lock`).

    The entry count, the size of the zip's central directory and its comment
    are checked from the end record before `zipfile` reads the directory
    (`_check_end_record`). The manifest is held to
    `snapshot.check_foreign_manifest` (its date against `now`, default this
    computer's clock), and the snapshot is recorded as imported."""
    source = Path(path)
    try:
        fd, _ = snapshot.open_regular_file(source)
    except FileNotFoundError:
        raise ArchiveError(f"there is no file {source}") from None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ArchiveError(f"{source} is a link; name the file itself") from exc
        raise ArchiveError(f"cannot read {source}: {exc}") from exc
    try:
        handle = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    with handle:
        try:
            _check_end_record(
                handle, os.fstat(handle.fileno()).st_size, max_entries, max_directory_bytes
            )
        except (OSError, struct.error) as exc:
            raise ArchiveError(f"{source} is not a snapshot archive ({exc})") from exc
        try:
            zf = zipfile.ZipFile(handle)
        except (zipfile.BadZipFile, zlib.error, EOFError, OSError, ValueError) as exc:
            raise ArchiveError(f"{source} is not a snapshot archive ({exc})") from exc
        with zf:
            if zf.comment != ARCHIVE_COMMENT:
                raise ArchiveError(
                    f"{source} is not a snapshot archive written by wowlab snap export"
                )
            manifest_info, objects = _check_entries(zf.infolist(), max_entries)
            data = _read_manifest(zf, manifest_info, max_manifest_bytes)
            manifest = snapshot.parse_manifest(data)
            wanted: dict[str, int] = {}
            for entry in manifest.entries:
                if entry.kind != "file" or entry.sha256 is None:
                    continue
                if wanted.setdefault(entry.sha256, entry.size) != entry.size:
                    raise ManifestIntegrityError(
                        f"manifest {manifest.id}: object {entry.sha256} is given two sizes"
                    )
            missing = sorted(wanted.keys() - objects.keys())
            if missing:
                raise ArchiveError(
                    f"the archive lacks {len(missing)} file(s) its snapshot names "
                    f"(first: {missing[0]})"
                )
            extra = sorted(objects.keys() - wanted.keys())
            if extra:
                raise ArchiveError(
                    f"the archive holds {len(extra)} file(s) its snapshot does not name "
                    f"(first: {extra[0]})"
                )
            total = 0
            for digest, size in sorted(wanted.items()):
                if size > max_object_bytes:
                    raise ArchiveError(
                        f"object {digest} is {size} bytes, more than the {max_object_bytes} "
                        "one import accepts"
                    )
                if objects[digest].file_size != size:
                    raise ArchiveError(
                        f"object {digest} is stored as {objects[digest].file_size} bytes, "
                        f"but its snapshot records {size}"
                    )
                total += size
            if total > max_total_bytes:
                raise ArchiveError(
                    f"the snapshot holds {total} bytes, more than the {max_total_bytes} "
                    "one import accepts"
                )

            def open_object(digest: str) -> snapshot.ObjectSource:
                try:
                    stream = zf.open(objects[digest], mode="r")
                except (zipfile.BadZipFile, zlib.error, EOFError, OSError, ValueError) as exc:
                    raise ObjectCorruptError(f"object {digest} does not read back: {exc}") from exc
                return _MemberSource(stream, digest)

            manifest, added = store.add_snapshot(data, open_object, now=now)
    return ImportReport(
        id=manifest.id,
        added=added,
        file=str(source),
        created_at=manifest.created_at,
        label=manifest.label,
        install_root=manifest.install_root,
        flavor_folder=manifest.flavor_folder,
        flavor_version=manifest.flavor_version,
        files=sum(1 for e in manifest.entries if e.kind == "file"),
        objects=len(wanted),
    )
