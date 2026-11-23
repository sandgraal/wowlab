"""The names a combat log gives a crew row (M21-02): a four-part unit name
(`<First>-<Second>-<Realm>-<Region>`, the launch client's) and a three-part
one (`<Name>-<Realm>-<Region>`, the beta's), read by `crew scan-log` into a
second-name flavor's database and shown by `crew list --json`.

CONSTRUCTED INPUT, every byte, as in `test_crew_world.py`: crew books from
`parser/_crew_build.py`, combat logs from `parser/_world_lines.py` (the real
beta log's lines with an invented player swapped in), inside a copy of the
captured install `fixtures/macos-70888/`, whose account holds a
`1/<First>-<Second>` character folder (a second-name flavor). The four-part
shape and its realm and region are those of the real launch log
(`fixtures/macos-71037/`, graded in `parser/test_worldpvp_launch_names.py`).
"""

# ruff: noqa: F811 - `make` and `owner` are imported fixtures, requested by name

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _crew_build import KEY_A, KEY_C
from _world_lines import ALLY_C, FOE_A, log, you_hit
from test_crew_world import _book, _scan, _write_log, owner  # noqa: F401 - the fixture
from test_crewdb import Env, make  # noqa: F401 - the fixture

# A launch-client player, as the real log writes one (constructed name).
FOE_TAY = ("Player-9998-0000000E", "Tay-Soulrend-Roleplay PvP-US")
TAY = "Tay-Soulrend"


def _rows(env: Env) -> dict[str, dict[str, Any]]:
    result = env.run("crew", "list", "--json")
    assert result.exit_code == 0, result.output
    return {p["key"]: p for p in json.loads(result.stdout)["players"]}


def _fights() -> bytes:
    """You hit A (in the book), Tay (four parts) and C (three parts, only
    the log names them)."""
    return log(you_hit(FOE_A, 10), you_hit(FOE_TAY, 11), you_hit(ALLY_C, 12))


def test_a_four_part_name_gives_the_second_name_realm_and_region_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    _write_log(env, _fights())
    assert _scan(env).exit_code == 0
    tay = _rows(env)[TAY]
    assert (tay["first_name"], tay["second_name"]) == ("Tay", "Soulrend")
    assert tay["game_name"] == "Tay Soulrend"
    assert tay["realm"] == "Roleplay PvP"
    assert (tay["region"], tay["region_from"]) == ("us", "log")


def test_a_three_part_name_on_a_second_name_flavor_keeps_its_realm_constructed(
    make: Any, owner: Any
) -> None:
    """Only the log names C, in the beta's three parts: its key's second
    part is a realm, not a second name, though the flavor has second names."""
    env = make(_book())
    _write_log(env, _fights())
    assert _scan(env).exit_code == 0
    rows = _rows(env)
    c = rows[KEY_C]
    assert (c["first_name"], c["second_name"], c["realm"]) == ("Inventedc", None, "Otherfakerealm")
    assert (c["region"], c["region_from"]) == ("us", "settings")
    # A in the book: the lab-addon's key on a second-name flavor.
    a = rows[KEY_A]
    assert (a["first_name"], a["second_name"], a["realm"]) == ("Inventeda", "Fakerealm", None)


def test_forget_drops_what_the_log_gave_constructed(make: Any, owner: Any) -> None:
    env = make(_book())
    _write_log(env, _fights())
    assert _scan(env).exit_code == 0
    assert env.run("crew", "forget", TAY, "--yes").exit_code == 0
    conn = sqlite3.connect(env.db())
    try:
        assert conn.execute("SELECT count(*) FROM log_names").fetchone()[0] == 0
    finally:
        conn.close()
    assert TAY.encode() not in env.db().read_bytes()
