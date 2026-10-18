# Probe from review of m13/08-binds-coach; reproduces [coach] values in a player's cost table accepting a boolean, a string and an out-of-range cost.
"""`bindscoach.parse_costs` checks every `[keys]` and `[modifiers]` value with
`_cost` ("a cost is a whole number from 0 to 1000"): a boolean, a string or
1001 is refused, naming the key (the positive control). The four `[coach]`
values go to Pydantic's lax mode instead, so `high_cost = true` silently
becomes a threshold of 1, `high_cost = "4"` and `frequent_share = "0.1"` are
accepted as numbers, and `unlisted` (the cost of every key not listed, so a
cost like any other) takes 2000. A player's typo changes every report with
no error. Expected: the same refusal, naming the `[coach]` key.

Constructed cost-table text (boundary inputs, L8), merged over the shipped
table read from the package; nothing reads an install or the user config
folder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import bindscoach


def _base() -> bindscoach.CostTable:
    return bindscoach.parse_costs(Path(bindscoach.shipped_costs_path()).read_text(encoding="utf-8"))


@pytest.mark.parametrize("text", ["[keys]\nq = true", '[keys]\nq = "1"', "[keys]\nq = 1001"])
def test_positive_control_a_key_cost_is_checked_constructed(text: str) -> None:
    with pytest.raises(bindscoach.CostTableError, match=r"\[keys\] q"):
        bindscoach.parse_costs(text, _base())


@pytest.mark.parametrize(
    "text",
    [
        "[coach]\nhigh_cost = true",
        '[coach]\nhigh_cost = "4"',
        '[coach]\nfrequent_share = "0.1"',
        "[coach]\nfrequent_min_casts = true",
        "[coach]\nunlisted = 2000",
    ],
)
def test_a_coach_value_is_checked_like_a_cost_constructed(text: str) -> None:
    with pytest.raises(bindscoach.CostTableError, match=r"\[coach\]"):
        bindscoach.parse_costs(text, _base())
