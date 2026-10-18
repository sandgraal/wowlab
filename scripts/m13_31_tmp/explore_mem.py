"""TEMPORARY (M13-31, removed before review): footprint, ru_maxrss and system paging per child."""

import re
import subprocess
import sys

sys.path.insert(0, "lab/core/tests/review")
import test_m10_04_bounded_document_misses_memory_target as mem

MIB = 1024 * 1024
COUNT = mem.MAX_FILE_BYTES // 4 // mem.ASSIGN_FRACTION


def vm() -> dict[str, int]:
    out = subprocess.run(["vm_stat"], capture_output=True, text=True, check=True).stdout
    want = ("Swapouts", "Compressions", "Pageouts", "Pages free", "Pages stored in compressor")
    found = {}
    for line in out.splitlines():
        key, _, value = line.partition(":")
        if key.strip() in want:
            found[key.strip()] = int(value.strip().rstrip("."))
    return found


def child(parser: str) -> str:
    v0 = vm()
    proc = subprocess.run(
        ["/usr/bin/time", "-l", sys.executable, "-c", mem.CHILD, "assign", str(COUNT), parser],
        check=True,
        capture_output=True,
        text=True,
    )
    v1 = vm()
    before, after, _ = map(int, proc.stdout.split())
    foot = int(re.search(r"(\d+)\s+peak memory footprint", proc.stderr).group(1))
    maxrss = int(re.search(r"(\d+)\s+maximum resident set size", proc.stderr).group(1))
    delta = {k[:6]: v1[k] - v0[k] for k in ("Swapouts", "Compressions", "Pageouts")}
    return (
        f"{parser:8s} footprint {foot / MIB:6.1f} time-maxrss {maxrss / MIB:6.1f} "
        f"child before {before / MIB:6.1f} after {after / MIB:6.1f} | free {v0['Pages free']} "
        f"compressor {v0['Pages stored in compressor']} delta {delta}"
    )


rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 4
print(
    "M1331",
    subprocess.run(["memory_pressure"], capture_output=True, text=True).stdout.splitlines()[-1],
)
for _ in range(rounds):
    for parser in ("none", "luadata", "double"):
        print("M1331", child(parser), flush=True)
