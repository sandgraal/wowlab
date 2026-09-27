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

CHILD = r"""
import resource, sys
from wowlab_core import luadata
item = bytes.fromhex(sys.argv[1])
size = int(sys.argv[2])
head = b"\r\nCONSTRUCTED_DB = {\r\n"
data = head + item * ((size - len(head)) // len(item)) + b"}\r\n"
scale = 1 if sys.platform == "darwin" else 1024
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
doc = luadata.parse(data)
after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
print(before, after)
"""


def _projected_peak(item: bytes) -> float:
    out = subprocess.run(
        [sys.executable, "-c", CHILD, item.hex(), str(SCALE_MIB * MIB)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    before, after = int(out[0]), int(out[1])
    factor = TARGET_MIB / SCALE_MIB
    # Interpreter and data at full size, plus the parse's growth scaled up.
    return before + (factor - 1) * SCALE_MIB * MIB + (after - before) * factor


def test_constructed_positional_array_of_small_integers_fits_the_rss_target() -> None:
    peak = _projected_peak(b"0,\r\n")
    assert peak < LIMIT_BYTES, f"projected peak RSS {peak / MIB:.0f} MiB for 50 MiB"


def test_constructed_positive_control_collection_shape_fits_the_rss_target() -> None:
    peak = _projected_peak(b"[100003] = true,\r\n")
    assert peak < LIMIT_BYTES, f"projected peak RSS {peak / MIB:.0f} MiB for 50 MiB"
