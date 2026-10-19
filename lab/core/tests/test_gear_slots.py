"""The shipped slot table `gear_slots.toml` and its loader (docs/LAB_PLAN.md §16.2, M14-05).

Every slot number and colour code the committed `WowLab.lua` captures hold
has a name and a word in the shipped file, and the file names no slot or
colour twice. The player's own table (in a user config folder redirected
into `tmp_path`) replaces only what it sets. Inputs labelled `constructed`
are tables written by the test.
"""

from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path

import platformdirs
import pytest

from wowlab_core import gear, itemlink, labaddon

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RECORDS = [
    "macos/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua",
    "macos/forever/WTF/Account/90000001#6/1/Labcharb-Labrealmf/SavedVariables/WowLab.lua",
    "macos-70058/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua",
    "macos-70425/forever/WTF/Account/90000001#6/1/Labchard-Labrealme/SavedVariables/WowLab.lua",
]


@pytest.fixture(autouse=True)
def user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "userconfig" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: config)
    return config


def _shipped_text() -> str:
    return resources.files("wowlab_core").joinpath(gear.SLOTS_FILE).read_text(encoding="utf-8")


def _gears() -> list[labaddon.Gear]:
    out = []
    for rel in RECORDS:
        record = labaddon.read_char(FIXTURES / rel)
        assert isinstance(record.gear, labaddon.Gear)
        out.append(record.gear)
    return out


def test_every_slot_number_in_the_captures_has_a_name() -> None:
    table = gear.load_slot_table().table
    numbers = set()
    for g in _gears():
        numbers |= {s.slot for s in g.slots}
        numbers |= set(range(g.first_slot, g.last_slot + 1))
    assert numbers == set(range(1, 20))  # INVSLOT_FIRST/LAST_EQUIPPED 1 and 19 (M11-03)
    assert numbers <= set(table.slots)


def test_every_colour_in_the_captures_has_a_quality_word() -> None:
    table = gear.load_slot_table().table
    colours = {itemlink.parse(s.link).colour for g in _gears() for s in g.slots}
    assert colours == {"nIQ0:", "nIQ1:", "nIQ2:"}
    assert {c: gear.quality_word(c, table) for c in sorted(colours)} == {
        "nIQ0:": "Poor",
        "nIQ1:": "Common",
        "nIQ2:": "Uncommon",
    }


def test_the_shipped_file_has_no_duplicate() -> None:
    raw = tomllib.loads(_shipped_text())  # a repeated key is a TOML error itself
    slots = raw["slots"]
    assert len({int(k) for k in slots}) == len(slots)  # no "1" beside "01"
    assert len(set(slots.values())) == len(slots)  # no two slots share a name
    quality = raw["quality"]
    assert len({k.casefold() for k in quality}) == len(quality)
    named = {k: v for k, v in quality.items() if k.startswith("n")}
    assert len(set(named.values())) == len(named)  # one named code per word
    hexed = {k: v for k, v in quality.items() if not k.startswith("n")}
    assert len(set(hexed.values())) == len(hexed)  # one hex code per word


def test_the_shipped_file_is_the_default(user_config: Path) -> None:
    source = gear.load_slot_table()
    assert source.source == "default"
    assert source.user_file == str(user_config / gear.SLOTS_FILE)
    assert source.shipped.endswith(gear.SLOTS_FILE)
    assert source.table.slots[16] == "Main Hand" and source.table.slots[18] == "Ranged"
    assert "[verify]" in _shipped_text()


def test_constructed_a_user_table_replaces_only_what_it_sets(user_config: Path) -> None:
    user_config.mkdir(parents=True)
    (user_config / gear.SLOTS_FILE).write_text(
        '[slots]\n18 = "Relic"\n[quality]\n"nIQ2:" = "Green"\n', encoding="utf-8"
    )
    source = gear.load_slot_table()
    assert source.source == "user"
    assert source.table.slots[18] == "Relic" and source.table.slots[1] == "Head"
    assert source.table.quality["nIQ2:"] == "Green" and source.table.quality["nIQ0:"] == "Poor"


@pytest.mark.parametrize(
    ("text", "fault"),
    [
        ("[slots]\n1 = 'A'\n01 = 'B'\n", "are one slot"),
        ("[slots]\nhead = 'Head'\n", "not a slot number"),
        ("[slots]\n0 = 'Zero'\n", "not a slot number"),
        ("[slots]\n1 = ''\n", "a name is 1 to 64"),
        ("[slots]\n1 = 3\n", "a name is 1 to 64"),
        ("[quality]\n'n|IQ2:' = 'X'\n", "not a colour code"),
        ("[quality]\n'ff1eff00' = 'A'\n'FF1EFF00' = 'B'\n", "are one code"),
        ("[colours]\n", "unknown table"),
        ("slots = 1\n", "must be a table"),
        ("[slots\n", "not valid TOML"),
    ],
    ids=[
        "constructed-one-slot-twice",
        "constructed-slot-name-key",
        "constructed-slot-zero",
        "constructed-empty-name",
        "constructed-number-name",
        "constructed-bad-colour",
        "constructed-one-code-twice",
        "constructed-unknown-table",
        "constructed-not-a-table",
        "constructed-not-toml",
    ],
)
def test_constructed_a_bad_table_is_refused(text: str, fault: str) -> None:
    with pytest.raises(gear.SlotTableError, match=fault):
        gear.parse_slot_table(text)


def test_constructed_a_bad_user_file_names_the_file(user_config: Path) -> None:
    user_config.mkdir(parents=True)
    path = user_config / gear.SLOTS_FILE
    path.write_text("[slots]\n1 = ''\n", encoding="utf-8")
    with pytest.raises(gear.SlotTableError, match="a name is 1 to 64") as caught:
        gear.load_slot_table()
    assert str(path) in str(caught.value)
