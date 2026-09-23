# Probe from review of m10/06-layout-filemap-toc; reproduces parse_toc taking
# quadratic time on a hostile TOC far below MAX_TOC_BYTES, so one addon hangs
# the inventory (LAB_PLAN §6.2 "walk depth and entry counts are bounded").
"""Two shapes, both constructed hostile inputs (L8 allows these):

- `_DIRECTIVE` ends `(.*?)[ \\t]*` under `fullmatch`: for every character the
  lazy value group takes inside a run of spaces, `[ \\t]*` re-scans the rest
  of the run. A 40 KB run of spaces inside one directive value costs seconds;
  at the 1 MiB read limit it is tens of minutes.
- `_file_line` strips trailing conditions one per loop iteration, each a
  fresh `fullmatch` over the whole remaining line, so N conditions cost
  O(N^2). 8000 `[c]` groups (32 KB) take seconds.

A linear parse of either line takes milliseconds. The positive controls are
lines of the same length without the pathological shape. Fix: match the
directive as `##[ \\t]*([^\\s:]+)[ \\t]*:(.*)` and strip the value with
`bytes.strip(b" \\t")`; peel trailing conditions with one right-to-left scan
(or `re.finditer` over `\\s+\\[[^\\[\\]]*\\]` anchored at the end) instead of
re-matching the whole line per condition.
"""

from __future__ import annotations

import time

from wowlab_core.toc import MAX_TOC_BYTES, parse_toc

LIMIT_SECONDS = 1.0


def _seconds(data: bytes) -> float:
    assert len(data) < MAX_TOC_BYTES // 8, "well inside the read limit"
    start = time.perf_counter()
    parse_toc(data)
    return time.perf_counter() - start


def test_control_same_length_lines_parse_fast_constructed() -> None:
    assert _seconds(b"## Title: x" + b"y" * 40_000 + b"\n") < LIMIT_SECONDS
    assert _seconds(b"Foo.lua" + b"c" * 32_000 + b"\n") < LIMIT_SECONDS


def test_directive_value_with_a_long_space_run_is_linear_constructed() -> None:
    elapsed = _seconds(b"## Title: x" + b" " * 40_000 + b"y\n")
    assert elapsed < LIMIT_SECONDS, f"one 40 KB directive line took {elapsed:.1f}s"


def test_file_line_with_many_trailing_conditions_is_linear_constructed() -> None:
    elapsed = _seconds(b"Foo.lua" + b" [c]" * 8_000 + b"\n")
    assert elapsed < LIMIT_SECONDS, f"one 32 KB file line took {elapsed:.1f}s"
