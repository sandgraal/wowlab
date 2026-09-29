# Probe from review of m11/18-snapshot-create-links; reproduces `create` failing under macOS's default 256-descriptor limit because it holds one directory descriptor per object shard, and blaming a real shard as "a link"
"""Fix round 1 of M11-18 (f1bea48) makes one `create` hold a directory
descriptor for the store root, `objects/`, `manifests/`, `tmp/` and every
object shard it touches (`_holding_dirs`, `_held_or_open`), closing them only
when `create` returns. There are 256 shards. A tree of a few thousand files
(one `Interface/AddOns/` easily) reaches nearly all of them, fresh or reused,
so one `create` needs about 260 directory descriptors on top of its own
files. macOS's default soft limit for a process started from Terminal or
Finder is 256 (`launchctl limit maxfiles` prints `256 unlimited`), so there
`create` runs out of descriptors: `wowlab snap create`, and every guard write
(its pre-write snapshot is a `create`), then fail on the owner's Mac. origin/main,
which opens and closes each chain per object, passes under the same limit
(checked by hand with the same tree: fresh 1.53 s, reuse 0.49 s).

Worse, `_open_child_dir` turns every `os.open` failure into
`_linked_store_dir`, so the `EMFILE` is reported as "objects/e4 is a link or
not a directory ... Move it aside so the store can make a real directory in
its place". Following that advice moves a real shard, and the objects that
snapshots refer to, out of the store.

Expected: `create` of such a tree succeeds under a 256 soft limit, and a
directory open that fails for a reason other than a link (here `EMFILE`) is
not reported as a link.

Constructed input (a generated tree; our own store format), POSIX only: the
`resource` module and directory descriptors. Everything lives in `tmp_path`;
nothing touches an install or the user data directory. The limit is lowered
in a child process only. Positive controls: the same tree succeeds with the
limit raised, and a small tree succeeds under 256, so the failure is the held
descriptors, not the tree or the limit alone.
"""

from __future__ import annotations

import errno
import os
import subprocess
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import snapshot
from wowlab_core.snapshot import SnapshotError, SnapshotStore

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not snapshot._WRITE_BY_DIR_FD,
    reason="POSIX: the resource module and directory descriptors",
)

MACOS_DEFAULT_SOFT_LIMIT = 256
FILES = 3000  # deterministic contents; reaches (nearly) every one of the 256 shards

CHILD = textwrap.dedent(
    """
    import resource, sys
    from datetime import UTC, datetime, timedelta
    from pathlib import Path
    from wowlab_core.snapshot import SnapshotStore

    limit, root, store = int(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    if hard != resource.RLIM_INFINITY:
        limit = min(limit, hard)
    resource.setrlimit(resource.RLIMIT_NOFILE, (limit, hard))
    t0 = datetime(2026, 9, 29, 12, tzinfo=UTC)
    s = SnapshotStore(store)
    s.create(root, ["WTF"], now=t0)                           # fresh: every object new
    s.create(root, ["WTF"], now=t0 + timedelta(seconds=1))    # every object reused
    print("ok")
    """
)


def _tree(root: Path, files: int) -> Path:
    for i in range(files):
        path = root / "WTF" / f"d{i % 40}" / f"f{i}.lua"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"constructed = {i}\n".encode() * 20)
    return root


def _create_under(limit: int, root: Path, store: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", CHILD, str(limit), str(root), str(store)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_constructed_positive_control_the_tree_snapshots_with_the_limit_raised(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path / "Install", FILES)
    done = _create_under(8192, root, tmp_path / "store")
    assert done.returncode == 0 and "ok" in done.stdout, done.stderr[-2000:]


def test_constructed_positive_control_a_small_tree_snapshots_under_256(tmp_path: Path) -> None:
    root = _tree(tmp_path / "Install", 20)
    done = _create_under(MACOS_DEFAULT_SOFT_LIMIT, root, tmp_path / "store")
    assert done.returncode == 0 and "ok" in done.stdout, done.stderr[-2000:]


def test_constructed_create_of_a_few_thousand_files_works_under_the_macos_default_limit(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path / "Install", FILES)
    done = _create_under(MACOS_DEFAULT_SOFT_LIMIT, root, tmp_path / "store")
    assert done.returncode == 0 and "ok" in done.stdout, (
        "create ran out of descriptors under the macOS default soft limit of 256:\n"
        + done.stderr[-2000:]
    )


def test_constructed_a_directory_open_failing_with_emfile_is_not_called_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path / "Install", 3)
    store = SnapshotStore(tmp_path / "store")
    store.create(root, ["WTF"], now=datetime(2026, 9, 29, 12, tzinfo=UTC))  # control: works

    real_open = os.open

    def emfile_on_child_dirs(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if kwargs.get("dir_fd") is not None and flags & os.O_DIRECTORY:
            raise OSError(errno.EMFILE, os.strerror(errno.EMFILE))
        return real_open(path, flags, *args, **kwargs)

    fresh = SnapshotStore(tmp_path / "store2")
    monkeypatch.setattr(os, "open", emfile_on_child_dirs)
    with pytest.raises(SnapshotError) as caught:
        fresh.create(root, ["WTF"], now=datetime(2026, 9, 29, 13, tzinfo=UTC))
    monkeypatch.undo()
    message = str(caught.value)
    assert "link" not in message and "Move it aside" not in message, (
        "an EMFILE on a real store directory is reported as a link, with advice to move "
        "the real directory aside:\n" + message
    )
