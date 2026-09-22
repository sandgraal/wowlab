# Probe from review of m10/11-guard-tests; reproduces: ReplaceSpy.fsync raises TypeError on os.fsync(file_object), which os.fsync accepts
"""`ReplaceSpy` in `test_guard.py` stands in for `os.fsync` during the atomic
replace graders. Its replacement calls `os.fstat(fd)`, which only takes an
integer, while the real `os.fsync` takes "an integer file descriptor or an
object with a fileno() method". A guard that writes `os.fsync(f)` on its temp
file is doing exactly what §6.10 asks, but under the spy it raises
`TypeError` and three graders fail on a correct implementation
(crash-between-temp-write-and-rename, operating-system-refusal, and
parent-swapped-for-a-symlink). Expected: the spy accepts whatever `os.fsync`
accepts and records the file's inode either way.

Constructed input: a file in `tmp_path`. No `guard` is needed; the spy is
loaded from the grader file by path.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType

import pytest

GRADERS = Path(__file__).resolve().parents[1] / "test_guard.py"


def _graders() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_review_m10_11_graders", GRADERS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fsync_under_spy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, as_object: bool) -> bool:
    graders = _graders()
    spy = graders.ReplaceSpy(tmp_path)
    spy.install(monkeypatch)
    target = tmp_path / "constructed-temp-file"
    with target.open("wb") as f:
        f.write(b"constructed")
        f.flush()
        os.fsync(f if as_object else f.fileno())
    return graders._file_id(target.stat()) in spy.fsynced


def test_constructed_real_os_fsync_accepts_a_file_object(tmp_path: Path) -> None:
    """Positive control: the call the spy must tolerate is valid Python."""
    with (tmp_path / "plain").open("wb") as f:
        f.write(b"constructed")
        f.flush()
        os.fsync(f)


def test_constructed_spy_records_an_integer_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control: the spelling the spy was written for works."""
    assert _fsync_under_spy(tmp_path, monkeypatch, as_object=False)


def test_constructed_spy_records_a_file_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect: `os.fsync(f)` under the spy raises instead of recording."""
    assert _fsync_under_spy(tmp_path, monkeypatch, as_object=True)
