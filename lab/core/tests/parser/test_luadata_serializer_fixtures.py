"""Graders for the `wowlab_core.luadata` serializer against the real
SavedVariables captures (M10-12T; L1, L3, L4, L6, L8).

Written before the serializer exists, from `docs/LAB_PLAN.md` §6.4
(serializer half, the owner decision of 2026-09-22 on the document's own
style, and the amendment of 2026-09-27, items 1 to 7) and
`docs/LAB_FORMATS.md` §4.2 with its 2026-09-22 and 2026-09-27 amendments
(the Forever part-2 capture: no indentation, no `-- [n]` comments, CRLF
everywhere, a leading empty line, `,` after every entry, two-line empty
tables). Every grader carries one marker line that M10-12 deletes; nothing
else here is the implementer's to change. Constructed inputs (§4.2's
remembered retail layout, other separators and indentation units, hostile
documents for the data-only rule) are in
`test_luadata_serializer_constructed.py`; a grader here that mixes in a
constructed file says `constructed` in its test id.

The seam (§6.4 amendment 2026-09-27, item 1), added to the one
`test_luadata_fixtures.py` pins for the parser
----------------------------------------------------------------------
- `luadata.serialize(document, *, target=None, lab_written=frozenset())
  -> bytes`: the bytes of the file for `document`, a `LuaDocument` from
  `parse` or `read`, possibly edited, or one the caller built. It writes
  nothing, anywhere (L1; the write is `guard`'s, L2).
- `target` (`str`, `Path` or `None`) is the path the bytes are meant for; it
  need not exist, and it is only a place to look from. `lab_written` holds
  the paths of files the guard journal records as last written by the Lab;
  the graders pass them as the same absolute `Path`s they built the tree
  with.
- Edits use the public immutable model (`_replace`, the NamedTuple
  constructors, `KeyStyle`). A trivia slot (`lead` of any value, entry or
  assignment; `eq_lead`, `key_close_lead`, `sep_lead`, `close_lead`; the
  document's `tail`) or an entry's `sep` that holds `None` is filled from the
  detected style; one that holds bytes is written as given, so a node from
  the parse, untouched, is its original bytes. A new entry has
  `comment=None` and `duplicate=False`.
- To append after the last entry of a parsed table the caller also sets the
  table's `close_lead` to `None`. The old last entry's `Entry.comment` is
  then written after its separator with one space; own-line comments that
  stood before the old `}` are dropped by that edit.
- Edits are graded by bytes, never by object identity: the parser shares
  identical immutable nodes, so the graders edit one of two equal subtrees
  and place one new node object twice.
- Data only (item 2): `serialize` raises `LuaDataError` when a given trivia
  slot holds anything but whitespace and `--` line comments, or a `raw`,
  key or name is not a literal §4.1 accepts. Its output always parses.

Where the style comes from (items 3 to 6), as graded:
- In order: the document, then its siblings, then the fallback. Indentation
  and `-- [n]` array comments are one pairing (item 5): whoever shows either
  decides both, so a document or sibling that shows entries at column 0
  also decides "no comments", and one that shows only `-- [n]` also decides
  tab indentation. The line ending, separator, leading empty line,
  `[number]` key form and empty-table form are decided one by one.
- Siblings (item 3): `WTF/Account/*/SavedVariables.lua`,
  `WTF/Account/*/SavedVariables/*.lua` and
  `WTF/Account/*/*/*/SavedVariables/*.lua` under the flavor folder holding
  `target`; never the target, a `*.lua.bak`, another flavor folder, a
  renamed copy of `WTF` or `Interface/`. A sibling that cannot be read or
  parsed is skipped, never raised. The graders' flavor folders have
  made-up names (L6).
- Disagreement (item 4): the most recently modified sibling that shows the
  property decides, excluding `lab_written`; a tie breaks on the byte-wise
  path relative to the flavor folder, the lowest path first.
- Fallback (item 6): the observed Forever layout: no indentation, no
  `-- [n]`, CRLF, a leading empty line, `,` after every entry, an empty
  table on two lines. The `[number]` key form, which no capture and no
  item states, is `[42] = ` as in §4.1 and §4.2.

Not gradable on real data yet (the corpus has no such file): a tab-indented
or `-- [n]`-commented capture, a `[number]` key, `;` separators, LF
SavedVariables, an account `SavedVariables.lua`, siblings that disagree.
The constructed files stand in for each until one does.
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
    ACCOUNT,
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
    set_mtime,
    string,
    table,
    tree_state,
)
from _luadata_oracle import indexed, load, rebuild

pytestmark = pytest.mark.parser

SAVEDVARIABLES = indexed("savedvariables")
CRLF = b"\r\n"
FLAT_CRLF = reference_text(indent=b"", comments=False, eol=CRLF)
TABS_LF = reference_text(indent=b"\t", comments=True, eol=b"\n")
OLD, NEW, NEWER = 1_700_000_000, 1_700_000_100, 1_700_000_200

# A constructed sibling in §4.2's remembered retail layout (tab indentation,
# `-- [n]` comments), LF: a stand-in until a real capture shows that style
# (L8).
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
    key's form comes from the fallback, `[42] = ` (§4.1, §4.2), the rest
    from the file."""
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

RARESCANNER_FLAT = (
    b"\r\nRareScannerDB = {\r\n"
    b'["lab_key"] = "text",\r\n'
    b'["lab_list"] = {\r\n'
    b'"first",\r\n'
    b'"second",\r\n'
    b"},\r\n"
    b"[42] = true,\r\n"
    b"}\r\n"
)
RARESCANNER_NEW_TABLE_EXPECTED = {
    "no-target": RARESCANNER_FLAT,
    "forever-siblings": RARESCANNER_FLAT,
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
def test_table_in_a_file_without_one_takes_the_pairing_from_siblings_else_the_fallback(
    luadata: Any, case: str, tmp_path: Path
) -> None:
    """RareScanner.lua is `RareScannerDB = nil`: it shows CRLF, the leading
    empty line and ` = `, but neither indentation nor array comments. Setting
    the variable to a table takes that pairing from the siblings under the
    same flavor folder (flat for the Forever files; tab-indented and
    commented for the constructed LF file), else the Forever fallback; the
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


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize("case", ["no-target", "forever-siblings", "constructed-tab-lf-sibling"])
def test_a_document_showing_no_indentation_writes_no_array_comment(
    luadata: Any, case: str, tmp_path: Path
) -> None:
    """Pairing (§6.4 amendment 2026-09-27, item 5): DBM-StatusBarTimers.lua
    has no positional entry, but its entries at column 0 show "no
    indentation", which decides "no `-- [n]`" too. So a new positional entry
    gets no comment whatever the siblings show, even a tab-indented,
    commented one (constructed)."""
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
    expected = insert_after_line(source, b'["VarianceTexture"]', b'"lab",\r\n')
    assert luadata.serialize(doc, target=target) == expected


# ── a new document: the sibling files' style, else the fallback ─────────────


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize("where", ["account", "character"])
def test_new_document_follows_the_forever_sibling_files(
    luadata: Any, where: str, tmp_path: Path
) -> None:
    """A new file under a flavor folder whose SavedVariables (account-wide,
    or only a character's, in the Forever `<digits>/<First>-<Second>` shape)
    are the real Forever captures: §4.2's example document comes out
    unindented, uncommented, CRLF, with the leading empty line."""
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
    assert luadata.serialize(reference_document(luadata), target=target) == FLAT_CRLF


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_a_sibling_showing_no_indentation_decides_no_array_comments(
    luadata: Any, tmp_path: Path
) -> None:
    """Pairing: the only siblings (DBM-StatusBarTimers.lua, RareScanner.lua)
    show no positional entry, but DBM's entries at column 0 decide the whole
    pairing: no indentation and no `-- [n]`. No mixed layout."""
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
    assert luadata.serialize(reference_document(luadata), target=target) == FLAT_CRLF


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_constructed_other_flavor_is_no_sibling_so_a_new_document_is_the_fallback(
    luadata: Any, tmp_path: Path
) -> None:
    """The target's flavor folder has no SavedVariables of its own; the
    newest SavedVariables of the install, tab-indented and LF, sit in
    another flavor folder (constructed) and are not siblings. So the
    fallback: flat, CRLF. (The case with no target at all is graded in the
    constructed file.)"""
    root = install(
        tmp_path / "install",
        {
            "_lab_empty_": {},
            "_lab_other_": {
                f"{ACCOUNT_SV}/Syndicator.lua": fixture(SYNDICATOR),
                f"{ACCOUNT_SV}/Constructed.lua": CONSTRUCTED_TAB_LF_SIBLING,
            },
        },
    )
    set_mtime(root / "_lab_other_" / ACCOUNT_SV / "Syndicator.lua", OLD)
    set_mtime(root / "_lab_other_" / ACCOUNT_SV / "Constructed.lua", NEWER)
    target = root / "_lab_empty_" / ACCOUNT_SV / "LabNewAddon.lua"
    assert luadata.serialize(reference_document(luadata), target=target) == FLAT_CRLF


# ── siblings: which files, which one decides (§6.4 amendment items 3, 4) ────

SYN, DBM_, TAB, NIL_LF = "syndicator", "dbm", "constructed-tab-lf", "constructed-nil-lf"
_CONTENT = {
    SYN: lambda: fixture(SYNDICATOR),
    DBM_: lambda: fixture(DBM),
    TAB: lambda: CONSTRUCTED_TAB_LF_SIBLING,
    NIL_LF: lambda: b"\nLabX = nil\n",
}


def _tree(root: Path, flavors: dict[str, dict[str, tuple[str, int]]]) -> Path:
    """An install whose files carry fixed modification times:
    `{flavor: {path relative to the flavor folder: (content, mtime)}}`."""
    install(
        root,
        {
            f: {rel: _CONTENT[kind]() for rel, (kind, _t) in files.items()}
            for f, files in flavors.items()
        },
    )
    for flavor, files in flavors.items():
        for rel, (_kind, seconds) in files.items():
            set_mtime(root / flavor / rel, seconds)
    return root


DISAGREEING = {
    "constructed-tab-newest": ({"Syndicator.lua": (SYN, OLD), "Tab.lua": (TAB, NEW)}, (), TABS_LF),
    "flat-newest-constructed-tab-older": (
        {"Syndicator.lua": (SYN, NEW), "Tab.lua": (TAB, OLD)},
        (),
        FLAT_CRLF,
    ),
    "constructed-tab-newest-but-lab-written": (
        {"Syndicator.lua": (SYN, OLD), "Tab.lua": (TAB, NEW)},
        ("Tab.lua",),
        FLAT_CRLF,
    ),
    "constructed-lab-written-compared-after-resolve": (
        {"Syndicator.lua": (SYN, OLD), "Tab.lua": (TAB, NEW)},
        ("unresolved/../Tab.lua",),
        FLAT_CRLF,
    ),
    "constructed-tie-lowest-byte-path-Zeta-before-alpha": (
        {"alpha.lua": (SYN, NEW), "Zeta.lua": (TAB, NEW)},
        (),
        TABS_LF,
    ),
    "constructed-tie-lowest-byte-path-Alpha-before-beta": (
        {"Alpha.lua": (SYN, NEW), "beta.lua": (TAB, NEW)},
        (),
        FLAT_CRLF,
    ),
    "constructed-newest-shows-only-the-line-ending": (
        {"Nil.lua": (NIL_LF, NEWER), "Syndicator.lua": (SYN, NEW), "Tab.lua": (TAB, OLD)},
        (),
        reference_text(indent=b"", comments=False, eol=b"\n"),
    ),
}


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize(
    ("files", "written", "expected"), list(DISAGREEING.values()), ids=list(DISAGREEING)
)
def test_disagreeing_siblings_newest_showing_the_property_decides(
    luadata: Any,
    files: dict[str, tuple[str, int]],
    written: tuple[str, ...],
    expected: bytes,
    tmp_path: Path,
) -> None:
    """Item 4 (owner): per property, the most recently modified sibling that
    shows it decides, leaving out the files in `lab_written` (compared
    after `Path.resolve()`, item 8, so an unresolved spelling of the same
    file counts); equal times
    break on the byte-wise path relative to the flavor folder, lowest first
    (`Z` 0x5A sorts before `a` 0x61, so neither case folding nor directory
    order can pass both tie cases). A newest sibling that shows only its
    line ending decides that alone; the pairing comes from the next one."""
    root = _tree(
        tmp_path / "install",
        {"_lab_one_": {f"{ACCOUNT_SV}/{name}": spec for name, spec in files.items()}},
    )
    folder = root / "_lab_one_" / ACCOUNT_SV
    out = luadata.serialize(
        reference_document(luadata),
        target=folder / "LabNewAddon.lua",
        lab_written=frozenset(folder / name for name in written),
    )
    assert out == expected


SIBLING_SETS = {
    "constructed-decoys-ignored": (
        {
            "_lab_one_": {
                f"{ACCOUNT_SV}/Syndicator.lua": (SYN, OLD),
                "WTF.old/Account/90000001#6/SavedVariables/Decoy.lua": (TAB, NEWER),
                "Interface/AddOns/LabDecoy/SavedVariables/Decoy.lua": (TAB, NEWER),
                f"{ACCOUNT_SV}/Syndicator.lua.bak": (TAB, NEWER),
                f"{ACCOUNT_SV}/LabNewAddon.lua": (TAB, NEWER),
            },
            "_lab_other_": {f"{ACCOUNT_SV}/Decoy.lua": (TAB, NEWER)},
        },
        FLAT_CRLF,
    ),
    "constructed-account-savedvariables-lua": (
        {
            "_lab_one_": {
                f"{ACCOUNT_SV}/Syndicator.lua": (SYN, OLD),
                f"{ACCOUNT}/SavedVariables.lua": (TAB, NEWER),
            }
        },
        TABS_LF,
    ),
    "constructed-retail-realm-character-shape": (
        {
            "_lab_one_": {
                f"{ACCOUNT_SV}/Syndicator.lua": (SYN, OLD),
                f"{ACCOUNT}/Labrealm/Labchar/SavedVariables/Addon.lua": (TAB, NEWER),
            }
        },
        TABS_LF,
    ),
}


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
@pytest.mark.parametrize(
    ("flavors", "expected"), list(SIBLING_SETS.values()), ids=list(SIBLING_SETS)
)
def test_sibling_set_is_decided_by_path(
    luadata: Any, flavors: dict[str, dict[str, tuple[str, int]]], expected: bytes, tmp_path: Path
) -> None:
    """Item 3: next to a real Forever file, tab-indented decoys that are
    newer (a renamed `WTF.old`, an addon's own folder under `Interface/`, a
    `.lua.bak`, the target itself, another flavor folder) change nothing; an
    account-level `SavedVariables.lua` and a retail-shaped
    `<Realm>/<Character>/SavedVariables/` file are siblings, and being
    newest they decide."""
    root = _tree(tmp_path / "install", flavors)
    target = root / "_lab_one_" / ACCOUNT_SV / "LabNewAddon.lua"
    assert luadata.serialize(reference_document(luadata), target=target) == expected


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_constructed_unreadable_unparsable_and_empty_siblings_are_skipped(
    luadata: Any, tmp_path: Path
) -> None:
    """Item 3: a sibling that is empty, cut off mid-table, over the depth
    bound, or not a file at all is skipped, even when it is the newest, and
    never makes `serialize` raise; Syndicator.lua decides."""
    root = install(
        tmp_path / "install",
        {
            "_lab_one_": {
                f"{ACCOUNT_SV}/Syndicator.lua": fixture(SYNDICATOR),
                f"{ACCOUNT_SV}/Empty.lua": b"",
                f"{ACCOUNT_SV}/Broken.lua": b"\r\nX = {\r\n",
                f"{ACCOUNT_SV}/Deep.lua": b"\r\nX = " + b"{" * 300 + b"}" * 300 + b"\r\n",
            }
        },
    )
    folder = root / "_lab_one_" / ACCOUNT_SV
    (folder / "Folder.lua").mkdir()
    set_mtime(folder / "Syndicator.lua", OLD)
    for name in ("Empty.lua", "Broken.lua", "Deep.lua", "Folder.lua"):
        set_mtime(folder / name, NEWER)
    target = folder / "LabNewAddon.lua"
    assert luadata.serialize(reference_document(luadata), target=target) == FLAT_CRLF


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

DECIMAL_COMMA_LOCALES = (
    "de_DE.UTF-8",
    "de_DE.utf8",
    "fr_FR.UTF-8",
    "fr_FR.utf8",
    "ru_RU.UTF-8",
    "ru_RU.utf8",
    "es_ES.UTF-8",
    "es_ES.utf8",
    "German_Germany.1252",
    "de-DE",
    "French_France.1252",
    "fr-FR",
)


def _decimal_comma_locale() -> str:
    """The first locale of `DECIMAL_COMMA_LOCALES` this machine has whose
    decimal point is not `.`. Where there is none, the grader is skipped with
    that reason: a determinism claim under a locale that formats numbers
    like C proves nothing."""
    saved = locale.setlocale(locale.LC_ALL)
    try:
        for name in DECIMAL_COMMA_LOCALES:
            try:
                locale.setlocale(locale.LC_ALL, name)
            except locale.Error:
                continue
            if locale.localeconv()["decimal_point"] != ".":
                return name
    finally:
        locale.setlocale(locale.LC_ALL, saved)
    pytest.skip(
        "no locale with a decimal point other than '.' on this machine "
        f"(tried {', '.join(DECIMAL_COMMA_LOCALES)}); on Linux, generate one with "
        "`sudo locale-gen de_DE.UTF-8`"
    )


def _determinism_cases(luadata: Any, root: Path) -> list[tuple[Any, Path | None, frozenset[Path]]]:
    """An edited real file (untouched subtrees plus new entries); §4.2's
    example as a new file with Forever siblings, with none, and in a
    disagreeing tree whose decision rests on a tie between equal times and on
    `lab_written` (so neither directory order nor set order can show)."""
    _tree(
        root,
        {
            "_lab_one_": {
                f"{ACCOUNT_SV}/Syndicator.lua": (SYN, OLD),
                f"{ACCOUNT_SV}/DBM-StatusBarTimers.lua": (DBM_, OLD),
            },
            "_lab_tie_": {
                f"{ACCOUNT_SV}/alpha.lua": (SYN, NEW),
                f"{ACCOUNT_SV}/Zeta.lua": (TAB, NEW),
                f"{ACCOUNT_SV}/Written.lua": (SYN, NEWER),
                f"{CHARACTER_SV}/Written.lua": (DBM_, NEWER),
            },
        },
    )
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
    tie = root / "_lab_tie_"
    written = frozenset({tie / ACCOUNT_SV / "Written.lua", tie / CHARACTER_SV / "Written.lua"})
    none: frozenset[Path] = frozenset()
    return [
        (syndicator, None, none),
        (reference_document(luadata), root / "_lab_one_" / ACCOUNT_SV / "LabNewAddon.lua", none),
        (reference_document(luadata), None, none),
        (reference_document(luadata), tie / ACCOUNT_SV / "LabNewAddon.lua", written),
    ]


def _serialize_all(
    luadata: Any, cases: list[tuple[Any, Path | None, frozenset[Path]]]
) -> list[bytes]:
    return [luadata.serialize(doc, target=t, lab_written=w) for doc, t, w in cases]


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_constructed_trees_hundred_runs_give_identical_bytes_under_a_decimal_comma_locale(
    luadata: Any, tmp_path: Path
) -> None:
    """100 serializations of each case under a locale whose decimal point
    is not `.`, each identical to the first under the process's own locale.
    The tie case is also checked against its expected bytes."""
    cases = _determinism_cases(luadata, tmp_path / "install")
    first = _serialize_all(luadata, cases)
    assert first[3] == TABS_LF
    name = _decimal_comma_locale()
    saved = locale.setlocale(locale.LC_ALL)
    try:
        locale.setlocale(locale.LC_ALL, name)
        for _ in range(100):
            assert _serialize_all(luadata, cases) == first
    finally:
        locale.setlocale(locale.LC_ALL, saved)


_CHILD = """
import locale, pickle, sys
locale.setlocale(locale.LC_ALL, sys.argv[3])
assert locale.localeconv()["decimal_point"] != "."
from wowlab_core import luadata
with open(sys.argv[1], "rb") as handle:
    cases = pickle.load(handle)
out = [luadata.serialize(doc, target=t, lab_written=w) for doc, t, w in cases]
with open(sys.argv[2], "wb") as handle:
    pickle.dump(out, handle)
"""


@pytest.mark.xfail(strict=True, reason="M10-12 not implemented")
def test_constructed_trees_fresh_interpreters_with_other_hash_seeds_and_locales_agree(
    luadata: Any, tmp_path: Path
) -> None:
    """Same documents, same bytes, in fresh interpreters under a
    decimal-comma locale and different hash seeds (so no set or dict order of
    `str` keys, and no directory listing order, can leak into the output)."""
    cases = _determinism_cases(luadata, tmp_path / "install")
    expected = _serialize_all(luadata, cases)
    name = _decimal_comma_locale()
    (tmp_path / "cases.pickle").write_bytes(pickle.dumps(cases))
    for seed in ("0", "1", "4242"):
        env = {**os.environ, "LC_ALL": name, "LANG": name, "PYTHONHASHSEED": seed}
        out_path = tmp_path / f"out-{seed}.pickle"
        subprocess.run(
            [sys.executable, "-c", _CHILD, str(tmp_path / "cases.pickle"), str(out_path), name],
            env=env,
            check=True,
            timeout=120,
        )
        assert pickle.loads(out_path.read_bytes()) == expected, (
            f"PYTHONHASHSEED={seed}, locale {name}"
        )
