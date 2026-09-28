"""Snapshot store hardening (M11-15, follow-ups from the #107 security review).

Every input here is constructed: the store is our own format, and these are
the hostile and boundary cases L8 allows to be built by hand. Nothing touches
a real install or the real user data directory; the guard cases use the
M10-11T synthetic install, store, redirected user data directory and injected
process table from `test_guard.py` (loaded, not copied).

(a) `gc` lists `objects/` by `lstat` and never deletes through a link: a
    shard that is a symbolic link (on Windows, a junction) to a directory
    outside the store, or one swapped for such a link between the listing and
    the delete, leaves the file behind it in place.
(b) `read_object(sha256, size=...)` inflates at most `size + 1` bytes and
    refuses an object that would inflate further, counted by wrapping the
    inflater the module uses (bytes produced, not memory measured); guard's
    restore, undo and rollback pass the entry's size.
(c) `create`'s reuse check freshens the object's mtime through the checked
    descriptor, so a link swapped in at the object path after the re-hash is
    never followed: the file behind it is not touched.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import stat
import sys
import types
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import snapshot
from wowlab_core.snapshot import ObjectCorruptError, SnapshotStore

T0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)
CONFIG_BYTES = b'SET portal "US"\n'
VICTIM = b"outside the store: gc must never delete this\n"
BOMB_MIB = 64
"""What the over-inflating object inflates to, in MiB: far past any bound."""

posix_symlinks = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "symlink"),
    reason="POSIX symbolic links (creating them needs privileges on Windows)",
)
windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="NTFS junctions exist only on Windows"
)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "Install"
    (root / "WTF").mkdir(parents=True)
    (root / "WTF" / "Config.wtf").write_bytes(CONFIG_BYTES)
    (root / "WTF" / "other.wtf").write_bytes(b"other\n")
    return root


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path / "userdata" / "store")


def _config_object(store: SnapshotStore) -> Path:
    return store.object_path(hashlib.sha256(CONFIG_BYTES).hexdigest())


def _unused_shard(store: SnapshotStore) -> str:
    used = {p.name for p in store.objects_dir.iterdir()}
    return next(f"{i:02x}" for i in range(256) if f"{i:02x}" not in used)


def _victim_dir(tmp_path: Path) -> tuple[Path, Path]:
    """A directory outside the store holding one file named like an object
    item (62 hex digits) with a well-formed zlib body: exactly what gc would
    collect if it listed into a link to this directory."""
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / ("0" * 62)
    victim.write_bytes(zlib.compress(VICTIM))
    return outside, victim


# ─── (a) gc never deletes through a link ─────────────────────────────────────


def _assert_victim_survives_gc(
    store: SnapshotStore, shard: str, victim: Path, before: bytes
) -> None:
    dry = store.gc(dry_run=True)
    assert shard + victim.name not in dry.unreferenced, "a linked shard is never listed into"
    assert f"objects/{shard}" in dry.skipped, f"the linked shard is reported: {dry.skipped!r}"
    real = store.gc(dry_run=False)
    assert victim.read_bytes() == before, "the file behind the link is untouched"
    assert shard + victim.name not in real.removed
    assert f"objects/{shard}" in real.skipped
    assert store.verify().missing_objects == (), "every referenced object is still stored"


@posix_symlinks
def test_constructed_gc_never_deletes_through_a_symlinked_shard(
    source: Path, store: SnapshotStore, tmp_path: Path
) -> None:
    store.create(source, ["WTF"], now=T0)
    outside, victim = _victim_dir(tmp_path)
    shard = _unused_shard(store)
    (store.objects_dir / shard).symlink_to(outside, target_is_directory=True)
    _assert_victim_survives_gc(store, shard, victim, victim.read_bytes())
    assert (store.objects_dir / shard).is_symlink(), "the link itself is left as it is"


@windows_only
def test_constructed_gc_never_deletes_through_a_junction_shard(
    source: Path, store: SnapshotStore, tmp_path: Path
) -> None:
    import _winapi  # Windows only; skipped elsewhere

    store.create(source, ["WTF"], now=T0)
    outside, victim = _victim_dir(tmp_path)
    shard = _unused_shard(store)
    _winapi.CreateJunction(str(outside), str(store.objects_dir / shard))
    _assert_victim_survives_gc(store, shard, victim, victim.read_bytes())


@posix_symlinks
def test_constructed_gc_skips_a_linked_object_item_and_keeps_its_target(
    source: Path, store: SnapshotStore, tmp_path: Path
) -> None:
    store.create(source, ["WTF"], now=T0)
    _outside, victim = _victim_dir(tmp_path)
    shard = _unused_shard(store)
    (store.objects_dir / shard).mkdir()
    item = store.objects_dir / shard / victim.name
    item.symlink_to(victim)
    report = store.gc(dry_run=False)
    assert shard + victim.name not in report.unreferenced
    assert f"objects/{shard}/{victim.name}" in report.skipped
    assert item.is_symlink() and victim.read_bytes() == zlib.compress(VICTIM)


@posix_symlinks
def test_constructed_gc_shard_swapped_for_a_link_after_the_listing_is_not_deleted_through(
    source: Path, store: SnapshotStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race: the listing sees a real shard holding an unreferenced object;
    before the delete, the shard is moved away and a link to a directory
    holding a file of the same name takes its place. Nothing is deleted
    through the link; the object is reported as skipped."""
    store.create(source, ["WTF"], now=T0)
    outside, victim = _victim_dir(tmp_path)
    shard = _unused_shard(store)
    (store.objects_dir / shard).mkdir()
    orphan = store.objects_dir / shard / victim.name
    orphan.write_bytes(zlib.compress(b"unreferenced\n"))
    moved = tmp_path / "moved-shard"
    real_listing = SnapshotStore._object_files

    def listing_then_swap(self: SnapshotStore) -> Any:
        listed = list(real_listing(self))
        (self.objects_dir / shard).rename(moved)
        (self.objects_dir / shard).symlink_to(outside, target_is_directory=True)
        return iter(listed)

    monkeypatch.setattr(SnapshotStore, "_object_files", listing_then_swap)
    report = store.gc(dry_run=False)
    assert shard + victim.name in report.unreferenced, "the listing saw the real object"
    assert report.removed == ()
    assert f"objects/{shard}/{victim.name}" in report.skipped
    assert victim.read_bytes() == zlib.compress(VICTIM), "nothing deleted through the link"
    assert (moved / victim.name).exists(), "the moved object was not deleted either"


def test_constructed_gc_still_removes_an_unreferenced_regular_object(
    source: Path, store: SnapshotStore
) -> None:
    store.create(source, ["WTF"], now=T0)
    shard = _unused_shard(store)
    (store.objects_dir / shard).mkdir()
    orphan = store.objects_dir / shard / ("1" * 62)
    orphan.write_bytes(zlib.compress(b"unreferenced\n"))
    report = store.gc(dry_run=False)
    assert report.removed == (shard + "1" * 62,) and report.skipped == ()
    assert not orphan.exists() and not (store.objects_dir / shard).exists()


# ─── (a, fix round 1) gc refuses a manifests/ it cannot trust ────────────────


def _object_listing(store: SnapshotStore) -> list[str]:
    return sorted(p.relative_to(store.objects_dir).as_posix() for p in store.objects_dir.rglob("*"))


def _break_manifests(store: SnapshotStore, how: str, tmp_path: Path) -> None:
    elsewhere = tmp_path / "manifests-elsewhere"
    if how == "missing":
        store.manifests_dir.rename(elsewhere)
    elif how == "not-a-directory":
        store.manifests_dir.rename(elsewhere)
        store.manifests_dir.write_bytes(b"")
    elif how == "dangling-link":  # an unmounted volume, say
        store.manifests_dir.rename(elsewhere)
        store.manifests_dir.symlink_to(tmp_path / "unmounted", target_is_directory=True)
    elif how == "linked-directory":  # a link to a real, sound manifests directory
        store.manifests_dir.rename(elsewhere)
        store.manifests_dir.symlink_to(elsewhere, target_is_directory=True)
    elif how == "junction":
        import _winapi  # Windows only; skipped elsewhere

        store.manifests_dir.rename(elsewhere)
        _winapi.CreateJunction(str(elsewhere), str(store.manifests_dir))
    else:
        raise AssertionError(how)


BROKEN_MANIFESTS = [
    pytest.param("missing", id="constructed-missing"),
    pytest.param("not-a-directory", id="constructed-not-a-directory"),
    pytest.param("dangling-link", id="constructed-dangling-link", marks=posix_symlinks),
    pytest.param("linked-directory", id="constructed-linked-directory", marks=posix_symlinks),
    pytest.param("junction", id="constructed-junction", marks=windows_only),
]


@pytest.mark.parametrize("how", BROKEN_MANIFESTS)
def test_constructed_gc_refuses_when_manifests_cannot_be_trusted(
    how: str, source: Path, store: SnapshotStore, tmp_path: Path
) -> None:
    """With `manifests/` gone, a link, or not a directory, every object would
    look unreferenced (guard's pre-write snapshot objects included): gc
    refuses, as it does for a manifest that does not load, and deletes
    nothing, dry run or not."""
    store.create(source, ["WTF"], now=T0)
    before = _object_listing(store)
    assert before
    _break_manifests(store, how, tmp_path)
    for dry_run in (True, False):
        with pytest.raises(snapshot.ManifestIntegrityError, match="gc"):
            store.gc(dry_run=dry_run)
    assert _object_listing(store) == before, "nothing was deleted"


def test_constructed_gc_on_a_store_without_manifests_or_objects_is_a_no_op(
    store: SnapshotStore,
) -> None:
    store.path.mkdir(parents=True)
    report = store.gc(dry_run=False)
    assert report.unreferenced == () and report.removed == ()


def test_constructed_snap_gc_exits_non_zero_when_manifests_is_missing(
    source: Path, tmp_path: Path
) -> None:
    from typer.testing import CliRunner

    from wowlab_core import cli

    default = SnapshotStore(snapshot.default_store_path())
    assert tmp_path in default.path.parents, "the user data directory is redirected"
    default.create(source, ["WTF"], now=T0)
    before = _object_listing(default)
    default.manifests_dir.rename(tmp_path / "manifests-elsewhere")
    result = CliRunner().invoke(cli.app, ["snap", "gc", "--yes"])
    assert result.exit_code == 1, (result.stdout, result.stderr)
    assert "missing but the store holds objects" in result.stderr
    assert _object_listing(default) == before


# ─── (a, fix round 1) the delete without directory descriptors ───────────────


@pytest.fixture
def without_dir_fds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the path Windows takes (no `dir_fd`) on any platform."""
    monkeypatch.setattr(snapshot, "_UNLINK_BY_DIR_FD", False)


def _parked(store: SnapshotStore) -> list[str]:
    return sorted(p.name for p in store.tmp_dir.glob("gc-*")) if store.tmp_dir.is_dir() else []


def test_constructed_rename_delete_still_removes_an_object_and_its_empty_shard(
    source: Path, store: SnapshotStore, without_dir_fds: None
) -> None:
    store.create(source, ["WTF"], now=T0)
    shard = _unused_shard(store)
    (store.objects_dir / shard).mkdir()
    orphan = store.objects_dir / shard / ("1" * 62)
    orphan.write_bytes(zlib.compress(b"unreferenced\n"))
    report = store.gc(dry_run=False)
    assert report.removed == (shard + "1" * 62,) and report.skipped == ()
    assert not orphan.exists() and not (store.objects_dir / shard).exists()
    assert _parked(store) == [], "nothing is left in tmp/"
    assert store.verify().ok


@posix_symlinks
def test_constructed_rename_delete_shard_swapped_after_the_checks_is_not_deleted_through(
    source: Path,
    store: SnapshotStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    without_dir_fds: None,
) -> None:
    """The Windows window: every `lstat` check passes, and only then is the
    shard swapped for a link to a directory holding a file of the object's
    name. The rename into `tmp/` moves that file; it fails the identity check
    and goes straight back. Nothing is deleted."""
    store.create(source, ["WTF"], now=T0)
    outside, victim = _victim_dir(tmp_path)
    shard = _unused_shard(store)
    (store.objects_dir / shard).mkdir()
    orphan = store.objects_dir / shard / victim.name
    orphan.write_bytes(zlib.compress(b"unreferenced\n"))
    moved = tmp_path / "moved-shard"
    real_still_names = SnapshotStore._still_names
    swapped: list[Path] = []

    def check_then_swap(path: Path, st: os.stat_result) -> bool:
        answer = real_still_names(path, st)
        if answer and path == orphan and not swapped:
            swapped.append(path)
            (store.objects_dir / shard).rename(moved)
            (store.objects_dir / shard).symlink_to(outside, target_is_directory=True)
        return answer

    monkeypatch.setattr(SnapshotStore, "_still_names", staticmethod(check_then_swap))
    report = store.gc(dry_run=False)
    assert swapped, "the swap happened after the checks"
    assert report.removed == ()
    assert f"objects/{shard}/{victim.name}" in report.skipped
    assert victim.read_bytes() == zlib.compress(VICTIM), "the file behind the link is back"
    assert (moved / victim.name).exists(), "the listed object was not deleted either"
    assert _parked(store) == [], "nothing is left in tmp/"


@posix_symlinks
def test_constructed_rename_delete_shard_swapped_before_its_rmdir_is_put_back(
    source: Path,
    store: SnapshotStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    without_dir_fds: None,
) -> None:
    """The empty-shard removal takes the same care: a shard swapped for a link
    to an empty directory outside the store after its check is renamed into
    `tmp/`, found to be a link, and renamed back; the directory survives."""
    store.create(source, ["WTF"], now=T0)
    shard = _unused_shard(store)
    shard_path = store.objects_dir / shard
    shard_path.mkdir()
    (shard_path / ("1" * 62)).write_bytes(zlib.compress(b"unreferenced\n"))
    outside = tmp_path / "outside-empty"
    outside.mkdir()
    real_same = SnapshotStore._same_dir_entry
    shard_checks: list[int] = []

    def check_then_swap(path: Path, expect: os.stat_result) -> bool:
        answer = real_same(path, expect)
        if path == shard_path:
            shard_checks.append(1)
            if len(shard_checks) == 2 and answer:  # the check before the rmdir
                shard_path.rmdir()
                shard_path.symlink_to(outside, target_is_directory=True)
        return answer

    monkeypatch.setattr(SnapshotStore, "_same_dir_entry", staticmethod(check_then_swap))
    report = store.gc(dry_run=False)
    assert report.removed == (shard + "1" * 62,)
    assert len(shard_checks) == 2
    assert outside.is_dir() and shard_path.is_symlink(), "the link was put back, not removed"
    assert _parked(store) == [], "nothing is left in tmp/"


# ─── (b) read_object inflates no further than size + 1 ───────────────────────


class _CountingInflater:
    def __init__(self, real: Any, produced: list[int]) -> None:
        self._real = real
        self._produced = produced

    def decompress(self, data: bytes, max_length: int = 0) -> bytes:
        out: bytes = self._real.decompress(data, max_length)
        self._produced.append(len(out))
        return out

    def flush(self, *args: Any) -> bytes:
        out: bytes = self._real.flush(*args)
        self._produced.append(len(out))
        return out

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _count_inflation(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Every byte any inflater in `wowlab_core.snapshot` produces, from now on."""
    produced: list[int] = []
    proxy = types.SimpleNamespace(
        **{name: getattr(zlib, name) for name in dir(zlib) if not name.startswith("__")}
    )
    proxy.decompressobj = lambda *a, **k: _CountingInflater(zlib.decompressobj(*a, **k), produced)
    monkeypatch.setattr(snapshot, "zlib", proxy)
    return produced


def _bomb(mib: int = BOMB_MIB) -> bytes:
    """A small zlib stream (about 1 KiB per MiB) that inflates to `mib` MiB of zeros."""
    packer = zlib.compressobj(9)
    block = b"\0" * (1 << 20)
    return b"".join([*(packer.compress(block) for _ in range(mib)), packer.flush()])


def test_constructed_over_inflating_object_is_refused_at_size_plus_one(
    source: Path, store: SnapshotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = store.create(source, ["WTF"], now=T0)
    entry = manifest.entry("WTF/Config.wtf")
    assert entry is not None and entry.sha256 is not None
    bomb = _bomb()
    assert len(bomb) < 256 << 10, "the object on disk is small"
    _config_object(store).write_bytes(bomb)

    produced = _count_inflation(monkeypatch)
    with pytest.raises(ObjectCorruptError, match="inflates past its recorded size"):
        store.read_object(entry.sha256, size=entry.size)
    assert sum(produced) <= entry.size + 1, f"inflated {sum(produced)} bytes"

    produced.clear()
    with pytest.raises(ObjectCorruptError, match="inflates past its recorded size"):
        store.read_file(manifest.id, "WTF/Config.wtf")
    assert sum(produced) <= entry.size + 1, f"read_file inflated {sum(produced)} bytes"


def test_constructed_size_bound_is_inclusive_and_one_under_is_refused(
    source: Path, store: SnapshotStore
) -> None:
    store.create(source, ["WTF"], now=T0)
    digest = hashlib.sha256(CONFIG_BYTES).hexdigest()
    assert store.read_object(digest, size=len(CONFIG_BYTES)) == CONFIG_BYTES
    assert store.read_object(digest) == CONFIG_BYTES, "no size: unbounded, as before"
    with pytest.raises(ObjectCorruptError, match="inflates past"):
        store.read_object(digest, size=len(CONFIG_BYTES) - 1)
    with pytest.raises(ObjectCorruptError, match="negative"):
        store.read_object(digest, size=-1)


# ─── (b) guard passes the recorded size ──────────────────────────────────────


def _load_m10_11t_graders() -> types.ModuleType:
    """`test_guard.py` as a module of its own, for its synthetic install,
    fixtures and helpers (as `test_guard_nonblocking_reads.py` loads it)."""
    path = Path(__file__).resolve().parent / "test_guard.py"
    spec = importlib.util.spec_from_file_location("_m11_15_base_graders", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


G = _load_m10_11t_graders()
_user_data_redirected = G._user_data_redirected
_real_process_table_is_never_listed = G._real_process_table_is_never_listed
guard = G.guard
world = G.world
install_root = G.install_root
flavor = G.flavor
idle = G.idle
guard_store = G.store  # the M10-11T store, beside the install; `store` above is this file's

CONFIG = G.CONFIG
ORIGINAL = G.ALLOWLISTED_FILES[G.CONFIG]
NEW_CONFIG = G.NEW_CONFIG


class _BodyFailedError(Exception):
    """Raised by a transaction body to make guard roll back."""


def _commit_a_change(guard: Any, flavor: Any, store: SnapshotStore) -> str:
    with guard.transaction(flavor, label="change", store=store.path) as tx:
        tx.write(CONFIG, NEW_CONFIG)
    snapshot_id: str = G.record_for(guard, store, "change").snapshot_id
    return snapshot_id


def _restore(guard: Any, flavor: Any, store: SnapshotStore) -> None:
    snap = _commit_a_change(guard, flavor, store)
    with guard.transaction(flavor, label="restore", store=store.path) as tx:
        tx.restore(snap, paths=[CONFIG])


def _undo(guard: Any, flavor: Any, store: SnapshotStore) -> None:
    _commit_a_change(guard, flavor, store)
    guard.undo(store=store.path)


def _rollback(guard: Any, flavor: Any, store: SnapshotStore) -> None:
    with (
        pytest.raises(_BodyFailedError),
        guard.transaction(flavor, label="rolled back", store=store.path) as tx,
    ):
        tx.write(CONFIG, NEW_CONFIG)
        raise _BodyFailedError


GUARD_READS = {"restore": _restore, "undo": _undo, "rollback": _rollback}


@pytest.mark.parametrize("op", [pytest.param(k, id=f"constructed-{k}") for k in GUARD_READS])
def test_constructed_guard_passes_the_recorded_size_to_every_object_read(
    op: str,
    guard: Any,
    flavor: Any,
    guard_store: SnapshotStore,
    idle: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int | None]] = []
    real_read = SnapshotStore.read_object

    def spy(self: SnapshotStore, sha256: str, *, size: int | None = None) -> bytes:
        calls.append((sha256, size))
        return real_read(self, sha256, size=size)

    monkeypatch.setattr(SnapshotStore, "read_object", spy)
    GUARD_READS[op](guard, flavor, guard_store)
    assert (flavor.path / CONFIG).read_bytes() == ORIGINAL, "the bytes from before are back"
    assert calls, "guard read the object from the store"
    assert calls == [(G.sha(ORIGINAL), len(ORIGINAL))] * len(calls), calls


def test_constructed_guard_restore_refuses_an_over_inflating_object_within_the_bound(
    guard: Any,
    flavor: Any,
    guard_store: SnapshotStore,
    idle: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = _commit_a_change(guard, flavor, guard_store)
    guard_store.object_path(G.sha(ORIGINAL)).write_bytes(_bomb())
    produced = _count_inflation(monkeypatch)
    with (
        pytest.raises(guard.GuardError, match="inflates past"),
        guard.transaction(flavor, label="restore", store=guard_store.path) as tx,
    ):
        tx.restore(snap, paths=[CONFIG])
    assert (flavor.path / CONFIG).read_bytes() == NEW_CONFIG, "nothing is written"
    # Everything inflated in the whole transaction, the pre-write snapshot's
    # reuse checks of the other (small) files included, stays tiny next to
    # the BOMB_MIB the object would inflate to.
    assert sum(produced) < 1 << 20, f"inflated {sum(produced)} bytes"


# ─── (c) the reuse check's utime acts on the checked descriptor ──────────────


@posix_symlinks
def test_constructed_reuse_utime_does_not_touch_a_link_swapped_in_after_the_rehash(
    source: Path, store: SnapshotStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second `create` re-hashes the stored Config object before reusing
    it. Right after the reuse check opens that object, the object is moved
    away and a link to a file outside the store is put at its path. The mtime
    refresh must act on the descriptor it checked, never on what the path
    names now: the file behind the link keeps its bytes and its mtime, and the
    link is replaced by a real object."""
    store.create(source, ["WTF"], now=T0)
    obj = _config_object(store)
    victim = tmp_path / "victim"
    victim.write_bytes(VICTIM)
    old = 1_000_000_000_000_000_000
    os.utime(victim, ns=(old, old))
    moved = tmp_path / "moved-object"
    real_open = snapshot.open_regular_file
    swapped: list[Path] = []

    def open_then_swap(path: Path, **kwargs: Any) -> tuple[int, os.stat_result]:
        fd, st = real_open(path, **kwargs)
        if Path(path) == obj and not swapped:
            swapped.append(obj)
            obj.rename(moved)
            obj.symlink_to(victim)
        return fd, st

    monkeypatch.setattr(snapshot, "open_regular_file", open_then_swap)
    store.create(source, ["WTF"], now=T1)
    assert swapped, "the reuse check never opened the stored object"
    assert victim.read_bytes() == VICTIM
    assert victim.stat().st_mtime_ns == old, "the link target's mtime was not touched"
    assert stat.S_ISREG(obj.lstat().st_mode), "the link was replaced by a real object"
    assert zlib.decompress(obj.read_bytes()) == CONFIG_BYTES
