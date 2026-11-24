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
    enemy_pet_hits_you,
    hits_you,
    log,
    npc_hits_you,
    pet_hits,
    pet_spell,
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
def test_constructed_a_death_long_after_the_last_hit_is_not_the_encounters() -> None:
    late = 10 + worldpvp.DEATH_WINDOW_SECONDS + 1
    [e] = _scan(log(hits_you(FOE_A, 10), you_die(late))).encounters
    assert not e.died and not any(p.killed_you for p in e.players)


@pytest.mark.parser
def test_constructed_the_killing_hit_credits_exactly_whoever_dealt_it() -> None:
    """B hits you last, but A's earlier hit had overkill 0: the real log
    writes 0 on the killing hit and -1 on every other."""
    scan = _scan(log(hits_you(FOE_A, 10, overkill=0), hits_you(FOE_B, 10), you_die(11)))
    [e] = scan.encounters
    assert e.died
    assert {p.key: p.killed_you for p in e.players} == {
        "Inventeda-Fakerealm": True,
        "Inventedb-Fakerealm": False,
    }


@pytest.mark.parser
def test_constructed_a_killing_hit_from_an_npc_credits_no_player() -> None:
    scan = _scan(log(hits_you(FOE_A, 10), npc_hits_you(11, overkill=0), you_die(11)))
    [e] = scan.encounters
    assert e.died and not any(p.killed_you for p in e.players)


@pytest.mark.parser
def test_constructed_a_killing_hit_from_an_npc_minutes_later_is_no_death_of_the_encounter() -> None:
    scan = _scan(log(hits_you(FOE_A, 10), npc_hits_you(250, overkill=0), you_die(250)))
    [e] = scan.encounters
    assert not e.died and not any(p.killed_you for p in e.players)


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
        ("Inventeda-Fake realm-", None, "Inventeda-Fakerealm"),
        ("Inventeda-Fake-realm-EU", None, "Inventeda-Fakerealm"),
        ("Inventeda", "Fake-real m", "Inventeda-Fakerealm"),
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
        "realm-space",
        "realm-hyphen",
        "home-normalized",
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
@pytest.mark.parametrize(
    ("name", "want"),
    [
        # M21-02: on a flavor with second names, four or more parts are the
        # launch client's <First>-<Second>-<Realm>-<Region>.
        ("Inventeda-Fake-realm-EU", ("Inventeda-Fake", "Inventeda", "Fake")),
        ("Tay-Soulrend-Roleplay PvP-US", ("Tay-Soulrend", "Tay", "Soulrend")),
        # Fewer parts read as before.
        ("Inventeda-Fakerealm-", ("Inventeda-Fakerealm", "Inventeda", "Fakerealm")),
        ("Inventeda-Fake realm-EU", ("Inventeda-Fakerealm", "Inventeda", "Fakerealm")),
    ],
    ids=["four-parts", "launch", "three-parts", "three-parts-space"],
)
def test_constructed_unit_names_on_a_second_name_flavor(
    name: str, want: tuple[str, str, str]
) -> None:
    assert worldpvp.player_key(name, None, second_names=True) == want
    if name.count("-") >= 3:  # without second names, the old reading
        got = worldpvp.player_key(name, None)
        assert got is not None and got[0] != want[0]


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
        pet_spell(FOE_B, 99),  # your pet's spell: known by its flags only
        you_hit(FOE_A, 100),
        pet_hits(FOE_B, 101),
        ally_hits(ALLY_C, FOE_A, 102),
        enemy_pet_hits_you(FOE_A[0], 103),  # A's pet: A was named above
        enemy_pet_hits_you("Player-9998-000000EE", 103),  # an owner never named
        hits_you(FOE_D, 103, overkill=0),
        npc_hits_you(104),
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
    assert len(e.players) == 4 and e.died and fast.pet_hits_no_owner == 1
    hits = {p.key: (p.hits, p.killed_you) for p in e.players}
    assert hits["Inventedb-Fakerealm"] == (2, False)  # the pet's spell and swing
    assert hits["Inventeda-Fakerealm"] == (2, False)  # your hit and A's pet's
    assert hits["Inventedd-Otherfakerealm"] == (1, True)  # the killing swing
    # From every offset, a read that continues with the state read so far
    # finds the rest as a read of every line does.
    offsets = [x.offset for x in combatlog.read_log(path)]
    for at in offsets[70:]:
        head = worldpvp.scan_entries((x for x in combatlog.read_log(path) if x.offset < at), OWNERS)
        rest_fast = worldpvp.read_world(path, OWNERS, start=head.end, state=head.state)
        rest_full = worldpvp.scan_entries(
            combatlog.read_log(path, start=head.end), OWNERS, start=head.end, state=head.state
        )
        assert rest_fast.model_dump(exclude={"unparsed"}) == rest_full.model_dump(
            exclude={"unparsed"}
        ), at


@pytest.mark.parser
def test_constructed_a_continued_read_knows_who_logs(tmp_path: Path) -> None:
    """A read from an offset after your own lines still knows your pet."""
    data = log(you_hit(("Creature-0-1-0-2-1554-0000000000", "x"), 5), pet_spell(FOE_B, 10))
    path = tmp_path / "WoWCombatLog-040126_021630.txt"
    path.write_bytes(data)
    cut = len(log(you_hit(("Creature-0-1-0-2-1554-0000000000", "x"), 5)))
    first = worldpvp.scan_entries(combatlog.read_log(path), OWNERS)
    head = worldpvp.scan_entries((x for x in combatlog.read_log(path) if x.offset < cut), OWNERS)
    assert head.state.logging == OWNER_GUID and head.end == cut
    rest = worldpvp.read_world(path, OWNERS, start=cut, state=head.state)
    assert rest.fights == first.fights == 1
    without = worldpvp.read_world(path, OWNERS, start=cut)  # control: no state, no owner
    assert without.fights == 0


@pytest.mark.parser
def test_constructed_a_member_of_your_group_is_no_foe() -> None:
    for flags in ("0x542", "0x544"):  # party, raid; hostile (charmed), player
        assert _scan(log(you_hit(FOE_A, 10, flags=flags))).fights == 0, flags


@pytest.mark.parser
@pytest.mark.parametrize(
    "line",
    [
        'ZONE_CHANGE,--5,"Tirisfal Glades",0',
        "ZONE_CHANGE," + chr(0x0663) + ',"Tirisfal Glades",0',
        'ZONE_CHANGE,0,"Tirisfal Glades",' + chr(0x0660),
        'ZONE_CHANGE,99999999999999999999,"Tirisfal Glades",0',
        "ZONE_CHANGE",
        "ZONE_CHANGE,0",
        "UNIT_DIED,0000000000000000,nil,0x80000000,0x80000000,Player-9999-00000001",
        "SWING_DAMAGE,Player-9998-0000000A,x,0x548,0x0,Player-9999-00000001,y,0x511,0x0",
        'SPELL_DAMAGE,Player-9998-0000000A,"x",0xZZ,0x0,Player-9999-00000001,"y",0x511',
        "PARTY_KILL,(a,b),[c],0x511",
    ],
    ids=[
        "double-minus",
        "arabic-digit",
        "arabic-difficulty",
        "too-long",
        "no-fields",
        "one-field",
        "short-death",
        "unquoted-names",
        "bad-flags",
        "groups",
    ],
)
def test_constructed_a_damaged_line_never_stops_a_read(line: str, tmp_path: Path) -> None:
    """HOSTILE INPUT: each line is read as unread or as nothing, and the read
    goes on to the fight after it."""
    data = log(f"4/1/2026 02:16:31.000-4  {line}", zone(2), you_hit(FOE_A, 10))
    path = tmp_path / "WoWCombatLog-040126_021630.txt"
    path.write_bytes(data)
    for scan in (_scan(data), worldpvp.read_world(path, OWNERS)):
        assert scan.end == len(data) and scan.fights == 1


@pytest.mark.parser
def test_constructed_a_read_stops_at_its_bound_and_goes_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(worldpvp, "MAX_READ_PLAYERS", 3)
    gap = worldpvp.ENCOUNTER_GAP_SECONDS + 1
    lines = [
        you_hit((f"Player-9998-{n:08X}", "Inventeda-Fakerealm-"), 10 + n * gap) for n in range(7)
    ]
    data = log(*lines)
    path = tmp_path / "WoWCombatLog-040126_021630.txt"
    path.write_bytes(data)
    found = 0
    start, state, parts = 0, None, 0
    while True:
        scan = worldpvp.read_world(path, OWNERS, start=start, state=state)
        found += len(scan.encounters)
        parts += 1
        if not scan.more:
            break
        start, state = scan.end, scan.state
    assert (found, parts) == (7, 3)


_COUNTS = (
    "lines",
    "unparsed",
    "bad_lines",
    "fights",
    "outside_world",
    "no_zone",
    "no_time",
    "pet_hits_no_owner",
    "left_out",
)


def _in_parts(path: Path) -> tuple[dict[str, int], list[worldpvp.WorldEncounter], int]:
    totals = dict.fromkeys(_COUNTS, 0)
    found: list[worldpvp.WorldEncounter] = []
    start, state, parts = 0, None, 0
    while True:
        scan = worldpvp.read_world(path, OWNERS, start=start, state=state)
        for k in _COUNTS:
            totals[k] += getattr(scan, k)
        found += scan.encounters
        parts += 1
        if not scan.more:
            return totals, found, parts
        start, state = scan.end, scan.state


@pytest.mark.parser
def test_constructed_a_read_in_parts_counts_every_line_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every count of a read in parts equals the one-part read's: each kind
    of line sits near a seam in turn (a fight, one outside the open world,
    a pet with no named owner, a damaged line, a pet whose owner was named
    in an earlier part)."""
    gap = worldpvp.ENCOUNTER_GAP_SECONDS + 1
    lines: list[str] = []
    for n in range(6):
        at = 10 + n * (gap + 10)  # each encounter closed before the next starts
        foe = (f"Player-9998-{n:08X}", "Inventeda-Fakerealm-")
        stamp_of = you_hit(foe, at + 2).split("  ")[0]
        lines += [
            zone(at, instance=489, name="Invented Gulch", difficulty=34),
            you_hit(foe, at),  # outside the open world
            zone(at),
            you_hit(foe, at + 1),
            enemy_pet_hits_you(foe[0], at + 2),  # owner named: a fight
            enemy_pet_hits_you("Player-9998-000000EE", at + 2),  # never named
            stamp_of + '  ZONE_CHANGE,--5,"x",0',  # damaged: no zone after it
            zone(at + 3),
        ]
    path = tmp_path / "WoWCombatLog-040126_021630.txt"
    path.write_bytes(log(*lines))
    whole, one, single = _in_parts(path)
    assert single == 1 and whole["fights"] == 12 and len(one) == 6
    assert whole["pet_hits_no_owner"] == 6 and whole["outside_world"] == 6
    for bound in (1, 2, 3):
        monkeypatch.setattr(worldpvp, "MAX_READ_PLAYERS", bound)
        parted, found, parts = _in_parts(path)
        assert parts > 1, bound
        assert parted == whole, bound
        assert [e.model_dump() for e in found] == [e.model_dump() for e in one], bound


@pytest.mark.parser
def test_constructed_a_pets_owner_named_before_the_offset_is_still_known(
    tmp_path: Path,
) -> None:
    data = log(you_hit(FOE_A, 10), enemy_pet_hits_you(FOE_A[0], 400))
    path = tmp_path / "WoWCombatLog-040126_021630.txt"
    path.write_bytes(data)
    cut = len(log(you_hit(FOE_A, 10)))
    head = worldpvp.scan_entries((x for x in combatlog.read_log(path) if x.offset < cut), OWNERS)
    assert head.state.names == {FOE_A[0]: FOE_A[1]}
    rest = worldpvp.read_world(path, OWNERS, start=cut, state=head.state)
    assert (rest.fights, rest.pet_hits_no_owner) == (1, 0)
    assert [p.key for e in rest.encounters for p in e.players] == ["Inventeda-Fakerealm"]


@pytest.mark.parser
def test_constructed_the_names_kept_with_the_offset_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worldpvp, "SAVED_NAMES", 2)
    lines = [you_hit((f"Player-9998-{n:08X}", "Inventeda-Fakerealm-"), 10 + n) for n in range(5)]
    scan = _scan(log(*lines))
    assert list(scan.state.names) == ["Player-9998-00000003", "Player-9998-00000004"]
