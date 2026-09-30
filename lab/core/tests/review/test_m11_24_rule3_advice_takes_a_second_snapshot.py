# Probe from review of m11/24-sv-merge-loader; reproduces a rule-3 refusal whose advice (one snapshot, one login) does not clear it: the snapshot pair still spans the Lab write, so a second snapshot is needed.
"""Constructed (L8: boundary case; edited copies of the real M11-03
`WowLab.lua` stand for each session after the merge, and the object the merge
kept is deleted in the temporary user data, as the rule-3 graders do).

docs/LAB_PLAN.md §13.4, the 2026-09-29 amendment, rule 3: when W's result
cannot be read the merge is refused, naming the journal record. At e38e8fb
the refusal advised "Take a snapshot (`wowlab snap create`), log in and out
once with the lab-addon enabled and check again". After exactly that, the
disk comparison no longer spans the merge, but the snapshot pair (guard's
pre-write snapshot -> the new one) still does, and refuses on the same
unreadable object. Only a second snapshot clears it.

This replaces `test_following_the_rule3_advice_once_clears_the_refusal_constructed`
in `test_m11_24_snap_gc_deletes_the_kept_lab_write_object.py`, which reached
rule 3 through `snap gc`; since 6281e9e gc keeps the object (as that file's
first test requires), so that test's setup no longer reaches rule 3.

Probe: the refusal gives the advice that works, one snapshot plus one login
still refuses, and a second snapshot clears it. Positive control: the object
is kept, so one snapshot and one login pass under rule 2.

The user data directory is redirected into `tmp_path`. Nothing reads or
writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    user_data,  # noqa: F401
)
from test_svmerge import LAB_A, REAL_A, _copy_within, _once, _out, _snap
from test_svmerge_loader import _merge_gear_into_wowlab_lua

from wowlab_core import guard, snapshot

ADVICE = (
    "Take a snapshot now (wowlab snap create), log in and out once with the lab-addon "
    "enabled, take another snapshot, then check again; or pass --force-loader-check if you "
    "are sure the last session loaded."
)


def _session(flavor: Path, merged: bytes, loads: int) -> None:
    """Constructed: a healthy session raised `probe.loads` to `loads`."""
    new = f'["loads"] = {loads},'.encode()
    (flavor / LAB_A).write_bytes(_once(merged, b'["loads"] = 4,', new))


@pytest.mark.parametrize("unreadable", [True, False], ids=["probe", "positive-control"])
def test_the_rule3_advice_takes_a_second_snapshot_constructed(
    flavor: Path, unreadable: bool
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
    kept = snapshot.SnapshotStore().object_path(change.after)
    assert kept.is_file(), "the merge kept the bytes it wrote (rule 2)"
    _session(flavor, merged, 5)
    if unreadable:
        kept.unlink()  # W's result cannot be read (rule 3)
        first = _copy_within()
        assert first.exit_code == 3, _out(first)
        assert "refused by the loader check" in first.stderr
        assert record.id in first.stderr, "the refusal names the journal record"
        assert ADVICE in " ".join(first.stderr.split()), first.stderr
    # The advice, first half: one snapshot now, then one healthy login (5 -> 6).
    _snap("a snapshot now")
    _session(flavor, merged, 6)
    once = _copy_within()
    if not unreadable:
        assert once.exit_code == 0, _out(once)
        return
    # The pair (guard's pre-write snapshot -> this one) still spans the merge,
    # and W's result still cannot be read.
    assert once.exit_code == 3, _out(once)
    assert "refused by the loader check" in once.stderr
    # The advice, second half: another snapshot. The pair no longer spans it.
    _snap("another snapshot")
    again = _copy_within()
    assert again.exit_code == 0, _out(again)
