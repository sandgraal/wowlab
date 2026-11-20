# Probe from review of m20/09-matches-desk; reproduces a player's entry left unread for one sync having its counters counted twice when it is read again
"""CONSTRUCTED INPUT: the four invented keys only, a book built at run time
inside a copy of the captured install under `tmp_path`, the database under a
user data directory redirected into `tmp_path` (as `test_crew_matches.py`).

§21.3 rule 2 adds the difference from the value the saved file showed at the
last sync, and adds the whole value only when the saved file's value is
lower ("the game dropped the player and started again"). The ruling of
2026-11-19 says an entry a sync cannot take is "not synced". But
`_changes` treats an unread entry as a player the saved file no longer
holds: `_left` sets every last-shown value to 0, so when the same entry is
readable again the whole count is added a second time. M20-09 extends this
to the four match counters and adds three new ways to leave an entry unread
(a counter over the bound, `pvp_last` out of range, `pvp_map` over 256
bytes); the same happened to `met` before it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))

import test_crew_matches
from _crew_build import KEY_A
from test_crew_matches import _four

make = test_crew_matches.make  # the fixture, by name

COUNTS = {"allied": 3, "faced": 2, "allied_won": 1, "faced_won": 1}


def test_positive_control_the_same_counts_synced_again_add_nothing_constructed(
    make: Any,
) -> None:
    env = make(_four(**COUNTS))
    env.sync()
    env.save(_four(**COUNTS, pvp_map="Invented Other Valley"))
    env.sync()
    a = env.rows()[KEY_A]
    assert (a.allied, a.faced, a.met) == (3, 2, 3)


def test_an_entry_unread_for_one_sync_is_not_counted_twice_constructed(make: Any) -> None:
    env = make(_four(**COUNTS))
    env.sync()
    # One save whose entry for KEY_A a sync does not take (a map name over
    # the bound): the entry is left out, its counts unchanged.
    env.save(_four(**COUNTS, pvp_map="x" * 300))
    assert env.sync().left_out == 1
    # The next save is readable again, with the very same counts.
    env.save(_four(**COUNTS))
    env.sync()
    a = env.rows()[KEY_A]
    assert (a.allied, a.faced, a.met) == (3, 2, 3), (a.allied, a.faced, a.met)
