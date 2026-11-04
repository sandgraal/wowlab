# Probe from review of m16/51-ui-fonts; reproduces test_ui_fonts.py's wheel test downloading hatchling from PyPI (ADR-0012: no live external calls from tests or CI).
"""`test_the_wheel_and_sdist_carry_the_fourteen_files` runs `uv build` with
build isolation, so uv resolves and downloads `hatchling` and its five
dependencies from pypi.org into its cache on every machine whose cache does
not hold them (a fresh CI runner included). With the network off and an
empty uv cache the test fails; it does not skip. ADR-0012 keeps network
needs out of the test suite: a test that needs the network "fails for
reasons that have nothing to do with the change under review".

Each case runs one test of `test_ui_fonts.py` in a child pytest with
`UV_OFFLINE=1` (uv refuses every network request) and an empty
`UV_CACHE_DIR` under `tmp_path`, so nothing here reaches the network either.
The positive control runs a test of the same file that needs no build: the
harness itself works offline.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
FONTS_TEST = ROOT / "lab" / "core" / "tests" / "test_ui_fonts.py"


def _run_offline(test: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    uv = os.environ.get("UV") or shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not available (the wheel test skips too)")
    cache = tmp_path / "empty-uv-cache"
    cache.mkdir()
    env = dict(os.environ, UV=uv, UV_OFFLINE="1", UV_CACHE_DIR=str(cache))
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:xdist",
            "-q",
            "-rA",
            f"{FONTS_TEST}::{test}",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def test_constructed_control_the_offline_harness_runs_a_fonts_test(tmp_path: Path) -> None:
    done = _run_offline("test_the_fonts_folder_holds_exactly_the_fourteen_pinned_files", tmp_path)
    assert done.returncode == 0 and "1 passed" in done.stdout, done.stdout[-3000:]


def test_the_wheel_test_passes_with_no_network_and_an_empty_uv_cache(tmp_path: Path) -> None:
    done = _run_offline("test_the_wheel_and_sdist_carry_the_fourteen_files", tmp_path)
    assert done.returncode == 0 and "1 passed" in done.stdout, done.stdout[-3000:]
