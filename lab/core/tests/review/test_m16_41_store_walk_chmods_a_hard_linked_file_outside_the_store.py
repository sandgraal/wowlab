# Probe from security review of m16/41-store-at-rest; reproduces the store's mode walk changing the mode of a file outside the store through a hard link planted in it.
"""`_privatize_below` refuses symlinks (`lstat`, `O_NOFOLLOW`, identity
check), but a mode belongs to the inode, not the name: a regular file in the
store that is a second hard link to a file elsewhere is opened and `fchmod`ed,
and the file outside the store loses its group and other bits. The store
never makes a hard link, so an entry with `st_nlink > 1` is not the store's
and should be left as it is (as guard's lock file refuses one). The tree is
constructed (a boundary test of the store's own format); nothing touches an
install."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from wowlab_core.snapshot import SnapshotStore

pytestmark = pytest.mark.skipif(os.name != "posix", reason="file modes are a POSIX matter")


def _old(top: Path) -> None:
    for path in [top, *top.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)


def test_a_write_never_changes_the_mode_of_a_file_outside_the_store_constructed(
    tmp_path: Path,
) -> None:
    old_mask = os.umask(0o022)
    try:
        src = tmp_path / "src" / "WTF"
        src.mkdir(parents=True)
        (src / "Config.wtf").write_bytes(b'SET constructed "1"\n')
        store = SnapshotStore(tmp_path / "store")
        store.create(tmp_path / "src", ["WTF"], label="constructed")
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"constructed: not the store's\n")
        os.link(outside, store.path / "objects" / "planted")
        _old(store.path)
        outside.chmod(0o644)

        store.put_object(b"constructed: bytes new to the store\n")

        assert stat.S_IMODE(outside.stat().st_mode) == 0o644
    finally:
        os.umask(old_mask)
