# Probe from review of m10/12-luadata-serializer; reproduces the output-size
# bound refusing only after the whole oversized output is held in memory.
"""CONSTRUCTED document (L8), a boundary case.

`serialize` refuses output over `MAX_FILE_BYTES` (its docstring; the
parser's bound, `docs/LAB_PLAN.md` §6.4 "Bounds": "Over a bound raises a
typed error"), but checks the length once, after the last byte is written,
and `refuse()` then copies the buffer to compute the position. A document
that shares one large node many times therefore holds its whole output,
twice, before it is refused. Reviewer's run on 3259dc7 (M1): twelve places
sharing one 60 MiB string built 720 MiB before the refusal, peak RSS
1.52 GiB (`/usr/bin/time -l`); a hundred places would need about 12 GiB.

This probe lowers `MAX_FILE_BYTES` (read at call time) to 1 MiB and shares
one 512 KiB string 64 times. A bound that is checked as the output grows
refuses after about 1.5 MiB; the peak traced allocation must stay within a
few times the bound. On 3259dc7 it is about 64 MiB.

Positive control: the refusal itself happens (the bound is enforced).
"""

from __future__ import annotations

import tracemalloc

import pytest

from wowlab_core import luadata
from wowlab_core.luadata import (
    Assignment,
    Entry,
    KeyStyle,
    LuaDocument,
    LuaLimitError,
    LuaString,
    LuaTable,
    serialize,
)

pytestmark = pytest.mark.parser

BOUND = 1 << 20


def _document() -> LuaDocument:
    big = LuaString(b"", b'"' + b"a" * (BOUND // 2) + b'"')
    entry = Entry(b"\r\n", KeyStyle.POSITIONAL, None, b"", b"", big, b"", b",", None, False)
    table = LuaTable(b" ", (entry,) * 64, b"\r\n")
    return LuaDocument((Assignment(b"\r\n", "X", b" ", table),), b"\r\n")


def test_positive_control_oversized_output_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(luadata, "MAX_FILE_BYTES", BOUND)
    with pytest.raises(LuaLimitError):
        serialize(_document())


def test_output_bound_is_enforced_before_memory_grows_past_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document()
    monkeypatch.setattr(luadata, "MAX_FILE_BYTES", BOUND)
    tracemalloc.start()
    try:
        with pytest.raises(LuaLimitError):
            serialize(document)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * BOUND, f"peak {peak} bytes before refusing at a {BOUND}-byte bound"
