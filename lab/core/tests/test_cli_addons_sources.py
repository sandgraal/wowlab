"""`wowlab addons list`: where each addon came from, from WowUp's records
(M22-16).

Graded on real captures joined to each other (L8): WowUp's list and
settings of the owner's machine (`fixtures/wowup/macos/addons.json`, 6
Wago addons, last written 2026-09-26; `preferences.json`, whose one game
folder is the beta's `_classic_beta_`) beside the two captured installs,
each copied into `tmp_path`:

- `fixtures/macos/` as `_classic_beta_`, the game folder that WowUp list
  was made for: `DBM-Brawlers` (no `## Version:`) and `DBM-Challenges`
  (`r264`, which agrees with the record's `DBM-Dungeons-r264`), each of a
  record whose other folders were not captured;
- `fixtures/macos-70888/` as `_forever_`, the launch client, which WowUp
  never managed: as on the owner's machine, none of its folders is
  WowUp's.

Pairing a WowUp list with an install capture is `constructed`; so is the
settings file with its location pointed at the copy (byte-level, the
location only), and anything added to the copies (a folder, a TOC, a
record); those test ids say so. The user's folders are redirected into
`tmp_path`, so the WowUp folder read is the test's own; nothing reads a
real WowUp folder or a real install, and nothing writes to either.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from _user_dirs import redirect_user_dirs
from test_cli_player_copy import hits
from typer.testing import CliRunner

from wowlab_core import addonsource, cli, install

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WOWUP = FIXTURES / "wowup" / "macos" / "addons.json"
PREFERENCES = FIXTURES / "wowup" / "macos" / "preferences.json"
LOCATION = (
    b"/Volumes/Labdisk/Games/World of Warcraft/_classic_beta_/World of Warcraft Classic Beta.app"
)
BETA = "_classic_beta_"  # the provenance rows' flavor folders; tests may name them (L6)
FOREVER = "_forever_"
DBM = "808eed70-c097-4ed3-907d-c297a8ad6d63"
DUNGEONS = "378c7d50-15e5-4d85-a5b6-49704549b308"
BAGANATOR = "c3104847-a552-469a-b11e-6489348ad0a9"
WRITTEN_NS = 1_790_000_000_000_000_000  # a fixed time for the list's last write

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    redirect_user_dirs(monkeypatch, folder)
    monkeypatch.delenv(install.ENV_FLAVOR, raising=False)
    return folder


def _install(tmp_path: Path, capture: str, flavor: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / capture / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / capture / "forever", root / flavor)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    return root


@pytest.fixture
def beta(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _install(tmp_path, "macos", BETA, monkeypatch)


@pytest.fixture
def forever(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _install(tmp_path, "macos-70888", FOREVER, monkeypatch)


def _folder() -> Path:
    folder = addonsource.wowup_folder()
    tmp = Path(os.environ["HOME"]).parent
    assert tmp in folder.parents, f"{folder} is not under this test's tmp_path"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _wowup(body: bytes | None = None) -> Path:
    """WowUp's list where this machine's WowUp keeps it (in the redirected home)."""
    path = _folder() / "addons.json"
    path.write_bytes(WOWUP.read_bytes() if body is None else body)
    os.utime(path, ns=(WRITTEN_NS, WRITTEN_NS))
    return path


def _settings(flavor: Path | None = None) -> Path:
    """WowUp's settings: the real file, or with its one location moved into
    `flavor` (`constructed`)."""
    data = PREFERENCES.read_bytes()
    if flavor is not None:
        assert data.count(LOCATION) == 1
        moved = str(flavor / "World of Warcraft Classic Beta.app").encode("utf-8")
        data = data.replace(LOCATION, json.dumps(moved.decode("utf-8"))[1:-1].encode("utf-8"))
    path = _folder() / "preferences.json"
    path.write_bytes(data)
    return path


def ok(*args: str) -> str:
    result = runner.invoke(cli.app, list(args), terminal_width=80)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    assert result.stderr == ""
    return result.stdout


def _report() -> cli.AddonsReport:
    return cli.AddonsReport.model_validate_json(ok("addons", "list", "--json"))


def _line(out: str, folder: str) -> str:
    [line] = [x for x in out.splitlines() if x.startswith(folder + "  ")]
    return line


# ─── the game folder WowUp's list was made for (`constructed` location) ──────


def test_the_beta_capture_is_joined_to_the_real_list_constructed(beta: Path, home: Path) -> None:
    path = _wowup()
    _settings(beta / BETA)
    sources = _report().sources
    # WowUp's list, then CurseForge's, which this home does not hold (M22-25).
    file, curseforge = sources.files
    assert (curseforge.manager, curseforge.state) == ("CurseForge", "missing")
    assert (file.state, file.path, file.written_ns) == ("read", str(path), WRITTEN_NS)
    assert len(file.records) == 6
    scope, _ = sources.scopes
    assert scope.manages is True and scope.others == () and scope.by_name == 0
    by_id = {m.record_id: m for m in sources.records}
    assert {m.installation for m in sources.records} == {"this_folder"}
    dbm = by_id[DBM]
    assert dbm.provider == "Wago" and dbm.installed_version == "12.1.11"
    assert dbm.folders_here == ("DBM-Brawlers",)
    assert dbm.version_check == "unknown"  # DBM-Brawlers' TOC gives no version
    assert dbm.stale_why == ("folders_missing",)
    dungeons = by_id[DUNGEONS]
    assert dungeons.folders_here == ("DBM-Challenges",)
    assert [v.version for v in dungeons.toc_versions] == ["r264"]
    assert dungeons.version_check == "agrees"
    assert dungeons.stale_why == ("folders_missing",)
    away = [m.name for m in sources.records if m.stale_why == ("no_folder_here",)]
    assert away == ["RareScanner", "Pawn", "Baganator", "Syndicator"]
    assert all(m.update_available is False for m in sources.records)
    assert [(f.folder, [c.record_id for c in f.claims], f.unclaimed) for f in sources.folders] == [
        ("DBM-Brawlers", [DBM], False),
        ("DBM-Challenges", [DUNGEONS], False),
    ]


def test_the_beta_capture_in_text_constructed(beta: Path, home: Path) -> None:
    _wowup()
    _settings(beta / BETA)
    out = ok("addons", "list")
    assert out.splitlines()[0].startswith("DBM-Brawlers  ")  # nothing to say first
    brawlers = _line(out, "DBM-Brawlers")
    assert "from Wago through WowUp, WowUp's record is out of date: it installed 12.1.11" in (
        brawlers
    )
    assert "not here: DBM-Core, DBM-GUI" in brawlers
    challenges = _line(out, "DBM-Challenges")
    assert "it installed DBM-Dungeons-r264;" in challenges
    assert "the TOC" not in challenges  # its version agrees
    assert "WowUp also lists RareScanner, Pawn, Baganator, Syndicator, with none of their" in out
    when = out.splitlines()[-2]
    assert when.startswith("WowUp's list: ~") and when.endswith(", 6 addons.")
    assert out.splitlines()[-1] == "4 notes: --notes shows them"
    assert "not in WowUp's list" not in out


# ─── the launch client, which WowUp never managed (the owner's case) ─────────


def test_a_folder_wowup_does_not_manage_gets_no_claim(forever: Path, home: Path) -> None:
    _wowup()
    _settings()
    sources = _report().sources
    scope, _ = sources.scopes
    assert scope.manages is False and scope.installation_ids == ()
    [other] = scope.others
    assert other.folder == "/Volumes/Labdisk/Games/World of Warcraft/_classic_beta_"
    assert other.records == 6
    assert {m.installation for m in sources.records} == {"other_folder"}
    assert not any(m.stale for m in sources.records)
    folders = {f.folder: f for f in sources.folders}
    assert all(f.claims == () and f.unclaimed is None for f in folders.values())
    assert [c.record_id for c in folders["Baganator"].same_name_as] == [BAGANATOR]
    assert folders["Auctionator"].same_name_as == ()
    out = ok("addons", "list")
    assert out.splitlines()[0] == (
        "WowUp does not manage this game folder; its list is for "
        "/Volumes/Labdisk/Games/World of Warcraft/_classic_beta_ (6 addons), and 1 folder "
        "here shares a name with one of them."
    )
    assert "through WowUp" not in out and "not in WowUp's list" not in out
    assert "out of date" not in out


# ─── a stale record and folders no one claims (`constructed` location) ───────


def test_a_stale_record_and_folders_no_one_claims_constructed(forever: Path, home: Path) -> None:
    _wowup()
    _settings(forever / FOREVER)
    sources = _report().sources
    baganator = next(m for m in sources.records if m.record_id == BAGANATOR)
    assert baganator.folders_here == ("Baganator",) and baganator.folders_missing == ()
    assert baganator.installed_version == "829"
    assert [(v.version, v.folders) for v in baganator.toc_versions] == [("850", ("Baganator",))]
    assert baganator.version_check == "differs"
    assert baganator.stale and baganator.stale_why == ("version_differs",)
    assert {f.folder: f.unclaimed for f in sources.folders} == {
        "Auctionator": True,
        "Baganator": False,
        "BanterBlocker": True,
    }
    out = ok("addons", "list")
    assert _line(out, "Baganator").endswith(
        "from Wago through WowUp, WowUp's record is out of date: it installed 829, "
        "the TOC here says 850"
    )
    assert _line(out, "Auctionator").endswith("not in WowUp's list")
    assert _line(out, "BanterBlocker").endswith("not in WowUp's list")


def test_several_folders_name_their_versions_constructed(forever: Path, home: Path) -> None:
    record = {
        "name": "Deadly Boss Mods",
        "providerName": "Wago",
        "installedVersion": "12.1.11",
        "installedFolderList": ["DBM-Core", "DBM-GUI", "DBM-VPVEM"],
    }
    _wowup(json.dumps({"d": record}).encode("utf-8"))
    addons = forever / FOREVER / "Interface" / "AddOns"
    for name, version in (
        ("DBM-Core", "12.1.25"),
        ("DBM-GUI", "12.1.25"),
        ("DBM-VPVEM", "a8e9f0e"),
    ):
        (addons / name).mkdir()
        (addons / name / f"{name}.toc").write_bytes(f"## Version: {version}\n".encode())
    line = _line(ok("addons", "list"), "DBM-Core")
    assert line.endswith(
        "it installed 12.1.11, the TOCs here say 12.1.25 (DBM-Core and 1 more), a8e9f0e (DBM-VPVEM)"
    )


# ─── no list, or one that cannot be read ─────────────────────────────────────


def test_with_no_wowup_list_nothing_is_said_about_sources(forever: Path, home: Path) -> None:
    sources = _report().sources
    file, curseforge = sources.files
    assert curseforge.state == "missing"
    assert file.state == "missing" and file.records == ()
    assert file.path == str(addonsource.wowup_folder() / "addons.json")
    assert sources.records == ()
    assert [f.unclaimed for f in sources.folders] == [None, None, None]
    out = ok("addons", "list")
    assert "WowUp" not in out and "manager" not in out and "notes" not in out
    assert len(out.splitlines()) == 3


def test_an_unreadable_list_is_said_once_constructed(forever: Path, home: Path) -> None:
    _wowup(b"{ not json")
    report = _report()
    file, _ = report.sources.files
    assert file.state == "unreadable" and file.error and file.error.startswith("not JSON")
    assert [f.unclaimed for f in report.sources.folders] == [None, None, None]
    out = ok("addons", "list")
    said = " ".join(out.split())  # the caveat is prose, wrapped to the width
    assert "wowlab could not read WowUp's list at ~" in said
    assert said.endswith(
        "addons.json: not JSON: Expecting property name enclosed in double "
        "quotes: line 1 column 3 (char 2). 4 notes: --notes shows them"
    )
    assert out.count("WowUp's list") == 1


def test_without_settings_records_are_matched_by_name_and_it_says_so_constructed(
    forever: Path, home: Path
) -> None:
    _wowup()
    sources = _report().sources
    assert sources.scopes[0].manages is None and sources.scopes[0].by_name == 6
    out = ok("addons", "list")
    assert " ".join(out.splitlines()[0:1]) == (
        "wowlab cannot tell which game folder WowUp's list is for (its settings file is not "
        "there), so its records are matched to these addons by folder name."
    )
    assert "it installed 829, the TOC here says 850" in _line(out, "Baganator")


# ─── a current record (`constructed`) ────────────────────────────────────────


def _pawn(forever: Path, **extra: object) -> None:
    record = {
        "name": "Pawn",
        "providerName": "Wago",
        "installedVersion": "Pawn 2.13.16",
        "latestVersion": "Pawn 2.13.17",
        "installedFolderList": ["Pawn"],
        **extra,
    }
    _wowup(json.dumps({"p": record}).encode("utf-8"))
    pawn = forever / FOREVER / "Interface" / "AddOns" / "Pawn"
    pawn.mkdir()
    (pawn / "Pawn.toc").write_bytes(b"## Interface: 16001\n## Title: Pawn\n## Version: 2.13.16\n")


def test_a_current_record_with_an_update_constructed(forever: Path, home: Path) -> None:
    _pawn(forever)
    [match] = _report().sources.records
    assert (match.version_check, match.stale, match.update_available) == ("agrees", False, True)
    line = _line(ok("addons", "list"), "Pawn")
    assert line.endswith("from Wago through WowUp, Pawn 2.13.16, update Pawn 2.13.17 available")


def test_an_ignored_record_says_so_instead_of_the_update_constructed(
    forever: Path, home: Path
) -> None:
    _pawn(forever, isIgnored=True)
    line = _line(ok("addons", "list"), "Pawn")
    assert line.endswith("from Wago through WowUp, Pawn 2.13.16, WowUp is set to leave it alone")
    assert "update" not in line


def test_an_unknown_provider_is_not_called_a_source_constructed(forever: Path, home: Path) -> None:
    _pawn(forever, providerName="Unknown")
    line = _line(ok("addons", "list"), "Pawn")
    assert "in WowUp's list, which does not know where it came from" in line
    assert "from Unknown" not in line


def test_the_lab_addon_is_not_called_unclaimed_constructed(forever: Path, home: Path) -> None:
    _wowup()
    _settings(forever / FOREVER)
    lab = forever / FOREVER / "Interface" / "AddOns" / "WowLab"
    lab.mkdir()
    (lab / "WowLab.toc").write_bytes(b"## Interface: 16001\n## Title: WowLab\n")
    [folder] = [f for f in _report().sources.folders if f.folder == "WowLab"]
    assert folder.lab_addon and folder.unclaimed is False
    assert "WowUp" not in _line(ok("addons", "list"), "WowLab")


# ─── reads only (L1), and the words a player reads ───────────────────────────


def _state(*roots: Path) -> dict[str, tuple[bytes | None, int]]:
    out: dict[str, tuple[bytes | None, int]] = {}
    for root in roots:
        for p in sorted([root, *root.rglob("*")]):
            st = p.lstat()
            out[str(p)] = (p.read_bytes() if p.is_file() else None, st.st_mtime_ns)
    return out


def test_listing_writes_nothing_in_the_install_or_wowups_folder(forever: Path, home: Path) -> None:
    path = _wowup()
    _settings()
    (path.parent / "sensitive.json").write_text("{}", encoding="utf-8")
    before = _state(forever, path.parent)
    ok("addons", "list")
    ok("addons", "list", "--json")
    ok("addons", "list", "--notes")
    assert _state(forever, path.parent) == before


@pytest.mark.parametrize("args", [(), ("--notes",)], ids=["default", "notes"])
@pytest.mark.parametrize("managed", [False, True], ids=["other-folder", "this-folder"])
def test_the_text_passes_the_player_copy_lint(
    forever: Path, home: Path, args: tuple[str, ...], managed: bool
) -> None:
    _wowup()
    _settings(forever / FOREVER if managed else None)
    out = ok("addons", "list", *args)
    assert hits(out) == []
    if args:
        assert "Notes:" in out and "--notes shows" not in out


def test_the_help_says_what_it_reads() -> None:
    out = ok("addons", "list", "--help")
    assert "WowUp" in out and "--notes" in out
    assert hits(out) == []


def test_a_second_game_folder_is_named_with_its_same_name_folders_constructed(
    forever: Path, home: Path, tmp_path: Path
) -> None:
    # Review round 2: WowUp manages this folder and another; the line about
    # the other also counts the folders here that share a name with it.
    here = forever / FOREVER
    there = forever / "_other_"
    records = {
        "mine": {
            "name": "Baganator",
            "installedVersion": "850",
            "installedFolderList": ["Baganator"],
            "installationId": "i-here",
        },
        "theirs": {
            "name": "Auctionator",
            "installedVersion": "1",
            "installedFolderList": ["Auctionator"],
            "installationId": "i-there",
        },
    }
    _wowup(json.dumps(records).encode("utf-8"))
    settings = {
        "wow_installations": [
            {"id": "i-here", "location": str(here / "World of Warcraft.app")},
            {"id": "i-there", "location": str(there / "World of Warcraft.app")},
        ]
    }
    (_folder() / "preferences.json").write_text(json.dumps(settings), encoding="utf-8")
    out = ok("addons", "list")
    said = " ".join(out.split())
    assert (
        f"WowUp's list also holds addons for {there} (1 addon), and 1 folder here shares a "
        "name with one of them." in said
    ), out
    assert "does not manage" not in out
