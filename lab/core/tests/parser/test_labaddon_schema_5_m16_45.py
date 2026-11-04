"""`wowlab_core.labaddon` reads schema 5, the quieter addon format of M16-45.

Schema 5 is schema 4 with:
- `debug` (`true` after `/wowlab debug on`) and `announced` (the schema
  whose first pass last finished in a written session) at the top;
- `probes` on `spellbook`, `actionbars` and `bindings` only in a debug
  session, so a schema-5 record without them is the usual case;
- on `bindings`, `clicks_left_out` and kept `CLICK <frame>:<button>`
  commands, each with `action`, the action slot it presses (ADR-0026,
  amendment of 2026-10-22).

No owner capture at schema 5 exists yet (the runbook is
`docs/handoffs/M16-45.md`). Until it lands, every schema-5 record here is
`constructed` in its test id (L8): it starts from the owner's real schema-4
capture (1.60.1.70450, Sangraal-Sett, `macos-70450/`) with `schema` set to 5
and the schema-5 keys added or removed in the shape `Actions.lua` and
`Core.lua` write. The CLICK pattern and bounds are read from `Actions.lua`,
so the reader and the addon cannot drift. The schema 1 to 4 captures are
read unchanged elsewhere (their own test files).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import keymap, labaddon, luadata

pytestmark = pytest.mark.parser

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ACTIONS_LUA = ROOT / "lab" / "addon" / "WowLab" / "Actions.lua"
SCHEMA_4 = "macos-70450/forever/WTF/Account/90000001#6/1/Sangraal-Sett/SavedVariables/WowLab.lua"
# A bar addon's button name, in the shape Bartender4 generates (from memory;
# the owner's capture settles real names).
CLICK = "CLICK BT4Button13:LeftButton"


def _raw() -> dict[str, Any]:
    values = luadata.parse((FIXTURES / SCHEMA_4).read_bytes()).to_python()
    record = values[labaddon.CHAR_VARIABLE]
    assert isinstance(record, dict)
    return record


def _schema_5(*, debug: bool = False) -> dict[str, Any]:
    """The real schema-4 record as the schema-5 addon would write it:
    `probes` only with `debug`, `clicks_left_out` on bindings, and the two
    top-level keys."""
    raw = copy.deepcopy(_raw())
    raw["schema"] = 5
    for key in ("spellbook", "actionbars", "bindings"):
        if not debug:
            raw[key].pop("probes", None)
    raw["bindings"]["clicks_left_out"] = 0
    raw["announced"] = 5
    if debug:
        raw["debug"] = True
    return raw


def _with_click(raw: dict[str, Any], command: str = CLICK, action: object = 13) -> None:
    raw["bindings"]["list"].append({"command": command, "action": action, "keys": ["SHIFT-1"]})
    raw["bindings"]["commands"] += 1


def test_constructed_schema_5_without_probes_is_read() -> None:
    record = labaddon.load_char(_schema_5())
    assert isinstance(record, labaddon.CharDBV5)
    assert isinstance(record, labaddon.CharDBV4)  # every earlier reader still applies
    assert record.announced == 5 and record.debug is None
    for section in (record.spellbook, record.actionbars, record.bindings):
        assert section is not None and not isinstance(section, labaddon.AbsentProbedSection)
        assert section.probes is None
    assert isinstance(record.bindings, labaddon.BindingsV5)
    assert record.bindings.clicks_left_out == 0
    assert labaddon.unknown_keys(record) == []


def test_constructed_schema_5_debug_session_keeps_probes() -> None:
    record = labaddon.load_char(_schema_5(debug=True))
    assert isinstance(record, labaddon.CharDBV5)
    assert record.debug is True
    assert record.bindings is not None and record.bindings.probes


def test_constructed_schema_5_text_says_nothing_of_missing_probes() -> None:
    text = "\n".join(labaddon.describe(labaddon.load_char(_schema_5())))
    assert "probes" not in text
    assert "click bindings: 0 addon action buttons kept" in text
    assert "0 others left out (counted above)" in text


def test_constructed_schema_5_click_binding_is_read_with_its_action_slot() -> None:
    raw = _schema_5()
    _with_click(raw)
    raw["bindings"]["commands_left_out"] += 1
    raw["bindings"]["clicks_left_out"] = 1
    record = labaddon.load_char(raw)
    assert isinstance(record, labaddon.CharDBV5)
    binds = record.bindings
    assert isinstance(binds, labaddon.BindingsV5)
    kept = [b for b in binds.list_ if b.action is not None]
    assert [(b.command, b.action, b.keys) for b in kept] == [(CLICK, 13, ["SHIFT-1"])]
    text = "\n".join(labaddon.describe(record))
    assert "click bindings: 1 addon action button kept with the action slot each presses" in text
    assert "1 other left out" in text
    dumped = json.loads(record.model_dump_json(by_alias=True))
    assert {"command": CLICK, "action": 13, "keys": ["SHIFT-1"]} in dumped["bindings"]["list"]
    assert labaddon.load_char(dumped) == record


def test_constructed_schema_5_keymap_does_not_break_on_a_click_binding() -> None:
    """The keybind views name CLICK binds in M16-46; until then a record
    holding one, with no probes, still joins."""
    raw = _schema_5()
    _with_click(raw)
    record = labaddon.load_char(raw)
    joined = keymap.join_record(account=None, character=None, record=record)
    assert joined is not None
    assert keymap.dropped(None, frozenset({"GetBinding"})) == []


@pytest.mark.parametrize(
    "command",
    [
        "CLICK BT4Button13",  # no button part
        "CLICK 4Button:LeftButton",  # frame part starts with a digit
        "CLICK BT4Button13:Left_Button",  # "_" in the button part
        "CLICK BT4Button13:LeftButton ",  # trailing space
        "CLICK " + "F" * 65 + ":LeftButton",  # frame part over 64 bytes
        "CLICK BT4Button13:" + "B" * 33,  # button part over 32 bytes
        "CLICK Bé:LeftButton",  # not ASCII
        "click BT4Button13:LeftButton",  # not the client's word
    ],
    ids=lambda c: c[:40],
)
def test_constructed_click_command_outside_the_rule_is_refused(command: str) -> None:
    raw = _schema_5()
    _with_click(raw, command)
    with pytest.raises(labaddon.LabAddonError, match="bindings"):
        labaddon.load_char(raw)


def test_constructed_click_bounds_are_the_addons() -> None:
    """The longest frame and button parts the addon keeps are read."""
    raw = _schema_5()
    _with_click(raw, "CLICK " + "_" + "F" * 63 + ":" + "B" * 32)
    labaddon.load_char(raw)
    source = ACTIONS_LUA.read_text(encoding="utf-8")
    assert '"^CLICK ([%a_][%w_]*):(%a%w*)$"' in source
    assert "#frameName <= 64 and #button <= 32" in source
    assert re.search(r"local MAX_ACTION_SLOT = 2147483647\n", source)


@pytest.mark.parametrize(
    "action",
    [None, 0, -1, 2**31, 1.5, "13", True],
    ids=["missing", "zero", "negative", "too-large", "fraction", "text", "boolean"],
)
def test_constructed_click_without_a_whole_action_slot_is_refused(action: object) -> None:
    raw = _schema_5()
    _with_click(raw, action=action)
    if action is None:
        del raw["bindings"]["list"][-1]["action"]
    with pytest.raises(labaddon.LabAddonError, match="bindings"):
        labaddon.load_char(raw)


def test_constructed_action_slot_on_a_plain_command_is_refused() -> None:
    raw = _schema_5()
    raw["bindings"]["list"][0]["action"] = 1
    with pytest.raises(labaddon.LabAddonError, match="only on a CLICK command"):
        labaddon.load_char(raw)


def test_constructed_more_clicks_than_commands_left_out_is_refused() -> None:
    raw = _schema_5()
    raw["bindings"]["clicks_left_out"] = raw["bindings"]["commands_left_out"] + 1
    with pytest.raises(labaddon.LabAddonError, match="more CLICK commands left out"):
        labaddon.load_char(raw)


def test_constructed_schema_5_bindings_need_clicks_left_out() -> None:
    raw = _schema_5()
    del raw["bindings"]["clicks_left_out"]
    with pytest.raises(labaddon.LabAddonError, match="clicks_left_out"):
        labaddon.load_char(raw)


def test_constructed_click_command_in_a_schema_4_file_is_refused() -> None:
    """Schema 4 is read exactly as before: no CLICK command, no action."""
    raw = copy.deepcopy(_raw())
    _with_click(raw)
    with pytest.raises(labaddon.LabAddonError, match="schema-4"):
        labaddon.load_char(raw)


@pytest.mark.parametrize("value", [0, 10000, "5", True, 1.5], ids=str)
def test_constructed_announced_that_is_not_a_schema_number_is_refused(value: object) -> None:
    raw = _schema_5()
    raw["announced"] = value
    with pytest.raises(labaddon.LabAddonError, match="announced"):
        labaddon.load_char(raw)


@pytest.mark.parametrize("value", ["true", 1], ids=str)
def test_constructed_debug_that_is_not_a_boolean_is_refused(value: object) -> None:
    raw = _schema_5()
    raw["debug"] = value
    with pytest.raises(labaddon.LabAddonError, match="debug"):
        labaddon.load_char(raw)


def test_constructed_get_attribute_probe_is_read_in_a_debug_session() -> None:
    raw = _schema_5(debug=True)
    raw["bindings"]["probes"].insert(
        -2,
        {
            "api": "GetAttribute",
            "present": True,
            "calls": 2,
            "errors": 0,
            "answered": 2,
            "secret": 0,
        },
    )
    record = labaddon.load_char(raw)
    assert record.bindings is not None and record.bindings.probes is not None
    assert "GetAttribute" in [p.api for p in record.bindings.probes]


def test_constructed_account_schema_5() -> None:
    record = labaddon.load_account({"schema": 5})
    assert isinstance(record, labaddon.AccountDBV5)
