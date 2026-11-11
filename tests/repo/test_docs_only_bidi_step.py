"""A docs-only PR still runs the bidi scan (M16-89).

`tests/repo/test_bidi_controls.py` reads every tracked file, docs included.
A docs-only PR skips the `quality` job's suite, so the job runs that one file
on its own when the suite is skipped. The steps it needs (uv and the install)
run on every PR. This reads `ci.yml` as data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCS_ONLY = "needs.changes.result == 'success' && needs.changes.outputs.docs_only == 'true'"
FULL = "needs.changes.result != 'success' || needs.changes.outputs.docs_only != 'true'"
BIDI_RUN = "uv run --frozen pytest -q tests/repo/test_bidi_controls.py"


def _quality() -> list[dict[str, Any]]:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
    steps: list[dict[str, Any]] = workflow["jobs"]["quality"]["steps"]
    return steps


def test_the_docs_only_branch_runs_the_bidi_scan() -> None:
    steps = _quality()
    bidi = [s for s in steps if "test_bidi_controls.py" in str(s.get("run", ""))]
    assert len(bidi) == 1, bidi
    (step,) = bidi
    assert step["run"].strip() == BIDI_RUN
    assert step["if"] == DOCS_ONLY
    assert not step.get("continue-on-error")


def test_what_the_bidi_step_needs_runs_on_every_pr() -> None:
    steps = _quality()
    bidi = next(i for i, s in enumerate(steps) if "test_bidi_controls.py" in str(s.get("run", "")))
    setup = [i for i, s in enumerate(steps) if "setup-uv" in str(s.get("uses", ""))]
    install = [i for i, s in enumerate(steps) if s.get("name") == "Install"]
    assert len(setup) == 1 and len(install) == 1
    for i in (*setup, *install):
        assert "if" not in steps[i], steps[i]
        assert i < bidi
    # Every other step runs either on the full branch or on the docs-only one.
    for i, step in enumerate(steps):
        if i in (*setup, *install) or "actions/checkout" in str(step.get("uses", "")):
            continue
        assert step.get("if") in (FULL, DOCS_ONLY), step
