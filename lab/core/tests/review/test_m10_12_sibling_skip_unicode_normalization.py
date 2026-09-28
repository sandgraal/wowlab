# Probe from review of m10/12-luadata-serializer (fix round 1); pins that an
# NFC spelling of an NFD file name still excludes it as target or lab_written.
"""CONSTRUCTED sibling tree in `tmp_path` (L8); no install is touched.

Requested by the conductor at fix round 1 (51fd0ba); it passes there.
`docs/LAB_PLAN.md` §6.4, amendment of 2026-09-27, items 3, 4 and 8: never
the target, never a file in `lab_written`, matched as the same file
(`st_dev`, `st_ino`) whatever the spelling. macOS keeps the spelling a name
was created with but finds it under either Unicode normalization, so
`Zoë.lua` created decomposed (NFD) and named composed (NFC) by a caller is
one file. A path comparison would miss it.

The only sibling on disk is the NFD `Zoë.lua`, an LF, tab-indented,
`-- [n]` document. Excluded, the fallback `FOREVER` is written; read, it
gives `REFERENCE` (the positive control). Skipped where the filesystem
tells the two spellings apart.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from wowlab_core.luadata import (
    Assignment,
    Entry,
    KeyStyle,
    LuaDocument,
    LuaNumber,
    LuaTable,
    serialize,
)

pytestmark = pytest.mark.parser

NEWEST = b'\nV = {\n\t"a", -- [1]\n}\n'
FOREVER = b"\r\nN = {\r\n1,\r\n}\r\n"
REFERENCE = b"\nN = {\n\t1, -- [1]\n}\n"
NFD = unicodedata.normalize("NFD", "Zoë.lua")
NFC = unicodedata.normalize("NFC", "Zoë.lua")

ONE = Entry(
    None, KeyStyle.POSITIONAL, None, None, None, LuaNumber(None, "1"), None, None, None, False
)
NEW = LuaDocument((Assignment(None, "N", None, LuaTable(None, (ONE,), None)),), None)


def _tree(tmp_path: Path) -> Path:
    assert NFD != NFC
    folder = tmp_path / "_x_" / "WTF" / "Account" / "A1" / "SavedVariables"
    folder.mkdir(parents=True)
    try:
        (folder / NFD).write_bytes(NEWEST)
    except (OSError, UnicodeEncodeError):
        pytest.skip("filesystem cannot hold the decomposed name")
    if not (folder / NFC).exists():
        pytest.skip("normalization-sensitive filesystem: NFC and NFD are two paths")
    return folder


def test_positive_control_the_nfd_sibling_decides(tmp_path: Path) -> None:
    folder = _tree(tmp_path)
    assert serialize(NEW, target=folder / "Target.lua") == REFERENCE


def test_nfc_target_is_not_its_own_sibling(tmp_path: Path) -> None:
    folder = _tree(tmp_path)
    assert serialize(NEW, target=folder / NFC) == FOREVER


def test_nfd_target_is_not_its_own_sibling(tmp_path: Path) -> None:
    folder = _tree(tmp_path)
    assert serialize(NEW, target=folder / NFD) == FOREVER


def test_nfc_lab_written_is_excluded(tmp_path: Path) -> None:
    folder = _tree(tmp_path)
    assert serialize(NEW, target=folder / "Target.lua", lab_written=[folder / NFC]) == FOREVER


def test_nfc_lab_written_as_str_is_excluded(tmp_path: Path) -> None:
    folder = _tree(tmp_path)
    out = serialize(NEW, target=folder / "Target.lua", lab_written=[str(folder / NFC)])
    assert out == FOREVER
