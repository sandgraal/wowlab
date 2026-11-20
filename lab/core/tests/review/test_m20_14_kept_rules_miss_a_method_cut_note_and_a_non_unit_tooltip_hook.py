# Probe from review of m20/14-crew-tooltip-note; reproduces a note cut instead of refused through `:sub`, and the tooltip hook moved off unit tooltips, each passing every addon check
"""Two kept rules of M20-14 that the addon checks no longer hold:

1. Refused, never cut (§20.1 "Limits", ADR-0036's second note of
   2026-11-20). The old note-rule check in tests/addon/test_crew_book.py
   was `not re.search(r"\\bsub\\s*\\(", text)`, which also matched the method
   form `:sub(`. Its replacement counts only `" . sub ("` in the rendered
   tokens, so `tidy = tidy:sub(1, NOTE_MAX_BYTES)` added before the refusal
   in the slash handler (a long note is now cut to 2,000 bytes, possibly
   inside a character, and stored, never refused) passes every check.
   Control: the same cut written `string.sub(tidy, 1, NOTE_MAX_BYTES)` is
   rejected.
2. The hook is "for unit tooltips" (§20.8, overturned decisions 2 and 8).
   tests/addon/test_crew_tooltip.py pins the install call
   `pcall(TooltipDataProcessor.AddTooltipPostCall, unitType, onTooltip)`
   but not `unitType`'s value, and `Enum.TooltipDataType` is `any: true` in
   wow_client.yml, so `local unitType = Enum.TooltipDataType.Item` passes:
   the line is added to item tooltips whenever a kept player is the
   mouseover unit. Control: a second AddTooltipPostCall is rejected.

Each case is run against every Crew.lua-reading test of test_crew_tooltip.py,
test_crew_book.py and test_lab_addon.py on a copy of the addon. Constructed
inputs (boundary cases for the checks), not addon sources; no Lua is run.
The book's saved variable is never spelled here.
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
TESTS = REPO / "tests" / "addon"

Edits = list[tuple[str, str]]  # (text found exactly once in Crew.lua, its replacement)

TIDY = "    local tidy = cleanNote(text)\n"
UNIT_TYPE = "    local unitType = Enum.TooltipDataType.Unit\n"
INSTALL = "    if not pcall(TooltipDataProcessor.AddTooltipPostCall, unitType, onTooltip) then\n"

CASES: dict[str, Edits] = {
    "constructed-note-cut-by-method-sub": [
        (TIDY, TIDY + "    tidy = tidy:sub(1, NOTE_MAX_BYTES)\n"),
    ],
    "constructed-hook-on-item-tooltips": [
        (UNIT_TYPE, "    local unitType = Enum.TooltipDataType.Item\n"),
    ],
}
CONTROLS: dict[str, Edits] = {
    "control-note-cut-by-string-sub": [
        (TIDY, TIDY + "    tidy = string.sub(tidy, 1, NOTE_MAX_BYTES)\n"),
    ],
    "control-second-hook": [
        (INSTALL, "    pcall(TooltipDataProcessor.AddTooltipPostCall, 1, onTooltip)\n" + INSTALL),
    ],
}

# The graders' own mutation loops cost most of a pass; they run last.
SELF_TESTS = frozenset(
    {
        "test_m11_37_a_call_added_outside_a_section_fails",
        "test_m11_37_a_call_added_inside_a_section_passes",
        "test_m13_05_constructed_secret_edits_fail",
        "test_m20_13_each_kept_rule_fails_its_own_edit",
        "test_m20_14_each_kept_rule_fails_its_own_edit",
    }
)


def _load(name: str, tag: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"m20_14_{tag}_{name}", TESTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def _graders(root: Path, tag: str) -> tuple[ModuleType, list[ModuleType]]:
    g = _load("test_lab_addon", tag)
    g.ROOT = root
    g.ADDON_ROOT = root / "lab" / "addon"
    g.ADDON = g.ADDON_ROOT / "WowLab"
    for attr, file in (
        ("ACTIONS", "Actions.lua"),
        ("IDENTITY", "Identity.lua"),
        ("TALENTS", "Talents.lua"),
        ("TOC", "WowLab.toc"),
    ):
        if hasattr(g, attr):
            setattr(g, attr, g.ADDON / file)
    g.STD = g.ADDON_ROOT / "wow_client.yml"
    g.README = g.ADDON_ROOT / "README.md"
    g.SOURCES = sorted(g.ADDON.glob("*.lua"))
    saved = sys.modules.get("test_lab_addon")
    sys.modules["test_lab_addon"] = g  # the other two import it by name
    try:
        others = [_load(name, tag) for name in ("test_crew_tooltip", "test_crew_book")]
    finally:
        if saved is not None:
            sys.modules["test_lab_addon"] = saved
        else:
            del sys.modules["test_lab_addon"]
    return g, others


def _failing_tests(tmp_path: Path, tag: str, edits: Edits) -> list[str]:
    shutil.copytree(REPO / "lab" / "addon", tmp_path / "lab" / "addon")
    target = tmp_path / "lab" / "addon" / "WowLab" / "Crew.lua"
    text = target.read_text(encoding="utf-8")
    for found, replacement in edits:
        assert text.count(found) == 1, "the edit did not apply; Crew.lua moved"
        text = text.replace(found, replacement)
    target.write_text(text, encoding="utf-8")
    g, others = _graders(tmp_path, tag)
    # The two M20-14 files first: they are quick, and a rejection ends the loop.
    for module in (*others, g):
        members = inspect.getmembers(module, inspect.isfunction)
        for name, fn in sorted(members, key=lambda m: m[0] in SELF_TESTS):
            if not name.startswith("test_") or fn.__module__ != module.__name__:
                continue
            params = inspect.signature(fn).parameters
            if "path" in params:
                calls: list[tuple[object, ...]] = [(p,) for p in g.SOURCES]
            elif params:
                continue  # fixture-taking tests are not about Crew.lua's text
            else:
                calls = [()]
            for args in calls:
                try:
                    fn(*args)
                except AssertionError:
                    return [f"{module.__name__}.{name}"]
    return []


@pytest.mark.parametrize("tag", sorted(CONTROLS))
def test_positive_control_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CONTROLS[tag])


@pytest.mark.parametrize("tag", sorted(CASES))
def test_a_kept_rule_rejects_the_edit(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CASES[tag])
