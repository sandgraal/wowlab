"""`snaparchive`: one snapshot as one file, and hostile archives (M16-04).

The archive is wowlab's own format, so its hostile cases are constructed
(L8: hostile and boundary inputs, labelled `constructed` in every test id):
an archive is first written by `export_snapshot` from a small constructed
tree, then rewritten entry by entry into what an attacker could hand a
player. Each refusal is checked to leave the receiving store exactly as it
was: no manifest, no object, nothing left under `tmp/`.

The round trip between two sandboxes on the real capture, followed by a
restore, is in `test_cli_snap_export_import.py`.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import stat
import struct
import sys
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import guard, pathfacts, snaparchive, snapshot
from wowlab_core.snaparchive import ARCHIVE_COMMENT, ArchiveError
from wowlab_core.snapshot import (
    ManifestIntegrityError,
    ObjectCorruptError,
    SnapshotError,
    SnapshotExistsError,
    SnapshotStore,
)

A = b"constructed: settings A\n"
B = b"constructed: saved data B, a little longer than A\n" * 40
SHA_A = hashlib.sha256(A).hexdigest()
SHA_B = hashlib.sha256(B).hexdigest()
FOLDER = "_flavor_"  # constructed: the flavor folder the snapshot is taken of (test data, L6)


def _tree(tmp_path: Path) -> Path:
    """Constructed: a small tree with two contents, one stored twice."""
    root = tmp_path / "source"
    (root / FOLDER / "WTF" / "Account").mkdir(parents=True)
    (root / FOLDER / "WTF" / "Config.wtf").write_bytes(A)
    (root / FOLDER / "WTF" / "Account" / "copy.wtf").write_bytes(A)
    (root / FOLDER / "WTF" / "Account" / "data.lua").write_bytes(B)
    return root


@pytest.fixture
def source(tmp_path: Path) -> tuple[SnapshotStore, snapshot.Manifest]:
    store = SnapshotStore(tmp_path / "store-a")
    manifest = store.create(
        _tree(tmp_path), [f"{FOLDER}/WTF"], label="constructed", flavor_folder=FOLDER
    )
    return store, manifest


@pytest.fixture
def archive(tmp_path: Path, source: tuple[SnapshotStore, snapshot.Manifest]) -> Path:
    store, manifest = source
    out = tmp_path / "carry" / "snap.zip"
    out.parent.mkdir()
    snaparchive.export_snapshot(store, manifest.id, out)
    return out


def _store_state(store: SnapshotStore) -> dict[str, bytes | None]:
    if not store.path.exists():
        return {}
    return {
        p.relative_to(store.path).as_posix(): (p.read_bytes() if p.is_file() else None)
        for p in sorted(store.path.rglob("*"))
    }


def _entries(path: Path) -> list[tuple[zipfile.ZipInfo, bytes]]:
    with zipfile.ZipFile(path) as zf:
        return [(info, zf.read(info)) for info in zf.infolist()]


def _rewrite(
    path: Path,
    edit: Callable[[list[tuple[zipfile.ZipInfo, bytes]]], list[tuple[zipfile.ZipInfo, bytes]]],
    *,
    comment: bytes = ARCHIVE_COMMENT,
) -> Path:
    """Constructed: the archive at `path` with its entries passed through `edit`."""
    entries = edit(_entries(path))
    out = path.with_name("hostile.zip")
    with (
        pytest.warns(UserWarning) if _has_duplicates(entries) else contextlib.nullcontext(),
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf,
    ):
        zf.comment = comment
        for info, data in entries:
            zf.writestr(info, data)
    return out


def _has_duplicates(entries: list[tuple[zipfile.ZipInfo, bytes]]) -> bool:
    names = [info.filename for info, _ in entries]
    return len(names) != len(set(names))


def _entry(
    name: str, data: bytes, *, mode: int = stat.S_IFREG | 0o644
) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = mode << 16
    return info, data


def _refused(
    tmp_path: Path,
    path: Path,
    error: type[Exception],
    match: str | None = None,
    **limits: int,
) -> SnapshotStore:
    """Import `path` into a new store; it must be refused with `error` and
    add nothing."""
    store = SnapshotStore(tmp_path / "store-b")
    store.create(_other_tree(tmp_path), ["x"])  # the receiving store is not empty
    before = _store_state(store)
    with pytest.raises(error, match=match):
        snaparchive.import_snapshot(store, path, **limits)
    assert _store_state(store) == before, "a refused archive adds nothing"
    return store


def _other_tree(tmp_path: Path) -> Path:
    root = tmp_path / "other"
    (root / "x").mkdir(parents=True, exist_ok=True)
    (root / "x" / "f").write_bytes(b"constructed: already in the receiving store\n")
    return root


# ─── the round trip ──────────────────────────────────────────────────────────


def test_export_then_import_adds_the_same_snapshot_constructed(
    tmp_path: Path, source: tuple[SnapshotStore, snapshot.Manifest], archive: Path
) -> None:
    _, manifest = source
    names = sorted(info.filename for info, _ in _entries(archive))
    assert names == ["manifest.json", f"objects/{SHA_A}", f"objects/{SHA_B}"]

    store = SnapshotStore(tmp_path / "store-b")
    report = snaparchive.import_snapshot(store, archive)
    assert report.added and report.id == manifest.id and report.objects == 2
    assert store.show(manifest.id) == manifest
    assert store.read_file(manifest.id, f"{FOLDER}/WTF/Account/data.lua") == B
    assert store.verify().ok
    assert not any((store.path / "tmp").iterdir())

    again = snaparchive.import_snapshot(store, archive)
    assert not again.added


def test_export_is_deterministic_constructed(
    tmp_path: Path, source: tuple[SnapshotStore, snapshot.Manifest], archive: Path
) -> None:
    store, manifest = source
    second = tmp_path / "carry" / "again.zip"
    snaparchive.export_snapshot(store, manifest.id, second)
    assert second.read_bytes() == archive.read_bytes()


def test_import_reuses_objects_already_in_the_store_constructed(
    tmp_path: Path, archive: Path
) -> None:
    store = SnapshotStore(tmp_path / "store-b")
    store.put_object(A)
    report = snaparchive.import_snapshot(store, archive)
    assert report.added and store.verify().ok


# ─── export refusals ─────────────────────────────────────────────────────────


@pytest.mark.skipif(os.name != "posix", reason="file modes are a POSIX matter")
@pytest.mark.parametrize(
    "mask", [0o022, 0o000, 0o002], ids=["umask-0022", "umask-0000", "umask-0002"]
)
def test_export_writes_the_archive_0600_whatever_the_umask_constructed(
    tmp_path: Path, source: tuple[SnapshotStore, snapshot.Manifest], mask: int
) -> None:
    """M16-67: the archive holds what the store holds, so it is private as
    the store's files are (M16-41), not 0666 less the umask."""
    store, manifest = source
    out = tmp_path / "carry.zip"
    old = os.umask(mask)
    try:
        snaparchive.export_snapshot(store, manifest.id, out)
    finally:
        os.umask(old)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    with zipfile.ZipFile(out) as zf:
        assert zf.comment == ARCHIVE_COMMENT


@pytest.mark.skipif(os.name != "posix", reason="file modes are a POSIX matter")
def test_export_is_created_0600_before_any_byte_is_written_constructed(
    tmp_path: Path,
    source: tuple[SnapshotStore, snapshot.Manifest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M16-67: the file is created 0600, not widened and narrowed after, so
    there is no moment another account could open it. Shown by the mode
    `os.open` is asked for, and by the export still being 0600 when the
    exact `fchmod` is refused (best effort, as for the store's files)."""
    store, manifest = source
    asked: list[int] = []
    real_open = os.open

    def spy_open(path: Any, flags: int, mode: int = 0o777, **kw: Any) -> int:
        if flags & os.O_CREAT:
            asked.append(mode)
        return real_open(path, flags, mode, **kw)

    def no_fchmod(fd: int, mode: int) -> None:
        raise PermissionError("constructed: fchmod refused")

    monkeypatch.setattr(snaparchive.os, "open", spy_open)
    monkeypatch.setattr(snapshot.os, "fchmod", no_fchmod)
    out = tmp_path / "carry.zip"
    old = os.umask(0o022)
    try:
        snaparchive.export_snapshot(store, manifest.id, out)
    finally:
        os.umask(old)
    assert asked == [0o600]
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_export_refuses_an_existing_file_constructed(
    tmp_path: Path, source: tuple[SnapshotStore, snapshot.Manifest]
) -> None:
    store, manifest = source
    there = tmp_path / "there.zip"
    there.write_bytes(b"constructed: the player's own file\n")
    with pytest.raises(SnapshotError, match="already exists"):
        snaparchive.export_snapshot(store, manifest.id, there)
    assert there.read_bytes() == b"constructed: the player's own file\n"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_export_never_writes_through_a_link_constructed(
    tmp_path: Path, source: tuple[SnapshotStore, snapshot.Manifest]
) -> None:
    store, manifest = source
    victim = tmp_path / "victim"
    link = tmp_path / "link.zip"
    link.symlink_to(victim)  # dangling: O_EXCL never follows it
    with pytest.raises(SnapshotError):
        snaparchive.export_snapshot(store, manifest.id, link)
    assert not victim.exists()


def test_export_refuses_a_file_inside_an_install_constructed(
    tmp_path: Path, source: tuple[SnapshotStore, snapshot.Manifest]
) -> None:
    store, manifest = source
    install = tmp_path / "Some Install"
    (install / "_flavor_" / "WTF").mkdir(parents=True)
    (install / ".build.info").write_bytes(b"constructed\n")
    for dest in (install / "x.zip", install / "_flavor_" / "WTF" / "x.zip"):
        with pytest.raises(guard.GuardError):
            snaparchive.export_snapshot(store, manifest.id, dest)
        assert not dest.exists()


def test_export_refuses_a_file_inside_the_store_constructed(
    source: tuple[SnapshotStore, snapshot.Manifest],
) -> None:
    store, manifest = source
    dest = store.path / "tmp" / "x.zip"
    with pytest.raises(SnapshotError, match="inside the snapshot store"):
        snaparchive.export_snapshot(store, manifest.id, dest)
    assert not dest.exists()


def test_export_of_a_damaged_object_leaves_no_file_constructed(
    tmp_path: Path, source: tuple[SnapshotStore, snapshot.Manifest]
) -> None:
    store, manifest = source
    obj = store.object_path(SHA_B)
    obj.chmod(0o644)
    obj.write_bytes(b"constructed: not zlib")
    dest = tmp_path / "damaged.zip"
    with pytest.raises(ObjectCorruptError):
        snaparchive.export_snapshot(store, manifest.id, dest)
    assert not dest.exists()


# ─── hostile archives ────────────────────────────────────────────────────────


TRAVERSAL = (
    "../escape",
    "../../home/x/.bashrc",
    "/etc/passwd",
    "objects/../manifest.json",
    f"objects/{SHA_A}/../../x",
    f"objects/{SHA_A[:2]}/{SHA_A[2:]}",
    "C:/Windows/x",
    "..\\..\\escape",
    "objects/",
)


@pytest.mark.parametrize("name", TRAVERSAL, ids=[f"constructed-{i}" for i in range(len(TRAVERSAL))])
def test_path_traversal_and_unknown_entries_are_refused_constructed(
    tmp_path: Path, archive: Path, name: str
) -> None:
    hostile = _rewrite(archive, lambda e: [*e, _entry(name, b"constructed: payload\n")])
    _refused(tmp_path, hostile, ArchiveError, "does not have")
    assert not (tmp_path / "escape").exists()


def test_a_link_entry_is_refused_constructed(tmp_path: Path, archive: Path) -> None:
    def as_link(
        entries: list[tuple[zipfile.ZipInfo, bytes]],
    ) -> list[tuple[zipfile.ZipInfo, bytes]]:
        out = []
        for info, data in entries:
            if info.filename == f"objects/{SHA_A}":
                out.append(_entry(info.filename, data, mode=stat.S_IFLNK | 0o777))
            else:
                out.append((info, data))
        return out

    _refused(tmp_path, _rewrite(archive, as_link), ArchiveError, "is a link")


def test_a_duplicate_manifest_entry_is_refused_constructed(tmp_path: Path, archive: Path) -> None:
    def twice(entries: list[tuple[zipfile.ZipInfo, bytes]]) -> list[tuple[zipfile.ZipInfo, bytes]]:
        manifest = next(e for e in entries if e[0].filename == "manifest.json")
        return [*entries, _entry("manifest.json", manifest[1])]

    _refused(tmp_path, _rewrite(archive, twice), ArchiveError, "twice")


def test_a_duplicate_object_entry_is_refused_constructed(tmp_path: Path, archive: Path) -> None:
    def twice(entries: list[tuple[zipfile.ZipInfo, bytes]]) -> list[tuple[zipfile.ZipInfo, bytes]]:
        # The second copy is the one a lax reader would take: different bytes.
        return [*entries, _entry(f"objects/{SHA_A}", b"constructed: a swapped copy\n")]

    _refused(tmp_path, _rewrite(archive, twice), ArchiveError, "twice")


def test_a_duplicate_id_already_in_the_store_is_refused_constructed(
    tmp_path: Path, source: tuple[SnapshotStore, snapshot.Manifest], archive: Path
) -> None:
    """The same id with other bytes (here, another label, which the id's
    fingerprint does not cover) is never overwritten."""
    _, manifest = source
    store = SnapshotStore(tmp_path / "store-b")
    snaparchive.import_snapshot(store, archive)
    store.set_label(manifest.id, "constructed: relabelled here")
    relabelled = store.show(manifest.id)
    other = _rewrite(archive, lambda e: e)  # the same archive, so the store must refuse
    before = _store_state(store)
    # The store's copy now differs from the archive's: a duplicate id.
    with pytest.raises(SnapshotExistsError):
        snaparchive.import_snapshot(store, other)
    assert _store_state(store) == before
    assert store.show(manifest.id) == relabelled


def test_a_hash_mismatch_adds_nothing_constructed(tmp_path: Path, archive: Path) -> None:
    """The object for B has its recorded size but other bytes. A sorts first
    and verifies, so the refusal must also drop A's staged copy."""
    assert SHA_A < SHA_B or SHA_B < SHA_A

    def swap(entries: list[tuple[zipfile.ZipInfo, bytes]]) -> list[tuple[zipfile.ZipInfo, bytes]]:
        out = []
        for info, data in entries:
            if info.filename.startswith("objects/") and data == B:
                data = b"X" + data[1:]  # constructed: same size, other hash
            out.append((info, data))
        return out

    store = _refused(tmp_path, _rewrite(archive, swap), ObjectCorruptError, "does not hash")
    for digest in (SHA_A, SHA_B):
        assert not store.object_path(digest).exists()


def test_an_object_entry_whose_size_differs_from_the_manifest_is_refused_constructed(
    tmp_path: Path, archive: Path
) -> None:
    def longer(entries: list[tuple[zipfile.ZipInfo, bytes]]) -> list[tuple[zipfile.ZipInfo, bytes]]:
        return [
            (info, data + b"constructed: more" if info.filename == f"objects/{SHA_A}" else data)
            for info, data in entries
        ]

    _refused(tmp_path, _rewrite(archive, longer), ArchiveError, "but its snapshot records")


def test_an_oversize_entry_is_refused_constructed(tmp_path: Path, archive: Path) -> None:
    """Over the per-object bound, the total bound, and the manifest bound
    (lowered here, so the test needs no gigabytes)."""
    _refused(tmp_path, archive, ArchiveError, max_object_bytes=len(B) - 1)
    _refused(tmp_path, archive, ArchiveError, max_total_bytes=len(A) + len(B) - 1)
    _refused(tmp_path, archive, ArchiveError, max_manifest_bytes=64)
    _refused(tmp_path, archive, ArchiveError, max_entries=2)


def test_a_stream_longer_than_its_header_is_cut_at_the_recorded_size_constructed(
    tmp_path: Path, source: tuple[SnapshotStore, snapshot.Manifest]
) -> None:
    """`add_snapshot` reads one byte past the recorded size and no more,
    whatever a zip header claims: an entry that inflates further (a bomb)
    is refused with nothing added."""
    _, manifest = source
    data = snapshot.manifest_bytes(manifest)
    read: list[int] = []
    closed: list[str] = []

    class Source:
        """Constructed: A as recorded; B far longer than its recorded size."""

        def __init__(self, digest: str) -> None:
            self._digest = digest
            self._io = io.BytesIO(A if digest == SHA_A else b"\0" * (len(B) * 64))

        def read(self, size: int, /) -> bytes:
            chunk = self._io.read(size)
            read.append(len(chunk))
            return chunk

        def close(self) -> None:
            closed.append(self._digest)

    store = SnapshotStore(tmp_path / "store-b")
    with pytest.raises(ObjectCorruptError, match="more than its recorded"):
        store.add_snapshot(data, Source)
    assert sum(read) <= len(A) + len(B) + 1, "never more than one byte past the record"
    assert sorted(closed) == sorted({SHA_A, SHA_B})
    assert not store.manifests_dir.exists() or not any(store.manifests_dir.iterdir())
    assert not any((store.path / "tmp").iterdir())
    for digest in (SHA_A, SHA_B):
        assert not store.object_path(digest).exists()


def test_missing_and_extra_objects_are_refused_constructed(tmp_path: Path, archive: Path) -> None:
    drop = _rewrite(archive, lambda e: [x for x in e if x[0].filename != f"objects/{SHA_A}"])
    _refused(tmp_path, drop, ArchiveError)
    extra_data = b"constructed: named by nothing\n"
    extra = hashlib.sha256(extra_data).hexdigest()
    added = _rewrite(archive, lambda e: [*e, _entry(f"objects/{extra}", extra_data)])
    _refused(tmp_path, added, ArchiveError)


def _replace_manifest(archive: Path, data: bytes) -> Path:
    return _rewrite(
        archive,
        lambda e: [(info, data if info.filename == "manifest.json" else d) for info, d in e],
    )


def test_a_tampered_manifest_is_refused_constructed(
    tmp_path: Path, source: tuple[SnapshotStore, snapshot.Manifest], archive: Path
) -> None:
    _, manifest = source
    # An entry path changed to climb out of the root: the model refuses it.
    climbing = snapshot.manifest_bytes(manifest).replace(b"WTF/Config.wtf", b"../../Config.wtf")
    _refused(tmp_path, _replace_manifest(archive, climbing), ManifestIntegrityError)
    # An entry renamed in place: the id's fingerprint no longer matches.
    renamed = snapshot.manifest_bytes(manifest).replace(b"WTF/Config.wtf", b"WTF/Config.wtx")
    _refused(tmp_path, _replace_manifest(archive, renamed), ManifestIntegrityError)
    # Not the canonical encoding (the store writes only that one).
    pretty = manifest.model_dump_json(indent=2).encode()
    _refused(tmp_path, _replace_manifest(archive, pretty), ManifestIntegrityError)
    # An id that is no snapshot id (it would name the manifest file).
    bad_id = snapshot.manifest_bytes(manifest).replace(manifest.id.encode(), b"../../x")
    _refused(tmp_path, _replace_manifest(archive, bad_id), ManifestIntegrityError)
    _refused(tmp_path, _replace_manifest(archive, b"[" * 100000), ManifestIntegrityError)


def test_a_file_that_is_not_an_archive_is_refused_constructed(
    tmp_path: Path, archive: Path
) -> None:
    plain = tmp_path / "plain.zip"
    plain.write_bytes(b"constructed: not a zip\n")
    _refused(tmp_path, plain, ArchiveError)
    foreign = _rewrite(archive, lambda e: e, comment=b"")
    _refused(tmp_path, foreign, ArchiveError)
    _refused(tmp_path, tmp_path / "missing.zip", ArchiveError)


def test_a_corrupted_deflate_stream_is_refused_constructed(tmp_path: Path, archive: Path) -> None:
    raw = bytearray(archive.read_bytes())
    with zipfile.ZipFile(archive) as zf:
        info = zf.getinfo(f"objects/{SHA_B}")
    # Flip a byte in the middle of the entry's compressed data.
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    raw[start + info.compress_size // 2] ^= 0xFF
    broken = tmp_path / "broken.zip"
    broken.write_bytes(bytes(raw))
    _refused(tmp_path, broken, ObjectCorruptError, "does not read back")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX")
def test_a_fifo_or_link_named_as_the_archive_is_refused_constructed(
    tmp_path: Path, archive: Path
) -> None:
    fifo = tmp_path / "fifo.zip"
    os.mkfifo(fifo)
    _refused(tmp_path, fifo, ArchiveError)  # never blocks
    link = tmp_path / "link.zip"
    link.symlink_to(archive)
    _refused(tmp_path, link, ArchiveError)


# ─── what an imported manifest may claim (review round 1) ────────────────────


def _forge(manifest: snapshot.Manifest, stamp: str | None = None, **update: object) -> bytes:
    """Constructed: `manifest` with `update` applied and its id remade (with
    `stamp` as its time part, when given), so the fingerprint holds: what an
    attacker who writes the archive can do."""
    forged = manifest.model_copy(update=update)
    fingerprint = snapshot.tree_fingerprint(forged.subtrees, forged.excluded, forged.entries)
    when = stamp if stamp is not None else manifest.id.rsplit("-", 1)[0]
    forged = forged.model_copy(update={"id": f"{when}-{fingerprint}"})
    return snapshot.manifest_bytes(forged)


def _forged_archive(
    archive: Path, manifest: snapshot.Manifest, when: datetime | None = None, **update: object
) -> Path:
    stamp = None
    if when is not None:
        update["created_at"] = f"{when:%Y-%m-%dT%H:%M:%S}.{when.microsecond:06d}Z"
        stamp = f"{when:%Y%m%dT%H%M%S}.{when.microsecond:06d}Z"
    return _replace_manifest(archive, _forge(manifest, stamp, **update))


def test_a_snapshot_dated_in_the_future_is_refused_constructed(
    tmp_path: Path, source: tuple[SnapshotStore, snapshot.Manifest], archive: Path
) -> None:
    _, manifest = source
    future = _forged_archive(archive, manifest, datetime(2099, 1, 1, tzinfo=UTC))
    _refused(tmp_path, future, ManifestIntegrityError, "from the future")
    # Within the allowance for two computers' clocks: accepted.
    soon = datetime.now(UTC) + snapshot.IMPORT_CLOCK_ALLOWANCE / 2
    store = SnapshotStore(tmp_path / "store-c")
    assert snaparchive.import_snapshot(store, _forged_archive(archive, manifest, soon)).added


def test_a_date_that_is_not_the_ids_is_refused_constructed(
    tmp_path: Path, source: tuple[SnapshotStore, snapshot.Manifest], archive: Path
) -> None:
    """`created_at` is outside the fingerprint, so it can be changed without
    remaking the id; it must still be the moment the id carries."""
    _, manifest = source
    other = manifest.model_copy(update={"created_at": "2000-01-01T00:00:00.000000Z"})
    _refused(
        tmp_path,
        _replace_manifest(archive, snapshot.manifest_bytes(other)),
        ManifestIntegrityError,
        "not the moment its id carries",
    )
    bad = manifest.model_copy(update={"created_at": "yesterday"})
    _refused(
        tmp_path,
        _replace_manifest(archive, snapshot.manifest_bytes(bad)),
        ManifestIntegrityError,
        "malformed",
    )


def test_an_imported_user_snapshot_is_never_latest_user_constructed(
    tmp_path: Path, source: tuple[SnapshotStore, snapshot.Manifest], archive: Path
) -> None:
    """The archive's manifest says `kind: user` and is newer than anything the
    receiving player took; `latest-user` still names the player's own, and
    `latest` names the newest by date, which is the imported one (its date is
    real: no later than now)."""
    _, manifest = source
    assert manifest.kind == "user"
    store = SnapshotStore(tmp_path / "store-b")
    own = store.create(
        _other_tree(tmp_path), ["x"], now=datetime(2020, 1, 1, tzinfo=UTC), label="mine"
    )
    snaparchive.import_snapshot(store, archive)
    assert store.imported_ids() == {manifest.id}
    assert snapshot.resolve_reference(store, "latest-user") == own.id
    assert snapshot.resolve_reference(store, "latest") == manifest.id
    assert snapshot.resolve_reference(store, "~1") == own.id
    imported = store.imported_ids()
    assert snapshot.kind_of(store.show(manifest.id), (), imported) == "imported"
    assert snapshot.kind_of(store.show(own.id), (), imported) == "user"


def test_the_import_record_is_written_before_the_manifest_constructed(
    tmp_path: Path, archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure writing the manifest leaves at most a record and no
    manifest: the store never holds an imported manifest unmarked."""
    store = SnapshotStore(tmp_path / "store-b")

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("constructed: the disk filled")

    monkeypatch.setattr(store, "_write_manifest", fail)
    with pytest.raises(SnapshotError):
        snaparchive.import_snapshot(store, archive)
    assert store.list() == ()
    assert len(store.imported_ids()) == 1


BAD_PATHS: tuple[tuple[str, object], ...] = (
    ("subtrees", ("../../../etc",)),
    ("subtrees", (f"{FOLDER}/../../etc",)),
    ("subtrees", ("Data",)),
    ("subtrees", (".",)),
    ("subtrees", ()),
    ("excluded", ("../../../etc",)),
    ("excluded", ("elsewhere/x",)),
    ("flavor_folder", None),
    ("flavor_folder", ".."),
    ("flavor_folder", "a/b"),
)


@pytest.mark.parametrize(
    ("field", "value"), BAD_PATHS, ids=[f"constructed-{i}" for i in range(len(BAD_PATHS))]
)
def test_subtrees_and_excluded_must_stay_in_the_flavor_folder_constructed(
    tmp_path: Path,
    source: tuple[SnapshotStore, snapshot.Manifest],
    archive: Path,
    field: str,
    value: object,
) -> None:
    _, manifest = source
    forged = _forged_archive(archive, manifest, **{field: value})
    _refused(tmp_path, forged, ManifestIntegrityError)


def test_an_entry_outside_what_was_captured_is_refused_constructed(
    tmp_path: Path, source: tuple[SnapshotStore, snapshot.Manifest], archive: Path
) -> None:
    _, manifest = source
    moved = tuple(
        e.model_copy(update={"path": ".build.info"}) if e.path.endswith("Config.wtf") else e
        for e in manifest.entries
    )
    forged = _forged_archive(archive, manifest, entries=tuple(sorted(moved, key=lambda e: e.path)))
    _refused(tmp_path, forged, ManifestIntegrityError, "outside what it captured")


# ─── the end record, read before zipfile (review round 1) ────────────────────


class _Spy:
    """Counts how often `zipfile.ZipFile` is built while it is installed."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls = 0
        real = zipfile.ZipFile

        def spy(*args: Any, **kwargs: Any) -> zipfile.ZipFile:
            self.calls += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(snaparchive.zipfile, "ZipFile", spy)


def test_an_over_count_archive_is_refused_before_zipfile_reads_it_constructed(
    tmp_path: Path, archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _Spy(monkeypatch)
    _refused(tmp_path, archive, ArchiveError, "more than 2", max_entries=2)
    _refused(tmp_path, archive, ArchiveError, "directory", max_directory_bytes=16)
    assert spy.calls == 0


def test_a_zip64_over_count_archive_is_refused_before_zipfile_reads_it_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Over 65535 entries the count lives in the Zip64 record only."""
    big = tmp_path / "zip64.zip"
    with zipfile.ZipFile(big, "w", zipfile.ZIP_STORED) as zf:
        zf.comment = ARCHIVE_COMMENT
        for i in range(0x10000):
            zf.writestr(f"objects/{i:064x}", b"")
    tail = big.read_bytes()[-200:]
    assert b"PK\x06\x06" in tail and b"PK\x06\x07" in tail, "constructed: a Zip64 archive"
    spy = _Spy(monkeypatch)
    _refused(tmp_path, big, ArchiveError, f"holds {0x10000} entries", max_entries=0xFFFF)
    assert spy.calls == 0


def test_bytes_after_the_comment_are_refused_constructed(tmp_path: Path, archive: Path) -> None:
    padded = tmp_path / "padded.zip"
    padded.write_bytes(archive.read_bytes() + b"constructed: trailing")
    _refused(tmp_path, padded, ArchiveError, "not a snapshot archive")


def _decoy(tmp_path: Path, entries: int, *, locator_points_home: bool) -> Path:
    """Constructed (review round 2): a zip of `entries` tiny entries whose
    classic end record claims one entry and a 46-byte directory, with no
    Zip64 marker, while a Zip64 locator and record just before it carry the
    true count and size, which `zipfile` follows. With `locator_points_home`
    false, eight junk bytes sit between the directory and the Zip64 record
    and the locator names the junk, so `zipfile` falls back to the record
    just before the locator (it then reads the directory eight bytes late,
    so only the first form also parses)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for i in range(entries):
            zf.writestr(f"objects/{i:064x}", b"")
    raw = buf.getvalue()
    eocd = struct.Struct("<4s4H2LH")
    _, _, _, _, count, cd_size, cd_offset, _ = eocd.unpack(raw[-eocd.size :])
    body = raw[: cd_offset + cd_size]
    zip64 = struct.pack(
        "<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, cd_size, cd_offset
    )
    junk = b"" if locator_points_home else b"JUNKJUNK"
    pointed = len(body)
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, pointed, 1)
    end = eocd.pack(b"PK\x05\x06", 0, 0, 1, 1, 46, cd_offset, len(ARCHIVE_COMMENT))
    path = tmp_path / f"decoy-{locator_points_home}.zip"
    path.write_bytes(body + junk + zip64 + locator + end + ARCHIVE_COMMENT)
    return path


@pytest.mark.parametrize("home", [True, False], ids=["constructed-at-offset", "constructed-before"])
def test_a_zip64_decoy_behind_an_unmarked_end_record_is_refused_before_zipfile_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: bool
) -> None:
    path = _decoy(tmp_path, 300, locator_points_home=home)
    if home:
        with zipfile.ZipFile(path) as zf:  # the decoy works on zipfile as described
            assert len(zf.infolist()) == 300
    spy = _Spy(monkeypatch)
    _refused(tmp_path, path, ArchiveError, "300 entries, more than 100", max_entries=100)
    _refused(tmp_path, path, ArchiveError, "directory", max_directory_bytes=1000)
    assert spy.calls == 0


def test_the_directory_bound_is_sized_to_the_entry_cap_constructed() -> None:
    """The directory size is the bound that holds (zipfile reads entries
    until it runs out): it has room for the entry cap as an export writes
    entries, and no more than a few times that in the shortest entries."""
    longest = 46 + len("objects/") + 64 + 28
    assert snaparchive.MAX_ENTRIES * longest == snaparchive.MAX_DIRECTORY_BYTES
    assert snaparchive.MAX_DIRECTORY_BYTES // 46 < 4 * snaparchive.MAX_ENTRIES


@pytest.mark.parametrize("claimed", ["user", "pre-write"], ids=lambda c: f"constructed-{c}")
def test_explains_baseline_reports_an_imported_snapshot_as_imported_constructed(
    tmp_path: Path, archive: Path, source: tuple[SnapshotStore, snapshot.Manifest], claimed: str
) -> None:
    """`explain`'s snapshot facts (review round 2): an imported snapshot of
    this very install, past-dated and newer than the player's own, claiming
    `user` or `pre-write`, is reported as `imported`, never as its claim."""
    _, manifest = source
    root = tmp_path / "source"  # the constructed tree `source` captured
    store = SnapshotStore(tmp_path / "store-b")
    own = store.create(
        root,
        [f"{FOLDER}/WTF"],
        flavor_folder=FOLDER,
        now=datetime(2020, 1, 1, tzinfo=UTC),
    )
    when = datetime(2021, 1, 1, tzinfo=UTC)  # in the past, and newer than `own`
    forged = _forged_archive(
        archive, manifest, when, kind=claimed, install_root=str(root.absolute())
    )
    report = snaparchive.import_snapshot(store, forged)
    facts = pathfacts.snapshot_facts(store, root, FOLDER, "WTF/Config.wtf", ["WTF"])
    assert facts.snapshot_id == report.id != own.id
    assert facts.kind == "imported"
    assert facts.newer_pre_write is None


@pytest.mark.parametrize(
    ("disk", "cd_disk"), [(1, 0), (0, 1), (0x4B50, 0x0605)], ids=lambda v: f"constructed-{v}"
)
def test_an_end_record_with_nonzero_disk_fields_is_refused_constructed(
    tmp_path: Path, archive: Path, monkeypatch: pytest.MonkeyPatch, disk: int, cd_disk: int
) -> None:
    """Review round 3: an export is one disk, so both disk fields are 0. The
    last pair spells a second end signature inside the record, where
    `zipfile`'s `rfind` would find a different record; it is refused before
    `zipfile` reads anything."""
    raw = bytearray(archive.read_bytes())
    at = len(raw) - 22 - len(ARCHIVE_COMMENT)
    raw[at + 4 : at + 8] = struct.pack("<2H", disk, cd_disk)
    patched = tmp_path / "disks.zip"
    patched.write_bytes(bytes(raw))
    spy = _Spy(monkeypatch)
    _refused(tmp_path, patched, ArchiveError, "end record")
    assert spy.calls == 0
