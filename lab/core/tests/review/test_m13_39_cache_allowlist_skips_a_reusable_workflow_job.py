# Probe from review of m13/39-improve-no-cache; reproduces the cache allowlist
# missing a job that calls a reusable workflow (job-level `uses:`, no steps).
"""CONSTRUCTED workflow edits (L8), boundary cases for the M13-39 grader.

`test_no_job_that_runs_or_follows_the_model_uses_a_cache` in
`tests/repo/test_wiki_daily_gates_job.py` says that only the actions in
`CACHE_REVIEWED`, at those commits, may be used anywhere in wiki-daily, so
that "a new action or a bump fails here until it is reviewed for caching".
It walks `body.get("steps", [])` of every job. A job that calls a reusable
workflow has a job-level `uses:` and no `steps`, so whatever that workflow
runs (setup-go, actions/cache, anything) is never checked, and a scheduled
run on main would give it the run's cache scope.

Each case copies the real workflow into `tmp_path`, makes one edit, points
the grader's `WORKFLOW` at the copy and expects the grader to fail. The
positive control adds an unreviewed action as a step, which the grader
rejects today.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[4]
GRADER = REPO / "tests" / "repo" / "test_wiki_daily_gates_job.py"
WORKFLOW = REPO / ".github" / "workflows" / "wiki-daily.yml"
SHA = "0aaccfd150d50ccaeb58ebd88d36e91967a5f35b"
ANCHOR = "  report:\n"


def _grader() -> ModuleType:
    spec = importlib.util.spec_from_file_location("m13_39_probe_grader", GRADER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_with(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    copy = tmp_path / "wiki-daily.yml"
    copy.write_text(text, encoding="utf-8")
    grader = _grader()
    monkeypatch.setattr(grader, "WORKFLOW", copy)
    grader.test_no_job_that_runs_or_follows_the_model_uses_a_cache()


def test_positive_control_an_unreviewed_step_action_fails_the_grader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count(ANCHOR) == 1
    job = (
        "  extra:\n    runs-on: ubuntu-latest\n    steps:\n"
        f"      - uses: actions/setup-go@{SHA}\n\n"
    )
    with pytest.raises(AssertionError):
        _run_with(tmp_path, monkeypatch, text.replace(ANCHOR, job + ANCHOR))


def test_constructed_reusable_workflow_job_fails_the_grader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count(ANCHOR) == 1
    job = f"  extra:\n    uses: someone/repo/.github/workflows/cache.yml@{SHA}\n\n"
    with pytest.raises(AssertionError):
        _run_with(tmp_path, monkeypatch, text.replace(ANCHOR, job + ANCHOR))
