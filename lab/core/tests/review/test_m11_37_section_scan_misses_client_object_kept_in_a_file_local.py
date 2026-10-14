# Probe from review of m11/37-bidi-api-sections; reproduces a client object made in section code, kept in a file-level local through a helper's return or parameter, and called outside every section
"""M11-37's section scan (tests/addon/test_lab_addon.py, `_SectionScan`) at
round 3 (5f5030b). The grader's comment defines a *handle* as "a file-level
local that is ever bound or assigned a value holding a client global, a
handle, or a local that holds either", and each use of a handle outside a
section is a reference rule 1 counts. `_handles` reads only the tokens of the
local's own sources, so a client object that crosses a function boundary on
its way to the file-level local is not seen:

- a section helper returns it (`scanner = scanner or newScanner()`, where
  `newScanner` returns `CreateFrame("GameTooltip")`): the lazily created
  scanning tooltip, a common addon pattern;
- a section helper takes it as a parameter and stores it (`keep(made)`);
- a section helper returns it second of two (`local ok, made = make()`).

Then `scanner:Hide()` in an `ns` function that Core.lua's PLAYER_LOGOUT
handler calls is a client call outside every section, and it still runs after
`/wowlab skip gear` (the gather ran earlier in the session, so `scanner` is
set). No test in the grader fails. This is not the review-only case of an
object stored in a table (ns.state, a record): that one is refused by
test_every_stored_value_is_type_checked. The given-value analysis already
follows returns and parameters (`returns_given`, call-site arguments); the
handle analysis does not.

Control: the same object assigned with the client global in the local's own
source (`scanner = scanner or CreateFrame("GameTooltip")`) is rejected.

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

GEAR_TOP = "local _, ns = ...\n"
GEAR_GATHER = "    gather = function()\n        local first, last"
LOGOUT = 'ns.On("PLAYER_LOGOUT", function()\n'
HIDE = "function ns.HideScanner()\n    if scanner then\n        scanner:Hide()\n    end\nend\n"
CALL_AT_LOGOUT = ("Core.lua", LOGOUT, LOGOUT + "    ns.HideScanner()\n")


def _gather(lines: str) -> tuple[str, str, str]:
    return (
        "Gear.lua",
        GEAR_GATHER,
        "    gather = function()\n" + lines + "        local first, last",
    )


# (file, text found exactly once, its replacement)
Edit = tuple[str, str, str]

CASES: dict[str, list[Edit]] = {
    "constructed-helper-returns-the-object": [
        (
            "Gear.lua",
            GEAR_TOP,
            GEAR_TOP
            + "local scanner = nil\n"
            + "local function newScanner()\n"
            + '    return CreateFrame("GameTooltip")\n'
            + "end\n"
            + HIDE,
        ),
        _gather("        scanner = scanner or newScanner()\n"),
        CALL_AT_LOGOUT,
    ],
    "constructed-helper-stores-its-parameter": [
        (
            "Gear.lua",
            GEAR_TOP,
            GEAR_TOP
            + "local scanner = nil\n"
            + "local function keep(made)\n"
            + "    scanner = made\n"
            + "end\n"
            + HIDE,
        ),
        _gather('        keep(CreateFrame("GameTooltip"))\n'),
        CALL_AT_LOGOUT,
    ],
    "constructed-helper-returns-it-second": [
        (
            "Gear.lua",
            GEAR_TOP,
            GEAR_TOP
            + "local scanner = nil\n"
            + "local function make()\n"
            + '    return true, CreateFrame("GameTooltip")\n'
            + "end\n"
            + HIDE,
        ),
        _gather("        local ok, made = make()\n        scanner = made\n"),
        CALL_AT_LOGOUT,
    ],
}
CONTROLS: dict[str, list[Edit]] = {
    "control-client-global-in-the-locals-own-source": [
        ("Gear.lua", GEAR_TOP, GEAR_TOP + "local scanner = nil\n" + HIDE),
        _gather('        scanner = scanner or CreateFrame("GameTooltip")\n'),
        CALL_AT_LOGOUT,
    ],
}


def _grader(root: Path, tag: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"m11_37_file_local_grader_{tag.replace('-', '_')}", GRADER
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
        # The grader's own constructed-case tests re-apply their edits on top
        # of this copy (its controls then fail on this edit's failure, which
        # would count a missed case as caught); they say nothing about it.
        if test_name.startswith("test_m11_37_a_call_added_"):
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
def test_client_object_kept_in_a_file_local_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CASES[tag])
