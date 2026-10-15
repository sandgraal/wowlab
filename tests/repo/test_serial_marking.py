"""The `serial` list in the root conftest.py names tests that exist (M13-18).

The root `conftest.py` marks, by node id, the tests that measure the machine
(RSS, CPU-time budgets), so they run alone after the parallel pass. A file or
test renamed without updating that list would leave its measurement in the
parallel pass with nothing failing; these checks fail instead. They read the
test files as text (ast), import none of them, and need no collection run.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def _root_conftest() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wowlab_root_conftest", ROOT / "conftest.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _top_level_tests(path: Path) -> set[str]:
    tree = ast.parse(path.read_bytes(), filename=str(path))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test")
    }


def test_every_serial_file_exists_and_holds_tests() -> None:
    conftest = _root_conftest()
    assert conftest.SERIAL_FILES, "the list is not empty"
    for name in sorted(conftest.SERIAL_FILES):
        path = ROOT / name
        assert path.is_file(), f"{name} is listed as serial but does not exist"
        assert _top_level_tests(path), f"{name} is listed as serial but holds no test"


def test_every_serial_test_exists() -> None:
    conftest = _root_conftest()
    assert conftest.SERIAL_TESTS, "the list is not empty"
    for nodeid in sorted(conftest.SERIAL_TESTS):
        file, _, test = nodeid.partition("::")
        assert test and "[" not in test and "::" not in test, f"{nodeid}: file::function only"
        path = ROOT / file
        assert path.is_file(), f"{nodeid}: {file} does not exist"
        assert test in _top_level_tests(path), f"{nodeid}: no such test in {file}"


def test_a_test_is_listed_once() -> None:
    conftest = _root_conftest()
    for nodeid in conftest.SERIAL_TESTS:
        file = nodeid.partition("::")[0]
        assert file not in conftest.SERIAL_FILES, f"{nodeid}: its whole file is already serial"


# What reads the machine. RSS: whoever reads it (a test, a helper, a child
# script held in a string) needs the whole module serial. CPU clock: a call
# of `cpu_clock()` (tests/parser/_cpu_clock.py, tests/scripts/test_lab_capture.py)
# or of `time.process_time()`, or `time.process_time` in a child script string.
RSS_WORDS = ("ru_maxrss", "getrusage", "VmHWM")
CPU_CALLS = frozenset({"cpu_clock", "process_time"})
CPU_WORDS = ("time.process_time",)
TEST_ROOTS = ("lab/core/tests", "tests")
HERE = Path(__file__).resolve().relative_to(ROOT).as_posix()  # names the words


def _docstrings(tree: ast.Module) -> set[int]:
    """The ids of the docstring nodes: words in prose are not measurements."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
    return found


def _reads(node: ast.AST, docstrings: set[int]) -> tuple[bool, bool]:
    """(reads RSS, reads a CPU clock) anywhere under `node`."""
    rss = cpu = False
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Attribute) and sub.attr in RSS_WORDS) or (
            isinstance(sub, ast.Name) and sub.id in RSS_WORDS
        ):
            rss = True
        elif isinstance(sub, ast.Call):
            func = sub.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            cpu = cpu or name in CPU_CALLS
        elif (
            isinstance(sub, ast.Constant)
            and isinstance(sub.value, str)
            and id(sub) not in docstrings
        ):
            rss = rss or any(word in sub.value for word in RSS_WORDS)
            cpu = cpu or any(word in sub.value for word in CPU_WORDS)
    return rss, cpu


def _unlisted_measurements(conftest: ModuleType) -> list[str]:
    problems: list[str] = []
    for top in TEST_ROOTS:
        for path in sorted((ROOT / top).rglob("test_*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if "/fixtures/" in rel or rel in conftest.SERIAL_FILES or rel == HERE:
                continue
            tree = ast.parse(path.read_bytes(), filename=rel)
            docstrings = _docstrings(tree)
            for node in tree.body:
                rss, cpu = _reads(node, docstrings)
                if not (rss or cpu):
                    continue
                is_test = isinstance(
                    node, ast.FunctionDef | ast.AsyncFunctionDef
                ) and node.name.startswith("test")
                if rss:
                    problems.append(f"{rel}: reads RSS; the whole module must be in SERIAL_FILES")
                elif not is_test:
                    where = getattr(node, "name", f"line {node.lineno}")
                    problems.append(
                        f"{rel}: {where} reads a CPU clock outside a test; "
                        "the module must be in SERIAL_FILES"
                    )
                elif f"{rel}::{node.name}" not in conftest.SERIAL_TESTS:
                    problems.append(
                        f"{rel}::{node.name} reads a CPU clock; list it in SERIAL_TESTS"
                    )
    return problems


def test_every_module_that_reads_rss_or_a_cpu_clock_is_serial() -> None:
    problems = _unlisted_measurements(_root_conftest())
    assert not problems, "measurements left in the parallel pass:\n" + "\n".join(problems)


def test_the_scan_sees_a_measurement_that_is_not_listed() -> None:
    """Positive control: with the lists emptied, every listed module is found
    again, so the scan above can fail."""
    conftest = _root_conftest()
    empty = ModuleType("empty_lists")
    empty.SERIAL_FILES = frozenset()  # type: ignore[attr-defined]
    empty.SERIAL_TESTS = frozenset()  # type: ignore[attr-defined]
    found = "\n".join(_unlisted_measurements(empty))
    for name in conftest.SERIAL_FILES:
        assert f"{name}:" in found, f"the scan does not see {name}"
    for nodeid in conftest.SERIAL_TESTS:
        if "test_layout.py" in nodeid or "test_guard_pins" in nodeid:
            continue  # wall clock, and a fork: listed by hand, not found by the scan
        assert nodeid.partition("::")[0] in found, f"the scan does not see {nodeid}"


def test_is_serial_matches_files_tests_and_parametrizations() -> None:
    conftest = _root_conftest()
    some_file = sorted(conftest.SERIAL_FILES)[0]
    some_test = sorted(conftest.SERIAL_TESTS)[0]
    file_of_test = some_test.partition("::")[0]
    assert conftest.is_serial(f"{some_file}::test_anything")
    assert conftest.is_serial(f"{some_file}::test_anything[param-1]")
    assert conftest.is_serial(some_test)
    assert conftest.is_serial(f"{some_test}[constructed-a::b]")
    # Positive controls: a neighbour in the same file, a prefix of the name.
    assert not conftest.is_serial(f"{file_of_test}::test_some_other_test")
    assert not conftest.is_serial(f"{some_test}_and_more")
    assert not conftest.is_serial("lab/core/tests/test_cli.py::test_version")
