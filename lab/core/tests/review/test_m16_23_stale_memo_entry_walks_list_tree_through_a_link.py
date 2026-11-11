# Probe from review of m16/23-listing-memo; reproduces list_tree, fed guard's listing memo, descending through a directory swapped for a symlink within one directory-clock tick and reporting files outside the install.
"""`SnapshotStore.list_tree(..., entries=memo.entries)` decides whether to
descend into a child from the `os.DirEntry` the memo kept
(`child.is_dir(follow_symlinks=False)` answers from the type cached when the
parent was listed). When a subdirectory is replaced by a symlink after the
memo listed its parent, and the parent's (dev, ino, mtime_ns) is unchanged
(a coarse directory clock; here the mtime is put back by hand), the cached
entry still says "directory": the walk then lists the child with
`memo.entries`, whose key for a link is None, so it scandirs the path, which
follows the link. The names found at the link's target come back as files
under the install (what guard reports under `left_alone`, M16-11).

Without the memo, the same walk scandirs the parent afresh, sees the link,
and reports it as a `symlink`. Read-only: nothing is written into the
install. Constructed (L8): a tree under `tmp_path`; no real install.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wowlab_core import guard
from wowlab_core.snapshot import SnapshotStore


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_m16_23_stale_memo_entry_walks_list_tree_through_a_link(tmp_path: Path) -> None:
    root = tmp_path / "install"
    wtf = root / "_flavor_" / "WTF"
    sub = wtf / "Sub"
    sub.mkdir(parents=True)
    (wtf / "Config.wtf").write_bytes(b"SET a 1\n")
    (sub / "added.lua").write_bytes(b"-- constructed\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_bytes(b"constructed: not in the install\n")

    store = SnapshotStore(tmp_path / "store")
    memo = guard._ListingMemo()
    memo.entries(wtf)  # an earlier lookup in this transaction listed WTF

    before = os.lstat(wtf)
    (sub / "added.lua").unlink()
    sub.rmdir()
    sub.symlink_to(outside, target_is_directory=True)
    os.utime(wtf, ns=(before.st_atime_ns, before.st_mtime_ns))  # clock did not tick
    assert os.lstat(wtf).st_mtime_ns == before.st_mtime_ns

    fresh = dict(store.list_tree(root, ["_flavor_/WTF"]))
    assert fresh.get("_flavor_/WTF/Sub") == "symlink", fresh  # positive control

    memoized = dict(store.list_tree(root, ["_flavor_/WTF"], entries=memo.entries))
    assert "_flavor_/WTF/Sub/private.txt" not in memoized, (
        "list_tree, fed the memo, walked through the symlink WTF/Sub and listed a "
        f"file outside the install: {memoized}"
    )
