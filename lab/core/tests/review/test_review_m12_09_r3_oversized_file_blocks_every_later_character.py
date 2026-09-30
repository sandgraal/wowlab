# Probe from review of m12/09-char-list; reproduces one WowLab.lua over the per-file bound, never read, using up the run's total bound so every later character is "not read"
"""Round 3 of the M12-09 review.

The ticket (docs/BACKLOG.md, M12-09): "One unreadable file names itself,
exits 1 and never blocks the others", and its acceptance: "a labelled
constructed damaged file is named and the rest still listed, exit 1".

Round 2 added a total bound for one run (`labaddon.MAX_SURVEY_BYTES`,
256 MiB). `_Reader.read` checks it against the file's `lstat` size before
the per-file bound (`luadata.MAX_FILE_BYTES`, also 256 MiB) is applied. A
file over the per-file bound is refused by `snapshot.read_regular_file`
from its `fstat` size without a byte read, so it costs the run nothing. But
the total-bound check sees `st_size > left` first, sets the bound "reached"
for the rest of the run, and every character after it in the order is
"not read: the listing's total size bound was reached", although nothing
has been read yet. At 7218249 (before the total bound) the same tree gave
the big file its own reason and listed the other two.

Positive controls: the same oversized file last in the order leaves the
two captured characters read (the bound is order-dependent, not the
file); and the total bound still stops a run that really spent it.

Constructed (L8): the `macos` capture copied into `tmp_path`, plus one
sparse file (no data blocks written on APFS, ext4 or NTFS). Nothing reads
or writes a real install.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, labaddon, luadata
from wowlab_core.layout import Layout

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder (tests may name it, L6)
ACCOUNT_DIR = "WTF/Account/90000001#6"
FIRST = "1/Labchard-Labrealmg"
SECOND = "1/Labcharb-Labrealmf"
BOUND_WORDS = "the listing's total size bound was reached"

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


@pytest.fixture
def flavor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root / FLAVOR


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def _plant_oversized(flavor: Path, character: str) -> None:
    """A `WowLab.lua` one byte over the per-file bound, as a sparse file."""
    target = flavor / ACCOUNT_DIR / character / "SavedVariables" / "WowLab.lua"
    target.parent.mkdir(parents=True)
    with target.open("wb") as fh:
        fh.truncate(luadata.MAX_FILE_BYTES + 1)


def _survey(flavor: Path, **kwargs: Any) -> labaddon.AllCharacters:
    return labaddon.survey(Layout(flavor, install_root=flavor.parent), **kwargs)


def test_constructed_oversized_file_first_does_not_block_the_others(flavor: Path) -> None:
    _plant_oversized(flavor, "1/Aaa-Oversized")  # first in the order
    by = {e.character: e for e in _survey(flavor).characters}
    assert by["1/Aaa-Oversized"].error is not None
    # Nothing was read before it, so the total bound cannot have been reached.
    assert BOUND_WORDS not in (by["1/Aaa-Oversized"].error or "")
    assert by[SECOND].summary is not None, by[SECOND].error
    assert by[FIRST].summary is not None, by[FIRST].error
    text = run("char", "list")
    assert text.exit_code == 1
    assert text.stdout.count("saved by client") == 2, text.stdout


def test_constructed_positive_control_oversized_file_last(flavor: Path) -> None:
    _plant_oversized(flavor, "1/Zzz-Oversized")  # last in the order
    by = {e.character: e for e in _survey(flavor).characters}
    assert by["1/Zzz-Oversized"].error is not None
    assert by[SECOND].summary is not None and by[FIRST].summary is not None


def test_constructed_positive_control_a_spent_bound_still_stops_the_run(flavor: Path) -> None:
    second = (flavor / ACCOUNT_DIR / SECOND / "SavedVariables" / "WowLab.lua").stat().st_size
    by = {e.character: e for e in _survey(flavor, budget=second).characters}
    assert by[SECOND].summary is not None
    assert BOUND_WORDS in (by[FIRST].error or "")
