# Probe from review of m16/06-addon-staleness; reproduces `doctor` saying "a link or not a folder" for an addon folder it merely could not look at
"""`lab_addon_state` maps every `AddonError` from `_addon_folder` to one
detail, "the way to ... is a link or not a folder". `_addon_folder` also
raises `AddonError` when `lstat` fails with an error other than
FileNotFoundError (permission denied, I/O error). `doctor` then tells the
player the folder is a link or a file, which is false, and drops the real
reason. Constructed: a permission error is injected at `longpath.lstat`
(the probe may run as root, where a mode bit does not deny)."""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from wowlab_core import addoninstall, longpath


def _flavor(tmp_path: Path) -> types.SimpleNamespace:
    folder = tmp_path / "flavor" / "Interface" / "AddOns" / "WowLab"
    folder.mkdir(parents=True)
    (folder / "WowLab.toc").write_bytes(b"## Interface: 16001\n")
    return types.SimpleNamespace(path=tmp_path / "flavor", version="1.60.1.69913")


def test_positive_control_a_linked_addon_folder_is_called_a_link_constructed(
    tmp_path: Path,
) -> None:
    flavor = _flavor(tmp_path)
    folder = flavor.path / "Interface" / "AddOns" / "WowLab"
    (folder / "WowLab.toc").unlink()
    folder.rmdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    try:
        folder.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted here")
    state = addoninstall.lab_addon_state(flavor)
    assert state.status == "unknown"
    assert state.detail is not None and "link" in state.detail


def test_an_unreadable_addon_folder_is_not_called_a_link_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flavor = _flavor(tmp_path)
    real = longpath.lstat

    def lstat(path: Path):  # type: ignore[no-untyped-def]
        if Path(path).name == "AddOns":
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr(longpath, "lstat", lstat)
    state = addoninstall.lab_addon_state(flavor)
    assert state.status == "unknown"
    assert state.detail is not None
    assert "link or not a folder" not in state.detail, state.detail
    assert "Permission denied" in state.detail, state.detail
