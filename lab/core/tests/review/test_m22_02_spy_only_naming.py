# Probe from review of m22/02-spy-pvp-book; reproduces a Spy-only player named "from the name the crew book keeps", and one only Spy's fights name not treated as seen by Spy
"""Two defects of the Spy-only naming rule (ticket M22-02, amendment of
2026-11-24: "Spy-only rows take their region from the crew book's recorded
region, then the settings, with `region_from`, and get their own
`names_from` value (`spy`)").

1. `crew show` says a Spy-only player's name came "from the name the crew
   book keeps": `_names_words` has no wording for `names_from` `spy`, so it
   falls through to the stored-key sentence. The crew book never held them.
2. A player whom only Spy's `Encounters` list names (no `PlayerData` row,
   no kill-on-sight mark) gets a players row and three fights in the
   record, but is not treated as seen by Spy: `read()` builds its `spied`
   set from `_spy_facts`, which reads `spy_sightings` and `spy_players`
   only. So the row is `names_from` `key` (its second name read as a
   realm), takes the settings' region instead of the book's, carries
   `spy: null`, and `crew list --spy` ("Only players the Spy addon saw")
   leaves it out.

Constructed (invented names, the test_crew_spy helpers); each test has a
positive control: a Spy player with a `PlayerData` row, which the branch
handles as the amendment says.
"""

# ruff: noqa: F811 - `make` is an imported fixture, requested by name

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _crew_build import KEY_A, T1, book, player
from test_crew_spy import SANDGRAAL, SPY_A, SPY_B, TS, _put, _row, _spy_file
from test_crewdb import make  # noqa: F401 - the fixture


def _book3() -> bytes:
    """A schema 3 book that recorded the region US, with one invented player."""
    return book({KEY_A: player()}, schema=3, region="US", region_at=T1, matches=[])


def _names_line(env: Any, key: str) -> str:
    result = env.run("crew", "show", key, "--json")
    assert result.exit_code == 0, result.output
    lines = json.loads(result.stdout)["lines"]
    return next(x for x in lines if x.startswith("  Name in game: "))


def test_a_spy_only_name_is_not_said_to_come_from_the_crew_book_constructed(make: Any) -> None:
    env = make(_book3())
    _put(env, SANDGRAAL, _spy_file({SPY_A: _row(SPY_A)}))
    # Positive control: the book's own player is named from the book's key.
    assert _names_line(env, KEY_A).endswith("from the name the crew book keeps)")
    spy_line = _names_line(env, SPY_A)
    assert "second name Fakesecond" in spy_line  # names_from is `spy`
    assert "crew book" not in spy_line, spy_line


def test_a_player_only_spys_fights_name_is_seen_by_spy_constructed(make: Any) -> None:
    env = make(_book3())
    fights = [{"t": TS + 10 * i, "n": SPY_B, "r": "L"} for i in range(1, 4)]
    _put(env, SANDGRAAL, _spy_file({SPY_A: _row(SPY_A)}, fights=fights))
    result = env.run("crew", "list", "--json")
    assert result.exit_code == 0, result.output
    rows = {p["key"]: p for p in json.loads(result.stdout)["players"]}
    # Positive control: a PlayerData player is Spy-only as the amendment says.
    a = rows[SPY_A]
    assert (a["names_from"], a["region_from"], a["spy"] is not None) == ("spy", "book", True)
    # The fight-only player is in the database, with Spy's three fights.
    b = rows[SPY_B]
    assert b["record"]["sources"] == {"spy": 3}
    assert b["names_from"] == "spy", b["names_from"]
    assert b["region_from"] == "book", b["region_from"]
    assert b["spy"] is not None
    spied = json.loads(env.run("crew", "list", "--json", "--spy").stdout)["players"]
    assert SPY_B in [p["key"] for p in spied]
