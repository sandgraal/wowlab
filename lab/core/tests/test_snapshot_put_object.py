"""`SnapshotStore.put_object` when a directory sits at the object's path
(M11-31 (b), from the M11-24 reviews).

Constructed (L8: hostile and boundary cases of the store, which is our own
format): a directory planted at the path where `put_object` stores an
object. Before M11-31 the rename onto it raised a raw `IsADirectoryError`;
every `OSError` on the way is now a `SnapshotError` that names the object's
path and what is in its place, and nothing is left behind.

The CLI case is an `sv merge` of a character's `WowLab.lua`, which keeps the
bytes it writes with `put_object` inside the guard transaction (§13.4). It
runs on the real M11-03 capture (`fixtures/macos/forever`) copied into
`tmp_path` as `test_cli.py` builds it; the planted directory is the
constructed part. The user data directory (store, journal, locks) is
redirected into `tmp_path` and the process table is a fake. Nothing reads or
writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from test_cli import (
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)
from test_svmerge import CHAR_A, CHAR_B, LAB, LAB_A, REAL_A, _out

from wowlab_core import cli, guard, snapshot
from wowlab_core.snapshot import SnapshotError, SnapshotStore

DATA = b"constructed: the bytes put_object is asked to store\n"
DIGEST = hashlib.sha256(DATA).hexdigest()
INNER = b"constructed: a file inside the planted directory\n"


def _plant(store: SnapshotStore, *, filled: bool) -> Path:
    """Constructed: a directory where `put_object` stores `DATA`."""
    planted = store.object_path(DIGEST)
    planted.mkdir(parents=True)
    if filled:
        (planted / "inner").write_bytes(INNER)
    return planted


@pytest.mark.parametrize("filled", [False, True], ids=["empty", "holding-a-file"])
def test_put_object_onto_a_planted_directory_raises_snapshot_error_naming_the_path_constructed(
    tmp_path: Path, filled: bool
) -> None:
    store = SnapshotStore(tmp_path / "store")
    planted = _plant(store, filled=filled)

    with pytest.raises(SnapshotError) as caught:
        store.put_object(DATA)

    assert not isinstance(caught.value, OSError), "not the raw OSError"
    assert isinstance(caught.value.__cause__, OSError), "the OSError is chained"
    message = str(caught.value)
    assert f"cannot store object {DIGEST} at {planted}" in message, message
    assert "a directory is in its place" in message, message
    assert "move it out of the store" in message, message
    # The directory is left as it was, and the staged copy is gone.
    assert planted.is_dir() and not planted.is_symlink()
    assert {p.name: p.read_bytes() for p in planted.iterdir()} == (
        {"inner": INNER} if filled else {}
    )
    assert [p.name for p in planted.parent.iterdir()] == [planted.name]
    assert list((store.path / "tmp").iterdir()) == [], "no staged file is left in tmp/"


def test_put_object_stores_the_object_once_the_directory_is_moved_aside_constructed(
    tmp_path: Path,
) -> None:
    store = SnapshotStore(tmp_path / "store")
    planted = _plant(store, filled=True)
    with pytest.raises(SnapshotError):
        store.put_object(DATA)

    planted.rename(tmp_path / "aside")  # what the message asks the owner to do
    assert store.put_object(DATA) == DIGEST
    assert store.object_path(DIGEST).is_file()
    assert store.read_object(DIGEST, size=len(DATA)) == DATA
    assert (tmp_path / "aside" / "inner").read_bytes() == INNER


def _merge_gear() -> object:
    """An `sv merge` of the first character's `WowLab.lua` (the gear block
    from the second character): a write that keeps its bytes with
    `put_object`."""
    return run(
        "sv",
        "merge",
        LAB,
        "--from",
        CHAR_B,
        "--into",
        CHAR_A,
        "--key",
        "WowLabCharDB.gear",
        "--yes",
    )


def test_sv_merge_of_wowlab_lua_onto_a_planted_object_directory_exits_with_a_clear_error_constructed(
    flavor: Path,
) -> None:
    # A merge learns the bytes the next identical merge will keep; `undo`
    # puts the real file back, and a directory is planted where those bytes go.
    first = _merge_gear()
    assert first.exit_code == 0, _out(first)
    (change,) = [c for c in guard.history()[-1].paths if c.path == LAB_A]
    assert change.after == hashlib.sha256((flavor / LAB_A).read_bytes()).hexdigest()
    ok("undo", "--yes")
    assert (flavor / LAB_A).read_bytes() == REAL_A
    store = snapshot.SnapshotStore()
    planted = store.object_path(change.after)
    planted.unlink()
    planted.mkdir()  # constructed
    records = len(guard.history())

    result = _merge_gear()

    assert result.exit_code == cli.EXIT_ERROR, _out(result)
    assert isinstance(result.exception, SystemExit), result.exception
    err = " ".join(str(result.stderr).split())
    assert f"cannot keep the merged {LAB_A} in the snapshot store" in err, err
    assert "a directory is in its place" in err, err
    assert "nothing was written" in err, err
    assert (flavor / LAB_A).read_bytes() == REAL_A, "nothing was written"
    assert planted.is_dir() and not any(planted.iterdir())
    assert all(r.state != "committed" for r in guard.history()[records:]), "no committed write"
