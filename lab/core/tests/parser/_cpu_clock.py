"""The clock for the CPU-bound timing budgets in the parser suite (M11-26).

What is measured, and why (M11-26, 2026-09-29): `time.process_time()`, the
CPU time (user plus system, every thread) of the test process, read around
the parse call alone. The parses timed with it compute over bytes already in
memory and do no I/O, so on an idle machine CPU time and wall clock agree
(measured idle on the owner's M1: the hostile TOC lines 0.3 to 10 ms and the
unclosed-quote combat-log line 40 ms, the same on both clocks). Under load,
wall clock also counts the time the parse waits for a core. That says nothing
about the parser, and it can fail a 1 s budget on a busy machine while the
test passes alone. M11-16T (the M10-04 probes) and M11-19 (the capture-tool
scans) moved to CPU time for the same reason; this follows M11-19's
in-process `cpu_clock()` in `tests/scripts/test_lab_capture.py`.

A slower parse still fails, because more work uses more CPU: a quadratic scan
of a hostile line is exactly what these budgets exist to catch. CPU time also
rises somewhat under load (a slower core, a lower clock), so under load a
budget can only overstate a parse, never flatter it. The blind spot is a
parse that waits (sleep, blocking I/O) rather than computes; these parses do
neither. Where the platform has no process clock, the clock is wall clock
(`time.perf_counter()`), and the name says so in the failure message.

Also used (M11-28) by the multi-MB document grader in
`test_luadata_constructed.py` and by two in-process review probes,
`tests/review/test_m10_06_toc_parse_quadratic_time.py` and
`tests/review/test_m10_13_unclosed_quote_quadratic_time_target.py`, which put
this folder on `sys.path` to import it. M11-36 did the same for the capture
tool's `scrub` timing in `tests/review/test_review_m10_02_followup_blank_runs.py`.
"""

from __future__ import annotations

import time
from collections.abc import Callable


def cpu_clock() -> tuple[Callable[[], float], str]:
    """The clock for a CPU-bound timing budget, and its name for messages."""
    try:
        time.get_clock_info("process_time")
    except (AttributeError, ValueError, OSError):
        return time.perf_counter, "wall-clock"
    return time.process_time, "CPU"
