"""`wowlab names fetch` against the real wago.tools (M18-02). Never run in CI
(ADR-0012); run by hand:

    uv run pytest -m live lab/core/tests/test_names_fetch_live.py

It makes at most five requests (the four name tables for 1.60.1.70058 and,
only if one is missing, the builds listing), into a cache under
`tmp_path`. wago.tools is community-run; do not loop this.

The install is the captured tree `fixtures/macos-70058/` copied into
`tmp_path`; the user data directory is redirected there too. Nothing reads
or writes a real install.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EARLY = FIXTURES / "macos-70058"
WAGO = FIXTURES / "wago"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
VERSION = "1.60.1.70058"


@pytest.mark.live
def test_live_names_fetch_downloads_the_four_tables_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(EARLY / ".build.info", root / ".build.info")
    shutil.copytree(EARLY / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.delenv(install.ENV_FLAVOR, raising=False)
    runner = CliRunner()

    first = runner.invoke(cli.app, ["names", "fetch", "--json"])
    assert first.exit_code == 0, (first.stdout, first.stderr)
    report = cli.NamesFetchReport.model_validate_json(first.stdout)
    (row,) = report.flavors
    assert row.version == VERSION
    assert {t.status for t in row.tables} == {"fetched"}
    assert report.fetched == 4
    folder = data / "gamedata" / "tables" / VERSION
    # The two recorded whole for this build still match what is served.
    for table in ("ChrClasses", "ChrRaces"):
        served = (folder / f"{table}.csv").read_bytes()
        assert served == (WAGO / f"{table}.{VERSION}.csv").read_bytes(), (
            f"{table} for {VERSION} changed upstream: log it in docs/DATA_SOURCES.md"
        )

    second = runner.invoke(cli.app, ["names", "fetch", "--json"])
    assert second.exit_code == 0
    again = cli.NamesFetchReport.model_validate_json(second.stdout)
    assert again.fetched == 0
    assert {t.status for f in again.flavors for t in f.tables} == {"cached"}
