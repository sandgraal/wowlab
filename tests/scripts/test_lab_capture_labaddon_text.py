"""`scripts/lab_capture.py`: the lab-addon's schema-5 text and times (M20-13).

Since schema 5 (ADR-0026, note of 2026-11-20) `WowLabCharDB` keeps every
macro's name and body, every binding command (`MACRO <name>`,
`CLICK <frame>:<button>`), the character's guild and the time of each gather
and write. The repository is public, so the capture tool replaces the typed
text with invented text of the same shape and moves the times, byte-level,
and refuses a file that still holds one. The own GUID is the identity
scrub's, as for every file.

CONSTRUCTED INPUT (L8: hostile-input and boundary tests). Every file below is
written for the test in the shape the lab-addon writes (lab/addon/README.md,
"Schema 5"): invented macros, names, guilds, GUIDs and times, none taken
from a capture. No test needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
from test_lab_capture import lab_capture

Identity = lab_capture.Identity

GUID = b"Player-1234-0A1B2C3D"
WRITTEN_AT = b"1763600000"
AT = b"1763599000"


def _record(
    body: bytes = b'#showtooltip\\n/cast Invented Spell\\n/say \\"Hi\\" \\195\\169',
    eol: bytes = b"\r\n",
) -> bytes:
    lines = [
        b"",
        b"WowLabCharDB = {",
        b'["schema"] = 5,',
        b'["written_at"] = ' + WRITTEN_AT + b",",
        b'["written_by"] = "logout",',
        b'["gathered"] = {',
        b"{",
        b'["section"] = "macros",',
        b'["at"] = ' + AT + b",",
        b"}, -- [1]",
        b"},",
        b'["identity"] = {',
        b'["guid"] = "' + GUID + b'",',
        b'["name"] = "Orlavin",',
        b'["realm"] = "Kestrel",',
        b'["guild"] = "Invented Guild Name",',
        b"},",
        b'["bindings"] = {',
        b'["list"] = {',
        b"{",
        b'["command"] = "MOVEFORWARD",',
        b'["keys"] = {',
        b'"W", -- [1]',
        b"},",
        b"}, -- [1]",
        b"{",
        b'["command"] = "MACRO My Secret Macro",',
        b"}, -- [2]",
        b"{",
        b'["command"] = "CLICK GSE_Typed1:LeftButton",',
        b"}, -- [3]",
        b"{",
        b'["command"] = "CLICK SomeFrame:typedword",',
        b"}, -- [4]",
        b"{",
        b'["command"] = "SPELL Invented Spell",',
        b"}, -- [5]",
        b"},",
        b"},",
        b'["macros"] = {',
        b'["list"] = {',
        b"{",
        b'["index"] = 1,',
        b'["scope"] = "account",',
        b'["name"] = "Mac{ro} 1",',
        b'["body"] = "' + body + b'",',
        b"}, -- [1]",
        b"},",
        b"},",
        b"}",
        b"WowLabDB = {",
        b'["schema"] = 5,',
        b"}",
        b"",
    ]
    return eol.join(lines)


def _process(data: bytes, tmp_path: Path, shift: int | None = 86400 * 100) -> object:
    folder = tmp_path / "in"
    folder.mkdir(exist_ok=True)
    name = "WowLab.lua"
    source = folder / name
    source.write_bytes(data)
    rel = PurePosixPath(name)
    item = lab_capture.Item(source, rel, "", "", lab_capture.kind_of(name))
    identity = Identity(guids=[GUID])
    time_shift = lab_capture.TimeShift(shift) if shift is not None else None
    return lab_capture.process(item, identity, shift=time_shift)


def test_constructed_every_typed_value_is_replaced_and_nothing_else(tmp_path: Path) -> None:
    original = _record()
    outcome = _process(original, tmp_path)
    assert list(outcome.problems) == []  # type: ignore[attr-defined]
    data = outcome.result.data  # type: ignore[attr-defined]
    for typed in (
        b"Invented Guild Name",
        b"My Secret Macro",
        b"GSE_Typed1",
        b"typedword",
        b"Mac{ro} 1",
        b"Invented Spell\\n/say",
        b"\\195\\169",
        GUID,
    ):
        assert typed not in data, typed
    assert b'["guild"] = "Xxxxxxxx Xxxxx Xxxx",' in data
    assert b'["command"] = "MACRO Xx Xxxxxx Xxxxx",' in data
    assert b'["command"] = "CLICK XXX_Xxxxx0:LeftButton",' in data  # a mouse button is kept
    assert b'["command"] = "CLICK XxxxXxxxx:xxxxxxxxx",' in data
    assert b'["command"] = "SPELL Invented Spell",' in data  # a spell's name is the game's
    assert b'["command"] = "MOVEFORWARD",' in data
    assert b'["name"] = "Xxx{xx} 0",' in data  # the macro's name
    assert b'["body"] = "#xxxxxxxxxxx\\n/xxxx Xxxxxxxx Xxxxx\\n/xxx \\"Xx\\" xx",' in data
    assert b'["name"] = "Orlavin",' in data and b'["realm"] = "Kestrel",' in data  # public
    assert b"Player-9999-00000001" in data  # the own GUID, as for every file
    # Every byte outside an edit is copied through.
    edits = outcome.result.edits  # type: ignore[attr-defined]
    rebuilt, cursor = bytearray(), 0
    for edit in edits:
        assert original[edit.offset : edit.end] == edit.old
        rebuilt += original[cursor : edit.offset] + edit.new
        cursor = edit.end
    assert bytes(rebuilt + original[cursor:]) == data


def test_constructed_times_move_by_the_offset(tmp_path: Path) -> None:
    outcome = _process(_record(), tmp_path, shift=-86400 * 90)
    data = outcome.result.data  # type: ignore[attr-defined]
    assert b'["written_at"] = %d,' % (int(WRITTEN_AT) - 86400 * 90) in data
    assert b'["at"] = %d,' % (int(AT) - 86400 * 90) in data
    assert WRITTEN_AT not in data and AT not in data
    row = lab_capture.provenance_row(outcome, "macos", "owner", "owner")  # type: ignore[arg-type]
    assert "labaddon-times-shifted: 2" in row
    assert "labaddon-text-replaced: 6" in row
    assert str(-86400 * 90) not in row and "Invented Guild" not in row


def test_constructed_without_an_offset_the_times_refuse() -> None:
    scan = lab_capture.labaddon_scan(_record(), None)
    assert scan is not None and scan.unshifted == 2


def test_constructed_a_file_gets_its_own_offset_when_the_run_has_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lab_capture, "draw_labaddon_shift", lambda: 86400 * 70)
    outcome = _process(_record(), tmp_path, shift=None)
    assert list(outcome.problems) == []  # type: ignore[attr-defined]
    data = outcome.result.data  # type: ignore[attr-defined]
    assert b'["written_at"] = %d,' % (int(WRITTEN_AT) + 86400 * 70) in data


def test_constructed_lf_and_no_macros_table(tmp_path: Path) -> None:
    original = _record(eol=b"\n").replace(b'["macros"]', b'["other"]')
    outcome = _process(original, tmp_path)
    assert list(outcome.problems) == []  # type: ignore[attr-defined]
    assert b'["name"] = "Mac{ro} 1",' in outcome.result.data  # type: ignore[attr-defined]


def test_constructed_unterminated_table_refuses(tmp_path: Path) -> None:
    original = _record().rsplit(b"}\r\nWowLabDB", 1)[0].replace(b'"macros"] = {', b'"macros"] = {{')
    outcome = _process(original, tmp_path)
    problems = list(outcome.problems)  # type: ignore[attr-defined]
    assert f"{lab_capture.LABADDON_TABLE_LABEL} x1" in problems


def test_constructed_a_value_left_in_place_refuses() -> None:
    """The gate holds on what leaves: a typed value in the scrubbed bytes
    that is not its own invented form refuses, whatever the scrub did."""
    problems = lab_capture.labaddon_unreplaced(_record(), ())
    assert f"{lab_capture.LABADDON_TEXT_LABEL} x6" in problems
    assert f"{lab_capture.LABADDON_TIME_LABEL} x2" in problems


@pytest.mark.parametrize(
    "escaped",
    [b"abc XYZ 123", b'\\n\\t\\"\\\\', b"\\200\\201", b"\xc3\xa9t\xc3\xa9", b"|cff00ff00x|r", b""],
    ids=[
        "constructed-ascii",
        "constructed-escapes",
        "constructed-decimal-escapes",
        "constructed-utf8",
        "constructed-colour-code",
        "constructed-empty",
    ],
)
def test_constructed_invented_text_is_a_fixed_point(escaped: bytes) -> None:
    once = lab_capture.invented_lua_text(escaped)
    assert lab_capture.invented_lua_text(once) == once
    assert all(c in b"xX0" or c < 0x80 for c in once)


def test_constructed_a_file_without_the_variable_is_untouched(tmp_path: Path) -> None:
    original = b'OtherAddonDB = {\r\n["macros"] = {\r\n["name"] = "Kept",\r\n},\r\n}\r\n'
    outcome = _process(original, tmp_path)
    assert outcome.result.data == original  # type: ignore[attr-defined]
    assert list(outcome.problems) == []  # type: ignore[attr-defined]
