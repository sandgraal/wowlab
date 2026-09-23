"""Graders for `wowlab_core.luadata` against the real SavedVariables captures
(M10-04T; L1, L3, L4, L8).

Written before `luadata.py` exists, from `docs/LAB_PLAN.md` §6.4 (parsing
half) with its 2026-09-22 amendment, `docs/LAB_FORMATS.md` §4 with its
2026-09-22 amendments, and the five files of the Forever beta part-2
capture. Every grader carries one marker line that M10-04 deletes; nothing
else in this file is the implementer's to change. Constructed inputs
(hostile and boundary cases, and the grammar points no capture shows yet)
are in `test_luadata_constructed.py`.

Every `savedvariables` row of the fixture index is graded by the
parametrized tests, so a new capture is graded the day its row lands. The
named tests pin facts of named files and never count the corpus. No grader
hands the committed fixture tree to `read()`: it reads a copy.

The seam these graders hold `luadata` to
----------------------------------------
§6.4 names the value types (`LuaTable`, `LuaString`, `LuaNumber`,
`LuaBool`, `LuaNil`), the key styles, `LuaString.data` / `.value` / `.raw`,
`as_int()` / `as_float()` and `document.to_python()`, and (amendment item 7)
requires the document alone to rebuild the source. The names that rebuild
needs are pinned here, as few as it could be done with:

- `luadata.parse(data: bytes)` returns the document; `luadata.read(path)`
  reads a file and returns the same document (both enforce the size bound).
- Trivia: every token keeps the bytes in front of it (whitespace, line
  breaks, comments), as `bytes`:
  - the document: `assignments` (source order) and `tail`, the bytes after
    the last token;
  - an assignment: `lead` (before its name), `name: str`, `eq_lead` (before
    `=`), `value`;
  - every value (`LuaTable`, `LuaString`, `LuaNumber`, `LuaBool`,
    `LuaNil`), including one used as a key: `lead` (before its first
    token); a `LuaTable` also has `entries` (source order) and
    `close_lead` (before `}`);
  - an entry: `lead` (before `[` or the bare name; a positional entry's
    leading bytes may sit here or on its value), `key_close_lead` (before
    `]`), `eq_lead` (before `=`), `sep` (`b","`, `b";"`, or `b""` when the
    entry has none) and `sep_lead` (before the separator).
  A slot the grammar does not have for that entry may be `b""` or `None`.
- An entry also has `style`, equal to one of `"positional"`, `"string"`,
  `"number"`, `"name"`, `"boolean"` (a `str` or a `StrEnum`); `key`: `None`
  (positional), `LuaString`, `LuaNumber`, the identifier as a `str` (bare
  name), `LuaBool` (`[true]` / `[false]`); `value`; `comment`, a view of the
  comment that follows the entry on its line (after its separator, or after
  its value when it has none), as `bytes` from `--` to the line break, or
  `None`; and `duplicate`, `True` only on the later of two entries of one
  table whose keys are equal under Lua key equality (`a` and `["a"]`, `[1]`
  and `[1.0]` and the first positional entry; `[1]` and `["1"]` differ).
- `LuaString.data` is the decoded `bytes`; `.value` is `data` decoded as
  UTF-8 with `surrogateescape`; `.raw` is the literal's source bytes,
  quotes included. `LuaNumber.raw` is its source text (`str`).
  `LuaBool.value` is a `bool`.
- `luadata.LuaDataError` is raised for every rejection and carries `line`
  and `column` (both 1-based; CRLF, LFCR, LF and CR each end one line, as
  in Lua 5.1) and `token` (the offending source text as `bytes` or `str`;
  empty or `None` at the end of input). `luadata.LuaLimitError` is its
  subclass for the depth, file-size and string-length bounds, and is
  raised for nothing else.

Numbers: every Lua 5.1 number is a double (the client's Lua is taken to be
5.1, **[verify]** for Forever). `int` or `float` out of `to_python()`
follows how the number is written, not a client type.

`_luadata_oracle.py` rebuilds tokens and bytes from the document alone;
M10-12's serializer is that rebuild for unmodified documents.

Not gradable on real data yet (the corpus has no such file or case): the
§6.4 performance target (the largest real file is 14 484 bytes; M10-04
measures it on a constructed input in its PR), a per-character
SavedVariables file, the account `SavedVariables.lua`, an Ace3 profile,
`-- [n]` or any other comment, tab indentation, `[number]`, bare-name and
boolean keys, `;` separators, non-ASCII, invalid UTF-8 or single-quoted
strings, escapes other than `\\`, raw control bytes in strings,
duplicates, non-finite numbers, and number text fidelity: every number in
the corpus is already in shortest round-trip form, so a parser that stored
`repr(float(text))` would still pass here. The constructed file covers
each of these.
"""

from __future__ import annotations

import re
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any

import pytest
from _luadata_oracle import (
    FIXTURES,
    POSITIONAL,
    STRING,
    document_tokens,
    entry,
    indexed,
    load,
    path,
    rebuild,
    source_tokens,
    tables,
)

pytestmark = pytest.mark.parser

SV_DIR = "macos/forever/WTF/Account/90000001#6/SavedVariables"
RARESCANNER = f"{SV_DIR}/RareScanner.lua"
RARESCANNER_BAK = f"{SV_DIR}/RareScanner.lua.bak"
GAMEPAD = f"{SV_DIR}/Blizzard_GamepadSmartNavigation.lua"
SYNDICATOR = f"{SV_DIR}/Syndicator.lua"
DBM = f"{SV_DIR}/DBM-StatusBarTimers.lua"
NAMED = [RARESCANNER, RARESCANNER_BAK, GAMEPAD, SYNDICATOR, DBM]

SAVEDVARIABLES = indexed("savedvariables")

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ITEM_LINK = b"|cnIQ1:|Hitem:277114::::::::7:1490:::::::::|h[Apprentice's Skinning Satchel]|h|r"


@pytest.fixture
def luadata() -> Any:
    return load()


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _entries(luadata: Any, doc: Any) -> list[Any]:
    return [e for _d, t in tables(luadata, doc) for e in t.entries]


# ── the corpus (no marker: these read bytes and never import luadata) ───────


def test_index_lists_the_named_savedvariables_fixtures() -> None:
    """The parametrized graders below take their cases from the index; this
    keeps them from silently collecting nothing."""
    for name in NAMED:
        assert name in SAVEDVARIABLES, name
        assert (FIXTURES / name).is_file(), name


def test_named_fixtures_are_what_the_graders_assume() -> None:
    """Guards the named graders against a mislabelled path. CRLF on every
    line, a leading blank line, no tab indentation, no comment (§4.2
    amendment of 2026-09-22)."""
    for name in NAMED:
        data = _read(name)
        assert data.startswith(b"\r\n"), name
        assert data.endswith(b"\r\n"), name
        assert data.count(b"\n") == data.count(b"\r\n") == data.count(b"\r"), name
        assert b"\t" not in data, name
        assert b"--" not in data, name
    assert _read(RARESCANNER) == _read(RARESCANNER_BAK)


# ── every indexed SavedVariables file ───────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("name", SAVEDVARIABLES)
def test_real_savedvariables_rebuilds_byte_for_byte_from_the_document(
    luadata: Any, name: str
) -> None:
    """L4 and §6.4 amendment item 7: the document alone gives the file back,
    exactly. Indentation and `-- [n]` comments are style, not grammar: the
    Forever files have neither."""
    data = _read(name)
    assert rebuild(luadata, luadata.parse(data)) == data


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("name", SAVEDVARIABLES)
def test_real_savedvariables_keeps_every_token_in_source_order(luadata: Any, name: str) -> None:
    """The structure, not only the bytes: names, key styles, key and value
    source text, separators, in file order. Nothing is dropped, merged,
    reordered or normalised, and nothing is hidden in trivia."""
    data = _read(name)
    assert document_tokens(luadata, luadata.parse(data)) == source_tokens(data)


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("name", SAVEDVARIABLES)
def test_real_savedvariables_parses(luadata: Any, name: str) -> None:
    doc = luadata.parse(_read(name))
    assert len(doc.assignments) >= 1
    for assignment in doc.assignments:
        assert isinstance(assignment.name, str)
        assert IDENT.fullmatch(assignment.name)
    for e in _entries(luadata, doc):
        assert isinstance(
            e.value,
            luadata.LuaTable | luadata.LuaString | luadata.LuaNumber | luadata.LuaBool,
        ), "nil is legal only as a top-level value (§4.1)"
        assert e.sep == b",", "the client ends every entry with `,` (§4.2)"


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("name", SAVEDVARIABLES)
def test_real_savedvariables_read_equals_parse(luadata: Any, name: str) -> None:
    """`read()` gets a copy, never the committed fixture tree (a reader that
    wrote beside its file would pollute the corpus)."""
    data = _read(name)
    with tempfile.TemporaryDirectory(prefix="luadata-read-") as folder:
        copy = Path(folder) / Path(name).name
        copy.write_bytes(data)
        from_file = luadata.read(copy)
    from_bytes = luadata.parse(data)
    assert rebuild(luadata, from_file) == data
    assert document_tokens(luadata, from_file) == document_tokens(luadata, from_bytes)
    assert from_file.to_python() == from_bytes.to_python()


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("name", SAVEDVARIABLES)
def test_real_savedvariables_to_python_is_plain_data(luadata: Any, name: str) -> None:
    doc = luadata.parse(_read(name))
    py = doc.to_python()
    assert type(py) is dict
    assert list(py) == list(dict.fromkeys(a.name for a in doc.assignments))

    def plain(v: Any) -> None:
        if type(v) is dict:
            for k, item in v.items():
                assert type(k) in (str, int, float, bool), k
                plain(item)
        elif type(v) is list:
            for item in v:
                plain(item)
        else:
            assert v is None or type(v) in (str, int, float, bool), repr(v)

    plain(py)


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("name", SAVEDVARIABLES)
def test_real_savedvariables_has_no_duplicate_flagged(luadata: Any, name: str) -> None:
    """The client writes a table from a Lua table, so its keys are unique;
    a flag on a real file is a false positive."""
    doc = luadata.parse(_read(name))
    assert not [e for e in _entries(luadata, doc) if e.duplicate]


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("writable", [False, True], ids=["read-only-folder", "writable-folder"])
@pytest.mark.parametrize("name", SAVEDVARIABLES)
def test_read_leaves_the_folder_untouched(
    luadata: Any, name: str, writable: bool, tmp_path: Path
) -> None:
    """L1: `read` works on a read-only file in a read-only folder, and in a
    writable folder it still creates, changes and removes nothing (no temp,
    cache or lock beside the file)."""
    folder = tmp_path / "SavedVariables"
    folder.mkdir()
    shutil.copyfile(FIXTURES / name, folder / "Other.lua")
    target = folder / Path(name).name
    shutil.copyfile(FIXTURES / name, target)
    target.chmod(stat.S_IRUSR)
    if not writable:
        folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
    before = {p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in folder.iterdir()}
    try:
        doc = luadata.read(target)
        after = {p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in folder.iterdir()}
    finally:
        folder.chmod(stat.S_IRWXU)
        target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert after == before
    assert target.read_bytes() == _read(name)
    assert rebuild(luadata, doc) == _read(name)


# ── named files ─────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize(
    ("name", "variable"),
    [
        (RARESCANNER, "RareScannerDB"),
        (RARESCANNER_BAK, "RareScannerDB"),
        (GAMEPAD, "SmartNavigation_Mod_Options"),
    ],
)
def test_nil_file_is_one_top_level_nil(luadata: Any, name: str, variable: str) -> None:
    """`X = nil` at top level is written by the client (§4.2 amendment); the
    variable is not named after the file. `to_python()` keeps the key with
    `None` (§6.4 amendment item 4). The leading blank line is the name's
    leading bytes; the final CRLF is the document's tail."""
    doc = luadata.parse(_read(name))
    [assignment] = doc.assignments
    assert assignment.name == variable
    assert isinstance(assignment.value, luadata.LuaNil)
    assert assignment.lead == b"\r\n"
    assert doc.tail == b"\r\n"
    py = doc.to_python()
    assert variable in py
    assert py[variable] is None


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_syndicator_has_three_assignments_in_file_order(luadata: Any) -> None:
    doc = luadata.parse(_read(SYNDICATOR))
    assert [a.name for a in doc.assignments] == [
        "SYNDICATOR_CONFIG",
        "SYNDICATOR_DATA",
        "SYNDICATOR_SUMMARIES",
    ]
    assert all(isinstance(a.value, luadata.LuaTable) for a in doc.assignments)
    assert [a.lead for a in doc.assignments] == [b"\r\n", b"\r\n", b"\r\n"]
    assert doc.tail == b"\r\n"


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_syndicator_config_keys_in_file_order(luadata: Any) -> None:
    data = _read(SYNDICATOR)
    doc = luadata.parse(data)
    config = doc.assignments[0].value
    block = data.split(b"SYNDICATOR_CONFIG = {\r\n", 1)[1].split(b"\r\n}\r\n", 1)[0]
    expected = re.findall(rb'^\["([^"]*)"\] = ', block, re.MULTILINE)
    assert len(expected) == 16
    assert [e.style for e in config.entries] == [STRING] * 16
    assert [e.key.data for e in config.entries] == expected
    assert [e.key.raw for e in config.entries] == [b'"' + k + b'"' for k in expected]
    limit = entry(luadata, config, "tooltips_character_limit").value
    assert isinstance(limit, luadata.LuaNumber)
    assert limit.raw == "4"
    assert limit.as_int() == 4
    source = entry(luadata, config, "auction_value_source").value
    assert isinstance(source, luadata.LuaString)
    assert (source.data, source.value, source.raw) == (b"none", "none", b'"none"')
    assert entry(luadata, config, "debug").value.value is False
    assert entry(luadata, config, "show_guild_banks_in_tooltips").value.value is True


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_syndicator_positional_entries_carry_no_comment(luadata: Any) -> None:
    """82 positional entries, none with a `-- [n]` comment, and no comment
    anywhere (§4.2 amendment): the comment is optional style."""
    doc = luadata.parse(_read(SYNDICATOR))
    entries = _entries(luadata, doc)
    positional = [e for e in entries if e.style == POSITIONAL]
    assert len(positional) == 82
    assert all(e.key is None for e in positional)
    assert [e for e in entries if e.comment is not None] == []
    assert {e.style for e in entries} == {POSITIONAL, STRING}


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_syndicator_empty_tables(luadata: Any) -> None:
    """An empty table is written as `{` then `}` on two lines, 47 times; the
    line break is the `}`'s leading bytes."""
    doc = luadata.parse(_read(SYNDICATOR))
    empty = [t for _d, t in tables(luadata, doc) if len(t.entries) == 0]
    assert len(empty) == 47
    assert {t.close_lead for t in empty} == {b"\r\n"}


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_syndicator_nests_six_tables_deep(luadata: Any) -> None:
    doc = luadata.parse(_read(SYNDICATOR))
    assert max(d for d, _t in tables(luadata, doc)) == 6


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_syndicator_item_link_is_ordinary_text(luadata: Any) -> None:
    """`|c…|r` colour codes and `|H…|h` links are ordinary bytes, and a `'`
    inside a double-quoted string is raw (§4.2)."""
    doc = luadata.parse(_read(SYNDICATOR))
    bags = path(
        luadata,
        doc,
        "SYNDICATOR_DATA",
        "Characters",
        "Labchard Labrealme",
        "containerInfo",
        "bags",
    )
    assert [e.style for e in bags.entries] == [POSITIONAL] * 5
    first = bags.entries[0].value
    assert isinstance(first, luadata.LuaTable)
    link = entry(luadata, first, "itemLink").value
    assert isinstance(link, luadata.LuaString)
    assert link.data == ITEM_LINK
    assert link.value == ITEM_LINK.decode("ascii")
    assert link.raw == b'"' + ITEM_LINK + b'"'
    item_id = entry(luadata, first, "itemID").value
    assert (item_id.raw, item_id.as_int()) == ("277114", 277114)
    assert isinstance(bags.entries[1].value, luadata.LuaTable)
    assert len(bags.entries[1].value.entries) == 0


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_syndicator_empty_string_key(luadata: Any) -> None:
    doc = luadata.parse(_read(SYNDICATOR))
    by_realm = path(luadata, doc, "SYNDICATOR_SUMMARIES", "Characters", "ByRealm")
    [only] = by_realm.entries
    assert only.style == STRING
    assert (only.key.data, only.key.value, only.key.raw) == (b"", "", b'""')
    assert isinstance(only.value, luadata.LuaTable)


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_syndicator_positional_false_and_positional_empty_table(luadata: Any) -> None:
    doc = luadata.parse(_read(SYNDICATOR))
    pending = path(luadata, doc, "SYNDICATOR_SUMMARIES", "Warband", "Pending")
    [flag] = pending.entries
    assert flag.style == POSITIONAL
    assert isinstance(flag.value, luadata.LuaBool)
    assert flag.value.value is False
    summary = path(luadata, doc, "SYNDICATOR_SUMMARIES", "Warband", "Summary")
    [holder] = summary.entries
    assert holder.style == POSITIONAL
    assert isinstance(holder.value, luadata.LuaTable)
    assert len(holder.value.entries) == 0


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_syndicator_to_python(luadata: Any) -> None:
    """Array-like tables (keys exactly 1..n) become lists; an empty table
    becomes `{}` (§6.4 amendment item 4)."""
    py = luadata.parse(_read(SYNDICATOR)).to_python()
    assert list(py) == ["SYNDICATOR_CONFIG", "SYNDICATOR_DATA", "SYNDICATOR_SUMMARIES"]
    assert py["SYNDICATOR_CONFIG"]["tooltips_character_limit"] == 4
    assert type(py["SYNDICATOR_CONFIG"]["tooltips_character_limit"]) is int
    assert py["SYNDICATOR_CONFIG"]["debug"] is False
    character = py["SYNDICATOR_DATA"]["Characters"]["Labchard Labrealme"]
    bags = character["containerInfo"]["bags"]
    assert type(bags) is list
    assert len(bags) == 5
    assert bags[0]["itemLink"] == ITEM_LINK.decode("ascii")
    assert bags[0]["itemID"] == 277114
    assert bags[1] == {}
    assert type(bags[1]) is dict
    warband = py["SYNDICATOR_SUMMARIES"]["Warband"]
    assert warband["Pending"] == [False]
    assert warband["Summary"] == [{}]
    assert list(py["SYNDICATOR_SUMMARIES"]["Characters"]["ByRealm"]) == [""]


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_dbm_keys_in_file_order_all_string_style(luadata: Any) -> None:
    data = _read(DBM)
    doc = luadata.parse(data)
    assert [a.name for a in doc.assignments] == ["DBT_AllPersistentOptions"]
    options = path(luadata, doc, "DBT_AllPersistentOptions", "Default", "DBM")
    expected = [k.decode() for k in re.findall(rb'^\["([^"]*)"\] = [^{]', data, re.MULTILINE)]
    assert len(expected) == 140
    assert [e.key.value for e in options.entries] == expected
    assert expected[0] == "HugeBorderColorG"
    assert expected[-1] == "VarianceTexture"
    assert all(e.style == STRING for e in options.entries)
    assert {e.lead for e in options.entries} == {b"\r\n"}
    assert max(d for d, _t in tables(luadata, doc)) == 3


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize(
    ("key", "raw", "as_int"),
    [("TimerY", "-260", -260), ("TimerX", "-223", -223), ("HugeTimerY", "-120", -120)],
)
def test_dbm_negative_integers_keep_their_text(
    luadata: Any, key: str, raw: str, as_int: int
) -> None:
    doc = luadata.parse(_read(DBM))
    number = entry(luadata, path(luadata, doc, "DBT_AllPersistentOptions", "Default", "DBM"), key)
    assert isinstance(number.value, luadata.LuaNumber)
    assert number.value.raw == raw
    assert number.value.as_int() == as_int
    assert number.value.as_float() == float(as_int)


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("StartColorI2G", "0.6745098233222961"),
        ("EndColorI2G", "0.5058823823928833"),
        ("EndColorUIB", "0.0117647058823529"),
        ("EndColorUIG", "0.92156862745098"),
        ("StartColorUIB", "0.0627450980392157"),
        ("EnlargeBarTime", "9.9"),
        ("BackgroundAlpha", "0.3"),
        ("HugeScale", "1.03"),
    ],
)
def test_dbm_floats_keep_their_text(luadata: Any, key: str, raw: str) -> None:
    """The source text of the file's floats, up to 16 significant digits
    (`0.6745098233222961`; `0.0117647058823529` has 15). Every one is
    already the shortest round-trip form, so this cannot tell kept text from
    `repr(float(text))`; the constructed number-text graders
    (`test_number_source_text_is_kept`: `100.000`, `1E-07`, `0x1F`, `-0`)
    are what grade fidelity."""
    doc = luadata.parse(_read(DBM))
    number = entry(
        luadata, path(luadata, doc, "DBT_AllPersistentOptions", "Default", "DBM"), key
    ).value
    assert isinstance(number, luadata.LuaNumber)
    assert number.raw == raw
    assert number.as_float() == float(raw)


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_dbm_backslash_escape_is_decoded_and_raw_is_kept(luadata: Any) -> None:
    doc = luadata.parse(_read(DBM))
    options = path(luadata, doc, "DBT_AllPersistentOptions", "Default", "DBM")
    texture = entry(luadata, options, "Texture").value
    decoded = b"Interface\\AddOns\\DBM-StatusBarTimers\\textures\\default.blp"
    assert texture.data == decoded
    assert texture.value == decoded.decode("ascii")
    assert texture.raw == b'"' + decoded.replace(b"\\", b"\\\\") + b'"'
    skin = entry(luadata, options, "Skin").value
    assert (skin.data, skin.value, skin.raw) == (b"", "", b'""')


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_dbm_to_python_number_types(luadata: Any) -> None:
    """`int` or `float` follows the spelling (`1` is an int although the
    client holds a double)."""
    py = luadata.parse(_read(DBM)).to_python()
    options = py["DBT_AllPersistentOptions"]["Default"]["DBM"]
    assert len(options) == 140
    assert (options["TimerY"], type(options["TimerY"])) == (-260, int)
    assert (options["TextColorR"], type(options["TextColorR"])) == (1, int)
    assert (options["Width"], type(options["Width"])) == (183, int)
    assert (options["StartColorI2G"], type(options["StartColorI2G"])) == (
        0.6745098233222961,
        float,
    )
    assert options["HugeBarsEnabled"] is True
    assert options["Skin"] == ""
