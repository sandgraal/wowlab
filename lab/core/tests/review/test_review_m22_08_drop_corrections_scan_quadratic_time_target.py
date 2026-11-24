# Probe from review of m22/08-addon-lookups (round 2); reproduces a 16 KB drop file of unclosed `a.b = {` lines taking about 20 s (32 KB: 77 s) in `_assigned_constructors`
"""CONSTRUCTED INPUT (L8, hostile case). `questiedb._assigned_constructors`
(fix round 1) finds every `Module.field = {` at the start of a line and runs
`_constructor_end` from it to the closing brace, one byte at a time in
Python. When the braces never close, each match scans to the end of the
file, so a file of n such lines costs n * n / 2 byte steps. Measured in
review: 1,000 lines (8 KB) 4.9 s, 2,000 (16 KB) 20.2 s, 4,000 (32 KB)
77.1 s; `MAX_SUPPORT_FILE_BYTES` is 32 MiB, and `read_drop_tables` reads
every file the TOC lists under `DropTables` on the first item hit. The
positive control is a well-formed corrections file of the same size, which
reads at once. CPU time, not wall clock, against a budget a hundred times
the control's."""

from __future__ import annotations

import time

from wowlab_core import questiedb

BUDGET_S = 2.0


def _cpu(data: bytes) -> tuple[float, list[tuple[str, bytes]]]:
    started = time.process_time()
    out = list(questiedb._assigned_constructors(data))
    return time.process_time() - started, out


def test_a_well_formed_file_of_the_same_size_reads_at_once() -> None:
    body = b"".join(b"  [%d] = { [%d] = 12.5 },\n" % (i, i + 1) for i in range(700))
    data = b"M.Era = {\n" + body + b"}\n"
    assert len(data) >= 16_000
    took, out = _cpu(data)
    assert len(out) == 1 and out[0][1].endswith(b"}")
    assert took < BUDGET_S, took


def test_unclosed_assignments_do_not_cost_the_square_of_the_file() -> None:
    data = b"a.b = {\n" * 2000  # 16,000 bytes
    took, out = _cpu(data)
    assert len(out) == 2000
    assert took < BUDGET_S, f"{took:.1f} s of CPU for {len(data)} bytes"
