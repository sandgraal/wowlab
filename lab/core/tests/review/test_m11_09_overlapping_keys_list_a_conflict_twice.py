# Probe from review of m11/09-sv-merge; reproduces one conflict listed twice when two three-way --key subtrees overlap.
"""`svmerge.merge` runs the three-way rules once per `--key`, each pass on the
document the previous pass produced, and appends every conflict it meets.
When two keys overlap (`--key A --key A.p`, or the same key given twice), a
conflict inside the overlap is appended once per pass, so `conflicts` (and
the CLI report, "2 conflict(s), unresolved") names one leaf twice.
docs/LAB_PLAN.md §13.4: a conflict is a leaf keyed by its Lua key path; one
leaf is one conflict.

Positive control: one `--key` lists the conflict once.

Constructed: three small documents; no capture has a common base.
"""

from __future__ import annotations

import pytest

from wowlab_core import luadata, svmerge

BASE = b'\nA = {\n["p"] = {\n["x"] = 1,\n},\n}\n'
OURS = b'\nA = {\n["p"] = {\n["x"] = 2,\n},\n}\n'
THEIRS = b'\nA = {\n["p"] = {\n["x"] = 3,\n},\n}\n'


def _conflicts(keys: list[str]) -> list[str]:
    result = svmerge.merge(
        luadata.parse(OURS),
        luadata.parse(THEIRS),
        base=luadata.parse(BASE),
        keys=keys,
    )
    return [c.path for c in result.conflicts]


def test_positive_control_one_key_lists_the_conflict_once_constructed() -> None:
    assert _conflicts(["A.p"]) == ['A["p"]["x"]']


@pytest.mark.parametrize("keys", [["A", "A.p"], ["A.p", "A.p"]], ids=["nested", "repeated"])
def test_overlapping_keys_list_the_conflict_once_constructed(keys: list[str]) -> None:
    assert _conflicts(keys) == ['A["p"]["x"]']
