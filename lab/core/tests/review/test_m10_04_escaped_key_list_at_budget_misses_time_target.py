# Probe from review of m10/04-luadata-parser (fix round 3); reproduces a list of `["\1"]=0,` keyed entries within the cost budget taking over 10 s.
"""CONSTRUCTED inputs (L8: boundary cases for the §6.4 cost budget).

`docs/LAB_PLAN.md` §6.4 (the 2026-09-23 amendment, rewritten after fix
rounds 2 and 3) says "The target covers every document within the budget,
whatever its shape", and gives 6.2 s as the slowest shape at the budget.

A string key containing a backslash is decoded at parse, once per entry,
even when the key object is shared: `_escape_over_255`, then `_unescape`
(two searches, a substitution, `_decimal_to_octal` with its own
`bytearray`/`memoryview`/`finditer`, then the codec). That is a fixed cost
of several Python calls per key, but the budget charges it only
`_C_ESCAPE` (100) per backslash. So a key with a single escape is
undercharged, and `["\\1"]=0,` repeated (duplicate keys, which the grammar
keeps and flags) fits 2.29 million entries in 19.6 MiB.

Measured on the owner's M1 on 2026-09-27, load average 1.4 to 2.3, in a
fresh interpreter under `/usr/bin/time -l`, two interleaved rounds:

    `0,\\r\\n` list (the §6.4 row, 5.2 s)   6,608,972 entries   5.19 s /  5.16 s
    `["\\1"]=0,` list                        2,286,123 entries  10.13 s / 10.08 s
    `["\\1<n>"]=0,` distinct keys, filled
        to the budget                        ~1,900,000 entries 10.05 s / 10.08 s
    wide-tables (§6.4 "Slowest", 6.2 s)     1,408,267 entries   6.09 s /  6.08 s

`["\\<LF>"]=0,` measured 9.80 s, and `["\\n"]=0,` (simple escape, no
rewrite) 7.66 s.

This probe times, in a fresh interpreter, a parse of an over-full document
up to the point where the budget refuses it (one budget's worth of the
shape), and takes the better of two runs. It is a timing probe: the margin
over 10 s is small on an idle machine and grows with load. The positive
control is the table's `0,` shape, measured the same way, well under 10 s.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from wowlab_core import luadata

TARGET_SECONDS = 10.0

CHILD = """
import sys, time
from wowlab_core import luadata
data = open(sys.argv[1], "rb").read()
started = time.perf_counter()
try:
    luadata.parse(data)
except luadata.LuaLimitError:
    print(time.perf_counter() - started)
else:
    print("parsed")
"""


def _seconds_to_the_budget(item: bytes, per_entry: int, folder: Path) -> float:
    # Over-full at `per_entry` cost units an entry: the budget refuses it part way.
    data = b"\r\nX = {\r\n" + item * (luadata.MAX_COST // per_entry) + b"}\r\n"
    path = folder / "Overfull.lua"
    path.write_bytes(data)
    best = float("inf")
    for _ in range(2):
        out = subprocess.run(
            [sys.executable, "-c", CHILD, str(path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert out != "parsed", "the document should cross the budget"
        best = min(best, float(out))
    return best


def test_constructed_control_zeros_at_the_budget_are_inside_the_target(tmp_path: Path) -> None:
    took = _seconds_to_the_budget(b"0,\r\n", 150, tmp_path)
    assert took < TARGET_SECONDS, f"`0,` list at the budget: {took:.2f} s"


def test_constructed_escaped_key_list_at_the_budget_is_inside_the_target(
    tmp_path: Path,
) -> None:
    took = _seconds_to_the_budget(b'["\\1"]=0,', 400, tmp_path)
    assert took < TARGET_SECONDS, (
        f'`["\\1"]=0,` list at the budget: {took:.2f} s, over the §6.4 target of '
        f"{TARGET_SECONDS:.0f} s (the amendment's slowest shape is 6.2 s)"
    )
