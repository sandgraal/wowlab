# Probe from review of m11/24-sv-merge-loader-tests; reproduces an M11-24 Lab-write skip that ignores which file was written, or when, passing every M11-24T grader.
"""docs/LAB_PLAN.md §13.4, owner ruling for M11-24: "A comparison that spans a
committed guard write to **that** `WowLab.lua` ... is skipped." Two bounds are
in that sentence and no M11-24T grader pins either:

- *that* file: a guard write to another character's `WowLab.lua` must not
  skip the `--into` character's check. An implementation that matches the
  journal on the file name `WowLab.lua` passes all 21 M11-24T graders.
- *spans*: the write must fall between the two states compared. A pair of
  snapshots that both predate the write (the guard's own pre-write snapshot
  is the newer one) does not span it. An implementation that skips any
  comparison whose older side predates a Lab write, with no upper bound,
  passes all 21 M11-24T graders.

Either mistake turns a real loader failure into a pass with a note, which is
the one thing the loader check exists to catch. Both tests below are green on
main today (the refusal comes from the M11-09 check) and must stay green
after M11-24. Each has a positive control: the same sequence without the
Lab write is refused too, so the write is the only variable.

Constructed: the captured tree copied into `tmp_path`, with the first
character's real `WowLab.lua` edited to `loads` 1 or 5 (another session).
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
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    user_data,  # noqa: F401
)
from test_svmerge import (
    _LOADS_5,
    DBM,
    LAB_A,
    LAB_B,
    REAL_A,
    REAL_B,
    REAL_DBM,
    _copy_within,
    _once,
    _out,
    _snap,
)

_LOADS_1 = (b'["loads"] = 4,', b'["loads"] = 1,')
_B_EDIT = (b'["loads"] = 2,', b'["loads"] = 3,')


@pytest.mark.parametrize("other_write", [True, False], ids=["probe", "positive-control"])
def test_guard_write_to_another_characters_wowlab_lua_does_not_skip_the_check_constructed(
    flavor: Path, other_write: bool
) -> None:
    # Snapshot, then (probe) a `snap restore` of the SECOND character's
    # WowLab.lua through guard, whose pre-write snapshot holds the first's at 4.
    # Then a session of the first character that loaded nothing: 1 on disk.
    first = _snap("loads 4")
    if other_write:
        (flavor / LAB_B).write_bytes(_once(REAL_B, *_B_EDIT))
        ok("snap", "restore", first, "--paths", LAB_B, "--yes")
        assert (flavor / LAB_B).read_bytes() == REAL_B
    (flavor / LAB_A).write_bytes(_once(REAL_A, *_LOADS_1))
    result = _copy_within()
    assert result.exit_code == 3, _out(result)
    assert "refused by the loader check" in result.stderr
    assert "went down" in result.stderr
    assert (flavor / DBM).read_bytes() == REAL_DBM


@pytest.mark.parametrize("restore", [True, False], ids=["probe", "positive-control"])
def test_drop_between_snapshots_that_predate_a_restore_is_still_refused_constructed(
    flavor: Path, restore: bool
) -> None:
    # 5 in a snapshot, then a session that loaded nothing (1 on disk). Probe:
    # the owner restores the 5 through guard; its pre-write snapshot holds the
    # 1, so the pair 5 -> 1 shows the drop and both sides predate the write.
    # Only the disk against that snapshot spans the restore. Control: a plain
    # snapshot instead of the restore.
    (flavor / LAB_A).write_bytes(_once(REAL_A, *_LOADS_5))
    five = _snap("loads 5")
    (flavor / LAB_A).write_bytes(_once(REAL_A, *_LOADS_1))
    if restore:
        ok("snap", "restore", five, "--paths", LAB_A, "--yes")
        assert (flavor / LAB_A).read_bytes() == _once(REAL_A, *_LOADS_5)
    else:
        _snap("loads 1")
    result = _copy_within()
    assert result.exit_code == 3, _out(result)
    assert "refused by the loader check" in result.stderr
    assert "went down" in result.stderr
    assert (flavor / DBM).read_bytes() == REAL_DBM
