# Probe from review of m20/12-crew-desk-fields; reproduces `crew forget --yes --json` saying nothing of the same character's other names, which the text result names.
"""docs/LAB_PLAN.md §21.3, ruled 2026-11-20 (M20-12 fix round 1): when the same
character is also kept under other names, the plan and the result say so in
one sentence naming them. Text and `--json` say the same (M20-12 acceptance).

The text result prints the sentence; the `--json` result (stdout and stderr)
does not carry the other name at all.

CONSTRUCTED INPUT: invented keys and an obviously invented GUID, built in
`tmp_path` by the shared crew builders; no real install, no real data dir.
"""

# ruff: noqa: F811  (the fixture imported from test_crew_matches is requested by name)
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _crew_build import KEY_A, KEY_B, KEY_C, player
from test_crew_matches import _book, make, runner  # noqa: F401  (the fixture)

GUID = "Player-0000-0000000A"


def _env(make: Any) -> Any:
    env = make(_book({KEY_A: player(guid=GUID), KEY_B: player(guid=GUID), KEY_C: player()}, []))
    env.sync()
    return env


def test_positive_control_the_text_result_names_the_other_constructed(make: Any) -> None:
    result = _env(make).run("crew", "forget", KEY_A, "--yes")
    assert result.exit_code == 0, result.output
    assert KEY_B in result.stdout


def test_the_json_result_names_the_other_constructed(make: Any) -> None:
    result = _env(make).run("crew", "forget", KEY_A, "--yes", "--json")
    assert result.exit_code == 0, result.output
    assert KEY_B in result.stdout, "the --json result does not name the other name"
