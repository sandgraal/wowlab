# Probe from review of m13/05-addon-schema-3; reproduces Actions.lua storing a macro's name (text the owner typed) passing every test in the grader
"""ADR-0026 and §15.2: an action-bar macro slot carries an id only, never the
macro's name or body. Actions.lua asks GetMacroInfo as
`local okMacro, _, icon = pcall(getMacro, id)`, so `_` holds the macro's
name. `STD_PRIVACY_ALLOW` admits GetMacroInfo because "the name and body are
never bound to a used name or stored", and
`test_m13_05_macro_info_binds_neither_name_nor_body` is the pin the PR cites
for it. That pin checks every use of `GetMacroInfo`, `getMacro` and `icon`,
but not of `_`. In Lua `_` is an ordinary local, so one added line stores the
name:

    record.macro_name = ns.String(_)

That edit passes every test in the grader and selene (0 errors, 0 warnings),
and the addon would write the owner's macro name into WowLabCharDB. (The
reader would refuse the file, but the name is already on disk in a file the
owner captures.) Control: binding the name to a named local and storing it
(`local okMacro, macroName, icon = ...`) is rejected. Constructed inputs
(boundary cases for the grader), not addon sources.
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

CALL = "                    local okMacro, _, icon = pcall(getMacro, id)\n"

# Each case: (text found exactly once in Actions.lua, its replacement), in order.
Edits = list[tuple[str, str]]

CASES: dict[str, Edits] = {
    "constructed-name-stored-through-underscore": [
        (CALL, CALL + "                    record.macro_name = ns.String(_)\n"),
    ],
}
CONTROLS: dict[str, Edits] = {
    "control-name-bound-to-a-named-local-and-stored": [
        (
            CALL,
            "                    local okMacro, macroName, icon = pcall(getMacro, id)\n"
            "                    record.macro_name = ns.String(macroName)\n",
        ),
    ],
}


def _grader(root: Path, tag: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"m13_05_macro_grader_{tag}", GRADER)
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
def test_a_stored_macro_name_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CASES[tag])
