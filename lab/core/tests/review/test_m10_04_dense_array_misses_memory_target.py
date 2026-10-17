# Probe from review of m10/04-luadata-parser; reproduces a 50 MB Forever-layout
# positional array of small integers missing the §6.4 RSS target (2.7 GB).
"""CONSTRUCTED input (L8: a boundary case of the §6.4 performance target).

`docs/LAB_PLAN.md` §6.4: "a 50 MB SavedVariables file ... parses in under
10 s and under 1.5 GB RSS". The Forever client writes positional entries
with no indentation and no `-- [n]` comment, one per CRLF line
(`docs/LAB_FORMATS.md` §4.2 amendment of 2026-09-22), so an array of small
integers costs four bytes an entry: `0,\\r\\n`. At 50 MiB that is 13.1
million entries, and the parser keeps about 210 bytes per entry (a 10-field
`Entry` tuple plus a `LuaNumber` tuple). Measured by hand on the reviewer's
laptop, full size, fresh interpreter:

    50 MiB of b'0,\\r\\n':    13,107,197 entries, parse 13.39 s, peak RSS 2740 MiB
    50 MiB of b'true,\\r\\n':  7,489,827 entries, parse  7.71 s, peak RSS 1619 MiB

This probe measures the same shape at 5 MiB in a child interpreter and
scales the parse's RSS growth linearly to 50 MiB (the per-entry cost is
constant). The positive control is the bench's own `collection` shape
(`[100003] = true,\\r\\n`, 18 bytes an entry), which the same measurement
passes (about 900 MiB projected). The bench's `ids` shape with a fixed
six-digit value (`100000,\\r\\n`) projects to about 1480 MiB, just under.
Only memory is asserted; timing on a CI runner is not the owner's laptop.
A fix that stays in pure Python: share immutable values and entries that
are byte-identical (a `LuaBool`, a small-integer `LuaNumber` with the same
lead, and the positional `Entry` built from them) instead of allocating a
fresh pair per entry.

How the child measures (M13-20T, the fix M13-17T made to its sibling probe):

- On Linux `ru_maxrss` is kept across `execve` (getrusage(2)), so a child of
  a large pytest process starts at the parent's high-water mark and a parse
  below it reads as no growth: the projection was then the parent plus
  45 MiB and passed without measuring anything. The child reads its own
  `VmHWM` from `/proc/self/status` on Linux, which is new at exec.
- The slice is built so that nothing is freed between it and the parse.
  `before` is a high-water mark: a freed temporary (`item * n`, then the
  concatenation) is counted in it, and when the parse reuses those pages
  its growth reads low by up to two slices, twenty projected. `body` is
  kept alive and `data` is one join, so `before` counts one unscaled extra
  slice of input (5 MiB), which only makes the projection stricter.
- `after > before` is asserted: a child that sees no growth is measuring
  something other than the parse. The last test runs a constructed
  stand-in that keeps two parses and checks that the measured growth
  follows it. Readings on macOS (M-series), at 5 MiB, `luadata` then the
  stand-in: small integers grew 21 and 32 MiB, the collection 71 and
  140 MiB; projected to 50 MiB, `luadata` reads about 300 and 800 MiB.
  On Linux (Python 3.12 in a container, launched from a parent holding
  900 MiB), the `ru_maxrss` child read 943 MiB before and after for both
  shapes, no growth and 988 MiB projected; the `VmHWM` child grew 20 and
  30 MiB, and 71 and 141 MiB.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.parser,
    pytest.mark.skipif(sys.platform == "win32", reason="uses the resource module"),
]

MIB = 1024 * 1024
SCALE_MIB = 5
TARGET_MIB = 50
LIMIT_BYTES = 1.5 * 1024 * MIB  # 1.5 GB read as GiB, the generous reading

# A parser that keeps a second copy of its result must read at least this
# many times `luadata`'s growth. Measured on macOS: 1.5 for small integers
# (the first parse's temporaries are freed before the second), 2.0 for the
# collection shape.
STAND_IN_RATIO = 1.25
# On a loaded Mac the system compresses a child's pages as it allocates them,
# and `ru_maxrss` counts resident pages only, so a reading can come out low
# (3 MiB of growth for small integers once, at load average 88, against an
# idle 21). That noise only lowers readings, and the fault this guards
# against (an inherited high-water mark) reads no growth every time, so the
# stand-in passes on the first of up to this many rounds that shows it.
STAND_IN_ROUNDS = 3

CHILD = r"""
import resource, sys
from wowlab_core import luadata
item = bytes.fromhex(sys.argv[1])
size = int(sys.argv[2])
parser = sys.argv[3]
parse = luadata.parse
if parser == "double":
    # CONSTRUCTED stand-in, a test of the probe itself: a parser that keeps
    # a second, independent copy of its result, so it holds twice the memory.
    def parse(data):
        return luadata.parse(data), luadata.parse(data)
head = b"\r\nCONSTRUCTED_DB = {\r\n"
# Kept alive through the parse, and joined once: a freed temporary is
# already in the `before` high-water mark, and the parse reusing its pages
# would hide that much growth.
body = item * ((size - len(head)) // len(item))
data = b"".join((head, body, b"}\r\n"))
def peak():
    # This process's peak RSS in bytes. On Linux `ru_maxrss` is kept across
    # execve (getrusage(2)), so a child of a large pytest process starts at
    # the parent's high-water mark and a parse below it reads as no growth.
    # VmHWM is this process's own address space, new at exec.
    if sys.platform.startswith("linux"):
        with open("/proc/self/status") as status:
            for line in status:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
        raise SystemExit("no VmHWM in /proc/self/status")
    scale = 1 if sys.platform == "darwin" else 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
before = peak()
doc = parse(data)
after = peak()
print(before, after)
"""


def _readings(item: bytes, parser: str = "luadata") -> tuple[int, int]:
    """The child's peak RSS before and after the parse, in bytes."""
    out = subprocess.run(
        [sys.executable, "-c", CHILD, item.hex(), str(SCALE_MIB * MIB), parser],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    before, after = int(out[0]), int(out[1])
    assert after > before, (
        f"the child read no growth ({before / MIB:.1f} -> {after / MIB:.1f} MiB): "
        "it is not measuring the parse"
    )
    return before, after


def _projected_peak(item: bytes, parser: str = "luadata") -> float:
    before, after = _readings(item, parser)
    factor = TARGET_MIB / SCALE_MIB
    # Interpreter and data at full size, plus the parse's growth scaled up.
    return before + (factor - 1) * SCALE_MIB * MIB + (after - before) * factor


def test_constructed_positional_array_of_small_integers_fits_the_rss_target() -> None:
    peak = _projected_peak(b"0,\r\n")
    assert peak < LIMIT_BYTES, f"projected peak RSS {peak / MIB:.0f} MiB for 50 MiB"


def test_constructed_positive_control_collection_shape_fits_the_rss_target() -> None:
    peak = _projected_peak(b"[100003] = true,\r\n")
    assert peak < LIMIT_BYTES, f"projected peak RSS {peak / MIB:.0f} MiB for 50 MiB"


@pytest.mark.parametrize(
    "item",
    [b"0,\r\n", b"[100003] = true,\r\n"],
    ids=["constructed-small-integers", "constructed-collection"],
)
def test_constructed_stand_in_holding_twice_the_memory_reads_more_growth(item: bytes) -> None:
    """The measurement follows the parser, not the process that launched it.

    CONSTRUCTED stand-in, not `luadata`: it parses the slice twice and keeps
    both results. Both shapes project well under the target, so a parser
    holding twice as much can still fit it and the target is not what this
    asserts. What it asserts is that the child sees the second copy: its
    growth must be at least `STAND_IN_RATIO` times `luadata`'s. Under the
    inherited `ru_maxrss` both read no growth, and this fails.
    """
    rounds = []
    for _ in range(STAND_IN_ROUNDS):
        before, after = _readings(item)
        double_before, double_after = _readings(item, "double")
        growth, double = after - before, double_after - double_before
        rounds.append(f"{growth / MIB:.1f} and {double / MIB:.1f}")
        if double >= STAND_IN_RATIO * growth:
            return
    pytest.fail(
        f"a parser holding twice the memory never grew {STAND_IN_RATIO} times what "
        f"luadata did (MiB, luadata and stand-in: {'; '.join(rounds)}); "
        "the probe cannot see it"
    )
