"""The Twink panel says where an item's stats came from (M22-23).

Driven under Node with `test_ui_twink.py`'s harness and setup: the real
`char twink` report of the `macos-70888` warlock through the real server.
The case is `constructed` (L8): the report is edited before the page reads
it so that the first candidate and the worn belt say their stats came from
the hotfix cache, as `char twink` writes it when the game's cache is laid
over the tables (`test_cli_char_twink_hotfix.py` grades that part). The
page must say so on the line a reader sees without opening anything, and in
the opened item's details. Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from the UI modules are requested by name)

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from test_ui_character_page import both, home, server  # noqa: F401
from test_ui_twink import _drive, _report, _scenario, _twink_run, _twink_steps, tables  # noqa: F401


def test_the_stats_source_is_shown_constructed(
    server: Any, both: Path, tables: Path, tmp_path: Path
) -> None:
    scenario = _scenario(server, [{"twink": "show"}, {"twink": "open", "at": 0}])
    report = _report(scenario)
    panel = report["twink"]
    first = panel["slots"][0]["candidates"][0]
    first["stats_from"] = "hotfix cache"
    second = panel["slots"][0]["candidates"][1]
    assert second["stats_from"] == "game tables"
    waist = next(s for s in panel["slots"] if s["slot"] == 6)["worn"]
    waist.update(score=4.5, unscored=None, stats_from="hotfix cache")
    _twink_run(scenario)["stdout"] = json.dumps(report)
    out = _drive(tmp_path / "node", scenario)
    shown, opened = _twink_steps(out)
    head = shown["slots"][0]["candidates"]
    assert "· stats from the hotfix cache" in head[0]["facts"]
    assert "hotfix" not in head[1]["facts"]
    belt = next(s for s in shown["slots"] if s["slot"] == "Waist")
    assert belt["worn"] == (
        "Worn: Aboriginal Sash of the Sorcerer · score 4.5 · stats from the hotfix cache"
    )
    detail = opened["slots"][0]["candidates"][0]["detail"]
    at = detail.index("Stats from")
    assert detail[at + 1] == (
        "your game's hotfix cache (item rows the server has sent your game, at login or when it "
        "first showed the item)"
    )
    other = opened["slots"][0]["candidates"][1]["detail"]
    assert other[other.index("Stats from") + 1] == "the game tables wowlab downloads"
    assert not out["traps"]
