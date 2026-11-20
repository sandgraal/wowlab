"""labaddon reads schema 5 (M20-13; ADR-0026, note of 2026-11-20).

Every input here is constructed: the owner's schema-5 capture is taken in
game after M20-13 merges, and until it is committed (with its provenance
row) the schema-5 reader is graded on these files and on the real schema 1
to 4 fixtures, which keep reading as before. Each constructed record starts
from the real 70450 schema-4 record (`macos-70450/`, index row in
`lab/core/tests/fixtures/README.md`), with its schema bumped and the schema-5
keys added the way `lab/addon/WowLab/` writes them (`lab/addon/README.md`,
"Schema 5"). Every macro, name, realm, guild and GUID below is invented.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import labaddon, luadata

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RECORD_70450 = (
    "macos-70450/forever/WTF/Account/90000001#6/1/Dangling-Participle/SavedVariables/WowLab.lua"
)
# Invented values, shaped as the client gives them.
GUID = "Player-0000-0A0B0C0D"
LINK = "|cffa335ee|Hitem:12345::::::::60:::::" + GUID + "::|h[Invented Blade]|h|r"
MACROS = {
    "account": 2,
    "character": 1,
    "first_character_index": 121,
    "list": [
        {
            "index": 1,
            "scope": "account",
            "name": "Testmacro",
            "icon": 134400,
            "body": "#showtooltip\n/cast Invented Spell\n",
        },
        {"index": 2, "scope": "account", "name": "Empty", "icon_text": "INV_Misc_QuestionMark"},
        {"index": 121, "scope": "character", "name": "Ünïcode", "icon": 1, "body": ""},
    ],
    "dropped": 0,
}
BINDINGS_V5 = {
    "commands": 3,
    "list": [
        {"command": "MOVEFORWARD", "keys": ["W", "UP"]},
        {"command": "MACRO Testmacro", "keys": ["SHIFT-1"]},
        {"command": "CLICK InventedBarButton13:LeftButton", "keys": ["CTRL-2"]},
    ],
    "dropped": 0,
}
OWN = {"guid": GUID, "name": "Inventedname", "realm": "Invented Realm", "guild": "Invented Guild"}


def _v4() -> dict[str, Any]:
    values = luadata.parse((FIXTURES / RECORD_70450).read_bytes()).to_python()
    raw = values["WowLabCharDB"]
    assert isinstance(raw, dict) and raw["schema"] == 4
    return raw


def _v5(**changes: Any) -> dict[str, Any]:
    """The real 70450 record as the schema-5 addon would write it
    (constructed)."""
    raw = copy.deepcopy(_v4())
    raw["schema"] = 5
    raw["debug"] = True
    raw["announced"] = 5
    raw["gathered"] = [
        {"section": "gear", "at": 1763600000},
        {"section": "macros", "at": 1763600001},
    ]
    raw["written_at"] = 1763600002
    raw["written_by"] = "logout"
    raw["gear"]["slots"][0]["link"] = LINK
    del raw["gear"]["slots"][0]["crafter_removed"]
    bindings = raw["bindings"]
    for key in ("commands_left_out", "keys_left_out", "probes", "list", "commands"):
        bindings.pop(key, None)
    bindings.update(copy.deepcopy(BINDINGS_V5))
    raw["identity"].update(OWN)
    raw["macros"] = copy.deepcopy(MACROS)
    for section in ("spellbook", "actionbars"):
        raw[section].pop("probes")
        raw[section]["dropped"] = 2
    raw.update(changes)
    return raw


def _char(raw: dict[str, Any]) -> labaddon.CharDBV5:
    char = labaddon.load_char(raw)
    assert isinstance(char, labaddon.CharDBV5)
    return char


def test_constructed_schema_5_reads_every_new_key() -> None:
    char = _char(_v5())
    assert isinstance(char, labaddon.CharDBV4)  # a V4 to every schema-4 reader
    assert (char.debug, char.announced, char.written_at, char.written_by) == (
        True,
        5,
        1763600002,
        "logout",
    )
    assert [(g.section, g.at) for g in char.gathered or []] == [
        ("gear", 1763600000),
        ("macros", 1763600001),
    ]
    assert isinstance(char.gear, labaddon.GearV5)
    assert char.gear.slots[0].link == LINK
    assert char.gear.slots[0].crafter_removed is False
    assert isinstance(char.bindings, labaddon.BindingsV5)
    assert [b.command for b in char.bindings.list_] == [b["command"] for b in BINDINGS_V5["list"]]
    assert (char.bindings.commands_left_out, char.bindings.keys_left_out) == (0, 0)
    assert char.bindings.probes == [] and char.bindings.dropped == 0
    assert isinstance(char.identity, labaddon.IdentityV5)
    assert {f: getattr(char.identity, f) for f in labaddon.OWN_FIELDS} == OWN
    assert isinstance(char.macros, labaddon.Macros)
    assert [m.name for m in char.macros.list_] == ["Testmacro", "Empty", "Ünïcode"]
    assert char.macros.list_[0].body == "#showtooltip\n/cast Invented Spell\n"
    assert char.macros.list_[1].icon_text == "INV_Misc_QuestionMark"
    assert isinstance(char.spellbook, labaddon.SpellbookV5) and char.spellbook.dropped == 2
    assert labaddon.unknown_keys(char) == []


def test_constructed_schema_5_round_trips_through_json() -> None:
    char = _char(_v5())
    again = labaddon.load_char(json.loads(char.model_dump_json(by_alias=True)))
    assert again == char


def test_constructed_schema_5_with_probes_in_a_debug_session() -> None:
    raw = _v5()
    raw["macros"]["probes"] = [
        {
            "api": "GetNumMacros",
            "present": True,
            "calls": 1,
            "errors": 0,
            "answered": 1,
            "secret": 0,
        },
        {
            "api": "GetMacroInfo",
            "present": True,
            "calls": 3,
            "errors": 0,
            "answered": 3,
            "secret": 0,
        },
    ]
    char = _char(raw)
    assert char.macros is not None and not isinstance(char.macros, labaddon.AbsentSection)
    assert [p.api for p in char.macros.probes] == ["GetNumMacros", "GetMacroInfo"]


def test_constructed_schema_5_absent_macros() -> None:
    raw = _v5(macros={"absent": "GetNumMacros missing", "events_unregistered": [], "dropped": 0})
    char = _char(raw)
    assert isinstance(char.macros, labaddon.AbsentProbedSectionV5)
    assert char.macros.absent == "GetNumMacros missing"


def test_constructed_schema_5_identity_value_that_does_not_fit_is_set_aside() -> None:
    raw = _v5()
    raw["identity"]["guid"] = "Creature-0-0-0-0-0"
    raw["identity"]["name"] = "bad\x07name"
    char = _char(raw)
    assert isinstance(char.identity, labaddon.IdentityV5)
    assert char.identity.guid is None and char.identity.name is None
    assert char.identity.guid_set_aside == (
        "guid is not a player GUID (Player-<realm id>-<hex>); set aside by the reader"
    )
    assert char.identity.name_set_aside is not None
    assert "bad" not in char.identity.name_set_aside  # never echoed
    assert char.identity.realm == OWN["realm"]


def _macro(**changes: Any) -> dict[str, Any]:
    macros = copy.deepcopy(MACROS)
    macros["list"][0].update(changes)
    return macros


@pytest.mark.parametrize(
    ("macros", "words"),
    [
        (_macro(index=2), "listed more than once"),
        (_macro(index=3), "outside its scope's range"),
        (_macro(scope="character"), "outside its scope's range"),
        (_macro(name="x" * 256), "1 to 255 characters"),
        (_macro(name=""), "1 to 255 characters"),
        (_macro(body="a\x00b"), "no control character but a line break or tab"),
        (_macro(body="x" * 4097), "at most 4096 characters"),
        (_macro(scope="guild"), "scope"),
    ],
    ids=[
        "constructed-duplicate-index",
        "constructed-index-past-the-count",
        "constructed-account-index-as-character",
        "constructed-name-too-long",
        "constructed-empty-name",
        "constructed-nul-in-body",
        "constructed-body-too-long",
        "constructed-unknown-scope",
    ],
)
def test_constructed_hostile_macros_are_refused(macros: dict[str, Any], words: str) -> None:
    with pytest.raises(labaddon.LabAddonError) as info:
        labaddon.load_char(_v5(macros=macros))
    assert "WowLabCharDB.macros" in str(info.value)
    assert words in str(info.value)


@pytest.mark.parametrize(
    ("changes", "where"),
    [
        ({"written_by": "crash"}, "written_by"),
        ({"gathered": [{"section": "Gear Section", "at": 1}]}, "gathered"),
        ({"debug": 1}, "debug"),
        ({"announced": "5"}, "announced"),
    ],
    ids=[
        "constructed-unknown-writer",
        "constructed-section-not-a-key",
        "constructed-debug-not-a-boolean",
        "constructed-announced-text",
    ],
)
def test_constructed_hostile_top_level_keys_are_refused(
    changes: dict[str, Any], where: str
) -> None:
    with pytest.raises(labaddon.LabAddonError, match=where):
        labaddon.load_char(_v5(**changes))


@pytest.mark.parametrize(
    "command",
    ["MACRO bad\nname", "x" * 1025, ""],
    ids=["constructed-line-break", "constructed-too-long", "constructed-empty"],
)
def test_constructed_hostile_binding_commands_are_refused(command: str) -> None:
    raw = _v5()
    raw["bindings"]["list"][1]["command"] = command
    with pytest.raises(labaddon.LabAddonError, match="bindings"):
        labaddon.load_char(raw)


def test_constructed_gear_link_with_a_crafter_guid_is_refused_at_schema_4() -> None:
    """The schema-4 reader is unchanged: a crafter GUID in a link is not the
    shape schema 4 writes."""
    raw = copy.deepcopy(_v4())
    raw["gear"]["slots"][0]["link"] = LINK
    with pytest.raises(labaddon.LabAddonError, match="gear"):
        labaddon.load_char(raw)


def test_constructed_schema_5_keys_in_a_schema_4_file_are_refused() -> None:
    """A macro's text in a schema-4 file is text in an unknown key."""
    raw = copy.deepcopy(_v4())
    raw["macros"] = copy.deepcopy(MACROS)
    with pytest.raises(
        labaddon.LabAddonError, match=r"unknown key macros\.list\[0\]\.\w+ holds text"
    ):
        labaddon.load_char(raw)


def test_constructed_schema_5_from_lua_text() -> None:
    """End to end from bytes: the real 70450 file with its schema number
    changed and a `macros` section added the way the client writes a table
    (constructed), read through `parse_char`. A byte that is not UTF-8 in a
    macro body is refused."""
    data = (FIXTURES / RECORD_70450).read_bytes()
    nl = b"\r\n" if b"\r\n" in data else b"\n"
    head = b'["schema"] = 4,' + nl
    assert data.count(head) == 1
    macros = nl.join(
        [
            b'["macros"] = {',
            b'["account"] = 1,',
            b'["character"] = 0,',
            b'["first_character_index"] = 121,',
            b'["list"] = {',
            b"{",
            b'["index"] = 1,',
            b'["scope"] = "account",',
            b'["name"] = "Testmacro",',
            b'["icon"] = 134400,',
            b'["body"] = "/cast Invented Spell\\n/say |cffff0000hi|r",',
            b"}, -- [1]",
            b"},",
            b"},",
            b"",
        ]
    )
    good = data.replace(head, b'["schema"] = 5,' + nl + macros)
    char = labaddon.parse_char(good)
    assert isinstance(char, labaddon.CharDBV5) and isinstance(char.macros, labaddon.Macros)
    assert char.macros.list_[0].body == "/cast Invented Spell\n/say |cffff0000hi|r"
    bad = good.replace(b"/cast Invented Spell", b"/cast Invented \xff Spell")
    with pytest.raises(labaddon.LabAddonError, match="macros"):
        labaddon.parse_char(bad)


def test_constructed_account_schema_5_reads() -> None:
    assert type(labaddon.load_account({"schema": 5})) is labaddon.AccountDBV5


def test_the_real_schema_4_record_still_reads_as_schema_4() -> None:
    """Control: the real 70450 record is a CharDBV4, not a CharDBV5."""
    char = labaddon.load_char(_v4())
    assert type(char) is labaddon.CharDBV4
