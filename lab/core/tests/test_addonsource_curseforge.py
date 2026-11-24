"""The CurseForge app's records (M22-25): where it keeps them, how they are
read, and how they join the addon folders of the game folder they name,
beside WowUp's.

Graded on real captures: the owner's CurseForge records
(`fixtures/curseforge/macos/AddonGameInstance.json`, captured through
`scripts/lab_capture.py --file`; one game instance, `_forever_`, 22 addons),
the owner's WowUp list and settings (`fixtures/wowup/macos/`), and the
launch client's install capture (`fixtures/macos-70888/`, Auctionator 346,
Baganator 850 and BanterBlocker), each copied into `tmp_path`. Pairing a
record file with an install capture is `constructed`; so is moving
its `installPath` onto the copy (byte-level, that path only), and every file built here (a FIFO, a
link, an oversized file, a record with a name of its own); those test ids
say so. The user's folders are redirected into `tmp_path`: nothing reads a
real CurseForge or WowUp folder or a real install, and nothing writes to
one. The one exception is the owner-run `@live` test at the end, which
reads this machine's CurseForge records and install, read only.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import _user_dirs
import pytest
from _user_dirs import redirect_user_dirs
from test_cli_player_copy import hits
from typer.testing import CliRunner

from wowlab_core import addonsource, cli, install, layout
from wowlab_core.addonsource import file_name_has_version

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CURSEFORGE = FIXTURES / "curseforge" / "macos" / "AddonGameInstance.json"
WOWUP = FIXTURES / "wowup" / "macos" / "addons.json"
PREFERENCES = FIXTURES / "wowup" / "macos" / "preferences.json"
WOWUP_LOCATION = (
    b"/Volumes/Labdisk/Games/World of Warcraft/_classic_beta_/World of Warcraft Classic Beta.app"
)
# The game folder CurseForge names, as the capture tool left it.
INSTALL_PATH = b"/Volumes/Labdisk/Games/World of Warcraft/_forever_/"
FOREVER = "_forever_"  # the provenance rows' flavor folder; tests may name it (L6)
AUCTIONATOR = "119a315a-11ba-40e5-9374-b38fdae7caed"
WRITTEN_NS = 1_790_000_000_000_000_000

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    redirect_user_dirs(monkeypatch, folder)
    monkeypatch.delenv(install.ENV_FLAVOR, raising=False)
    return folder


@pytest.fixture
def forever(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The launch client's capture as `_forever_`: its flavor folder."""
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / "macos-70888" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos-70888" / "forever", root / FOREVER)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    return root / FOREVER


def _app_folder() -> Path:
    folder = addonsource.curseforge_folder()
    tmp = Path(os.environ["HOME"]).parent
    assert tmp in folder.parents, f"{folder} is not under this test's tmp_path"
    return folder


def _records_path(base: Path) -> Path:
    return base.joinpath(*addonsource.CURSEFORGE_RECORDS)


def _json_path(path: Path) -> bytes:
    return json.dumps(str(path) + "/")[1:-1].encode("utf-8")


def _curseforge(flavor: Path | None = None, body: bytes | None = None) -> Path:
    """CurseForge's records where this machine's app keeps them: the real
    file, or with its one `installPath` moved onto `flavor` (`constructed`)."""
    data = CURSEFORGE.read_bytes() if body is None else body
    if flavor is not None:
        assert data.count(b'"installPath":"' + INSTALL_PATH + b'"') == 1
        data = data.replace(INSTALL_PATH, _json_path(flavor))
    path = _records_path(_app_folder())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, ns=(WRITTEN_NS, WRITTEN_NS))
    return path


def _wowup(flavor: Path) -> None:
    """WowUp's real list, with its one installation moved into `flavor`."""
    folder = addonsource.wowup_folder()
    assert Path(os.environ["HOME"]).parent in folder.parents
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(WOWUP, folder / "addons.json")
    data = PREFERENCES.read_bytes()
    assert data.count(WOWUP_LOCATION) == 1
    moved = json.dumps(str(flavor / "World of Warcraft Classic Beta.app"))[1:-1]
    (folder / "preferences.json").write_bytes(data.replace(WOWUP_LOCATION, moved.encode()))


def ok(*args: str) -> str:
    result = runner.invoke(cli.app, list(args), terminal_width=200)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    assert result.stderr == ""
    return result.stdout


def _report() -> cli.AddonsReport:
    return cli.AddonsReport.model_validate_json(ok("addons", "list", "--json"))


def _line(out: str, folder: str) -> str:
    [line] = [x for x in out.splitlines() if x.startswith(folder + "  ")]
    return line


# ─── where the app keeps its records ─────────────────────────────────────────


def test_the_folder_is_electrons_user_data_folder_for_the_app(home: Path) -> None:
    mac = addonsource.curseforge_folder("darwin")
    assert mac == home / "Library" / "Application Support" / "CurseForge"
    assert addonsource.curseforge_folder("linux").name == "CurseForge"


def test_the_real_records_are_read(home: Path) -> None:
    path = _curseforge()
    file = addonsource.read_curseforge()
    assert (file.manager, file.state, file.error) == ("CurseForge", "read", None)
    assert file.path == str(path) and file.settings_path == str(path)
    assert file.written_ns == WRITTEN_NS
    assert len(file.records) == 22 and file.skipped == 0
    [instance] = file.installations
    assert instance.location == INSTALL_PATH.decode() and instance.location_is_folder
    assert {r.installation_id for r in file.records} == {instance.id}
    auctionator = next(r for r in file.records if r.id == AUCTIONATOR)
    assert auctionator.name == "Auctionator" and auctionator.provider == "CurseForge"
    assert auctionator.external_id == "6124"
    assert auctionator.external_url == "https://www.curseforge.com/wow/addons/auctionator"
    assert auctionator.installed_version == "Auctionator-352.zip"
    assert auctionator.latest_version == "Auctionator-352.zip"
    assert auctionator.installed_file_date == auctionator.latest_file_date
    assert (auctionator.installed_file_date or "").startswith("2026-11-")
    assert auctionator.installed_file_id == auctionator.latest_file_id == 9761689
    assert auctionator.folders == ("Auctionator",)
    assert auctionator.ignored is False and auctionator.version_is_file_name
    dbm = next(r for r in file.records if r.external_id == "314994")
    assert len(dbm.folders) == 17 and dbm.folders[0] == "DBM-Party-Vanilla"
    # Questie's file carries QuestieTrace too: two records list that folder.
    trace = [r for r in file.records if "QuestieTrace" in r.folders]
    assert sorted(r.installed_version or "" for r in trace) == [
        "Questie-v12.13.0.zip",
        "QuestieTrace-v1.10.0.zip",
    ]


def test_a_missing_app_folder_is_missing(home: Path) -> None:
    assert not _app_folder().exists()
    file = addonsource.read_curseforge()
    assert file.state == "missing" and file.records == () and file.installations == ()
    # The app folder without its records: missing too.
    (_app_folder() / "agent").mkdir(parents=True)
    assert addonsource.read_curseforge().state == "missing"


# What the audit hook below records, only while `_OPENS["on"]` is set: the
# hook stays for the life of the process, so it must cost nothing otherwise.
_OPENS: dict[str, object] = {"on": False, "seen": []}


def _audit(event: str, args: tuple[object, ...]) -> None:
    if _OPENS["on"] and event == "open" and args and isinstance(args[0], (str, os.PathLike)):
        seen = _OPENS["seen"]
        assert isinstance(seen, list)
        seen.append(os.fspath(args[0]))  # type: ignore[arg-type]


def test_only_the_records_file_is_opened_constructed(home: Path) -> None:
    path = _curseforge()
    base = _app_folder()
    secrets = [base / "Cookies", base / "Local Storage" / "leveldb" / "000003.log"]
    secrets.append(base / "agent" / "database" / "app.db")
    for secret in secrets:
        secret.parent.mkdir(parents=True, exist_ok=True)
        secret.write_bytes(b"secret")
    if not _OPENS.get("hooked"):
        sys.addaudithook(_audit)
        _OPENS["hooked"] = True
    _OPENS["seen"] = []
    _OPENS["on"] = True
    try:
        addonsource.read_curseforge()
    finally:
        _OPENS["on"] = False
    seen = _OPENS["seen"]
    assert isinstance(seen, list)
    assert [p for p in seen if Path(p).is_relative_to(base)] == [str(path)]


# ─── a hostile path (constructed) ────────────────────────────────────────────


def test_a_link_in_place_of_the_records_is_not_followed_constructed(
    home: Path, tmp_path: Path
) -> None:
    real = tmp_path / "elsewhere.json"
    real.write_bytes(CURSEFORGE.read_bytes())
    path = _records_path(_app_folder())
    path.parent.mkdir(parents=True)
    try:
        path.symlink_to(real)
    except OSError:
        pytest.skip("symbolic links are not available here")
    file = addonsource.read_curseforge()
    assert file.state == "unreadable" and file.error == "a link, not followed"
    assert file.records == ()


def test_a_linked_folder_on_the_way_is_not_followed_constructed(home: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "agent-elsewhere"
    (elsewhere / "GameInstances").mkdir(parents=True)
    (elsewhere / "GameInstances" / "AddonGameInstance.json").write_bytes(CURSEFORGE.read_bytes())
    base = _app_folder()
    base.mkdir(parents=True)
    try:
        (base / "agent").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are not available here")
    file = addonsource.read_curseforge()
    assert file.state == "unreadable" and file.error == "agent is a link, not followed"


BLOCKED_AFTER = 3.0


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs here")
def test_a_fifo_in_place_of_the_records_does_not_block_constructed(home: Path) -> None:
    path = _records_path(_app_folder())
    path.parent.mkdir(parents=True)
    os.mkfifo(path)
    done = threading.Event()

    def unblock() -> None:
        if not done.wait(BLOCKED_AFTER):
            os.close(os.open(path, os.O_WRONLY))

    watchdog = threading.Thread(target=unblock, daemon=True)
    watchdog.start()
    start = time.monotonic()
    try:
        file = addonsource.read_curseforge()
    finally:
        done.set()
        watchdog.join(BLOCKED_AFTER + 1)
    assert time.monotonic() - start < BLOCKED_AFTER
    assert file.state == "unreadable" and file.error == "not a regular file"


def test_an_oversized_file_is_not_read_constructed(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _curseforge()
    monkeypatch.setattr(addonsource, "MAX_CURSEFORGE_BYTES", 1024)
    file = addonsource.read_curseforge()
    assert file.state == "unreadable" and file.error == "larger than 1024 bytes"


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (b"{}", "not a JSON list of game folders"),
        (b"[{", "not JSON"),
        (b"\xff\xfe[]", "not UTF-8 text"),
    ],
    ids=["object", "broken", "not-utf8"],
)
def test_a_file_that_is_not_records_is_unreadable_constructed(
    home: Path, body: bytes, error: str
) -> None:
    _curseforge(body=body)
    file = addonsource.read_curseforge()
    assert file.state == "unreadable" and (file.error or "").startswith(error)


def test_odd_entries_are_skipped_not_fatal_constructed(home: Path) -> None:
    body = [
        "not an instance",
        {"guid": "g", "installPath": "/x/", "installedAddons": [7, {"name": "Bare"}]},
        {"installedAddons": [{"name": "Nowhere", "addonID": 5}]},
    ]
    _curseforge(body=json.dumps(body).encode())
    file = addonsource.read_curseforge()
    assert file.state == "read" and file.skipped == 2
    bare, nowhere = file.records
    assert (bare.id, bare.installation_id, bare.folders) == ("#0", "g", ())
    assert (nowhere.id, nowhere.installation_id, nowhere.external_id) == ("None/5", None, "5")


# ─── a file name against a TOC's version ─────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "version", "agrees"),
    [
        ("Auctionator-352", "352", True),
        ("Auctionator-352", "346", False),
        ("ForeverLevelingBiS-v1.4.7-forever", "v1.4.7", True),
        ("QuestieDB-1.1.5-Forever", "1.1.5", True),
        ("BroodBGs-v0.20.0-beta", "v0.20.0-beta", True),
        ("Spy-1.3.8-forever29", "1.3.8-forever29", True),
        ("DBM-Dungeons-r273", "r273", True),
        ("forever_thanks-0.1.0.21", "0.1.0.21", True),
        ("QuestieTrace-v1.10.0", "1.14.0", False),
        ("UpgradeScout-2.11.0-forever", "2.11.0", True),
        ("Questie-v12.13.0", "12.13.0", True),
        ("Thing-2.11.05", "2.11.0", False),
        ("Thing-12.1.25", "12.1.2", False),
        ("Thing-1.10", "1.1", False),
        ("LFGForever", "2.5.0", None),
        ("BanterBlocker", "2.0.1", None),
    ],
)
def test_a_file_name_carries_a_version_between_separators(
    name: str, version: str, agrees: bool | None
) -> None:
    assert file_name_has_version(name, version) is agrees


# ─── joined to the game folder it names ──────────────────────────────────────


def test_records_join_the_folder_their_instance_names_constructed(
    forever: Path, home: Path
) -> None:
    _curseforge(forever)
    sources = _report().sources
    wowup, file = sources.files
    assert (wowup.state, file.state) == ("missing", "read")
    _, scope = sources.scopes
    assert scope.manages is True and scope.others == () and scope.by_name == 0
    assert sources.judged_by == ("CurseForge",)
    by_name = {m.name: m for m in sources.records}
    auctionator = by_name["Auctionator"]
    assert auctionator.installation == "this_folder"
    assert auctionator.folders_here == ("Auctionator",)
    assert [v.version for v in auctionator.toc_versions] == ["346"]
    assert auctionator.version_check == "differs"  # the capture is older than the record
    assert auctionator.stale_why == ("version_differs",)
    assert auctionator.update_available is False
    baganator = by_name["Baganator"]
    assert baganator.installed_version == "Baganator-868.zip" and baganator.stale
    assert auctionator.version_is_file_name and auctionator.version_in_name is True
    banter = by_name["BanterBlocker"]
    assert banter.version_check == "unknown" and not banter.stale  # no version in its name
    assert banter.version_in_name is False
    away = [m for m in sources.records if m.stale_why == ("no_folder_here",)]
    assert len(away) == 19
    assert [(f.folder, f.managers, f.unclaimed) for f in sources.folders] == [
        ("Auctionator", ("CurseForge",), False),
        ("Baganator", ("CurseForge",), False),
        ("BanterBlocker", ("CurseForge",), False),
    ]


def test_the_records_in_text_constructed(forever: Path, home: Path) -> None:
    _curseforge(forever)
    (forever / "Interface" / "AddOns" / "ByHand").mkdir()
    (forever / "Interface" / "AddOns" / "ByHand" / "ByHand.toc").write_bytes(b"## Version: 1\n")
    out = ok("addons", "list")
    assert _line(out, "Auctionator").endswith(
        "CurseForge's Auctionator record no longer matches this folder: it installed "
        "file Auctionator-352.zip, the TOC here says 346"
    )
    assert _line(out, "BanterBlocker").endswith(
        "through the CurseForge app, file BanterBlocker.zip names no version; not compared"
    )
    assert _line(out, "ByHand").endswith("not in CurseForge's list")
    assert "CurseForge also lists BroodBGs, QuestieTrace," in " ".join(out.split())
    when = out.splitlines()[-2]
    assert when.startswith("CurseForge's list: ~") and when.endswith(", 22 addons.")
    assert "AddonGameInstance.json" in when
    assert out.splitlines()[-1] == "4 notes: --notes shows them"
    assert hits(out) == [] and hits(ok("addons", "list", "--notes")) == []


def test_records_of_another_game_folder_are_hints_only_constructed(
    forever: Path, home: Path
) -> None:
    # The real file as captured: its game folder is the owner's `_forever_`,
    # not this copy.
    _curseforge()
    sources = _report().sources
    _, scope = sources.scopes
    assert scope.manages is False and scope.installation_ids == ()
    [other] = scope.others
    assert other.folder == "/Volumes/Labdisk/Games/World of Warcraft/_forever_"
    assert other.records == 22
    assert all(f.claims == () and f.unclaimed is None for f in sources.folders)
    assert [len(f.same_name_as) for f in sources.folders] == [1, 1, 1]
    out = ok("addons", "list")
    assert out.splitlines()[0] == (
        "CurseForge does not manage this game folder; its list is for "
        "/Volumes/Labdisk/Games/World of Warcraft/_forever_ (22 addons), and 3 folders here "
        "share a name with one of them."
    )
    assert "through the CurseForge app" not in out and "not in" not in out


def test_a_folder_both_managers_claim_says_so_constructed(forever: Path, home: Path) -> None:
    _curseforge(forever)
    _wowup(forever)
    report = _report()
    sources = report.sources
    assert [f.manager for f in sources.files] == ["WowUp", "CurseForge"]
    assert sources.judged_by == ("WowUp", "CurseForge")
    folders = {f.folder: f for f in sources.folders}
    baganator = folders["Baganator"]
    assert baganator.managers == ("WowUp", "CurseForge")
    assert [c.manager for c in baganator.claims] == ["WowUp", "CurseForge"]
    assert folders["Auctionator"].managers == ("CurseForge",)
    line = _line(ok("addons", "list"), "Baganator")
    assert (
        "WowUp's Baganator record (from Wago) no longer matches this folder: it installed 829"
        in (line)
    )
    assert "CurseForge's Baganator record no longer matches this folder: it installed file" in line
    assert line.endswith("both WowUp and CurseForge list it")
    raw = json.loads(ok("addons", "list", "--json"))
    assert raw["sources"]["judged_by"] == ["WowUp", "CurseForge"]
    assert [f["managers"] for f in raw["sources"]["folders"]] == [
        ["CurseForge"],
        ["WowUp", "CurseForge"],
        ["CurseForge"],
    ]


def test_an_unreadable_records_file_is_said_once_constructed(forever: Path, home: Path) -> None:
    _curseforge(body=b"{}")
    out = ok("addons", "list")
    said = " ".join(out.split())
    assert "wowlab could not read CurseForge's list at ~" in said
    assert "AddonGameInstance.json: not a JSON list of game folders." in said
    assert out.count("CurseForge's list") == 1


def test_a_record_naming_an_unlisted_instance_is_joined_by_name_constructed(
    forever: Path, home: Path
) -> None:
    body = [
        {
            "installedAddons": [
                {
                    "name": "Baganator",
                    "gameInstanceID": "gone",
                    "installedFile": {
                        "id": 1,
                        "fileName": "Baganator-850.zip",
                        "modules": [{"foldername": "Baganator"}],
                    },
                    "latestFile": {"id": 2, "fileName": "Baganator-851.zip"},
                }
            ]
        }
    ]
    _curseforge(body=json.dumps(body).encode())
    sources = _report().sources
    _, scope = sources.scopes
    assert scope.manages is None and scope.by_name == 1
    [match] = sources.records
    assert match.installation == "unknown" and match.version_check == "agrees"
    assert match.update_available is None and match.other_file_listed  # no dates
    out = ok("addons", "list")
    assert out.splitlines()[0] == (
        "wowlab cannot tell which game folder CurseForge's list is for (1 record names a game "
        "folder its list does not describe), so its records are matched to these addons by "
        "folder name."
    )
    assert _line(out, "Baganator").endswith(
        "through the CurseForge app, file Baganator-850.zip, CurseForge lists another file, "
        "Baganator-851.zip"
    )


def test_listing_writes_nothing_in_the_install_or_the_apps_folder(
    forever: Path, home: Path
) -> None:
    path = _curseforge(forever)

    def state() -> dict[str, tuple[bytes | None, int]]:
        out: dict[str, tuple[bytes | None, int]] = {}
        for root in (forever, _app_folder()):
            for p in sorted([root, *root.rglob("*")]):
                st = p.lstat()
                out[str(p)] = (p.read_bytes() if p.is_file() else None, st.st_mtime_ns)
        return out

    before = state()
    ok("addons", "list")
    ok("addons", "list", "--json")
    assert state() == before and path.is_file()


# ─── no test reads this machine's records (the root conftest's guard) ────────


def test_every_test_is_kept_out_of_the_real_folders() -> None:
    """This test leaves the home folder as it is: the rules it sees still
    never name the real folders, which the session noted before it ran."""
    real = set(_user_dirs.note_real_manager_folders())
    assert real, "the session noted the real folders"
    for rule in (addonsource.wowup_folder, addonsource.curseforge_folder):
        assert hasattr(rule, "__wrapped__"), "the rule is wrapped for every test"
        assert str(rule()) not in real and os.path.realpath(rule()) not in real


def test_the_guard_records_an_open_inside_a_real_folder_constructed() -> None:
    """Positive control for the audit hook: an open of a path inside a real
    folder is recorded (fed to the hook directly: nothing is opened)."""
    [first, *_] = _user_dirs.note_real_manager_folders()
    inside = str(Path(first, "agent", "GameInstances", "AddonGameInstance.json"))
    _user_dirs.audit_manager_opens("open", (inside, "r", 0))
    _user_dirs.audit_manager_opens("open", (first + "-elsewhere", "r", 0))
    _user_dirs.audit_manager_opens("open", (7, "r", 0))
    try:
        assert [inside] == _user_dirs.MANAGER_OPENS
    finally:
        del _user_dirs.MANAGER_OPENS[:]  # this test opened nothing


def test_a_moved_home_keeps_its_own_folder(home: Path) -> None:
    assert addonsource.curseforge_folder("darwin") == (
        home / "Library" / "Application Support" / "CurseForge"
    )


# ─── the owner's machine (run by hand) ───────────────────────────────────────


@pytest.mark.live
def test_live_this_machines_records_read_and_join_its_install() -> None:
    """Owner-run: `uv run pytest -m live lab/core/tests/test_addonsource_curseforge.py`
    with the CurseForge app installed. Reads only: its records and the
    install that `WOWLAB_WOW_ROOT` (or discovery) names."""
    file = addonsource.read_curseforge()
    if file.state == "missing":
        pytest.skip("no CurseForge records on this machine")
    assert file.state == "read", file.error
    assert file.records and file.installations
    assert all(r.folders for r in file.records)
    found = install.read_install(install.resolve_root())
    joined = 0
    for flavor in found.flavors:
        lay = layout.Layout.for_flavor(flavor, found.root)
        sources = addonsource.match_sources(list(lay.addons()), (file,), lay.flavor_path)
        joined += sum(1 for m in sources.records if m.installation == "this_folder")
    assert joined, "no record joined any flavor folder of this install"


# ─── a folder another record installs, and the update rule (review round 1) ──


def test_a_folder_another_record_installs_names_that_record_constructed(
    forever: Path, home: Path
) -> None:
    """The owner's case: Questie's file carries QuestieTrace, and the folder
    on disk is Questie's copy (1.14.0), not QuestieTrace's own (v1.10.0)."""
    _curseforge(forever)
    addons = forever / "Interface" / "AddOns"
    for name, version in (("Questie", "12.13.0"), ("QuestieTrace", "1.14.0")):
        (addons / name).mkdir()
        (addons / name / f"{name}.toc").write_bytes(f"## Version: {version}\n".encode())
    sources = _report().sources
    by_name = {m.name: m for m in sources.records}
    trace, questie = by_name["QuestieTrace"], by_name["Questie"]
    assert trace.stale_why == ("version_differs",)
    assert trace.also_installed_by == (questie.record_id,)
    assert questie.also_installed_by == (trace.record_id,) and not questie.stale
    line = _line(ok("addons", "list"), "QuestieTrace")
    assert (
        "CurseForge's QuestieTrace record no longer matches this folder: it installed file "
        "QuestieTrace-v1.10.0.zip, the TOC here says 1.14.0; CurseForge's Questie record "
        "(file Questie-v12.13.0.zip) also installs this folder  through the CurseForge app, "
        "file Questie-v12.13.0.zip"
    ) in line


def _one_record(installed: dict[str, object], latest: dict[str, object]) -> bytes:
    record = {
        "name": "Baganator",
        "gameInstanceID": "g",
        "installedFile": {**installed, "modules": [{"foldername": "Baganator"}]},
        "latestFile": latest,
    }
    return json.dumps([{"guid": "g", "installedAddons": [record]}]).encode()


@pytest.mark.parametrize(
    ("latest_date", "update", "other"),
    [
        ("2026-11-24T00:00:00Z", True, False),
        ("2026-11-01T00:00:00Z", None, True),
        (None, None, True),
    ],
    ids=["dated-later", "dated-earlier", "undated"],
)
def test_another_file_is_an_update_only_when_dated_later_constructed(
    forever: Path, home: Path, latest_date: str | None, update: bool | None, other: bool
) -> None:
    installed = {"id": 1, "fileName": "Baganator-850.zip", "fileDate": "2026-11-10T00:00:00Z"}
    latest: dict[str, object] = {"id": 2, "fileName": "Baganator-851.zip"}
    if latest_date is not None:
        latest["fileDate"] = latest_date
    _curseforge(body=_one_record(installed, latest))
    [match] = _report().sources.records
    assert (match.update_available, match.other_file_listed) == (update, other)
    line = _line(ok("addons", "list"), "Baganator")
    if update:
        assert line.endswith("update file Baganator-851.zip available")
    else:
        assert line.endswith("CurseForge lists another file, Baganator-851.zip")


def test_the_same_file_is_no_update_constructed(forever: Path, home: Path) -> None:
    same = {"id": 1, "fileName": "Baganator-850.zip", "fileDate": "2026-11-10T00:00:00Z"}
    _curseforge(body=_one_record(same, same))
    [match] = _report().sources.records
    assert (match.update_available, match.other_file_listed) == (False, False)


def test_an_ignored_record_is_said_as_the_apps_mark_constructed(forever: Path, home: Path) -> None:
    body = json.loads(_one_record({"id": 1, "fileName": "Baganator-850.zip"}, {"id": 1}))
    body[0]["installedAddons"][0]["preferenceIsIgnored"] = True
    _curseforge(body=json.dumps(body).encode())
    line = _line(ok("addons", "list"), "Baganator")
    assert line.endswith("file Baganator-850.zip, CurseForge marks it ignored")
