# Probe from review of m13/29-classify-missing-path; reproduces `explain WTF/Config.wtf` on a two-flavor install (no --flavor) saying "not found; nothing is at this path" for a file that is in both flavor folders.
"""M13-29 acceptance: "The text output for paths that exist is byte-identical."

`explain`'s PATH help says a relative path is read "relative to the install
folder; else to the flavor folder". With two flavors and no `--flavor`, the
flavor fallback does not run, so `result` is the install-root lookup. At
8db1943 that lookup lstats `<root>/WTF/Config.wtf`, finds nothing, and
reports "not found; nothing is at this path", and `--json` gives the verdict
words "not found; ...". On origin/main the same command printed
"no file-map row matches". The file is on disk in both flavor folders, so
the new words are false and the text output for an existing path changed.
Two flavors (retail plus a classic flavor) is an ordinary install.

Inputs: the captured macOS tree (`fixtures/macos/`) copied into `tmp_path`
twice, as `_classic_beta_` (its provenance row's folder) and as a second,
constructed flavor folder `_constructed_`. Nothing reads or writes a real
install.

Positive controls: a path that is missing everywhere still reads
"not found"; with `--flavor` the same path is classified as before.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

CAPTURE = Path(__file__).resolve().parent.parent / "fixtures" / "macos"
FLAVOR = "_classic_beta_"
SECOND = "_constructed_"
NOT_FOUND = "not found; nothing is at this path"

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
    shutil.copytree(CAPTURE / "forever", root / SECOND)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


def run(*args: str) -> Any:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, (args, result.stdout, result.stderr, result.exception)
    return result


def test_control_two_flavors_are_discovered(root: Path) -> None:
    folders = {f.folder for f in install.discover(root).flavors}
    assert folders == {FLAVOR, SECOND}
    assert (root / FLAVOR / "WTF" / "Config.wtf").is_file()
    assert (root / SECOND / "WTF" / "Config.wtf").is_file()


def test_control_a_path_missing_everywhere_is_not_found_constructed(root: Path) -> None:
    text = run("explain", "WTF/zz-missing-constructed.txt").stdout
    assert NOT_FOUND in text


def test_control_with_flavor_the_existing_path_is_classified(root: Path) -> None:
    report = json.loads(run("explain", "WTF/Config.wtf", "--flavor", FLAVOR, "--json").stdout)
    assert report["result"]["status"] == "classified"


def test_an_existing_flavor_path_is_not_reported_as_not_found(root: Path) -> None:
    text = run("explain", "WTF/Config.wtf").stdout
    assert NOT_FOUND not in text, text
    report = json.loads(run("explain", "WTF/Config.wtf", "--json").stdout)
    assert not report["result"]["reason"].startswith("not found"), report["result"]
    assert not report["verdict"]["words"].startswith("not found"), report["verdict"]
