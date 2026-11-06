"""`wowlab_core.labaddon` reads schema 5, the quieter addon format of M16-45.

Schema 5 is schema 4 with:
- `debug` (`true` after `/wowlab debug on`) and `announced` (the schema
  whose first pass last finished in a written session) at the top;
- `probes` on `spellbook`, `actionbars` and `bindings` only in a debug
  session, so a schema-5 record without them is the usual case;
- on `bindings`, `clicks_left_out` and kept `CLICK <frame>:<button>`
  commands, each with `action`, the button's `action` attribute when the
  bindings were gathered (ADR-0026, amendment of 2026-10-22);
- (round 1) `dropped` on each Actions section, always written.

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


def _schema_5(*, debug: bool = False, dropped: int | None = 0) -> dict[str, Any]:
    """The real schema-4 record as the schema-5 addon would write it:
    `probes` only with `debug`, `dropped` on each Actions section (always,
    from round 1; `None` leaves it out, which the addon never does),
    `clicks_left_out` on bindings, and the two top-level keys."""
    raw = copy.deepcopy(_raw())
    raw["schema"] = 5
    for key in ("spellbook", "actionbars", "bindings"):
        if not debug:
            raw[key].pop("probes", None)
        if dropped is not None:
            raw[key]["dropped"] = dropped
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
    assert record.bindings.dropped == 0
    assert labaddon.unknown_keys(record) == []


def test_constructed_schema_5_debug_session_keeps_probes() -> None:
    record = labaddon.load_char(_schema_5(debug=True))
    assert isinstance(record, labaddon.CharDBV5)
    assert record.debug is True
    assert record.bindings is not None and record.bindings.probes


def test_constructed_schema_5_text_says_nothing_of_missing_probes() -> None:
    text = "\n".join(labaddon.describe(labaddon.load_char(_schema_5())))
    assert "probes" not in text
    assert "click bindings: 0 action buttons kept, each with the button's action slot when " in text
    assert "values dropped (secret or untestable, or a call that failed): 0" in text
    assert labaddon.DROPPED_NOT_RECORDED not in text


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
    assert "click bindings: 1 action button kept" in text
    assert "(for a paging bar, the slot of the page shown then)" in text
    # Round 2 (domain 3): the rule's other failures (shape, length, not a
    # frame, a secret or invalid slot) are counted too, so the line does not
    # say why.
    assert (
        "; 1 of them click bindings the action-button rule left out (their names are not kept)"
        in text
    )
    assert "looked at" not in text
    assert labaddon.kept_clicks(binds) == 1
    assert labaddon.unseen_commands(binds) == binds.commands_left_out + 1
    assert CLICK not in [b.command for b in labaddon.seen_bindings(binds)]
    dumped = json.loads(record.model_dump_json(by_alias=True))
    assert {"command": CLICK, "action": 13, "keys": ["SHIFT-1"]} in dumped["bindings"]["list"]
    assert labaddon.load_char(dumped) == record


def test_constructed_schema_5_keymap_counts_a_kept_click_as_unseen() -> None:
    """The keybind views place CLICK binds in M16-46; until then a kept one
    is counted with the left-out commands and is not a row (ruling of
    2026-11-05)."""
    raw = _schema_5()
    _with_click(raw)
    record = labaddon.load_char(raw)
    assert isinstance(record, labaddon.CharDBV5) and record.bindings is not None
    assert isinstance(record.bindings, labaddon.BindingsV5)
    joined = keymap.join_record(account=None, character=None, record=record)
    assert CLICK not in [row.command for row in joined.keys]
    assert joined.commands_left_out == record.bindings.commands_left_out + 1
    assert not any(labaddon.DROPPED_NOT_RECORDED in n for n in joined.notes)


@pytest.mark.parametrize("section", ["spellbook", "actionbars", "bindings"])
def test_constructed_schema_5_dropped_values_make_the_section_incomplete(section: str) -> None:
    """`dropped` (round 1) takes the place of the probes' counts outside a
    debug session: a count above 0 is "incomplete", as the probes were."""
    raw = _schema_5()
    raw[section]["dropped"] = 3
    record = labaddon.load_char(raw)
    found = getattr(record, section)
    words = labaddon.dropped_words(found)
    assert words == [
        "3 value(s) were dropped as secret or untestable, or lost to a call that raised an error"
    ]
    assert not labaddon.dropped_unknown(found)
    if section == "actionbars":
        joined = keymap.join_record(account=None, character=None, record=record)
        assert any("The action-bar record is incomplete (3 value(s)" in n for n in joined.notes)
    if section == "bindings":
        joined = keymap.join_record(account=None, character=None, record=record)
        assert (
            joined.keys_incomplete
            == "the bindings record is incomplete, so a key may be one it dropped"
        )


def test_constructed_schema_5_without_dropped_says_it_is_not_recorded() -> None:
    """A schema-5 record with neither `probes` nor `dropped` (not written by
    this addon; defensive): unknown stays unknown."""
    record = labaddon.load_char(_schema_5(dropped=None))
    assert isinstance(record, labaddon.CharDBV5) and record.actionbars is not None
    assert not isinstance(record.actionbars, labaddon.AbsentProbedSection)
    assert labaddon.dropped_unknown(record.actionbars)
    assert labaddon.dropped_words(record.actionbars) == []
    joined = keymap.join_record(account=None, character=None, record=record)
    assert f"In the action-bar record, {labaddon.DROPPED_NOT_RECORDED}." in joined.notes
    assert f"In the bindings record, {labaddon.DROPPED_NOT_RECORDED}." in joined.notes
    text = "\n".join(labaddon.describe(record))
    assert text.count(labaddon.DROPPED_NOT_RECORDED) == 3


def test_constructed_schema_5_debug_session_uses_the_probes() -> None:
    raw = _schema_5(debug=True, dropped=5)
    record = labaddon.load_char(raw)
    assert record.bindings is not None and not isinstance(
        record.bindings, labaddon.AbsentProbedSection
    )
    # The probes hold the detail; `dropped` is their sum (0 in the capture).
    assert labaddon.dropped_words(record.bindings) == []


@pytest.mark.parametrize("value", [-1, 1.5, "3", True], ids=str)
def test_constructed_dropped_that_is_not_a_count_is_refused(value: object) -> None:
    raw = _schema_5()
    raw["bindings"]["dropped"] = value
    with pytest.raises(labaddon.LabAddonError, match="dropped"):
        labaddon.load_char(raw)


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


def test_constructed_get_attribute_errors_are_not_dropped_values() -> None:
    """Round 2 (domain 4): a CLICK command whose frame's GetAttribute failed
    is counted in clicks_left_out, so the debug probes' words leave the
    GetAttribute probe out of "dropped", as the addon's `droppedCount`
    does."""
    raw = _schema_5(debug=True)
    raw["bindings"]["probes"].insert(
        -2,
        {
            "api": "GetAttribute",
            "present": True,
            "calls": 4,
            "errors": 2,
            "answered": 1,
            "secret": 1,
        },
    )
    record = labaddon.load_char(raw)
    assert record.bindings is not None
    assert not isinstance(record.bindings, labaddon.AbsentProbedSection)
    assert labaddon.dropped_words(record.bindings) == []


def _note(commands: int, keys: int, kept: int) -> str | None:
    from wowlab_core import cli

    return cli._binds_left_out_note(commands, keys, kept)


def test_constructed_char_binds_note_says_kept_clicks_apart() -> None:
    """Round 2 (domain 1, code 2): a kept click binding is in the record;
    the sentence says so apart from the left-out counts, and leaves out a
    clause whose count is 0. Without a kept click it is the M13-77 sentence,
    unchanged."""
    assert _note(1, 0, 0) == (
        "Left out of the bindings record, counted with no list: 1 command not named in plain "
        "upper case (a key bound straight to a spell, macro, item or click may be one), and "
        "0 keys not printable ASCII."
    )
    assert _note(3, 2, 1) == (
        "Left out of the bindings record, counted with no list: 2 commands not named in plain "
        "upper case (a key bound straight to a spell, macro, item or click may be one), and "
        "2 keys not printable ASCII. 1 click binding to an action button is in the record, "
        "but this view does not place it yet."
    )
    assert _note(2, 0, 2) == (
        "2 click bindings to an action button are in the record, but this view does not place "
        "them yet."
    )
    assert _note(3, 0, 1) == (
        "Left out of the bindings record, counted with no list: 2 commands not named in plain "
        "upper case (a key bound straight to a spell, macro, item or click may be one). "
        "1 click binding to an action button is in the record, but this view does not place "
        "it yet."
    )
    for commands, keys, kept in [(3, 2, 1), (2, 0, 2), (3, 0, 1)]:
        note = _note(commands, keys, kept)
        assert note is not None and "not read" not in note and " 0 " not in note


def test_constructed_coach_unseen_says_kept_clicks_apart() -> None:
    from wowlab_core import bindscoach

    assert bindscoach.unseen_words(3, 2) == bindscoach.unseen_words(3, 2, 0)
    words = bindscoach.unseen_words(2, 0, 1)
    assert words.startswith(
        "Left out of the bindings record, counted with no list: 1 binding command it did not "
        "read, whether or not a key is bound to it (not named in plain upper case). 1 click "
        "binding to an action button is in the record, but the coach does not place it yet."
    )
    only_kept = bindscoach.unseen_words(1, 0, 1)
    assert only_kept.startswith(
        "1 click binding to an action button is in the record, but the coach does not place "
        "it yet. Any of these may reach a spell"
    )
    assert "did not read" not in only_kept and "0 keys" not in only_kept


def test_constructed_json_counts_include_kept_clicks() -> None:
    """The ruling's sum stays in `--json`: `commands_left_out` (char binds)
    and `commands_unseen` (binds coach) include kept click bindings."""
    raw = _schema_5()
    _with_click(raw)
    record = labaddon.load_char(raw)
    assert isinstance(record, labaddon.CharDBV5) and record.bindings is not None
    assert isinstance(record.bindings, labaddon.BindingsV5)
    joined = keymap.join_record(account=None, character=None, record=record)
    assert joined.commands_left_out == record.bindings.commands_left_out + 1


def test_constructed_account_schema_5() -> None:
    record = labaddon.load_account({"schema": 5})
    assert isinstance(record, labaddon.AccountDBV5)
