# Probe from review of m13/65-ci-hygiene; reproduces a bare sha256sum check swallowed by `set +e` or an enclosing `{ } || true` passing the download rule.
"""After 8524994 every `sha256sum` line must be a bare
`echo "<digest>  <path>" | sha256sum -c -`, which catches `!` and `if !`.
The failure can still be swallowed from outside that line: `set +e` before
it, or a brace group, subshell or function around it whose status is tested
with `|| true` (bash does not apply -e inside a compound command whose
status is tested). Each such script unpacks an unchecked tarball and the
rule reports nothing. Constructed inputs.

Conductor ruling (2026-11-04, M13-65): this is the rule's documented limit.
The rule stays line-level, its docstring names these four shell-level forms
as unseen, and no allowlist is added. The four cases are strict xfail, so
they fail as XPASS, and the mark comes off, if the rule ever grows to catch
them. Replaces test_m13_65_check_swallowed_by_enclosing_shell.py (1d641c9),
the same cases without the marks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests" / "repo"))
import test_workflow_tool_downloads as rule

_FETCH = f"curl -sSfL -o /tmp/a.tar.gz {rule._URL}\n"
_CHECK = f'echo "{rule._SUM}  /tmp/a.tar.gz" | sha256sum -c -\n'
_USE = "tar -xzf /tmp/a.tar.gz -C /tmp actionlint\n"

_LIMIT = pytest.mark.xfail(
    strict=True,
    reason=(
        "documented limit of the line-level download rule (M13-65 ruling, "
        "2026-11-04): a check swallowed by the shell around it is not seen"
    ),
)

SWALLOWED = [
    pytest.param(_FETCH + "set +e\n" + _CHECK + _USE, id="constructed-set-plus-e", marks=_LIMIT),
    pytest.param(
        _FETCH + "{\n" + _CHECK + "} || true\n" + _USE,
        id="constructed-brace-or-true",
        marks=_LIMIT,
    ),
    pytest.param(
        _FETCH + "(\n" + _CHECK + ") || true\n" + _USE,
        id="constructed-subshell-or-true",
        marks=_LIMIT,
    ),
    pytest.param(
        _FETCH + "chk() {\n" + _CHECK + "}\nchk || true\n" + _USE,
        id="constructed-function-or-true",
        marks=_LIMIT,
    ),
    # Positive control: the form the rule already catches.
    pytest.param(
        _FETCH + _CHECK.rstrip("\n") + " || true\n" + _USE,
        id="constructed-or-true-control",
    ),
]


def _kinds(script: str) -> set[str]:
    workflow = rule._ci()
    rule._actionlint_step(workflow)["run"] = script
    return {kind for _, kind in rule._findings(workflow)}


def test_constructed_plain_check_is_clean() -> None:
    assert _kinds(_FETCH + _CHECK + _USE) == set()


@pytest.mark.parametrize("script", SWALLOWED)
def test_a_swallowed_check_is_reported(script: str) -> None:
    assert _kinds(script)
