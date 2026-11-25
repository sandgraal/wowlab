"""World PvP from the combat log at the desk (M20-18, docs/LAB_PLAN.md
§21.3): the database step, `crew scan-log`, forget, `doctor`, and `crew
list --world`, `crew show`, `crew matches --world`. CONSTRUCTED INPUT,
every byte: crew books from `parser/_crew_build.py`, combat logs from
`parser/_world_lines.py` (the real log's lines with an invented player
swapped in), databases made at run time under a user data directory
redirected into `tmp_path`. Nothing here reads a real install or the real
data directory.

The owner's characters are known by the GUID the lab-addon stores (schema
5); the captured install's records are older, so the CLI tests set the
owner's GUID through `_owner_guids`, which is tested on its own against a
schema 5 record built as `test_m20_13_schema_5_views` builds one.
"""

# ruff: noqa: F811 - `make` is test_crewdb's fixture, imported and requested by name

from __future__ import annotations

import json
import os
import sqlite3
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import cli, crewdb, labaddon, worldpvp

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _crew_build import ACCOUNT, FLAVOR, KEY_A, KEY_B, KEY_C, KEY_D, book, player
from _world_lines import (
    ALLY_C,
    FOE_A,
    FOE_B,
    FOE_D,
    HEADER,
    OWNER_GUID,
    ally_hits,
    hits_you,
    log,
    you_die,
    you_hit,
    you_kill,
    zone,
)
from test_crewdb import Env, before_schema_7, make  # noqa: F401 - the fixture

LOG_NAME = "WoWCombatLog-040126_021630.txt"


@pytest.fixture
def owner(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    owners: dict[str, str | None] = {OWNER_GUID: None}
    monkeypatch.setattr(cli, "_owner_guids", lambda lay, account: (dict(owners), 0))
    return owners


def _book() -> bytes:
    return book({KEY_A: player(met=2), KEY_B: player(met=1)})


def _write_log(env: Env, data: bytes, name: str = LOG_NAME) -> Path:
    logs = env.flavor / "Logs"
    logs.mkdir(exist_ok=True)
    path = logs / name
    path.write_bytes(data)
    return path


def _fight() -> bytes:
    """You hit A, B hits you, C (friendly) hits A, you kill A, B kills you."""
    return log(
        you_hit(FOE_A, 10),
        hits_you(FOE_B, 11),
        ally_hits(ALLY_C, FOE_A, 12),
        you_kill(FOE_A, 13),
        hits_you(FOE_B, 14),
        you_die(15),
    )


def _second_log() -> bytes:
    """Another session's log (its first line a day later): a fight with D."""
    later = HEADER.replace("4/1/2026", "4/2/2026")
    return ("\r\n".join([later, zone(0), you_hit(FOE_D, 5000)]) + "\r\n").encode()


def _scan(env: Env, *args: str) -> Any:
    return env.run("crew", "scan-log", *args)


def _json(result: Any) -> dict[str, Any]:
    assert result.exit_code == 0, result.output
    loaded: dict[str, Any] = json.loads(result.stdout)
    return loaded


def _all_bytes(env: Env) -> list[tuple[str, bytes]]:
    folder = env.db().parent
    return [(f.name, f.read_bytes()) for f in sorted(folder.iterdir()) if f.is_file()]


# ─── the database step ───────────────────────────────────────────────────────


def test_a_new_database_and_a_migrated_one_have_the_same_world_tables_constructed(
    make: Any,
) -> None:
    env = make(_book())
    env.sync()
    conn = sqlite3.connect(env.db())
    try:
        made = dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE name LIKE 'world%'"))
    finally:
        conn.close()
    assert set(made) == {
        "world_logs",
        "world_logs_path",
        "world_logs_head",
        "world_encounters",
        "world_encounters_start",
        "world_encounter_players",
        "world_encounter_players_key",
    }
    step = [crewdb._MIGRATIONS[k] for k in sorted(k for k in crewdb._MIGRATIONS if k[0] == 3)]
    created = [s for s in crewdb._SQL_CREATE if " world_" in s]
    # M22-22: schema 6 adds `by_map` to `world_encounters`, so the 3 -> 4
    # step's statement is the one before it; with the 5 -> 6 step after it,
    # the columns are the new database's.
    assert [s for s in step if "TABLE world_encounters " not in s] == [
        s for s in created if "TABLE world_encounters " not in s
    ]
    later = [crewdb._MIGRATIONS[k] for k in sorted(k for k in crewdb._MIGRATIONS if k[0] == 5)]

    def columns(statements: list[str]) -> list[tuple[object, ...]]:
        mem = sqlite3.connect(":memory:")
        try:
            for statement in statements:
                mem.execute(statement)
            return mem.execute("PRAGMA table_info(world_encounters)").fetchall()
        finally:
            mem.close()

    assert columns(step + later) == columns(created)


def _as_schema_3(env: Env) -> None:
    """The database as main's schema 3 made it (M20-12): no world tables,
    user_version 3, its rows kept."""
    conn = sqlite3.connect(env.db(), isolation_level=None)
    try:
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute("BEGIN")
        # M21-02 added log_names at schema 5; schema 3 had none.
        for table in ("world_encounter_players", "world_encounters", "world_logs", "log_names"):
            conn.execute(f"DROP TABLE {table}")
        # M22-22's `by_map` went with world_encounters above.
        before_schema_7(conn)  # M21-12's columns, which schema 3 did not have
        conn.execute("PRAGMA user_version = 3")
        conn.execute("COMMIT")
    finally:
        conn.close()


def test_a_schema_3_database_is_copied_then_migrated_and_scanned_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    env.sync()
    _as_schema_3(env)
    _write_log(env, _fight())
    result = _scan(env)
    assert result.exit_code == 0, result.output
    pre = env.db().with_name(env.db().name + ".before-schema-3")
    conn = sqlite3.connect(pre)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert not any(n.startswith("world") for n in names)
        assert {r[0] for r in conn.execute("SELECT player_key FROM players")} == {KEY_A, KEY_B}
    finally:
        conn.close()
    conn = sqlite3.connect(env.db())
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == crewdb.SCHEMA == 8
        assert conn.execute("SELECT count(*) FROM world_encounters").fetchone()[0] == 1
    finally:
        conn.close()


# ─── scan-log ────────────────────────────────────────────────────────────────


def test_scan_log_keeps_the_encounter_and_the_players_constructed(make: Any, owner: Any) -> None:
    env = make(_book())
    _write_log(env, _fight())
    report = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json")))
    s = report.scan
    assert (s.state, s.how, s.read_from, s.fights) == ("scanned", "new", 0, 3)
    assert (s.encounters_added, s.opponents, s.allies, s.players_added) == (1, 2, 1, 3)
    assert report.characters == 1 and report.log == f"Logs/{LOG_NAME}"
    [e] = crewdb.world_encounters(FLAVOR, ACCOUNT)
    assert (e.foes, e.allies, e.your_kills, e.killed_you) == (
        [KEY_A, KEY_B],
        [KEY_C],
        [KEY_A],
        [KEY_B],
    )
    assert e.died and e.zone == "Tirisfal Glades" and e.owner_guid == OWNER_GUID
    rows = env.rows()
    assert (rows[KEY_A].world_faced, rows[KEY_A].world_kills, rows[KEY_A].world_deaths) == (1, 1, 0)
    assert (rows[KEY_B].world_faced, rows[KEY_B].world_deaths) == (1, 1)
    assert (rows[KEY_C].world_allied, rows[KEY_C].in_game_list) == (1, False)
    assert rows[KEY_C].guid == ALLY_C[0]
    # Never in the scoreboard's counts.
    assert all((r.faced, r.allied, r.met) == (0, 0, 0) for r in rows.values())
    assert crewdb.matches(FLAVOR, ACCOUNT) == []


def test_scan_log_text_says_what_its_json_says_constructed(make: Any, owner: Any) -> None:
    env = make(_book())
    _write_log(env, _fight())
    text = _scan(env)
    assert text.exit_code == 0, text.output
    gone = env.run("crew", "forget", "--all", "--yes")  # the same scan again, from nothing
    assert gone.exit_code == 0, gone.output
    report = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json")))
    shown = text.stdout.splitlines()
    assert shown[0] == report.words
    assert shown[1 : 1 + len(report.lines)] == report.lines
    assert "1 open-world encounter with 2 opponents and 1 ally" in report.words


def test_a_log_is_never_counted_twice_and_a_grown_one_continues_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    path = _write_log(env, _fight())
    first = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json"))).scan
    again = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json"))).scan
    assert (again.state, again.how, again.read_from) == ("unchanged", "continued", first.read_to)
    assert len(crewdb.world_encounters(FLAVOR, ACCOUNT)) == 1
    with path.open("ab") as f:  # the client appends, later
        f.write((you_hit(FOE_D, 2000) + "\r\n").encode())
    grown = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json"))).scan
    assert (grown.how, grown.read_from, grown.encounters_added) == ("continued", first.read_to, 1)
    assert [e.foes for e in crewdb.world_encounters(FLAVOR, ACCOUNT)] == [
        [KEY_D],
        [KEY_A, KEY_B],
    ]


def test_a_partial_last_line_waits_for_the_next_scan_constructed(make: Any, owner: Any) -> None:
    env = make(_book())
    whole = _fight()
    line = (you_hit(FOE_D, 2000) + "\r\n").encode()
    path = _write_log(env, whole + line[:40])
    first = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json"))).scan
    assert first.read_to == len(whole)
    path.write_bytes(whole + line)
    second = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json"))).scan
    assert (second.read_from, second.encounters_added) == (len(whole), 1)


def test_a_log_copied_elsewhere_is_the_same_log_constructed(
    make: Any, owner: Any, tmp_path: Path
) -> None:
    env = make(_book())
    path = _write_log(env, _fight())
    _json(_scan(env, "--json"))
    copy = tmp_path / "copied" / LOG_NAME
    copy.parent.mkdir()
    copy.write_bytes(path.read_bytes())
    moved = cli.CrewScanLogReport.model_validate(_json(_scan(env, str(copy), "--json"))).scan
    assert (moved.how, moved.encounters_added) == ("moved", 0)
    assert len(crewdb.world_encounters(FLAVOR, ACCOUNT)) == 1


def test_a_log_put_back_shorter_under_its_name_is_a_part_already_read_constructed(
    make: Any, owner: Any
) -> None:
    """The first line holds the session's start to the millisecond: the
    same first line, shorter, is an earlier copy of the same log."""
    env = make(_book())
    whole = _fight() + (you_hit(FOE_D, 2000) + "\r\n").encode()
    path = _write_log(env, whole)
    _json(_scan(env, "--json"))
    path.write_bytes(_fight())
    again = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json"))).scan
    assert (again.state, again.how, again.encounters_added) == ("unchanged", "already_read", 0)
    assert len(crewdb.world_encounters(FLAVOR, ACCOUNT)) == 2
    path.write_bytes(whole)  # and back again: nothing new
    third = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json"))).scan
    assert (third.how, third.encounters_added) == ("continued", 0)


def test_a_rewritten_log_is_a_new_log_constructed(make: Any, owner: Any) -> None:
    env = make(_book())
    path = _write_log(env, _fight())
    _json(_scan(env, "--json"))
    other = HEADER.replace("02:16:30.666", "03:00:00.000")
    path.write_bytes(("\r\n".join([other, zone(0), you_hit(FOE_D, 50)]) + "\r\n").encode())
    again = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json"))).scan
    assert (again.how, again.read_from, again.encounters_added) == ("restarted", 0, 1)


def test_scan_log_refuses_without_the_owners_game_id_constructed(
    make: Any, owner: dict[str, str | None]
) -> None:
    env = make(_book())
    _write_log(env, _fight())
    owner.clear()
    result = _scan(env)
    assert result.exit_code == 1
    flat = " ".join(result.output.split())
    assert "wowlab does not know which characters are yours" in flat
    assert "Log in once on each character with the lab-addon on" in flat
    assert not os.path.lexists(env.db())


def test_scan_log_with_no_log_names_the_fix_constructed(make: Any, owner: Any) -> None:
    env = make(_book())
    result = _scan(env)
    assert result.exit_code == 1
    assert "/combatlog" in result.output


def test_a_battleground_match_the_database_keeps_hides_fights_on_its_map_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    _write_log(env, log(zone(1, instance=572, name="Invented Ring"), you_hit(FOE_A, 10)))
    _json(_scan(env, "--json"))
    assert len(crewdb.world_encounters(FLAVOR, ACCOUNT)) == 1
    conn = sqlite3.connect(env.db())
    try:  # as a sync keeps a match the addon saw on that map
        conn.execute(
            "INSERT INTO matches (at, map, map_id, kind, won) VALUES (1795000000, 'x', 572, 'arena', 0)"
        )
        conn.commit()
    finally:
        conn.close()
    assert crewdb.world_encounters(FLAVOR, ACCOUNT) == []
    assert env.rows()[KEY_A].world_faced == 0


def test_a_sync_after_a_scan_is_not_held_constructed(make: Any, owner: Any) -> None:
    env = make(_book())
    _write_log(env, _fight())
    _json(_scan(env, "--json"))
    out = env.sync()
    assert out.state == "synced", out.words
    rows = env.rows()
    assert rows[KEY_A].met == 2 and rows[KEY_A].world_faced == 1 and rows[KEY_A].in_game_list
    assert rows[KEY_C].world_allied == 1 and not rows[KEY_C].in_game_list


def test_a_database_of_another_install_is_not_scanned_into_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    env.sync()
    conn = sqlite3.connect(env.db())
    try:
        conn.execute("UPDATE meta SET value = '/elsewhere' WHERE name = 'install_root'")
        conn.commit()
    finally:
        conn.close()
    _write_log(env, _fight())
    result = _scan(env)
    assert result.exit_code == 1
    assert "crew sync --adopt" in " ".join(result.output.split())
    assert crewdb.world_encounters(FLAVOR, ACCOUNT) == []


# ─── forget ──────────────────────────────────────────────────────────────────


def test_forget_leaves_no_key_and_no_game_id_in_the_database_or_its_copies_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    env.sync()  # a database, so the scan makes today's copy first
    _write_log(env, _fight())
    _json(_scan(env, "--json"))
    _write_log(env, _second_log(), name="WoWCombatLog-040226_000000.txt")
    _json(_scan(env, "--json"))  # a second write: today's copy holds the first scan
    assert crewdb.copies(FLAVOR, ACCOUNT)
    assert any(ALLY_C[0].encode() in data for _, data in _all_bytes(env))
    done = env.run("crew", "forget", KEY_C, "--yes")  # only the log named them
    assert done.exit_code == 0, done.output
    for name, data in _all_bytes(env):
        assert KEY_C.encode() not in data, name
        assert ALLY_C[0].encode() not in data, name
        assert b"Inventedc" not in data, name
    [*_, e] = crewdb.world_encounters(FLAVOR, ACCOUNT)
    assert e.allies == [] and e.foes == [KEY_A, KEY_B]
    done = env.run("crew", "forget", KEY_A, "--yes")  # in the book too
    assert done.exit_code == 0, done.output
    for name, data in _all_bytes(env):
        assert KEY_A.encode() not in data, name
        assert FOE_A[0].encode() not in data, name
    assert KEY_A not in env.rows()
    assert all(KEY_A not in e.foes + e.your_kills for e in crewdb.world_encounters(FLAVOR, ACCOUNT))


# ─── doctor ──────────────────────────────────────────────────────────────────


def test_doctor_counts_the_open_world_and_names_no_one_constructed(make: Any, owner: Any) -> None:
    env = make(_book())
    _write_log(env, _fight())
    _json(_scan(env, "--json"))
    snap = env.run_bare("snap", "create", "-m", "first", "--root", str(env.root))
    assert snap.exit_code == 0, snap.output
    for args in ((), ("--json",), ("--redact",)):
        result = env.run_bare("doctor", "--offline", "--root", str(env.root), *args)
        assert result.exit_code in (0, 1), result.output
        out = result.stdout + result.stderr
        for word in (KEY_A, KEY_B, KEY_C, "Inventeda", "Inventedb", "Inventedc", "Player-9998"):
            assert word not in out, (args, word)
        assert OWNER_GUID not in out
        if args == ("--json",):
            report = cli.DoctorReport.model_validate_json(result.stdout)
            [db] = [d for f in report.flavors for d in f.crew_databases]
            assert (db.world_encounters, db.world_opponents, db.world_logs) == (1, 2, 1)
        else:
            flat = " ".join(out.split())
            assert "1 open-world encounter with 2 opponents from 1 combat log read" in flat


# ─── list, show, matches ─────────────────────────────────────────────────────


def test_list_world_shows_only_players_met_in_the_open_world_constructed(
    make: Any, owner: Any
) -> None:
    env = make(book({KEY_A: player(met=2), KEY_D: player(met=1)}))
    _write_log(env, _fight())
    _json(_scan(env, "--json"))
    report = cli.CrewReport.model_validate(_json(env.run("crew", "list", "--world", "--json")))
    assert report.world and [p.key for p in report.players] == [KEY_A, KEY_B, KEY_C]
    text = env.run("crew", "list", "--world")
    assert text.exit_code == 0, text.output
    assert "3 of 4 players match." in text.stdout
    assert f"{KEY_A}: " in text.stdout and f"{KEY_D}: " not in text.stdout
    assert "in the open world against you 1, with you 0" in text.stdout
    plain = cli.CrewReport.model_validate(_json(env.run("crew", "list", "--json")))
    assert {p.key for p in plain.players} == {KEY_A, KEY_B, KEY_C, KEY_D}


def test_show_gives_the_world_record_in_text_and_json_constructed(make: Any, owner: Any) -> None:
    env = make(_book())
    _write_log(env, _fight())
    _json(_scan(env, "--json"))
    report = cli.CrewShowReport.model_validate(_json(env.run("crew", "show", KEY_B, "--json")))
    assert report.player is not None
    assert (report.player.world_faced, report.player.world_deaths) == (1, 1)
    text = env.run("crew", "show", KEY_B)
    assert text.exit_code == 0, text.output
    assert text.stdout.splitlines()[: len(report.lines)] == report.lines
    assert (
        "  Open-world encounters against you: 1 (your killing blows on them 0, theirs on you 1)"
        in report.lines
    )
    assert any(line.startswith("  First and latest in the open world: ") for line in report.lines)
    assert any("Tirisfal Glades" in line for line in report.lines)


def test_matches_world_lists_the_encounters_in_text_and_json_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    _write_log(env, _fight())
    _json(_scan(env, "--json"))
    report = cli.CrewMatchesReport.model_validate(
        _json(env.run("crew", "matches", "--world", "--json"))
    )
    assert report.world and report.matches == [] and report.total == 1
    [e] = report.encounters
    assert e.foes == [KEY_A, KEY_B] and e.allies == [KEY_C]
    text = env.run("crew", "matches", "--world")
    assert text.exit_code == 0, text.output
    assert text.stdout.splitlines()[0] == report.words
    assert text.stdout.splitlines()[1 : 1 + len(report.lines)] == report.lines
    assert report.lines[0].endswith(
        f"Tirisfal Glades (open world): your killing blow on {KEY_A}; you died to {KEY_B}"
    )
    assert report.lines[1:] == [f"    Against you: {KEY_A}, {KEY_B}", f"    With you: {KEY_C}"]
    one = cli.CrewMatchesReport.model_validate(
        _json(env.run("crew", "matches", KEY_C, "--world", "--json"))
    )
    assert one.player == KEY_C and one.total == 1
    plain = cli.CrewMatchesReport.model_validate(_json(env.run("crew", "matches", "--json")))
    assert plain.encounters == [] and not plain.world


# ─── who you are ─────────────────────────────────────────────────────────────


def test_the_owner_is_every_character_with_a_stored_game_id_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import test_m20_13_schema_5_views as v5

    record = v5._v5()
    older = types.SimpleNamespace(identity=None)  # an older addon's record: no game id
    files = [
        types.SimpleNamespace(record=record),
        types.SimpleNamespace(record=older),  # a record with no game id
        types.SimpleNamespace(record=None),  # unreadable: not counted
    ]
    monkeypatch.setattr(labaddon, "read_all", lambda lay, account=None: files)
    got = cli._owner_guids(None, ACCOUNT)  # type: ignore[arg-type]
    assert got == ({v5.GUID: "Invented Realm"}, 1)


def test_characters_with_no_game_id_are_named_in_text_and_json_constructed(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_owner_guids", lambda lay, account: ({OWNER_GUID: None}, 2))
    env = make(_book())
    _write_log(env, _fight())
    report = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json")))
    assert (report.characters, report.characters_without_id) == (1, 2)
    line = (
        "  2 characters of this game account have no game id stored yet, so their fights were "
        "not read: log in once on each with the lab-addon on."
    )
    assert line in report.lines
    gone = env.run("crew", "forget", "--all", "--yes")
    assert gone.exit_code == 0, gone.output
    text = _scan(env)
    assert line in text.stdout.splitlines()


def test_players_only_a_log_names_count_against_the_database_bound_constructed(
    make: Any, owner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(crewdb, "MAX_PLAYERS", 3)  # the book's A and B, and one more
    env = make(_book())
    _write_log(env, _fight())  # A, B, C (ally): C fits
    _write_log(env, _second_log(), name="WoWCombatLog-040226_000000.txt")
    first = cli.CrewScanLogReport.model_validate(
        _json(_scan(env, str(env.flavor / "Logs" / LOG_NAME), "--json"))
    ).scan
    assert first.players_left_out == 0
    second = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json"))).scan
    assert (second.players_added, second.players_left_out) == (0, 1)
    assert KEY_D not in env.rows() and len(env.rows()) == 3


def test_a_long_log_is_kept_a_bounded_part_at_a_time_constructed(
    make: Any, owner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worldpvp, "MAX_READ_PLAYERS", 1)
    gap = worldpvp.ENCOUNTER_GAP_SECONDS + 1
    env = make(_book())
    _write_log(env, log(*(you_hit(FOE_A, 10 + n * gap) for n in range(4))))
    report = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json")))
    s = report.scan
    assert (s.state, s.parts, s.encounters_added, s.read_from) == ("scanned", 4, 4, 0)
    assert len(crewdb.world_encounters(FLAVOR, ACCOUNT)) == 4
    assert "in 4 parts" in report.lines[0]
    again = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json"))).scan
    assert (again.state, again.encounters_added) == ("unchanged", 0)


def test_the_reader_uses_the_owners_realm_for_a_name_without_one_constructed() -> None:
    line = you_hit(("Player-9998-0000000A", "Inventeda"), 10)
    scan = worldpvp.scan_entries(
        worldpvp.combatlog.tokenize(log(line), events=worldpvp.WORLD_EVENTS),
        {OWNER_GUID: "Fake realm"},
    )
    assert [p.key for e in scan.encounters for p in e.players] == [
        "Inventeda-LabrealmbPartbPartcPartd"  # the owner's own unit name says it first
    ]


# ─── player copy ─────────────────────────────────────────────────────────────


def test_the_open_world_text_is_player_copy_constructed(make: Any, owner: Any) -> None:
    from test_cli_player_copy import Install as PlayerCopyInstall
    from test_cli_player_copy import hits

    env = make(_book())
    _write_log(env, _fight())
    says = PlayerCopyInstall.of(env.root)
    problems: list[str] = []
    for args in (
        ("crew", "scan-log", "--notes"),
        ("crew", "scan-log"),
        ("crew", "list", "--world", "--notes"),
        ("crew", "show", KEY_B),
        ("crew", "matches", "--world", "--notes"),
        ("crew", "scan-log", "--help"),
        ("crew", "matches", "--help"),
        ("crew", "list", "--help"),
    ):
        result = env.run(*args)
        assert result.exit_code == 0, result.output
        problems += [f"{' '.join(args)}: {h}" for h in hits(result.stdout + result.stderr, says)]
    assert problems == []


def test_a_hostile_bystander_kept_in_a_log_state_is_forgotten_cleanly_constructed(
    make: Any, owner: Any
) -> None:
    """A hostile player who only hit an NPC (never you) is kept by name and
    GUID in the log's saved state, to name a pet's owner later; no players
    row holds them, and `crew forget` reaches them all the same."""
    from _world_lines import HOSTILE, SPELL_DAMAGE_YOU_TO_NPC, _at, _owner_as

    env = make(_book())
    env.sync()  # a database first, so the scan also makes today's copy
    line = _at(_owner_as(SPELL_DAMAGE_YOU_TO_NPC, FOE_D, HOSTILE), 20)
    _write_log(env, log(line))
    _json(_scan(env, "--json"))
    _write_log(env, _second_log(), name="WoWCombatLog-040226_000000.txt")
    assert KEY_D not in env.rows()
    held = [n for n, data in _all_bytes(env) if FOE_D[0].encode() in data]
    assert held  # the saved state names them
    assert KEY_D in crewdb.holds(FLAVOR, ACCOUNT)
    done = env.run("crew", "forget", KEY_D, "--yes")
    assert done.exit_code == 0, done.output
    for name, data in _all_bytes(env):
        assert FOE_D[0].encode() not in data, name
        assert b"Inventedd" not in data, name
    assert KEY_D not in crewdb.holds(FLAVOR, ACCOUNT)


def test_a_friendly_bystander_is_not_kept_in_a_log_state_constructed(make: Any, owner: Any) -> None:
    from _world_lines import FRIENDLY, SPELL_DAMAGE_YOU_TO_NPC, _at, _owner_as

    env = make(_book())
    _write_log(env, log(_at(_owner_as(SPELL_DAMAGE_YOU_TO_NPC, ALLY_C, FRIENDLY), 20)))
    _json(_scan(env, "--json"))
    assert not [n for n, data in _all_bytes(env) if ALLY_C[0].encode() in data]


# ─── every log not read before (M20-19) ──────────────────────────────────────
#
# The lab-addon's combat-log switch starts a new log at each switch-on
# [verify]: `crew scan-log` with no PATH reads every log of the Logs folder
# not read before, oldest first, each once.

SECOND_NAME = "WoWCombatLog-040226_000000.txt"
THIRD_NAME = "WoWCombatLog-040326_000000.txt"


def _third_log() -> bytes:
    """A third session's log (its first line two days later): a fight with B."""
    later = HEADER.replace("4/1/2026", "4/3/2026")
    return ("\r\n".join([later, zone(0), hits_you(FOE_B, 9000)]) + "\r\n").encode()


def _aged(path: Path, seconds: int) -> None:
    os.utime(path, (seconds, seconds))


def test_scan_log_reads_every_log_not_read_before_oldest_first_constructed(
    make: Any, owner: Any
) -> None:
    env = make(_book())
    _aged(_write_log(env, _fight()), 1_000_000)
    first = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json")))
    assert (first.logs_read, first.logs_already_read, len(first.logs)) == (1, 0, 1)
    _aged(_write_log(env, _third_log(), name=THIRD_NAME), 3_000_000)
    _aged(_write_log(env, _second_log(), name=SECOND_NAME), 2_000_000)
    report = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json")))
    assert [f.log for f in report.logs] == [
        f"Logs/{LOG_NAME}",
        f"Logs/{SECOND_NAME}",
        f"Logs/{THIRD_NAME}",
    ]
    assert [f.scan.state for f in report.logs] == ["unchanged", "scanned", "scanned"]
    assert (report.logs_read, report.logs_already_read) == (2, 1)
    assert report.log == f"Logs/{THIRD_NAME}" and report.scan.encounters_added == 1
    # Each fight once: the first log's encounter, then D's and B's.
    assert [e.foes for e in crewdb.world_encounters(FLAVOR, ACCOUNT)] == [
        [KEY_B],
        [KEY_D],
        [KEY_A, KEY_B],
    ]
    again = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json")))
    assert (again.logs_read, again.logs_already_read) == (0, 3)
    assert len(crewdb.world_encounters(FLAVOR, ACCOUNT)) == 3


def test_scan_log_text_names_each_log_it_read_constructed(make: Any, owner: Any) -> None:
    env = make(_book())
    _aged(_write_log(env, _fight()), 1_000_000)
    _aged(_write_log(env, _second_log(), name=SECOND_NAME), 2_000_000)
    text = _scan(env)
    assert text.exit_code == 0, text.output
    shown = text.stdout.splitlines()
    assert shown[0].startswith("Read 2 combat logs of 2 in the Logs folder (0 read before")
    assert "2 open-world encounters added" in shown[0]
    assert any(line.startswith(f"  Logs/{LOG_NAME}: ") for line in shown)
    assert any(line.startswith(f"  Logs/{SECOND_NAME}: ") for line in shown)
    again = _scan(env)
    assert again.stdout.splitlines()[0].startswith(
        "Read 0 combat logs of 2 in the Logs folder (2 read before, nothing new)"
    )


def test_scan_log_with_a_path_reads_only_that_log_constructed(make: Any, owner: Any) -> None:
    env = make(_book())
    _aged(_write_log(env, _fight()), 1_000_000)
    second = _write_log(env, _second_log(), name=SECOND_NAME)
    _aged(second, 2_000_000)
    report = cli.CrewScanLogReport.model_validate(_json(_scan(env, str(second), "--json")))
    assert [f.log for f in report.logs] == [f"Logs/{SECOND_NAME}"]
    assert [e.foes for e in crewdb.world_encounters(FLAVOR, ACCOUNT)] == [[KEY_D]]


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="chmod 000 does not make a file unreadable here",
)
def test_scan_log_skips_and_names_a_log_it_cannot_read_constructed(make: Any, owner: Any) -> None:
    env = make(_book())
    _aged(_write_log(env, _fight()), 1_000_000)
    second = _write_log(env, _second_log(), name=SECOND_NAME)
    _aged(second, 2_000_000)
    _aged(_write_log(env, _third_log(), name=THIRD_NAME), 3_000_000)
    second.chmod(0)
    try:
        report = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json")))
        assert (report.logs_read, report.logs_unreadable) == (2, 1)
        [bad] = [f for f in report.logs if f.unreadable is not None]
        assert bad.log == f"Logs/{SECOND_NAME}" and bad.scan is None
        assert bad.unreadable is not None
        assert bad.unreadable.startswith("the file could not be opened: ")
        assert report.log == f"Logs/{THIRD_NAME}"
        text = _scan(env)
        assert text.exit_code == 0, text.output
        assert "1 could not be read" in text.stdout.splitlines()[0]
        assert any(f"Logs/{SECOND_NAME}: not read, skipped" in x for x in text.stdout.splitlines())
        # With PATH, the error stays an error.
        alone = _scan(env, str(second))
        assert alone.exit_code != 0 and "is not readable" in alone.output
    finally:
        second.chmod(0o644)
    later = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json")))
    assert (later.logs_read, later.logs_already_read, later.logs_unreadable) == (1, 2, 0)


def test_scan_log_says_how_many_older_logs_it_did_not_look_at_constructed(
    make: Any, owner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_MAX_LOGS", 2)
    env = make(_book())
    _aged(_write_log(env, _fight()), 1_000_000)
    _aged(_write_log(env, _second_log(), name=SECOND_NAME), 2_000_000)
    _aged(_write_log(env, _third_log(), name=THIRD_NAME), 3_000_000)
    report = cli.CrewScanLogReport.model_validate(_json(_scan(env, "--json")))
    assert report.logs_not_looked_at == 1
    assert [f.log for f in report.logs] == [f"Logs/{SECOND_NAME}", f"Logs/{THIRD_NAME}"]
    text = _scan(env).stdout.splitlines()
    assert "  1 older log not looked at: only the newest 2 are." in text
