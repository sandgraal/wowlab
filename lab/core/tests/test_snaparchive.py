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
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from wowlab_core import guard, snaparchive, snapshot
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


def _tree(tmp_path: Path) -> Path:
    """Constructed: a small tree with two contents, one stored twice."""
    root = tmp_path / "source"
    (root / "WTF" / "Account").mkdir(parents=True)
    (root / "WTF" / "Config.wtf").write_bytes(A)
    (root / "WTF" / "Account" / "copy.wtf").write_bytes(A)
    (root / "WTF" / "Account" / "data.lua").write_bytes(B)
    return root


@pytest.fixture
def source(tmp_path: Path) -> tuple[SnapshotStore, snapshot.Manifest]:
    store = SnapshotStore(tmp_path / "store-a")
    manifest = store.create(_tree(tmp_path), ["WTF"], label="constructed")
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
    assert store.read_file(manifest.id, "WTF/Account/data.lua") == B
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
