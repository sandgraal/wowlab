# Probe from review of m11/37-bidi-api-sections; reproduces the section scan missing client calls made through `self` in an ns method and through parameters the client fills (OnEvent frame, slash edit box)
"""M11-37's section scan (tests/addon/test_lab_addon.py, `_SectionScan`) after
round 2 (a003b62). Two classes of name reach the client or the namespace
without being a client global, a handle or `ns`, and the scan follows neither:

- `self` inside `function ns:Name()` is the namespace when the method is
  called as `ns:Name()`. Rule 3 says the namespace "is reached under no other
  name", but `self` is one: a Gear.lua method `function ns:Later() self.Spec()
  end`, called from Core.lua's ns.Refresh (the `/wowlab save` path), runs
  Talents.lua's ns.Spec (C_SpecializationInfo) with `spec` and
  `talents.class` switched off, and ns.Spec is still classed as section code.
  Control: `ns.Spec()` written in ns.Refresh is rejected.
- A parameter of a function the client calls holds a client object. The
  OnEvent dispatcher's first parameter (`_` today) is the event frame, and the
  slash handler's second parameter is the chat edit box. Naming them and
  calling a method (`f:UnregisterAllEvents()` on every event,
  `box:SetText("")` on every `/wowlab`) is a client API call outside every
  section that SECTIONLESS_API does not pin, and every test still passes.
  Control: the same call through the `frame` handle is rejected.

Each case checks that at least one test in the grader fails. Constructed
inputs (boundary cases for the grader), not addon sources.
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
DISPATCH = 'frame:SetScript("OnEvent", function(_, event, ...)\n'
SLASH = "SlashCmdList.WOWLAB = function(message)\n"

# (file, text found exactly once, its replacement)
Edit = tuple[str, str, str]

CASES: dict[str, list[Edit]] = {
    "constructed-self-in-an-ns-method-calls-ns-spec": [
        ("Gear.lua", GEAR_TOP, GEAR_TOP + "function ns:Later()\n    self.Spec()\nend\n"),
        ("Core.lua", REFRESH, REFRESH + "    ns:Later()\n"),
    ],
    "constructed-onevent-frame-parameter": [
        (
            "Core.lua",
            DISPATCH,
            'frame:SetScript("OnEvent", function(f, event, ...)\n    f:UnregisterAllEvents()\n',
        ),
    ],
    "constructed-slash-edit-box-parameter": [
        (
            "Core.lua",
            SLASH,
            "SlashCmdList.WOWLAB = function(message, box)\n"
            '    if box then\n        box:SetText("")\n    end\n',
        ),
    ],
}
CONTROLS: dict[str, list[Edit]] = {
    "control-ns-dot-call-from-core": [("Core.lua", REFRESH, REFRESH + "    ns.Spec()\n")],
    "control-frame-handle-call-in-the-dispatcher": [
        (
            "Core.lua",
            DISPATCH,
            DISPATCH + "    frame:UnregisterAllEvents()\n",
        ),
    ],
}


def _grader(root: Path, tag: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"m11_37_param_grader_{tag.replace('-', '_')}", GRADER
    )
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


def _failing_tests(tmp_path: Path, tag: str, edits: list[Edit]) -> list[str]:
    shutil.copytree(REPO / "lab" / "addon", tmp_path / "lab" / "addon")
    for name, found, replacement in edits:
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
def test_client_reached_outside_a_section_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CASES[tag])
