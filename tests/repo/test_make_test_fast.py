"""`make test-fast` leaves out only what it says, and CI still runs everything (M13-81).

`make test-fast` is a local iteration target. It skips the review probes
under `lab/core/tests/review/` and the `serial` and `live` markers, and
nothing else. These checks read the Makefile, `ci.yml`, `macos.yml` (M18-15),
`nightly.yml` (M15-21) and `pyproject.toml` as text, so the target cannot
drift into hiding a suite, and the two passes `make test`, `nightly.yml` and
the desktop suites run still add up to every test that is not `live`. Since
M15-21 (owner decisions, 2026-11-18) `ci.yml` leaves out of a pull request
the lab-addon mutation probes when no addon path changed, and the serial
pass except its one `os.fork` test; the checks pin exactly that. They also
check the hang guard's settings and its per-test overrides.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import shlex
import tomllib
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PARALLEL = "not live and not serial"
SERIAL = "serial and not live"
# ci.yml's parallel pass when the `changes` job's `addon_probes` filter did
# not match (M15-21).
WITHOUT_PROBES = "not live and not serial and not addon_mutation"
# The serial tests ci.yml keeps on every pull request (M15-21): serial for
# what they do to the machine, not because they measure, and both guard the
# write gate: one calls os.fork (the gate's lock, L2), the other starts a
# process named like the game client (the gate's client check, M15-18).
FORK_TEST = (
    "lab/core/tests/test_guard_pins_m10_16.py"
    "::test_constructed_pin_a_forked_child_leaving_the_with_neither_unlocks_nor_commits"
)
CLIENT_TEST = (
    "tests/scripts/test_perf_budget.py"
    "::test_constructed_without_the_stub_the_gate_refuses_the_same_children"
)
GATE_SERIAL_TESTS = (FORK_TEST, CLIENT_TEST)
# Options that would leave tests out of a run.
HIDING = ("--ignore", "--ignore-glob", "--deselect", "-k", "--lf", "--last-failed", "-x")


def _makefile() -> str:
    return (ROOT / "Makefile").read_text(encoding="utf-8")


def _variable(name: str) -> str:
    match = re.search(rf"^{name}\s*:?=\s*(.*)$", _makefile(), re.MULTILINE)
    assert match, f"Makefile defines {name}"
    return match.group(1).strip()


def _recipe(target: str) -> list[str]:
    lines = _makefile().splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(f"{target}:")]
    assert len(starts) == 1, f"one `{target}:` rule"
    recipe = []
    for line in lines[starts[0] + 1 :]:
        if not line.startswith("\t"):
            break
        recipe.append(line[1:])
    return recipe


def _pytest_args(command: str) -> list[str]:
    """The arguments after the `pytest` that runs (after `--` when uv has one)."""
    words = shlex.split(command)
    if "--" in words:
        words = words[words.index("--") + 1 :]
    assert "pytest" in words, command
    return words[words.index("pytest") + 1 :]


def _hides(args: list[str]) -> list[str]:
    return [a for a in args if a.split("=", 1)[0] in HIDING]


def test_test_fast_skips_only_the_review_probes_and_the_serial_and_live_markers() -> None:
    recipe = _recipe("test-fast")
    assert len(recipe) == 1, recipe
    args = _pytest_args(recipe[0])
    assert args == [
        "-n",
        "$(TEST_FAST_WORKERS)",
        "--dist",
        "worksteal",
        "-p",
        "no:cacheprovider",
        "-m",
        PARALLEL,
        "--ignore=lab/core/tests/review",
    ]
    # No path argument: it collects pyproject's testpaths, like `make test`.
    workers = _variable("TEST_FAST_WORKERS")
    assert "min(4, os.cpu_count() or 1)" in workers


def test_make_test_runs_both_passes_over_everything() -> None:
    recipe = _recipe("test")
    assert [_pytest_args(line) for line in recipe] == [["$(PYTEST_PARALLEL)"], ["$(PYTEST_SERIAL)"]]
    parallel = shlex.split(_variable("PYTEST_PARALLEL"))
    serial = shlex.split(_variable("PYTEST_SERIAL"))
    assert parallel == ["-n", "auto", "--dist", "worksteal", "-m", PARALLEL]
    assert serial == ["-m", SERIAL]


def test_make_ci_runs_every_stage() -> None:
    # M15-21: `test` already runs every parser test and tests/harness under
    # 3.12, so `make ci` no longer repeats `test-parser` or the 3.12 hook leg;
    # the 3.9 leg, which `test` cannot run, stays.
    stages = _variable("CI_STAGES").split()
    assert stages == ["lint", "lint-lua", "test", "hooks-test-py39"]
    assert "test-fast" not in " ".join(_recipe("ci"))
    (py39,) = _recipe("hooks-test-py39")
    assert _pytest_args(py39) == ["tests/harness"] and "--python 3.9" in py39
    # Both targets stay: a parser change runs `make test-parser`, CI's
    # parser job calls it, and `make hooks-test` runs both hook legs.
    assert _recipe("test-parser")
    assert _recipe("hooks-test") == ["$(UV) run --frozen pytest tests/harness", py39]


# Every pytest command in ci.yml, macos.yml and nightly.yml, by job, exactly.
# A marker expression is one of the two passes (together `not live`), ci.yml's
# parallel pass without the mutation probes, or none (the hook tests, which
# have no markers), and the paths are each job's documented roots: the whole
# testpaths for `quality` and the nightly suite, `lab/core/tests` for the
# desktop job (docs/LAB_PLAN.md section 15.3 E3; in macos.yml since M18-15),
# `tests/harness` for the hook tests on 3.9 and 3.12.
# `quality` (M15-21): the parallel pass with or without the mutation probes
# (exactly one of the two steps runs; test_ci_runs_the_probes_when_the_filter_says),
# then the write gate's two serial tests alone; the last entry is the bidi scan alone, which
# runs only on a docs-only PR, when the passes are skipped (M16-89).
CI_PYTEST = {
    "quality": [
        ["-q", "-n", "auto", "--dist", "worksteal", "-m", PARALLEL],
        ["-q", "-n", "auto", "--dist", "worksteal", "-m", WITHOUT_PROBES],
        ["-q", *GATE_SERIAL_TESTS],
        ["-q", "tests/repo/test_bidi_controls.py"],
    ],
    "harness": [["-q", "tests/harness"]],
}
NIGHTLY_PYTEST = {
    "suite": [
        ["-q", "-n", "auto", "--dist", "worksteal", "-m", PARALLEL],
        ["-q", "-m", SERIAL],
    ],
}
MACOS_PYTEST = {
    "lab-desktop": [
        ["-q", "-n", "auto", "--dist", "worksteal", "-m", PARALLEL, "lab/core/tests"],
        ["-q", "-m", SERIAL, "lab/core/tests"],
    ],
}


@pytest.mark.parametrize(
    ("name", "expected"),
    [("ci.yml", CI_PYTEST), ("macos.yml", MACOS_PYTEST), ("nightly.yml", NIGHTLY_PYTEST)],
)
def test_each_ci_workflow_runs_every_test_in_its_two_passes(
    name: str, expected: dict[str, list[list[str]]]
) -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text("utf-8"))
    commands: dict[str, list[list[str]]] = {}
    for job_id, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            run = step.get("run", "")
            for line in run.splitlines():
                if re.search(r"\bpytest\b", line) and "uv run" in line:
                    commands.setdefault(job_id, []).append(_pytest_args(line))
    for job_id, runs in commands.items():
        for args in runs:
            assert not _hides(args), f"{job_id}: {args} leaves tests out"
            if "-m" in args:
                marker = args[args.index("-m") + 1]
                assert marker in (PARALLEL, SERIAL, WITHOUT_PROBES), (
                    f"{job_id}: -m {marker!r} narrows a pass"
                )
    assert commands == expected


def _quality_steps() -> dict[str, dict[str, str]]:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
    steps = workflow["jobs"]["quality"]["steps"]
    return {step["name"]: step for step in steps if "name" in step}


def test_ci_runs_the_probes_when_the_filter_says() -> None:
    """The full parallel pass runs when the `addon_probes` filter matched or
    the `changes` job failed; the pass without the probes only when that job
    succeeded, the PR is not docs-only and the filter did not match (M15-21).
    Together: exactly one of them on every PR that is not docs-only."""
    steps = _quality_steps()
    full = steps["pytest (parallel)"]
    without = steps["pytest (parallel, lab-addon mutation probes skipped)"]
    assert full["if"] == (
        "needs.changes.result != 'success' || (needs.changes.outputs.docs_only != 'true'"
        " && needs.changes.outputs.addon_probes == 'true')"
    )
    assert without["if"] == (
        "needs.changes.result == 'success' && needs.changes.outputs.docs_only != 'true'"
        " && needs.changes.outputs.addon_probes != 'true'"
    )


def test_the_serial_tests_ci_keeps_are_the_write_gates() -> None:
    serial = _root_conftest().SERIAL_TESTS
    for nodeid in GATE_SERIAL_TESTS:
        assert nodeid in serial, nodeid
        file, _, test = nodeid.partition("::")
        tree = ast.parse((ROOT / file).read_bytes())
        assert test in {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    step = _quality_steps()["pytest (the write gate's serial tests)"]
    assert step["if"] == (
        "needs.changes.result != 'success' || needs.changes.outputs.docs_only != 'true'"
    )


def test_the_nightly_suite_runs_every_night_with_a_read_only_token() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "nightly.yml").read_text("utf-8"))
    on = workflow.get("on", workflow.get(True))
    assert set(on) == {"schedule", "workflow_dispatch"}
    assert on["schedule"] == [{"cron": "17 9 * * *"}]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["suite"]["permissions"] == {"contents": "read"}


def test_pyproject_hides_nothing_and_sets_the_hang_guard_and_retention() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pytest_ini = config["tool"]["pytest"]["ini_options"]
    assert pytest_ini["testpaths"] == ["lab/core/tests", "tests"]
    assert not _hides(shlex.split(pytest_ini["addopts"]))
    assert pytest_ini["timeout"] == 300
    assert pytest_ini["timeout_method"] == "thread"
    assert pytest_ini["tmp_path_retention_policy"] == "failed"
    assert pytest_ini["tmp_path_retention_count"] == 1


def _root_conftest() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wowlab_root_conftest_t", ROOT / "conftest.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_timeout_override_names_a_test_that_exists() -> None:
    overrides = _root_conftest().TIMEOUT_OVERRIDES
    for nodeid, seconds in overrides.items():
        file, _, test = nodeid.partition("::")
        assert test and "[" not in test and "::" not in test, f"{nodeid}: file::function only"
        path = ROOT / file
        assert path.is_file(), f"{nodeid}: {file} does not exist"
        names = {
            node.name
            for node in ast.parse(path.read_bytes()).body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        assert test in names, f"{nodeid}: no such test in {file}"
        assert isinstance(seconds, int) and seconds > 300, f"{nodeid}: an override raises it"
