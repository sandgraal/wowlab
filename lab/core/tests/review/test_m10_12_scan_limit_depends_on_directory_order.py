# Probe from review of m10/12-luadata-serializer (fix round 1); reproduces the
# sibling scan limit making the output depend on directory order.
"""CONSTRUCTED sibling tree in `tmp_path` (L8); no install is touched.

`docs/LAB_PLAN.md` §6.4, amendment of 2026-09-27, item 4: ties break on the
byte-wise relative path "so the output never depends on directory order";
§6.4: "Output is byte-deterministic: same document, same bytes, on every
platform". At 51fd0ba `_list_siblings` stops after `_SIBLING_SCAN_LIMIT`
(16,384) directory entries, counted in the order `os.scandir` returns them.
Which siblings are listed, and so which one decides, depends on that order,
which differs between filesystems (APFS, NTFS, ext4) for the same tree.

Reviewer's run at the real limit (M1, APFS): account `A` holding `Old.lua`
(Forever layout, older) and 17,000 `*.lua.bak` files, account `B` holding
the newest `New.lua` (LF, tab, `-- [n]`). The natural and name-sorted
orders gave the Forever layout; the reverse name order gave the reference
layout. The pairs of `.lua` and `.lua.bak` files make 16,384 entries a
plausible account (about 60 characters with 130 addons each).

This probe lowers the private limit (read at call time) to 6 and lists each
directory in name order, then in reverse name order: the bytes must be the
same. A fix that sorts each listing before counting passes it.

Positive control: with the default limit and either order, the newest
sibling decides (the tree is live and the order hook works).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from wowlab_core import luadata
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

REFERENCE_SIBLING = b'\nV = {\n\t"a", -- [1]\n}\n'
FOREVER_SIBLING = b'\r\nV = {\r\n"a",\r\n}\r\n'
REFERENCE = b"\nN = {\n\t1, -- [1]\n}\n"

ONE = Entry(
    None, KeyStyle.POSITIONAL, None, None, None, LuaNumber(None, "1"), None, None, None, False
)
NEW = LuaDocument((Assignment(None, "N", None, LuaTable(None, (ONE,), None)),), None)

REAL_SCANDIR = os.scandir


class _Listing:
    def __init__(self, items: list[os.DirEntry[str]]) -> None:
        self.items = items

    def __enter__(self) -> Iterator[os.DirEntry[str]]:
        return iter(self.items)

    def __exit__(self, *exc: object) -> None:
        return None


def _ordered(reverse: bool) -> Callable[[str | Path], _Listing]:
    def scandir(path: str | Path) -> _Listing:
        with REAL_SCANDIR(path) as it:
            items = sorted(it, key=lambda e: os.fsencode(e.name), reverse=reverse)
        return _Listing(items)

    return scandir


def _tree(tmp_path: Path) -> Path:
    account = tmp_path / "_x_" / "WTF" / "Account"
    a = account / "A" / "SavedVariables"
    a.mkdir(parents=True)
    (a / "Old.lua").write_bytes(FOREVER_SIBLING)
    os.utime(a / "Old.lua", ns=(10**18, 10**18))
    for i in range(8):
        (a / f"f{i}.lua.bak").write_bytes(b"")
    b = account / "B" / "SavedVariables"
    b.mkdir(parents=True)
    (b / "New.lua").write_bytes(REFERENCE_SIBLING)
    os.utime(b / "New.lua", ns=(2 * 10**18, 2 * 10**18))
    return account / "C" / "SavedVariables" / "Target.lua"


@pytest.mark.parametrize("reverse", [False, True], ids=["name-order", "reverse-order"])
def test_positive_control_newest_sibling_decides_without_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reverse: bool
) -> None:
    target = _tree(tmp_path)
    monkeypatch.setattr(luadata.os, "scandir", _ordered(reverse))
    assert serialize(NEW, target=target) == REFERENCE


def test_output_does_not_depend_on_directory_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _tree(tmp_path)
    monkeypatch.setattr(luadata, "_SIBLING_SCAN_LIMIT", 6)
    monkeypatch.setattr(luadata.os, "scandir", _ordered(False))
    forward = serialize(NEW, target=target)
    monkeypatch.setattr(luadata.os, "scandir", _ordered(True))
    backward = serialize(NEW, target=target)
    assert forward == backward  # differ at 51fd0ba: CRLF Forever layout vs LF reference
