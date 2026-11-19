"""A push to main always runs the whole `quality` job; a pull request may be
narrowed (M15-21, security review of #609).

With superseded runs on main cancelled, a docs-only merge landing seconds
after a code merge cancels the code merge's run. Its own filter compares only
the two commits, so if a push could take the docs-only path, or skip the
lab-addon mutation probes, the code merge's tree would never be graded. The
`changes` job's two outputs therefore narrow a pull request only.

This reads `ci.yml` as data and evaluates the outputs' and the steps'
`${{ }}` expressions with a small evaluator of the subset they use (`==`,
`!=`, `&&`, `||`, parentheses, string literals and context references), for
the five cases that matter. Nothing is executed; writes nothing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TOKEN = re.compile(r"\s*(\(|\)|&&|\|\||==|!=|'[^']*'|[A-Za-z_][\w.-]*)")


def _tokens(text: str) -> list[str]:
    text = text.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2]
    out, pos = [], 0
    while pos < len(text.rstrip()):
        match = TOKEN.match(text, pos)
        assert match, f"unsupported expression at {text[pos:]!r}"
        out.append(match.group(1))
        pos = match.end()
    return out


def evaluate(text: str, context: dict[str, str]) -> bool:
    """Evaluate `text` with every reference looked up in `context` (missing:
    the empty string, as GitHub gives for an unset output)."""
    tokens = _tokens(text)
    pos = 0

    def value() -> Any:
        nonlocal pos
        token = tokens[pos]
        pos += 1
        if token == "(":
            result = disjunction()
            assert tokens[pos] == ")"
            pos += 1
            return result
        if token.startswith("'"):
            return token[1:-1]
        return context.get(token, "")

    def comparison() -> Any:
        nonlocal pos
        left = value()
        if pos < len(tokens) and tokens[pos] in ("==", "!="):
            op = tokens[pos]
            pos += 1
            right = value()
            return (left == right) if op == "==" else (left != right)
        return left

    def conjunction() -> Any:
        nonlocal pos
        result = comparison()
        while pos < len(tokens) and tokens[pos] == "&&":
            pos += 1
            right = comparison()
            result = result and right
        return result

    def disjunction() -> Any:
        nonlocal pos
        result = conjunction()
        while pos < len(tokens) and tokens[pos] == "||":
            pos += 1
            right = conjunction()
            result = result or right
        return result

    result = disjunction()
    assert pos == len(tokens), tokens[pos:]
    return bool(result)


def _workflow() -> dict[str, Any]:
    data = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
    assert isinstance(data, dict)
    return data


def _outputs(event: str, filters: dict[str, str]) -> dict[str, str]:
    outputs = _workflow()["jobs"]["changes"]["outputs"]
    context = {"github.event_name": event}
    context.update({f"steps.filter.outputs.{k}": v for k, v in filters.items()})
    return {name: str(evaluate(expr, context)).lower() for name, expr in outputs.items()}


def _steps_run(result: str, outputs: dict[str, str]) -> set[str]:
    context = {"needs.changes.result": result}
    context.update({f"needs.changes.outputs.{k}": v for k, v in outputs.items()})
    steps = _workflow()["jobs"]["quality"]["steps"]
    return {s["name"] for s in steps if "name" in s and evaluate(s.get("if", "true"), context)}


def _filters(*, code: bool, doc_tests: bool = False, addon: bool = False) -> dict[str, str]:
    return {
        "non_docs": str(code).lower(),
        "doc_tests": str(doc_tests).lower(),
        "addon_probes": str(addon).lower(),
    }


FULL = "pytest (parallel)"
WITHOUT = "pytest (parallel, lab-addon mutation probes skipped)"
GATE = "pytest (the write gate's serial tests)"
BIDI = "Doc-only PR — bidi controls"

# (case, event, the changes job's result, the filter's outputs) -> what runs.
CASES = [
    ("pr-code-no-addon", "pull_request", "success", _filters(code=True), {WITHOUT, GATE}),
    ("pr-addon", "pull_request", "success", _filters(code=True, addon=True), {FULL, GATE}),
    ("pr-docs-only", "pull_request", "success", _filters(code=False), {BIDI}),
    ("push-docs-only-diff", "push", "success", _filters(code=False), {FULL, GATE}),
    ("push-code-diff", "push", "success", _filters(code=True), {FULL, GATE}),
    ("changes-failed", "pull_request", "failure", {}, {FULL, GATE}),
]


@pytest.mark.parametrize(
    ("event", "result", "filters", "expected"),
    [pytest.param(*case[1:], id=case[0]) for case in CASES],
)
def test_what_quality_runs(
    event: str, result: str, filters: dict[str, str], expected: set[str]
) -> None:
    outputs = _outputs(event, filters) if result == "success" else {}
    ran = _steps_run(result, outputs) & {FULL, WITHOUT, GATE, BIDI}
    assert ran == expected


@pytest.mark.parametrize("docs", [True, False])
@pytest.mark.parametrize("addon", [True, False])
def test_a_push_is_never_docs_only_and_always_runs_the_probes(docs: bool, addon: bool) -> None:
    outputs = _outputs("push", _filters(code=not docs, addon=addon))
    assert outputs == {"docs_only": "false", "addon_probes": "true"}


def test_the_evaluator_reads_precedence_and_parentheses() -> None:
    """Positive control for the evaluator itself: `&&` binds tighter than `||`."""
    ctx = {"a": "x", "b": "y"}
    assert evaluate("a == 'x' || a == 'z' && b == 'z'", ctx)
    assert not evaluate("(a == 'x' || a == 'z') && b == 'z'", ctx)
    assert evaluate("${{ missing != 'true' }}", ctx)
