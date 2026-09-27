"""Graders for the `wowlab_core.luadata` serializer against the real
SavedVariables captures (M10-12T; L1, L4, L6, L8).

Written before the serializer exists, from `docs/LAB_PLAN.md` §6.4
(serializer half, the owner decision of 2026-09-22 on the document's own
style, and the sibling-file fallback) and `docs/LAB_FORMATS.md` §4.2 with
its 2026-09-22 amendments (the Forever part-2 capture: no indentation, no
`-- [n]` comments, CRLF everywhere, a leading empty line, `,` after every
entry, two-line empty tables). Every grader carries one marker line that
M10-12 deletes; nothing else here is the implementer's to change.
Constructed inputs (§4.2's tab-indented, commented reference layout, other
separators and indentation units, sibling trees made of constructed files)
are in `test_luadata_serializer_constructed.py`; a grader here that mixes in
a constructed file says `constructed` in its test id.

The seam, added to the one `test_luadata_fixtures.py` pins for the parser
------------------------------------------------------------------------
- `luadata.serialize(document, *, target=None) -> bytes`: the bytes of the
  file for `document`, a `LuaDocument` from `parse` or `read`, possibly
  edited, or one the caller built. It writes nothing, anywhere (L1; the
  write is `guard`'s, L2).
- `target` (`str`, `Path` or `None`) is the path the bytes are meant for; it
  need not exist. It is only a place to look from. A layout property the
  document does not show is detected, at run time, in the other
  SavedVariables files (`*.lua` in a `SavedVariables` folder, account or
  character) under the same flavor folder as `target`, the folder holding
  the `WTF` folder above it; with none to read, or no `target`, it is
  §4.2's. Nothing about a flavor is a constant (L6): the graders' flavor
  folders have made-up names.
- Edits use the public immutable model (`_replace`, the NamedTuple
  constructors, `KeyStyle`). A trivia slot (`lead` of any value, entry or
  assignment; `eq_lead`, `key_close_lead`, `sep_lead`, `close_lead`; the
  document's `tail`) or an entry's `sep` that holds `None` is left to the
  serializer, which fills it from the detected style; one that holds bytes
  is written as given. So a node from the parse, untouched, is its original
  bytes. A new entry has `comment=None` and `duplicate=False`; whether a
  `-- [n]` follows it is the style's call.
- To append after the last entry of a parsed table the caller also sets the
  table's `close_lead` to `None`: those bytes hold the old last entry's line
  comment and the closing brace's indentation, and they now belong after the
  new entry. The old last entry's comment (`Entry.comment`) is kept.
- Edits are graded by bytes, never by object identity: the parser shares
  identical immutable nodes (`luadata` module docstring), so the graders
  edit one of two equal subtrees and place one new node object twice.

The style (§6.4, owner decision 2026-09-22), as graded: the indentation unit
or none, the `-- [n]` comments on positional entries or none, the entry
separator, the line ending, and the forms the client writes around them (a
leading empty line, ` = ` between key and value, a separator after every
entry including the last, none after a top-level value, one assignment per
line). Properties a document can fail to show: indentation (no table, or no
entry on its own line), array comments (no positional entry), the form of a
`[number]` key, an empty table's form. §4.2's line ending is CRLF: its
example writes line breaks as ⏎, and the 2026-09-22 line-endings amendment
records CRLF on every SavedVariables file and says a writer creating a new
file chooses by file kind. §4.2 shows no empty table, so no grader asks the
§4.2 fallback for one.

Not gradable on real data yet (the corpus has no such file): a tab-indented
or `-- [n]`-commented capture, a `[number]` key, `;` separators, LF
SavedVariables. The constructed file stands in for each until one does.
"""

from __future__ import annotations

import locale
import os
import pickle
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from _luadata_edits import (
    ACCOUNT_SV,
    CHARACTER_SV,
    DBM,
    RARESCANNER,
    SYNDICATOR,
    append,
    assignment,
    boolean,
    fixture,
    insert_after_line,
    install,
    keyed,
    number,
    numbered,
    positional,
    reference_document,
    reference_text,
    replace_nth_after,
    replace_once,
    set_leaf,
    string,
    table,
    tree_state,
)
from _luadata_oracle import indexed, load, rebuild

pytestmark = pytest.mark.parser

SAVEDVARIABLES = indexed("savedvariables")
CRLF = b"\r\n"

# A constructed sibling in §4.2's reference layout (tab indentation, `-- [n]`
# comments), LF: a stand-in until a real capture shows that style (L8).
CONSTRUCTED_TAB_LF_SIBLING = (
    b"\n"
    b"LabSiblingDB = {\n"
    b'\t["opts"] = {\n'
    b'\t\t["size"] = 3,\n'
    b"\t},\n"
    b'\t["seen"] = {\n'
    b'\t\t"x", -- [1]\n'
    b'\t\t"y", -- [2]\n'
    b"\t},\n"
    b"}\n"
)


@pytest.fixture
def luadata() -> Any:
    return load()


def _forever_install(root: Path) -> Path:
    """One flavor, `_lab_one_`, holding copies of the real Syndicator and DBM
    files in the account SavedVariables folder."""
    files = {
        f"{ACCOUNT_SV}/Syndicator.lua": fixture(SYNDICATOR),
        f"{ACCOUNT_SV}/DBM-StatusBarTimers.lua": fixture(DBM),
    }
    return install(root, {"_lab_one_": files})


# ── unmodified: byte for byte (L4) ──────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize("name", SAVEDVARIABLES)
def test_serialize_parse_is_the_file_byte_for_byte(luadata: Any, name: str) -> None:
    """L4 and §6.4: `serialize(parse(x)) == x` for every indexed capture."""
    data = fixture(name)
    out = luadata.serialize(luadata.parse(data))
    assert isinstance(out, bytes)
    assert out == data


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize("name", SAVEDVARIABLES)
def test_serialize_read_copy_is_the_file_byte_for_byte(
    luadata: Any, name: str, tmp_path: Path
) -> None:
    """The same through `read()`, on a copy (never the committed tree)."""
    copy = tmp_path / Path(name).name
    copy.write_bytes(fixture(name))
    assert luadata.serialize(luadata.read(copy)) == fixture(name)


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize("name", SAVEDVARIABLES)
def test_unmodified_file_ignores_constructed_sibling_style(
    luadata: Any, name: str, tmp_path: Path
) -> None:
    """Siblings only fill what the document lacks: an unmodified document is
    its own bytes even when every sibling is tab-indented, commented and LF
    (constructed sibling)."""
    root = install(
        tmp_path / "install",
        {"_lab_one_": {f"{ACCOUNT_SV}/Constructed.lua": CONSTRUCTED_TAB_LF_SIBLING}},
    )
    target = root / "_lab_one_" / ACCOUNT_SV / Path(name).name
    target.write_bytes(fixture(name))
    assert luadata.serialize(luadata.read(target), target=target) == fixture(name)


# ── a leaf edit changes that leaf only ──────────────────────────────────────

CONTAINER = b'["containerInfo"] = {'
LEAF_EDITS = [
    pytest.param(
        SYNDICATOR,
        "SYNDICATOR_CONFIG",
        ("tooltips_character_limit",),
        ("number", "9"),
        lambda s: replace_once(
            s, b'["tooltips_character_limit"] = 4,', b'["tooltips_character_limit"] = 9,'
        ),
        id="syndicator-config-number",
    ),
    pytest.param(
        SYNDICATOR,
        "SYNDICATOR_DATA",
        ("Characters", "Labchard Labrealme", "containerInfo", "bags", 1, "itemCount"),
        ("number", "2"),
        lambda s: replace_nth_after(s, CONTAINER, b'["itemCount"] = 1,', b'["itemCount"] = 2,', 1),
        id="syndicator-first-of-two-equal-bags",
    ),
    pytest.param(
        SYNDICATOR,
        "SYNDICATOR_DATA",
        ("Characters", "Labchard Labrealme", "containerInfo", "bags", 3, "itemCount"),
        ("number", "2"),
        lambda s: replace_nth_after(s, CONTAINER, b'["itemCount"] = 1,', b'["itemCount"] = 2,', 2),
        id="syndicator-second-of-two-equal-bags",
    ),
    pytest.param(
        SYNDICATOR,
        "SYNDICATOR_SUMMARIES",
        ("Warband", "Pending", 1),
        ("bool", True),
        lambda s: replace_once(s, b'["Pending"] = {\r\nfalse,', b'["Pending"] = {\r\ntrue,'),
        id="syndicator-positional-false",
    ),
    pytest.param(
        DBM,
        "DBT_AllPersistentOptions",
        ("Default", "DBM", "TimerY"),
        ("number", "-261"),
        lambda s: replace_once(s, b'\r\n["TimerY"] = -260,', b'\r\n["TimerY"] = -261,'),
        id="dbm-negative-integer",
    ),
    pytest.param(
        DBM,
        "DBT_AllPersistentOptions",
        ("Default", "DBM", "Font"),
        ("string", "lab_font"),
        lambda s: replace_once(s, b'\r\n["Font"] = "standardFont",', b'\r\n["Font"] = "lab_font",'),
        id="dbm-string",
    ),
]


def _scalar(luadata: Any, kind: str, value: Any) -> Any:
    return {"number": number, "string": string, "bool": boolean}[kind](luadata, value)


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize("keep_lead", [True, False], ids=["lead-kept", "lead-none"])
@pytest.mark.parametrize(("name", "variable", "steps", "new", "expected"), LEAF_EDITS)
def test_leaf_edit_changes_only_that_leaf(
    luadata: Any,
    name: str,
    variable: str,
    steps: tuple[str | int, ...],
    new: tuple[str, Any],
    expected: Any,
    keep_lead: bool,
) -> None:
    """§6.4: untouched subtrees serialize to their original bytes, so the
    output is the file with that one value's text changed. Two of the cases
    edit one of two equal bag tables (the parser may share their nodes); the
    other stays as it was. A value whose `lead` is `None` gets the document's
    own ` = ` spacing, so both ways give the same bytes."""
    source = fixture(name)
    doc = set_leaf(
        luadata, luadata.parse(source), variable, steps, _scalar(luadata, *new), keep_lead=keep_lead
    )
    out = luadata.serialize(doc)
    assert out == expected(source)
    if keep_lead:
        assert out == rebuild(luadata, doc), "a fully specified document is its rebuild"


# ── new entries follow the document's own style (owner decision 2026-09-22) ──

PERSON = ("Characters", "Labchard Labrealme")
NEW_ENTRIES = [
    pytest.param(
        SYNDICATOR,
        "SYNDICATOR_CONFIG",
        (),
        lambda lua: [keyed(lua, "lab_new", number(lua, "7"))],
        lambda s: insert_after_line(
            s, b'["no_auction_value_source"] = false,', b'["lab_new"] = 7,\r\n'
        ),
        id="syndicator-string-key-depth-1",
    ),
    pytest.param(
        SYNDICATOR,
        "SYNDICATOR_DATA",
        (*PERSON, "details"),
        lambda lua: [keyed(lua, "lab_t", table(lua, keyed(lua, "a", number(lua, "1"))))],
        lambda s: insert_after_line(
            s, b'["realm"] = "",\r\n},\r\n["bags"]', b'["lab_t"] = {\r\n["a"] = 1,\r\n},\r\n'
        ),
        id="syndicator-string-key-new-table-depth-4",
    ),
    pytest.param(
        SYNDICATOR,
        "SYNDICATOR_DATA",
        (*PERSON, "details"),
        lambda lua: [keyed(lua, "lab_empty", table(lua))],
        lambda s: insert_after_line(
            s, b'["realm"] = "",\r\n},\r\n["bags"]', b'["lab_empty"] = {\r\n},\r\n'
        ),
        id="syndicator-empty-table-two-lines",
    ),
    pytest.param(
        SYNDICATOR,
        "SYNDICATOR_SUMMARIES",
        ("Warband", "Pending"),
        lambda lua: [positional(lua, boolean(lua, True))],
        lambda s: replace_once(
            s, b'["Pending"] = {\r\nfalse,\r\n', b'["Pending"] = {\r\nfalse,\r\ntrue,\r\n'
        ),
        id="syndicator-positional-no-comment",
    ),
    pytest.param(
        SYNDICATOR,
        "SYNDICATOR_DATA",
        (*PERSON, "containerInfo", "bags"),
        lambda lua: [positional(lua, table(lua, keyed(lua, "itemCount", number(lua, "3"))))],
        lambda s: replace_once(
            s,
            b'},\r\n},\r\n},\r\n["bankTabs"] = {',
            b'},\r\n{\r\n["itemCount"] = 3,\r\n},\r\n},\r\n},\r\n["bankTabs"] = {',
        ),
        id="syndicator-positional-table",
    ),
    pytest.param(
        DBM,
        "DBT_AllPersistentOptions",
        ("Default", "DBM"),
        lambda lua: [numbered(lua, "42", boolean(lua, True))],
        lambda s: insert_after_line(s, b'["VarianceTexture"]', b"[42] = true,\r\n"),
        id="dbm-number-key",
    ),
    pytest.param(
        DBM,
        "DBT_AllPersistentOptions",
        ("Default",),
        lambda lua: [
            keyed(lua, "lab_a", string(lua, "x")),
            keyed(lua, "lab_b", number(lua, "0.5")),
        ],
        lambda s: replace_once(
            s, b"},\r\n},\r\n}\r\n", b'},\r\n["lab_a"] = "x",\r\n["lab_b"] = 0.5,\r\n},\r\n}\r\n'
        ),
        id="dbm-two-string-keys",
    ),
]


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize(("name", "variable", "steps", "new", "expected"), NEW_ENTRIES)
def test_new_entries_follow_the_unindented_forever_style(
    luadata: Any,
    name: str,
    variable: str,
    steps: tuple[str | int, ...],
    new: Any,
    expected: Any,
) -> None:
    """An unindented Forever file stays unindented: a new entry is one line
    per entry at column 0, CRLF, `,` after it, no `-- [n]` on a positional
    entry (the file shows positional entries without one), an empty table on
    two lines (the file shows that form). DBM has no `[number]` key, so that
    key's form falls back to §4.2's `[42] = `, the rest from the file."""
    source = fixture(name)
    doc = append(luadata, luadata.parse(source), variable, steps, *new(luadata))
    assert luadata.serialize(doc) == expected(source)


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_new_assignment_follows_the_forever_style(luadata: Any) -> None:
    """A new top-level assignment goes on its own line after the last one;
    the file's tail stays after it."""
    source = fixture(DBM)
    doc = luadata.parse(source)
    doc = doc._replace(
        assignments=(*doc.assignments, assignment(luadata, "LabNewVar", string(luadata, "text")))
    )
    assert source.endswith(b"}\r\n")
    assert luadata.serialize(doc) == source + b'LabNewVar = "text"\r\n'


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_one_new_node_object_placed_twice_is_laid_out_per_place(luadata: Any) -> None:
    """Placement, not identity: the same new entry object appended to a
    depth-1 and a depth-4 table of a Forever file (no indentation, so the
    bytes are the same line twice) and the same new table object as two
    values."""
    source = fixture(SYNDICATOR)
    shared = keyed(luadata, "lab_same", table(luadata, keyed(luadata, "a", number(luadata, "1"))))
    doc = append(luadata, luadata.parse(source), "SYNDICATOR_CONFIG", (), shared)
    doc = append(luadata, doc, "SYNDICATOR_DATA", (*PERSON, "details"), shared)
    new = b'["lab_same"] = {\r\n["a"] = 1,\r\n},\r\n'
    expected = insert_after_line(source, b'["no_auction_value_source"] = false,', new)
    expected = insert_after_line(expected, b'["realm"] = "",\r\n},\r\n["bags"]', new)
    assert luadata.serialize(doc) == expected


# ── a property the document does not show falls back as for a new file ─────

RARESCANNER_NEW_TABLE_EXPECTED = {
    "no-target": (
        b"\r\nRareScannerDB = {\r\n"
        b'\t["lab_key"] = "text",\r\n'
        b'\t["lab_list"] = {\r\n'
        b'\t\t"first", -- [1]\r\n'
        b'\t\t"second", -- [2]\r\n'
        b"\t},\r\n"
        b"\t[42] = true,\r\n"
        b"}\r\n"
    ),
    "forever-siblings": (
        b"\r\nRareScannerDB = {\r\n"
        b'["lab_key"] = "text",\r\n'
        b'["lab_list"] = {\r\n'
        b'"first",\r\n'
        b'"second",\r\n'
        b"},\r\n"
        b"[42] = true,\r\n"
        b"}\r\n"
    ),
    "constructed-tab-lf-sibling": (
        b"\r\nRareScannerDB = {\r\n"
        b'\t["lab_key"] = "text",\r\n'
        b'\t["lab_list"] = {\r\n'
        b'\t\t"first", -- [1]\r\n'
        b'\t\t"second", -- [2]\r\n'
        b"\t},\r\n"
        b"\t[42] = true,\r\n"
        b"}\r\n"
    ),
}


def _siblings(case: str) -> dict[str, bytes] | None:
    if case == "forever-siblings":
        return {
            f"{ACCOUNT_SV}/Syndicator.lua": fixture(SYNDICATOR),
            f"{ACCOUNT_SV}/DBM-StatusBarTimers.lua": fixture(DBM),
        }
    if case == "constructed-tab-lf-sibling":
        return {f"{ACCOUNT_SV}/Constructed.lua": CONSTRUCTED_TAB_LF_SIBLING}
    return None


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize("case", list(RARESCANNER_NEW_TABLE_EXPECTED))
def test_table_in_a_file_without_one_takes_absent_properties_from_siblings_else_reference(
    luadata: Any, case: str, tmp_path: Path
) -> None:
    """RareScanner.lua is `RareScannerDB = nil`: it shows CRLF, the leading
    empty line and ` = `, but no indentation and no array comments. Setting
    the variable to a table takes those two from the sibling files under the
    same flavor folder (unindented, no comments for the Forever files;
    tab-indented and commented for the constructed LF file), else §4.2's; the
    line ending stays the document's own CRLF in every case."""
    siblings = _siblings(case)
    if siblings is None:
        doc = luadata.parse(fixture(RARESCANNER))
        target = None
    else:
        root = install(tmp_path / "install", {"_lab_one_": siblings})
        target = root / "_lab_one_" / ACCOUNT_SV / "RareScanner.lua"
        target.write_bytes(fixture(RARESCANNER))
        doc = luadata.read(target)
    new = table(
        luadata,
        keyed(luadata, "lab_key", string(luadata, "text")),
        keyed(
            luadata,
            "lab_list",
            table(
                luadata,
                positional(luadata, string(luadata, "first")),
                positional(luadata, string(luadata, "second")),
            ),
        ),
        numbered(luadata, "42", boolean(luadata, True)),
    )
    [only] = doc.assignments
    doc = doc._replace(assignments=(only._replace(value=new),))
    assert luadata.serialize(doc, target=target) == RARESCANNER_NEW_TABLE_EXPECTED[case]


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_empty_table_form_comes_from_forever_siblings(luadata: Any, tmp_path: Path) -> None:
    """RareScanner.lua shows no empty table; Syndicator.lua, a sibling under
    the same flavor folder, writes one as `{` then `}` on two lines."""
    root = _forever_install(tmp_path / "install")
    target = root / "_lab_one_" / ACCOUNT_SV / "RareScanner.lua"
    target.write_bytes(fixture(RARESCANNER))
    doc = luadata.read(target)
    [only] = doc.assignments
    doc = doc._replace(
        assignments=(only._replace(value=table(luadata, keyed(luadata, "e", table(luadata)))),)
    )
    assert (
        luadata.serialize(doc, target=target)
        == b'\r\nRareScannerDB = {\r\n["e"] = {\r\n},\r\n}\r\n'
    )


DBM_POSITIONAL_EXPECTED = {
    "no-target": b'"lab", -- [1]\r\n',
    "forever-siblings": b'"lab",\r\n',
    "constructed-tab-lf-sibling": b'"lab", -- [1]\r\n',
}


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize("case", list(DBM_POSITIONAL_EXPECTED))
def test_positional_entry_in_a_file_without_one_takes_comments_from_siblings_else_reference(
    luadata: Any, case: str, tmp_path: Path
) -> None:
    """DBM-StatusBarTimers.lua has no positional entry, so it does not show
    whether one carries `-- [n]`. The sibling Syndicator.lua shows 82 without
    one; the constructed sibling shows them with one; §4.2 writes one. The
    indentation (none) and CRLF stay the document's own."""
    siblings = {
        "no-target": None,
        "forever-siblings": {f"{ACCOUNT_SV}/Syndicator.lua": fixture(SYNDICATOR)},
        "constructed-tab-lf-sibling": {f"{ACCOUNT_SV}/Constructed.lua": CONSTRUCTED_TAB_LF_SIBLING},
    }[case]
    source = fixture(DBM)
    if siblings is None:
        doc, target = luadata.parse(source), None
    else:
        root = install(tmp_path / "install", {"_lab_one_": siblings})
        target = root / "_lab_one_" / ACCOUNT_SV / "DBM-StatusBarTimers.lua"
        target.write_bytes(source)
        doc = luadata.read(target)
    doc = append(
        luadata,
        doc,
        "DBT_AllPersistentOptions",
        ("Default", "DBM"),
        positional(luadata, string(luadata, "lab")),
    )
    expected = insert_after_line(source, b'["VarianceTexture"]', DBM_POSITIONAL_EXPECTED[case])
    assert luadata.serialize(doc, target=target) == expected


# ── a new document: the sibling files' style, else §4.2 ─────────────────────


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize("where", ["account", "character"])
def test_new_document_follows_the_forever_sibling_files(
    luadata: Any, where: str, tmp_path: Path
) -> None:
    """A new file under a flavor folder whose SavedVariables (account-wide,
    or only a character's) are the real Forever captures: §4.2's example
    document comes out unindented, uncommented, CRLF, with the leading empty
    line."""
    folder = ACCOUNT_SV if where == "account" else CHARACTER_SV
    root = install(
        tmp_path / "install",
        {
            "_lab_one_": {
                f"{folder}/Syndicator.lua": fixture(SYNDICATOR),
                f"{folder}/DBM-StatusBarTimers.lua": fixture(DBM),
            }
        },
    )
    target = root / "_lab_one_" / ACCOUNT_SV / "LabNewAddon.lua"
    out = luadata.serialize(reference_document(luadata), target=target)
    assert out == reference_text(indent=b"", comments=False, eol=CRLF)


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_new_document_takes_what_siblings_do_not_show_from_the_reference(
    luadata: Any, tmp_path: Path
) -> None:
    """Siblings that show no positional entry (DBM-StatusBarTimers.lua,
    RareScanner.lua) leave the array comments to §4.2; indentation (none)
    and CRLF come from the siblings."""
    root = install(
        tmp_path / "install",
        {
            "_lab_one_": {
                f"{ACCOUNT_SV}/DBM-StatusBarTimers.lua": fixture(DBM),
                f"{ACCOUNT_SV}/RareScanner.lua": fixture(RARESCANNER),
            }
        },
    )
    target = root / "_lab_one_" / ACCOUNT_SV / "LabNewAddon.lua"
    out = luadata.serialize(reference_document(luadata), target=target)
    assert out == reference_text(indent=b"", comments=True, eol=CRLF)


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize(
    "target_rel", [None, "no-savedvariables"], ids=["no-target", "other-flavor-only"]
)
def test_new_document_with_no_sibling_to_read_is_the_reference_layout(
    luadata: Any, target_rel: str | None, tmp_path: Path
) -> None:
    """With no target, or a target in a flavor folder with no SavedVariables
    of its own, §4.2's layout: tab indentation, `-- [n]`, CRLF. The Forever
    files in another flavor folder of the same install are not siblings."""
    target = None
    if target_rel is not None:
        root = install(
            tmp_path / "install",
            {
                "_lab_empty_": {},
                "_lab_other_": {
                    f"{ACCOUNT_SV}/Syndicator.lua": fixture(SYNDICATOR),
                    f"{ACCOUNT_SV}/DBM-StatusBarTimers.lua": fixture(DBM),
                },
            },
        )
        target = root / "_lab_empty_" / ACCOUNT_SV / "LabNewAddon.lua"
    out = luadata.serialize(reference_document(luadata), target=target)
    assert out == reference_text(indent=b"\t", comments=True, eol=CRLF)


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_serialize_writes_nothing(luadata: Any, tmp_path: Path) -> None:
    """L1/L2: serializing reads siblings and returns bytes. It creates,
    changes and removes nothing, and the target stays absent."""
    root = _forever_install(tmp_path / "install")
    target = root / "_lab_one_" / ACCOUNT_SV / "LabNewAddon.lua"
    before = tree_state(tmp_path)
    luadata.serialize(reference_document(luadata), target=target)
    edited = luadata.read(root / "_lab_one_" / ACCOUNT_SV / "Syndicator.lua")
    luadata.serialize(
        append(luadata, edited, "SYNDICATOR_CONFIG", (), keyed(luadata, "x", number(luadata, "1"))),
        target=target,
    )
    assert tree_state(tmp_path) == before
    assert not target.exists()


# ── determinism: same document, same bytes (§6.4) ───────────────────────────

NON_C_LOCALES = (
    "de_DE.UTF-8",
    "de_DE.utf8",
    "fr_FR.UTF-8",
    "fr_FR.utf8",
    "tr_TR.UTF-8",
    "tr_TR.utf8",
    "German_Germany.1252",
    "de-DE",
    "en_US.UTF-8",
    "en_US.utf8",
    "English_United States.1252",
)


def _non_c_locale() -> str:
    """The first locale of `NON_C_LOCALES` this machine has. None at all
    fails the grader: a determinism claim with no non-C locale is untested."""
    saved = locale.setlocale(locale.LC_ALL)
    try:
        for name in NON_C_LOCALES:
            try:
                locale.setlocale(locale.LC_ALL, name)
            except locale.Error:
                continue
            return name
    finally:
        locale.setlocale(locale.LC_ALL, saved)
    pytest.fail(f"no non-C locale available among {NON_C_LOCALES}")


def _determinism_cases(luadata: Any, root: Path) -> list[tuple[Any, Path | None]]:
    """An edited real file (untouched subtrees plus new entries), and §4.2's
    example as a new file with and without Forever siblings."""
    syndicator = luadata.parse(fixture(SYNDICATOR))
    syndicator = set_leaf(
        luadata,
        syndicator,
        "SYNDICATOR_CONFIG",
        ("tooltips_character_limit",),
        number(luadata, "9"),
        keep_lead=False,
    )
    syndicator = append(
        luadata,
        syndicator,
        "SYNDICATOR_SUMMARIES",
        ("Warband", "Pending"),
        positional(luadata, boolean(luadata, True)),
    )
    syndicator = append(
        luadata,
        syndicator,
        "SYNDICATOR_DATA",
        (*PERSON, "details"),
        keyed(luadata, "t", table(luadata)),
    )
    target = root / "_lab_one_" / ACCOUNT_SV / "LabNewAddon.lua"
    return [
        (syndicator, None),
        (reference_document(luadata), target),
        (reference_document(luadata), None),
    ]


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_hundred_runs_give_identical_bytes_under_a_non_c_locale(
    luadata: Any, tmp_path: Path
) -> None:
    """100 serializations of each case under the C locale's opposite (a
    decimal comma where the machine has one), each identical to the first
    under the process's own locale."""
    root = _forever_install(tmp_path / "install")
    cases = _determinism_cases(luadata, root)
    first = [luadata.serialize(doc, target=target) for doc, target in cases]
    saved = locale.setlocale(locale.LC_ALL)
    try:
        locale.setlocale(locale.LC_ALL, _non_c_locale())
        for _ in range(100):
            assert [luadata.serialize(doc, target=target) for doc, target in cases] == first
    finally:
        locale.setlocale(locale.LC_ALL, saved)


_CHILD = """
import locale, pickle, sys
locale.setlocale(locale.LC_ALL, "")
from wowlab_core import luadata
cases = pickle.loads(open(sys.argv[1], "rb").read())
out = [luadata.serialize(doc, target=target) for doc, target in cases]
open(sys.argv[2], "wb").write(pickle.dumps(out))
"""


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_fresh_interpreters_with_other_hash_seeds_and_locales_give_the_same_bytes(
    luadata: Any, tmp_path: Path
) -> None:
    """Same documents, same bytes, in fresh interpreters under a non-C
    locale from the environment and different hash seeds (so no set or dict
    order of `str` keys can leak into the output)."""
    root = _forever_install(tmp_path / "install")
    cases = _determinism_cases(luadata, root)
    expected = [luadata.serialize(doc, target=target) for doc, target in cases]
    name = _non_c_locale()
    (tmp_path / "cases.pickle").write_bytes(pickle.dumps(cases))
    for seed in ("0", "1", "4242"):
        env = {**os.environ, "LC_ALL": name, "LANG": name, "PYTHONHASHSEED": seed}
        out_path = tmp_path / f"out-{seed}.pickle"
        subprocess.run(
            [sys.executable, "-c", _CHILD, str(tmp_path / "cases.pickle"), str(out_path)],
            env=env,
            check=True,
            timeout=120,
        )
        assert pickle.loads(out_path.read_bytes()) == expected, (
            f"PYTHONHASHSEED={seed}, LC_ALL={name}"
        )
