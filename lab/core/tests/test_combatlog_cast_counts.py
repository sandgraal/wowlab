"""`combatlog.cast_counts` (M13-08, docs/LAB_PLAN.md §15.2).

The real Forever log (`fixtures/macos/forever/Logs/`, format 22, one solo
fight) is the grade: its seven `SPELL_CAST_SUCCESS` records are six by the
owner (flags `0x511`) and one by the NPC (`0x10a48`), and the layout read
here (source flags third, spell id ninth, quoted spell name tenth) is the
one those lines show.

Inputs labelled `constructed` are boundary cases (L8): lines in the real
log's layout with one thing changed (the flags, the version line, a field
count), each one failing when the rule it checks is removed. Their names are
made up. Nothing here reads or writes an install.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import combatlog

LOG = Path(__file__).resolve().parent / "fixtures/macos/forever/Logs/WoWCombatLog-040126_021630.txt"
HEADER = "4/1/2026 02:16:30.666-4  COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1,BUILD_VERSION,1.60.1,PROJECT_ID,18"
ADVANCED = "Player-9999-00000001,0000000000000000,183,183,37,0,84,0,0,0,0,322,322,35,2689.43,-919.91,1420,2.5087,1"


def cast(
    spell_id: str = "172",
    name: str = '"Constructed Bolt"',
    flags: str = "0x511",
    source: str = "Player-9999-00000001",
    stamp: str = "4/1/2026 02:16:37.862-4",
    event: str = "SPELL_CAST_SUCCESS",
    advanced: bool = True,
) -> str:
    """One cast in the real log's format-22 layout, with made-up names."""
    fields = [
        source,
        '"Constructed-Unit-"',
        flags,
        "0x80000000",
        "Creature-0-1-0-2-1554-0000000000",
        '"Constructed Target"',
        "0x10a48",
        "0x80000000",
        spell_id,
        name,
        "0x20",
    ]
    tail = f",{ADVANCED}" if advanced else ""
    return f"{stamp}  {event},{','.join(fields)}{tail}"


def counts(*lines: str) -> combatlog.CastCounts:
    data = "".join(f"{line}\r\n" for line in lines).encode("utf-8")
    return combatlog.cast_counts(combatlog.tokenize(data))


# ─── the real log ────────────────────────────────────────────────────────────


def test_real_log_counts_the_owners_six_casts_and_not_the_npcs() -> None:
    got = combatlog.cast_counts(combatlog.read_log(LOG))
    assert got.lines == 79
    assert got.unparsed == 0
    assert got.versions == ("22",)
    assert got.cast_events == 7
    assert (got.mine, got.others, got.other_version, got.not_read) == (6, 1, 0, 0)
    assert got.sources == 1
    assert got.first_timestamp == "4/1/2026 02:16:37.862-4"
    assert got.last_timestamp == "4/1/2026 02:17:00.351-4"
    assert [(s.spell_id, s.spell_name, s.casts) for s in got.spells] == [
        (172, "Corruption", 2),
        (980, "Bane of Agony", 2),
        (348, "Immolate", 1),
        (695, "Shadow Bolt", 1),
    ]


def test_real_log_npc_cast_is_the_one_left_out() -> None:
    """The NPC's Ravage is the cast the flags leave out; nothing names it."""
    got = combatlog.cast_counts(combatlog.read_log(LOG))
    assert "Ravage" not in {s.spell_name for s in got.spells}
    assert sum(s.casts for s in got.spells) == got.mine


# ─── constructed: the flags ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("flags", "mine"),
    [
        ("0x511", True),  # the real owner's flags
        ("0x10511", True),  # constructed: the same with the target bit
        ("0x20511", True),  # constructed: and the focus bit
        ("0x1111", False),  # constructed: mine, but a pet
        ("0x2111", False),  # constructed: mine, a guardian
        ("0x512", False),  # constructed: a player in the party
        ("0x514", False),  # constructed: a player in the raid
        ("0x518", False),  # constructed: an outsider player
        ("0x10a48", False),  # the real NPC's flags
        ("0x501", True),  # constructed: mine and a player, reaction bits clear
        ("0x611", False),  # constructed: mine and a player, but NPC-controlled (charmed)
        ("0x411", False),  # constructed: mine and a player, no controller bit
    ],
    ids=lambda v: str(v),
)
def test_constructed_source_flags_pick_the_player(flags: str, mine: bool) -> None:
    got = counts(HEADER, cast(flags=flags))
    assert (got.mine, got.others) == ((1, 0) if mine else (0, 1))


def test_constructed_a_name_or_guid_does_not_pick_the_player() -> None:
    """The same GUID and unit name under NPC flags is not counted, and a
    different GUID under the owner's flags is."""
    got = counts(
        HEADER,
        cast(flags="0x10a48"),
        cast(source="Player-9999-00000002"),
    )
    assert (got.mine, got.others, got.sources) == (1, 1, 1)


def test_constructed_two_of_the_players_characters_are_counted_as_two_sources() -> None:
    got = counts(HEADER, cast(), cast(source="Player-9999-00000002"), cast())
    assert (got.mine, got.sources) == (3, 2)


# ─── constructed: the version and the layout ─────────────────────────────────


def test_constructed_casts_before_any_version_line_are_not_counted() -> None:
    got = counts(cast(), HEADER, cast())
    assert (got.cast_events, got.other_version, got.mine) == (2, 1, 1)


def test_constructed_casts_under_another_version_are_not_counted() -> None:
    other = HEADER.replace("COMBAT_LOG_VERSION,22", "COMBAT_LOG_VERSION,21")
    got = counts(other, cast(), HEADER, cast())
    assert got.versions == ("21", "22")
    assert (got.other_version, got.mine) == (1, 1)


@pytest.mark.parametrize(
    "line",
    [
        cast(flags="511"),  # flags not hex
        cast(flags="nil"),
        cast(spell_id="nil"),
        cast(spell_id="-1"),
        cast(spell_id="0x20"),
        cast(name="Constructed Bolt"),  # the name not quoted
        "4/1/2026 02:16:37.862-4  SPELL_CAST_SUCCESS,Player-9999-00000001,"
        '"Constructed-Unit-",0x511,0x80000000,0000000000000000,nil,0x80000000,'
        '0x80000000,172,"Constructed Bolt"',  # ten fields: no school
    ],
    ids=["flags-bare", "flags-nil", "id-nil", "id-negative", "id-hex", "name-bare", "short"],
)
def test_constructed_a_cast_without_the_layout_is_not_read(line: str) -> None:
    got = counts(HEADER, line)
    assert (got.cast_events, got.not_read, got.mine, got.others) == (1, 1, 0, 0)


def test_constructed_a_cast_without_the_advanced_block_is_counted() -> None:
    """The advanced block is not read; a log with advanced logging off is
    counted the same, on the layout's first eleven fields **[verify]**."""
    got = counts(HEADER, cast(advanced=False))
    assert got.mine == 1


def test_constructed_other_events_are_not_casts() -> None:
    got = counts(HEADER, cast(event="SPELL_CAST_START"), cast(event="SPELL_DAMAGE"))
    assert (got.cast_events, got.mine) == (0, 0)


def test_constructed_unparsed_lines_are_counted_and_skipped() -> None:
    got = counts(HEADER, "not a combat log line", cast())
    assert (got.lines, got.unparsed, got.mine) == (3, 1, 1)


def test_constructed_casts_are_counted_per_id_and_name_most_first() -> None:
    got = counts(
        HEADER,
        cast("686", '"Constructed Bolt"'),
        cast("695", '"Constructed Bolt"'),
        cast("695", '"Constructed Bolt"'),
        cast("10", '"Another"'),
        cast("10", '"Another"'),
    )
    assert [(s.spell_id, s.spell_name, s.casts) for s in got.spells] == [
        (10, "Another", 2),
        (695, "Constructed Bolt", 2),
        (686, "Constructed Bolt", 1),
    ]


def test_constructed_a_quoted_name_with_a_comma_is_one_name() -> None:
    got = counts(HEADER, cast(name='"Bolt, Constructed"'))
    assert got.spells[0].spell_name == "Bolt, Constructed"


def test_cast_counts_reads_an_iterator_once() -> None:
    """It consumes a generator (as `read_log` gives) without needing a list."""
    data = f"{HEADER}\r\n{cast()}\r\n".encode()
    got = combatlog.cast_counts(iter(list(combatlog.tokenize(data))))
    assert got.mine == 1
