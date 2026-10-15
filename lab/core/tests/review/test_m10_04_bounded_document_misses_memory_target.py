# Probe from review of m10/04-luadata-parser (fix round 1); reproduces a document
# within every bound (MAX_ENTRIES entries) projecting to about 2.5 GB peak RSS.
"""CONSTRUCTED input (L8: a boundary case of the §6.4 performance target).

The §6.4 amendment of 2026-09-23 says: "The performance target holds for any
document within the bounds." The bound on memory is `MAX_ENTRIES` table
entries, chosen from shapes that cost at most about 330 bytes an entry. A
hand-edited file (§4.1 allows whitespace and line comments anywhere)
costs more: every trivia slot that is neither empty nor one byte is its own
`bytes` object, and every lead that holds a comment is distinct. One line
per entry, `  [  "k0000001"  ]  =  {  }  ,  -- 0000001`, is 42 bytes. When
this probe was written `MAX_ENTRIES` was 3,000,000, so that was a 126 MiB
file inside `MAX_FILE_BYTES`, measured by hand at full size in a fresh
interpreter (reviewer's laptop, M-series, 16 GB, load average about 7 from
other agents) as below. Those figures are history: `MAX_ENTRIES` is now
derived from the `MAX_COST` budget (138,055 when M13-17T was done), so the
wide-trivia and empty-tables tests below parse 13,805 entries, not 300,000.

    wide-trivia-tables  3,000,000 entries  125.9 MiB  parse 16.33 s  peak RSS 2520 MiB
    wide-trivia         3,000,000 entries  143.1 MiB  parse 14.38 s  peak RSS 2355 MiB
      (same line with "v0000001" for the table)
    ref-comments        3,000,000 entries   78.0 MiB  parse  7.12 s  peak RSS 1451 MiB
      (`\\t\\t"s1", -- [1]`, the §4.2 reference form with distinct strings)

Top-level assignments are not table entries, so `MAX_ENTRIES` does not
count them: `a=1\n` repeated is four bytes an assignment, and a 50 MiB file of
it (13,107,200 assignments) measured parse 19.06 s, peak RSS 1244 MiB (same
machine and load). At `MAX_FILE_BYTES` that is 67 million assignments; the
third test projects its RSS from a 1/16 slice.

This probe parses a tenth of `MAX_ENTRIES` in a child interpreter and
scales the parse's RSS growth to `MAX_ENTRIES` (it grows linearly). The
positive control is the fix round's own costly shape at the same count,
`{},` per line, which projects well under the target. Only memory is
asserted; timing on a CI runner is not the owner's laptop.

The assignment projection was flaky on `macos-latest` (M13-17T): from a
1/64 slice it read a steady 1105 MiB locally, then 1754 to 1772 MiB on four
of fourteen macOS runs. Three things in the measurement, not the parser,
moved it:

- The slice was built as `b"\\r\\n" + body` and `body` freed before the
  parse. `before` is a high-water mark, so it already counted those pages;
  when the parse reused them, the growth read low by one slice of input
  (4 MiB, so 256 MiB projected). Whether they were reused varied by run:
  at 1/64 the runner read 1762 to 1774 MiB (growth 23.3 MiB) on some runs
  and 1514 to 1520 MiB (growth 19.3 MiB) on others, and the two differ by
  exactly that one 4 MiB slice. The high reading was the honest one, and
  locally 1105 MiB was the low one. With `body` kept alive through the
  parse, the local projection is 1340 MiB at 1/64, 1/32 and 1/16 alike.
- The multiplier also scaled growth that does not grow with the slice. On
  the runner the honest growth was 23.3 MiB for 1/64 and about 70.7 MiB for
  1/16, so about 7.5 MiB of it does not scale with the slice. A full-size
  parse pays it once. A 1/64 projection multiplies it by 64 (about
  480 MiB), a 1/16 projection by 16 (about 120 MiB). That is why 1/16
  reads about 1425 MiB on the runner where an honest 1/64 read about 1772.
  This is not a weakening: the 1/16 projection still overcounts that cost
  15 times, only less than 1/64 did. At 1/16 single samples still vary
  upward now and then (1435, 1790 and 1435 MiB in a row once), which the
  least of several samples absorbs.
- On Linux `ru_maxrss` is kept across `execve` (getrusage(2)), so a child
  of a large pytest process starts at the parent's high-water mark and a
  parse below it reads as no growth. In the full suite on CI the stand-in
  below projected 948 MiB: the probe saw the parent, not the parser. The
  child now reads its own `VmHWM` from `/proc/self/status` on Linux.

So the assignment test keeps `body` alive (its `before` then counts one
unscaled extra slice of input, 16 MiB, which only makes it stricter),
measures a 1/16 slice (4,194,304 assignments, 16 MiB, under `MAX_COST`) so
fixed costs and noise are multiplied by 16 rather than 64, and takes the
least of five projections, each from a fresh interpreter. The least, not the mean,
because the spread is upward; a parser that truly holds more memory raises
every sample, the least included. The 1.5 GiB target is unchanged. A
constructed stand-in parser that keeps a second copy of its result (the last
test) shows the same measurement still fails such a parser.
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
ASSIGN_FRACTION = 16  # of MAX_FILE_BYTES; a 1/10 slice is over MAX_COST
ASSIGN_SAMPLES = 5

CHILD = r"""
import resource, sys
from wowlab_core import luadata
shape, count, parser = sys.argv[1], int(sys.argv[2]), sys.argv[3]
parse = luadata.parse
if parser == "double":
    # CONSTRUCTED stand-in, a test of the probe itself: a parser that keeps
    # a second, independent copy of its result, so it holds twice the memory.
    def parse(data):
        return luadata.parse(data), luadata.parse(data)
if shape == "assign":
    # Kept alive through the parse: freed here, its pages (already in the
    # `before` high-water mark) would be reused by the parse and hidden.
    body = b"a=1\n" * count
    data = b"\r\n" + body
elif shape == "wide":
    body = b"".join(
        b'  [  "k%07d"  ]  =  {  }  ,  -- %07d\r\n' % (i, i) for i in range(count)
    )
else:
    body = b"{},\r\n" * count
if shape != "assign":
    data = b"\r\nCONSTRUCTED_DB = {\r\n" + body + b"}\r\n"
    del body
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
print(before, after, len(data))
"""


def _projected_peak(
    shape: str,
    count: int = MAX_ENTRIES // FRACTION,
    fraction: int = FRACTION,
    parser: str = "luadata",
) -> float:
    out = subprocess.run(
        [sys.executable, "-c", CHILD, shape, str(count), parser],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    before, after, size = int(out[0]), int(out[1]), int(out[2])
    # Interpreter and data at full size, plus the parse's growth scaled up.
    return before + (fraction - 1) * size + (after - before) * fraction


def _projected_assignments_peak(parser: str = "luadata") -> tuple[float, list[float]]:
    """The least of `ASSIGN_SAMPLES` projections, each from a fresh interpreter."""
    count = MAX_FILE_BYTES // 4 // ASSIGN_FRACTION
    samples = [
        _projected_peak("assign", count, ASSIGN_FRACTION, parser) for _ in range(ASSIGN_SAMPLES)
    ]
    return min(samples), samples


def _mib(samples: list[float]) -> str:
    return ", ".join(f"{s / MIB:.0f}" for s in samples)


def test_constructed_wide_trivia_document_at_the_entry_bound_fits_the_rss_target() -> None:
    peak = _projected_peak("wide")
    assert peak < LIMIT_BYTES, f"projected peak RSS {peak / MIB:.0f} MiB at MAX_ENTRIES"


def test_constructed_top_level_assignments_at_the_file_bound_fit_the_rss_target() -> None:
    peak, samples = _projected_assignments_peak()
    assert peak < LIMIT_BYTES, (
        f"projected peak RSS {peak / MIB:.0f} MiB at MAX_FILE_BYTES (samples {_mib(samples)})"
    )


def test_constructed_positive_control_empty_tables_at_the_entry_bound() -> None:
    peak = _projected_peak("tables")
    assert peak < LIMIT_BYTES, f"projected peak RSS {peak / MIB:.0f} MiB at MAX_ENTRIES"


def test_constructed_stand_in_holding_twice_the_memory_fails_the_assignment_probe() -> None:
    """The assignment measurement still catches a parser that holds twice as much.

    CONSTRUCTED stand-in, not `luadata`: it parses the same slice twice and
    keeps both results, so the memory it keeps after the parse doubles. Its
    peak growth rises only about 1.5 times (1.49 locally, 1.59 on the macOS
    runner, 1.51 on Linux), because the first parse's temporaries are freed
    before the second runs. The input, the interpreter, the slice, the
    target and the least-of-five rule are those of the test above. Its
    projection must miss the target (it does by at least 288 MiB), or the
    measurement has stopped being able to see the parser.
    """
    peak, samples = _projected_assignments_peak("double")
    assert peak >= LIMIT_BYTES, (
        f"a parser holding twice the memory projected only {peak / MIB:.0f} MiB "
        f"at MAX_FILE_BYTES (samples {_mib(samples)}); the probe cannot see it"
    )
