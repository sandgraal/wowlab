"""The reader side of M20-19: the lab-addon's combat-log switch writes
`WowLabCharDB.combatlog` (schema 5); `char show` prints what it last did and
nothing the log holds.

CONSTRUCTED INPUT: each record is the M20-16 test's constructed schema-5
record (the real 70450 schema-4 record with the schema-5 keys added the way
`lab/addon/WowLab/` writes them), with a `combatlog` record shaped as
CombatLog.lua's `record()` writes it. Every name, realm and GUID is invented.
"""

from __future__ import annotations

from typing import Any

import pytest
import test_m20_16_schema_5_first_run as m16

from wowlab_core import labaddon

# 2026-11-22 18:03:00 UTC.
AT = 1795370580


def _raw(**combatlog: object) -> dict[str, Any]:
    raw = m16._raw()
    raw["combatlog"] = {
        "idle_minutes": 10,
        "switches": 3,
        "last_at": AT,
        "last_to": "off",
        "last_why": "entered an instance",
        "mine": False,
        "log_on": False,
        "hands_off": False,
        "advanced": True,
        "events_unregistered": {},
        **combatlog,
    }
    return raw


def test_constructed_the_record_is_read_and_shown() -> None:
    char = m16._char(_raw())
    assert isinstance(char, labaddon.CharDBV5)
    assert isinstance(char.combatlog, labaddon.CombatLog)
    assert char.combatlog.switches == 3
    assert labaddon.unknown_keys(char) == []
    lines = labaddon.describe(char)
    at = lines.index(
        "Combat-log switch: 3 switches made, idle time 10 min; the last turned the log off "
        "at 2026-11-22 18:03 UTC (entered an instance)"
    )
    assert lines[at + 1] == "  the log was off at its last check"


def test_constructed_its_own_log_and_the_setting_off() -> None:
    raw = _raw(last_to="on", last_why="open world", mine=True, log_on=True, advanced=False)
    lines = labaddon.describe(m16._char(raw))
    assert (
        "  the log was on at its last check; turned on by WowLab; Advanced Combat Logging was off"
        in lines
    )


def test_constructed_a_log_not_its_own_and_hands_off() -> None:
    lines = labaddon.describe(m16._char(_raw(log_on=True, mine=False)))
    assert (
        "  the log was on at its last check; not turned on by WowLab, so WowLab leaves it on"
        in lines
    )
    lines = labaddon.describe(m16._char(_raw(hands_off=True)))
    assert any("turned off outside WowLab, so WowLab left it off that session" in x for x in lines)
    # The log is the game's, for every character: no claim about who did it.
    assert not any("you turned" in x or "by you" in x for x in lines)


def test_constructed_dormant_paused_and_refused_in_combat() -> None:
    raw = _raw(dormant="LoggingCombat missing: the combat log is not switched")
    char = m16._char(raw)
    assert isinstance(char, labaddon.CharDBV5)
    assert isinstance(char.combatlog, labaddon.CombatLog)
    lines = labaddon.describe(char)
    assert any(
        "it stopped switching that session: LoggingCombat missing: the combat log is not switched"
        in x
        for x in lines
    )
    assert any(x.startswith("Combat-log switch: 3 switches made") for x in lines)
    lines = labaddon.describe(m16._char(_raw(paused=True)))
    assert any(
        x.startswith("Combat-log switch: off (/wowlab combatlog off); 3 switches") for x in lines
    )
    lines = labaddon.describe(m16._char(_raw(refused_in_combat=2)))
    assert any(
        "the game refused 2 switches in combat (each tried again after the fight)" in x
        for x in lines
    )


def test_constructed_switched_off_is_an_absent_record() -> None:
    raw = m16._raw()
    raw["combatlog"] = {"absent": labaddon.SWITCHED_OFF}
    raw["skip"] = ["combatlog"]
    char = m16._char(raw)
    assert isinstance(char, labaddon.CharDBV5)
    assert isinstance(char.combatlog, labaddon.AbsentSection)
    assert labaddon.skip_known(char) == (["combatlog"], [])
    assert any(x.startswith("Combat-log switch: ") for x in labaddon.describe(char))


@pytest.mark.parametrize("at", [2**53, -(2**53)])
def test_constructed_a_last_switch_time_out_of_range_is_shown_not_raised(at: int) -> None:
    lines = labaddon.describe(m16._char(_raw(last_at=at)))
    assert any("the last turned the log off at a time that cannot be read" in x for x in lines)


def test_constructed_a_file_without_the_switch_prints_no_line() -> None:
    raw = m16._raw()
    raw.pop("combatlog", None)
    lines = labaddon.describe(m16._char(raw))
    assert not any("Combat-log switch" in x for x in lines)


@pytest.mark.parametrize(
    "field,value",
    [
        ("last_to", "sideways"),
        ("switches", -1),
        ("idle_minutes", 0),
        ("idle_minutes", 121),
        ("mine", 1),
        ("last_why", "line\nbreak"),
        ("refused_in_combat", -1),
        ("paused", "yes"),
    ],
)
def test_constructed_a_value_out_of_shape_is_refused(field: str, value: object) -> None:
    with pytest.raises(labaddon.LabAddonError):
        m16._char(_raw(**{field: value}))
