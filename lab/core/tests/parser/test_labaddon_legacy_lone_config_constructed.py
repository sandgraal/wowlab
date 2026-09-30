"""`wowlab_core.labaddon`, M11-34: two lines under the Legacy candidates
headline in `wowlab char show`.

(1) The lone candidate. The addon leaves the active class config out of
`talents.legacy` only when `C_ClassTalents.GetActiveConfigID` gave an id,
and does not record the id it left out. With one config listed, the reader
compares its id with the one `talents.class` records: on a match the line
says the one config listed is the active class config and infers no Legacy
system; when `talents.class` records no config id (not in the file, absent
with a reason, or its config absent without an id) the elimination is
qualified; a different id keeps the M11-32 line.

(2) The types not searched. The addon leaves `skipped_types` empty when it
did not search by type at all (`C_Traits.GetConfigsByType` missing while
`Enum.TraitConfigType` exists, or the reverse), which reads the same as a
search that skipped nothing. Without a config found by type, the line says
that possibly no type was searched.

Neither case has been observed on a client. The `constructed` cases (L8:
boundary cases, labelled in the test id) start from the real first-character
capture (M11-03, index row in `fixtures/README.md`) as `luadata.to_python`
gives it, and change the ids, `found_by`, `skipped_types` or the
`talents.class` section as each test says. Every real character capture
lists one config, found by type, that is not the class config, so its output
is unchanged (the last test here, and
`test_labaddon_legacy_wording.py::test_real_captures_list_one_config_and_keep_the_elimination_line`).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import labaddon, luadata

pytestmark = pytest.mark.parser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CHAR = "forever/WTF/Account/90000001#6/1/{}/SavedVariables/WowLab.lua"
FIRST = "macos/" + CHAR.format("Labchard-Labrealmg")
REAL = [
    FIRST,
    "macos/" + CHAR.format("Labcharb-Labrealmf"),
    "macos-70058/" + CHAR.format("Labchard-Labrealmg"),
]

_BASE: Any = luadata.parse((FIXTURES / FIRST).read_bytes()).to_python()["WowLabCharDB"]
CLASS_ID = 640511  # talents.class.config.id in FIRST
LEGACY_ID = 640755  # the one talents.legacy config in FIRST, found by type:Generic

HEADLINE = "Legacy candidates: present, nothing spent, 0 points available (level 13)"
ALL_ABSENT_HEADLINE = "Legacy candidates: present, every config absent with a reason (level 13)"
OPENER = "panel opener ToggleLegacySystemUI present: {opener}"
ONE_LINE = "  which config is the Legacy system is inferred by elimination; " + OPENER.format(
    opener="yes"
)
MATCH_LINE = (
    "  the one config listed is the active class config (config {id}, as Class talents records "
    "it), which the addon did not leave out, so no config is inferred to be the Legacy system; "
    + OPENER
)
UNCHECKED_LINE = (
    "  which config is the Legacy system is inferred by elimination, but {why}, so the reader "
    "cannot check that it is not the active class config (the addon leaves that out only when "
    "the client gave its id); " + OPENER
)
NO_CLASS_ID = "Class talents records no config id"
NO_LISTED_ID = "the config listed has no id"
SEVERAL_LINE = (  # M11-32, must not change
    "  the addon lists every trait config it found by type (except the types below) or by a "
    "client system id, less the active class talents when the client gave their id; which of "
    "these is the Legacy system is not recorded; panel opener ToggleLegacySystemUI present: yes"
)
NONE_LINE = "  no candidate config listed; panel opener ToggleLegacySystemUI present: yes"
SKIPPED_LINE = "  config types not searched: Invalid, Combat, Profession"
NONE_SKIPPED_LINE = "  config types not searched: none recorded"
NO_TYPE_SEARCH_LINE = (
    "  config types not searched: none recorded; no config was found by type, and the addon "
    "also records none when it did not search by type at all (C_Traits.GetConfigsByType or "
    "Enum.TraitConfigType missing), so possibly no type was searched"
)
BY_SYSTEM = "system:ExampleConsts.EXAMPLE_SYSTEM_ID"  # constructed, the shape Talents.lua writes


def _base() -> dict[str, Any]:
    return copy.deepcopy(_BASE)


def _legacy(raw: dict[str, Any]) -> dict[str, Any]:
    legacy: dict[str, Any] = raw["talents"]["legacy"]
    return legacy


def _absent_config(config_id: int | None, found_by: str = "type:Generic") -> dict[str, Any]:
    config: dict[str, Any] = {
        "absent": "C_Traits.GetConfigInfo returned nothing",
        "found_by": [found_by],
    }
    if config_id is not None:
        config["id"] = config_id
    return config


def _lines(raw: dict[str, Any]) -> list[str]:
    return labaddon.describe(labaddon.load_char(raw))


def _legacy_lines(lines: list[str]) -> list[str]:
    start = next(i for i, line in enumerate(lines) if line.startswith("Legacy candidates:"))
    return lines[start : start + 3]


def _described(raw: dict[str, Any]) -> list[str]:
    return _legacy_lines(_lines(raw))


def _elimination_claimed(lines: list[str]) -> bool:
    return any(line.endswith(ONE_LINE.strip()) for line in lines)


# ─── (1) the lone candidate is the active class config ───────────────────────


@pytest.mark.parametrize(("legacy_ui", "opener"), [(True, "yes"), (False, "no")])
def test_constructed_lone_config_matching_the_class_id_names_no_legacy_system(
    legacy_ui: bool, opener: str
) -> None:
    raw = _base()
    legacy = _legacy(raw)
    legacy["configs"][0]["id"] = CLASS_ID
    legacy["legacy_ui"] = legacy_ui
    lines = _lines(raw)
    assert _legacy_lines(lines) == [
        HEADLINE,
        MATCH_LINE.format(id=CLASS_ID, opener=opener),
        SKIPPED_LINE,
    ]
    assert not any("inferred by elimination" in line for line in lines)
    # the id the line names is the one Class talents shows
    assert f"Class talents: config {CLASS_ID}, type 4 (the client's raw enum number)" in lines


def test_constructed_lone_absent_config_matching_the_class_id_names_no_legacy_system() -> None:
    raw = _base()
    _legacy(raw)["configs"] = [_absent_config(CLASS_ID)]
    assert _described(raw) == [
        ALL_ABSENT_HEADLINE,
        MATCH_LINE.format(id=CLASS_ID, opener="yes"),
        SKIPPED_LINE,
    ]


def test_constructed_lone_config_matching_an_absent_class_config_id() -> None:
    # talents.class's config absent with a reason still carries the id the
    # client gave, so the comparison is made with it.
    raw = _base()
    raw["talents"]["class"]["config"] = {
        "id": LEGACY_ID,
        "absent": "C_Traits.GetConfigInfo returned nothing",
    }
    assert _described(raw) == [
        HEADLINE,
        MATCH_LINE.format(id=LEGACY_ID, opener="yes"),
        SKIPPED_LINE,
    ]


def test_constructed_lone_config_with_a_different_class_id_keeps_the_elimination_line() -> None:
    assert _described(_base()) == [HEADLINE, ONE_LINE, SKIPPED_LINE]
    raw = _base()
    _legacy(raw)["configs"] = [_absent_config(7)]
    assert _described(raw) == [ALL_ABSENT_HEADLINE, ONE_LINE, SKIPPED_LINE]


def test_constructed_several_configs_one_matching_the_class_id_keep_the_several_line() -> None:
    # M11-34 changes the one-config line only; the M11-32 wording stays.
    raw = _base()
    second = copy.deepcopy(_legacy(raw)["configs"][0])
    second["id"] = CLASS_ID
    _legacy(raw)["configs"].append(second)
    assert _described(raw)[1:] == [SEVERAL_LINE, SKIPPED_LINE]


# ─── (1) talents.class records no config id: the elimination is qualified ────


def _no_class_section(raw: dict[str, Any]) -> None:
    del raw["talents"]["class"]


def _class_absent(reason: str) -> Any:
    def change(raw: dict[str, Any]) -> None:
        raw["talents"]["class"] = {"absent": reason}

    return change


def _class_config_absent_without_id(raw: dict[str, Any]) -> None:
    raw["talents"]["class"]["config"] = {"absent": "C_Traits.GetConfigInfo returned nothing"}


NO_CLASS_ID_CASES = {
    "not-in-the-file": _no_class_section,
    "active-config-missing": _class_absent("C_ClassTalents.GetActiveConfigID missing"),
    "active-config-returned-none": _class_absent(
        "C_ClassTalents.GetActiveConfigID returned no config"
    ),
    "switched-off": _class_absent(labaddon.SWITCHED_OFF),
    "config-absent-without-id": _class_config_absent_without_id,
}


@pytest.mark.parametrize("case", sorted(NO_CLASS_ID_CASES))
def test_constructed_lone_config_without_a_class_id_qualifies_the_elimination(case: str) -> None:
    raw = _base()
    NO_CLASS_ID_CASES[case](raw)
    lines = _lines(raw)
    assert _legacy_lines(lines) == [
        HEADLINE,
        UNCHECKED_LINE.format(why=NO_CLASS_ID, opener="yes"),
        SKIPPED_LINE,
    ]
    assert not _elimination_claimed(lines)


def test_constructed_lone_absent_config_without_a_class_id_qualifies_the_elimination() -> None:
    raw = _base()
    _no_class_section(raw)
    _legacy(raw)["configs"] = [_absent_config(7)]
    _legacy(raw)["legacy_ui"] = False
    assert _described(raw) == [
        ALL_ABSENT_HEADLINE,
        UNCHECKED_LINE.format(why=NO_CLASS_ID, opener="no"),
        SKIPPED_LINE,
    ]


def test_constructed_lone_config_without_an_id_qualifies_the_elimination() -> None:
    raw = _base()
    _legacy(raw)["configs"] = [_absent_config(None)]
    assert _described(raw) == [
        ALL_ABSENT_HEADLINE,
        UNCHECKED_LINE.format(why=NO_LISTED_ID, opener="yes"),
        SKIPPED_LINE,
    ]
    # with no id on either side, the missing class id is the one named
    _no_class_section(raw)
    assert _described(raw)[1] == UNCHECKED_LINE.format(why=NO_CLASS_ID, opener="yes")


@pytest.mark.parametrize("case", sorted(NO_CLASS_ID_CASES))
def test_constructed_no_class_id_leaves_the_several_and_none_lines(case: str) -> None:
    raw = _base()
    NO_CLASS_ID_CASES[case](raw)
    _legacy(raw)["configs"].append(_absent_config(7))
    assert _described(raw)[1] == SEVERAL_LINE
    _legacy(raw)["configs"] = []
    assert _described(raw)[1] == NONE_LINE


# ─── (2) no type searched: an empty skipped_types says so ────────────────────


def test_constructed_no_type_search_lone_config_found_by_system_id() -> None:
    # C_Traits.GetConfigsByType missing while Enum.TraitConfigType exists:
    # Talents.lua leaves `skipped` empty, and only the system-id scan finds.
    raw = _base()
    legacy = _legacy(raw)
    legacy["skipped_types"] = {}  # an empty Lua table, as luadata reads it
    legacy["configs"][0]["found_by"] = [BY_SYSTEM]
    lines = _lines(raw)
    assert _legacy_lines(lines) == [HEADLINE, ONE_LINE, NO_TYPE_SEARCH_LINE]
    assert f"  config {LEGACY_ID}, type 3 (the client's raw enum number), found by {BY_SYSTEM}" in (
        lines
    )


def test_constructed_no_type_search_and_no_config_listed() -> None:
    raw = _base()
    legacy = _legacy(raw)
    legacy["skipped_types"] = []
    legacy["configs"] = []
    assert _described(raw) == [
        "Legacy candidates: none recorded (level 13)",
        NONE_LINE,
        NO_TYPE_SEARCH_LINE,
    ]


def test_constructed_no_type_search_several_configs_all_by_system_id() -> None:
    raw = _base()
    legacy = _legacy(raw)
    legacy["skipped_types"] = []
    legacy["configs"][0]["found_by"] = [BY_SYSTEM]
    legacy["configs"].append(_absent_config(7, found_by=BY_SYSTEM))
    assert _described(raw)[1:] == [SEVERAL_LINE, NO_TYPE_SEARCH_LINE]


@pytest.mark.parametrize("absent", [False, True], ids=["present", "absent"])
def test_constructed_empty_skipped_types_with_a_config_found_by_type_reads_none_recorded(
    absent: bool,
) -> None:
    # A config found by type shows the type search ran; an enum without
    # Invalid, Combat and Profession leaves nothing to skip.
    raw = _base()
    legacy = _legacy(raw)
    legacy["skipped_types"] = []
    if absent:
        legacy["configs"] = [_absent_config(7, found_by=BY_SYSTEM), _absent_config(8)]
    else:
        legacy["configs"][0]["found_by"] = [BY_SYSTEM, "type:Generic"]
    assert _described(raw)[2] == NONE_SKIPPED_LINE


def test_constructed_skipped_types_recorded_with_only_a_system_id_find_is_unchanged() -> None:
    raw = _base()
    _legacy(raw)["configs"][0]["found_by"] = [BY_SYSTEM]
    assert _described(raw) == [HEADLINE, ONE_LINE, SKIPPED_LINE]


# ─── both at once ────────────────────────────────────────────────────────────


def test_constructed_lone_class_config_found_by_system_id_with_no_type_search() -> None:
    # The unobserved case the ticket describes end to end: no type search,
    # the class config found by a system id, not left out.
    raw = _base()
    legacy = _legacy(raw)
    legacy["skipped_types"] = []
    legacy["configs"][0]["id"] = CLASS_ID
    legacy["configs"][0]["found_by"] = [BY_SYSTEM]
    lines = _lines(raw)
    assert _legacy_lines(lines) == [
        HEADLINE,
        MATCH_LINE.format(id=CLASS_ID, opener="yes"),
        NO_TYPE_SEARCH_LINE,
    ]
    assert not any("inferred by elimination" in line for line in lines)


# ─── real captures: byte-identical ───────────────────────────────────────────


@pytest.mark.parametrize("name", REAL)
def test_real_captures_keep_both_lines(name: str) -> None:
    char = labaddon.parse_char((FIXTURES / name).read_bytes())
    assert char.talents is not None
    legacy = char.talents.legacy
    klass = char.talents.class_
    assert isinstance(legacy, labaddon.LegacyTalents)
    assert isinstance(klass, labaddon.ClassTalents)
    # the premise: one config, found by type, not the class config
    assert len(legacy.configs) == 1
    assert legacy.configs[0].id != klass.config.id
    assert any(how.startswith("type:") for how in legacy.configs[0].found_by)
    assert legacy.skipped_types == ["Invalid", "Combat", "Profession"]
    lines = _legacy_lines(labaddon.describe(char))
    assert lines[1:] == [ONE_LINE, SKIPPED_LINE]
