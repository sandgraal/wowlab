# Probe from review of m13/25-explorer-first-stage; reproduces an explain reply for
# a path the player had already left filling the detail pane. Rewritten for the
# whole-install page by test-writer (M13-27): two picks, the first reply late.
"""A late `explain` reply is never drawn over a later pick (§15.6).

M13-25's page reloaded a one-folder tree when the flavor picker changed, and
the review found that a reply for a file of the old folder still filled the
detail pane. M13-27's page reads the whole install once (`tree --install
--explain --json`); the picker now opens a flavor folder in that tree and
reloads nothing, so the first probe's stand-in server (M13-25's element ids,
M13-25's order of requests) no longer described the page. The behaviour it
guarded still matters: the player can pick another path while `explain` for
the first is in flight, and the reply that arrives last must not describe a
path that is no longer selected. page.js keeps a sequence number
(`explainSeq`) that each pick bumps and each reply checks.

How it is driven: the Node harness and stand-in DOM of `test_ui_explorer.py`
(the one `test_ui_explorer_stage2.py` uses), fed the real CLI's JSON for the
captured tree copied into `tmp_path` and served by the real server. One
change to it, made here: `fetch` can hold the reply to the first `explain`
of a chosen path until a `release` step lets it through, so the order of
replies is the test's choice. What is held is the real reply; only its
timing is the test's. Nothing reads or writes a real install. Skipped when
`node` is not on PATH.

Each race is compared with the same pick made alone, without a hold: the
detail pane after the late reply arrives must be exactly the pane of the
second pick, and must not hold the first pick's file-map row. The positive
control releases the held reply before the second pick and gets the first
pick's pane, so the hold reaches the pane and lets a reply through.

The second flavor folder in the picker case is a copy of the capture's
(constructed, as in `test_ui_explorer_stage2.py`).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_ui_explorer as first_stage
from test_ui_explorer import (
    _HARNESS,
    _TEMPLATES,
    FLAVOR,
    SV_FILE,
    _node,
    _norm,
    _scenario,
    _stdout,
)

home = first_stage.home
install = first_stage.install
server = first_stage.server

CONFIG = "WTF/Config.wtf"
SECOND = (
    "_ptr_"  # a second flavor folder, a copy of the capture's (constructed); L6 lets tests name it
)

# The one change to the shared harness: a `fetch` that can hold the first
# `explain` of a path, and a `release` step that lets it through.
_HOLD = r"""
const held = [];
const holdPaths = new Set(scenario.hold || []);
const heldFetch = (url, opts) => {
  const reply = fetchStub(url, opts);
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  const isExplain = body && Array.isArray(body.command) && body.command.join(" ") === "explain";
  const path = isExplain && body.params ? norm(body.params.path) : null;
  if (path === null || !holdPaths.has(path)) return reply;
  holdPaths.delete(path);
  return new Promise((resolve) => held.push({ path: path, go: () => resolve(reply) }));
};
const navigator = {"""

_RELEASE = r"""      } else if (step.release !== undefined) {
        const at = held.findIndex((h) => h.path === step.release);
        if (at < 0) { out.steps.push({ missing: "held " + step.release }); continue; }
        held.splice(at, 1)[0].go();
        await tick();
        out.steps.push(detail());
      } else if (step.preview !== undefined) {"""

_EDITS = [
    ("const navigator = {", _HOLD),
    ("URLSearchParams, fetch: fetchStub,", "URLSearchParams, fetch: heldFetch,"),
    ("      } else if (step.preview !== undefined) {", _RELEASE),
]


def _harness() -> str:
    text = _HARNESS
    for anchor, replacement in _EDITS:
        # The shared harness changed under this probe: say so, never guess.
        assert text.count(anchor) == 1, f"test_ui_explorer._HARNESS lost {anchor!r}"
        text = text.replace(anchor, replacement)
    return text


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    harness = folder / "m13-27-held-harness.js"
    harness.write_text(_harness(), encoding="utf-8")
    data = folder / "m13-27-held-scenario.json"
    data.write_text(json.dumps(scenario), encoding="utf-8")
    done = subprocess.run(
        [_node(), str(harness), str(_TEMPLATES / "page.js"), str(data)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout)
    assert "error" not in out, out["error"]
    assert not out["traps"], out["traps"]
    for step in out["steps"]:
        assert "missing" not in step, step
    return out


def _root(scenario: dict[str, Any]) -> str:
    root: str = json.loads(scenario["install_tree"])["install_root"]
    return root


def _target(scenario: dict[str, Any], flavor: str, rel: str) -> str:
    """The key of the explain the page asks for: the absolute path of `rel`
    in a flavor folder, `/`-separated."""
    return _norm(_root(scenario) + os.sep + flavor + "/" + rel)


def _add_explain(server: Any, scenario: dict[str, Any], flavor: str, rel: str) -> None:
    """The real `explain --json` for `rel` in `flavor`, for a folder that
    `_scenario` does not cover (it explains in the first flavor folder only)."""
    target = _root(scenario) + os.sep + flavor + "/" + rel
    params = {"path": target, "root": _root(scenario), "flavor": flavor}
    scenario["explains"][_norm(target)] = _stdout(server, ["explain"], params)


def _row_id(scenario: dict[str, Any], flavor: str, rel: str) -> str:
    report = json.loads(scenario["explains"][_target(scenario, flavor, rel)])
    entry_id: str = report["result"]["entry"]["id"]
    return entry_id


def _alone(tmp_path: Path, scenario: dict[str, Any], *clicks: str) -> list[dict[str, Any]]:
    """The detail pane after each pick, with every reply on time."""
    solo = dict(scenario, hold=[], steps=[{"click": c} for c in clicks])
    panes: list[dict[str, Any]] = _drive(tmp_path, solo)["steps"]
    for pane in panes:
        assert pane["facts"], pane  # a reply was drawn, not a harness error
    return panes


@pytest.fixture
def two_flavors(install: Path) -> Path:
    """The capture with a second flavor folder, a copy of the first (constructed)."""
    shutil.copytree(install / FLAVOR, install / SECOND)
    return install


def test_positive_control_a_released_reply_fills_the_pane(
    server: Any, install: Path, tmp_path: Path
) -> None:
    """Held, then let through before the second pick: the first pick's pane
    is drawn, then the second's. The hold reaches the pane."""
    scenario, _ = _scenario(server, [CONFIG, SV_FILE], [])
    first, second = _alone(tmp_path / "alone", scenario, CONFIG, SV_FILE)
    assert _row_id(scenario, FLAVOR, CONFIG) != _row_id(scenario, FLAVOR, SV_FILE)
    held = _target(scenario, FLAVOR, CONFIG)
    scenario.update(hold=[held], steps=[{"click": CONFIG}, {"release": held}, {"click": SV_FILE}])
    asking, released, after = _drive(tmp_path / "race", scenario)["steps"]
    assert asking["facts"] == "", asking  # held: nothing drawn yet
    assert released == first
    assert after == second


def test_a_late_reply_is_not_drawn_over_the_next_pick(
    server: Any, install: Path, tmp_path: Path
) -> None:
    """Pick Config.wtf, then a SavedVariables file before Config.wtf's
    explain answers. When that reply comes, the pane still describes the
    SavedVariables file, exactly as if it had been picked alone."""
    scenario, _ = _scenario(server, [CONFIG, SV_FILE], [])
    _, second = _alone(tmp_path / "alone", scenario, CONFIG, SV_FILE)
    held = _target(scenario, FLAVOR, CONFIG)
    scenario.update(hold=[held], steps=[{"click": CONFIG}, {"click": SV_FILE}, {"release": held}])
    out = _drive(tmp_path / "race", scenario)
    asked = [
        r["body"]["params"]["path"]
        for r in out["requests"]
        if r["body"] and r["body"]["command"] == ["explain"]
    ]
    assert [_norm(p) for p in asked] == [held, _target(scenario, FLAVOR, SV_FILE)], asked
    _, picked, late = out["steps"]
    assert picked == second
    assert _row_id(scenario, FLAVOR, CONFIG) not in late["facts"], late
    assert late == second


def test_a_late_reply_is_not_drawn_after_the_picker_opens_another_folder_constructed(
    server: Any, two_flavors: Path, tmp_path: Path
) -> None:
    """The case the probe was first written for, on the whole-install page:
    Config.wtf picked in the first flavor folder, the picker opens the
    second, a SavedVariables file there is picked, and only then does
    Config.wtf's reply arrive. The pane describes the second folder's file."""
    scenario, _ = _scenario(server, [CONFIG], [])
    _add_explain(server, scenario, SECOND, SV_FILE)
    there = SECOND + "/" + SV_FILE
    [second] = _alone(tmp_path / "alone", scenario, there)
    assert _row_id(scenario, FLAVOR, CONFIG) != _row_id(scenario, SECOND, SV_FILE)
    held = _target(scenario, FLAVOR, CONFIG)
    steps = [{"click": CONFIG}, {"flavor": SECOND}, {"click": there}, {"release": held}]
    scenario.update(hold=[held], steps=steps)
    _, opened, picked, late = _drive(tmp_path / "race", scenario)["steps"]
    assert any(r["path"] == SECOND for r in opened["rows"]), "the picker opens the second folder"
    assert picked == second
    assert _row_id(scenario, FLAVOR, CONFIG) not in late["facts"], late
    assert late == second
