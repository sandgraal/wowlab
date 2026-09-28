# Probe from review of m10/12-luadata-serializer (fix round 1); pins sibling
# discovery through case-folded `wtf/account/savedvariables` spellings.
"""CONSTRUCTED sibling trees in `tmp_path` (L8); no install is touched.

Requested by the conductor at fix round 1 (51fd0ba); it passes there.
`docs/LAB_PLAN.md` §6.4, amendment of 2026-09-27, items 3 and 8, and the
`serialize` docstring: the flavor folder and the sibling names are matched
with case folded, and the target is never its own sibling, whatever its
spelling (same file by `st_dev`, `st_ino`). On a case-insensitive
filesystem (macOS APFS by default, NTFS) a client or a copy can leave
`wtf/account/.../savedvariables` in lower case on disk.

The newest file on disk is an LF, tab-indented, `-- [n]` document, so a
new table written in its style is `REFERENCE`; with it excluded and nothing
else to read, the fallback is `FOREVER`.
"""

from __future__ import annotations

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

ONE = Entry(
    None, KeyStyle.POSITIONAL, None, None, None, LuaNumber(None, "1"), None, None, None, False
)
NEW = LuaDocument((Assignment(None, "N", None, LuaTable(None, (ONE,), None)),), None)


def _lower_tree(tmp_path: Path) -> Path:
    folder = tmp_path / "_x_" / "wtf" / "account" / "A1" / "savedvariables"
    folder.mkdir(parents=True)
    (folder / "Newest.lua").write_bytes(NEWEST)
    return folder


def _case_insensitive(folder: Path) -> None:
    if not (folder / "NEWEST.LUA").exists():
        pytest.skip("case-sensitive filesystem: a case-variant spelling is another path")


def test_lower_case_folders_on_disk_are_found_with_a_lower_case_target(tmp_path: Path) -> None:
    folder = _lower_tree(tmp_path)
    assert serialize(NEW, target=folder / "Target.lua") == REFERENCE


def test_lower_case_folders_on_disk_are_found_with_the_usual_target_spelling(
    tmp_path: Path,
) -> None:
    folder = _lower_tree(tmp_path)
    _case_insensitive(folder)
    target = tmp_path / "_x_" / "WTF" / "Account" / "A1" / "SavedVariables" / "Target.lua"
    assert serialize(NEW, target=target) == REFERENCE


def test_upper_case_sibling_folder_is_found(tmp_path: Path) -> None:
    folder = tmp_path / "_x_" / "WTF" / "Account" / "A1" / "SAVEDVARIABLES"
    folder.mkdir(parents=True)
    (folder / "Newest.lua").write_bytes(NEWEST)
    target = tmp_path / "_x_" / "WTF" / "Account" / "A1" / "Other.lua"
    assert serialize(NEW, target=target) == REFERENCE


def test_case_variant_target_is_not_its_own_sibling(tmp_path: Path) -> None:
    folder = _lower_tree(tmp_path)
    _case_insensitive(folder)
    target = tmp_path / "_x_" / "WTF" / "ACCOUNT" / "a1" / "SAVEDVARIABLES" / "NEWEST.LUA"
    assert serialize(NEW, target=target) == FOREVER
