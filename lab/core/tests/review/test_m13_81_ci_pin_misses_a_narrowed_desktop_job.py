# Probe from review of m13/81-test-fast; reproduces the ci.yml pin passing when the lab-desktop job's pytest runs are narrowed by marker or path
"""M13-81 asks for a `tests/repo` pin that "the pytest commands in `ci.yml`
still collect everything, so the target cannot drift into hiding a suite".

`tests/repo/test_make_test_fast.py::test_ci_yml_runs_every_test_in_its_two_passes`
pins the `quality` job's two passes exactly, but for every other job it only
rejects the options in `HIDING` (`--ignore`, `-k`, `--deselect`, ...). The
`lab-desktop` job (macOS and Windows, the only CI run on those platforms)
can narrow its marker expression or its path and the pin still passes.

Constructed: a copy of the real `ci.yml` with one line edited, under
`tmp_path`. The positive control shows the pin does catch an `--ignore`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[4]
PIN = ROOT / "tests" / "repo" / "test_make_test_fast.py"
DESKTOP = 'pytest -q -n auto --dist worksteal -m "not live and not serial" lab/core/tests\n'


def _pin(root: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("m13_81_pin_probe", PIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = root  # type: ignore[attr-defined]
    return module


def _tree(tmp_path: Path, new: str) -> Path:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert DESKTOP in text, "the lab-desktop parallel pass is where this probe expects it"
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(text.replace(DESKTOP, new), encoding="utf-8")
    return tmp_path


def test_positive_control_an_ignore_in_the_desktop_job_fails_the_pin(tmp_path: Path) -> None:
    root = _tree(
        tmp_path, DESKTOP.replace("lab/core/tests\n", "--ignore=lab/core/tests/ui lab/core/tests\n")
    )
    with pytest.raises(AssertionError):
        _pin(root).test_ci_yml_runs_every_test_in_its_two_passes()


@pytest.mark.parametrize(
    "narrowed",
    [
        pytest.param(
            'pytest -q -n auto --dist worksteal -m "not live and not serial and not parser" lab/core/tests\n',
            id="marker",
        ),
        pytest.param(
            'pytest -q -n auto --dist worksteal -m "not live and not serial" lab/core/tests/parser\n',
            id="path",
        ),
    ],
)
def test_a_narrowed_desktop_job_fails_the_pin(tmp_path: Path, narrowed: str) -> None:
    root = _tree(tmp_path, narrowed)
    with pytest.raises(AssertionError):
        _pin(root).test_ci_yml_runs_every_test_in_its_two_passes()
