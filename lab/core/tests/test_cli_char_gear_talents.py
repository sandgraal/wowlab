"""`wowlab char gear`'s "Open my talents" (M21-07) on the `macos-70888` capture.

The install is a copy of `fixtures/macos-70888/` (`_forever_`, the owner's
warlock Sandgraal-Rizzen) in `tmp_path`; `WOWLAB_WOW_ROOT` points at it and
the user data directory is redirected into `tmp_path`. Where a test places
the recorded 1.60.1.70888 talent tables in that cache, the cache is
`constructed` (the tables are real, their place is set up by the test). The
expected address is the `link_sites.toml` example built from this record,
**[verify]** until the owner opens it (M21-O2). Nothing is fetched (a test
that would reach the network fails), and nothing reads or writes a real
install.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, gamedata, install, talentlink

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LAUNCH = FIXTURES / "macos-70888"
FOREVER = "_forever_"  # the provenance row's flavor folder; tests may name it (L6)
BUILD = "1.60.1.70888"
EXPECTED = "https://wowforeverbuilds.com/talents/warlock?b=022&race=undead"  # [verify] M21-O2

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    monkeypatch.delenv(install.ENV_FLAVOR, raising=False)

    def no_fetch(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("char gear fetched a table")

    monkeypatch.setattr(gamedata.GameData, "table", no_fetch)
    return redirected


@pytest.fixture(autouse=True)
def launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(LAUNCH / ".build.info", root / ".build.info")
    shutil.copytree(LAUNCH / "forever", root / FOREVER)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


def _cache(user_data: Path) -> None:
    folder = user_data / "gamedata" / "tables" / BUILD
    folder.mkdir(parents=True)
    for table in talentlink.TABLES:
        shutil.copy2(FIXTURES / "wago" / f"{table}.{BUILD}.csv", folder / f"{table}.csv")


def _ok(*args: str) -> Any:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def test_the_json_carries_the_address(user_data: Path) -> None:
    _cache(user_data)
    out = _ok("char", "gear", "--json").stdout
    report = cli.CharGearReport.model_validate_json(out)
    talents = report.talents
    assert talents.link.model_dump()["url"] == EXPECTED
    assert talents.ranks == "022" and talents.tables_build == BUILD
    assert talents.download == [] and talents.download_build is None
    data = json.loads(out)
    # `dressing` stays the report's last field (M16-114).
    assert list(data)[-1] == "dressing" and list(data)[-4:-1] == [
        "talents",
        "purist",
        "flavor_folder",
    ]


def test_the_text_prints_the_address(user_data: Path) -> None:
    _cache(user_data)
    lines = _ok("char", "gear").stdout.splitlines()
    assert f"Talents on WoW Forever Builds: {EXPECTED}" in lines


def test_without_the_tables_the_json_names_the_download() -> None:
    report = cli.CharGearReport.model_validate_json(_ok("char", "gear", "--json").stdout)
    assert report.talents.link.model_dump()["why"] == (
        f"the game's talent tables for {BUILD} are not downloaded"
    )
    assert report.talents.download == list(talentlink.TABLES)
    assert report.talents.download_build == BUILD


def test_without_the_tables_the_text_says_where_to_get_them(user_data: Path) -> None:
    out = _ok("char", "gear").stdout
    assert (
        f"Talents on WoW Forever Builds: no link: the game's talent tables for {BUILD} are not "
        "downloaded; the Character tab of wowlab ui downloads them with one click, or run "
        f'wowlab db2 fetch <table> --build {BUILD} for each of "TraitNode", '
        '"TraitNodeGroup" and "TraitNodeGroupXTraitNode"'
    ) in " ".join(out.split())
    assert not (user_data / "gamedata").exists()  # nothing was written


def test_db2_fetch_takes_each_table_the_report_names(
    user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The download the page offers: `db2 fetch <table> --build <build>` for
    each table, replayed from the recordings; then `char gear` gives the
    address."""

    def replay(self: gamedata.GameData, name: str, build: str) -> Path:
        folder = self.cache_dir / "tables" / build
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.csv"
        if not path.exists():
            shutil.copy2(FIXTURES / "wago" / f"{name}.{build}.csv", path)
        return path

    monkeypatch.setattr(gamedata.GameData, "table", replay)
    report = cli.CharGearReport.model_validate_json(_ok("char", "gear", "--json").stdout)
    assert report.talents.download_build is not None
    for table in report.talents.download:
        _ok("db2", "fetch", table, "--build", report.talents.download_build, "--json")
    again = cli.CharGearReport.model_validate_json(_ok("char", "gear", "--json").stdout)
    assert again.talents.link.model_dump()["url"] == EXPECTED
