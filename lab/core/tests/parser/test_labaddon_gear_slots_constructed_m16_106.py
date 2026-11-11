"""`labaddon.Gear` refuses slots the addon cannot have written (M16-106;
L8: constructed boundary and hostile cases, every test id says
`constructed`).

`Gear.lua` walks `for slot = first, last` once and lists only the slot it
is on, so a real file never holds a duplicate, a slot outside the walk or a
backwards range. Each case starts from a real `WowLab.lua` capture (index
rows in `fixtures/README.md`), read as `luadata.to_python` gives it, and
changes only the `gear` record. Every character capture is a base, so the
check holds on every schema the reader knows.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import labaddon, luadata

pytestmark = pytest.mark.parser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RECORDS = sorted(
    str(p.relative_to(FIXTURES))
    for p in FIXTURES.rglob("SavedVariables/WowLab.lua")
    if b"WowLabCharDB" in p.read_bytes()
)


def _base(record: str) -> dict[str, Any]:
    raw = luadata.parse((FIXTURES / record).read_bytes()).to_python()["WowLabCharDB"]
    assert isinstance(raw, dict)
    return copy.deepcopy(raw)


def _gear_with(record: str, first: int, last: int, slots: list[int]) -> dict[str, Any]:
    raw = _base(record)
    gear = raw["gear"]
    template = gear["slots"][0]
    gear["first_slot"] = first
    gear["last_slot"] = last
    gear["slots"] = [{**template, "slot": s} for s in slots]
    return raw


def _refused(raw: object, *fragments: str) -> str:
    with pytest.raises(labaddon.LabAddonError) as info:
        labaddon.load_char(raw)
    message = str(info.value)
    for fragment in fragments:
        assert fragment in message, message
    return message


def _gear(raw: object) -> labaddon.Gear:
    gear = labaddon.load_char(raw).gear
    assert isinstance(gear, labaddon.Gear)
    return gear


def test_every_character_capture_is_a_base() -> None:
    assert len(_RECORDS) >= 6
    for record in _RECORDS:
        assert isinstance(_gear(_base(record)), labaddon.Gear)


@pytest.mark.parametrize("record", _RECORDS)
def test_constructed_gear_slot_listed_twice_is_refused(record: str) -> None:
    """A slot listed twice would print twice in `char gear` and give the
    dressing room two items for one slot; refused as the whole record."""
    _refused(
        _gear_with(record, 1, 19, [5, 6, 6]),
        "WowLabCharDB.gear",
        "slot 6 is listed more than once",
    )


@pytest.mark.parametrize("record", _RECORDS)
@pytest.mark.parametrize("slot", [0, 20, -1])
def test_constructed_gear_slot_outside_the_walk_is_refused(record: str, slot: int) -> None:
    """Every listed slot lies in `first_slot`..`last_slot`."""
    _refused(
        _gear_with(record, 1, 19, [1, slot]),
        "WowLabCharDB.gear",
        f"slot {slot} is outside the slots walked (1 to 19)",
    )


@pytest.mark.parametrize("record", _RECORDS)
def test_constructed_gear_range_that_runs_backwards_is_refused(record: str) -> None:
    """`first_slot` after `last_slot` is refused, with or without slots."""
    _refused(_gear_with(record, 19, 1, []), "WowLabCharDB.gear", "first_slot is after last_slot")
    _refused(_gear_with(record, 19, 1, [5]), "WowLabCharDB.gear", "first_slot is after last_slot")


def test_constructed_gear_slots_at_the_bounds_are_read() -> None:
    """The bounds are inclusive; a one-slot walk and an empty list read."""
    record = _RECORDS[0]
    assert [s.slot for s in _gear(_gear_with(record, 1, 19, [1, 19])).slots] == [1, 19]
    assert [s.slot for s in _gear(_gear_with(record, 7, 7, [7])).slots] == [7]
    assert _gear(_gear_with(record, 7, 7, [])).slots == []


def test_constructed_gear_refusal_reaches_parse_char() -> None:
    """The refusal surfaces from the file reader too, so `read_all` shows
    it as the character's record error."""
    record = _RECORDS[0]
    text = (FIXTURES / record).read_bytes()
    marker = b'["last_slot"] = 19'
    assert text.count(marker) == 1
    with pytest.raises(labaddon.LabAddonError, match="first_slot is after last_slot"):
        labaddon.parse_char(text.replace(marker, b'["last_slot"] = 0'))
