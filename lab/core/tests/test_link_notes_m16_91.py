"""A linked combat log is a file in `tree`'s link note (M16-91).

`Logs/WoWCombatLog.txt` has its own row as a file (`combat-log`) and, as a
folder, only the general row of the folder above it (`logs-other`, which
hangs from `Logs`). M16-80 said "real one" for every pair of different rows;
the link stands in place of a file, so the note says "real file". The same
holds for the diagnostic logs (`Logs/*.log`) and for the cache files with
their own rows under `Cache/`'s general row (`cache-other`):
`Cache/ADB/<locale>/DBCache.bin` and `Cache/WDB/<locale>/*.wdb`. Where one
row covers files and folders alike below its folder (`Fonts/`, an addon's
folder, the rest of `Logs/` and `Cache/`), the note still says "real one",
as M16-80 ruled.

Constructed (L8, boundary): the unit cases classify places that do not exist
on disk. The end-to-end run copies the captured tree (`fixtures/macos/`)
into `tmp_path` and replaces its combat log with a symlink to a constructed
file outside the install. Nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, layout

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
COMBAT_LOG = "Logs/WoWCombatLog-040126_021630.txt"  # the captured tree's own combat log
FILE_END = "wowlab does not read through links, so it reads this only when the real file is there"
ONE_END = "wowlab does not read through links, so it reads this only when the real one is there"


@pytest.fixture
def flavor_tree(tmp_path: Path) -> layout.Layout:
    root = tmp_path / "World of Warcraft"
    (root / "_constructed_" / "WTF" / "Account").mkdir(parents=True)
    return layout.Layout(root / "_constructed_", install_root=root)


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        pytest.param("Logs/WoWCombatLog.txt", "file", id="constructed-combat-log"),
        pytest.param(
            "Logs/WoWCombatLog-040126_021630.txt", "file", id="constructed-dated-combat-log"
        ),
        pytest.param("Logs/Client.log", "file", id="constructed-diagnostic-log"),
        pytest.param("Cache/ADB/enUS/DBCache.bin", "file", id="constructed-hotfix-cache"),
        pytest.param("Cache/WDB/enUS/creaturecache.wdb", "file", id="constructed-wdb-cache"),
        # Unchanged from M16-80: the folder the general row hangs from, and
        # one row for both kinds below it.
        pytest.param("Logs", "folder", id="constructed-logs-folder"),
        pytest.param("Logs/constructed.dat", "one", id="constructed-other-log"),
        pytest.param("Cache/WDB/enUS", "one", id="constructed-wdb-locale-folder"),
        pytest.param("Fonts/FRIZQT__.TTF", "one", id="constructed-font-file"),
        pytest.param(
            "Interface/AddOns/Constructed/Constructed.toc", "one", id="constructed-addon-toc"
        ),
        # A folder row that is not a general row above this place. M16-95
        # changed this pin from "one": the file row spells out the name, and
        # the folder row (`realm-folder`) takes a folder of any name here.
        pytest.param(
            "WTF/Account/90000001#1/macros-cache.txt", "file", id="constructed-macros-cache"
        ),
    ],
)
def test_a_file_row_under_a_general_folder_row_stands_for_a_file_constructed(
    flavor_tree: layout.Layout, rel: str, expected: str
) -> None:
    stands_for = cli._link_stands_for(
        flavor_tree.classify(rel, is_dir=False), flavor_tree.classify(rel, is_dir=True)
    )
    assert stands_for == expected


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import platformdirs

    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))
    install = tmp_path / "wow"
    install.mkdir()
    shutil.copy2(CAPTURE / ".build.info", install / ".build.info")
    shutil.copytree(CAPTURE / "forever", install / FLAVOR)
    monkeypatch.chdir(tmp_path)
    return install


def _notes(root: Path) -> dict[str, Any]:
    result = CliRunner().invoke(cli.app, ["tree", "--root", str(root), "--json"])
    assert result.exit_code == 0, result.output
    return {i["path"]: i["note"] for i in json.loads(result.stdout)["items"]}


def test_tree_says_a_linked_combat_log_is_a_file_constructed(root: Path, tmp_path: Path) -> None:
    target = tmp_path / "shared" / "WoWCombatLog.txt"
    target.parent.mkdir()
    target.write_bytes(b"constructed\n")
    link = root / FLAVOR / COMBAT_LOG
    link.unlink()
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"cannot create a symlink here: {exc}")
    note = _notes(root)[COMBAT_LOG]
    assert note.endswith(FILE_END), note
    assert ONE_END not in note, note
