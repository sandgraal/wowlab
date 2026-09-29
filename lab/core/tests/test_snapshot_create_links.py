"""`create` never writes through a link (M11-18, from the #112 security review).

Every input here is constructed: the store is our own format, and these are
the hostile and boundary cases L8 allows to be built by hand. Nothing touches
a real install or the real user data directory (the CLI cases redirect it
into `tmp_path`).

`create` writes only into the store's `tmp/`, `manifests/`, `objects/` and an
object shard. A link at any of them (to a directory outside the store, or
dangling), or a file where a directory belongs, makes `create` refuse with a
`SnapshotError` and write nothing behind the link. Each case runs twice: on
the descriptor path POSIX takes (`O_DIRECTORY | O_NOFOLLOW`, `os.replace` by
`dir_fd`) and on the `lstat` path Windows takes, emulated here by turning the
descriptor path off. Also: `set_label` takes the same care, the store root
may itself sit below a linked directory, `snap verify` names a linked
`objects/` instead of printing ".", and `snap gc` counts only the bytes it
removed.
"""

from __future__ import annotations

import hashlib
import os
import sys
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import platformdirs
import pytest

from wowlab_core import snapshot
from wowlab_core.snapshot import SnapshotError, SnapshotStore

T0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)
CONFIG_BYTES = b'SET portal "US"\n'
OTHER_BYTES = b"other\n"
REFUSAL = "never writes through a link"

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
    (root / "WTF" / "other.wtf").write_bytes(OTHER_BYTES)
    return root


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path / "userdata" / "store")


@pytest.fixture(
    params=[
        pytest.param("dir-fd", id="constructed-dir-fd"),
        pytest.param("lstat", id="constructed-lstat-only"),
    ]
)
def write_path(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """The descriptor path (POSIX) or the `lstat` path (Windows, emulated)."""
    mode: str = request.param
    if mode == "dir-fd":
        if not snapshot._WRITE_BY_DIR_FD:
            pytest.skip("this platform has no directory descriptors")
    else:
        monkeypatch.setattr(snapshot, "_WRITE_BY_DIR_FD", False)
    return mode


def _config_shard() -> str:
    return hashlib.sha256(CONFIG_BYTES).hexdigest()[:2]


def _tree(path: Path) -> list[str]:
    """Every entry under `path`, links not followed."""
    if not path.exists():
        return []
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(path):
        for name in dirnames + filenames:
            found.append(Path(dirpath, name).relative_to(path).as_posix())
    return sorted(found)


def _link_point(store: SnapshotStore, which: str) -> Path:
    """Make the parents of the directory `which` and return where it goes."""
    if which == "shard":
        store.objects_dir.mkdir(parents=True)
        return store.objects_dir / _config_shard()
    store.path.mkdir(parents=True)
    return {"objects": store.objects_dir, "manifests": store.manifests_dir, "tmp": store.tmp_dir}[
        which
    ]


def _assert_nothing_stored_behind(store: SnapshotStore, outside: Path, before: list[str]) -> None:
    assert _tree(outside) == before, "nothing was written behind the link"
    assert store.list() == (), "no manifest was published"
    if store.tmp_dir.is_dir() and not store.tmp_dir.is_symlink():
        assert list(store.tmp_dir.iterdir()) == [], "no staged file is left in tmp/"


WHICH = [
    pytest.param("objects", id="constructed-objects"),
    pytest.param("shard", id="constructed-shard"),
    pytest.param("manifests", id="constructed-manifests"),
    pytest.param("tmp", id="constructed-tmp"),
]


# ─── the acceptance cases: a linked objects/, shard, manifests/, tmp/ ────────


@posix_symlinks
@pytest.mark.parametrize("which", WHICH)
def test_constructed_create_refuses_a_linked_store_directory_and_writes_nothing_behind_it(
    which: str, write_path: str, source: Path, store: SnapshotStore, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = _link_point(store, which)
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SnapshotError, match=REFUSAL):
        store.create(source, ["WTF"], now=T0)

    assert _tree(outside) == [], "nothing was written behind the link"
    assert link.is_symlink(), "the link itself is left as it is"
    _assert_nothing_stored_behind(store, outside, [])


@posix_symlinks
@pytest.mark.parametrize("which", WHICH)
def test_constructed_create_refuses_a_dangling_link_and_creates_nothing_at_its_target(
    which: str, write_path: str, source: Path, store: SnapshotStore, tmp_path: Path
) -> None:
    """A dangling link (an unmounted volume, say) is never made real by a
    `mkdir` through it."""
    nowhere = tmp_path / "nowhere"
    link = _link_point(store, which)
    link.symlink_to(nowhere, target_is_directory=True)

    with pytest.raises(SnapshotError, match=REFUSAL):
        store.create(source, ["WTF"], now=T0)

    assert not nowhere.exists() and link.is_symlink()


@pytest.mark.parametrize("which", WHICH)
def test_constructed_create_refuses_a_file_where_a_store_directory_belongs(
    which: str, write_path: str, source: Path, store: SnapshotStore
) -> None:
    spot = _link_point(store, which)
    spot.write_bytes(b"constructed: not a directory\n")
    with pytest.raises(SnapshotError, match=REFUSAL):
        store.create(source, ["WTF"], now=T0)
    assert spot.read_bytes() == b"constructed: not a directory\n"


@windows_only
@pytest.mark.parametrize("which", WHICH)
def test_constructed_create_refuses_a_junction_store_directory(
    which: str, source: Path, store: SnapshotStore, tmp_path: Path
) -> None:
    import _winapi  # Windows only; skipped elsewhere

    outside = tmp_path / "outside"
    outside.mkdir()
    _winapi.CreateJunction(str(outside), str(_link_point(store, which)))
    with pytest.raises(SnapshotError, match=REFUSAL):
        store.create(source, ["WTF"], now=T0)
    assert _tree(outside) == []


@posix_symlinks
@pytest.mark.parametrize(
    "which",
    [
        pytest.param("objects", id="constructed-objects"),
        pytest.param("shard", id="constructed-shard"),
    ],
)
def test_constructed_objects_behind_a_link_are_not_reused(
    which: str, write_path: str, source: Path, store: SnapshotStore, tmp_path: Path
) -> None:
    """The store's real objects moved outside and linked back: a second
    `create` must not reuse them through the link (its manifest would name
    objects `verify` reports missing). It refuses; nothing is added outside
    and no second manifest is written."""
    first = store.create(source, ["WTF"], now=T0)
    moved = tmp_path / "moved"
    real = store.objects_dir if which == "objects" else store.objects_dir / _config_shard()
    real.rename(moved)
    real.symlink_to(moved, target_is_directory=True)
    before = _tree(moved)
    mtimes = {p: (moved / p).stat().st_mtime_ns for p in before}

    with pytest.raises(SnapshotError, match=REFUSAL):
        store.create(source, ["WTF"], now=T1)

    assert _tree(moved) == before
    assert {p: (moved / p).stat().st_mtime_ns for p in before} == mtimes, "not even freshened"
    assert [m.id for m in store.list()] == [first.id]


# ─── races: swapped for a link after the check ───────────────────────────────


@posix_symlinks
@pytest.mark.parametrize(
    "which",
    [
        pytest.param("shard", id="constructed-shard"),
        pytest.param("manifests", id="constructed-manifests"),
        pytest.param("tmp", id="constructed-tmp"),
    ],
)
def test_constructed_directory_swapped_for_a_link_after_its_lstat_is_not_written_through(
    which: str, source: Path, store: SnapshotStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The descriptor path: the directory passes its `lstat`, and only then is
    it swapped for a link to an outside directory. The `O_NOFOLLOW` open, or
    the identity check against that `lstat`, refuses it."""
    if not snapshot._WRITE_BY_DIR_FD:
        pytest.skip("this platform has no directory descriptors")
    name = _config_shard() if which == "shard" else which
    parent = store.objects_dir if which == "shard" else store.path
    parent.mkdir(parents=True)
    (parent / name).mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    real_stat = os.stat
    swapped: list[str] = []

    def stat_then_swap(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        st = real_stat(path, *args, **kwargs)
        if (
            not swapped
            and os.fspath(path) == name
            and kwargs.get("dir_fd") is not None
            and kwargs.get("follow_symlinks") is False
        ):
            swapped.append(name)
            (parent / name).rename(tmp_path / "moved")
            (parent / name).symlink_to(outside, target_is_directory=True)
        return st

    monkeypatch.setattr(os, "stat", stat_then_swap)
    with pytest.raises(SnapshotError, match=REFUSAL):
        store.create(source, ["WTF"], now=T0)
    monkeypatch.undo()
    assert swapped, "the swap happened after the check"
    assert _tree(outside) == [], "nothing was written behind the link"


@posix_symlinks
def test_constructed_shard_swapped_for_a_link_during_the_reuse_check_is_not_reused(
    write_path: str,
    source: Path,
    store: SnapshotStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reuse check finds a real shard, and before it opens the object the
    shard is swapped for a link to a directory holding a copy of it. The
    object opened through the link is not the entry of the checked shard, so
    it is not reused (nor its mtime touched), and the rewrite refuses the
    link."""
    first = store.create(source, ["WTF"], now=T0)
    obj = store.object_path(hashlib.sha256(CONFIG_BYTES).hexdigest())
    shard = obj.parent
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / obj.name).write_bytes(obj.read_bytes())
    old = 1_000_000_000_000_000_000
    os.utime(copy / obj.name, ns=(old, old))
    real_open = snapshot.open_regular_file
    swapped: list[Path] = []

    def swap_then_open(path: Path, **kwargs: Any) -> tuple[int, os.stat_result]:
        if Path(path) == obj and not swapped:
            swapped.append(obj)
            shard.rename(tmp_path / "moved-shard")
            shard.symlink_to(copy, target_is_directory=True)
        return real_open(path, **kwargs)

    monkeypatch.setattr(snapshot, "open_regular_file", swap_then_open)
    with pytest.raises(SnapshotError, match=REFUSAL):
        store.create(source, ["WTF"], now=T1)
    assert swapped
    assert _tree(copy) == [obj.name]
    assert (copy / obj.name).stat().st_mtime_ns == old, "the copy behind the link was not freshened"
    assert [m.id for m in store.list()] == [first.id]


# ─── what still works ────────────────────────────────────────────────────────


def test_constructed_create_still_makes_every_store_directory_and_verifies(
    write_path: str, source: Path, store: SnapshotStore
) -> None:
    manifest = store.create(source, ["WTF"], now=T0)
    assert [m.id for m in store.list()] == [manifest.id]
    assert store.read_file(manifest.id, "WTF/Config.wtf") == CONFIG_BYTES
    assert store.verify().ok
    assert list(store.tmp_dir.iterdir()) == []
    again = store.create(source, ["WTF"], now=T1)  # every object reused
    assert store.verify().ok and len(store.list()) == 2 and again.entries == manifest.entries


def test_constructed_create_heals_a_damaged_object_in_a_real_shard(
    write_path: str, source: Path, store: SnapshotStore
) -> None:
    store.create(source, ["WTF"], now=T0)
    obj = store.object_path(hashlib.sha256(CONFIG_BYTES).hexdigest())
    obj.write_bytes(zlib.compress(b"damaged\n"))
    store.create(source, ["WTF"], now=T1)
    assert zlib.decompress(obj.read_bytes()) == CONFIG_BYTES
    assert store.verify().ok


@posix_symlinks
def test_constructed_a_store_below_a_linked_directory_still_works(
    write_path: str, source: Path, tmp_path: Path
) -> None:
    """Only the store's own directories are held to not being links; the store
    root is opened as the owner named it and may sit below a link (`/var` on
    macOS is one)."""
    real = tmp_path / "real-userdata"
    real.mkdir()
    linked = tmp_path / "linked-userdata"
    linked.symlink_to(real, target_is_directory=True)
    store = SnapshotStore(linked / "store")
    manifest = store.create(source, ["WTF"], now=T0)
    assert store.verify().ok and (real / "store" / "manifests" / f"{manifest.id}.json").is_file()


# ─── set_label writes as create does ─────────────────────────────────────────


@posix_symlinks
@pytest.mark.parametrize(
    "which",
    [
        pytest.param("manifests", id="constructed-manifests"),
        pytest.param("tmp", id="constructed-tmp"),
    ],
)
def test_constructed_set_label_refuses_a_linked_manifests_or_tmp(
    which: str, write_path: str, source: Path, store: SnapshotStore, tmp_path: Path
) -> None:
    manifest = store.create(source, ["WTF"], now=T0)
    moved = tmp_path / "moved"
    real = store.manifests_dir if which == "manifests" else store.tmp_dir
    real.rename(moved)
    real.symlink_to(moved, target_is_directory=True)
    before = {p: (moved / p).read_bytes() for p in _tree(moved)}
    with pytest.raises(SnapshotError, match=REFUSAL):
        store.set_label(manifest.id, "relabelled")
    assert {p: (moved / p).read_bytes() for p in _tree(moved)} == before


# ─── snap verify and snap gc wording ─────────────────────────────────────────


@pytest.fixture
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "redirected-userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    return redirected


@posix_symlinks
def test_constructed_verify_names_a_linked_objects_dir_instead_of_a_dot(
    source: Path, tmp_path: Path, user_data: Path
) -> None:
    from typer.testing import CliRunner

    from wowlab_core import cli

    store = SnapshotStore(snapshot.default_store_path())
    assert user_data in store.path.parents, "the user data directory is redirected"
    store.create(source, ["WTF"], now=T0)
    store.objects_dir.rename(tmp_path / "objects-elsewhere")
    store.objects_dir.symlink_to(tmp_path / "objects-elsewhere", target_is_directory=True)

    report = store.verify()
    assert report.corrupt_objects == (snapshot.OBJECTS_DIR_ENTRY,) == ("objects/",)
    assert not report.ok

    result = CliRunner().invoke(cli.app, ["snap", "verify"])
    assert result.exit_code == 1, (result.stdout, result.stderr)
    out = result.stdout + result.stderr
    assert "corrupt object: ." not in out
    assert "objects/ is a link: nothing under it was checked" in out


def test_constructed_gc_counts_only_the_bytes_it_removed(
    source: Path, tmp_path: Path, user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two unreferenced objects; the second is found changed at its delete and
    skipped. The report and `snap gc` count the first one's bytes as removed,
    not both."""
    from typer.testing import CliRunner

    from wowlab_core import cli

    store = SnapshotStore(snapshot.default_store_path())
    store.create(source, ["WTF"], now=T0)
    used = {p.name for p in store.objects_dir.iterdir()}
    shard = next(f"{i:02x}" for i in range(256) if f"{i:02x}" not in used)
    (store.objects_dir / shard).mkdir()
    gone = store.objects_dir / shard / ("1" * 62)
    kept = store.objects_dir / shard / ("2" * 62)
    gone.write_bytes(zlib.compress(b"unreferenced, removed\n"))
    kept.write_bytes(zlib.compress(b"unreferenced, and skipped at its delete: longer\n" * 8))
    old = 1_000_000_000_000_000_000  # well past gc's grace period
    for path in (gone, kept):
        os.utime(path, ns=(old, old))
    real_remove = SnapshotStore._remove_object

    def skip_kept(self: SnapshotStore, obj: Any) -> bool:
        return False if obj.path == kept else real_remove(self, obj)

    monkeypatch.setattr(SnapshotStore, "_remove_object", skip_kept)
    result = CliRunner().invoke(cli.app, ["snap", "gc", "--yes"])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    size = len(zlib.compress(b"unreferenced, removed\n"))
    assert f"Removed 1 object(s), {cli._bytes(size)}." in result.stdout + result.stderr
    assert not gone.exists() and kept.exists()

    kept_size = kept.stat().st_size
    report = store.gc(dry_run=False)
    assert report.removed == () and report.removed_bytes == 0
    assert report.unreferenced_bytes == kept_size
