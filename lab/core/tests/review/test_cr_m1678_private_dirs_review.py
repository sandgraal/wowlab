# Probe from review of m16/78-private-dirs; reproduces the folder writes moved into snapshot.make_private_dirs escaping the L2 write-site scan, and a dangling store parent reported as "vanished as it was created".
"""Two probes from the M16-78 review (constructed inputs, tmp_path only).

1. `lookstore`, `lookspage` and `db2lake` used to create their folders with a
   `mkdir` the L2 scan (`test_write_sites.py`) saw and that `ALLOWED` had to
   justify. M16-78 moved those `mkdir`s into `snapshot.make_private_dirs`,
   in a file the scan exempts as "its own store", and dropped the `ALLOWED`
   rows. `write_page` still creates the player's `--out` folder anywhere
   outside the user data directory, but no write site or reason accounts for
   it any more.

2. A store whose parent is a dangling symlink: on main `Path.mkdir` raised
   `FileExistsError` and the store said "cannot open the store"; now
   `make_private_dirs` raises `FileNotFoundError` and `_open_root` says the
   store "vanished as it was created", which is not what happened.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

from wowlab_core import lookspage
from wowlab_core.snapshot import SnapshotError, SnapshotStore

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "lab" / "core" / "tests"))
import test_write_sites as ws  # noqa: E402


def _calls_to(source: str, name: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
    ]


def test_write_page_creates_a_folder_outside_the_user_data_dir_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control: the call really creates folders the player named."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    out = tmp_path / "elsewhere" / "deep" / "page.html"
    lookspage.write_page("<!doctype html>", out)
    assert (tmp_path / "elsewhere" / "deep").is_dir()


def test_every_make_private_dirs_call_outside_the_exempt_files_is_justified_constructed() -> None:
    """L2: a folder write outside guard, snapshot (store) and gamedata (cache)
    is listed in ALLOWED with its reason, as the `mkdir` it replaced was."""
    unexplained = []
    for path in ws._library_files():
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        rel = path.relative_to(REPO).as_posix()
        for lineno in _calls_to(source, "make_private_dirs"):
            key = (rel, lines[lineno - 1].strip())
            if key not in ws.ALLOWED:
                unexplained.append(key)
    assert unexplained == [], f"folder writes no write-site row justifies: {unexplained}"


@pytest.mark.skipif(os.name != "posix", reason="symlinks without privileges: POSIX only")
def test_a_dangling_store_parent_is_not_reported_as_vanished_constructed(tmp_path: Path) -> None:
    ok = SnapshotStore(tmp_path / "fine" / "store")
    fd = ok._open_root(create=True)  # positive control: a missing store is made
    assert fd is not None
    os.close(fd)

    dangling = tmp_path / "dang"
    dangling.symlink_to(tmp_path / "nowhere")
    with pytest.raises(SnapshotError) as info:
        SnapshotStore(dangling / "store")._open_root(create=True)
    assert "vanished" not in str(info.value), str(info.value)
