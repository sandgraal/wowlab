"""The M11-03 lab-addon capture: facts the M11-04 reader and sv-merge rely on.

Three real files from the owner's Forever beta install (1.60.1.70009, macOS),
written by the Lab's own addon (`lab/addon/WowLab/`) and captured with
`scripts/lab_capture.py`: the account `WowLab.lua` (`WowLabDB`) and two
characters' `WowLab.lua` (`WowLabCharDB`), one capture run re-run with
`--reuse-out`, so the two pseudonymised folders are two characters. All three
are also graded by the parametrized SavedVariables suites through their
index rows; this file pins what `docs/LAB_FORMATS.md` (amendment of
2026-09-28, M11-03) reports from them. The committed tree is never handed to
`read()`: bytes are read and parsed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from wowlab_core import luadata

pytestmark = pytest.mark.parser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

ACCOUNT_DIR = "macos/forever/WTF/Account/90000001#6"
ACCOUNT = f"{ACCOUNT_DIR}/SavedVariables/WowLab.lua"
FIRST = f"{ACCOUNT_DIR}/1/Labchard-Labrealmg/SavedVariables/WowLab.lua"
SECOND = f"{ACCOUNT_DIR}/1/Labcharb-Labrealmf/SavedVariables/WowLab.lua"
CHARACTERS = [FIRST, SECOND]

# As each file reads (the fixture wins over session notes, which expected 2
# for the first character). A value above 1 can only come from the addon
# reading the value the file held and adding 1, so each shows the counter
# persisting and incrementing across sessions that ended in a write.
LOADS = {FIRST: "4", SECOND: "2"}
LEVEL = {FIRST: 13, SECOND: 10}


def _bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _char_db(name: str) -> dict[str, Any]:
    doc = luadata.parse(_bytes(name))
    (assignment,) = doc.assignments
    assert assignment.name == "WowLabCharDB"
    value = doc.to_python()["WowLabCharDB"]
    assert isinstance(value, dict)
    return value


def _entry(table: luadata.LuaTable, key: str) -> luadata.Entry:
    (entry,) = [
        e for e in table.entries if isinstance(e.key, luadata.LuaString) and e.key.value == key
    ]
    return entry


@pytest.mark.parametrize("name", [ACCOUNT, *CHARACTERS])
def test_lab_addon_capture_round_trips_byte_for_byte(name: str) -> None:
    data = _bytes(name)
    assert luadata.serialize(luadata.parse(data)) == data


def test_account_file_holds_only_the_schema() -> None:
    doc = luadata.parse(_bytes(ACCOUNT))
    (assignment,) = doc.assignments
    assert assignment.name == "WowLabDB"
    assert doc.to_python() == {"WowLabDB": {"schema": 1}}


@pytest.mark.parametrize("name", CHARACTERS)
def test_character_file_probe_loads(name: str) -> None:
    assert _char_db(name)["probe"] == {"loads": int(LOADS[name])}
    top = luadata.parse(_bytes(name)).assignments[0].value
    assert isinstance(top, luadata.LuaTable)
    probe = _entry(top, "probe").value
    assert isinstance(probe, luadata.LuaTable)
    loads = _entry(probe, "loads").value
    assert isinstance(loads, luadata.LuaNumber)
    assert loads.raw == LOADS[name]


@pytest.mark.parametrize("name", CHARACTERS)
def test_character_file_absent_sections_carry_the_addons_reasons(name: str) -> None:
    db = _char_db(name)
    assert db["customization"] == {"absent": "no barber-shop visit recorded with the addon enabled"}
    assert db["collections"]["appearances"] == {
        "absent": "not gathered: asking the client for the appearance collection crashed"
        " the Forever client once (M11-03); the addon no longer asks"
    }


@pytest.mark.parametrize("name", CHARACTERS)
def test_character_file_schema_and_client(name: str) -> None:
    db = _char_db(name)
    assert db["schema"] == 1
    assert db["client"] == {"interface": 16001, "version": "1.60.1", "build": "70009"}


@pytest.mark.parametrize("name", CHARACTERS)
def test_character_file_legacy_talents_present_below_level_25_with_no_ranks(name: str) -> None:
    legacy = _char_db(name)["talents"]["legacy"]
    assert legacy["player_level"] == LEVEL[name]
    assert legacy["legacy_ui"] is True
    nodes = [
        n for config in legacy["configs"] for tree in config["trees"] for n in (tree["nodes"] or [])
    ]
    assert nodes, "the Legacy candidate config lists nodes below level 25"
    assert all(n["ranks_purchased"] == 0 and n["current_rank"] == 0 for n in nodes)


def test_two_characters_share_the_top_level_shape() -> None:
    # The sv-merge pair (docs/LAB_PLAN.md §13.4): same keys in the same order.
    def keys(name: str) -> list[str]:
        top = luadata.parse(_bytes(name)).assignments[0].value
        assert isinstance(top, luadata.LuaTable)
        return [e.key.value for e in top.entries if isinstance(e.key, luadata.LuaString)]

    assert keys(FIRST) == keys(SECOND)
    assert _char_db(FIRST) != _char_db(SECOND)
