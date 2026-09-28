# Probe from review of m10/04-luadata-parser; reproduces a flat `0;` list within the cost budget taking over 10 s, slower than the §6.4 table's "Slowest" shape.
"""CONSTRUCTED inputs (L8: boundary cases for the §6.4 cost budget).

`docs/LAB_PLAN.md` §6.4 (the 2026-09-23 amendment, rewritten after fix
round 2) says the budget keeps every document inside the target of 50 MB in
under 10 s. Its table names the slowest shape at the budget: wide trivia
with `{  }` values, 7.6 s on the owner's M1.

A shared positional entry is charged `_C_SHARED` (170), a figure calibrated
on the `_FAST` path. `0;` uses a `;` separator, which the grammar allows and
`_FAST` does not match. It is shared the same way and costs the same per
entry, but every entry takes the full `_ENTRY` path, which takes about 1.9
times as long. `\\v0,` (trivia `_FAST` does not accept) behaves the same.

Measured at the full budget with the branch's own bench (`generate_items`
and `_run`, a fresh interpreter each, rounds interleaved), on the owner's
M1 with the WoW client running:

    load ~4:   zeros `0,\\r\\n` 6,609,192 entries   5.78 s /  5.76 s
               semi `0;\\n`     6,647,396 entries  10.59 s / 10.85 s
               wide-tables (§6.4 "Slowest") 1,872,963  9.84 s /  9.59 s
    load 7-21: zeros                               6.72 s /  6.79 s
               semi                               13.01 s / 13.50 s
               wide-tables                        10.61 s / 10.95 s

This probe times, in a fresh interpreter, a parse of an over-full document
up to the point where the budget refuses it. That is the parse of one
budget's worth of the shape. It takes the better of two runs. Positive
control: the table's `0,` shape, measured the same way, is well under 10 s.

What is measured, and why (M11-16T, 2026-09-28). The figures above are wall
clock. The probe itself measures the child's CPU time: the child reads the
file first, then prints `time.process_time()` (user plus system time of
that process, every thread) taken around `luadata.parse` alone, so
interpreter start-up, the import and the file read are outside it. The
§6.4 target is a wall-clock figure for an idle laptop, where a
single-threaded parse that does no I/O runs for as long as it uses the CPU,
so the two agree there (measured idle on the owner's M1: `0,` 5.9 s CPU
against 6.1 s wall). Under load they part: wall clock also counts the time
the parse waits for a core, which says nothing about the parser, and made
this probe fail on busy review runs and pass alone. Measured under 8 and 16
spinning processes on the same M1: the `0,` control took 10.6 to 20.8 s
wall but 7.2 to 8.0 s CPU. CPU time still rises some under load (a slower
core or a lower clock), so under load the probe can only overstate the
parse, never flatter it. A constructed slow parse still fails: a parse that
does more work uses more CPU. Where the platform has no process clock
(`time.get_clock_info("process_time")` fails), the child falls back to
`time.perf_counter()`, wall clock, and says so in the failure message.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from wowlab_core import luadata

TARGET_SECONDS = 10.0

# Prints "<clock> <seconds>": the CPU time of the parse, or wall clock where
# the platform has no process clock.
CHILD = """
import sys, time
from wowlab_core import luadata
try:
    time.get_clock_info("process_time")
    clock, name = time.process_time, "CPU"
except (AttributeError, ValueError, OSError):
    clock, name = time.perf_counter, "wall-clock"
data = open(sys.argv[1], "rb").read()
started = clock()
try:
    luadata.parse(data)
except luadata.LuaLimitError:
    print(name, clock() - started)
else:
    print("parsed")
"""


def _seconds_to_the_budget(item: bytes, folder: Path) -> tuple[float, str]:
    # Over-full for a 170-per-entry charge: the budget refuses it part way.
    data = b"\r\nX = {\r\n" + item * (luadata.MAX_COST // 150) + b"}\r\n"
    path = folder / "Overfull.lua"
    path.write_bytes(data)
    best = float("inf")
    clock = ""
    for _ in range(2):
        out = subprocess.run(
            [sys.executable, "-c", CHILD, str(path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert out != "parsed", "the document should cross the budget"
        clock, seconds = out.split()
        best = min(best, float(seconds))
    return best, clock


def test_constructed_control_zeros_at_the_budget_are_inside_the_target(tmp_path: Path) -> None:
    took, clock = _seconds_to_the_budget(b"0,\r\n", tmp_path)
    assert took < TARGET_SECONDS, f"`0,` list at the budget: {took:.2f} {clock} s"


def test_constructed_semicolon_list_at_the_budget_is_inside_the_target(tmp_path: Path) -> None:
    took, clock = _seconds_to_the_budget(b"0;\n", tmp_path)
    assert took < TARGET_SECONDS, (
        f"`0;` list at the budget: {took:.2f} {clock} s, over the §6.4 target of "
        f"{TARGET_SECONDS:.0f} s (the amendment's slowest shape is 7.6 s)"
    )
