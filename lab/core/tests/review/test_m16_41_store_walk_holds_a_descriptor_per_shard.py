# Probe from security review of m16/41-store-at-rest; reproduces the mode walk opening every non-private child directory at once, so an older store is left part-fixed under macOS's default 256-descriptor limit.
"""`_privatize_below` pushes a descriptor for every non-private directory
below a directory before it walks any of them: an older store (every
directory 0755) with its 256 `objects/` shards holds about 260 descriptors at
once, more than macOS's default soft limit of 256. Opens past the limit fail
with EMFILE and are skipped silently; the parent has already been set
private, so the next write never walks into it again and those shards and
their objects keep their group and other bits for good. Here the limit is
set to 64 in a child process to show it. The walk should hold a bounded
number of descriptors (open each child as it is walked, not all at once),
and a directory it fixes should have its children walked even when they
cannot all be reached in one pass. Constructed tree; no install is read."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from wowlab_core.snapshot import SnapshotStore

pytestmark = pytest.mark.skipif(
    os.name != "posix" or sys.platform == "win32", reason="file modes are a POSIX matter"
)

_CHILD = """
import os, resource, sys
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
from wowlab_core.snapshot import SnapshotStore
SnapshotStore(sys.argv[1]).put_object(b"constructed: bytes new to the store\\n")
"""


def test_one_write_fixes_an_older_store_under_a_small_descriptor_limit_constructed(
    tmp_path: Path,
) -> None:
    old_mask = os.umask(0o022)
    try:
        src = tmp_path / "src" / "WTF"
        src.mkdir(parents=True)
        (src / "Config.wtf").write_bytes(b'SET constructed "1"\n')
        store = SnapshotStore(tmp_path / "store")
        store.create(tmp_path / "src", ["WTF"], label="constructed")
        for i in range(256):
            shard = store.path / "objects" / f"{i:02x}"
            shard.mkdir(exist_ok=True)
            (shard / "constructed").write_bytes(b"constructed\n")
        for path in [store.path, *store.path.rglob("*")]:
            path.chmod(0o755 if path.is_dir() else 0o644)
    finally:
        os.umask(old_mask)

    subprocess.run([sys.executable, "-c", _CHILD, str(store.path)], check=True)

    left = [
        p.relative_to(store.path).as_posix()
        for p in store.path.rglob("*")
        if not p.is_symlink() and stat.S_IMODE(p.lstat().st_mode) & 0o077
    ]
    assert left == [], f"{len(left)} entries keep group or other bits, e.g. {left[:3]}"
