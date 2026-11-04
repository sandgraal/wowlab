# Probe from review of m13/65-ci-hygiene; reproduces a fetching step on a Windows runner with no `shell:` passing as `bash -e`, though GitHub runs it under pwsh.
"""`_shell_has_e(None)` is True because "GitHub then runs `bash -e {0}`".
That is GitHub's default on Linux and macOS runners only. On a Windows
runner an unset `shell:` means `pwsh`, which does not stop when a native
command such as `sha256sum -c -` exits non-zero, so the next line unpacks
the unchecked file. windows.yml's `lab-windows` job runs on
`windows-latest` with no `defaults.run.shell`; a download step added there
passes the rule. Constructed input, applied to the real windows.yml in
memory; no workflow file is edited.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests" / "repo"))
import test_workflow_tool_downloads as rule

_STEP = {
    "name": "Install actionlint",
    "env": {"ACTIONLINT_VERSION": "1.7.12"},
    "run": (
        f"curl -sSfL -o /tmp/a.tar.gz {rule._URL}\n"
        f'echo "{rule._SUM}  /tmp/a.tar.gz" | sha256sum -c -\n'
        "tar -xzf /tmp/a.tar.gz -C /tmp actionlint\n"
    ),
}


def _windows_with(step: dict[str, object]) -> dict[str, object]:
    workflow = copy.deepcopy(rule._workflows()["windows.yml"])
    job = workflow["jobs"]["lab-windows"]
    assert "windows" in str(job["runs-on"])
    job["steps"].append(copy.deepcopy(step))
    return workflow


def _kinds(workflow: dict[str, object]) -> set[str]:
    return {kind for _, kind in rule._findings(workflow)}


def test_constructed_windows_step_with_bash_is_accepted() -> None:
    # Positive control: an explicit `shell: bash` is Git Bash with -eo pipefail.
    assert _kinds(_windows_with({**_STEP, "shell": "bash"})) == set()


def test_constructed_windows_step_with_default_shell_is_reported() -> None:
    assert "shell-without-e" in _kinds(_windows_with(_STEP))
