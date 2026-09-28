# Probe from review of m10/13-combatlog-tokenizer; reproduces quadratic time in
# the `_quoted_fields` fast path on an unclosed quote followed by many commas.
"""CONSTRUCTED hostile input (L8), labelled in the ids.

`combatlog` bounds a line at `MAX_LINE_BYTES` (1 MiB) so a hostile file
stays cheap. `_quoted_fields`, the path for a line with quotes and no
groups, rebuilds the quoted text with ``text = text + "," + pieces[j]`` for
every comma-separated piece until one ends with ``"``. When none does, the
work is quadratic in the number of pieces before it gives up and hands the
line to `_scan`, which refuses it in linear time. Measured on the reviewer's
M1: 12.4 s for one 1 MiB line ``E,"`` + ``,a`` * N, against 0.07 s for 1 MiB at
the tokenizer's own 15 MB/s. A 50 MB file of such lines takes about 10 min.

Positive control: the same line with the quote closed (``E,"a"`` + ``,a`` * N),
which the fast path handles in one pass.

Named `*_time_target.py`, so `conftest.py` here skips it on Windows.
"""

from __future__ import annotations

import time

import pytest

from wowlab_core import combatlog

pytestmark = pytest.mark.parser

HEAD = "4/1/2026 02:16:30.000-4  E,"
BUDGET_S = 2.0  # about 30 times the linear cost of a 1 MiB line


def _line(start: str) -> str:
    n = (combatlog.MAX_LINE_BYTES - len(HEAD) - len(start)) // 2
    return HEAD + start + ",a" * n


def _time(line: str) -> tuple[float, object]:
    t = time.perf_counter()
    entry = combatlog.tokenize_line(line)
    return time.perf_counter() - t, entry


@pytest.mark.parametrize("start", [pytest.param('"a"', id="constructed-closed-quote-1MiB")])
def test_control_closed_quote_line_is_linear(start: str) -> None:
    elapsed, entry = _time(_line(start))
    assert isinstance(entry, combatlog.Record)
    assert elapsed < BUDGET_S


@pytest.mark.parametrize("start", [pytest.param('"', id="constructed-unclosed-quote-1MiB")])
def test_unclosed_quote_line_is_refused_in_linear_time(start: str) -> None:
    elapsed, entry = _time(_line(start))
    assert isinstance(entry, combatlog.Unparsed)
    assert elapsed < BUDGET_S, f"{elapsed:.2f}s for one {combatlog.MAX_LINE_BYTES}-byte line"
