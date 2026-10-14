# Probe from review of m11/37-bidi-api-sections; reproduces a gather handing a client function to ns.On or a timer passing every test, though it runs after the section is switched off
"""M11-37's section scan (tests/addon/test_lab_addon.py) says: "A function
handed on as a value (to `ns.On`, `C_Timer.After`, a field) may run after its
section is switched off, so it counts as outside." It applies that to the
addon's own functions only. A client function handed on from a gather is not
followed. Three ordinary edits to Professions.lua's gather pass every test in
the grader (and selene):

- `ns.On("PLAYER_TARGET_CHANGED", GetProfessions)`;
- `local later = ns.Fn(C_BarberShop, "GetViewingChrModel")`, then
  `ns.On("PLAYER_TARGET_CHANGED", later)`;
- the same `later` handed to the timer: `local after = ns.Fn(C_Timer, "After")`,
  then `after(20, later)`.

The client function then runs from the event frame or the timer. switchOff
(Core.lua) removes only the section's own listener, so `/wowlab skip
professions` does not stop it. Control: the same call inside a function
literal handed to ns.On from the gather is rejected. Each case checks that at
least one test in the grader fails. Constructed inputs (boundary cases for
the grader), not addon sources.
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

ANCHOR = "        local slots = ns.Pack(GetProfessions())\n"
LATER = '        local later = ns.Fn(C_BarberShop, "GetViewingChrModel")\n'

# (file, text found exactly once, its replacement)
Edit = tuple[str, str, str]

CASES: dict[str, Edit] = {
    "constructed-client-global-registered-as-a-handler": (
        "Professions.lua",
        ANCHOR,
        '        ns.On("PLAYER_TARGET_CHANGED", GetProfessions)\n' + ANCHOR,
    ),
    "constructed-client-function-local-registered-as-a-handler": (
        "Professions.lua",
        ANCHOR,
        LATER
        + '        if later then\n            ns.On("PLAYER_TARGET_CHANGED", later)\n        end\n'
        + ANCHOR,
    ),
    "constructed-client-function-local-handed-to-the-timer": (
        "Professions.lua",
        ANCHOR,
        '        local after = ns.Fn(C_Timer, "After")\n'
        + LATER
        + "        if after and later then\n            after(20, later)\n        end\n"
        + ANCHOR,
    ),
}
CONTROLS: dict[str, Edit] = {
    "control-literal-registered-as-a-handler": (
        "Professions.lua",
        ANCHOR,
        '        ns.On("PLAYER_TARGET_CHANGED", function()\n'
        "            GetProfessions()\n        end)\n" + ANCHOR,
    ),
}


def _grader(root: Path, tag: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"m11_37_handoff_grader_{tag}", GRADER)
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
def test_client_function_handed_on_from_a_gather_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CASES[tag])
