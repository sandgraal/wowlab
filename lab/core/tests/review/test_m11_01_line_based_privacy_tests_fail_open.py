# Probe from review of m11/01-lab-addon; reproduces the round-2 line-based privacy tests passing a saved table appended with table.insert and a pairs() walk of an API result bound over two lines
"""Fix round 2 added line-based checks to tests/addon/test_lab_addon.py. Two
ordinary edits to a copy of the real addon pass every test in that file (and
selene):

- in Customization.lua's `carry`, appending the saved choice table itself with
  `table.insert(out.choices, c)`: the rebuild check only looks at lines with
  `=`, so the saved table (and whatever else it holds) is carried forward;
- a `local d =` line break before `ns.Call(...GetCurrentCharacterData...)`, then
  `for k, v in pairs(d)` copying every field (the character's name included)
  into WowLabDB: `_binding_rhs` sees an empty right-hand side and allows it.

Controls: the same code with the call on one line, and a direct `out.x = r.x`
copy, are rejected. Each case checks that at least one test in the grader
fails. Constructed inputs (boundary cases for the grader), not addon sources.
"""

from __future__ import annotations

import importlib.util
import inspect
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[4]
GRADER = REPO / "tests" / "addon" / "test_lab_addon.py"

CHOICE_BODY = """                local entry = {
                    choice_index = type(c.choice_index) == "number" and c.choice_index or nil,
                    choice = type(c.choice) == "number" and c.choice or nil,
                }
                if type(c.option) == "number" then
                    entry.option = c.option
                    out.choices[#out.choices + 1] = entry
                end
"""
CARRY_RETURN = "        return out\n    end,\n    gather"
CHARACTER_DATA = 'ns.Call(ns.Fn(C_BarberShop, "GetCurrentCharacterData"))'
WALK = "for k, v in pairs(d) do\n    WowLabDB[k] = v\nend\n"

Edit = tuple[str, Callable[[str], str]]

CASES: dict[str, Edit] = {
    "constructed-carry-table-insert-saved-choice": (
        "Customization.lua",
        lambda s: s.replace(CHOICE_BODY, "                table.insert(out.choices, c)\n"),
    ),
    "constructed-pairs-over-two-line-binding": (
        "Collections.lua",
        lambda s: s + f"\nlocal d =\n    {CHARACTER_DATA}\n" + WALK,
    ),
}
CONTROLS: dict[str, Edit] = {
    "control-carry-direct-copy": (
        "Customization.lua",
        lambda s: s.replace(CARRY_RETURN, "        out.extra = r.extra\n" + CARRY_RETURN),
    ),
    "control-pairs-over-one-line-binding": (
        "Collections.lua",
        lambda s: s + f"\nlocal d = {CHARACTER_DATA}\n" + WALK,
    ),
}


def _grader(root: Path, tag: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"cr97c_grader_{tag}", GRADER)
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


def _failing_tests(tmp_path: Path, tag: str, edit: Edit) -> list[str]:
    shutil.copytree(REPO / "lab" / "addon", tmp_path / "lab" / "addon")
    name, change = edit
    target = tmp_path / "lab" / "addon" / "WowLab" / name
    before = target.read_text(encoding="utf-8")
    after = change(before)
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
def test_ordinary_edit_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CASES[tag])
