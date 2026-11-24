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

from wowlab_core import crewdb

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _crew_build import ACCOUNT, KEY_A, KEY_C
from _world_lines import ALLY_C, FOE_A, log, you_hit
from test_crew_world import _book, _scan, _write_log, owner  # noqa: F401 - the fixture
from test_crewdb import Env, before_schema_6, make  # noqa: F401 - the fixture

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


# ─── the schema-5 step re-reads launch logs an earlier wowlab keyed ──────────

OLD_TAY = "Tay-SoulrendRoleplayPvP"  # how schema 4's wowlab keyed the four-part name


def _as_schema_4_with_old_keys(env: Env) -> None:
    """Rewrite the database as the schema-4 wowlab left it after reading the
    same log: no `log_names`, user_version 4, and the four-part name keyed
    `<First>-<Second><Realm>` (second name and realm run together, spaces
    dropped). The read state keeps the raw unit names, as it always has."""
    conn = sqlite3.connect(env.db(), isolation_level=None)
    try:
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute("BEGIN")
        conn.execute("DROP TABLE log_names")
        before_schema_6(conn)  # M21-12's columns, which schema 4 did not have
        conn.execute(
            "UPDATE players SET player_key = ?, realm = ? WHERE player_key = ?",
            (OLD_TAY, "SoulrendRoleplayPvP", TAY),
        )
        conn.execute(
            "UPDATE world_encounter_players SET player_key = ? WHERE player_key = ?",
            (OLD_TAY, TAY),
        )
        conn.execute("PRAGMA user_version = 4")
        conn.execute("COMMIT")
    finally:
        conn.close()


def _count(env: Env, sql: str) -> int:
    conn = sqlite3.connect(env.db())
    try:
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


def test_the_schema_5_step_rereads_a_launch_log_under_the_new_keys_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    launch = _write_log(env, _fights())
    beta = _write_log(
        env,
        log(you_hit(FOE_A, 20)).replace(b"4/1/2026", b"3/30/2026"),
        "WoWCombatLog-033026_021630.txt",
    )
    assert _scan(env).exit_code == 0
    assert _count(env, "SELECT count(*) FROM world_encounters") == 2
    _as_schema_4_with_old_keys(env)
    assert OLD_TAY.encode() in env.db().read_bytes()

    result = env.run("crew", "scan-log", "--json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["logs_read_again"] == 1
    again = [f for f in report["logs"] if f["scan"]["read_again"]]
    assert [f["log"] for f in again] == [f"Logs/{launch.name}"]
    rows = _rows(env)
    assert TAY in rows and OLD_TAY not in rows
    assert (rows[TAY]["second_name"], rows[TAY]["realm"]) == ("Soulrend", "Roleplay PvP")
    # No stale encounter: one per log, the launch log's read once more.
    assert _count(env, "SELECT count(*) FROM world_encounters") == 2
    assert (
        _count(env, f"SELECT count(*) FROM world_encounter_players WHERE player_key = '{OLD_TAY}'")
        == 0
    )
    # The book's player and the beta log are as they were.
    assert rows[KEY_A]["world_faced"] == 2
    assert beta.name not in [f["log"].split("/")[-1] for f in again]
    # The copy kept before the step holds the old key; nothing else does.
    copy = env.db().with_name(env.db().name + ".before-schema-4")
    assert OLD_TAY.encode() in copy.read_bytes()
    assert OLD_TAY.encode() not in env.db().read_bytes()
    # Said once: the next scan reads nothing again.
    text = env.run("crew", "scan-log")
    assert text.exit_code == 0 and "read again from the start" not in text.stdout
    later = json.loads(env.run("crew", "scan-log", "--json").stdout)
    assert later["logs_read_again"] == 0


def test_the_requeue_step_runs_again_without_change_constructed(make: Any, owner: Any) -> None:
    env = make(_book())
    _write_log(env, _fights())
    assert _scan(env).exit_code == 0
    conn = sqlite3.connect(env.db(), isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN")
        assert crewdb._requeue_launch_logs(conn) == 1
        assert crewdb._requeue_launch_logs(conn) == 0
        conn.execute("COMMIT")
        assert conn.execute("SELECT count(*) FROM world_encounters").fetchone()[0] == 0
        assert conn.execute("SELECT read_to, scanned_at FROM world_logs").fetchall() == [(0, 0)]
    finally:
        conn.close()
    # The book's players stay; the log-only ones go until the log is read again.
    rows = _rows(env)
    assert KEY_A in rows and TAY not in rows and KEY_C not in rows
    assert _scan(env).exit_code == 0
    assert {TAY, KEY_C} <= set(_rows(env))


def test_a_launch_log_whose_file_is_gone_is_left_as_it_is_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    launch = _write_log(env, _fights())
    assert _scan(env).exit_code == 0
    launch.unlink()
    conn = sqlite3.connect(env.db(), isolation_level=None)
    try:
        conn.execute("BEGIN")
        assert crewdb._requeue_launch_logs(conn) == 0
        conn.execute("COMMIT")
        assert conn.execute("SELECT count(*) FROM world_encounters").fetchone()[0] == 1
    finally:
        conn.close()


# ─── fix round 4: marks follow the player; same log only; flavor gate ────────

TAY_GUID = FOE_TAY[0]


def _rate_old_tay(env: Env) -> None:
    """The schema-4 database's old-key row, rated and noted at the desk."""
    conn = sqlite3.connect(env.db(), isolation_level=None)
    try:
        conn.execute(
            "UPDATE players SET rating = 'good', note = 'healed me', rating_own = 1,"
            " note_own = 1, marks_changed = marks_changed + 1 WHERE player_key = ?",
            (OLD_TAY,),
        )
    finally:
        conn.close()


def _scan_json(env: Env) -> dict[str, Any]:
    result = env.run("crew", "scan-log", "--json")
    assert result.exit_code == 0, result.output
    loaded: dict[str, Any] = json.loads(result.stdout)
    return loaded


def test_a_rating_and_note_on_the_old_key_follow_the_player_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    _write_log(env, _fights())
    assert _scan(env).exit_code == 0
    _as_schema_4_with_old_keys(env)
    _rate_old_tay(env)
    report = _scan_json(env)
    assert (report["logs_read_again"], report["marks_carried"]) == (1, 1)
    rows = _rows(env)
    assert OLD_TAY not in rows
    assert (rows[TAY]["rating"], rows[TAY]["note"]) == ("good", "healed me")
    assert rows[TAY]["guid"] == TAY_GUID
    # Said once: the old row is gone, so nothing is carried again.
    text = env.run("crew", "scan-log")
    assert text.exit_code == 0 and "Kept your rating or note" not in text.stdout
    assert _scan_json(env)["marks_carried"] == 0


def test_a_rating_is_not_moved_when_the_game_id_names_two_players_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    _write_log(env, _fights())
    assert _scan(env).exit_code == 0
    _as_schema_4_with_old_keys(env)
    _rate_old_tay(env)
    conn = sqlite3.connect(env.db(), isolation_level=None)
    try:  # a second key with the same game id (a rename, constructed)
        conn.execute(
            "INSERT INTO players (player_key, name, realm, guid, marks_changed, marks_agreed)"
            " VALUES ('Tay-Elsewhere', 'Tay', 'Elsewhere', ?, 0, 0)",
            (TAY_GUID,),
        )
    finally:
        conn.close()
    report = _scan_json(env)
    assert (report["marks_carried"], report["marks_not_carried"]) == (0, 1)
    rows = _rows(env)
    assert rows[OLD_TAY]["rating"] == "good" and rows[TAY]["rating"] is None
    assert "Tay-Elsewhere" in rows
    text = env.run("crew", "scan-log").stdout
    assert "left as they were" not in text  # nothing new read, nothing said


def test_a_log_that_changed_since_is_not_reread_and_said_once_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    launch = _write_log(env, _fights())
    assert _scan(env).exit_code == 0
    _as_schema_4_with_old_keys(env)
    launch.write_bytes(launch.read_bytes().replace(b"4/1/2026", b"4/3/2026", 1))
    before = _count(env, "SELECT count(*) FROM world_encounter_players")
    result = env.run("crew", "scan-log")
    assert result.exit_code == 0, result.output
    assert result.stdout.count("could not be read again") == 1
    assert "crew forget removes one" in result.stdout
    # Nothing of the old read was deleted: its old key is still there.
    assert _count(env, f"SELECT count(*) FROM players WHERE player_key = '{OLD_TAY}'") == 1
    assert _count(env, "SELECT count(*) FROM world_encounter_players") >= before
    later = env.run("crew", "scan-log")
    assert "could not be read again" not in later.stdout


def test_no_log_is_requeued_on_a_flavor_without_second_names_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    _write_log(env, _fights())
    assert _scan(env).exit_code == 0
    account = env.flavor / "WTF" / "Account" / ACCOUNT
    (account / "Somerealm").mkdir()
    (account / "1" / "Sandgraal-Rizzen").rename(account / "Somerealm" / "Sandgraal")
    (account / "1").rmdir()
    conn = sqlite3.connect(env.db(), isolation_level=None)
    try:
        conn.execute("BEGIN")
        assert crewdb._requeue_launch_logs(conn) == 0
        conn.execute("COMMIT")
        assert conn.execute("SELECT count(*) FROM world_encounters").fetchone()[0] == 1
    finally:
        conn.close()
