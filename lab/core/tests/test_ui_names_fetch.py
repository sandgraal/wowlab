"""`wowlab ui` offers `names fetch` (M18-02), run as every other leaf is:
through the server's fixed-argv CLI path, with `--json`, no new route and no
fetch by the server itself (ADR-0029). The page's button is M18-04's.

The run goes through the real server and the real CLI in a child process,
so it cannot be given a replayed source: every name table is placed in the
redirected home's data folder first (`constructed`: the recordings of
`ChrClasses` and `ChrRaces` for 1.60.1.70058 and the two committed subsets),
and the run must report all four cached and make no request. The install is
the captured tree `fixtures/macos-70058/`, copied under the stand-in home.
Nothing reads or writes a real install.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import test_ui_explorer as first_stage

from wowlab_core import cli, gamedata

home = first_stage.home
server = first_stage.server

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EARLY = FIXTURES / "macos-70058"
WAGO = FIXTURES / "wago"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
VERSION = "1.60.1.70058"
COMMAND = ["names", "fetch"]


@pytest.fixture
def early(home: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = home / "Games" / "World of Warcraft"
    root.mkdir(parents=True)
    shutil.copy2(EARLY / ".build.info", root / ".build.info")
    shutil.copytree(EARLY / "forever", root / FLAVOR)
    monkeypatch.setenv("WOWLAB_WOW_ROOT", str(root))
    monkeypatch.delenv("WOWLAB_FLAVOR", raising=False)
    yield root


def test_the_server_offers_names_fetch_as_a_leaf_that_runs(server: Any) -> None:
    listed = first_stage._send(server, "GET", "/api/commands").json()
    offered = {tuple(c["path"]): c for c in listed["commands"]}
    leaf = offered[tuple(COMMAND)]
    assert leaf["excluded"] is None
    # No --yes: it writes only into wowlab's own cache, so it runs directly.
    assert leaf.get("confirm") is None


def test_a_run_from_the_ui_reads_the_cache_and_fetches_nothing_constructed(
    server: Any, home: Path, early: Path
) -> None:
    cache = gamedata.default_cache_dir()
    if not cache.is_relative_to(home):
        pytest.skip("the user data folder is not under the stand-in home on this platform")
    folder = cache / "tables" / VERSION
    folder.mkdir(parents=True)
    for table in ("ChrClasses", "ChrRaces"):
        shutil.copy2(WAGO / f"{table}.{VERSION}.csv", folder / f"{table}.csv")
    for table in ("ChrSpecialization", "SkillLine"):
        shutil.copy2(WAGO / f"{table}.{VERSION}.subset.csv", folder / f"{table}.csv")
    before = {p.name: p.read_bytes() for p in folder.iterdir()}

    report = cli.NamesFetchReport.model_validate(first_stage._run_json(server, COMMAND, {}))

    assert report.fetched == 0
    (row,) = report.flavors
    assert (row.folder, row.version) == (FLAVOR, VERSION)
    assert {t.status for t in row.tables} == {"cached"}
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
