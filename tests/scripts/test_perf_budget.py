"""Grades for the nightly performance budget (M16-27, docs/LAB_PLAN.md §6.12).

The budget itself runs only in `.github/workflows/perf-nightly.yml`, never on
the PR path (§18.1 item 6). What runs here, on every PR, keeps that job from
rotting unseen:

- §6.12's table and `scripts/perf_budget.py` agree: the same five commands,
  the same baselines, the same failure factor and the same absolute target;
- the verdict fails a ratio over 1.5 times its baseline, passes one at it,
  and fails a command with no baseline (constructed ratios);
- the workflow runs only on a schedule or by hand, on `ubuntu-latest`, with
  a read-only token, and runs the script;
- the whole pipeline runs end to end on a small constructed tree in record
  mode (no budget is checked here: the PR runners are not the reference
  runner), with the home and user data directories redirected into the
  sandbox, and leaves the captured fixture tree it copied unchanged.

Every ratio in the verdict tests is constructed. The smoke run copies the
captured `macos/` fixture into a temporary folder and grows it with
constructed files; it reads no real install and calls no network service.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import sys
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "LAB_PLAN.md"
WORKFLOW = ROOT / ".github" / "workflows" / "perf-nightly.yml"


def _load() -> types.ModuleType:
    path = ROOT / "scripts" / "perf_budget.py"
    spec = importlib.util.spec_from_file_location("perf_budget", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


perf_budget = _load()


def _section() -> str:
    text = PLAN.read_text(encoding="utf-8")
    start = text.index("### 6.12 ")
    end = text.index("\n## 7.", start)
    return text[start:end]


def _table() -> dict[str, float]:
    rows: dict[str, float] = {}
    for line in _section().splitlines():
        match = re.match(r"^\| `([a-z-]+)` \|.*\| ([0-9.]+) \|$", line)
        if match:
            rows[match.group(1)] = float(match.group(2))
    return rows


# ─── the spec table and the script agree ────────────────────────────────────


def test_the_plan_table_matches_the_script_baselines() -> None:
    assert _table() == perf_budget.BASELINE


def test_every_command_has_a_positive_baseline() -> None:
    assert set(perf_budget.BASELINE) == set(perf_budget.COMMANDS)
    assert all(value > 0 for value in perf_budget.BASELINE.values())


def test_the_ticket_commands_are_the_ones_timed() -> None:
    assert set(perf_budget.COMMANDS) == {
        "whole-restore",
        "sv-dump-json",
        "sv-merge",
        "addons-list",
        "version",
    }


def test_the_plan_states_the_factor_and_the_absolute_target() -> None:
    section = _section()
    assert perf_budget.FAIL_FACTOR == 1.5 and "1.5 times" in section
    assert perf_budget.RESTORE_FILES == 20_000 and "20,000" in section
    assert perf_budget.RESTORE_LIMIT_S == 5.0 and "5 s" in section


# ─── the verdict (constructed ratios) ───────────────────────────────────────


def test_constructed_a_ratio_at_the_limit_passes() -> None:
    assert perf_budget.verdict({"a": 15.0}, {"a": 10.0}) == []


def test_constructed_a_ratio_over_the_limit_fails() -> None:
    misses = perf_budget.verdict({"a": 15.01, "b": 1.0}, {"a": 10.0, "b": 1.0})
    assert len(misses) == 1 and misses[0].startswith("a: ")


def test_constructed_a_command_without_a_baseline_fails() -> None:
    assert perf_budget.verdict({"a": 1.0}, {}) == ["a: no baseline"]
    assert perf_budget.verdict({"a": 1.0}, {"a": 0.0}) == ["a: no baseline"]


def test_constructed_the_calibration_loop_takes_measurable_time() -> None:
    assert perf_budget.calibrate() > 0


# ─── the workflow ───────────────────────────────────────────────────────────


def _workflow() -> dict[object, object]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_the_workflow_is_off_the_pr_path() -> None:
    # PyYAML reads the bare key `on` as True.
    triggers = _workflow()[True]
    assert isinstance(triggers, dict)
    assert set(triggers) == {"schedule", "workflow_dispatch"}


def test_the_workflow_has_a_read_only_token_and_runs_the_script() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict) and set(jobs) == {"budget"}
    job = jobs["budget"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["permissions"] == {"contents": "read"}
    runs = [step["run"] for step in job["steps"] if "run" in step]
    assert runs == [
        "uv sync --frozen",
        "uv run --frozen python scripts/perf_budget.py",
    ]


# ─── the pipeline, end to end, small ────────────────────────────────────────


def _tree_hash(base: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        digest.update(path.relative_to(base).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.mark.skipif(os.name != "posix", reason="the budget script is POSIX only")
def test_constructed_the_pipeline_runs_on_a_small_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(perf_budget.tempfile, "tempdir", None)
    before = _tree_hash(perf_budget.FIXTURE)
    code = perf_budget.main(["--record", "--files", "40", "--runs", "1", "--restore-runs", "1"])
    out = capsys.readouterr()
    assert code == 0, out.err
    for command in perf_budget.COMMANDS:
        assert re.search(rf"^{command} +[0-9.]+ +[0-9.]+ +[0-9.]+ ", out.out, re.M), out.out
    # The absolute check times the gate's call in its own child process.
    assert re.search(r"^gate's whole-restore check at 40 files: [0-9.]+ s wall", out.out, re.M)
    assert "(information only)" in out.out
    assert _tree_hash(perf_budget.FIXTURE) == before
    # The sandbox, user data included, was removed with its temporary folder.
    assert list(tmp_path.iterdir()) == []


def test_constructed_the_sandbox_redirects_home_and_user_data(tmp_path: Path) -> None:
    root, env = perf_budget.build_sandbox(tmp_path / "sb", 16)
    assert root.is_relative_to(tmp_path)
    for key in (
        "HOME",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "WOWLAB_WOW_ROOT",
    ):
        assert Path(env[key]).is_relative_to(tmp_path), key
    flavor = root / perf_budget.FLAVOR_FOLDER
    constructed = list(flavor.rglob("Constructed*.lua")) + list(flavor.rglob("f*.lua"))
    assert len({p for p in constructed if b"constructed" in p.read_bytes()}) == 16
