"""The open world by the game's map table, not by difficulty (M22-22).

Real fixtures: `wago/Map.1.60.1.71037.csv` (the build's `Map` table, read
whole) and `macos-71037/forever/Logs/WoWCombatLog-012427_232527.txt` (50
lines of the owner's launch-client log, through the capture tool, ending in
`ZONE_CHANGE,33,"Shadowfang Keep",0`: a dungeon with difficulty 0). The
cases that need a fight in an instance append CONSTRUCTED lines (built by
`_world_lines` from the real beta log's own lines) to the real slice, or
are constructed whole (battleground and arena stays); each such test id
says `constructed`.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from _world_lines import FOE_A, OWNER_GUID, hits_you, log, stamp, you_hit, zone

from wowlab_core import combatlog, worldpvp

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MAP_TABLE = FIXTURES / "wago" / "Map.1.60.1.71037.csv"
SLICE = FIXTURES / "macos-71037" / "forever" / "Logs" / "WoWCombatLog-012427_232527.txt"
OWNERS = {OWNER_GUID: None}


def _maps() -> dict[int, int]:
    with MAP_TABLE.open(newline="", encoding="utf-8") as handle:
        return worldpvp.map_types(csv.DictReader(handle))


def _scan(data: bytes, **kw: object) -> worldpvp.WorldScan:
    return worldpvp.scan_entries(
        combatlog.tokenize(data, events=worldpvp.WORLD_EVENTS), OWNERS, **kw
    )  # type: ignore[arg-type]


# ─── the table ───────────────────────────────────────────────────────────────


@pytest.mark.parser
def test_the_recorded_map_table_gives_each_maps_instance_type() -> None:
    maps = _maps()
    assert len(maps) == 78
    # The open world, a dungeon, a raid and the battlegrounds of the build.
    assert maps[0] == maps[1] == worldpvp.WORLD_INSTANCE_TYPE == 0
    assert maps[33] == 1  # Shadowfang Keep
    assert maps[409] == 2  # Molten Core
    assert [maps[i] for i in (30, 489, 529)] == [3, 3, 3]
    assert sorted(set(maps.values())) == [0, 1, 2, 3]


@pytest.mark.parser
def test_map_types_skips_a_row_it_cannot_read_constructed() -> None:
    rows = [
        {"ID": "33", "InstanceType": "1"},
        {"ID": "x", "InstanceType": "0"},
        {"ID": "34", "InstanceType": ""},
        {"ID": "35"},
        {"ID": "0", "InstanceType": "0"},
    ]
    assert worldpvp.map_types(rows) == {33: 1, 0: 0}


# ─── the real slice ──────────────────────────────────────────────────────────


@pytest.mark.parser
def test_the_real_launch_log_writes_a_dungeon_with_difficulty_0() -> None:
    entries = list(combatlog.read_log(SLICE, events=worldpvp.WORLD_EVENTS))
    records = [e for e in entries if isinstance(e, combatlog.Record)]
    zones = [
        (r.fields[0], r.fields[1].text, r.fields[2])
        for r in records
        if r.event == "ZONE_CHANGE" and isinstance(r.fields[1], combatlog.Quoted)
    ]
    assert zones == [
        ("1", "Orgrimmar", "0"),
        ("0", "Tirisfal Glades", "0"),
        ("0", "Silverpine Forest", "0"),
        ("33", "Shadowfang Keep", "0"),
    ]
    # Logging switched back on within one session: a new header and zone
    # line appended to the same file, three times in this one.
    assert sum(1 for r in records if r.event == "COMBAT_LOG_VERSION") == 3


@pytest.mark.parser
def test_on_the_real_slice_shadowfang_keep_is_the_open_world_only_by_difficulty() -> None:
    scan = worldpvp.read_world(SLICE, OWNERS)
    state = scan.state
    assert (state.zone, state.instance_id, state.difficulty) == ("Shadowfang Keep", 33, 0)
    # The rule before the map table: difficulty 0 is the open world.
    assert worldpvp.in_open_world(state) is True
    # The map table says map 33 is a dungeon.
    assert worldpvp.in_open_world(state, maps=_maps()) is False
    assert worldpvp.decided_by_map(state, _maps())
    assert not worldpvp.decided_by_map(state, None)


@pytest.mark.parser
def test_the_real_slices_open_world_zones_stay_the_open_world_by_the_table() -> None:
    maps = _maps()
    for instance, name in ((1, "Orgrimmar"), (0, "Tirisfal Glades"), (0, "Silverpine Forest")):
        state = worldpvp.ZoneState(zone=name, instance_id=instance, difficulty=0)
        assert worldpvp.in_open_world(state, maps=maps) is True


def _slice_then(*lines: str) -> bytes:
    """The real slice, then CONSTRUCTED lines (CRLF, as the slice)."""
    return SLICE.read_bytes() + ("\r\n".join(lines) + "\r\n").encode("utf-8")


def _later(line: str, day: str) -> str:
    """A constructed line moved onto the slice's last day, so it follows
    the real Shadowfang Keep line in time."""
    return day + line[line.index("  ") :]


def _slice_day(offset: int) -> str:
    last = SLICE.read_bytes().decode("utf-8").rstrip("\r\n").split("\r\n")[-1]
    when = last.split("  ")[0]  # M/D/YYYY HH:MM:SS.mmm-6
    date, clock = when.split(" ")
    hh, mm, rest = clock.split(":")
    ss, tail = rest.split(".")
    total = int(hh) * 3600 + int(mm) * 60 + int(ss) + offset
    hh2, r = divmod(total, 3600)
    mm2, ss2 = divmod(r, 60)
    return f"{date} {hh2:02d}:{mm2:02d}:{ss2:02d}.{tail}"


@pytest.mark.parser
def test_constructed_a_fight_after_the_real_shadowfang_keep_line_is_not_the_open_world() -> None:
    fight = [_later(you_hit(FOE_A, 10), _slice_day(5)), _later(hits_you(FOE_A, 11), _slice_day(6))]
    data = _slice_then(*fight)
    by_map = _scan(data, maps=_maps())
    assert (by_map.fights, by_map.outside_world, by_map.encounters) == (0, 2, [])
    # Without the table the dungeon counts, as it did before.
    by_difficulty = _scan(data)
    assert (by_difficulty.fights, by_difficulty.outside_world) == (2, 0)
    [e] = by_difficulty.encounters
    assert (e.zone, e.instance_id, e.by_map) == ("Shadowfang Keep", 33, False)


@pytest.mark.parser
def test_constructed_back_in_the_open_world_after_the_dungeon_a_fight_counts_by_map() -> None:
    back = f'{_slice_day(60)}  ZONE_CHANGE,0,"Silverpine Forest",0'
    data = _slice_then(
        _later(you_hit(FOE_A, 10), _slice_day(5)), back, _later(you_hit(FOE_A, 10), _slice_day(70))
    )
    scan = _scan(data, maps=_maps())
    assert (scan.fights, scan.outside_world) == (1, 1)
    [e] = scan.encounters
    assert (e.zone, e.instance_id, e.by_map) == ("Silverpine Forest", 0, True)


# ─── battleground and arena stays (constructed) ──────────────────────────────


@pytest.mark.parser
@pytest.mark.parametrize(
    ("instance", "name"),
    [(489, "Warsong Gulch"), (30, "Alterac Valley"), (529, "Arathi Basin")],
    ids=["constructed-wsg", "constructed-av", "constructed-ab"],
)
def test_constructed_a_battleground_with_difficulty_0_is_not_the_open_world(
    instance: int, name: str
) -> None:
    data = log(zone(5, instance=instance, name=name, difficulty=0), you_hit(FOE_A, 10))
    assert (_scan(data, maps=_maps()).fights, _scan(data, maps=_maps()).outside_world) == (0, 1)
    assert _scan(data).fights == 1  # the difficulty alone takes it for the open world


@pytest.mark.parser
def test_constructed_an_arena_map_by_its_instance_type_is_not_the_open_world() -> None:
    # No arena is in the recorded table (no map has type 4); an arena's
    # type is 4 by community documentation.
    maps = {**_maps(), 559: 4}
    data = log(zone(5, instance=559, name="Invented Ring", difficulty=0), you_hit(FOE_A, 10))
    scan = _scan(data, maps=maps)
    assert (scan.fights, scan.outside_world) == (0, 1)


@pytest.mark.parser
def test_constructed_an_arena_span_on_a_map_the_table_does_not_hold_is_not_world() -> None:
    begin = f"{stamp(9)}  ARENA_MATCH_START,1505,0,2v2,0"
    end = f"{stamp(20)}  ARENA_MATCH_END,0,30,1500,1500"
    data = log(
        zone(5, instance=1505, name="Invented Ring", difficulty=0),
        begin,
        you_hit(FOE_A, 10),
        end,
    )
    scan = _scan(data, maps=_maps())
    assert (scan.fights, scan.outside_world) == (0, 1)


@pytest.mark.parser
def test_constructed_a_map_the_table_does_not_hold_is_not_the_open_world() -> None:
    maps = _maps()
    assert 4242 not in maps
    world = worldpvp.ZoneState(zone="Invented", instance_id=4242, difficulty=0)
    assert worldpvp.in_open_world(world, maps=maps) is False
    assert worldpvp.unknown_map(world, maps) and not worldpvp.decided_by_map(world, maps)
    # Without a table the difficulty decides, as before.
    assert worldpvp.in_open_world(world) is True
    assert not worldpvp.unknown_map(world, None)


@pytest.mark.parser
def test_constructed_fights_on_a_map_the_table_does_not_hold_are_counted_apart() -> None:
    data = log(
        zone(5, instance=4242, name="Invented Hold", difficulty=0),
        you_hit(FOE_A, 10),
        hits_you(FOE_A, 11),
        zone(30, instance=33, name="Shadowfang Keep", difficulty=0),
        you_hit(FOE_A, 40),
    )
    scan = _scan(data, maps=_maps())
    assert (scan.fights, scan.outside_world, scan.unknown_map) == (0, 1, 2)
    assert scan.unknown_maps == [4242]
    assert scan.encounters == []
    plain = _scan(data)
    assert (plain.fights, plain.unknown_map, plain.unknown_maps) == (3, 0, [])


@pytest.mark.parser
def test_constructed_at_most_so_many_unknown_map_ids_are_named() -> None:
    lines: list[str] = []
    for n in range(worldpvp.MAX_UNKNOWN_MAPS + 5):
        lines += [
            zone(10 * n, instance=5000 + n, name="Invented", difficulty=0),
            you_hit(FOE_A, 10 * n + 1),
        ]
    scan = _scan(log(*lines), maps=_maps())
    assert scan.unknown_map == worldpvp.MAX_UNKNOWN_MAPS + 5
    assert scan.unknown_maps == [5000 + n for n in range(worldpvp.MAX_UNKNOWN_MAPS)]


@pytest.mark.parser
def test_constructed_a_place_given_by_the_caller_decides_over_the_table() -> None:
    data = log(zone(5, instance=33, name="Shadowfang Keep", difficulty=0), you_hit(FOE_A, 10))
    scan = _scan(data, maps=_maps(), place=lambda s: s.instance_id == 33)
    assert (scan.fights, scan.unknown_map) == (1, 0)


@pytest.mark.parser
def test_constructed_a_kept_match_map_wins_over_the_table() -> None:
    state = worldpvp.ZoneState(zone="Eastern Kingdoms", instance_id=0, difficulty=0)
    assert worldpvp.in_open_world(state, [0], maps=_maps()) is False
    assert worldpvp.in_open_world(state.model_copy(update={"arena": True}), maps=_maps()) is False


@pytest.mark.parser
def test_constructed_read_world_and_scan_entries_agree_with_the_table(tmp_path: Path) -> None:
    data = log(
        zone(5, instance=33, name="Shadowfang Keep", difficulty=0),
        you_hit(FOE_A, 10),
        zone(30, instance=0, name="Silverpine Forest", difficulty=0),
        you_hit(FOE_A, 40),
    )
    path = tmp_path / "WoWCombatLog-040126_021630.txt"
    path.write_bytes(data)
    read = worldpvp.read_world(path, OWNERS, maps=_maps())
    scanned = _scan(data, maps=_maps())
    assert read.model_dump() == scanned.model_dump()
    assert (read.fights, read.outside_world) == (1, 1)
