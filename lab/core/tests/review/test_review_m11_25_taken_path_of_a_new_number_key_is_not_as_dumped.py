# Probe from review of m11/25-path-grammar; reproduces `sv merge --key SRC=DST` reporting a new number key as typed (`A[0x11]`, `A[2.0]`) while it writes and `sv dump` prints `A[17]`, `A[2]`
"""`docs/LAB_PLAN.md` §13.4, M11-09T rulings: "Paths are spelled as `wowlab
sv dump` prints them". On main, a `--key` copy to a number key ours lacks
reported the path in the canonical text it also wrote (`[17]`). M11-25 made
a number step keep its typed text (so `[1e400]` reads back), and `_Placer`
spells the new destination with `step.spelling`, so the report now says
`A[0x11]` while the file gets `[17] = ...` and `sv dump` prints `A[17]`.
The reported path still reads back to the same key, but it is not the
spelling the dump prints for the key that was written.

Constructed (L8, boundary): no real fixture has a hex or `2.0` key. The
positive control types the canonical spelling (`A[17]`), which main and the
branch both report as written.
"""

from __future__ import annotations

import pytest

from wowlab_core import luadata, svmerge

SOURCE = b'A = {\n\t["x"] = 1,\n}\n'


def _taken_and_dumped(dst: str) -> tuple[str, str]:
    doc = luadata.parse(SOURCE)
    result = svmerge.merge(doc, doc, keys=[f"A.x={dst}"])
    (taken,) = result.taken
    table = result.document.assignments[0].value
    assert isinstance(table, luadata.LuaTable)
    written = table.entries[-1]  # the appended destination (no positional entries)
    return taken.path, "A" + svmerge.path_step(written, None)


def test_positive_control_canonical_number_destination_constructed() -> None:
    taken, dumped = _taken_and_dumped("A[17]")
    assert taken == dumped == "A[17]"


@pytest.mark.parametrize("dst", ["A[0x11]", "A[2.0]", "A[1.5e0]"])
def test_taken_path_of_a_new_number_key_is_spelled_as_dumped_constructed(dst: str) -> None:
    taken, dumped = _taken_and_dumped(dst)
    assert taken == dumped, f"reported {taken}, but the file holds and sv dump prints {dumped}"
