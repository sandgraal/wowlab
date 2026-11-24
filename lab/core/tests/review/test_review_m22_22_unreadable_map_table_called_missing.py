# Probe from review of m22/22-open-world-map; reproduces a cached but unreadable Map table reported as "not downloaded", with a remedy (db2 fetch) that cannot change it
"""CONSTRUCTED INPUT (L8, hostile case): the logs and books are the
constructed ones of `test_crew_world`; the map table is either the real
recording `fixtures/wago/Map.1.60.1.71037.csv` (positive control) or a
cached `Map.csv` whose second line has one field too many, so
`gamedata.cached_rows` raises `MalformedTable` while iterating.

`_cmd_crew._map_types` swallows that error and returns None, the same value
as "not cached", so `crew scan-log` says the table "is not downloaded" and
that `wowlab db2 fetch Map --flavor ...` downloads it, and its `--json`
says `map_table: false`, which makes the Crew tab offer the download
button. But `db2 fetch` returns a cached table untouched, whatever it holds
(L5, `GameData.table`): it fetches nothing, and the next read says the
same thing again. The player is told something false and sent round a loop
that cannot end. Expected: a cached table that cannot be read is not called
missing.

Nothing here reads a real install, the real data directory or the network:
every HTTP send is made to fail, to show `db2 fetch` never tries one."""

# ruff: noqa: F811 - `make` and `owner` are fixtures, imported and requested by name

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from wowlab_core import gamedata, install, worldpvp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _crew_build import FLAVOR
from _world_lines import FOE_A, FOE_D, hits_you, log, you_hit, zone
from test_crew_world import _book, _json, _scan, _write_log, owner  # noqa: F401 - fixture
from test_crewdb import Env, make  # noqa: F401 - the fixture

MAP_TABLE = Path(__file__).resolve().parents[1] / "fixtures" / "wago" / "Map.1.60.1.71037.csv"


def _cached(env: Env) -> Path:
    (flavor,) = install.discover(env.root).flavors
    assert flavor.version is not None
    dest = gamedata.default_cache_dir() / "tables" / flavor.version / f"{worldpvp.MAP_TABLE}.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("no network in a probe")

    monkeypatch.setattr(httpx.Client, "send", refuse)


def _setup(make: Any) -> Env:
    env: Env = make(_book())
    env.sync()
    _write_log(
        env,
        log(
            zone(5, instance=33, name="Shadowfang Keep", difficulty=0),
            you_hit(FOE_D, 10),
            hits_you(FOE_D, 11),
            zone(400, instance=0, name="Silverpine Forest", difficulty=0),
            you_hit(FOE_A, 1000),
        ),
    )
    return env


def test_control_a_readable_cached_table_is_used(
    make: Any, owner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_network(monkeypatch)
    env = _setup(make)
    shutil.copyfile(MAP_TABLE, _cached(env))
    report = _json(_scan(env, "--json"))
    assert report["map_table"] is True


def test_a_cached_table_that_cannot_be_read_is_not_called_missing(
    make: Any, owner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_network(monkeypatch)
    env = _setup(make)
    _cached(env).write_bytes(b"ID,InstanceType\n33,1,extra\n0,0\n")
    first = _scan(env)
    assert first.exit_code == 0, first.output
    # The remedy the text names: it returns the same cached file, fetching nothing.
    fetched = env.run("db2", "fetch", worldpvp.MAP_TABLE, "--json")
    assert fetched.exit_code == 0, fetched.output
    again = _scan(env)
    assert again.exit_code == 0, again.output
    # The defect: the table is there (and db2 fetch just said so), yet the read
    # still says it is not downloaded and that db2 fetch downloads it.
    assert "is not downloaded" not in again.stdout, again.stdout
    assert f"wowlab db2 fetch Map --flavor {FLAVOR} downloads it" not in again.stdout
