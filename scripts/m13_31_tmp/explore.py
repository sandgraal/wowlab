"""TEMPORARY (M13-31 measurement, removed before review): parse CPU vs calibration."""

import subprocess
import sys
import tempfile
from pathlib import Path

from wowlab_core import luadata

CHILD = r"""
import re, sys, time
from wowlab_core import luadata
clock = time.process_time
def loop():
    started = clock()
    acc = 0
    for i in range(3_000_000):
        acc += len(str(i))
    return clock() - started
def alloc():
    started = clock()
    buf = b"\r\n".join(b"%d," % i for i in range(1_500_000))
    items = [(m.group(), m.start(), (m.end(),)) for m in re.finditer(rb"(\d+),", buf)]
    del items, buf
    return clock() - started
data = open(sys.argv[1], "rb").read()
a1, b1 = loop(), alloc()
started = clock()
try:
    luadata.parse(data)
except luadata.LuaLimitError:
    took = clock() - started
else:
    raise SystemExit("parsed")
a2, b2 = loop(), alloc()
print(a1, b1, took, a2, b2)
"""

SHAPES = {"zeros": (b"0,\r\n", 150), "semi": (b"0;\n", 150), "esc": (b'["\\1"]=0,', 400)}


def main() -> None:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    with tempfile.TemporaryDirectory() as tmp:
        paths = {}
        for name, (item, per) in SHAPES.items():
            p = Path(tmp) / f"{name}.lua"
            p.write_bytes(b"\r\nX = {\r\n" + item * (luadata.MAX_COST // per) + b"}\r\n")
            paths[name] = p
        for r in range(rounds):
            for name, p in paths.items():
                out = subprocess.run(
                    [sys.executable, "-c", CHILD, str(p)],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.split()
                a1, b1, took, a2, b2 = map(float, out)
                print(
                    f"M1331 round {r} {name:5s} loop {a1:.3f}/{a2:.3f} alloc {b1:.3f}/{b2:.3f} "
                    f"parse {took:.2f} p/loop {2 * took / (a1 + a2):.2f} "
                    f"p/alloc {2 * took / (b1 + b2):.2f}",
                    flush=True,
                )


main()
