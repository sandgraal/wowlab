"""`wowlab_core.labaddon`, M11-04 fix round 1, on constructed input (L8:
boundary and hostile cases; every test id says `constructed`).

Each case starts from the real first-character capture (M11-03, index row in
`fixtures/README.md`) as `luadata.to_python` gives it and changes one thing
the reviews named: aliased field names (S1), integers past a double's exact
range (S2), format characters in text (S3), the bounded "assigns no" message
(S4), an empty export (C1), a Legacy pool listed on several trees (C2),
integer keys in unknown tables (C3), required fields (C5), the final M11-22
keys (C6), and the domain wordings.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import labaddon, luadata

pytestmark = pytest.mark.parser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FIRST = "macos/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua"

RLO = chr(0x202E)
LRM = chr(0x200E)
LSEP = chr(0x2028)
PSEP = chr(0x2029)
ZWSP = chr(0x200B)
BOM = chr(0xFEFF)
NEL = chr(0x0085)
E_ACUTE = chr(0x00E9)
U_UML = chr(0x00FC)

_BASE: Any = luadata.parse((FIXTURES / FIRST).read_bytes()).to_python()["WowLabCharDB"]


def _base() -> dict[str, Any]:
    return copy.deepcopy(_BASE)


def _at(value: Any, path: str) -> Any:
    for part in path.split("."):
        value = value[int(part)] if part.isdigit() else value[part]
    return value


def _set(root: dict[str, Any], path: str, new: Any) -> None:
    parent_path, _, last = path.rpartition(".")
    parent = _at(root, parent_path) if parent_path else root
    parent[int(last) if last.isdigit() else last] = new


def _drop(root: dict[str, Any], path: str) -> None:
    parent_path, _, last = path.rpartition(".")
    parent = _at(root, parent_path) if parent_path else root
    del parent[last]


def _load(raw: dict[str, Any]) -> labaddon.CharDBV1:
    return labaddon.load_char(raw)


def _text(char: labaddon.CharDBV1) -> str:
    return "\n".join(labaddon.describe(char))


def _refused(raw: object, *fragments: str) -> str:
    with pytest.raises(labaddon.LabAddonError) as info:
        labaddon.load_char(raw)
    message = str(info.value)
    for fragment in fragments:
        assert fragment in message, message
    return message


# ─── S1: the Python-side names of aliased fields are unknown keys ────────────

ALIAS_PAIRS = [("", "schema_"), ("currencies", "list_"), ("talents", "class_")]


def _put(raw: dict[str, Any], parent: str, key: str, value: Any) -> None:
    (_at(raw, parent) if parent else raw)[key] = value


@pytest.mark.parametrize(("parent", "python_name"), ALIAS_PAIRS)
def test_constructed_python_name_beside_the_alias_with_text_is_refused(
    parent: str, python_name: str
) -> None:
    raw = _base()
    _put(raw, parent, python_name, "CANARY free text")
    message = _refused(raw, f"the unknown key {python_name} holds text")
    assert "CANARY" not in message


@pytest.mark.parametrize(("parent", "python_name"), ALIAS_PAIRS)
def test_constructed_python_name_beside_the_alias_with_a_non_plain_key_is_refused(
    parent: str, python_name: str
) -> None:
    raw = _base()
    _put(raw, parent, python_name, {"has space CANARY": 1})
    message = _refused(raw, f"the unknown key {python_name} holds a key of")
    assert "CANARY" not in message


@pytest.mark.parametrize(("parent", "python_name"), ALIAS_PAIRS)
def test_constructed_python_name_with_a_number_is_only_an_unknown_key(
    parent: str, python_name: str
) -> None:
    raw = _base()
    _put(raw, parent, python_name, 7)
    char = _load(raw)
    path = f"{parent}.{python_name}" if parent else python_name
    assert labaddon.unknown_keys(char) == [path]
    assert char.schema_ == 1  # the addon's `schema`, not the unknown key
    assert labaddon.CharDBV1.model_validate_json(char.model_dump_json()) == char


def test_constructed_python_name_alone_does_not_stand_in_for_the_field() -> None:
    raw = _base()
    raw["currencies"]["list_"] = raw["currencies"].pop("list")
    _refused(raw, "WowLabCharDB.currencies.list")


def test_constructed_account_python_name_is_checked() -> None:
    with pytest.raises(labaddon.LabAddonError, match="unknown key schema_ holds text"):
        labaddon.load_account({"schema": 1, "schema_": "CANARY"})
    with pytest.raises(labaddon.LabAddonError, match="unknown key schema_ holds a key of"):
        labaddon.load_account({"schema": 1, "schema_": {"not plain": 1}})
    account = labaddon.load_account({"schema": 1, "schema_": 2})
    assert labaddon.unknown_keys(account) == ["schema_"]


# ─── S2: integers bounded to a Lua double's exact range ──────────────────────

HUGE = 16**4000


@pytest.mark.parametrize(
    "path", ["probe.loads", "gear.slots.0.slot", "gear.slots.0.item_level", "spec.id"]
)
def test_constructed_huge_int_in_a_field_is_refused_cleanly(path: str) -> None:
    raw = _base()
    _set(raw, path, HUGE)
    _refused(raw, "less than or equal to 9007199254740992")


def test_constructed_int_bound_is_inclusive() -> None:
    raw = _base()
    raw["spec"]["id"] = 2**53
    assert _load(raw)
    raw["spec"]["id"] = -(2**53) - 1
    _refused(raw, "WowLabCharDB.spec.id")


def test_constructed_huge_int_in_an_unknown_key_is_refused_cleanly() -> None:
    raw = _base()
    raw["future"] = [1, HUGE]
    _refused(raw, "the unknown key future[1] holds a number out of range")
    raw = _base()
    raw["future"] = {HUGE: 1}
    _refused(raw, "the unknown key future holds a number key out of range")


@pytest.mark.parametrize("schema", [HUGE, -1, 10000], ids=["huge", "negative", "10000"])
def test_constructed_schema_out_of_range_is_refused_before_formatting(schema: int) -> None:
    raw = _base()
    raw["schema"] = schema
    _refused(raw, "WowLabCharDB.schema is out of range")


def test_constructed_huge_hex_int_in_lua_text_is_refused_cleanly() -> None:
    data = (FIXTURES / FIRST).read_bytes()
    marker = b'["loads"] = 4,'
    assert data.count(marker) == 1
    huge = b'["loads"] = 0x' + b"F" * 4000 + b","
    with pytest.raises(labaddon.LabAddonError, match=r"probe\.loads"):
        labaddon.parse_char(data.replace(marker, huge))
    schema = data.replace(b'["schema"] = 1,', b'["schema"] = 0x' + b"F" * 4000 + b",")
    with pytest.raises(labaddon.LabAddonError, match="schema is out of range"):
        labaddon.parse_char(schema)


def test_constructed_infinite_float_is_refused() -> None:
    raw = _base()
    raw["gear"]["average"]["equipped"] = float("inf")
    _refused(raw, "WowLabCharDB.gear.average.equipped")


# ─── S3: no format or separator characters in reasons or item names ──────────


@pytest.mark.parametrize(
    "reason",
    [
        "bidi " + RLO + " CANARY",
        "mark " + LRM + " CANARY",
        "line" + LSEP + "CANARY",
        "para" + PSEP + "CANARY",
        "newline\nCANARY",
        "non-ascii " + E_ACUTE + " CANARY",
        "CANARY" + "x" * 1025,
    ],
)
def test_constructed_reason_is_printable_ascii_only(reason: str) -> None:
    # M11-27: such a reason is clipped to printable ASCII and flagged instead
    # of refusing the file (test_labaddon_char_show_followups_constructed.py).
    raw = _base()
    raw["customization"] = {"absent": reason}
    record = _load(raw)
    section = record.customization
    assert isinstance(section, labaddon.AbsentSection)
    assert section.absent_clipped is not None
    assert all(" " <= ch <= "~" for ch in section.absent)
    assert all(" " <= ch <= "~" for ch in _text(record).replace("\n", ""))


def test_constructed_reason_at_the_length_cap_reads() -> None:
    raw = _base()
    raw["customization"] = {"absent": "x" * 1024}
    assert isinstance(_load(raw).customization, labaddon.AbsentSection)


@pytest.mark.parametrize(
    "char", [RLO, ZWSP, LSEP, PSEP, NEL, BOM], ids=["RLO", "ZWSP", "LSEP", "PSEP", "NEL", "BOM"]
)
def test_constructed_item_name_refuses_format_and_separator_characters(char: str) -> None:
    raw = _base()
    raw["gear"]["slots"][0]["link"] = f"|cnIQ2:|Hitem:9749::|h[Blouse{char}CANARY]|h|r"
    assert "CANARY" not in _refused(raw, "WowLabCharDB.gear.slots[0].link")


def test_constructed_item_name_keeps_other_letters() -> None:
    raw = _base()
    raw["gear"]["slots"][0]["link"] = (
        "|cnIQ2:|Hitem:9749::|h[Bl" + U_UML + "se d'" + E_ACUTE + "t" + E_ACUTE + "]|h|r"
    )
    assert (
        "Bl" + U_UML + "se d'" + E_ACUTE + "t" + E_ACUTE + " (item 9749; full link in --json)"
        in _text(_load(raw))
    )


# ─── S4: the "assigns no" message is bounded ─────────────────────────────────


def test_constructed_missing_variable_message_is_bounded() -> None:
    names = [f"Name{i:02d}" + "L" * 100 for i in range(15)]
    data = b"".join(f"{name} = 1\n".encode() for name in names)
    with pytest.raises(labaddon.LabAddonError) as info:
        labaddon.parse_char(data)
    message = str(info.value)
    assert "it assigns 15 names:" in message
    assert "and 5 more" in message
    assert "Name09" in message and "Name10" not in message
    assert "L" * 65 not in message


# ─── C1: an empty export ─────────────────────────────────────────────────────


def test_constructed_empty_export_reads_as_no_export() -> None:
    raw = _base()
    raw["talents"]["class"]["export"] = ""
    lines = _text(_load(raw)).splitlines()
    assert "  export string: the client returned an empty string" in lines


# ─── C2: a Legacy pool listed on several trees ───────────────────────────────


def _legacy_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    trees = raw["talents"]["legacy"]["configs"][0]["trees"]
    return [t["currencies"][0] for t in trees if isinstance(t["currencies"], list)]


def _legacy_headline(raw: dict[str, Any]) -> str:
    talents = _load(raw).talents
    assert talents is not None and isinstance(talents.legacy, labaddon.LegacyTalents)
    return labaddon.legacy_headline(talents.legacy)


def test_constructed_legacy_pool_on_three_trees_with_quantity_1_counts_once() -> None:
    raw = _base()
    rows = _legacy_rows(raw)
    assert [r["id"] for r in rows] == [4225, 4225, 4225]
    for row in rows:
        row["quantity"] = 1
    assert _legacy_headline(raw) == "Legacy candidates: present, nothing spent, 1 point available"


def test_constructed_legacy_pool_trees_that_disagree_are_not_added_up() -> None:
    raw = _base()
    _legacy_rows(raw)[1]["quantity"] = 2
    assert _legacy_headline(raw) == (
        "Legacy candidates: present, nothing spent, points available not added up: currency "
        "4225 reports different values on different trees (see below)"
    )


# ─── C3: integer keys in unknown tables ──────────────────────────────────────


@pytest.mark.parametrize("key", [-1, "-1", 3, "3"])
def test_constructed_integer_keys_in_unknown_tables_round_trip(key: Any) -> None:
    raw = _base()
    raw["future"] = {key: 5}
    char = _load(raw)
    again = labaddon.CharDBV1.model_validate_json(char.model_dump_json())
    assert again.model_extra is not None and again.model_extra["future"] == {str(key): 5}


@pytest.mark.parametrize("key", ["-x", "--1", "-" + "1" * 17])
def test_constructed_non_integer_dash_keys_in_unknown_tables_are_refused(key: str) -> None:
    raw = _base()
    raw["future"] = {key: 5}
    _refused(raw, "the unknown key future holds a key of")


# ─── C5: fields the addon always writes are required ─────────────────────────


@pytest.mark.parametrize(
    "path", ["gear.first_slot", "gear.last_slot", "gear.average", "collections.toys.filter"]
)
def test_constructed_field_the_addon_always_writes_is_required(path: str) -> None:
    raw = _base()
    _drop(raw, path)
    _refused(raw, "Field required")


def test_constructed_customization_as_of_and_absent_config_found_by_are_required() -> None:
    raw = _base()
    raw["customization"] = {"recorded_load": 1, "choices": []}
    _refused(raw, "WowLabCharDB.customization.as_of: Field required")
    raw = _base()
    raw["talents"]["legacy"]["configs"].append({"id": 7, "absent": "reason"})
    _refused(raw, "found_by: Field required")


# ─── C6: the final M11-22 keys ───────────────────────────────────────────────


def test_constructed_currency_rows_and_headers() -> None:
    raw = _base()
    raw["currencies"].update({"rows": 5, "headers": 2})
    char = _load(raw)
    assert isinstance(char.currencies, labaddon.Currencies)
    assert char.currencies.headers == 2
    assert (
        "Currencies: none recorded (read through the currency panel: filter value 1 (the "
        "client's raw value; meaning not known), 0 collapsed headers; 5 rows listed, "
        "2 header rows, 3 rows with no id the addon could read)" in _text(char).splitlines()
    )
    assert labaddon.unknown_keys(char) == []


def test_constructed_empty_events_unregistered_reads_all_registered() -> None:
    raw = _base()
    raw["gear"]["events_unregistered"] = {}  # the client writes an empty list as {}
    raw["customization"]["events_unregistered"] = {}
    char = _load(raw)
    assert isinstance(char.gear, labaddon.Gear) and char.gear.events_unregistered == []
    lines = _text(char).splitlines()
    assert "  all its change events registered" in lines
    assert (
        "Customization: absent (no barber-shop visit recorded with the addon enabled); "
        "all its change events registered" in lines
    )


def test_constructed_switched_off_section_has_no_events_line() -> None:
    raw = _base()
    raw["gear"] = {"absent": labaddon.SWITCHED_OFF}
    assert "Gear: absent (switched off by the owner)" in _text(_load(raw)).splitlines()


M11_22_REASONS = [
    ("last_selected_config_absent", "C_ClassTalents.GetLastSelectedSavedConfigID missing"),
    ("last_selected_config_absent", "no spec id to ask with"),
    ("last_selected_config_absent", "C_ClassTalents.GetLastSelectedSavedConfigID raised an error"),
    ("last_selected_config_absent", "the client returned no last-selected loadout for this spec"),
    (
        "last_selected_config_absent",
        "C_ClassTalents.GetLastSelectedSavedConfigID returned no number",
    ),
    ("export_absent", "C_Traits.GenerateImportString missing"),
    ("export_absent", "C_Traits.GenerateImportString raised an error"),
    ("export_absent", "C_Traits.GenerateImportString returned an empty string"),
    ("export_absent", "C_Traits.GenerateImportString returned no string"),
]


@pytest.mark.parametrize(("key", "reason"), M11_22_REASONS)
def test_constructed_m11_22_reasons_read(key: str, reason: str) -> None:
    raw = _base()
    if key == "export_absent":
        del raw["talents"]["class"]["export"]
    raw["talents"]["class"][key] = reason
    assert f"absent ({reason})" in _text(_load(raw))


# ─── domain wordings ─────────────────────────────────────────────────────────


def _customization_line(recorded_load: int) -> str:
    raw = _base()
    raw["customization"] = {
        "as_of": "last barber-shop visit with the addon enabled",
        "recorded_load": recorded_load,
        "sex": 1,
        "choices": [],
    }
    lines = labaddon.describe(_load(raw))
    return next(line for line in lines if line.startswith("Customization:"))


def test_constructed_customization_timing_words() -> None:
    assert "enabled, in the session that saved this file (" in _customization_line(4)
    assert "enabled, 1 login or reload ago (" in _customization_line(3)
    assert "enabled, 2 logins or reloads ago (" in _customization_line(2)
    assert "sex 1 (the client's raw value)" in _customization_line(2)


def test_constructed_customization_loads_ago_is_unknown_when_the_probe_was_lost() -> None:
    raw = _base()
    raw["probe"] = {"loads": 5, "lost": True}
    raw["customization"] = {
        "as_of": "last barber-shop visit with the addon enabled",
        "recorded_load": 1,
        "choices": [],
    }
    char = _load(raw)
    assert labaddon.customization_loads_ago(char) is None
    assert "an unknown number of logins or reloads ago" in _text(char)


def test_constructed_slot_line_names_the_item_level_api() -> None:
    raw = _base()
    raw["gear"]["slots"][0]["item_level_api"] = "C_Item.GetDetailedItemLevelInfo"
    assert (
        "  slot 5: Simple Blouse of the Owl (item 9749; full link in --json), item level 15 "
        "(C_Item.GetDetailedItemLevelInfo)" in _text(_load(raw)).splitlines()
    )


def test_constructed_missing_section_line_says_why() -> None:
    char = _load({"schema": 1, "probe": {"loads": 1}})
    lines = _text(char).splitlines()
    for label in ("Gear", "Spec", "Class talents", "Currencies", "Professions"):
        assert (
            f"{label}: not in the file (the addon's logout write did not reach it in the "
            "session that saved this file)"
        ) in lines
