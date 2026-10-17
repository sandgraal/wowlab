# Probe from review of m13/28-filemap-addon-executables; reproduces a whole `snap restore` refusing outright on a changed addon executable whose file-map row is now Edit "no", instead of skipping it as LAB_PLAN's 2026-09-27 amendment and `snap restore --help` say
"""Constructed (not a real capture): the captured tree is copied into
`tmp_path`, a constructed file is added to one addon folder, a snapshot is
taken, and that file and the addon's `.toc` are changed.

M13-28 gives `helper.sh` there the `addon-executable` row (Edit `no`).
LAB_PLAN §5 (amended 2026-09-27) says a whole-snapshot `snap restore` "skips
every file whose file-map row has Edit `no`", and the command's help says
such files "are left alone and counted in the plan". PR #231's user-facing
list says the same for this row. But `snap_restore` asks the gate for the
whole plan (`tx.restore(id, None)`) before `_client_managed` splits it, and
the gate refuses a whole restore holding a changed executable, so the
command exits 3 and nothing is skipped or restored.

Positive control: the same flow with a changed `.DS_Store` in the same addon
folder (row `os-metadata`, Edit `no`) is skipped and the `.toc` restored.
Nothing reads or writes a real install.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install
from wowlab_core.snapshot import Manifest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ADDON = "Interface/AddOns/DBM-Brawlers"
TOC = f"{ADDON}/DBM-Brawlers.toc"

runner = CliRunner()


@pytest.fixture
def flavor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    assert (root / FLAVOR / TOC).is_file()
    return root / FLAVOR


def _whole_restore_skips(flavor: Path, name: str, entry_id: str) -> None:
    edit_no = flavor / ADDON / name
    edit_no.write_bytes(b"constructed\n")
    made = runner.invoke(cli.app, ["snap", "create", "-m", "probe", "--json"])
    assert made.exit_code == 0, (made.stdout, made.stderr)
    snap = Manifest.model_validate_json(made.stdout)
    toc = flavor / TOC
    saved_toc = toc.read_bytes()
    toc.write_bytes(saved_toc + b"## constructed change\n")
    edit_no.write_bytes(b"constructed, changed\n")

    result = runner.invoke(cli.app, ["snap", "restore", snap.id, "--yes", "--json"])
    assert result.exit_code == 0, (result.exit_code, result.stdout, result.stderr)
    report = cli.RestoreReport.model_validate_json(result.stdout)
    assert [(s.path, s.entry_id) for s in report.skipped] == [(f"{ADDON}/{name}", entry_id)]
    assert [i.path for i in report.plan] == [TOC]
    assert toc.read_bytes() == saved_toc
    assert edit_no.read_bytes() == b"constructed, changed\n"


def test_positive_control_os_metadata_in_an_addon_is_skipped_constructed(flavor: Path) -> None:
    _whole_restore_skips(flavor, ".DS_Store", "os-metadata")


def test_a_changed_addon_executable_is_skipped_by_a_whole_restore_constructed(
    flavor: Path,
) -> None:
    _whole_restore_skips(flavor, "helper.sh", "addon-executable")
