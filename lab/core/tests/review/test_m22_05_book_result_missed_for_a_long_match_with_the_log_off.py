# Probe from review of m22/05-live-companion; reproduces a battleground longer than 15 min, logged off inside (the lab-addon's default), never getting the crew book's result
"""M22-05: the match recap takes the result from the crew book's match on
the same map "from 120 s before the stay to 900 s after its last line"
(`docs/LAB_PLAN.md` §21.3 as built). The book's `at` is `time()` when the
addon reads the scoreboard, at the match's end (§21.2).

The lab-addon turns the combat log off in an instance (M20-19), which the
view itself names as the normal case. The battleground's stay then holds
its zone line and nothing else, so `_recap`'s `end` (`tally.last`, the last
line inside the stay) is the stay's start: the window closes 900 s after
the player entered, though the log shows when they left (the next zone
line, or a newer log). A battleground of more than 15 minutes, saved with
/reload, still says "Result: not in the combat log; the crew book saves it
at your next /reload or logout".

CONSTRUCTED log (`parser/_world_lines.py`, the real log's line shapes) and
an invented map. Positive control: the same stay with the book's match
13 min in is found.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))

from _world_lines import log, stamp, zone

from wowlab_core import crewbook, live, worldpvp

OWNERS: dict[str, str | None] = {"Player-9999-00000001": None}
BG = 489
BG_NAME = "Invented Test Valley"
BASE = worldpvp.seconds_of(stamp(0))
assert BASE is not None


def _recap(tmp_path: Path, book_at: int) -> live.MatchRecap | None:
    path = tmp_path / "WoWCombatLog-040126_021630.txt"
    # Into the battleground at +100 s; out of it 22 min later; nothing logged inside.
    path.write_bytes(log(zone(100, instance=BG, name=BG_NAME, difficulty=0), zone(1420)))
    match = crewbook.CrewMatch(
        position=1, at=BASE + book_at, map=BG_NAME, map_id=BG, kind="bg", won=True, team=[], foes=[]
    )
    return live.match_recap([path], OWNERS, pvp_maps=[BG], book=[match])


def test_positive_control_a_short_match_gets_its_result(tmp_path: Path) -> None:
    recap = _recap(tmp_path, 100 + 780)
    assert recap is not None and recap.ended_by == "left"
    assert (recap.result, recap.result_from) == ("won", "crew_book")


def test_a_22_minute_match_logged_off_inside_gets_its_result(tmp_path: Path) -> None:
    recap = _recap(tmp_path, 100 + 1300)  # the scoreboard, 20 s before leaving
    assert recap is not None and recap.ended_by == "left"
    assert (recap.result, recap.result_from) == ("won", "crew_book"), (recap.end, recap.result)
