"""`worldpvp` against the real combat log (L8; LAB_FORMATS §8, M20-18).

The one real log holds one solo fight against one NPC and no other player,
so it must give no encounter, every line read, and the zone its first
`ZONE_CHANGE` line names. Reading it from an offset gives the lines the
whole read gives from there (`combatlog.read_log(start=...)`), and its head
is its first line. The PvP cases are graded on constructed lines in
`test_worldpvp_constructed.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import combatlog, worldpvp

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FOREVER_LOG = FIXTURES / "macos" / "forever" / "Logs" / "WoWCombatLog-040126_021630.txt"
OWNER = "Player-9999-00000001"


@pytest.mark.parser
def test_the_real_log_holds_no_encounter_and_every_line_is_read() -> None:
    scan = worldpvp.read_world(FOREVER_LOG, {OWNER: None})
    assert scan.encounters == []
    assert (scan.lines, scan.unparsed, scan.fights) == (79, 0, 0)
    assert (scan.outside_world, scan.no_zone, scan.no_time, scan.left_out) == (0, 0, 0, 0)
    assert scan.end == FOREVER_LOG.stat().st_size
    assert scan.state == worldpvp.ZoneState(
        zone="Tirisfal Glades", instance_id=0, difficulty=0, arena=False
    )


@pytest.mark.parser
def test_every_timestamp_of_the_real_log_reads_as_a_time() -> None:
    times = [
        worldpvp.seconds_of(e.timestamp)
        for e in combatlog.read_log(FOREVER_LOG)
        if isinstance(e, combatlog.Record)
    ]
    assert None not in times
    assert times == sorted(times)  # type: ignore[type-var]
    # 4/1/2026 02:16:30.666 at UTC-4 is 06:16:30 UTC.
    assert times[0] == 1_775_024_190


@pytest.mark.parser
def test_reading_from_an_offset_gives_the_same_lines_from_there() -> None:
    whole = list(combatlog.read_log(FOREVER_LOG))
    at = whole[40].offset
    rest = list(combatlog.read_log(FOREVER_LOG, start=at))
    assert rest == whole[40:]
    filtered = list(combatlog.read_log(FOREVER_LOG, events=worldpvp.WORLD_EVENTS, start=at))
    assert [e.offset for e in filtered] == [e.offset for e in whole[40:]]


@pytest.mark.parser
def test_skip_leaves_out_only_lines_of_events_asked_for() -> None:
    seen: list[bytes] = []

    def skip(event: bytes, data: bytes) -> bool:
        seen.append(event)
        return event == b"SPELL_DAMAGE"

    entries = list(combatlog.read_log(FOREVER_LOG, events={"SPELL_DAMAGE", "UNIT_DIED"}, skip=skip))
    kinds = {(type(e).__name__, getattr(e, "event", None)) for e in entries}
    assert ("Skipped", "SPELL_DAMAGE") in kinds and ("Record", "UNIT_DIED") in kinds
    assert ("Record", "SPELL_DAMAGE") not in kinds
    assert set(seen) == {b"SPELL_DAMAGE", b"UNIT_DIED"}
    assert len(entries) == 79


@pytest.mark.parser
def test_a_negative_start_is_refused() -> None:
    with pytest.raises(ValueError, match="start"):
        list(combatlog.read_log(FOREVER_LOG, start=-1))


@pytest.mark.parser
def test_a_read_that_continues_finds_what_one_read_finds() -> None:
    first = worldpvp.read_world(FOREVER_LOG, {OWNER: None})
    whole = list(combatlog.read_log(FOREVER_LOG))
    half = worldpvp.scan_entries(whole[:40], {OWNER: None})
    rest = worldpvp.read_world(FOREVER_LOG, {OWNER: None}, start=half.end, state=half.state)
    assert half.end == whole[40].offset
    assert rest.end == first.end
    assert half.lines + rest.lines == first.lines
    assert rest.state == first.state


@pytest.mark.parser
def test_the_head_is_the_first_line_and_the_size() -> None:
    size, line = combatlog.head(FOREVER_LOG)
    data = FOREVER_LOG.read_bytes()
    assert size == len(data)
    assert line == data.split(b"\r\n", 1)[0]
    assert line.endswith(b"PROJECT_ID,18")


@pytest.mark.parser
def test_the_owners_unit_name_gives_the_key_of_the_crew_book() -> None:
    """The real log's own name `"<First>-<Realm>-"`: the key is
    `<First>-<Realm>`, the realm without its spaces, the empty third part
    dropped."""
    name = next(
        e.fields[1]
        for e in combatlog.read_log(FOREVER_LOG)
        if isinstance(e, combatlog.Record) and e.event == "SPELL_DAMAGE"
    )
    assert isinstance(name, combatlog.Quoted)
    assert worldpvp.player_key(name.text, None) == (
        "Labchard-LabrealmbPartbPartcPartd",
        "Labchard",
        "LabrealmbPartbPartcPartd",
    )
