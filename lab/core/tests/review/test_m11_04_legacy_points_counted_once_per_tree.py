# Probe from review of m11/04-labaddon-reader; reproduces `legacy_headline` adding one trait currency's points once per tree that lists it (5 available shown as 15)
"""In the M11-03 capture the Legacy candidate config (type 3) has four trees;
three of them (1187, 1188, 1189) each list trait currency 4225 from
`C_Traits.GetTreeCurrencyInfo(configID, treeID)`. That is one pool per
config reported under every tree that spends it, not three pools.
`legacy_headline` sums `quantity` and `spent` over every tree, so the
headline multiplies the pool by the number of trees. Below level 25 every
value is 0, which hides it; at 25 and above (§13.1: "the Legacy points spent
and the cap") a character with 5 unspent points would read "15 points
available", and 3 spent would read "9 points spent".

The expectation is implementation-neutral: the headline must not change with
the number of trees that list the same currency id.

Constructed (L8, boundary): the real first-character capture (M11-03, index
row in `fixtures/README.md`) read through `luadata`, with currency 4225's
numbers changed to what a character past the unlock could report. Positive
control: the capture as committed reads "0 points available".
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import labaddon, luadata

pytestmark = pytest.mark.parser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FIRST = "macos/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua"


def _raw() -> dict[str, Any]:
    value = luadata.parse((FIXTURES / FIRST).read_bytes()).to_python()["WowLabCharDB"]
    assert isinstance(value, dict)
    return copy.deepcopy(value)


def _headline(raw: dict[str, Any]) -> str:
    talents = labaddon.load_char(raw).talents
    assert talents is not None and isinstance(talents.legacy, labaddon.LegacyTalents)
    return labaddon.legacy_headline(talents.legacy, class_config=None)


def _listing_trees(raw: dict[str, Any]) -> list[dict[str, Any]]:
    trees = raw["talents"]["legacy"]["configs"][0]["trees"]
    listing = [t for t in trees if isinstance(t["currencies"], list) and t["currencies"]]
    assert [c["id"] for t in listing for c in t["currencies"]] == [4225, 4225, 4225]
    return listing


def test_positive_control_capture_reads_zero_available() -> None:
    raw = _raw()
    _listing_trees(raw)
    assert _headline(raw) == "Legacy candidates: present, nothing spent, 0 points available"


@pytest.mark.parametrize(("quantity", "spent"), [(5, 0), (2, 3)])
def test_constructed_one_pool_listed_on_three_trees_is_counted_once(
    quantity: int, spent: int
) -> None:
    on_three = _raw()
    for tree in _listing_trees(on_three):
        tree["currencies"][0].update({"quantity": quantity, "spent": spent, "max_quantity": 5})
    on_one = copy.deepcopy(on_three)
    for tree in _listing_trees(on_one)[1:]:
        tree["currencies"] = {}
    assert _headline(on_three) == _headline(on_one)
    assert f"{quantity * 3} points available" not in _headline(on_three)
