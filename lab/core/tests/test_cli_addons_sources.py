"""`wowlab addons list`: where each addon came from, from WowUp's records
(M22-16).

Graded on real captures joined to each other (L8): WowUp's list of the
owner's machine (`fixtures/wowup/macos/addons.json`, 6 Wago addons, last
written 2026-09-26) beside the two captured installs, each copied into
`tmp_path`:

- `fixtures/macos/` as `_classic_beta_`, the game folder that WowUp list
  was made for: `DBM-Brawlers` (no `## Version:`) and `DBM-Challenges`
  (`r264`, which agrees with the record's `DBM-Dungeons-r264`), each of a
  record whose other folders were not captured;
- `fixtures/macos-70888/` as `_forever_`, the launch client, captured after
  the list was last written: `Baganator` at 850 where WowUp installed 829,
  so the record is out of date, and `Auctionator` and `BanterBlocker`,
  which no record claims.

Pairing a WowUp list with an install capture is `constructed`; so is
anything added to the copies (a folder, a TOC, a record), and those test
ids say so. The user's folders are redirected into `tmp_path`, so the WowUp
folder read is the test's own; nothing reads a real WowUp folder or a real
install, and nothing writes to either.
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


def _wowup(body: bytes | None = None) -> Path:
    """WowUp's list where this machine's WowUp keeps it (in the redirected home)."""
    folder = addonsource.wowup_folder()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "addons.json"
    path.write_bytes(WOWUP.read_bytes() if body is None else body)
    os.utime(path, ns=(WRITTEN_NS, WRITTEN_NS))
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


# ─── the game folder WowUp's list was made for ───────────────────────────────


def test_the_beta_capture_is_joined_to_the_real_list(beta: Path, home: Path) -> None:
    path = _wowup()
    report = _report()
    sources = report.sources
    [file] = sources.files
    assert (file.state, file.path, file.written_ns) == ("read", str(path), WRITTEN_NS)
    assert len(file.records) == 6
    by_id = {m.record_id: m for m in sources.records}
    dbm = by_id[DBM]
    assert dbm.provider == "Wago" and dbm.installed_version == "12.1.11"
    assert dbm.folders_here == ("DBM-Brawlers",)
    assert dbm.version_check == "unknown"  # DBM-Brawlers' TOC gives no version
    assert dbm.stale_why == ("folders_missing",)
    dungeons = by_id[DUNGEONS]
    assert dungeons.folders_here == ("DBM-Challenges",)
    assert dungeons.toc_versions == ("r264",) and dungeons.version_check == "agrees"
    assert dungeons.stale_why == ("folders_missing",)
    away = [m.name for m in sources.records if m.stale_why == ("no_folder_here",)]
    assert away == ["RareScanner", "Pawn", "Baganator", "Syndicator"]
    assert all(m.update_available is False for m in sources.records)
    assert [(f.folder, [c.record_id for c in f.claims], f.unclaimed) for f in sources.folders] == [
        ("DBM-Brawlers", [DBM], False),
        ("DBM-Challenges", [DUNGEONS], False),
    ]


def test_the_beta_capture_in_text(beta: Path, home: Path) -> None:
    _wowup()
    out = ok("addons", "list")
    brawlers = _line(out, "DBM-Brawlers")
    assert "from Wago through WowUp, WowUp's record is out of date: it installed 12.1.11" in (
        brawlers
    )
    assert "not here: DBM-Core, DBM-GUI" in brawlers
    challenges = _line(out, "DBM-Challenges")
    assert "it installed DBM-Dungeons-r264;" in challenges
    assert "the TOC says" not in challenges  # its version agrees
    assert "WowUp also lists RareScanner, Pawn, Baganator, Syndicator, with none of their" in out
    when = out.splitlines()[-2]
    assert when.startswith("WowUp's list: ~") and when.endswith(", 6 addons.")
    assert out.splitlines()[-1] == "4 notes: --notes shows them"
    assert "in no addon manager's list" not in out


# ─── the launch client, captured after the list was written ──────────────────


def test_a_stale_record_and_folders_no_one_claims(forever: Path, home: Path) -> None:
    _wowup()
    sources = _report().sources
    baganator = next(m for m in sources.records if m.record_id == BAGANATOR)
    assert baganator.folders_here == ("Baganator",) and baganator.folders_missing == ()
    assert baganator.installed_version == "829" and baganator.toc_versions == ("850",)
    assert baganator.version_check == "differs"
    assert baganator.stale and baganator.stale_why == ("version_differs",)
    assert {f.folder: f.unclaimed for f in sources.folders} == {
        "Auctionator": True,
        "Baganator": False,
        "BanterBlocker": True,
    }
    out = ok("addons", "list")
    assert _line(out, "Baganator").endswith(
        "from Wago through WowUp, WowUp's record is out of date: it installed 829, the TOC says 850"
    )
    assert _line(out, "Auctionator").endswith("in no addon manager's list")
    assert _line(out, "BanterBlocker").endswith("in no addon manager's list")


# ─── no list, or one that cannot be read ─────────────────────────────────────


def test_with_no_wowup_list_nothing_is_said_about_sources(forever: Path, home: Path) -> None:
    sources = _report().sources
    [file] = sources.files
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
    [file] = report.sources.files
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
    assert out.splitlines()[-1] == "4 notes: --notes shows them"


# ─── a current record (`constructed`) ────────────────────────────────────────


def test_a_current_record_with_an_update_constructed(forever: Path, home: Path) -> None:
    record = {
        "name": "Pawn",
        "providerName": "Wago",
        "installedVersion": "Pawn 2.13.16",
        "latestVersion": "Pawn 2.13.17",
        "installedFolderList": ["Pawn"],
    }
    _wowup(json.dumps({"p": record}).encode("utf-8"))
    pawn = forever / FOREVER / "Interface" / "AddOns" / "Pawn"
    pawn.mkdir()
    (pawn / "Pawn.toc").write_bytes(b"## Interface: 16001\n## Title: Pawn\n## Version: 2.13.16\n")
    [match] = _report().sources.records
    assert (match.version_check, match.stale, match.update_available) == ("agrees", False, True)
    line = _line(ok("addons", "list"), "Pawn")
    assert line.endswith("from Wago through WowUp, Pawn 2.13.16, update Pawn 2.13.17 available")


def test_the_lab_addon_is_not_called_unclaimed_constructed(forever: Path, home: Path) -> None:
    _wowup()
    lab = forever / FOREVER / "Interface" / "AddOns" / "WowLab"
    lab.mkdir()
    (lab / "WowLab.toc").write_bytes(b"## Interface: 16001\n## Title: WowLab\n")
    [folder] = [f for f in _report().sources.folders if f.folder == "WowLab"]
    assert folder.lab_addon and folder.unclaimed is False
    assert "manager" not in _line(ok("addons", "list"), "WowLab")


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
    (path.parent / "sensitive.json").write_text("{}", encoding="utf-8")
    before = _state(forever, path.parent)
    ok("addons", "list")
    ok("addons", "list", "--json")
    ok("addons", "list", "--notes")
    assert _state(forever, path.parent) == before


@pytest.mark.parametrize("args", [(), ("--notes",)], ids=["default", "notes"])
def test_the_text_passes_the_player_copy_lint(
    forever: Path, home: Path, args: tuple[str, ...]
) -> None:
    _wowup()
    out = ok("addons", "list", *args)
    assert hits(out) == []
    if args:
        assert "Notes:" in out and "--notes shows" not in out


def test_the_help_says_what_it_reads() -> None:
    out = ok("addons", "list", "--help")
    assert "WowUp" in out and "--notes" in out
    assert hits(out) == []
