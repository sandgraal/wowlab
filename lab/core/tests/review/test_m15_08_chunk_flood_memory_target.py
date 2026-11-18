# Probe from review of m15/08-m2-skin; reproduces a flood of empty chunks costing ~70x the file in memory and an error message as long as the file
"""A constructed file (labelled `constructed`, hostile input) of 125,000
empty unknown chunks and no `MD21`: `parse_m2` builds one `M2Chunk` per
chunk before it looks for `MD21` (tracemalloc peak about 72 MB for 1 MB of
input) and then names every tag in the refusal (a message of about 1 MB,
printed as one line by `scripts/casc_facts.py --m2`). The positive control
is the minimal constructed model, which still parses."""

from __future__ import annotations

import sys
import tracemalloc
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))
from _m2_build import chunked, md20

from wowlab_core import m2

FLOOD = b"ABCD\0\0\0\0" * (1_000_000 // 8)


def test_positive_control_minimal_model_parses_constructed() -> None:
    assert m2.parse_m2(chunked(bytes(md20()))).version == 272


def test_chunk_flood_refusal_message_is_bounded_constructed() -> None:
    with pytest.raises(m2.M2MalformedError) as caught:
        m2.parse_m2(FLOOD)
    assert len(str(caught.value)) < 2_000, f"{len(str(caught.value))} characters"


def test_chunk_flood_memory_is_bounded_constructed() -> None:
    tracemalloc.start()
    try:
        with pytest.raises(m2.M2MalformedError):
            m2.parse_m2(FLOOD)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 16_000_000, f"{peak} bytes for {len(FLOOD)} bytes of input"
