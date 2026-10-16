# Probe from review of m13/27t-explorer-stage2-tests; reproduces the explorer's
# Node harness having no setTimeout, so a page correct in every browser fails.
"""CONSTRUCTED stand-in page.js (L8: our own seam, not an external format).

`tests/test_ui_explorer.py::_HARNESS` runs page.js in a `vm` context whose
globals are only `window`, `document`, `navigator`, `URLSearchParams`,
`fetch`, `console`, `Set`, `Map`, `Promise`, `JSON` and `Error`. `setTimeout`
and `clearTimeout` are Node host functions, not language intrinsics, so the
context has neither. Every browser has them, and the M13-27 graders in
`test_ui_explorer_stage2.py` (copy buttons, Preview) invite exactly the code
that uses one: a "Copied" note that clears itself, a debounced text filter.
Such a page throws a ReferenceError under the harness and every grader that
drives it fails for a reason the spec and the seams never state.

Cases:
- control: a page that draws a status text and uses no timer runs clean;
- the probe: the same page, which also clears a note with `setTimeout`,
  runs clean too (fails today: `setTimeout is not defined`).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

# The first stage's grader file, loaded under a name of its own so this probe
# does not depend on `tests/` being on sys.path.
_SPEC = importlib.util.spec_from_file_location(
    "_m13_27t_probe_first_stage", Path(__file__).resolve().parents[1] / "test_ui_explorer.py"
)
assert _SPEC and _SPEC.loader
first_stage = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(first_stage)

_CONTROL = 'document.getElementById("status").textContent = "ready";\n'
_WITH_TIMER = _CONTROL + "const t = setTimeout(() => {}, 0); clearTimeout(t);\n"


def _run(tmp_path: Path, page: str) -> dict[str, Any]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    harness = tmp_path / "harness.js"
    harness.write_text(first_stage._HARNESS, encoding="utf-8")
    script = tmp_path / "page.js"
    script.write_text(page, encoding="utf-8")
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps({"ids": first_stage._page_ids(), "sep": "/", "commands": {}, "steps": []}),
        encoding="utf-8",
    )
    done = subprocess.run(
        [first_stage._node(), str(harness), str(script), str(scenario)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout)
    return out


def test_control_a_page_without_a_timer_runs_clean(tmp_path: Path) -> None:
    out = _run(tmp_path / "control", _CONTROL)
    assert "error" not in out, out.get("error")


def test_a_page_that_uses_set_timeout_runs_clean(tmp_path: Path) -> None:
    out = _run(tmp_path / "probe", _WITH_TIMER)
    assert "error" not in out, out.get("error")
