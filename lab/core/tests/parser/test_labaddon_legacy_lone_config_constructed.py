"""`wowlab_core.labaddon`, M11-34: the Legacy candidates headline and the two
lines under it in `wowlab char show`.

(1) The lone candidate. The addon leaves the active class config out of
`talents.legacy` only when `C_ClassTalents.GetActiveConfigID` gave an id,
and does not record the id it left out. With one config listed, the reader
compares its id with the one `talents.class` records, once
(`_lone_class_config`), and the headline and the line under it both use that
answer. On a match the headline gives no figures (they are the class
talents', shown under Class talents) and says only the active class config
is listed, and the line says the one config listed is the active class
config and infers no Legacy system. When `talents.class` records no config
id (not in the file, absent with a reason, or its config absent without an
id) the elimination is qualified. A different id keeps the M11-27 headline
and the M11-32 line.

(2) The types not searched. The addon leaves `skipped_types` empty when it
did not search by type at all (`C_Traits.GetConfigsByType` missing while
`Enum.TraitConfigType` exists, or the reverse), and also when the client's
enum lists none of Invalid, Combat and Profession. Without a config found by
type, the line says an empty list does not show the type search ran.

Neither case has been observed on a client. The `constructed` cases (L8:
boundary cases, labelled in the test id) start from the real first-character
capture (M11-03, index row in `fixtures/README.md`) as `luadata.to_python`
gives it, and change the ids, `found_by`, `skipped_types`, the figures or the
`talents.class` section as each test says. Every real character capture
lists one config, found by type, that is not the class config, so its output
is unchanged (the last test here, and
`test_labaddon_legacy_wording.py::test_real_captures_list_one_config_and_keep_the_elimination_line`).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import labaddon, luadata

pytestmark = pytest.mark.parser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CHAR = "forever/WTF/Account/90000001#6/1/{}/SavedVariables/WowLab.lua"
FIRST = "macos/" + CHAR.format("Labchard-Labrealmg")
REAL = {
    FIRST: 13,
    "macos/" + CHAR.format("Labcharb-Labrealmf"): 10,
    "macos-70058/" + CHAR.format("Labchard-Labrealmg"): 13,
}

_BASE: Any = luadata.parse((FIXTURES / FIRST).read_bytes()).to_python()["WowLabCharDB"]
CLASS_ID = 640511  # talents.class.config.id in FIRST
LEGACY_ID = 640755  # the one talents.legacy config in FIRST, found by type:Generic

HEADLINE = "Legacy candidates: present, nothing spent, 0 points available (level 13)"
ALL_ABSENT_HEADLINE = "Legacy candidates: present, every config absent with a reason (level 13)"
MATCH_HEADLINE = (
    "Legacy candidates: only the active class config listed (config {id}; its figures are "
    "under Class talents){level}"
)
MATCH_HEADLINE_CLASS_ABSENT = (
    "Legacy candidates: only the active class config listed (config {id}; Class talents shows "
    "it absent with a reason){level}"
)
LEVEL = " (level 13)"
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
    "cannot check that the config listed is not the active class config (the addon leaves the "
    "active class config out only when the client gave its id); " + OPENER
)
NO_CLASS_ID = "Class talents records no config id"
NO_LISTED_ID = "the config listed has no id"
SEVERAL_LINE = (  # M11-32, must not change
    "  the addon lists every trait config it found by type (except the types below) or by a "
    "client system id, less the active class talents when the client gave their id; which of "
    "these is the Legacy system is not recorded; panel opener ToggleLegacySystemUI present: yes"
)
SEVERAL_HEADLINE = "Legacy candidates: present, 2 configs (level 13); "  # M11-27, a prefix
NONE_LINE = "  no candidate config listed; panel opener ToggleLegacySystemUI present: yes"
SKIPPED_LINE = "  config types not searched: Invalid, Combat, Profession"
NONE_SKIPPED_LINE = "  config types not searched: none recorded"
NO_TYPE_SEARCH_LINE = (
    "  config types not searched: none recorded, which does not show the type search ran: no "
    "config here was found by type, and the addon records none both when "
    "C_Traits.GetConfigsByType or Enum.TraitConfigType is missing (no type searched) and when "
    "the client's enum lists none of Invalid, Combat and Profession"
)
BY_SYSTEM = "system:ExampleConsts.EXAMPLE_SYSTEM_ID"  # constructed, the shape Talents.lua writes
MATCH_HEAD = "Legacy candidates: only the active class config listed"
MATCH_START = "  the one config listed is the active class config"


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


def _with_figures(config: dict[str, Any]) -> None:
    """Spend points in a config, so its headline figures are not "nothing spent"."""
    for tree in config["trees"]:
        for row in tree["currencies"] if isinstance(tree["currencies"], list) else []:
            row["spent"] = 4
        if isinstance(tree["nodes"], list) and tree["nodes"]:  # `{}` when empty
            tree["nodes"][0]["active_rank"] = 4


def _lines(raw: dict[str, Any]) -> list[str]:
    return labaddon.describe(labaddon.load_char(raw))


def _legacy_lines(lines: list[str]) -> list[str]:
    start = next(i for i, line in enumerate(lines) if line.startswith("Legacy candidates:"))
    return lines[start : start + 3]


def _described(raw: dict[str, Any]) -> list[str]:
    return _legacy_lines(_lines(raw))


def _elimination_claimed(lines: list[str]) -> bool:
    return any(line.endswith(ONE_LINE.strip()) for line in lines)


def _models(
    raw: dict[str, Any],
) -> tuple[labaddon.LegacyTalents, labaddon.TraitConfig | labaddon.AbsentConfig | None]:
    talents = labaddon.load_char(raw).talents
    assert talents is not None and isinstance(talents.legacy, labaddon.LegacyTalents)
    klass = talents.class_
    config = klass.config if isinstance(klass, labaddon.ClassTalents) else None
    return talents.legacy, config


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
        MATCH_HEADLINE.format(id=CLASS_ID, level=LEVEL),
        MATCH_LINE.format(id=CLASS_ID, opener=opener),
        SKIPPED_LINE,
    ]
    assert not any("inferred by elimination" in line for line in lines)
    # the id both lines name is the one Class talents shows
    assert f"Class talents: config {CLASS_ID}, type 4 (the client's raw enum number)" in lines
    # the config's own dump is still listed under the headline
    assert f"  config {CLASS_ID}, type 3 (the client's raw enum number), found by type:Generic" in (
        lines
    )


def test_constructed_lone_class_config_headline_drops_its_figures_not_the_non_match() -> None:
    # With points spent, a non-match headline gives the figures; on a match
    # they are the class talents', so the headline gives none.
    raw = _base()
    _with_figures(_legacy(raw)["configs"][0])
    other = _described(raw)[0]
    assert other.startswith("Legacy candidates: present, ") and "4 points spent" in other
    assert other.endswith(LEVEL)
    _legacy(raw)["configs"][0]["id"] = CLASS_ID
    headline = _described(raw)[0]
    assert headline == MATCH_HEADLINE.format(id=CLASS_ID, level=LEVEL)
    assert "spent" not in headline and "available" not in headline and "ranks" not in headline


def test_constructed_lone_class_config_headline_without_a_level() -> None:
    raw = _base()
    _legacy(raw)["configs"][0]["id"] = CLASS_ID
    del _legacy(raw)["player_level"]
    assert _described(raw)[0] == MATCH_HEADLINE.format(id=CLASS_ID, level="")


def test_constructed_lone_absent_config_matching_the_class_id_names_no_legacy_system() -> None:
    raw = _base()
    _legacy(raw)["configs"] = [_absent_config(CLASS_ID)]
    assert _described(raw) == [
        MATCH_HEADLINE.format(id=CLASS_ID, level=LEVEL),
        MATCH_LINE.format(id=CLASS_ID, opener="yes"),
        SKIPPED_LINE,
    ]


def test_constructed_lone_config_matching_an_absent_class_config_id() -> None:
    # talents.class's config absent with a reason still carries the id the
    # client gave, so the comparison is made with it; Class talents shows no
    # figures for it, and the headline says so.
    raw = _base()
    raw["talents"]["class"]["config"] = {
        "id": LEGACY_ID,
        "absent": "C_Traits.GetConfigInfo returned nothing",
    }
    lines = _lines(raw)
    assert _legacy_lines(lines) == [
        MATCH_HEADLINE_CLASS_ABSENT.format(id=LEGACY_ID, level=LEVEL),
        MATCH_LINE.format(id=LEGACY_ID, opener="yes"),
        SKIPPED_LINE,
    ]
    assert (
        f"Class talents: config {LEGACY_ID} absent (C_Traits.GetConfigInfo returned nothing)"
        in lines
    )


def test_constructed_lone_config_with_a_different_class_id_keeps_both_lines() -> None:
    # the M11-27 headline and the M11-32 line
    assert _described(_base()) == [HEADLINE, ONE_LINE, SKIPPED_LINE]
    raw = _base()
    _legacy(raw)["configs"] = [_absent_config(7)]
    assert _described(raw) == [ALL_ABSENT_HEADLINE, ONE_LINE, SKIPPED_LINE]


def test_constructed_several_configs_one_matching_the_class_id_keep_their_lines() -> None:
    # M11-34 changes the one-config case only; the M11-27 headline and the
    # M11-32 line stay.
    raw = _base()
    second = copy.deepcopy(_legacy(raw)["configs"][0])
    second["id"] = CLASS_ID
    _legacy(raw)["configs"].append(second)
    headline, line, skipped = _described(raw)
    assert headline.startswith(SEVERAL_HEADLINE)
    assert f"config {CLASS_ID}: nothing spent, 0 points available" in headline
    assert [line, skipped] == [SEVERAL_LINE, SKIPPED_LINE]


def test_constructed_legacy_headline_takes_the_class_config() -> None:
    # The public function: without the class config it cannot compare and
    # gives the M11-27 headline; with it, the match headline (no level: the
    # caller adds it) or, on a different id, the M11-27 headline.
    raw = _base()
    _legacy(raw)["configs"][0]["id"] = CLASS_ID
    legacy, config = _models(raw)
    assert labaddon.legacy_headline(legacy) == HEADLINE.removesuffix(LEVEL)
    assert labaddon.legacy_headline(legacy, config) == MATCH_HEADLINE.format(id=CLASS_ID, level="")
    legacy, config = _models(_base())
    assert labaddon.legacy_headline(legacy, config) == HEADLINE.removesuffix(LEVEL)


# ─── (1) talents.class records no config id: the elimination is qualified ────


def _no_class_section(raw: dict[str, Any]) -> None:
    del raw["talents"]["class"]


def _class_absent(reason: str) -> Callable[[dict[str, Any]], None]:
    def change(raw: dict[str, Any]) -> None:
        raw["talents"]["class"] = {"absent": reason}

    return change


def _class_config_absent_without_id(raw: dict[str, Any]) -> None:
    raw["talents"]["class"]["config"] = {"absent": "C_Traits.GetConfigInfo returned nothing"}


NO_CLASS_ID_CASES: dict[str, Callable[[dict[str, Any]], None]] = {
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
    # the noun is named: no bare "it" for the config being checked
    assert "cannot check that it " not in lines[lines.index(HEADLINE) + 1]


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


# ─── (1) the headline and the line agree ─────────────────────────────────────


def _match_present(raw: dict[str, Any]) -> None:
    _legacy(raw)["configs"][0]["id"] = CLASS_ID


def _match_absent(raw: dict[str, Any]) -> None:
    _legacy(raw)["configs"] = [_absent_config(CLASS_ID)]


def _match_class_absent(raw: dict[str, Any]) -> None:
    raw["talents"]["class"]["config"] = {"id": LEGACY_ID, "absent": "reason"}


def _other_absent(raw: dict[str, Any]) -> None:
    _legacy(raw)["configs"] = [_absent_config(7)]


def _listed_without_id(raw: dict[str, Any]) -> None:
    _legacy(raw)["configs"] = [_absent_config(None)]


def _several_one_matching(raw: dict[str, Any]) -> None:
    _legacy(raw)["configs"].append(_absent_config(CLASS_ID))


def _none_listed(raw: dict[str, Any]) -> None:
    _legacy(raw)["configs"] = []


AGREEMENT: dict[str, tuple[Callable[[dict[str, Any]], None], bool]] = {
    "match-present": (_match_present, True),
    "match-absent": (_match_absent, True),
    "match-class-config-absent": (_match_class_absent, True),
    "different-id": (lambda raw: None, False),
    "different-id-absent": (_other_absent, False),
    "listed-without-id": (_listed_without_id, False),
    "several-one-matching": (_several_one_matching, False),
    "none-listed": (_none_listed, False),
    **{f"no-class-id-{name}": (change, False) for name, change in NO_CLASS_ID_CASES.items()},
}


@pytest.mark.parametrize("case", sorted(AGREEMENT))
def test_constructed_headline_and_line_agree_on_the_match(case: str) -> None:
    change, matched = AGREEMENT[case]
    raw = _base()
    change(raw)
    headline, line, _ = _described(raw)
    assert headline.startswith(MATCH_HEAD) is matched
    assert line.startswith(MATCH_START) is matched


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
        MATCH_HEADLINE.format(id=CLASS_ID, level=LEVEL),
        MATCH_LINE.format(id=CLASS_ID, opener="yes"),
        NO_TYPE_SEARCH_LINE,
    ]
    assert not any("inferred by elimination" in line for line in lines)


# ─── real captures: byte-identical ───────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(REAL))
def test_real_captures_keep_all_three_lines(name: str) -> None:
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
    assert _legacy_lines(labaddon.describe(char)) == [
        f"Legacy candidates: present, nothing spent, 0 points available (level {REAL[name]})",
        ONE_LINE,
        SKIPPED_LINE,
    ]
    assert labaddon.legacy_headline(legacy, klass.config) == labaddon.legacy_headline(legacy)
