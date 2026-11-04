# Probe from review of m13/65-ci-hygiene; reproduces a sha256sum check negated with `!` or `if !` passing the download rule.
"""tests/repo/test_workflow_tool_downloads.py promises "never swallow the
check", and catches `|| true` and `; true`. A check negated with `!` or
wrapped in `if ! ...; then ...; fi` is swallowed just as surely (bash -e does
not exit on a negated pipeline, nor on an `if` condition), and the rule
reports nothing. Constructed inputs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests" / "repo"))
import test_workflow_tool_downloads as rule

_FETCH = f"curl -sSfL -o /tmp/a.tar.gz {rule._URL}\n"
_CHECK = f'echo "{rule._SUM}  /tmp/a.tar.gz" | sha256sum -c -'
_USE = "\ntar -xzf /tmp/a.tar.gz -C /tmp actionlint\n"

SWALLOWED = {
    "constructed-bang": _FETCH + "! " + _CHECK + _USE,
    "constructed-if-not": _FETCH + "if ! " + _CHECK + "; then echo skipped; fi" + _USE,
    # Positive control: the form the rule already catches.
    "constructed-or-true-control": _FETCH + _CHECK + " || true" + _USE,
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
