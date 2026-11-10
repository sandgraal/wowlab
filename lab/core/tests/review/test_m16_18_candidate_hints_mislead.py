# Probe from review of m16/18-bare-names; reproduces two candidate lines that mislead: a shown path that does not explain with several flavors, and "nothing by this name was found" when a file by that name was found.
"""The install is the captured tree (`fixtures/macos/`) copied into
`tmp_path`, as `test_bare_names.py` does; the second flavor folder
`_constructed_` is a copy of it (constructed). Nothing reads or writes a
real install."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

CAPTURE = Path(__file__).resolve().parents[1] / "fixtures" / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
SECOND = "_constructed_"
A = "WTF/Account/90000001#6"

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)


@pytest.fixture(autouse=True)
def idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, second: bool) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    if second:
        shutil.copytree(CAPTURE / "forever", root / SECOND)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def test_control_one_flavor_a_shown_path_explains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(tmp_path, monkeypatch, second=False)
    text = run("explain", f"{A}/config-cache.wtf").stdout
    assert "file-map row: account-config-cache" in text


def test_several_flavors_the_path_shown_explains_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(tmp_path, monkeypatch, second=True)
    listed = run("explain", "config-cache.wtf").stdout
    assert "explain one of them by the path shown" in listed
    shown = f"    {A}/config-cache.wtf (in {FLAVOR})"
    assert shown in listed
    # The player types the path as shown, as the line tells them to.
    typed = run("explain", f"{A}/config-cache.wtf").stdout
    assert "file-map row: account-config-cache" in typed, (listed, typed)


def test_a_row_is_not_said_to_have_no_file_when_one_was_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(tmp_path, monkeypatch, second=False)
    text = run("explain", "bindings-cache.wtf").stdout
    # Control: a file by this name is on disk, and listed.
    assert f"{A}/bindings-cache.wtf (in {FLAVOR})" in text
    assert "nothing by this name was found" not in text, text
