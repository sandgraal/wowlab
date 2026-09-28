"""Graders for the per-folder cap on sibling listing (M10-18T, follow-up 1).

Written before M10-18 from the ticket text (docs/BACKLOG.md, M10-18T, the
owner-approved follow-ups of 2026-09-28 from the M10-12 reviews) and
`docs/LAB_PLAN.md` §6.4, amendment of 2026-09-27, items 3 and 4: a sibling
folder holding more than 65,536 entries is skipped whole during style
detection. It is never read or sorted in full, and whether it is skipped
depends only on its entry count, never on the order the file system lists
it in. A folder of exactly 65,536 entries is not skipped. Every folder the
detection lists is covered: `WTF/Account` itself, a per-account folder, an
account's `SavedVariables`, a realm (or `<digits>`) folder, a character
folder and a character's `SavedVariables`.

Where the newest sibling is an entry of the oversized folder (a per-account
`SavedVariables.lua`, a file in a `SavedVariables` folder), today's listing
takes it and it decides, so skipping the folder shows in the output. Where
it sits in a folder below the oversized one (`WTF/Account`, a realm or a
character folder), item 4's 16,384-entry scan budget is already used up by
the oversized folder today and nothing below it is listed; there the output
is a control that must keep holding, and the cap is graded by how far the
folder is read.

Every tree is CONSTRUCTED under `tmp_path` (L8; the file name puts it in
every test id). No install is touched. The oldest sibling, account `A`'s
`SavedVariables/Old.lua`, is LF with a leading empty line, no indentation
and `;` separators (`OLDER`). The newest, inside the oversized folder, is
the tab-indented `-- [n]` layout (`REFERENCE`). A folder that is skipped
leaves `OLDER` to decide, or the fallback when the skipped folder is
`WTF/Account`. Account `A` is listed before the oversized folder, so these
graders do not depend on whether a skipped folder's entries count toward
the 16,384-entry scan budget of item 4.

What these graders rely on (the seam)
-------------------------------------
- `serialize(document, *, target=None, lab_written=frozenset())`, as merged
  in M10-12.
- Sibling folders are listed with `os.scandir(path)`, looked up on the `os`
  module when called (as M10-12 does), with `path` a `str` or path-like,
  not a descriptor. The padded graders replace `os.scandir` so the
  oversized folder shows 65,537 or more entries without that many files on
  disk. Padding entries are named `pad<n>.txt` and report themselves as
  empty regular files. No luadata constant is patched: the bound's name is
  M10-18's choice, and the real 65,536 is graded.
- "Never read in full" is graded as: no single `os.scandir` iterator is
  advanced more than 65,537 times (the bound plus the one entry that shows
  a folder is over it).
- One grader makes 65,537 real files, so it does not depend on
  `os.scandir`. It takes a few seconds on APFS and ext4 and is skipped on
  Windows, where creating that many files on the CI runner is much slower.
  The padded graders run everywhere.

Graders that fail today carry one marker line M10-18 deletes. The unmarked
tests are positive controls that pass today and must keep passing. Nothing
else in this file is the implementer's to change.
"""

from __future__ import annotations

import os
import sys
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

CAP = 65_536  # §6.4 follow-up 1 (owner, 2026-09-28): more than this is skipped whole

NEWEST = b'\nV = {\n\t"a", -- [1]\n}\n'  # LF, leading empty line, `,`, tab, `-- [n]`
OLD = b'\nV = {\n"a";\n}\n'  # LF, leading empty line, `;`, no indentation or comment

REFERENCE = b"\nN = {\n\t1, -- [1]\n}\n"  # the newest sibling decided
OLDER = b"\nN = {\n1;\n}\n"  # the newest was never listed; `Old.lua` decided
FALLBACK = b"\r\nN = {\r\n1,\r\n}\r\n"  # nothing was listed (item 6)

ONE = Entry(
    None, KeyStyle.POSITIONAL, None, None, None, LuaNumber(None, "1"), None, None, None, False
)
NEW = LuaDocument((Assignment(None, "N", None, LuaTable(None, (ONE,), None)),), None)

T_OLD = 10**18
T_NEW = 2 * 10**18

REAL_SCANDIR = os.scandir

# site id -> (the newest sibling, relative to `WTF/Account`; the oversized
# folder, relative to `WTF/Account`, "" for `WTF/Account` itself)
SITES: dict[str, tuple[str, str]] = {
    "wtf-account": ("B/SavedVariables/New.lua", ""),
    "per-account-folder": ("B/SavedVariables.lua", "B"),
    "account-savedvariables": ("B/SavedVariables/New.lua", "B/SavedVariables"),
    "realm-folder": ("B/Realm/Char/SavedVariables/New.lua", "B/Realm"),
    "character-folder": ("B/Realm/Char/SavedVariables/New.lua", "B/Realm/Char"),
    "character-savedvariables": (
        "B/Realm/Char/SavedVariables/New.lua",
        "B/Realm/Char/SavedVariables",
    ),
}
SKIPPED = {site: (FALLBACK if folder == "" else OLDER) for site, (_new, folder) in SITES.items()}
# Sites where the newest sibling is an entry of the padded folder itself: it
# sorts first there and is listed today, so skipping the folder shows.
FILE_SITES = ["per-account-folder", "account-savedvariables", "character-savedvariables"]
# Sites where it sits in a folder below the padded one: see
# `test_nothing_below_a_folder_over_the_bound_decides`.
FOLDER_SITES = [site for site in SITES if site not in FILE_SITES]


def _key(path: Any) -> str:
    """The path made absolute and normalised as text; links not followed."""
    return os.path.normcase(os.path.normpath(Path(os.fspath(path)).absolute()))


def _tree(tmp_path: Path, site: str) -> tuple[Path, Path]:
    """(target, oversized folder) for `site`; `Old.lua` in account `A` and
    the newest sibling where `site` puts it."""
    account = tmp_path / "_x_" / "WTF" / "Account"
    old = account / "A" / "SavedVariables" / "Old.lua"
    old.parent.mkdir(parents=True)
    old.write_bytes(OLD)
    os.utime(old, ns=(T_OLD, T_OLD))
    newest_rel, folder_rel = SITES[site]
    newest = account / newest_rel
    newest.parent.mkdir(parents=True, exist_ok=True)
    newest.write_bytes(NEWEST)
    os.utime(newest, ns=(T_NEW, T_NEW))
    folder = account / folder_rel if folder_rel else account
    return account / "A" / "SavedVariables" / "Target.lua", folder


class _Pad:
    """A padding directory entry: an empty regular file named `pad<n>.txt`
    that exists only in the listing (its metadata is a real empty file's
    outside the install)."""

    __slots__ = ("_like", "name", "path")

    def __init__(self, folder: str, name: str, like: os.DirEntry[str]) -> None:
        self.name = name
        self.path = os.fspath(Path(folder) / name)
        self._like = like

    def __fspath__(self) -> str:
        return self.path

    def __repr__(self) -> str:
        return f"<_Pad {self.name!r}>"

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
    """A `scandir` iterator that counts how far it was advanced."""

    def __init__(self, items: list[Any]) -> None:
        self._items = iter(items)
        self.pulled = 0

    def __enter__(self) -> _Listing:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __iter__(self) -> Iterator[Any]:
        return self

    def __next__(self) -> Any:
        item = next(self._items)
        self.pulled += 1
        return item

    def close(self) -> None:
        self._items = iter(())


class _PaddedScandir:
    """`os.scandir`, with `folder` padded to `total` entries and listed in
    byte-wise name order or its reverse; every other folder listed as is.
    Every iterator handed out is kept, by folder, to count what was read."""

    def __init__(self, tmp_path: Path, folder: Path, total: int, *, reverse: bool = False) -> None:
        template = tmp_path / "template"
        template.mkdir()
        (template / "pad.txt").write_bytes(b"")
        with REAL_SCANDIR(template) as it:
            self._like = next(iter(it))
        self.folder = _key(folder)
        self.total = total
        self.reverse = reverse
        self.handed: list[tuple[str, _Listing]] = []

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
            real.sort(key=lambda entry: os.fsencode(entry.name), reverse=self.reverse)
        listing = _Listing(real)
        self.handed.append((_key(path), listing))
        return listing


def _padded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, folder: Path, total: int, *, reverse: bool
) -> _PaddedScandir:
    scandir = _PaddedScandir(tmp_path, folder, total, reverse=reverse)
    monkeypatch.setattr(os, "scandir", scandir)
    return scandir


ORDERS = [False, True]
ORDER_IDS = ["name-order", "reverse-name-order"]


# ── positive controls: the trees are live ───────────────────────────────────


@pytest.mark.parametrize("site", list(SITES))
def test_positive_control_the_newest_sibling_decides_unpadded(tmp_path: Path, site: str) -> None:
    """Without padding, the newest sibling at each site decides: every site
    is one the detection lists."""
    target, _folder = _tree(tmp_path, site)
    assert serialize(NEW, target=target) == REFERENCE


@pytest.mark.parametrize("site", [s for s, (_n, f) in SITES.items() if f])
def test_positive_control_old_decides_without_the_newest(tmp_path: Path, site: str) -> None:
    """With the newest sibling excluded (`lab_written`), `Old.lua` decides:
    `OLDER` is what a skipped folder leaves."""
    target, _folder = _tree(tmp_path, site)
    newest = tmp_path / "_x_" / "WTF" / "Account" / SITES[site][0]
    assert serialize(NEW, target=target, lab_written=[newest]) == OLDER


@pytest.mark.parametrize("reverse", ORDERS, ids=ORDER_IDS)
@pytest.mark.parametrize("site", FILE_SITES)
def test_positive_control_a_folder_of_exactly_the_bound_is_listed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, site: str, reverse: bool
) -> None:
    """Exactly 65,536 entries is not more than the bound: the folder is
    listed and its newest sibling decides, in either listing order (the
    newest sorts before every `pad<n>.txt`, inside the 16,384-entry scan
    budget of item 4)."""
    target, folder = _tree(tmp_path, site)
    _padded(monkeypatch, tmp_path, folder, CAP, reverse=reverse)
    assert serialize(NEW, target=target) == REFERENCE


@pytest.mark.parametrize("reverse", ORDERS, ids=ORDER_IDS)
@pytest.mark.parametrize("site", FOLDER_SITES)
def test_nothing_below_a_folder_over_the_bound_decides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, site: str, reverse: bool
) -> None:
    """Where the newest sibling sits in a folder below the padded one
    (65,537 entries), nothing below it decides: today because the padded
    folder uses up item 4's 16,384-entry scan budget, after M10-18 because
    it is skipped whole. This holds today and must keep holding; the budget
    masks the cap here, and `test_an_oversized_folder_is_never_read_in_full`
    grades it instead."""
    target, folder = _tree(tmp_path, site)
    _padded(monkeypatch, tmp_path, folder, CAP + 1, reverse=reverse)
    assert serialize(NEW, target=target) == SKIPPED[site]


# ── follow-up 1: a folder over the bound is skipped whole ───────────────────


@pytest.mark.parametrize("reverse", ORDERS, ids=ORDER_IDS)
@pytest.mark.parametrize("site", FILE_SITES)
def test_a_folder_over_the_bound_is_skipped_whole(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, site: str, reverse: bool
) -> None:
    """65,537 entries in the folder holding the newest sibling: the folder
    is skipped whole, so the newest never decides, whichever order the
    folder is listed in. A listing cut at the bound instead of skipped
    keeps the newest in name order, where it comes first, and fails here."""
    target, folder = _tree(tmp_path, site)
    _padded(monkeypatch, tmp_path, folder, CAP + 1, reverse=reverse)
    assert serialize(NEW, target=target) == OLDER  # REFERENCE today


@pytest.mark.parametrize("reverse", ORDERS, ids=ORDER_IDS)
@pytest.mark.parametrize("site", list(SITES))
def test_an_oversized_folder_is_never_read_in_full(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, site: str, reverse: bool
) -> None:
    """A folder of 69,632 entries is read at most 65,537 entries deep (the
    bound and the one entry that shows it is over), by every `os.scandir`
    iterator the detection opens, and the oversized folder is opened (the
    count is not vacuous)."""
    target, folder = _tree(tmp_path, site)
    scandir = _padded(monkeypatch, tmp_path, folder, CAP + 4096, reverse=reverse)
    serialize(NEW, target=target)
    assert scandir.folder in {path for path, _listing in scandir.handed}
    over = [(path, listing.pulled) for path, listing in scandir.handed if listing.pulled > CAP + 1]
    assert over == []  # [(the padded folder, 69632)] today


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="creates 65,537 real files, slow on the Windows runner; the padded graders run there",
)
def test_a_real_folder_over_the_bound_is_skipped_whole(tmp_path: Path) -> None:
    """65,537 real entries (the newest sibling and 65,536 empty `.txt`
    files) in account `B`'s `SavedVariables`, nothing patched: the folder
    is skipped whole and `Old.lua` decides."""
    target, folder = _tree(tmp_path, "account-savedvariables")
    for i in range(CAP):
        os.close(os.open(folder / f"pad{i:05d}.txt", os.O_CREAT | os.O_WRONLY | os.O_EXCL))
    assert sum(1 for _ in folder.iterdir()) == CAP + 1
    assert serialize(NEW, target=target) == OLDER  # REFERENCE today
