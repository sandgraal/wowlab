"""The record against each opponent at the desk (M22-03, docs/LAB_PLAN.md
§21.3): from the matches and open-world encounters wowlab's crew database
keeps, each player's fights, first and latest fight, and two parts kept
apart (owner ruling, 2026-11-23): killing blows each way and the nemesis
mark, and team results and the rival-team mark; in `crew list`, `crew list
--nemeses` and `--rivals`, `crew show` and their `--json`; every count
traced to the source rows it was added up from.

CONSTRUCTED INPUT, every byte: crew books from `parser/_crew_build.py` with
the four invented keys, open-world encounters built here as a combat-log
scan would hand them to the database (and, once, a constructed log from
`parser/_world_lines.py` read by `crew scan-log`), databases made at run
time under a user data directory redirected into `tmp_path`. ADR-0036 term
3 keeps every real crew record out of the repository. Nothing here reads a
real install or the real data directory.
"""

# ruff: noqa: F811 - `make` is test_crewdb's fixture, imported and requested by name

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import cli, crewdb, nemesis, worldpvp

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _crew_build import ACCOUNT, FLAVOR, KEY_A, KEY_B, KEY_C, KEY_D, MAP_ID, T2, book, match, player
from _world_lines import FOE_A, FOE_B, OWNER_GUID, hits_you, log, you_die, you_hit, you_kill
from test_crewdb import Env, make  # noqa: F401 - the fixture

ZONE = "Invented Test Woods"


def _matches() -> list[dict[str, Any]]:
    """Six matches (constructed): C is against you in five (lost, lost,
    lost with D, won, no winner); a solo shuffle puts C and D with you and
    no one against you."""
    return [
        match([KEY_A], [KEY_C], at=T2, won=False),
        match([KEY_A], [KEY_C], at=T2 + 30, won=False),
        match([KEY_A], [KEY_C, KEY_D], at=T2 + 60, won=False),
        match([], [KEY_C], at=T2 + 120, won=True),
        match([KEY_A], [KEY_C], at=T2 + 180, won=None),
        match([KEY_A, KEY_C, KEY_D], [], at=T2 + 240, won=None, kind="shuffle"),
    ]


def _book() -> bytes:
    players = {k: player() for k in (KEY_A, KEY_B, KEY_C, KEY_D)}
    return book(players, schema=2, matches=_matches())


def _p(
    key: str, side: worldpvp.Side, *, by_you: bool = False, you: bool = False
) -> worldpvp.WorldPlayer:
    name, realm = key.split("-")
    return worldpvp.WorldPlayer(
        key=key,
        name=name,
        realm=realm,
        guid=f"Player-9998-0000000{key[7].upper()}",
        side=side,
        hits=2,
        killed_by_you=by_you,
        killed_you=you,
    )


def _encounter(
    at: int, players: list[worldpvp.WorldPlayer], instance_id: int | None = 0
) -> worldpvp.WorldEncounter:
    return worldpvp.WorldEncounter(
        owner_guid=OWNER_GUID,
        start=at,
        end=at + 30,
        zone=ZONE,
        instance_id=instance_id,
        died=any(p.killed_you for p in players),
        players=players,
    )


def _encounters() -> list[worldpvp.WorldEncounter]:
    """Four open-world encounters (constructed): C kills you while you kill
    D, with B beside you; you and C trade killing blows; D fights you with
    no killing blow; and C kills you on the battleground's own map, which is
    not the open world."""
    return [
        _encounter(
            T2 + 1000,
            [_p(KEY_C, "foe", you=True), _p(KEY_D, "foe", by_you=True), _p(KEY_B, "ally")],
        ),
        _encounter(T2 + 2000, [_p(KEY_C, "foe", by_you=True, you=True)]),
        _encounter(T2 + 3000, [_p(KEY_D, "foe")]),
        _encounter(T2 + 4000, [_p(KEY_C, "foe", you=True)], instance_id=MAP_ID),
    ]


def _world(env: Env, encounters: list[worldpvp.WorldEncounter], n: int = 0) -> None:
    """Keep the encounters as one scan of log `n` would."""
    scan = worldpvp.WorldScan(start=0, end=100, state=worldpvp.ZoneState(), encounters=encounters)
    resume = crewdb.WorldResume(start=0, how="new")
    out = crewdb.record_world(
        env.target(), f"Logs/invented-{n}.txt", 100, str(n) * 64, resume, scan
    )
    assert out.state == "scanned", out.words


def _built(make: Any) -> Env:
    env: Env = make(_book())
    out = env.sync()
    assert out.state == "synced" and out.matches_left_out == 0, out.words
    _world(env, _encounters())
    return env


def _rows_of(env: Env, key: str) -> set[tuple[str, int]]:
    """The source rows that put `key` against you, read from the database's
    own tables: (source, row id)."""
    conn = sqlite3.connect(env.db())
    try:
        in_matches = conn.execute(
            "SELECT match_id FROM match_players WHERE player_key = ? AND side = 'foes'", (key,)
        ).fetchall()
        in_world = conn.execute(
            "SELECT p.encounter_id FROM world_encounter_players AS p"
            " JOIN world_encounters AS e ON e.encounter_id = p.encounter_id"
            " WHERE p.player_key = ? AND p.side = 'foe' AND e.instance_id != ?",
            (key, MAP_ID),
        ).fetchall()
    finally:
        conn.close()
    return {("match", int(r[0])) for r in in_matches} | {("world", int(r[0])) for r in in_world}


# ─── the record, from the database ───────────────────────────────────────────


def test_each_record_adds_up_the_matches_and_encounters_kept_constructed(make: Any) -> None:
    env = _built(make)
    rows = env.rows()
    c, d = rows[KEY_C].record, rows[KEY_D].record
    # C: five matches (lost, lost, lost, won, no winner), the death and the trade.
    assert (c.fights, c.first, c.last) == (7, T2, T2 + 2000)
    assert c.sources == {"match": 5, "world": 2}
    assert c.team == nemesis.Team(wins=1, losses=3, rival=True)
    assert c.personal == nemesis.Personal(kills=1, deaths=2, nemesis=False)
    # D: the lost match, your killing blow, the fight with none.
    assert (d.fights, d.first, d.last) == (3, T2 + 60, T2 + 3000)
    assert d.sources == {"match": 1, "world": 2}
    assert d.team == nemesis.Team(wins=0, losses=1, rival=False)
    assert d.personal == nemesis.Personal(kills=1, deaths=0, nemesis=False)
    # A was only ever with you, B only beside you: no fight.
    assert rows[KEY_A].record == nemesis.Record() and rows[KEY_B].record == nemesis.Record()


@pytest.mark.parametrize("key", [KEY_A, KEY_B, KEY_C, KEY_D])
def test_every_count_is_traced_to_its_source_rows_constructed(make: Any, key: str) -> None:
    env = _built(make)
    record = env.rows()[key].record
    fights = crewdb.fights(FLAVOR, ACCOUNT, key)
    # The fights are exactly the rows the database's own tables hold for them.
    assert {(f.source, f.row) for f in fights} == _rows_of(env, key)
    assert len(fights) == len({(f.source, f.row) for f in fights}) == record.fights
    # And the record is those fights added up.
    assert record == nemesis.record_of(fights)
    assert record.team.losses == sum(f.team == "loss" for f in fights)
    assert record.team.wins == sum(f.team == "win" for f in fights)
    assert record.personal.kills == sum(f.kills for f in fights)
    assert record.personal.deaths == sum(f.deaths for f in fights)
    # A match gives only its team result; an encounter only killing blows.
    kept = {m.at: m.won for m in crewdb.matches(FLAVOR, ACCOUNT)}
    for f in fights:
        if f.source == "match":
            assert f.team == nemesis.team_result(kept[f.at]) and (f.kills, f.deaths) == (0, 0)
        else:
            assert f.team is None


def test_an_encounter_on_a_kept_match_map_is_no_fight_constructed(make: Any) -> None:
    env = _built(make)
    fights = crewdb.fights(FLAVOR, ACCOUNT, KEY_C)
    assert T2 + 4000 not in {f.at for f in fights}
    assert env.rows()[KEY_C].record.personal.deaths == 2  # not 3


def test_no_database_means_no_fights_constructed(make: Any) -> None:
    make(_book())
    assert crewdb.fights(FLAVOR, ACCOUNT) == []


def test_a_forgotten_player_has_no_fights_left_constructed(make: Any) -> None:
    env = _built(make)
    result = env.run("crew", "forget", KEY_C, "--yes")
    assert result.exit_code == 0, result.output
    assert crewdb.fights(FLAVOR, ACCOUNT, KEY_C) == []
    assert KEY_C not in env.rows()
    assert env.rows()[KEY_D].record.fights == 3  # the others keep theirs


# ─── the commands ────────────────────────────────────────────────────────────


def _json(env: Env, *args: str) -> dict[str, Any]:
    result = env.run(*args, "--json")
    assert result.exit_code == 0, result.output
    loaded: dict[str, Any] = json.loads(result.stdout)
    return loaded


def _d_kills_you_three_times(env: Env) -> None:
    """Three more deaths to D, from another log: D becomes a nemesis."""
    _world(
        env,
        [_encounter(T2 + 5000 + i * 100, [_p(KEY_D, "foe", you=True)]) for i in range(3)],
        1,
    )


def test_list_json_carries_the_record_on_every_row_constructed(make: Any) -> None:
    env = _built(make)
    report = cli.CrewReport.model_validate(_json(env, "crew", "list"))
    by_key = {p.key: p for p in report.players}
    assert set(by_key) == {KEY_A, KEY_B, KEY_C, KEY_D}
    for key, p in by_key.items():
        assert p.record == nemesis.record_of(crewdb.fights(FLAVOR, ACCOUNT, key))
    assert (report.nemeses, report.rivals) == (False, False)
    assert (report.nemesis_rule, report.rival_rule) == (nemesis.NEMESIS_RULE, nemesis.RIVAL_RULE)


def test_nemeses_and_rivals_are_two_lists_constructed(make: Any) -> None:
    env = _built(make)
    _d_kills_you_three_times(env)
    nemeses = cli.CrewReport.model_validate(_json(env, "crew", "list", "--nemeses"))
    assert nemeses.nemeses is True
    # D killed you 3 times and you killed D once: a nemesis, not a rival team.
    assert [(p.key, p.record.personal.deaths) for p in nemeses.players] == [(KEY_D, 3)]
    rivals = cli.CrewReport.model_validate(_json(env, "crew", "list", "--rivals"))
    assert rivals.rivals is True
    # C's side beat yours 3 times to 1: a rival team, not a nemesis (2 deaths, 1 kill).
    assert [(p.key, p.record.team.losses) for p in rivals.players] == [(KEY_C, 3)]
    both = cli.CrewReport.model_validate(_json(env, "crew", "list", "--nemeses", "--rivals"))
    assert both.players == []
    text = env.run("crew", "list", "--nemeses")
    assert text.exit_code == 0, text.output
    lines = text.stdout.splitlines()
    assert "1 of 4 players match." in lines
    (row,) = [line for line in lines if line.startswith("  Invented")]
    assert row.startswith(f"  {KEY_D}: ")
    assert (
        "record against them: 6 fights; they killed you 3, you killed them 1; "
        "team won 0, lost 1; your nemesis" in row
    )
    assert "rival team" not in row


def test_the_two_sorts_constructed(make: Any) -> None:
    env = _built(make)
    _d_kills_you_three_times(env)
    by_deaths = cli.CrewReport.model_validate(_json(env, "crew", "list", "--sort", "deaths"))
    assert [p.key for p in by_deaths.players] == [KEY_D, KEY_C, KEY_A, KEY_B]
    by_team = cli.CrewReport.model_validate(_json(env, "crew", "list", "--sort", "team_losses"))
    assert [p.key for p in by_team.players] == [KEY_C, KEY_D, KEY_A, KEY_B]


def test_list_text_shows_the_record_and_the_marks_constructed(make: Any) -> None:
    env = _built(make)
    result = env.run("crew", "list")
    assert result.exit_code == 0, result.output
    row = {line.split(":")[0].strip(): line for line in result.stdout.splitlines()}
    assert (
        "record against them: 7 fights; they killed you 2, you killed them 1; "
        "team won 1, lost 3; rival team" in row[KEY_C]
    )
    assert "your nemesis" not in row[KEY_C]
    assert "record against them: 3 fights" in row[KEY_D]
    assert "nemesis" not in row[KEY_D] and "rival" not in row[KEY_D]
    assert "record against them" not in row[KEY_A]


def test_show_text_and_json_carry_the_record_and_its_rows_constructed(make: Any) -> None:
    env = _built(make)
    report = cli.CrewShowReport.model_validate(_json(env, "crew", "show", KEY_C))
    assert report.player is not None
    assert report.player.record == nemesis.record_of(report.fights)
    assert report.fights == crewdb.fights(FLAVOR, ACCOUNT, KEY_C)
    assert {(f.source, f.row) for f in report.fights} == _rows_of(env, KEY_C)
    assert (report.nemesis_rule, report.rival_rule) == (nemesis.NEMESIS_RULE, nemesis.RIVAL_RULE)
    result = env.run("crew", "show", KEY_C)
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[: len(report.lines)] == report.lines
    assert any(
        line.startswith("  Record against them: 7 fights (5 in matches, 2 in the open world)")
        for line in lines
    )
    assert "  Killing blows: theirs on you 2, yours on them 1" in lines
    assert f"  Your nemesis: no ({nemesis.NEMESIS_RULE})" in lines
    assert "  Team results in matches against them: your side won 1, their side won 3" in lines
    assert f"  Rival team: yes ({nemesis.RIVAL_RULE})" in lines
    nobody = cli.CrewShowReport.model_validate(_json(env, "crew", "show", KEY_A))
    assert nobody.fights == [] and "  Record against them: no fight kept" in nobody.lines


def test_both_rules_are_in_the_help_constructed(make: Any) -> None:
    env = make(_book())
    result = env.run("crew", "list", "--help")
    assert result.exit_code == 0, result.output
    flat = " ".join(result.stdout.split())
    assert "--nemeses" in flat and nemesis.NEMESIS_RULE in flat
    assert "--rivals" in flat and nemesis.RIVAL_RULE in flat
    assert "deaths: most killed by them first" in flat
    assert "team_losses: most team losses to them first" in flat


# ─── from a combat log, end to end ───────────────────────────────────────────


@pytest.fixture
def owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_owner_guids", lambda lay, account: ({OWNER_GUID: None}, 0))


def test_a_scanned_log_gives_the_personal_record_constructed(make: Any, owner: Any) -> None:
    """You hit A, B hits you, you kill A, B kills you: a killing blow on A,
    a death to B, each one open-world fight with no team result."""
    env = make(book({KEY_A: player(), KEY_B: player()}))
    logs = env.flavor / "Logs"
    logs.mkdir()
    (logs / "WoWCombatLog-040126_021630.txt").write_bytes(
        log(you_hit(FOE_A, 10), hits_you(FOE_B, 11), you_kill(FOE_A, 13), you_die(15))
    )
    result = env.run("crew", "scan-log")
    assert result.exit_code == 0, result.output
    rows = env.rows()
    a, b = rows[KEY_A].record, rows[KEY_B].record
    assert (a.fights, a.personal.kills, a.personal.deaths) == (1, 1, 0)
    assert (b.fights, b.personal.kills, b.personal.deaths) == (1, 0, 1)
    assert a.team == b.team == nemesis.Team()
    assert a.sources == b.sources == {"world": 1}
