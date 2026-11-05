# Probe from review of m16/40-grader-diff-gate; reproduces a guard change that neutralises its graders through conftest.py or a grader helper module passing the gate
"""Constructed (not a real capture): a throwaway repository in `tmp_path`.

M16-40 asks that, on a change to a load-bearing file, "a job fails if a
tracked grader file has any hunk other than the removal of an
`xfail(strict=True)` block". `scripts/check_grader_diff.py` only looks at
files named `test_*.py`. The graders on `main` do not live only there:
`lab/core/tests/parser/_luadata_oracle.py` is the M10-04T oracle the luadata
graders compare against, and the root `conftest.py` (and the per-folder
ones) decide which graders run at all. A change to `guard.py` that also
adds a `pytest_collection_modifyitems` hook deselecting every guard test,
or that makes the oracle return whatever the parser produced, leaves every
`test_*.py` byte-identical and the gate exits 0.

Positive control: the same neutralisation written into the grader itself
(`test_guard_constructed.py`) fails the gate.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "check_grader_diff.py"

GRADER = """\
from _oracle import expected
from wowlab_core import guard


def test_write() -> None:
    assert guard.write() == expected()
"""

ORACLE = """\
def expected() -> int:
    return 2
"""

CONFTEST = """\
def pytest_configure(config) -> None:
    pass
"""

DESELECT = """\
def pytest_collection_modifyitems(config, items) -> None:
    items[:] = [i for i in items if "guard" not in i.nodeid]
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
            "lab/core/src/wowlab_core/guard.py": "def write() -> int:\n    return 2\n",
            "lab/core/tests/test_guard_constructed.py": GRADER,
            "lab/core/tests/_oracle.py": ORACLE,
            "lab/core/tests/conftest.py": CONFTEST,
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


def test_positive_control_neutralising_the_grader_itself_fails(repo: Path) -> None:
    _commit(
        repo,
        {
            **BROKEN_GUARD,
            "lab/core/tests/test_guard_constructed.py": GRADER.replace(
                "assert guard.write() == expected()", "assert guard.write()"
            ),
        },
        "impl",
    )
    assert _gate(repo).returncode == 1


def test_conftest_that_deselects_the_graders_fails_the_gate(repo: Path) -> None:
    _commit(repo, {**BROKEN_GUARD, "lab/core/tests/conftest.py": CONFTEST + DESELECT}, "impl")
    done = _gate(repo)
    assert done.returncode == 1, done.stdout


def test_grader_helper_oracle_edit_fails_the_gate(repo: Path) -> None:
    _commit(
        repo,
        {**BROKEN_GUARD, "lab/core/tests/_oracle.py": ORACLE.replace("return 2", "return 3")},
        "impl",
    )
    done = _gate(repo)
    assert done.returncode == 1, done.stdout
