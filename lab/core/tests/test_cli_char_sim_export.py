"""`wowlab char sim-export` (M21-08) on the `macos-70888` capture.

The install is a copy of `fixtures/macos-70888/` (`_forever_`, one schema-4
character, Sandgraal-Rizzen) in `tmp_path`; `WOWLAB_WOW_ROOT` points at it
and the user data directory is redirected into `tmp_path`. Where a test
places the recorded `TraitNodeEntry.1.60.1.70888.csv` in that cache, the
cache is `constructed` (the table is real, its place is set up by the
test). The expected stdout is the recorded export of `test_simexport.py`,
**[verify]** until the owner imports it into WoWSims (M21-O2). Nothing is
fetched (the cache is the only source of the table, and a test that would
reach the network fails), and nothing reads or writes a real install.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, gamedata, install, simexport

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LAUNCH = FIXTURES / "macos-70888"
FOREVER = "_forever_"
ACCOUNT = "LABACCOUNTA"
WARLOCK = "1/Sandgraal-Rizzen"
BUILD = "1.60.1.70888"
EXPECTED = (
    Path(__file__).resolve().parent
    / "snapshots"
    / "sim_export"
    / "macos-70888-Sandgraal-Rizzen.json"
).read_text(encoding="ascii")

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    monkeypatch.delenv(install.ENV_FLAVOR, raising=False)

    def no_fetch(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("sim-export fetched a table")

    monkeypatch.setattr(gamedata.GameData, "table", no_fetch)
    return redirected


@pytest.fixture
def launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(LAUNCH / ".build.info", root / ".build.info")
    shutil.copytree(LAUNCH / "forever", root / FOREVER)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


@pytest.fixture
def cached(user_data: Path) -> Path:
    """The recorded table in the cache for the record's build (constructed)."""
    folder = user_data / "gamedata" / "tables" / BUILD
    folder.mkdir(parents=True)
    shutil.copy2(FIXTURES / "wago" / f"TraitNodeEntry.{BUILD}.csv", folder / "TraitNodeEntry.csv")
    return folder


def _tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def run(*args: str) -> Any:
    return runner.invoke(cli.app, ["char", "sim-export", *args], terminal_width=200)


def test_stdout_is_the_recorded_export_alone_constructed(launch: Path, cached: Path) -> None:
    before = _tree(launch)
    done = run()
    assert done.exit_code == 0, done.output
    assert done.stdout == EXPECTED
    err = done.stderr.splitlines()
    assert err[0].startswith(f"Character: {WARLOCK} in account {ACCOUNT}")
    assert f"Talents read with the game table TraitNodeEntry of {BUILD}." in err
    assert "Left out: slot 4 Shirt: Brown Linen Shirt: the sim has no such slot" in err
    assert err[-1].endswith("notes: --notes shows them")
    assert _tree(launch) == before


def test_json_is_the_report_with_the_same_text_constructed(launch: Path, cached: Path) -> None:
    done = run("--json", "--character", WARLOCK)
    assert done.exit_code == 0, done.output
    report = cli.CharSimExportReport.model_validate_json(done.stdout)
    assert report.sim.text + "\n" == EXPECTED
    assert report.sim.export == json.loads(EXPECTED)
    assert report.character == WARLOCK and report.flavor_folder == FOREVER
    assert report.path is None
    assert report.sim.talents_table_build == BUILD
    assert report.sim.source_commit == simexport.load_format().source.commit
    assert [x.what for x in report.sim.left_out] == ["slot 4 Shirt: Brown Linen Shirt"]


def test_without_the_cached_table_the_talents_are_left_out_and_said(launch: Path) -> None:
    done = run()
    assert done.exit_code == 0, done.output
    export = json.loads(done.stdout)
    assert "talents" not in export
    assert export["gear"] == json.loads(EXPECTED)["gear"]
    assert (
        f"Left out: talents: the game table TraitNodeEntry is not cached for {BUILD}; wowlab "
        f"db2 fetch TraitNodeEntry --build {BUILD} downloads it once"
    ) in done.stderr.splitlines()


def test_out_writes_the_export_under_the_user_data_directory(
    launch: Path, cached: Path, user_data: Path
) -> None:
    before = _tree(launch)
    done = run("--out", "rizzen.json")
    assert done.exit_code == 0, done.output
    path = user_data / "sim-exports" / "rizzen.json"
    assert path.read_text(encoding="ascii") == EXPECTED
    assert "Written to" in done.stdout and "rizzen.json" in done.stdout
    assert EXPECTED.strip() not in done.stdout
    report = cli.CharSimExportReport.model_validate_json(
        run("--out", "rizzen.json", "--json").stdout
    )
    assert report.path == str(path)
    assert _tree(launch) == before


@pytest.mark.parametrize(
    "name", ["../x.json", "a/b.json", ".x", ""], ids=lambda n: f"constructed: {n!r}"
)
def test_out_refuses_anything_but_a_plain_name_before_reading_constructed(
    launch: Path, user_data: Path, name: str
) -> None:
    done = run("--out", name)
    assert done.exit_code == 2
    assert "plain file name" in done.stderr
    assert not (user_data / "sim-exports").exists()


def test_notes_are_on_stderr_with_the_marker_in_player_words(launch: Path, cached: Path) -> None:
    done = run("--notes")
    assert done.exit_code == 0, done.output
    assert done.stdout == EXPECTED
    assert "Notes:" in done.stderr
    assert "WoWSims' Addon Import" in done.stderr
    assert "[verify]" not in done.stderr and "not yet confirmed" in done.stderr


def test_a_record_with_no_class_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The M11-03 capture (schema 1): no identity, so no class to export."""
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos" / "forever", root / "_classic_beta_")
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    done = run("--character", "1/Labchard-Labrealmg", "--account", "90000001#6")
    assert done.exit_code == 1
    assert "no export: the sim needs the class" in done.stderr
    assert done.stdout == ""


def test_the_help_names_the_json_model_behind_the_form_feed() -> None:
    done = runner.invoke(cli.app, ["char", "sim-export", "--help"], terminal_width=200)
    assert done.exit_code == 0
    assert "WoWSims" in done.stdout and "CharSimExportReport" not in done.stdout
    assert "JSON: CharSimExportReport." in (cli.char_sim_export.__doc__ or "")
