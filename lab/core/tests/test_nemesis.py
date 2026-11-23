"""The record against each opponent, the nemesis rule and the rival-team
rule (M22-03, docs/LAB_PLAN.md §21.3): `nemesis`, pure, on constructed
fights.

CONSTRUCTED INPUT: every fight is built here from invented keys; nothing is
read from an install, a database or a log.
"""

from __future__ import annotations

import pytest

from wowlab_core import nemesis

KEY_A = "Inventeda-Fakerealm"
KEY_C = "Inventedc-Otherfakerealm"
T = 1_795_650_000


def _fight(
    key: str = KEY_C,
    source: nemesis.Source = "match",
    row: int = 1,
    at: int = T,
    team: nemesis.Result | None = None,
    kills: int = 0,
    deaths: int = 0,
) -> nemesis.Fight:
    return nemesis.Fight(
        key=key, source=source, row=row, at=at, team=team, kills=kills, deaths=deaths
    )


@pytest.mark.parametrize(
    ("kills", "deaths", "expected"),
    [
        (0, 0, False),
        (0, 2, False),  # killed you twice: not yet
        (0, 3, True),  # killed you three times, never killed by you
        (2, 3, True),
        (3, 3, False),  # as often as you killed them: not more often
        (4, 3, False),
        (5, 6, True),
    ],
)
def test_the_nemesis_rule_is_three_deaths_and_more_deaths_than_kills_constructed(
    kills: int, deaths: int, expected: bool
) -> None:
    assert nemesis.is_nemesis(kills, deaths) is expected


@pytest.mark.parametrize(
    ("wins", "losses", "expected"),
    [(0, 2, False), (0, 3, True), (2, 3, True), (3, 3, False), (4, 3, False)],
)
def test_the_rival_rule_is_three_team_losses_and_more_losses_than_wins_constructed(
    wins: int, losses: int, expected: bool
) -> None:
    assert nemesis.is_rival(wins, losses) is expected


def test_each_rule_in_words_says_the_numbers_it_uses() -> None:
    assert nemesis.NEMESIS_DEATHS == 3 and nemesis.RIVAL_LOSSES == 3
    assert "killed you at least 3 times" in nemesis.NEMESIS_RULE
    assert "more often than you have killed them" in nemesis.NEMESIS_RULE
    assert "at least 3 battleground or arena matches" in nemesis.RIVAL_RULE
    assert "more often than your side has beaten theirs" in nemesis.RIVAL_RULE


def test_a_match_gives_its_team_result_constructed() -> None:
    assert nemesis.team_result(True) == "win"
    assert nemesis.team_result(False) == "loss"
    assert nemesis.team_result(None) is None


def test_a_record_keeps_the_personal_and_team_parts_apart_constructed() -> None:
    fights = [
        _fight(row=1, at=T + 300, team="loss"),
        _fight(row=2, at=T, team="win"),
        _fight(row=3, at=T + 100, team=None),
        _fight(row=4, at=T + 200, team="loss"),
        _fight(row=5, at=T + 250, team="loss"),
        _fight(source="world", row=1, at=T + 900, deaths=1),
        _fight(source="world", row=2, at=T + 50, kills=1, deaths=1),  # a trade counts both
    ]
    r = nemesis.record_of(fights)
    assert (r.fights, r.first, r.last) == (7, T, T + 900)
    assert r.sources == {"match": 5, "world": 2}
    assert r.personal == nemesis.Personal(kills=1, deaths=2, nemesis=False)
    assert r.team == nemesis.Team(wins=1, losses=3, rival=True)
    # Each count is the number the fights carry.
    assert r.team.wins == sum(f.team == "win" for f in fights)
    assert r.team.losses == sum(f.team == "loss" for f in fights)
    assert r.personal.kills == sum(f.kills for f in fights)
    assert r.personal.deaths == sum(f.deaths for f in fights)


def test_team_losses_never_make_a_nemesis_nor_deaths_a_rival_constructed() -> None:
    lost_matches = [_fight(row=n, team="loss") for n in range(10)]
    r = nemesis.record_of(lost_matches)
    assert r.team.rival is True and r.personal.nemesis is False
    deaths = [_fight(source="world", row=n, deaths=1) for n in range(10)]
    r = nemesis.record_of(deaths)
    assert r.personal.nemesis is True and r.team.rival is False


def test_a_later_source_of_kill_pairs_is_one_more_list_of_fights_constructed() -> None:
    """A battleground's kill pairs (a later ticket) are personal fights like
    the open world's: here two lists stand in for two sources."""
    world = [_fight(source="world", row=1, deaths=1), _fight(source="world", row=2, deaths=1)]
    more = [_fight(source="world", row=3, deaths=1)]
    got = nemesis.records(world, more)
    assert got[KEY_C].personal == nemesis.Personal(kills=0, deaths=3, nemesis=True)


def test_no_fight_is_the_empty_record_constructed() -> None:
    empty = nemesis.Record()
    assert nemesis.record_of([]) == empty
    assert not empty.personal.nemesis and not empty.team.rival and empty.first is None


def test_records_split_by_player_and_take_any_number_of_sources_constructed() -> None:
    matches = [_fight(KEY_C, row=1, team="loss"), _fight(KEY_A, row=1, team="loss")]
    world = [_fight(KEY_C, "world", row=7, deaths=1)]
    got = nemesis.records(matches, world)
    assert set(got) == {KEY_A, KEY_C}
    assert (got[KEY_C].fights, got[KEY_C].team.losses, got[KEY_C].personal.deaths) == (2, 1, 1)
    assert (got[KEY_A].fights, got[KEY_A].team.losses) == (1, 1)
    assert nemesis.records([*matches, *world]) == got


def test_the_orders_constructed() -> None:
    def rec(kills: int = 0, deaths: int = 0, wins: int = 0, losses: int = 0) -> nemesis.Record:
        return nemesis.Record(
            personal=nemesis.Personal(kills=kills, deaths=deaths),
            team=nemesis.Team(wins=wins, losses=losses),
        )

    keyed = [
        ("Inventedb-Fakerealm", rec(kills=2, deaths=3, wins=0, losses=5)),
        ("Inventedd-Otherfakerealm", rec(deaths=5, wins=1, losses=1)),
        (KEY_C, rec(deaths=3, losses=3, wins=2)),
        (KEY_A, rec(deaths=3, losses=3)),
    ]
    by_deaths = sorted(keyed, key=lambda kr: nemesis.nemesis_order(kr[1], kr[0]))
    assert [k for k, _ in by_deaths] == [
        "Inventedd-Otherfakerealm",
        KEY_A,
        KEY_C,
        "Inventedb-Fakerealm",
    ]
    by_team = sorted(keyed, key=lambda kr: nemesis.rival_order(kr[1], kr[0]))
    assert [k for k, _ in by_team] == [
        "Inventedb-Fakerealm",
        KEY_A,
        KEY_C,
        "Inventedd-Otherfakerealm",
    ]
