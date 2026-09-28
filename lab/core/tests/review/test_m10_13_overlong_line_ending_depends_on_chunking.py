# Probe from review of m10/13-combatlog-tokenizer; reproduces an over-long line
# reported with ending "" (no line break) by read_log but "\r\n" by tail/tokenize.
"""CONSTRUCTED hostile input (L8), labelled in the ids.

`combatlog` documents ``ending == ""`` as "a last line with no break", and
says an over-long line is yielded as `Unparsed` holding its first
`MAX_LINE_BYTES` bytes. For one line of 3 MiB followed by CRLF and a normal
line, `tokenize(data)` and `tail(path, 2)` report the cut line's ending as
``"\\r\\n"``, but `read_log(path)` reports ``""``: `_Splitter.feed` yields the
line as soon as its unterminated part exceeds `MAX_LINE_BYTES` in one
pending buffer, with the ending of a line that has no break, and then drops
the real break while skipping. What the model says about the same line
depends on how the file was chunked. `follow` shares the splitter.

Positive control: a 1.5 MiB line, whose break arrives in the second chunk,
gets the same `Unparsed` from all three readers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import combatlog

pytestmark = pytest.mark.parser

GOOD = b"4/1/2026 02:16:30.000-4  UNIT_DIED,a\r\n"


def _three_ways(tmp_path: Path, size: int) -> tuple[object, object, object]:
    path = tmp_path / "WoWCombatLog-040126_021630.txt"
    data = b"x" * size + b"\r\n" + GOOD
    path.write_bytes(data)
    from_read = next(iter(combatlog.read_log(path)))
    from_tokenize = next(iter(combatlog.tokenize(data)))
    from_tail = combatlog.tail(path, 2)[0][0]
    return from_read, from_tokenize, from_tail


@pytest.mark.parametrize(
    "size",
    [
        pytest.param(
            combatlog.MAX_LINE_BYTES + combatlog.MAX_LINE_BYTES // 2, id="constructed-1.5MiB"
        ),
    ],
)
def test_control_overlong_line_is_the_same_from_every_reader(tmp_path: Path, size: int) -> None:
    from_read, from_tokenize, from_tail = _three_ways(tmp_path, size)
    assert from_read == from_tokenize == from_tail
    assert isinstance(from_read, combatlog.Unparsed)
    assert from_read.ending == "\r\n"


@pytest.mark.parametrize(
    "size", [pytest.param(3 * combatlog.MAX_LINE_BYTES, id="constructed-3MiB")]
)
def test_overlong_line_keeps_its_ending_in_read_log(tmp_path: Path, size: int) -> None:
    from_read, from_tokenize, from_tail = _three_ways(tmp_path, size)
    assert from_tokenize == from_tail
    assert isinstance(from_tokenize, combatlog.Unparsed)
    assert from_tokenize.ending == "\r\n"
    assert from_read == from_tokenize, (
        f"read_log ending {from_read.ending!r} (type {type(from_read).__name__}) "
        f"vs tokenize/tail {from_tokenize.ending!r}"
    )
