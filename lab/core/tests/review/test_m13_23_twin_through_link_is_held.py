# Probe from review of m13/23-per-path-detail; reproduces `explain` matching a
# snapshot entry through a folder link and hinting a restore the gate refuses.
"""`pathfacts._Volume.same` decides that two spellings name one file by
`lstat`-ing both. `lstat` does not follow the last part, but it follows every
folder link before it. On a case-sensitive volume, when a snapshot holds
`.../SavedVariables/Syndicator.lua` and the folder is now `savedvariables`
with a link `SavedVariables -> savedvariables` beside it, `explain` of the
real path says the snapshot holds it (as the link spelling) and hints
`snap restore ... --paths .../SavedVariables/Syndicator.lua`, which the
write gate refuses ("is a link; guard never follows one").

Expected: a spelling reached through a link is not the same entry; no
restore hint that the gate refuses.

Constructed (L8): a folder renamed and a link added in a copy of the
captured tree. Skipped where the temporary volume folds case.
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
ACC = "WTF/Account/90000001#6"
REAL = f"{ACC}/savedvariables/Syndicator.lua"

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
    acc = root / FLAVOR / ACC
    if (acc / "savedvariables").exists():
        pytest.skip("the temporary volume folds case")
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    created = runner.invoke(cli.app, ["snap", "create"])
    assert created.exit_code == 0, created.stdout
    return root


def _explained(path: str) -> cli.ExplainReport:
    result = runner.invoke(cli.app, ["explain", path, "--json"])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    return cli.ExplainReport.model_validate_json(result.stdout)


def _relink_and_change(root: Path) -> None:
    acc = root / FLAVOR / ACC
    (acc / "SavedVariables").rename(acc / "savedvariables")
    (acc / "SavedVariables").symlink_to("savedvariables")
    target = acc / "savedvariables" / "Syndicator.lua"
    target.write_bytes(target.read_bytes() + b"\n")


def test_positive_control_a_changed_held_file_gets_a_restore_the_gate_accepts(
    root: Path,
) -> None:
    held = f"{ACC}/SavedVariables/Syndicator.lua"
    path = root / FLAVOR / held
    path.write_bytes(path.read_bytes() + b"\n")
    report = _explained(held)
    hints = [h for h in report.hints if h.argv[:2] == ["snap", "restore"]]
    assert hints, [h.command for h in report.hints]
    dry = runner.invoke(cli.app, [*hints[0].argv, "--dry-run"])
    assert dry.exit_code == 0, (dry.stdout, dry.stderr)


def test_no_restore_hint_through_a_folder_link(root: Path) -> None:
    _relink_and_change(root)
    report = _explained(REAL)
    for hint in report.hints:
        if hint.argv[:2] != ["snap", "restore"]:
            continue
        dry = runner.invoke(cli.app, [*hint.argv, "--dry-run"])
        assert dry.exit_code == 0, (
            f"explain hinted {hint.command!r}; the write gate refuses it: {dry.stderr}"
        )
