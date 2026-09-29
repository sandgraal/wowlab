"""`wowlab_core.labaddon` on the M11-03 lab-addon capture (M11-04).

The three real `WowLab.lua` files from the owner's Forever beta install
(1.60.1.70009, macOS), index rows in `fixtures/README.md`: the account file
(`WowLabDB`) and two characters (`WowLabCharDB`). Each tolerance the ticket
lists is checked here where the capture holds it; the ones the capture does
not hold are in `test_labaddon_reader_constructed.py`. The committed tree is
never handed to a reader that opens files: bytes are read here and parsed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from wowlab_core import labaddon, luadata

pytestmark = pytest.mark.parser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ACCOUNT_DIR = "macos/forever/WTF/Account/90000001#6"
ACCOUNT = f"{ACCOUNT_DIR}/SavedVariables/WowLab.lua"
FIRST = f"{ACCOUNT_DIR}/1/Labchard-Labrealmg/SavedVariables/WowLab.lua"
SECOND = f"{ACCOUNT_DIR}/1/Labcharb-Labrealmf/SavedVariables/WowLab.lua"
CHARACTERS = [FIRST, SECOND]
LEVEL = {FIRST: 13, SECOND: 10}
LOADS = {FIRST: 4, SECOND: 2}


def _bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _raw(name: str) -> dict[str, Any]:
    value = luadata.parse(_bytes(name)).to_python()["WowLabCharDB"]
    assert isinstance(value, dict)
    return value


def _char(name: str) -> labaddon.CharDBV1:
    return labaddon.parse_char(_bytes(name))


def _text(name: str) -> str:
    return "\n".join(labaddon.describe(_char(name)))


def _at(value: Any, path: str) -> Any:
    for part in path.split("."):
        value = value[int(part)] if part.isdigit() else value[part]
    return value


# ─── every file reads ────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", CHARACTERS)
def test_every_character_capture_reads_and_describes(name: str) -> None:
    char = _char(name)
    assert char.schema_ == 1
    assert char.probe is not None
    assert char.probe.loads == LOADS[name]
    lines = labaddon.describe(char)
    assert lines
    assert all(isinstance(line, str) for line in lines)


@pytest.mark.parametrize("name", CHARACTERS)
def test_every_character_capture_round_trips_through_json(name: str) -> None:
    char = _char(name)
    again = labaddon.CharDBV1.model_validate_json(char.model_dump_json())
    assert again == char
    dumped = char.model_dump(mode="json")
    assert dumped["schema"] == 1
    assert "class" in dumped["talents"]
    assert "list" in dumped["currencies"]


@pytest.mark.parametrize("name", CHARACTERS)
def test_capture_has_no_unknown_keys(name: str) -> None:
    assert labaddon.unknown_keys(_char(name)) == []


def test_account_file_holds_only_the_schema() -> None:
    account = labaddon.parse_account(_bytes(ACCOUNT))
    assert account.schema_ == 1
    assert account.model_extra == {}
    (line,) = labaddon.describe_account(account)
    assert "schema 1, nothing else" in line
    assert "per character" in line


def test_account_file_is_not_a_character_file() -> None:
    with pytest.raises(labaddon.LabAddonError, match="assigns no WowLabCharDB"):
        labaddon.parse_char(_bytes(ACCOUNT))


# ─── an empty table read back as a dict where a list is expected ─────────────

# Where the capture itself holds `{}` for a list the addon writes.
REAL_EMPTY = [
    "talents.legacy.configs.0.trees.0.nodes",
    "talents.legacy.configs.0.trees.0.currencies",
    "currencies.list",
    "collections.mounts.collected",
    "collections.toys.collected",
    "collections.pets.species",
]


@pytest.mark.parametrize("path", REAL_EMPTY)
@pytest.mark.parametrize("name", CHARACTERS)
def test_empty_table_where_a_list_is_expected_reads_as_empty_list(name: str, path: str) -> None:
    assert _at(_raw(name), path) == {}  # luadata gives a dict for `{}`
    model = _at(_char(name).model_dump(mode="json"), path)
    assert model == []


# ─── a missing key means the client returned nil ─────────────────────────────


@pytest.mark.parametrize("name", CHARACTERS)
def test_last_selected_config_missing_is_optional_not_absent(name: str) -> None:
    raw_class = _raw(name)["talents"]["class"]
    assert "last_selected_config" not in raw_class
    assert "last_selected_config_absent" not in raw_class
    talents = _char(name).talents
    assert talents is not None
    assert isinstance(talents.class_, labaddon.ClassTalents)
    assert talents.class_.last_selected_config is None
    assert talents.class_.last_selected_config_absent is None
    assert "last selected saved loadout: not returned by the client" in _text(name)


@pytest.mark.parametrize("name", CHARACTERS)
def test_sub_tree_and_probe_lost_missing_are_none(name: str) -> None:
    raw = _raw(name)
    node = raw["talents"]["class"]["config"]["trees"][0]["nodes"][0]
    assert "sub_tree" not in node
    assert "lost" not in raw["probe"]
    char = _char(name)
    talents = char.talents
    assert talents is not None and isinstance(talents.class_, labaddon.ClassTalents)
    config = talents.class_.config
    assert isinstance(config, labaddon.TraitConfig)
    assert all(n.sub_tree is None for t in config.trees for n in t.nodes)
    assert char.probe is not None and char.probe.lost is None
    assert "lost" not in _text(name)


# ─── absent with a reason: a whole section ───────────────────────────────────


@pytest.mark.parametrize("name", CHARACTERS)
def test_absent_sections_carry_the_addons_reason(name: str) -> None:
    char = _char(name)
    assert isinstance(char.customization, labaddon.AbsentSection)
    assert char.customization.absent == "no barber-shop visit recorded with the addon enabled"
    assert char.collections is not None
    assert isinstance(char.collections.appearances, labaddon.AbsentSection)
    text = _text(name)
    assert "Customization: absent (no barber-shop visit recorded with the addon enabled)" in text
    assert "Appearances: absent (not gathered: asking the client" in text


@pytest.mark.parametrize("name", CHARACTERS)
def test_no_gathered_section_in_the_capture_has_events_unregistered(name: str) -> None:
    assert "events the client did not know" not in _text(name)


# ─── talents.legacy present, every node at rank 0, cap 0 ─────────────────────


@pytest.mark.parametrize("name", CHARACTERS)
def test_legacy_present_with_nothing_spent(name: str) -> None:
    talents = _char(name).talents
    assert talents is not None
    legacy = talents.legacy
    assert isinstance(legacy, labaddon.LegacyTalents)
    assert legacy.player_level == LEVEL[name]
    assert (
        labaddon.legacy_headline(legacy)
        == "Legacy candidates: present, nothing spent, 0 points available"
    )
    legacy_lines = [line for line in _text(name).splitlines() if "Legacy" in line or "    " in line]
    joined = "\n".join(legacy_lines).casefold()
    assert "empty" not in joined
    assert "locked" not in joined
    assert (
        f"Legacy candidates: present, nothing spent, 0 points available (level {LEVEL[name]})"
        in (_text(name))
    )


# ─── raw client enum numbers for config type ─────────────────────────────────


@pytest.mark.parametrize("name", CHARACTERS)
def test_config_type_is_the_raw_enum_number(name: str) -> None:
    lines = _text(name).splitlines()
    class_line = next(line for line in lines if line.startswith("Class talents: config"))
    assert "type 4 (the client's raw enum number)" in class_line
    legacy_config = next(line for line in lines if "type 3" in line)
    assert "(the client's raw enum number)" in legacy_config
    for line in (class_line, legacy_config):
        for retail_name in ("Combat", "Generic", "Profession", "Invalid", "Trait"):
            assert retail_name not in line.split(", found by")[0]


# ─── gear: sparse slots keyed by `slot`; the equipped average ────────────────


def test_gear_slots_are_keyed_by_slot_not_position() -> None:
    first = _char(FIRST).gear
    second = _char(SECOND).gear
    assert isinstance(first, labaddon.Gear) and isinstance(second, labaddon.Gear)
    assert [s.slot for s in first.slots] == [5, 6, 7, 8, 9, 10, 15, 16, 18]
    assert 18 not in [s.slot for s in second.slots]
    assert first.slots[0].slot == 5  # position 0 holds slot 5
    text = _text(FIRST)
    assert "slot 18: item 5071 [Shadow Wand], item level 14" in text
    assert "slot  5: item 9749 [Simple Blouse of the Owl], item level 15" in text
    assert "slots 1 to 19 as the client numbers them, 9 filled" in text


@pytest.mark.parametrize("name", CHARACTERS)
def test_equipped_average_is_never_called_a_character_sheet_figure(name: str) -> None:
    text = _text(name)
    assert "sheet" not in text.casefold()
    assert "as the client reports it" in text
    assert "how the client computes it is not known" in text


def test_equipped_average_keeps_the_float() -> None:
    gear = _char(FIRST).gear
    assert isinstance(gear, labaddon.Gear)
    assert isinstance(gear.average, labaddon.GearAverage)
    assert gear.average.equipped == 3.3125


# ─── professions by skill line ───────────────────────────────────────────────


@pytest.mark.parametrize("name", CHARACTERS)
def test_professions_are_named_by_skill_line(name: str) -> None:
    raw = _raw(name)["professions"]["list"]
    lines = _text(name).splitlines()
    start = lines.index("Professions (by skill line)")
    shown = lines[start + 1 : start + 1 + len(raw)]
    for entry, line in zip(raw, shown, strict=True):
        assert line.startswith(f"  skill line {entry['skill_line']}: rank {entry['rank']} of ")
    joined = "\n".join(shown).casefold()
    for word in ("position", "primary", "secondary", "archaeology", "fishing", "cooking"):
        assert word not in joined


# ─── empty currencies and collections: "none recorded", with the filter ──────


@pytest.mark.parametrize("name", CHARACTERS)
def test_empty_currencies_and_collections_read_none_recorded(name: str) -> None:
    text = _text(name)
    assert (
        "Currencies: none recorded (read through the currency panel: filter 1, "
        "0 collapsed headers)" in text
    )
    assert "Mounts: none recorded (the journal read unfiltered)" in text
    assert (
        "Toys: none recorded (read through the toy box's filter: collected shown yes, "
        "uncollected shown yes, unusable shown yes)" in text
    )
    assert "Pets: none recorded (read through the pet journal's filters; default filters: yes)" in (
        text
    )


# ─── skip list absent in the capture ─────────────────────────────────────────


@pytest.mark.parametrize("name", CHARACTERS)
def test_capture_has_no_skip_list(name: str) -> None:
    char = _char(name)
    assert char.skip is None
    assert labaddon.skip_known(char) == ([], [])
    assert "Switched off by the owner (skip): none recorded" in _text(name)
