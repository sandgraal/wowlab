# Probe from review of m20/02-crewdb-writes-terms; reproduces a note written and then cleared in game kept in the crew database because the row holds a desk rating.
"""CONSTRUCTED books and databases (ADR-0036 term 3), built at run time.

§21.3, ruled 2026-11-20: in a normal sync the database keeps "its own
rating (and, separately, its own note)" where the returning entry has none.
`_merged` decides "own" for the row as a whole (`marks_changed >
marks_agreed`), not per field: a player given a rating at the desk while
dropped returns with a note written in game; when the player later clears
that note in game, the sync keeps it, though the database never had a note
of its own, and says nothing (`notes_kept` counts only a return). The
positive control: the desk rating itself is kept on the return.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))

from _crew_build import KEY_A, KEY_B, NOTE, book, player
from test_crewdb_writes import _dropped, _three, make  # noqa: F401

IN_GAME = "invented note: written in game"


def _return_with_a_note(env: Any) -> None:
    _dropped(env)
    assert env.run("crew", "rate", KEY_B, "bad", "--yes").exit_code == 0  # the database alone
    env.save(
        book({KEY_A: player(rating="bad", note=NOTE, met=4), KEY_B: player(met=10, note=IN_GAME)})
    )
    assert env.sync().state == "synced"


def test_control_the_desk_rating_is_kept_on_return_constructed(make: Any) -> None:  # noqa: F811
    env = make(_three())
    _return_with_a_note(env)
    row = env.rows()[KEY_B]
    assert (row.rating, row.note, row.in_game_list) == ("bad", IN_GAME, True)


def test_a_note_cleared_in_game_is_not_kept_constructed(make: Any) -> None:  # noqa: F811
    env = make(_three())
    _return_with_a_note(env)
    env.save(book({KEY_A: player(rating="bad", note=NOTE, met=4), KEY_B: player(met=11)}))
    assert env.sync().state == "synced"
    assert env.rows()[KEY_B].rating == "bad"  # the desk rating, still kept
    assert env.rows()[KEY_B].note is None  # fails: the in-game note is kept
