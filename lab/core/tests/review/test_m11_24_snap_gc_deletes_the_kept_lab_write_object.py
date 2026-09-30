# Probe from review of m11/24-sv-merge-loader; reproduces `wowlab snap gc` deleting the object an `sv merge` of WowLab.lua kept, so the next merge after a healthy login is refused under rule 3, and following the refusal's advice once does not clear it.
"""Constructed (L8: boundary case; an edited copy of the real M11-03
`WowLab.lua` stands for the session after the merge, and the kept object's
mtime is set back past the gc grace period in the temporary user data).

docs/LAB_PLAN.md §13.4, the 2026-09-29 amendment, rule 2: "Every Lab write to
`WowLab.lua` (`snap restore`, profile apply, `sv merge`) keeps the bytes it
wrote as a store object, so this object exists for any write made after
M11-24." `SnapshotStore.put_object` stores that object for `sv merge`, but no
manifest refers to it, and `wowlab snap gc` removes every object no manifest
refers to once it is an hour old (`GC_GRACE_SECONDS`). After a routine
`snap gc`, a healthy login (loads 4 -> 5) following an `sv merge` of the file
is refused as "W's result cannot be read" (rule 3), and
`--force-loader-check` becomes the only way through.

The refusal then advises "Take a snapshot, log in and out once ... and check
again"; after exactly that, the snapshot pair (guard's pre-write snapshot ->
the new one) still spans the merge and refuses on the same missing object.

Each positive control is the same sequence without `snap gc`: the merge
passes under rule 2 from the kept object. The only variable is the gc.

The user data directory is redirected into `tmp_path`. Nothing reads or
writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    user_data,  # noqa: F401
)
from test_svmerge import LAB_A, REAL_A, _copy_within, _once, _out, _snap
from test_svmerge_loader import _merge_gear_into_wowlab_lua

from wowlab_core import guard, snapshot


@pytest.mark.parametrize("gc", [True, False], ids=["probe", "positive-control"])
def test_snap_gc_keeps_the_object_an_sv_merge_of_wowlab_lua_left_constructed(
    flavor: Path, gc: bool
) -> None:
    _snap("loads 4")
    _merge_gear_into_wowlab_lua()
    merged = (flavor / LAB_A).read_bytes()
    assert merged != REAL_A
    (record,) = [
        r
        for r in guard.history()
        if r.state == "committed" and any(p.path == LAB_A for p in r.paths)
    ]
    (change,) = [p for p in record.paths if p.path == LAB_A]
    assert change.after is not None
    store = snapshot.SnapshotStore()
    kept = store.object_path(change.after)
    assert kept.is_file(), "the merge kept the bytes it wrote (rule 2)"
    # A healthy session since the merge: loads 4 -> 5.
    (flavor / LAB_A).write_bytes(_once(merged, b'["loads"] = 4,', b'["loads"] = 5,'))
    if gc:
        two_hours_ago = kept.stat().st_mtime - 7200
        os.utime(kept, (two_hours_ago, two_hours_ago))
        ok("snap", "gc", "--yes")
    result = _copy_within("--json")
    assert result.exit_code == 0, _out(result)
    assert kept.is_file(), "snap gc removed the object the next loader check compares from"


@pytest.mark.parametrize("gc", [True, False], ids=["probe", "positive-control"])
def test_following_the_rule3_advice_once_clears_the_refusal_constructed(
    flavor: Path, gc: bool
) -> None:
    # The rule-3 refusal says: "Take a snapshot (`wowlab snap create`), log in
    # and out once with the lab-addon enabled and check again". After that,
    # the disk comparison no longer spans the merge, but the snapshot pair
    # (guard's pre-write snapshot -> the new one) still does, so the same
    # missing object refuses again.
    _snap("loads 4")
    _merge_gear_into_wowlab_lua()
    merged = (flavor / LAB_A).read_bytes()
    (record,) = [
        r
        for r in guard.history()
        if r.state == "committed" and any(p.path == LAB_A for p in r.paths)
    ]
    (change,) = [p for p in record.paths if p.path == LAB_A]
    assert change.after is not None
    kept = snapshot.SnapshotStore().object_path(change.after)
    (flavor / LAB_A).write_bytes(_once(merged, b'["loads"] = 4,', b'["loads"] = 5,'))
    if gc:
        two_hours_ago = kept.stat().st_mtime - 7200
        os.utime(kept, (two_hours_ago, two_hours_ago))
        ok("snap", "gc", "--yes")
        first = _copy_within()
        assert first.exit_code == 3, _out(first)
        assert "Take a snapshot" in first.stderr
    # The advice: a snapshot, one healthy login (5 -> 6), check again.
    _snap("as the refusal advised")
    (flavor / LAB_A).write_bytes(_once(merged, b'["loads"] = 4,', b'["loads"] = 6,'))
    result = _copy_within()
    assert result.exit_code == 0, _out(result)
