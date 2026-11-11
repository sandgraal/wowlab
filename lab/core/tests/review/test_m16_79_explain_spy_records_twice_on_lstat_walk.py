# Probe from review of m16/79-pathfacts-casc-from-root; reproduces the edited
# explain spy test counting the named file twice on the lstat walk (Windows).
"""`test_explain_hashes_only_the_named_path_and_writes_nothing` now spies
both `snapshot.open_regular_file` and `snapshot.open_regular_file_from_root`.
On Windows (`_O_NOFOLLOW` 0) the walk is `_open_by_lstat`, which calls the
module-level `open_regular_file(path)` by its full path, so the patched spy
records the named file a second time per hash and the test's
`in_install == [flavor / SYNDICATOR] * 2` sees four. Constructed: the same
test run with `_O_NOFOLLOW` forced to 0, the walk Windows takes; the
positive control runs it on the `dir_fd` walk, where it passes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_cli_explain_detail as base
from test_cli_explain_detail import (
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    root,  # noqa: F401
    user_data,  # noqa: F401  (autouse: the store under tmp_path)
)

from wowlab_core import snapshot


def _run(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    base.test_explain_hashes_only_the_named_path_and_writes_nothing(
        request.getfixturevalue("root"), request.getfixturevalue("flavor"), monkeypatch
    )


@pytest.mark.skipif(sys.platform == "win32", reason="the base test runs there itself")
def test_the_explain_spy_test_holds_on_the_lstat_walk_constructed(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(snapshot, "_O_NOFOLLOW", 0)
    _run(request, monkeypatch)


def test_positive_control_the_dir_fd_walk_constructed(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(request, monkeypatch)
