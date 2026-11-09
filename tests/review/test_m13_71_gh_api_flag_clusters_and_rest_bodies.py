# Probe from review of m13/71-guard-bash-graphql; reproduces gh api calls the endpoint finder misreads (round 3)
"""Constructed commands (hostile input), run through the real hook.

Third review round of M13-71. Each defect test is a strict xfail: delete its
marker line when the fix lands. The positive controls pass today.

gh 2.89.0, pointed at a local capture server, showed:
- `gh api -iH 'X-A: b' <url>/graphql -f query=...` sends `POST /graphql` with
  header X-A=b. pflag reads `-iH` as `-i` plus `-H`, and the next token is
  the header value, not the endpoint.
- `gh api <url>/repos/o/r/releases#graphql -f tag_name=v9` sends
  `POST /repos/o/r/releases {"tag_name":"v9"}`. The fragment is dropped.
- `gh api <url>/repos/o/r/merges?graphql -f base=main -f head=x` sends a
  POST to /repos/o/r/merges.
`origin/main` refuses every defect case below.
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


# Positive controls.


def test_control_a_rest_post_to_a_repo_endpoint_is_refused(tmp_path: Path) -> None:
    assert _run("gh api repos/o/r/releases -f tag_name=v9", tmp_path) == 2


def test_control_a_read_only_rest_call_passes(tmp_path: Path) -> None:
    assert _run("gh api repos/o/r/pulls --jq '.[].number'", tmp_path) == 0


def test_control_a_separate_method_flag_is_read(tmp_path: Path) -> None:
    assert _run(f"gh api -X POST graphql -f query={MUTATION}", tmp_path) == 2


# Defects.


@pytest.mark.xfail(
    strict=True, reason="M13-71 review round 3: a short-flag cluster hides the endpoint"
)
@pytest.mark.parametrize("cluster", ["-iX POST", "-iH 'Accept: x'", "-iq .data", "-ip x"])
def test_a_short_flag_cluster_ending_in_a_value_flag_is_parsed(
    tmp_path: Path, cluster: str
) -> None:
    assert _run(f"gh api {cluster} graphql -f query={MUTATION}", tmp_path) == 2


@pytest.mark.xfail(
    strict=True, reason="M13-71 review round 3: 'graphql' in a REST endpoint skips the body check"
)
@pytest.mark.parametrize(
    "command",
    [
        "gh api 'repos/o/r/releases#graphql' -f tag_name=v9",
        "gh api 'repos/o/r/git/refs#graphql' -f ref=refs/tags/v9 -f sha=abc",
        "gh api 'repos/o/r/merges?graphql' -f base=main -f head=x",
    ],
)
def test_a_rest_post_naming_graphql_is_still_refused(tmp_path: Path, command: str) -> None:
    assert _run(command, tmp_path) == 2


@pytest.mark.xfail(
    strict=True, reason="already on main: a short-flag cluster hides -X from the method check"
)
def test_a_mutating_method_in_a_short_flag_cluster_is_refused(tmp_path: Path) -> None:
    assert _run("gh api -iX PUT repos/o/r/pulls/1/merge", tmp_path) == 2
