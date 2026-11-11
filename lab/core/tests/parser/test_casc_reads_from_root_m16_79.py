"""The CASC reads open from the install root one folder at a time (M16-79):
the build config and the `.idx` files (`casc._read`) and an archive record
(`read_record`), so a folder under `Data/` that is a link is refused rather
than followed, also when it becomes one after `_check_dir` looked.

Constructed only (ADR-0033): a synthetic install from `_casc_build`, no
Blizzard bytes. Each linked folder points at an identical copy, so a read
that followed it would succeed; a refusal is the walk's doing. Every
refusal is `CascUnreadableError` in M16-65's words (`snapshot`), with the
path and the folder in full, and nothing in the install changes (L1).
"""

from __future__ import annotations

import os
import random
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from _casc_build import (
    FLAVOR,
    blte,
    idx_bytes,
    idx_name,
    key_in,
    make_install,
    n_block,
    record,
    tree_state,
)

from wowlab_core import casc, snapshot
from wowlab_core.install import read_install

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "symlink"), reason="symlinks need a privilege"
)

WALKS = pytest.mark.parametrize("nofollow", [True, False], ids=["dir-fd-walk", "lstat-walk"])


def _walk(monkeypatch: pytest.MonkeyPatch, nofollow: bool) -> None:
    """The `dir_fd` walk (POSIX), or the `lstat` walk Windows takes."""
    if not nofollow:
        monkeypatch.setattr(snapshot, "_O_NOFOLLOW", 0)


def _link_out(folder: Path, elsewhere: Path) -> None:
    """Move `folder` (a folder or a file) to `elsewhere` and leave a link to
    it in its place."""
    is_dir = folder.is_dir()
    shutil.move(folder, elsewhere)
    folder.symlink_to(elsewhere, target_is_directory=is_dir)


def _swap_after_check(monkeypatch: pytest.MonkeyPatch, folder: Path, elsewhere: Path) -> list[Path]:
    """`_check_dir` runs as it does; once it has passed `folder` (and every
    check before the read is done), `folder` becomes a link. The read is
    then the only thing that can see it."""
    real: Callable[[Path], None] = casc._check_dir
    checked: list[Path] = []

    def check_then_swap(path: Path) -> None:
        real(path)
        checked.append(path)
        if path == folder:
            _link_out(folder, elsewhere)

    monkeypatch.setattr(casc, "_check_dir", check_then_swap)
    return checked


def _changed(path: Path, folder: Path) -> str:
    return f"{path} is reached through a linked folder: {folder} {snapshot.CHANGED_WHILE_READ}"


def _record_install(tmp_path: Path) -> tuple[Path, casc.CascStorage, casc.IndexEntry]:
    rng = random.Random(1579)
    key = key_in(3, rng) + bytes(7)
    rec = record(key, blte([n_block(b"constructed")]))
    root, _ = make_install(
        tmp_path, indexes={idx_name(3, 1): idx_bytes(3, [(key, 1, 16, len(rec))])}
    )
    (root / "Data" / "data" / "data.001").write_bytes(bytes(16) + rec)
    storage = casc.open_storage(read_install(root), FLAVOR)
    entry = storage.index.find(key)
    assert entry is not None
    return root, storage, entry


# ─── the build config ────────────────────────────────────────────────────────


def _config_path(root: Path, storage: casc.CascStorage) -> Path:
    return casc.build_config_path(root, storage.config.key)


@WALKS
@pytest.mark.parametrize("level", [0, 1, 2, 3], ids=["Data", "config", "k01", "k23"])
def test_a_config_folder_swapped_after_the_check_is_refused_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nofollow: bool, level: int
) -> None:
    root, storage, _ = _record_install(tmp_path)
    path = _config_path(root, storage)
    folder = [root / "Data", root / "Data" / "config", path.parent.parent, path.parent][level]
    _walk(monkeypatch, nofollow)
    checked = _swap_after_check(monkeypatch, folder, tmp_path / "elsewhere-constructed")
    with pytest.raises(casc.CascUnreadableError) as caught:
        casc.read_build_config(root, storage.config.key)
    assert folder in checked
    assert str(caught.value) == _changed(path, folder)
    assert caught.value.path == path
    assert isinstance(caught.value.__cause__, snapshot.LinkedPathError)


@WALKS
def test_a_config_folder_linked_with_no_check_is_refused_by_the_walk_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nofollow: bool
) -> None:
    """With `_check_dir` out of the way, the walk alone refuses the link."""
    root, storage, _ = _record_install(tmp_path)
    path = _config_path(root, storage)
    _link_out(root / "Data" / "config", tmp_path / "elsewhere-constructed")
    _walk(monkeypatch, nofollow)
    monkeypatch.setattr(casc, "_check_dir", lambda path: None)
    with pytest.raises(casc.CascUnreadableError) as caught:
        casc.read_build_config(root, storage.config.key)
    assert str(caught.value) == _changed(path, root / "Data" / "config")


@WALKS
def test_a_build_config_that_is_a_link_is_refused_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nofollow: bool
) -> None:
    root, storage, _ = _record_install(tmp_path)
    path = _config_path(root, storage)
    _link_out(path, tmp_path / "elsewhere-constructed")
    _walk(monkeypatch, nofollow)
    with pytest.raises(casc.CascUnreadableError) as caught:
        casc.read_build_config(root, storage.config.key)
    assert str(caught.value) == f"{path} is a link; {snapshot.not_read_through('file')}"


# ─── the index ───────────────────────────────────────────────────────────────


@WALKS
@pytest.mark.parametrize("which", ["Data", "data"])
def test_an_index_folder_swapped_after_the_check_is_refused_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nofollow: bool, which: str
) -> None:
    root, _, _ = _record_install(tmp_path)
    folder = root / "Data" if which == "Data" else root / "Data" / "data"
    _walk(monkeypatch, nofollow)
    checked = _swap_after_check(monkeypatch, folder, tmp_path / "elsewhere-constructed")
    with pytest.raises(casc.CascUnreadableError) as caught:
        casc.load_index(root)
    assert folder in checked
    path = root / "Data" / "data" / idx_name(3, 1)
    assert str(caught.value) == _changed(path, folder)
    assert caught.value.path == path


# ─── an archive record ───────────────────────────────────────────────────────


@WALKS
@pytest.mark.parametrize("which", ["Data", "data"])
def test_an_archive_folder_swapped_after_the_check_is_refused_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nofollow: bool, which: str
) -> None:
    root, _, entry = _record_install(tmp_path)
    folder = root / "Data" if which == "Data" else root / "Data" / "data"
    _walk(monkeypatch, nofollow)
    checked = _swap_after_check(monkeypatch, folder, tmp_path / "elsewhere-constructed")
    with pytest.raises(casc.CascUnreadableError) as caught:
        casc.read_record(root, entry)
    assert folder in checked
    path = casc.archive_path(root, entry.archive)
    assert str(caught.value) == _changed(path, folder)
    assert caught.value.path == path


@WALKS
def test_an_archive_that_is_a_link_is_refused_in_the_shared_words_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nofollow: bool
) -> None:
    root, _, entry = _record_install(tmp_path)
    path = casc.archive_path(root, entry.archive)
    _link_out(path, tmp_path / "elsewhere-constructed.bin")
    _walk(monkeypatch, nofollow)
    with pytest.raises(casc.CascUnreadableError) as caught:
        casc.read_record(root, entry)
    assert str(caught.value) == f"{path} is a link; {snapshot.not_read_through('file')}"


@WALKS
def test_the_reads_from_the_root_still_read_and_write_nothing_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nofollow: bool
) -> None:
    """A regular install reads as before, through either walk, also when the
    install root itself is reached through a link (what is above the root
    is the caller's choice)."""
    real_root, storage, entry = _record_install(tmp_path)
    root = tmp_path / "root-link-constructed"
    root.symlink_to(real_root, target_is_directory=True)
    _walk(monkeypatch, nofollow)
    before = tree_state(real_root)
    assert casc.read_build_config(root, storage.config.key) == storage.config
    _, versions = casc.load_index(root)
    assert versions == casc.load_index(real_root)[1]
    assert casc.read_record(root, entry) == casc.read_record(real_root, entry)
    assert tree_state(real_root) == before
