# Probe from review of m11/01-lab-addon; reproduces the pet-GUID grader missing a ns.Fn lookup whose key is concatenated
"""`test_pet_rows_bind_only_species_and_owned` in tests/addon/test_lab_addon.py
finds GetPetInfoByIndex handles only through the literal string
"GetPetInfoByIndex". A second handle looked up through a variable key
(`local key = "GetPetInfo" .. "ByIndex"; ns.Fn(C_PetJournal, key)`) is not
seen, so its first return (the battle-pet GUID) can be stored. The
concatenation is outside `[...]`, so `test_sources_build_no_index_by_concatenation`
misses it; `_fn_lookups` needs a string literal, so the std/README checks miss
it. Appended to the real Collections.lua, every test in test_lab_addon.py
passes, and selene accepts it because C_PetJournal is declared.

A closing rule would be: every `ns.Fn(` call is `ns.Fn(<Name>, "<literal>")`.

Constructed inputs (boundary cases for the grader), not addon sources.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

GRADER = Path(__file__).resolve().parents[4] / "tests" / "addon" / "test_lab_addon.py"

LEGIT = """local _, ns = ...
local function pets()
    local byIndex = ns.Fn(C_PetJournal, "GetPetInfoByIndex")
    if not (byIndex) then
        return
    end
    local speciesID, owned = select(2, ns.Call(byIndex, 1))
    return speciesID, owned
end
"""

# Positive control: the literal-looked-up handle's first return is stored.
DIRECT = LEGIT + "WowLabCharDB = { pet = ns.Call(byIndex, 1) }\n"

# Evasion: a second handle, looked up by a concatenated key.
CONCATENATED = (
    LEGIT
    + 'local key = "GetPetInfo" .. "ByIndex"\n'
    + "local rows = ns.Fn(C_PetJournal, key)\n"
    + "WowLabCharDB = { pet = ns.Call(rows, 1) }\n"
)


def _grader() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cr97b_lab_addon_grader", GRADER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def _check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    grader = _grader()
    path = tmp_path / "Constructed.lua"
    path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(grader, "ROOT", tmp_path)
    monkeypatch.setattr(grader, "SOURCES", [path])
    grader.test_pet_rows_bind_only_species_and_owned()


def test_legit_shape_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _check(tmp_path, monkeypatch, LEGIT)


def test_positive_control_direct_guid_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(AssertionError):
        _check(tmp_path, monkeypatch, DIRECT)


def test_constructed_concatenated_lookup_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(AssertionError):
        _check(tmp_path, monkeypatch, CONCATENATED)
