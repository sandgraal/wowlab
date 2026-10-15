# Probe from review of m13/18-parallel-tests; reproduces the serial-marking scan
# missing a module that reads RSS or a CPU clock through an aliased import.
"""CONSTRUCTED test modules (L8), boundary cases for the M13-18 scan.

M13-18 acceptance: "a static test puts every module that uses `ru_maxrss` or
`resource.getrusage` in the serial set". `tests/repo/test_serial_marking.py`
finds a reader by an attribute or a name spelled `getrusage`/`ru_maxrss`, a
call spelled `cpu_clock`/`process_time`, or a string holding one of those
words. An import alias hides every one of them: `from resource import
getrusage as g` then `g(...)[2]` (index 2 is `ru_maxrss`) reads RSS with no
matching name, attribute or string; `from time import process_time as pt`
then `pt()` reads the CPU clock the same way; so does `clock =
time.process_time` then `clock()` (an attribute that is not called).

Each case writes one constructed test module into a `tmp_path` tree laid out
like the repository, points the scan's `ROOT` there, and expects the scan to
report it with both serial lists empty. The positive control is the plain
spelling (`resource.getrusage(...).ru_maxrss`, `time.process_time()`), which
the scan reports today.

The words are assembled at run time so this file does not itself trip the
scan it probes.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[4]

RES = "res" + "ource"
RUSAGE = "get" + "rusage"
MAXRSS = "ru_" + "maxrss"
PTIME = "process" + "_time"


def _scan_module() -> ModuleType:
    path = REPO / "tests" / "repo" / "test_serial_marking.py"
    spec = importlib.util.spec_from_file_location("m13_18_probe_scan", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _empty_lists() -> ModuleType:
    empty = ModuleType("m13_18_probe_empty_lists")
    empty.SERIAL_FILES = frozenset()  # type: ignore[attr-defined]
    empty.SERIAL_TESTS = frozenset()  # type: ignore[attr-defined]
    return empty


def _scan_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> list[str]:
    scan = _scan_module()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_constructed_reader.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(scan, "ROOT", tmp_path)
    monkeypatch.setattr(scan, "TEST_ROOTS", ("tests",))
    reported: list[str] = scan._unlisted_measurements(_empty_lists())
    return reported


CONTROLS = [
    pytest.param(
        f"import {RES}\n\n\ndef test_peak():\n"
        f"    assert {RES}.{RUSAGE}({RES}.RUSAGE_SELF).{MAXRSS} > 0\n",
        id="control-rss-plain-spelling",
    ),
    pytest.param(
        f"import time\n\n\ndef test_budget():\n"
        f"    start = time.{PTIME}()\n    assert time.{PTIME}() - start < 10\n",
        id="control-cpu-plain-spelling",
    ),
]

ALIASED = [
    pytest.param(
        f"from {RES} import {RUSAGE} as usage\n\n\ndef test_peak():\n    assert usage(0)[2] > 0\n",
        id="constructed-rss-through-an-import-alias",
    ),
    pytest.param(
        f"from time import {PTIME} as cpu\n\n\ndef test_budget():\n"
        "    start = cpu()\n    assert cpu() - start < 10\n",
        id="constructed-cpu-clock-through-an-import-alias",
    ),
    pytest.param(
        f"import time\n\nclock = time.{PTIME}\n\n\ndef test_budget():\n"
        "    start = clock()\n    assert clock() - start < 10\n",
        id="constructed-cpu-clock-bound-to-a-name",
    ),
]


@pytest.mark.parametrize("source", CONTROLS)
def test_positive_control_plain_spelling_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    assert _scan_reports(tmp_path, monkeypatch, source), "the scan reports the plain spelling"


@pytest.mark.parametrize("source", ALIASED)
def test_an_aliased_reader_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    reported = _scan_reports(tmp_path, monkeypatch, source)
    assert reported, (
        "a module that reads RSS or a CPU clock through an alias is left in the "
        "parallel pass with no failure:\n" + source
    )
