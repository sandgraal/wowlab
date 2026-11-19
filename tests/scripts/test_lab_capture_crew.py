"""`scripts/lab_capture.py` keeps crew records out of the repository (M19-04).

`docs/LAB_PLAN.md` §20.5 term 3, ADR-0036 term 3: any captured file whose
bytes hold `WowLabCrewDB`, in any casing and whatever the file is called, is
refused unless the name occurs once, as a top-level assignment whose whole
table is the one empty shape, written as the client writes it. There is no
flag that empties a book. The refusal names the file and the reason, never a
key.

CONSTRUCTED INPUT. Every tree and byte string here is synthetic, built in
`tmp_path` or inline. Every player key, guild and note is invented, taken from
the fixed list in `tests/repo/test_crew_book_out_of_repo.py`
(`CONSTRUCTED_KEYS`). No test needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_lab_capture import ACCOUNT, build_install, capture, lab_capture, outputs, tree_state

# Invented, from CONSTRUCTED_KEYS in tests/repo/test_crew_book_out_of_repo.py.
KEY_A = "Inventeda-Fakerealm"
KEY_B = "Inventedb-Fakerealm"
GUILD = "Invented Guild Of Testing"
NOTE = "invented note for a test"


def _lua(*lines: str, eol: str = "\r\n") -> bytes:
    """A SavedVariables file as the client writes it: a leading blank line,
    no indentation, CRLF (LAB_FORMATS §4.2 amended)."""
    return (eol + eol.join(lines) + eol).encode()


EMPTY_LINES = (
    "WowLabCrewDB = {",
    '["schema"] = 1,',
    '["on"] = true,',
    '["started"] = 1794950000,',
    '["probe"] = {',
    '["loads"] = 12,',
    "},",
    '["skipped"] = 0,',
    '["players"] = {',
    "},",
    '["current"] = {',
    "},",
    "}",
)
EMPTY = _lua(*EMPTY_LINES)

PLAYER = (
    '["Inventeda-Fakerealm"] = {',
    '["class"] = "MAGE",',
    '["level"] = 60,',
    f'["guild"] = "{GUILD}",',
    '["met"] = 3,',
    f'["note"] = "{NOTE}",',
    "},",
)


def _with(lines: tuple[str, ...], at: str, insert: tuple[str, ...]) -> tuple[str, ...]:
    """`lines` with `insert` placed right after the first line equal to `at`."""
    index = lines.index(at) + 1
    return (*lines[:index], *insert, *lines[index:])


WITH_PLAYERS = _lua(*_with(EMPTY_LINES, '["players"] = {', PLAYER))
ONLY_CURRENT = _lua(*_with(EMPTY_LINES, '["current"] = {', ('["Inventedb-Fakerealm"] = true,',)))
IN_STRING = _lua(f'OtherAddonDB = "WowLabCrewDB = {{ {KEY_A} }}"')
IN_LONG_STRING = _lua("OtherAddonDB = [==[", *EMPTY_LINES, "]==]")
IN_LONG_STRING_FILLED = _lua(
    "OtherAddonDB = [[", *_with(EMPTY_LINES, '["players"] = {', PLAYER), "]]"
)
IN_COMMENT = _lua("--[[", *EMPTY_LINES, "]]", "OtherAddonDB = 1")
SECOND_ASSIGNMENT = EMPTY + _lua(*_with(EMPTY_LINES, '["players"] = {', PLAYER))
TWO_EMPTY = EMPTY + EMPTY
OTHER_CASING = _lua(*(line.replace("WowLabCrewDB", "WOWLABCREWDB") for line in EMPTY_LINES))
LOWER_CASING = _lua(*(line.replace("WowLabCrewDB", "wowlabcrewdb") for line in EMPTY_LINES))
NESTED = _lua("OtherAddonDB = {", *EMPTY_LINES[:-1], "},", "}")
STRING_VALUE = _lua(*(line.replace("1794950000", f'"{KEY_A}"') for line in EMPTY_LINES))
EXTRA_KEY = _lua(*_with(EMPTY_LINES, '["skipped"] = 0,', ('["guild"] = 1,',)))
REPEATED_KEY = _lua(*_with(EMPTY_LINES, '["skipped"] = 0,', ('["skipped"] = 0,',)))
ONE_LINE_PLAYERS = EMPTY.replace(b'["players"] = {\r\n},', b'["players"] = {},')
PROBE_EXTRA = _lua(*_with(EMPTY_LINES, '["loads"] = 12,', ('["Inventeda-Fakerealm"] = 1,',)))
POSITIONAL = _lua(*_with(EMPTY_LINES, '["players"] = {', (f'"{KEY_A}",',)))
NIL_BOOK = _lua("WowLabCrewDB = nil")
PROBE_LOST_TWICE = _lua(*_with(EMPTY_LINES, '["loads"] = 12,', ('["lost"] = true,',) * 2))
PROBE_LOST_STRING = _lua(*_with(EMPTY_LINES, '["loads"] = 12,', ('["lost"] = "yes",',)))
LOST_AT_TOP = _lua(*_with(EMPTY_LINES, '["skipped"] = 0,', ('["lost"] = true,',)))
# The name escaped or split behind a global-table write: refused for the write.
# (`"_G[" + ...` keeps the repository scan from reading a bracketed key here.)
ESCAPED_DECIMAL = _lua("_G[" + '"\\87owLabCrewDB"] = {', '["players"] = {', *PLAYER, "},", "}")
ESCAPED_HEX = _lua("_G[" + '"\\x57owLabCrewDB"] = {', "}")
ESCAPED_UNICODE = _lua("_G[" + '"\\u{57}owLabCrewDB"] = {', "}")
SPLIT = _lua('_G["WowLab".."CrewDB"] = {', "}")
SPLIT_THREE = _lua('_G["WowLab".."Crew".."DB"] = {', '["players"] = {', *PLAYER, "},", "}")
ESCAPED_C = _lua("_G[" + '"WowLab\\67rewDB"]={}')
SPACED_G = _lua("_G [" + '"WowLab\\67rewDB"] = {', "}")
RAWSET = _lua('rawset(_G, "WowLab".."Crew".."DB", {})')
SETGLOBAL = _lua('setglobal("WowLab".."Crew".."DB", {})')
# The same words inside strings and comments are data, not a write.
WRITE_WORDS_IN_TEXT = _lua(
    'OtherAddonDB = "_G[1] and rawset and setglobal",', "-- _G[2] rawset(x) setglobal(y)"
)
# The name escaped without a global write (a key of another table): it still holds `CrewDB`.
ESCAPED_KEY = _lua("OtherAddonDB = {", "[" + '"\\87owLabCrewDB"] = 1,', "}")
CREWDB_BESIDE = EMPTY + _lua('OtherAddonDB = "CrewDB"')
# UTF-16, each byte order, with and without a BOM.
UTF16 = {
    "UTF-16 little-endian": WITH_PLAYERS.decode().encode("utf-16-le"),
    "UTF-16 big-endian": WITH_PLAYERS.decode().encode("utf-16-be"),
    "UTF-16 with a BOM": WITH_PLAYERS.decode().encode("utf-16"),
    "UTF-16 empty shape, lower casing": EMPTY.lower().decode().encode("utf-16-be"),
}
NIL_TWICE = NIL_BOOK + NIL_BOOK
NIL_INDENTED = _lua("  WowLabCrewDB = nil")
NIL_OTHER_CASING = _lua("WOWLABCREWDB = nil")
NIL_THEN_MORE = _lua("WowLabCrewDB = nil -- trailing")
NIL_THEN_TABLE = _lua("WowLabCrewDB = nil, {}")
# `"abc\"` then a line break: the quote is escaped, so the string never closes.
ESCAPED_QUOTE_UNCLOSED = _lua('OtherAddonDB = "abc\\"', *EMPTY_LINES)
UNCLOSED = _lua(*EMPTY_LINES[:-1])
AFTER_UNCLOSED_STRING = _lua('OtherAddonDB = "open', *EMPTY_LINES)
CONTINUED_STRING = _lua('OtherAddonDB = "line\\', *EMPTY_LINES, '"')
INDENTED_NAME = _lua("\t" + EMPTY_LINES[0], *EMPTY_LINES[1:])
CONCATENATED = b"\r\nOtherAddonDB = 1 " + EMPTY.lstrip(b"\r\n")

REFUSED = {
    # The acceptance list of M19-04.
    "with players": (WITH_PLAYERS, lab_capture.CREW_BOOK_NOT_EMPTY_LABEL),
    "with only current": (ONLY_CURRENT, lab_capture.CREW_BOOK_NOT_EMPTY_LABEL),
    "inside a string": (IN_STRING, lab_capture.CREW_BOOK_IN_TEXT_LABEL),
    "inside a long-bracket string": (IN_LONG_STRING, lab_capture.CREW_BOOK_IN_TEXT_LABEL),
    "filled inside a long-bracket string": (
        IN_LONG_STRING_FILLED,
        lab_capture.CREW_BOOK_IN_TEXT_LABEL,
    ),
    "a second assignment": (SECOND_ASSIGNMENT, lab_capture.CREW_BOOK_TWICE_LABEL),
    "another casing": (OTHER_CASING, lab_capture.CREW_BOOK_CASING_LABEL),
    # More shapes a crafted file could take.
    "lower casing": (LOWER_CASING, lab_capture.CREW_BOOK_CASING_LABEL),
    "inside a long comment": (IN_COMMENT, lab_capture.CREW_BOOK_IN_TEXT_LABEL),
    "two empty books": (TWO_EMPTY, lab_capture.CREW_BOOK_TWICE_LABEL),
    "nested in another table": (NESTED, lab_capture.CREW_BOOK_NOT_TOP_LABEL),
    "a string value": (STRING_VALUE, lab_capture.CREW_BOOK_NOT_EMPTY_LABEL),
    "an extra key": (EXTRA_KEY, lab_capture.CREW_BOOK_NOT_EMPTY_LABEL),
    "a repeated key": (REPEATED_KEY, lab_capture.CREW_BOOK_NOT_EMPTY_LABEL),
    "an empty table on one line": (ONE_LINE_PLAYERS, lab_capture.CREW_BOOK_NOT_EMPTY_LABEL),
    "an extra key in probe": (PROBE_EXTRA, lab_capture.CREW_BOOK_NOT_EMPTY_LABEL),
    "a positional entry": (POSITIONAL, lab_capture.CREW_BOOK_NOT_EMPTY_LABEL),
    "lost twice in probe": (PROBE_LOST_TWICE, lab_capture.CREW_BOOK_NOT_EMPTY_LABEL),
    "lost as a string": (PROBE_LOST_STRING, lab_capture.CREW_BOOK_NOT_EMPTY_LABEL),
    "lost outside probe": (LOST_AT_TOP, lab_capture.CREW_BOOK_NOT_EMPTY_LABEL),
    "escaped decimal": (ESCAPED_DECIMAL, lab_capture.CREW_BOOK_GLOBAL_WRITE_LABEL),
    "escaped hex": (ESCAPED_HEX, lab_capture.CREW_BOOK_GLOBAL_WRITE_LABEL),
    "escaped unicode": (ESCAPED_UNICODE, lab_capture.CREW_BOOK_GLOBAL_WRITE_LABEL),
    "concatenated": (SPLIT, lab_capture.CREW_BOOK_GLOBAL_WRITE_LABEL),
    "concatenated in three": (SPLIT_THREE, lab_capture.CREW_BOOK_GLOBAL_WRITE_LABEL),
    "escaped C, no CrewDB left": (ESCAPED_C, lab_capture.CREW_BOOK_GLOBAL_WRITE_LABEL),
    "_G with a space": (SPACED_G, lab_capture.CREW_BOOK_GLOBAL_WRITE_LABEL),
    "rawset": (RAWSET, lab_capture.CREW_BOOK_GLOBAL_WRITE_LABEL),
    "setglobal": (SETGLOBAL, lab_capture.CREW_BOOK_GLOBAL_WRITE_LABEL),
    "escaped key of another table": (ESCAPED_KEY, lab_capture.CREW_BOOK_PART_LABEL),
    "CrewDB beside an empty book": (CREWDB_BESIDE, lab_capture.CREW_BOOK_PART_LABEL),
    **{case: (data, lab_capture.CREW_BOOK_UTF16_LABEL) for case, data in UTF16.items()},
    "a nil line twice": (NIL_TWICE, lab_capture.CREW_BOOK_TWICE_LABEL),
    "a nil line indented": (NIL_INDENTED, lab_capture.CREW_BOOK_NOT_TOP_LABEL),
    "a nil line in another casing": (NIL_OTHER_CASING, lab_capture.CREW_BOOK_CASING_LABEL),
    "a nil line with a comment": (NIL_THEN_MORE, lab_capture.CREW_BOOK_NOT_EMPTY_LABEL),
    "a nil line with more": (NIL_THEN_TABLE, lab_capture.CREW_BOOK_NOT_EMPTY_LABEL),
    "after an escaped quote": (ESCAPED_QUOTE_UNCLOSED, lab_capture.CREW_BOOK_IN_TEXT_LABEL),
    "an unclosed book": (UNCLOSED, lab_capture.CREW_BOOK_NOT_EMPTY_LABEL),
    "after an unclosed string": (AFTER_UNCLOSED_STRING, lab_capture.CREW_BOOK_IN_TEXT_LABEL),
    "inside a continued string": (CONTINUED_STRING, lab_capture.CREW_BOOK_IN_TEXT_LABEL),
    "an indented name": (INDENTED_NAME, lab_capture.CREW_BOOK_NOT_TOP_LABEL),
    "after another statement on its line": (
        CONCATENATED,
        lab_capture.CREW_BOOK_NOT_TOP_LABEL,
    ),
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_constructed_crew_book_refusals(case: str) -> None:
    data, label = REFUSED[case]
    assert lab_capture.crew_book_problems(data) == [label]


ACCEPTED = {
    "the client's layout": EMPTY,
    "line feeds": _lua(*EMPTY_LINES, eol="\n"),
    "another key order": _lua(
        "WowLabCrewDB = {",
        '["current"] = {',
        "},",
        '["skipped"] = 0,',
        '["probe"] = {',
        '["loads"] = 12,',
        "},",
        '["players"] = {',
        "},",
        '["started"] = 1794950000,',
        '["on"] = true,',
        '["schema"] = 1,',
        "}",
    ),
    "probe without loads": _lua(
        *(line for line in EMPTY_LINES if line != '["loads"] = 12,'),
    ),
    "off, with fewer keys": _lua(
        "WowLabCrewDB = {", '["schema"] = 1,', '["on"] = false,', '["players"] = {', "},", "}"
    ),
    "tab-indented": _lua(EMPTY_LINES[0], *("\t" + line for line in EMPTY_LINES[1:-1]), "}"),
    "beside another variable": _lua("OtherAddonDB = {", '["schema"] = 1,', "}", *EMPTY_LINES),
    "no trailing line break": EMPTY.rstrip(b"\r\n"),
    "probe with lost": _lua(*_with(EMPTY_LINES, '["loads"] = 12,', ('["lost"] = true,',))),
    "probe with lost first": _lua(*_with(EMPTY_LINES, '["probe"] = {', ('["lost"] = true,',))),
    "a nil line": NIL_BOOK,
    "a nil line with line feeds": _lua("WowLabCrewDB = nil", eol="\n"),
    "a nil line at the end of the file": NIL_BOOK.rstrip(b"\r\n"),
    "a nil line beside another variable": _lua("OtherAddonDB = nil", "WowLabCrewDB = nil"),
    "global-write words in strings and comments": WRITE_WORDS_IN_TEXT + EMPTY,
}


@pytest.mark.parametrize("case", sorted(ACCEPTED))
def test_constructed_empty_crew_book_is_accepted(case: str) -> None:
    assert lab_capture.crew_book_problems(ACCEPTED[case]) == []


def test_constructed_a_file_without_the_name_is_untouched() -> None:
    assert lab_capture.crew_book_problems(_lua("WowLabDB = {", '["schema"] = 4,', "}")) == []


def test_constructed_the_check_is_linear_on_hostile_input() -> None:
    # Many unclosed openers before the name: each byte is read a bounded number of times.
    data = b'"\n' * 200_000 + b"--[[" * 50_000 + EMPTY
    assert lab_capture.crew_book_problems(data) == [lab_capture.CREW_BOOK_IN_TEXT_LABEL]


def test_constructed_there_is_no_flag_that_empties_or_skips() -> None:
    parser_flags = lab_capture.parse_args(["--root", ".", "--out", "x"])
    names = " ".join(vars(parser_flags)).casefold()
    assert "crew" not in names and "empty" not in names


# ─── end to end, through the capture run ─────────────────────────────────────


@pytest.fixture
def install(tmp_path: Path) -> Path:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    return root


def _saved(install: Path, flavor: str = "_retail_") -> Path:
    return install / flavor / "WTF" / "Account" / ACCOUNT / "SavedVariables"


def _assert_no_key(printed: str) -> None:
    for secret in (KEY_A, KEY_B, GUILD, NOTE, "Inventeda", "Fakerealm"):
        assert secret not in printed


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("WowLab.lua", WITH_PLAYERS),
        ("WowLab.lua", ONLY_CURRENT),
        ("WowLab.lua.bak", WITH_PLAYERS),
        ("NotTheAddon.lua", IN_STRING),  # by content, whatever the file is called
        ("Renamed.lua.bak", IN_LONG_STRING_FILLED),
        ("WowLab.lua", SECOND_ASSIGNMENT),
        ("WowLab.lua", OTHER_CASING),
    ],
)
def test_constructed_sv_of_a_crew_book_is_refused(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str, data: bytes
) -> None:
    (_saved(install) / name).write_bytes(data)
    out = tmp_path / "incoming"
    before = tree_state(install)
    assert capture(install, out, "--sv", name) == 1
    written = outputs(out)
    assert not [p for p in written if p.endswith(f"/SavedVariables/{name}")]
    assert all(b"wowlabcrewdb" not in content.lower() for content in written.values())
    captured = capsys.readouterr()
    printed = captured.out + captured.err
    refusal = [line for line in printed.splitlines() if line.startswith("REFUSED")]
    assert len(refusal) == 1
    assert refusal[0].startswith("REFUSED  macos/")
    assert f"/SavedVariables/{name}: crew book (WowLabCrewDB)" in refusal[0]
    _assert_no_key(printed)
    assert tree_state(install) == before


def test_constructed_automatic_selection_passes_a_crew_book_over(
    install: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for flavor in ("_retail_", "_classic_beta_"):
        # The smallest file and a .lua.bak: what the automatic pick would take first.
        (_saved(install, flavor) / "A.lua").write_bytes(ONLY_CURRENT)
        (_saved(install, flavor) / "A.lua.bak").write_bytes(WITH_PLAYERS)
    out = tmp_path / "incoming"
    capture(install, out)
    written = outputs(out)
    assert all(b"wowlabcrewdb" not in content.lower() for content in written.values())
    captured = capsys.readouterr()
    _assert_no_key(captured.out + captured.err)


def test_constructed_sv_of_the_empty_shape_is_captured_unchanged(
    install: Path, tmp_path: Path
) -> None:
    for flavor in ("_retail_", "_classic_beta_"):
        (_saved(install, flavor) / "WowLab.lua").write_bytes(EMPTY)
    out = tmp_path / "incoming"
    assert capture(install, out, "--sv", "WowLab.lua") == 0
    captured = [c for p, c in outputs(out).items() if p.endswith("/SavedVariables/WowLab.lua")]
    assert captured == [EMPTY, EMPTY]
