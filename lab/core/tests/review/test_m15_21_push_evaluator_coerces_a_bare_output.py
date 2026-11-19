# Probe from review of m15/21-test-time; reproduces the ci.yml expression evaluator turning a bare output reference "false" into "true"
"""`tests/repo/test_push_runs_everything.py` evaluates the `changes` job's
outputs with `str(evaluate(expr, context)).lower()`. `evaluate` returns
`bool(result)`, so an output written as a bare reference, such as the
pre-fix `${{ steps.filter.outputs.addon_probes }}`, becomes `"true"` when
the filter gave `"false"`. GitHub keeps the string `"false"` there.

Effect: put the old `addon_probes` expression back in ci.yml (a push to
main with no addon change then skips the probes, the gap the security
review found) and every push case of that test still passes; only
`pr-code-no-addon` fails, and for the wrong reason (it claims the full pass
runs on that pull request). Run in review against a copy of ci.yml with only
that line reverted: 1 failed (pr-code-no-addon), 10 passed.

Constructed inputs (expression strings); reads the test module, writes
nothing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[4]
MODULE = ROOT / "tests" / "repo" / "test_push_runs_everything.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("m15_21_push_evaluator", MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_positive_control_a_comparison_output_is_false() -> None:
    evaluate = _module().evaluate
    out = str(evaluate("${{ x == 'true' }}", {"x": "false"})).lower()
    assert out == "false"


def test_constructed_a_bare_reference_output_keeps_its_string() -> None:
    # What GitHub gives for `addon_probes: ${{ steps.filter.outputs.addon_probes }}`
    # when the filter did not match: the string "false".
    evaluate = _module().evaluate
    out = str(evaluate("${{ x }}", {"x": "false"})).lower()
    assert out == "false"
