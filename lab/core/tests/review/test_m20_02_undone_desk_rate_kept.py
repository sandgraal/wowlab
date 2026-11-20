# Probe from review of m20/02-crewdb-writes-terms; reproduces a rating set at the desk and then undone surviving a sync by hand in wowlab's crew database.
"""CONSTRUCTED books and databases (ADR-0036 term 3), built at run time.

§21.3: from a held book a sync by hand "replaces a rating or note only where
the database's own row has not changed since it last agreed with the saved
file". `crew rate` on a player in the saved file writes the file and the
row together, so the row agrees with the file at that moment; `Marking`
sets `marks_changed` and not `marks_agreed`, so after `undo` the hand sync
keeps the rating the player undid, and the next sync is `unchanged` (same
bytes as recorded), so the database and the game disagree from then on. A
normal sync in between does not repair it (`_same` ignores the agreement
time, so the row is not rewritten). The positive control: a rating set on a
player only the database keeps is kept by a held sync, as §21.3 requires.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))

from _crew_build import KEY_A, KEY_B, account_file, book, player
from test_crewdb_writes import _dropped, _three, make  # noqa: F401


def test_control_a_database_only_rating_survives_a_held_sync_constructed(make: Any) -> None:  # noqa: F811
    env = make(_three())
    _dropped(env)
    assert env.run("crew", "rate", KEY_B, "bad", "--yes").exit_code == 0
    env.gate(
        "restore 20261119-000000-abcd (WTF)",
        account_file(book({KEY_A: player(rating="bad", met=4), KEY_B: player(met=9)})),
    )
    assert env.sync().held == ["put_back"]
    assert env.run("crew", "sync", input="y\n").exit_code == 0
    assert env.rows()[KEY_B].rating == "bad"


def test_an_undone_desk_rate_is_not_kept_by_a_sync_by_hand_constructed(make: Any) -> None:  # noqa: F811
    env = make(_three())
    assert env.sync().state == "synced"
    assert env.run("crew", "rate", KEY_A, "good", "--yes").exit_code == 0
    assert env.run_bare("undo", "--yes").exit_code == 0  # the file says bad again
    assert env.sync().held == ["put_back"]
    assert env.run("crew", "sync", input="y\n").exit_code == 0
    assert env.rows()[KEY_A].rating == "bad"  # fails: 'good'
    assert env.sync().state == "unchanged"


def test_a_normal_sync_between_does_not_repair_it_constructed(make: Any) -> None:  # noqa: F811
    env = make(_three())
    env.sync()
    assert env.run("crew", "rate", KEY_A, "good", "--yes").exit_code == 0
    env.sync()  # the row and the file agree on good
    assert env.run_bare("undo", "--yes").exit_code == 0
    assert env.run("crew", "sync", input="y\n").exit_code == 0
    assert env.rows()[KEY_A].rating == "bad"  # fails: 'good'
