# Probe from review of m11/01-lab-addon; reproduces the round-3 carry checks passing a saved sub-table (r.choices[i]) appended with table.insert or copied across a line break
"""Round 3 made `test_carry_reads_saved_tables_only_through_fields` token-based:
the bare names `saved`, `r` and `c` may appear only before `.` or `[`, or
inside `type(...)`/`ipairs(...)`. A field of a saved table is still a saved
table, and the value check that should catch it
(`test_carried_customization_is_rebuilt_from_checked_values`) is line-based and
reads only lines that contain `=`. Two ordinary edits to `carry` in a copy of
the real addon pass every test in tests/addon/test_lab_addon.py (and selene):

- `for i = 1, #r.choices do table.insert(out.choices, r.choices[i]) end`
  (each saved choice table carried as is, whatever it holds);
- `out.choices =` then `r.choices` on the next line (the saved list carried
  as is).

Control: `out.choices = r.choices` on one line is rejected. Each case checks
that at least one test in the grader fails. Constructed inputs (boundary
cases for the grader), not addon sources.
"""

from __future__ import annotations

import importlib.util
import inspect
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[4]
GRADER = REPO / "tests" / "addon" / "test_lab_addon.py"
CARRY_RETURN = "        return out\n    end,\n    gather"

CASES = {
    "constructed-table-insert-saved-choice-by-index": (
        "        for i = 1, #r.choices do\n"
        "            table.insert(out.choices, r.choices[i])\n"
        "        end\n"
    ),
    "constructed-saved-list-across-line-break": "        out.choices =\n            r.choices\n",
}
CONTROLS = {
    "control-saved-list-on-one-line": "        out.choices = r.choices\n",
}


def _grader(root: Path, tag: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"cr97d_grader_{tag}", GRADER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    module.ROOT = root
    module.ADDON_ROOT = root / "lab" / "addon"
    module.ADDON = module.ADDON_ROOT / "WowLab"
    module.TOC = module.ADDON / "WowLab.toc"
    module.STD = module.ADDON_ROOT / "wow_client.yml"
    module.README = module.ADDON_ROOT / "README.md"
    module.SOURCES = sorted(module.ADDON.glob("*.lua"))
    return module


def _failing_tests(tmp_path: Path, tag: str, insert: str) -> list[str]:
    shutil.copytree(REPO / "lab" / "addon", tmp_path / "lab" / "addon")
    target = tmp_path / "lab" / "addon" / "WowLab" / "Customization.lua"
    before = target.read_text(encoding="utf-8")
    after = before.replace(CARRY_RETURN, insert + CARRY_RETURN)
    assert after != before, "the edit did not apply; the addon source moved"
    target.write_text(after, encoding="utf-8")
    grader = _grader(tmp_path, tag)
    failing = []
    for test_name, fn in inspect.getmembers(grader, inspect.isfunction):
        if not test_name.startswith("test_"):
            continue
        calls = (
            [(p,) for p in grader.SOURCES] if "path" in inspect.signature(fn).parameters else [()]
        )
        for args in calls:
            try:
                fn(*args)
            except AssertionError:
                failing.append(test_name)
    return failing


@pytest.mark.parametrize("tag", sorted(CONTROLS))
def test_positive_control_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CONTROLS[tag])


@pytest.mark.parametrize("tag", sorted(CASES))
def test_saved_subtable_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CASES[tag])
