# Probe from review of m16/12-svmerge-rulings; reproduces a taken list followed by a kept entry losing that entry's own-line comment and the table's indentation (round 2 sets the kept entry's `lead` to None).
"""Constructed (L8: boundary cases; small documents, no fixture).

docs/LAB_PLAN.md §13.4, "Output style": unchanged nodes keep their bytes,
and taken nodes are laid out in the target document's style (line endings,
indentation, spacing). At f3d0846, `_Merger.take_list` replaces the `lead`
of the first kept entry after a taken list with None, to drop the removed
entry's `-- [n]`. That slot also holds the kept entry's own-line comment
and its indentation, so:

- `-- the scale below is per character`, which sits above `scale` and has
  nothing to do with the list, is deleted;
- the tabbed table comes out with no indentation on any line, and with no
  `-- [n]` on the written entries.

At 0b2e063 both survived (with a wrong `-- [2]`, the round-1 finding). The
minimal fix strips only the removed entry's own trailing comment from the
front of that slot (`Entry.comment`, as `_appended` already does for the
closing bytes) and keeps the rest. The values are right (the control).
"""

from __future__ import annotations

from wowlab_core import luadata, svmerge

OURS = (
    b'X = {\n\t"a", -- [1]\n\t"b", -- [2]\n\t-- the scale below is per character\n\tscale = 1,\n}\n'
)
THEIRS = b'X = {\n\t"q", -- [1]\n\tscale = 1,\n}\n'


def _merged() -> bytes:
    result = svmerge.merge(luadata.parse(OURS), luadata.parse(THEIRS), take="theirs")
    return luadata.serialize(result.document)


def test_control_the_values_are_theirs_constructed() -> None:
    assert luadata.parse(_merged()).to_python() == {"X": {1: "q", "scale": 1}}


def test_the_kept_entrys_own_comment_survives_constructed() -> None:
    assert b"\t-- the scale below is per character\n\tscale = 1,\n" in _merged()


def test_the_table_keeps_its_indentation_constructed() -> None:
    out = _merged()
    assert b'\n\t"q",' in out, out
