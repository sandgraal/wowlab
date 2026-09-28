# Probe from review of m10/18-luadata-sibling-followups; pins ruling (d): a
# folder skipped by the per-folder cap takes nothing from the scan budget.
"""CONSTRUCTED sibling trees in `tmp_path` (L8); no install is touched.

Requested by the conductor at review of 76cc382; it passes there.
`docs/LAB_PLAN.md` §6.4, amendment of 2026-09-28, item 11 (conductor's ruling
(d) on #86): "A skipped folder takes nothing from item 4's 16,384-entry
budget; only entries of folders that are listed count." No M10-18T grader
covers it: those graders put the only older sibling before the oversized
folder in listing order, so they pass whether or not a skipped folder is
charged to the budget.

Here the oversized folder (65,537 entries) is in account `A`, listed first,
and the only sibling, `Old.lua`, is in account `B`'s character
`SavedVariables`, the last folder the detection lists. If the skipped folder
were charged to the 16,384-entry budget, nothing after it would be listed
and the item 6 fallback would be written; with ruling (d), `Old.lua` decides.

Seam, as in the M10-18T graders: sibling folders are listed with
`os.scandir(path)` looked up on `os` at call time; the patched `scandir`
pads one folder with `pad<n>.txt` entries that report themselves as empty
regular files. No luadata constant is patched.

Positive controls: unpadded, `Old.lua` decides (the tree is live); padded to
exactly 65,536 (listed, not skipped), the folder uses up the budget and the
fallback is written (the budget is live, so the probe tells the two
behaviours apart). Mutant check at review: charging the skipped folder's
entries to the budget before returning turns every probe red.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
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

pytestmark = pytest.mark.parser

CAP = 65_536  # §6.4 item 10: more than this is skipped whole

OLD = b'\nV = {\n"a";\n}\n'  # LF, leading empty line, `;`, no indentation
OLDER = b"\nN = {\n1;\n}\n"  # `Old.lua` decided
FALLBACK = b"\r\nN = {\r\n1,\r\n}\r\n"  # nothing was listed (item 6)

ONE = Entry(
    None, KeyStyle.POSITIONAL, None, None, None, LuaNumber(None, "1"), None, None, None, False
)
NEW = LuaDocument((Assignment(None, "N", None, LuaTable(None, (ONE,), None)),), None)

REAL_SCANDIR = os.scandir

# The oversized folder, relative to `WTF/Account`; each sits in account `A`,
# listed before account `B`.
FOLDERS = {
    "per-account-folder": "A",
    "account-savedvariables": "A/SavedVariables",
    "realm-folder": "A/Realm",
    "character-folder": "A/Realm/Char",
    "character-savedvariables": "A/Realm/Char/SavedVariables",
}


def _key(path: Any) -> str:
    return os.path.normcase(os.path.normpath(Path(os.fspath(path)).absolute()))


class _Pad:
    """A listing-only entry: an empty regular file named `pad<n>.txt`."""

    __slots__ = ("_like", "name", "path")

    def __init__(self, folder: str, name: str, like: os.DirEntry[str]) -> None:
        self.name = name
        self.path = os.fspath(Path(folder) / name)
        self._like = like

    def __fspath__(self) -> str:
        return self.path

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        return False

    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        return True

    def is_symlink(self) -> bool:
        return False

    def is_junction(self) -> bool:
        return False

    def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
        return self._like.stat(follow_symlinks=follow_symlinks)

    def inode(self) -> int:
        return self._like.inode()


class _Listing:
    """A `scandir` iterator over a fixed list."""

    def __init__(self, items: list[Any]) -> None:
        self._items = iter(items)

    def __enter__(self) -> _Listing:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __iter__(self) -> Iterator[Any]:
        return self

    def __next__(self) -> Any:
        return next(self._items)

    def close(self) -> None:
        self._items = iter(())


class _PaddedScandir:
    """`os.scandir`, with `folder` padded to `total` entries."""

    def __init__(self, tmp_path: Path, folder: Path, total: int) -> None:
        template = tmp_path / "template"
        template.mkdir()
        (template / "pad.txt").write_bytes(b"")
        with REAL_SCANDIR(template) as it:
            self._like = next(iter(it))
        self.folder = _key(folder)
        self.total = total

    def __call__(self, path: Any = ".") -> _Listing:
        with REAL_SCANDIR(path) as it:
            real: list[Any] = list(it)
        if _key(path) == self.folder:
            base = os.fspath(path)
            width = len(str(self.total))
            real += [
                _Pad(base, f"pad{i:0{width}d}.txt", self._like)
                for i in range(self.total - len(real))
            ]
        return _Listing(real)


def _tree(tmp_path: Path, site: str) -> tuple[Path, Path]:
    """(target, oversized folder): `Old.lua` in `B`'s character folder, and
    the folder `site` names made in account `A`."""
    account = tmp_path / "_x_" / "WTF" / "Account"
    folder = account / FOLDERS[site]
    folder.mkdir(parents=True)
    old = account / "B" / "Realm" / "Char" / "SavedVariables" / "Old.lua"
    old.parent.mkdir(parents=True)
    old.write_bytes(OLD)
    os.utime(old, ns=(10**18, 10**18))
    return account / "B" / "SavedVariables" / "Target.lua", folder


@pytest.mark.parametrize("site", list(FOLDERS))
def test_positive_control_old_decides_unpadded_constructed(tmp_path: Path, site: str) -> None:
    target, _folder = _tree(tmp_path, site)
    assert serialize(NEW, target=target) == OLDER


@pytest.mark.parametrize("site", list(FOLDERS))
def test_positive_control_a_listed_folder_at_the_bound_uses_up_the_budget_constructed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, site: str
) -> None:
    target, folder = _tree(tmp_path, site)
    monkeypatch.setattr(os, "scandir", _PaddedScandir(tmp_path, folder, CAP))
    assert serialize(NEW, target=target) == FALLBACK


@pytest.mark.parametrize("site", list(FOLDERS))
def test_a_skipped_folder_takes_nothing_from_the_scan_budget_constructed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, site: str
) -> None:
    target, folder = _tree(tmp_path, site)
    monkeypatch.setattr(os, "scandir", _PaddedScandir(tmp_path, folder, CAP + 1))
    assert serialize(NEW, target=target) == OLDER
