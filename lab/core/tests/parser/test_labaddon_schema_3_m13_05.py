"""`wowlab_core.labaddon` reads schema 3, the addon format M13-05 introduces.

Schema 3 is schema 2 plus `left_out_config` or `left_out_config_absent` on
`talents.legacy` (M11-35, folded into M13-05) and three sections the addon
reads out of combat: `spellbook`, `actionbars` and `bindings`, each with
`probes` (what each API it asked did). Schema 1 and 2 files are read exactly
as before.

The real schema-1 captures are read unchanged. The first real schema-3
capture (1.60.1.70425, 2026-10-16) is graded in
`test_labaddon_schema_3_capture_m13_05.py`. The cases here are
constructed (L8: boundary and hostile cases, `constructed` in the test id). Each starts from
the real M11-23 record (1.60.1.70058, index row in `fixtures/README.md`),
with `schema` set to 3 and keys added in the shape the addon's own source
writes. The reasons and the probed API names are read from
`lab/addon/WowLab/Actions.lua` and `Talents.lua`, not typed here, so the
reader and the addon cannot drift apart. Hostile characters are built with
`chr()`, so this file holds none of them raw.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import labaddon, luadata

pytestmark = pytest.mark.parser

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RECORD_70058 = (
    "macos-70058/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua"
)
REAL_CHARACTERS = [
    "macos/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua",
    "macos/forever/WTF/Account/90000001#6/1/Labcharb-Labrealmf/SavedVariables/WowLab.lua",
    RECORD_70058,
]
ADDON = ROOT / "lab" / "addon" / "WowLab"
ACTIONS = (ADDON / "Actions.lua").read_text(encoding="utf-8")
TALENTS = (ADDON / "Talents.lua").read_text(encoding="utf-8")

_BASE: dict[str, Any] = luadata.parse((FIXTURES / RECORD_70058).read_bytes()).to_python()[  # type: ignore[assignment]
    "WowLabCharDB"
]

# What the addon's source writes, read from it.
PROBED = tuple(re.findall(r'probe\("([A-Za-z_.]+)", ', ACTIONS))
COMBAT_REASONS = tuple(
    re.findall(r'^\s+return "([^"]*combat[^"]*)"$', ACTIONS, re.M)
    + re.findall(r'^local IN_COMBAT = "([^"]+)"$', ACTIONS, re.M)
)
LEFT_OUT_REASONS = tuple(
    sorted(set(re.findall(r'return nil, "(C_ClassTalents\.GetActiveConfigID [^"]+)"', TALENTS)))
)
NEW_KEYS = ("spellbook", "actionbars", "bindings")


def _probe(
    api: str, calls: int = 0, errors: int = 0, answered: int = 0, secret: int = 0
) -> dict[str, Any]:
    return {
        "api": api,
        "present": True,
        "calls": calls,
        "errors": errors,
        "answered": answered,
        "secret": secret,
    }


def _missing(api: str) -> dict[str, Any]:
    return {"api": api, "present": False, "calls": 0, "errors": 0, "answered": 0, "secret": 0}


def _looked_up() -> list[dict[str, Any]]:
    """The two probes each section appends last, looked up and never called
    (round 3): here the client has C_Secrets and not canaccessvalue."""
    return [_probe("C_Secrets"), _missing("canaccessvalue")]


def _spellbook() -> dict[str, Any]:
    return {
        "item_types": {"None": 0, "Spell": 1, "FutureSpell": 2, "PetAction": 3, "Flyout": 4},
        "skill_lines": [
            {"index": 1, "offset": 0, "count": 2, "guild": False, "hidden": False},
            {"index": 2, "offset": 2, "count": 1, "guild": False, "hidden": False, "spec_id": 1486},
        ],
        "list": [
            {
                "slot": 1,
                "skill_line": 1,
                "type": 1,
                "action_id": 6603,
                "spell_id": 6603,
                "spell_name": "Auto Attack",
                "passive": False,
                "off_spec": False,
            },
            {
                "slot": 2,
                "skill_line": 1,
                "type": 1,
                "action_id": 20154,
                "spell_id": 20154,
                "spell_name": "Seal of Righteousness",
                "subtext": "Rank 1",
                "passive": False,
                "off_spec": False,
            },
            {
                "slot": 3,
                "skill_line": 2,
                "type": 2,
                "action_id": 20375,
                "spell_id": 20375,
                "spell_name": "Seal of Command",
                "passive": True,
                "off_spec": False,
            },
        ],
        "probes": [
            _probe("C_SpellBook.GetNumSpellBookSkillLines", 1, 0, 1),
            _probe("C_SpellBook.GetSpellBookSkillLineInfo", 2, 0, 2),
            _probe("C_SpellBook.GetSpellBookItemInfo", 3, 0, 3),
            _probe("C_SpellBook.GetSpellBookItemName", 3, 0, 3),
            _missing("issecretvalue"),
            *_looked_up(),
        ],
        "events_unregistered": {},
    }


def _actionbars() -> dict[str, Any]:
    return {
        "first_slot": 1,
        "last_slot": 180,
        "slots": [
            {"slot": 1, "type": "spell", "id": 6603, "sub_type": "spell"},
            {"slot": 2, "type": "macro", "id": 121},
            {"slot": 3, "type": "item", "id": 6948},
            {"slot": 4, "type": "equipmentset"},
            {"slot": 5, "type": "macro", "id": 20154, "sub_type": "spell"},
        ],
        "probes": [
            _probe("GetActionInfo", 180, 0, 5),
            _probe("GetMacroInfo", 1, 0, 1),
            _probe("issecretvalue", 15, 0, 15),
            *_looked_up(),
        ],
        "events_unregistered": {},
    }


def _bindings() -> dict[str, Any]:
    return {
        "binding_set": 2,
        "account_bindings": 1,
        "character_bindings": 2,
        "commands": 412,
        "commands_left_out": 1,
        "keys_left_out": 0,
        "list": [
            {"command": "ACTIONBUTTON1", "keys": ["1"]},
            {"command": "MOVEFORWARD", "keys": ["W", "UP"]},
            {"command": "MULTIACTIONBAR1BUTTON3", "keys": ["SHIFT-3"]},
        ],
        "probes": [
            _probe("GetCurrentBindingSet", 1, 0, 1),
            _probe("GetNumBindings", 1, 0, 1),
            _probe("GetBinding", 412, 0, 412),
            _probe("GetBindingKey", 411, 0, 3, 1),
            _probe("issecretvalue", 830, 0, 830),
            *_looked_up(),
        ],
        "events_unregistered": {},
    }


def _base_v3() -> dict[str, Any]:
    raw = copy.deepcopy(_BASE)
    raw["schema"] = 3
    raw["talents"]["legacy"]["left_out_config"] = raw["talents"]["class"]["config"]["id"]
    raw["spellbook"] = _spellbook()
    raw["actionbars"] = _actionbars()
    raw["bindings"] = _bindings()
    return raw


def _v3(raw: object) -> labaddon.CharDBV3:
    char = labaddon.load_char(raw)
    assert isinstance(char, labaddon.CharDBV3)
    return char


def _text(char: labaddon.CharDB) -> str:
    return "\n".join(labaddon.describe(char))


def _refused(raw: object, *fragments: str) -> str:
    with pytest.raises(labaddon.LabAddonError) as info:
        labaddon.load_char(raw)
    message = str(info.value)
    for fragment in fragments:
        assert fragment in message, message
    return message


def test_the_addon_source_lists_what_these_tests_use() -> None:
    assert PROBED == (
        "C_Secrets",
        "canaccessvalue",
        "C_SpellBook.GetNumSpellBookSkillLines",
        "C_SpellBook.GetSpellBookSkillLineInfo",
        "C_SpellBook.GetSpellBookItemInfo",
        "C_SpellBook.GetSpellBookItemName",
        "issecretvalue",
        "GetActionInfo",
        "GetMacroInfo",
        "issecretvalue",
        "GetCurrentBindingSet",
        "GetNumBindings",
        "GetBinding",
        "GetBindingKey",
        "issecretvalue",
    ), PROBED
    assert len(COMBAT_REASONS) == 3, COMBAT_REASONS
    assert len(LEFT_OUT_REASONS) == 4, LEFT_OUT_REASONS
    # The addon writes schema 4 since M15-01; schema 4 keeps these sections.
    assert re.search(r"^ns\.SCHEMA = 4$", (ADDON / "Core.lua").read_text(), re.M)


def test_every_probed_api_the_addon_writes_is_one_the_reader_takes() -> None:
    for api in PROBED:
        labaddon.ApiProbe.model_validate(_probe(api))


# ─── schema 1 and 2 are read exactly as before ───────────────────────────────


@pytest.mark.parametrize("name", REAL_CHARACTERS)
def test_real_capture_reads_as_schema_1_with_no_schema_3_line(name: str) -> None:
    char = labaddon.parse_char((FIXTURES / name).read_bytes())
    assert type(char) is labaddon.CharDBV1
    text = _text(char)
    for words in ("Spellbook", "Action bars", "Key bindings", "left out as the active class"):
        assert words not in text
    if char.talents is not None and char.talents.legacy is not None:
        assert type(char.talents.legacy) in (labaddon.LegacyTalents, labaddon.AbsentSection)


@pytest.mark.parametrize("schema", [1, 2])
def test_constructed_new_section_in_an_older_schema_is_an_unknown_key(schema: int) -> None:
    """A schema-1 or schema-2 file is not loosened: a new section's key is
    an unknown key there, refused when it holds text, kept when it does not."""
    raw = copy.deepcopy(_BASE)
    raw["schema"] = schema
    raw["spellbook"] = _spellbook()
    _refused(raw, "the unknown key spellbook", "holds text")
    raw["spellbook"] = {"count": 3}
    char = labaddon.load_char(raw)
    assert labaddon.unknown_keys(char) == ["spellbook"]
    raw = copy.deepcopy(_BASE)
    raw["schema"] = schema
    raw["talents"]["legacy"]["left_out_config_absent"] = LEFT_OUT_REASONS[0]
    _refused(raw, "left_out_config_absent", "holds text")


# ─── schema 3 ────────────────────────────────────────────────────────────────


def test_constructed_schema_3_reads_every_new_section() -> None:
    char = _v3(_base_v3())
    assert isinstance(char.spellbook, labaddon.Spellbook)
    assert [i.spell_name for i in char.spellbook.list_] == [
        "Auto Attack",
        "Seal of Righteousness",
        "Seal of Command",
    ]
    assert char.spellbook.list_[1].subtext == "Rank 1"
    assert isinstance(char.actionbars, labaddon.ActionBars)
    assert [s.id for s in char.actionbars.slots] == [6603, 121, 6948, None, 20154]
    assert char.spellbook.item_types == {
        "None": 0,
        "Spell": 1,
        "FutureSpell": 2,
        "PetAction": 3,
        "Flyout": 4,
    }
    assert isinstance(char.bindings, labaddon.Bindings)
    assert char.bindings.list_[1].keys == ["W", "UP"]
    assert char.talents is not None
    legacy = char.talents.legacy
    assert isinstance(legacy, labaddon.LegacyTalentsV3)
    assert legacy.left_out_config == 640511
    assert labaddon.unknown_keys(char) == []


def test_constructed_schema_3_json_reads_back_the_same() -> None:
    char = _v3(_base_v3())
    again = labaddon.CharDBV3.model_validate_json(char.model_dump_json())
    assert again == char
    dumped = json.loads(char.model_dump_json())
    assert list(dumped)[-3:] == list(NEW_KEYS)
    assert dumped["spellbook"]["list"][0]["spell_name"] == "Auto Attack"


def test_constructed_schema_3_account_reads() -> None:
    assert type(labaddon.load_account({"schema": 3})) is labaddon.AccountDBV3


def test_constructed_schema_3_describes_the_new_sections() -> None:
    lines = labaddon.describe(_v3(_base_v3()))
    text = "\n".join(lines)
    assert (
        "Spellbook: 3 items in 2 skill lines, 1 passive, 3 with a name, 1 with a subtext "
        "(ids, names and subtexts in --json); read out of combat"
    ) in lines
    assert "  by type: 1 FutureSpell, 2 Spell; future (not yet learned) spells are items too" in (
        lines
    )
    assert "  skill line 1: 2 items, slots 1 to 2" in lines
    assert "  skill line 2: 1 item, slots 3 to 3 (spec 1486)" in lines
    assert (
        "Action bars: 5 of slots 1 to 180 filled (1 equipmentset, 1 item, 2 macro, 1 spell; "
        "slots and ids in --json); read out of combat"
    ) in lines
    assert (
        "  macro slots: 1 with no subtype recorded (the id is the macro index [verify]), 1 with "
        "one (the id is the spell or item id the macro shows now [verify]); a macro's name and "
        "body are never recorded"
    ) in lines
    assert (
        "Key bindings: 3 commands with a key, of 412 listed (commands and keys in --json); "
        "binding set 2 (the character's own bindings); read out of combat"
    ) in lines
    assert (
        "  left out: 1 command of those listed not a plain upper-case name (text after the "
        "name, as in MACRO x, or other characters; never asked about), 0 keys not printable ASCII"
    ) in lines
    assert (
        "  a key bound straight to a spell, macro, item or click may not be listed by GetBinding "
        "at all [verify]"
    ) in lines
    assert (
        "  GetBindingKey: present, 411 calls, 0 raised an error, 3 answered, 408 answered "
        "nothing, 1 value dropped as secret or untestable (see issecretvalue's errors)"
    ) in lines
    assert "  issecretvalue: missing, not called" in lines
    assert "  C_Secrets: present (looked up only, never called)" in lines
    assert "  canaccessvalue: missing (looked up only, never called)" in lines
    assert "  left out as the active class config at this gather: config 640511" in lines
    # Every line printable ASCII and no longer than the longest line before (M11-34).
    assert all(re.fullmatch(r"[\x20-\x7e]*", line) for line in lines)
    assert max(len(line) for line in lines) <= 265, max(lines, key=len)
    assert "Seal of" not in text  # names stay in --json


@pytest.mark.parametrize("reason", LEFT_OUT_REASONS)
def test_constructed_left_out_reason_reads(reason: str) -> None:
    raw = _base_v3()
    del raw["talents"]["legacy"]["left_out_config"]
    raw["talents"]["legacy"]["left_out_config_absent"] = reason
    char = _v3(raw)
    assert f"  no config left out as the active class config ({reason})" in labaddon.describe(char)


def test_constructed_left_out_value_and_reason_together_are_refused() -> None:
    raw = _base_v3()
    raw["talents"]["legacy"]["left_out_config_absent"] = LEFT_OUT_REASONS[0]
    _refused(raw, "both left_out_config and left_out_config_absent are set")


def test_constructed_left_out_missing_is_said() -> None:
    raw = _base_v3()
    del raw["talents"]["legacy"]["left_out_config"]
    assert "  the config left out as the active class config is not in the file" in (
        labaddon.describe(_v3(raw))
    )


@pytest.mark.parametrize("reason", COMBAT_REASONS)
@pytest.mark.parametrize("key", NEW_KEYS)
def test_constructed_combat_reason_reads_as_absent(key: str, reason: str) -> None:
    raw = _base_v3()
    raw[key] = {"absent": reason, "events_unregistered": {}}
    char = _v3(raw)
    record = getattr(char, key)
    assert isinstance(record, labaddon.AbsentProbedSection)
    assert record.probes is None
    label = {"spellbook": "Spellbook", "actionbars": "Action bars", "bindings": "Key bindings"}
    # Since round 2 the addon writes a combat reason only when it gathered
    # nothing for the section in that session; the line says so.
    assert (
        f"{label[key]}: the addon was in combat (or could not tell) at its last gather of this "
        "section in the session that wrote this file, and had gathered nothing for it earlier "
        f"in that session: absent ({reason}); all its change events registered"
    ) in labaddon.describe(char)


def test_constructed_other_absent_reason_has_no_combat_note() -> None:
    raw = _base_v3()
    raw["bindings"] = {"absent": "GetNumBindings gave no number", "events_unregistered": {}}
    assert (
        "Key bindings: absent (GetNumBindings gave no number); all its change events registered"
    ) in labaddon.describe(_v3(raw))


def test_constructed_absent_for_a_missing_function_shows_its_probes() -> None:
    raw = _base_v3()
    raw["actionbars"] = {
        "absent": "GetActionInfo missing",
        "probes": [_missing("GetActionInfo"), _probe("GetMacroInfo"), _missing("issecretvalue")],
        "events_unregistered": {},
    }
    lines = labaddon.describe(_v3(raw))
    at = lines.index(
        "Action bars: absent (GetActionInfo missing); all its change events registered"
    )
    assert lines[at + 1 : at + 4] == [
        "  GetActionInfo: missing, not called",
        "  GetMacroInfo: present, 0 calls, 0 raised an error, 0 answered, 0 answered nothing, "
        "0 values dropped as secret or untestable (see issecretvalue's errors)",
        "  issecretvalue: missing, not called",
    ]


def test_constructed_item_types_name_each_item() -> None:
    """`type` is named from the record's own `item_types` (the client's
    Enum.SpellBookItemType): a number the map lacks, or a record without the
    map, is said as such, never guessed."""
    raw = _base_v3()
    raw["spellbook"]["list"][0]["type"] = 9
    del raw["spellbook"]["list"][1]["type"]
    assert (
        "  by type: 1 FutureSpell, 1 type 9 (not in item_types), 1 type not kept; future (not "
        "yet learned) spells are items too"
    ) in labaddon.describe(_v3(raw))
    raw = _base_v3()
    del raw["spellbook"]["item_types"]
    assert (
        "  by type: 2 type 1 (not in item_types), 1 type 2 (not in item_types) "
        "(Enum.SpellBookItemType is not in the record); future (not yet learned) spells are "
        "items too"
    ) in labaddon.describe(_v3(raw))


@pytest.mark.parametrize(
    ("which", "words"),
    [
        (1, "1 (the account's bindings)"),
        (2, "2 (the character's own bindings)"),
        (
            3,
            "3 (the client's raw value, neither set: the account's is 1, the character's is 2)",
        ),
    ],
)
def test_constructed_binding_set_is_named_by_the_client_values(which: int, words: str) -> None:
    raw = _with(("bindings", "binding_set"), which)
    text = _text(_v3(raw))
    assert f"binding set {words}; read out of combat" in text


def test_constructed_binding_set_without_the_client_values() -> None:
    raw = _base_v3()
    del raw["bindings"]["account_bindings"]
    del raw["bindings"]["character_bindings"]
    assert (
        "binding set 2 (the client's raw value; which number is the account's and which the "
        "character's was not recorded); read out of combat"
    ) in _text(_v3(raw))


def test_constructed_skill_line_without_items_or_offset() -> None:
    raw = _base_v3()
    raw["spellbook"]["skill_lines"].append({"index": 3, "offset": 3, "count": 0})
    raw["spellbook"]["skill_lines"].append({"index": 4})
    lines = labaddon.describe(_v3(raw))
    assert "  skill line 3: 0 items (offset 3)" in lines
    assert "  skill line 4: item count not returned, offset not returned" in lines


def test_constructed_skip_keys_include_the_new_sections() -> None:
    raw = _base_v3()
    raw["skip"] = ["spellbook", "bindings", "notasection"]
    for key in ("spellbook", "bindings"):
        raw[key] = {"absent": labaddon.SWITCHED_OFF}
    assert labaddon.skip_known(_v3(raw)) == (["spellbook", "bindings"], ["notasection"])


# ─── hostile and boundary values (constructed) ───────────────────────────────


def _with(path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    raw = _base_v3()
    node: Any = raw
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    return raw


@pytest.mark.parametrize(
    ("path", "value", "fragment"),
    [
        (("spellbook", "list", 0, "spell_name"), "Auto" + chr(10) + "Attack", "spell_name"),
        (("spellbook", "list", 0, "spell_name"), "Auto |cffff0000Attack", "spell_name"),
        (("spellbook", "list", 0, "spell_name"), "Auto" + chr(0x202E) + "Attack", "spell_name"),
        (("spellbook", "list", 0, "spell_name"), "Auto" + chr(0x85) + "Attack", "spell_name"),
        (("spellbook", "list", 0, "spell_name"), "x" * 256, "spell_name"),
        (("spellbook", "list", 0, "spell_name"), "", "spell_name"),
        (("spellbook", "list", 1, "subtext"), "Rank" + chr(0x2028) + "1", "subtext"),
        (("spellbook", "list", 0, "spell_id"), "6603", "spell_id"),
        (("spellbook", "list", 0, "spell_id"), 2**53 + 1, "spell_id"),
        (("actionbars", "slots", 1, "type"), "macro 1", "type"),
        (("actionbars", "slots", 1, "id"), "My Macro", "id"),
        (("actionbars", "slots", 0, "sub_type"), "spell" + chr(9), "sub_type"),
        (("bindings", "list", 0, "command"), "MACRO My Macro", "command"),
        (("bindings", "list", 0, "command"), "actionbutton1", "command"),
        (("bindings", "list", 0, "keys", 0), "SHIFT 1", "keys"),
        (("bindings", "list", 0, "keys", 0), chr(0xE4), "keys"),
        (("bindings", "commands"), -1, "commands"),
        (("bindings", "probes", 0, "api"), "UnitName", "api"),
        (("bindings", "probes", 0, "calls"), True, "calls"),
        (("bindings", "probes", 0, "secret"), -1, "secret"),
        (("bindings", "account_bindings"), "1", "account_bindings"),
        (("spellbook", "item_types"), {"Future Spell": 2}, "not an enum member's name"),
        (("spellbook", "item_types"), {"Spell|r": 1}, "not an enum member's name"),
        (("spellbook", "item_types"), {"Spell": "1"}, "item_types"),
        (("spellbook", "item_types"), {"Spell": True}, "item_types"),
        (("talents", "legacy", "left_out_config"), "640511", "left_out_config"),
    ],
)
def test_constructed_hostile_value_is_refused(
    path: tuple[Any, ...], value: Any, fragment: str
) -> None:
    message = _refused(_with(path, value), fragment)
    if isinstance(value, str) and len(value) > 1:
        assert value not in message  # never echoed


def test_constructed_probe_counts_that_cannot_happen_are_refused() -> None:
    raw = _base_v3()
    raw["bindings"]["probes"][3] = _probe("GetBindingKey", 3, 2, 2)
    _refused(raw, "errors and answered add up to more than calls")
    raw = _base_v3()
    raw["actionbars"]["probes"][1] = {
        "api": "GetMacroInfo",
        "present": False,
        "calls": 1,
        "errors": 0,
        "answered": 0,
        "secret": 0,
    }
    _refused(raw, "calls are counted for a function that was not present")
    raw = _base_v3()
    raw["actionbars"]["probes"][1] = {**_missing("GetMacroInfo"), "secret": 1}
    _refused(raw, "values are counted for a function that was not present")
    raw = _base_v3()
    del raw["actionbars"]["probes"][0]["secret"]
    _refused(raw, "secret")


def test_constructed_spell_name_in_another_script_reads() -> None:
    """Boundary: the client's own text in a non-Latin locale is kept."""
    name = "".join(chr(c) for c in (0x41E, 0x433, 0x43D, 0x435, 0x43D, 0x43D, 0x44B, 0x439))
    char = _v3(_with(("spellbook", "list", 0, "spell_name"), name))
    assert isinstance(char.spellbook, labaddon.Spellbook)
    assert char.spellbook.list_[0].spell_name == name


def test_constructed_unknown_key_in_a_new_section_is_kept() -> None:
    raw = _base_v3()
    raw["bindings"]["later_count"] = 4
    char = _v3(raw)
    assert labaddon.unknown_keys(char) == ["bindings.later_count"]


# ─── round 3: what the addon guarantees, checked (constructed) ───────────────


def test_constructed_left_out_config_that_is_listed_is_refused() -> None:
    """The addon leaves the active config out of `configs`, so the id it
    records as left out is never a listed config's id."""
    raw = _base_v3()
    listed = raw["talents"]["legacy"]["configs"]
    assert listed, "the M11-23 record lists a Legacy candidate"
    raw["talents"]["legacy"]["left_out_config"] = listed[0]["id"]
    _refused(raw, "left_out_config is the id of a listed config")


@pytest.mark.parametrize("reason", COMBAT_REASONS)
def test_constructed_combat_reason_with_probes_is_refused(reason: str) -> None:
    """A blocked gather calls nothing, so a combat reason carries no probes."""
    raw = _base_v3()
    raw["actionbars"] = {"absent": reason, "probes": [_probe("GetActionInfo")]}
    _refused(raw, "a combat reason carries probes")


def test_constructed_binding_counts_that_cannot_happen_are_refused() -> None:
    """Every listed command and every left-out one was counted in
    `commands`: together they are at most `commands`."""
    raw = _with(("bindings", "commands"), 3)
    _refused(raw, "add up to more than the commands counted")
    raw = _with(("bindings", "commands"), 4)
    assert isinstance(_v3(raw).bindings, labaddon.Bindings)


@pytest.mark.parametrize("field", ["spell_name", "subtext"])
def test_constructed_spell_text_is_capped_in_bytes(field: str) -> None:
    """The addon's `#value <= 255` counts bytes: 255 bytes of UTF-8 read,
    256 are refused even when they are fewer than 255 characters."""
    two_bytes = chr(0xE9)
    at_cap = two_bytes * 127 + "x"
    assert len(at_cap.encode("utf-8")) == 255
    char = _v3(_with(("spellbook", "list", 1, field), at_cap))
    assert isinstance(char.spellbook, labaddon.Spellbook)
    over = two_bytes * 128
    assert len(over) < 255 and len(over.encode("utf-8")) == 256
    message = _refused(_with(("spellbook", "list", 1, field), over), field, "256 bytes")
    assert over not in message


@pytest.mark.parametrize("api", sorted(labaddon.LOOKED_UP))
def test_constructed_looked_up_probe_with_calls_is_refused(api: str) -> None:
    """`C_Secrets` and `canaccessvalue` are looked up, never called."""
    for counts in ({"calls": 1}, {"secret": 1}):
        raw = _base_v3()
        raw["bindings"]["probes"][-1] = {**_probe(api), **counts}
        _refused(raw, "is looked up only")
