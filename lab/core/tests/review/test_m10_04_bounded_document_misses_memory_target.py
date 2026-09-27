# Probe from review of m10/04-luadata-parser (fix round 1); reproduces a document
# within every bound (MAX_ENTRIES entries) projecting to about 2.5 GB peak RSS.
"""CONSTRUCTED input (L8: a boundary case of the §6.4 performance target).

The §6.4 amendment of 2026-09-23 says: "The performance target holds for any
document within the bounds." The bound on memory is `MAX_ENTRIES` table
entries, chosen from shapes that cost at most about 330 bytes an entry. A
hand-edited file (§4.1 allows whitespace and line comments anywhere)
costs more: every trivia slot that is neither empty nor one byte is its own
`bytes` object, and every lead that holds a comment is distinct. One line
per entry, `  [  "k0000001"  ]  =  {  }  ,  -- 0000001`, is 42 bytes, so
`MAX_ENTRIES` of them is a 126 MiB file, inside `MAX_FILE_BYTES`. Measured
by hand at full size in a fresh interpreter (reviewer's laptop, M-series,
16 GB, load average about 7 from other agents):

    wide-trivia-tables  3,000,000 entries  125.9 MiB  parse 16.33 s  peak RSS 2520 MiB
    wide-trivia         3,000,000 entries  143.1 MiB  parse 14.38 s  peak RSS 2355 MiB
      (same line with "v0000001" for the table)
    ref-comments        3,000,000 entries   78.0 MiB  parse  7.12 s  peak RSS 1451 MiB
      (`\\t\\t"s1", -- [1]`, the §4.2 reference form with distinct strings)

Top-level assignments are not table entries, so `MAX_ENTRIES` does not
count them: `a=1\n` repeated is four bytes an assignment, and a 50 MiB file of
it (13,107,200 assignments) measured parse 19.06 s, peak RSS 1244 MiB (same
machine and load). At `MAX_FILE_BYTES` that is 67 million assignments; the
third test projects its RSS from a 1/64 slice.

This probe parses a tenth of `MAX_ENTRIES` in a child interpreter and
scales the parse's RSS growth to `MAX_ENTRIES` (it grows linearly). The
positive control is the fix round's own costly shape at the same count,
`{},` per line, which projects well under the target. Only memory is
asserted; timing on a CI runner is not the owner's laptop.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from wowlab_core.luadata import MAX_ENTRIES, MAX_FILE_BYTES

pytestmark = [
    pytest.mark.parser,
    pytest.mark.skipif(sys.platform == "win32", reason="uses the resource module"),
]

MIB = 1024 * 1024
LIMIT_BYTES = 1.5 * 1024 * MIB  # 1.5 GB read as GiB, the generous reading
FRACTION = 10

CHILD = r"""
import resource, sys
from wowlab_core import luadata
shape, count = sys.argv[1], int(sys.argv[2])
if shape == "assign":
    data = b"\r\n" + b"a=1\n" * count
elif shape == "wide":
    body = b"".join(
        b'  [  "k%07d"  ]  =  {  }  ,  -- %07d\r\n' % (i, i) for i in range(count)
    )
else:
    body = b"{},\r\n" * count
if shape != "assign":
    data = b"\r\nCONSTRUCTED_DB = {\r\n" + body + b"}\r\n"
    del body
scale = 1 if sys.platform == "darwin" else 1024
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
doc = luadata.parse(data)
after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
print(before, after, len(data))
"""


def _projected_peak(
    shape: str, count: int = MAX_ENTRIES // FRACTION, fraction: int = FRACTION
) -> float:
    out = subprocess.run(
        [sys.executable, "-c", CHILD, shape, str(count)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    before, after, size = int(out[0]), int(out[1]), int(out[2])
    # Interpreter and data at full size, plus the parse's growth scaled up.
    return before + (fraction - 1) * size + (after - before) * fraction


def test_constructed_wide_trivia_document_at_the_entry_bound_fits_the_rss_target() -> None:
    peak = _projected_peak("wide")
    assert peak < LIMIT_BYTES, f"projected peak RSS {peak / MIB:.0f} MiB at MAX_ENTRIES"


def test_constructed_top_level_assignments_at_the_file_bound_fit_the_rss_target() -> None:
    fraction = 64
    peak = _projected_peak("assign", MAX_FILE_BYTES // 4 // fraction, fraction)
    assert peak < LIMIT_BYTES, f"projected peak RSS {peak / MIB:.0f} MiB at MAX_FILE_BYTES"


def test_constructed_positive_control_empty_tables_at_the_entry_bound() -> None:
    peak = _projected_peak("tables")
    assert peak < LIMIT_BYTES, f"projected peak RSS {peak / MIB:.0f} MiB at MAX_ENTRIES"
