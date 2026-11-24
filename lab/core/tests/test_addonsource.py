"""`addonsource`: WowUp's records, read only, and the rule that joins them to
the addon folders (M22-16; LAB_IDEAS **addon-provenance**).

Graded on the real capture `fixtures/wowup/macos/addons.json` (the owner's
WowUp list: 6 Wago addons, last written 2026-09-26; row in
`fixtures/README.md`, L8) and, for the join, on `layout.Addon` models made
in the test, which are `constructed`. Hostile files (not JSON, too large,
nested too deeply, not an object) are `constructed` too. Nothing reads a
real WowUp folder: every folder is in `tmp_path`, and the user's folders
are redirected there.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from _user_dirs import redirect_user_dirs

from wowlab_core import addonsource, layout
from wowlab_core.toc import parse_toc

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "wowup" / "macos" / "addons.json"
PREFERENCES = FIXTURE.with_name("preferences.json")
INSTALLATION = "b83ae8a8-efcc-400b-b8c4-a1930a7863c7"
DBM = "808eed70-c097-4ed3-907d-c297a8ad6d63"
DUNGEONS = "378c7d50-15e5-4d85-a5b6-49704549b308"
BAGANATOR = "c3104847-a552-469a-b11e-6489348ad0a9"
NAMES = [
    "Deadly Boss Mods (DBM)",
    "RareScanner",
    "Deadly Boss Mods (DBM) - Dungeons, Delves, Visions, & Events",
    "Pawn",
    "Baganator",
    "Syndicator",
]


@pytest.fixture
def wowup(tmp_path: Path) -> Path:
    folder = tmp_path / "WowUp"
    folder.mkdir()
    shutil.copyfile(FIXTURE, folder / addonsource.RECORDS_FILE)
    return folder


def _addon(name: str, *tocs: tuple[str, str]) -> layout.Addon:
    """An addon folder as `layout` reports it, with `tocs` as (file, text)
    (`constructed`)."""
    made = tuple(
        layout.AddonToc(
            file=file,
            suffix=None,
            matches_folder=file.removesuffix(".toc").split("_")[0] == name,
            size=len(text),
            document=parse_toc(text.encode("utf-8")),
            error=None,
        )
        for file, text in tocs
    )
    return layout.Addon(
        name=name,
        path=f"Interface/AddOns/{name}",
        blizzard=False,
        tocs=made,
        selected_toc=made[0].file if made else None,
        selection="single" if made else "no_toc",
    )


def _versioned(name: str, version: str | None) -> layout.Addon:
    body = f"## Title: {name}\n" + (f"## Version: {version}\n" if version else "")
    return _addon(name, (f"{name}.toc", body))


# ─── reading the real capture ────────────────────────────────────────────────


def test_the_real_capture_reads_as_six_wago_records(wowup: Path) -> None:
    file = addonsource.read_wowup(wowup)
    assert file.state == "read" and file.error is None and file.skipped == 0
    assert file.manager == "WowUp"
    assert file.path == str(wowup / "addons.json")
    assert [r.name for r in file.records] == NAMES
    assert {r.provider for r in file.records} == {"Wago"}
    assert {r.client_type for r in file.records} == {5}
    assert all(r.ignored is False for r in file.records)
    by_id = {r.id: r for r in file.records}
    dbm = by_id[DBM]
    assert dbm.external_id == "qv633o6b"
    assert dbm.external_url == "https://addons.wago.io/addons/deadly-boss-mods-dbm"
    assert (dbm.installed_version, dbm.latest_version) == ("12.1.11", "12.1.11")
    assert dbm.installed_at == "2026-09-26T04:16:07.181Z"
    assert dbm.installation_id == "b83ae8a8-efcc-400b-b8c4-a1930a7863c7"
    assert dbm.folders == (
        "DBM-Brawlers",
        "DBM-Core",
        "DBM-GUI",
        "DBM-Lairs-Midnight",
        "DBM-Midnight",
        "DBM-Raids-Midnight",
        "DBM-StatusBarTimers",
        "DBM-Test",
        "DBM-VPVEM",
    )
    assert by_id[DUNGEONS].installed_version == "DBM-Dungeons-r264"
    assert "DBM-Challenges" in by_id[DUNGEONS].folders
    assert by_id[BAGANATOR].folders == ("Baganator",)
    assert by_id[BAGANATOR].installed_version == "829"


def test_the_download_link_is_never_read_into_a_model(wowup: Path) -> None:
    file = addonsource.read_wowup(wowup)
    dumped = json.dumps([r.model_dump(mode="json") for r in file.records])
    assert "download" not in dumped and "link=" not in dumped
    assert "cdn.wago.io" not in dumped  # thumbnails are not read either


def test_the_folder_list_wins_over_the_joined_string_constructed(tmp_path: Path) -> None:
    (tmp_path / "addons.json").write_text(
        json.dumps(
            {
                "a": {"installedFolderList": ["One", "Two"], "installedFolders": "Other"},
                "b": {"installedFolders": "Three, Four,,Three"},
                "c": {"installedFolders": 7, "clientType": True, "isIgnored": "yes"},
            }
        ),
        encoding="utf-8",
    )
    records = addonsource.read_wowup(tmp_path).records
    assert [r.folders for r in records] == [("One", "Two"), ("Three", "Four"), ()]
    assert records[2].client_type is None and records[2].ignored is None
    assert records[2].name is None and records[2].installed_version is None


def test_electron_stores_own_key_and_non_objects_are_skipped_constructed(tmp_path: Path) -> None:
    (tmp_path / "addons.json").write_text(
        json.dumps({"__internal__": {"migrations": {"version": "1"}}, "x": 3, "y": {}}),
        encoding="utf-8",
    )
    file = addonsource.read_wowup(tmp_path)
    assert [r.id for r in file.records] == ["y"]
    assert file.skipped == 2


# ─── where the folder is (L6: per OS, no flavor) ─────────────────────────────


def test_the_folder_is_the_roaming_config_folder_named_wowup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    redirect_user_dirs(monkeypatch, tmp_path / "home")
    found = addonsource.wowup_folder()
    assert found.name == "WowUp"
    assert (tmp_path / "home") in found.parents
    if sys.platform == "darwin":
        # Electron ignores XDG_CONFIG_HOME on macOS; platformdirs would follow it.
        assert found == tmp_path / "home" / "Library" / "Application Support" / "WowUp"
    else:
        assert found == platformdirs.user_config_path("WowUp", appauthor=False, roaming=True)
    if sys.platform == "win32":
        assert found == tmp_path / "home" / "appdata" / "WowUp"
    elif sys.platform != "darwin":
        assert found == tmp_path / "home" / "xdg_config_home" / "WowUp"


def test_the_macos_rule_ignores_xdg_config_home_on_any_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    redirect_user_dirs(monkeypatch, tmp_path / "home")
    found = addonsource.wowup_folder("darwin")
    assert found == tmp_path / "home" / "Library" / "Application Support" / "WowUp"


def test_the_default_folder_is_read_when_none_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    redirect_user_dirs(monkeypatch, tmp_path / "home")
    assert addonsource.read_wowup().state == "missing"
    folder = addonsource.wowup_folder()
    folder.mkdir(parents=True)
    shutil.copyfile(FIXTURE, folder / "addons.json")
    assert len(addonsource.read_wowup().records) == 6


# ─── a missing or unreadable file ────────────────────────────────────────────


def test_a_missing_file_is_missing_and_says_nothing_about_folders(tmp_path: Path) -> None:
    file = addonsource.read_wowup(tmp_path / "nowhere")
    assert file.state == "missing" and file.error is None and file.records == ()
    assert file.written_ns is None
    sources = addonsource.match_sources([_versioned("Pawn", "1")], [file])
    assert sources.records == ()
    [folder] = sources.folders
    assert folder.claims == () and folder.unclaimed is None


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (b"{not json", "not JSON"),
        (b"[1, 2]", "not a JSON object of addon records"),
        (b'"text"', "not a JSON object of addon records"),
        (b"\xff\xfe{}", "not UTF-8 text"),
        (b"[" * 200_000 + b"]" * 200_000, "nested too deeply"),
    ],
    ids=["not-json", "array", "string", "not-utf8", "deep"],
)
def test_an_unreadable_file_says_why_constructed(tmp_path: Path, body: bytes, why: str) -> None:
    (tmp_path / "addons.json").write_bytes(body)
    file = addonsource.read_wowup(tmp_path)
    assert file.state == "unreadable" and file.records == ()
    assert file.error is not None and file.error.startswith(why), file.error


def test_a_file_past_the_bound_is_not_read_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(addonsource, "MAX_RECORDS_BYTES", 100)
    (tmp_path / "addons.json").write_bytes(b"{" + b" " * 200 + b"}")
    file = addonsource.read_wowup(tmp_path)
    assert file.state == "unreadable" and file.error == "larger than 100 bytes"


def test_a_folder_in_place_of_the_file_is_unreadable_constructed(tmp_path: Path) -> None:
    (tmp_path / "addons.json").mkdir()
    file = addonsource.read_wowup(tmp_path)
    assert file.state == "unreadable" and file.error


def test_a_utf8_bom_is_accepted_constructed(tmp_path: Path) -> None:
    (tmp_path / "addons.json").write_bytes(b"\xef\xbb\xbf" + FIXTURE.read_bytes())
    assert len(addonsource.read_wowup(tmp_path).records) == 6


# ─── only addons.json and preferences.json are opened, read only (L1) ───────

_OPENED: list[tuple[str, Any, Any]] | None = None


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if _OPENED is not None and event == "open" and args and isinstance(args[0], (str, Path)):
        _OPENED.append((str(args[0]), args[1], args[2] if len(args) > 2 else None))


sys.addaudithook(_audit)


@pytest.fixture
def opened() -> Iterator[list[tuple[str, Any, Any]]]:
    global _OPENED
    _OPENED = []
    try:
        yield _OPENED
    finally:
        _OPENED = None


_WRITES = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def test_only_the_two_files_are_opened_and_only_to_read(
    wowup: Path, opened: list[tuple[str, Any, Any]]
) -> None:
    shutil.copyfile(PREFERENCES, wowup / "preferences.json")
    (wowup / "sensitive.json").write_text('{"secret": "never read"}', encoding="utf-8")
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in wowup.iterdir()}
    opened.clear()
    addonsource.read_wowup(wowup)
    seen = list(opened)
    assert {path for path, _, _ in seen} == {
        str(wowup / "addons.json"),
        str(wowup / "preferences.json"),
    }, seen
    for path, mode, flags in seen:
        assert mode in (None, "r", "rb"), (path, mode)
        assert flags is None or flags & _WRITES == 0, (path, flags)
        if hasattr(os, "O_NONBLOCK") and flags is not None:
            assert flags & os.O_NONBLOCK and flags & os.O_NOFOLLOW, (path, flags)
    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in wowup.iterdir()}
    assert after == before


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs here")
@pytest.mark.parametrize("name", ["addons.json", "preferences.json"])
def test_a_fifo_is_refused_without_waiting_constructed(wowup: Path, name: str) -> None:
    (wowup / name).unlink(missing_ok=True)
    os.mkfifo(wowup / name)
    file = addonsource.read_wowup(wowup)
    if name == "addons.json":
        assert file.state == "unreadable" and file.error == "not a regular file"
    else:
        assert file.state == "read"
        assert file.settings_state == "unreadable" and file.settings_error == "not a regular file"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
@pytest.mark.parametrize("name", ["addons.json", "preferences.json"])
def test_a_link_is_not_followed_constructed(wowup: Path, name: str) -> None:
    secret = wowup / "sensitive.json"
    secret.write_text('{"x": {"name": "SECRET"}}', encoding="utf-8")
    (wowup / name).unlink(missing_ok=True)
    (wowup / name).symlink_to(secret)
    file = addonsource.read_wowup(wowup)
    assert "SECRET" not in file.model_dump_json()
    if name == "addons.json":
        assert file.state == "unreadable" and file.error == "a link, not followed"
    else:
        assert file.settings_state == "unreadable"
        assert file.settings_error == "a link, not followed"


# ─── whether a TOC's version agrees with the recorded one ────────────────────


@pytest.mark.parametrize(
    ("toc", "recorded", "agree"),
    [
        ("12.1.11", "12.1.11", True),
        ("2.13.16", "Pawn 2.13.16", True),
        ("r264", "DBM-Dungeons-r264", True),
        ("5.5.4.1", "RareScanner_MISTS_5.5.4.1", True),
        ("v12.1.11", "12.1.11", True),
        ("R264", "r264", True),
        ("|cff00ff0012.1.11|r", "12.1.11", True),
        ("12.1.25", "12.1.11", False),
        ("868", "829", False),
        ("r273", "DBM-Dungeons-r264", False),
        ("a8e9f0e", "12.1.11", False),
        ("12.1.1", "12.1.11", False),
        ("1", "12.1.11", False),
        ("2.13", "Pawn 2.13.16", False),
        ("", "12.1.11", False),
        ("12.1.11", "  ", False),
    ],
)
def test_versions_agree(toc: str, recorded: str, agree: bool) -> None:
    assert addonsource.versions_agree(toc, recorded) is agree


# ─── the join (`constructed` folders) ────────────────────────────────────────


def _record(folders: list[str], installed: str | None, latest: str | None) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": "Thing", "providerName": "GitHub"}
    entry["installedFolderList"] = folders
    if installed is not None:
        entry["installedVersion"] = installed
    if latest is not None:
        entry["latestVersion"] = latest
    return entry


def _file(tmp_path: Path, records: dict[str, Any]) -> addonsource.ManagerFile:
    (tmp_path / "addons.json").write_text(json.dumps(records), encoding="utf-8")
    return addonsource.read_wowup(tmp_path)


def test_a_current_record_with_an_update_constructed(tmp_path: Path) -> None:
    file = _file(tmp_path, {"r": _record(["Thing", "Thing_Options"], "1.2.0", "1.3.0")})
    addons = [_versioned("Thing", "1.2.0"), _versioned("Thing_Options", None), _versioned("X", "9")]
    sources = addonsource.match_sources(addons, [file])
    [match] = sources.records
    assert match.folders_here == ("Thing", "Thing_Options") and match.folders_missing == ()
    assert match.version_check == "agrees"
    assert match.toc_versions == (addonsource.TocVersion(version="1.2.0", folders=("Thing",)),)
    assert match.stale is False and match.stale_why == ()
    assert match.update_available is True
    claims = {f.folder: (f.claims, f.unclaimed) for f in sources.folders}
    claim = addonsource.Claim(manager="WowUp", record_id="r")
    assert claims == {
        "Thing": ((claim,), False),
        "Thing_Options": ((claim,), False),
        "X": ((), True),
    }


def test_one_agreeing_folder_is_enough_constructed(tmp_path: Path) -> None:
    # DBM's voice pack carries a commit id where the core carries 12.1.x.
    file = _file(tmp_path, {"r": _record(["Core", "Voice"], "12.1.11", "12.1.11")})
    sources = addonsource.match_sources(
        [_versioned("Core", "12.1.11"), _versioned("Voice", "a8e9f0e")], [file]
    )
    [match] = sources.records
    assert match.version_check == "agrees" and not match.stale
    assert match.update_available is False


def test_a_stale_record_says_why_constructed(tmp_path: Path) -> None:
    file = _file(
        tmp_path,
        {
            "moved": _record(["Thing", "Gone"], "1.2.0", "1.2.0"),
            "newer": _record(["Other"], "829", "829"),
            "away": _record(["Elsewhere"], "1", "2"),
            "bare": _record(["Plain"], "1", None),
        },
    )
    addons = [_versioned("Thing", "1.2.0"), _versioned("Other", "868"), _versioned("Plain", None)]
    by_id = {m.record_id: m for m in addonsource.match_sources(addons, [file]).records}
    assert by_id["moved"].stale_why == ("folders_missing",)
    assert by_id["moved"].folders_missing == ("Gone",) and by_id["moved"].version_check == "agrees"
    assert by_id["newer"].stale_why == ("version_differs",)
    assert [v.version for v in by_id["newer"].toc_versions] == ["868"]
    assert by_id["away"].stale_why == ("no_folder_here",) and by_id["away"].folders_here == ()
    assert by_id["away"].version_check == "unknown"
    assert by_id["bare"].stale is False and by_id["bare"].version_check == "unknown"
    assert by_id["bare"].update_available is None


def test_folder_names_match_with_ascii_case_folded_constructed(tmp_path: Path) -> None:
    file = _file(tmp_path, {"r": _record(["thing"], "1", "1")})
    sources = addonsource.match_sources([_versioned("Thing", "1")], [file])
    assert sources.records[0].folders_here == ("Thing",)
    assert sources.folders[0].unclaimed is False


def test_the_lab_addon_is_never_unclaimed_constructed(tmp_path: Path) -> None:
    file = _file(tmp_path, {})
    sources = addonsource.match_sources([_versioned("WowLab", "1"), _versioned("X", "1")], [file])
    assert [(f.folder, f.unclaimed, f.lab_addon) for f in sources.folders] == [
        ("WowLab", False, True),
        ("X", True, False),
    ]


def test_an_unreadable_file_claims_nothing_and_judges_no_folder_constructed(
    tmp_path: Path,
) -> None:
    (tmp_path / "addons.json").write_bytes(b"oops")
    file = addonsource.read_wowup(tmp_path)
    sources = addonsource.match_sources([_versioned("X", "1")], [file])
    assert sources.folders[0].unclaimed is None


def test_two_records_may_claim_one_folder_constructed(tmp_path: Path) -> None:
    file = _file(tmp_path, {"a": _record(["X"], "1", "1"), "b": _record(["X"], "1", "1")})
    [folder] = addonsource.match_sources([_versioned("X", "1")], [file]).folders
    assert [c.record_id for c in folder.claims] == ["a", "b"]


# ─── the game folders WowUp manages (preferences.json) ───────────────────────


def test_the_real_settings_name_one_installation(wowup: Path) -> None:
    shutil.copyfile(PREFERENCES, wowup / "preferences.json")
    file = addonsource.read_wowup(wowup)
    assert file.settings_state == "read" and file.settings_error is None
    assert file.settings_path == str(wowup / "preferences.json")
    assert file.installations == (
        addonsource.Installation(
            id=INSTALLATION,
            location="/Volumes/Labdisk/Games/World of Warcraft/_classic_beta_/"
            "World of Warcraft Classic Beta.app",
        ),
    )
    # Every record of the real list is that installation's.
    assert {r.installation_id for r in file.records} == {INSTALLATION}
    # Only the id and the location are kept: nothing else of the settings.
    assert "install_id" not in file.model_dump_json()
    assert "displayName" not in file.model_dump_json()


def test_settings_that_are_missing_or_not_settings_constructed(wowup: Path) -> None:
    assert addonsource.read_wowup(wowup).settings_state == "missing"
    (wowup / "preferences.json").write_text('{"wow_installations": 3}', encoding="utf-8")
    file = addonsource.read_wowup(wowup)
    assert (file.settings_state, file.settings_error) == (
        "unreadable",
        "no list of game folders in it",
    )
    (wowup / "preferences.json").write_text(
        '{"wow_installations": [{"id": "a"}, 4, {"id": "b", "location": "/x/y.app"}]}',
        encoding="utf-8",
    )
    file = addonsource.read_wowup(wowup)
    assert file.installations == (addonsource.Installation(id="b", location="/x/y.app"),)


def _flavors(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "World of Warcraft"
    here, there = root / "_here_", root / "_there_"
    here.mkdir(parents=True)
    there.mkdir()
    return here, there


def _managed(tmp_path: Path, records: dict[str, Any], installs: list[dict[str, str]]) -> Any:
    folder = tmp_path / "WowUp"
    folder.mkdir(exist_ok=True)
    (folder / "addons.json").write_text(json.dumps(records), encoding="utf-8")
    (folder / "preferences.json").write_text(
        json.dumps({"other": 1, "wow_installations": installs}), encoding="utf-8"
    )
    return addonsource.read_wowup(folder)


def test_only_this_folders_records_are_joined_constructed(tmp_path: Path) -> None:
    here, there = _flavors(tmp_path)
    mine = {**_record(["Thing"], "1", "1"), "installationId": "i-here"}
    theirs = {**_record(["Thing", "Other"], "2", "2"), "installationId": "i-there"}
    loose = {**_record(["Loose"], "3", "3"), "installationId": "i-gone"}
    file = _managed(
        tmp_path,
        {"mine": mine, "theirs": theirs, "loose": loose},
        [
            {"id": "i-here", "location": str(here / "World of Warcraft.app")},
            {"id": "i-there", "location": str(there / "Wow.exe")},
        ],
    )
    addons = [_versioned("Thing", "1"), _versioned("Other", "9"), _versioned("Loose", "3")]
    sources = addonsource.match_sources(addons, [file], here)
    [scope] = sources.scopes
    assert scope.manages is True and scope.installation_ids == ("i-here",)
    assert scope.others == (
        addonsource.OtherInstallation(installation_id="i-there", folder=str(there), records=1),
    )
    assert scope.by_name == 1
    by_id = {m.record_id: m for m in sources.records}
    assert by_id["mine"].installation == "this_folder" and by_id["mine"].folders_here == ("Thing",)
    other = by_id["theirs"]
    assert other.installation == "other_folder" and other.installation_folder == str(there)
    assert other.folders_here == () and other.same_name_here == ("Thing", "Other")
    assert other.stale is False and other.version_check == "unknown"
    assert by_id["loose"].installation == "unknown" and by_id["loose"].folders_here == ("Loose",)
    claim = {
        m.record_id: addonsource.Claim(manager="WowUp", record_id=m.record_id)
        for m in by_id.values()
    }
    folders = {f.folder: f for f in sources.folders}
    assert folders["Thing"].claims == (claim["mine"],)
    assert folders["Thing"].same_name_as == (claim["theirs"],)
    assert folders["Other"].claims == () and folders["Other"].unclaimed is True
    assert folders["Other"].same_name_as == (claim["theirs"],)
    assert folders["Loose"].claims == (claim["loose"],)


def test_a_folder_wowup_does_not_manage_gets_no_claim_constructed(tmp_path: Path) -> None:
    here, there = _flavors(tmp_path)
    record = {**_record(["Thing"], "829", "829"), "installationId": "i-there"}
    file = _managed(tmp_path, {"r": record}, [{"id": "i-there", "location": str(there / "x.app")}])
    sources = addonsource.match_sources([_versioned("Thing", "868")], [file], here)
    assert sources.scopes[0].manages is False
    [match] = sources.records
    assert match.installation == "other_folder" and not match.stale
    [folder] = sources.folders
    assert folder.claims == () and folder.unclaimed is None
    assert [c.record_id for c in folder.same_name_as] == ["r"]


def test_the_location_may_be_the_flavor_folder_itself_constructed(tmp_path: Path) -> None:
    here, _ = _flavors(tmp_path)
    file = _managed(
        tmp_path,
        {"r": {**_record(["Thing"], "1", "1"), "installationId": "i"}},
        [{"id": "i", "location": str(here)}],
    )
    sources = addonsource.match_sources([_versioned("Thing", "1")], [file], here)
    assert sources.scopes[0].manages is True
    assert sources.records[0].installation == "this_folder"


def test_without_settings_records_are_joined_by_name_constructed(tmp_path: Path) -> None:
    here, _ = _flavors(tmp_path)
    file = _file(tmp_path, {"r": {**_record(["Thing"], "1", "1"), "installationId": "i"}})
    assert file.settings_state == "missing"
    sources = addonsource.match_sources([_versioned("Thing", "1")], [file], here)
    [scope] = sources.scopes
    assert scope.manages is None and scope.by_name == 1
    assert sources.records[0].installation == "unknown"
    assert sources.folders[0].claims and sources.folders[0].unclaimed is False


def test_the_real_list_against_a_folder_it_was_not_made_for(wowup: Path, tmp_path: Path) -> None:
    # The owner's case: WowUp's one installation is the beta's folder, and
    # this flavor folder is another (`constructed` folders, the real files).
    shutil.copyfile(PREFERENCES, wowup / "preferences.json")
    here, _ = _flavors(tmp_path)
    file = addonsource.read_wowup(wowup)
    sources = addonsource.match_sources([_versioned("Baganator", "868")], [file], here)
    [scope] = sources.scopes
    assert scope.manages is False and scope.by_name == 0
    assert scope.others == (
        addonsource.OtherInstallation(
            installation_id=INSTALLATION,
            folder="/Volumes/Labdisk/Games/World of Warcraft/_classic_beta_",
            records=6,
        ),
    )
    [folder] = sources.folders
    assert folder.claims == () and folder.unclaimed is None
    assert [c.record_id for c in folder.same_name_as] == [BAGANATOR]


# ─── what a record says ──────────────────────────────────────────────────────


def test_an_unknown_provider_is_not_a_source_constructed(tmp_path: Path) -> None:
    record = {**_record(["Thing"], "1", "1"), "providerName": "Unknown"}
    file = _file(tmp_path, {"r": record, "s": _record(["Other"], "1", "1")})
    sources = addonsource.match_sources([_versioned("Thing", "1")], [file])
    by_id = {m.record_id: m for m in sources.records}
    assert by_id["r"].provider == "Unknown" and by_id["r"].provider_known is False
    assert by_id["s"].provider_known is True


def test_an_ignored_record_keeps_wowups_view_constructed(tmp_path: Path) -> None:
    record = {**_record(["Thing"], "1", "2"), "isIgnored": True}
    [match] = addonsource.match_sources(
        [_versioned("Thing", "1")], [_file(tmp_path, {"r": record})]
    ).records
    assert match.ignored is True and match.update_available is True


def test_the_folder_naming_the_project_is_the_one_judged_constructed(tmp_path: Path) -> None:
    # DBM-Core's TOC names its Wago project; a folder left behind by a hand
    # update that still says the recorded version does not hide the change.
    record = {**_record(["Core", "Old"], "1.0", "1.0"), "providerName": "Wago", "externalId": "p1"}
    core = _addon("Core", ("Core.toc", "## Version: 2.0\n## X-Wago-ID: p1\n"))
    sources = addonsource.match_sources(
        [core, _versioned("Old", "1.0")], [_file(tmp_path, {"r": record})]
    )
    [match] = sources.records
    assert match.version_check == "differs" and match.stale_why == ("version_differs",)
    assert match.toc_versions == (addonsource.TocVersion(version="2.0", folders=("Core",)),)


def test_toc_versions_name_the_folders_that_carry_them_constructed(tmp_path: Path) -> None:
    record = _record(["A", "B", "C"], "1", "1")
    addons = [_versioned("A", "2"), _versioned("B", "2"), _versioned("C", "abc123")]
    [match] = addonsource.match_sources(addons, [_file(tmp_path, {"r": record})]).records
    assert match.toc_versions == (
        addonsource.TocVersion(version="2", folders=("A", "B")),
        addonsource.TocVersion(version="abc123", folders=("C",)),
    )


@pytest.mark.parametrize(
    ("toc", "recorded", "agree"),
    [
        ("1.0", "1.0.0", True),
        ("v2.0.0", "2", True),
        ("Pawn 2.13.0", "2.13", True),
        ("10", "1.0", False),
        ("12.1.26-alpha", "12.1.26", False),
        ("12.1.26", "12.1.26-beta2", False),
        ("12.1.26-1", "12.1.26", True),
        ("DBM-Dungeons-r264", "r264", True),
    ],
)
def test_versions_agree_at_the_edges(toc: str, recorded: str, agree: bool) -> None:
    assert addonsource.versions_agree(toc, recorded) is agree


def test_an_unlisted_installation_makes_the_folder_unknown_constructed(tmp_path: Path) -> None:
    # Review round 2: the settings list only another game folder and a
    # record names one they do not list; it is joined by name, so wowlab
    # cannot say WowUp does not manage this folder.
    here, there = _flavors(tmp_path)
    record = {**_record(["Thing"], "1", "1"), "installationId": "GONE"}
    file = _managed(tmp_path, {"r": record}, [{"id": "B", "location": str(there / "C.app")}])
    sources = addonsource.match_sources([_versioned("Thing", "1")], [file], here)
    [scope] = sources.scopes
    assert scope.manages is None and scope.by_name == 1 and scope.others == ()
    assert sources.records[0].installation == "unknown"
    assert sources.folders[0].claims and sources.folders[0].unclaimed is False
