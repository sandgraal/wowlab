# Probe from review of m13/65-ci-hygiene; reproduces a bare sha256sum check swallowed by `set +e` or an enclosing `{ } || true` passing the download rule.
"""After 8524994 every `sha256sum` line must be a bare
`echo "<digest>  <path>" | sha256sum -c -`, which catches `!` and `if !`.
The failure can still be swallowed from outside that line: `set +e` before
it, or a brace group, subshell or function around it whose status is tested
with `|| true` (bash does not apply -e inside a compound command whose
status is tested). Each script below unpacks an unchecked tarball and the
rule reports nothing. Constructed inputs.
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

SWALLOWED = {
    "constructed-set-plus-e": _FETCH + "set +e\n" + _CHECK + _USE,
    "constructed-brace-or-true": _FETCH + "{\n" + _CHECK + "} || true\n" + _USE,
    "constructed-subshell-or-true": _FETCH + "(\n" + _CHECK + ") || true\n" + _USE,
    "constructed-function-or-true": _FETCH + "chk() {\n" + _CHECK + "}\nchk || true\n" + _USE,
    # Positive control: the form the rule already catches.
    "constructed-or-true-control": _FETCH + _CHECK.rstrip("\n") + " || true\n" + _USE,
}


def _kinds(script: str) -> set[str]:
    workflow = rule._ci()
    rule._actionlint_step(workflow)["run"] = script
    return {kind for _, kind in rule._findings(workflow)}


def test_constructed_plain_check_is_clean() -> None:
    assert _kinds(_FETCH + _CHECK + _USE) == set()


@pytest.mark.parametrize("case", sorted(SWALLOWED))
def test_a_swallowed_check_is_reported(case: str) -> None:
    assert _kinds(SWALLOWED[case]), case
