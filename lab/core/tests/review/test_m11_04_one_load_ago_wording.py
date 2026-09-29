# Probe from review of m11/04-labaddon-reader; reproduces the customization line reading "1 login or reloads ago"
"""§13.1 and M11-04: the customization record is shown "as of the last
barber-shop visit with the addon enabled, N logins or reloads ago".
`_customization` makes only "login" singular for N = 1 and prints
"1 login or reloads ago".

Constructed (L8, boundary): no real capture holds a customization record
(M11-03), so one is added, in the addon's `gather` shape, to the real
first-character capture (`probe.loads` = 4, index row in
`fixtures/README.md`). Positive control: N = 3 reads "3 logins or reloads ago".
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


def _line(recorded_load: int) -> str:
    raw: dict[str, Any] = copy.deepcopy(
        luadata.parse((FIXTURES / FIRST).read_bytes()).to_python()["WowLabCharDB"]  # type: ignore[arg-type]
    )
    assert raw["probe"]["loads"] == 4
    raw["customization"] = {
        "as_of": "last barber-shop visit with the addon enabled",
        "recorded_at": "open",
        "recorded_load": recorded_load,
        "choices": [],
    }
    lines = labaddon.describe(labaddon.load_char(raw))
    return next(line for line in lines if line.startswith("Customization:"))


def test_positive_control_three_loads_ago() -> None:
    assert "3 logins or reloads ago" in _line(1)


def test_constructed_one_load_ago_is_singular() -> None:
    line = _line(3)
    assert "1 login or reloads ago" not in line
    assert "1 login or reload ago" in line
