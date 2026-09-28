# Probe from review of m10/12-luadata-serializer (fix round 1); pins that a
# sibling that is a file symlink is never read for style.
"""CONSTRUCTED sibling tree in `tmp_path` (L8); no install is touched.

Requested by the conductor at fix round 1 (51fd0ba); it passes there.
`docs/LAB_PLAN.md` §6.4, amendment of 2026-09-27, item 3: never a file in
another flavor folder or outside `WTF/Account`; the `serialize` docstring:
links are not followed. The client never writes a link, so a
`SavedVariables/*.lua` that is one is not a client-written sibling, wherever
it points: into another flavor folder, outside the install, or to a file
inside the same `WTF/Account` whose own name is not a sibling name.

Every link target here is an LF, tab-indented, `-- [n]` document. Not read,
the fallback `FOREVER` is written. Positive control: the same bytes as a
regular sibling decide (`REFERENCE`). Skipped where links cannot be made.
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


def _folder(tmp_path: Path) -> Path:
    folder = tmp_path / "_x_" / "WTF" / "Account" / "A1" / "SavedVariables"
    folder.mkdir(parents=True)
    return folder


def _link(link: Path, to: Path) -> None:
    try:
        link.symlink_to(to)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create a file symlink here")


def test_positive_control_a_regular_sibling_decides(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    (folder / "Real.lua").write_bytes(NEWEST)
    assert serialize(NEW, target=folder / "Target.lua") == REFERENCE


def test_link_outside_the_install_is_not_read(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    outside = tmp_path / "outside.lua"
    outside.write_bytes(NEWEST)
    _link(folder / "Link.lua", outside)
    assert serialize(NEW, target=folder / "Target.lua") == FOREVER


def test_link_into_another_flavor_folder_is_not_read(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    other = tmp_path / "_y_" / "WTF" / "Account" / "B1" / "SavedVariables" / "Other.lua"
    other.parent.mkdir(parents=True)
    other.write_bytes(NEWEST)
    _link(folder / "Link.lua", other)
    assert serialize(NEW, target=folder / "Target.lua") == FOREVER


def test_link_inside_the_same_account_folder_is_not_read(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    inside = folder.parent / "not-a-sibling.lua"
    inside.write_bytes(NEWEST)
    _link(folder / "Link.lua", inside)
    assert serialize(NEW, target=folder / "Target.lua") == FOREVER


def test_link_named_savedvariables_lua_is_not_read(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    outside = tmp_path / "outside.lua"
    outside.write_bytes(NEWEST)
    _link(folder.parent / "SavedVariables.lua", outside)
    assert serialize(NEW, target=folder / "Target.lua") == FOREVER
