# Probe from review of m13/05-addon-schema-3; reproduces every value of a gather stored untested once `isSecret` is rebound, passing every test in the grader
"""M13-05 round 3 (tests/addon/test_lab_addon.py,
`test_m13_05_every_client_value_is_asked_about_before_use`) says: "Where the
client has issecretvalue, each gather tests every value a client call gave
... before the value is compared, used or stored." `_m13_05_secret_problems`
pins the text `local isSecret = type ( issecretvalue ) == "function" and
issecretvalue or nil` (three times) and requires each `clean` call's first
argument to be the *name* `isSecret`. It never checks that the `isSecret` a
`clean` call sees is that binding: `_m13_05_call_problems` refuses a rebinding
only of the builtins, the file's local functions and `ns`, and `isSecret` is
none of those. `clean` returns its value untested when `isSecret` is nil, so
each of these one-line edits turns the test off for a whole gather (or the
whole spellbook item walk) while the record's `issecretvalue` probe still says
`present = true`:

- `isSecret = nil` after the action-bar gather's bind;
- `local isSecret = nil` at the top of the action-bar slot loop (a shadow);
- `local isSecret = false` before the bindings loop (a shadow);
- `isSecret = nil` as the first statement of `spellbookItem` (its parameter);
- `spellbookItem(..., isSecret and nil)` at the call site.

Control: the existing grader case, the bind line replaced by
`local isSecret = nil` (in the action-bar gather), is rejected. Constructed inputs (boundary cases for
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

MACRO_HANDLE = "and GetMacroInfo or nil\n        "
BIND = 'local isSecret = type(issecretvalue) == "function" and issecretvalue or nil'
ACTION_PROBE = '        local action = probe("GetActionInfo", getAction)\n'
SLOT_LOOP = "        for slot = FIRST_ACTION_SLOT, LAST_ACTION_SLOT do\n"
BINDING_LOOP = "        for index = 1, math.min(total, MAX_BINDINGS) do\n"
ITEM_CALL = "    local ok, info = pcall(itemInfo, slot, bank)\n"
ITEM_SITE = "spellbookItem(probes, slot, line, bank, itemInfo, itemName, isSecret)"

# Each case: (text found exactly once in Actions.lua, its replacement), in order.
Edits = list[tuple[str, str]]

CASES: dict[str, Edits] = {
    "constructed-rebound-to-nil-after-the-bind": [
        (ACTION_PROBE, "        isSecret = nil\n" + ACTION_PROBE),
    ],
    "constructed-shadowed-in-the-slot-loop": [
        (SLOT_LOOP, SLOT_LOOP + "            local isSecret = nil\n"),
    ],
    "constructed-shadowed-before-the-bindings-loop": [
        (BINDING_LOOP, "        local isSecret = false\n" + BINDING_LOOP),
    ],
    "constructed-parameter-cleared-in-spellbook-item": [
        (ITEM_CALL, "    isSecret = nil\n" + ITEM_CALL),
    ],
    "constructed-nil-passed-to-spellbook-item": [
        (ITEM_SITE, ITEM_SITE.replace(", isSecret)", ", isSecret and nil)")),
    ],
}
CONTROLS: dict[str, Edits] = {
    # The action-bar gather's bind only (the line after the GetMacroInfo handle).
    "control-gather-without-the-test": [
        (MACRO_HANDLE + BIND, MACRO_HANDLE + "local isSecret = nil"),
    ],
}


def _grader(root: Path, tag: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"m13_05_rebind_grader_{tag}", GRADER)
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
def test_a_rebound_is_secret_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CASES[tag])
