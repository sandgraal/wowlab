# Probe from review of m10/16-guard-tests-2; reproduces the UNC LookupSpy
# marker missing every Windows-spelled lookup of the forged UNC path.
"""`test_guard_pins_m10_16.py` (fix round 1, 6f7fc2a) says `_marker` keeps
"the distinctive part, whichever separator a lookup spells it with". For the
UNC case it returns `constructed-server.invalid/share`: the forward slash
survives. On Windows guard looks paths up through `pathlib`, which spells
`//constructed-server.invalid/share/World of Warcraft` as
`\\\\constructed-server.invalid\\share\\World of Warcraft`, so `LookupSpy`
never records a lookup of it there, and the `unc` cases of
`test_constructed_pin_paths_from_the_store_are_looked_up_only_when_local_absolute`
cannot fail on `lab (windows)` for the reason they exist.

Constructed: no guard call, no install; `PureWindowsPath` gives the Windows
spelling on every platform. Positive controls: the same spy records the
record's own (forward-slash) spelling, and the `relative` and
`rooted-without-a-drive` markers match their Windows spellings.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path, PureWindowsPath

import pytest


def _pins() -> types.ModuleType:
    path = Path(__file__).resolve().parent.parent / "test_guard_pins_m10_16.py"
    spec = importlib.util.spec_from_file_location("_review_m10_16t_pins", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


P = _pins()


def _windows_spellings(kind: str) -> list[PureWindowsPath]:
    root = PureWindowsPath(P.NOT_LOCAL[kind])
    return [root, root / P.FLAVOR_FOLDER]


@pytest.mark.parametrize(
    "kind",
    [
        pytest.param("relative", id="constructed-positive-control-relative"),
        pytest.param("rooted-without-a-drive", id="constructed-positive-control-rooted"),
    ],
)
def test_constructed_markers_match_their_windows_spelling(kind: str) -> None:
    spy = P.LookupSpy(P._marker(kind))
    for spelled in _windows_spellings(kind):
        assert spy._watched(spelled), f"{kind}: {spelled} is not watched"


def test_constructed_unc_marker_matches_the_records_own_spelling() -> None:
    """Positive control: the spy does see the forward-slash spelling."""
    spy = P.LookupSpy(P._marker("unc"))
    assert spy._watched(P.NOT_LOCAL["unc"])
    assert spy._watched(f"{P.NOT_LOCAL['unc']}/{P.FLAVOR_FOLDER}")


def test_constructed_unc_marker_matches_the_windows_spelling_of_a_lookup() -> None:
    spy = P.LookupSpy(P._marker("unc"))
    missed = [str(s) for s in _windows_spellings("unc") if not spy._watched(s)]
    assert missed == [], (
        f"marker {P._marker('unc')!r} misses the Windows spelling of {missed}; "
        "a UNC lookup on Windows would go unrecorded"
    )
