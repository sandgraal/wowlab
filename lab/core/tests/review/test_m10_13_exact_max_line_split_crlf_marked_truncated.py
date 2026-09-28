# Probe from review of m10/13-combatlog-tokenizer (fix round 1); reproduces a line
# of exactly MAX_LINE_BYTES, whose CR ends a chunk, read as truncated/over-long.
"""CONSTRUCTED boundary input (L8), labelled in the ids.

docs/LAB_PLAN.md §6.8 (amendment 2026-09-28) and the `combatlog` docstring:
only a line *longer* than `MAX_LINE_BYTES` is cut, and "the same line gives
the same entry however the file is chunked". `_Splitter.feed` measures the
pending rest with its trailing CR still attached, so a line of exactly
`MAX_LINE_BYTES` bytes ended by CRLF, whose CR is the last byte of a chunk
and whose LF starts the next, has a rest of `MAX_LINE_BYTES + 1` bytes and
becomes a `_Long`. `read_log` and `tail` (and `follow`, which shares the
splitter) then yield it as `Unparsed` with `truncated=True` and the reason
"a line longer than ... bytes", while `tokenize` yields the same bytes as a
`Record`. A differential fuzz (MAX_LINE_BYTES 5 and 8, 1-12 byte chunks)
found this as the only class of disagreement between the four readers.

Positive controls, same layout: the line ended by LF only; and the line
with its CR and LF in the same chunk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import combatlog

pytestmark = pytest.mark.parser

M = combatlog.MAX_LINE_BYTES
HEAD = b"4/1/2026 02:16:30.000-4  UNIT_DIED,"
RECORD = HEAD + b"a" * (M - len(HEAD))  # a valid record of exactly MAX_LINE_BYTES bytes


def _entries(tmp_path: Path, prefix_len: int, ending: bytes) -> tuple[object, object, object]:
    prefix = b"y" * (prefix_len - 2) + b"\r\n"
    data = prefix + RECORD + ending
    path = tmp_path / "WoWCombatLog-040126_021630.txt"
    path.write_bytes(data)
    return (
        list(combatlog.tokenize(data))[-1],
        list(combatlog.read_log(path))[-1],
        combatlog.tail(path, 1)[0][-1],
    )


@pytest.mark.parametrize(
    ("prefix_len", "ending"),
    [
        pytest.param(M - 1, b"\n", id="constructed-exact-max-lf-at-chunk-start"),
        pytest.param(M - 2, b"\r\n", id="constructed-exact-max-crlf-same-chunk"),
    ],
)
def test_control_exact_max_line_is_a_record_from_every_reader(
    tmp_path: Path, prefix_len: int, ending: bytes
) -> None:
    from_tokenize, from_read, from_tail = _entries(tmp_path, prefix_len, ending)
    assert isinstance(from_tokenize, combatlog.Record)
    assert from_read == from_tokenize == from_tail


@pytest.mark.parametrize(
    ("prefix_len", "ending"),
    [pytest.param(M - 1, b"\r\n", id="constructed-exact-max-cr-ends-chunk-lf-starts-next")],
)
def test_exact_max_line_split_crlf_is_a_record_from_every_reader(
    tmp_path: Path, prefix_len: int, ending: bytes
) -> None:
    from_tokenize, from_read, from_tail = _entries(tmp_path, prefix_len, ending)
    assert isinstance(from_tokenize, combatlog.Record)
    assert from_read == from_tokenize, f"read_log gave {type(from_read).__name__}"
    assert from_tail == from_tokenize, f"tail gave {type(from_tail).__name__}"
