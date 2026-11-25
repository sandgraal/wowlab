"""The Practice tab under Node (M22-20, fix round 1): a log that is gone, and
a new flavor.

Driven with the stand-in DOM and the flavor picker's two-flavor install of
`test_ui_flavor_picker.py` (the capture plus a constructed `_alpha_` with
no character). The `log practice` replies are constructed: what the page
does with a report and with an error is graded here, and what the command
reports is graded in `test_practice.py`. Nothing reads or writes a real
install.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import test_ui_flavor_picker as picker
from test_ui_flavor_picker import EMPTY, FLAVOR

home = picker.home
install = picker.install
server = picker.server
timed = picker.timed
two = picker.two
no_env_flavor = picker.no_env_flavor

_VIEW = r"""
function practiceView() {
  return {
    hidden: ids["practice-view"].hidden,
    status: ids["practice-status"].textContent,
    statusRole: ids["practice-status"].getAttribute("role"),
    logs: Array.from(ids["practice-log"].options, (o) => o.value),
    picked: ids["practice-log"].value,
    segments: ids["practice-segments"].all().filter((e) => e.tagName === "td").map((e) => e.textContent),
    said: ids["practice-segments"].all().filter((e) => e.tagName === "p").map((e) => e.textContent),
    summaryHidden: ids["practice-summary"].hidden,
  };
}
"""

_STEPS = r"""
      } else if (step.practice_tab !== undefined) {
        const before = requests.length;
        ids["tab-practice"].fire("click"); await tick(); await tick(); await tick();
        out.steps.push(Object.assign(practiceView(), { newRequests: requests.slice(before) }));
      } else if (step.practice_pick !== undefined) {
        // The picker holds a log the client has since removed.
        const before = requests.length;
        ids["practice-log"].value = step.practice_pick;
        ids["practice-read"].fire("click"); await tick(); await tick(); await tick(); await tick();
        out.steps.push(Object.assign(practiceView(), { newRequests: requests.slice(before) }));
      } else if (step.practice_flavor !== undefined) {
        const before = requests.length;
        ids["run-flavor"].value = step.practice_flavor; ids["run-flavor"].fire("change");
        await tick(); await tick(); await tick();
        out.steps.push(Object.assign(practiceView(), { newRequests: requests.slice(before) }));
"""

_ANCHOR = "      } else if (step.preview !== undefined) {\n"
_VIEW_ANCHOR = "vm.createContext(ctx);\n"


def _harness() -> str:
    text = picker._CHAR_HARNESS()
    assert text.count(_ANCHOR) == 1 and text.count(_VIEW_ANCHOR) == 1
    text = text.replace(_VIEW_ANCHOR, _VIEW + _VIEW_ANCHOR, 1)
    return text.replace(_ANCHOR, _STEPS.lstrip("\n") + _ANCHOR, 1)


def _report(logs: list[str], segments: int) -> str:
    """A constructed `PracticeReport`."""
    return json.dumps(
        {
            "file": f"Logs/{logs[-1]}" if logs else None,
            "logs": logs,
            "gap": 10.0,
            "segments": [
                {
                    "index": i + 1,
                    "guid": "Player-1-0000000A",
                    "name": "Me-Realm-",
                    "first_line": 2,
                    "last_line": 9,
                    "start": "1/2/2026 10:00:10.000-4",
                    "end": "1/2/2026 10:00:20.000-4",
                    "seconds": 10.0,
                    "casts": 3,
                    "damage": 120,
                    "targets": ["Training Dummy"],
                }
                for i in range(segments)
            ],
            "segments_left_out": 0,
            "summary": None,
            "timings": "table",
            "timing_build": "1.60.1.70009",
            "fetch": None,
            "notes": [],
        }
    )


_OLD = "WoWCombatLog-010226_100000.txt"
_NEW = "WoWCombatLog-010226_110000.txt"
_GONE = "WoWCombatLog-010126_090000.txt"
_ODD = "WoWCombatLog-010126_080000.txt"
_ODD_ERROR = f"wowlab: Logs/{_ODD} is not a regular file"


def _drive(server: Any, folder: Path, steps: list[dict[str, Any]]) -> dict[str, Any]:
    scenario = picker._scenario(server, steps, [FLAVOR, EMPTY])
    scenario["runs"] += [
        {
            "command": ["log", "practice"],
            "params": {"flavor": FLAVOR},
            "stdout": _report([_OLD, _NEW], 1),
            "exit_code": 0,
        },
        {
            "command": ["log", "practice"],
            "params": {"flavor": FLAVOR, "file": _GONE},
            "stdout": "",
            "stderr": f"wowlab: no combat log '{_GONE}' under Logs/",
            "exit_code": 2,
        },
        {
            "command": ["log", "practice"],
            "params": {"flavor": FLAVOR, "file": _ODD},
            "stdout": "",
            "stderr": _ODD_ERROR,
            "exit_code": 1,
        },
        {
            "command": ["log", "practice"],
            "params": {"flavor": EMPTY},
            "stdout": _report([], 0),
            "exit_code": 0,
        },
    ]
    original = picker.char_tab._harness
    picker.char_tab._harness = _harness
    try:
        return picker.char_tab._drive(folder, scenario)
    finally:
        picker.char_tab._harness = original


def _bodies(step: dict[str, Any]) -> list[dict[str, Any]]:
    return [r["body"] for r in step["newRequests"]]


def test_a_log_that_is_gone_is_dropped_and_the_newest_read_constructed(
    server: Any, two: Path, tmp_path: Path
) -> None:
    out = _drive(server, tmp_path / "node", [{"practice_tab": True}, {"practice_pick": _GONE}])
    opened, picked = out["steps"]
    assert _bodies(opened) == [{"command": ["log", "practice"], "params": {"flavor": FLAVOR}}]
    assert opened["logs"] == [_NEW, _OLD]
    # The pick fails once, is dropped, and the newest is read: the picker
    # holds the logs again and no error stays.
    assert _bodies(picked) == [
        {"command": ["log", "practice"], "params": {"flavor": FLAVOR, "file": _GONE}},
        {"command": ["log", "practice"], "params": {"flavor": FLAVOR}},
    ]
    assert picked["logs"] == [_NEW, _OLD]
    assert picked["status"] == "" and picked["statusRole"] == "status"
    assert "Training Dummy" in picked["segments"]


def test_another_failure_keeps_the_pick_and_says_why_constructed(
    server: Any, two: Path, tmp_path: Path
) -> None:
    """Only the command's "no combat log" refusal drops a pick: a log that
    fails for any other reason (here one that is not a regular file) stays
    picked, nothing else is read, and the error is said."""
    out = _drive(server, tmp_path / "node", [{"practice_tab": True}, {"practice_pick": _ODD}])
    _, picked = out["steps"]
    assert _bodies(picked) == [
        {"command": ["log", "practice"], "params": {"flavor": FLAVOR, "file": _ODD}}
    ]
    assert picked["picked"] == _ODD
    assert picked["status"] == _ODD_ERROR and picked["statusRole"] == "alert"
    # What the last good read drew stays.
    assert "Training Dummy" in picked["segments"]


def test_a_new_flavor_reads_its_own_logs_constructed(
    server: Any, two: Path, tmp_path: Path
) -> None:
    out = _drive(server, tmp_path / "node", [{"practice_tab": True}, {"practice_flavor": EMPTY}])
    opened, switched = out["steps"]
    assert "Training Dummy" in opened["segments"]
    assert {"command": ["log", "practice"], "params": {"flavor": EMPTY}} in _bodies(switched)
    # Nothing of the first flavor's log stays.
    assert switched["logs"] == []
    assert switched["segments"] == []
    assert switched["said"] == ["No combat log yet."]
    assert switched["summaryHidden"] is True
