# Probe from review of m11/37-bidi-api-sections; reproduces the section scan missing an ns function called, or ns.state written, through a second binding of the file's `...` (`local _, addon = ...`)
"""M11-37's section scan (tests/addon/test_lab_addon.py, `_SectionScan`)
knows the addon's private namespace only as the one local named `ns` bound
from the file's `...` (`_Flow.namespace`). Rule 3 ("`ns` and `ns.state` are
never aliased") and the count of references to each `ns` function both look
for that one name. The file's `...` can be bound again under any other name,
and the result is the same table:

    local _, addon = ...          -- the usual idiom in WoW addons
    local n = select(2, ...)

A reference through such a binding is invisible to the scan. Talents.lua's
`ns.Spec` (C_SpecializationInfo, GetSpecialization) stays classed as section
code while a new event handler calls it, so it runs on that event with `spec`
and `talents.class` switched off; and `ns.state` can be written behind the
race check's back, which is what the README's "`/wowlab skip customization`
stops this call too" (UnitRace) rests on. Every test in the grader passes
after each edit below, including a brand-new file listed in the TOC that names
the namespace `addon`; `addon.On` is not counted as a caller of ns.On either.

Control: the same new file with the namespace named `ns` is rejected. Each
case checks that at least one test in the grader fails. Constructed inputs
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

KEY = 'local KEY = "customization"\n'
TOC_LAST = "Professions.lua\n"
NEW = "<new file>"

# (file, text found exactly once or NEW, its replacement or the new file's text)
Edit = tuple[str, str, str]


def _new_file(name: str) -> list[Edit]:
    return [
        (
            "Extra.lua",
            NEW,
            f"-- A new file.\n\nlocal _, {name} = ...\n\n"
            f'{name}.On("PLAYER_LEVEL_UP", function()\n    {name}.Spec()\nend)\n',
        ),
        ("WowLab.toc", TOC_LAST, TOC_LAST + "Extra.lua\n"),
    ]


def _handler(binding: str, body: str) -> list[Edit]:
    return [
        (
            "Customization.lua",
            KEY,
            KEY + binding + 'ns.On("PLAYER_LEVEL_UP", function()\n' + body + "end)\n",
        )
    ]


CASES: dict[str, list[Edit]] = {
    "constructed-new-file-names-the-namespace-addon": _new_file("addon"),
    "constructed-second-binding-of-the-vararg": _handler("local _, n = ...\n", "    n.Spec()\n"),
    "constructed-select-of-the-vararg": _handler("local n = select(2, ...)\n", "    n.Spec()\n"),
    "constructed-state-written-through-a-second-binding": _handler(
        "local _, n = ...\n", "    n.state.customization = { carried = true, race_id = 0 }\n"
    ),
}
CONTROLS: dict[str, list[Edit]] = {
    "control-new-file-names-the-namespace-ns": _new_file("ns"),
    "control-handler-calls-ns-spec": _handler("", "    ns.Spec()\n"),
}


def _grader(root: Path, tag: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"m11_37_binding_grader_{tag}", GRADER)
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
        if found == NEW:
            assert not target.exists(), "the new file already exists"
            target.write_text(replacement, encoding="utf-8")
            continue
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
def test_namespace_reached_through_a_second_binding_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CASES[tag])
