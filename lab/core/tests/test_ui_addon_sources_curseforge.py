"""The Addons view's "Where they came from" panel with the CurseForge app's
records beside WowUp's (M22-25).

Driven under Node with the harness of `test_ui_addon_sources.py` (itself
`test_ui_account.py`'s), fed what the real CLI printed through the real
server. The install is that module's two captures side by side; the
records are the owner's real CurseForge file
(`fixtures/curseforge/macos/AddonGameInstance.json`) and WowUp list, put
where each app keeps them in the stand-in home. The pairing, and moving
each app's game folder onto the launch client's copy (byte-level, that
path only), are `constructed`, as is every test labelled so. Nothing reads
or writes a real install or a real app folder.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import test_ui_addon_sources as panel

from wowlab_core import addonsource

both = panel.both
home = panel.home
server = panel.server
no_env_flavor = panel.no_env_flavor

FOREVER = panel.FOREVER
CURSEFORGE = (
    Path(__file__).resolve().parent / "fixtures" / "curseforge" / "macos" / "AddonGameInstance.json"
)
INSTALL_PATH = b"/Volumes/Labdisk/Games/World of Warcraft/_forever_/"


def _curseforge(flavor: Path | None = None) -> None:
    folder = addonsource.curseforge_folder()
    assert Path(os.environ["HOME"]).parent in folder.parents
    path = folder.joinpath(*addonsource.CURSEFORGE_RECORDS)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = CURSEFORGE.read_bytes()
    if flavor is not None:
        moved = json.dumps(str(flavor) + "/")[1:-1].encode("utf-8")
        assert data.count(INSTALL_PATH) > 1
        data = data.replace(INSTALL_PATH, moved)
    path.write_bytes(data)
    os.utime(path, ns=(panel.WRITTEN_NS, panel.WRITTEN_NS))


def _forever(server: Any, tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    scenario = panel._scenario(server, [{"sources_open": True}])
    out = panel._drive(tmp_path / "node", scenario)
    return panel._blocks(out["steps"][0]["sources"])[FOREVER], scenario


def test_curseforge_records_are_listed_beside_wowups_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    _curseforge(both / FOREVER)
    panel._wowup()
    panel._settings(both / FOREVER)
    forever, scenario = _forever(server, tmp_path)
    assert forever["errors"] == []
    assert forever["what"][:4] == [
        "In WowUp's list",
        "In WowUp's list, with none of their folders here",
        "In CurseForge's list",
        "In CurseForge's list, with none of their folders here",
    ]
    [auctionator] = [i for i in forever["items"] if i.startswith("Auctionator:")]
    assert auctionator == (
        "Auctionator: from CurseForge, CurseForge installed file Auctionator-352.zip, "
        "CurseForge's record no longer matches this folder (the TOC here says 346). "
        "Folders: Auctionator."
    )
    [banter] = [i for i in forever["items"] if i.startswith("BanterBlocker:")]
    assert (
        banter == "BanterBlocker: from CurseForge, file BanterBlocker.zip names no version; "
        "not compared. Folders: BanterBlocker."
    )
    # Baganator is in both lists.
    assert "In both WowUp's and CurseForge's lists (1)" in forever["what"]
    assert forever["both"] == ["Baganator"]
    listed = json.loads(panel._list_run(scenario, FOREVER)["stdout"])["sources"]
    assert [f["folder"] for f in listed["folders"] if len(f["managers"]) > 1] == ["Baganator"]
    assert len(forever["clear"]) == 2
    assert forever["clear"][0].startswith("WowUp's list, last written ")
    assert forever["clear"][1].startswith("CurseForge's list, last written ")
    assert forever["clear"][1].endswith(": 22 addons.")
    assert forever["loose"] == []


def test_curseforge_alone_judges_the_folders_it_does_not_list_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    _curseforge(both / FOREVER)
    extra = both / FOREVER / "Interface" / "AddOns" / "ByHand"
    extra.mkdir()
    (extra / "ByHand.toc").write_bytes(b"## Interface: 16001\n## Version: 1\n")
    forever, _ = _forever(server, tmp_path)
    assert forever["what"][-1] == "Not in CurseForge's list (1)"
    assert forever["loose"] == ["ByHand"]


def test_records_of_the_owners_own_folder_are_another_folders_here_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    # As captured, CurseForge's one game folder is the owner's `_forever_`.
    _curseforge()
    forever, _ = _forever(server, tmp_path)
    assert forever["items"] == [] and forever["loose"] == []
    assert forever["clear"][0] == (
        "CurseForge does not manage this game folder; its list is for "
        "/Volumes/Labdisk/Games/World of Warcraft/_forever_ (22 addons), and 3 folders here "
        "share a name with one of them."
    )


def test_with_no_list_at_all_the_panel_names_both_apps_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    forever, _ = _forever(server, tmp_path)
    assert forever["clear"] == [
        "wowlab found no WowUp or CurseForge list on this computer, so it cannot tell where "
        "these addons came from."
    ]


def test_a_folder_another_record_installs_names_that_record_constructed(
    server: Any, both: Path, tmp_path: Path
) -> None:
    _curseforge(both / FOREVER)
    addons = both / FOREVER / "Interface" / "AddOns"
    for name, version in (("Questie", "12.13.0"), ("QuestieTrace", "1.14.0")):
        (addons / name).mkdir()
        (addons / name / f"{name}.toc").write_bytes(f"## Version: {version}\n".encode())
    forever, _ = _forever(server, tmp_path)
    [trace] = [i for i in forever["items"] if i.startswith("QuestieTrace:")]
    assert trace == (
        "QuestieTrace: from CurseForge, CurseForge installed file QuestieTrace-v1.10.0.zip, "
        "CurseForge's record no longer matches this folder (the TOC here says 1.14.0; "
        "CurseForge's Questie record (file Questie-v12.13.0.zip) also installs this folder). "
        "Folders: QuestieTrace."
    )
