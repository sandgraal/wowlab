"""The macOS and Windows Lab suites stay off the pull-request path (M18-15).

Owner decision, 2026-11-17: the desktop runs (about 18 minutes on macOS and
44 on Windows) run after each merge to main, nightly and by hand, never on a
pull request, and neither is a required check. Owner decision, 2026-11-18
(M15-21): not after each merge either, only nightly at 09:17 UTC and by hand.
Reads the workflow files and `.github/rulesets/main.json`; writes nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
DESKTOP = {"macos.yml": "lab (macos)", "windows.yml": "lab (windows)"}


def _triggers(name: str) -> dict[str, Any]:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as the boolean True.
    on = data.get("on", data.get(True))
    assert isinstance(on, dict), name
    return on


@pytest.mark.parametrize("name", sorted(DESKTOP))
def test_desktop_suite_runs_nightly_and_by_hand_only(name: str) -> None:
    on = _triggers(name)
    assert set(on) == {"schedule", "workflow_dispatch"}, name
    (entry,) = on["schedule"]
    # 09:17 UTC, 03:17 in the owner's local time (M15-21).
    assert entry["cron"] == "17 9 * * *", entry


@pytest.mark.parametrize("name", sorted(DESKTOP))
def test_the_nightly_run_has_its_own_concurrency_group(name: str) -> None:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    concurrency = data["concurrency"]
    assert "github.event_name == 'schedule'" in concurrency["group"], name
    assert "github.event_name != 'schedule'" in concurrency["cancel-in-progress"], name


def test_neither_desktop_job_is_a_required_check() -> None:
    ruleset = json.loads((ROOT / ".github" / "rulesets" / "main.json").read_text(encoding="utf-8"))
    contexts = {
        check["context"]
        for rule in ruleset["rules"]
        if rule["type"] == "required_status_checks"
        for check in rule["parameters"]["required_status_checks"]
    }
    assert not contexts & set(DESKTOP.values())
