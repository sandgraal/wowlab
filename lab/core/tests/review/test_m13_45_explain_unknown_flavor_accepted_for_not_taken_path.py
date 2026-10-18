# Probe from review of m13/45-why-not-a-flavor; reproduces `explain --flavor <no such flavor> _retail_` exiting 0 where main refused it
"""M13-45 round 1 keeps a relative path whose first part is a folder in
`Install.not_taken` at the install root (`at_root`), which skips
`_select_flavor` altogether. A `--flavor` naming no flavor at all is then
dropped without a word: `explain --flavor _nope_ _retail_` (and `_empty_`)
exits 0 and explains the root folder, where main (7e52f62/aabd378) exits 2
with "no flavor folder '_nope_' in <root> (flavors found: _classic_beta_)"
(reviewer's run of both trees on the same fixture copy). The skip is
needed for the reading of the path; the check of `--flavor` is not.

The install is the captured tree (`fixtures/macos/`) copied into
`tmp_path`. Constructed (L8, boundary): the `_retail_` symlink beside the
real flavor folder, and the empty `_empty_`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

CAPTURE = Path(__file__).resolve().parents[1] / "fixtures" / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder (L6: tests may name it)
EXIT_USAGE = 2


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import platformdirs

    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: tmp_path / "ud" / "wowlab")
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    monkeypatch.chdir(tmp_path)


def _install(tmp_path: Path) -> Path:
    root = tmp_path / "wow"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    (root / "_empty_").mkdir()
    target = tmp_path / "elsewhere" / "retail"
    shutil.copytree(CAPTURE / "forever", target)
    try:
        (root / "_retail_").symlink_to(target, target_is_directory=True)
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")
    return root


def _explain(root: Path, *args: str) -> tuple[int, str]:
    result = CliRunner().invoke(cli.app, ["explain", *args, "--root", str(root)])
    return result.exit_code, result.stderr


def test_positive_control_unknown_flavor_is_refused_for_a_flavor_path(tmp_path: Path) -> None:
    root = _install(tmp_path)
    code, err = _explain(root, "--flavor", "_nope_", "WTF/Config.wtf")
    assert code == EXIT_USAGE
    assert "no flavor folder '_nope_'" in err


@pytest.mark.parametrize("path", ["_retail_", "_retail_/WTF/Config.wtf", "_empty_"])
def test_unknown_flavor_is_refused_for_a_not_taken_path(tmp_path: Path, path: str) -> None:
    root = _install(tmp_path)
    code, err = _explain(root, "--flavor", "_nope_", path)
    assert code == EXIT_USAGE, err
    assert "no flavor folder '_nope_'" in err
