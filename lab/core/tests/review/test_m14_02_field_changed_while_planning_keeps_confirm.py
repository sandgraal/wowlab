# Probe from review of m14/02-confirm-flow; reproduces a field changed while the plan runs keeping Confirm for the older plan
"""The page's comment (page.js, the `input`/`change` listener) says: "A field
changed after the plan: the plan shown is no longer the form's, so its
Confirm goes until the plan is shown again." The listener only clears a
Confirm that is already offered (`pending !== null`). A field changed while
the plan run is still in flight (doctor scans the whole install, so the run
takes seconds) is not seen: the reply then offers Confirm for the parameters
sent before the edit, while the form shows the new value. Confirm runs the
older plan (the server's argv binding holds, so nothing unshown runs), but
the form and the plan disagree, which the listener is there to prevent.
M14-03 reuses this page for `snap restore` and `profile apply`.

The harness is test_ui_confirm.py's, with one step kind added (constructed):
`late`, a field set after the click and before the reply is drawn.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_ui_confirm as tc

# The original loop fires `set` before `click`; `late` fires after the click
# without waiting for the reply.
_LOOP = '      if (step.click) ids[step.click].fire("click");\n'
_LATE = (
    _LOOP + "      if (step.late) { const f = field(step.late[0]); f.value = step.late[1];"
    ' f.fire("input"); }\n'
)


def _drive(tmp_path: Path, replies: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    assert tc._HARNESS.count(_LOOP) == 1
    original = tc._HARNESS
    tc._HARNESS = original.replace(_LOOP, _LATE)
    try:
        return tc._drive(tmp_path, replies, steps)
    finally:
        tc._HARNESS = original


def _replies() -> dict[str, Any]:
    report = {"first_run": {"outcome": "not_asked", "chosen": "_x_"}}
    return {
        "doctor": tc._result(report, tc._offer("tok-3", "Confirm takes a first snapshot of _x_.")),
        "doctor confirm": tc._result(
            {"first_run": {"outcome": "taken"}},
            {"stage": "confirmed", "token": None, "note": None, "needs": None},
        ),
    }


def test_positive_control_a_field_changed_after_the_plan_takes_confirm_away(
    tmp_path: Path,
) -> None:
    steps = [
        {"pick": "doctor"},
        {"set": ["flavor", "_x_"]},
        {"click": "run"},
        {"set": ["flavor", "_y_"]},
    ]
    out = _drive(tmp_path, _replies(), steps)
    assert out["steps"][-1]["confirmHidden"]


def test_a_field_changed_while_the_plan_runs_takes_confirm_away(tmp_path: Path) -> None:
    steps = [
        {"pick": "doctor"},
        {"set": ["flavor", "_x_"]},
        {"click": "run", "late": ["flavor", "_y_"]},
    ]
    out = _drive(tmp_path, _replies(), steps)
    # The form now says _y_; the plan shown is _x_'s. Confirm must not be offered.
    assert out["steps"][-1]["confirmHidden"], out["steps"][-1]
