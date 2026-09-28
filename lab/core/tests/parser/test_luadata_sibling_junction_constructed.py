"""Graders for directory junctions during sibling listing (M10-18T,
follow-up 2). Windows only.

Written before M10-18 from the ticket text (docs/BACKLOG.md, M10-18T, the
owner-approved follow-ups of 2026-09-28 from the M10-12 reviews) and
`docs/LAB_PLAN.md` §6.4, amendment of 2026-09-27, item 3, with the
`serialize` docstring as merged in M10-12: below `WTF/Account`, a folder
that is a link is not read. On Windows a directory junction is made with
`_winapi.CreateJunction` and needs no privilege, so unlike a symlink it can
be graded on every Windows machine and on the CI runner. CPython reports a
junction as a directory, and not as a symlink, through
`os.DirEntry.is_dir(follow_symlinks=False)` and `is_symlink()`; only
`is_junction()` (3.12) or the reparse tag tells it apart.

A junction at each folder level the detection lists (a per-account folder,
an account's `SavedVariables`, a realm (or `<digits>`) folder, a character
folder, a character's `SavedVariables`) points at a real folder holding the
newest sibling, tab-indented with `-- [n]` (`REFERENCE`), in the shape the
listing expects below that level. The junction's target is outside the
install, or inside the same `WTF/Account` where no sibling pattern reaches
it (under account `A`'s `SavedVariables/Stash`). Account `A`'s
`SavedVariables/Old.lua`, older, is LF, no indentation, `;` (`OLDER`). The
junction is never walked into (no `os.scandir` at or below it or its
target) and nothing below it is read (no `os.open` or `open`), so `OLDER`
decides. The positive control puts a real folder with the same content
where the junction was, and its newest sibling decides.

Every tree is CONSTRUCTED under `tmp_path` (L8; the file name puts it in
every test id). No install is touched. Elsewhere than Windows the whole
file is skipped with a reason.

These tests carry no xfail marker. M10-12's listing already refuses an
entry for which `DirEntry.is_junction()` is true, so on Windows they are
expected to pass today; a strict xfail would turn the `lab (windows)` job
red. They pin the behaviour for M10-18 and later changes.
"""

from __future__ import annotations

import builtins
import io
import os
import sys
from pathlib import Path
from typing import Any

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

pytestmark = [
    pytest.mark.parser,
    pytest.mark.skipif(
        sys.platform != "win32",
        reason="directory junctions exist only on Windows (_winapi.CreateJunction)",
    ),
]

NEWEST = b'\nV = {\n\t"a", -- [1]\n}\n'  # LF, leading empty line, `,`, tab, `-- [n]`
OLD = b'\nV = {\n"a";\n}\n'  # LF, leading empty line, `;`, no indentation or comment

REFERENCE = b"\nN = {\n\t1, -- [1]\n}\n"  # the newest sibling decided
OLDER = b"\nN = {\n1;\n}\n"  # the newest was never read; `Old.lua` decided

ONE = Entry(
    None, KeyStyle.POSITIONAL, None, None, None, LuaNumber(None, "1"), None, None, None, False
)
NEW = LuaDocument((Assignment(None, "N", None, LuaTable(None, (ONE,), None)),), None)

T_OLD = 10**18
T_NEW = 2 * 10**18

# level id -> (the linked folder, relative to `WTF/Account`; the newest
# siblings, relative to that folder, in the shape the listing expects there)
LEVELS: dict[str, tuple[str, tuple[str, ...]]] = {
    "per-account-folder": ("J", ("SavedVariables.lua", "SavedVariables/New.lua")),
    "account-savedvariables": ("B/SavedVariables", ("New.lua",)),
    "realm-folder": ("B/Realm", ("Char/SavedVariables/New.lua",)),
    "character-folder": ("B/Realm/Char", ("SavedVariables/New.lua",)),
    "character-savedvariables": ("B/Realm/Char/SavedVariables", ("New.lua",)),
}
WHERE = ["outside-the-install", "inside-wtf-account"]


def _key(path: Any) -> str:
    """The path made absolute and normalised as text; links not followed."""
    return os.path.normcase(os.path.normpath(Path(os.fspath(path)).absolute()))


def _under(path: Any, folders: list[str]) -> bool:
    key = _key(path)
    return any(
        key == folder or key.startswith(folder.rstrip(os.sep) + os.sep) for folder in folders
    )


def _junction(link: Path, target: Path) -> None:
    import _winapi  # Windows only; this file is skipped elsewhere

    _winapi.CreateJunction(str(target), str(link))


def _tree(tmp_path: Path, level: str, where: str) -> tuple[Path, Path, Path]:
    """(target, the linked folder's path, the real folder with the newest
    siblings); nothing is at the linked path yet."""
    account = tmp_path / "_x_" / "WTF" / "Account"
    old = account / "A" / "SavedVariables" / "Old.lua"
    old.parent.mkdir(parents=True)
    old.write_bytes(OLD)
    os.utime(old, ns=(T_OLD, T_OLD))
    linked_rel, newest = LEVELS[level]
    if where == "outside-the-install":
        real = tmp_path / "outside" / "Linked"
    else:
        real = account / "A" / "SavedVariables" / "Stash" / "Linked"
    for rel in newest:
        path = real / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(NEWEST)
        os.utime(path, ns=(T_NEW, T_NEW))
    linked = account / linked_rel
    linked.parent.mkdir(parents=True, exist_ok=True)
    return account / "A" / "SavedVariables" / "Target.lua", linked, real


class _Spy:
    """Records every path `os.scandir`, `os.open` and `open` are given."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.listed: list[str] = []
        self.opened: list[str] = []
        real_scandir, real_os_open, real_open = os.scandir, os.open, io.open

        def scandir(path: Any = ".") -> Any:
            if not isinstance(path, int):
                self.listed.append(_key(path))
            return real_scandir(path)

        def os_open(path: Any, *args: Any, **kwargs: Any) -> int:
            if not isinstance(path, int):
                self.opened.append(_key(path))
            return real_os_open(path, *args, **kwargs)

        def open_(file: Any, *args: Any, **kwargs: Any) -> Any:
            if not isinstance(file, int):
                self.opened.append(_key(file))
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(os, "scandir", scandir)
        monkeypatch.setattr(os, "open", os_open)
        monkeypatch.setattr(io, "open", open_)
        monkeypatch.setattr(builtins, "open", open_)


@pytest.mark.parametrize("where", WHERE)
@pytest.mark.parametrize("level", list(LEVELS))
def test_positive_control_a_real_folder_in_its_place_decides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, level: str, where: str
) -> None:
    """The same content in a real folder at the linked path is listed and
    read (the spy sees it), and its newest sibling decides."""
    target, linked, real = _tree(tmp_path, level, where)
    real.rename(linked)
    spy = _Spy(monkeypatch)
    assert serialize(NEW, target=target) == REFERENCE
    assert any(_under(path, [_key(linked)]) for path in spy.opened)


@pytest.mark.parametrize("where", WHERE)
@pytest.mark.parametrize("level", list(LEVELS))
def test_a_junction_under_wtf_account_is_never_walked_or_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, level: str, where: str
) -> None:
    """A junction at the linked path: `OLDER` decides, no folder at or below
    the junction or its target is listed, and no file there is opened."""
    target, linked, real = _tree(tmp_path, level, where)
    _junction(linked, real)
    assert linked.is_junction() and not linked.is_symlink()  # the case DirEntry.is_dir misses
    spy = _Spy(monkeypatch)
    out = serialize(NEW, target=target)
    inside = [_key(linked), _key(real)]
    assert [path for path in spy.listed if _under(path, inside)] == []
    assert [path for path in spy.opened if _under(path, inside)] == []
    assert out == OLDER
