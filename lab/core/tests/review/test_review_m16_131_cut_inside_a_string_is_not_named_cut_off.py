# Probe from review of m16/131-unreadable-sv; reproduces a SavedVariables file
# cut off inside a string or a `true` being listed without the cut-off words.
"""Constructed from a real capture (rows in `fixtures/README.md`, L8): the
captured `DBM-StatusBarTimers.lua` cut off at three points, written into a
copy of the capture. `sv list --unreadable` names a file cut off between
entries as cut off (the positive control), but a cut inside a quoted string
reads "unterminated string" and a cut inside `true` reads "it holds a name
where a value should be, which saved data never does", which with the
standing note points the player at a hand edit. Over the six largest captured
files cut at 400 points each, about 47% of the cut points are not named as
cut off. The option's help gives "a file cut off during a save" as its case.
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
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
REL = "WTF/Account/90000001#6/SavedVariables/DBM-StatusBarTimers.lua"


@pytest.fixture
def flavor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        platformdirs, "user_data_path", lambda *a, **k: tmp_path / "userdata" / "wowlab"
    )
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root / FLAVOR


def _cut_at(data: bytes, marker: bytes, into: int) -> bytes:
    at = data.index(marker) + into
    return data[:at]


def _reason(flavor: Path, data: bytes) -> str:
    (flavor / REL).write_bytes(data)
    out = CliRunner().invoke(cli.app, ["sv", "list", "--unreadable", "--json"])
    assert out.exit_code == 0, out.stderr
    report = cli.SvListReport.model_validate_json(out.stdout)
    [failure] = report.read_failures
    return failure.reason


def test_control_a_cut_between_entries_is_named_cut_off(flavor: Path) -> None:
    data = (CAPTURE / "forever" / REL).read_bytes()
    assert "cut off" in _reason(flavor, _cut_at(data, b'["HugeBarsEnabled"] = true,\r\n', 29))


@pytest.mark.parametrize(
    ("marker", "into"),
    [(b'["TimerPoint"] = "TOPRIGHT"', 21), (b'["HugeBarsEnabled"] = true', 24)],
    ids=["inside-a-string", "inside-true"],
)
def test_a_cut_inside_a_value_is_named_cut_off(flavor: Path, marker: bytes, into: int) -> None:
    data = (CAPTURE / "forever" / REL).read_bytes()
    assert "cut off" in _reason(flavor, _cut_at(data, marker, into))
