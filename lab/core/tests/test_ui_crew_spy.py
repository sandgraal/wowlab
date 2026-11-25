"""The Crew tab shows what the Spy addon saw (M22-02): a "Seen by Spy"
filter in the Matches box (`crew list --spy`, with its own order), a "Last
seen by Spy" column (the date and the place Spy recorded, as the command
gave them), and a "Kill on sight" badge for a player on Spy's list. The
class of a player only Spy saw is Spy's, said as Spy's.

Input: the M22-01 capture's `Spy.lua` files (`fixtures/macos-71037/`,
provenance rows in `fixtures/README.md`) in the constructed install of
`test_ui_crew.py`, and, for the badge, one constructed Spy file with an
invented name (`constructed` in the test's name). The page is driven under
Node with that file's stand-in DOM; every crew run goes through the real
server to the real CLI.
"""

# ruff: noqa: F811 - `crew_at` is test_ui_crew's fixture, imported and requested by name

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from test_ui_crew import LIST, _bodies, _drive, _inert, crew_at  # noqa: F401 - the fixture

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

from _crew_build import ACCOUNT, FLAVOR, KEY_A, book, player

CAPTURE_ACCOUNTS = Path(__file__).resolve().parent / "fixtures" / "macos-71037" / "forever"
SANDGRAAL = f"WTF/Account/{ACCOUNT}/1/Sandgraal-Rizzen/SavedVariables/Spy.lua"
GORUTHUZ = "Goruthuz-Mugstout"  # from the capture: The Barrens, Ratchet, a level 43 paladin
SPY_A = "Inventspy-Fakesecond"  # invented
NL = "\r\n"


def _with_capture(root: Path) -> None:
    shutil.copytree(
        CAPTURE_ACCOUNTS / "WTF" / "Account", root / FLAVOR / "WTF" / "Account", dirs_exist_ok=True
    )


def test_the_seen_by_spy_view_and_column_capture(crew_at: Any, tmp_path: Path) -> None:
    crew = crew_at(book({KEY_A: player()}))
    _with_capture(crew.root)
    steps: list[dict[str, Any]] = [
        {"crew_tab": True},
        {"crew_set": "crew-pvp", "value": "spy"},
    ]
    out = _drive(crew, tmp_path / "node", steps)
    tab, spied = out["steps"]
    for view in out["steps"]:
        _inert(view)
    assert tab["heads"][-1] == "Last seen by Spy"
    rows = {r[0]: r for r in tab["rows"]}
    assert rows[KEY_A][-1] == "never"
    goruthuz = rows[GORUTHUZ]
    assert goruthuz[1] == "Paladin, level 43 (as Spy saw them)"
    # The page's own date (as "Last grouped" shows one), then the place.
    day, _, place = goruthuz[-1].partition(" in ")
    assert place == "The Barrens, Ratchet" and day and day != "never"
    # The view is the command's own filter and order.
    assert _bodies(spied) == [
        {"command": ["crew", "list"], "params": {"spied": True, "no_links": True}}
    ]
    assert len(spied["rows"]) == 24 and KEY_A not in [r[0] for r in spied["rows"]]
    assert spied["sort"] == "spy"
    listed = crew.report(LIST, {"spied": True})
    assert [p["key"] for p in listed["players"]] == [r[0] for r in spied["rows"]]


def _kos_file() -> bytes:
    row = NL.join(
        [
            f'["{SPY_A}"] = {{',
            f'["name"] = "{SPY_A}",',
            '["class"] = "ROGUE",',
            '["level"] = 29,',
            '["isEnemy"] = true,',
            '["zone"] = "Invented Vale",',
            '["time"] = 1795400000,',
            "},",
        ]
    )
    body = NL.join(
        [
            "",
            "SpyPerCharDB = {",
            '["version"] = "1.1",',
            '["PlayerData"] = {',
            row,
            "},",
            '["KOSData"] = {',
            f'["{SPY_A}"] = 1795400005,',
            "},",
            "}",
        ]
    )
    return (body + NL).encode()


def test_a_kill_on_sight_player_gets_the_badge_constructed(crew_at: Any, tmp_path: Path) -> None:
    crew = crew_at(book({KEY_A: player()}))
    path = crew.root / FLAVOR / SANDGRAAL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_kos_file())
    out = _drive(crew, tmp_path / "node", [{"crew_tab": True}])
    (tab,) = out["steps"]
    _inert(tab)
    badges = [e for e in tab["drawn"] if e["cls"].startswith("badge")]
    assert [(e["cls"], e["tag"]) for e in badges] == [("badge badge-kos", "span")]
    row = next(r for r in tab["rows"] if r[0].startswith(SPY_A))
    assert row[0] == SPY_A + "Kill on sight"
    day, _, place = row[-1].partition(" in ")
    assert place == "Invented Vale" and day and day != "never"
