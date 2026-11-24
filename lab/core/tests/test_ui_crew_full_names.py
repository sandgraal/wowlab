"""The Crew tab carries each player's names and region and where they came
from (M21-12): the rows the page asks for (`crew list --json`) hold
`first_name`, `second_name`, `region` and their sources, and the player the
page opens (`crew show --json`) shows the "Name in game" and region lines,
as text. The page itself is unchanged: it shows `crew show`'s lines as it
always has.

CONSTRUCTED INPUT, as in `test_ui_crew.py`: a schema 3 crew book built at
run time by `parser/_crew_build.py` inside a copy of the captured install,
the page driven under Node with that file's stand-in DOM. The name the game
gave is the owner's own test character's, as the owner gave it in session.
"""

# ruff: noqa: F811 - `crew_at` is test_ui_crew's fixture, imported and requested by name

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from test_ui_crew import LIST, SHOW, _drive, _inert, crew_at  # noqa: F401 - the fixture

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

from _crew_build import KEY_A, T1, book, player

TAY = "Tay-Soulrend"
NAME_LINE = (
    "Name in game: Tay Soulrend (first name Tay, second name Soulrend; "
    "as the game gave it for the character's id)"
)
REGION_LINE = "Region: US (from the game, as the crew book recorded it)"


def _book() -> bytes:
    tay = player(
        guid="Player-9998-0000000E",
        resolved_name="Tay Soulrend",
        resolved_realm="",
        resolved_eighth=22,
        resolved_at=T1 + 100,
        last=T1 + 200,
    )
    return book({TAY: tay, KEY_A: player()}, schema=3, matches=[], region="US")


def test_the_rows_and_the_opened_player_carry_the_names_and_their_source_constructed(
    crew_at: Any, tmp_path: Path
) -> None:
    crew = crew_at(_book())
    listed = crew.report(LIST, {"sort": "last"})
    rows = {p["key"]: p for p in listed["players"]}
    tay = rows[TAY]
    assert (tay["first_name"], tay["second_name"], tay["game_name"]) == (
        "Tay",
        "Soulrend",
        "Tay Soulrend",
    )
    assert (tay["names_from"], tay["region"], tay["region_from"]) == ("game", "us", "book")
    assert rows[KEY_A]["names_from"] == "key"
    shown = crew.report(SHOW, {"name": TAY})
    assert f"  {NAME_LINE}" in shown["lines"] and f"  {REGION_LINE}" in shown["lines"]
    at = [p["key"] for p in listed["players"]]
    out = _drive(crew, tmp_path / "node", [{"crew_tab": True}, {"crew_open": at.index(TAY)}])
    tab, opened = out["steps"]
    for view in out["steps"]:
        _inert(view)
    lines = opened["player"]["lines"]
    assert NAME_LINE in lines and REGION_LINE in lines
    # The row itself is drawn as before: the stored key opens the player.
    assert any(r[0] == TAY for r in tab["rows"])
