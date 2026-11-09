# Probe from review of m13/71-guard-bash-graphql; reproduces `-X=PUT` and `-F=query=@file` read with a stray "=" (round 4)
"""Constructed commands (hostile input), run through the real hook.

Fourth review round of M13-71. Each defect test is a strict xfail: delete its
marker when the fix lands. The positive controls pass today.

pflag reads a short flag written `-X=VALUE` as the value `VALUE`, and so does
the last letter of a cluster (`-iX=VALUE`). gh 2.89.0, pointed at a local
capture server, sent:
- `PUT /repos/o/r/pulls/1/merge` for `gh api -X=PUT <url>/repos/o/r/pulls/1/merge`;
- `DELETE /repos/o/r/git/refs/heads/x` for `gh api -iX=DELETE <url>/...`;
- `POST /graphql {"query":"mutation{mergePullRequest...` for
  `gh api <url>/graphql -F=query=@<file>`.
`_parse_gh_api` keeps the "=", so it sees the method `=PUT` and the field
`=query=@<file>`. `origin/main` has the same gap.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".claude" / "hooks" / "guard_bash.py"

MUTATION = 'mutation{mergePullRequest(input:{pullRequestId:"P"}){clientMutationId}}'


def _run(command: str, cwd: Path) -> int:
    env = dict(os.environ)
    env.setdefault("CLAUDE_PROJECT_DIR", str(REPO))
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    ).returncode


def _query_file(tmp_path: Path) -> Path:
    path = tmp_path / "q.graphql"
    path.write_text(MUTATION, encoding="utf-8")
    return path


# Positive controls.


def test_control_a_spaced_method_is_refused(tmp_path: Path) -> None:
    assert _run("gh api -X PUT repos/o/r/pulls/1/merge", tmp_path) == 2


def test_control_a_spaced_query_file_is_judged(tmp_path: Path) -> None:
    assert _run(f"gh api graphql -F query=@{_query_file(tmp_path)}", tmp_path) == 2


# Defects.


@pytest.mark.parametrize(
    "command",
    ["gh api -X=PUT repos/o/r/pulls/1/merge", "gh api -iX=DELETE repos/o/r/git/refs/heads/x"],
)
def test_a_short_method_written_with_equals_is_refused(tmp_path: Path, command: str) -> None:
    assert _run(command, tmp_path) == 2


@pytest.mark.parametrize("flag", ["-F=", "-iF="])
def test_a_short_field_written_with_equals_is_judged(tmp_path: Path, flag: str) -> None:
    assert _run(f"gh api graphql {flag}query=@{_query_file(tmp_path)}", tmp_path) == 2
