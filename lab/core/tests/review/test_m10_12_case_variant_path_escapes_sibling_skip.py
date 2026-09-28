# Probe from review of m10/12-luadata-serializer; reproduces the target, or a
# lab_written file, used as a style sibling when spelled in another case.
"""CONSTRUCTED sibling tree in `tmp_path` (L8); no install is touched.

`docs/LAB_PLAN.md` §6.4, amendment of 2026-09-27: item 3 says style comes
from siblings, "never the target itself"; item 4 excludes "the paths in
`lab_written`"; item 8 compares them after `Path.resolve()`. On a
case-insensitive filesystem (the owner's macOS APFS default; NTFS behaves
the same) `Path.resolve()` keeps the caller's spelling, so a target or a
`lab_written` path spelled in another case than on disk is not recognised
as the same file. That file then decides the style. Comparing file identity
as well (`os.path.samefile`, or `(st_dev, st_ino)`) would close it.

Here the newest file is an LF, tab-indented, `-- [n]` document and an older
sibling is in the Forever layout. Excluding the newest file must give the
Forever layout (CRLF, no indentation, no comment).

Positive controls: the exact spelling is excluded (Forever layout), and an
unexcluded newest file does decide (LF, tab, `-- [1]`), so the tree is live.
Skipped where the filesystem is case-sensitive.
"""

from __future__ import annotations

import os
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
OLDER = b'\r\nV = {\r\n"a",\r\n}\r\n'
FOREVER = b"\r\nN = {\r\n1,\r\n}\r\n"
REFERENCE = b"\nN = {\n\t1, -- [1]\n}\n"

NEW = LuaDocument(
    (
        Assignment(
            None,
            "N",
            None,
            LuaTable(
                None,
                (
                    Entry(
                        None,
                        KeyStyle.POSITIONAL,
                        None,
                        None,
                        None,
                        LuaNumber(None, "1"),
                        None,
                        None,
                        None,
                        False,
                    ),
                ),
                None,
            ),
        ),
    ),
    None,
)


def _tree(tmp_path: Path) -> Path:
    folder = tmp_path / "_x_" / "WTF" / "Account" / "A1" / "SavedVariables"
    folder.mkdir(parents=True)
    probe = folder / "CaseProbe.lua"
    probe.write_bytes(b"")
    if not (folder / "caseprobe.lua").exists():
        pytest.skip("case-sensitive filesystem")
    probe.unlink()
    older = folder / "Older.lua"
    older.write_bytes(OLDER)
    os.utime(older, ns=(10**18, 10**18))
    newest = folder / "Newest.lua"
    newest.write_bytes(NEWEST)
    os.utime(newest, ns=(2 * 10**18, 2 * 10**18))
    return folder


def test_positive_control_newest_sibling_decides(tmp_path: Path) -> None:
    folder = _tree(tmp_path)
    assert serialize(NEW, target=folder / "Target.lua") == REFERENCE


def test_positive_control_exact_spellings_are_excluded(tmp_path: Path) -> None:
    folder = _tree(tmp_path)
    assert serialize(NEW, target=folder / "Newest.lua") == FOREVER
    assert (
        serialize(NEW, target=folder / "Target.lua", lab_written=[folder / "Newest.lua"]) == FOREVER
    )


def test_case_variant_target_is_not_its_own_sibling(tmp_path: Path) -> None:
    folder = _tree(tmp_path)
    assert serialize(NEW, target=folder / "newest.lua") == FOREVER  # REFERENCE on 3259dc7


def test_case_variant_lab_written_is_excluded(tmp_path: Path) -> None:
    folder = _tree(tmp_path)
    out = serialize(NEW, target=folder / "Target.lua", lab_written=[folder / "NEWEST.lua"])
    assert out == FOREVER  # REFERENCE on 3259dc7
