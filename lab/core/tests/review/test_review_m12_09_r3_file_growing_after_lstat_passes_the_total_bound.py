# Probe from review of m12/09-char-list; reproduces a file that grows between its lstat and its read making one run read more than its total bound
"""Round 3 of the M12-09 review.

The round-2 docstring of `labaddon.survey`, the §14.4 amendment and the PR
body say: "One run reads at most `MAX_SURVEY_BYTES` (256 MiB) in all". The
bound exists so a folder of large files cannot cost unbounded time or
memory.

`_Reader.read` compares the file's `lstat` size with what is left of the
bound, then reads it with `snapshot.read_regular_file(path,
limit=luadata.MAX_FILE_BYTES, expect=st)`. The `expect` check compares
device and inode only, and the read's limit is the per-file bound, not
what is left of the total. A file that grows between the `lstat` and the
read (the client writing it at logout, or anything else) is read whole, up
to the per-file bound, so one run can read up to the total bound plus the
per-file bound (512 MiB at the shipped values). Here, with a small budget
injected: the first file fits by its `lstat` size, grows by 100,000 bytes
before it is opened, and the run reads about 123,000 bytes against a
budget of about 23,000.

The growth is made deterministic by wrapping `snapshot.read_regular_file`
(which `_Reader.read` calls right after its `lstat`) so the file is
appended to in exactly the window a concurrent writer would use. Positive
control: without the growth, the same run reads no more than its budget.

Constructed (L8): the `macos` capture copied into `tmp_path`. Nothing reads
or writes a real install.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest

from wowlab_core import install, labaddon, snapshot
from wowlab_core.layout import Layout

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder (tests may name it, L6)
ACCOUNT_DIR = "WTF/Account/90000001#6"
SECOND = "1/Labcharb-Labrealmf"  # first in the order
GROWTH = b"-- pad\n" * (100_000 // 7)


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
    return root / FLAVOR


def _bytes_read(flavor: Path, monkeypatch: pytest.MonkeyPatch, *, grow: bool) -> tuple[int, int]:
    first = flavor / ACCOUNT_DIR / SECOND / "SavedVariables" / "WowLab.lua"
    budget = first.stat().st_size + 10
    real = snapshot.read_regular_file
    total = 0

    def read(path: Path, **kwargs: Any) -> bytes:
        nonlocal total
        if grow and path == first:
            with first.open("ab") as fh:  # the window between lstat and open
                fh.write(GROWTH)
        data = real(path, **kwargs)
        total += len(data)
        return data

    monkeypatch.setattr(snapshot, "read_regular_file", read)
    labaddon.survey(Layout(flavor, install_root=flavor.parent), budget=budget)
    return total, budget


def test_constructed_a_file_growing_after_lstat_stays_within_the_total_bound(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    total, budget = _bytes_read(flavor, monkeypatch, grow=True)
    assert total <= budget, f"read {total:,} bytes against a total bound of {budget:,}"


def test_constructed_positive_control_no_growth(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    total, budget = _bytes_read(flavor, monkeypatch, grow=False)
    assert 0 < total <= budget
