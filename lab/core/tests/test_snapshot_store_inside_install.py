"""`SnapshotStore.put_object` and `gc` refuse a store inside an install
(M16-68, from the M16-41 code review).

`create` already refused a store inside any install (L1, `docs/LAB_PLAN.md`
§6.10); `put_object` and `gc(dry_run=False)` did not, so a library caller
could write or delete objects there. Both now run the same rule, `guard`'s,
before anything is created, listed or removed.

Constructed (L8: hostile and boundary cases of the store, our own format): a
synthetic install is a directory under `tmp_path` holding an empty
`.build.info` or `.flavor.info`. Nothing reads or writes a real install.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from wowlab_core import guard
from wowlab_core.snapshot import SnapshotStore, StoreLocationError

DATA = b"constructed: bytes put_object is asked to store\n"
MARKERS = [".build.info", ".flavor.info"]


def _tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def _install(tmp_path: Path, marker: str) -> Path:
    install = tmp_path / "constructed-install"
    install.mkdir()
    (install / marker).write_bytes(b"")
    return install


@pytest.mark.parametrize("marker", MARKERS)
def test_put_object_refuses_a_store_inside_an_install_constructed(
    tmp_path: Path, marker: str
) -> None:
    install = _install(tmp_path, marker)
    store = SnapshotStore(install / "WTF" / "wowlab" / "store")
    with pytest.raises(StoreLocationError, match="inside an install"):
        store.put_object(DATA)
    assert _tree(install) == [marker], "nothing created inside the install"


@pytest.mark.parametrize("marker", MARKERS)
def test_put_object_refuses_an_existing_store_inside_an_install_constructed(
    tmp_path: Path, marker: str
) -> None:
    """A store already there (made by an older build or by hand) gets no new object."""
    install = _install(tmp_path, marker)
    store = SnapshotStore(install / "store")
    store.objects_dir.mkdir(parents=True)
    before = _tree(install)
    with pytest.raises(StoreLocationError, match="inside an install"):
        store.put_object(DATA)
    assert _tree(install) == before
    assert not store.object_path(hashlib.sha256(DATA).hexdigest()).exists()


def _store_with_garbage(path: Path) -> tuple[SnapshotStore, Path]:
    """A store outside every install with one unreferenced object, moved
    afterwards into the install, as a store left there would be."""
    store = SnapshotStore(path)
    digest = store.put_object(DATA)
    store.manifests_dir.mkdir(parents=True, exist_ok=True)
    return store, store.object_path(digest)


@pytest.mark.parametrize("marker", MARKERS)
def test_gc_refuses_to_remove_from_a_store_inside_an_install_constructed(
    tmp_path: Path, marker: str
) -> None:
    outside = tmp_path / "outside"
    _store_with_garbage(outside / "store")
    install = _install(tmp_path, marker)
    (outside / "store").rename(install / "store")
    store = SnapshotStore(install / "store")
    obj = store.object_path(hashlib.sha256(DATA).hexdigest())
    before = _tree(install)
    with pytest.raises(StoreLocationError, match="inside an install"):
        store.gc(dry_run=False)
    assert obj.exists(), "the unreferenced object is not deleted"
    assert _tree(install) == before


def test_gc_dry_run_still_reads_a_store_inside_an_install_constructed(tmp_path: Path) -> None:
    """A dry run only reads; the ticket leaves it allowed, and it removes nothing."""
    outside = tmp_path / "outside"
    _store_with_garbage(outside / "store")
    install = _install(tmp_path, ".build.info")
    (outside / "store").rename(install / "store")
    store = SnapshotStore(install / "store")
    before = _tree(install)
    report = store.gc(dry_run=True)
    assert report.unreferenced == (hashlib.sha256(DATA).hexdigest(),)
    assert report.removed == ()
    assert _tree(install) == before


def test_put_object_and_gc_still_work_outside_every_install_constructed(tmp_path: Path) -> None:
    store, obj = _store_with_garbage(tmp_path / "ud" / "store")
    assert obj.exists()
    report = store.gc(dry_run=False)
    assert report.removed == (hashlib.sha256(DATA).hexdigest(),)
    assert not obj.exists()


@pytest.mark.parametrize("call", ["put_object", "gc"])
def test_the_rule_is_guards_own_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, call: str
) -> None:
    """No second rule: both refusals come from `guard._refuse_inside_any_install`,
    called on the store path before anything under it is created."""
    calls: list[tuple[Path, str]] = []

    def refuse(path: Path, what: str) -> Path:
        calls.append((path, what))
        raise guard.GuardError(f"{what} {path} is inside an install (constructed)")

    monkeypatch.setattr(guard, "_refuse_inside_any_install", refuse)
    store = SnapshotStore(tmp_path / "ud" / "store")
    with pytest.raises(StoreLocationError, match="inside an install") as caught:
        if call == "put_object":
            store.put_object(DATA)
        else:
            store.gc(dry_run=False)
    assert isinstance(caught.value.__cause__, guard.GuardError)
    assert calls == [(store.path, "the store")]
    assert not (tmp_path / "ud").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
@pytest.mark.parametrize("marker", MARKERS)
def test_put_object_rechecks_after_opening_the_store_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marker: str
) -> None:
    """Fix round 1 (security review, low 2): the store's parent is swapped
    for a link into an install after the first check and before the store's
    folder is opened, through a monkeypatched hook. The second check, made
    once the folder is open, refuses before any byte is written."""
    install = _install(tmp_path, marker)
    parent = tmp_path / "ud"
    parent.mkdir()
    store = SnapshotStore(parent / "store")
    real = SnapshotStore._refuse_linked_store_dirs
    swapped: list[bool] = []

    def swap_then_check(self: SnapshotStore) -> None:
        parent.rmdir()
        parent.symlink_to(install, target_is_directory=True)
        swapped.append(True)
        real(self)

    monkeypatch.setattr(SnapshotStore, "_refuse_linked_store_dirs", swap_then_check)
    with pytest.raises(StoreLocationError, match="second check") as caught:
        store.put_object(DATA)
    assert swapped == [True]
    assert isinstance(caught.value.__cause__, guard.GuardError)
    files = sorted(p.name for p in install.rglob("*") if not p.is_dir())
    assert files == [marker], "no object, staged file or other byte written in the install"
    assert not (install / "store" / "objects").exists()
    assert not (install / "store" / "tmp").exists()
