# Probe from review of m10/10-snapshot-store; reproduces create() following a
# symlink that is a component of a subtree path and recording files from
# outside the root as if they were inside it.
"""`create()` documents "Symlinks are recorded with their target and never
followed". That holds for a symlink met during the walk, and for a subtree
that is itself a symlink. It does not hold for a symlink in the middle of a
subtree path: `root / "link/sub"` is lstat'ed, which follows `link`, and the
walk then runs outside the root. The manifest names `link/sub/secret.txt`
as a regular file under the root, which it is not.

Every tree here is constructed in `tmp_path`.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wowlab_core.snapshot import SnapshotError, SnapshotStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)


def test_a_symlinked_component_of_a_subtree_is_not_followed_constructed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    (outside / "sub").mkdir(parents=True)
    (outside / "sub" / "secret.txt").write_bytes(b"outside")
    source = tmp_path / "src"
    (source / "WTF").mkdir(parents=True)
    (source / "WTF" / "a.txt").write_bytes(b"x")
    (source / "link").symlink_to(outside)
    store = SnapshotStore(tmp_path / "store")

    # Positive control: met during a walk, the same link is recorded, not followed.
    walked = store.create(source, ["."], now=NOW)
    assert [(e.path, e.kind) for e in walked.entries] == [
        ("WTF/a.txt", "file"),
        ("link", "symlink"),
    ]
    # Positive control: named as the subtree itself, it is recorded, not followed.
    named = store.create(source, ["link"], now=NOW)
    assert [(e.path, e.kind) for e in named.entries] == [("link", "symlink")]

    # The defect: named as a parent component, it is followed.
    try:
        through = store.create(source, ["link/sub"], now=NOW)
    except SnapshotError:
        return  # refusing is a fine fix
    assert [e.path for e in through.entries if e.kind == "file"] == [], (
        "captured files from outside the root through a symlinked path component"
    )
