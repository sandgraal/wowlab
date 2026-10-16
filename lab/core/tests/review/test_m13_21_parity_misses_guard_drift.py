# Probe from review of m13/21-verdict-in-core; reproduces that the restorable/gate parity test runs over a fixed path list and so cannot see a change to guard's executable suffixes or reserved names.
"""`test_verdict.py::test_restorable_agrees_with_the_write_gate` is meant to
fail when `verdict._gate_writes` and `guard._check_rel` drift apart. It runs
over `GATE_PATHS`, a fixed list of 47 constructed paths. A change to guard's
tables that no listed path exercises (a new executable suffix, a new reserved
device name) leaves every listed case agreeing, so the parity test passes
while `restorable` now says yes where the gate says no.

Each test below mutates guard the way a future ticket might, reloads the
grader's table (so a table generated from guard's tables sees the change),
replays the parity test's assertion over it, and asserts the drift is
caught. The positive control mutates guard in a way a listed path does
exercise, and is caught. Constructed inputs; no filesystem.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from wowlab_core import guard
from wowlab_core.verdict import restorable

_GRADER = Path(__file__).resolve().parents[1] / "test_verdict.py"


def _parity_table() -> list[str]:
    """`test_verdict.GATE_PATHS`, loaded fresh, so a table generated from
    guard's own tables at import time sees the mutation."""
    spec = importlib.util.spec_from_file_location("_m13_21_test_verdict", _GRADER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.GATE_PATHS)


def _drift_seen_by_parity_table() -> list[str]:
    """The paths of the parity table on which verdict and guard disagree."""
    seen = []
    for path in _parity_table():
        try:
            guard._check_rel(path)
        except guard.PathNotAllowedError:
            gate = False
        else:
            gate = True
        if restorable("flavor", path) is not gate:
            seen.append(path)
    return seen


def test_positive_control_a_drift_on_a_listed_case_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(guard, "_EXECUTABLE_SUFFIXES", (*guard._EXECUTABLE_SUFFIXES, ".tga"))
    assert _drift_seen_by_parity_table() == ["Interface/AddOns/Foo/media/a b.tga"]


@pytest.mark.parametrize(
    ("attr", "extra", "drifted"),
    [
        ("_EXECUTABLE_SUFFIXES", ".py", "Interface/AddOns/Foo/tool.py"),
        ("_EXECUTABLE_SUFFIXES", ".appimage", "Interface/AddOns/Foo/x.AppImage"),
        ("_RESERVED_NAMES", "clock$", "WTF/CLOCK$.txt"),
    ],
    ids=["constructed-suffix-py", "constructed-suffix-appimage", "constructed-reserved-clock"],
)
def test_parity_table_sees_a_guard_change(
    monkeypatch: pytest.MonkeyPatch, attr: str, extra: str, drifted: str
) -> None:
    current = getattr(guard, attr)
    mutated = current | {extra} if isinstance(current, frozenset) else (*current, extra)
    monkeypatch.setattr(guard, attr, mutated)
    # The drift is real: the gate now refuses a path verdict still calls restorable.
    with pytest.raises(guard.PathNotAllowedError):
        guard._check_rel(drifted)
    assert restorable("flavor", drifted) is True
    # The parity test should notice. It does not: its table has no such path.
    assert _drift_seen_by_parity_table(), (
        f"guard.{attr} gained {extra!r}; restorable({drifted!r}) disagrees with the gate, "
        "and the parity table has no case that shows it"
    )
