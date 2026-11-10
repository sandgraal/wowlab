"""M16-54: `sv merge --take theirs` keeps the trailing comment of a kept key
that sits between entries of the list it replaces (docs/LAB_PLAN.md §13.4,
"Output style": unchanged nodes keep their bytes).

The parser keeps an entry's trailing comment at the front of the slot after
it. For `x = 1, -- about x` followed by the list entry `"b"`, that slot is
`"b"`'s lead; taking theirs' list removes `"b"`, so the comment has to be
carried to the next kept slot (the next kept entry's lead, or the closing
bytes), at every such junction, not only the first.

Constructed (L8: boundary cases; small documents, no fixture).
"""

from __future__ import annotations

from wowlab_core import luadata, svmerge


def _merged(ours: bytes, theirs: bytes) -> bytes:
    result = svmerge.merge(luadata.parse(ours), luadata.parse(theirs), take="theirs")
    return luadata.serialize(result.document)


def test_the_ticket_shape_keeps_the_kept_keys_comment_constructed() -> None:
    out = _merged(
        b'X = {"a", -- [1]\nx = 1, -- about x\n"b", -- [2]\n}\n',
        b'X = {"q", x = 1}\n',
    )
    assert out == b'X = {"q", -- [1]\nx = 1, -- about x\n}\n', out
    assert luadata.parse(out).to_python() == {"X": {1: "q", "x": 1}}


def test_every_junction_keeps_its_comment_constructed() -> None:
    out = _merged(
        b'X = {"a", -- [1]\nx = 1, -- about x\n"b", -- [2]\ny = 2, -- about y\n'
        b'"c", -- [3]\nz = 3, -- about z\n}\n',
        b'X = {"q", x = 1, y = 2, z = 3}\n',
    )
    assert out == (
        b'X = {"q", -- [1]\nx = 1, -- about x\ny = 2, -- about y\nz = 3, -- about z\n}\n'
    ), out


def test_indented_table_keeps_comments_and_layout_constructed() -> None:
    out = _merged(
        b'X = {\n\t"a", -- [1]\n\tx = 1, -- about x\n\t"b", -- [2]\n'
        b'\ty = 2, -- about y\n\t"c", -- [3]\n}\n',
        b'X = {"q", "r", x = 1, y = 2}\n',
    )
    assert out == (
        b'X = {\n\t"q", -- [1]\n\t"r", -- [2]\n\tx = 1, -- about x\n\ty = 2, -- about y\n}\n'
    ), out


def test_a_kept_key_without_a_comment_gets_none_constructed() -> None:
    out = _merged(
        b'X = {"a", -- [1]\nx = 1,\n"b", -- [2]\ny = 2, -- about y\n}\n',
        b'X = {"q", x = 1, y = 2}\n',
    )
    assert out == b'X = {"q", -- [1]\nx = 1,\ny = 2, -- about y\n}\n', out
