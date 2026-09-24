# Probe from review of m10/02-lab-capture-followup-5; reproduces the owner's own
# character and realm names passing unrefused in the spellings follow-up 5 closed for others.
"""Follow-up 5 closed the recorded `_fold` gap (full-width letters, tab, NBSP,
zero-width space, doubled separators) for the other-player hunt and the loose
second-name detector only. The owner's own character and realm names, the
tool's primary identity strings, still pass in exactly those spellings: the
file is written with the name in it and no refusal (run at 0d91991).

The ASCII spellings are the positive controls: they are rewritten today.
Constructed boundary input, invented names only; no install (ADR-0012).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "lab_capture.py"


def _load() -> ModuleType:
    name = "lab_capture_review_probe_followup5_owner_fold"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lab_capture = _load()


def _identity() -> object:
    return lab_capture.Identity(characters=["Orlavin"], realms=["Kestrel Hollow"])


def _scrub(text: str) -> object:
    return _identity().scrub(('Notes = "' + text + '"\n').encode("utf-8"))


@pytest.mark.parametrize(
    "text",
    ["ORLAVIN waves", "Kestrel Hollow"],
    ids=["ascii-casing", "ascii-realm"],
)
def test_control_ascii_spellings_are_rewritten(text: str) -> None:
    result = _scrub(text)
    assert not result.problems
    assert b"orlavin" not in result.data.lower() and b"kestrel" not in result.data.lower()


@pytest.mark.parametrize(
    "text",
    [
        "\uff2f\uff52\uff4c\uff41\uff56\uff49\uff4e waves",  # full-width letters
        "Orla\u200bvin waves",  # zero-width space inside the name
        "Kestrel\u00a0Hollow",  # NBSP where the realm has its space
        "Kestrel\tHollow",  # tab where the realm has its space
        "Kestrel  Hollow",  # doubled separator
    ],
    ids=["full-width-character", "zwsp-character", "nbsp-realm", "tab-realm", "doubled-realm"],
)
def test_owner_name_in_a_follow_up_5_spelling_is_refused(text: str) -> None:
    result = _scrub(text)
    assert result.problems, "the owner's name survives unrefused in this spelling"
