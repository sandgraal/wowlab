"""Serial RSS probe of `blp.decode` at the cap (M15-07, security review
round 1).

CONSTRUCTED input (L8: a boundary and hostile case), labelled in the test
id: a 4096 x 4096 DXT5 of random blocks, the most pixels `MAX_PIXELS`
admits, every block's colour and alpha endpoints its own, so the endpoint
caches would grow without bound if they were not emptied past
`blp.CACHE_LIMIT` pairs (before the fix: about 411 MiB more, and about
10 s; the 62 s first recorded here was a loaded machine). A
child process builds the file, reads its own peak RSS, decodes it, and
reports the growth, which must stay under four times the 64 MiB output.
Measured 2026-11-18 on the owner's M1 with tracemalloc: 170 MiB, 3.6 s.

tracemalloc is not used here: it traces every integer the pixel gather
makes and turns 3.6 s into more than 300 s. On Linux the child reads its
own `VmHWM`, because `ru_maxrss` there carries the parent's high-water
mark across `execve` (`test_luadata_budget_constructed.py`). The file is
in the root `conftest.py`'s `SERIAL_FILES`: it runs alone after the
parallel pass.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.parser,
    pytest.mark.skipif(sys.platform == "win32", reason="uses the resource module"),
]

CHILD = r"""
import random, resource, struct, sys
from wowlab_core import blp
side = 4096
offsets = [blp.HEADER_SIZE + blp.PALETTE_SIZE] + [0] * 15
sizes = [side * side] + [0] * 15
head = struct.pack("<4sIBBBBII16I16I", b"BLP2", 1, 2, 8, 7, 1, side, side, *offsets, *sizes)
data = head + bytes(blp.PALETTE_SIZE) + random.Random(4096).randbytes(side * side)

def peak():
    if sys.platform.startswith("linux"):
        with open("/proc/self/status") as status:
            for row in status:
                if row.startswith("VmHWM:"):
                    return int(row.split()[1]) * 1024
        raise SystemExit("no VmHWM in /proc/self/status")
    scale = 1 if sys.platform == "darwin" else 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale

before = peak()
image = blp.decode(data)
assert len(image.rgba) == side * side * 4
print(peak() - before)
"""


def test_a_4096_square_dxt5_of_random_blocks_stays_under_four_outputs_constructed() -> None:
    done = subprocess.run(
        [sys.executable, "-c", CHILD], capture_output=True, text=True, timeout=240, check=False
    )
    assert done.returncode == 0, done.stderr
    growth = int(done.stdout.strip())
    output = 4096 * 4096 * 4
    assert growth < 4 * output, f"peak RSS grew {growth / 2**20:.0f} MiB"
