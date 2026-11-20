"""`scripts/lab_capture.py`: player GUIDs in the lab-addon's schema-5 file (M20-13).

Since schema 5 the lab-addon keeps an item link as the client gives it, so a
crafted item's link can carry another player's GUID. The owner's own GUID is
replaced as in every file; another player's gets the invented form a combat
log gives one (`Player-9998-...`), so the capture is not refused. Macro text,
binding commands, guild names and times are kept as captured (ADR-0026,
correction of 2026-11-20).

CONSTRUCTED INPUT (L8: hostile-input and boundary tests). Every file below is
written for the test in the shape the lab-addon writes: invented GUIDs,
names and macros. No test needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from test_lab_capture import lab_capture

OWN = b"Player-1234-0A1B2C3D"
CRAFTER = b"Player-1234-0F0F0F0F"
LINK = b"|cffa335ee|Hitem:12345::::::::60:::::" + CRAFTER + b"::|h[Invented Blade]|h|r"


def _record(*, variable: bytes = b"WowLabCharDB") -> bytes:
    lines = [
        b"",
        variable + b" = {",
        b'["schema"] = 5,',
        b'["written_at"] = 1763600000,',
        b'["identity"] = {',
        b'["guid"] = "' + OWN + b'",',
        b'["guild"] = "Invented Guild",',
        b"},",
        b'["gear"] = {',
        b'["slots"] = {',
        b"{",
        b'["slot"] = 16,',
        b'["link"] = "' + LINK + b'",',
        b"}, -- [1]",
        b"},",
        b"},",
        b'["macros"] = {',
        b'["list"] = {',
        b"{",
        b'["name"] = "Testmacro",',
        b'["body"] = "/cast Invented Spell",',
        b"}, -- [1]",
        b"},",
        b"},",
        b"}",
        b"",
    ]
    return b"\r\n".join(lines)


def _process(data: bytes, tmp_path: Path) -> object:
    folder = tmp_path / "in"
    folder.mkdir(exist_ok=True)
    source = folder / "WowLab.lua"
    source.write_bytes(data)
    item = lab_capture.Item(source, PurePosixPath("WowLab.lua"), "", "", "savedvariables")
    return lab_capture.process(item, lab_capture.Identity(guids=[OWN]))


def test_constructed_crafter_guid_is_invented_and_the_rest_kept(tmp_path: Path) -> None:
    original = _record()
    outcome = _process(original, tmp_path)
    assert list(outcome.problems) == []  # type: ignore[attr-defined]
    data = outcome.result.data  # type: ignore[attr-defined]
    assert OWN not in data and CRAFTER not in data
    assert b"Player-9999-00000001" in data  # the owner's, as in every file
    assert b"Player-9998-00000001" in data  # the crafter's, invented
    expected = original.replace(OWN, b"Player-9999-00000001").replace(
        CRAFTER, b"Player-9998-00000001"
    )
    assert data == expected  # macro text, the guild and the time kept as captured


def test_constructed_crafter_guid_outside_the_addon_file_still_refuses(tmp_path: Path) -> None:
    """Control: the same bytes under another variable keep the old rule."""
    outcome = _process(_record(variable=b"OtherAddonDB"), tmp_path)
    assert any("unmapped player GUID" in p for p in outcome.problems)  # type: ignore[attr-defined]
