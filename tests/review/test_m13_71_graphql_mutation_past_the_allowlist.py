# Probe from review of m13/71-guard-bash-graphql; reproduces a non-allowlisted GraphQL mutation passing guard_bash.py
"""Constructed GraphQL documents (hostile input), run through the real hook.

Each defect test is a strict xfail: delete its marker line when the fix lands.
The positive controls pass today and must keep passing.
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

MERGE = 'mergePullRequest(input:{pullRequestId:"P"}){clientMutationId}'
RESOLVE = 'resolveReviewThread(input:{threadId:"T"}){thread{id}}'


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


def _input_body(tmp_path: Path, query: str) -> str:
    body = tmp_path / "body.json"
    body.write_text(json.dumps({"query": query}), encoding="utf-8")
    return f"gh api graphql --input {body}"


# Positive controls.


def test_control_plain_mutation_is_blocked(tmp_path: Path) -> None:
    assert _run(_input_body(tmp_path, "mutation{" + MERGE + "}"), tmp_path) == 2


def test_control_allowed_mutation_passes(tmp_path: Path) -> None:
    assert _run(_input_body(tmp_path, "mutation{" + RESOLVE + "}"), tmp_path) == 0


# Defects.


@pytest.mark.xfail(strict=True, reason="M13-71 review: a lone CR ends a GraphQL comment")
def test_a_comment_ended_by_a_lone_cr_hides_a_mutation_field(tmp_path: Path) -> None:
    # GraphQL's LineTerminator includes a lone "\r"; _strip_graphql ends a
    # comment only at "\n", so mergePullRequest is blanked as comment text.
    query = "mutation{" + RESOLVE + " # x\r" + MERGE + "\n}"
    assert _run(_input_body(tmp_path, query), tmp_path) == 2


@pytest.mark.xfail(strict=True, reason='M13-71 review: \\""" does not end a block string')
def test_an_escaped_triple_quote_desyncs_the_block_string_scan(tmp_path: Path) -> None:
    # In a GraphQL block string \""" is an escaped quote, not the end.
    # _strip_graphql ends the block there, so its later quotes pair up out of
    # step with the real lexer and the mergePullRequest line is blanked.
    query = (
        'mutation{a: resolveReviewThread(input:{threadId:"""x\\"""y"""}){thread{id}}\n'
        + MERGE
        + '\n# """}){thread{id}}\n}'
    )
    assert _run(_input_body(tmp_path, query), tmp_path) == 2
    qfile = tmp_path / "q.graphql"
    qfile.write_text(query, encoding="utf-8")
    assert _run(f"gh api graphql -F query=@{qfile}", tmp_path) == 2


@pytest.mark.xfail(
    strict=True, reason="M13-71 review: only the literal endpoint `graphql` is judged"
)
@pytest.mark.parametrize("endpoint", ["/graphql", "https://api.github.com/graphql"])
def test_another_spelling_of_the_graphql_endpoint_is_judged(tmp_path: Path, endpoint: str) -> None:
    # gh sends {"query": ...} as a POST body to either spelling.
    command = f"gh api {endpoint} -f query={shlex.quote('mutation{' + MERGE + '}')}"
    assert _run(command, tmp_path) == 2
