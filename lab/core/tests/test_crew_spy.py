"""Spy feeds the PvP book (M22-02, docs/LAB_PLAN.md §21.3, ADR-0039).

`crew sync` (and `crew list` and `crew show`, which sync first) takes what
the Spy addon saved for each of the account's characters into wowlab's
crew database: each sighting, the kill-on-sight mark, Spy's own counts and
the fights its `Encounters` list kept, every value as Spy's own. A player
only Spy saw gets a players row, named by Spy's key (`names_from` `spy`)
and placed in the crew book's region (`region_from` `book`).

Two kinds of input:

- **The real capture** (tests named `capture`): the M22-01 capture's three
  `Spy.lua` files (`fixtures/macos-71037/forever/WTF/Account/`, provenance
  rows in `fixtures/README.md`), dropped into the constructed install of
  `test_crewdb.py` (a copy of `fixtures/macos-70888/`, whose account and
  `1/Sandgraal-Rizzen` character folders match the capture's).
- **Constructed** (tests named `constructed`): crew books from
  `parser/_crew_build.py`, Spy files built here in the capture's layout
  with invented names, and databases changed by hand to reach what the
  capture does not hold (a kill-on-sight entry, Spy's counts, a fight, an
  older schema).

No test reads a real install or the real data directory; every Spy file is
the same bytes after every run (Spy's files are never written).
"""

# ruff: noqa: F811 - `make` is an imported fixture, requested by name

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import crewdb, nemesis

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _crew_build import ACCOUNT, FLAVOR, KEY_A, T1, book, player
from test_crewdb import Env, before_schema_8, make  # noqa: F401 - the fixture

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE_ACCOUNTS = FIXTURES / "macos-71037" / "forever" / "WTF" / "Account"
SANDGRAAL = f"WTF/Account/{ACCOUNT}/1/Sandgraal-Rizzen/SavedVariables/Spy.lua"
TYRONE = f"WTF/Account/{ACCOUNT}/1/Tyrone-Hamberglar/SavedVariables/Spy.lua"
SEEN_BY = "1/Sandgraal-Rizzen"
NL = "\r\n"

# From the capture (`Spy.lua` of Sandgraal-Rizzen, 24 rows).
GORUTHUZ = "Goruthuz-Mugstout"  # The Barrens, Ratchet, map 1413 at 0.62, 0.37
SONJA = "Sonja-Frayle"  # an instance: Wailing Caverns, "" subzone, no map fields
TALA = "Tala-Kattéa"  # a name with a non-ASCII letter
CAPTURED_ROWS = 24

# Invented (constructed) Spy keys, in Forever's `<First>-<Second>` form.
SPY_A = "Inventspy-Fakesecond"
SPY_B = "Inventspyb-Fakesecond"
TS = 1_795_400_000  # a Spy `time`, before the capture's


def _with_spy(env: Env) -> Env:
    """The capture's Spy files in the constructed install (only Spy.lua
    files: the book's WowLab.lua is never replaced)."""
    shutil.copytree(CAPTURE_ACCOUNTS, env.flavor / "WTF" / "Account", dirs_exist_ok=True)
    return env


def _spy_bytes(env: Env) -> dict[str, bytes]:
    """Every Spy file of the install, by its path, as its bytes."""
    return {str(p.relative_to(env.flavor)): p.read_bytes() for p in env.flavor.rglob("Spy.lua")}


def _q(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _lua(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return _q(str(value))


def _row(key: str, **fields: Any) -> dict[str, Any]:
    """A constructed `PlayerData` row, in the capture's fields."""
    row: dict[str, Any] = {
        "name": key,
        "class": "ROGUE",
        "race": "Gnome",
        "level": 29,
        "faction": "Alliance",
        "isEnemy": True,
        "isGuess": False,
        "zone": "Invented Vale",
        "subZone": "Invented Hill",
        "mapID": 1413,
        "mapX": 0.5,
        "mapY": 0.25,
        "guild": "Invented Guild of Testing",
        "time": TS,
    }
    row.update(fields)
    return {k: v for k, v in row.items() if v is not None}


def _spy_file(
    rows: dict[str, dict[str, Any]],
    kos: dict[str, int] | None = None,
    fights: list[dict[str, Any]] | None = None,
) -> bytes:
    """A constructed `SpyPerCharDB` in the capture's layout (CRLF, a
    leading blank line, bracketed keys)."""
    lines = ["", "SpyPerCharDB = {", '["version"] = "1.1",', '["PlayerData"] = {']
    for key, row in rows.items():
        lines.append(f"[{_q(key)}] = {{")
        lines += [f"[{_q(k)}] = {_lua(v)}," for k, v in row.items()]
        lines.append("},")
    lines += ["},", '["KOSData"] = {']
    lines += [f"[{_q(k)}] = {v}," for k, v in (kos or {}).items()]
    lines += ["},", '["IgnoreData"] = {', "},", '["Encounters"] = {']
    for fight in fights or []:
        lines.append("{")
        lines += [f"[{_q(k)}] = {_lua(v)}," for k, v in fight.items()]
        lines.append("},")
    lines += ["},", "}"]
    return (NL.join(lines) + NL).encode("utf-8")


def _put(env: Env, rel: str, data: bytes) -> None:
    path = env.flavor / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _sync(env: Env) -> dict[str, Any]:
    result = env.run("crew", "sync", "--json")
    assert result.exit_code == 0, result.output
    report: dict[str, Any] = json.loads(result.stdout)
    return report


def _players(env: Env, *args: str) -> dict[str, dict[str, Any]]:
    result = env.run("crew", "list", "--json", *args)
    assert result.exit_code == 0, result.output
    return {p["key"]: p for p in json.loads(result.stdout)["players"]}


def _show(env: Env, key: str) -> dict[str, Any]:
    result = env.run("crew", "show", key, "--json")
    assert result.exit_code == 0, result.output
    report: dict[str, Any] = json.loads(result.stdout)
    return report


def _db_rows(env: Env, statement: str, *args: Any) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(env.db())
    try:
        return [tuple(r) for r in conn.execute(statement, args).fetchall()]
    finally:
        conn.close()


def _book3() -> bytes:
    """A schema 3 book that recorded the region US (M21-12), with one
    invented player."""
    return book({KEY_A: player()}, schema=3, region="US", region_at=T1, matches=[])


# ─── the capture ─────────────────────────────────────────────────────────────


def test_a_sync_takes_every_row_of_the_capture_as_sightings_capture(make: Any) -> None:
    env = _with_spy(make(_book3()))
    before = _spy_bytes(env)
    report = _sync(env)
    spy = report["spy"]
    assert spy["state"] == "synced", spy
    # Two characters' files hold `SpyPerCharDB` (Tyrone's with every table
    # empty); the account file holds `SpyDB` only.
    assert spy["characters"] == 2 and spy["files_unread"] == 0 and spy["left_out"] == 0
    assert spy["sightings_added"] == CAPTURED_ROWS and spy["players_added"] == CAPTURED_ROWS
    assert spy["fights_added"] == 0 and spy["marks_changed"] == CAPTURED_ROWS
    assert "Spy" in spy["words"] and "24 new sightings" in spy["words"]
    rows = _db_rows(env, "SELECT count(*), count(DISTINCT player_key) FROM spy_sightings")
    assert rows == [(CAPTURED_ROWS, CAPTURED_ROWS)]
    assert _spy_bytes(env) == before  # Spy's files are never written


def test_a_sighting_keeps_what_spy_saw_with_its_source_capture(make: Any) -> None:
    env = _with_spy(make(_book3()))
    seen = _players(env)[GORUTHUZ]["spy"]
    assert seen == {
        "source": "spy",
        "last_seen": 1795410775,
        "seen_by": SEEN_BY,
        "zone": "The Barrens",
        "sub_zone": "Ratchet",
        "map_id": 1413,
        "map_x": 0.62,
        "map_y": 0.37,
        "level": 43,
        "level_guessed": False,
        "class_token": "PALADIN",
        "race": "Dwarf",
        "faction": "Alliance",
        "guild": "Untamed Legacy",
        "is_enemy": True,
        "sightings": 1,
        "kos": False,
        "kos_added": None,
        "wins": 0,
        "losses": 0,
        "in_spy": True,
    }


def test_an_instance_row_keeps_its_zone_and_no_map_capture(make: Any) -> None:
    env = _with_spy(make(_book3()))
    seen = _players(env)[SONJA]["spy"]
    assert (seen["zone"], seen["sub_zone"]) == ("Wailing Caverns", "")
    assert seen["map_id"] is None and seen["map_x"] is None and seen["map_y"] is None
    assert seen["guild"] == "Doomhammer" and seen["faction"] == "Horde"


def test_a_spy_only_player_gets_a_row_named_by_spys_key_capture(make: Any) -> None:
    env = _with_spy(make(_book3()))
    rows = _players(env)
    assert len(rows) == CAPTURED_ROWS + 1  # the book's one player, and Spy's 24
    tala = rows[TALA]
    assert tala["in_game_list"] is False
    assert (tala["first_name"], tala["second_name"], tala["realm"]) == ("Tala", "Kattéa", None)
    assert tala["game_name"] == "Tala Kattéa" and tala["names_from"] == "spy"
    # The crew book's recorded region first (amendment of 2026-11-24).
    assert (tala["region"], tala["region_from"]) == ("us", "book")
    assert tala["region_recorded_at"] == T1
    # The book's own player is not one Spy saw.
    assert rows[KEY_A]["spy"] is None and rows[KEY_A]["names_from"] == "key"


def test_without_a_recorded_region_a_spy_only_row_takes_the_settings_capture(
    make: Any,
) -> None:
    env = _with_spy(make(book({KEY_A: player()})))
    tala = _players(env)[TALA]
    assert (tala["region"], tala["region_from"]) == ("us", "settings")
    assert tala["names_from"] == "spy"


def test_a_resync_is_idempotent_and_writes_nothing_capture(make: Any) -> None:
    env = _with_spy(make(_book3()))
    _sync(env)
    first = env.db_sha()
    again = _sync(env)
    assert again["spy"]["state"] == "unchanged", again["spy"]
    assert again["spy"]["sightings_added"] == 0 and again["spy"]["players_added"] == 0
    assert env.db_sha() == first
    _players(env)  # crew list syncs too
    _show(env, GORUTHUZ)
    assert env.db_sha() == first
    assert _db_rows(env, "SELECT count(*) FROM spy_sightings") == [(CAPTURED_ROWS,)]


def test_the_seen_by_spy_filter_and_order_capture(make: Any) -> None:
    env = _with_spy(make(_book3()))
    result = env.run("crew", "list", "--json", "--spy")
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["spy"] is True and report["sort"] == "spy"
    keys = [p["key"] for p in report["players"]]
    assert len(keys) == CAPTURED_ROWS and KEY_A not in keys
    lasts = [p["spy"]["last_seen"] for p in report["players"]]
    assert lasts == sorted(lasts, reverse=True)
    # An explicit --sort wins, as for every view.
    named = json.loads(env.run("crew", "list", "--json", "--spy", "--sort", "name").stdout)
    assert named["sort"] == "name"


def test_list_and_show_say_what_spy_saw_capture(make: Any) -> None:
    env = _with_spy(make(_book3()))
    listed = env.run("crew", "list")
    assert listed.exit_code == 0, listed.output
    line = next(x for x in listed.stdout.splitlines() if x.startswith(f"  {GORUTHUZ}:"))
    assert "PALADIN 43 (as Spy saw them)" in line
    assert "seen by Spy" in line and "The Barrens, Ratchet (map 1413 at 62, 37)" in line
    shown = _show(env, GORUTHUZ)
    lines = shown["lines"]
    assert any(x.startswith("  Seen by Spy: last on ") and "Sandgraal-Rizzen" in x for x in lines)
    assert (
        "  Where you stood when Spy last placed them: The Barrens, Ratchet (map 1413 at 62, 37)"
        in lines
    )
    assert any(
        x.startswith("  As Spy last saw them: level 43, PALADIN, Dwarf, Alliance")
        and x.endswith("hostile to you: yes")
        for x in lines
    )
    assert "  On Spy's kill-on-sight list: no" in lines
    assert shown["player"]["names_from"] == "spy" and shown["spy_sync"]["state"] == "unchanged"


def test_a_book_never_turned_on_takes_nothing_from_spy_capture(make: Any) -> None:
    env = _with_spy(make(None))
    report = _sync(env)
    assert report["spy"] is None and report["sync"] is None
    assert not env.db().exists()


# ─── constructed: new sightings, kill on sight, counts ───────────────────────


def test_a_new_spy_time_adds_a_sighting_and_moves_the_latest_constructed(make: Any) -> None:
    env = make(_book3())
    _put(env, SANDGRAAL, _spy_file({SPY_A: _row(SPY_A)}))
    assert _sync(env)["spy"]["sightings_added"] == 1
    _put(env, SANDGRAAL, _spy_file({SPY_A: _row(SPY_A, time=TS + 600, zone="Invented Hold")}))
    later = _sync(env)["spy"]
    assert (later["sightings_added"], later["players_added"]) == (1, 0)
    seen = _players(env)[SPY_A]["spy"]
    assert seen["sightings"] == 2 and seen["last_seen"] == TS + 600
    assert seen["zone"] == "Invented Hold"
    assert _db_rows(env, "SELECT at FROM spy_sightings ORDER BY at") == [(TS,), (TS + 600,)]


def test_kill_on_sight_and_spys_counts_constructed(make: Any) -> None:
    env = make(_book3())
    kos = {SPY_A: TS + 5, SPY_B: TS + 9}  # SPY_B is on the list with no row
    _put(env, SANDGRAAL, _spy_file({SPY_A: _row(SPY_A, wins=2, loses=1)}, kos=kos))
    _sync(env)
    rows = _players(env)
    a, b = rows[SPY_A]["spy"], rows[SPY_B]["spy"]
    assert (a["kos"], a["kos_added"], a["wins"], a["losses"]) == (True, TS + 5, 2, 1)
    assert (b["kos"], b["kos_added"], b["sightings"], b["last_seen"]) == (True, TS + 9, 0, None)
    assert rows[SPY_B]["names_from"] == "spy"
    lines = _show(env, SPY_A)["lines"]
    assert "  On Spy's kill-on-sight list: yes, since " + _day(TS + 5) in lines
    assert (
        "  Spy's own counts: your killing blows on them 2, your deaths it put down to them 1"
        in lines
    )
    line = next(x for x in env.run("crew", "list").stdout.splitlines() if SPY_A + ":" in x)
    assert "kill on sight in Spy" in line


def _day(seconds: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(seconds))


def test_spys_counts_add_up_and_are_never_lowered_constructed(make: Any) -> None:
    env = make(_book3())
    for wins in (2, 3, 1, 2):  # 1 after 3: Spy dropped them and started again
        _put(env, SANDGRAAL, _spy_file({SPY_A: _row(SPY_A, wins=wins)}))
        _sync(env)
    assert _players(env)[SPY_A]["spy"]["wins"] == 3 + 1 + 1


def test_off_the_list_and_dropped_by_spy_constructed(make: Any) -> None:
    env = make(_book3())
    _put(env, SANDGRAAL, _spy_file({SPY_A: _row(SPY_A, wins=2)}, kos={SPY_A: TS}))
    _sync(env)
    _put(env, SANDGRAAL, _spy_file({SPY_A: _row(SPY_A, wins=2)}))  # taken off the list
    _sync(env)
    seen = _players(env)[SPY_A]["spy"]
    assert (seen["kos"], seen["kos_added"], seen["in_spy"]) == (False, None, True)
    _put(env, SANDGRAAL, _spy_file({}))  # Spy dropped them
    _sync(env)
    gone = _players(env)[SPY_A]
    assert gone["spy"]["in_spy"] is False and gone["spy"]["wins"] == 2
    assert gone["spy"]["sightings"] == 1  # what Spy saw stays
    lines = _show(env, SPY_A)["lines"]
    assert "  In Spy's list now: no; Spy dropped them, and wowlab keeps what it saw" in lines


def test_a_file_that_cannot_be_read_changes_nothing_of_its_character_constructed(
    make: Any,
) -> None:
    env = make(_book3())
    _put(env, SANDGRAAL, _spy_file({SPY_A: _row(SPY_A, wins=2)}, kos={SPY_A: TS}))
    _sync(env)
    _put(env, SANDGRAAL, f"{NL}SpyPerCharDB = loadstring('x')(){NL}".encode())
    report = _sync(env)["spy"]
    assert report["files_unread"] == 1 and "could not be read" in report["words"]
    seen = _players(env)[SPY_A]["spy"]
    assert seen["kos"] is True and seen["in_spy"] is True and seen["wins"] == 2


def test_rows_outside_what_a_sync_takes_are_left_out_and_counted_constructed(
    make: Any,
) -> None:
    env = make(_book3())
    rows = {
        "Nohyphen": _row("Nohyphen"),
        SPY_A: _row(SPY_A),
        SPY_B: _row(SPY_B, guild="x" * (crewdb.TEXT_MAX_BYTES + 1)),
    }
    fights = [{"t": TS, "n": SPY_A, "r": "?"}, {"n": SPY_A, "r": "W"}]
    _put(env, SANDGRAAL, _spy_file(rows, fights=fights))
    report = _sync(env)["spy"]
    assert report["left_out"] == 4 and report["sightings_added"] == 1
    assert "4 entries of Spy's data were outside what wowlab takes" in report["words"]
    assert set(_players(env, "--spy")) == {SPY_A}


def test_players_past_the_bound_are_left_out_with_all_spy_saw_constructed(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make(_book3())
    env.sync()  # the book's one player
    monkeypatch.setattr(crewdb, "MAX_PLAYERS", 2)
    _put(env, SANDGRAAL, _spy_file({SPY_A: _row(SPY_A), SPY_B: _row(SPY_B)}))
    report = _sync(env)["spy"]
    assert (report["players_added"], report["players_left_out"]) == (1, 1)
    assert _db_rows(env, "SELECT DISTINCT player_key FROM spy_sightings") == [(SPY_A,)]


# ─── constructed: the record ─────────────────────────────────────────────────


def _fights() -> list[dict[str, Any]]:
    return [
        {"t": TS + 10, "n": SPY_A, "r": "L", "k": "recap"},
        {"t": TS + 20, "n": SPY_A, "r": "L", "k": "targeting"},
        {"t": TS + 30, "n": SPY_A, "r": "L", "k": "recap"},
        {"t": TS + 40, "n": SPY_A, "r": "W", "k": "kill"},
        {"t": TS + 50, "n": SPY_A, "r": "P", "k": "probable"},
    ]


def test_spys_fights_feed_the_record_and_the_nemesis_rule_constructed(make: Any) -> None:
    env = make(_book3())
    _put(env, SANDGRAAL, _spy_file({SPY_A: _row(SPY_A)}, fights=_fights()))
    assert _sync(env)["spy"]["fights_added"] == 5
    record = _players(env)[SPY_A]["record"]
    assert record["fights"] == 5 and record["sources"] == {"spy": 5}
    assert record["personal"] == {"kills": 1, "deaths": 3, "nemesis": True}
    assert record["team"] == {"wins": 0, "losses": 0, "rival": False}
    assert (record["first"], record["last"]) == (TS + 10, TS + 50)
    shown = _show(env, SPY_A)
    fights = shown["fights"]
    assert [(f["source"], f["at"], f["kills"], f["deaths"]) for f in fights] == [
        ("spy", TS + 10, 0, 1),
        ("spy", TS + 20, 0, 1),
        ("spy", TS + 30, 0, 1),
        ("spy", TS + 40, 1, 0),
        ("spy", TS + 50, 0, 0),
    ]
    # Every count is traced to its rows: the record is the fights added up.
    assert nemesis.record_of(nemesis.Fight.model_validate(f) for f in fights) == (
        nemesis.Record.model_validate(record)
    )
    assert any("counted by Spy" in x for x in shown["lines"])
    nemeses = _players(env, "--nemeses")
    assert list(nemeses) == [SPY_A]
    # Taken once: a second sync adds no fight.
    assert _sync(env)["spy"]["fights_added"] == 0
    assert _players(env)[SPY_A]["record"]["fights"] == 5


def test_a_fight_the_combat_log_also_kept_is_counted_once_constructed(make: Any) -> None:
    env = make(_book3())
    near = crewdb.SPY_SAME_FIGHT_SECONDS
    fights = [
        {"t": TS + 10, "n": SPY_A, "r": "L", "k": "recap"},  # inside the encounter
        {"t": TS + 20 + near, "n": SPY_A, "r": "L", "k": "recap"},  # at the window's edge
        {"t": TS + 21 + near, "n": SPY_A, "r": "W", "k": "kill"},  # just past it
    ]
    _put(env, SANDGRAAL, _spy_file({SPY_A: _row(SPY_A)}, fights=fights))
    _sync(env)
    # A constructed open-world encounter, as `crew scan-log` keeps one, in
    # which SPY_A fought you from TS + 5 to TS + 20 and killed you.
    conn = sqlite3.connect(env.db(), isolation_level=None)
    try:
        conn.execute(
            "INSERT INTO world_logs (path, head_sha256, size, read_to, scanned_at)"
            " VALUES ('invented.txt', 'x', 1, 1, 1)"
        )
        conn.execute(
            "INSERT INTO world_encounters (log_id, owner_guid, start_at, end_at, zone,"
            " instance_id, died, by_map) VALUES (1, 'Player-1-00000001', ?, ?, 'Invented Vale',"
            " NULL, 1, 1)",
            (TS + 5, TS + 20),
        )
        conn.execute(
            "INSERT INTO world_encounter_players (encounter_id, player_key, guid, side, hits,"
            " killed_by_you, killed_you) VALUES (1, ?, NULL, 'foe', 3, 0, 1)",
            (SPY_A,),
        )
    finally:
        conn.close()
    record = _players(env)[SPY_A]["record"]
    assert record["sources"] == {"spy": 1, "world": 1}
    assert record["personal"] == {"kills": 1, "deaths": 1, "nemesis": False}
    found = crewdb.fights(FLAVOR, ACCOUNT, SPY_A)
    assert [(f.source, f.at) for f in found] == [("world", TS + 5), ("spy", TS + 21 + near)]


# ─── constructed: forget ─────────────────────────────────────────────────────


def test_forget_drops_the_spy_rows_and_never_writes_spys_file_constructed(make: Any) -> None:
    env = make(_book3())
    _put(env, SANDGRAAL, _spy_file({SPY_A: _row(SPY_A, wins=1)}, kos={SPY_A: TS}, fights=_fights()))
    _sync(env)
    _sync(env)  # a second day's copy is the same file: copies are rewritten too
    spy_before = (env.flavor / SANDGRAAL).read_bytes()
    result = env.run("crew", "forget", SPY_A, "--yes", "--json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["database_forgotten"] is True and report["spy_keeps"] == [SPY_A]
    for table in ("players", "spy_sightings", "spy_players", "spy_fights"):
        assert _db_rows(env, f"SELECT count(*) FROM {table} WHERE player_key = ?", SPY_A) == [
            (0,)
        ], table
    for f in [env.db(), *crewdb.copies(FLAVOR, ACCOUNT)]:
        assert SPY_A.encode() not in f.read_bytes(), f
    assert (env.flavor / SANDGRAAL).read_bytes() == spy_before


def test_forget_says_spy_still_keeps_the_player_constructed(make: Any) -> None:
    env = make(_book3())
    _put(env, SANDGRAAL, _spy_file({SPY_A: _row(SPY_A)}))
    _sync(env)
    result = env.run("crew", "forget", SPY_A, "--yes")
    assert result.exit_code == 0, result.output
    assert "The Spy addon still keeps this player in its own saved data" in result.stdout
    # And the next sync takes them again, from Spy.
    assert _sync(env)["spy"]["players_added"] == 1


def test_a_spy_held_player_is_never_dropped_as_held_by_nothing_constructed(make: Any) -> None:
    env = make(_book3())
    _put(env, SANDGRAAL, _spy_file({SPY_A: _row(SPY_A)}))
    _sync(env)
    conn = sqlite3.connect(env.db(), isolation_level=None)
    try:
        gone = conn.execute(crewdb._SQL_DROP_LOG_ONLY_PLAYER_8, (SPY_A,)).rowcount
    finally:
        conn.close()
    assert gone == 0 and SPY_A in _players(env)
    (summary,) = crewdb.summaries(FLAVOR)
    assert summary.players == 2 and summary.not_in_game_list == 1


# ─── the schema ──────────────────────────────────────────────────────────────


def test_the_migration_statements_are_the_create_statements() -> None:
    created = [s for s in crewdb._SQL_CREATE if "spy_" in s]
    stepped = [crewdb._MIGRATIONS[k] for k in sorted(crewdb._MIGRATIONS) if k[0] == 7]
    assert created == stepped and len(created) == 6
    assert crewdb.SCHEMA == 8


def test_a_schema_7_database_gains_the_spy_tables_after_a_copy_constructed(
    make: Any,
) -> None:
    env = _with_spy(make(_book3()))
    env.sync()
    conn = sqlite3.connect(env.db())
    try:
        conn.execute("BEGIN")
        before_schema_8(conn)
        conn.execute("PRAGMA user_version = 7")
        conn.execute("END")
    finally:
        conn.close()
    assert len(env.rows()) == 1  # the open migrates
    copy = env.db().with_name(env.db().name + ".before-schema-7")
    assert copy.exists()
    assert _db_rows(env, "PRAGMA user_version") == [(8,)]
    assert _db_rows(env, "SELECT count(*) FROM spy_sightings") == [(0,)]
    assert _sync(env)["spy"]["sightings_added"] == CAPTURED_ROWS


def test_the_sync_sentence_quotes_no_name_capture(make: Any) -> None:
    env = _with_spy(make(_book3()))
    result = env.run("crew", "sync")
    assert result.exit_code == 0, result.output
    spy_line = result.stdout.splitlines()[-1]
    assert spy_line.startswith("Took Spy's saved data into wowlab's crew database: 24 new")
    for key in (GORUTHUZ, SONJA, TALA):
        assert key not in result.stdout


def test_two_rows_naming_one_player_at_one_time_sync_once_constructed(make: Any) -> None:
    env = make(_book3())
    # Two table keys whose `name` is the same player, seen at the same time.
    rows = {SPY_A: _row(SPY_A), SPY_A.lower(): _row(SPY_A, zone="Invented Hold")}
    _put(env, SANDGRAAL, _spy_file(rows))
    report = _sync(env)["spy"]
    assert report["state"] == "synced" and report["sightings_added"] == 1
    assert _db_rows(env, "SELECT count(*) FROM spy_sightings") == [(1,)]
