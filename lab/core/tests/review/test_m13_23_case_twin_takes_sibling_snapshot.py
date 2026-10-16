# Probe from review of m13/23-per-path-detail; reproduces `explain` on a
# case-sensitive volume answering with a case-twin sibling's snapshot entry.
"""On a case-sensitive volume (Linux under Wine, Lutris or Proton, which
`docs/LAB_PLAN.md` §6.1 supports; CI's ubuntu runner), `Syndicator.lua` and
`syndicator.lua` are two files (§6.4: "on a case-sensitive volume a missing
`a.lua` no longer excludes a separate sibling `A.lua`"). The round-1 fix in
`pathfacts._file_entry` falls back to a case-folded manifest match whenever
the exact spelling is absent, without asking whether the volume folds case.

So a new file `syndicator.lua`, created after a snapshot that holds only
its twin `Syndicator.lua`, is reported as held (`holds=True`,
`path=.../Syndicator.lua`), compared against the twin's bytes, and given a
`snap restore ... --paths .../Syndicator.lua` hint: a restore of a different
file than the one asked about.

Constructed (L8): one extra sibling file in a copy of the captured tree.
Skipped where the temporary volume folds case.
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
TWIN = "WTF/Account/90000001#6/SavedVariables/syndicator.lua"

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
    if (root / FLAVOR / TWIN).exists():
        pytest.skip("the temporary volume folds case")
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    created = runner.invoke(cli.app, ["snap", "create"])
    assert created.exit_code == 0, created.stdout
    # After the snapshot, a separate file appears beside the held one.
    (root / FLAVOR / TWIN).write_bytes(b"Syndicator = {}\n")
    return root


def _explained(path: str) -> cli.ExplainReport:
    result = runner.invoke(cli.app, ["explain", path, "--json"])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    return cli.ExplainReport.model_validate_json(result.stdout)


def test_positive_control_the_held_file_is_held_and_unchanged(root: Path) -> None:
    report = _explained(SYND)
    assert report.snapshot is not None
    assert report.snapshot.holds is True
    assert report.snapshot.live == "same"


def test_a_new_case_twin_is_not_held_by_the_snapshot(root: Path) -> None:
    report = _explained(TWIN)
    assert isinstance(report.result, cli.layout.Classified)
    assert report.snapshot is not None
    assert report.snapshot.holds is False, (
        f"the snapshot never held {TWIN}; explain says it does, as {report.snapshot.path}"
    )
    assert not any(h.argv[:2] == ["snap", "restore"] for h in report.hints), [
        h.command for h in report.hints
    ]
