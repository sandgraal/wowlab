# Probe from review of m21/02-region; reproduces the schema-5 step deleting a player row no requeued log ever held
# ruff: noqa: E402, F811 - imports after the sys.path lines; `make` and `owner` are fixtures
"""Constructed (L8: boundary input, labelled; no real file is needed).

`crewdb._requeue_launch_logs` documents that "a player row only such an
encounter held ... is deleted with it; every other row stays". Its last
statement, `_SQL_REQUEUE_DROP_LOG_ONLY_PLAYERS`, is not limited to the
players of the requeued logs: it deletes every row of the whole database
with no counters, no rating, no note and no encounter or match part.

Such a row exists without any combat log: the lab-addon admits a player at
`met = 0` when it is rated or noted from a target (`Crew.lua`, `admit`), a
cleared rating leaves it with nothing, and when the book later drops it a
sync keeps the row with `in_book = 0` (`crewdb._left`). It still holds what
the book said of the player (class, guild, first and last seen).

The probe builds a schema-4 database as the test suite does
(`test_crew_log_names._as_schema_4_with_old_keys`), adds one such former
book player, and lets `crew scan-log` migrate it. The positive control is
the same database whose one log is a beta (three-part) log: nothing is
requeued and the row stays. Expected in both: the row stays.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import crewdb

TESTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TESTS / "parser"))
sys.path.insert(0, str(TESTS))

from _world_lines import FOE_A, log, you_hit
from test_crew_log_names import _as_schema_4_with_old_keys, _count, _fights
from test_crew_world import _book, _scan, _write_log, owner  # noqa: F401 - the fixture
from test_crewdb import Env, before_schema_6, before_schema_7, make  # noqa: F401 - the fixture

from wowlab_core import crewdb

FORMER = "Formerbook-Someone"  # constructed key


def _add_former_book_player(env: Env) -> None:
    conn = sqlite3.connect(env.db(), isolation_level=None)
    try:
        conn.execute(
            "INSERT INTO players (player_key, name, realm, class, guild, first_at, last_at,"
            " marks_changed, marks_agreed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (FORMER, "Formerbook", "Someone", "WARRIOR", "Some Guild", 100, 200, 1, 1),
        )
    finally:
        conn.close()


def _as_schema_4(env: Env) -> None:
    conn = sqlite3.connect(env.db(), isolation_level=None)
    try:
        conn.execute("DROP TABLE log_names")
        before_schema_6(conn)  # M22-22's `by_map`, which schema 4 did not have
        before_schema_7(conn)  # M21-12's columns, which schema 4 did not have
        conn.execute("PRAGMA user_version = 4")
    finally:
        conn.close()


@pytest.mark.parametrize("launch", [True, False], ids=["launch-log-requeued", "control-beta-log"])
def test_constructed_schema_5_step_keeps_a_former_book_player(
    make: Any,
    owner: Any,
    launch: bool,
) -> None:
    env = make(_book())
    _write_log(env, _fights() if launch else log(you_hit(FOE_A, 10)))
    assert _scan(env).exit_code == 0
    if launch:
        _as_schema_4_with_old_keys(env)
    else:
        _as_schema_4(env)
    _add_former_book_player(env)
    assert _count(env, "PRAGMA user_version") == 4

    result = _scan(env, "--json")  # opens the database: the 4 -> 5 step runs
    assert result.exit_code == 0, result.output
    # M21-12: and on to this wowlab's schema.
    assert _count(env, "PRAGMA user_version") == crewdb.SCHEMA
    held = _count(env, f"SELECT count(*) FROM players WHERE player_key = '{FORMER}'")
    assert held == 1, "the schema-5 step deleted a player no requeued log held"
