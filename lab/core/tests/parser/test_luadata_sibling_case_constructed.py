"""Graders for how a missing target or `lab_written` path is compared with
siblings (M10-18T, follow-up 3).

Written before M10-18 from the ticket text (docs/BACKLOG.md, M10-18T, the
owner-approved follow-ups of 2026-09-28 from the M10-12 reviews) and
`docs/LAB_PLAN.md` §6.4, amendment of 2026-09-27, items 3, 4 and 8: the
target and the `lab_written` paths are never siblings. An existing one is
matched as the same file (`st_dev`, `st_ino`); a path that does not exist is
compared after `Path.resolve()`, and case-insensitively only where the
file system is case-insensitive. On a case-sensitive volume (ext4 on the
Linux CI runner, a case-sensitive APFS volume) `a.lua` and `A.lua` are two
files, so a missing target or `lab_written` path `a.lua` does not exclude
a separate sibling `A.lua`. Case-folding there can only lose a style source.

Each tree is CONSTRUCTED under `tmp_path` (L8; the file name puts it in
every test id). No install is touched. `A1/SavedVariables/A.lua` is the
newest sibling, tab-indented with `-- [n]` (`REFERENCE`); `Old.lua`, older,
is LF, no indentation, `;` (`OLDER`). The volume's case sensitivity is
detected in `tmp_path` by writing `CaseProbe.tmp` and looking for
`caseprobe.tmp`. The graders need a case-sensitive volume and are skipped
elsewhere (macOS and Windows defaults), with a reason.

Graders that fail today carry one marker line M10-18 deletes. The unmarked
tests are positive controls that pass today and must keep passing: the tree
is live; a missing path that resolves to a sibling in the same spelling
excludes it everywhere; and on a case-insensitive volume a missing path in
another case still excludes it. Nothing else in this file is the
implementer's to change.
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

NEWEST = b'\nV = {\n\t"a", -- [1]\n}\n'  # LF, leading empty line, `,`, tab, `-- [n]`
OLD = b'\nV = {\n"a";\n}\n'  # LF, leading empty line, `;`, no indentation or comment

REFERENCE = b"\nN = {\n\t1, -- [1]\n}\n"  # `A.lua` decided
OLDER = b"\nN = {\n1;\n}\n"  # `A.lua` was excluded; `Old.lua` decided

ONE = Entry(
    None, KeyStyle.POSITIONAL, None, None, None, LuaNumber(None, "1"), None, None, None, False
)
NEW = LuaDocument((Assignment(None, "N", None, LuaTable(None, (ONE,), None)),), None)

T_OLD = 10**18
T_NEW = 2 * 10**18


def _case_sensitive(folder: Path) -> bool:
    probe = folder / "CaseProbe.tmp"
    probe.write_bytes(b"")
    try:
        return not (folder / "caseprobe.tmp").exists()
    finally:
        probe.unlink()


def _tree(tmp_path: Path) -> Path:
    """The `SavedVariables` folder holding `Old.lua` and the newest `A.lua`."""
    folder = tmp_path / "_x_" / "WTF" / "Account" / "A1" / "SavedVariables"
    folder.mkdir(parents=True)
    old = folder / "Old.lua"
    old.write_bytes(OLD)
    os.utime(old, ns=(T_OLD, T_OLD))
    newest = folder / "A.lua"
    newest.write_bytes(NEWEST)
    os.utime(newest, ns=(T_NEW, T_NEW))
    return folder


def _sensitive_tree(tmp_path: Path) -> Path:
    if not _case_sensitive(tmp_path):
        pytest.skip("case-insensitive volume: a case variant of A.lua is A.lua itself")
    return _tree(tmp_path)


def _insensitive_tree(tmp_path: Path) -> Path:
    if _case_sensitive(tmp_path):
        pytest.skip("case-sensitive volume: a case variant of A.lua is another path")
    return _tree(tmp_path)


# spelling id -> the missing path, from the `SavedVariables` folder, that
# differs from `A.lua` only in case
SPELLINGS = {
    "file-name-case": lambda folder: folder / "a.lua",
    "folder-name-case": lambda folder: folder.parent / "savedvariables" / "A.lua",
}


def _call(folder: Path, role: str, missing: Path) -> bytes:
    assert not missing.exists()  # a missing path, compared after `Path.resolve()`
    if role == "target":
        return serialize(NEW, target=missing)
    return serialize(NEW, target=folder / "Target.lua", lab_written=[missing])


# ── positive controls ───────────────────────────────────────────────────────


def test_positive_control_the_newest_sibling_decides(tmp_path: Path) -> None:
    """Nothing excluded: `A.lua` decides (the tree is live), on any volume."""
    folder = _tree(tmp_path)
    assert serialize(NEW, target=folder / "Target.lua") == REFERENCE


@pytest.mark.parametrize("role", ["target", "lab-written"])
def test_positive_control_a_missing_path_in_the_same_spelling_excludes(
    tmp_path: Path, role: str
) -> None:
    """`Old.lua` alone decides when a path that does not exist resolves to
    `A.lua` in its own spelling (`SavedVariables/gone/../A.lua`: `gone` is
    missing, so on POSIX the path does not exist and is compared after
    `Path.resolve()`; Windows collapses `..` first and matches the file).
    Holds on every volume."""
    folder = _tree(tmp_path)
    missing = folder / "gone" / ".." / "A.lua"
    if role == "target":
        assert serialize(NEW, target=missing) == OLDER
    else:
        assert serialize(NEW, target=folder / "Target.lua", lab_written=[missing]) == OLDER


@pytest.mark.parametrize("role", ["target", "lab-written"])
def test_positive_control_case_insensitive_volume_excludes_a_case_variant(
    tmp_path: Path, role: str
) -> None:
    """On a case-insensitive volume a missing path that resolves to `a.lua`
    (`SavedVariables/gone/../a.lua`) names `A.lua` and excludes it."""
    folder = _insensitive_tree(tmp_path)
    missing = folder / "gone" / ".." / "a.lua"
    if role == "target":
        assert serialize(NEW, target=missing) == OLDER
    else:
        assert serialize(NEW, target=folder / "Target.lua", lab_written=[missing]) == OLDER


# ── follow-up 3: case-insensitive only where the volume is ──────────────────


@pytest.mark.parametrize("role", ["target", "lab-written"])
@pytest.mark.parametrize("spelling", list(SPELLINGS))
def test_case_sensitive_volume_keeps_a_sibling_that_differs_in_case(
    tmp_path: Path, spelling: str, role: str
) -> None:
    """On a case-sensitive volume a missing target or `lab_written` path
    that differs from `A.lua` only in case (file or folder name) is another
    path, so `A.lua` still counts and decides."""
    folder = _sensitive_tree(tmp_path)
    missing = SPELLINGS[spelling](folder)
    assert _call(folder, role, missing) == REFERENCE  # OLDER today: A.lua case-folded away
