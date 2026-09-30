# Probe from review of m11/24-sv-merge-loader-tests-2; reproduces ungraded holes: a Lab write outside the comparison's span, or one that did not commit, taken as W
"""Constructed (L8: boundary cases; edited copies of the real M11-03
`WowLab.lua`, one journal record's state edited in the temporary user data).

docs/LAB_PLAN.md §13.4, the 2026-09-29 amendment: W is the latest
*committed* guard write to that `WowLab.lua` *in the span* of a comparison.
A write before the older side, or a record that did not commit, is not W.

Each case here is green on main (the M11-09 rules refuse the drop) and on a
correct M11-24. Found by mutation of the M11-24T2 test-writer's scratch
implementation: with W taken from every journalled write (span ignored), or
from uncommitted records too, all 77 graders in test_svmerge.py,
test_svmerge_loader.py and test_svmerge_lab_write.py still pass, and the
failed session below passes under rule 1, because it wrote the same bytes a
Lab write once wrote (a restore of a first-login `loads = 1` file).
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (  # noqa: F401  (fixtures requested by name)
    flavor,
    idle,
    replayed,
    root,
    user_data,
)
from test_svmerge import _copy_within, _snap
from test_svmerge_lab_write import (
    _a,
    _after,
    _disk,
    _journal_file,
    _last_lab_write,
    _sha,
    _store,
)
from test_svmerge_loader import _frozen, _restore_lab

from wowlab_core import guard


def _restore_a_first_login(flavor: Path) -> guard.HistoryRecord:
    """A first login (loads 1) snapshotted; a login raises it to 2; the owner
    restores the loads-1 bytes (W, committed; its after is those bytes)."""
    _disk(flavor).write_bytes(_a(1))
    first = _snap("loads 1")
    _disk(flavor).write_bytes(_a(2))
    _restore_lab(first)
    record = _last_lab_write()
    assert _after(record) == _sha(_a(1))
    return record


def _refused_went_down(root: Path, user_data: Path) -> None:
    before = _frozen(root, user_data)
    result = _copy_within()
    assert result.exit_code == 3, result.stdout + result.stderr
    assert "refused by the loader check" in result.stderr
    assert "went down" in result.stderr
    assert _frozen(root, user_data) == before


@pytest.mark.parametrize(
    "pair", [False, True], ids=["disk-side-constructed", "pair-side-constructed"]
)
def test_lab_write_before_the_older_side_is_not_w_constructed(
    root: Path, flavor: Path, user_data: Path, pair: bool
) -> None:
    # W is older than the "loads 2" snapshot, so no comparison below spans it:
    # the disk (1) against that snapshot (2), or the pair 2 -> 1, went down.
    _restore_a_first_login(flavor)
    _disk(flavor).write_bytes(_a(2))
    _snap("loads 2")
    _disk(flavor).write_bytes(_a(1))  # a session that loaded nothing
    if pair:
        _snap("after the failed session")
    _refused_went_down(root, user_data)


def test_rolled_back_lab_write_is_not_w_constructed(
    root: Path, flavor: Path, user_data: Path
) -> None:
    # A restore of the loads-1 bytes over loads 2 whose record did not commit
    # (constructed: the committed record's state edited to rolled_back). Guard's
    # pre-write snapshot holds the 2; a failed session then writes loads 1,
    # which is also the rolled-back record's `after`.
    record = _restore_a_first_login(flavor)
    path = _journal_file(user_data, record.id)
    raw: dict[str, Any] = json.loads(path.read_text("ascii"))
    raw["state"] = "rolled_back"
    path.write_text(json.dumps(raw, ensure_ascii=True, sort_keys=True, indent=1) + "\n", "ascii")
    assert [r.state for r in guard.history() if r.id == record.id] == ["rolled_back"]
    _disk(flavor).write_bytes(_a(1))
    _refused_went_down(root, user_data)


def test_positive_control_the_trap_is_set_constructed(flavor: Path) -> None:
    # The cases above test something only if the failed session's bytes equal
    # a journalled Lab write's `after` and that write is older than the newest
    # snapshot: exactly what a W chosen outside the span would match.
    record = _restore_a_first_login(flavor)
    _disk(flavor).write_bytes(_a(2))
    later = _snap("loads 2")
    _disk(flavor).write_bytes(_a(1))
    assert record.state == "committed"
    assert record.created_at < _store().show(later).created_at
    assert _sha(_disk(flavor).read_bytes()) == _after(record)
