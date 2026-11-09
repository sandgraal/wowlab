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
opens nothing in an install.

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
import zipfile
import zlib
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
MAX_ENTRIES = 1_000_000
"""The most entries an archive to import may hold."""

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


def _refuse_destination(store: SnapshotStore, dest: Path) -> Path:
    """`dest` made absolute; refused inside any install (guard's rule, which
    raises `GuardError`) and inside the store."""
    from wowlab_core import guard  # guard imports snapshot; imported at use

    absolute = Path(dest).absolute()
    resolved = guard._refuse_inside_any_install(absolute, "the export file")
    store_root = store.path.resolve()
    if resolved == store_root or store_root in resolved.parents:
        raise SnapshotError(f"{absolute} is inside the snapshot store; name a file outside it")
    return absolute


def export_snapshot(store: SnapshotStore, snapshot_id: str, dest: Path) -> ExportReport:
    """Write snapshot `snapshot_id` (a full id or unique prefix) of `store`
    to a new archive at `dest`.

    Every object is read back from the store and checked against its name
    and recorded size first (`read_object`), so an archive never carries a
    damaged object. `dest` must not exist; it is created exclusively and
    without following a link at its last component, and removed again if
    the export fails part way. Refused before anything is created: a
    `dest` inside any install (`guard.GuardError`) or inside the store."""
    manifest = store.show(snapshot_id)
    data = snapshot.manifest_bytes(manifest)
    sizes: dict[str, int] = {}
    for entry in manifest.entries:
        if entry.kind == "file" and entry.sha256 is not None:
            sizes.setdefault(entry.sha256, entry.size)
    target = _refuse_destination(store, dest)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | _O_NOFOLLOW
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOCTTY", 0)
    )
    try:
        fd = os.open(target, flags, 0o666)
    except FileExistsError:
        raise SnapshotError(
            f"{target} already exists; name a new file, or move that one aside"
        ) from None
    except OSError as exc:
        raise SnapshotError(f"cannot create {target}: {exc.strerror or exc}") from exc
    try:
        with os.fdopen(fd, "wb") as out:
            if not stat.S_ISREG(os.fstat(out.fileno()).st_mode):
                raise SnapshotError(f"{target} is not a regular file")
            # Asked again now that it exists: a folder on the way swapped for
            # a link into an install after the first check refuses it here,
            # before a byte is written, and the new empty file is removed.
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
        with contextlib.suppress(OSError):
            target.unlink()
        raise
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


def import_snapshot(
    store: SnapshotStore,
    path: Path,
    *,
    max_object_bytes: int = MAX_OBJECT_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_manifest_bytes: int = MAX_MANIFEST_BYTES,
    max_entries: int = MAX_ENTRIES,
) -> ImportReport:
    """Add the snapshot in the archive at `path` to `store`.

    The archive is read only: opened as `snapshot.open_regular_file` opens
    a store file (never through a link, never blocking on a FIFO), and no
    entry is ever extracted to a path. Every check in the module docstring
    runs before the store is touched; then `add_snapshot` streams and
    hashes every object before the manifest is added. A refusal is an
    `ArchiveError`, a `ManifestIntegrityError`, an `ObjectCorruptError` or a
    `SnapshotExistsError` (a different snapshot under the same id), and
    adds nothing. Callers hold the store lock (`guard.store_lock`)."""
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

            manifest, added = store.add_snapshot(data, open_object)
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
