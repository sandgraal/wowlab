"""The Commands tab and a failed run's `ErrorReport` (M16-22).

Under `--json`, a failed run prints an `ErrorReport {kind, message,
exit_code}` on stdout as well as its error line on stderr. The Commands tab
shows the error line and does not draw the report: it repeats that line and
names a class, which page copy never does. A run that exits non-zero with
a report of its own still has that report drawn.

The page runs under Node in the stand-in DOM of `test_ui_explorer.py`, with
the steps of `test_ui_focus_status.py`. The failed run is a real one: the
server runs `sv dump` of a file the captured install does not hold. The
partial report is held (constructed) and says so in the test id.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import test_ui_explorer as first_stage
import test_ui_focus_status as focus
from test_ui_explorer import _TEMPLATES, _node, _scenario

from wowlab_core import cli

home = first_stage.home
install = first_stage.install
server = first_stage.server

_RESULT = r"""
      } else if (step.result_view !== undefined) {
        out.steps.push({ stdout: ids.stdout.textContent, treeHidden: ids["result-tree"].hidden,
          rawHidden: ids["result-raw"].hidden, stderr: ids.stderr.textContent,
          status: ids.status.textContent });
"""


def _drive(folder: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    text = focus._harness()
    assert text.count(focus._STEP_ANCHOR) == 1
    text = text.replace(focus._STEP_ANCHOR, _RESULT.lstrip("\n") + focus._STEP_ANCHOR, 1)
    harness = folder / "m16-22-harness.js"
    harness.write_text(text, encoding="utf-8")
    data = folder / "m16-22-scenario.json"
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
    return out


def _held(reply: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"command": ["sv", "dump"], "reply": reply}]


def test_a_failed_runs_error_report_is_not_drawn(
    server: Any, install: Path, home: Path, tmp_path: Path
) -> None:
    reply = first_stage._run(server, ["sv", "dump"], {"file": "Nope-m16-22.lua"}).json()
    assert reply["exit_code"] == cli.EXIT_ERROR, reply
    report = cli.ErrorReport.model_validate_json(reply["stdout"])
    steps: list[dict[str, Any]] = [{"run_command": "sv dump"}, {"result_view": True}]
    scenario, _ = _scenario(server, [], steps)
    scenario["held"] = _held(reply)
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][1]
    assert view["status"] == "Exit code 1", view
    assert view["stdout"] == "" and view["treeHidden"] and view["rawHidden"], view
    assert report.kind not in json.dumps(view), view
    assert f"wowlab: {report.message}" in view["stderr"], view


def test_a_partial_report_is_still_drawn_constructed(
    server: Any, install: Path, home: Path, tmp_path: Path
) -> None:
    """A run that exits 1 with a report of its own: the report is drawn."""
    partial = json.dumps({"characters": [], "kind": "x"})
    reply = {
        "exit_code": 1,
        "timed_out": False,
        "truncated": False,
        "stdout": partial,
        "stderr": "wowlab: some characters could not be read\n",
    }
    steps: list[dict[str, Any]] = [{"run_command": "sv dump"}, {"result_view": True}]
    scenario, _ = _scenario(server, [], steps)
    scenario["held"] = _held(reply)
    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][1]
    assert view["stdout"] == partial and not view["treeHidden"], view
