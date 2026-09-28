"""Graders for the `wowlab_core.luadata` serializer on constructed inputs
(M10-12T; L3, L4, L6, L8).

Every input here is constructed, which the file name puts in every test id.
The main one is §4.2's example in the remembered retail layout
(`docs/LAB_FORMATS.md`: tab indentation, a `-- [n]` comment after each
positional entry), in LF and CRLF. L8: it is a stand-in until a real
capture shows that style (no client has yet been seen writing it, §6.4
amendment 2026-09-27 item 6, **[verify]**); replace it with that capture
when one lands. The layout is followed when a document or sibling shows it,
never chosen by default. The rest are boundary cases no capture shows: `;`
separators, a two-space indentation unit, documents and siblings showing
only half of the indentation/comment pairing, sibling trees made of
constructed files, unmodified documents in shapes the grammar accepts but
the client does not write, and hostile edits the data-only rule refuses
(§6.4 amendment 2026-09-27 item 2). The real captures, and the seam every
grader here uses (`serialize(document, *, target=None,
lab_written=frozenset())`, `None` slots left to the serializer,
`close_lead=None` when appending), are in
`test_luadata_serializer_fixtures.py`. Every grader carries one marker line
that M10-12 deletes; nothing else here is the implementer's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from _luadata_edits import (
    ACCOUNT_SV,
    CHARACTER_SV,
    append,
    assignment,
    boolean,
    document,
    edit,
    install,
    keyed,
    number,
    numbered,
    positional,
    reference_document,
    reference_text,
    replace_once,
    set_leaf,
    string,
    table,
)
from _luadata_oracle import load, rebuild

pytestmark = pytest.mark.parser

EOLS = [b"\n", b"\r\n"]
EOL_IDS = ["lf", "crlf"]


@pytest.fixture
def luadata() -> Any:
    return load()


def _lines(eol: bytes, *lines: bytes) -> bytes:
    return b"".join(line + eol for line in lines)


def _reference(eol: bytes) -> bytes:
    return reference_text(indent=b"\t", comments=True, eol=eol)


# ── unmodified: byte for byte ───────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize("eol", EOLS, ids=EOL_IDS)
def test_reference_layout_round_trips(luadata: Any, eol: bytes) -> None:
    data = _reference(eol)
    assert luadata.serialize(luadata.parse(data)) == data


UNMODIFIED = {
    "comments-everywhere-no-final-eol": (
        b"-- head\r\n"
        b"X = { -- open\r\n"
        b"  a = 1; -- one\r\n"
        b"  [true] = 'q',\r\n"
        b"  -- own line\r\n"
        b'  [1.50] = "a\\tb\\"c" --[1] after a value\r\n'
        b"}\r\n"
        b"-- end"
    ),
    "cr-line-endings": b"\rX = {\r\t1,\r\t{\r\t},\r}\r",
    "no-leading-blank-line-lf": b'X = 1\nY = "s"\n',
    "mixed-line-endings": b'\r\nX = {\n\t["a"] = 1,\r\n\t["b"] = {\n\t},\r\n}\n',
    "odd-spacing": b'X={ [ "k" ] = { } , 1 ; [ 2 ]=false}  \t',
    "number-spellings": b"\nX = {0x1F, 1E-07, -0, 100.000, 1e+15, -2.5e-3}\n",
    "escapes": b'\nX = "\\195\\169\\n\\\nnext\\065\\0"\n',
    "two-nils": b"\r\nX = nil\r\nY = nil\r\n",
    "empty-document": b"",
    "only-a-comment": b"-- nothing assigned\n",
    "empty-table-inline": b"\nX = {}\n",
}


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize("data", list(UNMODIFIED.values()), ids=list(UNMODIFIED))
def test_unmodified_accepted_documents_round_trip(luadata: Any, data: bytes) -> None:
    """§6.4 amendment item 7: for an unmodified document the serializer is
    the rebuild, whatever shape the grammar accepted (L4 keeps comments the
    client never writes)."""
    doc = luadata.parse(data)
    assert rebuild(luadata, doc) == data, "the constructed input itself is well formed"
    assert luadata.serialize(doc) == data


# ── new entries in the reference layout ─────────────────────────────────────


def _cases(eol: bytes) -> list[Any]:
    return [
        pytest.param(
            eol,
            ("list",),
            lambda lua: [positional(lua, string(lua, "third"))],
            (
                b'\t\t"second", -- [2]' + eol + b"\t},",
                b'\t\t"second", -- [2]' + eol + b'\t\t"third", -- [3]' + eol + b"\t},",
            ),
            id="positional-numbered-comment",
        ),
        pytest.param(
            eol,
            ("list",),
            lambda lua: [positional(lua, number(lua, "3")), positional(lua, boolean(lua, False))],
            (
                b'\t\t"second", -- [2]' + eol + b"\t},",
                b'\t\t"second", -- [2]'
                + eol
                + b"\t\t3, -- [3]"
                + eol
                + b"\t\tfalse, -- [4]"
                + eol
                + b"\t},",
            ),
            id="two-positional",
        ),
        pytest.param(
            eol,
            (),
            lambda lua: [keyed(lua, "lab_new", number(lua, "7"))],
            (
                b'\t["scale"] = 0.8500000238418579,' + eol + b"}",
                b'\t["scale"] = 0.8500000238418579,' + eol + b'\t["lab_new"] = 7,' + eol + b"}",
            ),
            id="string-key-depth-1",
        ),
        pytest.param(
            eol,
            (),
            lambda lua: [numbered(lua, "43", boolean(lua, False))],
            (
                b'\t["scale"] = 0.8500000238418579,' + eol + b"}",
                b'\t["scale"] = 0.8500000238418579,' + eol + b"\t[43] = false," + eol + b"}",
            ),
            id="number-key",
        ),
        pytest.param(
            eol,
            ("profileKeys",),
            lambda lua: [keyed(lua, "lab_new", string(lua, "Other"))],
            (
                b'\t\t["Name - Realm"] = "Default",' + eol + b"\t},",
                b'\t\t["Name - Realm"] = "Default",'
                + eol
                + b'\t\t["lab_new"] = "Other",'
                + eol
                + b"\t},",
            ),
            id="string-key-depth-2",
        ),
        pytest.param(
            eol,
            (),
            lambda lua: [keyed(lua, "lab_t", table(lua, keyed(lua, "a", number(lua, "1"))))],
            (
                b'\t["scale"] = 0.8500000238418579,' + eol + b"}",
                b'\t["scale"] = 0.8500000238418579,'
                + eol
                + b'\t["lab_t"] = {'
                + eol
                + b'\t\t["a"] = 1,'
                + eol
                + b"\t},"
                + eol
                + b"}",
            ),
            id="new-table-indented",
        ),
        pytest.param(
            eol,
            ("list",),
            lambda lua: [positional(lua, table(lua, keyed(lua, "a", number(lua, "1"))))],
            (
                b'\t\t"second", -- [2]' + eol + b"\t},",
                b'\t\t"second", -- [2]'
                + eol
                + b"\t\t{"
                + eol
                + b'\t\t\t["a"] = 1,'
                + eol
                + b"\t\t}, -- [3]"
                + eol
                + b"\t},",
            ),
            id="positional-table-comment-on-closing-line-verify",
        ),
    ]


def _all_cases() -> list[Any]:
    return [
        pytest.param(*case.values, id=f"{eol_id}-{case.id}")
        for eol, eol_id in zip(EOLS, EOL_IDS, strict=True)
        for case in _cases(eol)
    ]


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize(("eol", "steps", "new", "change"), _all_cases())
def test_new_entries_follow_the_reference_layout(
    luadata: Any, eol: bytes, steps: tuple[str, ...], new: Any, change: tuple[bytes, bytes]
) -> None:
    """A tab-indented, commented document keeps both (owner decision
    2026-09-22): one tab per level, `,` after every entry, `-- [n]` after a
    new positional entry numbered from its place, the old last entry's
    comment kept after its separator with one space, and the document's own
    line ending (LF stays LF). A positional table's `-- [n]` goes after its
    closing `},` (**[verify]**: remembered retail form, no capture shows
    it)."""
    source = _reference(eol)
    doc = append(luadata, luadata.parse(source), "MyAddonDB", steps, *new(luadata))
    assert luadata.serialize(doc) == replace_once(source, *change)


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize("eol", EOLS, ids=EOL_IDS)
def test_new_assignment_follows_the_reference_layout(luadata: Any, eol: bytes) -> None:
    source = _reference(eol)
    doc = luadata.parse(source)
    doc = doc._replace(
        assignments=(*doc.assignments, assignment(luadata, "LabNewVar", number(luadata, "5")))
    )
    assert luadata.serialize(doc) == source + b"LabNewVar = 5" + eol


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize("eol", EOLS, ids=EOL_IDS)
def test_one_new_node_object_is_laid_out_per_place(luadata: Any, eol: bytes) -> None:
    """Placement, not identity: one entry object appended at depth 1 and at
    depth 2 gets one and two tabs; one positional entry object appended
    twice gets `-- [3]` then `-- [4]`."""
    source = _reference(eol)
    same_key = keyed(luadata, "lab_same", number(luadata, "1"))
    same_pos = positional(luadata, string(luadata, "again"))
    doc = luadata.parse(source)
    doc = append(luadata, doc, "MyAddonDB", (), same_key)
    doc = append(luadata, doc, "MyAddonDB", ("profileKeys",), same_key)
    doc = append(luadata, doc, "MyAddonDB", ("list",), same_pos, same_pos)
    expected = replace_once(
        source,
        b'\t["scale"] = 0.8500000238418579,' + eol + b"}",
        b'\t["scale"] = 0.8500000238418579,' + eol + b'\t["lab_same"] = 1,' + eol + b"}",
    )
    expected = replace_once(
        expected,
        b'\t\t["Name - Realm"] = "Default",' + eol,
        b'\t\t["Name - Realm"] = "Default",' + eol + b'\t\t["lab_same"] = 1,' + eol,
    )
    expected = replace_once(
        expected,
        b'\t\t"second", -- [2]' + eol,
        b'\t\t"second", -- [2]' + eol + b'\t\t"again", -- [3]' + eol + b'\t\t"again", -- [4]' + eol,
    )
    assert luadata.serialize(doc) == expected


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize("keep_lead", [True, False], ids=["lead-kept", "lead-none"])
@pytest.mark.parametrize("eol", EOLS, ids=EOL_IDS)
def test_leaf_edit_keeps_the_comments_and_every_other_byte(
    luadata: Any, eol: bytes, keep_lead: bool
) -> None:
    """Changing a commented positional value changes its text only: its own
    `-- [2]` and every other line stay as they were."""
    source = _reference(eol)
    doc = set_leaf(
        luadata,
        luadata.parse(source),
        "MyAddonDB",
        ("list", 2),
        string(luadata, "2nd"),
        keep_lead=keep_lead,
    )
    doc = set_leaf(
        luadata, doc, "MyAddonDB", ("scale",), number(luadata, "0.5"), keep_lead=keep_lead
    )
    expected = replace_once(source, b'"second", -- [2]', b'"2nd", -- [2]')
    expected = replace_once(expected, b"= 0.8500000238418579,", b"= 0.5,")
    assert luadata.serialize(doc) == expected


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize("eol", EOLS, ids=EOL_IDS)
def test_replacing_a_subtree_keeps_its_siblings(luadata: Any, eol: bytes) -> None:
    """A new table in place of `profileKeys` is laid out in the document's
    style; `list`, `[42]` and `scale` keep their bytes."""
    source = _reference(eol)
    new = table(luadata, keyed(luadata, "b", boolean(luadata, True)))
    doc = edit(luadata, luadata.parse(source), "MyAddonDB", ("profileKeys",), lambda _old: new)
    expected = replace_once(
        source,
        b'\t["profileKeys"] = {' + eol + b'\t\t["Name - Realm"] = "Default",' + eol,
        b'\t["profileKeys"] = {' + eol + b'\t\t["b"] = true,' + eol,
    )
    assert luadata.serialize(doc) == expected


# ── other separators and indentation units ──────────────────────────────────

SEMICOLONS = _lines(
    b"\r\n",
    b"",
    b"LabSemiDB = {",
    b'\t["a"] = 1;',
    b'\t["b"] = {',
    b'\t\t"x"; -- [1]',
    b"\t};",
    b"}",
)
TWO_SPACES = _lines(
    b"\n",
    b"",
    b"LabTwoDB = {",
    b'  ["a"] = {',
    b'    ["b"] = 1,',
    b"  },",
    b'  ["list"] = {',
    b'    "x", -- [1]',
    b"  },",
    b"}",
)
OTHER_STYLES = [
    pytest.param(
        SEMICOLONS,
        "LabSemiDB",
        (),
        lambda lua: [keyed(lua, "lab_new", number(lua, "7"))],
        (b"\t};\r\n}", b'\t};\r\n\t["lab_new"] = 7;\r\n}'),
        id="semicolon-string-key",
    ),
    pytest.param(
        SEMICOLONS,
        "LabSemiDB",
        ("b",),
        lambda lua: [positional(lua, string(lua, "y"))],
        (b'\t\t"x"; -- [1]\r\n', b'\t\t"x"; -- [1]\r\n\t\t"y"; -- [2]\r\n'),
        id="semicolon-positional",
    ),
    pytest.param(
        TWO_SPACES,
        "LabTwoDB",
        ("a",),
        lambda lua: [keyed(lua, "lab_new", number(lua, "7"))],
        (b'    ["b"] = 1,\n', b'    ["b"] = 1,\n    ["lab_new"] = 7,\n'),
        id="two-space-string-key-depth-2",
    ),
    pytest.param(
        TWO_SPACES,
        "LabTwoDB",
        ("list",),
        lambda lua: [positional(lua, string(lua, "y"))],
        (b'    "x", -- [1]\n', b'    "x", -- [1]\n    "y", -- [2]\n'),
        id="two-space-positional",
    ),
    pytest.param(
        TWO_SPACES,
        "LabTwoDB",
        (),
        lambda lua: [numbered(lua, "5", string(lua, "five"))],
        (b"  },\n}\n", b'  },\n  [5] = "five",\n}\n'),
        id="two-space-number-key-depth-1",
    ),
]


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize(("source", "variable", "steps", "new", "change"), OTHER_STYLES)
def test_new_entries_keep_the_documents_separator_and_indentation_unit(
    luadata: Any,
    source: bytes,
    variable: str,
    steps: tuple[str, ...],
    new: Any,
    change: tuple[bytes, bytes],
) -> None:
    """§6.4: "its indentation unit or none ... its separators": a `;`
    document gets `;`, a two-space document two spaces per level."""
    doc = append(luadata, luadata.parse(source), variable, steps, *new(luadata))
    assert luadata.serialize(doc) == replace_once(source, *change)


# ── the indentation / comment pairing (§6.4 amendment 2026-09-27, item 5) ───

INDENT_ONLY = _lines(b"\n", b"", b"LabIndentDB = {", b'\t["a"] = 1,', b"}")
COMMENTS_ONLY = _lines(b"\n", b"", b'LabCommentsDB = {"a", -- [1]', b"}")


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize(
    ("source", "variable", "change"),
    [
        pytest.param(
            INDENT_ONLY,
            "LabIndentDB",
            (b'\t["a"] = 1,\n}', b'\t["a"] = 1,\n\t"p", -- [1]\n}'),
            id="shows-only-tab-indentation",
        ),
        pytest.param(
            COMMENTS_ONLY,
            "LabCommentsDB",
            (b'{"a", -- [1]\n}', b'{"a", -- [1]\n\t"p", -- [2]\n}'),
            id="shows-only-array-comments",
        ),
    ],
)
def test_a_document_showing_half_the_pairing_decides_both(
    luadata: Any, source: bytes, variable: str, change: tuple[bytes, bytes]
) -> None:
    """Item 5 (owner): indentation and `-- [n]` are one pairing. A document
    whose only table is tab-indented but has no positional entry writes
    `-- [n]` on a new one; a document whose only positional entry carries
    `-- [1]` but sits on the brace's line (showing no indentation) indents a
    new one by a tab."""
    doc = append(
        luadata, luadata.parse(source), variable, (), positional(luadata, string(luadata, "p"))
    )
    assert luadata.serialize(doc) == replace_once(source, *change)


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_appending_drops_own_line_comments_before_the_old_brace(luadata: Any) -> None:
    """Item 1: with `close_lead=None`, the old last entry's comment is
    written after its separator with one space, and an own-line comment that
    stood before the old `}` is dropped by that edit."""
    source = _lines(
        b"\r\n", b"", b"LabNoteDB = {", b'["a"] = 1, -- keep me', b"-- dropped by the edit", b"}"
    )
    doc = append(
        luadata, luadata.parse(source), "LabNoteDB", (), keyed(luadata, "b", number(luadata, "2"))
    )
    expected = _lines(b"\r\n", b"", b"LabNoteDB = {", b'["a"] = 1, -- keep me', b'["b"] = 2,', b"}")
    assert luadata.serialize(doc) == expected


# ── new documents: sibling files, else the Forever fallback ─────────────────

TAB_SIBLING = (
    b"",
    b"LabSiblingDB = {",
    b'\t["opts"] = {',
    b'\t\t["size"] = 3,',
    b"\t},",
    b'\t["seen"] = {',
    b'\t\t"x", -- [1]',
    b"\t},",
    b"}",
)
FLAT_SIBLING = (
    b"",
    b"LabFlatDB = {",
    b'["opts"] = {',
    b'["size"] = 3,',
    b"},",
    b'["seen"] = {',
    b'"x",',
    b"},",
    b"}",
)


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize("eol", EOLS, ids=EOL_IDS)
def test_new_document_follows_a_tab_indented_commented_sibling(
    luadata: Any, eol: bytes, tmp_path: Path
) -> None:
    """A new file beside a tab-indented, commented sibling takes its layout
    and its line ending: §4.2's example in that sibling's style, LF or CRLF."""
    root = install(
        tmp_path / "install",
        {"_lab_one_": {f"{ACCOUNT_SV}/Sibling.lua": _lines(eol, *TAB_SIBLING)}},
    )
    target = root / "_lab_one_" / ACCOUNT_SV / "LabNewAddon.lua"
    assert luadata.serialize(reference_document(luadata), target=target) == _reference(eol)


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize(
    "sibling",
    [INDENT_ONLY, COMMENTS_ONLY],
    ids=["sibling-shows-only-tab-indentation", "sibling-shows-only-array-comments"],
)
def test_a_sibling_showing_half_the_pairing_decides_both(
    luadata: Any, sibling: bytes, tmp_path: Path
) -> None:
    """Item 5: a sibling that shows only tab indentation, or only `-- [n]`,
    gives a new file both, with its LF."""
    root = install(tmp_path / "install", {"_lab_one_": {f"{ACCOUNT_SV}/Sibling.lua": sibling}})
    target = root / "_lab_one_" / ACCOUNT_SV / "LabNewAddon.lua"
    assert luadata.serialize(reference_document(luadata), target=target) == _reference(b"\n")


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_new_document_takes_each_property_from_where_it_is_shown(
    luadata: Any, tmp_path: Path
) -> None:
    """The only sibling is `LabX = nil`, LF: it shows the line ending and
    nothing else, so the pairing is the fallback's (flat, no `-- [n]`) and
    the line ending is the sibling's LF."""
    root = install(
        tmp_path / "install", {"_lab_one_": {f"{ACCOUNT_SV}/Nil.lua": b"\nLabX = nil\n"}}
    )
    target = root / "_lab_one_" / ACCOUNT_SV / "LabNewAddon.lua"
    out = luadata.serialize(reference_document(luadata), target=target)
    assert out == reference_text(indent=b"", comments=False, eol=b"\n")


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_each_flavor_folder_uses_its_own_siblings(luadata: Any, tmp_path: Path) -> None:
    """L6: two flavor folders of one install, with made-up names and
    different styles (tab and LF; flat and CRLF, in a character's
    SavedVariables). A new file in each follows its own folder only."""
    root = install(
        tmp_path / "install",
        {
            "_lab_tabs_": {f"{ACCOUNT_SV}/Sibling.lua": _lines(b"\n", *TAB_SIBLING)},
            "_lab_flat_": {f"{CHARACTER_SV}/Sibling.lua": _lines(b"\r\n", *FLAT_SIBLING)},
        },
    )
    doc = reference_document(luadata)
    tabs = luadata.serialize(doc, target=root / "_lab_tabs_" / ACCOUNT_SV / "LabNewAddon.lua")
    flat = luadata.serialize(doc, target=root / "_lab_flat_" / ACCOUNT_SV / "LabNewAddon.lua")
    assert tabs == reference_text(indent=b"\t", comments=True, eol=b"\n")
    assert flat == reference_text(indent=b"", comments=False, eol=b"\r\n")


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_new_document_without_source_or_siblings_is_the_forever_layout(luadata: Any) -> None:
    """Item 6 (owner): with nothing to read, the layout every captured file
    shows: no indentation, no `-- [n]`, CRLF, a leading empty line, `,`
    after every entry. §4.2's tab-indented form is never the default."""
    out = luadata.serialize(reference_document(luadata))
    assert out == reference_text(indent=b"", comments=False, eol=b"\r\n")
    assert luadata.serialize(luadata.parse(out)) == out


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_new_empty_table_without_source_or_siblings_is_two_lines(luadata: Any) -> None:
    """Item 6: the fallback's empty table is `{` and `}` on two lines."""
    doc = document(
        luadata,
        assignment(luadata, "LabEmptyDB", table(luadata, keyed(luadata, "e", table(luadata)))),
    )
    assert luadata.serialize(doc) == b'\r\nLabEmptyDB = {\r\n["e"] = {\r\n},\r\n}\r\n'


# ── data only: serialize refuses what is not data (items 2, 8, 9) ───────────

DATA_SOURCE = _lines(
    b"\r\n",
    b"",
    b"LabDataDB = {",
    b'\t["a"] = 1,',
    b'\t["list"] = {',
    b'\t\t"x", -- [1]',
    b"\t},",
    b"}",
)


def _with_first_assignment(doc: Any, **changes: Any) -> Any:
    first = doc.assignments[0]._replace(**changes)
    return doc._replace(assignments=(first, *doc.assignments[1:]))


def _with_entry(doc: Any, index: int, **changes: Any) -> Any:
    """`doc` with entry `index` of `LabDataDB` changed (0 is `["a"]`, 1 is
    `["list"]`, the last)."""
    table_value = doc.assignments[0].value
    entries = list(table_value.entries)
    entries[index] = entries[index]._replace(**changes)
    return _with_first_assignment(doc, value=table_value._replace(entries=tuple(entries)))


def _with_close_lead(doc: Any, close_lead: bytes | None) -> Any:
    return _with_first_assignment(
        doc, value=doc.assignments[0].value._replace(close_lead=close_lead)
    )


LONG_CODE = b"os.exit() " * 10

# id: (edit, the refused slot's bytes (any one of them), (line, column) where
# they would start in the output, or None where item 9 leaves it open).
# Positions: DATA_SOURCE line 2 is `LabDataDB = {` (13 bytes), line 3
# `\t["a"] = 1,`, line 6 `\t},`, line 7 `}`.
HOSTILE: dict[str, tuple[Any, tuple[bytes, ...], tuple[int, int] | None]] = {
    "lead-holds-a-call": (
        lambda lua, d: _with_first_assignment(d, lead=b"\r\nos.exit()\r\n"),
        (b"\r\nos.exit()\r\n",),
        (1, 1),
    ),
    "lead-holds-a-long-call-token-cut-at-40": (
        lambda lua, d: _with_entry(d, 0, lead=b"\r\n" + LONG_CODE),
        (b"\r\n" + LONG_CODE,),
        (2, 14),
    ),
    "lead-holds-a-long-comment": (
        lambda lua, d: _with_entry(d, 0, lead=b"\r\n--[[ hidden ]]\t"),
        (b"\r\n--[[ hidden ]]\t",),
        (2, 14),
    ),
    "lead-holds-a-level-long-comment": (
        lambda lua, d: _with_entry(d, 0, lead=b"\r\n--[==[ x ]==]\t"),
        (b"\r\n--[==[ x ]==]\t",),
        (2, 14),
    ),
    "lead-ends-in-an-open-comment-swallowing-the-entry": (
        lambda lua, d: _with_entry(d, 0, lead=b"\r\n-- "),
        (b"\r\n-- ",),
        (2, 14),
    ),
    "tail-holds-a-nul": (
        lambda lua, d: d._replace(tail=b"\r\n\x00"),
        (b"\r\n\x00",),
        (7, 2),
    ),
    "comment-in-a-lead-holds-a-nul": (
        lambda lua, d: _with_entry(d, 0, lead=b"\r\n-- a\x00b\r\n\t"),
        (b"\r\n-- a\x00b\r\n\t",),
        (2, 14),
    ),
    "key-close-lead-holds-a-call": (
        lambda lua, d: _with_entry(d, 0, key_close_lead=b" f() "),
        (b" f() ",),
        (3, 6),
    ),
    "eq-lead-holds-an-operator": (
        lambda lua, d: _with_entry(d, 0, eq_lead=b" + 1 "),
        (b" + 1 ",),
        (3, 7),
    ),
    "eq-lead-ends-in-an-open-comment": (
        lambda lua, d: _with_entry(d, 0, eq_lead=b" --"),
        (b" --",),
        (3, 7),
    ),
    "sep-lead-holds-a-call": (
        lambda lua, d: _with_entry(d, 0, sep_lead=b" f() "),
        (b" f() ",),
        (3, 11),
    ),
    "sep-holds-code": (
        lambda lua, d: _with_entry(d, 0, sep=b",os.exit(),"),
        (b",os.exit(),",),
        (3, 11),
    ),
    "comment-holds-a-line-break-and-code": (
        lambda lua, d: _with_close_lead(_with_entry(d, 1, comment=b"-- x\r\nos.exit()"), None),
        (b"-- x\r\nos.exit()", b" -- x\r\nos.exit()"),
        None,
    ),
    "close-lead-holds-setmetatable": (
        lambda lua, d: _with_close_lead(d, b"\r\nsetmetatable({}, {})\r\n"),
        (b"\r\nsetmetatable({}, {})\r\n",),
        (6, 4),
    ),
    "string-raw-is-a-function": (
        lambda lua, d: _with_entry(d, 0, value=lua.LuaString(lead=b" ", raw=b"function() end")),
        (b"function() end",),
        (3, 10),
    ),
    "number-raw-is-a-function": (
        lambda lua, d: _with_entry(d, 0, value=lua.LuaNumber(lead=b" ", raw="function() end")),
        (b"function() end",),
        (3, 10),
    ),
    "number-raw-is-arithmetic": (
        lambda lua, d: _with_entry(d, 0, value=lua.LuaNumber(lead=b" ", raw="1+1")),
        (b"1+1",),
        (3, 10),
    ),
    "string-raw-is-a-concatenation": (
        lambda lua, d: _with_entry(d, 0, value=lua.LuaString(lead=b" ", raw=b'"a" .. "b"')),
        (b'"a" .. "b"',),
        (3, 10),
    ),
    "string-raw-holds-a-raw-line-break": (
        lambda lua, d: _with_entry(d, 0, value=lua.LuaString(lead=b" ", raw=b'"a\r\nb"')),
        (b'"a\r\nb"',),
        (3, 10),
    ),
    "nil-inside-a-table": (
        lambda lua, d: _with_entry(d, 0, value=lua.LuaNil(lead=b" ")),
        (b"nil", b" nil"),
        None,
    ),
    "string-key-is-a-call": (
        lambda lua, d: _with_entry(d, 0, key=lua.LuaString(lead=b"", raw=b"f()")),
        (b"f()",),
        (3, 3),
    ),
    "number-key-is-a-name": (
        lambda lua, d: _with_entry(
            d, 0, style=lua.KeyStyle.NUMBER, key=lua.LuaNumber(lead=b"", raw="math.huge")
        ),
        (b"math.huge",),
        (3, 3),
    ),
    "name-key-is-a-path": (
        lambda lua, d: _with_entry(d, 0, style=lua.KeyStyle.NAME, key="os.exit"),
        (b"os.exit",),
        (3, 2),
    ),
    "name-key-is-a-keyword": (
        lambda lua, d: _with_entry(d, 0, style=lua.KeyStyle.NAME, key="function"),
        (b"function",),
        (3, 2),
    ),
    "assignment-name-is-a-path": (
        lambda lua, d: _with_first_assignment(d, name="a.b"),
        (b"a.b",),
        (2, 1),
    ),
    "assignment-name-is-a-keyword": (
        lambda lua, d: _with_first_assignment(d, name="end"),
        (b"end",),
        (2, 1),
    ),
}


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize(
    ("hostile", "refused", "position"), list(HOSTILE.values()), ids=list(HOSTILE)
)
def test_serialize_refuses_what_is_not_data(
    luadata: Any, hostile: Any, refused: tuple[bytes, ...], position: tuple[int, int] | None
) -> None:
    """Items 2, 8 and 9: every slot is checked. A trivia slot holding
    anything but whitespace and `--` line comments (a call, a long comment,
    a NUL, an operator), or a comment that does not end with a line break
    inside its slot (it would swallow what follows), a `sep` other than
    `,`, `;` or empty, an `Entry.comment` that is not one line comment, a
    `raw`, key or name that is not a literal §4.1 accepts, or a `nil` inside
    a table raises `LuaDataError`; no bytes come back, so L3 holds for
    writes. `.token` is at most the first 40 bytes of the refused slot, and
    `.line` / `.column` are where it would start in the output (Lua line
    counting, 1-based, the column in bytes)."""
    doc = hostile(luadata, luadata.parse(DATA_SOURCE))
    with pytest.raises(luadata.LuaDataError) as caught:
        luadata.serialize(doc)
    token = caught.value.token
    assert isinstance(token, bytes)
    assert 1 <= len(token) <= 40
    assert any(slot.startswith(token) for slot in refused), token
    if position is not None:
        assert (caught.value.line, caught.value.column) == position


def _legal_trivia(lua: Any, d: Any) -> Any:
    d = _with_first_assignment(d, lead=b"\r\n-- a comment line\r\n \t")
    d = _with_entry(
        d,
        0,
        lead=b"\r\n\t--[1] not a long comment\r\n\t",
        key_close_lead=b"  ",
        eq_lead=b"  \t",
        sep_lead=b" \t-- before the separator\r\n\t",
        sep=b";",
    )
    d = _with_entry(d, 1, sep_lead=b" ", sep=b"")
    d = _with_close_lead(d, b" -- after the list\r\n\f\v")
    return d._replace(tail=b"\r\n-- end\r\n")


LEGAL_SLOTS = {
    "given-trivia-and-separators": (_legal_trivia, None),
    "one-line-comment-after-the-old-last-entry": (
        lambda lua, d: _with_close_lead(_with_entry(d, 1, comment=b"-- fine"), None),
        replace_once(DATA_SOURCE, b"\t},\r\n}", b"\t}, -- fine\r\n}"),
    ),
}


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize(("legal", "expected"), list(LEGAL_SLOTS.values()), ids=list(LEGAL_SLOTS))
def test_serialize_writes_legal_slot_contents_as_given(
    luadata: Any, legal: Any, expected: bytes | None
) -> None:
    """The positive control for the data-only rule, on the same slots:
    whitespace (space, tab, form feed, vertical tab, line breaks) and `--`
    line comments ending with a line break (`--[1]` included) in a lead, a
    `key_close_lead`, an `eq_lead`, a `sep_lead` and a `close_lead`; a `;`
    separator and an empty one on the last entry; a comment in the tail; a
    one-line `Entry.comment` written after the old last entry's separator.
    All written exactly, and the output parses back to the same bytes."""
    doc = legal(luadata, luadata.parse(DATA_SOURCE))
    out = luadata.serialize(doc)
    assert out == (rebuild(luadata, doc) if expected is None else expected)
    assert luadata.serialize(luadata.parse(out)) == out
