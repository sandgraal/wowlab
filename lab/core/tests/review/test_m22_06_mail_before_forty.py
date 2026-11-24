# Probe from review of m22/06-twink-gap-view; reproduces a level-29 hunter offered mail it cannot wear before level 40
"""`twink.toml` says its class gear is "from Classic-era class rules", under
which hunters and shamans learn Mail at level 40 and warriors and paladins
learn Plate at level 40. `[classes]` lists mail (and plate) with no level,
so for the 19, 29 and 39 brackets the panel ranks armour the character
cannot equip. Whether Forever keeps the level-40 rule is [verify] (M22-O1).

The tables are the real recordings of 1.60.1.70888 under `fixtures/wago/`;
the character (a hunter, class id 3, Undead) is constructed (L8: a boundary
case of the class rules). Rough Bronze Leggings (2865) is mail, required
level 16, uncommon, with stats, and is in the subsets.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from wowlab_core import twink

WAGO = Path(__file__).resolve().parents[1] / "fixtures" / "wago"
BUILD = "1.60.1.70888"
MAIL_LEGS = 2865  # Rough Bronze Leggings: ClassID 4, SubclassID 3 (mail), RequiredLevel 16


def _rows(name: str) -> Iterator[dict[str, str]]:
    path = WAGO / f"{name}.{BUILD}.csv"
    if not path.is_file():
        path = WAGO / f"{name}.{BUILD}.subset.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def _hunter_legs(bracket: int) -> list[int]:
    tables = twink.load_tables(BUILD, _rows)
    panel = twink.build_panel(
        tables,
        twink.shipped_config(),
        filled=[],
        slot_names={},
        class_file="HUNTER",
        class_id=3,
        race_id=5,
        level=bracket,
        bracket=bracket,
        weights_name=None,
        weights_source="default",
        version=None,
        top=twink.TOP_MAX,
    )
    [legs] = [s for s in panel.slots if s.slot == 7]
    return [c.item_id for c in legs.candidates]


def test_positive_control_mail_is_a_candidate_from_the_level_49_bracket() -> None:
    assert MAIL_LEGS in _hunter_legs(49)


def test_a_level_29_hunter_is_not_offered_mail() -> None:
    assert MAIL_LEGS not in _hunter_legs(29)
