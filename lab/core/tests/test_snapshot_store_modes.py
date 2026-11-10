"""The store at rest (M16-41): private modes whatever the umask, older stores
fixed on their next write, and `doctor` and `snap verify` warning when other
accounts can open the store.

Every test runs under a set umask (0022, the usual one, unless a test names
another) and restores the process's own on the way out. The source trees are
constructed (L8: the store is our own format, and file modes are a boundary
case of it): a few small files in `tmp_path`. The CLI cases run on the real
M11-03 capture (`fixtures/macos/forever`) copied into `tmp_path` as
`test_cli.py` builds it, with the user data directory redirected and a fake
process table. Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import io
import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_cli import (
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, snapshot
from wowlab_core.snapshot import SnapshotStore

posix_only = pytest.mark.skipif(os.name != "posix", reason="file modes are a POSIX matter")


@pytest.fixture
def umask() -> Iterator[None]:
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


def _tree(tmp_path: Path) -> Path:
    """Constructed: a small source tree with two files in one subtree."""
    src = tmp_path / "src"
    (src / "WTF" / "Account").mkdir(parents=True)
    (src / "WTF" / "Config.wtf").write_bytes(b'SET constructed "1"\n')
    (src / "WTF" / "Account" / "bindings-cache.wtf").write_bytes(b"constructed\n")
    return src


def _modes(top: Path) -> dict[str, int]:
    """Every entry under `top` (itself included as "."), by `lstat`, its permission bits."""
    out = {".": stat.S_IMODE(top.lstat().st_mode)}
    for path in sorted(top.rglob("*")):
        out[path.relative_to(top).as_posix()] = stat.S_IMODE(path.lstat().st_mode)
    return out


def _assert_private(top: Path) -> None:
    for rel, mode in _modes(top).items():
        path = top if rel == "." else top / rel
        if path.is_symlink():
            continue
        want = 0o700 if path.is_dir() else 0o600
        assert mode == want, f"{rel}: {oct(mode)}, wanted {oct(want)}"


def _make_old(store: Path) -> None:
    """Constructed: what an older version left under umask 0022, every
    directory 0755 and every file 0644, plus guard's `journal/` and `lock`
    as such a version could leave them."""
    (store / "journal").mkdir(exist_ok=True)
    (store / "journal" / "0001.json").write_bytes(b"{}\n")
    (store / "lock").touch()
    for path in [store, *store.rglob("*")]:
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() else 0o644)


# -- created private, whatever the umask --------------------------------------


@posix_only
@pytest.mark.parametrize("mask", [0o022, 0o002, 0o000, 0o077], ids=oct)
def test_create_makes_every_directory_0700_and_every_file_0600_constructed(
    tmp_path: Path, mask: int
) -> None:
    old = os.umask(mask)
    try:
        store = SnapshotStore(tmp_path / "data" / "store")
        store.create(_tree(tmp_path), ["WTF"], label="first")
        store.put_object(b"constructed: a Lab write's bytes\n")
    finally:
        os.umask(old)
    _assert_private(store.path)
    assert {"objects", "manifests", "tmp"} <= {p.name for p in store.path.iterdir()}
    assert store.exposure() == snapshot.StoreExposure(checked=True, count=0, entries=())


@posix_only
def test_a_staged_file_is_0600_while_it_is_written_constructed(tmp_path: Path, umask: None) -> None:
    store = SnapshotStore(tmp_path / "store")
    with store._staged("probe") as staged:
        assert stat.S_IMODE(os.fstat(staged.out.fileno()).st_mode) == 0o600
        assert stat.S_IMODE((store.tmp_dir).stat().st_mode) == 0o700


@posix_only
def test_import_stages_and_stores_privately_constructed(tmp_path: Path, umask: None) -> None:
    source = SnapshotStore(tmp_path / "a" / "store")
    src = tmp_path / "install"
    src.mkdir()
    _tree(tmp_path).rename(src / "_constructed_")  # constructed: a flavor folder
    manifest = source.create(
        src, ["_constructed_/WTF"], label="carried", flavor_folder="_constructed_"
    )
    data = snapshot.manifest_bytes(manifest)
    seen: list[int] = []
    target = SnapshotStore(tmp_path / "b" / "store")

    class Source(io.BytesIO):
        def read(self, size: int | None = -1, /) -> bytes:
            # Mid-import: `tmp/` and every file staged in it so far.
            for path in [target.tmp_dir, *target.tmp_dir.iterdir()]:
                seen.append(stat.S_IMODE(path.stat().st_mode))
            return super().read(size)

    def open_object(digest: str) -> io.BytesIO:
        return Source(source.read_object(digest))

    _, added = target.add_snapshot(data, open_object)
    assert added and set(seen) == {0o700, 0o600}
    _assert_private(target.path)


# -- an older store, fixed on its next write ----------------------------------


@posix_only
def test_an_older_store_is_fixed_on_its_next_create_constructed(
    tmp_path: Path, umask: None
) -> None:
    src = _tree(tmp_path)
    store = SnapshotStore(tmp_path / "store")
    store.create(src, ["WTF"], label="old")
    _make_old(store.path)
    before = store.exposure()
    assert before.readable_by_others and before.entries[0] == "."
    assert before.count == len(_modes(store.path))

    store.create(src, ["WTF"], label="next")
    _assert_private(store.path)  # journal/ and lock included
    assert not store.exposure().readable_by_others


@posix_only
def test_an_older_store_is_fixed_by_put_object_and_by_gc_constructed(
    tmp_path: Path, umask: None
) -> None:
    store = SnapshotStore(tmp_path / "store")
    store.create(_tree(tmp_path), ["WTF"], label="old")
    _make_old(store.path)
    store.put_object(b"constructed: bytes new to the store\n")
    _assert_private(store.path)

    _make_old(store.path)
    store.gc(dry_run=True)
    assert store.exposure().readable_by_others  # a dry run writes nothing
    store.gc(dry_run=False)
    _assert_private(store.path)


@posix_only
def test_the_fix_follows_no_link_and_walks_nothing_but_the_store_constructed(
    tmp_path: Path, umask: None
) -> None:
    store = SnapshotStore(tmp_path / "store")
    store.create(_tree(tmp_path), ["WTF"], label="old")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"constructed: not the store's\n")
    outside.chmod(0o644)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o755)
    elsewhere.chmod(0o755)
    (store.path / "objects" / "zz").mkdir()
    (store.path / "objects" / "zz" / "link").symlink_to(outside)
    (store.path / "tmp" / "dirlink").symlink_to(elsewhere, target_is_directory=True)
    # Something else the caller keeps beside the store: judged, never walked.
    other = store.path / "notes"
    (other / "deep").mkdir(parents=True)
    (other / "deep" / "file").write_bytes(b"constructed\n")
    _make_old(store.path)

    store.put_object(b"constructed: bytes new to the store\n")
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644
    assert stat.S_IMODE(elsewhere.stat().st_mode) == 0o755
    assert stat.S_IMODE(other.stat().st_mode) == 0o700  # an entry right below the root
    assert stat.S_IMODE((other / "deep").stat().st_mode) == 0o755  # not walked
    assert stat.S_IMODE((other / "deep" / "file").stat().st_mode) == 0o644
    assert not store.exposure().readable_by_others  # unreachable below notes/


# -- what exposure reports ------------------------------------------------------


@posix_only
def test_exposure_counts_only_what_another_account_can_reach_constructed(
    tmp_path: Path, umask: None
) -> None:
    store = SnapshotStore(tmp_path / "store")
    assert store.exposure() == snapshot.StoreExposure(checked=True, count=0, entries=())
    store.create(_tree(tmp_path), ["WTF"], label="x")
    manifests = store.manifests_dir
    name = next(manifests.iterdir()).name
    (manifests / name).chmod(0o644)
    # Behind a 0700 root and 0700 manifests/: no one else reaches it.
    assert not store.exposure().readable_by_others
    store.path.chmod(0o711)  # others may search the root, not list it
    assert not store.exposure().readable_by_others
    manifests.chmod(0o711)
    exposure = store.exposure()
    assert exposure.count == 1 and exposure.entries == (f"manifests/{name}",)
    # Group may search but only others may read: the classes do not mix.
    store.path.chmod(0o710)
    manifests.chmod(0o710)
    (manifests / name).chmod(0o604)
    assert not store.exposure().readable_by_others


@posix_only
def test_exposure_names_at_most_ten_and_counts_them_all_constructed(
    tmp_path: Path, umask: None
) -> None:
    store = SnapshotStore(tmp_path / "store")
    for i in range(12):
        store.put_object(f"constructed {i}\n".encode())
    _make_old(store.path)
    exposure = store.exposure()
    assert exposure.count == len(_modes(store.path))
    assert len(exposure.entries) == 10 and exposure.entries[0] == "."


def test_off_posix_nothing_is_checked_or_changed_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(snapshot, "_PRIVATE_MODES", False)
    store = SnapshotStore(tmp_path / "store")
    store.create(_tree(tmp_path), ["WTF"], label="x")
    assert store.exposure() == snapshot.StoreExposure(checked=False, count=0, entries=())
    assert store.verify().exposure.checked is False


# -- doctor and snap verify ------------------------------------------------------


_WARNING = "Other accounts on this computer can read the snapshot store"


@posix_only
def test_snap_verify_and_doctor_warn_on_an_open_store_and_not_after_a_write(
    root: Path, user_data: Path, umask: None
) -> None:
    ok("snap", "create", "-m", "first")
    store = user_data / "store"
    _assert_private(store)
    assert _WARNING not in ok("snap", "verify").stdout
    assert _WARNING not in ok("doctor").stdout

    _make_old(store)
    verify = ok("snap", "verify")  # a warning, not a failure
    assert _WARNING in verify.stdout and "all sound" in verify.stdout
    doctor = run("doctor")
    assert _WARNING in doctor.stdout
    report = json.loads(run("doctor", "--json").stdout)
    assert report["store"]["exposure"]["checked"] is True
    assert report["store"]["exposure"]["count"] == len(_modes(store))
    assert report["store"]["exposure"]["entries"][0] == "."
    verified = json.loads(ok("snap", "verify", "--json").stdout)
    assert verified["exposure"]["count"] == len(_modes(store))

    ok("snap", "create", "-m", "second")
    _assert_private(store)
    assert _WARNING not in ok("snap", "verify").stdout
    assert _WARNING not in run("doctor").stdout


def test_on_windows_snap_verify_and_doctor_say_the_check_is_not_made(
    root: Path, user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(snapshot, "_PRIVATE_MODES", False)
    ok("snap", "create", "-m", "first")
    assert "does not check who else can read the snapshot store" in ok("snap", "verify").stdout
    doctor = run("doctor", "--notes").stdout
    assert "does not check who else can read the snapshot store" in doctor
    assert _WARNING not in doctor
    assert cli._STORE_MODES_NOTE not in run("doctor").stdout  # behind --notes


@posix_only
def test_a_guard_write_leaves_nothing_others_can_reach_and_the_next_one_fixes_the_journal(
    root: Path, flavor: Path, user_data: Path, umask: None
) -> None:
    """Guard makes the store root, its `lock` and `journal/` itself; the
    pre-write snapshot makes the root private before the journal exists, and
    the next write's snapshot fixes `journal/` (constructed: the edit)."""
    first = json.loads(ok("snap", "create", "-m", "a", "--json").stdout)
    config = flavor / "WTF" / "Config.wtf"
    config.write_bytes(config.read_bytes() + b'SET constructed "edit"\n')
    ok("snap", "restore", first["id"], "--yes")
    store = SnapshotStore(user_data / "store")
    assert (store.path / "journal").is_dir() and (store.path / "lock").is_file()
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o700
    assert not store.exposure().readable_by_others
    ok("undo", "--yes")
    _assert_private(store.path)
