"""Graders for the `wowlab_core.luadata` serializer on constructed inputs
(M10-12T; L4, L6, L8).

Every input here is constructed, which the file name puts in every test id.
The main one is §4.2's reference layout (`docs/LAB_FORMATS.md`: tab
indentation, a `-- [n]` comment after each positional entry), in LF and
CRLF. L8: it is a stand-in until a real capture shows that style (no client
has yet been seen writing it, §6.4 **[verify]**); replace it with that
capture when one lands. The rest are boundary cases no capture shows: `;`
separators, a two-space indentation unit, sibling trees made of constructed
files, and unmodified documents in shapes the grammar accepts but the
client does not write. The real captures, and the seam every grader here
uses (`serialize(document, *, target=None)`, `None` slots left to the
serializer, `close_lead=None` when appending), are in
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
    ]


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize(
    ("eol", "steps", "new", "change"), [case for eol in EOLS for case in _cases(eol)]
)
def test_new_entries_follow_the_reference_layout(
    luadata: Any, eol: bytes, steps: tuple[str, ...], new: Any, change: tuple[bytes, bytes]
) -> None:
    """A tab-indented, commented document keeps both (owner decision
    2026-09-22): one tab per level, `,` after every entry, `-- [n]` after a
    new positional entry numbered from its place, the old last entry's
    comment kept, and the document's own line ending (LF stays LF)."""
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


# ── new documents: sibling files, else §4.2 ─────────────────────────────────

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
def test_new_document_takes_each_property_from_where_it_is_shown(
    luadata: Any, tmp_path: Path
) -> None:
    """The only sibling is `LabX = nil`, LF: it shows the line ending and
    nothing else, so indentation and comments are §4.2's."""
    root = install(
        tmp_path / "install", {"_lab_one_": {f"{ACCOUNT_SV}/Nil.lua": b"\nLabX = nil\n"}}
    )
    target = root / "_lab_one_" / ACCOUNT_SV / "LabNewAddon.lua"
    assert luadata.serialize(reference_document(luadata), target=target) == _reference(b"\n")


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
def test_new_document_without_source_or_siblings_is_the_reference_layout(luadata: Any) -> None:
    """No document, no target: §4.2's layout, with the line ending chosen
    by file kind (CRLF, 2026-09-22 amendment), not by platform."""
    out = luadata.serialize(reference_document(luadata))
    assert out == _reference(b"\r\n")
    assert luadata.serialize(luadata.parse(out)) == out
