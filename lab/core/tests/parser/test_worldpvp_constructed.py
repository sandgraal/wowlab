"""`worldpvp` on constructed PvP lines (M20-18). CONSTRUCTED INPUT.

The real log holds no other player, so every PvP case is graded here, on
lines built by `_world_lines` from the real log's own lines (an NPC or the
owner swapped for an invented player). What each test pins is the rule the
module states; what the client writes in such a fight is not yet confirmed
by any capture.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _world_lines import (
    ALLY_C,
    FOE_A,
    FOE_B,
    FOE_D,
    FRIENDLY,
    HEADER,
    OWNER_GUID,
    REAL,
    ZONE,
    ally_hits,
    hits_you,
    log,
    pet_hits,
    you_die,
    you_hit,
    you_kill,
    zone,
)

from wowlab_core import combatlog, worldpvp

OWNERS = {OWNER_GUID: None}


def _scan(data: bytes, **kw: object) -> worldpvp.WorldScan:
    return worldpvp.scan_entries(
        combatlog.tokenize(data, events=worldpvp.WORLD_EVENTS), OWNERS, **kw
    )  # type: ignore[arg-type]


@pytest.mark.parser
def test_constructed_one_fight_both_ways_is_one_encounter() -> None:
    scan = _scan(log(you_hit(FOE_A, 10), hits_you(FOE_A, 12), you_hit(FOE_A, 14)))
    assert scan.fights == 3
    [e] = scan.encounters
    assert (e.owner_guid, e.zone, e.instance_id, e.died) == (
        OWNER_GUID,
        "Tirisfal Glades",
        0,
        False,
    )
    assert e.end - e.start == 4
    [p] = e.players
    assert (p.key, p.name, p.realm, p.guid, p.side, p.hits) == (
        "Inventeda-Fakerealm",
        "Inventeda",
        "Fakerealm",
        FOE_A[0],
        "foe",
        3,
    )


@pytest.mark.parser
def test_constructed_the_same_player_after_the_gap_is_a_second_encounter() -> None:
    gap = worldpvp.ENCOUNTER_GAP_SECONDS
    scan = _scan(
        log(you_hit(FOE_A, 10), you_hit(FOE_A, 10 + gap), you_hit(FOE_A, 11 + 2 * gap + 1))
    )
    assert [len(e.players) for e in scan.encounters] == [1, 1]
    assert scan.encounters[0].end - scan.encounters[0].start == gap


@pytest.mark.parser
def test_constructed_two_players_at_once_are_one_encounter_and_later_ones_are_not() -> None:
    join = worldpvp.JOIN_SECONDS
    scan = _scan(
        log(
            you_hit(FOE_A, 10),
            hits_you(FOE_B, 10 + join),  # joins: within the join window
            you_hit(FOE_D, 10 + 3 * join),  # after it: an encounter of its own
        )
    )
    assert [[p.key for p in e.players] for e in scan.encounters] == [
        ["Inventeda-Fakerealm", "Inventedb-Fakerealm"],
        ["Inventedd-Otherfakerealm"],
    ]


@pytest.mark.parser
def test_constructed_a_friendly_player_on_your_opponent_is_an_ally() -> None:
    scan = _scan(
        log(you_hit(FOE_A, 10), ally_hits(ALLY_C, FOE_A, 11), ally_hits(ALLY_C, FOE_A, 12))
    )
    [e] = scan.encounters
    sides = {p.key: (p.side, p.hits) for p in e.players}
    assert sides == {"Inventeda-Fakerealm": ("foe", 1), "Inventedc-Otherfakerealm": ("ally", 2)}


@pytest.mark.parser
def test_constructed_a_friendly_player_with_no_encounter_open_is_nobody() -> None:
    assert _scan(log(ally_hits(ALLY_C, FOE_A, 11))).encounters == []


@pytest.mark.parser
def test_constructed_a_friendly_player_you_hit_is_no_fight() -> None:
    scan = _scan(log(you_hit(FOE_A, 10, flags=FRIENDLY)))
    assert (scan.fights, scan.encounters) == (0, [])


@pytest.mark.parser
def test_constructed_your_death_goes_to_the_last_player_who_hit_you() -> None:
    scan = _scan(log(hits_you(FOE_A, 10), hits_you(FOE_B, 12), you_die(13)))
    [e] = scan.encounters
    assert e.died
    killed = {p.key: p.killed_you for p in e.players}
    assert killed == {"Inventeda-Fakerealm": False, "Inventedb-Fakerealm": True}


@pytest.mark.parser
def test_constructed_a_death_long_after_the_last_hit_is_nobodys_kill() -> None:
    late = 10 + worldpvp.DEATH_WINDOW_SECONDS + 1
    [e] = _scan(log(hits_you(FOE_A, 10), you_die(late))).encounters
    assert e.died and not any(p.killed_you for p in e.players)


@pytest.mark.parser
def test_constructed_your_killing_blow_is_kept() -> None:
    [e] = _scan(log(you_hit(FOE_A, 10), you_kill(FOE_A, 11))).encounters
    assert [p.killed_by_you for p in e.players] == [True]
    assert not e.died


@pytest.mark.parser
def test_constructed_your_pet_by_its_flags_and_by_its_owner_field() -> None:
    by_owner_field = _scan(log(pet_hits(FOE_A, 10, pet_flags="0x2111")))  # guardian
    assert [p.key for e in by_owner_field.encounters for p in e.players] == ["Inventeda-Fakerealm"]
    # Before any line shows who logs, a pet is known by its owner field.
    by_flags = _scan(log(you_hit(FOE_B, 5), pet_hits(FOE_A, 10)))
    assert [p.key for p in by_flags.encounters[0].players] == [
        "Inventeda-Fakerealm",
        "Inventedb-Fakerealm",
    ]


@pytest.mark.parser
def test_constructed_a_battleground_by_difficulty_or_by_a_kept_match_is_not_world() -> None:
    bg = zone(5, instance=489, name="Invented Gulch", difficulty=34)
    scan = _scan(log(bg, you_hit(FOE_A, 10)))
    assert (scan.fights, scan.outside_world, scan.encounters) == (0, 1, [])
    by_match = _scan(
        log(zone(5, instance=489, name="Invented Gulch"), you_hit(FOE_A, 10)), pvp_maps=[489]
    )
    assert (by_match.fights, by_match.outside_world) == (0, 1)
    back = _scan(log(bg, you_hit(FOE_A, 10), zone(20), you_hit(FOE_A, 30)))
    assert (back.fights, back.outside_world, len(back.encounters)) == (1, 1, 1)


@pytest.mark.parser
def test_constructed_an_arena_span_is_not_world() -> None:
    start = f"{you_hit(FOE_A, 9).split('  ')[0]}  ARENA_MATCH_START,1505,0,2v2,0"
    end = f"{you_hit(FOE_A, 20).split('  ')[0]}  ARENA_MATCH_END,0,30,1500,1500"
    scan = _scan(log(start, you_hit(FOE_A, 10), end, you_hit(FOE_A, 21)))
    assert (scan.fights, scan.outside_world) == (1, 1)


@pytest.mark.parser
def test_constructed_a_fight_before_any_zone_line_is_not_counted() -> None:
    data = ("\r\n".join([HEADER, you_hit(FOE_A, 10)]) + "\r\n").encode()
    scan = _scan(data)
    assert (scan.fights, scan.no_zone, scan.encounters) == (0, 1, [])


@pytest.mark.parser
def test_constructed_a_line_still_being_written_is_left_for_the_next_read() -> None:
    data = log(you_hit(FOE_A, 10))
    partial = you_hit(FOE_B, 11).encode()[:50]
    scan = _scan(data + partial)
    assert scan.end == len(data)
    assert [p.key for e in scan.encounters for p in e.players] == ["Inventeda-Fakerealm"]


@pytest.mark.parser
def test_constructed_an_overlong_line_is_never_a_fight() -> None:
    long = you_hit(FOE_A, 10) + "," + "9" * combatlog.MAX_LINE_BYTES
    data = log(long)
    scan = _scan(data)
    assert (scan.fights, scan.unparsed, scan.end) == (0, 1, len(data))


@pytest.mark.parser
def test_constructed_the_side_cap_counts_the_rest() -> None:
    lines = [
        you_hit((f"Player-9998-{n:08X}", "Inventeda-Fakerealm-"), 10)
        for n in range(worldpvp.SIDE_MAX + 5)
    ]
    [e] = _scan(log(*lines)).encounters
    assert (len(e.players), e.players_left_out) == (worldpvp.SIDE_MAX, 5)


@pytest.mark.parser
def test_constructed_another_characters_guid_is_not_you() -> None:
    scan = worldpvp.scan_entries(
        combatlog.tokenize(log(you_hit(FOE_A, 10)), events=worldpvp.WORLD_EVENTS),
        {"Player-9999-0000000F": None},
    )
    assert scan.encounters == []


@pytest.mark.parser
@pytest.mark.parametrize(
    ("name", "home", "want"),
    [
        ("Inventeda-Fakerealm-", None, "Inventeda-Fakerealm"),
        ("Inventeda-Fakerealm-EU", None, "Inventeda-Fakerealm"),
        ("Inventeda-Fakerealm", None, "Inventeda-Fakerealm"),
        ("Inventeda", "Fakerealm", "Inventeda-Fakerealm"),
        ("Inventeda--", "Fakerealm", "Inventeda-Fakerealm"),
        ("Inventeda", None, None),
        ("-Fakerealm-", None, None),
        ("Invent\x07eda-Fakerealm-", None, None),
    ],
    ids=[
        "three-parts",
        "region",
        "two-parts",
        "no-realm",
        "empty-realm",
        "no-home",
        "no-name",
        "control",
    ],
)
def test_constructed_unit_names_to_keys(name: str, home: str | None, want: str | None) -> None:
    got = worldpvp.player_key(name, home)
    assert (got[0] if got else None) == want


@pytest.mark.parser
def test_constructed_a_continued_read_carries_the_zone(tmp_path: Path) -> None:
    bg = zone(5, instance=489, name="Invented Gulch", difficulty=34)
    data = log(bg) + (you_hit(FOE_A, 10) + "\r\n").encode()
    path = tmp_path / "WoWCombatLog-040126_021630.txt"
    path.write_bytes(data)
    head = worldpvp.read_world(path, OWNERS)
    cut = len(log(bg))
    first = worldpvp.scan_entries(
        combatlog.tokenize(data[:cut], events=worldpvp.WORLD_EVENTS), OWNERS
    )
    rest = worldpvp.read_world(path, OWNERS, start=first.end, state=first.state)
    assert first.end == cut
    assert (head.outside_world, rest.outside_world, rest.fights) == (1, 1, 0)


@pytest.mark.parser
def test_constructed_the_open_cap_closes_the_oldest() -> None:
    owners = {f"Player-9999-{n:08X}": None for n in range(worldpvp.MAX_OPEN + 1)}
    lines = []
    for n, guid in enumerate(owners):
        lines.append(you_hit(FOE_A, 10 + n).replace(OWNER_GUID, guid))
    scan = worldpvp.scan_entries(
        combatlog.tokenize(log(*lines), events=worldpvp.WORLD_EVENTS), owners
    )
    assert len(scan.encounters) == worldpvp.MAX_OPEN + 1


@pytest.mark.parser
def test_constructed_the_byte_test_leaves_out_no_fight(tmp_path: Path) -> None:
    """`read_world` skips damage lines naming only your GUIDs before they
    are tokenized; on every case above it finds what a full read finds."""
    lines = [
        *REAL[2:-1],
        you_hit(FOE_A, 100),
        pet_hits(FOE_B, 101),
        ally_hits(ALLY_C, FOE_A, 102),
        hits_you(FOE_D, 103),
        you_kill(FOE_A, 104),
        you_die(105),
    ]
    data = ("\r\n".join([HEADER, ZONE, *lines]) + "\r\n").encode()
    path = tmp_path / "WoWCombatLog-040126_021630.txt"
    path.write_bytes(data)
    fast = worldpvp.read_world(path, OWNERS)
    full = worldpvp.scan_entries(combatlog.read_log(path), OWNERS)
    assert fast.model_dump(exclude={"unparsed"}) == full.model_dump(exclude={"unparsed"})
    [e] = fast.encounters
    assert len(e.players) == 4 and e.died
