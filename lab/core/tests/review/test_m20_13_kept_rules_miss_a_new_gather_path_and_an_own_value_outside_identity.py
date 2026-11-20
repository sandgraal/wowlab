# Probe from review of m20/13-addon-own-account; reproduces a gather in combat on a new Core.lua path and the owner's GUID stored untested from another file, each passing every test in the grader
"""M20-13 keeps two rules (ADR-0026, note of 2026-11-20): nothing is
gathered in combat, and a client value is secret-tested before it is
compared or stored. The grader (tests/addon/test_lab_addon.py) misses each
on a path its pins do not name:

1. Combat. `_m20_13_core_combat` pins the combat test inside the four places
   Core.lua gathers today (`PLAYER_LOGOUT`, `ns.Refresh`, `schedule`'s timer,
   `startFirstPass`) and the places that set `inCombat`. It does not ask
   where else `gatherDirty()` is called, so `gatherDirty()` added to the
   slash handler's `debug` branch (`/wowlab debug` typed in combat gathers
   every dirty section) passes. Control: the logout gather with its combat
   test removed is rejected.
2. Secret test. `_m20_13_identity_problems` checks `UnitGUID`, `UnitName`,
   `GetRealmName` and `GetGuildInfo` in Identity.lua only. With the
   addon-wide privacy list gone, nothing else names them, so
   `record.guid = ns.String(UnitGUID("player"))` inside Gear.lua's gather
   (no `issecretvalue`) passes. Control: the same raw store in Identity.lua
   is rejected.

Each case passes every test in the grader (and selene, checked by hand in
the review). Constructed inputs (boundary cases for the grader), not addon
sources; no Lua is run.
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

# Each case: (file under WowLab/, text found exactly once, its replacement).
Edits = list[tuple[str, str, str]]

GEAR_AT = "                local level, source = itemLevel(slot, raw)\n"

CASES: dict[str, Edits] = {
    "constructed-debug-branch-gathers-in-combat": [
        (
            "Core.lua",
            "        debugCommand(key)\n",
            "        gatherDirty()\n        debugCommand(key)\n",
        ),
    ],
    "constructed-own-guid-stored-untested-in-gear": [
        (
            "Gear.lua",
            GEAR_AT,
            GEAR_AT + '                record.guid = ns.String(UnitGUID("player"))\n',
        ),
    ],
}
CONTROLS: dict[str, Edits] = {
    "control-logout-gathers-in-combat": [
        (
            "Core.lua",
            "    if not inCombat then\n        gatherDirty()\n    end\n",
            "    gatherDirty()\n",
        ),
    ],
    "control-own-guid-stored-untested-in-identity": [
        (
            "Identity.lua",
            "        return record\n    end,\n})\n",
            '        record.raw_guid = ns.String(UnitGUID("player"))\n        return record\n    end,\n})\n',
        ),
    ],
}


def _grader(root: Path, tag: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"m20_13_kept_grader_{tag}", GRADER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    module.ROOT = root
    module.ADDON_ROOT = root / "lab" / "addon"
    module.ADDON = module.ADDON_ROOT / "WowLab"
    module.ACTIONS = module.ADDON / "Actions.lua"
    module.IDENTITY = module.ADDON / "Identity.lua"
    module.TALENTS = module.ADDON / "Talents.lua"
    module.TOC = module.ADDON / "WowLab.toc"
    module.STD = module.ADDON_ROOT / "wow_client.yml"
    module.README = module.ADDON_ROOT / "README.md"
    module.SOURCES = sorted(module.ADDON.glob("*.lua"))
    return module


# The grader's self-tests are mutation loops of their own and cost most of a
# full pass; they run last, and the loop stops at the first failure.
GRADER_SELF_TESTS = frozenset(
    {
        "test_m11_37_a_call_added_outside_a_section_fails",
        "test_m11_37_a_call_added_inside_a_section_passes",
        "test_m13_05_constructed_secret_edits_fail",
        "test_m20_13_each_kept_rule_fails_its_own_edit",
    }
)


def _failing_tests(tmp_path: Path, tag: str, edits: Edits) -> list[str]:
    shutil.copytree(REPO / "lab" / "addon", tmp_path / "lab" / "addon")
    for name, found, replacement in edits:
        target = tmp_path / "lab" / "addon" / "WowLab" / name
        text = target.read_text(encoding="utf-8")
        assert text.count(found) == 1, "the edit did not apply; the addon source moved"
        target.write_text(text.replace(found, replacement), encoding="utf-8")
    grader = _grader(tmp_path, tag)
    members = inspect.getmembers(grader, inspect.isfunction)  # sorted by name
    for test_name, fn in sorted(members, key=lambda m: m[0] in GRADER_SELF_TESTS):
        if not test_name.startswith("test_"):
            continue
        calls = (
            [(p,) for p in grader.SOURCES] if "path" in inspect.signature(fn).parameters else [()]
        )
        for args in calls:
            try:
                fn(*args)
            except AssertionError:
                return [test_name]
    return []


@pytest.mark.parametrize("tag", sorted(CONTROLS))
def test_positive_control_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CONTROLS[tag])


@pytest.mark.parametrize("tag", sorted(CASES))
def test_a_kept_rule_rejects_the_edit(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CASES[tag])
