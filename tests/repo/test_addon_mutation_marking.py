"""The `addon_mutation` list in the root conftest.py is every test file that
loads the lab-addon grader, and ci.yml's filter names each (M15-21).

Owner decision, 2026-11-18: a pull request runs the lab-addon grader
mutation probes only when a path in the `changes` job's `addon_probes`
filter changes. A new probe left off `ADDON_MUTATION_FILES` would run on
every pull request (slow, not wrong); a probe on the list but not in the
filter would not run when its own file changes, and a listed file that no
longer loads the grader would be skipped for nothing. These checks fail
instead. They read files as text and import no test module; writes nothing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import yaml

ROOT = Path(__file__).resolve().parents[2]
GRADER = "test_lab_addon.py"
TEST_ROOTS = ("lab/core/tests", "tests")
HERE = Path(__file__).resolve().relative_to(ROOT).as_posix()  # names the grader
# What the filter holds besides the probe files: the addon, its grader, the
# grader's one import from the package, and what decides how tests are
# collected and installed.
FILTER_PATHS = (
    "lab/addon/**",
    "tests/addon/**",
    "lab/core/src/wowlab_core/toc.py",
    "conftest.py",
    "pyproject.toml",
    "uv.lock",
)


def _root_conftest() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wowlab_root_conftest_am", ROOT / "conftest.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _files_loading_the_grader() -> set[str]:
    found = set()
    for top in TEST_ROOTS:
        for path in (ROOT / top).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if "/fixtures/" in rel or rel.startswith("tests/addon/") or rel == HERE:
                continue
            if GRADER in path.read_text(encoding="utf-8"):
                found.add(rel)
    return found


def test_the_list_is_every_file_that_loads_the_grader() -> None:
    listed = set(_root_conftest().ADDON_MUTATION_FILES)
    assert len(listed) == 17, "the seventeen probes of M15-21"
    found = _files_loading_the_grader()
    assert found - listed == set(), "load the addon grader but are not in ADDON_MUTATION_FILES"
    assert listed - found == set(), "in ADDON_MUTATION_FILES but do not load the addon grader"


def test_ci_filters_on_each_probe_and_the_addon_paths() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
    step = next(s for s in workflow["jobs"]["changes"]["steps"] if s.get("id") == "filter")
    filters = yaml.safe_load(step["with"]["filters"])
    expected = list(FILTER_PATHS) + sorted(_root_conftest().ADDON_MUTATION_FILES)
    assert filters["addon_probes"] == expected
    outputs = workflow["jobs"]["changes"]["outputs"]
    assert outputs["addon_probes"] == "${{ steps.filter.outputs.addon_probes }}"


def test_is_addon_mutation_matches_files_and_parametrizations() -> None:
    conftest = _root_conftest()
    some = sorted(conftest.ADDON_MUTATION_FILES)[0]
    assert conftest.is_addon_mutation(f"{some}::test_anything")
    assert conftest.is_addon_mutation(f"{some}::test_anything[case-a::b]")
    # Positive controls: another review probe, the grader itself.
    assert not conftest.is_addon_mutation(
        "lab/core/tests/review/test_m10_04_dense_array_misses_memory_target.py::test_x"
    )
    assert not conftest.is_addon_mutation("tests/addon/test_lab_addon.py::test_x")
