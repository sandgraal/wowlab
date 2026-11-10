"""Security-review probes for M16-68 fix round 2: `put_object` writes only
into the root it checked.

Constructed (L8: hostile cases of the store, our own format): a synthetic
install is a directory under `tmp_path` holding an empty `.build.info`.
Nothing reads or writes a real install. The swaps are same-user races
(docs/LAB_PLAN.md §6.10); these record that the POSIX pin refuses them.
"""

from __future__ import annotations

import errno
import sys
from pathlib import Path

import pytest

from wowlab_core import snapshot
from wowlab_core.snapshot import SnapshotError, SnapshotStore, StoreLocationError

DATA = b"constructed: review probe bytes, swap after the re-check\n"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not snapshot._WRITE_BY_DIR_FD,
    reason="the pin needs held directory descriptors (POSIX) and symlinks",
)


def _install(tmp_path: Path) -> Path:
    install = tmp_path / "constructed-install"
    install.mkdir()
    (install / ".build.info").write_bytes(b"")
    return install


def _files(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def test_a_swap_after_the_recheck_is_refused_and_the_install_untouched_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = _install(tmp_path)
    parent = tmp_path / "ud"
    parent.mkdir()
    store = SnapshotStore(parent / "store")
    real = SnapshotStore._recheck_inside_any_install

    def recheck_then_swap(self: SnapshotStore) -> None:
        real(self)  # passes: the store is outside every install here
        parent.rename(tmp_path / "ud-moved")
        parent.symlink_to(install, target_is_directory=True)

    monkeypatch.setattr(SnapshotStore, "_recheck_inside_any_install", recheck_then_swap)
    with pytest.raises(StoreLocationError, match="no longer names the folder") as caught:
        store.put_object(DATA)
    assert type(caught.value) is StoreLocationError, "not wrapped"
    assert _files(install) == [".build.info"], "nothing created inside the install"
    assert store._root_pinned is False
    moved = tmp_path / "ud-moved" / "store"
    assert not [p for p in moved.rglob("*") if p.is_file()], _files(moved)


def test_the_pin_is_cleared_when_an_oserror_is_wrapped_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SnapshotStore(tmp_path / "ud" / "store")
    seen: list[bool] = []

    def boom(self: SnapshotStore, *args: object, **kwargs: object) -> object:
        seen.append(self._root_pinned)
        raise OSError(errno.EIO, "constructed I/O error")

    monkeypatch.setattr(SnapshotStore, "_store_stream", boom)
    with pytest.raises(SnapshotError) as caught:
        store.put_object(DATA)
    assert type(caught.value) is SnapshotError
    assert seen == [True], "pinned while writing"
    assert store._root_pinned is False


def test_the_pin_does_not_outlive_put_object_constructed(tmp_path: Path) -> None:
    """After `put_object`, a later write on the same store object is not
    refused by a stale pin."""
    store = SnapshotStore(tmp_path / "ud" / "store")
    store.put_object(DATA)
    assert store._root_pinned is False
    src = tmp_path / "src" / "_flavor_" / "WTF"
    src.mkdir(parents=True)
    (src / "Config.wtf").write_bytes(DATA)
    store.create(tmp_path / "src", ["_flavor_/WTF"], flavor_folder="_flavor_")
