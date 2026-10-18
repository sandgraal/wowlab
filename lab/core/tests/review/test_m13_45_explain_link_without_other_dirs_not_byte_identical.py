# Probe from review of m13/45-why-not-a-flavor; reproduces `explain` of a link in an install with no `other_dirs` printing differently from main
"""M13-45 acceptance: "output for an install with no `other_dirs` is
byte-identical to `main`". The branch's `elif verdict.link:` in `explain`
rewrites the first line for every unclassified link, not only for a path
under a folder in `other_dirs`. With one real flavor folder and a link
inside it (no `_..._` folder left over), `main` (7e52f62) prints

    _classic_beta_/Bar: no file-map row (_classic_beta_): no file-map row matches

and the branch prints

    _classic_beta_/Bar (_classic_beta_): no file-map row; a link, or inside one; ...

(reviewer's run of both trees on the same fixture copy). Either the change
is limited to a path whose first part is in `other_dirs`, or the owner
relaxes the criterion; this probe holds the criterion as written.

The install is the captured tree (`fixtures/macos/`) copied into
`tmp_path`. Constructed (L8, boundary): the link `Bar` inside the flavor
folder, pointing outside the install.
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
MAIN_LINE = f"{FLAVOR}/{{name}}: no file-map row ({FLAVOR}): no file-map row matches"


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
    (root / FLAVOR / "Plain").mkdir()
    try:
        (root / FLAVOR / "Bar").symlink_to(tmp_path, target_is_directory=True)
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")
    assert install.read_install(root).other_dirs == ()
    return root


def _first_line(path: str, root: Path) -> str:
    result = CliRunner().invoke(cli.app, ["explain", path, "--root", str(root)])
    assert result.exit_code == 0, result.output
    return result.stdout.splitlines()[0]


def test_positive_control_unclassified_folder_prints_as_main(tmp_path: Path) -> None:
    root = _install(tmp_path)
    assert _first_line(f"{FLAVOR}/Plain", root) == MAIN_LINE.format(name="Plain")


def test_unclassified_link_prints_as_main(tmp_path: Path) -> None:
    root = _install(tmp_path)
    assert _first_line(f"{FLAVOR}/Bar", root) == MAIN_LINE.format(name="Bar")
