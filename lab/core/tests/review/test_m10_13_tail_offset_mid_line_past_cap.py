# Probe from review of m10/13-combatlog-tokenizer; reproduces tail() returning an
# offset inside a partial last line longer than its read cap.
"""CONSTRUCTED hostile input (L8), labelled in the ids.

`combatlog.tail` promises: "A partial last line ... is not among them; the
offset is where it starts, so ``follow(path, offset=...)`` picks it up once
its line break arrives." When the partial last line is longer than the
backward read's cap (``(n + 1) * (MAX_LINE_BYTES + 2) + _CHUNK``, about
2 MiB for ``n = 0``), the loop stops with no line break in its window and
returns the file size, which is inside that line. `follow` from there
yields the rest of the line as if it were a line of its own (`Unparsed` at
an offset that is not a line start). `wowlab log tail --follow` passes that
offset straight to `follow`.

Positive control: a 1 MiB partial last line, inside the cap, where the
offset is the line's start.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import combatlog

pytestmark = pytest.mark.parser

GOOD = b"4/1/2026 02:16:30.000-4  UNIT_DIED,a\r\n"


def _offset(tmp_path: Path, partial: int) -> int:
    path = tmp_path / "WoWCombatLog-040126_021630.txt"
    path.write_bytes(GOOD + b"y" * partial)
    entries, end = combatlog.tail(path, 0)
    assert entries == []
    return end


@pytest.mark.parametrize("partial", [pytest.param(1 << 20, id="constructed-1MiB-partial")])
def test_control_offset_is_the_partial_line_start(tmp_path: Path, partial: int) -> None:
    assert _offset(tmp_path, partial) == len(GOOD)


@pytest.mark.parametrize("partial", [pytest.param(5 << 20, id="constructed-5MiB-partial")])
def test_offset_past_the_cap_is_still_a_line_start(tmp_path: Path, partial: int) -> None:
    end = _offset(tmp_path, partial)
    assert end == len(GOOD), f"offset {end} is inside the partial line starting at {len(GOOD)}"
