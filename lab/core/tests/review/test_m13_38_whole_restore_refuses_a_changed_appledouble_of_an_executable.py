# Probe from review of m13/38-restore-skips-executables; reproduces a whole `snap restore` exiting 3 on a changed AppleDouble file named like an executable (`._helper.sh`, row `os-metadata`, Edit "no") instead of skipping it, so the Edit "no" promise still fails for an executable-named file outside the `addon-executable` row.
"""Constructed (not a real capture): the captured tree is copied into
`tmp_path` (user data directory redirected, process table a fake), a
constructed AppleDouble file is added to one addon folder, a snapshot is
taken, and that file and the addon's `.toc` are changed.

`snap restore --help` says that without --paths "files whose file-map Edit
is `no` (..., file-browser metadata) are left alone and counted in the
plan". `._helper.sh` has the `os-metadata` row (Edit `no`): macOS writes one
beside `helper.sh` when an addon folder is copied onto a volume without
extended attributes (exFAT, FAT, SMB). The gate refuses it only for its
name, which ends in `.sh`, exactly as it refuses `helper.sh`. M13-38's
`_changed_executables` skips such a file only when its row id is
`addon-executable`, so this one goes to the gate, which refuses the whole
restore: exit 3, nothing restored. The `.toc` is not put back.

Positive control: the same flow with `._helper.lua` (same row, a name the
gate writes) is skipped and the `.toc` restored. A second control pins that
`--paths` naming `._helper.sh` is refused, so the plan must not offer
`--paths` for it either (the ticket's rule for executables).
Nothing reads or writes a real install.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, guard, install
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


def _snapshot_then_change(flavor: Path, name: str) -> tuple[Manifest, Path, bytes]:
    meta = flavor / ADDON / name
    meta.write_bytes(b"\x00\x05\x16\x07constructed appledouble\n")
    made = runner.invoke(cli.app, ["snap", "create", "-m", "probe", "--json"])
    assert made.exit_code == 0, (made.stdout, made.stderr)
    snap = Manifest.model_validate_json(made.stdout)
    assert f"{FLAVOR}/{ADDON}/{name}" in {e.path for e in snap.entries}
    toc = flavor / TOC
    saved_toc = toc.read_bytes()
    toc.write_bytes(saved_toc + b"## constructed change\n")
    meta.write_bytes(b"\x00\x05\x16\x07constructed appledouble, changed\n")
    return snap, toc, saved_toc


def _whole_restore_skips(flavor: Path, name: str) -> None:
    snap, toc, saved_toc = _snapshot_then_change(flavor, name)
    result = runner.invoke(cli.app, ["snap", "restore", snap.id, "--yes", "--json"])
    assert result.exit_code == 0, (result.exit_code, result.stdout, result.stderr)
    report = cli.RestoreReport.model_validate_json(result.stdout)
    assert [(s.path, s.entry_id) for s in report.skipped] == [(f"{ADDON}/{name}", "os-metadata")]
    assert [i.path for i in report.plan] == [TOC]
    assert toc.read_bytes() == saved_toc
    assert (flavor / ADDON / name).read_bytes().endswith(b"changed\n")


def test_positive_control_appledouble_of_a_lua_file_is_skipped_constructed(flavor: Path) -> None:
    _whole_restore_skips(flavor, "._helper.lua")


def test_positive_control_paths_naming_the_appledouble_executable_is_refused_constructed(
    flavor: Path,
) -> None:
    snap, _, _ = _snapshot_then_change(flavor, "._helper.sh")
    rel = f"{ADDON}/._helper.sh"
    result = runner.invoke(cli.app, ["snap", "restore", snap.id, "--paths", rel, "--yes"])
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert "is an executable; guard never writes one" in result.stderr
    assert guard.history() == ()


def test_a_changed_appledouble_of_an_executable_is_skipped_by_a_whole_restore_constructed(
    flavor: Path,
) -> None:
    _whole_restore_skips(flavor, "._helper.sh")


def test_the_plan_does_not_offer_paths_for_the_appledouble_executable_constructed(
    flavor: Path,
) -> None:
    snap, _, _ = _snapshot_then_change(flavor, "._helper.sh")
    result = runner.invoke(cli.app, ["snap", "restore", snap.id, "--dry-run"])
    assert result.exit_code == 0, (result.exit_code, result.stdout, result.stderr)
    lines = result.stdout.splitlines()
    listed = [n for n, ln in enumerate(lines) if ln.strip().startswith(f"{ADDON}/._helper.sh")]
    assert len(listed) == 1, result.stdout
    header = next(ln for ln in reversed(lines[: listed[0]]) if ln.startswith("  Skipped "))
    assert "--paths to restore" not in header, header
