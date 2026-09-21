# Probe from review of m10/02-lab-capture (round 3); reproduces the owner's own
# upper-cased non-ASCII name on an own realm being refused as a foreign player.
"""Supersedes `test_constructed_upper_cased_non_ascii_own_name_is_not_a_foreign_player`
in `test_review_m10_02_round2_protected_spans.py`.

That test pinned the pseudonym's spelling ("Labchára"), which the conductor has
since decided against: pseudonyms keep separators but always use the ASCII stem.
The property it guarded does not depend on the spelling, so it is restated here
without naming any pseudonym: an own `Name - Realm` pair, in any casing, comes
out with nothing real left, nothing refused, and only ASCII where the identity
stood. Constructed boundary input, invented names, no install (ADR-0012).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "lab_capture.py"
CHARACTER = "Zoë"
REALM = "Area 52"


def _load() -> ModuleType:
    name = "lab_capture_review_probe_round3"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lab_capture = _load()


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(f'"{CHARACTER} - {REALM}"', id="constructed-as-the-folders-spell-it"),
        pytest.param(f'"{CHARACTER.upper()} - {REALM.upper()}"', id="constructed-upper-cased"),
        pytest.param(f'"{CHARACTER.lower()}-area52"', id="constructed-lower-cased-normalised"),
    ],
)
def test_constructed_own_non_ascii_name_in_any_casing_is_scrubbed_and_not_refused(
    text: str,
) -> None:
    identity = lab_capture.Identity(characters=[CHARACTER], realms=[REALM])

    # Positive control: someone else beside the same realm is still refused.
    assert identity.scrub(f'"Bystander - {REALM}"'.encode()).problems

    result = identity.scrub(text.encode("utf-8"))
    assert not result.problems, "the owner's own pair was refused"
    folded = result.data.decode("utf-8").casefold()
    assert CHARACTER.casefold() not in folded and "area" not in folded
    # The ASCII stem: no accent is carried into the pseudonym (conductor decision,
    # security review N6), so byte-level detectors never need non-ASCII case folding.
    assert result.data.isascii(), "the pseudonym carries a non-ASCII letter"
