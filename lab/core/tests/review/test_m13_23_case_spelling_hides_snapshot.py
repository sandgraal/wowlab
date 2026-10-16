# Probe from review of m13/23-per-path-detail; reproduces `explain` saying
# "not held" for a file a snapshot holds when the path is spelled in another
# case on a case-insensitive volume.
"""`wowlab explain PATH` classifies PATH case-insensitively (the file map and
the layout fold case), and on a case-insensitive volume (the macOS default,
every Windows install) the live file is the same one. The snapshot lookup in
`pathfacts.snapshot_facts` matches the manifest entry by exact text, so a
player who types `wtf/account/...` is told the file is "not held" by any
snapshot, and is offered `snap create` instead of the restore that would put
back their file (§15.6, M13-23: "the latest snapshot holding the path").

Constructed (L8): only the spelling of the query differs from the captured
tree. Skipped where the temporary volume is case-sensitive.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder (tests may name it, L6)
SYND = "WTF/Account/90000001#6/SavedVariables/Syndicator.lua"

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)


@pytest.fixture(autouse=True)
def idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    if not (root / FLAVOR / SYND.lower()).exists():
        pytest.skip("the temporary volume is case-sensitive")
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


def _explained(path: str) -> cli.ExplainReport:
    result = runner.invoke(cli.app, ["explain", path, "--json"])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    return cli.ExplainReport.model_validate_json(result.stdout)


def _changed_after_snapshot(root: Path) -> None:
    created = runner.invoke(cli.app, ["snap", "create"])
    assert created.exit_code == 0, created.stdout
    live = root / FLAVOR / SYND
    live.write_bytes(live.read_bytes() + b"\n")


def test_positive_control_exact_spelling_finds_the_snapshot(root: Path) -> None:
    _changed_after_snapshot(root)
    report = _explained(SYND)
    assert report.snapshot is not None
    assert report.snapshot.snapshot_id is not None
    assert report.snapshot.live == "differs"
    assert any(h.command.startswith("wowlab snap restore ") for h in report.hints)


@pytest.mark.parametrize("spelling", [SYND.lower(), SYND.upper()])
def test_another_spelling_of_the_same_file_finds_the_same_snapshot(
    root: Path, spelling: str
) -> None:
    _changed_after_snapshot(root)
    exact = _explained(SYND)
    other = _explained(spelling)
    assert isinstance(other.result, cli.layout.Classified)  # same row, same file
    assert other.stat is not None and exact.stat is not None
    assert other.stat.mtime_ns == exact.stat.mtime_ns
    assert other.snapshot is not None and exact.snapshot is not None
    assert other.snapshot.snapshot_id == exact.snapshot.snapshot_id, (
        "a snapshot holds this file, but explain says it is not held"
    )
    assert other.snapshot.live == "differs"
