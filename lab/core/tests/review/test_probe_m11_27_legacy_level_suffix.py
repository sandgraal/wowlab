# Probe from review of m11/27-char-show-followups; reproduces the character's level glued onto the last Legacy config's figures
"""Constructed (L8 boundary case): the real first-character capture with its
one Legacy candidate config duplicated under a second id. `_legacy` appends
" (level N)" after `legacy_headline`, so in the per-config form (M11-27 (a))
the character's level reads as part of the last config's figures:
"...; config 9: nothing spent, 0 points available (level 13)".
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
_BASE: Any = luadata.parse((FIXTURES / FIRST).read_bytes()).to_python()["WowLabCharDB"]


def _legacy_line(raw: dict[str, Any]) -> str:
    lines = labaddon.describe(labaddon.load_char(raw))
    (line,) = [line for line in lines if line.startswith("Legacy candidates:")]
    return line


def test_constructed_positive_control_single_config_carries_the_level() -> None:
    line = _legacy_line(copy.deepcopy(_BASE))
    assert line.endswith("(level 13)"), line


def test_constructed_level_is_not_part_of_the_last_config_figures() -> None:
    raw = copy.deepcopy(_BASE)
    configs = raw["talents"]["legacy"]["configs"]
    second = copy.deepcopy(configs[0])
    second["id"] = 9
    configs.append(second)
    line = _legacy_line(raw)
    assert "(level 13)" in line, line
    last_config = line.rsplit("config 9:", 1)[1]
    assert "(level" not in last_config, line
