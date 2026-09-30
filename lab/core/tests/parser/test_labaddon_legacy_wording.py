"""`wowlab_core.labaddon`, M11-32: the line under the Legacy candidates
headline in `wowlab char show`.

(1) With several configs listed, the line says how the addon found them: by
type (except the types on the next line) or by a client system id (not
type-checked), less the active class talents only when the client gave
their id, which the file does not record (round 1 of review replaced the
ticket's wording with this one on the conductor's authority). (2) That line
is used whenever more than one config is listed, including when every one is
absent with a reason. One config keeps "inferred by elimination"; none reads
"no candidate config listed".

The `constructed` cases (L8: boundary cases, labelled in the test id) start
from the real first-character capture (M11-03, index row in
`fixtures/README.md`) as `luadata.to_python` gives it and change the config
list and, where a test says so, `legacy_ui` or `player_level`. The last test
reads every real character capture unchanged: each lists one config, so its
output keeps the one-config wording.
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
REAL = {
    FIRST: 13,
    "macos/" + CHAR.format("Labcharb-Labrealmf"): 10,
    "macos-70058/" + CHAR.format("Labchard-Labrealmg"): 13,
}

_BASE: Any = luadata.parse((FIXTURES / FIRST).read_bytes()).to_python()["WowLabCharDB"]

SEVERAL_LINE = (
    "  the addon lists every trait config it found by type (except the types below) or by a "
    "client system id, less the active class talents when the client gave their id; which of "
    "these is the Legacy system is not recorded; panel opener ToggleLegacySystemUI present: "
    "{opener}"
)
ONE_LINE = (
    "  which config is the Legacy system is inferred by elimination; panel opener "
    "ToggleLegacySystemUI present: yes"
)
NONE_LINE = "  no candidate config listed; panel opener ToggleLegacySystemUI present: {opener}"
SKIPPED_LINE = "  config types not searched: Invalid, Combat, Profession"


def _absent(config_id: int) -> dict[str, Any]:
    return {
        "id": config_id,
        "absent": "C_Traits.GetConfigInfo returned nothing",
        "found_by": ["type:Generic"],
    }


def _base() -> dict[str, Any]:
    return copy.deepcopy(_BASE)


def _legacy(raw: dict[str, Any]) -> dict[str, Any]:
    legacy: dict[str, Any] = raw["talents"]["legacy"]
    return legacy


def _with_second_config(raw: dict[str, Any]) -> dict[str, Any]:
    configs = _legacy(raw)["configs"]
    second = copy.deepcopy(configs[0])
    second["id"] = 640756
    configs.append(second)
    return raw


def _legacy_lines(lines: list[str]) -> list[str]:
    start = next(i for i, line in enumerate(lines) if line.startswith("Legacy candidates:"))
    return lines[start : start + 3]


def _described(raw: dict[str, Any]) -> list[str]:
    return _legacy_lines(labaddon.describe(labaddon.load_char(raw)))


# ─── (1) several configs: how the addon found them, with its caveats ─────────


def test_constructed_two_configs_with_figures_say_how_the_addon_found_them() -> None:
    raw = _with_second_config(_base())
    headline, second, skipped = _described(raw)
    assert headline.startswith("Legacy candidates: present, 2 configs (level 13); ")
    assert second == SEVERAL_LINE.format(opener="yes")
    # a system-id find is not type-checked; the class config is left out
    # only when the client gave its id, which the file does not record
    assert "or by a client system id" in second
    assert "less the active class talents when the client gave their id" in second
    assert "did not rule out" not in second
    assert "except the active class talents and" not in second
    assert skipped == SKIPPED_LINE


@pytest.mark.parametrize(("legacy_ui", "opener"), [(True, "yes"), (False, "no")])
def test_constructed_several_line_computes_the_panel_opener_clause(
    legacy_ui: bool, opener: str
) -> None:
    raw = _with_second_config(_base())
    _legacy(raw)["legacy_ui"] = legacy_ui
    assert _described(raw)[1] == SEVERAL_LINE.format(opener=opener)


def test_constructed_absent_config_beside_a_present_one_uses_the_several_line() -> None:
    raw = _base()
    _legacy(raw)["configs"].append(_absent(7))
    assert _described(raw)[1] == SEVERAL_LINE.format(opener="yes")


# ─── (2) more than one config listed, every one absent ───────────────────────


def test_constructed_two_configs_every_one_absent_use_the_several_line() -> None:
    raw = _base()
    _legacy(raw)["configs"] = [_absent(7), _absent(8)]
    lines = labaddon.describe(labaddon.load_char(raw))
    assert _legacy_lines(lines) == [
        "Legacy candidates: present, every config absent with a reason (level 13)",
        SEVERAL_LINE.format(opener="yes"),
        SKIPPED_LINE,
    ]
    assert not any("inferred by elimination" in line for line in lines)


def test_constructed_three_absent_configs_without_a_level_use_the_several_line() -> None:
    raw = _base()
    legacy = _legacy(raw)
    legacy["configs"] = [_absent(7), _absent(8), _absent(9)]
    legacy["legacy_ui"] = False
    del legacy["player_level"]
    assert _described(raw)[:2] == [
        "Legacy candidates: present, every config absent with a reason",
        SEVERAL_LINE.format(opener="no"),
    ]


# ─── one config keeps "inferred by elimination"; none says so ────────────────


def test_constructed_one_absent_config_keeps_the_elimination_line() -> None:
    raw = _base()
    _legacy(raw)["configs"] = [_absent(7)]
    assert _described(raw) == [
        "Legacy candidates: present, every config absent with a reason (level 13)",
        ONE_LINE,
        SKIPPED_LINE,
    ]


@pytest.mark.parametrize(("legacy_ui", "opener"), [(True, "yes"), (False, "no")])
def test_constructed_no_config_listed_infers_nothing(legacy_ui: bool, opener: str) -> None:
    raw = _base()
    _legacy(raw)["configs"] = []
    _legacy(raw)["legacy_ui"] = legacy_ui
    lines = labaddon.describe(labaddon.load_char(raw))
    assert _legacy_lines(lines) == [
        "Legacy candidates: none recorded (level 13)",
        NONE_LINE.format(opener=opener),
        SKIPPED_LINE,
    ]
    assert not any("inferred by elimination" in line for line in lines)


# ─── real captures: one config each, output unchanged ────────────────────────


@pytest.mark.parametrize("name", sorted(REAL))
def test_real_captures_list_one_config_and_keep_the_elimination_line(name: str) -> None:
    char = labaddon.parse_char((FIXTURES / name).read_bytes())
    assert char.talents is not None
    legacy = char.talents.legacy
    assert isinstance(legacy, labaddon.LegacyTalents)
    assert len(legacy.configs) == 1
    assert _legacy_lines(labaddon.describe(char)) == [
        f"Legacy candidates: present, nothing spent, 0 points available (level {REAL[name]})",
        ONE_LINE,
        SKIPPED_LINE,
    ]
