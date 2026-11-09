# Probe from review of m16/12-svmerge-rulings; reproduces a list taken from theirs leaving `-- [n]` index comments that name the wrong index (a dropped entry's comment survives, and theirs' own are lost).
"""Constructed (L8: boundary cases; small documents in the client's layout,
one entry per line with a `-- [n]` comment, CRLF; no fixture in the corpus
holds such comments).

docs/LAB_PLAN.md §13.4, "Output style": taken nodes are laid out in the
target document's style; `svmerge._appended` says a new positional entry
gets "its `-- [n]` where the layout has them". After `--take theirs` takes
a list (M16-12), every `-- [n]` comment in the merged list must name the
index the entry it follows loads as. At 0b2e063:

- a shorter list from theirs keeps ours' last dropped entry's comment,
  so `"Silver Ore", -- [3]` sits on key 1;
- a longer list (round 1's `take_list`) writes theirs' entries without
  their comments and leaves ours' `-- [3]` on key 4.

The values load correctly in both cases (the control); only the text the
player reads is wrong.
"""

from __future__ import annotations

import re

import pytest

from wowlab_core import luadata, svmerge


def _doc(*lines: str) -> bytes:
    return ("\r\n" + "\r\n".join(lines) + "\r\n").encode()


OURS = _doc(
    "BarCharDB = {",
    '["tracked"] = {',
    '"Copper Ore", -- [1]',
    '"Tin Ore", -- [2]',
    '"Iron Ore", -- [3]',
    "},",
    '["n"] = 1,',
    "}",
)
SHORTER = _doc("BarCharDB = {", '["tracked"] = {', '"Silver Ore", -- [1]', "},", '["n"] = 1,', "}")
LONGER = _doc(
    "BarCharDB = {",
    '["tracked"] = {',
    '"Silver Ore", -- [1]',
    '"Copper Ore", -- [2]',
    '"Tin Ore", -- [3]',
    '"Iron Ore", -- [4]',
    "},",
    '["n"] = 1,',
    "}",
)
CASES = pytest.mark.parametrize("theirs", [SHORTER, LONGER], ids=["shorter", "longer"])


def _merged(theirs: bytes) -> bytes:
    result = svmerge.merge(luadata.parse(OURS), luadata.parse(theirs), take="theirs")
    return luadata.serialize(result.document)


@CASES
def test_control_the_values_are_theirs_constructed(theirs: bytes) -> None:
    assert luadata.parse(_merged(theirs)).to_python() == luadata.parse(theirs).to_python()


@CASES
def test_every_index_comment_names_its_entry_constructed(theirs: bytes) -> None:
    out = _merged(theirs).decode()
    body = out[out.index('["tracked"] = {') : out.index("},")]
    entries = [line for line in body.split("\r\n")[1:] if line.strip()]
    for n, line in enumerate(entries, start=1):
        m = re.search(r"-- \[(\d+)\]\s*$", line)
        assert m is None or int(m.group(1)) == n, (n, line, out)
