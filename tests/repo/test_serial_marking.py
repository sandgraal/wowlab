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
