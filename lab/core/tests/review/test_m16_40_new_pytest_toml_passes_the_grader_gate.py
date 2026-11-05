# Probe from review of m16/40-grader-diff-gate; reproduces a new pytest.toml (pytest 9's highest-priority config file) deselecting graders on a guard change and passing the gate
"""Constructed (not a real capture): a throwaway repository in `tmp_path`.

After round 1 the gate fails a load-bearing PR that changes pytest
configuration, which it takes to be `pytest.ini`, `.pytest.ini`, the
`tool.pytest` table of `pyproject.toml`, `[tool:pytest]` in `setup.cfg` and
`[pytest]` in `tox.ini` (`CONFIG_NAMES`, `support_reason`). The lockfile pins
pytest 9.1.1, which also reads `pytest.toml` and `.pytest.toml`, and reads
them first: a `pytest.toml` beside `pyproject.toml` replaces its
`[tool.pytest.ini_options]` outright. A new root `pytest.toml` with
`addopts = ["--deselect", ...]` drops a grader from the run. Checked by
hand on pytest 9.1.1: the grader that fails alone is "1 deselected" once
the file is added. The gate never looks at the file and exits 0.

Positive control: the same deselection added as a new `pytest.ini` fails
the gate.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "check_grader_diff.py"

GRADER = """\
from wowlab_core import guard


def test_write() -> None:
    assert guard.write() == 2
"""

DESELECT_TOML = """\
[pytest]
addopts = ["--deselect", "lab/core/tests/test_guard_constructed.py::test_write"]
"""

DESELECT_INI = """\
[pytest]
addopts = --deselect lab/core/tests/test_guard_constructed.py::test_write
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _commit(repo: Path, files: dict[str, str], message: str) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "probe@example.invalid")
    _git(repo, "config", "user.name", "probe")
    _git(repo, "config", "commit.gpgsign", "false")
    _commit(
        repo,
        {
            "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["lab/core/tests"]\n',
            "lab/core/src/wowlab_core/guard.py": "def write() -> int:\n    return 2\n",
            "lab/core/tests/test_guard_constructed.py": GRADER,
        },
        "base",
    )
    return repo


def _gate(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--repo", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )


BROKEN_GUARD = {"lab/core/src/wowlab_core/guard.py": "def write() -> int:\n    return 3\n"}


def test_positive_control_new_pytest_ini_fails(repo: Path) -> None:
    _commit(repo, {**BROKEN_GUARD, "pytest.ini": DESELECT_INI}, "impl")
    assert _gate(repo).returncode == 1


@pytest.mark.parametrize("name", ["pytest.toml", ".pytest.toml"])
def test_new_pytest_toml_fails_the_gate(repo: Path, name: str) -> None:
    _commit(repo, {**BROKEN_GUARD, name: DESELECT_TOML}, "impl")
    done = _gate(repo)
    assert done.returncode == 1, done.stdout
