# Probe from review of m16/41-store-at-rest; reproduces the store's mode fix changing the mode of an install file hard-linked into an older store
"""The M16-41 fix walk (`snapshot._private_child`) checks an entry by `lstat`,
opens it with `O_NOFOLLOW` and checks its identity, then `fchmod`s it. A hard
link is not a link to `O_NOFOLLOW`: it is the same inode as a file elsewhere.
In an older store whose directories another account could write (an older
version under umask 0002 or 0000), that account can plant a hard link to one
of the player's files, on a platform without Linux's `protected_hardlinks`
(macOS). The next store write then changes the mode of that file, wherever it
is, an install's `WTF/Config.wtf` included: a write into an install outside
`guard` (L1). Here the link is planted by the same account, which shows the
mechanism on any POSIX system.

Constructed (L8 boundary case): a tiny tree in `tmp_path`; no real install.
Expected: a regular file with more than one link is left as it is (and may
still be reported by `exposure`); the positive control shows the walk still
fixes the store's own files.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from wowlab_core.snapshot import SnapshotStore

pytestmark = pytest.mark.skipif(os.name != "posix", reason="file modes are a POSIX matter")


@pytest.fixture(autouse=True)
def _umask() -> Iterator[None]:
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


def _older_store(tmp_path: Path) -> SnapshotStore:
    """Constructed: a store as an older version left it under umask 0000."""
    store = SnapshotStore(tmp_path / "store")
    store.put_object(b"constructed: seed\n")
    for path in [store.path, *store.path.rglob("*")]:
        path.chmod(0o777 if path.is_dir() else 0o644)
    return store


def test_positive_control_the_walk_fixes_the_stores_own_files_constructed(
    tmp_path: Path,
) -> None:
    store = _older_store(tmp_path)
    seed = next(p for p in (store.path / "objects").rglob("*") if p.is_file())
    store.put_object(b"constructed: next\n")
    assert stat.S_IMODE(seed.stat().st_mode) == 0o600


def test_a_hard_link_planted_in_the_store_leaves_the_install_file_alone_constructed(
    tmp_path: Path,
) -> None:
    wtf = tmp_path / "World of Warcraft" / "_constructed_" / "WTF"
    wtf.mkdir(parents=True)
    config = wtf / "Config.wtf"
    config.write_bytes(b'SET constructed "1"\n')
    config.chmod(0o644)
    store = _older_store(tmp_path)
    (store.path / "objects" / "zz").mkdir()
    os.link(config, store.path / "objects" / "zz" / "planted")

    store.put_object(b"constructed: next\n")

    assert stat.S_IMODE(config.stat().st_mode) == 0o644, "a store write changed an install file"
