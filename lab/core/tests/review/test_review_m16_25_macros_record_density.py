# Probe from review of m16/25-wtf-budgets-bom; reproduces a macros-cache.txt within MACROS_BUDGET growing RSS past the module's "under 100 MiB" bound.
"""CONSTRUCTED inputs (L8: boundary case), labelled in every test id.

`wtfconfig.py` says a document at its budget "stays under 100 MiB", and the
LAB_PLAN §6.5 amendment of 2026-11-09 says "about 70 to 80 MiB". The serial
probe (`test_wtfconfig_budget_rss_constructed.py`) builds its macros case as
one record with 99,999 body lines. Two-line records (`VER` then `END`) fill
the same 100,000-line budget with 50,000 macros, each with a header line, an
end line and a `Macro` view entry, and grow RSS by about 117 MiB on Linux
(reviewer's reading, 2026-11-10). The positive control is the probe's own
shape, which stays under the bound.

Measured in a child process from its own VmHWM, Linux only, as the serial
probe does; if kept, it belongs in the root conftest's SERIAL_FILES.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="reads VmHWM from /proc/self/status"
)

CLAIMED_BOUND = 100 * 1024 * 1024  # wtfconfig.py: "stays under 100 MiB"

CHILD = r"""
import sys
from wowlab_core import wtfconfig
shape = sys.argv[1]
budget = wtfconfig.MACROS_BUDGET
if shape == "records":
    n = budget.max_lines // 2
    data = b"".join(
        b'VER 3 %016X "m%d" "INV_MISC_QUESTIONMARK"\nEND\n' % (i, i) for i in range(n)
    )
else:  # the serial probe's shape: one record, every other line a body line
    width = budget.max_bytes // budget.max_lines
    rows = [b'VER 3 01 "n" "1"\n']
    for i in range(1, budget.max_lines):
        text = b"/cast %d " % i
        rows.append(text + b"s" * (width - len(text) - 1) + b"\n")
    data = b"".join(rows)
assert len(data) <= budget.max_bytes and data.count(b"\n") <= budget.max_lines

def peak():
    with open("/proc/self/status") as status:
        for row in status:
            if row.startswith("VmHWM:"):
                return int(row.split()[1]) * 1024
    raise SystemExit("no VmHWM")

before = peak()
doc = wtfconfig.parse_macros(data)
macros = doc.macros
print(len(macros), peak() - before)
"""


def _grown(shape: str) -> tuple[int, int]:
    out = subprocess.run(
        [sys.executable, "-c", CHILD, shape], check=True, capture_output=True, text=True
    ).stdout.split()
    return int(out[0]), int(out[1])


def test_constructed_positive_control_the_serial_probes_shape_stays_under_the_bound() -> None:
    macros, grown = _grown("one-record")
    assert macros == 1
    assert 0 < grown < CLAIMED_BOUND


def test_constructed_two_line_records_at_the_budget_stay_under_the_claimed_bound() -> None:
    macros, grown = _grown("records")
    assert macros == wtfconfig_max_records()
    assert grown < CLAIMED_BOUND, (
        f"{macros} two-line macro records within MACROS_BUDGET grew RSS by "
        f"{grown / 2**20:.1f} MiB, over the documented 100 MiB"
    )


def wtfconfig_max_records() -> int:
    from wowlab_core import wtfconfig

    return wtfconfig.MACROS_BUDGET.max_lines // 2
