# Probe from review of m11/30-terminal-safe-errors; reproduces a refused `addon remove lab` with ordinary file names printing its multi-line refusal as one line joined by literal `\x0a` text
"""A refused `addon remove lab` is a multi-line message by design
(`addoninstall.plan_remove`: every refused path with the gate's reason, one
per line, then `REMOVE_REFUSED_NOTE`, which speaks of "the path(s) named
above", M11-17). On main, `_fail` kept those line breaks. M11-30's `_fail`
passes the whole error text as a value, so every line break in it,
including the library's own, is escaped: with nothing hostile in the tree,
stderr becomes one line with `\\x0a` between the refused paths and before
the note.

The ticket asks `_note` to escape line breaks "that come from inserted
values while keeping the ones the message itself contains"; here the line
breaks are the library's, and the values (`helper.dll`, `tool.exe`) hold
none. `_handled` now prints an exception's `__notes__` one line each, so the
library can carry its extra lines safely as notes.

Positive control: the library's refusal holds one part per refused path
plus the note (in its text on main, or in its text and notes after a fix),
and no refused name holds a line break, so what is lost is the library's
line structure, not a hostile value's. A guard case pins what any fix must
keep (it passes on this branch today): a refused path whose name holds a
line feed never starts a line of its own (the gate names paths with
`repr`, so it shows as `\\n`; `_safe` would show `\\x0a`).

Constructed: `helper.dll` and `tool.exe` (which the gate refuses to delete)
in the installed lab-addon folder of the captured tree copied into
`tmp_path`, and for the guard case a `.dll` whose name holds a line feed.
Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    FLAVOR,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import addoninstall, guard, install

LAB = "Interface/AddOns/WowLab"
PLANTED = ("helper.dll", "tool.exe")
HOSTILE = "evil\nwowlab: nothing was refused.dll"


def _plant(root: Path, names: tuple[str, ...]) -> None:
    ok("addon", "install", "lab", "--yes")
    for name in names:
        (root / FLAVOR / LAB / name).write_bytes(b"constructed, not an executable")


def test_positive_control_the_library_refusal_has_its_own_lines_constructed(
    root: Path,
) -> None:
    _plant(root, PLANTED)
    inst = install.discover(root)
    chosen = next(f for f in inst.flavors if f.folder == FLAVOR)
    with pytest.raises(guard.GuardError) as refused:
        addoninstall.plan_remove(chosen)
    parts = [*str(refused.value).split("\n"), *getattr(refused.value, "__notes__", [])]
    assert len(parts) == len(PLANTED) + 1, parts
    assert all("\n" not in name for name in PLANTED)


def test_a_refused_remove_keeps_the_library_line_breaks_constructed(root: Path) -> None:
    _plant(root, PLANTED)
    result = run("addon", "remove", "lab", "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    lines = result.stderr.rstrip("\n").split("\n")
    assert "\\x0a" not in result.stderr, result.stderr
    assert len(lines) >= len(PLANTED) + 1, lines
    assert any(f"'{LAB}/tool.exe' is an executable" in line for line in lines[1:]), lines


def test_guard_a_refused_path_holding_a_line_break_stays_escaped_constructed(
    root: Path,
) -> None:
    _plant(root, (HOSTILE,))
    result = run("addon", "remove", "lab", "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert (
        "evil\\nwowlab: nothing was refused.dll" in result.stderr
        or "evil\\x0awowlab: nothing was refused.dll" in result.stderr
    ), result.stderr
    lines = result.stderr.split("\n")
    assert not any(line.startswith("wowlab: nothing") for line in lines), lines
