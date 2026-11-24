# Probe from review of m22/22-open-world-map; reproduces the map-table re-check deleting stored encounters on a `crew scan-log` that then fails (no log left), so the removal is never said
"""CONSTRUCTED INPUT (L8, boundary case): the logs and books are the
constructed ones of `test_crew_world`; the map table is the real recording
`fixtures/wago/Map.1.60.1.71037.csv`, cached in a user data directory
redirected into `tmp_path`.

M22-22's acceptance asks for "one `crew scan-log` line saying how many were
removed". `crew_scan_log` runs `crewdb.recheck_world` (which deletes) before
it looks for a log, and only adds the line to a report that it reaches. A
player who has deleted their combat logs (they grow large) runs `crew
scan-log`: it deletes the encounters that were in an instance, then fails
with "no combat log in ...", and no line, text or JSON, says anything was
removed. The next read finds nothing left to check, so it is never said.
Expected: what the re-check removes is said by the run that removed it (or
by a later one), or nothing is removed by a run that cannot say so.

The positive control is the same sequence with the log still there: the
line is printed."""

# ruff: noqa: F811 - `make` and `owner` are fixtures, imported and requested by name

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

from wowlab_core import gamedata, install, worldpvp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _world_lines import FOE_A, FOE_D, hits_you, log, you_hit, zone
from test_crew_world import _book, _scan, _write_log, owner  # noqa: F401 - fixture
from test_crewdb import Env, make  # noqa: F401 - the fixture

MAP_TABLE = Path(__file__).resolve().parents[1] / "fixtures" / "wago" / "Map.1.60.1.71037.csv"
SAID = "removed 1 that was in a dungeon, raid, battleground or arena"
LATER_LOG = "WoWCombatLog-040226_021630.txt"


def _cache_map_table(env: Env) -> None:
    (flavor,) = install.discover(env.root).flavors
    assert flavor.version is not None
    dest = gamedata.default_cache_dir() / "tables" / flavor.version / f"{worldpvp.MAP_TABLE}.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MAP_TABLE, dest)


def _encounters(env: Env) -> int:
    conn = sqlite3.connect(env.db())
    try:
        return int(conn.execute("SELECT count(*) FROM world_encounters").fetchone()[0])
    finally:
        conn.close()


def _kept_without_table(make: Any) -> tuple[Env, Path]:
    env: Env = make(_book())
    env.sync()
    path = _write_log(
        env,
        log(
            zone(5, instance=33, name="Shadowfang Keep", difficulty=0),
            you_hit(FOE_D, 10),
            hits_you(FOE_D, 11),
            zone(400, instance=0, name="Silverpine Forest", difficulty=0),
            you_hit(FOE_A, 1000),
        ),
    )
    assert _scan(env).exit_code == 0
    assert _encounters(env) == 2  # no table: the dungeon fight was kept
    return env, path


def test_control_with_the_log_there_the_removal_is_said(make: Any, owner: Any) -> None:
    env, _ = _kept_without_table(make)
    _cache_map_table(env)
    result = _scan(env)
    assert result.exit_code == 0, result.output
    assert SAID in result.stdout
    assert _encounters(env) == 1


def test_a_removal_by_a_scan_that_finds_no_log_is_said(make: Any, owner: Any) -> None:
    env, path = _kept_without_table(make)
    path.unlink()  # the player cleared their Logs folder
    _cache_map_table(env)
    failed = _scan(env)
    assert failed.exit_code != 0  # "no combat log in ..."
    removed = _encounters(env) < 2
    # The next read, with a log again.
    _write_log(env, log(zone(5, instance=0, name="Silverpine Forest", difficulty=0)), LATER_LOG)
    later = _scan(env)
    assert later.exit_code == 0, later.output
    said = SAID in failed.output + later.output
    assert said or not removed, (removed, failed.output, later.output)
