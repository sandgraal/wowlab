# Probe from review of m16/19-errors-in-words; reproduces a full disk met while creating a store folder (an object shard) reported as "cannot inspect ...: [Errno 28]" instead of as a full store.
"""M16-19: "A full store is reported as a full store." The branch maps
ENOSPC and EDQUOT to `StoreFullError` when the store opens a staged file,
writes it, syncs it or moves it into place, but not when it creates one of
its own folders. A new object shard (`objects/<2 hex>/`) is a new folder,
and on a full disk `mkdir` fails with ENOSPC like any write does; the
store then raises a plain `SnapshotError` that keeps the raw `[Errno 28]`
and never says the store is full.

Constructed input (labelled): `os.mkdir` fails with ENOSPC for a shard
folder only, as it does on a disk with no block left for a new directory.
The install is the captured tree (`fixtures/macos/`) copied into
`tmp_path`; the store is under `tmp_path`. Nothing touches a real install.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
from pathlib import Path

import pytest

from wowlab_core import snapshot

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_SHARD = re.compile(r"\A[0-9a-f]{2}\Z")


@pytest.fixture
def flavor(tmp_path: Path) -> Path:
    dest = tmp_path / "install" / "flavor"
    shutil.copytree(FIXTURES / "macos" / "forever", dest)
    return dest


def _full_on_shard_mkdir(monkeypatch: pytest.MonkeyPatch) -> None:
    real = os.mkdir

    def mkdir(path: str | Path, *args: object, **kwargs: object) -> None:
        if _SHARD.match(Path(path).name):
            raise OSError(errno.ENOSPC, "No space left on device", str(path))
        real(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "mkdir", mkdir)


def test_positive_control_unpatched_create_succeeds(flavor: Path, tmp_path: Path) -> None:
    store = snapshot.SnapshotStore(tmp_path / "store")
    manifest = store.create(flavor, subtrees=("WTF",))
    assert manifest.id


def test_positive_control_full_disk_on_publish_is_a_full_store_constructed(
    flavor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(snapshot.SnapshotStore, "_publish", staticmethod(full))
    store = snapshot.SnapshotStore(tmp_path / "store")
    with pytest.raises(snapshot.StoreFullError, match="is full"):
        store.create(flavor, subtrees=("WTF",))


def test_full_disk_creating_an_object_shard_is_a_full_store_constructed(
    flavor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = snapshot.SnapshotStore(tmp_path / "store")
    _full_on_shard_mkdir(monkeypatch)
    with pytest.raises(snapshot.SnapshotError) as caught:
        store.create(flavor, subtrees=("WTF",))
    # Today: SnapshotError("cannot inspect <store>/objects/5b: [Errno 28] No space left on device").
    assert isinstance(caught.value, snapshot.StoreFullError), str(caught.value)
    assert "Errno" not in str(caught.value)
