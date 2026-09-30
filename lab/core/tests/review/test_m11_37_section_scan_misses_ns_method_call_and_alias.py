# Probe from review of m11/37-bidi-api-sections; reproduces the section scan taking ns.Spec for section code when Core calls it as ns:Spec() or a handler calls it through a local alias of ns
"""M11-37's section scan (tests/addon/test_lab_addon.py, `_SectionScan`)
classes an `ns` function as section code when every reference to it is a call
made from section code. It finds those references only in the form
`ns . Name`. Two ordinary edits call Talents.lua's ns.Spec (it calls
C_SpecializationInfo) from outside every section, and every test in the
grader still passes:

- `ns:Spec()` in Core.lua's ns.Refresh (the `/wowlab save` path). Method
  syntax, one character away from the grader's own constructed case
  `ns.Spec()` there, which it rejects. ns.Spec takes no argument, so the
  call behaves the same;
- `local n = ns` and `n.Spec()` in a new Gear.lua event handler.

After either edit ns.Spec runs from the slash command or the event even with
`spec` and `talents.class` switched off, and the scan still counts its client
calls as inside a section. Control: the same call written `ns.Spec()` in
ns.Refresh is rejected. Each case checks that at least one test in the grader
fails. Constructed inputs (boundary cases for the grader), not addon sources.
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

REFRESH = "function ns.Refresh()\n"
GEAR_TOP = "local _, ns = ...\n"

# (file, text found exactly once, its replacement)
Edit = tuple[str, str, str]

CASES: dict[str, Edit] = {
    "constructed-ns-method-call-from-core": ("Core.lua", REFRESH, REFRESH + "    ns:Spec()\n"),
    "constructed-ns-alias-call-from-a-handler": (
        "Gear.lua",
        GEAR_TOP,
        GEAR_TOP + 'ns.On("PLAYER_LEVEL_UP", function()\n    local n = ns\n    n.Spec()\nend)\n',
    ),
}
CONTROLS: dict[str, Edit] = {
    "control-ns-dot-call-from-core": ("Core.lua", REFRESH, REFRESH + "    ns.Spec()\n"),
}


def _grader(root: Path, tag: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"m11_37_ns_grader_{tag}", GRADER)
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
def test_ns_function_called_outside_a_section_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CASES[tag])
