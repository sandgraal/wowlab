"""`live` and `crew scan-log` judge the open world alike, by the game's map
table (M22-22).

CONSTRUCTED INPUT for the logs, books and records, as `test_crew_world`
and `test_cli_live` build them; the map table is the real recording
`fixtures/wago/Map.1.60.1.71037.csv`, put in the gamedata cache of a user
data directory redirected into `tmp_path` under the constructed install's
version. Nothing reads a real install, the real data directory or the
network.
"""

# ruff: noqa: F811 - `make` and `owner` are fixtures, imported and requested by name

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import crewdb, gamedata, install, live, maptable, worldpvp

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _crew_build import ACCOUNT, FLAVOR
from _world_lines import FOE_A, FOE_D, OWNER_GUID, hits_you, log, you_hit, zone
from test_crew_world import _book as _crew_book
from test_crew_world import _scan, _write_log
from test_crew_world import owner as crew_owner  # noqa: F401 - the fixture
from test_crewdb import Env, make  # noqa: F401 - the fixture

MAP_TABLE = Path(__file__).resolve().parent / "fixtures" / "wago" / "Map.1.60.1.71037.csv"


def _version(env: Env) -> str:
    (flavor,) = install.discover(env.root).flavors
    assert flavor.version is not None
    return flavor.version


def _cache(env: Env) -> None:
    dest = gamedata.default_cache_dir() / "tables" / _version(env) / f"{worldpvp.MAP_TABLE}.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MAP_TABLE, dest)


def _dungeon_and_world() -> bytes:
    return log(
        zone(5, instance=33, name="Shadowfang Keep", difficulty=0),
        you_hit(FOE_D, 10),
        hits_you(FOE_D, 11),
        zone(400, instance=0, name="Silverpine Forest", difficulty=0),
        you_hit(FOE_A, 1000),
        hits_you(FOE_A, 1001),
    )


def _shape(found: list[Any]) -> list[tuple[Any, ...]]:
    return sorted((e.zone, e.instance_id, e.start, e.end) for e in found)


@pytest.mark.parametrize("cached", [True, False], ids=["table-cached", "no-table"])
def test_constructed_live_and_crew_scan_log_keep_the_same_encounters(
    make: Any, crew_owner: Any, cached: bool
) -> None:
    env = make(_crew_book())
    env.sync()
    path = _write_log(env, _dungeon_and_world())
    if cached:
        _cache(env)
    assert _scan(env).exit_code == 0
    kept = crewdb.world_encounters(FLAVOR, ACCOUNT)
    table = maptable.load(_version(env))
    assert table.state == ("read" if cached else "missing")
    seen, note = live.world_since([path], {OWNER_GUID: None}, 0, maps=table.usable())
    assert note is None
    assert _shape(seen) == _shape(kept)
    zones = [e.zone for e in seen]
    assert zones == (["Silverpine Forest"] if cached else ["Shadowfang Keep", "Silverpine Forest"])


def test_constructed_the_watch_judges_the_place_by_the_table(make: Any) -> None:
    env = make(_crew_book())
    _write_log(env, log(zone(5, instance=33, name="Shadowfang Keep", difficulty=0)))
    plain = live.Watcher().poll(env.flavor, FLAVOR)
    assert plain.log is not None and plain.log.in_world is True  # difficulty 0
    _cache(env)
    table = maptable.load(_version(env))
    watched = live.Watcher().poll(
        env.flavor, FLAVOR, maps=table.usable(), map_table=table.state, map_words=None
    )
    assert watched.log is not None and watched.log.in_world is False
    assert watched.map_table == "read"


def test_maptable_tells_missing_unreadable_and_no_version_apart(make: Any) -> None:
    env = make(_crew_book())
    version = _version(env)
    assert maptable.load(None).state == "no_version"
    assert maptable.load("not a build").state == "no_version"
    assert maptable.load(version).state == "missing"
    dest = gamedata.default_cache_dir() / "tables" / version / f"{worldpvp.MAP_TABLE}.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"ID,InstanceType\n33,1,extra\n")  # CONSTRUCTED: a malformed table
    bad = maptable.load(version)
    assert bad.state == "unreadable" and bad.usable() is None and bad.path == str(dest)
    dest.write_bytes(b"ID,Other\n33,1\n")  # CONSTRUCTED: no InstanceType column
    assert maptable.load(version).state == "unreadable"
    shutil.copyfile(MAP_TABLE, dest)
    good = maptable.load(version)
    assert good.state == "read" and good.usable() == good.maps and good.maps[33] == 1


def test_constructed_a_set_aside_map_is_left_out_by_live_and_crew_scan_log_alike(
    make: Any, crew_owner: Any
) -> None:
    env = make(_crew_book())
    env.sync()
    path = _write_log(
        env,
        log(
            zone(5, instance=4242, name="Invented Hold", difficulty=0),  # not in the table
            you_hit(FOE_D, 10),
            zone(400, instance=0, name="Silverpine Forest", difficulty=0),
            you_hit(FOE_A, 1000),
        ),
    )
    _cache(env)
    assert _scan(env).exit_code == 0
    kept = crewdb.world_encounters(FLAVOR, ACCOUNT)
    table = maptable.load(_version(env))
    seen, _ = live.world_since([path], {OWNER_GUID: None}, 0, maps=table.usable())
    assert _shape(seen) == _shape(kept)
    assert [e.zone for e in seen] == ["Silverpine Forest"]


def _unknown_place_log() -> bytes:
    return log(
        zone(5, instance=4242, name="Invented Hold", difficulty=0),
        you_hit(FOE_D, 10),
        hits_you(FOE_D, 11),
        zone(400, instance=0, name="Silverpine Forest", difficulty=0),
        you_hit(FOE_A, 1000),
    )


def test_constructed_live_says_what_it_set_aside(make: Any) -> None:
    env = make(_crew_book())
    path = _write_log(env, _unknown_place_log())
    _cache(env)
    table = maptable.load(_version(env))
    aside: list[worldpvp.WorldEncounter] = []
    seen, _ = live.world_since([path], {OWNER_GUID: None}, 0, maps=table.usable(), set_aside=aside)
    assert [e.zone for e in seen] == ["Silverpine Forest"]
    assert [(e.instance_id, e.unknown_map) for e in aside] == [(4242, True)]
    from wowlab_core import cli

    words = cli._set_aside_words(1, [4242])
    assert words.startswith(
        "Set aside: 1 encounter on a place the game's map table does not hold (map 4242)"
    )


def test_constructed_an_unknown_place_is_not_called_a_match(make: Any) -> None:
    env = make(_crew_book())
    path = _write_log(env, _unknown_place_log())
    _cache(env)
    table = maptable.load(_version(env))
    recap = live.match_recap([path], {OWNER_GUID: None}, maps=table.usable())
    assert recap is not None and recap.unknown_map and recap.instance_id == 4242
    from wowlab_core import cli

    first = cli._live_match_lines(recap, cli._LiveBook([]))[0]
    assert first.startswith("A place the game's map table does not hold (map 4242): Invented Hold")
    assert not first.startswith("Match")
