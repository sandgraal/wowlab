# Probe from review of m13/05-addon-schema-3; reproduces an Actions.lua whose combat check never reports combat passing every test in the grader
"""M13-05's acceptance asks for static tests that every new call "is not made
in combat". `_m13_05_combat_problems` (tests/addon/test_lab_addon.py) pins
that each gather starts with `combatReason()` and returns `ns.Blocked` on a
reason, and checks `combatReason`'s body only for three substrings:

    if type ( InCombatLockdown ) ~= "function" then return
    local ok , locked = pcall ( InCombatLockdown ) if not ok then return
    if locked then return IN_COMBAT end return nil end

A statement added between them is not seen. Each of these one-line edits
makes `combatReason` return nil whatever the client says, so spellbook,
actionbars and bindings read in combat, and passes every test in the grader:

- `locked = false` before `if locked then`;
- `if InCombatLockdown then return nil end` as the function's first line.

Control: deleting `if locked then return IN_COMBAT end` is rejected.
Constructed inputs (boundary cases for the grader), not addon sources.
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

HEAD = "local function combatReason()\n"
LOCKED = "    if locked then\n        return IN_COMBAT\n    end\n"

# Each case: (text found exactly once in Actions.lua, its replacement), in order.
Edits = list[tuple[str, str]]

CASES: dict[str, Edits] = {
    "constructed-locked-forced-false": [(LOCKED, "    locked = false\n" + LOCKED)],
    "constructed-early-return-nil": [
        (HEAD, HEAD + "    if InCombatLockdown then\n        return nil\n    end\n")
    ],
}
CONTROLS: dict[str, Edits] = {
    "control-in-combat-branch-removed": [(LOCKED, "")],
}


def _grader(root: Path, tag: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"m13_05_combat_grader_{tag}", GRADER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    module.ROOT = root
    module.ADDON_ROOT = root / "lab" / "addon"
    module.ADDON = module.ADDON_ROOT / "WowLab"
    module.ACTIONS = module.ADDON / "Actions.lua"
    module.TOC = module.ADDON / "WowLab.toc"
    module.STD = module.ADDON_ROOT / "wow_client.yml"
    module.README = module.ADDON_ROOT / "README.md"
    module.SOURCES = sorted(module.ADDON.glob("*.lua"))
    return module


def _failing_tests(tmp_path: Path, tag: str, edits: Edits) -> list[str]:
    shutil.copytree(REPO / "lab" / "addon", tmp_path / "lab" / "addon")
    target = tmp_path / "lab" / "addon" / "WowLab" / "Actions.lua"
    text = target.read_text(encoding="utf-8")
    for found, replacement in edits:
        assert text.count(found) == 1, "the edit did not apply; the addon source moved"
        text = text.replace(found, replacement)
    target.write_text(text, encoding="utf-8")
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
def test_a_combat_check_that_never_blocks_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CASES[tag])
