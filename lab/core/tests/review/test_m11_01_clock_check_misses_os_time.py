# Probe from review of m11/01-lab-addon; reproduces the no-wall-clock grader accepting os.time() and os.date()
"""The §13.1 rule "it records no wall-clock time" is enforced by
`test_sources_read_no_clock` in tests/addon/test_lab_addon.py. That check
skips any clock name reached as a field, so `os.time()` and `os.date()` pass.
The selene `lua51` base declares `os.time` and `os.date`, so `make lint-lua`
accepts them too. The WoW client exposes both. Neither gate catches it.

Constructed inputs (boundary cases for the grader), not addon sources. Lives
here, not under tests/addon/, because the review hook allows new files only
under tests/review/.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

GRADER = Path(__file__).resolve().parents[4] / "tests" / "addon" / "test_lab_addon.py"


def _grader() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cr97_lab_addon_grader", GRADER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def _check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    grader = _grader()
    monkeypatch.setattr(grader, "ROOT", tmp_path)
    path = tmp_path / "Constructed.lua"
    path.write_text(source, encoding="utf-8")
    grader.test_sources_read_no_clock(path)


def test_positive_control_bare_time_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(AssertionError):
        _check(tmp_path, monkeypatch, "WowLabCharDB = { stamp = time() }\n")


@pytest.mark.parametrize(
    "source",
    [
        "WowLabCharDB = { stamp = os.time() }\n",
        'WowLabCharDB = { day = os.date("%Y-%m-%d") }\n',
    ],
    ids=["constructed-os-time", "constructed-os-date"],
)
def test_os_clock_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    with pytest.raises(AssertionError):
        _check(tmp_path, monkeypatch, source)
