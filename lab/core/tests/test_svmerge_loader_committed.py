"""`wowlab sv merge` loader check: only a *committed* guard write skips a
comparison (M11-24, implementer's test).

docs/LAB_PLAN.md §13.4, owner ruling for M11-24: "A comparison that spans a
committed guard write to that `WowLab.lua` ... is skipped." No M11-24T grader
pins the word "committed". A transaction that rolled back left the file as it
was, so the client, not the Lab, is the only thing that can have lowered the
counter since; skipping on it would turn a real loader failure into a pass.

Constructed: the captured tree copied into `tmp_path` (as `test_cli.py`
builds it), the first character's real `WowLab.lua` edited to `loads` 1 (a
session that loaded nothing), and a guard transaction that writes that edit
and then raises, so guard rolls it back and journals `rolled_back`. The user
data directory is redirected into `tmp_path`. Nothing reads or writes a real
install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

from pathlib import Path

import pytest
from test_cli import (
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    user_data,  # noqa: F401
)
from test_svmerge import DBM, LAB_A, REAL_A, REAL_DBM, _copy_within, _once, _out, _report, _snap

from wowlab_core import guard, install

LAB_NOTE = "the Lab wrote WowLab.lua after that snapshot; the loader was not re-checked"
_LOADS_1 = (b'["loads"] = 4,', b'["loads"] = 1,')


class _AbortError(Exception):
    pass


def _guard_write_of_loads_1(root: Path, *, roll_back: bool) -> None:
    (discovered,) = install.read_install(root).flavors
    if roll_back:
        with pytest.raises(_AbortError), guard.transaction(discovered, label="constructed") as tx:
            tx.write(LAB_A, _once(REAL_A, *_LOADS_1))
            raise _AbortError
    else:
        with guard.transaction(discovered, label="constructed") as tx:
            tx.write(LAB_A, _once(REAL_A, *_LOADS_1))


@pytest.mark.parametrize("roll_back", [True, False], ids=["rolled-back", "committed-control"])
def test_only_a_committed_guard_write_skips_the_disk_comparison_constructed(
    root: Path, flavor: Path, roll_back: bool
) -> None:
    # Snapshot at 4; a guard write of the file with loads 1, rolled back or
    # committed; then the disk holds loads 1 (rolled back: the client wrote
    # it, a session that loaded nothing). The disk against the guard's
    # pre-write snapshot (4) spans that record either way.
    _snap("loads 4")
    _guard_write_of_loads_1(root, roll_back=roll_back)
    record = guard.history()[-1]
    (change,) = record.paths
    assert change.path == LAB_A and change.before != change.after, "the record names the file"
    if roll_back:
        assert record.state == "rolled_back"
        assert (flavor / LAB_A).read_bytes() == REAL_A
        (flavor / LAB_A).write_bytes(_once(REAL_A, *_LOADS_1))
    else:
        assert record.state == "committed"
    assert (flavor / LAB_A).read_bytes() == _once(REAL_A, *_LOADS_1)
    result = _copy_within("--json")
    if roll_back:
        assert result.exit_code == 3, _out(result)
        assert "refused by the loader check" in result.stderr
        assert "went down" in result.stderr
        assert LAB_NOTE not in _out(result)
        assert (flavor / DBM).read_bytes() == REAL_DBM
    else:
        assert result.exit_code == 0, _out(result)
        assert any(LAB_NOTE in note for note in _report(result)["notes"])
        assert (flavor / DBM).read_bytes() != REAL_DBM
