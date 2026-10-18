"""TEMPORARY (M13-31, removed before review): the probes against constructed stand-ins.

CPU: a parser twice as slow (parses the over-full document twice) must fail
every case. Memory: the twice-the-memory control must fail when the stand-in
holds only what `luadata` holds (half the stand-in's memory).
"""

import sys
import tempfile
import warnings
from pathlib import Path

sys.path.insert(0, "lab/core/tests/review")
import test_m10_04_bounded_document_misses_memory_target as mem
import test_m10_04_escaped_key_list_at_budget_misses_time_target as esc
import test_m10_04_semicolon_list_at_budget_misses_time_target as semi

ONCE = "    luadata.parse(data)\n"
TWICE = (
    "    try:\n        luadata.parse(data)\n    except luadata.LuaLimitError:\n        pass\n"
    "    luadata.parse(data)\n"
)
warnings.simplefilter("ignore")
what = sys.argv[1:] or ["cpu", "mem"]
if "cpu" in what:
    for mod in (semi, esc):
        assert mod.CHILD.count(ONCE) == 1
        mod.CHILD = mod.CHILD.replace(ONCE, TWICE)
    cases = [
        ("semicolon file, `0,`", lambda d: semi._seconds_to_the_budget(b"0,\r\n", d)),
        ("semicolon file, `0;`", lambda d: semi._seconds_to_the_budget(b"0;\n", d)),
        ("escaped file, `0,`", lambda d: esc._seconds_to_the_budget(b"0,\r\n", 150, d)),
        ("escaped file, `[\\1]`", lambda d: esc._seconds_to_the_budget(b'["\\1"]=0,', 400, d)),
    ]
    for label, run in cases:
        with tempfile.TemporaryDirectory() as tmp:
            took, clock, samples = run(Path(tmp))
        verdict = "FAILS (as it should)" if took >= 10.0 else "PASSES (probe blind)"
        print(
            f"M1331 twice-as-slow {label}: least {took:.2f} {clock} s, samples "
            f"{[round(s, 2) for s in samples]} -> {verdict}",
            flush=True,
        )
if "mem" in what:
    least, samples = mem._projected_assignments_peak("luadata")
    verdict = "FAILS (as it should)" if least < mem.LIMIT_BYTES else "PASSES (control blind)"
    print(
        f"M1331 memory control with a stand-in holding half (luadata): least "
        f"{least / mem.MIB:.0f} MiB, samples {mem._mib(samples)} -> {verdict}",
        flush=True,
    )
