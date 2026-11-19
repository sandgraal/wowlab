# Probe from review of m19/04-capture-refuses-crew; reproduces a filled crew book in docs/ passing the scan when its keys' realms hold a space or a hyphen
"""CONSTRUCTED INPUT, in memory; nothing is written to the tree.

In a checked place (`docs/`, non-source `lab/addon/`), an assignment the
capture tool would refuse passes when no quoted key is "player-shaped". The
shape is one hyphen between two single words, so a key whose realm holds a
space or a hyphen (whether the client normalises cross-realm realms is
**[verify]** in `docs/LAB_PLAN.md` §20.1) is not seen as a player key, and a
real book made of such keys passes. Every invented name here is fake.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[4]
# The name is assembled at run time so this file does not trip the scan itself.
NAME = b"WowLab" + b"CrewDB"


def _book(key: str) -> bytes:
    entry = b'["' + key.encode() + b'"] = {\r\n["note"] = "x",\r\n},\r\n'
    return b"\r\n" + NAME + b' = {\r\n["players"] = {\r\n' + entry + b"},\r\n}\r\n"


def _scan_module() -> ModuleType:
    path = ROOT / "tests" / "repo" / "test_crew_book_out_of_repo.py"
    spec = importlib.util.spec_from_file_location("m19_04_crew_scan_probe_realm", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_positive_control_a_one_word_realm_is_flagged() -> None:
    assert _scan_module().scan([("docs/x.md", _book("Fakeperson-Fakeland"))], index=b"")


@pytest.mark.parametrize("key", ["Fakeperson-Fake Land", "Fakeperson-Fake-Land"])
def test_a_realm_with_a_space_or_hyphen_is_flagged(key: str) -> None:
    assert _scan_module().scan([("docs/x.md", _book(key))], index=b"")
