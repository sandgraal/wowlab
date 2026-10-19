"""`itemlink` on constructed boundary and hostile links (L8: labelled `constructed`; M14-05).

No capture holds these: the hex colour form `|cffRRGGBB` (unobserved on
Forever), a link with no closing `|h`, a GUID-shaped run where the crafter
field is, a field count past the longest seen (22), and counts that do not
fit. Each link is written by hand in the shape of the real ones
(`test_itemlink_fixtures.py`); every accepted one must still give back its
input byte for byte.
"""

from __future__ import annotations

import pytest

from wowlab_core import itemlink

pytestmark = pytest.mark.parser

REAL_SHAPE = "|cnIQ2:|Hitem:9749::::::::15:1490::1:1:12720:1:28:5256:::::|h[Simple Blouse]|h|r"


def _round_trip(raw: str) -> itemlink.ItemLink:
    link = itemlink.parse(raw)
    assert link.raw == raw and link.text() == raw
    return link


def test_constructed_hex_colour_form() -> None:
    link = _round_trip("|cff1eff00|Hitem:9749::::::::15:1490:::::::::|h[Simple Blouse]|h|r")
    assert link.colour == "ff1eff00" and link.colour_form == "hex"
    assert link.item_id == 9749 and link.complete


def test_constructed_upper_case_hex_and_an_unknown_colour_are_kept() -> None:
    assert _round_trip("|cFF1EFF00|Hitem:1|h[X]|h|r").colour_form == "hex"
    other = _round_trip("|cabc|Hitem:1|h[X]|h|r")
    assert other.colour == "abc" and other.colour_form == "other"


@pytest.mark.parametrize(
    ("raw", "fault"),
    [
        ("|cnIQ2:|Hitem:9749::::::::15:1490:::::::::[Simple Blouse]|r", "no closing |h"),
        ("|cnIQ2:|Hitem:9749::::::::15:1490:::::::::", "no closing |h"),
        ("|Hitem:9749|h[X]|h|r", "starts with a colour code"),
        ("|cnIQ2:item:9749|h[X]|h|r", "no |H"),
        ("|c|Hitem:9749|h[X]|h|r", "colour code is empty"),
        ("|cnIQ2:|Hspell:348|h[Immolate]|h|r", "link type is 'spell'"),
        ("|cnIQ2:|Hitem|h[X]|h|r", "link type is 'item'"),
        ("|cnIQ2:|Hitem:1|hX|h|r", "no [ after"),
        ("|cnIQ2:|Hitem:1|h[X|r", "no ]|h"),
        ("|cnIQ2:|Hitem:1|h[X]|h", "does not end with |r"),
        ("|cnIQ2:|Hitem:1|h[X]|h|r|cnIQ2:|Hitem:2|h[Y]|h|r", "does not end with |r"),
        ("", "starts with a colour code"),
    ],
    ids=[
        "constructed-no-closing-h",
        "constructed-truncated-after-payload",
        "constructed-no-colour",
        "constructed-no-H",
        "constructed-empty-colour",
        "constructed-spell-link",
        "constructed-no-colon",
        "constructed-no-bracket",
        "constructed-no-closing-bracket",
        "constructed-no-r",
        "constructed-two-links",
        "constructed-empty",
    ],
)
def test_constructed_not_an_item_link(raw: str, fault: str) -> None:
    with pytest.raises(itemlink.ItemLinkError, match=fault.replace("|", r"\|").replace("[", r"\[")):
        itemlink.parse(raw)


def test_constructed_the_error_carries_the_offset() -> None:
    raw = "|cnIQ2:|Hitem:9749::::::::15:1490:::::::::[Simple Blouse]|r"
    with pytest.raises(itemlink.ItemLinkError) as caught:
        itemlink.parse(raw)
    assert caught.value.offset == raw.index("item:")


def test_constructed_too_long_is_refused_before_it_is_split() -> None:
    raw = "|cnIQ2:|Hitem:1" + ":" * itemlink.MAX_LINK_CHARS + "|h[X]|h|r"
    with pytest.raises(itemlink.ItemLinkError, match="longer than"):
        itemlink.parse(raw)


def test_constructed_guid_shaped_crafter_field() -> None:
    """The real 19-field shape with a `Player-<realm>-<hex>` run in the crafter field."""
    raw = "|cnIQ3:|Hitem:2310::::::::15:1490::::::::Player-1234-0ABCDEF0:|h[Cloak]|h|r"
    link = _round_trip(raw)
    assert len(link.fields) == 19
    assert link.crafter == "Player-1234-0ABCDEF0"
    assert link.guid_shaped and link.complete and link.extra_fields == []


def test_constructed_guid_shaped_run_outside_the_crafter_field() -> None:
    link = _round_trip("|cnIQ3:|Hitem:2310:Player-1-ab|h[Cloak]|h|r")
    assert link.guid_shaped and link.enchant_id is None and link.fields[1] == "Player-1-ab"


def test_constructed_field_count_past_the_longest_seen() -> None:
    """31 fields: every named group filled, then two fields past the extra enchant."""
    payload = "1:2:3:4:5:6:7:8:9:10:11:12:3:101:102:103:2:9:8:7:6:1:5:0:2:5:6::44:x:-7"
    link = _round_trip(f"|cnIQ4:|Hitem:{payload}|h[Long]|h|r")
    assert len(link.fields) == 31
    assert (link.item_id, link.enchant_id, link.gem_ids) == (1, 2, [3, 4, 5, 6])
    assert (link.suffix_id, link.unique_id, link.link_level, link.spec_id) == (7, 8, 9, 10)
    assert (link.modifiers_mask, link.context) == (11, 12)
    assert link.bonus_ids == [101, 102, 103]
    assert [(m.type, m.value) for m in link.modifiers] == [(9, 8), (7, 6)]
    assert link.relic_bonus_ids == [[5], [], [5, 6]]
    assert link.crafter == "" and link.extra_enchant_id == 44
    assert link.extra_fields == ["x", "-7"] and link.complete


def test_constructed_a_count_past_the_end_stops_the_naming() -> None:
    link = _round_trip("|cnIQ2:|Hitem:1::::::::15:1490:::9:12720:12721|h[X]|h|r")
    assert not link.complete
    assert link.bonus_ids == [] and link.modifiers == [] and link.crafter is None
    assert link.extra_fields == ["9", "12720", "12721"]
    assert link.spec_id == 1490  # the fixed fields before the count are still named


def test_constructed_a_count_that_is_not_a_number_stops_the_naming() -> None:
    link = _round_trip("|cnIQ2:|Hitem:1::::::::15:1490:::x:12720|h[X]|h|r")
    assert not link.complete and link.extra_fields == ["x", "12720"]


def test_constructed_a_short_payload_names_what_is_there() -> None:
    link = _round_trip("|cnIQ2:|Hitem:6948|h[Hearthstone]|h|r")
    assert link.fields == ["6948"] and link.item_id == 6948
    assert link.spec_id is None and link.bonus_ids == [] and link.crafter is None
    assert link.complete and link.extra_fields == []


def test_constructed_odd_fields_stay_raw() -> None:
    digits = "9" * 40
    link = _round_trip(f"|cnIQ2:|Hitem:{digits}:-5:+3|h[]|h|r")
    assert link.item_id is None and link.fields[0] == digits  # too long to be a number
    assert link.enchant_id == -5 and link.gem_ids == []  # "+3" is not a whole number here
    assert link.name == ""


def test_constructed_the_real_shape_with_a_name_holding_link_markup() -> None:
    raw = REAL_SHAPE.replace("[Simple Blouse]", "[a |cff00ff00 b]")
    assert _round_trip(raw).name == "a |cff00ff00 b"
