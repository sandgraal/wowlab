"""The record against each opponent and the nemesis rule (M22-03,
docs/LAB_PLAN.md §21.3): `nemesis`, pure, on constructed fights.

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
    result: nemesis.Result | None = None,
    kills: int = 0,
    deaths: int = 0,
) -> nemesis.Fight:
    return nemesis.Fight(
        key=key, source=source, row=row, at=at, result=result, kills=kills, deaths=deaths
    )


@pytest.mark.parametrize(
    ("wins", "losses", "expected"),
    [
        (0, 0, False),
        (0, 2, False),  # beaten twice: not yet
        (0, 3, True),  # beaten three times, never beaten them
        (2, 3, True),
        (3, 3, False),  # as often as you beat them: not more often
        (4, 3, False),
        (5, 6, True),
    ],
)
def test_the_rule_is_three_losses_and_more_losses_than_wins_constructed(
    wins: int, losses: int, expected: bool
) -> None:
    assert nemesis.is_nemesis(wins, losses) is expected


def test_the_rule_in_words_says_the_numbers_the_rule_uses() -> None:
    assert nemesis.NEMESIS_LOSSES == 3
    assert "at least 3 times" in nemesis.RULE
    assert "more often than you have beaten them" in nemesis.RULE


def test_a_match_is_decided_by_its_result_constructed() -> None:
    assert nemesis.match_result(True) == "win"
    assert nemesis.match_result(False) == "loss"
    assert nemesis.match_result(None) is None


@pytest.mark.parametrize(
    ("killed_by_you", "killed_you", "expected"),
    [(True, False, "win"), (False, True, "loss"), (True, True, None), (False, False, None)],
)
def test_an_open_world_fight_is_decided_by_a_death_constructed(
    killed_by_you: bool, killed_you: bool, expected: str | None
) -> None:
    assert nemesis.world_result(killed_by_you, killed_you) == expected


def test_a_record_adds_up_its_fights_and_nothing_else_constructed() -> None:
    fights = [
        _fight(row=1, at=T + 300, result="loss"),
        _fight(row=2, at=T, result="win"),
        _fight(row=3, at=T + 100, result=None),
        _fight(source="world", row=1, at=T + 900, result="loss", deaths=1),
        _fight(source="world", row=2, at=T + 50, result=None, kills=1, deaths=1),
        _fight(source="world", row=3, at=T + 60, result="loss", deaths=1),
    ]
    r = nemesis.record_of(fights)
    assert (r.fights, r.wins, r.losses, r.kills, r.deaths) == (6, 1, 3, 1, 3)
    assert (r.first, r.last) == (T, T + 900)
    assert r.sources == {"match": 3, "world": 3}
    assert r.nemesis is True
    # Each count is the number of fights that carry it.
    assert r.wins == sum(f.result == "win" for f in fights)
    assert r.losses == sum(f.result == "loss" for f in fights)
    assert r.kills == sum(f.kills for f in fights)
    assert r.deaths == sum(f.deaths for f in fights)


def test_no_fight_is_the_empty_record_constructed() -> None:
    assert nemesis.record_of([]) == nemesis.Record()
    assert nemesis.Record().nemesis is False and nemesis.Record().first is None


def test_records_split_by_player_and_take_any_number_of_sources_constructed() -> None:
    matches = [_fight(KEY_C, row=1, result="loss"), _fight(KEY_A, row=1, result="loss")]
    world = [_fight(KEY_C, "world", row=7, result="loss", deaths=1)]
    third = [_fight(KEY_C, "world", row=8, result="loss", deaths=1)]  # a later source's list
    got = nemesis.records(matches, world, third)
    assert set(got) == {KEY_A, KEY_C}
    assert (got[KEY_C].fights, got[KEY_C].losses, got[KEY_C].nemesis) == (3, 3, True)
    assert (got[KEY_A].fights, got[KEY_A].losses, got[KEY_A].nemesis) == (1, 1, False)
    # One list or several give the same records.
    assert nemesis.records([*matches, *world, *third]) == got


def test_the_nemeses_order_is_most_losses_then_fewest_wins_then_key_constructed() -> None:
    r = nemesis.Record
    keyed = [
        ("Inventedb-Fakerealm", r(losses=3, wins=2)),
        ("Inventedd-Otherfakerealm", r(losses=5, wins=0)),
        (KEY_C, r(losses=3, wins=0)),
        (KEY_A, r(losses=3, wins=0)),
    ]
    ordered = sorted(keyed, key=lambda kr: nemesis.nemesis_order(kr[1], kr[0]))
    assert [k for k, _ in ordered] == [
        "Inventedd-Otherfakerealm",
        KEY_A,
        KEY_C,
        "Inventedb-Fakerealm",
    ]
