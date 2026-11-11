"""`make test-fast` leaves out only what it says, and CI still runs everything (M13-81).

`make test-fast` is a local iteration target. It skips the review probes
under `lab/core/tests/review/` and the `serial` and `live` markers, and
nothing else. These checks read the Makefile, `ci.yml` and `pyproject.toml`
as text, so the target cannot drift into hiding a suite, and the two passes
CI and `make test` run still add up to every test that is not `live`. They
also check the hang guard's settings and its per-test overrides.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import shlex
import tomllib
from pathlib import Path
from types import ModuleType

import yaml

ROOT = Path(__file__).resolve().parents[2]
PARALLEL = "not live and not serial"
SERIAL = "serial and not live"
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
    stages = _variable("CI_STAGES").split()
    assert stages == ["lint", "lint-lua", "test", "test-parser", "hooks-test"]
    assert "test-fast" not in " ".join(_recipe("ci"))


# Every pytest command in ci.yml, by job, exactly. A marker expression is one
# of the two passes (together `not live`) or none (the hook tests, which have
# no markers), and the paths are each job's documented roots: the whole
# testpaths for `quality`, `lab/core/tests` for the desktop job (docs/LAB_PLAN.md
# section 15.3 E3), `tests/harness` for the hook tests on 3.9 and 3.12.
# The third `quality` entry is the bidi scan alone, which runs only on a
# docs-only PR, when the two passes are skipped (M16-89).
CI_PYTEST = {
    "quality": [
        ["-q", "-n", "auto", "--dist", "worksteal", "-m", PARALLEL],
        ["-q", "-m", SERIAL],
        ["-q", "tests/repo/test_bidi_controls.py"],
    ],
    "lab-desktop": [
        ["-q", "-n", "auto", "--dist", "worksteal", "-m", PARALLEL, "lab/core/tests"],
        ["-q", "-m", SERIAL, "lab/core/tests"],
    ],
    "harness": [["-q", "tests/harness"]],
}


def test_ci_yml_runs_every_test_in_its_two_passes() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
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
                assert marker in (PARALLEL, SERIAL), f"{job_id}: -m {marker!r} narrows a pass"
    assert commands == CI_PYTEST


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
