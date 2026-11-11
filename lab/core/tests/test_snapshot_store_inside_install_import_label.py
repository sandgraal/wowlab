"""`SnapshotStore.add_snapshot` and `set_label` refuse a store inside an
install (M16-75, from the M16-68 reviews).

M16-68 gave `put_object` and `gc` the inside-any-install rule; `add_snapshot`
(`snap import`) and `set_label` (`profile delete`) still wrote into a store
inside an install when the library was called directly. Every CLI caller
holds `guard.store_lock`, which refuses first, so this is defence in depth
(L1). Both now run `guard`'s rule before anything is created.

Constructed (L8: hostile and boundary cases of the store, our own format): a
synthetic install is a directory under `tmp_path` holding an empty
`.build.info` or `.flavor.info`, and the snapshot is taken from a
constructed tree under `tmp_path`. Nothing reads or writes a real install.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from wowlab_core import guard, snapshot
from wowlab_core.snapshot import Manifest, SnapshotStore, StoreLocationError

MARKERS = [".build.info", ".flavor.info"]
FLAVOR = "_constructed_"


def _tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _install(tmp_path: Path, marker: str) -> Path:
    install = tmp_path / "constructed-install"
    install.mkdir()
    (install / marker).write_bytes(b"")
    return install


def _source(tmp_path: Path) -> tuple[SnapshotStore, Manifest]:
    """A store outside every install holding one snapshot of a constructed tree."""
    src = tmp_path / "src"
    wtf = src / FLAVOR / "WTF"
    wtf.mkdir(parents=True)
    (wtf / "Config.wtf").write_bytes(b'SET constructed "1"\r\n')
    store = SnapshotStore(tmp_path / "outside" / "store")
    manifest = store.create(src, [f"{FLAVOR}/WTF"], label="before", flavor_folder=FLAVOR)
    return store, manifest


def _import(source: SnapshotStore, manifest: Manifest, target: SnapshotStore) -> None:
    def open_object(digest: str) -> io.BytesIO:
        return io.BytesIO(source.read_object(digest))

    target.add_snapshot(snapshot.manifest_bytes(manifest), open_object)


# -- add_snapshot -------------------------------------------------------------


@pytest.mark.parametrize("marker", MARKERS)
def test_add_snapshot_refuses_a_store_inside_an_install_constructed(
    tmp_path: Path, marker: str
) -> None:
    source, manifest = _source(tmp_path)
    install = _install(tmp_path, marker)
    target = SnapshotStore(install / "WTF" / "wowlab" / "store")
    with pytest.raises(StoreLocationError, match="inside an install"):
        _import(source, manifest, target)
    assert _tree(install) == [marker], "nothing created inside the install"


@pytest.mark.parametrize("marker", MARKERS)
def test_add_snapshot_refuses_an_existing_store_inside_an_install_constructed(
    tmp_path: Path, marker: str
) -> None:
    """A store already there (made by an older build or by hand) gets no snapshot."""
    source, manifest = _source(tmp_path)
    install = _install(tmp_path, marker)
    target = SnapshotStore(install / "store")
    target.manifests_dir.mkdir(parents=True)
    target.objects_dir.mkdir(parents=True)
    before = _tree(install)
    with pytest.raises(StoreLocationError, match="inside an install"):
        _import(source, manifest, target)
    assert _tree(install) == before
    assert target.list() == ()


def test_add_snapshot_still_works_outside_every_install_constructed(tmp_path: Path) -> None:
    source, manifest = _source(tmp_path)
    target = SnapshotStore(tmp_path / "ud" / "store")
    _import(source, manifest, target)
    assert [m.id for m in target.list()] == [manifest.id]


# -- set_label ----------------------------------------------------------------


@pytest.mark.parametrize("marker", MARKERS)
def test_set_label_refuses_a_store_inside_an_install_constructed(
    tmp_path: Path, marker: str
) -> None:
    """A store made outside every install and moved into one afterwards, as a
    store left there would be: its manifest is not rewritten."""
    _, manifest = _source(tmp_path)
    install = _install(tmp_path, marker)
    (tmp_path / "outside" / "store").rename(install / "store")
    store = SnapshotStore(install / "store")
    before = _files(install)
    tree = _tree(install)
    with pytest.raises(StoreLocationError, match="inside an install"):
        store.set_label(manifest.id, "after")
    assert _files(install) == before, "no byte changed inside the install"
    assert _tree(install) == tree, "nothing created inside the install"
    assert store.show(manifest.id).label == "before"


def test_set_label_still_works_outside_every_install_constructed(tmp_path: Path) -> None:
    source, manifest = _source(tmp_path)
    assert source.set_label(manifest.id, "after").label == "after"
    assert source.show(manifest.id).label == "after"


# -- one rule, guard's ----------------------------------------------------------


@pytest.mark.parametrize("call", ["add_snapshot", "set_label"])
def test_the_rule_is_guards_own_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, call: str
) -> None:
    """No second rule: both refusals come from `guard._refuse_inside_any_install`,
    called on the store path before anything under it is created or written."""
    source, manifest = _source(tmp_path)
    calls: list[tuple[Path, str]] = []

    def refuse(path: Path, what: str) -> Path:
        calls.append((path, what))
        raise guard.GuardError(f"{what} {path} is inside an install (constructed)")

    if call == "add_snapshot":
        store = SnapshotStore(tmp_path / "ud" / "store")
        monkeypatch.setattr(guard, "_refuse_inside_any_install", refuse)
        with pytest.raises(StoreLocationError, match="inside an install") as caught:
            _import(source, manifest, store)
        assert not (tmp_path / "ud").exists()
    else:
        store = source
        before = _files(store.path)
        monkeypatch.setattr(guard, "_refuse_inside_any_install", refuse)
        with pytest.raises(StoreLocationError, match="inside an install") as caught:
            store.set_label(manifest.id, "after")
        assert _files(store.path) == before
    assert isinstance(caught.value.__cause__, guard.GuardError)
    assert calls == [(store.path, "the store")]
