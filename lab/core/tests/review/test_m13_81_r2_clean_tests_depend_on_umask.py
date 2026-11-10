# Probe from review of m13/81-test-fast; reproduces test_clean_test_artifacts.py failing under umask 002 (its layouts are group-writable, so the cleaner refuses them)
"""Round 2 of M13-81 made `clean_test_artifacts.py` refuse a
`pytest-of-<user>` that is writable by group or others, as it should.
pytest itself always creates that directory 0o700. The graders in
`tests/scripts/test_clean_test_artifacts.py` build their layout with plain
`mkdir`, so its mode follows the umask. Under umask 002 (the default for
users on many Linux distributions) the layout is 0o775, the cleaner
refuses it, and six graders fail on a machine where nothing is wrong.

The positive control runs the same file under umask 022 and passes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
GRADERS = ROOT / "tests" / "scripts" / "test_clean_test_artifacts.py"

pytestmark = pytest.mark.skipif(not hasattr(os, "umask") or os.name == "nt", reason="POSIX umask")


def _run(mask: int, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    code = (
        f"import os, sys, pytest; os.umask({mask}); "
        "sys.exit(pytest.main(['-q', '-p', 'no:cacheprovider', '-p', 'no:xdist', "
        "'--basetemp', sys.argv[2], sys.argv[1]]))"
    )
    return subprocess.run(
        [sys.executable, "-c", code, str(GRADERS), str(tmp_path / "base")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )


def test_positive_control_the_graders_pass_under_umask_022(tmp_path: Path) -> None:
    done = _run(0o022, tmp_path)
    assert done.returncode == 0, done.stdout[-2000:]


def test_the_graders_pass_under_umask_002(tmp_path: Path) -> None:
    done = _run(0o002, tmp_path)
    assert done.returncode == 0, done.stdout[-2000:]
