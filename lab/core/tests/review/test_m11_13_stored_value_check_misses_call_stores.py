# Probe from review of m11/13-addon-static-hardening; reproduces the stored-value checks passing a whole API table stored with table.insert, and a gather returning one through a non-literal ns.Section call
"""M11-13 added a stored-value taint check and a gather/carry return check to
tests/addon/test_lab_addon.py, "so a whole API table can never be written".
Three ordinary edits to a copy of the real addon pass every test in that file:

- in Talents.lua's `treeCurrencies`, `table.insert(out, currency)` in place of
  the field-by-field copy: `currency` is a table from
  C_Traits.GetTreeCurrencyInfo, and it is written whole. The check only sees
  `=` assignments and constructor entries, not a store made through a call;
- in Professions.lua, the section spec bound to a local and passed as
  `ns.Section(spec)`, with `gather` returning the ns.Pack of an API call:
  test_every_gather_and_carry_returns_checked_values only looks for the tokens
  `ns . Section ( {`, so this section's gather is never checked;
- the same gather with the paren-less call `ns.Section{ ... }`.

Controls: a direct `out[#out + 1] = currency`, and the same gather return in
the literal `ns.Section({ ... })` call, are rejected. Each case checks that at
least one test in the grader fails. Constructed inputs (boundary cases for the
grader), not addon sources.
"""

from __future__ import annotations

import importlib.util
import inspect
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[4]
GRADER = REPO / "tests" / "addon" / "test_lab_addon.py"

CURRENCY_COPY = """                out[#out + 1] = {
                    id = ns.Number(currency.traitCurrencyID),
                    quantity = ns.Number(currency.quantity),
                    max_quantity = ns.Number(currency.maxQuantity),
                    spent = ns.Number(currency.spent),
                }
"""
GATHER_RETURN = "return { list = list }"
TAINTED_RETURN = "return slots"  # slots = ns.Pack(GetProfessions())

Edit = tuple[str, Callable[[str], str]]


def _spec_local(s: str) -> str:
    return s.replace("ns.Section({", "local spec = {").replace("\n})\n", "\n}\nns.Section(spec)\n")


def _paren_less(s: str) -> str:
    return s.replace("ns.Section({", "ns.Section{").replace("\n})\n", "\n}\n")


def _tainted_return(s: str) -> str:
    return s.replace(GATHER_RETURN, TAINTED_RETURN)


CASES: dict[str, Edit] = {
    "constructed-table-insert-api-table": (
        "Talents.lua",
        lambda s: s.replace(CURRENCY_COPY, "                table.insert(out, currency)\n"),
    ),
    "constructed-section-spec-in-a-local": (
        "Professions.lua",
        lambda s: _tainted_return(_spec_local(s)),
    ),
    "constructed-section-paren-less-call": (
        "Professions.lua",
        lambda s: _tainted_return(_paren_less(s)),
    ),
}
CONTROLS: dict[str, Edit] = {
    "control-direct-store-api-table": (
        "Talents.lua",
        lambda s: s.replace(CURRENCY_COPY, "                out[#out + 1] = currency\n"),
    ),
    "control-literal-section-tainted-return": ("Professions.lua", _tainted_return),
}


def _grader(root: Path, tag: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"cr106_grader_{tag}", GRADER)
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
    name, change = edit
    target = tmp_path / "lab" / "addon" / "WowLab" / name
    before = target.read_text(encoding="utf-8")
    after = change(before)
    assert after != before, "the edit did not apply; the addon source moved"
    target.write_text(after, encoding="utf-8")
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
def test_ordinary_edit_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CASES[tag])
