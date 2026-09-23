"""Graders for `wowlab_core.luadata` against the real SavedVariables captures
(M10-04T; L3, L4, L8).

Written before `luadata.py` exists, from `docs/LAB_PLAN.md` §6.4 (parsing
half), `docs/LAB_FORMATS.md` §4 with its 2026-09-22 amendment, and the five
files of the Forever beta part-2 capture. Every grader carries one marker
line that M10-04 deletes; nothing else in this file is the implementer's to
change. Constructed inputs (hostile and boundary cases, and the grammar
points no capture shows yet) are in `test_luadata_constructed.py`.

Every `savedvariables` row of the fixture index is graded by the
parametrized tests, so a new capture is graded the day its row lands. The
named tests pin facts of named files and never count the corpus.

The seam these graders hold `luadata` to
----------------------------------------
§6.4 names the value types (`LuaTable`, `LuaString`, `LuaNumber`,
`LuaBool`, `LuaNil`), the key styles, `as_int()` / `as_float()` and
`document.to_python()`. The rest is pinned here, as small as it could be:

- `luadata.parse(data: bytes)` returns the document; `luadata.read(path)`
  reads a file and returns the same document (the size bound is enforced on
  both).
- The document has `assignments` (in source order; each has `name: str` and
  `value`), `leading_comments` and `trailing_comments` (sequences of `str`,
  each a comment line's text from `--` up to, not including, its line
  ending), and `to_python()`.
- `LuaTable.entries` is a sequence of entries in source order. An entry has
  `style`, which compares equal to one of `"positional"`, `"string"`,
  `"number"`, `"name"` (a `str` or a `StrEnum`); `key`, which is `None` for
  a positional entry, a `LuaString` for `["string"]`, a `LuaNumber` for
  `[number]`, the identifier as a `str` for a bare `name`, and a `LuaBool`
  for `[true]` / `[false]` (whose style §6.4 does not name, so it is not
  graded); `value`; `comment`, the trailing comment on the entry's line as
  written from `--` to the line ending, or `None`; and `duplicate`, `True`
  on every entry whose key equals the key of an earlier entry in the same
  table (Lua key equality: `a = ` and `["a"]` are one key, the n-th
  positional entry and `[n]` are one key, `[1]` and `[1.0]` are one key,
  `[1]` and `["1"]` are two).
- `LuaString.value` is the decoded `str`; `LuaString.raw` is the source
  text including its quotes. `LuaNumber.raw` is the source text.
  `LuaBool.value` is a `bool`.
- `luadata.LuaDataError` is raised for every rejection and carries `line`
  and `column` (both 1-based; a CRLF or LF ends a line) and `token` (the
  offending source text, `""` or `None` at end of input).
  `luadata.LuaLimitError` is its subclass for the depth, file-size and
  string-length bounds, and is raised for nothing else.

The oracle in `_luadata_oracle.py` rebuilds each fixture's tokens and bytes
from the parse, taking only layout (line ending, indent unit, leading blank
lines) from the source. That is how L4 is graded here without a serializer:
`serialize(parse(x)) == x` itself is M10-12's, graded by M10-12T.

Not gradable on real data yet (the corpus has no such file): the §6.4
performance target (the largest real file is 14 484 bytes), a per-character
SavedVariables file, the account `SavedVariables.lua`, an Ace3 profile,
`-- [n]` comments, tab indentation, `[number]` keys, non-ASCII strings,
single-quoted strings, escapes other than `\\`, and non-finite numbers.
The constructed file covers each grammar point; the performance target is
M10-04's to measure (its acceptance) and is not graded here.
"""

from __future__ import annotations

import re
import shutil
import stat
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
ITEM_LINK = "|cnIQ1:|Hitem:277114::::::::7:1490:::::::::|h[Apprentice's Skinning Satchel]|h|r"


@pytest.fixture
def luadata() -> Any:
    return load()


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# ── the corpus (no marker: this is about the index, not luadata) ────────────


def test_index_lists_the_named_savedvariables_fixtures() -> None:
    """The parametrized graders below take their cases from the index; this
    keeps them from silently collecting nothing."""
    for name in NAMED:
        assert name in SAVEDVARIABLES, name
        assert (FIXTURES / name).is_file(), name


# ── every indexed SavedVariables file ───────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("name", SAVEDVARIABLES)
def test_real_savedvariables_parses(luadata: Any, name: str) -> None:
    doc = luadata.parse(_read(name))
    assert len(doc.assignments) >= 1
    for assignment in doc.assignments:
        assert isinstance(assignment.name, str)
        assert IDENT.fullmatch(assignment.name)
    for _depth, table in tables(luadata, doc):
        for e in table.entries:
            assert isinstance(
                e.value,
                luadata.LuaTable | luadata.LuaString | luadata.LuaNumber | luadata.LuaBool,
            ), "nil is legal only as a top-level value (§4.1)"


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("name", SAVEDVARIABLES)
def test_real_savedvariables_keeps_every_token_in_source_order(luadata: Any, name: str) -> None:
    """L4 at the parse: names, key styles, key and value source text
    (numbers and strings as written), comments, in file order. Nothing is
    dropped, merged, reordered or normalised."""
    data = _read(name)
    assert document_tokens(luadata, luadata.parse(data)) == source_tokens(data)


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("name", SAVEDVARIABLES)
def test_real_savedvariables_rebuilds_byte_for_byte_from_the_parse(luadata: Any, name: str) -> None:
    """L4, byte level: from the parse alone plus the file's own layout, the
    oracle reproduces the file exactly. Indentation and `-- [n]` comments
    are style, not grammar: the Forever files have neither."""
    data = _read(name)
    assert rebuild(luadata, luadata.parse(data), data) == data


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("name", SAVEDVARIABLES)
def test_real_savedvariables_read_equals_parse(luadata: Any, name: str) -> None:
    data = _read(name)
    from_file = luadata.read(FIXTURES / name)
    from_bytes = luadata.parse(data)
    assert document_tokens(luadata, from_file) == document_tokens(luadata, from_bytes)
    assert from_file.to_python() == from_bytes.to_python()


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
@pytest.mark.parametrize("name", SAVEDVARIABLES)
def test_real_savedvariables_to_python_is_plain_data(luadata: Any, name: str) -> None:
    doc = luadata.parse(_read(name))
    py = doc.to_python()
    assert type(py) is dict
    names = [a.name for a in doc.assignments]
    non_nil = [a.name for a in doc.assignments if not isinstance(a.value, luadata.LuaNil)]
    assert set(non_nil) <= set(py) <= set(names)

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
    assert not [e for _d, t in tables(luadata, doc) for e in t.entries if e.duplicate]


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
    assert document_tokens(luadata, doc) == source_tokens(_read(name))


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
    """`X = nil` at top level is written by the client (amendment of
    2026-09-22); the variable is not named after the file."""
    doc = luadata.parse(_read(name))
    assert [a.name for a in doc.assignments] == [variable]
    assert isinstance(doc.assignments[0].value, luadata.LuaNil)
    assert list(doc.leading_comments) == []
    assert list(doc.trailing_comments) == []
    assert doc.to_python().get(variable) is None


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_syndicator_has_three_assignments_in_file_order(luadata: Any) -> None:
    doc = luadata.parse(_read(SYNDICATOR))
    assert [a.name for a in doc.assignments] == [
        "SYNDICATOR_CONFIG",
        "SYNDICATOR_DATA",
        "SYNDICATOR_SUMMARIES",
    ]
    assert all(isinstance(a.value, luadata.LuaTable) for a in doc.assignments)


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_syndicator_config_keys_in_file_order(luadata: Any) -> None:
    data = _read(SYNDICATOR)
    doc = luadata.parse(data)
    config = doc.assignments[0].value
    block = data.split(b"SYNDICATOR_CONFIG = {\r\n", 1)[1].split(b"\r\n}\r\n", 1)[0]
    expected = re.findall(rb'^\["([^"]*)"\] = ', block, re.MULTILINE)
    assert len(expected) == 16
    assert [e.style for e in config.entries] == [STRING] * 16
    assert [e.key.value for e in config.entries] == [k.decode() for k in expected]
    assert [e.key.raw for e in config.entries] == [f'"{k.decode()}"' for k in expected]
    limit = entry(luadata, config, "tooltips_character_limit").value
    assert isinstance(limit, luadata.LuaNumber)
    assert limit.raw == "4"
    assert limit.as_int() == 4
    source = entry(luadata, config, "auction_value_source").value
    assert isinstance(source, luadata.LuaString)
    assert (source.value, source.raw) == ("none", '"none"')
    assert entry(luadata, config, "debug").value.value is False
    assert entry(luadata, config, "show_guild_banks_in_tooltips").value.value is True


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_syndicator_positional_entries_carry_no_comment(luadata: Any) -> None:
    """82 positional entries, none with a `-- [n]` comment, and no comment
    anywhere (amendment of 2026-09-22): the comment is optional style."""
    doc = luadata.parse(_read(SYNDICATOR))
    entries = [e for _d, t in tables(luadata, doc) for e in t.entries]
    positional = [e for e in entries if e.style == POSITIONAL]
    assert len(positional) == 82
    assert all(e.key is None for e in positional)
    assert [e for e in entries if e.comment is not None] == []
    assert {e.style for e in entries} == {POSITIONAL, STRING}


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_syndicator_empty_tables(luadata: Any) -> None:
    """An empty table is written as `{` then `}` on two lines, 47 times."""
    doc = luadata.parse(_read(SYNDICATOR))
    assert len([t for _d, t in tables(luadata, doc) if len(t.entries) == 0]) == 47


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_syndicator_nests_six_tables_deep(luadata: Any) -> None:
    doc = luadata.parse(_read(SYNDICATOR))
    assert max(d for d, _t in tables(luadata, doc)) == 6


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_syndicator_item_link_is_ordinary_text(luadata: Any) -> None:
    """`|c…|r` colour codes and `|H…|h` links are ordinary characters, and a
    `'` inside a double-quoted string is raw (§4.2)."""
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
    assert link.value == ITEM_LINK
    assert link.raw == f'"{ITEM_LINK}"'
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
    assert (only.key.value, only.key.raw) == ("", '""')
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
    """Array-like tables (keys exactly 1..n) become lists; an empty table is
    an empty container (whether `[]` or `{}` is not specified)."""
    py = luadata.parse(_read(SYNDICATOR)).to_python()
    assert list(py) == ["SYNDICATOR_CONFIG", "SYNDICATOR_DATA", "SYNDICATOR_SUMMARIES"]
    assert py["SYNDICATOR_CONFIG"]["tooltips_character_limit"] == 4
    assert type(py["SYNDICATOR_CONFIG"]["tooltips_character_limit"]) is int
    assert py["SYNDICATOR_CONFIG"]["debug"] is False
    character = py["SYNDICATOR_DATA"]["Characters"]["Labchard Labrealme"]
    bags = character["containerInfo"]["bags"]
    assert type(bags) is list
    assert len(bags) == 5
    assert bags[0]["itemLink"] == ITEM_LINK
    assert bags[0]["itemID"] == 277114
    assert bags[1] in ({}, [])
    warband = py["SYNDICATOR_SUMMARIES"]["Warband"]
    assert warband["Pending"] == [False]
    assert type(warband["Summary"]) is list
    assert len(warband["Summary"]) == 1
    assert warband["Summary"][0] in ({}, [])
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
    """The source text survives exactly, including a 16-digit shortest form
    whose float would print differently (`0.0117647058823529`)."""
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
    assert texture.value == "Interface\\AddOns\\DBM-StatusBarTimers\\textures\\default.blp"
    assert texture.raw == '"Interface\\\\AddOns\\\\DBM-StatusBarTimers\\\\textures\\\\default.blp"'
    skin = entry(luadata, options, "Skin").value
    assert (skin.value, skin.raw) == ("", '""')


@pytest.mark.xfail(strict=True, reason="M10-04 not implemented")
def test_dbm_to_python_number_types(luadata: Any) -> None:
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


def test_named_fixtures_are_what_the_graders_assume() -> None:
    """Guards the named graders against a mislabelled path (no marker: this
    reads bytes only). CRLF on every line, a leading blank line, no tab
    indentation, no comment (amendment of 2026-09-22)."""
    for name in NAMED:
        data = _read(name)
        assert data.startswith(b"\r\n"), name
        assert data.endswith(b"\r\n"), name
        assert data.count(b"\n") == data.count(b"\r\n"), name
        assert b"\t" not in data, name
        assert b"--" not in data, name
    assert _read(RARESCANNER) == _read(RARESCANNER_BAK)
