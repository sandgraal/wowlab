# Probe from review of m13/71-guard-bash-graphql; reproduces a GraphQL endpoint spelling that skips the mutation check
"""Constructed commands (hostile input), run through the real hook.

Second review round of M13-71. Each defect test is a strict xfail: delete its
marker line when the fix lands. The positive control passes today.

gh 2.89.0, pointed at a local capture server, sends
`POST /graphql {"query": ...}` for an endpoint `http://127.0.0.1:<port>/graphql#x`:
the fragment is dropped before the request. A shell variable is expanded by
the shell before gh sees it.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".claude" / "hooks" / "guard_bash.py"

MUTATION = shlex.quote('mutation{mergePullRequest(input:{pullRequestId:"P"}){clientMutationId}}')


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


def test_control_the_plain_endpoint_is_judged(tmp_path: Path) -> None:
    assert _run(f"gh api graphql -f query={MUTATION}", tmp_path) == 2


@pytest.mark.parametrize(
    "endpoint", ["graphql#x", "/graphql#x", "https://api.github.com/graphql#x"]
)
def test_an_endpoint_with_a_fragment_is_judged(tmp_path: Path, endpoint: str) -> None:
    assert _run(f"gh api {shlex.quote(endpoint)} -f query={MUTATION}", tmp_path) == 2


@pytest.mark.parametrize("command", ["E=graphql; gh api $E", "gh api ${E:-graphql}"])
def test_an_endpoint_from_a_shell_variable_is_judged(tmp_path: Path, command: str) -> None:
    assert _run(f"{command} -f query={MUTATION}", tmp_path) == 2
