# Probe from review of m13/57-explain-root-entries; reproduces `explain WTF/Config.wtf` (even with `--flavor`) losing the flavor's row when a stray root `WTF/Config.wtf` exists
"""M13-57 acceptance: "`explain` output for paths that do exist inside the
flavor byte-identical to `main`". With a stray `WTF/Config.wtf` at the
install root (a leftover of the pre-flavor-folder layout, or a file copied
one folder too high, as addons often are into a root `Interface/`), the
branch judges `WTF/Config.wtf` at the root, "no file-map row", where `main`
gives the flavor's `Config.wtf` row; with `--flavor <the flavor>` too, so
an explicit flavor no longer reads a path that is in it. The PR's
user-facing list does not name this change.

The install is the captured tree (`fixtures/macos/`) copied into
`tmp_path`. Constructed (L8, boundary): the root `WTF/Config.wtf`. Nothing
reads or writes a real install.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

CAPTURE = Path(__file__).resolve().parents[1] / "fixtures" / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder (L6: tests may name it)


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import platformdirs

    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: tmp_path / "ud" / "wowlab")
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    monkeypatch.chdir(tmp_path)  # outside the install


def _install(tmp_path: Path, *, stray: bool) -> Path:
    root = tmp_path / "wow"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    assert (root / FLAVOR / "WTF" / "Config.wtf").is_file()
    if stray:
        (root / "WTF").mkdir()
        (root / "WTF" / "Config.wtf").write_bytes(b'SET locale "enUS"\n')
    return root


def _json(root: Path, *extra: str) -> dict[str, Any]:
    args = ["explain", "WTF/Config.wtf", "--root", str(root), "--json", *extra]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    data: dict[str, Any] = json.loads(result.stdout)
    return data


@pytest.mark.parametrize("extra", [(), ("--flavor", FLAVOR)], ids=["only-flavor", "named"])
def test_positive_control_without_a_stray_root_entry_constructed(
    tmp_path: Path, extra: tuple[str, ...]
) -> None:
    data = _json(_install(tmp_path, stray=False), *extra)

    assert (data["result"]["base"], data["result"]["flavor_folder"]) == ("flavor", FLAVOR)


@pytest.mark.parametrize("extra", [(), ("--flavor", FLAVOR)], ids=["only-flavor", "named"])
def test_a_flavor_path_keeps_its_row_beside_a_stray_root_entry_constructed(
    tmp_path: Path, extra: tuple[str, ...]
) -> None:
    data = _json(_install(tmp_path, stray=True), *extra)

    assert (data["result"]["base"], data["result"].get("flavor_folder")) == ("flavor", FLAVOR)
