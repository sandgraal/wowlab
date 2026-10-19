"""`itemlink` against every item link in the committed `WowLab.lua` captures (L4, L8; M14-05).

The links are read from the records as `labaddon.read_char` parses them
(L3: the file is data, nothing is executed), one parametrised case per
character file with its link count in the test id: 38 links over the
70009 (`macos/`, two characters), 70058 (`macos-70058/`) and 70425
(`macos-70425/`) captures. Every one is in the named colour form
`|cnIQn:` and none carries a crafter (docs/LAB_PLAN.md §16.4); the 70425
cloak in slot 15 has `crafter_removed = true` and a blank crafter field,
the addon having blanked the GUID. Boundary and hostile links are
constructed, in `test_itemlink_constructed.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from wowlab_core import itemlink, labaddon

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ACCOUNT = "WTF/Account/90000001#6"
# (fixture tree, character folder, links in its gear record)
RECORDS = [
    ("macos", "1/Labchard-Labrealmg", 9),
    ("macos", "1/Labcharb-Labrealmf", 9),
    ("macos-70058", "1/Labchard-Labrealmg", 9),
    ("macos-70425", "1/Labchard-Labrealme", 11),
]
_ITEM_ID = re.compile(r"\|Hitem:([0-9]+):")


def _path(tree: str, character: str) -> Path:
    return FIXTURES / tree / "forever" / ACCOUNT / character / "SavedVariables" / "WowLab.lua"


def _links(tree: str, character: str) -> tuple[list[labaddon.GearSlot], labaddon.CharDB]:
    record = labaddon.read_char(_path(tree, character))
    assert isinstance(record.gear, labaddon.Gear)
    return list(record.gear.slots), record


def _id(case: tuple[str, str, int]) -> str:
    tree, character, count = case
    return f"{tree}-{character.split('/')[-1]}-{count}-links"


@pytest.mark.parser
def test_the_captures_hold_38_links() -> None:
    assert sum(len(_links(tree, ch)[0]) for tree, ch, _ in RECORDS) == 38
    # The raw text agrees: every `|Hitem:` in the four files is in a gear slot.
    in_text = sum(_path(t, c).read_text(encoding="utf-8").count("|Hitem:") for t, c, _ in RECORDS)
    assert in_text == 38


@pytest.mark.parser
@pytest.mark.parametrize("case", RECORDS, ids=_id)
def test_real_links_decode_losslessly(case: tuple[str, str, int]) -> None:
    tree, character, count = case
    slots, record = _links(tree, character)
    assert len(slots) == count
    assert isinstance(record.spec, labaddon.Spec)
    for slot in slots:
        link = itemlink.parse(slot.link)
        assert link.raw == slot.link
        assert link.text() == slot.link
        assert link.colour_form == "named"
        assert re.fullmatch(r"nIQ[0-9]:", link.colour)
        assert link.link_type == "item"
        assert link.tail == "|r"
        found = _ITEM_ID.search(slot.link)
        assert found is not None and link.item_id == int(found.group(1))
        assert slot.link.endswith(f"|h[{link.name}]|h|r") and link.name
        assert ":".join(link.fields) in slot.link
        # The specialization field carries the record's spec id (LAB_FORMATS
        # M11-03 table; 70425 again), and every count fitted.
        assert link.spec_id == record.spec.id
        assert link.complete and link.extra_fields == []
        assert len(link.relic_bonus_ids) == 3
        # No crafter in any link, and no GUID-shaped run anywhere.
        assert link.crafter == "" and not link.guid_shaped
        # Fields 1 to 7 (enchant, gems, suffix, unique) are empty in every capture.
        assert link.fields[1:8] == [""] * 7
        assert link.enchant_id is None and link.gem_ids == [] and link.suffix_id is None
        assert 19 <= len(link.fields) <= 22  # the longest seen is 22


@pytest.mark.parser
def test_the_named_fields_of_one_real_link() -> None:
    """The 70425 chest: a bonus id and one modifier pair, 22 fields."""
    slots, _ = _links("macos-70425", "1/Labchard-Labrealme")
    chest = next(s for s in slots if s.slot == 5)
    link = itemlink.parse(chest.link)
    assert link.name == "Simple Blouse of the Owl"
    assert (link.item_id, link.link_level, link.spec_id, link.context) == (9749, 15, 1490, 1)
    assert link.bonus_ids == [12720]
    assert [(m.type, m.value, m.raw) for m in link.modifiers] == [(28, 5256, ("28", "5256"))]
    assert link.modifiers_mask is None and link.extra_enchant_id is None
    assert len(link.fields) == 22


@pytest.mark.parser
def test_the_crafter_removed_slot_has_a_blank_crafter_field() -> None:
    slots, _ = _links("macos-70425", "1/Labchard-Labrealme")
    removed = [s for s in slots if s.crafter_removed]
    assert [s.slot for s in removed] == [15]
    link = itemlink.parse(removed[0].link)
    assert link.crafter == "" and not link.guid_shaped and link.complete
