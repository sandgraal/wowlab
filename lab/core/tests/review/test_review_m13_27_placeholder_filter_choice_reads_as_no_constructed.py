# Probe from review of m13/27-explorer-stage2; reproduces the Verdict filter
# offering two choices with the same words when a placeholder sits beside a `no` path.
"""CONSTRUCTED: one verdict in the captured tree swapped for a placeholder
(L8: our own JSON, a case the macOS capture cannot reach).

Round 1 on #M13-27 made placeholders a Verdict filter choice of their own
(`filterKey` returns "placeholder"). `fillLevels` labels each choice with the
first item's `label`, and Python labels a placeholder with `LABELS["no"]`,
"wowlab won't touch" (`verdict.Verdict._fill_labels`). So on an install that
has both a `no` path and a placeholder (a Windows install under OneDrive, say),
the filter lists "wowlab won't touch" twice, one choice showing `no` paths and
the other showing placeholders, and the player cannot tell which is which.

Cases:
- control: the capture as it is lists each choice's words once and includes
  "wowlab won't touch" (so the capture has a `no` path);
- the probe: with Config.wtf's verdict swapped for Python's placeholder
  verdict, the choices' words are still each listed once (fails at 85634bc).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_ui_explorer as first_stage
import test_ui_explorer_stage2 as second_stage
from test_ui_explorer import FLAVOR, _drive, _scenario

from wowlab_core import verdict as verdict_module

home = first_stage.home
install = first_stage.install
server = first_stage.server

REL = "WTF/Config.wtf"


def _texts(out: dict[str, Any]) -> list[str]:
    texts: list[str] = out["first"]["levelTexts"]
    return texts[1:]  # after "All"


def test_positive_control_each_choice_is_worded_once_constructed(
    server: Any, install: Path, tmp_path: Path
) -> None:
    scenario, _ = _scenario(server, [], [])
    texts = _texts(_drive(tmp_path / "node", scenario))
    assert verdict_module.LABELS["no"] in texts, texts
    assert len(texts) == len(set(texts)), texts


def test_a_placeholder_choice_is_not_worded_like_no_constructed(
    server: Any, install: Path, tmp_path: Path
) -> None:
    verdict = second_stage._placeholder(REL, "config-wtf")
    scenario, _ = _scenario(server, [], [])
    whole = json.loads(scenario["install_tree"])
    assert second_stage._swap_verdict(whole, (FLAVOR, REL), verdict) == 1
    scenario["install_tree"] = json.dumps(whole)
    out = _drive(tmp_path / "node", scenario)
    assert "placeholder" in out["first"]["levels"], out["first"]["levels"]
    texts = _texts(out)
    assert len(texts) == len(set(texts)), texts
