# Probe from review of m22/11-bnet-profiles; reproduces `blizzard logout` deleting a file inside a game install through a linked data folder
"""CONSTRUCTED INPUT (L1, hostile case): wowlab's `blizzard` data folder is a
link into a game install, and the install holds a file named `client.json`
there. `bnet.save_credentials` and `bnet.save_profile` refuse that folder
(`_refuse_place`: in an install, or a link), but `bnet.clear_credentials`
(`wowlab blizzard logout`) unlinks `credentials_path()` with no such check,
so it deletes the install's file: a write inside an install outside `guard`
(L1, L2). The positive control is the same layout with the folder a real
folder under the user data directory, where logout removes wowlab's own file.
"""

from __future__ import annotations

from pathlib import Path

import platformdirs
import pytest

from wowlab_core import bnet


def _layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    install = tmp_path / "World of Warcraft"
    folder = install / "_x_" / "WTF"
    folder.mkdir(parents=True)
    (install / ".build.info").write_text("constructed\n", encoding="utf-8")
    user_data = tmp_path / "userdata" / "wowlab"
    user_data.mkdir(parents=True)
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: user_data)
    return folder, user_data


def test_logout_leaves_a_file_inside_an_install_alone_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, user_data = _layout(tmp_path, monkeypatch)
    install_file = folder / "client.json"
    install_file.write_text("the install's own file\n", encoding="utf-8")
    (user_data / "blizzard").symlink_to(folder, target_is_directory=True)
    # The writers refuse this folder ...
    with pytest.raises(bnet.BnetError):
        bnet.save_credentials(bnet.Credentials("constructedid0001", "constructedsecret01"))
    assert install_file.read_text(encoding="utf-8") == "the install's own file\n"
    # ... and so must the remover: it refuses (raises) or removes nothing.
    try:
        removed = bnet.clear_credentials()
    except bnet.BnetError:
        removed = False
    assert install_file.exists(), "blizzard logout deleted a file inside the install"
    assert removed is False


def test_logout_removes_wowlabs_own_file_positive_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _layout(tmp_path, monkeypatch)
    bnet.save_credentials(bnet.Credentials("constructedid0001", "constructedsecret01"))
    assert bnet.credentials_path().is_file()
    assert bnet.clear_credentials() is True
    assert not bnet.credentials_path().exists()
