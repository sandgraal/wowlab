"""`crew scan-log` and the crew database with the game's map table (M22-22).

CONSTRUCTED INPUT for the logs and books (`parser/_world_lines.py`,
`parser/_crew_build.py`), as `test_crew_world`; the map table is the real
recording `fixtures/wago/Map.1.60.1.71037.csv`, put in the gamedata cache
of a user data directory redirected into `tmp_path`. Nothing here reads a
real install, the real data directory or the network.

What is graded: the open world is told from a dungeon by the table; with no
table the old rule holds and the text says so; the 5 -> 6 migration keeps a
`.before-schema-5` copy and its re-check removes the stored encounters that
were in an instance, with the players only they held, never a crew-book,
rated or noted player; running it again changes nothing.
"""

# ruff: noqa: F811 - `make` and `owner` are fixtures, imported and requested by name

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import crewdb, gamedata, install, worldpvp

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _crew_build import ACCOUNT, FLAVOR, KEY_A, KEY_D
from _world_lines import FOE_A, FOE_D, hits_you, log, you_hit, zone
from test_crew_world import _book, _json, _scan, _write_log, owner  # noqa: F401 - fixture
from test_crewdb import Env, before_schema_7, make  # noqa: F401 - the fixture

MAP_TABLE = Path(__file__).resolve().parent / "fixtures" / "wago" / "Map.1.60.1.71037.csv"


def _version(env: Env) -> str:
    (flavor,) = install.discover(env.root).flavors
    assert flavor.version is not None
    return flavor.version


def _cache_map_table(env: Env) -> Path:
    """The recorded table, cached for the constructed install's version (as
    `db2 fetch Map` would leave it)."""
    dest = gamedata.default_cache_dir() / "tables" / _version(env) / f"{worldpvp.MAP_TABLE}.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MAP_TABLE, dest)
    return dest


def _dungeon_then_world() -> bytes:
    """You fight D in Shadowfang Keep (difficulty 0, as the launch client
    writes it), then A back in the open world."""
    return log(
        zone(5, instance=33, name="Shadowfang Keep", difficulty=0),
        you_hit(FOE_D, 10),
        hits_you(FOE_D, 11),
        zone(400, instance=0, name="Silverpine Forest", difficulty=0),
        you_hit(FOE_A, 1000),
    )


def _rows(env: Env, sql: str) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(env.db())
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _encounters(env: Env) -> list[tuple[Any, ...]]:
    return _rows(env, "SELECT zone, instance_id, by_map FROM world_encounters ORDER BY start_at")


def _as_schema_5(env: Env) -> None:
    """The database as M21-02's schema 5 left it: no `by_map` (nor M21-12's
    schema 7 columns)."""
    conn = sqlite3.connect(env.db(), isolation_level=None)
    try:
        conn.execute("ALTER TABLE world_encounters DROP COLUMN by_map")
        before_schema_7(conn)
        conn.execute("PRAGMA user_version = 5")
    finally:
        conn.close()


# ─── a read with the table ───────────────────────────────────────────────────


def test_constructed_with_the_table_a_dungeon_fight_is_not_kept(make: Any, owner: Any) -> None:
    env = make(_book())
    env.sync()
    _cache_map_table(env)
    _write_log(env, _dungeon_then_world())
    report = _json(_scan(env, "--json"))
    assert report["map_table"] is True and report["map_version"] == _version(env)
    assert (report["scan"]["fights"], report["scan"]["outside_world"]) == (1, 2)
    assert _encounters(env) == [("Silverpine Forest", 0, 1)]
    text = _scan(env)  # nothing new; no map-table line either
    assert text.exit_code == 0 and "map table" not in text.stdout


def test_constructed_without_the_table_the_old_rule_holds_and_says_so(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    env.sync()
    _write_log(env, _dungeon_then_world())
    result = _scan(env)
    assert result.exit_code == 0, result.output
    text = " ".join(result.stdout.split())
    assert (
        f"The game's map table for {_version(env)} is not downloaded, so fights in a dungeon, "
        "raid or battleground whose zone line looks like the open world's may be counted: "
        f"wowlab db2 fetch Map --flavor {FLAVOR} downloads it from wago.tools" in text
    )
    assert _encounters(env) == [("Shadowfang Keep", 33, 0), ("Silverpine Forest", 0, 0)]
    # Once the table is cached, the next read checks what was kept.
    _cache_map_table(env)
    again = _json(_scan(env, "--json"))
    assert again["recheck"] == {
        "checked": 2,
        "removed": 1,
        "set_aside": 0,
        "brought_back": 0,
        "players_removed": 1,
        "skipped": False,
    }
    assert again["map_table_state"] == "read" and again["map_table"] is True
    assert _encounters(env) == [("Silverpine Forest", 0, 1)]
    keys = {k for (k,) in _rows(env, "SELECT player_key FROM players")}
    assert KEY_A in keys and KEY_D not in keys  # D was held only by the dungeon fight


# ─── the 5 -> 6 migration and its re-check ───────────────────────────────────


def test_constructed_a_schema_5_database_is_copied_then_rechecked(make: Any, owner: Any) -> None:
    env = make(_book())
    env.sync()
    _write_log(env, _dungeon_then_world())
    assert _scan(env).exit_code == 0  # no table yet: both kept, as schema 5 did
    _as_schema_5(env)
    before = env.db().read_bytes()
    _cache_map_table(env)
    result = _scan(env)
    assert result.exit_code == 0, result.output
    assert (
        "Checked 2 open-world encounters an earlier read kept against the game's map table: "
        "removed 1 that was in a dungeon, raid, battleground or arena, and 1 player only those "
        "fights held. The ones removed no longer count toward the open-world record or "
        "Nemesis; the copies wowlab keeps beside its crew database still hold them."
        in " ".join(result.stdout.split())
    )
    copy = env.db().with_name(env.db().name + ".before-schema-5")
    assert len(copy.read_bytes()) == len(before)  # a whole copy (SQLite's backup)
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute("SELECT count(*) FROM world_encounters").fetchone()[0] == 2
        held = {k for (k,) in conn.execute("SELECT player_key FROM players")}
        assert KEY_D in held
    finally:
        conn.close()
    assert _rows(env, "PRAGMA user_version") == [(crewdb.SCHEMA,)]
    assert _encounters(env) == [("Silverpine Forest", 0, 1)]
    parts = _rows(env, "SELECT player_key FROM world_encounter_players")
    assert parts == [(KEY_A,)]


def test_constructed_the_recheck_run_again_changes_nothing(make: Any, owner: Any) -> None:
    env = make(_book())
    env.sync()
    _write_log(env, _dungeon_then_world())
    _scan(env)
    _as_schema_5(env)
    _cache_map_table(env)
    _scan(env)
    after = (_encounters(env), _rows(env, "SELECT * FROM players ORDER BY player_key"))
    again = _json(_scan(env, "--json"))
    assert again["recheck"]["checked"] == 0 and again["recheck"]["removed"] == 0
    assert (_encounters(env), _rows(env, "SELECT * FROM players ORDER BY player_key")) == after
    direct = crewdb.recheck_world(env.target(), {33: 1, 0: 0})
    assert direct == crewdb.WorldRecheck()


@pytest.mark.parametrize("mark", ["rating", "note"])
def test_constructed_the_recheck_never_removes_a_rated_or_noted_player(
    make: Any, owner: Any, mark: str
) -> None:
    env = make(_book())
    env.sync()
    _write_log(env, _dungeon_then_world())
    _scan(env)
    conn = sqlite3.connect(env.db(), isolation_level=None)
    try:
        value = "bad" if mark == "rating" else "met in a dungeon"
        statement = {
            "rating": "UPDATE players SET rating = ? WHERE player_key = ?",
            "note": "UPDATE players SET note = ? WHERE player_key = ?",
        }[mark]
        conn.execute(statement, (value, KEY_D))
    finally:
        conn.close()
    _cache_map_table(env)
    got = crewdb.recheck_world(env.target(), {33: 1, 0: 0})
    assert (got.checked, got.removed, got.players_removed) == (2, 1, 0)
    keys = {k for (k,) in _rows(env, "SELECT player_key FROM players")}
    assert KEY_D in keys


def test_constructed_the_recheck_never_removes_a_crew_book_player(make: Any, owner: Any) -> None:
    env = make(_book())  # A is in the game's crew book
    env.sync()
    data = log(
        zone(5, instance=33, name="Shadowfang Keep", difficulty=0),
        you_hit(FOE_A, 10),
        zone(400, instance=0, name="Silverpine Forest", difficulty=0),
    )
    _write_log(env, data)
    _scan(env)
    got = crewdb.recheck_world(env.target(), {33: 1, 0: 0})
    assert (got.checked, got.removed, got.players_removed) == (1, 1, 0)
    assert KEY_A in {k for (k,) in _rows(env, "SELECT player_key FROM players")}
    assert _encounters(env) == []


def _by_map(env: Env) -> list[tuple[Any, ...]]:
    return _rows(env, "SELECT instance_id, by_map FROM world_encounters ORDER BY start_at")


def test_constructed_an_unknown_map_encounter_is_set_aside_not_deleted(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    env.sync()
    _write_log(env, _dungeon_then_world())
    _scan(env)  # no table: both kept
    got = crewdb.recheck_world(env.target(), {0: 0})  # map 33 not in this table
    assert (got.checked, got.removed, got.set_aside, got.brought_back) == (2, 0, 1, 0)
    assert _by_map(env) == [(33, 2), (0, 1)]
    keys = {k for (k,) in _rows(env, "SELECT player_key FROM players")}
    assert KEY_D in keys  # its players are kept with it, for a later table,
    assert KEY_D not in {r.key for r in crewdb.read(FLAVOR, ACCOUNT)}  # but not listed
    # Counted nowhere: not in `crew matches --world`, the record or doctor.
    assert [e.zone for e in crewdb.world_encounters(FLAVOR, ACCOUNT)] == ["Silverpine Forest"]
    assert KEY_D not in {r.key for r in crewdb.read(FLAVOR, ACCOUNT) if r.world_faced}
    (summary,) = crewdb.summaries(FLAVOR)
    assert summary.world_encounters == 1
    assert summary.players == len(crewdb.read(FLAVOR, ACCOUNT))  # nor counted
    # The same table again changes nothing and writes nothing.
    before = env.db().read_bytes()
    assert crewdb.recheck_world(env.target(), {0: 0}) == crewdb.WorldRecheck()
    assert env.db().read_bytes() == before


def test_constructed_a_later_table_naming_the_map_the_open_world_brings_it_back(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    env.sync()
    _write_log(env, _dungeon_then_world())
    _scan(env)
    crewdb.recheck_world(env.target(), {0: 0})
    got = crewdb.recheck_world(env.target(), {0: 0, 33: 0})  # a later table: map 33 is world
    assert (got.checked, got.removed, got.set_aside, got.brought_back) == (1, 0, 0, 1)
    assert _by_map(env) == [(33, 1), (0, 1)]
    assert len(crewdb.world_encounters(FLAVOR, ACCOUNT)) == 2
    assert KEY_D in {r.key for r in crewdb.read(FLAVOR, ACCOUNT)}  # listed again


def test_constructed_a_later_table_naming_the_map_a_dungeon_removes_it(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    env.sync()
    _write_log(env, _dungeon_then_world())
    _scan(env)
    crewdb.recheck_world(env.target(), {0: 0})
    got = crewdb.recheck_world(env.target(), {0: 0, 33: 1})  # a later table: a dungeon
    assert (got.checked, got.removed, got.set_aside, got.players_removed) == (1, 1, 0, 1)
    assert _by_map(env) == [(0, 1)]
    assert KEY_D not in {k for (k,) in _rows(env, "SELECT player_key FROM players")}


def test_constructed_an_unknown_map_is_said_in_the_text(make: Any, owner: Any) -> None:
    env = make(_book())
    env.sync()
    _cache_map_table(env)
    _write_log(
        env,
        log(
            zone(5, instance=4242, name="Invented Hold", difficulty=0),
            you_hit(FOE_D, 10),
            zone(400, instance=0, name="Silverpine Forest", difficulty=0),
            you_hit(FOE_A, 1000),
        ),
    )
    result = _scan(env)
    assert result.exit_code == 0, result.output
    assert (
        "Set aside: 1 fight on a map the game's map table does not hold (id 4242), so wowlab "
        "cannot tell whether it was in the open world: kept, with its players, but counted and "
        "listed nowhere until a later table names the map." in " ".join(result.stdout.split())
    )
    # Kept set aside, out of the open-world encounters; its player is kept
    # but neither counted as added nor listed.
    assert _by_map(env) == [(4242, 2), (0, 1)]
    assert KEY_D not in {r.key for r in crewdb.read(FLAVOR, ACCOUNT)}
    assert [e.zone for e in crewdb.world_encounters(FLAVOR, ACCOUNT)] == ["Silverpine Forest"]
    report = _json(_scan(env, "--json"))  # nothing new: the counts of this read are 0
    assert report["scan"]["unknown_map"] == 0
    assert report["recheck"]["checked"] == 0  # the same table: nothing judged again


def test_constructed_a_read_that_fails_after_a_recheck_says_what_it_removed(
    make: Any, owner: Any, tmp_path: Path
) -> None:
    env = make(_book())
    env.sync()
    _write_log(env, _dungeon_then_world())
    _scan(env)
    _cache_map_table(env)
    gone = tmp_path / "WoWCombatLog-gone.txt"  # a PATH that cannot be read
    result = _scan(env, str(gone))
    assert result.exit_code != 0
    assert "removed 1 that was in a dungeon, raid, battleground or arena" in " ".join(
        result.stderr.split()
    )


def test_constructed_no_database_no_recheck(make: Any) -> None:
    env = make(_book())
    assert crewdb.recheck_world(env.target(), {33: 1}) == crewdb.WorldRecheck()
    assert not env.db().exists()


# ─── what the open-world rule says agrees with what scan-log kept ────────────


def test_constructed_every_kept_encounter_is_the_open_world_by_the_shared_rule(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    env.sync()
    maps = worldpvp.map_types(gamedata.cached_rows("Map", _version(env)) or [])
    assert maps == {}  # nothing cached yet
    _cache_map_table(env)
    rows = gamedata.cached_rows(worldpvp.MAP_TABLE, _version(env))
    assert rows is not None
    maps = worldpvp.map_types(rows)
    _write_log(
        env,
        log(
            zone(5, instance=489, name="Warsong Gulch", difficulty=0),
            you_hit(FOE_D, 10),
            zone(100, instance=33, name="Shadowfang Keep", difficulty=0),
            you_hit(FOE_D, 110),
            zone(400, instance=1, name="Orgrimmar", difficulty=0),
            you_hit(FOE_A, 1000),
        ),
    )
    assert _scan(env).exit_code == 0
    for zone_name, instance_id, by_map in _encounters(env):
        state = worldpvp.ZoneState(zone=zone_name, instance_id=instance_id, difficulty=0)
        assert worldpvp.in_open_world(state, maps=maps) and by_map == 1
    assert [e[0] for e in _encounters(env)] == ["Orgrimmar"]


def test_constructed_the_recheck_line_leaves_out_a_zero_removal_and_says_the_set_aside(
    make: Any, owner: Any
) -> None:
    from wowlab_core import cli

    line = cli._recheck_line(crewdb.WorldRecheck(checked=2, set_aside=1))
    assert line is not None
    assert "removed" not in line
    assert (
        "set aside 1 on a map the table does not know: kept, with their players, but counted and "
        "listed nowhere until a later table names the map. The ones set aside no longer count "
        "toward the open-world record or Nemesis" in line
    )
    both = cli._recheck_line(crewdb.WorldRecheck(checked=3, removed=1, set_aside=1))
    assert both is not None and "The ones removed and set aside no longer count" in both
    assert cli._recheck_line(crewdb.WorldRecheck(checked=1, brought_back=1)) is not None
    assert "no longer count" not in (cli._recheck_line(crewdb.WorldRecheck(checked=1)) or "")


def test_constructed_an_encounter_with_no_instance_id_stays_on_the_difficulty_rule(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    env.sync()
    _write_log(env, _dungeon_then_world())
    _scan(env)
    conn = sqlite3.connect(env.db(), isolation_level=None)
    try:
        conn.execute("UPDATE world_encounters SET instance_id = NULL WHERE instance_id = 33")
    finally:
        conn.close()
    got = crewdb.recheck_world(env.target(), {0: 0, 33: 1})
    assert (got.checked, got.removed, got.set_aside) == (1, 0, 0)  # only map 0 judged
    assert _by_map(env) == [(None, 0), (0, 1)]
