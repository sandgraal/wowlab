# Probe from review of m11/37-bidi-api-sections; reproduces the ticket's GetViewingChrModel-at-ADDON_LOADED case passing every test when the call sits in the customization carry
"""M11-37 exists because "a `GetViewingChrModel` call added at `ADDON_LOADED`
in a copy passed every test" (docs/BACKLOG.md). The section scan
(tests/addon/test_lab_addon.py) counts a section's `carry` as section code, as
it does its `gather`. Core.lua runs every carry at ADDON_LOADED, before the
world is visible and before any `/wowlab skip` can be typed; only a skip saved
in an earlier session keeps it from running. The 15 s window
(docs/LAB_PLAN.md §13.1, amended 2026-09-29) exists so the owner can switch a
section off before it runs, and it does not cover the carry. A client call
that asserts in a carry crashes the client at every login, and a crash writes
nothing, so the skip can never be saved; the owner's only way out is to untick
the addon.

The same call the ticket names, moved into Customization.lua's carry, passes
every test in the grader (and selene):

    local viewing = ns.Fn(C_BarberShop, "GetViewingChrModel")
    if viewing then
        pcall(viewing)
    end

No carry reads a client global today, so the stricter rule costs nothing on
the real sources. The grader's own tests treat the same timing as outside a
section: the UnitRace race check needs a SECTIONLESS_API entry, and the README
says it "runs as the world is entered, not after the 15 s window".

Control: the same lines in a Customization.lua `ns.On("ADDON_LOADED")` handler
are rejected. Each case checks that at least one test in the grader fails.
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

CARRY_GUARD = (
    '        if type(r) ~= "table" or type(r.absent) ~= "nil" or type(r.choices) ~= "table" then\n'
    "            return nil\n"
    "        end\n"
)
CALL = (
    'local viewing = ns.Fn(C_BarberShop, "GetViewingChrModel")\n'
    "if viewing then\n"
    "    pcall(viewing)\n"
    "end\n"
)
KEY = 'local KEY = "customization"\n'


def _indent(text: str, by: int) -> str:
    return "".join(" " * by + line for line in text.splitlines(keepends=True))


# (file, text found exactly once, its replacement)
Edit = tuple[str, str, str]

CASES: dict[str, Edit] = {
    "constructed-model-call-in-the-carry": (
        "Customization.lua",
        CARRY_GUARD,
        CARRY_GUARD + _indent(CALL, 8),
    ),
}
CONTROLS: dict[str, Edit] = {
    "control-model-call-in-an-addon-loaded-handler": (
        "Customization.lua",
        KEY,
        KEY + 'ns.On("ADDON_LOADED", function()\n' + _indent(CALL, 4) + "end)\n",
    ),
}


def _grader(root: Path, tag: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"m11_37_carry_grader_{tag}", GRADER)
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
    name, found, replacement = edit
    target = tmp_path / "lab" / "addon" / "WowLab" / name
    before = target.read_text(encoding="utf-8")
    assert before.count(found) == 1, "the edit did not apply; the addon source moved"
    target.write_text(before.replace(found, replacement), encoding="utf-8")
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
def test_client_call_in_a_carry_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CASES[tag])
