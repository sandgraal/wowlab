"""`wowlab_core.labaddon` on constructed input (M11-04; L8: boundary and
hostile cases, every test id says `constructed`).

Each case starts from the real first-character capture (M11-03, index row in
`fixtures/README.md`), read as `luadata.to_python` gives it, and changes one
thing the capture does not hold: an empty list the capture has filled, a key
it has that the client may leave nil, an absent record at a level the
capture never showed, the keys M11-22 adds, the skip list (M11-21), a
customization record, a schema this reader does not know, and text or types
a tampered file might carry. The shape of each change follows the addon's
own sources (`lab/addon/WowLab/*.lua`), not an invented format. A few cases
parse constructed Lua text to reach `parse_char` itself.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import labaddon, luadata

pytestmark = pytest.mark.parser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FIRST = "macos/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua"

_BASE: dict[str, Any] = luadata.parse((FIXTURES / FIRST).read_bytes()).to_python()["WowLabCharDB"]  # type: ignore[assignment]


def _base() -> dict[str, Any]:
    return copy.deepcopy(_BASE)


def _at(value: Any, path: str) -> Any:
    for part in path.split("."):
        value = value[int(part)] if part.isdigit() else value[part]
    return value


def _set(root: dict[str, Any], path: str, new: Any) -> None:
    parent_path, _, last = path.rpartition(".")
    parent = _at(root, parent_path) if parent_path else root
    if last.isdigit():
        parent[int(last)] = new
    else:
        parent[last] = new


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


# ─── schema ──────────────────────────────────────────────────────────────────


def test_constructed_schema_2_is_refused_with_a_clear_message() -> None:
    raw = _base()
    raw["schema"] = 2
    message = _refused(raw, "schema 2", "knows schema 1 only", "nothing was read")
    assert "another version of the lab-addon" in message


def test_constructed_schema_2_file_is_refused_from_lua_text() -> None:
    data = b'\r\nWowLabCharDB = {\r\n["schema"] = 2,\r\n["gear"] = {\r\n},\r\n}\r\n'
    with pytest.raises(labaddon.LabAddonError, match="schema 2"):
        labaddon.parse_char(data)


def test_constructed_account_schema_2_is_refused() -> None:
    data = b'\r\nWowLabDB = {\r\n["schema"] = 2,\r\n}\r\n'
    with pytest.raises(labaddon.LabAddonError, match="WowLabDB is schema 2"):
        labaddon.parse_account(data)


@pytest.mark.parametrize(
    ("schema", "fragment"),
    [(None, "no schema number"), ("1", "not a whole number"), (True, "not a whole number")],
)
def test_constructed_missing_or_mistyped_schema_is_refused(schema: Any, fragment: str) -> None:
    raw = _base()
    if schema is None:
        del raw["schema"]
    else:
        raw["schema"] = schema
    _refused(raw, fragment)


def test_constructed_variable_set_to_nil_is_refused() -> None:
    with pytest.raises(labaddon.LabAddonError, match="sets WowLabCharDB to nil"):
        labaddon.parse_char(b"WowLabCharDB = nil\n")


def test_constructed_variable_not_a_table_is_refused() -> None:
    _refused(5, "not a table")


# ─── an empty table read back as a dict where a list is expected ─────────────

# Lists the capture holds filled; each is emptied the way the client writes an
# empty table, which `to_python` reads as `{}`.
CONSTRUCTED_EMPTY = [
    "talents.legacy.configs",
    "talents.class.config.trees",
    "gear.slots",
    "talents.class.config.trees.0.nodes.0.entries",
    "talents.legacy.configs.0.found_by",
    "talents.legacy.skipped_types",
    "professions.list",
]


@pytest.mark.parametrize("path", CONSTRUCTED_EMPTY)
def test_constructed_empty_table_where_a_list_is_expected(path: str) -> None:
    raw = _base()
    _set(raw, path, {})
    char = _load(raw)
    assert _at(char.model_dump(mode="json"), path) == []
    assert _text(char)


def test_constructed_empty_customization_choices_and_skip() -> None:
    raw = _base()
    raw["customization"] = {
        "as_of": "last barber-shop visit with the addon enabled",
        "recorded_at": "open",
        "recorded_load": 4,
        "choices": {},
    }
    raw["skip"] = {}
    char = _load(raw)
    assert isinstance(char.customization, labaddon.Customization)
    assert char.customization.choices == []
    assert char.skip == []
    assert "choices: none recorded" in _text(char)


def test_constructed_non_empty_dict_where_a_list_is_expected_is_refused() -> None:
    raw = _base()
    raw["gear"]["slots"] = {"x": 1}
    _refused(raw, "WowLabCharDB.gear.slots")


# ─── a missing key means the client returned nil ─────────────────────────────

NIL_KEYS = [
    ("spec.id", "id not returned"),
    ("talents.class.config.trees.0.nodes.0.active_entry", None),
    ("gear.slots.0.item_level", "item level not returned"),
    ("currencies.filter", "filter value not returned"),
    ("collections.pets.default_filters", "default filters: not reported"),
    ("collections.toys.filter.collected_shown", "collected shown not reported"),
    ("collections.toys.filter.uncollected_shown", "uncollected shown not reported"),
    ("collections.toys.filter.unusable_shown", "unusable shown not reported"),
    ("talents.legacy.player_level", None),
    ("talents.class.export", "export string: not returned by the client"),
]


@pytest.mark.parametrize(("path", "words"), NIL_KEYS)
def test_constructed_missing_key_is_optional_never_absent(path: str, words: str | None) -> None:
    raw = _base()
    _drop(raw, path)
    char = _load(raw)
    field = _at(char.model_dump(mode="json"), path)
    assert field is None
    text = _text(char)
    if words is not None:
        assert words in text
    # A nil from the client is not an absent record with a reason.
    assert "absent (" not in text.replace("Customization: absent (", "").replace(
        "Appearances: absent (", ""
    )


def test_constructed_missing_player_level_drops_the_level_only() -> None:
    raw = _base()
    del raw["talents"]["legacy"]["player_level"]
    text = _text(_load(raw))
    assert "Legacy candidates: present, nothing spent, 0 points available\n" in text + "\n"


def test_constructed_probe_lost_is_shown() -> None:
    raw = _base()
    raw["probe"] = {"loads": 1, "lost": True}
    char = _load(raw)
    assert char.probe is not None and char.probe.lost is True
    assert (
        "Probe: loads 1 (each login or /reload with the addon enabled adds 1 to the value "
        "the file held); lost: WowLabCharDB loaded without a probe count, so the count "
        "restarted at 1 on this load"
    ) in _text(char).splitlines()


def test_constructed_section_missing_from_the_file_is_not_absent() -> None:
    raw = {"schema": 1, "probe": {"loads": 1}}
    char = _load(raw)
    text = _text(char)
    assert (
        "Gear: not in the file (the addon's logout write did not reach it in the session "
        "that saved this file)"
    ) in text.splitlines()
    assert "Class talents: not in the file" in text
    assert "Client: not in the file" in text
    assert "absent" not in text


# ─── absent with a reason at every level ─────────────────────────────────────


def test_constructed_whole_section_absent() -> None:
    raw = _base()
    raw["gear"] = {"absent": "GetInventoryItemLink missing"}
    raw["talents"]["legacy"] = {"absent": "C_Traits missing"}
    char = _load(raw)
    assert isinstance(char.gear, labaddon.AbsentSection)
    text = _text(char)
    assert "Gear: absent (GetInventoryItemLink missing)" in text
    assert "Legacy candidates: absent (C_Traits missing)" in text


def test_constructed_one_config_absent() -> None:
    raw = _base()
    raw["talents"]["class"]["config"] = {
        "id": 640511,
        "absent": "C_Traits.GetConfigInfo returned nothing",
    }
    raw["talents"]["legacy"]["configs"].append(
        {"id": 7, "absent": "C_Traits.GetConfigInfo returned nothing", "found_by": ["type:Generic"]}
    )
    char = _load(raw)
    text = _text(char)
    assert "Class talents: config 640511 absent (C_Traits.GetConfigInfo returned nothing)" in text
    assert "config 7 absent (C_Traits.GetConfigInfo returned nothing); found by type:Generic" in (
        text
    )
    talents = char.talents
    assert talents is not None and isinstance(talents.legacy, labaddon.LegacyTalents)
    assert isinstance(talents.legacy.configs[1], labaddon.AbsentLegacyConfig)


def test_constructed_one_currency_absent_and_one_present() -> None:
    raw = _base()
    raw["currencies"]["list"] = [
        {"id": 1, "absent": "C_CurrencyInfo.GetCurrencyInfo returned nothing"},
        {"id": 2, "quantity": 10, "max_quantity": 0, "account_wide": False},
    ]
    char = _load(raw)
    text = _text(char)
    assert "currency 1: absent (C_CurrencyInfo.GetCurrencyInfo returned nothing)" in text
    assert "currency 2: quantity 10, max_quantity 0" in text
    assert "account-wide no" in text


def test_constructed_tree_currencies_absent() -> None:
    raw = _base()
    raw["talents"]["class"]["config"]["trees"][0]["currencies"] = {
        "absent": "C_Traits.GetTreeCurrencyInfo missing"
    }
    text = _text(_load(raw))
    assert "trait currencies absent (C_Traits.GetTreeCurrencyInfo missing)" in text


def test_constructed_gear_average_and_client_absent() -> None:
    raw = _base()
    raw["gear"]["average"] = {"absent": "GetAverageItemLevel missing"}
    raw["client"] = {"absent": "GetBuildInfo missing"}
    text = _text(_load(raw))
    assert "  GetAverageItemLevel: absent (GetAverageItemLevel missing)" in text
    assert "Client: absent (GetBuildInfo missing)" in text


def test_constructed_export_absent_and_last_selected_config_absent() -> None:
    raw = _base()
    del raw["talents"]["class"]["export"]
    raw["talents"]["class"]["export_absent"] = "C_Traits.GenerateImportString raised an error"
    raw["talents"]["class"]["last_selected_config_absent"] = "the client returned no saved loadout"
    char = _load(raw)
    text = _text(char)
    assert "export string: absent (C_Traits.GenerateImportString raised an error)" in text
    assert "last selected saved loadout: absent (the client returned no saved loadout)" in text


def test_constructed_last_selected_config_value_is_kept_raw() -> None:
    raw = _base()
    raw["talents"]["class"]["last_selected_config"] = -1
    assert "last selected saved loadout: -1 (raw)" in _text(_load(raw))


@pytest.mark.parametrize("pair", ["export", "last_selected_config"])
def test_constructed_value_and_its_absent_reason_together_are_refused(pair: str) -> None:
    raw = _base()
    raw["talents"]["class"][pair] = -1 if pair == "last_selected_config" else "AAAA"
    raw["talents"]["class"][f"{pair}_absent"] = "reason"
    _refused(raw, f"both {pair} and {pair}_absent")


def test_constructed_events_unregistered_on_a_present_section() -> None:
    raw = _base()
    raw["talents"]["class"]["events_unregistered"] = ["ACTIVE_COMBAT_CONFIG_CHANGED"]
    raw["gear"]["events_unregistered"] = ["PLAYER_AVG_ITEM_LEVEL_UPDATE"]
    char = _load(raw)
    text = _text(char)
    assert (
        "  events the client did not know (so the section was not refreshed on that change): "
        "ACTIVE_COMBAT_CONFIG_CHANGED" in text
    )
    assert "PLAYER_AVG_ITEM_LEVEL_UPDATE" in text


# ─── the keys M11-22 adds ────────────────────────────────────────────────────


def test_constructed_events_unregistered_on_a_never_gathered_section() -> None:
    raw = _base()
    raw["customization"]["events_unregistered"] = ["BARBER_SHOP_APPEARANCE_APPLIED"]
    char = _load(raw)
    assert isinstance(char.customization, labaddon.AbsentSection)
    assert char.customization.events_unregistered == ["BARBER_SHOP_APPEARANCE_APPLIED"]
    assert (
        "Customization: absent (no barber-shop visit recorded with the addon enabled); "
        "events the client did not know: BARBER_SHOP_APPEARANCE_APPLIED" in _text(char)
    )


def test_constructed_currency_rows() -> None:
    raw = _base()
    raw["currencies"]["rows"] = 3
    char = _load(raw)
    assert isinstance(char.currencies, labaddon.Currencies)
    assert char.currencies.rows == 3
    assert "; 3 rows listed, header rows included)" in _text(char)
    assert labaddon.unknown_keys(char) == []


# ─── Legacy absent; an optional spec ─────────────────────────────────────────


def test_constructed_legacy_absent_with_reason() -> None:
    raw = _base()
    raw["talents"]["legacy"] = {
        "absent": "neither C_Traits.GetConfigsByType with Enum.TraitConfigType nor "
        "C_Traits.GetConfigIDBySystemID with Constants"
    }
    text = _text(_load(raw))
    assert "Legacy candidates: absent (neither C_Traits.GetConfigsByType" in text


def test_constructed_legacy_with_no_candidate_reads_none_recorded() -> None:
    raw = _base()
    raw["talents"]["legacy"]["configs"] = {}
    lines = _text(_load(raw)).splitlines()
    legacy = [line for line in lines if line.startswith(("Legacy", "  config types", "  which"))]
    assert legacy[0] == "Legacy candidates: none recorded (level 13)"
    joined = "\n".join(legacy).casefold()
    assert "empty" not in joined
    assert "locked" not in joined


def test_constructed_legacy_with_ranks_and_points() -> None:
    raw = _base()
    trees = raw["talents"]["legacy"]["configs"][0]["trees"]
    trees[1]["nodes"][0]["active_rank"] = 1
    for tree in trees[1:]:  # one pool, the same row under each tree
        tree["currencies"][0].update({"spent": 1, "quantity": 2, "max_quantity": 3})
    talents = _load(raw).talents
    assert talents is not None and isinstance(talents.legacy, labaddon.LegacyTalents)
    assert (
        labaddon.legacy_headline(talents.legacy)
        == "Legacy candidates: present, 1 rank active, 1 point spent, 2 points available"
    )


def test_constructed_spec_absent_and_spec_without_id() -> None:
    raw = _base()
    raw["spec"] = {"absent": "no GetSpecialization API (C_SpecializationInfo or global)"}
    assert "Spec: absent (no GetSpecialization API" in _text(_load(raw))
    raw = _base()
    raw["spec"] = {"api": "GetSpecialization", "index": 0}
    assert "Spec: id not returned (index 0, from GetSpecialization)" in _text(_load(raw))


# ─── gear slots sparse, keyed by slot ────────────────────────────────────────


def test_constructed_gear_slots_out_of_order_are_shown_by_slot() -> None:
    raw = _base()
    raw["gear"]["slots"].reverse()
    lines = [line for line in _text(_load(raw)).splitlines() if line.startswith("  slot ")]
    numbers = [int(line.split(":")[0].split()[-1]) for line in lines]
    assert numbers == sorted(numbers)


def test_constructed_crafter_removed_is_shown() -> None:
    raw = _base()
    raw["gear"]["slots"][0]["crafter_removed"] = True
    assert "crafter GUID removed from the link" in _text(_load(raw))


# ─── switched-off sections (M11-21) and the skip list ────────────────────────


def test_constructed_switched_off_section_and_skip_list() -> None:
    raw = _base()
    raw["gear"] = {"absent": labaddon.SWITCHED_OFF}
    raw["currencies"] = {"absent": labaddon.SWITCHED_OFF}
    raw["skip"] = ["gear", "currencies"]
    char = _load(raw)
    assert labaddon.skip_known(char) == (["gear", "currencies"], [])
    text = _text(char)
    assert "Switched off by the owner (skip): gear, currencies" in text
    assert "Gear: absent (switched off by the owner)" in text
    assert "Currencies: absent (switched off by the owner)" in text


def test_constructed_skip_list_key_the_reader_does_not_know_is_kept_and_ignored() -> None:
    raw = _base()
    raw["skip"] = ["gear", "mounts.extra"]
    char = _load(raw)
    assert char.skip == ["gear", "mounts.extra"]
    assert labaddon.skip_known(char) == (["gear"], ["mounts.extra"])
    assert "ignored in the skip list (not a section key this reader knows): mounts.extra" in (
        _text(char)
    )


def test_constructed_skip_list_entry_must_be_a_section_shaped_key() -> None:
    raw = _base()
    raw["skip"] = ["gear", "Hello there, free text"]
    _refused(raw, "WowLabCharDB.skip[1]")


# ─── customization carried across sessions ───────────────────────────────────


def test_constructed_customization_record_as_of_the_last_visit() -> None:
    raw = _base()
    raw["customization"] = {
        "as_of": "last barber-shop visit with the addon enabled",
        "carried": True,
        "recorded_at": "applied",
        "recorded_load": 1,
        "race_id": 5,
        "sex": 1,
        "chr_model_id": 42,
        "choices": [
            {"option": 10, "choice_index": 2, "choice": 123},
            {"option": 11, "choice_index": 1},
        ],
    }
    char = _load(raw)
    assert labaddon.customization_loads_ago(char) == 3  # probe.loads 4
    text = _text(char)
    assert (
        "Customization: as of the last barber-shop visit with the addon enabled, "
        "3 logins or reloads ago" in text
    )
    assert "option 10: choice 123 (index 2)" in text
    assert "option 11: choice not returned (index 1)" in text
    assert "A paid appearance change that keeps the race is invisible to the addon" in text


def test_constructed_customization_without_recorded_load() -> None:
    raw = _base()
    raw["customization"] = {
        "as_of": "last barber-shop visit with the addon enabled",
        "choices": [{"option": 1}],
    }
    char = _load(raw)
    assert labaddon.customization_loads_ago(char) is None
    assert "an unknown number of logins or reloads ago" in _text(char)


# ─── unknown keys kept; text only where the addon writes text ────────────────


def test_constructed_unknown_keys_are_kept() -> None:
    raw = _base()
    raw["future"] = {"count": 3, "flags": [True, False], "nested": {"1": 2.5}}
    raw["gear"]["slots"][0]["extra_level"] = 7
    char = _load(raw)
    assert char.model_extra is not None and char.model_extra["future"]["count"] == 3
    assert labaddon.unknown_keys(char) == ["future", "gear.slots[0].extra_level"]
    dumped = json.loads(char.model_dump_json())
    assert dumped["future"]["nested"] == {"1": 2.5}
    assert dumped["gear"]["slots"][0]["extra_level"] == 7
    assert labaddon.CharDBV1.model_validate_json(char.model_dump_json()) == char
    assert (
        "Keys this reader does not know (kept in --json): future, gear.slots[0].extra_level"
        in _text(char)
    )


@pytest.mark.parametrize(
    "path",
    ["note", "gear.note", "gear.slots.0.note", "talents.class.config.trees.0.nodes.0.note"],
)
def test_constructed_text_in_an_unknown_key_is_refused(path: str) -> None:
    raw = _base()
    _set(raw, path, "CANARY free text")
    message = _refused(raw, "holds text")
    assert "CANARY" not in message


def test_constructed_text_nested_in_an_unknown_key_is_refused() -> None:
    raw = _base()
    raw["future"] = {"a": [1, {"b": "CANARY"}]}
    message = _refused(raw, "future.a[1].b holds text")
    assert "CANARY" not in message


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("probe.loads", "4"),
        ("probe.loads", True),
        ("probe.loads", 4.5),
        ("gear.slots.0.slot", "5"),
        ("gear.slots.0.crafter_removed", 0),
        ("gear.slots.0.item_level", True),
        ("gear.average.equipped", "3.3125"),
        ("gear.average.equipped", False),
        ("talents.legacy.legacy_ui", 1),
        ("client.interface", "16001"),
        ("client.build", 70009),
        ("currencies.filter", "1"),
        ("collections.mounts.filtered", "false"),
        ("professions.list.0.skill_line", "182"),
    ],
)
def test_constructed_exact_types(path: str, value: Any) -> None:
    raw = _base()
    _set(raw, path, value)
    _refused(raw, "does not fit the schema-1 model")


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("gear.slots.0.link", "CANARY not an item link"),
        ("gear.slots.0.link", "|cnIQ2:|Hitem:1::|h[Name\nCANARY]|h|r"),
        ("talents.class.export", "CANARY text"),
        ("spec.api", "CANARY"),
        ("gear.slots.0.item_level_api", "CANARY"),
        ("client.version", "CANARY"),
        ("talents.legacy.found_by", None),
        ("talents.legacy.configs.0.found_by.0", "CANARY"),
        ("talents.legacy.skipped_types.0", "CANARY"),
        ("gear.events_unregistered", ["CANARY free text"]),
    ],
)
def test_constructed_text_fields_accept_only_the_addons_shapes(path: str, value: Any) -> None:
    raw = _base()
    if value is None:  # a key the models know nowhere near there: an unknown key with text
        _set(raw, path, "CANARY")
    else:
        _set(raw, path, value)
    message = _refused(raw)
    assert "CANARY" not in message


def test_constructed_customization_enums_accept_only_the_addons_literals() -> None:
    raw = _base()
    raw["customization"] = {"recorded_at": "closed", "choices": []}
    _refused(raw, "WowLabCharDB.customization.recorded_at")
    raw["customization"] = {"as_of": "CANARY", "choices": []}
    assert "CANARY" not in _refused(raw, "WowLabCharDB.customization.as_of")


@pytest.mark.parametrize("key", [1, True, 2.5, "has space", "x" * 65])
def test_constructed_key_that_is_not_a_plain_name_is_refused(key: Any) -> None:
    raw = _base()
    raw["gear"][key] = 1
    message = _refused(raw, "WowLabCharDB.gear")
    if isinstance(key, str):
        assert key not in message


def test_constructed_error_names_where_and_why_without_the_value() -> None:
    raw = _base()
    raw["gear"]["slots"][2]["link"] = "CANARY"
    message = _refused(raw)
    assert message.startswith("WowLabCharDB does not fit the schema-1 model: ")
    assert "WowLabCharDB.gear.slots[2].link" in message
    assert "CANARY" not in message


def test_constructed_account_unknown_numeric_key_is_kept_and_text_refused() -> None:
    account = labaddon.load_account({"schema": 1, "mounts": [1, 2]})
    assert labaddon.unknown_keys(account) == ["mounts"]
    (line,) = labaddon.describe_account(account)
    assert "reflects whichever character logged out last" in line
    with pytest.raises(labaddon.LabAddonError, match="holds text"):
        labaddon.load_account({"schema": 1, "note": "CANARY"})


# ─── reads never write (L1) ──────────────────────────────────────────────────


def test_constructed_read_char_writes_nothing(tmp_path: Path) -> None:
    folder = tmp_path / "SavedVariables"
    folder.mkdir()
    target = folder / "WowLab.lua"
    target.write_bytes((FIXTURES / FIRST).read_bytes())
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()}
    char = labaddon.read_char(target)
    assert char.probe is not None and char.probe.loads == 4
    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()}
    assert after == before
    assert sorted(p.name for p in tmp_path.rglob("*")) == ["SavedVariables", "WowLab.lua"]


def test_constructed_read_refuses_a_link(tmp_path: Path) -> None:
    real = tmp_path / "real.lua"
    real.write_bytes((FIXTURES / FIRST).read_bytes())
    link = tmp_path / "WowLab.lua"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symbolic links not available")
    with pytest.raises(OSError):
        labaddon.read_char(link)
