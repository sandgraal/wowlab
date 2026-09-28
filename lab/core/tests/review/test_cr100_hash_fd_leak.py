# Probe from review of m11/12-profiles-hardening; reproduces a descriptor leak in profiles._hash_fd when an entry is swapped for a folder between its lstat and its open
"""Constructed (not a real capture): `<root>/_flavor_/Interface/a.lua` is a
regular file when `profiles._differs` `lstat`s it and a folder by the time it
is opened. `os.open(..., O_RDONLY | O_NOFOLLOW | O_NONBLOCK)` opens a folder
without error, and `os.fdopen(fd, "rb")` then raises IsADirectoryError without
closing the descriptor it was given, so `_hash_fd` returns "differs" and
leaks it. The positive control is the same walk with no swap. POSIX only
(counts descriptors through /dev/fd). Nothing reads or writes a real install.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import profiles
from wowlab_core.snapshot import Entry

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="counts descriptors via /dev/fd")

FLAVOR = "_flavor_"
SAVED = b"-- saved\n"


def _open_fds() -> set[str]:
    return {p.name for p in Path("/dev/fd").iterdir()}


def _setup(tmp_path: Path) -> tuple[Path, Path, Entry]:
    root = tmp_path / "install"
    folder = root / FLAVOR / "Interface"
    folder.mkdir(parents=True)
    (folder / "a.lua").write_bytes(SAVED)
    entry = Entry(
        path=f"{FLAVOR}/Interface/a.lua",
        kind="file",
        sha256=hashlib.sha256(SAVED).hexdigest(),
        size=len(SAVED),
    )
    return root, folder / "a.lua", entry


@pytest.fixture(params=["dir_fd", "lstat"])
def walk(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "dir_fd":
        if not profiles._DIR_FD_WALK:
            pytest.skip("no dir_fd walk on this platform")
    else:
        monkeypatch.setattr(profiles, "_DIR_FD_WALK", False)
    return str(request.param)


def test_control_no_swap_leaves_no_descriptor_open_constructed(walk: str, tmp_path: Path) -> None:
    root, _, entry = _setup(tmp_path)
    before = _open_fds()
    assert profiles._differs(root, FLAVOR, entry) == "same"
    assert _open_fds() == before


def test_entry_swapped_for_a_folder_before_open_leaks_no_descriptor_constructed(
    walk: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, file, entry = _setup(tmp_path)
    real_open = os.open
    swapped: list[bool] = []

    def swap_then_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        name = os.fsdecode(os.fspath(path)).rsplit("/", 1)[-1]
        if name == "a.lua" and not swapped:
            swapped.append(True)
            file.unlink()
            file.mkdir()  # a folder where the regular file was lstat'ed
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_then_open)
    before = _open_fds()
    result = profiles._differs(root, FLAVOR, entry)
    after = _open_fds()
    monkeypatch.undo()
    assert swapped, "the swap never happened"
    assert result == "differs"
    leaked = after - before
    for name in leaked:  # do not leave the leak behind for later tests
        with contextlib.suppress(OSError):
            os.close(int(name))
    assert not leaked, f"descriptor(s) left open: {sorted(leaked)}"
